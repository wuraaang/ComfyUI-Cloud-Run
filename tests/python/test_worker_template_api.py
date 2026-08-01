import io
import base64
import contextlib
import inspect
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from scripts.publish_worker_template import (
    BASE_TEMPLATE_HASH_ID,
    LOOKUP_COLUMNS,
    TemplatePublicationError,
    VastTemplateTransport,
    audit_base_template,
    publish_worker_template,
    main,
)


KEY = "synthetic-template-test-key"
WORKER_COMMIT = "a" * 40
NAME = "cloud-run-worker-" + WORKER_COMMIT
IMAGE = "docker.io/vastai/base-image@sha256:" + "d" * 64
HASH_ID = "e" * 32
WORKER_ARCHIVE_SHA256 = "b" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def remote_lock_payload():
    tag = "worker-v1-" + WORKER_COMMIT
    asset = (
        "comfyui-cloud-run-worker-"
        + WORKER_COMMIT
        + "-"
        + WORKER_ARCHIVE_SHA256
        + ".tar.gz"
    )
    return {
        "schema_version": 1,
        "archive_url": (
            "https://github.com/wuraaang/ComfyUI-Cloud-Run/"
            "releases/download/"
            + tag
            + "/"
            + asset
        ),
        "worker_commit": WORKER_COMMIT,
        "worker_archive_sha256": WORKER_ARCHIVE_SHA256,
        "worker_archive_size_bytes": 12345,
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.13.12",
        "destination": "/opt/comfyui-cloud-run",
    }


def template_request(*, bootstrap_bytes=None, lock_payload=None, image=IMAGE):
    if bootstrap_bytes is None:
        bootstrap_bytes = (
            REPOSITORY_ROOT / "remote_worker" / "bootstrap.py"
        ).read_bytes()
    if lock_payload is None:
        lock_payload = remote_lock_payload()
    bootstrap = base64.b64encode(bootstrap_bytes).decode("ascii")
    lock_bytes = (
        json.dumps(
            lock_payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    lock = base64.b64encode(lock_bytes).decode("ascii")
    onstart = "\n".join(
        (
            "#!/bin/sh",
            "set -eu",
            "umask 077",
            "mkdir -m 0700 /opt/comfyui-cloud-run-bootstrap",
            "printf '%s' '" + bootstrap + "' | base64 -d > /opt/comfyui-cloud-run-bootstrap/bootstrap.py",
            "chmod 0600 /opt/comfyui-cloud-run-bootstrap/bootstrap.py",
            "printf '%s' '" + lock + "' | base64 -d > /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "chmod 0600 /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "CLOUD_RUN_WORKER_VERSION=" + WORKER_COMMIT,
            "export CLOUD_RUN_WORKER_VERSION",
            "exec python3 /opt/comfyui-cloud-run-bootstrap/bootstrap.py /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "",
        )
    )
    return {
        "name": NAME,
        "image": image,
        "tag": "reviewed-pinned-tag",
        "runtype": "ssh",
        "use_ssh": True,
        "ssh_direct": True,
        "jup_direct": False,
        "jupyter_dir": "/workspace",
        "use_jupyter_lab": False,
        "docker_login_repo": "",
        "docker_login_user": "",
        "docker_login_pass": "",
        "onstart": onstart,
        "env": "-p 8765:8765",
        "recommended_disk_space": 80,
        "private": True,
    }


def template_row(request=None, *, template_id=17, hash_id=HASH_ID):
    request = request or template_request()
    return {
        "id": template_id,
        "name": request["name"],
        "hash_id": hash_id,
        "image": request["image"],
        "tag": request["tag"],
        "env": request["env"],
        "onstart": request["onstart"],
        "runtype": request["runtype"],
        "ssh_direct": request["ssh_direct"],
        "use_ssh": request["use_ssh"],
        "jup_direct": request["jup_direct"],
        "jupyter_dir": request["jupyter_dir"],
        "use_jupyter_lab": request["use_jupyter_lab"],
        "docker_login_repo": request["docker_login_repo"],
        "docker_login_user": request["docker_login_user"],
        "docker_login_pass": request["docker_login_pass"],
        "recommended_disk_space": request["recommended_disk_space"],
        "private": request["private"],
    }


class FakeResponse:
    def __init__(self, payload, *, status=200, content_encoding="identity"):
        self.status = status
        self.headers = {"Content-Encoding": content_encoding}
        self._body = io.BytesIO(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self.closed = False

    def read(self, amount=-1):
        return self._body.read(amount)

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class WorkerTemplateApiTests(unittest.TestCase):
    def _private_inputs(self, root):
        settings_directory = Path(root) / "settings"
        settings_directory.mkdir(mode=0o700)
        os.chmod(settings_directory, 0o700)
        settings = settings_directory / "settings.json"
        settings.write_text(json.dumps({"api_key": KEY, "hf_token": "ignored"}))
        os.chmod(settings, 0o600)
        output = Path(root) / "output"
        output.mkdir(mode=0o700)
        os.chmod(output, 0o700)
        request_path = Path(root) / "template-request.json"
        request_path.write_text(json.dumps(template_request()))
        os.chmod(request_path, 0o600)
        return settings, output, request_path

    def test_transport_fixes_get_endpoint_query_headers_and_timeout(self):
        response = FakeResponse(
            {"success": True, "templates_found": 0, "templates": []}
        )
        opener = FakeOpener([response])
        transport = VastTemplateTransport(opener=opener)

        result = transport.lookup_name(KEY, NAME)

        self.assertEqual(result, [])
        self.assertTrue(response.closed)
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "console.vast.ai")
        self.assertEqual(parsed.path, "/api/v0/template/")
        self.assertEqual(request.method, "GET")
        self.assertGreater(timeout, 0)
        query = parse_qs(parsed.query)
        self.assertEqual(
            query,
            {
                "select_filters": [
                    json.dumps(
                        {"name": {"eq": NAME}},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                ],
                "select_cols": [
                    json.dumps(LOOKUP_COLUMNS, separators=(",", ":"))
                ],
                "order_by": ["id"],
            },
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_transport_exposes_no_generic_http_or_mutation_surface(self):
        for forbidden in (
            "request",
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "create",
        ):
            self.assertFalse(hasattr(VastTemplateTransport, forbidden))
        self.assertEqual(
            tuple(inspect.signature(VastTemplateTransport.create_worker_template).parameters),
            ("self", "api_key", "template_payload"),
        )

    def test_audit_base_uses_exact_hash_and_writes_sanitized_private_record(self):
        base_request = template_request()
        base_request.update(
            {
                "name": "base",
                "runtype": "jupyter_direc ssh_direc",
                "jup_direct": True,
                "use_jupyter_lab": True,
            }
        )
        base_row = template_row(
            base_request,
            template_id=1,
            hash_id=BASE_TEMPLATE_HASH_ID,
        )
        response = FakeResponse(
            {"success": True, "templates_found": 1, "templates": [base_row]}
        )
        opener = FakeOpener([response])
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            record = audit_base_template(
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )
            artifact = output / "base-template-audit.json"
            self.assertEqual(record["hash_id"], BASE_TEMPLATE_HASH_ID)
            self.assertEqual(json.loads(artifact.read_text()), record)
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            self.assertNotIn(KEY, artifact.read_text())
            request = opener.calls[0][0]
            filters = json.loads(parse_qs(urlsplit(request.full_url).query)["select_filters"][0])
            self.assertEqual(filters, {"hash_id": {"eq": BASE_TEMPLATE_HASH_ID}})
            self.assertEqual(request.method, "GET")

    def test_audit_rejects_malformed_or_conflicting_lookup_without_post(self):
        invalid_payloads = (
            {"success": True, "templates_found": 2, "templates": [template_row(), template_row()]},
            {"success": True, "templates_found": 1, "templates": [{**template_row(), "api_key": KEY}]},
            {"success": True, "templates_found": 1, "templates": [{**template_row(), "hash_id": HASH_ID.upper()}]},
            {"success": True, "templates_found": 1, "templates": [{**template_row(), "image": "registry.example/latest"}]},
            {"success": True, "templates_found": 1, "templates": [{**template_row(), "private": 1}]},
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as root:
                settings, output, _request = self._private_inputs(root)
                opener = FakeOpener([FakeResponse(payload)])
                with self.assertRaisesRegex(
                    TemplatePublicationError,
                    "^Private template publication failed\\.$",
                ) as caught:
                    audit_base_template(
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=VastTemplateTransport(opener=opener),
                    )
                self.assertIsNone(caught.exception.__cause__)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual([call[0].method for call in opener.calls], ["GET"])

    def test_audit_rejects_altered_base_security_contract(self):
        base = template_request()
        base.update({"name": "base", "runtype": "ssh"})
        row = template_row(base, template_id=1, hash_id=BASE_TEMPLATE_HASH_ID)
        opener = FakeOpener(
            [FakeResponse({"success": True, "templates_found": 1, "templates": [row]})]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            with self.assertRaises(TemplatePublicationError):
                audit_base_template(
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=opener),
                )
            self.assertEqual(tuple(output.iterdir()), ())

    def test_audit_rejects_nonofficial_digest_pinned_registry(self):
        base = template_request(
            image="registry.example/vastai/base-image@sha256:" + "d" * 64
        )
        base.update(
            {
                "name": "base",
                "runtype": "jupyter_direc ssh_direc",
                "jup_direct": True,
                "use_jupyter_lab": True,
            }
        )
        row = template_row(
            base,
            template_id=1,
            hash_id=BASE_TEMPLATE_HASH_ID,
        )
        opener = FakeOpener(
            [
                FakeResponse(
                    {
                        "success": True,
                        "templates_found": 1,
                        "templates": [row],
                    }
                )
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            with self.assertRaises(TemplatePublicationError):
                audit_base_template(
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=opener),
                )
            self.assertEqual(tuple(output.iterdir()), ())

    def test_response_is_closed_when_json_is_invalid(self):
        response = FakeResponse({"success": True})
        response._body = io.BytesIO(b"not-json")
        opener = FakeOpener([response])
        with self.assertRaises(TemplatePublicationError):
            VastTemplateTransport(opener=opener).lookup_base(KEY)
        self.assertTrue(response.closed)

    def test_credential_read_rejects_symlink_permissions_and_oversize(self):
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            bad_paths = []
            public = settings.with_name("public.json")
            public.write_text(json.dumps({"api_key": KEY}))
            os.chmod(public, 0o640)
            bad_paths.append(public)
            symlink = settings.with_name("link.json")
            symlink.symlink_to(settings)
            bad_paths.append(symlink)
            oversized = settings.with_name("oversized.json")
            oversized.write_bytes(b"{" + b" " * (64 * 1024) + b"}")
            os.chmod(oversized, 0o600)
            bad_paths.append(oversized)
            invalid = settings.with_name("invalid.json")
            invalid.write_text("not-json")
            os.chmod(invalid, 0o600)
            bad_paths.append(invalid)
            missing = settings.with_name("missing-key.json")
            missing.write_text(json.dumps({"api_key": ""}))
            os.chmod(missing, 0o600)
            bad_paths.append(missing)
            for path in bad_paths:
                with self.subTest(path=path.name):
                    opener = FakeOpener([])
                    with self.assertRaises(TemplatePublicationError):
                        audit_base_template(
                            output,
                            settings_path_resolver=lambda path=path: path,
                            transport=VastTemplateTransport(opener=opener),
                        )
                    self.assertEqual(opener.calls, [])

    def test_credential_swap_between_lstat_and_open_is_rejected_and_closed(self):
        real_open = os.open
        real_close = os.close
        opened = []
        closed = []
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            replacement = settings.with_name("replacement.json")
            replacement.write_text(json.dumps({"api_key": "replacement"}))
            os.chmod(replacement, 0o600)

            def swapping_open(path, flags, *args, **kwargs):
                if Path(path) == settings:
                    os.replace(replacement, settings)
                descriptor = real_open(path, flags, *args, **kwargs)
                opened.append(descriptor)
                return descriptor

            def recording_close(descriptor):
                closed.append(descriptor)
                return real_close(descriptor)

            with mock.patch(
                "scripts.publish_worker_template.os.open",
                side_effect=swapping_open,
            ), mock.patch(
                "scripts.publish_worker_template.os.close",
                side_effect=recording_close,
            ):
                with self.assertRaises(TemplatePublicationError):
                    audit_base_template(
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=VastTemplateTransport(opener=FakeOpener([])),
                    )
            self.assertEqual(opened, closed)

    def test_publish_checks_absence_posts_once_and_verifies_exact_hash(self):
        request = template_request()
        responses = [
            FakeResponse({"success": True, "templates_found": 0, "templates": []}),
            FakeResponse(
                {
                    "success": True,
                    "msg": "created",
                    "template": {"id": 17, "name": NAME, "hash_id": HASH_ID},
                }
            ),
            FakeResponse(
                {"success": True, "templates_found": 1, "templates": [template_row(request)]}
            ),
        ]
        opener = FakeOpener(responses)
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            result = publish_worker_template(
                request_path,
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )
            self.assertEqual(result, {"id": 17, "hash_id": HASH_ID, "verified": True})
            artifact = output / "template-publication.json"
            self.assertEqual(json.loads(artifact.read_text()), result)
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            self.assertNotIn(KEY, artifact.read_text())
        self.assertEqual([call[0].method for call in opener.calls], ["GET", "POST", "GET"])
        body = json.loads(opener.calls[1][0].data)
        self.assertEqual(body, request)

    def test_publish_rejects_legacy_schema_before_http(self):
        invalid_requests = (
            {**template_request(), "runtype": "jupyter_direc ssh_direc"},
            {**template_request(), "ports": ["8765/tcp"]},
            {**template_request(), "private": False},
            {**template_request(), "name": "wrong-name"},
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            for index, payload in enumerate(invalid_requests):
                with self.subTest(index=index):
                    request_path.write_text(json.dumps(payload))
                    os.chmod(request_path, 0o600)
                    opener = FakeOpener([])
                    with self.assertRaises(TemplatePublicationError):
                        publish_worker_template(
                            request_path,
                            output,
                            settings_path_resolver=lambda: settings,
                            transport=VastTemplateTransport(opener=opener),
                        )
                    self.assertEqual(opener.calls, [])

    def test_publish_rejects_non_renderer_bootstrap_and_lock_before_http(self):
        invalid_requests = (
            template_request(bootstrap_bytes=b"arbitrary bootstrap"),
            template_request(
                lock_payload={**remote_lock_payload(), "extra": "payload"}
            ),
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            for index, payload in enumerate(invalid_requests):
                with self.subTest(index=index):
                    request_path.write_text(json.dumps(payload))
                    os.chmod(request_path, 0o600)
                    opener = FakeOpener([])
                    with self.assertRaises(TemplatePublicationError):
                        publish_worker_template(
                            request_path,
                            output,
                            settings_path_resolver=lambda: settings,
                            transport=VastTemplateTransport(opener=opener),
                        )
                    self.assertEqual(opener.calls, [])

    def test_ambiguous_post_is_not_retried_and_adopts_one_exact_name_match(self):
        request = template_request()
        opener = FakeOpener(
            [
                FakeResponse({"success": True, "templates_found": 0, "templates": []}),
                OSError("ambiguous secret " + KEY),
                FakeResponse(
                    {"success": True, "templates_found": 1, "templates": [template_row(request)]}
                ),
                FakeResponse(
                    {"success": True, "templates_found": 1, "templates": [template_row(request)]}
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            result = publish_worker_template(
                request_path,
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )
        self.assertTrue(result["verified"])
        self.assertEqual([call[0].method for call in opener.calls], ["GET", "POST", "GET", "GET"])
        filters = json.loads(parse_qs(urlsplit(opener.calls[2][0].full_url).query)["select_filters"][0])
        self.assertEqual(filters, {"name": {"eq": NAME}})

    def test_failed_publication_is_static_and_secret_safe(self):
        opener = FakeOpener(
            [FakeResponse({"success": True, "templates_found": 0, "templates": []}), OSError(KEY), OSError(KEY)]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            with self.assertRaisesRegex(
                TemplatePublicationError,
                "^Private template publication failed\\.$",
            ) as caught:
                publish_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=opener),
                )
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual([call[0].method for call in opener.calls].count("POST"), 1)

    def test_cli_rejects_key_arguments_without_echoing_the_key(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                main(["audit-base", "--api-key", KEY, "--output-directory", "/private"])
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(KEY, combined)
        self.assertNotIn("Traceback", combined)


if __name__ == "__main__":
    unittest.main()

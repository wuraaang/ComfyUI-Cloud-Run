import io
import base64
import contextlib
import gzip
import hashlib
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
IMAGE = (
    "docker.io/vastai/comfy@sha256:"
    "9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c"
)
TAG = "v0.29.0-cuda-12.9-py312"
EXTRA_FILTERS = {
    "gpu_arch": {"eq": "nvidia"},
    "cpu_arch": {"eq": "amd64"},
    "cuda_max_good": {"gte": 12.9},
    "compute_cap": {"gte": 750},
    "num_gpus": {"eq": 1},
}
HASH_ID = "e" * 32
WORKER_ARCHIVE_SHA256 = "b" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def invalid_extra_filters():
    return (
        {**EXTRA_FILTERS, "compute_cap": {"gte": True}},
        {**EXTRA_FILTERS, "num_gpus": {"eq": True}},
        {**EXTRA_FILTERS, "cuda_max_good": {"gte": "12.9"}},
        {**EXTRA_FILTERS, "compute_cap": {"gte": 800}},
        {
            key: value
            for key, value in EXTRA_FILTERS.items()
            if key != "gpu_arch"
        },
        {**EXTRA_FILTERS, "extra": {"eq": 1}},
        {**EXTRA_FILTERS, "compute_cap": {"eq": 750}},
        {
            **EXTRA_FILTERS,
            "compute_cap": {"gte": 750, "eq": 750},
        },
        {**EXTRA_FILTERS, "gpu_arch": {"eq": "amd"}},
    )


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
        "protocol_version": "2",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.12",
        "destination": "/opt/comfyui-cloud-run",
    }


def template_request(
    *,
    bootstrap_bytes=None,
    bootstrap_gzip=None,
    lock_payload=None,
    image=IMAGE,
    tag=TAG,
    extra_filters=None,
):
    if bootstrap_bytes is None:
        bootstrap_bytes = (
            REPOSITORY_ROOT / "remote_worker" / "bootstrap.py"
        ).read_bytes()
    if bootstrap_gzip is None:
        bootstrap_gzip = gzip.compress(
            bootstrap_bytes,
            compresslevel=9,
            mtime=0,
        )
    if lock_payload is None:
        lock_payload = remote_lock_payload()
    if extra_filters is None:
        extra_filters = EXTRA_FILTERS
    bootstrap = base64.b64encode(bootstrap_gzip).decode("ascii")
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
            "mkdir -p -m 0700 /opt/comfyui-cloud-run-bootstrap",
            "printf '%s' '" + bootstrap + "' | base64 -d | gzip -d > /opt/comfyui-cloud-run-bootstrap/bootstrap.py",
            "chmod 0600 /opt/comfyui-cloud-run-bootstrap/bootstrap.py",
            "printf '%s' '" + lock + "' | base64 -d > /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "chmod 0600 /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "CLOUD_RUN_WORKER_VERSION=" + WORKER_COMMIT,
            "export CLOUD_RUN_WORKER_VERSION",
            "CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI",
            "export CLOUD_RUN_COMFY_ROOT",
            "exec /venv/main/bin/python /opt/comfyui-cloud-run-bootstrap/bootstrap.py /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            "",
        )
    )
    return {
        "name": NAME,
        "image": image,
        "tag": tag,
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
        "extra_filters": extra_filters,
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
        "extra_filters": request["extra_filters"],
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


def base_template_row(**overrides):
    row = {
        "hash_id": BASE_TEMPLATE_HASH_ID,
        "use_ssh": True,
        "ssh_direct": True,
    }
    row.update(overrides)
    return row


def publication_intent_path(settings):
    return settings.parent / (
        ".template-publication-" + WORKER_COMMIT + ".intent"
    )


def publication_recovery_paths(settings):
    return tuple(
        settings.parent.glob(
            ".template-publication-"
            + WORKER_COMMIT
            + ".recovery.*.intent"
        )
    )


def publication_retired_paths(settings):
    return tuple(
        settings.parent.glob(
            ".template-publication-"
            + WORKER_COMMIT
            + ".retired.*.intent"
        )
    )


def request_with_recursive_lock():
    request = template_request()
    nested = "[" * 1100 + json.dumps(KEY) + "]" * 1100
    encoded = base64.b64encode(nested.encode("utf-8")).decode("ascii")
    lines = request["onstart"].splitlines()
    for index, line in enumerate(lines):
        if line.endswith(
            " > /opt/comfyui-cloud-run-bootstrap/release-lock.json"
        ):
            parts = line.split("'")
            parts[3] = encoded
            lines[index] = "'".join(parts)
            break
    else:
        raise AssertionError("release lock line missing")
    request["onstart"] = "\n".join(lines) + "\n"
    return request


def write_publication_intent(settings, request=None, *, payload=None):
    request = request or template_request()
    if payload is None:
        encoded_request = json.dumps(
            request,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload = {
            "schema_version": 1,
            "name": request["name"],
            "request_sha256": hashlib.sha256(encoded_request).hexdigest(),
        }
    intent = publication_intent_path(settings)
    intent.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
    os.chmod(intent, 0o600)
    return intent


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


class RawFakeResponse(FakeResponse):
    def __init__(self, body, *, status=200, content_encoding="identity"):
        self.status = status
        self.headers = {"Content-Encoding": content_encoding}
        self._body = io.BytesIO(body)
        self.closed = False


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


class ReadOnlyRecoveryTransport:
    def __init__(self, name_rows, hash_rows=()):
        self.name_rows = name_rows
        self.hash_rows = hash_rows
        self.calls = []

    def lookup_name(self, api_key, name):
        self.calls.append(("lookup_name", api_key, name))
        return self.name_rows

    def lookup_hash(self, api_key, hash_id):
        self.calls.append(("lookup_hash", api_key, hash_id))
        return self.hash_rows


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

    def _reconcile_worker_template(self, *args, **kwargs):
        publisher = __import__(
            "scripts.publish_worker_template",
            fromlist=["reconcile_worker_template"],
        )
        return publisher.reconcile_worker_template(*args, **kwargs)

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
                    json.dumps(["*"], separators=(",", ":"))
                ],
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

    def test_transport_rejects_near_miss_no_template_responses(self):
        invalid_payloads = (
            {"success": 0, "msg": "No templates found"},
            {"success": False, "msg": "no templates found"},
            {"success": False, "msg": "No templates found."},
            {
                "success": False,
                "msg": "No templates found",
                "templates": [],
            },
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(index=index):
                opener = FakeOpener([FakeResponse(payload)])
                with self.assertRaises(TemplatePublicationError):
                    VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)
                self.assertEqual(
                    [call[0].method for call in opener.calls],
                    ["GET"],
                )

    def test_transport_rejects_duplicate_json_members(self):
        response = RawFakeResponse(
            b'{"success":true,"success":false,"msg":"No templates found"}'
        )
        opener = FakeOpener([response])

        with self.assertRaises(TemplatePublicationError):
            VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)

        self.assertTrue(response.closed)
        self.assertEqual([call[0].method for call in opener.calls], ["GET"])

    def test_worker_lookup_uses_wildcard_and_projects_exact_compared_fields(self):
        provider_marker = "provider-worker-extra-marker"
        response = FakeResponse(
            {
                "success": True,
                "templates_found": 1,
                "templates": [
                    {**template_row(), "description": provider_marker}
                ],
            }
        )
        opener = FakeOpener([response])

        rows = VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)

        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), set(LOOKUP_COLUMNS))
        self.assertEqual(rows[0]["image"], IMAGE)
        self.assertEqual(rows[0]["tag"], TAG)
        self.assertEqual(rows[0]["extra_filters"], EXTRA_FILTERS)
        self.assertNotIn(provider_marker, json.dumps(rows[0]))
        query = parse_qs(urlsplit(opener.calls[0][0].full_url).query)
        self.assertEqual(json.loads(query["select_cols"][0]), ["*"])

    def test_worker_lookup_normalizes_exact_serialized_extra_filters(self):
        row = template_row()
        row["extra_filters"] = json.dumps(
            EXTRA_FILTERS,
            separators=(",", ":"),
        )
        response = FakeResponse(
            {
                "success": True,
                "templates_found": 1,
                "templates": [row],
            }
        )
        opener = FakeOpener([response])

        rows = VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)

        self.assertEqual(rows, [template_row()])
        self.assertEqual([call[0].method for call in opener.calls], ["GET"])

    def test_worker_lookup_rejects_non_exact_serialized_extra_filters(self):
        compact = json.dumps(EXTRA_FILTERS, separators=(",", ":"))
        duplicate = compact.replace(
            '"gpu_arch":{"eq":"nvidia"}',
            '"gpu_arch":{"eq":"nvidia","eq":"nvidia"}',
        )
        invalid_filters = [
            "{",
            duplicate,
            "[]",
            "null",
            "[" * 1100 + "]" * 1100,
        ]
        invalid_filters.extend(
            json.dumps(filters, separators=(",", ":"))
            for filters in invalid_extra_filters()
        )

        for index, filters in enumerate(invalid_filters):
            with self.subTest(index=index):
                row = template_row()
                row["extra_filters"] = filters
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

                with self.assertRaisesRegex(
                    TemplatePublicationError,
                    "^Private template publication failed\\.$",
                ) as caught:
                    VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)

                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(
                    [call[0].method for call in opener.calls],
                    ["GET"],
                )

    def test_audit_base_uses_exact_hash_and_writes_sanitized_private_record(self):
        base_row = base_template_row()
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
            columns = json.loads(
                parse_qs(urlsplit(request.full_url).query)["select_cols"][0]
            )
            self.assertEqual(
                columns,
                [
                    "hash_id",
                    "use_ssh",
                    "ssh_direct",
                ],
            )
            self.assertEqual(request.method, "GET")

    def test_audit_projects_allowlisted_fields_from_wildcard_response(self):
        provider_marker = "provider-extra-field-marker"
        base_row = {
            **base_template_row(),
            "image": "provider.example/mutable:latest",
            "tag": "latest",
            "description": provider_marker,
            "extra_filters": {"marker": provider_marker},
        }
        opener = FakeOpener(
            [
                FakeResponse(
                    {
                        "success": True,
                        "templates_found": 1,
                        "templates": [base_row],
                    }
                )
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            record = audit_base_template(
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )
            artifact_text = (output / "base-template-audit.json").read_text()

        self.assertEqual(
            set(record),
            {
                "schema_version",
                "hash_id",
                "use_ssh",
                "ssh_direct",
            },
        )
        self.assertNotIn(provider_marker, json.dumps(record))
        self.assertNotIn(provider_marker, artifact_text)

    def test_audit_rejects_malformed_or_conflicting_lookup_without_post(self):
        invalid_payloads = (
            {
                "success": True,
                "templates_found": 2,
                "templates": [base_template_row(), base_template_row()],
            },
            {
                "success": True,
                "templates_found": 1,
                "templates": [
                    {**base_template_row(), "hash_id": HASH_ID.upper()}
                ],
            },
            {
                "success": True,
                "templates_found": 1,
                "templates": [{**base_template_row(), "use_ssh": 1}],
            },
            {
                "success": True,
                "templates_found": 1,
                "templates": [
                    {**base_template_row(), "ssh_direct": 1}
                ],
            },
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

    def test_audit_ignores_mutable_base_runtime_fields(self):
        row = base_template_row(
            runtype="jupyter",
            jupyter_dir=None,
        )
        opener = FakeOpener(
            [FakeResponse({"success": True, "templates_found": 1, "templates": [row]})]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, _request = self._private_inputs(root)
            record = audit_base_template(
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )

        self.assertEqual(
            record,
            {
                "schema_version": 1,
                "hash_id": BASE_TEMPLATE_HASH_ID,
                "use_ssh": True,
                "ssh_direct": True,
            },
        )

    def test_audit_never_uses_or_records_the_base_image_and_tag(self):
        provider_marker = "untrusted-provider-image-marker"
        row = base_template_row(
            image=provider_marker + ":latest",
            tag="latest",
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
            record = audit_base_template(
                output,
                settings_path_resolver=lambda: settings,
                transport=VastTemplateTransport(opener=opener),
            )
            artifact = (output / "base-template-audit.json").read_text()
        self.assertNotIn("image", record)
        self.assertNotIn("tag", record)
        self.assertNotIn(provider_marker, artifact)

    def test_response_is_closed_when_json_is_invalid(self):
        response = FakeResponse({"success": True})
        response._body = io.BytesIO(b"not-json")
        opener = FakeOpener([response])
        with self.assertRaises(TemplatePublicationError):
            VastTemplateTransport(opener=opener).lookup_base(KEY)
        self.assertTrue(response.closed)

    def test_worker_lookup_normalizes_invalid_onstart_unicode_without_echo(self):
        provider_marker = "provider-unicode-marker"
        row = template_row()
        row["onstart"] = "\ud800" + provider_marker
        response = FakeResponse(
            {
                "success": True,
                "templates_found": 1,
                "templates": [row],
            }
        )
        opener = FakeOpener([response])

        with self.assertRaisesRegex(
            TemplatePublicationError,
            "^Private template publication failed\\.$",
        ) as caught:
            VastTemplateTransport(opener=opener).lookup_name(KEY, NAME)

        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(provider_marker, repr(caught.exception))
        self.assertNotIn(KEY, repr(caught.exception))
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

    def test_recursive_private_json_is_sanitized_before_lookup(self):
        nested = "[" * 1100 + json.dumps(KEY) + "]" * 1100
        for source in ("request", "settings"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                target = request_path if source == "request" else settings
                target.write_text(nested)
                os.chmod(target, 0o600)
                transport = ReadOnlyRecoveryTransport([], [])

                with self.assertRaisesRegex(
                    TemplatePublicationError,
                    "^Private template publication failed\\.$",
                ) as caught:
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

                self.assertIsNone(caught.exception.__cause__)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(transport.calls, [])
                self.assertEqual(tuple(output.iterdir()), ())

    def test_recursive_private_json_cli_failure_has_no_traceback_or_secret(self):
        nested = "[" * 1100 + json.dumps(KEY) + "]" * 1100
        for source in ("request", "settings"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                target = request_path if source == "request" else settings
                target.write_text(nested)
                os.chmod(target, 0o600)
                stdout = io.StringIO()
                stderr = io.StringIO()
                store = mock.Mock()
                store.path = settings

                with mock.patch(
                    "scripts.publish_worker_template.SettingsStore",
                    return_value=store,
                ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    with self.assertRaises(SystemExit) as caught:
                        main(
                            [
                                "reconcile",
                                "--request-file",
                                str(request_path),
                                "--output-directory",
                                str(output),
                            ]
                        )

                combined = stdout.getvalue() + stderr.getvalue()
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(
                    combined,
                    "Private template publication failed.\n",
                )
                self.assertNotIn(KEY, combined)
                self.assertNotIn("Traceback", combined)
                self.assertEqual(tuple(output.iterdir()), ())

    def test_recursive_embedded_lock_is_sanitized_before_lookup(self):
        recursive_request = request_with_recursive_lock()
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            request_path.write_text(json.dumps(recursive_request))
            os.chmod(request_path, 0o600)
            transport = ReadOnlyRecoveryTransport([], [])

            with self.assertRaisesRegex(
                TemplatePublicationError,
                "^Private template publication failed\\.$",
            ) as caught:
                self._reconcile_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=transport,
                )

            self.assertIsNone(caught.exception.__cause__)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertEqual(transport.calls, [])
            self.assertEqual(tuple(output.iterdir()), ())

    def test_recursive_embedded_lock_cli_has_no_traceback_or_secret(self):
        recursive_request = request_with_recursive_lock()
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            request_path.write_text(json.dumps(recursive_request))
            os.chmod(request_path, 0o600)
            stdout = io.StringIO()
            stderr = io.StringIO()
            store = mock.Mock()
            store.path = settings

            with mock.patch(
                "scripts.publish_worker_template.SettingsStore",
                return_value=store,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                with self.assertRaises(SystemExit) as caught:
                    main(
                        [
                            "reconcile",
                            "--request-file",
                            str(request_path),
                            "--output-directory",
                            str(output),
                        ]
                    )

            combined = stdout.getvalue() + stderr.getvalue()
            self.assertEqual(caught.exception.code, 1)
            self.assertEqual(
                combined,
                "Private template publication failed.\n",
            )
            self.assertNotIn(KEY, combined)
            self.assertNotIn("Traceback", combined)
            self.assertEqual(tuple(output.iterdir()), ())

    def test_publish_checks_absence_posts_once_and_verifies_exact_hash(self):
        request = template_request()
        responses = [
            FakeResponse({"success": False, "msg": "No templates found"}),
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

    def test_publish_rejects_unusable_record_destination_before_http(self):
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            public = Path(root) / "public"
            public.mkdir(mode=0o755)
            os.chmod(public, 0o755)
            read_only = Path(root) / "read-only"
            read_only.mkdir(mode=0o500)
            os.chmod(read_only, 0o500)
            symlink = Path(root) / "output-link"
            symlink.symlink_to(output, target_is_directory=True)
            existing_output = Path(root) / "existing-output"
            existing_output.mkdir(mode=0o700)
            os.chmod(existing_output, 0o700)
            existing = existing_output / "template-publication.json"
            existing.write_bytes(b"existing")
            os.chmod(existing, 0o600)

            for candidate in (
                Path(root) / "missing",
                public,
                read_only,
                symlink,
                existing_output,
            ):
                with self.subTest(candidate=candidate.name):
                    opener = FakeOpener([])
                    with self.assertRaises(TemplatePublicationError):
                        publish_worker_template(
                            request_path,
                            candidate,
                            settings_path_resolver=lambda: settings,
                            transport=VastTemplateTransport(opener=opener),
                        )
                    self.assertEqual(opener.calls, [])
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_publish_record_handles_partial_writes_and_fsyncs_parent(self):
        request = template_request()
        opener = FakeOpener(
            [
                FakeResponse(
                    {"success": True, "templates_found": 0, "templates": []}
                ),
                FakeResponse(
                    {
                        "success": True,
                        "msg": "created",
                        "template": {
                            "id": 17,
                            "name": NAME,
                            "hash_id": HASH_ID,
                        },
                    }
                ),
                FakeResponse(
                    {
                        "success": True,
                        "templates_found": 1,
                        "templates": [template_row(request)],
                    }
                ),
            ]
        )
        real_write = os.write
        real_fsync = os.fsync
        fsync_types = []

        def partial_write(descriptor, content):
            return real_write(descriptor, content[:7])

        def recording_fsync(descriptor):
            fsync_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
            return real_fsync(descriptor)

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            with mock.patch(
                "scripts.publish_worker_template.os.write",
                side_effect=partial_write,
            ), mock.patch(
                "scripts.publish_worker_template.os.fsync",
                side_effect=recording_fsync,
            ):
                result = publish_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=opener),
                )

            artifact = output / "template-publication.json"
            self.assertEqual(json.loads(artifact.read_text()), result)
            self.assertEqual(tuple(output.glob(".template-publication.json.*.part")), ())
        self.assertIn(stat.S_IFREG, fsync_types)
        self.assertIn(stat.S_IFDIR, fsync_types)

    def test_publish_intent_allows_only_one_overlapping_post(self):
        request = template_request()
        second_results = []
        second_errors = []
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            second_output = Path(root) / "second-output"
            second_output.mkdir(mode=0o700)
            os.chmod(second_output, 0o700)
            second_opener = FakeOpener(
                [
                    FakeResponse(
                        {
                            "success": True,
                            "templates_found": 0,
                            "templates": [],
                        }
                    ),
                    FakeResponse(
                        {
                            "success": True,
                            "msg": "created",
                            "template": {
                                "id": 17,
                                "name": NAME,
                                "hash_id": HASH_ID,
                            },
                        }
                    ),
                    FakeResponse(
                        {
                            "success": True,
                            "templates_found": 1,
                            "templates": [template_row(request)],
                        }
                    ),
                ]
            )

            class OverlappingTransport:
                def __init__(self):
                    self.post_calls = 0

                def lookup_name(self, api_key, name):
                    return []

                def create_worker_template(self, api_key, template_payload):
                    self.post_calls += 1
                    try:
                        second_results.append(
                            publish_worker_template(
                                request_path,
                                second_output,
                                settings_path_resolver=lambda: settings,
                                transport=VastTemplateTransport(
                                    opener=second_opener
                                ),
                            )
                        )
                    except TemplatePublicationError as exception:
                        second_errors.append(exception)
                    return {
                        "success": True,
                        "msg": "created",
                        "template": {
                            "id": 17,
                            "name": NAME,
                            "hash_id": HASH_ID,
                        },
                    }

                def lookup_hash(self, api_key, hash_id):
                    return [template_row(request)]

            transport = OverlappingTransport()
            result = publish_worker_template(
                request_path,
                output,
                settings_path_resolver=lambda: settings,
                transport=transport,
            )

            self.assertEqual(result["hash_id"], HASH_ID)
            self.assertEqual(transport.post_calls, 1)
            self.assertEqual(second_results, [])
            self.assertEqual(len(second_errors), 1)
            self.assertEqual(second_opener.calls, [])
            self.assertFalse(
                (second_output / "template-publication.json").exists()
            )
            self.assertEqual(
                tuple(settings.parent.glob(".template-publication-*.intent")),
                (),
            )

    def test_ambiguous_post_intent_blocks_restart_from_posting_again(self):
        first_opener = FakeOpener(
            [
                FakeResponse(
                    {"success": True, "templates_found": 0, "templates": []}
                ),
                OSError("ambiguous " + KEY),
                OSError("still ambiguous " + KEY),
            ]
        )
        second_opener = FakeOpener(
            [
                FakeResponse(
                    {"success": True, "templates_found": 0, "templates": []}
                ),
                FakeResponse(
                    {
                        "success": True,
                        "msg": "created",
                        "template": {
                            "id": 17,
                            "name": NAME,
                            "hash_id": HASH_ID,
                        },
                    }
                ),
                FakeResponse(
                    {
                        "success": True,
                        "templates_found": 1,
                        "templates": [template_row()],
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            with self.assertRaises(TemplatePublicationError):
                publish_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=first_opener),
                )
            with self.assertRaises(TemplatePublicationError):
                publish_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=VastTemplateTransport(opener=second_opener),
                )

            intents = tuple(
                settings.parent.glob(".template-publication-*.intent")
            )
            self.assertEqual(len(intents), 1)
            self.assertEqual(stat.S_IMODE(intents[0].stat().st_mode), 0o600)
            self.assertEqual(second_opener.calls, [])
            self.assertEqual(
                tuple(output.glob(".template-publication.json.*.part")),
                (),
            )
        self.assertEqual(
            [call[0].method for call in first_opener.calls],
            ["GET", "POST", "GET"],
        )

    def test_reconcile_reads_exact_name_and_hash_without_mutation(self):
        request = template_request()
        row = template_row(request)
        row["extra_filters"] = json.dumps(
            EXTRA_FILTERS,
            separators=(",", ":"),
        )
        transport = ReadOnlyRecoveryTransport(
            [row],
            [row],
        )
        self.assertFalse(hasattr(transport, "create_worker_template"))

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)

            result = self._reconcile_worker_template(
                request_path,
                output,
                settings_path_resolver=lambda: settings,
                transport=transport,
            )

            artifact = output / "template-publication.json"
            self.assertEqual(
                result,
                {"id": 17, "hash_id": HASH_ID, "verified": True},
            )
            self.assertEqual(json.loads(artifact.read_text()), result)
            self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)
            self.assertFalse(intent.exists())
            self.assertNotIn(KEY, artifact.read_text())

        self.assertEqual(
            [(call[0], call[2]) for call in transport.calls],
            [("lookup_name", NAME), ("lookup_hash", HASH_ID)],
        )

    def test_reconcile_retains_intent_when_intent_directory_fsync_fails(self):
        request = template_request()
        transport = ReadOnlyRecoveryTransport(
            [template_row(request)],
            [template_row(request)],
        )
        publisher = __import__(
            "scripts.publish_worker_template",
            fromlist=["_fsync_directory"],
        )
        real_fsync_directory = publisher._fsync_directory

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            settings_fsync_calls = 0

            def fail_intent_fsync_once(directory):
                nonlocal settings_fsync_calls
                if Path(directory) == settings.parent:
                    settings_fsync_calls += 1
                    if settings_fsync_calls == 2:
                        raise OSError("synthetic intent fsync failure")
                return real_fsync_directory(directory)

            with mock.patch(
                "scripts.publish_worker_template._fsync_directory",
                side_effect=fail_intent_fsync_once,
            ):
                with self.assertRaisesRegex(
                    TemplatePublicationError,
                    "^Private template publication failed\\.$",
                ):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertGreaterEqual(settings_fsync_calls, 2)
            self.assertEqual(intent.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
            self.assertEqual(intent.stat().st_nlink, 1)
            recovery = publication_recovery_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_prepares_recovery_before_unlinking_intent(self):
        request = template_request()
        real_mkstemp = tempfile.mkstemp
        real_write = os.write
        real_fsync = os.fsync

        for failure in ("open", "write", "fsync"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                intent = write_publication_intent(settings, request)
                original = intent.read_bytes()
                original_inode = intent.stat().st_ino
                recovery_descriptors = set()
                triggered = False

                def failing_mkstemp(*args, **kwargs):
                    nonlocal triggered
                    prefix = kwargs.get("prefix", "")
                    if ".recovery." not in prefix:
                        return real_mkstemp(*args, **kwargs)
                    if failure == "open":
                        triggered = True
                        raise OSError("synthetic recovery open failure")
                    descriptor, path = real_mkstemp(*args, **kwargs)
                    recovery_descriptors.add(descriptor)
                    return descriptor, path

                def failing_write(descriptor, content):
                    nonlocal triggered
                    if failure == "write" and descriptor in recovery_descriptors:
                        triggered = True
                        raise OSError("synthetic recovery write failure")
                    return real_write(descriptor, content)

                def failing_fsync(descriptor):
                    nonlocal triggered
                    if failure == "fsync" and descriptor in recovery_descriptors:
                        triggered = True
                        raise OSError("synthetic recovery fsync failure")
                    return real_fsync(descriptor)

                transport = ReadOnlyRecoveryTransport(
                    [template_row(request)],
                    [template_row(request)],
                )
                with mock.patch(
                    "scripts.publish_worker_template.tempfile.mkstemp",
                    side_effect=failing_mkstemp,
                ), mock.patch(
                    "scripts.publish_worker_template.os.write",
                    side_effect=failing_write,
                ), mock.patch(
                    "scripts.publish_worker_template.os.fsync",
                    side_effect=failing_fsync,
                ):
                    with self.assertRaises(TemplatePublicationError):
                        self._reconcile_worker_template(
                            request_path,
                            output,
                            settings_path_resolver=lambda: settings,
                            transport=transport,
                        )

                self.assertTrue(triggered)
                self.assertEqual(intent.read_bytes(), original)
                self.assertEqual(intent.stat().st_ino, original_inode)
                self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
                self.assertEqual(intent.stat().st_nlink, 1)
                self.assertEqual(publication_recovery_paths(settings), ())
                self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_restores_prepared_copy_without_rewriting_bytes(self):
        request = template_request()
        real_fsync_directory = __import__(
            "scripts.publish_worker_template",
            fromlist=["_fsync_directory"],
        )._fsync_directory
        real_open = os.open

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            settings_fsync_calls = 0

            def fail_unlink_fsync_once(directory):
                nonlocal settings_fsync_calls
                if Path(directory) == settings.parent:
                    settings_fsync_calls += 1
                    if settings_fsync_calls == 2:
                        raise OSError("synthetic post-unlink fsync failure")
                return real_fsync_directory(directory)

            def forbid_recreating_intent(path, flags, *args, **kwargs):
                if (
                    Path(path) == intent
                    and flags & os.O_CREAT
                    and not intent.exists()
                ):
                    raise OSError("intent bytes must not be rewritten")
                return real_open(path, flags, *args, **kwargs)

            transport = ReadOnlyRecoveryTransport(
                [template_row(request)],
                [template_row(request)],
            )
            with mock.patch(
                "scripts.publish_worker_template._fsync_directory",
                side_effect=fail_unlink_fsync_once,
            ), mock.patch(
                "scripts.publish_worker_template.os.open",
                side_effect=forbid_recreating_intent,
            ):
                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertGreaterEqual(settings_fsync_calls, 2)
            self.assertEqual(intent.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
            self.assertEqual(intent.stat().st_nlink, 1)
            recovery = publication_recovery_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_retains_prepared_copy_when_restore_rename_fails(self):
        request = template_request()
        publisher = __import__(
            "scripts.publish_worker_template",
            fromlist=["_fsync_directory"],
        )
        real_fsync_directory = publisher._fsync_directory
        real_mkstemp = tempfile.mkstemp
        real_close = os.close
        real_rename_no_replace = getattr(
            publisher,
            "_rename_no_replace",
            None,
        )

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            settings_fsync_calls = 0
            failed_restore = False
            recovery_descriptors = set()
            closed_descriptors = set()

            def recording_mkstemp(*args, **kwargs):
                descriptor, path = real_mkstemp(*args, **kwargs)
                if ".recovery." in kwargs.get("prefix", ""):
                    recovery_descriptors.add(descriptor)
                return descriptor, path

            def recording_close(descriptor):
                closed_descriptors.add(descriptor)
                return real_close(descriptor)

            def fail_unlink_fsync_once(directory):
                nonlocal settings_fsync_calls
                if Path(directory) == settings.parent:
                    settings_fsync_calls += 1
                    if settings_fsync_calls == 2:
                        raise OSError("synthetic post-unlink fsync failure")
                return real_fsync_directory(directory)

            def fail_retired_restore(source, destination):
                nonlocal failed_restore
                if (
                    Path(destination) == intent
                    and ".retired." in Path(source).name
                ):
                    failed_restore = True
                    raise OSError("synthetic recovery rename failure")
                if real_rename_no_replace is None:
                    raise AssertionError("no-replace rename missing")
                return real_rename_no_replace(source, destination)

            transport = ReadOnlyRecoveryTransport(
                [template_row(request)],
                [template_row(request)],
            )
            with mock.patch(
                "scripts.publish_worker_template._fsync_directory",
                side_effect=fail_unlink_fsync_once,
            ), mock.patch.object(
                publisher,
                "_rename_no_replace",
                side_effect=fail_retired_restore,
                create=True,
            ), mock.patch(
                "scripts.publish_worker_template.tempfile.mkstemp",
                side_effect=recording_mkstemp,
            ), mock.patch(
                "scripts.publish_worker_template.os.close",
                side_effect=recording_close,
            ):
                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertGreaterEqual(settings_fsync_calls, 2)
            self.assertTrue(failed_restore)
            recovery = publication_recovery_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(recovery[0].stat().st_mode), 0o600)
            self.assertEqual(recovery[0].stat().st_nlink, 1)
            self.assertFalse(recovery[0].is_symlink())
            self.assertFalse(intent.exists())
            retired = publication_retired_paths(settings)
            self.assertEqual(len(retired), 1)
            self.assertEqual(retired[0].read_bytes(), original)
            self.assertTrue(recovery_descriptors)
            self.assertTrue(recovery_descriptors.issubset(closed_descriptors))
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_detects_same_inode_intent_rewrite_before_removal(self):
        request = template_request()
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            original_inode = intent.stat().st_ino
            mutated_content = []

            class RewritingTransport(ReadOnlyRecoveryTransport):
                def lookup_hash(self, api_key, hash_id):
                    mutated = bytearray(original)
                    marker = mutated.index(b'"request_sha256":"') + len(
                        b'"request_sha256":"'
                    )
                    mutated[marker] = (
                        ord("0")
                        if mutated[marker] != ord("0")
                        else ord("1")
                    )
                    mutated_content.append(bytes(mutated))
                    with intent.open("r+b") as stream:
                        stream.write(mutated)
                        stream.truncate()
                        stream.flush()
                        os.fsync(stream.fileno())
                    self.calls.append(("lookup_hash", api_key, hash_id))
                    return self.hash_rows

            transport = RewritingTransport(
                [template_row(request)],
                [template_row(request)],
            )
            with self.assertRaises(TemplatePublicationError):
                self._reconcile_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=transport,
                )

            self.assertEqual(intent.read_bytes(), mutated_content[0])
            self.assertEqual(intent.stat().st_ino, original_inode)
            self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
            self.assertEqual(intent.stat().st_nlink, 1)
            recovery = publication_recovery_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(recovery[0].stat().st_mode), 0o600)
            self.assertEqual(recovery[0].stat().st_nlink, 1)
            self.assertFalse(recovery[0].is_symlink())
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_detects_rewrite_after_final_validation(self):
        request = template_request()
        publisher = __import__(
            "scripts.publish_worker_template",
            fromlist=["_rename_no_replace"],
        )
        real_rename_no_replace = getattr(
            publisher,
            "_rename_no_replace",
            None,
        )

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            original_inode = intent.stat().st_ino
            mutated = bytearray(original)
            marker = mutated.index(b'"request_sha256":"') + len(
                b'"request_sha256":"'
            )
            mutated[marker] = (
                ord("0") if mutated[marker] != ord("0") else ord("1")
            )
            mutated = bytes(mutated)
            rewrote = False

            def rewrite_immediately_before_move(source, destination):
                nonlocal rewrote
                if Path(source) == intent and ".retired." in Path(destination).name:
                    rewrote = True
                    with intent.open("r+b") as stream:
                        stream.write(mutated)
                        stream.truncate()
                        stream.flush()
                        os.fsync(stream.fileno())
                if real_rename_no_replace is None:
                    raise AssertionError("no-replace rename missing")
                return real_rename_no_replace(source, destination)

            transport = ReadOnlyRecoveryTransport(
                [template_row(request)],
                [template_row(request)],
            )
            with mock.patch.object(
                publisher,
                "_rename_no_replace",
                side_effect=rewrite_immediately_before_move,
                create=True,
            ):
                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertTrue(rewrote)
            self.assertEqual(intent.read_bytes(), mutated)
            self.assertEqual(intent.stat().st_ino, original_inode)
            self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
            self.assertEqual(intent.stat().st_nlink, 1)
            recovery = publication_recovery_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(recovery[0].stat().st_mode), 0o600)
            self.assertEqual(recovery[0].stat().st_nlink, 1)
            self.assertEqual(publication_retired_paths(settings), ())
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_restore_never_overwrites_competing_intent(self):
        request = template_request()
        publisher = __import__(
            "scripts.publish_worker_template",
            fromlist=["_rename_no_replace"],
        )
        real_fsync_directory = publisher._fsync_directory
        real_rename_no_replace = getattr(
            publisher,
            "_rename_no_replace",
            None,
        )

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            original = intent.read_bytes()
            competing = bytearray(original)
            marker = competing.index(b'"request_sha256":"') + len(
                b'"request_sha256":"'
            )
            competing[marker] = (
                ord("0")
                if competing[marker] != ord("0")
                else ord("1")
            )
            competing = bytes(competing)
            settings_fsync_calls = 0
            competitor_created = False

            def fail_post_move_fsync(directory):
                nonlocal settings_fsync_calls
                if Path(directory) == settings.parent:
                    settings_fsync_calls += 1
                    if settings_fsync_calls == 2:
                        raise OSError("synthetic post-move fsync failure")
                return real_fsync_directory(directory)

            def create_competitor_before_restore(source, destination):
                nonlocal competitor_created
                if (
                    Path(destination) == intent
                    and ".retired." in Path(source).name
                ):
                    competitor_created = True
                    intent.write_bytes(competing)
                    os.chmod(intent, 0o600)
                if real_rename_no_replace is None:
                    raise AssertionError("no-replace rename missing")
                return real_rename_no_replace(source, destination)

            transport = ReadOnlyRecoveryTransport(
                [template_row(request)],
                [template_row(request)],
            )
            with mock.patch(
                "scripts.publish_worker_template._fsync_directory",
                side_effect=fail_post_move_fsync,
            ), mock.patch.object(
                publisher,
                "_rename_no_replace",
                side_effect=create_competitor_before_restore,
                create=True,
            ):
                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertGreaterEqual(settings_fsync_calls, 2)
            self.assertTrue(competitor_created)
            self.assertEqual(intent.read_bytes(), competing)
            self.assertEqual(stat.S_IMODE(intent.stat().st_mode), 0o600)
            self.assertEqual(intent.stat().st_nlink, 1)
            recovery = publication_recovery_paths(settings)
            retired = publication_retired_paths(settings)
            self.assertEqual(len(recovery), 1)
            self.assertEqual(recovery[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(recovery[0].stat().st_mode), 0o600)
            self.assertEqual(recovery[0].stat().st_nlink, 1)
            self.assertEqual(len(retired), 1)
            self.assertEqual(retired[0].read_bytes(), original)
            self.assertEqual(stat.S_IMODE(retired[0].stat().st_mode), 0o600)
            self.assertEqual(retired[0].stat().st_nlink, 1)
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_requires_exact_private_intent_before_lookup(self):
        request = template_request()
        encoded_request = json.dumps(
            request,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        exact_hash = hashlib.sha256(encoded_request).hexdigest()
        invalid_payloads = (
            {"schema_version": 2, "name": NAME, "request_sha256": exact_hash},
            {"schema_version": True, "name": NAME, "request_sha256": exact_hash},
            {"schema_version": 1, "name": "wrong-name", "request_sha256": exact_hash},
            {"schema_version": 1, "name": NAME, "request_sha256": "d" * 64},
            {"schema_version": 1, "name": NAME, "request_sha256": True},
            {
                "schema_version": 1,
                "name": NAME,
                "request_sha256": exact_hash,
                "extra": True,
            },
        )

        for index, payload in enumerate(invalid_payloads):
            with self.subTest(payload=index), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                intent = write_publication_intent(
                    settings,
                    request,
                    payload=payload,
                )
                original = intent.read_bytes()
                transport = ReadOnlyRecoveryTransport([template_row(request)])

                with self.assertRaisesRegex(
                    TemplatePublicationError,
                    "^Private template publication failed\\.$",
                ):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

                self.assertEqual(intent.read_bytes(), original)
                self.assertEqual(transport.calls, [])
                self.assertEqual(tuple(output.iterdir()), ())

        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            transport = ReadOnlyRecoveryTransport([template_row(request)])

            with self.assertRaises(TemplatePublicationError):
                self._reconcile_worker_template(
                    request_path,
                    output,
                    settings_path_resolver=lambda: settings,
                    transport=transport,
                )

            self.assertEqual(transport.calls, [])
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_rejects_unsafe_intent_metadata_before_lookup(self):
        request = template_request()
        cases = ("directory", "symlink", "hardlink", "public_mode")

        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                intent = publication_intent_path(settings)
                if case == "directory":
                    intent.mkdir(mode=0o700)
                elif case == "symlink":
                    target = settings.parent / "intent-target"
                    write_publication_intent(settings, request)
                    intent.rename(target)
                    intent.symlink_to(target)
                else:
                    write_publication_intent(settings, request)
                    if case == "hardlink":
                        os.link(intent, settings.parent / "intent-peer")
                    else:
                        os.chmod(intent, 0o640)
                transport = ReadOnlyRecoveryTransport([template_row(request)])

                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

                self.assertTrue(intent.exists() or intent.is_symlink())
                self.assertEqual(transport.calls, [])
                self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_rejects_wrong_intent_owner_before_lookup(self):
        request = template_request()
        real_lstat = os.lstat
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            intent = write_publication_intent(settings, request)
            transport = ReadOnlyRecoveryTransport([template_row(request)])

            def wrong_owner(path):
                metadata = real_lstat(path)
                if Path(path) != intent:
                    return metadata
                values = list(metadata)
                values[4] = metadata.st_uid + 1
                return os.stat_result(values)

            with mock.patch(
                "scripts.publish_worker_template.os.lstat",
                side_effect=wrong_owner,
            ):
                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

            self.assertTrue(intent.exists())
            self.assertEqual(transport.calls, [])
            self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_rejects_zero_multiple_or_mismatched_name_rows(self):
        request = template_request()
        invalid_rows = (
            [],
            [template_row(request), template_row(request)],
            [{**template_row(request), "tag": "v0.29.0-cuda-12.8-py312"}],
        )

        for index, rows in enumerate(invalid_rows):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                intent = write_publication_intent(settings, request)
                transport = ReadOnlyRecoveryTransport(rows)

                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

                self.assertTrue(intent.exists())
                self.assertEqual(
                    [call[0] for call in transport.calls],
                    ["lookup_name"],
                )
                self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_rejects_non_exact_hash_readback(self):
        request = template_request()
        invalid_rows = (
            [],
            [template_row(request), template_row(request)],
            [template_row(request, template_id=18)],
            [template_row(request, hash_id="f" * 32)],
            [{**template_row(request), "env": "-p 9999:9999"}],
        )

        for index, rows in enumerate(invalid_rows):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                intent = write_publication_intent(settings, request)
                transport = ReadOnlyRecoveryTransport(
                    [template_row(request)],
                    rows,
                )

                with self.assertRaises(TemplatePublicationError):
                    self._reconcile_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=transport,
                    )

                self.assertTrue(intent.exists())
                self.assertEqual(
                    [call[0] for call in transport.calls],
                    ["lookup_name", "lookup_hash"],
                )
                self.assertEqual(tuple(output.iterdir()), ())

    def test_reconcile_cli_emits_only_safe_publication_fields(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        request_path = Path("/private/template-request.json")
        output = Path("/private/output")
        record = {"id": 17, "hash_id": HASH_ID, "verified": True}

        with mock.patch(
            "scripts.publish_worker_template.reconcile_worker_template",
            return_value=record,
        ) as reconcile, contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            main(
                [
                    "reconcile",
                    "--request-file",
                    str(request_path),
                    "--output-directory",
                    str(output),
                ]
            )

        reconcile.assert_called_once_with(request_path, output)
        self.assertEqual(
            stdout.getvalue().splitlines(),
            [
                "action=reconcile",
                "id=17",
                "hash_id=" + HASH_ID,
                "verified=true",
            ],
        )
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_help_describes_reconcile(self):
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as caught:
                main(["--help"])

        self.assertEqual(caught.exception.code, 0)
        self.assertIn(
            "Audit, publish, or reconcile the fixed private Vast worker template.",
            stdout.getvalue(),
        )

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

    def test_publish_rejects_non_exact_runtime_and_filters_before_http(self):
        missing_filters = template_request()
        missing_filters.pop("extra_filters")
        invalid_requests = [
            missing_filters,
            template_request(
                image="docker.io/vastai/comfy@sha256:" + "d" * 64
            ),
            template_request(tag="v0.29.0-cuda-12.8-py312"),
        ]
        invalid_requests.extend(
            template_request(extra_filters=filters)
            for filters in invalid_extra_filters()
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

    def test_publish_rejects_oversized_onstart_before_credentials_or_http(self):
        request = template_request()
        request["onstart"] = "x" * 16385
        canonical = template_request()
        bootstrap = (
            REPOSITORY_ROOT / "remote_worker" / "bootstrap.py"
        ).read_bytes()
        bootstrap_encoded = base64.b64encode(bootstrap).decode("ascii")
        lock_line = next(
            line
            for line in canonical["onstart"].splitlines()
            if line.startswith("printf '%s' '")
            and line.endswith(
                " > /opt/comfyui-cloud-run-bootstrap/release-lock.json"
            )
        )
        lock_encoded = lock_line.split("'", 2)[1]
        match = mock.Mock()
        match.group.side_effect = {
            1: bootstrap_encoded,
            2: lock_encoded,
            3: WORKER_COMMIT,
        }.__getitem__
        pattern = mock.Mock()
        pattern.fullmatch.return_value = match
        settings_resolver = mock.Mock(
            side_effect=AssertionError("credential lookup must not run")
        )

        with tempfile.TemporaryDirectory() as root:
            _settings, output, request_path = self._private_inputs(root)
            request_path.write_text(json.dumps(request))
            os.chmod(request_path, 0o600)
            opener = FakeOpener([])
            with mock.patch(
                "scripts.publish_worker_template._ONSTART",
                pattern,
            ):
                with self.assertRaises(TemplatePublicationError):
                    publish_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=settings_resolver,
                        transport=VastTemplateTransport(opener=opener),
                    )

        pattern.fullmatch.assert_not_called()
        settings_resolver.assert_not_called()
        self.assertEqual(opener.calls, [])

    def test_publish_rejects_noncanonical_compressed_bootstrap_before_http(self):
        bootstrap = (
            REPOSITORY_ROOT / "remote_worker" / "bootstrap.py"
        ).read_bytes()
        request = template_request(
            bootstrap_gzip=gzip.compress(
                bootstrap,
                compresslevel=9,
                mtime=1,
            )
        )
        with tempfile.TemporaryDirectory() as root:
            settings, output, request_path = self._private_inputs(root)
            request_path.write_text(json.dumps(request))
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

    def test_ambiguous_post_reconciliation_rejects_runtime_or_filter_mismatch(self):
        altered_rows = []
        for field, value in (
            ("image", "docker.io/vastai/comfy@sha256:" + "d" * 64),
            ("tag", "v0.29.0-cuda-12.8-py312"),
            (
                "extra_filters",
                {**EXTRA_FILTERS, "num_gpus": {"eq": True}},
            ),
            (
                "extra_filters",
                {**EXTRA_FILTERS, "compute_cap": {"gte": 800}},
            ),
        ):
            altered_rows.append({**template_row(), field: value})

        for index, row in enumerate(altered_rows):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                opener = FakeOpener(
                    [
                        FakeResponse(
                            {
                                "success": True,
                                "templates_found": 0,
                                "templates": [],
                            }
                        ),
                        OSError("ambiguous " + KEY),
                        FakeResponse(
                            {
                                "success": True,
                                "templates_found": 1,
                                "templates": [row],
                            }
                        ),
                    ]
                )
                with self.assertRaises(TemplatePublicationError):
                    publish_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=VastTemplateTransport(opener=opener),
                    )
                self.assertEqual(
                    [call[0].method for call in opener.calls].count("POST"),
                    1,
                )
                self.assertEqual(tuple(output.iterdir()), ())

    def test_exact_hash_readback_rejects_runtime_or_filter_mismatch(self):
        altered_rows = [
            {**template_row(), field: value}
            for field, value in (
                ("image", "docker.io/vastai/comfy@sha256:" + "d" * 64),
                ("tag", "v0.29.0-cuda-12.8-py312"),
            )
        ]
        altered_rows.extend(
            {**template_row(), "extra_filters": filters}
            for filters in invalid_extra_filters()
        )

        for index, row in enumerate(altered_rows):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as root:
                settings, output, request_path = self._private_inputs(root)
                opener = FakeOpener(
                    [
                        FakeResponse(
                            {
                                "success": True,
                                "templates_found": 0,
                                "templates": [],
                            }
                        ),
                        FakeResponse(
                            {
                                "success": True,
                                "msg": "created",
                                "template": {
                                    "id": 17,
                                    "name": NAME,
                                    "hash_id": HASH_ID,
                                },
                            }
                        ),
                        FakeResponse(
                            {
                                "success": True,
                                "templates_found": 1,
                                "templates": [row],
                            }
                        ),
                    ]
                )
                with self.assertRaises(TemplatePublicationError):
                    publish_worker_template(
                        request_path,
                        output,
                        settings_path_resolver=lambda: settings,
                        transport=VastTemplateTransport(opener=opener),
                    )
                self.assertEqual(
                    [call[0].method for call in opener.calls],
                    ["GET", "POST", "GET"],
                )
                self.assertEqual(tuple(output.iterdir()), ())

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

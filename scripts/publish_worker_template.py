#!/usr/bin/env python3
"""Audit and publish the one reviewed private Vast worker template."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib import error, parse, request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_run.settings import SettingsStore
from scripts.build_worker_release_bundle import (
    ReleaseBuildError,
    ReleaseMetadata,
)


BASE_TEMPLATE_HASH_ID = "027fba7753c024be019030fb42aed900"
TEMPLATE_ENDPOINT = "https://console.vast.ai/api/v0/template/"
LOOKUP_COLUMNS = [
    "id",
    "name",
    "hash_id",
    "image",
    "tag",
    "env",
    "onstart",
    "runtype",
    "ssh_direct",
    "use_ssh",
    "jup_direct",
    "jupyter_dir",
    "use_jupyter_lab",
    "docker_login_repo",
    "docker_login_user",
    "docker_login_pass",
    "recommended_disk_space",
    "private",
]
HTTP_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 256 * 1024
MAX_PRIVATE_JSON_BYTES = 64 * 1024

_FAILURE = "Private template publication failed."
_NAME = re.compile(r"cloud-run-worker-([0-9a-f]{40})")
_HASH = re.compile(r"[0-9a-f]{32}")
_IMAGE = re.compile(
    r"docker\.io/vastai/base-image@sha256:[0-9a-f]{64}"
)
_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MUTABLE_TAGS = {"edge", "latest", "main", "master", "nightly", "stable"}
_REQUEST_FIELDS = {
    "name",
    "image",
    "tag",
    "runtype",
    "use_ssh",
    "ssh_direct",
    "jup_direct",
    "jupyter_dir",
    "use_jupyter_lab",
    "docker_login_repo",
    "docker_login_user",
    "docker_login_pass",
    "onstart",
    "env",
    "recommended_disk_space",
    "private",
}
_REMOTE_LOCK_FIELDS = {
    "schema_version",
    "archive_url",
    "worker_commit",
    "worker_archive_sha256",
    "worker_archive_size_bytes",
    "protocol_version",
    "comfyui_core_version",
    "comfyui_frontend_version",
    "python_version",
    "destination",
}
_ROW_FIELDS = set(LOOKUP_COLUMNS)
_ONSTART = re.compile(
    r"#!/bin/sh\n"
    r"set -eu\n"
    r"umask 077\n"
    r"mkdir -m 0700 /opt/comfyui-cloud-run-bootstrap\n"
    r"printf '%s' '([A-Za-z0-9+/]+={0,2})' \| base64 -d > "
    r"/opt/comfyui-cloud-run-bootstrap/bootstrap\.py\n"
    r"chmod 0600 /opt/comfyui-cloud-run-bootstrap/bootstrap\.py\n"
    r"printf '%s' '([A-Za-z0-9+/]+={0,2})' \| base64 -d > "
    r"/opt/comfyui-cloud-run-bootstrap/release-lock\.json\n"
    r"chmod 0600 /opt/comfyui-cloud-run-bootstrap/release-lock\.json\n"
    r"CLOUD_RUN_WORKER_VERSION=([0-9a-f]{40})\n"
    r"export CLOUD_RUN_WORKER_VERSION\n"
    r"exec python3 /opt/comfyui-cloud-run-bootstrap/bootstrap\.py "
    r"/opt/comfyui-cloud-run-bootstrap/release-lock\.json\n"
)


class TemplatePublicationError(RuntimeError):
    """The fixed private-template operation failed without secret detail."""


class _PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Private template command is invalid.\n")


class _RejectRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener():
    return request.build_opener(request.ProxyHandler({}), _RejectRedirect())


def _fail():
    raise TemplatePublicationError(_FAILURE) from None


def _compact_json(payload):
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail()


class VastTemplateTransport:
    """Bounded fixed-endpoint transport for GET template and one POST."""

    def __init__(self, opener=None):
        self._opener = opener if opener is not None else _default_opener()

    def _response_object(self, http_request):
        response = None
        try:
            response = self._opener.open(
                http_request,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
            status_code = getattr(response, "status", None)
            if status_code is None and hasattr(response, "getcode"):
                status_code = response.getcode()
            if status_code != 200:
                _fail()
            content_encoding = response.headers.get(
                "Content-Encoding", "identity"
            )
            if content_encoding.casefold() != "identity":
                _fail()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                _fail()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                _fail()
            return payload
        except TemplatePublicationError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            _fail()
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    @staticmethod
    def _headers(api_key):
        if not isinstance(api_key, str) or not api_key:
            _fail()
        return {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Authorization": "Bearer " + api_key,
        }

    def _lookup(self, api_key, filters):
        query = parse.urlencode(
            {
                "select_filters": _compact_json(filters).decode("ascii"),
                "select_cols": json.dumps(
                    LOOKUP_COLUMNS, separators=(",", ":")
                ),
                "order_by": "id",
            }
        )
        http_request = request.Request(
            TEMPLATE_ENDPOINT + "?" + query,
            headers=self._headers(api_key),
            method="GET",
        )
        return _normalize_lookup(self._response_object(http_request))

    def lookup_base(self, api_key):
        return self._lookup(
            api_key,
            {"hash_id": {"eq": BASE_TEMPLATE_HASH_ID}},
        )

    def lookup_name(self, api_key, name):
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            _fail()
        return self._lookup(api_key, {"name": {"eq": name}})

    def lookup_hash(self, api_key, hash_id):
        if not isinstance(hash_id, str) or _HASH.fullmatch(hash_id) is None:
            _fail()
        return self._lookup(api_key, {"hash_id": {"eq": hash_id}})

    def create_worker_template(self, api_key, template_payload):
        template_payload = _validate_request(template_payload)
        http_request = request.Request(
            TEMPLATE_ENDPOINT,
            data=_compact_json(template_payload),
            headers={
                **self._headers(api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._response_object(http_request)


def _normalize_row(row):
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        _fail()
    template_id = row.get("id")
    hash_id = row.get("hash_id")
    image = row.get("image")
    tag = row.get("tag")
    if (
        type(template_id) is not int
        or template_id <= 0
        or not isinstance(row.get("name"), str)
        or not row["name"]
        or not isinstance(hash_id, str)
        or _HASH.fullmatch(hash_id) is None
        or not isinstance(image, str)
        or _IMAGE.fullmatch(image) is None
        or not isinstance(tag, str)
        or _TAG.fullmatch(tag) is None
        or tag.casefold() in _MUTABLE_TAGS
        or not isinstance(row.get("env"), str)
        or not isinstance(row.get("onstart"), str)
        or len(row["onstart"].encode("utf-8")) > MAX_PRIVATE_JSON_BYTES
        or not isinstance(row.get("runtype"), str)
        or type(row.get("ssh_direct")) is not bool
        or type(row.get("use_ssh")) is not bool
        or type(row.get("jup_direct")) is not bool
        or not isinstance(row.get("jupyter_dir"), str)
        or type(row.get("use_jupyter_lab")) is not bool
        or row.get("docker_login_repo") != ""
        or row.get("docker_login_user") != ""
        or row.get("docker_login_pass") != ""
        or type(row.get("recommended_disk_space")) not in {int, float}
        or row["recommended_disk_space"] <= 0
        or type(row.get("private")) is not bool
    ):
        _fail()
    return {column: row[column] for column in LOOKUP_COLUMNS}


def _normalize_lookup(payload):
    if not isinstance(payload, dict) or set(payload) != {
        "success",
        "templates_found",
        "templates",
    }:
        _fail()
    count = payload.get("templates_found")
    templates = payload.get("templates")
    if (
        payload.get("success") is not True
        or type(count) is not int
        or count not in {0, 1}
        or not isinstance(templates, list)
        or len(templates) != count
    ):
        _fail()
    return [_normalize_row(row) for row in templates]


def _private_path(path, *, require_directory=False):
    candidate = Path(path)
    try:
        metadata = os.lstat(candidate)
    except OSError:
        _fail()
    expected_type = stat.S_ISDIR if require_directory else stat.S_ISREG
    if (
        not expected_type(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        _fail()
    return candidate


def _read_private_json(path):
    candidate = Path(path)
    parent = _private_path(candidate.parent, require_directory=True)
    del parent
    before = None
    descriptor = None
    try:
        before = os.lstat(candidate)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_PRIVATE_JSON_BYTES
        ):
            _fail()
        if not hasattr(os, "O_NOFOLLOW"):
            _fail()
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size != before.st_size
        ):
            _fail()
        content = bytearray()
        while len(content) <= MAX_PRIVATE_JSON_BYTES:
            chunk = os.read(
                descriptor,
                min(8192, MAX_PRIVATE_JSON_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != opened.st_size
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
        ):
            _fail()
        payload = json.loads(bytes(content).decode("utf-8"))
        if not isinstance(payload, dict):
            _fail()
        return payload
    except TemplatePublicationError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        _fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _credential(settings_path_resolver):
    try:
        settings_path = settings_path_resolver()
    except Exception:
        _fail()
    payload = _read_private_json(settings_path)
    key = payload.get("api_key")
    if (
        not isinstance(key, str)
        or key != key.strip()
        or not 1 <= len(key) <= 4096
        or any(ord(character) < 33 or ord(character) > 126 for character in key)
    ):
        _fail()
    return key


def _resolved_settings_path():
    return SettingsStore().path


def _validate_onstart(value, worker_commit):
    if not isinstance(value, str):
        _fail()
    match = _ONSTART.fullmatch(value)
    if match is None or match.group(3) != worker_commit:
        _fail()
    try:
        bootstrap = base64.b64decode(match.group(1), validate=True)
        lock = base64.b64decode(match.group(2), validate=True)
        lock_payload = json.loads(lock.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        _fail()
    if (
        not isinstance(lock_payload, dict)
        or set(lock_payload) != _REMOTE_LOCK_FIELDS
        or lock != _compact_json(lock_payload) + b"\n"
    ):
        _fail()
    archive_sha256 = lock_payload.get("worker_archive_sha256")
    tag = "worker-v1-" + worker_commit
    asset_name = (
        "comfyui-cloud-run-worker-"
        + worker_commit
        + "-"
        + str(archive_sha256)
        + ".tar.gz"
    )
    try:
        metadata = ReleaseMetadata.from_payload(
            {
                **lock_payload,
                "repository": "wuraaang/ComfyUI-Cloud-Run",
                "tag": tag,
                "asset_name": asset_name,
            }
        )
        bootstrap_path = (
            Path(__file__).resolve().parents[1]
            / "remote_worker"
            / "bootstrap.py"
        )
        bootstrap_metadata = os.lstat(bootstrap_path)
        if (
            not stat.S_ISREG(bootstrap_metadata.st_mode)
            or stat.S_ISLNK(bootstrap_metadata.st_mode)
            or bootstrap_metadata.st_nlink != 1
            or bootstrap != bootstrap_path.read_bytes()
        ):
            _fail()
    except (OSError, ReleaseBuildError):
        _fail()
    if metadata.worker_commit != worker_commit:
        _fail()


def _validate_request(payload):
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        _fail()
    name = payload.get("name")
    name_match = _NAME.fullmatch(name) if isinstance(name, str) else None
    image = payload.get("image")
    tag = payload.get("tag")
    if (
        name_match is None
        or not isinstance(image, str)
        or _IMAGE.fullmatch(image) is None
        or not isinstance(tag, str)
        or _TAG.fullmatch(tag) is None
        or tag.casefold() in _MUTABLE_TAGS
        or payload.get("runtype") != "ssh"
        or payload.get("use_ssh") is not True
        or payload.get("ssh_direct") is not True
        or payload.get("jup_direct") is not False
        or payload.get("jupyter_dir") != "/workspace"
        or payload.get("use_jupyter_lab") is not False
        or payload.get("docker_login_repo") != ""
        or payload.get("docker_login_user") != ""
        or payload.get("docker_login_pass") != ""
        or payload.get("env") != "-p 8765:8765"
        or type(payload.get("recommended_disk_space")) is not int
        or payload.get("recommended_disk_space") != 80
        or payload.get("private") is not True
    ):
        _fail()
    _validate_onstart(payload.get("onstart"), name_match.group(1))
    return {field: payload[field] for field in sorted(_REQUEST_FIELDS)}


def _matches_request(row, template_payload):
    return all(
        row[field] == template_payload[field]
        for field in _REQUEST_FIELDS
        if field != "name"
    ) and row["name"] == template_payload["name"]


def _create_identity(payload, expected_name):
    if not isinstance(payload, dict) or set(payload) != {
        "success",
        "msg",
        "template",
    }:
        _fail()
    template = payload.get("template")
    if (
        payload.get("success") is not True
        or not isinstance(payload.get("msg"), str)
        or not isinstance(template, dict)
        or set(template) != {"id", "name", "hash_id"}
        or type(template.get("id")) is not int
        or template["id"] <= 0
        or template.get("name") != expected_name
        or not isinstance(template.get("hash_id"), str)
        or _HASH.fullmatch(template["hash_id"]) is None
    ):
        _fail()
    return template["id"], template["hash_id"]


def _write_private_record(output_directory, name, payload):
    directory = _private_path(output_directory, require_directory=True)
    destination = directory / name
    if destination.exists() or destination.is_symlink():
        _fail()
    descriptor = None
    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + name + ".",
            suffix=".part",
            dir=str(directory),
        )
        temporary_path = Path(temporary_name)
        content = _compact_json(payload) + b"\n"
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary_path, destination)
        temporary_path.unlink()
        temporary_path = None
        return destination
    except TemplatePublicationError:
        raise
    except OSError:
        _fail()
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def audit_base_template(
    output_directory,
    *,
    settings_path_resolver=_resolved_settings_path,
    transport=None,
):
    api_key = _credential(settings_path_resolver)
    client = transport if transport is not None else VastTemplateTransport()
    try:
        matches = client.lookup_base(api_key)
        if len(matches) != 1:
            _fail()
        row = matches[0]
        if (
            row["hash_id"] != BASE_TEMPLATE_HASH_ID
            or row["runtype"] != "jupyter_direc ssh_direc"
            or row["use_ssh"] is not True
            or row["ssh_direct"] is not True
            or row["jupyter_dir"] != "/workspace"
        ):
            _fail()
        record = {
            "schema_version": 1,
            "hash_id": row["hash_id"],
            "image": row["image"],
            "tag": row["tag"],
            "runtype": row["runtype"],
            "use_ssh": row["use_ssh"],
            "ssh_direct": row["ssh_direct"],
            "jupyter_dir": row["jupyter_dir"],
        }
        _write_private_record(
            output_directory,
            "base-template-audit.json",
            record,
        )
        return record
    except TemplatePublicationError:
        raise
    except Exception:
        _fail()


def publish_worker_template(
    request_file,
    output_directory,
    *,
    settings_path_resolver=_resolved_settings_path,
    transport=None,
):
    template_payload = _validate_request(_read_private_json(request_file))
    api_key = _credential(settings_path_resolver)
    client = transport if transport is not None else VastTemplateTransport()
    name = template_payload["name"]
    try:
        if client.lookup_name(api_key, name):
            _fail()
    except TemplatePublicationError:
        raise
    except Exception:
        _fail()

    template_id = None
    template_hash_id = None
    try:
        template_id, template_hash_id = _create_identity(
            client.create_worker_template(api_key, template_payload),
            name,
        )
    except Exception:
        try:
            reconciled = client.lookup_name(api_key, name)
            if len(reconciled) != 1 or not _matches_request(
                reconciled[0], template_payload
            ):
                _fail()
            template_id = reconciled[0]["id"]
            template_hash_id = reconciled[0]["hash_id"]
        except Exception:
            _fail()

    try:
        verified = client.lookup_hash(api_key, template_hash_id)
        if (
            len(verified) != 1
            or verified[0]["id"] != template_id
            or verified[0]["hash_id"] != template_hash_id
            or not _matches_request(verified[0], template_payload)
        ):
            _fail()
        record = {
            "id": template_id,
            "hash_id": template_hash_id,
            "verified": True,
        }
        _write_private_record(
            output_directory,
            "template-publication.json",
            record,
        )
        return record
    except TemplatePublicationError:
        raise
    except Exception:
        _fail()


def main(argv=None):
    parser = _PrivateArgumentParser(
        description="Audit or publish the fixed private Vast worker template."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    audit_parser = subparsers.add_parser("audit-base")
    audit_parser.add_argument("--output-directory", type=Path, required=True)
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--request-file", type=Path, required=True)
    publish_parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "audit-base":
            audit_base_template(arguments.output_directory)
            print("action=audit-base verified=true")
        else:
            result = publish_worker_template(
                arguments.request_file,
                arguments.output_directory,
            )
            print("action=publish")
            print("id=" + str(result["id"]))
            print("hash_id=" + result["hash_id"])
            print("verified=true")
    except TemplatePublicationError:
        parser.exit(1, _FAILURE + "\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Audit and publish the one reviewed private Vast worker template."""

from __future__ import annotations

import argparse
import base64
import hashlib
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
OFFICIAL_IMAGE = (
    "docker.io/vastai/comfy@sha256:"
    "9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c"
)
OFFICIAL_TAG = "v0.29.0-cuda-12.9-py312"
EXTRA_FILTERS = {
    "gpu_arch": {"eq": "nvidia"},
    "cpu_arch": {"eq": "amd64"},
    "cuda_max_good": {"gte": 12.9},
    "compute_cap": {"gte": 750},
    "num_gpus": {"eq": 1},
}
TEMPLATE_ENDPOINT = "https://console.vast.ai/api/v0/template/"
LOOKUP_COLUMNS = [
    "id",
    "name",
    "hash_id",
    "image",
    "tag",
    "env",
    "extra_filters",
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
BASE_LOOKUP_COLUMNS = [
    "hash_id",
    "use_ssh",
    "ssh_direct",
]
HTTP_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 256 * 1024
MAX_PRIVATE_JSON_BYTES = 64 * 1024

_FAILURE = "Private template publication failed."
_NAME = re.compile(r"cloud-run-worker-([0-9a-f]{40})")
_HASH = re.compile(r"[0-9a-f]{32}")
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
    "extra_filters",
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
_BASE_ROW_FIELDS = set(BASE_LOOKUP_COLUMNS)
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
    r"CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI\n"
    r"export CLOUD_RUN_COMFY_ROOT\n"
    r"exec /venv/main/bin/python "
    r"/opt/comfyui-cloud-run-bootstrap/bootstrap\.py "
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

    def _lookup(
        self,
        api_key,
        filters,
        *,
        select_columns=None,
        row_normalizer=None,
    ):
        columns = ["*"] if select_columns is None else select_columns
        normalizer = _normalize_row if row_normalizer is None else row_normalizer
        query = parse.urlencode(
            {
                "select_filters": _compact_json(filters).decode("ascii"),
                "select_cols": json.dumps(columns, separators=(",", ":")),
            }
        )
        http_request = request.Request(
            TEMPLATE_ENDPOINT + "?" + query,
            headers=self._headers(api_key),
            method="GET",
        )
        return _normalize_lookup(
            self._response_object(http_request),
            normalizer,
        )

    def lookup_base(self, api_key):
        return self._lookup(
            api_key,
            {"hash_id": {"eq": BASE_TEMPLATE_HASH_ID}},
            select_columns=BASE_LOOKUP_COLUMNS,
            row_normalizer=_normalize_base_row,
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


def _valid_extra_filters(value):
    if not isinstance(value, dict) or set(value) != set(EXTRA_FILTERS):
        return False
    for name, expected_constraint in EXTRA_FILTERS.items():
        constraint = value.get(name)
        if (
            not isinstance(constraint, dict)
            or set(constraint) != set(expected_constraint)
        ):
            return False
        operator, expected_value = next(iter(expected_constraint.items()))
        actual_value = constraint.get(operator)
        if (
            type(actual_value) is not type(expected_value)
            or actual_value != expected_value
        ):
            return False
    return True


def _normalize_base_row(row):
    if not isinstance(row, dict) or not _BASE_ROW_FIELDS.issubset(row):
        _fail()
    if (
        not isinstance(row.get("hash_id"), str)
        or _HASH.fullmatch(row["hash_id"]) is None
        or type(row.get("use_ssh")) is not bool
        or type(row.get("ssh_direct")) is not bool
    ):
        _fail()
    return {column: row[column] for column in BASE_LOOKUP_COLUMNS}


def _normalize_row(row):
    if not isinstance(row, dict) or not _ROW_FIELDS.issubset(row):
        _fail()
    template_id = row.get("id")
    hash_id = row.get("hash_id")
    image = row.get("image")
    tag = row.get("tag")
    onstart = row.get("onstart")
    onstart_size = None
    if isinstance(onstart, str):
        try:
            onstart_size = len(onstart.encode("utf-8"))
        except UnicodeError:
            _fail()
    if (
        type(template_id) is not int
        or template_id <= 0
        or not isinstance(row.get("name"), str)
        or not row["name"]
        or not isinstance(hash_id, str)
        or _HASH.fullmatch(hash_id) is None
        or image != OFFICIAL_IMAGE
        or tag != OFFICIAL_TAG
        or not isinstance(row.get("env"), str)
        or not _valid_extra_filters(row.get("extra_filters"))
        or not isinstance(onstart, str)
        or onstart_size > MAX_PRIVATE_JSON_BYTES
        or not isinstance(row.get("runtype"), str)
        or type(row.get("ssh_direct")) is not bool
        or type(row.get("use_ssh")) is not bool
        or type(row.get("jup_direct")) is not bool
        or (
            row.get("jupyter_dir") is not None
            and not isinstance(row.get("jupyter_dir"), str)
        )
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


def _normalize_lookup(payload, row_normalizer):
    if (
        isinstance(payload, dict)
        and set(payload) == {"success", "msg"}
        and payload.get("success") is False
        and type(payload.get("msg")) is str
        and payload["msg"] == "No templates found"
    ):
        return []
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
    return [row_normalizer(row) for row in templates]


def _private_path(
    path,
    *,
    require_directory=False,
    require_writable=False,
):
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
        or (require_writable and not metadata.st_mode & stat.S_IWUSR)
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
    return key, Path(settings_path)


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
        or image != OFFICIAL_IMAGE
        or tag != OFFICIAL_TAG
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
        or not _valid_extra_filters(payload.get("extra_filters"))
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


def _write_all(descriptor, content):
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("private record write made no progress")
        offset += written


def _fsync_directory(directory):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _PublicationIntent:
    def __init__(self, settings_path, template_name, template_payload):
        name_match = (
            _NAME.fullmatch(template_name)
            if isinstance(template_name, str)
            else None
        )
        if name_match is None:
            _fail()
        self.directory = _private_path(
            Path(settings_path).parent,
            require_directory=True,
            require_writable=True,
        )
        self.path = self.directory / (
            ".template-publication-" + name_match.group(1) + ".intent"
        )
        self.identity = None
        self.mutation_started = False
        self.removed = False
        descriptor = None
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            created = True
            os.fchmod(descriptor, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
            ):
                _fail()
            self.identity = (metadata.st_dev, metadata.st_ino)
            content = _compact_json(
                {
                    "schema_version": 1,
                    "name": template_name,
                    "request_sha256": hashlib.sha256(
                        _compact_json(template_payload)
                    ).hexdigest(),
                }
            ) + b"\n"
            _write_all(descriptor, content)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _fsync_directory(self.directory)
        except TemplatePublicationError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if created:
                try:
                    self.path.unlink()
                    _fsync_directory(self.directory)
                except OSError:
                    pass
            raise
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if created:
                try:
                    self.path.unlink()
                    _fsync_directory(self.directory)
                except OSError:
                    pass
            _fail()

    def mark_mutation_started(self):
        self.mutation_started = True

    def _remove(self):
        if self.removed:
            return
        try:
            metadata = os.lstat(self.path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino) != self.identity
            ):
                _fail()
            self.path.unlink()
            self.removed = True
            _fsync_directory(self.directory)
        except TemplatePublicationError:
            raise
        except OSError:
            _fail()

    def complete(self):
        self._remove()

    def close(self):
        if not self.mutation_started and not self.removed:
            self._remove()


class _PrivateRecordWriter:
    def __init__(self, output_directory, name):
        self.directory = _private_path(
            output_directory,
            require_directory=True,
            require_writable=True,
        )
        self.destination = self.directory / name
        self.descriptor = None
        self.temporary_path = None
        try:
            try:
                os.lstat(self.destination)
            except FileNotFoundError:
                pass
            else:
                _fail()
            self.descriptor, temporary_name = tempfile.mkstemp(
                prefix="." + name + ".",
                suffix=".part",
                dir=str(self.directory),
            )
            self.temporary_path = Path(temporary_name)
            os.fchmod(self.descriptor, 0o600)
        except TemplatePublicationError:
            self.close()
            raise
        except OSError:
            self.close()
            _fail()

    def publish(self, payload):
        try:
            content = _compact_json(payload) + b"\n"
            _write_all(self.descriptor, content)
            os.fsync(self.descriptor)
            os.close(self.descriptor)
            self.descriptor = None
            os.link(
                self.temporary_path,
                self.destination,
                follow_symlinks=False,
            )
            self.temporary_path.unlink()
            self.temporary_path = None
            _fsync_directory(self.directory)
            return self.destination
        except TemplatePublicationError:
            raise
        except OSError:
            _fail()

    def close(self):
        if self.descriptor is not None:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.descriptor = None
        if self.temporary_path is not None:
            try:
                self.temporary_path.unlink()
            except OSError:
                pass
            self.temporary_path = None


def _write_private_record(output_directory, name, payload):
    writer = _PrivateRecordWriter(output_directory, name)
    try:
        return writer.publish(payload)
    finally:
        writer.close()


def audit_base_template(
    output_directory,
    *,
    settings_path_resolver=_resolved_settings_path,
    transport=None,
):
    api_key, _settings_path = _credential(settings_path_resolver)
    client = transport if transport is not None else VastTemplateTransport()
    try:
        matches = client.lookup_base(api_key)
        if len(matches) != 1:
            _fail()
        row = matches[0]
        if (
            row["hash_id"] != BASE_TEMPLATE_HASH_ID
            or row["use_ssh"] is not True
            or row["ssh_direct"] is not True
        ):
            _fail()
        record = {
            "schema_version": 1,
            "hash_id": row["hash_id"],
            "use_ssh": row["use_ssh"],
            "ssh_direct": row["ssh_direct"],
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
    api_key, settings_path = _credential(settings_path_resolver)
    client = transport if transport is not None else VastTemplateTransport()
    name = template_payload["name"]
    writer = _PrivateRecordWriter(
        output_directory,
        "template-publication.json",
    )
    try:
        intent = _PublicationIntent(settings_path, name, template_payload)
        try:
            try:
                if client.lookup_name(api_key, name):
                    _fail()
            except TemplatePublicationError:
                raise
            except Exception:
                _fail()

            template_id = None
            template_hash_id = None
            intent.mark_mutation_started()
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
                writer.publish(record)
                intent.complete()
                return record
            except TemplatePublicationError:
                raise
            except Exception:
                _fail()
        finally:
            intent.close()
    finally:
        writer.close()


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

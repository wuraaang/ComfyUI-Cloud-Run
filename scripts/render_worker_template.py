#!/usr/bin/env python3
"""Render deterministic private Vast template inputs without publishing them."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_worker_release_bundle import (
    ReleaseBuildError,
    ReleaseMetadata,
    load_release_metadata,
)


BASE_TEMPLATE_HASH_ID = "027fba7753c024be019030fb42aed900"
BOOTSTRAP_DIRECTORY = "/opt/comfyui-cloud-run-bootstrap"
REMOTE_LOCK_NAME = "remote-release-lock.json"
ONSTART_NAME = "onstart.sh"
TEMPLATE_REQUEST_NAME = "template-request.json"
_BASE_FIELDS = {
    "schema_version",
    "hash_id",
    "image",
    "tag",
    "runtype",
    "use_ssh",
    "ssh_direct",
    "jupyter_dir",
}
_POLICY = {
    "schema_version": 1,
    "base_template_hash_id": BASE_TEMPLATE_HASH_ID,
    "comfyui_core_version": "0.29.0",
    "comfyui_frontend_version": "1.47.10",
    "python_version": "3.13.12",
    "protocol_version": "1",
    "worker_port": 8765,
}
_IMAGE_DIGEST = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]*@sha256:[0-9a-f]{64}"
)
_PINNED_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MUTABLE_TAGS = {"edge", "latest", "main", "master", "nightly", "stable"}


class TemplateRenderError(RuntimeError):
    """Template material is outside the exact reviewed offline contract."""


@dataclass(frozen=True)
class RenderedWorkerTemplate:
    remote_lock: dict
    onstart: str
    request: dict
    remote_lock_path: Path
    onstart_path: Path
    request_path: Path

    @property
    def paths(self):
        return (
            self.remote_lock_path,
            self.onstart_path,
            self.request_path,
        )


def _private_directory(path):
    directory = Path(path)
    try:
        metadata = os.lstat(directory)
    except OSError:
        raise TemplateRenderError(
            "Private template output directory is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise TemplateRenderError(
            "Private template output directory is unavailable."
        )
    return directory.resolve(strict=True)


def _validated_metadata(metadata):
    if not isinstance(metadata, ReleaseMetadata):
        raise TemplateRenderError("Worker release metadata is invalid.")
    try:
        validated = ReleaseMetadata.from_payload(metadata.to_record())
    except ReleaseBuildError:
        raise TemplateRenderError("Worker release metadata is invalid.") from None
    if validated != metadata:
        raise TemplateRenderError("Worker release metadata is invalid.")
    return validated


def _validated_base_template(base_template):
    if not isinstance(base_template, dict) or set(base_template) != _BASE_FIELDS:
        raise TemplateRenderError("Base template audit is invalid.")
    schema_version = base_template.get("schema_version")
    image = base_template.get("image")
    tag = base_template.get("tag")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or base_template.get("hash_id") != BASE_TEMPLATE_HASH_ID
        or not isinstance(image, str)
        or not _IMAGE_DIGEST.fullmatch(image)
        or not isinstance(tag, str)
        or not _PINNED_TAG.fullmatch(tag)
        or tag.casefold() in _MUTABLE_TAGS
        or base_template.get("runtype") != "jupyter_direc ssh_direc"
        or type(base_template.get("use_ssh")) is not bool
        or base_template.get("use_ssh") is not True
        or type(base_template.get("ssh_direct")) is not bool
        or base_template.get("ssh_direct") is not True
        or base_template.get("jupyter_dir") != "/workspace"
    ):
        raise TemplateRenderError("Base template audit is invalid.")
    return {
        "schema_version": 1,
        "hash_id": BASE_TEMPLATE_HASH_ID,
        "image": image,
        "tag": tag,
        "runtype": "jupyter_direc ssh_direc",
        "use_ssh": True,
        "ssh_direct": True,
        "jupyter_dir": "/workspace",
    }


def _repository_inputs(repository_root, metadata):
    try:
        root = Path(repository_root).resolve(strict=True)
        bootstrap_path = root / "remote_worker" / "bootstrap.py"
        policy_path = root / "remote_worker" / "template-policy.json"
        bootstrap_metadata = os.lstat(bootstrap_path)
        policy_metadata = os.lstat(policy_path)
        bootstrap = bootstrap_path.read_bytes()
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        raise TemplateRenderError(
            "Reviewed template policy is unavailable."
        ) from None
    for file_metadata in (bootstrap_metadata, policy_metadata):
        if (
            not stat.S_ISREG(file_metadata.st_mode)
            or stat.S_ISLNK(file_metadata.st_mode)
            or file_metadata.st_nlink != 1
        ):
            raise TemplateRenderError(
                "Reviewed template policy is unavailable."
            )
    if policy != _POLICY or (
        policy["protocol_version"] != metadata.protocol_version
        or policy["comfyui_core_version"] != metadata.comfyui_core_version
        or policy["comfyui_frontend_version"]
        != metadata.comfyui_frontend_version
        or policy["python_version"] != metadata.python_version
    ):
        raise TemplateRenderError("Reviewed template policy is invalid.")
    return bootstrap


def _json_bytes(payload):
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _write_private_atomic(path, content):
    destination = Path(path)
    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + destination.name + ".",
            suffix=".part",
            dir=str(destination.parent),
        )
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    except OSError:
        raise TemplateRenderError(
            "Private template output could not be written."
        ) from None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _onstart(bootstrap, remote_lock_bytes, worker_commit):
    bootstrap_base64 = base64.b64encode(bootstrap).decode("ascii")
    lock_base64 = base64.b64encode(remote_lock_bytes).decode("ascii")
    return "\n".join(
        (
            "#!/bin/sh",
            "set -eu",
            "umask 077",
            "mkdir -m 0700 " + BOOTSTRAP_DIRECTORY,
            "printf '%s' '"
            + bootstrap_base64
            + "' | base64 -d > "
            + BOOTSTRAP_DIRECTORY
            + "/bootstrap.py",
            "chmod 0600 " + BOOTSTRAP_DIRECTORY + "/bootstrap.py",
            "printf '%s' '"
            + lock_base64
            + "' | base64 -d > "
            + BOOTSTRAP_DIRECTORY
            + "/release-lock.json",
            "chmod 0600 " + BOOTSTRAP_DIRECTORY + "/release-lock.json",
            "CLOUD_RUN_WORKER_VERSION=" + worker_commit,
            "export CLOUD_RUN_WORKER_VERSION",
            "exec python3 "
            + BOOTSTRAP_DIRECTORY
            + "/bootstrap.py "
            + BOOTSTRAP_DIRECTORY
            + "/release-lock.json",
            "",
        )
    )


def render_worker_template(
    repository_root,
    output_directory,
    metadata,
    base_template,
):
    output = _private_directory(output_directory)
    validated_metadata = _validated_metadata(metadata)
    validated_base = _validated_base_template(base_template)
    bootstrap = _repository_inputs(repository_root, validated_metadata)

    remote_lock = {
        "schema_version": 1,
        "archive_url": validated_metadata.archive_url,
        "worker_commit": validated_metadata.worker_commit,
        "worker_archive_sha256": validated_metadata.worker_archive_sha256,
        "worker_archive_size_bytes": (
            validated_metadata.worker_archive_size_bytes
        ),
        "protocol_version": validated_metadata.protocol_version,
        "comfyui_core_version": validated_metadata.comfyui_core_version,
        "comfyui_frontend_version": (
            validated_metadata.comfyui_frontend_version
        ),
        "python_version": validated_metadata.python_version,
        "destination": "/opt/comfyui-cloud-run",
    }
    remote_lock_bytes = _json_bytes(remote_lock)
    onstart = _onstart(
        bootstrap,
        remote_lock_bytes,
        validated_metadata.worker_commit,
    )
    request = {
        "name": "cloud-run-worker-" + validated_metadata.worker_commit,
        "image": validated_base["image"],
        "tag": validated_base["tag"],
        "runtype": validated_base["runtype"],
        "use_ssh": validated_base["use_ssh"],
        "ssh_direct": validated_base["ssh_direct"],
        "jupyter_dir": validated_base["jupyter_dir"],
        "onstart": onstart,
        "ports": ["8765/tcp"],
        "env": "",
        "recommended_disk_space": 80,
    }
    outputs = (
        (output / REMOTE_LOCK_NAME, remote_lock_bytes),
        (output / ONSTART_NAME, onstart.encode("utf-8")),
        (output / TEMPLATE_REQUEST_NAME, _json_bytes(request)),
    )
    for path, _content in outputs:
        if path.exists() or path.is_symlink():
            raise TemplateRenderError("Private template output already exists.")
    written = []
    try:
        for path, content in outputs:
            _write_private_atomic(path, content)
            written.append(path)
    except TemplateRenderError:
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return RenderedWorkerTemplate(
        remote_lock=remote_lock,
        onstart=onstart,
        request=request,
        remote_lock_path=outputs[0][0],
        onstart_path=outputs[1][0],
        request_path=outputs[2][0],
    )


def _load_private_json(path):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(Path(path), flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not 0 < metadata.st_size <= 64 * 1024
        ):
            raise TemplateRenderError("Private template input is unavailable.")
        content = os.read(descriptor, 64 * 1024 + 1)
        if len(content) != metadata.st_size:
            raise TemplateRenderError("Private template input is unavailable.")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TemplateRenderError("Private template input is unavailable.")
        return payload
    except TemplateRenderError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise TemplateRenderError("Private template input is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render private immutable Vast template inputs."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--release-metadata", type=Path, required=True)
    parser.add_argument("--base-template-audit", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        metadata = load_release_metadata(arguments.release_metadata)
        rendered = render_worker_template(
            arguments.repository_root,
            arguments.output_directory,
            metadata,
            _load_private_json(arguments.base_template_audit),
        )
    except (ReleaseBuildError, TemplateRenderError):
        parser.exit(1, "Private template inputs could not be rendered.\n")
    print("remote_lock=" + rendered.remote_lock_path.name)
    print("onstart=" + rendered.onstart_path.name)
    print("template_request=" + rendered.request_path.name)


if __name__ == "__main__":
    main()

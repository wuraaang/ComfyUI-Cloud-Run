#!/usr/bin/env python3
"""Publish one owner-private worker release lock without overwriting."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_run.constants import REMOTE_WORKER_PORT
from cloud_run.worker_release import (
    WorkerRelease,
    WorkerReleaseError,
    WorkerReleaseUnavailable,
    load_worker_release,
)
from scripts.build_worker_release_bundle import (
    ReleaseBuildError,
    ReleaseMetadata,
    load_release_metadata,
)


_HEX_32 = re.compile(r"[0-9a-f]{32}")


class LockWriteError(RuntimeError):
    """The local reviewed release lock could not be published safely."""


def _validated_metadata(metadata):
    if not isinstance(metadata, ReleaseMetadata):
        raise LockWriteError("Worker release metadata is invalid.")
    try:
        validated = ReleaseMetadata.from_payload(metadata.to_record())
    except ReleaseBuildError:
        raise LockWriteError("Worker release metadata is invalid.") from None
    if validated != metadata:
        raise LockWriteError("Worker release metadata is invalid.")
    return validated


def _target_path(path):
    candidate = Path(path)
    if candidate.name in {"", ".", ".."}:
        raise LockWriteError("Worker release lock target is unavailable.")
    parent = candidate.parent
    try:
        parent_metadata = os.lstat(parent)
    except OSError:
        raise LockWriteError(
            "Worker release lock parent is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_ISLNK(parent_metadata.st_mode)
        or parent_metadata.st_uid != os.getuid()
        or parent_metadata.st_mode & 0o077
    ):
        raise LockWriteError("Worker release lock parent is unavailable.")
    parent = parent.resolve(strict=True)
    destination = parent / candidate.name
    try:
        os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError:
        raise LockWriteError(
            "Worker release lock target is unavailable."
        ) from None
    else:
        raise LockWriteError("Worker release lock target already exists.")
    return parent, destination


def _lock_record(template_hash_id, metadata):
    if (
        not isinstance(template_hash_id, str)
        or not _HEX_32.fullmatch(template_hash_id)
    ):
        raise LockWriteError("Project template hash is invalid.")
    release = WorkerRelease(
        schema_version=1,
        template_hash_id=template_hash_id,
        worker_commit=metadata.worker_commit,
        worker_archive_sha256=metadata.worker_archive_sha256,
        protocol_version=metadata.protocol_version,
        comfyui_core_version=metadata.comfyui_core_version,
        comfyui_frontend_version=metadata.comfyui_frontend_version,
        python_version=metadata.python_version,
        worker_port=REMOTE_WORKER_PORT,
    )
    return release.to_record()


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


def _write_all(descriptor, content):
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("release lock write made no progress")
        offset += written


def write_worker_release_lock(path, template_hash_id, metadata):
    """Create and verify a new lock by atomic no-overwrite hard link."""
    validated_metadata = _validated_metadata(metadata)
    parent, destination = _target_path(path)
    try:
        record = _lock_record(template_hash_id, validated_metadata)
    except WorkerReleaseError:
        raise LockWriteError("Worker release lock is invalid.") from None
    content = _json_bytes(record)

    temporary = parent / (
        "." + destination.name + "." + secrets.token_hex(16) + ".tmp"
    )
    descriptor = None
    parent_descriptor = None
    temporary_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None

        os.link(temporary, destination, follow_symlinks=False)
        temporary.unlink()
        temporary_created = False

        parent_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            parent_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            parent_flags |= os.O_NOFOLLOW
        parent_descriptor = os.open(parent, parent_flags)
        os.fsync(parent_descriptor)
        os.close(parent_descriptor)
        parent_descriptor = None

        loaded = load_worker_release(destination)
        if loaded.to_record() != record:
            raise LockWriteError("Worker release lock verification failed.")
        return loaded
    except LockWriteError:
        raise
    except (OSError, WorkerReleaseUnavailable):
        raise LockWriteError("Worker release lock could not be written.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if parent_descriptor is not None:
            try:
                os.close(parent_descriptor)
            except OSError:
                pass
        if temporary_created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write a private reviewed ComfyUI Cloud Run release lock."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template-hash-id", required=True)
    parser.add_argument("--release-metadata", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        loaded = write_worker_release_lock(
            arguments.output,
            arguments.template_hash_id,
            load_release_metadata(arguments.release_metadata),
        )
    except (ReleaseBuildError, LockWriteError):
        parser.exit(1, "Worker release lock could not be written.\n")
    print("template_hash_id=" + loaded.template_hash_id)
    print("worker_commit=" + loaded.worker_commit)
    print("sha256=" + loaded.worker_archive_sha256)
    print("worker_port=" + str(loaded.worker_port))


if __name__ == "__main__":
    main()

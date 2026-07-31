#!/usr/bin/env python3
"""Build one deterministic, sanitized Remote Worker release bundle."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_run.constants import PINNED_PYTHON_VERSION
from cloud_run.manifest import (
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
)
from remote_worker.bootstrap import DEFAULT_DESTINATION, MAX_ARCHIVE_BYTES
from scripts.build_worker_artifact import build_worker_artifact


SCHEMA_VERSION = 1
REPOSITORY = "wuraaang/ComfyUI-Cloud-Run"
DESTINATION = DEFAULT_DESTINATION.as_posix()
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


class ReleaseBuildError(RuntimeError):
    """Release inputs are not the exact reviewed immutable contract."""


@dataclass(frozen=True)
class ReleaseMetadata:
    schema_version: int
    repository: str
    worker_commit: str
    tag: str
    asset_name: str
    archive_url: str
    worker_archive_size_bytes: int
    worker_archive_sha256: str
    protocol_version: str
    comfyui_core_version: str
    comfyui_frontend_version: str
    python_version: str
    destination: str

    def __post_init__(self):
        if (
            type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
            or self.repository != REPOSITORY
            or not isinstance(self.worker_commit, str)
            or not _HEX_40.fullmatch(self.worker_commit)
            or isinstance(self.worker_archive_size_bytes, bool)
            or not isinstance(self.worker_archive_size_bytes, int)
            or not 0 < self.worker_archive_size_bytes <= MAX_ARCHIVE_BYTES
            or not isinstance(self.worker_archive_sha256, str)
            or not _HEX_64.fullmatch(self.worker_archive_sha256)
            or self.protocol_version != PROTOCOL_VERSION
            or self.comfyui_core_version != PINNED_COMFYUI_CORE_VERSION
            or self.comfyui_frontend_version
            != PINNED_COMFYUI_FRONTEND_VERSION
            or self.python_version != PINNED_PYTHON_VERSION
            or self.destination != DESTINATION
        ):
            raise ReleaseBuildError("Worker release metadata is invalid.")
        expected_tag = "worker-v1-" + self.worker_commit
        expected_asset = (
            "comfyui-cloud-run-worker-"
            + self.worker_commit
            + "-"
            + self.worker_archive_sha256
            + ".tar.gz"
        )
        expected_url = (
            "https://github.com/"
            + REPOSITORY
            + "/releases/download/"
            + expected_tag
            + "/"
            + expected_asset
        )
        if (
            self.tag != expected_tag
            or self.asset_name != expected_asset
            or self.archive_url != expected_url
        ):
            raise ReleaseBuildError("Worker release metadata is invalid.")

    @classmethod
    def from_payload(cls, payload):
        expected = {field.name for field in fields(cls)}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ReleaseBuildError("Worker release metadata is invalid.")
        try:
            return cls(**payload)
        except (TypeError, ReleaseBuildError):
            raise ReleaseBuildError(
                "Worker release metadata is invalid."
            ) from None

    def to_record(self):
        return asdict(self)


@dataclass(frozen=True)
class ReleaseBundle:
    archive: Path
    metadata_path: Path
    metadata: ReleaseMetadata


def _private_directory(path):
    directory = Path(path)
    try:
        metadata = os.lstat(directory)
    except OSError:
        raise ReleaseBuildError(
            "Private release output directory is unavailable."
        ) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise ReleaseBuildError(
            "Private release output directory is unavailable."
        )
    return directory.resolve(strict=True)


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
        raise ReleaseBuildError("Release metadata could not be written.") from None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def load_release_metadata(path):
    try:
        content = Path(path).read_bytes()
        payload = json.loads(content.decode("utf-8"))
        return ReleaseMetadata.from_payload(payload)
    except ReleaseBuildError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReleaseBuildError("Worker release metadata is invalid.") from None


def build_worker_release_bundle(
    repository_root,
    output_directory,
    worker_commit,
):
    if (
        not isinstance(worker_commit, str)
        or not _HEX_40.fullmatch(worker_commit)
    ):
        raise ReleaseBuildError("Worker commit is invalid.")
    output = _private_directory(output_directory)
    metadata_path = output / "release-metadata.json"
    if metadata_path.exists() or metadata_path.is_symlink():
        raise ReleaseBuildError("Release output already exists.")

    temporary_archive = None
    final_archive = None
    archive_published = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".worker-release-",
            suffix=".tar.gz.part",
            dir=str(output),
        )
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        temporary_archive = Path(temporary_name)
        temporary_archive.unlink()

        artifact = build_worker_artifact(
            repository_root,
            temporary_archive,
        )
        if (
            artifact.path != temporary_archive
            or isinstance(artifact.size_bytes, bool)
            or not isinstance(artifact.size_bytes, int)
            or not 0 < artifact.size_bytes <= MAX_ARCHIVE_BYTES
            or not isinstance(artifact.sha256, str)
            or not _HEX_64.fullmatch(artifact.sha256)
        ):
            raise ReleaseBuildError("Worker artifact identity is invalid.")
        archive_metadata = os.lstat(temporary_archive)
        archive_content = temporary_archive.read_bytes()
        if (
            not stat.S_ISREG(archive_metadata.st_mode)
            or stat.S_ISLNK(archive_metadata.st_mode)
            or archive_metadata.st_uid != os.getuid()
            or archive_metadata.st_mode & 0o077
            or archive_metadata.st_nlink != 1
            or archive_metadata.st_size != artifact.size_bytes
            or len(archive_content) != artifact.size_bytes
            or hashlib.sha256(archive_content).hexdigest() != artifact.sha256
        ):
            raise ReleaseBuildError("Worker artifact identity is invalid.")

        metadata = ReleaseMetadata(
            schema_version=SCHEMA_VERSION,
            repository=REPOSITORY,
            worker_commit=worker_commit,
            tag="worker-v1-" + worker_commit,
            asset_name=(
                "comfyui-cloud-run-worker-"
                + worker_commit
                + "-"
                + artifact.sha256
                + ".tar.gz"
            ),
            archive_url=(
                "https://github.com/"
                + REPOSITORY
                + "/releases/download/worker-v1-"
                + worker_commit
                + "/comfyui-cloud-run-worker-"
                + worker_commit
                + "-"
                + artifact.sha256
                + ".tar.gz"
            ),
            worker_archive_size_bytes=artifact.size_bytes,
            worker_archive_sha256=artifact.sha256,
            protocol_version=PROTOCOL_VERSION,
            comfyui_core_version=PINNED_COMFYUI_CORE_VERSION,
            comfyui_frontend_version=PINNED_COMFYUI_FRONTEND_VERSION,
            python_version=PINNED_PYTHON_VERSION,
            destination=DESTINATION,
        )
        final_archive = output / metadata.asset_name
        if final_archive.exists() or final_archive.is_symlink():
            raise ReleaseBuildError("Release output already exists.")
        os.replace(temporary_archive, final_archive)
        temporary_archive = None
        archive_published = True
        _write_private_atomic(metadata_path, _json_bytes(metadata.to_record()))
        return ReleaseBundle(
            archive=final_archive,
            metadata_path=metadata_path,
            metadata=metadata,
        )
    except ReleaseBuildError:
        if archive_published:
            try:
                final_archive.unlink()
            except OSError:
                pass
        raise
    except OSError:
        if archive_published:
            try:
                final_archive.unlink()
            except OSError:
                pass
        raise ReleaseBuildError("Worker release bundle could not be built.") from None
    finally:
        if temporary_archive is not None:
            try:
                temporary_archive.unlink()
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an immutable ComfyUI Cloud Run worker release."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--worker-commit", required=True)
    arguments = parser.parse_args(argv)
    try:
        bundle = build_worker_release_bundle(
            arguments.repository_root,
            arguments.output_directory,
            arguments.worker_commit,
        )
    except ReleaseBuildError:
        parser.exit(1, "Worker release bundle could not be built.\n")
    print("tag=" + bundle.metadata.tag)
    print("asset=" + bundle.metadata.asset_name)
    print("size=" + str(bundle.metadata.worker_archive_size_bytes))
    print("sha256=" + bundle.metadata.worker_archive_sha256)


if __name__ == "__main__":
    main()

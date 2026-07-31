#!/usr/bin/env python3
"""Build the reviewed Remote Worker source archive deterministically."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile


MAX_WORKER_SOURCE_BYTES = 64 * 1024 * 1024
ALLOWED_REMOTE_FILES = frozenset(
    {
        "remote_worker/Caddyfile",
        "remote_worker/__init__.py",
        "remote_worker/bootstrap.py",
        "remote_worker/comfy.py",
        "remote_worker/deadline.py",
        "remote_worker/install.py",
        "remote_worker/jobs.py",
        "remote_worker/main.py",
        "remote_worker/provision.py",
        "remote_worker/server.py",
        "remote_worker/state.py",
        "remote_worker/template-policy.json",
        "remote_worker/transfers.py",
    }
)
SHARED_FILES = frozenset(
    {
        "cloud_run/manifest.py",
        "cloud_run/worker_protocol.py",
    }
)
EXPECTED_FILES = ALLOWED_REMOTE_FILES | SHARED_FILES
_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    re.compile(
        rb"(?:VAST_API_KEY|R2_SECRET_ACCESS_KEY|HF_TOKEN|CIVITAI_TOKEN)"
        rb"\s*=\s*['\"][^'\"]{12,}['\"]"
    ),
    re.compile(rb"Bearer [A-Za-z0-9_=-]{32,}"),
)


class ArtifactBuildError(RuntimeError):
    """The public worker source tree is not safe to package."""


@dataclass(frozen=True)
class WorkerArtifact:
    path: Path
    size_bytes: int
    sha256: str
    members: tuple[str, ...]


def _source_files(repository_root):
    root = Path(repository_root).resolve(strict=True)
    remote_root = root / "remote_worker"
    shared_root = root / "cloud_run"
    if not remote_root.is_dir() or not shared_root.is_dir():
        raise ArtifactBuildError("Worker source tree is unavailable.")

    observed = set()
    try:
        for path in remote_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISDIR(metadata.st_mode):
                if path.name in {
                    ".git",
                    ".hg",
                    ".svn",
                    "__pycache__",
                    "tests",
                }:
                    raise ArtifactBuildError(
                        "Worker source tree contains forbidden metadata."
                    )
                continue
            observed.add(relative)
        for relative in SHARED_FILES:
            path = root / relative
            os.lstat(path)
            observed.add(relative)
    except (OSError, RuntimeError, ValueError):
        raise ArtifactBuildError("Worker source tree is unavailable.") from None

    if observed != EXPECTED_FILES:
        raise ArtifactBuildError(
            "Worker source allowlist requires explicit review."
        )

    records = []
    total = 0
    for relative in sorted(observed):
        path = root / relative
        try:
            before = os.lstat(path)
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or before.st_nlink != 1
                or before.st_size < 0
            ):
                raise ArtifactBuildError(
                    "Worker source contains an unsupported file."
                )
            content = path.read_bytes()
            after = os.lstat(path)
        except ArtifactBuildError:
            raise
        except OSError:
            raise ArtifactBuildError(
                "Worker source tree is unavailable."
            ) from None
        if (
            len(content) != before.st_size
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        ):
            raise ArtifactBuildError(
                "Worker source changed while it was reviewed."
            )
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            raise ArtifactBuildError(
                "Worker source contains secret-like material."
            )
        total += len(content)
        if total > MAX_WORKER_SOURCE_BYTES:
            raise ArtifactBuildError("Worker source tree is too large.")
        mode = (
            0o755
            if before.st_mode
            & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            else 0o644
        )
        records.append((relative, content, mode))
    return root, tuple(records)


def _archive_bytes(records):
    raw = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=raw,
            mode="w",
            format=tarfile.GNU_FORMAT,
        ) as archive:
            for relative, content, mode in records:
                info = tarfile.TarInfo(relative)
                info.size = len(content)
                info.mode = mode
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                archive.addfile(info, io.BytesIO(content))
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
    except (OSError, tarfile.TarError):
        raise ArtifactBuildError(
            "Worker artifact could not be built."
        ) from None


def build_worker_artifact(repository_root, output_path):
    _root, records = _source_files(repository_root)
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    try:
        parent = destination.parent.resolve(strict=True)
        metadata = os.lstat(parent)
    except (OSError, RuntimeError):
        raise ArtifactBuildError(
            "Worker artifact destination is unavailable."
        ) from None
    if not stat.S_ISDIR(metadata.st_mode) or destination.name in {"", ".", ".."}:
        raise ArtifactBuildError(
            "Worker artifact destination is unavailable."
        )
    destination = parent / destination.name
    content = _archive_bytes(records)
    digest = hashlib.sha256(content).hexdigest()

    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".worker-artifact-",
            suffix=".part",
            dir=str(parent),
        )
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    except OSError:
        raise ArtifactBuildError(
            "Worker artifact could not be written."
        ) from None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
    return WorkerArtifact(
        path=destination,
        size_bytes=len(content),
        sha256=digest,
        members=tuple(relative for relative, _content, _mode in records),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a deterministic ComfyUI Cloud Run worker artifact."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args(argv)
    result = build_worker_artifact(
        arguments.repository_root,
        arguments.output,
    )
    print(f"{result.sha256}  {result.path.name}")


if __name__ == "__main__":
    main()

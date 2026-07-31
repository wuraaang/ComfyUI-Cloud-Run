"""Fail-closed lock for the reviewed project-owned Remote Worker release."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import stat

from .constants import (
    PINNED_PYTHON_VERSION,
    REMOTE_WORKER_PORT,
    WORKER_RELEASE_SCHEMA_VERSION,
)
from .manifest import (
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
)


_HEX_32 = re.compile(r"[0-9a-f]{32}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_RELEASE_LOCK_BYTES = 64 * 1024
_FIELDS = {
    "schema_version",
    "template_hash_id",
    "worker_commit",
    "worker_archive_sha256",
    "protocol_version",
    "comfyui_core_version",
    "comfyui_frontend_version",
    "python_version",
    "worker_port",
}


class WorkerReleaseError(ValueError):
    """The reviewed release payload is not an exact immutable contract."""


class WorkerReleaseUnavailable(RuntimeError):
    """No usable private reviewed release lock is available."""


@dataclass(frozen=True)
class WorkerRelease:
    schema_version: int
    template_hash_id: str
    worker_commit: str
    worker_archive_sha256: str
    protocol_version: str
    comfyui_core_version: str
    comfyui_frontend_version: str
    python_version: str
    worker_port: int

    def __post_init__(self):
        if (
            isinstance(self.schema_version, bool)
            or self.schema_version != WORKER_RELEASE_SCHEMA_VERSION
            or not isinstance(self.template_hash_id, str)
            or not _HEX_32.fullmatch(self.template_hash_id)
            or not isinstance(self.worker_commit, str)
            or not _HEX_40.fullmatch(self.worker_commit)
            or not isinstance(self.worker_archive_sha256, str)
            or not _HEX_64.fullmatch(self.worker_archive_sha256)
            or self.protocol_version != PROTOCOL_VERSION
            or self.comfyui_core_version != PINNED_COMFYUI_CORE_VERSION
            or self.comfyui_frontend_version
            != PINNED_COMFYUI_FRONTEND_VERSION
            or self.python_version != PINNED_PYTHON_VERSION
            or isinstance(self.worker_port, bool)
            or self.worker_port != REMOTE_WORKER_PORT
        ):
            raise WorkerReleaseError(
                "Reviewed worker release lock is invalid."
            )

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != _FIELDS:
            raise WorkerReleaseError(
                "Reviewed worker release lock is invalid."
            )
        try:
            return cls(**payload)
        except (TypeError, WorkerReleaseError):
            raise WorkerReleaseError(
                "Reviewed worker release lock is invalid."
            ) from None

    def to_record(self):
        return asdict(self)


def load_worker_release(path):
    """Load one owner-private regular lock file without following symlinks."""
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
            or not 0 < metadata.st_size <= _MAX_RELEASE_LOCK_BYTES
        ):
            raise WorkerReleaseUnavailable(
                "Reviewed worker release lock is unavailable."
            )
        content = os.read(descriptor, _MAX_RELEASE_LOCK_BYTES + 1)
        if len(content) != metadata.st_size:
            raise WorkerReleaseUnavailable(
                "Reviewed worker release lock is unavailable."
            )
        payload = json.loads(content.decode("utf-8"))
        return WorkerRelease.from_payload(payload)
    except WorkerReleaseUnavailable:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, WorkerReleaseError):
        raise WorkerReleaseUnavailable(
            "Reviewed worker release lock is unavailable."
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

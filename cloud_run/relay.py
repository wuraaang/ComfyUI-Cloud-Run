"""Durable local relay for sanitized worker events and verified media."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import errno
import hashlib
import hmac
import json
import math
import mimetypes
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tempfile
import time
import uuid

from .job_repository import JobRepository
from .models import TransferState
from .worker_client import (
    ArtifactDownload,
    PreviewDownload,
    WorkerClientError,
)


MAX_OUTPUT_BYTES = 4 * 1024 * 1024 * 1024 * 1024
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MIME_TYPE = re.compile(r"[a-z0-9.+-]+/[a-z0-9.+-]+")
_OUTPUT_FIELDS = {
    "artifact_id",
    "node_id",
    "filename",
    "subfolder",
    "mime_type",
    "size_bytes",
    "sha256",
}
_EVENT_TYPES = {
    "execution_start",
    "status",
    "progress",
    "progress_text",
    "progress_state",
    "executing",
    "executed",
    "execution_cached",
    "execution_success",
    "execution_error",
    "execution_interrupted",
    "b_preview",
    "b_preview_with_metadata",
}
_ERROR_MESSAGES = {
    "validation_failed": "Remote ComfyUI rejected the compiled prompt.",
    "out_of_memory": "Remote execution ran out of GPU memory.",
    "execution_failed": "Remote execution failed.",
    "execution_interrupted": "Remote execution was interrupted.",
    "worker_restarted": (
        "Remote execution was interrupted by a worker restart."
    ),
}
_SUSPICIOUS_TEXT = re.compile(
    r"(?i)(authorization|bearer|api[_ -]?key|password|secret|token)"
)


class RelayError(RuntimeError):
    """A sanitized local relay failure."""


class RelayValidationError(RelayError):
    pass


class ArtifactVerificationError(RelayError):
    pass


@dataclass(frozen=True)
class RelayArtifactResult:
    job_id: str
    artifact_id: str
    node_id: str
    state: TransferState
    local_path: Path
    size_bytes: int
    sha256: str
    mime_type: str

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "artifact_id": self.artifact_id,
            "node_id": self.node_id,
            "state": self.state.value,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class RelayMedia:
    content: bytes
    mime_type: str
    sha256: str


@dataclass(frozen=True)
class PublishedArtifact:
    path: Path
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RelaySyncResult:
    job_id: str
    state: str
    last_sequence: int
    events: tuple
    outputs: tuple
    error: object

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "state": self.state,
            "last_sequence": self.last_sequence,
            "outputs": [
                output.public_payload() for output in self.outputs
            ],
            "error": dict(self.error) if self.error is not None else None,
        }


def _relay_error(message="Local relay rejected remote data."):
    return RelayValidationError(message)


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value)


def _finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _safe_identifier(value):
    if isinstance(value, (str, int)):
        normalized = str(value)
        if _IDENTIFIER.fullmatch(normalized):
            return normalized
    return None


def _safe_text(value, maximum=512):
    if not isinstance(value, str):
        return None
    normalized = "".join(
        character if ord(character) >= 32 else " "
        for character in value
    ).strip()
    if not normalized:
        return None
    if _SUSPICIOUS_TEXT.search(normalized):
        return None
    return normalized[:maximum]


def _private_directory(path):
    path = Path(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(path)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise _relay_error("Local relay storage is unavailable.")
        os.chmod(path, 0o700)
        return path.resolve(strict=True)
    except RelayError:
        raise
    except (OSError, RuntimeError):
        raise _relay_error("Local relay storage is unavailable.") from None


def _output_root(path):
    try:
        path = Path(path)
        metadata = os.lstat(path)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _relay_error("Local output directory is unavailable.") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise _relay_error("Local output directory is unavailable.")
    return resolved


def _write_all(descriptor, content):
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Short local artifact write.")
        view = view[written:]


def _fsync_directory(path):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise ArtifactVerificationError(
            "Local artifact durability failed."
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_path_identity(path, *, maximum):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 0
            or metadata.st_size > maximum
        ):
            raise ArtifactVerificationError(
                "Local artifact verification failed."
            )
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ArtifactVerificationError(
                    "Local artifact verification failed."
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            size != metadata.st_size
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ino != metadata.st_ino
            or after.st_dev != metadata.st_dev
        ):
            raise ArtifactVerificationError(
                "Local artifact verification failed."
            )
        return (
            size,
            digest.hexdigest(),
            metadata.st_dev,
            metadata.st_ino,
        )
    except ArtifactVerificationError:
        raise
    except OSError:
        raise ArtifactVerificationError(
            "Local artifact verification failed."
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _hash_path(path, *, maximum):
    size, digest, _device, _inode = _hash_path_identity(
        path,
        maximum=maximum,
    )
    return size, digest


def _validated_output(descriptor):
    if not isinstance(descriptor, dict) or set(descriptor) != _OUTPUT_FIELDS:
        raise _relay_error()
    artifact_id = descriptor["artifact_id"]
    node_id = descriptor["node_id"]
    filename = descriptor["filename"]
    subfolder = descriptor["subfolder"]
    mime_type = descriptor["mime_type"]
    size_bytes = descriptor["size_bytes"]
    sha256 = descriptor["sha256"]
    if (
        not _identifier(artifact_id)
        or not _identifier(node_id)
        or not isinstance(filename, str)
        or not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 for character in filename)
        or len(filename.encode("utf-8")) > 1024
        or not isinstance(subfolder, str)
        or "\\" in subfolder
        or any(ord(character) < 32 for character in subfolder)
        or len(subfolder.encode("utf-8")) > 4096
        or not isinstance(mime_type, str)
        or not _MIME_TYPE.fullmatch(mime_type)
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 < size_bytes <= MAX_OUTPUT_BYTES
        or not isinstance(sha256, str)
        or not _HEX_64.fullmatch(sha256)
    ):
        raise _relay_error()
    relative = PurePosixPath(subfolder)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        if subfolder:
            raise _relay_error()
        parts = ()
    else:
        parts = relative.parts
    return {
        **descriptor,
        "subfolder_parts": parts,
    }


def _sanitize_error(data):
    if not isinstance(data, dict):
        return {
            "code": "execution_failed",
            "message": _ERROR_MESSAGES["execution_failed"],
        }
    code = data.get("code")
    if code not in _ERROR_MESSAGES:
        code = "execution_failed"
    result = {
        "code": code,
        "message": _ERROR_MESSAGES[code],
    }
    for source, destination in (
        ("node_id", "node_id"),
        ("class_type", "class_type"),
    ):
        value = _safe_identifier(data.get(source))
        if value is not None:
            result[destination] = value
    title = _safe_text(data.get("title"), maximum=200)
    if title is not None:
        result["title"] = title
    return result


def _sanitize_event(event, expected_sequence):
    if (
        not isinstance(event, dict)
        or set(event) != {"sequence", "type", "data", "created_at"}
        or event.get("sequence") != expected_sequence
        or event.get("type") not in _EVENT_TYPES
        or not isinstance(event.get("data"), dict)
        or not _finite_number(event.get("created_at"))
        or event["created_at"] < 0
    ):
        raise _relay_error()
    event_type = event["type"]
    data = event["data"]
    if event_type in {"b_preview", "b_preview_with_metadata"}:
        preview_id = data.get("preview_id")
        mime_type = data.get("mime_type")
        size_bytes = data.get("size_bytes")
        sha256 = data.get("sha256")
        if (
            not _identifier(preview_id)
            or mime_type not in {"image/png", "image/jpeg"}
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 < size_bytes <= MAX_PREVIEW_BYTES
            or not isinstance(sha256, str)
            or not _HEX_64.fullmatch(sha256)
        ):
            raise _relay_error()
        sanitized = {
            "preview_id": preview_id,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
        node_id = _safe_identifier(data.get("node_id"))
        if node_id is not None:
            sanitized["node_id"] = node_id
    elif event_type in {"execution_error", "execution_interrupted"}:
        sanitized = _sanitize_error(data)
    elif event_type == "status":
        queue = data.get("queue_remaining")
        sanitized = (
            {"queue_remaining": queue}
            if _finite_number(queue) and queue >= 0
            else {}
        )
    elif event_type == "progress":
        sanitized = {}
        for key in ("value", "max", "total"):
            value = data.get(key)
            if _finite_number(value):
                sanitized[key] = value
        node_id = _safe_identifier(data.get("node_id"))
        if node_id is not None:
            sanitized["node_id"] = node_id
    elif event_type == "progress_text":
        sanitized = {}
        node_id = _safe_identifier(data.get("node_id"))
        text = _safe_text(data.get("text"))
        if node_id is not None:
            sanitized["node_id"] = node_id
        if text is not None:
            sanitized["text"] = text
    elif event_type == "progress_state":
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list) or len(raw_nodes) > 10_000:
            sanitized = {"nodes": []}
        else:
            nodes = []
            for item in raw_nodes:
                if not isinstance(item, dict):
                    continue
                node_id = _safe_identifier(item.get("node_id"))
                if node_id is None:
                    continue
                node = {"node_id": node_id}
                state = _safe_identifier(item.get("state"))
                if state is not None:
                    node["state"] = state
                for key in ("value", "max"):
                    value = item.get(key)
                    if _finite_number(value):
                        node[key] = value
                nodes.append(node)
            sanitized = {"nodes": nodes}
    elif event_type in {"executing", "executed"}:
        sanitized = {}
        for key in ("node_id", "display_node_id"):
            value = _safe_identifier(data.get(key))
            if value is not None:
                sanitized[key] = value
    elif event_type == "execution_cached":
        raw_nodes = data.get("nodes")
        sanitized = {
            "nodes": [
                value
                for value in (
                    _safe_identifier(item)
                    for item in (
                        raw_nodes if isinstance(raw_nodes, list) else []
                    )
                )
                if value is not None
            ][:10_000]
        }
    else:
        timestamp = data.get("timestamp")
        sanitized = (
            {"timestamp": timestamp}
            if _finite_number(timestamp) and timestamp >= 0
            else {}
        )
    return {
        "sequence": expected_sequence,
        "type": event_type,
        "data": sanitized,
        "created_at": float(event["created_at"]),
    }


class LocalRelay:
    def __init__(
        self,
        *,
        worker,
        repository,
        private_root,
        output_root,
        clock=None,
    ):
        if worker is not None and (
            not callable(getattr(worker, "events", None))
            or not callable(getattr(worker, "job", None))
        ):
            raise ValueError("Local relay worker boundary is invalid.")
        if not isinstance(repository, JobRepository):
            raise ValueError("Local relay repository is invalid.")
        self.worker = worker
        self.repository = repository
        self.private_root = _private_directory(private_root)
        self.parts_root = _private_directory(self.private_root / "parts")
        self.previews_root = _private_directory(
            self.private_root / "previews"
        )
        self.output_root = _output_root(output_root)
        self.clock = clock or time.time

    def _now(self):
        value = self.clock()
        if not _finite_number(value) or value < 0:
            raise RelayError("Local relay clock is unavailable.")
        return float(value)

    def _job(self, job_id):
        if not _identifier(job_id):
            raise _relay_error()
        job = self.repository.get_job(job_id)
        if job is None:
            raise _relay_error("Local relay job was not found.")
        return job

    def private_part_path(self, job_id, artifact_id):
        if not _identifier(job_id) or not _identifier(artifact_id):
            raise _relay_error()
        return self.parts_root / job_id / (artifact_id + ".part")

    def _part_path(self, job_id, artifact_id):
        directory = _private_directory(self.parts_root / job_id)
        return directory / (artifact_id + ".part")

    def _preview_path(self, job_id, preview_id):
        directory = _private_directory(self.previews_root / job_id)
        return directory / (preview_id + ".preview")

    def _output_parent(self, job_id, parts):
        current = self.output_root
        for part in (job_id, *parts):
            current = current / part
            try:
                current.mkdir(mode=0o700, exist_ok=True)
                metadata = os.lstat(current)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                ):
                    raise OSError("Unsafe output directory.")
            except OSError:
                raise ArtifactVerificationError(
                    "Local output publication failed."
                ) from None
        return current

    def _existing_part(self, path, expected_size):
        if not path.exists():
            return 0, hashlib.sha256()
        try:
            metadata = os.lstat(path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or not 0 <= metadata.st_size <= expected_size
            ):
                raise OSError("Unsafe partial output.")
            os.chmod(path, 0o600)
            size, digest = _hash_path(path, maximum=expected_size)
        except (OSError, ArtifactVerificationError):
            raise ArtifactVerificationError(
                "Local partial output is invalid."
            ) from None
        hasher = hashlib.sha256()
        descriptor = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        except OSError:
            raise ArtifactVerificationError(
                "Local partial output is invalid."
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not hmac.compare_digest(hasher.hexdigest(), digest):
            raise ArtifactVerificationError(
                "Local partial output is invalid."
            )
        return size, hasher

    def _publish(self, part, destination, *, expected_size, sha256):
        if destination.exists():
            size, digest = _hash_path(
                destination,
                maximum=expected_size,
            )
            if size != expected_size or not hmac.compare_digest(
                digest,
                sha256,
            ):
                raise ArtifactVerificationError(
                    "Local output destination already exists."
                )
            try:
                part.unlink()
            except FileNotFoundError:
                pass
            return destination
        try:
            os.link(part, destination)
            part.unlink()
            os.chmod(destination, 0o600)
            _fsync_directory(destination.parent)
            return destination
        except FileExistsError:
            return self._publish(
                part,
                destination,
                expected_size=expected_size,
                sha256=sha256,
            )
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise ArtifactVerificationError(
                    "Local output publication failed."
                ) from None
        temporary = None
        descriptor = None
        source = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".cloud-run-output-",
                suffix=".part",
                dir=str(destination.parent),
            )
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            source = os.open(part, flags)
            digest = hashlib.sha256()
            copied = 0
            while True:
                chunk = os.read(source, 1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > expected_size:
                    raise ArtifactVerificationError(
                        "Local output publication failed."
                    )
                digest.update(chunk)
                _write_all(descriptor, chunk)
            if (
                copied != expected_size
                or not hmac.compare_digest(
                    digest.hexdigest(),
                    sha256,
                )
            ):
                raise ArtifactVerificationError(
                    "Local output publication failed."
                )
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.link(temporary, destination)
            temporary.unlink()
            temporary = None
            part.unlink()
            _fsync_directory(destination.parent)
            return destination
        except FileExistsError:
            return self._publish(
                part,
                destination,
                expected_size=expected_size,
                sha256=sha256,
            )
        except ArtifactVerificationError:
            raise
        except OSError:
            raise ArtifactVerificationError(
                "Local output publication failed."
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if source is not None:
                os.close(source)
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    async def download_output(
        self,
        *,
        job_id,
        descriptor,
        output_root=None,
    ):
        self._job(job_id)
        output = _validated_output(descriptor)
        if output_root is not None and _output_root(output_root) != (
            self.output_root
        ):
            raise _relay_error("Local output directory changed.")
        if self.worker is None or not callable(
            getattr(self.worker, "download_artifact", None)
        ):
            raise RelayError("Remote output transfer is unavailable.")
        artifact_id = output["artifact_id"]
        existing = self.repository.get_transfer(job_id, artifact_id)
        if existing is not None and (
            existing.expected_size != output["size_bytes"]
            or existing.sha256 != output["sha256"]
            or existing.direction != "download"
            or existing.source_node_id not in {None, output["node_id"]}
        ):
            raise ArtifactVerificationError(
                "Remote output identity changed."
            )
        if (
            existing is not None
            and existing.state == TransferState.VERIFIED
            and existing.offset == output["size_bytes"]
        ):
            published = Path(existing.private_path)
            try:
                published.resolve(strict=True).relative_to(
                    self.output_root
                )
            except (OSError, RuntimeError, ValueError):
                raise ArtifactVerificationError(
                    "Local output verification failed."
                ) from None
            if (
                existing.source_node_id != output["node_id"]
                or existing.published_device is None
                or existing.published_inode is None
            ):
                raise ArtifactVerificationError(
                    "Local output verification failed."
                )
            (
                size,
                current_digest,
                current_device,
                current_inode,
            ) = _hash_path_identity(
                published,
                maximum=output["size_bytes"],
            )
            if (
                size != output["size_bytes"]
                or not hmac.compare_digest(
                    current_digest,
                    output["sha256"],
                )
                or current_device != existing.published_device
                or current_inode != existing.published_inode
            ):
                raise ArtifactVerificationError(
                    "Local output verification failed."
                )
            return RelayArtifactResult(
                job_id=job_id,
                artifact_id=artifact_id,
                node_id=output["node_id"],
                state=TransferState.VERIFIED,
                local_path=published,
                size_bytes=output["size_bytes"],
                sha256=output["sha256"],
                mime_type=output["mime_type"],
            )
        part = self._part_path(job_id, artifact_id)
        offset, digest = self._existing_part(
            part,
            output["size_bytes"],
        )
        if existing is not None and existing.offset > offset:
            self.repository.reset_transfer(job_id, artifact_id)
            existing = None
        self.repository.save_transfer(
            job_id=job_id,
            artifact_id=artifact_id,
            direction="download",
            expected_size=output["size_bytes"],
            sha256=output["sha256"],
            offset=offset,
            state=(
                TransferState.VERIFIED
                if existing is not None
                and existing.state == TransferState.VERIFIED
                and offset == output["size_bytes"]
                else TransferState.TRANSFERRING
            ),
            private_path=str(part),
            source_node_id=output["node_id"],
        )
        file_descriptor = None
        current = offset
        try:
            if offset < output["size_bytes"]:
                flags = os.O_WRONLY | os.O_CREAT
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                file_descriptor = os.open(part, flags, 0o600)
                os.fchmod(file_descriptor, 0o600)
                os.lseek(file_descriptor, offset, os.SEEK_SET)

                async def on_chunk(chunk):
                    nonlocal current
                    if (
                        not isinstance(chunk, bytes)
                        or not chunk
                        or current + len(chunk) > output["size_bytes"]
                    ):
                        raise ArtifactVerificationError(
                            "Remote output transfer was invalid."
                        )
                    _write_all(file_descriptor, chunk)
                    digest.update(chunk)
                    current += len(chunk)
                    os.fsync(file_descriptor)
                    self.repository.save_transfer(
                        job_id=job_id,
                        artifact_id=artifact_id,
                        direction="download",
                        expected_size=output["size_bytes"],
                        sha256=output["sha256"],
                        offset=current,
                        state=TransferState.TRANSFERRING,
                        private_path=str(part),
                        source_node_id=output["node_id"],
                    )

                receipt = await self.worker.download_artifact(
                    artifact_id,
                    start=offset,
                    on_chunk=on_chunk,
                )
                if (
                    not isinstance(receipt, ArtifactDownload)
                    or receipt.artifact_id != artifact_id
                    or receipt.start != offset
                    or receipt.total_size != output["size_bytes"]
                    or not hmac.compare_digest(
                        receipt.sha256,
                        output["sha256"],
                    )
                ):
                    raise ArtifactVerificationError(
                        "Remote output identity changed."
                    )
            if file_descriptor is not None:
                os.fsync(file_descriptor)
                os.close(file_descriptor)
                file_descriptor = None
            if (
                current != output["size_bytes"]
                or not hmac.compare_digest(
                    digest.hexdigest(),
                    output["sha256"],
                )
            ):
                raise ArtifactVerificationError(
                    "Remote output verification failed."
                )
            parent = self._output_parent(
                job_id,
                output["subfolder_parts"],
            )
            destination = parent / output["filename"]
            published = self._publish(
                part,
                destination,
                expected_size=output["size_bytes"],
                sha256=output["sha256"],
            )
            (
                published_size,
                published_digest,
                published_device,
                published_inode,
            ) = _hash_path_identity(
                published,
                maximum=output["size_bytes"],
            )
            if (
                published_size != output["size_bytes"]
                or not hmac.compare_digest(
                    published_digest,
                    output["sha256"],
                )
            ):
                raise ArtifactVerificationError(
                    "Local output verification failed."
                )
            self.repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact_id,
                direction="download",
                expected_size=output["size_bytes"],
                sha256=output["sha256"],
                offset=output["size_bytes"],
                state=TransferState.VERIFIED,
                private_path=str(published),
                source_node_id=output["node_id"],
                published_device=published_device,
                published_inode=published_inode,
            )
            return RelayArtifactResult(
                job_id=job_id,
                artifact_id=artifact_id,
                node_id=output["node_id"],
                state=TransferState.VERIFIED,
                local_path=published,
                size_bytes=output["size_bytes"],
                sha256=output["sha256"],
                mime_type=output["mime_type"],
            )
        except asyncio.CancelledError:
            raise
        except ArtifactVerificationError:
            if file_descriptor is not None:
                os.close(file_descriptor)
                file_descriptor = None
            try:
                actual = part.stat().st_size
            except OSError:
                actual = 0
            if actual >= output["size_bytes"]:
                try:
                    part.unlink()
                except OSError:
                    pass
                self.repository.reset_transfer(job_id, artifact_id)
                actual = 0
            self.repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact_id,
                direction="download",
                expected_size=output["size_bytes"],
                sha256=output["sha256"],
                offset=actual,
                state=TransferState.FAILED,
                private_path=str(part),
                source_node_id=output["node_id"],
            )
            raise
        except (WorkerClientError, OSError, ValueError):
            if file_descriptor is not None:
                os.close(file_descriptor)
                file_descriptor = None
            self.repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact_id,
                direction="download",
                expected_size=output["size_bytes"],
                sha256=output["sha256"],
                offset=current,
                state=TransferState.FAILED,
                private_path=str(part),
                source_node_id=output["node_id"],
            )
            raise ArtifactVerificationError(
                "Remote output transfer failed."
            ) from None
        except Exception:
            if file_descriptor is not None:
                os.close(file_descriptor)
                file_descriptor = None
            self.repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact_id,
                direction="download",
                expected_size=output["size_bytes"],
                sha256=output["sha256"],
                offset=current,
                state=TransferState.FAILED,
                private_path=str(part),
                source_node_id=output["node_id"],
            )
            raise ArtifactVerificationError(
                "Remote output transfer failed."
            ) from None
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)

    async def _cache_preview(self, job_id, data):
        if self.worker is None or not callable(
            getattr(self.worker, "preview", None)
        ):
            raise RelayError("Remote preview transfer is unavailable.")
        preview_id = data["preview_id"]
        try:
            response = await self.worker.preview(job_id, preview_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise ArtifactVerificationError(
                "Remote preview transfer failed."
            ) from None
        if isinstance(response, PreviewDownload):
            content = response.content
            mime_type = response.mime_type
            digest = response.sha256
        elif isinstance(response, dict):
            content = response.get("content")
            mime_type = response.get("mime_type")
            digest = response.get("sha256")
        else:
            raise ArtifactVerificationError(
                "Remote preview verification failed."
            )
        if (
            not isinstance(content, bytes)
            or len(content) != data["size_bytes"]
            or mime_type != data["mime_type"]
            or not isinstance(digest, str)
            or not hmac.compare_digest(digest, data["sha256"])
            or not hmac.compare_digest(
                hashlib.sha256(content).hexdigest(),
                data["sha256"],
            )
            or (
                mime_type == "image/png"
                and not content.startswith(b"\x89PNG\r\n\x1a\n")
            )
            or (
                mime_type == "image/jpeg"
                and not content.startswith(b"\xff\xd8\xff")
            )
        ):
            raise ArtifactVerificationError(
                "Remote preview verification failed."
            )
        path = self._preview_path(job_id, preview_id)
        if path.exists():
            size, current_digest = _hash_path(
                path,
                maximum=MAX_PREVIEW_BYTES,
            )
            if size != len(content) or not hmac.compare_digest(
                current_digest,
                data["sha256"],
            ):
                raise ArtifactVerificationError(
                    "Local preview cache is invalid."
                )
        else:
            descriptor = None
            temporary = None
            try:
                descriptor, name = tempfile.mkstemp(
                    prefix=".preview-",
                    suffix=".part",
                    dir=str(path.parent),
                )
                temporary = Path(name)
                os.fchmod(descriptor, 0o600)
                _write_all(descriptor, content)
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = None
                os.replace(temporary, path)
                temporary = None
                os.chmod(path, 0o600)
                _fsync_directory(path.parent)
            except OSError:
                raise ArtifactVerificationError(
                    "Local preview cache failed."
                ) from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if temporary is not None:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
        self.repository.save_transfer(
            job_id=job_id,
            artifact_id="preview:" + preview_id,
            direction="download",
            expected_size=data["size_bytes"],
            sha256=data["sha256"],
            offset=data["size_bytes"],
            state=TransferState.VERIFIED,
            private_path=str(path),
        )

    async def sync_job(self, job_id):
        self._job(job_id)
        if self.worker is None:
            raise RelayError("Remote worker is unavailable.")
        cursor = self.repository.last_event_sequence(job_id)
        try:
            response = await self.worker.events(job_id, cursor)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise RelayError("Remote worker events are unavailable.") from None
        if (
            not isinstance(response, dict)
            or set(response) != {
                "job_id",
                "events",
                "last_sequence",
            }
            or response.get("job_id") != job_id
            or not isinstance(response.get("events"), list)
            or isinstance(response.get("last_sequence"), bool)
            or not isinstance(response.get("last_sequence"), int)
        ):
            raise _relay_error()
        sanitized_events = []
        for raw_event in response["events"]:
            event = _sanitize_event(raw_event, cursor + 1)
            stored = self.repository.append_event(
                job_id,
                event["sequence"],
                event["type"],
                event["data"],
                created_at=event["created_at"],
            )
            cursor = event["sequence"]
            material = {
                "sequence": stored.sequence,
                "type": stored.event_type,
                "data": stored.payload,
                "created_at": stored.created_at,
            }
            sanitized_events.append(material)
            if event["type"] in {
                "b_preview",
                "b_preview_with_metadata",
            }:
                await self._cache_preview(job_id, event["data"])
        if response["last_sequence"] != cursor:
            raise _relay_error()
        for stored_event in self.repository.list_events(job_id, 0):
            if stored_event.event_type not in {
                "b_preview",
                "b_preview_with_metadata",
            }:
                continue
            preview_id = stored_event.payload.get("preview_id")
            transfer = self.repository.get_transfer(
                job_id,
                "preview:" + str(preview_id),
            )
            if (
                transfer is None
                or transfer.state != TransferState.VERIFIED
            ):
                await self._cache_preview(
                    job_id,
                    stored_event.payload,
                )
        try:
            remote = await self.worker.job(job_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise RelayError("Remote worker job is unavailable.") from None
        if (
            not isinstance(remote, dict)
            or set(remote) != {
                "job_id",
                "state",
                "prompt_id",
                "last_sequence",
                "outputs",
                "error",
            }
            or remote.get("job_id") != job_id
            or remote.get("state")
            not in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "interrupted",
            }
            or not isinstance(remote.get("outputs"), list)
            or isinstance(remote.get("last_sequence"), bool)
            or not isinstance(remote.get("last_sequence"), int)
            or remote["last_sequence"] != cursor
        ):
            raise _relay_error()
        prompt_id = remote.get("prompt_id")
        if prompt_id is not None:
            try:
                if str(uuid.UUID(prompt_id)) != prompt_id:
                    raise ValueError("Non-canonical prompt ID.")
            except (AttributeError, TypeError, ValueError):
                raise _relay_error() from None
        outputs = []
        if remote["state"] == "succeeded":
            seen_artifacts = set()
            for output in remote["outputs"]:
                validated = _validated_output(output)
                if validated["artifact_id"] in seen_artifacts:
                    raise _relay_error()
                seen_artifacts.add(validated["artifact_id"])
                outputs.append(
                    await self.download_output(
                        job_id=job_id,
                        descriptor=output,
                    )
                )
        error = (
            _sanitize_error(remote["error"])
            if remote["error"] is not None
            else None
        )
        return RelaySyncResult(
            job_id=job_id,
            state=remote["state"],
            last_sequence=cursor,
            events=tuple(sanitized_events),
            outputs=tuple(outputs),
            error=error,
        )

    def preview_content(self, job_id, preview_id):
        self._job(job_id)
        if not _identifier(preview_id):
            raise _relay_error()
        transfer = self.repository.get_transfer(
            job_id,
            "preview:" + preview_id,
        )
        if transfer is None or transfer.state != TransferState.VERIFIED:
            raise _relay_error("Local preview was not found.")
        path = Path(transfer.private_path)
        descriptor = None
        try:
            expected_parent = (
                self.previews_root / job_id
            ).resolve(strict=True)
            if path.parent.resolve(strict=True) != expected_parent:
                raise OSError("Preview escaped cache.")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or metadata.st_size != transfer.expected_size
            ):
                raise OSError("Preview metadata changed.")
            chunks = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, remaining),
                )
                if not chunk:
                    raise OSError("Preview changed.")
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        except (OSError, RuntimeError):
            raise ArtifactVerificationError(
                "Local preview verification failed."
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        digest = hashlib.sha256(content).hexdigest()
        if (
            len(content) != transfer.expected_size
            or not hmac.compare_digest(digest, transfer.sha256)
        ):
            raise ArtifactVerificationError(
                "Local preview verification failed."
            )
        mime_type = None
        for event in self.repository.list_events(job_id, 0):
            if event.payload.get("preview_id") == preview_id:
                mime_type = event.payload.get("mime_type")
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ArtifactVerificationError(
                "Local preview verification failed."
            )
        return RelayMedia(
            content=content,
            mime_type=mime_type,
            sha256=digest,
        )

    def published_artifact(self, job_id, artifact_id):
        self._job(job_id)
        if not _identifier(artifact_id):
            raise _relay_error()
        transfer = self.repository.get_transfer(job_id, artifact_id)
        if (
            transfer is None
            or transfer.state != TransferState.VERIFIED
            or transfer.direction != "download"
            or transfer.artifact_id.startswith("preview:")
            or transfer.source_node_id is None
            or transfer.published_device is None
            or transfer.published_inode is None
        ):
            raise _relay_error("Local output was not found.")
        path = Path(transfer.private_path)
        try:
            path.resolve(strict=True).relative_to(self.output_root)
        except (OSError, RuntimeError, ValueError):
            raise ArtifactVerificationError(
                "Local output verification failed."
            ) from None
        size, digest, device, inode = _hash_path_identity(
            path,
            maximum=MAX_OUTPUT_BYTES,
        )
        if (
            size != transfer.expected_size
            or not hmac.compare_digest(digest, transfer.sha256)
            or device != transfer.published_device
            or inode != transfer.published_inode
        ):
            raise ArtifactVerificationError(
                "Local output verification failed."
            )
        mime_type, _encoding = mimetypes.guess_type(path.name)
        if (
            not isinstance(mime_type, str)
            or not _MIME_TYPE.fullmatch(mime_type)
        ):
            mime_type = "application/octet-stream"
        return PublishedArtifact(
            path=path,
            mime_type=mime_type,
            size_bytes=size,
            sha256=digest,
        )

"""Single-job native ComfyUI execution with durable sanitized records."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
import uuid

from cloud_run.worker_protocol import (
    CaptureValidationError,
    CompiledCapture,
    canonical_json,
)
from .comfy import (
    ComfyProcessError,
    ComfyPromptValidationError,
    NativeExecution,
)
from .state import WorkerStateError, WorkerStateStore


MAX_JOB_REQUEST_BYTES = 16 * 1024 * 1024
MAX_JOB_EVENTS = 20_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_REQUEST_FIELDS = {
    "job_id",
    "manifest_digest",
    "workflow",
    "output",
    "queue_options",
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
_TERMINAL_EVENTS = {
    "execution_success",
    "execution_error",
    "execution_interrupted",
}
_SUSPICIOUS_TEXT = re.compile(
    r"(?i)(authorization|bearer|api[_ -]?key|password|secret|token)"
)


class JobError(RuntimeError):
    """A sanitized worker job failure."""


class JobValidationError(JobError):
    pass


class JobBusyError(JobError):
    pass


@dataclass(frozen=True)
class JobRequest:
    job_id: str
    manifest_digest: str
    capture: CompiledCapture
    request_digest: str


@dataclass(frozen=True)
class PreviewContent:
    content: bytes
    mime_type: str
    sha256: str


@dataclass(frozen=True)
class OutputContent:
    path: Path
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class JobResult:
    job_id: str
    state: str
    client_id: str
    prompt_id: object
    events: tuple
    outputs: tuple
    error: object

    @classmethod
    def from_record(cls, record):
        return cls(
            job_id=record["job_id"],
            state=record["state"],
            client_id=record["client_id"],
            prompt_id=record["prompt_id"],
            events=tuple(
                {
                    "sequence": event["sequence"],
                    "type": event["type"],
                    "data": dict(event["data"]),
                    "created_at": event["created_at"],
                }
                for event in record["events"]
            ),
            outputs=tuple(
                _public_output(record["outputs"][artifact_id])
                for artifact_id in sorted(record["outputs"])
            ),
            error=(
                dict(record["error"])
                if record["error"] is not None
                else None
            ),
        )

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "state": self.state,
            "prompt_id": self.prompt_id,
            "last_sequence": (
                self.events[-1]["sequence"] if self.events else 0
            ),
            "outputs": [dict(item) for item in self.outputs],
            "error": dict(self.error) if self.error is not None else None,
        }


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    state: str
    prompt_id: object
    events: tuple
    last_sequence: int
    outputs: tuple
    error: object
    created_at: float
    updated_at: float

    @classmethod
    def from_record(cls, record, after_sequence):
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise JobValidationError("Remote job event cursor is invalid.")
        return cls(
            job_id=record["job_id"],
            state=record["state"],
            prompt_id=record["prompt_id"],
            events=tuple(
                {
                    "sequence": event["sequence"],
                    "type": event["type"],
                    "data": dict(event["data"]),
                    "created_at": event["created_at"],
                }
                for event in record["events"]
                if event["sequence"] > after_sequence
            ),
            last_sequence=record["sequence"],
            outputs=tuple(
                _public_output(record["outputs"][artifact_id])
                for artifact_id in sorted(record["outputs"])
            ),
            error=(
                dict(record["error"])
                if record["error"] is not None
                else None
            ),
            created_at=float(record["created_at"]),
            updated_at=float(record["updated_at"]),
        )

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "state": self.state,
            "prompt_id": self.prompt_id,
            "events": [
                {
                    **event,
                    "data": dict(event["data"]),
                }
                for event in self.events
            ],
            "last_sequence": self.last_sequence,
            "outputs": [dict(item) for item in self.outputs],
            "error": dict(self.error) if self.error is not None else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _job_error():
    return JobValidationError("Remote job request was rejected.")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _request_from_payload(payload):
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        raise _job_error()
    job_id = payload.get("job_id")
    manifest_digest = payload.get("manifest_digest")
    if (
        not isinstance(job_id, str)
        or not _IDENTIFIER.fullmatch(job_id)
        or not isinstance(manifest_digest, str)
        or not _HEX_64.fullmatch(manifest_digest)
    ):
        raise _job_error()
    try:
        capture = CompiledCapture.from_payload(
            {
                "workflow": payload["workflow"],
                "output": payload["output"],
                "queue_options": payload["queue_options"],
            }
        )
        material = canonical_json(
            {
                "manifest_digest": manifest_digest,
                "workflow": capture.workflow,
                "output": capture.output,
                "queue_options": capture.queue_options,
            }
        ).encode("utf-8")
    except (CaptureValidationError, TypeError, ValueError):
        raise _job_error() from None
    return JobRequest(
        job_id=job_id,
        manifest_digest=manifest_digest,
        capture=capture,
        request_digest=hashlib.sha256(material).hexdigest(),
    )


def parse_job_request(body):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= MAX_JOB_REQUEST_BYTES
    ):
        raise _job_error()
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _job_error() from None
    return _request_from_payload(payload)


def _safe_identifier(value):
    if isinstance(value, (int, str)):
        normalized = str(value)
        if _IDENTIFIER.fullmatch(normalized):
            return normalized
    return None


def _safe_number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return value


def _safe_text(value, *, maximum=512):
    if not isinstance(value, str):
        return None
    normalized = "".join(
        character if ord(character) >= 32 else " "
        for character in value
    ).strip()
    if not normalized:
        return None
    if _SUSPICIOUS_TEXT.search(normalized):
        return "[redacted]"
    return normalized[:maximum]


def _workflow_context(capture, node_id):
    safe_node_id = _safe_identifier(node_id)
    if safe_node_id is None:
        return {}
    context = {"node_id": safe_node_id}
    prompt_node = capture.output.get(safe_node_id)
    if isinstance(prompt_node, dict):
        class_type = _safe_identifier(prompt_node.get("class_type"))
        if class_type is not None:
            context["class_type"] = class_type
    for workflow_node in capture.workflow.get("nodes", []):
        if (
            isinstance(workflow_node, dict)
            and str(workflow_node.get("id")) == safe_node_id
        ):
            title = _safe_text(workflow_node.get("title"), maximum=200)
            if title is not None and title != "[redacted]":
                context["title"] = title
            if "class_type" not in context:
                class_type = _safe_identifier(
                    workflow_node.get("type")
                )
                if class_type is not None:
                    context["class_type"] = class_type
            break
    return context


def _execution_error(capture, data, *, validation=False):
    message = data.get("exception_message") if isinstance(data, dict) else None
    is_oom = (
        isinstance(message, str)
        and "out of memory" in message.casefold()
    )
    if validation:
        code = "validation_failed"
        public_message = "Remote ComfyUI rejected the compiled prompt."
    elif is_oom:
        code = "out_of_memory"
        public_message = "Remote execution ran out of GPU memory."
    else:
        code = "execution_failed"
        public_message = "Remote execution failed."
    node_id = None
    if isinstance(data, dict):
        node_id = data.get("node_id", data.get("node"))
    return {
        "code": code,
        "message": public_message,
        **_workflow_context(capture, node_id),
    }


def _public_output(record):
    return {
        "artifact_id": record["artifact_id"],
        "node_id": record["node_id"],
        "filename": record["filename"],
        "subfolder": record["subfolder"],
        "mime_type": record["mime_type"],
        "size_bytes": record["size_bytes"],
        "sha256": record["sha256"],
    }


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
            raise JobValidationError(
                "Worker preview storage is unavailable."
            )
        os.chmod(path, 0o700)
        return path.resolve(strict=True)
    except JobValidationError:
        raise
    except (OSError, RuntimeError):
        raise JobValidationError(
            "Worker preview storage is unavailable."
        ) from None


def _hash_open_file(path):
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
            or metadata.st_size > MAX_OUTPUT_BYTES
        ):
            raise JobError("Remote output is unavailable.")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_OUTPUT_BYTES:
                raise JobError("Remote output is unavailable.")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            size != metadata.st_size
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ino != metadata.st_ino
        ):
            raise JobError("Remote output is unavailable.")
        return size, digest.hexdigest()
    except JobError:
        raise
    except OSError:
        raise JobError("Remote output is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class JobManager:
    def __init__(
        self,
        *,
        comfy,
        state,
        preview_root,
        token=None,
        clock=None,
    ):
        if not callable(getattr(comfy, "execute_native", None)):
            raise ValueError("Remote ComfyUI job boundary is invalid.")
        if not callable(getattr(comfy, "history", None)):
            raise ValueError("Remote ComfyUI job boundary is invalid.")
        if not callable(getattr(comfy, "output_path", None)):
            raise ValueError("Remote ComfyUI job boundary is invalid.")
        if not isinstance(state, WorkerStateStore):
            raise ValueError("Remote worker job state is invalid.")
        self.comfy = comfy
        self.state = state
        self.preview_root = _private_directory(preview_root)
        self.token = token or (lambda: secrets.token_urlsafe(24))
        self.clock = clock or time.time
        self._active_job_id = None
        self._guard = None
        self._guard_loop = None
        self._submission_guard = None
        self._submission_guard_loop = None
        self._background_tasks = set()
        self._reconcile_orphaned_jobs()

    @property
    def busy(self):
        return self._active_job_id is not None

    def _job_guard(self):
        loop = asyncio.get_running_loop()
        if self._guard is None or self._guard_loop is not loop:
            self._guard = asyncio.Lock()
            self._guard_loop = loop
        return self._guard

    def _job_submission_guard(self):
        loop = asyncio.get_running_loop()
        if (
            self._submission_guard is None
            or self._submission_guard_loop is not loop
        ):
            self._submission_guard = asyncio.Lock()
            self._submission_guard_loop = loop
        return self._submission_guard

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise JobError("Remote worker job clock is unavailable.")
        return float(value)

    def _new_token(self, existing, *, namespace):
        if namespace not in {"previews", "outputs"}:
            raise JobError("Remote worker identifier generation failed.")
        reserved = set(existing)
        try:
            state = self.state.load()
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        for record in state["jobs"].values():
            values = record.get(namespace)
            if isinstance(values, dict):
                reserved.update(values)
        for _attempt in range(100):
            value = self.token()
            if (
                isinstance(value, str)
                and _IDENTIFIER.fullmatch(value)
                and value not in reserved
            ):
                return value
        raise JobError("Remote worker identifier generation failed.")

    def _record(self, record):
        try:
            self.state.record_job(record["job_id"], record)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        return record

    def _reconcile_orphaned_jobs(self):
        try:
            state = self.state.load()
        except WorkerStateError:
            raise ValueError("Remote worker job state is invalid.") from None
        if not state["claimed"]:
            return
        for job_id in sorted(state["jobs"]):
            try:
                record = self.state.job(job_id)
            except WorkerStateError:
                raise ValueError(
                    "Remote worker job state is invalid."
                ) from None
            if record["state"] not in {"queued", "running"}:
                continue
            if len(record["events"]) >= MAX_JOB_EVENTS:
                raise ValueError(
                    "Remote worker job state is invalid."
                )
            now = self._now()
            error = {
                "code": "worker_restarted",
                "message": (
                    "Remote execution was interrupted by a worker restart."
                ),
            }
            event = {
                "sequence": record["sequence"] + 1,
                "type": "execution_interrupted",
                "data": dict(error),
                "created_at": now,
            }
            self._record(
                {
                    **record,
                    "state": "interrupted",
                    "sequence": event["sequence"],
                    "events": [*record["events"], event],
                    "error": error,
                    "updated_at": now,
                }
            )

    def _initial_record(self, request, client_id):
        now = self._now()
        return {
            "kind": "job",
            "job_id": request.job_id,
            "request_digest": request.request_digest,
            "manifest_digest": request.manifest_digest,
            "state": "queued",
            "client_id": client_id,
            "prompt_id": None,
            "sequence": 0,
            "events": [],
            "previews": {},
            "outputs": {},
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    def _append_event(self, record, event_type, data):
        if len(record["events"]) >= MAX_JOB_EVENTS:
            raise JobError("Remote worker event capacity was exceeded.")
        event = {
            "sequence": record["sequence"] + 1,
            "type": event_type,
            "data": data,
            "created_at": self._now(),
        }
        record = {
            **record,
            "sequence": event["sequence"],
            "events": [*record["events"], event],
            "updated_at": event["created_at"],
        }
        self._record(record)
        return record

    def _store_preview(self, record, data):
        content = data.get("content")
        mime_type = data.get("mime_type")
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= MAX_PREVIEW_BYTES
            or mime_type not in {"image/jpeg", "image/png"}
            or (
                mime_type == "image/jpeg"
                and not content.startswith(b"\xff\xd8\xff")
            )
            or (
                mime_type == "image/png"
                and not content.startswith(b"\x89PNG\r\n\x1a\n")
            )
        ):
            raise JobError("Remote preview was rejected.")
        preview_id = self._new_token(
            record["previews"],
            namespace="previews",
        )
        final_path = self.preview_root / (preview_id + ".preview")
        temporary_path = None
        descriptor = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".preview-",
                suffix=".part",
                dir=str(self.preview_root),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, final_path)
            temporary_path = None
            os.chmod(final_path, 0o600)
        except OSError:
            raise JobError("Remote preview storage failed.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        metadata = data.get("metadata")
        node_id = (
            _safe_identifier(metadata.get("node_id"))
            if isinstance(metadata, dict)
            else None
        )
        preview = {
            "preview_id": preview_id,
            "mime_type": mime_type,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "node_id": node_id,
            "private_path": str(final_path),
        }
        previews = dict(record["previews"])
        previews[preview_id] = preview
        return {
            **record,
            "previews": previews,
        }, {
            "preview_id": preview_id,
            "mime_type": mime_type,
            "size_bytes": len(content),
            "sha256": preview["sha256"],
            **({"node_id": node_id} if node_id is not None else {}),
        }

    def _sanitize_progress_state(self, data):
        nodes = data.get("nodes")
        if not isinstance(nodes, dict) or len(nodes) > 10_000:
            return {}
        safe_nodes = []
        for key in sorted(nodes):
            item = nodes[key]
            node_id = _safe_identifier(key)
            if node_id is None or not isinstance(item, dict):
                continue
            safe = {"node_id": node_id}
            state = _safe_identifier(item.get("state"))
            if state is not None:
                safe["state"] = state
            for source, destination in (
                ("value", "value"),
                ("max", "max"),
            ):
                number = _safe_number(item.get(source))
                if number is not None:
                    safe[destination] = number
            safe_nodes.append(safe)
        return {"nodes": safe_nodes}

    def _sanitize_event(self, request, record, event):
        if (
            not isinstance(event, dict)
            or event.get("type") not in _EVENT_TYPES
            or not isinstance(event.get("data"), dict)
        ):
            return record, None, None
        event_type = event["type"]
        data = event["data"]
        if event_type in {"b_preview", "b_preview_with_metadata"}:
            record, public = self._store_preview(record, data)
            return record, public, None
        if event_type == "status":
            status = data.get("status")
            exec_info = (
                status.get("exec_info")
                if isinstance(status, dict)
                else None
            )
            queue_remaining = (
                _safe_number(exec_info.get("queue_remaining"))
                if isinstance(exec_info, dict)
                else None
            )
            public = (
                {"queue_remaining": queue_remaining}
                if queue_remaining is not None
                else {}
            )
        elif event_type == "progress":
            public = {}
            for key in ("value", "max", "total"):
                number = _safe_number(data.get(key))
                if number is not None:
                    public[key] = number
            node_id = _safe_identifier(
                data.get("node", data.get("node_id"))
            )
            if node_id is not None:
                public["node_id"] = node_id
        elif event_type == "progress_text":
            public = {}
            node_id = _safe_identifier(
                data.get("node", data.get("node_id"))
            )
            text = _safe_text(data.get("text"))
            if node_id is not None:
                public["node_id"] = node_id
            if text is not None:
                public["text"] = text
        elif event_type == "progress_state":
            public = self._sanitize_progress_state(data)
        elif event_type in {"executing", "executed"}:
            public = {}
            node_id = _safe_identifier(
                data.get("node", data.get("node_id"))
            )
            display = _safe_identifier(
                data.get("display_node", data.get("display_node_id"))
            )
            if node_id is not None:
                public["node_id"] = node_id
            if display is not None:
                public["display_node_id"] = display
        elif event_type == "execution_cached":
            raw_nodes = data.get("nodes")
            public = {
                "nodes": [
                    safe
                    for safe in (
                        _safe_identifier(item)
                        for item in (
                            raw_nodes if isinstance(raw_nodes, list) else []
                        )
                    )
                    if safe is not None
                ][:10_000]
            }
        elif event_type == "execution_error":
            error = _execution_error(request.capture, data)
            public = dict(error)
            return record, public, error
        elif event_type == "execution_interrupted":
            error = {
                "code": "execution_interrupted",
                "message": "Remote execution was interrupted.",
                **_workflow_context(
                    request.capture,
                    data.get("node_id", data.get("node")),
                ),
            }
            return record, dict(error), error
        else:
            timestamp = _safe_number(data.get("timestamp"))
            public = (
                {"timestamp": timestamp}
                if timestamp is not None
                else {}
            )
        return record, public, None

    def _history_outputs(self, request, execution, history):
        if (
            not isinstance(history, dict)
            or set(history) != {execution.prompt_id}
            or not isinstance(history[execution.prompt_id], dict)
        ):
            raise JobError("Remote execution history is unavailable.")
        outputs = history[execution.prompt_id].get("outputs")
        if not isinstance(outputs, dict) or len(outputs) > 100_000:
            raise JobError("Remote execution history is unavailable.")
        records = {}
        seen_paths = set()
        for raw_node_id in sorted(outputs, key=str):
            node_id = _safe_identifier(raw_node_id)
            node_outputs = outputs[raw_node_id]
            if node_id is None or not isinstance(node_outputs, dict):
                raise JobError("Remote output descriptor is invalid.")
            for category in sorted(node_outputs):
                descriptors = node_outputs[category]
                if not isinstance(descriptors, list):
                    continue
                for descriptor in descriptors:
                    if (
                        not isinstance(descriptor, dict)
                        or not {
                            "filename",
                            "subfolder",
                            "type",
                        }.issubset(descriptor)
                    ):
                        raise JobError(
                            "Remote output descriptor is invalid."
                        )
                    minimal = {
                        "filename": descriptor["filename"],
                        "subfolder": descriptor["subfolder"],
                        "type": descriptor["type"],
                    }
                    descriptor_type = descriptor.get("type")
                    if descriptor_type == "temp":
                        continue
                    if descriptor_type != "output":
                        raise JobError(
                            "Remote output descriptor is invalid."
                        )
                    try:
                        path = Path(self.comfy.output_path(minimal))
                    except Exception:
                        raise JobError(
                            "Remote output descriptor is invalid."
                        ) from None
                    key = str(path)
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    size, digest = _hash_open_file(path)
                    artifact_id = self._new_token(
                        records,
                        namespace="outputs",
                    )
                    mime_method = getattr(
                        self.comfy,
                        "output_mime_type",
                        None,
                    )
                    mime_type = (
                        mime_method(path)
                        if callable(mime_method)
                        else "application/octet-stream"
                    )
                    if (
                        not isinstance(mime_type, str)
                        or len(mime_type) > 100
                    ):
                        mime_type = "application/octet-stream"
                    records[artifact_id] = {
                        "artifact_id": artifact_id,
                        "node_id": node_id,
                        "filename": minimal["filename"],
                        "subfolder": minimal["subfolder"],
                        "mime_type": mime_type,
                        "size_bytes": size,
                        "sha256": digest,
                        "private_path": str(path),
                    }
        return records

    async def run(self, payload):
        request = (
            payload
            if isinstance(payload, JobRequest)
            else _request_from_payload(payload)
        )
        async with self._job_guard():
            if self._active_job_id is not None:
                raise JobBusyError("Remote worker is already executing a job.")
            try:
                current_state = self.state.load()
                existing = self.state.job(request.job_id)
            except WorkerStateError:
                raise JobError(
                    "Remote worker job state is unavailable."
                ) from None
            installed_digest = current_state["installed"].get(
                "manifest_digest"
            )
            if (
                not isinstance(installed_digest, str)
                or not hmac.compare_digest(
                    installed_digest,
                    request.manifest_digest,
                )
            ):
                raise _job_error()
            if existing is not None:
                if not hmac.compare_digest(
                    existing["request_digest"],
                    request.request_digest,
                ):
                    raise _job_error()
                return JobResult.from_record(existing)
            client_id = str(uuid.uuid4())
            record = self._initial_record(request, client_id)
            self._record(record)
            self._active_job_id = request.job_id

        terminal_error = None

        async def on_event(event):
            nonlocal record, terminal_error
            updated, public, error = self._sanitize_event(
                request,
                record,
                event,
            )
            record = updated
            if public is None:
                return
            record = self._append_event(record, event["type"], public)
            if error is not None:
                terminal_error = error

        try:
            record = self._record(
                {
                    **record,
                    "state": "running",
                    "updated_at": self._now(),
                }
            )
            execution = await self.comfy.execute_native(
                {
                    "client_id": client_id,
                    "prompt": request.capture.output,
                    **(
                        {
                            "partial_execution_targets": (
                                request.capture.queue_options[
                                    "partial_execution_targets"
                                ]
                            )
                        }
                        if "partial_execution_targets"
                        in request.capture.queue_options
                        else {}
                    ),
                    "extra_data": {
                        "comfy_usage_source": "comfyui-cloud-run",
                        "extra_pnginfo": {
                            "workflow": request.capture.workflow,
                        },
                        **(
                            {
                                "preview_method": (
                                    request.capture.queue_options[
                                        "preview_method"
                                    ]
                                )
                            }
                            if "preview_method"
                            in request.capture.queue_options
                            else {}
                        ),
                    },
                },
                on_event,
            )
            if not isinstance(execution, NativeExecution):
                raise JobError("Remote execution result is invalid.")
            record = {
                **record,
                "prompt_id": execution.prompt_id,
                "updated_at": self._now(),
            }
            if execution.terminal_event == "execution_success":
                history = await self.comfy.history(execution.prompt_id)
                outputs = self._history_outputs(
                    request,
                    execution,
                    history,
                )
                record = self._record(
                    {
                        **record,
                        "state": "succeeded",
                        "outputs": outputs,
                        "error": None,
                        "updated_at": self._now(),
                    }
                )
            else:
                error = terminal_error or {
                    "code": (
                        "execution_interrupted"
                        if execution.terminal_event
                        == "execution_interrupted"
                        else "execution_failed"
                    ),
                    "message": (
                        "Remote execution was interrupted."
                        if execution.terminal_event
                        == "execution_interrupted"
                        else "Remote execution failed."
                    ),
                }
                record = self._record(
                    {
                        **record,
                        "state": (
                            "interrupted"
                            if execution.terminal_event
                            == "execution_interrupted"
                            else "failed"
                        ),
                        "error": error,
                        "updated_at": self._now(),
                    }
                )
        except ComfyPromptValidationError as error:
            context = {
                "node_id": error.node_ids[0]
                if error.node_ids
                else None
            }
            sanitized = _execution_error(
                request.capture,
                context,
                validation=True,
            )
            record = self._append_event(
                record,
                "execution_error",
                sanitized,
            )
            record = self._record(
                {
                    **record,
                    "state": "failed",
                    "error": sanitized,
                    "updated_at": self._now(),
                }
            )
        except asyncio.CancelledError:
            sanitized = {
                "code": "execution_interrupted",
                "message": "Remote execution was interrupted.",
            }
            record = self._record(
                {
                    **record,
                    "state": "interrupted",
                    "error": sanitized,
                    "updated_at": self._now(),
                }
            )
            raise
        except (JobError, ComfyProcessError):
            sanitized = {
                "code": "execution_failed",
                "message": "Remote execution failed.",
            }
            record = self._record(
                {
                    **record,
                    "state": "failed",
                    "error": sanitized,
                    "updated_at": self._now(),
                }
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            sanitized = {
                "code": "execution_failed",
                "message": "Remote execution failed.",
            }
            record = self._record(
                {
                    **record,
                    "state": "failed",
                    "error": sanitized,
                    "updated_at": self._now(),
                }
            )
        finally:
            async with self._job_guard():
                if self._active_job_id == request.job_id:
                    self._active_job_id = None
        return JobResult.from_record(record)

    async def start(self, payload):
        request = (
            payload
            if isinstance(payload, JobRequest)
            else _request_from_payload(payload)
        )
        async with self._job_submission_guard():
            existing = self.job(request.job_id)
            if existing is not None:
                try:
                    record = self.state.job(request.job_id)
                except WorkerStateError:
                    raise JobError(
                        "Remote worker job state is unavailable."
                    ) from None
                if not hmac.compare_digest(
                    record["request_digest"],
                    request.request_digest,
                ):
                    raise _job_error()
                return existing
            if self.busy:
                raise JobBusyError(
                    "Remote worker is already executing a job."
                )
            task = asyncio.create_task(self.run(request))
            self._background_tasks.add(task)

            def complete(done):
                self._background_tasks.discard(done)
                if not done.cancelled():
                    try:
                        done.exception()
                    except (asyncio.CancelledError, Exception):
                        pass

            task.add_done_callback(complete)
            await asyncio.sleep(0)
            current = self.job(request.job_id)
            if current is None:
                task.cancel()
                raise JobError(
                    "Remote worker job state is unavailable."
                )
            return current

    def job(self, job_id):
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        return JobResult.from_record(record) if record is not None else None

    def snapshot(self, job_id, after_sequence=0):
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise JobValidationError("Remote job event cursor is invalid.")
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            return None
        return JobSnapshot.from_record(record, after_sequence)

    def events(self, job_id, after_sequence=0):
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise JobValidationError("Remote job event cursor is invalid.")
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            return None
        return tuple(
            {
                "sequence": event["sequence"],
                "type": event["type"],
                "data": dict(event["data"]),
                "created_at": event["created_at"],
            }
            for event in record["events"]
            if event["sequence"] > after_sequence
        )

    def preview(self, job_id, preview_id):
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            return None
        preview = record["previews"].get(preview_id)
        if preview is None:
            return None
        path = Path(preview["private_path"])
        descriptor = None
        try:
            if path.parent.resolve(strict=True) != self.preview_root:
                raise OSError("Preview escaped private storage.")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or not 0 < metadata.st_size <= MAX_PREVIEW_BYTES
            ):
                raise OSError("Preview metadata is invalid.")
            chunks = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("Preview changed.")
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        except (OSError, RuntimeError):
            raise JobError("Remote preview is unavailable.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        digest = hashlib.sha256(content).hexdigest()
        if (
            len(content) != preview["size_bytes"]
            or not hmac.compare_digest(digest, preview["sha256"])
        ):
            raise JobError("Remote preview is unavailable.")
        return PreviewContent(
            content=content,
            mime_type=preview["mime_type"],
            sha256=digest,
        )

    def output(self, artifact_id):
        try:
            state = self.state.load()
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        found = []
        for record in state["jobs"].values():
            output = record.get("outputs", {}).get(artifact_id)
            if output is not None:
                found.append(output)
        if len(found) != 1:
            return None
        output = found[0]
        path = Path(output["private_path"])
        size, digest = _hash_open_file(path)
        if (
            size != output["size_bytes"]
            or not hmac.compare_digest(digest, output["sha256"])
        ):
            raise JobError("Remote output is unavailable.")
        return OutputContent(
            path=path,
            mime_type=output["mime_type"],
            size_bytes=size,
            sha256=digest,
        )

    async def close(self):
        tasks = tuple(self._background_tasks)
        self._background_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

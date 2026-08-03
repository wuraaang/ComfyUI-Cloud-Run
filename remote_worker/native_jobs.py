"""Durable passive recording for native ComfyUI prompt execution."""

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

from cloud_run.run_errors import RunErrorCode, RunPhase, SafeRunError
from cloud_run.worker_protocol import (
    CaptureValidationError,
    CompiledCapture,
    FORBIDDEN_KEYS,
    MAX_CAPTURE_BYTES,
    canonical_json,
)
from .comfy import (
    AiohttpComfyHttp,
    ComfyProcessError,
    MAX_NATIVE_WEBSOCKET_BYTES,
)
from .state import WorkerStateError, WorkerStateStore


MAX_JOB_EVENTS = 20_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024 * 1024 * 1024
MAX_NATIVE_TEXT_BYTES = 2 * 1024 * 1024
MAX_NATIVE_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CLOSE_SECONDS = 5
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
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
_PROMPT_FIELDS = {
    "client_id",
    "prompt",
    "extra_data",
    "front",
    "number",
    "partial_execution_targets",
}
_EXTRA_DATA_FIELDS = {
    "comfy_usage_source",
    "extra_pnginfo",
    "preview_method",
}
_SUSPICIOUS_TEXT = re.compile(
    r"(?i)(authorization|bearer|api[_ -]?key|password|secret|token)"
)


class JobError(RuntimeError):
    """A sanitized durable native-job failure."""


class JobValidationError(JobError):
    pass


class JobBusyError(JobError):
    pass


class InvalidOutputError(JobError):
    pass


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


def _safe_tree(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            raise _job_error()
        if isinstance(item, dict):
            for key, child in item.items():
                folded = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    str(key).casefold(),
                ).strip("_")
                components = set(folded.split("_"))
                if (
                    not isinstance(key, str)
                    or key.casefold() in FORBIDDEN_KEYS
                    or components.intersection(
                        {
                            "authorization",
                            "bearer",
                            "cookie",
                            "password",
                            "secret",
                            "token",
                        }
                    )
                    or folded.endswith(
                        (
                            "_api_key",
                            "_access_key",
                            "_access_token",
                            "_signed_url",
                        )
                    )
                ):
                    raise _job_error()
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise _job_error()
        elif item is not None and not isinstance(item, (bool, int, str)):
            raise _job_error()


def _canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


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


def _native_capture(body):
    if (
        not isinstance(body, dict)
        or not {"client_id", "prompt", "extra_data"}.issubset(body)
        or set(body) - _PROMPT_FIELDS
        or not isinstance(body.get("client_id"), str)
        or not _IDENTIFIER.fullmatch(body["client_id"])
        or not isinstance(body.get("extra_data"), dict)
        or set(body["extra_data"]) - _EXTRA_DATA_FIELDS
    ):
        raise _job_error()
    extra_data = body["extra_data"]
    extra_pnginfo = extra_data.get("extra_pnginfo")
    if (
        not isinstance(extra_pnginfo, dict)
        or set(extra_pnginfo) != {"workflow"}
    ):
        raise _job_error()
    if "comfy_usage_source" in extra_data and (
        not isinstance(extra_data["comfy_usage_source"], str)
        or not 0 < len(extra_data["comfy_usage_source"]) <= 200
        or _SUSPICIOUS_TEXT.search(extra_data["comfy_usage_source"])
    ):
        raise _job_error()
    queue_options = {
        key: body[key]
        for key in ("front", "number", "partial_execution_targets")
        if key in body
    }
    if "preview_method" in extra_data:
        queue_options["preview_method"] = extra_data["preview_method"]
    try:
        _safe_tree(body)
        encoded = canonical_json(body)
        if not 0 < len(encoded.encode("utf-8")) <= MAX_CAPTURE_BYTES:
            raise _job_error()
        detached = json.loads(encoded)
        capture = CompiledCapture.from_payload(
            {
                "workflow": detached["extra_data"]["extra_pnginfo"][
                    "workflow"
                ],
                "output": detached["prompt"],
                "queue_options": queue_options,
            }
        )
    except JobValidationError:
        raise
    except (CaptureValidationError, TypeError, ValueError):
        raise _job_error() from None
    return encoded, capture


@dataclass(frozen=True)
class NativePromptIntent:
    job_id: str
    request_id: str
    manifest_digest: str
    client_id: str
    canonical_body: str
    capture: CompiledCapture
    request_digest: str

    @classmethod
    def from_http(cls, *, job_id, request_id, manifest_digest, body):
        if (
            not isinstance(job_id, str)
            or not _IDENTIFIER.fullmatch(job_id)
            or not isinstance(request_id, str)
            or not _IDENTIFIER.fullmatch(request_id)
            or not isinstance(manifest_digest, str)
            or not _HEX_64.fullmatch(manifest_digest)
        ):
            raise _job_error()
        encoded, capture = _native_capture(body)
        material = canonical_json(
            {
                "manifest_digest": manifest_digest,
                "body": json.loads(encoded),
            }
        ).encode("utf-8")
        return cls(
            job_id=job_id,
            request_id=request_id,
            manifest_digest=manifest_digest,
            client_id=json.loads(encoded)["client_id"],
            canonical_body=encoded,
            capture=capture,
            request_digest=hashlib.sha256(material).hexdigest(),
        )

    @property
    def body(self):
        return json.loads(self.canonical_body)


def _validated_native_response(response):
    if (
        not isinstance(response, dict)
        or set(response) != {"prompt_id", "number", "node_errors"}
        or not _canonical_uuid(response.get("prompt_id"))
        or isinstance(response.get("number"), bool)
        or not isinstance(response.get("number"), (int, float))
        or not math.isfinite(response["number"])
        or not isinstance(response.get("node_errors"), dict)
    ):
        raise _job_error()
    try:
        _safe_tree(response)
        encoded = canonical_json(response)
    except (CaptureValidationError, JobValidationError, TypeError, ValueError):
        raise _job_error() from None
    if len(encoded.encode("utf-8")) > MAX_NATIVE_RESPONSE_BYTES:
        raise _job_error()
    return json.loads(encoded)


@dataclass(frozen=True)
class NativePromptReceipt:
    job_id: str
    request_id: str
    request_digest: str
    client_id: str
    prompt_id: str | None
    state: str
    response: dict | None
    should_forward: bool

    @classmethod
    def from_record(cls, record, *, should_forward):
        response = record["native_response"]
        return cls(
            job_id=record["job_id"],
            request_id=record["request_id"],
            request_digest=record["request_digest"],
            client_id=record["client_id"],
            prompt_id=record["prompt_id"],
            state=record["state"],
            response=(dict(response) if response is not None else None),
            should_forward=bool(should_forward),
        )


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


@dataclass(frozen=True)
class JobResult:
    job_id: str
    state: str
    client_id: str
    prompt_id: object
    execution_state: str
    harvest_state: str
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
            execution_state=record.get("execution_state", record["state"]),
            harvest_state=record.get(
                "harvest_state",
                "succeeded" if record["state"] == "succeeded" else "pending",
            ),
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
    execution_state: str
    harvest_state: str
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
            execution_state=record.get("execution_state", record["state"]),
            harvest_state=record.get(
                "harvest_state",
                "succeeded" if record["state"] == "succeeded" else "pending",
            ),
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
                {**event, "data": dict(event["data"])}
                for event in self.events
            ],
            "last_sequence": self.last_sequence,
            "outputs": [dict(item) for item in self.outputs],
            "error": dict(self.error) if self.error is not None else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
            raise InvalidOutputError("Remote output is unavailable.")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_OUTPUT_BYTES:
                raise InvalidOutputError("Remote output is unavailable.")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            size != metadata.st_size
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
            or after.st_ino != metadata.st_ino
        ):
            raise InvalidOutputError("Remote output is unavailable.")
        return size, digest.hexdigest()
    except JobError:
        raise
    except OSError:
        raise InvalidOutputError("Remote output is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _workflow_context(capture, node_id):
    safe_node_id = _safe_identifier(node_id)
    if safe_node_id is None:
        return {}
    context = {"node_id": safe_node_id}
    if capture is None:
        return context
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
                class_type = _safe_identifier(workflow_node.get("type"))
                if class_type is not None:
                    context["class_type"] = class_type
            break
    return context


def execution_error(capture, data, *, validation=False):
    message = data.get("exception_message") if isinstance(data, dict) else None
    is_oom = isinstance(message, str) and "out of memory" in message.casefold()
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


class NativeJobRecorder:
    def __init__(
        self,
        *,
        comfy,
        state,
        preview_root,
        token=None,
        correlation_id=None,
        clock=None,
    ):
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
        self.correlation_id = correlation_id or (
            lambda: "correlation-" + secrets.token_hex(12)
        )
        self.clock = clock or time.time
        self._intents = {}
        self._active_by_client = {}
        self._harvest_tasks = {}
        self._reconcile_orphaned_jobs()

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

    def _record(self, record):
        try:
            self.state.record_job(record["job_id"], record)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        return record

    def _initial_record(self, intent):
        now = self._now()
        return {
            "kind": "job",
            "job_id": intent.job_id,
            "request_id": intent.request_id,
            "request_digest": intent.request_digest,
            "manifest_digest": intent.manifest_digest,
            "state": "queued",
            "client_id": intent.client_id,
            "prompt_id": None,
            "native_response": None,
            "execution_state": "queued",
            "harvest_state": "pending",
            "sequence": 0,
            "events": [],
            "previews": {},
            "outputs": {},
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

    def _append_event(self, record, event_type, data, **changes):
        if len(record["events"]) >= MAX_JOB_EVENTS:
            raise JobError("Remote worker event capacity was exceeded.")
        now = self._now()
        event = {
            "sequence": record["sequence"] + 1,
            "type": event_type,
            "data": data,
            "created_at": now,
        }
        return self._record(
            {
                **record,
                **changes,
                "sequence": event["sequence"],
                "events": [*record["events"], event],
                "updated_at": now,
            }
        )

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

    def _reconcile_orphaned_jobs(self):
        try:
            state = self.state.load()
        except WorkerStateError:
            raise ValueError("Remote worker job state is invalid.") from None
        if not state["claimed"]:
            return
        for job_id in sorted(state["jobs"]):
            record = self.state.job(job_id)
            if record["execution_state"] == "succeeded":
                continue
            if record["state"] not in {"queued", "running"}:
                continue
            error = {
                "code": "worker_restarted",
                "message": (
                    "Remote execution was interrupted by a worker restart."
                ),
            }
            self._append_event(
                record,
                "execution_interrupted",
                dict(error),
                state="interrupted",
                execution_state="interrupted",
                harvest_state="pending",
                error=error,
            )

    def _resume_harvest_tasks(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for job_id, record in self.state.load()["jobs"].items():
            if (
                record.get("execution_state") == "succeeded"
                and record.get("harvest_state") == "running"
                and job_id not in self._harvest_tasks
            ):
                self._schedule_harvest(job_id, loop=loop)

    def begin(self, intent):
        if not isinstance(intent, NativePromptIntent):
            raise _job_error()
        try:
            current = self.state.load()
            existing = self.state.job(intent.job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        installed_digest = current["installed"].get("manifest_digest")
        if (
            not isinstance(installed_digest, str)
            or not hmac.compare_digest(installed_digest, intent.manifest_digest)
        ):
            raise _job_error()
        for job_id, record in current["jobs"].items():
            if record.get("request_id") == intent.request_id and job_id != intent.job_id:
                raise _job_error()
        if existing is not None:
            if (
                existing["request_id"] != intent.request_id
                or existing["client_id"] != intent.client_id
                or not hmac.compare_digest(
                    existing["manifest_digest"], intent.manifest_digest
                )
                or not hmac.compare_digest(
                    existing["request_digest"], intent.request_digest
                )
            ):
                raise _job_error()
            self._intents[intent.job_id] = intent
            self._resume_harvest_tasks()
            return NativePromptReceipt.from_record(
                existing,
                should_forward=False,
            )
        record = self._initial_record(intent)
        self._record(record)
        self._intents[intent.job_id] = intent
        return NativePromptReceipt.from_record(record, should_forward=True)

    def bind_prompt(self, job_id, response):
        response = _validated_native_response(response)
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            raise _job_error()
        existing = record["native_response"]
        if existing is not None:
            if canonical_json(existing) != canonical_json(response):
                raise _job_error()
            return NativePromptReceipt.from_record(
                record,
                should_forward=False,
            )
        if record["prompt_id"] not in {None, response["prompt_id"]}:
            raise _job_error()
        if record["execution_state"] == "queued":
            state = "running"
            execution_state = "running"
        else:
            state = record["state"]
            execution_state = record["execution_state"]
        updated = self._record(
            {
                **record,
                "state": state,
                "prompt_id": response["prompt_id"],
                "native_response": response,
                "execution_state": execution_state,
                "updated_at": self._now(),
            }
        )
        self._active_by_client.setdefault(record["client_id"], job_id)
        return NativePromptReceipt.from_record(updated, should_forward=False)

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
        preview_id = self._new_token(record["previews"], namespace="previews")
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
            for source in ("value", "max"):
                number = _safe_number(item.get(source))
                if number is not None:
                    safe[source] = number
            safe_nodes.append(safe)
        return {"nodes": safe_nodes}

    def _sanitize_event(self, capture, record, event):
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
            exec_info = status.get("exec_info") if isinstance(status, dict) else None
            remaining = (
                _safe_number(exec_info.get("queue_remaining"))
                if isinstance(exec_info, dict)
                else None
            )
            public = {"queue_remaining": remaining} if remaining is not None else {}
        elif event_type == "progress":
            public = {}
            for key in ("value", "max", "total"):
                number = _safe_number(data.get(key))
                if number is not None:
                    public[key] = number
            node_id = _safe_identifier(data.get("node", data.get("node_id")))
            if node_id is not None:
                public["node_id"] = node_id
        elif event_type == "progress_text":
            public = {}
            node_id = _safe_identifier(data.get("node", data.get("node_id")))
            text = _safe_text(data.get("text"))
            if node_id is not None:
                public["node_id"] = node_id
            if text is not None:
                public["text"] = text
        elif event_type == "progress_state":
            public = self._sanitize_progress_state(data)
        elif event_type in {"executing", "executed"}:
            public = {}
            node_id = _safe_identifier(data.get("node", data.get("node_id")))
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
            if (
                data.get("code") == "validation_failed"
                and data.get("message")
                == "Remote ComfyUI rejected the compiled prompt."
            ):
                error = {
                    "code": "validation_failed",
                    "message": (
                        "Remote ComfyUI rejected the compiled prompt."
                    ),
                    **_workflow_context(
                        capture,
                        data.get("node_id", data.get("node")),
                    ),
                }
            else:
                error = execution_error(capture, data)
            return record, dict(error), error
        elif event_type == "execution_interrupted":
            error = {
                "code": "execution_interrupted",
                "message": "Remote execution was interrupted.",
                **_workflow_context(
                    capture,
                    data.get("node_id", data.get("node")),
                ),
            }
            return record, dict(error), error
        else:
            timestamp = _safe_number(data.get("timestamp"))
            public = {"timestamp": timestamp} if timestamp is not None else {}
        return record, public, None

    def _record_for_event(self, client_id, event):
        if not isinstance(client_id, str) or not _IDENTIFIER.fullmatch(client_id):
            raise _job_error()
        data = event.get("data") if isinstance(event, dict) else None
        prompt_id = data.get("prompt_id") if isinstance(data, dict) else None
        if prompt_id is not None and not _canonical_uuid(prompt_id):
            return None
        state = self.state.load()
        candidates = [
            record
            for record in state["jobs"].values()
            if record.get("client_id") == client_id
            and record.get("state") in {"queued", "running"}
        ]
        if prompt_id is not None:
            exact = [record for record in candidates if record["prompt_id"] == prompt_id]
            if len(exact) == 1:
                self._active_by_client[client_id] = exact[0]["job_id"]
                return exact[0]
            unbound = sorted(
                (
                    record
                    for record in candidates
                    if record["prompt_id"] is None
                ),
                key=lambda item: (item["created_at"], item["job_id"]),
            )
            if unbound:
                record = self._record(
                    {
                        **unbound[0],
                        "prompt_id": prompt_id,
                        "state": "running",
                        "execution_state": "running",
                        "updated_at": self._now(),
                    }
                )
                self._active_by_client[client_id] = record["job_id"]
                return record
            return None
        active_job_id = self._active_by_client.get(client_id)
        if active_job_id is not None:
            for record in candidates:
                if record["job_id"] == active_job_id:
                    return record
        bound = [record for record in candidates if record["prompt_id"] is not None]
        return bound[0] if len(bound) == 1 else None

    def observe_event(self, job_id, event, *, schedule_harvest=True):
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            raise _job_error()
        return self._observe_record(
            record,
            event,
            schedule_harvest=schedule_harvest,
        )

    def _observe_record(self, record, event, *, schedule_harvest=True):
        intent = self._intents.get(record["job_id"])
        capture = intent.capture if intent is not None else None
        updated, public, error = self._sanitize_event(capture, record, event)
        if public is None:
            return None
        event_type = event["type"]
        changes = {}
        if event_type in {"execution_start", "executing"} and record[
            "execution_state"
        ] == "queued":
            changes.update(state="running", execution_state="running")
        if event_type == "execution_success":
            changes.update(
                state="running",
                execution_state="succeeded",
                harvest_state="running",
                error=None,
            )
        elif event_type == "execution_error":
            changes.update(
                state="failed",
                execution_state="failed",
                harvest_state="pending",
                error=error,
            )
        elif event_type == "execution_interrupted":
            changes.update(
                state="interrupted",
                execution_state="interrupted",
                harvest_state="pending",
                error=error,
            )
        record = self._append_event(updated, event_type, public, **changes)
        self._active_by_client[record["client_id"]] = record["job_id"]
        if event_type == "execution_success" and schedule_harvest:
            self._schedule_harvest(record["job_id"])
        elif event_type in {"execution_error", "execution_interrupted"}:
            self._active_by_client.pop(record["client_id"], None)
        return None

    def observe_text(self, client_id, frame):
        if (
            not isinstance(frame, str)
            or len(frame.encode("utf-8")) > MAX_NATIVE_TEXT_BYTES
        ):
            raise _job_error()
        try:
            payload = json.loads(
                frame,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise _job_error() from None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("type"), str)
            or not isinstance(payload.get("data"), dict)
        ):
            return None
        event = {"type": payload["type"], "data": dict(payload["data"])}
        record = self._record_for_event(client_id, event)
        if record is not None:
            self._observe_record(record, event)
        return None

    def observe_binary(self, client_id, frame):
        if (
            not isinstance(frame, bytes)
            or not 4 < len(frame) <= MAX_NATIVE_WEBSOCKET_BYTES
        ):
            raise _job_error()
        try:
            event = AiohttpComfyHttp._binary_event(bytes(frame))
        except ComfyProcessError:
            raise _job_error() from None
        if event is None:
            return None
        record = self._record_for_event(client_id, event)
        if record is not None:
            self._observe_record(record, event)
        return None

    def record_synchronization_failure(self, client_id):
        if not isinstance(client_id, str) or not _IDENTIFIER.fullmatch(client_id):
            raise _job_error()
        try:
            correlation = self.correlation_id()
        except Exception:
            correlation = None
        if not isinstance(correlation, str) or not _IDENTIFIER.fullmatch(
            correlation
        ):
            correlation = "correlation-unavailable"
        transaction_id = "native-sync-" + correlation
        if len(transaction_id) > 200:
            transaction_id = "native-sync-" + hashlib.sha256(
                correlation.encode("utf-8")
            ).hexdigest()
        try:
            self.state.record_transaction(
                transaction_id,
                {
                    "kind": "native_sync_error",
                    "transaction_id": transaction_id,
                    "client_id": client_id,
                    "code": "synchronization_error",
                    "phase": "synchronization",
                    "message": "Native event recording was interrupted.",
                    "correlation_id": correlation,
                    "updated_at": self._now(),
                },
            )
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        return transaction_id

    def fail_native_submission(self, job_id, *, validation):
        if not isinstance(validation, bool):
            raise _job_error()
        error = self._safe_run_error(
            code=(
                RunErrorCode.VALIDATION
                if validation
                else RunErrorCode.SYNCHRONIZATION
            ),
            phase=(
                RunPhase.EXECUTION
                if validation
                else RunPhase.SYNCHRONIZATION
            ),
            message=(
                "Remote ComfyUI rejected the compiled prompt."
                if validation
                else "Native prompt submission failed."
            ),
            retryable=not validation,
        )
        return self.fail(job_id, error, execution=True)

    def _history_outputs(self, prompt_id, history):
        if (
            not isinstance(history, dict)
            or set(history) != {prompt_id}
            or not isinstance(history[prompt_id], dict)
        ):
            raise JobError("Remote execution history is unavailable.")
        outputs = history[prompt_id].get("outputs")
        if not isinstance(outputs, dict) or len(outputs) > 100_000:
            raise JobError("Remote execution history is unavailable.")
        records = {}
        seen_paths = set()
        for raw_node_id in sorted(outputs, key=str):
            node_id = _safe_identifier(raw_node_id)
            node_outputs = outputs[raw_node_id]
            if node_id is None or not isinstance(node_outputs, dict):
                raise InvalidOutputError("Remote output descriptor is invalid.")
            for category in sorted(node_outputs):
                descriptors = node_outputs[category]
                if not isinstance(descriptors, list):
                    continue
                for descriptor in descriptors:
                    if (
                        not isinstance(descriptor, dict)
                        or not {"filename", "subfolder", "type"}.issubset(
                            descriptor
                        )
                    ):
                        raise InvalidOutputError(
                            "Remote output descriptor is invalid."
                        )
                    minimal = {
                        "filename": descriptor["filename"],
                        "subfolder": descriptor["subfolder"],
                        "type": descriptor["type"],
                    }
                    if descriptor["type"] == "temp":
                        continue
                    if descriptor["type"] != "output":
                        raise InvalidOutputError(
                            "Remote output descriptor is invalid."
                        )
                    try:
                        path = Path(self.comfy.output_path(minimal))
                    except Exception:
                        raise InvalidOutputError(
                            "Remote output descriptor is invalid."
                        ) from None
                    key = str(path)
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    size, digest = _hash_open_file(path)
                    artifact_id = self._new_token(records, namespace="outputs")
                    mime_method = getattr(self.comfy, "output_mime_type", None)
                    mime_type = (
                        mime_method(path)
                        if callable(mime_method)
                        else "application/octet-stream"
                    )
                    if not isinstance(mime_type, str) or len(mime_type) > 100:
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

    def _safe_run_error(self, *, code, phase, message, retryable):
        try:
            correlation = self.correlation_id()
            return SafeRunError(
                code=code,
                phase=phase,
                message=message,
                correlation_id=correlation,
                node_id=None,
                retryable=retryable,
            )
        except (TypeError, ValueError):
            return SafeRunError(
                code=RunErrorCode.INTERNAL,
                phase=RunPhase.INTERNAL,
                message="Remote job recording failed.",
                correlation_id="correlation-unavailable",
                node_id=None,
                retryable=True,
            )

    @staticmethod
    def _error_record(error):
        if isinstance(error, SafeRunError):
            return {
                "code": error.code.value,
                "phase": error.phase.value,
                "message": error.message,
                "correlation_id": error.correlation_id,
                "retryable": error.retryable,
                **({"node_id": error.node_id} if error.node_id else {}),
            }
        if (
            isinstance(error, dict)
            and isinstance(error.get("code"), str)
            and _IDENTIFIER.fullmatch(error["code"])
            and isinstance(error.get("message"), str)
            and _safe_text(error["message"], maximum=1024) == error["message"]
        ):
            return dict(error)
        raise _job_error()

    def fail(self, job_id, error, *, execution=False, interrupted=False):
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            raise _job_error()
        safe = self._error_record(error)
        if execution:
            state = "interrupted" if interrupted else "failed"
            execution_state = "interrupted" if interrupted else "failed"
            harvest_state = "pending"
        else:
            state = "failed"
            execution_state = record["execution_state"]
            harvest_state = (
                "failed"
                if execution_state == "succeeded"
                else record["harvest_state"]
            )
        updated = self._record(
            {
                **record,
                "state": state,
                "execution_state": execution_state,
                "harvest_state": harvest_state,
                "error": safe,
                "updated_at": self._now(),
            }
        )
        return JobResult.from_record(updated)

    async def _harvest_once(self, job_id):
        record = self.state.job(job_id)
        if record is None:
            raise _job_error()
        if record["execution_state"] != "succeeded":
            raise _job_error()
        if record["harvest_state"] == "succeeded":
            return JobResult.from_record(record)
        record = self._record(
            {
                **record,
                "state": "running",
                "harvest_state": "running",
                "error": None,
                "updated_at": self._now(),
            }
        )
        try:
            history = await self.comfy.history(record["prompt_id"])
            outputs = self._history_outputs(record["prompt_id"], history)
        except InvalidOutputError:
            error = self._safe_run_error(
                code=RunErrorCode.INVALID_OUTPUT,
                phase=RunPhase.HARVEST,
                message="Remote output descriptor is invalid.",
                retryable=True,
            )
            return self.fail(job_id, error)
        except (JobError, ComfyProcessError):
            error = self._safe_run_error(
                code=RunErrorCode.HARVEST,
                phase=RunPhase.HARVEST,
                message="Remote output harvesting failed.",
                retryable=True,
            )
            return self.fail(job_id, error)
        latest = self.state.job(job_id)
        updated = self._record(
            {
                **latest,
                "state": "succeeded",
                "execution_state": "succeeded",
                "harvest_state": "succeeded",
                "outputs": outputs,
                "error": None,
                "updated_at": self._now(),
            }
        )
        self._active_by_client.pop(updated["client_id"], None)
        return JobResult.from_record(updated)

    async def finish(self, job_id):
        existing = self._harvest_tasks.get(job_id)
        current = asyncio.current_task()
        if existing is not None and existing is not current:
            await asyncio.shield(existing)
            result = self.job(job_id)
            if result is None:
                raise _job_error()
            return result
        return await self._harvest_once(job_id)

    def _background_failure(self, job_id):
        error = self._safe_run_error(
            code=RunErrorCode.INTERNAL,
            phase=RunPhase.HARVEST,
            message="Remote job recording failed.",
            retryable=True,
        )
        try:
            self.fail(job_id, error)
        except (JobError, WorkerStateError):
            pass

    def _schedule_harvest(self, job_id, *, loop=None):
        if job_id in self._harvest_tasks:
            return self._harvest_tasks[job_id]
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return None
        task = loop.create_task(self._harvest_once(job_id))
        self._harvest_tasks[job_id] = task

        def complete(done):
            self._harvest_tasks.pop(job_id, None)
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                self._background_failure(job_id)

        task.add_done_callback(complete)
        return task

    def job(self, job_id):
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        return JobResult.from_record(record) if record is not None else None

    def snapshot(self, job_id, cursor=0):
        if (
            isinstance(cursor, bool)
            or not isinstance(cursor, int)
            or cursor < 0
        ):
            raise JobValidationError("Remote job event cursor is invalid.")
        self._resume_harvest_tasks()
        try:
            record = self.state.job(job_id)
        except WorkerStateError:
            raise JobError("Remote worker job state is unavailable.") from None
        if record is None:
            return None
        return JobSnapshot.from_record(record, cursor)

    def events(self, job_id, after_sequence=0):
        snapshot = self.snapshot(job_id, after_sequence)
        return None if snapshot is None else snapshot.events

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
        tasks = tuple(self._harvest_tasks.values())
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=MAX_CLOSE_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

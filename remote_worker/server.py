"""Allowlisted loopback-only HTTP boundary for one Remote Worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time

from cloud_run.manifest import ArtifactSpec, validate_dependency
from cloud_run.worker_protocol import (
    NonceCache,
    PROTOCOL_VERSION,
    ProtocolAuthenticationError,
    verify_request,
)
from .state import (
    WorkerAlreadyClaimed,
    WorkerSessionMismatch,
    WorkerStateError,
    WorkerStateStore,
)
from .provision import (
    ProvisionError,
    ProvisionResult,
    ProvisionStalled,
    UploadsRequired,
    manifest_upload_artifacts,
    parse_manifest_request,
)
from .transfers import TransferError
from .deadline import (
    DeadlineEnforcementError,
    DeadlineValidationError,
    parse_deadline_update,
)
from .jobs import (
    JobBusyError,
    JobError,
    JobResult,
    JobValidationError,
    parse_job_request,
)


WORKER_BIND_HOST = "127.0.0.1"
WORKER_BIND_PORT = 8766
MAX_CLAIM_BYTES = 4096
_IDENTIFIER_PART = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}"
_CLAIM_FIELDS = {
    "protocol_version",
    "session_id",
    "session_secret_hex",
}
_ROUTES = (
    ("GET", "/worker/v1/health"),
    ("POST", "/worker/v1/claim"),
    ("POST", "/worker/v1/manifests"),
    ("GET", "/worker/v1/transactions/{transaction_id}"),
    ("PUT", "/worker/v1/artifacts/{artifact_id}"),
    ("GET", "/worker/v1/artifacts/{artifact_id}"),
    ("POST", "/worker/v1/jobs"),
    ("GET", "/worker/v1/jobs/{job_id}"),
    ("GET", "/worker/v1/jobs/{job_id}/events"),
    (
        "GET",
        "/worker/v1/jobs/{job_id}/previews/{preview_id}",
    ),
    ("PUT", "/worker/v1/deadline"),
)


def _route_pattern(path):
    position = 0
    parts = []
    for match in re.finditer(r"\{[a-z_]+\}", path):
        parts.append(re.escape(path[position : match.start()]))
        name = match.group(0)[1:-1]
        parts.append(f"(?P<{name}>{_IDENTIFIER_PART})")
        position = match.end()
    parts.append(re.escape(path[position:]))
    return re.compile("".join(parts) + r"\Z")


_ROUTE_PATTERNS = tuple(
    (method, path, _route_pattern(path)) for method, path in _ROUTES
)


def worker_route_set():
    return set(_ROUTES)


@dataclass(frozen=True)
class WorkerResponse:
    status: int
    payload: object
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerFile:
    path: Path
    start: int
    end: int
    size_bytes: int
    sha256: str
    mime_type: str


def _response(status, payload, *, headers=None):
    return WorkerResponse(
        status=int(status),
        payload=payload,
        headers=dict(headers or {}),
    )


def _error(status, message):
    return _response(status, {"error": message})


def _headers(request):
    raw = getattr(request, "headers", {})
    try:
        return {
            str(key).casefold(): str(value)
            for key, value in raw.items()
        }
    except (AttributeError, TypeError, ValueError):
        return {}


def _has_boundary(request):
    if hasattr(request, "boundary_authenticated"):
        return getattr(request, "boundary_authenticated") is True
    return (
        _headers(request).get("x-cloud-run-boundary")
        == "authenticated"
    )


async def _body(request):
    value = getattr(request, "body", None)
    if isinstance(value, bytes):
        return value
    reader = getattr(request, "read", None)
    if not callable(reader):
        raise ValueError("Invalid worker request body.")
    value = await reader()
    if not isinstance(value, bytes):
        raise ValueError("Invalid worker request body.")
    return value


def _auth_envelope(request):
    provided = getattr(request, "auth_envelope", None)
    if provided is not None:
        return provided
    headers = _headers(request)
    try:
        timestamp = int(headers["x-cloud-run-timestamp"])
    except (KeyError, TypeError, ValueError):
        timestamp = None
    return {
        "protocol_version": headers.get(
            "x-cloud-run-protocol-version"
        ),
        "timestamp": timestamp,
        "nonce": headers.get("x-cloud-run-nonce"),
        "signature": headers.get("x-cloud-run-signature"),
    }


def _authentication_path(request):
    path_qs = getattr(request, "path_qs", None)
    if isinstance(path_qs, str) and path_qs.startswith("/"):
        return path_qs
    return getattr(request, "path", None)


def _route_for(method, path):
    if not isinstance(method, str) or not isinstance(path, str):
        return None
    normalized = method.upper()
    for route_method, route, pattern in _ROUTE_PATTERNS:
        if route_method != normalized:
            continue
        match = pattern.fullmatch(path)
        if match is not None:
            return route, match.groupdict()
    return None


def _claim_payload(body):
    if not isinstance(body, bytes) or len(body) > MAX_CLAIM_BYTES:
        raise ValueError("Invalid worker claim.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Invalid worker claim.") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != _CLAIM_FIELDS
        or payload.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(payload.get("session_id"), str)
        or not re.fullmatch(_IDENTIFIER_PART, payload["session_id"])
        or not isinstance(payload.get("session_secret_hex"), str)
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            payload["session_secret_hex"],
        )
    ):
        raise ValueError("Invalid worker claim.")
    return payload


class WorkerApplication:
    def __init__(
        self,
        *,
        state_path,
        expected_session_id=None,
        clock=None,
        nonce_cache=None,
        transfer_manager=None,
        upload_artifacts=(),
        provisioner=None,
        job_manager=None,
        deadline_watchdog=None,
    ):
        self.state = WorkerStateStore(
            state_path,
            expected_session_id=expected_session_id,
        )
        self.clock = clock or time.time
        self.nonce_cache = (
            nonce_cache if nonce_cache is not None else NonceCache()
        )
        self.transfer_manager = transfer_manager
        self.provisioner = provisioner
        self.job_manager = job_manager
        self.deadline_watchdog = deadline_watchdog
        self.upload_artifacts = {}
        self._manifest_lock = None
        self._manifest_lock_loop = None
        self.register_upload_artifacts(upload_artifacts)

    def _manifest_request_lock(self):
        loop = asyncio.get_running_loop()
        if (
            self._manifest_lock is None
            or self._manifest_lock_loop is not loop
        ):
            self._manifest_lock = asyncio.Lock()
            self._manifest_lock_loop = loop
        return self._manifest_lock

    def register_upload_artifacts(self, artifacts):
        catalog = {}
        for artifact in artifacts:
            try:
                validate_dependency(artifact)
            except (TypeError, ValueError):
                raise ValueError("Invalid worker upload artifact.") from None
            if (
                not isinstance(artifact, ArtifactSpec)
                or artifact.source.kind != "local-upload"
                or artifact.artifact_id in catalog
            ):
                raise ValueError("Invalid worker upload artifact.")
            catalog[artifact.artifact_id] = artifact
        if catalog and self.transfer_manager is None:
            raise ValueError("Worker upload manager is required.")
        if catalog:
            try:
                destinations = {
                    self.transfer_manager.destination_path(artifact)
                    for artifact in catalog.values()
                }
                parts = {
                    self.transfer_manager.part_path(artifact)
                    for artifact in catalog.values()
                }
            except TransferError:
                raise ValueError("Invalid worker upload artifact.") from None
            if (
                len(destinations) != len(catalog)
                or len(parts) != len(catalog)
                or destinations.intersection(parts)
            ):
                raise ValueError("Invalid worker upload artifact.")
        self.upload_artifacts = catalog
        return tuple(sorted(catalog))

    async def _health(self):
        try:
            state = self.state.load()
        except WorkerStateError:
            return _error(503, "Worker state is unavailable.")
        return _response(
            200,
            {
                "protocol_version": PROTOCOL_VERSION,
                "claimed": state["claimed"],
            },
        )

    async def _claim(self, request):
        try:
            payload = _claim_payload(await _body(request))
            state = self.state.claim(
                session_id=payload["session_id"],
                session_secret_hex=payload["session_secret_hex"],
            )
        except (WorkerAlreadyClaimed, WorkerSessionMismatch):
            return _error(409, "Worker claim was rejected.")
        except ValueError:
            return _error(400, "Worker claim was rejected.")
        except WorkerStateError:
            return _error(503, "Worker state is unavailable.")
        return _response(
            200,
            {
                "protocol_version": PROTOCOL_VERSION,
                "session_id": state["session_id"],
                "claimed": True,
            },
        )

    async def _authenticate(self, request, body):
        try:
            secret = self.state.secret_bytes()
            verify_request(
                secret,
                request.method,
                _authentication_path(request),
                body,
                _auth_envelope(request),
                now=self.clock(),
                seen_nonces=self.nonce_cache,
            )
        except (WorkerStateError, ProtocolAuthenticationError):
            return False
        return True

    @staticmethod
    def _event_cursor(request):
        query = getattr(request, "query", {})
        try:
            keys = set(query)
        except (TypeError, ValueError):
            raise JobValidationError(
                "Remote job event cursor is invalid."
            ) from None
        if not keys:
            return 0
        if keys != {"after_sequence"}:
            raise JobValidationError(
                "Remote job event cursor is invalid."
            )
        try:
            values = query.getall("after_sequence")
        except AttributeError:
            values = [query.get("after_sequence")]
        if len(values) != 1 or not isinstance(values[0], str):
            raise JobValidationError(
                "Remote job event cursor is invalid."
            )
        if not re.fullmatch(r"0|[1-9][0-9]{0,19}", values[0]):
            raise JobValidationError(
                "Remote job event cursor is invalid."
            )
        return int(values[0])

    @staticmethod
    def _file_range(request, size_bytes):
        value = _headers(request).get("range")
        if value is None:
            return 200, 0, size_bytes - 1
        match = re.fullmatch(
            r"bytes=(0|[1-9][0-9]*)-(0|[1-9][0-9]*)?",
            value,
        )
        if match is None:
            raise JobValidationError(
                "Remote output range is invalid."
            )
        start = int(match.group(1))
        end = (
            int(match.group(2))
            if match.group(2) is not None
            else size_bytes - 1
        )
        if start >= size_bytes or end < start or end >= size_bytes:
            raise JobValidationError(
                "Remote output range is invalid."
            )
        return 206, start, end

    async def _upload_artifact(self, request, artifact_id, body):
        artifact = self.upload_artifacts.get(artifact_id)
        if artifact is None:
            return _error(404, "Worker artifact was not found.")
        content_range = _headers(request).get("content-range")
        try:
            result = await self.transfer_manager.upload(
                artifact,
                content_range=content_range,
                body=body,
            )
            self.state.record_artifact_transfer(
                artifact_id=result.artifact_id,
                transfer_state=result.state,
                offset=result.next_offset,
                size_bytes=result.size_bytes,
                sha256=result.sha256,
            )
        except TransferError:
            return _error(409, "Artifact transfer was rejected.")
        except WorkerStateError:
            return _error(503, "Worker state is unavailable.")
        return _response(
            200,
            {
                "artifact_id": result.artifact_id,
                "state": result.state,
                "next_offset": result.next_offset,
                "size_bytes": result.size_bytes,
                "sha256": result.sha256,
            },
        )

    async def _apply_manifest_locked(self, body):
        if self.provisioner is None:
            return _error(501, "Worker route is not implemented.")
        try:
            desired, required, source_urls = parse_manifest_request(body)
            upload_artifacts = manifest_upload_artifacts(desired)
            if upload_artifacts:
                self.register_upload_artifacts(upload_artifacts)
        except (ProvisionError, ValueError):
            return _error(400, "Worker manifest was rejected.")
        try:
            result = await self.provisioner.apply_manifest(
                desired,
                required_class_types=required,
                source_urls=source_urls,
            )
        except UploadsRequired as required_uploads:
            result = self.provisioner.transaction(
                "provision-" + desired.digest
            )
            if result is None:
                return _error(503, "Worker transaction is unavailable.")
            payload = result.payload()
            payload["required_uploads"] = list(
                required_uploads.artifact_ids
            )
            return _response(202, payload)
        except ProvisionStalled:
            return _error(409, "Worker provisioning stalled.")
        except ProvisionError:
            return _error(409, "Worker provisioning was rejected.")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return _error(503, "Worker transaction is unavailable.")
        if not isinstance(result, ProvisionResult):
            return _error(503, "Worker transaction is unavailable.")
        return _response(200, result.payload())

    async def _apply_manifest(self, body):
        async with self._manifest_request_lock():
            return await self._apply_manifest_locked(body)

    async def _transaction(self, transaction_id):
        if self.provisioner is None:
            return _error(501, "Worker route is not implemented.")
        try:
            result = self.provisioner.transaction(transaction_id)
        except ProvisionError:
            return _error(503, "Worker transaction is unavailable.")
        except Exception:
            return _error(503, "Worker transaction is unavailable.")
        if result is None:
            return _error(404, "Worker transaction was not found.")
        if not isinstance(result, ProvisionResult):
            return _error(503, "Worker transaction is unavailable.")
        return _response(200, result.payload())

    async def _start_job(self, body):
        if self.job_manager is None:
            return _error(501, "Worker route is not implemented.")
        try:
            request = parse_job_request(body)
            result = await self.job_manager.start(request)
        except JobBusyError:
            return _error(409, "Worker is already executing a job.")
        except JobValidationError:
            return _error(400, "Worker job was rejected.")
        except JobError:
            return _error(503, "Worker job is unavailable.")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return _error(503, "Worker job is unavailable.")
        if not isinstance(result, JobResult):
            return _error(503, "Worker job is unavailable.")
        return _response(
            (
                202
                if result.state in {"queued", "running"}
                else 200
            ),
            result.public_payload(),
        )

    async def _job(self, job_id):
        if self.job_manager is None:
            return _error(501, "Worker route is not implemented.")
        try:
            result = self.job_manager.job(job_id)
        except JobError:
            return _error(503, "Worker job is unavailable.")
        if result is None:
            return _error(404, "Worker job was not found.")
        if not isinstance(result, JobResult):
            return _error(503, "Worker job is unavailable.")
        return _response(200, result.public_payload())

    async def _job_events(self, request, job_id):
        if self.job_manager is None:
            return _error(501, "Worker route is not implemented.")
        try:
            cursor = self._event_cursor(request)
            events = self.job_manager.events(job_id, cursor)
        except JobValidationError:
            return _error(400, "Worker event cursor was rejected.")
        except JobError:
            return _error(503, "Worker job events are unavailable.")
        if events is None:
            return _error(404, "Worker job was not found.")
        return _response(
            200,
            {
                "job_id": job_id,
                "events": [dict(event) for event in events],
                "last_sequence": (
                    events[-1]["sequence"] if events else cursor
                ),
            },
        )

    async def _job_preview(self, job_id, preview_id):
        if self.job_manager is None:
            return _error(501, "Worker route is not implemented.")
        try:
            preview = self.job_manager.preview(job_id, preview_id)
        except JobError:
            return _error(503, "Worker preview is unavailable.")
        if preview is None:
            return _error(404, "Worker preview was not found.")
        return _response(
            200,
            preview.content,
            headers={
                "Content-Type": preview.mime_type,
                "Content-Length": str(len(preview.content)),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    async def _job_output(self, request, artifact_id):
        if self.job_manager is None:
            return _error(501, "Worker route is not implemented.")
        try:
            output = self.job_manager.output(artifact_id)
            if output is None:
                return _error(404, "Worker output was not found.")
            status, start, end = self._file_range(
                request,
                output.size_bytes,
            )
        except JobValidationError:
            return _error(416, "Worker output range was rejected.")
        except JobError:
            return _error(503, "Worker output is unavailable.")
        headers = {
            "Content-Type": output.mime_type,
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
            "ETag": '"' + output.sha256 + '"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        }
        if status == 206:
            headers["Content-Range"] = (
                f"bytes {start}-{end}/{output.size_bytes}"
            )
        return _response(
            status,
            WorkerFile(
                path=output.path,
                start=start,
                end=end,
                size_bytes=output.size_bytes,
                sha256=output.sha256,
                mime_type=output.mime_type,
            ),
            headers=headers,
        )

    async def _deadline(self, body):
        if self.deadline_watchdog is None:
            return _error(501, "Worker route is not implemented.")
        try:
            payload = parse_deadline_update(body)
            result = self.deadline_watchdog.update(payload)
            self.deadline_watchdog.start()
        except DeadlineValidationError:
            return _error(400, "Worker deadline update was rejected.")
        except DeadlineEnforcementError:
            return _error(503, "Worker deadline state is unavailable.")
        except Exception:
            return _error(503, "Worker deadline state is unavailable.")
        return _response(200, result.payload())

    async def handle(self, request):
        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        matched_route = _route_for(method, path)
        if matched_route is None:
            return _error(404, "Worker route was not found.")
        route, parameters = matched_route
        if not _has_boundary(request):
            return _error(401, "Worker request authentication failed.")
        if route == "/worker/v1/health":
            return await self._health()
        if route == "/worker/v1/claim":
            return await self._claim(request)
        try:
            body = await _body(request)
        except ValueError:
            return _error(401, "Worker request authentication failed.")
        if not await self._authenticate(request, body):
            return _error(401, "Worker request authentication failed.")
        if route == "/worker/v1/manifests":
            return await self._apply_manifest(body)
        if route == "/worker/v1/transactions/{transaction_id}":
            return await self._transaction(
                parameters["transaction_id"]
            )
        if route == "/worker/v1/artifacts/{artifact_id}" and (
            str(method).upper() == "PUT"
        ):
            return await self._upload_artifact(
                request,
                parameters["artifact_id"],
                body,
            )
        if route == "/worker/v1/artifacts/{artifact_id}":
            return await self._job_output(
                request,
                parameters["artifact_id"],
            )
        if route == "/worker/v1/jobs":
            return await self._start_job(body)
        if route == "/worker/v1/jobs/{job_id}":
            return await self._job(parameters["job_id"])
        if route == "/worker/v1/jobs/{job_id}/events":
            return await self._job_events(
                request,
                parameters["job_id"],
            )
        if route == (
            "/worker/v1/jobs/{job_id}/previews/{preview_id}"
        ):
            return await self._job_preview(
                parameters["job_id"],
                parameters["preview_id"],
            )
        if route == "/worker/v1/deadline":
            return await self._deadline(body)
        return _error(501, "Worker route is not implemented.")

    async def close(self):
        if self.job_manager is not None:
            close = getattr(self.job_manager, "close", None)
            if callable(close):
                await close()
        if self.deadline_watchdog is not None:
            close = getattr(self.deadline_watchdog, "close", None)
            if callable(close):
                await close()

"""Authenticated local client for one claimed Remote Worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
import stat
import uuid
from urllib.parse import urlsplit

from .manifest import PROTOCOL_VERSION, ProfileFileSpec, validate_dependency
from .vast import derive_base_url
from .worker_protocol import (
    is_boundary_token,
    is_worker_session_id,
    native_request_material,
    sign_request,
)


MAX_WORKER_JSON_BYTES = 16 * 1024 * 1024
MAX_WORKER_NATIVE_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_WORKER_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_WORKER_ERROR_BYTES = 64 * 1024
MAX_WORKER_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_WORKER_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_CONTENT_RANGE = re.compile(
    r"bytes (0|[1-9][0-9]*)-(0|[1-9][0-9]*)/"
    r"(0|[1-9][0-9]*)"
)
_MIME_TYPE = re.compile(r"[a-z0-9.+-]+/[a-z0-9.+-]+")
_SNAPSHOT_FIELDS = {
    "job_id",
    "state",
    "prompt_id",
    "events",
    "last_sequence",
    "outputs",
    "error",
    "created_at",
    "updated_at",
}
_SNAPSHOT_STATES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "interrupted",
}
_SNAPSHOT_EVENT_TYPES = {
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
_SNAPSHOT_OUTPUT_FIELDS = {
    "artifact_id",
    "node_id",
    "filename",
    "subfolder",
    "mime_type",
    "size_bytes",
    "sha256",
}
_SNAPSHOT_ERROR_FIELDS = {
    "code",
    "message",
    "node_id",
    "class_type",
    "title",
}
_PROFILE_SNAPSHOT_FIELDS = {
    "profile_id",
    "revision",
    "base_revision",
    "bootstrap_digest",
    "archive_size_bytes",
    "archive_sha256",
    "archive_artifact_id",
    "artifacts",
}
_PROFILE_ARTIFACT_FIELDS = {"path", "kind", "size_bytes", "sha256"}
_PROFILE_KINDS = {
    "workflows": "workflow",
    "bootstrap": "bootstrap_workflow",
    "settings": "settings",
    "palettes": "palette",
    "backgrounds": "background",
    "assets": "ui_asset",
}
_SUSPICIOUS_TEXT = re.compile(
    r"(?i)(authorization|bearer|api[_ -]?key|password|secret|token)"
)


class WorkerClientError(RuntimeError):
    """A sanitized worker transport or protocol failure."""


class WorkerBoundaryAuthenticationError(WorkerClientError):
    """The worker boundary rejected its controller-owned token."""


@dataclass(frozen=True, repr=False)
class WorkerRequest:
    method: str
    url: str = field(repr=False)
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class WorkerTransportResponse:
    status: int
    headers: dict
    body: bytes = field(repr=False)


@dataclass(frozen=True)
class ArtifactDownload:
    artifact_id: str
    start: int
    total_size: int
    sha256: str
    mime_type: str


@dataclass(frozen=True)
class PreviewDownload:
    content: bytes = field(repr=False)
    mime_type: str
    sha256: str


def _client_error():
    return WorkerClientError("Remote worker request failed.")


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _native_route_policy():
    if "." in (__package__ or ""):
        from ..remote_worker.native_proxy import NativeRoutePolicy
    else:
        from remote_worker.native_proxy import NativeRoutePolicy
    return NativeRoutePolicy


def _json_bytes(payload):
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise _client_error() from None
    if not 0 < len(encoded) <= MAX_WORKER_JSON_BYTES:
        raise _client_error()
    return encoded


def _response_headers(headers):
    try:
        return {
            str(key).casefold(): str(value)
            for key, value in headers.items()
        }
    except (AttributeError, TypeError, ValueError):
        raise _client_error() from None


def _parse_json_response(response, *, maximum=MAX_WORKER_JSON_BYTES):
    if (
        not isinstance(response, WorkerTransportResponse)
        or isinstance(response.status, bool)
        or not isinstance(response.status, int)
        or not isinstance(response.body, bytes)
        or len(response.body) > maximum
    ):
        raise _client_error()
    if response.status == 401:
        raise WorkerBoundaryAuthenticationError(
            "Remote worker boundary authentication failed."
        )
    headers = _response_headers(response.headers)
    if headers.get("content-encoding", "identity").casefold() != "identity":
        raise _client_error()
    content_type = headers.get("content-type", "")
    if content_type and not content_type.casefold().startswith(
        "application/json"
    ):
        raise _client_error()
    if response.status != 200 and response.status != 202:
        raise _client_error()
    try:
        payload = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _client_error() from None
    if not isinstance(payload, dict):
        raise _client_error()
    return payload


def _finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _validated_snapshot_output(value):
    if not isinstance(value, dict) or set(value) != _SNAPSHOT_OUTPUT_FIELDS:
        raise _client_error()
    filename = value.get("filename")
    subfolder = value.get("subfolder")
    size_bytes = value.get("size_bytes")
    if (
        not _identifier(value.get("artifact_id"))
        or not _identifier(value.get("node_id"))
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
        or not isinstance(value.get("mime_type"), str)
        or not _MIME_TYPE.fullmatch(value["mime_type"])
        or isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or not 0 < size_bytes <= MAX_WORKER_ARTIFACT_BYTES
        or not isinstance(value.get("sha256"), str)
        or not _HEX_64.fullmatch(value["sha256"])
    ):
        raise _client_error()
    relative = PurePosixPath(subfolder)
    if relative.is_absolute() or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        if subfolder:
            raise _client_error()
    return dict(value)


def _validated_snapshot_error(value):
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not {"code", "message"}.issubset(value)
        or not set(value).issubset(_SNAPSHOT_ERROR_FIELDS)
        or not _identifier(value.get("code"))
        or not isinstance(value.get("message"), str)
        or not value["message"]
        or len(value["message"].encode("utf-8")) > 1024
        or any(ord(character) < 32 for character in value["message"])
        or _SUSPICIOUS_TEXT.search(value["message"])
    ):
        raise _client_error()
    for key in ("node_id", "class_type"):
        if key in value and not _identifier(value[key]):
            raise _client_error()
    if "title" in value and (
        not isinstance(value["title"], str)
        or not value["title"]
        or len(value["title"].encode("utf-8")) > 512
        or any(ord(character) < 32 for character in value["title"])
        or _SUSPICIOUS_TEXT.search(value["title"])
    ):
        raise _client_error()
    return dict(value)


def _validated_snapshot(payload, job_id, after_sequence):
    if (
        not isinstance(payload, dict)
        or set(payload) != _SNAPSHOT_FIELDS
        or payload.get("job_id") != job_id
        or payload.get("state") not in _SNAPSHOT_STATES
        or not isinstance(payload.get("events"), list)
        or len(payload["events"]) > 20_000
        or not isinstance(payload.get("outputs"), list)
        or len(payload["outputs"]) > 100_000
        or isinstance(payload.get("last_sequence"), bool)
        or not isinstance(payload.get("last_sequence"), int)
        or payload["last_sequence"] < after_sequence
        or not _finite_number(payload.get("created_at"))
        or payload["created_at"] < 0
        or not _finite_number(payload.get("updated_at"))
        or payload["updated_at"] < payload["created_at"]
    ):
        raise _client_error()
    prompt_id = payload.get("prompt_id")
    if prompt_id is not None and not _canonical_uuid(prompt_id):
        raise _client_error()
    events = []
    expected_sequence = after_sequence + 1
    for event in payload["events"]:
        if (
            not isinstance(event, dict)
            or set(event) != {"sequence", "type", "data", "created_at"}
            or event.get("sequence") != expected_sequence
            or event["sequence"] > payload["last_sequence"]
            or event.get("type") not in _SNAPSHOT_EVENT_TYPES
            or not isinstance(event.get("data"), dict)
            or not _finite_number(event.get("created_at"))
            or event["created_at"] < 0
            or event["created_at"] > payload["updated_at"]
        ):
            raise _client_error()
        events.append(
            {
                "sequence": event["sequence"],
                "type": event["type"],
                "data": dict(event["data"]),
                "created_at": float(event["created_at"]),
            }
        )
        expected_sequence += 1
    if (
        events
        and events[-1]["sequence"] != payload["last_sequence"]
    ) or (
        not events and payload["last_sequence"] != after_sequence
    ):
        raise _client_error()
    outputs = [
        _validated_snapshot_output(output)
        for output in payload["outputs"]
    ]
    if len({output["artifact_id"] for output in outputs}) != len(outputs):
        raise _client_error()
    error = _validated_snapshot_error(payload["error"])
    state = payload["state"]
    if (
        state in {"queued", "running", "succeeded"}
        and error is not None
    ) or (state in {"queued", "running"} and outputs) or (
        state in {"failed", "interrupted"} and error is None
    ):
        raise _client_error()
    return {
        "job_id": job_id,
        "state": state,
        "prompt_id": prompt_id,
        "events": events,
        "last_sequence": payload["last_sequence"],
        "outputs": outputs,
        "error": error,
        "created_at": float(payload["created_at"]),
        "updated_at": float(payload["updated_at"]),
    }


def _validated_profile_snapshot(payload, after_revision):
    if not isinstance(payload, dict) or set(payload) != _PROFILE_SNAPSHOT_FIELDS:
        raise _client_error()
    revision = payload.get("revision")
    base_revision = payload.get("base_revision")
    archive_size = payload.get("archive_size_bytes")
    archive_digest = payload.get("archive_sha256")
    if (
        not _identifier(payload.get("profile_id"))
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision <= after_revision
        or (
            base_revision is not None
            and (
                isinstance(base_revision, bool)
                or not isinstance(base_revision, int)
                or not 0 < base_revision < revision
            )
        )
        or (revision == 1) != (base_revision is None)
        or not isinstance(payload.get("bootstrap_digest"), str)
        or not _HEX_64.fullmatch(payload["bootstrap_digest"])
        or isinstance(archive_size, bool)
        or not isinstance(archive_size, int)
        or not 0 < archive_size <= 128 * 1024 * 1024
        or not isinstance(archive_digest, str)
        or not _HEX_64.fullmatch(archive_digest)
        or payload.get("archive_artifact_id") != "profile-" + archive_digest
        or not isinstance(payload.get("artifacts"), list)
        or not 0 < len(payload["artifacts"]) <= 2_000
    ):
        raise _client_error()
    artifacts = []
    paths = set()
    for item in payload["artifacts"]:
        if not isinstance(item, dict) or set(item) != _PROFILE_ARTIFACT_FIELDS:
            raise _client_error()
        path = item.get("path")
        kind = item.get("kind")
        try:
            validate_dependency(
                ProfileFileSpec(
                    path=path,
                    size_bytes=item.get("size_bytes"),
                    sha256=item.get("sha256"),
                )
            )
            root = PurePosixPath(path).parts[0]
        except (AttributeError, TypeError, ValueError):
            raise _client_error() from None
        if kind != _PROFILE_KINDS.get(root) or path in paths:
            raise _client_error()
        paths.add(path)
        artifacts.append(dict(item))
    if [item["path"] for item in artifacts] != sorted(paths):
        raise _client_error()
    return {
        "profile_id": payload["profile_id"],
        "revision": revision,
        "base_revision": base_revision,
        "bootstrap_digest": payload["bootstrap_digest"],
        "archive_size_bytes": archive_size,
        "archive_sha256": archive_digest,
        "archive_artifact_id": payload["archive_artifact_id"],
        "artifacts": artifacts,
    }


def _validated_base_url(value):
    if not isinstance(value, str) or len(value) > 512:
        raise _client_error()
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname)
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        raise _client_error() from None
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not address.is_global
        or port is None
        or not 1 <= port <= 65535
    ):
        raise _client_error()
    derived = derive_base_url(
        {
            "public_ipaddr": str(address),
            "ports": {
                "8765/tcp": [{"HostPort": str(port)}],
            },
        },
        8765,
    )
    if derived != value:
        raise _client_error()
    return value


class AiohttpWorkerTransport:
    async def request(self, request, *, max_bytes):
        if (
            not isinstance(request, WorkerRequest)
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 < max_bytes <= MAX_WORKER_NATIVE_RESPONSE_BYTES
        ):
            raise _client_error()
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise _client_error() from None
        try:
            async with ClientSession(
                timeout=ClientTimeout(total=30),
                auto_decompress=False,
                trust_env=False,
            ) as session:
                async with session.request(
                    request.method,
                    request.url,
                    headers=request.headers,
                    data=request.body,
                    allow_redirects=False,
                ) as response:
                    if response.content_length is not None and (
                        response.content_length > max_bytes
                    ):
                        raise _client_error()
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise _client_error()
                    return WorkerTransportResponse(
                        status=int(response.status),
                        headers=dict(response.headers),
                        body=bytes(content),
                    )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerClientError:
            raise
        except Exception:
            raise _client_error() from None

    async def stream(
        self,
        request,
        *,
        on_headers,
        on_chunk,
        max_bytes,
    ):
        if (
            not isinstance(request, WorkerRequest)
            or not callable(on_headers)
            or not callable(on_chunk)
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes < 0
        ):
            raise _client_error()
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise _client_error() from None
        try:
            async with ClientSession(
                timeout=ClientTimeout(
                    total=None,
                    sock_connect=30,
                    sock_read=60,
                ),
                auto_decompress=False,
                trust_env=False,
            ) as session:
                async with session.request(
                    request.method,
                    request.url,
                    headers=request.headers,
                    data=request.body,
                    allow_redirects=False,
                ) as response:
                    metadata = on_headers(
                        int(response.status),
                        dict(response.headers),
                    )
                    if asyncio.iscoroutine(metadata):
                        await metadata
                    received = 0
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        received += len(chunk)
                        if received > max_bytes:
                            raise _client_error()
                        result = on_chunk(bytes(chunk))
                        if asyncio.iscoroutine(result):
                            await result
                    return received
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerClientError:
            raise
        except Exception:
            raise _client_error() from None

    async def websocket(self, request, *, max_bytes):
        if (
            not isinstance(request, WorkerRequest)
            or request.method != "GET"
            or request.body
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 < max_bytes <= MAX_WORKER_JSON_BYTES
        ):
            raise _client_error()
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise _client_error() from None
        session = ClientSession(
            timeout=ClientTimeout(total=None, sock_connect=30, sock_read=60),
            auto_decompress=False,
            trust_env=False,
        )
        try:
            original_request = session.request

            def request_without_redirects(method, url, **kwargs):
                kwargs["allow_redirects"] = False
                return original_request(method, url, **kwargs)

            # aiohttp's public ws_connect API does not expose redirect
            # controls.  It delegates the handshake to session.request, so
            # pin that one request path to the authenticated worker origin.
            session.request = request_without_redirects
            socket = await session.ws_connect(
                request.url,
                headers=request.headers,
                heartbeat=30,
                autoclose=False,
                max_msg_size=max_bytes,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            await session.close()
            raise
        except Exception:
            await session.close()
            raise _client_error() from None
        return _OwnedWorkerWebSocket(session, socket)


class _OwnedWorkerWebSocket:
    def __init__(self, session, socket):
        self._session = session
        self._socket = socket

    def __aiter__(self):
        return self._socket.__aiter__()

    async def send_str(self, value):
        return await self._socket.send_str(value)

    async def send_bytes(self, value):
        return await self._socket.send_bytes(value)

    async def close(self, *, code=1000):
        try:
            return await self._socket.close(code=code)
        finally:
            await self._session.close()


class WorkerClient:
    def __init__(
        self,
        *,
        base_url,
        provider_token,
        session_id,
        session_secret,
        transport=None,
        clock=None,
        nonce=None,
    ):
        self._base_url = _validated_base_url(base_url)
        if (
            not is_boundary_token(provider_token)
            or not is_worker_session_id(session_id)
            or not isinstance(session_secret, bytes)
            or len(session_secret) != 32
        ):
            raise _client_error()
        self._provider_token = provider_token
        self.session_id = session_id
        self._session_secret = bytes(session_secret)
        self.transport = transport or AiohttpWorkerTransport()
        if not callable(getattr(self.transport, "request", None)):
            raise _client_error()
        self.clock = clock or __import__("time").time
        self.nonce = nonce or (lambda: secrets.token_urlsafe(24))

    def public_payload(self):
        return {
            "session_id": self.session_id,
            "protocol_version": PROTOCOL_VERSION,
        }

    def _timestamp(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise _client_error()
        return int(value)

    def _request(
        self,
        method,
        path,
        body=b"",
        *,
        signed=True,
        extra=None,
        content_type="application/json",
    ):
        if (
            not isinstance(method, str)
            or not isinstance(path, str)
            or not path.startswith("/")
            or "\r" in path
            or "\n" in path
            or not isinstance(body, bytes)
            or content_type
            not in {"application/json", "application/octet-stream"}
        ):
            raise _client_error()
        headers = {
            "Authorization": "Bearer " + self._provider_token,
            "Accept": "application/json",
        }
        if body:
            headers["Content-Type"] = content_type
        if signed:
            timestamp = self._timestamp()
            nonce = self.nonce()
            try:
                envelope = sign_request(
                    self._session_secret,
                    method,
                    path,
                    body,
                    timestamp=timestamp,
                    nonce=nonce,
                )
            except Exception:
                raise _client_error() from None
            headers.update(
                {
                    "X-Cloud-Run-Protocol-Version": envelope[
                        "protocol_version"
                    ],
                    "X-Cloud-Run-Timestamp": str(envelope["timestamp"]),
                    "X-Cloud-Run-Nonce": envelope["nonce"],
                    "X-Cloud-Run-Signature": envelope["signature"],
                }
            )
        if extra:
            if not isinstance(extra, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in extra.items()
            ):
                raise _client_error()
            allowed_headers = {"accept", "content-range", "range"}
            if any(key.casefold() not in allowed_headers for key in extra):
                raise _client_error()
            headers.update(extra)
        return WorkerRequest(
            method=method.upper(),
            url=self._base_url + path,
            headers=headers,
            body=body,
        )

    def native_envelope(
        self,
        method,
        path_qs,
        body,
        *,
        identity=None,
        headers=None,
    ):
        try:
            NativeRoutePolicy = _native_route_policy()
        except ImportError:
            raise _client_error() from None
        if (
            not isinstance(method, str)
            or not isinstance(path_qs, str)
            or not isinstance(body, bytes)
            or len(body) > MAX_WORKER_JSON_BYTES
        ):
            raise _client_error()
        route = NativeRoutePolicy().classify(method, path_qs)
        if route is None or (method.upper() == "GET" and body):
            raise _client_error()
        semantic_identity = {} if identity is None else identity
        if not isinstance(semantic_identity, dict):
            raise _client_error()
        if route.kind == "prompt":
            if set(semantic_identity) != {
                "job_id",
                "request_id",
                "manifest_digest",
            }:
                raise _client_error()
        elif semantic_identity:
            raise _client_error()
        forwarded = {} if headers is None else headers
        if not isinstance(forwarded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in forwarded.items()
        ):
            raise _client_error()
        allowed = {
            "accept": "Accept",
            "content-type": "Content-Type",
            "if-modified-since": "If-Modified-Since",
            "if-none-match": "If-None-Match",
            "range": "Range",
        }
        if any(key.casefold() not in allowed for key in forwarded):
            raise _client_error()
        request_headers = {
            "Authorization": "Bearer " + self._provider_token,
            "Accept": "*/*",
        }
        for key, value in forwarded.items():
            if (
                not value
                or len(value) > 8192
                or any(character in value for character in "\r\n")
            ):
                raise _client_error()
            request_headers[allowed[key.casefold()]] = value
        if body and "Content-Type" not in request_headers:
            request_headers["Content-Type"] = "application/json"
        try:
            material = native_request_material(body, semantic_identity)
            envelope = sign_request(
                self._session_secret,
                method,
                path_qs,
                material,
                timestamp=self._timestamp(),
                nonce=self.nonce(),
            )
        except Exception:
            raise _client_error() from None
        request_headers.update(
            {
                "X-Cloud-Run-Protocol-Version": envelope[
                    "protocol_version"
                ],
                "X-Cloud-Run-Timestamp": str(envelope["timestamp"]),
                "X-Cloud-Run-Nonce": envelope["nonce"],
                "X-Cloud-Run-Signature": envelope["signature"],
            }
        )
        if semantic_identity:
            request_headers.update(
                {
                    "X-Cloud-Vast-Job-Id": semantic_identity["job_id"],
                    "X-Cloud-Vast-Request-Id": semantic_identity[
                        "request_id"
                    ],
                    "X-Cloud-Vast-Manifest": semantic_identity[
                        "manifest_digest"
                    ],
                }
            )
        return WorkerRequest(
            method=method.upper(),
            url=self._base_url + path_qs,
            headers=request_headers,
            body=body,
        )

    async def native_websocket(self, request):
        websocket = getattr(self.transport, "websocket", None)
        if (
            not isinstance(request, WorkerRequest)
            or request.method != "GET"
            or request.body
            or not callable(websocket)
            or not request.url.startswith(self._base_url + "/ws?")
        ):
            raise _client_error()
        try:
            return await websocket(
                request,
                max_bytes=MAX_WORKER_JSON_BYTES,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _client_error() from None

    async def _json(
        self,
        method,
        path,
        *,
        payload=None,
        signed=True,
    ):
        body = _json_bytes(payload) if payload is not None else b""
        request = self._request(
            method,
            path,
            body,
            signed=signed,
        )
        try:
            response = await self.transport.request(
                request,
                max_bytes=MAX_WORKER_JSON_BYTES,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _client_error() from None
        return _parse_json_response(response)

    async def claim(self):
        payload = await self._json(
            "POST",
            "/worker/v1/claim",
            payload={
                "protocol_version": PROTOCOL_VERSION,
                "session_id": self.session_id,
                "session_secret_hex": self._session_secret.hex(),
            },
            signed=False,
        )
        if (
            set(payload) != {
                "protocol_version",
                "session_id",
                "claimed",
            }
            or payload.get("protocol_version") != PROTOCOL_VERSION
            or payload.get("session_id") != self.session_id
            or payload.get("claimed") is not True
        ):
            raise _client_error()
        return payload

    async def health(self):
        payload = await self._json("GET", "/worker/v1/health")
        if (
            set(payload) != {"protocol_version", "claimed"}
            or payload.get("protocol_version") != PROTOCOL_VERSION
            or not isinstance(payload.get("claimed"), bool)
        ):
            raise _client_error()
        return payload

    async def apply_manifest(self, payload):
        return await self._json(
            "POST",
            "/worker/v1/manifests",
            payload=payload,
        )

    async def transaction(self, transaction_id):
        if not _identifier(transaction_id):
            raise _client_error()
        return await self._json(
            "GET",
            "/worker/v1/transactions/" + transaction_id,
        )

    async def start_job(self, payload):
        return await self._json(
            "POST",
            "/worker/v1/jobs",
            payload=payload,
        )

    async def job(self, job_id):
        if not _identifier(job_id):
            raise _client_error()
        return await self._json("GET", "/worker/v1/jobs/" + job_id)

    async def events(self, job_id, after_sequence):
        if (
            not _identifier(job_id)
            or isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise _client_error()
        return await self._json(
            "GET",
            (
                "/worker/v1/jobs/"
                + job_id
                + "/events?after_sequence="
                + str(after_sequence)
            ),
        )

    async def snapshot(self, job_id, after_sequence):
        if (
            not _identifier(job_id)
            or isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise _client_error()
        payload = await self._json(
            "GET",
            (
                "/worker/v1/jobs/"
                + job_id
                + "/snapshot?after_sequence="
                + str(after_sequence)
            ),
        )
        return _validated_snapshot(payload, job_id, after_sequence)

    async def apply_profile(self, profile):
        payload = await self._json(
            "PUT",
            "/worker/v1/profile",
            payload={"profile": profile},
        )
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "profile_id",
                "revision",
                "bootstrap_digest",
                "bootstrap_loaded_at_revision",
            }
            or not _identifier(payload.get("profile_id"))
            or isinstance(payload.get("revision"), bool)
            or not isinstance(payload.get("revision"), int)
            or payload["revision"] <= 0
            or not isinstance(payload.get("bootstrap_digest"), str)
            or not _HEX_64.fullmatch(payload["bootstrap_digest"])
            or (
                payload["bootstrap_loaded_at_revision"] is not None
                and (
                    isinstance(payload["bootstrap_loaded_at_revision"], bool)
                    or not isinstance(
                        payload["bootstrap_loaded_at_revision"], int
                    )
                    or not 0
                    < payload["bootstrap_loaded_at_revision"]
                    <= payload["revision"]
                )
            )
        ):
            raise _client_error()
        return payload

    async def profile_snapshot(self, after_revision):
        if (
            isinstance(after_revision, bool)
            or not isinstance(after_revision, int)
            or after_revision < 0
        ):
            raise _client_error()
        path = "/worker/v1/profile?after_revision=" + str(after_revision)
        request = self._request("GET", path)
        try:
            response = await self.transport.request(
                request,
                max_bytes=MAX_WORKER_JSON_BYTES,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _client_error() from None
        if (
            isinstance(response, WorkerTransportResponse)
            and response.status == 204
            and response.body == b""
            and _response_headers(response.headers).get(
                "content-encoding", "identity"
            ).casefold()
            == "identity"
        ):
            return None
        payload = _parse_json_response(response)
        return _validated_profile_snapshot(payload, after_revision)

    async def preview(self, job_id, preview_id):
        if not _identifier(job_id) or not _identifier(preview_id):
            raise _client_error()
        path = (
            "/worker/v1/jobs/"
            + job_id
            + "/previews/"
            + preview_id
        )
        request = self._request(
            "GET",
            path,
            extra={"Accept": "image/png,image/jpeg"},
        )
        try:
            response = await self.transport.request(
                request,
                max_bytes=MAX_WORKER_PREVIEW_BYTES,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _client_error() from None
        if (
            not isinstance(response, WorkerTransportResponse)
            or response.status != 200
            or not isinstance(response.body, bytes)
            or not 0 < len(response.body) <= MAX_WORKER_PREVIEW_BYTES
        ):
            raise _client_error()
        headers = _response_headers(response.headers)
        mime_type = headers.get("content-type", "").casefold()
        if mime_type not in {"image/png", "image/jpeg"}:
            raise _client_error()
        return PreviewDownload(
            content=response.body,
            mime_type=mime_type,
            sha256=hashlib.sha256(response.body).hexdigest(),
        )

    async def _download_artifact(
        self,
        artifact_id,
        *,
        route_prefix,
        start,
        on_chunk,
    ):
        if (
            not _identifier(artifact_id)
            or isinstance(start, bool)
            or not isinstance(start, int)
            or start < 0
            or not callable(on_chunk)
            or not callable(getattr(self.transport, "stream", None))
        ):
            raise _client_error()
        if route_prefix not in {
            "/worker/v1/artifacts/",
            "/worker/v1/profile/artifacts/",
        }:
            raise _client_error()
        path = route_prefix + artifact_id + "?start=" + str(start)
        request = self._request(
            "GET",
            path,
            extra={
                "Accept": "application/octet-stream",
                "Range": "bytes=" + str(start) + "-",
            },
        )
        metadata = {}

        def validate_headers(status, raw_headers):
            headers = _response_headers(raw_headers)
            if (
                status != 206
                or headers.get(
                    "content-encoding",
                    "identity",
                ).casefold()
                != "identity"
            ):
                raise _client_error()
            match = _CONTENT_RANGE.fullmatch(
                headers.get("content-range", "")
            )
            etag = headers.get("etag", "")
            if (
                match is None
                or int(match.group(1)) != start
                or int(match.group(2)) < start
                or int(match.group(3)) <= int(match.group(2))
                or not re.fullmatch(r'"[0-9a-f]{64}"', etag)
            ):
                raise _client_error()
            total = int(match.group(3))
            content_length = int(match.group(2)) - start + 1
            try:
                declared_length = int(headers.get("content-length", ""))
            except ValueError:
                raise _client_error() from None
            if declared_length != content_length:
                raise _client_error()
            metadata.update(
                {
                    "total": total,
                    "sha256": etag[1:-1],
                    "mime_type": headers.get(
                        "content-type",
                        "application/octet-stream",
                    ),
                    "length": content_length,
                }
            )

        try:
            received = await self.transport.stream(
                request,
                on_headers=validate_headers,
                on_chunk=on_chunk,
                max_bytes=4 * 1024 * 1024 * 1024 * 1024,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _client_error() from None
        if (
            not metadata
            or isinstance(received, bool)
            or not isinstance(received, int)
            or received != metadata["length"]
        ):
            raise _client_error()
        return ArtifactDownload(
            artifact_id=artifact_id,
            start=start,
            total_size=metadata["total"],
            sha256=metadata["sha256"],
            mime_type=metadata["mime_type"],
        )

    async def download_artifact(self, artifact_id, *, start, on_chunk):
        return await self._download_artifact(
            artifact_id,
            route_prefix="/worker/v1/artifacts/",
            start=start,
            on_chunk=on_chunk,
        )

    async def download_profile_artifact(
        self,
        artifact_id,
        *,
        start,
        on_chunk,
    ):
        return await self._download_artifact(
            artifact_id,
            route_prefix="/worker/v1/profile/artifacts/",
            start=start,
            on_chunk=on_chunk,
        )

    async def upload_artifact(
        self,
        artifact_id,
        *,
        path,
        size_bytes,
        sha256,
        start,
        on_progress,
    ):
        if (
            not _identifier(artifact_id)
            or not isinstance(path, str)
            or not path
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 < size_bytes <= MAX_WORKER_ARTIFACT_BYTES
            or not isinstance(sha256, str)
            or not _HEX_64.fullmatch(sha256)
            or isinstance(start, bool)
            or not isinstance(start, int)
            or not 0 <= start < size_bytes
            or not callable(on_progress)
        ):
            raise _client_error()
        descriptor = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(Path(path), flags)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_size != size_bytes
            ):
                raise _client_error()
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), sha256):
                raise _client_error()
            os.lseek(descriptor, start, os.SEEK_SET)
            offset = start
            last = None
            request_path = "/worker/v1/artifacts/" + artifact_id
            while offset < size_bytes:
                chunk = os.read(
                    descriptor,
                    min(
                        MAX_WORKER_UPLOAD_CHUNK_BYTES,
                        size_bytes - offset,
                    ),
                )
                if not chunk:
                    raise _client_error()
                end = offset + len(chunk) - 1
                request = self._request(
                    "PUT",
                    request_path,
                    chunk,
                    extra={
                        "Accept": "application/json",
                        "Content-Range": (
                            "bytes "
                            + str(offset)
                            + "-"
                            + str(end)
                            + "/"
                            + str(size_bytes)
                        ),
                    },
                    content_type="application/octet-stream",
                )
                try:
                    response = await self.transport.request(
                        request,
                        max_bytes=MAX_WORKER_ERROR_BYTES,
                    )
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    raise _client_error() from None
                payload = _parse_json_response(
                    response,
                    maximum=MAX_WORKER_ERROR_BYTES,
                )
                expected_offset = end + 1
                expected_state = (
                    "verified"
                    if expected_offset == size_bytes
                    else "receiving"
                )
                if (
                    set(payload)
                    != {
                        "artifact_id",
                        "state",
                        "next_offset",
                        "size_bytes",
                        "sha256",
                    }
                    or payload.get("artifact_id") != artifact_id
                    or payload.get("state") != expected_state
                    or payload.get("next_offset") != expected_offset
                    or payload.get("size_bytes") != size_bytes
                    or not hmac.compare_digest(
                        str(payload.get("sha256") or ""),
                        sha256,
                    )
                ):
                    raise _client_error()
                offset = expected_offset
                progress = on_progress(offset)
                if asyncio.iscoroutine(progress):
                    await progress
                last = payload
            after = os.fstat(descriptor)
            if (
                after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
            ):
                raise _client_error()
            return last
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerClientError:
            raise
        except OSError:
            raise _client_error() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    async def upload_status(self, artifact_id):
        if not _identifier(artifact_id):
            raise _client_error()
        payload = await self._json(
            "GET",
            "/worker/v1/transactions/transfer:" + artifact_id,
        )
        if (
            set(payload)
            != {
                "artifact_id",
                "state",
                "next_offset",
                "size_bytes",
                "sha256",
            }
            or payload.get("artifact_id") != artifact_id
            or payload.get("state") not in {"receiving", "verified"}
            or isinstance(payload.get("next_offset"), bool)
            or not isinstance(payload.get("next_offset"), int)
            or isinstance(payload.get("size_bytes"), bool)
            or not isinstance(payload.get("size_bytes"), int)
            or not 0
            <= payload["next_offset"]
            <= payload["size_bytes"]
            <= MAX_WORKER_ARTIFACT_BYTES
            or (
                payload["state"] == "receiving"
                and payload["next_offset"] >= payload["size_bytes"]
            )
            or (
                payload["state"] == "verified"
                and payload["next_offset"] != payload["size_bytes"]
            )
            or not isinstance(payload.get("sha256"), str)
            or not _HEX_64.fullmatch(payload["sha256"])
        ):
            raise _client_error()
        return payload

    async def update_deadline(self, payload):
        return await self._json(
            "PUT",
            "/worker/v1/deadline",
            payload=payload,
        )

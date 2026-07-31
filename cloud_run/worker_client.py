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
import re
import secrets
import stat
from urllib.parse import urlsplit

from .manifest import PROTOCOL_VERSION
from .vast import derive_base_url
from .worker_protocol import sign_request


MAX_WORKER_JSON_BYTES = 16 * 1024 * 1024
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


class WorkerClientError(RuntimeError):
    """A sanitized worker transport or protocol failure."""


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
            or not 0 < max_bytes <= MAX_WORKER_JSON_BYTES
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
            not isinstance(provider_token, str)
            or not provider_token
            or provider_token != provider_token.strip()
            or len(provider_token) > 4096
            or any(ord(character) < 33 for character in provider_token)
            or not _identifier(session_id)
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

    async def download_artifact(
        self,
        artifact_id,
        *,
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
        path = (
            "/worker/v1/artifacts/"
            + artifact_id
            + "?start="
            + str(start)
        )
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

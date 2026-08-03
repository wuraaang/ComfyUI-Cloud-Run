"""Authenticated fixed-origin proxy for the reviewed native ComfyUI surface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .native_jobs import (
    JobError,
    NativePromptIntent,
    MAX_NATIVE_RESPONSE_BYTES,
)


COMFY_LOOPBACK_ORIGIN = "http://127.0.0.1:8188"
MAX_NATIVE_BODY_BYTES = 16 * 1024 * 1024
MAX_NATIVE_HTTP_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_NATIVE_WEBSOCKET_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_STATIC_COMPONENT = re.compile(r"[A-Za-z0-9@+_,.=-]{1,255}")
_IDENTITY_HEADERS = {
    "x-cloud-vast-job-id": "job_id",
    "x-cloud-vast-request-id": "request_id",
    "x-cloud-vast-manifest": "manifest_digest",
}
_REQUEST_HEADERS = {
    "accept": "Accept",
    "content-type": "Content-Type",
    "if-modified-since": "If-Modified-Since",
    "if-none-match": "If-None-Match",
    "range": "Range",
}
_RESPONSE_HEADERS = {
    "accept-ranges": "Accept-Ranges",
    "cache-control": "Cache-Control",
    "content-range": "Content-Range",
    "content-type": "Content-Type",
    "etag": "ETag",
    "last-modified": "Last-Modified",
    "x-content-type-options": "X-Content-Type-Options",
}
_DESKTOP_FRAME_FIELDS = {"type", "data"}


class NativeProxyError(RuntimeError):
    """The native ComfyUI proxy failed without exposing upstream details."""


class NativeProxyValidationError(NativeProxyError):
    """A native request or WebSocket frame was outside the reviewed surface."""


@dataclass(frozen=True)
class NativeRoute:
    kind: str
    path_qs: str
    client_id: object = None


@dataclass(frozen=True)
class NativeProxyResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NativeUpstreamResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


def _safe_components(path):
    if not isinstance(path, str) or not path.startswith("/"):
        return False
    try:
        decoded = unquote(path, encoding="utf-8", errors="strict")
    except (UnicodeError, ValueError):
        return False
    if "%" in decoded or "\\" in decoded or "//" in decoded:
        return False
    components = decoded.split("/")[1:]
    return bool(components) and all(
        component not in {"", ".", ".."}
        and _STATIC_COMPONENT.fullmatch(component) is not None
        for component in components
    )


def _query_pairs(query, *, maximum=8):
    try:
        pairs = parse_qsl(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=maximum,
        )
    except (UnicodeError, ValueError):
        return None
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        return None
    for key, value in pairs:
        if (
            not key
            or len(key) > 64
            or len(value) > 1024
            or any(ord(character) < 32 for character in key + value)
            or "\\" in key + value
            or "%" in value
        ):
            return None
    return dict(pairs)


def _safe_filename(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and all(ord(character) >= 32 for character in value)
    )


def _safe_subfolder(value):
    if value in {None, ""}:
        return True
    if not isinstance(value, str) or value.startswith("/"):
        return False
    components = value.split("/")
    return all(
        _safe_filename(component) and component not in {".", ".."}
        for component in components
    )


class NativeRoutePolicy:
    """Fail-closed method and path policy for the pinned ComfyUI frontend."""

    def classify(self, method, path_qs):
        if (
            not isinstance(method, str)
            or not isinstance(path_qs, str)
            or len(path_qs) > 2048
            or not path_qs.startswith("/")
            or any(character in path_qs for character in "\r\n\\")
        ):
            return None
        try:
            parsed = urlsplit(path_qs)
        except ValueError:
            return None
        if parsed.scheme or parsed.netloc or parsed.fragment:
            return None
        path = parsed.path
        if unquote(path, errors="replace") != path and not _safe_components(path):
            return None
        if any(part in {".", ".."} for part in path.split("/")):
            return None
        method = method.upper()
        query = _query_pairs(parsed.query) if parsed.query else {}
        if query is None:
            return None

        if method == "GET" and path == "/" and not query:
            return NativeRoute("http", path_qs)
        if method == "GET" and (
            path.startswith("/assets/")
            or path.startswith("/extensions/ComfyUI-Cloud-Run/")
        ):
            if _safe_components(path) and (
                not query
                or (
                    set(query) == {"v"}
                    and _IDENTIFIER.fullmatch(query["v"]) is not None
                )
            ):
                return NativeRoute("http", path_qs)
            return None
        if method == "GET" and not query:
            if path in {
                "/object_info",
                "/embeddings",
                "/queue",
                "/history",
            }:
                return NativeRoute("http", path_qs)
            if path.startswith("/models/") and _safe_components(path):
                return NativeRoute("http", path_qs)
            if path.startswith("/history/"):
                identifier = path.removeprefix("/history/")
                if _IDENTIFIER.fullmatch(identifier) is not None:
                    return NativeRoute("http", path_qs)
        if method == "GET" and path == "/view":
            if (
                set(query).issubset({"filename", "type", "subfolder"})
                and {"filename", "type"}.issubset(query)
                and _safe_filename(query["filename"])
                and query["type"] in {"input", "output", "temp"}
                and _safe_subfolder(query.get("subfolder"))
            ):
                return NativeRoute("http", path_qs)
            return None
        if method == "GET" and path == "/ws":
            client_id = query.get("clientId")
            if (
                set(query) == {"clientId"}
                and _IDENTIFIER.fullmatch(client_id or "") is not None
            ):
                return NativeRoute("websocket", path_qs, client_id)
            return None
        if (
            method == "POST"
            and path in {"/prompt", "/queue", "/interrupt", "/free"}
            and not query
        ):
            return NativeRoute(
                "prompt" if path == "/prompt" else "http",
                path_qs,
            )
        return None


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON number.")


def _headers(request):
    raw = getattr(request, "headers", {})
    try:
        return {str(key).casefold(): str(value) for key, value in raw.items()}
    except (AttributeError, TypeError, ValueError):
        return {}


def _request_headers(request):
    source = _headers(request)
    return {
        canonical: source[name]
        for name, canonical in _REQUEST_HEADERS.items()
        if name in source
    }


def _response_headers(headers):
    try:
        source = {}
        for key, value in headers.items():
            name = str(key).casefold()
            normalized = str(value)
            if (
                not name
                or len(name) > 128
                or len(normalized) > 8192
                or any(character in normalized for character in "\r\n")
            ):
                return None
            source[name] = normalized
    except (AttributeError, TypeError, ValueError):
        return None
    return {
        canonical: source[name]
        for name, canonical in _RESPONSE_HEADERS.items()
        if name in source
    }


def _validated_upstream(upstream, *, maximum):
    if (
        not isinstance(upstream, NativeUpstreamResponse)
        or isinstance(upstream.status, bool)
        or not isinstance(upstream.status, int)
        or not 100 <= upstream.status <= 599
        or not isinstance(upstream.body, bytes)
        or len(upstream.body) > maximum
    ):
        raise NativeProxyError("Native upstream response is unavailable.")
    try:
        headers = {
            str(key).casefold(): str(value)
            for key, value in upstream.headers.items()
        }
    except (AttributeError, TypeError, ValueError):
        raise NativeProxyError("Native upstream response is unavailable.") from None
    encoding = headers.get("content-encoding")
    if encoding is not None and encoding.casefold().strip() != "identity":
        raise NativeProxyError("Native upstream response is unavailable.")
    safe_headers = _response_headers(upstream.headers)
    if safe_headers is None:
        raise NativeProxyError("Native upstream response is unavailable.")
    return safe_headers


async def _body(request):
    cached = getattr(request, "_cloud_vast_authenticated_body", None)
    if isinstance(cached, bytes):
        value = cached
    else:
        value = getattr(request, "body", None)
        if not isinstance(value, bytes):
            reader = getattr(request, "read", None)
            if not callable(reader):
                raise NativeProxyValidationError("Native request was rejected.")
            value = await reader()
    if not isinstance(value, bytes) or len(value) > MAX_NATIVE_BODY_BYTES:
        raise NativeProxyValidationError("Native request was rejected.")
    return value


def _identity(request):
    headers = _headers(request)
    present = {
        header: headers[header]
        for header in _IDENTITY_HEADERS
        if header in headers
    }
    unknown = {
        key for key in headers if key.startswith("x-cloud-vast-")
    } - set(_IDENTITY_HEADERS)
    if unknown:
        raise NativeProxyValidationError("Native request was rejected.")
    return {
        field: present[header]
        for header, field in _IDENTITY_HEADERS.items()
        if header in present
    }


class AiohttpNativeTransport:
    """One fixed loopback client that ignores host proxy configuration."""

    def __init__(self):
        self._session = None

    def _client(self):
        if self._session is None:
            try:
                from aiohttp import ClientSession, ClientTimeout
            except ImportError:
                raise NativeProxyError("Native upstream is unavailable.") from None
            self._session = ClientSession(
                base_url=COMFY_LOOPBACK_ORIGIN,
                trust_env=False,
                auto_decompress=False,
                skip_auto_headers={"Accept-Encoding"},
                timeout=ClientTimeout(total=120, connect=10, sock_read=60),
            )
        return self._session

    async def request(self, *, method, path_qs, body, headers, allow_redirects):
        if allow_redirects:
            raise NativeProxyValidationError("Native request was rejected.")
        try:
            async with self._client().request(
                method,
                path_qs,
                data=body if body else None,
                headers=headers,
                allow_redirects=False,
            ) as response:
                content = await response.content.read(
                    MAX_NATIVE_HTTP_RESPONSE_BYTES + 1
                )
                if len(content) > MAX_NATIVE_HTTP_RESPONSE_BYTES:
                    raise NativeProxyError("Native upstream response is unavailable.")
                return NativeUpstreamResponse(
                    status=response.status,
                    body=content,
                    headers=dict(response.headers),
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except NativeProxyError:
            raise
        except Exception:
            raise NativeProxyError("Native upstream is unavailable.") from None

    async def websocket(self, path_qs):
        try:
            return await self._client().ws_connect(
                path_qs,
                heartbeat=30,
                max_msg_size=MAX_NATIVE_WEBSOCKET_BYTES,
                max_redirects=0,
                autoclose=False,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise NativeProxyError("Native WebSocket is unavailable.") from None

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None


def _message_type(message):
    value = getattr(message, "type", None)
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    return str(value).rsplit(".", 1)[-1].upper()


class NativeComfyProxy:
    def __init__(self, *, recorder, transport=None):
        for method in ("observe_text", "observe_binary"):
            if not callable(getattr(recorder, method, None)):
                raise ValueError("Native recorder boundary is invalid.")
        self.recorder = recorder
        self.transport = transport or AiohttpNativeTransport()
        self.policy = NativeRoutePolicy()

    @staticmethod
    def _error(status):
        return NativeProxyResponse(
            status=status,
            body=b'{"error":"Native ComfyUI request was rejected."}',
            headers={"Content-Type": "application/json"},
        )

    async def handle(self, request):
        route = self.policy.classify(
            getattr(request, "method", None),
            getattr(request, "path_qs", getattr(request, "path", None)),
        )
        if route is None:
            return self._error(404)
        if route.kind == "websocket":
            return await self.bridge_websocket(request, route)
        try:
            body = await _body(request)
            identity = _identity(request)
        except NativeProxyValidationError:
            return self._error(400)
        if route.kind != "prompt" and identity:
            return self._error(400)
        if route.kind == "prompt":
            return await self._prompt(request, route, body, identity)
        try:
            upstream = await self.transport.request(
                method=str(request.method).upper(),
                path_qs=route.path_qs,
                body=body,
                headers=_request_headers(request),
                allow_redirects=False,
            )
        except NativeProxyError:
            return self._error(502)
        try:
            safe_headers = _validated_upstream(
                upstream,
                maximum=MAX_NATIVE_HTTP_RESPONSE_BYTES,
            )
        except NativeProxyError:
            return self._error(502)
        if 300 <= upstream.status < 400:
            return self._error(502)
        return NativeProxyResponse(
            status=upstream.status,
            body=upstream.body,
            headers=safe_headers,
        )

    async def _prompt(self, request, route, body, identity):
        if set(identity) != {"job_id", "request_id", "manifest_digest"}:
            return self._error(409)
        try:
            parsed = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
            intent = NativePromptIntent.from_http(body=parsed, **identity)
            receipt = self.recorder.begin(intent)
        except (UnicodeError, ValueError, JobError):
            return self._error(409)
        if not receipt.should_forward:
            if receipt.response is None:
                return self._error(409)
            stored = json.dumps(
                receipt.response,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            return NativeProxyResponse(
                status=200,
                body=stored,
                headers={"Content-Type": "application/json"},
            )
        try:
            upstream = await self.transport.request(
                method="POST",
                path_qs=route.path_qs,
                body=intent.canonical_body.encode("utf-8"),
                headers=_request_headers(request),
                allow_redirects=False,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            self._fail_submission(intent.job_id, validation=False)
            return self._error(502)
        try:
            safe_headers = _validated_upstream(
                upstream,
                maximum=MAX_NATIVE_RESPONSE_BYTES,
            )
            if upstream.status != 200:
                raise NativeProxyError("Native prompt response is unavailable.")
            response = json.loads(
                upstream.body.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
            self.recorder.bind_prompt(intent.job_id, response)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (UnicodeError, ValueError, JobError, NativeProxyError):
            self._fail_submission(intent.job_id, validation=True)
            return self._error(502)
        return NativeProxyResponse(
            status=upstream.status,
            body=upstream.body,
            headers=safe_headers,
        )

    def _fail_submission(self, job_id, *, validation):
        fail = getattr(self.recorder, "fail_native_submission", None)
        if callable(fail):
            try:
                fail(job_id, validation=validation)
            except Exception:
                return False
            return True
        return False

    def _record_sync_failure(self, client_id):
        record = getattr(self.recorder, "record_synchronization_failure", None)
        if callable(record):
            try:
                record(client_id)
            except Exception:
                return

    async def pump_upstream(self, *, client_id, upstream, desktop):
        async for message in upstream:
            message_type = _message_type(message)
            if message_type == "TEXT":
                frame = message.data
                try:
                    self.recorder.observe_text(client_id, str(frame))
                except Exception:
                    self._record_sync_failure(client_id)
                await desktop.send_str(frame)
            elif message_type == "BINARY":
                frame = bytes(message.data)
                try:
                    self.recorder.observe_binary(client_id, bytes(frame))
                except Exception:
                    self._record_sync_failure(client_id)
                await desktop.send_bytes(frame)
            elif message_type in {"CLOSE", "CLOSED", "CLOSING"}:
                break
            elif message_type == "ERROR":
                raise NativeProxyError("Native WebSocket failed.")

    async def pump_desktop(self, *, desktop, upstream):
        async for message in desktop:
            message_type = _message_type(message)
            if message_type == "TEXT":
                try:
                    payload = json.loads(
                        str(message.data),
                        object_pairs_hook=_strict_object,
                        parse_constant=_reject_constant,
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise NativeProxyValidationError(
                        "Native WebSocket frame was rejected."
                    ) from None
                if (
                    not isinstance(payload, dict)
                    or set(payload) != _DESKTOP_FRAME_FIELDS
                    or payload.get("type") != "feature_flags"
                    or not isinstance(payload.get("data"), dict)
                    or set(payload["data"]) != {"supports_preview_metadata"}
                    or not isinstance(
                        payload["data"]["supports_preview_metadata"], bool
                    )
                ):
                    raise NativeProxyValidationError(
                        "Native WebSocket frame was rejected."
                    )
                await upstream.send_str(message.data)
            elif message_type == "BINARY":
                raise NativeProxyValidationError(
                    "Native WebSocket frame was rejected."
                )
            elif message_type in {"CLOSE", "CLOSED", "CLOSING"}:
                break
            elif message_type == "ERROR":
                raise NativeProxyError("Native WebSocket failed.")

    async def bridge_websocket(self, request, route):
        try:
            from aiohttp import web
        except ImportError:
            return self._error(502)
        try:
            if _identity(request):
                return self._error(400)
            upstream = await self.transport.websocket(route.path_qs)
            desktop = web.WebSocketResponse(
                heartbeat=30,
                max_msg_size=MAX_NATIVE_WEBSOCKET_BYTES,
                autoclose=False,
            )
            await desktop.prepare(request)
            tasks = {
                asyncio.create_task(
                    self.pump_upstream(
                        client_id=route.client_id,
                        upstream=upstream,
                        desktop=desktop,
                    )
                ),
                asyncio.create_task(
                    self.pump_desktop(desktop=desktop, upstream=upstream)
                ),
            }
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            failed = False
            for task in done:
                try:
                    task.result()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    failed = True
                    self._record_sync_failure(route.client_id)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await upstream.close(code=1011 if failed else 1000)
            await desktop.close(code=1011 if failed else 1000)
            return desktop
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except NativeProxyError:
            self._record_sync_failure(route.client_id)
            return self._error(502)

    async def close(self):
        close = getattr(self.transport, "close", None)
        if callable(close):
            await close()

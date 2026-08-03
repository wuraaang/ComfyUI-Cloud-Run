"""Stable loopback-only data plane for an official ComfyUI Vast environment."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import secrets

from .agent_bridge import AgentBridgeSession
from .readiness import ReadinessCheck, evidence_digest
from .repository import DesktopRelayConfig
from .worker_client import (
    MAX_WORKER_JSON_BYTES,
    MAX_WORKER_NATIVE_RESPONSE_BYTES,
    WorkerRequest,
    WorkerTransportResponse,
)


COOKIE_NAME = "comfy_vast_session"
COOKIE_TTL_SECONDS = 300
MAX_RELAY_RESPONSE_BYTES = MAX_WORKER_NATIVE_RESPONSE_BYTES
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{32,128}")
_RESPONSE_HEADERS = {
    "accept-ranges": "Accept-Ranges",
    "cache-control": "Cache-Control",
    "content-range": "Content-Range",
    "content-type": "Content-Type",
    "etag": "ETag",
    "last-modified": "Last-Modified",
    "x-content-type-options": "X-Content-Type-Options",
}
_REQUEST_HEADERS = {
    "accept": "Accept",
    "content-type": "Content-Type",
    "if-modified-since": "If-Modified-Since",
    "if-none-match": "If-None-Match",
    "range": "Range",
}
_AGENT_COMPATIBILITY_PATHS = frozenset({
    "/comfyui_mcp_panel/status",
    "/comfyui_mcp_panel/bridge_url",
    "/comfyui_mcp_panel/backends",
})


class DesktopRelayError(RuntimeError):
    """A sanitized local Desktop relay lifecycle failure."""


@dataclass(frozen=True)
class DesktopRelayStatus:
    bound: bool
    url: str | None
    active_session_id: str | None
    profile_revision: int | None
    ready: bool
    error: str | None

    def public_payload(self):
        return {
            "bound": self.bound,
            "url": self.url,
            "connection_name": "ComfyUI Vast",
            "active_session_id": self.active_session_id,
            "profile_revision": self.profile_revision,
            "ready": self.ready,
            "error": self.error,
        }


@dataclass(frozen=True)
class DesktopRelayResponse:
    status: int
    body: bytes
    headers: dict = field(default_factory=dict)


def _headers(request):
    raw = getattr(request, "headers", {})
    try:
        return {str(key).casefold(): str(value) for key, value in raw.items()}
    except (AttributeError, TypeError, ValueError):
        return {}


def _header_values(request, name):
    raw = getattr(request, "headers", {})
    getter = getattr(raw, "getall", None)
    if callable(getter):
        try:
            values = getter(name)
        except (KeyError, TypeError, ValueError):
            values = []
        try:
            return tuple(str(value) for value in values)
        except (TypeError, ValueError):
            return ()
    try:
        return tuple(
            str(value)
            for key, value in raw.items()
            if str(key).casefold() == name.casefold()
        )
    except (AttributeError, TypeError, ValueError):
        return ()


async def _body(request):
    value = getattr(request, "body", None)
    if not isinstance(value, bytes):
        reader = getattr(request, "read", None)
        if not callable(reader):
            raise DesktopRelayError("Desktop relay request was rejected.")
        value = await reader()
    if not isinstance(value, bytes) or len(value) > MAX_WORKER_JSON_BYTES:
        raise DesktopRelayError("Desktop relay request was rejected.")
    return value


def _message_type(message):
    value = getattr(message, "type", None)
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    return str(value).rsplit(".", 1)[-1].upper()


def _native_route_policy():
    if "." in (__package__ or ""):
        from ..remote_worker.native_proxy import NativeRoutePolicy
    else:
        from remote_worker.native_proxy import NativeRoutePolicy
    return NativeRoutePolicy


class _AiohttpRelayListener:
    def __init__(self, handler):
        self.handler = handler
        self.runner = None
        self.site = None

    async def start(self, host, port, handler):
        try:
            from aiohttp import web
        except ImportError:
            raise OSError("aiohttp unavailable") from None
        application = web.Application(
            client_max_size=MAX_WORKER_JSON_BYTES + 1
        )

        async def dispatch(request):
            response = await handler(request)
            if isinstance(response, web.StreamResponse):
                return response
            return web.Response(
                status=response.status,
                body=response.body,
                headers=response.headers,
            )

        application.router.add_route("*", "/{path:.*}", dispatch)
        runner = web.AppRunner(application, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(
                runner,
                host,
                port,
                shutdown_timeout=5,
            )
            await site.start()
            addresses = list(getattr(runner, "addresses", ()))
            if len(addresses) != 1:
                server = getattr(site, "_server", None)
                sockets = list(getattr(server, "sockets", ()) or ())
                addresses = [socket.getsockname() for socket in sockets]
            if len(addresses) != 1:
                raise OSError("ambiguous relay binding")
            bound_port = int(addresses[0][1])
        except Exception:
            await runner.cleanup()
            raise
        self.runner = runner
        self.site = site
        return bound_port

    async def close(self):
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None


class DesktopRelay:
    def __init__(
        self,
        *,
        repository,
        bind_host="127.0.0.1",
        port_selector=None,
        listener_factory=None,
        worker_factory=None,
        native_prompt=None,
        agent_bridge=None,
        local_comfy_root=None,
        capability_factory=None,
        clock=None,
    ):
        if bind_host != "127.0.0.1":
            raise DesktopRelayError(
                "Desktop relay requires the fixed loopback host."
            )
        for method in ("get_desktop_relay", "save_desktop_relay"):
            if not callable(getattr(repository, method, None)):
                raise DesktopRelayError(
                    "Desktop relay storage is unavailable."
                )
        if worker_factory is not None and not callable(worker_factory):
            raise DesktopRelayError("Desktop worker factory is unavailable.")
        if not callable(native_prompt):
            raise DesktopRelayError("Native prompt preparation is unavailable.")
        if agent_bridge is not None and not all(
            callable(getattr(agent_bridge, method, None))
            for method in (
                "allow",
                "open",
                "compatibility",
                "revoke",
                "close",
            )
        ):
            raise DesktopRelayError("Agent Panel bridge is unavailable.")
        if local_comfy_root is not None and not (
            isinstance(local_comfy_root, str) or callable(local_comfy_root)
        ):
            raise DesktopRelayError("Approved ComfyUI root is unavailable.")
        self.repository = repository
        self.bind_host = bind_host
        self.port_selector = port_selector or (lambda: 0)
        self.listener_factory = listener_factory or (
            lambda handler: _AiohttpRelayListener(handler)
        )
        self.worker_factory = worker_factory
        self.native_prompt = native_prompt
        self.agent_bridge = agent_bridge
        self.local_comfy_root = local_comfy_root
        self.capability_factory = capability_factory or (
            lambda: secrets.token_urlsafe(48)
        )
        self.clock = clock or __import__("time").time
        self._config = None
        self._listener = None
        self._worker = None
        self._bound = False
        self._error = None
        self._capability = None
        self._capability_expires_at = None
        self._lifecycle_lock = None
        self._lifecycle_lock_loop = None

    def _lock(self):
        loop = asyncio.get_running_loop()
        if (
            self._lifecycle_lock is None
            or self._lifecycle_lock_loop is not loop
        ):
            self._lifecycle_lock = asyncio.Lock()
            self._lifecycle_lock_loop = loop
        return self._lifecycle_lock

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise DesktopRelayError("Desktop relay clock is unavailable.")
        return float(value)

    def _next_updated_at(self, previous):
        value = max(self._now(), math.nextafter(previous, math.inf))
        if not math.isfinite(value):
            raise DesktopRelayError("Desktop relay clock is unavailable.")
        return value

    async def start(self):
        async with self._lock():
            if self._bound:
                return self.status()
            listener = None
            try:
                config = self.repository.get_desktop_relay()
                self._config = config
                if config is not None and config.bind_host != self.bind_host:
                    raise DesktopRelayError(
                        "Stored Desktop relay configuration is invalid."
                    )
                selected = config.port if config is not None else self.port_selector()
                if (
                    isinstance(selected, bool)
                    or not isinstance(selected, int)
                    or not 0 <= selected <= 65535
                ):
                    raise DesktopRelayError(
                        "Desktop relay port selection failed."
                    )
                listener = self.listener_factory(self.handle)
                bound_port = await listener.start(
                    self.bind_host,
                    selected,
                    self.handle,
                )
                if (
                    isinstance(bound_port, bool)
                    or not isinstance(bound_port, int)
                    or not 1 <= bound_port <= 65535
                    or (config is not None and bound_port != config.port)
                ):
                    raise OSError("invalid relay binding")
                if config is None:
                    config = self.repository.save_desktop_relay(
                        DesktopRelayConfig(
                            bind_host=self.bind_host,
                            port=bound_port,
                            active_session_id=None,
                            profile_revision=None,
                            updated_at=self._now(),
                        )
                    )
                self._config = config
                self._listener = listener
                self._bound = True
                self._error = None
            except OSError:
                self._bound = False
                self._error = "local_port_unavailable"
            except DesktopRelayError:
                self._bound = False
                self._error = "local_relay_unavailable"
            except Exception:
                self._bound = False
                self._error = "local_relay_unavailable"
            if not self._bound and listener is not None:
                try:
                    await listener.close()
                except Exception:
                    pass
            return self.status()

    async def activate(self, session_id, worker, profile_revision):
        if (
            not isinstance(session_id, str)
            or _IDENTIFIER.fullmatch(session_id) is None
            or isinstance(profile_revision, bool)
            or not isinstance(profile_revision, int)
            or profile_revision < 0
            or not callable(getattr(worker, "native_envelope", None))
            or not callable(
                getattr(getattr(worker, "transport", None), "request", None)
            )
        ):
            raise DesktopRelayError("Desktop relay activation was rejected.")
        async with self._lock():
            if not self._bound or self._config is None:
                raise DesktopRelayError("Desktop relay is not bound.")
            if self.agent_bridge is not None:
                try:
                    allowed = self.agent_bridge.allow(session_id)
                    if asyncio.iscoroutine(allowed):
                        await allowed
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    raise DesktopRelayError(
                        "Agent Panel bridge activation failed."
                    ) from None
            previous = self._config
            updated = DesktopRelayConfig(
                bind_host=previous.bind_host,
                port=previous.port,
                active_session_id=session_id,
                profile_revision=profile_revision,
                updated_at=self._next_updated_at(previous.updated_at),
            )
            self._config = self.repository.save_desktop_relay(
                updated,
                expected_updated_at=previous.updated_at,
            )
            self._worker = worker
            self._capability = None
            self._capability_expires_at = None
            self._error = None
            return self.status()

    async def probe_readiness(
        self,
        session_id,
        worker,
        profile_revision,
        *,
        agent_required,
    ):
        """Probe the exact relay data plane without exposing an active Desktop."""
        if (
            not isinstance(session_id, str)
            or _IDENTIFIER.fullmatch(session_id) is None
            or isinstance(profile_revision, bool)
            or not isinstance(profile_revision, int)
            or profile_revision < 0
            or not isinstance(agent_required, bool)
            or not callable(getattr(worker, "native_envelope", None))
            or not callable(
                getattr(getattr(worker, "transport", None), "request", None)
            )
        ):
            raise DesktopRelayError("Desktop readiness probe was rejected.")

        def check(name, status, proof, message):
            return ReadinessCheck(
                name=name,
                status=status,
                evidence_digest=evidence_digest(proof),
                message=message,
            )

        async with self._lock():
            config = self._config
            binding_ok = bool(
                self._bound
                and config is not None
                and config.bind_host == "127.0.0.1"
                and 1 <= config.port <= 65535
                and config.active_session_id in {None, session_id}
                and (
                    getattr(worker, "session_id", session_id)
                    == session_id
                )
            )
            binding = check(
                "loopback_session_binding",
                "passed" if binding_ok else "failed",
                {
                    "bound": binding_ok,
                    "session_id": session_id,
                    "profile_revision": profile_revision,
                },
                (
                    "Loopback session binding passed."
                    if binding_ok
                    else "Loopback session binding failed."
                ),
            )

            http_ok = False
            http_proof = {"status": "unavailable"}
            if binding_ok:
                try:
                    request = worker.native_envelope(
                        "GET",
                        "/system_stats",
                        b"",
                        headers={"Accept": "application/json"},
                    )
                    if not isinstance(request, WorkerRequest):
                        raise DesktopRelayError(
                            "Native HTTP readiness probe failed."
                        )
                    response = await worker.transport.request(
                        request,
                        max_bytes=MAX_RELAY_RESPONSE_BYTES,
                    )
                    http_ok = bool(
                        isinstance(response, WorkerTransportResponse)
                        and response.status == 200
                        and isinstance(response.body, bytes)
                        and len(response.body) <= MAX_RELAY_RESPONSE_BYTES
                    )
                    http_proof = {
                        "status": response.status,
                        "body_sha256": (
                            hashlib.sha256(response.body).hexdigest()
                            if http_ok
                            else None
                        ),
                    }
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    http_ok = False
            http = check(
                "native_http_probe",
                "passed" if http_ok else "failed",
                http_proof,
                (
                    "Native HTTP readiness probe passed."
                    if http_ok
                    else "Native HTTP readiness probe failed."
                ),
            )

            websocket_ok = False
            websocket_proof = {"status": "unavailable"}
            open_socket = getattr(worker, "native_websocket", None)
            if binding_ok and callable(open_socket):
                upstream = None
                try:
                    request = worker.native_envelope(
                        "GET",
                        "/ws?clientId=cloud-vast-readiness",
                        b"",
                    )
                    if not isinstance(request, WorkerRequest):
                        raise DesktopRelayError(
                            "Native WebSocket readiness probe failed."
                        )
                    upstream = await open_socket(request)
                    close = getattr(upstream, "close", None)
                    if not callable(close):
                        raise DesktopRelayError(
                            "Native WebSocket readiness probe failed."
                        )
                    closed = close(code=1000)
                    if asyncio.iscoroutine(closed):
                        await closed
                    websocket_ok = True
                    websocket_proof = {
                        "status": "opened_and_closed",
                        "session_id": session_id,
                    }
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    websocket_ok = False
                    if upstream is not None:
                        try:
                            closed = upstream.close(code=1011)
                            if asyncio.iscoroutine(closed):
                                await closed
                        except Exception:
                            pass
            websocket = check(
                "native_websocket_probe",
                "passed" if websocket_ok else "failed",
                websocket_proof,
                (
                    "Native WebSocket readiness probe passed."
                    if websocket_ok
                    else "Native WebSocket readiness probe failed."
                ),
            )

            agent_status = "not_required"
            agent_ok = not agent_required
            agent_proof = {"required": agent_required}
            if agent_required:
                bridge = self.agent_bridge
                probe = getattr(bridge, "probe", None)
                allow = getattr(bridge, "allow", None)
                revoke = getattr(bridge, "revoke", None)
                if all(callable(item) for item in (probe, allow, revoke)):
                    try:
                        capability = self.capability_factory()
                        now = self._now()
                        root = self.local_comfy_root
                        if callable(root):
                            root = root()
                        bridge_session = AgentBridgeSession(
                            session_id=session_id,
                            relay_origin=(
                                "http://127.0.0.1:" + str(config.port)
                            ),
                            capability=capability,
                            capability_expires_at=now + COOKIE_TTL_SECONDS,
                            local_comfy_root=(
                                str(root) if root is not None else None
                            ),
                        )
                        allowed = allow(session_id)
                        if asyncio.iscoroutine(allowed):
                            await allowed
                        result = probe(bridge_session)
                        if asyncio.iscoroutine(result):
                            result = await result
                        agent_ok = bool(getattr(result, "ready", None) is True)
                        agent_proof = (
                            result.public_payload()
                            if agent_ok
                            and callable(getattr(result, "public_payload", None))
                            else {"required": True, "ready": False}
                        )
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception:
                        agent_ok = False
                    finally:
                        try:
                            revoked = revoke(session_id)
                            if asyncio.iscoroutine(revoked):
                                await revoked
                        except Exception:
                            agent_ok = False
                agent_status = "passed" if agent_ok else "failed"
            agent = check(
                "agent_panel_capabilities",
                agent_status,
                agent_proof,
                (
                    "Agent Panel readiness probe passed."
                    if agent_ok
                    else "Agent Panel readiness probe failed."
                ),
            )
            return (binding, http, websocket, agent)

    async def deactivate(self, session_id):
        if not isinstance(session_id, str) or _IDENTIFIER.fullmatch(session_id) is None:
            raise DesktopRelayError("Desktop relay deactivation was rejected.")
        async with self._lock():
            if self._config is None:
                return self.status()
            if self._config.active_session_id not in {None, session_id}:
                raise DesktopRelayError(
                    "Desktop relay session identity does not match."
                )
            revoke_failed = False
            if self.agent_bridge is not None:
                try:
                    revoked = self.agent_bridge.revoke(session_id)
                    if asyncio.iscoroutine(revoked):
                        await revoked
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    revoke_failed = True
            previous = self._config
            self._config = self.repository.save_desktop_relay(
                DesktopRelayConfig(
                    bind_host=previous.bind_host,
                    port=previous.port,
                    active_session_id=None,
                    profile_revision=None,
                    updated_at=self._next_updated_at(previous.updated_at),
                ),
                expected_updated_at=previous.updated_at,
            )
            self._worker = None
            self._capability = None
            self._capability_expires_at = None
            if revoke_failed:
                self._error = "agent_bridge_unavailable"
                raise DesktopRelayError(
                    "Agent Panel bridge revocation failed."
                )
            return self.status()

    def status(self):
        config = self._config
        active = config.active_session_id if config is not None else None
        revision = config.profile_revision if config is not None else None
        ready = bool(self._bound and self._worker is not None and active)
        return DesktopRelayStatus(
            bound=self._bound,
            url=(
                "http://127.0.0.1:" + str(config.port)
                if config is not None
                else None
            ),
            active_session_id=active,
            profile_revision=revision,
            ready=ready,
            error=self._error,
        )

    def _request_origin_is_valid(self, request):
        if self._config is None:
            return False
        expected_host = "127.0.0.1:" + str(self._config.port)
        hosts = _header_values(request, "host")
        origins = _header_values(request, "origin")
        return (
            len(hosts) == 1
            and hosts[0] == expected_host
            and len(origins) <= 1
            and (
                not origins
                or origins[0] == "http://" + expected_host
            )
        )

    def _cookie_capability(self, request):
        values = _header_values(request, "cookie")
        if len(values) != 1:
            return None
        found = []
        for component in values[0].split(";"):
            name, separator, value = component.strip().partition("=")
            if separator and name == COOKIE_NAME:
                found.append(value)
        return found[0] if len(found) == 1 else None

    def _capability_is_valid(self, request):
        supplied = self._cookie_capability(request)
        if (
            supplied is None
            or self._capability is None
            or self._capability_expires_at is None
            or self._now() >= self._capability_expires_at
        ):
            return False
        return hmac.compare_digest(supplied, self._capability)

    def _cookie_header(self):
        now = self._now()
        if (
            self._capability is None
            or self._capability_expires_at is None
            or now >= self._capability_expires_at
        ):
            try:
                capability = self.capability_factory()
            except Exception:
                raise DesktopRelayError(
                    "Desktop relay capability is unavailable."
                ) from None
            if (
                not isinstance(capability, str)
                or _CAPABILITY.fullmatch(capability) is None
            ):
                raise DesktopRelayError(
                    "Desktop relay capability is unavailable."
                )
            self._capability = capability
            self._capability_expires_at = now + COOKIE_TTL_SECONDS
        remaining = max(
            1,
            min(
                COOKIE_TTL_SECONDS,
                int(self._capability_expires_at - now),
            ),
        )
        return (
            COOKIE_NAME
            + "="
            + self._capability
            + "; Path=/; HttpOnly; SameSite=Strict; Max-Age="
            + str(remaining)
        )

    @staticmethod
    def _response(status, body=b"", *, headers=None):
        return DesktopRelayResponse(
            status=status,
            body=body,
            headers=dict(headers or {}),
        )

    def _context_response(self, *, set_cookie):
        status = self.status()
        payload = {
            "role": "vast",
            "session_id": status.active_session_id,
            "profile_revision": status.profile_revision,
            "agent_bridge_url": (
                "ws://127.0.0.1:"
                + str(self._config.port)
                + "/cloud-run/api/agent/ws"
            ),
        }
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        if set_cookie:
            headers["Set-Cookie"] = self._cookie_header()
        return self._response(
            200,
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
            headers=headers,
        )

    def _agent_session(self):
        if (
            self._config is None
            or self._config.active_session_id is None
            or self._capability is None
            or self._capability_expires_at is None
        ):
            raise DesktopRelayError("Agent Panel bridge is unavailable.")
        root = self.local_comfy_root
        if callable(root):
            try:
                root = root()
            except Exception:
                raise DesktopRelayError(
                    "Approved ComfyUI root is unavailable."
                ) from None
        if root is not None:
            root = str(root)
        return AgentBridgeSession(
            session_id=self._config.active_session_id,
            relay_origin="http://127.0.0.1:" + str(self._config.port),
            capability=self._capability,
            capability_expires_at=self._capability_expires_at,
            local_comfy_root=root,
        )

    def _agent_compatibility_response(self, path):
        if self.agent_bridge is None:
            return self._response(404)
        try:
            payload = self.agent_bridge.compatibility(
                path,
                self._agent_session(),
            )
            body = json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(body) > MAX_WORKER_JSON_BYTES:
                raise DesktopRelayError(
                    "Agent Panel compatibility response was rejected."
                )
        except Exception:
            return self._response(502)
        return self._response(
            200,
            body,
            headers={
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @staticmethod
    def _forward_headers(request):
        source = _headers(request)
        return {
            canonical: source[name]
            for name, canonical in _REQUEST_HEADERS.items()
            if name in source
            and source[name]
            and len(source[name]) <= 8192
            and not any(character in source[name] for character in "\r\n")
        }

    @staticmethod
    def _upstream_headers(response):
        try:
            source = {
                str(key).casefold(): str(value)
                for key, value in response.headers.items()
            }
        except (AttributeError, TypeError, ValueError):
            raise DesktopRelayError("Desktop upstream response was rejected.")
        if source.get("content-encoding", "identity").casefold() != "identity":
            raise DesktopRelayError("Desktop upstream response was rejected.")
        headers = {}
        for name, canonical in _RESPONSE_HEADERS.items():
            if name not in source:
                continue
            value = source[name]
            if len(value) > 8192 or any(character in value for character in "\r\n"):
                raise DesktopRelayError(
                    "Desktop upstream response was rejected."
                )
            headers[canonical] = value
        return headers

    async def _native_http(self, request, route, *, set_cookie):
        try:
            body = await _body(request)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._response(400)
        identity = None
        response_callback = None
        if route.kind == "prompt":
            request_ids = _header_values(
                request,
                "x-cloud-vast-request-id",
            )
            if (
                len(request_ids) != 1
                or _IDENTIFIER.fullmatch(request_ids[0]) is None
            ):
                return self._response(409)
            try:
                prepared = self.native_prompt(
                    self._config.active_session_id,
                    request_id=request_ids[0],
                    body=body,
                )
                if asyncio.iscoroutine(prepared):
                    prepared = await prepared
                server_identity = getattr(
                    prepared,
                    "server_identity",
                    None,
                )
                if callable(server_identity):
                    identity = server_identity()
                    body = getattr(prepared, "body", None)
                    response_callback = getattr(
                        prepared,
                        "bind_response",
                        None,
                    )
                elif isinstance(prepared, dict):
                    prepared = dict(prepared)
                    body = prepared.pop("body", body)
                    identity = prepared
                if (
                    not isinstance(identity, dict)
                    or set(identity)
                    != {"job_id", "request_id", "manifest_digest"}
                    or identity.get("request_id") != request_ids[0]
                    or any(
                        not isinstance(identity.get(name), str)
                        for name in identity
                    )
                    or not isinstance(body, bytes)
                    or not body
                    or len(body) > MAX_WORKER_JSON_BYTES
                    or (
                        response_callback is not None
                        and not callable(response_callback)
                    )
                ):
                    raise DesktopRelayError(
                        "Native prompt preparation was rejected."
                    )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                return self._response(409)
        try:
            envelope = self._worker.native_envelope(
                str(request.method).upper(),
                route.path_qs,
                body,
                identity=identity,
                headers=self._forward_headers(request),
            )
            if not isinstance(envelope, WorkerRequest):
                raise DesktopRelayError("Desktop upstream request was rejected.")
            response = await self._worker.transport.request(
                envelope,
                max_bytes=MAX_RELAY_RESPONSE_BYTES,
            )
            if (
                not isinstance(response, WorkerTransportResponse)
                or isinstance(response.status, bool)
                or not isinstance(response.status, int)
                or not 100 <= response.status <= 599
                or not isinstance(response.body, bytes)
                or len(response.body) > MAX_RELAY_RESPONSE_BYTES
                or 300 <= response.status < 400
            ):
                raise DesktopRelayError(
                    "Desktop upstream response was rejected."
                )
            headers = self._upstream_headers(response)
            if response_callback is not None and response.status == 200:
                bound = response_callback(response.status, response.body)
                if asyncio.iscoroutine(bound):
                    await bound
            if set_cookie:
                headers["Set-Cookie"] = self._cookie_header()
            return self._response(
                response.status,
                response.body,
                headers=headers,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._response(502)

    async def _bridge_websocket(self, request, route):
        open_socket = getattr(self._worker, "native_websocket", None)
        if not callable(open_socket):
            return self._response(502)
        try:
            from aiohttp import web
        except ImportError:
            return self._response(502)
        try:
            envelope = self._worker.native_envelope(
                "GET",
                route.path_qs,
                b"",
                headers=self._forward_headers(request),
            )
            upstream = await open_socket(envelope)
            desktop = web.WebSocketResponse(
                heartbeat=30,
                max_msg_size=MAX_WORKER_JSON_BYTES,
                autoclose=False,
            )
            await desktop.prepare(request)

            async def upstream_to_desktop():
                async for message in upstream:
                    kind = _message_type(message)
                    if kind == "TEXT":
                        await desktop.send_str(message.data)
                    elif kind == "BINARY":
                        await desktop.send_bytes(bytes(message.data))
                    elif kind in {"CLOSE", "CLOSED", "CLOSING"}:
                        break
                    elif kind == "ERROR":
                        raise DesktopRelayError("Desktop WebSocket failed.")

            async def desktop_to_upstream():
                async for message in desktop:
                    kind = _message_type(message)
                    if kind == "TEXT":
                        await upstream.send_str(message.data)
                    elif kind == "BINARY":
                        await upstream.send_bytes(bytes(message.data))
                    elif kind in {"CLOSE", "CLOSED", "CLOSING"}:
                        break
                    elif kind == "ERROR":
                        raise DesktopRelayError("Desktop WebSocket failed.")

            tasks = {
                asyncio.create_task(upstream_to_desktop()),
                asyncio.create_task(desktop_to_upstream()),
            }
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            failed = False
            for task in done:
                try:
                    task.result()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    failed = True
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await upstream.close(code=1011 if failed else 1000)
            await desktop.close(code=1011 if failed else 1000)
            return desktop
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._response(502)

    async def handle(self, request):
        try:
            NativeRoutePolicy = _native_route_policy()
        except ImportError:
            return self._response(503)
        method = getattr(request, "method", None)
        path_qs = getattr(request, "path_qs", getattr(request, "path", None))
        path = getattr(request, "path", None)
        normalized_method = str(method).upper()
        context = (
            normalized_method == "GET"
            and path_qs == "/cloud-run/api/desktop-context"
        )
        agent = (
            normalized_method == "GET"
            and path_qs == "/cloud-run/api/agent/ws"
        )
        agent_compatibility = (
            normalized_method == "GET"
            and path_qs in _AGENT_COMPATIBILITY_PATHS
        )
        route = NativeRoutePolicy().classify(method, path_qs)
        if not context and not agent and not agent_compatibility and route is None:
            return self._response(404)
        if not self.status().ready:
            return self._response(503)
        if not self._request_origin_is_valid(request):
            return self._response(403)
        bootstrap = context or (
            route is not None
            and route.kind == "http"
            and normalized_method == "GET"
            and path == "/"
        )
        valid_cookie = self._capability_is_valid(request)
        if not bootstrap and not valid_cookie:
            return self._response(403)
        set_cookie = bootstrap and not valid_cookie
        if context:
            return self._context_response(set_cookie=set_cookie)
        if agent_compatibility:
            return self._agent_compatibility_response(path_qs)
        if agent:
            if self.agent_bridge is None:
                return self._response(404)
            try:
                result = self.agent_bridge.open(
                    request,
                    self._agent_session(),
                )
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            except Exception:
                return self._response(502)
        if route.kind == "websocket":
            return await self._bridge_websocket(request, route)
        return await self._native_http(
            request,
            route,
            set_cookie=set_cookie,
        )

    async def close(self):
        async with self._lock():
            listener = self._listener
            self._listener = None
            self._bound = False
            self._worker = None
            self._capability = None
            self._capability_expires_at = None
            if listener is not None:
                try:
                    await listener.close()
                except Exception:
                    self._error = "local_relay_unavailable"
            if self.agent_bridge is not None:
                try:
                    closed = self.agent_bridge.close()
                    if asyncio.iscoroutine(closed):
                        await closed
                except Exception:
                    self._error = "agent_bridge_unavailable"

"""Session-scoped Agent Panel bridge with a fixed loopback upstream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hmac
import inspect
import json
import math
from pathlib import PurePath
import re
from urllib.parse import urlsplit


AGENT_UPSTREAM_URL = "ws://127.0.0.1:9180"
AGENT_ENDPOINT = "/cloud-run/api/agent/ws"
AGENT_COOKIE_NAME = "comfy_vast_session"
MAX_AGENT_FRAME_BYTES = 256 * 1024
MAX_AGENT_JSON_DEPTH = 32
MAX_AGENT_COLLECTION_ITEMS = 2_000

AGENT_COMMANDS = frozenset({
    "refresh_nodes", "graph_serialize", "graph_get_state",
    "graph_view_selected", "graph_outline", "graph_query",
    "graph_find_nodes", "graph_get_subgraph", "graph_add_node",
    "graph_remove_node", "graph_clear", "graph_load", "graph_connect",
    "graph_disconnect", "graph_set_widget", "graph_set_node_property",
    "graph_move_node", "graph_resize_node", "graph_auto_layout",
    "graph_canvas", "graph_run", "graph_get_errors",
    "graph_select_nodes", "workflow_save", "workflow_save_as",
    "workflow_list", "workflow_new", "workflow_open",
    "workflow_rename", "workflow_close", "nodes_search", "nodes_list",
    "nodes_queue_status",
})

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{32,128}")
_SAFE_PANEL_TYPES = frozenset({
    "agent_event",
    "cancel_message",
    "hello",
    "interrupt",
    "new_session",
    "reorder",
    "resume_session",
    "rewind",
    "set_content_mode",
    "set_options",
    "title",
    "user_message",
})
_SAFE_ORCHESTRATOR_TYPES = frozenset({
    "ack",
    "action",
    "agent_status",
    "backends",
    "commands",
    "echo",
    "error",
    "log",
    "models",
    "say",
    "session",
    "stream",
    "thinking",
    "turn",
    "turn_anchor",
})
_FORBIDDEN_KEYS = frozenset({
    "access_key",
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "password",
    "private_key",
    "secret",
    "signed_url",
    "token",
})
_HELLO_KEYS = frozenset({
    "type",
    "tab_id",
    "title",
    "panel_version",
    "backend",
    "blind",
    "comfyui_url",
    "comfyui_path",
    "resume",
})
_PANEL_KEYS = {
    "title": frozenset({"type", "tab_id", "title"}),
    "user_message": frozenset({
        "type", "tab_id", "text", "context", "images", "mid",
    }),
    "set_options": frozenset({"type", "tab_id", "model", "effort"}),
    "set_content_mode": frozenset({"type", "tab_id", "blind"}),
    "new_session": frozenset({"type", "tab_id"}),
    "interrupt": frozenset({"type", "tab_id", "requeue"}),
    "cancel_message": frozenset({"type", "tab_id", "mid"}),
    "reorder": frozenset({"type", "tab_id", "order"}),
    "resume_session": frozenset({"type", "tab_id", "session_id"}),
    "rewind": frozenset({"type", "tab_id", "anchor"}),
    "agent_event": frozenset({
        "type", "tab_id", "kind", "error", "images", "note",
        "metadata", "prompt_id",
    }),
}
_AGENT_EVENT_KINDS = frozenset({"executed", "run_error"})


class AgentBridgeError(RuntimeError):
    """A sanitized Agent Panel bridge failure."""


class AgentBridgeAuthorizationError(AgentBridgeError):
    """The renderer did not prove its active relay capability."""


class AgentBridgePolicyError(ValueError):
    """A frame or endpoint was outside the approved Agent Panel contract."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise AgentBridgePolicyError("Agent Panel JSON was rejected.")


def _bounded_tree(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_AGENT_JSON_DEPTH:
            raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
        if isinstance(item, dict):
            if len(item) > MAX_AGENT_COLLECTION_ITEMS:
                raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 200
                    or key.casefold() in _FORBIDDEN_KEYS
                ):
                    raise AgentBridgePolicyError(
                        "Agent Panel JSON was rejected."
                    )
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_AGENT_COLLECTION_ITEMS:
                raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_AGENT_FRAME_BYTES:
                raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise AgentBridgePolicyError("Agent Panel JSON was rejected.")
        elif item is not None and not isinstance(item, (bool, int)):
            raise AgentBridgePolicyError("Agent Panel JSON was rejected.")


def _parse_json_frame(raw):
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw.encode("utf-8")) > MAX_AGENT_FRAME_BYTES
    ):
        raise AgentBridgePolicyError("Agent Panel frame was rejected.")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise AgentBridgePolicyError("Agent Panel frame was rejected.") from None
    if not isinstance(value, dict):
        raise AgentBridgePolicyError("Agent Panel frame was rejected.")
    _bounded_tree(value)
    return value


def _canonical_frame(value):
    _bounded_tree(value)
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise AgentBridgePolicyError("Agent Panel frame was rejected.") from None
    if len(rendered.encode("utf-8")) > MAX_AGENT_FRAME_BYTES:
        raise AgentBridgePolicyError("Agent Panel frame was rejected.")
    return rendered


def _identifier(value, name):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AgentBridgePolicyError(f"Invalid Agent Panel {name}.")
    return value


def _safe_string(value, name, *, optional=False, allow_empty=False):
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or (not value and not allow_empty)
        or len(value.encode("utf-8")) > 64 * 1024
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise AgentBridgePolicyError(f"Invalid Agent Panel {name}.")
    return value


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


def _cookie_capability(request):
    values = _header_values(request, "cookie")
    if len(values) != 1:
        return None
    found = []
    for component in values[0].split(";"):
        name, separator, value = component.strip().partition("=")
        if separator and name == AGENT_COOKIE_NAME:
            found.append(value)
    return found[0] if len(found) == 1 else None


def _message_type(message):
    value = getattr(message, "type", None)
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    return str(value).rsplit(".", 1)[-1].upper()


@dataclass(frozen=True, repr=False)
class AgentBridgeSession:
    session_id: str
    relay_origin: str
    capability: str = field(repr=False)
    capability_expires_at: float
    local_comfy_root: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.session_id, str) or _IDENTIFIER.fullmatch(
            self.session_id
        ) is None:
            raise AgentBridgePolicyError("Invalid Agent Panel session.")
        parsed = urlsplit(self.relay_origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query
            or parsed.fragment
        ):
            raise AgentBridgePolicyError("Invalid Agent Panel relay origin.")
        try:
            port = parsed.port
        except ValueError:
            raise AgentBridgePolicyError(
                "Invalid Agent Panel relay origin."
            ) from None
        if port is None or not 1 <= port <= 65535:
            raise AgentBridgePolicyError("Invalid Agent Panel relay origin.")
        if (
            not isinstance(self.capability, str)
            or _CAPABILITY.fullmatch(self.capability) is None
            or isinstance(self.capability_expires_at, bool)
            or not isinstance(self.capability_expires_at, (int, float))
            or not math.isfinite(self.capability_expires_at)
            or self.capability_expires_at < 0
        ):
            raise AgentBridgePolicyError("Invalid Agent Panel capability.")
        if self.local_comfy_root is not None:
            if (
                not isinstance(self.local_comfy_root, str)
                or not self.local_comfy_root
                or not PurePath(self.local_comfy_root).is_absolute()
                or len(self.local_comfy_root.encode("utf-8")) > 4096
                or any(ord(character) < 32 for character in self.local_comfy_root)
            ):
                raise AgentBridgePolicyError(
                    "Invalid approved ComfyUI root."
                )

    def __repr__(self):
        return (
            "AgentBridgeSession(session_id="
            + repr(self.session_id)
            + ", relay_origin="
            + repr(self.relay_origin)
            + ")"
        )

    @property
    def websocket_url(self):
        return "ws" + self.relay_origin[4:] + AGENT_ENDPOINT


@dataclass(frozen=True)
class AgentBridgeProbe:
    orchestrator_identity: str
    graph_read: str
    graph_edit_restore: str
    graph_run: str
    ordered_batch: str

    def __post_init__(self):
        values = (
            self.orchestrator_identity,
            self.graph_read,
            self.graph_edit_restore,
            self.graph_run,
            self.ordered_batch,
        )
        if any(item not in {"passed", "failed"} for item in values):
            raise AgentBridgePolicyError("Invalid Agent Panel probe result.")

    @property
    def ready(self):
        return all(
            item == "passed"
            for item in (
                self.orchestrator_identity,
                self.graph_read,
                self.graph_edit_restore,
                self.graph_run,
                self.ordered_batch,
            )
        )

    def public_payload(self):
        return {
            "orchestrator_identity": self.orchestrator_identity,
            "graph_read": self.graph_read,
            "graph_edit_restore": self.graph_edit_restore,
            "graph_run": self.graph_run,
            "ordered_batch": self.ordered_batch,
            "ready": self.ready,
        }


class AgentBridgePolicy:
    def __init__(self, *, upstream_url=AGENT_UPSTREAM_URL):
        if upstream_url != AGENT_UPSTREAM_URL:
            raise ValueError("Agent Panel upstream must be fixed loopback port 9180.")
        parsed = urlsplit(upstream_url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 9180
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != ""
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Agent Panel upstream must be fixed loopback port 9180.")
        self.upstream_url = upstream_url

    @staticmethod
    def _validate_reply(frame):
        if not set(frame).issubset({"rid", "ok", "result", "error"}):
            raise AgentBridgePolicyError("Agent Panel reply was rejected.")
        _identifier(frame.get("rid"), "request identity")
        if not isinstance(frame.get("ok"), bool):
            raise AgentBridgePolicyError("Agent Panel reply was rejected.")
        if frame["ok"]:
            if "error" in frame:
                raise AgentBridgePolicyError("Agent Panel reply was rejected.")
        else:
            _safe_string(frame.get("error"), "reply error")
            if "result" in frame:
                raise AgentBridgePolicyError("Agent Panel reply was rejected.")

    @staticmethod
    def _validate_optional_tab(frame):
        if "tab_id" in frame:
            _identifier(frame["tab_id"], "tab identity")

    def panel_to_orchestrator(self, raw, session):
        if not isinstance(session, AgentBridgeSession):
            raise AgentBridgePolicyError("Invalid Agent Panel session.")
        frame = _parse_json_frame(raw)
        if "rid" in frame or "ok" in frame:
            self._validate_reply(frame)
            return _canonical_frame(frame)
        kind = frame.get("type")
        if kind not in _SAFE_PANEL_TYPES:
            raise AgentBridgePolicyError("Agent Panel frame type was rejected.")
        self._validate_optional_tab(frame)
        if kind == "hello":
            if not set(frame).issubset(_HELLO_KEYS):
                raise AgentBridgePolicyError("Agent Panel hello was rejected.")
            _identifier(frame.get("tab_id"), "tab identity")
            for key in ("title", "panel_version", "backend", "resume"):
                if key in frame:
                    _safe_string(frame[key], key)
            if "blind" in frame and not isinstance(frame["blind"], bool):
                raise AgentBridgePolicyError("Agent Panel hello was rejected.")
            supplied_origin = frame.get("comfyui_url")
            if supplied_origin is not None and supplied_origin != session.relay_origin:
                raise AgentBridgePolicyError("Agent Panel target was rejected.")
            frame["comfyui_url"] = session.relay_origin
            frame.pop("comfyui_path", None)
            if session.local_comfy_root is not None:
                frame["comfyui_path"] = session.local_comfy_root
            return _canonical_frame(frame)
        allowed = _PANEL_KEYS[kind]
        if not set(frame).issubset(allowed):
            raise AgentBridgePolicyError("Agent Panel frame was rejected.")
        if kind == "title":
            _identifier(frame.get("tab_id"), "tab identity")
            _safe_string(frame.get("title"), "title", allow_empty=True)
        elif kind == "user_message":
            _safe_string(frame.get("text"), "message")
            if "context" in frame:
                _safe_string(frame["context"], "message context")
            if "mid" in frame:
                _identifier(frame["mid"], "message identity")
            if "images" in frame and not isinstance(frame["images"], list):
                raise AgentBridgePolicyError("Agent Panel images were rejected.")
        elif kind == "set_options":
            if "model" in frame:
                _safe_string(frame["model"], "model", optional=True)
            if "effort" in frame:
                _safe_string(frame["effort"], "effort", optional=True)
        elif kind == "set_content_mode":
            if not isinstance(frame.get("blind"), bool):
                raise AgentBridgePolicyError("Agent Panel mode was rejected.")
        elif kind == "interrupt":
            if "requeue" in frame and not isinstance(frame["requeue"], bool):
                raise AgentBridgePolicyError("Agent Panel interrupt was rejected.")
        elif kind == "cancel_message":
            _identifier(frame.get("mid"), "message identity")
        elif kind == "reorder":
            order = frame.get("order")
            if not isinstance(order, list):
                raise AgentBridgePolicyError("Agent Panel reorder was rejected.")
            for item in order:
                _identifier(item, "message identity")
        elif kind == "resume_session":
            _identifier(frame.get("session_id"), "resume identity")
        elif kind == "rewind":
            _identifier(frame.get("anchor"), "rewind identity")
        elif kind == "agent_event":
            if frame.get("kind") not in _AGENT_EVENT_KINDS:
                raise AgentBridgePolicyError("Agent Panel event was rejected.")
            if frame["kind"] == "run_error":
                _safe_string(frame.get("error"), "run error")
            if "prompt_id" in frame:
                _identifier(frame["prompt_id"], "prompt identity")
        return _canonical_frame(frame)

    def orchestrator_to_panel(self, raw, session):
        if not isinstance(session, AgentBridgeSession):
            raise AgentBridgePolicyError("Invalid Agent Panel session.")
        frame = _parse_json_frame(raw)
        if "rid" in frame or "cmd" in frame:
            _identifier(frame.get("rid"), "request identity")
            command = frame.get("cmd")
            if command not in AGENT_COMMANDS:
                raise AgentBridgePolicyError("Agent Panel command was rejected.")
            if "comfyui_path" in frame or "comfyuiPath" in frame:
                raise AgentBridgePolicyError("Agent Panel command was rejected.")
            for name in ("comfyui_url", "comfyuiUrl"):
                if name in frame and frame[name] != session.relay_origin:
                    raise AgentBridgePolicyError("Agent Panel target was rejected.")
            return _canonical_frame(frame)
        kind = frame.get("type")
        if kind not in _SAFE_ORCHESTRATOR_TYPES:
            raise AgentBridgePolicyError("Agent Panel content was rejected.")
        if kind == "backends":
            frame.pop("console_url", None)
            frame.pop("console_token", None)
        if (
            kind == "ack"
            and isinstance(frame.get("kind"), str)
            and frame["kind"].casefold().startswith(
                ("oauth", "pair", "secret")
            )
        ):
            raise AgentBridgePolicyError("Agent Panel content was rejected.")
        if "comfyui_path" in frame or "comfyuiPath" in frame:
            raise AgentBridgePolicyError("Agent Panel content was rejected.")
        for name in ("comfyui_url", "comfyuiUrl"):
            if name in frame and frame[name] != session.relay_origin:
                raise AgentBridgePolicyError("Agent Panel target was rejected.")
        return _canonical_frame(frame)


@dataclass(eq=False)
class _ActiveConnection:
    session_id: str
    downstream: object
    upstream: object
    revoked: bool = False


class AgentBridge:
    def __init__(
        self,
        *,
        upstream_url=AGENT_UPSTREAM_URL,
        connector=None,
        downstream_factory=None,
        probe_driver=None,
        journal=None,
        clock=None,
    ):
        self.policy = AgentBridgePolicy(upstream_url=upstream_url)
        if connector is not None and not callable(connector):
            raise AgentBridgeError("Agent Panel connector is unavailable.")
        if downstream_factory is not None and not callable(downstream_factory):
            raise AgentBridgeError("Agent Panel renderer is unavailable.")
        if probe_driver is not None and not callable(probe_driver):
            raise AgentBridgeError("Agent Panel probe is unavailable.")
        if journal is not None and not callable(journal):
            raise AgentBridgeError("Agent Panel journal is unavailable.")
        self.connector = connector
        self.downstream_factory = downstream_factory
        self.probe_driver = probe_driver
        self.journal = journal
        self.clock = clock or __import__("time").time
        self._client = None
        self._active = []
        self._revoked = set()
        self._handshakes = {}
        self._backends = {}
        self._probes = {}
        self._closed = False
        self._state_lock = None
        self._state_lock_loop = None

    def _lock(self):
        loop = asyncio.get_running_loop()
        if self._state_lock is None or self._state_lock_loop is not loop:
            self._state_lock = asyncio.Lock()
            self._state_lock_loop = loop
        return self._state_lock

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise AgentBridgeAuthorizationError(
                "Agent Panel capability could not be verified."
            )
        return float(value)

    def allow(self, session_id):
        if not isinstance(session_id, str) or _IDENTIFIER.fullmatch(session_id) is None:
            raise AgentBridgeError("Agent Panel session was rejected.")
        self._revoked.discard(session_id)

    def _authorize(self, request, session):
        if not isinstance(session, AgentBridgeSession):
            raise AgentBridgeAuthorizationError(
                "Agent Panel capability could not be verified."
            )
        parsed = urlsplit(session.relay_origin)
        expected_host = "127.0.0.1:" + str(parsed.port)
        supplied = _cookie_capability(request)
        if (
            self._closed
            or session.session_id in self._revoked
            or getattr(request, "method", None) != "GET"
            or getattr(request, "path_qs", None) != AGENT_ENDPOINT
            or _header_values(request, "host") != (expected_host,)
            or _header_values(request, "origin") != (session.relay_origin,)
            or supplied is None
            or self._now() >= session.capability_expires_at
            or not hmac.compare_digest(supplied, session.capability)
        ):
            raise AgentBridgeAuthorizationError(
                "Agent Panel capability could not be verified."
            )

    async def _connect_upstream(self):
        if self.connector is not None:
            result = self.connector(
                self.policy.upstream_url,
                headers={},
                max_msg_size=MAX_AGENT_FRAME_BYTES,
            )
            if inspect.isawaitable(result):
                result = await result
            return result
        try:
            import aiohttp
        except ImportError:
            raise AgentBridgeError("Agent Panel connector is unavailable.") from None
        if self._client is None or self._client.closed:
            self._client = aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
            )
        return await self._client.ws_connect(
            self.policy.upstream_url,
            headers={},
            max_msg_size=MAX_AGENT_FRAME_BYTES,
            autoclose=False,
            heartbeat=30,
        )

    def _new_downstream(self):
        if self.downstream_factory is not None:
            return self.downstream_factory()
        try:
            from aiohttp import web
        except ImportError:
            raise AgentBridgeError("Agent Panel renderer is unavailable.") from None
        return web.WebSocketResponse(
            heartbeat=30,
            max_msg_size=MAX_AGENT_FRAME_BYTES,
            autoclose=False,
        )

    async def _record_failure(self, session_id):
        if self.journal is None:
            return
        try:
            result = self.journal(
                session_id,
                "synchronization_error",
                "Agent Panel bridge synchronization failed.",
            )
            if inspect.isawaitable(result):
                await result
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            pass

    def _observe(self, session_id, rendered):
        try:
            frame = json.loads(rendered)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        kind = frame.get("type")
        if kind == "models":
            self._handshakes[session_id] = "models"
        elif kind == "ack" and frame.get("kind") == "degraded":
            self._handshakes[session_id] = "degraded"
        elif kind == "backends" and isinstance(frame.get("backends"), list):
            safe = []
            for item in frame["backends"][:32]:
                if not isinstance(item, dict):
                    continue
                backend = item.get("backend")
                if not isinstance(backend, str) or _IDENTIFIER.fullmatch(backend) is None:
                    continue
                record = {"backend": backend}
                for name in ("running", "ready", "experimental"):
                    if isinstance(item.get(name), bool):
                        record[name] = item[name]
                for name in ("cli", "auth"):
                    if item.get(name) is None or isinstance(item.get(name), bool):
                        record[name] = item.get(name)
                safe.append(record)
            self._backends[session_id] = tuple(safe)

    async def open(self, request, session):
        self._authorize(request, session)
        upstream = None
        downstream = None
        try:
            upstream = await self._connect_upstream()
            downstream = self._new_downstream()
            if not all(
                callable(getattr(upstream, method, None))
                for method in ("__aiter__", "send_str", "close")
            ) or not all(
                callable(getattr(downstream, method, None))
                for method in ("__aiter__", "send_str", "close", "prepare")
            ):
                raise AgentBridgeError(
                    "Agent Panel connection was rejected."
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            for socket in (upstream, downstream):
                close = getattr(socket, "close", None)
                if not callable(close):
                    continue
                try:
                    closed = close(code=1011)
                    if inspect.isawaitable(closed):
                        await closed
                except Exception:
                    pass
            await self._record_failure(session.session_id)
            raise AgentBridgeError(
                "Agent Panel connection is unavailable."
            ) from None
        connection = _ActiveConnection(
            session_id=session.session_id,
            downstream=downstream,
            upstream=upstream,
        )
        async with self._lock():
            if self._closed or session.session_id in self._revoked:
                await upstream.close(code=1008)
                await downstream.close(code=1008)
                raise AgentBridgeAuthorizationError(
                    "Agent Panel capability could not be verified."
                )
            self._active.append(connection)
        tasks = set()
        failed = False
        try:
            await downstream.prepare(request)

            async def panel_to_orchestrator():
                async for message in downstream:
                    kind = _message_type(message)
                    if kind == "TEXT":
                        rendered = self.policy.panel_to_orchestrator(
                            message.data,
                            session,
                        )
                        await upstream.send_str(rendered)
                    elif kind in {"CLOSE", "CLOSED", "CLOSING"}:
                        break
                    else:
                        raise AgentBridgePolicyError(
                            "Agent Panel frame was rejected."
                        )

            async def orchestrator_to_panel():
                async for message in upstream:
                    kind = _message_type(message)
                    if kind == "TEXT":
                        rendered = self.policy.orchestrator_to_panel(
                            message.data,
                            session,
                        )
                        self._observe(session.session_id, rendered)
                        await downstream.send_str(rendered)
                    elif kind in {"CLOSE", "CLOSED", "CLOSING"}:
                        break
                    else:
                        raise AgentBridgePolicyError(
                            "Agent Panel frame was rejected."
                        )

            tasks = {
                asyncio.create_task(panel_to_orchestrator()),
                asyncio.create_task(orchestrator_to_panel()),
            }
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
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
                results = await asyncio.gather(*pending, return_exceptions=True)
                if any(
                    isinstance(item, Exception)
                    and not isinstance(item, asyncio.CancelledError)
                    for item in results
                ):
                    failed = True
        except (asyncio.CancelledError, KeyboardInterrupt):
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception:
            failed = True
        finally:
            async with self._lock():
                if connection in self._active:
                    self._active.remove(connection)
            if failed and not connection.revoked:
                await self._record_failure(session.session_id)
            code = 1011 if failed and not connection.revoked else 1000
            for socket in (upstream, downstream):
                try:
                    await socket.close(code=code)
                except Exception:
                    pass
        return downstream

    async def revoke(self, session_id):
        if not isinstance(session_id, str) or _IDENTIFIER.fullmatch(session_id) is None:
            raise AgentBridgeError("Agent Panel session was rejected.")
        async with self._lock():
            self._revoked.add(session_id)
            connections = [
                item for item in self._active if item.session_id == session_id
            ]
            for item in connections:
                item.revoked = True
        for item in connections:
            for socket in (item.upstream, item.downstream):
                try:
                    await socket.close(code=1000)
                except Exception:
                    pass
        self._handshakes.pop(session_id, None)
        self._backends.pop(session_id, None)
        self._probes.pop(session_id, None)

    def compatibility(self, path, session):
        if not isinstance(session, AgentBridgeSession):
            raise AgentBridgeError("Agent Panel session was rejected.")
        if path not in {
            "/comfyui_mcp_panel/status",
            "/comfyui_mcp_panel/bridge_url",
            "/comfyui_mcp_panel/backends",
        }:
            raise AgentBridgeError("Agent Panel compatibility route was rejected.")
        running = (
            session.session_id in self._handshakes
            or any(item.session_id == session.session_id for item in self._active)
            or bool(getattr(self._probes.get(session.session_id), "ready", False))
        )
        if path == "/comfyui_mcp_panel/status":
            return {
                "running": running,
                "port": urlsplit(session.relay_origin).port,
                "can_spawn": False,
                "bridge_url": session.websocket_url,
                "comfyui_url": session.relay_origin,
                "comfyui_path": "",
                "start_command": "",
            }
        if path == "/comfyui_mcp_panel/bridge_url":
            return {"url": session.websocket_url}
        backends = list(self._backends.get(session.session_id, ()))
        if not backends:
            backends = [
                {
                    "backend": "claude",
                    "running": running,
                    "cli": None,
                    "auth": None,
                    "ready": running,
                }
            ]
        return {
            "backends": backends,
            "any_ready": any(item.get("ready") is True for item in backends),
            "can_spawn": False,
            "start_command": "",
        }

    @staticmethod
    def _probe_requests(session):
        return (
            {
                "type": "hello",
                "tab_id": "cloud-vast-probe",
                "title": "Cloud Vast readiness probe",
                "panel_version": "cloud-vast",
                "backend": "claude",
                "blind": True,
                "comfyui_url": session.relay_origin,
            },
            {"rid": "probe-read", "cmd": "graph_get_state"},
            {
                "rid": "probe-edit",
                "cmd": "graph_set_widget",
                "probe_fixture": True,
                "restore": True,
            },
            {
                "rid": "probe-run",
                "cmd": "graph_run",
                "probe_fixture": True,
                "batch_count": 1,
            },
            {
                "rid": "probe-batch",
                "cmd": "graph_run",
                "probe_fixture": True,
                "batch_count": 2,
            },
        )

    async def probe(self, session):
        if not isinstance(session, AgentBridgeSession):
            raise AgentBridgeError("Agent Panel probe session was rejected.")
        failed = AgentBridgeProbe(
            orchestrator_identity="failed",
            graph_read="failed",
            graph_edit_restore="failed",
            graph_run="failed",
            ordered_batch="failed",
        )
        if not callable(self.probe_driver):
            self._probes[session.session_id] = failed
            return failed
        requests = self._probe_requests(session)
        try:
            response = self.probe_driver(session, requests)
            if inspect.isawaitable(response):
                response = await response
            if not isinstance(response, (tuple, list)):
                raise AgentBridgePolicyError("Agent Panel probe was rejected.")
            frames = []
            for index, item in enumerate(response):
                raw = _canonical_frame(item)
                if index == 0:
                    frames.append(json.loads(self.policy.orchestrator_to_panel(raw, session)))
                else:
                    frames.append(json.loads(self.policy.panel_to_orchestrator(raw, session)))
            handshake = frames[0] if frames else {}
            identity = (
                handshake.get("type") == "models"
                and isinstance(handshake.get("models"), list)
            ) or (
                handshake.get("type") == "ack"
                and handshake.get("kind") == "degraded"
            )
            replies = {
                item.get("rid"): item
                for item in frames[1:]
                if isinstance(item, dict) and isinstance(item.get("rid"), str)
            }
            read = replies.get("probe-read", {})
            edit = replies.get("probe-edit", {})
            run = replies.get("probe-run", {})
            batch = replies.get("probe-batch", {})
            read_ok = read.get("ok") is True and isinstance(read.get("result"), dict)
            edit_result = edit.get("result") if edit.get("ok") is True else None
            edit_ok = (
                isinstance(edit_result, dict)
                and edit_result.get("observed") is True
                and edit_result.get("restored") is True
            )
            run_result = run.get("result") if run.get("ok") is True else None
            run_ids = run_result.get("job_ids") if isinstance(run_result, dict) else None
            run_ok = (
                isinstance(run_result, dict)
                and run_result.get("queued") is True
                and isinstance(run_ids, list)
                and len(run_ids) == 1
                and all(
                    isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                    for item in run_ids
                )
            )
            batch_result = batch.get("result") if batch.get("ok") is True else None
            batch_ids = (
                batch_result.get("job_ids")
                if isinstance(batch_result, dict)
                else None
            )
            batch_ok = (
                isinstance(batch_result, dict)
                and batch_result.get("queued") is True
                and batch_result.get("batch_count") == 2
                and isinstance(batch_ids, list)
                and len(batch_ids) == 2
                and len(set(batch_ids)) == 2
                and all(
                    isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                    for item in batch_ids
                )
            )
            report = AgentBridgeProbe(
                orchestrator_identity="passed" if identity else "failed",
                graph_read="passed" if read_ok else "failed",
                graph_edit_restore="passed" if edit_ok else "failed",
                graph_run="passed" if run_ok else "failed",
                ordered_batch="passed" if batch_ok else "failed",
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            report = failed
        self._probes[session.session_id] = report
        return report

    async def close(self):
        async with self._lock():
            self._closed = True
            connections = list(self._active)
            for item in connections:
                item.revoked = True
            self._active.clear()
        for item in connections:
            for socket in (item.upstream, item.downstream):
                try:
                    await socket.close(code=1000)
                except Exception:
                    pass
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
        self._handshakes.clear()
        self._backends.clear()
        self._probes.clear()

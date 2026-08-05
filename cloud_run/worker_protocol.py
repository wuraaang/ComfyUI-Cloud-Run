"""Canonical replay-resistant HMAC authentication for Remote Worker HTTP."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import re
import uuid

from .manifest import PINNED_COMFYUI_FRONTEND_VERSION, PROTOCOL_VERSION


MAX_CAPTURE_BYTES = 16 * 1024 * 1024
PINNED_FRONTEND_VERSION = PINNED_COMFYUI_FRONTEND_VERSION
BOUNDARY_TOKEN_ENVIRONMENT = "CLOUD_RUN_BOUNDARY_TOKEN"
SESSION_ID_ENVIRONMENT = "CLOUD_RUN_SESSION_ID"
_BOUNDARY_TOKEN = re.compile(r"[0-9a-f]{64}")
_WORKER_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
ALLOWED_QUEUE_OPTIONS = {
    "front",
    "number",
    "partial_execution_targets",
    "preview_method",
}
FORBIDDEN_KEYS = {
    "access_token",
    "auth_token_comfy_org",
    "api_key_comfy_org",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "password",
    "private_key",
    "secret",
    "signed_url",
    "token",
}
MAX_NESTING_DEPTH = 64
_NATIVE_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_NATIVE_PROMPT_FIELDS = {
    "client_id",
    "prompt",
    "extra_data",
    "front",
    "number",
    "partial_execution_targets",
}
_NATIVE_EXTRA_DATA_FIELDS = {
    "comfy_usage_source",
    "extra_pnginfo",
    "preview_method",
}


def is_boundary_token(value):
    return (
        isinstance(value, str)
        and _BOUNDARY_TOKEN.fullmatch(value) is not None
    )


def is_worker_session_id(value):
    return (
        isinstance(value, str)
        and _WORKER_SESSION_ID.fullmatch(value) is not None
    )


class CaptureValidationError(ValueError):
    """A native ComfyUI capture failed the shared worker boundary."""


def canonical_json(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError):
        raise CaptureValidationError(
            "Invalid compiled canvas payload."
        ) from None


def prompt_digest(output, queue_options):
    material = canonical_json(
        {
            "output": output,
            "queue_options": queue_options,
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _validate_tree(root):
    stack = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            raise CaptureValidationError(
                "Compiled canvas nesting is too deep."
            )
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise CaptureValidationError(
                        "Compiled canvas object keys must be strings."
                    )
                if key.casefold() in FORBIDDEN_KEYS:
                    raise CaptureValidationError(
                        "Compiled canvas contains forbidden credential data."
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise CaptureValidationError(
                "Compiled canvas numbers must be finite."
            )
        elif value is not None and not isinstance(
            value,
            (bool, int, float, str),
        ):
            raise CaptureValidationError(
                "Compiled canvas contains an unsupported value."
            )


def _validate_identifier(value, name, *, maximum=256):
    if not isinstance(value, str):
        raise CaptureValidationError(f"Invalid {name}.")
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise CaptureValidationError(f"Invalid {name}.")
    return value


def _validate_queue_options(queue_options, output_ids):
    if not isinstance(queue_options, dict):
        raise CaptureValidationError("Queue options must be an object.")
    if set(queue_options) - ALLOWED_QUEUE_OPTIONS:
        raise CaptureValidationError("Unsupported queue option.")
    if "front" in queue_options and not isinstance(
        queue_options["front"],
        bool,
    ):
        raise CaptureValidationError("Invalid front queue option.")
    if "number" in queue_options:
        number = queue_options["number"]
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or number == 0
        ):
            raise CaptureValidationError("Invalid queue number.")
    if queue_options.get("front") and "number" in queue_options:
        raise CaptureValidationError("Conflicting queue priority options.")
    if "partial_execution_targets" in queue_options:
        targets = queue_options["partial_execution_targets"]
        if not isinstance(targets, list) or not targets:
            raise CaptureValidationError(
                "Invalid partial execution targets."
            )
        normalized = [
            _validate_identifier(str(target), "partial execution target")
            for target in targets
        ]
        if len(set(normalized)) != len(normalized):
            raise CaptureValidationError(
                "Partial execution targets must be unique."
            )
        if not set(normalized).issubset(output_ids):
            raise CaptureValidationError(
                "Partial execution target is absent from the prompt."
            )
    if "preview_method" in queue_options:
        _validate_identifier(
            queue_options["preview_method"],
            "preview method",
            maximum=128,
        )


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise CaptureValidationError("Invalid native prompt JSON.")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise CaptureValidationError("Invalid native prompt JSON.")


def _native_prompt_payload(body):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_CAPTURE_BYTES:
        raise CaptureValidationError("Invalid native prompt body.")
    try:
        parsed = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (CaptureValidationError, UnicodeError, ValueError, json.JSONDecodeError):
        raise CaptureValidationError("Invalid native prompt body.") from None
    if (
        not isinstance(parsed, dict)
        or not {"client_id", "prompt", "extra_data"}.issubset(parsed)
        or set(parsed) - _NATIVE_PROMPT_FIELDS
        or not isinstance(parsed.get("client_id"), str)
        or _NATIVE_CLIENT_ID.fullmatch(parsed["client_id"]) is None
        or not isinstance(parsed.get("extra_data"), dict)
        or set(parsed["extra_data"]) - _NATIVE_EXTRA_DATA_FIELDS
    ):
        raise CaptureValidationError("Invalid native prompt body.")
    extra_data = parsed["extra_data"]
    extra_pnginfo = extra_data.get("extra_pnginfo")
    if (
        not isinstance(extra_pnginfo, dict)
        or set(extra_pnginfo) != {"workflow"}
    ):
        raise CaptureValidationError("Invalid native prompt body.")
    if "comfy_usage_source" in extra_data:
        _validate_identifier(
            extra_data["comfy_usage_source"],
            "Comfy usage source",
            maximum=200,
        )
    _validate_tree(parsed)
    encoded = canonical_json(parsed)
    if not 0 < len(encoded.encode("utf-8")) <= MAX_CAPTURE_BYTES:
        raise CaptureValidationError("Invalid native prompt body.")
    return json.loads(encoded), encoded.encode("utf-8")


def canonical_native_prompt_body(body):
    """Validate and canonicalize one pinned native ComfyUI prompt request."""

    _parsed, encoded = _native_prompt_payload(body)
    return encoded


@dataclass(frozen=True)
class CompiledCapture:
    capture_id: str
    workflow: dict
    output: dict
    queue_options: dict
    prompt_digest: str
    executable_class_types: tuple[str, ...]

    @classmethod
    def from_native_prompt(cls, body, *, capture_id=None):
        parsed, _encoded = _native_prompt_payload(body)
        extra_data = parsed["extra_data"]
        queue_options = {
            key: parsed[key]
            for key in ("front", "number", "partial_execution_targets")
            if key in parsed
        }
        if "preview_method" in extra_data:
            queue_options["preview_method"] = extra_data["preview_method"]
        return cls.from_payload(
            {
                "workflow": extra_data["extra_pnginfo"]["workflow"],
                "output": parsed["prompt"],
                "queue_options": queue_options,
            },
            capture_id=capture_id,
        )

    @classmethod
    def from_payload(cls, payload, *, capture_id=None):
        if not isinstance(payload, dict) or set(payload) != {
            "workflow",
            "output",
            "queue_options",
        }:
            raise CaptureValidationError(
                "Compiled canvas payload fields are invalid."
            )
        _validate_tree(payload)
        encoded = canonical_json(payload)
        if len(encoded.encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise CaptureValidationError(
                "Compiled canvas payload is too large."
            )
        detached = json.loads(encoded)
        workflow = detached["workflow"]
        output = detached["output"]
        queue_options = detached["queue_options"]
        if not isinstance(workflow, dict):
            raise CaptureValidationError("Workflow must be an object.")
        extra = workflow.get("extra")
        if (
            not isinstance(extra, dict)
            or extra.get("frontendVersion") != PINNED_FRONTEND_VERSION
        ):
            raise CaptureValidationError(
                "Pinned ComfyUI frontend version is required."
            )
        if not isinstance(workflow.get("nodes"), list):
            raise CaptureValidationError("Workflow nodes must be a list.")
        if not isinstance(output, dict) or not output:
            raise CaptureValidationError(
                "Executable prompt must not be empty."
            )

        class_types = set()
        output_ids = set()
        for node_id, node in output.items():
            output_ids.add(
                _validate_identifier(node_id, "prompt node ID")
            )
            if not isinstance(node, dict):
                raise CaptureValidationError(
                    "Prompt nodes must be objects."
                )
            class_type = _validate_identifier(
                node.get("class_type"),
                "prompt class type",
            )
            if not isinstance(node.get("inputs"), dict):
                raise CaptureValidationError(
                    "Prompt node inputs must be objects."
                )
            class_types.add(class_type)

        _validate_queue_options(queue_options, output_ids)
        identifier = str(capture_id or uuid.uuid4())
        try:
            identifier = str(uuid.UUID(identifier))
        except (AttributeError, TypeError, ValueError):
            raise CaptureValidationError(
                "Invalid capture identity."
            ) from None
        return cls(
            capture_id=identifier,
            workflow=workflow,
            output=output,
            queue_options=queue_options,
            prompt_digest=prompt_digest(output, queue_options),
            executable_class_types=tuple(sorted(class_types)),
        )

    def canonical_payload(self):
        return canonical_json(
            {
                "workflow": self.workflow,
                "output": self.output,
                "queue_options": self.queue_options,
            }
        )

    @classmethod
    def from_record(cls, capture_id, capture_json, expected_prompt_digest):
        try:
            payload = json.loads(capture_json)
        except (json.JSONDecodeError, TypeError):
            raise CaptureValidationError(
                "Stored capture is invalid."
            ) from None
        capture = cls.from_payload(payload, capture_id=capture_id)
        if not re.fullmatch(
            r"[0-9a-f]{64}",
            str(expected_prompt_digest or ""),
        ):
            raise CaptureValidationError(
                "Stored prompt digest is invalid."
            )
        if capture.prompt_digest != expected_prompt_digest:
            raise CaptureValidationError(
                "Stored capture digest does not match."
            )
        return capture

MAX_CLOCK_SKEW_SECONDS = 30
MAX_NONCE_CACHE_ENTRIES = 4096
_NONCE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_METHOD = re.compile(r"[A-Z]{1,16}")
_ENVELOPE_FIELDS = {
    "protocol_version",
    "timestamp",
    "nonce",
    "signature",
}
_NATIVE_IDENTITY_FIELDS = {
    "job_id",
    "request_id",
    "manifest_digest",
}
_NATIVE_DIGEST = re.compile(r"[0-9a-f]{64}")


class ProtocolAuthenticationError(ValueError):
    """A sanitized authentication or replay failure."""


def _authentication_error():
    return ProtocolAuthenticationError(
        "Worker request authentication failed."
    )


def native_request_material(body, identity):
    """Bind server-only native prompt identity to one HTTP body digest."""

    if not isinstance(body, bytes) or not isinstance(identity, dict):
        raise _authentication_error()
    if identity:
        if set(identity) != _NATIVE_IDENTITY_FIELDS:
            raise _authentication_error()
        if (
            not is_worker_session_id(identity.get("job_id"))
            or not is_worker_session_id(identity.get("request_id"))
            or not isinstance(identity.get("manifest_digest"), str)
            or _NATIVE_DIGEST.fullmatch(identity["manifest_digest"])
            is None
        ):
            raise _authentication_error()
    try:
        metadata = canonical_json(
            {
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "identity": dict(identity),
            }
        ).encode("utf-8")
    except CaptureValidationError:
        raise _authentication_error() from None
    return b"cloud-vast-native-v2\n" + metadata


def _secret(value):
    if not isinstance(value, bytes) or len(value) != 32:
        raise _authentication_error()
    return value


def _request_parts(method, path, body, timestamp, nonce):
    if (
        not isinstance(method, str)
        or not _METHOD.fullmatch(method.upper())
        or not isinstance(path, str)
        or not path.startswith("/")
        or len(path) > 2048
        or any(character in path for character in "\r\n")
        or not isinstance(body, bytes)
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, int)
        or not isinstance(nonce, str)
        or not _NONCE.fullmatch(nonce)
    ):
        raise _authentication_error()
    return method.upper(), path, body, timestamp, nonce


def signing_material(method, path, body, timestamp, nonce):
    method, path, body, timestamp, nonce = _request_parts(
        method,
        path,
        body,
        timestamp,
        nonce,
    )
    return "\n".join(
        (
            method,
            path,
            hashlib.sha256(body).hexdigest(),
            str(timestamp),
            nonce,
        )
    ).encode("utf-8")


def sign_request(secret, method, path, body, *, timestamp, nonce):
    key = _secret(secret)
    material = signing_material(method, path, body, timestamp, nonce)
    signature = hmac.new(key, material, hashlib.sha256).hexdigest()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
    }


class NonceCache:
    """A bounded fail-closed cache that never evicts a live replay guard."""

    def __init__(self, *, max_entries=MAX_NONCE_CACHE_ENTRIES):
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or max_entries <= 0
            or max_entries > MAX_NONCE_CACHE_ENTRIES
        ):
            raise ValueError("Invalid nonce cache capacity.")
        self.max_entries = max_entries
        self._entries = OrderedDict()

    def __len__(self):
        return len(self._entries)

    def check_and_add(self, nonce, timestamp, now):
        expired = [
            value
            for value, recorded_at in self._entries.items()
            if now - recorded_at > MAX_CLOCK_SKEW_SECONDS
        ]
        for value in expired:
            self._entries.pop(value, None)
        if nonce in self._entries or len(self._entries) >= self.max_entries:
            return False
        self._entries[nonce] = timestamp
        return True


def _remember_nonce(seen_nonces, nonce, timestamp, now):
    if isinstance(seen_nonces, NonceCache):
        return seen_nonces.check_and_add(nonce, timestamp, now)
    if not isinstance(seen_nonces, set):
        raise _authentication_error()
    if (
        nonce in seen_nonces
        or len(seen_nonces) >= MAX_NONCE_CACHE_ENTRIES
    ):
        return False
    seen_nonces.add(nonce)
    return True


def verify_request(
    secret,
    method,
    path,
    body,
    envelope,
    *,
    now,
    seen_nonces,
):
    key = _secret(secret)
    if (
        not isinstance(envelope, dict)
        or set(envelope) != _ENVELOPE_FIELDS
        or envelope.get("protocol_version") != PROTOCOL_VERSION
        or isinstance(now, bool)
        or not isinstance(now, (int, float))
        or not math.isfinite(now)
    ):
        raise _authentication_error()
    timestamp = envelope.get("timestamp")
    nonce = envelope.get("nonce")
    signature = envelope.get("signature")
    try:
        material = signing_material(
            method,
            path,
            body,
            timestamp,
            nonce,
        )
    except ProtocolAuthenticationError:
        raise _authentication_error() from None
    if (
        abs(float(now) - timestamp) > MAX_CLOCK_SKEW_SECONDS
        or not isinstance(signature, str)
        or not re.fullmatch(r"[0-9a-f]{64}", signature)
    ):
        raise _authentication_error()
    expected = hmac.new(key, material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _authentication_error()
    if not _remember_nonce(
        seen_nonces,
        nonce,
        timestamp,
        float(now),
    ):
        raise _authentication_error()

"""Stable, secret-safe error values shared by controller and worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
import re
from typing import Protocol


MAX_SAFE_TEXT_BYTES = 32 * 1024
MAX_SAFE_LOG_LINES = 64
MAX_JOURNAL_DETAILS_BYTES = 64 * 1024


class RunPhase(str, Enum):
    PREFLIGHT = "preflight"
    QUOTE = "quote"
    PROVIDER = "provider"
    BOOTSTRAP = "bootstrap"
    TRANSFER = "transfer"
    PROVISIONING = "provisioning"
    READINESS = "readiness"
    EXECUTION = "execution"
    SYNCHRONIZATION = "synchronization"
    HARVEST = "harvest"
    TEARDOWN = "teardown"
    INTERNAL = "internal"


class RunErrorCode(str, Enum):
    VALIDATION = "validation_error"
    DEPENDENCY = "dependency_error"
    TRANSFER = "transfer_error"
    QUOTE_EXPIRED = "quote_expired"
    PROVIDER = "provider_error"
    PROVISIONING = "provisioning_error"
    COMFY_STARTUP = "comfy_startup_error"
    EXECUTION = "execution_error"
    SYNCHRONIZATION = "synchronization_error"
    HARVEST = "harvest_error"
    INVALID_OUTPUT = "invalid_output"
    WORKER_RESTART = "worker_restart_error"
    LIFECYCLE = "lifecycle_error"
    INTERNAL = "internal_error"


_AUTHORIZATION = re.compile(
    r"(?i)\bauthorization\s*:\s*[^\r\n]+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|password|secret|token)"
    r"\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:x-amz-(?:signature|credential|security-token)|"
    r"signature|signed|token|api[_-]?key|secret)=)[^&#\s]+"
)
_POSIX_USER_PATH = re.compile(r"/(?:Users|home)/[^/\s]+(?:/[^\s]*)?")
_WINDOWS_USER_PATH = re.compile(
    r"(?i)\b[A-Z]:\\Users\\[^\\\s]+(?:\\[^\s]*)?"
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SENSITIVE_DETAIL_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "access_key",
        "access_token",
        "bearer",
        "cookie",
        "password",
        "secret",
        "signed_url",
        "token",
    }
)


def _bounded_utf8(value, limit):
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    suffix = b"...[truncated]"
    prefix = encoded[: max(0, limit - len(suffix))]
    return prefix.decode("utf-8", errors="ignore") + suffix.decode("ascii")


def sanitize_text(value, *, local_roots=()):
    """Return bounded diagnostic text with known secret shapes removed."""

    text = str(value or "")
    for root in sorted(
        {str(Path(root)) for root in local_roots if str(root)},
        key=len,
        reverse=True,
    ):
        text = text.replace(root, "[local-root]")
    text = _AUTHORIZATION.sub("Authorization: [redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: match.group(1) + "=[redacted]",
        text,
    )
    text = _SECRET_QUERY.sub(
        lambda match: match.group(1) + "[redacted]",
        text,
    )
    text = _POSIX_USER_PATH.sub("[local-path]", text)
    text = _WINDOWS_USER_PATH.sub("[local-path]", text)
    text = "".join(
        character
        if character in "\n\t" or ord(character) >= 32
        else " "
        for character in text
    )
    return _bounded_utf8(text, MAX_SAFE_TEXT_BYTES)


def _safe_text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {name}.")
    if sanitize_text(value) != value:
        raise ValueError(f"Invalid {name}.")
    if len(value.encode("utf-8")) > MAX_SAFE_TEXT_BYTES:
        raise ValueError(f"Invalid {name}.")
    return value


def _identifier(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name}.")
    return value


def _nonnegative_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Invalid {name}.")
    return value


def _safe_details(value):
    def normalize(item, *, depth=0):
        if depth > 12:
            raise ValueError("Invalid journal details.")
        if item is None or isinstance(item, bool) or isinstance(item, int):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("Invalid journal details.")
            return item
        if isinstance(item, str):
            return _safe_text(item, "journal detail")
        if isinstance(item, list):
            if len(item) > 1_000:
                raise ValueError("Invalid journal details.")
            return [normalize(child, depth=depth + 1) for child in item]
        if isinstance(item, dict):
            if len(item) > 1_000:
                raise ValueError("Invalid journal details.")
            normalized = {}
            for key in sorted(item):
                if not isinstance(key, str) or not key or len(key) > 200:
                    raise ValueError("Invalid journal details.")
                folded = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
                components = set(folded.split("_"))
                if (
                    folded in _SENSITIVE_DETAIL_KEYS
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
                    raise ValueError("Invalid journal details.")
                normalized[key] = normalize(item[key], depth=depth + 1)
            return normalized
        raise ValueError("Invalid journal details.")

    normalized = normalize(value)
    if not isinstance(normalized, dict):
        raise ValueError("Invalid journal details.")
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_JOURNAL_DETAILS_BYTES:
        raise ValueError("Invalid journal details.")
    return json.loads(encoded)


@dataclass(frozen=True)
class SafeRunError:
    code: RunErrorCode
    phase: RunPhase
    message: str
    correlation_id: str
    node_id: str | None
    retryable: bool

    def __post_init__(self):
        try:
            code = RunErrorCode(self.code)
            phase = RunPhase(self.phase)
        except (TypeError, ValueError):
            raise ValueError("Invalid run error classification.") from None
        if not isinstance(self.retryable, bool):
            raise ValueError("Invalid retryable flag.")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "message", _safe_text(self.message, "message"))
        object.__setattr__(
            self,
            "correlation_id",
            _identifier(self.correlation_id, "correlation ID"),
        )
        object.__setattr__(
            self,
            "node_id",
            _identifier(self.node_id, "node ID", optional=True),
        )


@dataclass(frozen=True)
class RunJournalEntry:
    entry_id: str
    session_id: str | None
    manifest_digest: str | None
    transaction_id: str | None
    job_id: str | None
    phase: RunPhase
    code: RunErrorCode
    message: str
    node_id: str | None
    process_exit_code: int | None
    restart_count: int
    last_probe: str | None
    byte_cursor: int
    event_cursor: int
    output_state: str | None
    details: dict
    created_at: float

    def __post_init__(self):
        try:
            phase = RunPhase(self.phase)
            code = RunErrorCode(self.code)
        except (TypeError, ValueError):
            raise ValueError("Invalid journal classification.") from None
        digest = self.manifest_digest
        if digest is not None and (
            not isinstance(digest, str) or not _HEX_64.fullmatch(digest)
        ):
            raise ValueError("Invalid manifest digest.")
        exit_code = self.process_exit_code
        if exit_code is not None and (
            isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not -(2**31) <= exit_code < 2**31
        ):
            raise ValueError("Invalid process exit code.")
        timestamp = self.created_at
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise ValueError("Invalid journal timestamp.")
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "entry_id", _identifier(self.entry_id, "entry ID"))
        object.__setattr__(
            self,
            "session_id",
            _identifier(self.session_id, "session ID", optional=True),
        )
        object.__setattr__(
            self,
            "transaction_id",
            _identifier(self.transaction_id, "transaction ID", optional=True),
        )
        object.__setattr__(
            self,
            "job_id",
            _identifier(self.job_id, "job ID", optional=True),
        )
        object.__setattr__(self, "message", _safe_text(self.message, "message"))
        object.__setattr__(
            self,
            "node_id",
            _identifier(self.node_id, "node ID", optional=True),
        )
        object.__setattr__(
            self,
            "restart_count",
            _nonnegative_integer(self.restart_count, "restart count"),
        )
        object.__setattr__(
            self,
            "last_probe",
            _safe_text(self.last_probe, "last probe", optional=True),
        )
        object.__setattr__(
            self,
            "byte_cursor",
            _nonnegative_integer(self.byte_cursor, "byte cursor"),
        )
        object.__setattr__(
            self,
            "event_cursor",
            _nonnegative_integer(self.event_cursor, "event cursor"),
        )
        object.__setattr__(
            self,
            "output_state",
            _identifier(self.output_state, "output state", optional=True),
        )
        object.__setattr__(self, "details", _safe_details(self.details))
        object.__setattr__(self, "created_at", float(timestamp))


class JournalPort(Protocol):
    def record(self, entry: RunJournalEntry) -> bool:
        ...

"""Canonical replay-resistant HMAC authentication for Remote Worker HTTP."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import hmac
import math
import re

from .manifest import PROTOCOL_VERSION

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


class ProtocolAuthenticationError(ValueError):
    """A sanitized authentication or replay failure."""


def _authentication_error():
    return ProtocolAuthenticationError(
        "Worker request authentication failed."
    )


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

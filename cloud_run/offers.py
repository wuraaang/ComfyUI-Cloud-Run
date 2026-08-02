"""Deterministic offer selection and private host blacklist policy."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import time

from .constants import (
    MIN_VAST_INET_DOWN_MBPS,
    MIN_VAST_RELIABILITY,
    PREFERRED_VAST_INET_DOWN_MBPS,
)


PRICE_BAIT_RATIO = 0.60
DEFAULT_BLACKLIST_TTL_SECONDS = 3 * 24 * 60 * 60
_IDENTITY_FIELDS = ("machine_id", "host_id", "public_ipaddr")
_SAFE_REASONS = {
    "boot_timeout",
    "healthcheck_failure",
    "manual",
    "provider_failure",
    "transport_failure",
}


class OfferSelectionError(RuntimeError):
    """No eligible offer can safely satisfy the requested GPU class."""


def _finite(value, default=0.0):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return float(default)
    return float(value)


def _identity(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text[:160] if text else None


def _offer_identity(offer, field):
    if not isinstance(offer, dict):
        return None
    return _identity(offer.get(field))


def _offer_id_key(offer):
    value = offer.get("offer_id") if isinstance(offer, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, value)
    return (1, str(value or ""))


def _dlperf_key(offer):
    value = offer.get("dlperf") if isinstance(offer, dict) else None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return (1, 0.0)
    return (0, -float(value))


def offer_quality_key(offer):
    down = _finite(offer.get("inet_down_mbps"), default=-math.inf)
    return (
        *_dlperf_key(offer),
        -min(down, PREFERRED_VAST_INET_DOWN_MBPS),
        -_finite(offer.get("reliability")),
        -_finite(offer.get("disk_bw_mbps")),
        _finite(offer.get("dph_total"), default=math.inf),
        _offer_id_key(offer),
    )


def offer_meets_connection_quality_policy(offer):
    if not isinstance(offer, dict):
        return False
    return (
        _finite(offer.get("reliability"), default=-math.inf)
        >= MIN_VAST_RELIABILITY
        and _finite(offer.get("inet_down_mbps"), default=-math.inf)
        >= MIN_VAST_INET_DOWN_MBPS
    )


def estimated_transfer_seconds(transfer_bytes, inet_down_mbps):
    if type(transfer_bytes) is not int or transfer_bytes < 0:
        return None
    speed = _finite(inet_down_mbps, default=-1)
    if speed <= 0:
        return None
    return math.ceil(transfer_bytes * 8 / (speed * 1_000_000))


class HostBlacklist:
    """Private, expiring machine/host/address denylist."""

    def __init__(self, path, default_ttl_seconds=DEFAULT_BLACKLIST_TTL_SECONDS):
        self.path = Path(path)
        self.default_ttl_seconds = float(default_ttl_seconds)

    def _load(self, now):
        if not self.path.exists():
            return []
        try:
            os.chmod(self.path, 0o600)
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict) or not isinstance(
            payload.get("entries"), list
        ):
            return []

        entries = []
        expired = False
        for raw in payload["entries"]:
            if not isinstance(raw, dict):
                continue
            expires_at = _finite(raw.get("expires_at"), default=-1)
            identities = {
                field: _identity(raw.get(field)) for field in _IDENTITY_FIELDS
            }
            if expires_at <= float(now):
                expired = True
                continue
            if not any(identities.values()):
                continue
            entries.append(
                {
                    **identities,
                    "expires_at": expires_at,
                    "reason": (
                        raw.get("reason")
                        if raw.get("reason") in _SAFE_REASONS
                        else "provider_failure"
                    ),
                }
            )
        if expired:
            try:
                self._write(entries)
            except OSError:
                pass
        return entries

    def _write(self, entries):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".blacklist-",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump(
                    {"version": 1, "entries": entries},
                    handle,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            os.chmod(self.path, 0o600)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def add(self, offer, *, reason="provider_failure", ttl_seconds=None, now=None):
        timestamp = float(time.time() if now is None else now)
        ttl = (
            self.default_ttl_seconds
            if ttl_seconds is None
            else _finite(ttl_seconds, default=-1)
        )
        if ttl <= 0:
            raise ValueError("Blacklist TTL must be positive.")
        identities = {
            field: _offer_identity(offer, field) for field in _IDENTITY_FIELDS
        }
        if not any(identities.values()):
            return False

        entries = self._load(timestamp)
        entries = [
            entry
            for entry in entries
            if not any(
                identities[field]
                and entry.get(field) == identities[field]
                for field in _IDENTITY_FIELDS
            )
        ]
        entries.append(
            {
                **identities,
                "expires_at": timestamp + ttl,
                "reason": reason if reason in _SAFE_REASONS else "provider_failure",
            }
        )
        entries.sort(
            key=lambda entry: tuple(entry.get(field) or "" for field in _IDENTITY_FIELDS)
        )
        self._write(entries)
        return True

    def contains(self, offer, *, now=None):
        timestamp = float(time.time() if now is None else now)
        identities = {
            field: _offer_identity(offer, field) for field in _IDENTITY_FIELDS
        }
        for entry in self._load(timestamp):
            if any(
                identities[field] and entry.get(field) == identities[field]
                for field in _IDENTITY_FIELDS
            ):
                return True
        return False


def _remove_bait_prices(offers):
    by_class = {}
    for offer in offers:
        name = str(offer.get("gpu_name") or "").strip().casefold()
        by_class.setdefault(name, []).append(offer)

    kept = []
    for group in by_class.values():
        prices = [
            _finite(offer.get("dph_total"), default=-1)
            for offer in group
            if _finite(offer.get("dph_total"), default=-1) >= 0
        ]
        if len(prices) >= 3:
            floor = statistics.median(prices) * PRICE_BAIT_RATIO
            group = [
                offer
                for offer in group
                if _finite(offer.get("dph_total"), default=-1) >= floor
            ]
        kept.extend(group)
    return kept


def apply_offer_policy(offers, *, blacklist=None, now=None):
    """Exclude known-bad identities and suspicious prices, then sort stably."""
    candidates = [offer for offer in offers if isinstance(offer, dict)]
    if blacklist is not None:
        candidates = [
            offer
            for offer in candidates
            if not blacklist.contains(offer, now=now)
        ]
    candidates = _remove_bait_prices(candidates)
    return sorted(candidates, key=offer_quality_key)


def select_best_offer(offers, *, requested_gpu=None):
    """Select the highest-quality eligible offer deterministically."""
    candidates = [offer for offer in offers if isinstance(offer, dict)]
    if requested_gpu is not None:
        requested = str(requested_gpu).strip()
        candidates = [
            offer
            for offer in candidates
            if str(offer.get("gpu_name") or "").strip() == requested
        ]
    if not candidates:
        suffix = (
            " for the requested GPU class"
            if requested_gpu is not None
            else ""
        )
        raise OfferSelectionError("There is no eligible offer" + suffix + ".")

    return min(candidates, key=offer_quality_key)

"""Deterministic offer selection and private host blacklist policy."""

from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import statistics
import tempfile
import time

from .models import OfferDecision, ReadinessEstimate

from .constants import (
    MIN_VAST_INET_DOWN_MBPS,
    MIN_VAST_RELIABILITY,
    PREFERRED_VAST_INET_DOWN_MBPS,
)


PRICE_BAIT_RATIO = 0.60
DEFAULT_BLACKLIST_TTL_SECONDS = 3 * 24 * 60 * 60
DEFAULT_OBSERVED_TRANSFER_MBPS = 25.0
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


def readiness_estimate(
    *,
    total_bytes,
    cached_bytes=0,
    assumed_mbps=DEFAULT_OBSERVED_TRANSFER_MBPS,
    source_ready,
):
    """Build a content-bound estimate using measured megabytes per second."""
    if (
        type(total_bytes) is not int
        or total_bytes < 0
        or type(cached_bytes) is not int
        or not 0 <= cached_bytes <= total_bytes
        or isinstance(assumed_mbps, bool)
        or not isinstance(assumed_mbps, (int, float))
        or not math.isfinite(assumed_mbps)
        or assumed_mbps <= 0
        or type(source_ready) is not bool
    ):
        raise ValueError("Invalid readiness estimate inputs.")
    remaining = total_bytes - cached_bytes
    estimated_seconds = (
        0
        if remaining == 0
        else math.ceil(remaining / (float(assumed_mbps) * 1_000_000))
    )
    label = (
        "warm"
        if remaining == 0
        else "prepositioned" if cached_bytes else "cold"
    )
    payload = {
        "assumed_mbps": float(assumed_mbps),
        "cached_bytes": cached_bytes,
        "estimated_seconds": estimated_seconds,
        "label": label,
        "remaining_bytes": remaining,
        "source_ready": source_ready,
        "ten_minute_eligible": bool(
            source_ready and estimated_seconds <= 600
        ),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return ReadinessEstimate(**payload, digest=digest)


_PUBLIC_OFFER_FIELDS = (
    "offer_id",
    "gpu_name",
    "gpu_ram_gb",
    "dph_total",
    "reliability",
    "inet_down_mbps",
    "disk_bw_mbps",
    "inet_down_cost",
    "inet_up_cost",
    "dlperf",
    "disk_gb",
)


def _public_offer(offer, workflow_disk_gb):
    return {
        field: (
            workflow_disk_gb
            if field == "disk_gb" and field not in offer
            else offer.get(field)
        )
        for field in _PUBLIC_OFFER_FIELDS
    }


def _metric(value, *, minimum=None):
    number = _finite(value, default=-math.inf)
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    return number


def _decision_estimate(
    offer,
    *,
    transfer_bytes,
    cached_bytes,
    source_ready,
    observed_transfer_mbps,
):
    network_mbps = _metric(offer.get("inet_down_mbps"), minimum=0)
    disk_mbps = _metric(offer.get("disk_bw_mbps"), minimum=0)
    ceilings = [float(observed_transfer_mbps)]
    if network_mbps is not None and network_mbps > 0:
        ceilings.append(network_mbps / 8.0)
    if disk_mbps is not None and disk_mbps > 0:
        ceilings.append(disk_mbps)
    return readiness_estimate(
        total_bytes=transfer_bytes,
        cached_bytes=cached_bytes,
        assumed_mbps=min(ceilings),
        source_ready=source_ready,
    )


def decide_offers(
    offers,
    *,
    workflow_min_vram_gb,
    workflow_disk_gb,
    transfer_bytes,
    source_ready,
    preferred_vram_gb=None,
    max_price_per_hour=None,
    cached_bytes=0,
    observed_transfer_mbps=DEFAULT_OBSERVED_TRANSFER_MBPS,
    blacklist=None,
    now=None,
):
    """Return stable, explainable decisions without provider free text."""
    hard_vram = _metric(workflow_min_vram_gb, minimum=0)
    disk_required = _metric(workflow_disk_gb, minimum=1)
    preference = (
        None
        if preferred_vram_gb is None
        else _metric(preferred_vram_gb, minimum=0)
    )
    price_cap = (
        None
        if max_price_per_hour is None
        else _metric(max_price_per_hour, minimum=0)
    )
    if (
        hard_vram is None
        or disk_required is None
        or (preferred_vram_gb is not None and preference is None)
        or (max_price_per_hour is not None and price_cap is None)
        or type(transfer_bytes) is not int
        or transfer_bytes < 0
        or type(cached_bytes) is not int
        or not 0 <= cached_bytes <= transfer_bytes
        or type(source_ready) is not bool
        or _metric(observed_transfer_mbps, minimum=0) in {None, 0}
    ):
        raise ValueError("Invalid offer-decision inputs.")
    target_vram = (
        None if preference is None else max(hard_vram, preference)
    )
    candidates = [offer for offer in offers if isinstance(offer, dict)]
    non_bait = {
        id(offer)
        for offer in _remove_bait_prices(candidates)
    }
    decisions = []
    for offer in candidates:
        public = _public_offer(offer, int(disk_required))
        gpu_ram = _metric(offer.get("gpu_ram_gb"), minimum=0)
        disk_gb = _metric(public.get("disk_gb"), minimum=0)
        price = _metric(offer.get("dph_total"), minimum=0)
        reliability = _metric(offer.get("reliability"), minimum=0)
        network = _metric(offer.get("inet_down_mbps"), minimum=0)
        disk_speed = _metric(offer.get("disk_bw_mbps"), minimum=0)
        dlperf = _metric(offer.get("dlperf"), minimum=0)
        estimate = _decision_estimate(
            offer,
            transfer_bytes=transfer_bytes,
            cached_bytes=cached_bytes,
            source_ready=source_ready,
            observed_transfer_mbps=observed_transfer_mbps,
        )
        reasons = []
        if gpu_ram is None or gpu_ram < hard_vram:
            reasons.append("vram")
        if disk_gb is None or disk_gb < disk_required:
            reasons.append("disk")
        if price is None or (price_cap is not None and price > price_cap):
            reasons.append("price")
        if reliability is None or network is None or disk_speed is None:
            reasons.append("missing_metrics")
        elif reliability < MIN_VAST_RELIABILITY:
            reasons.append("reliability")
        if not source_ready:
            reasons.append("source_readiness")
        if blacklist is not None and blacklist.contains(offer, now=now):
            reasons.append("blacklisted")
        if id(offer) not in non_bait:
            reasons.append("suspicious_price")
        included = not reasons
        if included:
            reasons = [
                "workflow_requirements",
                "price_cap",
                "source_ready",
                "quality_metrics",
            ]
        distance = (
            0.0
            if target_vram is None or gpu_ram is None
            else abs(gpu_ram - target_vram)
        )
        score = (
            distance,
            estimate.estimated_seconds,
            -float(dlperf) if dlperf is not None else math.inf,
            -float(gpu_ram) if gpu_ram is not None else math.inf,
            -min(float(network), PREFERRED_VAST_INET_DOWN_MBPS)
            if network is not None else math.inf,
            -float(disk_speed) if disk_speed is not None else math.inf,
            -float(reliability) if reliability is not None else math.inf,
            float(price) if price is not None else math.inf,
            _offer_id_key(offer),
        )
        decisions.append(
            OfferDecision(
                offer=public,
                included=included,
                score=score,
                reasons=tuple(dict.fromkeys(reasons)),
                estimate=estimate,
            )
        )
    return sorted(
        decisions,
        key=lambda decision: (
            0 if decision.included else 1,
            decision.score if decision.included else _offer_id_key(decision.offer),
        ),
    )


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

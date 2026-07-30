"""Durable lifecycle value objects for managed Vast.ai attempts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import re
import time
import uuid


class AttemptState(str, Enum):
    IDLE = "idle"
    SEARCHING = "searching"
    OFFER_SELECTED = "offer_selected"
    CONFIRMING = "confirming"
    CREATING = "creating"
    STARTING = "starting"
    CANCEL_REQUESTED = "cancel_requested"
    DESTROYING = "destroying"
    RETRYING = "retrying"
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


class InvalidStateTransition(ValueError):
    pass


_TRANSITIONS = {
    AttemptState.IDLE: {AttemptState.SEARCHING, AttemptState.CANCELLED},
    AttemptState.SEARCHING: {
        AttemptState.OFFER_SELECTED,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
    },
    AttemptState.OFFER_SELECTED: {
        AttemptState.SEARCHING,
        AttemptState.CONFIRMING,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
    },
    AttemptState.CONFIRMING: {
        AttemptState.CREATING,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
    },
    AttemptState.CREATING: {
        AttemptState.STARTING,
        AttemptState.CANCEL_REQUESTED,
        AttemptState.FAILED,
    },
    AttemptState.STARTING: {
        AttemptState.READY,
        AttemptState.CANCEL_REQUESTED,
        AttemptState.DESTROYING,
        AttemptState.FAILED,
    },
    AttemptState.CANCEL_REQUESTED: {
        AttemptState.DESTROYING,
        AttemptState.CANCELLED,
        AttemptState.FAILED,
    },
    AttemptState.DESTROYING: {
        AttemptState.CANCELLED,
        AttemptState.FAILED,
        AttemptState.RETRYING,
    },
    AttemptState.RETRYING: {
        AttemptState.CREATING,
        AttemptState.CANCEL_REQUESTED,
        AttemptState.FAILED,
    },
    AttemptState.READY: {
        AttemptState.DESTROYING,
        AttemptState.FAILED,
    },
    AttemptState.FAILED: {
        AttemptState.RETRYING,
        AttemptState.DESTROYING,
        AttemptState.CANCELLED,
    },
    AttemptState.CANCELLED: set(),
}


@dataclass(frozen=True)
class OfferQuote:
    offer_id: str
    gpu_name: str
    gpu_ram_gb: float
    dph_total: float
    reliability: float | None
    max_price_per_hour: float
    expires_at: float
    machine_id: str | None = None
    host_id: str | None = None
    public_ipaddr: str | None = None

    def to_record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, payload):
        return cls(**payload)

    def public_payload(self):
        return {
            "offer_id": self.offer_id,
            "gpu_name": self.gpu_name,
            "gpu_ram_gb": self.gpu_ram_gb,
            "dph_total": self.dph_total,
            "reliability": self.reliability,
            "max_price_per_hour": self.max_price_per_hour,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class CloudAttempt:
    attempt_id: str
    idempotency_key: str
    label: str
    state: AttemptState
    quote: OfferQuote
    instance_id: str | None = None
    ready_url: str | None = None
    provider_token: str | None = None
    retry_count: int = 0
    cancel_requested: bool = False
    sanitized_error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    version: int = 1

    @classmethod
    def new(
        cls,
        idempotency_key,
        quote,
        attempt_id=None,
        now=None,
        state=AttemptState.CONFIRMING,
    ):
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("A valid idempotency key is required.")
        identifier = str(attempt_id or uuid.uuid4())
        safe_identifier = re.sub(r"[^A-Za-z0-9-]", "-", identifier)[:48]
        timestamp = float(time.time() if now is None else now)
        return cls(
            attempt_id=identifier,
            idempotency_key=key,
            label="comfy-cloud-run-" + safe_identifier,
            state=AttemptState(state),
            quote=quote,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def transition(self, state, *, now=None, **changes):
        target = AttemptState(state)
        if target != self.state and target not in _TRANSITIONS[self.state]:
            raise InvalidStateTransition(
                f"Cannot transition from {self.state.value} to {target.value}."
            )
        allowed_changes = {
            "cancel_requested",
            "instance_id",
            "provider_token",
            "quote",
            "ready_url",
            "retry_count",
            "sanitized_error",
        }
        unknown = set(changes) - allowed_changes
        if unknown:
            raise TypeError("Unsupported attempt fields: " + ", ".join(sorted(unknown)))
        retry_count = int(changes.get("retry_count", self.retry_count))
        if retry_count not in (0, 1):
            raise ValueError("At most one automatic retry is allowed.")
        changes["retry_count"] = retry_count
        timestamp = float(time.time() if now is None else now)
        return replace(self, state=target, updated_at=timestamp, **changes)

    def public_payload(self):
        payload = {
            "attempt_id": self.attempt_id,
            "status": self.state.value,
            "offer": self.quote.public_payload(),
            "instance_id": self.instance_id,
            "ready_url": self.ready_url,
            "retry_count": self.retry_count,
            "cancel_requested": self.cancel_requested,
            "error": self.sanitized_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        residual = self.state == AttemptState.FAILED and bool(self.instance_id)
        payload["billing_may_continue"] = residual
        payload["emergency_action"] = (
            "Destroy Vast instance "
            + str(self.instance_id)
            + " in the Vast.ai console immediately."
            if residual
            else None
        )
        return payload

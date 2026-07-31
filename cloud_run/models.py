"""Durable lifecycle value objects for managed Vast.ai attempts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import re
import time
import uuid


class SessionState(str, Enum):
    PREFLIGHT = "preflight"
    OFFER_SELECTED = "offer_selected"
    CONFIRMING = "confirming"
    CREATING = "creating"
    BOOTSTRAPPING = "bootstrapping"
    PROVISIONING = "provisioning"
    VALIDATING = "validating"
    READY = "ready"
    RUNNING = "running"
    HARVESTING = "harvesting"
    REPAIRING = "repairing"
    DESTROY_REQUESTED = "destroy_requested"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


class JobState(str, Enum):
    CAPTURED = "captured"
    RESOLVING = "resolving"
    QUEUED = "queued"
    RUNNING = "running"
    HARVESTING = "harvesting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TransferState(str, Enum):
    PENDING = "pending"
    TRANSFERRING = "transferring"
    VERIFIED = "verified"
    FAILED = "failed"
    ABANDONED = "abandoned"


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


LEGACY_SESSION_STATES = {
    "idle": SessionState.PREFLIGHT,
    "searching": SessionState.PREFLIGHT,
    "offer_selected": SessionState.OFFER_SELECTED,
    "confirming": SessionState.CONFIRMING,
    "creating": SessionState.CREATING,
    "starting": SessionState.BOOTSTRAPPING,
    "cancel_requested": SessionState.DESTROY_REQUESTED,
    "destroying": SessionState.DESTROYING,
    "retrying": SessionState.CREATING,
    "ready": SessionState.READY,
    "cancelled": SessionState.DESTROYED,
    "failed": SessionState.FAILED,
}


SESSION_TRANSITIONS = {
    SessionState.PREFLIGHT: {
        SessionState.OFFER_SELECTED,
        SessionState.DESTROYED,
        SessionState.FAILED,
    },
    SessionState.OFFER_SELECTED: {
        SessionState.PREFLIGHT,
        SessionState.CONFIRMING,
        SessionState.DESTROYED,
        SessionState.FAILED,
    },
    SessionState.CONFIRMING: {
        SessionState.CREATING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.CREATING: {
        SessionState.BOOTSTRAPPING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.BOOTSTRAPPING: {
        SessionState.PROVISIONING,
        SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING,
        SessionState.FAILED,
    },
    SessionState.PROVISIONING: {
        SessionState.VALIDATING,
        SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.VALIDATING: {
        SessionState.READY,
        SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.REPAIRING: {
        SessionState.PROVISIONING,
        SessionState.VALIDATING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.READY: {
        SessionState.PROVISIONING,
        SessionState.RUNNING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.RUNNING: {
        SessionState.HARVESTING,
        SessionState.READY,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.HARVESTING: {
        SessionState.READY,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.DESTROY_REQUESTED: {
        SessionState.DESTROYING,
        SessionState.FAILED,
    },
    SessionState.DESTROYING: {
        SessionState.DESTROYED,
        SessionState.CREATING,
        SessionState.FAILED,
    },
    SessionState.FAILED: {
        SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING,
        SessionState.CREATING,
    },
    SessionState.DESTROYED: set(),
}


JOB_TRANSITIONS = {
    JobState.CAPTURED: {JobState.RESOLVING, JobState.FAILED},
    JobState.RESOLVING: {JobState.QUEUED, JobState.FAILED},
    JobState.QUEUED: {JobState.RUNNING, JobState.FAILED},
    JobState.RUNNING: {JobState.HARVESTING, JobState.FAILED},
    JobState.HARVESTING: {JobState.SUCCEEDED, JobState.FAILED},
    JobState.SUCCEEDED: set(),
    JobState.FAILED: set(),
}


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
class CloudSession:
    session_id: str
    idempotency_key: str
    label: str
    state: SessionState
    quote: OfferQuote | None
    manifest_digest: str | None
    installed_manifest_digest: str | None
    instance_id: str | None
    worker_base_url: str | None
    provider_token: str | None
    session_secret_hex: str | None
    deadline_at: float | None
    deadline_mode: str
    disk_gb: int
    retry_count: int
    destroy_requested: bool
    residual_inventory: tuple[str, ...]
    sanitized_error: str | None
    created_at: float
    updated_at: float
    version: int

    @classmethod
    def new(
        cls,
        idempotency_key,
        *,
        session_id=None,
        quote=None,
        manifest_digest=None,
        deadline_at=None,
        deadline_mode="finite",
        disk_gb=80,
        now=None,
        state=SessionState.PREFLIGHT,
    ):
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("A valid idempotency key is required.")
        identifier = str(session_id or uuid.uuid4())
        safe_identifier = re.sub(r"[^A-Za-z0-9-]", "-", identifier)[:48]
        timestamp = float(time.time() if now is None else now)
        session = cls(
            session_id=identifier,
            idempotency_key=key,
            label="comfy-cloud-run-" + safe_identifier,
            state=SessionState(state),
            quote=quote,
            manifest_digest=manifest_digest,
            installed_manifest_digest=None,
            instance_id=None,
            worker_base_url=None,
            provider_token=None,
            session_secret_hex=None,
            deadline_at=deadline_at,
            deadline_mode=str(deadline_mode),
            disk_gb=int(disk_gb),
            retry_count=0,
            destroy_requested=False,
            residual_inventory=(),
            sanitized_error=None,
            created_at=timestamp,
            updated_at=timestamp,
            version=1,
        )
        session._validate()
        return session

    def _validate(self):
        if self.deadline_mode not in {"finite", "none"}:
            raise ValueError("Unsupported deadline mode.")
        if int(self.disk_gb) < 80:
            raise ValueError("Cloud Run sessions require at least 80 GiB.")
        if int(self.retry_count) not in (0, 1):
            raise ValueError("At most one automatic retry is allowed.")
        for name, digest in (
            ("manifest digest", self.manifest_digest),
            ("installed manifest digest", self.installed_manifest_digest),
        ):
            if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"Invalid {name}.")
        if self.session_secret_hex is not None and not re.fullmatch(
            r"[0-9a-f]{64}",
            self.session_secret_hex,
        ):
            raise ValueError("Invalid session secret.")

    def transition(self, state, *, now=None, **changes):
        target = SessionState(state)
        if (
            target != self.state
            and target not in SESSION_TRANSITIONS[self.state]
        ):
            raise InvalidStateTransition(
                f"Cannot transition from {self.state.value} to {target.value}."
            )
        allowed_changes = {
            "deadline_at",
            "deadline_mode",
            "destroy_requested",
            "disk_gb",
            "installed_manifest_digest",
            "instance_id",
            "manifest_digest",
            "provider_token",
            "quote",
            "residual_inventory",
            "retry_count",
            "sanitized_error",
            "session_secret_hex",
            "worker_base_url",
        }
        unknown = set(changes) - allowed_changes
        if unknown:
            raise TypeError("Unsupported session fields: " + ", ".join(sorted(unknown)))
        if "residual_inventory" in changes:
            changes["residual_inventory"] = tuple(
                str(item) for item in changes["residual_inventory"]
            )
        timestamp = float(time.time() if now is None else now)
        changed = replace(
            self,
            state=target,
            updated_at=timestamp,
            **changes,
        )
        changed._validate()
        return changed

    def public_payload(self):
        payload = {
            "session_id": self.session_id,
            "status": self.state.value,
            "offer": self.quote.public_payload() if self.quote else None,
            "instance_id": self.instance_id,
            "deadline_at": self.deadline_at,
            "deadline_mode": self.deadline_mode,
            "disk_gb": self.disk_gb,
            "retry_count": self.retry_count,
            "destroy_requested": self.destroy_requested,
            "residual_inventory": list(self.residual_inventory),
            "error": self.sanitized_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        residual = self.state == SessionState.FAILED and bool(
            self.instance_id or self.residual_inventory
        )
        payload["billing_may_continue"] = residual
        payload["emergency_action"] = (
            "Destroy the residual Vast instance in the Vast.ai console immediately."
            if residual
            else None
        )
        return payload


@dataclass(frozen=True)
class CloudJob:
    job_id: str
    session_id: str
    idempotency_key: str
    state: JobState
    prompt_digest: str
    capture_json: str
    manifest_digest: str
    remote_prompt_id: str | None
    sanitized_error: str | None
    created_at: float
    updated_at: float
    version: int

    def transition(self, state, *, now=None, **changes):
        target = JobState(state)
        if target != self.state and target not in JOB_TRANSITIONS[self.state]:
            raise InvalidStateTransition(
                f"Cannot transition from {self.state.value} to {target.value}."
            )
        unknown = set(changes) - {"remote_prompt_id", "sanitized_error"}
        if unknown:
            raise TypeError("Unsupported job fields: " + ", ".join(sorted(unknown)))
        timestamp = float(time.time() if now is None else now)
        return replace(self, state=target, updated_at=timestamp, **changes)

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "status": self.state.value,
            "error": self.sanitized_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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

"""Durable lifecycle value objects for managed Vast.ai attempts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import math
import re
import time
import uuid

from .constants import (
    MAX_SESSION_DISK_GB,
    MIN_SESSION_DISK_GB,
    VAST_CREATE_FAILURE_CODES,
)
from .run_errors import RunErrorCode
from .worker_protocol import PROTOCOL_VERSION, is_boundary_token


class SessionState(str, Enum):
    PREFLIGHT = "preflight"
    OFFER_SELECTED = "offer_selected"
    CONFIRMING = "confirming"
    CREATING = "creating"
    RECONCILING_CREATE = "reconciling_create"
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


class ExecutionState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class HarvestState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
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
    "retrying": SessionState.FAILED,
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
        SessionState.OFFER_SELECTED,
        SessionState.CREATING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.CREATING: {
        SessionState.RECONCILING_CREATE,
        SessionState.BOOTSTRAPPING,
        SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.RECONCILING_CREATE: {
        SessionState.BOOTSTRAPPING,
        SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING,
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
        SessionState.FAILED,
    },
    SessionState.FAILED: {
        SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING,
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
    },
    AttemptState.RETRYING: {
        AttemptState.CANCEL_REQUESTED,
        AttemptState.FAILED,
    },
    AttemptState.READY: {
        AttemptState.DESTROYING,
        AttemptState.FAILED,
    },
    AttemptState.FAILED: {
        AttemptState.DESTROYING,
        AttemptState.CANCELLED,
    },
    AttemptState.CANCELLED: set(),
}


_LEGACY_QUOTE_KEYS = {
    "offer_id",
    "gpu_name",
    "gpu_ram_gb",
    "dph_total",
    "reliability",
    "max_price_per_hour",
    "expires_at",
    "machine_id",
    "host_id",
    "public_ipaddr",
}
_UNBOUND_TEMPLATE_HASH = "0" * 32
_UNBOUND_WORKER_COMMIT = "0" * 40
_UNBOUND_SHA256 = "0" * 64
_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_CONFIGURATION_REVISION = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
)


def _valid_node_id_tuple(value):
    return (
        isinstance(value, tuple)
        and all(
            isinstance(node_id, str)
            and _NODE_ID.fullmatch(node_id) is not None
            for node_id in value
        )
        and tuple(sorted(value)) == value
        and len(set(value)) == len(value)
    )


def _canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class OfferQuote:
    offer_id: str
    gpu_name: str
    gpu_ram_gb: float
    dph_total: float
    reliability: float | None
    max_price_per_hour: float
    expires_at: float
    disk_gb: int
    transfer_bytes: int
    output_allowance_bytes: int
    inet_down_cost: float | None
    inet_up_cost: float | None
    duration_seconds: int | None
    deadline_mode: str
    approximate_max_active_charge: float | None
    template_hash_id: str
    worker_commit: str
    worker_archive_sha256: str
    protocol_version: str
    manifest_digest: str
    execution_baseline_digest: str = _UNBOUND_SHA256
    randomized_seed_node_ids: tuple[str, ...] = ()
    machine_id: str | None = field(default=None, repr=False)
    host_id: str | None = field(default=None, repr=False)
    public_ipaddr: str | None = field(default=None, repr=False)
    max_instance_creates: int = 1
    inet_down_mbps: float | None = None
    disk_bw_mbps: float | None = None

    def __post_init__(self):
        finite_numbers = (
            self.gpu_ram_gb,
            self.dph_total,
            self.max_price_per_hour,
            self.expires_at,
        )
        if (
            not isinstance(self.offer_id, str)
            or not self.offer_id.isdigit()
            or not isinstance(self.gpu_name, str)
            or not self.gpu_name.strip()
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in finite_numbers
            )
            or self.gpu_ram_gb <= 0
            or self.dph_total < 0
            or self.max_price_per_hour < self.dph_total
            or self.expires_at <= 0
            or isinstance(self.disk_gb, bool)
            or not isinstance(self.disk_gb, int)
            or not MIN_SESSION_DISK_GB
            <= self.disk_gb
            <= MAX_SESSION_DISK_GB
            or isinstance(self.transfer_bytes, bool)
            or not isinstance(self.transfer_bytes, int)
            or self.transfer_bytes < 0
            or isinstance(self.output_allowance_bytes, bool)
            or not isinstance(self.output_allowance_bytes, int)
            or self.output_allowance_bytes <= 0
            or self.deadline_mode not in {"finite", "none"}
            or not re.fullmatch(r"[0-9a-f]{32}", self.template_hash_id)
            or not re.fullmatch(r"[0-9a-f]{40}", self.worker_commit)
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                self.worker_archive_sha256,
            )
            or self.protocol_version != PROTOCOL_VERSION
            or not re.fullmatch(r"[0-9a-f]{64}", self.manifest_digest)
            or not isinstance(self.execution_baseline_digest, str)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                self.execution_baseline_digest,
            )
            is None
            or not _valid_node_id_tuple(self.randomized_seed_node_ids)
            or type(self.max_instance_creates) is not int
            or self.max_instance_creates not in {1, 2}
        ):
            raise ValueError("Invalid paid offer quote.")
        if self.reliability is not None and (
            isinstance(self.reliability, bool)
            or not isinstance(self.reliability, (int, float))
            or not math.isfinite(self.reliability)
            or not 0 <= self.reliability <= 1
        ):
            raise ValueError("Invalid paid offer quote.")
        for cost in (self.inet_down_cost, self.inet_up_cost):
            if cost is not None and (
                isinstance(cost, bool)
                or not isinstance(cost, (int, float))
                or not math.isfinite(cost)
                or cost < 0
            ):
                raise ValueError("Invalid paid offer quote.")
        for metric in (self.inet_down_mbps, self.disk_bw_mbps):
            if metric is not None and (
                isinstance(metric, bool)
                or not isinstance(metric, (int, float))
                or not math.isfinite(metric)
                or metric < 0
            ):
                raise ValueError("Invalid paid offer quote.")
        if self.deadline_mode == "finite":
            if (
                isinstance(self.duration_seconds, bool)
                or not isinstance(self.duration_seconds, int)
                or self.duration_seconds <= 0
                or isinstance(self.approximate_max_active_charge, bool)
                or not isinstance(
                    self.approximate_max_active_charge,
                    (int, float),
                )
                or not math.isfinite(
                    self.approximate_max_active_charge
                )
                or self.approximate_max_active_charge < 0
                or not math.isclose(
                    self.approximate_max_active_charge,
                    self.dph_total * self.duration_seconds / 3600,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError("Invalid paid offer quote.")
        elif (
            self.duration_seconds is not None
            or self.approximate_max_active_charge is not None
        ):
            raise ValueError("Invalid paid offer quote.")

    def to_record(self):
        return asdict(self)

    @classmethod
    def from_record(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid paid offer quote.")
        fields = set(payload)
        if (
            {
                "offer_id",
                "gpu_name",
                "gpu_ram_gb",
                "dph_total",
                "reliability",
                "max_price_per_hour",
                "expires_at",
            }.issubset(fields)
            and fields.issubset(_LEGACY_QUOTE_KEYS)
        ):
            payload = {
                **payload,
                "disk_gb": 80,
                "transfer_bytes": 0,
                "output_allowance_bytes": 1,
                "inet_down_cost": None,
                "inet_up_cost": None,
                "duration_seconds": None,
                "deadline_mode": "none",
                "approximate_max_active_charge": None,
                "template_hash_id": _UNBOUND_TEMPLATE_HASH,
                "worker_commit": _UNBOUND_WORKER_COMMIT,
                "worker_archive_sha256": _UNBOUND_SHA256,
                "protocol_version": PROTOCOL_VERSION,
                "manifest_digest": _UNBOUND_SHA256,
                "execution_baseline_digest": _UNBOUND_SHA256,
                "randomized_seed_node_ids": (),
                "max_instance_creates": 1,
                "inet_down_mbps": None,
                "disk_bw_mbps": None,
            }
        values = dict(payload)
        values.setdefault(
            "execution_baseline_digest",
            _UNBOUND_SHA256,
        )
        node_ids = values.get("randomized_seed_node_ids", ())
        if not isinstance(node_ids, (list, tuple)):
            raise ValueError("Invalid paid offer quote.")
        values["randomized_seed_node_ids"] = tuple(node_ids)
        try:
            return cls(**values)
        except (TypeError, ValueError):
            raise ValueError("Invalid paid offer quote.") from None

    @property
    def reviewed_release_bound(self):
        return (
            self.template_hash_id != _UNBOUND_TEMPLATE_HASH
            and self.worker_commit != _UNBOUND_WORKER_COMMIT
            and self.worker_archive_sha256 != _UNBOUND_SHA256
            and self.manifest_digest != _UNBOUND_SHA256
        )

    def public_payload(self):
        reviewed = self.reviewed_release_bound
        return {
            "offer_id": self.offer_id,
            "gpu_name": self.gpu_name,
            "gpu_ram_gb": self.gpu_ram_gb,
            "dph_total": self.dph_total,
            "reliability": self.reliability,
            "inet_down_mbps": (
                self.inet_down_mbps if reviewed else None
            ),
            "disk_bw_mbps": self.disk_bw_mbps if reviewed else None,
            "max_price_per_hour": self.max_price_per_hour,
            "expires_at": self.expires_at,
            "disk_gb": self.disk_gb if reviewed else None,
            "transfer_bytes": self.transfer_bytes if reviewed else None,
            "output_allowance_bytes": (
                self.output_allowance_bytes if reviewed else None
            ),
            "inet_down_cost": self.inet_down_cost if reviewed else None,
            "inet_up_cost": self.inet_up_cost if reviewed else None,
            "duration_seconds": self.duration_seconds if reviewed else None,
            "deadline_mode": self.deadline_mode if reviewed else None,
            "approximate_max_active_charge": (
                self.approximate_max_active_charge if reviewed else None
            ),
            "template_hash_id": (
                self.template_hash_id if reviewed else None
            ),
            "worker_commit": self.worker_commit if reviewed else None,
            "worker_archive_sha256": (
                self.worker_archive_sha256 if reviewed else None
            ),
            "protocol_version": self.protocol_version if reviewed else None,
            "manifest_digest": self.manifest_digest if reviewed else None,
            "max_instance_creates": (
                self.max_instance_creates if reviewed else None
            ),
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
    provider_token: str | None = field(repr=False)
    session_secret_hex: str | None = field(repr=False)
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
    pending_deadline_at: float | None = None
    pending_deadline_mode: str | None = None
    pending_deadline_action: str | None = None
    failure_code: str | None = None
    create_reconcile_started_at: float | None = field(
        default=None,
        repr=False,
    )
    create_empty_observations: int = field(default=0, repr=False)
    create_first_empty_at: float | None = field(default=None, repr=False)
    create_last_empty_at: float | None = field(default=None, repr=False)
    create_settings_revision: str | None = field(default=None, repr=False)
    create_configuration_revision: str | None = field(
        default=None,
        repr=False,
    )
    remediation_verified_at: float | None = field(default=None, repr=False)
    remediation_revision: str | None = field(default=None, repr=False)
    execution_baseline_digest: str = _UNBOUND_SHA256
    randomized_seed_node_ids: tuple[str, ...] = ()

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
        execution_baseline_digest=None,
        randomized_seed_node_ids=None,
    ):
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise ValueError("A valid idempotency key is required.")
        identifier = str(session_id or uuid.uuid4())
        safe_identifier = re.sub(r"[^A-Za-z0-9-]", "-", identifier)[:48]
        timestamp = float(time.time() if now is None else now)
        baseline_digest = (
            quote.execution_baseline_digest
            if execution_baseline_digest is None
            and isinstance(quote, OfferQuote)
            else (
                _UNBOUND_SHA256
                if execution_baseline_digest is None
                else execution_baseline_digest
            )
        )
        seed_node_ids = (
            quote.randomized_seed_node_ids
            if randomized_seed_node_ids is None
            and isinstance(quote, OfferQuote)
            else (
                ()
                if randomized_seed_node_ids is None
                else tuple(randomized_seed_node_ids)
            )
        )
        if quote is not None:
            expected_deadline = (
                timestamp + quote.duration_seconds
                if isinstance(quote, OfferQuote)
                and quote.duration_seconds is not None
                else None
            )
            if (
                not isinstance(quote, OfferQuote)
                or quote.manifest_digest != manifest_digest
                or quote.disk_gb != disk_gb
                or quote.deadline_mode != deadline_mode
                or quote.execution_baseline_digest != baseline_digest
                or quote.randomized_seed_node_ids != seed_node_ids
                or (
                    expected_deadline is None
                    and deadline_at is not None
                )
                or (
                    expected_deadline is not None
                    and (
                        not isinstance(deadline_at, (int, float))
                        or isinstance(deadline_at, bool)
                        or not math.isclose(
                            float(deadline_at),
                            expected_deadline,
                            rel_tol=0,
                            abs_tol=1e-9,
                        )
                    )
                )
            ):
                raise ValueError(
                    "Session quote does not match its durable contract."
                )
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
            disk_gb=disk_gb,
            retry_count=0,
            destroy_requested=False,
            residual_inventory=(),
            sanitized_error=None,
            created_at=timestamp,
            updated_at=timestamp,
            version=1,
            execution_baseline_digest=baseline_digest,
            randomized_seed_node_ids=seed_node_ids,
        )
        session._validate()
        return session

    def _validate(self):
        if self.deadline_mode not in {"finite", "none"}:
            raise ValueError("Unsupported deadline mode.")
        if self.deadline_mode == "none" and self.deadline_at is not None:
            raise ValueError("No-limit sessions cannot have a deadline.")
        if self.deadline_at is not None and (
            isinstance(self.deadline_at, bool)
            or not isinstance(self.deadline_at, (int, float))
            or not math.isfinite(self.deadline_at)
            or self.deadline_at <= 0
        ):
            raise ValueError("Invalid finite session deadline.")
        pending_values = (
            self.pending_deadline_at,
            self.pending_deadline_mode,
            self.pending_deadline_action,
        )
        if all(value is None for value in pending_values):
            pass
        elif (
            self.pending_deadline_mode == "finite"
            and self.pending_deadline_action
            in {"add_30_minutes", "add_1_hour"}
            and isinstance(self.pending_deadline_at, (int, float))
            and not isinstance(self.pending_deadline_at, bool)
            and math.isfinite(self.pending_deadline_at)
            and self.pending_deadline_at > 0
            and self.deadline_mode == "finite"
            and isinstance(self.deadline_at, (int, float))
            and not isinstance(self.deadline_at, bool)
            and self.pending_deadline_at > self.deadline_at
        ):
            pass
        elif (
            self.pending_deadline_mode == "none"
            and self.pending_deadline_action == "disable"
            and self.pending_deadline_at is None
            and self.deadline_mode == "finite"
            and isinstance(self.deadline_at, (int, float))
            and not isinstance(self.deadline_at, bool)
        ):
            pass
        else:
            raise ValueError("Invalid pending deadline synchronization.")
        if (
            isinstance(self.disk_gb, bool)
            or not isinstance(self.disk_gb, int)
            or not MIN_SESSION_DISK_GB
            <= self.disk_gb
            <= MAX_SESSION_DISK_GB
        ):
            raise ValueError(
                "Cloud Run sessions require 80 to 2048 GiB."
            )
        if int(self.retry_count) not in (0, 1):
            raise ValueError("At most one automatic retry is allowed.")
        for name, digest in (
            ("manifest digest", self.manifest_digest),
            ("installed manifest digest", self.installed_manifest_digest),
        ):
            if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"Invalid {name}.")
        if (
            self.provider_token is not None
            and not is_boundary_token(self.provider_token)
        ):
            raise ValueError("Invalid provider token.")
        if self.session_secret_hex is not None and not re.fullmatch(
            r"[0-9a-f]{64}",
            self.session_secret_hex,
        ):
            raise ValueError("Invalid session secret.")
        if (
            self.failure_code is not None
            and self.failure_code not in VAST_CREATE_FAILURE_CODES
        ):
            raise ValueError("Invalid Vast create failure code.")
        if self.create_settings_revision is not None and not _canonical_uuid(
            self.create_settings_revision
        ):
            raise ValueError("Invalid API-key settings revision.")
        if (
            self.create_configuration_revision is not None
            and (
                not isinstance(self.create_configuration_revision, str)
                or _CONFIGURATION_REVISION.fullmatch(
                    self.create_configuration_revision
                )
                is None
            )
        ):
            raise ValueError("Invalid create-configuration revision.")
        if (
            not isinstance(self.execution_baseline_digest, str)
            or re.fullmatch(
                r"[0-9a-f]{64}",
                self.execution_baseline_digest,
            )
            is None
            or not _valid_node_id_tuple(self.randomized_seed_node_ids)
        ):
            raise ValueError("Invalid certified execution baseline.")
        if self.quote is not None and (
            self.quote.execution_baseline_digest
            != self.execution_baseline_digest
            or self.quote.randomized_seed_node_ids
            != self.randomized_seed_node_ids
        ):
            raise ValueError(
                "Session quote does not match its durable contract."
            )

        def valid_time(value):
            return (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(value)
                and value >= 0
            )

        if (
            self.create_reconcile_started_at is not None
            and not valid_time(self.create_reconcile_started_at)
        ):
            raise ValueError("Invalid create-reconciliation start time.")
        if (
            self.state == SessionState.RECONCILING_CREATE
            and self.create_reconcile_started_at is None
        ):
            raise ValueError("Create reconciliation requires a start time.")
        if (
            type(self.create_empty_observations) is not int
            or not 0 <= self.create_empty_observations <= 3
        ):
            raise ValueError("Invalid empty-inventory observation count.")
        if self.create_empty_observations == 0:
            if (
                self.create_first_empty_at is not None
                or self.create_last_empty_at is not None
            ):
                raise ValueError("Invalid empty-inventory evidence.")
        elif (
            not valid_time(self.create_first_empty_at)
            or not valid_time(self.create_last_empty_at)
            or self.create_first_empty_at > self.create_last_empty_at
            or self.create_reconcile_started_at is None
            or self.create_first_empty_at
            < self.create_reconcile_started_at
        ):
            raise ValueError("Invalid empty-inventory evidence.")

        remediation_values = (
            self.remediation_verified_at,
            self.remediation_revision,
        )
        if all(value is None for value in remediation_values):
            pass
        elif (
            self.state != SessionState.FAILED
            or not valid_time(self.remediation_verified_at)
            or self.remediation_verified_at < self.created_at
        ):
            raise ValueError("Invalid remediation evidence.")
        elif self.failure_code == "api_key_rejected":
            if (
                self.create_settings_revision is None
                or not _canonical_uuid(self.remediation_revision)
                or self.remediation_revision
                == self.create_settings_revision
            ):
                raise ValueError("Invalid remediation evidence.")
        elif self.failure_code == "configuration_rejected":
            if (
                self.create_configuration_revision is None
                or not isinstance(self.remediation_revision, str)
                or _CONFIGURATION_REVISION.fullmatch(
                    self.remediation_revision
                )
                is None
                or self.remediation_revision
                == self.create_configuration_revision
            ):
                raise ValueError("Invalid remediation evidence.")
        else:
            raise ValueError("Invalid remediation evidence.")

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
            "create_configuration_revision",
            "create_empty_observations",
            "create_first_empty_at",
            "create_last_empty_at",
            "create_reconcile_started_at",
            "create_settings_revision",
            "deadline_at",
            "deadline_mode",
            "destroy_requested",
            "disk_gb",
            "execution_baseline_digest",
            "failure_code",
            "installed_manifest_digest",
            "instance_id",
            "manifest_digest",
            "pending_deadline_action",
            "pending_deadline_at",
            "pending_deadline_mode",
            "provider_token",
            "quote",
            "randomized_seed_node_ids",
            "remediation_revision",
            "remediation_verified_at",
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
        if "randomized_seed_node_ids" in changes:
            changes["randomized_seed_node_ids"] = tuple(
                changes["randomized_seed_node_ids"]
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

    def record_create_empty_observation(self, *, now):
        if self.state not in {
            SessionState.RECONCILING_CREATE,
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
        }:
            raise InvalidStateTransition(
                "Empty create evidence requires reconciliation."
            )
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
            or self.create_reconcile_started_at is None
            or now < self.create_reconcile_started_at
            or (
                self.create_last_empty_at is not None
                and now < self.create_last_empty_at
            )
        ):
            raise ValueError("Invalid empty-inventory observation time.")
        timestamp = float(now)
        return self.transition(
            self.state,
            now=timestamp,
            create_empty_observations=min(
                3,
                self.create_empty_observations + 1,
            ),
            create_first_empty_at=(
                timestamp
                if self.create_first_empty_at is None
                else self.create_first_empty_at
            ),
            create_last_empty_at=timestamp,
        )

    @property
    def create_absence_verified(self):
        return bool(
            self.create_empty_observations >= 3
            and self.create_reconcile_started_at is not None
            and self.create_first_empty_at is not None
            and self.create_last_empty_at is not None
            and self.create_last_empty_at
            >= self.create_reconcile_started_at
            and self.create_last_empty_at
            - self.create_first_empty_at
            >= 120
        )

    @property
    def rental_outcome(self):
        if self.instance_id is not None or self.residual_inventory:
            return "active"
        if self.state in {
            SessionState.PREFLIGHT,
            SessionState.OFFER_SELECTED,
            SessionState.CONFIRMING,
        } and self.provider_token is None and self.session_secret_hex is None:
            return "not_started"
        if self.state == SessionState.FAILED:
            if (
                self.provider_token is not None
                or self.session_secret_hex is not None
            ):
                return "unknown"
            return "absent"
        if self.state == SessionState.DESTROYED:
            if (
                self.provider_token is not None
                or self.session_secret_hex is not None
            ):
                return "unknown"
            return "absent"
        if self.state in {
            SessionState.BOOTSTRAPPING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
            SessionState.REPAIRING,
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            return "unknown"
        return "unknown"

    @property
    def billing_may_continue(self):
        return self.rental_outcome in {"unknown", "active"}

    @property
    def blocks_new_rental(self):
        return bool(
            self.failure_code
            in {"configuration_rejected", "api_key_rejected"}
            and self.remediation_verified_at is None
        )

    @property
    def can_search_offers(self):
        if self.blocks_new_rental:
            return False
        if self.state in {
            SessionState.PREFLIGHT,
            SessionState.OFFER_SELECTED,
        }:
            return self.rental_outcome == "not_started"
        return self.rental_outcome == "absent"

    @property
    def can_destroy(self):
        return bool(
            self.state
            not in {
                SessionState.DESTROY_REQUESTED,
                SessionState.DESTROYING,
                SessionState.DESTROYED,
            }
            and self.rental_outcome in {"unknown", "active"}
        )

    @property
    def can_verify_vast_access(self):
        return bool(
            self.state == SessionState.FAILED
            and self.rental_outcome == "absent"
            and self.failure_code == "api_key_rejected"
            and self.blocks_new_rental
        )

    def public_payload(self):
        payload = {
            "session_id": self.session_id,
            "status": self.state.value,
            "offer": self.quote.public_payload() if self.quote else None,
            "instance_id": self.instance_id,
            "deadline_at": self.deadline_at,
            "deadline_mode": self.deadline_mode,
            "deadline_sync_pending": (
                self.pending_deadline_mode is not None
            ),
            "pending_deadline_at": self.pending_deadline_at,
            "pending_deadline_mode": self.pending_deadline_mode,
            "disk_gb": self.disk_gb,
            "retry_count": self.retry_count,
            "destroy_requested": self.destroy_requested,
            "residual_inventory": list(self.residual_inventory),
            "error": self.sanitized_error,
            "failure_code": self.failure_code,
            "rental_outcome": self.rental_outcome,
            "can_search_offers": self.can_search_offers,
            "can_destroy": self.can_destroy,
            "can_verify_vast_access": self.can_verify_vast_access,
            "billing_may_continue": self.billing_may_continue,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        emergency = (
            self.state == SessionState.FAILED
            and bool(self.instance_id or self.residual_inventory)
        )
        payload["emergency_action"] = (
            "Destroy the residual Vast instance in the Vast.ai console immediately."
            if emergency
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
    execution_state: ExecutionState = ExecutionState.PENDING
    harvest_state: HarvestState = HarvestState.PENDING
    error_code: RunErrorCode | None = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "execution_state",
            ExecutionState(self.execution_state),
        )
        object.__setattr__(
            self,
            "harvest_state",
            HarvestState(self.harvest_state),
        )
        object.__setattr__(
            self,
            "error_code",
            None if self.error_code is None else RunErrorCode(self.error_code),
        )

    def transition(self, state, *, now=None, **changes):
        target = JobState(state)
        if target != self.state and target not in JOB_TRANSITIONS[self.state]:
            raise InvalidStateTransition(
                f"Cannot transition from {self.state.value} to {target.value}."
            )
        unknown = set(changes) - {
            "remote_prompt_id",
            "sanitized_error",
            "execution_state",
            "harvest_state",
            "error_code",
        }
        if unknown:
            raise TypeError("Unsupported job fields: " + ", ".join(sorted(unknown)))
        if "execution_state" in changes:
            changes["execution_state"] = ExecutionState(
                changes["execution_state"]
            )
        if "harvest_state" in changes:
            changes["harvest_state"] = HarvestState(changes["harvest_state"])
        if "error_code" in changes and changes["error_code"] is not None:
            changes["error_code"] = RunErrorCode(changes["error_code"])
        timestamp = float(time.time() if now is None else now)
        return replace(self, state=target, updated_at=timestamp, **changes)

    def public_payload(self):
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "status": self.state.value,
            "execution_state": self.execution_state.value,
            "harvest_state": self.harvest_state.value,
            "error_code": (
                self.error_code.value if self.error_code is not None else None
            ),
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
    residual_inventory: tuple[str, ...] = ()
    ready_url: str | None = None
    provider_token: str | None = field(default=None, repr=False)
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
            "residual_inventory",
            "retry_count",
            "sanitized_error",
        }
        unknown = set(changes) - allowed_changes
        if unknown:
            raise TypeError("Unsupported attempt fields: " + ", ".join(sorted(unknown)))
        provider_token = changes.get("provider_token", self.provider_token)
        if (
            provider_token is not None
            and not is_boundary_token(provider_token)
        ):
            raise ValueError("Invalid provider token.")
        retry_count = int(changes.get("retry_count", self.retry_count))
        if retry_count not in (0, 1):
            raise ValueError("At most one automatic retry is allowed.")
        if "residual_inventory" in changes:
            changes["residual_inventory"] = tuple(
                str(item) for item in changes["residual_inventory"]
            )
        changes["retry_count"] = retry_count
        timestamp = float(time.time() if now is None else now)
        return replace(self, state=target, updated_at=timestamp, **changes)

    def public_payload(self):
        payload = {
            "attempt_id": self.attempt_id,
            "status": self.state.value,
            "offer": self.quote.public_payload(),
            "instance_id": self.instance_id,
            "residual_inventory": list(self.residual_inventory),
            "ready_url": self.ready_url,
            "retry_count": self.retry_count,
            "cancel_requested": self.cancel_requested,
            "error": self.sanitized_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        residual_ids = self.residual_inventory or (
            (str(self.instance_id),) if self.instance_id else ()
        )
        residual = self.state == AttemptState.FAILED and bool(residual_ids)
        payload["billing_may_continue"] = residual
        payload["emergency_action"] = (
            "Destroy residual Vast instance"
            + ("s " if len(residual_ids) != 1 else " ")
            + ", ".join(residual_ids)
            + " in the Vast.ai console immediately."
            if residual
            else None
        )
        return payload

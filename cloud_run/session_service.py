"""Free dependency preflight and the paid-offer gating boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
import time
import uuid

from .artifacts import ArtifactPathError, hash_file
from .capture import (
    CaptureValidationError,
    CompiledCapture,
    certified_execution_baseline,
)
from .manifest import (
    DependencyManifest,
    ManifestDelta,
    ManifestValidationError,
    MANIFEST_SCHEMA_VERSION,
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
    SourceSpec,
    validate_dependency,
)
from .models import CloudJob, JobState, SessionState, TransferState
from .offers import estimated_transfer_seconds
from .relay import RelaySyncResult
from .repository import ConcurrentSessionUpdate, SessionRepository
from .worker_client import WorkerBoundaryAuthenticationError
from .worker_release import WorkerRelease


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_STATUSES = {"resolved", "mapping_required", "unsupported"}
_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
    "comfyui_startup",
    "environment_validation",
    "ready",
}
_ARTIFACT_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
}
_SOURCE_KINDS = {
    "agent",
    "approved",
    "civitai",
    "core",
    "huggingface",
    "installed_git",
    "local-upload",
    "manual",
    "r2",
    "registry",
}
_MAPPING_CANDIDATE_FIELDS = {
    "approved",
    "archive_complete",
    "candidate_digest",
    "class_type",
    "origin_source_kind",
    "package_id",
    "repository_url",
    "revision",
    "source_kind",
    "wheels_complete",
}
_JOB_TASKS = {}
DEADLINE_ACTION_SECONDS = {
    "add_30_minutes": 30 * 60,
    "add_1_hour": 60 * 60,
}
DESTROY_REVIEW_TTL_SECONDS = 5 * 60


class SessionServiceError(RuntimeError):
    pass


class CaptureNotFound(SessionServiceError):
    pass


class PreflightNotFound(SessionServiceError):
    pass


class PreflightBlocked(SessionServiceError):
    pass


class SessionBusy(SessionServiceError):
    pass


class IncompatibleSession(SessionServiceError):
    pass


class SessionExecutionError(SessionServiceError):
    pass


class TerminalProvisioningError(SessionExecutionError):
    """A sanitized deterministic failure after bounded recovery is exhausted."""


class DeadlineValidationError(SessionServiceError):
    pass


class DeadlineSynchronizationError(SessionServiceError):
    pass


class DestroyConfirmationError(SessionServiceError):
    pass


@dataclass(frozen=True, repr=False)
class DestroyReview:
    session_id: str
    instance_id: str | None
    status: str
    unverified_artifact_ids: tuple[str, ...]
    warning: str
    token: str
    expires_at: float

    def public_payload(self):
        return {
            "session_id": self.session_id,
            "instance_id": self.instance_id,
            "status": self.status,
            "unverified_artifact_ids": list(
                self.unverified_artifact_ids
            ),
            "warning": self.warning,
            "review_token": self.token,
            "expires_at": self.expires_at,
        }


def _identifier(value, name):
    normalized = str(value or "")
    if not _IDENTIFIER.fullmatch(normalized):
        raise SessionServiceError(f"Invalid {name}.")
    return normalized


def _strict_identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SessionServiceError(f"Invalid {name}.")
    return value


def _safe_text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or any(ord(character) < 32 for character in value)
    ):
        raise SessionServiceError(f"Invalid {name}.")
    return value


@dataclass(frozen=True)
class PreflightRow:
    dependency_id: str
    kind: str
    display_name: str
    status: str
    source_kind: str | None
    immutable_revision: str | None
    size_bytes: int | None
    sha256: str | None
    destination: str | None
    reason: str | None
    mapping_candidate: dict | None = None
    source_locator: str | None = None

    def __post_init__(self):
        _identifier(self.dependency_id, "dependency ID")
        _identifier(self.kind, "dependency kind")
        _safe_text(self.display_name, "dependency display name")
        if self.status not in _STATUSES:
            raise SessionServiceError("Invalid dependency status.")
        if (
            self.source_kind is not None
            and self.source_kind not in _SOURCE_KINDS
        ):
            raise SessionServiceError("Invalid dependency source kind.")
        if (
            self.immutable_revision is not None
            and not _HEX_40.fullmatch(self.immutable_revision)
        ):
            raise SessionServiceError("Invalid immutable revision.")
        has_size = self.size_bytes is not None
        has_digest = self.sha256 is not None
        if has_size != has_digest:
            raise SessionServiceError("Dependency content identity is incomplete.")
        if has_size and (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
            or not isinstance(self.sha256, str)
            or not _HEX_64.fullmatch(self.sha256)
        ):
            raise SessionServiceError("Invalid dependency content identity.")
        if self.destination is not None:
            destination = PurePosixPath(self.destination)
            if (
                not self.destination
                or self.destination.startswith("/")
                or "\\" in self.destination
                or str(destination) != self.destination
                or any(part in {"", ".", ".."} for part in destination.parts)
            ):
                raise SessionServiceError("Invalid dependency destination.")
        _safe_text(self.reason, "dependency reason", optional=True)
        if self.source_locator is not None:
            if (
                self.status != "resolved"
                or self.source_kind != "huggingface"
                or self.immutable_revision is None
            ):
                raise SessionServiceError(
                    "Invalid dependency source locator."
                )
            try:
                validate_dependency(
                    SourceSpec(
                        kind="huggingface",
                        locator=self.source_locator,
                        immutable_revision=self.immutable_revision,
                    )
                )
            except ManifestValidationError:
                raise SessionServiceError(
                    "Invalid dependency source locator."
                ) from None
        candidate = self.mapping_candidate
        if candidate is not None:
            if (
                not isinstance(candidate, dict)
                or set(candidate) != _MAPPING_CANDIDATE_FIELDS
                or candidate.get("class_type") != self.display_name
                or not _IDENTIFIER.fullmatch(
                    str(candidate.get("class_type") or "")
                )
                or candidate.get("source_kind") not in _SOURCE_KINDS
                or candidate.get("origin_source_kind")
                not in _SOURCE_KINDS
                or not isinstance(candidate.get("candidate_digest"), str)
                or not _HEX_64.fullmatch(candidate["candidate_digest"])
                or not isinstance(candidate.get("revision"), str)
                or not _HEX_40.fullmatch(candidate["revision"])
                or not isinstance(candidate.get("repository_url"), str)
                or not re.fullmatch(
                    r"https://github\.com/"
                    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
                    candidate["repository_url"],
                )
                or (
                    candidate.get("package_id") is not None
                    and not _IDENTIFIER.fullmatch(
                        str(candidate["package_id"])
                    )
                )
                or not isinstance(
                    candidate.get("archive_complete"),
                    bool,
                )
                or not isinstance(
                    candidate.get("wheels_complete"),
                    bool,
                )
                or candidate.get("approved") is not False
                or self.status != "mapping_required"
            ):
                raise SessionServiceError(
                    "Invalid mapping approval candidate."
                )
            object.__setattr__(
                self,
                "mapping_candidate",
                dict(candidate),
            )

    def public_payload(self):
        return {
            "dependency_id": self.dependency_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "status": self.status,
            "source_kind": self.source_kind,
            "immutable_revision": self.immutable_revision,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "destination": self.destination,
            "reason": self.reason,
            "source_locator": self.source_locator,
            "mapping_candidate": (
                dict(self.mapping_candidate)
                if self.mapping_candidate is not None
                else None
            ),
        }

    @classmethod
    def from_payload(cls, payload):
        fields = {
            "dependency_id",
            "kind",
            "display_name",
            "status",
            "source_kind",
            "immutable_revision",
            "size_bytes",
            "sha256",
            "destination",
            "reason",
        }
        optional_fields = {"mapping_candidate", "source_locator"}
        allowed = {
            frozenset(fields | subset)
            for subset in (
                set(),
                {"mapping_candidate"},
                {"source_locator"},
                optional_fields,
            )
        }
        if not isinstance(payload, dict) or frozenset(payload) not in allowed:
            raise SessionServiceError("Stored preflight row is invalid.")
        values = dict(payload)
        values.setdefault("mapping_candidate", None)
        values.setdefault("source_locator", None)
        return cls(**values)


@dataclass(frozen=True)
class PreflightResult:
    preflight_id: str
    capture_id: str
    rows: tuple[PreflightRow, ...]
    rentable: bool
    manifest_digest: str | None
    transfer_bytes: int
    output_allowance_bytes: int | None
    disk_gb: int | None
    execution_baseline_digest: str
    randomized_seed_node_ids: tuple[str, ...]

    def __post_init__(self):
        _identifier(self.preflight_id, "preflight ID")
        _identifier(self.capture_id, "capture ID")
        if not isinstance(self.rows, tuple) or not all(
            isinstance(row, PreflightRow) for row in self.rows
        ):
            raise SessionServiceError("Invalid preflight rows.")
        if not isinstance(self.rentable, bool):
            raise SessionServiceError("Invalid preflight rental state.")
        if (
            isinstance(self.transfer_bytes, bool)
            or not isinstance(self.transfer_bytes, int)
            or self.transfer_bytes < 0
        ):
            raise SessionServiceError("Invalid preflight transfer size.")
        if self.output_allowance_bytes is not None and (
            isinstance(self.output_allowance_bytes, bool)
            or not isinstance(self.output_allowance_bytes, int)
            or self.output_allowance_bytes <= 0
        ):
            raise SessionServiceError("Invalid output allowance.")
        if self.disk_gb is not None and (
            isinstance(self.disk_gb, bool)
            or not isinstance(self.disk_gb, int)
            or not 80 <= self.disk_gb <= 2048
        ):
            raise SessionServiceError("Invalid disk allocation.")
        if (
            not isinstance(self.execution_baseline_digest, str)
            or not _HEX_64.fullmatch(self.execution_baseline_digest)
        ):
            raise SessionServiceError(
                "Invalid execution baseline digest."
            )
        if (
            not isinstance(self.randomized_seed_node_ids, tuple)
            or any(
                not isinstance(node_id, str)
                or not _IDENTIFIER.fullmatch(node_id)
                for node_id in self.randomized_seed_node_ids
            )
            or tuple(sorted(set(self.randomized_seed_node_ids)))
            != self.randomized_seed_node_ids
        ):
            raise SessionServiceError(
                "Invalid randomized seed node IDs."
            )
        if self.rentable:
            if (
                not isinstance(self.manifest_digest, str)
                or not _HEX_64.fullmatch(self.manifest_digest)
                or self.output_allowance_bytes is None
                or self.disk_gb is None
                or any(row.status != "resolved" for row in self.rows)
            ):
                raise SessionServiceError(
                    "Rentable preflight contract is incomplete."
                )
        elif self.manifest_digest is not None:
            raise SessionServiceError(
                "Blocked preflight cannot identify a manifest."
            )

    def public_payload(self):
        return {
            "preflight_id": self.preflight_id,
            "capture_id": self.capture_id,
            "rows": [row.public_payload() for row in self.rows],
            "rentable": self.rentable,
            "manifest_digest": self.manifest_digest,
            "transfer_bytes": self.transfer_bytes,
            "output_allowance_bytes": self.output_allowance_bytes,
            "disk_gb": self.disk_gb,
            "execution_baseline_digest": (
                self.execution_baseline_digest
            ),
            "randomized_seed_node_ids": list(
                self.randomized_seed_node_ids
            ),
        }

    @classmethod
    def from_payload(cls, payload):
        fields = {
            "preflight_id",
            "capture_id",
            "rows",
            "rentable",
            "manifest_digest",
            "transfer_bytes",
            "output_allowance_bytes",
            "disk_gb",
            "execution_baseline_digest",
            "randomized_seed_node_ids",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != fields
            or not isinstance(payload["rows"], list)
            or not isinstance(payload["randomized_seed_node_ids"], list)
        ):
            raise SessionServiceError("Stored preflight is invalid.")
        values = dict(payload)
        values["rows"] = tuple(
            PreflightRow.from_payload(row) for row in payload["rows"]
        )
        values["randomized_seed_node_ids"] = tuple(
            payload["randomized_seed_node_ids"]
        )
        return cls(**values)


def _candidate_for(repository, class_type, source_kind):
    if repository is None or source_kind is None:
        return None
    try:
        if source_kind == "approved":
            return repository.approved(class_type)
        return next(
            (
                candidate
                for candidate in repository.candidates(class_type)
                if candidate.source_kind == source_kind
            ),
            None,
        )
    except Exception:
        return None


def _preflight_rows(resolution, mapping_repository):
    custom_by_class = {
        class_type: node
        for node in resolution.custom_nodes
        for class_type in node.provided_class_types
    }
    artifacts = {
        artifact.artifact_id: artifact for artifact in resolution.artifacts
    }
    rows = []
    for node_row in resolution.node_rows:
        custom = custom_by_class.get(node_row.class_type)
        candidate = _candidate_for(
            mapping_repository,
            node_row.class_type,
            node_row.source_kind,
        )
        revision = (
            custom.revision
            if custom is not None
            else getattr(candidate, "revision", None)
        )
        size = None
        digest = None
        destination = None
        if custom is not None:
            size = custom.archive.size_bytes + sum(
                wheel.size_bytes for wheel in custom.wheels
            )
            digest = custom.archive.sha256
            destination = custom.archive.destination
        rows.append(
            PreflightRow(
                dependency_id="node:" + node_row.class_type,
                kind=(
                    "core_node"
                    if node_row.source_kind == "core"
                    else "custom_node"
                ),
                display_name=node_row.class_type,
                status=node_row.status,
                source_kind=node_row.source_kind,
                immutable_revision=revision,
                size_bytes=size,
                sha256=digest,
                destination=destination,
                reason=node_row.reason,
                mapping_candidate=(
                    candidate.public_payload()
                    if (
                        node_row.status == "mapping_required"
                        and candidate is not None
                        and callable(
                            getattr(candidate, "public_payload", None)
                        )
                    )
                    else None
                ),
            )
        )

    for artifact_row in resolution.artifact_rows:
        artifact = artifacts.get(artifact_row.artifact_id)
        rows.append(
            PreflightRow(
                dependency_id=(
                    "artifact:"
                    + (
                        artifact_row.artifact_id
                        or (
                            artifact_row.node_id
                            + ":"
                            + artifact_row.input_name
                        )
                    )
                ),
                kind=artifact_row.kind,
                display_name=(
                    artifact.logical_name
                    if artifact is not None
                    else artifact_row.input_name
                ),
                status=artifact_row.status,
                source_kind=(
                    artifact.source.kind if artifact is not None else None
                ),
                immutable_revision=(
                    artifact.source.immutable_revision
                    if artifact is not None
                    else None
                ),
                size_bytes=artifact_row.size_bytes,
                sha256=artifact_row.sha256,
                destination=artifact_row.destination,
                reason=artifact_row.reason,
                source_locator=(
                    artifact.source.locator
                    if (
                        artifact is not None
                        and artifact.source.kind == "huggingface"
                    )
                    else None
                ),
            )
        )
    return tuple(rows)


def _transfer_bytes(resolution):
    identities = {}
    for node in resolution.custom_nodes:
        identities[
            ("archive", node.archive.artifact_id)
        ] = node.archive.size_bytes
        for wheel in node.wheels:
            identities[("wheel", wheel.filename, wheel.sha256)] = (
                wheel.size_bytes
            )
    for artifact in resolution.artifacts:
        identities[("artifact", artifact.artifact_id)] = artifact.size_bytes
    return sum(identities.values())


def _required_local_upload_ids(resolution):
    identifiers = []
    for artifact in resolution.artifacts:
        if artifact.source.kind == "local-upload":
            identifiers.append(
                (
                    artifact.artifact_id,
                    artifact.source.locator.removeprefix(
                        "local-upload:"
                    ),
                )
            )
    for node in resolution.custom_nodes:
        if node.archive.source.kind == "local-upload":
            identifiers.append(
                (
                    node.archive.artifact_id,
                    node.archive.source.locator.removeprefix(
                        "local-upload:"
                    ),
                )
            )
        for wheel in node.wheels:
            if wheel.source.kind == "local-upload":
                identifier = wheel.source.locator.removeprefix(
                    "local-upload:"
                )
                identifiers.append((identifier,))
    return tuple(identifiers)


def _manifest_transfer_bytes(payload):
    total = 0
    seen = set()
    try:
        for node in payload["custom_nodes"]:
            archive = node["archive"]
            identity = ("archive", archive["artifact_id"])
            if identity not in seen:
                total += int(archive["size_bytes"])
                seen.add(identity)
            for wheel in node["wheels"]:
                identity = ("wheel", wheel["filename"], wheel["sha256"])
                if identity not in seen:
                    total += int(wheel["size_bytes"])
                    seen.add(identity)
        for artifact in payload["artifacts"]:
            identity = ("artifact", artifact["artifact_id"])
            if identity not in seen:
                total += int(artifact["size_bytes"])
                seen.add(identity)
    except (KeyError, TypeError, ValueError):
        raise PreflightBlocked("Stored dependency manifest is invalid.") from None
    return total


def _stored_manifest(repository, manifest_digest):
    if not isinstance(manifest_digest, str) or not _HEX_64.fullmatch(
        manifest_digest
    ):
        raise SessionExecutionError(
            "Stored session manifest is unavailable."
        )
    encoded = repository.get_manifest(manifest_digest)
    if not isinstance(encoded, str) or hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest() != manifest_digest:
        raise SessionExecutionError(
            "Stored session manifest is unavailable."
        )
    try:
        payload = json.loads(encoded)
        from remote_worker.provision import dependency_manifest_from_record

        manifest = dependency_manifest_from_record(payload)
    except Exception:
        raise SessionExecutionError(
            "Stored session manifest is unavailable."
        ) from None
    if manifest.digest != manifest_digest:
        raise SessionExecutionError(
            "Stored session manifest is unavailable."
        )
    return manifest


def _manifest_payload(manifest):
    try:
        return json.loads(manifest.canonical_bytes().decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise SessionExecutionError(
            "Dependency manifest could not be prepared."
        ) from None


def _transfer_catalog(manifest):
    try:
        from remote_worker.transfers import wheel_artifact

        artifacts = [
            *manifest.artifacts,
            *(node.archive for node in manifest.custom_nodes),
            *(
                wheel_artifact(wheel)
                for node in manifest.custom_nodes
                for wheel in node.wheels
            ),
        ]
    except Exception:
        raise SessionExecutionError(
            "Dependency transfer plan is unavailable."
        ) from None
    catalog = {}
    for artifact in artifacts:
        prior = catalog.get(artifact.artifact_id)
        if prior is not None and prior != artifact:
            raise SessionExecutionError(
                "Dependency transfer identities conflict."
            )
        catalog[artifact.artifact_id] = artifact
    return catalog


def _installed_records(manifest):
    records = []
    for artifact_id, artifact in sorted(_transfer_catalog(manifest).items()):
        revision = None
        for node in manifest.custom_nodes:
            if node.archive.artifact_id == artifact_id:
                revision = node.revision
                break
        records.append(
            {
                "dependency_id": artifact_id,
                "digest": artifact.sha256,
                "revision": revision,
                "destination": artifact.destination,
            }
        )
    return records


def _safe_remote_error(error):
    if not isinstance(error, dict):
        return "Remote execution failed."
    message = error.get("message")
    allowed = {
        "Remote ComfyUI rejected the compiled prompt.",
        "Remote execution ran out of GPU memory.",
        "Remote execution failed.",
        "Remote execution was interrupted.",
        "Remote execution was interrupted by a worker restart.",
    }
    return message if message in allowed else "Remote execution failed."


def _deadline_response_matches(response, request):
    fields = {
        "mode",
        "deadline_at",
        "retrieval_grace_seconds",
        "destroy_intent",
        "destroy_requested",
    }
    if not isinstance(response, dict) or set(response) != fields:
        return False
    mode = request.get("mode") if isinstance(request, dict) else None
    if mode == "finite":
        expected_deadline = request.get("deadline_at")
        expected_grace = request.get("retrieval_grace_seconds")
        deadline_at = response.get("deadline_at")
        if (
            isinstance(deadline_at, bool)
            or not isinstance(deadline_at, (int, float))
            or not math.isfinite(deadline_at)
            or deadline_at != expected_deadline
        ):
            return False
    elif mode == "none":
        expected_deadline = None
        expected_grace = 0
    else:
        return False
    return (
        response.get("mode") == mode
        and response.get("deadline_at") == expected_deadline
        and type(response.get("retrieval_grace_seconds")) is int
        and response.get("retrieval_grace_seconds") == expected_grace
        and response.get("destroy_intent") is False
        and response.get("destroy_requested") is False
    )


def _validated_provision_progress(payload, manifest):
    progress = payload.get("progress")
    if progress is None:
        if "progress" in payload:
            raise ValueError("Invalid provisioning progress.")
        return None
    fields = {
        "phase",
        "dependency_id",
        "transferred_bytes",
        "total_bytes",
    }
    if not isinstance(progress, dict) or set(progress) != fields:
        raise ValueError("Invalid provisioning progress.")
    phase = progress.get("phase")
    dependency_id = progress.get("dependency_id")
    transferred_bytes = progress.get("transferred_bytes")
    total_bytes = progress.get("total_bytes")
    catalog = _transfer_catalog(manifest)
    expected_total = sum(
        artifact.size_bytes for artifact in catalog.values()
    )
    artifact = (
        catalog.get(dependency_id)
        if isinstance(dependency_id, str)
        else None
    )
    if (
        phase not in _PROVISION_PHASES
        or isinstance(transferred_bytes, bool)
        or not isinstance(transferred_bytes, int)
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or total_bytes != expected_total
        or not 0 <= transferred_bytes <= total_bytes
        or (
            phase in _ARTIFACT_PROVISION_PHASES
            and (
                not isinstance(dependency_id, str)
                or not _IDENTIFIER.fullmatch(dependency_id)
                or artifact is None
            )
        )
        or (
            phase not in _ARTIFACT_PROVISION_PHASES
            and dependency_id is not None
        )
        or (phase == "model_transfer" and artifact.kind != "model")
        or (
            phase == "dependency_transfer"
            and artifact.kind == "model"
        )
        or (phase == "ready" and transferred_bytes != total_bytes)
    ):
        raise ValueError("Invalid provisioning progress.")
    return dict(progress)


def _provision_payload_valid(payload, manifest):
    required = {
        "transaction_id",
        "manifest_digest",
        "state",
        "planned_restarts",
        "repair_restarts",
        "missing_class_types",
        "missing_artifacts",
    }
    optional = {"required_uploads", "progress"}
    allowed = {
        frozenset(required | subset)
        for subset in (
            set(),
            {"required_uploads"},
            {"progress"},
            optional,
        )
    }
    if not isinstance(payload, dict) or frozenset(payload) not in allowed:
        return False
    if (
        not isinstance(payload.get("transaction_id"), str)
        or not _IDENTIFIER.fullmatch(payload["transaction_id"])
        or payload["transaction_id"] != "provision-" + manifest.digest
        or payload.get("manifest_digest") != manifest.digest
        or payload.get("state")
        not in {"applying", "awaiting_upload", "ready", "failed", "stalled"}
        or payload.get("planned_restarts") not in {0, 1}
        or isinstance(payload.get("planned_restarts"), bool)
        or payload.get("repair_restarts") not in {0, 1}
        or isinstance(payload.get("repair_restarts"), bool)
    ):
        return False
    for name in (
        "missing_class_types",
        "missing_artifacts",
        "required_uploads",
    ):
        values = payload.get(name, [])
        if (
            not isinstance(values, list)
            or len(values) > 100_000
            or not all(
                isinstance(value, str) and _IDENTIFIER.fullmatch(value)
                for value in values
            )
            or len(set(values)) != len(values)
        ):
            return False
    try:
        progress = _validated_provision_progress(payload, manifest)
    except (SessionExecutionError, TypeError, ValueError):
        return False
    if progress is not None and (
        (progress["phase"] == "ready")
        != (payload["state"] == "ready")
    ):
        return False
    return True


def _stored_provision_transaction_id(session_id, manifest_digest):
    if (
        not isinstance(session_id, str)
        or not _IDENTIFIER.fullmatch(session_id)
        or not isinstance(manifest_digest, str)
        or not _HEX_64.fullmatch(manifest_digest)
    ):
        raise ValueError("Invalid provisioning transaction identity.")
    identity = hashlib.sha256(
        (session_id + "\0" + manifest_digest).encode("ascii")
    ).hexdigest()
    return "provision-session-" + identity


class SessionService:
    def __init__(
        self,
        *,
        job_repository,
        resolver,
        offer_search=None,
        mapping_repository=None,
        release,
        session_repository=None,
        worker_factory=None,
        relay_factory=None,
        source_url_resolver=None,
        lifecycle=None,
        clock=None,
        id_factory=None,
        review_token_factory=None,
        sleep=None,
        job_poll_interval_seconds=1,
        max_job_polls=86_400,
    ):
        self.job_repository = job_repository
        self.resolver = resolver
        self.offer_search = offer_search
        self.mapping_repository = mapping_repository
        self.release = release if isinstance(release, WorkerRelease) else None
        self.session_repository = session_repository
        self.worker_factory = worker_factory
        self.relay_factory = relay_factory
        self.source_url_resolver = source_url_resolver
        self.lifecycle = lifecycle
        self.clock = clock or time.time
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self.review_token_factory = (
            review_token_factory
            or (lambda: secrets.token_hex(32))
        )
        self.sleep = sleep or asyncio.sleep
        if (
            isinstance(job_poll_interval_seconds, bool)
            or not isinstance(job_poll_interval_seconds, (int, float))
            or not math.isfinite(job_poll_interval_seconds)
            or job_poll_interval_seconds < 0
            or isinstance(max_job_polls, bool)
            or not isinstance(max_job_polls, int)
            or not 1 <= max_job_polls <= 1_000_000
        ):
            raise ValueError("Invalid session polling policy.")
        self.job_poll_interval_seconds = float(
            job_poll_interval_seconds
        )
        self.max_job_polls = max_job_polls

    async def preflight(
        self,
        capture_id,
        *,
        explicit_output_allowance_bytes=None,
    ):
        capture = self.job_repository.get_capture(str(capture_id))
        if capture is None:
            raise CaptureNotFound("Cloud Run capture was not found.")
        (
            execution_baseline_digest,
            randomized_seed_node_ids,
        ) = certified_execution_baseline(capture)
        resolution = await self.resolver.resolve_preflight(
            capture,
            explicit_output_allowance_bytes=(
                explicit_output_allowance_bytes
            ),
        )
        local_artifacts = getattr(resolution, "local_artifacts", None)
        local_uploads_available = True
        if local_artifacts is not None:
            for artifact in local_artifacts:
                try:
                    self.job_repository.register_local_artifact(
                        artifact,
                        created_at=float(self.clock()),
                    )
                except Exception:
                    continue
            lookup = getattr(
                self.job_repository,
                "get_local_artifact",
                None,
            )
            required_local = _required_local_upload_ids(resolution)
            local_uploads_available = callable(lookup) and all(
                any(
                    lookup(identifier) is not None
                    for identifier in alternatives
                )
                for alternatives in required_local
            )
        rows = _preflight_rows(resolution, self.mapping_repository)
        output_allowance = getattr(
            resolution,
            "output_allowance_bytes",
            None,
        )
        disk_gb = getattr(resolution, "disk_gb", None)
        rentable = bool(
            resolution.rentable
            and self.release is not None
            and local_uploads_available
            and rows
            and all(row.status == "resolved" for row in rows)
            and isinstance(output_allowance, int)
            and not isinstance(output_allowance, bool)
            and output_allowance > 0
            and isinstance(disk_gb, int)
            and not isinstance(disk_gb, bool)
            and 80 <= disk_gb <= 2048
        )
        manifest_digest = None
        if rentable:
            manifest = DependencyManifest(
                schema_version=MANIFEST_SCHEMA_VERSION,
                protocol_version=PROTOCOL_VERSION,
                comfyui_core_version=PINNED_COMFYUI_CORE_VERSION,
                comfyui_frontend_version=PINNED_COMFYUI_FRONTEND_VERSION,
                worker_version=self.release.worker_commit,
                prompt_digest=capture.prompt_digest,
                custom_nodes=tuple(resolution.custom_nodes),
                artifacts=tuple(resolution.artifacts),
                output_allowance_bytes=output_allowance,
                disk_gb=disk_gb,
            )
            manifest_json = manifest.canonical_bytes().decode("utf-8")
            manifest_digest = manifest.digest
            self.job_repository.save_manifest(
                manifest_digest,
                manifest_json,
                created_at=float(self.clock()),
            )
        result = PreflightResult(
            preflight_id=_identifier(
                self.id_factory(),
                "preflight ID",
            ),
            capture_id=capture.capture_id,
            rows=rows,
            rentable=rentable,
            manifest_digest=manifest_digest,
            transfer_bytes=_transfer_bytes(resolution),
            output_allowance_bytes=output_allowance,
            disk_gb=disk_gb,
            execution_baseline_digest=execution_baseline_digest,
            randomized_seed_node_ids=randomized_seed_node_ids,
        )
        self.job_repository.save_preflight(
            result.preflight_id,
            capture.capture_id,
            result.public_payload(),
            created_at=float(self.clock()),
        )
        return result

    def get_preflight(self, preflight_id):
        try:
            stored = self.job_repository.get_preflight(preflight_id)
        except ValueError:
            raise PreflightBlocked("Stored preflight is invalid.") from None
        if stored is None:
            raise PreflightNotFound("Cloud Run preflight was not found.")
        result = PreflightResult.from_payload(stored.payload)
        if (
            result.preflight_id != stored.preflight_id
            or result.capture_id != stored.capture_id
        ):
            raise PreflightBlocked("Stored preflight identity is invalid.")
        return result

    def require_rentable_preflight(self, preflight_id):
        result = self.get_preflight(preflight_id)
        if (
            not result.rentable
            or result.manifest_digest is None
            or result.output_allowance_bytes is None
            or result.disk_gb is None
            or any(row.status != "resolved" for row in result.rows)
        ):
            raise PreflightBlocked(
                "Resolve every dependency before searching Vast offers."
            )
        manifest_json = self.job_repository.get_manifest(
            result.manifest_digest
        )
        if not isinstance(manifest_json, str) or hashlib.sha256(
            manifest_json.encode("utf-8")
        ).hexdigest() != result.manifest_digest:
            raise PreflightBlocked("Stored dependency manifest is invalid.")
        try:
            manifest = json.loads(manifest_json)
        except json.JSONDecodeError:
            raise PreflightBlocked("Stored dependency manifest is invalid.") from None
        capture = self.job_repository.get_capture(result.capture_id)
        try:
            baseline = certified_execution_baseline(capture)
        except CaptureValidationError:
            baseline = None
        if (
            capture is None
            or baseline
            != (
                result.execution_baseline_digest,
                result.randomized_seed_node_ids,
            )
            or manifest.get("prompt_digest") != capture.prompt_digest
            or manifest.get("output_allowance_bytes")
            != result.output_allowance_bytes
            or manifest.get("disk_gb") != result.disk_gb
            or _manifest_transfer_bytes(manifest) != result.transfer_bytes
            or self.release is None
            or manifest.get("worker_version")
            != self.release.worker_commit
            or manifest.get("protocol_version")
            != self.release.protocol_version
            or manifest.get("comfyui_core_version")
            != self.release.comfyui_core_version
            or manifest.get("comfyui_frontend_version")
            != self.release.comfyui_frontend_version
        ):
            raise PreflightBlocked("Stored dependency manifest is invalid.")
        return result

    async def search_offers(self, preflight_id):
        result = self.require_rentable_preflight(preflight_id)
        if self.offer_search is None:
            raise PreflightBlocked("Vast offer search is unavailable.")
        if callable(self.offer_search):
            offers = await self.offer_search(disk_gb=result.disk_gb)
        elif callable(getattr(self.offer_search, "search", None)):
            offers = await self.offer_search.search(disk_gb=result.disk_gb)
        else:
            raise PreflightBlocked("Vast offer search is unavailable.")
        return [
            {
                **offer,
                "estimated_transfer_seconds": estimated_transfer_seconds(
                    result.transfer_bytes,
                    offer.get("inet_down_mbps"),
                ),
            }
            for offer in offers
        ]

    def approve_mapping(self, mapping_id, candidate_digest):
        if self.mapping_repository is None:
            raise SessionServiceError("Dependency mappings are unavailable.")
        candidate = self.mapping_repository.approve(
            mapping_id,
            candidate_digest,
        )
        return candidate.public_payload()

    def register_agent_suggestion(self, payload):
        candidate = self.resolver.register_agent_suggestion(payload)
        return candidate.public_payload()

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise SessionExecutionError("Session clock is unavailable.")
        return float(value)

    def _sessions(self):
        if not isinstance(self.session_repository, SessionRepository):
            raise SessionExecutionError(
                "Cloud Run session storage is unavailable."
            )
        return self.session_repository

    def session(self, session_id):
        identifier = _strict_identifier(session_id, "session ID")
        session = self._sessions().get(identifier)
        if session is None:
            raise SessionExecutionError(
                "Cloud Run session was not found."
            )
        return session

    def get_job(self, session_id, job_id):
        session = self.session(session_id)
        job = self.job_repository.get_job(
            _strict_identifier(job_id, "job ID")
        )
        if job is None or job.session_id != session.session_id:
            raise SessionExecutionError("Cloud Run job was not found.")
        return job

    @staticmethod
    def alerts(session, *, now):
        if not hasattr(session, "deadline_mode"):
            raise DeadlineValidationError(
                "Cloud Run session deadline is unavailable."
            )
        try:
            timestamp = float(now)
        except (TypeError, ValueError):
            raise DeadlineValidationError(
                "Cloud Run session deadline is unavailable."
            ) from None
        if not math.isfinite(timestamp) or timestamp < 0:
            raise DeadlineValidationError(
                "Cloud Run session deadline is unavailable."
            )
        if session.deadline_mode != "finite":
            return []
        if (
            isinstance(session.deadline_at, bool)
            or not isinstance(session.deadline_at, (int, float))
            or not math.isfinite(session.deadline_at)
        ):
            raise DeadlineValidationError(
                "Cloud Run session deadline is unavailable."
            )
        remaining = float(session.deadline_at) - timestamp
        if remaining <= 0:
            return ["deadline_reached"]
        if remaining <= 5 * 60:
            return ["5_minutes"]
        if remaining <= 15 * 60:
            return ["15_minutes"]
        return []

    def _deadline_intent(self, session, payload):
        if not isinstance(payload, dict):
            raise DeadlineValidationError(
                "Invalid session deadline action."
            )
        action = payload.get("action")
        if action in DEADLINE_ACTION_SECONDS:
            if set(payload) != {"action"}:
                raise DeadlineValidationError(
                    "Invalid session deadline action."
                )
        elif action == "disable":
            if (
                set(payload) != {"action", "acknowledged"}
                or payload.get("acknowledged") is not True
            ):
                raise DeadlineValidationError(
                    "Disabling the deadline requires explicit acknowledgement."
                )
        else:
            raise DeadlineValidationError(
                "Invalid session deadline action."
            )
        if session.pending_deadline_mode is not None:
            if session.pending_deadline_action != action:
                raise DeadlineSynchronizationError(
                    "A deadline update is still awaiting synchronization."
                )
            return (
                session.pending_deadline_mode,
                session.pending_deadline_at,
                session.pending_deadline_action,
            )
        if action == "disable":
            if session.deadline_mode == "none":
                return "none", None, action
            return "none", None, action
        if (
            session.deadline_mode != "finite"
            or isinstance(session.deadline_at, bool)
            or not isinstance(session.deadline_at, (int, float))
            or not math.isfinite(session.deadline_at)
            or session.deadline_at <= self._now()
        ):
            raise DeadlineValidationError(
                "The finite session deadline can no longer be extended."
            )
        return (
            "finite",
            float(session.deadline_at)
            + DEADLINE_ACTION_SECONDS[action],
            action,
        )

    async def update_deadline(self, session_id, payload):
        synchronized_states = {
            SessionState.BOOTSTRAPPING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
            SessionState.REPAIRING,
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }
        for _attempt in range(8):
            session = self.session(session_id)
            if session.state not in synchronized_states:
                raise DeadlineValidationError(
                    "The remote worker cannot synchronize a deadline update."
                )
            mode, deadline_at, action = self._deadline_intent(
                session,
                payload,
            )
            if (
                session.pending_deadline_mode is None
                and session.deadline_mode == mode
                and session.deadline_at == deadline_at
            ):
                return session
            if session.pending_deadline_mode is not None:
                break
            pending = session.transition(
                session.state,
                now=self._now(),
                pending_deadline_at=deadline_at,
                pending_deadline_mode=mode,
                pending_deadline_action=action,
                sanitized_error=None,
            )
            try:
                session = self._sessions().save(pending)
            except ConcurrentSessionUpdate:
                continue
            break
        else:
            raise DeadlineSynchronizationError(
                "The session changed before the deadline update could be recorded."
            )
        try:
            worker = self._worker(session)
            update = getattr(worker, "update_deadline", None)
        except SessionExecutionError:
            update = None
        request = (
            {
                "mode": "finite",
                "deadline_at": deadline_at,
                "retrieval_grace_seconds": 300,
            }
            if mode == "finite"
            else {"mode": "none", "acknowledged": True}
        )
        if callable(update):
            try:
                response = await update(request)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                response = None
        else:
            response = None
        valid = _deadline_response_matches(response, request)
        if not valid:
            self._sessions().update(
                session.session_id,
                now=self._now(),
                sanitized_error=(
                    "The deadline update is not synchronized; the earlier "
                    "finite boundary remains effective."
                ),
            )
            raise DeadlineSynchronizationError(
                "The remote deadline could not be synchronized."
            )
        return self._sessions().update(
            session.session_id,
            now=self._now(),
            deadline_at=deadline_at,
            deadline_mode=mode,
            pending_deadline_at=None,
            pending_deadline_mode=None,
            pending_deadline_action=None,
            sanitized_error=None,
        )

    def _unverified_outputs(self, session_id):
        artifact_ids = set()
        for job in self.job_repository.list_jobs(session_id):
            for transfer in self.job_repository.list_transfers(job.job_id):
                if (
                    transfer.direction == "download"
                    and transfer.state != TransferState.VERIFIED
                ):
                    artifact_ids.add(transfer.artifact_id)
        return tuple(sorted(artifact_ids))

    async def review_destroy(self, session_id):
        session = self.session(session_id)
        if session.state == SessionState.DESTROYED:
            raise DestroyConfirmationError(
                "The Cloud Run session is already destroyed."
            )
        try:
            token = self.review_token_factory()
        except Exception:
            raise DestroyConfirmationError(
                "A destruction review could not be created."
            ) from None
        if (
            not isinstance(token, str)
            or not _IDENTIFIER.fullmatch(token)
            or len(token.encode("utf-8")) < 32
        ):
            raise DestroyConfirmationError(
                "A destruction review could not be created."
            )
        expires_at = self._now() + DESTROY_REVIEW_TTL_SECONDS
        unverified = self._unverified_outputs(session.session_id)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            self._sessions().save_destroy_review(
                session.session_id,
                token_digest=digest,
                expires_at=expires_at,
                session_version=session.version,
                instance_id=session.instance_id,
                unverified_artifact_ids=unverified,
            )
        except Exception:
            raise DestroyConfirmationError(
                "A destruction review could not be created."
            ) from None
        return DestroyReview(
            session_id=session.session_id,
            instance_id=session.instance_id,
            status=session.state.value,
            unverified_artifact_ids=unverified,
            warning=(
                "Destroying the GPU is irreversible. Unverified or "
                "incomplete results will be irreversibly lost."
            ),
            token=token,
            expires_at=expires_at,
        )

    def _abandon_session_work(self, session_id):
        for job in self.job_repository.list_jobs(session_id):
            for transfer in self.job_repository.list_transfers(job.job_id):
                if (
                    transfer.direction == "download"
                    and transfer.state != TransferState.VERIFIED
                ):
                    self.job_repository.save_transfer(
                        job_id=transfer.job_id,
                        artifact_id=transfer.artifact_id,
                        direction=transfer.direction,
                        expected_size=transfer.expected_size,
                        sha256=transfer.sha256,
                        offset=transfer.offset,
                        state=TransferState.ABANDONED,
                        private_path=transfer.private_path,
                        source_node_id=transfer.source_node_id,
                        published_device=transfer.published_device,
                        published_inode=transfer.published_inode,
                    )
            if job.state in {
                JobState.CAPTURED,
                JobState.RESOLVING,
                JobState.QUEUED,
                JobState.RUNNING,
                JobState.HARVESTING,
            }:
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error=(
                        "Remote execution was interrupted by GPU destruction."
                    ),
                )

    def confirmed_deadline_destroy(self, session_id):
        session = self.session(session_id)
        if session.state != SessionState.DESTROYED:
            raise SessionExecutionError(
                "Deadline destruction is not inventory verified."
            )
        self._abandon_session_work(session.session_id)

    def confirmed_terminal_destroy(self, session_id):
        session = self.session(session_id)
        if session.state != SessionState.DESTROYED:
            raise SessionExecutionError(
                "Terminal destruction is not inventory verified."
            )
        self._abandon_session_work(session.session_id)

    async def prepare_deadline_destroy(self, session_id):
        session = self.session(session_id)
        active = [
            job
            for job in self.job_repository.list_jobs(session.session_id)
            if job.state
            in {
                JobState.CAPTURED,
                JobState.RESOLVING,
                JobState.QUEUED,
                JobState.RUNNING,
                JobState.HARVESTING,
            }
        ]
        if len(active) != 1 or session.state not in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            return
        job = active[0]
        try:
            worker = self._worker(session)
            relay = self._relay(worker, session)
        except SessionExecutionError:
            return
        for attempt in range(3):
            try:
                result = await relay.sync_job(job.job_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                result = None
            if (
                isinstance(result, RelaySyncResult)
                and result.job_id == job.job_id
                and result.state
                in {"succeeded", "failed", "interrupted"}
            ):
                try:
                    await self._finish_remote_job(
                        self.session(session.session_id),
                        self.job_repository.get_job(job.job_id),
                        worker,
                    )
                except SessionExecutionError:
                    pass
                return
            if attempt < 2:
                await self.sleep(self.job_poll_interval_seconds)

    async def destroy(self, session_id, confirmation):
        session = self.session(session_id)
        if (
            not isinstance(confirmation, dict)
            or set(confirmation)
            != {"review_token", "acknowledge_data_loss"}
            or confirmation.get("acknowledge_data_loss") is not True
            or not isinstance(confirmation.get("review_token"), str)
        ):
            raise DestroyConfirmationError(
                "GPU destruction requires the fresh review and data-loss acknowledgement."
            )
        token = confirmation["review_token"]
        if not _IDENTIFIER.fullmatch(token):
            raise DestroyConfirmationError(
                "The destruction review is invalid or expired."
            )
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        reviewed = self._sessions().consume_destroy_review(
            session.session_id,
            token_digest=digest,
            now=self._now(),
        )
        if reviewed is None:
            raise DestroyConfirmationError(
                "The destruction review is invalid or expired."
            )
        current_unverified = self._unverified_outputs(session.session_id)
        if current_unverified != reviewed.unverified_artifact_ids:
            raise DestroyConfirmationError(
                "Session outputs changed; review destruction again."
            )
        destroy = getattr(self.lifecycle, "destroy_session", None)
        if not callable(destroy):
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            )
        try:
            result = await destroy(session.session_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            ) from None
        if (
            not hasattr(result, "state")
            or result.session_id != session.session_id
        ):
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            )
        if result.state == SessionState.DESTROYED:
            self._abandon_session_work(session.session_id)
        return result

    async def _request_terminal_destruction(self, session_id, diagnostic):
        destroy = getattr(self.lifecycle, "destroy_session", None)
        if not callable(destroy):
            return None
        try:
            return await destroy(
                session_id,
                terminal_error=diagnostic,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return None

    def _worker(self, session):
        if not callable(self.worker_factory):
            raise SessionExecutionError("Remote worker is unavailable.")
        try:
            worker = self.worker_factory(session)
        except Exception:
            raise SessionExecutionError("Remote worker is unavailable.") from None
        required = ("apply_manifest", "start_job")
        if not all(callable(getattr(worker, name, None)) for name in required):
            raise SessionExecutionError("Remote worker is unavailable.")
        return worker

    def _relay(self, worker, session):
        if not callable(self.relay_factory):
            raise SessionExecutionError("Local relay is unavailable.")
        try:
            relay = self.relay_factory(worker, session)
        except Exception:
            raise SessionExecutionError("Local relay is unavailable.") from None
        if not callable(getattr(relay, "sync_job", None)):
            raise SessionExecutionError("Local relay is unavailable.")
        return relay

    def _new_job(self, session, capture, manifest_digest, key):
        now = self._now()
        return CloudJob(
            job_id=_strict_identifier(self.id_factory(), "job ID"),
            session_id=session.session_id,
            idempotency_key=_strict_identifier(
                key,
                "job idempotency key",
            ),
            state=JobState.CAPTURED,
            prompt_digest=capture.prompt_digest,
            capture_json=capture.canonical_payload(),
            manifest_digest=manifest_digest,
            remote_prompt_id=None,
            sanitized_error=None,
            created_at=now,
            updated_at=now,
            version=1,
        )

    def _transition_job(self, job, state, **changes):
        return self.job_repository.save_job(
            job.transition(
                state,
                now=self._now(),
                **changes,
            )
        )

    async def _fresh_manifest(self, session, capture):
        installed = _stored_manifest(
            self.job_repository,
            session.installed_manifest_digest,
        )
        result = await self.preflight(
            capture.capture_id,
            explicit_output_allowance_bytes=(
                installed.output_allowance_bytes
            ),
        )
        if (
            not result.rentable
            or result.manifest_digest is None
            or result.disk_gb is None
            or result.disk_gb > session.disk_gb
        ):
            raise PreflightBlocked(
                "The fresh canvas is not compatible with this session."
            )
        desired = _stored_manifest(
            self.job_repository,
            result.manifest_digest,
        )
        return installed, desired

    async def _source_url(self, artifact, session):
        locator = artifact.source.locator
        if (
            artifact.source.kind in {"huggingface", "civitai"}
            and artifact.source.secret_handle is None
            and isinstance(locator, str)
            and locator.startswith("https://")
        ):
            return locator
        if not callable(self.source_url_resolver):
            raise SessionExecutionError(
                "A temporary dependency source is unavailable."
            )
        try:
            value = self.source_url_resolver(artifact, session)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            raise SessionExecutionError(
                "A temporary dependency source is unavailable."
            ) from None
        if (
            not isinstance(value, str)
            or not value.startswith("https://")
            or len(value.encode("utf-8")) > 8192
        ):
            raise SessionExecutionError(
                "A temporary dependency source is unavailable."
            )
        return value

    async def _source_urls(self, manifest, session):
        result = {}
        for artifact_id, artifact in sorted(
            _transfer_catalog(manifest).items()
        ):
            if artifact.source.kind == "local-upload":
                continue
            result[artifact_id] = await self._source_url(
                artifact,
                session,
            )
        return result

    def _local_artifact(self, artifact):
        lookup = getattr(self.job_repository, "get_local_artifact", None)
        if not callable(lookup):
            raise SessionExecutionError(
                "A local dependency artifact is unavailable."
            )
        candidates = [artifact.artifact_id]
        locator = artifact.source.locator
        if (
            artifact.source.kind == "local-upload"
            and isinstance(locator, str)
            and locator.startswith("local-upload:")
        ):
            candidates.append(locator.removeprefix("local-upload:"))
        record = None
        for candidate in candidates:
            record = lookup(candidate)
            if record is not None:
                break
        if (
            record is None
            or record.size_bytes != artifact.size_bytes
            or record.sha256 != artifact.sha256
        ):
            raise SessionExecutionError(
                "A local dependency artifact is unavailable."
            )
        try:
            current = hash_file(record.private_path)
        except ArtifactPathError:
            raise SessionExecutionError(
                "A local dependency artifact is unavailable."
            ) from None
        if (
            current.size_bytes != artifact.size_bytes
            or current.sha256 != artifact.sha256
        ):
            raise SessionExecutionError(
                "A local dependency artifact changed."
            )
        return record

    async def _upload_artifact(self, worker, job_id, artifact):
        upload = getattr(worker, "upload_artifact", None)
        if not callable(upload):
            raise SessionExecutionError(
                "Remote dependency upload is unavailable."
            )
        local = self._local_artifact(artifact)
        existing = self.job_repository.get_transfer(
            job_id,
            artifact.artifact_id,
        )
        if existing is not None and (
            existing.direction != "upload"
            or existing.expected_size != artifact.size_bytes
            or existing.sha256 != artifact.sha256
        ):
            raise TerminalProvisioningError(
                "Stored dependency upload identity changed."
            )
        offset = existing.offset if existing is not None else 0
        if existing is not None and existing.state == TransferState.VERIFIED:
            return
        upload_status = getattr(worker, "upload_status", None)
        if callable(upload_status):
            try:
                remote_status = await upload_status(
                    artifact.artifact_id
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                remote_status = None
            if remote_status is not None:
                if (
                    not isinstance(remote_status, dict)
                    or remote_status.get("artifact_id")
                    != artifact.artifact_id
                    or remote_status.get("size_bytes")
                    != artifact.size_bytes
                    or remote_status.get("sha256") != artifact.sha256
                    or remote_status.get("state")
                    not in {"receiving", "verified"}
                    or isinstance(
                        remote_status.get("next_offset"),
                        bool,
                    )
                    or not isinstance(
                        remote_status.get("next_offset"),
                        int,
                    )
                    or not 0
                    <= remote_status["next_offset"]
                    <= artifact.size_bytes
                    or (
                        remote_status["state"] == "verified"
                        and remote_status["next_offset"]
                        != artifact.size_bytes
                    )
                    or (
                        remote_status["state"] == "receiving"
                        and remote_status["next_offset"]
                        >= artifact.size_bytes
                    )
                ):
                    raise TerminalProvisioningError(
                        "Remote dependency upload identity changed."
                    )
                remote_offset = remote_status["next_offset"]
                if existing is not None and remote_offset < existing.offset:
                    self.job_repository.reset_transfer(
                        job_id,
                        artifact.artifact_id,
                    )
                offset = remote_offset
                if (
                    remote_status["state"] == "verified"
                    and offset == artifact.size_bytes
                ):
                    self.job_repository.save_transfer(
                        job_id=job_id,
                        artifact_id=artifact.artifact_id,
                        direction="upload",
                        expected_size=artifact.size_bytes,
                        sha256=artifact.sha256,
                        offset=offset,
                        state=TransferState.VERIFIED,
                        private_path=local.private_path,
                    )
                    return
        self.job_repository.save_transfer(
            job_id=job_id,
            artifact_id=artifact.artifact_id,
            direction="upload",
            expected_size=artifact.size_bytes,
            sha256=artifact.sha256,
            offset=offset,
            state=TransferState.TRANSFERRING,
            private_path=local.private_path,
        )

        async def progress(next_offset):
            if (
                isinstance(next_offset, bool)
                or not isinstance(next_offset, int)
                or not offset <= next_offset <= artifact.size_bytes
            ):
                raise TerminalProvisioningError(
                    "Remote dependency upload made invalid progress."
                )
            self.job_repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact.artifact_id,
                direction="upload",
                expected_size=artifact.size_bytes,
                sha256=artifact.sha256,
                offset=next_offset,
                state=(
                    TransferState.VERIFIED
                    if next_offset == artifact.size_bytes
                    else TransferState.TRANSFERRING
                ),
                private_path=local.private_path,
            )

        try:
            receipt = await upload(
                artifact.artifact_id,
                path=local.private_path,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                start=offset,
                on_progress=progress,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerBoundaryAuthenticationError:
            raise
        except Exception:
            current = self.job_repository.get_transfer(
                job_id,
                artifact.artifact_id,
            )
            current_offset = current.offset if current is not None else offset
            self.job_repository.save_transfer(
                job_id=job_id,
                artifact_id=artifact.artifact_id,
                direction="upload",
                expected_size=artifact.size_bytes,
                sha256=artifact.sha256,
                offset=current_offset,
                state=TransferState.FAILED,
                private_path=local.private_path,
            )
            raise SessionExecutionError(
                "Remote dependency upload failed."
            ) from None
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "artifact_id",
                "state",
                "next_offset",
                "size_bytes",
                "sha256",
            }
            or receipt.get("artifact_id") != artifact.artifact_id
            or receipt.get("state") != "verified"
            or receipt.get("next_offset") != artifact.size_bytes
            or receipt.get("size_bytes") != artifact.size_bytes
            or receipt.get("sha256") != artifact.sha256
        ):
            raise TerminalProvisioningError(
                "Remote dependency upload was not verified."
            )
        await progress(artifact.size_bytes)

    def _record_provision_progress(
        self,
        response,
        *,
        session,
        manifest,
        transfer_job_id,
    ):
        try:
            progress = _validated_provision_progress(response, manifest)
            if progress is None:
                return
            sanitized_error = {
                "failed": "Remote provisioning failed.",
                "stalled": "Remote provisioning stalled.",
            }.get(response["state"])
            self.job_repository.record_provision_progress(
                transaction_id=_stored_provision_transaction_id(
                    session.session_id,
                    manifest.digest,
                ),
                session_id=session.session_id,
                job_id=transfer_job_id,
                manifest_digest=manifest.digest,
                state=response["state"],
                phase=progress["phase"],
                current_dependency_id=progress["dependency_id"],
                transferred_bytes=progress["transferred_bytes"],
                total_bytes=progress["total_bytes"],
                last_progress_at=self._now(),
                sanitized_error=sanitized_error,
            )
        except (KeyError, TypeError, ValueError):
            raise TerminalProvisioningError(
                "Remote provisioning response was invalid."
            ) from None

    async def _apply_with_progress_polling(
        self,
        worker,
        request,
        *,
        session,
        manifest,
        transfer_job_id,
    ):
        apply_task = asyncio.ensure_future(worker.apply_manifest(request))
        transaction = getattr(worker, "transaction", None)
        transaction_id = "provision-" + manifest.digest
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {apply_task},
                    timeout=self.job_poll_interval_seconds,
                )
                if apply_task in done:
                    response = apply_task.result()
                    break
                if not callable(transaction):
                    continue
                try:
                    observed = await transaction(transaction_id)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except WorkerBoundaryAuthenticationError:
                    raise
                except Exception:
                    continue
                if observed is None:
                    continue
                if not _provision_payload_valid(observed, manifest):
                    raise TerminalProvisioningError(
                        "Remote provisioning response was invalid."
                    )
                self._record_provision_progress(
                    observed,
                    session=session,
                    manifest=manifest,
                    transfer_job_id=transfer_job_id,
                )
            if not _provision_payload_valid(response, manifest):
                raise TerminalProvisioningError(
                    "Remote provisioning response was invalid."
                )
            self._record_provision_progress(
                response,
                session=session,
                manifest=manifest,
                transfer_job_id=transfer_job_id,
            )
            return response
        except BaseException:
            if not apply_task.done():
                apply_task.cancel()
            await asyncio.gather(apply_task, return_exceptions=True)
            raise

    async def _apply_manifest(
        self,
        worker,
        session,
        manifest,
        *,
        transfer_job_id,
        capture,
    ):
        request = {
            "manifest": _manifest_payload(manifest),
            "manifest_digest": manifest.digest,
            "required_class_types": sorted(
                set(capture.executable_class_types)
            ),
            "source_urls": await self._source_urls(manifest, session),
        }
        catalog = _transfer_catalog(manifest)
        for _attempt in range(3):
            try:
                response = await self._apply_with_progress_polling(
                    worker,
                    request,
                    session=session,
                    manifest=manifest,
                    transfer_job_id=transfer_job_id,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except SessionExecutionError:
                raise
            except Exception:
                raise SessionExecutionError(
                    "Remote provisioning failed."
                ) from None
            if not _provision_payload_valid(response, manifest):
                raise TerminalProvisioningError(
                    "Remote provisioning response was invalid."
                )
            required_uploads = response.get("required_uploads", [])
            if required_uploads:
                for artifact_id in required_uploads:
                    artifact = catalog.get(artifact_id)
                    if (
                        artifact is None
                        or artifact.source.kind != "local-upload"
                    ):
                        raise TerminalProvisioningError(
                            "Remote provisioning requested an unknown upload."
                        )
                    await self._upload_artifact(
                        worker,
                        transfer_job_id,
                        artifact,
                    )
                continue
            if response["state"] == "ready":
                return response
            if response["state"] in {"failed", "stalled"}:
                raise TerminalProvisioningError(
                    "Remote provisioning did not become ready."
                )
            transaction = getattr(worker, "transaction", None)
            if not callable(transaction):
                raise SessionExecutionError(
                    "Remote provisioning is incomplete."
                )
            try:
                response = await transaction(response["transaction_id"])
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                raise SessionExecutionError(
                    "Remote provisioning status is unavailable."
                ) from None
            if (
                not _provision_payload_valid(response, manifest)
                or response["state"] != "ready"
            ):
                raise TerminalProvisioningError(
                    "Remote provisioning is incomplete."
                )
            self._record_provision_progress(
                response,
                session=session,
                manifest=manifest,
                transfer_job_id=transfer_job_id,
            )
            return response
        raise TerminalProvisioningError(
            "Remote provisioning did not accept required uploads."
        )

    async def bootstrap_session(self, session_id):
        try:
            return await self._bootstrap_session(session_id)
        except WorkerBoundaryAuthenticationError:
            raise TerminalProvisioningError(
                "Remote worker boundary authentication failed."
            ) from None

    async def _bootstrap_session(self, session_id):
        session = self.session(session_id)
        if session.state not in {
            SessionState.BOOTSTRAPPING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            return session
        if (
            not session.worker_base_url
            or not session.provider_token
            or not session.session_secret_hex
        ):
            raise SessionExecutionError(
                "Remote worker connection is unavailable."
            )
        worker = self._worker(session)
        health = getattr(worker, "health", None)
        claim = getattr(worker, "claim", None)
        deadline = getattr(worker, "update_deadline", None)
        if not all(callable(item) for item in (health, claim, deadline)):
            raise SessionExecutionError("Remote worker is unavailable.")
        try:
            health_payload = await health()
            if health_payload.get("claimed") is not True:
                await claim()
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerBoundaryAuthenticationError:
            raise
        except Exception:
            raise SessionExecutionError(
                "Remote worker authentication failed."
            ) from None
        if session.state == SessionState.BOOTSTRAPPING:
            session = self._sessions().transition_if_state(
                session.session_id,
                SessionState.BOOTSTRAPPING,
                SessionState.PROVISIONING,
                now=self._now(),
            )
        manifest = _stored_manifest(
            self.job_repository,
            session.manifest_digest,
        )
        capture = self.job_repository.get_capture_by_prompt_digest(
            manifest.prompt_digest
        )
        if capture is None:
            raise SessionExecutionError(
                "Initial session capture is unavailable."
            )
        if session.state == SessionState.PROVISIONING:
            await self._apply_manifest(
                worker,
                session,
                manifest,
                transfer_job_id="bootstrap:" + session.session_id,
                capture=capture,
            )
            session = self._sessions().transition(
                session.session_id,
                SessionState.VALIDATING,
                now=self._now(),
            )
        policy = (
            {
                "mode": "finite",
                "deadline_at": session.deadline_at,
                "retrieval_grace_seconds": 300,
            }
            if session.deadline_mode == "finite"
            else {"mode": "none", "acknowledged": True}
        )
        try:
            deadline_result = await deadline(policy)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerBoundaryAuthenticationError:
            raise
        except Exception:
            raise SessionExecutionError(
                "Remote deadline enforcement failed."
            ) from None
        if not _deadline_response_matches(deadline_result, policy):
            raise TerminalProvisioningError(
                "Remote deadline enforcement failed."
            )
        self.job_repository.replace_installed_set(
            session.session_id,
            _installed_records(manifest),
        )
        return self._sessions().transition(
            session.session_id,
            SessionState.READY,
            now=self._now(),
            installed_manifest_digest=manifest.digest,
            sanitized_error=None,
        )

    async def recover_session(self, session_id):
        try:
            return await self._recover_session(session_id)
        except WorkerBoundaryAuthenticationError:
            raise TerminalProvisioningError(
                "Remote worker boundary authentication failed."
            ) from None

    async def _recover_session(self, session_id):
        session = self.session(session_id)
        if session.state not in {
            SessionState.READY,
            SessionState.RUNNING,
            SessionState.HARVESTING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            return session
        if (
            not session.worker_base_url
            or not session.provider_token
            or not session.session_secret_hex
        ):
            raise SessionExecutionError(
                "Remote worker connection is unavailable."
            )
        worker = self._worker(session)
        health = getattr(worker, "health", None)
        claim = getattr(worker, "claim", None)
        deadline = getattr(worker, "update_deadline", None)
        if not all(callable(item) for item in (health, claim, deadline)):
            raise SessionExecutionError("Remote worker is unavailable.")
        try:
            health_payload = await health()
            if health_payload.get("claimed") is not True:
                await claim()
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerBoundaryAuthenticationError:
            raise
        except Exception:
            raise SessionExecutionError(
                "Remote worker authentication failed."
            ) from None
        manifest = _stored_manifest(
            self.job_repository,
            session.installed_manifest_digest,
        )
        if session.state == SessionState.READY:
            capture = self.job_repository.get_capture_by_prompt_digest(
                manifest.prompt_digest
            )
            if capture is None:
                raise SessionExecutionError(
                    "Installed session capture is unavailable."
                )
            await self._apply_manifest(
                worker,
                session,
                manifest,
                transfer_job_id="recovery:" + session.session_id,
                capture=capture,
            )
        policy = (
            {
                "mode": "finite",
                "deadline_at": session.deadline_at,
                "retrieval_grace_seconds": 300,
            }
            if session.deadline_mode == "finite"
            else {"mode": "none", "acknowledged": True}
        )
        try:
            deadline_result = await deadline(policy)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except WorkerBoundaryAuthenticationError:
            raise
        except Exception:
            raise SessionExecutionError(
                "Remote deadline enforcement failed."
            ) from None
        if not _deadline_response_matches(deadline_result, policy):
            raise TerminalProvisioningError(
                "Remote deadline enforcement failed."
            )
        self.job_repository.replace_installed_set(
            session.session_id,
            _installed_records(manifest),
        )
        session = self._sessions().transition(
            session.session_id,
            session.state,
            now=self._now(),
            installed_manifest_digest=manifest.digest,
            sanitized_error=None,
        )
        if session.state in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            await self.resume_session(session.session_id)
            return self.session(session.session_id)
        return session

    async def _finish_remote_job(self, session, job, worker):
        relay = self._relay(worker, session)
        for poll in range(self.max_job_polls):
            try:
                result = await relay.sync_job(job.job_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                raise SessionExecutionError(
                    "Remote job synchronization failed."
                ) from None
            if (
                not isinstance(result, RelaySyncResult)
                or result.job_id != job.job_id
            ):
                raise SessionExecutionError(
                    "Remote job synchronization failed."
                )
            if result.state in {"queued", "running"}:
                if poll + 1 >= self.max_job_polls:
                    break
                await self.sleep(self.job_poll_interval_seconds)
                continue
            if result.state == "succeeded":
                if session.state == SessionState.RUNNING:
                    session = self._sessions().transition_if_state(
                        session.session_id,
                        SessionState.RUNNING,
                        SessionState.HARVESTING,
                        now=self._now(),
                    )
                if job.state == JobState.RUNNING:
                    job = self._transition_job(
                        job,
                        JobState.HARVESTING,
                    )
                if (
                    session.state != SessionState.HARVESTING
                    or job.state != JobState.HARVESTING
                ):
                    raise SessionExecutionError(
                        "Local harvesting state is inconsistent."
                    )
                job = self._transition_job(job, JobState.SUCCEEDED)
                self._sessions().transition_if_state(
                    session.session_id,
                    SessionState.HARVESTING,
                    SessionState.READY,
                    now=self._now(),
                    sanitized_error=None,
                )
                return job
            if result.state in {"failed", "interrupted"}:
                job = self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error=_safe_remote_error(result.error),
                )
                self._sessions().transition_if_state(
                    session.session_id,
                    session.state,
                    SessionState.READY,
                    now=self._now(),
                    sanitized_error=None,
                )
                return job
            raise SessionExecutionError(
                "Remote job state was invalid."
            )
        raise SessionExecutionError("Remote job polling limit was reached.")

    def _schedule_remote_job(self, session, job, worker):
        existing = _JOB_TASKS.get(job.job_id)
        if existing is not None and not existing.done():
            return existing
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        task = loop.create_task(
            self._finish_remote_job(session, job, worker)
        )
        _JOB_TASKS[job.job_id] = task

        def completed(done):
            if _JOB_TASKS.get(job.job_id) is done:
                _JOB_TASKS.pop(job.job_id, None)
            if not done.cancelled():
                try:
                    done.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        task.add_done_callback(completed)
        return task

    async def submit_job(
        self,
        session_id,
        *,
        capture_id,
        idempotency_key,
    ):
        session = self.session(session_id)
        key = _strict_identifier(
            idempotency_key,
            "job idempotency key",
        )
        duplicate = self.job_repository.get_job_by_idempotency_key(
            session.session_id,
            key,
        )
        if duplicate is not None:
            return duplicate
        if session.state != SessionState.READY:
            raise SessionBusy("Cloud Run session is busy.")
        capture = self.job_repository.get_capture(
            _strict_identifier(capture_id, "capture ID")
        )
        if not isinstance(capture, CompiledCapture):
            raise CaptureNotFound("Cloud Run capture was not found.")
        try:
            execution_baseline = certified_execution_baseline(capture)
        except CaptureValidationError:
            raise IncompatibleSession(
                "The current canvas no longer matches the reviewed session."
            ) from None
        if execution_baseline != (
            session.execution_baseline_digest,
            session.randomized_seed_node_ids,
        ):
            raise IncompatibleSession(
                "The current canvas no longer matches the reviewed session."
            )
        installed, desired = await self._fresh_manifest(session, capture)
        delta = ManifestDelta.between(installed, desired)
        if not delta.compatible:
            raise IncompatibleSession(
                "The fresh canvas requires a new Cloud Run session."
            )
        candidate = self._new_job(
            session,
            capture,
            desired.digest,
            key,
        )
        job, created = self.job_repository.create_job(candidate)
        if not created:
            return job
        job = self._transition_job(job, JobState.RESOLVING)
        has_delta = bool(
            delta.custom_nodes or delta.artifacts or delta.wheels
        )
        try:
            session = self._sessions().transition_if_state(
                session.session_id,
                SessionState.READY,
                (
                    SessionState.PROVISIONING
                    if has_delta
                    else SessionState.RUNNING
                ),
                now=self._now(),
            )
        except ConcurrentSessionUpdate:
            self._transition_job(
                job,
                JobState.FAILED,
                sanitized_error="Cloud Run session is busy.",
            )
            raise SessionBusy("Cloud Run session is busy.") from None
        worker = self._worker(session)
        if has_delta:
            try:
                await self._apply_manifest(
                    worker,
                    session,
                    desired,
                    transfer_job_id=job.job_id,
                    capture=capture,
                )
            except TerminalProvisioningError as error:
                diagnostic = str(error)
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error=diagnostic,
                )
                self._sessions().transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=self._now(),
                    sanitized_error=diagnostic,
                )
                await self._request_terminal_destruction(
                    session.session_id,
                    diagnostic,
                )
                raise
            except Exception:
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error="Remote provisioning failed.",
                )
                self._sessions().transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=self._now(),
                    sanitized_error="Remote provisioning failed.",
                )
                raise
            session = self._sessions().transition(
                session.session_id,
                SessionState.VALIDATING,
                now=self._now(),
            )
            self.job_repository.replace_installed_set(
                session.session_id,
                _installed_records(desired),
            )
            session = self._sessions().transition(
                session.session_id,
                SessionState.READY,
                now=self._now(),
                manifest_digest=desired.digest,
                installed_manifest_digest=desired.digest,
                sanitized_error=None,
            )
            session = self._sessions().transition_if_state(
                session.session_id,
                SessionState.READY,
                SessionState.RUNNING,
                now=self._now(),
            )
        elif desired.digest != installed.digest:
            try:
                await self._apply_manifest(
                    worker,
                    session,
                    desired,
                    transfer_job_id=job.job_id,
                    capture=capture,
                )
            except TerminalProvisioningError as error:
                diagnostic = str(error)
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error=diagnostic,
                )
                self._sessions().transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=self._now(),
                    sanitized_error=diagnostic,
                )
                await self._request_terminal_destruction(
                    session.session_id,
                    diagnostic,
                )
                raise
            except Exception:
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error="Remote manifest validation failed.",
                )
                self._sessions().transition(
                    session.session_id,
                    SessionState.FAILED,
                    now=self._now(),
                    sanitized_error="Remote manifest validation failed.",
                )
                raise
            self.job_repository.replace_installed_set(
                session.session_id,
                _installed_records(desired),
            )
            session = self._sessions().transition(
                session.session_id,
                SessionState.RUNNING,
                now=self._now(),
                manifest_digest=desired.digest,
                installed_manifest_digest=desired.digest,
                sanitized_error=None,
            )
        job = self._transition_job(job, JobState.QUEUED)
        payload = json.loads(job.capture_json)
        request = {
            "job_id": job.job_id,
            "manifest_digest": job.manifest_digest,
            "workflow": payload["workflow"],
            "output": payload["output"],
            "queue_options": payload["queue_options"],
        }
        try:
            remote = await worker.start_job(request)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise SessionExecutionError(
                "Remote job submission is unavailable."
            ) from None
        if (
            not isinstance(remote, dict)
            or set(remote)
            != {
                "job_id",
                "state",
                "prompt_id",
                "last_sequence",
                "outputs",
                "error",
            }
            or remote.get("job_id") != job.job_id
            or remote.get("state")
            not in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "interrupted",
            }
        ):
            raise SessionExecutionError(
                "Remote job submission response was invalid."
            )
        prompt_id = remote.get("prompt_id")
        if prompt_id is not None:
            try:
                if str(uuid.UUID(prompt_id)) != prompt_id:
                    raise ValueError("Non-canonical remote prompt ID.")
            except (AttributeError, TypeError, ValueError):
                raise SessionExecutionError(
                    "Remote job submission response was invalid."
                ) from None
        job = self._transition_job(
            job,
            JobState.RUNNING,
            remote_prompt_id=prompt_id,
        )
        if remote["state"] in {"queued", "running"}:
            self._schedule_remote_job(session, job, worker)
            return job
        return await self._finish_remote_job(
            session,
            job,
            worker,
        )

    async def resume_session(self, session_id):
        session = self.session(session_id)
        active = [
            job
            for job in self.job_repository.list_jobs(session.session_id)
            if job.state
            in {
                JobState.CAPTURED,
                JobState.RESOLVING,
                JobState.QUEUED,
                JobState.RUNNING,
                JobState.HARVESTING,
            }
        ]
        if len(active) != 1:
            if not active:
                return session
            raise SessionExecutionError(
                "Multiple active jobs were found for one session."
            )
        job = active[0]
        worker = self._worker(session)
        if session.state in {
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            capture = CompiledCapture.from_record(
                job.job_id,
                job.capture_json,
                job.prompt_digest,
            )
            desired = _stored_manifest(
                self.job_repository,
                job.manifest_digest,
            )
            if session.state == SessionState.PROVISIONING:
                await self._apply_manifest(
                    worker,
                    session,
                    desired,
                    transfer_job_id=job.job_id,
                    capture=capture,
                )
                session = self._sessions().transition(
                    session.session_id,
                    SessionState.VALIDATING,
                    now=self._now(),
                )
            self.job_repository.replace_installed_set(
                session.session_id,
                _installed_records(desired),
            )
            session = self._sessions().transition(
                session.session_id,
                SessionState.READY,
                now=self._now(),
                manifest_digest=desired.digest,
                installed_manifest_digest=desired.digest,
            )
            session = self._sessions().transition_if_state(
                session.session_id,
                SessionState.READY,
                SessionState.RUNNING,
                now=self._now(),
            )
        if session.state not in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            return session
        if job.state == JobState.CAPTURED:
            job = self._transition_job(job, JobState.RESOLVING)
        if job.state == JobState.RESOLVING:
            job = self._transition_job(job, JobState.QUEUED)
        remote = None
        if session.state != SessionState.HARVESTING:
            payload = json.loads(job.capture_json)
            remote = await worker.start_job(
                {
                    "job_id": job.job_id,
                    "manifest_digest": job.manifest_digest,
                    "workflow": payload["workflow"],
                    "output": payload["output"],
                    "queue_options": payload["queue_options"],
                }
            )
            if job.state == JobState.QUEUED:
                job = self._transition_job(job, JobState.RUNNING)
        if (
            session.state == SessionState.HARVESTING
            or not isinstance(remote, dict)
            or remote.get("state") in {"queued", "running"}
        ):
            self._schedule_remote_job(session, job, worker)
            return job
        return await self._finish_remote_job(session, job, worker)

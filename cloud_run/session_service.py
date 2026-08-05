"""Free dependency preflight and the paid-offer gating boundary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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
    canonical_native_prompt_body,
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
from .models import (
    CloudJob,
    ExecutionState,
    HarvestState,
    JobState,
    SessionState,
    TransferState,
)
from .orchestrator import LocalOrchestrator
from .reconciler import SessionReconciler
from .relay import (
    RelaySyncResult,
    RelayValidationError,
)
from .repository import ConcurrentSessionUpdate, SessionRepository
from .run_errors import (
    RunErrorCode,
    RunJournalEntry,
    RunPhase,
)
from .worker_client import (
    MAX_WORKER_NATIVE_RESPONSE_BYTES,
    WorkerBoundaryAuthenticationError,
    WorkerRequest,
    WorkerTransportResponse,
)
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
    "certified_baseline",
    "civitai",
    "core",
    "huggingface",
    "installed_git",
    "local-upload",
    "manual",
    "r2",
    "registry",
}
_AGENT_PANEL_PACKAGE_ID = "comfyui-agent-panel"


def _valid_class_type(value):
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 200
        and value == value.strip()
        and all(32 <= ord(character) <= 126 for character in value)
    )
_AGENT_PANEL_CAPABILITIES = frozenset({
    "graph_read",
    "graph_edit",
    "native_run",
    "native_batch",
})
_RUNTIME_PACKAGE_NAMES = frozenset({"aiohttp", "torch"})
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
DEADLINE_ACTION_SECONDS = {
    "add_30_minutes": 30 * 60,
    "add_1_hour": 60 * 60,
}
DESTROY_REVIEW_TTL_SECONDS = 5 * 60
READINESS_MAX_ATTEMPTS = 6
READINESS_WINDOW_SECONDS = 60
SURFACE_PREEMPT_TIMEOUT_SECONDS = 1
TEARDOWN_PROFILE_TIMEOUT_SECONDS = 5
_MAX_PROFILE_ARCHIVE_BYTES = 128 * 1024 * 1024
_EXTENSION_PATH = re.compile(
    r"/extensions/[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}/"
    r"[A-Za-z0-9._@+/-]+"
)


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


class _DestroyRequested(asyncio.CancelledError):
    """Internal cooperative cancellation after durable destroy intent."""


@dataclass(frozen=True, repr=False)
class NativePromptForward:
    job_id: str
    request_id: str
    manifest_digest: str
    body: bytes = field(repr=False)
    response_callback: object = field(repr=False, compare=False)

    def __post_init__(self):
        _strict_identifier(self.job_id, "native job ID")
        _strict_identifier(self.request_id, "native request ID")
        if (
            not isinstance(self.manifest_digest, str)
            or _HEX_64.fullmatch(self.manifest_digest) is None
            or not isinstance(self.body, bytes)
            or not self.body
            or not callable(self.response_callback)
        ):
            raise SessionServiceError("Invalid native prompt forward.")

    def server_identity(self):
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "manifest_digest": self.manifest_digest,
        }

    async def bind_response(self, status, body):
        result = self.response_callback(
            self.job_id,
            status=status,
            body=body,
        )
        if inspect.isawaitable(result):
            result = await result
        return result


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
    minimum_vram_gb: float = 0.0
    cached_bytes: int = 0

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
        if (
            isinstance(self.minimum_vram_gb, bool)
            or not isinstance(self.minimum_vram_gb, (int, float))
            or not math.isfinite(self.minimum_vram_gb)
            or not 0 <= float(self.minimum_vram_gb) <= 1024
            or type(self.cached_bytes) is not int
            or not 0 <= self.cached_bytes <= self.transfer_bytes
        ):
            raise SessionServiceError("Invalid preflight readiness inputs.")
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
            "minimum_vram_gb": float(self.minimum_vram_gb),
            "cached_bytes": self.cached_bytes,
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
        optional = {"minimum_vram_gb", "cached_bytes"}
        if (
            not isinstance(payload, dict)
            or not fields.issubset(payload)
            or not set(payload).issubset(fields | optional)
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
        values.setdefault("minimum_vram_gb", 0.0)
        values.setdefault("cached_bytes", 0)
        return cls(**values)


def _candidate_for(repository, class_type, source_kind):
    if (
        repository is None
        or source_kind is None
        or source_kind in {"core", "certified_baseline"}
    ):
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


def _node_dependency_id(class_type):
    direct = "node:" + class_type
    if _IDENTIFIER.fullmatch(direct):
        return direct
    return "node:" + hashlib.sha256(
        class_type.encode("utf-8")
    ).hexdigest()


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
                dependency_id=_node_dependency_id(node_row.class_type),
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
    for package in getattr(resolution, "ui_packages", ()):
        identities[("ui-package", package.archive.artifact_id)] = (
            package.archive.size_bytes
        )
    profile = getattr(resolution, "profile", None)
    if profile is not None:
        identities[("profile", profile.archive.artifact_id)] = (
            profile.archive.size_bytes
        )
    return sum(identities.values())


def _cached_bytes(resolution):
    identities = {}
    verified = getattr(resolution, "verified_cached_artifact_ids", ())
    if (
        not isinstance(verified, (tuple, list, frozenset, set))
        or any(not isinstance(item, str) for item in verified)
    ):
        return 0
    verified = frozenset(verified)

    def remember(identity, artifact):
        cache_identities = {
            value
            for value in (
                getattr(artifact, "artifact_id", None),
                getattr(artifact, "sha256", None),
            )
            if isinstance(value, str)
        }
        if (
            getattr(getattr(artifact, "source", None), "kind", None) == "r2"
            and not cache_identities.isdisjoint(verified)
        ):
            identities[identity] = artifact.size_bytes

    for node in resolution.custom_nodes:
        remember(("archive", node.archive.artifact_id), node.archive)
        for wheel in node.wheels:
            remember(("wheel", wheel.filename, wheel.sha256), wheel)
    for artifact in resolution.artifacts:
        remember(("artifact", artifact.artifact_id), artifact)
    for package in getattr(resolution, "ui_packages", ()):
        remember(("ui-package", package.archive.artifact_id), package.archive)
    profile = getattr(resolution, "profile", None)
    if profile is not None:
        remember(("profile", profile.archive.artifact_id), profile.archive)
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
    for package in getattr(resolution, "ui_packages", ()):
        archive = package.archive
        if archive.source.kind == "local-upload":
            identifiers.append(
                (
                    archive.artifact_id,
                    archive.source.locator.removeprefix("local-upload:"),
                )
            )
    profile = getattr(resolution, "profile", None)
    if profile is not None and profile.archive.source.kind == "local-upload":
        identifiers.append(
            (
                profile.archive.artifact_id,
                profile.archive.source.locator.removeprefix("local-upload:"),
            )
        )
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
        for package in payload["ui_packages"]:
            archive = package["archive"]
            identity = ("ui-package", archive["artifact_id"])
            if identity not in seen:
                total += int(archive["size_bytes"])
                seen.add(identity)
        profile = payload["profile"]
        if profile is not None:
            archive = profile["archive"]
            identity = ("profile", archive["artifact_id"])
            if identity not in seen:
                total += int(archive["size_bytes"])
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
        try:
            from ..remote_worker.provision import (
                dependency_manifest_from_record,
            )
        except ImportError:
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
        try:
            from ..remote_worker.transfers import wheel_artifact
        except ImportError:
            from remote_worker.transfers import wheel_artifact

        artifacts = [
            *manifest.artifacts,
            *(node.archive for node in manifest.custom_nodes),
            *(package.archive for package in manifest.ui_packages),
            *(
                wheel_artifact(wheel)
                for node in manifest.custom_nodes
                for wheel in node.wheels
            ),
        ]
        if manifest.profile is not None:
            artifacts.append(manifest.profile.archive)
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
        for package in manifest.ui_packages:
            if package.archive.artifact_id == artifact_id:
                revision = package.revision
                break
        # Profile revisions are monotonic integers, not immutable source-code
        # revisions. Their exact revision is tracked by profile sync state while
        # this installed set binds the archive by digest.
        records.append(
            {
                "dependency_id": artifact_id,
                "digest": artifact.sha256,
                "revision": revision,
                "destination": artifact.destination,
            }
        )
    return records


_REMOTE_ERROR_MESSAGES = {
    RunErrorCode.VALIDATION: ("Cloud Vast validation failed.",),
    RunErrorCode.DEPENDENCY: (
        "A required Cloud Vast dependency is unavailable.",
    ),
    RunErrorCode.TRANSFER: ("Cloud Vast data transfer failed.",),
    RunErrorCode.QUOTE_EXPIRED: ("The quote expired before confirmation.",),
    RunErrorCode.PROVIDER: ("The Vast provider request failed.",),
    RunErrorCode.PROVISIONING: (
        "The remote environment could not become ready.",
    ),
    RunErrorCode.COMFY_STARTUP: ("Remote ComfyUI did not become ready.",),
    RunErrorCode.EXECUTION: (
        "Remote workflow execution failed.",
        "Remote ComfyUI rejected the compiled prompt.",
        "Remote execution ran out of GPU memory.",
        "Remote workflow execution was interrupted.",
    ),
    RunErrorCode.SYNCHRONIZATION: ("Remote job synchronization failed.",),
    RunErrorCode.HARVEST: ("Remote output retrieval failed.",),
    RunErrorCode.INVALID_OUTPUT: ("Remote output metadata was invalid.",),
    RunErrorCode.WORKER_RESTART: ("The worker restarted during execution.",),
    RunErrorCode.LIFECYCLE: ("GPU lifecycle verification failed.",),
    RunErrorCode.INTERNAL: ("An unexpected Cloud Vast error occurred.",),
}
_LEGACY_REMOTE_ERROR_CODES = {
    "validation_failed": RunErrorCode.EXECUTION,
    "out_of_memory": RunErrorCode.EXECUTION,
    "execution_failed": RunErrorCode.EXECUTION,
    "execution_interrupted": RunErrorCode.EXECUTION,
    "worker_restarted": RunErrorCode.WORKER_RESTART,
}
_REMOTE_ERROR_PHASES = {
    RunErrorCode.VALIDATION: RunPhase.PREFLIGHT,
    RunErrorCode.DEPENDENCY: RunPhase.PROVISIONING,
    RunErrorCode.TRANSFER: RunPhase.TRANSFER,
    RunErrorCode.QUOTE_EXPIRED: RunPhase.QUOTE,
    RunErrorCode.PROVIDER: RunPhase.PROVIDER,
    RunErrorCode.PROVISIONING: RunPhase.PROVISIONING,
    RunErrorCode.COMFY_STARTUP: RunPhase.READINESS,
    RunErrorCode.EXECUTION: RunPhase.EXECUTION,
    RunErrorCode.SYNCHRONIZATION: RunPhase.SYNCHRONIZATION,
    RunErrorCode.HARVEST: RunPhase.HARVEST,
    RunErrorCode.INVALID_OUTPUT: RunPhase.HARVEST,
    RunErrorCode.WORKER_RESTART: RunPhase.EXECUTION,
    RunErrorCode.LIFECYCLE: RunPhase.TEARDOWN,
    RunErrorCode.INTERNAL: RunPhase.INTERNAL,
}


def _safe_remote_error(error):
    raw_code = error.get("code") if isinstance(error, dict) else None
    code = _LEGACY_REMOTE_ERROR_CODES.get(raw_code)
    if code is None:
        try:
            code = RunErrorCode(raw_code)
        except (TypeError, ValueError):
            code = RunErrorCode.INTERNAL
    allowed = _REMOTE_ERROR_MESSAGES[code]
    message = error.get("message") if isinstance(error, dict) else None
    return code, (message if message in allowed else allowed[0])


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
    optional = {"required_uploads", "progress", "readiness"}
    if (
        not isinstance(payload, dict)
        or not required.issubset(payload)
        or not set(payload).issubset(required | optional)
    ):
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
    readiness = payload.get("readiness")
    if readiness is not None and (
        payload["state"] != "ready"
        or not _worker_readiness_payload_valid(readiness, manifest)
    ):
        return False
    return True


def _worker_readiness_payload_valid(payload, manifest):
    fields = {
        "protocol_version",
        "comfyui_core_version",
        "comfyui_frontend_version",
        "worker_version",
        "validated_class_types",
        "validated_artifacts",
        "profile_revision",
        "profile_digest",
        "bootstrap_digest",
        "ui_package_digests",
        "served_extension_paths",
        "runtime_package_versions",
        "comfy_process_healthy",
        "completed_at",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return False
    class_types = payload.get("validated_class_types")
    artifacts = payload.get("validated_artifacts")
    expected_artifacts = [
        item.artifact_id
        for item in sorted(
            (
                *manifest.artifacts,
                *(package.archive for package in manifest.ui_packages),
                *((manifest.profile.archive,) if manifest.profile is not None else ()),
            ),
            key=lambda item: (
                item.destination,
                item.artifact_id,
                item.sha256,
            ),
        )
    ]
    expected_ui = {
        item.package_id: item.web_sha256
        for item in sorted(
            manifest.ui_packages,
            key=lambda item: item.package_id,
        )
    }
    profile = manifest.profile
    extension_paths = payload.get("served_extension_paths")
    runtime_versions = payload.get("runtime_package_versions")
    completed_at = payload.get("completed_at")
    return bool(
        payload.get("protocol_version") == manifest.protocol_version
        and payload.get("comfyui_core_version")
        == manifest.comfyui_core_version
        and payload.get("comfyui_frontend_version")
        == manifest.comfyui_frontend_version
        and payload.get("worker_version") == manifest.worker_version
        and isinstance(class_types, list)
        and len(class_types) <= 100_000
        and all(_valid_class_type(item) for item in class_types)
        and class_types == sorted(set(class_types))
        and all(
            class_type in class_types
            for node in manifest.custom_nodes
            for class_type in node.provided_class_types
        )
        and isinstance(artifacts, list)
        and len(artifacts) <= 100_000
        and all(
            isinstance(item, str) and _IDENTIFIER.fullmatch(item)
            for item in artifacts
        )
        and artifacts == expected_artifacts
        and payload.get("profile_revision")
        == (profile.revision if profile is not None else None)
        and payload.get("profile_digest")
        == (profile.archive.sha256 if profile is not None else None)
        and payload.get("bootstrap_digest")
        == (profile.bootstrap_digest if profile is not None else None)
        and payload.get("ui_package_digests") == expected_ui
        and isinstance(extension_paths, list)
        and len(extension_paths) <= 100_000
        and all(isinstance(path, str) for path in extension_paths)
        and extension_paths == sorted(set(extension_paths))
        and all(
            _EXTENSION_PATH.fullmatch(path) is not None
            and "//" not in path
            and "/./" not in path
            and "/../" not in path
            and all(
                component not in {".", ".."}
                for component in path.split("/")
            )
            for path in extension_paths
        )
        and isinstance(runtime_versions, dict)
        and set(runtime_versions) == _RUNTIME_PACKAGE_NAMES
        and all(
            isinstance(version, str)
            and 1 <= len(version) <= 200
            and version == version.strip()
            and all(ord(character) >= 32 for character in version)
            for version in runtime_versions.values()
        )
        and payload.get("comfy_process_healthy") is True
        and not isinstance(completed_at, bool)
        and isinstance(completed_at, (int, float))
        and math.isfinite(completed_at)
        and completed_at >= 0
    )


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
        orchestrator=None,
        reconciler=None,
        profile_store=None,
        agent_bridge=None,
        readiness_validator=None,
        desktop_relay=None,
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
        self.profile_store = profile_store
        if agent_bridge is not None and not all(
            callable(getattr(agent_bridge, method, None))
            for method in ("probe", "revoke")
        ):
            raise ValueError("Invalid Agent Panel bridge.")
        self.agent_bridge = agent_bridge
        if readiness_validator is not None and not all(
            callable(getattr(readiness_validator, method, None))
            for method in ("identity", "validate")
        ):
            raise ValueError("Invalid readiness validator.")
        self.readiness_validator = readiness_validator
        self.desktop_relay = desktop_relay
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
        self.orchestrator = orchestrator or LocalOrchestrator(
            self.job_repository
        )
        self.reconciler = reconciler or SessionReconciler(
            service=self,
            orchestrator=self.orchestrator,
            clock=self.clock,
        )
        self._native_prompt_locks = {}
        self._teardown_tasks = {}
        self._surface_preemptions = set()
        self._surface_tasks = set()

    def _native_prompt_lock(self, session_id):
        loop = asyncio.get_running_loop()
        key = (loop, session_id)
        lock = self._native_prompt_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._native_prompt_locks[key] = lock
        return lock

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
                ui_packages=tuple(
                    getattr(resolution, "ui_packages", ())
                ),
                profile=getattr(resolution, "profile", None),
                minimum_vram_gb=getattr(
                    resolution,
                    "minimum_vram_gb",
                    0.0,
                ),
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
            minimum_vram_gb=float(
                getattr(resolution, "minimum_vram_gb", 0.0)
            ),
            cached_bytes=_cached_bytes(resolution),
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
            or float(manifest.get("minimum_vram_gb", -1))
            != float(result.minimum_vram_gb)
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
        search = (
            self.offer_search
            if callable(self.offer_search)
            else getattr(self.offer_search, "search", None)
        )
        if callable(search):
            offers = await search(
                disk_gb=result.disk_gb,
                workflow_min_vram_gb=result.minimum_vram_gb,
                transfer_bytes=result.transfer_bytes,
                cached_bytes=result.cached_bytes,
                source_ready=result.rentable,
            )
        else:
            raise PreflightBlocked("Vast offer search is unavailable.")
        if not isinstance(offers, list):
            raise PreflightBlocked("Vast offer search is unavailable.")
        return offers

    def matches_paid_preflight(
        self,
        preflight_id,
        *,
        reviewed_manifest_digest,
        reviewed_execution_baseline_digest,
        reviewed_randomized_seed_node_ids,
        reviewed_transfer_bytes,
        reviewed_cached_bytes,
        reviewed_output_allowance_bytes,
        reviewed_disk_gb,
    ):
        """Compare the current canvas/profile to the reviewed paid terms."""
        try:
            current = self.require_rentable_preflight(preflight_id)
            reviewed_json = self.job_repository.get_manifest(
                reviewed_manifest_digest
            )
            current_json = self.job_repository.get_manifest(
                current.manifest_digest
            )
            if not isinstance(reviewed_json, str) or not isinstance(
                current_json,
                str,
            ):
                return False
            reviewed_manifest = json.loads(reviewed_json)
            current_manifest = json.loads(current_json)
            if not isinstance(reviewed_manifest, dict) or not isinstance(
                current_manifest,
                dict,
            ):
                return False
            reviewed_manifest = dict(reviewed_manifest)
            current_manifest = dict(current_manifest)
            reviewed_manifest.pop("prompt_digest", None)
            current_manifest.pop("prompt_digest", None)
            return bool(
                current.execution_baseline_digest
                == reviewed_execution_baseline_digest
                and current.randomized_seed_node_ids
                == tuple(reviewed_randomized_seed_node_ids)
                and current.transfer_bytes == reviewed_transfer_bytes
                and current.cached_bytes == reviewed_cached_bytes
                and current.output_allowance_bytes
                == reviewed_output_allowance_bytes
                and current.disk_gb == reviewed_disk_gb
                and current_manifest == reviewed_manifest
            )
        except (
            json.JSONDecodeError,
            PreflightBlocked,
            PreflightNotFound,
            TypeError,
            ValueError,
        ):
            return False

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

    def _stored_session(self, session_id):
        identifier = _strict_identifier(session_id, "session ID")
        session = self._sessions().get(identifier)
        if session is None:
            raise SessionExecutionError(
                "Cloud Run session was not found."
            )
        return session

    def _raise_if_destroy_requested(self, session_id):
        session = self._stored_session(session_id)
        if session.destroy_requested or session.state in {
            SessionState.DESTROY_REQUESTED,
            SessionState.DESTROYING,
            SessionState.DESTROYED,
        }:
            raise _DestroyRequested()
        return session

    @staticmethod
    def _teardown_pending(session):
        return bool(
            session.state != SessionState.DESTROYED
            and (
                session.destroy_requested
                or session.state
                in {
                    SessionState.DESTROY_REQUESTED,
                    SessionState.DESTROYING,
                }
            )
        )

    def session(self, session_id):
        session = self._stored_session(session_id)
        if session.state in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
        } and self._reconciliation_job(session.session_id) is not None:
            try:
                self.reconciler.schedule(session.session_id)
            except RuntimeError:
                pass
        return session

    def get_job(self, session_id, job_id):
        session = self.session(session_id)
        job = self.job_repository.get_job(
            _strict_identifier(job_id, "job ID")
        )
        if job is None or job.session_id != session.session_id:
            raise SessionExecutionError("Cloud Run job was not found.")
        return job

    def harvest_retry_context(self, job_id):
        identifier = _strict_identifier(job_id, "job ID")
        job = self.job_repository.get_job(identifier)
        if job is None:
            raise SessionExecutionError("Cloud Run job was not found.")
        session = self._stored_session(job.session_id)
        if (
            session.state != SessionState.HARVESTING
            or job.state != JobState.HARVESTING
            or job.execution_state != ExecutionState.SUCCEEDED
            or job.harvest_state
            not in {HarvestState.FAILED, HarvestState.PENDING}
        ):
            raise SessionBusy(
                "Cloud Vast output retrieval is not retryable."
            )
        return {
            "session_id": session.session_id,
            "job_id": job.job_id,
        }

    async def retry_harvest(self, session_id, job_id):
        session = self._stored_session(session_id)
        identifier = _strict_identifier(job_id, "job ID")
        job = self.job_repository.get_job(identifier)
        if job is None or job.session_id != session.session_id:
            raise SessionExecutionError("Cloud Run job was not found.")
        self.harvest_retry_context(job.job_id)
        retry = getattr(self.reconciler, "retry_harvest", None)
        if not callable(retry):
            raise SessionExecutionError(
                "Cloud Vast output retrieval is unavailable."
            )
        await retry(job.job_id)
        current = self.job_repository.get_job(job.job_id)
        if current is None or current.session_id != session.session_id:
            raise SessionExecutionError("Cloud Run job was not found.")
        return current

    def _profile_manifest(self, session):
        digest = session.installed_manifest_digest or session.manifest_digest
        manifest = _stored_manifest(self.job_repository, digest)
        return manifest, manifest.profile

    def _current_readiness_report(self, session, manifest=None):
        validator = self.readiness_validator
        if validator is None:
            return None
        if manifest is None:
            manifest, profile = self._profile_manifest(session)
        else:
            profile = manifest.profile
        try:
            identity = validator.identity(session, manifest, profile)
            return self.job_repository.current_readiness_report(**identity)
        except Exception:
            return None

    def desktop_readiness(self, session_id):
        session = self._stored_session(session_id)
        if self.readiness_validator is None:
            return {
                "desktop_ready": session.state
                in {
                    SessionState.READY,
                    SessionState.RUNNING,
                    SessionState.HARVESTING,
                },
                "readiness_report": None,
            }
        report = self._current_readiness_report(session)
        relay_ready = False
        relay = self.desktop_relay
        status = getattr(relay, "status", None)
        if report is not None and callable(status):
            try:
                current = status()
                relay_ready = bool(
                    getattr(current, "ready", None) is True
                    and getattr(current, "active_session_id", None)
                    == session.session_id
                    and getattr(current, "url", None) == report.relay_origin
                    and getattr(current, "profile_revision", None)
                    == report.profile_revision
                )
            except Exception:
                relay_ready = False
        desktop_ready = bool(
            report is not None
            and report.ready
            and relay_ready
            and session.state
            in {
                SessionState.READY,
                SessionState.RUNNING,
                SessionState.HARVESTING,
            }
        )
        payload = report.public_payload() if report is not None else None
        if payload is not None:
            payload["desktop_ready"] = desktop_ready
        return {
            "desktop_ready": desktop_ready,
            "readiness_report": payload,
        }

    def readiness_certified(self, session_id):
        session = self._stored_session(session_id)
        if self.readiness_validator is None:
            return session.state in {
                SessionState.READY,
                SessionState.RUNNING,
                SessionState.HARVESTING,
            }
        report = self._current_readiness_report(session)
        return bool(report is not None and report.ready)

    def _record_readiness_failure(
        self,
        session,
        report,
        *,
        relay=False,
        retryable=True,
    ):
        failed = tuple(
            item.name for item in report.checks if item.status == "failed"
        )
        entry = RunJournalEntry(
            entry_id=(
                "readiness-relay-" if relay else "readiness-"
            )
            + report.report_digest,
            session_id=session.session_id,
            manifest_digest=report.manifest_digest,
            transaction_id=None,
            job_id=None,
            phase=RunPhase.READINESS,
            code=(
                RunErrorCode.SYNCHRONIZATION
                if relay or "agent_panel_capabilities" in failed
                else RunErrorCode.VALIDATION
            ),
            message=(
                "ComfyUI Vast Desktop activation failed."
                if relay
                else "ComfyUI Vast readiness validation failed."
            ),
            node_id=None,
            process_exit_code=None,
            restart_count=0,
            last_probe=(failed[0] if failed else "loopback_session_binding"),
            byte_cursor=0,
            event_cursor=0,
            output_state=None,
            details={
                "failed_checks": list(failed),
                "retryable": bool(retryable),
            },
            created_at=report.created_at,
        )
        return self.orchestrator.record(entry)

    async def _certify_readiness(self, session, manifest):
        validator = self.readiness_validator
        if validator is None:
            return None
        self._raise_if_destroy_requested(session.session_id)
        try:
            identity = validator.identity(session, manifest, manifest.profile)
            success = self.job_repository.current_readiness_report(**identity)
            if success is not None:
                return success
            attempts = self.job_repository.list_readiness_attempts(**identity)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise SessionExecutionError(
                "ComfyUI Vast readiness validation failed."
            ) from None
        window_start = (
            attempts[0].created_at if attempts else self._now()
        )
        latest = attempts[-1] if attempts else None
        next_attempt = (
            latest.attempt_number + 1 if latest is not None else 1
        )
        if (
            next_attempt > READINESS_MAX_ATTEMPTS
            or self._now() - window_start >= READINESS_WINDOW_SECONDS
        ):
            if latest is not None:
                self._record_readiness_failure(
                    session,
                    latest,
                    retryable=False,
                )
                return latest
            raise SessionExecutionError(
                "ComfyUI Vast readiness validation failed."
            )
        relay = self.desktop_relay
        start = getattr(relay, "start", None)
        if not callable(start):
            raise SessionExecutionError(
                "ComfyUI Vast Desktop readiness is unavailable."
            )
        try:
            self._raise_if_destroy_requested(session.session_id)
            await start()
            while next_attempt <= READINESS_MAX_ATTEMPTS:
                self._raise_if_destroy_requested(session.session_id)
                if (
                    next_attempt > 1
                    and self._now() - window_start
                    >= READINESS_WINDOW_SECONDS
                ):
                    break
                report = await validator.validate(
                    session,
                    manifest,
                    manifest.profile,
                    attempt_number=next_attempt,
                )
                report = self.job_repository.save_readiness_report(report)
                if report.ready:
                    return report
                latest = report
                next_attempt = report.attempt_number + 1
                elapsed = self._now() - window_start
                if (
                    next_attempt > READINESS_MAX_ATTEMPTS
                    or elapsed >= READINESS_WINDOW_SECONDS
                ):
                    break
                delay = min(
                    self.job_poll_interval_seconds,
                    READINESS_WINDOW_SECONDS - elapsed,
                )
                await self.sleep(delay)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise SessionExecutionError(
                "ComfyUI Vast readiness validation failed."
            ) from None
        if latest is None:
            raise SessionExecutionError(
                "ComfyUI Vast readiness validation failed."
            )
        self._record_readiness_failure(
            session,
            latest,
            retryable=False,
        )
        return latest

    async def _activate_committed_readiness(self, session, worker, report):
        if report is None:
            return True
        if not report.ready:
            return False
        relay = self.desktop_relay
        activate = getattr(relay, "activate", None)
        if not callable(activate):
            self._record_readiness_failure(session, report, relay=True)
            return False
        try:
            self._raise_if_destroy_requested(session.session_id)
            status = await activate(
                session.session_id,
                worker,
                report.profile_revision,
            )
            if not (
                getattr(status, "ready", None) is True
                and getattr(status, "active_session_id", None)
                == session.session_id
                and getattr(status, "profile_revision", None)
                == report.profile_revision
                and getattr(status, "url", None) == report.relay_origin
            ):
                raise SessionExecutionError(
                    "ComfyUI Vast Desktop activation failed."
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            self._record_readiness_failure(session, report, relay=True)
            return False
        return True

    def _agent_panel_package(self, session):
        manifest, _profile = self._profile_manifest(session)
        for package in manifest.ui_packages:
            if package.package_id == _AGENT_PANEL_PACKAGE_ID:
                return package
        return None

    def agent_panel_required(self, session_id):
        session = self._stored_session(session_id)
        return self._agent_panel_package(session) is not None

    def record_agent_bridge_issue(self, session_id, code, _diagnostic=None):
        session = self._stored_session(session_id)
        try:
            classified = RunErrorCode(code)
        except (TypeError, ValueError):
            raise SessionExecutionError(
                "Agent Panel bridge issue was rejected."
            ) from None
        if classified not in {
            RunErrorCode.DEPENDENCY,
            RunErrorCode.SYNCHRONIZATION,
        }:
            raise SessionExecutionError(
                "Agent Panel bridge issue was rejected."
            )
        job = None
        try:
            job = self._reconciliation_job(session.session_id)
        except Exception:
            job = None
        event_cursor = 0
        if job is not None:
            try:
                event_cursor = self.job_repository.last_event_sequence(
                    job.job_id
                )
            except Exception:
                event_cursor = 0
        digest = (
            session.installed_manifest_digest
            or session.manifest_digest
        )
        message = (
            "Agent Panel dependency validation failed."
            if classified == RunErrorCode.DEPENDENCY
            else "Agent Panel bridge synchronization failed."
        )
        entry = RunJournalEntry(
            entry_id=uuid.uuid4().hex,
            session_id=session.session_id,
            manifest_digest=digest,
            transaction_id=None,
            job_id=job.job_id if job is not None else None,
            phase=(
                RunPhase.PROVISIONING
                if classified == RunErrorCode.DEPENDENCY
                else RunPhase.SYNCHRONIZATION
            ),
            code=classified,
            message=message,
            node_id=None,
            process_exit_code=None,
            restart_count=0,
            last_probe="agent_panel",
            byte_cursor=0,
            event_cursor=event_cursor,
            output_state=(
                job.harvest_state.value if job is not None else None
            ),
            details={
                "component": "agent_panel_bridge",
                "retryable": True,
            },
            created_at=self._now(),
        )
        return self.orchestrator.record(entry)

    async def probe_agent_panel(self, session_id, bridge_session):
        session = self._stored_session(session_id)
        package = self._agent_panel_package(session)
        if package is None:
            return {
                "required": False,
                "status": "not_required",
                "ready": True,
            }
        capabilities = frozenset(package.required_capabilities)
        bridge = self.agent_bridge
        report = None
        if capabilities == _AGENT_PANEL_CAPABILITIES and bridge is not None:
            try:
                report = await bridge.probe(bridge_session)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                report = None
        ready = bool(
            report is not None
            and getattr(report, "ready", None) is True
            and callable(getattr(report, "public_payload", None))
        )
        if ready:
            payload = report.public_payload()
            if not isinstance(payload, dict) or payload.get("ready") is not True:
                ready = False
        if not ready:
            self.record_agent_bridge_issue(
                session.session_id,
                RunErrorCode.SYNCHRONIZATION,
            )
            return {
                "required": True,
                "status": "failed",
                "ready": False,
            }
        return {
            "required": True,
            "status": "passed",
            **payload,
        }

    async def _revoke_agent_bridge(self, session_id):
        bridge = self.agent_bridge
        if bridge is None:
            return
        try:
            result = bridge.revoke(session_id)
            if inspect.isawaitable(result):
                await result
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            self.record_agent_bridge_issue(
                session_id,
                RunErrorCode.SYNCHRONIZATION,
            )
            raise SessionExecutionError(
                "Agent Panel bridge revocation failed."
            ) from None

    def _track_surface_task(self, task):
        self._surface_tasks.add(task)

        def finished(completed):
            self._surface_tasks.discard(completed)
            if not completed.cancelled():
                try:
                    completed.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        task.add_done_callback(finished)
        return task

    @staticmethod
    async def _bounded_surface_cleanup(awaitable):
        try:
            await asyncio.wait_for(
                awaitable,
                timeout=SURFACE_PREEMPT_TIMEOUT_SECONDS,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            pass

    async def close_surface_tasks(self):
        tasks = set(self._surface_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._surface_tasks.difference_update(tasks)

    async def preempt_session_surfaces(self, session_id):
        identifier = _strict_identifier(session_id, "session ID")
        if identifier in self._surface_preemptions:
            return
        self._surface_preemptions.add(identifier)
        relay = self.desktop_relay
        relay_preempt = getattr(relay, "preempt", None)
        relay_preemption_started = False
        if callable(relay_preempt):
            try:
                result = relay_preempt(identifier)
                relay_preemption_started = True
                if inspect.isawaitable(result):
                    self._track_surface_task(
                        asyncio.create_task(
                            result,
                            name="cloud-vast-relay-preempt-" + identifier,
                        )
                    )
            except Exception:
                pass
        bridge = self.agent_bridge
        revoke = getattr(bridge, "revoke", None)
        relay_owns_bridge = bool(
            relay_preemption_started
            and getattr(relay, "agent_bridge", None) is bridge
        )
        if callable(revoke) and not relay_owns_bridge:
            try:
                result = revoke(identifier)
                if inspect.isawaitable(result):
                    self._track_surface_task(
                        asyncio.create_task(
                            self._bounded_surface_cleanup(result),
                            name="cloud-vast-agent-revoke-" + identifier,
                        )
                    )
            except Exception:
                pass
        await asyncio.sleep(0)

    def _mark_profile_applied(self, session, profile):
        if profile is None or self.profile_store is None:
            return None
        prior = self.job_repository.get_profile_sync_state(
            session.session_id
        )
        if prior is not None and prior["remote_revision"] > profile.revision:
            return prior
        return self.job_repository.save_profile_sync_state(
            {
                "session_id": session.session_id,
                "profile_id": profile.profile_id,
                "remote_revision": profile.revision,
                "archive_sha256": profile.archive.sha256,
                "warning": None,
                "updated_at": self._now(),
            }
        )

    def _profile_status(self, session):
        if self.profile_store is None:
            return {
                "profile_id": None,
                "local_revision": None,
                "remote_revision": None,
                "state": "unavailable",
                "warning": "Desktop profile synchronization is unavailable.",
                "conflicts": [],
            }
        latest = self.profile_store.latest()
        sync = self.job_repository.get_profile_sync_state(
            session.session_id
        )
        conflicts = self.profile_store.conflicts(unresolved_only=True)
        warning = sync["warning"] if sync is not None else None
        remote_revision = sync["remote_revision"] if sync is not None else None
        remote_digest = sync["archive_sha256"] if sync is not None else None
        if conflicts:
            state = "conflict"
        elif warning is not None:
            state = "warning"
        elif (
            latest is not None
            and sync is not None
            and latest.archive_sha256 == remote_digest
        ):
            state = "synchronized"
        elif latest is not None:
            state = "local_changes_pending"
        else:
            state = "unavailable"
        return {
            "profile_id": (
                latest.profile_id
                if latest is not None
                else (sync["profile_id"] if sync is not None else None)
            ),
            "local_revision": (
                latest.revision if latest is not None else None
            ),
            "remote_revision": remote_revision,
            "state": state,
            "warning": warning,
            "conflicts": [
                (
                    conflict.public_payload()
                    if callable(getattr(conflict, "public_payload", None))
                    else {
                        "conflict_id": conflict.conflict_id,
                        "profile_id": conflict.profile_id,
                        "base_revision": conflict.base_revision,
                        "local_revision": conflict.local_revision,
                        "remote_revision": conflict.remote_revision,
                        "resolved_revision": conflict.resolved_revision,
                        "local_label": conflict.local_label,
                        "remote_label": conflict.remote_label,
                    }
                )
                for conflict in conflicts
            ],
        }

    async def sync_profile(
        self,
        session_id,
        *,
        worker=None,
        teardown=False,
    ):
        session = self._stored_session(session_id)
        if teardown is not True:
            self._raise_if_destroy_requested(session.session_id)
        if self.profile_store is None:
            return self._profile_status(session)
        try:
            _manifest, manifest_profile = self._profile_manifest(session)
        except Exception:
            return self._profile_status(session)
        if manifest_profile is None:
            return self._profile_status(session)
        sync = self.job_repository.get_profile_sync_state(
            session.session_id
        )
        if sync is None:
            sync = self._mark_profile_applied(session, manifest_profile)
        warning = "Desktop profile synchronization is temporarily unavailable."
        try:
            remote = worker if worker is not None else self._worker(session)
            snapshot_method = getattr(remote, "profile_snapshot", None)
            download = getattr(remote, "download_profile_artifact", None)
            if not callable(snapshot_method) or not callable(download):
                raise SessionExecutionError(
                    "Remote Desktop profile is unavailable."
                )
            if teardown is not True:
                self._raise_if_destroy_requested(session.session_id)
            payload = await snapshot_method(sync["remote_revision"])
            if teardown is not True:
                self._raise_if_destroy_requested(session.session_id)
            if payload is not None:
                if payload.get("profile_id") != manifest_profile.profile_id:
                    raise SessionExecutionError(
                        "Remote Desktop profile identity changed."
                    )
                chunks = []
                received = 0

                def on_chunk(chunk):
                    nonlocal received
                    if not isinstance(chunk, bytes):
                        raise SessionExecutionError(
                            "Remote Desktop profile archive is invalid."
                        )
                    received += len(chunk)
                    if received > _MAX_PROFILE_ARCHIVE_BYTES:
                        raise SessionExecutionError(
                            "Remote Desktop profile archive is invalid."
                        )
                    chunks.append(chunk)

                if teardown is not True:
                    self._raise_if_destroy_requested(session.session_id)
                receipt = await download(
                    payload["archive_artifact_id"],
                    start=0,
                    on_chunk=on_chunk,
                )
                if teardown is not True:
                    self._raise_if_destroy_requested(session.session_id)
                archive = b"".join(chunks)
                if (
                    getattr(receipt, "artifact_id", None)
                    != payload["archive_artifact_id"]
                    or getattr(receipt, "start", None) != 0
                    or getattr(receipt, "total_size", None) != len(archive)
                    or getattr(receipt, "sha256", None)
                    != payload["archive_sha256"]
                    or getattr(receipt, "mime_type", None)
                    != "application/gzip"
                    or len(archive) != payload["archive_size_bytes"]
                    or hashlib.sha256(archive).hexdigest()
                    != payload["archive_sha256"]
                ):
                    raise SessionExecutionError(
                        "Remote Desktop profile archive is invalid."
                    )
                self.profile_store.apply_remote_payload(payload, archive)
                sync = self.job_repository.save_profile_sync_state(
                    {
                        "session_id": session.session_id,
                        "profile_id": payload["profile_id"],
                        "remote_revision": payload["revision"],
                        "archive_sha256": payload["archive_sha256"],
                        "warning": None,
                        "updated_at": self._now(),
                    }
                )
            elif sync["warning"] is not None:
                sync = self.job_repository.save_profile_sync_state(
                    {**sync, "warning": None, "updated_at": self._now()}
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            self.job_repository.save_profile_sync_state(
                {**sync, "warning": warning, "updated_at": self._now()}
            )
        return self._profile_status(session)

    async def resolve_profile_conflict(
        self,
        session_id,
        conflict_id,
        *,
        winner,
    ):
        session = self._stored_session(session_id)
        if self.profile_store is None:
            raise SessionExecutionError(
                "Desktop profile synchronization is unavailable."
            )
        self.profile_store.resolve_conflict(conflict_id, winner=winner)
        return self._profile_status(session)

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
            session = self._stored_session(session_id)
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
        session = self._stored_session(session_id)
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
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            _manifest, profile = self._profile_manifest(session)
            review, reviewed_session = self._sessions().save_destroy_review(
                session.session_id,
                token_digest=digest,
                expires_at=expires_at,
                profile_id=(
                    profile.profile_id if profile is not None else None
                ),
            )
            if (
                review.session_id != reviewed_session.session_id
                or review.managed_label != reviewed_session.label
                or review.instance_id != reviewed_session.instance_id
                or review.residual_instance_ids
                != reviewed_session.residual_inventory
                or reviewed_session.state == SessionState.DESTROYED
            ):
                raise ValueError("Destroy review snapshot changed.")
        except Exception:
            raise DestroyConfirmationError(
                "A destruction review could not be created."
            ) from None
        return DestroyReview(
            session_id=reviewed_session.session_id,
            instance_id=review.instance_id,
            status=reviewed_session.state.value,
            unverified_artifact_ids=review.unverified_artifact_ids,
            warning=(
                "Destroying the GPU is irreversible. Unverified or "
                "incomplete results will be irreversibly lost."
            ),
            token=token,
            expires_at=review.expires_at,
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
        session = self._stored_session(session_id)
        if session.state != SessionState.DESTROYED:
            raise SessionExecutionError(
                "Deadline destruction is not inventory verified."
            )
        self._abandon_session_work(session.session_id)

    def confirmed_terminal_destroy(self, session_id):
        session = self._stored_session(session_id)
        if session.state != SessionState.DESTROYED:
            raise SessionExecutionError(
                "Terminal destruction is not inventory verified."
            )
        self._abandon_session_work(session.session_id)

    async def prepare_deadline_destroy(self, session_id):
        session = self._stored_session(session_id)
        await self.preempt_session_surfaces(session.session_id)
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
        for attempt in range(3):
            try:
                await self.reconciler.before_teardown(session.session_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
            current = self.job_repository.get_job(job.job_id)
            if current is not None and current.state in {
                JobState.SUCCEEDED,
                JobState.FAILED,
            }:
                return
            if attempt < 2:
                await self.sleep(self.job_poll_interval_seconds)

    async def destroy(self, session_id, confirmation):
        session = self._stored_session(session_id)
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
        consumed = self._sessions().consume_destroy_review_and_request_destroy(
            session.session_id,
            token_digest=digest,
            now=self._now(),
        )
        if consumed is None:
            raise DestroyConfirmationError(
                "The destruction review is invalid or expired."
            )
        reviewed, requested = consumed
        if (
            reviewed.session_id != session.session_id
            or requested.session_id != session.session_id
            or requested.label != reviewed.managed_label
            or requested.instance_id != reviewed.instance_id
            or requested.residual_inventory
            != reviewed.residual_instance_ids
            or requested.state != SessionState.DESTROY_REQUESTED
            or requested.destroy_requested is not True
        ):
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            )
        task = self._ensure_teardown_task(requested.session_id)
        return await asyncio.shield(task)

    def _ensure_teardown_task(self, session_id):
        identifier = _strict_identifier(session_id, "session ID")
        existing = self._teardown_tasks.get(identifier)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.get_running_loop().create_task(
            self._teardown_session(identifier),
            name="cloud-vast-teardown-" + identifier,
        )
        self._teardown_tasks[identifier] = task

        def finished(completed):
            if self._teardown_tasks.get(identifier) is completed:
                self._teardown_tasks.pop(identifier, None)
            if not completed.cancelled():
                try:
                    completed.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        task.add_done_callback(finished)
        return task

    async def _teardown_session(self, session_id):
        await self.preempt_session_surfaces(session_id)
        preempt = getattr(self.reconciler, "preempt", None)
        if callable(preempt):
            try:
                await preempt(session_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        preempt_lifecycle = getattr(self.lifecycle, "preempt_session", None)
        if callable(preempt_lifecycle):
            try:
                await preempt_lifecycle(session_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        try:
            await asyncio.wait_for(
                self.sync_profile(session_id, teardown=True),
                timeout=TEARDOWN_PROFILE_TIMEOUT_SECONDS,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            pass
        destroy = getattr(self.lifecycle, "destroy_session", None)
        if not callable(destroy):
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            )
        try:
            result = await destroy(session_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            ) from None
        if (
            not hasattr(result, "state")
            or result.session_id != session_id
        ):
            raise SessionExecutionError(
                "Verified GPU destruction is unavailable."
            )
        if result.state == SessionState.DESTROYED:
            self._abandon_session_work(session_id)
        return result

    async def _request_terminal_destruction(self, session_id, diagnostic):
        destroy = getattr(self.lifecycle, "destroy_session", None)
        if not callable(destroy):
            return None
        try:
            await self.preempt_session_surfaces(session_id)
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
        if not callable(getattr(relay, "sync_snapshot", None)):
            raise SessionExecutionError("Local relay is unavailable.")
        return relay

    def _new_job(
        self,
        session,
        capture,
        manifest_digest,
        key,
        *,
        native_request_digest=None,
        native_body_json=None,
    ):
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
            native_request_digest=native_request_digest,
            native_body_json=native_body_json,
        )

    def _native_forward(self, job):
        if (
            job.native_request_digest is None
            or job.native_body_json is None
        ):
            raise SessionExecutionError(
                "Native prompt identity is unavailable."
            )
        return NativePromptForward(
            job_id=job.job_id,
            request_id=job.idempotency_key,
            manifest_digest=job.manifest_digest,
            body=job.native_body_json.encode("utf-8"),
            response_callback=self.bind_native_prompt_response,
        )

    def _fail_native_dependency(
        self,
        job,
        message="Native prompt dependency validation failed.",
    ):
        failed = self._transition_job(
            job,
            JobState.FAILED,
            sanitized_error=message,
            error_code=RunErrorCode.DEPENDENCY,
        )
        self._record_run_issue(
            failed,
            phase=RunPhase.PREFLIGHT,
            code=RunErrorCode.DEPENDENCY,
            message=message,
            retryable=False,
        )
        return failed

    async def bind_native_prompt_response(self, job_id, *, status, body):
        job = self.job_repository.get_job(
            _strict_identifier(job_id, "native job ID")
        )
        if job is None or job.native_request_digest is None:
            raise SessionExecutionError(
                "Native prompt identity is unavailable."
            )
        if (
            isinstance(status, bool)
            or not isinstance(status, int)
            or status != 200
            or not isinstance(body, bytes)
            or not 0 < len(body) <= 2 * 1024 * 1024
        ):
            raise SessionExecutionError(
                "Native prompt response is unavailable."
            )
        try:
            response = json.loads(body.decode("utf-8"))
            prompt_id = response.get("prompt_id")
            if (
                not isinstance(response, dict)
                or set(response) != {"prompt_id", "number", "node_errors"}
                or str(uuid.UUID(prompt_id)) != prompt_id
                or isinstance(response["number"], bool)
                or not isinstance(response["number"], (int, float))
                or not math.isfinite(response["number"])
                or not isinstance(response["node_errors"], dict)
            ):
                raise ValueError("invalid response")
        except (AttributeError, KeyError, TypeError, UnicodeError, ValueError):
            raise SessionExecutionError(
                "Native prompt response is unavailable."
            ) from None
        if job.remote_prompt_id is not None:
            if job.remote_prompt_id != prompt_id:
                raise SessionExecutionError(
                    "Native prompt response identity changed."
                )
            return job
        if job.state != JobState.QUEUED:
            raise SessionExecutionError(
                "Native prompt response arrived out of order."
            )
        return self._transition_job(
            job,
            JobState.RUNNING,
            remote_prompt_id=prompt_id,
            execution_state=ExecutionState.QUEUED,
            sanitized_error=None,
            error_code=None,
        )

    async def _submit_native_worker_request(self, worker, job):
        self._raise_if_destroy_requested(job.session_id)
        if job.native_body_json is None:
            raise SessionExecutionError(
                "Native prompt identity is unavailable."
            )
        native_envelope = getattr(worker, "native_envelope", None)
        transport = getattr(worker, "transport", None)
        request_method = getattr(transport, "request", None)
        if not callable(native_envelope) or not callable(request_method):
            raise SessionExecutionError(
                "Native prompt recovery is unavailable."
            )
        body = job.native_body_json.encode("utf-8")
        try:
            request = native_envelope(
                "POST",
                "/prompt",
                body,
                identity={
                    "job_id": job.job_id,
                    "request_id": job.idempotency_key,
                    "manifest_digest": job.manifest_digest,
                },
                headers={"Content-Type": "application/json"},
            )
            if not isinstance(request, WorkerRequest):
                raise SessionExecutionError(
                    "Native prompt recovery is unavailable."
                )
            self._raise_if_destroy_requested(job.session_id)
            response = await request_method(
                request,
                max_bytes=MAX_WORKER_NATIVE_RESPONSE_BYTES,
            )
            self._raise_if_destroy_requested(job.session_id)
            if not isinstance(response, WorkerTransportResponse):
                raise SessionExecutionError(
                    "Native prompt recovery is unavailable."
                )
            return await self.bind_native_prompt_response(
                job.job_id,
                status=response.status,
                body=response.body,
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except SessionServiceError:
            raise
        except Exception:
            raise SessionExecutionError(
                "Native prompt recovery is unavailable."
            ) from None

    async def prepare_native_prompt(
        self,
        session_id,
        *,
        request_id,
        body,
    ):
        identifier = _strict_identifier(session_id, "session ID")
        key = _strict_identifier(request_id, "native request identity")
        try:
            canonical_body = canonical_native_prompt_body(body)
            capture = CompiledCapture.from_native_prompt(canonical_body)
        except CaptureValidationError:
            raise SessionServiceError("Invalid native prompt.") from None
        request_digest = hashlib.sha256(canonical_body).hexdigest()
        native_body_json = canonical_body.decode("utf-8")

        async with self._native_prompt_lock(identifier):
            session = self._stored_session(identifier)
            existing = self.job_repository.get_job_by_idempotency_key(
                identifier,
                key,
            )
            job = existing
            if existing is not None:
                if (
                    existing.native_request_digest != request_digest
                    or existing.native_body_json != native_body_json
                ):
                    raise SessionServiceError(
                        "Native prompt identity was already used."
                    )
                if existing.state == JobState.FAILED:
                    raise IncompatibleSession(
                        existing.sanitized_error
                        or "Native prompt dependency validation failed."
                    )
                if existing.state in {
                    JobState.QUEUED,
                    JobState.RUNNING,
                    JobState.HARVESTING,
                    JobState.SUCCEEDED,
                }:
                    return self._native_forward(existing)
                if (
                    existing.state == JobState.RESOLVING
                    and session.state
                    in {SessionState.PROVISIONING, SessionState.VALIDATING}
                ):
                    await self.resume_session(identifier)
                    resumed = self.job_repository.get_job(existing.job_id)
                    if resumed is None:
                        raise SessionExecutionError(
                            "Native prompt identity is unavailable."
                        )
                    return self._native_forward(resumed)

            if existing is None:
                active = [
                    item
                    for item in self.job_repository.list_jobs(identifier)
                    if item.state
                    in {
                        JobState.CAPTURED,
                        JobState.RESOLVING,
                        JobState.QUEUED,
                        JobState.RUNNING,
                        JobState.HARVESTING,
                    }
                ]
                if session.state == SessionState.READY:
                    pass
                elif session.state in {
                    SessionState.RUNNING,
                    SessionState.HARVESTING,
                } and active:
                    pass
                else:
                    raise SessionBusy("Cloud Run session is busy.")
            installed_digest = session.installed_manifest_digest
            if (
                not isinstance(installed_digest, str)
                or _HEX_64.fullmatch(installed_digest) is None
            ):
                raise SessionExecutionError(
                    "The ComfyUI Vast environment is not validated."
                )

            self.job_repository.save_capture(
                capture,
                created_at=self._now(),
            )
            if job is None:
                candidate = self._new_job(
                    session,
                    capture,
                    installed_digest,
                    key,
                    native_request_digest=request_digest,
                    native_body_json=native_body_json,
                )
                job, created = self.job_repository.create_job(candidate)
                if not created:
                    if (
                        job.native_request_digest != request_digest
                        or job.native_body_json != native_body_json
                    ):
                        raise SessionServiceError(
                            "Native prompt identity was already used."
                        )
                    return self._native_forward(job)

            try:
                installed, desired = await self._fresh_manifest(
                    session,
                    capture,
                )
                delta = ManifestDelta.between(installed, desired)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                self._fail_native_dependency(job)
                raise IncompatibleSession(
                    "Native prompt dependency validation failed."
                ) from None
            if not delta.compatible:
                message = (
                    "The native canvas requires a new Cloud Vast session."
                )
                self._fail_native_dependency(job, message)
                raise IncompatibleSession(message)

            if desired.digest != job.manifest_digest:
                job = self._transition_job(
                    job,
                    job.state,
                    manifest_digest=desired.digest,
                )
            job = self._transition_job(job, JobState.RESOLVING)
            session = await self._wait_for_native_turn(job)
            current = self.job_repository.get_job(job.job_id)
            if current is not None and current.state in {
                JobState.RUNNING,
                JobState.HARVESTING,
            }:
                return self._native_forward(current)
            worker = self._worker(session)
            if desired.digest != installed.digest:
                try:
                    session = self._sessions().transition_if_state(
                        session.session_id,
                        SessionState.READY,
                        SessionState.PROVISIONING,
                        now=self._now(),
                    )
                    await self._apply_manifest(
                        worker,
                        session,
                        desired,
                        transfer_job_id=job.job_id,
                        capture=capture,
                    )
                    self._mark_profile_applied(session, desired.profile)
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
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    failed = self._transition_job(
                        job,
                        JobState.FAILED,
                        sanitized_error="Native prompt provisioning failed.",
                        error_code=RunErrorCode.PROVISIONING,
                    )
                    self._record_run_issue(
                        failed,
                        phase=RunPhase.PROVISIONING,
                        code=RunErrorCode.PROVISIONING,
                        message="Native prompt provisioning failed.",
                        retryable=True,
                    )
                    raise SessionExecutionError(
                        "Native prompt provisioning failed."
                    ) from None

            try:
                session = self._sessions().transition_if_state(
                    session.session_id,
                    SessionState.READY,
                    SessionState.RUNNING,
                    now=self._now(),
                )
            except ConcurrentSessionUpdate:
                self._transition_job(
                    job,
                    JobState.FAILED,
                    sanitized_error="Cloud Run session is busy.",
                    error_code=RunErrorCode.SYNCHRONIZATION,
                )
                raise SessionBusy("Cloud Run session is busy.") from None
            job = self._transition_job(
                job,
                JobState.QUEUED,
                execution_state=ExecutionState.QUEUED,
                sanitized_error=None,
                error_code=None,
            )
            return self._native_forward(job)

    def _transition_job(self, job, state, **changes):
        return self.job_repository.save_job(
            job.transition(
                state,
                now=self._now(),
                **changes,
            )
        )

    def _reconciliation_job(self, session_id):
        active = [
            job
            for job in self.job_repository.list_jobs(session_id)
            if job.state
            in {
                JobState.CAPTURED,
                JobState.RESOLVING,
                JobState.QUEUED,
                JobState.RUNNING,
                JobState.HARVESTING,
            }
        ]
        executing = [
            job
            for job in active
            if job.state in {JobState.RUNNING, JobState.HARVESTING}
        ]
        if len(executing) > 1:
            raise SessionExecutionError(
                "Multiple GPU executions were found for one session."
            )
        if executing:
            return executing[0]
        return active[0] if active else None

    async def _wait_for_native_turn(self, job):
        active_states = {
            JobState.CAPTURED,
            JobState.RESOLVING,
            JobState.QUEUED,
            JobState.RUNNING,
            JobState.HARVESTING,
        }
        for _attempt in range(self.max_job_polls):
            current = self.job_repository.get_job(job.job_id)
            if (
                current is not None
                and current.state
                in {JobState.RUNNING, JobState.HARVESTING}
                and current.remote_prompt_id is not None
            ):
                return self._stored_session(job.session_id)
            predecessors = [
                item
                for item in self.job_repository.list_jobs(job.session_id)
                if item.queue_position < job.queue_position
                and item.state in active_states
            ]
            if not predecessors:
                session = self._stored_session(job.session_id)
                if session.state == SessionState.READY:
                    return session
            else:
                predecessor = predecessors[0]
                if predecessor.state in {
                    JobState.RUNNING,
                    JobState.HARVESTING,
                }:
                    try:
                        await self.reconcile_session_once(job.session_id)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception:
                        pass
                elif predecessor.state in {
                    JobState.CAPTURED,
                    JobState.RESOLVING,
                    JobState.QUEUED,
                }:
                    try:
                        await self.resume_session(job.session_id)
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception:
                        pass
            await self.sleep(self.job_poll_interval_seconds)
        raise SessionBusy("The native GPU queue did not advance.")

    async def _advance_native_queue(self, session_id, queue_position):
        pending = [
            item
            for item in self.job_repository.list_jobs(session_id)
            if item.queue_position > queue_position
            and item.native_body_json is not None
            and item.state
            in {JobState.CAPTURED, JobState.RESOLVING, JobState.QUEUED}
        ]
        if not pending:
            return None
        next_job = pending[0]
        try:
            worker = self._worker(self._stored_session(session_id))
        except Exception:
            return None
        if (
            not callable(getattr(worker, "native_envelope", None))
            or not callable(
                getattr(getattr(worker, "transport", None), "request", None)
            )
        ):
            return None
        try:
            return await self.resume_session(session_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._retryable_job_issue(
                next_job,
                phase=RunPhase.SYNCHRONIZATION,
                code=RunErrorCode.SYNCHRONIZATION,
                message="Native GPU queue recovery failed.",
            )

    def reconciliation_context(self, session_id):
        identifier = _strict_identifier(session_id, "session ID")
        job = self._reconciliation_job(identifier)
        session = self._sessions().get(identifier)
        return {
            "session_id": identifier,
            "manifest_digest": (
                job.manifest_digest
                if job is not None
                else (
                    session.manifest_digest
                    if session is not None
                    else None
                )
            ),
            "job_id": job.job_id if job is not None else None,
            "event_cursor": (
                self.job_repository.last_event_sequence(job.job_id)
                if job is not None
                else 0
            ),
            "output_state": (
                job.harvest_state.value if job is not None else None
            ),
        }

    def recoverable_session_ids(self):
        return tuple(
            session.session_id
            for session in self._sessions().list_recoverable()
            if self._teardown_pending(session)
            or self._reconciliation_job(session.session_id) is not None
        )

    def _record_run_issue(self, job, *, phase, code, message, retryable):
        correlation_id = uuid.uuid4().hex
        self.orchestrator.record(
            RunJournalEntry(
                entry_id=uuid.uuid4().hex,
                session_id=job.session_id,
                manifest_digest=job.manifest_digest,
                transaction_id=None,
                job_id=job.job_id,
                phase=phase,
                code=code,
                message=message,
                node_id=None,
                process_exit_code=None,
                restart_count=0,
                last_probe=None,
                byte_cursor=0,
                event_cursor=self.job_repository.last_event_sequence(
                    job.job_id
                ),
                output_state=job.harvest_state.value,
                details={
                    "correlation_id": correlation_id,
                    "retryable": retryable,
                },
                created_at=self._now(),
            )
        )

    def _retryable_job_issue(self, job, *, phase, code, message):
        updated = self._transition_job(
            job,
            job.state,
            sanitized_error=message,
            error_code=code,
        )
        self._record_run_issue(
            updated,
            phase=phase,
            code=code,
            message=message,
            retryable=True,
        )
        return updated

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
            self._raise_if_destroy_requested(session.session_id)
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

    async def _upload_artifact(
        self,
        worker,
        job_id,
        artifact,
        *,
        session_id=None,
    ):
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
                if session_id is not None:
                    self._raise_if_destroy_requested(session_id)
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
            if session_id is not None:
                self._raise_if_destroy_requested(session_id)
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
        self._raise_if_destroy_requested(session.session_id)
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
                self._raise_if_destroy_requested(session.session_id)
                if not callable(transaction):
                    continue
                try:
                    self._raise_if_destroy_requested(session.session_id)
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
        self._raise_if_destroy_requested(session.session_id)
        transaction_id = "provision-" + manifest.digest
        local_transaction = self.job_repository.get_provision_transaction(
            _stored_provision_transaction_id(
                session.session_id,
                manifest.digest,
            )
        )
        transaction = getattr(worker, "transaction", None)
        if local_transaction is not None:
            if (
                local_transaction.session_id != session.session_id
                or local_transaction.manifest_digest != manifest.digest
                or local_transaction.job_id != transfer_job_id
            ):
                raise TerminalProvisioningError(
                    "Stored provisioning transaction is invalid."
                )
        if local_transaction is not None and callable(transaction):
            try:
                self._raise_if_destroy_requested(session.session_id)
                existing = await transaction(transaction_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                existing = None
            if existing is not None:
                if not _provision_payload_valid(existing, manifest):
                    raise TerminalProvisioningError(
                        "Remote provisioning response was invalid."
                    )
                if existing["state"] == "ready":
                    self._record_provision_progress(
                        existing,
                        session=session,
                        manifest=manifest,
                        transfer_job_id=transfer_job_id,
                    )
                    return existing
        self._raise_if_destroy_requested(session.session_id)
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
                    self._raise_if_destroy_requested(session.session_id)
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
                        session_id=session.session_id,
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
                self._raise_if_destroy_requested(session.session_id)
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
        session = self._stored_session(session_id)
        if self._teardown_pending(session):
            return await asyncio.shield(
                self._ensure_teardown_task(session.session_id)
            )
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
            self._raise_if_destroy_requested(session.session_id)
            health_payload = await health()
            if health_payload.get("claimed") is not True:
                self._raise_if_destroy_requested(session.session_id)
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
            self._mark_profile_applied(session, manifest.profile)
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
            self._raise_if_destroy_requested(session.session_id)
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
        report = await self._certify_readiness(session, manifest)
        if report is not None and not report.ready:
            return self._stored_session(session.session_id)
        ready = self._sessions().transition(
            session.session_id,
            SessionState.READY,
            now=self._now(),
            installed_manifest_digest=manifest.digest,
            sanitized_error=None,
        )
        await self._activate_committed_readiness(ready, worker, report)
        return ready

    async def recover_session(self, session_id):
        try:
            return await self._recover_session(session_id)
        except WorkerBoundaryAuthenticationError:
            raise TerminalProvisioningError(
                "Remote worker boundary authentication failed."
            ) from None

    async def _recover_session(self, session_id):
        session = self._stored_session(session_id)
        if self._teardown_pending(session):
            return await asyncio.shield(
                self._ensure_teardown_task(session.session_id)
            )
        self._raise_if_destroy_requested(session.session_id)
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
            self._raise_if_destroy_requested(session.session_id)
            health_payload = await health()
            if health_payload.get("claimed") is not True:
                self._raise_if_destroy_requested(session.session_id)
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
            self._mark_profile_applied(session, manifest.profile)
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
            self._raise_if_destroy_requested(session.session_id)
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
        self._raise_if_destroy_requested(session.session_id)
        await self.sync_profile(session.session_id, worker=worker)
        if session.state in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            await self.resume_session(session.session_id)
            await self.reconciler.schedule(session.session_id)
            return self._stored_session(session.session_id)
        report = self._current_readiness_report(session, manifest)
        if self.readiness_validator is not None and report is None:
            report = await self._certify_readiness(session, manifest)
        await self._activate_committed_readiness(session, worker, report)
        return session

    async def reconcile_session_once(self, session_id):
        session = self._stored_session(session_id)
        if self._teardown_pending(session):
            return await asyncio.shield(
                self._ensure_teardown_task(session.session_id)
            )
        self._raise_if_destroy_requested(session.session_id)
        job = self._reconciliation_job(session.session_id)
        if job is None:
            return session
        if job.state in {
            JobState.CAPTURED,
            JobState.RESOLVING,
            JobState.QUEUED,
        }:
            return await self.resume_session(session.session_id)
        try:
            worker = self._worker(session)
            snapshot_method = getattr(worker, "snapshot", None)
            if not callable(snapshot_method):
                raise SessionExecutionError(
                    "Remote worker snapshot is unavailable."
                )
            relay = self._relay(worker, session)
            cursor = self.job_repository.last_event_sequence(job.job_id)
            self._raise_if_destroy_requested(session.session_id)
            snapshot = await snapshot_method(job.job_id, cursor)
            self._raise_if_destroy_requested(session.session_id)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._retryable_job_issue(
                job,
                phase=RunPhase.SYNCHRONIZATION,
                code=RunErrorCode.SYNCHRONIZATION,
                message="Remote job synchronization failed.",
            )
        if (
            not isinstance(snapshot, dict)
            or set(snapshot)
            != {
                "job_id",
                "state",
                "prompt_id",
                "events",
                "last_sequence",
                "outputs",
                "error",
                "created_at",
                "updated_at",
            }
            or snapshot.get("job_id") != job.job_id
            or snapshot.get("state")
            not in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "interrupted",
            }
        ):
            return self._retryable_job_issue(
                job,
                phase=RunPhase.SYNCHRONIZATION,
                code=RunErrorCode.SYNCHRONIZATION,
                message="Remote job synchronization failed.",
            )
        prompt_id = snapshot.get("prompt_id")
        if prompt_id is not None:
            try:
                if str(uuid.UUID(prompt_id)) != prompt_id:
                    raise ValueError("Non-canonical remote prompt ID.")
            except (AttributeError, TypeError, ValueError):
                return self._retryable_job_issue(
                    job,
                    phase=RunPhase.SYNCHRONIZATION,
                    code=RunErrorCode.SYNCHRONIZATION,
                    message="Remote job synchronization failed.",
                )
        remote_prompt_id = prompt_id or job.remote_prompt_id
        remote_state = snapshot["state"]

        if remote_state in {"queued", "running"}:
            if job.state != JobState.RUNNING:
                return self._retryable_job_issue(
                    job,
                    phase=RunPhase.SYNCHRONIZATION,
                    code=RunErrorCode.SYNCHRONIZATION,
                    message="Remote job synchronization failed.",
                )
            job = self._transition_job(
                job,
                job.state,
                remote_prompt_id=remote_prompt_id,
                execution_state=(
                    ExecutionState.QUEUED
                    if remote_state == "queued"
                    else ExecutionState.RUNNING
                ),
                sanitized_error=None,
                error_code=None,
            )
            try:
                self._raise_if_destroy_requested(session.session_id)
                result = await relay.sync_snapshot(job.job_id, snapshot)
                if (
                    not isinstance(result, RelaySyncResult)
                    or result.job_id != job.job_id
                    or result.state != remote_state
                ):
                    raise RelayValidationError(
                        "Remote snapshot did not match the local job."
                    )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                return self._retryable_job_issue(
                    job,
                    phase=RunPhase.SYNCHRONIZATION,
                    code=RunErrorCode.SYNCHRONIZATION,
                    message="Remote job synchronization failed.",
                )
            return self.job_repository.get_job(job.job_id)

        if remote_state == "succeeded":
            if job.state == JobState.RUNNING:
                job = self._transition_job(
                    job,
                    JobState.HARVESTING,
                    remote_prompt_id=remote_prompt_id,
                    execution_state=ExecutionState.SUCCEEDED,
                    harvest_state=HarvestState.RUNNING,
                    sanitized_error=None,
                    error_code=None,
                )
            elif job.state == JobState.HARVESTING:
                job = self._transition_job(
                    job,
                    job.state,
                    remote_prompt_id=remote_prompt_id,
                    execution_state=ExecutionState.SUCCEEDED,
                    harvest_state=HarvestState.RUNNING,
                    sanitized_error=None,
                    error_code=None,
                )
            else:
                return job
            current_session = self._stored_session(session.session_id)
            if current_session.state == SessionState.READY:
                current_session = self._sessions().transition_if_state(
                    current_session.session_id,
                    SessionState.READY,
                    SessionState.RUNNING,
                    now=self._now(),
                )
            if current_session.state == SessionState.RUNNING:
                current_session = self._sessions().transition_if_state(
                    current_session.session_id,
                    SessionState.RUNNING,
                    SessionState.HARVESTING,
                    now=self._now(),
                )
            try:
                self._raise_if_destroy_requested(session.session_id)
                result = await relay.sync_snapshot(job.job_id, snapshot)
                if (
                    not isinstance(result, RelaySyncResult)
                    or result.job_id != job.job_id
                    or result.state != "succeeded"
                ):
                    raise RelayValidationError(
                        "Remote output metadata was invalid."
                    )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except RelayValidationError:
                failed = self._transition_job(
                    job,
                    job.state,
                    harvest_state=HarvestState.FAILED,
                    sanitized_error="Remote output metadata was invalid.",
                    error_code=RunErrorCode.INVALID_OUTPUT,
                )
                self._record_run_issue(
                    failed,
                    phase=RunPhase.HARVEST,
                    code=RunErrorCode.INVALID_OUTPUT,
                    message="Remote output metadata was invalid.",
                    retryable=True,
                )
                return failed
            except Exception:
                failed = self._transition_job(
                    job,
                    job.state,
                    harvest_state=HarvestState.FAILED,
                    sanitized_error="Remote output retrieval failed.",
                    error_code=RunErrorCode.HARVEST,
                )
                self._record_run_issue(
                    failed,
                    phase=RunPhase.HARVEST,
                    code=RunErrorCode.HARVEST,
                    message="Remote output retrieval failed.",
                    retryable=True,
                )
                return failed
            job = self._transition_job(
                job,
                JobState.SUCCEEDED,
                execution_state=ExecutionState.SUCCEEDED,
                harvest_state=HarvestState.SUCCEEDED,
                sanitized_error=None,
                error_code=None,
            )
            self._sessions().transition_if_state(
                current_session.session_id,
                SessionState.HARVESTING,
                SessionState.READY,
                now=self._now(),
                sanitized_error=None,
            )
            await self._advance_native_queue(
                current_session.session_id,
                job.queue_position,
            )
            return job

        execution_state = (
            ExecutionState.FAILED
            if remote_state == "failed"
            else ExecutionState.INTERRUPTED
        )
        job = self._transition_job(
            job,
            job.state,
            remote_prompt_id=remote_prompt_id,
            execution_state=execution_state,
            sanitized_error=None,
            error_code=None,
        )
        try:
            self._raise_if_destroy_requested(session.session_id)
            result = await relay.sync_snapshot(job.job_id, snapshot)
            if (
                not isinstance(result, RelaySyncResult)
                or result.job_id != job.job_id
                or result.state != remote_state
            ):
                raise RelayValidationError(
                    "Remote failure snapshot did not match the local job."
                )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return self._retryable_job_issue(
                job,
                phase=RunPhase.SYNCHRONIZATION,
                code=RunErrorCode.SYNCHRONIZATION,
                message="Remote job synchronization failed.",
            )
        remote_code, remote_message = _safe_remote_error(result.error)
        job = self._transition_job(
            job,
            JobState.FAILED,
            execution_state=execution_state,
            sanitized_error=remote_message,
            error_code=remote_code,
        )
        self._record_run_issue(
            job,
            phase=_REMOTE_ERROR_PHASES[remote_code],
            code=remote_code,
            message=remote_message,
            retryable=bool(
                remote_code
                in {
                    RunErrorCode.TRANSFER,
                    RunErrorCode.QUOTE_EXPIRED,
                    RunErrorCode.PROVIDER,
                    RunErrorCode.PROVISIONING,
                    RunErrorCode.COMFY_STARTUP,
                    RunErrorCode.SYNCHRONIZATION,
                    RunErrorCode.HARVEST,
                    RunErrorCode.INVALID_OUTPUT,
                    RunErrorCode.WORKER_RESTART,
                    RunErrorCode.LIFECYCLE,
                    RunErrorCode.INTERNAL,
                }
            ),
        )
        current_session = self._stored_session(session.session_id)
        if current_session.state in {
            SessionState.RUNNING,
            SessionState.HARVESTING,
        }:
            self._sessions().transition_if_state(
                current_session.session_id,
                current_session.state,
                SessionState.READY,
                now=self._now(),
                sanitized_error=None,
            )
        await self._advance_native_queue(
            current_session.session_id,
            job.queue_position,
        )
        return job

    async def submit_job(
        self,
        session_id,
        *,
        capture_id,
        idempotency_key,
    ):
        session = self._stored_session(session_id)
        key = _strict_identifier(
            idempotency_key,
            "job idempotency key",
        )
        duplicate = self.job_repository.get_job_by_idempotency_key(
            session.session_id,
            key,
        )
        if duplicate is not None:
            if duplicate.state in {
                JobState.RUNNING,
                JobState.HARVESTING,
            }:
                self.reconciler.schedule(session.session_id)
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
                self._mark_profile_applied(session, desired.profile)
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
                self._mark_profile_applied(session, desired.profile)
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
        job = self._transition_job(
            job,
            JobState.QUEUED,
            execution_state=ExecutionState.QUEUED,
        )
        payload = json.loads(job.capture_json)
        request = {
            "job_id": job.job_id,
            "manifest_digest": job.manifest_digest,
            "workflow": payload["workflow"],
            "output": payload["output"],
            "queue_options": payload["queue_options"],
        }
        try:
            self._raise_if_destroy_requested(session.session_id)
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
        self._raise_if_destroy_requested(session.session_id)
        job = self._transition_job(
            job,
            JobState.RUNNING,
            remote_prompt_id=prompt_id,
            execution_state=(
                ExecutionState.QUEUED
                if remote["state"] == "queued"
                else (
                    ExecutionState.RUNNING
                    if remote["state"] == "running"
                    else job.execution_state
                )
            ),
        )
        self._raise_if_destroy_requested(session.session_id)
        if remote["state"] in {"queued", "running"}:
            self.reconciler.schedule(session.session_id)
            return job
        await self.reconciler.reconcile(session.session_id)
        return self.job_repository.get_job(
            job.job_id
        )

    async def resume_session(self, session_id):
        session = self._stored_session(session_id)
        if self._teardown_pending(session):
            return await asyncio.shield(
                self._ensure_teardown_task(session.session_id)
            )
        self._raise_if_destroy_requested(session.session_id)
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
        if not active:
            return session
        executing = [
            item
            for item in active
            if item.state in {JobState.RUNNING, JobState.HARVESTING}
        ]
        if len(executing) > 1:
            raise SessionExecutionError(
                "Multiple GPU executions were found for one session."
            )
        job = executing[0] if executing else active[0]
        if job.native_body_json is not None and job.state == JobState.CAPTURED:
            await self.prepare_native_prompt(
                session.session_id,
                request_id=job.idempotency_key,
                body=job.native_body_json.encode("utf-8"),
            )
            session = self._stored_session(session.session_id)
            job = self.job_repository.get_job(job.job_id)
            if job is None:
                raise SessionExecutionError(
                    "Native prompt identity is unavailable."
                )
        worker = self._worker(session)
        if session.state in {
            SessionState.PROVISIONING,
            SessionState.VALIDATING,
        }:
            if job.native_body_json is not None:
                capture = CompiledCapture.from_native_prompt(
                    job.native_body_json.encode("utf-8")
                )
            else:
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
                self._mark_profile_applied(session, desired.profile)
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
        elif (
            session.state == SessionState.READY
            and job.native_body_json is not None
            and job.state == JobState.RESOLVING
            and session.installed_manifest_digest != job.manifest_digest
        ):
            capture = CompiledCapture.from_native_prompt(
                job.native_body_json.encode("utf-8")
            )
            desired = _stored_manifest(
                self.job_repository,
                job.manifest_digest,
            )
            session = self._sessions().transition_if_state(
                session.session_id,
                SessionState.READY,
                SessionState.PROVISIONING,
                now=self._now(),
            )
            await self._apply_manifest(
                worker,
                session,
                desired,
                transfer_job_id=job.job_id,
                capture=capture,
            )
            self._mark_profile_applied(session, desired.profile)
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
        if (
            session.state == SessionState.READY
            and job.state in {JobState.RESOLVING, JobState.QUEUED}
        ):
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
            job = self._transition_job(
                job,
                JobState.QUEUED,
                execution_state=ExecutionState.QUEUED,
            )
        if job.state == JobState.QUEUED:
            if job.native_body_json is not None:
                self._raise_if_destroy_requested(session.session_id)
                job = await self._submit_native_worker_request(worker, job)
                self._raise_if_destroy_requested(session.session_id)
                self.reconciler.schedule(session.session_id)
                return job
            payload = json.loads(job.capture_json)
            self._raise_if_destroy_requested(session.session_id)
            remote = await worker.start_job(
                {
                    "job_id": job.job_id,
                    "manifest_digest": job.manifest_digest,
                    "workflow": payload["workflow"],
                    "output": payload["output"],
                    "queue_options": payload["queue_options"],
                }
            )
            self._raise_if_destroy_requested(session.session_id)
            if (
                not isinstance(remote, dict)
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
            job = self._transition_job(
                job,
                JobState.RUNNING,
                remote_prompt_id=remote.get("prompt_id"),
                execution_state=(
                    ExecutionState.QUEUED
                    if remote["state"] == "queued"
                    else (
                        ExecutionState.RUNNING
                        if remote["state"] == "running"
                        else job.execution_state
                    )
                ),
            )
            self._raise_if_destroy_requested(session.session_id)
            if remote["state"] not in {"queued", "running"}:
                await self.reconciler.reconcile(session.session_id)
                return self.job_repository.get_job(job.job_id)
        if job.state in {JobState.RUNNING, JobState.HARVESTING}:
            self.reconciler.schedule(session.session_id)
            return job
        return job

"""Free dependency preflight and the paid-offer gating boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import time
import uuid

from .manifest import (
    DependencyManifest,
    MANIFEST_SCHEMA_VERSION,
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
)
from .worker_release import WorkerRelease


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_STATUSES = {"resolved", "mapping_required", "unsupported"}
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


class SessionServiceError(RuntimeError):
    pass


class CaptureNotFound(SessionServiceError):
    pass


class PreflightNotFound(SessionServiceError):
    pass


class PreflightBlocked(SessionServiceError):
    pass


def _identifier(value, name):
    normalized = str(value or "")
    if not _IDENTIFIER.fullmatch(normalized):
        raise SessionServiceError(f"Invalid {name}.")
    return normalized


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
        if not isinstance(payload, dict) or set(payload) != fields:
            raise SessionServiceError("Stored preflight row is invalid.")
        return cls(**payload)


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
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != fields
            or not isinstance(payload["rows"], list)
        ):
            raise SessionServiceError("Stored preflight is invalid.")
        values = dict(payload)
        values["rows"] = tuple(
            PreflightRow.from_payload(row) for row in payload["rows"]
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


class SessionService:
    def __init__(
        self,
        *,
        job_repository,
        resolver,
        offer_search=None,
        mapping_repository=None,
        release,
        clock=None,
        id_factory=None,
    ):
        self.job_repository = job_repository
        self.resolver = resolver
        self.offer_search = offer_search
        self.mapping_repository = mapping_repository
        self.release = release if isinstance(release, WorkerRelease) else None
        self.clock = clock or time.time
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    async def preflight(
        self,
        capture_id,
        *,
        explicit_output_allowance_bytes=None,
    ):
        capture = self.job_repository.get_capture(str(capture_id))
        if capture is None:
            raise CaptureNotFound("Cloud Run capture was not found.")
        resolution = await self.resolver.resolve_preflight(
            capture,
            explicit_output_allowance_bytes=(
                explicit_output_allowance_bytes
            ),
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
        if (
            capture is None
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
            return await self.offer_search(disk_gb=result.disk_gb)
        if callable(getattr(self.offer_search, "search", None)):
            return await self.offer_search.search(disk_gb=result.disk_gb)
        raise PreflightBlocked("Vast offer search is unavailable.")

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

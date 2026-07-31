"""Ordered, fail-closed resolution of workflow node dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .artifacts import (
    ArtifactCollisionError,
    calculate_disk_gb,
    estimate_output_bytes,
    resolve_artifacts,
)
from .comfy_host import HostCompatibilityError, NodeNotFound
from .dependency_repository import MappingValidationError
from .manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    PythonWheelSpec,
    SourceSpec,
    validate_dependency,
)
from .registry import RegistryError
from .r2 import LocalCacheArtifact


@dataclass(frozen=True)
class NodeResolution:
    class_type: str
    status: str
    source_kind: str | None
    candidate_digest: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class NodeResolutionResult:
    rows: tuple[NodeResolution, ...]
    rentable: bool


@dataclass(frozen=True)
class DependencyPreflightResult:
    node_rows: tuple[NodeResolution, ...]
    artifact_rows: tuple
    custom_nodes: tuple[CustomNodeSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]
    output_allowance_bytes: int
    disk_gb: int
    rentable: bool


def _candidate_is_complete(candidate, class_type):
    payload = candidate.payload
    return (
        isinstance(payload.get("package_id"), str)
        and isinstance(payload.get("archive"), dict)
        and isinstance(payload.get("wheels"), list)
        and isinstance(payload.get("provided_class_types"), list)
        and class_type in payload["provided_class_types"]
    )


def _first_candidate(repository, class_type, source_kind):
    return next(
        (
            candidate
            for candidate in repository.candidates(class_type)
            if candidate.source_kind == source_kind
        ),
        None,
    )


def _custom_node_from_mapping(candidate):
    payload = candidate.payload
    archive_payload = payload["archive"]
    archive = ArtifactSpec(
        artifact_id=archive_payload["artifact_id"],
        kind="custom_node_archive",
        logical_name=payload["package_id"],
        destination=archive_payload["destination"],
        size_bytes=archive_payload["size_bytes"],
        sha256=archive_payload["sha256"],
        source=SourceSpec(
            kind="local-upload",
            locator=archive_payload["locator"],
        ),
    )
    wheels = tuple(
        PythonWheelSpec(
            filename=wheel["filename"],
            size_bytes=wheel["size_bytes"],
            sha256=wheel["sha256"],
            source=SourceSpec(
                kind="local-upload",
                locator=wheel["locator"],
            ),
        )
        for wheel in payload["wheels"]
    )
    node = CustomNodeSpec(
        package_id=payload["package_id"],
        repository_url=candidate.repository_url,
        revision=candidate.revision,
        archive=archive,
        wheels=wheels,
        provided_class_types=tuple(payload["provided_class_types"]),
    )
    validate_dependency(node)
    return node


class DependencyResolver:
    """Resolve nodes without installing, uploading, renting, or executing."""

    def __init__(
        self,
        *,
        host,
        repository,
        registry,
        cache_catalog=None,
        resolution_context=None,
    ):
        self.host = host
        self.repository = repository
        self.registry = registry
        self.cache_catalog = cache_catalog
        self.resolution_context = resolution_context

    def register_agent_suggestion(self, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "class_type",
            "candidate",
        }:
            raise MappingValidationError("Invalid Agent Panel suggestion.")
        return self.repository.save_candidate(
            payload["class_type"],
            "agent",
            payload["candidate"],
            approved=False,
        )

    async def _registry_candidate(self, class_type):
        stored = _first_candidate(
            self.repository,
            class_type,
            "registry",
        )
        if stored is not None:
            return stored
        try:
            inferred = await self.registry.infer_package(class_type)
        except RegistryError:
            return None
        if inferred is None:
            return None
        try:
            return self.repository.save_candidate(
                class_type,
                "registry",
                {
                    "package_id": inferred.package_id,
                    "repository_url": inferred.repository_url,
                    "revision": inferred.revision,
                },
                approved=False,
            )
        except MappingValidationError:
            return None

    async def _resolve_one(self, class_type):
        description = None
        try:
            description = self.host.describe_node(class_type)
        except NodeNotFound:
            pass
        except HostCompatibilityError:
            return NodeResolution(
                class_type=class_type,
                status="unsupported",
                source_kind=None,
                reason="Local node origin is unsupported.",
            )

        if description is not None and description.kind == "core":
            return NodeResolution(
                class_type=class_type,
                status="resolved",
                source_kind="core",
            )

        approved = self.repository.approved(class_type)
        if approved is not None:
            return NodeResolution(
                class_type=class_type,
                status=(
                    "resolved"
                    if _candidate_is_complete(approved, class_type)
                    else "mapping_required"
                ),
                source_kind="approved",
                candidate_digest=approved.candidate_digest,
                reason=(
                    None
                    if _candidate_is_complete(approved, class_type)
                    else "Approved package metadata is incomplete."
                ),
            )

        registry_candidate = await self._registry_candidate(class_type)
        if registry_candidate is not None:
            return NodeResolution(
                class_type=class_type,
                status="mapping_required",
                source_kind="registry",
                candidate_digest=registry_candidate.candidate_digest,
                reason="Registry candidate requires explicit approval.",
            )

        if (
            description is not None
            and description.repository_url is not None
            and description.revision is not None
        ):
            try:
                installed = self.repository.save_candidate(
                    class_type,
                    "installed_git",
                    {
                        "repository_url": description.repository_url,
                        "revision": description.revision,
                    },
                    approved=False,
                )
            except MappingValidationError:
                installed = None
            if installed is not None:
                return NodeResolution(
                    class_type=class_type,
                    status="mapping_required",
                    source_kind="installed_git",
                    candidate_digest=installed.candidate_digest,
                    reason="Installed Git candidate requires explicit approval.",
                )

        for source_kind in ("agent", "manual"):
            candidate = _first_candidate(
                self.repository,
                class_type,
                source_kind,
            )
            if candidate is not None:
                return NodeResolution(
                    class_type=class_type,
                    status="mapping_required",
                    source_kind=source_kind,
                    candidate_digest=candidate.candidate_digest,
                    reason=f"{source_kind.title()} candidate requires approval.",
                )

        return NodeResolution(
            class_type=class_type,
            status="mapping_required",
            source_kind=None,
            reason="No approved immutable source is available.",
        )

    async def resolve_nodes(self, capture):
        self.host.assert_compatible()
        rows = []
        seen = set()
        for raw_class_type in capture.executable_class_types:
            class_type = str(raw_class_type)
            if class_type in seen:
                continue
            seen.add(class_type)
            rows.append(await self._resolve_one(class_type))
        resolved = tuple(rows)
        return NodeResolutionResult(
            rows=resolved,
            rentable=all(row.status == "resolved" for row in resolved),
        )

    async def resolve_dependencies(
        self,
        capture,
        *,
        metadata,
        model_roots,
        input_root,
        source_mappings,
        base_bytes,
        explicit_output_allowance_bytes,
    ):
        nodes = await self.resolve_nodes(capture)
        artifact_result = resolve_artifacts(
            capture,
            metadata=metadata,
            model_roots=model_roots,
            input_root=input_root,
            source_mappings=source_mappings,
        )
        artifact_rows = artifact_result.rows
        if self.cache_catalog is not None:
            registered = set()
            for local in artifact_result.local_artifacts:
                try:
                    self.cache_catalog.register_local_artifact(
                        LocalCacheArtifact(
                            artifact_id=local.artifact_id,
                            private_path=local.private_path,
                            size_bytes=local.size_bytes,
                            sha256=local.sha256,
                        )
                    )
                except Exception:
                    continue
                registered.add(local.artifact_id)
            try:
                cache_configured = (
                    self.cache_catalog.cache_configured() is True
                )
            except Exception:
                cache_configured = False
            if cache_configured:
                artifact_rows = tuple(
                    replace(
                        row,
                        cache_available=row.artifact_id in registered,
                    )
                    for row in artifact_rows
                )
        output_allowance = estimate_output_bytes(
            capture,
            explicit_bytes=explicit_output_allowance_bytes,
        )

        custom_nodes_by_package = {}
        for row in nodes.rows:
            if row.status != "resolved" or row.source_kind != "approved":
                continue
            candidate = self.repository.approved(row.class_type)
            if candidate is None or not _candidate_is_complete(
                candidate,
                row.class_type,
            ):
                continue
            custom_node = _custom_node_from_mapping(candidate)
            existing = custom_nodes_by_package.get(custom_node.package_id)
            if existing is not None and existing != custom_node:
                raise ArtifactCollisionError(
                    "Custom-node package has conflicting approved mappings."
                )
            custom_nodes_by_package[custom_node.package_id] = custom_node
        custom_nodes = tuple(
            custom_nodes_by_package[key]
            for key in sorted(custom_nodes_by_package)
        )

        custom_dependency_bytes = sum(
            node.archive.size_bytes
            + sum(wheel.size_bytes for wheel in node.wheels)
            for node in custom_nodes
        )
        model_bytes = sum(
            artifact.size_bytes
            for artifact in artifact_result.artifacts
            if artifact.kind == "model"
        )
        input_bytes = sum(
            artifact.size_bytes
            for artifact in artifact_result.artifacts
            if artifact.kind == "input"
        )
        disk_gb = calculate_disk_gb(
            base_bytes=base_bytes,
            dependency_bytes=custom_dependency_bytes + model_bytes,
            input_bytes=input_bytes,
            output_bytes=output_allowance,
        )
        return DependencyPreflightResult(
            node_rows=nodes.rows,
            artifact_rows=artifact_rows,
            custom_nodes=custom_nodes,
            artifacts=artifact_result.artifacts,
            output_allowance_bytes=output_allowance,
            disk_gb=disk_gb,
            rentable=nodes.rentable and artifact_result.rentable,
        )

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes=None,
    ):
        context = self.resolution_context
        if callable(context):
            context = context(capture)
        required = {
            "metadata",
            "model_roots",
            "input_root",
            "source_mappings",
            "base_bytes",
        }
        if not isinstance(context, dict) or set(context) != required:
            raise MappingValidationError(
                "Dependency resolution context is unavailable."
            )
        return await self.resolve_dependencies(
            capture,
            metadata=context["metadata"],
            model_roots=context["model_roots"],
            input_root=context["input_root"],
            source_mappings=context["source_mappings"],
            base_bytes=context["base_bytes"],
            explicit_output_allowance_bytes=(
                explicit_output_allowance_bytes
            ),
        )

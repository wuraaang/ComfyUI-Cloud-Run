"""Ordered, fail-closed resolution of workflow node dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from .comfy_host import HostCompatibilityError, NodeNotFound
from .dependency_repository import MappingValidationError
from .registry import RegistryError


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


def _candidate_is_complete(candidate, class_type):
    payload = candidate.payload
    return (
        isinstance(payload.get("archive"), dict)
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


class DependencyResolver:
    """Resolve nodes without installing, uploading, renting, or executing."""

    def __init__(self, *, host, repository, registry):
        self.host = host
        self.repository = repository
        self.registry = registry

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

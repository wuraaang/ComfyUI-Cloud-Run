"""Resolve active workflow model annotations to immutable sources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re

from .artifacts import StaticFileRequirement
from .huggingface import (
    HuggingFaceError,
    ResolvedHuggingFaceFile,
    parse_huggingface_url,
)
from .manifest import (
    ManifestValidationError,
    SourceSpec,
    validate_dependency,
)
from .model_metadata import EmbeddedModelIndex, ModelMetadataError


_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAPPING_REASON = "Native model metadata is missing or ambiguous."
_INVALID_REASON = "Native model metadata is invalid."
_UNVERIFIED_REASON = "The public Hugging Face file could not be verified."


@dataclass(frozen=True)
class ModelSourceResolution:
    status: str
    source: SourceSpec | None
    size_bytes: int | None
    sha256: str | None
    reason: str | None


def _failure(status, reason):
    return ModelSourceResolution(
        status=status,
        source=None,
        size_bytes=None,
        sha256=None,
        reason=reason,
    )


def _validated_resolution(candidate, resolved):
    if not isinstance(resolved, ResolvedHuggingFaceFile):
        return _failure("unsupported", _UNVERIFIED_REASON)
    if (
        resolved.repository_id != candidate.reference.repository_id
        or resolved.file_path != candidate.reference.file_path
        or not isinstance(resolved.immutable_revision, str)
        or not _HEX_40.fullmatch(resolved.immutable_revision)
        or not isinstance(resolved.size_bytes, int)
        or isinstance(resolved.size_bytes, bool)
        or resolved.size_bytes <= 0
        or not isinstance(resolved.sha256, str)
        or not _HEX_64.fullmatch(resolved.sha256)
        or (
            candidate.expected_sha256 is not None
            and resolved.sha256 != candidate.expected_sha256
        )
    ):
        return _failure("unsupported", _UNVERIFIED_REASON)
    try:
        locator_reference = parse_huggingface_url(resolved.locator)
    except HuggingFaceError:
        return _failure("unsupported", _UNVERIFIED_REASON)
    if (
        locator_reference.repository_id != resolved.repository_id
        or locator_reference.file_path != resolved.file_path
        or locator_reference.revision != resolved.immutable_revision
    ):
        return _failure("unsupported", _UNVERIFIED_REASON)

    source = SourceSpec(
        kind="huggingface",
        locator=resolved.locator,
        immutable_revision=resolved.immutable_revision,
    )
    try:
        validate_dependency(source)
    except ManifestValidationError:
        return _failure("unsupported", _UNVERIFIED_REASON)
    return ModelSourceResolution(
        status="resolved",
        source=source,
        size_bytes=resolved.size_bytes,
        sha256=resolved.sha256,
        reason=None,
    )


class WorkflowModelSourceResolver:
    def __init__(self, client):
        self.client = client

    async def _resolve_group(self, candidate):
        try:
            resolved = await self.client.resolve(
                candidate.reference,
                expected_sha256=candidate.expected_sha256,
            )
        except HuggingFaceError:
            return _failure("unsupported", _UNVERIFIED_REASON)
        return _validated_resolution(candidate, resolved)

    async def resolve(
        self,
        capture,
        *,
        requirements: tuple[StaticFileRequirement, ...],
    ) -> dict[tuple[str, str], ModelSourceResolution]:
        if not isinstance(requirements, tuple) or not all(
            isinstance(requirement, StaticFileRequirement)
            for requirement in requirements
        ):
            raise ValueError("Static model requirements are invalid.")
        static_models = tuple(
            requirement
            for requirement in requirements
            if requirement.metadata.kind == "model"
            and isinstance(requirement.value, str)
        )
        if not static_models:
            return {}

        try:
            index = EmbeddedModelIndex.from_workflow(
                getattr(capture, "workflow", None)
            )
        except ModelMetadataError:
            invalid = _failure("unsupported", _INVALID_REASON)
            return {
                (requirement.node_id, requirement.input_name): invalid
                for requirement in static_models
            }

        pending = []
        groups = {}
        for requirement in static_models:
            lookup = index.lookup(
                node_id=requirement.node_id,
                name=requirement.value,
                directory=str(requirement.metadata.category),
            )
            if lookup.status == "mapping_required":
                pending.append(
                    (
                        requirement,
                        _failure("mapping_required", _MAPPING_REASON),
                    )
                )
                continue
            if lookup.status != "resolved" or lookup.candidate is None:
                pending.append(
                    (requirement, _failure("unsupported", _INVALID_REASON))
                )
                continue
            group_key = (
                lookup.candidate.reference,
                lookup.candidate.expected_sha256,
            )
            groups.setdefault(group_key, lookup.candidate)
            pending.append((requirement, group_key))

        group_keys = tuple(groups)
        group_results = await asyncio.gather(
            *(self._resolve_group(groups[key]) for key in group_keys)
        )
        resolved_groups = dict(zip(group_keys, group_results))

        result = {}
        for requirement, resolution in pending:
            if not isinstance(resolution, ModelSourceResolution):
                resolution = resolved_groups[resolution]
            result[(requirement.node_id, requirement.input_name)] = resolution
        return result

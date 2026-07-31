"""Read-only indexing of native ComfyUI workflow model metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from .huggingface import (
    HuggingFaceError,
    HuggingFaceReference,
    parse_huggingface_url,
)


MAX_MODEL_METADATA_DEPTH = 32
MAX_MODEL_METADATA_NODES = 10_000
MAX_MODEL_METADATA_RECORDS = 10_000

_DIRECTORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_NODE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_MODEL_NAME_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_ACTIVE_NODE_MODES = {0, 1, 3}
_INACTIVE_NODE_MODES = {2, 4}
_MAPPING_REASON = "Native model metadata is missing or ambiguous."
_INVALID_REASON = "Native model metadata is invalid."


class ModelMetadataError(RuntimeError):
    """Native workflow model metadata is invalid or exceeds bounds."""


@dataclass(frozen=True)
class EmbeddedModelCandidate:
    node_id: str | None
    name: str
    directory: str
    reference: HuggingFaceReference
    expected_sha256: str | None


@dataclass(frozen=True)
class EmbeddedModelLookup:
    status: str
    candidate: EmbeddedModelCandidate | None
    reason: str | None


class _CandidateBucket:
    def __init__(self):
        self.candidates = []
        self.invalid = False

    def add_candidate(self, candidate):
        self.candidates.append(candidate)

    def add_invalid(self):
        self.invalid = True

    def lookup(self):
        if self.invalid:
            return EmbeddedModelLookup(
                status="unsupported",
                candidate=None,
                reason=_INVALID_REASON,
            )
        if not self.candidates:
            return EmbeddedModelLookup(
                status="mapping_required",
                candidate=None,
                reason=_MAPPING_REASON,
            )

        references = {candidate.reference for candidate in self.candidates}
        supplied_hashes = {
            candidate.expected_sha256
            for candidate in self.candidates
            if candidate.expected_sha256 is not None
        }
        if len(references) != 1 or len(supplied_hashes) > 1:
            return EmbeddedModelLookup(
                status="mapping_required",
                candidate=None,
                reason=_MAPPING_REASON,
            )

        first = self.candidates[0]
        expected_sha256 = (
            next(iter(supplied_hashes)) if supplied_hashes else None
        )
        return EmbeddedModelLookup(
            status="resolved",
            candidate=EmbeddedModelCandidate(
                node_id=first.node_id,
                name=first.name,
                directory=first.directory,
                reference=first.reference,
                expected_sha256=expected_sha256,
            ),
            reason=None,
        )


def _node_component(value):
    if isinstance(value, bool):
        raise ModelMetadataError("Workflow model node identity is invalid.")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _NODE_PART.fullmatch(value):
        raise ModelMetadataError("Workflow model node identity is invalid.")
    return value


def _definition_id(value):
    if not isinstance(value, str) or not _NODE_PART.fullmatch(value):
        raise ModelMetadataError("Workflow subgraph identity is invalid.")
    return value


def _record_key(record):
    if not isinstance(record, dict):
        raise ModelMetadataError("Workflow model records must be objects.")
    name = record.get("name")
    directory = record.get("directory")
    if not isinstance(name, str) or not isinstance(directory, str):
        raise ModelMetadataError("Workflow model record identity is invalid.")
    return name, directory


def _safe_model_name(value):
    if (
        not value
        or not value.isascii()
        or len(value) > 2048
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 33 for character in value)
    ):
        return False
    relative = PurePosixPath(value)
    return (
        str(relative) == value
        and bool(relative.parts)
        and all(_MODEL_NAME_PART.fullmatch(part) for part in relative.parts)
    )


def _parse_record(record, *, node_id):
    name, directory = _record_key(record)
    fields = set(record)
    base_fields = {"name", "url", "directory"}
    hashed_fields = base_fields | {"hash", "hash_type"}
    if fields not in (base_fields, hashed_fields):
        return (name, directory), None
    if not _safe_model_name(name) or not _DIRECTORY.fullmatch(directory):
        return (name, directory), None
    url = record.get("url")
    if not isinstance(url, str):
        return (name, directory), None

    expected_sha256 = None
    if fields == hashed_fields:
        digest = record.get("hash")
        if (
            record.get("hash_type") != "sha256"
            or not isinstance(digest, str)
            or not _HEX_64.fullmatch(digest)
        ):
            return (name, directory), None
        expected_sha256 = digest
    try:
        reference = parse_huggingface_url(url)
    except HuggingFaceError:
        return (name, directory), None
    return (
        (name, directory),
        EmbeddedModelCandidate(
            node_id=node_id,
            name=name,
            directory=directory,
            reference=reference,
            expected_sha256=expected_sha256,
        ),
    )


class _IndexBuilder:
    def __init__(self, workflow):
        self.workflow = workflow
        self.node_entries = {}
        self.workflow_entries = {}
        self.seen_flattened_ids = set()
        self.node_count = 0
        self.record_count = 0
        self.definitions = self._definitions()

    def _definitions(self):
        definitions = self.workflow.get("definitions")
        if definitions is None:
            return {}
        if not isinstance(definitions, dict):
            raise ModelMetadataError("Workflow definitions are invalid.")
        subgraphs = definitions.get("subgraphs", [])
        if not isinstance(subgraphs, list):
            raise ModelMetadataError("Workflow subgraphs are invalid.")
        result = {}
        for subgraph in subgraphs:
            if not isinstance(subgraph, dict):
                raise ModelMetadataError("Workflow subgraphs are invalid.")
            identifier = _definition_id(subgraph.get("id"))
            nodes = subgraph.get("nodes")
            if not isinstance(nodes, list) or identifier in result:
                raise ModelMetadataError("Workflow subgraphs are invalid.")
            result[identifier] = nodes
        return result

    def _count_record(self):
        self.record_count += 1
        if self.record_count > MAX_MODEL_METADATA_RECORDS:
            raise ModelMetadataError("Workflow model metadata is too large.")

    @staticmethod
    def _bucket(entries, key):
        bucket = entries.get(key)
        if bucket is None:
            bucket = _CandidateBucket()
            entries[key] = bucket
        return bucket

    def _add_records(self, records, *, node_id, selected_names=None):
        if not isinstance(records, list):
            raise ModelMetadataError("Workflow model records must be a list.")
        for record in records:
            self._count_record()
            name, directory = _record_key(record)
            if selected_names is not None and name not in selected_names:
                continue
            key, candidate = _parse_record(record, node_id=node_id)
            entries = self.workflow_entries if node_id is None else self.node_entries
            full_key = key if node_id is None else (node_id, *key)
            bucket = self._bucket(entries, full_key)
            if candidate is None:
                bucket.add_invalid()
            else:
                bucket.add_candidate(candidate)

    def _node_is_active(self, node):
        mode = node.get("mode", 0)
        if isinstance(mode, bool) or not isinstance(mode, int):
            raise ModelMetadataError("Workflow node mode is invalid.")
        if mode in _ACTIVE_NODE_MODES:
            return True
        if mode in _INACTIVE_NODE_MODES:
            return False
        raise ModelMetadataError("Workflow node mode is invalid.")

    def _walk_nodes(self, nodes, *, prefix, definition_stack, depth):
        if depth > MAX_MODEL_METADATA_DEPTH:
            raise ModelMetadataError("Workflow subgraph nesting is too deep.")
        if not isinstance(nodes, list):
            raise ModelMetadataError("Workflow nodes must be a list.")
        for node in nodes:
            self.node_count += 1
            if self.node_count > MAX_MODEL_METADATA_NODES:
                raise ModelMetadataError("Workflow model node count is too large.")
            if not isinstance(node, dict):
                raise ModelMetadataError("Workflow nodes must be objects.")
            component = _node_component(node.get("id"))
            flattened_id = component if not prefix else prefix + ":" + component
            if flattened_id in self.seen_flattened_ids:
                raise ModelMetadataError("Workflow model node identity is ambiguous.")
            self.seen_flattened_ids.add(flattened_id)
            if not self._node_is_active(node):
                continue

            properties = node.get("properties")
            if properties is not None:
                if not isinstance(properties, dict):
                    raise ModelMetadataError("Workflow node properties are invalid.")
                if "models" in properties:
                    widget_values = node.get("widgets_values")
                    if not isinstance(widget_values, list):
                        raise ModelMetadataError(
                            "Workflow model widget values are invalid."
                        )
                    selected_names = {
                        value for value in widget_values if isinstance(value, str)
                    }
                    self._add_records(
                        properties["models"],
                        node_id=flattened_id,
                        selected_names=selected_names,
                    )

            node_type = node.get("type")
            if node_type in self.definitions:
                if node_type in definition_stack:
                    raise ModelMetadataError("Workflow subgraph cycle is invalid.")
                self._walk_nodes(
                    self.definitions[node_type],
                    prefix=flattened_id,
                    definition_stack=(*definition_stack, node_type),
                    depth=depth + 1,
                )

    def build(self):
        workflow_models = self.workflow.get("models")
        if workflow_models is not None:
            self._add_records(workflow_models, node_id=None)
        self._walk_nodes(
            self.workflow["nodes"],
            prefix="",
            definition_stack=(),
            depth=0,
        )
        return EmbeddedModelIndex(
            node_entries=self.node_entries,
            workflow_entries=self.workflow_entries,
        )


class EmbeddedModelIndex:
    def __init__(self, *, node_entries, workflow_entries):
        self._node_entries = dict(node_entries)
        self._workflow_entries = dict(workflow_entries)

    @classmethod
    def from_workflow(cls, workflow: dict) -> "EmbeddedModelIndex":
        if not isinstance(workflow, dict) or not isinstance(
            workflow.get("nodes"),
            list,
        ):
            raise ModelMetadataError("Workflow model metadata is invalid.")
        return _IndexBuilder(workflow).build()

    def lookup(
        self,
        *,
        node_id: str,
        name: str,
        directory: str,
    ) -> EmbeddedModelLookup:
        if (
            not isinstance(node_id, str)
            or not node_id
            or not isinstance(name, str)
            or not isinstance(directory, str)
        ):
            raise ModelMetadataError("Workflow model lookup is invalid.")
        node_bucket = self._node_entries.get((node_id, name, directory))
        if node_bucket is not None:
            return node_bucket.lookup()
        workflow_bucket = self._workflow_entries.get((name, directory))
        if workflow_bucket is not None:
            return workflow_bucket.lookup()
        return EmbeddedModelLookup(
            status="mapping_required",
            candidate=None,
            reason=_MAPPING_REASON,
        )

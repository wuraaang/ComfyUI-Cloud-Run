"""Immutable, content-addressed dependency contracts for Cloud Run."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit


PINNED_COMFYUI_CORE_VERSION = "0.29.0"
PINNED_COMFYUI_FRONTEND_VERSION = "1.47.10"
MANIFEST_SCHEMA_VERSION = 1
PROTOCOL_VERSION = "1"

_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_GITHUB_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_R2_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_R2_KEY = re.compile(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}")
_ARTIFACT_KINDS = {
    "custom_node_archive",
    "input",
    "model",
    "python_wheel",
    "worker",
}


class ManifestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    locator: str
    immutable_revision: str | None = None
    secret_handle: str | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    artifact_id: str
    kind: str
    logical_name: str
    destination: str
    size_bytes: int
    sha256: str
    source: SourceSpec


@dataclass(frozen=True)
class PythonWheelSpec:
    filename: str
    size_bytes: int
    sha256: str
    source: SourceSpec


@dataclass(frozen=True)
class CustomNodeSpec:
    package_id: str
    repository_url: str
    revision: str
    archive: ArtifactSpec
    wheels: tuple[PythonWheelSpec, ...]
    provided_class_types: tuple[str, ...]


@dataclass(frozen=True)
class DependencyManifest:
    schema_version: int
    protocol_version: str
    comfyui_core_version: str
    comfyui_frontend_version: str
    worker_version: str
    prompt_digest: str
    custom_nodes: tuple[CustomNodeSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]
    output_allowance_bytes: int
    disk_gb: int

    def canonical_bytes(self):
        validate_dependency(self)
        return json.dumps(
            _manifest_record(self),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def digest(self):
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def delta_from(self, installed):
        return ManifestDelta.between(installed, self)


@dataclass(frozen=True)
class ManifestDelta:
    compatible: bool
    reason: str | None
    custom_nodes: tuple[CustomNodeSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]
    wheels: tuple[PythonWheelSpec, ...]

    @classmethod
    def between(cls, installed, desired):
        validate_dependency(installed)
        validate_dependency(desired)
        version_fields = (
            "schema_version",
            "protocol_version",
            "comfyui_core_version",
            "comfyui_frontend_version",
            "worker_version",
        )
        if any(
            getattr(installed, field) != getattr(desired, field)
            for field in version_fields
        ):
            return cls(False, "runtime_identity_changed", (), (), ())

        installed_nodes = {
            item.package_id: item for item in installed.custom_nodes
        }
        installed_destinations = {
            item.destination: item.sha256
            for item in (
                *installed.artifacts,
                *(node.archive for node in installed.custom_nodes),
            )
        }
        installed_wheels = {
            wheel.filename: wheel.sha256
            for node in installed.custom_nodes
            for wheel in node.wheels
        }

        new_nodes = []
        new_wheels = []
        for node in sorted(desired.custom_nodes, key=lambda item: item.package_id):
            prior = installed_nodes.get(node.package_id)
            if prior is None:
                new_nodes.append(node)
                new_wheels.extend(node.wheels)
                continue
            if (
                prior.revision != node.revision
                or prior.repository_url != node.repository_url
                or prior.archive.sha256 != node.archive.sha256
                or prior.archive.destination != node.archive.destination
                or sorted(prior.provided_class_types)
                != sorted(node.provided_class_types)
            ):
                return cls(False, "installed_custom_node_changed", (), (), ())
            for wheel in node.wheels:
                prior_digest = installed_wheels.get(wheel.filename)
                if prior_digest is not None and prior_digest != wheel.sha256:
                    return cls(False, "installed_wheel_changed", (), (), ())
                if prior_digest is None:
                    new_wheels.append(wheel)

        new_artifacts = []
        for artifact in sorted(
            desired.artifacts,
            key=lambda item: (item.destination, item.artifact_id),
        ):
            prior_digest = installed_destinations.get(artifact.destination)
            if prior_digest is not None and prior_digest != artifact.sha256:
                return cls(False, "installed_destination_changed", (), (), ())
            if prior_digest is None:
                new_artifacts.append(artifact)

        return cls(
            True,
            None,
            tuple(new_nodes),
            tuple(new_artifacts),
            tuple(
                sorted(
                    new_wheels,
                    key=lambda item: (item.filename, item.sha256),
                )
            ),
        )


def _validated_identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ManifestValidationError(f"Invalid {name}.")
    return value


def normalize_github_repository(locator):
    if not isinstance(locator, str) or "%" in locator:
        raise ManifestValidationError("Invalid GitHub repository URL.")
    parsed = urlsplit(locator)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or not all(_GITHUB_PART.fullmatch(part) for part in parts)
        or parts[1].endswith(".git")
    ):
        raise ManifestValidationError("Invalid GitHub repository URL.")
    normalized = f"https://github.com/{parts[0]}/{parts[1]}"
    if locator != normalized:
        raise ManifestValidationError("GitHub repository URL is not canonical.")
    return normalized


def _validate_source(source):
    if not isinstance(source, SourceSpec):
        raise ManifestValidationError("Invalid dependency source.")
    kind = str(source.kind)
    locator = source.locator
    revision = source.immutable_revision
    if source.secret_handle is not None:
        _validated_identifier(source.secret_handle, "secret handle")

    if kind == "git":
        normalize_github_repository(locator)
        if not isinstance(revision, str) or not _HEX_40.fullmatch(revision):
            raise ManifestValidationError(
                "Git sources require an immutable commit."
            )
        return

    if kind == "huggingface":
        if not isinstance(locator, str) or "%" in locator:
            raise ManifestValidationError("Invalid Hugging Face source.")
        parsed = urlsplit(locator)
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.netloc != "huggingface.co"
            or parsed.query
            or parsed.fragment
            or len(parts) < 5
            or parts[2] != "resolve"
            or not _HEX_40.fullmatch(parts[3])
            or revision != parts[3]
            or any(part in {".", ".."} for part in parts[4:])
        ):
            raise ManifestValidationError("Hugging Face source is not immutable.")
        return

    if kind == "civitai":
        if not isinstance(locator, str):
            raise ManifestValidationError("Invalid Civitai source.")
        parsed = urlsplit(locator)
        prefix = "/api/download/models/"
        version = parsed.path.removeprefix(prefix)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "civitai.com"
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(prefix)
            or not version.isdigit()
            or not version
            or revision != version
        ):
            raise ManifestValidationError("Civitai model version is not immutable.")
        return

    if kind == "r2":
        if not isinstance(locator, str):
            raise ManifestValidationError("Invalid R2 source.")
        parsed = urlsplit(locator)
        key = parsed.path.removeprefix("/")
        if (
            parsed.scheme != "r2"
            or not _R2_BUCKET.fullmatch(parsed.netloc)
            or parsed.query
            or parsed.fragment
            or not _R2_KEY.fullmatch(key)
            or revision is not None
        ):
            raise ManifestValidationError("R2 source is not content addressed.")
        return

    if kind == "local-upload":
        if (
            not isinstance(locator, str)
            or not locator.startswith("local-upload:")
            or not _IDENTIFIER.fullmatch(locator.removeprefix("local-upload:"))
            or revision is not None
            or source.secret_handle is not None
        ):
            raise ManifestValidationError("Invalid local upload source.")
        return

    raise ManifestValidationError("Unsupported dependency source kind.")


def _validate_destination(destination):
    if (
        not isinstance(destination, str)
        or not destination
        or destination.startswith("/")
        or "\\" in destination
        or any(ord(character) < 32 for character in destination)
    ):
        raise ManifestValidationError("Invalid ComfyUI destination.")
    path = PurePosixPath(destination)
    parts = path.parts
    if (
        not parts
        or parts[0] not in {"custom_nodes", "input", "models"}
        or any(part in {"", ".", ".."} for part in parts)
        or str(path) != destination
    ):
        raise ManifestValidationError("Invalid ComfyUI destination.")
    return path


def _validate_artifact(artifact):
    if not isinstance(artifact, ArtifactSpec):
        raise ManifestValidationError("Invalid artifact.")
    _validated_identifier(artifact.artifact_id, "artifact ID")
    if artifact.kind not in _ARTIFACT_KINDS:
        raise ManifestValidationError("Invalid artifact kind.")
    _validated_identifier(artifact.logical_name, "artifact logical name")
    destination = _validate_destination(artifact.destination)
    required_root = {
        "custom_node_archive": "custom_nodes",
        "input": "input",
        "model": "models",
        "python_wheel": "custom_nodes",
        "worker": "custom_nodes",
    }[artifact.kind]
    if destination.parts[0] != required_root:
        raise ManifestValidationError("Artifact destination kind mismatch.")
    if (
        isinstance(artifact.size_bytes, bool)
        or not isinstance(artifact.size_bytes, int)
        or artifact.size_bytes <= 0
    ):
        raise ManifestValidationError("Artifact size must be exact and positive.")
    if not isinstance(artifact.sha256, str) or not _HEX_64.fullmatch(
        artifact.sha256
    ):
        raise ManifestValidationError("Invalid artifact SHA-256.")
    _validate_source(artifact.source)
    if artifact.source.kind == "r2":
        key_digest = artifact.source.locator.rsplit("/", 1)[-1]
        if key_digest != artifact.sha256:
            raise ManifestValidationError("R2 object digest does not match artifact.")


def _validate_wheel(wheel):
    if not isinstance(wheel, PythonWheelSpec):
        raise ManifestValidationError("Invalid Python wheel.")
    if (
        not isinstance(wheel.filename, str)
        or PurePosixPath(wheel.filename).name != wheel.filename
        or not wheel.filename.endswith(".whl")
        or len(wheel.filename) > 255
    ):
        raise ManifestValidationError("Invalid Python wheel filename.")
    if (
        isinstance(wheel.size_bytes, bool)
        or not isinstance(wheel.size_bytes, int)
        or wheel.size_bytes <= 0
    ):
        raise ManifestValidationError("Python wheel size must be positive.")
    if not isinstance(wheel.sha256, str) or not _HEX_64.fullmatch(wheel.sha256):
        raise ManifestValidationError("Invalid Python wheel SHA-256.")
    _validate_source(wheel.source)
    if wheel.source.kind == "r2":
        if wheel.source.locator.rsplit("/", 1)[-1] != wheel.sha256:
            raise ManifestValidationError("R2 object digest does not match wheel.")


def _validate_custom_node(node):
    if not isinstance(node, CustomNodeSpec):
        raise ManifestValidationError("Invalid custom-node package.")
    _validated_identifier(node.package_id, "package ID")
    normalize_github_repository(node.repository_url)
    if not isinstance(node.revision, str) or not _HEX_40.fullmatch(node.revision):
        raise ManifestValidationError("Custom node requires an immutable commit.")
    _validate_artifact(node.archive)
    if node.archive.kind != "custom_node_archive":
        raise ManifestValidationError("Custom-node archive kind is invalid.")
    if not isinstance(node.wheels, tuple):
        raise ManifestValidationError("Custom-node wheels must be immutable.")
    wheel_names = set()
    for wheel in node.wheels:
        _validate_wheel(wheel)
        if wheel.filename in wheel_names:
            raise ManifestValidationError("Python wheel filenames must be unique.")
        wheel_names.add(wheel.filename)
    if not isinstance(node.provided_class_types, tuple) or not node.provided_class_types:
        raise ManifestValidationError("Custom node must declare class types.")
    class_types = set()
    for class_type in node.provided_class_types:
        _validated_identifier(class_type, "provided class type")
        if class_type in class_types:
            raise ManifestValidationError("Provided class types must be unique.")
        class_types.add(class_type)


def _validate_manifest(manifest):
    if not isinstance(manifest, DependencyManifest):
        raise ManifestValidationError("Invalid dependency manifest.")
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise ManifestValidationError("Unsupported manifest schema.")
    if manifest.protocol_version != PROTOCOL_VERSION:
        raise ManifestValidationError("Unsupported worker protocol.")
    if manifest.comfyui_core_version != PINNED_COMFYUI_CORE_VERSION:
        raise ManifestValidationError("Unsupported ComfyUI core version.")
    if manifest.comfyui_frontend_version != PINNED_COMFYUI_FRONTEND_VERSION:
        raise ManifestValidationError("Unsupported ComfyUI frontend version.")
    _validated_identifier(manifest.worker_version, "worker version")
    if not isinstance(manifest.prompt_digest, str) or not _HEX_64.fullmatch(
        manifest.prompt_digest
    ):
        raise ManifestValidationError("Invalid prompt digest.")
    if not isinstance(manifest.custom_nodes, tuple):
        raise ManifestValidationError("Custom nodes must be immutable.")
    if not isinstance(manifest.artifacts, tuple):
        raise ManifestValidationError("Artifacts must be immutable.")
    if (
        isinstance(manifest.output_allowance_bytes, bool)
        or not isinstance(manifest.output_allowance_bytes, int)
        or manifest.output_allowance_bytes <= 0
    ):
        raise ManifestValidationError("Output allowance must be positive.")
    if (
        isinstance(manifest.disk_gb, bool)
        or not isinstance(manifest.disk_gb, int)
        or not 80 <= manifest.disk_gb <= 2048
    ):
        raise ManifestValidationError("Disk allocation is outside safe bounds.")

    package_ids = set()
    provided_class_types = set()
    artifact_ids = set()
    destinations = set()
    for node in manifest.custom_nodes:
        _validate_custom_node(node)
        if node.package_id in package_ids:
            raise ManifestValidationError("Custom-node package IDs must be unique.")
        package_ids.add(node.package_id)
        if provided_class_types.intersection(node.provided_class_types):
            raise ManifestValidationError("Class types have multiple providers.")
        provided_class_types.update(node.provided_class_types)
        if node.archive.artifact_id in artifact_ids:
            raise ManifestValidationError("Artifact IDs must be unique.")
        artifact_ids.add(node.archive.artifact_id)
        if node.archive.destination in destinations:
            raise ManifestValidationError("Artifact destinations must be unique.")
        destinations.add(node.archive.destination)

    for artifact in manifest.artifacts:
        _validate_artifact(artifact)
        if artifact.artifact_id in artifact_ids:
            raise ManifestValidationError("Artifact IDs must be unique.")
        artifact_ids.add(artifact.artifact_id)
        if artifact.destination in destinations:
            raise ManifestValidationError("Artifact destinations must be unique.")
        destinations.add(artifact.destination)


def validate_dependency(value):
    if isinstance(value, SourceSpec):
        _validate_source(value)
    elif isinstance(value, ArtifactSpec):
        _validate_artifact(value)
    elif isinstance(value, PythonWheelSpec):
        _validate_wheel(value)
    elif isinstance(value, CustomNodeSpec):
        _validate_custom_node(value)
    elif isinstance(value, DependencyManifest):
        _validate_manifest(value)
    else:
        raise ManifestValidationError("Unsupported dependency value.")
    return value


def _source_record(source):
    return {
        "kind": source.kind,
        "locator": source.locator,
        "immutable_revision": source.immutable_revision,
    }


def _artifact_record(artifact):
    return {
        "artifact_id": artifact.artifact_id,
        "kind": artifact.kind,
        "logical_name": artifact.logical_name,
        "destination": artifact.destination,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "source": _source_record(artifact.source),
    }


def _wheel_record(wheel):
    return {
        "filename": wheel.filename,
        "size_bytes": wheel.size_bytes,
        "sha256": wheel.sha256,
        "source": _source_record(wheel.source),
    }


def _custom_node_record(node):
    return {
        "package_id": node.package_id,
        "repository_url": node.repository_url,
        "revision": node.revision,
        "archive": _artifact_record(node.archive),
        "wheels": [
            _wheel_record(wheel)
            for wheel in sorted(
                node.wheels,
                key=lambda item: (item.filename, item.sha256),
            )
        ],
        "provided_class_types": sorted(node.provided_class_types),
    }


def _manifest_record(manifest):
    return {
        "schema_version": manifest.schema_version,
        "protocol_version": manifest.protocol_version,
        "comfyui_core_version": manifest.comfyui_core_version,
        "comfyui_frontend_version": manifest.comfyui_frontend_version,
        "worker_version": manifest.worker_version,
        "prompt_digest": manifest.prompt_digest,
        "custom_nodes": [
            _custom_node_record(node)
            for node in sorted(
                manifest.custom_nodes,
                key=lambda item: item.package_id,
            )
        ],
        "artifacts": [
            _artifact_record(artifact)
            for artifact in sorted(
                manifest.artifacts,
                key=lambda item: (
                    item.destination,
                    item.artifact_id,
                    item.sha256,
                ),
            )
        ],
        "output_allowance_bytes": manifest.output_allowance_bytes,
        "disk_gb": manifest.disk_gb,
    }

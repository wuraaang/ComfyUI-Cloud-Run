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
MANIFEST_SCHEMA_VERSION = 2
PROTOCOL_VERSION = "2"

_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_UI_REVISION = re.compile(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_GITHUB_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_R2_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_R2_KEY = re.compile(r"sha256/[0-9a-f]{2}/[0-9a-f]{64}")
_ARTIFACT_KINDS = {
    "custom_node_archive",
    "input",
    "model",
    "profile_archive",
    "python_wheel",
    "ui_package_archive",
    "worker",
}
_PROFILE_ROOTS = frozenset(
    {"assets", "backgrounds", "bootstrap", "palettes", "settings", "workflows"}
)
_PROFILE_FORBIDDEN_NAMES = frozenset(
    {
        ".env",
        "cache",
        "caches",
        "comfyui.db",
        "credentials",
        "cookies",
        "logs",
        "secrets",
        "tokens",
        "__pycache__",
    }
)
_PROFILE_FORBIDDEN_SUFFIXES = frozenset(
    {".db", ".key", ".log", ".pem", ".pyc", ".pyo"}
)
_UI_CAPABILITIES = frozenset(
    {"graph_read", "graph_edit", "native_run", "native_batch"}
)


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
class ProfileFileSpec:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    revision: int
    archive: ArtifactSpec
    bootstrap_digest: str
    files: tuple[ProfileFileSpec, ...]


@dataclass(frozen=True)
class UiPackageSpec:
    package_id: str
    repository_url: str
    revision: str
    archive: ArtifactSpec
    web_sha256: str
    required_capabilities: tuple[str, ...]


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
    ui_packages: tuple[UiPackageSpec, ...] = ()
    profile: ProfileSpec | None = None
    minimum_vram_gb: float = 0.0

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
    ui_packages: tuple[UiPackageSpec, ...] = ()
    profile_changed: bool = False
    runtime_change: bool = False

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
            return cls(
                False,
                "runtime_identity_changed",
                (),
                (),
                (),
                runtime_change=True,
            )

        installed_nodes = {
            item.package_id: item for item in installed.custom_nodes
        }
        installed_destinations = {
            item.destination: item.sha256
            for item in (
                *installed.artifacts,
                *(node.archive for node in installed.custom_nodes),
                *(package.archive for package in installed.ui_packages),
            )
        }
        if installed.profile is not None:
            installed_destinations[
                installed.profile.archive.destination
            ] = installed.profile.archive.sha256
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
                return cls(
                    False,
                    "installed_custom_node_changed",
                    (),
                    (),
                    (),
                    runtime_change=True,
                )
            for wheel in node.wheels:
                prior_digest = installed_wheels.get(wheel.filename)
                if prior_digest is not None and prior_digest != wheel.sha256:
                    return cls(
                        False,
                        "installed_wheel_changed",
                        (),
                        (),
                        (),
                        runtime_change=True,
                    )
                if prior_digest is None:
                    new_wheels.append(wheel)

        new_artifacts = []
        for artifact in sorted(
            desired.artifacts,
            key=lambda item: (item.destination, item.artifact_id),
        ):
            prior_digest = installed_destinations.get(artifact.destination)
            if prior_digest is not None and prior_digest != artifact.sha256:
                return cls(
                    False,
                    "installed_destination_changed",
                    (),
                    (),
                    (),
                )
            if prior_digest is None:
                new_artifacts.append(artifact)

        installed_ui = {
            item.package_id: item for item in installed.ui_packages
        }
        new_ui = []
        for package in sorted(
            desired.ui_packages,
            key=lambda item: item.package_id,
        ):
            prior = installed_ui.get(package.package_id)
            if prior is None:
                prior_digest = installed_destinations.get(
                    package.archive.destination
                )
                if (
                    prior_digest is not None
                    and prior_digest != package.archive.sha256
                ):
                    return cls(
                        False,
                        "installed_destination_changed",
                        (),
                        (),
                        (),
                    )
                new_ui.append(package)
            elif prior != package:
                return cls(
                    False,
                    "installed_ui_package_changed",
                    (),
                    (),
                    (),
                )

        profile_changed = False
        if desired.profile is not None:
            prior_profile = installed.profile
            if prior_profile is None:
                profile_changed = True
            elif desired.profile == prior_profile:
                profile_changed = False
            elif (
                desired.profile.profile_id == prior_profile.profile_id
                and desired.profile.revision > prior_profile.revision
            ):
                profile_changed = True
            else:
                return cls(
                    False,
                    "installed_profile_changed",
                    (),
                    (),
                    (),
                )

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
            tuple(new_ui),
            profile_changed,
            False,
        )


def _validated_identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ManifestValidationError(f"Invalid {name}.")
    return value


def _validated_class_type(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or value != value.strip()
        or any(
            not 32 <= ord(character) <= 126
            for character in value
        )
    ):
        raise ManifestValidationError("Invalid provided class type.")
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
        or parts[0] not in {"custom_nodes", "input", "models", "user"}
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
        "profile_archive": "user",
        "python_wheel": "custom_nodes",
        "ui_package_archive": "custom_nodes",
        "worker": "custom_nodes",
    }[artifact.kind]
    if destination.parts[0] != required_root:
        raise ManifestValidationError("Artifact destination kind mismatch.")
    if artifact.kind == "profile_archive" and artifact.destination != (
        "user/default/cloud-vast-profile"
    ):
        raise ManifestValidationError("Profile archive destination is invalid.")
    if artifact.kind != "profile_archive" and destination.parts[0] == "user":
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
        _validated_class_type(class_type)
        if class_type in class_types:
            raise ManifestValidationError("Provided class types must be unique.")
        class_types.add(class_type)


def _validate_ui_package(package):
    if not isinstance(package, UiPackageSpec):
        raise ManifestValidationError("Invalid UI-only package.")
    _validated_identifier(package.package_id, "UI package ID")
    normalize_github_repository(package.repository_url)
    if not isinstance(package.revision, str) or not _UI_REVISION.fullmatch(
        package.revision
    ):
        raise ManifestValidationError("UI package requires an immutable revision.")
    _validate_artifact(package.archive)
    if package.archive.kind != "ui_package_archive":
        raise ManifestValidationError("UI package archive kind is invalid.")
    if not isinstance(package.web_sha256, str) or not _HEX_64.fullmatch(
        package.web_sha256
    ):
        raise ManifestValidationError("Invalid UI package web digest.")
    capabilities = package.required_capabilities
    if (
        not isinstance(capabilities, tuple)
        or len(capabilities) != len(set(capabilities))
        or any(item not in _UI_CAPABILITIES for item in capabilities)
    ):
        raise ManifestValidationError("Invalid UI package capabilities.")


def _validate_profile_file(item):
    if not isinstance(item, ProfileFileSpec):
        raise ManifestValidationError("Invalid profile file.")
    path = item.path
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise ManifestValidationError("Invalid profile file path.")
    relative = PurePosixPath(path)
    folded = tuple(part.casefold() for part in relative.parts)
    if (
        str(relative) != path
        or not relative.parts
        or relative.parts[0] not in _PROFILE_ROOTS
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part in _PROFILE_FORBIDDEN_NAMES for part in folded)
        or PurePosixPath(path).suffix.casefold() in _PROFILE_FORBIDDEN_SUFFIXES
    ):
        raise ManifestValidationError("Invalid profile file path.")
    if (
        isinstance(item.size_bytes, bool)
        or not isinstance(item.size_bytes, int)
        or item.size_bytes < 0
    ):
        raise ManifestValidationError("Invalid profile file size.")
    if not isinstance(item.sha256, str) or not _HEX_64.fullmatch(item.sha256):
        raise ManifestValidationError("Invalid profile file digest.")


def _validate_profile(profile):
    if not isinstance(profile, ProfileSpec):
        raise ManifestValidationError("Invalid safe profile.")
    _validated_identifier(profile.profile_id, "profile ID")
    if (
        isinstance(profile.revision, bool)
        or not isinstance(profile.revision, int)
        or profile.revision <= 0
    ):
        raise ManifestValidationError("Invalid profile revision.")
    _validate_artifact(profile.archive)
    if profile.archive.kind != "profile_archive":
        raise ManifestValidationError("Profile archive kind is invalid.")
    if not isinstance(profile.bootstrap_digest, str) or not _HEX_64.fullmatch(
        profile.bootstrap_digest
    ):
        raise ManifestValidationError("Invalid profile bootstrap digest.")
    if not isinstance(profile.files, tuple) or not profile.files:
        raise ManifestValidationError("Safe profile files must be immutable.")
    paths = set()
    for item in profile.files:
        _validate_profile_file(item)
        if item.path in paths:
            raise ManifestValidationError("Profile file paths must be unique.")
        paths.add(item.path)


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
    if not isinstance(manifest.ui_packages, tuple):
        raise ManifestValidationError("UI packages must be immutable.")
    if manifest.profile is not None:
        _validate_profile(manifest.profile)
    if (
        isinstance(manifest.minimum_vram_gb, bool)
        or not isinstance(manifest.minimum_vram_gb, (int, float))
        or not 0 <= float(manifest.minimum_vram_gb) <= 1024
    ):
        raise ManifestValidationError("Minimum VRAM is outside safe bounds.")
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

    for package in manifest.ui_packages:
        _validate_ui_package(package)
        if package.package_id in package_ids:
            raise ManifestValidationError("Package IDs must be unique.")
        package_ids.add(package.package_id)
        archive = package.archive
        if archive.artifact_id in artifact_ids:
            raise ManifestValidationError("Artifact IDs must be unique.")
        artifact_ids.add(archive.artifact_id)
        if archive.destination in destinations:
            raise ManifestValidationError("Artifact destinations must be unique.")
        destinations.add(archive.destination)

    if manifest.profile is not None:
        archive = manifest.profile.archive
        if archive.artifact_id in artifact_ids:
            raise ManifestValidationError("Artifact IDs must be unique.")
        if archive.destination in destinations:
            raise ManifestValidationError("Artifact destinations must be unique.")


def validate_dependency(value):
    if isinstance(value, SourceSpec):
        _validate_source(value)
    elif isinstance(value, ArtifactSpec):
        _validate_artifact(value)
    elif isinstance(value, PythonWheelSpec):
        _validate_wheel(value)
    elif isinstance(value, CustomNodeSpec):
        _validate_custom_node(value)
    elif isinstance(value, UiPackageSpec):
        _validate_ui_package(value)
    elif isinstance(value, ProfileFileSpec):
        _validate_profile_file(value)
    elif isinstance(value, ProfileSpec):
        _validate_profile(value)
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


def _ui_package_record(package):
    return {
        "package_id": package.package_id,
        "repository_url": package.repository_url,
        "revision": package.revision,
        "archive": _artifact_record(package.archive),
        "web_sha256": package.web_sha256,
        "required_capabilities": sorted(package.required_capabilities),
    }


def _profile_file_record(item):
    return {
        "path": item.path,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
    }


def _profile_record(profile):
    if profile is None:
        return None
    return {
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "archive": _artifact_record(profile.archive),
        "bootstrap_digest": profile.bootstrap_digest,
        "files": [
            _profile_file_record(item)
            for item in sorted(profile.files, key=lambda item: item.path)
        ],
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
        "ui_packages": [
            _ui_package_record(package)
            for package in sorted(
                manifest.ui_packages,
                key=lambda item: item.package_id,
            )
        ],
        "profile": _profile_record(manifest.profile),
        "minimum_vram_gb": float(manifest.minimum_vram_gb),
        "output_allowance_bytes": manifest.output_allowance_bytes,
        "disk_gb": manifest.disk_gb,
    }

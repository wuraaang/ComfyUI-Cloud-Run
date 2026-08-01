"""Deterministic local artifact discovery for pre-rental analysis."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile

from .manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    PythonWheelSpec,
    SourceSpec,
    validate_dependency,
)


HASH_CHUNK_BYTES = 8 * 1024 * 1024
GIB = 1024**3
HEADROOM_BYTES = 20 * GIB
MINIMUM_DISK_GB = 80
MAX_OUTPUT_ALLOWANCE_BYTES = 2 * 1024**5

_EXCLUDED_DIRECTORY_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "node_modules",
    "output",
    "outputs",
    "venv",
}
_EXCLUDED_FILE_NAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "secrets",
    "secrets.json",
}
_EXCLUDED_FILE_SUFFIXES = {
    ".key",
    ".pem",
    ".pyc",
    ".pyo",
}
_REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[A-Za-z0-9_,.-]+\])?"
    r"==([^;\s]+)(?:\s*;\s*.+)?"
)


class ArtifactResolutionError(ValueError):
    pass


class ArtifactPathError(ArtifactResolutionError):
    pass


class ArtifactCollisionError(ArtifactResolutionError):
    pass


class OutputAllowanceRequired(ArtifactResolutionError):
    pass


class UnpinnedRequirementsError(ArtifactResolutionError):
    pass


@dataclass(frozen=True)
class FileDigest:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FileInputMetadata:
    kind: str
    category: str | None = None

    def __post_init__(self):
        if self.kind not in {"input", "model"}:
            raise ArtifactResolutionError("Invalid file-backed input kind.")
        if self.kind == "model":
            category = str(self.category or "")
            if (
                not category
                or "/" in category
                or "\\" in category
                or category in {".", ".."}
            ):
                raise ArtifactResolutionError("Invalid model category.")
        elif self.category is not None:
            raise ArtifactResolutionError(
                "Input media cannot declare a model category."
            )


@dataclass(frozen=True)
class StaticFileRequirement:
    node_id: str
    class_type: str
    input_name: str
    metadata: FileInputMetadata
    value: object


@dataclass(frozen=True)
class ArtifactResolution:
    node_id: str
    class_type: str
    input_name: str
    kind: str
    status: str
    destination: str | None
    size_bytes: int | None = None
    sha256: str | None = None
    reason: str | None = None
    artifact_id: str | None = None
    cache_available: bool = False


@dataclass(frozen=True, repr=False)
class ResolvedLocalArtifact:
    artifact_id: str
    private_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArtifactResolutionResult:
    rows: tuple[ArtifactResolution, ...]
    artifacts: tuple[ArtifactSpec, ...]
    local_artifacts: tuple[ResolvedLocalArtifact, ...]
    rentable: bool


def _within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolved_root(path):
    try:
        root = Path(path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ArtifactPathError("Approved artifact root is unavailable.") from None
    if not root.is_dir():
        raise ArtifactPathError("Approved artifact root is invalid.")
    return root


def _resolved_file(path, *, allowed_root=None):
    raw = Path(path)
    try:
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ArtifactPathError("Artifact file is unavailable.") from None
    if allowed_root is not None:
        root = _resolved_root(allowed_root)
        if not _within(resolved, root):
            raise ArtifactPathError("Artifact path escapes its approved root.")
    try:
        file_stat = resolved.stat()
    except OSError:
        raise ArtifactPathError("Artifact file is unavailable.") from None
    if not stat.S_ISREG(file_stat.st_mode):
        raise ArtifactPathError("Artifact path is not a regular file.")
    return resolved, file_stat


def hash_file(path, *, allowed_root=None):
    resolved, before = _resolved_file(path, allowed_root=allowed_root)
    digest = hashlib.sha256()
    size = 0
    try:
        with resolved.open("rb") as stream:
            while True:
                chunk = stream.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
        after = resolved.stat()
    except OSError:
        raise ArtifactPathError("Artifact could not be read safely.") from None
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or size != before.st_size or size <= 0:
        raise ArtifactPathError("Artifact changed while it was being read.")
    return FileDigest(size_bytes=size, sha256=digest.hexdigest())


def _excluded(relative):
    parts = relative.parts
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    return (
        name in _EXCLUDED_FILE_NAMES
        or Path(name).suffix.casefold() in _EXCLUDED_FILE_SUFFIXES
    )


def _package_files(package_root):
    files = []
    try:
        for directory, directory_names, file_names in os.walk(
            package_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(directory)
            retained_directories = []
            for name in sorted(directory_names):
                candidate = current / name
                relative = candidate.relative_to(package_root)
                if name in _EXCLUDED_DIRECTORY_NAMES or _excluded(relative):
                    continue
                if candidate.is_symlink():
                    try:
                        target = candidate.resolve(strict=True)
                    except (OSError, RuntimeError):
                        raise ArtifactPathError(
                            "Package contains an invalid symbolic link."
                        ) from None
                    if not _within(target, package_root):
                        raise ArtifactPathError(
                            "Package symbolic link escapes its approved root."
                        )
                    raise ArtifactPathError(
                        "Package directory symbolic links are unsupported."
                    )
                retained_directories.append(name)
            directory_names[:] = retained_directories

            for name in sorted(file_names):
                candidate = current / name
                relative = candidate.relative_to(package_root)
                if _excluded(relative):
                    continue
                if not candidate.is_symlink():
                    if candidate.is_file():
                        files.append((relative, candidate))
                    continue
                try:
                    target = candidate.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ArtifactPathError(
                        "Package contains an invalid symbolic link."
                    ) from None
                if not _within(target, package_root):
                    raise ArtifactPathError(
                        "Package symbolic link escapes its approved root."
                    )
                if target.is_dir():
                    raise ArtifactPathError(
                        "Package directory symbolic links are unsupported."
                    )
                if not target.is_file():
                    raise ArtifactPathError(
                        "Package symbolic link target is unsupported."
                    )
                files.append((relative, target))
    except ArtifactPathError:
        raise
    except OSError:
        raise ArtifactPathError("Package contents are unavailable.") from None
    files.sort(key=lambda item: item[0].as_posix())
    return files


def build_package_archive(package_root, destination):
    root = _resolved_root(package_root)
    destination = Path(destination)
    try:
        destination_parent = destination.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ArtifactPathError("Archive destination is unavailable.") from None
    resolved_destination = destination_parent / destination.name
    if _within(resolved_destination, root):
        raise ArtifactPathError(
            "Package archive destination must be outside the package."
        )
    files = _package_files(root)

    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".cloud-run-archive-",
            suffix=".part",
            dir=str(destination_parent),
        )
        with os.fdopen(descriptor, "wb") as raw_stream:
            with tarfile.open(
                fileobj=raw_stream,
                mode="w",
                format=tarfile.GNU_FORMAT,
            ) as archive:
                for relative, source in files:
                    before = source.stat()
                    info = tarfile.TarInfo(relative.as_posix())
                    info.size = before.st_size
                    info.mode = (
                        0o755
                        if before.st_mode
                        & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        else 0o644
                    )
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with source.open("rb") as source_stream:
                        archive.addfile(info, source_stream)
                    after = source.stat()
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise ArtifactPathError(
                            "Package changed while it was archived."
                        )
            raw_stream.flush()
            os.fsync(raw_stream.fileno())
        os.replace(temporary_name, resolved_destination)
        temporary_name = None
    except ArtifactPathError:
        raise
    except (OSError, tarfile.TarError):
        raise ArtifactPathError("Package archive could not be created.") from None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
    return hash_file(resolved_destination, allowed_root=destination_parent)


def _nonnegative_bytes(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArtifactResolutionError(f"Invalid {name}.")
    return value


def calculate_disk_gb(
    *,
    base_bytes,
    dependency_bytes,
    input_bytes,
    output_bytes,
):
    total = (
        _nonnegative_bytes(base_bytes, "base allowance")
        + _nonnegative_bytes(dependency_bytes, "dependency allowance")
        + _nonnegative_bytes(input_bytes, "input allowance")
        + _nonnegative_bytes(output_bytes, "output allowance")
        + HEADROOM_BYTES
    )
    return max(MINIMUM_DISK_GB, math.ceil(total / GIB))


def reject_destination_collisions(artifacts):
    destinations = {}
    artifact_ids = {}
    result = []
    for item in artifacts:
        destination = str(item.destination)
        digest = str(item.sha256)
        prior_digest = destinations.get(destination)
        if prior_digest is not None and prior_digest != digest:
            raise ArtifactCollisionError(
                "Artifact destination has conflicting content."
            )
        prior_identity = artifact_ids.get(item.artifact_id)
        identity = (destination, digest)
        if prior_identity is not None and prior_identity != identity:
            raise ArtifactCollisionError(
                "Artifact identity has conflicting content."
            )
        destinations[destination] = digest
        artifact_ids[item.artifact_id] = identity
        if not any(
            existing.destination == destination
            and existing.sha256 == digest
            for existing in result
        ):
            result.append(item)
    return tuple(result)


def _safe_relative(value):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ArtifactPathError("File-backed input path is invalid.")
    relative = PurePosixPath(value)
    if (
        str(relative) != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ArtifactPathError("File-backed input path is invalid.")
    return relative


def _metadata_for(metadata, class_type):
    if callable(metadata):
        result = metadata(class_type)
    elif hasattr(metadata, "file_input_metadata"):
        result = metadata.file_input_metadata(class_type)
    else:
        result = metadata.get(class_type, {})
    if result is None:
        return {}
    if not isinstance(result, dict) or not all(
        isinstance(name, str) and isinstance(item, FileInputMetadata)
        for name, item in result.items()
    ):
        raise ArtifactResolutionError("File-input metadata is invalid.")
    return result


def static_file_requirements(capture, metadata):
    output = getattr(capture, "output", None)
    if not isinstance(output, dict):
        raise ArtifactResolutionError("Compiled prompt is invalid.")
    requirements = []
    for raw_node_id, node in output.items():
        node_id = str(raw_node_id)
        if not isinstance(node, dict):
            raise ArtifactResolutionError("Compiled prompt node is invalid.")
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs")
        if not class_type or not isinstance(inputs, dict):
            raise ArtifactResolutionError("Compiled prompt node is invalid.")
        rules = _metadata_for(metadata, class_type)
        for input_name, rule in rules.items():
            requirements.append(
                StaticFileRequirement(
                    node_id=node_id,
                    class_type=class_type,
                    input_name=input_name,
                    metadata=rule,
                    value=inputs.get(input_name),
                )
            )
    return tuple(requirements)


def _roots_for_model(model_roots, category):
    configured = model_roots.get(category, ())
    if isinstance(configured, (str, os.PathLike)):
        configured = (configured,)
    if not isinstance(configured, (tuple, list)):
        raise ArtifactResolutionError("Model roots are invalid.")
    return tuple(_resolved_root(root) for root in configured)


def _find_asset(relative, roots):
    matches = []
    for root in roots:
        try:
            candidate = (root / Path(*relative.parts)).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if _within(candidate, root) and candidate.is_file():
            matches.append((root, candidate))
    if len(matches) > 1:
        raise ArtifactPathError("File-backed input is ambiguous.")
    if not matches:
        raise FileNotFoundError
    return matches[0]


def _artifact_id(kind, digest):
    return f"{kind}-{digest}"


def _normalized_model_sources(model_sources):
    if model_sources is None:
        return {}
    if not isinstance(model_sources, dict):
        raise ArtifactResolutionError("Workflow model sources are invalid.")
    from .model_sources import ModelSourceResolution

    allowed_reasons = {
        "Native model metadata is missing or ambiguous.",
        "Native model metadata is invalid.",
        "The public Hugging Face file could not be verified.",
    }
    normalized = {}
    for key, resolution in model_sources.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(part, str) and part for part in key)
            or not isinstance(resolution, ModelSourceResolution)
        ):
            raise ArtifactResolutionError("Workflow model source is invalid.")
        if resolution.status == "resolved":
            if (
                not isinstance(resolution.source, SourceSpec)
                or resolution.source.kind != "huggingface"
                or resolution.source.secret_handle is not None
                or not isinstance(resolution.size_bytes, int)
                or isinstance(resolution.size_bytes, bool)
                or resolution.size_bytes <= 0
                or not isinstance(resolution.sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", resolution.sha256)
                or resolution.reason is not None
            ):
                raise ArtifactResolutionError(
                    "Resolved workflow model source is invalid."
                )
            validate_dependency(resolution.source)
        elif resolution.status in {"mapping_required", "unsupported"}:
            if (
                resolution.source is not None
                or resolution.size_bytes is not None
                or resolution.sha256 is not None
                or resolution.reason not in allowed_reasons
            ):
                raise ArtifactResolutionError(
                    "Unresolved workflow model source is invalid."
                )
        else:
            raise ArtifactResolutionError("Workflow model source is invalid.")
        normalized[key] = resolution
    return normalized


def resolve_artifacts(
    capture,
    *,
    metadata,
    model_roots,
    input_root,
    source_mappings,
    model_sources=None,
    requirements=None,
):
    model_sources = _normalized_model_sources(model_sources)
    if not isinstance(source_mappings, dict):
        raise ArtifactResolutionError("Artifact source mappings are invalid.")
    normalized_sources = {}
    for digest, source in source_mappings.items():
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(source, SourceSpec)
        ):
            raise ArtifactResolutionError(
                "Artifact source mapping is invalid."
            )
        validate_dependency(source)
        normalized_sources[digest] = source

    resolved_input_root = _resolved_root(input_root)
    rows = []
    artifacts = []
    local_artifacts = {}
    if requirements is None:
        requirements = static_file_requirements(capture, metadata)
    elif not isinstance(requirements, tuple) or not all(
        isinstance(requirement, StaticFileRequirement)
        for requirement in requirements
    ):
        raise ArtifactResolutionError("Static file requirements are invalid.")
    for requirement in requirements:
        node_id = requirement.node_id
        class_type = requirement.class_type
        input_name = requirement.input_name
        rule = requirement.metadata
        value = requirement.value
        if not isinstance(value, str):
            if value is None:
                continue
            rows.append(
                ArtifactResolution(
                    node_id,
                    class_type,
                    input_name,
                    rule.kind,
                    "unsupported",
                    None,
                    reason="File-backed input is not a static filename.",
                )
            )
            continue
        try:
            relative = _safe_relative(value)
        except ArtifactPathError:
            rows.append(
                ArtifactResolution(
                    node_id,
                    class_type,
                    input_name,
                    rule.kind,
                    "unsupported",
                    None,
                    reason="File-backed input cannot be resolved safely.",
                )
            )
            continue

        model_resolution = None
        local_path = None
        if rule.kind == "model":
            destination = (
                PurePosixPath("models")
                / str(rule.category)
                / relative
            ).as_posix()
            model_resolution = model_sources.get((node_id, input_name))
            if (
                model_resolution is not None
                and model_resolution.status != "resolved"
            ):
                rows.append(
                    ArtifactResolution(
                        node_id,
                        class_type,
                        input_name,
                        rule.kind,
                        model_resolution.status,
                        destination,
                        reason=model_resolution.reason,
                    )
                )
                continue
            try:
                roots = _roots_for_model(model_roots, rule.category)
                _, local_path = _find_asset(relative, roots)
            except FileNotFoundError:
                local_path = None
            except ArtifactPathError:
                if model_resolution is not None:
                    local_path = None
                else:
                    rows.append(
                        ArtifactResolution(
                            node_id,
                            class_type,
                            input_name,
                            rule.kind,
                            "unsupported",
                            destination,
                            reason=(
                                "File-backed input cannot be resolved safely."
                            ),
                        )
                    )
                    continue
            if model_resolution is not None:
                file_digest = FileDigest(
                    size_bytes=model_resolution.size_bytes,
                    sha256=model_resolution.sha256,
                )
                source = model_resolution.source
                if local_path is not None:
                    try:
                        local_digest = hash_file(
                            local_path,
                            allowed_root=local_path.parent,
                        )
                    except ArtifactPathError:
                        rows.append(
                            ArtifactResolution(
                                node_id,
                                class_type,
                                input_name,
                                rule.kind,
                                "unsupported",
                                destination,
                                reason=(
                                    "File-backed input cannot be resolved safely."
                                ),
                            )
                        )
                        continue
                    if local_digest != file_digest:
                        rows.append(
                            ArtifactResolution(
                                node_id,
                                class_type,
                                input_name,
                                rule.kind,
                                "unsupported",
                                destination,
                                reason=(
                                    "Local model content conflicts with the "
                                    "verified workflow source."
                                ),
                            )
                        )
                        continue
            elif local_path is None:
                rows.append(
                    ArtifactResolution(
                        node_id,
                        class_type,
                        input_name,
                        rule.kind,
                        "unsupported",
                        None,
                        reason="File-backed input cannot be resolved safely.",
                    )
                )
                continue
            else:
                try:
                    file_digest = hash_file(
                        local_path,
                        allowed_root=local_path.parent,
                    )
                except ArtifactPathError:
                    rows.append(
                        ArtifactResolution(
                            node_id,
                            class_type,
                            input_name,
                            rule.kind,
                            "unsupported",
                            None,
                            reason=(
                                "File-backed input cannot be resolved safely."
                            ),
                        )
                    )
                    continue
                source = normalized_sources.get(file_digest.sha256)
        else:
            try:
                _, local_path = _find_asset(
                    relative,
                    (resolved_input_root,),
                )
                destination = (
                    PurePosixPath("input") / relative
                ).as_posix()
                file_digest = hash_file(
                    local_path,
                    allowed_root=resolved_input_root,
                )
            except (ArtifactPathError, FileNotFoundError):
                rows.append(
                    ArtifactResolution(
                        node_id,
                        class_type,
                        input_name,
                        rule.kind,
                        "unsupported",
                        None,
                        reason="File-backed input cannot be resolved safely.",
                    )
                )
                continue
            source = normalized_sources.get(file_digest.sha256)

        artifact_id = _artifact_id(rule.kind, file_digest.sha256)
        if source is None and rule.kind == "input":
            source = SourceSpec(
                kind="local-upload",
                locator="local-upload:" + artifact_id,
            )
        if local_path is not None:
            local_artifacts.setdefault(
                artifact_id,
                ResolvedLocalArtifact(
                    artifact_id=artifact_id,
                    private_path=str(local_path),
                    size_bytes=file_digest.size_bytes,
                    sha256=file_digest.sha256,
                ),
            )
        if source is None:
            rows.append(
                ArtifactResolution(
                    node_id,
                    class_type,
                    input_name,
                    rule.kind,
                    "mapping_required",
                    destination,
                    file_digest.size_bytes,
                    file_digest.sha256,
                    "Exact artifact source requires approval.",
                    artifact_id,
                )
            )
            continue
        artifact = ArtifactSpec(
            artifact_id=artifact_id,
            kind=rule.kind,
            logical_name=relative.name,
            destination=destination,
            size_bytes=file_digest.size_bytes,
            sha256=file_digest.sha256,
            source=source,
        )
        validate_dependency(artifact)
        artifacts.append(artifact)
        rows.append(
            ArtifactResolution(
                node_id,
                class_type,
                input_name,
                rule.kind,
                "resolved",
                destination,
                file_digest.size_bytes,
                file_digest.sha256,
                artifact_id=artifact_id,
            )
        )

    unique_artifacts = reject_destination_collisions(artifacts)
    immutable_rows = tuple(rows)
    return ArtifactResolutionResult(
        rows=immutable_rows,
        artifacts=unique_artifacts,
        local_artifacts=tuple(
            local_artifacts[key] for key in sorted(local_artifacts)
        ),
        rentable=all(row.status == "resolved" for row in immutable_rows),
    )


def _positive_output_allowance(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 < value <= MAX_OUTPUT_ALLOWANCE_BYTES
    )


def _is_link(value, output):
    return (
        isinstance(value, list)
        and len(value) == 2
        and str(value[0]) in output
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _upstream_nodes(output, start_id):
    queue = [str(start_id)]
    visited = set()
    result = []
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = output.get(node_id)
        if not isinstance(node, dict):
            continue
        result.append(node)
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            if _is_link(value, output):
                queue.append(str(value[0]))
    return result


def _dimension(nodes, names, *, default=None):
    values = []
    for node in nodes:
        inputs = node.get("inputs", {})
        for name in names:
            value = inputs.get(name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
            ):
                values.append(value)
    return max(values) if values else default


def estimate_output_bytes(capture, explicit_bytes=None):
    output = getattr(capture, "output", None)
    if not isinstance(output, dict) or not output:
        raise OutputAllowanceRequired("Compiled output shape is unavailable.")
    total = 0
    unknown = False
    found = False
    for node_id, node in output.items():
        if not isinstance(node, dict):
            continue
        class_name = str(node.get("class_type") or "").casefold()
        is_video = any(
            marker in class_name
            for marker in (
                "savevideo",
                "videocombine",
                "saveanimated",
            )
        )
        is_image = class_name in {"saveimage", "previewimage"}
        if not is_video and not is_image:
            continue
        found = True
        nodes = _upstream_nodes(output, node_id)
        width = _dimension(nodes, ("width",))
        height = _dimension(nodes, ("height",))
        batch = _dimension(nodes, ("batch_size", "batch"), default=1)
        frames = (
            _dimension(
                nodes,
                ("frame_count", "frames", "length", "num_frames"),
            )
            if is_video
            else 1
        )
        if width is None or height is None or frames is None:
            unknown = True
            continue
        estimate = width * height * batch * frames * 4 * 4 * 2
        if estimate <= 0 or estimate > MAX_OUTPUT_ALLOWANCE_BYTES:
            unknown = True
            continue
        total += estimate
        if total > MAX_OUTPUT_ALLOWANCE_BYTES:
            unknown = True

    explicit_valid = _positive_output_allowance(explicit_bytes)
    if unknown or not found or total <= 0:
        if explicit_valid:
            return explicit_bytes
        raise OutputAllowanceRequired(
            "An explicit positive output allowance is required."
        )
    if explicit_bytes is not None and not explicit_valid:
        raise OutputAllowanceRequired("Output allowance is invalid.")
    return max(total, explicit_bytes or 0)


def _wheel_identity(path):
    filename = Path(path).name
    if not filename.endswith(".whl"):
        raise UnpinnedRequirementsError(
            "Python dependencies must be resolved wheel files."
        )
    parts = filename[:-4].rsplit("-", 3)
    if len(parts) != 4:
        raise UnpinnedRequirementsError("Python wheel filename is invalid.")
    distribution_and_version, python_tag, abi_tag, platform_tag = parts
    prefix = distribution_and_version.split("-", 1)
    if len(prefix) != 2 or not all(prefix):
        raise UnpinnedRequirementsError("Python wheel filename is invalid.")
    python_tags = set(python_tag.split("."))
    abi_tags = set(abi_tag.split("."))
    platform_tags = set(platform_tag.split("."))
    if not (
        (
            "cp312" in python_tags
            and abi_tags.intersection({"abi3", "cp312", "none"})
        )
        or ("py3" in python_tags and "none" in abi_tags)
    ):
        raise UnpinnedRequirementsError(
            "Python wheel is incompatible with Python 3.12."
        )
    if "any" not in platform_tags and not any(
        tag == "linux_x86_64"
        or (
            tag.startswith("manylinux")
            and tag.endswith("_x86_64")
        )
        for tag in platform_tags
    ):
        raise UnpinnedRequirementsError(
            "Python wheel is incompatible with Linux."
        )
    return filename, prefix[0].replace("_", "-").casefold()


def _requirements(package_root, wheel_distributions):
    for requirement_file in sorted(package_root.glob("requirements*.txt")):
        try:
            lines = requirement_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise UnpinnedRequirementsError(
                "Python requirements cannot be validated."
            ) from None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _REQUIREMENT.fullmatch(line)
            if match is None:
                raise UnpinnedRequirementsError(
                    "Python requirements must use exact versions."
                )
            distribution = match.group(1).replace("_", "-").casefold()
            if distribution not in wheel_distributions:
                raise UnpinnedRequirementsError(
                    "Every Python requirement needs a resolved wheel."
                )


def build_custom_node_dependency(
    *,
    package_id,
    repository_url,
    revision,
    provided_class_types,
    package_root,
    archive_path,
    wheel_paths,
):
    root = _resolved_root(package_root)
    wheel_records = []
    wheel_distributions = set()
    for wheel_path in wheel_paths:
        filename, distribution = _wheel_identity(wheel_path)
        wheel_distributions.add(distribution)
        file_digest = hash_file(
            wheel_path,
            allowed_root=Path(wheel_path).parent,
        )
        source = SourceSpec(
            kind="local-upload",
            locator="local-upload:wheel-" + file_digest.sha256,
        )
        wheel = PythonWheelSpec(
            filename=filename,
            size_bytes=file_digest.size_bytes,
            sha256=file_digest.sha256,
            source=source,
        )
        validate_dependency(wheel)
        wheel_records.append(wheel)
    _requirements(root, wheel_distributions)

    archive_digest = build_package_archive(root, archive_path)
    archive_id = "custom-node-" + archive_digest.sha256
    archive = ArtifactSpec(
        artifact_id=archive_id,
        kind="custom_node_archive",
        logical_name=str(package_id),
        destination="custom_nodes/" + str(package_id),
        size_bytes=archive_digest.size_bytes,
        sha256=archive_digest.sha256,
        source=SourceSpec(
            kind="local-upload",
            locator="local-upload:" + archive_id,
        ),
    )
    node = CustomNodeSpec(
        package_id=str(package_id),
        repository_url=str(repository_url),
        revision=str(revision),
        archive=archive,
        wheels=tuple(
            sorted(
                wheel_records,
                key=lambda item: (item.filename, item.sha256),
            )
        ),
        provided_class_types=tuple(provided_class_types),
    )
    validate_dependency(node)
    return node

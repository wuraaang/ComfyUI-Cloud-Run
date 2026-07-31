"""Narrow read-only adapter over the already-running local ComfyUI host."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import inspect
import os
from pathlib import Path
import platform
import re
import subprocess

from .artifacts import FileInputMetadata
from .manifest import ManifestValidationError, normalize_github_repository


PINNED_HOST_VERSIONS = ("0.29.0", "1.47.10", "3.13.12")
FORBIDDEN_TREE = Path("/Users/wuraaang/comfyui-vast-cockpit")
_HEX_40 = re.compile(r"[0-9a-f]{40}")


class HostCompatibilityError(RuntimeError):
    pass


class NodeNotFound(LookupError):
    pass


@dataclass(frozen=True)
class NodeRecord:
    source_path: str
    module_name: str
    input_types: dict | None = None


@dataclass(frozen=True)
class NodeDescription:
    class_type: str
    kind: str
    status: str
    source_path: str
    module_name: str
    package_root: str | None
    repository_url: str | None
    revision: str | None


def _is_within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _absolute_lexical(path):
    return Path(os.path.abspath(os.fspath(path)))


def _default_version_reader():
    try:
        from comfyui_version import __version__ as core_version

        frontend_version = importlib.metadata.version(
            "comfyui-frontend-package"
        )
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise HostCompatibilityError(
            "Pinned ComfyUI host versions are unavailable."
        ) from None
    return (
        str(core_version),
        str(frontend_version),
        platform.python_version(),
    )


def _default_git_runner(argv):
    return subprocess.run(
        list(argv),
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        timeout=10,
    )


def _normalize_installed_origin(value):
    origin = str(value or "").strip()
    if origin.startswith("git@github.com:"):
        origin = "https://github.com/" + origin.removeprefix(
            "git@github.com:"
        )
    elif origin.startswith("ssh://git@github.com/"):
        origin = "https://github.com/" + origin.removeprefix(
            "ssh://git@github.com/"
        )
    if origin.endswith(".git"):
        origin = origin[:-4]
    try:
        return normalize_github_repository(origin)
    except ManifestValidationError:
        return None


class ComfyHost:
    def __init__(
        self,
        *,
        comfy_root,
        custom_nodes_root=None,
        node_records=None,
        version_reader=None,
        git_runner=None,
    ):
        lexical_comfy_root = _absolute_lexical(comfy_root)
        lexical_custom_nodes_root = _absolute_lexical(
            custom_nodes_root or lexical_comfy_root / "custom_nodes"
        )
        if _is_within(lexical_comfy_root, FORBIDDEN_TREE) or _is_within(
            lexical_custom_nodes_root,
            FORBIDDEN_TREE,
        ):
            raise HostCompatibilityError("Forbidden ComfyUI host origin.")
        self.comfy_root = lexical_comfy_root.resolve()
        self.custom_nodes_root = lexical_custom_nodes_root.resolve()
        self.node_records = (
            dict(node_records) if node_records is not None else None
        )
        self.version_reader = version_reader or _default_version_reader
        self.git_runner = git_runner or _default_git_runner

    @classmethod
    def from_running_host(cls):
        try:
            import folder_paths
            import nodes
        except ImportError:
            raise HostCompatibilityError(
                "Running ComfyUI node mappings are unavailable."
            ) from None
        records = {}
        for class_type, node_class in nodes.NODE_CLASS_MAPPINGS.items():
            try:
                source_path = inspect.getsourcefile(node_class) or inspect.getfile(
                    node_class
                )
            except (OSError, TypeError):
                continue
            try:
                input_types = node_class.INPUT_TYPES()
            except Exception:
                input_types = None
            if not isinstance(input_types, dict):
                input_types = None
            records[str(class_type)] = NodeRecord(
                source_path=str(source_path),
                module_name=str(getattr(node_class, "__module__", "")),
                input_types=input_types,
            )
        root = Path(folder_paths.base_path)
        return cls(
            comfy_root=root,
            custom_nodes_root=root / "custom_nodes",
            node_records=records,
        )

    def assert_compatible(self):
        try:
            actual = tuple(str(value) for value in self.version_reader())
        except HostCompatibilityError:
            raise
        except Exception:
            raise HostCompatibilityError(
                "Pinned ComfyUI host versions are unavailable."
            ) from None
        if actual != PINNED_HOST_VERSIONS:
            raise HostCompatibilityError(
                "Cloud Run requires ComfyUI Core 0.29.0, frontend 1.47.10, "
                "and Python 3.13.12."
            )
        if not _is_within(self.custom_nodes_root, self.comfy_root):
            raise HostCompatibilityError(
                "ComfyUI custom-node root is outside the pinned host."
            )

    def _git_identity(self, package_root):
        root = str(package_root)
        commands = (
            (
                "git",
                "-C",
                root,
                "config",
                "--get",
                "remote.origin.url",
            ),
            ("git", "-C", root, "rev-parse", "HEAD"),
            (
                "git",
                "-C",
                root,
                "status",
                "--porcelain",
                "--untracked-files=no",
            ),
        )
        results = []
        try:
            for command in commands:
                result = self.git_runner(command)
                if int(result.returncode) != 0:
                    return None
                results.append(str(result.stdout))
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        repository_url = _normalize_installed_origin(results[0])
        revision = results[1].strip()
        dirty = bool(results[2].strip())
        if (
            repository_url is None
            or not _HEX_40.fullmatch(revision)
            or dirty
        ):
            return None
        return repository_url, revision

    def describe_node(self, class_type):
        self.assert_compatible()
        identifier = str(class_type or "")
        record = (self.node_records or {}).get(identifier)
        if record is None:
            raise NodeNotFound("Node is not installed in the pinned host.")
        if not isinstance(record, NodeRecord):
            raise HostCompatibilityError("Local node origin is unavailable.")
        lexical_source = _absolute_lexical(record.source_path)
        if _is_within(lexical_source, FORBIDDEN_TREE):
            raise HostCompatibilityError("Forbidden node origin.")
        source = lexical_source.resolve()
        if _is_within(source, FORBIDDEN_TREE):
            raise HostCompatibilityError("Forbidden node origin.")
        if not _is_within(source, self.comfy_root):
            raise HostCompatibilityError(
                "Local node origin escapes the pinned ComfyUI host."
            )
        if not _is_within(source, self.custom_nodes_root):
            return NodeDescription(
                class_type=identifier,
                kind="core",
                status="resolved",
                source_path=str(source),
                module_name=record.module_name,
                package_root=None,
                repository_url=None,
                revision=None,
            )

        relative = source.relative_to(self.custom_nodes_root)
        if not relative.parts:
            raise HostCompatibilityError("Custom-node origin is invalid.")
        package_root = self.custom_nodes_root / relative.parts[0]
        if package_root.suffix == ".py":
            return NodeDescription(
                class_type=identifier,
                kind="custom",
                status="mapping_required",
                source_path=str(source),
                module_name=record.module_name,
                package_root=None,
                repository_url=None,
                revision=None,
            )
        identity = self._git_identity(package_root)
        return NodeDescription(
            class_type=identifier,
            kind="custom",
            status="candidate" if identity is not None else "mapping_required",
            source_path=str(source),
            module_name=record.module_name,
            package_root=str(package_root),
            repository_url=identity[0] if identity else None,
            revision=identity[1] if identity else None,
        )

    def file_input_metadata(self, capture, *, model_filenames):
        self.assert_compatible()
        if not isinstance(model_filenames, dict) or not all(
            isinstance(category, str)
            and isinstance(filenames, (set, frozenset))
            for category, filenames in model_filenames.items()
        ):
            raise HostCompatibilityError(
                "ComfyUI model metadata is unavailable."
            )
        output = getattr(capture, "output", None)
        if not isinstance(output, dict):
            raise HostCompatibilityError(
                "Compiled prompt metadata is unavailable."
            )
        metadata = {}
        for node in output.values():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "")
            inputs = node.get("inputs")
            record = (self.node_records or {}).get(class_type)
            if (
                not isinstance(record, NodeRecord)
                or not isinstance(record.input_types, dict)
                or not isinstance(inputs, dict)
            ):
                continue
            specifications = {}
            for section in ("required", "optional"):
                values = record.input_types.get(section, {})
                if isinstance(values, dict):
                    specifications.update(values)
            class_metadata = metadata.setdefault(class_type, {})
            for input_name, value in inputs.items():
                if not isinstance(value, str):
                    continue
                specification = specifications.get(input_name)
                if (
                    not isinstance(specification, (tuple, list))
                    or not specification
                ):
                    continue
                input_type = specification[0]
                options = (
                    specification[1]
                    if len(specification) > 1
                    and isinstance(specification[1], dict)
                    else {}
                )
                upload = options.get("image_upload") is True or (
                    isinstance(input_type, str)
                    and input_type
                    in {
                        "AUDIO_UPLOAD",
                        "FILE_UPLOAD",
                        "IMAGE_UPLOAD",
                        "VIDEO_UPLOAD",
                    }
                )
                if upload:
                    class_metadata[input_name] = FileInputMetadata(
                        kind="input"
                    )
                    continue
                if not isinstance(input_type, (tuple, list)):
                    continue
                matching_categories = sorted(
                    category
                    for category, filenames in model_filenames.items()
                    if value in filenames
                )
                if len(matching_categories) > 1:
                    raise HostCompatibilityError(
                        "ComfyUI model filename category is ambiguous."
                    )
                if len(matching_categories) == 1:
                    class_metadata[input_name] = FileInputMetadata(
                        kind="model",
                        category=matching_categories[0],
                    )
            if not class_metadata:
                metadata.pop(class_type, None)
        return metadata

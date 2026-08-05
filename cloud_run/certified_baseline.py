"""Fail-closed materialization of the reviewed Desktop extension baseline."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

from .artifacts import ResolvedLocalArtifact, build_package_archive
from .manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    PythonWheelSpec,
    SourceSpec,
    UiPackageSpec,
    validate_dependency,
)


_LOCK_PATH = Path(__file__).with_name("certified_baseline.lock.json")
_ASSETS_ROOT = Path(__file__).with_name("baseline_assets")
_OFFICIAL_LOCK_SHA256 = (
    "158f59a73e5b898747f53bdbd3940ca2f627445f778218b76338d484f9e48a52"
)
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_EXECUTABLE_SUFFIXES = frozenset(
    {".bat", ".cmd", ".com", ".exe", ".ps1", ".py", ".pyc", ".pyo", ".sh"}
)
_PROTECTED_DISTRIBUTIONS = (
    "accelerate",
    "aiohttp",
    "comfyui",
    "comfyui-frontend-package",
    "numpy",
    "open-clip-torch",
    "pillow",
    "pip",
    "requests",
    "safetensors",
    "setuptools",
    "torch",
    "torchaudio",
    "torchvision",
    "transformers",
    "wheel",
)
_AGENT_LOADER = (
    b"NODE_CLASS_MAPPINGS = {}\n"
    b"NODE_DISPLAY_NAME_MAPPINGS = {}\n"
    b'WEB_DIRECTORY = "./web"\n\n'
    b"__all__ = [\"NODE_CLASS_MAPPINGS\", "
    b'"NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]\n'
)
class CertifiedBaselineUnavailable(RuntimeError):
    """The immutable Desktop baseline cannot be certified safely."""

    def __init__(self):
        super().__init__("baseline_unavailable")


@dataclass(frozen=True)
class CertifiedBaselineResolution:
    ui_packages: tuple[UiPackageSpec, ...]
    custom_nodes: tuple[CustomNodeSpec, ...]
    local_artifacts: tuple[ResolvedLocalArtifact, ...]
    digest: str


def _fail():
    raise CertifiedBaselineUnavailable()


def _sha256(body):
    return hashlib.sha256(body).hexdigest()


def _canonical_bytes(value):
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _tree_records(root):
    root = Path(root)
    try:
        files = sorted(path for path in root.rglob("*") if path.is_file())
        records = []
        for path in files:
            if path.is_symlink():
                _fail()
            body = path.read_bytes()
            records.append(
                {
                    "mode": 0o644,
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256(body),
                    "size_bytes": len(body),
                }
            )
        return records
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, RuntimeError):
        _fail()


def canonical_tree_sha256(root):
    """Hash sorted path/mode/size/content records for a materialized tree."""

    return _sha256(_canonical_bytes(_tree_records(root)))


def _distribution_from_wheel(filename):
    stem = filename.split("-", 1)[0]
    return re.sub(r"[-_.]+", "-", stem).lower()


def _validate_https(url):
    if not isinstance(url, str) or not url.startswith("https://"):
        _fail()


def _validate_digest_record(record, *, file_record=False):
    required = {"size_bytes", "sha256"}
    if file_record:
        required.add("path")
    if not isinstance(record, dict) or not required.issubset(record):
        _fail()
    size = record["size_bytes"]
    digest = record["sha256"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not isinstance(digest, str)
        or _HEX_64.fullmatch(digest) is None
    ):
        _fail()


def _validate_relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        _fail()
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail()


def _validate_source(source):
    if not isinstance(source, dict):
        _fail()
    if set(source) != {"files", "sha256", "size_bytes", "url"}:
        _fail()
    _validate_https(source["url"])
    _validate_digest_record(source)
    files = source["files"]
    if not isinstance(files, list) or not files:
        _fail()
    paths = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "sha256",
            "size_bytes",
        }:
            _fail()
        _validate_digest_record(item, file_record=True)
        _validate_relative_path(item["path"])
        if item["path"] in paths:
            _fail()
        paths.add(item["path"])


def _validate_package_record(record, *, local=False):
    if not isinstance(record, dict):
        _fail()
    for field in (
        "curated_archive",
        "package_id",
        "permitted_paths",
        "repository",
        "revision",
        "web",
    ):
        if field not in record:
            _fail()
    if not isinstance(record["package_id"], str):
        _fail()
    _validate_https(record["repository"])
    if not isinstance(record["revision"], str):
        _fail()
    _validate_digest_record(record["curated_archive"])
    permitted = record["permitted_paths"]
    if (
        not isinstance(permitted, list)
        or not permitted
        or not all(isinstance(item, str) and item for item in permitted)
        or len(permitted) != len(set(permitted))
    ):
        _fail()
    web = record["web"]
    if not isinstance(web, dict) or set(web) != {
        "file_count",
        "root",
        "sha256",
        "size_bytes",
    }:
        _fail()
    _validate_digest_record(web)
    if (
        not isinstance(web["file_count"], int)
        or web["file_count"] <= 0
        or not isinstance(web["root"], str)
    ):
        _fail()
    if local:
        files = record.get("files")
        if not isinstance(files, list) or not files:
            _fail()
        for item in files:
            _validate_digest_record(item, file_record=True)
            _validate_relative_path(item["path"])
        if not isinstance(record.get("source_tree_sha256"), str):
            _fail()
    else:
        _validate_source(record.get("source_archive"))


def _validate_lock(record, *, lock_bytes):
    if not isinstance(record, dict) or set(record) != {
        "comfyui_frontend_version",
        "custom_nodes",
        "protected_distributions",
        "schema_version",
        "ui_packages",
    }:
        _fail()
    if (
        record["schema_version"] != 1
        or record["comfyui_frontend_version"] != "1.47.10"
        or record["protected_distributions"] != list(_PROTECTED_DISTRIBUTIONS)
        or not isinstance(record["ui_packages"], list)
        or len(record["ui_packages"]) != 2
        or not isinstance(record["custom_nodes"], list)
        or len(record["custom_nodes"]) != 1
    ):
        _fail()
    if _sha256(lock_bytes) != _OFFICIAL_LOCK_SHA256:
        _fail()

    agent, hermes = record["ui_packages"]
    efficiency = record["custom_nodes"][0]
    _validate_package_record(agent)
    _validate_package_record(hermes, local=True)
    _validate_package_record(efficiency)
    exact_identity = (
        agent.get("package_id") == "comfyui-agent-panel"
        and agent.get("repository")
        == "https://github.com/artokun/comfyui-mcp-panel"
        and agent.get("version") == "0.11.38"
        and agent.get("revision")
        == "e4de6a5a2e8fbcde166b5fe2983bf3404ca10e0b"
        and agent.get("registry_version_id")
        == "e4ad28a2-549e-4804-9927-b4699ad5ec0f"
        and agent["source_archive"]["url"]
        == "https://cdn.comfy.org/artokun/comfyui-agent-panel/0.11.38/node.zip"
        and hermes.get("package_id") == "hermes-nous"
        and hermes.get("repository")
        == "https://github.com/wuraaang/ComfyUI-Cloud-Run"
        and hermes.get("revision")
        == "sha256:743a5a2505cab78c3502c0fcf50b83780790a5a5b14e7fc1be3e438a3ca7c82f"
        and efficiency.get("package_id") == "efficiency-nodes-comfyui"
        and efficiency.get("repository")
        == "https://github.com/jags111/efficiency-nodes-comfyui"
        and efficiency.get("version") == "1.0.9"
        and efficiency.get("revision")
        == "835bbe14627cccc871822e804c65c734960d3c6e"
        and efficiency.get("registry_version_id")
        == "03cae633-3466-451d-b479-4ad1e1fbba04"
        and efficiency["source_archive"]["url"]
        == "https://cdn.comfy.org/jags111/efficiency-nodes-comfyui/1.0.9/node.zip"
    )
    if not exact_identity:
        _fail()
    if (
        not isinstance(agent.get("required_capabilities"), list)
        or not isinstance(hermes.get("required_capabilities"), list)
        or not isinstance(efficiency.get("class_types"), list)
        or len(efficiency["class_types"]) != 40
        or len(set(efficiency["class_types"])) != 40
        or efficiency.get("dropped_dependencies") != ["clip-interrogator"]
    ):
        _fail()
    wheels = efficiency.get("wheels")
    if not isinstance(wheels, list) or len(wheels) != 1:
        _fail()
    wheel = wheels[0]
    if not isinstance(wheel, dict) or set(wheel) != {
        "distribution",
        "filename",
        "sha256",
        "size_bytes",
        "url",
    }:
        _fail()
    _validate_digest_record(wheel)
    _validate_https(wheel["url"])
    distribution = _distribution_from_wheel(wheel.get("filename", ""))
    if (
        wheel.get("distribution") != "simpleeval"
        or distribution != "simpleeval"
        or distribution in _PROTECTED_DISTRIBUTIONS
        or not wheel["filename"].endswith(".whl")
        or wheel["url"] != (
            "https://files.pythonhosted.org/packages/0f/2f/"
            "f32aa85591882378bb43caa09363f3ed97df399369a5144c7f19f2275bc0/"
            "simpleeval-1.0.7-py3-none-any.whl"
        )
    ):
        _fail()


def _match_permitted(path, patterns):
    for pattern in patterns:
        if pattern.endswith("/**"):
            root = pattern[:-3]
            if path.startswith(root + "/"):
                return True
        elif path == pattern:
            return True
    return False


def _zip_records_and_bodies(body):
    records = []
    bodies = {}
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                _validate_relative_path(info.filename)
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK or info.flag_bits & 1:
                    _fail()
                if info.filename in bodies:
                    _fail()
                content = archive.read(info)
                records.append(
                    {
                        "path": info.filename,
                        "sha256": _sha256(content),
                        "size_bytes": len(content),
                    }
                )
                bodies[info.filename] = content
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile):
        _fail()
    return records, bodies


def _write_file(root, relative, body):
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o644)
    except OSError:
        _fail()


def _verify_web(root, record):
    records = _tree_records(root)
    if (
        len(records) != record["file_count"]
        or sum(item["size_bytes"] for item in records) != record["size_bytes"]
    ):
        _fail()
    measured = _sha256(_canonical_bytes(records))
    if measured != record["sha256"]:
        _fail()


def _verify_efficiency_source(root, class_types):
    strings = set()
    try:
        for path in sorted(root.rglob("*.py")):
            body = path.read_bytes()
            if b"clip_interrogator" in body.lower() or b"clip-interrogator" in body.lower():
                _fail()
            tree = ast.parse(body, filename=path.as_posix())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    strings.add(node.value)
                if isinstance(node, (ast.Name, ast.Attribute)):
                    identifier = getattr(node, "id", getattr(node, "attr", ""))
                    if str(identifier).casefold() == "clip_interrogator":
                        _fail()
                if isinstance(node, ast.Import):
                    if any(alias.name.casefold() == "clip_interrogator" for alias in node.names):
                        _fail()
                if isinstance(node, ast.ImportFrom):
                    if str(node.module or "").casefold() == "clip_interrogator":
                        _fail()
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, SyntaxError, ValueError):
        _fail()
    if not set(class_types).issubset(strings):
        _fail()


def _resolved_cache_root(cache_root):
    root = Path(cache_root)
    try:
        if root.exists() and root.is_symlink():
            _fail()
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        root.chmod(0o700)
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            _fail()
        return resolved
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, RuntimeError):
        _fail()


def _private_cache_child(cache_root, name):
    child = cache_root / name
    try:
        if child.is_symlink():
            _fail()
        child.mkdir(mode=0o700, exist_ok=True)
        if child.is_symlink() or not child.is_dir():
            _fail()
        resolved = child.resolve(strict=True)
        if resolved.parent != cache_root:
            _fail()
        resolved.chmod(0o700)
        return resolved
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, RuntimeError):
        _fail()


@contextmanager
def _private_staging_root(cache_root):
    try:
        with tempfile.TemporaryDirectory(
            prefix="baseline-stage-",
            dir=cache_root,
        ) as raw_stage:
            stage = Path(raw_stage).resolve(strict=True)
            if stage.parent != cache_root:
                _fail()
            yield stage
    except CertifiedBaselineUnavailable:
        raise
    except (OSError, RuntimeError):
        _fail()


def _file_matches(path, record):
    try:
        if not path.is_file() or path.is_symlink():
            return False
        body = path.read_bytes()
        return len(body) == record["size_bytes"] and _sha256(body) == record["sha256"]
    except OSError:
        return False


class CertifiedBaselineResolver:
    def __init__(self):
        path = _LOCK_PATH
        try:
            body = path.read_bytes()
            record = json.loads(body.decode("utf-8"))
            _validate_lock(record, lock_bytes=body)
        except CertifiedBaselineUnavailable:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            _fail()
        self._lock = record
        self._assets_root = _ASSETS_ROOT

    async def _download(self, fetcher, cache_root, record, suffix):
        downloads = _private_cache_child(cache_root, "downloads")
        destination = downloads / (record["sha256"] + suffix)
        if destination.exists():
            if not _file_matches(destination, record):
                _fail()
            return destination
        try:
            fetched = fetcher(record["url"])
            if inspect.isawaitable(fetched):
                fetched = await fetched
            if not isinstance(fetched, bytes):
                _fail()
            if (
                len(fetched) != record["size_bytes"]
                or _sha256(fetched) != record["sha256"]
            ):
                _fail()
            descriptor, temporary = tempfile.mkstemp(
                prefix=".baseline-download-",
                dir=downloads,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(fetched)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            return destination
        except CertifiedBaselineUnavailable:
            raise
        except Exception:
            _fail()

    def _archive_artifact(self, cache_root, staging, record, *, kind, prefix):
        archive_record = record["curated_archive"]
        artifact_id = prefix + "-" + archive_record["sha256"]
        archive_root = _private_cache_child(cache_root, "archives")
        destination = archive_root / (archive_record["sha256"] + ".tar")
        if destination.exists():
            if not _file_matches(destination, archive_record):
                _fail()
        else:
            try:
                digest = build_package_archive(staging, destination)
            except Exception:
                _fail()
            if (
                digest.size_bytes != archive_record["size_bytes"]
                or digest.sha256 != archive_record["sha256"]
            ):
                try:
                    destination.unlink()
                except OSError:
                    pass
                _fail()
        artifact = ArtifactSpec(
            artifact_id=artifact_id,
            kind=kind,
            logical_name=record["package_id"] + ".tar",
            destination="custom_nodes/" + record["package_id"],
            size_bytes=archive_record["size_bytes"],
            sha256=archive_record["sha256"],
            source=SourceSpec("local-upload", "local-upload:" + artifact_id),
        )
        local = ResolvedLocalArtifact(
            artifact_id=artifact_id,
            private_path=str(destination),
            size_bytes=archive_record["size_bytes"],
            sha256=archive_record["sha256"],
        )
        return artifact, local

    async def resolve(self, *, fetcher, cache_root):
        root = _resolved_cache_root(cache_root)
        agent, hermes = self._lock["ui_packages"]
        efficiency = self._lock["custom_nodes"][0]
        agent_source = await self._download(
            fetcher, root, agent["source_archive"], ".zip"
        )
        efficiency_source = await self._download(
            fetcher, root, efficiency["source_archive"], ".zip"
        )
        wheel_record = efficiency["wheels"][0]
        wheel_path = await self._download(fetcher, root, wheel_record, ".whl")

        try:
            agent_body = agent_source.read_bytes()
            efficiency_body = efficiency_source.read_bytes()
        except OSError:
            _fail()
        agent_records, agent_bodies = _zip_records_and_bodies(agent_body)
        efficiency_records, efficiency_bodies = _zip_records_and_bodies(
            efficiency_body
        )
        if agent_records != agent["source_archive"]["files"]:
            _fail()
        if efficiency_records != efficiency["source_archive"]["files"]:
            _fail()

        with _private_staging_root(root) as stage:
            agent_stage = stage / "agent"
            efficiency_stage = stage / "efficiency"
            hermes_stage = stage / "hermes"
            for package_stage in (agent_stage, efficiency_stage, hermes_stage):
                package_stage.mkdir()

            for path, body in agent_bodies.items():
                if not _match_permitted(path, agent["permitted_paths"]):
                    continue
                if (
                    path != "LICENSE"
                    and PurePosixPath(path).suffix.casefold()
                    in _EXECUTABLE_SUFFIXES
                ):
                    _fail()
                _write_file(agent_stage, path, body)
            _write_file(agent_stage, "__init__.py", _AGENT_LOADER)
            _verify_web(
                agent_stage / agent["web"]["root"],
                agent["web"],
            )

            selected_efficiency = {}
            for path, body in efficiency_bodies.items():
                if _match_permitted(path, efficiency["permitted_paths"]):
                    selected_efficiency[path] = body
                    _write_file(efficiency_stage, path, body)
            if len(selected_efficiency) != efficiency["curated_archive"]["file_count"]:
                _fail()
            _verify_efficiency_source(
                efficiency_stage,
                efficiency["class_types"],
            )
            _verify_web(
                efficiency_stage / efficiency["web"]["root"],
                efficiency["web"],
            )

            expected_hermes = {item["path"]: item for item in hermes["files"]}
            try:
                actual_paths = sorted(
                    path.relative_to(self._assets_root / "hermes-nous").as_posix()
                    for path in (self._assets_root / "hermes-nous").rglob("*")
                    if path.is_file()
                )
            except (OSError, RuntimeError, ValueError):
                _fail()
            if actual_paths != sorted(expected_hermes):
                _fail()
            for path, expected in expected_hermes.items():
                source = self._assets_root / "hermes-nous" / path
                if not _file_matches(source, expected):
                    _fail()
                try:
                    body = source.read_bytes()
                except OSError:
                    _fail()
                _write_file(hermes_stage, path, body)
            _verify_web(
                hermes_stage / hermes["web"]["root"],
                hermes["web"],
            )

            agent_artifact, agent_local = self._archive_artifact(
                root,
                agent_stage,
                agent,
                kind="ui_package_archive",
                prefix="ui-agent-panel",
            )
            hermes_artifact, hermes_local = self._archive_artifact(
                root,
                hermes_stage,
                hermes,
                kind="ui_package_archive",
                prefix="ui-hermes-nous",
            )
            efficiency_artifact, efficiency_local = self._archive_artifact(
                root,
                efficiency_stage,
                efficiency,
                kind="custom_node_archive",
                prefix="custom-efficiency-nodes",
            )

        wheel_artifact_id = "wheel-simpleeval-" + wheel_record["sha256"]
        wheel = PythonWheelSpec(
            filename=wheel_record["filename"],
            size_bytes=wheel_record["size_bytes"],
            sha256=wheel_record["sha256"],
            source=SourceSpec(
                "local-upload",
                "local-upload:" + wheel_artifact_id,
            ),
        )
        wheel_local = ResolvedLocalArtifact(
            artifact_id=wheel_artifact_id,
            private_path=str(wheel_path),
            size_bytes=wheel_record["size_bytes"],
            sha256=wheel_record["sha256"],
        )
        ui_packages = (
            UiPackageSpec(
                package_id=agent["package_id"],
                repository_url=agent["repository"],
                revision=agent["revision"],
                archive=agent_artifact,
                web_sha256=agent["web"]["sha256"],
                required_capabilities=tuple(agent["required_capabilities"]),
            ),
            UiPackageSpec(
                package_id=hermes["package_id"],
                repository_url=hermes["repository"],
                revision=hermes["revision"],
                archive=hermes_artifact,
                web_sha256=hermes["web"]["sha256"],
                required_capabilities=tuple(hermes["required_capabilities"]),
            ),
        )
        custom_nodes = (
            CustomNodeSpec(
                package_id=efficiency["package_id"],
                repository_url=efficiency["repository"],
                revision=efficiency["revision"],
                archive=efficiency_artifact,
                wheels=(wheel,),
                provided_class_types=tuple(efficiency["class_types"]),
            ),
        )
        for item in (*ui_packages, *custom_nodes):
            validate_dependency(item)
        return CertifiedBaselineResolution(
            ui_packages=ui_packages,
            custom_nodes=custom_nodes,
            local_artifacts=(
                agent_local,
                hermes_local,
                efficiency_local,
                wheel_local,
            ),
            digest=_sha256(_canonical_bytes(self._lock)),
        )


__all__ = [
    "CertifiedBaselineResolution",
    "CertifiedBaselineResolver",
    "CertifiedBaselineUnavailable",
    "canonical_tree_sha256",
]

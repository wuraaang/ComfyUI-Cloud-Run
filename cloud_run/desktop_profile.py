"""Content-addressed, allowlisted ComfyUI Desktop profile snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile

from .artifacts import ResolvedLocalArtifact
from .manifest import (
    ArtifactSpec,
    ProfileFileSpec,
    ProfileSpec,
    SourceSpec,
    UiPackageSpec,
    validate_dependency,
)


MAX_PROFILE_FILE_BYTES = 16 * 1024 * 1024
MAX_PROFILE_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_PROFILE_FILES = 2_000
MAX_JSON_DEPTH = 64
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_SAFE_KINDS = {
    "workflow",
    "bootstrap_workflow",
    "settings",
    "palette",
    "background",
    "ui_asset",
}
_SAFE_ROOTS = {
    "workflows": "workflow",
    "bootstrap": "bootstrap_workflow",
    "settings": "settings",
    "palettes": "palette",
    "backgrounds": "background",
    "assets": "ui_asset",
}
_FORBIDDEN_NAMES = {
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
_FORBIDDEN_SUFFIXES = {".db", ".key", ".log", ".pem", ".pyc", ".pyo"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_UI_ASSET_SUFFIXES = {".css", ".jpg", ".jpeg", ".png", ".webp"}
_SUSPICIOUS_KEY = re.compile(
    r"(?i)(authorization|bearer|cookie|credential|password|secret|token|api[_ -]?key)"
)
_SUSPICIOUS_VALUE = re.compile(
    r"(?i)(bearer\s+|[?&](?:token|signature|x-amz-[^=]+)=|-----begin .*private key)"
)
_AGENT_PANEL_PACKAGE_ID = "comfyui-agent-panel"
_AGENT_PANEL_BRIDGE_SETTING = "comfyui-mcp.bridgeUrl.single"
_AGENT_PANEL_REMOTE_SETTING = "comfyui-mcp.remoteComfyuiUrl"
_AGENT_PANEL_BRIDGE_PATH = "/cloud-run/api/agent/ws"
_DROP = object()


class DesktopProfileError(RuntimeError):
    """A safe profile could not be captured or synchronized."""


@dataclass(frozen=True, repr=False)
class ProfileArtifact:
    logical_path: str
    kind: str
    size_bytes: int
    sha256: str
    private_path: Path = field(repr=False)

    def __post_init__(self):
        _validate_logical_path(self.logical_path, self.kind)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or not 0 <= self.size_bytes <= MAX_PROFILE_FILE_BYTES
            or not isinstance(self.sha256, str)
            or _HEX_64.fullmatch(self.sha256) is None
            or not isinstance(self.private_path, Path)
        ):
            raise DesktopProfileError("Desktop profile artifact is invalid.")


@dataclass(frozen=True, repr=False)
class DesktopProfile:
    profile_id: str
    revision: int
    base_revision: int | None
    bootstrap_digest: str
    archive_size_bytes: int
    archive_sha256: str
    artifacts: tuple[ProfileArtifact, ...]
    ui_packages: tuple[UiPackageSpec, ...]
    archive_private_path: Path = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.profile_id, str)
            or _IDENTIFIER.fullmatch(self.profile_id) is None
            or isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision <= 0
            or (
                self.base_revision is not None
                and (
                    isinstance(self.base_revision, bool)
                    or not isinstance(self.base_revision, int)
                    or not 0 < self.base_revision < self.revision
                )
            )
            or (self.revision == 1) != (self.base_revision is None)
            or not isinstance(self.bootstrap_digest, str)
            or _HEX_64.fullmatch(self.bootstrap_digest) is None
            or isinstance(self.archive_size_bytes, bool)
            or not isinstance(self.archive_size_bytes, int)
            or not 0 < self.archive_size_bytes <= MAX_PROFILE_ARCHIVE_BYTES
            or not isinstance(self.archive_sha256, str)
            or _HEX_64.fullmatch(self.archive_sha256) is None
            or not isinstance(self.artifacts, tuple)
            or not self.artifacts
            or len(self.artifacts) > MAX_PROFILE_FILES
            or not all(isinstance(item, ProfileArtifact) for item in self.artifacts)
            or tuple(sorted(item.logical_path for item in self.artifacts))
            != tuple(item.logical_path for item in self.artifacts)
            or len({item.logical_path for item in self.artifacts})
            != len(self.artifacts)
            or not isinstance(self.ui_packages, tuple)
            or not isinstance(self.archive_private_path, Path)
        ):
            raise DesktopProfileError("Desktop profile is invalid.")
        try:
            for package in self.ui_packages:
                validate_dependency(package)
        except (TypeError, ValueError):
            raise DesktopProfileError("Desktop profile is invalid.") from None

    def manifest_spec(self):
        artifact_id = "profile-" + self.archive_sha256
        archive = ArtifactSpec(
            artifact_id=artifact_id,
            kind="profile_archive",
            logical_name=artifact_id,
            destination="user/default/cloud-vast-profile",
            size_bytes=self.archive_size_bytes,
            sha256=self.archive_sha256,
            source=SourceSpec(
                kind="local-upload",
                locator="local-upload:" + artifact_id,
            ),
        )
        spec = ProfileSpec(
            profile_id=self.profile_id,
            revision=self.revision,
            archive=archive,
            bootstrap_digest=self.bootstrap_digest,
            files=tuple(
                ProfileFileSpec(
                    path=item.logical_path,
                    size_bytes=item.size_bytes,
                    sha256=item.sha256,
                )
                for item in self.artifacts
            ),
        )
        validate_dependency(spec)
        return spec

    def public_payload(self):
        return {
            "profile_id": self.profile_id,
            "revision": self.revision,
            "base_revision": self.base_revision,
            "bootstrap_digest": self.bootstrap_digest,
            "archive_size_bytes": self.archive_size_bytes,
            "archive_sha256": self.archive_sha256,
            "artifacts": [
                {
                    "path": item.logical_path,
                    "kind": item.kind,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in self.artifacts
            ],
        }


@dataclass(frozen=True)
class ProfileConflict:
    conflict_id: str
    profile_id: str
    base_revision: int
    local_revision: int
    remote_revision: int
    resolved_revision: int | None
    local_label: str = "Local"
    remote_label: str = "Cloud Vast"

    def public_payload(self):
        return {
            "conflict_id": self.conflict_id,
            "profile_id": self.profile_id,
            "base_revision": self.base_revision,
            "local_revision": self.local_revision,
            "remote_revision": self.remote_revision,
            "resolved_revision": self.resolved_revision,
            "local_label": self.local_label,
            "remote_label": self.remote_label,
        }


def _validate_logical_path(value, kind):
    if (
        kind not in _SAFE_KINDS
        or not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise DesktopProfileError("Desktop profile path is invalid.")
    path = PurePosixPath(value)
    folded = tuple(part.casefold() for part in path.parts)
    if (
        str(path) != value
        or not path.parts
        or _SAFE_ROOTS.get(path.parts[0]) != kind
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part in _FORBIDDEN_NAMES for part in folded)
        or path.suffix.casefold() in _FORBIDDEN_SUFFIXES
        or (kind == "ui_asset" and path.suffix.casefold() not in _UI_ASSET_SUFFIXES)
        or (kind == "background" and path.suffix.casefold() not in _IMAGE_SUFFIXES)
    ):
        raise DesktopProfileError("Desktop profile path is invalid.")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _bounded_tree(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise DesktopProfileError("Desktop profile JSON is invalid.")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise DesktopProfileError("Desktop profile JSON is invalid.")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise DesktopProfileError("Desktop profile JSON is invalid.")


def _canonical_json(value):
    _bounded_tree(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise DesktopProfileError("Desktop profile JSON is invalid.") from None
    if not 0 < len(encoded) <= MAX_PROFILE_FILE_BYTES:
        raise DesktopProfileError("Desktop profile JSON is invalid.")
    return encoded


def _read_json(path):
    content = _read_regular(path, maximum=MAX_PROFILE_FILE_BYTES)
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise DesktopProfileError("Desktop profile JSON is invalid.") from None
    return value


def _read_regular(path, *, maximum):
    path = Path(path)
    descriptor = None
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_size < 0
            or before.st_size > maximum
        ):
            raise OSError("unsafe file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise OSError("changed file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise OSError("oversized file")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise OSError("changed file")
        return b"".join(chunks)
    except OSError:
        raise DesktopProfileError("Desktop profile file is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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


def _ui_package_record(package):
    return {
        "package_id": package.package_id,
        "repository_url": package.repository_url,
        "revision": package.revision,
        "archive": _artifact_record(package.archive),
        "web_sha256": package.web_sha256,
        "required_capabilities": list(package.required_capabilities),
    }


def _source_from_record(record):
    if not isinstance(record, dict) or set(record) != {
        "kind",
        "locator",
        "immutable_revision",
    }:
        raise DesktopProfileError("Stored Desktop profile is invalid.")
    return SourceSpec(
        kind=record["kind"],
        locator=record["locator"],
        immutable_revision=record["immutable_revision"],
    )


def _artifact_from_record(record):
    if not isinstance(record, dict) or set(record) != {
        "artifact_id",
        "kind",
        "logical_name",
        "destination",
        "size_bytes",
        "sha256",
        "source",
    }:
        raise DesktopProfileError("Stored Desktop profile is invalid.")
    return ArtifactSpec(
        artifact_id=record["artifact_id"],
        kind=record["kind"],
        logical_name=record["logical_name"],
        destination=record["destination"],
        size_bytes=record["size_bytes"],
        sha256=record["sha256"],
        source=_source_from_record(record["source"]),
    )


def _ui_package_from_record(record):
    if not isinstance(record, dict) or set(record) != {
        "package_id",
        "repository_url",
        "revision",
        "archive",
        "web_sha256",
        "required_capabilities",
    } or not isinstance(record["required_capabilities"], list):
        raise DesktopProfileError("Stored Desktop profile is invalid.")
    package = UiPackageSpec(
        package_id=record["package_id"],
        repository_url=record["repository_url"],
        revision=record["revision"],
        archive=_artifact_from_record(record["archive"]),
        web_sha256=record["web_sha256"],
        required_capabilities=tuple(record["required_capabilities"]),
    )
    try:
        validate_dependency(package)
    except (TypeError, ValueError):
        raise DesktopProfileError("Stored Desktop profile is invalid.") from None
    return package


class DesktopProfileStore:
    def __init__(
        self,
        *,
        repository,
        private_root,
        profile_id="desktop-profile",
        clock=None,
    ):
        required = (
            "latest_profile_revision",
            "get_profile_revision",
            "save_profile_revision",
            "save_profile_conflict",
            "get_profile_conflict",
            "resolve_profile_conflict",
            "list_profile_conflicts",
            "register_local_artifact",
        )
        if not all(callable(getattr(repository, name, None)) for name in required):
            raise DesktopProfileError("Desktop profile storage is unavailable.")
        if not isinstance(profile_id, str) or _IDENTIFIER.fullmatch(profile_id) is None:
            raise DesktopProfileError("Desktop profile identity is invalid.")
        self.repository = repository
        self.profile_id = profile_id
        self.clock = clock or __import__("time").time
        self.private_root = Path(private_root)
        self.files_root = self.private_root / "files"
        self.archives_root = self.private_root / "archives"
        try:
            for path in (self.private_root, self.files_root, self.archives_root):
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                metadata = os.lstat(path)
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise OSError("unsafe directory")
                os.chmod(path, 0o700)
            self.private_root = self.private_root.resolve(strict=True)
            self.files_root = self.files_root.resolve(strict=True)
            self.archives_root = self.archives_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise DesktopProfileError("Desktop profile storage is unavailable.") from None

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise DesktopProfileError("Desktop profile clock is unavailable.")
        return float(value)

    @staticmethod
    def _kind(logical_path):
        return _SAFE_ROOTS[PurePosixPath(logical_path).parts[0]]

    def _persist_bytes(self, content):
        digest = hashlib.sha256(content).hexdigest()
        destination = self.files_root / digest
        self._atomic_content(destination, content)
        return destination

    @staticmethod
    def _atomic_content(destination, content):
        if destination.exists():
            existing = _read_regular(destination, maximum=max(1, len(content)))
            if existing != content:
                raise DesktopProfileError("Desktop profile content collided.")
            return
        descriptor = None
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".profile-",
                dir=str(destination.parent),
            )
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, destination)
            temporary = None
            os.chmod(destination, 0o600)
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise DesktopProfileError("Desktop profile could not be persisted.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _artifact(self, logical_path, content):
        kind = self._kind(logical_path)
        _validate_logical_path(logical_path, kind)
        if len(content) > MAX_PROFILE_FILE_BYTES:
            raise DesktopProfileError("Desktop profile file is too large.")
        digest = hashlib.sha256(content).hexdigest()
        return ProfileArtifact(
            logical_path=logical_path,
            kind=kind,
            size_bytes=len(content),
            sha256=digest,
            private_path=self._persist_bytes(content),
        )

    def _sanitize_settings(self, value, input_root, assets, *, depth=0):
        if depth > MAX_JSON_DEPTH:
            raise DesktopProfileError("Desktop profile settings are invalid.")
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if not isinstance(key, str) or _SUSPICIOUS_KEY.search(key):
                    continue
                sanitized = self._sanitize_settings(
                    child,
                    input_root,
                    assets,
                    depth=depth + 1,
                )
                if sanitized is not _DROP:
                    result[key] = sanitized
            return result
        if isinstance(value, list):
            result = []
            for child in value:
                sanitized = self._sanitize_settings(
                    child,
                    input_root,
                    assets,
                    depth=depth + 1,
                )
                if sanitized is not _DROP:
                    result.append(sanitized)
            return result
        if isinstance(value, str):
            if _SUSPICIOUS_VALUE.search(value):
                return _DROP
            candidate = Path(value)
            if not candidate.is_absolute():
                return value
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                return _DROP
            suffix = resolved.suffix.casefold()
            if not _within(resolved, input_root) or suffix not in _IMAGE_SUFFIXES:
                return _DROP
            content = _read_regular(resolved, maximum=MAX_PROFILE_FILE_BYTES)
            digest = hashlib.sha256(content).hexdigest()
            logical = "backgrounds/" + digest + suffix
            existing = assets.get(logical)
            if existing is not None and existing != content:
                raise DesktopProfileError("Desktop profile asset collided.")
            assets[logical] = content
            return (
                "/api/view?filename=cloud-vast/backgrounds/"
                + digest
                + suffix
                + "&type=input"
            )
        if isinstance(value, float) and not math.isfinite(value):
            return _DROP
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _DROP

    def _archive(self, artifacts):
        raw = io.BytesIO()
        try:
            with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for item in artifacts:
                    content = _read_regular(
                        item.private_path,
                        maximum=MAX_PROFILE_FILE_BYTES,
                    )
                    if (
                        len(content) != item.size_bytes
                        or hashlib.sha256(content).hexdigest() != item.sha256
                    ):
                        raise DesktopProfileError("Desktop profile content changed.")
                    info = tarfile.TarInfo(item.logical_path)
                    info.size = len(content)
                    info.mode = 0o600
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(content))
            compressed = io.BytesIO()
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=compressed,
                compresslevel=9,
                mtime=0,
            ) as stream:
                stream.write(raw.getvalue())
            content = compressed.getvalue()
        except (OSError, tarfile.TarError):
            raise DesktopProfileError("Desktop profile archive failed.") from None
        if not 0 < len(content) <= MAX_PROFILE_ARCHIVE_BYTES:
            raise DesktopProfileError("Desktop profile archive is invalid.")
        digest = hashlib.sha256(content).hexdigest()
        path = self.archives_root / (digest + ".tar.gz")
        self._atomic_content(path, content)
        return path, len(content), digest

    def _profile_from_record(self, record):
        if record is None:
            return None
        artifacts = []
        for item in record["artifacts"]:
            if not isinstance(item, dict) or set(item) != {
                "logical_path",
                "kind",
                "size_bytes",
                "sha256",
                "private_path",
            }:
                raise DesktopProfileError("Stored Desktop profile is invalid.")
            artifacts.append(
                ProfileArtifact(
                    logical_path=item["logical_path"],
                    kind=item["kind"],
                    size_bytes=item["size_bytes"],
                    sha256=item["sha256"],
                    private_path=Path(item["private_path"]),
                )
            )
        return DesktopProfile(
            profile_id=record["profile_id"],
            revision=record["revision"],
            base_revision=record["base_revision"],
            bootstrap_digest=record["bootstrap_digest"],
            archive_size_bytes=record["archive_size_bytes"],
            archive_sha256=record["archive_sha256"],
            artifacts=tuple(artifacts),
            ui_packages=tuple(
                _ui_package_from_record(item) for item in record["ui_packages"]
            ),
            archive_private_path=Path(record["archive_path"]),
        )

    def _record(self, profile, *, source):
        record = self.repository.save_profile_revision(
            {
                "profile_id": profile.profile_id,
                "revision": profile.revision,
                "base_revision": profile.base_revision,
                "bootstrap_digest": profile.bootstrap_digest,
                "archive_path": str(profile.archive_private_path),
                "archive_size_bytes": profile.archive_size_bytes,
                "archive_sha256": profile.archive_sha256,
                "artifacts": [
                    {
                        "logical_path": item.logical_path,
                        "kind": item.kind,
                        "size_bytes": item.size_bytes,
                        "sha256": item.sha256,
                        "private_path": str(item.private_path),
                    }
                    for item in profile.artifacts
                ],
                "ui_packages": [
                    _ui_package_record(item) for item in profile.ui_packages
                ],
                "source": source,
                "created_at": self._now(),
            }
        )
        stored = self._profile_from_record(record)
        spec = stored.manifest_spec()
        self.repository.register_local_artifact(
            ResolvedLocalArtifact(
                artifact_id=spec.archive.artifact_id,
                private_path=str(stored.archive_private_path),
                size_bytes=stored.archive_size_bytes,
                sha256=stored.archive_sha256,
            ),
            created_at=self._now(),
        )
        return stored

    def latest(self):
        return self._profile_from_record(
            self.repository.latest_profile_revision(self.profile_id)
        )

    def archive_path(self, profile):
        if not isinstance(profile, DesktopProfile) or profile.profile_id != self.profile_id:
            raise DesktopProfileError("Desktop profile identity is invalid.")
        return profile.archive_private_path

    def capture(
        self,
        *,
        user_root,
        profile_name,
        input_root,
        bootstrap_workflow,
        ui_packages=(),
        ui_assets=(),
    ):
        if profile_name != "default" or not isinstance(ui_packages, tuple):
            raise DesktopProfileError("Desktop profile capture is invalid.")
        try:
            for package in ui_packages:
                validate_dependency(package)
        except (TypeError, ValueError):
            raise DesktopProfileError("Desktop profile capture is invalid.") from None
        try:
            user_root = Path(user_root).resolve(strict=True)
            input_root = Path(input_root).resolve(strict=True)
            profile_root = (user_root / profile_name).resolve(strict=True)
        except (OSError, RuntimeError):
            raise DesktopProfileError("Desktop profile root is unavailable.") from None
        if not _within(profile_root, user_root):
            raise DesktopProfileError("Desktop profile root is unavailable.")
        content = {}
        agent_panel_enabled = any(
            package.package_id == _AGENT_PANEL_PACKAGE_ID
            for package in ui_packages
        )
        workflows = profile_root / "workflows"
        palettes = profile_root / "color_palettes"
        for root, prefix in ((workflows, "workflows"), (palettes, "palettes")):
            if not root.exists():
                continue
            try:
                files = sorted(root.rglob("*.json"))
            except OSError:
                raise DesktopProfileError("Desktop profile files are unavailable.") from None
            if len(files) + len(content) > MAX_PROFILE_FILES:
                raise DesktopProfileError("Desktop profile has too many files.")
            for path in files:
                try:
                    relative = path.relative_to(root).as_posix()
                except ValueError:
                    raise DesktopProfileError("Desktop profile path is invalid.") from None
                logical = prefix + "/" + relative
                _validate_logical_path(logical, self._kind(logical))
                value = _read_json(path)
                if prefix == "workflows" and self._contains_secret_key(value):
                    raise DesktopProfileError("Desktop profile workflow is unsafe.")
                content[logical] = _canonical_json(value)
        assets = {}
        settings_path = profile_root / "comfy.settings.json"
        sanitized = None
        if settings_path.exists():
            settings = _read_json(settings_path)
            sanitized = self._sanitize_settings(settings, input_root, assets)
        elif agent_panel_enabled:
            sanitized = {}
        if sanitized is not None:
            if agent_panel_enabled:
                if not isinstance(sanitized, dict):
                    raise DesktopProfileError(
                        "Desktop profile settings are invalid."
                    )
                sanitized[_AGENT_PANEL_BRIDGE_SETTING] = (
                    _AGENT_PANEL_BRIDGE_PATH
                )
                sanitized[_AGENT_PANEL_REMOTE_SETTING] = ""
            content["settings/comfy.settings.json"] = _canonical_json(sanitized)
        content.update(assets)
        bootstrap = _canonical_json(bootstrap_workflow)
        if self._contains_secret_key(bootstrap_workflow):
            raise DesktopProfileError("Desktop profile bootstrap is unsafe.")
        content["bootstrap/current.json"] = bootstrap
        try:
            asset_pairs = tuple(ui_assets)
        except TypeError:
            raise DesktopProfileError("Desktop profile UI assets are invalid.") from None
        for logical, path in asset_pairs:
            _validate_logical_path(logical, "ui_asset")
            data = _read_regular(path, maximum=MAX_PROFILE_FILE_BYTES)
            if logical in content and content[logical] != data:
                raise DesktopProfileError("Desktop profile asset collided.")
            content[logical] = data
        artifacts = tuple(
            self._artifact(logical, data)
            for logical, data in sorted(content.items())
        )
        archive_path, archive_size, archive_digest = self._archive(artifacts)
        bootstrap_digest = hashlib.sha256(bootstrap).hexdigest()
        latest = self.latest()
        if latest is not None and (
            latest.archive_sha256 == archive_digest
            and latest.bootstrap_digest == bootstrap_digest
            and latest.ui_packages == ui_packages
        ):
            return latest
        revision = 1 if latest is None else latest.revision + 1
        profile = DesktopProfile(
            profile_id=self.profile_id,
            revision=revision,
            base_revision=None if latest is None else latest.revision,
            bootstrap_digest=bootstrap_digest,
            archive_size_bytes=archive_size,
            archive_sha256=archive_digest,
            artifacts=artifacts,
            ui_packages=ui_packages,
            archive_private_path=archive_path,
        )
        return self._record(profile, source="local")

    @staticmethod
    def _contains_secret_key(value):
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str) or _SUSPICIOUS_KEY.search(key):
                        return True
                    stack.append(child)
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and _SUSPICIOUS_VALUE.search(item):
                return True
        return False

    def _adopt(self, source, *, revision, base_revision):
        archive = _read_regular(
            source.archive_private_path,
            maximum=MAX_PROFILE_ARCHIVE_BYTES,
        )
        if (
            len(archive) != source.archive_size_bytes
            or hashlib.sha256(archive).hexdigest() != source.archive_sha256
        ):
            raise DesktopProfileError("Remote Desktop profile is invalid.")
        archive_path = self.archives_root / (source.archive_sha256 + ".tar.gz")
        self._atomic_content(archive_path, archive)
        expected = {item.logical_path: item for item in source.artifacts}
        adopted = []
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                members = bundle.getmembers()
                if [item.name for item in members] != sorted(expected):
                    raise DesktopProfileError("Remote Desktop profile is invalid.")
                for member in members:
                    item = expected.get(member.name)
                    if (
                        item is None
                        or not member.isfile()
                        or member.size != item.size_bytes
                        or member.size > MAX_PROFILE_FILE_BYTES
                    ):
                        raise DesktopProfileError("Remote Desktop profile is invalid.")
                    stream = bundle.extractfile(member)
                    data = stream.read(MAX_PROFILE_FILE_BYTES + 1) if stream else b""
                    if (
                        len(data) != item.size_bytes
                        or hashlib.sha256(data).hexdigest() != item.sha256
                    ):
                        raise DesktopProfileError("Remote Desktop profile is invalid.")
                    adopted.append(self._artifact(item.logical_path, data))
        except DesktopProfileError:
            raise
        except (OSError, tarfile.TarError):
            raise DesktopProfileError("Remote Desktop profile is invalid.") from None
        return DesktopProfile(
            profile_id=self.profile_id,
            revision=revision,
            base_revision=base_revision,
            bootstrap_digest=source.bootstrap_digest,
            archive_size_bytes=source.archive_size_bytes,
            archive_sha256=source.archive_sha256,
            artifacts=tuple(adopted),
            ui_packages=source.ui_packages,
            archive_private_path=archive_path,
        )

    def apply_remote_snapshot(self, profile):
        if not isinstance(profile, DesktopProfile) or profile.profile_id != self.profile_id:
            raise DesktopProfileError("Remote Desktop profile is invalid.")
        latest = self.latest()
        if latest is not None and latest.archive_sha256 == profile.archive_sha256:
            return latest
        if latest is None:
            if profile.base_revision is not None:
                raise DesktopProfileError("Remote Desktop profile base is invalid.")
            return self._record(
                self._adopt(profile, revision=1, base_revision=None),
                source="remote",
            )
        imported = self._record(
            self._adopt(
                profile,
                revision=latest.revision + 1,
                base_revision=latest.revision,
            ),
            source="remote",
        )
        if profile.base_revision == latest.revision:
            return imported
        if (
            profile.base_revision is None
            or profile.base_revision >= latest.revision
            or self.repository.get_profile_revision(
                self.profile_id,
                profile.base_revision,
            )
            is None
        ):
            raise DesktopProfileError("Remote Desktop profile base is invalid.")
        material = (
            self.profile_id
            + ":"
            + str(profile.base_revision)
            + ":"
            + str(latest.revision)
            + ":"
            + str(imported.revision)
        ).encode("ascii")
        conflict_id = "conflict-" + hashlib.sha256(material).hexdigest()[:32]
        record = self.repository.save_profile_conflict(
            {
                "conflict_id": conflict_id,
                "profile_id": self.profile_id,
                "base_revision": profile.base_revision,
                "local_revision": latest.revision,
                "remote_revision": imported.revision,
                "resolved_revision": None,
                "created_at": self._now(),
            }
        )
        return self._conflict(record)

    def apply_remote_payload(self, payload, archive):
        fields = {
            "profile_id",
            "revision",
            "base_revision",
            "bootstrap_digest",
            "archive_size_bytes",
            "archive_sha256",
            "archive_artifact_id",
            "artifacts",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != fields
            or not isinstance(archive, bytes)
            or not isinstance(payload.get("artifacts"), list)
            or payload.get("archive_artifact_id")
            != "profile-" + str(payload.get("archive_sha256") or "")
            or len(archive) != payload.get("archive_size_bytes")
            or hashlib.sha256(archive).hexdigest()
            != payload.get("archive_sha256")
        ):
            raise DesktopProfileError("Remote Desktop profile is invalid.")
        archive_path = self.archives_root / (
            payload["archive_sha256"] + ".tar.gz"
        )
        self._atomic_content(archive_path, archive)
        artifacts = []
        for item in payload["artifacts"]:
            if not isinstance(item, dict) or set(item) != {
                "path",
                "kind",
                "size_bytes",
                "sha256",
            }:
                raise DesktopProfileError("Remote Desktop profile is invalid.")
            artifacts.append(
                ProfileArtifact(
                    logical_path=item["path"],
                    kind=item["kind"],
                    size_bytes=item["size_bytes"],
                    sha256=item["sha256"],
                    private_path=archive_path,
                )
            )
        if tuple(item.logical_path for item in artifacts) != tuple(
            sorted(item.logical_path for item in artifacts)
        ):
            raise DesktopProfileError("Remote Desktop profile is invalid.")
        latest = self.latest()
        remote = DesktopProfile(
            profile_id=payload["profile_id"],
            revision=payload["revision"],
            base_revision=payload["base_revision"],
            bootstrap_digest=payload["bootstrap_digest"],
            archive_size_bytes=payload["archive_size_bytes"],
            archive_sha256=payload["archive_sha256"],
            artifacts=tuple(artifacts),
            ui_packages=(latest.ui_packages if latest is not None else ()),
            archive_private_path=archive_path,
        )
        bootstrap = next(
            (
                item
                for item in artifacts
                if item.logical_path == "bootstrap/current.json"
            ),
            None,
        )
        if bootstrap is None or bootstrap.sha256 != remote.bootstrap_digest:
            raise DesktopProfileError("Remote Desktop profile is invalid.")
        return self.apply_remote_snapshot(remote)

    def conflicts(self, *, unresolved_only=False):
        try:
            records = self.repository.list_profile_conflicts(
                self.profile_id,
                unresolved_only=unresolved_only,
            )
        except (TypeError, ValueError):
            raise DesktopProfileError(
                "Desktop profile conflicts are unavailable."
            ) from None
        return tuple(self._conflict(record) for record in records)

    @staticmethod
    def _conflict(record):
        return ProfileConflict(
            conflict_id=record["conflict_id"],
            profile_id=record["profile_id"],
            base_revision=record["base_revision"],
            local_revision=record["local_revision"],
            remote_revision=record["remote_revision"],
            resolved_revision=record["resolved_revision"],
        )

    def resolve_conflict(self, conflict_id, *, winner):
        if winner not in {"local", "cloud_vast"}:
            raise DesktopProfileError("Desktop profile conflict choice is invalid.")
        record = self.repository.get_profile_conflict(conflict_id)
        if record is None or record["resolved_revision"] is not None:
            raise DesktopProfileError("Desktop profile conflict is unavailable.")
        selected_revision = (
            record["local_revision"]
            if winner == "local"
            else record["remote_revision"]
        )
        selected = self._profile_from_record(
            self.repository.get_profile_revision(self.profile_id, selected_revision)
        )
        latest = self.latest()
        resolved = self._record(
            self._adopt(
                selected,
                revision=latest.revision + 1,
                base_revision=latest.revision,
            ),
            source="resolution",
        )
        self.repository.resolve_profile_conflict(
            conflict_id,
            resolved.revision,
        )
        return resolved

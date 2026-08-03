"""Safe managed profile state for the pod's ComfyUI user interface."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile

if "." in (__package__ or ""):
    from ..cloud_run.manifest import (
        ArtifactSpec,
        ProfileFileSpec,
        ProfileSpec,
        SourceSpec,
        validate_dependency,
    )
else:
    from cloud_run.manifest import (
        ArtifactSpec,
        ProfileFileSpec,
        ProfileSpec,
        SourceSpec,
        validate_dependency,
    )


MAX_PROFILE_FILE_BYTES = 16 * 1024 * 1024
MAX_PROFILE_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_PROFILE_FILES = 2_000
MAX_PROFILE_REQUEST_BYTES = 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_ROOT_KINDS = {
    "workflows": "workflow",
    "bootstrap": "bootstrap_workflow",
    "settings": "settings",
    "palettes": "palette",
    "backgrounds": "background",
    "assets": "ui_asset",
}
_JSON_KINDS = {"workflow", "bootstrap_workflow", "settings", "palette"}
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
_UI_SUFFIXES = {".css", ".jpg", ".jpeg", ".png", ".webp"}
_SUSPICIOUS_KEY = re.compile(
    r"(?i)(authorization|bearer|cookie|credential|password|secret|token|api[_ -]?key)"
)


class ProfileError(RuntimeError):
    """The managed worker profile failed closed."""


@dataclass(frozen=True, repr=False)
class WorkerProfileSnapshot:
    profile_id: str
    revision: int
    base_revision: int | None
    bootstrap_digest: str
    archive_size_bytes: int
    archive_sha256: str
    artifacts: tuple[dict, ...]
    archive_path: Path = field(repr=False)

    def public_payload(self):
        return {
            "profile_id": self.profile_id,
            "revision": self.revision,
            "base_revision": self.base_revision,
            "bootstrap_digest": self.bootstrap_digest,
            "archive_size_bytes": self.archive_size_bytes,
            "archive_sha256": self.archive_sha256,
            "archive_artifact_id": "profile-" + self.archive_sha256,
            "artifacts": [dict(item) for item in self.artifacts],
        }


@dataclass(frozen=True, repr=False)
class WorkerProfileArtifact:
    path: Path = field(repr=False)
    size_bytes: int
    sha256: str
    mime_type: str


def _logical_kind(value):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ProfileError("Worker profile path is invalid.")
    relative = PurePosixPath(value)
    folded = tuple(part.casefold() for part in relative.parts)
    kind = _ROOT_KINDS.get(relative.parts[0] if relative.parts else "")
    if (
        kind is None
        or str(relative) != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part in _FORBIDDEN_NAMES for part in folded)
        or relative.suffix.casefold() in _FORBIDDEN_SUFFIXES
        or (
            kind == "background"
            and relative.suffix.casefold() not in _IMAGE_SUFFIXES
        )
        or kind == "ui_asset"
        and relative.suffix.casefold() not in _UI_SUFFIXES
    ):
        raise ProfileError("Worker profile path is invalid.")
    return kind


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _exact_record(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ProfileError("Worker profile request is invalid.")
    return value


def _source_from_record(record):
    record = _exact_record(
        record,
        {"kind", "locator", "immutable_revision"},
    )
    return SourceSpec(
        kind=record["kind"],
        locator=record["locator"],
        immutable_revision=record["immutable_revision"],
    )


def _artifact_from_record(record):
    record = _exact_record(
        record,
        {
            "artifact_id",
            "kind",
            "logical_name",
            "destination",
            "size_bytes",
            "sha256",
            "source",
        },
    )
    return ArtifactSpec(
        artifact_id=record["artifact_id"],
        kind=record["kind"],
        logical_name=record["logical_name"],
        destination=record["destination"],
        size_bytes=record["size_bytes"],
        sha256=record["sha256"],
        source=_source_from_record(record["source"]),
    )


def _profile_from_record(record):
    record = _exact_record(
        record,
        {
            "profile_id",
            "revision",
            "archive",
            "bootstrap_digest",
            "files",
        },
    )
    if not isinstance(record["files"], list):
        raise ProfileError("Worker profile request is invalid.")
    files = []
    for item in record["files"]:
        item = _exact_record(item, {"path", "size_bytes", "sha256"})
        files.append(
            ProfileFileSpec(
                path=item["path"],
                size_bytes=item["size_bytes"],
                sha256=item["sha256"],
            )
        )
    profile = ProfileSpec(
        profile_id=record["profile_id"],
        revision=record["revision"],
        archive=_artifact_from_record(record["archive"]),
        bootstrap_digest=record["bootstrap_digest"],
        files=tuple(files),
    )
    try:
        validate_dependency(profile)
    except (TypeError, ValueError):
        raise ProfileError("Worker profile request is invalid.") from None
    return profile


def parse_profile_request(body):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= MAX_PROFILE_REQUEST_BYTES
    ):
        raise ProfileError("Worker profile request is invalid.")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ProfileError("Worker profile request is invalid.") from None
    payload = _exact_record(payload, {"profile"})
    return _profile_from_record(payload["profile"])


def _validate_json(content, *, settings=False):
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ProfileError("Worker profile JSON is invalid.") from None
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            raise ProfileError("Worker profile JSON is invalid.")
        if isinstance(item, dict):
            for key, child in item.items():
                if _SUSPICIOUS_KEY.search(key):
                    raise ProfileError("Worker profile JSON is unsafe.")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ProfileError("Worker profile JSON is invalid.")
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ProfileError("Worker profile JSON is invalid.")
    if settings and not isinstance(value, dict):
        raise ProfileError("Worker profile settings are invalid.")


def _read_regular(path, maximum):
    descriptor = None
    try:
        path = Path(path)
        metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or not 0 <= metadata.st_size <= maximum
        ):
            raise OSError("unsafe file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
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
        raise ProfileError("Worker profile content is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _atomic_write(destination, content):
    destination = Path(destination)
    descriptor = None
    temporary = None
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = destination.parent
        while True:
            metadata = os.lstat(current)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise OSError("unsafe parent")
            if current == current.parent:
                break
            current = current.parent
        if destination.exists() and destination.is_symlink():
            raise OSError("unsafe destination")
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
        parent = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    except OSError:
        raise ProfileError("Worker profile could not be persisted.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _archive_bytes(records):
    raw = io.BytesIO()
    try:
        with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as bundle:
            for logical, content in sorted(records.items()):
                info = tarfile.TarInfo(logical)
                info.size = len(content)
                info.mode = 0o600
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                bundle.addfile(info, io.BytesIO(content))
        compressed = io.BytesIO()
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=compressed,
            compresslevel=9,
            mtime=0,
        ) as stream:
            stream.write(raw.getvalue())
        result = compressed.getvalue()
    except (OSError, tarfile.TarError):
        raise ProfileError("Worker profile archive failed.") from None
    if not 0 < len(result) <= MAX_PROFILE_ARCHIVE_BYTES:
        raise ProfileError("Worker profile archive is invalid.")
    return result


class ProfileStore:
    def __init__(
        self,
        *,
        state_root,
        user_root,
        input_root,
        custom_nodes_root,
        archive_resolver,
        clock=None,
    ):
        if not callable(archive_resolver):
            raise ProfileError("Worker profile archive resolver is unavailable.")
        self.state_root = Path(state_root)
        self.user_root = Path(user_root)
        self.input_root = Path(input_root)
        self.custom_nodes_root = Path(custom_nodes_root)
        self.archive_resolver = archive_resolver
        self.clock = clock or __import__("time").time
        try:
            for path in (
                self.state_root,
                self.user_root,
                self.input_root,
                self.custom_nodes_root,
            ):
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                metadata = os.lstat(path)
                if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    raise OSError("unsafe profile root")
            self.state_root = self.state_root.resolve(strict=True)
            self.user_root = self.user_root.resolve(strict=True)
            self.input_root = self.input_root.resolve(strict=True)
            self.custom_nodes_root = self.custom_nodes_root.resolve(strict=True)
            self.snapshots_root = self.state_root / "snapshots"
            self.snapshots_root.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self.snapshots_root, 0o700)
            self.snapshots_root = self.snapshots_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ProfileError("Worker profile roots are unavailable.") from None
        self.state_path = self.state_root / "profile-state.json"

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ProfileError("Worker profile clock is unavailable.")
        return float(value)

    def _destination(self, logical):
        relative = PurePosixPath(logical)
        kind = _logical_kind(logical)
        tail = relative.parts[1:]
        if kind == "workflow":
            destination = self.user_root / "workflows" / "cloud-vast" / Path(*tail)
        elif kind == "settings":
            if tail != ("comfy.settings.json",):
                raise ProfileError("Worker profile settings path is invalid.")
            destination = self.user_root / "comfy.settings.json"
        elif kind == "palette":
            destination = self.user_root / "color_palettes" / "cloud-vast" / Path(*tail)
        elif kind == "background":
            destination = self.input_root / "cloud-vast" / "backgrounds" / Path(*tail)
        else:
            destination = self.user_root / "cloud-vast-profile" / relative
        return destination

    def _state(self):
        if not self.state_path.exists():
            return None
        content = _read_regular(self.state_path, MAX_PROFILE_FILE_BYTES)
        try:
            state = json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise ProfileError("Worker profile state is invalid.") from None
        required = {
            "schema_version",
            "profile_id",
            "revision",
            "base_revision",
            "bootstrap_digest",
            "archive_size_bytes",
            "archive_sha256",
            "archive_path",
            "artifacts",
            "bootstrap_loaded_at_revision",
            "updated_at",
        }
        if (
            not isinstance(state, dict)
            or set(state) != required
            or state["schema_version"] != 1
            or not isinstance(state["artifacts"], list)
        ):
            raise ProfileError("Worker profile state is invalid.")
        revision = state["revision"]
        base_revision = state["base_revision"]
        loaded = state["bootstrap_loaded_at_revision"]
        archive_path = Path(str(state["archive_path"] or ""))
        if (
            not isinstance(state["profile_id"], str)
            or _IDENTIFIER.fullmatch(state["profile_id"]) is None
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
            or (
                base_revision is not None
                and (
                    isinstance(base_revision, bool)
                    or not isinstance(base_revision, int)
                    or not 0 < base_revision < revision
                )
            )
            or (revision == 1) != (base_revision is None)
            or not isinstance(state["bootstrap_digest"], str)
            or _HEX_64.fullmatch(state["bootstrap_digest"]) is None
            or isinstance(state["archive_size_bytes"], bool)
            or not isinstance(state["archive_size_bytes"], int)
            or not 0 < state["archive_size_bytes"] <= MAX_PROFILE_ARCHIVE_BYTES
            or not isinstance(state["archive_sha256"], str)
            or _HEX_64.fullmatch(state["archive_sha256"]) is None
            or archive_path.parent != self.snapshots_root
            or archive_path.name != state["archive_sha256"] + ".tar.gz"
            or (
                loaded is not None
                and (
                    isinstance(loaded, bool)
                    or not isinstance(loaded, int)
                    or not 0 < loaded <= revision
                )
            )
            or isinstance(state["updated_at"], bool)
            or not isinstance(state["updated_at"], (int, float))
            or not math.isfinite(state["updated_at"])
            or state["updated_at"] < 0
            or not 0 < len(state["artifacts"]) <= MAX_PROFILE_FILES
        ):
            raise ProfileError("Worker profile state is invalid.")
        paths = []
        for item in state["artifacts"]:
            if not isinstance(item, dict) or set(item) != {
                "path",
                "kind",
                "size_bytes",
                "sha256",
                "destination",
            }:
                raise ProfileError("Worker profile state is invalid.")
            try:
                kind = _logical_kind(item["path"])
                expected_destination = self._destination(item["path"])
            except (ProfileError, TypeError, ValueError):
                raise ProfileError("Worker profile state is invalid.") from None
            if (
                item["kind"] != kind
                or item["destination"] != str(expected_destination)
                or isinstance(item["size_bytes"], bool)
                or not isinstance(item["size_bytes"], int)
                or not 0 <= item["size_bytes"] <= MAX_PROFILE_FILE_BYTES
                or not isinstance(item["sha256"], str)
                or _HEX_64.fullmatch(item["sha256"]) is None
            ):
                raise ProfileError("Worker profile state is invalid.")
            paths.append(item["path"])
        if paths != sorted(set(paths)):
            raise ProfileError("Worker profile state is invalid.")
        bootstrap = next(
            (
                item
                for item in state["artifacts"]
                if item["path"] == "bootstrap/current.json"
            ),
            None,
        )
        if (
            bootstrap is None
            or bootstrap["sha256"] != state["bootstrap_digest"]
        ):
            raise ProfileError("Worker profile state is invalid.")
        return state

    def _save_state(self, state):
        try:
            content = json.dumps(
                state,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ProfileError("Worker profile state is invalid.") from None
        _atomic_write(self.state_path, content)

    def _snapshot_from_state(self, state):
        return WorkerProfileSnapshot(
            profile_id=state["profile_id"],
            revision=state["revision"],
            base_revision=state["base_revision"],
            bootstrap_digest=state["bootstrap_digest"],
            archive_size_bytes=state["archive_size_bytes"],
            archive_sha256=state["archive_sha256"],
            artifacts=tuple(
                {
                    "path": item["path"],
                    "kind": item["kind"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                }
                for item in state["artifacts"]
            ),
            archive_path=Path(state["archive_path"]),
        )

    def _store_archive(self, content):
        digest = hashlib.sha256(content).hexdigest()
        destination = self.snapshots_root / (digest + ".tar.gz")
        if destination.exists():
            existing = _read_regular(destination, MAX_PROFILE_ARCHIVE_BYTES)
            if existing != content:
                raise ProfileError("Worker profile archive collided.")
        else:
            _atomic_write(destination, content)
        return destination, digest

    def apply(self, profile):
        try:
            validate_dependency(profile)
        except (TypeError, ValueError):
            raise ProfileError("Worker profile descriptor is invalid.") from None
        if not isinstance(profile, ProfileSpec):
            raise ProfileError("Worker profile descriptor is invalid.")
        current = self._state()
        if current is not None:
            if profile.profile_id != current["profile_id"]:
                raise ProfileError("Worker profile identity changed.")
            if profile.revision < current["revision"]:
                raise ProfileError("Worker profile revision regressed.")
            if profile.revision == current["revision"]:
                if hmac.compare_digest(
                    profile.archive.sha256,
                    current["archive_sha256"],
                ):
                    return self._apply_payload(current)
                raise ProfileError("Worker profile revision changed.")
        try:
            archive_path = Path(self.archive_resolver(profile))
        except Exception:
            raise ProfileError("Worker profile archive is unavailable.") from None
        archive = _read_regular(archive_path, MAX_PROFILE_ARCHIVE_BYTES)
        if (
            len(archive) != profile.archive.size_bytes
            or not hmac.compare_digest(
                hashlib.sha256(archive).hexdigest(),
                profile.archive.sha256,
            )
        ):
            raise ProfileError("Worker profile archive is invalid.")
        expected = {item.path: item for item in profile.files}
        if len(expected) != len(profile.files) or len(expected) > MAX_PROFILE_FILES:
            raise ProfileError("Worker profile file list is invalid.")
        records = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                members = bundle.getmembers()
                if [member.name for member in members] != sorted(expected):
                    raise ProfileError("Worker profile archive is invalid.")
                for member in members:
                    spec = expected.get(member.name)
                    kind = _logical_kind(member.name)
                    if (
                        spec is None
                        or not member.isfile()
                        or member.size != spec.size_bytes
                        or member.size > MAX_PROFILE_FILE_BYTES
                    ):
                        raise ProfileError("Worker profile archive is invalid.")
                    stream = bundle.extractfile(member)
                    content = stream.read(MAX_PROFILE_FILE_BYTES + 1) if stream else b""
                    if (
                        len(content) != spec.size_bytes
                        or not hmac.compare_digest(
                            hashlib.sha256(content).hexdigest(),
                            spec.sha256,
                        )
                    ):
                        raise ProfileError("Worker profile archive is invalid.")
                    if kind in _JSON_KINDS:
                        _validate_json(content, settings=kind == "settings")
                    records[member.name] = content
        except ProfileError:
            raise
        except (OSError, tarfile.TarError):
            raise ProfileError("Worker profile archive is invalid.") from None
        managed = []
        for logical, content in sorted(records.items()):
            destination = self._destination(logical)
            _atomic_write(destination, content)
            managed.append(
                {
                    "path": logical,
                    "kind": _logical_kind(logical),
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "destination": str(destination),
                }
            )
        stored_archive, archive_digest = self._store_archive(archive)
        prior_revision = (
            current["revision"]
            if current is not None
            else (profile.revision - 1 if profile.revision > 1 else None)
        )
        state = {
            "schema_version": 1,
            "profile_id": profile.profile_id,
            "revision": profile.revision,
            "base_revision": prior_revision,
            "bootstrap_digest": profile.bootstrap_digest,
            "archive_size_bytes": len(archive),
            "archive_sha256": archive_digest,
            "archive_path": str(stored_archive),
            "artifacts": managed,
            "bootstrap_loaded_at_revision": (
                current["bootstrap_loaded_at_revision"]
                if current is not None
                else None
            ),
            "updated_at": self._now(),
        }
        self._save_state(state)
        return self._apply_payload(state)

    @staticmethod
    def _apply_payload(state):
        return {
            "profile_id": state["profile_id"],
            "revision": state["revision"],
            "bootstrap_digest": state["bootstrap_digest"],
            "bootstrap_loaded_at_revision": state[
                "bootstrap_loaded_at_revision"
            ],
        }

    def snapshot(self, after_revision):
        if (
            isinstance(after_revision, bool)
            or not isinstance(after_revision, int)
            or after_revision < 0
        ):
            raise ProfileError("Worker profile cursor is invalid.")
        state = self._state()
        if state is None:
            return None
        records = {}
        artifacts = []
        changed = False
        for item in state["artifacts"]:
            logical = item["path"]
            kind = _logical_kind(logical)
            content = _read_regular(item["destination"], MAX_PROFILE_FILE_BYTES)
            digest = hashlib.sha256(content).hexdigest()
            if kind in _JSON_KINDS:
                _validate_json(content, settings=kind == "settings")
            if len(content) != item["size_bytes"] or digest != item["sha256"]:
                changed = True
            records[logical] = content
            artifacts.append(
                {
                    "path": logical,
                    "kind": kind,
                    "size_bytes": len(content),
                    "sha256": digest,
                    "destination": item["destination"],
                }
            )
        if changed:
            archive = _archive_bytes(records)
            archive_path, archive_digest = self._store_archive(archive)
            previous = state["revision"]
            bootstrap = records.get("bootstrap/current.json")
            if bootstrap is None:
                raise ProfileError("Worker profile bootstrap is unavailable.")
            state = {
                **state,
                "revision": previous + 1,
                "base_revision": previous,
                "bootstrap_digest": hashlib.sha256(bootstrap).hexdigest(),
                "archive_size_bytes": len(archive),
                "archive_sha256": archive_digest,
                "archive_path": str(archive_path),
                "artifacts": artifacts,
                "updated_at": self._now(),
            }
            self._save_state(state)
        if state["revision"] <= after_revision:
            return None
        return self._snapshot_from_state(state)

    def artifact(self, profile_id, path):
        if (
            not isinstance(profile_id, str)
            or _IDENTIFIER.fullmatch(profile_id) is None
            or not isinstance(path, str)
        ):
            raise ProfileError("Worker profile artifact identity is invalid.")
        state = self._state()
        if state is None or state["profile_id"] != profile_id:
            return None
        archive_id = "profile-" + state["archive_sha256"]
        if path == archive_id:
            return WorkerProfileArtifact(
                path=Path(state["archive_path"]),
                size_bytes=state["archive_size_bytes"],
                sha256=state["archive_sha256"],
                mime_type="application/gzip",
            )
        match = next((item for item in state["artifacts"] if item["path"] == path), None)
        if match is None:
            return None
        return WorkerProfileArtifact(
            path=Path(match["destination"]),
            size_bytes=match["size_bytes"],
            sha256=match["sha256"],
            mime_type=(
                "application/json"
                if match["kind"] in _JSON_KINDS
                else "application/octet-stream"
            ),
        )

    def should_load_bootstrap(self, revision, *, remote_edit_revision):
        state = self._state()
        if state is None:
            return False
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or isinstance(remote_edit_revision, bool)
            or not isinstance(remote_edit_revision, int)
        ):
            raise ProfileError("Worker profile bootstrap revision is invalid.")
        loaded = state["bootstrap_loaded_at_revision"]
        return bool(
            revision == state["revision"]
            and remote_edit_revision <= revision
            and (loaded is None or loaded < revision)
        )

    def mark_bootstrap_loaded(self, revision):
        state = self._state()
        if (
            state is None
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision != state["revision"]
        ):
            raise ProfileError("Worker profile bootstrap revision is invalid.")
        loaded = state["bootstrap_loaded_at_revision"]
        if loaded is not None and loaded >= revision:
            return self._apply_payload(state)
        state = {
            **state,
            "bootstrap_loaded_at_revision": revision,
            "updated_at": self._now(),
        }
        self._save_state(state)
        return self._apply_payload(state)

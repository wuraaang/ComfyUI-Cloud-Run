"""Fail-closed extraction and fixed-argv custom-node installation."""

from __future__ import annotations

import asyncio
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile

from cloud_run.manifest import (
    CustomNodeSpec,
    PythonWheelSpec,
    UiPackageSpec,
    validate_dependency,
)
from .transfers import TRANSFER_CHUNK_BYTES


MAX_ARCHIVE_MEMBERS = 100_000
_SAFE_UI_LOADER = (
    b"NODE_CLASS_MAPPINGS = {}\n"
    b"NODE_DISPLAY_NAME_MAPPINGS = {}\n"
    b'WEB_DIRECTORY = "./web"\n\n'
    b"__all__ = [\"NODE_CLASS_MAPPINGS\", "
    b'"NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]\n'
)
_UI_ROOT_FILES = frozenset({"LICENSE", "__init__.py"})
_UI_FORBIDDEN_EXECUTABLE_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".exe",
        ".ps1",
        ".py",
        ".pyc",
        ".pyo",
        ".sh",
    }
)
_SERVED_RELATIVE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+/-]*")
_PROTECTED_DISTRIBUTIONS = frozenset(
    {
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
    }
)


class InstallError(RuntimeError):
    """A sanitized archive, wheel, or installer failure."""


@dataclass(frozen=True)
class InstallResult:
    package_id: str
    revision: str
    destination: Path
    wheels: tuple[str, ...]
    extension_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class UiInstallResult:
    package_id: str
    revision: str
    destination: Path
    wheels: tuple[str, ...]
    web_sha256: str
    extension_paths: tuple[str, ...]


def _install_error():
    return InstallError("Custom-node installation was rejected.")


def _resolved_directory(path, message):
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(message) from None
    if not resolved.is_dir():
        raise ValueError(message)
    return resolved


def _within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _archive_name(value):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise _install_error()
    relative = PurePosixPath(value)
    if (
        str(relative) != value
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _install_error()
    return relative


def _member_kind(member):
    if member.isdir():
        if member.mode != 0o755 or member.size != 0:
            raise _install_error()
        return "directory"
    if member.isreg():
        if member.mode not in {0o644, 0o755} or member.size < 0:
            raise _install_error()
        return "file"
    raise _install_error()


def _validated_members(archive, archive_size):
    try:
        members = archive.getmembers()
    except (OSError, tarfile.TarError):
        raise _install_error() from None
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise _install_error()
    records = []
    kinds = {}
    total_size = 0
    for member in members:
        relative = _archive_name(member.name)
        if (
            member.uid != 0
            or member.gid != 0
            or member.uname not in {"", None}
            or member.gname not in {"", None}
            or member.mtime != 0
            or member.pax_headers
            or getattr(member, "sparse", None)
        ):
            raise _install_error()
        kind = _member_kind(member)
        name = relative.as_posix()
        if name in kinds:
            raise _install_error()
        for parent in relative.parents:
            if str(parent) == ".":
                continue
            if kinds.get(parent.as_posix()) == "file":
                raise _install_error()
        if kind == "file":
            prefix = name + "/"
            if any(existing.startswith(prefix) for existing in kinds):
                raise _install_error()
            total_size += member.size
            if total_size > archive_size:
                raise _install_error()
        kinds[name] = kind
        records.append((relative, member, kind))
    return tuple(records)


def _safe_remove_tree(path, allowed_parent):
    try:
        parent = path.parent.resolve(strict=True)
        if parent != allowed_parent or path.is_symlink():
            raise _install_error()
        if path.exists():
            shutil.rmtree(path)
    except InstallError:
        raise
    except OSError:
        raise _install_error() from None


def safe_extract(archive_path, destination):
    """Extract a deterministic Task 6 tar without tarfile.extract()."""

    archive_path = Path(archive_path)
    destination = Path(destination)
    try:
        archive_metadata = os.lstat(archive_path)
        parent = destination.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _install_error() from None
    if (
        not stat.S_ISREG(archive_metadata.st_mode)
        or archive_metadata.st_uid != os.getuid()
        or archive_metadata.st_nlink != 1
        or archive_metadata.st_size <= 0
        or destination.exists()
        or destination.is_symlink()
    ):
        raise _install_error()
    resolved_destination = parent / destination.name
    if resolved_destination.parent != parent:
        raise _install_error()

    created = False
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            records = _validated_members(
                archive,
                archive_metadata.st_size,
            )
            os.mkdir(resolved_destination, mode=0o700)
            created = True
            for relative, _member, kind in records:
                target = resolved_destination.joinpath(*relative.parts)
                target_parent = target.parent
                target_parent.mkdir(
                    mode=0o755,
                    parents=True,
                    exist_ok=True,
                )
                resolved_parent = target_parent.resolve(strict=True)
                if not _within(resolved_parent, resolved_destination):
                    raise _install_error()
                if kind == "directory":
                    target.mkdir(mode=0o755, exist_ok=True)
                    metadata = os.lstat(target)
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise _install_error()
                    os.chmod(target, 0o755)

            for relative, member, kind in records:
                if kind != "file":
                    continue
                target = resolved_destination.joinpath(*relative.parts)
                if target.exists() or target.is_symlink():
                    raise _install_error()
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise _install_error()
                descriptor = None
                size = 0
                try:
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    descriptor = os.open(target, flags, member.mode)
                    while True:
                        chunk = extracted.read(TRANSFER_CHUNK_BYTES)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > member.size:
                            raise _install_error()
                        view = memoryview(chunk)
                        while view:
                            written = os.write(descriptor, view)
                            if written <= 0:
                                raise _install_error()
                            view = view[written:]
                    if size != member.size:
                        raise _install_error()
                    os.fchmod(descriptor, member.mode)
                    os.fsync(descriptor)
                finally:
                    extracted.close()
                    if descriptor is not None:
                        os.close(descriptor)
        os.chmod(resolved_destination, 0o755)
    except InstallError:
        if created:
            _safe_remove_tree(resolved_destination, parent)
        raise
    except (OSError, tarfile.TarError):
        if created:
            try:
                _safe_remove_tree(resolved_destination, parent)
            except InstallError:
                pass
        raise _install_error() from None
    return resolved_destination


def _verified_file(path, *, size_bytes, sha256):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_size != size_bytes
        ):
            raise _install_error()
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, TRANSFER_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        if size != size_bytes or digest.hexdigest() != sha256:
            raise _install_error()
    except InstallError:
        raise
    except OSError:
        raise _install_error() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return path


def _canonical_bytes(value):
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _measured_web_tree(root, package_id):
    root = Path(root)
    try:
        root_metadata = os.lstat(root)
        if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
            raise _install_error()
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
        if not files:
            raise _install_error()
        records = []
        extension_paths = []
        for path in files:
            relative = path.relative_to(root).as_posix()
            if (
                _SERVED_RELATIVE_PATH.fullmatch(relative) is None
                or "//" in relative
                or "/./" in relative
                or "/../" in relative
            ):
                raise _install_error()
            metadata = os.lstat(path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise _install_error()
            body = path.read_bytes()
            if len(body) != metadata.st_size:
                raise _install_error()
            records.append(
                {
                    "mode": 0o644,
                    "path": relative,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                }
            )
            extension_paths.append(
                f"/extensions/{package_id}/{relative}"
            )
        digest = hashlib.sha256(_canonical_bytes(records)).hexdigest()
    except InstallError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _install_error() from None
    return digest, tuple(extension_paths)


def _ui_measurement(content, package):
    try:
        files = sorted(
            path
            for path in content.rglob("*")
            if path.is_file()
        )
        relative_files = tuple(
            path.relative_to(content).as_posix() for path in files
        )
        if "__init__.py" not in relative_files:
            raise _install_error()
        for path, relative in zip(files, relative_files, strict=True):
            if relative in _UI_ROOT_FILES:
                continue
            if not relative.startswith("web/"):
                raise _install_error()
            if path.suffix.casefold() in _UI_FORBIDDEN_EXECUTABLE_SUFFIXES:
                raise _install_error()
        if (content / "__init__.py").read_bytes() != _SAFE_UI_LOADER:
            raise _install_error()
        digest, extension_paths = _measured_web_tree(
            content / "web",
            package.package_id,
        )
        if not secrets.compare_digest(digest, package.web_sha256):
            raise _install_error()
        return digest, extension_paths
    except InstallError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _install_error() from None


def _declared_web_root(content):
    loader = content / "__init__.py"
    try:
        body = loader.read_bytes()
        if len(body) > 1024 * 1024:
            return None
        tree = ast.parse(body, filename="__init__.py")
    except (OSError, SyntaxError, ValueError):
        return None
    values = []
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else (statement.target,)
        )
        if not any(
            isinstance(target, ast.Name)
            and target.id == "WEB_DIRECTORY"
            for target in targets
        ):
            continue
        value = statement.value
        if not isinstance(value, ast.Constant) or not isinstance(
            value.value,
            str,
        ):
            return None
        values.append(value.value)
    if len(values) != 1:
        return None
    value = values[0]
    if value.startswith("./"):
        value = value[2:]
    try:
        relative = _archive_name(value)
        root = content.joinpath(*relative.parts)
        if not root.is_dir() or root.is_symlink():
            return None
        if not _within(root.resolve(strict=True), content.resolve(strict=True)):
            return None
    except (InstallError, OSError, RuntimeError):
        return None
    return root


def _extension_paths(content, package_id):
    root = _declared_web_root(content)
    if root is None:
        return ()
    _digest, extension_paths = _measured_web_tree(root, package_id)
    return extension_paths


def _wheel_distribution(filename):
    stem = filename.split("-", 1)[0]
    return re.sub(r"[-_.]+", "-", stem).casefold()


def _validated_wheel_distributions(wheels, required=None):
    distributions = {
        _wheel_distribution(wheel.filename) for wheel, _path in wheels
    }
    if (
        not distributions
        or "" in distributions
        or distributions.intersection(_PROTECTED_DISTRIBUTIONS)
        or (required is not None and distributions != set(required))
    ):
        raise _install_error()
    return distributions


class SubprocessRunner:
    async def run(self, argv):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env={
                    "PATH": os.defpath,
                    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                    "PIP_NO_INDEX": "1",
                    "PIP_NO_INPUT": "1",
                    "PYTHONHASHSEED": "0",
                    "PYTHONNOUSERSITE": "1",
                },
            )
            return await process.wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (OSError, ProcessLookupError, asyncio.TimeoutError):
                    try:
                        process.kill()
                    except (OSError, ProcessLookupError):
                        pass
            raise
        except Exception:
            raise _install_error() from None


class CustomNodeInstaller:
    def __init__(
        self,
        *,
        custom_nodes_root,
        wheel_root,
        runner=None,
        artifact_root=None,
    ):
        self.custom_nodes_root = _resolved_directory(
            custom_nodes_root,
            "Custom-node root is unavailable.",
        )
        self.wheel_root = _resolved_directory(
            wheel_root,
            "Wheel root is unavailable.",
        )
        if artifact_root is None:
            artifact_root = self.wheel_root.parent / ".cloud-run-artifacts"
        self.artifact_root = _resolved_directory(
            artifact_root,
            "Artifact root is unavailable.",
        )
        self.runner = runner or SubprocessRunner()
        if not callable(getattr(self.runner, "run", None)):
            raise ValueError("Invalid fixed-argv runner.")

    def archive_path(self, archive):
        path = self.artifact_root / f"{archive.artifact_id}.tar"
        try:
            path.parent.resolve(strict=True).relative_to(self.artifact_root)
        except (OSError, RuntimeError, ValueError):
            raise _install_error() from None
        return path

    def _wheel_path(self, wheel):
        if not isinstance(wheel, PythonWheelSpec):
            raise _install_error()
        path = self.wheel_root / wheel.filename
        try:
            path.parent.resolve(strict=True).relative_to(self.wheel_root)
        except (OSError, RuntimeError, ValueError):
            raise _install_error() from None
        return path

    def _validate_node(self, node):
        try:
            validate_dependency(node)
        except (TypeError, ValueError):
            raise _install_error() from None
        if (
            not isinstance(node, CustomNodeSpec)
            or node.archive.destination
            != f"custom_nodes/{node.package_id}"
        ):
            raise _install_error()
        return node

    async def _install_wheels(self, wheels, *, required_distributions=None):
        wheels = tuple(
            sorted(wheels, key=lambda item: str(item[1].resolve()))
        )
        if not wheels:
            if required_distributions is not None:
                raise _install_error()
            return ()
        _validated_wheel_distributions(wheels, required_distributions)
        argv = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            "--no-compile",
            *(str(path.resolve()) for _wheel, path in wheels),
        ]
        try:
            return_code = await self.runner.run(argv)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except InstallError:
            raise
        except Exception:
            raise _install_error() from None
        if (
            isinstance(return_code, bool)
            or not isinstance(return_code, int)
            or return_code != 0
        ):
            raise _install_error()
        return tuple(wheel.filename for wheel, _path in wheels)

    async def install_wheels(self, wheels):
        try:
            wheels = tuple(wheels)
        except TypeError:
            raise _install_error() from None
        verified = []
        identities = set()
        for wheel in sorted(
            wheels,
            key=lambda item: (
                getattr(item, "filename", ""),
                getattr(item, "sha256", ""),
            ),
        ):
            try:
                validate_dependency(wheel)
            except (TypeError, ValueError):
                raise _install_error() from None
            identity = (wheel.filename, wheel.sha256)
            if identity in identities:
                raise _install_error()
            identities.add(identity)
            path = _verified_file(
                self._wheel_path(wheel),
                size_bytes=wheel.size_bytes,
                sha256=wheel.sha256,
            )
            verified.append((wheel, path))
        return await self._install_wheels(verified)

    def _replace_content(self, content, destination):
        backup = None
        try:
            if destination.exists() or destination.is_symlink():
                metadata = os.lstat(destination)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                ):
                    raise _install_error()
                backup = self.custom_nodes_root / (
                    f".{destination.name}-{secrets.token_hex(12)}.backup"
                )
                os.replace(destination, backup)
            os.replace(content, destination)
            descriptor = os.open(
                self.custom_nodes_root,
                os.O_RDONLY
                | (
                    os.O_DIRECTORY
                    if hasattr(os, "O_DIRECTORY")
                    else 0
                ),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except InstallError:
            if backup is not None and backup.exists() and not destination.exists():
                try:
                    os.replace(backup, destination)
                except OSError:
                    pass
            raise
        except OSError:
            if backup is not None and backup.exists() and not destination.exists():
                try:
                    os.replace(backup, destination)
                except OSError:
                    pass
            raise _install_error() from None
        if backup is not None:
            _safe_remove_tree(backup, self.custom_nodes_root)

    async def install(self, node):
        node = self._validate_node(node)
        archive_path = _verified_file(
            self.archive_path(node.archive),
            size_bytes=node.archive.size_bytes,
            sha256=node.archive.sha256,
        )
        wheels = []
        for wheel in sorted(
            node.wheels,
            key=lambda item: (item.filename, item.sha256),
        ):
            path = _verified_file(
                self._wheel_path(wheel),
                size_bytes=wheel.size_bytes,
                sha256=wheel.sha256,
            )
            wheels.append((wheel, path))

        staging_parent = Path(
            tempfile.mkdtemp(
                prefix=f".{node.package_id}-",
                suffix=".part",
                dir=str(self.custom_nodes_root),
            )
        )
        os.chmod(staging_parent, 0o700)
        content = staging_parent / "content"
        destination = self.custom_nodes_root / node.package_id
        try:
            safe_extract(archive_path, content)
            extension_paths = _extension_paths(content, node.package_id)
            installed_wheels = await self._install_wheels(
                wheels,
                required_distributions=(
                    {"simpleeval"}
                    if node.package_id == "efficiency-nodes-comfyui"
                    else None
                ),
            )
            self._replace_content(content, destination)
        except (InstallError, asyncio.CancelledError, KeyboardInterrupt):
            if staging_parent.exists():
                try:
                    _safe_remove_tree(
                        staging_parent,
                        self.custom_nodes_root,
                    )
                except InstallError:
                    pass
            raise
        except Exception:
            if staging_parent.exists():
                try:
                    _safe_remove_tree(
                        staging_parent,
                        self.custom_nodes_root,
                    )
                except InstallError:
                    pass
            raise _install_error() from None
        if staging_parent.exists():
            _safe_remove_tree(staging_parent, self.custom_nodes_root)
        return InstallResult(
            package_id=node.package_id,
            revision=node.revision,
            destination=destination,
            wheels=installed_wheels,
            extension_paths=extension_paths,
        )

    async def install_ui_package(self, package):
        try:
            validate_dependency(package)
        except (TypeError, ValueError):
            raise _install_error() from None
        if (
            not isinstance(package, UiPackageSpec)
            or package.archive.destination
            != f"custom_nodes/{package.package_id}"
        ):
            raise _install_error()
        archive_path = _verified_file(
            self.archive_path(package.archive),
            size_bytes=package.archive.size_bytes,
            sha256=package.archive.sha256,
        )
        staging_parent = Path(
            tempfile.mkdtemp(
                prefix=f".{package.package_id}-",
                suffix=".part",
                dir=str(self.custom_nodes_root),
            )
        )
        os.chmod(staging_parent, 0o700)
        content = staging_parent / "content"
        destination = self.custom_nodes_root / package.package_id
        try:
            safe_extract(archive_path, content)
            web_sha256, extension_paths = _ui_measurement(content, package)
            self._replace_content(content, destination)
        except (InstallError, asyncio.CancelledError, KeyboardInterrupt):
            if staging_parent.exists():
                try:
                    _safe_remove_tree(staging_parent, self.custom_nodes_root)
                except InstallError:
                    pass
            raise
        except Exception:
            if staging_parent.exists():
                try:
                    _safe_remove_tree(staging_parent, self.custom_nodes_root)
                except InstallError:
                    pass
            raise _install_error() from None
        if staging_parent.exists():
            _safe_remove_tree(staging_parent, self.custom_nodes_root)
        return UiInstallResult(
            package_id=package.package_id,
            revision=package.revision,
            destination=destination,
            wheels=(),
            web_sha256=web_sha256,
            extension_paths=extension_paths,
        )

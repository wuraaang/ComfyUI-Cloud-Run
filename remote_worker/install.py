"""Fail-closed extraction and fixed-argv custom-node installation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import stat
import sys
import tarfile
import tempfile

from cloud_run.manifest import (
    CustomNodeSpec,
    PythonWheelSpec,
    validate_dependency,
)
from .transfers import TRANSFER_CHUNK_BYTES


MAX_ARCHIVE_MEMBERS = 100_000


class InstallError(RuntimeError):
    """A sanitized archive, wheel, or installer failure."""


@dataclass(frozen=True)
class InstallResult:
    package_id: str
    revision: str
    destination: Path
    wheels: tuple[str, ...]


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

    async def _install_wheels(self, wheels):
        installed = []
        for wheel, path in wheels:
            argv = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--disable-pip-version-check",
                str(path),
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
            installed.append(wheel.filename)
        return tuple(installed)

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
            installed_wheels = await self._install_wheels(wheels)
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
        )

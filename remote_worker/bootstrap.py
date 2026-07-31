"""Standalone reviewed downloader for one immutable Remote Worker release."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit
import zlib


MAX_LOCK_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
DEFAULT_DESTINATION = Path("/opt/comfyui-cloud-run")
STATE_DIRECTORY = "/var/lib/comfyui-cloud-run"
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_LOCK_FIELDS = {
    "schema_version",
    "archive_url",
    "worker_commit",
    "worker_archive_sha256",
    "worker_archive_size_bytes",
    "protocol_version",
    "comfyui_core_version",
    "comfyui_frontend_version",
    "python_version",
    "destination",
}


class BootstrapError(RuntimeError):
    """The immutable worker release could not be safely installed."""


@dataclass(frozen=True)
class DownloadStream:
    source_url: str
    redirect_count: int
    chunks: object


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urlerror.HTTPError(
            request.full_url,
            code,
            "redirect rejected",
            headers,
            fp,
        )


class HttpsTransport:
    """HTTPS-only stream that neither follows redirects nor uses a shell."""

    def __init__(self, *, timeout_seconds=30):
        self.timeout_seconds = timeout_seconds

    def stream(self, url):
        opener = urlrequest.build_opener(
            urlrequest.ProxyHandler({}),
            _NoRedirect(),
        )
        headers = {
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "User-Agent": "ComfyUI-Cloud-Run-Bootstrap/1",
        }
        request = urlrequest.Request(url, headers=headers, method="GET")
        redirect_count = 0
        try:
            response = opener.open(
                request,
                timeout=self.timeout_seconds,
            )
        except urlerror.HTTPError as redirect:
            if redirect.code != 302:
                redirect.close()
                raise BootstrapError(
                    "Reviewed worker archive is unavailable."
                ) from None
            try:
                location = redirect.headers.get("Location")
                if not isinstance(location, str):
                    raise BootstrapError(
                        "Reviewed worker archive is unavailable."
                    )
                parsed = urlsplit(location)
                if (
                    parsed.scheme != "https"
                    or parsed.hostname
                    != "release-assets.githubusercontent.com"
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.port is not None
                    or parsed.fragment
                ):
                    raise BootstrapError(
                        "Reviewed worker archive is unavailable."
                    )
            except (TypeError, ValueError):
                raise BootstrapError(
                    "Reviewed worker archive is unavailable."
                ) from None
            finally:
                redirect.close()
            redirect_count = 1
            request = urlrequest.Request(
                location,
                headers=headers,
                method="GET",
            )
            try:
                response = opener.open(
                    request,
                    timeout=self.timeout_seconds,
                )
            except urlerror.HTTPError as terminal_error:
                terminal_error.close()
                raise BootstrapError(
                    "Reviewed worker archive is unavailable."
                ) from None
            except (OSError, urlerror.URLError):
                raise BootstrapError(
                    "Reviewed worker archive is unavailable."
                ) from None
        except (OSError, urlerror.URLError):
            raise BootstrapError(
                "Reviewed worker archive is unavailable."
            ) from None
        encoding = response.headers.get("Content-Encoding", "identity")
        if getattr(response, "status", None) != 200 or (
            not isinstance(encoding, str)
            or encoding.casefold() != "identity"
        ):
            response.close()
            raise BootstrapError(
                "Reviewed worker archive is unavailable."
            )

        def chunks():
            try:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                response.close()

        return DownloadStream(
            source_url=url,
            redirect_count=redirect_count,
            chunks=chunks(),
        )


def _bootstrap_error():
    return BootstrapError("Remote Worker bootstrap rejected the release.")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("invalid object")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("invalid constant")


def _validated_lock(payload, allowed_destination):
    if not isinstance(payload, dict) or set(payload) != _LOCK_FIELDS:
        raise _bootstrap_error()
    commit = payload.get("worker_commit")
    digest = payload.get("worker_archive_sha256")
    size = payload.get("worker_archive_size_bytes")
    if (
        payload.get("schema_version") != 1
        or not isinstance(commit, str)
        or not _HEX_40.fullmatch(commit)
        or not isinstance(digest, str)
        or not _HEX_64.fullmatch(digest)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 < size <= MAX_ARCHIVE_BYTES
        or payload.get("protocol_version") != "1"
        or payload.get("comfyui_core_version") != "0.29.0"
        or payload.get("comfyui_frontend_version") != "1.47.10"
        or payload.get("python_version") != "3.13.12"
        or payload.get("destination") != str(allowed_destination)
    ):
        raise _bootstrap_error()
    url = payload.get("archive_url")
    if not isinstance(url, str) or "%" in url:
        raise _bootstrap_error()
    try:
        parsed = urlsplit(url)
        expected_path = (
            "/wuraaang/ComfyUI-Cloud-Run/releases/download/"
            + "worker-v1-"
            + commit
            + "/comfyui-cloud-run-worker-"
            + commit
            + "-"
            + digest
            + ".tar.gz"
        )
    except (TypeError, ValueError):
        raise _bootstrap_error() from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        raise _bootstrap_error()
    return dict(payload)


def _archive_name(value):
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise _bootstrap_error()
    relative = PurePosixPath(value)
    if (
        str(relative) != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _bootstrap_error()
    return relative


def _validated_members(archive):
    try:
        members = archive.getmembers()
    except (OSError, tarfile.TarError):
        raise _bootstrap_error() from None
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise _bootstrap_error()
    records = []
    kinds = {}
    total = 0
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
            raise _bootstrap_error()
        if member.isdir():
            if member.mode != 0o755 or member.size != 0:
                raise _bootstrap_error()
            kind = "directory"
        elif member.isreg():
            if member.mode not in {0o644, 0o755} or member.size < 0:
                raise _bootstrap_error()
            kind = "file"
            total += member.size
            if total > MAX_EXPANDED_BYTES:
                raise _bootstrap_error()
        else:
            raise _bootstrap_error()
        name = relative.as_posix()
        if name in kinds:
            raise _bootstrap_error()
        for parent in relative.parents:
            if str(parent) != "." and kinds.get(parent.as_posix()) == "file":
                raise _bootstrap_error()
        if kind == "file" and any(
            existing.startswith(name + "/") for existing in kinds
        ):
            raise _bootstrap_error()
        kinds[name] = kind
        records.append((relative, member, kind))
    return tuple(records)


def _write_all(descriptor, content):
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _safe_extract(tar_path, destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise _bootstrap_error()
    created = False
    try:
        with tarfile.open(tar_path, mode="r:") as archive:
            records = _validated_members(archive)
            os.mkdir(destination, mode=0o700)
            created = True
            for relative, _member, kind in records:
                if kind != "directory":
                    continue
                target = destination.joinpath(*relative.parts)
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                if not stat.S_ISDIR(os.lstat(target).st_mode):
                    raise _bootstrap_error()
                os.chmod(target, 0o755)
            for relative, member, kind in records:
                if kind != "file":
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                if (
                    target.parent.resolve(strict=True) != destination
                    and destination.resolve(strict=True)
                    not in target.parent.resolve(strict=True).parents
                ):
                    raise _bootstrap_error()
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise _bootstrap_error()
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, member.mode)
                size = 0
                try:
                    while True:
                        chunk = extracted.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > member.size:
                            raise _bootstrap_error()
                        _write_all(descriptor, chunk)
                    if size != member.size:
                        raise _bootstrap_error()
                    os.fsync(descriptor)
                    os.fchmod(descriptor, member.mode)
                finally:
                    os.close(descriptor)
    except BootstrapError:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise
    except (OSError, RuntimeError, tarfile.TarError):
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise _bootstrap_error() from None


def _verify_layout(destination):
    required = {
        "remote_worker/__init__.py",
        "remote_worker/main.py",
        "cloud_run/manifest.py",
        "cloud_run/worker_protocol.py",
    }
    observed = set()
    try:
        for path in destination.rglob("*"):
            relative = path.relative_to(destination).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                raise _bootstrap_error()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise _bootstrap_error()
            if (
                relative.startswith("remote_worker/")
                and (
                    relative.endswith(".py")
                    or relative.endswith(".json")
                    or relative == "remote_worker/Caddyfile"
                )
            ) or relative in {
                "cloud_run/manifest.py",
                "cloud_run/worker_protocol.py",
            }:
                observed.add(relative)
            else:
                raise _bootstrap_error()
    except BootstrapError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _bootstrap_error() from None
    if not required.issubset(observed):
        raise _bootstrap_error()


def _decompress_gzip(archive_path, tar_path):
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    try:
        with Path(archive_path).open("rb") as source, Path(tar_path).open(
            "xb"
        ) as destination:
            os.chmod(tar_path, 0o600)
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                decoded = decompressor.decompress(chunk)
                total += len(decoded)
                if total > MAX_EXPANDED_BYTES:
                    raise _bootstrap_error()
                destination.write(decoded)
            decoded = decompressor.flush()
            total += len(decoded)
            if total > MAX_EXPANDED_BYTES:
                raise _bootstrap_error()
            destination.write(decoded)
            destination.flush()
            os.fsync(destination.fileno())
        if (
            not decompressor.eof
            or decompressor.unused_data
            or decompressor.unconsumed_tail
            or total <= 0
        ):
            raise _bootstrap_error()
    except BootstrapError:
        raise
    except (OSError, zlib.error):
        raise _bootstrap_error() from None


def _default_exec(argv, *, cwd):
    os.chdir(cwd)
    os.execv(argv[0], argv)


class Bootstrap:
    def __init__(
        self,
        *,
        transport=None,
        exec_runner=None,
        allowed_destination=DEFAULT_DESTINATION,
    ):
        self.transport = transport or HttpsTransport()
        self.exec_runner = exec_runner or _default_exec
        destination = Path(allowed_destination)
        if not destination.is_absolute():
            raise ValueError("Bootstrap destination must be absolute.")
        self.allowed_destination = destination

    def _download(self, lock, path):
        try:
            stream = self.transport.stream(lock["archive_url"])
        except BootstrapError:
            raise
        except Exception:
            raise _bootstrap_error() from None
        if (
            not isinstance(stream, DownloadStream)
            or stream.source_url != lock["archive_url"]
            or stream.redirect_count not in {0, 1}
        ):
            raise _bootstrap_error()
        digest = hashlib.sha256()
        size = 0
        try:
            with Path(path).open("xb") as destination:
                os.chmod(path, 0o600)
                for chunk in stream.chunks:
                    if not isinstance(chunk, bytes) or not chunk:
                        raise _bootstrap_error()
                    size += len(chunk)
                    if (
                        size > lock["worker_archive_size_bytes"]
                        or size > MAX_ARCHIVE_BYTES
                    ):
                        raise _bootstrap_error()
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except BootstrapError:
            raise
        except (OSError, TypeError):
            raise _bootstrap_error() from None
        if (
            size != lock["worker_archive_size_bytes"]
            or digest.hexdigest() != lock["worker_archive_sha256"]
        ):
            raise _bootstrap_error()

    def run(self, payload):
        destination = self.allowed_destination
        lock = _validated_lock(payload, destination)
        try:
            parent = destination.parent.resolve(strict=True)
            metadata = os.lstat(parent)
        except (OSError, RuntimeError):
            raise _bootstrap_error() from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or destination.exists()
            or destination.is_symlink()
        ):
            raise _bootstrap_error()

        staging = None
        installed = False
        try:
            staging = Path(
                tempfile.mkdtemp(
                    prefix=".cloud-run-bootstrap-",
                    dir=str(parent),
                )
            )
            os.chmod(staging, 0o700)
            archive_path = staging / "worker.tar.gz.part"
            tar_path = staging / "worker.tar.part"
            extracted = staging / "payload"
            self._download(lock, archive_path)
            _decompress_gzip(archive_path, tar_path)
            _safe_extract(tar_path, extracted)
            _verify_layout(extracted)
            archive_path.unlink()
            tar_path.unlink()
            os.replace(extracted, destination)
            installed = True
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            staging.rmdir()
            staging = None
        except BootstrapError:
            raise
        except (OSError, RuntimeError):
            raise _bootstrap_error() from None
        finally:
            if staging is not None:
                try:
                    resolved = staging.resolve(strict=True)
                    if resolved.parent == parent and resolved.name.startswith(
                        ".cloud-run-bootstrap-"
                    ):
                        shutil.rmtree(resolved)
                except (OSError, RuntimeError):
                    pass
        if not installed:
            raise _bootstrap_error()
        argv = [
            sys.executable,
            "-m",
            "remote_worker.main",
            "--state-directory",
            STATE_DIRECTORY,
        ]
        try:
            self.exec_runner(argv, cwd=destination)
        except Exception:
            raise BootstrapError(
                "Reviewed Remote Worker could not be launched."
            ) from None
        return destination


def load_release_lock(path):
    try:
        content = Path(path).read_bytes()
        if not 0 < len(content) <= MAX_LOCK_BYTES:
            raise ValueError("invalid lock")
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise _bootstrap_error() from None
    return payload


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Install one reviewed ComfyUI Cloud Run worker release."
    )
    parser.add_argument("release_lock", type=Path)
    arguments = parser.parse_args(argv)
    try:
        Bootstrap().run(load_release_lock(arguments.release_lock))
    except BootstrapError:
        raise SystemExit(
            "Remote Worker bootstrap rejected the reviewed release."
        ) from None


if __name__ == "__main__":
    main()

"""Bounded, resumable, content-verified Remote Worker transfers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import os
from pathlib import Path, PurePosixPath
import re
import stat
from collections.abc import Mapping
from urllib.parse import urljoin, urlsplit

try:
    from ..cloud_run.manifest import (
        ArtifactSpec,
        PythonWheelSpec,
        validate_dependency,
    )
except ImportError:
    from cloud_run.manifest import (
        ArtifactSpec,
        PythonWheelSpec,
        validate_dependency,
    )


TRANSFER_CHUNK_BYTES = 8 * 1024 * 1024
MAX_CONCURRENT_TRANSFERS = 4
MAX_TRANSFER_RETRIES = 3
MAX_REDIRECTS = 3
_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)")
_HUGGINGFACE_ORIGIN = ("https", "huggingface.co", 443)
_HUGGINGFACE_BLOB_ORIGINS = frozenset(
    {
        ("https", "cas-bridge.xethub.hf.co", 443),
        ("https", "cdn-lfs-eu-1.hf.co", 443),
        ("https", "cdn-lfs-us-1.hf.co", 443),
        ("https", "transfer.xethub-eu.hf.co", 443),
        ("https", "transfer.xethub.hf.co", 443),
        ("https", "us.aws.cdn.hf.co", 443),
        ("https", "us.gcp.cdn.hf.co", 443),
    }
)


class TransferError(RuntimeError):
    """A sanitized transfer-policy, transport, or integrity failure."""


class ArtifactIntegrityError(TransferError):
    pass


class TransferProgressError(TransferError):
    def __init__(self, original):
        self.original = original
        super().__init__("Artifact transfer progress failed.")


class _RetryableTransferError(TransferError):
    pass


@dataclass(frozen=True)
class TransferResult:
    artifact_id: str
    state: str
    destination: Path
    next_offset: int
    size_bytes: int
    sha256: str


def _transfer_error():
    return TransferError("Artifact transfer was rejected.")


def _retryable_error():
    return _RetryableTransferError("Artifact transfer was interrupted.")


def _bounded_integer(value, *, minimum, maximum, message):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(message)
    return value


def _origin(value):
    if not isinstance(value, str):
        raise _transfer_error()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise _transfer_error() from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise _transfer_error()
    return (
        parsed.scheme,
        parsed.hostname.casefold(),
        port or 443,
    )


def _headers(value):
    try:
        return {
            str(name).casefold(): str(item)
            for name, item in value.items()
        }
    except (AttributeError, TypeError, ValueError):
        raise _transfer_error() from None


def _part_path(destination):
    return destination.with_suffix(".part")


def _hash_regular_file(path):
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
            or metadata.st_size < 0
        ):
            raise _transfer_error()
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, TRANSFER_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        if size != metadata.st_size:
            raise _transfer_error()
        return size, digest.hexdigest()
    except TransferError:
        raise
    except OSError:
        raise _transfer_error() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verified_file(path, artifact):
    size, digest = _hash_regular_file(path)
    return size == artifact.size_bytes and digest == artifact.sha256


def _fsync_directory(path):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        raise _transfer_error() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


async def _close_response(response):
    closer = getattr(response, "close", None)
    if not callable(closer):
        return
    try:
        result = closer()
        if inspect.isawaitable(result):
            await result
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return


class _AiohttpRangeResponse:
    def __init__(self, response, session):
        self._response = response
        self._session = session
        self.status = response.status
        self.headers = response.headers
        self.url = response.url
        self._closed = False

    async def iter_chunks(self, maximum_bytes):
        try:
            async for chunk in self._response.content.iter_chunked(
                maximum_bytes
            ):
                yield bytes(chunk)
        finally:
            await self.close()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._response.release()
        await self._session.close()


class AiohttpRangeClient:
    async def get(self, url, *, headers, allow_redirects):
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise TransferError("Artifact transport is unavailable.") from None
        session = None
        try:
            timeout = ClientTimeout(
                total=None,
                sock_connect=30,
                sock_read=120,
            )
            session = ClientSession(
                timeout=timeout,
                auto_decompress=False,
                trust_env=False,
            )
            request_headers = {
                "Accept-Encoding": "identity",
                **dict(headers),
            }
            response = await session.get(
                url,
                headers=request_headers,
                allow_redirects=allow_redirects,
            )
            return _AiohttpRangeResponse(response, session)
        except (asyncio.CancelledError, KeyboardInterrupt):
            if session is not None:
                await session.close()
            raise
        except Exception:
            if session is not None:
                await session.close()
            raise TransferError("Artifact transport is unavailable.") from None


def wheel_artifact(wheel):
    try:
        validate_dependency(wheel)
    except (TypeError, ValueError):
        raise _transfer_error() from None
    if not isinstance(wheel, PythonWheelSpec):
        raise _transfer_error()
    artifact_id = (
        wheel.source.locator.removeprefix("local-upload:")
        if wheel.source.kind == "local-upload"
        else "wheel-" + wheel.sha256
    )
    artifact = ArtifactSpec(
        artifact_id=artifact_id,
        kind="python_wheel",
        logical_name=artifact_id,
        destination=(
            "custom_nodes/.cloud-run-wheels/" + wheel.filename
        ),
        size_bytes=wheel.size_bytes,
        sha256=wheel.sha256,
        source=wheel.source,
    )
    try:
        validate_dependency(artifact)
    except (TypeError, ValueError):
        raise _transfer_error() from None
    return artifact


class TransferManager:
    def __init__(
        self,
        *,
        root,
        max_concurrency=MAX_CONCURRENT_TRANSFERS,
        max_retries=MAX_TRANSFER_RETRIES,
        sleeper=None,
        progress=None,
        artifact_root=None,
        wheel_root=None,
    ):
        try:
            resolved_root = Path(root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Transfer root is unavailable.") from None
        if not resolved_root.is_dir():
            raise ValueError("Transfer root is unavailable.")
        self.root = resolved_root
        if artifact_root is None:
            artifact_root = self.root / ".cloud-run-artifacts"
            try:
                artifact_root.mkdir(mode=0o700, exist_ok=True)
                os.chmod(artifact_root, 0o700)
            except OSError:
                raise ValueError("Artifact staging root is unavailable.") from None
        try:
            artifact_metadata = os.lstat(artifact_root)
            resolved_artifact_root = Path(artifact_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Artifact staging root is unavailable.") from None
        if (
            not stat.S_ISDIR(artifact_metadata.st_mode)
            or artifact_metadata.st_uid != os.getuid()
            or Path(artifact_root).is_symlink()
        ):
            raise ValueError("Artifact staging root is unavailable.")
        try:
            os.chmod(resolved_artifact_root, 0o700)
        except OSError:
            raise ValueError("Artifact staging root is unavailable.") from None
        self.artifact_root = resolved_artifact_root
        if wheel_root is None:
            wheel_root = self.root / ".cloud-run-wheels"
            try:
                wheel_root.mkdir(mode=0o700, exist_ok=True)
            except OSError:
                raise ValueError("Wheel staging root is unavailable.") from None
        try:
            wheel_metadata = os.lstat(wheel_root)
            resolved_wheel_root = Path(wheel_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Wheel staging root is unavailable.") from None
        if (
            not stat.S_ISDIR(wheel_metadata.st_mode)
            or wheel_metadata.st_uid != os.getuid()
            or Path(wheel_root).is_symlink()
        ):
            raise ValueError("Wheel staging root is unavailable.")
        try:
            os.chmod(resolved_wheel_root, 0o700)
        except OSError:
            raise ValueError("Wheel staging root is unavailable.") from None
        self.wheel_root = resolved_wheel_root
        self.max_concurrency = _bounded_integer(
            max_concurrency,
            minimum=1,
            maximum=MAX_CONCURRENT_TRANSFERS,
            message="Invalid transfer concurrency.",
        )
        self.max_retries = _bounded_integer(
            max_retries,
            minimum=0,
            maximum=MAX_TRANSFER_RETRIES,
            message="Invalid transfer retry budget.",
        )
        self.sleeper = sleeper or asyncio.sleep
        self.progress = progress
        self._semaphore = None
        self._semaphore_loop = None
        self._artifact_locks = {}
        self._artifact_lock_loops = {}

    def _transfer_semaphore(self):
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
            self._semaphore_loop = loop
        return self._semaphore

    def _artifact(self, artifact):
        try:
            validate_dependency(artifact)
        except (TypeError, ValueError):
            raise _transfer_error() from None
        if not isinstance(artifact, ArtifactSpec):
            raise _transfer_error()
        return artifact

    def _destination(self, artifact):
        artifact = self._artifact(artifact)
        if artifact.kind in {"custom_node_archive", "profile_archive"}:
            suffix = (
                ".tar"
                if artifact.kind == "custom_node_archive"
                else ".profile.tar.gz"
            )
            destination = self.artifact_root / (
                artifact.artifact_id + suffix
            )
            try:
                destination.parent.resolve(
                    strict=True
                ).relative_to(self.artifact_root)
            except (OSError, RuntimeError, ValueError):
                raise _transfer_error() from None
            if destination.is_symlink():
                raise _transfer_error()
            if _part_path(destination) == destination:
                raise _transfer_error()
            return destination
        if artifact.kind == "python_wheel":
            relative = PurePosixPath(artifact.destination)
            if (
                relative.parts
                != (
                    "custom_nodes",
                    ".cloud-run-wheels",
                    relative.name,
                )
            ):
                raise _transfer_error()
            destination = self.wheel_root / relative.name
            if destination.is_symlink() or _part_path(
                destination
            ) == destination:
                raise _transfer_error()
            return destination
        relative = PurePosixPath(artifact.destination)
        current = self.root
        try:
            for part in relative.parts[:-1]:
                candidate = current / part
                try:
                    metadata = os.lstat(candidate)
                except FileNotFoundError:
                    os.mkdir(candidate, mode=0o700)
                    metadata = os.lstat(candidate)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                ):
                    raise _transfer_error()
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.root)
                current = resolved
            destination = current / relative.name
            destination.parent.resolve(strict=True).relative_to(self.root)
            if destination.is_symlink():
                raise _transfer_error()
            if _part_path(destination) == destination:
                raise _transfer_error()
            return destination
        except TransferError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _transfer_error() from None

    def part_path(self, artifact):
        return _part_path(self._destination(artifact))

    def destination_path(self, artifact):
        return self._destination(artifact)

    def verify(self, artifact):
        artifact = self._artifact(artifact)
        destination = self._destination(artifact)
        return self._existing_result(artifact, destination)

    def reset(self, artifact):
        artifact = self._artifact(artifact)
        destination = self._destination(artifact)
        part = _part_path(destination)
        changed_parents = set()
        for path in (part, destination):
            try:
                metadata = os.lstat(path)
            except FileNotFoundError:
                continue
            except OSError:
                raise _transfer_error() from None
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise _transfer_error()
            try:
                path.unlink()
            except OSError:
                raise _transfer_error() from None
            changed_parents.add(path.parent)
        for parent in changed_parents:
            _fsync_directory(parent)

    async def _notify_progress(self, artifact, offset, event):
        if self.progress is None:
            return
        if event not in {"transferring", "verifying", "verified"}:
            raise _transfer_error()
        try:
            result = self.progress(
                artifact.artifact_id,
                int(offset),
                event,
            )
            if inspect.isawaitable(result):
                await result
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as error:
            raise TransferProgressError(error) from None

    def _result(self, artifact, destination, state, offset):
        return TransferResult(
            artifact_id=artifact.artifact_id,
            state=state,
            destination=destination,
            next_offset=int(offset),
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
        )

    def _existing_result(self, artifact, destination):
        try:
            metadata = os.lstat(destination)
        except FileNotFoundError:
            return None
        except OSError:
            raise _transfer_error() from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise _transfer_error()
        if not _verified_file(destination, artifact):
            raise ArtifactIntegrityError(
                "Artifact content verification failed."
            )
        return self._result(
            artifact,
            destination,
            "verified",
            artifact.size_bytes,
        )

    def _part_offset(self, part, artifact):
        try:
            metadata = os.lstat(part)
        except FileNotFoundError:
            return 0
        except OSError:
            raise _transfer_error() from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or metadata.st_size < 0
            or metadata.st_size > artifact.size_bytes
        ):
            raise _transfer_error()
        return metadata.st_size

    async def _response(self, artifact, client, url, request_headers):
        original_origin = _origin(url)
        current_origin = original_origin
        crossed_origin = False
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                response = await client.get(
                    current_url,
                    headers=dict(request_headers),
                    allow_redirects=False,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                raise _retryable_error() from None
            try:
                status = getattr(response, "status", None)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise _transfer_error()
                response_url = str(getattr(response, "url", current_url))
                if _origin(response_url) != current_origin:
                    raise _transfer_error()
                response_headers = _headers(
                    getattr(response, "headers", {})
                )
            except BaseException:
                await _close_response(response)
                raise
            if 300 <= status < 400:
                location = response_headers.get("location")
                if location is None or redirect_count >= MAX_REDIRECTS:
                    await _close_response(response)
                    raise _transfer_error()
                redirected = urljoin(current_url, location)
                redirected_origin = _origin(redirected)
                if redirected_origin != current_origin and (
                    crossed_origin
                    or artifact.kind != "model"
                    or artifact.source.kind != "huggingface"
                    or original_origin != _HUGGINGFACE_ORIGIN
                    or current_origin != _HUGGINGFACE_ORIGIN
                    or redirected_origin not in _HUGGINGFACE_BLOB_ORIGINS
                ):
                    await _close_response(response)
                    raise _transfer_error()
                if redirected_origin != current_origin:
                    crossed_origin = True
                await _close_response(response)
                current_url = redirected
                current_origin = redirected_origin
                continue
            return response, response_headers
        raise _transfer_error()

    def _validate_response(self, response, headers, *, offset, expected_size):
        status = response.status
        remaining = expected_size - offset
        if offset:
            if (
                status == 200
                and headers.get("accept-ranges", "").casefold() == "none"
                and headers.get("content-length") == str(expected_size)
            ):
                return "reset"
            if status != 206:
                raise _transfer_error()
            expected_range = (
                f"bytes {offset}-{expected_size - 1}/{expected_size}"
            )
            if headers.get("content-range") != expected_range:
                raise _transfer_error()
        elif status == 206:
            expected_range = f"bytes 0-{expected_size - 1}/{expected_size}"
            if headers.get("content-range") != expected_range:
                raise _transfer_error()
        elif status != 200:
            if 500 <= status <= 599:
                raise _retryable_error()
            raise _transfer_error()
        length = headers.get("content-length")
        if length is not None:
            try:
                parsed_length = int(length)
            except (TypeError, ValueError):
                raise _transfer_error() from None
            if (
                str(parsed_length) != length
                or parsed_length != remaining
            ):
                raise _transfer_error()
        return "append"

    async def _append_response(
        self,
        artifact,
        part,
        *,
        offset,
        response,
    ):
        iterator = getattr(response, "iter_chunks", None)
        if not callable(iterator):
            raise _transfer_error()
        descriptor = None
        handle = None
        written = offset
        try:
            flags = os.O_WRONLY | os.O_CREAT
            flags |= os.O_APPEND if offset else os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(part, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
                or metadata.st_size != offset
            ):
                raise _transfer_error()
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "ab", buffering=0)
            descriptor = None
            try:
                async for chunk in iterator(TRANSFER_CHUNK_BYTES):
                    if (
                        not isinstance(chunk, bytes)
                        or not chunk
                        or written + len(chunk) > artifact.size_bytes
                    ):
                        raise _transfer_error()
                    for position in range(0, len(chunk), TRANSFER_CHUNK_BYTES):
                        piece = chunk[
                            position : position + TRANSFER_CHUNK_BYTES
                        ]
                        view = memoryview(piece)
                        while view:
                            count = handle.write(view)
                            if count is None or count <= 0:
                                raise _transfer_error()
                            written += count
                            view = view[count:]
                        handle.flush()
                        os.fsync(handle.fileno())
                        _fsync_directory(part.parent)
                        await self._notify_progress(
                            artifact,
                            written,
                            "transferring",
                        )
            except (TransferError, asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                raise _retryable_error() from None
            handle.flush()
            os.fsync(handle.fileno())
        except (TransferError, asyncio.CancelledError, KeyboardInterrupt):
            raise
        except OSError:
            raise _transfer_error() from None
        finally:
            if handle is not None:
                handle.close()
            elif descriptor is not None:
                os.close(descriptor)
            await _close_response(response)
        if written != artifact.size_bytes:
            raise _transfer_error()

    async def _download_once(self, artifact, client, source_url):
        destination = self._destination(artifact)
        existing = self._existing_result(artifact, destination)
        if existing is not None:
            return existing
        part = _part_path(destination)
        offset = self._part_offset(part, artifact)
        if offset == artifact.size_bytes:
            if not _verified_file(part, artifact):
                try:
                    part.unlink()
                    _fsync_directory(part.parent)
                except OSError:
                    pass
                raise _transfer_error()
        else:
            request_headers = {}
            if offset:
                request_headers["Range"] = f"bytes={offset}-"
            response, response_headers = await self._response(
                artifact,
                client,
                source_url,
                request_headers,
            )
            try:
                response_action = self._validate_response(
                    response,
                    response_headers,
                    offset=offset,
                    expected_size=artifact.size_bytes,
                )
            except BaseException:
                await _close_response(response)
                raise
            if response_action == "reset":
                await _close_response(response)
                try:
                    part.unlink()
                    _fsync_directory(part.parent)
                except TransferError:
                    raise
                except OSError:
                    raise _transfer_error() from None
                offset = 0
                response, response_headers = await self._response(
                    artifact,
                    client,
                    source_url,
                    {},
                )
                try:
                    self._validate_response(
                        response,
                        response_headers,
                        offset=0,
                        expected_size=artifact.size_bytes,
                    )
                except BaseException:
                    await _close_response(response)
                    raise
            await self._append_response(
                artifact,
                part,
                offset=offset,
                response=response,
            )
        await self._notify_progress(
            artifact,
            artifact.size_bytes,
            "verifying",
        )
        if not _verified_file(part, artifact):
            try:
                part.unlink()
                _fsync_directory(part.parent)
            except OSError:
                pass
            raise _transfer_error()
        try:
            os.replace(part, destination)
            os.chmod(destination, 0o600)
            _fsync_directory(destination.parent)
        except TransferError:
            raise
        except OSError:
            raise _transfer_error() from None
        await self._notify_progress(
            artifact,
            artifact.size_bytes,
            "verified",
        )
        return self._result(
            artifact,
            destination,
            "verified",
            artifact.size_bytes,
        )

    async def download(self, artifact, *, client, source_url=None):
        artifact = self._artifact(artifact)
        source_url = source_url or artifact.source.locator
        if (
            artifact.source.kind == "huggingface"
            and source_url != artifact.source.locator
        ):
            raise _transfer_error()
        _origin(source_url)
        async with self._transfer_semaphore():
            for attempt in range(self.max_retries + 1):
                try:
                    return await self._download_once(
                        artifact,
                        client,
                        source_url,
                    )
                except _RetryableTransferError:
                    if attempt >= self.max_retries:
                        raise _transfer_error() from None
                    await self.sleeper(min(2**attempt, 4))
        raise _transfer_error()

    async def download_many(
        self,
        artifacts,
        *,
        client,
        source_urls=None,
    ):
        artifacts = tuple(self._artifact(item) for item in artifacts)
        destinations = [self._destination(item) for item in artifacts]
        part_paths = [_part_path(item) for item in destinations]
        if len(set(destinations)) != len(destinations) or len(
            set(part_paths)
        ) != len(part_paths) or set(destinations).intersection(part_paths):
            raise _transfer_error()
        if source_urls is None:
            urls = {}
        elif isinstance(source_urls, Mapping):
            urls = source_urls
        else:
            raise _transfer_error()
        tasks = [
            asyncio.create_task(
                self.download(
                    artifact,
                    client=client,
                    source_url=urls.get(artifact.artifact_id),
                )
            )
            for artifact in artifacts
        ]
        try:
            return tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    def _upload_lock(self, artifact_id):
        loop = asyncio.get_running_loop()
        lock = self._artifact_locks.get(artifact_id)
        if (
            lock is None
            or self._artifact_lock_loops.get(artifact_id) is not loop
        ):
            lock = asyncio.Lock()
            self._artifact_locks[artifact_id] = lock
            self._artifact_lock_loops[artifact_id] = loop
        return lock

    async def upload(self, artifact, *, content_range, body):
        artifact = self._artifact(artifact)
        if not isinstance(body, bytes) or not 0 < len(body) <= TRANSFER_CHUNK_BYTES:
            raise _transfer_error()
        match = (
            _CONTENT_RANGE.fullmatch(content_range)
            if isinstance(content_range, str)
            else None
        )
        if match is None:
            raise _transfer_error()
        start, end, total = (int(value) for value in match.groups())
        if (
            content_range != f"bytes {start}-{end}/{total}"
            or total != artifact.size_bytes
            or start > end
            or end - start + 1 != len(body)
            or end >= total
        ):
            raise _transfer_error()

        async with self._upload_lock(artifact.artifact_id):
            destination = self._destination(artifact)
            existing = self._existing_result(artifact, destination)
            if existing is not None:
                return existing
            part = _part_path(destination)
            expected_offset = self._part_offset(part, artifact)
            if start != expected_offset:
                raise _transfer_error()
            descriptor = None
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(part, flags, 0o600)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                    or metadata.st_nlink != 1
                    or metadata.st_size != expected_offset
                ):
                    raise _transfer_error()
                os.fchmod(descriptor, 0o600)
                view = memoryview(body)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise _transfer_error()
                    view = view[written:]
                os.fsync(descriptor)
            except TransferError:
                raise
            except OSError:
                raise _transfer_error() from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)

            next_offset = end + 1
            _fsync_directory(part.parent)
            await self._notify_progress(
                artifact,
                next_offset,
                "transferring",
            )
            if next_offset < artifact.size_bytes:
                return self._result(
                    artifact,
                    destination,
                    "receiving",
                    next_offset,
                )
            await self._notify_progress(
                artifact,
                next_offset,
                "verifying",
            )
            if not _verified_file(part, artifact):
                try:
                    part.unlink()
                    _fsync_directory(part.parent)
                except OSError:
                    pass
                raise _transfer_error()
            try:
                os.replace(part, destination)
                os.chmod(destination, 0o600)
                _fsync_directory(destination.parent)
            except TransferError:
                raise
            except OSError:
                raise _transfer_error() from None
            await self._notify_progress(
                artifact,
                next_offset,
                "verified",
            )
            return self._result(
                artifact,
                destination,
                "verified",
                next_offset,
            )

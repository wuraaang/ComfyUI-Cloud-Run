"""Fixed-boundary lifecycle for the internal native ComfyUI process."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
from pathlib import Path
import stat
import time

from cloud_run.constants import PINNED_PYTHON_VERSION
from cloud_run.manifest import (
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
)

COMFY_BIND_HOST = "127.0.0.1"
COMFY_BIND_PORT = 8188
DEFAULT_STARTUP_TIMEOUT_SECONDS = 180
DEFAULT_STOP_TIMEOUT_SECONDS = 15
_COMFY_ORIGIN = f"http://{COMFY_BIND_HOST}:{COMFY_BIND_PORT}"
_RUNTIME_ENVIRONMENT = {
    "CUDA_CACHE_PATH",
    "CUDA_DEVICE_ORDER",
    "CUDA_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "HSA_OVERRIDE_GFX_VERSION",
    "LD_LIBRARY_PATH",
    "NVIDIA_DRIVER_CAPABILITIES",
    "NVIDIA_VISIBLE_DEVICES",
    "PATH",
    "ROCR_VISIBLE_DEVICES",
    "TMPDIR",
    "XDG_CACHE_HOME",
}


class ComfyProcessError(RuntimeError):
    """A sanitized process, readiness, or internal API failure."""


class ComfyIdentityError(ComfyProcessError):
    pass


def _comfy_error():
    return ComfyProcessError("Remote ComfyUI is unavailable.")


def _finite_clock(clock):
    value = clock()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise _comfy_error()
    return float(value)


def _positive_seconds(value, message):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > 3600
    ):
        raise ValueError(message)
    return float(value)


def _runtime_environment():
    result = {
        name: value
        for name, value in os.environ.items()
        if name in _RUNTIME_ENVIRONMENT
        and isinstance(value, str)
        and value
    }
    result.setdefault("PATH", os.defpath)
    result.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return result


def _validated_system_stats(stats):
    if not isinstance(stats, dict):
        raise ComfyIdentityError(
            "Remote ComfyUI identity does not match."
        )
    system = stats.get("system")
    devices = stats.get("devices")
    if (
        not isinstance(system, dict)
        or system.get("comfyui_version")
        != PINNED_COMFYUI_CORE_VERSION
        or system.get("required_frontend_version")
        != PINNED_COMFYUI_FRONTEND_VERSION
        or not isinstance(system.get("python_version"), str)
        or not system["python_version"].startswith(
            PINNED_PYTHON_VERSION + " "
        )
        or not isinstance(devices, list)
        or not any(
            isinstance(device, dict)
            and device.get("type") == "cuda"
            for device in devices
        )
    ):
        raise ComfyIdentityError(
            "Remote ComfyUI identity does not match."
        )
    packages = system.get("comfy_package_versions")
    if not isinstance(packages, list) or len(packages) > 1000:
        raise ComfyIdentityError(
            "Remote ComfyUI identity does not match."
        )
    frontend = next(
        (
            item
            for item in packages
            if isinstance(item, dict)
            and item.get("name") == "comfyui-frontend-package"
        ),
        None,
    )
    if (
        frontend is None
        or frontend.get("installed")
        != PINNED_COMFYUI_FRONTEND_VERSION
        or frontend.get("required")
        != PINNED_COMFYUI_FRONTEND_VERSION
    ):
        raise ComfyIdentityError(
            "Remote ComfyUI identity does not match."
        )
    return stats


class AiohttpComfyHttp:
    async def get_json(self, path):
        if path not in {"/system_stats", "/object_info"}:
            raise _comfy_error()
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise _comfy_error() from None
        try:
            timeout = ClientTimeout(total=10)
            async with ClientSession(
                timeout=timeout,
                auto_decompress=False,
            ) as session:
                async with session.get(
                    _COMFY_ORIGIN + path,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise _comfy_error()
                    if response.headers.get(
                        "Content-Encoding",
                        "identity",
                    ).casefold() != "identity":
                        raise _comfy_error()
                    if response.content_length is not None and (
                        response.content_length > 64 * 1024 * 1024
                    ):
                        raise _comfy_error()
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        content.extend(chunk)
                        if len(content) > 64 * 1024 * 1024:
                            raise _comfy_error()
                    payload = json.loads(bytes(content).decode("utf-8"))
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except ComfyProcessError:
            raise
        except (UnicodeError, json.JSONDecodeError):
            raise _comfy_error() from None
        except Exception:
            raise _comfy_error() from None
        if not isinstance(payload, dict):
            raise _comfy_error()
        return payload


class ComfyProcess:
    def __init__(
        self,
        *,
        comfy_root,
        working_root,
        python_executable,
        process_factory=None,
        http=None,
        sleeper=None,
        clock=None,
        progress=None,
        startup_timeout=DEFAULT_STARTUP_TIMEOUT_SECONDS,
        stop_timeout=DEFAULT_STOP_TIMEOUT_SECONDS,
    ):
        try:
            self.comfy_root = Path(comfy_root).resolve(strict=True)
            main_path = self.comfy_root / "main.py"
            main_metadata = os.lstat(main_path)
            executable = Path(python_executable).resolve(strict=True)
            executable_metadata = os.lstat(executable)
        except (OSError, RuntimeError):
            raise ValueError("Pinned ComfyUI runtime is unavailable.") from None
        if (
            not self.comfy_root.is_dir()
            or not stat.S_ISREG(main_metadata.st_mode)
            or stat.S_ISLNK(main_metadata.st_mode)
            or not stat.S_ISREG(executable_metadata.st_mode)
            or stat.S_ISLNK(executable_metadata.st_mode)
            or not os.access(executable, os.X_OK)
        ):
            raise ValueError("Pinned ComfyUI runtime is unavailable.")
        self.main_path = main_path.resolve(strict=True)
        self.python_executable = executable

        working_path = Path(working_root)
        try:
            working_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            working_metadata = os.lstat(working_path)
            if (
                not stat.S_ISDIR(working_metadata.st_mode)
                or stat.S_ISLNK(working_metadata.st_mode)
                or working_metadata.st_uid != os.getuid()
            ):
                raise ValueError("ComfyUI working root is unavailable.")
            os.chmod(working_path, 0o700)
            self.working_root = working_path.resolve(strict=True)
        except ValueError:
            raise
        except (OSError, RuntimeError):
            raise ValueError("ComfyUI working root is unavailable.") from None

        self.process_factory = (
            process_factory or asyncio.create_subprocess_exec
        )
        if not callable(self.process_factory):
            raise ValueError("ComfyUI process factory is invalid.")
        self.http = http or AiohttpComfyHttp()
        if not callable(getattr(self.http, "get_json", None)):
            raise ValueError("ComfyUI HTTP boundary is invalid.")
        self.sleeper = sleeper or asyncio.sleep
        self.clock = clock or time.monotonic
        self.progress = progress
        self.startup_timeout = _positive_seconds(
            startup_timeout,
            "Invalid ComfyUI startup timeout.",
        )
        self.stop_timeout = _positive_seconds(
            stop_timeout,
            "Invalid ComfyUI stop timeout.",
        )
        self._process = None
        self._lock = None
        self._lock_loop = None

    def _process_lock(self):
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _progress(self, kind):
        if self.progress is None:
            return
        try:
            result = self.progress(kind)
            if inspect.isawaitable(result):
                await result
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _comfy_error() from None

    def _running(self):
        return (
            self._process is not None
            and getattr(self._process, "returncode", None) is None
        )

    async def _wait_ready(self):
        deadline = _finite_clock(self.clock) + self.startup_timeout
        while True:
            if not self._running():
                raise _comfy_error()
            try:
                stats = await self.http.get_json("/system_stats")
                stats = _validated_system_stats(stats)
                await self._progress("health")
                return stats
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except ComfyIdentityError:
                raise
            except ComfyProcessError:
                if _finite_clock(self.clock) >= deadline:
                    raise _comfy_error() from None
                await self.sleeper(1)

    async def start(self):
        async with self._process_lock():
            if self._running():
                return await self._wait_ready()
            argv = (
                str(self.python_executable),
                str(self.main_path),
                "--listen",
                COMFY_BIND_HOST,
                "--port",
                str(COMFY_BIND_PORT),
                "--disable-auto-launch",
            )
            try:
                self._process = await self.process_factory(
                    *argv,
                    cwd=str(self.working_root),
                    env=_runtime_environment(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                self._process = None
                raise _comfy_error() from None
            try:
                return await self._wait_ready()
            except BaseException:
                process = self._process
                self._process = None
                if process is not None:
                    try:
                        await self._terminate_process(process)
                    except ComfyProcessError:
                        pass
                raise

    async def ensure_running(self):
        return await self.start()

    async def _terminate_process(self, process):
        try:
            if getattr(process, "returncode", None) is None:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.stop_timeout,
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.stop_timeout,
                    )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _comfy_error() from None

    async def stop(self):
        async with self._process_lock():
            process = self._process
            if process is None:
                return
            try:
                await self._terminate_process(process)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            finally:
                self._process = None

    async def restart(self):
        await self.stop()
        return await self.start()

    async def object_info(self):
        if not self._running():
            raise _comfy_error()
        try:
            payload = await self.http.get_json("/object_info")
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _comfy_error() from None
        if (
            not isinstance(payload, dict)
            or len(payload) > 100_000
            or not all(
                isinstance(name, str)
                and 0 < len(name) <= 200
                and isinstance(value, dict)
                for name, value in payload.items()
            )
        ):
            raise _comfy_error()
        await self._progress("validation")
        return payload

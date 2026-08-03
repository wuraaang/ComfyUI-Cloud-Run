"""Fixed-boundary lifecycle for the internal native ComfyUI process."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import json
import math
import mimetypes
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import struct
import time
import uuid

from cloud_run.manifest import (
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
)
from .diagnostics import BoundedDiagnostics, ProcessDiagnostic

PINNED_PYTHON_VERSION = "3.12"
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
MAX_NATIVE_JSON_BYTES = 64 * 1024 * 1024
MAX_NATIVE_WEBSOCKET_BYTES = 16 * 1024 * 1024
_SAFE_EVENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_TERMINAL_EVENTS = {
    "execution_success",
    "execution_error",
    "execution_interrupted",
}


class ComfyProcessError(RuntimeError):
    """A sanitized process, readiness, or internal API failure."""

    def __init__(self, message="Remote ComfyUI is unavailable.", *, diagnostic=None):
        if diagnostic is not None and not isinstance(
            diagnostic,
            ProcessDiagnostic,
        ):
            raise ValueError("Invalid ComfyUI process diagnostic.")
        super().__init__(message)
        self.diagnostic = diagnostic


class ComfyIdentityError(ComfyProcessError):
    pass


class ComfyPromptValidationError(ComfyProcessError):
    def __init__(self, *, error_type=None, node_ids=()):
        super().__init__("Remote ComfyUI rejected the compiled prompt.")
        self.error_type = (
            error_type
            if isinstance(error_type, str)
            and _SAFE_EVENT.fullmatch(error_type)
            else None
        )
        self.node_ids = tuple(
            item
            for item in node_ids
            if isinstance(item, str) and _SAFE_EVENT.fullmatch(item)
        )[:1000]


@dataclass(frozen=True)
class NativeExecution:
    prompt_id: str
    terminal_event: str
    response: dict | None = None

    def __post_init__(self):
        try:
            prompt_id = str(uuid.UUID(self.prompt_id))
        except (AttributeError, TypeError, ValueError):
            raise ComfyProcessError(
                "Remote ComfyUI returned an invalid prompt identity."
            ) from None
        if (
            prompt_id != self.prompt_id
            or self.terminal_event not in _TERMINAL_EVENTS
            or (
                self.response is not None
                and (
                    not isinstance(self.response, dict)
                    or set(self.response)
                    != {"prompt_id", "number", "node_errors"}
                    or self.response.get("prompt_id") != self.prompt_id
                    or isinstance(self.response.get("number"), bool)
                    or not isinstance(
                        self.response.get("number"),
                        (int, float),
                    )
                    or not math.isfinite(self.response["number"])
                    or not isinstance(
                        self.response.get("node_errors"),
                        dict,
                    )
                )
            )
        ):
            raise ComfyProcessError(
                "Remote ComfyUI returned an invalid execution result."
            )


def _comfy_error(diagnostic=None):
    return ComfyProcessError(
        "Remote ComfyUI is unavailable.",
        diagnostic=diagnostic,
    )


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
            PINNED_PYTHON_VERSION + "."
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
    @staticmethod
    async def _response_json(response):
        if response.headers.get(
            "Content-Encoding",
            "identity",
        ).casefold() != "identity":
            raise _comfy_error()
        if response.content_length is not None and (
            response.content_length > MAX_NATIVE_JSON_BYTES
        ):
            raise _comfy_error()
        content = bytearray()
        async for chunk in response.content.iter_chunked(1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_NATIVE_JSON_BYTES:
                raise _comfy_error()
        try:
            payload = json.loads(bytes(content).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise _comfy_error() from None
        if not isinstance(payload, dict):
            raise _comfy_error()
        return payload

    async def get_json(self, path):
        if path not in {"/system_stats", "/object_info"} and not re.fullmatch(
            r"/history/[0-9a-f-]{36}",
            str(path),
        ):
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
                    payload = await self._response_json(response)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except ComfyProcessError:
            raise
        except Exception:
            raise _comfy_error() from None
        return payload

    @staticmethod
    def _text_event(data):
        if not isinstance(data, str) or len(data.encode("utf-8")) > (
            2 * 1024 * 1024
        ):
            raise _comfy_error()
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            raise _comfy_error() from None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("type"), str)
            or not isinstance(payload.get("data"), dict)
        ):
            return None
        return {
            "type": payload["type"],
            "data": payload["data"],
        }

    @staticmethod
    def _binary_event(data):
        if (
            not isinstance(data, (bytes, bytearray))
            or not 4 < len(data) <= MAX_NATIVE_WEBSOCKET_BYTES
        ):
            raise _comfy_error()
        event_type = struct.unpack(">I", data[:4])[0]
        content = bytes(data[4:])
        if event_type == 1:
            if len(content) <= 4:
                raise _comfy_error()
            image_type = struct.unpack(">I", content[:4])[0]
            mime_type = {
                1: "image/jpeg",
                2: "image/png",
            }.get(image_type)
            if mime_type is None:
                raise _comfy_error()
            return {
                "type": "b_preview",
                "data": {
                    "content": content[4:],
                    "mime_type": mime_type,
                    "metadata": {},
                },
            }
        if event_type == 3:
            if len(content) < 4:
                raise _comfy_error()
            node_length = struct.unpack(">I", content[:4])[0]
            if (
                node_length > 200
                or len(content) < 4 + node_length
            ):
                raise _comfy_error()
            try:
                node_id = content[4 : 4 + node_length].decode("utf-8")
                text = content[4 + node_length :].decode("utf-8")
            except UnicodeError:
                raise _comfy_error() from None
            return {
                "type": "progress_text",
                "data": {"node": node_id, "text": text},
            }
        if event_type == 4:
            if len(content) < 4:
                raise _comfy_error()
            metadata_length = struct.unpack(">I", content[:4])[0]
            if (
                metadata_length > 64 * 1024
                or len(content) <= 4 + metadata_length
            ):
                raise _comfy_error()
            try:
                metadata = json.loads(
                    content[4 : 4 + metadata_length].decode("utf-8")
                )
            except (UnicodeError, json.JSONDecodeError):
                raise _comfy_error() from None
            if not isinstance(metadata, dict):
                raise _comfy_error()
            mime_type = metadata.get("image_type")
            if mime_type not in {"image/jpeg", "image/png"}:
                raise _comfy_error()
            return {
                "type": "b_preview_with_metadata",
                "data": {
                    "content": content[4 + metadata_length :],
                    "mime_type": mime_type,
                    "metadata": metadata,
                },
            }
        return None

    async def execute_native(self, prompt_body, on_event):
        try:
            from aiohttp import (
                ClientSession,
                ClientTimeout,
                WSMsgType,
            )
        except ImportError:
            raise _comfy_error() from None
        if (
            not isinstance(prompt_body, dict)
            or not callable(on_event)
            or not isinstance(prompt_body.get("client_id"), str)
        ):
            raise _comfy_error()
        try:
            encoded = json.dumps(
                prompt_body,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise _comfy_error() from None
        if not 0 < len(encoded) <= MAX_NATIVE_JSON_BYTES:
            raise _comfy_error()
        timeout = ClientTimeout(
            total=None,
            sock_connect=10,
            sock_read=None,
        )
        try:
            async with ClientSession(
                timeout=timeout,
                auto_decompress=False,
            ) as session:
                async with session.ws_connect(
                    _COMFY_ORIGIN + "/ws",
                    params={"clientId": prompt_body["client_id"]},
                    max_msg_size=MAX_NATIVE_WEBSOCKET_BYTES,
                    autoping=True,
                ) as websocket:
                    await websocket.send_json(
                        {
                            "type": "feature_flags",
                            "data": {
                                "supports_preview_metadata": True,
                            },
                        }
                    )
                    async with session.post(
                        _COMFY_ORIGIN + "/prompt",
                        data=encoded,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        allow_redirects=False,
                    ) as response:
                        payload = await self._response_json(response)
                        if response.status != 200:
                            error = payload.get("error")
                            error_type = (
                                error.get("type")
                                if isinstance(error, dict)
                                else None
                            )
                            node_errors = payload.get("node_errors")
                            node_ids = (
                                tuple(node_errors)
                                if isinstance(node_errors, dict)
                                else ()
                            )
                            raise ComfyPromptValidationError(
                                error_type=error_type,
                                node_ids=node_ids,
                            )
                    prompt_id = payload.get("prompt_id")
                    try:
                        normalized_prompt_id = str(uuid.UUID(prompt_id))
                    except (AttributeError, TypeError, ValueError):
                        raise _comfy_error() from None
                    if normalized_prompt_id != prompt_id:
                        raise _comfy_error()
                    async for message in websocket:
                        if message.type == WSMsgType.TEXT:
                            event = self._text_event(message.data)
                        elif message.type == WSMsgType.BINARY:
                            event = self._binary_event(message.data)
                        elif message.type in {
                            WSMsgType.CLOSE,
                            WSMsgType.CLOSED,
                            WSMsgType.ERROR,
                        }:
                            break
                        else:
                            continue
                        if event is None:
                            continue
                        result = on_event(event)
                        if inspect.isawaitable(result):
                            await result
                        data = event["data"]
                        if (
                            event["type"] in _TERMINAL_EVENTS
                            and data.get("prompt_id") == prompt_id
                        ):
                            return NativeExecution(
                                prompt_id=prompt_id,
                                terminal_event=event["type"],
                                response=dict(payload),
                            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (ComfyProcessError, ComfyPromptValidationError):
            raise
        except Exception:
            raise _comfy_error() from None
        raise _comfy_error()


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
        output_root=None,
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
        self.output_root = Path(
            output_root
            if output_root is not None
            else self.comfy_root / "output"
        )

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
        self._reader_tasks = ()
        self._diagnostic_buffer = BoundedDiagnostics(
            local_roots=(
                self.comfy_root,
                self.working_root,
                self.output_root,
                self.python_executable,
            )
        )
        self._diagnostic_phase = "idle"
        self._last_probe = None
        self._last_exit_code = None
        self._start_count = 0
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

    def diagnostics(self):
        process = self._process
        exit_code = (
            getattr(process, "returncode", None)
            if process is not None
            else self._last_exit_code
        )
        return self._diagnostic_buffer.snapshot(
            phase=self._diagnostic_phase,
            exit_code=exit_code,
            restart_count=max(0, self._start_count - 1),
            last_probe=self._last_probe,
        )

    async def _drain_stream(self, name, stream):
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                self._diagnostic_buffer.feed(name, chunk)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            self._diagnostic_buffer.feed(
                name,
                "Diagnostic stream became unavailable.\n",
            )

    def _start_readers(self, process):
        tasks = []
        loop = asyncio.get_running_loop()
        for name in ("stdout", "stderr"):
            stream = getattr(process, name, None)
            if callable(getattr(stream, "read", None)):
                tasks.append(
                    loop.create_task(
                        self._drain_stream(name, stream),
                        name="cloud-vast-comfy-" + name,
                    )
                )
        self._reader_tasks = tuple(tasks)

    async def _finish_readers(self):
        tasks = self._reader_tasks
        self._reader_tasks = ()
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.stop_timeout,
            )
        except asyncio.TimeoutError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_ready(self):
        self._diagnostic_phase = "readiness"
        deadline = _finite_clock(self.clock) + self.startup_timeout
        while True:
            if not self._running():
                process = self._process
                self._last_exit_code = getattr(process, "returncode", None)
                self._last_probe = "ComfyUI process exited."
                raise _comfy_error(self.diagnostics())
            try:
                stats = await self.http.get_json("/system_stats")
                stats = _validated_system_stats(stats)
                self._last_probe = "System stats accepted."
                self._diagnostic_phase = "ready"
                await self._progress("health")
                return stats
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except ComfyIdentityError:
                self._last_probe = "System identity rejected."
                raise
            except ComfyProcessError:
                self._last_probe = "System stats unavailable."
                if _finite_clock(self.clock) >= deadline:
                    raise _comfy_error(self.diagnostics()) from None
                await self.sleeper(1)

    async def start(self):
        async with self._process_lock():
            if self._running():
                return await self._wait_ready()
            self._diagnostic_phase = "starting"
            self._last_probe = None
            self._last_exit_code = None
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
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._start_count += 1
                self._start_readers(self._process)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                self._process = None
                self._diagnostic_phase = "failed"
                raise _comfy_error(self.diagnostics()) from None
            try:
                return await self._wait_ready()
            except BaseException as error:
                process = self._process
                self._process = None
                if process is not None:
                    try:
                        await self._terminate_process(process)
                    except ComfyProcessError:
                        pass
                    self._last_exit_code = getattr(
                        process,
                        "returncode",
                        None,
                    )
                await self._finish_readers()
                self._diagnostic_phase = "failed"
                if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
                    raise
                if isinstance(error, ComfyIdentityError):
                    raise ComfyIdentityError(
                        "Remote ComfyUI identity does not match.",
                        diagnostic=self.diagnostics(),
                    ) from None
                if isinstance(error, ComfyProcessError):
                    raise _comfy_error(self.diagnostics()) from None
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
            self._diagnostic_phase = "stopping"
            try:
                await self._terminate_process(process)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            finally:
                self._last_exit_code = getattr(process, "returncode", None)
                self._process = None
                await self._finish_readers()
                self._diagnostic_phase = "stopped"

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

    async def execute_native(self, prompt_body, on_event):
        if not self._running():
            raise _comfy_error()
        method = getattr(self.http, "execute_native", None)
        if not callable(method):
            raise _comfy_error()
        try:
            result = await method(prompt_body, on_event)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except (ComfyPromptValidationError, ComfyProcessError):
            raise
        except Exception:
            raise _comfy_error() from None
        if not isinstance(result, NativeExecution):
            raise _comfy_error()
        return result

    async def history(self, prompt_id):
        try:
            normalized = str(uuid.UUID(prompt_id))
        except (AttributeError, TypeError, ValueError):
            raise _comfy_error() from None
        if normalized != prompt_id or not self._running():
            raise _comfy_error()
        try:
            payload = await self.http.get_json(
                "/history/" + normalized
            )
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise _comfy_error() from None
        return payload

    def output_path(self, descriptor):
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("type") != "output"
            or not isinstance(descriptor.get("filename"), str)
            or not isinstance(descriptor.get("subfolder"), str)
        ):
            raise _comfy_error()
        filename = descriptor["filename"]
        subfolder = descriptor["subfolder"]
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or any(ord(character) < 32 for character in filename)
            or len(filename.encode("utf-8")) > 1024
            or "\\" in subfolder
            or any(ord(character) < 32 for character in subfolder)
            or len(subfolder.encode("utf-8")) > 4096
        ):
            raise _comfy_error()
        relative = PurePosixPath(subfolder)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            if subfolder:
                raise _comfy_error()
            parts = ()
        else:
            parts = relative.parts
        try:
            root_metadata = os.lstat(self.output_root)
            root = self.output_root.resolve(strict=True)
            if (
                not stat.S_ISDIR(root_metadata.st_mode)
                or stat.S_ISLNK(root_metadata.st_mode)
            ):
                raise _comfy_error()
            current = self.output_root
            for part in parts:
                current = current / part
                metadata = os.lstat(current)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                ):
                    raise _comfy_error()
            candidate = current / filename
            metadata = os.lstat(candidate)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except ComfyProcessError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _comfy_error() from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
        ):
            raise _comfy_error()
        return resolved

    @staticmethod
    def output_mime_type(path):
        mime_type, _encoding = mimetypes.guess_type(str(path))
        if (
            not isinstance(mime_type, str)
            or len(mime_type) > 100
            or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime_type)
        ):
            return "application/octet-stream"
        return mime_type

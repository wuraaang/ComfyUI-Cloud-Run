"""Executable aiohttp adapter for the loopback-only Remote Worker server."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys

from .comfy import ComfyProcess
from .deadline import deadline_watchdog
from .install import CustomNodeInstaller
from .jobs import MAX_JOB_REQUEST_BYTES, JobManager
from .provision import (
    MAX_MANIFEST_REQUEST_BYTES,
    DiskReservation,
    Provisioner,
    WorkerArtifactProvider,
)
from .server import (
    WORKER_BIND_HOST,
    WORKER_BIND_PORT,
    WorkerApplication,
    WorkerFile,
)
from .state import WorkerStateStore
from .transfers import AiohttpRangeClient, TransferManager


_WORKER_VERSION = re.compile(r"[0-9a-f]{40}")
_FIXED_STATE_DIRECTORY = Path("/var/lib/comfyui-cloud-run")


def _private_directory(path):
    path = Path(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(path)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError("Worker data directory is unavailable.")
        os.chmod(path, 0o700)
        return path.resolve(strict=True)
    except ValueError:
        raise
    except (OSError, RuntimeError):
        raise ValueError("Worker data directory is unavailable.") from None


def build_worker_runtime(
    *,
    state_path,
    expected_session_id,
    comfy_root,
    data_root,
    worker_version,
    python_executable,
    transfer_client=None,
    deadline_factory=None,
):
    if (
        not isinstance(worker_version, str)
        or not _WORKER_VERSION.fullmatch(worker_version)
    ):
        raise ValueError("Reviewed worker version is unavailable.")
    data_root = _private_directory(data_root)
    artifacts_root = _private_directory(data_root / "artifacts")
    wheels_root = _private_directory(data_root / "wheels")
    working_root = _private_directory(data_root / "comfy-work")
    previews_root = _private_directory(data_root / "previews")
    state_path = Path(state_path)
    try:
        state_parent = state_path.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Worker state path is unavailable.") from None
    if state_parent != data_root or not state_path.is_absolute():
        raise ValueError("Worker state path is unavailable.")
    state_path = data_root / state_path.name

    transfer_manager = TransferManager(
        root=comfy_root,
        artifact_root=artifacts_root,
        wheel_root=wheels_root,
    )
    installer = CustomNodeInstaller(
        custom_nodes_root=Path(comfy_root) / "custom_nodes",
        wheel_root=wheels_root,
        artifact_root=artifacts_root,
    )
    comfy = ComfyProcess(
        comfy_root=comfy_root,
        working_root=working_root,
        python_executable=python_executable,
    )
    state_store = WorkerStateStore(
        state_path,
        expected_session_id=expected_session_id,
    )
    artifacts = WorkerArtifactProvider(
        transfer_manager,
        (
            transfer_client
            if transfer_client is not None
            else AiohttpRangeClient()
        ),
    )
    provisioner = Provisioner(
        state_store=state_store,
        worker_version=worker_version,
        comfy=comfy,
        artifacts=artifacts,
        installer=installer,
        disk=DiskReservation(comfy_root),
    )
    job_manager = JobManager(
        comfy=comfy,
        state=state_store,
        preview_root=previews_root,
    )
    watchdog = (
        deadline_factory(state=state_store)
        if deadline_factory is not None
        else None
    )
    return WorkerApplication(
        state_path=state_path,
        expected_session_id=expected_session_id,
        transfer_manager=transfer_manager,
        provisioner=provisioner,
        job_manager=job_manager,
        deadline_watchdog=watchdog,
    )


def build_aiohttp_application(
    *,
    worker=None,
    state_path=None,
    expected_session_id=None,
):
    try:
        from aiohttp import web
    except ImportError:
        raise RuntimeError("aiohttp is required by the Remote Worker.") from None

    if worker is None:
        worker = WorkerApplication(
            state_path=state_path,
            expected_session_id=expected_session_id,
        )
    application = web.Application(
        client_max_size=(
            max(MAX_MANIFEST_REQUEST_BYTES, MAX_JOB_REQUEST_BYTES) + 1
        )
    )

    async def handle(request):
        response = await worker.handle(request)
        if isinstance(response.payload, bytes):
            return web.Response(
                body=response.payload,
                status=response.status,
                headers=response.headers,
            )
        if isinstance(response.payload, WorkerFile):
            file_response = web.StreamResponse(
                status=response.status,
                headers=response.headers,
            )
            await file_response.prepare(request)
            descriptor = None
            try:
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(response.payload.path, flags)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size != response.payload.size_bytes
                ):
                    raise OSError("Worker output changed.")
                os.lseek(
                    descriptor,
                    response.payload.start,
                    os.SEEK_SET,
                )
                remaining = (
                    response.payload.end - response.payload.start + 1
                )
                while remaining:
                    chunk = os.read(
                        descriptor,
                        min(1024 * 1024, remaining),
                    )
                    if not chunk:
                        raise OSError("Worker output changed.")
                    remaining -= len(chunk)
                    await file_response.write(chunk)
                await file_response.write_eof()
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            return file_response
        return web.json_response(
            response.payload,
            status=response.status,
            headers=response.headers,
        )

    application.router.add_route(
        "*",
        "/{path:.*}",
        handle,
    )

    async def startup(_application):
        watchdog = getattr(worker, "deadline_watchdog", None)
        start = getattr(watchdog, "start", None)
        if callable(start):
            start()

    async def cleanup(_application):
        close = getattr(worker, "close", None)
        if callable(close):
            await close()

    application.on_startup.append(startup)
    application.on_cleanup.append(cleanup)
    return application


def parse_worker_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the loopback-only ComfyUI Cloud Run worker."
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        required=True,
    )
    arguments = parser.parse_args(argv)
    if arguments.state_directory != _FIXED_STATE_DIRECTORY:
        parser.error("the reviewed worker state directory is required")
    return arguments


def main(argv=None):
    try:
        from aiohttp import web
    except ImportError:
        raise RuntimeError("aiohttp is required by the Remote Worker.") from None
    arguments = parse_worker_arguments(argv)
    session_id = (
        os.environ.get("CLOUD_RUN_SESSION_ID", "").strip() or None
    )
    data_root = arguments.state_directory
    state_path = data_root / "worker-state.json"
    comfy_root = Path(
        os.environ.get(
            "CLOUD_RUN_COMFY_ROOT",
            "/workspace/ComfyUI",
        )
    )
    worker_version = os.environ.get(
        "CLOUD_RUN_WORKER_VERSION",
        "",
    ).strip()
    worker = build_worker_runtime(
        state_path=state_path,
        expected_session_id=session_id,
        comfy_root=comfy_root,
        data_root=data_root,
        worker_version=worker_version,
        python_executable=sys.executable,
        deadline_factory=deadline_watchdog,
    )
    application = build_aiohttp_application(
        worker=worker,
    )
    web.run_app(
        application,
        host=WORKER_BIND_HOST,
        port=WORKER_BIND_PORT,
        access_log=None,
    )


if __name__ == "__main__":
    main()

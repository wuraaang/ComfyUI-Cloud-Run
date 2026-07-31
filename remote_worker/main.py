"""Executable aiohttp adapter for the loopback-only Remote Worker server."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys

from .comfy import ComfyProcess
from .install import CustomNodeInstaller
from .provision import (
    MAX_MANIFEST_REQUEST_BYTES,
    DiskReservation,
    Provisioner,
    WorkerArtifactProvider,
)
from .server import WORKER_BIND_HOST, WORKER_BIND_PORT, WorkerApplication
from .state import WorkerStateStore
from .transfers import AiohttpRangeClient, TransferManager


_WORKER_VERSION = re.compile(r"[0-9a-f]{40}")


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
    return WorkerApplication(
        state_path=state_path,
        expected_session_id=expected_session_id,
        transfer_manager=transfer_manager,
        provisioner=provisioner,
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
        client_max_size=MAX_MANIFEST_REQUEST_BYTES + 1
    )

    async def handle(request):
        response = await worker.handle(request)
        return web.json_response(
            response.payload,
            status=response.status,
        )

    application.router.add_route(
        "*",
        "/{path:.*}",
        handle,
    )
    return application


def main():
    try:
        from aiohttp import web
    except ImportError:
        raise RuntimeError("aiohttp is required by the Remote Worker.") from None
    session_id = (
        os.environ.get("CLOUD_RUN_SESSION_ID", "").strip() or None
    )
    data_root = Path(
        os.environ.get(
            "CLOUD_RUN_DATA_ROOT",
            "/var/lib/comfyui-cloud-run",
        )
    )
    state_path = Path(
        os.environ.get(
            "CLOUD_RUN_WORKER_STATE",
            str(data_root / "worker-state.json"),
        )
    )
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

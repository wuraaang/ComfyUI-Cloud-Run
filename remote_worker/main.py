"""Executable aiohttp adapter for the loopback-only Remote Worker server."""

from __future__ import annotations

import os
from pathlib import Path

from .server import WORKER_BIND_HOST, WORKER_BIND_PORT, WorkerApplication
from .transfers import TRANSFER_CHUNK_BYTES


def build_aiohttp_application(*, state_path, expected_session_id):
    try:
        from aiohttp import web
    except ImportError:
        raise RuntimeError("aiohttp is required by the Remote Worker.") from None

    worker = WorkerApplication(
        state_path=state_path,
        expected_session_id=expected_session_id,
    )
    application = web.Application(
        client_max_size=TRANSFER_CHUNK_BYTES + 1
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
    state_path = Path(
        os.environ.get(
            "CLOUD_RUN_WORKER_STATE",
            "/var/lib/comfyui-cloud-run/worker-state.json",
        )
    )
    application = build_aiohttp_application(
        state_path=state_path,
        expected_session_id=session_id,
    )
    web.run_app(
        application,
        host=WORKER_BIND_HOST,
        port=WORKER_BIND_PORT,
        access_log=None,
    )


if __name__ == "__main__":
    main()

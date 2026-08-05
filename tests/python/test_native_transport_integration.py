import importlib.util
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest import mock


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "aiohttp unavailable")
class NativeTransportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def _loopback_server(self, application):
        from aiohttp import web

        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        return runner.addresses[0][1]

    async def _worker(self, *, transport):
        from remote_worker.native_proxy import NativeComfyProxy
        from remote_worker.server import WorkerApplication

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "private" / "worker-state.json"

        class Recorder:
            def observe_text(self, _client_id, _frame):
                return None

            def observe_binary(self, _client_id, _frame):
                return None

            def record_synchronization_failure(self, _client_id):
                return None

        worker = WorkerApplication(
            state_path=state_path,
            clock=lambda: 1000,
            native_proxy=NativeComfyProxy(
                recorder=Recorder(),
                transport=transport,
            ),
        )
        worker.state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )
        self.addAsyncCleanup(worker.close)
        return worker

    def _client(self, *, nonce=None):
        from cloud_run.worker_client import WorkerClient

        class EnvelopeTransport:
            async def request(self, _request, *, max_bytes):
                raise AssertionError("native envelope must not send HTTP")

        return WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="a" * 64,
            session_id="session-1",
            session_secret=bytes.fromhex("a" * 64),
            transport=EnvelopeTransport(),
            clock=lambda: 1000,
            nonce=nonce or (lambda: "native-transport"),
        )

    async def _native_response(self, worker, request):
        class BoundaryRequest:
            def __init__(self, worker_request):
                self.method = worker_request.method
                self.path_qs = worker_request.url.removeprefix(
                    "http://8.8.8.8:30000"
                )
                self.path = self.path_qs.split("?", 1)[0]
                self.body = worker_request.body
                self.headers = {
                    key: value
                    for key, value in worker_request.headers.items()
                    if key.casefold() != "authorization"
                }
                self.boundary_authenticated = True
                self.auth_envelope = {
                    "protocol_version": self.headers[
                        "X-Cloud-Run-Protocol-Version"
                    ],
                    "timestamp": int(
                        self.headers["X-Cloud-Run-Timestamp"]
                    ),
                    "nonce": self.headers["X-Cloud-Run-Nonce"],
                    "signature": self.headers["X-Cloud-Run-Signature"],
                }

        return await worker.handle_native(BoundaryRequest(request))

    async def test_signed_system_stats_crosses_real_worker_policy_to_loopback_comfy(
        self,
    ):
        from aiohttp import web

        from remote_worker.native_proxy import AiohttpNativeTransport

        upstream_headers = []

        async def system_stats(request):
            upstream_headers.append(dict(request.headers))
            return web.json_response({"devices": [{"name": "loopback"}]})

        application = web.Application()
        application.router.add_get("/system_stats", system_stats)
        port = await self._loopback_server(application)
        transport = AiohttpNativeTransport()
        worker = await self._worker(transport=transport)
        request = self._client().native_envelope(
            "GET",
            "/system_stats",
            b"",
            headers={"Accept": "application/json"},
        )

        with mock.patch(
            "remote_worker.native_proxy.COMFY_LOOPBACK_ORIGIN",
            "http://127.0.0.1:" + str(port),
        ):
            response = await self._native_response(worker, request)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"devices": [{"name": "loopback"}]}')
        self.assertEqual(len(upstream_headers), 1)
        for header in (
            "authorization",
            "cookie",
            "x-forwarded-for",
            "x-cloud-vast-job-id",
            "x-cloud-vast-request-id",
            "x-cloud-vast-manifest",
        ):
            self.assertNotIn(header, {key.casefold() for key in upstream_headers[0]})
        self.assertEqual(
            upstream_headers[0]["Host"],
            "127.0.0.1:" + str(port),
        )

    async def test_adjacent_backend_and_credential_routes_fail_before_upstream(self):
        from cloud_run.worker_client import WorkerClientError

        client = self._client(nonce=lambda: "controller-policy-refusal")
        rejected = (
            ("POST", "/system_stats", b""),
            ("GET", "/system_stats?debug=1", b""),
            ("GET", "/%2e%2e/system_stats", b""),
            ("GET", "/comfyui_mcp_panel/civitai", b""),
            ("GET", "/comfyui_mcp_panel/training", b""),
            ("GET", "/comfyui_mcp_panel/apps", b""),
            ("GET", "/manager/queue", b""),
            ("POST", "/api/restart", b"{}"),
            ("POST", "/reload", b"{}"),
            ("GET", "/arbitrary-backend", b""),
        )
        for method, path, body in rejected:
            with self.subTest(path=path), self.assertRaises(
                WorkerClientError
            ):
                client.native_envelope(method, path, body)
        for header in ("Authorization", "Cookie", "X-Api-Key"):
            with self.subTest(header=header), self.assertRaises(
                WorkerClientError
            ):
                client.native_envelope(
                    "GET",
                    "/system_stats",
                    b"",
                    headers={header: "must-not-cross"},
                )

        class NoUpstreamTransport:
            def __init__(inner_self):
                inner_self.calls = []

            async def request(inner_self, **kwargs):
                inner_self.calls.append(("http", kwargs))
                raise AssertionError("rejected route reached upstream")

            async def websocket(inner_self, path_qs):
                inner_self.calls.append(("websocket", path_qs))
                raise AssertionError("rejected route reached upstream")

            async def close(inner_self):
                return None

        transport = NoUpstreamTransport()
        worker = await self._worker(transport=transport)
        valid = self._client(
            nonce=lambda: "worker-policy-refusal"
        ).native_envelope("GET", "/system_stats", b"")
        for _method, path, _body in rejected:
            mutated = replace(
                valid,
                method=_method,
                url="http://8.8.8.8:30000" + path,
                body=_body,
            )
            with self.subTest(worker_path=path):
                response = await self._native_response(worker, mutated)
                self.assertEqual(response.status, 404)
        self.assertEqual(transport.calls, [])

    async def test_signed_websocket_crosses_worker_policy_and_redirect_cannot_escape(
        self,
    ):
        from aiohttp import web

        from remote_worker.native_proxy import AiohttpNativeTransport

        target_hits = []

        async def target(request):
            target_hits.append(dict(request.headers))
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            return socket

        target_application = web.Application()
        target_application.router.add_get("/target", target)
        target_port = await self._loopback_server(target_application)

        async def redirect(_request):
            raise web.HTTPFound(
                "http://127.0.0.1:" + str(target_port) + "/target"
            )

        redirect_application = web.Application()
        redirect_application.router.add_get("/ws", redirect)
        redirect_port = await self._loopback_server(redirect_application)
        transport = AiohttpNativeTransport()
        worker = await self._worker(transport=transport)
        request = self._client().native_envelope(
            "GET",
            "/ws?clientId=desktop-client-1",
            b"",
        )

        with mock.patch(
            "remote_worker.native_proxy.COMFY_LOOPBACK_ORIGIN",
            "http://127.0.0.1:" + str(redirect_port),
        ):
            response = await self._native_response(worker, request)

        self.assertEqual(response.status, 502)
        self.assertEqual(target_hits, [])

    async def test_signed_websocket_opens_and_closes_through_both_policies(self):
        from aiohttp import ClientSession, web

        from remote_worker.main import build_aiohttp_application
        from remote_worker.native_proxy import AiohttpNativeTransport

        upstream_hits = []

        async def websocket(request):
            upstream_hits.append(dict(request.headers))
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            return socket

        application = web.Application()
        application.router.add_get("/ws", websocket)
        port = await self._loopback_server(application)
        worker = await self._worker(transport=AiohttpNativeTransport())
        worker_port = await self._loopback_server(
            build_aiohttp_application(worker=worker)
        )
        worker_origin = "http://127.0.0.1:" + str(worker_port)
        request = self._client(
            nonce=lambda: "native-websocket-open",
        ).native_envelope(
            "GET",
            "/ws?clientId=desktop-client-1",
            b"",
        )
        request = replace(
            request,
            url=(
                worker_origin
                + request.url.removeprefix("http://8.8.8.8:30000")
            ),
        )
        boundary_headers = {
            key: value
            for key, value in request.headers.items()
            if key.casefold() != "authorization"
        }
        boundary_headers["X-Cloud-Run-Boundary"] = "authenticated"

        with mock.patch(
            "remote_worker.native_proxy.COMFY_LOOPBACK_ORIGIN",
            "http://127.0.0.1:" + str(port),
        ):
            async with ClientSession(trust_env=False) as session:
                async with session.ws_connect(
                    request.url,
                    headers=boundary_headers,
                    autoclose=False,
                ) as socket:
                    self.assertFalse(socket.closed)
                    await socket.close(code=1000)

        self.assertEqual(len(upstream_hits), 1)
        upstream_header_names = {
            key.casefold() for key in upstream_hits[0]
        }
        for header in (
            "authorization",
            "cookie",
            "x-cloud-run-boundary",
            "x-cloud-run-signature",
            "x-cloud-vast-job-id",
        ):
            self.assertNotIn(header, upstream_header_names)

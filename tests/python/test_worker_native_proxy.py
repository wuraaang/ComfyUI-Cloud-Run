import asyncio
import importlib.util
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from cloud_run.worker_protocol import sign_request


SESSION_SECRET = bytes.fromhex("a" * 64)
MANIFEST_DIGEST = "b" * 64
PROMPT_ID = "11111111-1111-4111-8111-111111111111"


def workflow_fixture():
    return {
        "version": 0.4,
        "nodes": [{"id": 9, "type": "SaveImage"}],
        "extra": {"frontendVersion": "1.47.10"},
    }


def prompt_body(*, seed=7):
    return {
        "client_id": "desktop-client-1",
        "prompt": {
            "9": {
                "class_type": "SaveImage",
                "inputs": {"seed": seed},
            }
        },
        "extra_data": {
            "extra_pnginfo": {"workflow": workflow_fixture()},
        },
    }


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class NativeComfy:
    async def history(self, _prompt_id):
        return {_prompt_id: {"outputs": {}}}

    def output_path(self, _descriptor):
        raise AssertionError("no output was expected")


class RecordingTransport:
    def __init__(self):
        from remote_worker.native_proxy import NativeUpstreamResponse

        self.NativeUpstreamResponse = NativeUpstreamResponse
        self.calls = []

    async def request(
        self,
        *,
        method,
        path_qs,
        body,
        headers,
        allow_redirects,
    ):
        self.calls.append(
            {
                "method": method,
                "path_qs": path_qs,
                "body": body,
                "headers": dict(headers),
                "allow_redirects": allow_redirects,
            }
        )
        if path_qs == "/prompt":
            return self.NativeUpstreamResponse(
                status=200,
                body=encoded(
                    {
                        "prompt_id": PROMPT_ID,
                        "number": 1,
                        "node_errors": {},
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
        return self.NativeUpstreamResponse(
            status=200,
            body=b"native-response",
            headers={
                "Content-Type": "application/octet-stream",
                "Set-Cookie": "must-not-cross",
                "X-Private": "must-not-cross",
            },
        )


class FakeRequest:
    def __init__(
        self,
        method,
        path_qs,
        *,
        body=b"",
        headers=None,
        boundary_authenticated=True,
        envelope=None,
    ):
        self.method = method
        self.path_qs = path_qs
        self.path = path_qs.split("?", 1)[0]
        self.body = body
        self.headers = headers or {}
        self.boundary_authenticated = boundary_authenticated
        self.auth_envelope = envelope
        self.query = {}


class RepeatedHeaders:
    def __init__(self, pairs):
        self.pairs = list(pairs)

    def items(self):
        return list(self.pairs)

    def getall(self, name):
        values = [
            value
            for key, value in self.pairs
            if key.casefold() == name.casefold()
        ]
        if not values:
            raise KeyError(name)
        return values


@dataclass
class SocketMessage:
    type: str
    data: object


class FakeSocket:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.sent_text = []
        self.sent_binary = []
        self.closed = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send_str(self, value):
        self.sent_text.append(value)

    async def send_bytes(self, value):
        self.sent_binary.append(value)

    async def close(self, *, code=1000):
        self.closed.append(code)


class RecordingObserver:
    def __init__(self):
        self.text = []
        self.binary = []
        self.sync_errors = []

    def observe_text(self, client_id, frame):
        self.text.append((client_id, frame))

    def observe_binary(self, client_id, frame):
        self.binary.append((client_id, frame))

    def record_synchronization_failure(self, client_id):
        self.sync_errors.append(client_id)


class NativeRoutePolicyTests(unittest.TestCase):
    def test_system_stats_allows_only_exact_get(self):
        from remote_worker.native_proxy import NativeRoutePolicy

        policy = NativeRoutePolicy()

        route = policy.classify("GET", "/system_stats")

        self.assertIsNotNone(route)
        self.assertEqual(route.kind, "http")
        self.assertEqual(route.path_qs, "/system_stats")
        rejected = {
            ("POST", "/system_stats"),
            ("PUT", "/system_stats"),
            ("GET", "/system_stats?"),
            ("GET", "/system_stats?detail=1"),
            ("GET", "/system_stats#fragment"),
            ("GET", "http://attacker.invalid/system_stats"),
            ("GET", "/system_stats%2f..%2fmanager"),
        }
        for method, path_qs in sorted(rejected):
            with self.subTest(method=method, path_qs=path_qs):
                self.assertIsNone(policy.classify(method, path_qs))

    def test_exact_native_surface_is_allowlisted(self):
        from remote_worker.native_proxy import NativeRoutePolicy

        policy = NativeRoutePolicy()
        allowed = {
            ("GET", "/"),
            ("GET", "/assets/index.js"),
            (
                "GET",
                "/extensions/ComfyUI-Cloud-Run/cloud-run.js",
            ),
            ("GET", "/object_info"),
            ("GET", "/models/checkpoints"),
            ("GET", "/embeddings"),
            ("GET", "/queue"),
            ("GET", "/history"),
            (
                "GET",
                "/history/00000000-0000-0000-0000-000000000001",
            ),
            ("GET", "/view?filename=x.png&type=temp"),
            ("GET", "/ws?clientId=desktop-client-1"),
            ("POST", "/prompt"),
            ("POST", "/queue"),
            ("POST", "/interrupt"),
            ("POST", "/free"),
        }
        for method, path in sorted(allowed):
            with self.subTest(method=method, path=path):
                self.assertIsNotNone(policy.classify(method, path))

    def test_provider_manager_agent_and_ambiguous_paths_are_rejected(self):
        from remote_worker.native_proxy import NativeRoutePolicy

        policy = NativeRoutePolicy()
        rejected = {
            ("GET", "/cloud-run/api/sessions"),
            ("GET", "/comfyui_mcp_panel/status"),
            ("GET", "/manager"),
            ("POST", "/v2/manager/update"),
            ("GET", "/terminal"),
            ("POST", "/prompt/../manager"),
            ("GET", "/assets/%2e%2e/manager.js"),
            ("GET", "https://attacker.example/object_info"),
            ("DELETE", "/history"),
            ("GET", "/ws?clientId=one&clientId=two"),
            ("GET", "/view?filename=../../secret&type=output"),
        }
        for method, path in sorted(rejected):
            with self.subTest(method=method, path=path):
                self.assertIsNone(policy.classify(method, path))


@unittest.skipUnless(importlib.util.find_spec("aiohttp"), "aiohttp unavailable")
class AiohttpNativeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_opens_and_closes_with_real_aiohttp_api(self):
        from aiohttp import WSMsgType, web

        from remote_worker.native_proxy import AiohttpNativeTransport

        async def websocket_handler(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            await socket.send_str("native-status")
            await socket.close()
            return socket

        application = web.Application()
        application.router.add_get("/ws", websocket_handler)
        runner = web.AppRunner(application, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.addAsyncCleanup(runner.cleanup)
        port = runner.addresses[0][1]
        transport = AiohttpNativeTransport()
        self.addAsyncCleanup(transport.close)

        with mock.patch(
            "remote_worker.native_proxy.COMFY_LOOPBACK_ORIGIN",
            "http://127.0.0.1:" + str(port),
        ):
            socket = await transport.websocket("/ws?clientId=desktop-client-1")
            message = await socket.receive()
            closed = await socket.receive()
            await socket.close()

        self.assertEqual(message.type, WSMsgType.TEXT)
        self.assertEqual(message.data, "native-status")
        self.assertIn(closed.type, {WSMsgType.CLOSE, WSMsgType.CLOSED})
        self.assertTrue(socket.closed)

    async def test_websocket_redirect_is_rejected_before_target_or_headers_are_reached(
        self,
    ):
        from aiohttp import web

        from remote_worker.native_proxy import (
            AiohttpNativeTransport,
            NativeProxyError,
        )

        target_hits = []

        async def target_handler(request):
            target_hits.append(dict(request.headers))
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            return socket

        target_application = web.Application()
        target_application.router.add_get("/target", target_handler)
        target_runner = web.AppRunner(target_application, access_log=None)
        await target_runner.setup()
        target_site = web.TCPSite(target_runner, "127.0.0.1", 0)
        await target_site.start()
        self.addAsyncCleanup(target_runner.cleanup)
        target_port = target_runner.addresses[0][1]

        async def redirect_handler(_request):
            raise web.HTTPFound(
                "http://127.0.0.1:" + str(target_port) + "/target"
            )

        redirect_application = web.Application()
        redirect_application.router.add_get("/ws", redirect_handler)
        redirect_runner = web.AppRunner(redirect_application, access_log=None)
        await redirect_runner.setup()
        redirect_site = web.TCPSite(redirect_runner, "127.0.0.1", 0)
        await redirect_site.start()
        self.addAsyncCleanup(redirect_runner.cleanup)
        redirect_port = redirect_runner.addresses[0][1]
        transport = AiohttpNativeTransport()
        self.addAsyncCleanup(transport.close)

        with mock.patch(
            "remote_worker.native_proxy.COMFY_LOOPBACK_ORIGIN",
            "http://127.0.0.1:" + str(redirect_port),
        ):
            with self.assertRaises(NativeProxyError):
                await transport.websocket("/ws?clientId=desktop-client-1")

        self.assertEqual(target_hits, [])


class NativeProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.native_jobs import NativeJobRecorder
        from remote_worker.native_proxy import NativeComfyProxy
        from remote_worker.state import WorkerStateStore

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_path = self.root / "private" / "worker-state.json"
        state = WorkerStateStore(self.state_path)
        state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )
        saved = state.load()
        state.save(
            {
                **saved,
                "installed": {"manifest_digest": MANIFEST_DIGEST},
            }
        )
        self.recorder = NativeJobRecorder(
            comfy=NativeComfy(),
            state=state,
            preview_root=self.root / "previews",
            clock=lambda: 10.0,
        )
        self.transport = RecordingTransport()
        self.proxy = NativeComfyProxy(
            recorder=self.recorder,
            transport=self.transport,
        )

    def prompt_request(self, *, body=None, headers=None):
        return FakeRequest(
            "POST",
            "/prompt",
            body=encoded(body or prompt_body()),
            headers=headers
            or {
                "X-Cloud-Vast-Job-Id": "job-1",
                "X-Cloud-Vast-Request-Id": "request-1",
                "X-Cloud-Vast-Manifest": MANIFEST_DIGEST,
                "Content-Type": "application/json",
                "Authorization": "must-not-cross",
                "Cookie": "must-not-cross",
                "X-Forwarded-For": "must-not-cross",
            },
        )

    async def test_prompt_is_recorded_then_exact_retry_reuses_native_response(self):
        first = await self.proxy.handle(self.prompt_request())
        second = await self.proxy.handle(self.prompt_request())

        self.assertEqual(first.status, 200)
        self.assertEqual(second.body, first.body)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(self.transport.calls[0]["allow_redirects"])
        forwarded_headers = self.transport.calls[0]["headers"]
        for forbidden in (
            "authorization",
            "cookie",
            "host",
            "x-forwarded-for",
            "x-cloud-vast-job-id",
            "x-cloud-vast-request-id",
            "x-cloud-vast-manifest",
        ):
            self.assertNotIn(forbidden, forwarded_headers)
        saved = self.recorder.state.job("job-1")
        self.assertEqual(saved["native_response"], json.loads(first.body))

        changed = await self.proxy.handle(
            self.prompt_request(body=prompt_body(seed=8))
        )
        self.assertEqual(changed.status, 409)
        self.assertEqual(len(self.transport.calls), 1)

    async def test_nonprompt_headers_and_response_headers_are_confined(self):
        rejected = await self.proxy.handle(
            FakeRequest(
                "GET",
                "/object_info",
                headers={"X-Cloud-Vast-Job-Id": "job-1"},
            )
        )
        accepted = await self.proxy.handle(
            FakeRequest(
                "GET",
                "/object_info",
                headers={"Accept": "application/json"},
            )
        )

        self.assertEqual(rejected.status, 400)
        self.assertEqual(accepted.status, 200)
        self.assertEqual(
            accepted.headers,
            {"Content-Type": "application/octet-stream"},
        )
        self.assertNotIn("must-not-cross", repr(accepted))

    async def test_oversized_body_and_encoded_upstream_are_rejected(self):
        from remote_worker.native_proxy import (
            MAX_NATIVE_BODY_BYTES,
            NativeUpstreamResponse,
        )

        oversized = await self.proxy.handle(
            FakeRequest(
                "GET",
                "/object_info",
                body=b"x" * (MAX_NATIVE_BODY_BYTES + 1),
            )
        )
        self.assertEqual(oversized.status, 400)
        self.assertEqual(self.transport.calls, [])

        class EncodedTransport(RecordingTransport):
            async def request(nested_self, **options):
                nested_self.calls.append(options)
                return NativeUpstreamResponse(
                    status=200,
                    body=b"compressed-without-a-safe-contract",
                    headers={
                        "Content-Type": "application/javascript",
                        "Content-Encoding": "gzip",
                    },
                )

        encoded_proxy = self.proxy.__class__(
            recorder=self.recorder,
            transport=EncodedTransport(),
        )
        encoded = await encoded_proxy.handle(FakeRequest("GET", "/"))
        self.assertEqual(encoded.status, 502)

    async def test_prompt_forward_failure_is_persisted_without_raw_error(self):
        from remote_worker.native_proxy import NativeProxyError

        marker = "upstream-secret-marker"

        class FailingTransport(RecordingTransport):
            async def request(nested_self, **options):
                nested_self.calls.append(options)
                raise NativeProxyError(marker)

        proxy = self.proxy.__class__(
            recorder=self.recorder,
            transport=FailingTransport(),
        )
        response = await proxy.handle(self.prompt_request())
        saved = self.recorder.state.job("job-1")

        self.assertEqual(response.status, 502)
        self.assertEqual(saved["state"], "failed")
        self.assertEqual(
            saved["error"]["code"],
            "synchronization_error",
        )
        self.assertNotIn(marker, repr(saved))

    async def test_websocket_upstream_frames_are_observed_then_forwarded_exactly(self):
        from remote_worker.native_proxy import NativeComfyProxy

        observer = RecordingObserver()
        proxy = NativeComfyProxy(recorder=observer, transport=self.transport)
        text = json.dumps(
            {
                "type": "progress",
                "data": {"prompt_id": PROMPT_ID, "value": 1, "max": 2},
            },
            separators=(",", ":"),
        )
        binary = b"\x00\x00\x00\x01\x00\x00\x00\x02png"
        upstream = FakeSocket(
            [SocketMessage("TEXT", text), SocketMessage("BINARY", binary)]
        )
        desktop = FakeSocket()

        await proxy.pump_upstream(
            client_id="desktop-client-1",
            upstream=upstream,
            desktop=desktop,
        )

        self.assertEqual(desktop.sent_text, [text])
        self.assertEqual(desktop.sent_binary, [binary])
        self.assertEqual(observer.text, [("desktop-client-1", text)])
        self.assertEqual(
            observer.binary,
            [("desktop-client-1", binary)],
        )

    async def test_websocket_observation_failure_is_recorded_before_forwarding(self):
        class FailingObserver(RecordingObserver):
            def observe_text(self, client_id, frame):
                raise RuntimeError("raw-secret-marker")

        observer = FailingObserver()
        proxy = self.proxy.__class__(recorder=observer, transport=self.transport)
        desktop = FakeSocket()
        frame = '{"type":"status","data":{}}'

        await proxy.pump_upstream(
            client_id="desktop-client-1",
            upstream=FakeSocket([SocketMessage("TEXT", frame)]),
            desktop=desktop,
        )

        self.assertEqual(observer.sync_errors, ["desktop-client-1"])
        self.assertEqual(desktop.sent_text, [frame])

    async def test_websocket_desktop_allows_only_pinned_client_frames(self):
        from remote_worker.native_proxy import NativeProxyValidationError

        feature = json.dumps(
            {
                "type": "feature_flags",
                "data": {"supports_preview_metadata": True},
            },
            separators=(",", ":"),
        )
        desktop = FakeSocket([SocketMessage("TEXT", feature)])
        upstream = FakeSocket()
        await self.proxy.pump_desktop(desktop=desktop, upstream=upstream)
        self.assertEqual(upstream.sent_text, [feature])

        with self.assertRaises(NativeProxyValidationError):
            await self.proxy.pump_desktop(
                desktop=FakeSocket([SocketMessage("BINARY", b"forbidden")]),
                upstream=FakeSocket(),
            )


class NativeWorkerAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.server import WorkerApplication

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []

        class Proxy:
            async def handle(nested_self, request):
                from remote_worker.native_proxy import NativeProxyResponse

                self.calls.append(request.path_qs)
                return NativeProxyResponse(
                    status=200,
                    body=b"ok",
                    headers={"Content-Type": "text/plain"},
                )

        self.worker = WorkerApplication(
            state_path=self.root / "private" / "worker-state.json",
            clock=lambda: 1000,
            native_proxy=Proxy(),
        )
        self.worker.state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )
        self.nonce = 0

    def signed_request(self, *, job_id="job-1", boundary=True):
        from cloud_run.worker_protocol import native_request_material

        body = encoded(prompt_body())
        identity = {
            "job_id": job_id,
            "request_id": "request-1",
            "manifest_digest": MANIFEST_DIGEST,
        }
        self.nonce += 1
        envelope = sign_request(
            SESSION_SECRET,
            "POST",
            "/prompt",
            native_request_material(body, identity),
            timestamp=1000,
            nonce="native-" + str(self.nonce),
        )
        return FakeRequest(
            "POST",
            "/prompt",
            body=body,
            boundary_authenticated=boundary,
            envelope=envelope,
            headers={
                "X-Cloud-Vast-Job-Id": job_id,
                "X-Cloud-Vast-Request-Id": "request-1",
                "X-Cloud-Vast-Manifest": MANIFEST_DIGEST,
            },
        )

    async def test_boundary_hmac_replay_and_identity_rewrite_fail_closed(self):
        missing_boundary = await self.worker.handle_native(
            self.signed_request(boundary=False)
        )
        accepted_request = self.signed_request()
        accepted = await self.worker.handle_native(accepted_request)
        replay = await self.worker.handle_native(accepted_request)
        rewritten = self.signed_request()
        rewritten.headers["X-Cloud-Vast-Job-Id"] = "job-2"
        altered = await self.worker.handle_native(rewritten)
        duplicate = self.signed_request()
        duplicate.headers = RepeatedHeaders(
            [
                ("X-Cloud-Vast-Job-Id", "job-1"),
                ("X-Cloud-Vast-Job-Id", "job-1"),
                ("X-Cloud-Vast-Request-Id", "request-1"),
                ("X-Cloud-Vast-Manifest", MANIFEST_DIGEST),
            ]
        )
        duplicated = await self.worker.handle_native(duplicate)
        authorized_header = self.signed_request()
        authorized_header.headers["Authorization"] = "must-not-reach-worker"
        smuggled = await self.worker.handle_native(authorized_header)

        self.assertEqual(missing_boundary.status, 401)
        self.assertEqual(accepted.status, 200)
        self.assertEqual(replay.status, 401)
        self.assertEqual(altered.status, 401)
        self.assertEqual(duplicated.status, 401)
        self.assertEqual(smuggled.status, 401)
        self.assertEqual(self.calls, ["/prompt"])
        self.assertEqual(self.calls, ["/prompt"])


if __name__ == "__main__":
    unittest.main()

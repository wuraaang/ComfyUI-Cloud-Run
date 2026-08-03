import asyncio
import json
import tempfile
import unittest
from pathlib import Path


class FakeListener:
    def __init__(self, *, selected_port=32145, occupied=()):
        self.selected_port = selected_port
        self.occupied = set(occupied)
        self.starts = []
        self.closed = False

    async def start(self, host, port, handler):
        self.starts.append((host, port, handler))
        chosen = self.selected_port if port == 0 else port
        if chosen in self.occupied:
            raise OSError("occupied private detail")
        return chosen

    async def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self):
        self.requests = []

    async def request(self, request, *, max_bytes):
        from cloud_run.worker_client import WorkerTransportResponse

        self.requests.append((request, max_bytes))
        if request.url.endswith("/prompt"):
            body = json.dumps(
                {
                    "prompt_id": "11111111-1111-4111-8111-111111111111",
                    "number": 1,
                    "node_errors": {},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            content_type = "application/json"
        else:
            body = b"native-body"
            content_type = "application/octet-stream"
        return WorkerTransportResponse(
            status=200,
            headers={
                "Content-Type": content_type,
                "Set-Cookie": "remote-cookie-must-not-cross",
            },
            body=body,
        )


class FakeWorker:
    def __init__(self):
        from cloud_run.worker_client import WorkerRequest

        self.WorkerRequest = WorkerRequest
        self.transport = FakeTransport()
        self.envelopes = []

    def native_envelope(
        self,
        method,
        path_qs,
        body,
        *,
        identity=None,
        headers=None,
    ):
        self.envelopes.append(
            (method, path_qs, body, dict(identity or {}), dict(headers or {}))
        )
        return self.WorkerRequest(
            method=method,
            url="http://worker.invalid" + path_qs,
            headers={"private": "never-returned"},
            body=body,
        )


class FakeRequest:
    def __init__(
        self,
        method,
        path_qs,
        *,
        body=b"",
        headers=None,
    ):
        self.method = method
        self.path_qs = path_qs
        self.path = path_qs.split("?", 1)[0]
        self.body = body
        self.headers = headers or {}


class FakeAgentBridge:
    def __init__(self):
        self.allowed = []
        self.opened = []
        self.revoked = []
        self.closed = False

    def allow(self, session_id):
        self.allowed.append(session_id)

    async def open(self, request, session):
        from cloud_run.desktop_relay import DesktopRelayResponse

        self.opened.append((request, session))
        return DesktopRelayResponse(299, b"agent-websocket")

    def compatibility(self, path, session):
        if path.endswith("/status"):
            return {
                "running": True,
                "bridge_url": session.websocket_url,
                "comfyui_path": "",
            }
        if path.endswith("/bridge_url"):
            return {"url": session.websocket_url}
        return {
            "backends": [{"backend": "claude", "ready": True}],
            "any_ready": True,
        }

    async def revoke(self, session_id):
        self.revoked.append(session_id)

    async def close(self):
        self.closed = True


def cookie_value(response):
    raw = response.headers.get("Set-Cookie", "")
    return raw.split(";", 1)[0].split("=", 1)[1]


class DesktopRelayPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_bind_persists_and_restart_reuses_exact_port(self):
        from cloud_run.desktop_relay import DesktopRelay
        from cloud_run.job_repository import JobRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = JobRepository(Path(temporary.name) / "attempts.sqlite3")
        first_listener = FakeListener(selected_port=32145)
        first = DesktopRelay(
            repository=repository,
            bind_host="127.0.0.1",
            port_selector=lambda: 0,
            listener_factory=lambda _handler: first_listener,
            worker_factory=lambda _session: FakeWorker(),
            native_prompt=lambda _session, _body: {},
            capability_factory=lambda: "capability-" + "a" * 48,
            clock=lambda: 100.0,
        )

        await first.start()

        self.assertEqual(first.status().url, "http://127.0.0.1:32145")
        stored = repository.get_desktop_relay()
        self.assertEqual(stored.port, 32145)
        self.assertEqual(stored.bind_host, "127.0.0.1")
        await first.close()

        second_listener = FakeListener(selected_port=40000)
        second = DesktopRelay(
            repository=repository,
            bind_host="127.0.0.1",
            port_selector=lambda: 40000,
            listener_factory=lambda _handler: second_listener,
            worker_factory=lambda _session: FakeWorker(),
            native_prompt=lambda _session, _body: {},
            clock=lambda: 101.0,
        )
        await second.start()

        self.assertEqual(second_listener.starts[0][1], 32145)
        self.assertEqual(second.status().url, "http://127.0.0.1:32145")
        await second.close()

    async def test_saved_occupied_port_fails_without_selecting_another(self):
        from cloud_run.desktop_relay import DesktopRelay, DesktopRelayConfig
        from cloud_run.job_repository import JobRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = JobRepository(Path(temporary.name) / "attempts.sqlite3")
        repository.save_desktop_relay(
            DesktopRelayConfig(
                bind_host="127.0.0.1",
                port=32145,
                active_session_id=None,
                profile_revision=None,
                updated_at=90.0,
            )
        )
        listener = FakeListener(selected_port=40000, occupied={32145})
        selected = []
        relay = DesktopRelay(
            repository=repository,
            bind_host="127.0.0.1",
            port_selector=lambda: selected.append(True) or 40000,
            listener_factory=lambda _handler: listener,
            worker_factory=lambda _session: FakeWorker(),
            native_prompt=lambda _session, _body: {},
            clock=lambda: 100.0,
        )

        await relay.start()

        self.assertFalse(relay.status().bound)
        self.assertEqual(relay.status().error, "local_port_unavailable")
        self.assertEqual(selected, [])
        self.assertEqual([item[1] for item in listener.starts], [32145])

    async def test_non_loopback_configuration_is_rejected(self):
        from cloud_run.desktop_relay import DesktopRelay, DesktopRelayError
        from cloud_run.job_repository import JobRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = JobRepository(Path(temporary.name) / "attempts.sqlite3")
        with self.assertRaises(DesktopRelayError):
            DesktopRelay(
                repository=repository,
                bind_host="0.0.0.0",
                worker_factory=lambda _session: FakeWorker(),
                native_prompt=lambda _session, _body: {},
            )


class DesktopRelayBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from cloud_run.desktop_relay import DesktopRelay
        from cloud_run.job_repository import JobRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = JobRepository(
            Path(temporary.name) / "attempts.sqlite3"
        )
        self.listener = FakeListener(selected_port=32145)
        self.worker = FakeWorker()
        self.agent_bridge = FakeAgentBridge()
        self.prompt_calls = []
        self.capability_calls = 0
        self.now = 100.0

        async def native_prompt(session_id, *, request_id, body):
            self.prompt_calls.append((session_id, request_id, body))
            return {
                "job_id": "job-1",
                "request_id": request_id,
                "manifest_digest": "b" * 64,
                "body": body,
            }

        def capability():
            self.capability_calls += 1
            return "capability-" + str(self.capability_calls).zfill(48)

        self.relay = DesktopRelay(
            repository=self.repository,
            bind_host="127.0.0.1",
            port_selector=lambda: 32145,
            listener_factory=lambda _handler: self.listener,
            worker_factory=lambda _session: self.worker,
            native_prompt=native_prompt,
            agent_bridge=self.agent_bridge,
            local_comfy_root=lambda: "/approved/comfyui",
            capability_factory=capability,
            clock=lambda: self.now,
        )
        await self.relay.start()
        self.host = {"Host": "127.0.0.1:32145"}

    async def asyncTearDown(self):
        await self.relay.close()

    async def test_lifecycle_updates_keep_a_strict_concurrency_token(self):
        initial = self.repository.get_desktop_relay()

        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        active = self.repository.get_desktop_relay()
        await self.relay.deactivate("session-1")
        inactive = self.repository.get_desktop_relay()

        self.assertGreater(active.updated_at, initial.updated_at)
        self.assertGreater(inactive.updated_at, active.updated_at)

    async def test_readiness_probes_data_plane_without_activating_or_prompting(self):
        class Socket:
            def __init__(inner_self):
                inner_self.closed = False

            async def close(inner_self, *, code):
                self.assertEqual(code, 1000)
                inner_self.closed = True

        socket = Socket()

        async def native_websocket(_request):
            return socket

        self.worker.native_websocket = native_websocket

        checks = await self.relay.probe_readiness(
            "session-1",
            self.worker,
            profile_revision=3,
            agent_required=False,
        )

        self.assertEqual(
            {item.name: item.status for item in checks},
            {
                "loopback_session_binding": "passed",
                "native_http_probe": "passed",
                "native_websocket_probe": "passed",
                "agent_panel_capabilities": "not_required",
            },
        )
        self.assertTrue(socket.closed)
        self.assertEqual(
            [(item[0], item[1]) for item in self.worker.envelopes],
            [
                ("GET", "/system_stats"),
                ("GET", "/ws?clientId=cloud-vast-readiness"),
            ],
        )
        self.assertEqual(self.prompt_calls, [])
        self.assertFalse(self.relay.status().ready)
        self.assertIsNone(
            self.repository.get_desktop_relay().active_session_id
        )

    async def test_inactive_wrong_origin_and_non_native_paths_fail_closed(self):
        inactive = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        self.assertEqual(inactive.status, 503)

        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        wrong_host = await self.relay.handle(
            FakeRequest("GET", "/", headers={"Host": "localhost:32145"})
        )
        wrong_origin = await self.relay.handle(
            FakeRequest(
                "GET",
                "/",
                headers={
                    **self.host,
                    "Origin": "https://attacker.example",
                },
            )
        )
        controller = await self.relay.handle(
            FakeRequest(
                "GET",
                "/cloud-run/api/sessions",
                headers=self.host,
            )
        )
        provider = await self.relay.handle(
            FakeRequest("GET", "/worker/v1/health", headers=self.host)
        )

        self.assertEqual(wrong_host.status, 403)
        self.assertEqual(wrong_origin.status, 403)
        self.assertEqual(controller.status, 404)
        self.assertEqual(provider.status, 404)

    async def test_navigation_mints_one_scoped_cookie_and_context_is_safe(self):
        from remote_worker.native_proxy import MAX_NATIVE_HTTP_RESPONSE_BYTES

        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        capability = cookie_value(navigation)
        missing = await self.relay.handle(
            FakeRequest("GET", "/object_info", headers=self.host)
        )
        authenticated_headers = {
            **self.host,
            "Cookie": "comfy_vast_session=" + capability,
        }
        native = await self.relay.handle(
            FakeRequest(
                "GET",
                "/object_info",
                headers=authenticated_headers,
            )
        )
        context = await self.relay.handle(
            FakeRequest(
                "GET",
                "/cloud-run/api/desktop-context",
                headers=authenticated_headers,
            )
        )

        self.assertEqual(navigation.status, 200)
        cookie = navigation.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Max-Age=", cookie)
        self.assertEqual(self.capability_calls, 1)
        self.assertEqual(missing.status, 403)
        self.assertEqual(native.status, 200)
        self.assertNotIn("Set-Cookie", native.headers)
        self.assertEqual(
            self.worker.transport.requests[-1][1],
            MAX_NATIVE_HTTP_RESPONSE_BYTES,
        )
        self.assertEqual(
            json.loads(context.body),
            {
                "role": "vast",
                "session_id": "session-1",
                "profile_revision": 3,
                "agent_bridge_url": (
                    "ws://127.0.0.1:32145/cloud-run/api/agent/ws"
                ),
            },
        )
        status = self.relay.status()
        for rendered in (repr(status), status.url, context.body.decode("utf-8")):
            self.assertNotIn(capability, rendered)

    async def test_prompt_is_prepared_before_signed_forward_and_deactivate_revokes(self):
        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        capability = cookie_value(navigation)
        headers = {
            **self.host,
            "Cookie": "comfy_vast_session=" + capability,
            "Authorization": "must-not-cross",
            "X-Forwarded-For": "must-not-cross",
            "X-Cloud-Vast-Request-Id": "request-1",
            "Content-Type": "application/json",
        }
        body = b'{"client_id":"desktop-client-1","prompt":{}}'

        response = await self.relay.handle(
            FakeRequest("POST", "/prompt", body=body, headers=headers)
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            self.prompt_calls,
            [("session-1", "request-1", body)],
        )
        envelope = self.worker.envelopes[-1]
        self.assertEqual(envelope[0:3], ("POST", "/prompt", body))
        self.assertEqual(
            envelope[3],
            {
                "job_id": "job-1",
                "request_id": "request-1",
                "manifest_digest": "b" * 64,
            },
        )
        self.assertNotIn("Authorization", envelope[4])
        self.assertNotIn("X-Forwarded-For", envelope[4])
        self.assertNotIn("X-Cloud-Vast-Request-Id", envelope[4])

        await self.relay.deactivate("session-1")
        denied = await self.relay.handle(
            FakeRequest("GET", "/object_info", headers=headers)
        )
        self.assertEqual(denied.status, 503)
        self.assertIsNone(self.relay.status().active_session_id)

        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        revoked = await self.relay.handle(
            FakeRequest("GET", "/object_info", headers=headers)
        )
        self.assertEqual(revoked.status, 403)

    async def test_expired_capability_is_rejected_and_replaced_on_navigation(self):
        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        expired = cookie_value(navigation)
        self.now = 401.0

        denied = await self.relay.handle(
            FakeRequest(
                "GET",
                "/object_info",
                headers={
                    **self.host,
                    "Cookie": "comfy_vast_session=" + expired,
                },
            )
        )
        renewed = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )

        self.assertEqual(denied.status, 403)
        self.assertEqual(renewed.status, 200)
        self.assertNotEqual(cookie_value(renewed), expired)
        self.assertEqual(self.capability_calls, 2)

    async def test_prompt_preparation_failure_is_a_local_conflict(self):
        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        capability = cookie_value(navigation)

        async def reject_prompt(_session_id, *, request_id, body):
            raise RuntimeError("private validation detail")

        self.relay.native_prompt = reject_prompt
        response = await self.relay.handle(
            FakeRequest(
                "POST",
                "/prompt",
                body=b"{}",
                headers={
                    **self.host,
                    "Cookie": "comfy_vast_session=" + capability,
                    "X-Cloud-Vast-Request-Id": "request-1",
                    "Content-Type": "application/json",
                },
            )
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(len(self.worker.envelopes), 1)
        self.assertEqual(self.worker.envelopes[0][1], "/")

    async def test_prompt_requires_one_client_identity_before_preparation(self):
        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        capability = cookie_value(navigation)
        base = {
            **self.host,
            "Cookie": "comfy_vast_session=" + capability,
            "Content-Type": "application/json",
        }

        missing = await self.relay.handle(
            FakeRequest("POST", "/prompt", body=b"{}", headers=base)
        )
        malformed = await self.relay.handle(
            FakeRequest(
                "POST",
                "/prompt",
                body=b"{}",
                headers={
                    **base,
                    "X-Cloud-Vast-Request-Id": " request-1 ",
                },
            )
        )

        self.assertEqual(missing.status, 409)
        self.assertEqual(malformed.status, 409)
        self.assertEqual(self.prompt_calls, [])
        self.assertEqual(len(self.worker.envelopes), 1)

    async def test_agent_panel_routes_are_local_scoped_and_revoked_with_session(self):
        await self.relay.activate(
            "session-1",
            self.worker,
            profile_revision=3,
        )
        navigation = await self.relay.handle(
            FakeRequest("GET", "/", headers=self.host)
        )
        capability = cookie_value(navigation)
        headers = {
            **self.host,
            "Origin": "http://127.0.0.1:32145",
            "Cookie": "comfy_vast_session=" + capability,
        }

        compatibility = {}
        for path in (
            "/comfyui_mcp_panel/status",
            "/comfyui_mcp_panel/bridge_url",
            "/comfyui_mcp_panel/backends",
        ):
            response = await self.relay.handle(
                FakeRequest("GET", path, headers=headers)
            )
            self.assertEqual(response.status, 200)
            compatibility[path] = json.loads(response.body)

        websocket = await self.relay.handle(
            FakeRequest(
                "GET",
                "/cloud-run/api/agent/ws",
                headers=headers,
            )
        )
        self.assertEqual(websocket.status, 299)
        self.assertEqual(
            compatibility["/comfyui_mcp_panel/bridge_url"]["url"],
            "ws://127.0.0.1:32145/cloud-run/api/agent/ws",
        )
        self.assertEqual(self.agent_bridge.allowed, ["session-1"])
        opened_session = self.agent_bridge.opened[0][1]
        self.assertEqual(opened_session.session_id, "session-1")
        self.assertEqual(opened_session.capability, capability)
        self.assertEqual(opened_session.local_comfy_root, "/approved/comfyui")
        self.assertNotIn(capability, repr(opened_session))
        self.assertEqual(self.worker.transport.requests[-1][0].url, "http://worker.invalid/")

        for path in (
            "/comfyui_mcp_panel/advertise_bridge",
            "/comfyui_mcp_panel/connect",
            "/comfyui_mcp_panel/disconnect",
            "/comfyui_mcp_panel/reload",
            "/comfyui_mcp_panel/restart",
            "/comfyui_mcp_panel/civitai/oauth/status",
            "/comfyui_mcp_panel/training/file",
            "/comfyui_mcp_panel/apps/run",
            "/manager/queue/install",
        ):
            with self.subTest(path=path):
                denied = await self.relay.handle(
                    FakeRequest("GET", path, headers=headers)
                )
                self.assertEqual(denied.status, 404)

        await self.relay.deactivate("session-1")
        self.assertEqual(self.agent_bridge.revoked, ["session-1"])


class NativeWorkerEnvelopeTests(unittest.TestCase):
    def test_native_envelope_signs_body_and_server_identity(self):
        from cloud_run.worker_client import WorkerClient, WorkerClientError
        from cloud_run.worker_protocol import (
            native_request_material,
            verify_request,
        )

        secret = b"s" * 32
        client = WorkerClient(
            base_url="http://8.8.8.8:8765",
            provider_token="a" * 64,
            session_id="session-1",
            session_secret=secret,
            transport=FakeTransport(),
            clock=lambda: 1000,
            nonce=lambda: "native-nonce-1",
        )
        identity = {
            "job_id": "job-1",
            "request_id": "request-1",
            "manifest_digest": "b" * 64,
        }
        body = b'{"prompt":{}}'
        request = client.native_envelope(
            "POST",
            "/prompt",
            body,
            identity=identity,
            headers={"Content-Type": "application/json"},
        )
        envelope = {
            "protocol_version": request.headers[
                "X-Cloud-Run-Protocol-Version"
            ],
            "timestamp": int(request.headers["X-Cloud-Run-Timestamp"]),
            "nonce": request.headers["X-Cloud-Run-Nonce"],
            "signature": request.headers["X-Cloud-Run-Signature"],
        }

        verify_request(
            secret,
            "POST",
            "/prompt",
            native_request_material(body, identity),
            envelope,
            now=1000,
            seen_nonces=set(),
        )
        self.assertEqual(request.headers["Authorization"], "Bearer " + "a" * 64)
        self.assertEqual(request.headers["X-Cloud-Vast-Job-Id"], "job-1")
        with self.assertRaises(WorkerClientError):
            client.native_envelope("GET", "/manager", b"")


if __name__ == "__main__":
    unittest.main()

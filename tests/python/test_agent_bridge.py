import asyncio
import json
import unittest


class FakeMessage:
    def __init__(self, kind, data):
        self.type = type("MessageType", (), {"name": kind})()
        self.data = data


class FakeSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.close_code = None
        self.prepared = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is StopAsyncIteration:
            raise StopAsyncIteration
        return item

    async def send_str(self, value):
        self.sent.append(value)

    async def close(self, *, code=1000):
        self.closed = True
        self.close_code = code
        await self.queue.put(StopAsyncIteration)

    async def prepare(self, _request):
        self.prepared = True


class FakeRequest:
    def __init__(self, *, headers=None, path_qs="/cloud-run/api/agent/ws"):
        self.method = "GET"
        self.path_qs = path_qs
        self.path = path_qs.split("?", 1)[0]
        self.headers = headers or {}


def bridge_session(*, expires_at=200.0, capability="c" * 48):
    from cloud_run.agent_bridge import AgentBridgeSession

    return AgentBridgeSession(
        session_id="session-1",
        relay_origin="http://127.0.0.1:32145",
        capability=capability,
        capability_expires_at=expires_at,
        local_comfy_root="/approved/comfyui",
    )


def authorized_headers(capability="c" * 48):
    return {
        "Host": "127.0.0.1:32145",
        "Origin": "http://127.0.0.1:32145",
        "Cookie": "comfy_vast_session=" + capability,
        "Authorization": "must-not-cross",
    }


class AgentBridgePolicyTests(unittest.TestCase):
    def test_upstream_is_literal_loopback_port_without_path_or_query(self):
        from cloud_run.agent_bridge import AgentBridgePolicy, AgentBridgeSession

        policy = AgentBridgePolicy()
        self.assertEqual(policy.upstream_url, "ws://127.0.0.1:9180")
        for rejected in (
            "ws://localhost:9180",
            "ws://127.0.0.1:9181",
            "ws://127.0.0.1:9180/bridge",
            "ws://127.0.0.1:9180?token=private",
            "wss://127.0.0.1:9180",
            "ws://127.0.0.1:9180@attacker.invalid",
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(ValueError):
                    AgentBridgePolicy(upstream_url=rejected)
        with self.assertRaises(ValueError):
            AgentBridgeSession(
                session_id="session-1",
                relay_origin="http://127.0.0.1:32145/",
                capability="c" * 48,
                capability_expires_at=200.0,
            )

    def test_hello_is_pinned_and_renderer_path_is_replaced(self):
        from cloud_run.agent_bridge import AgentBridgePolicy

        policy = AgentBridgePolicy()
        session = bridge_session()
        transformed = json.loads(
            policy.panel_to_orchestrator(
                json.dumps(
                    {
                        "type": "hello",
                        "tab_id": "tab-1",
                        "title": "Canvas",
                        "panel_version": "0.42.0",
                        "backend": "claude",
                        "blind": False,
                        "comfyui_url": session.relay_origin,
                        "comfyui_path": "/renderer/controlled/path",
                    }
                ),
                session,
            )
        )
        self.assertEqual(transformed["comfyui_url"], session.relay_origin)
        self.assertEqual(
            transformed["comfyui_path"],
            "/approved/comfyui",
        )
        self.assertNotIn("renderer/controlled", repr(transformed))

        for wrong in (
            "http://127.0.0.1:8188",
            "http://attacker.invalid",
            "file:///approved/comfyui",
        ):
            with self.subTest(wrong=wrong):
                with self.assertRaises(ValueError):
                    policy.panel_to_orchestrator(
                        json.dumps(
                            {
                                "type": "hello",
                                "tab_id": "tab-1",
                                "title": "Canvas",
                                "panel_version": "0.42.0",
                                "backend": "claude",
                                "blind": False,
                                "comfyui_url": wrong,
                            }
                        ),
                        session,
                    )

    def test_exact_graph_command_allowlist_and_general_tools_fail_closed(self):
        from cloud_run.agent_bridge import AGENT_COMMANDS, AgentBridgePolicy

        policy = AgentBridgePolicy()
        session = bridge_session()
        expected = {
            "refresh_nodes", "graph_serialize", "graph_get_state",
            "graph_view_selected", "graph_outline", "graph_query",
            "graph_find_nodes", "graph_get_subgraph", "graph_add_node",
            "graph_remove_node", "graph_clear", "graph_load", "graph_connect",
            "graph_disconnect", "graph_set_widget", "graph_set_node_property",
            "graph_move_node", "graph_resize_node", "graph_auto_layout",
            "graph_canvas", "graph_run", "graph_get_errors",
            "graph_select_nodes", "workflow_save", "workflow_save_as",
            "workflow_list", "workflow_new", "workflow_open",
            "workflow_rename", "workflow_close", "nodes_search", "nodes_list",
            "nodes_queue_status",
        }
        self.assertEqual(AGENT_COMMANDS, frozenset(expected))
        for command in sorted(expected):
            rendered = policy.orchestrator_to_panel(
                json.dumps({"rid": "request-1", "cmd": command}),
                session,
            )
            self.assertEqual(json.loads(rendered)["cmd"], command)

        rejected = (
            "request_secret",
            "call_tool",
            "manager_install",
            "manager_update",
            "restart_comfyui",
            "soft_reload",
            "open_training",
            "training_set_field",
            "download_civitai_model",
            "provider_login",
            "terminal_run",
            "read_file",
        )
        for command in rejected:
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    policy.orchestrator_to_panel(
                        json.dumps({"rid": "request-1", "cmd": command}),
                        session,
                    )

    def test_only_bounded_json_safe_panel_frames_are_accepted(self):
        from cloud_run.agent_bridge import (
            MAX_AGENT_FRAME_BYTES,
            AgentBridgePolicy,
        )

        policy = AgentBridgePolicy()
        session = bridge_session()
        accepted = (
            {"type": "title", "tab_id": "tab-1", "title": "Canvas"},
            {"type": "user_message", "text": "Improve this workflow"},
            {"type": "set_options", "model": None, "effort": "medium"},
            {"type": "new_session"},
            {"type": "interrupt", "requeue": True},
            {"type": "agent_event", "kind": "run_error", "error": "failed"},
            {"rid": "request-1", "ok": True, "result": {"queued": True}},
        )
        for frame in accepted:
            with self.subTest(frame=frame):
                self.assertEqual(
                    json.loads(
                        policy.panel_to_orchestrator(json.dumps(frame), session)
                    ),
                    frame,
                )

        rejected = (
            {"type": "request_secret", "key": "provider_token"},
            {"type": "call_tool", "tool": "save_workflow"},
            {"type": "upload_media", "data_base64": "private"},
            {"type": "set_secret", "value": "private"},
            {"type": "set_config", "custom": {"base_url": "http://127.0.0.1"}},
            {"type": "oauth_begin", "provider": "civitai"},
            {"type": "pair", "mode": "remote"},
        )
        for frame in rejected:
            with self.subTest(frame=frame):
                with self.assertRaises(ValueError):
                    policy.panel_to_orchestrator(json.dumps(frame), session)

        with self.assertRaises(ValueError):
            policy.panel_to_orchestrator("{\"type\":\"new_session\",}", session)
        with self.assertRaises(ValueError):
            policy.panel_to_orchestrator(
                json.dumps({"type": "user_message", "text": "x" * MAX_AGENT_FRAME_BYTES}),
                session,
            )

    def test_local_orchestrator_credentials_and_oauth_frames_never_cross(self):
        from cloud_run.agent_bridge import AgentBridgePolicy

        policy = AgentBridgePolicy()
        session = bridge_session()
        sanitized = json.loads(
            policy.orchestrator_to_panel(
                json.dumps(
                    {
                        "type": "backends",
                        "backends": [
                            {
                                "backend": "claude",
                                "running": True,
                                "ready": True,
                            }
                        ],
                        "any_ready": True,
                        "console_url": "http://127.0.0.1:9999",
                        "console_token": "local-long-lived-credential",
                    }
                ),
                session,
            )
        )
        self.assertEqual(set(sanitized), {"type", "backends", "any_ready"})
        self.assertNotIn("credential", repr(sanitized))
        self.assertNotIn("9999", repr(sanitized))

        for frame in (
            {"type": "ack", "kind": "oauth_begin", "device_code": "private"},
            {"type": "secret_saved", "ok": True},
            {"type": "pair_url", "url": "https://private.invalid"},
        ):
            with self.subTest(frame=frame):
                with self.assertRaises(ValueError):
                    policy.orchestrator_to_panel(json.dumps(frame), session)


class AgentBridgeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from cloud_run.agent_bridge import AgentBridge

        self.downstream = FakeSocket()
        self.upstream = FakeSocket()
        self.connect_calls = []
        self.journal = []

        async def connect(url, **kwargs):
            self.connect_calls.append((url, kwargs))
            return self.upstream

        self.bridge = AgentBridge(
            connector=connect,
            downstream_factory=lambda: self.downstream,
            journal=lambda *items: self.journal.append(items),
            clock=lambda: 100.0,
        )
        self.session = bridge_session()

    async def asyncTearDown(self):
        await self.bridge.close()

    async def test_capability_host_origin_path_and_session_are_all_bound(self):
        from cloud_run.agent_bridge import AgentBridgeAuthorizationError

        cases = (
            FakeRequest(headers={
                "Host": "127.0.0.1:32145",
                "Origin": "http://127.0.0.1:32145",
            }),
            FakeRequest(headers={**authorized_headers(), "Host": "localhost:32145"}),
            FakeRequest(headers={**authorized_headers(), "Origin": "https://evil.invalid"}),
            FakeRequest(headers=authorized_headers(), path_qs="/cloud-run/api/agent/ws?next=/"),
        )
        for request in cases:
            with self.subTest(headers=request.headers, path=request.path_qs):
                with self.assertRaises(AgentBridgeAuthorizationError):
                    await self.bridge.open(request, self.session)

        expired = bridge_session(expires_at=100.0)
        with self.assertRaises(AgentBridgeAuthorizationError):
            await self.bridge.open(FakeRequest(headers=authorized_headers()), expired)
        self.assertEqual(self.connect_calls, [])

    async def test_json_frames_cross_without_cookie_or_client_headers(self):
        request = FakeRequest(headers=authorized_headers())
        task = asyncio.create_task(self.bridge.open(request, self.session))
        await self.downstream.queue.put(
            FakeMessage(
                "TEXT",
                json.dumps(
                    {
                        "type": "hello",
                        "tab_id": "tab-1",
                        "title": "Canvas",
                        "panel_version": "0.42.0",
                        "backend": "claude",
                        "blind": False,
                        "comfyui_url": self.session.relay_origin,
                        "comfyui_path": "/renderer/path",
                    }
                ),
            )
        )
        await self.upstream.queue.put(
            FakeMessage(
                "TEXT",
                json.dumps(
                    {
                        "type": "models",
                        "models": [{"id": "safe-model", "name": "Safe"}],
                        "current": "safe-model",
                        "backend": "claude",
                    }
                ),
            )
        )
        for _ in range(20):
            if self.upstream.sent and self.downstream.sent:
                break
            await asyncio.sleep(0)

        self.assertTrue(self.downstream.prepared)
        self.assertEqual(self.connect_calls[0][0], "ws://127.0.0.1:9180")
        self.assertEqual(self.connect_calls[0][1].get("headers"), {})
        self.assertNotIn("Cookie", repr(self.connect_calls))
        self.assertNotIn("Authorization", repr(self.connect_calls))
        hello = json.loads(self.upstream.sent[0])
        self.assertEqual(hello["comfyui_url"], self.session.relay_origin)
        self.assertEqual(hello["comfyui_path"], "/approved/comfyui")
        self.assertEqual(json.loads(self.downstream.sent[0])["type"], "models")

        await self.bridge.revoke("session-1")
        await asyncio.wait_for(task, timeout=1)
        self.assertTrue(self.upstream.closed)
        self.assertTrue(self.downstream.closed)
        with self.assertRaises(Exception):
            await self.bridge.open(request, self.session)

    async def test_binary_or_forbidden_frame_closes_both_and_journals_safe_code(self):
        task = asyncio.create_task(
            self.bridge.open(
                FakeRequest(headers=authorized_headers()),
                self.session,
            )
        )
        await self.downstream.queue.put(
            FakeMessage("BINARY", b"token=must-never-be-journaled")
        )
        await asyncio.wait_for(task, timeout=1)

        self.assertTrue(self.upstream.closed)
        self.assertTrue(self.downstream.closed)
        self.assertEqual(len(self.journal), 1)
        rendered = repr(self.journal)
        self.assertIn("synchronization_error", rendered)
        self.assertNotIn("must-never-be-journaled", rendered)
        self.assertNotIn("token=", rendered)

    async def test_connection_setup_failure_is_sanitized_and_journaled(self):
        from cloud_run.agent_bridge import AgentBridge, AgentBridgeError

        records = []

        async def fail_connect(_url, **_kwargs):
            raise RuntimeError(
                "Bearer private-connection-token /Users/private/comfy"
            )

        bridge = AgentBridge(
            connector=fail_connect,
            downstream_factory=FakeSocket,
            journal=lambda *items: records.append(items),
            clock=lambda: 100.0,
        )
        self.addAsyncCleanup(bridge.close)

        with self.assertRaises(AgentBridgeError):
            await bridge.open(
                FakeRequest(headers=authorized_headers()),
                bridge_session(),
            )

        self.assertEqual(len(records), 1)
        rendered = repr(records)
        self.assertIn("synchronization_error", rendered)
        self.assertNotIn("private-connection-token", rendered)
        self.assertNotIn("/Users/private", rendered)


class AgentBridgeProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_requires_identity_read_edit_restore_run_and_ordered_batch(self):
        from cloud_run.agent_bridge import AgentBridge

        observed = []

        async def probe_driver(session, probes):
            observed.append((session.session_id, probes))
            return (
                {"type": "models", "models": [], "backend": "claude"},
                {"rid": "probe-read", "ok": True, "result": {"graph": {}}},
                {
                    "rid": "probe-edit",
                    "ok": True,
                    "result": {"observed": True, "restored": True},
                },
                {
                    "rid": "probe-run",
                    "ok": True,
                    "result": {"queued": True, "job_ids": ["job-1"]},
                },
                {
                    "rid": "probe-batch",
                    "ok": True,
                    "result": {
                        "queued": True,
                        "batch_count": 2,
                        "job_ids": ["job-2", "job-3"],
                    },
                },
            )

        bridge = AgentBridge(
            connector=lambda *_args, **_kwargs: None,
            downstream_factory=FakeSocket,
            probe_driver=probe_driver,
            clock=lambda: 100.0,
        )
        self.addAsyncCleanup(bridge.close)
        report = await bridge.probe(bridge_session())

        self.assertTrue(report.ready)
        self.assertEqual(
            report.public_payload(),
            {
                "orchestrator_identity": "passed",
                "graph_read": "passed",
                "graph_edit_restore": "passed",
                "graph_run": "passed",
                "ordered_batch": "passed",
                "ready": True,
            },
        )
        probe_commands = [item.get("cmd") for item in observed[0][1] if "cmd" in item]
        self.assertEqual(
            probe_commands,
            ["graph_get_state", "graph_set_widget", "graph_run", "graph_run"],
        )
        self.assertEqual(observed[0][1][-1]["batch_count"], 2)

        async def incomplete(_session, _probes):
            return ({"type": "ack", "kind": "degraded"},)

        bridge.probe_driver = incomplete
        failed = await bridge.probe(bridge_session())
        self.assertFalse(failed.ready)
        self.assertEqual(failed.graph_read, "failed")


if __name__ == "__main__":
    unittest.main()

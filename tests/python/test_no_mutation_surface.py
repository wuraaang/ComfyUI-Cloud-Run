import ast
import asyncio
import inspect
import unittest
from pathlib import Path

from cloud_run import vast
from cloud_run.session_service import PreflightBlocked
from remote_worker import deadline
from remote_worker.server import worker_route_set
from tests.python.test_fake_session_integration import FakeCloudRunSystem
from tests.python.test_routes import captured_handlers


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ProviderMutationSurfaceTests(unittest.TestCase):
    def test_blocked_source_first_preflight_is_read_only_and_transfer_free(self):
        system = FakeCloudRunSystem()
        self.addCleanup(system.close)
        payload = system.source_first_capture_payload()
        system.huggingface.gated = True

        capture = system.capture(payload)
        preflight = system.preflight(
            capture,
            explicit_output_allowance_bytes=4_096,
        )

        self.assertFalse(preflight.rentable)
        self.assertTrue(
            any(row.status == "unsupported" for row in preflight.rows)
        )
        with self.assertRaises(PreflightBlocked):
            asyncio.run(system.service.search(preflight.preflight_id))
        self.assertEqual(system.huggingface.metadata_calls, 2)
        self.assertTrue(
            all(
                request.startswith("https://huggingface.co/api/models/")
                for request in system.huggingface.requests
            )
        )
        self.assertEqual(system.vast.search_count, 0)
        self.assertEqual(system.vast.get_offer_count, 0)
        self.assertEqual(system.vast.create_count, 0)
        self.assertEqual(system.vast.destroy_count, 0)
        self.assertEqual(system.vast.mutations, [])
        self.assertEqual(system.worker.model_byte_calls, 0)
        self.assertEqual(system.worker.upload_offsets, {})
        self.assertEqual(system.cache_mutations, [])

    def test_repository_contract_authorizes_only_human_gated_sessions(self):
        agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        project_state = (REPOSITORY_ROOT / "docs" / "project-state.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "Current slice: local Desktop / remote GPU bridge",
            agents,
        )
        self.assertIn("No real Vast.ai rental without a separate human GO", agents)
        self.assertIn("workflow-derived reusable sessions implemented", project_state)
        self.assertIn("fake/offline", project_state)

    def test_backend_exposes_only_the_allowlisted_lifecycle_routes(self):
        self.assertEqual(
            set(captured_handlers()),
            {
                ("GET", "/cloud-run/api/settings"),
                ("PUT", "/cloud-run/api/settings"),
                ("POST", "/cloud-run/api/captures"),
                ("POST", "/cloud-run/api/preflights"),
                ("PUT", "/cloud-run/api/mappings/{mapping_id}"),
                (
                    "POST",
                    "/cloud-run/api/integrations/agent-panel/suggestions",
                ),
                (
                    "POST",
                    "/cloud-run/api/cache/artifacts/{artifact_id}",
                ),
                ("POST", "/cloud-run/api/offers"),
                ("POST", "/cloud-run/api/sessions"),
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/confirm",
                ),
                (
                    "GET",
                    "/cloud-run/api/sessions/{session_id}",
                ),
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/jobs",
                ),
                (
                    "GET",
                    "/cloud-run/api/sessions/{session_id}/jobs/{job_id}",
                ),
                (
                    "PUT",
                    "/cloud-run/api/sessions/{session_id}/deadline",
                ),
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/destroy-review",
                ),
                (
                    "DELETE",
                    "/cloud-run/api/sessions/{session_id}",
                ),
                (
                    "GET",
                    "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/events",
                ),
                (
                    "GET",
                    "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/previews/{preview_id}",
                ),
                (
                    "GET",
                    "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/artifacts/{artifact_id}",
                ),
            },
        )

    def test_agent_suggestion_route_has_no_provider_or_lifecycle_capability(self):
        handler = captured_handlers()[
            (
                "POST",
                "/cloud-run/api/integrations/agent-panel/suggestions",
            )
        ]
        source = inspect.getsource(handler).casefold()
        for forbidden in (
            "confirm",
            "destroy",
            "lifecycle",
            "offer",
            "provider",
            "vast",
        ):
            self.assertNotIn(forbidden, source)

    def test_vast_module_uses_only_the_approved_provider_origins_and_methods(self):
        source_path = REPOSITORY_ROOT / "cloud_run" / "vast.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        provider_urls = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "console.vast.ai" in node.value
        ]
        self.assertEqual(
            set(provider_urls),
            {
                "https://console.vast.ai/api/v0",
                "https://console.vast.ai/api/v1",
            },
        )
        self.assertEqual(
            vast.OFFER_SEARCH_URL,
            "https://console.vast.ai/api/v0/bundles/",
        )

        provider_http_calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "session"
            and node.func.attr in {"get", "post", "put", "patch", "delete"}
        }
        self.assertLessEqual(
            provider_http_calls,
            {"delete", "get", "post", "put"},
        )

    def test_only_the_approved_provider_action_functions_exist(self):
        production_files = [
            REPOSITORY_ROOT / "__init__.py",
            *sorted((REPOSITORY_ROOT / "cloud_run").glob("*.py")),
        ]
        production_source = "\n".join(
            path.read_text(encoding="utf-8") for path in production_files
        )
        function_names = {
            name
            for name, value in inspect.getmembers(vast, inspect.isfunction)
        }
        approved = {
            "search_offers",
            "get_offer",
            "create_instance",
            "list_instances",
            "get_instance",
            "destroy_instance",
        }
        self.assertEqual(function_names & approved, approved)
        self.assertNotIn("rent_instance", production_source)
        self.assertNotIn("run_command", production_source)

    def test_worker_exposes_only_the_reviewed_routes_and_own_delete(self):
        self.assertEqual(
            worker_route_set(),
            {
                ("GET", "/worker/v1/health"),
                ("POST", "/worker/v1/claim"),
                ("POST", "/worker/v1/manifests"),
                (
                    "GET",
                    "/worker/v1/transactions/{transaction_id}",
                ),
                ("PUT", "/worker/v1/artifacts/{artifact_id}"),
                ("GET", "/worker/v1/artifacts/{artifact_id}"),
                ("POST", "/worker/v1/jobs"),
                ("GET", "/worker/v1/jobs/{job_id}"),
                ("GET", "/worker/v1/jobs/{job_id}/events"),
                (
                    "GET",
                    "/worker/v1/jobs/{job_id}/previews/{preview_id}",
                ),
                ("PUT", "/worker/v1/deadline"),
            },
        )
        methods = {
            name
            for name, value in inspect.getmembers(
                deadline.AiohttpOwnInstanceProvider,
                inspect.isfunction,
            )
            if not name.startswith("_")
        }
        self.assertEqual(methods, {"delete"})
        source = inspect.getsource(deadline.AiohttpOwnInstanceProvider)
        self.assertIn("/api/v0/instances/", source)
        self.assertNotIn("stop", source.casefold())
        self.assertNotIn("volume", source.casefold())


if __name__ == "__main__":
    unittest.main()

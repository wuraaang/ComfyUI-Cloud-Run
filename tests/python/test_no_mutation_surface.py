import ast
import inspect
import unittest
from pathlib import Path

from cloud_run import vast
from tests.python.test_routes import captured_handlers


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ProviderMutationSurfaceTests(unittest.TestCase):
    def test_repository_contract_authorizes_only_human_gated_sessions(self):
        agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        project_state = (REPOSITORY_ROOT / "docs" / "project-state.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "Current slice: workflow-derived Vast GPU sessions",
            agents,
        )
        self.assertIn("No real Vast.ai rental without a separate human GO", agents)
        self.assertIn("managed lifecycle implemented", project_state)
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
                ("POST", "/cloud-run/api/quotes"),
                ("POST", "/cloud-run/api/sessions"),
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/confirm",
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
                ("GET", "/cloud-run/api/attempts/{attempt_id}"),
                ("POST", "/cloud-run/api/attempts/{attempt_id}/confirm"),
                ("POST", "/cloud-run/api/attempts/{attempt_id}/cancel"),
                ("DELETE", "/cloud-run/api/attempts/{attempt_id}"),
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
        self.assertEqual(
            {
                name
                for name in function_names
                if name in {
                    "create_instance",
                    "destroy_instance",
                    "get_instance",
                    "list_instances",
                }
            },
            {
                "create_instance",
                "destroy_instance",
                "get_instance",
                "list_instances",
            },
        )
        self.assertNotIn("rent_instance", production_source)
        self.assertNotIn("run_command", production_source)


if __name__ == "__main__":
    unittest.main()

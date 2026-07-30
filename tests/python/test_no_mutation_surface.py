import ast
import inspect
import unittest
from pathlib import Path

from cloud_run import vast
from tests.python.test_routes import captured_handlers


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class NoProviderMutationSurfaceTests(unittest.TestCase):
    def test_backend_exposes_only_preview_routes(self):
        self.assertEqual(
            set(captured_handlers()),
            {
                ("GET", "/cloud-run/api/settings"),
                ("PUT", "/cloud-run/api/settings"),
                ("POST", "/cloud-run/api/offers"),
            },
        )

    def test_vast_module_has_one_provider_url_and_only_post_call(self):
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
            provider_urls,
            ["https://console.vast.ai/api/v0/bundles/"],
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
        self.assertEqual(provider_http_calls, {"post"})

    def test_no_forbidden_provider_path_or_action_function_exists(self):
        production_files = [
            REPOSITORY_ROOT / "__init__.py",
            *sorted((REPOSITORY_ROOT / "cloud_run").glob("*.py")),
        ]
        production_source = "\n".join(
            path.read_text(encoding="utf-8") for path in production_files
        )
        for forbidden_path in ("/" + "asks", "/" + "instances"):
            self.assertNotIn(forbidden_path, production_source)

        action_prefixes = ("create", "rent", "start", "stop", "destroy")
        function_names = {
            name
            for name, value in inspect.getmembers(vast, inspect.isfunction)
        }
        self.assertFalse(
            any(
                name.startswith(action_prefixes)
                for name in function_names
            )
        )


if __name__ == "__main__":
    unittest.main()

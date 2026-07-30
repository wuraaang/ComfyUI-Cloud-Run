import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FakeRoutes:
    def __init__(self):
        self.registered = []

    def _register(self, method, path):
        def decorator(handler):
            self.registered.append((method, path, handler))
            return handler

        return decorator

    def get(self, path):
        return self._register("GET", path)

    def put(self, path):
        return self._register("PUT", path)

    def post(self, path):
        return self._register("POST", path)


class LoaderContractTests(unittest.TestCase):
    def test_web_only_exports_and_exact_decorator_routes(self):
        entrypoint = REPOSITORY_ROOT / "__init__.py"
        self.assertTrue(entrypoint.is_file(), "root custom-node entrypoint is missing")

        routes = FakeRoutes()
        server_module = types.ModuleType("server")
        server_module.PromptServer = types.SimpleNamespace(
            instance=types.SimpleNamespace(routes=routes)
        )

        aiohttp_module = types.ModuleType("aiohttp")
        aiohttp_module.web = types.SimpleNamespace(
            json_response=lambda payload, status=200: {
                "payload": payload,
                "status": status,
            }
        )

        package_name = "comfyui_cloud_run_loader_test"
        spec = importlib.util.spec_from_file_location(
            package_name,
            entrypoint,
            submodule_search_locations=[str(REPOSITORY_ROOT)],
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)

        prior_server = sys.modules.get("server")
        prior_aiohttp = sys.modules.get("aiohttp")
        sys.modules["server"] = server_module
        sys.modules["aiohttp"] = aiohttp_module
        try:
            package = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = package
            spec.loader.exec_module(package)
        finally:
            sys.modules.pop(package_name, None)
            for name in list(sys.modules):
                if name.startswith(package_name + "."):
                    sys.modules.pop(name, None)
            if prior_server is None:
                sys.modules.pop("server", None)
            else:
                sys.modules["server"] = prior_server
            if prior_aiohttp is None:
                sys.modules.pop("aiohttp", None)
            else:
                sys.modules["aiohttp"] = prior_aiohttp

        self.assertEqual(package.WEB_DIRECTORY, "./web")
        self.assertEqual(package.NODE_CLASS_MAPPINGS, {})
        self.assertEqual(package.NODE_DISPLAY_NAME_MAPPINGS, {})
        self.assertEqual(
            [
                (method, path)
                for method, path, _handler in routes.registered
            ],
            [
                ("GET", "/cloud-run/api/settings"),
                ("PUT", "/cloud-run/api/settings"),
                ("POST", "/cloud-run/api/offers"),
            ],
        )
        self.assertTrue(all(callable(item[2]) for item in routes.registered))


if __name__ == "__main__":
    unittest.main()

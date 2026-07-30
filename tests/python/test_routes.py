import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from cloud_run.routes import register_routes
from cloud_run.vast import OfferSearchError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status


class FakeRoutes:
    def __init__(self):
        self.handlers = {}

    def _register(self, method, path):
        def decorator(handler):
            self.handlers[(method, path)] = handler
            return handler

        return decorator

    def get(self, path):
        return self._register("GET", path)

    def put(self, path):
        return self._register("PUT", path)

    def post(self, path):
        return self._register("POST", path)


class FakeRequest:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error

    async def json(self):
        if self.error is not None:
            raise self.error
        return self.body


def captured_handlers():
    routes = FakeRoutes()
    server_module = types.ModuleType("server")
    server_module.PromptServer = types.SimpleNamespace(
        instance=types.SimpleNamespace(routes=routes)
    )
    aiohttp_module = types.ModuleType("aiohttp")
    aiohttp_module.web = types.SimpleNamespace(
        json_response=lambda payload, status=200: FakeResponse(payload, status)
    )

    prior_server = sys.modules.get("server")
    prior_aiohttp = sys.modules.get("aiohttp")
    sys.modules["server"] = server_module
    sys.modules["aiohttp"] = aiohttp_module
    try:
        register_routes()
    finally:
        if prior_server is None:
            sys.modules.pop("server", None)
        else:
            sys.modules["server"] = prior_server
        if prior_aiohttp is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = prior_aiohttp
    return routes.handlers


class SettingsRouteTests(unittest.TestCase):
    def setUp(self):
        self.handlers = captured_handlers()

    def test_get_returns_only_public_defaults(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                response = asyncio.run(
                    self.handlers[("GET", "/cloud-run/api/settings")](FakeRequest())
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.payload,
            {
                "configured": False,
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
                "official_template_id": "57808457573e32120301649763d8e019",
                "official_template_name": "Official ComfyUI",
                "preview_only": True,
            },
        )

    def test_put_saves_key_but_returns_only_public_settings(self):
        sensitive_marker = "synthetic-value"
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                put_response = asyncio.run(
                    self.handlers[("PUT", "/cloud-run/api/settings")](
                        FakeRequest(
                            {
                                "api_key": sensitive_marker,
                                "max_price_per_hour": 0.9,
                                "min_vram_gb": 24,
                            }
                        )
                    )
                )
                get_response = asyncio.run(
                    self.handlers[("GET", "/cloud-run/api/settings")](FakeRequest())
                )

        expected = {
            "configured": True,
            "max_price_per_hour": 0.9,
            "min_vram_gb": 24,
            "official_template_id": "57808457573e32120301649763d8e019",
            "official_template_name": "Official ComfyUI",
            "preview_only": True,
        }
        self.assertEqual(put_response.status, 200)
        self.assertEqual(put_response.payload, expected)
        self.assertEqual(get_response.payload, expected)
        self.assertNotIn("api_key", put_response.payload)
        self.assertNotIn(sensitive_marker, repr(put_response.payload))

    def test_put_sanitizes_invalid_json_and_validation_errors(self):
        sensitive_marker = "do-not-echo-this-marker"
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                invalid_json = asyncio.run(
                    self.handlers[("PUT", "/cloud-run/api/settings")](
                        FakeRequest(error=ValueError(sensitive_marker))
                    )
                )
                invalid_settings = asyncio.run(
                    self.handlers[("PUT", "/cloud-run/api/settings")](
                        FakeRequest(
                            {
                                "api_key": sensitive_marker * 500,
                                "max_price_per_hour": 0.9,
                                "min_vram_gb": 24,
                            }
                        )
                    )
                )

        self.assertEqual(invalid_json.status, 400)
        self.assertEqual(
            invalid_json.payload,
            {"error": "Invalid JSON body.", "preview_only": True},
        )
        self.assertEqual(invalid_settings.status, 400)
        self.assertTrue(invalid_settings.payload["error"])
        self.assertTrue(invalid_settings.payload["preview_only"])
        self.assertNotIn(sensitive_marker, repr(invalid_settings.payload))


class OffersRouteTests(unittest.TestCase):
    def setUp(self):
        self.handlers = captured_handlers()

    def test_missing_key_is_a_sanitized_client_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                response = asyncio.run(
                    self.handlers[("POST", "/cloud-run/api/offers")](FakeRequest())
                )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            response.payload,
            {
                "error": "Vast API key is not configured.",
                "preview_only": True,
            },
        )

    def test_success_uses_saved_settings_and_returns_only_sanitized_offers(self):
        offers = [
            {
                "offer_id": 42,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.42,
                "reliability": 0.99,
            }
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                asyncio.run(
                    self.handlers[("PUT", "/cloud-run/api/settings")](
                        FakeRequest(
                            {
                                "api_key": "synthetic-value",
                                "max_price_per_hour": 0.75,
                                "min_vram_gb": 24,
                            }
                        )
                    )
                )
                with mock.patch(
                    "cloud_run.routes.search_offers",
                    create=True,
                    new=mock.AsyncMock(return_value=offers),
                ) as search:
                    response = asyncio.run(
                        self.handlers[("POST", "/cloud-run/api/offers")](
                            FakeRequest()
                        )
                    )

        search.assert_awaited_once_with(
            "synthetic-value",
            max_price_per_hour=0.75,
            min_vram_gb=24,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.payload,
            {"offers": offers, "preview_only": True},
        )
        self.assertNotIn("api_key", repr(response.payload))

    def test_provider_error_is_sanitized_at_route_boundary(self):
        sensitive_marker = "do-not-echo-this-marker"
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                asyncio.run(
                    self.handlers[("PUT", "/cloud-run/api/settings")](
                        FakeRequest(
                            {
                                "api_key": "synthetic-value",
                                "max_price_per_hour": 0.75,
                                "min_vram_gb": 24,
                            }
                        )
                    )
                )
                with mock.patch(
                    "cloud_run.routes.search_offers",
                    create=True,
                    new=mock.AsyncMock(
                        side_effect=OfferSearchError(sensitive_marker)
                    ),
                ):
                    response = asyncio.run(
                        self.handlers[("POST", "/cloud-run/api/offers")](
                            FakeRequest()
                        )
                    )

        self.assertEqual(response.status, 502)
        self.assertEqual(
            response.payload,
            {
                "error": "Vast offer search is unavailable.",
                "preview_only": True,
            },
        )
        self.assertNotIn(sensitive_marker, repr(response.payload))


if __name__ == "__main__":
    unittest.main()

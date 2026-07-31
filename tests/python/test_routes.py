import asyncio
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from cloud_run.capture import CompiledCapture
from cloud_run.routes import register_routes
from cloud_run.models import AttemptState, CloudAttempt, OfferQuote
from cloud_run.service import (
    AttemptNotFound,
    CloudRunValidationError,
    QuoteUnavailable,
)
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

    def delete(self, path):
        return self._register("DELETE", path)


class FakeRequest:
    def __init__(self, body=None, error=None, match_info=None):
        self.body = body
        self.error = error
        self.match_info = match_info or {}

    async def json(self):
        if self.error is not None:
            raise self.error
        return self.body


def captured_handlers(service_factory=None):
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
        register_routes(service_factory=service_factory)
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
                "r2_configured": False,
                "hf_configured": False,
                "civitai_configured": False,
                "max_price_per_hour": 1.0,
                "min_vram_gb": 16,
                "official_template_id": "027fba7753c024be019030fb42aed900",
                "official_template_name": "Official ComfyUI",
                "lifecycle_enabled": True,
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
            "r2_configured": False,
            "hf_configured": False,
            "civitai_configured": False,
            "max_price_per_hour": 0.9,
            "min_vram_gb": 24,
            "official_template_id": "027fba7753c024be019030fb42aed900",
            "official_template_name": "Official ComfyUI",
            "lifecycle_enabled": True,
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
            {"error": "Invalid JSON body."},
        )
        self.assertEqual(invalid_settings.status, 400)
        self.assertTrue(invalid_settings.payload["error"])
        self.assertNotIn(sensitive_marker, repr(invalid_settings.payload))


class OffersRouteTests(unittest.TestCase):
    def setUp(self):
        self.handlers = captured_handlers()

    def test_missing_preflight_blocks_before_provider_configuration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                response = asyncio.run(
                    self.handlers[("POST", "/cloud-run/api/offers")](
                        FakeRequest({"preflight_id": "preflight-1"})
                    )
                )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            response.payload,
            {"error": "Cloud Run preflight was not found."},
        )

    def test_success_returns_only_the_service_sanitized_offers(self):
        offers = [
            {
                "offer_id": 42,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.42,
                "reliability": 0.99,
            }
        ]
        service = mock.Mock()
        service.search = mock.AsyncMock(return_value=offers)
        handlers = captured_handlers(service_factory=lambda: service)
        response = asyncio.run(
            handlers[("POST", "/cloud-run/api/offers")](
                FakeRequest({"preflight_id": "preflight-1"})
            )
        )

        service.search.assert_awaited_once_with("preflight-1")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, {"offers": offers})
        self.assertNotIn("api_key", repr(response.payload))

    def test_provider_error_is_sanitized_at_route_boundary(self):
        sensitive_marker = "do-not-echo-this-marker"
        service = mock.Mock()
        service.search = mock.AsyncMock(
            side_effect=OfferSearchError(sensitive_marker)
        )
        handlers = captured_handlers(service_factory=lambda: service)
        response = asyncio.run(
            handlers[("POST", "/cloud-run/api/offers")](
                FakeRequest({"preflight_id": "preflight-1"})
            )
        )

        self.assertEqual(response.status, 502)
        self.assertEqual(
            response.payload, {"error": "Vast offer search is unavailable."}
        )
        self.assertNotIn(sensitive_marker, repr(response.payload))


class CaptureRouteTests(unittest.TestCase):
    def test_capture_route_persists_native_payload_without_provider_call(self):
        class FakeCaptureService:
            def __init__(self):
                self.provider_mutations = []
                self.received = None

            async def capture(self, payload):
                self.received = payload
                return CompiledCapture.from_payload(payload)

        service = FakeCaptureService()
        handlers = captured_handlers(service_factory=lambda: service)
        payload = {
            "workflow": {
                "version": 1,
                "nodes": [{"id": 1}],
                "extra": {"frontendVersion": "1.47.10"},
            },
            "output": {
                "1": {
                    "class_type": "KSampler",
                    "inputs": {"seed": 7},
                }
            },
            "queue_options": {},
        }

        response = asyncio.run(
            handlers[("POST", "/cloud-run/api/captures")](
                FakeRequest(payload)
            )
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["status"], "captured")
        self.assertEqual(service.received, payload)
        self.assertNotIn("workflow", response.payload)
        self.assertNotIn("output", response.payload)
        self.assertEqual(service.provider_mutations, [])


class PreflightRouteTests(unittest.TestCase):
    def test_preflight_mapping_and_agent_routes_are_typed_and_provider_free(self):
        preflight = types.SimpleNamespace(
            public_payload=lambda: {
                "preflight_id": "preflight-1",
                "capture_id": "capture-1",
                "rows": [],
                "rentable": False,
                "manifest_digest": None,
                "transfer_bytes": 0,
                "output_allowance_bytes": None,
                "disk_gb": None,
            }
        )
        service = mock.Mock()
        service.preflight = mock.AsyncMock(return_value=preflight)
        service.approve_mapping = mock.AsyncMock(
            return_value={
                "class_type": "Fancy",
                "source_kind": "approved",
                "approved": True,
            }
        )
        service.register_agent_suggestion = mock.AsyncMock(
            return_value={
                "class_type": "Fancy",
                "source_kind": "agent",
                "approved": False,
            }
        )
        service.provider_mutations = []
        handlers = captured_handlers(service_factory=lambda: service)

        preflight_response = asyncio.run(
            handlers[("POST", "/cloud-run/api/preflights")](
                FakeRequest(
                    {
                        "capture_id": "capture-1",
                        "explicit_output_allowance_bytes": 1024,
                    }
                )
            )
        )
        mapping_response = asyncio.run(
            handlers[
                ("PUT", "/cloud-run/api/mappings/{mapping_id}")
            ](
                FakeRequest(
                    {"candidate_digest": "a" * 64},
                    match_info={"mapping_id": "Fancy"},
                )
            )
        )
        suggestion = {
            "class_type": "Fancy",
            "candidate": {
                "repository_url": "https://github.com/acme/fancy",
                "revision": "b" * 40,
            },
        }
        agent_response = asyncio.run(
            handlers[
                (
                    "POST",
                    "/cloud-run/api/integrations/agent-panel/suggestions",
                )
            ](FakeRequest(suggestion))
        )

        service.preflight.assert_awaited_once_with(
            "capture-1",
            explicit_output_allowance_bytes=1024,
        )
        service.approve_mapping.assert_awaited_once_with(
            "Fancy",
            "a" * 64,
        )
        service.register_agent_suggestion.assert_awaited_once_with(
            suggestion
        )
        self.assertEqual(preflight_response.status, 200)
        self.assertEqual(mapping_response.status, 200)
        self.assertEqual(agent_response.status, 202)
        self.assertFalse(agent_response.payload["approved"])
        self.assertEqual(service.provider_mutations, [])

    def test_preflight_routes_reject_extra_or_missing_fields(self):
        service = mock.Mock()
        service.preflight = mock.AsyncMock()
        service.approve_mapping = mock.AsyncMock()
        service.register_agent_suggestion = mock.AsyncMock()
        handlers = captured_handlers(service_factory=lambda: service)

        responses = [
            asyncio.run(
                handlers[("POST", "/cloud-run/api/preflights")](
                    FakeRequest(
                        {"capture_id": "capture-1", "unexpected": True}
                    )
                )
            ),
            asyncio.run(
                handlers[
                    ("PUT", "/cloud-run/api/mappings/{mapping_id}")
                ](
                    FakeRequest(
                        {},
                        match_info={"mapping_id": "Fancy"},
                    )
                )
            ),
            asyncio.run(
                handlers[
                    (
                        "POST",
                        "/cloud-run/api/integrations/agent-panel/suggestions",
                    )
                ](
                    FakeRequest(
                        {
                            "class_type": "Fancy",
                            "candidate": {},
                            "command": "forbidden",
                        }
                    )
                )
            ),
        ]

        self.assertTrue(all(response.status == 400 for response in responses))
        service.preflight.assert_not_awaited()
        service.approve_mapping.assert_not_awaited()
        service.register_agent_suggestion.assert_not_awaited()


class CacheRouteTests(unittest.TestCase):
    def test_cache_route_requires_exact_ack_and_returns_no_signed_url(self):
        result = types.SimpleNamespace(
            public_payload=lambda: {
                "artifact_id": "model-" + "a" * 64,
                "status": "cached",
                "size_bytes": 42,
                "sha256": "a" * 64,
            }
        )
        service = mock.Mock()
        service.populate_cache = mock.AsyncMock(return_value=result)
        handlers = captured_handlers(service_factory=lambda: service)
        path = "/cloud-run/api/cache/artifacts/{artifact_id}"

        response = asyncio.run(
            handlers[("POST", path)](
                FakeRequest(
                    {"acknowledged": True},
                    match_info={"artifact_id": "model-" + "a" * 64},
                )
            )
        )
        rejected = asyncio.run(
            handlers[("POST", path)](
                FakeRequest(
                    {"acknowledged": False, "extra": True},
                    match_info={"artifact_id": "model-" + "a" * 64},
                )
            )
        )

        service.populate_cache.assert_awaited_once_with(
            "model-" + "a" * 64,
            acknowledged=True,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, result.public_payload())
        self.assertNotIn("url", repr(response.payload).casefold())
        self.assertEqual(rejected.status, 400)


def attempt(state=AttemptState.OFFER_SELECTED):
    return CloudAttempt.new(
        idempotency_key="browser-idempotency-key",
        attempt_id="attempt-1",
        state=state,
        now=100.0,
        quote=OfferQuote(
            offer_id="42",
            gpu_name="RTX 4090",
            gpu_ram_gb=24.0,
            dph_total=0.42,
            reliability=0.99,
            max_price_per_hour=0.55,
            expires_at=160.0,
        ),
    )


class LifecycleRouteTests(unittest.TestCase):
    def test_quote_confirm_and_get_attempt_are_same_origin_sanitized_routes(self):
        service = mock.Mock()
        quoted = attempt()
        started = quoted.transition(
            AttemptState.CONFIRMING,
            now=101.0,
        ).transition(
            AttemptState.CREATING,
            now=102.0,
        ).transition(
            AttemptState.STARTING,
            now=103.0,
            instance_id="instance-9",
        )
        service.preview_offer = mock.AsyncMock(return_value=quoted)
        service.confirm = mock.AsyncMock(return_value=started)
        service.refresh = mock.AsyncMock(return_value=started)
        service.cancel = mock.AsyncMock(
            return_value=started.transition(
                AttemptState.CANCEL_REQUESTED,
                now=104.0,
                cancel_requested=True,
            )
        )
        service.destroy = mock.AsyncMock(
            return_value=started.transition(
                AttemptState.DESTROYING,
                now=104.0,
            ).transition(
                AttemptState.CANCELLED,
                now=105.0,
                instance_id=None,
            )
        )
        handlers = captured_handlers(service_factory=lambda: service)

        quote_response = asyncio.run(
            handlers[("POST", "/cloud-run/api/quotes")](
                FakeRequest(
                    {
                        "offer_id": 42,
                        "idempotency_key": "browser-idempotency-key",
                    }
                )
            )
        )
        confirm_response = asyncio.run(
            handlers[
                ("POST", "/cloud-run/api/attempts/{attempt_id}/confirm")
            ](
                FakeRequest(
                    {"idempotency_key": "browser-idempotency-key"},
                    match_info={"attempt_id": "attempt-1"},
                )
            )
        )
        get_response = asyncio.run(
            handlers[("GET", "/cloud-run/api/attempts/{attempt_id}")](
                FakeRequest(match_info={"attempt_id": "attempt-1"})
            )
        )
        cancel_response = asyncio.run(
            handlers[
                ("POST", "/cloud-run/api/attempts/{attempt_id}/cancel")
            ](
                FakeRequest(match_info={"attempt_id": "attempt-1"})
            )
        )
        destroy_response = asyncio.run(
            handlers[("DELETE", "/cloud-run/api/attempts/{attempt_id}")](
                FakeRequest(match_info={"attempt_id": "attempt-1"})
            )
        )

        service.preview_offer.assert_awaited_once_with(
            offer_id=42,
            idempotency_key="browser-idempotency-key",
        )
        service.confirm.assert_awaited_once_with(
            "attempt-1",
            idempotency_key="browser-idempotency-key",
        )
        service.refresh.assert_awaited_once_with("attempt-1")
        service.cancel.assert_awaited_once_with("attempt-1")
        service.destroy.assert_awaited_once_with("attempt-1")
        self.assertEqual(quote_response.status, 200)
        self.assertEqual(confirm_response.payload["status"], "starting")
        self.assertEqual(get_response.payload["instance_id"], "instance-9")
        self.assertEqual(cancel_response.payload["status"], "cancel_requested")
        self.assertEqual(destroy_response.payload["status"], "cancelled")
        for response in (
            quote_response,
            confirm_response,
            get_response,
            cancel_response,
            destroy_response,
        ):
            self.assertEqual(
                response.payload["official_template_id"],
                "027fba7753c024be019030fb42aed900",
            )
            self.assertNotIn("idempotency", repr(response.payload))

    def test_lifecycle_routes_validate_json_and_map_safe_service_errors(self):
        service = mock.Mock()
        service.preview_offer = mock.AsyncMock(
            side_effect=CloudRunValidationError("Invalid confirmation.")
        )
        service.confirm = mock.AsyncMock(
            side_effect=QuoteUnavailable("The quote expired.")
        )
        service.refresh = mock.AsyncMock(
            side_effect=AttemptNotFound("Attempt not found.")
        )
        service.cancel = mock.AsyncMock(
            side_effect=AttemptNotFound("Attempt not found.")
        )
        service.destroy = mock.AsyncMock(
            side_effect=AttemptNotFound("Attempt not found.")
        )
        handlers = captured_handlers(service_factory=lambda: service)

        invalid_json = asyncio.run(
            handlers[("POST", "/cloud-run/api/quotes")](
                FakeRequest(error=ValueError("sensitive"))
            )
        )
        invalid_payload = asyncio.run(
            handlers[("POST", "/cloud-run/api/quotes")](
                FakeRequest({"offer_id": 42, "unexpected": "sensitive"})
            )
        )
        expired = asyncio.run(
            handlers[
                ("POST", "/cloud-run/api/attempts/{attempt_id}/confirm")
            ](
                FakeRequest(
                    {"idempotency_key": "browser-idempotency-key"},
                    match_info={"attempt_id": "attempt-1"},
                )
            )
        )
        missing = asyncio.run(
            handlers[("GET", "/cloud-run/api/attempts/{attempt_id}")](
                FakeRequest(match_info={"attempt_id": "missing"})
            )
        )

        self.assertEqual(invalid_json.status, 400)
        self.assertEqual(invalid_payload.status, 400)
        self.assertEqual(expired.status, 409)
        self.assertEqual(missing.status, 404)
        self.assertNotIn(
            "sensitive",
            repr(
                [
                    invalid_json.payload,
                    invalid_payload.payload,
                    expired.payload,
                    missing.payload,
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()

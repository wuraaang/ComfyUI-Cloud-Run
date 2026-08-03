import asyncio
from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from cloud_run.artifacts import FileInputMetadata
from cloud_run.capture import CompiledCapture
from cloud_run.dependency_repository import DependencyRepository
from cloud_run.manifest import ArtifactSpec, DependencyManifest, SourceSpec
from cloud_run.model_sources import ModelSourceResolution
from cloud_run.routes import (
    _RuntimeResolver,
    _runtime_resolution_context,
    build_service,
    register_routes,
)
from cloud_run.repository import PaidRentalConflict
from cloud_run.models import (
    AttemptState,
    CloudAttempt,
    CloudJob,
    CloudSession,
    JobState,
    OfferQuote,
    SessionState,
    TransferState,
)
from cloud_run.vast import OfferSearchError
from cloud_run.worker_release import WorkerRelease


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}


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
    def __init__(
        self,
        body=None,
        error=None,
        match_info=None,
        query=None,
    ):
        self.body = body
        self.error = error
        self.match_info = match_info or {}
        self.query = query or {}

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
        json_response=lambda payload, status=200: FakeResponse(
            payload,
            status,
        ),
        Response=lambda body=b"", status=200, headers=None: FakeResponse(
            body,
            status,
            headers,
        ),
        FileResponse=lambda path, status=200, headers=None: FakeResponse(
            Path(path).read_bytes(),
            status,
            headers,
        ),
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
                "worker_release": None,
                "active_sessions": [],
            },
        )

    def test_get_returns_only_the_loaded_worker_release_record(self):
        release = WorkerRelease.from_payload(
            {
                "schema_version": 1,
                "template_hash_id": "1" * 32,
                "worker_commit": "a" * 40,
                "worker_archive_sha256": "b" * 64,
                "protocol_version": "2",
                "comfyui_core_version": "0.29.0",
                "comfyui_frontend_version": "1.47.10",
                "python_version": "3.12",
                "worker_port": 8765,
            }
        )
        private_markers = (
            "/private/worker-release.json",
            "https://signed.example/private-worker.tar.gz",
            "credential-marker",
            "a" * 64,
            "session-secret-marker",
            "private-workflow-marker",
            "private-model-marker",
            "private-template-payload-marker",
        )
        service = types.SimpleNamespace(
            release=release,
            lock_path=private_markers[0],
            archive_url=private_markers[1],
            api_key=private_markers[2],
            provider_token=private_markers[3],
            session_secret=private_markers[4],
            workflow=private_markers[5],
            model=private_markers[6],
            template_request=private_markers[7],
        )
        handlers = captured_handlers(service_factory=lambda: service)

        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                response = asyncio.run(
                    handlers[("GET", "/cloud-run/api/settings")](
                        FakeRequest()
                    )
                )

        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.payload["worker_release"],
            release.to_record(),
        )
        rendered = repr(response.payload)
        for marker in private_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, rendered)

    def test_get_rediscovers_recovered_sessions_without_browser_storage(self):
        from cloud_run.job_repository import JobRepository
        from cloud_run.repository import SessionRepository

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = root / "private" / "sessions.sqlite3"
            sessions = SessionRepository(database)
            jobs = JobRepository(database)
            ready = CloudSession.new(
                "private-session-key",
                session_id="session-recovered",
                manifest_digest="c" * 64,
                deadline_at=7_300.0,
                deadline_mode="finite",
                disk_gb=80,
                now=100.0,
                state=SessionState.READY,
            ).transition(
                SessionState.READY,
                now=100.0,
                instance_id="77",
                provider_token="a" * 64,
                session_secret_hex="d" * 64,
            )
            sessions.create_or_get(ready)
            service = types.SimpleNamespace(
                session_repository=sessions,
                job_repository=jobs,
                session_service=types.SimpleNamespace(
                    alerts=lambda session, now: []
                ),
                clock=lambda: 200.0,
            )
            handlers = captured_handlers(
                service_factory=lambda: service
            )
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": str(root / "settings")},
                clear=False,
            ):
                response = asyncio.run(
                    handlers[("GET", "/cloud-run/api/settings")](
                        FakeRequest()
                    )
                )

        active = response.payload["active_sessions"]
        self.assertEqual(
            [session["session_id"] for session in active],
            ["session-recovered"],
        )
        self.assertEqual(active[0]["status"], "ready")
        self.assertNotIn("private-session-key", repr(active))
        self.assertNotIn("a" * 64, repr(active))
        self.assertNotIn("d" * 64, repr(active))

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
            "worker_release": None,
            "active_sessions": [],
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


class ServiceConstructionTests(unittest.TestCase):
    def test_missing_private_release_lock_constructs_a_non_rentable_service(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.dict(
                os.environ,
                {"COMFYUI_CLOUD_RUN_DATA_DIR": temporary_directory},
                clear=False,
            ):
                service = build_service()

        self.assertIsNone(service.release)
        self.assertIsNone(service.session_service.release)


class RuntimeResolverTests(unittest.TestCase):
    def test_runtime_context_keeps_category_when_local_listing_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_root = root / "models"
            input_root = root / "input"
            model_root.mkdir()
            input_root.mkdir()

            class FakeHost:
                def file_input_metadata(self, capture, *, model_filenames):
                    self.model_filenames = model_filenames
                    return {}

            host = FakeHost()
            folder_paths = types.ModuleType("folder_paths")
            folder_paths.folder_names_and_paths = {
                "upscale_models": ((str(model_root),), {".pth"})
            }

            def unavailable_listing(_category):
                raise OSError("model directory is unavailable")

            folder_paths.get_filename_list = unavailable_listing
            folder_paths.get_input_directory = lambda: str(input_root)

            with mock.patch.dict(
                sys.modules,
                {"folder_paths": folder_paths},
            ):
                context = _runtime_resolution_context(host)(
                    types.SimpleNamespace()
                )

        self.assertEqual(host.model_filenames, {"upscale_models": set()})
        self.assertEqual(
            context["model_roots"],
            {"upscale_models": (model_root,)},
        )

    def test_injected_model_source_resolver_keeps_route_tests_offline(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_root = root / "models"
            input_root = root / "input"
            model_root.mkdir()
            input_root.mkdir()
            repository = DependencyRepository(
                root / "private" / "sessions.sqlite3"
            )
            revision = "a" * 40
            digest = "c" * 64
            model_source_resolver = types.SimpleNamespace(
                resolve=mock.AsyncMock(
                    return_value={
                        ("1", "model_name"): ModelSourceResolution(
                            status="resolved",
                            source=SourceSpec(
                                kind="huggingface",
                                locator=(
                                    "https://huggingface.co/example/model/"
                                    "resolve/"
                                    + revision
                                    + "/files/example.safetensors"
                                ),
                                immutable_revision=revision,
                            ),
                            size_bytes=4096,
                            sha256=digest,
                            reason=None,
                        )
                    }
                )
            )

            class FakeHost:
                def assert_compatible(self):
                    return None

                def describe_node(self, class_type):
                    return types.SimpleNamespace(kind="core")

                def file_input_metadata(self, capture, *, model_filenames):
                    self.model_filenames = model_filenames
                    return {
                        "UNETLoader": {
                            "model_name": FileInputMetadata(
                                kind="model",
                                category="diffusion_models",
                            )
                        }
                    }

            host = FakeHost()
            folder_paths = types.ModuleType("folder_paths")
            folder_paths.folder_names_and_paths = {
                "diffusion_models": ((str(model_root),), {".safetensors"})
            }
            folder_paths.get_filename_list = lambda _category: []
            folder_paths.get_input_directory = lambda: str(input_root)
            capture = types.SimpleNamespace(
                executable_class_types=("UNETLoader",),
                output={
                    "1": {
                        "class_type": "UNETLoader",
                        "inputs": {"model_name": "example.safetensors"},
                    }
                },
                workflow={"nodes": []},
            )
            resolver = _RuntimeResolver(
                repository,
                model_source_resolver=model_source_resolver,
            )

            with mock.patch.dict(
                sys.modules,
                {"folder_paths": folder_paths},
            ), mock.patch(
                "cloud_run.routes.ComfyHost.from_running_host",
                return_value=host,
            ):
                result = asyncio.run(
                    resolver.resolve_preflight(
                        capture,
                        explicit_output_allowance_bytes=1024,
                    )
                )

        self.assertTrue(result.rentable)
        self.assertEqual(result.artifacts[0].source.kind, "huggingface")
        model_source_resolver.resolve.assert_awaited_once()


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
            disk_gb=80,
            transfer_bytes=0,
            output_allowance_bytes=1,
            inet_down_cost=None,
            inet_up_cost=None,
            duration_seconds=7200,
            deadline_mode="finite",
            approximate_max_active_charge=0.84,
            template_hash_id="1" * 32,
            worker_commit="a" * 40,
            worker_archive_sha256="b" * 64,
            protocol_version="2",
            manifest_digest="c" * 64,
            max_instance_creates=1,
        ),
    )


class LegacyRouteRemovalTests(unittest.TestCase):
    def test_superseded_quote_and_attempt_routes_are_not_registered(self):
        routes = set(captured_handlers())

        self.assertNotIn(("POST", "/cloud-run/api/quotes"), routes)
        self.assertFalse(
            any("/cloud-run/api/attempts/" in path for _method, path in routes)
        )


class PaidSessionRouteTests(unittest.TestCase):
    def test_paid_rental_conflict_maps_to_static_http_409(self):
        service = mock.Mock()
        service.confirm_session = mock.AsyncMock(
            side_effect=PaidRentalConflict()
        )
        handlers = captured_handlers(service_factory=lambda: service)

        response = asyncio.run(
            handlers[
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/confirm",
                )
            ](
                FakeRequest(
                    {"idempotency_key": "private-session-key"},
                    match_info={"session_id": "session-1"},
                )
            )
        )

        self.assertEqual(response.status, 409)
        self.assertEqual(
            response.payload,
            {
                "error": (
                    "Another Cloud Run rental is active or unresolved. "
                    "Do not start another rental yet."
                )
            },
        )

    def test_quote_and_confirm_routes_use_only_the_exact_session_contract(self):
        quoted = CloudSession.new(
            "private-session-idempotency-key",
            session_id="session-1",
            quote=attempt().quote,
            manifest_digest="c" * 64,
            deadline_at=7300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.OFFER_SELECTED,
        )
        started = quoted.transition(
            SessionState.CONFIRMING,
            now=101.0,
        ).transition(
            SessionState.CREATING,
            now=102.0,
            session_secret_hex="d" * 64,
        ).transition(
            SessionState.BOOTSTRAPPING,
            now=103.0,
            instance_id="77",
        )
        service = mock.Mock()
        service.preview_session = mock.AsyncMock(return_value=quoted)
        service.confirm_session = mock.AsyncMock(return_value=started)
        handlers = captured_handlers(service_factory=lambda: service)
        request_payload = {
            "preflight_id": "preflight-1",
            "offer_id": "42",
            "idempotency_key": "private-session-idempotency-key",
            "deadline": {
                "mode": "finite",
                "duration_seconds": 7200,
            },
            "max_instance_creates": 1,
        }

        quote_response = asyncio.run(
            handlers[("POST", "/cloud-run/api/sessions")](
                FakeRequest(request_payload)
            )
        )
        confirm_response = asyncio.run(
            handlers[
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/confirm",
                )
            ](
                FakeRequest(
                    {
                        "idempotency_key": (
                            "private-session-idempotency-key"
                        )
                    },
                    match_info={"session_id": "session-1"},
                )
            )
        )
        rejected = asyncio.run(
            handlers[("POST", "/cloud-run/api/sessions")](
                FakeRequest({**request_payload, "unexpected": True})
            )
        )
        missing_limit = dict(request_payload)
        del missing_limit["max_instance_creates"]
        missing = asyncio.run(
            handlers[("POST", "/cloud-run/api/sessions")](
                FakeRequest(missing_limit)
            )
        )

        service.preview_session.assert_awaited_once_with(
            preflight_id="preflight-1",
            offer_id="42",
            idempotency_key="private-session-idempotency-key",
            deadline={
                "mode": "finite",
                "duration_seconds": 7200,
            },
            max_instance_creates=1,
        )
        service.confirm_session.assert_awaited_once_with(
            "session-1",
            idempotency_key="private-session-idempotency-key",
        )
        self.assertEqual(quote_response.status, 200)
        self.assertEqual(quote_response.payload["status"], "offer_selected")
        self.assertEqual(confirm_response.payload["status"], "bootstrapping")
        self.assertEqual(confirm_response.payload["instance_id"], "77")
        self.assertEqual(rejected.status, 400)
        self.assertEqual(missing.status, 400)
        for response in (quote_response, confirm_response):
            encoded = repr(response.payload)
            self.assertNotIn("private-session-idempotency-key", encoded)
            self.assertNotIn("d" * 64, encoded)

    def test_session_and_job_routes_are_owned_idempotent_and_browser_safe(self):
        session = CloudSession.new(
            "private-session-key",
            session_id="session-1",
            manifest_digest="c" * 64,
            deadline_at=7300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        )
        job = CloudJob(
            job_id="job-1",
            session_id="session-1",
            idempotency_key="private-job-key",
            state=JobState.SUCCEEDED,
            prompt_digest="d" * 64,
            capture_json='{"output":{},"queue_options":{},"workflow":{}}',
            manifest_digest="e" * 64,
            remote_prompt_id="11111111-1111-1111-1111-111111111111",
            sanitized_error=None,
            created_at=101.0,
            updated_at=102.0,
            version=5,
        )
        service = mock.Mock()
        service.refresh_session = mock.AsyncMock(return_value=session)
        service.submit_job = mock.AsyncMock(return_value=job)
        service.get_job.return_value = job
        handlers = captured_handlers(service_factory=lambda: service)

        session_response = asyncio.run(
            handlers[
                ("GET", "/cloud-run/api/sessions/{session_id}")
            ](
                FakeRequest(match_info={"session_id": "session-1"})
            )
        )
        submit_response = asyncio.run(
            handlers[
                ("POST", "/cloud-run/api/sessions/{session_id}/jobs")
            ](
                FakeRequest(
                    {
                        "capture_id": "capture-2",
                        "idempotency_key": "private-job-key",
                    },
                    match_info={"session_id": "session-1"},
                )
            )
        )
        job_response = asyncio.run(
            handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                    }
                )
            )
        )
        invalid = asyncio.run(
            handlers[
                ("POST", "/cloud-run/api/sessions/{session_id}/jobs")
            ](
                FakeRequest(
                    {
                        "capture_id": "capture-2",
                        "idempotency_key": "private-job-key",
                        "provider_token": "forbidden",
                    },
                    match_info={"session_id": "session-1"},
                )
            )
        )

        service.refresh_session.assert_awaited_once_with("session-1")
        service.submit_job.assert_awaited_once_with(
            "session-1",
            capture_id="capture-2",
            idempotency_key="private-job-key",
        )
        service.get_job.assert_called_once_with("session-1", "job-1")
        self.assertEqual(session_response.payload["status"], "ready")
        self.assertEqual(submit_response.payload["status"], "succeeded")
        self.assertEqual(job_response.payload, submit_response.payload)
        self.assertEqual(invalid.status, 400)
        encoded = repr(
            [
                session_response.payload,
                submit_response.payload,
                job_response.payload,
            ]
        )
        self.assertNotIn("private-session-key", encoded)
        self.assertNotIn("private-job-key", encoded)
        self.assertNotIn("11111111-1111-1111-1111-111111111111", encoded)

    def test_deadline_and_two_stage_destroy_routes_accept_only_exact_controls(self):
        ready = CloudSession.new(
            "private-session-key",
            session_id="session-1",
            manifest_digest="c" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        )
        destroyed = ready.transition(
            SessionState.DESTROY_REQUESTED,
            now=101.0,
            destroy_requested=True,
        ).transition(
            SessionState.DESTROYING,
            now=102.0,
        ).transition(
            SessionState.DESTROYED,
            now=103.0,
            instance_id=None,
        )
        review_payload = {
            "session_id": "session-1",
            "instance_id": "77",
            "status": "ready",
            "unverified_artifact_ids": ["output-2"],
            "warning": "Unverified results will be irreversibly lost.",
            "review_token": "browser-one-time-token",
            "expires_at": 400.0,
        }
        review = types.SimpleNamespace(
            public_payload=lambda: review_payload
        )
        service = mock.Mock()
        service.update_session_deadline = mock.AsyncMock(
            return_value=ready.transition(
                SessionState.READY,
                now=101.0,
                deadline_at=9_100.0,
            )
        )
        service.review_session_destroy = mock.AsyncMock(
            return_value=review
        )
        service.destroy_session = mock.AsyncMock(return_value=destroyed)
        handlers = captured_handlers(service_factory=lambda: service)

        deadline_response = asyncio.run(
            handlers[
                ("PUT", "/cloud-run/api/sessions/{session_id}/deadline")
            ](
                FakeRequest(
                    {"action": "add_30_minutes"},
                    match_info={"session_id": "session-1"},
                )
            )
        )
        review_response = asyncio.run(
            handlers[
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/destroy-review",
                )
            ](
                FakeRequest(
                    {},
                    match_info={"session_id": "session-1"},
                )
            )
        )
        destroy_response = asyncio.run(
            handlers[
                ("DELETE", "/cloud-run/api/sessions/{session_id}")
            ](
                FakeRequest(
                    {
                        "review_token": "browser-one-time-token",
                        "acknowledge_data_loss": True,
                    },
                    match_info={"session_id": "session-1"},
                )
            )
        )
        invalid = asyncio.run(
            handlers[
                ("DELETE", "/cloud-run/api/sessions/{session_id}")
            ](
                FakeRequest(
                    {
                        "review_token": "browser-one-time-token",
                        "acknowledge_data_loss": True,
                        "stop": True,
                    },
                    match_info={"session_id": "session-1"},
                )
            )
        )

        self.assertEqual(deadline_response.payload["deadline_at"], 9_100.0)
        self.assertEqual(
            review_response.payload["unverified_artifact_ids"],
            ["output-2"],
        )
        self.assertEqual(destroy_response.payload["status"], "destroyed")
        self.assertEqual(invalid.status, 400)
        service.update_session_deadline.assert_awaited_once_with(
            "session-1",
            {"action": "add_30_minutes"},
        )
        service.review_session_destroy.assert_awaited_once_with(
            "session-1"
        )
        service.destroy_session.assert_awaited_once_with(
            "session-1",
            {
                "review_token": "browser-one-time-token",
                "acknowledge_data_loss": True,
            },
        )


class RelayMediaRouteTests(unittest.TestCase):
    def setUp(self):
        from cloud_run.job_repository import JobRepository
        from cloud_run.models import CloudJob, JobState, TransferState
        from cloud_run.relay import LocalRelay

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output_root = self.root / "output"
        self.output_root.mkdir()
        repository = JobRepository(
            self.root / "private" / "sessions.sqlite3"
        )
        repository.create_job(
            CloudJob(
                job_id="job-1",
                session_id="session-1",
                idempotency_key="job-key-1",
                state=JobState.SUCCEEDED,
                prompt_digest="c" * 64,
                capture_json=json.dumps(
                    {
                        "workflow": {
                            "nodes": [
                                {
                                    "id": 9,
                                    "type": "SaveImage",
                                    "title": "Wallpaper output",
                                }
                            ]
                        },
                        "output": {},
                        "queue_options": {},
                    }
                ),
                manifest_digest="a" * 64,
                remote_prompt_id=None,
                sanitized_error=None,
                created_at=10,
                updated_at=20,
                version=1,
            )
        )
        preview = b"\x89PNG\r\n\x1a\npreview"
        preview_id = "preview-1"
        repository.append_event(
            "job-1",
            1,
            "b_preview",
            {
                "preview_id": preview_id,
                "mime_type": "image/png",
                "size_bytes": len(preview),
                "sha256": hashlib.sha256(preview).hexdigest(),
            },
            created_at=15,
        )
        relay = LocalRelay(
            worker=None,
            repository=repository,
            private_root=self.root / "private" / "relay",
            output_root=self.output_root,
        )
        preview_path = (
            relay.previews_root
            / "job-1"
            / (preview_id + ".preview")
        )
        preview_path.parent.mkdir(mode=0o700)
        preview_path.write_bytes(preview)
        os.chmod(preview_path, 0o600)
        repository.save_transfer(
            job_id="job-1",
            artifact_id="preview:" + preview_id,
            direction="download",
            expected_size=len(preview),
            sha256=hashlib.sha256(preview).hexdigest(),
            offset=len(preview),
            state=TransferState.VERIFIED,
            private_path=str(preview_path),
        )

        output = b"verified-output"
        output_path = self.output_root / "job-1" / "wallpaper.png"
        output_path.parent.mkdir(mode=0o700)
        output_path.write_bytes(output)
        output_metadata = output_path.stat()
        repository.save_transfer(
            job_id="job-1",
            artifact_id="output-1",
            direction="download",
            expected_size=len(output),
            sha256=hashlib.sha256(output).hexdigest(),
            offset=len(output),
            state=TransferState.VERIFIED,
            private_path=str(output_path),
            source_node_id="9",
            published_device=output_metadata.st_dev,
            published_inode=output_metadata.st_ino,
        )
        self.service = types.SimpleNamespace(
            job_repository=repository,
            relay=relay,
            get_job=lambda session_id, job_id: (
                repository.get_job(job_id)
                if session_id == "session-1"
                else None
            ),
        )
        ready = CloudSession.new(
            "session-key",
            session_id="session-1",
            quote=attempt().quote,
            manifest_digest="c" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        ).transition(
            SessionState.READY,
            now=100.0,
            instance_id="77",
        )
        self.service.refresh_session = mock.AsyncMock(return_value=ready)
        self.service.clock = lambda: 7_000.0
        self.service.session_service = types.SimpleNamespace(
            alerts=lambda session, now: ["5_minutes"]
        )
        self.handlers = captured_handlers(
            service_factory=lambda: self.service
        )

    def test_routes_return_only_owned_local_verified_media(self):
        events = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}/events"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                    },
                    query={"after_sequence": "0"},
                )
            )
        )
        preview = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}/previews/{preview_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                        "preview_id": "preview-1",
                    }
                )
            )
        )
        output = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}/artifacts/{artifact_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                        "artifact_id": "output-1",
                    }
                )
            )
        )

        self.assertEqual(events.status, 200)
        self.assertEqual(events.payload["events"][0]["sequence"], 1)
        self.assertEqual(preview.payload, b"\x89PNG\r\n\x1a\npreview")
        self.assertEqual(preview.headers["Content-Type"], "image/png")
        self.assertEqual(output.payload, b"verified-output")
        self.assertNotIn(str(self.root), repr(events.payload))
        self.assertNotIn(str(self.root), repr(preview.headers))
        self.assertNotIn(str(self.root), repr(output.headers))

    def test_job_status_lists_only_safe_verified_outputs_and_previews(self):
        response = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                    }
                )
            )
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["status"], "succeeded")
        self.assertEqual(response.payload["previews"], [
            {
                "id": "preview-1",
                "state": "verified",
                "size_bytes": len(b"\x89PNG\r\n\x1a\npreview"),
            }
        ])
        self.assertEqual(
            response.payload["outputs"][0]["id"],
            "output-1",
        )
        self.assertEqual(
            response.payload["outputs"][0]["state"],
            "local_verified",
        )
        self.assertEqual(
            response.payload["outputs"][0]["filename"],
            "wallpaper.png",
        )
        self.assertEqual(response.payload["outputs"][0]["node_id"], "9")
        self.assertNotIn("node_id", response.payload["transfers"][1])
        self.assertNotIn(str(self.root), repr(response.payload))

    def test_job_status_folds_native_events_into_current_node_and_progress(self):
        repository = self.service.job_repository
        repository.append_event(
            "job-1",
            2,
            "executing",
            {"node_id": "9", "display_node_id": "9"},
            created_at=16.0,
        )
        repository.append_event(
            "job-1",
            3,
            "progress",
            {"value": 20, "max": 20, "node_id": "9"},
            created_at=17.0,
        )
        repository.append_event(
            "job-1",
            4,
            "progress_text",
            {"node_id": "9", "text": "Sampling complete"},
            created_at=18.0,
        )

        response = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "session-1",
                        "job_id": "job-1",
                    }
                )
            )
        )

        self.assertEqual(
            response.payload["current_node"],
            {"id": "9", "title": "Wallpaper output"},
        )
        self.assertEqual(
            response.payload["progress"],
            {"value": 20, "max": 20},
        )
        self.assertEqual(
            response.payload["progress_text"],
            "Sampling complete",
        )
        self.assertEqual(response.payload["last_sequence"], 4)
        self.assertEqual(response.payload["execution_state"], "pending")
        self.assertEqual(response.payload["harvest_state"], "pending")
        self.assertEqual(response.payload["execution_status"], "pending")
        self.assertEqual(response.payload["harvest_status"], "pending")

    def test_session_status_aggregates_cost_alerts_job_and_local_history(self):
        response = asyncio.run(
            self.handlers[
                ("GET", "/cloud-run/api/sessions/{session_id}")
            ](
                FakeRequest(
                    match_info={"session_id": "session-1"}
                )
            )
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["elapsed_seconds"], 6_900.0)
        self.assertAlmostEqual(
            response.payload["approximate_spend"],
            0.42 * 6_900 / 3_600,
        )
        self.assertEqual(
            response.payload["deadline_alerts"],
            ["5_minutes"],
        )
        self.assertEqual(
            response.payload["current_job"]["job_id"],
            "job-1",
        )
        self.assertEqual(
            response.payload["current_job"]["outputs"][0]["state"],
            "local_verified",
        )
        self.assertEqual(
            [item["job_id"] for item in response.payload["history"]],
            ["job-1"],
        )
        self.assertNotIn(str(self.root), repr(response.payload))

    def test_session_status_maps_bounded_progress_to_safe_current_model(self):
        repository = self.service.job_repository
        revision = "a" * 40
        model = ArtifactSpec(
            artifact_id="model-" + "b" * 64,
            kind="model",
            logical_name="Gold-model.safetensors",
            destination="models/diffusion_models/gold.safetensors",
            size_bytes=10,
            sha256="b" * 64,
            source=SourceSpec(
                "huggingface",
                (
                    "https://huggingface.co/example/public-model/resolve/"
                    + revision
                    + "/gold.safetensors"
                ),
                immutable_revision=revision,
            ),
        )
        input_artifact = ArtifactSpec(
            artifact_id="input-private",
            kind="input",
            logical_name="private-input.png",
            destination="input/private-input.png",
            size_bytes=5,
            sha256="c" * 64,
            source=SourceSpec(
                "local-upload",
                "local-upload:input-private",
            ),
        )
        manifest = DependencyManifest(
            schema_version=2,
            protocol_version="2",
            comfyui_core_version="0.29.0",
            comfyui_frontend_version="1.47.10",
            worker_version="a" * 40,
            prompt_digest="d" * 64,
            custom_nodes=(),
            artifacts=(model, input_artifact),
            output_allowance_bytes=1024,
            disk_gb=80,
        )
        repository.save_manifest(
            manifest.digest,
            manifest.canonical_bytes().decode("utf-8"),
            created_at=100.0,
        )
        repository.record_provision_progress(
            transaction_id="provision-" + manifest.digest,
            session_id="session-1",
            job_id="bootstrap:session-1",
            manifest_digest=manifest.digest,
            state="applying",
            phase="model_transfer",
            current_dependency_id=model.artifact_id,
            transferred_bytes=4,
            total_bytes=15,
            last_progress_at=6_995.0,
        )
        repository.save_transfer(
            job_id="bootstrap:session-1",
            artifact_id=input_artifact.artifact_id,
            direction="upload",
            expected_size=input_artifact.size_bytes,
            sha256=input_artifact.sha256,
            offset=3,
            state=TransferState.TRANSFERRING,
            private_path=str(self.root / "private" / "input.part"),
        )
        provisioning = CloudSession.new(
            "session-key-progress",
            session_id="session-1",
            quote=replace(
                attempt().quote,
                manifest_digest=manifest.digest,
                transfer_bytes=15,
                output_allowance_bytes=1024,
                disk_gb=80,
            ),
            manifest_digest=manifest.digest,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.PROVISIONING,
        ).transition(
            SessionState.PROVISIONING,
            now=6_990.0,
            instance_id="77",
        )
        self.service.refresh_session = mock.AsyncMock(
            return_value=provisioning
        )
        handler = self.handlers[
            ("GET", "/cloud-run/api/sessions/{session_id}")
        ]

        response = asyncio.run(
            handler(FakeRequest(match_info={"session_id": "session-1"}))
        )

        self.assertEqual(
            response.payload["provisioning"],
            {
                "phase": "model_transfer",
                "current_model": "Gold-model.safetensors",
                "transferred_bytes": 7,
                "total_bytes": 15,
                "installed_units": 0,
                "validated_units": 0,
                "seconds_without_progress": 5.0,
                "stall_budget_seconds": 600,
            },
        )
        self.assertNotIn("huggingface.co", repr(response.payload))
        self.assertNotIn("local-upload", repr(response.payload))
        self.assertNotIn(str(self.root), repr(response.payload))

        with closing(repository._connect()) as connection:
            connection.execute(
                """
                UPDATE provision_transactions
                SET phase = 'unknown', transferred_bytes = 999
                WHERE transaction_id = ?
                """,
                ("provision-" + manifest.digest,),
            )
            connection.commit()
        hostile = asyncio.run(
            handler(FakeRequest(match_info={"session_id": "session-1"}))
        )
        self.assertEqual(
            hostile.payload["provisioning"]["phase"],
            "provisioning",
        )
        self.assertIsNone(
            hostile.payload["provisioning"]["current_model"]
        )
        self.assertEqual(
            hostile.payload["provisioning"]["transferred_bytes"],
            3,
        )
        self.assertEqual(
            hostile.payload["provisioning"]["total_bytes"],
            15,
        )

    def test_wrong_session_cannot_read_an_existing_job(self):
        response = asyncio.run(
            self.handlers[
                (
                    "GET",
                    (
                        "/cloud-run/api/sessions/{session_id}/jobs/"
                        "{job_id}/artifacts/{artifact_id}"
                    ),
                )
            ](
                FakeRequest(
                    match_info={
                        "session_id": "other-session",
                        "job_id": "job-1",
                        "artifact_id": "output-1",
                    }
                )
            )
        )

        self.assertEqual(response.status, 404)
        self.assertNotIn("job-1", repr(response.payload))


class DesktopRelayRouteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        class Status:
            def __init__(inner_self, active):
                inner_self.active = active

            def public_payload(inner_self):
                return {
                    "bound": True,
                    "url": "http://127.0.0.1:32145",
                    "connection_name": "ComfyUI Vast",
                    "active_session_id": (
                        "session-1" if inner_self.active else None
                    ),
                    "profile_revision": 3 if inner_self.active else None,
                    "ready": inner_self.active,
                    "error": None,
                }

        test = self

        class Service:
            def desktop_setup(inner_self):
                return {
                    **Status(True).public_payload(),
                    "manual_setup_required": True,
                    "instructions": ["Open Remote Connections."],
                }

            async def activate_desktop_relay(inner_self, session_id):
                test.calls.append(("activate", session_id))
                return Status(True)

            async def deactivate_desktop_relay(inner_self, session_id):
                test.calls.append(("deactivate", session_id))
                return Status(False)

        service = Service()
        self.handlers = captured_handlers(service_factory=lambda: service)

    def test_local_context_and_setup_never_set_remote_capability(self):
        context = asyncio.run(
            self.handlers[("GET", "/cloud-run/api/desktop-context")](
                FakeRequest()
            )
        )
        setup = asyncio.run(
            self.handlers[("GET", "/cloud-run/api/desktop-setup")](
                FakeRequest()
            )
        )

        self.assertEqual(context.payload, {"role": "local"})
        self.assertNotIn("Set-Cookie", context.headers)
        self.assertEqual(setup.payload["url"], "http://127.0.0.1:32145")
        self.assertEqual(setup.payload["connection_name"], "ComfyUI Vast")
        self.assertNotIn("capability", repr(setup.payload).casefold())

    def test_activation_and_deactivation_are_explicit_local_only_routes(self):
        activate = asyncio.run(
            self.handlers[
                (
                    "POST",
                    "/cloud-run/api/sessions/{session_id}/desktop-relay",
                )
            ](
                FakeRequest(
                    body={},
                    match_info={"session_id": "session-1"},
                )
            )
        )
        deactivate = asyncio.run(
            self.handlers[
                (
                    "DELETE",
                    "/cloud-run/api/sessions/{session_id}/desktop-relay",
                )
            ](
                FakeRequest(match_info={"session_id": "session-1"})
            )
        )

        self.assertEqual(activate.status, 200)
        self.assertTrue(activate.payload["ready"])
        self.assertEqual(deactivate.status, 200)
        self.assertFalse(deactivate.payload["ready"])
        self.assertEqual(
            self.calls,
            [("activate", "session-1"), ("deactivate", "session-1")],
        )


if __name__ == "__main__":
    unittest.main()

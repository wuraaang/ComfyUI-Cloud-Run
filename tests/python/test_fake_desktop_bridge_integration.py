"""Offline acceptance campaign for the local-to-Vast Desktop bridge."""

import asyncio
from contextlib import closing, contextmanager, ExitStack
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tarfile
import types
import unittest
from unittest import mock

from cloud_run.agent_bridge import AgentBridgeProbe
from cloud_run.desktop_profile import DesktopProfileStore, ProfileConflict
from cloud_run.desktop_relay import DesktopRelay, DesktopRelayResponse
from cloud_run.job_repository import JobRepository
from cloud_run.models import JobState, SessionState, TransferState
from cloud_run.readiness import (
    ControllerReadinessProbe,
    LocalExecutionGuard,
    ReadinessValidator,
)
from cloud_run.run_errors import RunErrorCode, RunJournalEntry, RunPhase
from remote_worker.install import CustomNodeInstaller
from remote_worker.native_proxy import NativeRoutePolicy
from tests.python import test_certified_baseline as certified_baseline_fixture
from tests.python.test_fake_session_integration import (
    FakeCloudRunSystem,
    confirmed,
    native_capture,
)


SEEDED_SECRET = "seeded-secret-must-never-appear"


@contextmanager
def blocked_external_network():
    """Make an accidental real HTTP/socket boundary fail the campaign."""

    failure = AssertionError("offline Desktop campaign attempted real network I/O")
    targets = (
        "socket.socket.connect",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.request",
        "http.client.HTTPSConnection.request",
    )
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(mock.patch(target, side_effect=failure))
        yield


class _CertifiedBaselineFixture:
    """Materialize the reviewed package shape through the production resolver."""

    def __init__(self):
        self._case = certified_baseline_fixture.CertifiedBaselineTests(
            methodName="runTest"
        )
        self._case.setUp()
        lock_path, fetcher, calls, _record = (
            self._case._fixture_lock_and_fetcher()
        )
        self.resolution = self._case._resolve(lock_path, fetcher)
        self.fetch_calls = tuple(calls)

    def close(self):
        self._case.doCleanups()


LIVE_AUDIT_REGRESSION_MAP = {
    1: {
        "finding": "quote_expiry",
        "tests": (
            "python:tests.python.test_service.CloudRunServiceTests."
            "test_confirmation_rejects_quote_identity_changes_without_create",
        ),
    },
    2: {
        "finding": "caddy_bearer_order",
        "tests": (
            "python:tests.python.test_worker_server.WorkerApplicationTests."
            "test_proxy_and_worker_bindings_are_exact_and_loopback_only",
        ),
    },
    3: {
        "finding": "nested_desktop_loader_imports",
        "tests": (
            "python:tests.python.test_loader_and_routes.LoaderContractTests."
            "test_web_only_exports_and_exact_decorator_routes",
        ),
    },
    4: {
        "finding": "competing_local_backends",
        "tests": (
            "python:tests.python.test_fake_desktop_bridge_integration."
            "FakeDesktopBridgeCampaignTests."
            "test_bridge_uses_existing_prompt_server_lifecycle_only",
        ),
    },
    5: {
        "finding": "measured_29347469703_byte_transfer",
        "tests": (
            "python:tests.python.test_offers.ExplainedOfferDecisionTests."
            "test_readiness_estimate_uses_remaining_bytes_and_measured_megabytes",
        ),
    },
    6: {
        "finding": "bounded_comfy_diagnostics",
        "tests": (
            "python:tests.python.test_worker_diagnostics.WorkerDiagnosticsTests."
            "test_comfy_startup_failure_carries_only_safe_bounded_diagnostics",
        ),
    },
    7: {
        "finding": "ready_transaction_timeout_recovery",
        "tests": (
            "python:tests.python.test_session_service.ReusableSessionTests."
            "test_ready_recovery_reauthenticates_manifest_and_deadline_without_create",
            "python:tests.python.test_session_service.ReusableSessionTests."
            "test_ready_transaction_reuse_rejects_a_different_local_job_scope",
        ),
    },
    8: {
        "finding": "existing_bootstrap_directory",
        "tests": (
            "python:tests.python.test_worker_bootstrap.BootstrapTests."
            "test_bootstrap_reuses_only_an_exact_verified_existing_install",
        ),
    },
    9: {
        "finding": "native_execution_success",
        "tests": (
            "python:tests.python.test_fake_desktop_bridge_integration."
            "FakeDesktopBridgeCampaignTests."
            "test_manual_campaign_survives_both_desktops_until_explicit_destroy",
        ),
    },
    10: {
        "finding": "event_94_split_read_freeze",
        "tests": (
            "python:tests.python.test_relay.LocalRelayTests."
            "test_sync_catches_up_from_event_94_using_only_atomic_snapshot",
        ),
    },
    11: {
        "finding": "temporary_preview_false_failure",
        "tests": (
            "python:tests.python.test_worker_native_jobs.NativeJobRecorderTests."
            "test_success_harvest_ignores_temp_and_is_idempotent",
        ),
    },
    12: {
        "finding": "generic_worker_error_collapse",
        "tests": (
            "python:tests.python.test_run_errors.RunErrorContractTests."
            "test_all_run_error_code_mappings",
        ),
    },
    13: {
        "finding": "empty_local_progress",
        "tests": (
            "python:tests.python.test_routes.RelayMediaRouteTests."
            "test_job_status_folds_native_events_into_current_node_and_progress",
        ),
    },
    14: {
        "finding": "confusing_offer_filters",
        "tests": (
            "python:tests.python.test_offers.ExplainedOfferDecisionTests."
            "test_workflow_minimum_and_price_cap_are_hard_but_vram_is_soft",
            "python:tests.python.test_offers.ExplainedOfferDecisionTests."
            "test_exclusions_are_typed_and_performant_offer_beats_cheap_weak_offer",
        ),
    },
    15: {
        "finding": "second_run_wizard",
        "tests": (
            "node:tests/js/session-console.test.mjs::"
            "ready session shows ComfyUI Vast Desktop guidance without a second Run",
        ),
    },
    16: {
        "finding": "dishonest_launch_timing",
        "tests": (
            "python:tests.python.test_offers.ExplainedOfferDecisionTests."
            "test_readiness_estimate_uses_remaining_bytes_and_measured_megabytes",
        ),
    },
}


class FakeListener:
    def __init__(self, port=32145):
        self.port = port
        self.starts = []
        self.closed = False

    async def start(self, host, requested_port, handler):
        self.starts.append((host, requested_port, handler))
        return self.port if requested_port == 0 else requested_port

    async def close(self):
        self.closed = True


class FakeRequest:
    def __init__(self, method, path_qs, *, body=b"", headers=None):
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
        self.graph_edits = []
        self.ordered_batches = []

    def allow(self, session_id):
        self.allowed.append(session_id)

    async def probe(self, _session):
        return AgentBridgeProbe(
            orchestrator_identity="passed",
            graph_read="passed",
            graph_edit_restore="passed",
            graph_run="passed",
            ordered_batch="passed",
        )

    async def open(self, request, session):
        self.opened.append((request.path_qs, session.session_id))
        return DesktopRelayResponse(200, b"agent-panel-connected")

    def compatibility(self, path, session):
        if path.endswith("/status"):
            return {
                "running": True,
                "bridge_url": session.websocket_url,
                "comfyui_url": session.relay_origin,
                "comfyui_path": "",
                "start_command": "",
                "can_spawn": False,
            }
        if path.endswith("/bridge_url"):
            return {"url": session.websocket_url}
        return {
            "backends": [
                {
                    "backend": "claude",
                    "running": True,
                    "cli": None,
                    "auth": None,
                    "ready": True,
                }
            ],
            "any_ready": True,
            "can_spawn": False,
            "start_command": "",
        }

    async def revoke(self, session_id):
        self.revoked.append(session_id)

    async def close(self):
        self.closed = True


def _native_body(payload, client_id="desktop-client"):
    return json.dumps(
        {
            "client_id": client_id,
            "prompt": payload["output"],
            "extra_data": {
                "extra_pnginfo": {"workflow": payload["workflow"]}
            },
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _campaign_canvas(*, seed=11):
    payload = native_capture(
        model="model-a.safetensors",
        input_name="input-a.png",
        seed=seed,
    )
    payload["workflow"]["nodes"].extend(
        (
            {"id": 9, "type": "SaveImage"},
            {"id": 66, "type": "PreviewImage"},
        )
    )
    payload["output"].update(
        {
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "cloud-vast-offline",
                    "images": ["3", 0],
                },
            },
            "66": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["3", 0]},
            },
        }
    )
    payload["workflow"]["extra"].update(
        {
            "cloudVastUnsaved": True,
            "palette": "midnight",
            "background": "backgrounds/studio.jpg",
        }
    )
    return payload


def _agent_edited_canvas():
    payload = _campaign_canvas(seed=12)
    payload["workflow"]["nodes"].extend(
        (
            {"id": 5, "type": "CheckpointLoaderSimple"},
            {"id": 6, "type": "LoadImage"},
            {"id": 7, "type": "KSampler"},
        )
    )
    payload["output"].update(
        {
            "5": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model-b.safetensors"},
            },
            "6": {
                "class_type": "LoadImage",
                "inputs": {"image": "input-b.png"},
            },
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["5", 0],
                    "image": ["6", 0],
                    "seed": 12,
                },
            },
        }
    )
    return payload


class OfflineDesktopBridgeCampaign:
    """Compose production boundaries with deterministic fake side effects."""

    def __init__(self, *, deadline_mode):
        if deadline_mode not in {"none", "finite"}:
            raise ValueError("invalid synthetic deadline mode")
        self.deadline_mode = deadline_mode
        self.system = FakeCloudRunSystem()
        self.baseline_fixture = _CertifiedBaselineFixture()
        self.baseline = self.baseline_fixture.resolution
        self.agent = FakeAgentBridge()
        self.listeners = []
        self.relays = []
        self.local_backend_starts = 0
        self.local_prompt_calls = []
        self.steps = []
        self.readiness_probe_calls = 0
        self.worker_readiness_proofs = []
        self.local_execution_guard = LocalExecutionGuard()
        self._profile_setup()
        self._wire_desktop()

    def close(self):
        for relay in self.relays:
            try:
                asyncio.run(relay.close())
            except RuntimeError:
                pass
        self.system.close()
        self.baseline_fixture.close()

    def _measure_ui_baseline(self):
        worker_root = self.system.root / "measured-worker"
        custom_nodes = worker_root / "custom_nodes"
        wheels = worker_root / "wheels"
        artifacts = worker_root / "artifacts"
        for path in (custom_nodes, wheels, artifacts):
            path.mkdir(parents=True, mode=0o700)

        local_by_id = {
            item.artifact_id: Path(item.private_path)
            for item in self.baseline.local_artifacts
        }
        for package in self.baseline.ui_packages:
            source = local_by_id[package.archive.artifact_id]
            (artifacts / (package.archive.artifact_id + ".tar")).write_bytes(
                source.read_bytes()
            )

        class NoPipRunner:
            async def run(_self, _argv):
                raise AssertionError("UI-only baseline attempted pip")

        installer = CustomNodeInstaller(
            custom_nodes_root=custom_nodes,
            wheel_root=wheels,
            artifact_root=artifacts,
            runner=NoPipRunner(),
        )
        return tuple(
            asyncio.run(installer.install_ui_package(package))
            for package in self.baseline.ui_packages
        )

    def _configure_resolver_baseline(self):
        resolver = self.system.resolver
        original = resolver.resolve_preflight
        baseline_nodes = self.baseline.custom_nodes

        async def resolve_preflight(
            capture,
            *,
            explicit_output_allowance_bytes,
        ):
            result = await original(
                capture,
                explicit_output_allowance_bytes=(
                    explicit_output_allowance_bytes
                ),
            )
            values = vars(result).copy()
            values["custom_nodes"] = (
                *baseline_nodes,
                *result.custom_nodes,
            )
            return types.SimpleNamespace(**values)

        resolver.resolve_preflight = resolve_preflight

    def _profile_setup(self):
        root = self.system.root
        self.user_root = root / "desktop-user"
        profile_root = self.user_root / "default"
        workflows = profile_root / "workflows"
        palettes = profile_root / "color_palettes"
        self.input_root = root / "desktop-input"
        backgrounds = self.input_root / "backgrounds"
        workflows.mkdir(parents=True)
        palettes.mkdir(parents=True)
        backgrounds.mkdir(parents=True)
        (workflows / "canvas.json").write_text(
            json.dumps(_campaign_canvas()),
            encoding="utf-8",
        )
        (palettes / "midnight.json").write_text(
            json.dumps({"name": "midnight", "colors": {"node": "#151525"}}),
            encoding="utf-8",
        )
        (backgrounds / "studio.jpg").write_bytes(b"\xff\xd8\xffsynthetic-background")
        (profile_root / "comfy.settings.json").write_text(
            json.dumps(
                {
                    "Comfy.ColorPalette": "midnight",
                    "Comfy.Canvas.BackgroundImage": str(backgrounds / "studio.jpg"),
                    "comfyui-mcp.remoteComfyuiUrl": "http://old.invalid",
                }
            ),
            encoding="utf-8",
        )
        self.ui_measurements = self._measure_ui_baseline()
        self.profile_store = DesktopProfileStore(
            repository=self.system.jobs,
            private_root=self.system.data_root / "desktop-profile",
            profile_id="desktop-profile",
            clock=self.system.clock,
        )
        self.initial_profile = self.profile_store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow=_campaign_canvas(),
            ui_packages=self.baseline.ui_packages,
        )
        self.system.configure_desktop_profile(
            profile_store=self.profile_store,
            profile=self.initial_profile,
            ui_packages=self.baseline.ui_packages,
            ui_local_artifacts=self.baseline.local_artifacts,
        )
        self._configure_resolver_baseline()

    def _served_extension_paths(self):
        paths = {
            path
            for result in self.ui_measurements
            for path in result.extension_paths
        }
        node = self.baseline.custom_nodes[0]
        local = next(
            item
            for item in self.baseline.local_artifacts
            if item.artifact_id == node.archive.artifact_id
        )
        with tarfile.open(local.private_path, "r:") as archive:
            for member in archive.getmembers():
                if member.isfile() and member.name.startswith("js/"):
                    paths.add(
                        "/extensions/"
                        + node.package_id
                        + "/"
                        + member.name.removeprefix("js/")
                    )
        return sorted(paths)

    def _required_class_types(self, manifest):
        capture = self.system.jobs.get_capture_by_prompt_digest(
            manifest.prompt_digest
        )
        required = set(capture.executable_class_types)
        for node in manifest.custom_nodes:
            required.update(node.provided_class_types)
        return tuple(sorted(required))

    async def _worker_transaction(self, transaction_id):
        self.readiness_probe_calls += 1
        call = self.system.worker.manifest_calls[-1]
        manifest = call["manifest"]
        if transaction_id != "provision-" + call["manifest_digest"]:
            return None
        profile = manifest["profile"]
        required_classes = {
            "CheckpointLoaderSimple",
            "KSampler",
            "LoadImage",
            "PreviewImage",
            "SaveImage",
        }
        for node in manifest["custom_nodes"]:
            required_classes.update(node["provided_class_types"])
        expected_ui = {
            package["package_id"]: package["web_sha256"]
            for package in manifest["ui_packages"]
        }
        readiness = {
            "protocol_version": manifest["protocol_version"],
            "comfyui_core_version": manifest["comfyui_core_version"],
            "comfyui_frontend_version": manifest[
                "comfyui_frontend_version"
            ],
            "worker_version": manifest["worker_version"],
            "validated_class_types": sorted(required_classes),
            "validated_artifacts": sorted(
                item["artifact_id"] for item in manifest["artifacts"]
            ),
            "profile_revision": profile["revision"],
            "profile_digest": profile["archive"]["sha256"],
            "bootstrap_digest": profile["bootstrap_digest"],
            "ui_package_digests": expected_ui,
            "served_extension_paths": self._served_extension_paths(),
            "runtime_package_versions": {
                "aiohttp": "offline-fixture",
                "torch": "offline-fixture",
            },
            "comfy_process_healthy": True,
            "completed_at": self.system.clock(),
        }
        if self.readiness_probe_calls == 1:
            readiness["ui_package_digests"] = {}
        self.worker_readiness_proofs.append(
            json.loads(json.dumps(readiness))
        )
        return {
            "transaction_id": transaction_id,
            "manifest_digest": call["manifest_digest"],
            "state": "ready",
            "readiness": readiness,
        }

    async def _worker_native_websocket(self, request):
        if not request.url.endswith(
            "/ws?clientId=cloud-vast-readiness"
        ):
            raise AssertionError("unexpected readiness WebSocket")

        class Socket:
            async def close(_self, *, code):
                if code != 1000:
                    raise AssertionError("readiness WebSocket closed unsafely")

        return Socket()

    def _wire_desktop(self):
        listener = FakeListener()
        relay = DesktopRelay(
            repository=self.system.jobs,
            listener_factory=lambda _handler: listener,
            worker_factory=lambda _session: self.system.worker,
            native_prompt=self.system.session_service.prepare_native_prompt,
            agent_bridge=self.agent,
            local_comfy_root=str(self.system.root / "existing-comfy-backend"),
            capability_factory=lambda: "campaign-capability-" + "c" * 32,
            clock=self.system.clock,
        )
        self.system.worker.transaction = self._worker_transaction
        self.system.worker.native_websocket = self._worker_native_websocket

        async def inventory_probe(session):
            return await self.system.vast.get_instance(
                "synthetic-offline-key",
                session.instance_id,
            )

        controller_probe = ControllerReadinessProbe(
            worker_factory=lambda _session: self.system.worker,
            inventory_probe=inventory_probe,
            desktop_relay=relay,
            release=self.system.release,
            required_class_types=self._required_class_types,
            local_execution_counter=self.local_execution_guard.count,
            continue_guard=lambda _session_id: None,
        )
        validator = ReadinessValidator(
            probe=controller_probe,
            worker_release_digest=self.system.release.worker_archive_sha256,
            relay_origin="http://127.0.0.1:32145",
            clock=self.system.clock,
        )
        self.system.session_service.desktop_relay = relay
        self.system.session_service.readiness_validator = validator
        self.system.session_service.profile_store = self.profile_store
        self.listeners.append(listener)
        self.relays.append(relay)
        self.relay = relay

    @staticmethod
    def _cookie(response):
        return response.headers["Set-Cookie"].split(";", 1)[0]

    def _desktop_headers(self, cookie, **extra):
        return {
            "Host": "127.0.0.1:32145",
            "Cookie": cookie,
            **extra,
        }

    def _open_desktop(self):
        navigation = asyncio.run(
            self.relay.handle(
                FakeRequest(
                    "GET",
                    "/",
                    headers={"Host": "127.0.0.1:32145"},
                )
            )
        )
        if navigation.status != 200:
            raise AssertionError("synthetic Vast Desktop did not open")
        return self._cookie(navigation)

    def _desktop_get(self, path, cookie):
        return asyncio.run(
            self.relay.handle(
                FakeRequest(
                    "GET",
                    path,
                    headers=self._desktop_headers(cookie),
                )
            )
        )

    def _desktop_run(self, payload, *, request_id):
        body = _native_body(payload, client_id="desktop-client-" + request_id)
        response = asyncio.run(
            self.relay.handle(
                FakeRequest(
                    "POST",
                    "/prompt",
                    body=body,
                    headers=self._desktop_headers(
                        self.cookie,
                        **{
                            "X-Cloud-Vast-Request-Id": request_id,
                            "Content-Type": "application/json",
                        },
                    ),
                )
            )
        )
        if response.status != 200:
            raise AssertionError("synthetic native Run was rejected")
        prompt_id = json.loads(response.body)["prompt_id"]
        job = self.system.jobs.get_job_by_idempotency_key(
            self.session.session_id,
            request_id,
        )
        if job is None:
            raise AssertionError("synthetic native intent was not persisted")
        completed = asyncio.run(
            self.system.session_service.reconcile_session_once(
                self.session.session_id
            )
        )
        if getattr(completed, "job_id", None) != job.job_id:
            completed = self.system.jobs.get_job(job.job_id)
        return completed, prompt_id

    def _local_run(self, label):
        self.local_prompt_calls.append(label)
        return {"prompt_id": "local-" + label}

    def _reopen_vast_desktop(self):
        asyncio.run(self.relay.close())
        self._wire_desktop()
        asyncio.run(
            self.relay.start()
        )
        asyncio.run(
            self.relay.activate(
                self.session.session_id,
                self.system.worker,
                self.initial_profile.revision,
            )
        )
        self.cookie = self._open_desktop()

    def _reconstruct_local_service(self):
        asyncio.run(self.relay.close())
        effects_before = dict(self.system.worker.native_prompt_effects)
        self.system.reopen()
        self.system.configure_desktop_profile(
            profile_store=self.profile_store,
            profile=self.initial_profile,
            ui_packages=self.initial_profile.ui_packages,
            ui_local_artifacts=self.system.ui_local_artifacts,
        )
        self._configure_resolver_baseline()
        self._wire_desktop()
        asyncio.run(self.system.service.recover())
        asyncio.run(self.relay.start())
        asyncio.run(
            self.relay.activate(
                self.session.session_id,
                self.system.worker,
                self.initial_profile.revision,
            )
        )
        self.cookie = self._open_desktop()
        if effects_before != self.system.worker.native_prompt_effects:
            raise AssertionError("recovery resubmitted a native prompt")

    def _profile_conflict(self):
        workflow_path = self.user_root / "default" / "workflows" / "canvas.json"
        workflow_path.write_text(
            json.dumps({"nodes": [{"id": 1, "title": "Local revision"}]}),
            encoding="utf-8",
        )
        local = self.profile_store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow=_campaign_canvas(seed=99),
            ui_packages=self.initial_profile.ui_packages,
        )

        remote_root = self.system.root / "remote-profile"
        remote_user = remote_root / "user"
        remote_workflows = remote_user / "default" / "workflows"
        remote_inputs = remote_root / "input"
        remote_workflows.mkdir(parents=True)
        remote_inputs.mkdir(parents=True)
        (remote_workflows / "canvas.json").write_text(
            json.dumps({"nodes": [{"id": 1, "title": "Cloud Vast revision"}]}),
            encoding="utf-8",
        )
        (remote_user / "default" / "comfy.settings.json").write_text(
            json.dumps({"Comfy.ColorPalette": "cloud-vast-revision"}),
            encoding="utf-8",
        )
        remote_store = DesktopProfileStore(
            repository=JobRepository(remote_root / "remote.sqlite3"),
            private_root=remote_root / "private",
            profile_id="desktop-profile",
            clock=lambda: self.system.clock() + 1,
        )
        remote_initial = remote_store.capture(
            user_root=remote_user,
            profile_name="default",
            input_root=remote_inputs,
            bootstrap_workflow=_campaign_canvas(seed=100),
            ui_packages=self.initial_profile.ui_packages,
        )
        remote = replace(
            remote_initial,
            revision=self.initial_profile.revision + 1,
            base_revision=self.initial_profile.revision,
        )
        payload = remote.public_payload()
        payload["archive_artifact_id"] = "profile-" + remote.archive_sha256
        conflict = self.profile_store.apply_remote_payload(
            payload,
            remote.archive_private_path.read_bytes(),
        )
        if not isinstance(conflict, ProfileConflict):
            raise AssertionError("synthetic profile conflict was not retained")
        return local, remote, conflict

    def _record_safe_journal(self, job):
        self.system.jobs.record_journal(
            RunJournalEntry(
                entry_id="campaign-evidence-" + job.job_id,
                session_id=self.session.session_id,
                manifest_digest=job.manifest_digest,
                transaction_id="provision-" + job.manifest_digest,
                job_id=job.job_id,
                phase=RunPhase.SYNCHRONIZATION,
                code=RunErrorCode.SYNCHRONIZATION,
                message="Atomic snapshot reconciliation evidence recorded.",
                node_id="3",
                process_exit_code=0,
                restart_count=1,
                last_probe="native snapshot reached terminal state",
                byte_cursor=29_347_469_703,
                event_cursor=7,
                output_state="succeeded",
                details={
                    "last_logs": ["bounded worker diagnostic"],
                    "output": "locally_verified",
                    "retryable": False,
                },
                created_at=self.system.clock(),
            )
        )

    def run(self):
        # 1. A normal local Run remains local; capture itself never posts it.
        self._local_run("before")
        local_count = len(self.local_prompt_calls)
        payload = _campaign_canvas()
        capture = self.system.capture(payload)
        assert len(self.local_prompt_calls) == local_count
        assert "offlineCustomRevision" not in payload["workflow"]["extra"]
        assert "FancyNode" not in capture.executable_class_types
        assert set(self.baseline_fixture.fetch_calls) == {
            certified_baseline_fixture.AGENT_URL,
            certified_baseline_fixture.EFFICIENCY_URL,
            certified_baseline_fixture.WHEEL_URL,
        }
        self.steps.append(1)

        # 2. Search stays free and contains both explained outcomes.
        preflight = self.system.preflight(capture)
        decisions = asyncio.run(self.system.service.search(preflight.preflight_id))
        assert self.system.vast.mutations == []
        assert any(item["included"] for item in decisions)
        assert any(not item["included"] for item in decisions)
        assert all(
            item["included_reasons"] or item["excluded_reasons"]
            for item in decisions
        )
        self.steps.append(2)

        # 3. The durable paid intent exists before the sole fake create.
        intents_seen = []
        self.system.vast.before_create = lambda session_id: intents_seen.append(
            self.system.sessions.get(session_id)
        )
        self.session = self.system.quote_confirm_and_ready(
            preflight,
            offer_id="42",
            idempotency_key="desktop-campaign-session",
            duration_seconds=(None if self.deadline_mode == "none" else 7_200),
        )
        assert len(intents_seen) == 1 and intents_seen[0] is not None
        assert self.system.vast.create_count == 1
        self.steps.append(3)

        # 4. Baseline installation and both immutable readiness attempts exist.
        assert self.system.worker.installed_profile_members
        assert set(self.system.worker.installed_ui_packages) == {
            "comfyui-agent-panel",
            "hermes-nous",
        }
        assert self.system.worker._installed_custom_nodes == {
            "efficiency-nodes-comfyui": (
                "835bbe14627cccc871822e804c65c734960d3c6e"
            )
        }
        manifest, profile = self.system.session_service._profile_manifest(
            self.session
        )
        assert {
            item.package_id for item in manifest.ui_packages
        } == {"comfyui-agent-panel", "hermes-nous"}
        assert tuple(
            item.package_id for item in manifest.custom_nodes
        ) == ("efficiency-nodes-comfyui",)
        efficiency_classes = set(
            self.baseline.custom_nodes[0].provided_class_types
        )
        assert len(efficiency_classes) == 40
        readiness = self.system.session_service.desktop_readiness(
            self.session.session_id
        )
        assert readiness["desktop_ready"] is True
        readiness_report = readiness["readiness_report"]
        assert readiness_report["attempt_number"] == 2
        assert self.readiness_probe_calls == 2
        readiness_checks = {
            item["name"]: item["status"]
            for item in readiness_report["checks"]
        }
        assert readiness_checks["agent_panel_capabilities"] == "passed"
        assert readiness_checks["native_http_probe"] == "passed"
        assert readiness_checks["native_websocket_probe"] == "passed"
        assert readiness_checks["local_execution_unused"] == "passed"
        assert efficiency_classes.issubset(
            self.worker_readiness_proofs[-1]["validated_class_types"]
        )
        served_paths = self.worker_readiness_proofs[-1][
            "served_extension_paths"
        ]
        for package_id in (
            "comfyui-agent-panel",
            "hermes-nous",
            "efficiency-nodes-comfyui",
        ):
            assert any(
                path.startswith("/extensions/" + package_id + "/")
                for path in served_paths
            )
        identity = self.system.session_service.readiness_validator.identity(
            self.session,
            manifest,
            profile,
        )
        attempts = self.system.jobs.list_readiness_attempts(**identity)
        assert [item.ready for item in attempts] == [False, True]
        assert next(
            item
            for item in attempts[0].checks
            if item.name == "approved_ui_asset_digests"
        ).status == "failed"
        with closing(sqlite3.connect(self.system.database)) as connection:
            readiness_count = connection.execute(
                "SELECT COUNT(*) FROM readiness_reports"
            ).fetchone()[0]
        assert readiness_count == 2
        self.steps.append(4)

        # 5. The official-Remote-shaped local endpoint exposes native resources.
        self.cookie = self._open_desktop()
        object_info = self._desktop_get("/object_info", self.cookie)
        models = self._desktop_get("/models/checkpoints", self.cookie)
        assets = self._desktop_get("/assets/theme.css", self.cookie)
        assert object_info.status == models.status == assets.status == 200
        assert "KSampler" in json.loads(object_info.body)
        assert "model-a.safetensors" in json.loads(models.body)
        assert assets.body.startswith(b"/* synthetic")
        self.steps.append(5)

        # 6. The visual bootstrap and Agent Panel contract crossed the boundary.
        members = self.system.worker.installed_profile_members
        assert "bootstrap/current.json" in members
        assert "palettes/midnight.json" in members
        assert any(
            item.startswith("backgrounds/") and item.endswith(".jpg")
            for item in members
        )
        assert {
            item.package_id: item.web_sha256
            for item in self.ui_measurements
        } == {
            item.package_id: item.web_sha256
            for item in self.baseline.ui_packages
        }
        with tarfile.open(self.initial_profile.archive_private_path, "r:gz") as archive:
            settings_stream = archive.extractfile("settings/comfy.settings.json")
            settings = json.loads(settings_stream.read())
        assert settings["comfyui-mcp.bridgeUrl.single"] == (
            "/cloud-run/api/agent/ws"
        )
        assert settings["comfyui-mcp.remoteComfyuiUrl"] == ""
        assert settings["Comfy.Canvas.BackgroundImage"].startswith(
            "/api/view?filename=cloud-vast/backgrounds/"
        )
        assert settings["Comfy.Canvas.BackgroundImage"].endswith(
            "&type=input"
        )
        agent_status = self._desktop_get("/comfyui_mcp_panel/status", self.cookie)
        agent_socket = self._desktop_get("/cloud-run/api/agent/ws", self.cookie)
        assert json.loads(agent_status.body)["running"] is True
        assert agent_socket.status == 200
        assert self.agent.opened[-1][1] == self.session.session_id
        native_policy = NativeRoutePolicy()
        for method, path in (
            ("GET", "/comfyui_mcp_panel/civitai"),
            ("GET", "/comfyui_mcp_panel/training"),
            ("GET", "/comfyui_mcp_panel/apps"),
            ("GET", "/manager/queue"),
            ("POST", "/api/restart"),
            ("POST", "/reload"),
            ("GET", "/arbitrary-backend"),
        ):
            assert native_policy.classify(method, path) is None
        self.steps.append(6)

        # 7-9. Native frames, atomic terminal identity and persistent output.
        first, prompt_id = self._desktop_run(payload, request_id="native-main")
        assert first.state == JobState.SUCCEEDED
        events = self.system.jobs.list_events(first.job_id, 0)
        assert [item.event_type for item in events] == [
            "executing",
            "progress",
            "progress_state",
            "progress_text",
            "b_preview",
            "executed",
            "execution_success",
        ]
        self.steps.append(7)
        snapshot = asyncio.run(self.system.worker.snapshot(first.job_id, 0))
        assert snapshot["prompt_id"] == prompt_id == first.remote_prompt_id
        assert snapshot["last_sequence"] == 7
        self.steps.append(8)
        transfers = self.system.jobs.list_transfers(first.job_id)
        persistent = [item for item in transfers if item.artifact_id.startswith("output-")]
        assert len(persistent) == 1
        assert persistent[0].state == TransferState.VERIFIED
        assert Path(persistent[0].private_path).is_file()
        history = self.system.worker.native_history_descriptors[first.job_id]
        assert any(item.get("type") == "temp" for item in history)
        assert all(item.artifact_id != "preview.png" for item in transfers)
        self.steps.append(9)

        # 10. Reopening the Vast Desktop catches up but cannot resubmit.
        effects = dict(self.system.worker.native_prompt_effects)
        self._reopen_vast_desktop()
        asyncio.run(
            self.system.session_service.reconcile_session_once(
                self.session.session_id
            )
        )
        assert effects == self.system.worker.native_prompt_effects
        self.steps.append(10)

        # 11. A fresh service object over the same SQLite is equally idempotent.
        self._reconstruct_local_service()
        assert effects == self.system.worker.native_prompt_effects
        assert self.system.jobs.get_job(first.job_id).state == JobState.SUCCEEDED
        self.steps.append(11)

        # 12. One additive Agent edit provisions a delta and queues batch count 2.
        edited = _agent_edited_canvas()
        self.agent.graph_edits.append(
            {
                "added_model": "model-b.safetensors",
                "added_input": "input-b.png",
            }
        )
        before_manifests = len(self.system.worker.manifest_calls)
        batch = []
        after_first_delta = None
        for request_id in ("agent-batch-1", "agent-batch-2"):
            job, _batch_prompt = self._desktop_run(
                edited,
                request_id=request_id,
            )
            batch.append(job)
            if after_first_delta is None:
                after_first_delta = len(self.system.worker.manifest_calls)
        assert all(job.state == JobState.SUCCEEDED for job in batch)
        assert [job.queue_position for job in batch] == sorted(
            job.queue_position for job in batch
        )
        assert len({job.queue_position for job in batch}) == 2
        assert after_first_delta > before_manifests
        assert len(self.system.worker.manifest_calls) == after_first_delta
        delta_digests = {
            item["manifest_digest"]
            for item in self.system.worker.manifest_calls[before_manifests:]
        }
        assert len(delta_digests) == 1
        assert self.system.worker.download_count("model-b.safetensors") == 1
        assert self.system.worker.download_count("input-b.png") == 1
        self.agent.ordered_batches.append(tuple(job.job_id for job in batch))
        assert len(self.agent.ordered_batches[0]) == 2
        self.steps.append(12)

        # 13. Concurrent safe revisions remain explicitly recoverable on both sides.
        local_profile, remote_profile, conflict = self._profile_conflict()
        assert local_profile.archive_sha256 != remote_profile.archive_sha256
        assert conflict.local_label == "Local"
        assert conflict.remote_label == "Cloud Vast"
        assert self.profile_store.conflicts(unresolved_only=True) == (conflict,)
        self.steps.append(13)

        # 14. A detail failure preserves the safety card. Progress after a
        # manual review is append-only and cannot revoke that review.
        from cloud_run.routes import _active_session_payloads

        with mock.patch(
            "cloud_run.routes._session_payload",
            side_effect=RuntimeError("private-detail-marker"),
        ):
            cards, detail_error = _active_session_payloads(
                self.system.service
            )
        assert detail_error == (
            "Active session details are temporarily unavailable."
        )
        card = next(
            item
            for item in cards
            if item["session_id"] == self.session.session_id
        )
        assert card["can_destroy"] is True
        assert card["billing_may_continue"] is True
        assert "private-detail-marker" not in repr(card)

        manual_review = None
        if self.deadline_mode == "none":
            manual_review = self.system.review_destroy(self.session)
        self._record_safe_journal(first)
        journal = self.system.jobs.list_journal(self.session.session_id)
        rendered = repr(journal)
        assert journal and journal[-1].event_cursor == 7
        assert journal[-1].byte_cursor == 29_347_469_703
        assert SEEDED_SECRET not in rendered
        assert len(rendered.encode("utf-8")) < 64 * 1024
        self.steps.append(14)

        # 15. Closing both surfaces never ends billing; only the authorized path does.
        asyncio.run(self.relay.close())
        assert len(self.system.vast.inventory) == 1
        assert self.system.vast.destroy_count == 0
        destroy_intent_seen = []
        original_destroy = self.system.vast.destroy_instance

        async def destroy_after_intent(api_key, instance_id):
            stored = self.system.sessions.get(self.session.session_id)
            destroy_intent_seen.append(stored.destroy_requested)
            return await original_destroy(api_key, instance_id)

        self.system.vast.destroy_instance = destroy_after_intent
        if self.deadline_mode == "finite":
            destroyed = self.system.expire_deadline(self.session)
        else:
            destroyed = self.system.destroy(
                self.session,
                confirmed(manual_review),
            )
        assert destroyed.state == SessionState.DESTROYED
        assert self.system.vast.inventory == []
        assert destroyed.public_payload()["billing_may_continue"] is False
        assert self.system.vast.destroy_count == 1
        assert destroy_intent_seen == [True]
        self.steps.append(15)

        # 16. The ordinary local Run still reaches only the existing local backend.
        self._local_run("after")
        assert self.system.local_prompt_posts == []
        assert self.local_prompt_calls == ["before", "after"]
        assert self.local_execution_guard.count(self.session.session_id) == 0
        self.steps.append(16)

        return {
            "steps": tuple(self.steps),
            "deadline_destroyed_once": (
                self.deadline_mode == "finite"
                and self.system.vast.destroy_count == 1
            ),
            "inventory": tuple(self.system.vast.inventory),
            "billing_may_continue": destroyed.public_payload()[
                "billing_may_continue"
            ],
            "local_backend_starts": self.local_backend_starts,
            "local_prompt_calls": len(self.local_prompt_calls),
        }


class FakeDesktopBridgeCampaignTests(unittest.TestCase):
    def test_manual_campaign_survives_both_desktops_until_explicit_destroy(self):
        with blocked_external_network():
            campaign = OfflineDesktopBridgeCampaign(deadline_mode="none")
            self.addCleanup(campaign.close)
            evidence = campaign.run()

        self.assertEqual(evidence["steps"], tuple(range(1, 17)))

    def test_finite_campaign_uses_only_authorized_deadline_path(self):
        with blocked_external_network():
            campaign = OfflineDesktopBridgeCampaign(deadline_mode="finite")
            self.addCleanup(campaign.close)
            evidence = campaign.run()

        self.assertTrue(evidence["deadline_destroyed_once"])
        self.assertEqual(evidence["inventory"], ())
        self.assertFalse(evidence["billing_may_continue"])

    def test_bridge_uses_existing_prompt_server_lifecycle_only(self):
        with blocked_external_network():
            campaign = OfflineDesktopBridgeCampaign(deadline_mode="none")
            self.addCleanup(campaign.close)
            evidence = campaign.run()

        self.assertEqual(evidence["local_backend_starts"], 0)
        self.assertEqual(evidence["local_prompt_calls"], 2)


if __name__ == "__main__":
    unittest.main()

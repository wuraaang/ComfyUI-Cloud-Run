import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from cloud_run.artifacts import ArtifactResolution
from cloud_run.capture import CompiledCapture
from cloud_run.job_repository import JobRepository
from cloud_run.manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    DependencyManifest,
    PythonWheelSpec,
    SourceSpec,
)
from cloud_run.models import CloudJob, CloudSession, JobState, SessionState
from cloud_run.relay import RelaySyncResult
from cloud_run.repository import SessionRepository
from cloud_run.resolver import NodeResolution
from cloud_run.session_service import (
    DeadlineSynchronizationError,
    DeadlineValidationError,
    DestroyConfirmationError,
    IncompatibleSession,
    PreflightBlocked,
    PreflightRow,
    SessionBusy,
    SessionExecutionError,
    SessionService,
    SessionServiceError,
)
from cloud_run.worker_release import WorkerRelease


def capture_payload():
    return {
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


def worker_release():
    return WorkerRelease.from_payload(
        {
            "schema_version": 1,
            "template_hash_id": "1" * 32,
            "worker_commit": "a" * 40,
            "worker_archive_sha256": "b" * 64,
            "protocol_version": "1",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "python_version": "3.13.12",
            "worker_port": 8765,
        }
    )


class FakeResolver:
    def __init__(self, resolution):
        self.resolution = resolution
        self.calls = []
        self.agent_suggestions = []

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes,
    ):
        self.calls.append(
            (capture.capture_id, explicit_output_allowance_bytes)
        )
        return self.resolution

    def register_agent_suggestion(self, payload):
        self.agent_suggestions.append(payload)
        return types.SimpleNamespace(
            public_payload=lambda: {
                "class_type": payload["class_type"],
                "source_kind": "agent",
                "approved": False,
            }
        )


class FakeOfferSearch:
    def __init__(self):
        self.calls = 0
        self.mutations = []

    async def __call__(self, *, disk_gb):
        self.calls += 1
        self.disk_gb = disk_gb
        return [{"offer_id": 42}]


def blocked_resolution():
    return types.SimpleNamespace(
        node_rows=(
            NodeResolution("KSampler", "resolved", "core"),
            NodeResolution(
                "Fancy",
                "mapping_required",
                "registry",
                reason="Approve an immutable source.",
            ),
        ),
        artifact_rows=(),
        custom_nodes=(),
        artifacts=(),
        output_allowance_bytes=8,
        disk_gb=80,
        rentable=False,
    )


def resolved_resolution():
    artifact = ArtifactSpec(
        artifact_id="model-" + "a" * 64,
        kind="model",
        logical_name="upscale.pth",
        destination="models/upscale_models/upscale.pth",
        size_bytes=12,
        sha256="a" * 64,
        source=SourceSpec(
            "local-upload",
            "local-upload:approved-model",
        ),
    )
    return types.SimpleNamespace(
        node_rows=(NodeResolution("KSampler", "resolved", "core"),),
        artifact_rows=(
            ArtifactResolution(
                node_id="1",
                class_type="LoadUpscaleModel",
                input_name="model_name",
                kind="model",
                status="resolved",
                destination=artifact.destination,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                artifact_id=artifact.artifact_id,
            ),
        ),
        custom_nodes=(),
        artifacts=(artifact,),
        output_allowance_bytes=8,
        disk_gb=80,
        rentable=True,
    )


def huggingface_resolution():
    revision = "b" * 40
    artifact = ArtifactSpec(
        artifact_id="model-" + "c" * 64,
        kind="model",
        logical_name="example.safetensors",
        destination="models/diffusion_models/example.safetensors",
        size_bytes=4096,
        sha256="c" * 64,
        source=SourceSpec(
            "huggingface",
            (
                "https://huggingface.co/example/public-model/resolve/"
                + revision
                + "/files/example.safetensors"
            ),
            immutable_revision=revision,
        ),
    )
    return types.SimpleNamespace(
        node_rows=(NodeResolution("KSampler", "resolved", "core"),),
        artifact_rows=(
            ArtifactResolution(
                node_id="1",
                class_type="UNETLoader",
                input_name="model_name",
                kind="model",
                status="resolved",
                destination=artifact.destination,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                artifact_id=artifact.artifact_id,
            ),
        ),
        custom_nodes=(),
        artifacts=(artifact,),
        output_allowance_bytes=8,
        disk_gb=80,
        rentable=True,
    )


class PreflightRowTests(unittest.TestCase):
    revision = "a" * 40
    locator = (
        "https://huggingface.co/example/public-model/resolve/"
        + revision
        + "/files/example.safetensors"
    )

    def row(self, **overrides):
        values = {
            "dependency_id": "artifact:model-example",
            "kind": "model",
            "display_name": "example.safetensors",
            "status": "resolved",
            "source_kind": "huggingface",
            "immutable_revision": self.revision,
            "size_bytes": 4096,
            "sha256": "c" * 64,
            "destination": "models/diffusion_models/example.safetensors",
            "reason": None,
            "source_locator": self.locator,
        }
        values.update(overrides)
        return PreflightRow(**values)

    def test_resolved_huggingface_row_exposes_only_its_pinned_locator(self):
        row = self.row()

        self.assertEqual(row.source_locator, self.locator)
        self.assertEqual(row.public_payload()["source_locator"], self.locator)

    def test_locator_is_forbidden_for_other_sources_or_unresolved_rows(self):
        invalid = {
            "local upload": {
                "source_kind": "local-upload",
                "immutable_revision": None,
            },
            "r2": {"source_kind": "r2", "immutable_revision": None},
            "custom node": {
                "kind": "custom_node",
                "source_kind": "approved",
            },
            "mapping required": {
                "status": "mapping_required",
                "source_kind": None,
                "immutable_revision": None,
                "size_bytes": None,
                "sha256": None,
                "reason": "Native model metadata is missing or ambiguous.",
            },
            "unsupported": {
                "status": "unsupported",
                "source_kind": None,
                "immutable_revision": None,
                "size_bytes": None,
                "sha256": None,
                "reason": "Native model metadata is invalid.",
            },
        }

        for label, overrides in invalid.items():
            with self.subTest(label=label):
                with self.assertRaises(SessionServiceError):
                    self.row(**overrides)

    def test_locator_and_revision_are_jointly_validated_as_source_spec(self):
        invalid = {
            "mutable": self.locator.replace(self.revision, "main"),
            "wrong revision": self.locator.replace(self.revision, "b" * 40),
            "other host": self.locator.replace(
                "huggingface.co",
                "example.com",
            ),
            "userinfo": self.locator.replace(
                "huggingface.co",
                "user@huggingface.co",
            ),
            "port": self.locator.replace(
                "huggingface.co",
                "huggingface.co:443",
            ),
            "fragment": self.locator + "#secret",
            "query": self.locator + "?download=true",
            "percent": self.locator.replace("files", "%66iles"),
        }

        for label, locator in invalid.items():
            with self.subTest(label=label):
                with self.assertRaises(SessionServiceError):
                    self.row(source_locator=locator)

    def test_legacy_and_new_payload_shapes_round_trip_exactly(self):
        new_payload = self.row().public_payload()
        restored = PreflightRow.from_payload(new_payload)
        self.assertEqual(restored, self.row())

        legacy_payload = dict(new_payload)
        legacy_payload.pop("source_locator")
        legacy = PreflightRow.from_payload(legacy_payload)
        self.assertIsNone(legacy.source_locator)
        self.assertEqual(
            set(legacy.public_payload()),
            set(new_payload),
        )

        with self.assertRaises(SessionServiceError):
            PreflightRow.from_payload({**new_payload, "extra": True})


class SessionServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "private" / "sessions.sqlite3"
        self.repository = JobRepository(self.path)
        self.capture = CompiledCapture.from_payload(capture_payload())
        self.repository.save_capture(self.capture, created_at=100.0)
        self.offer_search = FakeOfferSearch()

    def service(self, resolution):
        return SessionService(
            job_repository=self.repository,
            resolver=FakeResolver(resolution),
            offer_search=self.offer_search,
            release=worker_release(),
            clock=lambda: 100.0,
            id_factory=lambda: "preflight-1",
        )

    def test_preflight_blocks_offer_search_until_every_row_is_resolved(self):
        service = self.service(blocked_resolution())

        result = asyncio.run(service.preflight(self.capture.capture_id))

        self.assertFalse(result.rentable)
        self.assertIsNone(result.manifest_digest)
        self.assertEqual(result.rows[1].status, "mapping_required")
        with self.assertRaises(PreflightBlocked):
            asyncio.run(service.search_offers(result.preflight_id))
        self.assertEqual(self.offer_search.calls, 0)
        self.assertEqual(self.offer_search.mutations, [])

    def test_mapping_required_row_exposes_only_the_pinned_candidate_for_approval(self):
        candidate_payload = {
            "class_type": "Fancy",
            "source_kind": "registry",
            "candidate_digest": "d" * 64,
            "repository_url": "https://github.com/example/fancy",
            "revision": "e" * 40,
            "package_id": "example.fancy",
            "archive_complete": True,
            "wheels_complete": True,
            "approved": False,
            "origin_source_kind": "registry",
        }
        candidate = types.SimpleNamespace(
            revision="e" * 40,
            source_kind="registry",
            public_payload=lambda: dict(candidate_payload),
        )
        mapping_repository = types.SimpleNamespace(
            candidates=lambda class_type: (
                [candidate] if class_type == "Fancy" else []
            ),
            approved=lambda _class_type: None,
        )
        service = SessionService(
            job_repository=self.repository,
            resolver=FakeResolver(blocked_resolution()),
            offer_search=self.offer_search,
            mapping_repository=mapping_repository,
            release=worker_release(),
            clock=lambda: 100.0,
            id_factory=lambda: "preflight-mapping",
        )

        result = asyncio.run(service.preflight(self.capture.capture_id))
        public = result.public_payload()
        reopened = service.get_preflight(result.preflight_id)

        self.assertEqual(
            public["rows"][1]["mapping_candidate"],
            candidate_payload,
        )
        self.assertEqual(
            reopened.rows[1].mapping_candidate,
            candidate_payload,
        )
        self.assertNotIn("candidate_json", repr(public))
        self.assertNotIn("payload", repr(public))

    def test_resolved_preflight_persists_manifest_and_exact_totals(self):
        service = self.service(resolved_resolution())

        result = asyncio.run(service.preflight(self.capture.capture_id))
        reopened = SessionService(
            job_repository=JobRepository(self.path),
            resolver=FakeResolver(resolved_resolution()),
            offer_search=self.offer_search,
            release=worker_release(),
        )
        offers = asyncio.run(reopened.search_offers(result.preflight_id))

        self.assertTrue(result.rentable)
        self.assertEqual(result.transfer_bytes, 12)
        self.assertEqual(result.output_allowance_bytes, 8)
        self.assertEqual(result.disk_gb, 80)
        self.assertEqual(len(result.manifest_digest), 64)
        self.assertIsNotNone(
            self.repository.get_manifest(result.manifest_digest)
        )
        self.assertEqual(offers, [{"offer_id": 42}])
        self.assertEqual(self.offer_search.calls, 1)
        self.assertEqual(self.offer_search.disk_gb, 80)
        self.assertEqual(self.offer_search.mutations, [])
        public = result.public_payload()
        self.assertNotIn("private_path", repr(public))
        self.assertNotIn("'source':", repr(public))

    def test_huggingface_locator_round_trips_through_public_and_storage(self):
        resolution = huggingface_resolution()
        service = self.service(resolution)

        result = asyncio.run(service.preflight(self.capture.capture_id))
        public = result.public_payload()
        reopened = service.get_preflight(result.preflight_id)
        artifact = resolution.artifacts[0]

        self.assertEqual(
            public["rows"][1]["source_locator"],
            artifact.source.locator,
        )
        self.assertEqual(
            reopened.rows[1].source_locator,
            artifact.source.locator,
        )
        self.assertTrue(result.rentable)
        self.assertEqual(
            asyncio.run(service.search_offers(result.preflight_id)),
            [{"offer_id": 42}],
        )

    def test_non_huggingface_row_does_not_expose_locator(self):
        result = asyncio.run(
            self.service(resolved_resolution()).preflight(
                self.capture.capture_id
            )
        )

        self.assertIsNone(result.rows[1].source_locator)

    def test_tampered_or_missing_manifest_blocks_offer_search(self):
        result = asyncio.run(
            self.service(resolved_resolution()).preflight(
                self.capture.capture_id
            )
        )
        with self.repository._connect() as connection:
            connection.execute(
                """
                UPDATE manifests SET manifest_json = ?
                WHERE manifest_digest = ?
                """,
                ('{"tampered":true}', result.manifest_digest),
            )
            connection.commit()

        with self.assertRaises(PreflightBlocked):
            asyncio.run(
                self.service(resolved_resolution()).search_offers(
                    result.preflight_id
                )
            )
        self.assertEqual(self.offer_search.calls, 0)

    def test_missing_reviewed_release_keeps_resolved_preflight_non_rentable(self):
        service = SessionService(
            job_repository=self.repository,
            resolver=FakeResolver(resolved_resolution()),
            offer_search=self.offer_search,
            release=None,
            clock=lambda: 100.0,
            id_factory=lambda: "preflight-no-release",
        )

        result = asyncio.run(service.preflight(self.capture.capture_id))

        self.assertFalse(result.rentable)
        self.assertIsNone(result.manifest_digest)
        with self.assertRaises(PreflightBlocked):
            asyncio.run(service.search_offers(result.preflight_id))
        self.assertEqual(self.offer_search.calls, 0)

    def test_unavailable_direct_upload_is_blocked_before_offer_search(self):
        resolution = resolved_resolution()
        resolution.local_artifacts = ()
        service = self.service(resolution)

        result = asyncio.run(service.preflight(self.capture.capture_id))

        self.assertFalse(result.rentable)
        self.assertIsNone(result.manifest_digest)
        with self.assertRaises(PreflightBlocked):
            asyncio.run(service.search_offers(result.preflight_id))
        self.assertEqual(self.offer_search.calls, 0)


def capture_with_seed(seed):
    payload = capture_payload()
    payload["output"]["1"]["inputs"]["seed"] = seed
    return CompiledCapture.from_payload(payload)


def artifact_resolution(*artifacts, custom_nodes=()):
    return types.SimpleNamespace(
        node_rows=(NodeResolution("KSampler", "resolved", "core"),),
        artifact_rows=tuple(
            ArtifactResolution(
                node_id="1",
                class_type="KSampler",
                input_name=artifact.logical_name,
                kind=artifact.kind,
                status="resolved",
                destination=artifact.destination,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                artifact_id=artifact.artifact_id,
            )
            for artifact in artifacts
        ),
        custom_nodes=tuple(custom_nodes),
        artifacts=tuple(artifacts),
        output_allowance_bytes=1024,
        disk_gb=80,
        rentable=True,
    )


def local_artifact(name, digest_character, *, kind="input"):
    digest = digest_character * 64
    return ArtifactSpec(
        artifact_id=name,
        kind=kind,
        logical_name=name,
        destination=(
            "models/checkpoints/" + name
            if kind == "model"
            else "input/" + name
        ),
        size_bytes=10,
        sha256=digest,
        source=SourceSpec("local-upload", "local-upload:" + name),
    )


def custom_node(revision_character):
    revision = revision_character * 40
    archive = ArtifactSpec(
        artifact_id="custom-node-acme-" + revision_character,
        kind="custom_node_archive",
        logical_name="acme.nodes",
        destination="custom_nodes/acme.nodes",
        size_bytes=10,
        sha256=revision_character * 64,
        source=SourceSpec(
            "local-upload",
            "local-upload:custom-node-acme-" + revision_character,
        ),
    )
    return CustomNodeSpec(
        package_id="acme.nodes",
        repository_url="https://github.com/acme/nodes",
        revision=revision,
        archive=archive,
        wheels=tuple(),
        provided_class_types=("KSampler",),
    )


class PerCaptureResolver:
    def __init__(self, resolutions):
        self.resolutions = resolutions
        self.calls = []

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes,
    ):
        self.calls.append(
            (capture.capture_id, explicit_output_allowance_bytes)
        )
        return self.resolutions[capture.capture_id]


class SequentialWorker:
    def __init__(self, *, terminal_state="succeeded", error=None):
        self.manifest_calls = []
        self.job_calls = []
        self.terminal_state = terminal_state
        self.error = error
        self.claimed = False
        self.claim_calls = 0
        self.deadline_calls = []

    async def health(self):
        return {
            "protocol_version": "1",
            "claimed": self.claimed,
        }

    async def claim(self):
        self.claimed = True
        self.claim_calls += 1
        return {
            "protocol_version": "1",
            "session_id": "session-boot",
            "claimed": True,
        }

    async def update_deadline(self, payload):
        self.deadline_calls.append(payload)
        return {
            "mode": payload["mode"],
            "deadline_at": payload.get("deadline_at"),
            "retrieval_grace_seconds": payload.get(
                "retrieval_grace_seconds",
                0,
            ),
            "destroy_intent": False,
            "destroy_requested": False,
        }

    async def apply_manifest(self, payload):
        self.manifest_calls.append(payload)
        return {
            "transaction_id": "provision-" + payload["manifest_digest"],
            "manifest_digest": payload["manifest_digest"],
            "state": "ready",
            "planned_restarts": 0,
            "repair_restarts": 0,
            "missing_class_types": [],
            "missing_artifacts": [],
        }

    async def start_job(self, payload):
        self.job_calls.append(payload)
        return {
            "job_id": payload["job_id"],
            "state": self.terminal_state,
            "prompt_id": "11111111-1111-1111-1111-111111111111",
            "last_sequence": 0,
            "outputs": [],
            "error": self.error,
        }


class SequentialRelay:
    def __init__(self, worker):
        self.worker = worker
        self.calls = []

    async def sync_job(self, job_id):
        self.calls.append(job_id)
        return RelaySyncResult(
            job_id=job_id,
            state=self.worker.terminal_state,
            last_sequence=0,
            events=(),
            outputs=(),
            error=self.worker.error,
        )


class ReusableSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "private" / "sessions.sqlite3"
        self.jobs = JobRepository(self.path)
        self.sessions = SessionRepository(self.path)
        self.first_capture = capture_with_seed(11)
        self.second_capture = capture_with_seed(12)
        self.third_capture = capture_with_seed(13)
        self.jobs.save_capture(self.first_capture, created_at=100.0)
        self.jobs.save_capture(self.second_capture, created_at=100.0)
        self.jobs.save_capture(self.third_capture, created_at=100.0)
        self.model = local_artifact("model-a.safetensors", "a", kind="model")
        self.input_a = local_artifact("input-a.jpg", "b")
        self.input_b = local_artifact("input-b.jpg", "c")
        self.first_resolution = artifact_resolution(
            self.model,
            self.input_a,
        )
        self.second_resolution = artifact_resolution(
            self.model,
            self.input_a,
            self.input_b,
        )
        self.worker = SequentialWorker()
        self.ids = iter(
            (
                "preflight-first",
                "job-first",
                "preflight-second",
                "job-second",
                "preflight-extra",
                "job-extra",
            )
        )
        self.service = SessionService(
            job_repository=self.jobs,
            session_repository=self.sessions,
            resolver=PerCaptureResolver(
                {
                    self.first_capture.capture_id: self.first_resolution,
                    self.second_capture.capture_id: self.second_resolution,
                    self.third_capture.capture_id: self.first_resolution,
                }
            ),
            release=worker_release(),
            worker_factory=lambda _session: self.worker,
            relay_factory=lambda worker, _session: SequentialRelay(worker),
            clock=lambda: 100.0,
            id_factory=lambda: next(self.ids),
        )
        initial = DependencyManifest(
            schema_version=1,
            protocol_version="1",
            comfyui_core_version="0.29.0",
            comfyui_frontend_version="1.47.10",
            worker_version="a" * 40,
            prompt_digest=self.first_capture.prompt_digest,
            custom_nodes=(),
            artifacts=(self.model, self.input_a),
            output_allowance_bytes=1024,
            disk_gb=80,
        )
        self.initial_manifest = initial
        self.jobs.save_manifest(
            initial.digest,
            initial.canonical_bytes().decode("utf-8"),
            created_at=100.0,
        )
        session = CloudSession.new(
            "session-key",
            session_id="session-1",
            manifest_digest=initial.digest,
            deadline_at=7300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        ).transition(
            SessionState.READY,
            now=100.0,
            installed_manifest_digest=initial.digest,
            instance_id="77",
            worker_base_url="http://8.8.8.8:30000",
            provider_token="provider-token",
            session_secret_hex="d" * 64,
        )
        self.sessions.create_or_get(session)

    def test_apply_manifest_polls_and_persists_only_sanitized_progress(self):
        service = self.service
        service.job_poll_interval_seconds = 0
        manifest = self.initial_manifest
        session = self.sessions.get("session-1")
        total_bytes = sum(
            artifact.size_bytes for artifact in manifest.artifacts
        )

        class PollingWorker:
            def __init__(self):
                self.release = asyncio.Event()
                self.transaction_calls = []
                self.polls = 0

            async def apply_manifest(self, _payload):
                await self.release.wait()
                return {
                    "transaction_id": "provision-" + manifest.digest,
                    "manifest_digest": manifest.digest,
                    "state": "ready",
                    "planned_restarts": 0,
                    "repair_restarts": 0,
                    "missing_class_types": [],
                    "missing_artifacts": [],
                    "progress": {
                        "phase": "ready",
                        "dependency_id": None,
                        "transferred_bytes": total_bytes,
                        "total_bytes": total_bytes,
                    },
                }

            async def transaction(self, transaction_id):
                self.transaction_calls.append(transaction_id)
                self.polls += 1
                if self.polls == 1:
                    return None
                progress = {
                    "phase": (
                        "model_transfer"
                        if self.polls == 2
                        else "digest_verification"
                    ),
                    "dependency_id": self_outer.model.artifact_id,
                    "transferred_bytes": (
                        self_outer.model.size_bytes // 2
                        if self.polls == 2
                        else self_outer.model.size_bytes
                    ),
                    "total_bytes": total_bytes,
                }
                if self.polls == 3:
                    self.release.set()
                return {
                    "transaction_id": transaction_id,
                    "manifest_digest": manifest.digest,
                    "state": "applying",
                    "planned_restarts": 0,
                    "repair_restarts": 0,
                    "missing_class_types": [],
                    "missing_artifacts": [],
                    "progress": progress,
                }

        self_outer = self

        async def exercise():
            worker = PollingWorker()
            result = await asyncio.wait_for(
                service._apply_manifest(
                    worker,
                    session,
                    manifest,
                    transfer_job_id="bootstrap:session-1",
                    capture=self.first_capture,
                ),
                timeout=0.2,
            )
            return worker, result

        worker, result = asyncio.run(exercise())
        latest = self.jobs.latest_provision_transaction("session-1")

        self.assertEqual(result["state"], "ready")
        self.assertGreaterEqual(worker.polls, 3)
        self.assertEqual(
            set(worker.transaction_calls),
            {"provision-" + manifest.digest},
        )
        self.assertEqual(latest.phase, "ready")
        self.assertIsNone(latest.current_dependency_id)
        self.assertEqual(latest.transferred_bytes, total_bytes)
        self.assertEqual(latest.total_bytes, total_bytes)
        self.assertNotIn("huggingface.co", repr(latest))
        self.assertNotIn("provider-token", repr(latest))

    def test_legacy_apply_response_without_progress_remains_accepted(self):
        result = asyncio.run(
            self.service._apply_manifest(
                self.worker,
                self.sessions.get("session-1"),
                self.initial_manifest,
                transfer_job_id="bootstrap:session-1",
                capture=self.first_capture,
            )
        )

        self.assertEqual(result["state"], "ready")
        self.assertNotIn("progress", result)
        self.assertIsNone(
            self.jobs.latest_provision_transaction("session-1")
        )

    def test_hostile_worker_progress_is_rejected_without_persistence(self):
        manifest = self.initial_manifest
        total_bytes = sum(
            artifact.size_bytes for artifact in manifest.artifacts
        )
        base_progress = {
            "phase": "model_transfer",
            "dependency_id": self.model.artifact_id,
            "transferred_bytes": 1,
            "total_bytes": total_bytes,
        }
        hostile = (
            {**base_progress, "phase": "unknown"},
            {**base_progress, "dependency_id": "https://example.com/model"},
            {**base_progress, "transferred_bytes": total_bytes + 1},
            {**base_progress, "total_bytes": total_bytes + 1},
            {
                **base_progress,
                "source_url": "https://huggingface.co/private?token=secret",
            },
        )

        for progress in hostile:
            with self.subTest(progress=progress):
                worker = SequentialWorker()

                async def apply_manifest(_payload, value=progress):
                    return {
                        "transaction_id": "provision-" + manifest.digest,
                        "manifest_digest": manifest.digest,
                        "state": "ready",
                        "planned_restarts": 0,
                        "repair_restarts": 0,
                        "missing_class_types": [],
                        "missing_artifacts": [],
                        "progress": value,
                    }

                worker.apply_manifest = apply_manifest
                with self.assertRaises(SessionExecutionError):
                    asyncio.run(
                        self.service._apply_manifest(
                            worker,
                            self.sessions.get("session-1"),
                            manifest,
                            transfer_job_id="bootstrap:session-1",
                            capture=self.first_capture,
                        )
                    )
                self.assertIsNone(
                    self.jobs.latest_provision_transaction("session-1")
                )

    def test_observed_ready_progress_cannot_replace_failed_apply_result(self):
        self.service.job_poll_interval_seconds = 0
        manifest = self.initial_manifest
        total_bytes = sum(
            artifact.size_bytes for artifact in manifest.artifacts
        )

        class FinalFailureWorker:
            def __init__(self):
                self.release = asyncio.Event()

            async def apply_manifest(self, _payload):
                await self.release.wait()
                return {
                    "transaction_id": "provision-" + manifest.digest,
                    "manifest_digest": manifest.digest,
                    "state": "failed",
                    "planned_restarts": 0,
                    "repair_restarts": 0,
                    "missing_class_types": [],
                    "missing_artifacts": [],
                    "progress": {
                        "phase": "environment_validation",
                        "dependency_id": None,
                        "transferred_bytes": total_bytes,
                        "total_bytes": total_bytes,
                    },
                }

            async def transaction(self, transaction_id):
                self.release.set()
                return {
                    "transaction_id": transaction_id,
                    "manifest_digest": manifest.digest,
                    "state": "ready",
                    "planned_restarts": 0,
                    "repair_restarts": 0,
                    "missing_class_types": [],
                    "missing_artifacts": [],
                    "progress": {
                        "phase": "ready",
                        "dependency_id": None,
                        "transferred_bytes": total_bytes,
                        "total_bytes": total_bytes,
                    },
                }

        async def exercise():
            await asyncio.wait_for(
                self.service._apply_manifest(
                    FinalFailureWorker(),
                    self.sessions.get("session-1"),
                    manifest,
                    transfer_job_id="bootstrap:session-1",
                    capture=self.first_capture,
                ),
                timeout=0.2,
            )

        with self.assertRaises(SessionExecutionError):
            asyncio.run(exercise())

        latest = self.jobs.latest_provision_transaction("session-1")
        self.assertEqual(latest.state, "failed")
        self.assertEqual(latest.phase, "environment_validation")

    def test_invalid_polled_progress_cancels_and_consumes_active_apply(self):
        self.service.job_poll_interval_seconds = 0
        manifest = self.initial_manifest

        class InvalidPollingWorker:
            def __init__(self):
                self.cancelled = False

            async def apply_manifest(self, _payload):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

            async def transaction(self, transaction_id):
                return {
                    "transaction_id": transaction_id,
                    "manifest_digest": manifest.digest,
                    "state": "applying",
                    "planned_restarts": 0,
                    "repair_restarts": 0,
                    "missing_class_types": [],
                    "missing_artifacts": [],
                    "progress": {
                        "phase": "model_transfer",
                        "dependency_id": "https://example.com/model",
                        "transferred_bytes": 1,
                        "total_bytes": 20,
                    },
                }

        async def exercise():
            worker = InvalidPollingWorker()
            with self.assertRaises(SessionExecutionError):
                await self.service._apply_manifest(
                    worker,
                    self.sessions.get("session-1"),
                    manifest,
                    transfer_job_id="bootstrap:session-1",
                    capture=self.first_capture,
                )
            return worker

        worker = asyncio.run(exercise())
        self.assertTrue(worker.cancelled)
        self.assertIsNone(
            self.jobs.latest_provision_transaction("session-1")
        )

    def test_two_compatible_jobs_reuse_one_session_and_transfer_only_delta(self):
        first = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.first_capture.capture_id,
                idempotency_key="job-key-1",
            )
        )
        second = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.second_capture.capture_id,
                idempotency_key="job-key-2",
            )
        )

        self.assertEqual(first.state, JobState.SUCCEEDED)
        self.assertEqual(second.state, JobState.SUCCEEDED)
        self.assertEqual(len(self.worker.job_calls), 2)
        self.assertEqual(len(self.worker.manifest_calls), 1)
        self.assertEqual(
            [
                item["artifact_id"]
                for item in self.worker.manifest_calls[0]["manifest"][
                    "artifacts"
                ]
            ],
            ["input-a.jpg", "input-b.jpg", "model-a.safetensors"],
        )
        self.assertEqual(
            self.sessions.get("session-1").state,
            SessionState.READY,
        )
        self.assertEqual(
            self.sessions.get("session-1").instance_id,
            "77",
        )

    def test_changed_package_revision_requires_new_session_without_mutation(self):
        first_node = custom_node("e")
        second_node = custom_node("f")
        initial = DependencyManifest(
            schema_version=1,
            protocol_version="1",
            comfyui_core_version="0.29.0",
            comfyui_frontend_version="1.47.10",
            worker_version="a" * 40,
            prompt_digest=self.first_capture.prompt_digest,
            custom_nodes=(first_node,),
            artifacts=(),
            output_allowance_bytes=1024,
            disk_gb=80,
        )
        self.jobs.save_manifest(
            initial.digest,
            initial.canonical_bytes().decode("utf-8"),
            created_at=100.0,
        )
        session = self.sessions.get("session-1")
        self.sessions.save(
            session.transition(
                SessionState.READY,
                installed_manifest_digest=initial.digest,
                manifest_digest=initial.digest,
                now=100.0,
            )
        )
        self.service.resolver.resolutions[
            self.second_capture.capture_id
        ] = artifact_resolution(custom_nodes=(second_node,))

        with self.assertRaises(IncompatibleSession):
            asyncio.run(
                self.service.submit_job(
                    "session-1",
                    capture_id=self.second_capture.capture_id,
                    idempotency_key="incompatible-key",
                )
            )

        self.assertEqual(self.worker.manifest_calls, [])
        self.assertEqual(self.worker.job_calls, [])
        self.assertEqual(
            self.sessions.get("session-1").state,
            SessionState.READY,
        )

    def test_empty_dependency_delta_refreshes_prompt_identity_without_provisioning_state(self):
        job = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.third_capture.capture_id,
                idempotency_key="fresh-prompt-key",
            )
        )

        self.assertEqual(job.state, JobState.SUCCEEDED)
        self.assertEqual(len(self.worker.manifest_calls), 1)
        self.assertEqual(
            self.worker.manifest_calls[0]["manifest_digest"],
            job.manifest_digest,
        )
        self.assertEqual(
            self.sessions.get("session-1").installed_manifest_digest,
            job.manifest_digest,
        )

    def test_boot_claims_provisions_deadline_and_records_verified_installed_set(self):
        initial_digest = self.sessions.get(
            "session-1"
        ).installed_manifest_digest
        boot = CloudSession.new(
            "boot-key",
            session_id="session-boot",
            manifest_digest=initial_digest,
            deadline_at=7300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.BOOTSTRAPPING,
        ).transition(
            SessionState.BOOTSTRAPPING,
            now=100.0,
            instance_id="88",
            worker_base_url="http://8.8.8.8:30001",
            provider_token="provider-token-boot",
            session_secret_hex="e" * 64,
        )
        self.sessions.create_or_get(boot)

        ready = asyncio.run(
            self.service.bootstrap_session("session-boot")
        )

        self.assertEqual(ready.state, SessionState.READY)
        self.assertEqual(ready.installed_manifest_digest, initial_digest)
        self.assertEqual(self.worker.claim_calls, 1)
        self.assertEqual(len(self.worker.manifest_calls), 1)
        self.assertEqual(
            self.worker.deadline_calls,
            [
                {
                    "mode": "finite",
                    "deadline_at": 7300.0,
                    "retrieval_grace_seconds": 300,
                }
            ],
        )
        self.assertEqual(
            [
                item.dependency_id
                for item in self.jobs.installed_set("session-boot")
            ],
            ["input-a.jpg", "model-a.safetensors"],
        )

    def test_ready_recovery_reauthenticates_manifest_and_deadline_without_create(self):
        self.worker.claimed = True

        recovered = asyncio.run(
            self.service.recover_session("session-1")
        )

        self.assertEqual(recovered.state, SessionState.READY)
        self.assertEqual(self.worker.claim_calls, 0)
        self.assertEqual(len(self.worker.manifest_calls), 1)
        self.assertEqual(
            self.worker.deadline_calls,
            [
                {
                    "mode": "finite",
                    "deadline_at": 7300.0,
                    "retrieval_grace_seconds": 300,
                }
            ],
        )

    def test_running_recovery_idempotently_resumes_remote_job_and_harvest(self):
        session = self.sessions.get("session-1")
        session = self.sessions.save(
            session.transition(SessionState.RUNNING, now=101.0)
        )
        running = CloudJob(
            job_id="recover-job",
            session_id=session.session_id,
            idempotency_key="recover-key",
            state=JobState.RUNNING,
            prompt_digest=self.first_capture.prompt_digest,
            capture_json=self.first_capture.canonical_payload(),
            manifest_digest=session.installed_manifest_digest,
            remote_prompt_id=(
                "11111111-1111-1111-1111-111111111111"
            ),
            sanitized_error=None,
            created_at=100.0,
            updated_at=101.0,
            version=1,
        )
        self.jobs.create_job(running)
        self.worker.claimed = True

        recovered = asyncio.run(
            self.service.recover_session("session-1")
        )

        self.assertEqual(recovered.state, SessionState.READY)
        self.assertEqual(
            self.sessions.get("session-1").state,
            SessionState.READY,
        )
        self.assertEqual(
            self.jobs.get_job("recover-job").state,
            JobState.SUCCEEDED,
        )
        self.assertEqual(len(self.worker.job_calls), 1)
        self.assertEqual(self.worker.manifest_calls, [])

    def test_execution_failure_returns_the_healthy_session_to_ready(self):
        self.worker.terminal_state = "failed"
        self.worker.error = {
            "code": "out_of_memory",
            "message": "Remote execution ran out of GPU memory.",
        }

        failed = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.first_capture.capture_id,
                idempotency_key="failed-key",
            )
        )

        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(
            failed.sanitized_error,
            "Remote execution ran out of GPU memory.",
        )
        self.assertEqual(
            self.sessions.get("session-1").state,
            SessionState.READY,
        )

    def test_running_remote_job_returns_immediately_then_finishes_in_background(self):
        async def scenario():
            released = asyncio.Event()
            self.worker.terminal_state = "running"

            class PendingRelay:
                async def sync_job(inner_self, job_id):
                    await released.wait()
                    return RelaySyncResult(
                        job_id=job_id,
                        state="succeeded",
                        last_sequence=1,
                        events=(),
                        outputs=(),
                        error=None,
                    )

            self.service.relay_factory = (
                lambda _worker, _session: PendingRelay()
            )
            running = await self.service.submit_job(
                "session-1",
                capture_id=self.first_capture.capture_id,
                idempotency_key="background-key",
            )

            self.assertEqual(running.state, JobState.RUNNING)
            self.assertEqual(
                self.sessions.get("session-1").state,
                SessionState.RUNNING,
            )
            released.set()
            for _ in range(20):
                if (
                    self.jobs.get_job(running.job_id).state
                    == JobState.SUCCEEDED
                ):
                    break
                await asyncio.sleep(0)
            return running.job_id

        job_id = asyncio.run(scenario())

        self.assertEqual(
            self.jobs.get_job(job_id).state,
            JobState.SUCCEEDED,
        )
        self.assertEqual(
            self.sessions.get("session-1").state,
            SessionState.READY,
        )

    def test_deadline_alerts_extensions_and_no_limit_acknowledgement(self):
        self.assertEqual(
            self.service.alerts(
                self.sessions.get("session-1"),
                now=6_400.0,
            ),
            ["15_minutes"],
        )
        self.assertEqual(
            self.service.alerts(
                self.sessions.get("session-1"),
                now=7_000.0,
            ),
            ["5_minutes"],
        )

        extended = asyncio.run(
            self.service.update_deadline(
                "session-1",
                {"action": "add_30_minutes"},
            )
        )

        self.assertEqual(extended.deadline_at, 9_100.0)
        self.assertEqual(
            self.worker.deadline_calls[-1],
            {
                "mode": "finite",
                "deadline_at": 9_100.0,
                "retrieval_grace_seconds": 300,
            },
        )
        with self.assertRaises(DeadlineValidationError):
            asyncio.run(
                self.service.update_deadline(
                    "session-1",
                    {
                        "action": "disable",
                        "acknowledged": False,
                    },
                )
            )
        unlimited = asyncio.run(
            self.service.update_deadline(
                "session-1",
                {
                    "action": "disable",
                    "acknowledged": True,
                },
            )
        )
        self.assertEqual(unlimited.deadline_mode, "none")
        self.assertIsNone(unlimited.deadline_at)

    def test_failed_worker_deadline_sync_keeps_earlier_finite_boundary(self):
        async def unavailable(_payload):
            raise RuntimeError("private transport detail")

        self.worker.update_deadline = unavailable

        with self.assertRaises(DeadlineSynchronizationError):
            asyncio.run(
                self.service.update_deadline(
                    "session-1",
                    {"action": "add_30_minutes"},
                )
            )

        session = self.sessions.get("session-1")
        self.assertEqual(session.deadline_mode, "finite")
        self.assertEqual(session.deadline_at, 7_300.0)
        self.assertEqual(session.pending_deadline_at, 9_100.0)
        self.assertEqual(
            session.pending_deadline_action,
            "add_30_minutes",
        )
        self.assertNotIn("private transport detail", session.sanitized_error)

    def test_deadline_update_preserves_a_concurrent_running_state(self):
        async def scenario():
            entered_update = asyncio.Event()
            release_update = asyncio.Event()

            async def blocked_update(payload):
                entered_update.set()
                await release_update.wait()
                return {
                    "mode": payload["mode"],
                    "deadline_at": payload.get("deadline_at"),
                }

            self.worker.update_deadline = blocked_update
            update = asyncio.create_task(
                self.service.update_deadline(
                    "session-1",
                    {"action": "add_30_minutes"},
                )
            )
            await entered_update.wait()
            self.sessions.transition_if_state(
                "session-1",
                SessionState.READY,
                SessionState.RUNNING,
                now=101.0,
            )
            release_update.set()
            return await update

        updated = asyncio.run(scenario())

        self.assertEqual(updated.state, SessionState.RUNNING)
        self.assertEqual(updated.deadline_at, 9_100.0)
        self.assertIsNone(updated.pending_deadline_mode)

    def test_deadline_update_is_rejected_before_a_worker_can_synchronize_it(self):
        creating = CloudSession.new(
            "creating-key",
            session_id="creating-session",
            manifest_digest="a" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.CREATING,
        )
        self.sessions.create_or_get(creating)

        with self.assertRaises(DeadlineValidationError):
            asyncio.run(
                self.service.update_deadline(
                    creating.session_id,
                    {"action": "add_30_minutes"},
                )
            )

        unchanged = self.sessions.get(creating.session_id)
        self.assertIsNone(unchanged.pending_deadline_mode)
        self.assertEqual(self.worker.deadline_calls, [])

    def test_default_destroy_review_token_is_always_identifier_safe(self):
        with mock.patch(
            "cloud_run.session_service.secrets.token_urlsafe",
            return_value="_" + "a" * 42,
        ):
            with mock.patch(
                "cloud_run.session_service.secrets.token_hex",
                return_value="b" * 64,
            ):
                try:
                    review = asyncio.run(
                        self.service.review_destroy("session-1")
                    )
                except DestroyConfirmationError:
                    self.fail(
                        "Default review-token generation must always "
                        "satisfy its identifier contract."
                    )

        self.assertEqual(review.token, "b" * 64)

    def test_destroy_requires_fresh_review_and_abandons_unverified_outputs(self):
        job = CloudJob(
            job_id="destroy-job",
            session_id="session-1",
            idempotency_key="destroy-job-key",
            state=JobState.SUCCEEDED,
            prompt_digest=self.first_capture.prompt_digest,
            capture_json=self.first_capture.canonical_payload(),
            manifest_digest=self.sessions.get(
                "session-1"
            ).manifest_digest,
            remote_prompt_id=None,
            sanitized_error=None,
            created_at=100.0,
            updated_at=100.0,
            version=1,
        )
        self.jobs.create_job(job)
        self.jobs.save_transfer(
            job_id=job.job_id,
            artifact_id="output-2",
            direction="download",
            expected_size=20,
            sha256="f" * 64,
            offset=10,
            state="transferring",
            private_path=str(self.path.parent / "output-2.part"),
        )

        class DestroyLifecycle:
            def __init__(inner_self):
                inner_self.calls = []

            async def destroy_session(inner_self, session_id):
                inner_self.calls.append(session_id)
                current = self.sessions.get(session_id)
                current = self.sessions.transition(
                    session_id,
                    SessionState.DESTROY_REQUESTED,
                    now=102.0,
                    destroy_requested=True,
                )
                current = self.sessions.transition(
                    session_id,
                    SessionState.DESTROYING,
                    now=103.0,
                )
                return self.sessions.transition(
                    session_id,
                    SessionState.DESTROYED,
                    now=104.0,
                    instance_id=None,
                    worker_base_url=None,
                    provider_token=None,
                    session_secret_hex=None,
                    residual_inventory=(),
                    sanitized_error=None,
                )

        lifecycle = DestroyLifecycle()
        self.service.lifecycle = lifecycle
        review = asyncio.run(
            self.service.review_destroy("session-1")
        )

        self.assertEqual(review.instance_id, "77")
        self.assertEqual(
            review.unverified_artifact_ids,
            ("output-2",),
        )
        with self.assertRaises(DestroyConfirmationError):
            asyncio.run(
                self.service.destroy(
                    "session-1",
                    {
                        "review_token": review.token,
                        "acknowledge_data_loss": False,
                    },
                )
            )
        destroyed = asyncio.run(
            self.service.destroy(
                "session-1",
                {
                    "review_token": review.token,
                    "acknowledge_data_loss": True,
                },
            )
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertEqual(lifecycle.calls, ["session-1"])
        self.assertEqual(
            self.jobs.get_transfer(
                "destroy-job",
                "output-2",
            ).state.value,
            "abandoned",
        )
        with self.assertRaises(DestroyConfirmationError):
            asyncio.run(
                self.service.destroy(
                    "session-1",
                    {
                        "review_token": review.token,
                        "acknowledge_data_loss": True,
                    },
                )
            )

    def test_duplicate_key_is_idempotent_and_busy_session_rejects_new_job(self):
        first = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.first_capture.capture_id,
                idempotency_key="same-key",
            )
        )
        duplicate = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.second_capture.capture_id,
                idempotency_key="same-key",
            )
        )
        current = self.sessions.get("session-1")
        self.sessions.save(
            current.transition(SessionState.RUNNING, now=101.0)
        )

        with self.assertRaises(SessionBusy):
            asyncio.run(
                self.service.submit_job(
                    "session-1",
                    capture_id=self.second_capture.capture_id,
                    idempotency_key="busy-key",
                )
            )

        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertEqual(len(self.worker.job_calls), 1)

    def test_job_submission_rejects_non_string_identifiers_before_resolution(self):
        calls = len(self.service.resolver.calls)

        with self.assertRaisesRegex(SessionServiceError, "idempotency"):
            asyncio.run(
                self.service.submit_job(
                    "session-1",
                    capture_id=self.first_capture.capture_id,
                    idempotency_key=7,
                )
            )

        self.assertEqual(len(self.service.resolver.calls), calls)

    def test_compatible_delta_resumes_required_local_upload_before_job(self):
        content = b"new-private-input"
        path = self.path.parent / "new-input.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        digest = __import__("hashlib").sha256(content).hexdigest()
        artifact = ArtifactSpec(
            artifact_id="new-input",
            kind="input",
            logical_name="new-input.jpg",
            destination="input/new-input.jpg",
            size_bytes=len(content),
            sha256=digest,
            source=SourceSpec(
                "local-upload",
                "local-upload:new-input",
            ),
        )
        self.jobs.register_local_artifact(
            types.SimpleNamespace(
                artifact_id="new-input",
                private_path=str(path),
                size_bytes=len(content),
                sha256=digest,
            )
        )
        self.service.resolver.resolutions[
            self.second_capture.capture_id
        ] = artifact_resolution(self.model, self.input_a, artifact)
        apply_count = 0

        async def apply_manifest(payload):
            nonlocal apply_count
            apply_count += 1
            self.worker.manifest_calls.append(payload)
            response = {
                "transaction_id": (
                    "provision-" + payload["manifest_digest"]
                ),
                "manifest_digest": payload["manifest_digest"],
                "state": (
                    "awaiting_upload" if apply_count == 1 else "ready"
                ),
                "planned_restarts": 0,
                "repair_restarts": 0,
                "missing_class_types": [],
                "missing_artifacts": [],
            }
            if apply_count == 1:
                response["required_uploads"] = ["new-input"]
            return response

        async def upload_artifact(
            artifact_id,
            *,
            path,
            size_bytes,
            sha256,
            start,
            on_progress,
        ):
            self.assertEqual(artifact_id, "new-input")
            self.assertEqual(start, 0)
            self.assertEqual(Path(path).read_bytes(), content)
            await on_progress(size_bytes)
            return {
                "artifact_id": artifact_id,
                "state": "verified",
                "next_offset": size_bytes,
                "size_bytes": size_bytes,
                "sha256": sha256,
            }

        self.worker.apply_manifest = apply_manifest
        self.worker.upload_artifact = upload_artifact

        job = asyncio.run(
            self.service.submit_job(
                "session-1",
                capture_id=self.second_capture.capture_id,
                idempotency_key="upload-key",
            )
        )

        transfer = self.jobs.get_transfer(job.job_id, "new-input")
        self.assertEqual(job.state, JobState.SUCCEEDED)
        self.assertEqual(transfer.state.value, "verified")
        self.assertEqual(transfer.offset, len(content))
        self.assertEqual(apply_count, 2)


if __name__ == "__main__":
    unittest.main()

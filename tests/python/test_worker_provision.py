import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cloud_run.manifest import (
    MANIFEST_SCHEMA_VERSION,
    PINNED_COMFYUI_CORE_VERSION,
    PINNED_COMFYUI_FRONTEND_VERSION,
    PROTOCOL_VERSION,
    ArtifactSpec,
    CustomNodeSpec,
    DependencyManifest,
    ProfileFileSpec,
    ProfileSpec,
    PythonWheelSpec,
    SourceSpec,
    UiPackageSpec,
)
from cloud_run.worker_protocol import sign_request
from remote_worker.state import WorkerStateStore


WORKER_VERSION = "worker-v1"


def source(artifact_id):
    return SourceSpec(
        kind="local-upload",
        locator=f"local-upload:{artifact_id}",
    )


def artifact(
    artifact_id,
    *,
    kind="model",
    destination=None,
    payload=None,
):
    payload = payload or artifact_id.encode("utf-8")
    destination = destination or (
        f"models/checkpoints/{artifact_id}.bin"
    )
    return ArtifactSpec(
        artifact_id=artifact_id,
        kind=kind,
        logical_name=artifact_id,
        destination=destination,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        source=source(artifact_id),
    )


def public_artifact(
    artifact_id,
    *,
    kind="model",
    destination=None,
    payload=None,
):
    revision = "b" * 40
    return replace(
        artifact(
            artifact_id,
            kind=kind,
            destination=destination,
            payload=payload,
        ),
        source=SourceSpec(
            kind="huggingface",
            locator=(
                "https://huggingface.co/example/private-repository-marker/"
                f"resolve/{revision}/{artifact_id}.bin"
            ),
            immutable_revision=revision,
        ),
    )


def custom_node():
    archive = artifact(
        "fancy-archive",
        kind="custom_node_archive",
        destination="custom_nodes/fancy",
        payload=b"archive",
    )
    wheel_payload = b"wheel"
    wheel = PythonWheelSpec(
        filename="dep-1.0-py3-none-any.whl",
        size_bytes=len(wheel_payload),
        sha256=hashlib.sha256(wheel_payload).hexdigest(),
        source=source("wheel-dep"),
    )
    return CustomNodeSpec(
        package_id="fancy",
        repository_url="https://github.com/example/fancy",
        revision="a" * 40,
        archive=archive,
        wheels=(wheel,),
        provided_class_types=("Fancy",),
    )


def manifest(
    *,
    prompt_marker="1",
    custom_nodes=(),
    artifacts=(),
    ui_packages=(),
    profile=None,
    worker_version=WORKER_VERSION,
):
    return DependencyManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        protocol_version=PROTOCOL_VERSION,
        comfyui_core_version=PINNED_COMFYUI_CORE_VERSION,
        comfyui_frontend_version=PINNED_COMFYUI_FRONTEND_VERSION,
        worker_version=worker_version,
        prompt_digest=prompt_marker * 64,
        custom_nodes=tuple(custom_nodes),
        artifacts=tuple(artifacts),
        ui_packages=tuple(ui_packages),
        profile=profile,
        minimum_vram_gb=12.0,
        output_allowance_bytes=1024,
        disk_gb=80,
    )


class FakeDisk:
    def __init__(self, events=None, error=None):
        self.events = events if events is not None else []
        self.error = error
        self.calls = []

    def reserve(self, desired, delta):
        self.events.append("disk")
        self.calls.append((desired.digest, delta))
        if self.error is not None:
            raise self.error


class FakeArtifacts:
    def __init__(
        self,
        *,
        events=None,
        missing_sequences=None,
        clock=None,
        advance_seconds=0,
        uploads_required=(),
    ):
        self.events = events if events is not None else []
        self.ensure_calls = []
        self.repair_calls = []
        self.missing_sequences = list(missing_sequences or [()])
        self.clock = clock
        self.advance_seconds = advance_seconds
        self.uploads_required = tuple(uploads_required)

    async def ensure_many(
        self,
        artifacts,
        *,
        source_urls,
        progress,
        force=False,
    ):
        from remote_worker.provision import UploadsRequired

        identifiers = tuple(item.artifact_id for item in artifacts)
        self.events.append("transfer")
        self.ensure_calls.append((identifiers, force))
        if self.advance_seconds:
            self.clock.advance(self.advance_seconds)
        if self.uploads_required:
            raise UploadsRequired(self.uploads_required)
        for item in artifacts:
            progress(
                (
                    "model_transfer"
                    if item.kind == "model"
                    else "dependency_transfer"
                ),
                item.artifact_id,
                item.size_bytes,
            )
        if force:
            self.repair_calls.append(identifiers)
        return identifiers

    def validate(self, artifacts):
        if len(self.missing_sequences) > 1:
            return tuple(self.missing_sequences.pop(0))
        return tuple(self.missing_sequences[0])


class FakeInstaller:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.nodes = []
        self.wheel_batches = []
        self.ui_packages = []

    async def install(self, node):
        self.events.append(f"install:{node.package_id}")
        self.nodes.append(node.package_id)

    async def install_wheels(self, wheels):
        names = tuple(item.filename for item in wheels)
        self.events.append("install-wheels")
        self.wheel_batches.append(names)

    async def install_ui_package(self, package):
        self.events.append(f"install-ui:{package.package_id}")
        self.ui_packages.append(package.package_id)


class FakeComfy:
    def __init__(self, object_info_sequence):
        self.object_info_sequence = list(object_info_sequence)
        self.ensure_calls = 0
        self.restarts = 0
        self.health_calls = 0

    async def ensure_running(self):
        self.ensure_calls += 1
        self.health_calls += 1
        return {"ready": True}

    async def restart(self):
        self.restarts += 1
        self.health_calls += 1
        return {"ready": True}

    async def object_info(self):
        if len(self.object_info_sequence) > 1:
            return self.object_info_sequence.pop(0)
        return self.object_info_sequence[0]


class FakeClock:
    def __init__(self, value=1000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def claimed_store(path):
    store = WorkerStateStore(path)
    store.claim(
        session_id="session-1",
        session_secret_hex="a" * 64,
    )
    return store


class ProvisionerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = claimed_store(
            self.root / "state" / "worker-state.json"
        )

    def provisioner(
        self,
        *,
        comfy,
        artifacts=None,
        installer=None,
        disk=None,
        clock=None,
    ):
        from remote_worker.provision import Provisioner

        return Provisioner(
            state_store=self.state,
            worker_version=WORKER_VERSION,
            comfy=comfy,
            artifacts=artifacts or FakeArtifacts(),
            installer=installer or FakeInstaller(),
            disk=disk or FakeDisk(),
            clock=clock or FakeClock(),
        )

    def test_initial_manifest_installs_validates_and_records_readiness(self):
        events = []
        node = custom_node()
        model = artifact("upscaler")
        input_media = artifact(
            "source-image",
            kind="input",
            destination="input/source.jpg",
        )
        desired = manifest(
            custom_nodes=(node,),
            artifacts=(model, input_media),
        )
        artifacts = FakeArtifacts(events=events)
        installer = FakeInstaller(events=events)
        comfy = FakeComfy(
            [{"KSampler": {}, "Fancy": {}}]
        )
        provisioner = self.provisioner(
            comfy=comfy,
            artifacts=artifacts,
            installer=installer,
            disk=FakeDisk(events=events),
        )

        result = asyncio.run(
            provisioner.apply_manifest(
                desired,
                required_class_types=("KSampler", "Fancy"),
                source_urls={
                    "upscaler": (
                        "https://huggingface.co/private?"
                        "token=temporary-source-marker"
                    )
                },
            )
        )

        self.assertEqual(result.state, "ready")
        self.assertEqual(result.planned_restarts, 1)
        self.assertEqual(result.repair_restarts, 0)
        self.assertEqual(result.missing_class_types, ())
        self.assertEqual(result.missing_artifacts, ())
        self.assertEqual(events[0], "disk")
        self.assertEqual(events[1], "transfer")
        self.assertEqual(
            set(artifacts.ensure_calls[0][0]),
            {
                "fancy-archive",
                "wheel-dep",
                "upscaler",
                "source-image",
            },
        )
        self.assertEqual(installer.nodes, ["fancy"])
        self.assertEqual(comfy.restarts, 1)
        persisted = self.state.load()
        self.assertNotIn("temporary-source-marker", repr(persisted))
        self.assertEqual(
            persisted["installed"]["manifest_digest"],
            desired.digest,
        )
        self.assertEqual(
            persisted["installed"]["readiness"][
                "validated_class_types"
            ],
            ["Fancy", "KSampler"],
        )
        self.assertEqual(
            persisted["installed"]["readiness"][
                "validated_artifacts"
            ],
            ["source-image", "upscaler"],
        )
        self.assertEqual(
            persisted["transactions"][result.transaction_id]["state"],
            "ready",
        )

    def test_ui_package_and_profile_are_transferred_without_graph_authority(self):
        panel_archive = artifact(
            "agent-panel-archive",
            kind="ui_package_archive",
            destination="custom_nodes/comfyui-agent-panel",
            payload=b"agent-panel",
        )
        panel = UiPackageSpec(
            package_id="comfyui-agent-panel",
            repository_url="https://github.com/example/comfyui-agent-panel",
            revision="a" * 40,
            archive=panel_archive,
            web_sha256="b" * 64,
            required_capabilities=(
                "graph_read",
                "graph_edit",
                "native_run",
                "native_batch",
            ),
        )
        profile_archive = artifact(
            "profile-archive",
            kind="profile_archive",
            destination="user/default/cloud-vast-profile",
            payload=b"profile",
        )
        safe_profile = ProfileSpec(
            profile_id="profile-1",
            revision=3,
            archive=profile_archive,
            bootstrap_digest="c" * 64,
            files=(
                ProfileFileSpec(
                    path="workflows/example.json",
                    size_bytes=12,
                    sha256="d" * 64,
                ),
            ),
        )
        desired = manifest(ui_packages=(panel,), profile=safe_profile)
        artifacts = FakeArtifacts()
        installer = FakeInstaller()
        provisioner = self.provisioner(
            comfy=FakeComfy([{"KSampler": {}}]),
            artifacts=artifacts,
            installer=installer,
        )

        result = asyncio.run(
            provisioner.apply_manifest(
                desired,
                required_class_types=("KSampler",),
            )
        )

        self.assertEqual(result.state, "ready")
        self.assertEqual(
            set(artifacts.ensure_calls[0][0]),
            {"agent-panel-archive", "profile-archive"},
        )
        self.assertEqual(installer.ui_packages, ["comfyui-agent-panel"])
        installed = self.state.load()["installed"]
        self.assertEqual(installed["profile_revision"], 3)
        self.assertEqual(
            installed["ui_package_digests"],
            {"comfyui-agent-panel": "b" * 64},
        )
        self.assertEqual(
            installed["required_class_types"],
            ["KSampler"],
        )

    def test_progress_is_bounded_monotonic_sanitized_and_reaches_ready(self):
        model = public_artifact(
            "progress-model",
            payload=b"progress-model-payload",
        )
        input_media = public_artifact(
            "progress-input",
            kind="input",
            destination="input/progress-input.bin",
            payload=b"progress-input-payload",
        )
        desired = manifest(artifacts=(model, input_media))
        history = []
        original_record = self.state.record_transaction

        def record_transaction(transaction_id, record, **kwargs):
            history.append(json.loads(json.dumps(record)))
            return original_record(transaction_id, record, **kwargs)

        self.state.record_transaction = record_transaction

        class ProgressArtifacts(FakeArtifacts):
            async def ensure_many(
                self,
                artifacts,
                *,
                source_urls,
                progress,
                force=False,
            ):
                del source_urls, force
                for item in artifacts:
                    phase = (
                        "model_transfer"
                        if item.kind == "model"
                        else "dependency_transfer"
                    )
                    progress(phase, item.artifact_id, item.size_bytes // 2)
                    progress(phase, item.artifact_id, item.size_bytes)
                    progress(
                        "digest_verification",
                        item.artifact_id,
                        item.size_bytes,
                    )
                    progress(phase, item.artifact_id, item.size_bytes)
                return tuple(item.artifact_id for item in artifacts)

        provisioner = self.provisioner(
            comfy=FakeComfy([{"KSampler": {}}]),
            artifacts=ProgressArtifacts(),
        )

        result = asyncio.run(
            provisioner.apply_manifest(
                desired,
                required_class_types=("KSampler",),
                source_urls={
                    model.artifact_id: (
                        "https://huggingface.co/example/"
                        "private-repository-marker/resolve/"
                        + "b" * 40
                        + "/progress-model.bin?"
                        "token=temporary-source-secret"
                    )
                },
            )
        )

        total_bytes = model.size_bytes + input_media.size_bytes
        self.assertEqual(
            result.progress,
            {
                "phase": "ready",
                "dependency_id": None,
                "transferred_bytes": total_bytes,
                "total_bytes": total_bytes,
            },
        )
        snapshots = [
            record.get("progress")
            for record in history
            if record.get("progress") is not None
        ]
        self.assertEqual(
            {snapshot["phase"] for snapshot in snapshots},
            {
                "dependency_transfer",
                "model_transfer",
                "digest_verification",
                "comfyui_startup",
                "environment_validation",
                "ready",
            },
        )
        self.assertEqual(
            [snapshot["transferred_bytes"] for snapshot in snapshots],
            sorted(snapshot["transferred_bytes"] for snapshot in snapshots),
        )
        self.assertTrue(
            all(
                set(snapshot)
                == {
                    "phase",
                    "dependency_id",
                    "transferred_bytes",
                    "total_bytes",
                }
                and snapshot["dependency_id"]
                in {None, model.artifact_id, input_media.artifact_id}
                and 0
                <= snapshot["transferred_bytes"]
                <= snapshot["total_bytes"]
                == total_bytes
                for snapshot in snapshots
            )
        )
        encoded = repr(history)
        for private_value in (
            "https://huggingface.co/",
            "private-repository-marker",
            "temporary-source-secret",
            "/Users/private/model.bin",
            "raw transport exception",
        ):
            self.assertNotIn(private_value, encoded)

    def test_progress_rejects_unknown_regressing_unbounded_or_invalid_events(self):
        from remote_worker.provision import ProvisionError, _ProgressTracker

        model = public_artifact(
            "bounded-model",
            payload=b"bounded-model-payload",
        )

        def tracker():
            return _ProgressTracker(
                FakeClock(),
                (model,),
                lambda _snapshot: None,
            )

        current = tracker()
        current.progress("model_transfer", model.artifact_id, 2)
        with self.assertRaises(ProvisionError):
            current.progress("model_transfer", model.artifact_id, 1)

        invalid_events = (
            ("model_transfer", "unknown-artifact", 1),
            ("model_transfer", model.artifact_id, model.size_bytes + 1),
            ("untrusted_phase", model.artifact_id, 1),
        )
        for phase, artifact_id, offset in invalid_events:
            with self.subTest(phase=phase, artifact_id=artifact_id):
                with self.assertRaises(ProvisionError):
                    tracker().progress(phase, artifact_id, offset)

    def test_one_approved_repair_gets_one_extra_restart_then_stops(self):
        from remote_worker.provision import ProvisionError

        node = custom_node()
        desired = manifest(custom_nodes=(node,))
        installer = FakeInstaller()
        comfy = FakeComfy(
            [
                {"KSampler": {}},
                {"KSampler": {}, "Fancy": {}},
            ]
        )
        provisioner = self.provisioner(
            comfy=comfy,
            installer=installer,
        )

        result = asyncio.run(
            provisioner.apply_manifest(
                desired,
                required_class_types=("KSampler", "Fancy"),
            )
        )

        self.assertEqual(result.state, "ready")
        self.assertEqual(result.planned_restarts, 1)
        self.assertEqual(result.repair_restarts, 1)
        self.assertEqual(installer.nodes, ["fancy", "fancy"])
        self.assertEqual(comfy.restarts, 2)
        with self.assertRaises(ProvisionError):
            asyncio.run(provisioner.repair(result.transaction_id))

    def test_retry_adopts_completed_planned_restart_without_second_restart(self):
        from remote_worker.provision import UnapprovedRepairError

        desired = manifest(custom_nodes=(custom_node(),))
        comfy = FakeComfy(
            [
                {"KSampler": {}, "Fancy": {}},
                {"KSampler": {}, "Fancy": {}, "Unknown": {}},
            ]
        )
        provisioner = self.provisioner(comfy=comfy)

        with self.assertRaises(UnapprovedRepairError):
            asyncio.run(
                provisioner.apply_manifest(
                    desired,
                    required_class_types=(
                        "KSampler",
                        "Fancy",
                        "Unknown",
                    ),
                )
            )
        result = asyncio.run(
            provisioner.apply_manifest(
                desired,
                required_class_types=(
                    "KSampler",
                    "Fancy",
                    "Unknown",
                ),
            )
        )

        self.assertEqual(result.state, "ready")
        self.assertEqual(result.planned_restarts, 1)
        self.assertEqual(comfy.restarts, 1)

    def test_unapproved_missing_item_never_repairs_or_restarts(self):
        from remote_worker.provision import UnapprovedRepairError

        comfy = FakeComfy([{"KSampler": {}}])
        installer = FakeInstaller()
        provisioner = self.provisioner(
            comfy=comfy,
            installer=installer,
        )

        with self.assertRaises(UnapprovedRepairError):
            asyncio.run(
                provisioner.apply_manifest(
                    manifest(),
                    required_class_types=("KSampler", "Unknown"),
                )
            )

        self.assertEqual(comfy.restarts, 0)
        self.assertEqual(installer.nodes, [])
        self.assertEqual(self.state.load()["installed"], {})

    def test_ten_minute_transfer_stall_persists_stalled_without_restart(self):
        from remote_worker.provision import ProvisionStalled

        clock = FakeClock()
        artifacts = FakeArtifacts(
            clock=clock,
            advance_seconds=601,
        )
        desired = manifest(artifacts=(artifact("large-model"),))
        comfy = FakeComfy([{"KSampler": {}}])
        provisioner = self.provisioner(
            comfy=comfy,
            artifacts=artifacts,
            clock=clock,
        )

        with self.assertRaises(ProvisionStalled):
            asyncio.run(
                provisioner.apply_manifest(
                    desired,
                    required_class_types=("KSampler",),
                )
            )

        transaction = next(
            record
            for record in self.state.load()["transactions"].values()
            if record.get("kind") == "provision"
        )
        self.assertEqual(transaction["state"], "stalled")
        self.assertEqual(comfy.restarts, 0)
        self.assertEqual(self.state.load()["installed"], {})

    def test_reopen_computes_artifact_only_delta_without_restart(self):
        first_model = artifact("model-a")
        baseline = manifest(
            prompt_marker="1",
            artifacts=(first_model,),
        )
        second_input = artifact(
            "input-b",
            kind="input",
            destination="input/b.png",
        )
        desired = manifest(
            prompt_marker="2",
            artifacts=(first_model, second_input),
        )
        artifacts = FakeArtifacts()
        comfy = FakeComfy(
            [{"KSampler": {}}, {"KSampler": {}}]
        )
        first = self.provisioner(
            comfy=comfy,
            artifacts=artifacts,
        )
        first_result = asyncio.run(
            first.apply_manifest(
                baseline,
                required_class_types=("KSampler",),
            )
        )
        self.assertEqual(first_result.planned_restarts, 0)

        reopened = self.provisioner(
            comfy=comfy,
            artifacts=artifacts,
        )
        second_result = asyncio.run(
            reopened.apply_manifest(
                desired,
                required_class_types=("KSampler",),
            )
        )

        self.assertEqual(second_result.planned_restarts, 0)
        self.assertEqual(comfy.restarts, 0)
        self.assertEqual(
            artifacts.ensure_calls,
            [
                (("model-a",), False),
                (("input-b",), False),
            ],
        )
        self.assertEqual(
            self.state.load()["installed"]["manifest_digest"],
            desired.digest,
        )

    def test_upload_wait_and_disk_or_identity_failure_precede_mutation(self):
        from remote_worker.provision import (
            DiskReservationError,
            UploadsRequired,
            WorkerIdentityError,
        )

        desired = manifest(artifacts=(artifact("private-model"),))
        waiting_artifacts = FakeArtifacts(
            uploads_required=("private-model",)
        )
        waiting = self.provisioner(
            comfy=FakeComfy([{"KSampler": {}}]),
            artifacts=waiting_artifacts,
        )
        with self.assertRaises(UploadsRequired):
            asyncio.run(
                waiting.apply_manifest(
                    desired,
                    required_class_types=("KSampler",),
                )
            )
        waiting_record = next(
            record
            for record in self.state.load()["transactions"].values()
            if record.get("kind") == "provision"
        )
        self.assertEqual(waiting_record["state"], "awaiting_upload")

        other_state = claimed_store(
            self.root / "other-state" / "worker.json"
        )
        disk_artifacts = FakeArtifacts()
        from remote_worker.provision import Provisioner

        disk_failure = Provisioner(
            state_store=other_state,
            worker_version=WORKER_VERSION,
            comfy=FakeComfy([{"KSampler": {}}]),
            artifacts=disk_artifacts,
            installer=FakeInstaller(),
            disk=FakeDisk(error=DiskReservationError("full")),
            clock=FakeClock(),
        )
        with self.assertRaises(DiskReservationError):
            asyncio.run(
                disk_failure.apply_manifest(
                    desired,
                    required_class_types=("KSampler",),
                )
            )
        self.assertEqual(disk_artifacts.ensure_calls, [])

        bad_identity = replace(
            desired,
            worker_version="other-worker",
        )
        with self.assertRaises(WorkerIdentityError):
            asyncio.run(
                disk_failure.apply_manifest(
                    bad_identity,
                    required_class_types=("KSampler",),
                )
            )
        self.assertEqual(disk_artifacts.ensure_calls, [])


class WorkerArtifactProviderProgressTests(unittest.TestCase):
    def test_two_artifacts_keep_identity_and_map_transfer_phases(self):
        from remote_worker.provision import WorkerArtifactProvider

        model = public_artifact(
            "provider-model",
            payload=b"provider-model-payload",
        )
        input_media = public_artifact(
            "provider-input",
            kind="input",
            destination="input/provider-input.bin",
            payload=b"provider-input-payload",
        )

        class ProgressManager:
            progress = None

            def verify(self, _artifact):
                return None

            def reset(self, _artifact):
                return None

            async def download_many(self, artifacts, **_kwargs):
                for item in artifacts:
                    self.progress(
                        item.artifact_id,
                        item.size_bytes // 2,
                        "transferring",
                    )
                    self.progress(
                        item.artifact_id,
                        item.size_bytes,
                        "verifying",
                    )
                    self.progress(
                        item.artifact_id,
                        item.size_bytes,
                        "verified",
                    )
                return tuple(item.artifact_id for item in artifacts)

        class UnusedClient:
            async def get(self, *_args, **_kwargs):
                raise AssertionError("fake provider transport was bypassed")

        events = []
        provider = WorkerArtifactProvider(ProgressManager(), UnusedClient())
        asyncio.run(
            provider.ensure_many(
                (model, input_media),
                source_urls={},
                progress=lambda phase, artifact_id, offset: events.append(
                    (phase, artifact_id, offset)
                ),
            )
        )

        self.assertEqual(
            events,
            [
                ("model_transfer", model.artifact_id, model.size_bytes // 2),
                (
                    "digest_verification",
                    model.artifact_id,
                    model.size_bytes,
                ),
                ("model_transfer", model.artifact_id, model.size_bytes),
                (
                    "dependency_transfer",
                    input_media.artifact_id,
                    input_media.size_bytes // 2,
                ),
                (
                    "digest_verification",
                    input_media.artifact_id,
                    input_media.size_bytes,
                ),
                (
                    "dependency_transfer",
                    input_media.artifact_id,
                    input_media.size_bytes,
                ),
            ],
        )
        self.assertNotIn("huggingface.co", repr(events))
        self.assertNotIn(model.logical_name + ".bin", repr(events))


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


class ManifestProtocolTests(unittest.TestCase):
    def test_canonical_manifest_roundtrip_is_exact_and_strict(self):
        from remote_worker.provision import (
            ProvisionError,
            dependency_manifest_from_record,
            parse_manifest_request,
        )

        public_model = replace(
            artifact("model-a"),
            source=SourceSpec(
                kind="huggingface",
                locator=(
                    "https://huggingface.co/example/model/resolve/"
                    + "b" * 40
                    + "/model.bin"
                ),
                immutable_revision="b" * 40,
            ),
        )
        desired = manifest(
            custom_nodes=(custom_node(),),
            artifacts=(public_model,),
        )
        record = json.loads(desired.canonical_bytes())
        restored = dependency_manifest_from_record(record)
        self.assertEqual(restored, desired)
        self.assertEqual(restored.digest, desired.digest)

        request = {
            "manifest": record,
            "manifest_digest": desired.digest,
            "required_class_types": ["KSampler", "Fancy"],
            "source_urls": {
                "model-a": (
                    "https://huggingface.co/example/model?"
                    "token=temporary"
                )
            },
        }
        parsed, required, urls = parse_manifest_request(
            json.dumps(
                request,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        self.assertEqual(parsed, desired)
        self.assertEqual(required, ("KSampler", "Fancy"))
        self.assertIn("model-a", urls)

        malformed = (
            b'{"manifest":{},"manifest":{},'
            b'"manifest_digest":"' + b"a" * 64
            + b'","required_class_types":[],"source_urls":{}}'
        )
        with self.assertRaises(ProvisionError):
            parse_manifest_request(malformed)

    def test_temporary_source_cannot_change_an_approved_origin(self):
        from remote_worker.provision import (
            ProvisionError,
            WorkerArtifactProvider,
        )

        public_model = replace(
            artifact("model-a"),
            source=SourceSpec(
                kind="huggingface",
                locator=(
                    "https://huggingface.co/example/model/resolve/"
                    + "b" * 40
                    + "/model.bin"
                ),
                immutable_revision="b" * 40,
            ),
        )

        class BoundaryManager:
            progress = None

            def verify(self, _artifact):
                return None

            async def download_many(self, *_args, **_kwargs):
                raise AssertionError("unapproved URL reached transport")

        class BoundaryClient:
            async def get(self, *_args, **_kwargs):
                raise AssertionError("unapproved URL reached transport")

        provider = WorkerArtifactProvider(
            BoundaryManager(),
            BoundaryClient(),
        )
        with self.assertRaises(ProvisionError):
            asyncio.run(
                provider.ensure_many(
                    (public_model,),
                    source_urls={
                        "model-a": "https://evil.example/model.bin"
                    },
                    progress=lambda *_args: None,
                )
            )


class RecordingProcessFactory:
    def __init__(self):
        self.calls = []
        self.process = FakeProcess()

    async def __call__(self, *argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return self.process


class FakeComfyHttp:
    def __init__(self):
        self.paths = []

    async def get_json(self, path):
        self.paths.append(path)
        if path == "/system_stats":
            return {
                "system": {
                    "comfyui_version": "0.29.0",
                    "required_frontend_version": "1.47.10",
                    "python_version": "3.12.7 (main)",
                    "comfy_package_versions": [
                        {
                            "name": "comfyui-frontend-package",
                            "installed": "1.47.10",
                            "required": "1.47.10",
                        }
                    ],
                },
                "devices": [{"type": "cuda"}],
            }
        if path == "/object_info":
            return {"KSampler": {}, "Fancy": {}}
        raise AssertionError(path)


async def no_sleep(_seconds):
    return None


def comfy_system_stats(python_version):
    return {
        "system": {
            "comfyui_version": "0.29.0",
            "required_frontend_version": "1.47.10",
            "python_version": python_version,
            "comfy_package_versions": [
                {
                    "name": "comfyui-frontend-package",
                    "installed": "1.47.10",
                    "required": "1.47.10",
                }
            ],
        },
        "devices": [{"type": "cuda"}],
    }


class ComfyProcessTests(unittest.TestCase):
    def test_remote_health_accepts_qualified_python_312_report(self):
        from remote_worker.comfy import _validated_system_stats

        stats = comfy_system_stats("3.12.7 (main)")

        self.assertIs(_validated_system_stats(stats), stats)

    def test_remote_health_rejects_wrong_or_unqualified_python_series(self):
        from remote_worker.comfy import (
            ComfyIdentityError,
            _validated_system_stats,
        )

        for version in ("3.11.9 (main)", "3.13.12 (main)", "3.12"):
            with self.subTest(version=version):
                with self.assertRaises(ComfyIdentityError):
                    _validated_system_stats(comfy_system_stats(version))

    def test_fixed_loopback_argv_private_workdir_and_sanitized_environment(self):
        from remote_worker.comfy import ComfyProcess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            comfy_root.mkdir()
            (comfy_root / "main.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            working_root = root / "worker"
            factory = RecordingProcessFactory()
            http = FakeComfyHttp()
            comfy = ComfyProcess(
                comfy_root=comfy_root,
                working_root=working_root,
                python_executable=sys.executable,
                process_factory=factory,
                http=http,
                sleeper=no_sleep,
                clock=FakeClock(),
            )

            with patch.dict(
                os.environ,
                {
                    "PATH": "/runtime/bin",
                    "CUDA_VISIBLE_DEVICES": "0",
                    "CONTAINER_API_KEY": "must-not-leak",
                    "CLOUD_RUN_SESSION_SECRET": "must-not-leak",
                },
                clear=True,
            ):
                asyncio.run(comfy.start())
                object_info = asyncio.run(comfy.object_info())
                asyncio.run(comfy.stop())

            argv, options = factory.calls[0]
            self.assertEqual(
                argv,
                [
                    str(Path(sys.executable).resolve()),
                    str((comfy_root / "main.py").resolve()),
                    "--listen",
                    "127.0.0.1",
                    "--port",
                    "8188",
                    "--disable-auto-launch",
                ],
            )
            self.assertEqual(
                Path(options["cwd"]),
                working_root.resolve(),
            )
            self.assertEqual(options["env"]["CUDA_VISIBLE_DEVICES"], "0")
            self.assertEqual(options["env"]["PATH"], "/runtime/bin")
            self.assertNotIn("CONTAINER_API_KEY", options["env"])
            self.assertNotIn("CLOUD_RUN_SESSION_SECRET", options["env"])
            self.assertEqual(
                object_info,
                {"KSampler": {}, "Fancy": {}},
            )
            self.assertEqual(
                http.paths,
                ["/system_stats", "/object_info"],
            )
            self.assertTrue(factory.process.terminated)
            self.assertEqual(
                oct(working_root.stat().st_mode & 0o777),
                "0o700",
            )

    def test_wrong_core_or_gpu_identity_stops_the_internal_process(self):
        from remote_worker.comfy import ComfyProcess, ComfyProcessError

        class WrongIdentityHttp:
            async def get_json(self, _path):
                return {
                    "system": {
                        "comfyui_version": "0.30.0",
                        "required_frontend_version": "1.47.10",
                        "python_version": "3.12.7 (main)",
                        "comfy_package_versions": [],
                    },
                    "devices": [{"type": "cpu"}],
                }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            comfy_root.mkdir()
            (comfy_root / "main.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            clock = FakeClock(0)

            async def advance(_seconds):
                clock.advance(2)

            factory = RecordingProcessFactory()
            comfy = ComfyProcess(
                comfy_root=comfy_root,
                working_root=root / "worker",
                python_executable=sys.executable,
                process_factory=factory,
                http=WrongIdentityHttp(),
                sleeper=advance,
                clock=clock,
                startup_timeout=1,
            )

            with self.assertRaises(ComfyProcessError):
                asyncio.run(comfy.start())

            self.assertTrue(factory.process.terminated)

    def test_runtime_builder_wires_real_provisioning_components_offline(self):
        from remote_worker.jobs import JobManager
        from remote_worker.main import build_worker_runtime

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            (comfy_root / "custom_nodes").mkdir(parents=True)
            (comfy_root / "main.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            data_root = root / "worker-data"
            state_path = data_root / "worker-state.json"

            class OfflineTransport:
                async def get(self, *_args, **_kwargs):
                    raise AssertionError("network must remain unused")

            transport = OfflineTransport()

            worker = build_worker_runtime(
                state_path=state_path,
                expected_session_id="session-1",
                comfy_root=comfy_root,
                data_root=data_root,
                worker_version="a" * 40,
                python_executable=sys.executable,
                transfer_client=transport,
            )

            self.assertEqual(
                worker.transfer_manager.root,
                comfy_root.resolve(),
            )
            self.assertEqual(
                worker.transfer_manager.artifact_root,
                (data_root / "artifacts").resolve(),
            )
            self.assertEqual(
                worker.provisioner.worker_version,
                "a" * 40,
            )
            self.assertIs(
                worker.provisioner.artifacts.client,
                transport,
            )
            self.assertEqual(
                worker.state.path,
                state_path.resolve(),
            )
            self.assertIsInstance(worker.job_manager, JobManager)
            self.assertEqual(
                worker.job_manager.preview_root,
                (data_root / "previews").resolve(),
            )
            self.assertIs(
                worker.job_manager.comfy,
                worker.provisioner.comfy,
            )


class FakeWorkerRequest:
    def __init__(self, method, path, body, envelope):
        self.method = method
        self.path = path
        self.body = body
        self.auth_envelope = envelope
        self.boundary_authenticated = True
        self.headers = {}


class FakeRouteProvisioner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def apply_manifest(
        self,
        desired,
        *,
        required_class_types,
        source_urls,
    ):
        self.calls.append(
            (desired, tuple(required_class_types), dict(source_urls))
        )
        return self.result

    def transaction(self, transaction_id):
        if transaction_id != self.result.transaction_id:
            return None
        return self.result


class ProvisionResultProgressTests(unittest.TestCase):
    def test_progress_is_optional_exact_and_sanitized(self):
        from remote_worker.provision import ProvisionError, ProvisionResult

        base = {
            "transaction_id": "provision-1",
            "manifest_digest": "a" * 64,
            "state": "applying",
            "planned_restarts": 0,
            "repair_restarts": 0,
            "missing_class_types": (),
            "missing_artifacts": (),
        }
        old_shape = ProvisionResult(**base).payload()
        self.assertNotIn("progress", old_shape)

        progress = {
            "phase": "model_transfer",
            "dependency_id": "model-" + "b" * 64,
            "transferred_bytes": 1_048_576,
            "total_bytes": 8_388_608,
        }
        extended = ProvisionResult(**base, progress=progress)
        self.assertEqual(extended.payload()["progress"], progress)

        invalid_progress = (
            {**progress, "phase": "unknown"},
            {**progress, "phase": []},
            {**progress, "dependency_id": "../model.bin"},
            {**progress, "transferred_bytes": -1},
            {**progress, "transferred_bytes": progress["total_bytes"] + 1},
            {**progress, "source_url": "https://example.com/private"},
        )
        for value in invalid_progress:
            with self.subTest(value=value):
                with self.assertRaises(ProvisionError):
                    ProvisionResult(**base, progress=value)

    def test_server_rebuilds_the_allowlisted_result_payload(self):
        from remote_worker.provision import ProvisionResult
        from remote_worker.server import _provision_result_payload

        class LeakyProvisionResult(ProvisionResult):
            def payload(self):
                return {
                    **super().payload(),
                    "source_url": "https://huggingface.co/private?token=secret",
                    "raw_exception": "transport stack trace",
                }

        result = LeakyProvisionResult(
            transaction_id="provision-1",
            manifest_digest="a" * 64,
            state="ready",
            planned_restarts=0,
            repair_restarts=0,
            missing_class_types=(),
            missing_artifacts=(),
            progress={
                "phase": "ready",
                "dependency_id": None,
                "transferred_bytes": 10,
                "total_bytes": 10,
            },
        )

        payload = _provision_result_payload(result)
        self.assertEqual(
            set(payload),
            {
                "transaction_id",
                "manifest_digest",
                "state",
                "planned_restarts",
                "repair_restarts",
                "missing_class_types",
                "missing_artifacts",
                "progress",
            },
        )
        self.assertNotIn("source_url", repr(payload))
        self.assertNotIn("raw_exception", repr(payload))


class WorkerProvisionRouteTests(unittest.TestCase):
    def test_signed_manifest_route_parses_exact_record_and_reads_transaction(self):
        from remote_worker.provision import ProvisionResult
        from remote_worker.server import WorkerApplication

        desired = manifest()
        result = ProvisionResult(
            transaction_id="provision-1",
            manifest_digest=desired.digest,
            state="ready",
            planned_restarts=0,
            repair_restarts=0,
            missing_class_types=(),
            missing_artifacts=(),
        )
        provisioner = FakeRouteProvisioner(result)
        with tempfile.TemporaryDirectory() as directory:
            worker = WorkerApplication(
                state_path=Path(directory) / "state" / "worker.json",
                clock=lambda: 1000,
                provisioner=provisioner,
            )
            claim_body = json.dumps(
                {
                    "protocol_version": "2",
                    "session_id": "session-1",
                    "session_secret_hex": "a" * 64,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            claim = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "POST",
                        "/worker/v1/claim",
                        claim_body,
                        None,
                    )
                )
            )
            self.assertEqual(claim.status, 200)

            body = json.dumps(
                {
                    "manifest": json.loads(
                        desired.canonical_bytes()
                    ),
                    "manifest_digest": desired.digest,
                    "required_class_types": ["KSampler"],
                    "source_urls": {},
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            path = "/worker/v1/manifests"
            created = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "POST",
                        path,
                        body,
                        sign_request(
                            bytes.fromhex("a" * 64),
                            "POST",
                            path,
                            body,
                            timestamp=1000,
                            nonce="manifest-1",
                        ),
                    )
                )
            )
            transaction_path = (
                "/worker/v1/transactions/provision-1"
            )
            transaction = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "GET",
                        transaction_path,
                        b"",
                        sign_request(
                            bytes.fromhex("a" * 64),
                            "GET",
                            transaction_path,
                            b"",
                            timestamp=1000,
                            nonce="transaction-1",
                        ),
                    )
                )
            )

            expected_payload = {
                "transaction_id": "provision-1",
                "manifest_digest": desired.digest,
                "state": "ready",
                "planned_restarts": 0,
                "repair_restarts": 0,
                "missing_class_types": [],
                "missing_artifacts": [],
            }
            self.assertEqual(created.status, 200)
            self.assertEqual(created.payload, expected_payload)
            self.assertEqual(transaction.payload, expected_payload)
            self.assertEqual(
                provisioner.calls[0][1],
                ("KSampler",),
            )

    def test_manifest_registers_local_uploads_before_returning_wait_state(self):
        from remote_worker.provision import (
            ProvisionResult,
            UploadsRequired,
        )
        from remote_worker.server import WorkerApplication
        from remote_worker.transfers import TransferManager

        private_model = artifact("private-model")
        desired = manifest(artifacts=(private_model,))
        waiting_result = ProvisionResult(
            transaction_id="provision-" + desired.digest,
            manifest_digest=desired.digest,
            state="awaiting_upload",
            planned_restarts=0,
            repair_restarts=0,
            missing_class_types=(),
            missing_artifacts=("private-model",),
        )

        class WaitingProvisioner:
            async def apply_manifest(self, *_args, **_kwargs):
                raise UploadsRequired(("private-model",))

            def transaction(self, transaction_id):
                if transaction_id == waiting_result.transaction_id:
                    return waiting_result
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transfer_root = root / "comfy"
            transfer_root.mkdir()
            manager = TransferManager(root=transfer_root)
            worker = WorkerApplication(
                state_path=root / "state" / "worker.json",
                clock=lambda: 1000,
                transfer_manager=manager,
                provisioner=WaitingProvisioner(),
            )
            claim_body = json.dumps(
                {
                    "protocol_version": "2",
                    "session_id": "session-1",
                    "session_secret_hex": "a" * 64,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "POST",
                        "/worker/v1/claim",
                        claim_body,
                        None,
                    )
                )
            )
            body = json.dumps(
                {
                    "manifest": json.loads(
                        desired.canonical_bytes()
                    ),
                    "manifest_digest": desired.digest,
                    "required_class_types": ["KSampler"],
                    "source_urls": {},
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            path = "/worker/v1/manifests"

            response = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "POST",
                        path,
                        body,
                        sign_request(
                            bytes.fromhex("a" * 64),
                            "POST",
                            path,
                            body,
                            timestamp=1000,
                            nonce="manifest-wait-1",
                        ),
                    )
                )
            )

            self.assertEqual(response.status, 202)
            self.assertEqual(
                response.payload["required_uploads"],
                ["private-model"],
            )
            self.assertEqual(
                tuple(worker.upload_artifacts),
                ("private-model",),
            )


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import types
import unittest
from pathlib import Path

from cloud_run.artifacts import ArtifactResolution
from cloud_run.capture import CompiledCapture
from cloud_run.job_repository import JobRepository
from cloud_run.manifest import ArtifactSpec, SourceSpec
from cloud_run.resolver import NodeResolution
from cloud_run.session_service import PreflightBlocked, SessionService
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


if __name__ == "__main__":
    unittest.main()

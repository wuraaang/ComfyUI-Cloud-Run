import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from cloud_run.job_repository import JobRepository
from cloud_run.models import CloudJob, JobState, TransferState


EXPECTED_OUTPUT = b"wallpaper-output"


def cloud_job(job_id="job-1", session_id="session-1"):
    return CloudJob(
        job_id=job_id,
        session_id=session_id,
        idempotency_key="key-" + job_id,
        state=JobState.RUNNING,
        prompt_digest="c" * 64,
        capture_json=json.dumps(
            {
                "workflow": {
                    "nodes": [],
                    "extra": {"frontendVersion": "1.47.10"},
                },
                "output": {
                    "1": {
                        "class_type": "SaveImage",
                        "inputs": {},
                    }
                },
                "queue_options": {},
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        manifest_digest="a" * 64,
        remote_prompt_id=None,
        sanitized_error=None,
        created_at=10.0,
        updated_at=10.0,
        version=1,
    )


def descriptor(
    expected=EXPECTED_OUTPUT,
    *,
    artifact_id="output-1",
):
    return {
        "artifact_id": artifact_id,
        "node_id": "7",
        "filename": "wallpaper.png",
        "subfolder": "",
        "mime_type": "image/png",
        "size_bytes": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
    }


class FakeWorker:
    def __init__(
        self,
        *,
        output=EXPECTED_OUTPUT,
        descriptors=None,
        events=None,
        previews=None,
    ):
        self.output = output
        self.descriptors = (
            list(descriptors)
            if descriptors is not None
            else [descriptor()]
        )
        self.remote_events = list(events or [])
        self.previews = dict(previews or {})
        self.ranges = []
        self.snapshot_calls = []
        self.events_calls = 0
        self.job_calls = 0

    async def snapshot(self, job_id, after_sequence):
        self.snapshot_calls.append((job_id, after_sequence))
        last_sequence = (
            self.remote_events[-1]["sequence"]
            if self.remote_events
            else 0
        )
        return {
            "job_id": job_id,
            "state": "succeeded",
            "prompt_id": "11111111-1111-4111-8111-111111111111",
            "events": [
                event
                for event in self.remote_events
                if event["sequence"] > after_sequence
            ],
            "last_sequence": last_sequence,
            "outputs": self.descriptors,
            "error": None,
            "created_at": 10.0,
            "updated_at": 20.0,
        }

    async def events(self, job_id, after_sequence):
        self.events_calls += 1
        return {
            "job_id": job_id,
            "events": [
                event
                for event in self.remote_events
                if event["sequence"] > after_sequence
            ],
            "last_sequence": (
                self.remote_events[-1]["sequence"]
                if self.remote_events
                else after_sequence
            ),
        }

    async def job(self, job_id):
        self.job_calls += 1
        return {
            "job_id": job_id,
            "state": "succeeded",
            "prompt_id": "11111111-1111-4111-8111-111111111111",
            "last_sequence": (
                self.remote_events[-1]["sequence"]
                if self.remote_events
                else 0
            ),
            "outputs": self.descriptors,
            "error": None,
        }

    async def download_artifact(
        self,
        artifact_id,
        *,
        start,
        on_chunk,
    ):
        from cloud_run.worker_client import ArtifactDownload

        self.ranges.append("bytes=" + str(start) + "-")
        remainder = self.output[start:]
        if remainder:
            result = on_chunk(remainder)
            if asyncio.iscoroutine(result):
                await result
        return ArtifactDownload(
            artifact_id=artifact_id,
            start=start,
            total_size=len(self.output),
            sha256=hashlib.sha256(self.output).hexdigest(),
            mime_type="image/png",
        )

    async def preview(self, job_id, preview_id):
        return self.previews.get(preview_id)


class LocalRelayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.private_root = self.root / "private"
        self.output_root = self.root / "output"
        self.output_root.mkdir()
        self.output_root = self.output_root.resolve()
        self.repository = JobRepository(
            self.private_root / "sessions.sqlite3"
        )
        self.repository.create_job(cloud_job())

    def relay(self, worker):
        from cloud_run.relay import LocalRelay

        return LocalRelay(
            worker=worker,
            repository=self.repository,
            private_root=self.private_root / "relay",
            output_root=self.output_root,
            clock=lambda: 20.0,
        )

    def test_relay_resumes_verifies_and_atomically_publishes_output(self):
        relay = self.relay(FakeWorker())
        part = relay.private_part_path("job-1", "output-1")
        part.parent.mkdir(mode=0o700, parents=True)
        part.write_bytes(EXPECTED_OUTPUT[:5])

        result = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
                output_root=self.output_root,
            )
        )

        self.assertEqual(relay.worker.ranges, ["bytes=5-"])
        self.assertEqual(result.state, TransferState.VERIFIED)
        self.assertEqual(result.local_path.read_bytes(), EXPECTED_OUTPUT)
        self.assertFalse(result.local_path.name.endswith(".part"))
        self.assertEqual(
            result.local_path,
            self.output_root.resolve()
            / "cloud-vast"
            / "session-1"
            / "job-1"
            / "wallpaper.png",
        )
        self.assertEqual(
            os.stat(result.local_path).st_mode & 0o777,
            0o600,
        )
        for directory in (
            result.local_path.parent,
            result.local_path.parent.parent,
            result.local_path.parent.parent.parent,
        ):
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        transfer = self.repository.get_transfer("job-1", "output-1")
        self.assertEqual(transfer.offset, len(EXPECTED_OUTPUT))
        self.assertEqual(transfer.state, TransferState.VERIFIED)
        metadata = os.stat(result.local_path)
        self.assertEqual(transfer.source_node_id, "7")
        self.assertEqual(transfer.published_device, metadata.st_dev)
        self.assertEqual(transfer.published_inode, metadata.st_ino)

        duplicate = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
                output_root=self.output_root,
            )
        )
        self.assertEqual(duplicate.local_path, result.local_path)
        self.assertEqual(relay.worker.ranges, ["bytes=5-"])

    def test_exact_desktop_download_is_adopted_once_without_worker_fetch(self):
        candidate = self.output_root / "wallpaper.png"
        candidate.write_bytes(EXPECTED_OUTPUT)
        worker = FakeWorker()
        relay = self.relay(worker)

        first = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
                desktop_candidates=(candidate,),
            )
        )
        second = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
                desktop_candidates=(candidate,),
            )
        )

        self.assertEqual(worker.ranges, [])
        self.assertEqual(first.local_path, second.local_path)
        self.assertEqual(first.local_path.read_bytes(), EXPECTED_OUTPUT)
        self.assertEqual(
            first.local_path,
            self.output_root.resolve()
            / "cloud-vast"
            / "session-1"
            / "job-1"
            / "wallpaper.png",
        )
        outputs = [
            item
            for item in self.repository.list_transfers("job-1")
            if not item.artifact_id.startswith("preview:")
        ]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].state, TransferState.VERIFIED)

    def test_mismatched_desktop_download_fetches_to_digest_suffixed_collision(self):
        candidate = self.output_root / "wallpaper.png"
        candidate.write_bytes(b"different-desktop-output")
        collision = (
            self.output_root
            / "cloud-vast"
            / "session-1"
            / "job-1"
            / "wallpaper.png"
        )
        collision.parent.mkdir(mode=0o700, parents=True)
        collision.write_bytes(b"existing-different-output")
        worker = FakeWorker()
        relay = self.relay(worker)
        expected_digest = hashlib.sha256(EXPECTED_OUTPUT).hexdigest()

        result = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
                desktop_candidates=(candidate,),
            )
        )

        self.assertEqual(worker.ranges, ["bytes=0-"])
        self.assertEqual(result.local_path.read_bytes(), EXPECTED_OUTPUT)
        self.assertEqual(
            result.local_path.name,
            "wallpaper-" + expected_digest + ".png",
        )
        self.assertEqual(candidate.read_bytes(), b"different-desktop-output")
        self.assertEqual(collision.read_bytes(), b"existing-different-output")

    def test_persistent_output_rejects_escaping_paths_and_symlinks(self):
        from cloud_run.relay import RelayValidationError

        invalid_descriptors = []
        absolute = descriptor()
        absolute["subfolder"] = "/outside"
        invalid_descriptors.append(absolute)
        traversal = descriptor()
        traversal["subfolder"] = "../outside"
        invalid_descriptors.append(traversal)
        malformed_temp = descriptor()
        malformed_temp["type"] = "temp"
        invalid_descriptors.append(malformed_temp)

        for invalid in invalid_descriptors:
            with self.subTest(invalid=invalid):
                with self.assertRaises(RelayValidationError):
                    asyncio.run(
                        self.relay(FakeWorker()).download_output(
                            job_id="job-1",
                            descriptor=invalid,
                        )
                    )

        outside = self.root / "outside.png"
        outside.write_bytes(EXPECTED_OUTPUT)
        worker = FakeWorker()
        with self.assertRaises(RelayValidationError):
            asyncio.run(
                self.relay(worker).download_output(
                    job_id="job-1",
                    descriptor=descriptor(),
                    desktop_candidates=(outside,),
                )
            )
        self.assertEqual(worker.ranges, [])

        link = self.output_root / "wallpaper.png"
        link.symlink_to(outside)
        with self.assertRaises(RelayValidationError):
            asyncio.run(
                self.relay(FakeWorker()).download_output(
                    job_id="job-1",
                    descriptor=descriptor(),
                )
            )
        link.unlink()

        outside_directory = self.root / "outside-directory"
        outside_directory.mkdir()
        (self.output_root / "cloud-vast").symlink_to(
            outside_directory,
            target_is_directory=True,
        )
        with self.assertRaises(RelayValidationError):
            asyncio.run(
                self.relay(FakeWorker()).download_output(
                    job_id="job-1",
                    descriptor=descriptor(),
                )
            )
        self.assertEqual(list(outside_directory.iterdir()), [])

    def test_verified_output_rejects_changed_node_and_same_byte_replacement(self):
        from cloud_run.relay import ArtifactVerificationError

        relay = self.relay(FakeWorker())
        result = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
            )
        )
        changed_node = descriptor()
        changed_node["node_id"] = "8"

        with self.assertRaises(ArtifactVerificationError):
            asyncio.run(
                relay.download_output(
                    job_id="job-1",
                    descriptor=changed_node,
                )
            )

        replacement = result.local_path.with_suffix(".replacement")
        shutil.copyfile(result.local_path, replacement)
        os.replace(replacement, result.local_path)
        with self.assertRaises(ArtifactVerificationError):
            relay.published_artifact("job-1", "output-1")

    def test_transport_interruption_keeps_only_a_resumable_private_part(self):
        from cloud_run.relay import ArtifactVerificationError
        from cloud_run.worker_client import ArtifactDownload

        class InterruptedWorker(FakeWorker):
            def __init__(self):
                super().__init__()
                self.calls = 0

            async def download_artifact(
                self,
                artifact_id,
                *,
                start,
                on_chunk,
            ):
                self.ranges.append("bytes=" + str(start) + "-")
                self.calls += 1
                if self.calls == 1:
                    result = on_chunk(EXPECTED_OUTPUT[:6])
                    if asyncio.iscoroutine(result):
                        await result
                    raise RuntimeError("synthetic transport detail")
                result = on_chunk(EXPECTED_OUTPUT[start:])
                if asyncio.iscoroutine(result):
                    await result
                return ArtifactDownload(
                    artifact_id=artifact_id,
                    start=start,
                    total_size=len(EXPECTED_OUTPUT),
                    sha256=hashlib.sha256(
                        EXPECTED_OUTPUT
                    ).hexdigest(),
                    mime_type="image/png",
                )

        worker = InterruptedWorker()
        relay = self.relay(worker)
        with self.assertRaises(ArtifactVerificationError) as raised:
            asyncio.run(
                relay.download_output(
                    job_id="job-1",
                    descriptor=descriptor(),
                )
            )
        self.assertNotIn("synthetic", str(raised.exception))
        self.assertEqual(list(self.output_root.iterdir()), [])

        result = asyncio.run(
            relay.download_output(
                job_id="job-1",
                descriptor=descriptor(),
            )
        )
        self.assertEqual(
            worker.ranges,
            ["bytes=0-", "bytes=6-"],
        )
        self.assertEqual(result.local_path.read_bytes(), EXPECTED_OUTPUT)

    def test_sync_sanitizes_events_and_never_publishes_bad_output(self):
        from cloud_run.relay import ArtifactVerificationError

        worker = FakeWorker(
            output=b"wrong",
            descriptors=[descriptor(EXPECTED_OUTPUT)],
            events=[
                {
                    "sequence": 1,
                    "type": "execution_error",
                    "data": {
                        "code": "execution_failed",
                        "message": "provider-key must not survive",
                        "node_id": "7",
                        "secret": "provider-key",
                    },
                    "created_at": 15.0,
                }
            ],
        )
        relay = self.relay(worker)

        with self.assertRaises(ArtifactVerificationError):
            asyncio.run(relay.sync_job("job-1"))

        events = self.repository.list_events("job-1", 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].payload,
            {
                "code": "execution_error",
                "message": "Remote workflow execution failed.",
                "node_id": "7",
            },
        )
        self.assertNotIn("provider-key", repr(events))
        self.assertEqual(list(self.output_root.iterdir()), [])

    def test_sync_catches_up_from_event_94_using_only_atomic_snapshot(self):
        remote_events = [
            {
                "sequence": sequence,
                "type": (
                    "execution_success"
                    if sequence == 158
                    else "progress"
                ),
                "data": (
                    {"timestamp": 158.0}
                    if sequence == 158
                    else {"value": sequence, "max": 158}
                ),
                "created_at": float(sequence),
            }
            for sequence in range(1, 159)
        ]
        for event in remote_events[:93]:
            self.repository.append_event(
                "job-1",
                event["sequence"],
                event["type"],
                event["data"],
                created_at=event["created_at"],
            )
        worker = FakeWorker(descriptors=[], events=remote_events)
        relay = self.relay(worker)

        result = asyncio.run(relay.sync_job("job-1"))

        self.assertEqual(result.last_sequence, 158)
        self.assertEqual(
            self.repository.last_event_sequence("job-1"),
            158,
        )
        self.assertEqual(worker.snapshot_calls, [("job-1", 93)])
        self.assertEqual(worker.events_calls, 0)
        self.assertEqual(worker.job_calls, 0)

    def test_snapshot_replay_is_idempotent_but_gap_or_change_is_rejected(self):
        from cloud_run.relay import RelayValidationError

        relay = self.relay(FakeWorker(descriptors=[]))
        event = {
            "sequence": 1,
            "type": "progress",
            "data": {"value": 1, "max": 2},
            "created_at": 11.0,
        }
        snapshot = {
            "job_id": "job-1",
            "state": "running",
            "prompt_id": "11111111-1111-4111-8111-111111111111",
            "events": [event],
            "last_sequence": 1,
            "outputs": [],
            "error": None,
            "created_at": 10.0,
            "updated_at": 12.0,
        }

        first = asyncio.run(relay.sync_snapshot("job-1", snapshot))
        replay = asyncio.run(relay.sync_snapshot("job-1", snapshot))

        self.assertEqual(len(first.events), 1)
        self.assertEqual(replay.events, ())
        self.assertEqual(len(self.repository.list_events("job-1", 0)), 1)

        changed = {
            **snapshot,
            "events": [{**event, "data": {"value": 2, "max": 2}}],
        }
        with self.assertRaises(RelayValidationError):
            asyncio.run(relay.sync_snapshot("job-1", changed))

        gap = {
            **snapshot,
            "events": [{**event, "sequence": 3}],
            "last_sequence": 3,
        }
        with self.assertRaises(RelayValidationError):
            asyncio.run(relay.sync_snapshot("job-1", gap))

    def test_preview_is_verified_into_private_cache(self):
        preview = b"\x89PNG\r\n\x1a\npreview"
        preview_id = "preview-1"
        worker = FakeWorker(
            descriptors=[],
            events=[
                {
                    "sequence": 1,
                    "type": "b_preview",
                    "data": {
                        "preview_id": preview_id,
                        "mime_type": "image/png",
                        "size_bytes": len(preview),
                        "sha256": hashlib.sha256(preview).hexdigest(),
                    },
                    "created_at": 15.0,
                }
            ],
            previews={
                preview_id: {
                    "content": preview,
                    "mime_type": "image/png",
                    "sha256": hashlib.sha256(preview).hexdigest(),
                }
            },
        )
        relay = self.relay(worker)

        result = asyncio.run(relay.sync_job("job-1"))
        cached = relay.preview_content("job-1", preview_id)

        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.outputs, ())
        self.assertEqual(cached.content, preview)
        self.assertNotIn(str(self.private_root), repr(result.public_payload()))
        transfer = self.repository.get_transfer(
            "job-1",
            "preview:" + preview_id,
        )
        self.assertEqual(transfer.state, TransferState.VERIFIED)

    def test_preview_retry_recovers_after_event_was_already_persisted(self):
        from cloud_run.relay import ArtifactVerificationError

        preview = b"\x89PNG\r\n\x1a\npreview"
        preview_id = "preview-1"

        class FlakyPreviewWorker(FakeWorker):
            def __init__(self):
                super().__init__(
                    descriptors=[],
                    events=[
                        {
                            "sequence": 1,
                            "type": "b_preview",
                            "data": {
                                "preview_id": preview_id,
                                "mime_type": "image/png",
                                "size_bytes": len(preview),
                                "sha256": hashlib.sha256(
                                    preview
                                ).hexdigest(),
                            },
                            "created_at": 15.0,
                        }
                    ],
                )
                self.preview_calls = 0

            async def preview(self, job_id, requested_preview_id):
                self.preview_calls += 1
                if self.preview_calls == 1:
                    raise RuntimeError("private transport detail")
                return {
                    "content": preview,
                    "mime_type": "image/png",
                    "sha256": hashlib.sha256(preview).hexdigest(),
                }

        worker = FlakyPreviewWorker()
        relay = self.relay(worker)
        with self.assertRaises(ArtifactVerificationError) as raised:
            asyncio.run(relay.sync_job("job-1"))
        self.assertNotIn("private transport detail", str(raised.exception))
        self.assertEqual(
            self.repository.last_event_sequence("job-1"),
            1,
        )

        recovered = asyncio.run(relay.sync_job("job-1"))
        self.assertEqual(recovered.last_sequence, 1)
        self.assertEqual(
            relay.preview_content("job-1", preview_id).content,
            preview,
        )


if __name__ == "__main__":
    unittest.main()

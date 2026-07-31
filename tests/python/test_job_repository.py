import json
import os
import tempfile
import types
import unittest
from pathlib import Path

from cloud_run.job_repository import JobRepository
from cloud_run.models import CloudJob, JobState, TransferState


def cloud_job(
    *,
    job_id="job-1",
    session_id="session-1",
    idempotency_key="job-key-1",
):
    return CloudJob(
        job_id=job_id,
        session_id=session_id,
        idempotency_key=idempotency_key,
        state=JobState.CAPTURED,
        prompt_digest="c" * 64,
        capture_json=json.dumps(
            {"workflow": {"nodes": []}, "output": {"1": {}}},
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


class JobRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = (
            Path(self.temporary_directory.name)
            / "private"
            / "sessions.sqlite3"
        )

    def test_job_manifest_event_and_transfer_offsets_survive_reopen(self):
        jobs = JobRepository(self.path)
        jobs.save_manifest("a" * 64, '{"schema_version":1}')
        saved, created = jobs.create_job(cloud_job())
        jobs.append_event(
            "job-1",
            1,
            "progress",
            {"value": 2, "max": 10},
        )
        jobs.save_transfer(
            job_id="job-1",
            artifact_id="output-1",
            direction="download",
            expected_size=10,
            sha256="b" * 64,
            offset=4,
            state=TransferState.TRANSFERRING,
            private_path="/private/output.part",
        )

        reopened = JobRepository(self.path)
        transfer = reopened.get_transfer("job-1", "output-1")
        event = reopened.list_events("job-1", after_sequence=0)[0]

        self.assertTrue(created)
        self.assertEqual(reopened.get_job(saved.job_id), saved)
        self.assertEqual(reopened.get_manifest("a" * 64), '{"schema_version":1}')
        self.assertEqual(transfer.offset, 4)
        self.assertEqual(transfer.state, TransferState.TRANSFERRING)
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.payload, {"max": 10, "value": 2})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_provision_restart_repair_and_installed_sets_survive_reopen(self):
        jobs = JobRepository(self.path)
        jobs.create_provision_transaction(
            transaction_id="tx-1",
            session_id="session-1",
            job_id="job-1",
            manifest_digest="a" * 64,
            state="repairing",
            planned_restart_count=1,
            repair_count=1,
            repair_restart_count=1,
            last_progress_at=50.0,
        )
        jobs.replace_installed_set(
            "session-1",
            [
                {
                    "dependency_id": "model-a",
                    "digest": "b" * 64,
                    "revision": None,
                    "destination": "models/a",
                }
            ],
        )

        reopened = JobRepository(self.path)
        transaction = reopened.get_provision_transaction("tx-1")
        installed = reopened.installed_set("session-1")

        self.assertEqual(
            (
                transaction.planned_restart_count,
                transaction.repair_count,
                transaction.repair_restart_count,
            ),
            (1, 1, 1),
        )
        self.assertEqual(installed[0].digest, "b" * 64)
        self.assertEqual(installed[0].destination, "models/a")

    def test_duplicate_job_key_returns_original_and_stale_save_is_rejected(self):
        jobs = JobRepository(self.path)
        first, first_created = jobs.create_job(cloud_job())
        duplicate, duplicate_created = jobs.create_job(
            cloud_job(job_id="job-2")
        )
        reader = jobs.get_job(first.job_id)
        stale = jobs.get_job(first.job_id)
        updated = jobs.save_job(
            reader.transition(JobState.RESOLVING, now=20.0)
        )

        self.assertTrue(first_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, first)
        self.assertEqual(updated.version, 2)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            jobs.save_job(stale.transition(JobState.FAILED, now=21.0))

    def test_event_poll_replay_is_idempotent_but_gap_or_change_is_rejected(self):
        jobs = JobRepository(self.path)
        jobs.create_job(cloud_job())
        first = jobs.append_event(
            "job-1",
            1,
            "progress",
            {"value": 1, "max": 2},
            created_at=11.0,
        )
        replay = jobs.append_event(
            "job-1",
            1,
            "progress",
            {"value": 1, "max": 2},
            created_at=99.0,
        )

        self.assertEqual(first, replay)
        self.assertEqual(jobs.last_event_sequence("job-1"), 1)
        with self.assertRaisesRegex(ValueError, "identity"):
            jobs.append_event(
                "job-1",
                1,
                "progress",
                {"value": 2, "max": 2},
            )
        with self.assertRaisesRegex(ValueError, "contiguous"):
            jobs.append_event(
                "job-1",
                3,
                "execution_success",
                {},
            )

    def test_lists_only_the_requested_jobs_transfers(self):
        jobs = JobRepository(self.path)
        jobs.create_job(cloud_job())
        jobs.create_job(
            cloud_job(
                job_id="job-2",
                session_id="session-2",
                idempotency_key="job-key-2",
            )
        )
        for job_id, artifact_id in (
            ("job-1", "output-1"),
            ("job-1", "preview:preview-1"),
            ("job-2", "output-2"),
        ):
            jobs.save_transfer(
                job_id=job_id,
                artifact_id=artifact_id,
                direction="download",
                expected_size=10,
                sha256="b" * 64,
                offset=10,
                state=TransferState.VERIFIED,
                private_path="/private/" + artifact_id,
            )

        self.assertEqual(
            [item.artifact_id for item in jobs.list_transfers("job-1")],
            ["output-1", "preview:preview-1"],
        )

    def test_local_artifact_catalog_is_content_verified_and_durable(self):
        content = b"private-input"
        source = self.path.parent / "input.jpg"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        digest = __import__("hashlib").sha256(content).hexdigest()
        jobs = JobRepository(self.path)

        saved = jobs.register_local_artifact(
            types.SimpleNamespace(
                artifact_id="input-1",
                private_path=str(source),
                size_bytes=len(content),
                sha256=digest,
            ),
            created_at=10.0,
        )
        reopened = JobRepository(self.path).get_local_artifact("input-1")

        self.assertEqual(reopened.artifact_id, saved.artifact_id)
        self.assertEqual(reopened.sha256, digest)
        self.assertNotIn(str(source), repr(reopened))
        source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "content changed"):
            jobs.register_local_artifact(
                types.SimpleNamespace(
                    artifact_id="input-1",
                    private_path=str(source),
                    size_bytes=len(content),
                    sha256=digest,
                )
            )


if __name__ == "__main__":
    unittest.main()

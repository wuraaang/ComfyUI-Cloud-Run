import tempfile
import unittest
from pathlib import Path

from cloud_run.job_repository import JobRepository
from cloud_run.orchestrator import LocalOrchestrator, OrchestratorPort
from cloud_run.run_errors import RunErrorCode, RunJournalEntry, RunPhase


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def journal_entry(entry_id="journal-1", created_at=20.0):
    return RunJournalEntry(
        entry_id=entry_id,
        session_id="session-1",
        manifest_digest="a" * 64,
        transaction_id="provision-" + "a" * 64,
        job_id="job-1",
        phase=RunPhase.SYNCHRONIZATION,
        code=RunErrorCode.SYNCHRONIZATION,
        message="Worker snapshot timed out; retry scheduled.",
        node_id=None,
        process_exit_code=None,
        restart_count=1,
        last_probe="gateway reachable",
        byte_cursor=29_347_469_703,
        event_cursor=94,
        output_state="pending",
        details={"retry": 2},
        created_at=created_at,
    )


class LocalOrchestratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = JobRepository(
            Path(temporary.name) / "private" / "sessions.sqlite3"
        )
        self.orchestrator = LocalOrchestrator(repository)

    def test_records_only_validated_metadata_and_resumes_after_timestamp(self):
        first = journal_entry()
        second = journal_entry("journal-2", 21.0)

        self.assertTrue(self.orchestrator.record(first))
        self.assertFalse(self.orchestrator.record(first))
        self.assertTrue(self.orchestrator.record(second))
        self.assertEqual(
            self.orchestrator.entries("session-1", after=20.0),
            (second,),
        )
        self.assertTrue(hasattr(OrchestratorPort, "entries"))

        for invalid in (
            b"workflow-bytes",
            {"workflow": {"nodes": []}},
            {"api_key": "private-value"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    self.orchestrator.record(invalid)

    def test_module_has_no_saas_or_frontend_framework_dependency(self):
        source = (
            REPOSITORY_ROOT / "cloud_run" / "orchestrator.py"
        ).read_text(encoding="utf-8").casefold()
        self.assertNotIn("convex", source)
        self.assertNotIn("tanstack", source)


if __name__ == "__main__":
    unittest.main()

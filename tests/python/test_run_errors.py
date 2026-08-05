import unittest
from pathlib import Path
import tempfile

from cloud_run.job_repository import JobRepository
from cloud_run.orchestrator import LocalOrchestrator
from cloud_run.run_errors import (
    JournalPort,
    MAX_JOURNAL_DETAILS_BYTES,
    MAX_SAFE_TEXT_BYTES,
    RunErrorCode,
    RunFailureBoundary,
    RunJournalEntry,
    RunPhase,
    SafeRunError,
    classify_run_error,
    record_safe_run_error,
    sanitize_text,
)


class RunErrorContractTests(unittest.TestCase):
    def test_all_run_error_code_mappings(self):
        cases = (
            (RunFailureBoundary.VALIDATION, RunErrorCode.VALIDATION, RunPhase.PREFLIGHT),
            (RunFailureBoundary.DEPENDENCY, RunErrorCode.DEPENDENCY, RunPhase.PROVISIONING),
            (RunFailureBoundary.TRANSFER, RunErrorCode.TRANSFER, RunPhase.TRANSFER),
            (RunFailureBoundary.QUOTE, RunErrorCode.QUOTE_EXPIRED, RunPhase.QUOTE),
            (RunFailureBoundary.PROVIDER, RunErrorCode.PROVIDER, RunPhase.PROVIDER),
            (RunFailureBoundary.PROVISIONING, RunErrorCode.PROVISIONING, RunPhase.PROVISIONING),
            (RunFailureBoundary.COMFY_STARTUP, RunErrorCode.COMFY_STARTUP, RunPhase.READINESS),
            (RunFailureBoundary.EXECUTION, RunErrorCode.EXECUTION, RunPhase.EXECUTION),
            (RunFailureBoundary.SYNCHRONIZATION, RunErrorCode.SYNCHRONIZATION, RunPhase.SYNCHRONIZATION),
            (RunFailureBoundary.HARVEST, RunErrorCode.HARVEST, RunPhase.HARVEST),
            (RunFailureBoundary.INVALID_OUTPUT, RunErrorCode.INVALID_OUTPUT, RunPhase.HARVEST),
            (RunFailureBoundary.WORKER_RESTART, RunErrorCode.WORKER_RESTART, RunPhase.EXECUTION),
            (RunFailureBoundary.LIFECYCLE, RunErrorCode.LIFECYCLE, RunPhase.TEARDOWN),
            (RunFailureBoundary.INTERNAL, RunErrorCode.INTERNAL, RunPhase.INTERNAL),
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = JobRepository(Path(temporary.name) / "attempts.sqlite3")
        journal = LocalOrchestrator(repository)

        for index, (boundary, code, phase) in enumerate(cases, start=1):
            with self.subTest(boundary=boundary):
                cause = RuntimeError(
                    "token=private-value " + str(index)
                )
                error = classify_run_error(
                    boundary,
                    correlation_id="mapping-" + str(index),
                    phase=phase,
                    cause=cause,
                )
                self.assertEqual(error.code, code)
                self.assertEqual(error.phase, phase)
                self.assertIs(error.cause, cause)
                self.assertEqual(
                    set(error.public_payload()),
                    {"code", "message", "action"},
                )
                self.assertNotIn("private-value", repr(error))
                self.assertTrue(
                    record_safe_run_error(
                        journal,
                        error,
                        session_id="session-1",
                        manifest_digest="a" * 64,
                        job_id="job-" + str(index),
                        created_at=float(index),
                    )
                )
                self.assertFalse(
                    record_safe_run_error(
                        journal,
                        error,
                        session_id="session-1",
                        manifest_digest="a" * 64,
                        job_id="job-" + str(index),
                        created_at=float(index),
                    )
                )

        entries = repository.list_journal("session-1")
        self.assertEqual(len(entries), 14)
        self.assertEqual({entry.code for entry in entries}, set(RunErrorCode))
        rendered = repr(entries)
        self.assertNotIn("private-value", rendered)
        self.assertNotIn("token", rendered.casefold())

        unknown_cause = ValueError("password=unknown-private-value")
        unknown = classify_run_error(
            "unregistered-boundary",
            correlation_id="mapping-unknown",
            cause=unknown_cause,
        )
        self.assertEqual(unknown.code, RunErrorCode.INTERNAL)
        self.assertEqual(unknown.phase, RunPhase.INTERNAL)
        self.assertIs(unknown.cause, unknown_cause)
        self.assertNotIn("unknown-private-value", repr(unknown))

    def test_codes_and_phases_are_exact(self):
        self.assertEqual(
            {item.value for item in RunErrorCode},
            {
                "validation_error",
                "dependency_error",
                "transfer_error",
                "quote_expired",
                "provider_error",
                "provisioning_error",
                "comfy_startup_error",
                "execution_error",
                "synchronization_error",
                "harvest_error",
                "invalid_output",
                "worker_restart_error",
                "lifecycle_error",
                "internal_error",
            },
        )
        self.assertEqual(
            {item.value for item in RunPhase},
            {
                "preflight",
                "quote",
                "provider",
                "bootstrap",
                "transfer",
                "provisioning",
                "readiness",
                "execution",
                "synchronization",
                "harvest",
                "teardown",
                "internal",
            },
        )

    def test_sanitizer_removes_every_secret_shape_and_bounds_output(self):
        raw = (
            "Authorization: Bearer abcdef token=xyz api_key=qwerty "
            "https://host/path?X-Amz-Signature=secret "
            "/Users/alice/private/model.safetensors\n"
            + "x" * 40_000
        )
        safe = sanitize_text(
            raw,
            local_roots=(Path("/Users/alice/private"),),
        )
        self.assertLessEqual(
            len(safe.encode("utf-8")),
            MAX_SAFE_TEXT_BYTES,
        )
        for forbidden in (
            "abcdef",
            "xyz",
            "qwerty",
            "secret",
            "/Users/alice",
        ):
            self.assertNotIn(forbidden, safe)

    def test_safe_error_and_journal_values_reject_unsafe_evidence(self):
        error = SafeRunError(
            code=RunErrorCode.SYNCHRONIZATION,
            phase=RunPhase.SYNCHRONIZATION,
            message="Worker snapshot timed out; retry scheduled.",
            correlation_id="correlation-1",
            node_id=None,
            retryable=True,
        )
        entry = RunJournalEntry(
            entry_id="journal-1",
            session_id="session-1",
            manifest_digest="a" * 64,
            transaction_id="provision-" + "a" * 64,
            job_id="job-1",
            phase=error.phase,
            code=error.code,
            message=error.message,
            node_id=error.node_id,
            process_exit_code=None,
            restart_count=1,
            last_probe="gateway reachable",
            byte_cursor=29_347_469_703,
            event_cursor=94,
            output_state="pending",
            details={"retry": 2, "inventory": "unchanged"},
            created_at=20.0,
        )

        self.assertEqual(entry.code, RunErrorCode.SYNCHRONIZATION)
        self.assertEqual(entry.details, {"inventory": "unchanged", "retry": 2})
        self.assertTrue(hasattr(JournalPort, "record"))
        self.assertEqual(MAX_JOURNAL_DETAILS_BYTES, 64 * 1024)

        unsafe_errors = (
            {"message": "token=private-value"},
            {"correlation_id": "https://invalid.example"},
            {"retryable": 1},
        )
        for changes in unsafe_errors:
            with self.subTest(changes=changes):
                values = {
                    "code": RunErrorCode.SYNCHRONIZATION,
                    "phase": RunPhase.SYNCHRONIZATION,
                    "message": "Safe message.",
                    "correlation_id": "correlation-1",
                    "node_id": None,
                    "retryable": True,
                    **changes,
                }
                with self.assertRaises(ValueError):
                    SafeRunError(**values)

        for details in (
            {"payload": b"workflow-bytes"},
            {"api_key": "private-value"},
            {"vast_api_key": "private-value"},
            {"r2_secret_access_key": "private-value"},
            {"nested": {"token": "private-value"}},
            {1: "non-string-key"},
        ):
            with self.subTest(details=details):
                with self.assertRaises(ValueError):
                    RunJournalEntry(
                        **{
                            **entry.__dict__,
                            "entry_id": "unsafe-journal",
                            "details": details,
                        }
                    )


if __name__ == "__main__":
    unittest.main()

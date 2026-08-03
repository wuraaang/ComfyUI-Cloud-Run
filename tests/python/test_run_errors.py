import unittest
from pathlib import Path

from cloud_run.run_errors import (
    JournalPort,
    MAX_JOURNAL_DETAILS_BYTES,
    MAX_SAFE_TEXT_BYTES,
    RunErrorCode,
    RunJournalEntry,
    RunPhase,
    SafeRunError,
    sanitize_text,
)


class RunErrorContractTests(unittest.TestCase):
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

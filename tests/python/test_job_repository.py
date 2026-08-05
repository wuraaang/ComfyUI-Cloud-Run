from contextlib import closing
import hashlib
import json
import os
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

from cloud_run.job_repository import JobRepository
from cloud_run.models import (
    CloudJob,
    ExecutionState,
    HarvestState,
    JobState,
    TransferState,
)
from cloud_run.run_errors import RunErrorCode, RunJournalEntry, RunPhase
from cloud_run.repository import (
    ConcurrentDesktopRelayUpdate,
    DesktopRelayConfig,
)


def cloud_job(
    *,
    job_id="job-1",
    session_id="session-1",
    idempotency_key="job-key-1",
    execution_state=ExecutionState.PENDING,
    harvest_state=HarvestState.PENDING,
    error_code=None,
    queue_position=0,
    native_request_digest=None,
    native_body_json=None,
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
        execution_state=execution_state,
        harvest_state=harvest_state,
        error_code=error_code,
        queue_position=queue_position,
        native_request_digest=native_request_digest,
        native_body_json=native_body_json,
    )


def readiness_report(
    *,
    attempt_number=1,
    failed_check=None,
    observed_at=None,
):
    from cloud_run.readiness import (
        REQUIRED_READINESS_CHECKS,
        ReadinessCheck,
        ReadinessReport,
        evidence_digest,
        readiness_message,
    )

    observed = (
        float(99 + attempt_number)
        if observed_at is None
        else float(observed_at)
    )
    checks = tuple(
        ReadinessCheck(
            name=name,
            status="failed" if name == failed_check else "passed",
            evidence_digest=evidence_digest(
                {
                    "attempt_number": attempt_number,
                    "check": name,
                    "status": "failed" if name == failed_check else "passed",
                }
            ),
            message=readiness_message(
                name, "failed" if name == failed_check else "passed"
            ),
            diagnostic_code=(
                "native_http_status" if name == failed_check else None
            ),
        )
        for name in REQUIRED_READINESS_CHECKS
    )
    return ReadinessReport.create(
        session_id="session-1",
        instance_id="instance-1",
        worker_release_digest="a" * 64,
        manifest_digest="b" * 64,
        profile_revision=3,
        relay_origin="http://127.0.0.1:32145",
        inventory_observed_at=observed,
        created_at=observed + 0.5,
        attempt_number=attempt_number,
        checks=checks,
    )


class JobRepositoryTests(unittest.TestCase):
    def test_native_queue_positions_and_request_material_are_durable_and_idempotent(self):
        jobs = JobRepository(self.path)
        body = json.dumps(
            {
                "client_id": "desktop-client-1",
                "extra_data": {
                    "extra_pnginfo": {
                        "workflow": {
                            "extra": {"frontendVersion": "1.47.10"},
                            "nodes": [],
                        }
                    }
                },
                "prompt": {
                    "1": {"class_type": "SaveImage", "inputs": {}}
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        first, first_created = jobs.create_job(
            cloud_job(
                native_request_digest=hashlib.sha256(
                    body.encode("utf-8")
                ).hexdigest(),
                native_body_json=body,
            )
        )
        second, second_created = jobs.create_job(
            cloud_job(
                job_id="job-0",
                idempotency_key="job-key-2",
                native_request_digest=hashlib.sha256(
                    body.encode("utf-8")
                ).hexdigest(),
                native_body_json=body,
            )
        )
        duplicate, duplicate_created = jobs.create_job(
            cloud_job(
                job_id="job-retry",
                idempotency_key="job-key-1",
                native_request_digest=hashlib.sha256(
                    body.encode("utf-8")
                ).hexdigest(),
                native_body_json=body,
            )
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.queue_position, 1)
        self.assertEqual(second.queue_position, 2)
        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertEqual(duplicate.queue_position, 1)
        reopened = JobRepository(self.path)
        self.assertEqual(reopened.get_job("job-1"), first)
        self.assertEqual(reopened.get_job("job-0"), second)

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
        self.assertIsNone(transfer.source_node_id)
        self.assertIsNone(transfer.published_device)
        self.assertIsNone(transfer.published_inode)
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.payload, {"max": 10, "value": 2})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_run_journal_is_idempotent_bounded_and_survives_reopen(self):
        jobs = JobRepository(self.path)
        entry = RunJournalEntry(
            entry_id="journal-1",
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
            details={"retry": 2, "inventory": "unchanged"},
            created_at=20.0,
        )

        self.assertTrue(jobs.record_journal(entry))
        self.assertFalse(jobs.record_journal(entry))
        self.assertEqual(JobRepository(self.path).list_journal("session-1"), [entry])
        self.assertEqual(
            JobRepository(self.path).list_journal("session-1", after=20.0),
            [],
        )

        with self.assertRaisesRegex(ValueError, "identity"):
            jobs.record_journal(
                RunJournalEntry(
                    **{
                        **entry.__dict__,
                        "message": "A different safe message.",
                    }
                )
            )

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
        self.assertIsNone(transaction.phase)
        self.assertIsNone(transaction.current_dependency_id)
        self.assertEqual(transaction.transferred_bytes, 0)
        self.assertEqual(transaction.total_bytes, 0)
        self.assertEqual(installed[0].digest, "b" * 64)
        self.assertEqual(installed[0].destination, "models/a")

    def test_installed_set_accepts_exact_content_addressed_ui_revision(self):
        jobs = JobRepository(self.path)
        revision = "sha256:" + "c" * 64

        jobs.replace_installed_set(
            "session-1",
            [
                {
                    "dependency_id": "hermes-nous",
                    "digest": "b" * 64,
                    "revision": revision,
                    "destination": "custom_nodes/hermes-nous",
                }
            ],
        )

        installed = JobRepository(self.path).installed_set("session-1")
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0].revision, revision)

        for invalid in (
            "sha256:" + "C" * 64,
            "sha256:" + "c" * 63,
            "sha256:" + "c" * 65,
            "sha512:" + "c" * 64,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    ValueError,
                    "Invalid installed revision",
                ):
                    jobs.replace_installed_set(
                        "session-1",
                        [
                            {
                                "dependency_id": "hermes-nous",
                                "digest": "b" * 64,
                                "revision": invalid,
                                "destination": "custom_nodes/hermes-nous",
                            }
                        ],
                    )

    def test_provision_progress_upserts_exact_bounds_and_selects_latest(self):
        jobs = JobRepository(self.path)
        base = {
            "session_id": "session-1",
            "job_id": "job-1",
            "manifest_digest": "a" * 64,
            "state": "applying",
            "sanitized_error": None,
        }

        first = jobs.record_provision_progress(
            transaction_id="provision-a",
            phase="model_transfer",
            current_dependency_id="model-" + "b" * 64,
            transferred_bytes=4,
            total_bytes=10,
            last_progress_at=50.0,
            **base,
        )
        ready = jobs.record_provision_progress(
            transaction_id="provision-a",
            phase="ready",
            current_dependency_id=None,
            transferred_bytes=10,
            total_bytes=10,
            last_progress_at=55.0,
            **{**base, "state": "ready"},
        )
        latest = jobs.record_provision_progress(
            transaction_id="provision-b",
            phase="environment_validation",
            current_dependency_id=None,
            transferred_bytes=2,
            total_bytes=20,
            last_progress_at=60.0,
            **{**base, "manifest_digest": "c" * 64},
        )

        self.assertEqual(first.phase, "model_transfer")
        self.assertEqual(ready.transferred_bytes, 10)
        self.assertEqual(
            JobRepository(self.path).latest_provision_transaction(
                "session-1"
            ),
            latest,
        )

        valid = {
            "transaction_id": "provision-invalid",
            "phase": "model_transfer",
            "current_dependency_id": "model-1",
            "transferred_bytes": 1,
            "total_bytes": 2,
            "last_progress_at": 70.0,
            **base,
        }
        invalid = (
            {**valid, "phase": "unknown"},
            {**valid, "phase": []},
            {**valid, "current_dependency_id": "https://example.com/file"},
            {**valid, "transferred_bytes": -1},
            {**valid, "transferred_bytes": 3},
            {**valid, "last_progress_at": float("nan")},
            {**valid, "sanitized_error": "secret\nheader"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    jobs.record_provision_progress(**payload)

    def test_desktop_relay_config_is_exact_secret_free_and_concurrent(self):
        jobs = JobRepository(self.path)
        first = jobs.save_desktop_relay(
            DesktopRelayConfig(
                bind_host="127.0.0.1",
                port=32145,
                active_session_id=None,
                profile_revision=None,
                updated_at=10.0,
            )
        )
        active = jobs.save_desktop_relay(
            DesktopRelayConfig(
                bind_host="127.0.0.1",
                port=32145,
                active_session_id="session-1",
                profile_revision=3,
                updated_at=11.0,
            ),
            expected_updated_at=first.updated_at,
        )

        self.assertEqual(JobRepository(self.path).get_desktop_relay(), active)
        with self.assertRaises(ConcurrentDesktopRelayUpdate):
            jobs.save_desktop_relay(
                DesktopRelayConfig(
                    bind_host="127.0.0.1",
                    port=32145,
                    active_session_id=None,
                    profile_revision=None,
                    updated_at=12.0,
                ),
                expected_updated_at=10.0,
            )
        with self.assertRaises(ConcurrentDesktopRelayUpdate):
            jobs.save_desktop_relay(
                DesktopRelayConfig(
                    bind_host="127.0.0.1",
                    port=32145,
                    active_session_id=None,
                    profile_revision=None,
                    updated_at=11.0,
                ),
                expected_updated_at=11.0,
            )
        with closing(sqlite3.connect(self.path)) as connection:
            columns = [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(desktop_relay)"
                )
            ]
            row = connection.execute(
                "SELECT * FROM desktop_relay"
            ).fetchone()
        self.assertEqual(
            columns,
            [
                "singleton",
                "bind_host",
                "port",
                "active_session_id",
                "profile_revision",
                "updated_at",
            ],
        )
        rendered = repr((columns, row))
        for forbidden in ("bearer", "secret", "token", "worker_url"):
            self.assertNotIn(forbidden, rendered.casefold())

    def test_failed_attempts_are_append_only_and_do_not_block_later_success(self):
        jobs = JobRepository(self.path)
        first = readiness_report(
            attempt_number=1,
            failed_check="native_http_probe",
        )
        second = readiness_report(
            attempt_number=2,
            failed_check="native_websocket_probe",
        )
        success = readiness_report(attempt_number=3)

        self.assertEqual(jobs.save_readiness_report(first), first)
        self.assertEqual(jobs.save_readiness_report(second), second)
        self.assertIsNone(
            jobs.current_readiness_report(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            )
        )
        self.assertEqual(jobs.save_readiness_report(success), success)
        self.assertEqual(
            jobs.list_readiness_attempts(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            ),
            [first, second, success],
        )
        self.assertEqual(
            jobs.latest_readiness_attempt(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            ),
            success,
        )

    def test_success_is_reused_but_failures_remain_queryable(self):
        jobs = JobRepository(self.path)
        failed = readiness_report(
            attempt_number=1,
            failed_check="native_http_probe",
        )
        success = readiness_report(attempt_number=2)
        later_success = readiness_report(attempt_number=3)

        jobs.save_readiness_report(failed)
        jobs.save_readiness_report(success)
        reused = jobs.save_readiness_report(later_success)

        self.assertEqual(reused, success)
        self.assertEqual(
            jobs.list_readiness_attempts(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            ),
            [failed, success],
        )
        self.assertEqual(
            jobs.current_readiness_report(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            ),
            success,
        )

    def test_v13_failed_report_migrates_without_blocking_retry(self):
        from cloud_run.readiness import (
            REQUIRED_READINESS_CHECKS,
            readiness_message,
        )

        self.path.parent.mkdir(parents=True)
        checks = [
            {
                "name": name,
                "status": (
                    "failed" if name == "native_http_probe" else "passed"
                ),
                "evidence_digest": hashlib.sha256(
                    name.encode("ascii")
                ).hexdigest(),
                "message": (
                    "Readiness proof failed."
                    if name == "native_http_probe"
                    else "Readiness proof passed."
                ),
            }
            for name in REQUIRED_READINESS_CHECKS
        ]
        identity = {
            "session_id": "session-1",
            "instance_id": "instance-1",
            "worker_release_digest": "a" * 64,
            "manifest_digest": "b" * 64,
            "profile_revision": 3,
            "relay_origin": "http://127.0.0.1:32145",
            "inventory_observed_at": 100.0,
            "created_at": 101.0,
            "checks": checks,
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        old_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_meta VALUES ('schema_version', '13')"
            )
            connection.execute(
                """
                CREATE TABLE readiness_reports (
                    report_digest TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    worker_release_digest TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    profile_revision INTEGER NOT NULL,
                    relay_origin TEXT NOT NULL,
                    inventory_observed_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    checks_json TEXT NOT NULL,
                    ready INTEGER NOT NULL CHECK(ready IN (0, 1)),
                    UNIQUE(
                        session_id, instance_id, worker_release_digest,
                        manifest_digest, profile_revision, relay_origin
                    )
                )
                """
            )
            connection.execute(
                """
                INSERT INTO readiness_reports VALUES (
                    ?, 'session-1', 'instance-1', ?, ?, 3,
                    'http://127.0.0.1:32145', 100.0, 101.0, ?, 0
                )
                """,
                (
                    old_digest,
                    "a" * 64,
                    "b" * 64,
                    json.dumps(checks, separators=(",", ":"), sort_keys=True),
                ),
            )
            connection.commit()

        jobs = JobRepository(self.path)
        migrated = jobs.latest_readiness_attempt(
            session_id="session-1",
            instance_id="instance-1",
            worker_release_digest="a" * 64,
            manifest_digest="b" * 64,
            profile_revision=3,
            relay_origin="http://127.0.0.1:32145",
        )

        self.assertEqual(migrated.attempt_number, 1)
        self.assertFalse(migrated.ready)
        self.assertEqual(
            next(
                item.diagnostic_code
                for item in migrated.checks
                if item.status == "failed"
            ),
            "legacy_readiness_failure",
        )
        self.assertTrue(
            all(
                item.message == readiness_message(item.name, item.status)
                for item in migrated.checks
            )
        )
        self.assertNotEqual(migrated.report_digest, old_digest)
        self.assertIsNone(
            jobs.current_readiness_report(
                session_id="session-1",
                instance_id="instance-1",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            )
        )
        retry = readiness_report(attempt_number=2)
        self.assertEqual(jobs.save_readiness_report(retry), retry)
        with closing(sqlite3.connect(self.path)) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        self.assertEqual(version, "14")

    def test_legacy_schema_is_migrated_idempotently_to_readiness_v14(self):
        self.path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE provision_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    job_id TEXT,
                    manifest_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    planned_restart_count INTEGER NOT NULL,
                    repair_count INTEGER NOT NULL,
                    repair_restart_count INTEGER NOT NULL,
                    last_progress_at REAL NOT NULL,
                    sanitized_error TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO provision_transactions VALUES (
                    'legacy-tx', 'session-1', NULL, ?, 'applying',
                    0, 0, 0, 25.0, NULL
                )
                """,
                ("d" * 64,),
            )
            connection.execute(
                """
                CREATE TABLE transfers (
                    job_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    expected_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    offset INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    private_path TEXT NOT NULL,
                    PRIMARY KEY(job_id, artifact_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transfers VALUES (
                    'legacy-job', 'legacy-output', 'download', 10, ?, 10,
                    'verified', '/private/legacy-output.png'
                )
                """,
                ("e" * 64,),
            )
            connection.commit()

        jobs = JobRepository(self.path)
        reopened = JobRepository(self.path)
        legacy = reopened.get_provision_transaction("legacy-tx")
        legacy_transfer = reopened.get_transfer(
            "legacy-job",
            "legacy-output",
        )
        with closing(sqlite3.connect(self.path)) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(provision_transactions)"
                )
            }
            schema_version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            transfer_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(transfers)"
                )
            }
            job_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(jobs)")
            }
            journal_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'run_journal'
                """
            ).fetchone()
            readiness_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'readiness_reports'
                """
            ).fetchone()

        self.assertIsNotNone(jobs)
        self.assertEqual(
            {
                "phase",
                "current_dependency_id",
                "transferred_bytes",
                "total_bytes",
            }.difference(columns),
            set(),
        )
        self.assertIsNone(legacy.phase)
        self.assertEqual(legacy.transferred_bytes, 0)
        self.assertEqual(legacy.total_bytes, 0)
        self.assertEqual(
            {
                "source_node_id",
                "published_device",
                "published_inode",
            }.difference(transfer_columns),
            set(),
        )
        self.assertIsNone(legacy_transfer.source_node_id)
        self.assertIsNone(legacy_transfer.published_device)
        self.assertIsNone(legacy_transfer.published_inode)
        self.assertEqual(
            {
                "execution_state",
                "harvest_state",
                "error_code",
            }.difference(job_columns),
            set(),
        )
        self.assertIsNotNone(journal_exists)
        self.assertIsNotNone(readiness_exists)
        self.assertEqual(schema_version, "14")
        self.assertIn("queue_position", job_columns)
        self.assertIn("native_request_digest", job_columns)
        self.assertIn("native_body_json", job_columns)

    def test_execution_success_survives_an_independent_harvest_failure(self):
        jobs = JobRepository(self.path)
        saved, created = jobs.create_job(
            cloud_job(
                execution_state=ExecutionState.SUCCEEDED,
                harvest_state=HarvestState.FAILED,
                error_code=RunErrorCode.INVALID_OUTPUT,
            )
        )
        reopened = JobRepository(self.path).get_job(saved.job_id)

        self.assertTrue(created)
        self.assertEqual(reopened.execution_state, ExecutionState.SUCCEEDED)
        self.assertEqual(reopened.harvest_state, HarvestState.FAILED)
        self.assertEqual(reopened.error_code, RunErrorCode.INVALID_OUTPUT)

    def test_verified_output_provenance_survives_reopen_and_is_immutable(self):
        jobs = JobRepository(self.path)
        jobs.save_transfer(
            job_id="job-1",
            artifact_id="output-1",
            direction="download",
            expected_size=10,
            sha256="b" * 64,
            offset=10,
            state=TransferState.VERIFIED,
            private_path="/private/output.png",
            source_node_id="2",
            published_device=101,
            published_inode=202,
        )

        transfer = JobRepository(self.path).get_transfer(
            "job-1",
            "output-1",
        )

        self.assertEqual(transfer.source_node_id, "2")
        self.assertEqual(transfer.published_device, 101)
        self.assertEqual(transfer.published_inode, 202)
        for changes in (
            {"source_node_id": "9"},
            {"published_device": 303},
            {"published_inode": 404},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "identity"):
                    jobs.save_transfer(
                        job_id="job-1",
                        artifact_id="output-1",
                        direction="download",
                        expected_size=10,
                        sha256="b" * 64,
                        offset=10,
                        state=TransferState.VERIFIED,
                        private_path="/private/output.png",
                        source_node_id=changes.get("source_node_id", "2"),
                        published_device=changes.get(
                            "published_device",
                            101,
                        ),
                        published_inode=changes.get(
                            "published_inode",
                            202,
                        ),
                    )

    def test_identical_verified_output_is_one_durable_transfer_record(self):
        jobs = JobRepository(self.path)
        record = {
            "job_id": "job-1",
            "artifact_id": "output-1",
            "direction": "download",
            "expected_size": 10,
            "sha256": "b" * 64,
            "offset": 10,
            "state": TransferState.VERIFIED,
            "private_path": (
                "/output/cloud-vast/session-1/job-1/output.png"
            ),
            "source_node_id": "2",
            "published_device": 101,
            "published_inode": 202,
        }

        jobs.save_transfer(**record)
        jobs.save_transfer(**record)
        reopened = JobRepository(self.path)

        self.assertEqual(len(reopened.list_transfers("job-1")), 1)
        self.assertEqual(
            reopened.get_transfer("job-1", "output-1").private_path,
            record["private_path"],
        )

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

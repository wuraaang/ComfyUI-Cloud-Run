import concurrent.futures
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud_run.models import (
    AttemptState,
    CloudAttempt,
    CloudSession,
    OfferQuote,
    SessionState,
)
from cloud_run import repository


def make_attempt(key="idem-1", attempt_id="attempt-1", now=100.0):
    return CloudAttempt.new(
        idempotency_key=key,
        quote=OfferQuote(
            offer_id="42",
            gpu_name="RTX 4090",
            gpu_ram_gb=24.0,
            dph_total=0.42,
            reliability=0.99,
            max_price_per_hour=0.55,
            expires_at=160.0,
            disk_gb=80,
            transfer_bytes=0,
            output_allowance_bytes=1,
            inet_down_cost=None,
            inet_up_cost=None,
            duration_seconds=7200,
            deadline_mode="finite",
            approximate_max_active_charge=0.84,
            template_hash_id="1" * 32,
            worker_commit="a" * 40,
            worker_archive_sha256="b" * 64,
            protocol_version="1",
            manifest_digest="a" * 64,
            execution_baseline_digest="e" * 64,
            randomized_seed_node_ids=("3",),
            machine_id="machine-7",
            host_id="host-3",
            public_ipaddr="203.0.113.7",
            max_instance_creates=1,
        ),
        attempt_id=attempt_id,
        now=now,
    )


def make_session(key="session-key", session_id="session-1", now=100.0):
    return CloudSession.new(
        key,
        session_id=session_id,
        quote=make_attempt().quote,
        manifest_digest="a" * 64,
        deadline_at=now + 7200,
        disk_gb=80,
        now=now,
    )


def make_confirming_session(
    key="confirming-key",
    session_id="confirming-session",
    now=100.0,
):
    return make_session(
        key=key,
        session_id=session_id,
        now=now,
    ).transition(
        SessionState.OFFER_SELECTED,
        now=now + 1,
    ).transition(
        SessionState.CONFIRMING,
        now=now + 2,
    )


class AttemptRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "private" / "attempts.sqlite3"

    def repository_api(self):
        for name in ("AttemptRepository", "ConcurrentAttemptUpdate"):
            self.assertTrue(hasattr(repository, name), name + " is required")
        return repository.AttemptRepository, repository.ConcurrentAttemptUpdate

    def test_create_round_trip_reopen_and_private_permissions(self):
        AttemptRepository, _ = self.repository_api()
        store = AttemptRepository(self.database_path)

        saved, created = store.create_or_get(make_attempt())
        reopened = AttemptRepository(self.database_path).get(saved.attempt_id)

        self.assertTrue(created)
        self.assertEqual(reopened, saved)
        self.assertEqual(os.stat(self.database_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.database_path.parent).st_mode & 0o777, 0o700)

    def test_duplicate_idempotency_key_returns_the_original_attempt(self):
        AttemptRepository, _ = self.repository_api()
        store = AttemptRepository(self.database_path)
        first, first_created = store.create_or_get(make_attempt())
        duplicate, duplicate_created = store.create_or_get(
            make_attempt(key="idem-1", attempt_id="different-attempt", now=200.0)
        )

        self.assertTrue(first_created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, first)
        self.assertEqual(len(store.list_all()), 1)

    def test_concurrent_duplicate_creation_persists_exactly_one_row(self):
        AttemptRepository, _ = self.repository_api()
        store = AttemptRepository(self.database_path)

        def create(index):
            return store.create_or_get(
                make_attempt(
                    key="same-browser-request",
                    attempt_id="attempt-" + str(index),
                    now=100.0 + index,
                )
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(8)))

        self.assertEqual(sum(1 for _, created in results if created), 1)
        self.assertEqual(len({attempt.attempt_id for attempt, _ in results}), 1)
        self.assertEqual(len(store.list_all()), 1)

    def test_optimistic_version_rejects_a_stale_writer(self):
        AttemptRepository, ConcurrentAttemptUpdate = self.repository_api()
        store = AttemptRepository(self.database_path)
        saved, _ = store.create_or_get(make_attempt())
        first_reader = store.get(saved.attempt_id)
        stale_reader = store.get(saved.attempt_id)

        updated = store.save(
            first_reader.transition(AttemptState.CREATING, now=101.0)
        )

        self.assertEqual(updated.version, 2)
        with self.assertRaises(ConcurrentAttemptUpdate):
            store.save(stale_reader.transition(AttemptState.CANCELLED, now=102.0))

    def test_transition_and_recoverable_listing_are_durable(self):
        AttemptRepository, _ = self.repository_api()
        store = AttemptRepository(self.database_path)
        active, _ = store.create_or_get(make_attempt())
        finished, _ = store.create_or_get(
            make_attempt(key="idem-2", attempt_id="attempt-2", now=200.0)
        )

        active = store.transition(
            active.attempt_id,
            AttemptState.CREATING,
            now=101.0,
            instance_id="instance-1",
        )
        store.transition(
            finished.attempt_id,
            AttemptState.CANCELLED,
            now=201.0,
        )

        reopened = AttemptRepository(self.database_path)
        self.assertEqual(reopened.get(active.attempt_id).instance_id, "instance-1")
        self.assertEqual(
            [attempt.attempt_id for attempt in reopened.list_recoverable()],
            ["attempt-1"],
        )


class SessionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name)
            / "private"
            / "sessions.sqlite3"
        )

    def _assert_create_claim_blocked(self, blocker):
        sessions = repository.SessionRepository(self.database_path)
        target = make_confirming_session()
        sessions.create_or_get(target)
        sessions.create_or_get(blocker)

        with self.assertRaises(repository.PaidRentalConflict):
            sessions.claim_create_intent(
                target.session_id,
                now=110.0,
                provider_token="a" * 64,
                session_secret_hex="b" * 64,
            )

        reopened = sessions.get(target.session_id)
        self.assertEqual(reopened.state, SessionState.OFFER_SELECTED)
        self.assertIsNone(reopened.provider_token)
        self.assertIsNone(reopened.session_secret_hex)

    def test_atomic_create_claim_blocks_another_unknown_session(self):
        blocker = make_confirming_session(
            key="unknown-key",
            session_id="unknown-session",
        ).transition(
            SessionState.CREATING,
            now=103.0,
            provider_token="c" * 64,
            session_secret_hex="d" * 64,
        )

        self._assert_create_claim_blocked(blocker)

    def test_atomic_create_claim_blocks_another_active_session(self):
        blocker = CloudSession.new(
            "active-key",
            session_id="active-session",
            now=90.0,
            state=SessionState.READY,
        ).transition(
            SessionState.READY,
            now=91.0,
            instance_id="instance-active",
        )

        self._assert_create_claim_blocked(blocker)

    def test_failed_with_instance_blocks_create(self):
        blocker = CloudSession.new(
            "failed-instance-key",
            session_id="failed-instance-session",
            now=90.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            now=91.0,
            instance_id="instance-residual",
            failure_code="offer_unavailable",
        )

        self._assert_create_claim_blocked(blocker)

    def test_failed_with_residual_inventory_blocks_create(self):
        blocker = CloudSession.new(
            "failed-residual-key",
            session_id="failed-residual-session",
            now=90.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            now=91.0,
            residual_inventory=("instance-1", "instance-2"),
            failure_code="offer_unavailable",
        )

        self._assert_create_claim_blocked(blocker)

    def test_unremediated_400_401_403_block_direct_create_claim(self):
        cases = (
            (
                "400",
                "configuration_rejected",
                {"create_configuration_revision": "typed-env-object-v1"},
            ),
            (
                "401",
                "api_key_rejected",
                {
                    "create_settings_revision": (
                        "11111111-1111-4111-8111-111111111111"
                    )
                },
            ),
            (
                "403",
                "api_key_rejected",
                {
                    "create_settings_revision": (
                        "11111111-1111-4111-8111-111111111111"
                    )
                },
            ),
        )
        original_path = self.database_path
        try:
            for status, failure_code, evidence in cases:
                with self.subTest(status=status):
                    self.database_path = original_path.with_name(
                        "sessions-" + status + ".sqlite3"
                    )
                    blocker = CloudSession.new(
                        "blocker-key-" + status,
                        session_id="blocker-session-" + status,
                        now=90.0,
                        state=SessionState.FAILED,
                    ).transition(
                        SessionState.FAILED,
                        now=91.0,
                        failure_code=failure_code,
                        **evidence,
                    )
                    self._assert_create_claim_blocked(blocker)
        finally:
            self.database_path = original_path

    def test_typed_newer_revision_proof_lifts_only_the_matching_blocker(self):
        sessions = repository.SessionRepository(self.database_path)
        target = make_confirming_session()
        api_remediated = CloudSession.new(
            "api-blocker-key",
            session_id="api-blocker-session",
            now=80.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            now=90.0,
            failure_code="api_key_rejected",
            create_settings_revision=(
                "11111111-1111-4111-8111-111111111111"
            ),
            remediation_verified_at=90.0,
            remediation_revision=(
                "22222222-2222-4222-8222-222222222222"
            ),
        )
        configuration_blocked = CloudSession.new(
            "configuration-blocker-key",
            session_id="configuration-blocker-session",
            now=80.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            now=90.0,
            failure_code="configuration_rejected",
            create_configuration_revision="typed-env-object-v1",
        )
        for candidate in (target, api_remediated, configuration_blocked):
            sessions.create_or_get(candidate)

        with self.assertRaises(repository.PaidRentalConflict):
            sessions.claim_create_intent(
                target.session_id,
                now=110.0,
                provider_token="a" * 64,
                session_secret_hex="b" * 64,
            )

        configuration_blocked = sessions.get(
            configuration_blocked.session_id
        )
        sessions.save(
            configuration_blocked.transition(
                SessionState.FAILED,
                now=111.0,
                remediation_verified_at=111.0,
                remediation_revision="typed-env-object-v2",
            )
        )
        target = sessions.get(target.session_id)
        sessions.save(
            target.transition(SessionState.CONFIRMING, now=112.0)
        )

        claimed = sessions.claim_create_intent(
            target.session_id,
            now=113.0,
            provider_token="c" * 64,
            session_secret_hex="d" * 64,
        )

        self.assertEqual(claimed.state, SessionState.CREATING)
        self.assertEqual(claimed.provider_token, "c" * 64)
        self.assertEqual(claimed.session_secret_hex, "d" * 64)

    def test_concurrent_atomic_create_claims_select_exactly_one_session(self):
        sessions = repository.SessionRepository(self.database_path)
        first = make_confirming_session(
            key="first-claim-key",
            session_id="first-claim-session",
        )
        second = make_confirming_session(
            key="second-claim-key",
            session_id="second-claim-session",
        )
        sessions.create_or_get(first)
        sessions.create_or_get(second)

        def claim(candidate):
            try:
                return sessions.claim_create_intent(
                    candidate.session_id,
                    now=110.0,
                    provider_token=(
                        "a" * 64
                        if candidate is first
                        else "c" * 64
                    ),
                    session_secret_hex=(
                        "b" * 64
                        if candidate is first
                        else "d" * 64
                    ),
                )
            except repository.PaidRentalConflict as error:
                return error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, (first, second)))

        self.assertEqual(
            sum(isinstance(item, CloudSession) for item in results),
            1,
        )
        self.assertEqual(
            sum(
                isinstance(item, repository.PaidRentalConflict)
                for item in results
            ),
            1,
        )
        self.assertEqual(
            {session.state for session in sessions.list_all()},
            {SessionState.OFFER_SELECTED, SessionState.CREATING},
        )

    def test_legacy_attempt_is_migrated_without_losing_billing_identity(self):
        legacy = repository.AttemptRepository(self.database_path)
        attempt, _ = legacy.create_or_get(make_attempt())
        attempt = legacy.transition(
            attempt.attempt_id,
            AttemptState.CREATING,
            now=101.0,
            instance_id="77",
            provider_token="a" * 64,
        )
        legacy.transition(
            attempt.attempt_id,
            AttemptState.STARTING,
            now=102.0,
        )

        sessions = repository.SessionRepository(self.database_path)
        session = sessions.get("attempt-1")

        self.assertEqual(session.state, SessionState.BOOTSTRAPPING)
        self.assertEqual(session.instance_id, "77")
        self.assertEqual(session.label, "comfy-cloud-run-attempt-1")
        self.assertEqual(session.provider_token, "a" * 64)

    def test_session_round_trip_and_optimistic_version_survive_reopen(self):
        sessions = repository.SessionRepository(self.database_path)
        saved, created = sessions.create_or_get(make_session())
        first_reader = sessions.get(saved.session_id)
        stale_reader = sessions.get(saved.session_id)

        updated = sessions.save(
            first_reader.transition(SessionState.OFFER_SELECTED, now=101.0)
        )

        self.assertTrue(created)
        self.assertEqual(updated.version, 2)
        with self.assertRaises(repository.ConcurrentSessionUpdate):
            sessions.save(
                stale_reader.transition(SessionState.FAILED, now=102.0)
            )
        reopened = repository.SessionRepository(self.database_path)
        self.assertEqual(reopened.get("session-1"), updated)
        self.assertEqual(os.stat(self.database_path).st_mode & 0o777, 0o600)
        self.assertEqual(
            os.stat(self.database_path.parent).st_mode & 0o777,
            0o700,
        )

    def test_ready_session_can_be_claimed_by_exactly_one_concurrent_job(self):
        sessions = repository.SessionRepository(self.database_path)
        selected = make_attempt().quote
        ready = CloudSession.new(
            "ready-key",
            session_id="ready-session",
            quote=selected,
            manifest_digest=selected.manifest_digest,
            deadline_at=7300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        )
        sessions.create_or_get(ready)

        def claim(_index):
            try:
                return sessions.transition_if_state(
                    "ready-session",
                    SessionState.READY,
                    SessionState.RUNNING,
                    now=101.0,
                )
            except repository.ConcurrentSessionUpdate:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(claim, range(8)))

        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(
            sessions.get("ready-session").state,
            SessionState.RUNNING,
        )

    def test_destroy_review_is_hashed_version_bound_and_consumed_once(self):
        sessions = repository.SessionRepository(self.database_path)
        saved, _created = sessions.create_or_get(make_session())
        raw_token = "review-token-never-store-raw"
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        sessions.save_destroy_review(
            saved.session_id,
            token_digest=digest,
            expires_at=400.0,
            session_version=saved.version,
            instance_id=saved.instance_id,
            unverified_artifact_ids=("output-2",),
        )
        consumed = sessions.consume_destroy_review(
            saved.session_id,
            token_digest=digest,
            now=200.0,
        )
        repeated = sessions.consume_destroy_review(
            saved.session_id,
            token_digest=digest,
            now=200.0,
        )

        self.assertEqual(
            consumed.unverified_artifact_ids,
            ("output-2",),
        )
        self.assertIsNone(repeated)
        with closing(sessions._connect()) as connection:
            rows = connection.execute(
                "SELECT token_digest FROM destroy_reviews"
            ).fetchall()
        self.assertNotIn(raw_token, repr(rows))

    def test_destroy_review_is_invalidated_by_session_version_change(self):
        sessions = repository.SessionRepository(self.database_path)
        saved, _created = sessions.create_or_get(make_session())
        digest = "f" * 64
        sessions.save_destroy_review(
            saved.session_id,
            token_digest=digest,
            expires_at=400.0,
            session_version=saved.version,
            instance_id=saved.instance_id,
            unverified_artifact_ids=(),
        )
        sessions.save(
            saved.transition(SessionState.OFFER_SELECTED, now=101.0)
        )

        self.assertIsNone(
            sessions.consume_destroy_review(
                saved.session_id,
                token_digest=digest,
                now=200.0,
            )
        )

    def test_destroy_review_expires_at_the_five_minute_boundary(self):
        sessions = repository.SessionRepository(self.database_path)
        saved, _created = sessions.create_or_get(make_session())
        digest = "e" * 64
        sessions.save_destroy_review(
            saved.session_id,
            token_digest=digest,
            expires_at=400.0,
            session_version=saved.version,
            instance_id=saved.instance_id,
            unverified_artifact_ids=(),
        )

        self.assertIsNone(
            sessions.consume_destroy_review(
                saved.session_id,
                token_digest=digest,
                now=400.0,
            )
        )

    def test_pending_deadline_intent_survives_repository_reopen(self):
        sessions = repository.SessionRepository(self.database_path)
        ready = CloudSession.new(
            "deadline-key",
            session_id="deadline-session",
            manifest_digest="a" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        )
        saved, _created = sessions.create_or_get(ready)
        sessions.save(
            saved.transition(
                SessionState.READY,
                now=101.0,
                pending_deadline_at=9_100.0,
                pending_deadline_mode="finite",
                pending_deadline_action="add_30_minutes",
            )
        )

        reopened = repository.SessionRepository(self.database_path)
        pending = reopened.get(ready.session_id)

        self.assertEqual(pending.deadline_at, 7_300.0)
        self.assertEqual(pending.pending_deadline_at, 9_100.0)
        self.assertEqual(
            pending.pending_deadline_action,
            "add_30_minutes",
        )

    def test_create_reconciliation_evidence_survives_reopen(self):
        sessions = repository.SessionRepository(self.database_path)
        creating = make_session().transition(
            SessionState.OFFER_SELECTED,
            now=101.0,
        ).transition(
            SessionState.CONFIRMING,
            now=102.0,
        ).transition(
            SessionState.CREATING,
            now=103.0,
            provider_token="a" * 64,
            session_secret_hex="b" * 64,
        ).transition(
            SessionState.RECONCILING_CREATE,
            now=104.0,
            failure_code="timeout",
            create_reconcile_started_at=104.0,
            create_settings_revision=(
                "11111111-1111-4111-8111-111111111111"
            ),
            create_configuration_revision="typed-env-object-v1",
        )
        for timestamp in (104.0, 119.0, 134.0, 224.0):
            creating = creating.record_create_empty_observation(
                now=timestamp
            )
        saved, _created = sessions.create_or_get(creating)

        reopened = repository.SessionRepository(self.database_path).get(
            saved.session_id
        )

        self.assertEqual(reopened, saved)
        self.assertEqual(reopened.failure_code, "timeout")
        self.assertEqual(reopened.create_empty_observations, 3)
        self.assertEqual(reopened.create_first_empty_at, 104.0)
        self.assertEqual(reopened.create_last_empty_at, 224.0)
        self.assertTrue(reopened.create_absence_verified)

    def test_legacy_database_migration_defaults_reconciliation_evidence(self):
        self.database_path.parent.mkdir(parents=True)
        legacy_quote = make_attempt().quote.to_record()
        legacy_quote.pop("execution_baseline_digest")
        legacy_quote.pop("randomized_seed_node_ids")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_meta VALUES ('schema_version', '5')"
            )
            connection.execute(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    quote_json TEXT,
                    manifest_digest TEXT,
                    installed_manifest_digest TEXT,
                    instance_id TEXT,
                    worker_base_url TEXT,
                    provider_token TEXT,
                    session_secret_hex TEXT,
                    deadline_at REAL,
                    deadline_mode TEXT NOT NULL DEFAULT 'finite',
                    disk_gb INTEGER NOT NULL DEFAULT 80,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    destroy_requested INTEGER NOT NULL DEFAULT 0,
                    residual_inventory_json TEXT,
                    sanitized_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL,
                    pending_deadline_at REAL,
                    pending_deadline_mode TEXT,
                    pending_deadline_action TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO sessions VALUES (
                    ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,
                    ?, 'finite', 80, 0, 0, '[]', NULL, ?, ?, 1,
                    NULL, NULL, NULL
                )
                """,
                (
                    "legacy-session",
                    "legacy-key",
                    "comfy-cloud-run-legacy-session",
                    SessionState.PREFLIGHT.value,
                    json.dumps(legacy_quote),
                    "a" * 64,
                    7300.0,
                    100.0,
                    100.0,
                ),
            )
            connection.commit()

        sessions = repository.SessionRepository(self.database_path)
        migrated = sessions.get("legacy-session")
        with closing(sessions._connect()) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            }

        self.assertEqual(version, "8")
        self.assertEqual(migrated.failure_code, None)
        self.assertEqual(migrated.create_empty_observations, 0)
        self.assertIsNone(migrated.create_first_empty_at)
        self.assertIsNone(migrated.create_last_empty_at)
        self.assertEqual(migrated.execution_baseline_digest, "0" * 64)
        self.assertEqual(migrated.randomized_seed_node_ids, ())
        self.assertTrue(
            {
                "failure_code",
                "create_reconcile_started_at",
                "create_empty_observations",
                "create_first_empty_at",
                "create_last_empty_at",
                "create_settings_revision",
                "create_configuration_revision",
                "remediation_verified_at",
                "remediation_revision",
                "execution_baseline_digest",
                "randomized_seed_node_ids_json",
            }.issubset(columns)
        )

    def test_recoverable_filter_retains_unknown_active_and_orphaned_confirming(self):
        sessions = repository.SessionRepository(self.database_path)
        confirming = CloudSession.new(
            "confirming-key",
            session_id="confirming-session",
            now=100.0,
            state=SessionState.CONFIRMING,
        )
        reconciling = CloudSession.new(
            "reconciling-key",
            session_id="reconciling-session",
            now=101.0,
            state=SessionState.CREATING,
        ).transition(
            SessionState.RECONCILING_CREATE,
            now=102.0,
            create_reconcile_started_at=102.0,
            failure_code="timeout",
        )
        active = CloudSession.new(
            "active-key",
            session_id="active-session",
            now=103.0,
            state=SessionState.READY,
        ).transition(
            SessionState.READY,
            instance_id="77",
        )
        failed_residual = CloudSession.new(
            "residual-key",
            session_id="residual-session",
            now=104.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            residual_inventory=("88",),
        )
        for candidate in (
            confirming,
            reconciling,
            active,
            failed_residual,
        ):
            sessions.create_or_get(candidate)

        self.assertEqual(
            [item.session_id for item in sessions.list_recoverable()],
            [
                "confirming-session",
                "reconciling-session",
                "active-session",
                "residual-session",
            ],
        )

    def test_recoverable_filter_excludes_failed_absent(self):
        sessions = repository.SessionRepository(self.database_path)
        failed = CloudSession.new(
            "failed-key",
            session_id="failed-session",
            now=100.0,
            state=SessionState.FAILED,
        ).transition(
            SessionState.FAILED,
            failure_code="offer_unavailable",
        )
        sessions.create_or_get(failed)

        self.assertEqual(sessions.list_recoverable(), [])

    def test_recent_sessions_query_is_bounded(self):
        sessions = repository.SessionRepository(self.database_path)
        for index in range(25):
            sessions.create_or_get(
                CloudSession.new(
                    "recent-key-" + str(index),
                    session_id="recent-session-" + str(index),
                    now=100.0 + index,
                )
            )

        recent = sessions.list_recent(limit=20)

        self.assertEqual(len(recent), 20)
        self.assertEqual(recent[0].session_id, "recent-session-5")
        self.assertEqual(recent[-1].session_id, "recent-session-24")
        for invalid in (True, 0, 21, 1.0, "20", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    sessions.list_recent(limit=invalid)

    def test_remediation_and_execution_baseline_survive_legacy_migration_and_reopen(self):
        sessions = repository.SessionRepository(self.database_path)
        failed = CloudSession.new(
            "failed-key",
            session_id="failed-session",
            now=100.0,
            state=SessionState.FAILED,
            execution_baseline_digest="e" * 64,
            randomized_seed_node_ids=("3",),
        ).transition(
            SessionState.FAILED,
            now=110.0,
            failure_code="api_key_rejected",
            create_settings_revision=(
                "11111111-1111-4111-8111-111111111111"
            ),
            remediation_verified_at=110.0,
            remediation_revision=(
                "22222222-2222-4222-8222-222222222222"
            ),
        )
        saved, _created = sessions.create_or_get(failed)

        reopened = repository.SessionRepository(self.database_path).get(
            saved.session_id
        )

        self.assertEqual(reopened.execution_baseline_digest, "e" * 64)
        self.assertEqual(reopened.randomized_seed_node_ids, ("3",))
        self.assertEqual(
            reopened.create_settings_revision,
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(reopened.remediation_verified_at, 110.0)
        self.assertEqual(
            reopened.remediation_revision,
            "22222222-2222-4222-8222-222222222222",
        )

    def test_randomized_seed_storage_rejects_non_array_json(self):
        sessions = repository.SessionRepository(self.database_path)
        saved, _created = sessions.create_or_get(make_session())
        with closing(sessions._connect()) as connection:
            connection.execute(
                """
                UPDATE sessions
                SET randomized_seed_node_ids_json = ?
                WHERE session_id = ?
                """,
                ('{"3":true}', saved.session_id),
            )
            connection.commit()

        with self.assertRaises(ValueError):
            repository.SessionRepository(self.database_path).get(
                saved.session_id
            )


if __name__ == "__main__":
    unittest.main()

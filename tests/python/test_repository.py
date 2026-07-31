import concurrent.futures
import hashlib
import os
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

    def test_legacy_attempt_is_migrated_without_losing_billing_identity(self):
        legacy = repository.AttemptRepository(self.database_path)
        attempt, _ = legacy.create_or_get(make_attempt())
        attempt = legacy.transition(
            attempt.attempt_id,
            AttemptState.CREATING,
            now=101.0,
            instance_id="77",
            provider_token="private-boundary-token",
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
        self.assertEqual(session.provider_token, "private-boundary-token")

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
        with sessions._connect() as connection:
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


if __name__ == "__main__":
    unittest.main()

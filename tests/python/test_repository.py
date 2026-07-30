import concurrent.futures
import os
import tempfile
import unittest
from pathlib import Path

from cloud_run.models import AttemptState, CloudAttempt, OfferQuote
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
            machine_id="machine-7",
            host_id="host-3",
            public_ipaddr="203.0.113.7",
        ),
        attempt_id=attempt_id,
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


if __name__ == "__main__":
    unittest.main()

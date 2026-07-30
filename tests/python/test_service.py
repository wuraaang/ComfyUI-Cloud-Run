import asyncio
import tempfile
import unittest
from pathlib import Path

from cloud_run.models import AttemptState
from cloud_run.repository import AttemptRepository
from cloud_run.vast import VastError


def offer(offer_id=42, *, price=0.42, gpu_name="RTX 4090"):
    return {
        "offer_id": offer_id,
        "gpu_name": gpu_name,
        "gpu_ram_gb": 24.0,
        "dph_total": price,
        "reliability": 0.99,
        "machine_id": "machine-7",
        "host_id": "host-3",
        "public_ipaddr": "203.0.113.7",
        "inet_down_mbps": 500.0,
        "disk_bw_mbps": 600.0,
    }


class FakeSettings:
    def load(self):
        return {
            "api_key": "synthetic-value",
            "max_price_per_hour": 0.55,
            "min_vram_gb": 24,
        }


class FakeProvider:
    def __init__(self, searches=None):
        self.searches = list(searches or [[offer()]])
        self.search_calls = []
        self.create_calls = []
        self.inventory_calls = []
        self.instances = []
        self.create_result = "instance-9"
        self.create_error = None
        self.on_create = None

    async def search_offers(
        self,
        api_key,
        *,
        max_price_per_hour,
        min_vram_gb,
    ):
        self.search_calls.append(
            (api_key, max_price_per_hour, min_vram_gb)
        )
        if len(self.searches) > 1:
            return self.searches.pop(0)
        return list(self.searches[0])

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
    ):
        self.create_calls.append(
            {
                "api_key": api_key,
                "offer_id": offer_id,
                "disk_gb": disk_gb,
                "label": label,
            }
        )
        if self.on_create is not None:
            self.on_create(label)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    async def list_instances(self, api_key):
        self.inventory_calls.append(api_key)
        return list(self.instances)


class CloudRunServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "attempts.sqlite3"
        )
        self.repository = AttemptRepository(self.database_path)

    def service(self, provider, now=100.0):
        from cloud_run.service import CloudRunService

        return CloudRunService(
            FakeSettings(),
            self.repository,
            provider=provider,
            clock=lambda: now,
            quote_ttl_seconds=60,
        )

    def test_search_selection_and_quote_preview_never_create_a_rental(self):
        provider = FakeProvider()
        service = self.service(provider)

        searched = asyncio.run(service.search())
        preview = asyncio.run(
            service.preview_offer(
                offer_id="42",
                idempotency_key="idem-preview-1",
            )
        )
        reopened = AttemptRepository(self.database_path).get(
            preview.attempt_id
        )

        self.assertEqual([item["offer_id"] for item in searched], [42])
        self.assertEqual(preview.state, AttemptState.OFFER_SELECTED)
        self.assertEqual(reopened.quote.offer_id, "42")
        self.assertEqual(reopened.quote.gpu_name, "RTX 4090")
        self.assertEqual(reopened.quote.gpu_ram_gb, 24.0)
        self.assertEqual(reopened.quote.dph_total, 0.42)
        self.assertEqual(reopened.quote.max_price_per_hour, 0.55)
        self.assertEqual(reopened.quote.expires_at, 160.0)
        self.assertEqual(provider.create_calls, [])

    def test_duplicate_preview_idempotency_key_returns_original_attempt(self):
        provider = FakeProvider()
        service = self.service(provider)

        first = asyncio.run(
            service.preview_offer(
                offer_id="42",
                idempotency_key="same-browser-attempt",
            )
        )
        duplicate = asyncio.run(
            service.preview_offer(
                offer_id="999",
                idempotency_key="same-browser-attempt",
            )
        )

        self.assertEqual(duplicate, first)
        self.assertEqual(len(self.repository.list_all()), 1)
        self.assertEqual(provider.create_calls, [])

    def test_confirmation_revalidates_same_offer_and_never_accepts_price_rise(self):
        from cloud_run.service import QuoteUnavailable

        provider = FakeProvider(searches=[[offer()], [offer(price=0.43)]])
        service = self.service(provider)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-price-check",
            )
        )

        with self.assertRaises(QuoteUnavailable):
            asyncio.run(
                service.confirm(
                    preview.attempt_id,
                    idempotency_key="idem-price-check",
                )
            )

        saved = self.repository.get(preview.attempt_id)
        self.assertEqual(saved.state, AttemptState.FAILED)
        self.assertEqual(provider.create_calls, [])
        self.assertNotIn("0.43", saved.sanitized_error)

    def test_attempt_and_creating_state_exist_before_the_only_paid_call(self):
        provider = FakeProvider(searches=[[offer()], [offer()]])
        service = self.service(provider)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-create",
            )
        )

        def assert_persisted_before_create(label):
            persisted = self.repository.get(preview.attempt_id)
            self.assertEqual(persisted.label, label)
            self.assertEqual(persisted.state, AttemptState.CREATING)
            self.assertEqual(persisted.idempotency_key, "idem-create")

        provider.on_create = assert_persisted_before_create
        started = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-create",
            )
        )
        duplicate = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-create",
            )
        )

        self.assertEqual(started.state, AttemptState.STARTING)
        self.assertEqual(started.instance_id, "instance-9")
        self.assertEqual(duplicate.attempt_id, started.attempt_id)
        self.assertEqual(len(provider.create_calls), 1)
        self.assertNotIn("synthetic-value", repr(started.public_payload()))

    def test_ambiguous_create_is_reconciled_by_unique_label_without_retry(self):
        provider = FakeProvider(searches=[[offer()], [offer()]])
        provider.create_error = VastError(
            "sensitive provider timeout",
            retryable=True,
        )
        service = self.service(provider)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-ambiguous",
            )
        )
        provider.instances = [
            {
                "instance_id": "instance-77",
                "label": preview.label,
                "actual_status": "loading",
            }
        ]

        reconciled = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-ambiguous",
            )
        )
        retried_request = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-ambiguous",
            )
        )

        self.assertEqual(reconciled.state, AttemptState.STARTING)
        self.assertEqual(reconciled.instance_id, "instance-77")
        self.assertEqual(retried_request.instance_id, "instance-77")
        self.assertEqual(len(provider.create_calls), 1)
        self.assertEqual(len(provider.inventory_calls), 1)
        self.assertNotIn("sensitive", repr(reconciled.public_payload()))

    def test_unknown_create_outcome_stays_recoverable_and_is_never_reissued(self):
        provider = FakeProvider(searches=[[offer()], [offer()]])
        provider.create_error = VastError(
            "sensitive provider timeout",
            retryable=True,
        )
        service = self.service(provider)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-unknown",
            )
        )

        unknown = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-unknown",
            )
        )
        duplicate = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-unknown",
            )
        )

        self.assertEqual(unknown.state, AttemptState.CREATING)
        self.assertEqual(duplicate.state, AttemptState.CREATING)
        self.assertEqual(len(provider.create_calls), 1)
        self.assertIn("inventory", unknown.sanitized_error.lower())
        self.assertNotIn("sensitive", unknown.sanitized_error)

    def test_confirmation_rejects_expired_quote_and_wrong_idempotency_key(self):
        from cloud_run.service import CloudRunValidationError, QuoteUnavailable

        provider = FakeProvider()
        service = self.service(provider, now=100.0)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-expiry",
            )
        )
        expired_service = self.service(provider, now=161.0)

        with self.assertRaises(CloudRunValidationError):
            asyncio.run(
                expired_service.confirm(
                    preview.attempt_id,
                    idempotency_key="different-key",
                )
            )
        with self.assertRaises(QuoteUnavailable):
            asyncio.run(
                expired_service.confirm(
                    preview.attempt_id,
                    idempotency_key="idem-expiry",
                )
            )
        self.assertEqual(provider.create_calls, [])


if __name__ == "__main__":
    unittest.main()

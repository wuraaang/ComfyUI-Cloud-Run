import asyncio
import tempfile
import types
import unittest
from pathlib import Path

from cloud_run.models import AttemptState
from cloud_run.repository import AttemptRepository, SessionRepository
from cloud_run.vast import VastError
from cloud_run.worker_release import WorkerRelease


_DEFAULT_RELEASE = object()


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
        "inet_down_cost": 0.01,
        "inet_up_cost": 0.02,
    }


def worker_release():
    return WorkerRelease.from_payload(
        {
            "schema_version": 1,
            "template_hash_id": "1" * 32,
            "worker_commit": "a" * 40,
            "worker_archive_sha256": "b" * 64,
            "protocol_version": "1",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "python_version": "3.13.12",
            "worker_port": 8765,
        }
    )


class FakePreflightService:
    def __init__(self):
        self.calls = []
        self.result = types.SimpleNamespace(
            preflight_id="preflight-1",
            rentable=True,
            manifest_digest="c" * 64,
            transfer_bytes=12_000,
            output_allowance_bytes=4_000,
            disk_gb=96,
        )

    def require_rentable_preflight(self, preflight_id):
        self.calls.append(preflight_id)
        return self.result


class FakeSettings:
    def load(self):
        return {
            "api_key": "synthetic-value",
            "max_price_per_hour": 0.55,
            "min_vram_gb": 24,
        }


class FakeProvider:
    def __init__(self, searches=None, lookups=None):
        self.searches = list(searches or [[offer()]])
        self.lookups = list([offer()] if lookups is None else lookups)
        self.search_calls = []
        self.lookup_calls = []
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
        disk_gb,
    ):
        self.search_calls.append(
            (api_key, max_price_per_hour, min_vram_gb, disk_gb)
        )
        if len(self.searches) > 1:
            return self.searches.pop(0)
        return list(self.searches[0])

    async def get_offer(
        self,
        api_key,
        offer_id,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        self.lookup_calls.append(
            (
                api_key,
                str(offer_id),
                max_price_per_hour,
                min_vram_gb,
                disk_gb,
            )
        )
        if not self.lookups:
            return None
        if len(self.lookups) > 1:
            return self.lookups.pop(0)
        return self.lookups[0]

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
        release,
    ):
        self.create_calls.append(
            {
                "api_key": api_key,
                "offer_id": offer_id,
                "disk_gb": disk_gb,
                "label": label,
                "release": release,
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
        self.session_repository = SessionRepository(self.database_path)

    def service(
        self,
        provider,
        now=100.0,
        *,
        release=_DEFAULT_RELEASE,
        session_service=None,
        lifecycle=None,
    ):
        from cloud_run.service import CloudRunService

        return CloudRunService(
            FakeSettings(),
            self.repository,
            provider=provider,
            clock=lambda: now,
            quote_ttl_seconds=60,
            session_repository=self.session_repository,
            release=(
                worker_release()
                if release is _DEFAULT_RELEASE
                else release
            ),
            session_service=session_service,
            lifecycle=lifecycle,
        )

    def test_paid_session_quote_is_complete_read_only_and_durable(self):
        provider = FakeProvider(lookups=[offer(price=0.50)])
        preflights = FakePreflightService()
        service = self.service(
            provider,
            session_service=preflights,
        )

        quoted = asyncio.run(
            service.preview_session(
                preflight_id="preflight-1",
                offer_id="42",
                idempotency_key="session-idem-1",
                deadline={
                    "mode": "finite",
                    "duration_seconds": 7_200,
                },
            )
        )
        reopened = SessionRepository(self.database_path).get(
            quoted.session_id
        )

        self.assertEqual(quoted.state.value, "offer_selected")
        self.assertEqual(reopened.quote.disk_gb, 96)
        self.assertEqual(reopened.quote.transfer_bytes, 12_000)
        self.assertEqual(reopened.quote.output_allowance_bytes, 4_000)
        self.assertEqual(reopened.quote.duration_seconds, 7_200)
        self.assertEqual(
            reopened.quote.approximate_max_active_charge,
            1.0,
        )
        self.assertEqual(
            reopened.quote.template_hash_id,
            "1" * 32,
        )
        self.assertEqual(reopened.quote.worker_commit, "a" * 40)
        self.assertEqual(reopened.deadline_at, 7_300.0)
        self.assertEqual(provider.lookup_calls[0][-1], 96)
        self.assertEqual(provider.create_calls, [])
        self.assertIsNone(reopened.session_secret_hex)
        self.assertNotIn(
            "session_secret_hex",
            repr(quoted.public_payload()),
        )

    def test_paid_confirmation_persists_secret_and_intent_before_one_put(self):
        provider = FakeProvider(
            lookups=[offer(price=0.50), offer(price=0.50)]
        )
        service = self.service(
            provider,
            session_service=FakePreflightService(),
        )
        quoted = asyncio.run(
            service.preview_session(
                preflight_id="preflight-1",
                offer_id=42,
                idempotency_key="session-idem-create",
                deadline={
                    "mode": "finite",
                    "duration_seconds": 7_200,
                },
            )
        )

        def assert_persisted_before_create(label):
            persisted = self.session_repository.get(quoted.session_id)
            self.assertEqual(persisted.label, label)
            self.assertEqual(persisted.state.value, "creating")
            self.assertEqual(
                persisted.idempotency_key,
                "session-idem-create",
            )
            self.assertEqual(persisted.manifest_digest, "c" * 64)
            self.assertEqual(len(persisted.session_secret_hex), 64)

        provider.on_create = assert_persisted_before_create
        started = asyncio.run(
            service.confirm_session(
                quoted.session_id,
                idempotency_key="session-idem-create",
            )
        )
        duplicate = asyncio.run(
            service.confirm_session(
                quoted.session_id,
                idempotency_key="session-idem-create",
            )
        )

        self.assertEqual(started.state.value, "bootstrapping")
        self.assertEqual(started.instance_id, "instance-9")
        self.assertEqual(duplicate.session_id, started.session_id)
        self.assertEqual(len(provider.create_calls), 1)
        self.assertIs(
            provider.create_calls[0]["release"],
            service.release,
        )

    def test_paid_confirmation_starts_the_session_boot_watchdog(self):
        class WatchdogLifecycle:
            def __init__(self):
                self.calls = []

            def schedule_session_watchdog(self, session_id):
                self.calls.append(session_id)

        provider = FakeProvider(
            lookups=[offer(price=0.50), offer(price=0.50)]
        )
        lifecycle = WatchdogLifecycle()
        service = self.service(
            provider,
            session_service=FakePreflightService(),
            lifecycle=lifecycle,
        )
        quoted = asyncio.run(
            service.preview_session(
                preflight_id="preflight-1",
                offer_id=42,
                idempotency_key="session-watchdog",
                deadline={
                    "mode": "finite",
                    "duration_seconds": 7_200,
                },
            )
        )

        started = asyncio.run(
            service.confirm_session(
                quoted.session_id,
                idempotency_key="session-watchdog",
            )
        )

        self.assertEqual(started.state.value, "bootstrapping")
        self.assertEqual(lifecycle.calls, [started.session_id])

    def test_concurrent_paid_confirmations_issue_exactly_one_create(self):
        async def scenario():
            provider = FakeProvider(
                lookups=[
                    offer(price=0.50),
                    offer(price=0.50),
                    offer(price=0.50),
                ]
            )
            service = self.service(
                provider,
                session_service=FakePreflightService(),
            )
            quoted = await service.preview_session(
                preflight_id="preflight-1",
                offer_id=42,
                idempotency_key="session-idem-concurrent",
                deadline={
                    "mode": "finite",
                    "duration_seconds": 7_200,
                },
            )
            original_lookup = provider.get_offer
            both_revalidating = asyncio.Event()
            revalidation_count = 0

            async def synchronized_lookup(*args, **kwargs):
                nonlocal revalidation_count
                revalidation_count += 1
                if revalidation_count == 2:
                    both_revalidating.set()
                await both_revalidating.wait()
                return await original_lookup(*args, **kwargs)

            provider.get_offer = synchronized_lookup
            first, second = await asyncio.gather(
                service.confirm_session(
                    quoted.session_id,
                    idempotency_key="session-idem-concurrent",
                ),
                service.confirm_session(
                    quoted.session_id,
                    idempotency_key="session-idem-concurrent",
                ),
            )
            return first, second, provider

        first, second, provider = asyncio.run(scenario())

        self.assertEqual(first.state.value, "bootstrapping")
        self.assertEqual(second.state.value, "bootstrapping")
        self.assertEqual(len(provider.create_calls), 1)

    def test_missing_release_blocks_paid_quote_before_provider_request(self):
        provider = FakeProvider(lookups=[offer(price=0.50)])
        service = self.service(
            provider,
            release=None,
            session_service=FakePreflightService(),
        )
        from cloud_run.worker_release import WorkerReleaseUnavailable

        with self.assertRaises(WorkerReleaseUnavailable):
            asyncio.run(
                service.preview_session(
                    preflight_id="preflight-1",
                    offer_id="42",
                    idempotency_key="session-idem-no-release",
                    deadline={
                        "mode": "finite",
                        "duration_seconds": 7_200,
                    },
                )
            )

        self.assertEqual(provider.lookup_calls, [])
        self.assertEqual(provider.create_calls, [])

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

    def test_preview_uses_exact_lookup_when_offer_is_absent_from_broad_results(self):
        provider = FakeProvider(searches=[[]], lookups=[offer()])
        service = self.service(provider)

        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-targeted-preview",
            )
        )

        self.assertEqual(preview.quote.offer_id, "42")
        self.assertEqual(provider.search_calls, [])
        self.assertEqual(len(provider.lookup_calls), 1)
        self.assertEqual(provider.lookup_calls[0][1], "42")
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

        provider = FakeProvider(
            searches=[[]],
            lookups=[offer(), offer(price=0.43)],
        )
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
        self.assertEqual(len(provider.lookup_calls), 2)
        self.assertEqual(provider.search_calls, [])

    def test_confirmation_rejects_quote_identity_changes_without_create(self):
        from cloud_run.service import QuoteUnavailable

        changed_offers = [
            ("gpu-name", {**offer(), "gpu_name": "RTX 4080"}),
            ("vram", {**offer(), "gpu_ram_gb": 16.0}),
            ("canonical-id", {**offer(), "offer_id": 99}),
        ]
        for case_name, changed_offer in changed_offers:
            with self.subTest(case=case_name):
                provider = FakeProvider(
                    lookups=[offer(), changed_offer],
                )
                service = self.service(provider)
                idempotency_key = "idem-changed-" + case_name
                preview = asyncio.run(
                    service.preview_offer(
                        offer_id=42,
                        idempotency_key=idempotency_key,
                    )
                )

                with self.assertRaises(QuoteUnavailable):
                    asyncio.run(
                        service.confirm(
                            preview.attempt_id,
                            idempotency_key=idempotency_key,
                        )
                    )

                self.assertEqual(len(provider.lookup_calls), 2)
                self.assertEqual(provider.create_calls, [])

    def test_attempt_and_creating_state_exist_before_the_only_paid_call(self):
        provider = FakeProvider(lookups=[offer(), offer()])
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
        provider = FakeProvider(lookups=[offer(), offer()])
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
        provider = FakeProvider(lookups=[offer(), offer()])
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

    def test_known_sanitized_create_refusal_is_preserved_for_the_user(self):
        provider = FakeProvider(lookups=[offer(), offer()])
        provider.create_error = VastError(
            "Vast API key cannot create instances.",
            status=403,
        )
        service = self.service(provider)
        preview = asyncio.run(
            service.preview_offer(
                offer_id=42,
                idempotency_key="idem-known-refusal",
            )
        )

        failed = asyncio.run(
            service.confirm(
                preview.attempt_id,
                idempotency_key="idem-known-refusal",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(
            failed.sanitized_error,
            "Vast API key cannot create instances.",
        )

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

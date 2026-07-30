import asyncio
import tempfile
import unittest
from pathlib import Path

from cloud_run.models import AttemptState, CloudAttempt, OfferQuote
from cloud_run.offers import HostBlacklist
from cloud_run.repository import AttemptRepository


def quote(
    *,
    offer_id="42",
    gpu_name="RTX 4090",
    price=0.42,
    machine_id="machine-7",
    host_id="host-3",
    public_ipaddr="8.8.8.8",
):
    return OfferQuote(
        offer_id=offer_id,
        gpu_name=gpu_name,
        gpu_ram_gb=24.0,
        dph_total=price,
        reliability=0.99,
        max_price_per_hour=0.55,
        expires_at=1000.0,
        machine_id=machine_id,
        host_id=host_id,
        public_ipaddr=public_ipaddr,
    )


def provider_instance(
    instance_id,
    label,
    *,
    status="loading",
    address="8.8.8.8",
    host_port="32100",
):
    return {
        "instance_id": instance_id,
        "label": label,
        "actual_status": status,
        "public_ipaddr": address,
        "ports": {"8188/tcp": [{"HostPort": host_port}]},
        "status_msg": None,
    }


class FakeSettings:
    def load(self):
        return {
            "api_key": "synthetic-value",
            "max_price_per_hour": 0.55,
            "min_vram_gb": 24,
        }


class FakeClock:
    def __init__(self, now=100.0):
        self.now = float(now)
        self.sleeps = []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


class FakeProvider:
    def __init__(self):
        self.instances = []
        self.search_results = []
        self.create_result = "instance-2"
        self.destroy_success = True
        self.destroy_removes = True
        self.list_error = None
        self.get_error = None
        self.calls = []

    async def search_offers(
        self,
        api_key,
        *,
        max_price_per_hour,
        min_vram_gb,
    ):
        self.calls.append(("search", api_key, max_price_per_hour, min_vram_gb))
        return list(self.search_results)

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
    ):
        self.calls.append(("create", offer_id, disk_gb, label, api_key))
        return self.create_result

    async def list_instances(self, api_key):
        self.calls.append(("list", api_key))
        if self.list_error is not None:
            raise self.list_error
        return [dict(instance) for instance in self.instances]

    async def get_instance(self, api_key, instance_id):
        self.calls.append(("get", instance_id, api_key))
        if self.get_error is not None:
            raise self.get_error
        return next(
            (
                dict(instance)
                for instance in self.instances
                if str(instance.get("instance_id")) == str(instance_id)
            ),
            None,
        )

    async def destroy_instance(self, api_key, instance_id):
        self.calls.append(("destroy", str(instance_id), api_key))
        if self.destroy_success and self.destroy_removes:
            self.instances = [
                instance
                for instance in self.instances
                if str(instance.get("instance_id")) != str(instance_id)
            ]
        return self.destroy_success


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.data_directory = Path(self.temporary_directory.name) / "private"
        self.repository = AttemptRepository(
            self.data_directory / "attempts.sqlite3"
        )
        self.blacklist = HostBlacklist(
            self.data_directory / "host-blacklist.json"
        )
        self.provider = FakeProvider()
        self.clock = FakeClock()
        self.probe_result = True
        self.probe_urls = []

    async def probe(self, url):
        self.probe_urls.append(url)
        return self.probe_result

    def lifecycle(self, **options):
        from cloud_run.lifecycle import CloudRunLifecycle

        return CloudRunLifecycle(
            FakeSettings(),
            self.repository,
            provider=self.provider,
            blacklist=self.blacklist,
            clock=self.clock,
            sleep=self.clock.sleep,
            readiness_probe=self.probe,
            poll_interval_seconds=options.get("poll_interval_seconds", 1),
            boot_deadline_seconds=options.get("boot_deadline_seconds", 3),
        )

    def save_attempt(
        self,
        state,
        *,
        attempt_id="attempt-1",
        instance_id=None,
        retry_count=0,
        cancel_requested=False,
        selected_quote=None,
    ):
        attempt = CloudAttempt.new(
            idempotency_key="idem-" + attempt_id,
            attempt_id=attempt_id,
            quote=selected_quote or quote(),
            state=state,
            now=self.clock(),
        )
        if instance_id is not None or retry_count or cancel_requested:
            attempt = attempt.transition(
                state,
                now=self.clock(),
                instance_id=instance_id,
                retry_count=retry_count,
                cancel_requested=cancel_requested,
            )
        return self.repository.create_or_get(attempt)[0]


class CancellationAndReadinessTests(LifecycleTestCase):
    def test_cancel_before_create_rents_nothing(self):
        attempt = self.save_attempt(AttemptState.OFFER_SELECTED)

        cancelled = asyncio.run(self.lifecycle().cancel(attempt.attempt_id))

        self.assertEqual(cancelled.state, AttemptState.CANCELLED)
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "destroy"],
            [],
        )

    def test_cancel_during_unknown_create_discovers_label_then_destroys(self):
        attempt = self.save_attempt(AttemptState.CREATING)
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]

        cancelled = asyncio.run(self.lifecycle().cancel(attempt.attempt_id))

        self.assertEqual(cancelled.state, AttemptState.CANCELLED)
        self.assertIsNone(cancelled.instance_id)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list", "destroy", "list"],
        )

    def test_destroy_during_unknown_create_becomes_a_pending_cancellation(self):
        attempt = self.save_attempt(AttemptState.CREATING)

        pending = asyncio.run(
            self.lifecycle().destroy(attempt.attempt_id)
        )

        self.assertEqual(pending.state, AttemptState.CANCEL_REQUESTED)
        self.assertTrue(pending.cancel_requested)
        self.assertIn("pending", pending.sanitized_error.lower())
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_destroy_never_claims_success_when_inventory_is_unavailable(self):
        attempt = self.save_attempt(AttemptState.FAILED)
        self.provider.list_error = RuntimeError("synthetic inventory outage")

        failed = asyncio.run(
            self.lifecycle().destroy(attempt.attempt_id)
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertIn("could not be verified", failed.sanitized_error.lower())

    def test_cancel_requested_while_create_is_in_flight_is_finished_by_creator(self):
        from cloud_run.service import CloudRunService

        async def scenario():
            self.provider.search_results = [
                {
                    "offer_id": 42,
                    "gpu_name": "RTX 4090",
                    "gpu_ram_gb": 24.0,
                    "dph_total": 0.42,
                    "reliability": 0.99,
                    "machine_id": "machine-7",
                    "host_id": "host-3",
                    "public_ipaddr": "8.8.8.8",
                }
            ]
            lifecycle = self.lifecycle()
            service = CloudRunService(
                FakeSettings(),
                self.repository,
                provider=self.provider,
                blacklist=self.blacklist,
                clock=self.clock,
                lifecycle=lifecycle,
            )
            preview = await service.preview_offer(
                offer_id=42,
                idempotency_key="idem-concurrent-cancel",
            )
            create_started = asyncio.Event()
            release_create = asyncio.Event()

            async def blocking_create(
                api_key,
                *,
                offer_id,
                disk_gb,
                label,
            ):
                self.provider.calls.append(
                    ("create", offer_id, disk_gb, label, api_key)
                )
                create_started.set()
                await release_create.wait()
                return "instance-1"

            self.provider.create_instance = blocking_create
            confirmation = asyncio.create_task(
                service.confirm(
                    preview.attempt_id,
                    idempotency_key="idem-concurrent-cancel",
                )
            )
            await create_started.wait()
            pending = await lifecycle.cancel(preview.attempt_id)
            self.assertEqual(pending.state, AttemptState.CANCEL_REQUESTED)

            self.provider.instances = [
                provider_instance("instance-1", preview.label)
            ]
            release_create.set()
            return await confirmation

        cancelled = asyncio.run(scenario())

        self.assertEqual(cancelled.state, AttemptState.CANCELLED)
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "destroy"]),
            1,
        )

    def test_cancel_while_starting_destroys_and_verifies_inventory_absence(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]

        cancelled = asyncio.run(self.lifecycle().cancel(attempt.attempt_id))

        self.assertEqual(cancelled.state, AttemptState.CANCELLED)
        self.assertNotIn(
            "instance-1",
            [item["instance_id"] for item in self.provider.instances],
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_destroy_failure_never_claims_success_and_exposes_emergency_action(self):
        attempt = self.save_attempt(
            AttemptState.READY,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label, status="running")
        ]
        self.provider.destroy_success = False

        failed = asyncio.run(self.lifecycle().destroy(attempt.attempt_id))
        payload = failed.public_payload()

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertTrue(payload["billing_may_continue"])
        self.assertIn("instance-1", payload["emergency_action"])
        self.assertIn("Vast", payload["emergency_action"])

    def test_destroy_response_is_not_success_until_inventory_is_empty(self):
        attempt = self.save_attempt(
            AttemptState.READY,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label, status="running")
        ]
        self.provider.destroy_removes = False

        failed = asyncio.run(self.lifecycle().destroy(attempt.attempt_id))

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertIn("still", failed.sanitized_error.lower())

    def test_ready_url_is_exposed_only_after_valid_mapping_and_health_probe(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label, status="running")
        ]

        ready = asyncio.run(
            self.lifecycle().reconcile_once(attempt.attempt_id)
        )

        self.assertEqual(ready.state, AttemptState.READY)
        self.assertEqual(ready.ready_url, "http://8.8.8.8:32100")
        self.assertEqual(self.probe_urls, ["http://8.8.8.8:32100"])

    def test_private_or_missing_mapping_never_exposes_open_comfyui(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance(
                "instance-1",
                attempt.label,
                status="running",
                address="127.0.0.1",
            )
        ]

        starting = asyncio.run(
            self.lifecycle().reconcile_once(attempt.attempt_id)
        )

        self.assertEqual(starting.state, AttemptState.STARTING)
        self.assertIsNone(starting.ready_url)
        self.assertEqual(self.probe_urls, [])

    def test_boot_polling_uses_bounded_intervals_and_deadline(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            retry_count=1,
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label, status="loading")
        ]

        finished = asyncio.run(
            self.lifecycle(
                poll_interval_seconds=1,
                boot_deadline_seconds=3,
            ).wait_until_ready(attempt.attempt_id)
        )

        self.assertEqual(finished.state, AttemptState.FAILED)
        self.assertLessEqual(len(self.clock.sleeps), 3)
        self.assertTrue(all(delay == 1 for delay in self.clock.sleeps))
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )


class RecoveryAndReplacementTests(LifecycleTestCase):
    def test_restart_reconciles_every_nonterminal_state_without_blind_create(self):
        states = (
            AttemptState.IDLE,
            AttemptState.SEARCHING,
            AttemptState.OFFER_SELECTED,
            AttemptState.CONFIRMING,
            AttemptState.CREATING,
            AttemptState.STARTING,
            AttemptState.CANCEL_REQUESTED,
            AttemptState.DESTROYING,
            AttemptState.RETRYING,
        )
        for index, state in enumerate(states):
            instance_id = None
            cancel_requested = False
            if state in {
                AttemptState.STARTING,
                AttemptState.CANCEL_REQUESTED,
                AttemptState.DESTROYING,
            }:
                instance_id = "instance-" + str(index)
                cancel_requested = state in {
                    AttemptState.CANCEL_REQUESTED,
                    AttemptState.DESTROYING,
                }
            saved = self.save_attempt(
                state,
                attempt_id="attempt-" + str(index),
                instance_id=instance_id,
                cancel_requested=cancel_requested,
            )
            if instance_id is not None:
                self.provider.instances.append(
                    provider_instance(instance_id, saved.label)
                )
        self.provider.instances.append(
            provider_instance("unrelated", "someone-elses-instance")
        )

        recovered = asyncio.run(self.lifecycle().recover())

        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )
        self.assertIn(
            "unrelated",
            [item["instance_id"] for item in self.provider.instances],
        )
        self.assertEqual(
            {attempt.attempt_id for attempt in recovered},
            {"attempt-" + str(index) for index in range(len(states))},
        )

    def test_transient_failure_replaces_once_after_verified_destroy_and_blacklist(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 42,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.42,
                "reliability": 0.99,
                "machine_id": "machine-7",
                "host_id": "host-3",
                "public_ipaddr": "8.8.8.8",
            },
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
            },
        ]

        replacement = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(replacement.state, AttemptState.STARTING)
        self.assertEqual(replacement.retry_count, 1)
        self.assertEqual(replacement.instance_id, "instance-2")
        self.assertEqual(replacement.quote.offer_id, "43")
        actions = [call[0] for call in self.provider.calls]
        self.assertLess(actions.index("destroy"), actions.index("search"))
        self.assertLess(actions.index("list"), actions.index("create"))
        self.assertTrue(
            self.blacklist.contains(
                {
                    "machine_id": "machine-7",
                    "host_id": "host-3",
                    "public_ipaddr": "8.8.8.8",
                },
                now=self.clock(),
            )
        )

        self.provider.instances = [
            provider_instance("instance-2", replacement.label)
        ]
        second_failure = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="healthcheck_failure",
            )
        )
        self.assertEqual(second_failure.state, AttemptState.FAILED)
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_no_replacement_when_destruction_cannot_be_verified(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.destroy_removes = False
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.99,
            }
        ]

        failed = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="transport_failure",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_auth_quota_budget_validation_and_configuration_never_retry(self):
        for failure_code in (
            "auth",
            "quota",
            "budget",
            "validation",
            "configuration",
        ):
            with self.subTest(failure_code=failure_code):
                self.provider.calls.clear()
                attempt = self.save_attempt(
                    AttemptState.STARTING,
                    attempt_id="attempt-" + failure_code,
                    instance_id="instance-" + failure_code,
                )
                self.provider.instances = [
                    provider_instance(
                        "instance-" + failure_code,
                        attempt.label,
                    )
                ]

                failed = asyncio.run(
                    self.lifecycle().handle_start_failure(
                        attempt.attempt_id,
                        failure_code=failure_code,
                    )
                )

                self.assertEqual(failed.state, AttemptState.FAILED)
                self.assertEqual(
                    [
                        call
                        for call in self.provider.calls
                        if call[0] == "create"
                    ],
                    [],
                )


if __name__ == "__main__":
    unittest.main()

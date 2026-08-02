import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cloud_run.models import (
    AttemptState,
    CloudAttempt,
    CloudSession,
    OfferQuote,
    SessionState,
)
from cloud_run.offers import HostBlacklist
from cloud_run.repository import AttemptRepository, SessionRepository
from cloud_run.session_service import TerminalProvisioningError
from cloud_run.vast import VastError
from cloud_run.worker_release import WorkerRelease


def worker_release(
    *,
    template_character="1",
    commit_character="a",
    archive_character="b",
):
    return WorkerRelease.from_payload(
        {
            "schema_version": 1,
            "template_hash_id": template_character * 32,
            "worker_commit": commit_character * 40,
            "worker_archive_sha256": archive_character * 64,
            "protocol_version": "1",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "python_version": "3.12",
            "worker_port": 8765,
        }
    )


def quote(
    *,
    offer_id="42",
    gpu_name="RTX 4090",
    price=0.42,
    machine_id="machine-7",
    host_id="host-3",
    public_ipaddr="8.8.8.8",
    max_instance_creates=1,
):
    return OfferQuote(
        offer_id=offer_id,
        gpu_name=gpu_name,
        gpu_ram_gb=24.0,
        dph_total=price,
        reliability=0.99,
        max_price_per_hour=0.55,
        expires_at=1000.0,
        disk_gb=80,
        transfer_bytes=0,
        output_allowance_bytes=1,
        inet_down_cost=None,
        inet_up_cost=None,
        duration_seconds=7200,
        deadline_mode="finite",
        approximate_max_active_charge=price * 2,
        template_hash_id="1" * 32,
        worker_commit="a" * 40,
        worker_archive_sha256="b" * 64,
        protocol_version="1",
        manifest_digest="c" * 64,
        machine_id=machine_id,
        host_id=host_id,
        public_ipaddr=public_ipaddr,
        max_instance_creates=max_instance_creates,
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


class PrivateBoundaryContext:
    __slots__ = ("boundary_token", "session_id")

    def __init__(self, boundary_token, session_id):
        self.boundary_token = boundary_token
        self.session_id = session_id

    def __repr__(self):
        return "PrivateBoundaryContext(<redacted>)"


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
        self.create_boundaries = []

    async def search_offers(
        self,
        api_key,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        self.calls.append(
            ("search", api_key, max_price_per_hour, min_vram_gb, disk_gb)
        )
        return list(self.search_results)

    async def get_offer(
        self,
        api_key,
        offer_id,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        self.calls.append(
            (
                "lookup",
                str(offer_id),
                api_key,
                max_price_per_hour,
                min_vram_gb,
                disk_gb,
            )
        )
        return next(
            (
                dict(offer)
                for offer in self.search_results
                if str(offer.get("offer_id")) == str(offer_id)
                and float(offer.get("dph_total", float("inf")))
                <= max_price_per_hour
                and float(offer.get("gpu_ram_gb", 0)) >= min_vram_gb
            ),
            None,
        )

    async def create_instance(
        self,
        api_key,
        *,
        offer_id,
        disk_gb,
        label,
        release,
        boundary_token,
        session_id,
    ):
        self.calls.append(
            ("create", offer_id, disk_gb, label, api_key, release)
        )
        self.create_boundaries.append(
            PrivateBoundaryContext(boundary_token, session_id)
        )
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
            release=options.get("release", worker_release()),
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
        provider_token=None,
    ):
        attempt = CloudAttempt.new(
            idempotency_key="idem-" + attempt_id,
            attempt_id=attempt_id,
            quote=selected_quote or quote(),
            state=state,
            now=self.clock(),
        )
        if (
            instance_id is not None
            or retry_count
            or cancel_requested
            or provider_token is not None
        ):
            attempt = attempt.transition(
                state,
                now=self.clock(),
                instance_id=instance_id,
                retry_count=retry_count,
                cancel_requested=cancel_requested,
                provider_token=provider_token,
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
                    "inet_down_mbps": 500.0,
                    "disk_bw_mbps": 600.0,
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
                release=worker_release(),
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
                release,
                boundary_token,
                session_id,
            ):
                self.provider.calls.append(
                    (
                        "create",
                        offer_id,
                        disk_gb,
                        label,
                        api_key,
                        release,
                    )
                )
                self.provider.create_boundaries.append(
                    PrivateBoundaryContext(boundary_token, session_id)
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
            selected_quote=quote(max_instance_creates=2),
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
                "inet_down_mbps": 1200.0,
                "disk_bw_mbps": 700.0,
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
        self.assertIn("inet_down_mbps", replacement.quote.to_record())
        self.assertEqual(replacement.quote.inet_down_mbps, 1200.0)
        self.assertEqual(replacement.quote.disk_bw_mbps, 700.0)
        self.assertEqual(replacement.quote.max_instance_creates, 2)
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
            second_failure.sanitized_error,
            "The authorized total instance-create limit was reached.",
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_attempt_replacement_rotates_boundary_before_create(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=2),
            provider_token="a" * 64,
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1200.0,
                "disk_bw_mbps": 700.0,
            }
        ]
        original_create = self.provider.create_instance

        async def ambiguous_create(*args, **kwargs):
            persisted = self.repository.get(attempt.attempt_id)
            self.assertEqual(persisted.state, AttemptState.CREATING)
            self.assertEqual(persisted.provider_token, "b" * 64)
            self.assertEqual(kwargs["boundary_token"], "b" * 64)
            self.assertNotEqual(kwargs["boundary_token"], "a" * 64)
            self.assertEqual(kwargs["session_id"], attempt.attempt_id)
            await original_create(*args, **kwargs)
            self.provider.instances = [
                provider_instance("instance-2", attempt.label)
            ]
            raise VastError("Synthetic lost response.", retryable=True)

        self.provider.create_instance = ambiguous_create

        with patch("secrets.token_hex", return_value="b" * 64):
            replacement = asyncio.run(
                self.lifecycle().handle_start_failure(
                    attempt.attempt_id,
                    failure_code="boot_timeout",
                )
            )

        self.assertEqual(replacement.state, AttemptState.STARTING)
        self.assertEqual(replacement.instance_id, "instance-2")
        self.assertEqual(replacement.provider_token, "b" * 64)
        self.assertEqual(len(self.provider.create_boundaries), 1)
        boundary = self.provider.create_boundaries[0]
        self.assertEqual(boundary.boundary_token, "b" * 64)
        self.assertEqual(boundary.session_id, attempt.attempt_id)
        self.assertNotIn("provider_token", repr(replacement))
        self.assertNotIn("provider_token", replacement.public_payload())

    def test_replacement_reapplies_connection_floors_before_create(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=2),
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.999,
                "inet_down_mbps": 400.0,
                "disk_bw_mbps": 700.0,
            }
        ]

        failed = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(
            failed.sanitized_error,
            "No safe replacement offer is currently available.",
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_legacy_limit_one_stops_before_blacklist_and_replacement_search(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=1),
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]

        failed = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertIsNone(failed.instance_id)
        self.assertEqual(failed.retry_count, 0)
        self.assertEqual(
            failed.sanitized_error,
            "The authorized total instance-create limit was reached.",
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )
        self.assertFalse(
            self.blacklist.contains(
                {
                    "machine_id": "machine-7",
                    "host_id": "host-3",
                    "public_ipaddr": "8.8.8.8",
                },
                now=self.clock(),
            )
        )

    def test_replacement_never_uses_a_release_different_from_the_quote(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=2),
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
            }
        ]
        changed_release = worker_release(
            template_character="2",
            commit_character="c",
            archive_character="d",
        )

        failed = asyncio.run(
            self.lifecycle(release=changed_release).handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
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


class RecoveringSessionService:
    def __init__(self, repository):
        self.repository = repository
        self.bootstrap_calls = []
        self.recovery_calls = []
        self.deadline_destroy_calls = []
        self.deadline_prepare_calls = []
        self.terminal_destroy_calls = []
        self.active_work = {}

    async def bootstrap_session(self, session_id):
        session = self.repository.get(session_id)
        self.bootstrap_calls.append(session)
        return session

    async def recover_session(self, session_id):
        session = self.repository.get(session_id)
        self.recovery_calls.append(session)
        return session

    def confirmed_deadline_destroy(self, session_id):
        self.deadline_destroy_calls.append(session_id)

    def confirmed_terminal_destroy(self, session_id):
        state = self.repository.get(session_id).state
        self.terminal_destroy_calls.append((session_id, state))
        if state == SessionState.DESTROYED:
            self.active_work[session_id] = "failed"

    async def prepare_deadline_destroy(self, session_id):
        self.deadline_prepare_calls.append(session_id)


class ReadySessionService(RecoveringSessionService):
    async def bootstrap_session(self, session_id):
        session = await super().bootstrap_session(session_id)
        if session.state == SessionState.BOOTSTRAPPING:
            session = self.repository.transition(
                session.session_id,
                SessionState.PROVISIONING,
                now=session.updated_at + 1,
            )
        if session.state == SessionState.PROVISIONING:
            session = self.repository.transition(
                session.session_id,
                SessionState.VALIDATING,
                now=session.updated_at + 1,
            )
        if session.state == SessionState.VALIDATING:
            session = self.repository.transition(
                session.session_id,
                SessionState.READY,
                now=session.updated_at + 1,
                installed_manifest_digest=session.manifest_digest,
            )
        return session


class TerminalSessionService(RecoveringSessionService):
    diagnostic = "Remote worker boundary authentication failed."

    async def bootstrap_session(self, session_id):
        session = self.repository.get(session_id)
        self.bootstrap_calls.append(session)
        if session.state == SessionState.BOOTSTRAPPING:
            self.repository.transition(
                session.session_id,
                SessionState.PROVISIONING,
                now=session.updated_at + 1,
            )
        raise TerminalProvisioningError(self.diagnostic)


class TerminalRecoverySessionService(RecoveringSessionService):
    diagnostic = "Remote deadline enforcement failed."

    async def recover_session(self, session_id):
        session = self.repository.get(session_id)
        self.recovery_calls.append(session)
        raise TerminalProvisioningError(self.diagnostic)


class SessionLifecycleTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.sessions = SessionRepository(
            self.data_directory / "attempts.sqlite3"
        )
        self.session_service = RecoveringSessionService(self.sessions)

    def session_lifecycle(self):
        from cloud_run.lifecycle import CloudRunLifecycle

        return CloudRunLifecycle(
            FakeSettings(),
            self.repository,
            provider=self.provider,
            blacklist=self.blacklist,
            clock=self.clock,
            sleep=self.clock.sleep,
            readiness_probe=self.probe,
            release=worker_release(),
            session_repository=self.sessions,
            session_service=self.session_service,
        )

    def save_session(
        self,
        state=SessionState.BOOTSTRAPPING,
        *,
        session_id="session-1",
        instance_id="instance-1",
        retry_count=0,
        max_instance_creates=1,
    ):
        selected = quote(max_instance_creates=max_instance_creates)
        session = CloudSession.new(
            "key-" + session_id,
            session_id=session_id,
            quote=selected,
            manifest_digest=selected.manifest_digest,
            deadline_at=self.clock() + 7200,
            deadline_mode="finite",
            disk_gb=80,
            now=self.clock(),
            state=state,
        ).transition(
            state,
            now=self.clock(),
            instance_id=instance_id,
            retry_count=retry_count,
            provider_token="a" * 64,
            session_secret_hex="d" * 64,
        )
        return self.sessions.create_or_get(session)[0]

    @staticmethod
    def worker_instance(instance_id, label):
        return {
            "instance_id": instance_id,
            "label": label,
            "actual_status": "running",
            "public_ipaddr": "8.8.8.8",
            "ports": {"8765/tcp": [{"HostPort": "32100"}]},
            "status_msg": None,
            "jupyter_token": "f" * 64,
        }

    def test_boot_adopts_exact_worker_mapping_and_private_boundary_token(self):
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        connected = asyncio.run(
            self.session_lifecycle().reconcile_session_once(
                session.session_id
            )
        )

        self.assertEqual(
            connected.worker_base_url,
            "http://8.8.8.8:32100",
        )
        self.assertEqual(
            connected.provider_token,
            "a" * 64,
        )
        self.assertEqual(len(self.session_service.bootstrap_calls), 1)
        self.assertNotIn(
            "a" * 64,
            repr(connected.public_payload()),
        )

    def test_session_watchdog_reaches_ready_without_browser_polling(self):
        self.session_service = ReadySessionService(self.sessions)
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        lifecycle = self.session_lifecycle()

        async def scenario():
            task = lifecycle.schedule_session_watchdog(
                session.session_id
            )
            self.assertIsNotNone(task)
            return await task

        ready = asyncio.run(scenario())

        self.assertEqual(ready.state, SessionState.READY)
        self.assertEqual(
            self.sessions.get(session.session_id).state,
            SessionState.READY,
        )
        self.assertEqual(len(self.session_service.bootstrap_calls), 1)

    def test_terminal_provisioning_failure_destroys_and_verifies_immediately(self):
        self.session_service = TerminalSessionService(self.sessions)
        session = self.save_session(max_instance_creates=1)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        asyncio.run(
            self.provider.create_instance(
                "synthetic-value",
                offer_id=session.quote.offer_id,
                disk_gb=session.disk_gb,
                label=session.label,
                release=worker_release(),
                boundary_token="a" * 64,
                session_id=session.session_id,
            )
        )
        lifecycle = self.session_lifecycle()
        self.assertEqual(lifecycle.boot_deadline_seconds, 15 * 60)

        destroyed = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertIsNone(destroyed.instance_id)
        self.assertFalse(destroyed.public_payload()["billing_may_continue"])
        self.assertEqual(
            destroyed.sanitized_error,
            TerminalSessionService.diagnostic,
        )
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["create", "get", "destroy", "list"],
        )
        self.assertEqual(
            len(
                [
                    call
                    for call in self.provider.calls
                    if call[0] == "create"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    call
                    for call in self.provider.calls
                    if call[0] == "destroy"
                ]
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    call
                    for call in self.provider.calls
                    if call[0] == "list"
                ]
            ),
            1,
        )
        self.assertEqual(
            [
                call
                for call in self.provider.calls
                if call[0] == "search"
            ],
            [],
        )

    def test_terminal_destroy_exception_keeps_residual_billing_warning(self):
        self.session_service = TerminalSessionService(self.sessions)
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        async def failing_destroy(api_key, instance_id):
            self.provider.calls.append(("destroy", instance_id, api_key))
            raise RuntimeError("synthetic destroy failure")

        self.provider.destroy_instance = failing_destroy
        lifecycle = self.session_lifecycle()
        lifecycle.boot_deadline_seconds = 3
        lifecycle.poll_interval_seconds = 1

        failed = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.residual_inventory, ("instance-1",))
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            failed.sanitized_error,
            "The Vast instance is still present; destroy it in "
            "the Vast console immediately.",
        )
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["get", "destroy", "list"],
        )
        for rendered in (repr(failed), repr(failed.public_payload())):
            self.assertNotIn(session.provider_token, rendered)
            self.assertNotIn(session.session_secret_hex, rendered)

    def test_terminal_destroy_unverifiable_inventory_keeps_warning(self):
        self.session_service = TerminalSessionService(self.sessions)
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.list_error = RuntimeError("synthetic inventory failure")
        lifecycle = self.session_lifecycle()
        lifecycle.boot_deadline_seconds = 3
        lifecycle.poll_interval_seconds = 1

        failed = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.residual_inventory, ("instance-1",))
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            failed.sanitized_error,
            "Destruction could not be verified because Vast inventory is unavailable.",
        )
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["get", "destroy", "list"],
        )

    def test_terminal_destroy_residual_label_keeps_warning(self):
        self.session_service = TerminalSessionService(self.sessions)
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.destroy_removes = False
        lifecycle = self.session_lifecycle()
        lifecycle.boot_deadline_seconds = 3
        lifecycle.poll_interval_seconds = 1

        failed = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.residual_inventory, ("instance-1",))
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            failed.sanitized_error,
            "The Vast instance is still present; destroy it in "
            "the Vast console immediately.",
        )
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["get", "destroy", "list"],
        )

    def test_recovery_lists_once_and_fails_closed_on_duplicate_label(self):
        session = self.save_session(
            retry_count=1,
            max_instance_creates=2,
        )
        self.provider.instances = [
            self.worker_instance("instance-1", session.label),
            self.worker_instance("instance-2", session.label),
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        failed = self.sessions.get(session.session_id)
        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(
            failed.residual_inventory,
            ("instance-1", "instance-2"),
        )
        self.assertEqual(failed.retry_count, 1)
        self.assertEqual(failed.quote.max_instance_creates, 2)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list"],
        )
        self.assertEqual(recovered, [failed])
        self.assertEqual(self.session_service.bootstrap_calls, [])

    def test_restart_adopts_one_instance_and_reenforces_session_recovery(self):
        session = self.save_session(
            state=SessionState.READY,
            retry_count=1,
            max_instance_creates=2,
        )
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].retry_count, 1)
        self.assertEqual(recovered[0].quote.max_instance_creates, 2)
        self.assertEqual(
            self.sessions.get(session.session_id).worker_base_url,
            "http://8.8.8.8:32100",
        )
        self.assertEqual(recovered[0].provider_token, "a" * 64)
        self.assertEqual(len(self.session_service.recovery_calls), 1)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list"],
        )

    def test_terminal_recovery_failure_destroys_and_verifies_immediately(self):
        self.session_service = TerminalRecoverySessionService(self.sessions)
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        destroyed = self.sessions.get(session.session_id)
        self.assertEqual(recovered, [destroyed])
        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertIsNone(destroyed.instance_id)
        self.assertFalse(destroyed.public_payload()["billing_may_continue"])
        self.assertEqual(
            destroyed.sanitized_error,
            TerminalRecoverySessionService.diagnostic,
        )
        self.assertEqual(self.clock.sleeps, [])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list", "destroy", "list"],
        )

    def test_terminal_running_recovery_abandons_work_after_verified_destroy(self):
        self.session_service = TerminalRecoverySessionService(self.sessions)
        session = self.save_session(state=SessionState.RUNNING)
        self.session_service.active_work[session.session_id] = "running"
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        destroyed = self.sessions.get(session.session_id)
        self.assertEqual(recovered, [destroyed])
        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertEqual(
            self.session_service.terminal_destroy_calls,
            [(session.session_id, SessionState.DESTROYED)],
        )
        self.assertEqual(
            self.session_service.active_work[session.session_id],
            "failed",
        )

    def test_expired_session_absence_is_recorded_as_deadline_destruction(self):
        session = self.save_session(state=SessionState.READY)
        self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=100.0,
            deadline_at=99.0,
        )
        self.provider.instances = []

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        self.assertEqual(recovered[0].state, SessionState.DESTROYED)
        self.assertIsNone(recovered[0].instance_id)
        self.assertEqual(
            self.session_service.deadline_destroy_calls,
            [session.session_id],
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list"],
        )

    def test_deadline_recovery_requires_both_known_id_and_label_absent(self):
        session = self.save_session(state=SessionState.READY)
        self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=100.0,
            deadline_at=99.0,
        )
        self.provider.instances = [
            self.worker_instance("instance-1", "unexpected-label")
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        self.assertEqual(recovered[0].state, SessionState.FAILED)
        self.assertEqual(recovered[0].instance_id, "instance-1")
        self.assertEqual(
            recovered[0].residual_inventory,
            ("instance-1",),
        )
        self.assertTrue(
            recovered[0].public_payload()["billing_may_continue"]
        )

    def test_local_expiry_attempts_bounded_retrieval_then_verifies_destroy(self):
        session = self.save_session(state=SessionState.READY)
        self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=100.0,
            deadline_at=99.0,
        )
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        destroyed = asyncio.run(
            self.session_lifecycle().enforce_session_deadline(
                session.session_id
            )
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertEqual(
            self.session_service.deadline_prepare_calls,
            [session.session_id],
        )
        self.assertEqual(
            self.session_service.deadline_destroy_calls,
            [session.session_id],
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_only_boot_failure_replaces_once_after_inventory_absence(self):
        session = self.save_session(max_instance_creates=2)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1000.0,
                "disk_bw_mbps": 750.0,
            }
        ]

        replacement = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(replacement.state, SessionState.BOOTSTRAPPING)
        self.assertEqual(replacement.retry_count, 1)
        self.assertEqual(replacement.instance_id, "instance-2")
        self.assertEqual(replacement.quote.offer_id, "43")
        self.assertIn("inet_down_mbps", replacement.quote.to_record())
        self.assertEqual(replacement.quote.inet_down_mbps, 1000.0)
        self.assertEqual(replacement.quote.disk_bw_mbps, 750.0)
        self.assertEqual(replacement.quote.max_instance_creates, 2)
        actions = [call[0] for call in self.provider.calls]
        self.assertEqual(actions, ["destroy", "list", "search", "create"])

        self.provider.instances = [
            self.worker_instance("instance-2", replacement.label)
        ]
        search_count = len(
            [call for call in self.provider.calls if call[0] == "search"]
        )
        exhausted = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="healthcheck_failure",
            )
        )
        self.assertEqual(exhausted.state, SessionState.FAILED)
        self.assertEqual(
            exhausted.sanitized_error,
            "The authorized total instance-create limit was reached.",
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "search"]),
            search_count,
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_session_replacement_rotates_boundary_before_create(self):
        session = self.save_session(max_instance_creates=2)
        original_secret = session.session_secret_hex
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1000.0,
                "disk_bw_mbps": 750.0,
            }
        ]
        original_destroy = self.provider.destroy_instance
        original_create = self.provider.create_instance

        async def observing_destroy(*args, **kwargs):
            persisted = self.sessions.get(session.session_id)
            self.assertEqual(persisted.state, SessionState.DESTROYING)
            self.assertEqual(persisted.provider_token, "a" * 64)
            self.assertEqual(persisted.session_secret_hex, original_secret)
            return await original_destroy(*args, **kwargs)

        async def ambiguous_create(*args, **kwargs):
            persisted = self.sessions.get(session.session_id)
            self.assertEqual(persisted.state, SessionState.CREATING)
            self.assertEqual(persisted.provider_token, "b" * 64)
            self.assertEqual(persisted.session_secret_hex, original_secret)
            self.assertEqual(kwargs["boundary_token"], "b" * 64)
            self.assertNotEqual(kwargs["boundary_token"], "a" * 64)
            self.assertEqual(kwargs["session_id"], session.session_id)
            await original_create(*args, **kwargs)
            self.provider.instances = [
                self.worker_instance("instance-2", session.label)
            ]
            raise VastError("Synthetic lost response.", retryable=True)

        self.provider.destroy_instance = observing_destroy
        self.provider.create_instance = ambiguous_create

        with patch("secrets.token_hex", return_value="b" * 64):
            replacement = asyncio.run(
                self.session_lifecycle().handle_session_boot_failure(
                    session.session_id,
                    failure_code="boot_timeout",
                )
            )

        self.assertEqual(replacement.state, SessionState.BOOTSTRAPPING)
        self.assertEqual(replacement.instance_id, "instance-2")
        self.assertEqual(replacement.provider_token, "b" * 64)
        self.assertEqual(replacement.session_secret_hex, original_secret)
        self.assertEqual(len(self.provider.create_boundaries), 1)
        boundary = self.provider.create_boundaries[0]
        self.assertEqual(boundary.boundary_token, "b" * 64)
        self.assertEqual(boundary.session_id, session.session_id)
        rendered = repr(replacement)
        self.assertNotIn("provider_token", rendered)
        self.assertNotIn("session_secret_hex", rendered)
        self.assertNotIn("provider_token", replacement.public_payload())

    def test_session_ambiguous_replacement_fails_closed_on_multiple_matches(self):
        session = self.save_session(max_instance_creates=2)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1000.0,
                "disk_bw_mbps": 750.0,
            }
        ]
        original_create = self.provider.create_instance

        async def ambiguous_create(*args, **kwargs):
            await original_create(*args, **kwargs)
            self.provider.instances = [
                self.worker_instance(instance_id, session.label)
                for instance_id in ("instance-2", "instance-3")
            ]
            raise VastError("Synthetic lost response.", retryable=True)

        self.provider.create_instance = ambiguous_create

        failed = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertIsNone(failed.instance_id)
        self.assertEqual(
            failed.residual_inventory,
            ("instance-2", "instance-3"),
        )
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

        repeated = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(repeated.state, SessionState.FAILED)
        self.assertIsNone(repeated.instance_id)
        self.assertEqual(
            repeated.residual_inventory,
            ("instance-2", "instance-3"),
        )
        self.assertTrue(repeated.public_payload()["billing_may_continue"])
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_session_replacement_reapplies_connection_floors_before_create(self):
        session = self.save_session(max_instance_creates=2)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.999,
                "inet_down_mbps": 400.0,
                "disk_bw_mbps": 750.0,
            }
        ]

        failed = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(
            failed.sanitized_error,
            "No safe replacement offer is currently available.",
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_limit_one_destroys_failed_boot_without_replacement_search(self):
        session = self.save_session(max_instance_creates=1)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        asyncio.run(
            self.provider.create_instance(
                "synthetic-value",
                offer_id=session.quote.offer_id,
                disk_gb=session.disk_gb,
                label=session.label,
                release=worker_release(),
                boundary_token="a" * 64,
                session_id=session.session_id,
            )
        )
        initial_create_count = len(
            [call for call in self.provider.calls if call[0] == "create"]
        )

        result = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            initial_create_count,
        )
        self.assertEqual(initial_create_count, 1)
        self.assertFalse(
            self.blacklist.contains(
                {
                    "machine_id": "machine-7",
                    "host_id": "host-3",
                    "public_ipaddr": "8.8.8.8",
                },
                now=self.clock(),
            )
        )
        self.assertEqual(
            result.sanitized_error,
            "The authorized total instance-create limit was reached.",
        )

    def test_ambiguous_replacement_create_adopts_inventory_without_second_create(self):
        session = self.save_session(max_instance_creates=2)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1000.0,
                "disk_bw_mbps": 750.0,
            }
        ]
        original_create = self.provider.create_instance

        async def ambiguous_create(*args, **kwargs):
            await original_create(*args, **kwargs)
            self.provider.instances = [
                self.worker_instance(
                    "instance-2",
                    session.label,
                )
            ]
            raise VastError(
                "Synthetic lost response.",
                retryable=True,
            )

        self.provider.create_instance = ambiguous_create

        replacement = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(replacement.state, SessionState.BOOTSTRAPPING)
        self.assertEqual(replacement.instance_id, "instance-2")
        self.assertEqual(replacement.retry_count, 1)
        self.assertEqual(replacement.quote.max_instance_creates, 2)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list", "search", "create", "list"],
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_attempt_ambiguous_replacement_fails_closed_on_multiple_matches(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=2),
            provider_token="a" * 64,
        )
        self.provider.instances = [
            provider_instance("instance-1", attempt.label)
        ]
        self.provider.search_results = [
            {
                "offer_id": 43,
                "gpu_name": "RTX 4090",
                "gpu_ram_gb": 24.0,
                "dph_total": 0.44,
                "reliability": 0.995,
                "machine_id": "machine-8",
                "host_id": "host-4",
                "public_ipaddr": "1.1.1.1",
                "inet_down_mbps": 1200.0,
                "disk_bw_mbps": 700.0,
            }
        ]
        original_create = self.provider.create_instance

        async def ambiguous_create(*args, **kwargs):
            await original_create(*args, **kwargs)
            self.provider.instances = [
                provider_instance(instance_id, attempt.label)
                for instance_id in ("instance-2", "instance-3")
            ]
            raise VastError("Synthetic lost response.", retryable=True)

        self.provider.create_instance = ambiguous_create

        failed = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertIsNone(failed.instance_id)
        self.assertEqual(
            failed.residual_inventory,
            ("instance-2", "instance-3"),
        )
        payload = failed.public_payload()
        self.assertTrue(payload["billing_may_continue"])
        self.assertIn("instance-2", payload["emergency_action"])
        self.assertIn("instance-3", payload["emergency_action"])
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            1,
        )

    def test_session_destroy_requires_inventory_absence_after_delete(self):
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        destroyed = asyncio.run(
            self.session_lifecycle().destroy_session(
                session.session_id
            )
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertIsNone(destroyed.instance_id)
        self.assertIsNone(destroyed.provider_token)
        self.assertIsNone(destroyed.session_secret_hex)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_delete_response_with_residual_inventory_keeps_billing_warning(self):
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        self.provider.destroy_removes = False

        failed = asyncio.run(
            self.session_lifecycle().destroy_session(
                session.session_id
            )
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_destroy_never_treats_a_matching_label_without_id_as_absent(self):
        session = self.save_session(
            state=SessionState.READY,
            instance_id=None,
        )
        self.provider.instances = [
            {
                "instance_id": None,
                "label": session.label,
                "actual_status": "running",
            }
        ]

        failed = asyncio.run(
            self.session_lifecycle().destroy_session(
                session.session_id
            )
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list"],
        )


if __name__ == "__main__":
    unittest.main()

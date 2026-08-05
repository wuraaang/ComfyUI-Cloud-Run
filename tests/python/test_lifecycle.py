import asyncio
from dataclasses import replace
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


SETTINGS_REVISION_A = "11111111-1111-4111-8111-111111111111"
SETTINGS_REVISION_B = "22222222-2222-4222-8222-222222222222"


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
            "protocol_version": "2",
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
    deadline_mode="finite",
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
        duration_seconds=(7200 if deadline_mode == "finite" else None),
        deadline_mode=deadline_mode,
        approximate_max_active_charge=(
            price * 2 if deadline_mode == "finite" else None
        ),
        template_hash_id="1" * 32,
        worker_commit="a" * 40,
        worker_archive_sha256="b" * 64,
        protocol_version="2",
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
    def __init__(
        self,
        *,
        api_key="synthetic-value",
        api_key_revision=SETTINGS_REVISION_A,
    ):
        self.api_key = api_key
        self.api_key_revision = api_key_revision

    def load(self):
        return {
            "api_key": self.api_key,
            "api_key_revision": self.api_key_revision,
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

    def test_attempt_boot_failure_with_historical_limit_two_never_searches_or_creates(self):
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
                "reliability": 0.99,
            }
        ]

        result = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, AttemptState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.residual_inventory, ())
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.quote.max_instance_creates, 2)
        self.assertEqual(result.sanitized_error, "The managed instance did not become ready.")
        self.assertEqual([call[0] for call in self.provider.calls], ["destroy", "list"])
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
                attempt.quote.to_record(),
                now=self.clock(),
            )
        )

    def test_authentication_failure_destroys_without_replacement(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-auth",
            selected_quote=quote(max_instance_creates=2),
        )
        self.provider.instances = [
            provider_instance("instance-auth", attempt.label)
        ]

        result = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="auth",
            )
        )

        self.assertEqual(result.state, AttemptState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertEqual([call[0] for call in self.provider.calls], ["destroy", "list"])

    def test_restart_never_resurrects_historical_retry_state(self):
        attempt = self.save_attempt(
            AttemptState.RETRYING,
            instance_id="instance-legacy",
            retry_count=1,
            selected_quote=quote(max_instance_creates=2),
        )
        self.provider.instances = [
            provider_instance("instance-legacy", attempt.label)
        ]

        recovered = asyncio.run(self.lifecycle().recover())

        self.assertEqual(len(recovered), 1)
        result = recovered[0]
        self.assertEqual(result.state, AttemptState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(
            result.sanitized_error,
            "Automatic Vast replacement is disabled. Start a new reviewed rental.",
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list", "destroy", "list"],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] in {"search", "create"}],
            [],
        )

    def test_failed_boot_with_unverified_destruction_retains_billing_warning(self):
        attempt = self.save_attempt(
            AttemptState.STARTING,
            instance_id="instance-1",
            selected_quote=quote(max_instance_creates=2),
        )
        self.provider.instances = [provider_instance("instance-1", attempt.label)]
        self.provider.destroy_removes = False

        failed = asyncio.run(
            self.lifecycle().handle_start_failure(
                attempt.attempt_id,
                failure_code="transport_failure",
            )
        )

        self.assertEqual(failed.state, AttemptState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            [call for call in self.provider.calls if call[0] in {"search", "create"}],
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


class ResumingTeardownSessionService(RecoveringSessionService):
    def __init__(self, repository):
        super().__init__(repository)
        self.resume_calls = []

    async def resume_session(self, session_id):
        self.resume_calls.append(session_id)
        return self.repository.get(session_id)


class FailingTeardownSessionService(ResumingTeardownSessionService):
    async def resume_session(self, session_id):
        self.resume_calls.append(session_id)
        raise RuntimeError("synthetic common teardown failure")


class PreemptingTeardownSessionService(ResumingTeardownSessionService):
    def __init__(self, repository):
        super().__init__(repository)
        self.lifecycle = None
        self.teardown_tasks = {}

    async def resume_session(self, session_id):
        self.resume_calls.append(session_id)
        task = self.teardown_tasks.get(session_id)
        if task is None or task.done():
            task = asyncio.create_task(self._teardown(session_id))
            self.teardown_tasks[session_id] = task
        return await asyncio.shield(task)

    async def _teardown(self, session_id):
        await self.lifecycle.preempt_session(session_id)
        return await self.lifecycle.destroy_session(session_id)


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


class ImmediateTerminalSessionService(TerminalSessionService):
    diagnostic = "Remote provisioning is incomplete."


class TransientAuthenticationSessionService(ReadySessionService):
    diagnostic = "Remote worker boundary authentication failed."

    def __init__(self, repository):
        super().__init__(repository)
        self.authentication_failures = 1

    async def bootstrap_session(self, session_id):
        if self.authentication_failures:
            self.authentication_failures -= 1
            session = self.repository.get(session_id)
            self.bootstrap_calls.append(session)
            raise TerminalProvisioningError(self.diagnostic)
        return await super().bootstrap_session(session_id)


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

    def session_lifecycle(self, *, settings_store=None, provider=None):
        from cloud_run.lifecycle import CloudRunLifecycle

        return CloudRunLifecycle(
            settings_store or FakeSettings(),
            self.repository,
            provider=provider or self.provider,
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
        deadline_mode="finite",
        settings_revision=SETTINGS_REVISION_A,
    ):
        selected = quote(
            max_instance_creates=max_instance_creates,
            deadline_mode=deadline_mode,
        )
        session = CloudSession.new(
            "key-" + session_id,
            session_id=session_id,
            quote=selected,
            manifest_digest=selected.manifest_digest,
            deadline_at=(
                self.clock() + 7200
                if deadline_mode == "finite"
                else None
            ),
            deadline_mode=deadline_mode,
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
            create_settings_revision=settings_revision,
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

    def test_preempt_session_cancels_watchdog_before_teardown(self):
        session = self.save_session()
        lifecycle = self.session_lifecycle()

        async def scenario():
            started = asyncio.Event()
            blocked = asyncio.Event()

            async def blocking_reconcile(_session_id):
                started.set()
                await blocked.wait()

            lifecycle.reconcile_session_once = blocking_reconcile
            task = lifecycle.schedule_session_watchdog(session.session_id)
            await started.wait()
            await lifecycle.preempt_session(session.session_id)
            await lifecycle.preempt_session(session.session_id)
            return task

        task = asyncio.run(scenario())

        self.assertTrue(task.cancelled())
        self.assertEqual(self.provider.calls, [])

    def test_watchdog_schedule_during_preempt_cannot_restart_cancelled_work(self):
        session = self.save_session()
        lifecycle = self.session_lifecycle()

        async def scenario():
            started = asyncio.Event()
            cancelling = asyncio.Event()
            finish_cancellation = asyncio.Event()
            calls = 0

            async def cancellation_blocked_reconcile(_session_id):
                nonlocal calls
                calls += 1
                if calls > 1:
                    return session.transition(
                        SessionState.FAILED,
                        now=self.clock(),
                    )
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    await finish_cancellation.wait()
                    raise

            lifecycle.reconcile_session_once = (
                cancellation_blocked_reconcile
            )
            original = lifecycle.schedule_session_watchdog(
                session.session_id
            )
            await started.wait()
            preempt = asyncio.create_task(
                lifecycle.preempt_session(session.session_id)
            )
            await cancelling.wait()
            try:
                blocked = lifecycle.schedule_session_watchdog(
                    session.session_id
                )
                self.assertIsNone(blocked)
            finally:
                finish_cancellation.set()
                await preempt

            self.assertTrue(original.cancelled())
            self.assertEqual(calls, 1)
            restarted = lifecycle.schedule_session_watchdog(
                session.session_id
            )
            self.assertIsNotNone(restarted)
            self.assertEqual(
                (await restarted).state,
                SessionState.FAILED,
            )
            return calls

        calls = asyncio.run(scenario())

        self.assertEqual(calls, 2)
        self.assertEqual(self.provider.calls, [])

    def test_recovery_registration_during_preempt_cannot_start_remote_work(self):
        session = self.save_session(state=SessionState.READY)
        lifecycle = self.session_lifecycle()
        self.provider.instances = []

        async def scenario():
            started = asyncio.Event()
            cancelling = asyncio.Event()
            finish_cancellation = asyncio.Event()

            async def cancellation_blocked_reconcile(_session_id):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    await finish_cancellation.wait()
                    raise

            lifecycle.reconcile_session_once = (
                cancellation_blocked_reconcile
            )
            lifecycle.schedule_session_watchdog(session.session_id)
            await started.wait()
            preempt = asyncio.create_task(
                lifecycle.preempt_session(session.session_id)
            )
            await cancelling.wait()
            try:
                blocked_recovery = await lifecycle.recover_sessions()
                current_during_preempt = self.sessions.get(
                    session.session_id
                )
            finally:
                finish_cancellation.set()
                await preempt

            recovered_after_preempt = await lifecycle.recover_sessions()
            return (
                blocked_recovery,
                current_during_preempt,
                recovered_after_preempt,
            )

        blocked, current, recovered = asyncio.run(scenario())

        self.assertEqual(blocked, [])
        self.assertEqual(current.state, SessionState.READY)
        self.assertEqual(current.instance_id, session.instance_id)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, SessionState.DESTROYED)
        self.assertEqual([call[0] for call in self.provider.calls], ["list"])

    def test_preempt_session_cancels_inflight_recovery_inventory(self):
        session = self.save_session(state=SessionState.READY)
        lifecycle = self.session_lifecycle()

        async def scenario():
            started = asyncio.Event()
            blocked = asyncio.Event()

            async def blocking_inventory(api_key):
                self.provider.calls.append(("list", api_key))
                started.set()
                await blocked.wait()
                return []

            self.provider.list_instances = blocking_inventory
            recovery = asyncio.create_task(lifecycle.recover_sessions())
            await started.wait()
            await lifecycle.preempt_session(session.session_id)
            await asyncio.gather(recovery, return_exceptions=True)
            cancelled_by_preempt = recovery.cancelled()
            return cancelled_by_preempt

        cancelled = asyncio.run(scenario())

        self.assertTrue(cancelled)
        self.assertEqual([call[0] for call in self.provider.calls], ["list"])

    def test_preempting_one_session_does_not_cancel_another_recovery(self):
        first = self.save_session(
            state=SessionState.READY,
            session_id="session-a",
            instance_id="instance-a",
        )
        second = self.save_session(
            state=SessionState.READY,
            session_id="session-b",
            instance_id="instance-b",
        )
        lifecycle = self.session_lifecycle()

        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()

            async def blocking_inventory(api_key):
                self.provider.calls.append(("list", api_key))
                started.set()
                await release.wait()
                return []

            self.provider.list_instances = blocking_inventory
            recovery = asyncio.create_task(lifecycle.recover_sessions())
            await started.wait()
            await lifecycle.preempt_session(first.session_id)
            release.set()
            return await recovery

        recovered = asyncio.run(scenario())

        self.assertEqual(
            self.sessions.get(first.session_id).state,
            SessionState.READY,
        )
        self.assertEqual(
            self.sessions.get(second.session_id).state,
            SessionState.DESTROYED,
        )
        self.assertEqual(
            [session.session_id for session in recovered],
            [second.session_id],
        )

    def test_preempt_session_cancels_all_concurrent_recovery_work(self):
        session = self.save_session(state=SessionState.READY)
        lifecycle = self.session_lifecycle()

        async def scenario():
            both_started = asyncio.Event()
            release = asyncio.Event()
            started = 0

            async def blocking_inventory(api_key):
                nonlocal started
                self.provider.calls.append(("list", api_key))
                started += 1
                if started == 2:
                    both_started.set()
                await release.wait()
                return []

            self.provider.list_instances = blocking_inventory
            recoveries = [
                asyncio.create_task(lifecycle.recover_sessions())
                for _attempt in range(2)
            ]
            await both_started.wait()
            await lifecycle.preempt_session(session.session_id)
            release.set()
            return await asyncio.gather(
                *recoveries,
                return_exceptions=True,
            )

        recovered = asyncio.run(scenario())

        self.assertTrue(
            all(
                isinstance(result, asyncio.CancelledError)
                for result in recovered
            )
        )
        current = self.sessions.get(session.session_id)
        self.assertEqual(current.state, SessionState.READY)
        self.assertEqual(current.instance_id, session.instance_id)

    def test_stale_absence_observation_cannot_clear_new_residual_paid_identity(self):
        session = self.save_session(state=SessionState.READY)
        lifecycle = self.session_lifecycle()

        async def scenario():
            started = asyncio.Event()
            release = asyncio.Event()

            async def blocking_inventory(api_key):
                self.provider.calls.append(("list", api_key))
                started.set()
                await release.wait()
                return []

            self.provider.list_instances = blocking_inventory
            recovery = asyncio.create_task(lifecycle.recover_sessions())
            await started.wait()
            self.sessions.transition(
                session.session_id,
                SessionState.READY,
                now=self.clock(),
                residual_inventory=("new-paid-instance",),
            )
            release.set()
            return await recovery

        asyncio.run(scenario())

        current = self.sessions.get(session.session_id)
        self.assertNotEqual(current.state, SessionState.DESTROYED)
        self.assertEqual(
            current.residual_inventory,
            ("new-paid-instance",),
        )
        self.assertTrue(current.public_payload()["billing_may_continue"])

    def test_recovery_finalizes_externally_destroyed_known_session_without_delete_or_create(self):
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = []

        recovered = asyncio.run(self.session_lifecycle().recover_sessions())

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, SessionState.DESTROYED)
        self.assertIsNone(recovered[0].instance_id)
        self.assertFalse(recovered[0].public_payload()["billing_may_continue"])
        self.assertEqual([call[0] for call in self.provider.calls], ["list"])

    def test_changed_vast_credential_cannot_finalize_recovery_absence(self):
        session = self.save_session(state=SessionState.READY)
        changed = FakeSettings(
            api_key="synthetic-account-b",
            api_key_revision=SETTINGS_REVISION_B,
        )
        self.provider.instances = []

        recovered = asyncio.run(
            self.session_lifecycle(settings_store=changed).recover_sessions()
        )[0]

        self.assertNotEqual(recovered.state, SessionState.DESTROYED)
        self.assertEqual(recovered.instance_id, session.instance_id)
        self.assertEqual(recovered.provider_token, session.provider_token)
        self.assertEqual(
            recovered.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(recovered.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])

    def test_changed_vast_credential_blocks_reconciliation_provider_probe(self):
        session = self.save_session(state=SessionState.READY)
        changed = FakeSettings(
            api_key="synthetic-account-b",
            api_key_revision=SETTINGS_REVISION_B,
        )

        result = asyncio.run(
            self.session_lifecycle(
                settings_store=changed
            ).reconcile_session_once(session.session_id)
        )

        self.assertEqual(result, session)
        self.assertEqual(self.provider.calls, [])

    def test_changed_vast_credential_cannot_falsely_confirm_destroy(self):
        session = self.save_session(state=SessionState.READY)
        changed = FakeSettings(
            api_key="synthetic-account-b",
            api_key_revision=SETTINGS_REVISION_B,
        )
        self.provider.instances = []

        result = asyncio.run(
            self.session_lifecycle(settings_store=changed).destroy_session(
                session.session_id
            )
        )

        self.assertNotEqual(result.state, SessionState.DESTROYED)
        self.assertEqual(result.instance_id, session.instance_id)
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertEqual(
            result.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(result.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])

    def test_legacy_unbound_session_cannot_falsely_confirm_destroy(self):
        session = self.save_session(
            state=SessionState.READY,
            settings_revision=None,
        )
        self.provider.instances = []

        result = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertNotEqual(result.state, SessionState.DESTROYED)
        self.assertEqual(result.instance_id, session.instance_id)
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertEqual(
            result.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(result.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])

    def test_legacy_unbound_session_cannot_finalize_recovery_absence(self):
        session = self.save_session(
            state=SessionState.READY,
            settings_revision=None,
        )
        self.provider.instances = []

        recovered = asyncio.run(self.session_lifecycle().recover_sessions())[0]

        self.assertNotEqual(recovered.state, SessionState.DESTROYED)
        self.assertEqual(recovered.instance_id, session.instance_id)
        self.assertEqual(recovered.provider_token, session.provider_token)
        self.assertTrue(recovered.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])

    def test_changed_vast_credential_blocks_boot_failure_provider_cleanup(self):
        session = self.save_session(state=SessionState.BOOTSTRAPPING)
        changed = FakeSettings(
            api_key="synthetic-account-b",
            api_key_revision=SETTINGS_REVISION_B,
        )
        self.provider.instances = []

        result = asyncio.run(
            self.session_lifecycle(
                settings_store=changed
            ).handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertEqual(result.instance_id, session.instance_id)
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertEqual(
            result.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(result.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])

    def test_boot_failure_requires_every_residual_id_absent(self):
        session = self.save_session(state=SessionState.BOOTSTRAPPING)
        session = self.sessions.transition(
            session.session_id,
            SessionState.BOOTSTRAPPING,
            now=self.clock(),
            residual_inventory=("residual-instance",),
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label),
            self.worker_instance("residual-instance", "changed-label"),
        ]

        result = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertIn("residual-instance", result.residual_inventory)
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_boot_failure_stale_absence_cannot_clear_new_residual_identity(self):
        session = self.save_session(state=SessionState.BOOTSTRAPPING)
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]
        lifecycle = self.session_lifecycle()

        async def scenario():
            inventory_started = asyncio.Event()
            release_inventory = asyncio.Event()

            async def blocking_inventory(api_key):
                self.provider.calls.append(("list", api_key))
                inventory_started.set()
                await release_inventory.wait()
                return []

            self.provider.list_instances = blocking_inventory
            cleanup = asyncio.create_task(
                lifecycle.handle_session_boot_failure(
                    session.session_id,
                    failure_code="boot_timeout",
                )
            )
            await inventory_started.wait()
            current = self.sessions.get(session.session_id)
            self.sessions.transition(
                current.session_id,
                current.state,
                now=self.clock(),
                residual_inventory=("new-paid-instance",),
            )
            release_inventory.set()
            return await cleanup

        result = asyncio.run(scenario())

        self.assertIn("new-paid-instance", result.residual_inventory)
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_external_absence_clears_secrets_and_preserves_verified_evidence(self):
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=self.clock(),
            installed_manifest_digest=session.manifest_digest,
            residual_inventory=("residual-instance",),
            sanitized_error="Verified output evidence remains local.",
        )
        self.provider.instances = []

        recovered = asyncio.run(self.session_lifecycle().recover_sessions())[0]

        self.assertEqual(recovered.state, SessionState.DESTROYED)
        self.assertIsNone(recovered.instance_id)
        self.assertIsNone(recovered.provider_token)
        self.assertIsNone(recovered.session_secret_hex)
        self.assertEqual(recovered.residual_inventory, ())
        self.assertEqual(
            recovered.installed_manifest_digest,
            session.installed_manifest_digest,
        )
        self.assertEqual(recovered.quote, session.quote)
        self.assertEqual(
            recovered.sanitized_error,
            "Verified output evidence remains local.",
        )

    def test_unavailable_inventory_or_invalid_row_never_finalizes_external_destruction(self):
        for label in ("unavailable", "invalid-row"):
            with self.subTest(label=label):
                session_id = "session-" + label
                session = self.save_session(
                    state=SessionState.READY,
                    session_id=session_id,
                    instance_id="instance-" + label,
                )
                lifecycle = self.session_lifecycle()
                if label == "unavailable":
                    self.provider.list_error = RuntimeError("private inventory error")
                else:
                    async def invalid_inventory(api_key):
                        self.provider.calls.append(("list", api_key))
                        return [{"instance_id": "unrelated", "label": "other"}, None]

                    self.provider.list_instances = invalid_inventory

                recovered = asyncio.run(lifecycle.recover_sessions())
                current = self.sessions.get(session.session_id)

                self.assertIn(current, recovered)
                self.assertNotEqual(current.state, SessionState.DESTROYED)
                self.assertEqual(current.instance_id, session.instance_id)
                self.assertEqual(current.provider_token, session.provider_token)
                self.provider.list_error = None

    def test_destroy_session_accepts_pre_persisted_destroy_requested_state(self):
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.DESTROY_REQUESTED,
            now=self.clock(),
            destroy_requested=True,
        )
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        destroyed = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_direct_destroy_persists_intent_and_preempts_before_provider(self):
        events = []
        outer = self

        class TrackingSessionService(RecoveringSessionService):
            async def preempt_session_surfaces(inner_self, session_id):
                stored = inner_self.repository.get(session_id)
                events.append(
                    (
                        "preempt",
                        stored.destroy_requested,
                        stored.state,
                    )
                )

        self.session_service = TrackingSessionService(self.sessions)
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]
        original_destroy = self.provider.destroy_instance

        async def destroy_after_preempt(api_key, instance_id):
            stored = outer.sessions.get(session.session_id)
            events.append(
                (
                    "provider",
                    stored.destroy_requested,
                    stored.state,
                )
            )
            return await original_destroy(api_key, instance_id)

        self.provider.destroy_instance = destroy_after_preempt

        destroyed = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertEqual(
            events,
            [
                ("preempt", True, SessionState.DESTROY_REQUESTED),
                ("provider", True, SessionState.DESTROYING),
            ],
        )

    def test_recovery_delegates_destroy_request_to_common_teardown(self):
        self.session_service = ResumingTeardownSessionService(self.sessions)
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.DESTROY_REQUESTED,
            now=self.clock(),
            destroy_requested=True,
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]

        recovered = asyncio.run(self.session_lifecycle().recover_sessions())

        self.assertEqual(
            self.session_service.resume_calls,
            [session.session_id],
        )
        self.assertEqual(recovered, [session])
        self.assertEqual(self.provider.calls, [])

    def test_recovery_common_teardown_does_not_self_cancel(self):
        self.session_service = PreemptingTeardownSessionService(self.sessions)
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.DESTROY_REQUESTED,
            now=self.clock(),
            destroy_requested=True,
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]
        lifecycle = self.session_lifecycle()
        self.session_service.lifecycle = lifecycle

        recovered = asyncio.run(lifecycle.recover_sessions())

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, SessionState.DESTROYED)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_recovery_resumes_failed_persisted_destroy_intent(self):
        session = self.save_session(state=SessionState.READY)
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]
        self.provider.destroy_removes = False

        failed = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertTrue(failed.destroy_requested)
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.provider.calls.clear()
        self.provider.destroy_removes = True
        self.session_service = PreemptingTeardownSessionService(self.sessions)
        restarted = self.session_lifecycle()
        self.session_service.lifecycle = restarted

        recovered = asyncio.run(restarted.recover_sessions())

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, SessionState.DESTROYED)
        self.assertFalse(
            recovered[0].public_payload()["billing_may_continue"]
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )

    def test_offer_selected_residual_uses_credential_bound_teardown(self):
        session = self.save_session(
            state=SessionState.OFFER_SELECTED,
            instance_id=None,
        )
        session = self.sessions.transition(
            session.session_id,
            SessionState.OFFER_SELECTED,
            now=self.clock(),
            provider_token=None,
            session_secret_hex=None,
            residual_inventory=("residual-instance",),
        )
        self.provider.instances = [
            self.worker_instance("residual-instance", session.label)
        ]

        destroyed = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertFalse(destroyed.public_payload()["billing_may_continue"])
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list", "destroy", "list"],
        )

    def test_offer_selected_installed_manifest_cannot_local_finalize(self):
        stored = self.save_session(
            state=SessionState.OFFER_SELECTED,
            instance_id=None,
        )
        session = self.sessions.save(
            replace(
                stored,
                instance_id=None,
                worker_base_url=None,
                provider_token=None,
                session_secret_hex=None,
                create_settings_revision=None,
                create_configuration_revision=None,
                installed_manifest_digest=stored.manifest_digest,
            )
        )
        self.provider.instances = [
            self.worker_instance("paid-instance", session.label)
        ]

        result = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertTrue(result.public_payload()["billing_may_continue"])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(len(self.provider.instances), 1)

    def test_destroy_inventory_outage_preserves_every_known_paid_id(self):
        session = self.save_session(state=SessionState.FAILED)
        session = self.sessions.transition(
            session.session_id,
            SessionState.FAILED,
            now=self.clock(),
            residual_inventory=("second-paid-instance",),
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label),
            self.worker_instance("second-paid-instance", "changed-label"),
        ]
        self.provider.list_error = RuntimeError("synthetic inventory outage")

        result = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertEqual(result.instance_id, session.instance_id)
        self.assertEqual(
            set(result.residual_inventory),
            {session.instance_id, "second-paid-instance"},
        )
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertEqual(
            result.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_destroy_adoption_retains_changed_label_residual_identity(self):
        session = self.save_session(
            state=SessionState.FAILED,
            instance_id=None,
        )
        session = self.sessions.transition(
            session.session_id,
            SessionState.FAILED,
            now=self.clock(),
            residual_inventory=("old-paid-instance",),
        )
        self.provider.instances = [
            self.worker_instance("new-label-match", session.label),
            self.worker_instance("old-paid-instance", "changed-label"),
        ]

        result = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertNotEqual(result.state, SessionState.DESTROYED)
        self.assertIn("old-paid-instance", result.residual_inventory)
        self.assertEqual(
            [item["instance_id"] for item in self.provider.instances],
            ["old-paid-instance"],
        )
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_recovery_activation_retains_residual_across_later_absence(self):
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=self.clock(),
            residual_inventory=("old-paid-instance",),
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label),
            self.worker_instance("old-paid-instance", "changed-label"),
        ]

        first = asyncio.run(self.session_lifecycle().recover_sessions())[0]
        self.provider.instances = [
            self.worker_instance("old-paid-instance", "changed-label")
        ]
        second = asyncio.run(self.session_lifecycle().recover_sessions())[0]

        self.assertIn("old-paid-instance", first.residual_inventory)
        self.assertNotEqual(second.state, SessionState.DESTROYED)
        self.assertIn("old-paid-instance", second.residual_inventory)
        self.assertTrue(second.public_payload()["billing_may_continue"])

    def test_duplicate_label_recovery_retains_residual_across_later_absence(self):
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.READY,
            now=self.clock(),
            residual_inventory=("old-paid-instance",),
        )
        self.provider.instances = [
            self.worker_instance("label-match-a", session.label),
            self.worker_instance("label-match-b", session.label),
            self.worker_instance("old-paid-instance", "changed-label"),
        ]

        first = asyncio.run(self.session_lifecycle().recover_sessions())[0]
        self.provider.instances = [
            self.worker_instance("old-paid-instance", "changed-label")
        ]
        second = asyncio.run(self.session_lifecycle().recover_sessions())[0]

        self.assertEqual(first.state, SessionState.FAILED)
        self.assertIn("old-paid-instance", first.residual_inventory)
        self.assertNotEqual(second.state, SessionState.DESTROYED)
        self.assertIn("old-paid-instance", second.residual_inventory)
        self.assertTrue(second.public_payload()["billing_may_continue"])

    def test_destroy_inventory_outage_cas_preserves_concurrent_residual(self):
        session = self.save_session(state=SessionState.FAILED)
        session = self.sessions.transition(
            session.session_id,
            SessionState.FAILED,
            now=self.clock(),
            residual_inventory=("old-paid-instance",),
        )
        lifecycle = self.session_lifecycle()

        async def scenario():
            inventory_started = asyncio.Event()
            release_inventory = asyncio.Event()

            async def blocking_inventory(api_key):
                self.provider.calls.append(("list", api_key))
                inventory_started.set()
                await release_inventory.wait()
                raise RuntimeError("synthetic inventory outage")

            self.provider.list_instances = blocking_inventory
            cleanup = asyncio.create_task(
                lifecycle.destroy_session(session.session_id)
            )
            await inventory_started.wait()
            current = self.sessions.get(session.session_id)
            self.sessions.transition(
                current.session_id,
                current.state,
                now=self.clock(),
                residual_inventory=tuple(
                    sorted(
                        set(current.residual_inventory)
                        | {"new-paid-instance"}
                    )
                ),
            )
            release_inventory.set()
            return await cleanup

        result = asyncio.run(scenario())

        self.assertIn("new-paid-instance", result.residual_inventory)
        self.assertEqual(result.instance_id, session.instance_id)
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_destroy_request_recovery_precedes_inventory_and_falls_back_safely(self):
        self.session_service = FailingTeardownSessionService(self.sessions)
        session = self.save_session(state=SessionState.READY)
        session = self.sessions.transition(
            session.session_id,
            SessionState.DESTROY_REQUESTED,
            now=self.clock(),
            destroy_requested=True,
        )
        self.provider.instances = [
            self.worker_instance(session.instance_id, session.label)
        ]
        self.provider.list_error = RuntimeError("synthetic inventory outage")

        recovered = asyncio.run(self.session_lifecycle().recover_sessions())

        self.assertEqual(
            self.session_service.resume_calls,
            [session.session_id],
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].state, SessionState.FAILED)
        self.assertEqual(recovered[0].instance_id, session.instance_id)
        self.assertIn(
            session.instance_id,
            recovered[0].residual_inventory,
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )
        self.assertTrue(
            recovered[0].public_payload()["billing_may_continue"]
        )

    def test_destroy_requires_every_residual_id_absent_even_if_label_changed(self):
        session = self.save_session(
            state=SessionState.FAILED,
            instance_id=None,
        )
        session = self.sessions.transition(
            session.session_id,
            SessionState.FAILED,
            now=self.clock(),
            residual_inventory=("residual-instance",),
        )
        self.provider.instances = [
            self.worker_instance("residual-instance", "changed-label")
        ]

        result = asyncio.run(
            self.session_lifecycle().destroy_session(session.session_id)
        )

        self.assertNotEqual(result.state, SessionState.DESTROYED)
        self.assertEqual(
            result.residual_inventory,
            ("residual-instance",),
        )
        self.assertEqual(result.provider_token, session.provider_token)
        self.assertTrue(result.public_payload()["billing_may_continue"])

    def test_bootstrap_retries_one_early_boundary_authentication_response(self):
        self.session_service = TransientAuthenticationSessionService(
            self.sessions
        )
        session = self.save_session()
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        lifecycle = self.session_lifecycle()

        ready = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(ready.state, SessionState.READY)
        self.assertEqual(len(self.session_service.bootstrap_calls), 2)
        self.assertEqual(self.clock.sleeps, [5])
        self.assertNotIn(
            "destroy",
            [call[0] for call in self.provider.calls],
        )

    def test_persistent_boot_authentication_failure_destroys_after_grace(self):
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
        self.assertEqual(self.clock.sleeps, [5.0] * 12)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["create", *("get" for _ in range(13)), "destroy", "list"],
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

    def test_manual_terminal_failure_waits_for_reviewed_destroy(self):
        self.session_service = ImmediateTerminalSessionService(self.sessions)
        session = self.save_session(deadline_mode="none")
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]
        lifecycle = self.session_lifecycle()

        failed = asyncio.run(
            lifecycle.wait_until_session_ready(session.session_id)
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertFalse(failed.destroy_requested)
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertTrue(failed.public_payload()["can_destroy"])
        self.assertEqual(
            failed.sanitized_error,
            ImmediateTerminalSessionService.diagnostic,
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["get"],
        )

        destroyed = asyncio.run(
            lifecycle.destroy_session(session.session_id)
        )

        self.assertEqual(destroyed.state, SessionState.DESTROYED)
        self.assertFalse(
            destroyed.public_payload()["billing_may_continue"]
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["get", "destroy", "list"],
        )

    def test_manual_boot_timeout_waits_for_reviewed_destroy(self):
        session = self.save_session(deadline_mode="none")
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        failed = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertFalse(failed.destroy_requested)
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertTrue(failed.public_payload()["can_destroy"])
        self.assertEqual(self.provider.calls, [])

    def test_manual_terminal_recovery_waits_for_reviewed_destroy(self):
        self.session_service = TerminalRecoverySessionService(self.sessions)
        session = self.save_session(
            state=SessionState.READY,
            deadline_mode="none",
        )
        self.provider.instances = [
            self.worker_instance("instance-1", session.label)
        ]

        recovered = asyncio.run(
            self.session_lifecycle().recover_sessions()
        )

        failed = self.sessions.get(session.session_id)
        self.assertEqual(recovered, [failed])
        self.assertEqual(failed.state, SessionState.FAILED)
        self.assertEqual(failed.instance_id, "instance-1")
        self.assertEqual(failed.provider_token, session.provider_token)
        self.assertEqual(
            failed.session_secret_hex,
            session.session_secret_hex,
        )
        self.assertFalse(failed.destroy_requested)
        self.assertTrue(failed.public_payload()["billing_may_continue"])
        self.assertTrue(failed.public_payload()["can_destroy"])
        self.assertEqual(
            failed.sanitized_error,
            TerminalRecoverySessionService.diagnostic,
        )
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["list"],
        )
        self.assertEqual(self.session_service.terminal_destroy_calls, [])

    def test_terminal_destroy_exception_keeps_residual_billing_warning(self):
        self.session_service = ImmediateTerminalSessionService(self.sessions)
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
        self.session_service = ImmediateTerminalSessionService(self.sessions)
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
        self.session_service = ImmediateTerminalSessionService(self.sessions)
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

    def test_session_boot_failure_with_historical_limit_two_never_searches_or_creates(self):
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

        result = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertEqual(result.retry_count, 0)
        self.assertIsNone(result.instance_id)
        self.assertIsNone(result.provider_token)
        self.assertIsNone(result.session_secret_hex)
        self.assertEqual(result.quote.offer_id, "42")
        self.assertEqual(result.quote.max_instance_creates, 2)
        self.assertEqual(
            result.sanitized_error,
            "The managed worker did not become ready.",
        )
        actions = [call[0] for call in self.provider.calls]
        self.assertEqual(actions, ["destroy", "list"])
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_session_boot_failure_never_rotates_boundary_for_a_second_create(self):
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
            result = asyncio.run(
                self.session_lifecycle().handle_session_boot_failure(
                    session.session_id,
                    failure_code="boot_timeout",
                )
            )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertIsNone(result.provider_token)
        self.assertIsNone(result.session_secret_hex)
        self.assertEqual(self.provider.create_boundaries, [])
        rendered = repr(result)
        self.assertNotIn("provider_token", rendered)
        self.assertNotIn("session_secret_hex", rendered)
        self.assertNotIn("provider_token", result.public_payload())

    def test_session_boot_failure_never_invokes_ambiguous_second_create(self):
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
        self.assertEqual(failed.residual_inventory, ())
        self.assertFalse(failed.public_payload()["billing_may_continue"])
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            0,
        )

        repeated = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(repeated.state, SessionState.FAILED)
        self.assertIsNone(repeated.instance_id)
        self.assertEqual(repeated.residual_inventory, ())
        self.assertFalse(repeated.public_payload()["billing_may_continue"])
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            0,
        )

    def test_session_failure_never_searches_connection_floor_replacements(self):
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
            "The managed worker did not become ready.",
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "search"],
            [],
        )
        self.assertEqual(
            [call for call in self.provider.calls if call[0] == "create"],
            [],
        )

    def test_limit_one_destroys_failed_boot_without_offer_search(self):
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
            "The managed worker did not become ready.",
        )

    def test_boot_failure_cannot_enter_ambiguous_second_create(self):
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

        result = asyncio.run(
            self.session_lifecycle().handle_session_boot_failure(
                session.session_id,
                failure_code="boot_timeout",
            )
        )

        self.assertEqual(result.state, SessionState.FAILED)
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.retry_count, 0)
        self.assertEqual(result.quote.max_instance_creates, 2)
        self.assertEqual(
            [call[0] for call in self.provider.calls],
            ["destroy", "list"],
        )
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            0,
        )

    def test_attempt_boot_failure_cannot_enter_ambiguous_second_create(self):
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
        self.assertEqual(failed.residual_inventory, ())
        self.assertEqual(
            len([call for call in self.provider.calls if call[0] == "create"]),
            0,
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

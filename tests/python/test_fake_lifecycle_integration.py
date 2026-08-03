import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from cloud_run.lifecycle import CloudRunLifecycle
from cloud_run.models import AttemptState, CloudSession, SessionState
from cloud_run.offers import HostBlacklist
from cloud_run.repository import AttemptRepository, SessionRepository
from cloud_run.service import CloudRunService
from cloud_run.session_service import TerminalProvisioningError
from cloud_run.settings import SettingsStore
from cloud_run.worker_release import WorkerRelease


def worker_release():
    return WorkerRelease.from_payload(
        {
            "schema_version": 1,
            "template_hash_id": "1" * 32,
            "worker_commit": "a" * 40,
            "worker_archive_sha256": "b" * 64,
            "protocol_version": "2",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "python_version": "3.12",
            "worker_port": 8765,
        }
    )


class PrivateBoundaryContext:
    __slots__ = ("boundary_token", "session_id")

    def __init__(self, boundary_token, session_id):
        self.boundary_token = boundary_token
        self.session_id = session_id

    def __repr__(self):
        return "PrivateBoundaryContext(<redacted>)"


class OfflineVast:
    def __init__(self):
        self.create_count = 0
        self.create_boundaries = []
        self.destroy_count = 0
        self.instances = []
        self.offer = {
            "offer_id": 42,
            "gpu_name": "RTX 4090",
            "gpu_ram_gb": 24.0,
            "dph_total": 0.42,
            "reliability": 0.99,
            "machine_id": "machine-7",
            "host_id": "host-3",
            "public_ipaddr": "8.8.8.8",
            "inet_down_mbps": 500.0,
            "disk_bw_mbps": 600.0,
            "inet_down_cost": None,
            "inet_up_cost": None,
        }

    async def search_offers(
        self,
        _api_key,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        if (
            self.offer["dph_total"] <= max_price_per_hour
            and self.offer["gpu_ram_gb"] >= min_vram_gb
        ):
            return [dict(self.offer)]
        return []

    async def get_offer(
        self,
        _api_key,
        offer_id,
        *,
        max_price_per_hour,
        min_vram_gb,
        disk_gb,
    ):
        if (
            str(self.offer["offer_id"]) == str(offer_id)
            and self.offer["dph_total"] <= max_price_per_hour
            and self.offer["gpu_ram_gb"] >= min_vram_gb
        ):
            return dict(self.offer)
        return None

    async def create_instance(
        self,
        _api_key,
        *,
        offer_id,
        disk_gb,
        label,
        release,
        boundary_token,
        session_id,
    ):
        self.create_count += 1
        self.asserted_create = (str(offer_id), disk_gb, label)
        self.create_boundaries.append(
            PrivateBoundaryContext(boundary_token, session_id)
        )
        self.instances = [
            {
                "instance_id": "900",
                "label": label,
                "actual_status": "loading",
                "public_ipaddr": "8.8.8.8",
                "ports": {"8188/tcp": [{"HostPort": "32100"}]},
            }
        ]
        return "900"

    async def list_instances(self, _api_key):
        return [dict(instance) for instance in self.instances]

    async def get_instance(self, _api_key, instance_id):
        return next(
            (
                dict(instance)
                for instance in self.instances
                if instance["instance_id"] == str(instance_id)
            ),
            None,
        )

    async def destroy_instance(self, _api_key, instance_id):
        self.destroy_count += 1
        self.instances = [
            instance
            for instance in self.instances
            if instance["instance_id"] != str(instance_id)
        ]
        return True


class FullOfflineLifecycleTests(unittest.TestCase):
    def test_quote_confirm_ready_duplicate_destroy_and_reopen(self):
        async def scenario(data_directory):
            sensitive_key = "synthetic-offline-only-value"
            settings = SettingsStore(data_directory)
            settings.update(
                {
                    "api_key": sensitive_key,
                    "max_price_per_hour": 0.55,
                    "min_vram_gb": 24,
                }
            )
            repository = AttemptRepository(
                data_directory / "attempts.sqlite3"
            )
            blacklist = HostBlacklist(
                data_directory / "host-blacklist.json"
            )
            provider = OfflineVast()
            service = CloudRunService(
                settings,
                repository,
                provider=provider,
                blacklist=blacklist,
                clock=lambda: 100.0,
                release=worker_release(),
            )
            lifecycle = CloudRunLifecycle(
                settings,
                repository,
                provider=provider,
                blacklist=blacklist,
                clock=lambda: 101.0,
                readiness_probe=lambda _url: asyncio.sleep(0, result=True),
                release=worker_release(),
            )

            offers = await service.search()
            self.assertEqual(provider.create_count, 0)
            quoted = await service.preview_offer(
                offer_id=offers[0]["offer_id"],
                idempotency_key="offline-browser-idempotency-key",
            )
            self.assertEqual(quoted.state, AttemptState.OFFER_SELECTED)
            self.assertEqual(provider.create_count, 0)

            starting = await service.confirm(
                quoted.attempt_id,
                idempotency_key="offline-browser-idempotency-key",
            )
            self.assertEqual(starting.state, AttemptState.STARTING)
            self.assertEqual(provider.create_count, 1)
            provider.instances[0]["actual_status"] = "running"

            ready = await lifecycle.reconcile_once(quoted.attempt_id)
            self.assertEqual(ready.state, AttemptState.READY)
            self.assertEqual(ready.ready_url, "http://8.8.8.8:32100")
            duplicate = await service.confirm(
                quoted.attempt_id,
                idempotency_key="offline-browser-idempotency-key",
            )
            self.assertEqual(duplicate.state, AttemptState.READY)
            self.assertEqual(provider.create_count, 1)

            cancelled = await lifecycle.destroy(quoted.attempt_id)
            self.assertEqual(cancelled.state, AttemptState.CANCELLED)
            self.assertEqual(provider.destroy_count, 1)
            self.assertEqual(provider.instances, [])
            public = json.dumps(cancelled.public_payload(), sort_keys=True)
            self.assertNotIn(sensitive_key, public)
            self.assertNotIn("idempotency", public)

            reopened = AttemptRepository(
                data_directory / "attempts.sqlite3"
            )
            self.assertEqual(
                reopened.get(quoted.attempt_id).state,
                AttemptState.CANCELLED,
            )
            recovered = await CloudRunLifecycle(
                settings,
                reopened,
                provider=provider,
                blacklist=blacklist,
                release=worker_release(),
            ).recover()
            self.assertEqual(recovered, [])
            self.assertEqual(provider.create_count, 1)

        with tempfile.TemporaryDirectory() as temporary_directory:
            asyncio.run(scenario(Path(temporary_directory) / "private"))

    def test_terminal_session_provisioning_destroys_fake_inventory_once(self):
        async def scenario(data_directory):
            settings = SettingsStore(data_directory)
            settings.update(
                {
                    "api_key": "synthetic-offline-only-value",
                    "max_price_per_hour": 0.55,
                    "min_vram_gb": 24,
                }
            )
            attempts = AttemptRepository(
                data_directory / "attempts.sqlite3"
            )
            sessions = SessionRepository(
                data_directory / "attempts.sqlite3"
            )
            provider = OfflineVast()
            session = CloudSession.new(
                "terminal-offline-key",
                session_id="terminal-offline-session",
                manifest_digest="c" * 64,
                deadline_at=7_300.0,
                deadline_mode="finite",
                disk_gb=80,
                now=100.0,
                state=SessionState.BOOTSTRAPPING,
            ).transition(
                SessionState.BOOTSTRAPPING,
                now=100.0,
                instance_id="900",
                provider_token="a" * 64,
                session_secret_hex="d" * 64,
            )
            sessions.create_or_get(session)
            provider.instances = [
                {
                    "instance_id": "900",
                    "label": session.label,
                    "actual_status": "running",
                    "public_ipaddr": "8.8.8.8",
                    "ports": {"8765/tcp": [{"HostPort": "32100"}]},
                    "jupyter_token": "f" * 64,
                }
            ]
            sleeps = []

            class TerminalService:
                async def bootstrap_session(inner_self, _session_id):
                    raise TerminalProvisioningError(
                        "Remote provisioning response was invalid."
                    )

            async def sleep(seconds):
                sleeps.append(seconds)

            lifecycle = CloudRunLifecycle(
                settings,
                attempts,
                provider=provider,
                blacklist=HostBlacklist(
                    data_directory / "host-blacklist.json"
                ),
                clock=lambda: 101.0,
                sleep=sleep,
                release=worker_release(),
                session_repository=sessions,
                session_service=TerminalService(),
            )

            destroyed = await lifecycle.wait_until_session_ready(
                session.session_id
            )

            self.assertEqual(destroyed.state, SessionState.DESTROYED)
            self.assertEqual(provider.destroy_count, 1)
            self.assertEqual(provider.instances, [])
            self.assertEqual(sleeps, [])
            self.assertEqual(
                destroyed.sanitized_error,
                "Remote provisioning response was invalid.",
            )
            self.assertFalse(
                destroyed.public_payload()["billing_may_continue"]
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            asyncio.run(scenario(Path(temporary_directory) / "private"))


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloud_run.worker_protocol import sign_request

class FakeContainerProvider:
    def __init__(self):
        self.calls = []

    async def delete(self, url, api_key):
        self.calls.append(("DELETE", url, api_key))
        return 200


class WorkerDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.state import WorkerStateStore

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = (
            Path(temporary.name) / "private" / "worker-state.json"
        )
        self.state = WorkerStateStore(self.path)
        self.state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )

    async def test_deadline_uses_only_own_instance_credentials_and_delete(self):
        from remote_worker.deadline import DeadlineWatchdog

        provider = FakeContainerProvider()
        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=provider,
            state=self.state,
            clock=lambda: 200,
        )

        await watchdog.enforce(
            deadline_at=199,
            mode="finite",
            retrieval_grace_seconds=0,
        )

        self.assertEqual(
            provider.calls,
            [
                (
                    "DELETE",
                    "https://console.vast.ai/api/v0/instances/77/",
                    "instance-key",
                )
            ],
        )
        record = self.state.load()["transactions"]["deadline"]
        self.assertTrue(record["destroy_intent"])
        self.assertTrue(record["destroy_requested"])
        self.assertNotIn("77", repr(record))
        self.assertNotIn("instance-key", repr(record))

    async def test_grace_begins_only_after_durable_destroy_intent(self):
        from remote_worker.deadline import DeadlineWatchdog

        provider = FakeContainerProvider()
        observed = []

        async def sleeper(seconds):
            record = self.state.load()["transactions"]["deadline"]
            observed.append(
                (
                    seconds,
                    record["destroy_intent"],
                    record["destroy_requested"],
                )
            )

        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=provider,
            state=self.state,
            clock=lambda: 200,
            sleeper=sleeper,
        )
        await watchdog.enforce(
            deadline_at=199,
            mode="finite",
            retrieval_grace_seconds=30,
        )

        self.assertEqual(observed, [(30, True, False)])
        self.assertEqual(len(provider.calls), 1)

    async def test_no_limit_requires_explicit_acknowledgement(self):
        from remote_worker.deadline import (
            DeadlineValidationError,
            DeadlineWatchdog,
        )

        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=FakeContainerProvider(),
            state=self.state,
            clock=lambda: 100,
        )
        with self.assertRaises(DeadlineValidationError):
            watchdog.update(
                {"mode": "none", "acknowledged": False}
            )
        update = watchdog.update(
            {"mode": "none", "acknowledged": True}
        )

        self.assertEqual(update.mode, "none")
        self.assertIsNone(update.deadline_at)
        saved = self.state.load()
        self.assertEqual(saved["deadline_mode"], "none")
        self.assertIsNone(saved["deadline_at"])
        self.assertFalse(
            saved["transactions"]["deadline"]["destroy_intent"]
        )

    async def test_finite_update_is_future_bounded_and_durable(self):
        from remote_worker.deadline import (
            DeadlineValidationError,
            DeadlineWatchdog,
        )

        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=FakeContainerProvider(),
            state=self.state,
            clock=lambda: 100,
        )
        for payload in (
            {
                "mode": "finite",
                "deadline_at": 100,
                "retrieval_grace_seconds": 0,
            },
            {
                "mode": "finite",
                "deadline_at": 200,
                "retrieval_grace_seconds": 301,
            },
            {
                "mode": "finite",
                "deadline_at": 200,
                "retrieval_grace_seconds": 0,
                "command": "curl x | sh",
            },
        ):
            with self.assertRaises(DeadlineValidationError):
                watchdog.update(payload)

        update = watchdog.update(
            {
                "mode": "finite",
                "deadline_at": 200,
                "retrieval_grace_seconds": 45,
            }
        )
        self.assertEqual(update.deadline_at, 200.0)
        self.assertEqual(update.retrieval_grace_seconds, 45)
        saved = self.state.load()
        self.assertEqual(saved["deadline_at"], 200.0)
        self.assertEqual(saved["deadline_mode"], "finite")
        self.assertEqual(
            saved["transactions"]["deadline"][
                "retrieval_grace_seconds"
            ],
            45,
        )

    async def test_environment_factory_reads_only_instance_scope(self):
        from remote_worker.deadline import deadline_watchdog

        provider = FakeContainerProvider()
        environment = {
            "CONTAINER_ID": "77",
            "CONTAINER_API_KEY": "instance-key",
            "VAST_API_KEY": "must-never-be-read",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            watchdog = deadline_watchdog(
                state=self.state,
                provider=provider,
                clock=lambda: 200,
            )
        await watchdog.enforce(
            deadline_at=199,
            mode="finite",
            retrieval_grace_seconds=0,
        )

        self.assertEqual(provider.calls[0][2], "instance-key")
        self.assertNotIn("must-never-be-read", repr(watchdog))

    async def test_signed_deadline_route_persists_and_arms_watchdog(self):
        from remote_worker.deadline import DeadlineWatchdog
        from remote_worker.server import WorkerApplication

        provider = FakeContainerProvider()
        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=provider,
            state=self.state,
            clock=lambda: 1000,
        )
        worker = WorkerApplication(
            state_path=self.path,
            clock=lambda: 1000,
            deadline_watchdog=watchdog,
        )
        payload = {
            "mode": "finite",
            "deadline_at": 2000,
            "retrieval_grace_seconds": 30,
        }
        body = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        envelope = sign_request(
            bytes.fromhex("a" * 64),
            "PUT",
            "/worker/v1/deadline",
            body,
            timestamp=1000,
            nonce="deadline-route-1",
        )

        class Request:
            method = "PUT"
            path = "/worker/v1/deadline"
            path_qs = path
            boundary_authenticated = True
            auth_envelope = envelope
            headers = {}

        request = Request()
        request.body = body
        response = await worker.handle(request)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["mode"], "finite")
        self.assertEqual(response.payload["deadline_at"], 2000.0)
        self.assertEqual(
            self.state.load()["transactions"]["deadline"]["mode"],
            "finite",
        )
        self.assertIsNotNone(watchdog._task)
        await worker.close()
        self.assertEqual(provider.calls, [])

    async def test_restart_rearms_expired_durable_deadline_without_mac(self):
        from remote_worker.deadline import DeadlineWatchdog

        configured = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=FakeContainerProvider(),
            state=self.state,
            clock=lambda: 100,
        )
        configured.update(
            {
                "mode": "finite",
                "deadline_at": 150,
                "retrieval_grace_seconds": 0,
            }
        )

        provider = FakeContainerProvider()
        reopened = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=provider,
            state=self.state,
            clock=lambda: 200,
        )
        await reopened.watch()

        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(
            self.state.load()["transactions"]["deadline"][
                "destroy_requested"
            ]
        )

    async def test_restart_uses_only_remaining_retrieval_grace(self):
        from remote_worker.deadline import DeadlineWatchdog

        self.state.record_deadline(
            mode="finite",
            deadline_at=150,
            retrieval_grace_seconds=30,
            destroy_intent=True,
            destroy_intent_at=180,
            destroy_requested=False,
            updated_at=180,
        )
        sleeps = []

        async def sleeper(seconds):
            sleeps.append(seconds)

        provider = FakeContainerProvider()
        reopened = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=provider,
            state=self.state,
            clock=lambda: 200,
            sleeper=sleeper,
        )
        await reopened.enforce(
            deadline_at=150,
            mode="finite",
            retrieval_grace_seconds=30,
        )

        self.assertEqual(sleeps, [10.0])
        self.assertEqual(len(provider.calls), 1)

    async def test_expired_policy_cannot_be_extended_or_disabled(self):
        from remote_worker.deadline import (
            DeadlineValidationError,
            DeadlineWatchdog,
        )

        self.state.record_deadline(
            mode="finite",
            deadline_at=150,
            retrieval_grace_seconds=0,
            destroy_intent=False,
            destroy_intent_at=None,
            destroy_requested=False,
            updated_at=100,
        )
        watchdog = DeadlineWatchdog(
            container_id="77",
            container_api_key="instance-key",
            provider=FakeContainerProvider(),
            state=self.state,
            clock=lambda: 151,
        )
        for payload in (
            {
                "mode": "finite",
                "deadline_at": 300,
                "retrieval_grace_seconds": 0,
            },
            {"mode": "none", "acknowledged": True},
        ):
            with self.assertRaises(DeadlineValidationError):
                watchdog.update(payload)


if __name__ == "__main__":
    unittest.main()

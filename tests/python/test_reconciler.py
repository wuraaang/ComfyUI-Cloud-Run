import asyncio
import tempfile
import unittest
from pathlib import Path

from cloud_run.job_repository import JobRepository
from cloud_run.orchestrator import LocalOrchestrator
from cloud_run.run_errors import RunErrorCode, RunPhase


class BlockingReconciliationService:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def reconcile_session_once(self, session_id):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return session_id

    def reconciliation_context(self, session_id):
        return {
            "session_id": session_id,
            "manifest_digest": "a" * 64,
            "job_id": "job-1",
            "event_cursor": 0,
            "output_state": "pending",
        }


class FailingReconciliationService(BlockingReconciliationService):
    async def reconcile_session_once(self, session_id):
        self.calls += 1
        self.started.set()
        raise RuntimeError("private transport failure")


class SessionReconcilerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = JobRepository(
            Path(temporary.name) / "attempts.sqlite3"
        )
        self.orchestrator = LocalOrchestrator(self.repository)

    async def test_schedule_is_single_flight_per_session(self):
        from cloud_run.reconciler import SessionReconciler

        service = BlockingReconciliationService()
        reconciler = SessionReconciler(
            service=service,
            orchestrator=self.orchestrator,
            clock=lambda: 10.0,
            id_factory=lambda: "correlation-1",
            max_retries=0,
        )

        tasks = [reconciler.schedule("session-1") for _ in range(5)]
        await service.started.wait()

        self.assertTrue(all(task is tasks[0] for task in tasks))
        self.assertEqual(service.calls, 1)
        service.release.set()
        self.assertEqual(await tasks[0], "session-1")
        await reconciler.close()

    async def test_unexpected_failure_is_observed_and_journaled_once(self):
        from cloud_run.reconciler import SessionReconciler

        service = FailingReconciliationService()
        reconciler = SessionReconciler(
            service=service,
            orchestrator=self.orchestrator,
            clock=lambda: 10.0,
            id_factory=iter(("correlation-1", "entry-1")).__next__,
            max_retries=0,
        )
        loop = asyncio.get_running_loop()
        unhandled = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: unhandled.append(context)
        )
        self.addAsyncCleanup(reconciler.close)
        try:
            task = reconciler.schedule("session-1")
            await service.started.wait()
            for _attempt in range(3):
                await asyncio.sleep(0)
            self.assertTrue(task.done())
            self.assertEqual(unhandled, [])
        finally:
            loop.set_exception_handler(previous_handler)

        entries = self.orchestrator.entries("session-1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].phase, RunPhase.SYNCHRONIZATION)
        self.assertEqual(entries[0].code, RunErrorCode.SYNCHRONIZATION)
        self.assertEqual(entries[0].message, "Cloud Vast reconciliation failed.")
        self.assertEqual(
            entries[0].details,
            {"correlation_id": "correlation-1", "retryable": True},
        )
        self.assertNotIn("private transport", repr(entries))


if __name__ == "__main__":
    unittest.main()

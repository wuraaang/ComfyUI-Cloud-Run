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


class BlockingHarvestService(BlockingReconciliationService):
    def __init__(self):
        super().__init__()
        self.context_calls = []
        self.start_job_calls = 0
        self.prompt_calls = 0

    def harvest_retry_context(self, job_id):
        self.context_calls.append(job_id)
        return {
            "session_id": "session-1",
            "job_id": job_id,
        }

    async def start_job(self, *_args, **_kwargs):
        self.start_job_calls += 1
        raise AssertionError("Harvest retry must not start a GPU job.")

    async def prompt(self, *_args, **_kwargs):
        self.prompt_calls += 1
        raise AssertionError("Harvest retry must not submit a prompt.")


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

    async def test_preempt_cancels_inflight_reconciliation_and_pending_retry(self):
        from cloud_run.reconciler import SessionReconciler

        blocking = BlockingReconciliationService()
        active = SessionReconciler(
            service=blocking,
            orchestrator=self.orchestrator,
            max_retries=1,
            retry_delay_seconds=60,
        )
        self.addAsyncCleanup(active.close)
        active_task = active.schedule("active-session")
        await blocking.started.wait()

        await active.preempt("active-session")

        self.assertTrue(active_task.cancelled())
        self.assertNotIn("active-session", active._tasks)

        failing = FailingReconciliationService()
        retrying = SessionReconciler(
            service=failing,
            orchestrator=self.orchestrator,
            max_retries=1,
            retry_delay_seconds=60,
        )
        self.addAsyncCleanup(retrying.close)
        failed_task = retrying.schedule("retry-session")
        await failing.started.wait()
        with self.assertRaises(RuntimeError):
            await failed_task
        for _attempt in range(3):
            await asyncio.sleep(0)
        self.assertIn("retry-session", retrying._retry_handles)

        await retrying.preempt("retry-session")
        await asyncio.sleep(0)

        self.assertNotIn("retry-session", retrying._retry_handles)
        self.assertNotIn("retry-session", retrying._retry_counts)
        self.assertEqual(failing.calls, 1)

    async def test_preempt_is_idempotent_and_not_recorded_as_retryable_failure(self):
        from cloud_run.reconciler import SessionReconciler

        service = BlockingReconciliationService()
        reconciler = SessionReconciler(
            service=service,
            orchestrator=self.orchestrator,
            max_retries=2,
        )
        self.addAsyncCleanup(reconciler.close)
        task = reconciler.schedule("session-1")
        await service.started.wait()

        await reconciler.preempt("session-1")
        await reconciler.preempt("session-1")
        await asyncio.sleep(0)

        self.assertTrue(task.cancelled())
        self.assertEqual(service.calls, 1)
        self.assertEqual(self.orchestrator.entries("session-1"), ())
        self.assertEqual(reconciler._tasks, {})
        self.assertEqual(reconciler._retry_handles, {})


class ReconcilerOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repository = JobRepository(
            Path(temporary.name) / "attempts.sqlite3"
        )
        self.orchestrator = LocalOrchestrator(self.repository)

    async def test_harvest_retry_is_single_flight_and_never_starts_execution(self):
        from cloud_run.reconciler import SessionReconciler

        service = BlockingHarvestService()
        reconciler = SessionReconciler(
            service=service,
            orchestrator=self.orchestrator,
            max_retries=0,
        )
        self.addAsyncCleanup(reconciler.close)

        retries = [
            asyncio.create_task(reconciler.retry_harvest("job-1"))
            for _attempt in range(5)
        ]
        await service.started.wait()

        self.assertEqual(service.calls, 1)
        self.assertEqual(service.start_job_calls, 0)
        self.assertEqual(service.prompt_calls, 0)
        service.release.set()
        self.assertEqual(
            await asyncio.gather(*retries),
            ["session-1"] * 5,
        )
        self.assertEqual(service.start_job_calls, 0)
        self.assertEqual(service.prompt_calls, 0)


if __name__ == "__main__":
    unittest.main()

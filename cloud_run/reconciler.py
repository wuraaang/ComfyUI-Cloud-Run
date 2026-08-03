"""Single-flight durable reconciliation for remote Cloud Vast jobs."""

from __future__ import annotations

import asyncio
import math
import re
import time
import uuid

from .run_errors import (
    RunErrorCode,
    RunJournalEntry,
    RunPhase,
)


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


class SessionReconciler:
    def __init__(
        self,
        *,
        service,
        orchestrator,
        clock=None,
        id_factory=None,
        max_retries=2,
        retry_delay_seconds=1.0,
    ):
        if not callable(getattr(service, "reconcile_session_once", None)):
            raise ValueError("Cloud Vast reconciliation service is invalid.")
        if not callable(getattr(orchestrator, "record", None)):
            raise ValueError("Cloud Vast journal boundary is invalid.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= 10
            or isinstance(retry_delay_seconds, bool)
            or not isinstance(retry_delay_seconds, (int, float))
            or not math.isfinite(retry_delay_seconds)
            or not 0 <= retry_delay_seconds <= 300
        ):
            raise ValueError("Cloud Vast retry policy is invalid.")
        self.service = service
        self.orchestrator = orchestrator
        self.clock = clock or time.time
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.max_retries = max_retries
        self.retry_delay_seconds = float(retry_delay_seconds)
        self._tasks = {}
        self._retry_counts = {}
        self._retry_handles = {}
        self._closing = False

    @staticmethod
    def _session_id(value):
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise ValueError("Cloud Vast session identity is invalid.")
        return value

    def _now(self):
        value = self.clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("Cloud Vast reconciliation clock is invalid.")
        return float(value)

    def _identifier(self):
        try:
            value = self.id_factory()
        except Exception:
            value = None
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            value = uuid.uuid4().hex
        return value

    def _context(self, session_id):
        context_method = getattr(self.service, "reconciliation_context", None)
        try:
            context = (
                context_method(session_id)
                if callable(context_method)
                else {}
            )
        except Exception:
            context = {}
        if not isinstance(context, dict):
            context = {}
        manifest_digest = context.get("manifest_digest")
        if not isinstance(manifest_digest, str) or not _HEX_64.fullmatch(
            manifest_digest
        ):
            manifest_digest = None
        job_id = context.get("job_id")
        if not isinstance(job_id, str) or not _IDENTIFIER.fullmatch(job_id):
            job_id = None
        cursor = context.get("event_cursor", 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            cursor = 0
        output_state = context.get("output_state")
        if (
            output_state is not None
            and (
                not isinstance(output_state, str)
                or not _IDENTIFIER.fullmatch(output_state)
            )
        ):
            output_state = None
        return {
            "manifest_digest": manifest_digest,
            "job_id": job_id,
            "event_cursor": cursor,
            "output_state": output_state,
        }

    def _record_failure(self, session_id, *, cancelled=False):
        try:
            context = self._context(session_id)
            correlation_id = self._identifier()
            entry_id = self._identifier()
            self.orchestrator.record(
                RunJournalEntry(
                    entry_id=entry_id,
                    session_id=session_id,
                    manifest_digest=context["manifest_digest"],
                    transaction_id=None,
                    job_id=context["job_id"],
                    phase=RunPhase.SYNCHRONIZATION,
                    code=RunErrorCode.SYNCHRONIZATION,
                    message=(
                        "Cloud Vast reconciliation was cancelled."
                        if cancelled
                        else "Cloud Vast reconciliation failed."
                    ),
                    node_id=None,
                    process_exit_code=None,
                    restart_count=0,
                    last_probe=None,
                    byte_cursor=0,
                    event_cursor=context["event_cursor"],
                    output_state=context["output_state"],
                    details={
                        "correlation_id": correlation_id,
                        "retryable": True,
                    },
                    created_at=self._now(),
                )
            )
        except Exception:
            return

    async def reconcile(self, session_id):
        identifier = self._session_id(session_id)
        return await self.service.reconcile_session_once(identifier)

    def schedule(self, session_id):
        identifier = self._session_id(session_id)
        existing = self._tasks.get(identifier)
        if existing is not None and not existing.done():
            return existing
        if self._closing:
            raise RuntimeError("Cloud Vast reconciler is closed.")
        task = asyncio.get_running_loop().create_task(
            self.reconcile(identifier),
            name="cloud-vast-reconcile-" + identifier,
        )
        self._tasks[identifier] = task
        task.add_done_callback(
            lambda done, identity=identifier: self._observe(identity, done)
        )
        return task

    def _retry(self, session_id):
        self._retry_handles.pop(session_id, None)
        if self._closing:
            return
        try:
            self.schedule(session_id)
        except (RuntimeError, ValueError):
            return

    def _observe(self, session_id, done):
        if self._tasks.get(session_id) is done:
            self._tasks.pop(session_id, None)
        try:
            result = done.result()
        except asyncio.CancelledError:
            if not self._closing:
                self._record_failure(session_id, cancelled=True)
            return
        except Exception:
            self._record_failure(session_id)
            retries = self._retry_counts.get(session_id, 0)
            if not self._closing and retries < self.max_retries:
                self._retry_counts[session_id] = retries + 1
                loop = done.get_loop()
                self._retry_handles[session_id] = loop.call_later(
                    self.retry_delay_seconds,
                    self._retry,
                    session_id,
                )
            return
        self._retry_counts.pop(session_id, None)
        return result

    async def recover(self):
        identities = getattr(self.service, "recoverable_session_ids", None)
        if not callable(identities):
            return ()
        session_ids = identities()
        if not isinstance(session_ids, (tuple, list)):
            raise ValueError("Cloud Vast recovery set is invalid.")
        tasks = [self.schedule(session_id) for session_id in session_ids]
        if not tasks:
            return ()
        return tuple(
            await asyncio.gather(*tasks, return_exceptions=True)
        )

    async def before_teardown(self, session_id):
        identifier = self._session_id(session_id)
        existing = self._tasks.get(identifier)
        if existing is not None and not existing.done():
            await asyncio.shield(existing)
        return await self.reconcile(identifier)

    async def close(self):
        self._closing = True
        for handle in tuple(self._retry_handles.values()):
            handle.cancel()
        self._retry_handles.clear()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

"""Narrow durable metadata orchestration for Cloud Vast runs."""

from __future__ import annotations

from typing import Protocol

from .run_errors import RunJournalEntry


class OrchestratorPort(Protocol):
    def record(self, entry: RunJournalEntry) -> bool:
        ...

    def entries(
        self,
        session_id: str,
        *,
        after: float = 0.0,
    ) -> tuple[RunJournalEntry, ...]:
        ...


class LocalOrchestrator:
    def __init__(self, repository):
        self.repository = repository

    def record(self, entry):
        return self.repository.record_journal(entry)

    def entries(self, session_id, *, after=0.0):
        return tuple(
            self.repository.list_journal(session_id, after=after)
        )

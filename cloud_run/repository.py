"""Transactional persistence for managed Vast.ai attempts."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from .models import AttemptState, CloudAttempt, OfferQuote


class ConcurrentAttemptUpdate(RuntimeError):
    pass


_COLUMNS = """
    attempt_id, idempotency_key, label, state, quote_json, instance_id,
    ready_url, provider_token, retry_count, cancel_requested, sanitized_error,
    created_at, updated_at, version
"""


class AttemptRepository:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    quote_json TEXT NOT NULL,
                    instance_id TEXT,
                    ready_url TEXT,
                    provider_token TEXT,
                    retry_count INTEGER NOT NULL,
                    cancel_requested INTEGER NOT NULL,
                    sanitized_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL
                )
                """
            )
            connection.commit()
        os.chmod(self.path, 0o600)

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _quote_json(quote):
        return json.dumps(
            quote.to_record(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _row_to_attempt(row):
        if row is None:
            return None
        return CloudAttempt(
            attempt_id=row["attempt_id"],
            idempotency_key=row["idempotency_key"],
            label=row["label"],
            state=AttemptState(row["state"]),
            quote=OfferQuote.from_record(json.loads(row["quote_json"])),
            instance_id=row["instance_id"],
            ready_url=row["ready_url"],
            provider_token=row["provider_token"],
            retry_count=int(row["retry_count"]),
            cancel_requested=bool(row["cancel_requested"]),
            sanitized_error=row["sanitized_error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _values(attempt):
        return (
            attempt.attempt_id,
            attempt.idempotency_key,
            attempt.label,
            attempt.state.value,
            AttemptRepository._quote_json(attempt.quote),
            attempt.instance_id,
            attempt.ready_url,
            attempt.provider_token,
            attempt.retry_count,
            int(attempt.cancel_requested),
            attempt.sanitized_error,
            attempt.created_at,
            attempt.updated_at,
            attempt.version,
        )

    def get(self, attempt_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts WHERE attempt_id = ?",
                (str(attempt_id),),
            ).fetchone()
        return self._row_to_attempt(row)

    def get_by_idempotency_key(self, idempotency_key):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts WHERE idempotency_key = ?",
                (str(idempotency_key),),
            ).fetchone()
        return self._row_to_attempt(row)

    def create_or_get(self, attempt):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts WHERE idempotency_key = ?",
                (attempt.idempotency_key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._row_to_attempt(row), False
            try:
                connection.execute(
                    f"""
                    INSERT INTO attempts ({_COLUMNS})
                    VALUES ({",".join("?" for _ in range(14))})
                    """,
                    self._values(attempt),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    f"SELECT {_COLUMNS} FROM attempts WHERE idempotency_key = ?",
                    (attempt.idempotency_key,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise
                connection.commit()
                return self._row_to_attempt(row), False
            connection.commit()
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts WHERE attempt_id = ?",
                (attempt.attempt_id,),
            ).fetchone()
        return self._row_to_attempt(row), True

    def _save_in_transaction(self, connection, attempt):
        cursor = connection.execute(
            """
            UPDATE attempts SET
                state = ?,
                quote_json = ?,
                instance_id = ?,
                ready_url = ?,
                provider_token = ?,
                retry_count = ?,
                cancel_requested = ?,
                sanitized_error = ?,
                updated_at = ?,
                version = version + 1
            WHERE attempt_id = ? AND version = ?
            """,
            (
                attempt.state.value,
                self._quote_json(attempt.quote),
                attempt.instance_id,
                attempt.ready_url,
                attempt.provider_token,
                attempt.retry_count,
                int(attempt.cancel_requested),
                attempt.sanitized_error,
                attempt.updated_at,
                attempt.attempt_id,
                attempt.version,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrentAttemptUpdate(
                "The attempt changed while this operation was in progress."
            )
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM attempts WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        return self._row_to_attempt(row)

    def save(self, attempt):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                saved = self._save_in_transaction(connection, attempt)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return saved

    def transition(self, attempt_id, state, *, now=None, **changes):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts WHERE attempt_id = ?",
                (str(attempt_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(str(attempt_id))
            changed = self._row_to_attempt(row).transition(
                state,
                now=now,
                **changes,
            )
            try:
                saved = self._save_in_transaction(connection, changed)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return saved

    def list_all(self):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM attempts ORDER BY created_at, attempt_id"
            ).fetchall()
        return [self._row_to_attempt(row) for row in rows]

    def list_recoverable(self):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_COLUMNS}
                FROM attempts
                WHERE state != ?
                ORDER BY created_at, attempt_id
                """,
                (AttemptState.CANCELLED.value,),
            ).fetchall()
        return [self._row_to_attempt(row) for row in rows]

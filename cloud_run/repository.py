"""Transactional persistence for managed Vast.ai attempts."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from .models import (
    AttemptState,
    CloudAttempt,
    CloudSession,
    LEGACY_SESSION_STATES,
    OfferQuote,
    SessionState,
)


class ConcurrentAttemptUpdate(RuntimeError):
    pass


class ConcurrentSessionUpdate(RuntimeError):
    pass


_COLUMNS = """
    attempt_id, idempotency_key, label, state, quote_json, instance_id,
    ready_url, provider_token, retry_count, cancel_requested, sanitized_error,
    created_at, updated_at, version
"""

_SESSION_COLUMNS = """
    session_id, idempotency_key, label, state, quote_json, manifest_digest,
    installed_manifest_digest, instance_id, worker_base_url, provider_token,
    session_secret_hex, deadline_at, deadline_mode, disk_gb, retry_count,
    destroy_requested, residual_inventory_json, sanitized_error, created_at,
    updated_at, version
"""


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _private_connection(path):
    connection = sqlite3.connect(str(path), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _initialize_database(path):
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(database_path.parent, 0o700)
    with closing(_private_connection(database_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    quote_json TEXT,
                    manifest_digest TEXT,
                    installed_manifest_digest TEXT,
                    instance_id TEXT,
                    worker_base_url TEXT,
                    provider_token TEXT,
                    session_secret_hex TEXT,
                    deadline_at REAL,
                    deadline_mode TEXT NOT NULL DEFAULT 'finite',
                    disk_gb INTEGER NOT NULL DEFAULT 80,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    destroy_requested INTEGER NOT NULL DEFAULT 0,
                    residual_inventory_json TEXT,
                    sanitized_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    prompt_digest TEXT NOT NULL,
                    capture_json TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    remote_prompt_id TEXT,
                    sanitized_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL,
                    UNIQUE(session_id, idempotency_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS manifests (
                    manifest_digest TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    capture_id TEXT PRIMARY KEY,
                    prompt_digest TEXT NOT NULL,
                    capture_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS preflights (
                    preflight_id TEXT PRIMARY KEY,
                    capture_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                    job_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(job_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transfers (
                    job_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    expected_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    offset INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    private_path TEXT NOT NULL,
                    PRIMARY KEY(job_id, artifact_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provision_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    job_id TEXT,
                    manifest_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    planned_restart_count INTEGER NOT NULL,
                    repair_count INTEGER NOT NULL,
                    repair_restart_count INTEGER NOT NULL,
                    last_progress_at REAL NOT NULL,
                    sanitized_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS installed_dependencies (
                    session_id TEXT NOT NULL,
                    dependency_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    revision TEXT,
                    destination TEXT NOT NULL,
                    PRIMARY KEY(session_id, dependency_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    private_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dependency_candidates (
                    class_type TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    approved INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(class_type, candidate_digest)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dependency_mappings (
                    class_type TEXT PRIMARY KEY,
                    candidate_digest TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            _migrate_legacy_attempts(connection)
            connection.execute(
                """
                INSERT INTO schema_meta(key, value) VALUES('schema_version', '3')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
        except Exception:
            connection.rollback()
            raise
        connection.commit()
    os.chmod(database_path, 0o600)


def _migrate_legacy_attempts(connection):
    exists = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'attempts'
        """
    ).fetchone()
    if exists is None:
        return
    rows = connection.execute(
        """
        SELECT
            attempt_id, idempotency_key, label, state, quote_json,
            instance_id, ready_url, provider_token, retry_count,
            cancel_requested, sanitized_error, created_at, updated_at, version
        FROM attempts
        """
    ).fetchall()
    for row in rows:
        state = LEGACY_SESSION_STATES.get(
            str(row["state"]),
            SessionState.FAILED,
        )
        residual_inventory = (
            [str(row["instance_id"])]
            if state == SessionState.FAILED and row["instance_id"]
            else []
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO sessions (
                session_id, idempotency_key, label, state, quote_json,
                manifest_digest, installed_manifest_digest, instance_id,
                worker_base_url, provider_token, session_secret_hex,
                deadline_at, deadline_mode, disk_gb, retry_count,
                destroy_requested, residual_inventory_json, sanitized_error,
                created_at, updated_at, version
            ) VALUES (
                ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL,
                NULL, 'finite', 80, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                row["attempt_id"],
                row["idempotency_key"],
                row["label"],
                state.value,
                row["quote_json"],
                row["instance_id"],
                row["ready_url"],
                row["provider_token"],
                int(row["retry_count"]),
                int(row["cancel_requested"]),
                _canonical_json(residual_inventory),
                row["sanitized_error"],
                float(row["created_at"]),
                float(row["updated_at"]),
                int(row["version"]),
            ),
        )


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


class SessionRepository:
    def __init__(self, path):
        self.path = Path(path)
        _initialize_database(self.path)

    def _connect(self):
        return _private_connection(self.path)

    @staticmethod
    def _quote_json(quote):
        return _canonical_json(quote.to_record()) if quote is not None else None

    @staticmethod
    def _row_to_session(row):
        if row is None:
            return None
        quote_json = row["quote_json"]
        residual_json = row["residual_inventory_json"] or "[]"
        return CloudSession(
            session_id=row["session_id"],
            idempotency_key=row["idempotency_key"],
            label=row["label"],
            state=SessionState(row["state"]),
            quote=(
                OfferQuote.from_record(json.loads(quote_json))
                if quote_json is not None
                else None
            ),
            manifest_digest=row["manifest_digest"],
            installed_manifest_digest=row["installed_manifest_digest"],
            instance_id=row["instance_id"],
            worker_base_url=row["worker_base_url"],
            provider_token=row["provider_token"],
            session_secret_hex=row["session_secret_hex"],
            deadline_at=(
                float(row["deadline_at"])
                if row["deadline_at"] is not None
                else None
            ),
            deadline_mode=row["deadline_mode"],
            disk_gb=int(row["disk_gb"]),
            retry_count=int(row["retry_count"]),
            destroy_requested=bool(row["destroy_requested"]),
            residual_inventory=tuple(
                str(item) for item in json.loads(residual_json)
            ),
            sanitized_error=row["sanitized_error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _values(session):
        session._validate()
        return (
            session.session_id,
            session.idempotency_key,
            session.label,
            session.state.value,
            SessionRepository._quote_json(session.quote),
            session.manifest_digest,
            session.installed_manifest_digest,
            session.instance_id,
            session.worker_base_url,
            session.provider_token,
            session.session_secret_hex,
            session.deadline_at,
            session.deadline_mode,
            session.disk_gb,
            session.retry_count,
            int(session.destroy_requested),
            _canonical_json(list(session.residual_inventory)),
            session.sanitized_error,
            session.created_at,
            session.updated_at,
            session.version,
        )

    def get(self, session_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        return self._row_to_session(row)

    def get_by_idempotency_key(self, idempotency_key):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE idempotency_key = ?
                """,
                (str(idempotency_key),),
            ).fetchone()
        return self._row_to_session(row)

    def create_or_get(self, session):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE idempotency_key = ?
                """,
                (session.idempotency_key,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._row_to_session(row), False
            try:
                connection.execute(
                    f"""
                    INSERT INTO sessions ({_SESSION_COLUMNS})
                    VALUES ({",".join("?" for _ in range(21))})
                    """,
                    self._values(session),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    f"""
                    SELECT {_SESSION_COLUMNS}
                    FROM sessions
                    WHERE idempotency_key = ?
                    """,
                    (session.idempotency_key,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise
                connection.commit()
                return self._row_to_session(row), False
            connection.commit()
            row = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE session_id = ?
                """,
                (session.session_id,),
            ).fetchone()
        return self._row_to_session(row), True

    def _save_in_transaction(self, connection, session):
        session._validate()
        cursor = connection.execute(
            """
            UPDATE sessions SET
                state = ?,
                quote_json = ?,
                manifest_digest = ?,
                installed_manifest_digest = ?,
                instance_id = ?,
                worker_base_url = ?,
                provider_token = ?,
                session_secret_hex = ?,
                deadline_at = ?,
                deadline_mode = ?,
                disk_gb = ?,
                retry_count = ?,
                destroy_requested = ?,
                residual_inventory_json = ?,
                sanitized_error = ?,
                updated_at = ?,
                version = version + 1
            WHERE session_id = ? AND version = ?
            """,
            (
                session.state.value,
                self._quote_json(session.quote),
                session.manifest_digest,
                session.installed_manifest_digest,
                session.instance_id,
                session.worker_base_url,
                session.provider_token,
                session.session_secret_hex,
                session.deadline_at,
                session.deadline_mode,
                session.disk_gb,
                session.retry_count,
                int(session.destroy_requested),
                _canonical_json(list(session.residual_inventory)),
                session.sanitized_error,
                session.updated_at,
                session.session_id,
                session.version,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrentSessionUpdate(
                "The session changed while this operation was in progress."
            )
        row = connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()
        return self._row_to_session(row)

    def save(self, session):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                saved = self._save_in_transaction(connection, session)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return saved

    def transition(self, session_id, state, *, now=None, **changes):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(str(session_id))
            changed = self._row_to_session(row).transition(
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

    def transition_if_state(
        self,
        session_id,
        expected_state,
        state,
        *,
        now=None,
        **changes,
    ):
        expected = SessionState(expected_state)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(str(session_id))
            current = self._row_to_session(row)
            if current.state != expected:
                connection.rollback()
                raise ConcurrentSessionUpdate(
                    "The session is not in the required state."
                )
            changed = current.transition(
                state,
                now=now,
                **changes,
            )
            changed._validate()
            cursor = connection.execute(
                """
                UPDATE sessions SET
                    state = ?,
                    quote_json = ?,
                    manifest_digest = ?,
                    installed_manifest_digest = ?,
                    instance_id = ?,
                    worker_base_url = ?,
                    provider_token = ?,
                    session_secret_hex = ?,
                    deadline_at = ?,
                    deadline_mode = ?,
                    disk_gb = ?,
                    retry_count = ?,
                    destroy_requested = ?,
                    residual_inventory_json = ?,
                    sanitized_error = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE session_id = ? AND version = ? AND state = ?
                """,
                (
                    changed.state.value,
                    self._quote_json(changed.quote),
                    changed.manifest_digest,
                    changed.installed_manifest_digest,
                    changed.instance_id,
                    changed.worker_base_url,
                    changed.provider_token,
                    changed.session_secret_hex,
                    changed.deadline_at,
                    changed.deadline_mode,
                    changed.disk_gb,
                    changed.retry_count,
                    int(changed.destroy_requested),
                    _canonical_json(list(changed.residual_inventory)),
                    changed.sanitized_error,
                    changed.updated_at,
                    changed.session_id,
                    current.version,
                    expected.value,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ConcurrentSessionUpdate(
                    "The session changed while it was being claimed."
                )
            saved_row = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE session_id = ?
                """,
                (changed.session_id,),
            ).fetchone()
            connection.commit()
        return self._row_to_session(saved_row)

    def list_all(self):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                ORDER BY created_at, session_id
                """
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def list_recoverable(self):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE state != ?
                ORDER BY created_at, session_id
                """,
                (SessionState.DESTROYED.value,),
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

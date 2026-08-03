"""Transactional persistence for managed Vast.ai attempts."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hmac
import json
import math
import os
from pathlib import Path
import re
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


class ConcurrentDesktopRelayUpdate(RuntimeError):
    pass


class PaidRentalConflict(RuntimeError):
    """A static refusal while another paid rental may still exist."""

    def __init__(self):
        super().__init__(
            "Another Cloud Run rental is active or unresolved. "
            "Do not start another rental yet."
        )


_COLUMNS = """
    attempt_id, idempotency_key, label, state, quote_json, instance_id,
    residual_inventory_json, ready_url, provider_token, retry_count,
    cancel_requested, sanitized_error, created_at, updated_at, version
"""

_SESSION_COLUMNS = """
    session_id, idempotency_key, label, state, quote_json, manifest_digest,
    installed_manifest_digest, instance_id, worker_base_url, provider_token,
    session_secret_hex, deadline_at, deadline_mode, disk_gb, retry_count,
    destroy_requested, residual_inventory_json, sanitized_error, created_at,
    updated_at, version, pending_deadline_at, pending_deadline_mode,
    pending_deadline_action, failure_code, create_reconcile_started_at,
    create_empty_observations, create_first_empty_at, create_last_empty_at,
    create_settings_revision, create_configuration_revision,
    remediation_verified_at, remediation_revision,
    execution_baseline_digest, randomized_seed_node_ids_json
"""


@dataclass(frozen=True, repr=False)
class DestroyReviewRecord:
    session_id: str
    expires_at: float
    session_version: int
    instance_id: str | None
    unverified_artifact_ids: tuple[str, ...]


@dataclass(frozen=True)
class DesktopRelayConfig:
    bind_host: str
    port: int
    active_session_id: str | None
    profile_revision: int | None
    updated_at: float

    def __post_init__(self):
        active = self.active_session_id
        revision = self.profile_revision
        if (
            self.bind_host != "127.0.0.1"
            or isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
            or (
                active is not None
                and (
                    not isinstance(active, str)
                    or re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", active
                    )
                    is None
                )
            )
            or (
                revision is not None
                and (
                    isinstance(revision, bool)
                    or not isinstance(revision, int)
                    or revision < 0
                )
            )
            or (active is None) != (revision is None)
            or isinstance(self.updated_at, bool)
            or not isinstance(self.updated_at, (int, float))
            or not math.isfinite(self.updated_at)
            or self.updated_at < 0
        ):
            raise ValueError("Invalid Desktop relay configuration.")


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _randomized_seed_node_ids(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Stored randomized seed policy is invalid.") from None
    if not isinstance(parsed, list):
        raise ValueError("Stored randomized seed policy is invalid.")
    return tuple(parsed)


def _private_connection(path):
    connection = sqlite3.connect(str(path), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _desktop_relay_from_row(row):
    if row is None:
        return None
    return DesktopRelayConfig(
        bind_host=row["bind_host"],
        port=int(row["port"]),
        active_session_id=row["active_session_id"],
        profile_revision=(
            int(row["profile_revision"])
            if row["profile_revision"] is not None
            else None
        ),
        updated_at=float(row["updated_at"]),
    )


def _get_desktop_relay(path):
    with closing(_private_connection(path)) as connection:
        row = connection.execute(
            """
            SELECT bind_host, port, active_session_id, profile_revision,
                   updated_at
            FROM desktop_relay
            WHERE singleton = 1
            """
        ).fetchone()
    return _desktop_relay_from_row(row)


def _save_desktop_relay(path, config, *, expected_updated_at=None):
    if not isinstance(config, DesktopRelayConfig):
        raise ValueError("Invalid Desktop relay configuration.")
    if expected_updated_at is not None and (
        isinstance(expected_updated_at, bool)
        or not isinstance(expected_updated_at, (int, float))
        or not math.isfinite(expected_updated_at)
        or expected_updated_at < 0
    ):
        raise ValueError("Invalid Desktop relay update precondition.")
    with closing(_private_connection(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """
            SELECT bind_host, port, active_session_id, profile_revision,
                   updated_at
            FROM desktop_relay
            WHERE singleton = 1
            """
        ).fetchone()
        if expected_updated_at is not None and (
            current is None
            or float(current["updated_at"]) != float(expected_updated_at)
        ):
            connection.rollback()
            raise ConcurrentDesktopRelayUpdate(
                "Desktop relay configuration changed concurrently."
            )
        if current is not None and config.updated_at <= float(
            current["updated_at"]
        ):
            connection.rollback()
            raise ConcurrentDesktopRelayUpdate(
                "Desktop relay configuration revision must advance."
            )
        connection.execute(
            """
            INSERT INTO desktop_relay(
                singleton, bind_host, port, active_session_id,
                profile_revision, updated_at
            ) VALUES(1, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                bind_host = excluded.bind_host,
                port = excluded.port,
                active_session_id = excluded.active_session_id,
                profile_revision = excluded.profile_revision,
                updated_at = excluded.updated_at
            """,
            (
                config.bind_host,
                config.port,
                config.active_session_id,
                config.profile_revision,
                float(config.updated_at),
            ),
        )
        row = connection.execute(
            """
            SELECT bind_host, port, active_session_id, profile_revision,
                   updated_at
            FROM desktop_relay
            WHERE singleton = 1
            """
        ).fetchone()
        connection.commit()
    return _desktop_relay_from_row(row)


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
                    version INTEGER NOT NULL,
                    pending_deadline_at REAL,
                    pending_deadline_mode TEXT,
                    pending_deadline_action TEXT,
                    failure_code TEXT,
                    create_reconcile_started_at REAL,
                    create_empty_observations INTEGER NOT NULL DEFAULT 0,
                    create_first_empty_at REAL,
                    create_last_empty_at REAL,
                    create_settings_revision TEXT,
                    create_configuration_revision TEXT,
                    remediation_verified_at REAL,
                    remediation_revision TEXT,
                    execution_baseline_digest TEXT,
                    randomized_seed_node_ids_json TEXT
                )
                """
            )
            session_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(sessions)"
                ).fetchall()
            }
            for name, declaration in (
                ("pending_deadline_at", "REAL"),
                ("pending_deadline_mode", "TEXT"),
                ("pending_deadline_action", "TEXT"),
                ("failure_code", "TEXT"),
                ("create_reconcile_started_at", "REAL"),
                (
                    "create_empty_observations",
                    "INTEGER NOT NULL DEFAULT 0",
                ),
                ("create_first_empty_at", "REAL"),
                ("create_last_empty_at", "REAL"),
                ("create_settings_revision", "TEXT"),
                ("create_configuration_revision", "TEXT"),
                ("remediation_verified_at", "REAL"),
                ("remediation_revision", "TEXT"),
                ("execution_baseline_digest", "TEXT"),
                ("randomized_seed_node_ids_json", "TEXT"),
            ):
                if name not in session_columns:
                    connection.execute(
                        f"ALTER TABLE sessions ADD COLUMN {name} {declaration}"
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
                    execution_state TEXT NOT NULL DEFAULT 'pending',
                    harvest_state TEXT NOT NULL DEFAULT 'pending',
                    error_code TEXT,
                    queue_position INTEGER NOT NULL DEFAULT 0,
                    native_request_digest TEXT,
                    native_body_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    version INTEGER NOT NULL,
                    UNIQUE(session_id, idempotency_key)
                )
                """
            )
            job_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(jobs)"
                ).fetchall()
            }
            if "execution_state" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN execution_state "
                    "TEXT NOT NULL DEFAULT 'pending'"
                )
                connection.execute(
                    """
                    UPDATE jobs SET execution_state = CASE state
                        WHEN 'queued' THEN 'queued'
                        WHEN 'running' THEN 'running'
                        WHEN 'harvesting' THEN 'succeeded'
                        WHEN 'succeeded' THEN 'succeeded'
                        WHEN 'failed' THEN 'failed'
                        ELSE 'pending'
                    END
                    """
                )
            if "harvest_state" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN harvest_state "
                    "TEXT NOT NULL DEFAULT 'pending'"
                )
                connection.execute(
                    """
                    UPDATE jobs SET harvest_state = CASE state
                        WHEN 'harvesting' THEN 'running'
                        WHEN 'succeeded' THEN 'succeeded'
                        ELSE 'pending'
                    END
                    """
                )
            if "error_code" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN error_code TEXT")
                connection.execute(
                    """
                    UPDATE jobs SET error_code = 'internal_error'
                    WHERE state = 'failed'
                    """
                )
            queue_position_added = "queue_position" not in job_columns
            if queue_position_added:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN queue_position "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "native_request_digest" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN native_request_digest TEXT"
                )
            if "native_body_json" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN native_body_json TEXT"
                )
            if queue_position_added:
                session_ids = connection.execute(
                    "SELECT DISTINCT session_id FROM jobs"
                ).fetchall()
                for session_row in session_ids:
                    rows = connection.execute(
                        """
                        SELECT job_id FROM jobs
                        WHERE session_id = ?
                        ORDER BY created_at, job_id
                        """,
                        (session_row["session_id"],),
                    ).fetchall()
                    for position, row in enumerate(rows, start=1):
                        connection.execute(
                            "UPDATE jobs SET queue_position = ? WHERE job_id = ?",
                            (position, row["job_id"]),
                        )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_session_queue_position
                ON jobs(session_id, queue_position)
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
                CREATE TABLE IF NOT EXISTS run_journal (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    manifest_digest TEXT,
                    transaction_id TEXT,
                    job_id TEXT,
                    phase TEXT NOT NULL,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    node_id TEXT,
                    process_exit_code INTEGER,
                    restart_count INTEGER NOT NULL,
                    last_probe TEXT,
                    byte_cursor INTEGER NOT NULL,
                    event_cursor INTEGER NOT NULL,
                    output_state TEXT,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL
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
                    source_node_id TEXT,
                    published_device INTEGER,
                    published_inode INTEGER,
                    PRIMARY KEY(job_id, artifact_id)
                )
                """
            )
            transfer_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(transfers)"
                ).fetchall()
            }
            for name, declaration in (
                ("source_node_id", "TEXT"),
                ("published_device", "INTEGER"),
                ("published_inode", "INTEGER"),
            ):
                if name not in transfer_columns:
                    connection.execute(
                        f"ALTER TABLE transfers ADD COLUMN {name} {declaration}"
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
                    phase TEXT,
                    current_dependency_id TEXT,
                    transferred_bytes INTEGER NOT NULL DEFAULT 0
                        CHECK(transferred_bytes >= 0),
                    total_bytes INTEGER NOT NULL DEFAULT 0
                        CHECK(total_bytes >= 0),
                    last_progress_at REAL NOT NULL,
                    sanitized_error TEXT
                )
                """
            )
            provision_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(provision_transactions)"
                ).fetchall()
            }
            for name, declaration in (
                ("phase", "TEXT"),
                ("current_dependency_id", "TEXT"),
                (
                    "transferred_bytes",
                    "INTEGER NOT NULL DEFAULT 0 CHECK(transferred_bytes >= 0)",
                ),
                (
                    "total_bytes",
                    "INTEGER NOT NULL DEFAULT 0 CHECK(total_bytes >= 0)",
                ),
            ):
                if name not in provision_columns:
                    connection.execute(
                        "ALTER TABLE provision_transactions "
                        f"ADD COLUMN {name} {declaration}"
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
                CREATE TABLE IF NOT EXISTS destroy_reviews (
                    session_id TEXT PRIMARY KEY,
                    token_digest TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    session_version INTEGER NOT NULL,
                    instance_id TEXT,
                    unverified_artifact_ids_json TEXT NOT NULL
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS desktop_relay (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    bind_host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    active_session_id TEXT,
                    profile_revision INTEGER,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_revisions (
                    profile_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    base_revision INTEGER,
                    bootstrap_digest TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    archive_size_bytes INTEGER NOT NULL,
                    archive_sha256 TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    ui_packages_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(profile_id, revision)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_conflicts (
                    conflict_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    base_revision INTEGER NOT NULL,
                    local_revision INTEGER NOT NULL,
                    remote_revision INTEGER NOT NULL,
                    resolved_revision INTEGER,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_sync_state (
                    session_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    remote_revision INTEGER NOT NULL,
                    archive_sha256 TEXT,
                    warning TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )
            _migrate_legacy_attempts(connection)
            connection.execute(
                """
                INSERT INTO schema_meta(key, value) VALUES('schema_version', '11')
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
    attempt_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(attempts)").fetchall()
    }
    residual_expression = (
        "residual_inventory_json"
        if "residual_inventory_json" in attempt_columns
        else "NULL AS residual_inventory_json"
    )
    rows = connection.execute(
        f"""
        SELECT
            attempt_id, idempotency_key, label, state, quote_json,
            instance_id, {residual_expression}, ready_url, provider_token, retry_count,
            cancel_requested, sanitized_error, created_at, updated_at, version
        FROM attempts
        """
    ).fetchall()
    for row in rows:
        state = LEGACY_SESSION_STATES.get(
            str(row["state"]),
            SessionState.FAILED,
        )
        try:
            residual_inventory = [
                str(item)
                for item in json.loads(
                    row["residual_inventory_json"] or "[]"
                )
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            residual_inventory = []
        if (
            state == SessionState.FAILED
            and row["instance_id"]
            and str(row["instance_id"]) not in residual_inventory
        ):
            residual_inventory.append(str(row["instance_id"]))
        if state != SessionState.FAILED:
            residual_inventory = []
        try:
            migrated_quote = OfferQuote.from_record(
                json.loads(row["quote_json"])
            )
            execution_baseline_digest = (
                migrated_quote.execution_baseline_digest
            )
            randomized_seed_node_ids_json = _canonical_json(
                list(migrated_quote.randomized_seed_node_ids)
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            execution_baseline_digest = "0" * 64
            randomized_seed_node_ids_json = "[]"
        connection.execute(
            """
            INSERT OR IGNORE INTO sessions (
                session_id, idempotency_key, label, state, quote_json,
                manifest_digest, installed_manifest_digest, instance_id,
                worker_base_url, provider_token, session_secret_hex,
                deadline_at, deadline_mode, disk_gb, retry_count,
                destroy_requested, residual_inventory_json, sanitized_error,
                created_at, updated_at, version,
                execution_baseline_digest, randomized_seed_node_ids_json
            ) VALUES (
                ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL,
                NULL, 'finite', 80, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                execution_baseline_digest,
                randomized_seed_node_ids_json,
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
                    residual_inventory_json TEXT,
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
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(attempts)"
                ).fetchall()
            }
            if "residual_inventory_json" not in columns:
                connection.execute(
                    "ALTER TABLE attempts ADD COLUMN residual_inventory_json TEXT"
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
            residual_inventory=tuple(
                str(item)
                for item in json.loads(
                    row["residual_inventory_json"] or "[]"
                )
            ),
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
            _canonical_json(list(attempt.residual_inventory)),
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
                    VALUES ({",".join("?" for _ in range(15))})
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
                residual_inventory_json = ?,
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
                _canonical_json(list(attempt.residual_inventory)),
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

    def get_desktop_relay(self):
        return _get_desktop_relay(self.path)

    def save_desktop_relay(self, config, *, expected_updated_at=None):
        return _save_desktop_relay(
            self.path,
            config,
            expected_updated_at=expected_updated_at,
        )

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
            pending_deadline_at=(
                float(row["pending_deadline_at"])
                if row["pending_deadline_at"] is not None
                else None
            ),
            pending_deadline_mode=row["pending_deadline_mode"],
            pending_deadline_action=row["pending_deadline_action"],
            failure_code=row["failure_code"],
            create_reconcile_started_at=(
                float(row["create_reconcile_started_at"])
                if row["create_reconcile_started_at"] is not None
                else None
            ),
            create_empty_observations=int(
                row["create_empty_observations"]
            ),
            create_first_empty_at=(
                float(row["create_first_empty_at"])
                if row["create_first_empty_at"] is not None
                else None
            ),
            create_last_empty_at=(
                float(row["create_last_empty_at"])
                if row["create_last_empty_at"] is not None
                else None
            ),
            create_settings_revision=row["create_settings_revision"],
            create_configuration_revision=(
                row["create_configuration_revision"]
            ),
            remediation_verified_at=(
                float(row["remediation_verified_at"])
                if row["remediation_verified_at"] is not None
                else None
            ),
            remediation_revision=row["remediation_revision"],
            execution_baseline_digest=(
                row["execution_baseline_digest"] or "0" * 64
            ),
            randomized_seed_node_ids=_randomized_seed_node_ids(
                row["randomized_seed_node_ids_json"]
            ),
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
            session.pending_deadline_at,
            session.pending_deadline_mode,
            session.pending_deadline_action,
            session.failure_code,
            session.create_reconcile_started_at,
            session.create_empty_observations,
            session.create_first_empty_at,
            session.create_last_empty_at,
            session.create_settings_revision,
            session.create_configuration_revision,
            session.remediation_verified_at,
            session.remediation_revision,
            session.execution_baseline_digest,
            _canonical_json(list(session.randomized_seed_node_ids)),
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
                    VALUES ({",".join("?" for _ in range(35))})
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
                pending_deadline_at = ?,
                pending_deadline_mode = ?,
                pending_deadline_action = ?,
                failure_code = ?,
                create_reconcile_started_at = ?,
                create_empty_observations = ?,
                create_first_empty_at = ?,
                create_last_empty_at = ?,
                create_settings_revision = ?,
                create_configuration_revision = ?,
                remediation_verified_at = ?,
                remediation_revision = ?,
                execution_baseline_digest = ?,
                randomized_seed_node_ids_json = ?,
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
                session.pending_deadline_at,
                session.pending_deadline_mode,
                session.pending_deadline_action,
                session.failure_code,
                session.create_reconcile_started_at,
                session.create_empty_observations,
                session.create_first_empty_at,
                session.create_last_empty_at,
                session.create_settings_revision,
                session.create_configuration_revision,
                session.remediation_verified_at,
                session.remediation_revision,
                session.execution_baseline_digest,
                _canonical_json(list(session.randomized_seed_node_ids)),
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

    def update(self, session_id, *, now=None, **changes):
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
            changed = current.transition(
                current.state,
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
                    pending_deadline_at = ?,
                    pending_deadline_mode = ?,
                    pending_deadline_action = ?,
                    failure_code = ?,
                    create_reconcile_started_at = ?,
                    create_empty_observations = ?,
                    create_first_empty_at = ?,
                    create_last_empty_at = ?,
                    create_settings_revision = ?,
                    create_configuration_revision = ?,
                    remediation_verified_at = ?,
                    remediation_revision = ?,
                    execution_baseline_digest = ?,
                    randomized_seed_node_ids_json = ?,
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
                    changed.pending_deadline_at,
                    changed.pending_deadline_mode,
                    changed.pending_deadline_action,
                    changed.failure_code,
                    changed.create_reconcile_started_at,
                    changed.create_empty_observations,
                    changed.create_first_empty_at,
                    changed.create_last_empty_at,
                    changed.create_settings_revision,
                    changed.create_configuration_revision,
                    changed.remediation_verified_at,
                    changed.remediation_revision,
                    changed.execution_baseline_digest,
                    _canonical_json(
                        list(changed.randomized_seed_node_ids)
                    ),
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

    def claim_create_intent(
        self,
        session_id,
        *,
        now,
        provider_token,
        session_secret_hex,
    ):
        """Atomically serialize the boundary immediately before Vast PUT."""
        identifier = str(session_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE session_id = ?
                """,
                (identifier,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(identifier)
            target = self._row_to_session(row)
            if target.state != SessionState.CONFIRMING:
                connection.rollback()
                raise ConcurrentSessionUpdate(
                    "The session is not awaiting a paid create claim."
                )

            other_rows = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                WHERE session_id != ?
                """,
                (identifier,),
            ).fetchall()
            conflict = any(
                other.rental_outcome in {"unknown", "active"}
                or other.instance_id is not None
                or bool(other.residual_inventory)
                or other.blocks_new_rental
                for other in (
                    self._row_to_session(other_row)
                    for other_row in other_rows
                )
            )
            if conflict:
                offered = target.transition(
                    SessionState.OFFER_SELECTED,
                    now=now,
                    sanitized_error=None,
                )
                try:
                    self._save_in_transaction(connection, offered)
                except Exception:
                    connection.rollback()
                    raise
                connection.commit()
                raise PaidRentalConflict()

            creating = target.transition(
                SessionState.CREATING,
                now=now,
                provider_token=provider_token,
                session_secret_hex=session_secret_hex,
                sanitized_error=None,
            )
            try:
                saved = self._save_in_transaction(connection, creating)
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return saved

    def save_destroy_review(
        self,
        session_id,
        *,
        token_digest,
        expires_at,
        session_version,
        instance_id,
        unverified_artifact_ids,
    ):
        identifier = str(session_id or "")
        digest = str(token_digest or "")
        expiry = float(expires_at)
        version = int(session_version)
        instance = None if instance_id is None else str(instance_id)
        artifact_ids = tuple(
            sorted(str(item) for item in unverified_artifact_ids)
        )
        if (
            not identifier
            or len(identifier) > 200
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not math.isfinite(expiry)
            or expiry <= 0
            or version < 1
            or (instance is not None and (not instance or len(instance) > 200))
            or len(artifact_ids) > 100_000
            or len(set(artifact_ids)) != len(artifact_ids)
            or any(not item or len(item) > 200 for item in artifact_ids)
        ):
            raise ValueError("Destroy review metadata is invalid.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                """
                SELECT version, instance_id
                FROM sessions
                WHERE session_id = ?
                """,
                (identifier,),
            ).fetchone()
            if (
                session is None
                or int(session["version"]) != version
                or session["instance_id"] != instance
            ):
                connection.rollback()
                raise ConcurrentSessionUpdate(
                    "The session changed before destruction review."
                )
            connection.execute(
                """
                INSERT INTO destroy_reviews(
                    session_id, token_digest, expires_at, session_version,
                    instance_id, unverified_artifact_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    token_digest = excluded.token_digest,
                    expires_at = excluded.expires_at,
                    session_version = excluded.session_version,
                    instance_id = excluded.instance_id,
                    unverified_artifact_ids_json =
                        excluded.unverified_artifact_ids_json
                """,
                (
                    identifier,
                    digest,
                    expiry,
                    version,
                    instance,
                    _canonical_json(list(artifact_ids)),
                ),
            )
            connection.commit()

    def consume_destroy_review(
        self,
        session_id,
        *,
        token_digest,
        now,
    ):
        identifier = str(session_id or "")
        digest = str(token_digest or "")
        timestamp = float(now)
        if (
            not identifier
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            return None
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    review.token_digest,
                    review.expires_at,
                    review.session_version,
                    review.instance_id AS reviewed_instance_id,
                    review.unverified_artifact_ids_json,
                    session.version AS current_version,
                    session.instance_id AS live_instance_id
                FROM destroy_reviews AS review
                JOIN sessions AS session
                    ON session.session_id = review.session_id
                WHERE review.session_id = ?
                """,
                (identifier,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            stale = (
                timestamp >= float(row["expires_at"])
                or int(row["session_version"])
                != int(row["current_version"])
                or row["reviewed_instance_id"]
                != row["live_instance_id"]
            )
            if stale:
                connection.execute(
                    "DELETE FROM destroy_reviews WHERE session_id = ?",
                    (identifier,),
                )
                connection.commit()
                return None
            if not hmac.compare_digest(row["token_digest"], digest):
                connection.commit()
                return None
            try:
                artifact_ids = tuple(
                    str(item)
                    for item in json.loads(
                        row["unverified_artifact_ids_json"]
                    )
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                connection.execute(
                    "DELETE FROM destroy_reviews WHERE session_id = ?",
                    (identifier,),
                )
                connection.commit()
                return None
            connection.execute(
                "DELETE FROM destroy_reviews WHERE session_id = ?",
                (identifier,),
            )
            connection.commit()
        return DestroyReviewRecord(
            session_id=identifier,
            expires_at=float(row["expires_at"]),
            session_version=int(row["session_version"]),
            instance_id=row["reviewed_instance_id"],
            unverified_artifact_ids=artifact_ids,
        )

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
                ORDER BY created_at, session_id
                """
            ).fetchall()
        sessions = [self._row_to_session(row) for row in rows]
        return [
            session
            for session in sessions
            if session.state == SessionState.CONFIRMING
            or session.rental_outcome in {"unknown", "active"}
        ]

    def list_recent(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Recent session limit must be between 1 and 20.")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_SESSION_COLUMNS}
                FROM sessions
                ORDER BY created_at DESC, session_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_session(row) for row in reversed(rows)]

"""Durable job, manifest, event, transfer, and provisioning persistence."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import time

from .models import CloudJob, JobState, TransferState
from .repository import _canonical_json, _initialize_database, _private_connection


class ConcurrentJobUpdate(RuntimeError):
    pass


@dataclass(frozen=True)
class JobEvent:
    job_id: str
    sequence: int
    event_type: str
    payload: dict
    created_at: float


@dataclass(frozen=True)
class TransferRecord:
    job_id: str
    artifact_id: str
    direction: str
    expected_size: int
    sha256: str
    offset: int
    state: TransferState
    private_path: str


@dataclass(frozen=True)
class ProvisionTransaction:
    transaction_id: str
    session_id: str
    job_id: str | None
    manifest_digest: str
    state: str
    planned_restart_count: int
    repair_count: int
    repair_restart_count: int
    last_progress_at: float
    sanitized_error: str | None


@dataclass(frozen=True)
class InstalledDependency:
    session_id: str
    dependency_id: str
    digest: str
    revision: str | None
    destination: str


_JOB_COLUMNS = """
    job_id, session_id, idempotency_key, state, prompt_digest, capture_json,
    manifest_digest, remote_prompt_id, sanitized_error, created_at, updated_at,
    version
"""


def _require_digest(value, name):
    normalized = str(value or "")
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"Invalid {name}.")
    return normalized


def _require_identifier(value, name):
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200:
        raise ValueError(f"Invalid {name}.")
    return normalized


class JobRepository:
    def __init__(self, path):
        self.path = Path(path)
        _initialize_database(self.path)

    def _connect(self):
        return _private_connection(self.path)

    @staticmethod
    def _row_to_job(row):
        if row is None:
            return None
        return CloudJob(
            job_id=row["job_id"],
            session_id=row["session_id"],
            idempotency_key=row["idempotency_key"],
            state=JobState(row["state"]),
            prompt_digest=row["prompt_digest"],
            capture_json=row["capture_json"],
            manifest_digest=row["manifest_digest"],
            remote_prompt_id=row["remote_prompt_id"],
            sanitized_error=row["sanitized_error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _job_values(job):
        _require_identifier(job.job_id, "job ID")
        _require_identifier(job.session_id, "session ID")
        _require_identifier(job.idempotency_key, "idempotency key")
        _require_digest(job.prompt_digest, "prompt digest")
        _require_digest(job.manifest_digest, "manifest digest")
        json.loads(job.capture_json)
        return (
            job.job_id,
            job.session_id,
            job.idempotency_key,
            job.state.value,
            job.prompt_digest,
            job.capture_json,
            job.manifest_digest,
            job.remote_prompt_id,
            job.sanitized_error,
            job.created_at,
            job.updated_at,
            job.version,
        )

    def get_job(self, job_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
        return self._row_to_job(row)

    def get_job_by_idempotency_key(self, session_id, idempotency_key):
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM jobs
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (str(session_id), str(idempotency_key)),
            ).fetchone()
        return self._row_to_job(row)

    def create_job(self, job):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM jobs
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (job.session_id, job.idempotency_key),
            ).fetchone()
            if row is not None:
                connection.commit()
                return self._row_to_job(row), False
            try:
                connection.execute(
                    f"""
                    INSERT INTO jobs ({_JOB_COLUMNS})
                    VALUES ({",".join("?" for _ in range(12))})
                    """,
                    self._job_values(job),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    f"""
                    SELECT {_JOB_COLUMNS}
                    FROM jobs
                    WHERE session_id = ? AND idempotency_key = ?
                    """,
                    (job.session_id, job.idempotency_key),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise
                connection.commit()
                return self._row_to_job(row), False
            connection.commit()
            row = connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()
        return self._row_to_job(row), True

    def save_job(self, job):
        self._job_values(job)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE jobs SET
                    state = ?,
                    prompt_digest = ?,
                    capture_json = ?,
                    manifest_digest = ?,
                    remote_prompt_id = ?,
                    sanitized_error = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE job_id = ? AND version = ?
                """,
                (
                    job.state.value,
                    job.prompt_digest,
                    job.capture_json,
                    job.manifest_digest,
                    job.remote_prompt_id,
                    job.sanitized_error,
                    job.updated_at,
                    job.job_id,
                    job.version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ConcurrentJobUpdate(
                    "The job changed while this operation was in progress."
                )
            row = connection.execute(
                f"SELECT {_JOB_COLUMNS} FROM jobs WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()
            connection.commit()
        return self._row_to_job(row)

    def save_manifest(self, manifest_digest, manifest_json, *, created_at=None):
        digest = _require_digest(manifest_digest, "manifest digest")
        payload = json.loads(str(manifest_json))
        encoded = _canonical_json(payload)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT manifest_json
                FROM manifests
                WHERE manifest_digest = ?
                """,
                (digest,),
            ).fetchone()
            if existing is not None and existing["manifest_json"] != encoded:
                connection.rollback()
                raise ValueError("A manifest digest cannot identify different content.")
            connection.execute(
                """
                INSERT OR IGNORE INTO manifests(
                    manifest_digest, manifest_json, created_at
                ) VALUES (?, ?, ?)
                """,
                (
                    digest,
                    encoded,
                    float(time.time() if created_at is None else created_at),
                ),
            )
            connection.commit()

    def get_manifest(self, manifest_digest):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT manifest_json
                FROM manifests
                WHERE manifest_digest = ?
                """,
                (str(manifest_digest),),
            ).fetchone()
        return row["manifest_json"] if row is not None else None

    def append_event(
        self,
        job_id,
        sequence,
        event_type,
        payload,
        *,
        created_at=None,
    ):
        identifier = _require_identifier(job_id, "job ID")
        number = int(sequence)
        if number < 1:
            raise ValueError("Event sequence must be positive.")
        kind = _require_identifier(event_type, "event type")
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be an object.")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO job_events(
                    job_id, sequence, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    number,
                    kind,
                    _canonical_json(payload),
                    float(time.time() if created_at is None else created_at),
                ),
            )
            connection.commit()

    def list_events(self, job_id, after_sequence=0):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT job_id, sequence, event_type, payload_json, created_at
                FROM job_events
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (str(job_id), int(after_sequence)),
            ).fetchall()
        return [
            JobEvent(
                job_id=row["job_id"],
                sequence=int(row["sequence"]),
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def save_transfer(
        self,
        *,
        job_id,
        artifact_id,
        direction,
        expected_size,
        sha256,
        offset,
        state,
        private_path,
    ):
        job_identifier = _require_identifier(job_id, "job ID")
        artifact_identifier = _require_identifier(artifact_id, "artifact ID")
        normalized_direction = str(direction)
        if normalized_direction not in {"download", "upload"}:
            raise ValueError("Invalid transfer direction.")
        total = int(expected_size)
        current = int(offset)
        if total < 0 or current < 0 or current > total:
            raise ValueError("Invalid transfer offset.")
        digest = _require_digest(sha256, "transfer digest")
        transfer_state = TransferState(state)
        private = str(private_path or "")
        if not private:
            raise ValueError("A private transfer path is required.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT expected_size, sha256, offset, direction
                FROM transfers
                WHERE job_id = ? AND artifact_id = ?
                """,
                (job_identifier, artifact_identifier),
            ).fetchone()
            if existing is not None:
                identity = (
                    int(existing["expected_size"]),
                    existing["sha256"],
                    existing["direction"],
                )
                if identity != (total, digest, normalized_direction):
                    connection.rollback()
                    raise ValueError("Transfer identity cannot change.")
                if current < int(existing["offset"]):
                    connection.rollback()
                    raise ValueError("Transfer offsets cannot move backwards.")
            connection.execute(
                """
                INSERT INTO transfers(
                    job_id, artifact_id, direction, expected_size, sha256,
                    offset, state, private_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, artifact_id) DO UPDATE SET
                    offset = excluded.offset,
                    state = excluded.state,
                    private_path = excluded.private_path
                """,
                (
                    job_identifier,
                    artifact_identifier,
                    normalized_direction,
                    total,
                    digest,
                    current,
                    transfer_state.value,
                    private,
                ),
            )
            connection.commit()

    def get_transfer(self, job_id, artifact_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    job_id, artifact_id, direction, expected_size, sha256,
                    offset, state, private_path
                FROM transfers
                WHERE job_id = ? AND artifact_id = ?
                """,
                (str(job_id), str(artifact_id)),
            ).fetchone()
        if row is None:
            return None
        return TransferRecord(
            job_id=row["job_id"],
            artifact_id=row["artifact_id"],
            direction=row["direction"],
            expected_size=int(row["expected_size"]),
            sha256=row["sha256"],
            offset=int(row["offset"]),
            state=TransferState(row["state"]),
            private_path=row["private_path"],
        )

    def create_provision_transaction(
        self,
        *,
        transaction_id,
        session_id,
        job_id,
        manifest_digest,
        state,
        planned_restart_count,
        repair_count,
        repair_restart_count,
        last_progress_at,
        sanitized_error=None,
    ):
        values = (
            _require_identifier(transaction_id, "transaction ID"),
            _require_identifier(session_id, "session ID"),
            (
                _require_identifier(job_id, "job ID")
                if job_id is not None
                else None
            ),
            _require_digest(manifest_digest, "manifest digest"),
            _require_identifier(state, "transaction state"),
            int(planned_restart_count),
            int(repair_count),
            int(repair_restart_count),
            float(last_progress_at),
            sanitized_error,
        )
        if any(value < 0 for value in values[5:8]):
            raise ValueError("Restart and repair counts cannot be negative.")
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO provision_transactions(
                    transaction_id, session_id, job_id, manifest_digest, state,
                    planned_restart_count, repair_count, repair_restart_count,
                    last_progress_at, sanitized_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            connection.commit()

    def get_provision_transaction(self, transaction_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    transaction_id, session_id, job_id, manifest_digest, state,
                    planned_restart_count, repair_count, repair_restart_count,
                    last_progress_at, sanitized_error
                FROM provision_transactions
                WHERE transaction_id = ?
                """,
                (str(transaction_id),),
            ).fetchone()
        if row is None:
            return None
        return ProvisionTransaction(
            transaction_id=row["transaction_id"],
            session_id=row["session_id"],
            job_id=row["job_id"],
            manifest_digest=row["manifest_digest"],
            state=row["state"],
            planned_restart_count=int(row["planned_restart_count"]),
            repair_count=int(row["repair_count"]),
            repair_restart_count=int(row["repair_restart_count"]),
            last_progress_at=float(row["last_progress_at"]),
            sanitized_error=row["sanitized_error"],
        )

    def replace_installed_set(self, session_id, dependencies):
        session_identifier = _require_identifier(session_id, "session ID")
        records = []
        seen = set()
        for dependency in dependencies:
            if set(dependency) != {
                "dependency_id",
                "digest",
                "revision",
                "destination",
            }:
                raise ValueError("Installed dependency fields are invalid.")
            dependency_id = _require_identifier(
                dependency["dependency_id"],
                "dependency ID",
            )
            if dependency_id in seen:
                raise ValueError("Installed dependency IDs must be unique.")
            seen.add(dependency_id)
            revision = dependency["revision"]
            if revision is not None and not re.fullmatch(
                r"[0-9a-f]{40}",
                str(revision),
            ):
                raise ValueError("Invalid installed revision.")
            records.append(
                (
                    session_identifier,
                    dependency_id,
                    _require_digest(dependency["digest"], "dependency digest"),
                    str(revision) if revision is not None else None,
                    _require_identifier(
                        dependency["destination"],
                        "dependency destination",
                    ),
                )
            )
        records.sort(key=lambda item: item[1])
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM installed_dependencies WHERE session_id = ?",
                (session_identifier,),
            )
            connection.executemany(
                """
                INSERT INTO installed_dependencies(
                    session_id, dependency_id, digest, revision, destination
                ) VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )
            connection.commit()

    def installed_set(self, session_id):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT session_id, dependency_id, digest, revision, destination
                FROM installed_dependencies
                WHERE session_id = ?
                ORDER BY dependency_id
                """,
                (str(session_id),),
            ).fetchall()
        return [
            InstalledDependency(
                session_id=row["session_id"],
                dependency_id=row["dependency_id"],
                digest=row["digest"],
                revision=row["revision"],
                destination=row["destination"],
            )
            for row in rows
        ]

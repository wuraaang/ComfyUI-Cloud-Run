"""Durable job, manifest, event, transfer, and provisioning persistence."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from .capture import CompiledCapture
from .models import (
    CloudJob,
    ExecutionState,
    HarvestState,
    JobState,
    TransferState,
)
from .repository import (
    _canonical_json,
    _get_desktop_relay,
    _initialize_database,
    _private_connection,
    _save_desktop_relay,
)
from .run_errors import RunErrorCode, RunJournalEntry, RunPhase


_INSTALLED_REVISION = re.compile(
    r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})"
)


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
    source_node_id: str | None
    published_device: int | None
    published_inode: int | None


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
    phase: str | None
    current_dependency_id: str | None
    transferred_bytes: int
    total_bytes: int
    last_progress_at: float
    sanitized_error: str | None


@dataclass(frozen=True)
class InstalledDependency:
    session_id: str
    dependency_id: str
    digest: str
    revision: str | None
    destination: str


@dataclass(frozen=True, repr=False)
class LocalArtifactRecord:
    artifact_id: str
    private_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class StoredPreflight:
    preflight_id: str
    capture_id: str
    payload: dict
    created_at: float


_JOB_COLUMNS = """
    job_id, session_id, idempotency_key, state, prompt_digest, capture_json,
    manifest_digest, remote_prompt_id, sanitized_error, execution_state,
    harvest_state, error_code, queue_position, native_request_digest,
    native_body_json, created_at, updated_at, version
"""
_JOURNAL_COLUMNS = """
    entry_id, session_id, manifest_digest, transaction_id, job_id, phase, code,
    message, node_id, process_exit_code, restart_count, last_probe, byte_cursor,
    event_cursor, output_state, details_json, created_at
"""
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
    "comfyui_startup",
    "environment_validation",
    "ready",
}
_ARTIFACT_PROVISION_PHASES = {
    "dependency_transfer",
    "model_transfer",
    "digest_verification",
}


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


def _require_strict_identifier(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name}.")
    return value


def _require_finite_timestamp(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"Invalid {name}.")
    return float(value)


def _require_sanitized_error(value):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or any(ord(character) < 32 for character in value)
        or any(
            marker in value.casefold()
            for marker in (
                "://",
                "bearer ",
                "token=",
                "secret",
                "/users/",
            )
        )
    ):
        raise ValueError("Invalid sanitized provisioning error.")
    return value


def _provision_progress_values(
    *,
    phase,
    current_dependency_id,
    transferred_bytes,
    total_bytes,
):
    if phase is not None and (
        not isinstance(phase, str) or phase not in _PROVISION_PHASES
    ):
        raise ValueError("Invalid provisioning phase.")
    current = _require_strict_identifier(
        current_dependency_id,
        "current dependency ID",
        optional=True,
    )
    if (
        isinstance(transferred_bytes, bool)
        or not isinstance(transferred_bytes, int)
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or not 0 <= transferred_bytes <= total_bytes
        or (
            phase in _ARTIFACT_PROVISION_PHASES
            and current is None
        )
        or (
            phase not in _ARTIFACT_PROVISION_PHASES
            and current is not None
        )
        or (phase == "ready" and transferred_bytes != total_bytes)
    ):
        raise ValueError("Invalid provisioning progress.")
    return phase, current, transferred_bytes, total_bytes


class JobRepository:
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
    def _readiness_report_from_row(row):
        if row is None:
            return None
        from .readiness import ReadinessReport

        try:
            checks = json.loads(row["checks_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("Stored readiness report is invalid.") from None
        report = ReadinessReport._from_stored_record(
            {
                "report_digest": row["report_digest"],
                "session_id": row["session_id"],
                "instance_id": row["instance_id"],
                "worker_release_digest": row["worker_release_digest"],
                "manifest_digest": row["manifest_digest"],
                "profile_revision": int(row["profile_revision"]),
                "relay_origin": row["relay_origin"],
                "inventory_observed_at": float(row["inventory_observed_at"]),
                "created_at": float(row["created_at"]),
                "attempt_number": int(row["attempt_number"]),
                "checks": checks,
            }
        )
        if bool(row["ready"]) != report.ready:
            raise ValueError("Stored readiness report is invalid.")
        return report

    @staticmethod
    def _readiness_columns():
        return (
            "report_digest, session_id, instance_id, worker_release_digest, "
            "manifest_digest, profile_revision, relay_origin, "
            "inventory_observed_at, created_at, attempt_number, "
            "checks_json, ready"
        )

    @staticmethod
    def _readiness_identity_values(
        *,
        session_id,
        instance_id,
        worker_release_digest,
        manifest_digest,
        profile_revision,
        relay_origin,
    ):
        from .readiness import _relay_origin

        if (
            not isinstance(session_id, str)
            or _IDENTIFIER.fullmatch(session_id) is None
            or not isinstance(instance_id, str)
            or _IDENTIFIER.fullmatch(instance_id) is None
            or not isinstance(worker_release_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", worker_release_digest) is None
            or not isinstance(manifest_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", manifest_digest) is None
            or isinstance(profile_revision, bool)
            or not isinstance(profile_revision, int)
            or profile_revision < 0
        ):
            raise ValueError("Invalid readiness report identity.")
        _relay_origin(relay_origin)
        return (
            session_id,
            instance_id,
            worker_release_digest,
            manifest_digest,
            profile_revision,
            relay_origin,
        )

    def get_readiness_report(self, report_digest):
        from .readiness import ReadinessReport

        if (
            not isinstance(report_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", report_digest) is None
        ):
            raise ValueError("Invalid readiness report digest.")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE report_digest = ?",
                (report_digest,),
            ).fetchone()
        report = self._readiness_report_from_row(row)
        if report is not None and not isinstance(report, ReadinessReport):
            raise ValueError("Stored readiness report is invalid.")
        return report

    def current_readiness_report(
        self,
        *,
        session_id,
        instance_id,
        worker_release_digest,
        manifest_digest,
        profile_revision,
        relay_origin,
    ):
        identity = self._readiness_identity_values(
            session_id=session_id,
            instance_id=instance_id,
            worker_release_digest=worker_release_digest,
            manifest_digest=manifest_digest,
            profile_revision=profile_revision,
            relay_origin=relay_origin,
        )
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE "
                "session_id = ? AND instance_id = ? AND "
                "worker_release_digest = ? AND manifest_digest = ? AND "
                "profile_revision = ? AND relay_origin = ? AND ready = 1",
                identity,
            ).fetchone()
        return self._readiness_report_from_row(row)

    def latest_readiness_attempt(self, **identity):
        values = self._readiness_identity_values(**identity)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE "
                "session_id = ? AND instance_id = ? AND "
                "worker_release_digest = ? AND manifest_digest = ? AND "
                "profile_revision = ? AND relay_origin = ? "
                "ORDER BY attempt_number DESC LIMIT 1",
                values,
            ).fetchone()
        return self._readiness_report_from_row(row)

    def list_readiness_attempts(self, **identity):
        values = self._readiness_identity_values(**identity)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE "
                "session_id = ? AND instance_id = ? AND "
                "worker_release_digest = ? AND manifest_digest = ? AND "
                "profile_revision = ? AND relay_origin = ? "
                "ORDER BY attempt_number",
                values,
            ).fetchall()
        return [self._readiness_report_from_row(row) for row in rows]

    def save_readiness_report(self, report):
        from .readiness import ReadinessReport

        if not isinstance(report, ReadinessReport):
            raise ValueError("Invalid readiness report.")
        values = (
            report.report_digest,
            report.session_id,
            report.instance_id,
            report.worker_release_digest,
            report.manifest_digest,
            report.profile_revision,
            report.relay_origin,
            report.inventory_observed_at,
            report.created_at,
            report.attempt_number,
            _canonical_json([item.to_record() for item in report.checks]),
            int(report.ready),
        )
        identity = values[1:7]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            success = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE "
                "session_id = ? AND instance_id = ? AND "
                "worker_release_digest = ? AND manifest_digest = ? AND "
                "profile_revision = ? AND relay_origin = ? AND ready = 1",
                identity,
            ).fetchone()
            if success is not None:
                connection.commit()
                return self._readiness_report_from_row(success)
            existing = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE "
                "session_id = ? AND instance_id = ? AND "
                "worker_release_digest = ? AND manifest_digest = ? AND "
                "profile_revision = ? AND relay_origin = ? AND "
                "attempt_number = ?",
                (*identity, report.attempt_number),
            ).fetchone()
            if existing is not None:
                stored = self._readiness_report_from_row(existing)
                connection.commit()
                if stored == report:
                    return stored
                raise ValueError(
                    "Readiness report attempt already exists."
                )
            try:
                connection.execute(
                    """
                    INSERT INTO readiness_reports(
                        report_digest, session_id, instance_id,
                        worker_release_digest, manifest_digest,
                        profile_revision, relay_origin,
                        inventory_observed_at, created_at, attempt_number,
                        checks_json, ready
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError:
                success = connection.execute(
                    "SELECT "
                    + self._readiness_columns()
                    + " FROM readiness_reports WHERE "
                    "session_id = ? AND instance_id = ? AND "
                    "worker_release_digest = ? AND manifest_digest = ? AND "
                    "profile_revision = ? AND relay_origin = ? AND ready = 1",
                    identity,
                ).fetchone()
                if success is not None:
                    connection.commit()
                    return self._readiness_report_from_row(success)
                connection.rollback()
                raise ValueError(
                    "Readiness report attempt already exists."
                ) from None
            row = connection.execute(
                "SELECT "
                + self._readiness_columns()
                + " FROM readiness_reports WHERE report_digest = ?",
                (report.report_digest,),
            ).fetchone()
            connection.commit()
        return self._readiness_report_from_row(row)

    @staticmethod
    def _profile_revision_from_row(row):
        if row is None:
            return None
        return {
            "profile_id": row["profile_id"],
            "revision": int(row["revision"]),
            "base_revision": (
                int(row["base_revision"])
                if row["base_revision"] is not None
                else None
            ),
            "bootstrap_digest": row["bootstrap_digest"],
            "archive_path": row["archive_path"],
            "archive_size_bytes": int(row["archive_size_bytes"]),
            "archive_sha256": row["archive_sha256"],
            "artifacts": json.loads(row["artifacts_json"]),
            "ui_packages": json.loads(row["ui_packages_json"]),
            "source": row["source"],
            "created_at": float(row["created_at"]),
        }

    def get_profile_revision(self, profile_id, revision):
        identifier = _require_strict_identifier(profile_id, "profile ID")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
        ):
            raise ValueError("Invalid profile revision.")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT profile_id, revision, base_revision, bootstrap_digest,
                       archive_path, archive_size_bytes, archive_sha256,
                       artifacts_json, ui_packages_json, source, created_at
                FROM profile_revisions
                WHERE profile_id = ? AND revision = ?
                """,
                (identifier, revision),
            ).fetchone()
        return self._profile_revision_from_row(row)

    def latest_profile_revision(self, profile_id):
        identifier = _require_strict_identifier(profile_id, "profile ID")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT profile_id, revision, base_revision, bootstrap_digest,
                       archive_path, archive_size_bytes, archive_sha256,
                       artifacts_json, ui_packages_json, source, created_at
                FROM profile_revisions
                WHERE profile_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (identifier,),
            ).fetchone()
        return self._profile_revision_from_row(row)

    def save_profile_revision(self, record):
        fields = {
            "profile_id",
            "revision",
            "base_revision",
            "bootstrap_digest",
            "archive_path",
            "archive_size_bytes",
            "archive_sha256",
            "artifacts",
            "ui_packages",
            "source",
            "created_at",
        }
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Invalid profile revision.")
        profile_id = _require_strict_identifier(record["profile_id"], "profile ID")
        revision = record["revision"]
        base_revision = record["base_revision"]
        size_bytes = record["archive_size_bytes"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
            or (
                base_revision is not None
                and (
                    isinstance(base_revision, bool)
                    or not isinstance(base_revision, int)
                    or not 0 < base_revision < revision
                )
            )
            or not isinstance(record["archive_path"], str)
            or not record["archive_path"]
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
            or not isinstance(record["artifacts"], list)
            or not isinstance(record["ui_packages"], list)
            or record["source"] not in {"local", "remote", "resolution"}
        ):
            raise ValueError("Invalid profile revision.")
        bootstrap_digest = _require_digest(
            record["bootstrap_digest"], "profile bootstrap digest"
        )
        archive_sha256 = _require_digest(
            record["archive_sha256"], "profile archive digest"
        )
        created_at = _require_finite_timestamp(record["created_at"], "profile timestamp")
        artifacts_json = _canonical_json(record["artifacts"])
        ui_packages_json = _canonical_json(record["ui_packages"])
        values = (
            profile_id,
            revision,
            base_revision,
            bootstrap_digest,
            record["archive_path"],
            size_bytes,
            archive_sha256,
            artifacts_json,
            ui_packages_json,
            record["source"],
            created_at,
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT profile_id, revision, base_revision, bootstrap_digest,
                       archive_path, archive_size_bytes, archive_sha256,
                       artifacts_json, ui_packages_json, source, created_at
                FROM profile_revisions
                WHERE profile_id = ? AND revision = ?
                """,
                (profile_id, revision),
            ).fetchone()
            if existing is not None:
                stored = self._profile_revision_from_row(existing)
                candidate = {
                    **record,
                    "profile_id": profile_id,
                    "bootstrap_digest": bootstrap_digest,
                    "archive_sha256": archive_sha256,
                    "created_at": created_at,
                }
                connection.rollback()
                if stored != candidate:
                    raise ValueError("Profile revision identity cannot change.")
                return stored
            latest = connection.execute(
                """
                SELECT MAX(revision) AS revision
                FROM profile_revisions
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()["revision"]
            expected_revision = 1 if latest is None else int(latest) + 1
            if revision != expected_revision or (
                revision == 1 and base_revision is not None
            ) or (
                revision > 1 and base_revision is None
            ):
                connection.rollback()
                raise ValueError("Profile revision sequence is invalid.")
            connection.execute(
                """
                INSERT INTO profile_revisions(
                    profile_id, revision, base_revision, bootstrap_digest,
                    archive_path, archive_size_bytes, archive_sha256,
                    artifacts_json, ui_packages_json, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            row = connection.execute(
                """
                SELECT profile_id, revision, base_revision, bootstrap_digest,
                       archive_path, archive_size_bytes, archive_sha256,
                       artifacts_json, ui_packages_json, source, created_at
                FROM profile_revisions
                WHERE profile_id = ? AND revision = ?
                """,
                (profile_id, revision),
            ).fetchone()
            connection.commit()
        return self._profile_revision_from_row(row)

    @staticmethod
    def _profile_conflict_from_row(row):
        if row is None:
            return None
        return {
            "conflict_id": row["conflict_id"],
            "profile_id": row["profile_id"],
            "base_revision": int(row["base_revision"]),
            "local_revision": int(row["local_revision"]),
            "remote_revision": int(row["remote_revision"]),
            "resolved_revision": (
                int(row["resolved_revision"])
                if row["resolved_revision"] is not None
                else None
            ),
            "created_at": float(row["created_at"]),
        }

    def save_profile_conflict(self, record):
        fields = {
            "conflict_id",
            "profile_id",
            "base_revision",
            "local_revision",
            "remote_revision",
            "resolved_revision",
            "created_at",
        }
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Invalid profile conflict.")
        conflict_id = _require_strict_identifier(record["conflict_id"], "conflict ID")
        profile_id = _require_strict_identifier(record["profile_id"], "profile ID")
        revisions = (
            record["base_revision"],
            record["local_revision"],
            record["remote_revision"],
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in revisions
        ) or not revisions[0] < revisions[1] < revisions[2] or record[
            "resolved_revision"
        ] is not None:
            raise ValueError("Invalid profile conflict.")
        created_at = _require_finite_timestamp(record["created_at"], "conflict timestamp")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM profile_conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
            candidate = {
                **record,
                "conflict_id": conflict_id,
                "profile_id": profile_id,
                "created_at": created_at,
            }
            if existing is not None:
                stored = self._profile_conflict_from_row(existing)
                connection.rollback()
                if stored != candidate:
                    raise ValueError("Profile conflict identity cannot change.")
                return stored
            connection.execute(
                """
                INSERT INTO profile_conflicts(
                    conflict_id, profile_id, base_revision, local_revision,
                    remote_revision, resolved_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    conflict_id,
                    profile_id,
                    revisions[0],
                    revisions[1],
                    revisions[2],
                    created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM profile_conflicts WHERE conflict_id = ?",
                (conflict_id,),
            ).fetchone()
            connection.commit()
        return self._profile_conflict_from_row(row)

    def get_profile_conflict(self, conflict_id):
        identifier = _require_strict_identifier(conflict_id, "conflict ID")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM profile_conflicts WHERE conflict_id = ?",
                (identifier,),
            ).fetchone()
        return self._profile_conflict_from_row(row)

    def list_profile_conflicts(self, profile_id, *, unresolved_only=False):
        identifier = _require_strict_identifier(profile_id, "profile ID")
        if not isinstance(unresolved_only, bool):
            raise ValueError("Invalid profile conflict filter.")
        query = "SELECT * FROM profile_conflicts WHERE profile_id = ?"
        if unresolved_only:
            query += " AND resolved_revision IS NULL"
        query += " ORDER BY created_at, conflict_id"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, (identifier,)).fetchall()
        return tuple(self._profile_conflict_from_row(row) for row in rows)

    def resolve_profile_conflict(self, conflict_id, resolved_revision):
        identifier = _require_strict_identifier(conflict_id, "conflict ID")
        if (
            isinstance(resolved_revision, bool)
            or not isinstance(resolved_revision, int)
            or resolved_revision <= 0
        ):
            raise ValueError("Invalid profile conflict resolution.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM profile_conflicts WHERE conflict_id = ?",
                (identifier,),
            ).fetchone()
            if row is None or row["resolved_revision"] is not None or (
                resolved_revision <= int(row["remote_revision"])
            ):
                connection.rollback()
                raise ValueError("Profile conflict cannot be resolved.")
            updated = connection.execute(
                """
                UPDATE profile_conflicts
                SET resolved_revision = ?
                WHERE conflict_id = ? AND resolved_revision IS NULL
                """,
                (resolved_revision, identifier),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise ValueError("Profile conflict cannot be resolved.")
            result = connection.execute(
                "SELECT * FROM profile_conflicts WHERE conflict_id = ?",
                (identifier,),
            ).fetchone()
            connection.commit()
        return self._profile_conflict_from_row(result)

    @staticmethod
    def _profile_sync_from_row(row):
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "profile_id": row["profile_id"],
            "remote_revision": int(row["remote_revision"]),
            "archive_sha256": row["archive_sha256"],
            "warning": row["warning"],
            "updated_at": float(row["updated_at"]),
        }

    def get_profile_sync_state(self, session_id):
        identifier = _require_strict_identifier(session_id, "session ID")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM profile_sync_state WHERE session_id = ?",
                (identifier,),
            ).fetchone()
        return self._profile_sync_from_row(row)

    def save_profile_sync_state(self, record):
        fields = {
            "session_id",
            "profile_id",
            "remote_revision",
            "archive_sha256",
            "warning",
            "updated_at",
        }
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Invalid profile synchronization state.")
        session_id = _require_strict_identifier(record["session_id"], "session ID")
        profile_id = _require_strict_identifier(record["profile_id"], "profile ID")
        revision = record["remote_revision"]
        digest = record["archive_sha256"]
        warning = record["warning"]
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or (revision == 0) != (digest is None)
            or (
                digest is not None
                and (
                    not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                )
            )
            or (
                warning is not None
                and (
                    not isinstance(warning, str)
                    or not warning
                    or len(warning) > 500
                    or any(ord(character) < 32 for character in warning)
                    or any(
                        marker in warning.casefold()
                        for marker in (
                            "://",
                            "bearer ",
                            "token=",
                            "secret",
                            "/users/",
                        )
                    )
                )
            )
        ):
            raise ValueError("Invalid profile synchronization state.")
        updated_at = _require_finite_timestamp(
            record["updated_at"],
            "profile synchronization timestamp",
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM profile_sync_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is not None:
                prior = self._profile_sync_from_row(existing)
                if (
                    prior["profile_id"] != profile_id
                    or revision < prior["remote_revision"]
                    or (
                        revision == prior["remote_revision"]
                        and digest != prior["archive_sha256"]
                    )
                ):
                    connection.rollback()
                    raise ValueError(
                        "Profile synchronization identity cannot regress."
                    )
            connection.execute(
                """
                INSERT INTO profile_sync_state(
                    session_id, profile_id, remote_revision,
                    archive_sha256, warning, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    remote_revision = excluded.remote_revision,
                    archive_sha256 = excluded.archive_sha256,
                    warning = excluded.warning,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    profile_id,
                    revision,
                    digest,
                    warning,
                    updated_at,
                ),
            )
            result = connection.execute(
                "SELECT * FROM profile_sync_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            connection.commit()
        return self._profile_sync_from_row(result)

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
            execution_state=ExecutionState(row["execution_state"]),
            harvest_state=HarvestState(row["harvest_state"]),
            error_code=(
                RunErrorCode(row["error_code"])
                if row["error_code"] is not None
                else None
            ),
            queue_position=int(row["queue_position"]),
            native_request_digest=row["native_request_digest"],
            native_body_json=row["native_body_json"],
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
            job.execution_state.value,
            job.harvest_state.value,
            job.error_code.value if job.error_code is not None else None,
            job.queue_position,
            job.native_request_digest,
            job.native_body_json,
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

    def list_jobs(self, session_id):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_JOB_COLUMNS}
                FROM jobs
                WHERE session_id = ?
                ORDER BY queue_position, created_at, job_id
                """,
                (str(session_id),),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

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
            next_position = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(queue_position), 0) + 1
                    FROM jobs WHERE session_id = ?
                    """,
                    (job.session_id,),
                ).fetchone()[0]
            )
            if job.queue_position == 0:
                job = replace(job, queue_position=next_position)
            elif job.queue_position != next_position:
                connection.rollback()
                raise ValueError("Invalid job queue position.")
            try:
                connection.execute(
                    f"""
                    INSERT INTO jobs ({_JOB_COLUMNS})
                    VALUES ({",".join("?" for _ in range(18))})
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
                    execution_state = ?,
                    harvest_state = ?,
                    error_code = ?,
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
                    job.execution_state.value,
                    job.harvest_state.value,
                    job.error_code.value if job.error_code is not None else None,
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

    @staticmethod
    def _row_to_journal(row):
        if row is None:
            return None
        try:
            details = json.loads(row["details_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("Stored journal evidence is invalid.") from None
        return RunJournalEntry(
            entry_id=row["entry_id"],
            session_id=row["session_id"],
            manifest_digest=row["manifest_digest"],
            transaction_id=row["transaction_id"],
            job_id=row["job_id"],
            phase=RunPhase(row["phase"]),
            code=RunErrorCode(row["code"]),
            message=row["message"],
            node_id=row["node_id"],
            process_exit_code=(
                int(row["process_exit_code"])
                if row["process_exit_code"] is not None
                else None
            ),
            restart_count=int(row["restart_count"]),
            last_probe=row["last_probe"],
            byte_cursor=int(row["byte_cursor"]),
            event_cursor=int(row["event_cursor"]),
            output_state=row["output_state"],
            details=details,
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _journal_values(entry):
        if not isinstance(entry, RunJournalEntry):
            raise TypeError("A validated journal entry is required.")
        return (
            entry.entry_id,
            entry.session_id,
            entry.manifest_digest,
            entry.transaction_id,
            entry.job_id,
            entry.phase.value,
            entry.code.value,
            entry.message,
            entry.node_id,
            entry.process_exit_code,
            entry.restart_count,
            entry.last_probe,
            entry.byte_cursor,
            entry.event_cursor,
            entry.output_state,
            _canonical_json(entry.details),
            entry.created_at,
        )

    def record_journal(self, entry):
        values = self._journal_values(entry)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"""
                INSERT OR IGNORE INTO run_journal ({_JOURNAL_COLUMNS})
                VALUES ({",".join("?" for _ in range(17))})
                """,
                values,
            )
            if cursor.rowcount == 1:
                connection.commit()
                return True
            row = connection.execute(
                f"""
                SELECT {_JOURNAL_COLUMNS}
                FROM run_journal
                WHERE entry_id = ?
                """,
                (entry.entry_id,),
            ).fetchone()
            connection.commit()
        if self._row_to_journal(row) != entry:
            raise ValueError("Journal entry identity cannot change.")
        return False

    def list_journal(self, session_id, *, after=0.0):
        session_identifier = _require_strict_identifier(
            session_id,
            "session ID",
        )
        threshold = _require_finite_timestamp(after, "journal timestamp")
        if threshold < 0:
            raise ValueError("Invalid journal timestamp.")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT {_JOURNAL_COLUMNS}
                FROM run_journal
                WHERE session_id = ? AND created_at > ?
                ORDER BY created_at, entry_id
                LIMIT 10000
                """,
                (session_identifier, threshold),
            ).fetchall()
        return [self._row_to_journal(row) for row in rows]

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

    def save_capture(self, capture, *, created_at=None):
        if not isinstance(capture, CompiledCapture):
            raise TypeError("A compiled capture is required.")
        encoded = capture.canonical_payload()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT prompt_digest, capture_json
                FROM captures
                WHERE capture_id = ?
                """,
                (capture.capture_id,),
            ).fetchone()
            if existing is not None and (
                existing["prompt_digest"] != capture.prompt_digest
                or existing["capture_json"] != encoded
            ):
                connection.rollback()
                raise ValueError("A capture identity cannot change content.")
            connection.execute(
                """
                INSERT OR IGNORE INTO captures(
                    capture_id, prompt_digest, capture_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    capture.capture_id,
                    capture.prompt_digest,
                    encoded,
                    float(time.time() if created_at is None else created_at),
                ),
            )
            connection.commit()
        return capture

    def get_capture(self, capture_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT capture_id, prompt_digest, capture_json
                FROM captures
                WHERE capture_id = ?
                """,
                (str(capture_id),),
            ).fetchone()
        if row is None:
            return None
        return CompiledCapture.from_record(
            row["capture_id"],
            row["capture_json"],
            row["prompt_digest"],
        )

    def get_capture_by_prompt_digest(self, prompt_digest):
        digest = _require_digest(prompt_digest, "prompt digest")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT capture_id, prompt_digest, capture_json
                FROM captures
                WHERE prompt_digest = ?
                ORDER BY created_at, capture_id
                LIMIT 1
                """,
                (digest,),
            ).fetchone()
        if row is None:
            return None
        return CompiledCapture.from_record(
            row["capture_id"],
            row["capture_json"],
            row["prompt_digest"],
        )

    def register_local_artifact(self, artifact, *, created_at=None):
        try:
            artifact_id = _require_identifier(
                artifact.artifact_id,
                "artifact ID",
            )
            private_path = str(artifact.private_path)
            size_bytes = int(artifact.size_bytes)
            sha256 = _require_digest(
                artifact.sha256,
                "artifact digest",
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Local artifact metadata is invalid.") from None
        if (
            not private_path
            or isinstance(artifact.size_bytes, bool)
            or size_bytes <= 0
        ):
            raise ValueError("Local artifact metadata is invalid.")
        from .artifacts import ArtifactPathError, hash_file

        try:
            current = hash_file(private_path)
        except ArtifactPathError:
            raise ValueError("Local artifact is unavailable.") from None
        if current.size_bytes != size_bytes or current.sha256 != sha256:
            raise ValueError("Local artifact content changed.")
        timestamp = float(
            time.time() if created_at is None else created_at
        )
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Local artifact timestamp is invalid.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT private_path, size_bytes, sha256
                FROM local_artifacts
                WHERE artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()
            identity = (private_path, size_bytes, sha256)
            if existing is not None and (
                existing["private_path"],
                int(existing["size_bytes"]),
                existing["sha256"],
            ) != identity:
                connection.rollback()
                raise ValueError("Local artifact identity cannot change.")
            connection.execute(
                """
                INSERT OR IGNORE INTO local_artifacts(
                    artifact_id, private_path, size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    private_path,
                    size_bytes,
                    sha256,
                    timestamp,
                ),
            )
            connection.commit()
        return LocalArtifactRecord(
            artifact_id=artifact_id,
            private_path=private_path,
            size_bytes=size_bytes,
            sha256=sha256,
        )

    def get_local_artifact(self, artifact_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT artifact_id, private_path, size_bytes, sha256
                FROM local_artifacts
                WHERE artifact_id = ?
                """,
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            return None
        return LocalArtifactRecord(
            artifact_id=row["artifact_id"],
            private_path=row["private_path"],
            size_bytes=int(row["size_bytes"]),
            sha256=row["sha256"],
        )

    def cache_configured(self):
        return False

    def save_preflight(
        self,
        preflight_id,
        capture_id,
        payload,
        *,
        created_at=None,
    ):
        identifier = _require_identifier(preflight_id, "preflight ID")
        capture_identifier = _require_identifier(capture_id, "capture ID")
        if not isinstance(payload, dict):
            raise ValueError("Preflight result must be an object.")
        encoded = _canonical_json(payload)
        timestamp = float(time.time() if created_at is None else created_at)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT capture_id, result_json
                FROM preflights
                WHERE preflight_id = ?
                """,
                (identifier,),
            ).fetchone()
            if existing is not None and (
                existing["capture_id"] != capture_identifier
                or existing["result_json"] != encoded
            ):
                connection.rollback()
                raise ValueError(
                    "A preflight identity cannot change content."
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO preflights(
                    preflight_id, capture_id, result_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    identifier,
                    capture_identifier,
                    encoded,
                    timestamp,
                ),
            )
            connection.commit()

    def get_preflight(self, preflight_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT preflight_id, capture_id, result_json, created_at
                FROM preflights
                WHERE preflight_id = ?
                """,
                (str(preflight_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError):
            raise ValueError("Stored preflight is invalid.") from None
        if not isinstance(payload, dict):
            raise ValueError("Stored preflight is invalid.")
        return StoredPreflight(
            preflight_id=row["preflight_id"],
            capture_id=row["capture_id"],
            payload=payload,
            created_at=float(row["created_at"]),
        )

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
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            raise ValueError("Event sequence must be positive.")
        number = sequence
        kind = _require_identifier(event_type, "event type")
        if not isinstance(payload, dict):
            raise ValueError("Event payload must be an object.")
        encoded = _canonical_json(payload)
        timestamp = float(
            time.time() if created_at is None else created_at
        )
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Event timestamp is invalid.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT event_type, payload_json, created_at
                FROM job_events
                WHERE job_id = ? AND sequence = ?
                """,
                (identifier, number),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != kind
                    or existing["payload_json"] != encoded
                ):
                    connection.rollback()
                    raise ValueError("Event identity cannot change.")
                connection.commit()
                return JobEvent(
                    job_id=identifier,
                    sequence=number,
                    event_type=kind,
                    payload=json.loads(encoded),
                    created_at=float(existing["created_at"]),
                )
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS last_sequence
                FROM job_events
                WHERE job_id = ?
                """,
                (identifier,),
            ).fetchone()
            if number != int(row["last_sequence"]) + 1:
                connection.rollback()
                raise ValueError("Event sequences must be contiguous.")
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
                    encoded,
                    timestamp,
                ),
            )
            connection.commit()
        return JobEvent(
            job_id=identifier,
            sequence=number,
            event_type=kind,
            payload=json.loads(encoded),
            created_at=timestamp,
        )

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

    def last_event_sequence(self, job_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS last_sequence
                FROM job_events
                WHERE job_id = ?
                """,
                (str(job_id),),
            ).fetchone()
        return int(row["last_sequence"])

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
        source_node_id=None,
        published_device=None,
        published_inode=None,
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
        node_id = _require_strict_identifier(
            source_node_id,
            "source node ID",
            optional=True,
        )
        if (published_device is None) != (published_inode is None):
            raise ValueError("Invalid published output identity.")
        if published_device is None:
            device = inode = None
        else:
            if (
                isinstance(published_device, bool)
                or not isinstance(published_device, int)
                or published_device < 0
                or isinstance(published_inode, bool)
                or not isinstance(published_inode, int)
                or published_inode < 0
                or node_id is None
                or normalized_direction != "download"
                or artifact_identifier.startswith("preview:")
                or transfer_state != TransferState.VERIFIED
            ):
                raise ValueError("Invalid published output identity.")
            device = published_device
            inode = published_inode
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT
                    expected_size, sha256, offset, direction,
                    source_node_id, published_device, published_inode
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
                for stored, candidate in (
                    (existing["source_node_id"], node_id),
                    (existing["published_device"], device),
                    (existing["published_inode"], inode),
                ):
                    if stored is not None and stored != candidate:
                        connection.rollback()
                        raise ValueError("Transfer identity cannot change.")
                if current < int(existing["offset"]):
                    connection.rollback()
                    raise ValueError("Transfer offsets cannot move backwards.")
            connection.execute(
                """
                INSERT INTO transfers(
                    job_id, artifact_id, direction, expected_size, sha256,
                    offset, state, private_path, source_node_id,
                    published_device, published_inode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, artifact_id) DO UPDATE SET
                    offset = excluded.offset,
                    state = excluded.state,
                    private_path = excluded.private_path,
                    source_node_id = COALESCE(
                        transfers.source_node_id,
                        excluded.source_node_id
                    ),
                    published_device = COALESCE(
                        transfers.published_device,
                        excluded.published_device
                    ),
                    published_inode = COALESCE(
                        transfers.published_inode,
                        excluded.published_inode
                    )
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
                    node_id,
                    device,
                    inode,
                ),
            )
            connection.commit()

    def get_transfer(self, job_id, artifact_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    job_id, artifact_id, direction, expected_size, sha256,
                    offset, state, private_path, source_node_id,
                    published_device, published_inode
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
            source_node_id=row["source_node_id"],
            published_device=(
                int(row["published_device"])
                if row["published_device"] is not None
                else None
            ),
            published_inode=(
                int(row["published_inode"])
                if row["published_inode"] is not None
                else None
            ),
        )

    def list_transfers(self, job_id):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    job_id, artifact_id, direction, expected_size, sha256,
                    offset, state, private_path, source_node_id,
                    published_device, published_inode
                FROM transfers
                WHERE job_id = ?
                ORDER BY artifact_id
                """,
                (str(job_id),),
            ).fetchall()
        return [
            TransferRecord(
                job_id=row["job_id"],
                artifact_id=row["artifact_id"],
                direction=row["direction"],
                expected_size=int(row["expected_size"]),
                sha256=row["sha256"],
                offset=int(row["offset"]),
                state=TransferState(row["state"]),
                private_path=row["private_path"],
                source_node_id=row["source_node_id"],
                published_device=(
                    int(row["published_device"])
                    if row["published_device"] is not None
                    else None
                ),
                published_inode=(
                    int(row["published_inode"])
                    if row["published_inode"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    def reset_transfer(self, job_id, artifact_id):
        job_identifier = _require_identifier(job_id, "job ID")
        artifact_identifier = _require_identifier(
            artifact_id,
            "artifact ID",
        )
        with closing(self._connect()) as connection:
            connection.execute(
                """
                DELETE FROM transfers
                WHERE job_id = ? AND artifact_id = ?
                """,
                (job_identifier, artifact_identifier),
            )
            connection.commit()

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
                    phase, current_dependency_id, transferred_bytes,
                    total_bytes, last_progress_at, sanitized_error
                FROM provision_transactions
                WHERE transaction_id = ?
                """,
                (str(transaction_id),),
            ).fetchone()
        return self._provision_transaction_from_row(row)

    @staticmethod
    def _provision_transaction_from_row(row):
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
            phase=row["phase"],
            current_dependency_id=row["current_dependency_id"],
            transferred_bytes=int(row["transferred_bytes"]),
            total_bytes=int(row["total_bytes"]),
            last_progress_at=float(row["last_progress_at"]),
            sanitized_error=row["sanitized_error"],
        )

    def record_provision_progress(
        self,
        *,
        transaction_id,
        session_id,
        job_id,
        manifest_digest,
        state,
        phase,
        current_dependency_id,
        transferred_bytes,
        total_bytes,
        last_progress_at,
        sanitized_error=None,
    ):
        phase, current_dependency_id, transferred_bytes, total_bytes = (
            _provision_progress_values(
                phase=phase,
                current_dependency_id=current_dependency_id,
                transferred_bytes=transferred_bytes,
                total_bytes=total_bytes,
            )
        )
        values = (
            _require_strict_identifier(
                transaction_id,
                "transaction ID",
            ),
            _require_strict_identifier(session_id, "session ID"),
            _require_strict_identifier(
                job_id,
                "job ID",
                optional=True,
            ),
            _require_digest(manifest_digest, "manifest digest"),
            _require_strict_identifier(state, "transaction state"),
            phase,
            current_dependency_id,
            transferred_bytes,
            total_bytes,
            _require_finite_timestamp(
                last_progress_at,
                "last progress timestamp",
            ),
            _require_sanitized_error(sanitized_error),
        )
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO provision_transactions(
                    transaction_id, session_id, job_id, manifest_digest, state,
                    planned_restart_count, repair_count, repair_restart_count,
                    phase, current_dependency_id, transferred_bytes,
                    total_bytes, last_progress_at, sanitized_error
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    state = excluded.state,
                    phase = excluded.phase,
                    current_dependency_id = excluded.current_dependency_id,
                    transferred_bytes = excluded.transferred_bytes,
                    total_bytes = excluded.total_bytes,
                    last_progress_at = excluded.last_progress_at,
                    sanitized_error = excluded.sanitized_error
                WHERE
                    provision_transactions.session_id = excluded.session_id
                    AND provision_transactions.manifest_digest =
                        excluded.manifest_digest
                    AND provision_transactions.transferred_bytes <=
                        excluded.transferred_bytes
                    AND (
                        provision_transactions.total_bytes = 0
                        OR provision_transactions.total_bytes =
                            excluded.total_bytes
                    )
                    AND provision_transactions.last_progress_at <=
                        excluded.last_progress_at
                """,
                values,
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ValueError(
                    "Provisioning progress identity or order changed."
                )
            connection.commit()
        return self.get_provision_transaction(values[0])

    def latest_provision_transaction(self, session_id):
        session_identifier = _require_strict_identifier(
            session_id,
            "session ID",
        )
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    transaction_id, session_id, job_id, manifest_digest, state,
                    planned_restart_count, repair_count, repair_restart_count,
                    phase, current_dependency_id, transferred_bytes,
                    total_bytes, last_progress_at, sanitized_error
                FROM provision_transactions
                WHERE session_id = ?
                ORDER BY last_progress_at DESC, transaction_id DESC
                LIMIT 1
                """,
                (session_identifier,),
            ).fetchone()
        return self._provision_transaction_from_row(row)

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
            if revision is not None and not _INSTALLED_REVISION.fullmatch(
                str(revision)
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

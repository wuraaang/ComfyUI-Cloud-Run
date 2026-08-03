"""Atomic private state for a single claimed Cloud Run worker session."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

if "." in (__package__ or ""):
    from ..cloud_run.worker_protocol import PROTOCOL_VERSION
else:
    from cloud_run.worker_protocol import PROTOCOL_VERSION

STATE_SCHEMA_VERSION = 3
MAX_STATE_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_JOB_STATES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "interrupted",
}
_EXECUTION_STATES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "interrupted",
}
_HARVEST_STATES = {"pending", "running", "succeeded", "failed"}
_JOB_RECORD_FIELDS = {
    "kind",
    "job_id",
    "request_digest",
    "request_id",
    "manifest_digest",
    "state",
    "client_id",
    "prompt_id",
    "native_response",
    "execution_state",
    "harvest_state",
    "sequence",
    "events",
    "previews",
    "outputs",
    "error",
    "created_at",
    "updated_at",
}
_SCHEMA_ONE_JOB_RECORD_FIELDS = _JOB_RECORD_FIELDS - {
    "created_at",
    "request_id",
    "native_response",
    "execution_state",
    "harvest_state",
}
_SCHEMA_TWO_JOB_RECORD_FIELDS = _JOB_RECORD_FIELDS - {
    "request_id",
    "native_response",
    "execution_state",
    "harvest_state",
}
_PROVISION_STATES = {
    "applying",
    "awaiting_upload",
    "ready",
    "failed",
    "stalled",
}
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
_PROVISION_RECORD_FIELDS = {
    "kind",
    "transaction_id",
    "manifest_digest",
    "manifest",
    "required_class_types",
    "state",
    "planned_restarts",
    "repair_restarts",
    "repair_used",
    "missing_class_types",
    "missing_artifacts",
    "failure_code",
    "updated_at",
    "last_progress_at",
    "progress",
}
_PROGRESS_FIELDS = {
    "phase",
    "dependency_id",
    "transferred_bytes",
    "total_bytes",
}
_STATE_FIELDS = {
    "schema_version",
    "protocol_version",
    "session_id",
    "session_secret_hex",
    "claimed",
    "deadline_at",
    "deadline_mode",
    "installed",
    "transactions",
    "jobs",
}
_UNCHANGED = object()


class WorkerStateError(RuntimeError):
    pass


class WorkerAlreadyClaimed(WorkerStateError):
    pass


class WorkerSessionMismatch(WorkerStateError):
    pass


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value)


def _finite_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _safe_native_response_tree(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64:
            return False
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    return False
                folded = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    key.casefold(),
                ).strip("_")
                components = set(folded.split("_"))
                if components.intersection(
                    {
                        "authorization",
                        "bearer",
                        "cookie",
                        "password",
                        "secret",
                        "token",
                    }
                ) or folded.endswith(
                    (
                        "_api_key",
                        "_access_key",
                        "_access_token",
                        "_signed_url",
                    )
                ):
                    return False
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                return False
        elif item is not None and not isinstance(item, (bool, int, str)):
            return False
    return True


def _validate_job_record(job_id, record):
    if (
        not _identifier(job_id)
        or not isinstance(record, dict)
        or set(record) != _JOB_RECORD_FIELDS
        or record.get("kind") != "job"
        or record.get("job_id") != job_id
        or not isinstance(record.get("request_digest"), str)
        or not _HEX_64.fullmatch(record["request_digest"])
        or not _identifier(record.get("request_id"))
        or not isinstance(record.get("manifest_digest"), str)
        or not _HEX_64.fullmatch(record["manifest_digest"])
        or record.get("state") not in _JOB_STATES
        or not _identifier(record.get("client_id"))
        or (
            record.get("prompt_id") is not None
            and not _identifier(record["prompt_id"])
        )
        or record.get("execution_state") not in _EXECUTION_STATES
        or record.get("harvest_state") not in _HARVEST_STATES
        or isinstance(record.get("sequence"), bool)
        or not isinstance(record.get("sequence"), int)
        or record["sequence"] < 0
        or not isinstance(record.get("events"), list)
        or not isinstance(record.get("previews"), dict)
        or not isinstance(record.get("outputs"), dict)
        or (
            record.get("error") is not None
            and not isinstance(record["error"], dict)
        )
        or not _finite_number(record.get("created_at"))
        or record["created_at"] < 0
        or not _finite_number(record.get("updated_at"))
        or record["updated_at"] < 0
        or record["updated_at"] < record["created_at"]
    ):
        raise WorkerStateError("Worker job state is invalid.")
    response = record["native_response"]
    if response is not None:
        if (
            not isinstance(response, dict)
            or set(response) != {"prompt_id", "number", "node_errors"}
            or response.get("prompt_id") != record["prompt_id"]
            or not _canonical_uuid(response.get("prompt_id"))
            or isinstance(response.get("number"), bool)
            or not isinstance(response.get("number"), (int, float))
            or not math.isfinite(response["number"])
            or not isinstance(response.get("node_errors"), dict)
            or not _safe_native_response_tree(response)
        ):
            raise WorkerStateError("Worker job state is invalid.")
    if (
        (record["state"] == "queued" and record["execution_state"] != "queued")
        or (
            record["state"] == "running"
            and record["execution_state"] not in {"running", "succeeded"}
        )
        or (
            record["state"] == "succeeded"
            and (
                record["execution_state"] != "succeeded"
                or record["harvest_state"] != "succeeded"
            )
        )
        or (
            record["state"] == "failed"
            and record["execution_state"] not in {"failed", "succeeded"}
        )
        or (
            record["state"] == "interrupted"
            and record["execution_state"] != "interrupted"
        )
        or (
            record["execution_state"] != "succeeded"
            and record["harvest_state"] != "pending"
        )
    ):
        raise WorkerStateError("Worker job state is invalid.")
    if len(record["events"]) != record["sequence"]:
        raise WorkerStateError("Worker job state is invalid.")
    for expected_sequence, event in enumerate(record["events"], start=1):
        if (
            not isinstance(event, dict)
            or set(event) != {
                "sequence",
                "type",
                "data",
                "created_at",
            }
            or event.get("sequence") != expected_sequence
            or not _identifier(event.get("type"))
            or not isinstance(event.get("data"), dict)
            or not _finite_number(event.get("created_at"))
            or event["created_at"] < 0
        ):
            raise WorkerStateError("Worker job state is invalid.")
    for preview_id, preview in record["previews"].items():
        if (
            not _identifier(preview_id)
            or not isinstance(preview, dict)
            or preview.get("preview_id") != preview_id
            or not isinstance(preview.get("private_path"), str)
        ):
            raise WorkerStateError("Worker job state is invalid.")
    for artifact_id, output in record["outputs"].items():
        if (
            not _identifier(artifact_id)
            or not isinstance(output, dict)
            or output.get("artifact_id") != artifact_id
            or not isinstance(output.get("private_path"), str)
        ):
            raise WorkerStateError("Worker job state is invalid.")
    return record


def _validate_identifier_list(value):
    return (
        isinstance(value, list)
        and len(value) <= 100_000
        and all(_identifier(item) for item in value)
        and len(value) == len(set(value))
    )


def _validate_provision_progress(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _PROGRESS_FIELDS:
        raise WorkerStateError("Worker provisioning state is invalid.")
    phase = value.get("phase")
    dependency_id = value.get("dependency_id")
    transferred_bytes = value.get("transferred_bytes")
    total_bytes = value.get("total_bytes")
    if (
        not isinstance(phase, str)
        or phase not in _PROVISION_PHASES
        or isinstance(transferred_bytes, bool)
        or not isinstance(transferred_bytes, int)
        or isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or not 0 <= transferred_bytes <= total_bytes
        or (
            phase in _ARTIFACT_PROVISION_PHASES
            and not _identifier(dependency_id)
        )
        or (
            phase not in _ARTIFACT_PROVISION_PHASES
            and dependency_id is not None
        )
        or (phase == "ready" and transferred_bytes != total_bytes)
    ):
        raise WorkerStateError("Worker provisioning state is invalid.")


def _validate_provision_record(transaction_id, record):
    if (
        not _identifier(transaction_id)
        or not isinstance(record, dict)
        or set(record) != _PROVISION_RECORD_FIELDS
        or record.get("kind") != "provision"
        or record.get("transaction_id") != transaction_id
        or not isinstance(record.get("manifest_digest"), str)
        or not _HEX_64.fullmatch(record["manifest_digest"])
        or record.get("manifest") is not None
        or not _validate_identifier_list(
            record.get("required_class_types")
        )
        or record.get("state") not in _PROVISION_STATES
        or record.get("planned_restarts") not in {0, 1}
        or isinstance(record.get("planned_restarts"), bool)
        or record.get("repair_restarts") not in {0, 1}
        or isinstance(record.get("repair_restarts"), bool)
        or not isinstance(record.get("repair_used"), bool)
        or not _validate_identifier_list(
            record.get("missing_class_types")
        )
        or not _validate_identifier_list(record.get("missing_artifacts"))
        or (
            record.get("failure_code") is not None
            and not _identifier(record["failure_code"])
        )
        or not _finite_number(record.get("updated_at"))
        or not _finite_number(record.get("last_progress_at"))
    ):
        raise WorkerStateError("Worker provisioning state is invalid.")
    _validate_provision_progress(record.get("progress"))
    if (
        record["progress"] is not None
        and (record["progress"]["phase"] == "ready")
        != (record["state"] == "ready")
    ):
        raise WorkerStateError("Worker provisioning state is invalid.")
    return record


def _default_state(expected_session_id):
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "session_id": expected_session_id,
        "session_secret_hex": None,
        "claimed": False,
        "deadline_at": 0,
        "deadline_mode": "finite",
        "installed": {},
        "transactions": {},
        "jobs": {},
    }


def _legacy_execution_state(state):
    return {
        "queued": "queued",
        "running": "running",
        "succeeded": "succeeded",
        "failed": "failed",
        "interrupted": "interrupted",
    }.get(state)


def _migrate_state(state):
    if not isinstance(state, dict) or state.get("schema_version") not in {1, 2}:
        return state, False
    if set(state) != _STATE_FIELDS or not isinstance(state.get("jobs"), dict):
        raise WorkerStateError("Worker state is invalid.")
    source_schema = state["schema_version"]
    expected_fields = (
        _SCHEMA_ONE_JOB_RECORD_FIELDS
        if source_schema == 1
        else _SCHEMA_TWO_JOB_RECORD_FIELDS
    )
    jobs = {}
    for job_id, record in state["jobs"].items():
        if (
            not isinstance(record, dict)
            or set(record) != expected_fields
            or not _finite_number(record.get("updated_at"))
            or record["updated_at"] < 0
        ):
            raise WorkerStateError("Worker job state is invalid.")
        legacy_state = record.get("state")
        execution_state = _legacy_execution_state(legacy_state)
        if execution_state is None:
            raise WorkerStateError("Worker job state is invalid.")
        migrated = {
            **record,
            **(
                {"created_at": float(record["updated_at"])}
                if source_schema == 1
                else {}
            ),
            "request_id": job_id,
            "native_response": None,
            "execution_state": execution_state,
            "harvest_state": (
                "succeeded" if legacy_state == "succeeded" else "pending"
            ),
        }
        _validate_job_record(job_id, migrated)
        jobs[job_id] = migrated
    return {
        **state,
        "schema_version": STATE_SCHEMA_VERSION,
        "jobs": jobs,
    }, True


def _validate_state(state):
    if (
        not isinstance(state, dict)
        or set(state) != _STATE_FIELDS
        or state.get("schema_version") != STATE_SCHEMA_VERSION
        or isinstance(state.get("schema_version"), bool)
        or state.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(state.get("claimed"), bool)
        or state.get("deadline_mode") not in {"finite", "none"}
        or not isinstance(state.get("installed"), dict)
        or not isinstance(state.get("transactions"), dict)
        or not isinstance(state.get("jobs"), dict)
    ):
        raise WorkerStateError("Worker state is invalid.")
    session_id = state.get("session_id")
    secret = state.get("session_secret_hex")
    if state["claimed"]:
        if not _identifier(session_id) or not (
            isinstance(secret, str) and _HEX_64.fullmatch(secret)
        ):
            raise WorkerStateError("Worker state is invalid.")
    elif (
        (session_id is not None and not _identifier(session_id))
        or secret is not None
    ):
        raise WorkerStateError("Worker state is invalid.")
    deadline = state.get("deadline_at")
    if state["deadline_mode"] == "finite":
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or deadline < 0
        ):
            raise WorkerStateError("Worker state is invalid.")
    elif deadline is not None:
        raise WorkerStateError("Worker state is invalid.")
    for job_id, record in state["jobs"].items():
        _validate_job_record(job_id, record)
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise WorkerStateError("Worker state is invalid.") from None
    if len(encoded) > MAX_STATE_BYTES:
        raise WorkerStateError("Worker state is invalid.")
    return encoded


class WorkerStateStore:
    def __init__(self, path, *, expected_session_id=None):
        if (
            expected_session_id is not None
            and not _identifier(expected_session_id)
        ):
            raise WorkerStateError("Worker session identity is invalid.")
        self.path = Path(path)
        self.expected_session_id = expected_session_id

    def load(self):
        if not self.path.exists():
            return _default_state(self.expected_session_id)
        descriptor = None
        migrated = False
        state = None
        try:
            parent_metadata = os.lstat(self.path.parent)
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or parent_metadata.st_uid != os.getuid()
                or parent_metadata.st_mode & 0o077
            ):
                raise WorkerStateError("Worker state is unavailable.")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
                or not 0 < metadata.st_size <= MAX_STATE_BYTES
            ):
                raise WorkerStateError("Worker state is unavailable.")
            content = os.read(descriptor, MAX_STATE_BYTES + 1)
            if len(content) != metadata.st_size:
                raise WorkerStateError("Worker state is unavailable.")
            state = json.loads(content.decode("utf-8"))
            state, migrated = _migrate_state(state)
            _validate_state(state)
            if (
                self.expected_session_id is not None
                and state.get("session_id")
                != self.expected_session_id
            ):
                raise WorkerStateError("Worker state is unavailable.")
        except WorkerStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise WorkerStateError("Worker state is unavailable.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if migrated:
            return self.save(state)
        return state

    def save(self, state):
        encoded = _validate_state(state)
        if (
            self.expected_session_id is not None
            and state.get("session_id") != self.expected_session_id
        ):
            raise WorkerSessionMismatch(
                "Worker session identity does not match."
            )
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".worker-state-",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            os.chmod(self.path, 0o600)
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            directory_descriptor = os.open(
                self.path.parent,
                directory_flags,
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except WorkerStateError:
            raise
        except OSError:
            raise WorkerStateError("Worker state is unavailable.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
        return dict(state)

    def claim(self, *, session_id, session_secret_hex):
        if not _identifier(session_id) or not (
            isinstance(session_secret_hex, str)
            and _HEX_64.fullmatch(session_secret_hex)
        ):
            raise WorkerSessionMismatch(
                "Worker session identity does not match."
            )
        state = self.load()
        if state["claimed"]:
            raise WorkerAlreadyClaimed("Worker is already claimed.")
        expected = state.get("session_id") or self.expected_session_id
        if expected is not None and session_id != expected:
            raise WorkerSessionMismatch(
                "Worker session identity does not match."
            )
        claimed = {
            **state,
            "session_id": session_id,
            "session_secret_hex": session_secret_hex,
            "claimed": True,
        }
        return self.save(claimed)

    def secret_bytes(self):
        state = self.load()
        if not state["claimed"]:
            raise WorkerStateError("Worker is not claimed.")
        return bytes.fromhex(state["session_secret_hex"])

    def record_artifact_transfer(
        self,
        *,
        artifact_id,
        transfer_state,
        offset,
        size_bytes,
        sha256,
    ):
        if (
            not _identifier(artifact_id)
            or transfer_state not in {"receiving", "verified"}
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 <= offset <= size_bytes
            or size_bytes <= 0
            or not isinstance(sha256, str)
            or not _HEX_64.fullmatch(sha256)
            or (
                transfer_state == "verified"
                and offset != size_bytes
            )
        ):
            raise WorkerStateError("Worker transfer state is invalid.")
        state = self.load()
        if not state["claimed"]:
            raise WorkerStateError("Worker is not claimed.")
        transactions = dict(state["transactions"])
        transactions[f"transfer:{artifact_id}"] = {
            "kind": "artifact_transfer",
            "artifact_id": artifact_id,
            "state": transfer_state,
            "offset": offset,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
        return self.save({**state, "transactions": transactions})

    def record_transaction(
        self,
        transaction_id,
        record,
        *,
        installed=_UNCHANGED,
    ):
        if (
            not _identifier(transaction_id)
            or not isinstance(record, dict)
            or record.get("transaction_id") != transaction_id
            or not isinstance(record.get("kind"), str)
            or not _identifier(record["kind"])
            or (
                installed is not _UNCHANGED
                and not isinstance(installed, dict)
            )
        ):
            raise WorkerStateError("Worker transaction state is invalid.")
        if record["kind"] == "provision":
            _validate_provision_record(transaction_id, record)
        state = self.load()
        if not state["claimed"]:
            raise WorkerStateError("Worker is not claimed.")
        transactions = dict(state["transactions"])
        transactions[transaction_id] = dict(record)
        updated = {
            **state,
            "transactions": transactions,
        }
        if installed is not _UNCHANGED:
            updated["installed"] = dict(installed)
        return self.save(updated)

    def record_job(self, job_id, record):
        _validate_job_record(job_id, record)
        state = self.load()
        if not state["claimed"]:
            raise WorkerStateError("Worker is not claimed.")
        jobs = dict(state["jobs"])
        jobs[job_id] = dict(record)
        return self.save({**state, "jobs": jobs})

    def job(self, job_id):
        if not _identifier(job_id):
            raise WorkerStateError("Worker job identity is invalid.")
        record = self.load()["jobs"].get(job_id)
        if record is None:
            return None
        _validate_job_record(job_id, record)
        return dict(record)

    def record_deadline(
        self,
        *,
        mode,
        deadline_at,
        retrieval_grace_seconds,
        destroy_intent,
        destroy_intent_at,
        destroy_requested,
        updated_at,
    ):
        if (
            mode not in {"finite", "none"}
            or (
                mode == "finite"
                and (
                    not _finite_number(deadline_at)
                    or deadline_at < 0
                )
            )
            or (mode == "none" and deadline_at is not None)
            or isinstance(retrieval_grace_seconds, bool)
            or not isinstance(retrieval_grace_seconds, int)
            or not 0 <= retrieval_grace_seconds <= 300
            or not isinstance(destroy_intent, bool)
            or (
                destroy_intent
                and (
                    not _finite_number(destroy_intent_at)
                    or destroy_intent_at < 0
                )
            )
            or (not destroy_intent and destroy_intent_at is not None)
            or not isinstance(destroy_requested, bool)
            or (destroy_requested and not destroy_intent)
            or not _finite_number(updated_at)
            or updated_at < 0
        ):
            raise WorkerStateError("Worker deadline state is invalid.")
        state = self.load()
        if not state["claimed"]:
            raise WorkerStateError("Worker is not claimed.")
        transactions = dict(state["transactions"])
        transactions["deadline"] = {
            "kind": "deadline",
            "mode": mode,
            "deadline_at": (
                float(deadline_at) if deadline_at is not None else None
            ),
            "retrieval_grace_seconds": retrieval_grace_seconds,
            "destroy_intent": destroy_intent,
            "destroy_intent_at": (
                float(destroy_intent_at)
                if destroy_intent_at is not None
                else None
            ),
            "destroy_requested": destroy_requested,
            "updated_at": float(updated_at),
        }
        return self.save(
            {
                **state,
                "deadline_mode": mode,
                "deadline_at": (
                    float(deadline_at)
                    if deadline_at is not None
                    else None
                ),
                "transactions": transactions,
            }
        )

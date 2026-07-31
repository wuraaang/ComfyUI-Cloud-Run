"""Atomic private state for a single claimed Cloud Run worker session."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from cloud_run.worker_protocol import PROTOCOL_VERSION

STATE_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
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


class WorkerStateError(RuntimeError):
    pass


class WorkerAlreadyClaimed(WorkerStateError):
    pass


class WorkerSessionMismatch(WorkerStateError):
    pass


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value)


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
            _validate_state(state)
            if (
                self.expected_session_id is not None
                and state.get("session_id")
                != self.expected_session_id
            ):
                raise WorkerStateError("Worker state is unavailable.")
            return state
        except WorkerStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise WorkerStateError("Worker state is unavailable.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

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

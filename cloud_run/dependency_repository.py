"""Private persistence for dependency candidates and explicit approvals."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import time

from .manifest import ManifestValidationError, normalize_github_repository
from .repository import _canonical_json, _initialize_database, _private_connection


MAPPING_PRECEDENCE = {
    "approved": 0,
    "registry": 1,
    "installed_git": 2,
    "agent": 3,
    "manual": 4,
}
_PENDING_SOURCE_KINDS = frozenset(MAPPING_PRECEDENCE) - {"approved"}
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_ALLOWED_CANDIDATE_FIELDS = {
    "archive",
    "package_id",
    "provided_class_types",
    "repository_url",
    "revision",
    "subdirectory",
    "wheels",
}
_FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "command",
    "env",
    "script",
    "secret",
    "shell",
    "token",
}


class MappingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MappingCandidate:
    class_type: str
    source_kind: str
    candidate_digest: str
    repository_url: str
    revision: str
    package_id: str | None
    payload: dict
    approved: bool
    origin_source_kind: str

    def public_payload(self):
        return {
            "class_type": self.class_type,
            "source_kind": self.source_kind,
            "candidate_digest": self.candidate_digest,
            "repository_url": self.repository_url,
            "revision": self.revision,
            "package_id": self.package_id,
            "archive_complete": bool(self.payload.get("archive")),
            "wheels_complete": "wheels" in self.payload,
            "approved": self.approved,
            "origin_source_kind": self.origin_source_kind,
        }


def _identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise MappingValidationError(f"Invalid {name}.")
    return value


def _reject_forbidden_tree(value):
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, child in current.items():
                if not isinstance(key, str):
                    raise MappingValidationError(
                        "Candidate object keys must be strings."
                    )
                if key.casefold() in _FORBIDDEN_FIELDS:
                    raise MappingValidationError(
                        "Candidate contains a forbidden field."
                    )
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)
        elif current is not None and not isinstance(
            current,
            (bool, int, float, str),
        ):
            raise MappingValidationError(
                "Candidate contains an unsupported value."
            )


def _validate_subdirectory(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise MappingValidationError("Invalid package subdirectory.")
    path = PurePosixPath(value)
    if (
        str(path) != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise MappingValidationError("Invalid package subdirectory.")


def _validate_archive(value):
    if not isinstance(value, dict) or set(value) != {
        "artifact_id",
        "destination",
        "locator",
        "sha256",
        "size_bytes",
    }:
        raise MappingValidationError("Invalid candidate archive.")
    _identifier(value["artifact_id"], "archive artifact ID")
    if (
        not isinstance(value["destination"], str)
        or not value["destination"].startswith("custom_nodes/")
        or ".." in PurePosixPath(value["destination"]).parts
        or "\\" in value["destination"]
    ):
        raise MappingValidationError("Invalid candidate archive destination.")
    if (
        isinstance(value["size_bytes"], bool)
        or not isinstance(value["size_bytes"], int)
        or value["size_bytes"] <= 0
    ):
        raise MappingValidationError("Invalid candidate archive size.")
    if not isinstance(value["sha256"], str) or not _HEX_64.fullmatch(
        value["sha256"]
    ):
        raise MappingValidationError("Invalid candidate archive digest.")
    locator = value["locator"]
    if not isinstance(locator, str) or not locator.startswith("local-upload:"):
        raise MappingValidationError("Invalid candidate archive locator.")
    _identifier(locator.removeprefix("local-upload:"), "archive locator")


def _validate_wheels(value):
    if not isinstance(value, list):
        raise MappingValidationError("Candidate wheels must be a list.")
    filenames = set()
    for wheel in value:
        if not isinstance(wheel, dict) or set(wheel) != {
            "filename",
            "locator",
            "sha256",
            "size_bytes",
        }:
            raise MappingValidationError("Invalid candidate wheel.")
        filename = wheel["filename"]
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or not filename.endswith(".whl")
            or filename in filenames
        ):
            raise MappingValidationError("Invalid candidate wheel filename.")
        filenames.add(filename)
        if (
            isinstance(wheel["size_bytes"], bool)
            or not isinstance(wheel["size_bytes"], int)
            or wheel["size_bytes"] <= 0
        ):
            raise MappingValidationError("Invalid candidate wheel size.")
        if not isinstance(wheel["sha256"], str) or not _HEX_64.fullmatch(
            wheel["sha256"]
        ):
            raise MappingValidationError("Invalid candidate wheel digest.")
        locator = wheel["locator"]
        if not isinstance(locator, str) or not locator.startswith(
            "local-upload:"
        ):
            raise MappingValidationError("Invalid candidate wheel locator.")
        _identifier(locator.removeprefix("local-upload:"), "wheel locator")


def _validate_candidate_payload(candidate):
    if not isinstance(candidate, dict):
        raise MappingValidationError("Candidate must be an object.")
    _reject_forbidden_tree(candidate)
    if set(candidate) - _ALLOWED_CANDIDATE_FIELDS:
        raise MappingValidationError("Candidate fields are invalid.")
    if not {"repository_url", "revision"}.issubset(candidate):
        raise MappingValidationError("Candidate source identity is incomplete.")
    try:
        normalize_github_repository(candidate["repository_url"])
    except ManifestValidationError as error:
        raise MappingValidationError(str(error)) from None
    revision = candidate["revision"]
    if not isinstance(revision, str) or not _HEX_40.fullmatch(revision):
        raise MappingValidationError("Candidate revision must be immutable.")
    if "package_id" in candidate:
        _identifier(candidate["package_id"], "package ID")
    if "subdirectory" in candidate:
        _validate_subdirectory(candidate["subdirectory"])
    if "archive" in candidate:
        _validate_archive(candidate["archive"])
    if "wheels" in candidate:
        _validate_wheels(candidate["wheels"])
    if "provided_class_types" in candidate:
        class_types = candidate["provided_class_types"]
        if not isinstance(class_types, list) or not class_types:
            raise MappingValidationError("Candidate class types are invalid.")
        normalized = [
            _identifier(class_type, "provided class type")
            for class_type in class_types
        ]
        if len(set(normalized)) != len(normalized):
            raise MappingValidationError("Candidate class types must be unique.")
    encoded = _canonical_json(candidate)
    return json.loads(encoded), encoded


def _candidate_digest(class_type, source_kind, encoded):
    material = _canonical_json(
        {
            "class_type": class_type,
            "source_kind": source_kind,
            "candidate": json.loads(encoded),
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _candidate_from_row(row, *, approved=False):
    payload = json.loads(row["candidate_json"])
    origin = row["source_kind"]
    return MappingCandidate(
        class_type=row["class_type"],
        source_kind="approved" if approved else origin,
        candidate_digest=row["candidate_digest"],
        repository_url=payload["repository_url"],
        revision=payload["revision"],
        package_id=payload.get("package_id"),
        payload=payload,
        approved=approved,
        origin_source_kind=origin,
    )


class DependencyRepository:
    def __init__(self, path, *, clock=None):
        self.path = path
        self.clock = clock or time.time
        _initialize_database(self.path)

    def _connect(self):
        return _private_connection(self.path)

    def save_candidate(
        self,
        class_type,
        source_kind,
        candidate,
        *,
        approved=False,
    ):
        normalized_class = _identifier(class_type, "class type")
        if source_kind not in _PENDING_SOURCE_KINDS:
            raise MappingValidationError("Invalid candidate source kind.")
        if approved is not False:
            raise MappingValidationError(
                "Candidates require a separate explicit approval."
            )
        payload, encoded = _validate_candidate_payload(candidate)
        digest = _candidate_digest(normalized_class, source_kind, encoded)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT source_kind, candidate_json
                FROM dependency_candidates
                WHERE class_type = ? AND candidate_digest = ?
                """,
                (normalized_class, digest),
            ).fetchone()
            if existing is not None and (
                existing["source_kind"] != source_kind
                or existing["candidate_json"] != encoded
            ):
                connection.rollback()
                raise MappingValidationError(
                    "Candidate identity cannot change content."
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO dependency_candidates(
                    class_type, candidate_digest, source_kind,
                    candidate_json, approved, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                """,
                (
                    normalized_class,
                    digest,
                    source_kind,
                    encoded,
                    float(self.clock()),
                ),
            )
            connection.commit()
        return MappingCandidate(
            class_type=normalized_class,
            source_kind=source_kind,
            candidate_digest=digest,
            repository_url=payload["repository_url"],
            revision=payload["revision"],
            package_id=payload.get("package_id"),
            payload=payload,
            approved=False,
            origin_source_kind=source_kind,
        )

    def candidates(self, class_type):
        normalized_class = _identifier(class_type, "class type")
        rows = []
        approved = self.approved(normalized_class)
        if approved is not None:
            rows.append(approved)
        with closing(self._connect()) as connection:
            pending = connection.execute(
                """
                SELECT
                    class_type, candidate_digest, source_kind, candidate_json
                FROM dependency_candidates
                WHERE class_type = ? AND approved = 0
                """,
                (normalized_class,),
            ).fetchall()
        rows.extend(_candidate_from_row(row) for row in pending)
        rows.sort(
            key=lambda item: (
                MAPPING_PRECEDENCE[item.source_kind],
                item.candidate_digest,
            )
        )
        return rows

    def approve(self, class_type, candidate_digest):
        normalized_class = _identifier(class_type, "class type")
        digest = str(candidate_digest or "")
        if not _HEX_64.fullmatch(digest):
            raise MappingValidationError("Invalid candidate digest.")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    class_type, candidate_digest, source_kind, candidate_json
                FROM dependency_candidates
                WHERE class_type = ? AND candidate_digest = ?
                """,
                (normalized_class, digest),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise MappingValidationError(
                    "The candidate is no longer available."
                )
            _validate_candidate_payload(json.loads(row["candidate_json"]))
            connection.execute(
                """
                UPDATE dependency_candidates
                SET approved = 0
                WHERE class_type = ?
                """,
                (normalized_class,),
            )
            connection.execute(
                """
                UPDATE dependency_candidates
                SET approved = 1
                WHERE class_type = ? AND candidate_digest = ?
                """,
                (normalized_class, digest),
            )
            connection.execute(
                """
                INSERT INTO dependency_mappings(
                    class_type, candidate_digest, source_kind,
                    candidate_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(class_type) DO UPDATE SET
                    candidate_digest = excluded.candidate_digest,
                    source_kind = excluded.source_kind,
                    candidate_json = excluded.candidate_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_class,
                    digest,
                    row["source_kind"],
                    row["candidate_json"],
                    float(self.clock()),
                ),
            )
            connection.commit()
        return _candidate_from_row(row, approved=True)

    def approved(self, class_type):
        normalized_class = _identifier(class_type, "class type")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    class_type, candidate_digest, source_kind, candidate_json
                FROM dependency_mappings
                WHERE class_type = ?
                """,
                (normalized_class,),
            ).fetchone()
        if row is None:
            return None
        _validate_candidate_payload(json.loads(row["candidate_json"]))
        return _candidate_from_row(row, approved=True)

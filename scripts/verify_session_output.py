#!/usr/bin/env python3
"""Verify one output selected from durable Cloud Run ownership evidence."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_run.capture import certified_execution_baseline
from cloud_run.worker_protocol import CompiledCapture
from remote_worker.provision import dependency_manifest_from_record
from scripts.validate_gold_output import validate_gold_output
from scripts.validate_smoke_output import validate_smoke_output


_HEX_64 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_CAPTURE_ID = "00000000-0000-0000-0000-000000000000"
_MAX_EVIDENCE_FILE_BYTES = 4 * 1024 * 1024 * 1024 * 1024


class SessionOutputEvidenceError(RuntimeError):
    """A sanitized session-output evidence failure."""


@dataclass(frozen=True)
class SessionOutputEvidence:
    kind: str
    node_id: str
    dimensions: tuple[int, int]
    size_bytes: int
    output_sha256: str
    source_match: bool


def _invalid():
    return SessionOutputEvidenceError("evidence_invalid")


def _digest(value):
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise _invalid()
    return value


def _identifier(value):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise _invalid()
    return value


def _strict_json(value):
    def object_from_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=object_from_pairs)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise _invalid() from None


def _canonical_json(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError):
        raise _invalid() from None


def _approved_root(value):
    try:
        path = Path(value)
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _invalid()
        return path.resolve(strict=True)
    except SessionOutputEvidenceError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _invalid() from None


def _database_uri(value):
    try:
        path = Path(value)
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise _invalid()
        return path.resolve(strict=True).as_uri() + "?mode=ro"
    except SessionOutputEvidenceError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _invalid() from None


def _exactly_one(connection, query, parameters):
    rows = connection.execute(query, parameters).fetchall()
    if len(rows) != 1:
        raise _invalid()
    return rows[0]


def _integer(value, *, positive=False, nonnegative=False):
    if type(value) is not int:
        raise _invalid()
    if positive and value <= 0:
        raise _invalid()
    if nonnegative and value < 0:
        raise _invalid()
    return value


def _path_under_root(value, root):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _invalid()
    path = Path(value)
    if not path.is_absolute():
        raise _invalid()
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise _invalid()
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except SessionOutputEvidenceError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _invalid() from None
    return resolved, metadata


def _hash_regular_file(
    value,
    root,
    *,
    expected_size,
    expected_sha256,
    expected_device=None,
    expected_inode=None,
):
    size = _integer(expected_size, positive=True)
    digest = _digest(expected_sha256)
    if size > _MAX_EVIDENCE_FILE_BYTES:
        raise _invalid()
    if expected_device is not None:
        device = _integer(expected_device, nonnegative=True)
        inode = _integer(expected_inode, nonnegative=True)
    else:
        device = inode = None

    path, path_metadata = _path_under_root(value, root)
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != path_metadata.st_dev
            or before.st_ino != path_metadata.st_ino
            or before.st_size != path_metadata.st_size
            or before.st_size != size
            or (device is not None and before.st_dev != device)
            or (inode is not None and before.st_ino != inode)
        ):
            raise _invalid()

        calculated = hashlib.sha256()
        bytes_read = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > size:
                raise _invalid()
            calculated.update(chunk)
        after = os.fstat(descriptor)
        if (
            bytes_read != size
            or calculated.hexdigest() != digest
            or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            )
        ):
            raise _invalid()
        return path
    except SessionOutputEvidenceError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise _invalid() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _randomized_node_ids(value):
    parsed = _strict_json(value)
    if (
        not isinstance(parsed, list)
        or any(
            not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None
            for item in parsed
        )
        or parsed != sorted(set(parsed))
    ):
        raise _invalid()
    return tuple(parsed)


def _load_manifest(connection, manifest_digest):
    row = _exactly_one(
        connection,
        """
        SELECT manifest_digest, manifest_json
        FROM manifests
        WHERE manifest_digest = ?
        """,
        (manifest_digest,),
    )
    if row["manifest_digest"] != manifest_digest:
        raise _invalid()
    encoded_manifest = row["manifest_json"]
    if (
        not isinstance(encoded_manifest, str)
        or hashlib.sha256(encoded_manifest.encode("utf-8")).hexdigest()
        != manifest_digest
    ):
        raise _invalid()
    manifest = _strict_json(encoded_manifest)
    if not isinstance(manifest, dict):
        raise _invalid()
    try:
        validated = dependency_manifest_from_record(manifest)
    except Exception:
        raise _invalid() from None
    if validated.digest != manifest_digest:
        raise _invalid()
    return manifest


def _output_rows(connection, job_id):
    rows = connection.execute(
        """
        SELECT
            job_id, artifact_id, direction, expected_size, sha256,
            offset, state, private_path, source_node_id,
            published_device, published_inode
        FROM transfers
        WHERE job_id = ? AND direction = 'download'
        ORDER BY artifact_id
        """,
        (job_id,),
    ).fetchall()
    outputs = [
        row
        for row in rows
        if not (
            isinstance(row["artifact_id"], str)
            and row["artifact_id"].startswith("preview:")
        )
    ]
    if not outputs:
        raise _invalid()
    for row in outputs:
        size = _integer(row["expected_size"], positive=True)
        if (
            row["job_id"] != job_id
            or row["direction"] != "download"
            or row["state"] != "verified"
            or _integer(row["offset"], nonnegative=True) != size
        ):
            raise _invalid()
        _identifier(row["artifact_id"])
        _identifier(row["source_node_id"])
        _digest(row["sha256"])
        _integer(row["published_device"], nonnegative=True)
        _integer(row["published_inode"], nonnegative=True)
        if not isinstance(row["private_path"], str) or not row["private_path"]:
            raise _invalid()
    return outputs


def _gold_source(connection, manifest, source_sha256, input_root):
    candidates = []
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict) or artifact.get("kind") != "input":
            continue
        source = artifact.get("source")
        artifact_id = artifact.get("artifact_id")
        if (
            artifact.get("sha256") == source_sha256
            and isinstance(source, dict)
            and source.get("kind") == "local-upload"
            and isinstance(artifact_id, str)
            and source.get("locator") == "local-upload:" + artifact_id
        ):
            candidates.append(artifact)
    if len(candidates) != 1:
        raise _invalid()
    artifact = candidates[0]
    artifact_id = _identifier(artifact.get("artifact_id"))
    artifact_size = _integer(artifact.get("size_bytes"), positive=True)
    if _digest(artifact.get("sha256")) != source_sha256:
        raise _invalid()

    local = _exactly_one(
        connection,
        """
        SELECT artifact_id, private_path, size_bytes, sha256
        FROM local_artifacts
        WHERE artifact_id = ?
        """,
        (artifact_id,),
    )
    if (
        local["artifact_id"] != artifact_id
        or _integer(local["size_bytes"], positive=True) != artifact_size
        or _digest(local["sha256"]) != source_sha256
    ):
        raise _invalid()
    return _hash_regular_file(
        local["private_path"],
        input_root,
        expected_size=artifact_size,
        expected_sha256=source_sha256,
    ), artifact_size


def _verify_session_output(
    *,
    connection,
    output_root,
    input_root,
    session_id,
    job_id,
    expected_prompt_digest,
    expected_manifest_digest,
    expected_execution_baseline_digest,
    node_id,
    kind,
    source_sha256,
):
    schema = _exactly_one(
        connection,
        "SELECT value FROM schema_meta WHERE key = 'schema_version'",
        (),
    )
    if schema["value"] != "7":
        raise _invalid()

    session = _exactly_one(
        connection,
        """
        SELECT
            session_id, manifest_digest, execution_baseline_digest,
            randomized_seed_node_ids_json
        FROM sessions
        WHERE session_id = ?
        """,
        (session_id,),
    )
    job = _exactly_one(
        connection,
        """
        SELECT
            job_id, session_id, state, prompt_digest,
            capture_json, manifest_digest
        FROM jobs
        WHERE job_id = ?
        """,
        (job_id,),
    )
    if (
        session["session_id"] != session_id
        or job["job_id"] != job_id
        or job["session_id"] != session_id
        or job["state"] != "succeeded"
        or job["prompt_digest"] != expected_prompt_digest
        or job["manifest_digest"] != expected_manifest_digest
        or session["manifest_digest"] != expected_manifest_digest
        or session["execution_baseline_digest"]
        != expected_execution_baseline_digest
    ):
        raise _invalid()

    try:
        capture = CompiledCapture.from_record(
            _CAPTURE_ID,
            job["capture_json"],
            job["prompt_digest"],
        )
        baseline_digest, randomized_node_ids = (
            certified_execution_baseline(capture)
        )
    except Exception:
        raise _invalid() from None
    if (
        capture.prompt_digest != expected_prompt_digest
        or baseline_digest != expected_execution_baseline_digest
        or randomized_node_ids
        != _randomized_node_ids(session["randomized_seed_node_ids_json"])
        or randomized_node_ids
        != (() if kind == "smoke" else ("3",))
    ):
        raise _invalid()

    manifest = _load_manifest(connection, expected_manifest_digest)
    if manifest.get("prompt_digest") != expected_prompt_digest:
        raise _invalid()
    outputs = _output_rows(connection, job_id)
    matches = [row for row in outputs if row["source_node_id"] == node_id]
    if len(matches) != 1:
        raise _invalid()
    selected = matches[0]
    output_path = _hash_regular_file(
        selected["private_path"],
        output_root,
        expected_size=selected["expected_size"],
        expected_sha256=selected["sha256"],
        expected_device=selected["published_device"],
        expected_inode=selected["published_inode"],
    )

    if kind == "smoke":
        if source_sha256 is not None:
            raise _invalid()
        try:
            result = validate_smoke_output(output_path)
        except Exception:
            raise _invalid() from None
        if (
            result.format != "PNG"
            or result.dimensions != (512, 512)
            or result.size_bytes != selected["expected_size"]
            or result.sha256 != selected["sha256"]
        ):
            raise _invalid()
        dimensions = result.dimensions
    else:
        source_path, source_size = _gold_source(
            connection,
            manifest,
            source_sha256,
            input_root,
        )
        try:
            result = validate_gold_output(
                source_path,
                output_path,
                expected_size=(3840, 2160),
                expected_format="PNG",
                require_enlargement=True,
            )
        except Exception:
            raise _invalid() from None
        if (
            result.passed is not True
            or result.source_sha256 != source_sha256
            or result.output_sha256 != selected["sha256"]
            or result.output_format != "PNG"
            or result.output_size != (3840, 2160)
        ):
            raise _invalid()
        dimensions = result.output_size
        _hash_regular_file(
            str(source_path),
            input_root,
            expected_size=source_size,
            expected_sha256=source_sha256,
        )

    _hash_regular_file(
        str(output_path),
        output_root,
        expected_size=selected["expected_size"],
        expected_sha256=selected["sha256"],
        expected_device=selected["published_device"],
        expected_inode=selected["published_inode"],
    )
    return SessionOutputEvidence(
        kind=kind,
        node_id=node_id,
        dimensions=dimensions,
        size_bytes=selected["expected_size"],
        output_sha256=selected["sha256"],
        source_match=True,
    )


def verify_session_output(
    database,
    output_root,
    input_root,
    *,
    session_id,
    job_id,
    expected_prompt_digest,
    expected_manifest_digest,
    expected_execution_baseline_digest,
    node_id,
    kind,
    source_sha256=None,
):
    """Return sanitized evidence selected only from one immutable DB snapshot."""

    try:
        session_id = _identifier(session_id)
        job_id = _identifier(job_id)
        node_id = _identifier(node_id)
        expected_prompt_digest = _digest(expected_prompt_digest)
        expected_manifest_digest = _digest(expected_manifest_digest)
        expected_execution_baseline_digest = _digest(
            expected_execution_baseline_digest
        )
        if kind not in {"smoke", "gold"}:
            raise _invalid()
        if kind == "gold":
            source_sha256 = _digest(source_sha256)
        elif source_sha256 is not None:
            raise _invalid()
        output_root = _approved_root(output_root)
        input_root = _approved_root(input_root)
        uri = _database_uri(database)
        with closing(
            sqlite3.connect(uri, uri=True, timeout=5)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            return _verify_session_output(
                connection=connection,
                output_root=output_root,
                input_root=input_root,
                session_id=session_id,
                job_id=job_id,
                expected_prompt_digest=expected_prompt_digest,
                expected_manifest_digest=expected_manifest_digest,
                expected_execution_baseline_digest=(
                    expected_execution_baseline_digest
                ),
                node_id=node_id,
                kind=kind,
                source_sha256=source_sha256,
            )
    except SessionOutputEvidenceError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise _invalid() from None


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise _invalid()


def _argument_parser():
    parser = _SafeArgumentParser(
        description="Verify one session-owned Cloud Run output.",
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--expected-prompt-digest", required=True)
    parser.add_argument("--expected-manifest-digest", required=True)
    parser.add_argument(
        "--expected-execution-baseline-digest",
        required=True,
    )
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--source-sha256")
    return parser


def main(argv=None):
    try:
        arguments = _argument_parser().parse_args(argv)
        result = verify_session_output(
            arguments.database,
            arguments.output_root,
            arguments.input_root,
            session_id=arguments.session_id,
            job_id=arguments.job_id,
            expected_prompt_digest=arguments.expected_prompt_digest,
            expected_manifest_digest=arguments.expected_manifest_digest,
            expected_execution_baseline_digest=(
                arguments.expected_execution_baseline_digest
            ),
            node_id=arguments.node_id,
            kind=arguments.kind,
            source_sha256=arguments.source_sha256,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print("FAIL reason=evidence_invalid", file=sys.stderr)
        return 1
    print(
        "PASS kind={} node_id={} dimensions={}x{} size_bytes={} "
        "output_sha256={} source_match=true".format(
            result.kind,
            result.node_id,
            result.dimensions[0],
            result.dimensions[1],
            result.size_bytes,
            result.output_sha256,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

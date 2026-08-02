"""Read-only ownership evidence for one Cloud Run session output."""

from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import types
import unittest
from unittest import mock

from PIL import Image

from cloud_run.capture import certified_execution_baseline
from cloud_run.worker_protocol import CompiledCapture, prompt_digest
from scripts.verify_session_output import (
    SessionOutputEvidenceError,
    main,
    verify_session_output,
)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _capture_payload(kind):
    if kind == "smoke":
        workflow_nodes = [
            {
                "id": 1,
                "type": "EmptyImage",
                "mode": 0,
                "properties": {"cnr_id": "comfy-core"},
                "widgets_values": [512, 512, 1, 1206179],
            },
            {
                "id": 2,
                "type": "SaveImage",
                "mode": 0,
                "properties": {"cnr_id": "comfy-core"},
                "widgets_values": ["cloud_run_core_smoke"],
            },
        ]
        output = {
            "1": {
                "class_type": "EmptyImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1,
                    "color": 1206179,
                },
            },
            "2": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["1", 0],
                    "filename_prefix": "cloud_run_core_smoke",
                },
            },
        }
    else:
        workflow_nodes = [
            {
                "id": 3,
                "type": "KSampler",
                "mode": 0,
                "properties": {"cnr_id": "comfy-core"},
                "widgets_values": [123, "randomize"],
            },
            {
                "id": 9,
                "type": "SaveImage",
                "mode": 0,
                "properties": {"cnr_id": "comfy-core"},
                "widgets_values": ["wallpaper"],
            },
        ]
        output = {
            "3": {
                "class_type": "KSampler",
                "inputs": {"seed": 123, "steps": 20},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["3", 0],
                    "filename_prefix": "wallpaper",
                },
            },
        }
    return {
        "workflow": {
            "nodes": workflow_nodes,
            "extra": {"frontendVersion": "1.47.10"},
        },
        "output": output,
        "queue_options": {},
    }


class SessionOutputEvidenceTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output_root = self.root / "private-output-root"
        self.input_root = self.root / "private-input-root"
        self.output_root.mkdir(mode=0o700)
        self.input_root.mkdir(mode=0o700)
        self.database = self.root / "private-attempts.sqlite3"
        self.session_id = "session-1"
        self.job_id = "job-1"
        self.node_id = "2"
        self.source_path = self.input_root / "private-source-name.png"
        Image.new("RGB", (64, 96), (18, 103, 163)).save(
            self.source_path,
            format="PNG",
        )
        self.source_sha256 = _sha256(self.source_path)
        self.output_path = self.output_root / "private-output-name.png"
        Image.new("RGB", (512, 512), (18, 103, 163)).save(
            self.output_path,
            format="PNG",
        )
        self._build_database("smoke")

    def _manifest(self, kind, *, artifacts=None):
        if artifacts is None:
            artifacts = []
            if kind == "gold":
                artifacts = [
                    {
                        "artifact_id": "private-input-artifact",
                        "kind": "input",
                        "logical_name": "private-source-name.png",
                        "destination": "input/private-source-name.png",
                        "size_bytes": self.source_path.stat().st_size,
                        "sha256": self.source_sha256,
                        "source": {
                            "kind": "local-upload",
                            "locator": (
                                "local-upload:private-input-artifact"
                            ),
                            "immutable_revision": None,
                        },
                    }
                ]
        return {
            "schema_version": 1,
            "protocol_version": "1",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "worker_version": "worker-v1",
            "prompt_digest": self.prompt_digest,
            "custom_nodes": [],
            "artifacts": artifacts,
            "output_allowance_bytes": 1024 * 1024 * 1024,
            "disk_gb": 80,
        }

    def _build_database(self, kind, *, bootstrap_upload=False):
        if self.database.exists():
            self.database.unlink()
        self.kind = kind
        self.node_id = "2" if kind == "smoke" else "9"
        payload = _capture_payload(kind)
        capture = CompiledCapture.from_payload(
            payload,
            capture_id="00000000-0000-0000-0000-000000000001",
        )
        self.capture_json = capture.canonical_payload()
        self.prompt_digest = capture.prompt_digest
        (
            self.execution_baseline_digest,
            randomized_node_ids,
        ) = certified_execution_baseline(capture)
        self.randomized_node_ids = randomized_node_ids
        manifest = self._manifest(kind)
        self.manifest_json = _canonical_json(manifest)
        self.manifest_digest = hashlib.sha256(
            self.manifest_json.encode("utf-8")
        ).hexdigest()
        self.output_sha256 = _sha256(self.output_path)
        output_metadata = self.output_path.stat()

        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                """
                CREATE TABLE schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE sessions (
                    session_id TEXT,
                    manifest_digest TEXT,
                    execution_baseline_digest TEXT,
                    randomized_seed_node_ids_json TEXT
                );
                CREATE TABLE jobs (
                    job_id TEXT,
                    session_id TEXT,
                    state TEXT,
                    prompt_digest TEXT,
                    capture_json TEXT,
                    manifest_digest TEXT
                );
                CREATE TABLE manifests (
                    manifest_digest TEXT,
                    manifest_json TEXT
                );
                CREATE TABLE transfers (
                    job_id TEXT,
                    artifact_id TEXT,
                    direction TEXT,
                    expected_size INTEGER,
                    sha256 TEXT,
                    offset INTEGER,
                    state TEXT,
                    private_path TEXT,
                    source_node_id TEXT,
                    published_device INTEGER,
                    published_inode INTEGER
                );
                CREATE TABLE local_artifacts (
                    artifact_id TEXT,
                    private_path TEXT,
                    size_bytes INTEGER,
                    sha256 TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO schema_meta VALUES('schema_version', '7')"
            )
            connection.execute(
                "INSERT INTO sessions VALUES(?, ?, ?, ?)",
                (
                    self.session_id,
                    self.manifest_digest,
                    self.execution_baseline_digest,
                    _canonical_json(list(randomized_node_ids)),
                ),
            )
            connection.execute(
                "INSERT INTO jobs VALUES(?, ?, 'succeeded', ?, ?, ?)",
                (
                    self.job_id,
                    self.session_id,
                    self.prompt_digest,
                    self.capture_json,
                    self.manifest_digest,
                ),
            )
            connection.execute(
                "INSERT INTO manifests VALUES(?, ?)",
                (self.manifest_digest, self.manifest_json),
            )
            connection.execute(
                "INSERT INTO transfers VALUES(?, ?, 'download', ?, ?, ?, "
                "'verified', ?, ?, ?, ?)",
                (
                    self.job_id,
                    "owned-output",
                    output_metadata.st_size,
                    self.output_sha256,
                    output_metadata.st_size,
                    str(self.output_path),
                    self.node_id,
                    output_metadata.st_dev,
                    output_metadata.st_ino,
                ),
            )
            if kind == "gold":
                connection.execute(
                    "INSERT INTO local_artifacts VALUES(?, ?, ?, ?)",
                    (
                        "private-input-artifact",
                        str(self.source_path),
                        self.source_path.stat().st_size,
                        self.source_sha256,
                    ),
                )
                if bootstrap_upload:
                    connection.execute(
                        "INSERT INTO transfers VALUES(?, ?, 'upload', ?, ?, ?, "
                        "'verified', ?, NULL, NULL, NULL)",
                        (
                            "bootstrap:" + self.session_id,
                            "private-input-artifact",
                            self.source_path.stat().st_size,
                            self.source_sha256,
                            self.source_path.stat().st_size,
                            str(self.source_path),
                        ),
                    )
            connection.commit()
        os.chmod(self.database, 0o640)

    def _arguments(self, **changes):
        values = {
            "database": self.database,
            "output_root": self.output_root,
            "input_root": self.input_root,
            "session_id": self.session_id,
            "job_id": self.job_id,
            "expected_prompt_digest": self.prompt_digest,
            "expected_manifest_digest": self.manifest_digest,
            "expected_execution_baseline_digest": (
                self.execution_baseline_digest
            ),
            "node_id": self.node_id,
            "kind": self.kind,
            "source_sha256": (
                self.source_sha256 if self.kind == "gold" else None
            ),
        }
        values.update(changes)
        return values

    def _verify(self, **changes):
        return verify_session_output(**self._arguments(**changes))

    def _gold_result(self, *, passed=True):
        return types.SimpleNamespace(
            passed=passed,
            reasons=() if passed else ("composition_mismatch",),
            source_format="PNG",
            source_size=(64, 96),
            source_sha256=self.source_sha256,
            output_format="PNG",
            output_size=(3840, 2160),
            output_sha256=self.output_sha256,
            perceptual_distance=0,
        )

    def _replace_manifest(self, manifest):
        encoded = _canonical_json(manifest)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("DELETE FROM manifests")
            connection.execute(
                "INSERT INTO manifests VALUES(?, ?)",
                (digest, encoded),
            )
            connection.execute(
                "UPDATE sessions SET manifest_digest = ?",
                (digest,),
            )
            connection.execute(
                "UPDATE jobs SET manifest_digest = ?",
                (digest,),
            )
            connection.commit()
        self.manifest_json = encoded
        self.manifest_digest = digest

    def _insert_output(
        self,
        artifact_id,
        node_id,
        *,
        state="verified",
        provenance=True,
    ):
        path = self.output_root / (artifact_id + ".png")
        shutil.copyfile(self.output_path, path)
        metadata = path.stat()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO transfers VALUES(?, ?, 'download', ?, ?, ?, ?, "
                "?, ?, ?, ?)",
                (
                    self.job_id,
                    artifact_id,
                    metadata.st_size,
                    _sha256(path),
                    metadata.st_size if state == "verified" else 0,
                    state,
                    str(path),
                    node_id if provenance else None,
                    metadata.st_dev if provenance else None,
                    metadata.st_ino if provenance else None,
                ),
            )
            connection.commit()
        return path

    def test_smoke_requires_owned_evidence_then_calls_smoke_validator(self):
        result = self._verify()

        self.assertEqual(result.kind, "smoke")
        self.assertEqual(result.node_id, "2")
        self.assertEqual(result.dimensions, (512, 512))
        self.assertEqual(result.size_bytes, self.output_path.stat().st_size)
        self.assertEqual(result.output_sha256, self.output_sha256)
        self.assertTrue(result.source_match)

    def test_gold_resolves_manifest_local_record_without_job_upload(self):
        self._build_database("gold", bootstrap_upload=False)
        with mock.patch(
            "scripts.verify_session_output.validate_gold_output",
            return_value=self._gold_result(),
        ) as validator:
            result = self._verify()

        self.assertEqual(result.kind, "gold")
        self.assertEqual(result.node_id, "9")
        self.assertTrue(result.source_match)
        validator.assert_called_once_with(
            self.source_path.resolve(),
            self.output_path.resolve(),
            expected_size=(3840, 2160),
            expected_format="PNG",
            require_enlargement=True,
        )

    def test_gold_accepts_bootstrap_owned_input_transfer_without_requiring_it(self):
        self._build_database("gold", bootstrap_upload=True)
        with mock.patch(
            "scripts.verify_session_output.validate_gold_output",
            return_value=self._gold_result(),
        ):
            result = self._verify()

        self.assertTrue(result.source_match)

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing-private.sqlite3"

        with self.assertRaises(SessionOutputEvidenceError):
            self._verify(database=missing)

        self.assertFalse(missing.exists())

    def test_existing_database_bytes_mode_schema_and_mtime_do_not_change(self):
        before_bytes = self.database.read_bytes()
        before = self.database.stat()
        with closing(sqlite3.connect(self.database)) as connection:
            before_schema = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()

        self._verify()

        after = self.database.stat()
        with closing(sqlite3.connect(self.database)) as connection:
            after_schema = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
        self.assertEqual(self.database.read_bytes(), before_bytes)
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after_schema, before_schema)

    def test_rejects_wrong_session_job_and_job_ownership(self):
        for changes in (
            {"session_id": "session-other"},
            {"job_id": "job-other"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(SessionOutputEvidenceError):
                    self._verify(**changes)

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE jobs SET session_id = 'session-other'"
            )
            connection.commit()
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_wrong_prompt_manifest_and_execution_baseline_digests(self):
        for field in (
            "expected_prompt_digest",
            "expected_manifest_digest",
            "expected_execution_baseline_digest",
        ):
            with self.subTest(field=field):
                with self.assertRaises(SessionOutputEvidenceError):
                    self._verify(**{field: "f" * 64})

    def test_rejects_wrong_or_ambiguous_output_node(self):
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify(node_id="9")

        self._insert_output("second-owned-output", self.node_id)
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_unverified_additional_output_but_ignores_preview(self):
        self._insert_output("other-output", "10", state="pending")
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "DELETE FROM transfers WHERE artifact_id = 'other-output'"
            )
            connection.execute(
                "INSERT INTO transfers VALUES(?, ?, 'download', 1, ?, 0, "
                "'pending', ?, NULL, NULL, NULL)",
                (
                    self.job_id,
                    "preview:one",
                    "b" * 64,
                    str(self.output_root / "unfinished-preview.part"),
                ),
            )
            connection.commit()
        self.assertEqual(self._verify().node_id, self.node_id)

    def test_rejects_legacy_output_without_node_or_published_identity(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE transfers SET source_node_id = NULL, "
                "published_device = NULL, published_inode = NULL"
            )
            connection.commit()

        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_missing_or_ambiguous_gold_manifest_input(self):
        self._build_database("gold")
        self._replace_manifest(self._manifest("gold", artifacts=[]))
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        first = self._manifest("gold")["artifacts"][0]
        second = json.loads(_canonical_json(first))
        second["artifact_id"] = "private-input-artifact-two"
        second["source"]["locator"] = (
            "local-upload:private-input-artifact-two"
        )
        self._replace_manifest(
            self._manifest("gold", artifacts=[first, second])
        )
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_wrong_local_upload_identity_and_source_hash(self):
        self._build_database("gold")
        manifest = self._manifest("gold")
        manifest["artifacts"][0]["source"]["locator"] = (
            "local-upload:another-artifact"
        )
        self._replace_manifest(manifest)
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        self._build_database("gold")
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify(source_sha256="f" * 64)

    def test_rejects_output_symlink_escape_and_same_byte_inode_replacement(self):
        link = self.output_root / "output-link.png"
        link.symlink_to(self.output_path)
        link_metadata = os.lstat(link)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE transfers SET private_path = ?, published_device = ?, "
                "published_inode = ? WHERE artifact_id = 'owned-output'",
                (str(link), link_metadata.st_dev, link_metadata.st_ino),
            )
            connection.commit()
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        self._build_database("smoke")
        replacement = self.output_root / "replacement.png"
        shutil.copyfile(self.output_path, replacement)
        os.replace(replacement, self.output_path)
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_output_content_change_and_path_escape(self):
        Image.new("RGB", (512, 512), (1, 2, 3)).save(
            self.output_path,
            format="PNG",
        )
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        self._build_database("smoke")
        escaped = self.root / "outside-output.png"
        shutil.copyfile(self.output_path, escaped)
        metadata = escaped.stat()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE transfers SET private_path = ?, published_device = ?, "
                "published_inode = ?",
                (str(escaped), metadata.st_dev, metadata.st_ino),
            )
            connection.commit()
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_gold_input_symlink_and_path_escape(self):
        self._build_database("gold")
        source_link = self.input_root / "source-link.png"
        source_link.symlink_to(self.source_path)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE local_artifacts SET private_path = ?",
                (str(source_link),),
            )
            connection.commit()
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

        self._build_database("gold")
        escaped = self.root / "outside-source.png"
        shutil.copyfile(self.source_path, escaped)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE local_artifacts SET private_path = ?",
                (str(escaped),),
            )
            connection.commit()
        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_canvas_changed_after_reviewed_baseline(self):
        payload = json.loads(self.capture_json)
        payload["output"]["2"]["inputs"]["filename_prefix"] = "changed"
        changed_json = _canonical_json(payload)
        changed_prompt = prompt_digest(
            payload["output"],
            payload["queue_options"],
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE jobs SET capture_json = ?, prompt_digest = ?",
                (changed_json, changed_prompt),
            )
            connection.commit()

        with self.assertRaises(SessionOutputEvidenceError):
            self._verify(expected_prompt_digest=changed_prompt)

    def test_rejects_manifest_prompt_not_owned_by_the_job(self):
        manifest = self._manifest("smoke")
        manifest["prompt_digest"] = "f" * 64
        self._replace_manifest(manifest)

        with self.assertRaises(SessionOutputEvidenceError):
            self._verify()

    def test_rejects_gold_randomized_seed_policy_on_any_node_but_three(self):
        self._build_database("gold")
        payload = json.loads(self.capture_json)
        payload["workflow"]["nodes"][0]["id"] = 4
        payload["output"]["4"] = payload["output"].pop("3")
        payload["output"]["9"]["inputs"]["images"][0] = "4"
        capture = CompiledCapture.from_payload(
            payload,
            capture_id="00000000-0000-0000-0000-000000000002",
        )
        baseline, randomized_nodes = certified_execution_baseline(capture)
        self.capture_json = capture.canonical_payload()
        self.prompt_digest = capture.prompt_digest
        self.execution_baseline_digest = baseline
        self.randomized_node_ids = randomized_nodes
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE jobs SET capture_json = ?, prompt_digest = ?",
                (self.capture_json, self.prompt_digest),
            )
            connection.execute(
                "UPDATE sessions SET execution_baseline_digest = ?, "
                "randomized_seed_node_ids_json = ?",
                (baseline, _canonical_json(list(randomized_nodes))),
            )
            connection.commit()
        self._replace_manifest(self._manifest("gold"))

        with mock.patch(
            "scripts.verify_session_output.validate_gold_output",
            return_value=self._gold_result(),
        ):
            with self.assertRaises(SessionOutputEvidenceError):
                self._verify()

    def test_gold_validator_failure_or_hash_mismatch_is_not_evidence(self):
        self._build_database("gold")
        for result in (
            self._gold_result(passed=False),
            types.SimpleNamespace(
                **{
                    **vars(self._gold_result()),
                    "output_sha256": "f" * 64,
                }
            ),
            types.SimpleNamespace(
                **{
                    **vars(self._gold_result()),
                    "source_sha256": "f" * 64,
                }
            ),
        ):
            with self.subTest(result=result):
                with mock.patch(
                    "scripts.verify_session_output.validate_gold_output",
                    return_value=result,
                ):
                    with self.assertRaises(SessionOutputEvidenceError):
                        self._verify()

    def test_cli_prints_only_sanitized_success_and_failure(self):
        self._build_database("gold")
        argv = [
            "--database",
            str(self.database),
            "--output-root",
            str(self.output_root),
            "--input-root",
            str(self.input_root),
            "--session-id",
            self.session_id,
            "--job-id",
            self.job_id,
            "--expected-prompt-digest",
            self.prompt_digest,
            "--expected-manifest-digest",
            self.manifest_digest,
            "--expected-execution-baseline-digest",
            self.execution_baseline_digest,
            "--node-id",
            self.node_id,
            "--kind",
            "gold",
            "--source-sha256",
            self.source_sha256,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch(
            "scripts.verify_session_output.validate_gold_output",
            return_value=self._gold_result(),
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)

        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        rendered = stdout.getvalue()
        self.assertTrue(rendered.startswith("PASS kind=gold node_id=9 "))
        self.assertIn("source_match=true", rendered)
        self.assertIn(self.output_sha256, rendered)
        for private_marker in (
            str(self.root),
            self.source_sha256,
            "private-source-name",
            "private-input-artifact",
        ):
            self.assertNotIn(private_marker, rendered)

        stdout = io.StringIO()
        stderr = io.StringIO()
        argv[argv.index(self.prompt_digest)] = "f" * 64
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(argv)
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "FAIL reason=evidence_invalid\n")
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(str(self.root), combined)
        self.assertNotIn(self.source_sha256, combined)


if __name__ == "__main__":
    unittest.main()

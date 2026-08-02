"""Contracts for the public zero-model Cloud Run smoke workflow."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from PIL import Image

from cloud_run.capture import CompiledCapture, certified_execution_baseline
from cloud_run.job_repository import JobRepository
from cloud_run.resolver import NodeResolution
from cloud_run.session_service import SessionService
from cloud_run.worker_release import WorkerRelease
from scripts.validate_smoke_output import (
    SmokeValidationError,
    validate_smoke_output,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "cloud-run-core-output-smoke.json"
)
EXPECTED_CANONICAL_SHA256 = (
    "e72b293f4d08e007623ea647b58c1733913e04261fb04eb9a96f7566895e2f30"
)
EXPECTED_RAW_SHA256 = (
    "c9124f764fe4335d1c150e78f108284dd9b560f268f54caf92b132ab045eaaba"
)
EXPECTED_RGB = (18, 103, 163)
EXPECTED_PROMPT_SHA256 = (
    "6609d42a6887c8838dfb0c9a4346e5cdf8aaf8e1755b2d2bdadf43800424da04"
)


def _write_png(path: Path, *, size=(512, 512), rgb=EXPECTED_RGB) -> Path:
    Image.new("RGB", size, rgb).save(path, format="PNG")
    return path


def _compiled_smoke_payload(workflow):
    captured_workflow = copy.deepcopy(workflow)
    captured_workflow["extra"]["frontendVersion"] = "1.47.10"
    return {
        "workflow": captured_workflow,
        "output": {
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
        },
        "queue_options": {},
    }


def _reviewed_worker_release():
    return WorkerRelease.from_payload({
        "schema_version": 1,
        "template_hash_id": "9d6822f9429822ee9e7339a804a549da",
        "worker_commit": "76f2fff05f05b2fc7372b8cdf507d84dc794abe8",
        "worker_archive_sha256": (
            "844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5"
        ),
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.12",
        "worker_port": 8765,
    })


class _CoreOnlyResolver:
    def __init__(self):
        self.allowances = []

    async def resolve_preflight(
        self,
        capture,
        *,
        explicit_output_allowance_bytes,
    ):
        self.allowances.append(explicit_output_allowance_bytes)
        return types.SimpleNamespace(
            node_rows=(
                NodeResolution("EmptyImage", "resolved", "core"),
                NodeResolution("SaveImage", "resolved", "core"),
            ),
            artifact_rows=(),
            custom_nodes=(),
            artifacts=(),
            output_allowance_bytes=explicit_output_allowance_bytes,
            disk_gb=80,
            rentable=True,
        )


class SmokeWorkflowFixtureTests(unittest.TestCase):
    def test_fixture_is_the_exact_zero_model_core_workflow(self):
        raw = FIXTURE_PATH.read_bytes()
        workflow = json.loads(raw)
        canonical = json.dumps(
            workflow,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")

        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            EXPECTED_CANONICAL_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            EXPECTED_RAW_SHA256,
        )
        self.assertEqual(workflow["last_node_id"], 2)
        self.assertEqual(workflow["last_link_id"], 1)
        self.assertEqual(workflow["links"], [[1, 1, 0, 2, 0, "IMAGE"]])
        self.assertEqual(
            [(node["id"], node["type"]) for node in workflow["nodes"]],
            [(1, "EmptyImage"), (2, "SaveImage")],
        )
        self.assertEqual(workflow["nodes"][0]["widgets_values"], [
            512,
            512,
            1,
            1206179,
        ])
        self.assertEqual(
            workflow["nodes"][1]["widgets_values"],
            ["cloud_run_core_smoke"],
        )
        self.assertNotIn("model", json.dumps(workflow).casefold())
        self.assertEqual(workflow["nodes"][0]["properties"], {
            "Node name for S&R": "EmptyImage",
        })
        self.assertEqual(workflow["nodes"][1]["properties"], {
            "Node name for S&R": "SaveImage",
        })

    def test_fixture_compiles_and_passes_the_free_core_only_preflight(self):
        workflow = json.loads(FIXTURE_PATH.read_bytes())
        capture = CompiledCapture.from_payload(
            _compiled_smoke_payload(workflow)
        )
        resolver = _CoreOnlyResolver()
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = JobRepository(
                Path(temporary_directory) / "private" / "attempts.sqlite3"
            )
            repository.save_capture(capture, created_at=1.0)
            service = SessionService(
                job_repository=repository,
                resolver=resolver,
                release=_reviewed_worker_release(),
                clock=lambda: 2.0,
                id_factory=lambda: "smoke-preflight",
            )

            preflight = asyncio.run(
                service.preflight(
                    capture.capture_id,
                    explicit_output_allowance_bytes=16 * 1024 * 1024,
                )
            )
            manifest = json.loads(
                repository.get_manifest(preflight.manifest_digest)
            )

        self.assertEqual(capture.prompt_digest, EXPECTED_PROMPT_SHA256)
        self.assertEqual(
            (
                preflight.execution_baseline_digest,
                preflight.randomized_seed_node_ids,
            ),
            certified_execution_baseline(capture),
        )
        self.assertEqual(preflight.randomized_seed_node_ids, ())
        self.assertEqual(
            capture.executable_class_types,
            ("EmptyImage", "SaveImage"),
        )
        self.assertEqual(resolver.allowances, [16 * 1024 * 1024])
        self.assertTrue(preflight.rentable)
        self.assertEqual(preflight.transfer_bytes, 0)
        self.assertEqual(preflight.output_allowance_bytes, 16 * 1024 * 1024)
        self.assertEqual(preflight.disk_gb, 80)
        self.assertEqual(
            [
                (row.display_name, row.status, row.source_kind)
                for row in preflight.rows
            ],
            [
                ("EmptyImage", "resolved", "core"),
                ("SaveImage", "resolved", "core"),
            ],
        )
        self.assertEqual(manifest["artifacts"], [])
        self.assertEqual(manifest["custom_nodes"], [])


class SmokeOutputValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_accepts_exact_regular_png_and_returns_sanitized_evidence(self):
        output = _write_png(self.root / "private-name.png")

        result = validate_smoke_output(output)

        self.assertEqual(result.format, "PNG")
        self.assertEqual(result.dimensions, (512, 512))
        self.assertEqual(result.size_bytes, output.stat().st_size)
        self.assertEqual(
            result.sha256,
            hashlib.sha256(output.read_bytes()).hexdigest(),
        )
        self.assertNotIn("path", result.__dataclass_fields__)

    def test_rejects_wrong_format_dimensions_pixel_truncation_and_empty_file(self):
        jpeg = self.root / "wrong-format.jpg"
        Image.new("RGB", (512, 512), EXPECTED_RGB).save(jpeg, format="JPEG")
        wrong_dimensions = _write_png(
            self.root / "wrong-dimensions.png",
            size=(511, 512),
        )
        wrong_pixel = _write_png(self.root / "wrong-pixel.png")
        with Image.open(wrong_pixel) as image:
            image.load()
            altered = image.copy()
        altered.putpixel((511, 511), (18, 103, 162))
        altered.save(wrong_pixel, format="PNG")
        valid = _write_png(self.root / "source.png")
        truncated = self.root / "truncated.png"
        content = valid.read_bytes()
        truncated.write_bytes(content[: len(content) // 2])
        empty = self.root / "empty.png"
        empty.touch()

        for candidate in (
            jpeg,
            wrong_dimensions,
            wrong_pixel,
            truncated,
            empty,
        ):
            with self.subTest(candidate=candidate.name):
                with self.assertRaises(SmokeValidationError):
                    validate_smoke_output(candidate)

    def test_rejects_symlink(self):
        target = _write_png(self.root / "target.png")
        link = self.root / "link.png"
        link.symlink_to(target)

        with self.assertRaisesRegex(
            SmokeValidationError,
            "image_file_invalid",
        ):
            validate_smoke_output(link)

    def test_rejects_file_identity_change_during_validation(self):
        output = _write_png(self.root / "changing.png")
        identity = (
            output.stat().st_dev,
            output.stat().st_ino,
            output.stat().st_size,
            output.stat().st_mtime_ns,
        )
        changed = identity[:-1] + (identity[-1] + 1,)

        with mock.patch(
            "scripts.validate_smoke_output._descriptor_identity",
            side_effect=(identity, changed),
        ):
            with self.assertRaisesRegex(
                SmokeValidationError,
                "image_file_changed",
            ):
                validate_smoke_output(output)

    def test_error_never_contains_private_path(self):
        marker = "PRIVATE_SMOKE_MARKER"
        output = self.root / (marker + ".png")
        output.touch()

        with self.assertRaises(SmokeValidationError) as caught:
            validate_smoke_output(output)

        self.assertNotIn(marker, str(caught.exception))

    def test_cli_prints_only_sanitized_pass_or_failure(self):
        marker = "PRIVATE_CLI_MARKER"
        valid = _write_png(self.root / (marker + "-valid.png"))
        invalid = self.root / (marker + "-invalid.png")
        invalid.touch()
        script = REPOSITORY_ROOT / "scripts" / "validate_smoke_output.py"

        passed = subprocess.run(
            [sys.executable, str(script), str(valid)],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        failed = subprocess.run(
            [sys.executable, str(script), str(invalid)],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertTrue(
            passed.stdout.startswith("PASS format=PNG dimensions=512x512 ")
        )
        self.assertEqual(passed.stderr, "")
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(failed.stderr, "FAIL reason=image_file_invalid\n")
        self.assertNotIn(marker, passed.stdout + passed.stderr)
        self.assertNotIn(marker, failed.stdout + failed.stderr)


class ComfyPythonRunnerTests(unittest.TestCase):
    def test_explicit_interpreter_runs_without_echoing_arguments(self):
        runner = REPOSITORY_ROOT / "scripts" / "run_with_comfyui_python.sh"
        environment = dict(os.environ)
        environment["COMFYUI_PYTHON_COMMAND"] = sys.executable
        environment["PYTHON_COMMAND"] = "/definitely/not/the/selected/python"

        result = subprocess.run(
            [str(runner), "-c", "print('runner-ok')"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "runner-ok\n")
        self.assertEqual(result.stderr, "")

    def test_missing_explicit_interpreter_does_not_echo_private_argument(self):
        marker = "PRIVATE_RUNNER_ARGUMENT"
        runner = REPOSITORY_ROOT / "scripts" / "run_with_comfyui_python.sh"
        environment = dict(os.environ)
        environment["COMFYUI_PYTHON_COMMAND"] = "/definitely/missing-python"

        result = subprocess.run(
            [str(runner), "-c", marker],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(marker, result.stderr)


if __name__ == "__main__":
    unittest.main()

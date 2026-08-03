import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from cloud_run import capture as capture_contract
from cloud_run.capture import (
    MAX_CAPTURE_BYTES,
    CaptureValidationError,
    CompiledCapture,
)
from cloud_run.job_repository import JobRepository
from cloud_run.service import CloudRunService


def certified_execution_baseline(capture):
    if not hasattr(capture_contract, "certified_execution_baseline"):
        raise AssertionError("certified_execution_baseline is required")
    return capture_contract.certified_execution_baseline(capture)


def certified_bootstrap_workflow(capture):
    if not hasattr(capture_contract, "certified_bootstrap_workflow"):
        raise AssertionError("certified_bootstrap_workflow is required")
    return capture_contract.certified_bootstrap_workflow(capture)


def native_capture(
    *,
    frontend_version="1.47.10",
    workflow=None,
    output=None,
    queue_options=None,
):
    return {
        "workflow": workflow
        or {
            "version": 1,
            "nodes": [
                {
                    "id": 1,
                    "type": "KSampler",
                    "mode": 0,
                    "properties": {"cnr_id": "comfy-core"},
                    "widgets_values": [7, "fixed"],
                }
            ],
            "extra": {"frontendVersion": frontend_version},
        },
        "output": output
        or {
            "1": {
                "class_type": "KSampler",
                "inputs": {"seed": 7},
            }
        },
        "queue_options": queue_options
        if queue_options is not None
        else {"preview_method": "latent2rgb"},
    }


class CompiledCaptureTests(unittest.TestCase):
    def test_native_prompt_uses_the_shared_capture_boundary(self):
        payload = native_capture(queue_options={})
        body = {
            "client_id": "desktop-client-1",
            "prompt": payload["output"],
            "extra_data": {
                "comfy_usage_source": "desktop",
                "extra_pnginfo": {"workflow": payload["workflow"]},
                "preview_method": "latent2rgb",
            },
            "front": False,
            "number": 7,
            "partial_execution_targets": ["1"],
        }

        capture = CompiledCapture.from_native_prompt(
            json.dumps(body, separators=(",", ":")).encode("utf-8")
        )

        self.assertEqual(capture.workflow, payload["workflow"])
        self.assertEqual(capture.output, payload["output"])
        self.assertEqual(
            capture.queue_options,
            {
                "front": False,
                "number": 7,
                "partial_execution_targets": ["1"],
                "preview_method": "latent2rgb",
            },
        )

    def test_native_prompt_rejects_duplicate_fields_credentials_and_reconstruction(self):
        payload = native_capture(queue_options={})
        valid = {
            "client_id": "desktop-client-1",
            "prompt": payload["output"],
            "extra_data": {
                "extra_pnginfo": {"workflow": payload["workflow"]},
            },
        }
        rejected = (
            b'{"client_id":"a","client_id":"b","prompt":{},"extra_data":{}}',
            json.dumps({**valid, "provider_token": "private"}).encode("utf-8"),
            json.dumps(
                {
                    **valid,
                    "extra_data": {
                        **valid["extra_data"],
                        "api_key": "private",
                    },
                }
            ).encode("utf-8"),
            json.dumps({**valid, "prompt": {}}).encode("utf-8"),
        )
        for body in rejected:
            with self.subTest(body=body[:80]):
                with self.assertRaises(CaptureValidationError):
                    CompiledCapture.from_native_prompt(body)

    def test_capture_accepts_native_shape_and_hashes_only_execution_material(self):
        first = CompiledCapture.from_payload(native_capture())
        second = CompiledCapture.from_payload(
            native_capture(
                workflow={
                    "version": 1,
                    "nodes": [{"id": 1, "pos": [99, 99]}],
                    "extra": {"frontendVersion": "1.47.10"},
                },
                output={
                    "1": {
                        "inputs": {"seed": 7},
                        "class_type": "KSampler",
                    }
                },
            )
        )

        self.assertEqual(first.prompt_digest, second.prompt_digest)
        self.assertEqual(first.executable_class_types, ("KSampler",))
        self.assertNotEqual(first.capture_id, second.capture_id)

    def test_capture_rejects_wrong_frontend_secrets_and_invalid_nodes(self):
        payloads = (
            native_capture(frontend_version="1.47.11"),
            native_capture(
                workflow={
                    "version": 1,
                    "nodes": [],
                    "extra": {
                        "frontendVersion": "1.47.10",
                        "auth_token_comfy_org": "forbidden",
                    },
                }
            ),
            native_capture(
                output={
                    "1": {
                        "class_type": "../bad",
                        "inputs": {},
                    }
                }
            ),
            native_capture(
                output={
                    "": {
                        "class_type": "KSampler",
                        "inputs": {},
                    }
                }
            ),
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(CaptureValidationError):
                    CompiledCapture.from_payload(payload)

    def test_capture_rejects_unknown_queue_options_and_oversized_payload(self):
        with self.assertRaises(CaptureValidationError):
            CompiledCapture.from_payload(
                native_capture(queue_options={"provider_token": "forbidden"})
            )
        oversized = native_capture()
        oversized["workflow"]["padding"] = "x" * MAX_CAPTURE_BYTES
        with self.assertRaises(CaptureValidationError):
            CompiledCapture.from_payload(oversized)

    def test_capture_is_detached_and_persists_across_repository_reopen(self):
        payload = native_capture()
        capture = CompiledCapture.from_payload(payload)
        payload["output"]["1"]["inputs"]["seed"] = 99

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "private" / "sessions.sqlite3"
            repository = JobRepository(path)
            repository.save_capture(capture, created_at=10.0)
            reopened = JobRepository(path).get_capture(capture.capture_id)

        self.assertEqual(capture.output["1"]["inputs"]["seed"], 7)
        self.assertEqual(reopened, capture)

    def test_execution_baseline_normalizes_only_certified_randomized_ksampler_seed(self):
        first_payload = native_capture()
        first_payload["workflow"]["nodes"][0]["widgets_values"] = [
            7,
            "randomize",
        ]
        first = CompiledCapture.from_payload(first_payload)
        second_payload = copy.deepcopy(first_payload)
        second_payload["output"]["1"]["inputs"]["seed"] = 999
        second = CompiledCapture.from_payload(second_payload)

        first_baseline = certified_execution_baseline(first)
        second_baseline = certified_execution_baseline(second)

        self.assertEqual(first_baseline, second_baseline)
        self.assertEqual(first_baseline[1], ("1",))

        fixed_payload = copy.deepcopy(first_payload)
        fixed_payload["workflow"]["nodes"][0]["widgets_values"][1] = "fixed"
        fixed_first = CompiledCapture.from_payload(fixed_payload)
        fixed_payload["output"]["1"]["inputs"]["seed"] = 999
        fixed_second = CompiledCapture.from_payload(fixed_payload)
        self.assertNotEqual(
            certified_execution_baseline(fixed_first)[0],
            certified_execution_baseline(fixed_second)[0],
        )
        self.assertEqual(
            certified_execution_baseline(fixed_first)[1],
            (),
        )

    def test_profile_bootstrap_normalizes_certified_random_seed_without_mutation(self):
        first_payload = native_capture()
        first_payload["workflow"]["nodes"][0]["widgets_values"] = [
            7,
            "randomize",
        ]
        second_payload = copy.deepcopy(first_payload)
        second_payload["output"]["1"]["inputs"]["seed"] = 999
        second_payload["workflow"]["nodes"][0]["widgets_values"][0] = 999
        first = CompiledCapture.from_payload(first_payload)
        second = CompiledCapture.from_payload(second_payload)

        first_bootstrap = certified_bootstrap_workflow(first)
        second_bootstrap = certified_bootstrap_workflow(second)

        self.assertEqual(first_bootstrap, second_bootstrap)
        self.assertEqual(
            first_bootstrap["nodes"][0]["widgets_values"][0],
            0,
        )
        self.assertEqual(
            first.workflow["nodes"][0]["widgets_values"][0],
            7,
        )
        self.assertEqual(
            second.workflow["nodes"][0]["widgets_values"][0],
            999,
        )

        from cloud_run.desktop_profile import DesktopProfileStore

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "user" / "default").mkdir(parents=True)
            (root / "input").mkdir()
            store = DesktopProfileStore(
                repository=JobRepository(root / "attempts.sqlite3"),
                private_root=root / "profiles",
            )
            first_profile = store.capture(
                user_root=root / "user",
                profile_name="default",
                input_root=root / "input",
                bootstrap_workflow=first_bootstrap,
            )
            second_profile = store.capture(
                user_root=root / "user",
                profile_name="default",
                input_root=root / "input",
                bootstrap_workflow=second_bootstrap,
            )

        self.assertEqual(first_profile.revision, 1)
        self.assertEqual(
            first_profile.archive_sha256,
            second_profile.archive_sha256,
        )
        self.assertEqual(
            first_profile.bootstrap_digest,
            second_profile.bootstrap_digest,
        )

    def test_profile_bootstrap_keeps_uncertified_seed_values_significant(self):
        cases = (
            {"widgets_values": [7, "fixed"]},
            {"properties": {"cnr_id": "third-party"}},
            {"mode": 4},
            {"type": "KSamplerAdvanced"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                first_payload = native_capture()
                first_payload["workflow"]["nodes"][0]["widgets_values"] = [
                    7,
                    "randomize",
                ]
                first_payload["workflow"]["nodes"][0].update(changes)
                second_payload = copy.deepcopy(first_payload)
                second_payload["output"]["1"]["inputs"]["seed"] = 999
                second_payload["workflow"]["nodes"][0][
                    "widgets_values"
                ][0] = 999

                first = certified_bootstrap_workflow(
                    CompiledCapture.from_payload(first_payload)
                )
                second = certified_bootstrap_workflow(
                    CompiledCapture.from_payload(second_payload)
                )

                self.assertNotEqual(first, second)

    def test_execution_baseline_changes_for_every_other_prompt_or_queue_edit(self):
        payload = native_capture()
        payload["workflow"]["nodes"][0]["widgets_values"] = [
            7,
            "randomize",
        ]
        baseline = certified_execution_baseline(
            CompiledCapture.from_payload(payload)
        )

        prompt_changed = copy.deepcopy(payload)
        prompt_changed["output"]["1"]["inputs"]["steps"] = 21
        queue_changed = copy.deepcopy(payload)
        queue_changed["queue_options"] = {"preview_method": "none"}
        control_changed = copy.deepcopy(payload)
        control_changed["workflow"]["nodes"][0]["widgets_values"][1] = (
            "fixed"
        )

        for changed in (prompt_changed, queue_changed, control_changed):
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    certified_execution_baseline(
                        CompiledCapture.from_payload(changed)
                    ),
                    baseline,
                )

    def test_execution_baseline_fails_closed_on_ambiguous_randomized_pairing(self):
        payload = native_capture()
        payload["workflow"]["nodes"][0]["widgets_values"] = [
            7,
            "randomize",
        ]
        payload["workflow"]["nodes"].append(
            copy.deepcopy(payload["workflow"]["nodes"][0])
        )

        capture = CompiledCapture.from_payload(payload)

        with self.assertRaises(CaptureValidationError):
            certified_execution_baseline(capture)

    def test_execution_baseline_ignores_noncertified_seed_controls(self):
        cases = (
            {"properties": {"cnr_id": "third-party"}},
            {"mode": 4},
            {"widgets_values": [7, "Randomize"]},
            {"type": "KSamplerAdvanced"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                first_payload = native_capture()
                first_payload["workflow"]["nodes"][0].update(changes)
                second_payload = copy.deepcopy(first_payload)
                second_payload["output"]["1"]["inputs"]["seed"] = 999

                first = certified_execution_baseline(
                    CompiledCapture.from_payload(first_payload)
                )
                second = certified_execution_baseline(
                    CompiledCapture.from_payload(second_payload)
                )

                self.assertEqual(first[1], ())
                self.assertEqual(second[1], ())
                self.assertNotEqual(first[0], second[0])

    def test_execution_baseline_rejects_invalid_randomized_executable_seed(self):
        for seed in (True, -1, 1.5, 2**64):
            with self.subTest(seed=seed):
                payload = native_capture()
                payload["workflow"]["nodes"][0]["widgets_values"] = [
                    7,
                    "randomize",
                ]
                payload["output"]["1"]["inputs"]["seed"] = seed
                with self.assertRaises(CaptureValidationError):
                    certified_execution_baseline(
                        CompiledCapture.from_payload(payload)
                    )

    def test_smoke_has_no_randomized_seed_and_gold_uses_node_three(self):
        smoke = CompiledCapture.from_payload(
            native_capture(
                workflow={
                    "version": 1,
                    "nodes": [
                        {
                            "id": 1,
                            "type": "EmptyImage",
                            "mode": 0,
                            "properties": {"cnr_id": "comfy-core"},
                            "widgets_values": [512, 512, 1, 0x1267A3],
                        },
                        {
                            "id": 2,
                            "type": "SaveImage",
                            "mode": 0,
                            "properties": {"cnr_id": "comfy-core"},
                            "widgets_values": ["cloud_run_core_smoke"],
                        },
                    ],
                    "extra": {"frontendVersion": "1.47.10"},
                },
                output={
                    "1": {
                        "class_type": "EmptyImage",
                        "inputs": {
                            "width": 512,
                            "height": 512,
                            "batch_size": 1,
                            "color": 0x1267A3,
                        },
                    },
                    "2": {
                        "class_type": "SaveImage",
                        "inputs": {
                            "filename_prefix": "cloud_run_core_smoke",
                            "images": ["1", 0],
                        },
                    },
                },
                queue_options={},
            )
        )
        gold_payload = native_capture()
        gold_payload["workflow"]["nodes"][0]["id"] = 3
        gold_payload["workflow"]["nodes"][0]["widgets_values"] = [
            591042719527861,
            "randomize",
        ]
        gold_payload["output"] = {
            "3": {
                "class_type": "KSampler",
                "inputs": {"seed": 591042719527861},
            }
        }
        gold = CompiledCapture.from_payload(gold_payload)

        self.assertEqual(certified_execution_baseline(smoke)[1], ())
        self.assertEqual(certified_execution_baseline(gold)[1], ("3",))


class CaptureServiceTests(unittest.TestCase):
    def test_service_validates_and_persists_without_provider_access(self):
        class ProviderThatMustNotBeUsed:
            def __getattr__(self, name):
                raise AssertionError("capture accessed provider method " + name)

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = JobRepository(
                Path(temporary_directory) / "private" / "sessions.sqlite3"
            )
            service = CloudRunService(
                settings_store=None,
                repository=None,
                job_repository=repository,
                provider=ProviderThatMustNotBeUsed(),
                clock=lambda: 25.0,
            )

            capture = asyncio.run(service.capture(native_capture()))

            self.assertEqual(
                repository.get_capture(capture.capture_id),
                capture,
            )
            self.assertEqual(capture.executable_class_types, ("KSampler",))


if __name__ == "__main__":
    unittest.main()

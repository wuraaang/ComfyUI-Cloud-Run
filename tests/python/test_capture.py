import asyncio
import tempfile
import unittest
from pathlib import Path

from cloud_run.capture import (
    MAX_CAPTURE_BYTES,
    CaptureValidationError,
    CompiledCapture,
)
from cloud_run.job_repository import JobRepository
from cloud_run.service import CloudRunService


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
            "nodes": [{"id": 1}],
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

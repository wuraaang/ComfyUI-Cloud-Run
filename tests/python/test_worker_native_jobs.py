import asyncio
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path


MANIFEST_DIGEST = "a" * 64
PROMPT_ID = "11111111-1111-4111-8111-111111111111"


def workflow_fixture():
    return {
        "version": 0.4,
        "nodes": [
            {
                "id": 9,
                "type": "SaveImage",
                "title": "Final image",
            },
            {"id": 66, "type": "PreviewImage"},
        ],
        "extra": {"frontendVersion": "1.47.10"},
    }


def native_body(*, seed=7, client_id="desktop-client-1"):
    return {
        "client_id": client_id,
        "prompt": {
            "9": {
                "class_type": "SaveImage",
                "inputs": {"seed": seed},
            },
            "66": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["9", 0]},
            },
        },
        "extra_data": {
            "extra_pnginfo": {"workflow": workflow_fixture()},
        },
    }


class StepClock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return float(self.value)


class NativeComfy:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir()
        self.output = self.root / "final.png"
        self.output.write_bytes(b"verified-native-output")
        self.history_calls = []
        self.output_descriptors = []

    async def history(self, prompt_id):
        self.history_calls.append(prompt_id)
        return {
            prompt_id: {
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": "final.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    },
                    "66": {
                        "images": [
                            {
                                "filename": "preview.png",
                                "subfolder": "",
                                "type": "temp",
                            }
                        ]
                    },
                }
            }
        }

    def output_path(self, descriptor):
        self.output_descriptors.append(dict(descriptor))
        if descriptor != {
            "filename": "final.png",
            "subfolder": "",
            "type": "output",
        }:
            raise AssertionError("temporary output reached output_path")
        return self.output

    def output_mime_type(self, path):
        if Path(path) != self.output:
            raise AssertionError("unexpected output path")
        return "image/png"


class BrokenHistoryComfy(NativeComfy):
    async def history(self, prompt_id):
        raise RuntimeError("token=must-not-survive")


class NativeJobRecorderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.state import WorkerStateStore

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = WorkerStateStore(
            self.root / "private" / "worker-state.json"
        )
        self.state.claim(
            session_id="session-1",
            session_secret_hex="b" * 64,
        )
        state = self.state.load()
        self.state.save(
            {
                **state,
                "installed": {"manifest_digest": MANIFEST_DIGEST},
            }
        )
        self.clock = StepClock()

    def recorder(self, comfy=None, *, correlation_id=None):
        from remote_worker.native_jobs import NativeJobRecorder

        return NativeJobRecorder(
            comfy=comfy or NativeComfy(self.root / "output"),
            state=self.state,
            preview_root=self.root / "previews",
            token=lambda: "native-artifact-1",
            correlation_id=(
                correlation_id or (lambda: "correlation-1")
            ),
            clock=self.clock,
        )

    def intent(self, *, job_id="job-1", request_id="request-1", body=None):
        from remote_worker.native_jobs import NativePromptIntent

        return NativePromptIntent.from_http(
            job_id=job_id,
            request_id=request_id,
            manifest_digest=MANIFEST_DIGEST,
            body=body or native_body(),
        )

    async def test_begin_is_durable_idempotent_and_manifest_bound(self):
        from remote_worker.native_jobs import (
            JobValidationError,
            NativePromptReceipt,
        )

        recorder = self.recorder()
        first = recorder.begin(self.intent())
        retry = recorder.begin(self.intent())

        self.assertIsInstance(first, NativePromptReceipt)
        self.assertTrue(first.should_forward)
        self.assertFalse(retry.should_forward)
        self.assertEqual(first.job_id, retry.job_id)
        self.assertEqual(first.request_digest, retry.request_digest)
        self.assertEqual(len(self.state.load()["jobs"]), 1)

        changed = native_body(seed=8)
        with self.assertRaises(JobValidationError):
            recorder.begin(self.intent(body=changed))
        with self.assertRaises(JobValidationError):
            recorder.begin(
                type(self.intent()).from_http(
                    job_id="job-2",
                    request_id="request-2",
                    manifest_digest="c" * 64,
                    body=native_body(),
                )
            )
        self.assertNotIn("job-2", self.state.load()["jobs"])

    async def test_identical_batch_bodies_keep_distinct_ordered_identities(self):
        recorder = self.recorder()

        first = recorder.begin(
            self.intent(job_id="job-1", request_id="request-1")
        )
        second = recorder.begin(
            self.intent(job_id="job-2", request_id="request-2")
        )

        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(first.request_digest, second.request_digest)
        records = self.state.load()["jobs"]
        self.assertLess(
            records["job-1"]["created_at"],
            records["job-2"]["created_at"],
        )
        self.assertNotEqual(
            records["job-1"]["request_id"],
            records["job-2"]["request_id"],
        )

    async def test_native_body_rejects_nested_credential_fields(self):
        from remote_worker.native_jobs import JobValidationError

        body = native_body()
        body["prompt"]["9"]["inputs"]["provider_token"] = (
            "must-not-be-persisted"
        )

        with self.assertRaises(JobValidationError):
            self.intent(body=body)
        self.assertEqual(self.state.load()["jobs"], {})

    async def test_native_text_and_binary_frames_are_only_observed(self):
        recorder = self.recorder()
        recorder.begin(self.intent())
        response = {
            "prompt_id": PROMPT_ID,
            "number": 1,
            "node_errors": {},
        }
        receipt = recorder.bind_prompt("job-1", response)
        self.assertEqual(receipt.response, response)

        text_frame = json.dumps(
            {
                "type": "executing",
                "data": {
                    "prompt_id": PROMPT_ID,
                    "node": "9",
                    "private_token": "must-not-survive",
                },
            }
        )
        original_text = str(text_frame)
        self.assertIsNone(
            recorder.observe_text("desktop-client-1", text_frame)
        )
        self.assertEqual(text_frame, original_text)

        metadata = json.dumps(
            {
                "image_type": "image/png",
                "node_id": "9",
                "prompt_id": PROMPT_ID,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        binary_frame = (
            struct.pack(">II", 4, len(metadata))
            + metadata
            + b"\x89PNG\r\n\x1a\npreview"
        )
        original_binary = bytes(binary_frame)
        self.assertIsNone(
            recorder.observe_binary("desktop-client-1", binary_frame)
        )
        self.assertEqual(binary_frame, original_binary)

        snapshot = recorder.snapshot("job-1", 0)
        self.assertEqual(
            [event["type"] for event in snapshot.events],
            ["executing", "b_preview_with_metadata"],
        )
        self.assertNotIn("must-not-survive", repr(snapshot))
        preview_id = snapshot.events[-1]["data"]["preview_id"]
        self.assertEqual(
            recorder.preview("job-1", preview_id).content,
            b"\x89PNG\r\n\x1a\npreview",
        )

    async def test_success_harvest_ignores_temp_and_is_idempotent(self):
        comfy = NativeComfy(self.root / "successful-output")
        recorder = self.recorder(comfy)
        recorder.begin(self.intent())
        recorder.bind_prompt(
            "job-1",
            {"prompt_id": PROMPT_ID, "number": 1, "node_errors": {}},
        )
        recorder.observe_text(
            "desktop-client-1",
            json.dumps(
                {
                    "type": "execution_success",
                    "data": {"prompt_id": PROMPT_ID, "timestamp": 2},
                }
            ),
        )

        first = await recorder.finish("job-1")
        second = await recorder.finish("job-1")

        self.assertEqual(first.state, "succeeded")
        self.assertEqual(second.public_payload(), first.public_payload())
        self.assertEqual(comfy.history_calls, [PROMPT_ID])
        self.assertEqual(
            comfy.output_descriptors,
            [
                {
                    "filename": "final.png",
                    "subfolder": "",
                    "type": "output",
                }
            ],
        )
        self.assertEqual(len(first.outputs), 1)
        self.assertEqual(
            first.outputs[0]["sha256"],
            hashlib.sha256(b"verified-native-output").hexdigest(),
        )
        await recorder.close()

    async def test_background_harvest_exception_is_observed_and_durable(self):
        recorder = self.recorder(
            BrokenHistoryComfy(self.root / "broken-output")
        )
        recorder.begin(self.intent())
        recorder.bind_prompt(
            "job-1",
            {"prompt_id": PROMPT_ID, "number": 1, "node_errors": {}},
        )
        recorder.observe_text(
            "desktop-client-1",
            json.dumps(
                {
                    "type": "execution_success",
                    "data": {"prompt_id": PROMPT_ID},
                }
            ),
        )

        await recorder.close()
        snapshot = recorder.snapshot("job-1", 0)

        self.assertEqual(snapshot.error["code"], "internal_error")
        self.assertEqual(snapshot.error["phase"], "internal")
        self.assertEqual(
            snapshot.error["correlation_id"],
            "correlation-1",
        )
        self.assertEqual(snapshot.execution_state, "succeeded")
        self.assertEqual(snapshot.harvest_state, "failed")
        self.assertNotIn("must-not-survive", repr(snapshot))


if __name__ == "__main__":
    unittest.main()

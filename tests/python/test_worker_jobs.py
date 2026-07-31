import asyncio
import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from cloud_run.worker_protocol import sign_request

MANIFEST_DIGEST = "d" * 64


def job_request(job_id="job-1"):
    return {
        "job_id": job_id,
        "manifest_digest": MANIFEST_DIGEST,
        "workflow": {
            "version": 0.4,
            "nodes": [
                {
                    "id": 7,
                    "type": "SaveImage",
                    "title": "Wallpaper output",
                }
            ],
            "extra": {"frontendVersion": "1.47.10"},
        },
        "output": {
            "7": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "wallpaper"},
            }
        },
        "queue_options": {
            "partial_execution_targets": ["7"],
            "preview_method": "latent2rgb",
        },
    }


class SuccessfulComfy:
    def __init__(self, output_root):
        from remote_worker.comfy import NativeExecution

        self.NativeExecution = NativeExecution
        self.output_root = Path(output_root)
        self.output_root.mkdir()
        self.output = self.output_root / "wallpaper.png"
        self.output.write_bytes(b"verified-wallpaper")
        self.prompt_bodies = []

    async def execute_native(self, prompt_body, on_event):
        self.prompt_bodies.append(prompt_body)
        prompt_id = "11111111-1111-4111-8111-111111111111"
        await on_event(
            {
                "type": "execution_start",
                "data": {"prompt_id": prompt_id, "timestamp": 1000},
            }
        )
        await on_event(
            {
                "type": "untrusted_future_event",
                "data": {"secret": "must-not-survive"},
            }
        )
        await on_event(
            {
                "type": "executing",
                "data": {
                    "prompt_id": prompt_id,
                    "node": "7",
                    "display_node": "7",
                    "secret": "must-not-survive",
                },
            }
        )
        await on_event(
            {
                "type": "b_preview_with_metadata",
                "data": {
                    "content": b"\x89PNG\r\n\x1a\npreview",
                    "mime_type": "image/png",
                    "metadata": {
                        "prompt_id": prompt_id,
                        "node_id": "7",
                        "secret": "must-not-survive",
                    },
                },
            }
        )
        await on_event(
            {
                "type": "execution_success",
                "data": {"prompt_id": prompt_id, "timestamp": 2000},
            }
        )
        return self.NativeExecution(
            prompt_id=prompt_id,
            terminal_event="execution_success",
        )

    async def history(self, prompt_id):
        return {
            prompt_id: {
                "outputs": {
                    "7": {
                        "images": [
                            {
                                "filename": "wallpaper.png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                }
            }
        }

    def output_path(self, descriptor):
        if descriptor != {
            "filename": "wallpaper.png",
            "subfolder": "",
            "type": "output",
        }:
            raise AssertionError("unexpected descriptor")
        return self.output


class BlockingComfy(SuccessfulComfy):
    def __init__(self, output_root):
        super().__init__(output_root)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_native(self, prompt_body, on_event):
        self.prompt_bodies.append(prompt_body)
        self.started.set()
        await self.release.wait()
        prompt_id = "22222222-2222-4222-8222-222222222222"
        await on_event(
            {
                "type": "execution_success",
                "data": {"prompt_id": prompt_id},
            }
        )
        return self.NativeExecution(
            prompt_id=prompt_id,
            terminal_event="execution_success",
        )


class FailingComfy(SuccessfulComfy):
    async def execute_native(self, prompt_body, on_event):
        self.prompt_bodies.append(prompt_body)
        prompt_id = "33333333-3333-4333-8333-333333333333"
        await on_event(
            {
                "type": "execution_error",
                "data": {
                    "prompt_id": prompt_id,
                    "node_id": "7",
                    "node_type": "SaveImage",
                    "exception_message": (
                        "CUDA out of memory; provider-key=must-not-survive"
                    ),
                    "traceback": ["provider-key=must-not-survive"],
                },
            }
        )
        return self.NativeExecution(
            prompt_id=prompt_id,
            terminal_event="execution_error",
        )


class ValidationFailingComfy(SuccessfulComfy):
    async def execute_native(self, prompt_body, on_event):
        from remote_worker.comfy import ComfyPromptValidationError

        self.prompt_bodies.append(prompt_body)
        raise ComfyPromptValidationError(
            error_type="prompt_outputs_failed_validation",
            node_ids=("7",),
        )


class WorkerJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.state import WorkerStateStore

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = WorkerStateStore(
            self.directory / "private" / "worker-state.json"
        )
        self.state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )
        state = self.state.load()
        self.state.save(
            {
                **state,
                "installed": {
                    "manifest_digest": MANIFEST_DIGEST,
                    "manifest": {},
                    "ready_at": 1.0,
                    "readiness": {},
                    "required_class_types": [],
                },
            }
        )

    async def test_job_posts_captured_native_body_and_returns_to_idle(self):
        from remote_worker.jobs import JobManager

        comfy = SuccessfulComfy(self.directory / "output")
        jobs = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
            token=lambda: "preview-random-id",
            clock=lambda: 10.0,
        )

        result = await jobs.run(job_request())

        self.assertEqual(
            comfy.prompt_bodies,
            [
                {
                    "client_id": result.client_id,
                    "prompt": job_request()["output"],
                    "partial_execution_targets": ["7"],
                    "extra_data": {
                        "comfy_usage_source": "comfyui-cloud-run",
                        "extra_pnginfo": {
                            "workflow": job_request()["workflow"]
                        },
                        "preview_method": "latent2rgb",
                    },
                }
            ],
        )
        uuid.UUID(result.client_id)
        self.assertEqual(result.state, "succeeded")
        self.assertFalse(jobs.busy)
        self.assertEqual(len(result.outputs), 1)
        self.assertEqual(
            result.outputs[0]["sha256"],
            hashlib.sha256(b"verified-wallpaper").hexdigest(),
        )
        self.assertEqual(
            result.outputs[0]["size_bytes"],
            len(b"verified-wallpaper"),
        )
        self.assertNotIn("private_path", result.outputs[0])
        self.assertEqual(
            [event["type"] for event in result.events],
            [
                "execution_start",
                "executing",
                "b_preview_with_metadata",
                "execution_success",
            ],
        )
        self.assertNotIn("must-not-survive", repr(result))
        preview = jobs.preview("job-1", "preview-random-id")
        self.assertEqual(preview.content, b"\x89PNG\r\n\x1a\npreview")
        self.assertEqual(preview.mime_type, "image/png")
        saved = self.state.load()["jobs"]["job-1"]
        self.assertEqual(saved["state"], "succeeded")
        self.assertEqual(saved["sequence"], 4)
        self.assertNotIn("must-not-survive", repr(saved))

    async def test_second_concurrent_job_is_rejected_without_touching_comfy(self):
        from remote_worker.jobs import JobBusyError, JobManager

        comfy = BlockingComfy(self.directory / "output")
        jobs = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
        )

        first = asyncio.create_task(jobs.run(job_request("job-1")))
        await comfy.started.wait()
        with self.assertRaises(JobBusyError):
            await jobs.run(job_request("job-2"))
        self.assertEqual(len(comfy.prompt_bodies), 1)
        comfy.release.set()
        result = await first
        self.assertEqual(result.state, "succeeded")
        self.assertFalse(jobs.busy)

    async def test_oom_is_sanitized_and_the_next_job_can_run(self):
        from remote_worker.jobs import JobManager

        failing = FailingComfy(self.directory / "failed-output")
        jobs = JobManager(
            comfy=failing,
            state=self.state,
            preview_root=self.directory / "previews",
        )
        failed = await jobs.run(job_request("job-1"))

        self.assertEqual(failed.state, "failed")
        self.assertEqual(
            failed.error,
            {
                "code": "out_of_memory",
                "message": "Remote execution ran out of GPU memory.",
                "node_id": "7",
                "class_type": "SaveImage",
                "title": "Wallpaper output",
            },
        )
        self.assertNotIn("must-not-survive", repr(failed))

        succeeding = SuccessfulComfy(self.directory / "next-output")
        jobs.comfy = succeeding
        recovered = await jobs.run(job_request("job-2"))
        self.assertEqual(recovered.state, "succeeded")
        self.assertEqual(len(succeeding.prompt_bodies), 1)

    async def test_wrong_manifest_and_duplicate_mismatch_never_touch_comfy(self):
        from remote_worker.jobs import JobManager, JobValidationError

        comfy = SuccessfulComfy(self.directory / "output")
        jobs = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
        )
        wrong = {**job_request(), "manifest_digest": "e" * 64}
        with self.assertRaises(JobValidationError):
            await jobs.run(wrong)
        self.assertEqual(comfy.prompt_bodies, [])

        first = await jobs.run(job_request())
        duplicate = await jobs.run(job_request())
        self.assertEqual(first.public_payload(), duplicate.public_payload())
        self.assertEqual(len(comfy.prompt_bodies), 1)

        changed = job_request()
        changed["output"]["7"]["inputs"]["filename_prefix"] = "changed"
        with self.assertRaises(JobValidationError):
            await jobs.run(changed)
        self.assertEqual(len(comfy.prompt_bodies), 1)

    async def test_native_validation_error_has_only_safe_node_context(self):
        from remote_worker.jobs import JobManager

        comfy = ValidationFailingComfy(
            self.directory / "validation-output"
        )
        jobs = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
        )

        result = await jobs.run(job_request())

        self.assertEqual(result.state, "failed")
        self.assertEqual(
            result.error,
            {
                "code": "validation_failed",
                "message": "Remote ComfyUI rejected the compiled prompt.",
                "node_id": "7",
                "class_type": "SaveImage",
                "title": "Wallpaper output",
            },
        )
        self.assertEqual(result.events[-1]["type"], "execution_error")
        self.assertFalse(jobs.busy)

    async def test_reopen_marks_an_orphaned_running_job_interrupted(self):
        from remote_worker.jobs import JobManager, parse_job_request

        comfy = SuccessfulComfy(self.directory / "output")
        initial = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
            clock=lambda: 10,
        )
        request = job_request()
        initial_request = parse_job_request(
            json.dumps(
                request,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        running = initial._initial_record(
            initial_request,
            "44444444-4444-4444-8444-444444444444",
        )
        self.state.record_job(
            "job-1",
            {
                **running,
                "state": "running",
            },
        )

        reopened = JobManager(
            comfy=comfy,
            state=self.state,
            preview_root=self.directory / "previews",
            clock=lambda: 20,
        )

        result = reopened.job(initial_request.job_id)
        self.assertEqual(result.state, "interrupted")
        self.assertEqual(
            result.error,
            {
                "code": "worker_restarted",
                "message": "Remote execution was interrupted by a worker restart.",
            },
        )
        self.assertEqual(
            result.events[-1]["type"],
            "execution_interrupted",
        )


class NativeComfyBoundaryTests(unittest.TestCase):
    def test_binary_protocol_decodes_only_text_and_preview_frames(self):
        from remote_worker.comfy import AiohttpComfyHttp

        jpeg = AiohttpComfyHttp._binary_event(
            struct.pack(">II", 1, 1) + b"jpeg-preview"
        )
        text = AiohttpComfyHttp._binary_event(
            struct.pack(">II", 3, 1) + b"7" + b"halfway"
        )
        metadata = json.dumps(
            {
                "image_type": "image/png",
                "node_id": "7",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        png = AiohttpComfyHttp._binary_event(
            struct.pack(">II", 4, len(metadata))
            + metadata
            + b"png-preview"
        )
        unknown = AiohttpComfyHttp._binary_event(
            struct.pack(">I", 99) + b"ignored"
        )

        self.assertEqual(jpeg["type"], "b_preview")
        self.assertEqual(jpeg["data"]["content"], b"jpeg-preview")
        self.assertEqual(
            text,
            {
                "type": "progress_text",
                "data": {"node": "7", "text": "halfway"},
            },
        )
        self.assertEqual(png["type"], "b_preview_with_metadata")
        self.assertEqual(png["data"]["content"], b"png-preview")
        self.assertIsNone(unknown)

    def test_output_descriptor_is_confined_to_real_output_files(self):
        from remote_worker.comfy import ComfyProcess, ComfyProcessError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            output_root = comfy_root / "output"
            nested = output_root / "nested"
            nested.mkdir(parents=True)
            (comfy_root / "main.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            expected = nested / "result.png"
            expected.write_bytes(b"result")
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            symlink = output_root / "link.png"
            os.symlink(outside, symlink)
            comfy = ComfyProcess(
                comfy_root=comfy_root,
                working_root=root / "working",
                python_executable=sys.executable,
            )

            self.assertEqual(
                comfy.output_path(
                    {
                        "filename": "result.png",
                        "subfolder": "nested",
                        "type": "output",
                    }
                ),
                expected.resolve(),
            )
            for descriptor in (
                {
                    "filename": "outside.png",
                    "subfolder": "..",
                    "type": "output",
                },
                {
                    "filename": "link.png",
                    "subfolder": "",
                    "type": "output",
                },
                {
                    "filename": "result.png",
                    "subfolder": "nested",
                    "type": "input",
                },
            ):
                with self.assertRaises(ComfyProcessError):
                    comfy.output_path(descriptor)


class FakeWorkerRequest:
    def __init__(
        self,
        method,
        path,
        *,
        body=b"",
        envelope=None,
        query=None,
        headers=None,
    ):
        self.method = method
        self.path = path
        self.path_qs = (
            path
            if not query
            else path
            + "?"
            + "&".join(
                str(key) + "=" + str(value)
                for key, value in query.items()
            )
        )
        self.body = body
        self.auth_envelope = envelope
        self.boundary_authenticated = True
        self.query = query or {}
        self.headers = headers or {}


class WorkerJobRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from remote_worker.jobs import JobManager
        from remote_worker.server import WorkerApplication
        from remote_worker.state import WorkerStateStore

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = (
            self.directory / "private" / "worker-state.json"
        )
        state = WorkerStateStore(self.path)
        state.claim(
            session_id="session-1",
            session_secret_hex="a" * 64,
        )
        saved = state.load()
        state.save(
            {
                **saved,
                "installed": {
                    "manifest_digest": MANIFEST_DIGEST,
                    "manifest": {},
                    "ready_at": 1.0,
                    "readiness": {},
                    "required_class_types": [],
                },
            }
        )
        self.comfy = SuccessfulComfy(self.directory / "output")
        self.jobs = JobManager(
            comfy=self.comfy,
            state=state,
            preview_root=self.directory / "previews",
            token=lambda: "preview-random-id",
            clock=lambda: 1000,
        )
        self.worker = WorkerApplication(
            state_path=self.path,
            clock=lambda: 1000,
            job_manager=self.jobs,
        )
        self.nonce = 0

    def request(
        self,
        method,
        path,
        *,
        payload=None,
        query=None,
        headers=None,
    ):
        body = (
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if payload is not None
            else b""
        )
        path_qs = (
            path
            if not query
            else path
            + "?"
            + "&".join(
                str(key) + "=" + str(value)
                for key, value in query.items()
            )
        )
        self.nonce += 1
        envelope = sign_request(
            bytes.fromhex("a" * 64),
            method,
            path_qs,
            body,
            timestamp=1000,
            nonce="job-route-" + str(self.nonce),
        )
        return FakeWorkerRequest(
            method,
            path,
            body=body,
            envelope=envelope,
            query=query,
            headers=headers,
        )

    async def test_signed_job_status_events_and_preview_routes(self):
        created = await self.worker.handle(
            self.request(
                "POST",
                "/worker/v1/jobs",
                payload=job_request(),
            )
        )
        status = await self.worker.handle(
            self.request("GET", "/worker/v1/jobs/job-1")
        )
        events = await self.worker.handle(
            self.request(
                "GET",
                "/worker/v1/jobs/job-1/events",
                query={"after_sequence": "1"},
            )
        )
        preview = await self.worker.handle(
            self.request(
                "GET",
                (
                    "/worker/v1/jobs/job-1/previews/"
                    "preview-random-id"
                ),
            )
        )

        self.assertEqual(created.status, 200)
        self.assertEqual(created.payload["state"], "succeeded")
        self.assertEqual(status.status, 200)
        self.assertEqual(status.payload["job_id"], "job-1")
        self.assertEqual(events.status, 200)
        self.assertEqual(
            [event["sequence"] for event in events.payload["events"]],
            [2, 3, 4],
        )
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.payload, b"\x89PNG\r\n\x1a\npreview")
        self.assertEqual(preview.headers["Content-Type"], "image/png")
        self.assertNotIn("private_path", repr(created.payload))

    async def test_output_route_honors_one_bounded_range(self):
        created = await self.worker.handle(
            self.request(
                "POST",
                "/worker/v1/jobs",
                payload=job_request(),
            )
        )
        artifact_id = created.payload["outputs"][0]["artifact_id"]
        response = await self.worker.handle(
            self.request(
                "GET",
                "/worker/v1/artifacts/" + artifact_id,
                query={"start": "5"},
                headers={"Range": "bytes=5-"},
            )
        )

        self.assertEqual(response.status, 206)
        self.assertEqual(response.payload.start, 5)
        self.assertEqual(
            response.headers["Content-Range"],
            "bytes 5-17/18",
        )
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        self.assertNotIn(str(self.directory), repr(response.headers))


if __name__ == "__main__":
    unittest.main()

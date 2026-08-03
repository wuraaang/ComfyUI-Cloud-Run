"""Compatibility headless jobs backed by the native Desktop recorder."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
import uuid

from cloud_run.run_errors import RunErrorCode, RunPhase
from cloud_run.worker_protocol import (
    CaptureValidationError,
    CompiledCapture,
    canonical_json,
)
from .comfy import (
    ComfyProcessError,
    ComfyPromptValidationError,
    NativeExecution,
)
from .native_jobs import (
    JobBusyError,
    JobError,
    JobResult,
    JobSnapshot,
    JobValidationError,
    NativeJobRecorder,
    NativePromptIntent,
    OutputContent,
    PreviewContent,
    execution_error,
)
from .state import WorkerStateError, WorkerStateStore


MAX_JOB_REQUEST_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_REQUEST_FIELDS = {
    "job_id",
    "manifest_digest",
    "workflow",
    "output",
    "queue_options",
}


@dataclass(frozen=True)
class JobRequest:
    job_id: str
    manifest_digest: str
    capture: CompiledCapture
    request_digest: str


def _job_error():
    return JobValidationError("Remote job request was rejected.")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def _request_from_payload(payload):
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        raise _job_error()
    job_id = payload.get("job_id")
    manifest_digest = payload.get("manifest_digest")
    if (
        not isinstance(job_id, str)
        or not _IDENTIFIER.fullmatch(job_id)
        or not isinstance(manifest_digest, str)
        or not _HEX_64.fullmatch(manifest_digest)
    ):
        raise _job_error()
    try:
        capture = CompiledCapture.from_payload(
            {
                "workflow": payload["workflow"],
                "output": payload["output"],
                "queue_options": payload["queue_options"],
            }
        )
        material = canonical_json(
            {
                "manifest_digest": manifest_digest,
                "workflow": capture.workflow,
                "output": capture.output,
                "queue_options": capture.queue_options,
            }
        ).encode("utf-8")
    except (CaptureValidationError, TypeError, ValueError):
        raise _job_error() from None
    return JobRequest(
        job_id=job_id,
        manifest_digest=manifest_digest,
        capture=capture,
        request_digest=hashlib.sha256(material).hexdigest(),
    )


def parse_job_request(body):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= MAX_JOB_REQUEST_BYTES
    ):
        raise _job_error()
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _job_error() from None
    return _request_from_payload(payload)


def _native_body(request, client_id):
    options = request.capture.queue_options
    body = {
        "client_id": client_id,
        "prompt": request.capture.output,
        "extra_data": {
            "comfy_usage_source": "comfyui-cloud-run",
            "extra_pnginfo": {"workflow": request.capture.workflow},
            **(
                {"preview_method": options["preview_method"]}
                if "preview_method" in options
                else {}
            ),
        },
    }
    for key in ("front", "number", "partial_execution_targets"):
        if key in options:
            body[key] = options[key]
    return body


class JobManager:
    def __init__(
        self,
        *,
        comfy,
        state,
        preview_root,
        token=None,
        clock=None,
    ):
        if not callable(getattr(comfy, "execute_native", None)):
            raise ValueError("Remote ComfyUI job boundary is invalid.")
        if not isinstance(state, WorkerStateStore):
            raise ValueError("Remote worker job state is invalid.")
        self._comfy = comfy
        self.state = state
        self.recorder = NativeJobRecorder(
            comfy=comfy,
            state=state,
            preview_root=preview_root,
            token=token,
            clock=clock,
        )
        self.preview_root = self.recorder.preview_root
        self._active_job_id = None
        self._guard = None
        self._guard_loop = None
        self._submission_guard = None
        self._submission_guard_loop = None
        self._background_tasks = set()

    @property
    def comfy(self):
        return self._comfy

    @comfy.setter
    def comfy(self, value):
        if (
            not callable(getattr(value, "execute_native", None))
            or not callable(getattr(value, "history", None))
            or not callable(getattr(value, "output_path", None))
        ):
            raise ValueError("Remote ComfyUI job boundary is invalid.")
        self._comfy = value
        if hasattr(self, "recorder"):
            self.recorder.comfy = value

    @property
    def busy(self):
        return self._active_job_id is not None

    def _job_guard(self):
        loop = asyncio.get_running_loop()
        if self._guard is None or self._guard_loop is not loop:
            self._guard = asyncio.Lock()
            self._guard_loop = loop
        return self._guard

    def _job_submission_guard(self):
        loop = asyncio.get_running_loop()
        if (
            self._submission_guard is None
            or self._submission_guard_loop is not loop
        ):
            self._submission_guard = asyncio.Lock()
            self._submission_guard_loop = loop
        return self._submission_guard

    def _intent(self, request, client_id):
        return NativePromptIntent.from_http(
            job_id=request.job_id,
            request_id=request.job_id,
            manifest_digest=request.manifest_digest,
            body=_native_body(request, client_id),
        )

    def _initial_record(self, request, client_id):
        return self.recorder._initial_record(self._intent(request, client_id))

    def _safe_error(self, *, code, phase, message, retryable):
        return self.recorder._safe_run_error(
            code=code,
            phase=phase,
            message=message,
            retryable=retryable,
        )

    async def run(self, payload):
        request = (
            payload
            if isinstance(payload, JobRequest)
            else _request_from_payload(payload)
        )
        async with self._job_guard():
            try:
                existing_record = self.state.job(request.job_id)
            except WorkerStateError:
                raise JobError(
                    "Remote worker job state is unavailable."
                ) from None
            if existing_record is None and self._active_job_id is not None:
                raise JobBusyError("Remote worker is already executing a job.")
            client_id = (
                existing_record["client_id"]
                if existing_record is not None
                else str(uuid.uuid4())
            )
            intent = self._intent(request, client_id)
            receipt = self.recorder.begin(intent)
            if not receipt.should_forward:
                result = self.recorder.job(request.job_id)
                if result is None:
                    raise JobError(
                        "Remote worker job state is unavailable."
                    )
                return result
            self._active_job_id = request.job_id

        async def on_event(event):
            self.recorder.observe_event(
                request.job_id,
                event,
                schedule_harvest=False,
            )

        try:
            execution = await self.comfy.execute_native(intent.body, on_event)
            if not isinstance(execution, NativeExecution):
                raise JobError("Remote execution result is invalid.")
            response = execution.response or {
                "prompt_id": execution.prompt_id,
                "number": 0,
                "node_errors": {},
            }
            self.recorder.bind_prompt(request.job_id, response)
            current = self.recorder.job(request.job_id)
            if current is None:
                raise JobError("Remote worker job state is unavailable.")
            if execution.terminal_event == "execution_success":
                if current.execution_state != "succeeded":
                    self.recorder.observe_event(
                        request.job_id,
                        {
                            "type": "execution_success",
                            "data": {"prompt_id": execution.prompt_id},
                        },
                        schedule_harvest=False,
                    )
                return await self.recorder.finish(request.job_id)
            if current.execution_state not in {"failed", "interrupted"}:
                self.recorder.observe_event(
                    request.job_id,
                    {
                        "type": execution.terminal_event,
                        "data": {"prompt_id": execution.prompt_id},
                    },
                    schedule_harvest=False,
                )
            result = self.recorder.job(request.job_id)
            if result is None:
                raise JobError("Remote worker job state is unavailable.")
            return result
        except ComfyPromptValidationError as error:
            context = {"node_id": error.node_ids[0] if error.node_ids else None}
            self.recorder.observe_event(
                request.job_id,
                {
                    "type": "execution_error",
                    "data": execution_error(
                        request.capture,
                        context,
                        validation=True,
                    ),
                },
                schedule_harvest=False,
            )
        except asyncio.CancelledError:
            self.recorder.fail(
                request.job_id,
                {
                    "code": "execution_interrupted",
                    "message": "Remote execution was interrupted.",
                },
                execution=True,
                interrupted=True,
            )
            raise
        except (JobError, ComfyProcessError):
            self.recorder.fail(
                request.job_id,
                {
                    "code": "execution_failed",
                    "message": "Remote execution failed.",
                },
                execution=True,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.recorder.fail(
                request.job_id,
                self._safe_error(
                    code=RunErrorCode.INTERNAL,
                    phase=RunPhase.EXECUTION,
                    message="Remote execution failed.",
                    retryable=False,
                ),
                execution=True,
            )
        finally:
            async with self._job_guard():
                if self._active_job_id == request.job_id:
                    self._active_job_id = None
        result = self.recorder.job(request.job_id)
        if result is None:
            raise JobError("Remote worker job state is unavailable.")
        return result

    async def start(self, payload):
        request = (
            payload
            if isinstance(payload, JobRequest)
            else _request_from_payload(payload)
        )
        async with self._job_submission_guard():
            existing = self.job(request.job_id)
            if existing is not None:
                try:
                    record = self.state.job(request.job_id)
                except WorkerStateError:
                    raise JobError(
                        "Remote worker job state is unavailable."
                    ) from None
                intent = self._intent(request, record["client_id"])
                receipt = self.recorder.begin(intent)
                if receipt.should_forward:
                    raise JobError(
                        "Remote worker job state is unavailable."
                    )
                return existing
            if self.busy:
                raise JobBusyError(
                    "Remote worker is already executing a job."
                )
            task = asyncio.create_task(self.run(request))
            self._background_tasks.add(task)

            def complete(done):
                self._background_tasks.discard(done)
                if done.cancelled():
                    return
                try:
                    done.result()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    try:
                        self.recorder.fail(
                            request.job_id,
                            self._safe_error(
                                code=RunErrorCode.INTERNAL,
                                phase=RunPhase.EXECUTION,
                                message="Remote execution failed.",
                                retryable=False,
                            ),
                            execution=True,
                        )
                    except JobError:
                        pass

            task.add_done_callback(complete)
            await asyncio.sleep(0)
            current = self.job(request.job_id)
            if current is None:
                task.cancel()
                raise JobError(
                    "Remote worker job state is unavailable."
                )
            return current

    def job(self, job_id):
        return self.recorder.job(job_id)

    def snapshot(self, job_id, after_sequence=0):
        return self.recorder.snapshot(job_id, after_sequence)

    def events(self, job_id, after_sequence=0):
        return self.recorder.events(job_id, after_sequence)

    def preview(self, job_id, preview_id):
        return self.recorder.preview(job_id, preview_id)

    def output(self, artifact_id):
        return self.recorder.output(artifact_id)

    async def close(self):
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        await self.recorder.close()

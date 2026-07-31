"""Validation and content addressing for native ComfyUI queue captures."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import uuid


MAX_CAPTURE_BYTES = 16 * 1024 * 1024
PINNED_FRONTEND_VERSION = "1.47.10"
ALLOWED_QUEUE_OPTIONS = {
    "front",
    "number",
    "partial_execution_targets",
    "preview_method",
}
FORBIDDEN_KEYS = {
    "auth_token_comfy_org",
    "api_key_comfy_org",
    "api_key",
    "authorization",
    "bearer",
    "signed_url",
}
MAX_NESTING_DEPTH = 64


class CaptureValidationError(ValueError):
    pass


def canonical_json(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError):
        raise CaptureValidationError("Invalid compiled canvas payload.") from None


def prompt_digest(output, queue_options):
    material = canonical_json(
        {
            "output": output,
            "queue_options": queue_options,
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _validate_tree(root):
    stack = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            raise CaptureValidationError("Compiled canvas nesting is too deep.")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise CaptureValidationError(
                        "Compiled canvas object keys must be strings."
                    )
                if key.casefold() in FORBIDDEN_KEYS:
                    raise CaptureValidationError(
                        "Compiled canvas contains forbidden credential data."
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise CaptureValidationError(
                "Compiled canvas numbers must be finite."
            )
        elif value is not None and not isinstance(
            value,
            (bool, int, float, str),
        ):
            raise CaptureValidationError(
                "Compiled canvas contains an unsupported value."
            )


def _validate_identifier(value, name, *, maximum=256):
    if not isinstance(value, str):
        raise CaptureValidationError(f"Invalid {name}.")
    if (
        not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise CaptureValidationError(f"Invalid {name}.")
    return value


def _validate_queue_options(queue_options, output_ids):
    if not isinstance(queue_options, dict):
        raise CaptureValidationError("Queue options must be an object.")
    if set(queue_options) - ALLOWED_QUEUE_OPTIONS:
        raise CaptureValidationError("Unsupported queue option.")
    if "front" in queue_options and not isinstance(
        queue_options["front"],
        bool,
    ):
        raise CaptureValidationError("Invalid front queue option.")
    if "number" in queue_options:
        number = queue_options["number"]
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or number == 0
        ):
            raise CaptureValidationError("Invalid queue number.")
    if queue_options.get("front") and "number" in queue_options:
        raise CaptureValidationError("Conflicting queue priority options.")
    if "partial_execution_targets" in queue_options:
        targets = queue_options["partial_execution_targets"]
        if not isinstance(targets, list) or not targets:
            raise CaptureValidationError("Invalid partial execution targets.")
        normalized = [
            _validate_identifier(str(target), "partial execution target")
            for target in targets
        ]
        if len(set(normalized)) != len(normalized):
            raise CaptureValidationError(
                "Partial execution targets must be unique."
            )
        if not set(normalized).issubset(output_ids):
            raise CaptureValidationError(
                "Partial execution target is absent from the prompt."
            )
    if "preview_method" in queue_options:
        _validate_identifier(
            queue_options["preview_method"],
            "preview method",
            maximum=128,
        )


@dataclass(frozen=True)
class CompiledCapture:
    capture_id: str
    workflow: dict
    output: dict
    queue_options: dict
    prompt_digest: str
    executable_class_types: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload, *, capture_id=None):
        if not isinstance(payload, dict) or set(payload) != {
            "workflow",
            "output",
            "queue_options",
        }:
            raise CaptureValidationError(
                "Compiled canvas payload fields are invalid."
            )
        _validate_tree(payload)
        encoded = canonical_json(payload)
        if len(encoded.encode("utf-8")) > MAX_CAPTURE_BYTES:
            raise CaptureValidationError("Compiled canvas payload is too large.")
        detached = json.loads(encoded)
        workflow = detached["workflow"]
        output = detached["output"]
        queue_options = detached["queue_options"]
        if not isinstance(workflow, dict):
            raise CaptureValidationError("Workflow must be an object.")
        extra = workflow.get("extra")
        if (
            not isinstance(extra, dict)
            or extra.get("frontendVersion") != PINNED_FRONTEND_VERSION
        ):
            raise CaptureValidationError(
                "Pinned ComfyUI frontend version is required."
            )
        if not isinstance(workflow.get("nodes"), list):
            raise CaptureValidationError("Workflow nodes must be a list.")
        if not isinstance(output, dict) or not output:
            raise CaptureValidationError("Executable prompt must not be empty.")

        class_types = set()
        output_ids = set()
        for node_id, node in output.items():
            output_ids.add(_validate_identifier(node_id, "prompt node ID"))
            if not isinstance(node, dict):
                raise CaptureValidationError("Prompt nodes must be objects.")
            class_type = _validate_identifier(
                node.get("class_type"),
                "prompt class type",
            )
            if not isinstance(node.get("inputs"), dict):
                raise CaptureValidationError(
                    "Prompt node inputs must be objects."
                )
            class_types.add(class_type)

        _validate_queue_options(queue_options, output_ids)
        identifier = str(capture_id or uuid.uuid4())
        try:
            identifier = str(uuid.UUID(identifier))
        except (AttributeError, TypeError, ValueError):
            raise CaptureValidationError("Invalid capture identity.") from None
        return cls(
            capture_id=identifier,
            workflow=workflow,
            output=output,
            queue_options=queue_options,
            prompt_digest=prompt_digest(output, queue_options),
            executable_class_types=tuple(sorted(class_types)),
        )

    def canonical_payload(self):
        return canonical_json(
            {
                "workflow": self.workflow,
                "output": self.output,
                "queue_options": self.queue_options,
            }
        )

    @classmethod
    def from_record(cls, capture_id, capture_json, expected_prompt_digest):
        try:
            payload = json.loads(capture_json)
        except (json.JSONDecodeError, TypeError):
            raise CaptureValidationError("Stored capture is invalid.") from None
        capture = cls.from_payload(payload, capture_id=capture_id)
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected_prompt_digest or "")):
            raise CaptureValidationError("Stored prompt digest is invalid.")
        if capture.prompt_digest != expected_prompt_digest:
            raise CaptureValidationError("Stored capture digest does not match.")
        return capture

"""Public local import surface for the shared native capture contract."""

import hashlib
import json

from .worker_protocol import (
    ALLOWED_QUEUE_OPTIONS,
    FORBIDDEN_KEYS,
    MAX_CAPTURE_BYTES,
    MAX_NESTING_DEPTH,
    PINNED_FRONTEND_VERSION,
    CaptureValidationError,
    CompiledCapture,
    canonical_native_prompt_body,
    canonical_json,
    prompt_digest,
)


_RANDOMIZED_SEED_SENTINEL = "__cloud_run_randomized_seed__"
_MAX_KSAMPLER_SEED = 2**64 - 1


def _workflow_node_id(node):
    if not isinstance(node, dict):
        return None
    value = node.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value)


def certified_execution_baseline(capture):
    """Hash executable material while normalizing reviewed random seeds."""
    if not isinstance(capture, CompiledCapture):
        raise CaptureValidationError("Compiled canvas is required.")

    normalized_output = json.loads(canonical_json(capture.output))
    randomized_node_ids = []
    workflow_nodes = capture.workflow.get("nodes")

    for node_id, prompt_node in capture.output.items():
        if prompt_node.get("class_type") != "KSampler":
            continue
        matches = [
            node
            for node in workflow_nodes
            if isinstance(node, dict)
            and type(node.get("mode", 0)) is int
            and node.get("mode", 0) == 0
            and _workflow_node_id(node) == node_id
        ]
        if len(matches) > 1:
            raise CaptureValidationError(
                "Randomized KSampler pairing is ambiguous."
            )
        if not matches:
            continue
        workflow_node = matches[0]
        properties = workflow_node.get("properties")
        widgets = workflow_node.get("widgets_values")
        if (
            workflow_node.get("type") != "KSampler"
            or not isinstance(properties, dict)
            or properties.get("cnr_id") != "comfy-core"
            or not isinstance(widgets, list)
            or len(widgets) < 2
            or widgets[1] != "randomize"
        ):
            continue
        seed = prompt_node.get("inputs", {}).get("seed")
        if (
            type(seed) is not int
            or not 0 <= seed <= _MAX_KSAMPLER_SEED
        ):
            raise CaptureValidationError(
                "Randomized KSampler seed is invalid."
            )
        normalized_output[node_id]["inputs"]["seed"] = (
            _RANDOMIZED_SEED_SENTINEL
        )
        randomized_node_ids.append(node_id)

    randomized_node_ids = tuple(sorted(randomized_node_ids))
    material = canonical_json(
        {
            "output": normalized_output,
            "queue_options": capture.queue_options,
            "randomized_seed_node_ids": list(randomized_node_ids),
        }
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest(), randomized_node_ids


__all__ = [
    "ALLOWED_QUEUE_OPTIONS",
    "FORBIDDEN_KEYS",
    "MAX_CAPTURE_BYTES",
    "MAX_NESTING_DEPTH",
    "PINNED_FRONTEND_VERSION",
    "CaptureValidationError",
    "CompiledCapture",
    "canonical_native_prompt_body",
    "canonical_json",
    "certified_execution_baseline",
    "prompt_digest",
]

import asyncio
import unittest

from cloud_run.artifacts import FileInputMetadata, StaticFileRequirement
from cloud_run.huggingface import (
    HUGGINGFACE_ORIGIN,
    HuggingFaceError,
    ResolvedHuggingFaceFile,
    parse_huggingface_url,
)
from cloud_run.manifest import SourceSpec
from cloud_run.model_sources import (
    ModelSourceResolution,
    WorkflowModelSourceResolver,
)


def model_record(
    name="example.safetensors",
    *,
    repository="example/public-model",
    file_path="files/example.safetensors",
    directory="diffusion_models",
    digest="c" * 64,
    query="",
):
    record = {
        "name": name,
        "url": (
            HUGGINGFACE_ORIGIN
            + "/"
            + repository
            + "/resolve/main/"
            + file_path
            + query
        ),
        "directory": directory,
    }
    if digest is not None:
        record["hash"] = digest
        record["hash_type"] = "sha256"
    return record


def workflow_node(node_id, *, selected, records=None):
    node = {
        "id": int(node_id),
        "type": "UNETLoader",
        "mode": 0,
        "widgets_values": [selected],
    }
    if records is not None:
        node["properties"] = {"models": records}
    return node


def model_requirement(
    node_id,
    *,
    value="example.safetensors",
    input_name="model_name",
    directory="diffusion_models",
):
    return StaticFileRequirement(
        node_id=str(node_id),
        class_type="UNETLoader",
        input_name=input_name,
        metadata=FileInputMetadata(kind="model", category=directory),
        value=value,
    )


def resolved_file(
    *,
    repository="example/public-model",
    file_path="files/example.safetensors",
    revision="a" * 40,
    digest="c" * 64,
    size_bytes=4096,
):
    return ResolvedHuggingFaceFile(
        repository_id=repository,
        file_path=file_path,
        immutable_revision=revision,
        locator=(
            HUGGINGFACE_ORIGIN
            + "/"
            + repository
            + "/resolve/"
            + revision
            + "/"
            + file_path
        ),
        size_bytes=size_bytes,
        sha256=digest,
    )


class FakeCapture:
    def __init__(self, workflow):
        self.workflow = workflow


class FakeHuggingFaceClient:
    def __init__(self, responses, *, yield_once=False):
        self.responses = dict(responses)
        self.calls = []
        self.yield_once = yield_once
        self.active = 0
        self.peak_active = 0

    async def resolve(self, reference, *, expected_sha256=None):
        key = (reference, expected_sha256)
        self.calls.append(key)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        if self.yield_once:
            await asyncio.sleep(0)
        self.active -= 1
        response = self.responses[key]
        if isinstance(response, Exception):
            raise response
        return response


class WorkflowModelSourceResolverTests(unittest.TestCase):
    def test_verified_candidate_becomes_only_a_commit_pinned_source(self):
        record = model_record(query="?download=true")
        reference = parse_huggingface_url(record["url"])
        verified = resolved_file()
        client = FakeHuggingFaceClient(
            {(reference, "c" * 64): verified}
        )
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(
                        1,
                        selected="example.safetensors",
                        records=[record],
                    )
                ]
            }
        )

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(model_requirement(1),),
            )
        )

        self.assertEqual(
            result,
            {
                ("1", "model_name"): ModelSourceResolution(
                    status="resolved",
                    source=SourceSpec(
                        kind="huggingface",
                        locator=verified.locator,
                        immutable_revision="a" * 40,
                    ),
                    size_bytes=4096,
                    sha256="c" * 64,
                    reason=None,
                )
            },
        )
        self.assertEqual(client.calls, [(reference, "c" * 64)])
        self.assertNotEqual(result[("1", "model_name")].source.locator, record["url"])
        self.assertNotIn("main", result[("1", "model_name")].source.locator)

    def test_only_static_string_model_requirements_are_resolved(self):
        record = model_record()
        reference = parse_huggingface_url(record["url"])
        client = FakeHuggingFaceClient(
            {(reference, "c" * 64): resolved_file()}
        )
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(
                        1,
                        selected="example.safetensors",
                        records=[record],
                    )
                ]
            }
        )
        input_requirement = StaticFileRequirement(
            node_id="1",
            class_type="LoadImage",
            input_name="image",
            metadata=FileInputMetadata(kind="input"),
            value="source.png",
        )

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(
                    input_requirement,
                    model_requirement(1),
                    model_requirement(1, value=["2", 0], input_name="linked"),
                    model_requirement(1, value=None, input_name="missing"),
                ),
            )
        )

        self.assertEqual(tuple(result), (("1", "model_name"),))
        self.assertEqual(len(client.calls), 1)

    def test_missing_mismatched_and_conflicting_metadata_require_mapping(self):
        conflict = [
            model_record(),
            model_record(repository="other/public-model"),
        ]
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(1, selected="example.safetensors"),
                    workflow_node(
                        2,
                        selected="example.safetensors",
                        records=[model_record(name="stale.safetensors")],
                    ),
                    workflow_node(
                        3,
                        selected="example.safetensors",
                        records=conflict,
                    ),
                ]
            }
        )
        client = FakeHuggingFaceClient({})

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=tuple(model_requirement(node_id) for node_id in (1, 2, 3)),
            )
        )

        self.assertEqual(
            [item.status for item in result.values()],
            ["mapping_required", "mapping_required", "mapping_required"],
        )
        self.assertTrue(
            all(
                item.reason == "Native model metadata is missing or ambiguous."
                and item.source is None
                for item in result.values()
            )
        )
        self.assertEqual(client.calls, [])

    def test_invalid_metadata_and_unverifiable_file_are_sanitized(self):
        malformed = {
            **model_record(),
            "url": "https://example.com/not-supported",
        }
        valid = model_record(
            name="other.safetensors",
            file_path="files/other.safetensors",
            digest="d" * 64,
        )
        valid_reference = parse_huggingface_url(valid["url"])
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(
                        1,
                        selected="example.safetensors",
                        records=[malformed],
                    ),
                    workflow_node(
                        2,
                        selected="other.safetensors",
                        records=[valid],
                    ),
                ]
            }
        )
        client = FakeHuggingFaceClient(
            {
                (valid_reference, "d" * 64): HuggingFaceError(
                    "private upstream detail"
                )
            }
        )

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(
                    model_requirement(1),
                    model_requirement(2, value="other.safetensors"),
                ),
            )
        )

        self.assertEqual(result[("1", "model_name")].status, "unsupported")
        self.assertEqual(
            result[("1", "model_name")].reason,
            "Native model metadata is invalid.",
        )
        self.assertEqual(result[("2", "model_name")].status, "unsupported")
        self.assertEqual(
            result[("2", "model_name")].reason,
            "The public Hugging Face file could not be verified.",
        )
        self.assertNotIn("private", result[("2", "model_name")].reason)

    def test_malformed_workflow_marks_each_static_model_unsupported(self):
        capture = FakeCapture({"nodes": [], "definitions": []})
        client = FakeHuggingFaceClient({})

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(model_requirement(1), model_requirement(2)),
            )
        )

        self.assertEqual(tuple(result), (("1", "model_name"), ("2", "model_name")))
        self.assertTrue(
            all(
                item.status == "unsupported"
                and item.reason == "Native model metadata is invalid."
                for item in result.values()
            )
        )
        self.assertEqual(client.calls, [])

    def test_identical_candidates_are_resolved_once_for_multiple_nodes(self):
        record = model_record()
        reference = parse_huggingface_url(record["url"])
        client = FakeHuggingFaceClient(
            {(reference, "c" * 64): resolved_file()}
        )
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(
                        node_id,
                        selected="example.safetensors",
                        records=[record],
                    )
                    for node_id in (1, 2)
                ]
            }
        )

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(model_requirement(1), model_requirement(2)),
            )
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            tuple(result),
            (("1", "model_name"), ("2", "model_name")),
        )
        self.assertTrue(all(item.status == "resolved" for item in result.values()))

    def test_distinct_candidates_resolve_concurrently_in_requirement_order(self):
        first_record = model_record(
            name="first.safetensors",
            repository="example/first",
            file_path="files/first.safetensors",
            digest="1" * 64,
        )
        second_record = model_record(
            name="second.safetensors",
            repository="example/second",
            file_path="files/second.safetensors",
            digest="2" * 64,
        )
        first_reference = parse_huggingface_url(first_record["url"])
        second_reference = parse_huggingface_url(second_record["url"])
        client = FakeHuggingFaceClient(
            {
                (second_reference, "2" * 64): resolved_file(
                    repository="example/second",
                    file_path="files/second.safetensors",
                    digest="2" * 64,
                ),
                (first_reference, "1" * 64): resolved_file(
                    repository="example/first",
                    file_path="files/first.safetensors",
                    digest="1" * 64,
                ),
            },
            yield_once=True,
        )
        capture = FakeCapture(
            {
                "nodes": [
                    workflow_node(
                        1,
                        selected="first.safetensors",
                        records=[first_record],
                    ),
                    workflow_node(
                        2,
                        selected="second.safetensors",
                        records=[second_record],
                    ),
                ]
            }
        )

        result = asyncio.run(
            WorkflowModelSourceResolver(client).resolve(
                capture,
                requirements=(
                    model_requirement(2, value="second.safetensors"),
                    model_requirement(1, value="first.safetensors"),
                ),
            )
        )

        self.assertEqual(client.peak_active, 2)
        self.assertEqual(
            tuple(result),
            (("2", "model_name"), ("1", "model_name")),
        )

    def test_substituted_or_inconsistent_client_result_is_unsupported(self):
        record = model_record()
        reference = parse_huggingface_url(record["url"])
        bad_results = {
            "repository substitution": resolved_file(
                repository="other/public-model"
            ),
            "digest mismatch": resolved_file(digest="d" * 64),
            "mutable locator": ResolvedHuggingFaceFile(
                repository_id="example/public-model",
                file_path="files/example.safetensors",
                immutable_revision="a" * 40,
                locator=(
                    HUGGINGFACE_ORIGIN
                    + "/example/public-model/resolve/main/"
                    + "files/example.safetensors"
                ),
                size_bytes=4096,
                sha256="c" * 64,
            ),
        }

        for label, bad_result in bad_results.items():
            with self.subTest(label=label):
                client = FakeHuggingFaceClient(
                    {(reference, "c" * 64): bad_result}
                )
                capture = FakeCapture(
                    {
                        "nodes": [
                            workflow_node(
                                1,
                                selected="example.safetensors",
                                records=[record],
                            )
                        ]
                    }
                )
                result = asyncio.run(
                    WorkflowModelSourceResolver(client).resolve(
                        capture,
                        requirements=(model_requirement(1),),
                    )
                )
                resolution = result[("1", "model_name")]
                self.assertEqual(resolution.status, "unsupported")
                self.assertIsNone(resolution.source)
                self.assertEqual(
                    resolution.reason,
                    "The public Hugging Face file could not be verified.",
                )


if __name__ == "__main__":
    unittest.main()

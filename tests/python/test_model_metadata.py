from copy import deepcopy
import unittest
from unittest.mock import patch

from cloud_run import model_metadata
from cloud_run.huggingface import HuggingFaceReference
from cloud_run.model_metadata import (
    EmbeddedModelCandidate,
    EmbeddedModelIndex,
    ModelMetadataError,
)


_NO_HASH = object()


def model_record(
    name="example.safetensors",
    *,
    repository="example/public-model",
    revision="main",
    file_path="files/example.safetensors",
    directory="diffusion_models",
    digest="c" * 64,
    query="",
):
    record = {
        "name": name,
        "url": (
            "https://huggingface.co/"
            + repository
            + "/resolve/"
            + revision
            + "/"
            + file_path
            + query
        ),
        "directory": directory,
    }
    if digest is not _NO_HASH:
        record["hash"] = digest
        record["hash_type"] = "sha256"
    return record


def workflow_node(
    node_id,
    *,
    selected="example.safetensors",
    records=None,
    node_type="UNETLoader",
    mode=0,
):
    node = {
        "id": node_id,
        "type": node_type,
        "mode": mode,
        "widgets_values": [selected],
    }
    if records is not None:
        node["properties"] = {"models": records}
    return node


class EmbeddedModelIndexTests(unittest.TestCase):
    def test_flat_exact_match_is_read_only_and_ignores_stale_records(self):
        exact = model_record()
        stale = model_record(
            "stale.safetensors",
            file_path="files/stale.safetensors",
        )
        workflow = {
            "nodes": [workflow_node(1, records=[exact, stale])],
        }
        original = deepcopy(workflow)

        index = EmbeddedModelIndex.from_workflow(workflow)
        lookup = index.lookup(
            node_id="1",
            name="example.safetensors",
            directory="diffusion_models",
        )

        self.assertEqual(workflow, original)
        self.assertEqual(lookup.status, "resolved")
        self.assertIsNone(lookup.reason)
        self.assertEqual(
            lookup.candidate,
            EmbeddedModelCandidate(
                node_id="1",
                name="example.safetensors",
                directory="diffusion_models",
                reference=HuggingFaceReference(
                    repository_id="example/public-model",
                    revision="main",
                    file_path="files/example.safetensors",
                ),
                expected_sha256="c" * 64,
            ),
        )
        self.assertEqual(
            index.lookup(
                node_id="1",
                name="stale.safetensors",
                directory="diffusion_models",
            ).status,
            "mapping_required",
        )
        self.assertEqual(
            index.lookup(
                node_id="1",
                name="example.safetensors",
                directory="vae",
            ).status,
            "mapping_required",
        )

    def test_normalized_duplicates_collapse_and_merge_one_supplied_hash(self):
        without_hash = model_record(digest=_NO_HASH)
        with_hash = model_record(query="?download=true")
        workflow = {
            "nodes": [
                workflow_node(1, records=[without_hash, with_hash]),
            ],
        }

        lookup = EmbeddedModelIndex.from_workflow(workflow).lookup(
            node_id="1",
            name="example.safetensors",
            directory="diffusion_models",
        )

        self.assertEqual(lookup.status, "resolved")
        self.assertEqual(lookup.candidate.expected_sha256, "c" * 64)

    def test_conflicting_source_or_hash_is_mapping_required(self):
        conflicts = {
            "source": [
                model_record(),
                model_record(repository="other/public-model"),
            ],
            "hash": [
                model_record(digest="c" * 64),
                model_record(digest="d" * 64),
            ],
        }

        for label, records in conflicts.items():
            with self.subTest(label=label):
                index = EmbeddedModelIndex.from_workflow(
                    {"nodes": [workflow_node(1, records=records)]}
                )
                lookup = index.lookup(
                    node_id="1",
                    name="example.safetensors",
                    directory="diffusion_models",
                )
                self.assertEqual(lookup.status, "mapping_required")
                self.assertIsNone(lookup.candidate)
                self.assertIsNotNone(lookup.reason)

    def test_exact_malformed_records_are_unsupported(self):
        malformed = {
            "http URL": {
                **model_record(),
                "url": "http://huggingface.co/example/model/resolve/main/file",
            },
            "credential URL": {
                **model_record(),
                "url": (
                    "https://user@huggingface.co/example/model/"
                    "resolve/main/file"
                ),
            },
            "half hash": {
                key: value
                for key, value in model_record().items()
                if key != "hash_type"
            },
            "wrong hash type": {
                **model_record(),
                "hash_type": "md5",
            },
            "uppercase hash": {
                **model_record(),
                "hash": "C" * 64,
            },
            "unknown field": {
                **model_record(),
                "token": "forbidden",
            },
            "absolute name": model_record(name="/example.safetensors"),
            "traversal name": model_record(name="../example.safetensors"),
            "bad directory": model_record(directory="../models"),
        }

        for label, record in malformed.items():
            with self.subTest(label=label):
                index = EmbeddedModelIndex.from_workflow(
                    {
                        "nodes": [
                            workflow_node(
                                1,
                                selected=record["name"],
                                records=[record],
                            )
                        ]
                    }
                )
                lookup = index.lookup(
                    node_id="1",
                    name=record["name"],
                    directory=record["directory"],
                )
                self.assertEqual(lookup.status, "unsupported")
                self.assertIsNone(lookup.candidate)
                self.assertIsNotNone(lookup.reason)

    def test_nested_subgraph_identity_is_flattened_for_each_instance(self):
        workflow = {
            "nodes": [
                workflow_node(10, node_type="outer"),
                workflow_node(20, node_type="outer"),
            ],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "outer",
                        "nodes": [workflow_node(7, node_type="inner")],
                    },
                    {
                        "id": "inner",
                        "nodes": [workflow_node(4, records=[model_record()])],
                    },
                ]
            },
        }

        index = EmbeddedModelIndex.from_workflow(workflow)

        for node_id in ("10:7:4", "20:7:4"):
            with self.subTest(node_id=node_id):
                lookup = index.lookup(
                    node_id=node_id,
                    name="example.safetensors",
                    directory="diffusion_models",
                )
                self.assertEqual(lookup.status, "resolved")
                self.assertEqual(lookup.candidate.node_id, node_id)
        self.assertEqual(
            index.lookup(
                node_id="7:4",
                name="example.safetensors",
                directory="diffusion_models",
            ).status,
            "mapping_required",
        )
        self.assertEqual(
            index.lookup(
                node_id="10:4",
                name="example.safetensors",
                directory="diffusion_models",
            ).status,
            "mapping_required",
        )

    def test_per_node_candidate_wins_over_unique_workflow_fallback(self):
        workflow = {
            "models": [model_record(repository="fallback/model")],
            "nodes": [
                workflow_node(
                    1,
                    records=[model_record(repository="node/model")],
                ),
                workflow_node(2, records=None),
            ],
        }

        index = EmbeddedModelIndex.from_workflow(workflow)
        node_lookup = index.lookup(
            node_id="1",
            name="example.safetensors",
            directory="diffusion_models",
        )
        fallback_lookup = index.lookup(
            node_id="2",
            name="example.safetensors",
            directory="diffusion_models",
        )

        self.assertEqual(
            node_lookup.candidate.reference.repository_id,
            "node/model",
        )
        self.assertEqual(
            fallback_lookup.candidate.reference.repository_id,
            "fallback/model",
        )
        self.assertIsNone(fallback_lookup.candidate.node_id)

    def test_other_node_metadata_is_never_a_fallback(self):
        workflow = {
            "nodes": [
                workflow_node(1, records=[model_record()]),
                workflow_node(2, records=None),
            ]
        }
        index = EmbeddedModelIndex.from_workflow(workflow)

        lookup = index.lookup(
            node_id="2",
            name="example.safetensors",
            directory="diffusion_models",
        )

        self.assertEqual(lookup.status, "mapping_required")
        self.assertIsNone(lookup.candidate)

    def test_workflow_fallback_requires_one_normalized_candidate(self):
        cases = {
            "normalized duplicate": (
                [model_record(), model_record(query="?download=true")],
                "resolved",
            ),
            "source conflict": (
                [
                    model_record(),
                    model_record(repository="other/public-model"),
                ],
                "mapping_required",
            ),
            "invalid": (
                [
                    {
                        **model_record(),
                        "url": "https://example.com/model",
                    }
                ],
                "unsupported",
            ),
        }

        for label, (records, status) in cases.items():
            with self.subTest(label=label):
                index = EmbeddedModelIndex.from_workflow(
                    {
                        "models": records,
                        "nodes": [workflow_node(1, records=None)],
                    }
                )
                lookup = index.lookup(
                    node_id="1",
                    name="example.safetensors",
                    directory="diffusion_models",
                )
                self.assertEqual(lookup.status, status)

    def test_inactive_nodes_and_unreferenced_definitions_are_ignored(self):
        invalid_record = {
            **model_record(),
            "url": "https://example.com/not-hugging-face",
        }
        workflow = {
            "nodes": [
                workflow_node(1, records=[invalid_record], mode=2),
                workflow_node(2, records=[invalid_record], mode=4),
                workflow_node(3, records=None),
            ],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "unused",
                        "nodes": [
                            {
                                **workflow_node(1),
                                "properties": {"models": "invalid"},
                            }
                        ],
                    }
                ]
            },
        }

        index = EmbeddedModelIndex.from_workflow(workflow)

        for node_id in ("1", "2", "3"):
            with self.subTest(node_id=node_id):
                self.assertEqual(
                    index.lookup(
                        node_id=node_id,
                        name="example.safetensors",
                        directory="diffusion_models",
                    ).status,
                    "mapping_required",
                )

    def test_malformed_workflow_containers_fail_closed(self):
        malformed = {
            "workflow": None,
            "missing nodes": {},
            "nodes object": {"nodes": {}},
            "node scalar": {"nodes": [None]},
            "definitions list": {"nodes": [], "definitions": []},
            "subgraphs object": {
                "nodes": [],
                "definitions": {"subgraphs": {}},
            },
            "definition scalar": {
                "nodes": [],
                "definitions": {"subgraphs": [None]},
            },
            "duplicate definitions": {
                "nodes": [],
                "definitions": {
                    "subgraphs": [
                        {"id": "same", "nodes": []},
                        {"id": "same", "nodes": []},
                    ]
                },
            },
            "referenced nodes object": {
                "nodes": [workflow_node(1, node_type="bad")],
                "definitions": {
                    "subgraphs": [{"id": "bad", "nodes": {}}]
                },
            },
            "workflow models object": {"nodes": [], "models": {}},
            "active node models object": {
                "nodes": [
                    {
                        **workflow_node(1),
                        "properties": {"models": {}},
                    }
                ]
            },
        }

        for label, workflow in malformed.items():
            with self.subTest(label=label):
                with self.assertRaises(ModelMetadataError):
                    EmbeddedModelIndex.from_workflow(workflow)

    def test_recursive_definition_cycle_fails_closed(self):
        workflow = {
            "nodes": [workflow_node(1, node_type="outer")],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "outer",
                        "nodes": [workflow_node(2, node_type="inner")],
                    },
                    {
                        "id": "inner",
                        "nodes": [workflow_node(3, node_type="outer")],
                    },
                ]
            },
        }

        with self.assertRaises(ModelMetadataError):
            EmbeddedModelIndex.from_workflow(workflow)

    def test_explicit_depth_node_and_record_bounds_fail_closed(self):
        depth_workflow = {
            "nodes": [workflow_node(1, node_type="outer")],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "outer",
                        "nodes": [workflow_node(2, node_type="inner")],
                    },
                    {
                        "id": "inner",
                        "nodes": [workflow_node(3, records=None)],
                    },
                ]
            },
        }
        with patch.object(model_metadata, "MAX_MODEL_METADATA_DEPTH", 1):
            with self.assertRaises(ModelMetadataError):
                EmbeddedModelIndex.from_workflow(depth_workflow)

        with patch.object(model_metadata, "MAX_MODEL_METADATA_NODES", 2):
            with self.assertRaises(ModelMetadataError):
                EmbeddedModelIndex.from_workflow(
                    {
                        "nodes": [
                            workflow_node(1),
                            workflow_node(2),
                            workflow_node(3),
                        ]
                    }
                )

        with patch.object(model_metadata, "MAX_MODEL_METADATA_RECORDS", 1):
            with self.assertRaises(ModelMetadataError):
                EmbeddedModelIndex.from_workflow(
                    {
                        "models": [model_record(), model_record()],
                        "nodes": [],
                    }
                )


if __name__ == "__main__":
    unittest.main()

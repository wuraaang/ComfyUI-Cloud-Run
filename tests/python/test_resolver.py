import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from cloud_run.artifacts import FileInputMetadata, GIB, StaticFileRequirement
from cloud_run.comfy_host import NodeDescription, NodeNotFound
from cloud_run.dependency_repository import DependencyRepository
from cloud_run.manifest import SourceSpec
from cloud_run.model_sources import ModelSourceResolution
from cloud_run.registry import RegistryCandidate
from cloud_run.resolver import DependencyResolver


def complete_candidate(revision="a" * 40):
    return {
        "repository_url": "https://github.com/acme/nodes",
        "revision": revision,
        "package_id": "acme.nodes",
        "archive": {
            "artifact_id": "archive-acme-nodes",
            "destination": "custom_nodes/acme.nodes",
            "locator": "local-upload:archive-acme-nodes",
            "sha256": "b" * 64,
            "size_bytes": 100,
        },
        "wheels": [],
        "provided_class_types": ["ApprovedNode"],
    }


def pending_candidate(revision="a" * 40):
    return {
        "repository_url": "https://github.com/acme/nodes",
        "revision": revision,
        "package_id": "acme.nodes",
    }


class FakeCapture:
    def __init__(self, *class_types, output=None, workflow=None):
        self.executable_class_types = tuple(class_types)
        self.output = output or {}
        self.workflow = workflow or {"nodes": []}


class FakeHost:
    def __init__(self, descriptions):
        self.descriptions = descriptions
        self.compatibility_checks = 0

    def assert_compatible(self):
        self.compatibility_checks += 1

    def describe_node(self, class_type):
        value = self.descriptions.get(class_type)
        if value is None:
            raise NodeNotFound("Node is not installed.")
        return value


class FakeRegistry:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def infer_package(self, class_type):
        self.calls.append(class_type)
        return self.candidates.get(class_type)


class FakeCacheCatalog:
    def __init__(self):
        self.registered = []
        self.external_calls = []

    def cache_configured(self):
        return True

    def register_local_artifact(self, artifact):
        self.registered.append(artifact)


class FakeModelSourceResolver:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def resolve(self, capture, *, requirements):
        self.calls.append((capture, requirements))
        return self.result


def core(class_type):
    return NodeDescription(
        class_type=class_type,
        kind="core",
        status="resolved",
        source_path="/safe/ComfyUI/nodes.py",
        module_name="nodes",
        package_root=None,
        repository_url=None,
        revision=None,
    )


def custom(class_type, *, repository_url=None, revision=None):
    return NodeDescription(
        class_type=class_type,
        kind="custom",
        status="candidate" if repository_url else "mapping_required",
        source_path=f"/safe/ComfyUI/custom_nodes/{class_type}/node.py",
        module_name=f"custom_nodes.{class_type}",
        package_root=f"/safe/ComfyUI/custom_nodes/{class_type}",
        repository_url=repository_url,
        revision=revision,
    )


class DependencyResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository = DependencyRepository(
            Path(self.temporary_directory.name)
            / "private"
            / "sessions.sqlite3"
        )

    def test_resolver_uses_exact_order_and_blocks_unapproved_unknown_nodes(self):
        approved = self.repository.save_candidate(
            "ApprovedNode",
            "manual",
            complete_candidate(),
            approved=False,
        )
        self.repository.approve(
            "ApprovedNode",
            approved.candidate_digest,
        )
        self.repository.save_candidate(
            "AgentNode",
            "agent",
            pending_candidate(revision="c" * 40),
            approved=False,
        )
        self.repository.save_candidate(
            "ManualNode",
            "manual",
            pending_candidate(revision="d" * 40),
            approved=False,
        )
        host = FakeHost(
            {
                "KSampler": core("KSampler"),
                "ApprovedNode": custom("ApprovedNode"),
                "RegistryNode": custom("RegistryNode"),
                "GitNode": custom(
                    "GitNode",
                    repository_url="https://github.com/acme/git-node",
                    revision="b" * 40,
                ),
                "AgentNode": custom("AgentNode"),
                "ManualNode": custom("ManualNode"),
            }
        )
        registry = FakeRegistry(
            {
                "RegistryNode": RegistryCandidate(
                    package_id="acme.registry",
                    repository_url="https://github.com/acme/registry",
                    revision="a" * 40,
                    version="1.0.0",
                )
            }
        )
        resolver = DependencyResolver(
            host=host,
            repository=self.repository,
            registry=registry,
        )

        result = asyncio.run(
            resolver.resolve_nodes(
                FakeCapture(
                    "KSampler",
                    "ApprovedNode",
                    "RegistryNode",
                    "GitNode",
                    "AgentNode",
                    "ManualNode",
                    "MissingNode",
                )
            )
        )

        self.assertEqual(
            [(row.class_type, row.source_kind) for row in result.rows],
            [
                ("KSampler", "core"),
                ("ApprovedNode", "approved"),
                ("RegistryNode", "registry"),
                ("GitNode", "installed_git"),
                ("AgentNode", "agent"),
                ("ManualNode", "manual"),
                ("MissingNode", None),
            ],
        )
        self.assertEqual(result.rows[0].status, "resolved")
        self.assertEqual(result.rows[1].status, "resolved")
        self.assertTrue(
            all(row.status == "mapping_required" for row in result.rows[2:])
        )
        self.assertFalse(result.rentable)
        self.assertEqual(host.compatibility_checks, 1)

    def test_registry_precedes_installed_git_agent_and_manual_for_same_node(self):
        for source_kind, revision in (
            ("agent", "c" * 40),
            ("manual", "d" * 40),
        ):
            self.repository.save_candidate(
                "PriorityNode",
                source_kind,
                pending_candidate(revision=revision),
                approved=False,
            )
        resolver = DependencyResolver(
            host=FakeHost(
                {
                    "PriorityNode": custom(
                        "PriorityNode",
                        repository_url="https://github.com/acme/installed",
                        revision="b" * 40,
                    )
                }
            ),
            repository=self.repository,
            registry=FakeRegistry(
                {
                    "PriorityNode": RegistryCandidate(
                        "acme.registry",
                        "https://github.com/acme/registry",
                        "a" * 40,
                        "1.0.0",
                    )
                }
            ),
        )

        result = asyncio.run(
            resolver.resolve_nodes(FakeCapture("PriorityNode"))
        )

        self.assertEqual(result.rows[0].source_kind, "registry")

    def test_agent_suggestion_is_metadata_only_and_never_approved(self):
        resolver = DependencyResolver(
            host=FakeHost({}),
            repository=self.repository,
            registry=FakeRegistry({}),
        )

        candidate = resolver.register_agent_suggestion(
            {
                "class_type": "AgentNode",
                "candidate": pending_candidate(),
            }
        )

        self.assertEqual(candidate.source_kind, "agent")
        self.assertFalse(candidate.approved)
        self.assertIsNone(self.repository.approved("AgentNode"))

    def test_dependency_preflight_assembles_verified_artifacts_and_disk(self):
        approved = self.repository.save_candidate(
            "ApprovedNode",
            "manual",
            complete_candidate(),
            approved=False,
        )
        self.repository.approve(
            "ApprovedNode",
            approved.candidate_digest,
        )
        model_root = Path(self.temporary_directory.name) / "models"
        input_root = Path(self.temporary_directory.name) / "input"
        model_root.mkdir()
        input_root.mkdir()
        model = model_root / "upscaler.pth"
        model.write_bytes(b"model")
        model_digest = hashlib.sha256(b"model").hexdigest()
        capture = FakeCapture(
            "ApprovedNode",
            "EmptyLatentImage",
            "SaveImage",
            output={
                "1": {
                    "class_type": "ApprovedNode",
                    "inputs": {"model_name": "upscaler.pth"},
                },
                "2": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {
                        "width": 512,
                        "height": 512,
                        "batch_size": 1,
                    },
                },
                "3": {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["2", 0]},
                },
            },
        )
        cache_catalog = FakeCacheCatalog()
        resolution_context = {
            "metadata": {
                "ApprovedNode": {
                    "model_name": FileInputMetadata(
                        kind="model",
                        category="upscale_models",
                    )
                }
            },
            "model_roots": {"upscale_models": (model_root,)},
            "input_root": input_root,
            "source_mappings": {
                model_digest: SourceSpec(
                    "local-upload",
                    "local-upload:approved-model",
                )
            },
            "base_bytes": 40 * GIB,
        }
        resolver = DependencyResolver(
            host=FakeHost(
                {
                    "ApprovedNode": custom("ApprovedNode"),
                    "EmptyLatentImage": core("EmptyLatentImage"),
                    "SaveImage": core("SaveImage"),
                }
            ),
            repository=self.repository,
            registry=FakeRegistry({}),
            cache_catalog=cache_catalog,
            resolution_context=resolution_context,
        )

        result = asyncio.run(
            resolver.resolve_preflight(
                capture,
                explicit_output_allowance_bytes=None,
            )
        )

        self.assertTrue(result.rentable)
        self.assertEqual(result.disk_gb, 80)
        self.assertEqual(len(result.custom_nodes), 1)
        self.assertEqual(result.custom_nodes[0].package_id, "acme.nodes")
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].sha256, model_digest)
        self.assertEqual(result.artifacts[0].source.kind, "local-upload")
        self.assertTrue(result.artifact_rows[0].cache_available)
        self.assertEqual(
            cache_catalog.registered[0].artifact_id,
            result.artifacts[0].artifact_id,
        )
        self.assertEqual(cache_catalog.external_calls, [])

    def test_model_sources_resolve_before_artifact_assembly(self):
        model_root = Path(self.temporary_directory.name) / "empty-models"
        input_root = Path(self.temporary_directory.name) / "input"
        model_root.mkdir()
        input_root.mkdir()
        digest = "c" * 64
        revision = "a" * 40
        capture = FakeCapture(
            "UNETLoader",
            output={
                "1": {
                    "class_type": "UNETLoader",
                    "inputs": {"model_name": "example.safetensors"},
                }
            },
        )
        metadata = {
            "UNETLoader": {
                "model_name": FileInputMetadata(
                    kind="model",
                    category="diffusion_models",
                )
            }
        }
        model_source_resolver = FakeModelSourceResolver(
            {
                ("1", "model_name"): ModelSourceResolution(
                    status="resolved",
                    source=SourceSpec(
                        kind="huggingface",
                        locator=(
                            "https://huggingface.co/example/public-model/"
                            "resolve/"
                            + revision
                            + "/files/example.safetensors"
                        ),
                        immutable_revision=revision,
                    ),
                    size_bytes=4096,
                    sha256=digest,
                    reason=None,
                )
            }
        )
        resolver = DependencyResolver(
            host=FakeHost({"UNETLoader": core("UNETLoader")}),
            repository=self.repository,
            registry=FakeRegistry({}),
            model_source_resolver=model_source_resolver,
        )

        result = asyncio.run(
            resolver.resolve_dependencies(
                capture,
                metadata=metadata,
                model_roots={"diffusion_models": (model_root,)},
                input_root=input_root,
                source_mappings={},
                base_bytes=40 * GIB,
                explicit_output_allowance_bytes=1024,
            )
        )

        self.assertTrue(result.rentable)
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].source.kind, "huggingface")
        self.assertEqual(len(model_source_resolver.calls), 1)
        called_capture, requirements = model_source_resolver.calls[0]
        self.assertIs(called_capture, capture)
        self.assertEqual(
            requirements,
            (
                StaticFileRequirement(
                    node_id="1",
                    class_type="UNETLoader",
                    input_name="model_name",
                    metadata=metadata["UNETLoader"]["model_name"],
                    value="example.safetensors",
                ),
            ),
        )


if __name__ == "__main__":
    unittest.main()

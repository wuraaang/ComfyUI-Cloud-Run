import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

from cloud_run.artifacts import (
    GIB,
    ArtifactCollisionError,
    ArtifactPathError,
    FileInputMetadata,
    OutputAllowanceRequired,
    StaticFileRequirement,
    UnpinnedRequirementsError,
    build_custom_node_dependency,
    build_package_archive,
    calculate_disk_gb,
    estimate_output_bytes,
    hash_file,
    reject_destination_collisions,
    resolve_artifacts,
    static_file_requirements,
)
from cloud_run.manifest import ArtifactSpec, SourceSpec
from cloud_run.model_sources import ModelSourceResolution


class FakeCapture:
    def __init__(self, output):
        self.output = output


def local_source(artifact_id):
    return SourceSpec(
        kind="local-upload",
        locator="local-upload:" + artifact_id,
    )


def artifact(destination, digest):
    return ArtifactSpec(
        artifact_id="artifact-" + digest[:12],
        kind="model",
        logical_name="fixture",
        destination=destination,
        size_bytes=1,
        sha256=digest,
        source=local_source("artifact-" + digest[:12]),
    )


class StaticFileRequirementTests(unittest.TestCase):
    def test_mapping_preserves_prompt_and_metadata_order_for_every_value(self):
        model_rule = FileInputMetadata(
            kind="model",
            category="diffusion_models",
        )
        input_rule = FileInputMetadata(kind="input")
        capture = FakeCapture(
            {
                "20": {
                    "class_type": "First",
                    "inputs": {
                        "model": "example.safetensors",
                        "linked": ["10", 0],
                    },
                },
                "10": {
                    "class_type": "Second",
                    "inputs": {"number": 7},
                },
            }
        )
        metadata = {
            "First": {
                "missing": input_rule,
                "model": model_rule,
                "linked": input_rule,
            },
            "Second": {
                "number": input_rule,
                "absent_model": model_rule,
            },
        }

        requirements = static_file_requirements(capture, metadata)

        self.assertEqual(
            requirements,
            (
                StaticFileRequirement(
                    node_id="20",
                    class_type="First",
                    input_name="missing",
                    metadata=input_rule,
                    value=None,
                ),
                StaticFileRequirement(
                    node_id="20",
                    class_type="First",
                    input_name="model",
                    metadata=model_rule,
                    value="example.safetensors",
                ),
                StaticFileRequirement(
                    node_id="20",
                    class_type="First",
                    input_name="linked",
                    metadata=input_rule,
                    value=["10", 0],
                ),
                StaticFileRequirement(
                    node_id="10",
                    class_type="Second",
                    input_name="number",
                    metadata=input_rule,
                    value=7,
                ),
                StaticFileRequirement(
                    node_id="10",
                    class_type="Second",
                    input_name="absent_model",
                    metadata=model_rule,
                    value=None,
                ),
            ),
        )

    def test_callable_and_host_object_metadata_use_the_same_contract(self):
        rule = FileInputMetadata(kind="input")
        capture = FakeCapture(
            {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "source.png"},
                }
            }
        )
        calls = []

        def callable_metadata(class_type):
            calls.append(("callable", class_type))
            return {"image": rule}

        class HostMetadata:
            def file_input_metadata(self, class_type):
                calls.append(("host", class_type))
                return {"image": rule}

        callable_result = static_file_requirements(
            capture,
            callable_metadata,
        )
        host_result = static_file_requirements(capture, HostMetadata())

        self.assertEqual(callable_result, host_result)
        self.assertEqual(
            calls,
            [("callable", "LoadImage"), ("host", "LoadImage")],
        )

    def test_missing_class_metadata_is_empty_and_invalid_metadata_fails(self):
        capture = FakeCapture(
            {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "source.png"},
                }
            }
        )
        self.assertEqual(static_file_requirements(capture, {}), ())

        invalid_metadata = (
            {"LoadImage": []},
            {"LoadImage": {1: FileInputMetadata(kind="input")}},
            {"LoadImage": {"image": object()}},
        )
        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                with self.assertRaisesRegex(
                    ValueError,
                    "File-input metadata is invalid",
                ):
                    static_file_requirements(capture, metadata)


class ArtifactResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.input_root = self.root / "input"
        self.input_root.mkdir()
        self.model_roots = {}
        for category in (
            "diffusion_models",
            "text_encoders",
            "vae",
            "upscale_models",
        ):
            root = self.root / category
            root.mkdir()
            self.model_roots[category] = (root,)

    @staticmethod
    def verified_source(
        digest,
        size_bytes,
        *,
        repository="example/public-model",
        file_path="files/example.safetensors",
    ):
        revision = "a" * 40
        return ModelSourceResolution(
            status="resolved",
            source=SourceSpec(
                kind="huggingface",
                locator=(
                    "https://huggingface.co/"
                    + repository
                    + "/resolve/"
                    + revision
                    + "/"
                    + file_path
                ),
                immutable_revision=revision,
            ),
            size_bytes=size_bytes,
            sha256=digest,
            reason=None,
        )

    @staticmethod
    def one_model_capture(name="example.safetensors"):
        return FakeCapture(
            {
                "1": {
                    "class_type": "UNETLoader",
                    "inputs": {"model_name": name},
                }
            }
        )

    @staticmethod
    def one_model_metadata(category="diffusion_models"):
        return {
            "UNETLoader": {
                "model_name": FileInputMetadata(
                    kind="model",
                    category=category,
                )
            }
        }

    def test_optional_source_first_arguments_preserve_legacy_resolution(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_root = root / "models"
            input_root = root / "input"
            model_root.mkdir()
            input_root.mkdir()
            model = model_root / "example.safetensors"
            model.write_bytes(b"legacy model")
            digest = hash_file(model).sha256
            capture = FakeCapture(
                {
                    "1": {
                        "class_type": "UNETLoader",
                        "inputs": {"model_name": "example.safetensors"},
                    }
                }
            )
            metadata = {
                "UNETLoader": {
                    "model_name": FileInputMetadata(
                        kind="model",
                        category="diffusion_models",
                    )
                }
            }
            requirements = static_file_requirements(capture, metadata)

            result = resolve_artifacts(
                capture,
                metadata=metadata,
                model_roots={"diffusion_models": (model_root,)},
                input_root=input_root,
                source_mappings={digest: local_source("approved-model")},
                model_sources={},
                requirements=requirements,
            )

        self.assertEqual(result.rows[0].status, "resolved")
        self.assertEqual(len(result.artifacts), 1)

    def test_verified_model_absent_locally_resolves_to_exact_destination(self):
        digest = "c" * 64
        capture = self.one_model_capture()
        metadata = self.one_model_metadata()

        result = resolve_artifacts(
            capture,
            metadata=metadata,
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings={},
            model_sources={
                ("1", "model_name"): self.verified_source(digest, 4096)
            },
        )

        self.assertEqual(
            (
                result.rows[0].status,
                result.rows[0].destination,
                result.rows[0].size_bytes,
                result.rows[0].sha256,
            ),
            (
                "resolved",
                "models/diffusion_models/example.safetensors",
                4096,
                digest,
            ),
        )
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].source.kind, "huggingface")
        self.assertEqual(result.local_artifacts, ())
        self.assertTrue(result.rentable)

    def test_verified_model_does_not_require_an_existing_local_category_root(self):
        digest = "d" * 64

        result = resolve_artifacts(
            self.one_model_capture(),
            metadata=self.one_model_metadata(),
            model_roots={
                "diffusion_models": (self.root / "missing-model-root",),
            },
            input_root=self.input_root,
            source_mappings={},
            model_sources={
                ("1", "model_name"): self.verified_source(digest, 8192)
            },
        )

        self.assertTrue(result.rentable)
        self.assertEqual(result.rows[0].status, "resolved")
        self.assertEqual(result.artifacts[0].source.kind, "huggingface")

    def test_matching_local_copy_and_verified_source_produce_one_artifact(self):
        model = self.model_roots["diffusion_models"][0] / "example.safetensors"
        model.write_bytes(b"matching model")
        file_digest = hash_file(model)

        result = resolve_artifacts(
            self.one_model_capture(),
            metadata=self.one_model_metadata(),
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings={
                file_digest.sha256: local_source("legacy-approved")
            },
            model_sources={
                ("1", "model_name"): self.verified_source(
                    file_digest.sha256,
                    file_digest.size_bytes,
                )
            },
        )

        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].source.kind, "huggingface")
        self.assertEqual(len(result.local_artifacts), 1)
        self.assertEqual(
            result.local_artifacts[0].sha256,
            file_digest.sha256,
        )

    def test_local_size_or_digest_collision_blocks_verified_source(self):
        model = self.model_roots["diffusion_models"][0] / "example.safetensors"
        model.write_bytes(b"local collision")
        file_digest = hash_file(model)
        conflicts = {
            "size": self.verified_source(
                file_digest.sha256,
                file_digest.size_bytes + 1,
            ),
            "digest": self.verified_source(
                "d" * 64,
                file_digest.size_bytes,
            ),
        }

        for label, source in conflicts.items():
            with self.subTest(label=label):
                result = resolve_artifacts(
                    self.one_model_capture(),
                    metadata=self.one_model_metadata(),
                    model_roots=self.model_roots,
                    input_root=self.input_root,
                    source_mappings={
                        file_digest.sha256: local_source("legacy-approved")
                    },
                    model_sources={("1", "model_name"): source},
                )
                self.assertEqual(result.rows[0].status, "unsupported")
                self.assertEqual(result.artifacts, ())
                self.assertFalse(result.rentable)

    def test_equal_destination_and_digest_deduplicate_but_conflicts_raise(self):
        capture = FakeCapture(
            {
                str(node_id): {
                    "class_type": "UNETLoader",
                    "inputs": {"model_name": "example.safetensors"},
                }
                for node_id in (1, 2)
            }
        )
        metadata = self.one_model_metadata()
        shared = self.verified_source("c" * 64, 4096)

        deduplicated = resolve_artifacts(
            capture,
            metadata=metadata,
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings={},
            model_sources={
                ("1", "model_name"): shared,
                ("2", "model_name"): shared,
            },
        )

        self.assertEqual([row.status for row in deduplicated.rows], ["resolved"] * 2)
        self.assertEqual(len(deduplicated.artifacts), 1)

        with self.assertRaises(ArtifactCollisionError):
            resolve_artifacts(
                capture,
                metadata=metadata,
                model_roots=self.model_roots,
                input_root=self.input_root,
                source_mappings={},
                model_sources={
                    ("1", "model_name"): shared,
                    ("2", "model_name"): self.verified_source(
                        "d" * 64,
                        4096,
                    ),
                },
            )

    def test_all_supported_model_categories_keep_exact_destinations(self):
        categories = (
            "diffusion_models",
            "text_encoders",
            "vae",
            "upscale_models",
        )
        output = {}
        metadata_rules = {}
        model_sources = {}
        for offset, category in enumerate(categories, start=1):
            input_name = "model_" + str(offset)
            filename = category + ".bin"
            digest = format(offset, "x") * 64
            output[str(offset)] = {
                "class_type": "MultiLoader",
                "inputs": {input_name: filename},
            }
            metadata_rules[input_name] = FileInputMetadata(
                kind="model",
                category=category,
            )
            model_sources[(str(offset), input_name)] = self.verified_source(
                digest,
                1000 + offset,
                file_path="files/" + filename,
            )

        result = resolve_artifacts(
            FakeCapture(output),
            metadata={"MultiLoader": metadata_rules},
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings={},
            model_sources=model_sources,
        )

        self.assertEqual(
            [row.destination for row in result.rows],
            [
                "models/" + category + "/" + category + ".bin"
                for category in categories
            ],
        )

    def test_present_invalid_or_conflicting_annotation_cannot_use_local_mapping(self):
        model = self.model_roots["diffusion_models"][0] / "example.safetensors"
        model.write_bytes(b"approved local model")
        file_digest = hash_file(model)
        source_mappings = {
            file_digest.sha256: local_source("legacy-approved")
        }
        blockers = {
            "invalid": ModelSourceResolution(
                "unsupported",
                None,
                None,
                None,
                "Native model metadata is invalid.",
            ),
            "conflicting": ModelSourceResolution(
                "mapping_required",
                None,
                None,
                None,
                "Native model metadata is missing or ambiguous.",
            ),
        }

        legacy = resolve_artifacts(
            self.one_model_capture(),
            metadata=self.one_model_metadata(),
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings=source_mappings,
            model_sources={},
        )
        self.assertEqual(legacy.rows[0].status, "resolved")

        for label, blocker in blockers.items():
            with self.subTest(label=label):
                blocked = resolve_artifacts(
                    self.one_model_capture(),
                    metadata=self.one_model_metadata(),
                    model_roots=self.model_roots,
                    input_root=self.input_root,
                    source_mappings=source_mappings,
                    model_sources={("1", "model_name"): blocker},
                )
                self.assertEqual(blocked.rows[0].status, blocker.status)
                self.assertEqual(blocked.artifacts, ())

    def test_input_resolution_is_unchanged_and_missing_input_stays_unsupported(self):
        existing = self.input_root / "source.png"
        existing.write_bytes(b"private image")
        digest = hash_file(existing).sha256
        metadata = {
            "LoadImage": {"image": FileInputMetadata(kind="input")}
        }
        existing_capture = FakeCapture(
            {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "source.png"},
                }
            }
        )
        source_mappings = {digest: local_source("approved-input")}

        legacy = resolve_artifacts(
            existing_capture,
            metadata=metadata,
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings=source_mappings,
        )
        extended = resolve_artifacts(
            existing_capture,
            metadata=metadata,
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings=source_mappings,
            model_sources={
                ("unrelated", "model"): self.verified_source("e" * 64, 1)
            },
            requirements=static_file_requirements(existing_capture, metadata),
        )
        self.assertEqual(extended, legacy)

        missing = resolve_artifacts(
            FakeCapture(
                {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "missing.png"},
                    }
                }
            ),
            metadata=metadata,
            model_roots=self.model_roots,
            input_root=self.input_root,
            source_mappings={},
            model_sources={},
        )
        self.assertEqual(missing.rows[0].status, "unsupported")
        self.assertEqual(missing.artifacts, ())


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_stream_hash_archive_and_disk_are_deterministic(self):
        fixture = self.root / "fixture.bin"
        fixture.write_bytes(b"deterministic fixture")
        digest = hash_file(fixture, allowed_root=self.root)

        self.assertEqual(digest.size_bytes, len(fixture.read_bytes()))
        self.assertEqual(
            digest.sha256,
            hashlib.sha256(fixture.read_bytes()).hexdigest(),
        )

        package = self.root / "package"
        package.mkdir()
        (package / "node.py").write_text("NODE = True\n", encoding="utf-8")
        executable = package / "launch"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        (package / ".git").mkdir()
        (package / ".git" / "config").write_text(
            "ignored metadata",
            encoding="utf-8",
        )
        (package / "__pycache__").mkdir()
        (package / "__pycache__" / "node.pyc").write_bytes(b"ignored")
        out = self.root / "out"
        out.mkdir()

        first = build_package_archive(package, out / "first.tar")
        second = build_package_archive(package, out / "second.tar")

        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.size_bytes, second.size_bytes)
        with tarfile.open(out / "first.tar", "r:") as archive:
            members = archive.getmembers()
        self.assertEqual(
            [member.name for member in members],
            ["launch", "node.py"],
        )
        self.assertTrue(
            all(
                member.uid == member.gid == member.mtime == 0
                and member.uname == member.gname == ""
                for member in members
            )
        )
        self.assertEqual(members[0].mode, 0o755)
        self.assertEqual(members[1].mode, 0o644)

        self.assertEqual(
            calculate_disk_gb(
                base_bytes=40 * GIB,
                dependency_bytes=10 * GIB,
                input_bytes=2 * GIB,
                output_bytes=3 * GIB,
            ),
            80,
        )
        self.assertEqual(
            calculate_disk_gb(
                base_bytes=60 * GIB,
                dependency_bytes=20 * GIB,
                input_bytes=10 * GIB,
                output_bytes=5 * GIB,
            ),
            115,
        )

    def test_paths_symlinks_and_destination_collisions_fail_closed(self):
        outside = self.root / "outside.bin"
        outside.write_bytes(b"x")
        allowed = self.root / "allowed"
        allowed.mkdir()
        link = allowed / "escape.bin"
        link.symlink_to(outside)

        with self.assertRaises(ArtifactPathError):
            hash_file(link, allowed_root=allowed)
        with self.assertRaises(ArtifactPathError):
            build_package_archive(allowed, self.root / "escape.tar")
        with self.assertRaises(ArtifactCollisionError):
            reject_destination_collisions(
                [
                    artifact(
                        "models/checkpoints/a.safetensors",
                        "a" * 64,
                    ),
                    artifact(
                        "models/checkpoints/a.safetensors",
                        "b" * 64,
                    ),
                ]
            )

    def test_resolve_models_and_inputs_uses_metadata_hash_and_approved_source(self):
        model_root = self.root / "models"
        input_root = self.root / "input"
        model_root.mkdir()
        input_root.mkdir()
        model = model_root / "upscaler.pth"
        media = input_root / "source.jpg"
        model.write_bytes(b"model bytes")
        media.write_bytes(b"image bytes")
        model_digest = hash_file(model).sha256
        capture = FakeCapture(
            {
                "1": {
                    "class_type": "Upscale",
                    "inputs": {
                        "model_name": "upscaler.pth",
                        "strength": 0.5,
                    },
                },
                "2": {
                    "class_type": "LoadImage",
                    "inputs": {
                        "image": "source.jpg",
                        "ordinary_text": "not-a-file.txt",
                    },
                },
            }
        )
        metadata = {
            "Upscale": {
                "model_name": FileInputMetadata(
                    kind="model",
                    category="upscale_models",
                )
            },
            "LoadImage": {
                "image": FileInputMetadata(kind="input"),
            },
        }

        result = resolve_artifacts(
            capture,
            metadata=metadata,
            model_roots={"upscale_models": (model_root,)},
            input_root=input_root,
            source_mappings={
                model_digest: local_source("approved-model"),
            },
        )

        self.assertEqual(
            [
                (row.input_name, row.status, row.destination)
                for row in result.rows
            ],
            [
                (
                    "model_name",
                    "resolved",
                    "models/upscale_models/upscaler.pth",
                ),
                ("image", "resolved", "input/source.jpg"),
            ],
        )
        self.assertEqual(len(result.artifacts), 2)
        resolved_model = next(
            item for item in result.artifacts if item.kind == "model"
        )
        self.assertEqual(resolved_model.sha256, model_digest)
        self.assertTrue(result.rentable)

    def test_existing_local_input_uses_its_verified_local_upload_source(self):
        input_root = self.root / "input"
        input_root.mkdir()
        media = input_root / "source.png"
        media.write_bytes(b"synthetic image bytes")
        capture = FakeCapture(
            {
                "1": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "source.png"},
                }
            }
        )

        result = resolve_artifacts(
            capture,
            metadata={
                "LoadImage": {
                    "image": FileInputMetadata(kind="input"),
                }
            },
            model_roots={},
            input_root=input_root,
            source_mappings={},
        )

        self.assertTrue(result.rentable)
        self.assertEqual(len(result.artifacts), 1)
        resolved = result.artifacts[0]
        self.assertEqual(resolved.kind, "input")
        self.assertEqual(resolved.source.kind, "local-upload")
        self.assertEqual(
            resolved.source.locator,
            "local-upload:" + resolved.artifact_id,
        )
        self.assertEqual(result.rows[0].status, "resolved")

    def test_ambiguous_or_escaping_file_metadata_is_unsupported(self):
        first = self.root / "first"
        second = self.root / "second"
        input_root = self.root / "input"
        first.mkdir()
        second.mkdir()
        input_root.mkdir()
        (first / "same.bin").write_bytes(b"a")
        (second / "same.bin").write_bytes(b"b")
        capture = FakeCapture(
            {
                "1": {
                    "class_type": "ModelNode",
                    "inputs": {"model": "same.bin"},
                },
                "2": {
                    "class_type": "InputNode",
                    "inputs": {"file": "../outside.bin"},
                },
            }
        )

        result = resolve_artifacts(
            capture,
            metadata={
                "ModelNode": {
                    "model": FileInputMetadata(
                        kind="model",
                        category="checkpoints",
                    )
                },
                "InputNode": {"file": FileInputMetadata(kind="input")},
            },
            model_roots={"checkpoints": (first, second)},
            input_root=input_root,
            source_mappings={},
        )

        self.assertEqual(
            [row.status for row in result.rows],
            ["unsupported", "unsupported"],
        )
        self.assertEqual(result.artifacts, ())
        self.assertFalse(result.rentable)

    def test_output_estimate_uses_upstream_shape_or_requires_allowance(self):
        known = FakeCapture(
            {
                "1": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {
                        "width": 1024,
                        "height": 512,
                        "batch_size": 2,
                    },
                },
                "2": {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["1", 0]},
                },
            }
        )
        unknown = FakeCapture(
            {
                "1": {
                    "class_type": "VHS_VideoCombine",
                    "inputs": {"images": ["dynamic", 0]},
                }
            }
        )

        self.assertEqual(
            estimate_output_bytes(known, explicit_bytes=None),
            1024 * 512 * 2 * 4 * 4 * 2,
        )
        with self.assertRaises(OutputAllowanceRequired):
            estimate_output_bytes(unknown, explicit_bytes=None)
        self.assertEqual(
            estimate_output_bytes(unknown, explicit_bytes=123456),
            123456,
        )

    def test_custom_node_archive_requires_resolved_compatible_wheels(self):
        package = self.root / "package"
        package.mkdir()
        (package / "node.py").write_text("NODE = True\n", encoding="utf-8")
        wheel = self.root / "dependency-1.0-py3-none-any.whl"
        wheel.write_bytes(b"wheel")

        node = build_custom_node_dependency(
            package_id="acme.nodes",
            repository_url="https://github.com/acme/nodes",
            revision="a" * 40,
            provided_class_types=("AcmeNode",),
            package_root=package,
            archive_path=self.root / "acme-nodes.tar",
            wheel_paths=(wheel,),
        )

        self.assertGreater(node.archive.size_bytes, 0)
        self.assertEqual(
            node.archive.sha256,
            hash_file(self.root / "acme-nodes.tar").sha256,
        )
        self.assertEqual(
            [item.filename for item in node.wheels],
            ["dependency-1.0-py3-none-any.whl"],
        )
        (package / "requirements.txt").write_text(
            "floating-dependency>=1\n",
            encoding="utf-8",
        )
        with self.assertRaises(UnpinnedRequirementsError):
            build_custom_node_dependency(
                package_id="acme.nodes",
                repository_url="https://github.com/acme/nodes",
                revision="a" * 40,
                provided_class_types=("AcmeNode",),
                package_root=package,
                archive_path=self.root / "rejected.tar",
                wheel_paths=(wheel,),
            )
        incompatible = self.root / "dependency-1.0-cp312-cp312-linux_x86_64.whl"
        incompatible.write_bytes(b"wheel")
        (package / "requirements.txt").unlink()
        with self.assertRaises(UnpinnedRequirementsError):
            build_custom_node_dependency(
                package_id="acme.nodes",
                repository_url="https://github.com/acme/nodes",
                revision="a" * 40,
                provided_class_types=("AcmeNode",),
                package_root=package,
                archive_path=self.root / "rejected-abi.tar",
                wheel_paths=(incompatible,),
            )


if __name__ == "__main__":
    unittest.main()

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
    UnpinnedRequirementsError,
    build_custom_node_dependency,
    build_package_archive,
    calculate_disk_gb,
    estimate_output_bytes,
    hash_file,
    reject_destination_collisions,
    resolve_artifacts,
)
from cloud_run.manifest import ArtifactSpec, SourceSpec


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
                ("image", "mapping_required", "input/source.jpg"),
            ],
        )
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(result.artifacts[0].sha256, model_digest)
        self.assertFalse(result.rentable)

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

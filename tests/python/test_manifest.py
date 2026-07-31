import hashlib
import unittest

from cloud_run.manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    DependencyManifest,
    ManifestDelta,
    ManifestValidationError,
    PythonWheelSpec,
    SourceSpec,
    validate_dependency,
)


def source(kind="local-upload", *, locator="local-upload:artifact-1", revision=None):
    return SourceSpec(
        kind=kind,
        locator=locator,
        immutable_revision=revision,
    )


def artifact(
    kind="model",
    destination="models/upscale/a.safetensors",
    size=12,
    digest="a" * 64,
    *,
    artifact_id=None,
    artifact_source=None,
):
    return ArtifactSpec(
        artifact_id=artifact_id or kind + "-a",
        kind=kind,
        logical_name=destination.rsplit("/", 1)[-1],
        destination=destination,
        size_bytes=size,
        sha256=digest,
        source=artifact_source or source(locator="local-upload:" + (artifact_id or kind + "-a")),
    )


def custom_node(
    *,
    revision="b" * 40,
    archive_digest="c" * 64,
    class_types=("FancyNode",),
):
    return CustomNodeSpec(
        package_id="acme.fancy",
        repository_url="https://github.com/acme/fancy",
        revision=revision,
        archive=artifact(
            "custom_node_archive",
            "custom_nodes/acme.fancy",
            20,
            archive_digest,
            artifact_id="custom-acme-fancy",
        ),
        wheels=(
            PythonWheelSpec(
                filename="dependency-1.0-py3-none-any.whl",
                size_bytes=5,
                sha256="d" * 64,
                source=source(
                    locator="local-upload:wheel-dependency-1",
                ),
            ),
        ),
        provided_class_types=tuple(class_types),
    )


def dependency_manifest(*, artifacts=(), custom_nodes=()):
    return DependencyManifest(
        schema_version=1,
        protocol_version="1",
        comfyui_core_version="0.29.0",
        comfyui_frontend_version="1.47.10",
        worker_version="worker-1",
        prompt_digest="e" * 64,
        custom_nodes=tuple(custom_nodes),
        artifacts=tuple(artifacts),
        output_allowance_bytes=1024,
        disk_gb=80,
    )


class DependencyManifestTests(unittest.TestCase):
    def test_manifest_is_canonical_content_addressed_and_browser_safe(self):
        manifest = dependency_manifest(
            artifacts=(
                artifact(
                    "model",
                    "models/upscale/a.safetensors",
                    12,
                    "a" * 64,
                    artifact_id="model-a",
                ),
                artifact(
                    "input",
                    "input/source.jpg",
                    7,
                    "b" * 64,
                    artifact_id="input-source",
                ),
            ),
        )
        reversed_manifest = dependency_manifest(
            artifacts=tuple(reversed(manifest.artifacts)),
        )

        encoded = manifest.canonical_bytes()

        self.assertEqual(
            manifest.digest,
            hashlib.sha256(encoded).hexdigest(),
        )
        self.assertEqual(manifest.digest, reversed_manifest.digest)
        self.assertNotIn(b"/Users/", encoded)
        self.assertNotIn(b"token", encoded.lower())

    def test_manifest_rejects_mutable_or_unbounded_dependencies(self):
        invalid = (
            SourceSpec(
                kind="git",
                locator="https://github.com/acme/nodes",
                immutable_revision="main",
            ),
            artifact("model", "../escape", 1, "a" * 64),
            artifact("model", "models/a", 0, "a" * 64),
            artifact("model", "models/a", 1, "not-a-sha"),
            SourceSpec(
                kind="http",
                locator="http://example.com/a",
                immutable_revision="a" * 40,
            ),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ManifestValidationError):
                    validate_dependency(value)

    def test_source_contracts_accept_only_immutable_approved_origins(self):
        approved = (
            SourceSpec(
                "git",
                "https://github.com/acme/nodes",
                "a" * 40,
            ),
            SourceSpec(
                "huggingface",
                "https://huggingface.co/acme/model/resolve/"
                + "b" * 40
                + "/model.safetensors",
                "b" * 40,
            ),
            SourceSpec(
                "civitai",
                "https://civitai.com/api/download/models/12345",
                "12345",
            ),
            SourceSpec(
                "r2",
                "r2://cloud-run/sha256/aa/" + "a" * 64,
                None,
                "r2-account",
            ),
            SourceSpec(
                "local-upload",
                "local-upload:input-source",
            ),
        )
        for value in approved:
            with self.subTest(value=value):
                self.assertIs(validate_dependency(value), value)

    def test_delta_rejects_changed_installed_identity_and_selects_only_new_items(self):
        installed = dependency_manifest(
            custom_nodes=(custom_node(),),
            artifacts=(
                artifact(
                    "model",
                    "models/checkpoints/a.safetensors",
                    digest="a" * 64,
                    artifact_id="model-a",
                ),
            ),
        )
        compatible = dependency_manifest(
            custom_nodes=(custom_node(),),
            artifacts=(
                *installed.artifacts,
                artifact(
                    "input",
                    "input/new.jpg",
                    digest="f" * 64,
                    artifact_id="input-new",
                ),
            ),
        )
        delta = ManifestDelta.between(installed, compatible)
        self.assertTrue(delta.compatible)
        self.assertEqual(
            tuple(item.artifact_id for item in delta.artifacts),
            ("input-new",),
        )
        self.assertEqual(delta.custom_nodes, ())

        for changed in (
            dependency_manifest(
                custom_nodes=(custom_node(revision="f" * 40),),
                artifacts=installed.artifacts,
            ),
            dependency_manifest(
                custom_nodes=(custom_node(),),
                artifacts=(
                    artifact(
                        "model",
                        "models/checkpoints/a.safetensors",
                        digest="f" * 64,
                        artifact_id="model-a",
                    ),
                ),
            ),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(
                    ManifestDelta.between(installed, changed).compatible
                )

    def test_canonical_order_does_not_create_a_false_incompatible_delta(self):
        installed = dependency_manifest(
            custom_nodes=(
                custom_node(class_types=("FancyNode", "HelperNode")),
            )
        )
        reordered = dependency_manifest(
            custom_nodes=(
                custom_node(class_types=("HelperNode", "FancyNode")),
            )
        )

        self.assertEqual(installed.digest, reordered.digest)
        self.assertTrue(ManifestDelta.between(installed, reordered).compatible)


if __name__ == "__main__":
    unittest.main()

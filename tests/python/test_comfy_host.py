import types
import unittest
from pathlib import Path

from cloud_run.artifacts import FileInputMetadata
from cloud_run.comfy_host import (
    ComfyHost,
    HostCompatibilityError,
    NodeRecord,
)


class RecordingGitRunner:
    def __init__(self, *, dirty=False, top_level=None):
        self.calls = []
        self.dirty = dirty
        self.top_level = top_level

    def __call__(self, argv):
        self.calls.append(tuple(argv))
        command = tuple(argv[3:])
        if command == ("config", "--get", "remote.origin.url"):
            return types.SimpleNamespace(
                returncode=0,
                stdout="https://github.com/acme/fancy.git\n",
                stderr="",
            )
        if command == ("rev-parse", "HEAD"):
            return types.SimpleNamespace(
                returncode=0,
                stdout="a" * 40 + "\n",
                stderr="",
            )
        if command == ("rev-parse", "--show-toplevel"):
            return types.SimpleNamespace(
                returncode=0,
                stdout=(self.top_level or argv[2]) + "\n",
                stderr="",
            )
        if command == (
            "status",
            "--porcelain",
            "--untracked-files=no",
        ):
            return types.SimpleNamespace(
                returncode=0,
                stdout=" M node.py\n" if self.dirty else "",
                stderr="",
            )
        raise AssertionError("unexpected git argv: " + repr(argv))


class ComfyHostTests(unittest.TestCase):
    def test_core_and_custom_node_paths_are_classified_without_importing_code(self):
        runner = RecordingGitRunner()
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "KSampler": NodeRecord(
                    source_path="/safe/ComfyUI/nodes.py",
                    module_name="nodes",
                ),
                "Fancy": NodeRecord(
                    source_path=(
                        "/safe/ComfyUI/custom_nodes/Fancy/__init__.py"
                    ),
                    module_name="custom_nodes.Fancy",
                ),
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
            git_runner=runner,
        )

        core = host.describe_node("KSampler")
        custom = host.describe_node("Fancy")

        self.assertEqual(core.kind, "core")
        self.assertEqual(core.status, "resolved")
        self.assertEqual(custom.kind, "custom")
        self.assertEqual(custom.status, "candidate")
        self.assertEqual(custom.repository_url, "https://github.com/acme/fancy")
        self.assertEqual(custom.revision, "a" * 40)
        self.assertEqual(
            runner.calls,
            [
                (
                    "git",
                    "-C",
                    "/safe/ComfyUI/custom_nodes/Fancy",
                    "config",
                    "--get",
                    "remote.origin.url",
                ),
                (
                    "git",
                    "-C",
                    "/safe/ComfyUI/custom_nodes/Fancy",
                    "rev-parse",
                    "HEAD",
                ),
                (
                    "git",
                    "-C",
                    "/safe/ComfyUI/custom_nodes/Fancy",
                    "rev-parse",
                    "--show-toplevel",
                ),
                (
                    "git",
                    "-C",
                    "/safe/ComfyUI/custom_nodes/Fancy",
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                ),
            ],
        )

    def test_nested_cnr_package_is_not_identified_as_parent_checkout(self):
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "Nested": NodeRecord(
                    "/safe/ComfyUI/custom_nodes/Nested/node.py",
                    "custom_nodes.Nested.node",
                )
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
            git_runner=RecordingGitRunner(top_level="/safe/ComfyUI"),
        )

        result = host.describe_node("Nested")

        self.assertEqual(result.status, "mapping_required")
        self.assertIsNone(result.repository_url)
        self.assertIsNone(result.revision)

    def test_dirty_custom_node_requires_mapping_without_using_untracked_files(self):
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "Fancy": NodeRecord(
                    "/safe/ComfyUI/custom_nodes/Fancy/node.py",
                    "custom_nodes.Fancy.node",
                )
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
            git_runner=RecordingGitRunner(dirty=True),
        )

        result = host.describe_node("Fancy")

        self.assertEqual(result.status, "mapping_required")
        self.assertIsNone(result.repository_url)
        self.assertIsNone(result.revision)

    def test_wrong_host_version_symlink_escape_and_forbidden_tree_fail_closed(self):
        wrong = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={},
            version_reader=lambda: ("0.29.0", "1.47.11", "3.13.12"),
        )
        forbidden = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "X": NodeRecord(
                    "/Users/wuraaang/comfyui-vast-cockpit/x.py",
                    "forbidden.x",
                )
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
        )
        escaped = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "X": NodeRecord("/outside/x.py", "outside.x")
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
        )

        with self.assertRaises(HostCompatibilityError):
            wrong.assert_compatible()
        with self.assertRaises(HostCompatibilityError):
            forbidden.describe_node("X")
        with self.assertRaises(HostCompatibilityError):
            escaped.describe_node("X")

    def test_file_widget_metadata_comes_from_running_node_contracts(self):
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "Upscale": NodeRecord(
                    "/safe/ComfyUI/nodes.py",
                    "nodes",
                    input_types={
                        "required": {
                            "model_name": (
                                ["upscaler.pth"],
                                {},
                            ),
                            "strength": ("FLOAT", {}),
                        }
                    },
                ),
                "LoadImage": NodeRecord(
                    "/safe/ComfyUI/nodes.py",
                    "nodes",
                    input_types={
                        "required": {
                            "image": (
                                ["source.jpg"],
                                {"image_upload": True},
                            )
                        }
                    },
                ),
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
        )
        capture = types.SimpleNamespace(
            output={
                "1": {
                    "class_type": "Upscale",
                    "inputs": {
                        "model_name": "upscaler.pth",
                        "strength": 0.5,
                    },
                },
                "2": {
                    "class_type": "LoadImage",
                    "inputs": {"image": "source.jpg"},
                },
            }
        )

        metadata = host.file_input_metadata(
            capture,
            model_filenames={
                "upscale_models": {"upscaler.pth"},
                "checkpoints": set(),
            },
        )

        self.assertEqual(
            metadata,
            {
                "Upscale": {
                    "model_name": FileInputMetadata(
                        kind="model",
                        category="upscale_models",
                    )
                },
                "LoadImage": {
                    "image": FileInputMetadata(kind="input"),
                },
            },
        )

    def test_absent_model_category_comes_from_exact_native_annotation(self):
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "UNETLoader": NodeRecord(
                    "/safe/ComfyUI/nodes.py",
                    "nodes",
                    input_types={
                        "required": {
                            "model_name": ([], {}),
                        }
                    },
                ),
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
        )
        capture = types.SimpleNamespace(
            output={
                "1": {
                    "class_type": "UNETLoader",
                    "inputs": {
                        "model_name": "missing.safetensors",
                    },
                },
            },
            workflow={
                "nodes": [
                    {
                        "id": 1,
                        "type": "UNETLoader",
                        "mode": 0,
                        "properties": {
                            "models": [
                                {
                                    "name": "missing.safetensors",
                                    "url": (
                                        "https://huggingface.co/example/"
                                        "public-model/resolve/main/"
                                        "missing.safetensors"
                                    ),
                                    "directory": "diffusion_models",
                                }
                            ]
                        },
                        "widgets_values": ["missing.safetensors"],
                    }
                ]
            },
        )

        metadata = host.file_input_metadata(
            capture,
            model_filenames={"diffusion_models": set()},
        )

        self.assertEqual(
            metadata,
            {
                "UNETLoader": {
                    "model_name": FileInputMetadata(
                        kind="model",
                        category="diffusion_models",
                    )
                }
            },
        )

    def test_schema_combo_model_uses_exact_native_annotation(self):
        host = ComfyHost(
            comfy_root=Path("/safe/ComfyUI"),
            custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
            node_records={
                "UpscaleModelLoader": NodeRecord(
                    "/safe/ComfyUI/comfy_extras/nodes_upscale_model.py",
                    "comfy_extras.nodes_upscale_model",
                    input_types={
                        "required": {
                            "model_name": (
                                "COMBO",
                                {"options": []},
                            ),
                        }
                    },
                ),
                "TextNode": NodeRecord(
                    "/safe/ComfyUI/nodes.py",
                    "nodes",
                    input_types={
                        "required": {"text": ("STRING", {})},
                    },
                ),
            },
            version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
        )
        capture = types.SimpleNamespace(
            output={
                "61": {
                    "class_type": "UpscaleModelLoader",
                    "inputs": {"model_name": "missing-upscaler.pth"},
                },
                "62": {
                    "class_type": "TextNode",
                    "inputs": {"text": ""},
                },
            },
            workflow={
                "nodes": [
                    {
                        "id": 61,
                        "type": "UpscaleModelLoader",
                        "mode": 0,
                        "properties": {
                            "models": [
                                {
                                    "name": "missing-upscaler.pth",
                                    "url": (
                                        "https://huggingface.co/example/"
                                        "public-model/resolve/main/"
                                        "missing-upscaler.pth"
                                    ),
                                    "directory": "upscale_models",
                                }
                            ]
                        },
                        "widgets_values": ["missing-upscaler.pth"],
                    },
                    {
                        "id": 62,
                        "type": "TextNode",
                        "mode": 0,
                        "properties": {},
                        "widgets_values": [""],
                    },
                ]
            },
        )

        metadata = host.file_input_metadata(
            capture,
            model_filenames={"upscale_models": set()},
        )

        self.assertEqual(
            metadata,
            {
                "UpscaleModelLoader": {
                    "model_name": FileInputMetadata(
                        kind="model",
                        category="upscale_models",
                    )
                }
            },
        )


if __name__ == "__main__":
    unittest.main()

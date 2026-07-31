import types
import unittest
from pathlib import Path

from cloud_run.comfy_host import (
    ComfyHost,
    HostCompatibilityError,
    NodeRecord,
)


class RecordingGitRunner:
    def __init__(self, *, dirty=False):
        self.calls = []
        self.dirty = dirty

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
                    "status",
                    "--porcelain",
                    "--untracked-files=no",
                ),
            ],
        )

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


if __name__ == "__main__":
    unittest.main()

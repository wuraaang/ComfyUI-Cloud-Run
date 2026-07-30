import os
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class RepositoryContractTests(unittest.TestCase):
    def test_required_package_and_gate_files_exist(self):
        for relative_path in (
            "pyproject.toml",
            "README.md",
            "LICENSE",
            ".gitignore",
            "scripts/check.sh",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue(
                    (REPOSITORY_ROOT / relative_path).is_file(),
                    relative_path + " is required",
                )

    def test_metadata_is_dependency_free_and_registry_ready(self):
        metadata = (REPOSITORY_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('name = "comfyui-cloud-run"', metadata)
        self.assertIn('license = { file = "LICENSE" }', metadata)
        self.assertIn('requires-python = ">=3.9"', metadata)
        self.assertIn("dependencies = []", metadata)
        self.assertIn("[tool.comfy]", metadata)
        self.assertIn('DisplayName = "Cloud Run"', metadata)

    def test_readme_documents_preview_security_and_gate(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        for required_text in (
            "Preview only",
            "57808457573e32120301649763d8e019",
            "0600",
            "/cloud-run/api/settings",
            "/cloud-run/api/offers",
            "scripts/check.sh",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, readme)

    def test_gate_is_executable(self):
        gate = REPOSITORY_ROOT / "scripts" / "check.sh"
        self.assertTrue(os.access(gate, os.X_OK))


if __name__ == "__main__":
    unittest.main()

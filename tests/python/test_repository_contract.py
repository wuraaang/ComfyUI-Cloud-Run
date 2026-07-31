import os
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class RepositoryContractTests(unittest.TestCase):
    def test_agents_authorizes_only_the_workflow_derived_session_slice(self):
        text = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(
            "## Current slice: workflow-derived Vast GPU sessions",
            text,
        )
        self.assertIn(
            "capture the exact prompt compiled by the pinned frontend",
            text,
        )
        self.assertIn(
            "resolve every dependency before the first paid mutation",
            text,
        )
        self.assertIn("one sequential job at a time", text)
        self.assertIn("no Vast volume", text)
        self.assertIn("Destroy GPU — stop all Vast billing", text)
        self.assertNotIn("No workflow transfer,", text)

    def test_required_package_and_gate_files_exist(self):
        for relative_path in (
            "pyproject.toml",
            "README.md",
            "NOTICE",
            "LICENSE",
            ".gitignore",
            "scripts/check.sh",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue(
                    (REPOSITORY_ROOT / relative_path).is_file(),
                    relative_path + " is required",
                )

    def test_metadata_is_dependency_free_and_lifecycle_ready(self):
        metadata = (REPOSITORY_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn('name = "comfyui-cloud-run"', metadata)
        self.assertIn('version = "0.2.0"', metadata)
        self.assertIn("managed Vast.ai lifecycle", metadata)
        self.assertIn('license = { file = "LICENSE" }', metadata)
        self.assertIn('requires-python = ">=3.9"', metadata)
        self.assertIn("dependencies = []", metadata)
        self.assertIn("[tool.comfy]", metadata)
        self.assertIn('DisplayName = "Cloud Run"', metadata)

    def test_readme_documents_cost_cleanup_recovery_and_gate(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        for required_text in (
            "Paid Vast.ai rental",
            "027fba7753c024be019030fb42aed900",
            "0600",
            "/cloud-run/api/settings",
            "/cloud-run/api/offers",
            "/cloud-run/api/quotes",
            "/cloud-run/api/attempts/{attempt_id}",
            "idempotency",
            "recovery",
            "emergency",
            "uninstalling",
            "does not destroy",
            "scripts/check.sh",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, readme)
        self.assertNotIn("Preview only — no instance will be rented", readme)

    def test_notice_records_behavioral_reference_without_vendored_source(self):
        notice = (REPOSITORY_ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("LoRA Dataset Studio", notice)
        self.assertIn(
            "de697caf9d607a29c72cebdc2794eecd6b147606",
            notice,
        )
        self.assertIn("PolyForm Noncommercial 1.0.0", notice)
        self.assertIn("behavioral reference", notice)
        self.assertIn("No source code was copied", notice)

    def test_gate_contains_route_state_and_secret_allowlists(self):
        gate = (REPOSITORY_ROOT / "scripts" / "check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("allowed_cloud_run_routes", gate)
        self.assertIn("expected_attempt_states", gate)
        self.assertIn("secret_patterns", gate)

    def test_gate_is_executable(self):
        gate = REPOSITORY_ROOT / "scripts" / "check.sh"
        self.assertTrue(os.access(gate, os.X_OK))


if __name__ == "__main__":
    unittest.main()

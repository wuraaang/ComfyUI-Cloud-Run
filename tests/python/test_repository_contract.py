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
            "scripts/build_worker_artifact.py",
            "scripts/validate_gold_output.py",
            "remote_worker/bootstrap.py",
            "docs/remote-worker-bootstrap-review.md",
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

    def test_readme_documents_final_session_flow_and_safety_boundary(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split()).casefold()
        for required_text in (
            "Paid Vast.ai rental",
            "027fba7753c024be019030fb42aed900",
            "exact prompt compiled by ComfyUI",
            "never posts that prompt to local `/prompt`",
            "one job at a time",
            "delta",
            "20 GiB",
            "80 GiB",
            "no Vast volume",
            "15 and 5 minutes",
            "Destroy GPU — stop all Vast billing",
            "/cloud-run/api/settings",
            "/cloud-run/api/captures",
            "/cloud-run/api/preflights",
            "/cloud-run/api/offers",
            "/cloud-run/api/sessions/{session_id}/confirm",
            "/cloud-run/api/sessions/{session_id}/jobs",
            "/cloud-run/api/sessions/{session_id}/deadline",
            "idempotency",
            "restart recovery",
            "resume",
            "worker release lock",
            "fake/offline",
            "no project-specific worker template has been published or pinned",
            "no real Vast rental or Gold run has occurred",
            "ComfyRelay remote",
            "maximum instance count",
            "maximum hourly price",
            "absolute duration or cost",
            "private Gold image and workflow are never repository fixtures",
            "does not destroy",
            "scripts/check.sh",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text.casefold(), normalized)
        self.assertNotIn("/cloud-run/api/quotes", readme)
        self.assertNotIn("/cloud-run/api/attempts/", readme)
        self.assertNotIn("Workflow transfer", readme)

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

    def test_gate_contains_final_route_state_boundary_and_artifact_checks(self):
        gate = (REPOSITORY_ROOT / "scripts" / "check.sh").read_text(
            encoding="utf-8"
        )
        for required_text in (
            "[check] fake reusable session",
            "[check] worker protocol and artifact",
            "[check] secret, origin, route, state, subprocess, and provider boundary scan",
            "[check] public artifact scan",
            "allowed_cloud_run_routes",
            "allowed_worker_routes",
            "expected_session_states",
            "expected_job_states",
            "expected_transfer_states",
            "allowed_provider_actions",
            "secret_patterns",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, gate)

    def test_publication_docs_name_only_the_correct_source_origin(self):
        paths = (
            REPOSITORY_ROOT / "README.md",
            REPOSITORY_ROOT / "docs" / "project-state.md",
            REPOSITORY_ROOT / "docs" / "remote-worker-bootstrap-review.md",
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in paths
        )
        self.assertIn(
            "https://github.com/wuraaang/ComfyUI-Cloud-Run",
            combined,
        )
        self.assertIn("comfy-relay-do-not-push", combined)
        self.assertIn(
            "https://github.com/wuraaang/comfy-relay.git",
            combined,
        )
        self.assertNotIn(
            "configured Git remote currently points at the wrong",
            combined,
        )

    def test_bootstrap_review_records_the_unpublished_worker_boundary(self):
        review = (
            REPOSITORY_ROOT
            / "docs"
            / "remote-worker-bootstrap-review.md"
        ).read_text(encoding="utf-8")
        for required_text in (
            "scripts/build_worker_artifact.py",
            "remote_worker/Caddyfile",
            "cloud_run/manifest.py",
            "cloud_run/worker_protocol.py",
            "SHA-256",
            "own-instance DELETE",
            "No worker archive or project-specific Vast template has been published",
            "ComfyRelay",
            "Public source repository:",
            "Published source commit:",
            "Fetched archive URL:",
            "Fetched archive size:",
            "Fetched archive SHA-256:",
            "Observed redirect boundary:",
            "Current bootstrap result:",
            "No project-specific Vast template has been created",
            "No live worker-release.json has been created",
            "No paid Gold run has occurred",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, review)

    def test_gate_is_executable(self):
        gate = REPOSITORY_ROOT / "scripts" / "check.sh"
        self.assertTrue(os.access(gate, os.X_OK))


if __name__ == "__main__":
    unittest.main()

import ast
import importlib
import os
import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_live_audit_regression_map():
    path = (
        REPOSITORY_ROOT
        / "tests"
        / "python"
        / "test_fake_desktop_bridge_integration.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and (
            any(
                isinstance(target, ast.Name)
                and target.id == "LIVE_AUDIT_REGRESSION_MAP"
                for target in getattr(node, "targets", ())
            )
            or (
                isinstance(getattr(node, "target", None), ast.Name)
                and node.target.id == "LIVE_AUDIT_REGRESSION_MAP"
            )
        )
    ]
    if len(assignments) != 1:
        raise AssertionError("live-audit regression map must be one static assignment")
    try:
        value = ast.literal_eval(assignments[0].value)
    except (TypeError, ValueError, SyntaxError):
        raise AssertionError("live-audit regression map must be static") from None
    if not isinstance(value, dict):
        raise AssertionError("live-audit regression map must be a dictionary")
    return value


def _assert_regression_test_exists(case, reference):
    case.assertIsInstance(reference, str)
    if reference.startswith("python:"):
        module_name, class_name, method_name = reference.removeprefix(
            "python:"
        ).rsplit(".", 2)
        module = importlib.import_module(module_name)
        test_class = getattr(module, class_name, None)
        method = getattr(test_class, method_name, None)
        case.assertTrue(
            isinstance(test_class, type)
            and issubclass(test_class, unittest.TestCase)
            and method_name.startswith("test_")
            and callable(method),
            reference + " does not resolve to a unittest method",
        )
        return
    if reference.startswith("node:"):
        relative, title = reference.removeprefix("node:").split("::", 1)
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        case.assertRegex(
            source,
            re.compile(r"\btest\(\s*['\"]" + re.escape(title) + r"['\"]"),
            reference + " does not resolve to a Node test",
        )
        return
    case.fail(reference + " uses an unknown test reference scheme")


class RepositoryContractTests(unittest.TestCase):
    def test_user_docs_explain_the_cloud_vast_desktop_flow(self):
        documents = {
            "README.md": (REPOSITORY_ROOT / "README.md").read_text(
                encoding="utf-8"
            ),
            "docs/project-state.md": (
                REPOSITORY_ROOT / "docs" / "project-state.md"
            ).read_text(encoding="utf-8"),
        }
        combined = "\n".join(documents.values())
        normalized = " ".join(combined.split())
        for required_text in (
            "Cloud Vast",
            "ComfyUI Vast",
            "Louer et préparer",
            "native Run",
            "127.0.0.1",
            "Hugging Face",
            "Civitai",
            "optional R2",
            "Agent Panel remains on the Mac",
            "29,347,469,703",
            "16–20 minutes",
            "no Convex dependency",
            "no TanStack dependency",
            "fresh human GO",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, normalized)

        readme = " ".join(documents["README.md"].split())
        for required_text in (
            "output/cloud-vast/<session>/<job>",
            "closing either Desktop window does not destroy",
            "ordinary Run, queue, and batch controls",
        ):
            with self.subTest(readme=required_text):
                self.assertIn(required_text, readme)

        project_state = " ".join(
            documents["docs/project-state.md"].split()
        )
        for required_text in (
            "SQLite + worker persistent state + atomic snapshots",
            "credential-free PR-opening workflow",
            "NOT AUTHORIZED AND NOT RUN",
        ):
            with self.subTest(project_state=required_text):
                self.assertIn(required_text, project_state)

    def test_paid_bridge_acceptance_is_complete_and_non_executing(self):
        acceptance = (
            REPOSITORY_ROOT
            / "docs"
            / "superpowers"
            / "live-tests"
            / "2026-08-03-local-desktop-remote-execution-bridge-acceptance.md"
        ).read_text(encoding="utf-8")
        required_opening = (
            "Status: NOT AUTHORIZED AND NOT RUN.\n\n"
            "This checklist performs no action by itself. Before any create, "
            "destroy,\n"
            "template mutation, worker publication, external install, or paid "
            "probe, stop\n"
            "and obtain a new human GO naming the exact action, offer/rate cap, "
            "maximum\n"
            "instances, maximum duration/cost, and teardown proof."
        )
        self.assertTrue(acceptance.startswith(required_opening))
        numbered_criteria = re.findall(r"(?m)^(\d+)\. ", acceptance)
        self.assertEqual(numbered_criteria, [str(index) for index in range(1, 17)])

        for required_text in (
            "pre-test inventory is exactly zero",
            "never in an external browser",
            "local GPU execution remains unused",
            "native Run and an Agent Panel batch",
            "close/reopen and local-backend restart/reconnect",
            "second compatible job",
            "real dependency delta",
            "secret-free journal",
            "explicit destroy",
            "final inventory is exactly zero",
            "billing_may_continue=false",
            "Commit:",
            "Release:",
            "Manifest digest:",
            "Profile digest:",
            "Session:",
            "Job:",
            "Prompt:",
            "Instance:",
            "Output digest:",
            "Timestamps:",
            "must not be hot-patched or replaced",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, acceptance)

    def test_live_audit_regression_map_has_sixteen_resolvable_findings(self):
        regression_map = _load_live_audit_regression_map()

        self.assertEqual(set(regression_map), set(range(1, 17)))
        self.assertEqual(
            len({item["finding"] for item in regression_map.values()}),
            16,
        )
        for finding, item in regression_map.items():
            with self.subTest(finding=finding):
                self.assertEqual(set(item), {"finding", "tests"})
                self.assertTrue(item["tests"])
                for reference in item["tests"]:
                    _assert_regression_test_exists(self, reference)

    def test_agents_authorizes_only_the_local_desktop_remote_bridge_slice(self):
        text = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Current slice: local Desktop / remote GPU bridge", text)
        self.assertIn("official Remote Connection named `ComfyUI Vast`", text)
        self.assertIn("There is no `Run Vast` button", text)
        self.assertIn("Agent Panel orchestrator remains on the Mac", text)
        self.assertIn("atomic worker snapshots", text)
        self.assertIn("No real Vast mutation without a fresh human GO", text)
        self.assertNotIn(
            "capture the exact prompt compiled by the pinned frontend without posting it",
            text,
        )

    def test_required_package_and_gate_files_exist(self):
        for relative_path in (
            "pyproject.toml",
            "README.md",
            "NOTICE",
            "LICENSE",
            ".gitignore",
            "scripts/check.sh",
            "scripts/build_worker_artifact.py",
            "scripts/build_worker_release_bundle.py",
            "scripts/publish_worker_template.py",
            "scripts/render_worker_template.py",
            "scripts/run_with_comfyui_python.sh",
            "scripts/validate_gold_output.py",
            "scripts/validate_smoke_output.py",
            "scripts/write_worker_release_lock.py",
            "tests/fixtures/cloud-run-core-output-smoke.json",
            "remote_worker/bootstrap.py",
            "remote_worker/gateway.py",
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
            "no private project-specific Vast template exists",
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

    def test_readme_documents_controller_owned_worker_boundary(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())

        self.assertIn(
            "controller generates a per-instance boundary token",
            normalized,
        )
        self.assertIn("injects it at create time", normalized)
        self.assertIn("Caddy alone receives", normalized)
        self.assertIn(
            "session ID plus its existing allowlisted runtime variables",
            normalized,
        )
        self.assertIn(
            "`JUPYTER_TOKEN` and `OPEN_BUTTON_TOKEN` are not fallback credentials",
            normalized,
        )

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
            "[check] fake Desktop bridge",
            "[check] worker protocol and artifact",
            "[check] immutable worker release bundle",
            "[check] secret, origin, route, state, subprocess, and provider boundary scan",
            "[check] public artifact scan",
            "allowed_cloud_run_routes",
            "allowed_worker_routes",
            "expected_session_states",
            "expected_job_states",
            "expected_transfer_states",
            "allowed_provider_actions",
            "reviewed_release_tool_paths",
            "build_worker_release_bundle",
            "publish_worker_template.py",
            "test_worker_template_api.py",
            '("remote_worker/gateway.py", "subprocess.Popen")',
            "secret_patterns",
            "unset VAST_API_KEY CONTAINER_API_KEY",
            'Path("tests/fixtures/native-model-metadata-workflow.json")',
            'Path("tests/fixtures/cloud-run-core-output-smoke.json")',
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, gate)
        self.assertGreaterEqual(
            gate.count('"release-assets.githubusercontent.com"'),
            2,
        )

    def test_gate_rejects_provider_credentials_in_boundary_production_files(self):
        gate = (REPOSITORY_ROOT / "scripts" / "check.sh").read_text(
            encoding="utf-8"
        )
        for required_text in (
            'Path("cloud_run/vast.py")',
            'Path("cloud_run/lifecycle.py")',
            'Path("remote_worker/gateway.py")',
            'Path("remote_worker/Caddyfile")',
            '"JUPYTER_TOKEN"',
            '"OPEN_BUTTON_TOKEN"',
            '"jupyter_token"',
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
        normalized = " ".join(review.split())
        for required_text in (
            "immutable GitHub Release asset",
            "direct `200` response or exactly one HTTP `302` redirect",
            "release-assets.githubusercontent.com",
            "never retained, persisted, logged, or returned",
            "scripts/build_worker_release_bundle.py",
            "scripts/render_worker_template.py",
            "scripts/write_worker_release_lock.py",
            "owner-private directory",
            "mode `0600`",
            "scripts/build_worker_artifact.py",
            "remote_worker/Caddyfile",
            "remote_worker/gateway.py",
            "cloud_run/manifest.py",
            "cloud_run/worker_protocol.py",
            "SHA-256",
            "own-instance DELETE",
            "Immutable Python 3.12 Remote Worker releases for",
            "No private project-specific Vast template exists",
            "No local live `worker-release.json` exists",
            "ComfyRelay",
            "Public source repository:",
            "Published source commit:",
            "Fetched archive URL:",
            "Fetched archive size:",
            "Fetched archive SHA-256:",
            "Observed redirect boundary:",
            "Historical full-repository source-archive result: `FAIL`",
            "No Vast offer search has been performed",
            "No paid Vast instance has been created",
            "No post-migration live workflow run has occurred",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, normalized)

    def test_offline_docs_record_total_create_and_release_boundaries(self):
        paths = (
            REPOSITORY_ROOT / "README.md",
            REPOSITORY_ROOT / "docs" / "project-state.md",
            REPOSITORY_ROOT / "docs" / "remote-worker-bootstrap-review.md",
        )
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in paths
        )
        normalized = " ".join(combined.split())
        for required_text in (
            "Maximum total instance creates",
            "limited to `1` or `2`",
            "before any replacement offer search or create",
            "Immutable Python 3.12 Remote Worker releases for",
            "No private project-specific Vast template exists",
            "No local live `worker-release.json` exists",
            "No post-migration ComfyUI restart has occurred",
            "No Vast offer search has been performed",
            "No paid Vast instance has been created",
            "No post-migration live workflow run has occurred",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, normalized)

    def test_offline_docs_record_the_exact_official_runtime_contract(self):
        documents = {
            path.name: path.read_text(encoding="utf-8")
            for path in (
                REPOSITORY_ROOT / "README.md",
                REPOSITORY_ROOT / "docs" / "project-state.md",
                REPOSITORY_ROOT
                / "docs"
                / "remote-worker-bootstrap-review.md",
            )
        }
        combined = "\n".join(documents.values())
        for required_text in (
            "docker.io/vastai/comfy@sha256:"
            "9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c",
            "v0.29.0-cuda-12.9-py312",
            "sha256:7a83c93be852db309d4be3e415cf38e186977c202638f1ef1b4a605a3bc49f0a",
            "sha256:992e89c2d0641a6c894885d4246dc706911c7a02266f368337b41bf968eaaaf2",
            "38584 bytes",
            "org.opencontainers.image.revision",
            "in-toto",
            "Python `3.12`",
            "Python `3.13.12`",
            "`cp312`",
            "`py3`",
            "`manylinux_2_39_x86_64`",
            "`musllinux*`",
            "`/venv/main/bin/python`",
            "`CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI`",
            "only `hash_id`, `use_ssh`, and `ssh_direct`",
            "`runtype=ssh`",
            "`jup_direct=false`",
            "`jupyter_dir=/workspace`",
            "`use_jupyter_lab=false`",
            "`-p 8765:8765`",
            "`private=true`",
            '"gpu_arch": {"eq": "nvidia"}',
            '"cpu_arch": {"eq": "amd64"}',
            '"cuda_max_good": {"gte": 12.9}',
            '"compute_cap": {"gte": 750}',
            '"num_gpus": {"eq": 1}',
            "`worker_release`",
            "`WorkerRelease.to_record()`",
        ):
            with self.subTest(text=required_text):
                self.assertIn(required_text, combined)

        for name, text in documents.items():
            normalized = " ".join(text.split())
            for required_text in (
                "Immutable Python 3.12 Remote Worker releases for",
                "No private project-specific Vast template exists",
                "No local live `worker-release.json` exists",
                "No post-migration ComfyUI restart has occurred",
                "No Vast offer search has been performed",
                "No paid Vast instance has been created",
                "No post-migration live workflow run has occurred",
            ):
                with self.subTest(document=name, text=required_text):
                    self.assertIn(required_text, normalized)

    def test_readme_audits_the_base_before_rendering_the_private_request(self):
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        audit = "python3 scripts/publish_worker_template.py \\\n  audit-base"
        renderer = "python3 scripts/render_worker_template.py"

        self.assertIn(audit, readme)
        self.assertIn(renderer, readme)
        self.assertLess(readme.index(audit), readme.index(renderer))

    def test_gate_is_executable(self):
        gate = REPOSITORY_ROOT / "scripts" / "check.sh"
        self.assertTrue(os.access(gate, os.X_OK))


if __name__ == "__main__":
    unittest.main()

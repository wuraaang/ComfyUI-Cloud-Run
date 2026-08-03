import asyncio
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class ReadinessValueTests(unittest.TestCase):
    def setUp(self):
        from cloud_run.readiness import (
            REQUIRED_READINESS_CHECKS,
            ReadinessCheck,
        )

        self.names = REQUIRED_READINESS_CHECKS
        self.ReadinessCheck = ReadinessCheck

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    def check(self, name, status="passed"):
        return self.ReadinessCheck(
            name=name,
            status=status,
            evidence_digest=self.digest(name + ":" + status),
            message=(
                "Readiness proof passed."
                if status == "passed"
                else "Readiness proof failed."
            ),
        )

    def test_complete_matrix_is_immutable_digest_bound_and_secret_safe(self):
        from cloud_run.readiness import ReadinessReport

        report = ReadinessReport.create(
            session_id="session-1",
            instance_id="instance-1",
            worker_release_digest="a" * 64,
            manifest_digest="b" * 64,
            profile_revision=3,
            relay_origin="http://127.0.0.1:32145",
            inventory_observed_at=100.0,
            created_at=101.0,
            checks=tuple(self.check(name) for name in self.names),
        )

        self.assertTrue(report.ready)
        self.assertTrue(report.desktop_ready)
        self.assertEqual(len(report.checks), 16)
        self.assertEqual(len(report.report_digest), 64)
        self.assertEqual(
            ReadinessReport.from_record(report.to_record()),
            report,
        )
        public = report.public_payload()
        self.assertTrue(public["desktop_ready"])
        self.assertNotIn("evidence", str(public).casefold())
        self.assertNotIn("secret", repr(report).casefold())

    def test_unknown_duplicate_incomplete_and_unsafe_values_are_rejected(self):
        from cloud_run.readiness import ReadinessCheck, ReadinessReport

        base = tuple(self.check(name) for name in self.names)
        arguments = {
            "session_id": "session-1",
            "instance_id": "instance-1",
            "worker_release_digest": "a" * 64,
            "manifest_digest": "b" * 64,
            "profile_revision": 3,
            "relay_origin": "http://127.0.0.1:32145",
            "inventory_observed_at": 100.0,
            "created_at": 101.0,
        }
        for checks in (
            base[:-1],
            base + (base[0],),
            base[:-1]
            + (
                ReadinessCheck(
                    name="unknown_probe",
                    status="passed",
                    evidence_digest="c" * 64,
                    message="Readiness proof passed.",
                ),
            ),
        ):
            with self.subTest(size=len(checks)):
                with self.assertRaises(ValueError):
                    ReadinessReport.create(checks=checks, **arguments)
        with self.assertRaises(ValueError):
            self.ReadinessCheck(
                name=self.names[0],
                status="failed",
                evidence_digest="c" * 64,
                message="Bearer private-token",
            )

    def test_only_absent_agent_panel_may_be_not_required(self):
        for name in self.names:
            with self.subTest(name=name):
                status = "not_required"
                if name == "agent_panel_capabilities":
                    self.check(name, status)
                else:
                    with self.assertRaises(ValueError):
                        self.check(name, status)


class ReadinessValidatorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode("ascii")).hexdigest()

    def session(self):
        return SimpleNamespace(
            session_id="session-1",
            instance_id="instance-1",
        )

    def manifest(self):
        return SimpleNamespace(digest="b" * 64)

    def profile(self):
        return SimpleNamespace(revision=3)

    def checks(self, *, failed=None, omitted=None, agent_absent=False):
        from cloud_run.readiness import (
            REQUIRED_READINESS_CHECKS,
            ReadinessCheck,
        )

        values = []
        for name in REQUIRED_READINESS_CHECKS:
            if name == omitted:
                continue
            status = "failed" if name == failed else "passed"
            if name == "agent_panel_capabilities" and agent_absent:
                status = "not_required"
            values.append(
                ReadinessCheck(
                    name=name,
                    status=status,
                    evidence_digest=self.digest(name + ":" + status),
                    message=(
                        "Readiness proof passed."
                        if status in {"passed", "not_required"}
                        else "Readiness proof failed."
                    ),
                )
            )
        return tuple(values)

    async def validate(self, checks):
        from cloud_run.readiness import ReadinessValidator

        calls = []

        async def probe(session, manifest, profile):
            calls.append((session, manifest, profile))
            return checks

        validator = ReadinessValidator(
            probe=probe,
            worker_release_digest="a" * 64,
            relay_origin="http://127.0.0.1:32145",
            clock=lambda: 101.0,
        )
        report = await validator.validate(
            self.session(),
            self.manifest(),
            self.profile(),
        )
        self.assertEqual(len(calls), 1)
        return report

    async def test_every_missing_or_failed_proof_blocks_desktop_readiness(self):
        from cloud_run.readiness import REQUIRED_READINESS_CHECKS

        passing = await self.validate(self.checks())
        self.assertTrue(passing.desktop_ready)
        for name in REQUIRED_READINESS_CHECKS:
            with self.subTest(name=name, case="failed"):
                report = await self.validate(self.checks(failed=name))
                self.assertFalse(report.desktop_ready)
            with self.subTest(name=name, case="missing"):
                report = await self.validate(self.checks(omitted=name))
                self.assertFalse(report.desktop_ready)

    async def test_absent_agent_panel_is_the_only_not_required_success(self):
        report = await self.validate(self.checks(agent_absent=True))
        self.assertTrue(report.desktop_ready)
        check = next(
            item
            for item in report.checks
            if item.name == "agent_panel_capabilities"
        )
        self.assertEqual(check.status, "not_required")

    async def test_controller_probe_uses_only_read_only_worker_and_relay_checks(self):
        from cloud_run.readiness import (
            ControllerReadinessProbe,
            ReadinessCheck,
            ReadinessValidator,
            evidence_digest,
        )

        release = SimpleNamespace(
            worker_archive_sha256="a" * 64,
            worker_commit="c" * 40,
            protocol_version="2",
            comfyui_core_version="0.29.0",
            comfyui_frontend_version="1.47.10",
        )
        artifact = SimpleNamespace(
            artifact_id="model-1",
            destination="models/checkpoints/model.safetensors",
            sha256="d" * 64,
            size_bytes=10,
        )
        profile = SimpleNamespace(
            revision=3,
            archive=SimpleNamespace(sha256="e" * 64),
            bootstrap_digest="f" * 64,
        )
        manifest = SimpleNamespace(
            digest="b" * 64,
            worker_version=release.worker_commit,
            protocol_version=release.protocol_version,
            comfyui_core_version=release.comfyui_core_version,
            comfyui_frontend_version=release.comfyui_frontend_version,
            custom_nodes=(),
            artifacts=(artifact,),
            ui_packages=(),
            profile=profile,
        )
        session = SimpleNamespace(
            session_id="session-1",
            instance_id="instance-1",
            label="comfy-cloud-run-session-1",
        )
        calls = []

        class Worker:
            async def health(inner_self):
                calls.append("health")
                return {"protocol_version": "2", "claimed": True}

            async def transaction(inner_self, transaction_id):
                calls.append(("transaction", transaction_id))
                return {
                    "state": "ready",
                    "manifest_digest": manifest.digest,
                    "readiness": {
                        "protocol_version": "2",
                        "comfyui_core_version": "0.29.0",
                        "comfyui_frontend_version": "1.47.10",
                        "worker_version": "c" * 40,
                        "validated_class_types": ["KSampler"],
                        "validated_artifacts": ["model-1"],
                        "profile_revision": 3,
                        "profile_digest": "e" * 64,
                        "bootstrap_digest": "f" * 64,
                        "ui_package_digests": {},
                        "comfy_process_healthy": True,
                        "completed_at": 99.0,
                    },
                }

        class Relay:
            async def probe_readiness(inner_self, *args, **kwargs):
                calls.append(("relay", args[0], kwargs["agent_required"]))
                return tuple(
                    ReadinessCheck(
                        name=name,
                        status=(
                            "not_required"
                            if name == "agent_panel_capabilities"
                            else "passed"
                        ),
                        evidence_digest=evidence_digest(name),
                        message="Readiness proof passed.",
                    )
                    for name in (
                        "loopback_session_binding",
                        "native_http_probe",
                        "native_websocket_probe",
                        "agent_panel_capabilities",
                    )
                )

        async def inventory(observed_session):
            calls.append("inventory")
            return {
                "instance_id": observed_session.instance_id,
                "label": observed_session.label,
                "actual_status": "running",
            }

        probe = ControllerReadinessProbe(
            worker_factory=lambda _session: Worker(),
            inventory_probe=inventory,
            desktop_relay=Relay(),
            release=release,
            required_class_types=lambda _manifest: ("KSampler",),
            local_execution_counter=lambda _session_id: 0,
        )
        validator = ReadinessValidator(
            probe=probe,
            worker_release_digest=release.worker_archive_sha256,
            relay_origin="http://127.0.0.1:32145",
            clock=lambda: 100.0,
        )

        report = await validator.validate(session, manifest, profile)

        self.assertTrue(report.desktop_ready)
        self.assertEqual(
            calls,
            [
                "inventory",
                "health",
                ("transaction", "provision-" + manifest.digest),
                ("relay", "session-1", False),
            ],
        )


class ReadinessRepositoryTests(unittest.TestCase):
    def setUp(self):
        from cloud_run.readiness import (
            REQUIRED_READINESS_CHECKS,
            ReadinessCheck,
            ReadinessReport,
        )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "attempts.sqlite3"
        self.names = REQUIRED_READINESS_CHECKS
        checks = tuple(
            ReadinessCheck(
                name=name,
                status="passed",
                evidence_digest=hashlib.sha256(name.encode("ascii")).hexdigest(),
                message="Readiness proof passed.",
            )
            for name in self.names
        )
        self.report = ReadinessReport.create(
            session_id="session-1",
            instance_id="instance-1",
            worker_release_digest="a" * 64,
            manifest_digest="b" * 64,
            profile_revision=3,
            relay_origin="http://127.0.0.1:32145",
            inventory_observed_at=100.0,
            created_at=101.0,
            checks=checks,
        )

    def test_schema_v12_persists_and_reuses_exact_identity(self):
        from cloud_run.job_repository import JobRepository

        repository = JobRepository(self.path)
        saved = repository.save_readiness_report(self.report)
        reopened = JobRepository(self.path)
        current = reopened.current_readiness_report(
            session_id="session-1",
            instance_id="instance-1",
            worker_release_digest="a" * 64,
            manifest_digest="b" * 64,
            profile_revision=3,
            relay_origin="http://127.0.0.1:32145",
        )

        self.assertEqual(saved, self.report)
        self.assertEqual(current, self.report)
        self.assertIsNone(
            reopened.current_readiness_report(
                session_id="session-1",
                instance_id="instance-2",
                worker_release_digest="a" * 64,
                manifest_digest="b" * 64,
                profile_revision=3,
                relay_origin="http://127.0.0.1:32145",
            )
        )
        self.assertEqual(
            reopened.save_readiness_report(self.report),
            self.report,
        )
        with sqlite3.connect(self.path) as connection:
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            count = connection.execute(
                "SELECT COUNT(*) FROM readiness_reports"
            ).fetchone()[0]
        self.assertEqual(version, "12")
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

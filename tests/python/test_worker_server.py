import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from cloud_run.worker_protocol import sign_request


def claim_payload(
    *,
    session_id="session-1",
    secret_character="a",
):
    return {
        "protocol_version": "2",
        "session_id": session_id,
        "session_secret_hex": secret_character * 64,
    }


class FakeRequest:
    def __init__(
        self,
        method,
        path,
        *,
        payload=None,
        body=None,
        boundary_authenticated=True,
        auth_envelope=None,
        headers=None,
        query=None,
    ):
        self.method = method
        self.path = path
        self.query = query or {}
        self.path_qs = (
            path
            if not self.query
            else path
            + "?"
            + "&".join(
                str(key) + "=" + str(value)
                for key, value in self.query.items()
            )
        )
        self.body = (
            json.dumps(
                payload,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if payload is not None
            else (body if body is not None else b"")
        )
        self.boundary_authenticated = boundary_authenticated
        self.auth_envelope = auth_envelope
        self.headers = headers or {}


class FakeProfileStore:
    def __init__(self, snapshot, artifact):
        self.saved_snapshot = snapshot
        self.saved_artifact = artifact
        self.applied = []
        self.cursors = []
        self.artifact_calls = []

    def apply(self, profile):
        self.applied.append(profile)
        return {
            "profile_id": profile.profile_id,
            "revision": profile.revision,
            "bootstrap_digest": profile.bootstrap_digest,
            "bootstrap_loaded_at_revision": None,
        }

    def snapshot(self, after_revision):
        self.cursors.append(after_revision)
        return self.saved_snapshot

    def artifact(self, profile_id, path):
        self.artifact_calls.append((profile_id, path))
        return self.saved_artifact


class WorkerStateTests(unittest.TestCase):
    def test_claim_state_is_atomic_private_and_survives_reopen(self):
        from remote_worker.state import WorkerStateStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "worker-state.json"
            store = WorkerStateStore(path)

            saved = store.claim(
                session_id="session-1",
                session_secret_hex="a" * 64,
            )
            reopened = WorkerStateStore(path).load()

            self.assertEqual(saved, reopened)
            self.assertEqual(
                reopened,
                {
                    "schema_version": 3,
                    "protocol_version": "2",
                    "session_id": "session-1",
                    "session_secret_hex": "a" * 64,
                    "claimed": True,
                    "deadline_at": 0,
                    "deadline_mode": "finite",
                    "installed": {},
                    "transactions": {},
                    "jobs": {},
                },
            )
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                0o600,
            )
            self.assertEqual(
                stat.S_IMODE(path.parent.stat().st_mode),
                0o700,
            )
            self.assertEqual(
                sorted(item.name for item in path.parent.iterdir()),
                ["worker-state.json"],
            )

    def test_schema_one_job_migrates_native_fields_and_persists_schema_three(self):
        from remote_worker.state import WorkerStateStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-state.json"
            legacy = {
                "schema_version": 1,
                "protocol_version": "2",
                "session_id": "session-1",
                "session_secret_hex": "a" * 64,
                "claimed": True,
                "deadline_at": 0,
                "deadline_mode": "finite",
                "installed": {},
                "transactions": {},
                "jobs": {
                    "job-1": {
                        "kind": "job",
                        "job_id": "job-1",
                        "request_digest": "c" * 64,
                        "manifest_digest": "d" * 64,
                        "state": "queued",
                        "client_id": "client-1",
                        "prompt_id": None,
                        "sequence": 0,
                        "events": [],
                        "previews": {},
                        "outputs": {},
                        "error": None,
                        "updated_at": 11.0,
                    }
                },
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            os.chmod(path, 0o600)

            migrated = WorkerStateStore(path).load()

            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(
                migrated["jobs"]["job-1"]["created_at"],
                11.0,
            )
            self.assertEqual(
                migrated["jobs"]["job-1"]["request_id"],
                "job-1",
            )
            self.assertEqual(
                migrated["jobs"]["job-1"]["execution_state"],
                "queued",
            )
            self.assertEqual(
                migrated["jobs"]["job-1"]["harvest_state"],
                "pending",
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted, migrated)

    def test_schema_two_job_migrates_without_losing_terminal_success(self):
        from remote_worker.state import WorkerStateStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-state.json"
            legacy = {
                "schema_version": 2,
                "protocol_version": "2",
                "session_id": "session-1",
                "session_secret_hex": "a" * 64,
                "claimed": True,
                "deadline_at": 0,
                "deadline_mode": "finite",
                "installed": {},
                "transactions": {},
                "jobs": {
                    "job-1": {
                        "kind": "job",
                        "job_id": "job-1",
                        "request_digest": "c" * 64,
                        "manifest_digest": "d" * 64,
                        "state": "succeeded",
                        "client_id": "client-1",
                        "prompt_id": (
                            "11111111-1111-4111-8111-111111111111"
                        ),
                        "sequence": 0,
                        "events": [],
                        "previews": {},
                        "outputs": {},
                        "error": None,
                        "created_at": 10.0,
                        "updated_at": 11.0,
                    }
                },
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            os.chmod(path, 0o600)

            migrated = WorkerStateStore(path).load()
            job = migrated["jobs"]["job-1"]

            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(job["execution_state"], "succeeded")
            self.assertEqual(job["harvest_state"], "succeeded")
            self.assertIsNone(job["native_response"])

    def test_corrupt_or_world_readable_state_fails_closed(self):
        from remote_worker.state import WorkerStateError, WorkerStateStore

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-state.json"
            path.write_text('{"claimed":true}', encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaises(WorkerStateError):
                WorkerStateStore(path).load()

            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "protocol_version": "2",
                        "session_id": "session-1",
                        "session_secret_hex": "a" * 64,
                        "claimed": True,
                        "deadline_at": 0,
                        "deadline_mode": "finite",
                        "installed": {},
                        "transactions": {},
                        "jobs": {},
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(path, 0o644)
            with self.assertRaises(WorkerStateError):
                WorkerStateStore(path).load()

            os.chmod(path, 0o600)
            os.chmod(path.parent, 0o755)
            with self.assertRaises(WorkerStateError):
                WorkerStateStore(path).load()

    def test_provision_transaction_accepts_only_sanitized_progress_fields(self):
        from remote_worker.state import WorkerStateError, WorkerStateStore

        with tempfile.TemporaryDirectory() as directory:
            store = WorkerStateStore(Path(directory) / "worker.json")
            store.claim(
                session_id="session-1",
                session_secret_hex="a" * 64,
            )
            transaction_id = "provision-" + "b" * 64
            record = {
                "kind": "provision",
                "transaction_id": transaction_id,
                "manifest_digest": "b" * 64,
                "manifest": None,
                "required_class_types": ["KSampler"],
                "state": "applying",
                "planned_restarts": 0,
                "repair_restarts": 0,
                "repair_used": False,
                "missing_class_types": [],
                "missing_artifacts": [],
                "failure_code": None,
                "updated_at": 10.0,
                "last_progress_at": 10.0,
                "progress": {
                    "phase": "model_transfer",
                    "dependency_id": "model-" + "c" * 64,
                    "transferred_bytes": 4,
                    "total_bytes": 10,
                },
            }

            store.record_transaction(transaction_id, record)
            saved = store.load()["transactions"][transaction_id]
            self.assertEqual(saved["progress"], record["progress"])
            with self.assertRaises(WorkerStateError):
                store.record_transaction(
                    transaction_id,
                    {
                        **record,
                        "source_url": (
                            "https://huggingface.co/private?token=secret"
                        ),
                    },
                )
            with self.assertRaises(WorkerStateError):
                store.record_transaction(
                    transaction_id,
                    {
                        **record,
                        "progress": {
                            **record["progress"],
                            "transferred_bytes": 11,
                        },
                    },
                )
            with self.assertRaises(WorkerStateError):
                store.record_transaction(
                    transaction_id,
                    {
                        **record,
                        "progress": {
                            **record["progress"],
                            "phase": [],
                        },
                    },
                )
            with self.assertRaises(WorkerStateError):
                store.record_transaction(
                    transaction_id,
                    {**record, "required_class_types": [[]]},
                )


class WorkerApplicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / "private" / "worker-state.json"

    def application(self, **kwargs):
        from remote_worker.server import WorkerApplication

        return WorkerApplication(state_path=self.path, **kwargs)

    def test_worker_claim_requires_proxy_boundary_and_is_one_time(self):
        worker = self.application()
        denied = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(),
                    boundary_authenticated=False,
                )
            )
        )
        accepted = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(),
                    boundary_authenticated=True,
                )
            )
        )
        duplicate = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(
                        session_id="session-2",
                        secret_character="b",
                    ),
                    boundary_authenticated=True,
                )
            )
        )

        self.assertEqual(denied.status, 401)
        self.assertEqual(accepted.status, 200)
        self.assertEqual(
            accepted.payload,
            {
                "protocol_version": "2",
                "session_id": "session-1",
                "claimed": True,
            },
        )
        self.assertEqual(duplicate.status, 409)
        encoded = repr([denied.payload, accepted.payload, duplicate.payload])
        self.assertNotIn("a" * 64, encoded)
        self.assertNotIn("b" * 64, encoded)

    def test_expected_session_id_must_match_the_first_claim(self):
        worker = self.application(expected_session_id="expected-session")

        mismatched = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(session_id="different-session"),
                )
            )
        )
        accepted = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(session_id="expected-session"),
                )
            )
        )

        self.assertEqual(mismatched.status, 409)
        self.assertEqual(accepted.status, 200)

    def test_worker_exposes_only_the_protocol_allowlist(self):
        from remote_worker.server import worker_route_set

        self.assertEqual(
            worker_route_set(),
            {
                ("GET", "/worker/v1/health"),
                ("POST", "/worker/v1/claim"),
                ("POST", "/worker/v1/manifests"),
                (
                    "GET",
                    "/worker/v1/transactions/{transaction_id}",
                ),
                ("PUT", "/worker/v1/artifacts/{artifact_id}"),
                ("GET", "/worker/v1/artifacts/{artifact_id}"),
                ("POST", "/worker/v1/jobs"),
                ("GET", "/worker/v1/jobs/{job_id}"),
                ("GET", "/worker/v1/jobs/{job_id}/events"),
                (
                    "GET",
                    "/worker/v1/jobs/{job_id}/snapshot",
                ),
                (
                    "GET",
                    "/worker/v1/jobs/{job_id}/previews/{preview_id}",
                ),
                ("PUT", "/worker/v1/profile"),
                ("GET", "/worker/v1/profile"),
                (
                    "GET",
                    "/worker/v1/profile/artifacts/{artifact_id}",
                ),
                ("PUT", "/worker/v1/deadline"),
            },
        )

    def test_signed_profile_routes_apply_snapshot_and_serve_ranges(self):
        from remote_worker.profile import (
            WorkerProfileArtifact,
            WorkerProfileSnapshot,
        )

        archive = self.directory / "profile.tar.gz"
        archive.write_bytes(b"profile-archive")
        digest = __import__("hashlib").sha256(
            archive.read_bytes()
        ).hexdigest()
        snapshot = WorkerProfileSnapshot(
            profile_id="desktop-profile",
            revision=2,
            base_revision=1,
            bootstrap_digest="b" * 64,
            archive_size_bytes=archive.stat().st_size,
            archive_sha256=digest,
            artifacts=(
                {
                    "path": "bootstrap/current.json",
                    "kind": "bootstrap_workflow",
                    "size_bytes": 2,
                    "sha256": "c" * 64,
                },
            ),
            archive_path=archive,
        )
        artifact = WorkerProfileArtifact(
            path=archive,
            size_bytes=archive.stat().st_size,
            sha256=digest,
            mime_type="application/gzip",
        )
        profiles = FakeProfileStore(snapshot, artifact)
        worker = self.application(clock=lambda: 1000, profile_store=profiles)
        asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(),
                )
            )
        )
        profile_record = {
            "profile_id": "desktop-profile",
            "revision": 1,
            "archive": {
                "artifact_id": "profile-" + "d" * 64,
                "kind": "profile_archive",
                "logical_name": "profile-" + "d" * 64,
                "destination": "user/default/cloud-vast-profile",
                "size_bytes": 10,
                "sha256": "d" * 64,
                "source": {
                    "kind": "local-upload",
                    "locator": "local-upload:profile-" + "d" * 64,
                    "immutable_revision": None,
                },
            },
            "bootstrap_digest": "b" * 64,
            "files": [
                {
                    "path": "bootstrap/current.json",
                    "size_bytes": 2,
                    "sha256": "c" * 64,
                }
            ],
        }

        def signed(method, path, *, payload=None, query=None, headers=None):
            body = (
                json.dumps(
                    payload,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                if payload is not None
                else b""
            )
            path_qs = (
                path
                if not query
                else path
                + "?"
                + "&".join(
                    str(key) + "=" + str(value)
                    for key, value in query.items()
                )
            )
            nonce = "profile-" + str(len(profiles.cursors) + len(profiles.applied))
            return FakeRequest(
                method,
                path,
                body=body,
                query=query,
                headers=headers,
                auth_envelope=sign_request(
                    bytes.fromhex("a" * 64),
                    method,
                    path_qs,
                    body,
                    timestamp=1000,
                    nonce=nonce,
                ),
            )

        applied = asyncio.run(
            worker.handle(
                signed(
                    "PUT",
                    "/worker/v1/profile",
                    payload={"profile": profile_record},
                )
            )
        )
        remote = asyncio.run(
            worker.handle(
                signed(
                    "GET",
                    "/worker/v1/profile",
                    query={"after_revision": "1"},
                )
            )
        )
        artifact_id = "profile-" + digest
        download = asyncio.run(
            worker.handle(
                signed(
                    "GET",
                    "/worker/v1/profile/artifacts/" + artifact_id,
                    query={"start": "2"},
                    headers={"Range": "bytes=2-"},
                )
            )
        )

        self.assertEqual(applied.status, 200)
        self.assertEqual(profiles.applied[0].profile_id, "desktop-profile")
        self.assertEqual(remote.payload, snapshot.public_payload())
        self.assertEqual(profiles.cursors, [1, 0])
        self.assertEqual(
            profiles.artifact_calls,
            [("desktop-profile", artifact_id)],
        )
        self.assertEqual(download.status, 206)
        self.assertEqual(download.payload.start, 2)
        self.assertEqual(download.headers["ETag"], '"' + digest + '"')

    def test_authenticated_routes_require_boundary_hmac_and_reject_replay(self):
        worker = self.application(clock=lambda: 1000)
        asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/claim",
                    payload=claim_payload(),
                )
            )
        )
        body = b'{"manifest":"private-body-marker"}'
        envelope = sign_request(
            bytes.fromhex("a" * 64),
            "POST",
            "/worker/v1/manifests",
            body,
            timestamp=1000,
            nonce="nonce-1",
        )

        no_hmac = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/manifests",
                    body=body,
                )
            )
        )
        no_boundary = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/manifests",
                    body=body,
                    auth_envelope=envelope,
                    boundary_authenticated=False,
                )
            )
        )
        accepted = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/manifests",
                    body=body,
                    auth_envelope=envelope,
                )
            )
        )
        replay = asyncio.run(
            worker.handle(
                FakeRequest(
                    "POST",
                    "/worker/v1/manifests",
                    body=body,
                    auth_envelope=envelope,
                )
            )
        )

        self.assertEqual(no_hmac.status, 401)
        self.assertEqual(no_boundary.status, 401)
        self.assertEqual(accepted.status, 501)
        self.assertEqual(replay.status, 401)
        responses = repr(
            [
                no_hmac.payload,
                no_boundary.payload,
                accepted.payload,
                replay.payload,
            ]
        )
        self.assertNotIn("a" * 64, responses)
        self.assertNotIn("private-body-marker", responses)

    def test_health_is_boundary_protected_and_never_returns_secrets(self):
        worker = self.application()

        denied = asyncio.run(
            worker.handle(
                FakeRequest(
                    "GET",
                    "/worker/v1/health",
                    boundary_authenticated=False,
                )
            )
        )
        healthy = asyncio.run(
            worker.handle(
                FakeRequest("GET", "/worker/v1/health")
            )
        )
        unknown = asyncio.run(
            worker.handle(
                FakeRequest("POST", "/worker/v1/shell")
            )
        )

        self.assertEqual(denied.status, 401)
        self.assertEqual(
            healthy.payload,
            {"protocol_version": "2", "claimed": False},
        )
        self.assertEqual(unknown.status, 404)

    def test_proxy_and_worker_bindings_are_exact_and_loopback_only(self):
        from remote_worker.server import WORKER_BIND_HOST, WORKER_BIND_PORT

        caddyfile = (
            Path(__file__).resolve().parents[2]
            / "remote_worker"
            / "Caddyfile"
        ).read_text(encoding="utf-8")

        self.assertEqual(WORKER_BIND_HOST, "127.0.0.1")
        self.assertEqual(WORKER_BIND_PORT, 8766)
        expected = """{
    admin off
    auto_https off
}

:8765 {
    route {
        @unauthorized not header Authorization "Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}"
        respond @unauthorized 401

        request_header -Authorization
        request_header -Cookie
        request_header -X-Cloud-Run-Boundary
        request_header -X-Forwarded-For
        request_header -X-Forwarded-Host
        request_header -X-Forwarded-Proto
        request_header X-Cloud-Run-Boundary authenticated
        reverse_proxy 127.0.0.1:8766
    }
}
"""
        self.assertEqual(caddyfile, expected)
        self.assertLess(
            caddyfile.index("@unauthorized not header Authorization"),
            caddyfile.index("request_header -Authorization"),
        )


if __name__ == "__main__":
    unittest.main()

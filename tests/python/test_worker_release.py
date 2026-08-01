import json
import os
import tempfile
import unittest
from pathlib import Path


def release_payload():
    return {
        "schema_version": 1,
        "template_hash_id": "1" * 32,
        "worker_commit": "a" * 40,
        "worker_archive_sha256": "b" * 64,
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.12",
        "worker_port": 8765,
    }


class WorkerReleaseTests(unittest.TestCase):
    def test_release_lock_requires_every_immutable_identity(self):
        from cloud_run.worker_release import (
            WorkerRelease,
            WorkerReleaseError,
        )

        release = WorkerRelease.from_payload(release_payload())

        self.assertEqual(release.worker_port, 8765)
        self.assertEqual(release.to_record(), release_payload())
        for field, value in (
            ("template_hash_id", "mutable"),
            ("worker_commit", "main"),
            ("worker_archive_sha256", "bad"),
            ("comfyui_frontend_version", "1.47.11"),
            ("comfyui_core_version", "latest"),
            ("python_version", "3.13"),
            ("protocol_version", "2"),
            ("worker_port", 8188),
            ("schema_version", True),
        ):
            with self.subTest(field=field):
                payload = release.to_record()
                payload[field] = value
                with self.assertRaises(WorkerReleaseError):
                    WorkerRelease.from_payload(payload)

    def test_release_lock_rejects_missing_and_unknown_fields(self):
        from cloud_run.worker_release import (
            WorkerRelease,
            WorkerReleaseError,
        )

        missing = release_payload()
        missing.pop("worker_commit")
        unknown = {**release_payload(), "mutable_tag": "latest"}

        for payload in (missing, unknown, [], None):
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(WorkerReleaseError):
                    WorkerRelease.from_payload(payload)

    def test_missing_or_non_private_release_lock_is_unavailable(self):
        from cloud_run.worker_release import (
            WorkerReleaseUnavailable,
            load_worker_release,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-release.json"
            with self.assertRaises(WorkerReleaseUnavailable):
                load_worker_release(path)

            path.write_text(json.dumps(release_payload()), encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaises(WorkerReleaseUnavailable):
                load_worker_release(path)

    def test_private_reviewed_release_lock_loads_exactly(self):
        from cloud_run.worker_release import load_worker_release

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-release.json"
            path.write_text(json.dumps(release_payload()), encoding="utf-8")
            os.chmod(path, 0o600)

            loaded = load_worker_release(path)

        self.assertEqual(loaded.to_record(), release_payload())


if __name__ == "__main__":
    unittest.main()

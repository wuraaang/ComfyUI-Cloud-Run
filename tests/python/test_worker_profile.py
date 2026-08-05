import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


class WorkerProfileStoreTests(unittest.TestCase):
    def setUp(self):
        from cloud_run.desktop_profile import DesktopProfileStore
        from cloud_run.job_repository import JobRepository

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source_user = self.root / "source" / "user"
        workflow_root = source_user / "default" / "workflows"
        workflow_root.mkdir(parents=True)
        (workflow_root / "canvas.json").write_text(
            json.dumps({"nodes": [{"id": 1, "title": "Initial"}]}),
            encoding="utf-8",
        )
        (source_user / "default" / "comfy.settings.json").write_text(
            '{"theme":"dark"}',
            encoding="utf-8",
        )
        source_input = self.root / "source" / "input"
        source_input.mkdir(parents=True)
        local_repository = JobRepository(self.root / "source.sqlite3")
        local_store = DesktopProfileStore(
            repository=local_repository,
            private_root=self.root / "source-private",
            clock=lambda: 100.0,
        )
        self.desktop_profile = local_store.capture(
            user_root=source_user,
            profile_name="default",
            input_root=source_input,
            bootstrap_workflow={"nodes": [{"id": 9, "type": "SaveImage"}]},
        )
        self.spec = self.desktop_profile.manifest_spec()
        self.pod_root = self.root / "pod"
        self.user_root = self.pod_root / "user" / "default"
        self.input_root = self.pod_root / "input"
        self.custom_nodes_root = self.pod_root / "custom_nodes"
        for path in (self.user_root, self.input_root, self.custom_nodes_root):
            path.mkdir(parents=True)

    def store(self, archive=None):
        from remote_worker.profile import ProfileStore

        return ProfileStore(
            state_root=self.root / "worker-profile",
            user_root=self.user_root,
            input_root=self.input_root,
            custom_nodes_root=self.custom_nodes_root,
            archive_resolver=lambda _profile: (
                archive or self.desktop_profile.archive_private_path
            ),
            clock=lambda: 200.0,
        )

    def test_apply_maps_only_managed_paths_and_snapshot_tracks_edits(self):
        unrelated = self.user_root / "workflows" / "unrelated.json"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('{"keep":true}', encoding="utf-8")
        store = self.store()

        applied = store.apply(self.spec)

        managed_workflow = (
            self.user_root / "workflows" / "cloud-vast" / "canvas.json"
        )
        bootstrap = (
            self.user_root
            / "cloud-vast-profile"
            / "bootstrap"
            / "current.json"
        )
        self.assertEqual(applied["profile_id"], "desktop-profile")
        self.assertEqual(applied["revision"], 1)
        self.assertTrue(managed_workflow.is_file())
        self.assertTrue(bootstrap.is_file())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), '{"keep":true}')
        initial = store.snapshot(after_revision=0)
        self.assertEqual(initial.revision, 1)
        self.assertIsNone(store.snapshot(after_revision=1))

        managed_workflow.write_text(
            json.dumps({"nodes": [{"id": 1, "title": "Cloud Vast"}]}),
            encoding="utf-8",
        )
        (self.user_root / "comfy.settings.json").write_text(
            '{\n  "theme": "light"\n}',
            encoding="utf-8",
        )
        changed = store.snapshot(after_revision=1)

        self.assertEqual(changed.revision, 2)
        self.assertEqual(changed.base_revision, 1)
        self.assertEqual(
            hashlib.sha256(changed.archive_path.read_bytes()).hexdigest(),
            changed.archive_sha256,
        )
        artifact = store.artifact(
            changed.profile_id,
            "profile-" + changed.archive_sha256,
        )
        self.assertEqual(artifact.path, changed.archive_path)
        self.assertEqual(artifact.size_bytes, changed.archive_size_bytes)

    def test_malicious_or_mismatched_archive_never_touches_user_tree(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            payload = b"private"
            info = tarfile.TarInfo("../escape")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        malicious = self.root / "malicious.tar.gz"
        malicious.write_bytes(raw.getvalue())
        archive_spec = replace(
            self.spec.archive,
            size_bytes=malicious.stat().st_size,
            sha256=hashlib.sha256(malicious.read_bytes()).hexdigest(),
        )
        profile = replace(self.spec, archive=archive_spec)
        store = self.store(archive=malicious)

        with self.assertRaisesRegex(Exception, "profile"):
            store.apply(profile)

        self.assertFalse((self.root / "escape").exists())
        self.assertFalse(
            (self.user_root / "workflows" / "cloud-vast" / "canvas.json").exists()
        )

    def test_bootstrap_is_consumed_once_and_never_overwrites_newer_edit(self):
        store = self.store()
        store.apply(self.spec)

        self.assertTrue(
            store.should_load_bootstrap(
                self.spec.revision,
                remote_edit_revision=self.spec.revision,
            )
        )
        store.mark_bootstrap_loaded(self.spec.revision)
        self.assertFalse(
            store.should_load_bootstrap(
                self.spec.revision,
                remote_edit_revision=self.spec.revision,
            )
        )

        reopened = self.store()
        self.assertFalse(
            reopened.should_load_bootstrap(
                self.spec.revision,
                remote_edit_revision=self.spec.revision + 1,
            )
        )

    def test_corrupt_private_state_cannot_redirect_snapshot_or_artifact_reads(self):
        store = self.store()
        store.apply(self.spec)
        outside = self.root / "outside-secret.txt"
        outside.write_text("private", encoding="utf-8")
        state = json.loads(store.state_path.read_text(encoding="utf-8"))
        state["artifacts"][0]["destination"] = str(outside)
        store.state_path.write_text(json.dumps(state), encoding="utf-8")

        with self.assertRaisesRegex(Exception, "profile state"):
            store.snapshot(after_revision=0)
        with self.assertRaisesRegex(Exception, "profile state"):
            store.artifact("desktop-profile", state["artifacts"][0]["path"])


if __name__ == "__main__":
    unittest.main()

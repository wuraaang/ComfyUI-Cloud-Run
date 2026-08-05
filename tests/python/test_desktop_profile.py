import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


def _jpeg_bytes():
    return b"\xff\xd8\xff\xe0" + b"safe-wallpaper" + b"\xff\xd9"


class DesktopProfileCaptureTests(unittest.TestCase):
    def setUp(self):
        from cloud_run.desktop_profile import DesktopProfileStore
        from cloud_run.job_repository import JobRepository

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user_root = self.root / "user"
        self.default = self.user_root / "default"
        self.workflows = self.default / "workflows"
        self.palettes = self.default / "color_palettes"
        self.input_root = self.root / "input"
        self.backgrounds = self.input_root / "backgrounds"
        self.workflows.mkdir(parents=True)
        self.palettes.mkdir(parents=True)
        self.backgrounds.mkdir(parents=True)
        self.private_root = self.root / "private-profile"
        self.repository = JobRepository(self.root / "attempts.sqlite3")
        self.store = DesktopProfileStore(
            repository=self.repository,
            private_root=self.private_root,
            profile_id="desktop-profile",
            clock=lambda: 100.0,
        )

    @staticmethod
    def _agent_panel():
        from cloud_run.manifest import ArtifactSpec, SourceSpec, UiPackageSpec

        return UiPackageSpec(
            package_id="comfyui-agent-panel",
            repository_url="https://github.com/acme/comfyui-agent-panel",
            revision="a" * 40,
            archive=ArtifactSpec(
                artifact_id="ui-agent-panel",
                kind="ui_package_archive",
                logical_name="ui-agent-panel",
                destination="custom_nodes/comfyui-agent-panel",
                size_bytes=10,
                sha256="b" * 64,
                source=SourceSpec(
                    kind="local-upload",
                    locator="local-upload:ui-agent-panel",
                ),
            ),
            web_sha256="c" * 64,
            required_capabilities=(
                "graph_read",
                "graph_edit",
                "native_run",
                "native_batch",
            ),
        )

    def _capture(self):
        return self.store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow={
                "version": 0.4,
                "nodes": [{"id": 1, "type": "KSampler"}],
            },
            ui_packages=(),
            ui_assets=(("assets/approved/theme.css", self.root / "theme.css"),),
        )

    def _capture_settings(self, settings):
        settings_path = self.default / "comfy.settings.json"
        original = json.dumps(settings)
        settings_path.write_text(original, encoding="utf-8")
        profile = self.store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow={"nodes": []},
        )
        with tarfile.open(profile.archive_private_path, mode="r:gz") as archive:
            names = archive.getnames()
            remote = json.loads(
                archive.extractfile("settings/comfy.settings.json").read()
            )
        self.assertEqual(settings_path.read_text(encoding="utf-8"), original)
        return profile, names, remote

    def test_same_origin_input_view_at_root_is_captured_content_addressably(self):
        wallpaper = self.input_root / "wallpaper.jpg"
        wallpaper.write_bytes(_jpeg_bytes())
        source = "/api/view?filename=wallpaper.jpg&type=input"

        first, names, remote = self._capture_settings(
            {"theme": "dark", "background": source}
        )
        second, second_names, second_remote = self._capture_settings(
            {"theme": "dark", "background": source}
        )

        digest = hashlib.sha256(_jpeg_bytes()).hexdigest()
        logical = f"backgrounds/{digest}.jpg"
        rewritten = (
            f"/api/view?filename=cloud-vast/backgrounds/{digest}.jpg&type=input"
        )
        self.assertIn(logical, names)
        self.assertEqual(remote["background"], rewritten)
        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(names, second_names)
        self.assertEqual(remote, second_remote)

    def test_same_origin_input_view_subfolder_is_captured_content_addressably(self):
        wallpaper = self.backgrounds / "wallpaper.webp"
        content = b"safe-webp-background"
        wallpaper.write_bytes(content)
        source = (
            "/api/view?filename=wallpaper.webp&type=input&subfolder=backgrounds"
        )

        _profile, names, remote = self._capture_settings(
            {"theme": "dark", "background": source}
        )

        digest = hashlib.sha256(content).hexdigest()
        self.assertIn(f"backgrounds/{digest}.webp", names)
        self.assertEqual(
            remote["background"],
            f"/api/view?filename=cloud-vast/backgrounds/{digest}.webp&type=input",
        )

    def test_same_origin_input_view_rejects_unsafe_forms_and_files(self):
        (self.input_root / "wallpaper.jpg").write_bytes(_jpeg_bytes())
        (self.backgrounds / "wallpaper.jpg").write_bytes(_jpeg_bytes())
        (self.input_root / "linked.jpg").symlink_to(
            self.backgrounds / "wallpaper.jpg"
        )
        (self.input_root / "linked-folder").symlink_to(self.backgrounds)

        cases = (
            (
                "duplicate keys",
                "/api/view?filename=wallpaper.jpg&filename=other.jpg&type=input",
                None,
            ),
            (
                "unknown key",
                "/api/view?filename=wallpaper.jpg&type=input&preview=true",
                None,
            ),
            (
                "output type",
                "/api/view?filename=wallpaper.jpg&type=output",
                None,
            ),
            (
                "absolute filename",
                "/api/view?filename=/tmp/wallpaper.jpg&type=input",
                None,
            ),
            (
                "encoded dot traversal",
                "/api/view?filename=wallpaper.jpg&type=input&subfolder=%2e%2e",
                None,
            ),
            (
                "encoded slash",
                "/api/view?filename=backgrounds%2Fwallpaper.jpg&type=input",
                None,
            ),
            (
                "invalid percent encoding",
                "/api/view?filename=wallpaper%2.jpg&type=input",
                None,
            ),
            (
                "raw control character",
                "/api/\nview?filename=wallpaper.jpg&type=input",
                None,
            ),
            (
                "backslash",
                "/api/view?filename=..\\wallpaper.jpg&type=input",
                None,
            ),
            (
                "unsupported suffix",
                "/api/view?filename=wallpaper.txt&type=input",
                None,
            ),
            (
                "final symlink",
                "/api/view?filename=linked.jpg&type=input",
                None,
            ),
            (
                "intermediate symlink",
                "/api/view?filename=wallpaper.jpg&type=input&subfolder=linked-folder",
                None,
            ),
            (
                "non-owned file",
                "/api/view?filename=wallpaper.jpg&type=input",
                "non_owned",
            ),
            (
                "escape outside root",
                "/api/view?filename=wallpaper.jpg&type=input&subfolder=../outside",
                None,
            ),
        )

        real_stat = os.stat

        def non_owned_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if path == "wallpaper.jpg" and kwargs.get("dir_fd") is not None:
                values = list(result)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return result

        for label, source, special in cases:
            with self.subTest(label=label):
                context = (
                    mock.patch(
                        "cloud_run.desktop_profile.os.stat",
                        side_effect=non_owned_stat,
                    )
                    if special == "non_owned"
                    else mock.patch(
                        "cloud_run.desktop_profile.os.stat",
                        wraps=real_stat,
                    )
                )
                with context:
                    _profile, names, remote = self._capture_settings(
                        {"theme": "dark", "background": source}
                    )
                self.assertNotIn("background", remote)
                self.assertFalse(
                    any(name.startswith("backgrounds/") for name in names)
                )

    def test_capture_is_allowlisted_rewritten_and_reproducible(self):
        wallpaper = self.backgrounds / "wallpaper.jpg"
        wallpaper.write_bytes(_jpeg_bytes())
        workflow = {"version": 0.4, "nodes": [{"id": 1, "type": "KSampler"}]}
        (self.workflows / "a.json").write_text(
            json.dumps(workflow), encoding="utf-8"
        )
        (self.palettes / "my-palette.json").write_text(
            json.dumps({"name": "night", "colors": {"node": "#000"}}),
            encoding="utf-8",
        )
        settings = {
            "theme": "dark",
            "background": str(wallpaper),
            "api_token": "must-not-enter-profile",
        }
        settings_path = self.default / "comfy.settings.json"
        original_settings = json.dumps(settings)
        settings_path.write_text(original_settings, encoding="utf-8")
        (self.default / "comfyui.db").write_bytes(b"database-private")
        (self.default / ".env").write_text("SECRET=value", encoding="utf-8")
        (self.default / "cache").mkdir()
        (self.default / "cache" / "item").write_text("private", encoding="utf-8")
        (self.default / "__pycache__").mkdir()
        (self.default / "__pycache__" / "x.pyc").write_bytes(b"private")
        (self.root / "outside-secret.txt").write_text("private", encoding="utf-8")
        (self.root / "theme.css").write_text("body { color: white; }", encoding="utf-8")

        first = self._capture()
        second = self._capture()

        self.assertEqual(first, second)
        self.assertEqual(first.revision, 1)
        self.assertIsNone(first.base_revision)
        self.assertEqual(settings_path.read_text(encoding="utf-8"), original_settings)
        self.assertEqual(
            self.store.archive_path(first).read_bytes(),
            self.store.archive_path(second).read_bytes(),
        )
        with tarfile.open(self.store.archive_path(first), mode="r:gz") as archive:
            names = archive.getnames()
            contents = {
                name: archive.extractfile(name).read()
                for name in names
            }
        background_digest = hashlib.sha256(_jpeg_bytes()).hexdigest()
        expected_background = "backgrounds/" + background_digest + ".jpg"
        self.assertEqual(
            names,
            sorted(
                [
                    "assets/approved/theme.css",
                    expected_background,
                    "bootstrap/current.json",
                    "palettes/my-palette.json",
                    "settings/comfy.settings.json",
                    "workflows/a.json",
                ]
            ),
        )
        remote_settings = json.loads(contents["settings/comfy.settings.json"])
        self.assertEqual(remote_settings["theme"], "dark")
        self.assertNotIn("api_token", remote_settings)
        self.assertEqual(
            remote_settings["background"],
            "/api/view?filename=cloud-vast/backgrounds/"
            + background_digest
            + ".jpg&type=input",
        )
        rendered = repr((first, names, contents))
        for forbidden in (
            "comfyui.db",
            ".env",
            "__pycache__",
            "outside-secret",
            "must-not-enter-profile",
            str(self.root),
        ):
            self.assertNotIn(forbidden, rendered)
        registered = self.repository.get_local_artifact(
            "profile-" + first.archive_sha256
        )
        self.assertEqual(registered.sha256, first.archive_sha256)

    def test_duplicate_json_symlink_and_unsafe_asset_fail_closed(self):
        (self.workflows / "a.json").write_text(
            '{"nodes":[],"nodes":[1]}', encoding="utf-8"
        )
        (self.root / "theme.css").write_text("safe", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "profile"):
            self._capture()

        (self.workflows / "a.json").write_text('{"nodes":[]}', encoding="utf-8")
        (self.workflows / "linked.json").symlink_to(self.root / "outside-secret.txt")
        (self.root / "outside-secret.txt").write_text("private", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "profile"):
            self._capture()

        (self.workflows / "linked.json").unlink()
        with self.assertRaisesRegex(Exception, "profile"):
            self.store.capture(
                user_root=self.user_root,
                profile_name="default",
                input_root=self.input_root,
                bootstrap_workflow={"nodes": []},
                ui_packages=(),
                ui_assets=(("assets/approved/payload.js", self.root / "theme.css"),),
            )

    def test_agent_panel_settings_are_rewritten_only_in_remote_profile(self):
        original = {
            "theme": "dark",
            "comfyui-mcp.bridgeUrl.single": "ws://127.0.0.1:9180",
            "comfyui-mcp.remoteComfyuiUrl": "http://127.0.0.1:8188",
        }
        settings_path = self.default / "comfy.settings.json"
        settings_path.write_text(json.dumps(original), encoding="utf-8")
        profile = self.store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow={"nodes": []},
            ui_packages=(self._agent_panel(),),
        )

        with tarfile.open(profile.archive_private_path, mode="r:gz") as archive:
            remote = json.loads(
                archive.extractfile("settings/comfy.settings.json").read()
            )
        self.assertEqual(remote["theme"], "dark")
        self.assertEqual(
            remote["comfyui-mcp.bridgeUrl.single"],
            "/cloud-run/api/agent/ws",
        )
        self.assertEqual(remote["comfyui-mcp.remoteComfyuiUrl"], "")
        self.assertEqual(
            json.loads(settings_path.read_text(encoding="utf-8")),
            original,
        )

    def test_agent_panel_remote_settings_exist_without_creating_local_settings(self):
        settings_path = self.default / "comfy.settings.json"
        self.assertFalse(settings_path.exists())

        profile = self.store.capture(
            user_root=self.user_root,
            profile_name="default",
            input_root=self.input_root,
            bootstrap_workflow={"nodes": []},
            ui_packages=(self._agent_panel(),),
        )

        with tarfile.open(profile.archive_private_path, mode="r:gz") as archive:
            remote = json.loads(
                archive.extractfile("settings/comfy.settings.json").read()
            )
        self.assertEqual(
            remote,
            {
                "comfyui-mcp.bridgeUrl.single": "/cloud-run/api/agent/ws",
                "comfyui-mcp.remoteComfyuiUrl": "",
            },
        )
        self.assertFalse(settings_path.exists())


class DesktopProfileConflictTests(unittest.TestCase):
    def test_concurrent_descendants_preserve_both_sides_and_resolution(self):
        from cloud_run.desktop_profile import DesktopProfileStore
        from cloud_run.job_repository import JobRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)

        def tree(name, value):
            user = root / name / "user"
            workflow_root = user / "default" / "workflows"
            workflow_root.mkdir(parents=True)
            (workflow_root / "canvas.json").write_text(
                json.dumps({"nodes": [{"id": 1, "title": value}]}),
                encoding="utf-8",
            )
            inputs = root / name / "input"
            inputs.mkdir(parents=True)
            return user, inputs

        repository = JobRepository(root / "attempts.sqlite3")
        local_store = DesktopProfileStore(
            repository=repository,
            private_root=root / "local-private",
            profile_id="desktop-profile",
            clock=lambda: 100.0,
        )
        user, inputs = tree("local", "base")
        base = local_store.capture(
            user_root=user,
            profile_name="default",
            input_root=inputs,
            bootstrap_workflow={"nodes": []},
        )
        (user / "default" / "workflows" / "canvas.json").write_text(
            json.dumps({"nodes": [{"id": 1, "title": "Local"}]}),
            encoding="utf-8",
        )
        local = local_store.capture(
            user_root=user,
            profile_name="default",
            input_root=inputs,
            bootstrap_workflow={"nodes": []},
        )

        remote_repository = JobRepository(root / "remote.sqlite3")
        remote_store = DesktopProfileStore(
            repository=remote_repository,
            private_root=root / "remote-private",
            profile_id="desktop-profile",
            clock=lambda: 101.0,
        )
        remote_user, remote_inputs = tree("remote", "Cloud Vast")
        remote_initial = remote_store.capture(
            user_root=remote_user,
            profile_name="default",
            input_root=remote_inputs,
            bootstrap_workflow={"nodes": []},
        )
        remote = replace(
            remote_initial,
            revision=base.revision + 1,
            base_revision=base.revision,
        )

        remote_payload = remote.public_payload()
        remote_payload["archive_artifact_id"] = (
            "profile-" + remote.archive_sha256
        )
        conflict = local_store.apply_remote_payload(
            remote_payload,
            remote.archive_private_path.read_bytes(),
        )

        self.assertEqual(conflict.local_label, "Local")
        self.assertEqual(conflict.remote_label, "Cloud Vast")
        self.assertEqual(conflict.local_revision, local.revision)
        self.assertGreater(conflict.remote_revision, local.revision)
        resolved = local_store.resolve_conflict(
            conflict.conflict_id,
            winner="cloud_vast",
        )
        self.assertGreater(resolved.revision, conflict.remote_revision)
        self.assertEqual(
            resolved.archive_sha256,
            remote.archive_sha256,
        )
        self.assertEqual(
            local_store.conflicts(unresolved_only=False)[0].resolved_revision,
            resolved.revision,
        )


if __name__ == "__main__":
    unittest.main()

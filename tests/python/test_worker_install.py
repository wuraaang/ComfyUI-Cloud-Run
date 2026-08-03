import asyncio
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cloud_run.manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    PythonWheelSpec,
    SourceSpec,
    UiPackageSpec,
)


REVISION = "a" * 40


def write_tar(path, entries):
    with tarfile.open(path, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for entry in entries:
            name = entry["name"]
            kind = entry.get("kind", "file")
            info = tarfile.TarInfo(name)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            if kind == "file":
                payload = entry.get("payload", b"content")
                info.type = tarfile.REGTYPE
                info.mode = entry.get("mode", 0o644)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.size = 0
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.mode = 0o777
                info.size = 0
                info.linkname = entry["target"]
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.mode = 0o644
                info.size = 0
                info.linkname = entry["target"]
                archive.addfile(info)
            else:
                info.type = tarfile.CHRTYPE
                info.mode = 0o600
                info.size = 0
                archive.addfile(info)
    return path


class RecordingRunner:
    def __init__(self, *, return_code=0):
        self.calls = []
        self.shell_used = False
        self.return_code = return_code

    async def run(self, argv):
        self.calls.append(list(argv))
        return self.return_code


class WorkerInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.custom_nodes = self.root / "comfy" / "custom_nodes"
        self.wheels = self.root / "worker" / "wheels"
        self.artifacts = self.root / "worker" / "artifacts"
        self.custom_nodes.mkdir(parents=True)
        self.wheels.mkdir(parents=True)
        self.artifacts.mkdir(parents=True)

    def node_spec(
        self,
        *,
        entries=None,
        wheel_filename="dep-1.0-py3-none-any.whl",
        archive_digest=None,
        wheel_digest=None,
    ):
        entries = entries or (
            {"name": "__init__.py", "payload": b"NODE = True\n"},
            {
                "name": "assets/runner",
                "payload": b"binary",
                "mode": 0o755,
            },
        )
        archive_id = "fancy-archive"
        archive_path = self.artifacts / f"{archive_id}.tar"
        write_tar(archive_path, entries)
        archive_payload = archive_path.read_bytes()

        wheel_path = self.wheels / wheel_filename
        if "/" not in wheel_filename and "\\" not in wheel_filename:
            wheel_path.write_bytes(b"verified-wheel")
        wheel = PythonWheelSpec(
            filename=wheel_filename,
            size_bytes=len(b"verified-wheel"),
            sha256=wheel_digest
            or hashlib.sha256(b"verified-wheel").hexdigest(),
            source=SourceSpec(
                kind="local-upload",
                locator="local-upload:wheel-1",
            ),
        )
        archive = ArtifactSpec(
            artifact_id=archive_id,
            kind="custom_node_archive",
            logical_name="fancy",
            destination="custom_nodes/fancy",
            size_bytes=len(archive_payload),
            sha256=archive_digest
            or hashlib.sha256(archive_payload).hexdigest(),
            source=SourceSpec(
                kind="local-upload",
                locator="local-upload:fancy-archive",
            ),
        )
        return CustomNodeSpec(
            package_id="fancy",
            repository_url="https://github.com/example/fancy",
            revision=REVISION,
            archive=archive,
            wheels=(wheel,),
            provided_class_types=("FancyNode",),
        )

    def installer(self, runner):
        from remote_worker.install import CustomNodeInstaller

        return CustomNodeInstaller(
            custom_nodes_root=self.custom_nodes,
            wheel_root=self.wheels,
            artifact_root=self.artifacts,
            runner=runner,
        )

    def ui_package_spec(self):
        archive_id = "agent-panel-archive"
        archive_path = self.artifacts / f"{archive_id}.tar"
        write_tar(
            archive_path,
            (
                {"name": "__init__.py", "payload": b"WEB_DIRECTORY = 'web'\n"},
                {"name": "web/panel.js", "payload": b"export const panel = true;\n"},
            ),
        )
        payload = archive_path.read_bytes()
        return UiPackageSpec(
            package_id="agent-panel",
            repository_url="https://github.com/example/agent-panel",
            revision=REVISION,
            archive=ArtifactSpec(
                artifact_id=archive_id,
                kind="ui_package_archive",
                logical_name="agent-panel",
                destination="custom_nodes/agent-panel",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                source=SourceSpec(
                    kind="local-upload",
                    locator=f"local-upload:{archive_id}",
                ),
            ),
            web_sha256=hashlib.sha256(b"export const panel = true;\n").hexdigest(),
            required_capabilities=("graph_read", "native_run"),
        )

    def test_install_extracts_confined_archive_and_uses_no_shell(self):
        runner = RecordingRunner()
        spec = self.node_spec()
        installer = self.installer(runner)

        result = asyncio.run(installer.install(spec))

        self.assertEqual(result.package_id, "fancy")
        self.assertEqual(result.revision, REVISION)
        self.assertEqual(
            result.destination,
            (self.custom_nodes / "fancy").resolve(),
        )
        self.assertEqual(
            (result.destination / "__init__.py").read_bytes(),
            b"NODE = True\n",
        )
        self.assertEqual(
            runner.calls,
            [
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--disable-pip-version-check",
                    str(
                        self.wheels.resolve()
                        / "dep-1.0-py3-none-any.whl"
                    ),
                ]
            ],
        )
        self.assertFalse(runner.shell_used)
        self.assertFalse(
            any(
                item.name.startswith(".fancy-")
                for item in self.custom_nodes.iterdir()
            )
        )

    def test_ui_only_package_installs_without_graph_classes_or_pip(self):
        runner = RecordingRunner()
        spec = self.ui_package_spec()

        result = asyncio.run(
            self.installer(runner).install_ui_package(spec)
        )

        self.assertEqual(result.package_id, "agent-panel")
        self.assertEqual(result.wheels, ())
        self.assertEqual(
            (result.destination / "web" / "panel.js").read_bytes(),
            b"export const panel = true;\n",
        )
        self.assertEqual(runner.calls, [])

    def test_archive_traversal_links_devices_and_bad_metadata_fail_closed(self):
        from remote_worker.install import InstallError, safe_extract

        cases = (
            ({"name": "../escape", "payload": b"x"},),
            (
                {
                    "name": "escape-link",
                    "kind": "symlink",
                    "target": "../../escape",
                },
            ),
            (
                {
                    "name": "hard-link",
                    "kind": "hardlink",
                    "target": "target",
                },
            ),
            ({"name": "device", "kind": "device"},),
            ({"name": "bad-mode", "payload": b"x", "mode": 0o666},),
        )
        for index, entries in enumerate(cases):
            with self.subTest(index=index):
                archive = write_tar(
                    self.root / f"unsafe-{index}.tar",
                    entries,
                )
                destination = self.root / f"extract-{index}"
                with self.assertRaises(InstallError):
                    safe_extract(archive, destination)
                self.assertFalse(destination.exists())
        self.assertFalse((self.root.parent / "escape").exists())

    def test_wrong_archive_wheel_or_unlisted_wheel_never_runs_pip(self):
        from remote_worker.install import InstallError

        cases = (
            self.node_spec(archive_digest="0" * 64),
            self.node_spec(wheel_digest="0" * 64),
            self.node_spec(wheel_filename="../unlisted.whl"),
        )
        for index, spec in enumerate(cases):
            with self.subTest(index=index):
                runner = RecordingRunner()
                with self.assertRaises(InstallError):
                    asyncio.run(self.installer(runner).install(spec))
                self.assertEqual(runner.calls, [])
                self.assertFalse((self.custom_nodes / "fancy").exists())

    def test_failed_fixed_argv_install_preserves_existing_node(self):
        from remote_worker.install import InstallError

        existing = self.custom_nodes / "fancy"
        existing.mkdir()
        marker = existing / "user-marker"
        marker.write_text("preserved", encoding="utf-8")
        runner = RecordingRunner(return_code=1)

        with self.assertRaises(InstallError):
            asyncio.run(self.installer(runner).install(self.node_spec()))

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserved")

    def test_archive_destination_must_match_package_identity(self):
        from remote_worker.install import InstallError

        spec = self.node_spec()
        mismatched = replace(
            spec,
            archive=replace(
                spec.archive,
                destination="custom_nodes/another-package",
            ),
        )
        with self.assertRaises(InstallError):
            asyncio.run(
                self.installer(RecordingRunner()).install(mismatched)
            )

    def test_manifest_delta_can_install_only_verified_new_wheels(self):
        runner = RecordingRunner()
        spec = self.node_spec()
        installer = self.installer(runner)

        installed = asyncio.run(
            installer.install_wheels(spec.wheels)
        )

        self.assertEqual(
            installed,
            ("dep-1.0-py3-none-any.whl",),
        )
        self.assertEqual(len(runner.calls), 1)
        self.assertFalse((self.custom_nodes / "fancy").exists())


if __name__ == "__main__":
    unittest.main()

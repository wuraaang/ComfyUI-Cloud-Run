import asyncio
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from cloud_run.manifest import (
    ArtifactSpec,
    CustomNodeSpec,
    PythonWheelSpec,
    SourceSpec,
    UiPackageSpec,
)


REVISION = "a" * 40
SIMPLEEVAL_FILENAME = "simpleeval-1.0.7-py3-none-any.whl"
SIMPLEEVAL_SIZE_BYTES = 18_792
SIMPLEEVAL_SHA256 = (
    "97ac271bfd8f2af9e7b9a36ceea67617f26fa873f9d5ae1922f64d4c1442534b"
)
SIMPLEEVAL_SOURCE = f"local-upload:wheel-simpleeval-{SIMPLEEVAL_SHA256}"
SAFE_UI_LOADER = (
    b"NODE_CLASS_MAPPINGS = {}\n"
    b"NODE_DISPLAY_NAME_MAPPINGS = {}\n"
    b'WEB_DIRECTORY = "./web"\n\n'
    b"__all__ = [\"NODE_CLASS_MAPPINGS\", "
    b'"NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]\n'
)


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


def canonical_web_digest(entries):
    records = []
    for entry in entries:
        name = entry["name"]
        if entry.get("kind", "file") != "file" or not name.startswith(
            "web/"
        ):
            continue
        payload = entry.get("payload", b"content")
        records.append(
            {
                "mode": 0o644,
                "path": name.removeprefix("web/"),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    body = json.dumps(
        sorted(records, key=lambda item: item["path"]),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


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

    def ui_package_spec(
        self,
        *,
        entries=None,
        web_digest=None,
        package_id="agent-panel",
        revision=REVISION,
    ):
        archive_id = package_id + "-archive"
        archive_path = self.artifacts / f"{archive_id}.tar"
        entries = entries or (
            {"name": "__init__.py", "payload": SAFE_UI_LOADER},
            {
                "name": "web/panel.js",
                "payload": b"export const panel = true;\n",
            },
        )
        write_tar(archive_path, entries)
        payload = archive_path.read_bytes()
        return UiPackageSpec(
            package_id=package_id,
            repository_url="https://github.com/example/" + package_id,
            revision=revision,
            archive=ArtifactSpec(
                artifact_id=archive_id,
                kind="ui_package_archive",
                logical_name=package_id,
                destination="custom_nodes/" + package_id,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                source=SourceSpec(
                    kind="local-upload",
                    locator=f"local-upload:{archive_id}",
                ),
            ),
            web_sha256=web_digest or canonical_web_digest(entries),
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
                    "--no-deps",
                    "--disable-pip-version-check",
                    "--no-compile",
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

    def test_ui_install_returns_measured_canonical_web_digest(self):
        entries = (
            {"name": "__init__.py", "payload": SAFE_UI_LOADER},
            {
                "name": "web/z-last.js",
                "payload": b"export const z = true;\n",
                "mode": 0o755,
            },
            {
                "name": "web/a-first.css",
                "payload": b"body { color: #fff; }\n",
            },
        )
        expected = canonical_web_digest(entries)

        result = asyncio.run(
            self.installer(RecordingRunner()).install_ui_package(
                self.ui_package_spec(entries=entries)
            )
        )

        self.assertEqual(result.web_sha256, expected)
        self.assertEqual(
            result.extension_paths,
            (
                "/extensions/agent-panel/a-first.css",
                "/extensions/agent-panel/z-last.js",
            ),
        )

    def test_shipped_hermes_ui_identity_installs_exactly(self):
        repository_root = Path(__file__).parents[2]
        assets = repository_root / "cloud_run/baseline_assets/hermes-nous"
        lock = json.loads(
            (
                repository_root
                / "cloud_run/certified_baseline.lock.json"
            ).read_text(encoding="utf-8")
        )
        hermes = next(
            item
            for item in lock["ui_packages"]
            if item["package_id"] == "hermes-nous"
        )
        entries = tuple(
            {
                "name": path.relative_to(assets).as_posix(),
                "payload": path.read_bytes(),
            }
            for path in sorted(assets.rglob("*"))
            if path.is_file()
        )
        spec = self.ui_package_spec(
            entries=entries,
            web_digest=hermes["web"]["sha256"],
            package_id="hermes-nous",
            revision=hermes["revision"],
        )

        result = asyncio.run(
            self.installer(RecordingRunner()).install_ui_package(spec)
        )

        self.assertEqual(result.web_sha256, hermes["web"]["sha256"])
        self.assertEqual(
            result.extension_paths,
            (
                "/extensions/hermes-nous/hermes-nous.css",
                "/extensions/hermes-nous/hermes-nous.js",
            ),
        )

    def test_installed_ui_remeasurement_rejects_content_drift(self):
        from remote_worker.install import InstallError

        installer = self.installer(RecordingRunner())
        spec = self.ui_package_spec()
        result = asyncio.run(installer.install_ui_package(spec))
        self.assertTrue(
            hasattr(installer, "measure_ui_package"),
            "installer must expose installed UI remeasurement",
        )
        (result.destination / "web" / "panel.js").write_bytes(
            b"export const panel = false;\n"
        )

        with self.assertRaises(InstallError):
            installer.measure_ui_package(spec)

    def test_node_remeasurement_rejects_runtime_python_drift(self):
        from remote_worker.install import InstallError

        entries = (
            {"name": "__init__.py", "payload": b"from . import runtime\n"},
            {"name": "runtime.py", "payload": b"VALUE = 1\n"},
        )
        installer = self.installer(RecordingRunner())
        spec = self.node_spec(entries=entries)
        result = asyncio.run(installer.install(spec))
        (result.destination / "runtime.py").write_bytes(b"VALUE = 2\n")

        with self.assertRaises(InstallError):
            installer.measure_node(spec)

    def test_node_remeasurement_rejects_symlink_files_and_fifos(self):
        from remote_worker.install import InstallError

        installer = self.installer(RecordingRunner())
        spec = self.node_spec()
        outside = self.root / "outside-runtime.py"
        outside.write_bytes(b"outside\n")
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                result = asyncio.run(installer.install(spec))
                unexpected = result.destination / "unexpected"
                if kind == "symlink":
                    unexpected.symlink_to(outside)
                else:
                    os.mkfifo(unexpected)
                with self.assertRaises(InstallError):
                    installer.measure_node(spec)

    def test_ui_remeasurement_rejects_an_extra_symlink_directory(self):
        from remote_worker.install import InstallError

        installer = self.installer(RecordingRunner())
        spec = self.ui_package_spec()
        result = asyncio.run(installer.install_ui_package(spec))
        outside = self.root / "outside-ui"
        outside.mkdir()
        (outside / "ignored.js").write_bytes(b"export {};\n")
        (result.destination / "web" / "ignored").symlink_to(
            outside,
            target_is_directory=True,
        )

        with self.assertRaises(InstallError):
            installer.measure_ui_package(spec)

    def test_remeasurement_rejects_world_writable_package_roots(self):
        from remote_worker.install import InstallError

        for kind in ("node", "ui"):
            with self.subTest(kind=kind):
                installer = self.installer(RecordingRunner())
                if kind == "node":
                    spec = self.node_spec()
                    result = asyncio.run(installer.install(spec))
                    measure = installer.measure_node
                else:
                    spec = self.ui_package_spec()
                    result = asyncio.run(
                        installer.install_ui_package(spec)
                    )
                    measure = installer.measure_ui_package
                os.chmod(result.destination, 0o777)

                with self.assertRaises(InstallError):
                    measure(spec)

    def test_install_normalizes_implicit_directories_under_private_umask(self):
        installer = self.installer(RecordingRunner())
        node = self.node_spec()
        ui = self.ui_package_spec()
        previous_umask = os.umask(0o077)
        try:
            node_result = asyncio.run(installer.install(node))
            ui_result = asyncio.run(installer.install_ui_package(ui))
        finally:
            os.umask(previous_umask)

        for directory in (
            node_result.destination,
            node_result.destination / "assets",
            ui_result.destination,
            ui_result.destination / "web",
        ):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
        self.assertEqual(installer.measure_node(node), node_result)
        self.assertEqual(installer.measure_ui_package(ui), ui_result)

    def test_runtime_identity_measures_torch_and_aiohttp_distributions(self):
        installer = self.installer(RecordingRunner())
        self.assertTrue(
            hasattr(installer, "runtime_identity"),
            "installer must expose protected runtime identity",
        )

        versions = {"aiohttp": "3.12.15", "torch": "2.4.1"}
        with patch(
            "remote_worker.install.importlib_metadata.version",
            side_effect=versions.__getitem__,
        ):
            identity = installer.runtime_identity()

        self.assertEqual(set(identity), {"aiohttp", "torch"})
        self.assertEqual(identity, versions)

    def test_ui_install_rejects_manifest_web_digest_mismatch(self):
        from remote_worker.install import InstallError

        spec = self.ui_package_spec(web_digest="0" * 64)

        with self.assertRaises(InstallError):
            asyncio.run(
                self.installer(RecordingRunner()).install_ui_package(spec)
            )
        self.assertFalse((self.custom_nodes / "agent-panel").exists())

    def test_ui_install_rejects_unreviewed_executable_path(self):
        from remote_worker.install import InstallError

        entries = (
            {"name": "__init__.py", "payload": SAFE_UI_LOADER},
            {"name": "web/panel.js", "payload": b"export {};\n"},
            {"name": "py/backend.py", "payload": b"raise SystemExit\n"},
        )

        with self.assertRaises(InstallError):
            asyncio.run(
                self.installer(RecordingRunner()).install_ui_package(
                    self.ui_package_spec(entries=entries)
                )
            )
        self.assertFalse((self.custom_nodes / "agent-panel").exists())

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

    def test_efficiency_wheels_install_offline_without_dependency_resolution(self):
        from remote_worker.install import InstallError

        runner = RecordingRunner()
        spec = self.node_spec(
            entries=(
                {
                    "name": "__init__.py",
                    "payload": b'WEB_DIRECTORY = "./js"\n',
                },
                {
                    "name": "js/efficiency.js",
                    "payload": b"export const efficiency = true;\n",
                },
            ),
            wheel_filename=SIMPLEEVAL_FILENAME,
        )
        spec = replace(
            spec,
            package_id="efficiency-nodes-comfyui",
            archive=replace(
                spec.archive,
                destination="custom_nodes/efficiency-nodes-comfyui",
            ),
            wheels=(
                replace(
                    spec.wheels[0],
                    size_bytes=SIMPLEEVAL_SIZE_BYTES,
                    sha256=SIMPLEEVAL_SHA256,
                    source=SourceSpec(
                        kind="local-upload",
                        locator=SIMPLEEVAL_SOURCE,
                    ),
                ),
            ),
        )

        with patch(
            "remote_worker.install._verified_file",
            side_effect=lambda path, **_kwargs: path,
        ):
            asyncio.run(self.installer(runner).install(spec))

        self.assertEqual(
            runner.calls,
            [
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--disable-pip-version-check",
                    "--no-compile",
                    str(
                        self.wheels.resolve()
                        / SIMPLEEVAL_FILENAME
                    ),
                ]
            ],
        )

        empty_runner = RecordingRunner()
        with self.assertRaises(InstallError):
            asyncio.run(
                self.installer(empty_runner).install(
                    replace(spec, wheels=())
                )
            )
        self.assertEqual(empty_runner.calls, [])

    def test_efficiency_rejects_non_locked_or_duplicate_simpleeval_identity(self):
        from remote_worker.install import InstallError

        spec = self.node_spec(
            entries=(
                {"name": "__init__.py", "payload": b'WEB_DIRECTORY = "./js"\n'},
                {"name": "js/efficiency.js", "payload": b"export {};\n"},
            ),
            wheel_filename=SIMPLEEVAL_FILENAME,
        )
        spec = replace(
            spec,
            package_id="efficiency-nodes-comfyui",
            archive=replace(
                spec.archive,
                destination="custom_nodes/efficiency-nodes-comfyui",
            ),
            wheels=(
                replace(
                    spec.wheels[0],
                    size_bytes=SIMPLEEVAL_SIZE_BYTES,
                    sha256=SIMPLEEVAL_SHA256,
                    source=SourceSpec(
                        kind="local-upload",
                        locator=SIMPLEEVAL_SOURCE,
                    ),
                ),
            ),
        )
        second = replace(
            spec.wheels[0],
            filename="simpleeval-1.0.8-py3-none-any.whl",
        )
        cases = (
            replace(spec, wheels=(spec.wheels[0], second)),
            replace(
                spec,
                wheels=(
                    replace(
                        spec.wheels[0],
                        size_bytes=SIMPLEEVAL_SIZE_BYTES + 1,
                    ),
                ),
            ),
        )
        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                runner = RecordingRunner()
                with patch(
                    "remote_worker.install._verified_file",
                    side_effect=lambda path, **_kwargs: path,
                ):
                    with self.assertRaises(InstallError):
                        asyncio.run(self.installer(runner).install(candidate))
                self.assertEqual(runner.calls, [])

    def test_protected_core_wheel_is_rejected_before_pip(self):
        from remote_worker.install import InstallError

        runner = RecordingRunner()
        spec = self.node_spec(
            wheel_filename="torch-2.4.1-py3-none-any.whl"
        )

        with self.assertRaises(InstallError):
            asyncio.run(self.installer(runner).install_wheels(spec.wheels))
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()

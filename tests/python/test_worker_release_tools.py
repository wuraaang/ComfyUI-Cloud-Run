import dataclasses
import base64
import gzip
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from remote_worker.bootstrap import MAX_ARCHIVE_BYTES
from scripts.build_worker_artifact import WorkerArtifact, build_worker_artifact
from scripts.build_worker_release_bundle import (
    ReleaseBuildError,
    ReleaseMetadata,
    build_worker_release_bundle,
)
from scripts.render_worker_template import (
    TemplateRenderError,
    render_worker_template,
)
from scripts.write_worker_release_lock import (
    LockWriteError,
    write_worker_release_lock,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKER_COMMIT = "a" * 40
OFFICIAL_IMAGE = (
    "docker.io/vastai/comfy@sha256:"
    "9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c"
)
OFFICIAL_TAG = "v0.29.0-cuda-12.9-py312"
EXTRA_FILTERS = {
    "gpu_arch": {"eq": "nvidia"},
    "cpu_arch": {"eq": "amd64"},
    "cuda_max_good": {"gte": 12.9},
    "compute_cap": {"gte": 750},
    "num_gpus": {"eq": 1},
}


def release_metadata():
    digest = "b" * 64
    tag = "worker-v1-" + WORKER_COMMIT
    asset_name = (
        "comfyui-cloud-run-worker-"
        + WORKER_COMMIT
        + "-"
        + digest
        + ".tar.gz"
    )
    return ReleaseMetadata(
        schema_version=1,
        repository="wuraaang/ComfyUI-Cloud-Run",
        worker_commit=WORKER_COMMIT,
        tag=tag,
        asset_name=asset_name,
        archive_url=(
            "https://github.com/wuraaang/ComfyUI-Cloud-Run/"
            "releases/download/"
            + tag
            + "/"
            + asset_name
        ),
        worker_archive_size_bytes=12345,
        worker_archive_sha256=digest,
        protocol_version="2",
        comfyui_core_version="0.29.0",
        comfyui_frontend_version="1.47.10",
        python_version="3.12",
        destination="/opt/comfyui-cloud-run",
    )


def base_template_audit():
    return {
        "schema_version": 1,
        "hash_id": "027fba7753c024be019030fb42aed900",
        "use_ssh": True,
        "ssh_direct": True,
    }


class WorkerReleaseToolTests(unittest.TestCase):
    def test_rendered_onstart_reuses_only_exact_verified_install(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "render"
            output.mkdir(mode=0o700)
            rendered = render_worker_template(
                REPOSITORY_ROOT,
                output,
                release_metadata(),
                base_template_audit(),
            )

        self.assertIn(
            "mkdir -p -m 0700 /opt/comfyui-cloud-run-bootstrap",
            rendered.onstart,
        )
        self.assertNotIn(
            "mkdir -m 0700 /opt/comfyui-cloud-run-bootstrap",
            rendered.onstart,
        )
        self.assertIn(
            "exec /venv/main/bin/python "
            "/opt/comfyui-cloud-run-bootstrap/bootstrap.py "
            "/opt/comfyui-cloud-run-bootstrap/release-lock.json",
            rendered.onstart,
        )

    def test_worker_artifact_ignores_generated_bytecode_cache(self):
        cache = REPOSITORY_ROOT / "remote_worker" / "__pycache__"
        cache_preexisted = cache.exists()
        cache.mkdir(exist_ok=True)
        bytecode = cache / "jobs.cpython-313.pyc"
        previous = bytecode.read_bytes() if bytecode.exists() else None
        bytecode.write_bytes(b"generated")
        try:
            with tempfile.TemporaryDirectory() as temporary_root:
                result = build_worker_artifact(
                    REPOSITORY_ROOT,
                    Path(temporary_root) / "worker.tar.gz",
                )
            self.assertNotIn(
                "remote_worker/__pycache__/jobs.cpython-313.pyc",
                result.members,
            )
            self.assertIn("remote_worker/native_proxy.py", result.members)
        finally:
            if previous is None:
                bytecode.unlink(missing_ok=True)
            else:
                bytecode.write_bytes(previous)
            if not cache_preexisted:
                cache.rmdir()


class ReleaseBundleTests(unittest.TestCase):
    def _private_directory(self, root, name):
        path = Path(root) / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        return path

    def test_bundle_identity_and_bytes_are_deterministic(self):
        with tempfile.TemporaryDirectory() as first_root, \
             tempfile.TemporaryDirectory() as second_root:
            first_output = self._private_directory(first_root, "release")
            first = build_worker_release_bundle(
                REPOSITORY_ROOT,
                first_output,
                WORKER_COMMIT,
            )
            second_output = self._private_directory(second_root, "release")
            second = build_worker_release_bundle(
                REPOSITORY_ROOT,
                second_output,
                WORKER_COMMIT,
            )

            self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
            self.assertEqual(first.metadata, second.metadata)
            self.assertEqual(first.metadata.tag, "worker-v1-" + WORKER_COMMIT)
            self.assertEqual(
                first.metadata.asset_name,
                "comfyui-cloud-run-worker-"
                + WORKER_COMMIT
                + "-"
                + first.metadata.worker_archive_sha256
                + ".tar.gz",
            )
            self.assertEqual(
                first.metadata.archive_url,
                "https://github.com/wuraaang/ComfyUI-Cloud-Run/"
                "releases/download/"
                + first.metadata.tag
                + "/"
                + first.metadata.asset_name,
            )
            self.assertEqual(first.archive.name, first.metadata.asset_name)
            self.assertEqual(first.metadata_path.name, "release-metadata.json")

    def test_metadata_is_exact_sanitized_compact_private_json(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "release")
            bundle = build_worker_release_bundle(
                REPOSITORY_ROOT,
                output,
                WORKER_COMMIT,
            )

            expected_fields = (
                "schema_version",
                "repository",
                "worker_commit",
                "tag",
                "asset_name",
                "archive_url",
                "worker_archive_size_bytes",
                "worker_archive_sha256",
                "protocol_version",
                "comfyui_core_version",
                "comfyui_frontend_version",
                "python_version",
                "destination",
            )
            self.assertEqual(
                tuple(field.name for field in dataclasses.fields(ReleaseMetadata)),
                expected_fields,
            )
            expected = (
                json.dumps(
                    bundle.metadata.to_record(),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            self.assertEqual(bundle.metadata_path.read_bytes(), expected)
            self.assertEqual(
                os.stat(bundle.metadata_path).st_mode & 0o777,
                0o600,
            )
            self.assertNotIn(str(output), expected.decode("utf-8"))

    def test_builder_rejects_non_exact_worker_commits(self):
        invalid_commits = (True, False, "A" * 40, "a" * 39, "a" * 41)
        with tempfile.TemporaryDirectory() as root:
            for index, worker_commit in enumerate(invalid_commits):
                output = self._private_directory(root, f"release-{index}")
                with self.subTest(worker_commit=worker_commit):
                    with self.assertRaises(ReleaseBuildError):
                        build_worker_release_bundle(
                            REPOSITORY_ROOT,
                            output,
                            worker_commit,
                        )

    def test_builder_rejects_symlink_and_non_private_output_directories(self):
        with tempfile.TemporaryDirectory() as root:
            private = self._private_directory(root, "private")
            symlink = Path(root) / "symlink"
            symlink.symlink_to(private, target_is_directory=True)
            public = Path(root) / "public"
            public.mkdir(mode=0o755)
            os.chmod(public, 0o755)

            for output in (symlink, public):
                with self.subTest(output=output.name):
                    with self.assertRaises(ReleaseBuildError):
                        build_worker_release_bundle(
                            REPOSITORY_ROOT,
                            output,
                            WORKER_COMMIT,
                        )

    def test_builder_rejects_an_oversized_worker_archive(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "release")

            def oversized_artifact(_repository_root, output_path):
                path = Path(output_path)
                path.write_bytes(b"x")
                os.chmod(path, 0o600)
                return WorkerArtifact(
                    path=path,
                    size_bytes=MAX_ARCHIVE_BYTES + 1,
                    sha256="b" * 64,
                    members=(),
                )

            with mock.patch(
                "scripts.build_worker_release_bundle.build_worker_artifact",
                side_effect=oversized_artifact,
            ) as builder:
                with self.assertRaises(ReleaseBuildError):
                    build_worker_release_bundle(
                        REPOSITORY_ROOT,
                        output,
                        WORKER_COMMIT,
                    )
            builder.assert_called_once()
            self.assertEqual(tuple(output.iterdir()), ())

    def test_builder_leaves_a_preexisting_asset_untouched(self):
        from scripts.build_worker_artifact import build_worker_artifact

        with tempfile.TemporaryDirectory() as root:
            artifact_output = self._private_directory(root, "artifact")
            artifact = build_worker_artifact(
                REPOSITORY_ROOT,
                artifact_output / "worker.tar.gz",
            )
            output = self._private_directory(root, "release")
            asset_name = (
                "comfyui-cloud-run-worker-"
                + WORKER_COMMIT
                + "-"
                + artifact.sha256
                + ".tar.gz"
            )
            existing = output / asset_name
            existing.write_bytes(b"preexisting-release-asset")
            os.chmod(existing, 0o600)

            with self.assertRaises(ReleaseBuildError):
                build_worker_release_bundle(
                    REPOSITORY_ROOT,
                    output,
                    WORKER_COMMIT,
                )

            self.assertEqual(existing.read_bytes(), b"preexisting-release-asset")
            self.assertEqual(tuple(output.iterdir()), (existing,))

    def test_builder_normalizes_an_unavailable_repository(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "release")
            with self.assertRaises(ReleaseBuildError):
                build_worker_release_bundle(
                    Path(root) / "missing-repository",
                    output,
                    WORKER_COMMIT,
                )
            self.assertEqual(tuple(output.iterdir()), ())

    def test_metadata_rejects_unknown_fields_and_boolean_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "release")
            metadata = build_worker_release_bundle(
                REPOSITORY_ROOT,
                output,
                WORKER_COMMIT,
            ).metadata

        unknown = {**metadata.to_record(), "local_path": "/private/release"}
        with self.assertRaises(ReleaseBuildError):
            ReleaseMetadata.from_payload(unknown)
        for field, value in (
            ("schema_version", True),
            ("schema_version", 1.0),
            ("worker_archive_size_bytes", True),
        ):
            payload = metadata.to_record()
            payload[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ReleaseBuildError):
                    ReleaseMetadata.from_payload(payload)


class TemplateRendererTests(unittest.TestCase):
    def _private_directory(self, root, name):
        path = Path(root) / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        return path

    def test_renderer_is_deterministic_and_writes_only_private_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            first_output = self._private_directory(root, "first")
            second_output = self._private_directory(root, "second")
            first = render_worker_template(
                REPOSITORY_ROOT,
                first_output,
                release_metadata(),
                base_template_audit(),
            )
            second = render_worker_template(
                REPOSITORY_ROOT,
                second_output,
                release_metadata(),
                base_template_audit(),
            )

            self.assertEqual(first.remote_lock, second.remote_lock)
            self.assertEqual(first.onstart, second.onstart)
            self.assertEqual(first.request, second.request)
            for first_path, second_path in zip(first.paths, second.paths):
                self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
                self.assertEqual(os.stat(first_path).st_mode & 0o777, 0o600)
            self.assertEqual(
                {path.name for path in first_output.iterdir()},
                {
                    "onstart.sh",
                    "remote-release-lock.json",
                    "template-request.json",
                },
            )

    def test_request_and_remote_lock_have_only_the_reviewed_fields(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "render")
            rendered = render_worker_template(
                REPOSITORY_ROOT,
                output,
                release_metadata(),
                base_template_audit(),
            )

        self.assertEqual(
            set(rendered.request),
            {
                "name",
                "image",
                "tag",
                "runtype",
                "use_ssh",
                "ssh_direct",
                "jup_direct",
                "jupyter_dir",
                "use_jupyter_lab",
                "docker_login_repo",
                "docker_login_user",
                "docker_login_pass",
                "onstart",
                "env",
                "extra_filters",
                "recommended_disk_space",
                "private",
            },
        )
        self.assertEqual(rendered.request["image"], OFFICIAL_IMAGE)
        self.assertEqual(rendered.request["tag"], OFFICIAL_TAG)
        self.assertEqual(rendered.request["extra_filters"], EXTRA_FILTERS)
        self.assertEqual(rendered.request["runtype"], "ssh")
        self.assertIs(rendered.request["use_ssh"], True)
        self.assertIs(rendered.request["ssh_direct"], True)
        self.assertIs(rendered.request["jup_direct"], False)
        self.assertIs(rendered.request["use_jupyter_lab"], False)
        self.assertEqual(rendered.request["jupyter_dir"], "/workspace")
        self.assertEqual(rendered.request["docker_login_repo"], "")
        self.assertEqual(rendered.request["docker_login_user"], "")
        self.assertEqual(rendered.request["docker_login_pass"], "")
        self.assertEqual(rendered.request["env"], "-p 8765:8765")
        self.assertEqual(rendered.request["recommended_disk_space"], 80)
        self.assertIs(rendered.request["private"], True)
        self.assertNotIn("ports", rendered.request)
        self.assertEqual(
            rendered.request["name"],
            "cloud-run-worker-" + WORKER_COMMIT,
        )
        self.assertEqual(
            set(rendered.remote_lock),
            {
                "schema_version",
                "archive_url",
                "worker_commit",
                "worker_archive_sha256",
                "worker_archive_size_bytes",
                "protocol_version",
                "comfyui_core_version",
                "comfyui_frontend_version",
                "python_version",
                "destination",
            },
        )
        serialized = json.dumps(rendered.request, sort_keys=True).casefold()
        for forbidden in (
            "api_key",
            "token",
            "session_identity",
            "workflow",
            "model_url",
            "signed_url",
            "arbitrary_command",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_onstart_round_trips_bootstrap_and_compact_remote_lock(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "render")
            rendered = render_worker_template(
                REPOSITORY_ROOT,
                output,
                release_metadata(),
                base_template_audit(),
            )

            prefix = "printf '%s' '"
            bootstrap_suffix = "' | base64 -d | gzip -d > "
            lock_suffix = "' | base64 -d > "
            bootstrap_encoded = next(
                line[len(prefix):].split(bootstrap_suffix, 1)[0]
                for line in rendered.onstart.splitlines()
                if line.startswith(prefix) and bootstrap_suffix in line
            )
            lock_encoded = next(
                line[len(prefix):].split(lock_suffix, 1)[0]
                for line in rendered.onstart.splitlines()
                if line.startswith(prefix) and lock_suffix in line
            )
            self.assertEqual(
                gzip.decompress(
                    base64.b64decode(bootstrap_encoded, validate=True)
                ),
                (REPOSITORY_ROOT / "remote_worker" / "bootstrap.py").read_bytes(),
            )
            compact_lock = (
                json.dumps(
                    rendered.remote_lock,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            self.assertEqual(
                base64.b64decode(lock_encoded, validate=True),
                compact_lock,
            )
            self.assertLessEqual(len(rendered.onstart), 16384)
            self.assertEqual(rendered.remote_lock_path.read_bytes(), compact_lock)
            self.assertEqual(rendered.onstart_path.read_text(), rendered.onstart)

        self.assertIn("umask 077", rendered.onstart)
        self.assertIn("mkdir -p -m 0700 /opt/comfyui-cloud-run-bootstrap", rendered.onstart)
        self.assertIn("chmod 0600 /opt/comfyui-cloud-run-bootstrap/bootstrap.py", rendered.onstart)
        self.assertIn(
            "chmod 0600 /opt/comfyui-cloud-run-bootstrap/release-lock.json",
            rendered.onstart,
        )
        self.assertIn(
            "CLOUD_RUN_WORKER_VERSION=" + WORKER_COMMIT,
            rendered.onstart,
        )
        self.assertIn(
            "CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI",
            rendered.onstart,
        )
        self.assertIn("export CLOUD_RUN_COMFY_ROOT", rendered.onstart)
        self.assertIn(
            "exec /venv/main/bin/python /opt/comfyui-cloud-run-bootstrap/bootstrap.py "
            "/opt/comfyui-cloud-run-bootstrap/release-lock.json",
            rendered.onstart,
        )
        exec_line = next(
            line for line in rendered.onstart.splitlines()
            if line.startswith("exec ")
        )
        self.assertEqual(
            exec_line,
            "exec /venv/main/bin/python "
            "/opt/comfyui-cloud-run-bootstrap/bootstrap.py "
            "/opt/comfyui-cloud-run-bootstrap/release-lock.json",
        )
        for forbidden in (
            "curl",
            "wget",
            "eval",
            "sh -c",
            "bash -c",
            "entrypoint",
            "boot_default",
            "supervisor",
            "comfyui-wrapper",
            "portal_config",
            "serverless",
            "$(",
            "${",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered.onstart.casefold())

    def test_renderer_rejects_non_exact_base_template_audits(self):
        invalid = []
        missing = base_template_audit()
        missing.pop("ssh_direct")
        invalid.append(missing)
        invalid.append({**base_template_audit(), "env": "SECRET=value"})
        invalid.append({**base_template_audit(), "image": OFFICIAL_IMAGE})
        invalid.append({**base_template_audit(), "tag": OFFICIAL_TAG})
        invalid.append({**base_template_audit(), "hash_id": "e" * 32})
        invalid.append({**base_template_audit(), "schema_version": 1.0})
        invalid.append({**base_template_audit(), "runtype": "jupyter"})
        invalid.append({**base_template_audit(), "use_ssh": False})
        invalid.append({**base_template_audit(), "jupyter_dir": "/tmp"})

        with tempfile.TemporaryDirectory() as root:
            for index, base in enumerate(invalid):
                output = self._private_directory(root, f"render-{index}")
                with self.subTest(index=index):
                    with self.assertRaises(TemplateRenderError):
                        render_worker_template(
                            REPOSITORY_ROOT,
                            output,
                            release_metadata(),
                            base,
                        )
                    self.assertEqual(tuple(output.iterdir()), ())

    def test_renderer_rejects_altered_metadata_and_output_permissions(self):
        metadata = release_metadata()
        object.__setattr__(metadata, "protocol_version", "1")
        with tempfile.TemporaryDirectory() as root:
            private = self._private_directory(root, "private")
            symlink = Path(root) / "symlink"
            symlink.symlink_to(private, target_is_directory=True)
            public = Path(root) / "public"
            public.mkdir(mode=0o777)
            os.chmod(public, 0o777)

            for output, candidate_metadata in (
                (private, metadata),
                (symlink, release_metadata()),
                (public, release_metadata()),
            ):
                with self.subTest(output=output.name):
                    with self.assertRaises(TemplateRenderError):
                        render_worker_template(
                            REPOSITORY_ROOT,
                            output,
                            candidate_metadata,
                            base_template_audit(),
                        )

    def test_renderer_rejects_an_altered_template_policy(self):
        with tempfile.TemporaryDirectory() as root:
            repository = Path(root) / "repository"
            remote = repository / "remote_worker"
            remote.mkdir(parents=True)
            (remote / "bootstrap.py").write_bytes(b"reviewed bootstrap")
            policy = json.loads(
                (REPOSITORY_ROOT / "remote_worker" / "template-policy.json")
                .read_text(encoding="utf-8")
            )
            policy["worker_port"] = 8188
            (remote / "template-policy.json").write_text(
                json.dumps(policy),
                encoding="utf-8",
            )
            output = self._private_directory(root, "render")

            with self.assertRaises(TemplateRenderError):
                render_worker_template(
                    repository,
                    output,
                    release_metadata(),
                    base_template_audit(),
                )
            self.assertEqual(tuple(output.iterdir()), ())

    def test_renderer_normalizes_an_unavailable_repository(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "render")
            with self.assertRaises(TemplateRenderError):
                render_worker_template(
                    Path(root) / "missing-repository",
                    output,
                    release_metadata(),
                    base_template_audit(),
                )
            self.assertEqual(tuple(output.iterdir()), ())


class WorkerReleaseLockWriterTests(unittest.TestCase):
    def _private_directory(self, root, name):
        path = Path(root) / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        return path

    def _expected_record(self, template_hash_id="1" * 32):
        metadata = release_metadata()
        return {
            "schema_version": 1,
            "template_hash_id": template_hash_id,
            "worker_commit": metadata.worker_commit,
            "worker_archive_sha256": metadata.worker_archive_sha256,
            "protocol_version": metadata.protocol_version,
            "comfyui_core_version": metadata.comfyui_core_version,
            "comfyui_frontend_version": metadata.comfyui_frontend_version,
            "python_version": metadata.python_version,
            "worker_port": 8765,
        }

    def test_writer_creates_exact_private_lock_and_round_trips(self):
        from cloud_run.worker_release import load_worker_release

        with tempfile.TemporaryDirectory() as root:
            parent = self._private_directory(root, "private")
            target = parent / "worker-release.json"
            loaded = write_worker_release_lock(
                target,
                "1" * 32,
                release_metadata(),
            )

            expected = self._expected_record()
            expected_bytes = (
                json.dumps(
                    expected,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            self.assertEqual(target.read_bytes(), expected_bytes)
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
            self.assertEqual(loaded.to_record(), expected)
            self.assertEqual(load_worker_release(target).to_record(), expected)
            self.assertEqual(
                tuple(parent.glob(".worker-release.json.*.tmp")),
                (),
            )

    def test_writer_rejects_existing_symlink_and_non_private_targets(self):
        with tempfile.TemporaryDirectory() as root:
            private = self._private_directory(root, "private")
            existing = private / "existing.json"
            existing.write_bytes(b"existing")
            os.chmod(existing, 0o600)
            symlink = private / "symlink.json"
            symlink.symlink_to(existing)
            public = Path(root) / "public"
            public.mkdir(mode=0o777)
            os.chmod(public, 0o777)
            missing_parent = Path(root) / "missing" / "worker-release.json"

            for target in (
                existing,
                symlink,
                public / "worker-release.json",
                missing_parent,
            ):
                with self.subTest(target=target.name):
                    with self.assertRaises(LockWriteError):
                        write_worker_release_lock(
                            target,
                            "1" * 32,
                            release_metadata(),
                        )
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_writer_rejects_wrong_template_hash_and_altered_metadata(self):
        altered = release_metadata()
        object.__setattr__(altered, "python_version", "3.14.0")
        with tempfile.TemporaryDirectory() as root:
            for index, (template_hash_id, metadata) in enumerate(
                (
                    (True, release_metadata()),
                    ("1" * 31, release_metadata()),
                    ("A" * 32, release_metadata()),
                    ("1" * 32, altered),
                )
            ):
                parent = self._private_directory(root, f"private-{index}")
                target = parent / "worker-release.json"
                with self.subTest(index=index):
                    with self.assertRaises(LockWriteError):
                        write_worker_release_lock(
                            target,
                            template_hash_id,
                            metadata,
                        )
                    self.assertFalse(target.exists())
                    self.assertEqual(tuple(parent.iterdir()), ())

    def test_writer_publishes_only_after_fsyncing_complete_temp_file(self):
        real_link = os.link
        observations = []
        with tempfile.TemporaryDirectory() as root:
            parent = self._private_directory(root, "private")
            target = parent / "worker-release.json"
            expected_bytes = (
                json.dumps(
                    self._expected_record(),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )

            def inspect_link(source, destination, **kwargs):
                source_path = Path(source)
                destination_path = Path(destination)
                observations.append(
                    (
                        destination_path.exists(),
                        source_path.read_bytes(),
                        os.stat(source_path).st_mode & 0o777,
                    )
                )
                return real_link(source, destination, **kwargs)

            with mock.patch(
                "scripts.write_worker_release_lock.os.link",
                side_effect=inspect_link,
            ), mock.patch(
                "scripts.write_worker_release_lock.os.replace",
                side_effect=AssertionError("lock publication must not replace"),
            ):
                write_worker_release_lock(
                    target,
                    "1" * 32,
                    release_metadata(),
                )

        self.assertEqual(observations, [(False, expected_bytes, 0o600)])

    def test_competing_writer_wins_without_being_overwritten(self):
        real_link = os.link
        competitor = b"competitor-owned-final"
        with tempfile.TemporaryDirectory() as root:
            parent = self._private_directory(root, "private")
            target = parent / "worker-release.json"

            def competing_link(source, destination, **kwargs):
                destination_path = Path(destination)
                destination_path.write_bytes(competitor)
                os.chmod(destination_path, 0o600)
                return real_link(source, destination, **kwargs)

            with mock.patch(
                "scripts.write_worker_release_lock.os.link",
                side_effect=competing_link,
            ):
                with self.assertRaises(LockWriteError):
                    write_worker_release_lock(
                        target,
                        "1" * 32,
                        release_metadata(),
                    )

            self.assertEqual(target.read_bytes(), competitor)
            self.assertEqual(
                tuple(parent.glob(".worker-release.json.*.tmp")),
                (),
            )

    def test_every_prepublication_failure_removes_only_the_temp_file(self):
        with tempfile.TemporaryDirectory() as root:
            for index, patcher in enumerate(
                (
                    mock.patch(
                        "scripts.write_worker_release_lock.os.write",
                        side_effect=OSError("write failure"),
                    ),
                    mock.patch(
                        "scripts.write_worker_release_lock.os.fsync",
                        side_effect=OSError("fsync failure"),
                    ),
                    mock.patch(
                        "scripts.write_worker_release_lock.os.link",
                        side_effect=OSError("link failure"),
                    ),
                )
            ):
                parent = self._private_directory(root, f"private-{index}")
                target = parent / "worker-release.json"
                with self.subTest(index=index), patcher:
                    with self.assertRaises(LockWriteError):
                        write_worker_release_lock(
                            target,
                            "1" * 32,
                            release_metadata(),
                        )
                self.assertFalse(target.exists())
                self.assertEqual(tuple(parent.iterdir()), ())

    def test_temp_name_collision_does_not_remove_the_competing_file(self):
        token = "c" * 32
        competitor = b"competing-private-temp"
        with tempfile.TemporaryDirectory() as root:
            parent = self._private_directory(root, "private")
            target = parent / "worker-release.json"
            competing_temp = parent / (
                ".worker-release.json." + token + ".tmp"
            )
            competing_temp.write_bytes(competitor)
            os.chmod(competing_temp, 0o600)

            with mock.patch(
                "scripts.write_worker_release_lock.secrets.token_hex",
                return_value=token,
            ):
                with self.assertRaises(LockWriteError):
                    write_worker_release_lock(
                        target,
                        "1" * 32,
                        release_metadata(),
                    )

            self.assertFalse(target.exists())
            self.assertEqual(competing_temp.read_bytes(), competitor)

    def test_writer_uses_nofollow_creation_and_fsyncs_file_and_parent(self):
        real_open = os.open
        real_fsync = os.fsync
        open_calls = []
        fsync_types = []
        with tempfile.TemporaryDirectory() as root:
            parent = self._private_directory(root, "private")
            target = parent / "worker-release.json"

            def recording_open(path, flags, *args, **kwargs):
                open_calls.append(flags)
                return real_open(path, flags, *args, **kwargs)

            def recording_fsync(descriptor):
                fsync_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
                return real_fsync(descriptor)

            with mock.patch(
                "scripts.write_worker_release_lock.os.open",
                side_effect=recording_open,
            ), mock.patch(
                "scripts.write_worker_release_lock.os.fsync",
                side_effect=recording_fsync,
            ):
                write_worker_release_lock(
                    target,
                    "1" * 32,
                    release_metadata(),
                )

        creation_flags = [flags for flags in open_calls if flags & os.O_CREAT]
        self.assertEqual(len(creation_flags), 1)
        self.assertTrue(creation_flags[0] & os.O_EXCL)
        if hasattr(os, "O_NOFOLLOW"):
            self.assertTrue(creation_flags[0] & os.O_NOFOLLOW)
        self.assertIn(stat.S_IFREG, fsync_types)
        self.assertIn(stat.S_IFDIR, fsync_types)


class ReleaseToolCliTests(unittest.TestCase):
    def _private_directory(self, root, name):
        path = Path(root) / name
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        return path

    def _run(self, *arguments):
        return subprocess.run(
            (sys.executable, *arguments),
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_direct_script_cli_round_trip_stays_in_private_directories(self):
        with tempfile.TemporaryDirectory() as root:
            bundle_output = self._private_directory(root, "bundle")
            bundle_result = self._run(
                "scripts/build_worker_release_bundle.py",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--output-directory",
                str(bundle_output),
                "--worker-commit",
                WORKER_COMMIT,
            )
            self.assertEqual(bundle_result.returncode, 0, bundle_result.stderr)
            self.assertEqual(len(bundle_result.stdout.splitlines()), 4)
            self.assertNotIn(str(bundle_output), bundle_result.stdout)

            base_path = Path(root) / "base-template-audit.json"
            base_path.write_text(
                json.dumps(base_template_audit()),
                encoding="utf-8",
            )
            os.chmod(base_path, 0o600)
            render_output = self._private_directory(root, "render")
            render_result = self._run(
                "scripts/render_worker_template.py",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--output-directory",
                str(render_output),
                "--release-metadata",
                str(bundle_output / "release-metadata.json"),
                "--base-template-audit",
                str(base_path),
            )
            self.assertEqual(render_result.returncode, 0, render_result.stderr)
            self.assertEqual(len(render_result.stdout.splitlines()), 3)
            self.assertNotIn(str(render_output), render_result.stdout)

            lock_parent = self._private_directory(root, "lock")
            lock_path = lock_parent / "worker-release.json"
            lock_result = self._run(
                "scripts/write_worker_release_lock.py",
                "--output",
                str(lock_path),
                "--template-hash-id",
                "1" * 32,
                "--release-metadata",
                str(bundle_output / "release-metadata.json"),
            )
            self.assertEqual(lock_result.returncode, 0, lock_result.stderr)
            self.assertEqual(len(lock_result.stdout.splitlines()), 4)
            self.assertNotIn(str(lock_path), lock_result.stdout)
            self.assertEqual(os.stat(lock_path).st_mode & 0o777, 0o600)

    def test_cli_failure_does_not_print_private_absolute_paths(self):
        with tempfile.TemporaryDirectory() as root:
            output = self._private_directory(root, "bundle")
            result = self._run(
                "scripts/build_worker_release_bundle.py",
                "--repository-root",
                str(REPOSITORY_ROOT),
                "--output-directory",
                str(output),
                "--worker-commit",
                "not-a-commit",
            )

        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(str(REPOSITORY_ROOT), combined)
        self.assertNotIn(str(output), combined)
        self.assertNotIn("Traceback", combined)

    def test_cli_redacts_an_invalid_present_worker_tree_build_failure(self):
        marker = "task4-private-environment-marker"
        with tempfile.TemporaryDirectory() as root:
            invalid_repository = Path(root) / "invalid-repository"
            (invalid_repository / "remote_worker").mkdir(parents=True)
            (invalid_repository / "cloud_run").mkdir()
            (invalid_repository / "remote_worker" / "unreviewed.py").write_text(
                "UNREVIEWED = True\n",
                encoding="utf-8",
            )
            output = self._private_directory(root, "bundle")
            with mock.patch.dict(
                os.environ,
                {"TASK4_PRIVATE_MARKER": marker},
            ):
                result = self._run(
                    "scripts/build_worker_release_bundle.py",
                    "--repository-root",
                    str(invalid_repository),
                    "--output-directory",
                    str(output),
                    "--worker-commit",
                    WORKER_COMMIT,
                )

        combined = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            combined,
            "Worker release bundle could not be built.\n",
        )
        self.assertNotIn("Traceback", combined)
        self.assertNotIn(str(invalid_repository), combined)
        self.assertNotIn(str(REPOSITORY_ROOT), combined)
        self.assertNotIn(marker, combined)


if __name__ == "__main__":
    unittest.main()

"""Secret-minimal Caddy gateway and worker supervision tests."""

from contextlib import redirect_stderr
import hmac
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cloud_run.worker_protocol import (
    BOUNDARY_TOKEN_ENVIRONMENT,
    SESSION_ID_ENVIRONMENT,
)
from remote_worker.gateway import (
    CADDY_CANDIDATES,
    GatewayError,
    STATE_DIRECTORY,
    parse_gateway_arguments,
    run_gateway,
    select_caddy_binary,
    validated_gateway_token,
)


STATIC_ERROR = "Remote Worker gateway configuration is unavailable."


def assert_gateway_error_does_not_retain(test_case, error, token):
    gateway_path = (
        Path(__import__("remote_worker.gateway").gateway.__file__).resolve()
    )
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        test_case.assertNotIn(token, repr(current))
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if Path(frame.f_code.co_filename).resolve() == gateway_path:
                for name, value in frame.f_locals.items():
                    test_case.assertNotIn(
                        token,
                        repr(value),
                        msg="retained gateway local: " + name,
                    )
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)


def file_metadata(*, mode=None, uid=None):
    return SimpleNamespace(
        st_mode=stat.S_IFREG | 0o755 if mode is None else mode,
        st_uid=os.getuid() if uid is None else uid,
    )


class FakeFilesystem:
    def __init__(self, metadata_by_path, executable_paths=()):
        self.metadata_by_path = dict(metadata_by_path)
        self.executable_paths = set(executable_paths)
        self.lstat_calls = []
        self.access_calls = []

    def lstat(self, path):
        self.lstat_calls.append(path)
        try:
            result = self.metadata_by_path[path]
        except KeyError:
            raise FileNotFoundError(path) from None
        if isinstance(result, BaseException):
            raise result
        return result

    def access(self, path, mode):
        self.access_calls.append((path, mode))
        return path in self.executable_paths


class FakeProcess:
    def __init__(self, poll_results, *, wait_result=0, time_out=False):
        self.poll_results = list(poll_results)
        self.wait_result = wait_result
        self.time_out = time_out
        self.returncode = None
        self.events = []
        self.killed = False

    def poll(self):
        if self.poll_results:
            result = self.poll_results.pop(0)
            if result is not None:
                self.returncode = result
            return result
        return self.returncode

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout):
        self.events.append(("wait", timeout))
        if self.time_out and not self.killed:
            raise subprocess.TimeoutExpired([], timeout)
        self.returncode = self.wait_result
        return self.wait_result

    def kill(self):
        self.events.append("kill")
        self.killed = True


class RecordingPopen:
    def __init__(self, caddy, worker):
        self.caddy = caddy
        self.worker = worker
        self.calls = []

    def __call__(self, argv, **options):
        self.calls.append((list(argv), dict(options)))
        return self.caddy if len(self.calls) == 1 else self.worker


class GatewayTokenTests(unittest.TestCase):
    def test_accepts_only_the_project_boundary_token(self):
        accepted = "a" * 64
        self.assertEqual(
            validated_gateway_token({BOUNDARY_TOKEN_ENVIRONMENT: accepted}),
            accepted,
        )

        for token in ("", "A" * 64, "a" * 63, "a" * 65, "has space"):
            with self.subTest(token_length=len(token)):
                with self.assertRaisesRegex(GatewayError, "^" + STATIC_ERROR + "$"):
                    validated_gateway_token({BOUNDARY_TOKEN_ENVIRONMENT: token})

    def test_provider_owned_tokens_never_substitute_for_project_boundary(self):
        provider_tokens = {
            "JUPYTER_TOKEN": "a" * 64,
            "OPEN_BUTTON_TOKEN": "b" * 64,
        }

        with self.assertRaisesRegex(GatewayError, "^" + STATIC_ERROR + "$"):
            validated_gateway_token(provider_tokens)

    def test_rejections_disclose_only_the_static_gateway_error(self):
        token = "not allowed secret"
        caught = None
        try:
            validated_gateway_token({BOUNDARY_TOKEN_ENVIRONMENT: token})
        except GatewayError as error:
            caught = error
        self.assertIsNotNone(caught)
        self.assertEqual(str(caught), STATIC_ERROR)
        assert_gateway_error_does_not_retain(self, caught, token)

    def test_mapping_failure_is_static_and_does_not_retain_its_token(self):
        token = "mapping-failure-secret"

        class FailingMapping:
            def get(self, _name):
                raise RuntimeError(token)

        caught = None
        try:
            validated_gateway_token(FailingMapping())
        except Exception as error:
            caught = error

        self.assertIsInstance(caught, GatewayError)
        self.assertEqual(str(caught), STATIC_ERROR)
        assert_gateway_error_does_not_retain(self, caught, token)


class CaddySelectionTests(unittest.TestCase):
    def test_candidate_is_the_exact_official_vast_portal_binary(self):
        self.assertEqual(
            CADDY_CANDIDATES,
            (Path("/opt/portal-aio/caddy_manager/caddy"),),
        )

    def test_selects_the_only_regular_owned_executable_candidate(self):
        selected = CADDY_CANDIDATES[0]
        filesystem = FakeFilesystem(
            {selected: file_metadata()},
            {selected},
        )

        result = select_caddy_binary(
            lstat_fn=filesystem.lstat,
            access_fn=filesystem.access,
        )

        self.assertEqual(
            result,
            Path("/opt/portal-aio/caddy_manager/caddy"),
        )
        self.assertEqual(filesystem.lstat_calls, list(CADDY_CANDIDATES))
        self.assertEqual(filesystem.access_calls, [(selected, os.X_OK)])

    def test_missing_official_candidate_fails_closed_without_trying_generic_paths(self):
        filesystem = FakeFilesystem(
            {
                Path("/usr/bin/caddy"): file_metadata(),
                Path("/usr/local/bin/caddy"): file_metadata(),
                Path("/opt/instance-tools/bin/caddy"): file_metadata(),
            },
            {
                Path("/usr/bin/caddy"),
                Path("/usr/local/bin/caddy"),
                Path("/opt/instance-tools/bin/caddy"),
            },
        )

        with self.assertRaisesRegex(GatewayError, "^" + STATIC_ERROR + "$"):
            select_caddy_binary(
                lstat_fn=filesystem.lstat,
                access_fn=filesystem.access,
            )

        self.assertEqual(
            filesystem.lstat_calls,
            [Path("/opt/portal-aio/caddy_manager/caddy")],
        )
        self.assertEqual(filesystem.access_calls, [])

    def test_rejects_symlinks_non_files_unowned_and_non_executable_files(self):
        candidate = CADDY_CANDIDATES[0]
        cases = {
            "symlink": file_metadata(mode=stat.S_IFLNK | 0o777),
            "directory": file_metadata(mode=stat.S_IFDIR | 0o755),
            "unowned": file_metadata(uid=max(1, os.getuid() + 1)),
            "not executable": file_metadata(),
        }
        for label, metadata in cases.items():
            executable = set() if label == "not executable" else {candidate}
            filesystem = FakeFilesystem({candidate: metadata}, executable)
            with self.subTest(label=label):
                with self.assertRaises(GatewayError):
                    select_caddy_binary(
                        lstat_fn=filesystem.lstat,
                        access_fn=filesystem.access,
                    )


class GatewayProcessTests(unittest.TestCase):
    def setUp(self):
        self.token = "a" * 64
        self.runtime_environment = {
            BOUNDARY_TOKEN_ENVIRONMENT: self.token,
            "JUPYTER_TOKEN": "b" * 64,
            "OPEN_BUTTON_TOKEN": "c" * 64,
            SESSION_ID_ENVIRONMENT: "session-1",
            "CLOUD_RUN_COMFY_ROOT": "/opt/ComfyUI",
            "CLOUD_RUN_WORKER_VERSION": "a" * 40,
            "CONTAINER_ID": "77",
            "CONTAINER_API_KEY": "instance-only-key",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin",
            "PYTHONPATH": "/opt/comfyui-cloud-run",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": "/tmp",
            "VAST_API_KEY": "must-not-cross",
            "UNRELATED": "must-not-cross",
        }
        self.config_directory = STATE_DIRECTORY / "caddy-config"
        self.data_directory = STATE_DIRECTORY / "caddy-data"
        self.caddy_path = CADDY_CANDIDATES[0]

    def run_recorded(self, caddy, worker, *, timeout=3):
        factory = RecordingPopen(caddy, worker)
        with (
            patch(
                "remote_worker.gateway.select_caddy_binary",
                return_value=self.caddy_path,
            ),
            patch(
                "remote_worker.gateway._prepare_caddy_directories",
                return_value=(self.config_directory, self.data_directory),
            ),
        ):
            result = run_gateway(
                environ=self.runtime_environment,
                popen_factory=factory,
                wait_timeout_seconds=timeout,
            )
        return result, factory

    def test_starts_fixed_argv_without_shell_from_installed_worker_root(self):
        result, factory = self.run_recorded(
            FakeProcess([None], wait_result=0),
            FakeProcess([7], wait_result=7),
        )

        caddyfile = Path(__import__("remote_worker.gateway").gateway.__file__).with_name(
            "Caddyfile"
        )
        installed_root = caddyfile.parent.parent
        self.assertEqual(result, 7)
        self.assertEqual(
            factory.calls[0][0],
            [
                self.caddy_path,
                "run",
                "--config",
                caddyfile,
                "--adapter",
                "caddyfile",
            ],
        )
        self.assertEqual(
            factory.calls[1][0],
            [
                sys.executable,
                "-m",
                "remote_worker.main",
                "--state-directory",
                "/var/lib/comfyui-cloud-run",
            ],
        )
        for argv, options in factory.calls:
            self.assertEqual(options["cwd"], installed_root)
            self.assertIs(options["shell"], False)
            self.assertNotIn(self.token, repr(argv))

    def test_splits_caddy_token_from_the_explicit_worker_allowlist(self):
        _result, factory = self.run_recorded(
            FakeProcess([None], wait_result=0),
            FakeProcess([0], wait_result=0),
        )

        self.assertEqual(
            factory.calls[0][1]["env"],
            {
                BOUNDARY_TOKEN_ENVIRONMENT: self.token,
                "HOME": "/var/lib/comfyui-cloud-run",
                "XDG_CONFIG_HOME": str(self.config_directory),
                "XDG_DATA_HOME": str(self.data_directory),
            },
        )
        worker_environment = factory.calls[1][1]["env"]
        expected_names = {
            SESSION_ID_ENVIRONMENT,
            "CLOUD_RUN_COMFY_ROOT",
            "CLOUD_RUN_WORKER_VERSION",
            "CONTAINER_ID",
            "CONTAINER_API_KEY",
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "PYTHONPATH",
            "PYTHONUNBUFFERED",
            "TMPDIR",
        }
        self.assertEqual(set(worker_environment), expected_names)
        for name in expected_names - {"CONTAINER_API_KEY"}:
            self.assertEqual(
                worker_environment[name],
                self.runtime_environment[name],
            )
        self.assertTrue(
            hmac.compare_digest(
                worker_environment["CONTAINER_API_KEY"],
                self.runtime_environment["CONTAINER_API_KEY"],
            ),
            msg="worker instance credential was not preserved",
        )
        self.assertEqual(
            worker_environment[SESSION_ID_ENVIRONMENT],
            self.runtime_environment[SESSION_ID_ENVIRONMENT],
        )
        self.assertNotIn(BOUNDARY_TOKEN_ENVIRONMENT, worker_environment)
        self.assertNotIn("JUPYTER_TOKEN", worker_environment)
        self.assertNotIn("OPEN_BUTTON_TOKEN", worker_environment)

    def test_filtered_environment_builds_real_deadline_armed_worker_runtime(self):
        from remote_worker.deadline import (
            DeadlineValidationError,
            deadline_watchdog,
        )
        from remote_worker.main import build_worker_runtime

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy_root = root / "ComfyUI"
            (comfy_root / "custom_nodes").mkdir(parents=True)
            (comfy_root / "main.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            data_root = root / "worker-data"
            self.runtime_environment["CLOUD_RUN_COMFY_ROOT"] = str(comfy_root)
            result, factory = self.run_recorded(
                FakeProcess([None], wait_result=0),
                FakeProcess([0], wait_result=0),
            )

            worker_environment = factory.calls[1][1]["env"]
            try:
                with patch.dict(os.environ, worker_environment, clear=True):
                    worker = build_worker_runtime(
                        state_path=data_root / "worker-state.json",
                        expected_session_id="session-1",
                        comfy_root=comfy_root,
                        data_root=data_root,
                        worker_version="a" * 40,
                        python_executable=sys.executable,
                        deadline_factory=deadline_watchdog,
                    )
            except DeadlineValidationError as error:
                self.fail(
                    "filtered environment prevented real worker startup: "
                    + str(error)
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            worker.deadline_watchdog.own_instance_url,
            "https://console.vast.ai/api/v0/instances/77/",
        )

    def test_terminates_and_reaps_caddy_when_worker_exits(self):
        caddy = FakeProcess([None], wait_result=0)
        worker = FakeProcess([4], wait_result=4)

        result, _factory = self.run_recorded(caddy, worker, timeout=6)

        self.assertEqual(result, 4)
        self.assertEqual(caddy.events, ["terminate", ("wait", 6)])
        self.assertNotIn("kill", caddy.events)
        self.assertEqual(worker.events, [])

    def test_terminates_and_reaps_worker_when_caddy_exits(self):
        caddy = FakeProcess([0], wait_result=0)
        worker = FakeProcess([None], wait_result=0)

        result, _factory = self.run_recorded(caddy, worker, timeout=5)

        self.assertEqual(result, 0)
        self.assertEqual(worker.events, ["terminate", ("wait", 5)])
        self.assertNotIn("kill", worker.events)

    def test_kills_only_after_the_bounded_terminate_wait_times_out(self):
        caddy = FakeProcess([None], wait_result=0, time_out=True)
        worker = FakeProcess([0], wait_result=0)

        with self.assertRaisesRegex(GatewayError, "^" + STATIC_ERROR + "$"):
            self.run_recorded(caddy, worker, timeout=2)

        self.assertEqual(
            caddy.events,
            ["terminate", ("wait", 2), "kill", ("wait", 2)],
        )

    def test_child_start_failure_does_not_disclose_token(self):
        calls = []

        def failing_popen(argv, **options):
            calls.append((list(argv), dict(options)))
            raise RuntimeError(self.token)

        caught = None
        with (
            patch(
                "remote_worker.gateway.select_caddy_binary",
                return_value=self.caddy_path,
            ),
            patch(
                "remote_worker.gateway._prepare_caddy_directories",
                return_value=(self.config_directory, self.data_directory),
            ),
        ):
            try:
                run_gateway(
                    environ=self.runtime_environment,
                    popen_factory=failing_popen,
                    wait_timeout_seconds=2,
                )
            except GatewayError as error:
                caught = error

        self.assertIsNotNone(caught)
        self.assertEqual(str(caught), STATIC_ERROR)
        assert_gateway_error_does_not_retain(
            self,
            caught,
            self.token,
        )
        self.assertNotIn(self.token, repr(calls[0][0]))


class GatewayConfigurationTests(unittest.TestCase):
    def test_caddyfile_is_the_exact_external_bearer_boundary(self):
        caddyfile = Path(__import__("remote_worker.gateway").gateway.__file__).with_name(
            "Caddyfile"
        )
        text = caddyfile.read_text(encoding="utf-8")
        self.assertIn("admin off", text)
        self.assertIn("auto_https off", text)
        self.assertEqual(text.count(":8765 {"), 1)
        self.assertIn(
            '@unauthorized not header Authorization "Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}"',
            text,
        )
        self.assertIn("request_header -Authorization", text)
        self.assertIn("request_header -X-Cloud-Run-Boundary", text)
        self.assertIn(
            "request_header X-Cloud-Run-Boundary authenticated",
            text,
        )
        self.assertIn("reverse_proxy 127.0.0.1:8766", text)
        self.assertNotIn("0.0.0.0:8766", text)

    def test_argument_parser_accepts_only_the_fixed_state_directory(self):
        arguments = parse_gateway_arguments(
            ["--state-directory", "/var/lib/comfyui-cloud-run"]
        )
        self.assertEqual(arguments.state_directory, STATE_DIRECTORY)
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_gateway_arguments(["--state-directory", "/tmp/worker"])


if __name__ == "__main__":
    unittest.main()

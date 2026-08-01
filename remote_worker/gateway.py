"""Secret-minimal Caddy boundary and loopback worker supervisor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Mapping

from cloud_run.worker_protocol import (
    BOUNDARY_TOKEN_ENVIRONMENT,
    SESSION_ID_ENVIRONMENT,
    is_boundary_token,
)


CADDY_CANDIDATES = (
    Path("/opt/portal-aio/caddy_manager/caddy"),
)
STATE_DIRECTORY = Path("/var/lib/comfyui-cloud-run")
SHUTDOWN_TIMEOUT_SECONDS = 10

_CADDY_CONFIG_DIRECTORY = STATE_DIRECTORY / "caddy-config"
_CADDY_DATA_DIRECTORY = STATE_DIRECTORY / "caddy-data"
_GATEWAY_ERROR_TEXT = "Remote Worker gateway configuration is unavailable."
_GATEWAY_REJECTED = object()
_WORKER_ENVIRONMENT_ALLOWLIST = (
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
)


class GatewayError(RuntimeError):
    """The reviewed gateway boundary could not be started safely."""


def _gateway_error():
    return GatewayError(_GATEWAY_ERROR_TEXT)


def _validated_gateway_token_result(environ):
    try:
        token = environ.get(BOUNDARY_TOKEN_ENVIRONMENT)
    except Exception:
        return _GATEWAY_REJECTED
    if not is_boundary_token(token):
        return _GATEWAY_REJECTED
    return token


def validated_gateway_token(environ: Mapping[str, str]) -> str:
    result = _validated_gateway_token_result(environ)
    environ = None
    if result is _GATEWAY_REJECTED:
        raise _gateway_error()
    return result


def select_caddy_binary(*, lstat_fn, access_fn) -> Path:
    matches = []
    allowed_owners = {0, os.getuid()}
    for candidate in CADDY_CANDIDATES:
        try:
            metadata = lstat_fn(candidate)
            accepted = (
                stat.S_ISREG(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_uid in allowed_owners
                and access_fn(candidate, os.X_OK)
            )
        except (AttributeError, OSError, TypeError, ValueError):
            accepted = False
        if accepted:
            matches.append(candidate)
    if len(matches) != 1:
        raise _gateway_error()
    return matches[0]


def _private_directory(path):
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = os.lstat(path)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise OSError("unsupported directory")
        os.chmod(path, 0o700)
    except (OSError, RuntimeError, ValueError):
        raise _gateway_error() from None
    return path


def _prepare_caddy_directories():
    _private_directory(STATE_DIRECTORY)
    return (
        _private_directory(_CADDY_CONFIG_DIRECTORY),
        _private_directory(_CADDY_DATA_DIRECTORY),
    )


def _validated_caddyfile():
    path = Path(__file__).with_name("Caddyfile")
    try:
        metadata = os.lstat(path)
    except OSError:
        raise _gateway_error() from None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise _gateway_error()
    return path


def _wait_for_exit(caddy, worker):
    while True:
        caddy_code = caddy.poll()
        worker_code = worker.poll()
        if caddy_code is not None or worker_code is not None:
            return caddy_code, worker_code
        time.sleep(0.1)


def _terminate_and_reap(process, timeout):
    timed_out = False
    try:
        process.terminate()
        result = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
            result = process.wait(timeout=timeout)
        except Exception:
            return None, True, False
    except Exception:
        return None, False, False
    return result, timed_out, True


def _best_effort_stop(process, timeout):
    if process is None:
        return
    try:
        if process.poll() is None:
            _terminate_and_reap(process, timeout)
    except Exception:
        return


def _run_gateway_unsafe(*, environ, popen_factory, wait_timeout_seconds):
    try:
        token = validated_gateway_token(environ)
        caddy = select_caddy_binary(
            lstat_fn=os.lstat,
            access_fn=os.access,
        )
        caddyfile = _validated_caddyfile()
        config_directory, data_directory = _prepare_caddy_directories()
        installed_root = caddyfile.parent.parent
        caddy_environment = {
            BOUNDARY_TOKEN_ENVIRONMENT: token,
            "HOME": str(STATE_DIRECTORY),
            "XDG_CONFIG_HOME": str(config_directory),
            "XDG_DATA_HOME": str(data_directory),
        }
        worker_environment = {
            name: environ[name]
            for name in _WORKER_ENVIRONMENT_ALLOWLIST
            if name in environ and isinstance(environ[name], str)
        }
        caddy_argv = [
            caddy,
            "run",
            "--config",
            caddyfile,
            "--adapter",
            "caddyfile",
        ]
        worker_argv = [
            sys.executable,
            "-m",
            "remote_worker.main",
            "--state-directory",
            str(STATE_DIRECTORY),
        ]
    except GatewayError:
        raise
    except Exception:
        raise _gateway_error() from None

    caddy_process = None
    worker_process = None
    start_failed = False
    try:
        caddy_process = popen_factory(
            caddy_argv,
            cwd=installed_root,
            env=caddy_environment,
            shell=False,
        )
        worker_process = popen_factory(
            worker_argv,
            cwd=installed_root,
            env=worker_environment,
            shell=False,
        )
    except Exception:
        start_failed = True
    if start_failed:
        _best_effort_stop(caddy_process, wait_timeout_seconds)
        raise _gateway_error()

    supervision_failed = False
    caddy_was_running = False
    try:
        caddy_code, worker_code = _wait_for_exit(
            caddy_process,
            worker_process,
        )
        caddy_was_running = caddy_code is None
        if caddy_code is None:
            caddy_code, timed_out, reaped = _terminate_and_reap(
                caddy_process,
                wait_timeout_seconds,
            )
            supervision_failed = timed_out or not reaped
        if worker_code is None:
            worker_code, timed_out, reaped = _terminate_and_reap(
                worker_process,
                wait_timeout_seconds,
            )
            supervision_failed = supervision_failed or timed_out or not reaped
    except Exception:
        supervision_failed = True
        caddy_code = None
        worker_code = None

    if supervision_failed:
        _best_effort_stop(caddy_process, wait_timeout_seconds)
        _best_effort_stop(worker_process, wait_timeout_seconds)
        raise _gateway_error()
    if (
        isinstance(worker_code, bool)
        or not isinstance(worker_code, int)
        or worker_code < 0
        or (
            not caddy_was_running
            and (
                isinstance(caddy_code, bool)
                or not isinstance(caddy_code, int)
                or caddy_code != 0
            )
        )
    ):
        raise _gateway_error()
    return worker_code


def _run_gateway_result(*, environ, popen_factory, wait_timeout_seconds):
    try:
        return _run_gateway_unsafe(
            environ=environ,
            popen_factory=popen_factory,
            wait_timeout_seconds=wait_timeout_seconds,
        )
    except Exception:
        return _GATEWAY_REJECTED


def run_gateway(*, environ, popen_factory, wait_timeout_seconds) -> int:
    result = _run_gateway_result(
        environ=environ,
        popen_factory=popen_factory,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    environ = None
    popen_factory = None
    wait_timeout_seconds = None
    if result is _GATEWAY_REJECTED:
        raise _gateway_error()
    return result


def parse_gateway_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the reviewed Remote Worker gateway."
    )
    parser.add_argument(
        "--state-directory",
        type=Path,
        required=True,
    )
    arguments = parser.parse_args(argv)
    if arguments.state_directory != STATE_DIRECTORY:
        parser.error("the reviewed worker state directory is required")
    return arguments


def main(argv=None):
    parse_gateway_arguments(argv)
    try:
        return run_gateway(
            environ=os.environ,
            popen_factory=subprocess.Popen,
            wait_timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS,
        )
    except GatewayError:
        raise SystemExit(_GATEWAY_ERROR_TEXT) from None


if __name__ == "__main__":
    raise SystemExit(main())

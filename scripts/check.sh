#!/bin/sh
set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/.." && pwd)
comfyui_python_runner="$script_directory/run_with_comfyui_python.sh"
cd "$repository_root"

python_command=${PYTHON_COMMAND:-python3}
node_command=${NODE_COMMAND:-node}

command -v "$python_command" >/dev/null 2>&1 || {
  echo "[check] Python command not found" >&2
  exit 1
}
command -v "$node_command" >/dev/null 2>&1 || {
  echo "[check] Node command not found" >&2
  exit 1
}
if [ ! -x "$comfyui_python_runner" ]; then
  echo "[check] shared ComfyUI Python runner is unavailable" >&2
  exit 1
fi

echo "[check] Python tests"
PYTHONDONTWRITEBYTECODE=1 "$comfyui_python_runner" -m unittest discover \
  -s tests/python -p 'test_*.py' -v

echo "[check] fake reusable session"
PYTHONDONTWRITEBYTECODE=1 "$python_command" -m unittest \
  tests.python.test_fake_session_integration -v

echo "[check] worker protocol and artifact"
PYTHONDONTWRITEBYTECODE=1 "$python_command" -m unittest \
  tests.python.test_worker_protocol \
  tests.python.test_worker_bootstrap -v
PYTHONDONTWRITEBYTECODE=1 "$python_command" - <<'PY'
from pathlib import Path
import tempfile

from scripts.build_worker_artifact import build_worker_artifact

with tempfile.TemporaryDirectory() as temporary_directory:
    root = Path(temporary_directory)
    first = build_worker_artifact(Path.cwd(), root / "worker-first.tar.gz")
    second = build_worker_artifact(Path.cwd(), root / "worker-second.tar.gz")
    if (
        first.sha256 != second.sha256
        or first.path.read_bytes() != second.path.read_bytes()
    ):
        raise SystemExit("[check] worker artifact is not deterministic")
    print("[check] worker artifact sha256 " + first.sha256)
PY

echo "[check] immutable worker release bundle"
PYTHONDONTWRITEBYTECODE=1 "$python_command" - <<'PY'
import os
from pathlib import Path
import tempfile

from scripts.build_worker_release_bundle import build_worker_release_bundle


expected_metadata_fields = {
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
}
with tempfile.TemporaryDirectory() as temporary_directory:
    root = Path(temporary_directory)
    first_output = root / "first"
    second_output = root / "second"
    first_output.mkdir(mode=0o700)
    second_output.mkdir(mode=0o700)
    os.chmod(first_output, 0o700)
    os.chmod(second_output, 0o700)
    worker_commit = "a" * 40
    first = build_worker_release_bundle(
        Path.cwd(),
        first_output,
        worker_commit,
    )
    second = build_worker_release_bundle(
        Path.cwd(),
        second_output,
        worker_commit,
    )
    if (
        first.archive.read_bytes() != second.archive.read_bytes()
        or first.metadata != second.metadata
        or first.metadata_path.read_bytes()
        != second.metadata_path.read_bytes()
        or set(first.metadata.to_record()) != expected_metadata_fields
    ):
        raise SystemExit("[check] worker release bundle is not deterministic")
    print(
        "[check] worker release bundle sha256 "
        + first.metadata.worker_archive_sha256
    )
PY

echo "[check] synthetic Gold validator"
PYTHONDONTWRITEBYTECODE=1 "$comfyui_python_runner" \
  -W error -m unittest \
  tests.python.test_gold_output_validation \
  tests.python.test_smoke_output_validation -v

echo "[check] Node tests"
"$node_command" --test tests/js/*.test.mjs

echo "[check] Python compile"
"$python_command" - <<'PY'
from pathlib import Path

excluded = {".git", ".worktrees", "__pycache__", "node_modules"}
paths = []
for path in Path(".").rglob("*.py"):
    if any(part in excluded for part in path.parts):
        continue
    paths.append(path)
for path in sorted(paths):
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
print("[check] compiled {} Python files".format(len(paths)))
PY

echo "[check] JavaScript syntax"
for javascript_path in web/js/*.js tests/js/*.mjs
do
  "$node_command" --check "$javascript_path"
done

echo "[check] secret, origin, route, state, subprocess, and provider boundary scan"
PYTHONDONTWRITEBYTECODE=1 "$python_command" - <<'PY'
import ast
import re
from pathlib import Path

from cloud_run.constants import OFFICIAL_TEMPLATE_ID
from cloud_run.models import JobState, SessionState, TransferState
from cloud_run.r2 import R2ValidationError, _endpoint
from remote_worker.server import worker_route_set


def fail(message):
    print("[check] " + message)
    raise SystemExit(1)


excluded = {".git", ".worktrees", "__pycache__", "node_modules"}
text_files = []
for path in Path(".").rglob("*"):
    if not path.is_file() or any(part in excluded for part in path.parts):
        continue
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    text_files.append(path)

secret_patterns = {
    "private-key header": re.compile(
        "-----BEGIN " + r"(?:[A-Z]+ )?" + "PRIVATE KEY-----"
    ),
    "assigned Vast credential": re.compile(
        "VAST_" + r"API_KEY\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    "assigned R2 credential": re.compile(
        "R2_" + r"SECRET_ACCESS_KEY\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    "Hugging Face credential": re.compile(
        "hf" + r"_[A-Za-z0-9]{20,}"
    ),
    "AWS access credential": re.compile(
        "AK" + r"IA[0-9A-Z]{16}"
    ),
    "long bearer token": re.compile(
        "Bearer " + r"[A-Za-z0-9_=.-]{32,}"
    ),
}
secret_findings = []
for path in sorted(text_files):
    text = path.read_text(encoding="utf-8")
    for label, pattern in secret_patterns.items():
        if pattern.search(text):
            secret_findings.append((str(path), label))
if secret_findings:
    for path, label in secret_findings:
        print("[check] possible {} in {}".format(label, path))
    raise SystemExit(1)

vast_path = Path("cloud_run/vast.py")
vast_source = vast_path.read_text(encoding="utf-8")
vast_tree = ast.parse(vast_source, str(vast_path))
provider_urls = {
    node.value
    for node in ast.walk(vast_tree)
    if isinstance(node, ast.Constant)
    and isinstance(node.value, str)
    and "console.vast.ai" in node.value
}
if provider_urls != {
    "https://console.vast.ai/api/v0",
    "https://console.vast.ai/api/v1",
}:
    fail("unexpected Vast provider URL surface")

provider_methods = {
    node.func.attr
    for node in ast.walk(vast_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id in {"client", "session"}
    and node.func.attr in {"delete", "get", "patch", "post", "put"}
}
if provider_methods != {"delete", "get", "post", "put"}:
    fail("unexpected Vast provider HTTP method surface")

allowed_provider_actions = {
    "create_instance",
    "destroy_instance",
    "get_instance",
    "get_offer",
    "list_instances",
    "search_offers",
}
public_provider_functions = {
    node.name
    for node in vast_tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and not node.name.startswith("_")
}
provider_actions = public_provider_functions - {
    "build_search_payload",
    "derive_base_url",
    "normalize_offers",
}
if provider_actions != allowed_provider_actions:
    fail("unexpected Vast provider action surface")

if OFFICIAL_TEMPLATE_ID != "027fba7753c024be019030fb42aed900":
    fail("official base template allowlist changed")

routes_path = Path("cloud_run/routes.py")
routes_tree = ast.parse(
    routes_path.read_text(encoding="utf-8"),
    str(routes_path),
)
registered_routes = set()
for node in ast.walk(routes_tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in {"delete", "get", "post", "put"}
            and len(decorator.args) == 1
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
        ):
            registered_routes.add(
                (decorator.func.attr.upper(), decorator.args[0].value)
            )

allowed_cloud_run_routes = {
    ("GET", "/cloud-run/api/settings"),
    ("PUT", "/cloud-run/api/settings"),
    ("GET", "/cloud-run/api/desktop-context"),
    ("GET", "/cloud-run/api/desktop-setup"),
    ("POST", "/cloud-run/api/captures"),
    ("POST", "/cloud-run/api/preflights"),
    ("PUT", "/cloud-run/api/mappings/{mapping_id}"),
    (
        "POST",
        "/cloud-run/api/integrations/agent-panel/suggestions",
    ),
    ("POST", "/cloud-run/api/cache/artifacts/{artifact_id}"),
    ("POST", "/cloud-run/api/offers"),
    ("POST", "/cloud-run/api/sessions"),
    ("POST", "/cloud-run/api/sessions/{session_id}/confirm"),
    ("GET", "/cloud-run/api/sessions/{session_id}"),
    ("GET", "/cloud-run/api/sessions/{session_id}/profile"),
    (
        "POST",
        "/cloud-run/api/sessions/{session_id}/profile/conflicts/{conflict_id}",
    ),
    (
        "POST",
        "/cloud-run/api/sessions/{session_id}/desktop-relay",
    ),
    (
        "DELETE",
        "/cloud-run/api/sessions/{session_id}/desktop-relay",
    ),
    ("POST", "/cloud-run/api/sessions/{session_id}/jobs"),
    ("GET", "/cloud-run/api/sessions/{session_id}/jobs/{job_id}"),
    (
        "GET",
        "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/events",
    ),
    (
        "GET",
        "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/previews/{preview_id}",
    ),
    (
        "GET",
        "/cloud-run/api/sessions/{session_id}/jobs/{job_id}/artifacts/{artifact_id}",
    ),
    ("PUT", "/cloud-run/api/sessions/{session_id}/deadline"),
    ("POST", "/cloud-run/api/sessions/{session_id}/destroy-review"),
    ("DELETE", "/cloud-run/api/sessions/{session_id}"),
}
if registered_routes != allowed_cloud_run_routes:
    fail("unexpected same-origin route surface")

allowed_worker_routes = {
    ("GET", "/worker/v1/health"),
    ("POST", "/worker/v1/claim"),
    ("POST", "/worker/v1/manifests"),
    ("GET", "/worker/v1/transactions/{transaction_id}"),
    ("PUT", "/worker/v1/artifacts/{artifact_id}"),
    ("GET", "/worker/v1/artifacts/{artifact_id}"),
    ("POST", "/worker/v1/jobs"),
    ("GET", "/worker/v1/jobs/{job_id}"),
    ("GET", "/worker/v1/jobs/{job_id}/events"),
    ("GET", "/worker/v1/jobs/{job_id}/snapshot"),
    ("GET", "/worker/v1/jobs/{job_id}/previews/{preview_id}"),
    ("PUT", "/worker/v1/profile"),
    ("GET", "/worker/v1/profile"),
    ("GET", "/worker/v1/profile/artifacts/{artifact_id}"),
    ("PUT", "/worker/v1/deadline"),
}
if worker_route_set() != allowed_worker_routes:
    fail("unexpected Remote Worker route surface")

expected_session_states = {
    "preflight",
    "offer_selected",
    "confirming",
    "creating",
    "reconciling_create",
    "bootstrapping",
    "provisioning",
    "validating",
    "ready",
    "running",
    "harvesting",
    "repairing",
    "destroy_requested",
    "destroying",
    "destroyed",
    "failed",
}
expected_job_states = {
    "captured",
    "resolving",
    "queued",
    "running",
    "harvesting",
    "succeeded",
    "failed",
}
expected_transfer_states = {
    "pending",
    "transferring",
    "verified",
    "failed",
    "abandoned",
}
if {state.value for state in SessionState} != expected_session_states:
    fail("session state allowlist changed")
if {state.value for state in JobState} != expected_job_states:
    fail("job state allowlist changed")
if {state.value for state in TransferState} != expected_transfer_states:
    fail("transfer state allowlist changed")

deadline_path = Path("remote_worker/deadline.py")
deadline_tree = ast.parse(
    deadline_path.read_text(encoding="utf-8"),
    str(deadline_path),
)
worker_provider_actions = set()
worker_provider_methods = set()
for node in deadline_tree.body:
    if (
        isinstance(node, ast.ClassDef)
        and node.name == "AiohttpOwnInstanceProvider"
    ):
        worker_provider_actions = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not child.name.startswith("_")
        }
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr
                in {"delete", "get", "patch", "post", "put"}
            ):
                worker_provider_methods.add(child.func.attr)
if (
    worker_provider_actions != {"delete"}
    or worker_provider_methods != {"delete"}
):
    fail("Remote Worker provider surface is not own-instance DELETE only")

reviewed_release_tool_paths = [
    Path("scripts/build_worker_release_bundle.py"),
    Path("scripts/publish_worker_template.py"),
    Path("scripts/render_worker_template.py"),
    Path("scripts/write_worker_release_lock.py"),
]
reviewed_release_test_paths = [
    Path("tests/python/test_worker_template_api.py"),
]
reviewed_worker_tool_paths = [
    Path("remote_worker/gateway.py"),
]
if not all(
    path.is_file()
    for path in [
        *reviewed_release_tool_paths,
        *reviewed_release_test_paths,
        *reviewed_worker_tool_paths,
    ]
):
    fail("reviewed worker tooling is unavailable")

production_paths = [
    Path("__init__.py"),
    *sorted(Path("cloud_run").glob("*.py")),
    *sorted(Path("remote_worker").glob("*.py")),
    *reviewed_release_tool_paths,
]
provider_boundary_paths = [
    Path("cloud_run/vast.py"),
    Path("cloud_run/lifecycle.py"),
    Path("remote_worker/gateway.py"),
    Path("remote_worker/Caddyfile"),
]
provider_credential_literals = (
    "JUPYTER_TOKEN",
    "OPEN_BUTTON_TOKEN",
    "jupyter_token",
)
for path in provider_boundary_paths:
    source = path.read_text(encoding="utf-8")
    for literal in provider_credential_literals:
        if literal in source:
            fail("provider credential literal in " + str(path))
frontend_paths = sorted(Path("web/js").glob("*.js"))
for path in production_paths:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, str(path))
    lowered = source.casefold()
    if "stop_instance" in source or "/volumes" in lowered:
        fail("forbidden stop or volume provider action in " + str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "eval",
                "exec",
            }:
                fail("dynamic evaluation in " + str(path))
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "system"
            ):
                fail("os.system in " + str(path))
            for keyword in node.keywords:
                if keyword.arg == "shell" and not (
                    isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                ):
                    fail("non-fixed subprocess shell in " + str(path))
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
            and "volume" in node.name.casefold()
        ):
            fail("volume action in " + str(path))

subprocess_surface = set()
subprocess_attributes = {
    "call",
    "check_call",
    "check_output",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "execv",
    "execve",
    "Popen",
    "run",
    "system",
}
for path in production_paths:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"asyncio", "os", "subprocess"}
            and node.attr in subprocess_attributes
        ):
            subprocess_surface.add(
                (str(path), node.value.id + "." + node.attr)
            )
expected_subprocess_surface = {
    ("cloud_run/comfy_host.py", "subprocess.run"),
    ("remote_worker/bootstrap.py", "os.execv"),
    ("remote_worker/comfy.py", "asyncio.create_subprocess_exec"),
    ("remote_worker/gateway.py", "subprocess.Popen"),
    ("remote_worker/install.py", "asyncio.create_subprocess_exec"),
}
if subprocess_surface != expected_subprocess_surface:
    fail("subprocess surface changed")

for path in frontend_paths:
    source = path.read_text(encoding="utf-8")
    for forbidden_frontend_value in (
        "console.vast.ai",
        "/" + "asks/",
        "/" + "instances/",
        "http" + "://",
        "https" + "://",
        "local" + "Storage",
        "session" + "Storage",
        ".inner" + "HTML",
    ):
        if forbidden_frontend_value in source:
            fail("forbidden frontend provider or browser storage surface")

forbidden_response_keys = {
    "api_key",
    "civitai_token",
    "container_api_key",
    "hf_token",
    "provider_token",
    "r2_secret_access_key",
    "session_secret_hex",
}
response_scan_paths = [
    *sorted(Path("cloud_run").glob("*.py")),
    *sorted(Path("remote_worker").glob("*.py")),
]
for path in response_scan_paths:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for function in ast.walk(tree):
        if not isinstance(
            function,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        ) or function.name not in {"public_payload", "public_settings"}:
            continue
        keys = {
            key.value
            for node in ast.walk(function)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant)
            and isinstance(key.value, str)
        }
        if keys & forbidden_response_keys:
            fail("secret-bearing public response key in " + str(path))
route_keys = {
    key.value
    for node in ast.walk(routes_tree)
    if isinstance(node, ast.Dict)
    for key in node.keys
    if isinstance(key, ast.Constant) and isinstance(key.value, str)
}
if route_keys & forbidden_response_keys:
    fail("secret-bearing route response key")

allowed_external_hosts = {
    "api.comfy.org",
    "cas-bridge.xethub.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "cdn-lfs-us-1.hf.co",
    "civitai.com",
    "console.vast.ai",
    "github.com",
    "huggingface.co",
    "release-assets.githubusercontent.com",
    "transfer.xethub-eu.hf.co",
    "transfer.xethub.hf.co",
    "us.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "vast.ai",
}
observed_external_hosts = set()
host_pattern = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?:[A-Za-z0-9-]+\.)+(?:ai|co|com|org)"
    r"(?![A-Za-z0-9_-])"
)
for path in [*production_paths, *frontend_paths]:
    observed_external_hosts.update(
        match.casefold()
        for match in host_pattern.findall(
            path.read_text(encoding="utf-8")
        )
    )
if observed_external_hosts - allowed_external_hosts:
    fail("external origin allowlist changed")
if not {
    "api.comfy.org",
    "cas-bridge.xethub.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "cdn-lfs-us-1.hf.co",
    "civitai.com",
    "console.vast.ai",
    "github.com",
    "huggingface.co",
    "release-assets.githubusercontent.com",
    "transfer.xethub-eu.hf.co",
    "transfer.xethub.hf.co",
    "us.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
}.issubset(observed_external_hosts):
    fail("approved external origin policy is incomplete")

if _endpoint("https://r2.example") != "https://r2.example":
    fail("configured R2 origin is not exact")
for invalid_r2_endpoint in (
    "http://r2.example",
    "https://r2.example/path",
    "https://r2.example:443",
    "https://user@r2.example",
):
    try:
        _endpoint(invalid_r2_endpoint)
    except R2ValidationError:
        pass
    else:
        fail("configured R2 origin boundary changed")

print("[check] boundary scan passed")
PY

echo "[check] public artifact scan"
"$python_command" - <<'PY'
import json
import os
from pathlib import Path
import re
import stat


def fail(message):
    print("[check] " + message)
    raise SystemExit(1)


excluded = {".git", ".worktrees", "__pycache__", "node_modules"}
allowed_suffixes = {
    "",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
}
allowed_json = {
    Path("package.json"),
    Path("remote_worker/template-policy.json"),
    Path("tests/fixtures/cloud-run-core-output-smoke.json"),
    Path("tests/fixtures/native-model-metadata-workflow.json"),
}
forbidden_suffixes = {
    ".avi",
    ".bin",
    ".ckpt",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".png",
    ".pt",
    ".safetensors",
    ".tar",
    ".webp",
    ".workflow",
    ".zip",
}
secret_patterns = {
    "private-key header": re.compile(
        "-----BEGIN " + r"(?:[A-Z]+ )?" + "PRIVATE KEY-----"
    ),
    "assigned provider credential": re.compile(
        r"(?:VAST_" + "API_KEY|R2_" + "SECRET_ACCESS_KEY)"
        r"\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    "Hugging Face credential": re.compile(
        "hf" + r"_[A-Za-z0-9]{20,}"
    ),
    "long bearer token": re.compile(
        "Bearer " + r"[A-Za-z0-9_=.-]{32,}"
    ),
}
observed_json = set()
count = 0
for path in sorted(Path(".").rglob("*")):
    if any(part in excluded for part in path.parts):
        continue
    try:
        metadata = os.lstat(path)
    except OSError:
        fail("repository artifact is unavailable")
    if stat.S_ISDIR(metadata.st_mode):
        continue
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail("repository contains a non-regular public artifact")
    if path.suffix.casefold() in forbidden_suffixes:
        fail("repository contains a forbidden binary/private artifact")
    if path.suffix.casefold() not in allowed_suffixes:
        fail("repository contains an unreviewed artifact type")
    if metadata.st_size > 16 * 1024 * 1024:
        fail("repository contains an oversized public artifact")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        fail("repository public artifact is not UTF-8 text")
    relative = Path(path.as_posix().removeprefix("./"))
    if relative.name in {
        ".env",
        "settings.json",
        "worker-release.json",
    }:
        fail("repository contains private runtime configuration")
    if relative.suffix == ".json":
        observed_json.add(relative)
        try:
            json.loads(text)
        except json.JSONDecodeError:
            fail("repository JSON artifact is invalid")
    for label, pattern in secret_patterns.items():
        if pattern.search(text):
            fail("possible {} in {}".format(label, relative))
    count += 1
if observed_json != allowed_json:
    fail("public JSON artifact allowlist changed")
print("[check] scanned {} public text artifacts".format(count))
PY

echo "[check] all checks passed"

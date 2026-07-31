#!/bin/sh
set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/.." && pwd)
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

echo "[check] Python tests"
PYTHONDONTWRITEBYTECODE=1 "$python_command" -m unittest discover \
  -s tests/python -p 'test_*.py' -v

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
"$node_command" --check web/js/cloud-run.js
"$node_command" --check web/js/session-console.js
"$node_command" --check tests/js/fake-dom.mjs
"$node_command" --check tests/js/cloud-run-ui.test.mjs

echo "[check] secret and provider boundary scan"
"$python_command" - <<'PY'
import ast
import re
from pathlib import Path

from cloud_run.constants import OFFICIAL_TEMPLATE_ID
from cloud_run.models import AttemptState

excluded = {".git", ".worktrees", "__pycache__", "node_modules"}
text_files = []
for path in Path(".").rglob("*"):
    if not path.is_file() or any(part in excluded for part in path.parts):
        continue
    if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".zip"}:
        continue
    text_files.append(path)

secret_patterns = {
    "private-key header": re.compile(
        "-----BEGIN " + r"(?:[A-Z]+ )?" + "PRIVATE KEY-----"
    ),
    "assigned Vast credential": re.compile(
        r"VAST_API_KEY\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    "long bearer token": re.compile(r"Bearer [A-Za-z0-9_=-]{32,}"),
}
secret_findings = []
for path in sorted(text_files):
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
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
    print("[check] unexpected Vast provider URL surface")
    raise SystemExit(1)

provider_methods = {
    node.func.attr
    for node in ast.walk(vast_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id in {"client", "session"}
    and node.func.attr in {"get", "post", "put", "patch", "delete"}
}
if provider_methods != {"delete", "get", "post", "put"}:
    print("[check] unexpected Vast provider HTTP method surface")
    raise SystemExit(1)

allowed_provider_actions = {
    "create_instance",
    "destroy_instance",
    "get_instance",
    "get_offer",
    "list_instances",
    "search_offers",
}
provider_actions = {
    node.name
    for node in ast.walk(vast_tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and (
        (
            node.name.endswith(("_instance", "_instances"))
            and not node.name.startswith("_")
        )
        or node.name
        in {"get_offer", "search_offers", "rent_instance", "stop_instance"}
    )
}
if provider_actions != allowed_provider_actions:
    print("[check] unexpected Vast provider action surface")
    raise SystemExit(1)

if OFFICIAL_TEMPLATE_ID != "027fba7753c024be019030fb42aed900":
    print("[check] official template allowlist changed")
    raise SystemExit(1)

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
    ("POST", "/cloud-run/api/sessions/{session_id}/jobs"),
    ("GET", "/cloud-run/api/sessions/{session_id}/jobs/{job_id}"),
    ("PUT", "/cloud-run/api/sessions/{session_id}/deadline"),
    ("POST", "/cloud-run/api/sessions/{session_id}/destroy-review"),
    ("DELETE", "/cloud-run/api/sessions/{session_id}"),
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
}
if registered_routes != allowed_cloud_run_routes:
    print("[check] unexpected same-origin route surface")
    raise SystemExit(1)

expected_attempt_states = {
    "idle",
    "searching",
    "offer_selected",
    "confirming",
    "creating",
    "starting",
    "cancel_requested",
    "destroying",
    "retrying",
    "ready",
    "cancelled",
    "failed",
}
if {state.value for state in AttemptState} != expected_attempt_states:
    print("[check] lifecycle state allowlist changed")
    raise SystemExit(1)

frontend_source = Path("web/js/cloud-run.js").read_text(encoding="utf-8")
for forbidden_frontend_value in (
    "console.vast.ai",
    "/" + "asks/",
    "/" + "instances/",
    "localStorage",
    "sessionStorage",
    ".innerHTML",
):
    if forbidden_frontend_value in frontend_source:
        print("[check] forbidden frontend provider or secret surface")
        raise SystemExit(1)

print("[check] secret and provider boundary scan passed")
PY

echo "[check] all checks passed"

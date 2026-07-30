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
"$node_command" --check tests/js/fake-dom.mjs
"$node_command" --check tests/js/cloud-run-ui.test.mjs

echo "[check] secret and provider boundary scan"
"$python_command" - <<'PY'
import ast
import re
from pathlib import Path

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

production_paths = [
    Path("__init__.py"),
    *sorted(Path("cloud_run").glob("*.py")),
    *sorted(Path("web").rglob("*.js")),
]
production_source = "\n".join(
    path.read_text(encoding="utf-8") for path in production_paths
)
for forbidden_path in ("/" + "asks", "/" + "instances"):
    if forbidden_path in production_source:
        print("[check] forbidden provider path detected")
        raise SystemExit(1)

vast_path = Path("cloud_run/vast.py")
vast_source = vast_path.read_text(encoding="utf-8")
vast_tree = ast.parse(vast_source, str(vast_path))
provider_urls = [
    node.value
    for node in ast.walk(vast_tree)
    if isinstance(node, ast.Constant)
    and isinstance(node.value, str)
    and "console.vast.ai" in node.value
]
if provider_urls != ["https://console.vast.ai/api/v0/bundles/"]:
    print("[check] unexpected Vast provider URL surface")
    raise SystemExit(1)

provider_methods = {
    node.func.attr
    for node in ast.walk(vast_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == "session"
    and node.func.attr in {"get", "post", "put", "patch", "delete"}
}
if provider_methods != {"post"}:
    print("[check] unexpected Vast provider HTTP method surface")
    raise SystemExit(1)

for node in ast.walk(vast_tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if node.name.startswith(("create", "rent", "start", "stop", "destroy")):
        print("[check] forbidden provider action function detected")
        raise SystemExit(1)

print("[check] secret and provider boundary scan passed")
PY

echo "[check] all checks passed"

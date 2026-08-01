# Immutable Worker Release and Live Workflow Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and consume one deterministic immutable Remote Worker release, enforce the human-authorized total Vast instance-create limit and a reusable connection-quality floor, then run one separately authorized live workflow acceptance with verified teardown.

**Architecture:** Keep the reviewed worker-only archive and strict extractor. Serve that archive as an immutable GitHub Release asset, permit only GitHub's single release-asset redirect, supervise the loopback worker behind Caddy, and generate the private Vast template from deterministic repository tooling. Apply fixed provider and local connection-quality gates before displaying an offer, use one target-saturated ranking for initial and replacement selection, and persist both `max_instance_creates` and reviewed connection metrics in the paid quote so confirmation cannot cross either human or quality boundaries.

**Tech Stack:** Python 3 standard library and `unittest`, aiohttp application code already in the repository, JavaScript ES modules and Node test runner, SQLite repositories, GitHub CLI/API, and Vast.ai HTTP API.

## Global Constraints

- Begin from branch `feat/vast-cloud-run-lifecycle` in worktree `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`.
- Require commit `1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0` to be an ancestor of `HEAD`; do not redo Tasks 1 through 10 of `docs/superpowers/plans/2026-07-31-workflow-embedded-model-metadata-bridge.md` or the three review corrections already contained in that commit.
- Read `AGENTS.md`, the design paired with this plan, the two preceding workflow designs, and `docs/remote-worker-bootstrap-review.md` completely before editing.
- Use `superpowers:receiving-code-review` before evaluating review findings, `superpowers:test-driven-development` for every behavior change, `superpowers:verification-before-completion` before success claims or commits, and `superpowers:requesting-code-review` at the review gates.
- Use `certifying-comfyui-cloud-workflows` for the actual canvas metadata and free Cloud Run preflight in Task 9 and for sanitized certification evidence in Task 11.
- Keep the deterministic worker archive worker-only. Never accept a codeload source archive or an unreviewed archive member.
- The reviewed archive URL is always an immutable release URL for repository `wuraaang/ComfyUI-Cloud-Run`, a 40-character lowercase commit, and the matching 64-character lowercase SHA-256.
- Disable proxies and automatic redirects. Accept either direct HTTP `200` or exactly one HTTP `302` to `release-assets.githubusercontent.com`, followed by direct HTTP `200`.
- Never persist, return, log, or include the temporary signed redirect target in an exception.
- Never commit a release archive, rendered template request, remote lock, local `worker-release.json`, API key, token, workflow, input, output, instance identifier, provider response, or signed URL.
- Do not infer a model source from a filename. Unknown or changed live selections remain `mapping_required`.
- Do not execute the private workflow locally. Do not export it into this repository.
- Implementation permission, GitHub release permission, Vast template permission, and paid-test permission are separate. A permission for one boundary does not authorize another.
- No offer search, instance creation, paid confirmation, model download, model transfer, workflow execution, R2 mutation, template mutation, or release publication occurs without the exact gate in the corresponding task.
- The first live acceptance uses the actual intended private input, not the previous 206-byte synthetic proof input.
- Before paid confirmation require a new message stating maximum total instance creates, maximum hourly price, and either absolute maximum duration or total maximum cost.
- Default the first live acceptance to `max_instance_creates = 1`; a value of `2` is valid only when the new paid authorization explicitly allows two total creates.
- Require verified on-demand offers with reliability `>= 0.99` and finite advertised download bandwidth `>= 500` Mbps. Prefer `1,000` Mbps, saturate ranking at that target, and never relax either floor automatically.
- Treat the displayed transfer duration as a theoretical lower bound derived from fresh preflight bytes, never as a four-minute startup guarantee.
- Use only `/opt/portal-aio/caddy_manager/caddy`, the path invoked by the reviewed official Vast base-image source; reject generic or duplicate Caddy candidates and revalidate the real regular executable at worker boot.
- After every code change run focused tests, then the complete suite, then `scripts/check.sh` twice consecutively before publication or paid use.
- Stop at every authorization boundary. The session that wrote and pushed this plan authorizes none of its implementation or publication tasks.

---

## Session bootstrap and current state

Run these read-only commands before changing anything:

```bash
cd /Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes
git status --short --branch
git branch --show-current
git rev-parse HEAD
git merge-base --is-ancestor 1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0 HEAD
git log --oneline --decorate -12
gh pr view 2 --json number,state,isDraft,headRefName,headRefOid,url,mergeable
```

Expected:

- branch is `feat/vast-cloud-run-lifecycle`;
- the ancestor command exits `0`;
- PR `#2` is open and draft;
- only changes intentionally made in this plan are present.

The 2026-08-01 planning baseline before Task 6A is commit
`cd041cae3dbe87fd67751f20586c6714708cf7ca`. If the human validates the
amendment by pasting the fresh-session handoff below, the only expected
uncommitted starting changes are this plan and its paired design. Preserve
them, inspect them, and include them in the reviewed Task 6A commit; do not
discard or overwrite them.

Read all required contracts:

```bash
sed -n '1,260p' AGENTS.md
sed -n '261,620p' AGENTS.md
sed -n '1,260p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '261,620p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '621,980p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '1,260p' docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
sed -n '261,620p' docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
sed -n '1,560p' docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md
sed -n '1,300p' docs/remote-worker-bootstrap-review.md
```

If any read is truncated, continue from its last displayed line to end of file.

Before Task 1, obtain explicit authorization to edit code/tests/docs and push implementation commits to the existing branch and draft PR. Do not treat the push of this plan as that authorization.

## File map

### New focused files

- `remote_worker/gateway.py`: validate the runtime token, select one reviewed Caddy binary, supervise Caddy and the loopback Python worker, and shut both down safely.
- `tests/python/test_worker_gateway.py`: isolated process, environment, binary-selection, secret-redaction, and shutdown tests.
- `scripts/build_worker_release_bundle.py`: create the deterministic archive with its exact immutable tag, asset name, URL, size, digest, and sanitized metadata.
- `scripts/render_worker_template.py`: validate the release metadata and render the fixed remote lock, fixed base64 `onstart`, and private-template request in a private output directory.
- `scripts/publish_worker_template.py`: perform only the exact Vast template list, single-create, and read-back operations with bounded secret-safe transport and typed response normalization.
- `scripts/write_worker_release_lock.py`: atomically write the final owner-private `0600` local lock after a verified template creation.
- `tests/python/test_worker_release_tools.py`: deterministic release naming, template rendering, secret exclusion, round-trip, file-permission, and fail-closed input tests.
- `tests/python/test_worker_template_api.py`: exact template API queries, response schemas, credential non-disclosure, no-redirect/no-proxy transport, single-create, and ambiguous-result reconciliation tests.

### Existing files changed together

- `remote_worker/bootstrap.py` and `tests/python/test_worker_bootstrap.py`: immutable release URL validation and the single-host redirect transport.
- `remote_worker/gateway.py` and `tests/python/test_worker_gateway.py`: replace the incorrect generic Caddy candidates with the one official Vast portal path and preserve fail-closed runtime validation.
- `remote_worker/Caddyfile`, `scripts/build_worker_artifact.py`, `tests/python/test_worker_bootstrap.py`, and `tests/python/test_repository_contract.py`: disable unused Caddy services and explicitly review/package the gateway file.
- `cloud_run/models.py`, `cloud_run/service.py`, `cloud_run/lifecycle.py`, `cloud_run/routes.py`, `web/js/cloud-run.js`, and `web/js/session-console.js`: persist, revalidate, enforce, submit, and render `max_instance_creates`.
- `tests/python/test_models.py`, `tests/python/test_service.py`, `tests/python/test_lifecycle.py`, `tests/python/test_routes.py`, `tests/python/test_repository.py`, `tests/python/test_fake_session_integration.py`, `tests/js/cloud-run-ui.test.mjs`, and `tests/js/session-console.test.mjs`: regression coverage for the total-create boundary.
- `cloud_run/constants.py`, `cloud_run/vast.py`, `cloud_run/offers.py`, `cloud_run/session_service.py`, `cloud_run/models.py`, `cloud_run/service.py`, `cloud_run/lifecycle.py`, `web/js/cloud-run.js`, and `web/js/session-console.js`: fixed connection floors, target-saturated ordering, theoretical estimates, quote persistence, confirmation revalidation, and inert UI rendering.
- `tests/python/test_vast.py`, `tests/python/test_offers.py`, `tests/python/test_session_service.py`, `tests/python/test_models.py`, `tests/python/test_service.py`, `tests/python/test_lifecycle.py`, `tests/python/test_fake_session_integration.py`, `tests/js/cloud-run-ui.test.mjs`, and `tests/js/session-console.test.mjs`: connection-quality provider, policy, persistence, lifecycle, and UI regression coverage.
- `scripts/check.sh`: include the new reviewed worker and release-tool files in repository gates.
- `README.md`, `docs/project-state.md`, and `docs/remote-worker-bootstrap-review.md`: record the implemented offline contract, then separately record sanitized publication/live evidence only after it exists.

### Private temporary outputs that must never enter Git

- deterministic worker `.tar.gz`;
- `release-metadata.json`;
- `remote-release-lock.json`;
- `template-request.json`;
- `base-template-audit.json` and `template-publication.json`;
- the local data-directory `worker-release.json`;
- downloaded verification asset and any live output.

---

### Task 1: Establish the free implementation boundary

**Files:**

- Inspect only: all files listed in the session bootstrap
- Modify: none

**Interfaces:**

- Consumes: the exact branch, required ancestor, draft PR, and explicit implementation authorization.
- Produces: a written checkpoint in the session confirming that release, template, offer, model, workflow, and paid actions remain disabled.

- [ ] **Step 1: Confirm the worktree and PR identity**

Run the session-bootstrap commands above and compare the local and PR head SHAs.

Expected: no divergence and no unrelated worktree changes. If either SHA or branch is unexpected, stop and report it without editing.

- [ ] **Step 2: Confirm repository-level immutable-release state read-only**

```bash
gh api \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/wuraaang/ComfyUI-Cloud-Run/immutable-releases
```

Expected at plan creation: `enabled` is `false`. Do not call `PUT` in this task.

- [ ] **Step 3: Record the no-mutation checkpoint**

State in commentary that the implementation authorization covers local
code/tests/docs and branch pushes only. Explicitly state that Tasks 7 through
11 each retain their documented authorization gate; only the validated
overnight handoff may conditionally preauthorize Tasks 7 and 8, never 9--11.

---

### Task 2: Accept the deterministic GitHub Release asset without opening arbitrary redirects

**Files:**

- Modify: `remote_worker/bootstrap.py`
- Modify: `tests/python/test_worker_bootstrap.py`

**Interfaces:**

- Consumes: `worker_commit: str`, `worker_archive_sha256: str`, `worker_archive_size_bytes: int`, and the fixed repository identity.
- Produces: `DownloadStream(source_url: str, redirect_count: int, chunks: object)` and an unchanged strict archive verification/extraction boundary.

- [ ] **Step 1: Replace fixture URLs and write the failing release-identity tests**

Change the test lock URL to this exact construction:

```python
def release_asset_url(commit, digest):
    tag = "worker-v1-" + commit
    asset = (
        "comfyui-cloud-run-worker-"
        + commit
        + "-"
        + digest
        + ".tar.gz"
    )
    return (
        "https://github.com/wuraaang/ComfyUI-Cloud-Run/releases/download/"
        + tag
        + "/"
        + asset
    )
```

Add table-driven assertions that reject a branch archive, codeload URL, wrong owner, wrong repository, short commit, mismatched tag commit, mismatched asset commit, mismatched asset digest, extra path component, percent encoding, query, fragment, explicit port, user information, and mutable asset name.

- [ ] **Step 2: Run the identity test red**

```bash
python3 -m unittest \
  tests.python.test_worker_bootstrap.BootstrapTests.test_bootstrap_accepts_only_the_exact_release_asset_identity -v
```

Expected: fail because `_validated_lock()` still accepts `/archive/<commit>.tar.gz` and rejects the release URL.

- [ ] **Step 3: Implement the exact lock URL grammar**

Keep all current lock fields and pin checks. Replace only the URL path expression so it accepts exactly:

```python
expected_path = (
    "/wuraaang/ComfyUI-Cloud-Run/releases/download/"
    + "worker-v1-"
    + commit
    + "/comfyui-cloud-run-worker-"
    + commit
    + "-"
    + digest
    + ".tar.gz"
)
```

Require `parsed.scheme == "https"`, `parsed.netloc == "github.com"`, `parsed.path == expected_path`, and no username, password, query, fragment, or percent character.

- [ ] **Step 4: Run the identity test green**

Run the command from Step 2.

Expected: one passing test.

- [ ] **Step 5: Write failing transport tests for direct and redirected downloads**

Use fake opener responses; do not make network requests in unit tests. Cover this exact matrix:

```text
github.com direct 200, identity encoding                     accept, count 0
github.com 302 -> release-assets.githubusercontent.com 200   accept, count 1
github.com 301/303/307/308                                   reject
relative Location                                            reject
HTTP Location                                                reject
wrong host or subdomain                                      reject
userinfo, explicit port, or fragment                         reject
second redirect                                              reject
non-200 terminal status                                      reject
non-identity Content-Encoding                                reject
```

Assert every rejection has only the static text `Reviewed worker archive is unavailable.` and that the signed `Location` string is absent from `repr(error)`.

- [ ] **Step 6: Run the redirect tests red**

```bash
python3 -m unittest \
  tests.python.test_worker_bootstrap.HttpsTransportTests -v
```

Expected: fail because `_NoRedirect` currently converts the first `302` into a terminal bootstrap error and `DownloadStream` exposes `final_url`.

- [ ] **Step 7: Implement the narrow transport**

Change the value object to:

```python
@dataclass(frozen=True)
class DownloadStream:
    source_url: str
    redirect_count: int
    chunks: object
```

Use one `_NoRedirect` opener for both requests. On the first request, accept `200` directly or catch exactly one `HTTPError` with code `302`, validate its absolute `Location`, close the first response, and open the validated target once. The target validator must require HTTPS, hostname exactly `release-assets.githubusercontent.com`, no username/password, no explicit port, and no fragment. The signed query is allowed because GitHub requires it, but the target remains a local variable only.

For the terminal response require status `200` and `Content-Encoding` equal to `identity` case-insensitively. Return the original reviewed URL and redirect count, never `response.geturl()`.

Update `Bootstrap._download()` to require `stream.source_url == lock["archive_url"]` and `stream.redirect_count in {0, 1}` before consuming bytes. Preserve all current nonempty chunk, maximum size, exact size, SHA-256, gzip, member, and layout checks.

- [ ] **Step 8: Run bootstrap tests green**

```bash
python3 -m unittest tests.python.test_worker_bootstrap -v
```

Expected: all bootstrap and artifact tests pass.

- [ ] **Step 9: Commit the transport correction**

```bash
git add remote_worker/bootstrap.py tests/python/test_worker_bootstrap.py
git commit -m "fix: bootstrap immutable worker release asset"
```

---

### Task 3: Supervise Caddy and the loopback worker with a secret-minimal boundary

**Files:**

- Create: `remote_worker/gateway.py`
- Create: `tests/python/test_worker_gateway.py`
- Modify: `remote_worker/bootstrap.py`
- Modify: `remote_worker/Caddyfile`
- Modify: `scripts/build_worker_artifact.py`
- Modify: `tests/python/test_worker_bootstrap.py`
- Modify: `tests/python/test_repository_contract.py`

**Interfaces:**

- Consumes: `JUPYTER_TOKEN`, instance-scoped `CONTAINER_ID` and
  `CONTAINER_API_KEY`, fixed Caddy candidates, installed repository root, and
  `/var/lib/comfyui-cloud-run`.
- Produces: `select_caddy_binary(*, lstat_fn, access_fn) -> Path`, `validated_gateway_token(environ: Mapping[str, str]) -> str`, and `run_gateway(*, environ, popen_factory, wait_timeout_seconds) -> int`.

- [ ] **Step 1: Write failing gateway tests**

Test these exact requirements with fake `lstat`/`access` results for the one
hard-coded absolute candidate and fake process objects:

- a token outside `[A-Za-z0-9._~+/=-]{1,4096}` in UTF-8 bytes is rejected with `GatewayError("Remote Worker gateway configuration is unavailable.")`;
- exactly one regular non-symlink executable at
  `/opt/portal-aio/caddy_manager/caddy` is required;
- the documented `/opt/instance-tools/bin/caddy` symlink and generic system
  paths fail closed;
- Caddy argv is `[caddy, "run", "--config", Caddyfile, "--adapter", "caddyfile"]`;
- worker argv is `[sys.executable, "-m", "remote_worker.main", "--state-directory", "/var/lib/comfyui-cloud-run"]`;
- Caddy receives only `JUPYTER_TOKEN` plus fixed `HOME`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`; the worker environment contains only the explicit runtime and own-instance deadline allowlist and never `JUPYTER_TOKEN`;
- the Caddyfile disables the admin endpoint and automatic HTTPS, listens only on `:8765`, strips authorization, and proxies only to `127.0.0.1:8766`;
- when either child exits, the other receives terminate, then a bounded wait, then kill only after timeout;
- no error, captured argv, or logged diagnostic contains the token.

- [ ] **Step 2: Observe the gateway tests red**

```bash
python3 -m unittest tests.python.test_worker_gateway -v
```

Expected: import failure because `remote_worker.gateway` does not exist.

- [ ] **Step 3: Implement the gateway module**

Define these constants exactly:

```python
CADDY_CANDIDATES = (
    Path("/opt/portal-aio/caddy_manager/caddy"),
)
STATE_DIRECTORY = Path("/var/lib/comfyui-cloud-run")
MAX_TOKEN_BYTES = 4096
SHUTDOWN_TIMEOUT_SECONDS = 10
```

Validate the one hard-coded candidate file with `os.lstat`: regular file, not
symlink, owned by root or the current user, and executable according to
`os.access`. Resolve neither caller paths nor `PATH`. Resolve the Caddyfile only
as `Path(__file__).with_name("Caddyfile")`, require it to be a regular
non-symlink file, and pass that exact path. Create fixed private Caddy
config/data directories beneath `/var/lib/comfyui-cloud-run`, then build a
Caddy environment containing only `JUPYTER_TOKEN`,
`HOME=/var/lib/comfyui-cloud-run`, and those two fixed XDG paths. Build the
worker environment from an explicit allowlist containing
`CLOUD_RUN_SESSION_ID`, `CLOUD_RUN_COMFY_ROOT`, `CLOUD_RUN_WORKER_VERSION`,
`CONTAINER_ID`, `CONTAINER_API_KEY`, `HOME`, `LANG`, `LC_ALL`, `PATH`,
`PYTHONPATH`, `PYTHONUNBUFFERED`, and `TMPDIR`. The two `CONTAINER_*` values are
the Vast-injected, own-instance credentials required by the independently
enforced billing deadline; always remove `JUPYTER_TOKEN`, provider-account
credentials such as `VAST_API_KEY`, and every other variable.

Add the fixed global Caddy options `admin off` and `auto_https off`. Preserve the exact `:8765` bearer matcher, request-header stripping, authenticated boundary header, and `127.0.0.1:8766` reverse proxy.

Start both children without a shell, with the installed worker root as `cwd`. Wait until one exits. Terminate and reap the other using the fixed timeout. Return the worker's nonnegative exit code when both shut down normally; otherwise raise only the static `GatewayError` text.

- [ ] **Step 4: Make bootstrap execute the gateway**

Replace only the module name in the fixed bootstrap argv:

```python
[
    sys.executable,
    "-m",
    "remote_worker.gateway",
    "--state-directory",
    "/var/lib/comfyui-cloud-run",
]
```

The gateway argument parser must reject every other state directory. Keep `remote_worker.main` loopback-only on `127.0.0.1:8766`.

- [ ] **Step 5: Explicitly review the new archive member**

Add `remote_worker/gateway.py` to `ALLOWED_REMOTE_FILES`, the artifact member assertions, and the repository required-file contract. Do not broaden the glob or allowlist rule.

- [ ] **Step 6: Run the focused worker boundary green**

```bash
python3 -m unittest \
  tests.python.test_worker_gateway \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_server \
  tests.python.test_repository_contract -v
```

Expected: all selected tests pass and the artifact contains exactly the explicitly reviewed files.

- [ ] **Step 7: Commit the gateway**

```bash
git add \
  remote_worker/gateway.py remote_worker/bootstrap.py remote_worker/Caddyfile \
  scripts/build_worker_artifact.py \
  tests/python/test_worker_gateway.py \
  tests/python/test_worker_bootstrap.py \
  tests/python/test_repository_contract.py
git commit -m "feat: supervise remote worker gateway"
```

---

### Task 4: Generate deterministic release and private-template inputs

**Files:**

- Create: `scripts/build_worker_release_bundle.py`
- Create: `scripts/render_worker_template.py`
- Create: `scripts/write_worker_release_lock.py`
- Create: `tests/python/test_worker_release_tools.py`
- Modify: `tests/python/test_repository_contract.py`
- Modify: `scripts/check.sh`

**Interfaces:**

- Consumes: repository root, private output directory, exact 40-hex worker commit, deterministic `WorkerArtifact`, fixed template policy, exact reviewed base-template audit record, and a final 32-hex project template hash.
- Produces: `ReleaseMetadata`, one validated remote release-lock mapping, deterministic `onstart`, deterministic private-template request, and an owner-private `worker-release.json` accepted by `cloud_run.worker_release.load_worker_release()`.

- [ ] **Step 1: Write failing release-identity and determinism tests**

Import `build_worker_release_bundle` and `ReleaseMetadata`. For commit `"a" * 40`, build into two independent temporary directories and assert:

```python
self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
self.assertEqual(first.metadata, second.metadata)
self.assertEqual(first.metadata.tag, "worker-v1-" + "a" * 40)
self.assertEqual(
    first.metadata.asset_name,
    "comfyui-cloud-run-worker-"
    + "a" * 40
    + "-"
    + first.metadata.worker_archive_sha256
    + ".tar.gz",
)
self.assertEqual(
    first.metadata.archive_url,
    "https://github.com/wuraaang/ComfyUI-Cloud-Run/releases/download/"
    + first.metadata.tag
    + "/"
    + first.metadata.asset_name,
)
```

Reject booleans, uppercase or short commits, symlink output directories, non-private output directories, an archive larger than `MAX_ARCHIVE_BYTES`, and metadata containing an unexpected key.

- [ ] **Step 2: Run the release-tool test red**

```bash
python3 -m unittest \
  tests.python.test_worker_release_tools.ReleaseBundleTests -v
```

Expected: import failure because `scripts.build_worker_release_bundle` does not exist.

- [ ] **Step 3: Implement `build_worker_release_bundle()`**

Use this exact public metadata schema:

```python
@dataclass(frozen=True)
class ReleaseMetadata:
    schema_version: int
    repository: str
    worker_commit: str
    tag: str
    asset_name: str
    archive_url: str
    worker_archive_size_bytes: int
    worker_archive_sha256: str
    protocol_version: str
    comfyui_core_version: str
    comfyui_frontend_version: str
    python_version: str
    destination: str
```

Require schema `1`, repository `wuraaang/ComfyUI-Cloud-Run`, protocol `1`, ComfyUI core `0.29.0`, frontend `1.47.10`, Python `3.13.12`, and destination `/opt/comfyui-cloud-run`. Call `build_worker_artifact()` once to a private temporary name, compute the exact final identity from its returned size and digest, then atomically rename it to the asset name. Write `release-metadata.json` with sorted keys, compact separators, a final newline, mode `0600`, and no local path.

The command-line contract is:

```text
python3 scripts/build_worker_release_bundle.py \
  --repository-root REPOSITORY_ROOT \
  --output-directory PRIVATE_OUTPUT_DIRECTORY \
  --worker-commit WORKER_COMMIT
```

It prints only tag, asset filename, byte size, and SHA-256. It never prints an absolute path or environment value.

- [ ] **Step 4: Run release bundle tests green**

Run the command from Step 2.

Expected: all `ReleaseBundleTests` pass.

- [ ] **Step 5: Write failing remote-lock and `onstart` round-trip tests**

Use one synthetic, secret-free base-template audit record with this exact private input schema:

```python
base = {
    "schema_version": 1,
    "hash_id": "027fba7753c024be019030fb42aed900",
    "image": "reviewed-image.example/comfyui@sha256:" + "d" * 64,
    "tag": "reviewed-pinned-tag",
    "runtype": "ssh",
    "use_ssh": True,
    "ssh_direct": True,
    "jupyter_dir": "/workspace",
}
```

Assert the renderer rejects missing, additional, mutable, control-character, shell-metacharacter, non-digest image, or wrong base-hash fields. Assert the resulting request contains only:

```text
name, image, tag, runtype, use_ssh, ssh_direct, jupyter_dir,
jup_direct, use_jupyter_lab, docker_login_repo, docker_login_user,
docker_login_pass, onstart, env, recommended_disk_space, private
```

Require `env == "-p 8765:8765"`, `private is True`, and no environment
variable, API key, token, session identity, workflow field, model URL, signed
URL, or arbitrary command. Require `jup_direct is False`,
`use_jupyter_lab is False`, and every `docker_login_*` value to be the empty
string. The fixed `env` value is Vast's documented Docker flag field for port
mappings; no undocumented `ports` key is sent. Decode the
two base64 constants from `onstart` and assert byte equality with
`remote_worker/bootstrap.py` and the compact remote lock.

- [ ] **Step 6: Run renderer tests red**

```bash
python3 -m unittest \
  tests.python.test_worker_release_tools.TemplateRendererTests -v
```

Expected: import failure because `scripts.render_worker_template` does not exist.

- [ ] **Step 7: Implement strict template rendering**

Define the remote bootstrap lock with exactly these fields and values from `ReleaseMetadata`:

```python
remote_lock = {
    "schema_version": 1,
    "archive_url": metadata.archive_url,
    "worker_commit": metadata.worker_commit,
    "worker_archive_sha256": metadata.worker_archive_sha256,
    "worker_archive_size_bytes": metadata.worker_archive_size_bytes,
    "protocol_version": metadata.protocol_version,
    "comfyui_core_version": metadata.comfyui_core_version,
    "comfyui_frontend_version": metadata.comfyui_frontend_version,
    "python_version": metadata.python_version,
    "destination": "/opt/comfyui-cloud-run",
}
```

Render a fixed POSIX `onstart` program that performs only these operations:

1. set `umask 077`;
2. create `/opt/comfyui-cloud-run-bootstrap` with mode `0700`;
3. decode the validated base64 bootstrap and remote-lock constants into two new regular files with mode `0600`;
4. set `CLOUD_RUN_WORKER_VERSION` to the validated 40-hex commit;
5. execute `python3 /opt/comfyui-cloud-run-bootstrap/bootstrap.py /opt/comfyui-cloud-run-bootstrap/release-lock.json`.

Use no downloaded script, interpolated shell path, caller command, `eval`, pipe-to-shell, here-document delimiter derived from input, or secret. The only substituted strings are validated commit and base64 alphabets. Write outputs atomically as private regular files.

Set request `name` to `cloud-run-worker-` plus the full 40-hex commit,
`recommended_disk_space` to the fixed minimum `80`, `private` to `true`, and
`env` to the single fixed port mapping `-p 8765:8765`. Copy the validated
immutable `image`, pinned `tag`, and Jupyter directory from the exact audited
base record. Require and emit the documented fixed connection contract
`runtype == "ssh"`, `use_ssh is True`, and `ssh_direct is True`; reject legacy
combined runtype strings. Emit false Jupyter-direct flags and explicit empty
Docker-registry credential fields as required by Vast's documented complete
template request. Do not copy any additional base-template field.

If the real read-only base-template record in Task 8 cannot be reduced exactly to the tested input schema without copying a secret or mutable launch field, stop. Do not loosen this renderer during publication; return to a reviewed TDD change.

- [ ] **Step 8: Write failing local-lock permission tests**

Prove `write_worker_release_lock(path, template_hash_id, metadata)`:

- accepts only a nonexistent file beneath an existing owner-private directory;
- rejects symlink, pre-existing target, group/world-writable parent, wrong template hash, and altered release metadata;
- writes exactly the existing `WorkerRelease` fields, compact sorted JSON plus newline, mode `0600`;
- never exposes a partial final file, fails without overwriting when a competing
  writer creates the target before publication, and removes its temporary file
  on every failure;
- round-trips through `load_worker_release()`.

- [ ] **Step 9: Implement the atomic private lock writer and run all tool tests**

Create one unpredictable same-directory temporary regular file with mode
`0600`, using `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW` when `O_NOFOLLOW` is
supported. Write all bytes, `fsync`, and close it before publication. Publish
without overwriting by creating the final path as an atomic hard link to that
temporary file; a pre-existing or concurrently created target must make the
operation fail closed. Unlink the temporary name, `fsync` the parent directory,
then call `load_worker_release()` and compare every returned field before
reporting success. On every failure, remove only the known temporary file and
leave any pre-existing final target untouched. Do not use `os.replace()` for
this lock because it would overwrite a target created by a competing writer.

```bash
python3 -m unittest tests.python.test_worker_release_tools -v
```

Expected: all release-tool tests pass.

- [ ] **Step 10: Extend repository gates explicitly**

Add the three scripts and `remote_worker/gateway.py` to the expected reviewed file lists. Add a check that two release bundles built at the same commit are byte-identical and have identical sanitized metadata. Build only in `tempfile.TemporaryDirectory()`; do not leave an archive in the worktree.

Run:

```bash
python3 -m unittest tests.python.test_repository_contract -v
bash -n scripts/check.sh
```

Expected: repository contracts pass and shell syntax is valid.

- [ ] **Step 11: Commit deterministic publication tooling**

```bash
git add \
  scripts/build_worker_release_bundle.py \
  scripts/render_worker_template.py \
  scripts/write_worker_release_lock.py \
  scripts/check.sh \
  tests/python/test_worker_release_tools.py \
  tests/python/test_repository_contract.py
git commit -m "feat: build immutable worker release inputs"
```

---

### Task 5: Persist and enforce the total Vast instance-create budget

**Files:**

- Modify: `cloud_run/models.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `cloud_run/routes.py`
- Modify: `web/js/cloud-run.js`
- Modify: `web/js/session-console.js`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_service.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/python/test_repository.py`
- Modify: `tests/python/test_fake_session_integration.py`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `tests/js/session-console.test.mjs`

**Interfaces:**

- Consumes: `max_instance_creates: int` from paid preview, limited to `1` or `2`.
- Produces: one immutable quote field rendered to the human and checked before initial create and before any replacement search/create.

- [ ] **Step 1: Write the failing quote-model tests**

Add `max_instance_creates` to the test quote factory and assert:

```python
self.assertEqual(quote.max_instance_creates, 1)
self.assertEqual(quote.to_record()["max_instance_creates"], 1)
self.assertEqual(quote.public_payload()["max_instance_creates"], 1)
```

Table-test rejection of `None`, `True`, `0`, `3`, `1.0`, and `"1"`. Test one legacy persisted quote without the key and require a conservative restored value of `1`.

- [ ] **Step 2: Observe the model test red**

```bash
python3 -m unittest tests.python.test_models -v
```

Expected: assertions fail because `OfferQuote` has no total-create field.

- [ ] **Step 3: Add the durable quote field**

Add this final dataclass field after the existing optional provider identities:

```python
max_instance_creates: int = 1
```

In `__post_init__`, require `type(self.max_instance_creates) is int` and membership in `{1, 2}`. Include it in `to_record()` automatically and in `public_payload()` for reviewed quotes. When upgrading the existing legacy quote shape in `from_record()`, inject `max_instance_creates: 1`; never infer `2`.

- [ ] **Step 4: Run model and repository tests green**

```bash
python3 -m unittest \
  tests.python.test_models \
  tests.python.test_repository -v
```

Expected: all selected tests pass after updating explicit test fixtures.

- [ ] **Step 5: Write failing preview, route, and UI tests**

Require session creation JSON to contain exactly:

```json
{
  "preflight_id": "preflight-1",
  "offer_id": "42",
  "idempotency_key": "session-key",
  "deadline": {"mode": "finite", "duration_seconds": 7200},
  "max_instance_creates": 1
}
```

Backend tests must reject a missing key and all invalid values before offer revalidation or provider mutation. UI tests must prove the control offers only `1` and `2`, defaults to `1`, sends the selected integer, and renders `Maximum total instance creates: 1` in the paid review.

- [ ] **Step 6: Observe preview tests red**

```bash
python3 -m unittest \
  tests.python.test_service \
  tests.python.test_routes -v
npm test -- --test-name-pattern='instance creates|paid review'
```

Expected: new assertions fail because the route, service, and UI do not accept or display the field.

- [ ] **Step 7: Thread the field through preview**

Change the service signature to:

```python
async def preview_session(
    self,
    *,
    preflight_id,
    offer_id,
    idempotency_key,
    deadline,
    max_instance_creates,
):
```

Validate with one helper before `_eligible_offer()`:

```python
def _max_instance_creates(value):
    if type(value) is not int or value not in {1, 2}:
        raise CloudRunValidationError(
            "Maximum total instance creates must be 1 or 2."
        )
    return value
```

Persist the result in `OfferQuote`. Make `/cloud-run/api/sessions` require the exact key and forward it. Duplicate idempotent preview must return its original quote; a changed retry payload cannot alter the persisted limit.

In `confirm_session()`, before either state transition or provider call, require `1 + session.retry_count <= session.quote.max_instance_creates`. A violated persisted contract transitions to `FAILED` with `The authorized total instance-create limit was reached.` and performs no provider request. This is the confirmation-time recheck; confirmation accepts no replacement limit from the browser.

Add a labelled `<select>` in `web/js/cloud-run.js`, containing only integer-valued options `1` and `2`, with `1` selected. Send `Number(select.value)`. Add the exact rendered line in `renderQuote()`.

- [ ] **Step 8: Run preview, route, and UI tests green**

```bash
python3 -m unittest \
  tests.python.test_service \
  tests.python.test_routes -v
npm test
```

Expected: Python selections and all Node tests pass.

- [ ] **Step 9: Write the red lifecycle regression for limit one**

Create a failed boot session with `quote.max_instance_creates == 1`, one existing instance, successful DELETE, and fresh inventory absence. Invoke `handle_session_boot_failure(session.session_id, failure_code="boot_timeout")` and assert:

```python
self.assertEqual(result.state, SessionState.FAILED)
self.assertIsNone(result.instance_id)
self.assertEqual(result.retry_count, 0)
self.provider.search_offers.assert_not_awaited()
self.provider.create_instance.assert_awaited_once()
```

The single `create_instance` assertion represents the already recorded initial create in the test harness; capture its count before the handler and assert the handler does not increase it. Also assert the diagnostic is `The authorized total instance-create limit was reached.`

- [ ] **Step 10: Observe the lifecycle regression red**

```bash
python3 -m unittest \
  tests.python.test_lifecycle.SessionLifecycleTests.test_limit_one_destroys_failed_boot_without_replacement_search -v
```

Expected: fail because current lifecycle searches and creates one replacement after verified destruction.

- [ ] **Step 11: Enforce the budget before replacement search**

After verified absence and before release validation, blacklist mutation, or offer search, compute the number of already consumed creates as `1 + session.retry_count`. If it is greater than or equal to `session.quote.max_instance_creates`, transition to terminal `FAILED` with no instance and the static diagnostic from Step 9.

Apply the same guard to the legacy attempt replacement path because it shares `OfferQuote`. Existing tests that intentionally prove one replacement must construct quotes with `max_instance_creates=2`.

- [ ] **Step 12: Prove limit two, ambiguity, idempotence, and recovery**

Add or update tests proving:

- limit `2` retains exactly one replacement after verified destruction;
- a second boot failure performs no third create;
- an ambiguous initial create adopted by label remains one consumed create;
- an ambiguous replacement adopted by label remains the second consumed create;
- duplicate confirmation never invokes create again;
- a forged or incompatible offered session whose consumed-count expression exceeds its persisted limit fails before initial provider mutation;
- recovery adopts one labelled instance without changing `retry_count` or the limit;
- multiple labelled instances remain a residual-billing failure.

Run:

```bash
python3 -m unittest \
  tests.python.test_lifecycle \
  tests.python.test_service \
  tests.python.test_fake_session_integration -v
```

Expected: all lifecycle, service, and integration tests pass.

- [ ] **Step 13: Commit the machine-enforced create budget**

```bash
git add \
  cloud_run/models.py cloud_run/service.py cloud_run/lifecycle.py \
  cloud_run/routes.py web/js/cloud-run.js web/js/session-console.js \
  tests/python/test_models.py tests/python/test_service.py \
  tests/python/test_lifecycle.py tests/python/test_routes.py \
  tests/python/test_repository.py \
  tests/python/test_fake_session_integration.py \
  tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
git commit -m "fix: enforce total Vast instance create limit"
```

---

### Task 6: Pass the complete offline gate and obtain code review

**Files:**

- Modify: `README.md`
- Modify: `docs/project-state.md`
- Modify: `docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md`
- Modify: `docs/superpowers/plans/2026-07-31-immutable-worker-release-and-live-workflow-acceptance.md`
- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify: `tests/python/test_repository_contract.py`
- Modify: only source/test files required by demonstrated review findings

**Interfaces:**

- Consumes: Tasks 2 through 5 and their focused green tests.
- Produces: two consecutive complete gates, sanitized offline documentation, a pushed draft PR, and a fresh review with no unresolved demonstrated defect.

- [ ] **Step 1: Update offline documentation truthfully**

Document the immutable release identity, one-redirect host rule, gateway supervisor, deterministic generation commands, private lock boundary, and total-create semantics. Preserve explicit statements that no immutable release, private template, local live lock, offer search, paid instance, or live workflow run exists yet.

Do not paste a local temporary path, redirect target, provider payload, token, workflow, input name, or model file content.

- [ ] **Step 2: Run the focused suite**

```bash
python3 -m unittest \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_gateway \
  tests.python.test_worker_release_tools \
  tests.python.test_models \
  tests.python.test_service \
  tests.python.test_lifecycle \
  tests.python.test_routes \
  tests.python.test_fake_session_integration \
  tests.python.test_repository_contract -v
npm test
```

Expected: all selected Python and Node tests pass.

- [ ] **Step 3: Run the entire Python and Node suites**

```bash
python3 -m unittest discover -s tests/python -v
npm test
```

Expected: all tests pass, with only repository-documented skips.

- [ ] **Step 4: Run the complete gate twice consecutively**

```bash
scripts/check.sh
scripts/check.sh
```

Expected on both runs: the same deterministic worker SHA, all Python/Node tests and compile/syntax/boundary/public-artifact checks pass, ending with `[check] all checks passed`.

- [ ] **Step 5: Review the diff and public boundary**

```bash
git diff --check
git status --short
git diff --stat 1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0 HEAD
git diff 1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0 HEAD -- \
  remote_worker scripts cloud_run web tests README.md docs
```

Run a public-content scan without printing matches that could be private:

```bash
python3 - <<'PY'
from pathlib import Path
import re

patterns = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9_=.-]{32,}"),
    re.compile(r"release-assets\.githubusercontent\.com/.+[?&]"),
)
for path in Path(".").rglob("*"):
    if not path.is_file() or ".git" in path.parts:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        continue
    if any(pattern.search(text) for pattern in patterns):
        raise SystemExit("private or signed material detected in public files")
print("public content scan passed")
PY
```

Expected: clean diff check and `public content scan passed`.

- [ ] **Step 6: Request code review**

Use `superpowers:requesting-code-review`. Ask the reviewer to inspect these exact risks:

- redirect validation and signed-target non-disclosure;
- archive URL/commit/digest binding and strict extraction;
- Caddy binary selection, token lifetime, subprocess shutdown, and loopback binding;
- generated shell/template injection and file permissions;
- total-create enforcement before any replacement search or create;
- idempotent confirmation, ambiguous create reconciliation, recovery, destruction, and residual billing;
- public artifact and private-data boundaries.

- [ ] **Step 7: Process review findings rigorously**

Use `superpowers:receiving-code-review`. For each finding, locate the exact code path and reproduce it with a failing regression test before changing implementation. Reject speculative scope expansion with evidence. After each demonstrated correction, run its focused test, the related suite, and repeat Steps 3 through 5.

- [ ] **Step 8: Commit documentation/review corrections and push**

```bash
git add README.md docs/project-state.md docs/remote-worker-bootstrap-review.md \
  tests/python/test_repository_contract.py
git diff --cached --check
git commit -m "docs: record immutable worker release contract"
git push origin feat/vast-cloud-run-lifecycle
```

If review corrections touched other files, stage them explicitly in their own focused commits before the documentation commit. Verify:

```bash
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/feat/vast-cloud-run-lifecycle)
test "$LOCAL_HEAD" = "$REMOTE_HEAD"
gh pr view 2 --json state,isDraft,headRefOid,url,mergeable
git status --short --branch
```

Expected: local and remote SHA match, PR remains open/draft, and the worktree is clean.

- [ ] **Step 9: Stop at the next authorization boundary**

Report the offline evidence. If the 2026-08-01 connection-quality amendment has
not received explicit implementation permission, stop before Task 6A. After
Tasks 6A, 6B, and 6C are green, reviewed, committed, pushed, and re-verified,
request the separate or conditionally preauthorized GitHub release permission
required by Task 7.

---

## Approved accelerated execution amendment — 2026-08-01

The human approved a file-disjoint parallel execution of Tasks 6A, 6B, and 6C
after the original sequential run proved unnecessarily slow. Each behavior
change still follows an observed focused RED/GREEN cycle. Implementers do not
commit independently and may not overlap file ownership. The controller then
integrates documentation, runs the combined suites, obtains one whole-change
review covering every risk named by the three task gates, processes every
demonstrated finding with TDD, and runs `scripts/check.sh` twice consecutively
on the final reviewed code. Targeted commits and the branch push occur only
after that integrated gate. This changes scheduling and removes redundant
intermediate full-gate/review repetitions; it does not weaken provider,
publication, provenance, secret, immutability, or paid-action boundaries.

---

### Task 6A: Enforce and expose the reusable Vast connection-quality policy

**Authorization gate:** Obtain an explicit message authorizing the
2026-08-01 connection-quality amendment, its tests/documentation, commits, and
push to the existing draft PR. This permits only code and offline/fake-provider
verification. It does not authorize a real offer search, GitHub release
publication, Vast template mutation, instance creation, model download, or
workflow execution.

**Files:**

- Modify: `cloud_run/constants.py`
- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/offers.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `web/js/cloud-run.js`
- Modify: `web/js/session-console.js`
- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_offers.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_service.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_fake_session_integration.py`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `tests/js/session-console.test.mjs`
- Modify: `README.md`
- Modify: `docs/project-state.md`

**Interfaces:**

- Consumes: normalized Vast `reliability`, `inet_down`, `disk_bw`, bandwidth
  prices, the fresh preflight `transfer_bytes`, the existing hourly/VRAM/disk
  caps, blacklist, and exact-offer revalidation.
- Produces: constants `MIN_VAST_RELIABILITY = 0.99`,
  `MIN_VAST_INET_DOWN_MBPS = 500`, and
  `PREFERRED_VAST_INET_DOWN_MBPS = 1000`; one shared
  `offer_quality_key(offer)`; one
  `estimated_transfer_seconds(transfer_bytes, inet_down_mbps)` helper; offer
  payload field `estimated_transfer_seconds`; and persisted quote fields
  `inet_down_mbps` and `disk_bw_mbps`.

- [ ] **Step 1: Write failing provider-contract tests**

Update `tests/python/test_vast.py` so one default `search_offers()` call makes
exactly two provider requests. Both retain the existing GPU, price, disk,
rental, and verification clauses and require reliability `0.99`. The target
request contains:

```python
"reliability": {"gte": 0.99},
"inet_down": {"gte": 1000},
"order": [["reliability", "desc"], ["disk_bw", "desc"], ["dph_total", "asc"], ["id", "asc"]],
```

The fallback request contains:

```python
"reliability": {"gte": 0.99},
"inet_down": {"gte": 500, "lt": 1000},
"order": [["inet_down", "desc"], ["reliability", "desc"], ["disk_bw", "desc"], ["dph_total", "asc"], ["id", "asc"]],
```

Add table-driven normalization cases proving that missing/non-finite
`inet_down`, `499`, reliability `0.989`, and missing `type`, `num_gpus`,
`rentable`, both verification forms, or `disk_space` evidence are rejected, while a complete
offer at exactly `500` Mbps and reliability `0.99` is accepted. Prove that the
documented `verification == "verified"` string is also accepted, while a
conflict with boolean `verified` is rejected. Prove that the
two normalized result sets merge by offer ID and return at most
`2 * OFFER_SEARCH_LIMIT` unique candidates for the shared local quality sort.
Add tests that stricter explicit floors remain supported and any explicit value
below `0.99` or `500` is rejected before HTTP. A stricter download floor below
`1000` raises both applicable lower bounds; at `1000` or above, only the target
request is made because the fallback interval is empty. Exact-ID `get_offer()`
still makes one request with the hard floors.

- [ ] **Step 2: Run the provider tests red**

```bash
python3 -m unittest \
  tests.python.test_vast.VastRequestTests.test_search_posts_exact_read_only_contract_and_normalizes_result \
  tests.python.test_vast.VastNormalizationAndErrorTests -v
```

Expected: failures show that the current implementation makes one legacy
request, defaults to reliability `0.95`, has no bandwidth floor, accepts
incomplete provider evidence, and permits weaker explicit values.

- [ ] **Step 3: Implement the fixed provider and local floors**

Add to `cloud_run/constants.py`:

```python
MIN_VAST_RELIABILITY = 0.99
MIN_VAST_INET_DOWN_MBPS = 500
PREFERRED_VAST_INET_DOWN_MBPS = 1000
```

Import those values in `cloud_run/vast.py`. Validate every caller-provided
minimum before HTTP and raise `VastConfigurationError` when it is below the
fixed floor; a default argument alone is insufficient. `search_offers()` uses
one owned HTTP session. The target lower bound is
`max(PREFERRED_VAST_INET_DOWN_MBPS, min_inet_down_mbps)`. When the caller floor
is below the preferred target, execute the disjoint fallback interval from the
caller floor inclusive to the preferred target exclusive; otherwise omit that
empty query. Execute the target and applicable fallback payloads, then merge
normalized offers by exact ID in deterministic order with a hard maximum
of `2 * OFFER_SEARCH_LIMIT` unique candidates.
The target query orders by reliability, disk bandwidth, price, and offer ID.
The fallback query is capped below 1,000 Mbps, orders first by speed, and then
uses the same tie-breakers.
Provider-side truncation therefore matches the saturated local quality policy
as closely as the bounded query surface allows: raw 5,000 Mbps rows cannot
crowd out a better 1,000 Mbps target, and the fallback query supplies the best
fallback if no target exists. Any query failure fails the whole search; it
never becomes permission to weaken a threshold.

`get_offer()` stays one ID-scoped request with reliability `0.99` and bandwidth
`500`. Local normalization requires explicit complete evidence for every
provider gate and rejects missing, non-finite, or below-floor values. Keep the
normalized public field name `inet_down_mbps`: the implementation explicitly
follows Vast's CLI and marketplace-guide Mbps contract, while documenting the
conflicting `MB/s` label on the API page instead of silently changing scale.

- [ ] **Step 4: Run provider tests green**

```bash
python3 -m unittest tests.python.test_vast -v
```

Expected: every Vast request, normalization, exact-offer lookup, and sanitized
error test passes without a network request.

- [ ] **Step 5: Write failing shared-ranking and estimate tests**

In `tests/python/test_offers.py`, replace the ten-percent-cheapest behavior
test with exact cases proving:

```text
999 Mbps loses to 1000 Mbps even when 999 Mbps is cheaper or more reliable
1000 Mbps beats 5000 Mbps when reliability/disk are equal and 1000 is cheaper
900 Mbps beats 600 Mbps when no target-class offer exists
equal capped speed -> reliability -> disk speed -> price -> offer ID
apply_offer_policy order equals select_best_offer priority
```

Add exact estimate assertions:

```python
self.assertEqual(
    estimated_transfer_seconds(29_347_330_907, 500),
    470,
)
self.assertEqual(
    estimated_transfer_seconds(29_347_330_907, 1000),
    235,
)
```

Also assert that booleans, negative bytes, absent/non-finite speeds, and zero
speed return `None` rather than raising or displaying a fabricated estimate.

- [ ] **Step 6: Run ranking and estimate tests red**

```bash
python3 -m unittest tests.python.test_offers.RankingPolicyTests -v
```

Expected: failures identify the current ten-percent price window,
reliability-first key, and missing estimate helper.

- [ ] **Step 7: Implement one saturated quality key and estimate helper**

In `cloud_run/offers.py`, remove the similar-price shortlist from
`select_best_offer()` and define the reusable key with this behavior:

```python
def offer_quality_key(offer):
    down = _finite(offer.get("inet_down_mbps"), default=-math.inf)
    return (
        -min(down, PREFERRED_VAST_INET_DOWN_MBPS),
        -_finite(offer.get("reliability")),
        -_finite(offer.get("disk_bw_mbps")),
        _finite(offer.get("dph_total"), default=math.inf),
        _offer_id_key(offer),
    )
```

Both `apply_offer_policy()` and `select_best_offer()` use this exact key after
their existing blacklist, bait-price, and exact requested-GPU checks. Define:

```python
def estimated_transfer_seconds(transfer_bytes, inet_down_mbps):
    if type(transfer_bytes) is not int or transfer_bytes < 0:
        return None
    speed = _finite(inet_down_mbps, default=-1)
    if speed <= 0:
        return None
    return math.ceil(transfer_bytes * 8 / (speed * 1_000_000))
```

Do not add adaptive thresholds, regional heuristics, probes, or new settings.

- [ ] **Step 8: Run ranking and offer-policy tests green**

```bash
python3 -m unittest tests.python.test_offers -v
```

Expected: all blacklist, bait-price, deterministic ordering, exact GPU, target
saturation, fallback, and estimate tests pass.

- [ ] **Step 9: Write failing preflight, quote, revalidation, and UI tests**

Add coverage proving all of the following before implementation:

- `SessionService.search_offers()` attaches the estimate derived from that
  exact preflight's aggregate `transfer_bytes`, without exposing a workflow or
  private input identity;
- `OfferQuote` record/public round-trips preserve finite nonnegative
  `inet_down_mbps` and `disk_bw_mbps`; malformed values fail closed and legacy
  unbound records receive `None` only for backward-compatible inspection;
- both session and legacy-attempt preview paths plus both replacement quote
  construction paths copy the two reviewed metrics;
- both `_revalidated_session_offer()` and legacy `_revalidated_offer()` accept
  1,200 -> 1,000 Mbps, reject 1,200 -> 999 Mbps, accept an unchanged 850 Mbps
  fallback, and reject 850 -> 849 Mbps without `create_instance()`;
- offer rows render `1000 Mbps download`, disk MB/s, bandwidth prices, and a
  label containing `theoretical`; paid review renders the same metrics and
  derives the estimate from persisted quote bytes;
- all provider strings remain inert `textContent`; missing metrics display
  `unavailable` and never `NaN`, `Infinity`, or HTML.

- [ ] **Step 10: Run the cross-layer tests red**

```bash
python3 -m unittest \
  tests.python.test_session_service \
  tests.python.test_models \
  tests.python.test_service \
  tests.python.test_lifecycle \
  tests.python.test_fake_session_integration -v
node --test tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
```

Expected: focused failures are limited to the new estimate, quote fields,
revalidation, and display assertions.

- [ ] **Step 11: Implement estimate propagation, durable review, and display**

In `cloud_run/session_service.py`, await the existing offer search once and
return a new sanitized offer mapping per result containing:

```python
"estimated_transfer_seconds": estimated_transfer_seconds(
    result.transfer_bytes,
    offer.get("inet_down_mbps"),
)
```

Do not mutate the provider's input mapping. Add optional
`inet_down_mbps`/`disk_bw_mbps` fields to `OfferQuote`, validate them as finite
nonnegative numbers when present, include them in record/public payloads, and
copy them at all four initial/replacement quote constructors across the session
and legacy-attempt paths.

During exact-offer confirmation, make both `_revalidated_session_offer()` and
legacy `_revalidated_offer()` first reapply the hard provider/local policy,
then require:

```python
current_inet_down_mbps >= min(
    quoted_inet_down_mbps,
    PREFERRED_VAST_INET_DOWN_MBPS,
)
```

If the quoted or current metric is absent, malformed, or below the fixed
floor, return unavailable before any create. In `web/js/cloud-run.js` and
`web/js/session-console.js`, use finite-number helpers and `textContent` to
show provider-advertised download Mbps, disk MB/s, target/fallback status,
bandwidth prices, and `theoretical transfer ≈ ...`; explicitly state that
actual startup can be longer.

- [ ] **Step 12: Run all connection-quality tests green**

```bash
python3 -m unittest \
  tests.python.test_vast \
  tests.python.test_offers \
  tests.python.test_session_service \
  tests.python.test_models \
  tests.python.test_service \
  tests.python.test_lifecycle \
  tests.python.test_fake_session_integration \
  tests.python.test_routes \
  tests.python.test_repository -v
npm test
```

Expected: all selected Python tests and the complete Node suite pass.

- [ ] **Step 13: Update truthful public documentation**

Record the fixed `0.99` reliability floor, 500 Mbps hard floor, 1,000 Mbps
target, capped ranking, theoretical-estimate formula, exact-offer downgrade
rule, and no-silent-relaxation behavior in `README.md` and
`docs/project-state.md`. Do not claim measured throughput, marketplace
availability, a four-minute startup, or a successful real search.

When and only when the human pasted the fresh-session handoff that explicitly
validates this amendment, update the paired design status from awaiting review
to approved on `2026-08-01` in the same focused documentation commit.

- [ ] **Step 14: Run the complete gate twice and obtain review**

```bash
scripts/check.sh
scripts/check.sh
git diff --check
git status --short
```

Expected on both gate runs: all Python/Node tests, security scans, and public
artifact checks pass. Use `superpowers:requesting-code-review`; require the
reviewer to inspect unit semantics, provider/local double enforcement,
the two-query provider-limit strategy, target-saturated ordering, quote
migration, both confirmation downgrade paths, replacement parity, UI wording,
and proof that no create can occur after quality revalidation fails. Process
demonstrated findings with
`superpowers:receiving-code-review` and TDD, then repeat both full gates.

- [ ] **Step 15: Commit and push only the reviewed amendment**

```bash
git add \
  cloud_run/constants.py cloud_run/vast.py cloud_run/offers.py \
  cloud_run/session_service.py cloud_run/models.py cloud_run/service.py \
  cloud_run/lifecycle.py web/js/cloud-run.js web/js/session-console.js \
  tests/python/test_vast.py tests/python/test_offers.py \
  tests/python/test_session_service.py tests/python/test_models.py \
  tests/python/test_service.py tests/python/test_lifecycle.py \
  tests/python/test_fake_session_integration.py \
  tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs \
  README.md docs/project-state.md \
  docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md \
  docs/superpowers/plans/2026-07-31-immutable-worker-release-and-live-workflow-acceptance.md
git diff --cached --check
git commit -m "feat: require fast reliable Vast offers"
git push origin feat/vast-cloud-run-lifecycle
```

Re-run `scripts/check.sh` once on the committed tree, require a clean worktree,
local/remote/PR head equality, and keep PR `#2` open and draft. Continue to the
separately reviewed Task 6B before any release. Task 7 may use only the final
HEAD after Tasks 6A, 6B, and 6C and only when its explicit conditional publication
authorization is present in the same execution session.

---

### Task 6B: Bind the gateway to the official Vast portal Caddy path

**Authorization gate:** The validated overnight handoff must explicitly
authorize this focused pre-release correction, its offline tests, documentation,
commit, and push. It authorizes read-only retrieval of the listed public Vast
source files. It does not authorize pulling/running a container, creating a
template or instance, or any paid action.

**Files:**

- Modify: `remote_worker/gateway.py`
- Modify: `tests/python/test_worker_gateway.py`
- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify: `docs/project-state.md`

**Interfaces:**

- Consumes: official Vast base-image source commit
  `46e032d852ece6edb2a2a477c5b9557cba6645bf`, whose runtime Dockerfile
  inherits the stock base image, whose Supervisor configuration invokes
  `caddy.sh`, and whose script directly executes
  `/opt/portal-aio/caddy_manager/caddy`.
- Produces: one allowlisted Caddy candidate at that path, with the existing
  regular-file, non-symlink, owner, and executable runtime checks unchanged.

- [ ] **Step 1: Reconfirm the immutable public source evidence read-only**

Fetch only these files at the exact commit through GitHub's contents API:

```text
Dockerfile.runtime
ROOT/opt/supervisor-scripts/caddy.sh
ROOT/etc/supervisor/conf.d/caddy.conf
```

Require `Dockerfile.runtime` to identify the inherited runtime image,
`caddy.conf` to invoke `/opt/supervisor-scripts/caddy.sh`, and that script to
execute `/opt/portal-aio/caddy_manager/caddy` directly. Record the commit and
public permalinks, not raw API bodies. Do not infer that `Dockerfile.runtime`
copies Caddy: it does not. If any of these exact facts differ, stop and revise
the design; do not guess or inspect a paid instance.

- [ ] **Step 2: Write the failing exact-path tests**

Update `tests/python/test_worker_gateway.py` to require:

```python
CADDY_CANDIDATES == (
    Path("/opt/portal-aio/caddy_manager/caddy"),
)
```

Prove `/usr/bin/caddy`, `/usr/local/bin/caddy`, the documented symlink
`/opt/instance-tools/bin/caddy`, a symlink at the accepted path, wrong owner,
non-regular file, and non-executable file all fail closed. Keep the exact argv,
minimal environment, shutdown, and secret-redaction assertions.

- [ ] **Step 3: Run the gateway test red**

```bash
python3 -m unittest \
  tests.python.test_worker_gateway.GatewayBinarySelectionTests -v
```

Expected: the exact-candidate assertion fails because production still lists
the two generic paths.

- [ ] **Step 4: Implement the one-path allowlist**

Replace only the candidate tuple in `remote_worker/gateway.py`:

```python
CADDY_CANDIDATES = (
    Path("/opt/portal-aio/caddy_manager/caddy"),
)
```

Do not accept the symlink, search `PATH`, install/download Caddy, loosen file
validation, or change gateway process behavior.

- [ ] **Step 5: Run focused and related tests green**

```bash
python3 -m unittest \
  tests.python.test_worker_gateway \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_release_tools -v
```

Expected: every gateway, artifact membership, bootstrap, and deterministic
release-tool test passes.

- [ ] **Step 6: Update truthful evidence and verify twice**

Document the exact source commit/path and explicitly state that source review
is not a live filesystem measurement; the worker runtime remains the final
fail-closed check. Then run:

```bash
scripts/check.sh
scripts/check.sh
git diff --check
```

Use `superpowers:requesting-code-review` for the source-to-path evidence,
symlink rejection, allowlist, worker artifact SHA change, and absence of a new
download/install surface. Process demonstrated findings with TDD and repeat
both gates.

- [ ] **Step 7: Commit, push, and freeze the release candidate**

```bash
git add remote_worker/gateway.py tests/python/test_worker_gateway.py \
  docs/remote-worker-bootstrap-review.md docs/project-state.md
git diff --cached --check
git commit -m "fix: use official Vast Caddy path"
git push origin feat/vast-cloud-run-lifecycle
```

Re-run `scripts/check.sh` on the committed tree. Require a clean worktree and
local/remote/PR head equality. Continue to the separately reviewed Task 6C;
Task 7 may use only the final HEAD after Tasks 6A, 6B, and 6C.

---

### Task 6C: Add the single-purpose Vast private-template transport

**Authorization gate:** The validated overnight handoff must explicitly
authorize this focused pre-publication implementation, its fake-transport
tests, documentation, commit, and push. It authorizes read-only access to the
three listed official Vast template API documents and registry
manifest/config metadata for the exact audited official image. It does not
authorize an offer query, image-layer pull, container execution, real template
mutation during tests, instance creation, or any paid action.

**Files:**

- Create: `scripts/publish_worker_template.py`
- Create: `tests/python/test_worker_template_api.py`
- Modify: `scripts/render_worker_template.py`
- Modify: `tests/python/test_worker_release_tools.py`
- Modify: `tests/python/test_repository_contract.py`
- Modify: `scripts/check.sh`
- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify: `docs/project-state.md`

**Interfaces:**

- Consumes: the existing owner-private API key from the settings path resolved
  by `SettingsStore`, but never through the current permissive `load()` file
  open, plus exact base hash
  `027fba7753c024be019030fb42aed900`, a generated name matching
  `cloud-run-worker-[0-9a-f]{40}`, and the exact strict request emitted by
  `render_worker_template.py`.
- Uses only: `GET https://console.vast.ai/api/v0/template/` with exact encoded
  `select_filters`, `select_cols`, and `order_by`, plus at most one
  `POST https://console.vast.ai/api/v0/template/`.
- Produces: a sanitized base-template audit or a sanitized publication record
  containing only the validated public template ID/hash and comparison result.
  Raw requests, raw responses, authorization headers, the API key, `onstart`,
  and base64 bodies are never printed or persisted.

Authoritative transport references:

- https://docs.vast.ai/api-reference/search/search-templates
- https://docs.vast.ai/api-reference/templates/create-template
- https://docs.vast.ai/api-reference/creating-and-using-templates-with-api

- [ ] **Step 1: Write the fake-transport tests red**

Create table-driven tests that require both lookup forms to call only the fixed
template endpoint. Base audit uses the exact filter:

```python
{"hash_id": {"eq": "027fba7753c024be019030fb42aed900"}}
```

Project lookup uses one exact validated generated name. Both send compact,
sorted JSON in `select_filters`, set `select_cols` to exactly
`["id","name","hash_id","image","tag","env","onstart","runtype","ssh_direct","use_ssh","jup_direct","jupyter_dir","use_jupyter_lab","docker_login_repo","docker_login_user","docker_login_pass","recommended_disk_space","private"]`,
and set deterministic `order_by=id`. Require an exact HTTP `200`, JSON object, documented success
shape, bounded response body, and either zero or exactly one normalized match
as appropriate. Missing, duplicate, malformed, additional security-relevant,
wrong-type, secret-bearing, mutable-image, uppercase hash, or conflicting rows
fail with one static sanitized exception.

Prove the transport:

- obtains the key only from a synthetic owner-private settings file through a
  supplied fake path resolver in tests and never accepts a key on argv;
- fixes HTTPS host, path, method, headers, timeout, and maximum response bytes;
- disables redirects and ambient proxies and closes every response;
- has no arbitrary URL, method, header, query, or generic payload parameter;
- never renders the authorization header, key, request, response, `onstart`, or
  base64 content through stdout, stderr, return errors, or exception chains;
- performs no POST in audit and absence-check modes.

Then prove publication validates the renderer's exact request schema before
HTTP, makes one POST at most, accepts only documented `success`, `msg`, and
`template` response fields, and requires one numeric ID plus one lowercase
32-hex `hash_id`. It then performs one exact-hash GET and compares every
security-relevant field: image digest, tag, runtype, SSH flags, Jupyter
directory, fixed `onstart`, exact `-p 8765:8765` port-only `env`, false
Jupyter-direct flags, three empty registry-credential fields, recommended disk,
and private visibility. An ambiguous connection or response failure never retries
POST; it makes only one exact-name GET and adopts only one exact content match.

Add failing renderer tests proving that the current legacy combined runtype
and undocumented `ports` request are rejected. Require the corrected renderer
to emit exactly `runtype == "ssh"`, `use_ssh is True`, `ssh_direct is True`,
`env == "-p 8765:8765"`, false Jupyter-direct flags, three empty
`docker_login_*` values, and `private is True` with no `ports` key.

- [ ] **Step 2: Run the focused tests red**

```bash
python3 -m unittest \
  tests.python.test_worker_template_api \
  tests.python.test_worker_release_tools.TemplateRendererTests -v
```

Expected: import failure because `scripts.publish_worker_template` does not
exist plus renderer failures for the current legacy runtype and `ports` payload.

- [ ] **Step 3: Implement the bounded client**

First make the focused renderer correction described in Step 1; do not change
its bootstrap or release-lock bytes. Then use a small injectable
standard-library HTTPS transport with a no-proxy opener and redirect handler
that always rejects. Use `SettingsStore` only to resolve the expected
`settings.json` path. Before any read, validate the parent and file with
`lstat`, open the file read-only with `O_NOFOLLOW`, and use `fstat` on the open
descriptor to require a current-user-owned regular file with exact mode `0600`
and unchanged device/inode. Read a bounded JSON document from that descriptor,
extract and validate only `api_key`, close it on every path, and never call the
existing `SettingsStore.load()` for this publication credential. Keep the key
in memory only and read no unrelated credential value. Tests must cover
symlinks, mode/owner/type mismatch, swap races, oversize/invalid JSON, missing
key, closure, and secret-free errors. Enforce a finite HTTP timeout, a small
fixed response cap, identity content encoding, UTF-8 JSON object, and static
sanitized failures.

Expose only these CLI actions:

```text
audit-base --output-directory PRIVATE_DIRECTORY
publish --request-file TEMPLATE_REQUEST --output-directory PRIVATE_DIRECTORY
```

`audit-base` writes one compact `base-template-audit.json` with mode `0600` and
only the renderer schema. `publish` validates the exact generated request,
checks exact-name absence, attempts one create, reconciles ambiguity read-only,
reads back by exact returned hash, compares the full security contract, and
writes one compact `template-publication.json` with mode `0600`. It prints only
the action result, public numeric ID, public hash, and boolean verified flag.
It implements no PUT, PATCH, DELETE, arbitrary endpoint, general HTTP helper,
or retrying mutation.

- [ ] **Step 4: Run focused and repository tests green**

```bash
python3 -m unittest \
  tests.python.test_worker_template_api \
  tests.python.test_worker_release_tools \
  tests.python.test_repository_contract -v
bash -n scripts/check.sh
```

Add the script and test to the explicit reviewed/public artifact lists. Tests
must use only fake sessions and synthetic keys.

- [ ] **Step 5: Verify twice and review**

```bash
scripts/check.sh
scripts/check.sh
git diff --check
```

Use `superpowers:requesting-code-review` specifically for credential flow,
redirect/proxy behavior, exact lookup filters, schema normalization,
single-mutation semantics, ambiguous-result reconciliation, response cleanup,
and absence of generic/provider-paid surfaces. Process demonstrated findings
with `superpowers:receiving-code-review` and TDD, then repeat both gates.

- [ ] **Step 6: Commit, push, and freeze the release candidate**

```bash
git add scripts/publish_worker_template.py \
  scripts/render_worker_template.py tests/python/test_worker_template_api.py \
  tests/python/test_worker_release_tools.py \
  tests/python/test_repository_contract.py scripts/check.sh \
  docs/remote-worker-bootstrap-review.md docs/project-state.md
git diff --cached --check
git commit -m "feat: publish private worker template safely"
git push origin feat/vast-cloud-run-lifecycle
```

Re-run `scripts/check.sh` on the committed tree. Require a clean worktree and
local/remote/PR head equality. This exact final reviewed HEAD, not an earlier
Task 6A or 6B SHA, is the only release candidate authorized by the conditional
Task 7 permission in the overnight handoff.

---

### Task 7: Publish and verify the immutable GitHub worker release

**Authorization gate:** Obtain a new explicit message authorizing both
repository-level immutable-release enablement and publication of one exact
worker release for the reviewed `HEAD`, or use the itemized Task 7 permission
in the validated fresh-session overnight handoff in this same execution
session. The authorization must also cover uploading and downloading the small
worker archive for verification. It does not authorize Vast mutation or paid
use.

**Files:**

- Create outside Git only: deterministic archive, sanitized metadata, downloaded verification copy
- Modify on GitHub only after authorization: immutable-release setting, one draft release, one asset, final published release/tag
- Modify in worktree: none

**Interfaces:**

- Consumes: clean pushed reviewed `HEAD` including Tasks 6A, 6B, and 6C, two
  consecutive offline gates after the final worker change,
  `build_worker_release_bundle()`, and explicit conditional release
  authorization for that final reviewed HEAD.
- Produces: one immutable release whose tag, asset name, browser URL, size, and SHA-256 match local metadata, plus a real production-bootstrap transport proof.

- [ ] **Step 1: Reconfirm the exact release scope**

```bash
git status --short --branch
git fetch origin feat/vast-cloud-run-lifecycle
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/feat/vast-cloud-run-lifecycle)
test "$LOCAL_HEAD" = "$REMOTE_HEAD"
gh pr view 2 --json state,isDraft,headRefOid,url,mergeable
gh release view "worker-v1-$LOCAL_HEAD" --json tagName,isDraft,url
```

Expected: worktree clean, local/remote/PR heads identical, and the release lookup reports that the tag does not exist. If it already exists, stop; do not edit or replace it.

- [ ] **Step 2: Build twice in one new private temporary directory**

Create a directory with `mktemp -d`, verify its owner and mode, then run the bundle builder twice into two private child directories. Compare archive bytes and both metadata documents. Keep the temporary directory path out of public logs and store it in a task-specific shell variable.

```bash
python3 scripts/build_worker_release_bundle.py \
  --repository-root "$(pwd -P)" \
  --output-directory "$RELEASE_BUILD_ONE" \
  --worker-commit "$LOCAL_HEAD"
python3 scripts/build_worker_release_bundle.py \
  --repository-root "$(pwd -P)" \
  --output-directory "$RELEASE_BUILD_TWO" \
  --worker-commit "$LOCAL_HEAD"
cmp "$RELEASE_ASSET_ONE" "$RELEASE_ASSET_TWO"
cmp "$RELEASE_METADATA_ONE" "$RELEASE_METADATA_TWO"
```

Expected: both comparisons exit `0`. Resolve the four task-specific path variables from the generated, validated metadata rather than a glob.

- [ ] **Step 3: Enable immutable releases only after confirming authorization**

Read state once more, then enable with the official API version:

```bash
gh api \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/wuraaang/ComfyUI-Cloud-Run/immutable-releases
gh api \
  --method PUT \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  repos/wuraaang/ComfyUI-Cloud-Run/immutable-releases
```

Expected: pre-read matches the authorized state transition and `PUT` returns success. Read back and require `enabled: true`. If the owner policy does not allow enablement, stop without creating a release.

- [ ] **Step 4: Create the exact draft and upload exactly one asset**

Load `TAG`, `ASSET_NAME`, `ASSET_PATH`, `SIZE`, and `SHA256` from the validated metadata. Require the tag and asset regexes again before using them.

```bash
gh release create "$TAG" \
  --repo wuraaang/ComfyUI-Cloud-Run \
  --target "$LOCAL_HEAD" \
  --title "$TAG" \
  --notes "Deterministic reviewed Remote Worker archive for commit $LOCAL_HEAD." \
  --draft
gh release upload "$TAG" "$ASSET_PATH" \
  --repo wuraaang/ComfyUI-Cloud-Run
```

Expected: one draft and one asset. Do not use `--clobber`.

- [ ] **Step 5: Verify the draft asset before publication**

Read release and asset metadata through the GitHub API. Require target commit, asset name, asset count `1`, API `size == SIZE`, API `digest == "sha256:" + SHA256`, and `browser_download_url` exactly equal to the generated URL. Download to a new private filename and require exact byte count and SHA-256.

Run the production `Bootstrap` with real `HttpsTransport`, the generated remote lock, a temporary allowed destination, and a recording exec runner. Assert `redirect_count in {0, 1}`, the installed layout verifies, and the recorded argv selects `remote_worker.gateway`. The runner must not start Caddy or the worker.

If any check fails, leave the release draft, report the sanitized mismatch, and stop. Do not publish.

- [ ] **Step 6: Publish once and verify immutability**

```bash
gh release edit "$TAG" \
  --repo wuraaang/ComfyUI-Cloud-Run \
  --draft=false
gh release verify "$TAG" --repo wuraaang/ComfyUI-Cloud-Run
gh release verify-asset "$TAG" "$DOWNLOADED_ASSET" \
  --repo wuraaang/ComfyUI-Cloud-Run
```

Expected: release verification and asset verification succeed. Read back release metadata and require published, immutable, exact target, one asset, and unchanged size/digest/URL.

- [ ] **Step 7: Remove private temporary build/download material**

Resolve and validate that the temporary root is the exact directory created in Step 2, is not the repository/worktree root, and contains only the expected generated files. Delete that one temporary root, then verify it is absent. The published GitHub asset remains.

- [ ] **Step 8: Report and stop**

Report only tag, commit, asset filename, byte size, SHA-256, public release
URL, redirect count, bootstrap proof result, and immutable verification result.
If no explicit Task 8 permission exists, request it and stop. If the validated
overnight handoff explicitly granted Task 8, continue only after every Task 7
check succeeded; no offer search or paid action is implied.

---

### Task 8: Create one private Vast template and its matching local lock

**Authorization gate:** Obtain a new explicit message authorizing creation of
one private project-specific Vast template, one matching owner-private local
release lock, and the local ComfyUI restart needed to load it, or use the
itemized Task 8 permission in the validated fresh-session overnight handoff in
this same execution session. This permission does not authorize offer search,
instance creation, or workflow execution.

**Files:**

- Create outside Git only: sanitized base-template audit record, remote lock, `onstart`, private template request, sanitized publication record, local `worker-release.json`
- Modify on Vast only after authorization: one private template
- Modify in worktree: none

**Interfaces:**

- Consumes: the verified immutable release metadata, official base hash `027fba7753c024be019030fb42aed900`, deterministic renderer, the reviewed Task 6C transport, exact image-digest provenance, and explicit template authorization.
- Produces: exactly one verified private template hash and one local `0600` lock accepted after ComfyUI restart.

- [ ] **Step 1: Audit the official base template read-only**

Run only:

```bash
python3 scripts/publish_worker_template.py \
  audit-base --output-directory "$PRIVATE_TEMPLATE_ROOT"
```

The script uses the exact authenticated `GET /api/v0/template/` lookup without
offer search. Require exactly one record whose content-derived `hash_id` is
`027fba7753c024be019030fb42aed900`. Record only the sanitized fields required
by `render_worker_template.py`; never print or retain the raw response.

Require the base record to use an immutable official image digest and pinned
tag consistent with the reviewed Vast base-image family. Apply this one exact
trust rule, with no judgment-based substitute:

1. canonicalize and require repository `docker.io/vastai/base-image` plus one
   lowercase `sha256:` digest from the audited record; never resolve provenance
   from its tag;
2. with proxies and redirects disabled, retrieve only the digest-scoped Docker
   Registry manifest. Require the returned `Docker-Content-Digest` and locally
   hashed bytes to equal the audited digest. If it is a manifest list, accept
   only one Linux/amd64 child and verify that child digest identically;
3. retrieve only that manifest's config descriptor, cap it at 1 MiB, and
   require its byte count and SHA-256 to match the descriptor; retrieve no
   layer;
4. require exact config labels
   `org.opencontainers.image.source=https://github.com/vast-ai/base-image` and
   `org.opencontainers.image.revision=46e032d852ece6edb2a2a477c5b9557cba6645bf`.

The official repository plus content-addressed config labels bind the selected
image to Task 6B's reviewed source revision, whose Dockerfile and supervisor
prove `/opt/portal-aio/caddy_manager/caddy`. A tag, absent/conflicting label,
ambiguous platform, foreign registry/repository, response mismatch, redirect,
proxy, SBOM guessed from a tag, or free-form unsigned claim is insufficient.
Do not pull image layers or claim this is a live filesystem measurement. The
gateway will still revalidate the real regular executable at boot. If any rule
fails, stop before creating anything and report the one precise blocker.

- [ ] **Step 2: Render and inspect the private request offline**

Run `scripts/render_worker_template.py` in a fresh owner-private temporary directory with the verified release metadata and sanitized base audit. Run its round-trip verifier. Inspect keys and static strings without printing base64 bodies.

Expected:

- exact base hash and immutable image digest;
- fixed `env == "-p 8765:8765"`, exposing port `8765` only and no environment variable;
- `private is True`;
- documented `runtype == "ssh"` with SSH flags true, Jupyter-direct flags false, and registry credential fields empty;
- no API key, Jupyter token, session identity, workflow, model, signed URL, caller shell, or path outside the fixed bootstrap destination;
- decoded bootstrap hash equals the repository file;
- decoded remote lock equals the verified release metadata.

- [ ] **Step 3: Reconfirm no existing project template**

Use the Task 6C `publish` action's mandatory exact-name precheck. Require zero
matches. If any match exists, the script stops and reports sanitized identity;
never create a duplicate or overwrite a template.

- [ ] **Step 4: Create exactly one private template**

Run only the reviewed single-purpose transport:

```bash
python3 scripts/publish_worker_template.py \
  publish \
  --request-file "$PRIVATE_TEMPLATE_ROOT/template-request.json" \
  --output-directory "$PRIVATE_TEMPLATE_ROOT"
```

It POSTs the exact private request to the fixed template endpoint once at most
using the existing owner-private Vast credential. It never prints or persists
the raw request, response, authorization header, key, `onstart`, or base64
body.

Expected: one success response containing one 32-lowercase-hex template `hash_id`. Any ambiguous timeout or response stops further mutation and triggers a read-only lookup by exact generated name; adopt only one exact content match.

- [ ] **Step 5: Read back and compare the template**

The same Task 6C command fetches the created template read-only by exact
returned hash. It normalizes only documented transport fields and requires
every security-relevant field to equal the rendered request: immutable image,
tag, runtype, SSH booleans, Jupyter directory, fixed `onstart`, exact port-only
`env`, false Jupyter-direct flags, empty registry credential fields,
recommended disk, and private visibility.

If comparison fails, report the template identifier and stop. Do not automatically delete or edit it.

- [ ] **Step 6: Write the local release lock**

Use `scripts/write_worker_release_lock.py` with the verified template hash and release metadata. Target the owner-private Cloud Run data directory discovered through the existing runtime configuration. Require parent mode `0700`, new file mode `0600`, current-user ownership, no symlink, and successful `load_worker_release()` round-trip.

- [ ] **Step 7: Restart locally and prove the free lock gate**

Restart the local ComfyUI/extension process without opening the private workflow. Verify the service loads the exact template hash, worker commit, archive digest, protocol, versions, and port. Call only free status/preflight-capability endpoints; do not search offers.

- [ ] **Step 8: Remove temporary template material and stop**

Delete the validated private temporary root containing
audit/request/publication/onstart/remote-lock files. Preserve only the
owner-private local release lock and private Vast template. Report sanitized
hashes and the lock-loaded result. If no explicit Task 9 permission exists,
request the actual workflow/input free-certification step and stop. If the
validated overnight handoff explicitly granted Task 9, continue only after
the lock-loaded proof succeeds; no offer search or paid authorization is
implied.

---

### Task 9: Re-certify the actual workflow and actual private input for free

**Authorization gate:** Obtain a new explicit message authorizing read-only
inspection/capture of the actual private workflow and input plus in-memory
native metadata annotations after the human has opened the actual workflow and
selected the actual input in the pinned ComfyUI browser. This permission does
not authorize local execution, export, model download, offer search, or paid
use.

**Human/browser gate:** This task is intentionally not part of the unattended
overnight handoff. The canvas and its in-memory annotations exist only in the
live frontend; the backend and a fresh Codex session cannot read or click that
browser state. The morning human must open/confirm the workflow and input, then
invoke the Cloud Run capture/preflight action while Codex verifies the
sanitized result. Do not replace that gesture with filesystem scraping,
browser-profile manipulation, synthetic workflow export, or local execution.

**Files:**

- Read only in the local ComfyUI runtime: actual open canvas and actual selected input
- Modify only in ComfyUI canvas memory when necessary: five native `properties.models` annotations
- Create in repository: none

**Interfaces:**

- Consumes: pinned ComfyUI frontend, actual workflow, actual intended private input, five proven immutable public model identities, and loaded release/template lock.
- Produces: one fresh, fully resolved, rentable free preflight tied to the actual capture; it does not produce an offer or instance.

- [ ] **Step 1: Establish the private-input identity without disclosure**

Open the actual intended workflow in pinned ComfyUI. Confirm the selected `LoadImage` value resolves under the approved ComfyUI input root to the actual intended private regular file. Record its byte size and SHA-256 only in owner-private session evidence. Do not reveal or commit its filename, path, pixels, or hash.

If the actual input is missing or the selected input is synthetic, stop. Do not substitute another file.

- [ ] **Step 2: Capture without local execution or repository export**

Use the existing Cloud Run capture action, which compiles the exact pinned frontend prompt without posting to local `/prompt`. Do not click local Queue/Run, do not execute the workflow, and do not export the private workflow into Git.

- [ ] **Step 3: Verify the five active model selections and native metadata**

Reuse each public record only if the actual active node still selects the exact `directory/name`. Each native `properties.models` entry contains exactly `name`, `url`, `directory`, `hash_type: "sha256"`, and the verified lowercase `hash`. Do not put size, revision, destination, credential, or a Cloud Run-specific field in the native record. The size listed below is the exact value that free preflight must resolve from the immutable source:

1. `diffusion_models/flux1-fill-dev.safetensors`
   - URL: `https://huggingface.co/Comfy-Org/flux1-dev/resolve/0f6b956e6e2e041fb73d079b72ec0e761506f601/split_files/diffusion_models/flux1-fill-dev.safetensors`
   - size: `23804922408`
   - SHA-256: `03e289f530df51d014f48e675a9ffa2141bc003259bf5f25d75b957e920a41ca`
2. `text_encoders/clip_l.safetensors`
   - URL: `https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/6af2a98e3f615bdfa612fbd85da93d1ed5f69ef5/clip_l.safetensors`
   - size: `246144152`
   - SHA-256: `660c6f5b1abae9dc498ac2d21e1347d2abdb0cf6c0c0c8576cd796491d9a6cdd`
3. `text_encoders/t5xxl_fp8_e4m3fn.safetensors`
   - URL: `https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/6af2a98e3f615bdfa612fbd85da93d1ed5f69ef5/t5xxl_fp8_e4m3fn.safetensors`
   - size: `4893934904`
   - SHA-256: `7d330da4816157540d6bb7838bf63a0f02f573fc48ca4d8de34bb0cbfd514f09`
4. `vae/ae.safetensors`
   - URL: `https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/22e393d707f2d13e736b1a461c958644258cd9d9/split_files/vae/ae.safetensors`
   - size: `335304388`
   - SHA-256: `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38`
5. `upscale_models/4x_foolhardy_Remacri.pth`
   - URL: `https://huggingface.co/fofr/comfyui/resolve/0cd0e3e76111f1f2e6f25091581958f381a2357e/upscale_models/4x_foolhardy_Remacri.pth`
   - size: `67025055`
   - SHA-256: `e1a73bd89c2da1ae494774746398689048b5a892bd9653e146713f9df8bca86a`

If a loader, directory, or filename differs, leave it `mapping_required` and stop the acceptance. Never adapt a URL from the filename.

- [ ] **Step 4: Run a fresh free preflight**

Require every active core/custom node, all active model rows, the actual input, output allowance, disk estimate, immutable worker release, and private template lock to resolve. This step may perform bounded metadata/HEAD verification but must not download model bodies.

Expected model-only byte floor:

```text
29,347,330,907 bytes
```

Expected transfer total:

```text
29,347,330,907 + actual deduplicated private-input bytes
+ any newly active verified artifact bytes
```

Recompute disk from the actual total. Do not reuse the historical synthetic total `29,347,331,113` or its `89 GiB` estimate.

- [ ] **Step 5: Inspect the free result and stop before search**

Require HTTP `200`, `rentable == true`, no `mapping_required`, exact manifest digest, positive output allowance, valid disk bound, and the expected dependency count derived from the actual graph. Keep all private fields local.

Do not click Search Vast GPUs. Report only sanitized counts, public model total, private-input byte contribution without identity, total transfer, disk, release digest, and `rentable` result. Request a new paid authorization.

---

### Task 10: Run one bounded paid live workflow acceptance

**Authorization gate:** Obtain a new message in this execution session that states all three required bounds: maximum total instance creates, maximum hourly price, and either maximum absolute duration or maximum total cost. Confirm whether bandwidth/storage charges shown in the quote are accepted. No earlier message or plan text counts.

**Files:**

- Create outside Git only: private runtime/session records and downloaded verified output
- Modify on Vast only within authorization: one or two total create attempts exactly as authorized, followed by verified destruction
- Modify in repository during the run: none

**Interfaces:**

- Consumes: fresh rentable actual-input preflight, exact paid authorization, private template/release lock, and machine-enforced `max_instance_creates`.
- Produces: one verified coherent output or one sanitized terminal failure, followed in every case by fresh inventory proof that no managed instance remains.

- [ ] **Step 1: Translate authorization into exact UI bounds**

Set `max_instance_creates` no higher than the authorized number and in `{1, 2}`. Set configured hourly price no higher than the authorized price. Set a finite deadline no later than the authorized duration; if the user authorized total cost instead, derive a shorter finite duration from the selected hourly quote and preserve a conservative margin for storage/bandwidth.

Restate the effective three bounds before search. If any bound is ambiguous, stop and ask; do not search.

- [ ] **Step 2: Require clean starting inventory**

Fetch Vast inventory read-only. Require no instance carrying this project's managed session labels and no unresolved residual instance from earlier work. If inventory cannot be fetched or a matching instance exists, stop before search and expose the emergency console action.

- [ ] **Step 3: Search once and review one eligible offer**

Click Search Vast GPUs once. Select only a verified on-demand, one-GPU offer
within hourly, VRAM, disk, worker-template, and authorized bounds, with
reliability at least `0.99` and advertised download bandwidth at least `500`
Mbps. Prefer a target-class offer at `1,000` Mbps or more; if only a 500--999
Mbps fallback exists, require the human to review its longer theoretical
estimate rather than silently lowering the floor. Generate the paid preview
with the authorized total-create value and finite deadline.

Review exact offer ID, GPU, hourly price, reliability, advertised download
Mbps, disk MB/s, theoretical transfer estimate, bandwidth prices, disk,
transfer bytes, output allowance, approximate active charge, duration,
template hash, worker commit, archive digest, protocol, manifest digest, and
maximum total instance creates.

If any reviewed value differs from the authorization or fresh preflight, do not confirm.

- [ ] **Step 4: Confirm the exact durable session once**

Press `Confirm & rent this GPU` once. Do not manually repeat confirmation after timeout. Let idempotency and labelled-inventory reconciliation determine whether the first create was accepted.

Immediately record sanitized timestamps and state transitions. Never print provider token, worker base URL, session secret, raw settings, raw provider response, or private input identity.

- [ ] **Step 5: Monitor deterministic bootstrap and provisioning**

Require gateway authentication, matching worker commit/protocol/session, deterministic transaction progress, exact model/input size and SHA-256 verification, install completion, `/object_info` compatibility, and the persisted finite deadline.

With create limit `1`, any boot failure must destroy the first instance, prove inventory absence, and become terminal without offer search. With an explicitly authorized limit `2`, one replacement is permitted only after verified absence and only within every original bound.

- [ ] **Step 6: Execute the captured prompt once**

Submit the already captured actual workflow only after the session reaches `ready`. Run no local prompt and no second sequential job unless the first returns a healthy same-instance retry decision within the existing deadline and the user explicitly chooses it.

- [ ] **Step 7: Retrieve and verify the output**

Download the declared remote output through the existing bounded relay. Require exact byte count, SHA-256, permitted media type, local private file permissions, and `scripts/validate_gold_output.py` structural success. Present the image for human visual confirmation without copying it into Git or public evidence.

The first structurally valid, visually coherent output ends the acceptance attempt.

- [ ] **Step 8: Destroy immediately in every terminal path**

Use the two-stage Destroy GPU review and explicit data-loss acknowledgement. Issue DELETE, then fetch fresh Vast inventory until the exact instance ID and managed label are absent within the bounded destroy verification window.

On bootstrap, provisioning, gateway, deadline, release, or compatibility failure, trigger the same immediate verified destruction. On unverifiable DELETE, keep the instance identifier and emergency Vast-console instruction visible; never report completion.

- [ ] **Step 9: Report sanitized live outcome**

Report offer class, bounded hourly price, create attempts consumed, elapsed time, public model bytes, private-input byte count without identity, output size/hash, structural/human result, approximate charge, destroy API result, and fresh inventory absence. Stop before any second workflow, template edit, release edit, merge, or general publication.

---

### Task 11: Record sanitized evidence, re-run gates, review, commit, and push

**Authorization gate:** Obtain explicit permission to publish the listed sanitized evidence to the existing feature branch and draft PR. The paid-run permission alone does not authorize public documentation of its outcome.

**Files:**

- Modify: `docs/project-state.md`
- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify when public evidence is appropriate: `docs/workflow-model-metadata-proof.md`
- Modify: `tests/python/test_repository_contract.py` only when documentation contracts require it

**Interfaces:**

- Consumes: immutable release proof, private-template proof, actual-input preflight, paid live result, and fresh empty inventory.
- Produces: sanitized public evidence, final gates, final review, focused documentation commit, pushed draft PR, and an exact stop report.

- [ ] **Step 1: Sanitize evidence before writing**

Allow only public commit/tag/asset URL, archive size/digest, redirect count, template hash, public model identities, aggregate private-input bytes, dependency counts, disk, bounded offer price, duration, approximate charge, output size/digest, result classification, and verified teardown status.

Exclude private workflow JSON, input/output filename or path, image content, API key, Jupyter token, session secret, signed redirect URL, worker base URL, raw provider response, settings payload, database content, local release-lock path, instance ID, and private template request/onstart body.

- [ ] **Step 2: Update truthful state documentation**

Record success only if output verification and fresh inventory absence both succeeded. If the run failed, record the sanitized stage, static diagnostic, charge estimate, and verified teardown separately; do not describe the feature as live-certified.

- [ ] **Step 3: Run documentation contracts and public scans**

```bash
python3 -m unittest tests.python.test_repository_contract -v
git diff --check
scripts/check.sh
scripts/check.sh
```

Expected: both complete gates pass consecutively. If documentation changed the deterministic worker SHA, investigate because documentation is not an archive member; do not accept unexplained drift.

- [ ] **Step 4: Request final code/evidence review**

Use `superpowers:requesting-code-review`. Ask for spec compliance, authorization-boundary compliance, paid-limit enforcement, teardown evidence, and private-data review. Process only demonstrated findings with `superpowers:receiving-code-review`; any code correction returns to a red regression and repeats the two full gates.

- [ ] **Step 5: Commit and push sanitized evidence**

```bash
git add \
  docs/project-state.md \
  docs/remote-worker-bootstrap-review.md \
  docs/workflow-model-metadata-proof.md \
  tests/python/test_repository_contract.py
git diff --cached --check
git commit -m "docs: record live worker acceptance evidence"
git push origin feat/vast-cloud-run-lifecycle
```

Stage only files that actually changed. Do not use `git add -A`.

- [ ] **Step 6: Verify final GitHub and local state**

```bash
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse origin/feat/vast-cloud-run-lifecycle)
test "$LOCAL_HEAD" = "$REMOTE_HEAD"
git status --short --branch
gh pr view 2 --json state,isDraft,headRefOid,url,mergeable
```

Expected: clean worktree, matching remote/PR head, PR still draft and open.

- [ ] **Step 7: Stop**

Report exact commits, gate outputs, immutable release identity, sanitized live result, and verified empty inventory. Do not merge PR `#2`, edit/delete the release or template, publish a worker lock, announce general availability, or begin broader arbitrary-workflow/plugin work.

---

## Plan self-review checklist

### Spec coverage

| Design requirement | Implemented or executed by |
|---|---|
| Deterministic immutable GitHub Release identity | Tasks 2, 4, and 7 |
| Direct `200` or one exact release-assets `302` | Task 2 |
| Signed-target non-disclosure and strict archive extraction | Task 2 |
| Fixed Caddy gateway with loopback worker | Task 3 |
| Deterministic remote lock, `onstart`, and template request | Task 4 |
| Read-only official-base audit and one private template | Task 8 |
| Owner-private local release lock | Tasks 4 and 8 |
| Machine-enforced total-create authorization | Task 5 |
| Fixed reliability/download floors at provider and local boundaries | Task 6A |
| Shared target-saturated initial/replacement ordering | Task 6A |
| Preflight-derived theoretical estimate and inert UI wording | Task 6A |
| Quote persistence and bandwidth-downgrade revalidation | Task 6A |
| Official Vast portal Caddy path with fail-closed runtime validation | Task 6B |
| Secret-safe exact template API transport and single-create reconciliation | Task 6C |
| Exact image digest bound to reviewed build provenance before template creation | Tasks 6B and 8 |
| Two full offline gates and code review before publication | Tasks 6, 6A, 6B, and 6C |
| Actual workflow and actual private input | Task 9 |
| Exact native model metadata with no filename guessing | Task 9 |
| Separately bounded paid authorization | Task 10 |
| Verified output and immediate destruction | Task 10 |
| Fresh empty inventory and sanitized evidence | Tasks 10 and 11 |

### Type and naming consistency

- `ReleaseMetadata.worker_archive_size_bytes` and `.worker_archive_sha256` feed the identically named remote-lock keys.
- `DownloadStream.source_url` is always the reviewed GitHub URL; `redirect_count` is integer `0` or `1`; no final/signed URL field exists.
- `WorkerRelease` keeps the existing local lock fields; the archive URL and byte size stay in private publication inputs, not the runtime public payload.
- `OfferQuote.max_instance_creates` is an integer `1` or `2`, survives record/public round-trip, and remains the only source of replacement authority.
- `OfferQuote.inet_down_mbps` and `.disk_bw_mbps` are provider-advertised finite nonnegative metrics; `estimated_transfer_seconds` is derived from aggregate preflight bytes and is never persisted as measured startup time.
- `MIN_VAST_RELIABILITY`, `MIN_VAST_INET_DOWN_MBPS`, and `PREFERRED_VAST_INET_DOWN_MBPS` are exactly `0.99`, `500`, and `1000`; search, normalization, ordering, quote review, confirmation, and replacement use those same values.
- `CADDY_CANDIDATES` contains only `/opt/portal-aio/caddy_manager/caddy`; the documented `/opt/instance-tools/bin/caddy` symlink and generic system paths remain rejected.
- `publish_worker_template.py` fixes one HTTPS template endpoint, accepts no API key on argv, performs at most one POST, and emits only sanitized ID/hash/verification evidence.
- `retry_count` remains `0` or `1`; consumed creates are `1 + retry_count` after initial confirmation.
- External port stays `8765`; the Python worker stays loopback on `8766`.

### Placeholder and privacy scan

Before executing this plan, run:

```bash
PLAN=docs/superpowers/plans/2026-07-31-immutable-worker-release-and-live-workflow-acceptance.md
python3 - "$PLAN" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
for forbidden in ("T" + "BD", "T" + "ODO", "implement " + "later", "fill in " + "details"):
    if forbidden.casefold() in text.casefold():
        raise SystemExit("plan placeholder detected")
print("plan placeholder scan passed")
PY
```

Expected: `plan placeholder scan passed`. Then run `scripts/check.sh` so the repository-wide public-artifact scan covers this plan as committed text.

## Fresh-session overnight handoff after human validation

Start a new session in the exact worktree and paste the following instruction
only after the human has reviewed and accepted this amended design and plan.
The message explicitly grants each listed non-paid boundary; it grants no paid
GPU action:

```text
Worktree: /Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes
Branch: feat/vast-cloud-run-lifecycle
Planning baseline HEAD: cd041cae3dbe87fd67751f20586c6714708cf7ca

Je valide la politique générale de qualité Vast du 2026-08-01 et les deux
modifications de documentation actuellement non commitées : fiabilité minimale
0,99, débit descendant annoncé minimal 500 Mbps, cible 1 000 Mbps avec score
saturé à cette cible, deux requêtes read-only bornées pour éviter le biais de
la limite fournisseur, aucun relâchement automatique, métriques/estimation
théorique visibles et revalidation des deux chemins avant création. Je valide
aussi la correction ciblée du chemin Caddy officiel Vast vers
/opt/portal-aio/caddy_manager/caddy et l'ajout du transport template Vast
spécialisé, testé, sans redirection/proxy ni clé sur argv.

Read AGENTS.md and these files completely:
- docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md
- docs/superpowers/plans/2026-07-31-immutable-worker-release-and-live-workflow-acceptance.md
- docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
- docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
- docs/remote-worker-bootstrap-review.md

Utilise le mécanisme Goal dès le début. Appelle d'abord get_goal. Si le goal
existant avec l'objectif de préparer ComfyUI-Cloud-Run pour le workflow réel
est marqué blocked, ce message constitue sa reprise explicite : poursuis-le
sans créer de doublon. S'il n'existe aucun goal, crée-en un avec cet objectif.
Travaille jusqu'à avoir terminé les tâches autorisées ci-dessous ou jusqu'à un
blocage réel vérifié; respecte les règles du Goal pour complete/blocked.

Utilise superpowers:executing-plans pour exécuter le plan, strictement
superpowers:test-driven-development pour Tasks 6A, 6B et 6C,
superpowers:verification-before-completion avant tout commit ou affirmation,
superpowers:requesting-code-review aux gates de review et
superpowers:receiving-code-review pour chaque finding. Utilise
certifying-comfyui-cloud-workflows pour le canvas réel, ses métadonnées natives,
le préflight gratuit et les preuves sanitisées.

Autorisations explicites pour cette session :

1. J'autorise Tasks 6A, 6B et 6C : modifier uniquement les fichiers listés par ces
   tâches, lire les trois fichiers publics du dépôt officiel vast-ai/base-image
   au commit exact indiqué, les trois pages officielles de l'API template Vast,
   ainsi que le seul manifeste OCI et le petit blob de configuration publics,
   adressés par digest, de l'image officielle exacte auditée. J'autorise
   l'exécution des tests
   offline/fake-provider, la mise à jour de la documentation, les commits
   ciblés et le push de la branche
   feat/vast-cloud-run-lifecycle afin de mettre à jour la PR draft #2. Cela
   autorise explicitement l'inclusion des deux docs intentionnellement modifiés
   au départ dans le commit ciblé de Task 6A. Préserve-les. Ne touche pas aux
   autres changements éventuels, ne télécharge aucune couche d'image OCI et
   n'exécute aucune image.
2. Après Tasks 6A, 6B et 6C, deux scripts/check.sh consécutifs sur le dernier code,
   une review sans défaut démontré restant, un worktree propre et l'égalité
   HEAD local/distant/PR, j'autorise conditionnellement Task 7 pour cet unique
   HEAD final revu : activer les immutable releases du dépôt GitHub si
   nécessaire, créer exactement une release ciblant ce HEAD, uploader
   exactement le petit worker archive déterministe, le retélécharger pour
   vérification, publier la release immuable et vérifier
   tag/asset/taille/SHA-256/bootstrap. Cette autorisation ne couvre aucun autre
   commit. Ne remplace, n'édite et ne supprime aucune release existante; en cas
   de collision, de nouveau commit après review ou d'ambiguïté, arrête cette
   branche d'action.
3. Si Task 7 réussit, j'autorise Task 8 : lire/auditer le template officiel
   Vast exact 027fba7753c024be019030fb42aed900, créer exactement un template
   privé spécifique au projet uniquement via scripts/publish_worker_template.py
   avec le payload déterministe revu, écrire son unique worker-release.json
   local privé en mode 0600, redémarrer proprement le ComfyUI Desktop épinglé
   et vérifier gratuitement que le lock exact est chargé. Cette création n'est
   autorisée que si le manifeste/config vérifié par digest contient exactement
   les labels source et revision exigés par Task 8; sinon arrête-toi avant le
   POST et rapporte ce seul blocage. Ne modifie, ne supprime et ne duplique
   aucun template existant.
Interdictions absolues pour cette session : ne lance aucune recherche d'offre
Vast, ne crée/loue aucune instance, ne confirme aucun devis, n'effectue aucun
téléchargement/transfert de modèle, n'exécute le workflow ni localement ni à
distance, ne crée aucun volume, et n'engage aucune dépense. N'exécute pas Tasks
9, 10 ou 11 : Task 9 exige demain le vrai navigateur/canvas et un geste humain
que tu ne dois ni simuler ni contourner. Ne merge pas la PR #2, ne ferme pas
d'issue/PR, ne publie pas au
Comfy Registry, ne mets pas à jour ComfyUI ou ses dépendances et ne généralise
pas au-delà de Tasks 6A, 6B et 6C. N'utilise aucun client HTTP/CLI improvisé
pour le template et n'effectue aucune autre mutation fournisseur.

Si une étape est bloquée mais qu'une autre autorisée et indépendante peut
avancer sans contourner le gate, poursuis cette autre étape. Ne devine jamais
une identité de modèle, un template, une release ou un état fournisseur. À la
fin, laisse un worktree propre quand c'est possible et fournis un rapport du
matin avec : commits et SHA local/distant/PR, résultats exacts des gates,
identité publique de la release, hash public du template privé sans payload,
état du lock/restart, preuve que l'UI est prête, l'unique checkpoint humain
restant pour ouvrir/confirmer le vrai workflow et lancer capture/préflight,
puis ce qui restera avant le test payant. Ne fournis le prompt GO payant
qu'après un futur préflight réel réussi; il devra exiger maximum de créations,
prix horaire maximal, durée/coût maximal et acceptation explicite des frais de
bande passante/stockage.
```

The handoff intentionally combines explicit non-paid permissions so the
overnight session can progress through Tasks 6A--8. It stops at the unavoidable
live-browser gate before Task 9, before offer search, and before every paid
action. A new morning workflow/preflight permission and a later separately
bounded paid GO remain mandatory.

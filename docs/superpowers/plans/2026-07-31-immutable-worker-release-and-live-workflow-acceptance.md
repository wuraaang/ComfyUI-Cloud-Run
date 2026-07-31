# Immutable Worker Release and Live Workflow Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish and consume one deterministic immutable Remote Worker release, enforce the human-authorized total Vast instance-create limit, then run one separately authorized live workflow acceptance with verified teardown.

**Architecture:** Keep the reviewed worker-only archive and strict extractor. Serve that archive as an immutable GitHub Release asset, permit only GitHub's single release-asset redirect, supervise the loopback worker behind Caddy, and generate the private Vast template from deterministic repository tooling. Persist `max_instance_creates` in the paid quote so confirmation, recovery, and boot replacement cannot exceed the human boundary.

**Tech Stack:** Python 3 standard library and `unittest`, aiohttp application code already in the repository, JavaScript ES modules and Node test runner, SQLite repositories, GitHub CLI/API, and Vast.ai HTTP API.

## Global Constraints

- Begin from branch `feat/vast-cloud-run-lifecycle` in worktree `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`.
- Require commit `1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0` to be an ancestor of `HEAD`; do not redo Tasks 1 through 10 or the three review corrections already contained in that commit.
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

Read all required contracts:

```bash
sed -n '1,260p' AGENTS.md
sed -n '261,620p' AGENTS.md
sed -n '1,260p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '261,620p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '621,980p' docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
sed -n '1,260p' docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
sed -n '261,620p' docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
sed -n '1,460p' docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md
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
- `scripts/write_worker_release_lock.py`: atomically write the final owner-private `0600` local lock after a verified template creation.
- `tests/python/test_worker_release_tools.py`: deterministic release naming, template rendering, secret exclusion, round-trip, file-permission, and fail-closed input tests.

### Existing files changed together

- `remote_worker/bootstrap.py` and `tests/python/test_worker_bootstrap.py`: immutable release URL validation and the single-host redirect transport.
- `remote_worker/Caddyfile`, `scripts/build_worker_artifact.py`, `tests/python/test_worker_bootstrap.py`, and `tests/python/test_repository_contract.py`: disable unused Caddy services and explicitly review/package the gateway file.
- `cloud_run/models.py`, `cloud_run/service.py`, `cloud_run/lifecycle.py`, `cloud_run/routes.py`, `web/js/cloud-run.js`, and `web/js/session-console.js`: persist, revalidate, enforce, submit, and render `max_instance_creates`.
- `tests/python/test_models.py`, `tests/python/test_service.py`, `tests/python/test_lifecycle.py`, `tests/python/test_routes.py`, `tests/python/test_repository.py`, `tests/python/test_fake_session_integration.py`, `tests/js/cloud-run-ui.test.mjs`, and `tests/js/session-console.test.mjs`: regression coverage for the total-create boundary.
- `scripts/check.sh`: include the new reviewed worker and release-tool files in repository gates.
- `README.md`, `docs/project-state.md`, and `docs/remote-worker-bootstrap-review.md`: record the implemented offline contract, then separately record sanitized publication/live evidence only after it exists.

### Private temporary outputs that must never enter Git

- deterministic worker `.tar.gz`;
- `release-metadata.json`;
- `remote-release-lock.json`;
- `template-request.json`;
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

State in commentary that the implementation authorization covers local code/tests/docs and branch pushes only. Explicitly state that Tasks 7 through 11 each retain their documented new authorization gate.

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

- Consumes: `JUPYTER_TOKEN`, fixed Caddy candidates, installed repository root, and `/var/lib/comfyui-cloud-run`.
- Produces: `select_caddy_binary(*, lstat_fn, access_fn) -> Path`, `validated_gateway_token(environ: Mapping[str, str]) -> str`, and `run_gateway(*, environ, popen_factory, wait_timeout_seconds) -> int`.

- [ ] **Step 1: Write failing gateway tests**

Test these exact requirements with fake `lstat`/`access` results for the two hard-coded absolute candidates and fake process objects:

- a token outside `[A-Za-z0-9._~+/=-]{1,4096}` in UTF-8 bytes is rejected with `GatewayError("Remote Worker gateway configuration is unavailable.")`;
- exactly one regular non-symlink executable at `/usr/bin/caddy` or `/usr/local/bin/caddy` is required;
- zero or two matching candidates fail closed;
- Caddy argv is `[caddy, "run", "--config", Caddyfile, "--adapter", "caddyfile"]`;
- worker argv is `[sys.executable, "-m", "remote_worker.main", "--state-directory", "/var/lib/comfyui-cloud-run"]`;
- Caddy receives only `JUPYTER_TOKEN` plus fixed `HOME`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`; the worker environment contains only the explicit runtime allowlist and never `JUPYTER_TOKEN`;
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
CADDY_CANDIDATES = (Path("/usr/bin/caddy"), Path("/usr/local/bin/caddy"))
STATE_DIRECTORY = Path("/var/lib/comfyui-cloud-run")
MAX_TOKEN_BYTES = 4096
SHUTDOWN_TIMEOUT_SECONDS = 10
```

Validate the two hard-coded candidate files with `os.lstat`: regular file, not symlink, owned by root or the current user, and executable according to `os.access`. Resolve neither caller paths nor `PATH`. Resolve the Caddyfile only as `Path(__file__).with_name("Caddyfile")`, require it to be a regular non-symlink file, and pass that exact path. Create fixed private Caddy config/data directories beneath `/var/lib/comfyui-cloud-run`, then build a Caddy environment containing only `JUPYTER_TOKEN`, `HOME=/var/lib/comfyui-cloud-run`, and those two fixed XDG paths. Build the worker environment from an explicit allowlist containing `CLOUD_RUN_SESSION_ID`, `CLOUD_RUN_COMFY_ROOT`, `CLOUD_RUN_WORKER_VERSION`, `HOME`, `LANG`, `LC_ALL`, `PATH`, `PYTHONPATH`, `PYTHONUNBUFFERED`, and `TMPDIR`; always remove `JUPYTER_TOKEN` and every other variable.

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
    "runtype": "jupyter_direc ssh_direc",
    "use_ssh": True,
    "ssh_direct": True,
    "jupyter_dir": "/workspace",
}
```

Assert the renderer rejects missing, additional, mutable, control-character, shell-metacharacter, non-digest image, or wrong base-hash fields. Assert the resulting request contains only:

```text
name, image, tag, runtype, use_ssh, ssh_direct, jupyter_dir,
onstart, ports, env, recommended_disk_space
```

Require `ports == ["8765/tcp"]`, `env == ""`, and no API key, token, session identity, workflow field, model URL, signed URL, or arbitrary command. Decode the two base64 constants from `onstart` and assert byte equality with `remote_worker/bootstrap.py` and the compact remote lock.

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

Set request `name` to `cloud-run-worker-` plus the full 40-hex commit and `recommended_disk_space` to the fixed minimum `80`. Copy the validated immutable `image`, pinned `tag`, `runtype`, SSH booleans, and Jupyter directory from the exact audited base record; do not copy any additional base-template field.

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

- [ ] **Step 9: Stop at the first external-mutation boundary**

Report the offline evidence and request a separate GitHub release authorization. Do not continue automatically to Task 7.

---

### Task 7: Publish and verify the immutable GitHub worker release

**Authorization gate:** Obtain a new explicit message authorizing both repository-level immutable-release enablement and publication of one exact worker release for the reviewed `HEAD`. The message must also authorize uploading and downloading the small worker archive for verification. It does not authorize Vast mutation or paid use.

**Files:**

- Create outside Git only: deterministic archive, sanitized metadata, downloaded verification copy
- Modify on GitHub only after authorization: immutable-release setting, one draft release, one asset, final published release/tag
- Modify in worktree: none

**Interfaces:**

- Consumes: clean pushed reviewed `HEAD`, two consecutive offline gates, `build_worker_release_bundle()`, and explicit release authorization.
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

Report only tag, commit, asset filename, byte size, SHA-256, public release URL, redirect count, bootstrap proof result, and immutable verification result. Request separate Vast template authorization. Do not proceed automatically.

---

### Task 8: Create one private Vast template and its matching local lock

**Authorization gate:** Obtain a new explicit message authorizing creation of one private project-specific Vast template, one matching owner-private local release lock, and the local ComfyUI restart needed to load it. This permission does not authorize offer search, instance creation, or workflow execution.

**Files:**

- Create outside Git only: sanitized base-template audit record, remote lock, `onstart`, private template request, raw private API response, local `worker-release.json`
- Modify on Vast only after authorization: one private template
- Modify in worktree: none

**Interfaces:**

- Consumes: the verified immutable release metadata, official base hash `027fba7753c024be019030fb42aed900`, deterministic renderer, and explicit template authorization.
- Produces: exactly one verified private template hash and one local `0600` lock accepted after ComfyUI restart.

- [ ] **Step 1: Audit the official base template read-only**

Use the authenticated Vast template-list endpoint without offer search. Require exactly one record whose content-derived `hash_id` is `027fba7753c024be019030fb42aed900`. Record only the sanitized fields required by `render_worker_template.py`.

Verify from immutable image metadata or reviewed base-template launch content that exactly one Caddy binary will exist at `/usr/bin/caddy` or `/usr/local/bin/caddy`. If the record is absent, duplicated, mutable, secret-bearing, unexpectedly shaped, or cannot prove the Caddy path, stop before creating anything.

- [ ] **Step 2: Render and inspect the private request offline**

Run `scripts/render_worker_template.py` in a fresh owner-private temporary directory with the verified release metadata and sanitized base audit. Run its round-trip verifier. Inspect keys and static strings without printing base64 bodies.

Expected:

- exact base hash and immutable image digest;
- port `8765/tcp` only;
- empty static environment;
- no API key, Jupyter token, session identity, workflow, model, signed URL, caller shell, or path outside the fixed bootstrap destination;
- decoded bootstrap hash equals the repository file;
- decoded remote lock equals the verified release metadata.

- [ ] **Step 3: Reconfirm no existing project template**

Perform one read-only template lookup by the exact generated name and release tag. Require zero matches. If any match exists, stop and reconcile it; never create a duplicate or overwrite a template.

- [ ] **Step 4: Create exactly one private template**

POST the exact private `template-request.json` to `/api/v0/template/` with the existing owner-private Vast credential transport. Keep the response in a `0600` temporary file. Never print request, response, authorization header, or key.

Expected: one success response containing one 32-lowercase-hex template `hash_id`. Any ambiguous timeout or response stops further mutation and triggers a read-only lookup by exact generated name; adopt only one exact content match.

- [ ] **Step 5: Read back and compare the template**

Fetch the created template read-only. Normalize only documented transport fields and require every security-relevant field to equal the rendered request: immutable image, tag, runtype, SSH booleans, Jupyter directory, fixed `onstart`, one port, empty environment, and recommended disk. Require it to be private.

If comparison fails, report the template identifier and stop. Do not automatically delete or edit it.

- [ ] **Step 6: Write the local release lock**

Use `scripts/write_worker_release_lock.py` with the verified template hash and release metadata. Target the owner-private Cloud Run data directory discovered through the existing runtime configuration. Require parent mode `0700`, new file mode `0600`, current-user ownership, no symlink, and successful `load_worker_release()` round-trip.

- [ ] **Step 7: Restart locally and prove the free lock gate**

Restart the local ComfyUI/extension process without opening the private workflow. Verify the service loads the exact template hash, worker commit, archive digest, protocol, versions, and port. Call only free status/preflight-capability endpoints; do not search offers.

- [ ] **Step 8: Remove temporary template material and stop**

Delete the validated private temporary root containing audit/request/response/onstart/remote-lock files. Preserve only the owner-private local release lock and private Vast template. Report sanitized hashes and the lock-loaded result. Request the actual workflow/input free-certification step; no paid authorization is implied.

---

### Task 9: Re-certify the actual workflow and actual private input for free

**Authorization gate:** Obtain a new explicit message authorizing read-only inspection/capture of the actual private workflow and input plus in-memory native metadata annotations. This permission does not authorize local execution, export, model download, offer search, or paid use.

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

Click Search Vast GPUs once. Select only an on-demand, one-GPU offer within hourly, VRAM, disk, reliability, worker-template, and authorized bounds. Generate the paid preview with the authorized total-create value and finite deadline.

Review exact offer ID, GPU, hourly price, bandwidth prices, disk, transfer bytes, output allowance, approximate active charge, duration, template hash, worker commit, archive digest, protocol, manifest digest, and maximum total instance creates.

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
| Two full offline gates and code review before publication | Task 6 |
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

## Fresh-session handoff

Start a new session in the exact worktree and paste this instruction only when intentionally granting offline implementation permission:

```text
Worktree: /Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes
Branch: feat/vast-cloud-run-lifecycle

Read AGENTS.md and these files completely:
- docs/superpowers/specs/2026-07-31-immutable-worker-release-and-live-workflow-acceptance-design.md
- docs/superpowers/plans/2026-07-31-immutable-worker-release-and-live-workflow-acceptance.md
- docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md
- docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md
- docs/remote-worker-bootstrap-review.md

Use superpowers:executing-plans, superpowers:receiving-code-review,
superpowers:test-driven-development for every correction,
superpowers:verification-before-completion, and
superpowers:requesting-code-review. Use certifying-comfyui-cloud-workflows
for the actual workflow metadata/preflight and sanitized certification.

I authorize offline implementation, tests, documentation, commits, and pushes
for Tasks 1 through 6 of the immutable-worker-release plan. Do not execute
Tasks 7 through 11 without their new explicit authorization gates. In
particular: no GitHub release mutation, no Vast template mutation, no offer
search, no instance creation, no paid confirmation, no model download, and no
workflow execution. Stop after Task 6 and report the exact evidence.
```

After Task 6, give only the next boundary's explicit permission. Never bundle release, template, and paid permissions into an implied continuation.

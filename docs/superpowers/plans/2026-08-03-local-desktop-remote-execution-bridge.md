# Local Desktop / Remote Execution Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one explicitly rented Vast pod behave as the GPU backend of an independent `ComfyUI Vast` environment inside ComfyUI Desktop, while the normal local environment, profile source, lifecycle control, Agent Panel orchestrator, verified outputs, and durable recovery remain on the Mac.

**Architecture:** The local `Cloud Vast` control captures and resolves the current canvas for a free preflight, persists one paid intent, provisions an immutable pod, and activates a stable loopback-only Desktop relay only after readiness. The official Desktop Remote Connection points at that relay; native ComfyUI HTTP and WebSocket traffic is authenticated server-side and proxied through a worker data plane to pod ComfyUI, while an atomic worker snapshot and a single-flight local reconciler independently preserve jobs, events, errors, and outputs. A content-addressed safe profile mirrors workflows, approved appearance assets, UI-only packages, and Agent Panel configuration without copying databases, credentials, caches, or arbitrary local paths.

**Tech Stack:** Python 3.13.12 stdlib plus ComfyUI-provided `aiohttp`, SQLite, HMAC-SHA256, browser-native JavaScript ES modules, Node's built-in test runner, Python `unittest`, immutable worker archives, and the existing Vast/Hugging Face/Civitai/R2 adapters.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`; the approved design baseline is commit `cfc46b2`.
- On 2026-08-03 the user explicitly authorized autonomous execution of this self-reviewed, committed plan for repository-local offline work. That authorization does not include any external installation, publication, Desktop process mutation, or Vast mutation.
- Preserve `origin = https://github.com/wuraaang/ComfyUI-Cloud-Run.git`; never push to `comfy-relay-do-not-push = https://github.com/wuraaang/comfy-relay.git`.
- Do not modify, import, depend on, inspect for reconstruction, or copy code from `/Users/wuraaang/comfyui-vast-cockpit`.
- The product remains one `ComfyUI-Cloud-Run` custom-node package: backend/web extension only, no graph node, no separately packaged application, no hosted frontend, and no SaaS control plane.
- `Cloud Vast` is the local lifecycle control. `ComfyUI Vast` is the official independent Desktop Remote Connection. Local and remote native Run controls remain unchanged; there is no `Run Vast` button.
- The local environment stays independently usable and local Run stays local. Every `/prompt` from the signed `ComfyUI Vast` role must reach the pod and must have no code path to local `/prompt` or local GPU execution.
- V1 binds exactly one managed Desktop environment to at most one active paid session and executes one pod GPU prompt at a time; native batches become ordered durable jobs.
- Pin ComfyUI Core `0.29.0`, frontend `1.47.10`, and Python `3.13.12`; bump the worker/manifest protocol only through reviewed fixtures and immutable release metadata.
- Keep every controller route under `/cloud-run/api/`. The separate loopback origin exposes only the session-scoped native ComfyUI data plane plus the safe Desktop-context and Agent Panel handshakes.
- Bind the Desktop relay and Agent bridge only to `127.0.0.1`. Persist one stable relay port; if it is occupied, fail closed instead of changing the saved Desktop URL or binding a non-loopback address.
- Never edit ComfyUI Desktop's private installation registry. The one-time setup is an official Remote Connection named `ComfyUI Vast` pointed at the displayed stable loopback URL.
- Use the official Desktop lifecycle only. Never start a second local ComfyUI backend against the same `comfyui.db`.
- The Mac is the source of truth for the safe profile. Copy only saved workflow JSON, the unsaved bootstrap workflow, allowlisted settings/palettes, approved UI-only releases, the background image, and referenced safe UI assets. Never copy a database, credential, cookie, token, browser store wholesale, log, cache, virtual environment, bytecode, unrelated file, or absolute Mac path.
- Agent Panel remains on the Mac and crosses only an expiring, cookie-authenticated, method-allowlisted loopback bridge. If the approved source profile contains Agent Panel, readiness requires graph read, graph edit, native run, and multi-prompt batch probes.
- Resolve executable custom nodes, UI-only packages, models, inputs, sizes, SHA-256 digests, immutable revisions, and normalized ComfyUI destinations before rental. `/object_info`, model APIs, and release locks must prove readiness.
- Hugging Face and Civitai immutable sources plus digest-verified local upload are the universal baseline. Per-user R2 is optional, content-addressed, and read-only during automatic preflight; R2 cache population requires a separately saved explicit opt-in.
- A user price value is a hard cap. A VRAM value is only a preference above the workflow hard minimum. Show a ranked shortlist and every exclusion reason; never silently substitute an offer.
- The paid action is exactly `Louer et préparer`. Persist intent, recapture dependencies, refresh inventory, and revalidate the exact quote before create. A changed or expired quote records `quote_expired` and requires a new confirmation.
- A source-ready accepted estimate targets readiness within 600 seconds. Show cached bytes, remaining bytes, bandwidth assumption, and cold/pre-positioned/warm label. The measured 29,347,469,703-byte transfer at 25–30 MB/s must be represented as roughly 16–20 minutes, never promised under ten minutes.
- No real Vast create, destroy, template mutation, worker publication, deployment, external install, or paid field test is authorized by this plan. Such an action always requires a fresh human `GO` naming the exact mutation and cost boundary. Offline tests use fake providers only.
- Never use Vast Stop or a Vast volume. Billing termination is an explicit destroy followed by fresh complete inventory proving instance ID and managed label absent and `billing_may_continue=false`.
- The pod never receives the account-level Vast key. Renderers never receive remote bearer tokens, signed artifact URLs, provider credentials, or long-lived local credentials.
- Sanitize before persistence or display: authorization headers, bearer values, API keys, cookies, tokens, signed URLs, secret query values, sensitive environment values, and unrelated absolute local paths. Use bounded payloads and `textContent`, never HTML interpolation.
- Remote execution state and local harvest state are distinct. A real `execution_success` remains successful through synchronization, temporary-preview, or output-download failures; harvesting can retry without another prompt.
- Convex and TanStack are excluded from V1. Keep a narrow local orchestration interface for a future metadata-only adapter; never send workflows, models, previews, inputs, outputs, or a Vast key through Convex.
- Follow strict RED → observe the intended failure → GREEN → focused regression suite → small commit for every task. Never change production code before the task's failing test is observed.
- `scripts/check.sh` is the deterministic final gate. It must use no real secret, network, provider mutation, publication, Desktop process control, or external filesystem installation.
- Preserve all unrelated user changes. Before each commit run `git status --short`, stage only the task files, and inspect `git diff --cached --check` plus `git diff --cached`.

## File Structure

### Focused local-controller modules

- `cloud_run/run_errors.py`: stable phases/codes, bounded sanitizer, safe error values, and journal evidence validation shared with the worker artifact.
- `cloud_run/orchestrator.py`: narrow metadata-only orchestration protocol and SQLite-backed local implementation; no Convex dependency.
- `cloud_run/desktop_profile.py`: safe profile discovery, content-addressed revisions, background rewriting, atomic sync, and conflict preservation.
- `cloud_run/desktop_relay.py`: stable loopback HTTP/WebSocket server, HttpOnly session capability, strict route/origin policy, native prompt intent binding, and server-side worker authentication.
- `cloud_run/agent_bridge.py`: session-scoped WebSocket proxy to the fixed Mac Agent Panel orchestrator with frame and command allowlists.
- `cloud_run/reconciler.py`: backend-owned single-flight snapshot polling, restart/refresh recovery, harvest retry, and observed task failures.
- `cloud_run/readiness.py`: immutable all-or-nothing Desktop readiness checks and durable identity-bound reports.
- Existing `cloud_run/manifest.py`, `models.py`, `repository.py`, `job_repository.py`, `worker_client.py`, `relay.py`, `session_service.py`, `routes.py`, `offers.py`, `service.py`, and `settings.py` retain their current responsibilities and consume the focused modules above.

### Focused worker modules

- `remote_worker/native_jobs.py`: native prompt identity, passive event recording, terminal execution state, history harvest, and atomic job snapshots shared by headless and Desktop paths.
- `remote_worker/native_proxy.py`: allowlisted native ComfyUI HTTP/WebSocket proxy that forwards frames byte-for-byte while recording sanitized copies.
- `remote_worker/profile.py`: safe profile apply/snapshot, approved UI package verification, remote revision tracking, and conflict metadata.
- `remote_worker/diagnostics.py`: bounded redacted stdout/stderr and startup probe evidence.
- Existing `remote_worker/jobs.py`, `state.py`, `server.py`, `main.py`, `comfy.py`, `bootstrap.py`, `provision.py`, and `Caddyfile` are changed only at their existing boundaries.

### Focused web modules

- `web/js/comfyui-vast.js`: signed-role discovery, current-canvas bootstrap, one-request/one-idempotency header injection, and remote-role lifecycle suppression.
- `web/js/cloud-run.js` and `web/js/session-console.js`: local `Cloud Vast` copy, paid preparation/readiness/setup UI, cost and destruction status, and no controller Run action.
- `web/js/cloud-run-api.js`: same-origin local controller methods for Desktop setup, relay activation, profile state, journal, and reconciliation.

### New deterministic tests

- `tests/python/test_run_errors.py`
- `tests/python/test_orchestrator.py`
- `tests/python/test_reconciler.py`
- `tests/python/test_desktop_profile.py`
- `tests/python/test_desktop_relay.py`
- `tests/python/test_agent_bridge.py`
- `tests/python/test_worker_native_jobs.py`
- `tests/python/test_worker_native_proxy.py`
- `tests/python/test_worker_profile.py`
- `tests/python/test_worker_diagnostics.py`
- `tests/python/test_fake_desktop_bridge_integration.py`
- `tests/js/comfyui-vast.test.mjs`

---

### Task 0: Adopt the approved Desktop bridge contract

**Files:**
- Modify: `AGENTS.md`
- Modify: `tests/python/test_repository_contract.py`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-08-03-local-desktop-remote-execution-bridge-design.md`.
- Produces: the repository-local authority for all subsequent runtime changes.

- [ ] **Step 1: Replace the obsolete slice assertion with a failing Desktop-bridge assertion**

Replace `test_agents_authorizes_only_the_workflow_derived_session_slice` with this exact test in `RepositoryContractTests`:

```python
def test_agents_authorizes_only_the_local_desktop_remote_bridge_slice(self):
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    self.assertIn("## Current slice: local Desktop / remote GPU bridge", text)
    self.assertIn("official Remote Connection named `ComfyUI Vast`", text)
    self.assertIn("There is no `Run Vast` button", text)
    self.assertIn("Agent Panel orchestrator remains on the Mac", text)
    self.assertIn("atomic worker snapshots", text)
    self.assertIn("No real Vast mutation without a fresh human GO", text)
    self.assertNotIn("capture the exact prompt compiled by the pinned frontend without posting it", text)
```

- [ ] **Step 2: Run it and observe RED**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract.RepositoryContractTests.test_agents_authorizes_only_the_local_desktop_remote_bridge_slice -v
```

Expected: `FAIL` because `AGENTS.md` still names `workflow-derived Vast GPU sessions` and describes the superseded single-local-canvas execution path.

- [ ] **Step 3: Replace only the current-slice section**

Keep Product, Paid-action gate, Engineering contract, and Pinned development host. Replace the old current-slice section with the following contract:

```markdown
## Current slice: local Desktop / remote GPU bridge

Use ComfyUI Desktop's official independent environments to make one temporary
Vast pod the GPU backend of an official Remote Connection named `ComfyUI Vast`:

- the normal local Desktop environment remains independently usable and owns
  the `Cloud Vast` lifecycle control;
- `ComfyUI Vast` uses the pod's native node definitions, model lists, Run,
  queue, batch, progress, previews, history, errors, and outputs;
- There is no `Run Vast` button and a remote prompt has no route to local
  `/prompt` or local GPU execution;
- a safe versioned profile mirrors the canvas, workflows, appearance, approved
  UI extensions, and background without copying databases or credentials;
- the Agent Panel orchestrator remains on the Mac behind a scoped loopback
  bridge and is a mandatory readiness check when present;
- atomic worker snapshots and a backend-owned reconciler recover jobs, events,
  typed errors, and verified outputs across disconnects and restarts;
- one explicit `Louer et préparer` confirmation persists intent and revalidates
  the exact quote before any provider create.

No real Vast mutation without a fresh human GO. Automated implementation and
certification use fake providers only; worker publication, template changes,
external installation, and paid field testing remain separate approvals.
```

- [ ] **Step 4: Run the focused contract suite**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
```

Expected: all repository-contract tests pass and no forbidden-tree expectation changes.

- [ ] **Step 5: Commit**

```sh
git add AGENTS.md tests/python/test_repository_contract.py
git diff --cached --check
git commit -m "docs: adopt local Desktop remote GPU bridge slice"
```

---

### Task 1: Restore a deterministic green baseline

**Files:**
- Modify: `cloud_run/session_service.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Modify: `tests/python/test_worker_server.py`
- Modify: `scripts/check.sh`
- Modify: `scripts/build_worker_artifact.py`
- Modify: `tests/python/test_worker_release_tools.py`

**Interfaces:**
- Consumes: exact durable provisioning records from `JobRepository.get_provision_transaction(_stored_provision_transaction_id(session_id, manifest_digest))` and authenticated worker transaction reads.
- Produces: a gate that runs all Python tests under the configured ComfyUI Python, ignores generated bytecode caches in worker packaging, preserves the fixed Caddy `route` ordering, and never lets a remote poll replace a fresh apply result.

- [ ] **Step 1: Record the existing RED baseline without modifying files**

Run:

```sh
scripts/check.sh
```

Expected: the three known failures named below plus two Pillow import errors under the system Xcode Python; no provider call occurs:

```text
test_observed_ready_progress_cannot_replace_failed_apply_result
test_invalid_polled_progress_cancels_and_consumes_active_apply
test_proxy_and_worker_bindings_are_exact_and_loopback_only
test_session_output_evidence (Pillow import)
test_smoke_output_validation (Pillow import)
```

- [ ] **Step 2: Add the worker-artifact bytecode-cache regression**

Add to `WorkerReleaseToolTests`:

```python
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
    finally:
        if previous is None:
            bytecode.unlink(missing_ok=True)
        else:
            bytecode.write_bytes(previous)
        if not cache_preexisted:
            cache.rmdir()
```

Run:

```sh
python3 -m unittest tests.python.test_worker_release_tools.WorkerReleaseToolTests.test_worker_artifact_ignores_generated_bytecode_cache -v
```

Expected: `ERROR` because generated bytecode currently makes the reviewed source tree unavailable.

- [ ] **Step 3: Make only generated bytecode invisible to the reviewed source set**

In `_source_files`, prune entries whose relative parts contain `__pycache__` or whose suffix is `.pyc`; continue to reject `.git`, `.hg`, `.svn`, `tests`, symlinks, non-allowlisted regular files, and every unexpected source member:

```python
        for path in remote_root.rglob("*"):
            relative_path = path.relative_to(root)
            if "__pycache__" in relative_path.parts or path.suffix == ".pyc":
                continue
            relative = relative_path.as_posix()
            metadata = os.lstat(path)
            if stat.S_ISDIR(metadata.st_mode):
                if path.name in {".git", ".hg", ".svn", "tests"}:
                    raise ArtifactBuildError(
                        "Worker source tree contains forbidden metadata."
                    )
                continue
            observed.add(relative)
```

- [ ] **Step 4: Restrict ready-transaction reuse to a locally known retry**

Keep the existing tests red until production changes. At the top of `_apply_manifest`, compute `transaction_id = "provision-" + manifest.digest`; query the local repository first and call remote `worker.transaction` only when the local row exists and matches the same session, job scope, and manifest:

```python
        transaction_id = "provision-" + manifest.digest
        local_transaction = self.job_repository.get_provision_transaction(
            _stored_provision_transaction_id(
                session.session_id,
                manifest.digest,
            )
        )
        transaction = getattr(worker, "transaction", None)
        if local_transaction is not None:
            if (
                local_transaction.session_id != session.session_id
                or local_transaction.manifest_digest != manifest.digest
                or local_transaction.job_id != transfer_job_id
            ):
                raise TerminalProvisioningError(
                    "Stored provisioning transaction is invalid."
                )
        if local_transaction is not None and callable(transaction):
            try:
                existing = await transaction(transaction_id)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                existing = None
            if existing is not None:
                if not _provision_payload_valid(existing, manifest):
                    raise TerminalProvisioningError(
                        "Remote provisioning response was invalid."
                    )
                if existing["state"] == "ready":
                    self._record_provision_progress(
                        existing,
                        session=session,
                        manifest=manifest,
                        transfer_job_id=transfer_job_id,
                    )
                    return existing
```

The fresh path must immediately start `apply_manifest`; a later polled `ready` record cannot replace the eventual failed/cancelled apply result. Add `test_ready_transaction_reuse_rejects_a_different_local_job_scope` and assert a mismatched local row fails before any remote transaction lookup.

- [ ] **Step 5: Update the stale Caddy fixture without weakening production**

Change the exact expected string in `test_proxy_and_worker_bindings_are_exact_and_loopback_only` to the current four-space `route` block:

```python
expected = """:8765 {
    route {
        @unauthorized not header Authorization \"Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}\"
        respond @unauthorized 401

        request_header -Authorization
        request_header -X-Cloud-Run-Boundary
        request_header X-Cloud-Run-Boundary authenticated
        reverse_proxy 127.0.0.1:8766
    }
}
"""
```

Assert authorization is checked before `request_header -Authorization`, and keep missing/invalid bearer tests.

- [ ] **Step 6: Run the full Python discovery with the shared ComfyUI interpreter**

Replace only the first Python-test invocation in `scripts/check.sh`:

```sh
echo "[check] Python tests"
PYTHONDONTWRITEBYTECODE=1 "$comfyui_python_runner" \
  -m unittest discover -s tests/python -p 'test_*.py' -v
```

Keep system `python_command` for pure static scans and compilation. `COMFYUI_PYTHON_COMMAND` remains the explicit override consumed by `scripts/run_with_comfyui_python.sh`.

- [ ] **Step 7: Verify GREEN**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.ReusableSessionTests.test_observed_ready_progress_cannot_replace_failed_apply_result \
  tests.python.test_session_service.ReusableSessionTests.test_invalid_polled_progress_cancels_and_consumes_active_apply \
  tests.python.test_worker_server.WorkerApplicationTests.test_proxy_and_worker_bindings_are_exact_and_loopback_only \
  tests.python.test_worker_release_tools.WorkerReleaseToolTests.test_worker_artifact_ignores_generated_bytecode_cache -v
scripts/check.sh
```

Expected: four focused tests pass; the repository gate ends `[check] all checks passed`, with no manual cache move.

- [ ] **Step 8: Commit**

```sh
git add cloud_run/session_service.py tests/python/test_session_service.py \
  tests/python/test_no_mutation_surface.py tests/python/test_worker_server.py \
  scripts/check.sh \
  scripts/build_worker_artifact.py tests/python/test_worker_release_tools.py
git diff --cached --check
git commit -m "test: restore deterministic bridge baseline"
```

**Offline checkpoint A:** do not continue if `scripts/check.sh` is red. Do not inspect credentials or contact Vast to diagnose a deterministic failure.

---

### Task 2: Add typed safe errors, a complete durable run journal, and the local orchestration seam

**Files:**
- Create: `cloud_run/run_errors.py`
- Create: `cloud_run/orchestrator.py`
- Modify: `cloud_run/repository.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/models.py`
- Modify: `remote_worker/bootstrap.py`
- Create: `tests/python/test_run_errors.py`
- Create: `tests/python/test_orchestrator.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_repository.py`
- Modify: `tests/python/test_worker_bootstrap.py`
- Modify: `tests/python/test_worker_release_tools.py`
- Modify: `scripts/build_worker_artifact.py`

**Interfaces:**
- Produces: `RunPhase`, `RunErrorCode`, `SafeRunError`, `RunJournalEntry`, `JournalPort`, `sanitize_text(value, *, local_roots=())`, `JobRepository.record_journal(entry)`, and `OrchestratorPort.record(entry)`.
- Consumes downstream: every lifecycle, provisioning, native proxy, synchronization, harvest, and relay task uses these exact values instead of generic strings.

- [ ] **Step 1: Write failing error and sanitizer tests**

Create `tests/python/test_run_errors.py` with exact code coverage:

```python
class RunErrorContractTests(unittest.TestCase):
    def test_codes_and_phases_are_exact(self):
        self.assertEqual(
            {item.value for item in RunErrorCode},
            {
                "validation_error", "dependency_error", "transfer_error",
                "quote_expired", "provider_error", "provisioning_error",
                "comfy_startup_error", "execution_error",
                "synchronization_error", "harvest_error", "invalid_output",
                "worker_restart_error", "lifecycle_error", "internal_error",
            },
        )
        self.assertEqual(
            {item.value for item in RunPhase},
            {
                "preflight", "quote", "provider", "bootstrap", "transfer",
                "provisioning", "readiness", "execution", "synchronization",
                "harvest", "teardown", "internal",
            },
        )

    def test_sanitizer_removes_every_secret_shape_and_bounds_output(self):
        raw = (
            "Authorization: Bearer abcdef token=xyz api_key=qwerty "
            "https://host/path?X-Amz-Signature=secret "
            "/Users/alice/private/model.safetensors\n" + "x" * 40_000
        )
        safe = sanitize_text(raw, local_roots=(Path("/Users/alice/private"),))
        self.assertLessEqual(len(safe.encode("utf-8")), MAX_SAFE_TEXT_BYTES)
        for forbidden in ("abcdef", "xyz", "qwerty", "secret", "/Users/alice"):
            self.assertNotIn(forbidden, safe)
```

Run:

```sh
python3 -m unittest tests.python.test_run_errors -v
```

Expected: `ERROR` because `cloud_run.run_errors` does not exist.

- [ ] **Step 2: Implement the pure shared error contract**

Define immutable enums/dataclasses and constants:

```python
MAX_SAFE_TEXT_BYTES = 32 * 1024
MAX_SAFE_LOG_LINES = 64
MAX_JOURNAL_DETAILS_BYTES = 64 * 1024

class RunPhase(str, Enum):
    PREFLIGHT = "preflight"
    QUOTE = "quote"
    PROVIDER = "provider"
    BOOTSTRAP = "bootstrap"
    TRANSFER = "transfer"
    PROVISIONING = "provisioning"
    READINESS = "readiness"
    EXECUTION = "execution"
    SYNCHRONIZATION = "synchronization"
    HARVEST = "harvest"
    TEARDOWN = "teardown"
    INTERNAL = "internal"

class RunErrorCode(str, Enum):
    VALIDATION = "validation_error"
    DEPENDENCY = "dependency_error"
    TRANSFER = "transfer_error"
    QUOTE_EXPIRED = "quote_expired"
    PROVIDER = "provider_error"
    PROVISIONING = "provisioning_error"
    COMFY_STARTUP = "comfy_startup_error"
    EXECUTION = "execution_error"
    SYNCHRONIZATION = "synchronization_error"
    HARVEST = "harvest_error"
    INVALID_OUTPUT = "invalid_output"
    WORKER_RESTART = "worker_restart_error"
    LIFECYCLE = "lifecycle_error"
    INTERNAL = "internal_error"
```

`SafeRunError` contains `code`, `phase`, `message`, `correlation_id`, optional `node_id`, and `retryable`; it validates all values and never stores the original exception text without `sanitize_text`.

Define the journal value and port exactly:

```python
@dataclass(frozen=True)
class RunJournalEntry:
    entry_id: str
    session_id: str | None
    manifest_digest: str | None
    transaction_id: str | None
    job_id: str | None
    phase: RunPhase
    code: RunErrorCode
    message: str
    node_id: str | None
    process_exit_code: int | None
    restart_count: int
    last_probe: str | None
    byte_cursor: int
    event_cursor: int
    output_state: str | None
    details: dict
    created_at: float

class JournalPort(Protocol):
    def record(self, entry: RunJournalEntry) -> bool: ...
```

- [ ] **Step 3: Write failing SQLite journal and execution/harvest-state tests**

Add tests that open the same version-7 fixture used by repository migration tests and assert schema version `8`, idempotent journal identity, bounded evidence, and independent job dimensions:

```python
entry = RunJournalEntry(
    entry_id="journal-1",
    session_id="session-1",
    manifest_digest="a" * 64,
    transaction_id="provision-" + "a" * 64,
    job_id="job-1",
    phase=RunPhase.SYNCHRONIZATION,
    code=RunErrorCode.SYNCHRONIZATION,
    message="Worker snapshot timed out; retry scheduled.",
    node_id=None,
    process_exit_code=None,
    restart_count=1,
    last_probe="gateway reachable",
    byte_cursor=29347469703,
    event_cursor=94,
    output_state="pending",
    details={"retry": 2, "inventory": "unchanged"},
    created_at=20.0,
)
self.assertTrue(repository.record_journal(entry))
self.assertFalse(repository.record_journal(entry))
self.assertEqual(repository.list_journal("session-1"), [entry])
```

Extend `CloudJob` with `execution_state: ExecutionState`, `harvest_state: HarvestState`, and `error_code: RunErrorCode | None`. Exact enums:

```python
class ExecutionState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

class HarvestState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
```

Assert `execution_state=SUCCEEDED` remains unchanged when `harvest_state` becomes `FAILED` with `invalid_output`.

- [ ] **Step 4: Add schema version 8 and journal methods**

Add `execution_state`, `harvest_state`, and `error_code` columns to `jobs`, migrate them deterministically from current `state`, and create:

```sql
CREATE TABLE IF NOT EXISTS run_journal (
    entry_id TEXT PRIMARY KEY,
    session_id TEXT,
    manifest_digest TEXT,
    transaction_id TEXT,
    job_id TEXT,
    phase TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    node_id TEXT,
    process_exit_code INTEGER,
    restart_count INTEGER NOT NULL,
    last_probe TEXT,
    byte_cursor INTEGER NOT NULL,
    event_cursor INTEGER NOT NULL,
    output_state TEXT,
    details_json TEXT NOT NULL,
    created_at REAL NOT NULL
)
```

Set `schema_meta.schema_version` to `8`. `record_journal` uses `INSERT OR IGNORE`; an existing `entry_id` with different canonical content raises `ValueError` instead of overwriting evidence.

- [ ] **Step 5: Add the metadata-only orchestration interface**

Create:

```python
class OrchestratorPort(Protocol):
    def record(self, entry: RunJournalEntry) -> bool: ...
    def entries(self, session_id: str, *, after: float = 0.0) -> tuple[RunJournalEntry, ...]: ...

class LocalOrchestrator:
    def __init__(self, repository: JobRepository):
        self.repository = repository

    def record(self, entry):
        return self.repository.record_journal(entry)

    def entries(self, session_id, *, after=0.0):
        return tuple(self.repository.list_journal(session_id, after=after))
```

`tests/python/test_orchestrator.py` must prove there is no import or configuration reference to Convex or TanStack and the interface accepts metadata only; passing bytes, workflow objects, or secret-bearing detail values is rejected.

- [ ] **Step 6: Include only the pure error module in the worker artifact**

Add `cloud_run/run_errors.py` to `SHARED_FILES` and the standalone bootstrap's exact reviewed archive allowlist; do not add `orchestrator.py`, SQLite repositories, or provider code. Update exact worker member tests.

- [ ] **Step 7: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_run_errors \
  tests.python.test_orchestrator \
  tests.python.test_models \
  tests.python.test_job_repository \
  tests.python.test_repository \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_release_tools -v
scripts/check.sh
```

Expected: all pass and the artifact member list contains `cloud_run/run_errors.py` exactly once.

```sh
git add cloud_run/run_errors.py cloud_run/orchestrator.py cloud_run/repository.py \
  cloud_run/job_repository.py cloud_run/models.py remote_worker/bootstrap.py \
  scripts/build_worker_artifact.py \
  tests/python/test_run_errors.py tests/python/test_orchestrator.py \
  tests/python/test_job_repository.py tests/python/test_models.py \
  tests/python/test_repository.py tests/python/test_worker_bootstrap.py \
  tests/python/test_worker_release_tools.py
git diff --cached --check
git commit -m "feat: persist typed Cloud Vast run evidence"
```

---

### Task 3: Fix final output classification and add one atomic worker snapshot

**Files:**
- Modify: `remote_worker/jobs.py`
- Modify: `remote_worker/state.py`
- Modify: `remote_worker/server.py`
- Modify: `cloud_run/worker_client.py`
- Modify: `cloud_run/relay.py`
- Modify: `tests/python/test_worker_jobs.py`
- Modify: `tests/python/test_worker_server.py`
- Modify: `tests/python/test_worker_client.py`
- Modify: `tests/python/test_relay.py`
- Modify: `tests/python/test_worker_protocol.py`

**Interfaces:**
- Produces: `JobSnapshot`, `JobManager.snapshot(job_id, after_sequence)`, `GET /worker/v1/jobs/{job_id}/snapshot?after_sequence=N`, `WorkerClient.snapshot(job_id, after_sequence)`, and `LocalRelay.sync_snapshot(job, snapshot)`.
- Snapshot exact public fields: `job_id`, `state`, `prompt_id`, `events`, `last_sequence`, `outputs`, `error`, `created_at`, `updated_at`.

- [ ] **Step 1: Write the legitimate temporary-output regression**

Add a job history containing node `9` `SaveImage` with `type="output"` and node `66` `PreviewImage` with `type="temp"`. Assert terminal state remains `succeeded`, only the persistent descriptor is hashed, and `comfy.output_path` is never called for the temp descriptor.

Run:

```sh
python3 -m unittest tests.python.test_worker_jobs.JobManagerTests.test_success_ignores_previewimage_temp_descriptor_during_harvest -v
```

Expected: `FAIL` because `_history_outputs` passes the temp descriptor to `ComfyProcess.output_path` and rewrites the real success as failure.

- [ ] **Step 2: Apply the strict three-way descriptor rule**

Before `output_path` in `_history_outputs`, add:

```python
descriptor_type = descriptor.get("type")
if descriptor_type == "temp":
    continue
if descriptor_type != "output":
    raise JobError("Remote output descriptor is invalid.")
```

Do not relax filename, subfolder, root, symlink, size, digest, or MIME validation for persistent outputs.

- [ ] **Step 3: Write the atomic race regression**

Create a state-store fake whose `job()` returns one record and mutates the backing record immediately after the read. Assert `snapshot("job-1", 93)` returns events 94 through the captured record's own `sequence` and matching terminal fields, not a later `job()` value:

```python
snapshot = manager.snapshot("job-1", 93)
self.assertEqual(snapshot.last_sequence, 158)
self.assertEqual(snapshot.events[0]["sequence"], 94)
self.assertEqual(snapshot.events[-1]["sequence"], 158)
self.assertEqual(snapshot.state, "succeeded")
```

Run the worker, server, client, and relay focused new tests. Expected: `ERROR` because `snapshot` and its route do not exist.

- [ ] **Step 4: Implement the snapshot from exactly one persisted record**

`JobManager.snapshot` calls `self.state.job(job_id)` once. `JobSnapshot.from_record(record, after_sequence)` validates a nonnegative cursor, selects events from that detached record, and uses `record["sequence"]` even when no events follow the cursor. Add `created_at` to worker job records and state validation; migrate schema-1 state in-memory by assigning `created_at = updated_at` only for old records, then save schema version `2` atomically.

The route response is exact and authenticated:

```python
return _response(200, snapshot.public_payload())
```

Keep `/job` and `/events` for protocol compatibility, but no local reconciliation code may compose them.

- [ ] **Step 5: Strictly validate the client snapshot**

`WorkerClient.snapshot` rejects extra/missing fields, identity mismatch, noncontiguous sequences, an event above `last_sequence`, invalid terminal state, malformed output/error, nonfinite timestamps, and responses over existing JSON limits. It returns a detached dictionary with no headers or secrets.

- [ ] **Step 6: Replace the relay's split read**

`LocalRelay.sync_job` becomes a compatibility wrapper that obtains one snapshot and delegates:

```python
async def sync_job(self, job):
    cursor = self.repository.last_event_sequence(job.job_id)
    snapshot = await self.worker.snapshot(job.job_id, cursor)
    return await self.sync_snapshot(job, snapshot)
```

`sync_snapshot` appends by `(job_id, sequence)`, accepts an exact replay without mutation, rejects gaps or changed events, downloads only new previews/outputs, and never compares a separately fetched job cursor.

- [ ] **Step 7: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_jobs \
  tests.python.test_worker_server \
  tests.python.test_worker_client \
  tests.python.test_relay \
  tests.python.test_worker_protocol -v
scripts/check.sh
```

Expected: the 158-event/event-94 race fixture catches up to 158; `PreviewImage` stays temporary; the gate passes.

```sh
git add remote_worker/jobs.py remote_worker/state.py remote_worker/server.py \
  cloud_run/worker_client.py cloud_run/relay.py tests/python/test_worker_jobs.py \
  tests/python/test_worker_server.py tests/python/test_worker_client.py \
  tests/python/test_relay.py tests/python/test_worker_protocol.py
git diff --cached --check
git commit -m "fix: reconcile jobs from atomic worker snapshots"
```

---

### Task 4: Replace swallowed tasks with durable single-flight reconciliation

**Files:**
- Create: `cloud_run/reconciler.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/routes.py`
- Modify: `cloud_run/relay.py`
- Create: `tests/python/test_reconciler.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/python/test_fake_session_integration.py`

**Interfaces:**
- Consumes: `WorkerClient.snapshot(job_id, cursor)`, `LocalRelay.sync_snapshot(job, snapshot)`, `OrchestratorPort.record(entry)`, `ExecutionState`, and `HarvestState`.
- Produces: `SessionReconciler.schedule(session_id)`, `await reconcile(session_id)`, `await recover()`, `await before_teardown(session_id)`, and `await close()`.

- [ ] **Step 1: Write failing single-flight and exception-observation tests**

Use a blocking fake worker and assert five `schedule("session-1")` calls create one task. Make `sync_snapshot` raise a transport error after remote `execution_success`; assert the exception is absent from the event loop's unhandled-exception handler, one `synchronization_error` journal row exists, execution remains `succeeded`, harvest remains retryable, and a later refresh catches up without another `start_job`.

Run:

```sh
python3 -m unittest tests.python.test_reconciler -v
```

Expected: `ERROR` because `cloud_run.reconciler` does not exist.

- [ ] **Step 2: Implement the backend-owned reconciler**

Use one task map and always persist unexpected failures:

```python
class SessionReconciler:
    def schedule(self, session_id):
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.get_running_loop().create_task(
            self.reconcile(session_id),
            name="cloud-vast-reconcile-" + session_id,
        )
        self._tasks[session_id] = task
        task.add_done_callback(
            lambda done, identity=session_id: self._observe(identity, done)
        )
        return task
```

`_observe` removes only the same task. Cancellation is recorded only when unexpected; every other exception becomes a sanitized correlation-backed journal entry and a bounded retry. It must never call `done.exception()` and discard the result.

- [ ] **Step 3: Separate execution and harvesting transitions**

On snapshot `succeeded`, persist `ExecutionState.SUCCEEDED` before any output work, transition `HarvestState.RUNNING`, and show `Execution succeeded — retrieving outputs.` A `harvest_error` or `invalid_output` sets `HarvestState.FAILED` while preserving execution success and prompt ID. A successful retry sets `HarvestState.SUCCEEDED` and overall `JobState.SUCCEEDED` without resubmission.

Remote `failed`/`interrupted` affects execution state and returns a healthy reusable session to `READY` when provider/worker health is intact. A synchronization transport failure changes neither remote execution dimension nor provider lifecycle state.

- [ ] **Step 4: Wire every recovery trigger**

Remove `_JOB_TASKS` and `_schedule_remote_job`. Delegate after job submission, on `SessionService.session`, on relevant job/session GET routes, during `service.recover()`, after relay reconnect, and before/after teardown. Startup hook calls `await reconciler.recover()` after lifecycle inventory recovery; cleanup calls `await reconciler.close()`.

No GET waits for the entire job: it schedules single-flight reconciliation, performs one bounded immediate pass when safe, and returns durable state.

- [ ] **Step 5: Derive local panel progress from stored native events**

Replace the three forced `None` assignments in `_job_payload`. Fold events in sequence:

```python
if event.event_type == "executing":
    current_node = event.payload.get("node_id")
elif event.event_type == "progress":
    progress = {
        "value": event.payload.get("value"),
        "max": event.payload.get("max", event.payload.get("total")),
    }
elif event.event_type == "progress_text":
    progress_text = event.payload.get("text")
elif event.event_type in {"execution_success", "execution_error", "execution_interrupted"}:
    current_node = None
```

Return `execution_status`, `harvest_status`, `error_code`, current node `{id,title}` when workflow metadata supplies a title, progress, progress text, and `last_sequence`. All strings remain inert JSON rendered with `textContent`.

- [ ] **Step 6: Verify restart recovery and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_reconciler \
  tests.python.test_session_service \
  tests.python.test_routes \
  tests.python.test_fake_session_integration -v
scripts/check.sh
```

Expected: the fixture resumes `RUNNING`/`HARVESTING` from SQLite plus one atomic worker snapshot, downloads once, and never duplicates prompt submission.

```sh
git add cloud_run/reconciler.py cloud_run/session_service.py cloud_run/service.py \
  cloud_run/routes.py cloud_run/relay.py tests/python/test_reconciler.py \
  tests/python/test_session_service.py tests/python/test_routes.py \
  tests/python/test_fake_session_integration.py
git diff --cached --check
git commit -m "feat: reconcile Cloud Vast jobs durably"
```

**Offline checkpoint B:** inspect the journal fixture for the original event-94 race and temp-output false failure. Require remote execution success to remain distinct from harvest/synchronization state.

---

### Task 5: Preserve bounded ComfyUI process diagnostics and make bootstrap reboot-idempotent

**Files:**
- Create: `remote_worker/diagnostics.py`
- Modify: `remote_worker/comfy.py`
- Modify: `remote_worker/bootstrap.py`
- Modify: `scripts/render_worker_template.py`
- Modify: `scripts/build_worker_artifact.py`
- Create: `tests/python/test_worker_diagnostics.py`
- Modify: `tests/python/test_worker_bootstrap.py`
- Modify: `tests/python/test_worker_release_tools.py`
- Modify: `tests/python/test_worker_template_api.py`

**Interfaces:**
- Produces: `BoundedDiagnostics.feed(stream, chunk)`, `BoundedDiagnostics.snapshot(...)`, `ComfyProcess.diagnostics()`, and an on-start path that validates an existing immutable installation before relaunching the gateway.
- Consumes: `sanitize_text`, `MAX_SAFE_LOG_LINES`, the reviewed release lock, `_verify_layout`, and the existing fixed `os.execv` gateway handoff.

- [ ] **Step 1: Write failing bounded-log tests**

Create a fake subprocess with readable `stdout` and `stderr`, a nonzero exit code, and lines containing bearer, signed URL, and absolute-path values. Assert the final diagnostic contains phase, exit code, restart count, last safe probe, at most 64 lines and 32 KiB, contains `[redacted]`, and contains none of the raw secrets.

Run:

```sh
python3 -m unittest tests.python.test_worker_diagnostics -v
```

Expected: `ERROR` because `remote_worker.diagnostics` does not exist and current Comfy output is sent to `DEVNULL`.

- [ ] **Step 2: Implement the bounded diagnostic buffer**

Use a `deque(maxlen=MAX_SAFE_LOG_LINES)` and bound each sanitized line before append. The public immutable snapshot fields are exact:

```python
@dataclass(frozen=True)
class ProcessDiagnostic:
    phase: str
    exit_code: int | None
    restart_count: int
    last_probe: str | None
    stdout_tail: tuple[str, ...]
    stderr_tail: tuple[str, ...]
```

Only `stdout` and `stderr` stream names are accepted. Invalid UTF-8 uses replacement characters before sanitization. No snapshot includes environment values or argv secrets.

- [ ] **Step 3: Pipe and continuously drain ComfyUI output**

Change process creation to:

```python
stdin=asyncio.subprocess.DEVNULL,
stdout=asyncio.subprocess.PIPE,
stderr=asyncio.subprocess.PIPE,
```

Start one reader task per stream immediately, drain until EOF, and observe both tasks during stop/start failure. `_wait_ready` records each bounded probe outcome. If process startup fails, `ComfyProcessError` carries only a `ProcessDiagnostic`; controller journal mapping chooses `comfy_startup_error` and never serializes a raw exception.

- [ ] **Step 4: Write the existing-install reboot regression**

Build a valid installed tree and lock at `/opt/comfyui-cloud-run-bootstrap` under a temporary root. Invoke the rendered on-start flow twice. Assert the second invocation performs no download, extraction, rename, or overwrite; it validates exact lock/layout and invokes the same gateway argv. Change one lock digest and assert fail closed without modifying the directory.

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_release_tools.WorkerReleaseToolTests.test_rendered_onstart_reuses_only_exact_verified_install -v
```

Expected: the new test fails because the rendered script uses plain `mkdir` and treats an existing directory as an error.

- [ ] **Step 5: Add the verified reuse branch**

`Bootstrap.run(payload)` gains an existing-destination path before download:

```python
if self.allowed_destination.exists():
    existing = load_release_lock(
        self.allowed_destination / ".cloud-run-release-lock.json"
    )
    if existing != lock:
        raise BootstrapError("Installed worker release does not match its lock.")
    _verify_installed_layout(self.allowed_destination)
    argv = [
        sys.executable, "-m", "remote_worker.gateway",
        "--state-directory", STATE_DIRECTORY,
    ]
    self.exec_runner(argv, cwd=self.allowed_destination)
    return self.allowed_destination
```

On first install, after `_verify_layout(extracted)` validates the archive, write canonical mode-0600 `.cloud-run-release-lock.json` from the already validated payload, fsync it, and use `_verify_installed_layout` to require exactly the reviewed archive layout plus that lock. The rendered shell uses `mkdir -p` only for the parent state directory, never overwrites the installed release, and always lets Python validate the immutable lock before gateway exec. Preserve archive download, safe extraction, and atomic first install.

- [ ] **Step 6: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_diagnostics \
  tests.python.test_worker_bootstrap \
  tests.python.test_worker_release_tools \
  tests.python.test_worker_template_api -v
scripts/check.sh
```

Expected: diagnostics are bounded/redacted, two identical boots work, a mismatched lock fails closed, and the gate passes.

```sh
git add remote_worker/diagnostics.py remote_worker/comfy.py remote_worker/bootstrap.py \
  scripts/render_worker_template.py scripts/build_worker_artifact.py \
  tests/python/test_worker_diagnostics.py tests/python/test_worker_bootstrap.py \
  tests/python/test_worker_release_tools.py tests/python/test_worker_template_api.py
git diff --cached --check
git commit -m "fix: preserve worker startup diagnostics across reboot"
```

---

### Task 6: Version the executable, UI-only, and safe-profile manifest contract

**Files:**
- Modify: `cloud_run/manifest.py`
- Modify: `cloud_run/worker_protocol.py`
- Modify: `cloud_run/resolver.py`
- Modify: `cloud_run/artifacts.py`
- Modify: `remote_worker/provision.py`
- Modify: `remote_worker/state.py`
- Modify: `tests/python/test_manifest.py`
- Modify: `tests/python/test_resolver.py`
- Modify: `tests/python/test_artifacts.py`
- Modify: `tests/python/test_worker_protocol.py`
- Modify: `tests/python/test_worker_provision.py`
- Modify: `tests/python/test_worker_release.py`
- Modify: `tests/python/test_worker_server.py`

**Interfaces:**
- Produces: manifest schema `2`, worker protocol `2`, `UiPackageSpec`, `ProfileSpec`, `ProfileFileSpec`, and a delta that distinguishes executable runtime incompatibility from compatible profile/UI/artifact changes.
- Consumes downstream: profile capture creates `ProfileSpec`; provisioner verifies both executable classes and UI asset/profile digests.

- [ ] **Step 1: Write failing manifest separation tests**

Add exact cases:

```python
agent_panel = UiPackageSpec(
    package_id="comfyui-agent-panel",
    repository_url="https://github.com/example/comfyui-agent-panel",
    revision="a" * 40,
    archive=artifact(kind="ui_package_archive", destination="custom_nodes/comfyui-agent-panel"),
    web_sha256="b" * 64,
    required_capabilities=("graph_read", "graph_edit", "native_run", "native_batch"),
)
profile = ProfileSpec(
    profile_id="profile-1",
    revision=1,
    archive=artifact(kind="profile_archive", destination="user/default/cloud-vast-profile"),
    bootstrap_digest="c" * 64,
    files=(ProfileFileSpec(path="workflows/example.json", size_bytes=12, sha256="d" * 64),),
)
```

Assert UI-only packages may declare zero graph classes, executable `CustomNodeSpec` still requires at least one class, profile paths cannot address `comfyui.db`, `.env`, caches, bytecode, absolute paths, or `..`, and `secret_handle` remains absent from canonical bytes.

Run:

```sh
python3 -m unittest tests.python.test_manifest tests.python.test_worker_protocol -v
```

Expected: `ERROR` because the new immutable types do not exist.

- [ ] **Step 2: Add exact immutable types and bump both versions**

Set:

```python
MANIFEST_SCHEMA_VERSION = 2
PROTOCOL_VERSION = "2"
```

Add:

```python
@dataclass(frozen=True)
class ProfileFileSpec:
    path: str
    size_bytes: int
    sha256: str

@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    revision: int
    archive: ArtifactSpec
    bootstrap_digest: str
    files: tuple[ProfileFileSpec, ...]

@dataclass(frozen=True)
class UiPackageSpec:
    package_id: str
    repository_url: str
    revision: str
    archive: ArtifactSpec
    web_sha256: str
    required_capabilities: tuple[str, ...]
```

`DependencyManifest` gains `ui_packages: tuple[UiPackageSpec, ...]`, `profile: ProfileSpec`, and `minimum_vram_gb: float`. Add artifact kinds `ui_package_archive` and `profile_archive`, with exact roots `custom_nodes` and `user`. The destination validator allows `user/default/cloud-vast-profile` only for profile archives; no other arbitrary user path.

- [ ] **Step 3: Define delta compatibility explicitly**

`ManifestDelta` gains `ui_packages`, `profile_changed`, and `runtime_change`. Core/frontend/worker version, installed executable-node revision, changed installed wheel, or conflicting destination remains incompatible. A new pinned executable package, model/input, approved UI package, or higher safe-profile revision is compatible and enters bounded delta provisioning. Parameter, text, and seed-only changes with identical dependencies produce an empty delta.

Use digest identity for all comparisons. A same filename/path with a different digest is incompatible; no overwrite occurs.

- [ ] **Step 4: Make resolver and provisioning validate separate authorities**

Resolver derives graph classes only from executable prompt nodes. UI packages come only from the approved profile allowlist, never from prompt metadata. Provisioning installs both immutable archive classes but readiness validates:

- executable package expected classes through `/object_info`;
- UI package release/web digest through the profile validator;
- every model/input path, size, and SHA-256;
- profile archive, file list, bootstrap digest, and revision.

Store the validated manifest digest, profile revision, and UI package digests in worker installed state. Do not treat a UI-only package as satisfying a graph class.

- [ ] **Step 5: Update exact protocol/release fixtures**

All signed request fixtures, manifest JSON fixtures, worker release metadata, and boundary tests use protocol `2`. Preserve rejection of protocol `1` at the new worker and rejection of protocol `2` at an old fixture. This intentionally makes the existing public release lock fail closed until a separately approved release is built and published.

- [ ] **Step 6: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_manifest \
  tests.python.test_resolver \
  tests.python.test_artifacts \
  tests.python.test_worker_protocol \
  tests.python.test_worker_provision \
  tests.python.test_worker_release -v
scripts/check.sh
```

Expected: schema/protocol 2 passes offline; existing private runtime release files remain untouched and live launch stays fail closed.

```sh
git add cloud_run/manifest.py cloud_run/worker_protocol.py cloud_run/resolver.py \
  cloud_run/artifacts.py remote_worker/provision.py remote_worker/state.py \
  tests/python/test_manifest.py tests/python/test_resolver.py \
  tests/python/test_artifacts.py tests/python/test_worker_protocol.py \
  tests/python/test_worker_provision.py tests/python/test_worker_server.py \
  tests/python/test_worker_release.py
git diff --cached --check
git commit -m "feat: version Cloud Vast profile manifests"
```

---

### Task 7: Record native Desktop prompts without replacing their event stream

**Files:**
- Create: `remote_worker/native_jobs.py`
- Modify: `remote_worker/jobs.py`
- Modify: `remote_worker/state.py`
- Modify: `remote_worker/comfy.py`
- Create: `tests/python/test_worker_native_jobs.py`
- Modify: `tests/python/test_worker_jobs.py`
- Modify: `tests/python/test_worker_server.py`
- Modify: `scripts/build_worker_artifact.py`

**Interfaces:**
- Produces: `NativePromptIntent`, `NativePromptReceipt`, and `NativeJobRecorder.begin(intent)`, `bind_prompt(job_id, response)`, `observe_text(client_id, frame)`, `observe_binary(client_id, frame)`, `finish(job_id)`, `fail(job_id, error)`, `snapshot(job_id, cursor)`.
- Consumes: signed server-only job headers, standard Comfy `/prompt` request/response bodies, native WebSocket frames, `WorkerStateStore`, safe event sanitizers, and strict final output rules.

- [ ] **Step 1: Write failing native intent/idempotency tests**

Build a standard Comfy request:

```python
body = {
    "client_id": "desktop-client-1",
    "prompt": {"9": {"class_type": "SaveImage", "inputs": {}}},
    "extra_data": {"extra_pnginfo": {"workflow": workflow_fixture()}},
}
intent = NativePromptIntent.from_http(
    job_id="job-1",
    request_id="request-1",
    manifest_digest="a" * 64,
    body=body,
)
```

Assert first `begin` persists queued intent; exact retry returns the existing receipt without another forward; the same request ID with changed bytes fails validation; two identical batch bodies with distinct request IDs create two ordered jobs; an installed-manifest mismatch fails before forward.

Run:

```sh
python3 -m unittest tests.python.test_worker_native_jobs -v
```

Expected: `ERROR` because `remote_worker.native_jobs` does not exist.

- [ ] **Step 2: Extract reusable recording from `JobManager`**

Move event sanitization, preview storage, output-history parsing, and snapshot construction behind `NativeJobRecorder`; retain `JobManager.start` as a compatibility headless route that drives `ComfyProcess.execute_native` and calls the same recorder. Do not duplicate sanitizer/output rules in two files.

`NativePromptIntent.request_digest` covers manifest digest plus canonical standard prompt body, excluding server-only headers. `request_id` is part of job identity, so identical fixed-seed batch prompts remain distinct.

- [ ] **Step 3: Observe native frames but return them unchanged**

`observe_text` parses a detached copy, resolves job by client ID and prompt ID, sanitizes only the persistent copy, increments one contiguous sequence, and returns no replacement payload. `observe_binary` validates size/type, stores a bounded preview for recovery, and never mutates the original bytes. The proxy task forwards the exact original text/binary frame separately.

Terminal `execution_success`, `execution_error`, and `execution_interrupted` persist execution state immediately. `finish` fetches history after success, ignores `temp`, validates only `output`, and records a harvest error independently.

- [ ] **Step 4: Make unexpected background recorder failures durable**

Every task created for history harvest has a callback that calls `result()` once. An unexpected exception is converted to `internal_error`/`harvest` state in the same job record with a correlation ID; it is never consumed silently. `close()` awaits all recorder tasks with bounded cancellation and preserves stored terminal execution state.

- [ ] **Step 5: Verify compatibility and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_native_jobs \
  tests.python.test_worker_jobs \
  tests.python.test_worker_server -v
scripts/check.sh
```

Expected: the old headless path and new passive path produce the same safe event/output snapshot shapes; no native frame content is rewritten for the connected UI.

```sh
git add remote_worker/native_jobs.py remote_worker/jobs.py remote_worker/state.py \
  remote_worker/comfy.py scripts/build_worker_artifact.py \
  tests/python/test_worker_native_jobs.py tests/python/test_worker_jobs.py \
  tests/python/test_worker_server.py
git diff --cached --check
git commit -m "feat: record native ComfyUI Desktop jobs"
```

---

### Task 8: Add the authenticated native ComfyUI HTTP/WebSocket worker data plane

**Files:**
- Create: `remote_worker/native_proxy.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/main.py`
- Modify: `remote_worker/Caddyfile`
- Modify: `remote_worker/gateway.py`
- Modify: `scripts/build_worker_artifact.py`
- Create: `tests/python/test_worker_native_proxy.py`
- Modify: `tests/python/test_worker_server.py`
- Modify: `tests/python/test_worker_gateway.py`
- Modify: `tests/python/test_worker_release_tools.py`

**Interfaces:**
- Produces: `NativeRoutePolicy.classify(method, path)`, `NativeComfyProxy.handle(request)`, and `WorkerApplication.handle_native(request)`.
- Consumes: Caddy-authenticated boundary marker, protocol-2 HMAC envelope, fixed upstream `http://127.0.0.1:8188`, `NativeJobRecorder`, and server-only headers `X-Cloud-Vast-Job-Id`, `X-Cloud-Vast-Request-Id`, and `X-Cloud-Vast-Manifest`.

- [ ] **Step 1: Write the route-policy and auth RED tests**

Exact allowed categories:

```python
allowed = {
    ("GET", "/"),
    ("GET", "/assets/index.js"),
    ("GET", "/extensions/ComfyUI-Cloud-Run/cloud-run.js"),
    ("GET", "/object_info"),
    ("GET", "/models/checkpoints"),
    ("GET", "/embeddings"),
    ("GET", "/queue"),
    ("GET", "/history"),
    ("GET", "/history/00000000-0000-0000-0000-000000000001"),
    ("GET", "/view?filename=x.png&type=temp"),
    ("GET", "/ws?clientId=desktop-client-1"),
    ("POST", "/prompt"),
    ("POST", "/queue"),
    ("POST", "/interrupt"),
    ("POST", "/free"),
}
```

Explicitly reject `/cloud-run/api/sessions`, every `/comfyui_mcp_panel/*` route, `/manager`, `/v2/manager`, arbitrary proxy URLs, traversal/encoded traversal, nonallowlisted methods, oversized bodies, missing boundary, bad/replayed HMAC, and any client-supplied `Authorization` or `X-Cloud-Vast-*` header that did not come through the controller signature. The three safe Agent Panel compatibility reads are synthesized later by the local Desktop relay and never reach this pod proxy.

Run:

```sh
python3 -m unittest tests.python.test_worker_native_proxy -v
```

Expected: `ERROR` because the native proxy does not exist.

- [ ] **Step 2: Implement a fixed-origin proxy with no redirect following**

`NativeRoutePolicy` uses method-specific compiled patterns and normalized raw paths. Static asset GET allows only safe relative path components and approved roots. Native API writes are limited to the pinned frontend fixture. Manager install/update, custom URL fetch, provider, terminal, and worker control surfaces are never part of the native policy.

`NativeComfyProxy` creates its client session with fixed `base_url=http://127.0.0.1:8188`, disabled redirects, bounded timeouts, and no environment proxy. Strip hop-by-hop, authorization, cookie, host, forwarded, and server-only headers before upstream. Copy only reviewed content headers and safe response headers.

- [ ] **Step 3: Bind native prompt recording to the forward operation**

For signed POST `/prompt`, require all three server-only identity headers, call `recorder.begin` before upstream, and forward a canonical standard Comfy body. On exact durable retry with an existing `prompt_id`, return the stored native response without another upstream POST. On first response, require native `{prompt_id, number, node_errors}`, bind it, and return those bytes/status.

Other native routes cannot carry job identity headers. A native prompt lacking a durable identity returns a safe 409 and never reaches Comfy.

- [ ] **Step 4: Proxy WebSocket frames byte-for-byte while observing copies**

Validate `clientId`, open one fixed upstream WS, and pump both directions with a maximum message size and heartbeat. For upstream→Desktop, call recorder observation on a detached copy, then send the original frame with the same text/binary type. For Desktop→upstream, allow only pinned Comfy client frame types. Close codes propagate; task exceptions are observed and stored as synchronization evidence.

- [ ] **Step 5: Route authenticated non-worker requests in aiohttp**

`remote_worker/main.py` dispatches `/worker/v1/*` through the existing `WorkerResponse` adapter and all other requests through `await worker.handle_native(request)`, which performs the same boundary and HMAC validation before proxying. Caddy keeps the fixed `route` order and sends all accepted traffic only to `127.0.0.1:8766`; it never proxies directly to `8188`.

- [ ] **Step 6: Verify exact Caddy and proxy behavior**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_native_proxy \
  tests.python.test_worker_server \
  tests.python.test_worker_gateway \
  tests.python.test_worker_release_tools -v
scripts/check.sh
```

Expected: native fixtures pass, missing/invalid bearer receives 401 before header removal, worker listens only on 127.0.0.1:8766 behind Caddy :8765, and no token appears in captured logs.

```sh
git add remote_worker/native_proxy.py remote_worker/server.py remote_worker/main.py \
  remote_worker/Caddyfile remote_worker/gateway.py scripts/build_worker_artifact.py \
  tests/python/test_worker_native_proxy.py tests/python/test_worker_server.py \
  tests/python/test_worker_gateway.py tests/python/test_worker_release_tools.py
git diff --cached --check
git commit -m "feat: proxy native ComfyUI through the worker"
```

**Offline checkpoint C:** a fake Desktop client must receive the original native WebSocket frames, while an independent atomic snapshot contains sanitized ordered copies. Assert no request touched local `127.0.0.1:8188` outside the fake pod boundary.

---

### Task 9: Provide the stable loopback-only official Desktop relay

**Files:**
- Create: `cloud_run/desktop_relay.py`
- Modify: `cloud_run/repository.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/worker_client.py`
- Modify: `cloud_run/routes.py`
- Modify: `cloud_run/service.py`
- Create: `tests/python/test_desktop_relay.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/python/test_service.py`

**Interfaces:**
- Produces: `DesktopRelayConfig`, `DesktopRelayStatus`, `DesktopRelay.start()`, `activate(session_id, worker, profile_revision)`, `deactivate(session_id)`, `status()`, `close()`, and `WorkerClient.native_envelope(method, path_qs, body)`.
- Consumes downstream: official Remote Connection URL `http://127.0.0.1:<persisted-port>`, safe desktop context, native prompt-preparation callback, and Agent bridge callback.

- [ ] **Step 1: Write failing persistence, binding, origin, and capability tests**

Required cases:

```python
relay = DesktopRelay(
    repository=repository,
    bind_host="127.0.0.1",
    port_selector=lambda: 32145,
    worker_factory=fake_worker_factory,
    native_prompt=fake_native_prompt,
    capability_factory=lambda: "capability-" + "a" * 48,
    clock=lambda: 100.0,
)
await relay.start()
self.assertEqual(relay.status().url, "http://127.0.0.1:32145")
```

Assert first launch persists 32145; restart reuses it; occupied 32145 yields `local_port_unavailable` and never selects another port; non-loopback configuration is rejected; inactive relay returns 503; active wrong Host/Origin returns 403; provider/controller paths return 404; top-level navigation mints one HttpOnly `SameSite=Strict` session cookie with a bounded expiry; subrequests and WebSockets require that cookie; capability expires/revokes at deactivate and never appears in URL/body/log/status.

Run:

```sh
python3 -m unittest tests.python.test_desktop_relay -v
```

Expected: `ERROR` because `cloud_run.desktop_relay` does not exist.

- [ ] **Step 2: Add exact relay configuration persistence**

Create a singleton table in schema version `9`:

```sql
CREATE TABLE IF NOT EXISTS desktop_relay (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    bind_host TEXT NOT NULL,
    port INTEGER NOT NULL,
    active_session_id TEXT,
    profile_revision INTEGER,
    updated_at REAL NOT NULL
)
```

Repository methods are `get_desktop_relay()` and `save_desktop_relay(config, *, expected_updated_at=None)`. Store no worker URL, bearer, HMAC secret, cookie capability, or Agent token in SQLite. Set schema version `9`.

- [ ] **Step 3: Expose a server-side native signing envelope**

`WorkerClient.native_envelope` validates an allowlisted method/path, canonical body bytes, and returns a private `WorkerRequest` using the same boundary bearer plus protocol timestamp/nonce/HMAC as control routes. It accepts headers only through explicit parameters owned by `DesktopRelay`; it does not expose this method through browser JSON routes.

- [ ] **Step 4: Implement the loopback aiohttp lifecycle**

Select and persist a free port only when no config row exists; bind it immediately to eliminate selection races. On restart bind the exact saved port. Configure bounded `client_max_size`, no environment proxy, and an explicit cleanup path. `activate` accepts only a locally persisted paid session in validated `READY` state and keeps one upstream. `deactivate` revokes in-memory capabilities and makes every native route 503 without destroying anything.

`GET /cloud-run/api/desktop-context` on the relay returns only:

```json
{
  "role": "vast",
  "session_id": "session-1",
  "profile_revision": 3,
  "agent_bridge_url": "ws://127.0.0.1:32145/cloud-run/api/agent/ws"
}
```

It sets the HttpOnly cookie in the response. The ordinary local backend route returns `{"role":"local"}` and never sets this cookie.

- [ ] **Step 5: Proxy allowed native HTTP and WebSocket traffic**

Reuse the same route policy fixture as Task 8. Sign upstream requests server-side; strip renderer cookies, authorization, forwarded headers, and all secrets. `/prompt` delegates to the injected `native_prompt` callback before signing/forwarding; `/ws` signs its handshake and relays frames byte-for-byte. Static/assets/object-info/models/history/view stay native upstream calls.

Do not define any forwarding target for the local Comfy origin. A fake local prompt trap must remain at zero calls across all tests.

- [ ] **Step 6: Add safe controller setup/status routes**

Register:

```text
GET  /cloud-run/api/desktop-setup
POST /cloud-run/api/sessions/{session_id}/desktop-relay
DELETE /cloud-run/api/sessions/{session_id}/desktop-relay
```

The setup response says whether the relay is bound, exact nonsecret loopback URL, expected connection name `ComfyUI Vast`, one-time manual setup instructions, active session ID, readiness, and safe error. Activation is local-only and never opens an external browser or edits Desktop registry files. Deactivation only disconnects the data plane; it does not destroy the pod.

- [ ] **Step 7: Wire startup/cleanup without a second local backend**

`build_service` owns one relay instance. ComfyUI's existing backend startup hook starts it; cleanup closes it. No subprocess, `open`, Desktop private file, or second PromptServer is created. Startup reconciliation reactivates only the persisted session that still passes durable readiness.

- [ ] **Step 8: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_desktop_relay \
  tests.python.test_job_repository \
  tests.python.test_routes \
  tests.python.test_service \
  tests.python.test_loader_and_routes -v
scripts/check.sh
```

Expected: a fixture shaped like official Desktop can load through the stable loopback origin; all auth remains backend-owned; the local prompt trap remains unused.

```sh
git add cloud_run/desktop_relay.py cloud_run/repository.py \
  cloud_run/job_repository.py cloud_run/worker_client.py cloud_run/routes.py \
  cloud_run/service.py tests/python/test_desktop_relay.py \
  tests/python/test_job_repository.py tests/python/test_routes.py \
  tests/python/test_service.py
git diff --cached --check
git commit -m "feat: expose a scoped ComfyUI Vast Desktop relay"
```

---

### Task 10: Build and synchronize the content-addressed safe Desktop profile

**Files:**
- Create: `cloud_run/desktop_profile.py`
- Create: `remote_worker/profile.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/routes.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/provision.py`
- Modify: `scripts/build_worker_artifact.py`
- Create: `tests/python/test_desktop_profile.py`
- Create: `tests/python/test_worker_profile.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_worker_server.py`

**Interfaces:**
- Produces: `ProfileArtifact`, `DesktopProfile`, `ProfileConflict`, `DesktopProfileStore.capture(...)`, `apply_remote_snapshot(...)`, and worker `ProfileStore.apply(profile)`, `snapshot(after_revision)`, `artifact(profile_id, path)`.
- Routes: authenticated worker `PUT /worker/v1/profile`, `GET /worker/v1/profile?after_revision=N`, and ranged `GET /worker/v1/profile/artifacts/{artifact_id}`; safe controller `GET /cloud-run/api/sessions/{session_id}/profile` and `POST .../profile/conflicts/{conflict_id}`.

- [ ] **Step 1: Write the safe allowlist RED tests**

Build a temporary user tree containing:

```text
default/workflows/a.json
default/comfy.settings.json
default/color_palettes/my-palette.json
default/comfyui.db
default/.env
default/cache/item
default/__pycache__/x.pyc
outside-secret.txt
input/backgrounds/wallpaper.jpg
```

Capture an unsaved bootstrap workflow and settings containing an absolute wallpaper path. Assert only the two safe JSON groups, bootstrap, approved background, and approved UI asset records enter the archive; forbidden names/paths never appear; local settings retain the absolute value; remote settings use `/api/view?filename=cloud-vast/backgrounds/<digest>.jpg&type=input`; archive members are normalized, sorted, digest-verified, and reproducible.

Run:

```sh
python3 -m unittest tests.python.test_desktop_profile -v
```

Expected: `ERROR` because the profile module does not exist.

- [ ] **Step 2: Implement exact profile value objects and archive rules**

Use immutable fields:

```python
@dataclass(frozen=True)
class ProfileArtifact:
    logical_path: str
    kind: str
    size_bytes: int
    sha256: str
    private_path: Path

@dataclass(frozen=True)
class DesktopProfile:
    profile_id: str
    revision: int
    base_revision: int | None
    bootstrap_digest: str
    archive_size_bytes: int
    archive_sha256: str
    artifacts: tuple[ProfileArtifact, ...]
    ui_packages: tuple[UiPackageSpec, ...]
```

Allowed kinds are `workflow`, `bootstrap_workflow`, `settings`, `palette`, `background`, and `ui_asset`. JSON parses with duplicate-key rejection, finite numbers, depth/size limits, and secret-key rejection. Archives use fixed metadata and contain no private source path.

- [ ] **Step 3: Persist profile revisions and conflicts atomically**

Add schema version `10` tables `profile_revisions` and `profile_conflicts`. Store canonical metadata and private content-addressed archive path, not raw secrets. Revisions increase by exactly one. Remote changes based on the current base atomically replace the safe local mirror. Concurrent local and remote descendants create two immutable conflict sides labeled `Local` and `Cloud Vast`; neither silently wins. Resolution names one side and creates a new revision while retaining conflict evidence.

- [ ] **Step 4: Implement worker apply and snapshot**

`ProfileStore.apply` safely extracts to a private staging directory, validates every listed member/digest, verifies UI package asset roots, then atomically swaps only the managed profile subtree. It places background under standard pod `input/cloud-vast/backgrounds`, workflows/settings under the pinned user's allowed directory, and approved UI packages under their exact custom-node destinations. Existing unrelated pod files are not deleted or exposed.

`snapshot(after_revision)` walks only managed safe paths, emits a new content-addressed revision when changed, and never includes database, environment, cache, logs, bytecode, credentials, symlinks, or unrelated custom-node state.

- [ ] **Step 5: Synchronize at every durable boundary**

Initial profile capture is part of free preflight and exact quote digest. Provisioning uploads/applies it before readiness. Reconciler polls profile metadata on refresh/restart/reconnect, downloads changed safe artifacts with digest verification, and performs a final sync before an already-authorized teardown. A profile sync warning does not enqueue a prompt or mutate Vast.

- [ ] **Step 6: Verify bootstrap overwrite protection**

Worker profile state records `bootstrap_loaded_at_revision`. The frontend consumes the bootstrap exactly once only when no remote edit revision is newer. A reopen or delayed initial response cannot overwrite a newer remote canvas.

- [ ] **Step 7: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_desktop_profile \
  tests.python.test_worker_profile \
  tests.python.test_session_service \
  tests.python.test_worker_server -v
scripts/check.sh
```

Expected: round-trip profile sync, remote-only wallpaper rewrite, exact conflict preservation, and malicious archives all behave deterministically.

```sh
git add cloud_run/desktop_profile.py remote_worker/profile.py \
  cloud_run/session_service.py cloud_run/job_repository.py cloud_run/routes.py \
  remote_worker/server.py remote_worker/provision.py scripts/build_worker_artifact.py \
  tests/python/test_desktop_profile.py tests/python/test_worker_profile.py \
  tests/python/test_session_service.py tests/python/test_worker_server.py
git diff --cached --check
git commit -m "feat: synchronize safe ComfyUI Vast profiles"
```

---

### Task 11: Keep Agent Panel local through a scoped session bridge

**Files:**
- Create: `cloud_run/agent_bridge.py`
- Modify: `cloud_run/desktop_relay.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/desktop_profile.py`
- Modify: `cloud_run/routes.py`
- Create: `tests/python/test_agent_bridge.py`
- Modify: `tests/python/test_desktop_relay.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_desktop_profile.py`

**Interfaces:**
- Produces: `AgentBridgePolicy`, `AgentBridge.open(request, session)`, `probe(session)`, `revoke(session_id)`, and `close()`.
- Fixed upstream: `ws://127.0.0.1:9180`; fixed same-origin endpoint: `/cloud-run/api/agent/ws`; safe compatibility reads: `/comfyui_mcp_panel/status`, `/comfyui_mcp_panel/bridge_url`, and `/comfyui_mcp_panel/backends`; authentication: relay HttpOnly capability cookie plus exact Host/Origin/session binding.

- [ ] **Step 1: Write failing tunnel-confinement tests**

Assert constructor rejects every upstream except literal loopback port 9180; URL path/query overrides are rejected; missing/expired/revoked capability fails; wrong Host/Origin/session fails; binary frames and oversized JSON fail; secrets are never journaled; destruction revokes active sockets.

Allow the pinned frame envelope types needed for conversation/status plus graph commands. The command allowlist contains the exact tested integration names:

```python
AGENT_COMMANDS = frozenset({
    "refresh_nodes", "graph_serialize", "graph_get_state",
    "graph_view_selected", "graph_outline", "graph_query",
    "graph_find_nodes", "graph_get_subgraph", "graph_add_node",
    "graph_remove_node", "graph_clear", "graph_load", "graph_connect",
    "graph_disconnect", "graph_set_widget", "graph_set_node_property",
    "graph_move_node", "graph_resize_node", "graph_auto_layout",
    "graph_canvas", "graph_run", "graph_get_errors",
    "graph_select_nodes", "workflow_save", "workflow_save_as",
    "workflow_list", "workflow_new", "workflow_open",
    "workflow_rename", "workflow_close", "nodes_search", "nodes_list",
    "nodes_queue_status",
})
```

Explicitly reject `request_secret`, node-manager install/update/reboot commands, training/provider tools, arbitrary `call_tool`, arbitrary local filesystem/terminal operations, and messages whose `comfyuiUrl` differs from the active relay origin.

Run:

```sh
python3 -m unittest tests.python.test_agent_bridge -v
```

Expected: `ERROR` because `cloud_run.agent_bridge` does not exist.

- [ ] **Step 2: Implement a JSON-aware fixed-upstream bridge**

Both directions parse bounded JSON and apply direction-specific schemas. Panel→orchestrator permits hello/title, user message, safe agent event, session options, and graph-scoped control frames; orchestrator→panel permits safe status/content frames plus `rid` commands only from `AGENT_COMMANDS`. Pin/replace hello `comfyuiUrl` with the active relay origin. Ignore the renderer-supplied `comfyuiPath` and, only when the pinned Agent Panel handshake requires it, substitute the controller-owned approved local ComfyUI root resolved by `ComfyHost`; remote code can never choose another Mac path.

Forward no raw cookie/capability to 9180. Keep capability only in the local handshake. Observe pump task exceptions, journal `synchronization_error`, and close both sockets.

Handle the three pinned Agent Panel compatibility GETs in `DesktopRelay`, returning only safe bridge/backend readiness derived from `AgentBridge`; never forward them to the pod's Agent Panel backend or local ComfyUI HTTP origin. Explicitly reject Agent Panel `advertise_bridge`, process connect/disconnect/reload/restart, CivitAI proxy, training, Apps, node installation, and arbitrary backend-tool HTTP routes. The required graph/chat/run/batch path is the scoped WebSocket, not a general HTTP tunnel.

- [ ] **Step 3: Rewrite only the remote Agent Panel setting**

When the approved profile includes the pinned Agent Panel release, set remote `comfyui-mcp.bridgeUrl.single` to the same-origin bridge URL and leave `comfyui-mcp.remoteComfyuiUrl` empty so the panel targets `window.location.origin`. Keep the local user's settings unchanged. Do not modify or import the installed Agent Panel source tree.

- [ ] **Step 4: Add mandatory readiness probes**

`probe(session)` establishes the scoped bridge with a fake/pinned handshake and proves:

1. `models` or safe degraded handshake identifies the intended local orchestrator;
2. graph state can be read from the active Vast canvas;
3. a reversible fixture widget edit is observed and restored;
4. `graph_run` queues through the native remote prompt boundary;
5. `graph_run` with `batch_count=2` creates two ordered durable remote identities.

Offline readiness uses fake frames and never invokes a real agent. In production, if Agent Panel is present and any probe fails, readiness remains false with `dependency_error` or `synchronization_error`; no security rule is relaxed.

- [ ] **Step 5: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_agent_bridge \
  tests.python.test_desktop_relay \
  tests.python.test_desktop_profile \
  tests.python.test_session_service -v
scripts/check.sh
```

Expected: graph read/edit/run/batch fixtures work against the active relay origin and every general-tunnel attempt fails closed.

```sh
git add cloud_run/agent_bridge.py cloud_run/desktop_relay.py \
  cloud_run/session_service.py cloud_run/desktop_profile.py cloud_run/routes.py \
  tests/python/test_agent_bridge.py tests/python/test_desktop_relay.py \
  tests/python/test_session_service.py tests/python/test_desktop_profile.py
git diff --cached --check
git commit -m "feat: bridge Agent Panel to ComfyUI Vast safely"
```

---

### Task 12: Persist every native Run or batch intent before forwarding and apply compatible deltas

**Files:**
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/desktop_relay.py`
- Modify: `cloud_run/capture.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_desktop_relay.py`
- Modify: `tests/python/test_capture.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `tests/python/test_fake_session_integration.py`

**Interfaces:**
- Produces: `SessionService.prepare_native_prompt(session_id, *, request_id, body) -> NativePromptForward`, where the return contains `job_id`, `request_id`, `manifest_digest`, canonical `body`, and signed server-only header values.
- Consumes: native standard prompt body, current installed manifest, resolver, compatible `ManifestDelta`, worker transaction, and one-at-a-time session lock.

- [ ] **Step 1: Write failing native Run and batch intent tests**

Assert:

- a request ID is required and canonical;
- durable `CloudJob` intent exists before `desktop_relay` calls upstream;
- an exact retry returns the same job ID and remote prompt response;
- changed bytes under the same request ID fail;
- two equal batch bodies with `request-1` and `request-2` create two jobs in order;
- a covered manifest forwards immediately;
- a compatible model/input/pinned-node/profile delta visibly provisions then forwards the same job once;
- unresolved source, unpinned node, digest collision, or runtime/core change rejects before `/prompt`;
- session not `READY`, wrong active relay session, and a second simultaneous GPU execution fail safely;
- the local prompt trap is never called.

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.NativeDesktopPromptTests \
  tests.python.test_desktop_relay.DesktopNativePromptTests -v
```

Expected: `ERROR` because `prepare_native_prompt` and the native relay identity path do not exist.

- [ ] **Step 2: Parse the native body through the shared capture boundary**

Add `CompiledCapture.from_native_prompt(body)` that extracts `prompt`, `extra_data.extra_pnginfo.workflow`, partial targets, preview method, and queue semantics from the pinned frontend request, then calls the same tree/credential/version validators. It never reconstructs inputs and never posts locally.

The browser-supplied body cannot choose manifest, session, job, provider, worker, destination, source URL, or credential handles.

- [ ] **Step 3: Persist identity before any remote or provider effect**

Under a per-session async lock:

```python
existing = repository.get_job_by_idempotency_key(session_id, request_id)
if existing is not None:
    verify_same_prompt_digest(existing, capture.prompt_digest)
    return forward_from(existing)
job, created = repository.create_job(new_native_job(...))
assert created
```

Only after the committed row exists may resolver/provisioner/relay send anything. This path never calls lifecycle create and never authorizes a replacement; it uses the already-rented exact session.

- [ ] **Step 4: Apply and validate only compatible delta work**

Resolve the fresh capture plus current safe profile. If delta is empty, mark queued and return. If compatible, transition job resolving and session provisioning, reuse `provision-<desired digest>` transaction recovery, validate installed classes/models/profile, update installed set, restore session ready/running, and return the same forward identity. If incompatible, mark this job with `dependency_error`, leave the existing healthy pod intact, and tell the user a new quoted session is required.

No native prompt is forwarded before delta readiness. When ready, forwarding is automatic and requires no second Run click or paid confirmation.

- [ ] **Step 5: Enforce ordered one-at-a-time GPU execution**

Persist a monotonic `queue_position` on jobs in schema version `11`. Multiple native batch submissions may be queued durably; only the first ready position forwards. On terminal execution state the reconciler releases the next position. A restart reconstructs ordering from SQLite and worker snapshots. Exact retry never gets a second position.

- [ ] **Step 6: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_capture \
  tests.python.test_job_repository \
  tests.python.test_session_service \
  tests.python.test_desktop_relay \
  tests.python.test_fake_session_integration -v
scripts/check.sh
```

Expected: native Run, keyboard-equivalent submission, and a two-item Agent batch all run only through fake pod endpoints with durable idempotent identities and compatible delta provisioning.

```sh
git add cloud_run/session_service.py cloud_run/desktop_relay.py \
  cloud_run/capture.py cloud_run/job_repository.py cloud_run/models.py \
  cloud_run/routes.py tests/python/test_session_service.py \
  tests/python/test_desktop_relay.py tests/python/test_capture.py \
  tests/python/test_job_repository.py tests/python/test_fake_session_integration.py
git diff --cached --check
git commit -m "feat: bind native Vast prompts to durable jobs"
```

**Offline checkpoint D:** require two independent fake Desktop environments: local native Run hits only the fake local backend; `ComfyUI Vast` native Run and batch hit only the fake worker/pod. Close/reopen both and require zero duplicate prompt IDs.

---

### Task 13: Make the web extension role-aware and keep native Run unchanged

**Files:**
- Create: `web/js/comfyui-vast.js`
- Modify: `web/js/cloud-run.js`
- Modify: `web/js/cloud-run-api.js`
- Modify: `web/js/session-console.js`
- Modify: `tests/js/fake-dom.mjs`
- Create: `tests/js/comfyui-vast.test.mjs`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `tests/js/cloud-run-api.test.mjs`
- Modify: `tests/js/session-console.test.mjs`

**Interfaces:**
- Produces: `readDesktopContext(fetchImpl)`, `installNativePromptIdentity(api, crypto)`, `bootstrapVastCanvas(app, context)`, and `isVastRole(context)`.
- Consumes: same-origin `/cloud-run/api/desktop-context`, `crypto.randomUUID`, supported `app.loadGraphData`, and the pinned frontend `api.fetchApi` boundary.

- [ ] **Step 1: Write failing role and native-prompt tests**

Required JavaScript assertions:

```javascript
test("local role mounts Cloud Vast and leaves native queuePrompt untouched", async () => {
  const original = api.fetchApi;
  await startExtension({ context: { role: "local" }, api, app });
  assert.equal(api.fetchApi, original);
  assert.match(view.launcher.textContent, /Cloud Vast/);
});

test("vast role hides lifecycle and adds one identity only to native prompt", async () => {
  await startExtension({ context: { role: "vast", session_id: "session-1" }, api, app, crypto });
  assert.equal(view.launcher, null);
  await api.fetchApi("/prompt", { method: "POST", body: "{}" });
  assert.equal(calls[0].headers.get("X-Cloud-Vast-Request-Id"), "request-1");
  await api.fetchApi("/object_info");
  assert.equal(calls[1].headers.has("X-Cloud-Vast-Request-Id"), false);
});
```

Assert there is no visible string `Run Vast`, no controller `createJob` call, ordinary `app.queuePrompt` identity/function remains unchanged, and native keyboard/batch actions still reach its existing code.

Run:

```sh
node --test tests/js/comfyui-vast.test.mjs tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
```

Expected: new module import fails and current user copy still says `Cloud Run` and exposes a second controller Run flow.

- [ ] **Step 2: Discover role before mounting lifecycle UI**

At extension setup, call same-origin Desktop context. Fail closed on malformed context. Local role mounts lifecycle; Vast role installs only prompt identity/bootstrap hooks; unknown role mounts neither and displays no secret-bearing error.

Do not infer role from hostname, query string, local storage, referrer, or mutable browser state. Context comes from the backend/relay-signed origin.

- [ ] **Step 3: Wrap only the pinned native fetch boundary**

Preserve function binding and every option. For Vast role and normalized `/prompt` POST only, clone `Headers`, reject a preexisting identity header, add one canonical UUID, and call the original `api.fetchApi` once:

```javascript
export function installNativePromptIdentity(api, cryptoImpl) {
  const original = api.fetchApi.bind(api);
  api.fetchApi = (route, options = {}) => {
    if (normalizedPromptPost(route, options)) {
      const headers = new Headers(options.headers || {});
      if (headers.has("X-Cloud-Vast-Request-Id")) {
        throw new Error("Cloud Vast prompt identity was already set.");
      }
      headers.set("X-Cloud-Vast-Request-Id", cryptoImpl.randomUUID());
      return original(route, { ...options, headers });
    }
    return original(route, options);
  };
  return () => { api.fetchApi = original; };
}
```

The wrapper never changes body, Run label, batch count, queue number, seeds, partial targets, previews, or response/event processing.

- [ ] **Step 4: Bootstrap the unsaved canvas once without overwriting edits**

Fetch the safe bootstrap revision from Desktop context/profile route. Before `app.loadGraphData`, compare remote active revision and dirty/edit marker. Load only when `bootstrap_revision > active_revision` and no newer edit exists; immediately acknowledge the revision. A delayed response, reopen, or second tab cannot overwrite a newer remote canvas.

- [ ] **Step 5: Rename the local product copy and remove controller execution**

User-facing labels become:

```text
☁ Cloud Vast
Cloud Vast
Louer et préparer
ComfyUI Vast
Destroy GPU — stop all Vast billing
```

Keep internal module, CSS class, database, and route names `cloud-run` for compatibility. Remove the second controller `Run`/`Run current canvas` button and its `api.createJob` call from the view. Ready state shows the Remote Connection name, stable setup URL/instructions, activation status, and `Ouvrir ComfyUI Vast dans Desktop` guidance; it never opens Brave/Chrome.

- [ ] **Step 6: Verify and commit**

Run:

```sh
node --test tests/js/*.test.mjs
scripts/check.sh
```

Expected: local role retains lifecycle/cost/destroy UI, Vast role retains native ComfyUI and Agent Panel UI only, and no `Run Vast` string exists in production assets.

```sh
git add web/js/comfyui-vast.js web/js/cloud-run.js web/js/cloud-run-api.js \
  web/js/session-console.js tests/js/comfyui-vast.test.mjs \
  tests/js/cloud-run-ui.test.mjs tests/js/cloud-run-api.test.mjs \
  tests/js/session-console.test.mjs tests/js/fake-dom.mjs
git diff --cached --check
git commit -m "feat: separate Cloud Vast and ComfyUI Vast roles"
```

---

### Task 14: Explain offers and enforce honest source-readiness estimates

**Files:**
- Modify: `cloud_run/offers.py`
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/routes.py`
- Modify: `web/js/cloud-run.js`
- Modify: `web/js/session-console.js`
- Modify: `tests/python/test_offers.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `tests/js/session-console.test.mjs`

**Interfaces:**
- Produces: `ReadinessEstimate`, `OfferDecision`, visible `included_reasons`/`excluded_reasons`, and confirmation fields `estimate_digest` plus `accepted_longer_estimate`.
- Consumes: workflow hard minimum VRAM/disk, optional user preference, hard price cap, DLPerf, network, disk, reliability, cached bytes, remaining bytes, and source-ready verdict.

- [ ] **Step 1: Write failing semantic and ranking tests**

Exact cases:

- empty user VRAM/price fields apply no user filter;
- 12 GiB preference cannot lower a workflow 24 GiB minimum;
- price cap 0.50 excludes 0.51 even when faster;
- a performant close-fit GPU outranks a weak cheap GPU when both satisfy cap/minimum;
- exclusion reason identifies VRAM, disk, price, reliability, source-readiness, or missing metrics;
- estimated transfer uses remaining bytes and conservative observed bandwidth;
- 29,347,469,703 remaining bytes at 25 and 30 MB/s is labeled cold and above 600 seconds;
- pre-positioned/source-ready estimate at or below 600 seconds is eligible;
- an estimate above 600 requires explicit `accepted_longer_estimate=true` bound to its digest;
- a changed estimate/quote rejects the old confirmation with `quote_expired` and zero creates.

Run:

```sh
python3 -m unittest tests.python.test_offers tests.python.test_session_service -v
```

Expected: new explanation/estimate assertions fail.

- [ ] **Step 2: Define immutable public decision values**

```python
@dataclass(frozen=True)
class ReadinessEstimate:
    label: str
    cached_bytes: int
    remaining_bytes: int
    assumed_mbps: float
    estimated_seconds: int
    source_ready: bool
    ten_minute_eligible: bool
    digest: str

@dataclass(frozen=True)
class OfferDecision:
    offer: OfferQuote | None
    included: bool
    score: tuple
    reasons: tuple[str, ...]
    estimate: ReadinessEstimate | None
```

The only labels are `cold`, `prepositioned`, and `warm`. Digest canonical estimate fields. Public reasons use finite allowlisted templates, not provider free text.

- [ ] **Step 3: Make price/minima hard and preference soft**

Filter in this order: workflow VRAM, workflow disk, user price cap, required metrics/reliability, source-readiness policy. Rank included offers by preference distance, readiness seconds, GPU/DLPerf, network, disk, reliability, then price and stable offer ID. Do not rank by lowest price first.

- [ ] **Step 4: Bind quote confirmation to current canvas and estimate**

At `Louer et préparer`, recapture current canvas/profile, recompute dependency/estimate digests, refresh exact offer/inventory, and compare every visible authorized term: offer ID, GPU, VRAM, hourly rate, disk, duration/manual mode, max authorized cost, manifest, profile, and estimate. Any change returns to free review and records `quote_expired`; no fallback offer or create.

Block confirmation when any required artifact lacks a source. For an estimate above 600 seconds, require the explicit longer-estimate checkbox for that exact digest. R2 cache writes remain absent; an existing user R2 object may count only after read-only metadata plus digest validation.

- [ ] **Step 5: Render a short explained shortlist**

Show three to five included offers and a collapsible excluded summary. Each row shows GPU/VRAM, hourly price, DLPerf, network/disk/reliability, cached/remaining bytes, cold/pre-positioned/warm, and estimated readiness. Use `textContent`. The paid button copy is only `Louer et préparer`; readiness does not auto-run a workflow.

- [ ] **Step 6: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_offers tests.python.test_models \
  tests.python.test_session_service tests.python.test_routes -v
node --test tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
scripts/check.sh
```

Expected: all price/estimate boundaries and zero-create expiry assertions pass.

```sh
git add cloud_run/offers.py cloud_run/models.py cloud_run/session_service.py \
  cloud_run/service.py cloud_run/routes.py web/js/cloud-run.js \
  web/js/session-console.js tests/python/test_offers.py \
  tests/python/test_models.py tests/python/test_session_service.py \
  tests/python/test_routes.py tests/js/cloud-run-ui.test.mjs \
  tests/js/session-console.test.mjs
git diff --cached --check
git commit -m "feat: explain Cloud Vast readiness and offers"
```

---

### Task 15: Gate relay activation on one durable complete readiness report

**Files:**
- Create: `cloud_run/readiness.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/repository.py`
- Modify: `cloud_run/desktop_relay.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/provision.py`
- Create: `tests/python/test_readiness.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_desktop_relay.py`
- Modify: `tests/python/test_worker_provision.py`

**Interfaces:**
- Produces: `ReadinessCheck`, `ReadinessReport`, `ReadinessValidator.validate(session, manifest, profile)`, durable `readiness_reports`, and controller payload `desktop_ready`.
- A report is valid only for exact session, instance, worker release, manifest, profile revision, relay origin, and timestamped inventory observation.

- [ ] **Step 1: Write the complete readiness-matrix RED test**

Create a passing fixture only when all exact check names are true:

```python
REQUIRED_READINESS_CHECKS = (
    "provider_instance_identity",
    "worker_authenticated",
    "worker_release_lock",
    "comfy_core_release",
    "comfy_frontend_release",
    "comfy_process_health",
    "required_object_info_classes",
    "model_and_input_digests",
    "safe_profile_digest",
    "approved_ui_asset_digests",
    "bootstrap_revision",
    "loopback_session_binding",
    "native_http_probe",
    "native_websocket_probe",
    "agent_panel_capabilities",
    "local_execution_unused",
)
```

Remove or fail each check in a subtest and assert `desktop_ready=false`, relay inactive, one typed safe journal entry, and no create/destroy call. When Agent Panel is absent by approved profile, its check passes as `not_required`; when present, it must be verified.

Run:

```sh
python3 -m unittest tests.python.test_readiness -v
```

Expected: `ERROR` because readiness module/report persistence does not exist.

- [ ] **Step 2: Implement strict immutable report values**

Each `ReadinessCheck` has `name`, `status` (`passed|failed|not_required`), `evidence_digest`, and safe `message`. `ReadinessReport` includes exact identities, all named checks once, creation time, `ready` derived from checks, and canonical report digest. Unknown/duplicate checks and free-form evidence objects are rejected.

- [ ] **Step 3: Persist and reuse without progress regression**

Add schema version `12` table keyed by report digest with a unique current identity tuple. Refresh returns the same validated report while identities match. A changed manifest/profile/worker/instance invalidates it and requires validation; it never restarts a ready provisioning transaction or resets transferred bytes.

- [ ] **Step 4: Probe only through intended boundaries**

Provider check is a fresh read-only inventory match. Worker/control checks use authenticated worker APIs. Native HTTP and WS probes go through the loopback relay with its capability and fixed upstream. `local_execution_unused` is a testable counter/guard that stays zero from paid confirmation through readiness. No probe submits `/prompt`, runs a node, writes R2, installs externally, or mutates Vast.

- [ ] **Step 5: Activate only after the report commits**

Session transitions `VALIDATING -> READY`, commits the report, then calls `desktop_relay.activate`. A crash between commit and activation is repaired on startup from the same report. Activation before commit is impossible. A later relay failure makes Desktop unavailable but does not claim provider teardown or execution failure.

- [ ] **Step 6: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_readiness \
  tests.python.test_session_service \
  tests.python.test_desktop_relay \
  tests.python.test_worker_provision -v
scripts/check.sh
```

Expected: the full matrix passes once, each missing proof blocks readiness, and a refresh reuses the durable report.

```sh
git add cloud_run/readiness.py cloud_run/session_service.py \
  cloud_run/job_repository.py cloud_run/repository.py cloud_run/desktop_relay.py \
  remote_worker/server.py remote_worker/provision.py \
  tests/python/test_readiness.py tests/python/test_session_service.py \
  tests/python/test_desktop_relay.py tests/python/test_worker_provision.py
git diff --cached --check
git commit -m "feat: certify ComfyUI Vast readiness atomically"
```

---

### Task 16: Verify and publish persistent outputs on the Mac without rerunning GPU work

**Files:**
- Modify: `cloud_run/relay.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/reconciler.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_relay.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `tests/python/test_reconciler.py`
- Modify: `tests/python/test_session_output_evidence.py`
- Modify: `tests/python/test_fake_session_integration.py`

**Interfaces:**
- Produces: deterministic output root `cloud-vast/<session-id>/<job-id>`, atomic `.part` download/publish, digest deduplication across Desktop auto-download and controller fetch, and `retry_harvest(job_id)`.
- Consumes: strict worker output descriptors, official Desktop downloaded-file candidates, resumable authenticated worker range fetch, and transfer records.

- [ ] **Step 1: Write failing dual-download and harvest-retry tests**

Required cases:

- an already present Desktop-downloaded file with exact size/SHA-256 is adopted without worker download;
- missing/mismatched candidate triggers authenticated ranged download into `.part`, fsync, digest check, atomic rename;
- a matching digest from both paths yields one final file and one verified record;
- temp preview never appears in final outputs;
- absolute/traversal/symlink/unexpected-root/malformed persistent descriptor yields `invalid_output` while execution remains succeeded;
- interrupted download resumes from stored offset;
- retry after harvest failure calls zero `start_job`/`/prompt` operations;
- local path never enters a worker response or event.

Run:

```sh
python3 -m unittest \
  tests.python.test_session_output_evidence \
  tests.python.test_reconciler.ReconcilerOutputTests -v
```

Expected: new deterministic-root/adoption/retry assertions fail.

- [ ] **Step 2: Constrain and construct final paths**

Validate canonical session/job/artifact identifiers. Root is the configured local Comfy output directory joined with literal `cloud-vast/session-id/job-id`; create directories mode 0700, regular output mode 0600, reject symlinks and ownership changes. Use public descriptor filename only after basename validation; collisions with different digest gain a deterministic digest suffix rather than overwrite.

- [ ] **Step 3: Adopt or fetch with one digest authority**

Hash an official Desktop candidate through an open descriptor with pre/post inode/size checks. Adopt only exact descriptor digest/size. Otherwise resume worker download from the durable offset, hash the complete temporary file, compare, fsync, and `os.replace`. Mark transfer verified only after final-file descriptor revalidation.

- [ ] **Step 4: Expose safe retry and evidence**

`POST /cloud-run/api/sessions/{session_id}/jobs/{job_id}/harvest` accepts only a job with execution succeeded and harvest failed/pending. It schedules reconciler harvest under single flight and returns durable status. It cannot submit a prompt or mutate provider state. Public payload includes safe filename, size, digest, node ID, and local-verified state; no private path.

- [ ] **Step 5: Verify and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_relay \
  tests.python.test_job_repository \
  tests.python.test_reconciler \
  tests.python.test_session_output_evidence \
  tests.python.test_fake_session_integration -v
scripts/check.sh
```

Expected: output recovery is idempotent, resumable, digest-verified, and never reruns execution.

```sh
git add cloud_run/relay.py cloud_run/job_repository.py cloud_run/reconciler.py \
  cloud_run/routes.py tests/python/test_relay.py \
  tests/python/test_job_repository.py tests/python/test_reconciler.py \
  tests/python/test_session_output_evidence.py \
  tests/python/test_fake_session_integration.py
git diff --cached --check
git commit -m "feat: verify ComfyUI Vast outputs locally"
```

---

### Task 17: Prove the complete offline Desktop experience and every live-audit regression

**Files:**
- Modify: `cloud_run/lifecycle.py`
- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/relay.py`
- Modify: `remote_worker/jobs.py`
- Modify: `remote_worker/provision.py`
- Create: `tests/python/test_fake_desktop_bridge_integration.py`
- Modify: `tests/python/test_fake_session_integration.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_loader_and_routes.py`
- Modify: `tests/python/test_relay.py`
- Modify: `tests/python/test_run_errors.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_worker_jobs.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Modify: `tests/python/test_repository_contract.py`
- Modify: `tests/js/comfyui-vast.test.mjs`
- Modify: `tests/js/session-console.test.mjs`
- Modify: `scripts/check.sh`

**Interfaces:**
- Produces: one deterministic end-to-end fake campaign covering free preflight, exact paid intent with fake create, profile provision, readiness, native Run, Agent batch, reconnect, backend restart, compatible delta, output recovery, and verified fake destroy.
- Consumes: every interface introduced in Tasks 0–16; no real network, credential, Desktop process, provider, publication, or external install.

- [ ] **Step 1: Write the end-to-end fake campaign before its harness**

The test must run this exact sequence:

1. local environment captures unsaved canvas/profile without local `/prompt`;
2. offer search is read-only and explains included/excluded fakes;
3. fake `Louer et préparer` persists intent before exactly one fake create;
4. worker/profile/model/custom-node fixtures provision and one readiness report commits;
5. relay activates on fixed loopback and official-Remote-Connection-shaped client loads object info/models/assets;
6. bootstrap canvas, palette, background, approved UI assets, and Agent Panel appear;
7. native Run emits executing/progress/progress-state/progress-text/preview/executed/success frames unchanged;
8. atomic snapshot independently reaches the same terminal prompt ID;
9. `SaveImage` verifies on Mac and `PreviewImage` temp is absent from final harvest;
10. closing/reopening the fake Vast Desktop catches up without resubmit;
11. reconstructing local service from the same SQLite reconciles without resubmit;
12. Agent graph edit adds one compatible input/model delta and its batch count two queues two ordered jobs;
13. safe workflow/settings revision returns to Mac and a conflict preserves both sides;
14. journal contains bounded phase/cursor/probe/log/output evidence and no seeded secret;
15. explicit fake destroy verifies fresh fake inventory zero and `billing_may_continue=false`;
16. local Run before/after the campaign touches only the fake local backend.

Run two lifecycle variants: a manual session survives both Desktop surfaces closing until explicit fake destroy, while a finite session executes only its already-authorized deadline path once and still requires fake inventory absence before claiming billing ended.

Run:

```sh
python3 -m unittest tests.python.test_fake_desktop_bridge_integration -v
```

Expected: fail at the first unintegrated boundary; fix integration wiring only, not component contracts.

- [ ] **Step 2: Replace every generic failure collapse with an exact typed mapping**

Add table-driven tests and wire the caught boundaries to these exact values:

| Boundary | Code | Phase |
| --- | --- | --- |
| capture/payload/readiness validation | `validation_error` | `preflight` or `readiness` |
| unresolved/incompatible manifest or UI package | `dependency_error` | `preflight` or `provisioning` |
| source/upload/download/install byte failure | `transfer_error` | `transfer` |
| refreshed quote/estimate differs | `quote_expired` | `quote` |
| Vast inventory/create transport or provider refusal | `provider_error` | `provider` |
| manifest transaction cannot become valid/ready | `provisioning_error` | `provisioning` |
| Comfy process spawn/health/release failure | `comfy_startup_error` | `readiness` |
| prompt validation/runtime/OOM/interruption | `execution_error` | `execution` |
| snapshot/relay/WebSocket/cursor transport failure | `synchronization_error` | `synchronization` |
| persistent output transfer/publication failure | `harvest_error` | `harvest` |
| malformed or escaping persistent descriptor | `invalid_output` | `harvest` |
| orphaned running record after worker restart | `worker_restart_error` | `execution` |
| deadline/destroy/inventory-absence failure | `lifecycle_error` | `teardown` |
| uncategorized exception | `internal_error` | `internal` |

Each mapping creates one `SafeRunError`, persists one idempotent journal entry with a correlation ID, preserves the original typed cause only in process memory, and exposes only safe code/message/action. Remove public/internal uses of `Remote execution failed.` as a catch-all. OOM may be a safe detail under `execution_error`, never a distinct unbounded provider string.

Run:

```sh
python3 -m unittest \
  tests.python.test_run_errors \
  tests.python.test_lifecycle \
  tests.python.test_session_service \
  tests.python.test_worker_jobs \
  tests.python.test_relay -v
```

Expected: all fourteen codes are produced by at least one concrete boundary, and an unknown seeded exception becomes `internal_error` with no raw text leak.

- [ ] **Step 3: Add an explicit sixteen-finding regression map**

In `test_repository_contract.py`, parse a static map exported by the integration test and require exactly:

| Finding | Automated proof |
| --- | --- |
| 1 quote expiry | `test_quote_change_requires_new_confirmation_and_zero_create` |
| 2 Caddy bearer order | `test_proxy_and_worker_bindings_are_exact_and_loopback_only` |
| 3 nested Desktop loader imports | `LoaderContractTests.test_web_only_exports_and_exact_decorator_routes` plus unchanged `sys.path` assertion |
| 4 competing local backends/db lock | `test_bridge_uses_existing_prompt_server_lifecycle_only` |
| 5 29,347,469,703-byte transfer | readiness-estimate measurement test |
| 6 DEVNULL diagnostics | bounded process diagnostic test |
| 7 ready transaction timeout | local-known transaction recovery tests |
| 8 existing bootstrap directory | reboot-idempotent bootstrap test |
| 9 real execution success | native success state fixture |
| 10 event 94 split-read freeze | atomic 94→158 snapshot/restart test |
| 11 temp preview false failure | SaveImage/PreviewImage history test |
| 12 generic worker errors | all `RunErrorCode` mappings test |
| 13 empty local progress | event-fold payload/UI test |
| 14 confusing offer filters | hard-minimum/preference/cap/reason tests |
| 15 second Run wizard | role-aware UI/no-controller-run test |
| 16 dishonest launch timing | cold/pre-positioned/warm estimate tests |

The contract fails if any row is absent or points to a missing test method.

- [ ] **Step 4: Strengthen no-mutation and no-secret scans**

Extend deterministic AST/text scans to assert:

- only `cloud_run/vast.py` and reviewed deadline own-instance delete surface can call provider APIs;
- desktop relay and Agent bridge contain no account-level credential names in public values;
- web code contains no provider host, bearer, signed URL, `innerHTML`, arbitrary external URL, local/session storage for Cloud Vast secrets, or `Run Vast`;
- native proxy cannot route provider/manager/terminal paths;
- tests monkeypatch network so any unregistered socket/HTTP request fails;
- full gate environment removes provider credential variables before tests.

Extend `LoaderContractTests.test_web_only_exports_and_exact_decorator_routes` to snapshot `tuple(sys.path)` before nested package execution and require it unchanged afterward. Add the new decorator routes to its exact ordered allowlist; do not introduce top-level `cloud_run`/`remote_worker` imports or a global `sys.path` mutation.

- [ ] **Step 5: Run the complete gate twice**

Run:

```sh
scripts/check.sh
scripts/check.sh
git status --short
```

Expected: both runs end `[check] all checks passed`, produce the same worker artifact digest, create no tracked/untracked bytecode/cache files, and show only the intended task changes before commit.

- [ ] **Step 6: Commit**

```sh
git add cloud_run/lifecycle.py cloud_run/vast.py cloud_run/service.py \
  cloud_run/session_service.py cloud_run/relay.py remote_worker/jobs.py \
  remote_worker/provision.py tests/python/test_fake_desktop_bridge_integration.py \
  tests/python/test_fake_session_integration.py \
  tests/python/test_lifecycle.py tests/python/test_loader_and_routes.py \
  tests/python/test_relay.py tests/python/test_run_errors.py \
  tests/python/test_session_service.py tests/python/test_worker_jobs.py \
  tests/python/test_no_mutation_surface.py \
  tests/python/test_repository_contract.py tests/js/comfyui-vast.test.mjs \
  tests/js/session-console.test.mjs scripts/check.sh
git diff --cached --check
git commit -m "test: certify the offline ComfyUI Vast Desktop bridge"
```

**Offline checkpoint E:** this is the end of autonomous runtime implementation. Do not install into the user's live ComfyUI, restart Desktop, build/publish a public worker release, change a Vast template, or contact Vast with a mutating method.

---

### Task 18: Document operation, field acceptance, and the hard external-action stop

**Files:**
- Modify: `README.md`
- Modify: `docs/project-state.md`
- Create: `docs/superpowers/live-tests/2026-08-03-local-desktop-remote-execution-bridge-acceptance.md`
- Modify: `tests/python/test_repository_contract.py`

**Interfaces:**
- Produces: user-readable one-time Desktop setup, lifecycle/run distinction, profile/model source behavior, honest timing, recovery, Agent Panel behavior, Convex/TanStack decision, and an exact non-executing paid field checklist.
- Consumes: final deterministic behavior and the approved design's sixteen paid acceptance criteria.

- [ ] **Step 1: Write failing documentation-contract assertions**

Require README/project state to contain all of:

```text
Cloud Vast
ComfyUI Vast
Louer et préparer
native Run
127.0.0.1
Hugging Face
Civitai
optional R2
Agent Panel remains on the Mac
29,347,469,703
16–20 minutes
no Convex dependency
no TanStack dependency
fresh human GO
```

Require the acceptance document to contain numbered criteria 1 through 16, pre-inventory zero, no external browser, local GPU unused, native Run plus Agent batch, restart/reconnect, second compatible job/delta, secret-free journal, explicit destroy, final inventory zero, and `billing_may_continue=false`.

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
```

Expected: fail until documentation reflects the implemented bridge.

- [ ] **Step 2: Document the normal nontechnical flow**

Explain in plain language:

1. build/open workflow locally;
2. open `Cloud Vast`;
3. review free analysis, source readiness, explained offers, and exact estimate;
4. click one paid `Louer et préparer` confirmation;
5. wait for every readiness check;
6. on first use, create official Remote Connection `ComfyUI Vast` with the displayed stable loopback URL;
7. open it inside Desktop and use ordinary Run/queue/batch/Agent Panel;
8. find verified outputs under `output/cloud-vast/<session>/<job>`;
9. explicitly destroy and wait for inventory-backed billing proof.

State clearly that closing either window does not destroy a pod and that no universal cold 27.3 GiB transfer can honestly be guaranteed below ten minutes.

- [ ] **Step 3: Record architecture and excluded dependencies**

Document SQLite + worker persistent state + atomic snapshots as V1. Convex is only a potential metadata/watchdog adapter and cannot provide exactly-once Vast side effects; workflows/models/previews/outputs and Vast keys never belong there. TanStack adds no value to this existing ComfyUI surface. Compatibility automation for upstream commits remains a separately designed, credential-free PR-opening workflow and is not added to runtime.

- [ ] **Step 4: Write the exact paid field gate without running it**

The acceptance file begins with:

```markdown
Status: NOT AUTHORIZED AND NOT RUN.

This checklist performs no action by itself. Before any create, destroy,
template mutation, worker publication, external install, or paid probe, stop
and obtain a new human GO naming the exact action, offer/rate cap, maximum
instances, maximum duration/cost, and teardown proof.
```

Then copy the spec's sixteen numbered acceptance criteria verbatim in meaning, add evidence fields for commit/release/manifest/profile/session/job/prompt/instance/output digests and timestamps, and ban hot-patching/replacement without another decision.

- [ ] **Step 5: Perform final repository verification**

Before claiming completion, invoke `superpowers:verification-before-completion` and run fresh:

```sh
scripts/check.sh
git diff --check
git status --short --branch
git log --oneline --decorate -20
```

Expected: gate green; no whitespace errors; branch contains only intentional commits; no secret, binary model/image, cache, live release lock, or runtime configuration is tracked.

- [ ] **Step 6: Commit documentation**

```sh
git add README.md docs/project-state.md \
  docs/superpowers/live-tests/2026-08-03-local-desktop-remote-execution-bridge-acceptance.md \
  tests/python/test_repository_contract.py
git diff --cached --check
git commit -m "docs: hand off the ComfyUI Vast field gate"
```

- [ ] **Step 7: Stop and report the external boundary**

Report the exact final commit, deterministic test counts, unpushed branch state, and remaining external actions. Do not push, install, restart Desktop, publish a worker, edit a template, rent, destroy, or run the field checklist. Ask for a new precise GO only after the user has reviewed this completed offline implementation and its evidence.

## Execution checkpoints and recovery rules

- Each task is an independently reviewable commit. If interrupted, run `git status --short`, inspect the last commit, and resume at the first unchecked step; never repeat a provider or external side effect.
- Continue autonomously through offline checkpoints A–E after the user-approved plan. A checkpoint is a local evidence gate, not permission for external mutation.
- If a RED test fails for a reason different from its expected missing behavior, invoke `superpowers:systematic-debugging`, identify the root cause, and update the plan only when the approved product contract would otherwise be violated.
- If the worktree contains unrelated user changes, preserve them and stage path-by-path. If overlap makes a safe edit impossible, stop and report the exact files rather than discard or overwrite work.
- If deterministic tests reveal the official Desktop pinned fixture cannot support the safe relay contract, stop before weakening security or modifying private Desktop state; document the incompatibility for user review.
- If any command would need a provider credential, internet publication, live Desktop process control, external installation, or Vast mutation, stop. The offline implementation authorization does not cover it.

## Definition of offline completion

Offline implementation is complete only when all of the following are true:

- the normal local Desktop and fake `ComfyUI Vast` environments operate independently;
- local Run remains local and every Vast-role prompt reaches only the fake pod;
- native object info, model menus, Run, queue, batch, progress, previews, history, errors, and results traverse the dedicated relay;
- safe profile, background, approved UI packages, and Agent Panel behavior pass their readiness fixtures;
- prompt/delta, event, execution, harvest, profile, and output recovery survive close/reopen and backend reconstruction without duplication;
- all fourteen typed error codes and all sixteen audit findings have deterministic coverage;
- offer, quote, source-readiness, timing, cost, destroy, and inventory contracts remain explicit;
- `scripts/check.sh` passes twice from a clean worktree without secrets or network mutation;
- the field checklist remains marked not authorized/not run;
- no code or document claims that a real Desktop/pod campaign passed before a newly authorized field test supplies that evidence.

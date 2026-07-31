# Workflow-Derived Vast GPU Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn ComfyUI Desktop into the local control plane for exact workflow-derived execution on one reusable, temporary Vast GPU session, with complete offline certification and no local inference.

**Architecture:** The browser invokes the pinned ComfyUI `app.queuePrompt` preparation path while a one-shot adapter captures the resulting `{workflow, output, queue options}` instead of posting to local `/prompt`. The local backend resolves an immutable dependency manifest before any rental, extends the existing inventory-safe Vast lifecycle into durable sessions and jobs, and communicates through an authenticated relay with a versioned Remote Worker. The worker provisions only approved content, validates native ComfyUI, executes the captured prompt, returns events and verified artifacts, and enforces the same absolute deadline as the local orchestrator.

**Tech Stack:** Python 3.13 stdlib plus ComfyUI-provided `aiohttp`, SQLite, browser-native JavaScript ES modules, Node's built-in test runner, Python `unittest`, fixed-argv `git`/`pip` subprocesses, HMAC-SHA256, and a local Git worktree.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes` on `feat/vast-cloud-run-lifecycle`.
- The starting commit is `1918723`; never push the configured ComfyRelay remote.
- Before any runtime change, replace the obsolete official-template-only slice in `AGENTS.md` with the approved workflow-derived session contract.
- Preserve a separate `Cloud Run` launcher beside local `Run/Exécuter`; never patch the local button or send a Cloud Run prompt to local `/prompt`.
- Pin compatibility to ComfyUI Core `0.29.0`, frontend `1.47.10`, and Python `3.13.12`; fail preflight on a different compilation contract.
- Capture the pinned frontend's real `app.queuePrompt` path so `beforeQueued`, seeds, promoted controls, virtual-node `applyToGraph`, async `serializeValue`, subgraphs, bypass/mute, complete workflow serialization, partial targets, and preview options stay authoritative.
- Never infer, translate, or repair a workflow with AI and never run a local reference generation.
- Resolve every executable node, model, input, install artifact, size, immutable revision, SHA-256, and ComfyUI destination before the first provider mutation.
- Use only approved HTTPS origins and immutable identities; fail closed on redirects, mutable branches, unknown sizes, wrong hashes, filename collisions, path traversal, or generated shell.
- Keep Vast, R2, Hugging Face, and Civitai credentials write-only, backend-owned, absent from browser responses, logs, screenshots, fixtures, commits, remote URLs, and worker state.
- A Remote Worker receives only its session material plus Vast-provided `CONTAINER_ID` and `CONTAINER_API_KEY`; it never receives the account-level Vast key.
- V1 supports one sequential job at a time and at least two compatible jobs per session; it creates, attaches, stops, or manages no Vast volume.
- Disk allocation is base environment + custom-node/Python artifacts + unique models + inputs + output allowance + exactly 20 GiB headroom, rounded up, with a minimum of 80 GiB.
- A non-derivable output budget requires an explicit positive user allocation before offer search.
- Initial provisioning and each compatible manifest delta permit one planned ComfyUI restart; one approved repair may use one additional restart for that transaction.
- Ten minutes without meaningful transfer, install, health, or validation progress is stalled.
- Default session duration is two hours, with warnings at 15 and 5 minutes, explicit +30 minute and +1 hour extensions, and a red per-rental acknowledgement for no automatic limit.
- Never use Vast Stop. `Destroy GPU — stop all Vast billing` is the destructive control, and success requires a fresh provider inventory proving both instance ID and managed label absent.
- Host boot failure may replace at most once and only after verified destruction; workflow, OOM, dependency, budget, validation, and provisioning failures never create another rental.
- Automated and autonomous work uses fake/offline providers only. No real GPU, GitHub publication, Registry publication, or paid mutation is authorized by this plan.
- Never commit or publish the user's Gold JPEG or personal workflow. Paid Gold certification remains behind a separate GO naming maximum instances, hourly rate, and absolute duration or cost.
- `/Users/wuraaang/lora-dataset-studio` is read-only at `de697caf9d607a29c72cebdc2794eecd6b147606`; reimplement narrow contracts and never import its application code.
- Do not modify, inspect for reconstruction, import from, or depend on `/Users/wuraaang/comfyui-vast-cockpit`.
- Keep the package web-only: `WEB_DIRECTORY`, empty node mappings, decorator routes under `/cloud-run/api/`, dependency-light metadata, and `textContent` for remote values.
- Follow RED → observe the intended failure → GREEN → focused tests → small local commit for every task.
- `scripts/check.sh` is the final deterministic gate and must remain incapable of reading real secrets, contacting Vast, renting hardware, or publishing anything.

## File Structure

### Existing files retained and evolved

- `AGENTS.md`: authoritative approved slice and safety contract.
- `cloud_run/models.py`: session/quote state, transition rules, browser-safe payloads, and legacy-state mapping.
- `cloud_run/repository.py`: versioned SQLite session schema, legacy `attempts` migration, optimistic writes, and recovery queries.
- `cloud_run/settings.py`: write-only provider/cache/model-source settings and public configured flags.
- `cloud_run/vast.py`: the only account-level Vast HTTP surface.
- `cloud_run/offers.py`: quality policy and exact-offer selection.
- `cloud_run/service.py`: public application facade delegating preflight, session, and job operations.
- `cloud_run/lifecycle.py`: inventory-backed create, boot, replacement, deadline, recovery, and verified destruction.
- `cloud_run/routes.py`: allowlisted same-origin browser routes and startup recovery.
- `web/js/cloud-run.js`: extension registration and composition of the session console.
- `tests/js/fake-dom.mjs`: deterministic DOM primitives used by browser tests.
- `scripts/check.sh`: complete offline, security, route, state, and artifact gate.
- `README.md`, `NOTICE`, `docs/project-state.md`: user contract, provenance, and certified state.

### New local-control-plane files

- `web/js/canvas-adapter.js`: one-shot capture through official `app.queuePrompt`.
- `web/js/cloud-run-api.js`: same-origin JSON client with sanitized errors.
- `web/js/session-console.js`: preflight, quote, progress, jobs, deadline, results, and destroy UI.
- `cloud_run/capture.py`: compiled-payload validation, canonical digest, and durable capture records.
- `cloud_run/manifest.py`: immutable dependency/source/install records, canonical JSON, HMAC signatures, and compatible deltas.
- `cloud_run/dependency_repository.py`: approved mappings, Agent Panel suggestions, manifests, installed sets, and artifact offsets.
- `cloud_run/comfy_host.py`: narrow adapter over local ComfyUI node definitions, module origins, model folders, and input folders.
- `cloud_run/registry.py`: read-only Comfy Registry client restricted to `https://api.comfy.org`.
- `cloud_run/resolver.py`: ordered node/source resolution and preflight assembly.
- `cloud_run/artifacts.py`: path confinement, streaming SHA-256, deterministic package archives, and output/disk estimates.
- `cloud_run/r2.py`: optional Cloudflare R2 SigV4 operations and object-scoped temporary URLs.
- `cloud_run/job_repository.py`: durable jobs, provisioning transactions, installed sets, events, transfer offsets, and local artifact verification.
- `cloud_run/worker_release.py`: reviewed worker/template lock validation and fail-closed live-launch gate.
- `cloud_run/worker_protocol.py`: canonical signed request envelopes shared with the worker.
- `cloud_run/worker_client.py`: authenticated remote calls, event polling, uploads, and resumable output retrieval.
- `cloud_run/session_service.py`: preflight-to-session orchestration and one-job-at-a-time reusable-session policy.
- `cloud_run/relay.py`: event sanitization, preview proxying, output verification, and local history.

### New Remote Worker files

- `remote_worker/__init__.py`: worker package marker and protocol version.
- `remote_worker/bootstrap.py`: fixed immutable GitHub artifact download, digest verification, safe extraction, and worker exec.
- `remote_worker/main.py`: fixed startup entry point.
- `remote_worker/Caddyfile`: Vast bearer boundary on external port 8765 and loopback relay to worker port 8766.
- `remote_worker/server.py`: allowlisted authenticated worker routes.
- `remote_worker/state.py`: private atomic container-local state.
- `remote_worker/transfers.py`: concurrent ranged downloads/uploads, `.part` resume, hash verification, and atomic placement.
- `remote_worker/install.py`: safe archive extraction and fixed-argv Python wheel installation.
- `remote_worker/comfy.py`: internal ComfyUI process, `/object_info`, `/prompt`, WebSocket events, history, and output discovery.
- `remote_worker/provision.py`: manifest transaction, installed-set delta, validation, bounded repair, restart budgets, and stall watchdog.
- `remote_worker/jobs.py`: single-job execution and durable event/artifact records.
- `remote_worker/deadline.py`: signed deadline updates and own-instance destruction only.
- `remote_worker/template-policy.json`: reviewed base-template/version/port policy without a live project-template ID.
- `scripts/build_worker_artifact.py`: deterministic public worker archive builder and SHA-256 emitter.
- `scripts/validate_gold_output.py`: offline-testable structural validator used by the separately authorized Gold procedure.

### New tests

- `tests/js/canvas-adapter.test.mjs`
- `tests/js/session-console.test.mjs`
- `tests/python/test_capture.py`
- `tests/python/test_manifest.py`
- `tests/python/test_dependency_repository.py`
- `tests/python/test_comfy_host.py`
- `tests/python/test_registry.py`
- `tests/python/test_resolver.py`
- `tests/python/test_artifacts.py`
- `tests/python/test_r2.py`
- `tests/python/test_job_repository.py`
- `tests/python/test_worker_release.py`
- `tests/python/test_worker_bootstrap.py`
- `tests/python/test_worker_protocol.py`
- `tests/python/test_worker_server.py`
- `tests/python/test_worker_transfers.py`
- `tests/python/test_worker_install.py`
- `tests/python/test_worker_provision.py`
- `tests/python/test_worker_jobs.py`
- `tests/python/test_worker_deadline.py`
- `tests/python/test_worker_client.py`
- `tests/python/test_relay.py`
- `tests/python/test_session_service.py`
- `tests/python/test_fake_session_integration.py`
- `tests/python/test_gold_output_validation.py`

---

### Task 0: Adopt the approved slice contract before runtime work

**Files:**
- Modify: `tests/python/test_repository_contract.py`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the approved design in `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`.
- Produces: an authoritative `AGENTS.md` contract that permits only the planned workflow-derived session work.

- [ ] **Step 1: Write the failing repository-contract test**

Add:

```python
def test_agents_authorizes_only_the_workflow_derived_session_slice(self):
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    self.assertIn("## Current slice: workflow-derived Vast GPU sessions", text)
    self.assertIn("capture the exact prompt compiled by the pinned frontend", text)
    self.assertIn("resolve every dependency before the first paid mutation", text)
    self.assertIn("one sequential job at a time", text)
    self.assertIn("no Vast volume", text)
    self.assertIn("Destroy GPU — stop all Vast billing", text)
    self.assertNotIn("No workflow transfer,", text)
```

- [ ] **Step 2: Run the test and verify the contract is still red**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract.RepositoryContractTests.test_agents_authorizes_only_the_workflow_derived_session_slice -v
```

Expected: FAIL because `AGENTS.md` still declares the official-template-only slice.

- [ ] **Step 3: Replace only the obsolete slice section**

Replace `## Current slice: safe Vast.ai lifecycle` through its final scope paragraph with:

```markdown
## Current slice: workflow-derived Vast GPU sessions

Extend the certified Vast lifecycle into a local ComfyUI Desktop control plane
for temporary remote GPU sessions:

- capture the exact prompt compiled by the pinned frontend without posting it
  to local `/prompt` or running a local reference generation;
- resolve every dependency before the first paid mutation, with immutable
  custom-node revisions and exact artifact sizes and SHA-256 digests;
- rent one explicitly confirmed ephemeral Vast instance as a reusable session;
- provision and validate the repository-owned Remote Worker and native ComfyUI;
- execute one sequential job at a time and support compatible manifest deltas;
- relay progress, previews, errors, history, and verified outputs locally;
- enforce the finite deadline locally and from the worker;
- create no Vast volume and never use Stop as a billing terminal action;
- expose `Destroy GPU — stop all Vast billing` with strengthened confirmation
  and fresh-inventory absence verification.

The project template remains fail-closed until its public worker artifact,
bootstrap, authentication, deadline enforcement, and teardown have passed the
offline gate and an immutable release lock is reviewed. Automated work uses
fake providers only; live rental and publication require separate human
authorization.
```

- [ ] **Step 4: Re-run the focused contract and baseline gate**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
scripts/check.sh
```

Expected: repository-contract tests pass, then the unchanged baseline reports 89 Python and 18 Node tests passing.

- [ ] **Step 5: Commit the contract change**

```sh
git add AGENTS.md tests/python/test_repository_contract.py
git commit -m "docs: adopt workflow-derived Vast session slice"
```

---

### Task 1: Introduce durable session, job, manifest, and artifact state

**Files:**
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/repository.py`
- Create: `cloud_run/job_repository.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_repository.py`
- Create: `tests/python/test_job_repository.py`

**Interfaces:**
- Consumes: `OfferQuote`, optimistic `version`, private SQLite permissions, and legacy rows in `attempts`.
- Produces: `SessionState`, `JobState`, `TransferState`, `CloudSession`, `CloudJob`, `SessionRepository`, and `JobRepository`.

- [ ] **Step 1: Write failing state and transition tests**

Add these tests:

```python
def test_session_and_job_state_contracts_are_exact(self):
    from cloud_run.models import JobState, SessionState, TransferState

    self.assertEqual(
        {state.value for state in SessionState},
        {
            "preflight", "offer_selected", "confirming", "creating",
            "bootstrapping", "provisioning", "validating", "ready",
            "running", "harvesting", "repairing", "destroy_requested",
            "destroying", "destroyed", "failed",
        },
    )
    self.assertEqual(
        {state.value for state in JobState},
        {"captured", "resolving", "queued", "running", "harvesting",
         "succeeded", "failed"},
    )
    self.assertEqual(
        {state.value for state in TransferState},
        {"pending", "transferring", "verified", "failed", "abandoned"},
    )

def test_execution_failure_returns_a_healthy_session_to_ready(self):
    session = cloud_session(state=SessionState.RUNNING)
    saved = session.transition(SessionState.READY, now=20.0)
    self.assertEqual(saved.state, SessionState.READY)
    with self.assertRaises(InvalidStateTransition):
        saved.transition(SessionState.CREATING)
```

- [ ] **Step 2: Run the model tests and verify missing types fail**

Run:

```sh
python3 -m unittest tests.python.test_models -v
```

Expected: FAIL with an import error for `SessionState`.

- [ ] **Step 3: Add exact states, legacy mapping, and bounded transitions**

Implement:

```python
class SessionState(str, Enum):
    PREFLIGHT = "preflight"
    OFFER_SELECTED = "offer_selected"
    CONFIRMING = "confirming"
    CREATING = "creating"
    BOOTSTRAPPING = "bootstrapping"
    PROVISIONING = "provisioning"
    VALIDATING = "validating"
    READY = "ready"
    RUNNING = "running"
    HARVESTING = "harvesting"
    REPAIRING = "repairing"
    DESTROY_REQUESTED = "destroy_requested"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    FAILED = "failed"


class JobState(str, Enum):
    CAPTURED = "captured"
    RESOLVING = "resolving"
    QUEUED = "queued"
    RUNNING = "running"
    HARVESTING = "harvesting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TransferState(str, Enum):
    PENDING = "pending"
    TRANSFERRING = "transferring"
    VERIFIED = "verified"
    FAILED = "failed"
    ABANDONED = "abandoned"


LEGACY_SESSION_STATES = {
    "idle": SessionState.PREFLIGHT,
    "searching": SessionState.PREFLIGHT,
    "offer_selected": SessionState.OFFER_SELECTED,
    "confirming": SessionState.CONFIRMING,
    "creating": SessionState.CREATING,
    "starting": SessionState.BOOTSTRAPPING,
    "cancel_requested": SessionState.DESTROY_REQUESTED,
    "destroying": SessionState.DESTROYING,
    "retrying": SessionState.CREATING,
    "ready": SessionState.READY,
    "cancelled": SessionState.DESTROYED,
    "failed": SessionState.FAILED,
}
```

Use this transition map:

```python
SESSION_TRANSITIONS = {
    SessionState.PREFLIGHT: {
        SessionState.OFFER_SELECTED, SessionState.DESTROYED, SessionState.FAILED,
    },
    SessionState.OFFER_SELECTED: {
        SessionState.PREFLIGHT, SessionState.CONFIRMING,
        SessionState.DESTROYED, SessionState.FAILED,
    },
    SessionState.CONFIRMING: {
        SessionState.CREATING, SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.CREATING: {
        SessionState.BOOTSTRAPPING, SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.BOOTSTRAPPING: {
        SessionState.PROVISIONING, SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING, SessionState.FAILED,
    },
    SessionState.PROVISIONING: {
        SessionState.VALIDATING, SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED, SessionState.FAILED,
    },
    SessionState.VALIDATING: {
        SessionState.READY, SessionState.REPAIRING,
        SessionState.DESTROY_REQUESTED, SessionState.FAILED,
    },
    SessionState.REPAIRING: {
        SessionState.PROVISIONING, SessionState.VALIDATING,
        SessionState.DESTROY_REQUESTED, SessionState.FAILED,
    },
    SessionState.READY: {
        SessionState.PROVISIONING, SessionState.RUNNING,
        SessionState.DESTROY_REQUESTED, SessionState.FAILED,
    },
    SessionState.RUNNING: {
        SessionState.HARVESTING, SessionState.READY,
        SessionState.DESTROY_REQUESTED, SessionState.FAILED,
    },
    SessionState.HARVESTING: {
        SessionState.READY, SessionState.DESTROY_REQUESTED,
        SessionState.FAILED,
    },
    SessionState.DESTROY_REQUESTED: {
        SessionState.DESTROYING, SessionState.FAILED,
    },
    SessionState.DESTROYING: {
        SessionState.DESTROYED, SessionState.CREATING, SessionState.FAILED,
    },
    SessionState.FAILED: {
        SessionState.REPAIRING, SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING, SessionState.CREATING,
    },
    SessionState.DESTROYED: set(),
}
```

Define the durable records with these exact fields:

```python
@dataclass(frozen=True)
class CloudSession:
    session_id: str
    idempotency_key: str
    label: str
    state: SessionState
    quote: OfferQuote | None
    manifest_digest: str | None
    installed_manifest_digest: str | None
    instance_id: str | None
    worker_base_url: str | None
    provider_token: str | None
    session_secret_hex: str | None
    deadline_at: float | None
    deadline_mode: str
    disk_gb: int
    retry_count: int
    destroy_requested: bool
    residual_inventory: tuple[str, ...]
    sanitized_error: str | None
    created_at: float
    updated_at: float
    version: int


@dataclass(frozen=True)
class CloudJob:
    job_id: str
    session_id: str
    idempotency_key: str
    state: JobState
    prompt_digest: str
    capture_json: str
    manifest_digest: str
    remote_prompt_id: str | None
    sanitized_error: str | None
    created_at: float
    updated_at: float
    version: int
```

`CloudSession.public_payload()` must omit `provider_token`, `session_secret_hex`, local paths, manifest JSON, and secret handles.

- [ ] **Step 4: Write failing persistence and migration tests**

Create a current-schema database, then assert:

```python
def test_legacy_attempt_is_migrated_without_losing_billing_identity(self):
    create_legacy_attempt_database(self.path, state="starting", instance_id="77")
    repository = SessionRepository(self.path)
    session = repository.get("attempt-1")
    self.assertEqual(session.state, SessionState.BOOTSTRAPPING)
    self.assertEqual(session.instance_id, "77")
    self.assertEqual(session.label, "comfy-cloud-run-attempt-1")

def test_job_manifest_event_and_transfer_offsets_survive_reopen(self):
    jobs = JobRepository(self.path)
    jobs.save_manifest("a" * 64, '{"schema_version":1}')
    jobs.create_job(cloud_job(job_id="job-1", session_id="session-1"))
    jobs.append_event("job-1", 1, "progress", {"value": 2, "max": 10})
    jobs.save_transfer(
        job_id="job-1", artifact_id="output-1", direction="download",
        expected_size=10, sha256="b" * 64, offset=4,
        state=TransferState.TRANSFERRING, private_path="/private/output.part",
    )
    reopened = JobRepository(self.path)
    self.assertEqual(reopened.get_transfer("job-1", "output-1").offset, 4)
    self.assertEqual(reopened.list_events("job-1", after_sequence=0)[0].sequence, 1)

def test_provision_restart_repair_and_installed_sets_survive_reopen(self):
    jobs = JobRepository(self.path)
    jobs.create_provision_transaction(
        transaction_id="tx-1", session_id="session-1", job_id="job-1",
        manifest_digest="a" * 64, state="repairing",
        planned_restart_count=1, repair_count=1,
        repair_restart_count=1, last_progress_at=50.0,
    )
    jobs.replace_installed_set(
        "session-1",
        [{"dependency_id": "model-a", "digest": "b" * 64,
          "revision": None, "destination": "models/a"}],
    )
    reopened = JobRepository(self.path)
    transaction = reopened.get_provision_transaction("tx-1")
    self.assertEqual(
        (transaction.planned_restart_count, transaction.repair_count,
         transaction.repair_restart_count),
        (1, 1, 1),
    )
    self.assertEqual(reopened.installed_set("session-1")[0].digest, "b" * 64)
```

- [ ] **Step 5: Run persistence tests and verify schema failures**

Run:

```sh
python3 -m unittest tests.python.test_repository tests.python.test_job_repository -v
```

Expected: FAIL because session migration and job tables do not exist.

- [ ] **Step 6: Add schema version 2 and focused repositories**

`SessionRepository` must open with `BEGIN IMMEDIATE`, create `schema_meta`, migrate legacy rows transactionally, and create these columns/tables:

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    quote_json TEXT,
    manifest_digest TEXT,
    installed_manifest_digest TEXT,
    instance_id TEXT,
    worker_base_url TEXT,
    provider_token TEXT,
    session_secret_hex TEXT,
    deadline_at REAL,
    deadline_mode TEXT NOT NULL DEFAULT 'finite',
    disk_gb INTEGER NOT NULL DEFAULT 80,
    retry_count INTEGER NOT NULL DEFAULT 0,
    destroy_requested INTEGER NOT NULL DEFAULT 0,
    residual_inventory_json TEXT,
    sanitized_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    prompt_digest TEXT NOT NULL,
    capture_json TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    remote_prompt_id TEXT,
    sanitized_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    version INTEGER NOT NULL,
    UNIQUE(session_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS manifests (
    manifest_digest TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    job_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(job_id, sequence)
);

CREATE TABLE IF NOT EXISTS transfers (
    job_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    expected_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    offset INTEGER NOT NULL,
    state TEXT NOT NULL,
    private_path TEXT NOT NULL,
    PRIMARY KEY(job_id, artifact_id)
);

CREATE TABLE IF NOT EXISTS provision_transactions (
    transaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    job_id TEXT,
    manifest_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    planned_restart_count INTEGER NOT NULL,
    repair_count INTEGER NOT NULL,
    repair_restart_count INTEGER NOT NULL,
    last_progress_at REAL NOT NULL,
    sanitized_error TEXT
);

CREATE TABLE IF NOT EXISTS installed_dependencies (
    session_id TEXT NOT NULL,
    dependency_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    revision TEXT,
    destination TEXT NOT NULL,
    PRIMARY KEY(session_id, dependency_id)
);
```

Keep database/directory modes `0600`/`0700`, JSON deterministic, parameterized SQL only, and optimistic `WHERE version = ?` updates.

- [ ] **Step 7: Run focused tests and the baseline regression suite**

Run:

```sh
python3 -m unittest tests.python.test_models tests.python.test_repository tests.python.test_job_repository tests.python.test_fake_lifecycle_integration -v
```

Expected: PASS, including migration and the existing fake lifecycle.

- [ ] **Step 8: Commit the durable domain**

```sh
git add cloud_run/models.py cloud_run/repository.py cloud_run/job_repository.py \
  tests/python/test_models.py tests/python/test_repository.py \
  tests/python/test_job_repository.py
git commit -m "feat: persist reusable Cloud Run sessions and jobs"
```

---

### Task 2: Capture the official ComfyUI queue payload without local execution

**Files:**
- Create: `web/js/canvas-adapter.js`
- Create: `tests/js/canvas-adapter.test.mjs`
- Modify: `web/js/cloud-run.js`
- Modify: `tests/js/cloud-run-ui.test.mjs`

**Interfaces:**
- Consumes: pinned frontend `app.queuePrompt(number, batchCount, queueNodeIds?)` and `api.queuePrompt(number, {workflow, output}, {partialExecutionTargets, previewMethod})`.
- Produces: `captureOfficialQueuePayload({ app, api, number=0, queueNodeIds }) -> Promise<{workflow, output, queue_options}>`.

- [ ] **Step 1: Write a failing semantic-equivalence test**

Create a fake official queue pipeline whose internals record every required semantic:

```javascript
test("captures through official queue preparation and never calls local prompt", async () => {
  const trace = [];
  const api = {
    async queuePrompt(number, data, options) {
      trace.push(["local-api", number, data, options]);
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const app = {
    async queuePrompt(number, batchCount, queueNodeIds) {
      trace.push("beforeQueued");
      trace.push("promoted-beforeQueued");
      trace.push("virtual-applyToGraph");
      await Promise.resolve();
      trace.push("async-serializeValue");
      const data = {
        workflow: { version: 1, nodes: [{ id: 1 }] },
        output: { "1": { class_type: "KSampler", inputs: { seed: 44 } } },
      };
      const result = await api.queuePrompt(number, data, {
        partialExecutionTargets: queueNodeIds,
        previewMethod: "latent2rgb",
      });
      trace.push("afterQueued");
      trace.push("promoted-afterQueued");
      assert.equal(result.prompt_id, null);
      return true;
    },
  };

  const captured = await captureOfficialQueuePayload({
    app, api, number: -1, queueNodeIds: ["1"],
  });

  assert.deepEqual(trace, [
    "beforeQueued", "promoted-beforeQueued", "virtual-applyToGraph",
    "async-serializeValue", "afterQueued", "promoted-afterQueued",
  ]);
  assert.equal(captured.output["1"].inputs.seed, 44);
  assert.deepEqual(captured.queue_options, {
    front: true,
    partial_execution_targets: ["1"],
    preview_method: "latent2rgb",
  });
});
```

Also add:

```javascript
test("restores local Run before a queued local submission and after errors", async () => {
  const local = [];
  const api = {
    async queuePrompt(number, data) {
      local.push([number, data.output]);
      return { prompt_id: "real-local", node_errors: {} };
    },
  };
  const original = api.queuePrompt;
  const app = {
    async queuePrompt() {
      await api.queuePrompt(0, {
        workflow: { nodes: [] },
        output: { cloud: { class_type: "Cloud", inputs: {} } },
      }, {});
      await api.queuePrompt(0, {
        workflow: { nodes: [] },
        output: { local: { class_type: "Local", inputs: {} } },
      }, {});
      return true;
    },
  };

  await captureOfficialQueuePayload({ app, api });
  assert.strictEqual(api.queuePrompt, original);
  assert.deepEqual(local, [[0, { local: { class_type: "Local", inputs: {} } }]]);
});
```

- [ ] **Step 2: Run the adapter test and verify the module is missing**

Run:

```sh
node --test tests/js/canvas-adapter.test.mjs
```

Expected: FAIL because `web/js/canvas-adapter.js` does not exist.

- [ ] **Step 3: Implement the one-shot official queue adapter**

Create:

```javascript
let captureInFlight = false;

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

export async function captureOfficialQueuePayload({
  app,
  api,
  number = 0,
  queueNodeIds,
}) {
  if (!app || typeof app.queuePrompt !== "function") {
    throw new Error("Pinned ComfyUI queue API is unavailable.");
  }
  if (!api || typeof api.queuePrompt !== "function") {
    throw new Error("Pinned ComfyUI prompt API is unavailable.");
  }
  if (captureInFlight) {
    throw new Error("A Cloud Run canvas capture is already in progress.");
  }

  captureInFlight = true;
  const original = api.queuePrompt;
  let captured = null;

  async function intercept(queueNumber, data, options = {}) {
    if (api.queuePrompt === intercept) api.queuePrompt = original;
    captured = {
      workflow: cloneJson(data.workflow),
      output: cloneJson(data.output),
      queue_options: {
        ...(queueNumber === -1 ? { front: true } : {}),
        ...(queueNumber !== 0 && queueNumber !== -1
          ? { number: queueNumber }
          : {}),
        ...(options.partialExecutionTargets?.length
          ? {
              partial_execution_targets: cloneJson(
                options.partialExecutionTargets,
              ),
            }
          : {}),
        ...(options.previewMethod && options.previewMethod !== "default"
          ? { preview_method: String(options.previewMethod) }
          : {}),
      },
    };
    return { prompt_id: null, node_errors: {} };
  }

  api.queuePrompt = intercept;
  try {
    await app.queuePrompt(number, 1, queueNodeIds);
    if (!captured) {
      throw new Error("ComfyUI was busy; no Cloud Run payload was captured.");
    }
    return captured;
  } finally {
    if (api.queuePrompt === intercept) api.queuePrompt = original;
    captureInFlight = false;
  }
}
```

Do not call `app.graphToPrompt` directly and do not copy ComfyUI's traversal or serialization implementation into this repository.

- [ ] **Step 4: Wire only the Cloud Run action to capture**

Import `captureOfficialQueuePayload` from `cloud-run.js`. Pass `app` and `api` from `registerCloudRunWhenReady` into `mountCloudRun`; keep the local queue button, command, listeners, and `api.fetchApi` untouched. On a Cloud Run click, capture once and pass the payload to the console callback. If the official queue API is busy or incompatible, render the sanitized adapter error and make no backend or provider mutation.

- [ ] **Step 5: Run adapter and existing launcher tests**

Run:

```sh
node --test tests/js/canvas-adapter.test.mjs tests/js/cloud-run-ui.test.mjs
```

Expected: PASS; the existing 18 launcher/lifecycle tests remain green, the local API spy receives no Cloud capture, and the original `api.queuePrompt` identity is restored.

- [ ] **Step 6: Commit the canvas adapter**

```sh
git add web/js/canvas-adapter.js web/js/cloud-run.js \
  tests/js/canvas-adapter.test.mjs tests/js/cloud-run-ui.test.mjs
git commit -m "feat: capture the official ComfyUI queue payload"
```

---

### Task 3: Validate and persist compiled captures at the local boundary

**Files:**
- Create: `cloud_run/capture.py`
- Create: `tests/python/test_capture.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Create: `web/js/cloud-run-api.js`
- Modify: `web/js/cloud-run.js`

**Interfaces:**
- Consumes: `{workflow, output, queue_options}` from Task 2 and `JobRepository`.
- Produces: `CompiledCapture.from_payload(payload)`, `prompt_digest`, `POST /cloud-run/api/captures`, and browser-safe `{capture_id, prompt_digest, status:"captured"}`.

- [ ] **Step 1: Write failing validation and digest tests**

```python
def test_capture_accepts_native_shape_and_hashes_only_execution_material(self):
    first = CompiledCapture.from_payload({
        "workflow": {"nodes": [{"id": 1}], "extra": {"frontendVersion": "1.47.10"}},
        "output": {"1": {"class_type": "KSampler", "inputs": {"seed": 7}}},
        "queue_options": {"preview_method": "latent2rgb"},
    })
    second = CompiledCapture.from_payload({
        "workflow": {"nodes": [{"id": 1, "pos": [99, 99]}],
                     "extra": {"frontendVersion": "1.47.10"}},
        "output": {"1": {"inputs": {"seed": 7}, "class_type": "KSampler"}},
        "queue_options": {"preview_method": "latent2rgb"},
    })
    self.assertEqual(first.prompt_digest, second.prompt_digest)
    self.assertEqual(first.executable_class_types, ("KSampler",))

def test_capture_rejects_wrong_frontend_secrets_and_oversized_payload(self):
    for payload in (
        native_capture(frontend_version="1.47.11"),
        native_capture(extra={"auth_token_comfy_org": "forbidden"}),
        native_capture(output={"1": {"class_type": "../bad", "inputs": {}}}),
    ):
        with self.assertRaises(CaptureValidationError):
            CompiledCapture.from_payload(payload)
```

- [ ] **Step 2: Run capture tests and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_capture -v
```

Expected: FAIL because `cloud_run.capture` does not exist.

- [ ] **Step 3: Implement strict capture parsing and canonical digest**

Use:

```python
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
ALLOWED_QUEUE_OPTIONS = {
    "front", "number", "partial_execution_targets", "preview_method",
}
FORBIDDEN_KEYS = {
    "auth_token_comfy_org", "api_key_comfy_org", "api_key",
    "authorization", "bearer", "signed_url",
}


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True,
    )


def prompt_digest(output, queue_options):
    material = canonical_json({
        "output": output,
        "queue_options": queue_options,
    }).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
```

`CompiledCapture.from_payload` must:

1. require exactly `workflow`, `output`, and `queue_options`;
2. canonicalize to at most `MAX_CAPTURE_BYTES`;
3. require workflow `extra.frontendVersion == "1.47.10"`;
4. require non-empty node IDs, `class_type`, and object `inputs`;
5. allow only `ALLOWED_QUEUE_OPTIONS`;
6. recursively reject every case-folded `FORBIDDEN_KEYS` member;
7. sort unique executable class types;
8. generate a UUID capture ID without trusting a browser ID.

- [ ] **Step 4: Write the failing route test**

```python
def test_capture_route_persists_native_payload_without_provider_call(self):
    service = FakeService()
    handlers = captured_handlers(lambda: service)
    response = asyncio.run(handlers[("POST", "/cloud-run/api/captures")](
        FakeRequest(payload=native_capture())
    ))
    self.assertEqual(response.status, 200)
    self.assertEqual(response.payload["status"], "captured")
    self.assertNotIn("workflow", response.payload)
    self.assertNotIn("output", response.payload)
    self.assertEqual(service.provider_mutations, [])
```

- [ ] **Step 5: Register the route and same-origin client**

Add:

```python
@routes.post("/cloud-run/api/captures")
async def post_capture(request):
    try:
        payload = await _request_payload(
            request,
            allowed={"workflow", "output", "queue_options"},
            required={"workflow", "output", "queue_options"},
        )
        capture = await make_service().capture(payload)
    except Exception as error:
        return service_error(error)
    return web.json_response({
        "capture_id": capture.capture_id,
        "prompt_digest": capture.prompt_digest,
        "status": "captured",
    })
```

Create `postCapture(fetchImpl, capture)` in `web/js/cloud-run-api.js` using the existing sanitized `fetchJson` behavior and only `/cloud-run/api/captures`.

- [ ] **Step 6: Run focused backend and frontend tests**

Run:

```sh
python3 -m unittest tests.python.test_capture tests.python.test_routes tests.python.test_no_mutation_surface -v
node --test tests/js/canvas-adapter.test.mjs tests/js/cloud-run-ui.test.mjs
```

Expected: PASS; route allowlist includes captures and no provider mutation is observed.

- [ ] **Step 7: Commit compiled capture persistence**

```sh
git add cloud_run/capture.py cloud_run/routes.py web/js/cloud-run-api.js \
  web/js/cloud-run.js tests/python/test_capture.py \
  tests/python/test_routes.py tests/python/test_no_mutation_surface.py
git commit -m "feat: persist native Cloud Run captures"
```

---

### Task 4: Define immutable dependency manifests and approved mappings

**Files:**
- Create: `cloud_run/manifest.py`
- Create: `cloud_run/dependency_repository.py`
- Create: `tests/python/test_manifest.py`
- Create: `tests/python/test_dependency_repository.py`

**Interfaces:**
- Consumes: `prompt_digest`, pinned host versions, private SQLite.
- Produces: `SourceSpec`, `ArtifactSpec`, `PythonWheelSpec`, `CustomNodeSpec`, `DependencyManifest`, `ManifestDelta`, and mapping precedence records.

- [ ] **Step 1: Write failing manifest validation tests**

```python
def test_manifest_is_canonical_content_addressed_and_browser_safe(self):
    manifest = dependency_manifest(
        artifacts=(
            artifact("model", "models/upscale/a.safetensors", 12, "a" * 64),
            artifact("input", "input/source.jpg", 7, "b" * 64),
        ),
    )
    encoded = manifest.canonical_bytes()
    self.assertEqual(
        manifest.digest,
        hashlib.sha256(encoded).hexdigest(),
    )
    self.assertNotIn(b"/Users/", encoded)
    self.assertNotIn(b"token", encoded.casefold())

def test_manifest_rejects_mutable_or_unbounded_dependencies(self):
    invalid = (
        source("git", url="https://github.com/acme/nodes", revision="main"),
        artifact("model", "../escape", 1, "a" * 64),
        artifact("model", "models/a", 0, "a" * 64),
        artifact("model", "models/a", 1, "not-a-sha"),
        source("http", url="http://example.com/a", revision="a" * 40),
    )
    for value in invalid:
        with self.assertRaises(ManifestValidationError):
            validate_dependency(value)
```

- [ ] **Step 2: Run manifest tests and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_manifest -v
```

Expected: FAIL because the immutable manifest types do not exist.

- [ ] **Step 3: Implement canonical immutable records**

Use frozen dataclasses with these exact public fields:

```python
@dataclass(frozen=True)
class SourceSpec:
    kind: str
    locator: str
    immutable_revision: str | None = None
    secret_handle: str | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    artifact_id: str
    kind: str
    logical_name: str
    destination: str
    size_bytes: int
    sha256: str
    source: SourceSpec


@dataclass(frozen=True)
class PythonWheelSpec:
    filename: str
    size_bytes: int
    sha256: str
    source: SourceSpec


@dataclass(frozen=True)
class CustomNodeSpec:
    package_id: str
    repository_url: str
    revision: str
    archive: ArtifactSpec
    wheels: tuple[PythonWheelSpec, ...]
    provided_class_types: tuple[str, ...]


@dataclass(frozen=True)
class DependencyManifest:
    schema_version: int
    protocol_version: str
    comfyui_core_version: str
    comfyui_frontend_version: str
    worker_version: str
    prompt_digest: str
    custom_nodes: tuple[CustomNodeSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]
    output_allowance_bytes: int
    disk_gb: int
```

Validation must allow only:

- GitHub repository URLs normalized to `https://github.com/<owner>/<repo>`;
- 40-character lowercase Git revisions;
- Hugging Face artifacts pinned under `/resolve/<40-hex-commit>/`;
- numeric Civitai model-version IDs;
- configured R2 bucket/key locators;
- `local-upload:<artifact_id>` locators;
- relative POSIX destinations rooted under `custom_nodes/`, `models/`, or `input/`.

Sort all records by stable identity before canonical JSON. `ManifestDelta` must mark a changed revision for an installed package or a changed digest at an installed destination as incompatible.

- [ ] **Step 4: Write failing mapping precedence and approval tests**

```python
def test_mapping_precedence_is_approved_registry_git_agent_then_manual(self):
    repository = DependencyRepository(self.path)
    repository.save_candidate("NodeA", "agent", candidate("agent"), approved=False)
    repository.save_candidate("NodeA", "installed_git", candidate("git"), approved=False)
    repository.save_candidate("NodeA", "registry", candidate("registry"), approved=False)
    repository.save_candidate("NodeA", "manual", candidate("manual"), approved=False)
    self.assertEqual(repository.candidates("NodeA")[0].source_kind, "registry")

    repository.approve("NodeA", candidate("approved"))
    self.assertEqual(repository.candidates("NodeA")[0].source_kind, "approved")

def test_agent_suggestion_never_becomes_approved_or_contains_commands(self):
    repository = DependencyRepository(self.path)
    with self.assertRaises(MappingValidationError):
        repository.save_candidate(
            "NodeA", "agent",
            {"repository_url": "https://github.com/a/b", "revision": "a" * 40,
             "shell": "curl secret | sh"},
            approved=False,
        )
```

- [ ] **Step 5: Implement private mapping persistence**

Create tables `dependency_mappings` and `dependency_candidates`. Enforce precedence numerically:

```python
MAPPING_PRECEDENCE = {
    "approved": 0,
    "registry": 1,
    "installed_git": 2,
    "agent": 3,
    "manual": 4,
}
```

Only an explicit `approve(class_type, candidate)` call writes `approved=1`; Registry, installed Git, Agent Panel, and manual candidates remain pending until their immutable plan passes validation and the user approves it. Reject keys outside the typed candidate schema, including `shell`, `command`, `env`, `script`, and secret values.

- [ ] **Step 6: Run focused tests**

Run:

```sh
python3 -m unittest tests.python.test_manifest tests.python.test_dependency_repository -v
```

Expected: PASS with deterministic manifest digests and exact precedence.

- [ ] **Step 7: Commit manifest and mapping contracts**

```sh
git add cloud_run/manifest.py cloud_run/dependency_repository.py \
  tests/python/test_manifest.py tests/python/test_dependency_repository.py
git commit -m "feat: define immutable Cloud Run dependency manifests"
```

---

### Task 5: Resolve core and custom nodes in the required order

**Files:**
- Create: `cloud_run/comfy_host.py`
- Create: `cloud_run/registry.py`
- Create: `cloud_run/resolver.py`
- Create: `tests/python/test_comfy_host.py`
- Create: `tests/python/test_registry.py`
- Create: `tests/python/test_resolver.py`

**Interfaces:**
- Consumes: capture class types, local `nodes.NODE_CLASS_MAPPINGS`, Comfy Registry, and Task 4 mappings.
- Produces: `ComfyHost.describe_node(class_type)`, `RegistryClient.infer_package(class_type)`, and `DependencyResolver.resolve_nodes(capture)`.

- [ ] **Step 1: Write failing local-host classification tests**

```python
def test_core_and_custom_node_paths_are_classified_without_importing_code(self):
    host = ComfyHost(
        comfy_root=Path("/safe/ComfyUI"),
        custom_nodes_root=Path("/safe/ComfyUI/custom_nodes"),
        node_records={
            "KSampler": fake_node("/safe/ComfyUI/nodes.py", "nodes"),
            "Fancy": fake_node(
                "/safe/ComfyUI/custom_nodes/Fancy/__init__.py",
                "custom_nodes.Fancy",
            ),
        },
        version_reader=lambda: ("0.29.0", "1.47.10", "3.13.12"),
    )
    self.assertEqual(host.describe_node("KSampler").kind, "core")
    self.assertEqual(host.describe_node("Fancy").kind, "custom")

def test_wrong_host_version_symlink_escape_and_forbidden_tree_fail_closed(self):
    with self.assertRaises(HostCompatibilityError):
        fake_host(frontend="1.47.11").assert_compatible()
    with self.assertRaises(HostCompatibilityError):
        fake_host(node_path="/Users/wuraaang/comfyui-vast-cockpit/x.py").describe_node("X")
```

- [ ] **Step 2: Implement the narrow host adapter**

`ComfyHost` must lazily import ComfyUI modules only inside the running host, resolve real paths, refuse anything outside the Comfy root, explicitly refuse the forbidden cockpit path, and execute only these read-only Git argv forms:

```python
("git", "-C", package_root, "config", "--get", "remote.origin.url")
("git", "-C", package_root, "rev-parse", "HEAD")
("git", "-C", package_root, "status", "--porcelain", "--untracked-files=no")
```

A dirty tracked package is `mapping_required`; a clean GitHub package with a 40-hex HEAD is an `installed_git` candidate. Core means a source file inside the pinned Comfy root and outside `custom_nodes`.

- [ ] **Step 3: Write failing Registry request tests**

```python
def test_registry_infers_node_then_reads_an_immutable_release(self):
    session = FakeSession([
        FakeResponse(200, {"id": "acme.fancy", "repository": "https://github.com/acme/fancy"}),
        FakeResponse(200, [{"version": "1.2.3", "status": "NodeVersionStatusActive",
                            "git_commit": "a" * 40}]),
    ])
    result = asyncio.run(RegistryClient(session=session).infer_package("Fancy"))
    self.assertEqual(session.urls, [
        "https://api.comfy.org/comfy-nodes/Fancy/node",
        "https://api.comfy.org/nodes/acme.fancy/versions",
    ])
    self.assertEqual(result.revision, "a" * 40)
```

- [ ] **Step 4: Implement a read-only, sanitized Registry client**

Use only `GET` against `https://api.comfy.org`, percent-encode path components, a 30-second total timeout, no authentication, no redirects to a different origin, response-size bounds, and sanitized `RegistryError` messages. A Registry response is usable only when it resolves to a GitHub repository and a release with a 40-hex commit.

- [ ] **Step 5: Write failing resolver-order tests**

```python
def test_resolver_uses_exact_order_and_blocks_unknown_nodes(self):
    resolver = resolver_with(
        approved={"ApprovedNode": candidate("approved")},
        registry={"RegistryNode": candidate("registry")},
        installed={"GitNode": candidate("installed_git")},
        agent={"AgentNode": candidate("agent")},
        manual={"ManualNode": candidate("manual")},
    )
    result = asyncio.run(resolver.resolve_nodes(capture_with(
        "KSampler", "ApprovedNode", "RegistryNode", "GitNode",
        "AgentNode", "ManualNode", "MissingNode",
    )))
    self.assertEqual(
        [(row.class_type, row.source_kind) for row in result.rows[:-1]],
        [
            ("KSampler", "core"),
            ("ApprovedNode", "approved"),
            ("RegistryNode", "registry"),
            ("GitNode", "installed_git"),
            ("AgentNode", "agent"),
            ("ManualNode", "manual"),
        ],
    )
    self.assertEqual(result.rows[-1].status, "mapping_required")
    self.assertFalse(result.rentable)
```

- [ ] **Step 6: Implement ordered resolution and Agent Panel boundary**

Return rows with exact statuses `resolved`, `mapping_required`, or `unsupported`. Agent suggestions can add candidates through `register_agent_suggestion(payload)` but cannot call offer, confirm, job, deadline, or destroy services. A candidate becomes `resolved` only after immutable archive and Python wheel metadata from Task 6 are complete.

- [ ] **Step 7: Run focused resolver tests**

Run:

```sh
python3 -m unittest tests.python.test_comfy_host tests.python.test_registry tests.python.test_resolver -v
```

Expected: PASS; no network is used because every client is fake.

- [ ] **Step 8: Commit node resolution**

```sh
git add cloud_run/comfy_host.py cloud_run/registry.py cloud_run/resolver.py \
  tests/python/test_comfy_host.py tests/python/test_registry.py \
  tests/python/test_resolver.py
git commit -m "feat: resolve immutable core and custom node sources"
```

---

### Task 6: Resolve models, inputs, package archives, and disk before rental

**Files:**
- Create: `cloud_run/artifacts.py`
- Create: `tests/python/test_artifacts.py`
- Modify: `cloud_run/resolver.py`
- Modify: `tests/python/test_resolver.py`

**Interfaces:**
- Consumes: compiled prompt inputs, ComfyUI node/input metadata, local model/input roots, approved source mappings.
- Produces: `hash_file`, `build_package_archive`, `resolve_artifacts`, `estimate_output_bytes`, and `calculate_disk_gb`.

- [ ] **Step 1: Write failing hashing, path, collision, and disk tests**

```python
def test_stream_hash_archive_and_disk_are_deterministic(self):
    digest = hash_file(self.fixture)
    self.assertEqual(digest.size_bytes, len(self.fixture.read_bytes()))
    self.assertEqual(digest.sha256, hashlib.sha256(self.fixture.read_bytes()).hexdigest())

    first = build_package_archive(self.package, self.out / "first.tar")
    second = build_package_archive(self.package, self.out / "second.tar")
    self.assertEqual(first.sha256, second.sha256)
    self.assertEqual(first.size_bytes, second.size_bytes)

    self.assertEqual(
        calculate_disk_gb(
            base_bytes=40 * GIB,
            dependency_bytes=10 * GIB,
            input_bytes=2 * GIB,
            output_bytes=3 * GIB,
        ),
        80,
    )
    self.assertEqual(
        calculate_disk_gb(
            base_bytes=60 * GIB,
            dependency_bytes=20 * GIB,
            input_bytes=10 * GIB,
            output_bytes=5 * GIB,
        ),
        115,
    )

def test_unknown_output_and_filename_digest_collision_block_preflight(self):
    with self.assertRaises(OutputAllowanceRequired):
        estimate_output_bytes(capture_with_unknown_video_shape(), explicit_bytes=None)
    with self.assertRaises(ArtifactCollisionError):
        reject_destination_collisions([
            artifact("model", "models/checkpoints/a.safetensors", 1, "a" * 64),
            artifact("model", "models/checkpoints/a.safetensors", 1, "b" * 64),
        ])
```

- [ ] **Step 2: Run artifact tests and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_artifacts -v
```

Expected: FAIL because `cloud_run.artifacts` does not exist.

- [ ] **Step 3: Implement bounded streaming and deterministic archives**

Use 8 MiB chunks for SHA-256. Confine every source and destination with `Path.resolve()` plus `relative_to(allowed_root)`. Deterministic tar entries must use sorted POSIX names, UID/GID 0, empty owner names, mode stripped to `0755` for executable files or `0644` otherwise, and mtime 0. Exclude `.git`, caches, bytecode, outputs, secrets, and symlinks escaping the package.

Disk calculation must be:

```python
GIB = 1024 ** 3
HEADROOM_BYTES = 20 * GIB
MINIMUM_DISK_GB = 80


def calculate_disk_gb(*, base_bytes, dependency_bytes, input_bytes, output_bytes):
    total = (
        int(base_bytes)
        + int(dependency_bytes)
        + int(input_bytes)
        + int(output_bytes)
        + HEADROOM_BYTES
    )
    return max(MINIMUM_DISK_GB, math.ceil(total / GIB))
```

- [ ] **Step 4: Implement model and input discovery**

For every executable prompt input, use `ComfyHost` metadata to identify file-backed widgets. Resolve model category/path through configured ComfyUI model roots and input media through the input root. Record exact relative remote destination, size, digest, and approved source. Scalar strings that cannot be proven file-backed stay ordinary prompt values. A file-backed value with no source mapping is `mapping_required`; an escaping or ambiguous value is `unsupported`.

Custom-node archives must record the approved GitHub URL and 40-hex revision plus the locally built archive's exact size/hash. Python installation accepts only a pre-resolved set of wheel artifacts with exact filenames, sizes, hashes, and Python 3.13/Linux compatibility; unpinned requirements block rental.

- [ ] **Step 5: Add output estimation rules**

Implement conservative rules for known image/video output nodes using width, height, batch size, frames, channels, bytes per channel, and a 2× temporary-workspace factor. If any required dimension or frame count is dynamic, require `explicit_output_allowance_bytes > 0`. Never silently use zero.

- [ ] **Step 6: Run artifact and resolver tests**

Run:

```sh
python3 -m unittest tests.python.test_artifacts tests.python.test_resolver -v
```

Expected: PASS; every artifact has an exact size/hash/destination and disk has 20 GiB headroom with an 80 GiB floor.

- [ ] **Step 7: Commit artifact resolution**

```sh
git add cloud_run/artifacts.py cloud_run/resolver.py \
  tests/python/test_artifacts.py tests/python/test_resolver.py
git commit -m "feat: resolve verified workflow artifacts and disk"
```

---

### Task 7: Add optional R2 cache planning without exposing long-lived keys

**Files:**
- Modify: `cloud_run/settings.py`
- Modify: `tests/python/test_settings.py`
- Create: `cloud_run/r2.py`
- Create: `tests/python/test_r2.py`
- Modify: `cloud_run/resolver.py`
- Modify: `tests/python/test_resolver.py`

**Interfaces:**
- Consumes: write-only settings and content-addressed artifacts.
- Produces: `R2Config`, `R2Signer.sign_get`, `R2Signer.sign_put`, and explicit cache-population plans.

- [ ] **Step 1: Write failing write-only settings tests**

```python
def test_optional_source_credentials_are_write_only(self):
    stored = self.store.update({
        "max_price_per_hour": 1.0,
        "min_vram_gb": 16,
        "r2_endpoint": "https://account.r2.cloudflarestorage.com",
        "r2_bucket": "cloud-run",
        "r2_access_key_id": "access-id",
        "r2_secret_access_key": "secret-value",
        "hf_token": "hf-private",
        "civitai_token": "civitai-private",
    })
    public = public_settings(stored)
    self.assertTrue(public["r2_configured"])
    self.assertTrue(public["hf_configured"])
    self.assertTrue(public["civitai_configured"])
    self.assertNotIn("r2_access_key_id", public)
    self.assertNotIn("r2_secret_access_key", public)
    self.assertNotIn("hf_token", public)
    self.assertNotIn("civitai_token", public)
```

- [ ] **Step 2: Write failing SigV4 scope and expiry tests**

```python
def test_r2_signer_scopes_one_object_and_short_expiry(self):
    signed = R2Signer(config()).sign_get("sha256/aa/" + "a" * 64, expires=300)
    self.assertEqual(signed.method, "GET")
    self.assertEqual(signed.expires_seconds, 300)
    self.assertIn("/cloud-run/sha256/aa/" + "a" * 64, signed.url)
    self.assertNotIn("secret-value", signed.url)
    with self.assertRaises(R2ValidationError):
        R2Signer(config()).sign_get("../other-object", expires=300)
    with self.assertRaises(R2ValidationError):
        R2Signer(config()).sign_put("sha256/x", expires=3601)
```

- [ ] **Step 3: Implement settings and stdlib SigV4**

Allow only an HTTPS endpoint host saved by the user, one bucket, key/secret pairs, and token strings within the existing secret length bound. Preserve existing secret values when omitted from updates. Public settings return booleans only.

`R2Signer` must build AWS Signature Version 4 query authentication with `service="s3"`, `region="auto"`, a maximum 3600-second expiry, exact object key, and method-bound signature. Persist only bucket/key/digest metadata; generate signed URLs immediately before a worker transfer and never return them to the browser.

- [ ] **Step 4: Make cache population explicit**

`DependencyResolver` may mark a private local artifact as `cache_available` but must keep source `local-upload:<artifact_id>` until a separate explicit `populate_cache(artifact_id)` call verifies the completed R2 object by exact size and SHA-256. Free preflight never uploads by itself.

Register `POST /cloud-run/api/cache/artifacts/{artifact_id}`. It accepts exactly `{"acknowledged": true}`, requires saved R2 settings and a server-known local artifact, uploads with resumable fake-tested parts, verifies the remote object's exact metadata, and returns only artifact ID/status/size/digest. It never returns a signed URL or credential.

- [ ] **Step 5: Run settings, R2, resolver, and route tests**

Run:

```sh
python3 -m unittest tests.python.test_settings tests.python.test_r2 \
  tests.python.test_resolver tests.python.test_routes -v
```

Expected: PASS; fixtures use fake requests and no real R2/HF/Civitai operation.

- [ ] **Step 6: Commit optional cache support**

```sh
git add cloud_run/settings.py cloud_run/r2.py cloud_run/resolver.py \
  tests/python/test_settings.py tests/python/test_r2.py \
  tests/python/test_resolver.py
git commit -m "feat: plan optional content-addressed R2 transfers"
```

---

### Task 8: Expose free preflight and mapping approval before offer search

**Files:**
- Create: `cloud_run/session_service.py`
- Create: `tests/python/test_session_service.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Create: `web/js/session-console.js`
- Create: `tests/js/session-console.test.mjs`
- Modify: `web/js/cloud-run.js`

**Interfaces:**
- Consumes: capture, resolver, mapping repository, and manifest repository.
- Produces: `PreflightResult`, `POST /preflights`, `PUT /mappings/{mapping_id}`, and a browser dependency table.

- [ ] **Step 1: Write failing preflight service tests**

```python
def test_preflight_blocks_offer_search_until_every_row_is_resolved(self):
    service = session_service(resolver=FakeResolver([
        row("KSampler", "resolved"),
        row("Fancy", "mapping_required"),
    ]))
    result = asyncio.run(service.preflight("capture-1"))
    self.assertFalse(result.rentable)
    self.assertIsNone(result.manifest_digest)
    with self.assertRaises(PreflightBlocked):
        asyncio.run(service.search_offers(result.preflight_id))

def test_resolved_preflight_persists_manifest_and_exact_totals(self):
    service = session_service(resolver=FakeResolver.resolved(
        transfer_bytes=12, disk_gb=80, output_allowance_bytes=8,
    ))
    result = asyncio.run(service.preflight("capture-1"))
    self.assertTrue(result.rentable)
    self.assertEqual(result.transfer_bytes, 12)
    self.assertEqual(result.disk_gb, 80)
    self.assertEqual(len(result.manifest_digest), 64)
    self.assertEqual(service.provider.mutations, [])
```

- [ ] **Step 2: Run the service test and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_session_service -v
```

Expected: FAIL because preflight orchestration does not exist.

- [ ] **Step 3: Implement preflight orchestration**

Define browser-safe records:

```python
@dataclass(frozen=True)
class PreflightRow:
    dependency_id: str
    kind: str
    display_name: str
    status: str
    source_kind: str | None
    immutable_revision: str | None
    size_bytes: int | None
    sha256: str | None
    destination: str | None
    reason: str | None


@dataclass(frozen=True)
class PreflightResult:
    preflight_id: str
    capture_id: str
    rows: tuple[PreflightRow, ...]
    rentable: bool
    manifest_digest: str | None
    transfer_bytes: int
    output_allowance_bytes: int | None
    disk_gb: int | None
```

Persist the result. `search_offers(preflight_id)` must reload it and require `rentable`, a stored manifest digest, exact artifact sizes, and positive output allowance.

- [ ] **Step 4: Add allowlisted routes**

Register:

```text
POST /cloud-run/api/preflights
PUT  /cloud-run/api/mappings/{mapping_id}
POST /cloud-run/api/integrations/agent-panel/suggestions
```

The Agent Panel route accepts only typed metadata, returns `202`, never approves it, and has no reference to Vast/provider/lifecycle methods. Mapping approval requires the browser to echo the candidate digest being approved.

- [ ] **Step 5: Write and implement the dependency-console test**

Test:

```javascript
test("renders resolved mapping-required and unsupported rows as inert text", () => {
  const consoleView = createSessionConsole(document, fakeApi());
  consoleView.renderPreflight({
    rentable: false,
    transfer_bytes: 12,
    disk_gb: null,
    rows: [
      { display_name: "<KSampler>", status: "resolved", size_bytes: 1 },
      { display_name: "Fancy", status: "mapping_required", reason: "Approve source" },
      { display_name: "Unsafe", status: "unsupported", reason: "Mutable revision" },
    ],
  });
  assert.match(consoleView.root.textContent, /<KSampler>/);
  assert.equal(consoleView.searchButton.disabled, true);
  assert.equal(consoleView.root.querySelectorAll("script").length, 0);
});
```

`session-console.js` must render with `createElement`/`textContent`, show transfer bytes, output allowance, and disk, and disable offer search until `rentable === true`.

- [ ] **Step 6: Run focused service, route, and UI tests**

Run:

```sh
python3 -m unittest tests.python.test_session_service tests.python.test_routes tests.python.test_no_mutation_surface -v
node --test tests/js/session-console.test.mjs tests/js/cloud-run-ui.test.mjs
```

Expected: PASS; capture and preflight perform no provider mutation.

- [ ] **Step 7: Commit the free preflight**

```sh
git add cloud_run/session_service.py cloud_run/service.py cloud_run/routes.py \
  web/js/session-console.js web/js/cloud-run.js \
  tests/python/test_session_service.py tests/python/test_routes.py \
  tests/python/test_no_mutation_surface.py tests/js/session-console.test.mjs
git commit -m "feat: gate Vast offers behind dependency preflight"
```

---

### Task 9: Pin the reviewed worker release and quote the complete paid session

**Files:**
- Create: `cloud_run/worker_release.py`
- Create: `tests/python/test_worker_release.py`
- Create: `remote_worker/template-policy.json`
- Modify: `cloud_run/constants.py`
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/session_service.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_service.py`
- Modify: `tests/python/test_session_service.py`

**Interfaces:**
- Consumes: resolved preflight, exact disk, selected Vast offer, and an injected immutable worker release.
- Produces: `WorkerRelease`, expanded `OfferQuote`, dynamic-disk offer lookup, and fail-closed project-template creation.

- [ ] **Step 1: Write failing release-lock tests**

```python
def test_release_lock_requires_every_immutable_identity(self):
    release = WorkerRelease.from_payload({
        "schema_version": 1,
        "template_hash_id": "1" * 32,
        "worker_commit": "a" * 40,
        "worker_archive_sha256": "b" * 64,
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.13.12",
        "worker_port": 8765,
    })
    self.assertEqual(release.worker_port, 8765)

    for field, value in (
        ("template_hash_id", "mutable"),
        ("worker_commit", "main"),
        ("worker_archive_sha256", "bad"),
        ("comfyui_frontend_version", "1.47.11"),
        ("worker_port", 8188),
    ):
        payload = release.to_record()
        payload[field] = value
        with self.assertRaises(WorkerReleaseError):
            WorkerRelease.from_payload(payload)

def test_missing_release_lock_blocks_live_create_before_provider_request(self):
    with self.assertRaises(WorkerReleaseUnavailable):
        load_worker_release(self.empty_directory / "worker-release.json")
```

- [ ] **Step 2: Run release tests and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_worker_release -v
```

Expected: FAIL because `cloud_run.worker_release` does not exist.

- [ ] **Step 3: Implement the release gate and non-live template policy**

Create `remote_worker/template-policy.json`:

```json
{
  "schema_version": 1,
  "base_template_hash_id": "027fba7753c024be019030fb42aed900",
  "comfyui_core_version": "0.29.0",
  "comfyui_frontend_version": "1.47.10",
  "python_version": "3.13.12",
  "protocol_version": "1",
  "worker_port": 8765
}
```

Do not create a repository `worker-release.json` during offline implementation. `load_worker_release()` must require a reviewed file containing the generated project template ID, public worker commit, and archive digest. Production service construction loads it from the private Cloud Run data directory; tests inject a valid `WorkerRelease`.

- [ ] **Step 4: Write failing complete-quote tests**

```python
def test_quote_contains_disk_transfer_bandwidth_deadline_and_release(self):
    quote = quote_for_resolved_preflight(
        disk_gb=96,
        transfer_bytes=12_000,
        output_allowance_bytes=4_000,
        duration_seconds=7_200,
        dph_total=0.50,
        inet_down_cost=0.01,
        inet_up_cost=0.02,
        release=worker_release(),
    )
    public = quote.public_payload()
    self.assertEqual(public["disk_gb"], 96)
    self.assertEqual(public["transfer_bytes"], 12_000)
    self.assertEqual(public["duration_seconds"], 7_200)
    self.assertEqual(public["approximate_max_active_charge"], 1.0)
    self.assertEqual(public["template_hash_id"], "1" * 32)
    self.assertEqual(public["worker_commit"], "a" * 40)
    self.assertNotIn("session_secret_hex", public)
```

- [ ] **Step 5: Expand quote and exact-offer contracts**

Add immutable quote fields:

```python
disk_gb: int
transfer_bytes: int
output_allowance_bytes: int
inet_down_cost: float | None
inet_up_cost: float | None
duration_seconds: int | None
deadline_mode: str
approximate_max_active_charge: float | None
template_hash_id: str
worker_commit: str
worker_archive_sha256: str
protocol_version: str
manifest_digest: str
```

`build_search_payload` and exact `get_offer` must accept `disk_gb` and set both `disk_space.gte` and `allocated_storage` to that exact value. Normalize optional provider bandwidth prices as finite non-negative numbers. Quote preview requires a resolved preflight and release lock but remains read-only.

- [ ] **Step 6: Write failing project-template create test**

```python
def test_create_uses_only_the_injected_reviewed_project_template(self):
    session = FakeSession(FakeResponse(200, {
        "success": True, "new_contract": 777,
    }))
    instance_id = asyncio.run(create_instance(
        "secret", offer_id=42, disk_gb=96,
        label="comfy-cloud-run-session-1",
        release=worker_release(), session=session,
    ))
    self.assertEqual(instance_id, "777")
    self.assertEqual(session.json, {
        "template_hash_id": "1" * 32,
        "label": "comfy-cloud-run-session-1",
        "disk": 96,
    })
```

- [ ] **Step 7: Switch creation to the validated injected release**

Replace the old optional template argument with required `release: WorkerRelease`. `vast.create_instance` must reject any non-`WorkerRelease`, mismatched protocol/version, disk outside 80–2048 GiB, or template hash not equal to the injected lock before opening a request. Persist a random 32-byte session secret, quote, idempotency key, manifest digest, deadline, and create intent before the one `PUT`.

- [ ] **Step 8: Add the session quote and confirmation routes**

Register:

```text
POST /cloud-run/api/sessions
POST /cloud-run/api/sessions/{session_id}/confirm
```

The first route accepts exactly:

```json
{
  "preflight_id": "server-issued-id",
  "offer_id": "42",
  "idempotency_key": "browser-random-id",
  "deadline": {
    "mode": "finite",
    "duration_seconds": 7200
  }
}
```

It persists and returns the read-only paid quote. The confirm route accepts only the same idempotency key and performs the revalidation/create contract. Keep the old quote/attempt routes temporarily for the still-green baseline UI; Task 17 removes them in the same commit that switches the browser fully to session routes.

- [ ] **Step 9: Run quote, provider, and lifecycle regressions**

Run:

```sh
python3 -m unittest tests.python.test_worker_release tests.python.test_models \
  tests.python.test_vast tests.python.test_service \
  tests.python.test_session_service tests.python.test_lifecycle -v
```

Expected: PASS; fake creation uses one reviewed project-template ID and production construction without a release remains non-rentable.

- [ ] **Step 10: Commit the paid-session contract**

```sh
git add cloud_run/worker_release.py cloud_run/constants.py cloud_run/models.py \
  cloud_run/vast.py cloud_run/service.py cloud_run/session_service.py \
  remote_worker/template-policy.json tests/python/test_worker_release.py \
  tests/python/test_models.py tests/python/test_vast.py \
  tests/python/test_service.py tests/python/test_session_service.py
git commit -m "feat: bind paid sessions to a reviewed worker release"
```

---

### Task 10: Authenticate a claimed Remote Worker and signed manifest protocol

**Files:**
- Create: `cloud_run/worker_protocol.py`
- Create: `tests/python/test_worker_protocol.py`
- Create: `remote_worker/__init__.py`
- Create: `remote_worker/state.py`
- Create: `remote_worker/server.py`
- Create: `remote_worker/main.py`
- Create: `remote_worker/Caddyfile`
- Create: `tests/python/test_worker_server.py`

**Interfaces:**
- Consumes: session ID/secret, Vast-authenticated proxy boundary, manifest canonical bytes.
- Produces: `sign_request`, `verify_request`, one-time `/worker/v1/claim`, and allowlisted authenticated worker routes.

- [ ] **Step 1: Write failing canonical-signature and replay tests**

```python
def test_signed_request_binds_method_path_body_time_and_nonce(self):
    body = b'{"manifest":"abc"}'
    envelope = sign_request(
        b"s" * 32, "POST", "/worker/v1/manifests",
        body, timestamp=1000, nonce="n-1",
    )
    verify_request(
        b"s" * 32, "POST", "/worker/v1/manifests",
        body, envelope, now=1002, seen_nonces=set(),
    )
    for changed in (
        ("GET", "/worker/v1/manifests", body),
        ("POST", "/worker/v1/jobs", body),
        ("POST", "/worker/v1/manifests", b"{}"),
    ):
        with self.assertRaises(ProtocolAuthenticationError):
            verify_request(
                b"s" * 32, *changed, envelope,
                now=1002, seen_nonces=set(),
            )

def test_reused_nonce_and_old_timestamp_are_rejected(self):
    seen = {"n-1"}
    with self.assertRaises(ProtocolAuthenticationError):
        verify_request(
            b"s" * 32, "POST", "/worker/v1/manifests", b"{}",
            signed_envelope(timestamp=1000, nonce="n-1"),
            now=1001, seen_nonces=seen,
        )
```

- [ ] **Step 2: Implement the shared HMAC protocol**

Use:

```python
def signing_material(method, path, body, timestamp, nonce):
    return "\n".join((
        method.upper(),
        path,
        hashlib.sha256(body).hexdigest(),
        str(int(timestamp)),
        nonce,
    )).encode("utf-8")


def sign_request(secret, method, path, body, *, timestamp, nonce):
    signature = hmac.new(
        secret,
        signing_material(method, path, body, timestamp, nonce),
        hashlib.sha256,
    ).hexdigest()
    return {
        "timestamp": int(timestamp),
        "nonce": nonce,
        "signature": signature,
    }
```

Verification uses `hmac.compare_digest`, a ±30 second clock window, a bounded nonce cache, exact raw request bytes, and protocol version `1`.

- [ ] **Step 3: Write failing claim and route-allowlist tests**

```python
def test_worker_claim_requires_proxy_boundary_and_is_one_time(self):
    worker = WorkerApplication(state_path=self.path)
    denied = asyncio.run(worker.handle(fake_request(
        "POST", "/worker/v1/claim", json=claim_payload(),
        boundary_authenticated=False,
    )))
    self.assertEqual(denied.status, 401)

    accepted = asyncio.run(worker.handle(fake_request(
        "POST", "/worker/v1/claim", json=claim_payload(),
        boundary_authenticated=True,
    )))
    self.assertEqual(accepted.status, 200)
    duplicate = asyncio.run(worker.handle(fake_request(
        "POST", "/worker/v1/claim", json=other_claim(),
        boundary_authenticated=True,
    )))
    self.assertEqual(duplicate.status, 409)

def test_worker_exposes_only_the_protocol_allowlist(self):
    self.assertEqual(worker_route_set(), {
        ("GET", "/worker/v1/health"),
        ("POST", "/worker/v1/claim"),
        ("POST", "/worker/v1/manifests"),
        ("GET", "/worker/v1/transactions/{transaction_id}"),
        ("PUT", "/worker/v1/artifacts/{artifact_id}"),
        ("GET", "/worker/v1/artifacts/{artifact_id}"),
        ("POST", "/worker/v1/jobs"),
        ("GET", "/worker/v1/jobs/{job_id}"),
        ("GET", "/worker/v1/jobs/{job_id}/events"),
        ("GET", "/worker/v1/jobs/{job_id}/previews/{preview_id}"),
        ("PUT", "/worker/v1/deadline"),
    })
```

- [ ] **Step 4: Implement private worker state and server**

`WorkerStateStore` atomically persists mode `0600` under a mode `0700` directory:

```json
{
  "schema_version": 1,
  "protocol_version": "1",
  "session_id": "session-id",
  "session_secret_hex": "64-lowercase-hex",
  "claimed": true,
  "deadline_at": 0,
  "deadline_mode": "finite",
  "installed": {},
  "transactions": {},
  "jobs": {}
}
```

The server binds worker HTTP to `127.0.0.1:8766`. Create this reviewed proxy configuration:

```caddyfile
:8765 {
	@unauthorized not header Authorization "Bearer {$JUPYTER_TOKEN}"
	respond @unauthorized 401

	request_header -Authorization
	request_header -X-Cloud-Run-Boundary
	request_header X-Cloud-Run-Boundary authenticated
	reverse_proxy 127.0.0.1:8766
}
```

The project Vast template exposes only Caddy port 8765 and supplies its per-instance `JUPYTER_TOKEN`; port 8766 stays loopback-only. The claim route requires the injected boundary header, matching protocol/session IDs, one unclaimed state, and a 64-hex session secret. Health and claim require the Vast bearer boundary. Manifest, transaction, artifact, job, event, preview, and deadline routes additionally require Task 10 HMAC authentication.

- [ ] **Step 5: Run protocol/server tests**

Run:

```sh
python3 -m unittest tests.python.test_worker_protocol tests.python.test_worker_server -v
```

Expected: PASS; raw secrets and request bodies never appear in response errors.

- [ ] **Step 6: Commit worker authentication**

```sh
git add cloud_run/worker_protocol.py remote_worker/__init__.py \
  remote_worker/state.py remote_worker/server.py remote_worker/main.py \
  remote_worker/Caddyfile tests/python/test_worker_protocol.py \
  tests/python/test_worker_server.py
git commit -m "feat: authenticate the Cloud Run worker protocol"
```

---

### Task 11: Transfer and install only verified manifest content

**Files:**
- Create: `remote_worker/transfers.py`
- Create: `remote_worker/install.py`
- Create: `tests/python/test_worker_transfers.py`
- Create: `tests/python/test_worker_install.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/state.py`

**Interfaces:**
- Consumes: `ArtifactSpec`, approved temporary source operations, confined destination roots.
- Produces: `TransferManager.download_many`, resumable upload route, `safe_extract`, and `CustomNodeInstaller.install`.

- [ ] **Step 1: Write failing ranged-download tests**

```python
def test_download_resumes_part_verifies_hash_and_renames_atomically(self):
    payload = b"verified-model"
    part = self.destination.with_suffix(".part")
    part.write_bytes(payload[:4])
    client = FakeRangeClient(payload)
    result = asyncio.run(self.manager.download(
        artifact_for(payload, self.destination),
        client=client,
    ))
    self.assertEqual(client.ranges, ["bytes=4-"])
    self.assertEqual(result.state, "verified")
    self.assertEqual(self.destination.read_bytes(), payload)
    self.assertFalse(part.exists())

def test_wrong_length_hash_redirect_and_non_range_resume_are_rejected(self):
    for client in (
        FakeRangeClient(b"wrong"),
        FakeRangeClient(b"payload", redirect="https://evil.example/x"),
        FakeRangeClient(b"payload", ignores_range=True),
    ):
        with self.assertRaises(TransferError):
            asyncio.run(self.manager.download(self.artifact, client=client))
```

- [ ] **Step 2: Implement bounded concurrent transfers**

Use `.part` in the final destination directory, `Range: bytes=<offset>-`, strict `Content-Range`, exact final size, streaming SHA-256, `fsync`, and `os.replace`. Permit at most four concurrent downloads, three retries with capped backoff, and reset to byte zero only when the server explicitly cannot resume. Reject cross-origin redirects and any destination escaping the manifest root. Update meaningful-progress time on verified bytes only.

The upload route requires `Content-Range`, matches the manifest artifact ID/size, writes only at the durable expected offset, and returns the next offset. It never accepts a browser/local path.

- [ ] **Step 3: Write failing extraction and fixed-argv install tests**

```python
def test_install_extracts_confined_archive_and_uses_no_shell(self):
    runner = RecordingRunner()
    installer = CustomNodeInstaller(
        custom_nodes_root=self.custom_nodes,
        wheel_root=self.wheels,
        runner=runner,
    )
    result = asyncio.run(installer.install(custom_node_spec()))
    self.assertEqual(result.revision, "a" * 40)
    self.assertEqual(runner.calls, [[
        sys.executable, "-m", "pip", "install",
        "--no-index", "--disable-pip-version-check",
        str(self.wheels / "dep-1.0-py3-none-any.whl"),
    ]])
    self.assertFalse(runner.shell_used)

def test_archive_traversal_symlink_and_unlisted_wheel_fail_closed(self):
    for archive in (traversal_tar(), escaping_symlink_tar()):
        with self.assertRaises(InstallError):
            safe_extract(archive, self.custom_nodes)
    with self.assertRaises(InstallError):
        asyncio.run(self.installer.install(spec_with_unlisted_wheel()))
```

- [ ] **Step 4: Implement safe install contracts**

Accept only the deterministic tar layout from Task 6. Extract regular files/directories with prevalidated relative POSIX names; reject device files, FIFOs, absolute paths, hard links, and escaping symlinks. Install only wheel files present in the signed manifest and already size/hash verified. Invoke `asyncio.create_subprocess_exec` with an argv list; never use `shell=True`, `os.system`, a package-provided script, `setup.py`, or workflow text.

- [ ] **Step 5: Run transfer/install tests**

Run:

```sh
python3 -m unittest tests.python.test_worker_transfers tests.python.test_worker_install tests.python.test_worker_server -v
```

Expected: PASS, including resume, digest rejection, traversal rejection, and fixed argv.

- [ ] **Step 6: Commit verified worker provisioning primitives**

```sh
git add remote_worker/transfers.py remote_worker/install.py \
  remote_worker/server.py remote_worker/state.py \
  tests/python/test_worker_transfers.py tests/python/test_worker_install.py
git commit -m "feat: transfer and install verified worker artifacts"
```

---

### Task 12: Provision, validate, repair once, and enforce restart/stall budgets

**Files:**
- Create: `remote_worker/comfy.py`
- Create: `remote_worker/provision.py`
- Create: `tests/python/test_worker_provision.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/state.py`

**Interfaces:**
- Consumes: signed manifest, Task 11 transfers/install, internal ComfyUI.
- Produces: `ComfyProcess`, `Provisioner.apply_manifest`, readiness record, compatible installed set, and bounded repair.

- [ ] **Step 1: Write failing provisioning transaction tests**

```python
def test_initial_manifest_installs_validates_and_records_readiness(self):
    provisioner = fake_provisioner(
        object_info={"KSampler": {}, "Fancy": {}},
        files={"models/upscale/a.pth", "input/source.jpg"},
    )
    result = asyncio.run(provisioner.apply_manifest(manifest_with_custom_node()))
    self.assertEqual(result.state, "ready")
    self.assertEqual(result.planned_restarts, 1)
    self.assertEqual(result.repair_restarts, 0)
    self.assertEqual(result.missing_class_types, ())

def test_one_approved_repair_gets_one_extra_restart_then_stops(self):
    provisioner = fake_provisioner(
        object_info_sequence=[{"KSampler": {}}, {"KSampler": {}, "Fancy": {}}],
        repairable={"Fancy"},
    )
    result = asyncio.run(provisioner.apply_manifest(manifest_with_custom_node()))
    self.assertEqual(result.state, "ready")
    self.assertEqual(result.planned_restarts, 1)
    self.assertEqual(result.repair_restarts, 1)
    with self.assertRaises(ProvisionError):
        asyncio.run(provisioner.repair(result.transaction_id))

def test_unapproved_missing_item_and_ten_minute_stall_never_repair_or_restart(self):
    with self.assertRaises(UnapprovedRepairError):
        asyncio.run(fake_provisioner(missing="Unknown").apply_manifest(manifest()))
    with self.assertRaises(ProvisionStalled):
        asyncio.run(fake_provisioner(clock=stalled_clock(601)).apply_manifest(manifest()))
```

- [ ] **Step 2: Run provisioning tests and verify failure**

Run:

```sh
python3 -m unittest tests.python.test_worker_provision -v
```

Expected: FAIL because provision/comfy managers do not exist.

- [ ] **Step 3: Implement internal ComfyUI process management**

`ComfyProcess` starts the pinned installation with fixed argv and internal port `8188`, a private working directory, and no workflow-derived environment. It waits for `/system_stats`, fetches `/object_info`, and stops/restarts with bounded process timeouts. It exposes no raw internal URL outside the worker.

- [ ] **Step 4: Implement the provisioning state machine**

For each unique transaction:

1. compare protocol/core/frontend/worker identities;
2. reserve declared disk plus headroom before transfer;
3. compute `ManifestDelta` against installed state;
4. download/upload only delta artifacts;
5. install only new pinned custom-node packages/wheels;
6. perform zero restarts for artifact-only delta or one planned restart when code changed;
7. validate every executable `class_type` in `/object_info`;
8. validate every exact model/input destination;
9. on the first mismatch, repair only manifest-listed missing content;
10. permit one repair restart, revalidate, then fail;
11. persist the installed set only after complete validation.

Meaningful progress is a changed byte offset, completed install unit, successful health response, or validation advancement. At 600 seconds without one, transition the transaction to `stalled`; do not alter the absolute session deadline.

- [ ] **Step 5: Run worker provisioning tests**

Run:

```sh
python3 -m unittest tests.python.test_worker_provision \
  tests.python.test_worker_transfers tests.python.test_worker_install -v
```

Expected: PASS with exact planned/repair restart counts per transaction.

- [ ] **Step 6: Commit remote provisioning**

```sh
git add remote_worker/comfy.py remote_worker/provision.py \
  remote_worker/server.py remote_worker/state.py \
  tests/python/test_worker_provision.py
git commit -m "feat: provision and validate the remote ComfyUI worker"
```

---

### Task 13: Execute native remote prompts, relay events, and self-destroy at deadline

**Files:**
- Create: `remote_worker/jobs.py`
- Create: `remote_worker/deadline.py`
- Create: `tests/python/test_worker_jobs.py`
- Create: `tests/python/test_worker_deadline.py`
- Modify: `remote_worker/comfy.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/state.py`

**Interfaces:**
- Consumes: captured native prompt/workflow/options, validated manifest, `CONTAINER_ID`, `CONTAINER_API_KEY`.
- Produces: single-job queue, sanitized ordered events/previews/history/output descriptors, and own-instance deadline destruction.

- [ ] **Step 1: Write failing exact-native-prompt and single-job tests**

```python
def test_job_posts_the_captured_native_body_and_returns_to_idle(self):
    comfy = FakeComfy()
    jobs = JobManager(comfy=comfy, state=self.state)
    result = asyncio.run(jobs.run(job_request()))
    self.assertEqual(comfy.prompt_body, {
        "client_id": result.client_id,
        "prompt": job_request()["output"],
        "partial_execution_targets": ["7"],
        "extra_data": {
            "comfy_usage_source": "comfyui-cloud-run",
            "extra_pnginfo": {"workflow": job_request()["workflow"]},
            "preview_method": "latent2rgb",
        },
    })
    self.assertNotIn("auth_token_comfy_org", repr(comfy.prompt_body))
    self.assertEqual(result.state, "succeeded")

def test_second_concurrent_job_is_rejected_without_touching_comfy(self):
    jobs = JobManager(comfy=BlockingComfy(), state=self.state)
    first = asyncio.create_task(jobs.run(job_request("job-1")))
    await jobs.started.wait()
    with self.assertRaises(JobBusyError):
        await jobs.run(job_request("job-2"))
    jobs.release.set()
    await first
```

- [ ] **Step 2: Implement native Comfy submission and event capture**

Generate a random worker-only `client_id`, POST the exact `output`, full `workflow`, partial targets, and preview method to internal `/prompt`, then consume `/ws?clientId=<id>`. Allowlist and sanitize:

```text
execution_start
status
progress
progress_text
progress_state
executing
executed
execution_cached
execution_success
execution_error
execution_interrupted
b_preview
b_preview_with_metadata
```

Store increasing sequence numbers. Keep binary previews in private files referenced by random preview IDs. On success, read native `/history/{prompt_id}`, confine output descriptors to ComfyUI output roots, compute size/SHA-256, and expose descriptors without an internal URL. On native validation, execution, interruption, or OOM error, sanitize node ID/class/title context and release the single-job lock without modifying provider state.

- [ ] **Step 3: Write failing deadline-scope tests**

```python
def test_deadline_uses_only_own_instance_credentials_and_delete(self):
    provider = FakeContainerProvider()
    watchdog = DeadlineWatchdog(
        container_id="77",
        container_api_key="instance-key",
        provider=provider,
        clock=lambda: 200,
    )
    asyncio.run(watchdog.enforce(deadline_at=199, mode="finite"))
    self.assertEqual(provider.calls, [
        ("DELETE", "https://console.vast.ai/api/v0/instances/77/",
         "instance-key"),
    ])

def test_no_limit_disables_watchdog_only_after_signed_update(self):
    watchdog = deadline_watchdog()
    with self.assertRaises(DeadlineValidationError):
        watchdog.update({"mode": "none", "acknowledged": False})
    watchdog.update({"mode": "none", "acknowledged": True})
    self.assertEqual(watchdog.mode, "none")
```

- [ ] **Step 4: Implement the instance-scoped watchdog**

Read `CONTAINER_ID` and `CONTAINER_API_KEY` only in `remote_worker/deadline.py`. Validate a numeric ID and non-empty key, never print either, expose only `DELETE /api/v0/instances/<same-id>/`, and provide no search/list/create/stop method. A finite signed update must have a future absolute epoch. An expired deadline persists destroy intent, permits bounded output-retrieval grace configured by the signed request, then calls own-instance DELETE even if artifacts remain incomplete.

- [ ] **Step 5: Run job and deadline tests**

Run:

```sh
python3 -m unittest tests.python.test_worker_jobs \
  tests.python.test_worker_deadline tests.python.test_worker_server -v
```

Expected: PASS; the fake provider records no method except DELETE for the worker.

- [ ] **Step 6: Commit native execution and deadline enforcement**

```sh
git add remote_worker/jobs.py remote_worker/deadline.py \
  remote_worker/comfy.py remote_worker/server.py remote_worker/state.py \
  tests/python/test_worker_jobs.py tests/python/test_worker_deadline.py
git commit -m "feat: execute remote ComfyUI jobs with a hard deadline"
```

---

### Task 14: Relay authenticated worker progress and verified outputs locally

**Files:**
- Create: `cloud_run/worker_client.py`
- Create: `cloud_run/relay.py`
- Create: `tests/python/test_worker_client.py`
- Create: `tests/python/test_relay.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_routes.py`

**Interfaces:**
- Consumes: private worker URL/provider token/session secret, worker events/artifacts.
- Produces: `WorkerClient`, `LocalRelay.sync_job`, resumable local output files, same-origin preview/output routes.

- [ ] **Step 1: Write failing worker-client boundary tests**

```python
def test_client_claims_through_vast_bearer_then_uses_hmac(self):
    transport = RecordingTransport()
    client = WorkerClient(
        base_url="http://8.8.8.8:30000",
        provider_token="vast-boundary-token",
        session_id="session-1",
        session_secret=b"s" * 32,
        transport=transport,
        clock=lambda: 1000,
        nonce=lambda: "n-1",
    )
    asyncio.run(client.claim())
    asyncio.run(client.health())
    self.assertEqual(transport.requests[0].headers["Authorization"],
                     "Bearer vast-boundary-token")
    self.assertEqual(transport.requests[1].headers["Authorization"],
                     "Bearer vast-boundary-token")
    self.assertIn("X-Cloud-Run-Signature",
                  transport.requests[1].headers)

def test_client_rejects_private_loopback_redirect_and_browser_payload_secrets(self):
    for url in ("http://127.0.0.1:8765", "http://10.0.0.1:8765",
                "https://evil.example/worker"):
        with self.assertRaises(WorkerClientError):
            worker_client(base_url=url)
```

- [ ] **Step 2: Implement the authenticated worker client**

Reuse the global-IP/port validation in `vast.derive_base_url`, disable automatic redirects, use bounded `aiohttp` timeouts, and send the per-instance Vast bearer token to Caddy on every call. Claim once, then also sign exact raw bodies using Task 10 headers. Sanitize status/body failures and never expose `base_url`, provider token, session secret, signed URL, or worker headers through `public_payload`.

- [ ] **Step 3: Write failing output-resume and event tests**

```python
def test_relay_resumes_verifies_and_atomically_publishes_output(self):
    expected = b"wallpaper-output"
    relay = local_relay(worker=FakeWorker(output=expected))
    part = relay.private_part_path("job-1", "output-1")
    part.parent.mkdir(parents=True)
    part.write_bytes(expected[:5])
    result = asyncio.run(relay.download_output(
        job_id="job-1",
        descriptor=descriptor(expected),
        output_root=self.output_root,
    ))
    self.assertEqual(relay.worker.ranges, ["bytes=5-"])
    self.assertEqual(result.state, TransferState.VERIFIED)
    self.assertEqual(result.local_path.read_bytes(), expected)
    self.assertFalse(result.local_path.name.endswith(".part"))

def test_relay_sanitizes_events_and_never_publishes_unverified_file(self):
    relay = local_relay(worker=FakeWorker(
        events=[{"type": "execution_error",
                 "data": {"node_id": "7", "secret": "provider-key"}}],
        output=b"wrong",
    ))
    with self.assertRaises(ArtifactVerificationError):
        asyncio.run(relay.sync_job("job-1"))
    self.assertNotIn("provider-key", repr(relay.repository.list_events("job-1", 0)))
    self.assertEqual(list(self.output_root.iterdir()), [])
```

- [ ] **Step 4: Implement relay persistence and same-origin media routes**

Poll ordered events with durable `after_sequence`; map node/progress/error fields through explicit allowlists. Fetch previews by random ID into bounded private cache. Download outputs with `Range`, stored offset, exact size/hash, `fsync`, and atomic rename into a configured local output root. Mark `local_verified` only after rename.

Register:

```text
GET /cloud-run/api/sessions/{session_id}/jobs/{job_id}/events
GET /cloud-run/api/sessions/{session_id}/jobs/{job_id}/previews/{preview_id}
GET /cloud-run/api/sessions/{session_id}/jobs/{job_id}/artifacts/{artifact_id}
```

Routes authorize ownership by session/job records and return local bytes only; they never redirect to a worker/provider URL.

- [ ] **Step 5: Run relay/client/route tests**

Run:

```sh
python3 -m unittest tests.python.test_worker_client tests.python.test_relay \
  tests.python.test_job_repository tests.python.test_routes -v
```

Expected: PASS with resumed offsets and no unverified final file.

- [ ] **Step 6: Commit the local relay**

```sh
git add cloud_run/worker_client.py cloud_run/relay.py \
  cloud_run/job_repository.py cloud_run/routes.py \
  tests/python/test_worker_client.py tests/python/test_relay.py \
  tests/python/test_job_repository.py tests/python/test_routes.py
git commit -m "feat: relay verified remote results into ComfyUI"
```

---

### Task 15: Orchestrate reusable one-job sessions, deltas, and recovery

**Files:**
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_routes.py`

**Interfaces:**
- Consumes: session repository, manifests/deltas, worker client, relay, Vast lifecycle.
- Produces: `submit_job`, compatible delta provisioning, return-to-ready, startup adoption, transfer resume, and deadline recovery.

- [ ] **Step 1: Write failing reusable-session tests**

```python
def test_two_compatible_jobs_use_one_instance_and_transfer_only_delta(self):
    service = reusable_session_service()
    session = asyncio.run(service.confirm_and_ready("session-1"))
    first = asyncio.run(service.submit_job(
        session.session_id, capture="capture-1", idempotency_key="job-key-1",
    ))
    second = asyncio.run(service.submit_job(
        session.session_id, capture="capture-2", idempotency_key="job-key-2",
    ))
    self.assertEqual(first.state, JobState.SUCCEEDED)
    self.assertEqual(second.state, JobState.SUCCEEDED)
    self.assertEqual(service.provider.create_calls, 1)
    self.assertEqual(service.worker.download_counts["model-a"], 1)
    self.assertEqual(service.worker.download_counts["input-b"], 1)
    self.assertEqual(service.session("session-1").state, SessionState.READY)

def test_changed_package_revision_requires_new_session_without_mutation(self):
    service = reusable_session_service(installed=manifest(package_revision="a" * 40))
    with self.assertRaises(IncompatibleSession):
        asyncio.run(service.submit_job(
            "session-1",
            capture=capture_for(manifest(package_revision="b" * 40)),
            idempotency_key="job-key-2",
        ))
    self.assertEqual(service.provider.create_calls, 1)
    self.assertEqual(service.worker.manifest_calls, [])
```

- [ ] **Step 2: Write failing error/replacement tests**

```python
def test_execution_and_oom_fail_job_but_keep_healthy_session(self):
    for error_code in ("execution_error", "out_of_memory"):
        service = reusable_session_service(worker_error=error_code)
        job = asyncio.run(service.submit_job(
            "session-1", capture="capture-1",
            idempotency_key="job-key",
        ))
        self.assertEqual(job.state, JobState.FAILED)
        self.assertEqual(service.session("session-1").state, SessionState.READY)
        self.assertEqual(service.provider.create_calls, 1)

def test_only_boot_failure_replaces_once_after_inventory_absence(self):
    service = reusable_session_service(boot_failure=True)
    session = asyncio.run(service.recover_or_replace("session-1"))
    self.assertEqual(session.retry_count, 1)
    self.assertEqual(service.provider.call_order, [
        "create:first", "destroy:first", "inventory:absent", "create:replacement",
    ])
```

- [ ] **Step 3: Implement transactional job submission**

`submit_job(session_id, capture, idempotency_key)` must:

1. require `SessionState.READY`;
2. create/get one durable job by session idempotency key;
3. resolve a fresh manifest from the fresh capture;
4. compute delta against the worker-verified installed set;
5. reject incompatible revisions before a worker/provider mutation;
6. transition through `PROVISIONING`/`VALIDATING` only for non-empty delta;
7. transition `READY → RUNNING → HARVESTING → READY`;
8. return job execution/OOM failures to `READY`;
9. never create another provider instance from a job path.

Use one SQLite conditional update on session version/state to enforce one job at a time.

- [ ] **Step 4: Implement worker boot and startup recovery**

After Vast reports the mapped worker port and authenticated proxy readiness:

1. claim worker with the persisted session secret;
2. send the initial signed manifest;
3. apply provisioning/validation states;
4. send the durable deadline;
5. transition to `READY`.

Startup recovery lists managed Vast instances once, matches by label, rejects more than one match as residual-billing failure, adopts at most one instance, reconnects/claims worker, restores installed set, resumes uploads/downloads from offsets, polls running job state, and re-enforces the deadline. It never blindly creates. Keep the existing destruction-gated single host replacement behavior with new session states.

- [ ] **Step 5: Register session/job routes**

Add:

```text
GET  /cloud-run/api/sessions/{session_id}
POST /cloud-run/api/sessions/{session_id}/jobs
GET  /cloud-run/api/sessions/{session_id}/jobs/{job_id}
```

The job POST requires `capture_id` and a fresh idempotency key. Duplicate requests return the original job and never submit twice.

- [ ] **Step 6: Run session/lifecycle/route tests**

Run:

```sh
python3 -m unittest tests.python.test_session_service \
  tests.python.test_lifecycle tests.python.test_routes \
  tests.python.test_fake_lifecycle_integration -v
```

Expected: PASS; old lifecycle safety remains, and two compatible jobs use one fake instance.

- [ ] **Step 7: Commit reusable session orchestration**

```sh
git add cloud_run/session_service.py cloud_run/service.py \
  cloud_run/lifecycle.py cloud_run/routes.py \
  tests/python/test_session_service.py tests/python/test_lifecycle.py \
  tests/python/test_routes.py
git commit -m "feat: run sequential jobs on one Vast session"
```

---

### Task 16: Enforce deadline controls and strengthened verified destruction

**Files:**
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/repository.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_repository.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_routes.py`

**Interfaces:**
- Consumes: durable deadline, worker client, instance ID/label, artifact verification state.
- Produces: extension/disable controls, alerts, one-time destruction review token, and inventory-proven `destroyed`.

- [ ] **Step 1: Write failing deadline-control tests**

```python
def test_default_deadline_alerts_extensions_and_no_limit_acknowledgement(self):
    service = session_service(clock=lambda: 1000)
    session = service.create_session(duration_seconds=None)
    self.assertEqual(session.deadline_at, 1000 + 7200)
    self.assertEqual(service.alerts(session, now=1000 + 6300), ["15_minutes"])
    self.assertEqual(service.alerts(session, now=1000 + 6900), ["5_minutes"])
    extended = asyncio.run(service.update_deadline(session.session_id, {
        "action": "add_30_minutes",
    }))
    self.assertEqual(extended.deadline_at, 1000 + 9000)
    with self.assertRaises(DeadlineValidationError):
        asyncio.run(service.update_deadline(session.session_id, {
            "action": "disable", "acknowledged": False,
        }))
```

- [ ] **Step 2: Write failing multi-step destruction tests**

```python
def test_destroy_requires_fresh_review_token_and_data_loss_ack(self):
    service = session_service_with_instance(unverified_outputs=["output-2"])
    review = asyncio.run(service.review_destroy("session-1"))
    self.assertEqual(review.instance_id, "77")
    self.assertEqual(review.unverified_artifact_ids, ("output-2",))
    with self.assertRaises(DestroyConfirmationError):
        asyncio.run(service.destroy("session-1", {
            "review_token": review.token,
            "acknowledge_data_loss": False,
        }))
    destroyed = asyncio.run(service.destroy("session-1", {
        "review_token": review.token,
        "acknowledge_data_loss": True,
    }))
    self.assertEqual(destroyed.state, SessionState.DESTROYED)
    self.assertEqual(service.provider.call_order, ["destroy:77", "inventory"])

def test_delete_response_without_inventory_absence_keeps_billing_warning(self):
    service = session_service_with_instance(inventory_still_contains=True)
    review = asyncio.run(service.review_destroy("session-1"))
    failed = asyncio.run(service.destroy("session-1", confirmed(review)))
    self.assertEqual(failed.state, SessionState.FAILED)
    self.assertEqual(failed.instance_id, "77")
    self.assertTrue(failed.public_payload()["billing_may_continue"])
```

- [ ] **Step 3: Implement durable deadline actions**

Allow exactly:

```python
DEADLINE_ACTION_SECONDS = {
    "add_30_minutes": 30 * 60,
    "add_1_hour": 60 * 60,
}
```

Persist locally before sending the signed worker update. `disable` requires `acknowledged is True`, sets `deadline_mode="none"` and `deadline_at=None`, and remains red/public. If worker update fails, retain a visible synchronization error and keep the earlier effective finite boundary until both sides agree.

- [ ] **Step 4: Implement one-time destruction review**

`review_destroy` returns browser-safe identity/state, locally unverified artifacts, irreversible-loss copy, and a random one-time token stored as a SHA-256 digest with five-minute expiry. `destroy` requires token match, expiry, same session version/instance, and `acknowledge_data_loss=True`; consume the token before mutation.

For normal user teardown, list unverified outputs and require acknowledgement. For deadline teardown, make bounded retrieval attempts then mark remaining transfers `abandoned`. Always persist destroy intent, call DELETE, fetch fresh inventory, and transition to `DESTROYED` only when both ID and label are absent. Never call or define Stop.

- [ ] **Step 5: Register exact control routes**

```text
PUT  /cloud-run/api/sessions/{session_id}/deadline
POST /cloud-run/api/sessions/{session_id}/destroy-review
DELETE /cloud-run/api/sessions/{session_id}
```

- [ ] **Step 6: Run deadline/destruction regressions**

Run:

```sh
python3 -m unittest tests.python.test_models tests.python.test_repository \
  tests.python.test_lifecycle tests.python.test_routes \
  tests.python.test_session_service -v
```

Expected: PASS; a DELETE response alone never yields `destroyed`.

- [ ] **Step 7: Commit cost and destruction controls**

```sh
git add cloud_run/models.py cloud_run/repository.py cloud_run/lifecycle.py \
  cloud_run/session_service.py cloud_run/routes.py \
  tests/python/test_models.py tests/python/test_repository.py \
  tests/python/test_lifecycle.py tests/python/test_routes.py
git commit -m "feat: enforce session deadlines and verified destruction"
```

---

### Task 17: Complete the browser session console without altering local Run

**Files:**
- Modify: `web/js/session-console.js`
- Modify: `web/js/cloud-run-api.js`
- Modify: `web/js/cloud-run.js`
- Modify: `tests/js/session-console.test.mjs`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `tests/js/fake-dom.mjs`

**Interfaces:**
- Consumes: all browser-safe preflight/session/job/event/deadline/destroy routes.
- Produces: full Cloud Run session console and repeated capture/job submission.

- [ ] **Step 1: Write failing paid-review and progress tests**

```javascript
test("paid review shows every bounded cost and immutable identity", () => {
  const view = createSessionConsole(document, fakeApi());
  view.renderQuote(fullQuote());
  const text = view.root.textContent;
  for (const expected of [
    "Offer 42", "RTX 4090", "24 GB", "$0.50/h",
    "96 GB ephemeral disk", "12 KB dependencies and inputs",
    "2 hour automatic limit", "approximately $1.00 active/storage",
    "template " + "1".repeat(32), "worker " + "a".repeat(40),
    "bandwidth pricing can change the final provider charge",
  ]) assert.match(text, new RegExp(expected.replace("$", "\\$")));
});

test("console renders remote node progress previews errors and verified outputs", () => {
  const view = createSessionConsole(document, fakeApi());
  view.renderSession(sessionPayload({
    status: "running",
    elapsed_seconds: 20,
    approximate_spend: 0.01,
    current_node: { id: "7", title: "<Upscale>" },
    progress: { value: 3, max: 10 },
    previews: [{ id: "preview-1" }],
    outputs: [{ id: "output-1", state: "local_verified",
                filename: "wallpaper.png" }],
  }));
  assert.match(view.root.textContent, /<Upscale>/);
  assert.match(view.root.textContent, /3 of 10/);
  assert.match(view.root.textContent, /wallpaper.png/);
  assert.equal(view.root.querySelectorAll("script").length, 0);
});
```

- [ ] **Step 2: Write failing deadline/destroy/reuse tests**

```javascript
test("no-limit stays red and destroy requires review then final acknowledgement", async () => {
  const api = fakeApi();
  const view = createSessionConsole(document, api);
  view.renderSession(sessionPayload({ deadline_mode: "none", status: "ready" }));
  assert.match(view.deadlineWarning.className, /danger/);
  assert.equal(view.destroyButton.textContent,
               "Destroy GPU — stop all Vast billing");

  await click(view.destroyButton);
  assert.match(view.destroyReview.textContent, /irreversibly lost/);
  assert.equal(api.destroyCalls.length, 0);
  view.dataLossCheckbox.checked = true;
  await click(view.destroyNowButton);
  assert.equal(api.destroyCalls.length, 1);
});

test("ready session captures a fresh canvas and submits a second job", async () => {
  const api = fakeApi();
  const capture = fakeCaptureAdapter();
  const view = createSessionConsole(document, api, { capture });
  view.renderSession(sessionPayload({ status: "ready" }));
  await click(view.runNextJobButton);
  assert.equal(capture.calls, 1);
  assert.equal(api.jobCalls[0].capture_id, "capture-2");
});
```

- [ ] **Step 3: Implement the complete server-driven console**

The console must display:

- write-only Vast and optional cache/source configured flags;
- preflight rows and explicit mapping approval;
- disk/download/output totals;
- offer quality/bandwidth and complete paid review;
- provisioning phase, bytes/install/validation progress, and 10-minute stall;
- hourly rate, elapsed time, approximate spend, finite deadline, 15/5 minute warnings, extensions, and red no-limit mode;
- one current job, node/progress, same-origin previews, sanitized errors, local history, and verified output links;
- `Run current canvas on this GPU` only when `ready`;
- permanently available `Destroy GPU — stop all Vast billing` once provider mutation may have occurred;
- two-stage review, data-loss checkbox, and final `Destroy now`.

All server values use `textContent` or safe attribute setters. Poll only states that need progress. Stop polling on `destroyed`; keep polling or emergency guidance on residual-billing failure.

- [ ] **Step 4: Preserve the local launcher and Agent Panel**

Keep the existing `MutationObserver`, command ID, adjacent placement, keyboard behavior, compact layout, and no local button listener changes. Do not overwrite global `fetch`, `api.fetchApi`, `app.queuePrompt`, or `api.queuePrompt` outside the Task 2 one-shot `try/finally`.

- [ ] **Step 5: Remove the superseded attempt routes atomically with the UI switch**

Delete the old `/cloud-run/api/quotes` and `/cloud-run/api/attempts/{attempt_id}...` decorators only after `cloud-run-api.js` uses Task 9's session quote/confirm routes and Tasks 15–16's session/job/deadline/destroy routes. Update route tests and the gate to the final allowlist in Task 18. Migrate durable legacy rows through Task 1; do not expose two paid-create surfaces.

- [ ] **Step 6: Run the complete Node and route suites**

Run:

```sh
node --test tests/js/*.test.mjs
python3 -m unittest tests.python.test_routes \
  tests.python.test_loader_and_routes \
  tests.python.test_no_mutation_surface -v
```

Expected: PASS; all original 18 tests plus canvas/session-console tests pass.

- [ ] **Step 7: Commit the session console**

```sh
git add web/js/session-console.js web/js/cloud-run-api.js \
  web/js/cloud-run.js tests/js/session-console.test.mjs \
  tests/js/cloud-run-ui.test.mjs tests/js/fake-dom.mjs \
  cloud_run/routes.py tests/python/test_routes.py \
  tests/python/test_loader_and_routes.py \
  tests/python/test_no_mutation_surface.py
git commit -m "feat: add the reusable Cloud Run session console"
```

---

### Task 18: Certify the full fake session, security boundaries, and worker artifact

**Files:**
- Create: `tests/python/test_fake_session_integration.py`
- Create: `remote_worker/bootstrap.py`
- Create: `tests/python/test_worker_bootstrap.py`
- Create: `scripts/build_worker_artifact.py`
- Create: `scripts/validate_gold_output.py`
- Create: `tests/python/test_gold_output_validation.py`
- Modify: `scripts/check.sh`
- Modify: `tests/python/test_repository_contract.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Modify: `README.md`
- Modify: `NOTICE`
- Modify: `docs/project-state.md`
- Create: `docs/remote-worker-bootstrap-review.md`

**Interfaces:**
- Consumes: every prior task.
- Produces: deterministic two-job fake certification, reproducible worker artifact hash, security scans, and an accurate offline release handoff.

- [ ] **Step 1: Write the failing complete fake integration**

The test must execute this exact scenario:

```python
def test_capture_preflight_one_rental_two_jobs_verified_outputs_and_destroy(self):
    system = FakeCloudRunSystem()

    first_capture = system.capture(native_capture(
        model="model-a", input_name="input-a.jpg", seed=11,
    ))
    preflight = system.preflight(first_capture)
    self.assertTrue(preflight.rentable)
    self.assertEqual(system.vast.mutations, [])

    session = system.quote_confirm_and_ready(
        preflight,
        offer_id="42",
        idempotency_key="session-key",
        duration_seconds=7200,
    )
    first = system.run_job(session, first_capture, "job-key-1")
    self.assertEqual(first.state, JobState.SUCCEEDED)
    self.assertTrue(all(a.local_verified for a in first.outputs))

    second_capture = system.capture(native_capture(
        model="model-a", input_name="input-b.jpg", seed=12,
    ))
    second = system.run_job(session, second_capture, "job-key-2")
    self.assertEqual(second.state, JobState.SUCCEEDED)
    self.assertEqual(system.vast.create_count, 1)
    self.assertEqual(system.worker.download_count("model-a"), 1)
    self.assertEqual(system.worker.download_count("input-a.jpg"), 1)
    self.assertEqual(system.worker.download_count("input-b.jpg"), 1)

    review = system.review_destroy(session)
    destroyed = system.destroy(session, confirmed(review))
    self.assertEqual(destroyed.state, SessionState.DESTROYED)
    self.assertEqual(system.vast.inventory, [])
    self.assertEqual(system.local_prompt_posts, [])
```

Add companion fake scenarios for:

- duplicate session confirmation and duplicate job submission;
- compatible new model/input delta without restart;
- compatible custom-node delta with one planned restart;
- incompatible custom-node revision requiring a new session;
- one repair plus one repair restart;
- ten-minute provisioning stall;
- execution/OOM failure returning to ready;
- restart adoption/resumed upload/resumed output;
- finite deadline destruction with abandoned incomplete output;
- residual inventory warning;
- one boot replacement only after absence;
- explicit no-limit mode;
- Agent Panel suggestion unable to approve, spend, execute shell, or destroy.

- [ ] **Step 2: Run the integration test and verify missing harness failure**

Run:

```sh
python3 -m unittest tests.python.test_fake_session_integration -v
```

Expected: FAIL because `FakeCloudRunSystem` certification fixtures are not implemented.

- [ ] **Step 3: Implement the fake system entirely inside tests**

Compose real service/repository/resolver/lifecycle/relay code with:

- `FakeVastProvider` recording search/get/create/list/destroy;
- `FakeWorkerClient` executing the real manifest/delta state transitions;
- deterministic in-memory artifacts and clocks;
- temporary private data/output directories;
- zero network, credential lookup, subprocess, provider SDK, or user Gold asset.

Do not weaken production interfaces for the fake.

- [ ] **Step 4: Build a deterministic worker artifact**

`scripts/build_worker_artifact.py` must:

1. include only `remote_worker/` plus shared `cloud_run/manifest.py` and `cloud_run/worker_protocol.py`;
2. reject symlinks, secrets, bytecode, VCS data, tests, and unknown files;
3. sort entries and normalize tar UID/GID/names/modes/mtime as in Task 6;
4. write to an explicit output path;
5. print only `<sha256>  <filename>`;
6. produce byte-identical archives on two consecutive builds.

The script prepares review material only; it does not upload or create a Vast template.

- [ ] **Step 5: Implement and test the fixed remote bootstrap**

`remote_worker/bootstrap.py` accepts one reviewed JSON lock containing the exact GitHub archive URL, 40-hex worker commit, 64-hex archive digest, protocol/version pins, and destination. It permits only HTTPS `github.com` archives whose path contains that same commit, streams to `.part`, verifies size and SHA-256, safely extracts using Task 11 rules, and launches:

```python
[
    sys.executable,
    "-m",
    "remote_worker.main",
    "--state-directory",
    "/var/lib/comfyui-cloud-run",
]
```

through `os.execv` or fixed-argv `create_subprocess_exec`. It accepts no workflow/manifest command, mutable branch, redirect to another origin, or environment-supplied shell.

Test:

```python
def test_bootstrap_downloads_one_commit_verifies_then_execs_fixed_worker(self):
    archive = deterministic_worker_archive()
    runner = RecordingExec()
    bootstrap = Bootstrap(
        transport=FakeTransport(archive),
        exec_runner=runner,
    )
    bootstrap.run(release_lock_for(archive))
    self.assertEqual(bootstrap.transport.urls, [
        "https://github.com/example/ComfyUI-Cloud-Run/archive/" + "a" * 40 + ".tar.gz",
    ])
    self.assertEqual(runner.argv[:3], [sys.executable, "-m", "remote_worker.main"])
    self.assertFalse(runner.shell_used)

def test_bootstrap_rejects_mutable_url_wrong_hash_redirect_and_shell_fields(self):
    for lock in (
        release_lock(url="https://github.com/example/repo/archive/main.tar.gz"),
        release_lock(sha256="b" * 64),
        release_lock(url="https://evil.example/archive/" + "a" * 40),
        release_lock(extra={"command": "curl x | sh"}),
    ):
        with self.assertRaises(BootstrapError):
            Bootstrap(transport=FakeTransport(b"bad")).run(lock)
```

- [ ] **Step 6: Prepare offline Gold output validation without the private asset**

`scripts/validate_gold_output.py` must accept explicit source/output paths, expected format/dimensions, and enlargement requirement. Using Pillow supplied by ComfyUI, it must fully decode both images, require exact output format/dimensions, reject truncation, compare a 32×32 luminance perceptual hash for recognizable composition, and scan an 8×8 grid for newly introduced nearly-black blocks, repeated tiles, and discontinuous seams. It returns nonzero for structural failure and prints no absolute input path or image metadata beyond format/dimensions/digests.

Create synthetic gradient/checkerboard images inside a temporary test directory:

```python
def test_gold_validator_accepts_coherent_upscale_and_rejects_corruption(self):
    source = make_synthetic_source(size=(64, 96))
    coherent = nearest_upscale(source, size=(128, 192))
    self.assertTrue(validate_gold_output(
        source, coherent, expected_size=(128, 192),
        expected_format="PNG", require_enlargement=True,
    ).passed)

    for output in (
        with_black_block(coherent),
        with_repeated_tile(coherent),
        truncate_file(coherent),
        image_with_size((127, 192)),
    ):
        self.assertFalse(validate_gold_output(
            source, output, expected_size=(128, 192),
            expected_format="PNG", require_enlargement=True,
        ).passed)
```

The future paid Gold still requires human visual confirmation of composition and tile/seam quality; this tool supplies deterministic structural evidence only. Never read, copy, rename, hash again, or place the user's private JPEG in a test directory during autonomous execution.

- [ ] **Step 7: Expand the deterministic repository gate**

Add:

```text
[check] fake reusable session
[check] worker protocol and artifact
[check] secret, origin, route, state, subprocess, and provider boundary scan
[check] public artifact scan
```

The AST/text scans must assert:

- browser routes equal the documented allowlist;
- worker routes equal Task 10's allowlist;
- session/job/transfer states equal Task 1;
- account-level provider actions remain only search/get-offer/create/list/get/destroy;
- worker provider action is only own-instance DELETE;
- no `stop_instance`, volume endpoint/action, `shell=True`, `os.system`, dynamic `eval`/`exec`, browser storage, `.innerHTML`, provider URL in frontend, or secret-bearing response key;
- external origins are only Vast API v0/v1, Comfy Registry, approved GitHub/HF/Civitai, and the configured exact R2 host;
- every bundled public artifact is text or approved worker source and contains no private image/workflow/key.

The final same-origin route allowlist is exactly:

```text
GET    /cloud-run/api/settings
PUT    /cloud-run/api/settings
POST   /cloud-run/api/captures
POST   /cloud-run/api/preflights
PUT    /cloud-run/api/mappings/{mapping_id}
POST   /cloud-run/api/integrations/agent-panel/suggestions
POST   /cloud-run/api/cache/artifacts/{artifact_id}
POST   /cloud-run/api/offers
POST   /cloud-run/api/sessions
POST   /cloud-run/api/sessions/{session_id}/confirm
GET    /cloud-run/api/sessions/{session_id}
POST   /cloud-run/api/sessions/{session_id}/jobs
GET    /cloud-run/api/sessions/{session_id}/jobs/{job_id}
GET    /cloud-run/api/sessions/{session_id}/jobs/{job_id}/events
GET    /cloud-run/api/sessions/{session_id}/jobs/{job_id}/previews/{preview_id}
GET    /cloud-run/api/sessions/{session_id}/jobs/{job_id}/artifacts/{artifact_id}
PUT    /cloud-run/api/sessions/{session_id}/deadline
POST   /cloud-run/api/sessions/{session_id}/destroy-review
DELETE /cloud-run/api/sessions/{session_id}
```

- [ ] **Step 8: Update docs to the implemented offline truth**

Document the full flow, supported resolver contracts, exact routes, session reuse, cost/deadline controls, verified output/destruction, no-volume policy, worker release-lock gate, and fake certification. `docs/remote-worker-bootstrap-review.md` must list the exact source files, build command, produced digest, Caddy boundary, worker outbound surface, own-instance deadline DELETE, and review evidence required before publication.

State explicitly:

- no project template has been published or pinned;
- no real rental or Gold run has occurred;
- the wrong ComfyRelay remote must not be used;
- Gold requires a separate bounded GO and destroys immediately after the first coherent verified output;
- the private Gold image/workflow is never part of repository fixtures.

- [ ] **Step 9: Run the complete gate twice and inspect the tree**

Run:

```sh
scripts/check.sh
scripts/check.sh
git diff --check
git status --short
```

Expected: both gates pass with identical worker artifact digest; status contains only intended Task 18 files before commit.

- [ ] **Step 10: Commit offline certification**

```sh
git add tests/python/test_fake_session_integration.py \
  remote_worker/bootstrap.py tests/python/test_worker_bootstrap.py \
  scripts/build_worker_artifact.py scripts/validate_gold_output.py \
  tests/python/test_gold_output_validation.py scripts/check.sh \
  tests/python/test_repository_contract.py \
  tests/python/test_no_mutation_surface.py README.md NOTICE \
  docs/project-state.md docs/remote-worker-bootstrap-review.md
git commit -m "test: certify reusable Vast sessions offline"
```

---

### Task 19: Perform final verification and stop before publication or spend

**Files:**
- Verify only: all repository files
- Do not create: a live `worker-release.json`
- Do not modify: external ComfyUI, LoRA Dataset Studio, the forbidden cockpit, or any remote repository

**Interfaces:**
- Consumes: the complete offline implementation.
- Produces: evidence-backed handoff ready for separate worker-review/publication authorization.

- [ ] **Step 1: Invoke the completion-verification skill**

Read and follow `superpowers:verification-before-completion` before claiming any success.

- [ ] **Step 2: Verify repository identity and history**

Run:

```sh
git status --short
git branch --show-current
git log --oneline --decorate -25
git remote -v
```

Expected: branch is `feat/vast-cloud-run-lifecycle`; worktree is clean after the planned commits; the displayed ComfyRelay remote is recorded as forbidden and receives no push.

- [ ] **Step 3: Run the deterministic gate with fresh evidence**

Run:

```sh
scripts/check.sh
git diff --check
```

Expected: all Python/Node/fake-session/compile/syntax/security/artifact checks pass and no whitespace error exists.

- [ ] **Step 4: Verify development link without changing it**

Run:

```sh
readlink /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run
```

Expected:

```text
/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes
```

- [ ] **Step 5: Audit secrets and forbidden assets one final time**

Run the repository's public-artifact/secret scan directly, plus:

```sh
git ls-files | rg -i '\.(jpg|jpeg|png|webp|mp4|mov)$'
git grep -n -E 'VAST_API_KEY=|R2_SECRET|hf_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9_=.-]{20,}'
```

Expected: no private Gold asset, personal workflow, or credential finding. Approved UI icon/font assets, if any, must be explainable and unrelated to the Gold input.

- [ ] **Step 6: Record the exact autonomous stopping point**

Report:

1. final commit and clean status;
2. fresh test counts and worker artifact digest;
3. no live release lock, publication, provider mutation, or paid GPU;
4. current compatibility limits and unresolved mappings;
5. next gated action: human review of `docs/remote-worker-bootstrap-review.md`, correct GitHub repository creation, immutable artifact publication/template pin, then a separately bounded paid Gold GO.

Do not push, publish, create a Vast template, search using real credentials, or rent a GPU.

## Spec Coverage Audit

| Approved requirement | Implemented and certified by |
| --- | --- |
| Exact official canvas compilation; no local `/prompt`; local Run unchanged | Tasks 2, 3, 17 |
| Core/custom nodes, Registry/Git/Agent/manual precedence | Tasks 4, 5, 8 |
| Models, inputs, immutable source, size, hash, destination | Tasks 4, 6 |
| Output allowance, 20 GiB headroom, minimum 80 GiB | Tasks 6, 8, 9 |
| Optional private R2 cache and short object-scoped operations | Task 7 |
| Complete quote, exact revalidation, durable idempotent create | Tasks 9, 15 |
| Public project worker/template stays locked until reviewed | Tasks 9, 18, 19 |
| Signed worker manifest and authenticated local-only relay boundary | Tasks 10, 14 |
| Concurrent resume, `.part`, exact verification, atomic install | Task 11 |
| Native `/object_info`, one repair, restart budgets, 10-minute stall | Task 12 |
| Native `/prompt`, progress/node/previews/errors/history/results | Tasks 13, 14, 17 |
| One session, one job at a time, two compatible sequential jobs | Tasks 15, 18 |
| Empty/model/input/custom-node deltas and incompatible-session refusal | Tasks 12, 15, 18 |
| Default two-hour limit, alerts, extensions, explicit no-limit | Tasks 13, 16, 17 |
| Worker own-instance deadline destruction while Mac is disconnected | Task 13 |
| Strengthened manual destroy and inventory-proven billing end | Tasks 16, 17, 18 |
| Restart adoption, transfer resume, residual billing, one safe replacement | Tasks 14, 15, 16, 18 |
| No Vast volume, Stop, arbitrary shell, browser secret, or account key in worker | Global constraints; Tasks 10–13, 18 |
| Optional Agent Panel suggestions without authority | Tasks 5, 8, 18 |
| Offline fake certification and security/public-artifact gate | Tasks 18, 19 |
| Gold structural validator, private-asset exclusion, separate bounded paid GO | Tasks 18, 19 |

No approved spec requirement is intentionally omitted. The optional authenticated remote-debug link is not exposed in V1; the spec explicitly permits it rather than requiring it, and omitting it reduces the attack surface.

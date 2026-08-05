# Controller-Owned Remote Worker Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the next paid Vast session authenticate deterministically, reach `ready`, run the certified Wallpaper Outpaint workflow remotely, retrieve a verified output, and prove that the GPU was destroyed.

**Architecture:** The local controller generates and persists a fresh 64-lowercase-hex worker-boundary token before every Vast create. The create request injects that token and the expected session ID into the one instance. The worker gateway and Caddy use only that controller-owned token; the controller ignores Vast's `jupyter_token`. A returned HTTP `401` is a typed terminal configuration failure that triggers immediate verified destruction.

**Tech stack:** Python 3.13, `unittest`, aiohttp-compatible Vast transport, Caddy, SQLite, ComfyUI Desktop frontend 1.47.10, immutable GitHub worker releases, private Vast templates.

## Start here after the context reset

- [ ] Call `get_goal` before every other tool. It was `null`; do not create a Goal.
- [ ] Announce and use, in this order, `certifying-comfyui-cloud-workflows`, `superpowers:executing-plans`, and `superpowers:verification-before-completion`. Invoke `superpowers:systematic-debugging` only if behavior or a test fails unexpectedly.
- [ ] Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`. Do not create another worktree.
- [ ] Read the approved design at `docs/superpowers/specs/2026-08-02-controller-owned-worker-boundary-design.md` and this plan completely.
- [ ] Verify `git status --short --branch` and `git log -4 --oneline`. The history must include `f173b33` (`docs: design controller-owned worker boundary`). Preserve unrelated user changes if any appear.
- [ ] Confirm read-only that Vast inventory is empty and that no Cloud Run session says billing may continue. The previous instance `46539955` is historical evidence, not a recovery target.
- [ ] Keep the old immutable release, template `c9d083b55074441a53eff655797e0a6e`, and current local lock untouched until the publication gate. Never overwrite, edit, republish, or delete them.
- [ ] Use focused tests during implementation. Run `scripts/check.sh` once at the final offline gate; do not replay older live-test batteries.
- [ ] Use the existing ComfyUI Desktop backend on `127.0.0.1:8188`. Revalidate its owner before any restart and never launch a second backend.
- [ ] For live UI work, communicate through the ComfyUI Agent Panel and direct same-origin status reads. Do not depend on screenshots, construct an offline prompt, or POST to `/prompt`.
- [ ] The human presses every paid confirm, no-limit acknowledgement, remote-run, and destroy button. Code or an agent must never press those controls on the human's behalf.

## Completion boundary

There are two distinct milestones:

1. **Offline-ready:** focused regressions and the one complete repository gate pass on a clean, pushed commit.
2. **Gold-validated:** a new immutable release/template reaches authenticated `ready`, the real workflow succeeds remotely, its output is verified, and Vast inventory is empty after the human destroys the GPU.

Do not say the production problem is fixed at the first milestone. Only the second milestone proves the real path.

---

### Task 1: Define one strict controller-owned boundary contract

**Files:**

- Modify: `cloud_run/worker_protocol.py`
- Modify: `cloud_run/models.py`
- Modify: `cloud_run/worker_client.py`
- Modify: `tests/python/test_worker_protocol.py`
- Modify: `tests/python/test_models.py`
- Modify: `tests/python/test_worker_client.py`
- Modify private-token fixtures in: `tests/python/test_repository.py`, `tests/python/test_routes.py`, `tests/python/test_session_service.py`

**Interfaces:**

- `BOUNDARY_TOKEN_ENVIRONMENT == "CLOUD_RUN_BOUNDARY_TOKEN"`
- `SESSION_ID_ENVIRONMENT == "CLOUD_RUN_SESSION_ID"`
- `is_boundary_token(value) -> bool`
- `is_worker_session_id(value) -> bool`

- [ ] **Step 1: Write the failing validation tests**

Add focused tests proving:

- exactly 64 lowercase hexadecimal characters are accepted;
- 63/65 characters, uppercase hex, whitespace, punctuation, bytes, and `None` are rejected;
- worker session IDs match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}`;
- `CloudSession.provider_token` and `CloudAttempt.provider_token`, when present, must satisfy the boundary contract;
- `WorkerClient` rejects every nonconforming boundary token before transport;
- neither public model payload exposes the token.

Use fixed test values such as `"a" * 64`; never log a generated token.

- [ ] **Step 2: Run only the new tests and observe RED**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_protocol \
  tests.python.test_models.LifecycleModelTests.test_boundary_tokens_use_the_shared_private_contract \
  tests.python.test_worker_client.WorkerClientTests.test_client_rejects_invalid_boundary_tokens_before_transport \
  -v
```

Expected: the new constants/helpers do not exist and the new strict-validation assertions fail.

- [ ] **Step 3: Implement the shared contract**

Add this contract to `cloud_run/worker_protocol.py`:

```python
BOUNDARY_TOKEN_ENVIRONMENT = "CLOUD_RUN_BOUNDARY_TOKEN"
SESSION_ID_ENVIRONMENT = "CLOUD_RUN_SESSION_ID"
_BOUNDARY_TOKEN = re.compile(r"[0-9a-f]{64}")
_WORKER_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}")


def is_boundary_token(value):
    return (
        isinstance(value, str)
        and _BOUNDARY_TOKEN.fullmatch(value) is not None
    )


def is_worker_session_id(value):
    return (
        isinstance(value, str)
        and _WORKER_SESSION_ID.fullmatch(value) is not None
    )
```

Import `is_boundary_token` in `cloud_run/models.py`. Validate the current `CloudSession.provider_token` in `_validate()`, and validate the resulting current-or-changed `CloudAttempt.provider_token` inside every `transition()`. Keep `None` valid for pre-confirmation and destroyed records.

Import `is_boundary_token` and `is_worker_session_id` in `cloud_run/worker_client.py`. Replace only the constructor's loose provider-token check and session-ID check; keep the existing identifier validation for job/artifact protocol fields.

Mechanically replace loose private-token fixtures in the files listed above with deterministic 64-lowercase-hex values. Preserve tests that deliberately submit an invalid token, and never weaken production validation to accommodate a fixture. The current Desktop database has only destroyed/failed historical rows and no retained provider token, so no data migration is required for this installation.

- [ ] **Step 4: Re-run the focused tests and observe GREEN**

Run the Step 2 command again. Expected: all named tests pass with zero errors.

- [ ] **Step 5: Commit the shared contract**

```sh
git add cloud_run/worker_protocol.py cloud_run/models.py cloud_run/worker_client.py tests/python/test_worker_protocol.py tests/python/test_models.py tests/python/test_worker_client.py tests/python/test_repository.py tests/python/test_routes.py tests/python/test_session_service.py
git diff --cached --check
git commit -m "fix: define controller-owned worker boundary"
```

---

### Task 2: Persist and inject the boundary before every Vast create

**Files:**

- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/service.py`
- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_service.py`
- Modify fake provider signatures in: `tests/python/test_lifecycle.py`, `tests/python/test_fake_lifecycle_integration.py`, `tests/python/test_fake_session_integration.py`

**Interfaces:**

- `vast.create_instance(api_key, *, offer_id, disk_gb, label, release, boundary_token, session_id, session=None)` requires both boundary values.
- Every provider fake records the boundary token and expected session ID without including either in `repr` or failure messages.

- [ ] **Step 1: Add failing transport and durability regressions**

In `tests/python/test_vast.py`, extend the create-request test to require this exact request field:

```python
"env": (
    "-e CLOUD_RUN_BOUNDARY_TOKEN=" + "a" * 64
    + " -e CLOUD_RUN_SESSION_ID=session-1"
),
```

Add rejection cases for malformed token and session ID and assert the fake HTTP session received no request.

In `tests/python/test_service.py`, make `FakeProvider.create_instance()` accept and record `boundary_token` and `session_id`. Extend the existing initial-confirmation, duplicate-confirmation, concurrent-confirmation, and ambiguous-create tests to prove:

- the session/attempt is already durable in `creating` with its token before `create_instance()` runs;
- boundary token and HMAC `session_secret_hex` are both 64 lowercase hex and are different;
- the passed session ID equals the durable session or attempt ID;
- duplicate and ambiguous confirmation preserve the original token and issue one create only;
- public payloads and sanitized errors contain neither secret.

- [ ] **Step 2: Run the focused tests and observe RED**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_vast.VastLifecycleRequestTests.test_create_uses_only_the_injected_reviewed_project_template \
  tests.python.test_vast.VastLifecycleRequestTests.test_create_rejects_invalid_worker_boundary_before_request \
  tests.python.test_service.CloudRunServiceTests.test_paid_confirmation_persists_secret_and_intent_before_one_put \
  tests.python.test_service.CloudRunServiceTests.test_ambiguous_initial_create_adopts_one_consumed_instance \
  tests.python.test_service.CloudRunServiceTests.test_concurrent_paid_confirmations_issue_exactly_one_create \
  tests.python.test_service.CloudRunServiceTests.test_attempt_and_creating_state_exist_before_the_only_paid_call \
  -v
```

Expected: the create interface lacks the new required context and the request has no runtime `env`.

- [ ] **Step 3: Add a fail-closed Vast environment builder**

In `cloud_run/vast.py`, import the two environment names and validators. Add:

```python
def _worker_environment(boundary_token, session_id):
    if (
        not is_boundary_token(boundary_token)
        or not is_worker_session_id(session_id)
    ):
        raise VastConfigurationError(
            "A valid worker boundary context is required."
        )
    return (
        "-e " + BOUNDARY_TOKEN_ENVIRONMENT + "=" + boundary_token
        + " -e " + SESSION_ID_ENVIRONMENT + "=" + session_id
    )
```

Require `boundary_token` and `session_id` as keyword-only `create_instance()` arguments, validate before `_run_with_session()`, and add the returned string as JSON field `env`. Keep the private template hash, label, and disk checks unchanged.

- [ ] **Step 4: Generate and persist independent secrets before mutation**

Extend `VastProvider.create_instance()` in `cloud_run/service.py` with the required arguments.

For the reusable session path, the transition to `SessionState.CREATING` must atomically include:

```python
provider_token=secrets.token_hex(32),
session_secret_hex=secrets.token_hex(32),
```

Pass `session.provider_token` and `session.session_id` to the provider. The existing state/idempotency gate ensures the values are generated once before the first network call and reused after an ambiguous outcome.

For the legacy attempt path, persist one fresh `provider_token` in the `AttemptState.CREATING` transition and pass it with `attempt.attempt_id`. This keeps every `create_instance()` call on the same required contract.

Update every fake provider signature found by:

```sh
rg -n "async def create_instance|create_instance\(" cloud_run tests/python
```

Do not make the new arguments optional merely to satisfy a fake.

- [ ] **Step 5: Re-run the focused tests and observe GREEN**

Run the Step 2 command. Expected: all named tests pass, each create has the exact inert `env`, and no secret appears in a public value.

- [ ] **Step 6: Commit the controller-to-Vast path**

```sh
git add cloud_run/vast.py cloud_run/service.py tests/python/test_vast.py tests/python/test_service.py tests/python/test_lifecycle.py tests/python/test_fake_lifecycle_integration.py tests/python/test_fake_session_integration.py
git diff --cached --check
git commit -m "fix: inject worker boundary into Vast creates"
```

---

### Task 3: Make the remote gateway consume only the project token

**Files:**

- Modify: `remote_worker/gateway.py`
- Modify: `remote_worker/Caddyfile`
- Modify: `remote_worker/main.py`
- Modify: `tests/python/test_worker_gateway.py`
- Modify: `tests/python/test_worker_server.py`

- [ ] **Step 1: Write failing gateway/Caddy regressions**

Change gateway tests to provide only `CLOUD_RUN_BOUNDARY_TOKEN`. Prove:

- a valid 64-lowercase-hex token starts the fixed Caddy/worker argv;
- `JUPYTER_TOKEN` and `OPEN_BUTTON_TOKEN`, even when valid-looking, never satisfy gateway validation;
- Caddy receives `CLOUD_RUN_BOUNDARY_TOKEN` and the Python worker child does not;
- the worker child still receives the validated `CLOUD_RUN_SESSION_ID`;
- the Caddyfile matches exactly `Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}` and still strips both inbound authorization and boundary-marker headers before setting its internal marker.

- [ ] **Step 2: Run the focused tests and observe RED**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_gateway \
  tests.python.test_worker_server.WorkerApplicationTests.test_proxy_and_worker_bindings_are_exact_and_loopback_only \
  -v
```

Expected: current gateway requires `JUPYTER_TOKEN` and current Caddyfile references it.

- [ ] **Step 3: Switch gateway and Caddy without fallback**

In `remote_worker/gateway.py`:

- import the shared token/session environment names and `is_boundary_token`;
- delete the 4096-byte provider-token regex and read only `CLOUD_RUN_BOUNDARY_TOKEN`;
- pass that same environment name only to Caddy;
- build the worker allowlist with `SESSION_ID_ENVIRONMENT`;
- never copy the boundary token into `worker_environment`.

In `remote_worker/main.py`, read the session ID through `SESSION_ID_ENVIRONMENT` rather than a second literal.

In `remote_worker/Caddyfile`, change only the bearer variable to `{$CLOUD_RUN_BOUNDARY_TOKEN}`. Keep port `8765`, loopback target `127.0.0.1:8766`, header stripping, and authenticated internal marker unchanged.

- [ ] **Step 4: Re-run focused tests and observe GREEN**

Run the Step 2 command. Expected: all gateway/server tests pass and provider-owned token names are rejected.

- [ ] **Step 5: Commit the worker boundary**

```sh
git add remote_worker/gateway.py remote_worker/Caddyfile remote_worker/main.py tests/python/test_worker_gateway.py tests/python/test_worker_server.py
git diff --cached --check
git commit -m "fix: isolate remote gateway boundary token"
```

---

### Task 4: Ignore Vast's token during boot, recovery, and replacement

**Files:**

- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_fake_lifecycle_integration.py`
- Modify: `tests/python/test_fake_session_integration.py`

- [ ] **Step 1: Write the failing mismatch and rotation tests**

Change the normalized-instance test so a raw `jupyter_token` is deliberately present but absent from normalized output.

In session lifecycle tests, seed the durable session with `provider_token="a" * 64` and make the instance report `jupyter_token="intentionally-wrong-provider-token"`. Prove boot/recovery uses the durable `a` token, derives only the mapped base URL from the instance, and never overwrites the token.

Add replacement tests for both reusable session and legacy attempt paths. Patch the token generator to return `"b" * 64`; prove the replacement transition persists `b` before create, passes `b` to the provider, never reuses `a`, and preserves `b` if an ambiguous replacement is adopted from inventory.

In `tests/python/test_fake_session_integration.py`, make `FakeVastProvider` record the boundary context passed at create and return an instance containing an intentionally wrong `jupyter_token`. Add `test_controller_owned_boundary_ignores_provider_jupyter_token_through_full_fake_run` that reaches `ready`, runs one job, verifies output, destroys once, and ends with empty inventory.

- [ ] **Step 2: Run the focused tests and observe RED**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_vast.VastLifecycleRequestTests.test_list_get_destroy_and_url_derivation_are_normalized \
  tests.python.test_lifecycle.SessionLifecycleTests.test_boot_adopts_exact_worker_mapping_and_private_boundary_token \
  tests.python.test_lifecycle.SessionLifecycleTests.test_session_replacement_rotates_boundary_before_create \
  tests.python.test_lifecycle.RecoveryAndReplacementTests.test_attempt_replacement_rotates_boundary_before_create \
  tests.python.test_fake_session_integration.FakeReusableSessionIntegrationTests.test_controller_owned_boundary_ignores_provider_jupyter_token_through_full_fake_run \
  -v
```

Expected: current normalization exposes the provider token, activation overwrites the durable token, and replacements do not rotate it.

- [ ] **Step 3: Remove provider credential coupling**

Remove `jupyter_token` from `_normalize_instance()` in `cloud_run/vast.py`.

Change lifecycle connection resolution to receive the durable session:

```python
def _session_connection(self, session, instance):
```

It must require running/ready status, a valid mapped worker base URL, and `is_boundary_token(session.provider_token)`. It returns only the base URL. `_activate_session_instance()` updates `worker_base_url`, instance ID, and sanitized state; it never writes `provider_token`.

Before each authorized replacement `PUT`, generate a fresh boundary token, include it in the durable `creating` transition, and pass it with the existing session/attempt ID to `create_instance()`. Keep the original HMAC session secret for the reusable session.

- [ ] **Step 4: Re-run the focused tests and observe GREEN**

Run the Step 2 command. Expected: the deliberate Vast-token mismatch has no effect, replacement rotation is durable, and the complete fake lifecycle succeeds with one instance.

- [ ] **Step 5: Commit lifecycle decoupling**

```sh
git add cloud_run/vast.py cloud_run/lifecycle.py tests/python/test_vast.py tests/python/test_lifecycle.py tests/python/test_fake_lifecycle_integration.py tests/python/test_fake_session_integration.py
git diff --cached --check
git commit -m "fix: keep controller boundary across worker lifecycle"
```

---

### Task 5: Turn a real boundary `401` into immediate verified destruction

**Files:**

- Modify: `cloud_run/worker_client.py`
- Modify: `cloud_run/session_service.py`
- Modify: `tests/python/test_worker_client.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_lifecycle.py`

**Interface:**

```python
class WorkerBoundaryAuthenticationError(WorkerClientError):
    """The worker boundary rejected its controller-owned token."""
```

- [ ] **Step 1: Write failing typed-error tests**

Add a worker-client test whose transport returns status `401`, non-JSON bytes, misleading headers, and a body containing a private marker. Require the dedicated exception, the exact static message `Remote worker boundary authentication failed.`, and absence of the marker from `str()` and `repr()`.

Add bootstrap and recovery tests in `tests/python/test_session_service.py` where worker `health()` raises `WorkerBoundaryAuthenticationError`. Require `TerminalProvisioningError` with that same static message. Keep connection refusal, timeout, and generic `WorkerClientError` mapped to the existing retryable `SessionExecutionError`.

Extend the existing immediate terminal-destruction lifecycle test to assert one create total, one destroy, one inventory verification, no replacement search, no sleeps to the 15-minute deadline, and no billing warning after verified absence.

- [ ] **Step 2: Run the focused tests and observe RED**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_client.WorkerClientTests.test_boundary_401_is_typed_and_never_echoes_response \
  tests.python.test_session_service.ReusableSessionTests.test_bootstrap_boundary_401_is_terminal \
  tests.python.test_session_service.ReusableSessionTests.test_recovery_boundary_401_is_terminal \
  tests.python.test_lifecycle.SessionLifecycleTests.test_terminal_provisioning_failure_destroys_and_verifies_immediately \
  -v
```

If the actual session-service test class differs, place the two tests beside the existing bootstrap/recovery authentication tests and run their fully qualified discovered names. Do not broaden the run to the whole suite at this step.

- [ ] **Step 3: Implement typed, sanitized handling**

In `_parse_json_response()`, after validating the response object, integer status, bytes body, and maximum body size—but before reading headers or parsing the body—raise `WorkerBoundaryAuthenticationError("Remote worker boundary authentication failed.")` for status `401`. Other non-200/202 statuses remain generic.

In both `SessionService.bootstrap_session()` and `recover_session()`, catch that specific exception before the generic exception and raise:

```python
raise TerminalProvisioningError(
    "Remote worker boundary authentication failed."
) from None
```

The existing lifecycle terminal handler must perform the destruction; do not add a second destroy path in `SessionService`.

- [ ] **Step 4: Re-run the focused tests and observe GREEN**

Run the Step 2 tests by their actual qualified names. Expected: typed static failure, zero leaked response bytes, immediate single destruction, empty inventory, and no replacement at `max_instance_creates=1`.

- [ ] **Step 5: Commit fail-fast behavior**

```sh
git add cloud_run/worker_client.py cloud_run/session_service.py tests/python/test_worker_client.py tests/python/test_session_service.py tests/python/test_lifecycle.py
git diff --cached --check
git commit -m "fix: destroy on worker boundary rejection"
```

---

### Task 6: Harden documentation, scans, and complete offline verification

**Files:**

- Modify: `README.md`
- Modify: `scripts/check.sh`
- Modify test fixtures containing loose provider tokens as required by the new strict contract
- Verify: all production and test files changed in Tasks 1–5

- [ ] **Step 1: Update the runtime explanation**

Replace the README statement that Caddy receives Vast's Jupyter token. State instead that the controller generates a per-instance boundary token, injects it at create time, Caddy alone receives it, and the Python worker receives only the session ID plus its existing allowlisted runtime variables. State explicitly that `JUPYTER_TOKEN` and `OPEN_BUTTON_TOKEN` are not fallback credentials.

Do not rewrite the historical 2026-07-31 design document; the approved 2026-08-02 design supersedes that provider-owned-token decision.

- [ ] **Step 2: Add a production regression scan**

In `scripts/check.sh`, add the relevant boundary production files to a static scan that fails if any of these provider credential literals return:

```text
JUPYTER_TOKEN
OPEN_BUTTON_TOKEN
jupyter_token
```

The scan must cover `cloud_run/vast.py`, `cloud_run/lifecycle.py`, `remote_worker/gateway.py`, and `remote_worker/Caddyfile`. It must not scan historical design documents.

- [ ] **Step 3: Normalize test fixtures without weakening production**

Use this inventory:

```sh
rg -n "provider_token=|jupyter_token|JUPYTER_TOKEN|OPEN_BUTTON_TOKEN" cloud_run remote_worker tests/python README.md scripts/check.sh
```

Change private test tokens to deterministic 64-lowercase-hex values. Deliberately wrong provider tokens remain only in tests whose purpose is proving they are ignored. Do not add compatibility fallbacks.

- [ ] **Step 4: Run the focused boundary regression set**

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_protocol \
  tests.python.test_models.LifecycleModelTests.test_boundary_tokens_use_the_shared_private_contract \
  tests.python.test_vast.VastLifecycleRequestTests.test_create_uses_only_the_injected_reviewed_project_template \
  tests.python.test_service.CloudRunServiceTests.test_paid_confirmation_persists_secret_and_intent_before_one_put \
  tests.python.test_worker_gateway.GatewayProcessTests.test_splits_caddy_token_from_the_explicit_worker_allowlist \
  tests.python.test_worker_client.WorkerClientTests.test_boundary_401_is_typed_and_never_echoes_response \
  tests.python.test_session_service.ReusableSessionTests.test_bootstrap_boundary_401_is_terminal \
  tests.python.test_session_service.ReusableSessionTests.test_recovery_boundary_401_is_terminal \
  tests.python.test_lifecycle.SessionLifecycleTests.test_boot_adopts_exact_worker_mapping_and_private_boundary_token \
  tests.python.test_lifecycle.SessionLifecycleTests.test_terminal_provisioning_failure_destroys_and_verifies_immediately \
  tests.python.test_fake_session_integration.FakeReusableSessionIntegrationTests.test_controller_owned_boundary_ignores_provider_jupyter_token_through_full_fake_run \
  -v
```

Expected: all selected tests pass. If an unrelated old test fails unexpectedly, invoke `superpowers:systematic-debugging`, establish the cause, and change only what the new contract requires.

- [ ] **Step 5: Commit documentation and scans**

```sh
git add README.md scripts/check.sh tests/python
git diff --cached --check
git commit -m "docs: document controller-owned worker boundary"
```

If no fixture or test file remains uncommitted, `git add` simply stages the two intended production-support files.

- [ ] **Step 6: Run the complete repository gate once on committed code**

```sh
scripts/check.sh
```

Expected: Python tests, the fake reusable session, deterministic worker artifacts/releases, synthetic Gold validator, Node tests, compilation, syntax checks, secret scans, route checks, and provider-boundary checks all pass.

Do not rerun the complete gate if it passes. If it fails due to the new work, diagnose, add the smallest regression fix, commit it, and rerun because fresh complete evidence is then required.

- [ ] **Step 7: Perform final offline inspection**

```sh
git diff --check
git status --short --branch
git log --oneline --decorate -10
rg -n "JUPYTER_TOKEN|OPEN_BUTTON_TOKEN|jupyter_token" cloud_run/vast.py cloud_run/lifecycle.py remote_worker/gateway.py remote_worker/Caddyfile
```

Expected: clean tree; the final `rg` returns no matches; the complete gate is fresh for the current commit. Call this **offline-ready**, not production-fixed.

---

### Task 7: Push the reviewed offline fix to the existing draft PR

**External scope already requested:** update the existing branch/PR so GitHub records the incident design and correction. Do not merge the PR or mark it ready.

- [ ] **Step 1: Reconfirm exact local scope**

```sh
git status --short --branch
git diff 45e43e2d0bcd1e16eea3be098b212999a6dd7ad9..HEAD --stat
git log 45e43e2d0bcd1e16eea3be098b212999a6dd7ad9..HEAD --oneline
```

Expected: only the design/plan and controller-boundary implementation commits are present; tree is clean.

- [ ] **Step 2: Push only the existing branch**

```sh
git push origin fix/vast-template-live-audit
```

- [ ] **Step 3: Verify branch and PR identity**

```sh
git fetch origin fix/vast-template-live-audit
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/fix/vast-template-live-audit)"
gh pr view 3 --repo wuraaang/ComfyUI-Cloud-Run --json number,state,isDraft,headRefName,headRefOid,url
```

Expected: PR `#3` remains draft, points to this exact head, and nothing has been released or changed on Vast.

Stop and report offline evidence if explicit release authorization is not yet present.

---

### Task 8: Build and publish one new immutable worker release

**Authorization gate:** Before any `gh release create`, obtain an explicit user message authorizing one new immutable release for the exact verified commit. The old release remains immutable historical evidence. This permission does not authorize a Vast template or rental.

- [ ] **Step 1: Reconfirm the release commit**

```sh
git status --short --branch
git fetch origin fix/vast-template-live-audit
CLOUD_RUN_RELEASE_HEAD=$(git rev-parse HEAD)
test "$CLOUD_RUN_RELEASE_HEAD" = "$(git rev-parse origin/fix/vast-template-live-audit)"
```

Require a clean tree, a 40-lowercase-hex head, and the fresh passing `scripts/check.sh` evidence from Task 6.

- [ ] **Step 2: Build twice in a private root and compare bytes**

```sh
CLOUD_RUN_RELEASE_ROOT=$(mktemp -d /Users/wuraaang/.cloud-run-worker-release.XXXXXX)
chmod 700 "$CLOUD_RUN_RELEASE_ROOT"
mkdir -m 700 "$CLOUD_RUN_RELEASE_ROOT/first"
mkdir -m 700 "$CLOUD_RUN_RELEASE_ROOT/second"
python3 scripts/build_worker_release_bundle.py --repository-root "$(pwd -P)" --output-directory "$CLOUD_RUN_RELEASE_ROOT/first" --worker-commit "$CLOUD_RUN_RELEASE_HEAD"
python3 scripts/build_worker_release_bundle.py --repository-root "$(pwd -P)" --output-directory "$CLOUD_RUN_RELEASE_ROOT/second" --worker-commit "$CLOUD_RUN_RELEASE_HEAD"
```

Load the validated asset name and tag from `first/release-metadata.json`:

```sh
CLOUD_RUN_RELEASE_ASSET_NAME=$(python3 -c 'import json,re,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["asset_name"]; assert re.fullmatch(r"comfyui-cloud-run-worker-[0-9a-f]{40}-[0-9a-f]{64}\.tar\.gz", value); print(value)' "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json")
CLOUD_RUN_RELEASE_TAG=$(python3 -c 'import json,re,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["tag"]; assert re.fullmatch(r"worker-v1-[0-9a-f]{40}", value); print(value)' "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json")
```

Then run:

```sh
cmp "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json" "$CLOUD_RUN_RELEASE_ROOT/second/release-metadata.json"
cmp "$CLOUD_RUN_RELEASE_ROOT/first/$CLOUD_RUN_RELEASE_ASSET_NAME" "$CLOUD_RUN_RELEASE_ROOT/second/$CLOUD_RUN_RELEASE_ASSET_NAME"
```

Expected: both comparisons exit `0`. Keep the private root for template/lock generation and later verification.

- [ ] **Step 3: Prove the exact tag does not exist**

Run this read:

```sh
gh release view "$CLOUD_RUN_RELEASE_TAG" --repo wuraaang/ComfyUI-Cloud-Run
```

It must fail specifically because the release/tag is not found. Authentication, network, or permission failures are blockers, not proof of absence. If the tag exists, stop; never edit, replace, or upload with `--clobber`.

- [ ] **Step 4: Create a draft and upload exactly one asset**

After rechecking the authorization message:

```sh
gh release create "$CLOUD_RUN_RELEASE_TAG" --repo wuraaang/ComfyUI-Cloud-Run --target "$CLOUD_RUN_RELEASE_HEAD" --title "$CLOUD_RUN_RELEASE_TAG" --notes "Controller-owned Remote Worker boundary for commit $CLOUD_RUN_RELEASE_HEAD." --draft
gh release upload "$CLOUD_RUN_RELEASE_TAG" "$CLOUD_RUN_RELEASE_ROOT/first/$CLOUD_RUN_RELEASE_ASSET_NAME" --repo wuraaang/ComfyUI-Cloud-Run
```

Never use `--clobber`.

- [ ] **Step 5: Verify the draft before publication**

Read release/asset metadata through `gh api`. Require exact target commit, one asset, exact name, byte size, `sha256:` digest, and browser download URL from `release-metadata.json`. Load the expected identity, download once, and compare:

```sh
CLOUD_RUN_RELEASE_SIZE=$(python3 -c 'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["worker_archive_size_bytes"]; assert isinstance(value, int) and value > 0; print(value)' "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json")
CLOUD_RUN_RELEASE_SHA256=$(python3 -c 'import json,re,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["worker_archive_sha256"]; assert re.fullmatch(r"[0-9a-f]{64}", value); print(value)' "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json")
mkdir -m 700 "$CLOUD_RUN_RELEASE_ROOT/downloaded"
gh release download "$CLOUD_RUN_RELEASE_TAG" --repo wuraaang/ComfyUI-Cloud-Run --pattern "$CLOUD_RUN_RELEASE_ASSET_NAME" --dir "$CLOUD_RUN_RELEASE_ROOT/downloaded"
test "$(stat -f %z "$CLOUD_RUN_RELEASE_ROOT/downloaded/$CLOUD_RUN_RELEASE_ASSET_NAME")" = "$CLOUD_RUN_RELEASE_SIZE"
test "$(shasum -a 256 "$CLOUD_RUN_RELEASE_ROOT/downloaded/$CLOUD_RUN_RELEASE_ASSET_NAME" | awk '{print $1}')" = "$CLOUD_RUN_RELEASE_SHA256"
```

`scripts/check.sh` already supplied the fresh deterministic archive and bootstrap tests for this exact commit; do not start Caddy or ComfyUI here.

If any field or byte differs, leave the release as a draft and stop.

- [ ] **Step 6: Publish once and verify immutability**

```sh
gh release edit "$CLOUD_RUN_RELEASE_TAG" --repo wuraaang/ComfyUI-Cloud-Run --draft=false
gh release verify "$CLOUD_RUN_RELEASE_TAG" --repo wuraaang/ComfyUI-Cloud-Run
gh release verify-asset "$CLOUD_RUN_RELEASE_TAG" "$CLOUD_RUN_RELEASE_ROOT/downloaded/$CLOUD_RUN_RELEASE_ASSET_NAME" --repo wuraaang/ComfyUI-Cloud-Run
```

Read it back once more and require exact commit, published immutable state, one unchanged asset, size, digest, and URL. Do not remove the private build root yet.

---

### Task 9: Create one new private Vast template and rotate the local lock

**Authorization gate:** Obtain explicit user authorization for exactly one new private template plus local lock rotation/restart. Do not reuse or delete template `c9d083b55074441a53eff655797e0a6e`. This permission does not authorize a rental.

- [ ] **Step 1: Audit and render in a private directory**

```sh
CLOUD_RUN_TEMPLATE_ROOT=$(mktemp -d /Users/wuraaang/.cloud-run-worker-template.XXXXXX)
chmod 700 "$CLOUD_RUN_TEMPLATE_ROOT"
python3 scripts/publish_worker_template.py audit-base --output-directory "$CLOUD_RUN_TEMPLATE_ROOT"
python3 scripts/render_worker_template.py --repository-root "$(pwd -P)" --output-directory "$CLOUD_RUN_TEMPLATE_ROOT" --release-metadata "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json" --base-template-audit "$CLOUD_RUN_TEMPLATE_ROOT/base-template-audit.json"
python3 -c 'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); raw=json.dumps(p, sort_keys=True); assert p["env"] == "-p 8765:8765"; assert p["private"] is True; assert p["runtype"] == "ssh"; assert p["docker_login_repo"] == p["docker_login_user"] == p["docker_login_pass"] == ""; assert "CLOUD_RUN_BOUNDARY_TOKEN" not in raw; assert "CLOUD_RUN_SESSION_ID" not in raw; assert "JUPYTER_TOKEN" not in raw; assert "OPEN_BUTTON_TOKEN" not in raw' "$CLOUD_RUN_TEMPLATE_ROOT/template-request.json"
```

The renderer itself validates the one immutable image digest and fixed request schema. The additional assertion proves the template has no boundary token, provider token, or session identity. Do not print `onstart` or encoded bodies.

- [ ] **Step 2: Publish and read back exactly one template**

After rechecking authorization:

```sh
python3 scripts/publish_worker_template.py publish --request-file "$CLOUD_RUN_TEMPLATE_ROOT/template-request.json" --output-directory "$CLOUD_RUN_TEMPLATE_ROOT"
```

The command performs the exact-name precheck, one create at most, ambiguous-create reconciliation, and exact hash readback. Require `template-publication.json` to contain one 32-lowercase-hex `hash_id` and `verified: true`. If verification fails, report the sanitized identifier and stop; do not edit or delete the template automatically.

Load the verified value without printing the record:

```sh
CLOUD_RUN_TEMPLATE_HASH=$(python3 -c 'import json,re,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); value=p["hash_id"]; assert p["verified"] is True and re.fullmatch(r"[0-9a-f]{32}", value); print(value)' "$CLOUD_RUN_TEMPLATE_ROOT/template-publication.json")
```

- [ ] **Step 3: Build a new lock beside the current lock**

Use these exact paths:

```text
/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json
/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.next.json
```

Require `worker-release.next.json` not to exist. Read the verified template hash from `template-publication.json`, then run:

```sh
python3 scripts/write_worker_release_lock.py --output /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.next.json --template-hash-id "$CLOUD_RUN_TEMPLATE_HASH" --release-metadata "$CLOUD_RUN_RELEASE_ROOT/first/release-metadata.json"
```

Round-trip it through `cloud_run.worker_release.load_worker_release`, require mode `0600`, current ownership, exact new template hash/commit/archive digest, and no symlink.

- [ ] **Step 4: Rotate recoverably and restart only Desktop's existing backend**

Derive the old commit from the current validated lock:

```sh
CLOUD_RUN_OLD_WORKER_COMMIT=$(python3 -c 'import json,re,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["worker_commit"]; assert re.fullmatch(r"[0-9a-f]{40}", value); print(value)' /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json)
CLOUD_RUN_LOCK_BACKUP="/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.pre-$CLOUD_RUN_OLD_WORKER_COMMIT.json"
test ! -e "$CLOUD_RUN_LOCK_BACKUP"
```

Move the current lock to that backup, then atomically rename `worker-release.next.json` to `worker-release.json`:

```sh
mv /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json "$CLOUD_RUN_LOCK_BACKUP"
if ! mv /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.next.json /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json
then
  mv "$CLOUD_RUN_LOCK_BACKUP" /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json
  exit 1
fi
```

If the second rename fails, this restores the backup before doing anything else.

Revalidate the existing listener and queue:

```sh
lsof -nP -iTCP:8188 -sTCP:LISTEN
curl -fsS http://127.0.0.1:8188/queue
```

Require port `8188` to belong to `/Applications/ComfyUI.app` using the existing local installation and `/queue` to have no running/pending local prompts. Restart the existing Desktop backend once through Desktop/Agent Panel; never launch Python or another backend manually.

- [ ] **Step 5: Verify the loaded lock without paid action**

Read `http://127.0.0.1:8188/cloud-run/api/settings`. Require `configured=true`, `lifecycle_enabled=true`, and `worker_release` fields equal the new template hash, exact release commit/digest, protocol, ComfyUI versions, and worker port. Require no active session with `billing_may_continue=true`.

Stop before offer search or rental if the loaded lock differs. Keep the old backup and both old immutable artifacts.

---

### Task 10: Run one human-controlled live Gold test through ComfyUI

**Paid-action boundary:** The human operates every paid UI control. The agent observes and verifies only. Use `max_instance_creates=1`. Per the human's instruction, do not add an automatic stop timer; the human explicitly acknowledges no limit in the frontend and performs manual destruction.

**Workflow:**

```text
/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/Wallpaper Outpaint FLUX Fill 4K.json
```

**Workflow ID:** `f6a8a9d4-8763-4f73-aaae-06ee57580d9c`

- [ ] **Step 1: Revalidate the real canvas and free preflight**

Through ComfyUI Desktop/Agent Panel, verify the target workflow is open, its real `LoadImage` selection is still present, it has 32 active nodes and no custom nodes, and nodes 31, 32, 34, and 61 still contain the certified native model metadata.

Use the real Cloud Run frontend button to create one fresh capture, then run one free preflight with `explicit_output_allowance_bytes=1073741824`. Do not call `/prompt`. Require `rentable=true`, the four expected model files recognized, the real input recognized, and no missing dependency. Record the new capture and preflight IDs.

- [ ] **Step 2: Search and let the human review one offer**

The human saves the desired search settings, clicks `Search Vast GPUs`, chooses the offer, and reviews the exact GPU, VRAM, hourly rate, reliability, advertised download, disk speed, bandwidth charges, disk size, release commit, template hash, manifest digest, and `max_instance_creates=1`.

The agent does not choose a price ceiling or offer for the human. The transfer estimate remains theoretical; measured timings come from this run.

- [ ] **Step 3: Start timing when the human confirms**

The human presses `Confirm & rent this GPU`. Record wall-clock timestamps for:

- confirmation accepted and instance ID assigned;
- image pull start/end and container running when available;
- first authenticated worker health JSON;
- dependency transfer start/end;
- session `ready`;
- remote job submit/start/success;
- output retrieval/verification;
- destroy request and verified inventory absence.

As soon as the worker boundary is authenticated and the deadline update can synchronize, the human uses the frontend acknowledgement and `Disable automatic limit`. Before remote execution, verify `deadline_mode == "none"` and no deadline update is pending. The agent does not automate this click.

Observe through the Agent Panel and `GET /cloud-run/api/sessions/{session_id}`. A temporary connection refusal while the image starts is retryable. An HTTP `401` must now become the static terminal boundary error followed by immediate verified destruction; if that occurs, stop and invoke systematic debugging instead of renting a second GPU.

- [ ] **Step 4: Run only after authenticated `ready`**

Require the session to pass `bootstrapping`, `provisioning`, `validating`, then `ready`. Confirm the controller used one instance and the dependency transaction verified every model and input.

The human presses **Run current canvas on this GPU**. Do not press ComfyUI's normal local queue button. Verify one remote job, no local `/prompt`, successful completion, retrieved output bytes, local output SHA-256/size validation, and a visible result in ComfyUI.

- [ ] **Step 5: Destroy manually and prove billing stopped**

After any desired visual inspection, the human opens destroy review and presses `Destroy GPU — stop all Vast billing`. Require:

- session state `destroyed`;
- `billing_may_continue=false`;
- `instance_id`, boundary token, and HMAC secret cleared from the durable session;
- controller inventory verification found no instance with the session label;
- a final read-only Vast inventory contains no matching instance.

Do not end the handoff while billing may continue. If inventory cannot be verified, show the emergency Vast-console action immediately.

- [ ] **Step 6: Record the Gold result on PR #3**

Add one concise sanitized PR comment containing:

- the previous failure was provider-token mismatch at the worker boundary;
- exact new worker commit/release tag/template hash;
- capture, preflight, session, instance, and job IDs;
- GPU model and advertised connection metrics;
- phase durations and total time to `ready`/output;
- recognized model count, transferred bytes, verified output count;
- one create, one run, one destroy, final inventory empty;
- no local prompt execution and no secrets/private input names.

Keep PR #3 draft and do not merge. Only now report **Gold-validated** and clean up private temporary release/template material with a recoverable, explicitly validated operation; retain the old lock backup until the user decides it is no longer needed.

## Expected user experience after Gold

The user opens Cloud Run, performs free preflight, searches, reviews one GPU, confirms it, waits until `ready`, presses `Run current canvas on this GPU`, and destroys it when finished. Vast's own Jupyter/portal token format is irrelevant to this flow.

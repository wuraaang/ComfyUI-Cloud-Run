# Idempotent Provision Replay and Manual Destruction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent an identical queued provision request from replaying completed work, and require explicit user destruction after terminal provisioning failures in manual-duration sessions.

**Architecture:** Reuse the worker's existing durable `provision-<manifest digest>` transaction from inside its existing provision lock before any new mutation. At the controller's single destruction boundary, treat a terminal error as diagnostic-only when `deadline_mode=none`; boot timeout follows the same manual policy, while explicit destroy and explicitly armed finite deadlines keep their current behavior.

**Tech Stack:** Python 3.13, `asyncio`, immutable worker state, SQLite-backed session state, `unittest`, existing ComfyUI Python runner.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`.
- Do not call Vast, rent or destroy an instance, restart Desktop, publish a worker, modify a template, push, or create a pull request.
- Do not expose provider keys, boundary tokens, session secrets, signed URLs, or private worker addresses.
- Do not modify `/Users/wuraaang/comfyui-vast-cockpit`.
- Keep explicit user destruction and explicitly armed finite-deadline destruction unchanged.
- Use strict red/green TDD before each production change.

---

### Task 1: Make completed worker provisioning idempotent

**Files:**
- Modify: `tests/python/test_worker_provision.py`
- Modify: `remote_worker/provision.py`

**Interfaces:**
- Consumes: `Provisioner.apply_manifest(desired, required_class_types=(), source_urls=None)` and the existing durable `Provisioner.transaction(transaction_id)` boundary.
- Produces: identical ready replays return the stored `ProvisionResult` before any new disk, progress, transfer, installation, or ComfyUI operation.

- [ ] **Step 1: Write the concurrent failing test**

Add `ProvisionerTests.test_concurrent_duplicate_ready_manifest_is_reused_without_replay`. Use a `BlockingArtifacts` subclass with `asyncio.Event` values so the first request pauses in `ensure_many()` and the second queues on the existing provision lock. Release the first request, await both, and assert:

```python
self.assertEqual(first_result, second_result)
self.assertEqual(len(artifacts.ensure_calls), 1)
self.assertEqual(len(disk.calls), 1)
self.assertEqual(installer.nodes, ["fancy"])
self.assertEqual(comfy.restarts, 1)
self.assertEqual(comfy.ensure_calls, 0)
self.assertEqual(
    self.state.load()["transactions"][first_result.transaction_id]["state"],
    "ready",
)
```

- [ ] **Step 2: Write the inconsistent-replay failing test**

Add `ProvisionerTests.test_ready_manifest_replay_with_changed_required_classes_fails_without_mutation`. Provision one manifest successfully for `("KSampler",)`, snapshot the stored transaction and operation counts, then replay the same manifest for `("KSampler", "Unknown")`. Assert a sanitized `ProvisionError`, byte-for-byte equivalent durable transaction data, and unchanged disk, artifact, installer, and ComfyUI call counts.

- [ ] **Step 3: Run both tests and verify RED**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_worker_provision.ProvisionerTests.test_concurrent_duplicate_ready_manifest_is_reused_without_replay \
  tests.python.test_worker_provision.ProvisionerTests.test_ready_manifest_replay_with_changed_required_classes_fails_without_mutation -v
```

Expected: both tests fail against the current implementation because the ready transaction is overwritten and provisioning work is repeated.

- [ ] **Step 4: Implement the minimal ready-transaction reuse**

Inside the existing `async with self._provision_lock():` block in `Provisioner.apply_manifest()`, keep the current manifest, class-type, and source container validation. Immediately after deriving `transaction_id`, add:

```python
existing = self.transaction(transaction_id)
if existing is not None and existing.state == "ready":
    readiness = existing.readiness
    if (
        readiness is None
        or readiness.get("validated_class_types") != list(required)
    ):
        raise _provision_error()
    return existing
```

Do not alter non-ready retry behavior or any transaction validation rule.

- [ ] **Step 5: Run both tests and verify GREEN**

Run the exact command from Step 3. Expected: two tests pass with no warning or error.

- [ ] **Step 6: Run the complete worker provisioning suite**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest tests.python.test_worker_provision -v
```

Expected: all discovered worker provisioning tests pass.

- [ ] **Step 7: Commit the worker fix locally**

```bash
git add remote_worker/provision.py tests/python/test_worker_provision.py
git commit -m "fix: reuse completed worker provisioning"
```

Do not push.

---

### Task 2: Require manual destruction for manual-duration failures

**Files:**
- Modify: `tests/python/test_lifecycle.py`
- Modify: `cloud_run/lifecycle.py`

**Interfaces:**
- Consumes: `CloudRunLifecycle.destroy_session(session_id, terminal_error=None)` and `CloudRunLifecycle.handle_session_boot_failure(session_id, failure_code=...)`.
- Produces: terminal errors in `deadline_mode=none` return a durable failed session with the exact instance retained, `destroy_requested=false`, `can_destroy=true`, and `billing_may_continue=true`; explicit destruction remains the only destructive action in that mode.

- [ ] **Step 1: Allow lifecycle fixtures to create manual sessions**

Extend `SessionLifecycleTests.save_session()` with `deadline_mode="finite"`. Build the session using:

```python
deadline_at=(
    self.clock() + 7200
    if deadline_mode == "finite"
    else None
),
deadline_mode=deadline_mode,
```

Existing tests keep their finite default unchanged.

- [ ] **Step 2: Write the manual terminal-error failing test**

Add `test_manual_terminal_failure_waits_for_reviewed_destroy`. Use `ImmediateTerminalSessionService`, a session with `deadline_mode="none"`, and one fake provider instance. After `wait_until_session_ready()`, assert:

```python
self.assertEqual(failed.state, SessionState.FAILED)
self.assertEqual(failed.instance_id, "instance-1")
self.assertFalse(failed.destroy_requested)
self.assertTrue(failed.public_payload()["can_destroy"])
self.assertTrue(failed.public_payload()["billing_may_continue"])
self.assertEqual([call[0] for call in self.provider.calls], ["get"])
```

Then call the existing explicit `destroy_session()` and prove it reaches `DESTROYED` through exactly one fake `destroy` and one final `list` call.

- [ ] **Step 3: Write the manual boot-timeout failing test**

Add `test_manual_boot_timeout_waits_for_reviewed_destroy`. Call `handle_session_boot_failure()` for a session with `deadline_mode="none"` and assert failed/active billing state, retained credentials and instance identity, `destroy_requested=false`, and zero provider calls.

- [ ] **Step 4: Write the manual recovery failing test**

Add `test_manual_terminal_recovery_waits_for_reviewed_destroy`. Use `TerminalRecoverySessionService`, a ready manual session, and one matching fake instance. Assert recovery performs its initial read-only inventory list but never calls provider destruction, retains the instance, keeps billing warning true, and does not invoke `confirmed_terminal_destroy`.

- [ ] **Step 5: Run the three tests and verify RED**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_lifecycle.SessionLifecycleTests.test_manual_terminal_failure_waits_for_reviewed_destroy \
  tests.python.test_lifecycle.SessionLifecycleTests.test_manual_boot_timeout_waits_for_reviewed_destroy \
  tests.python.test_lifecycle.SessionLifecycleTests.test_manual_terminal_recovery_waits_for_reviewed_destroy -v
```

Expected: tests fail because the current terminal and boot-timeout paths destroy the fake instance automatically.

- [ ] **Step 6: Add the central manual-mode guard**

At the start of `CloudRunLifecycle.destroy_session()`, after the already-destroyed return and before provider mutation, add the exact policy:

```python
if (
    terminal_error is not None
    and session.deadline_mode == "none"
    and not session.destroy_requested
    and session.state
    not in {
        SessionState.DESTROY_REQUESTED,
        SessionState.DESTROYING,
    }
):
    return self.session_repository.transition(
        session.session_id,
        SessionState.FAILED,
        now=float(self.clock()),
        sanitized_error=terminal_error,
    )
```

This guard must not call `_api_key()`, provider destroy, or provider inventory.

- [ ] **Step 7: Apply the same policy to boot timeout**

In `handle_session_boot_failure()`, after recording the failed session and before `_api_key()`, return the failed session immediately when `deadline_mode == "none"`. Leave the existing finite-session destruction body unchanged.

- [ ] **Step 8: Run the three tests and verify GREEN**

Run the exact command from Step 5. Expected: three tests pass.

- [ ] **Step 9: Prove finite deadlines and explicit destruction are unchanged**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_lifecycle.SessionLifecycleTests.test_persistent_boot_authentication_failure_destroys_after_grace \
  tests.python.test_lifecycle.SessionLifecycleTests.test_terminal_recovery_failure_destroys_and_verifies_immediately \
  tests.python.test_lifecycle.SessionLifecycleTests.test_local_expiry_attempts_bounded_retrieval_then_verifies_destroy \
  tests.python.test_lifecycle.SessionLifecycleTests.test_session_destroy_requires_inventory_absence_after_delete -v
```

Expected: four tests pass with their existing verified-destruction assertions.

- [ ] **Step 10: Run the complete lifecycle suite**

```bash
scripts/run_with_comfyui_python.sh -m unittest tests.python.test_lifecycle -v
```

Expected: all discovered lifecycle tests pass.

- [ ] **Step 11: Commit the lifecycle policy locally**

```bash
git add cloud_run/lifecycle.py tests/python/test_lifecycle.py
git commit -m "fix: require manual terminal destruction"
```

Do not push.

---

### Task 3: Certify the complete offline result

**Files:**
- Verify only; no new production file is expected.

**Interfaces:**
- Consumes: the worker and lifecycle changes from Tasks 1 and 2.
- Produces: fresh offline evidence that the requested behavior works and unrelated certified behavior remains green.

- [ ] **Step 1: Run the critical legacy paid-claim regression tests**

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_repository.SessionRepositoryTests.test_legacy_terminal_quote_does_not_block_paid_claim \
  tests.python.test_repository.SessionRepositoryTests.test_legacy_uncertain_billing_fields_still_block_paid_claim \
  tests.python.test_service.CloudRunServiceTests.test_pre_provider_claim_failure_returns_offer_for_review \
  tests.python.test_service.CloudRunServiceTests.test_refresh_recovers_only_quiet_tokenless_confirmation \
  tests.python.test_service.CloudRunServiceTests.test_blocked_confirmation_returns_to_pre_mutation_state \
  tests.python.test_service.CloudRunServiceTests.test_concurrent_paid_confirmations_issue_exactly_one_create \
  tests.python.test_service.CloudRunServiceTests.test_concurrent_sessions_issue_only_one_provider_create -v
```

Expected: seven tests pass.

- [ ] **Step 2: Run the complete offline certification twice**

```bash
scripts/check.sh
scripts/check.sh
```

Expected for each run: every suite, compilation, boundary scan, and secret scan passes. The worker source SHA-256 and generated worker-bundle SHA-256 are exactly equal within that run. Because worker source changes, the new digest is expected to differ from the previous release digest and must not be published without a later explicit GO.

- [ ] **Step 3: Verify repository integrity and exact scope**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -6
```

Expected: no uncommitted change, the branch is only ahead by the reviewed local design, plan, and implementation commits, and nothing has been pushed.

- [ ] **Step 4: Report the result without overstating live certification**

Report separately:

- proven offline behavior;
- the live behavior that remains unproven until a separately authorized worker publication/template update and paid campaign;
- current Vast inventory zero and `billing_may_continue=false` from the already completed final safety check;
- no external action taken during this implementation.

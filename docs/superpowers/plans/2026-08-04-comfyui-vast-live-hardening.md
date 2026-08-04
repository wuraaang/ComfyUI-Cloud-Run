# ComfyUI Vast Live Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Use superpowers:test-driven-development for every production change and superpowers:verification-before-completion before any completion claim.

**Goal:** Remove the deterministic transport, readiness, destruction, profile, extension, and status-reporting defects observed during the 2026-08-04 paid campaign, while keeping provider lifetime under explicit user control and making the approved Desktop baseline fail closed before rental.

**Architecture:** Three parallel specialist tracks build independently testable primitives: native transport/readiness, priority destruction/observability, and certified Desktop baseline/profile installation. One integration owner then wires the primitives through SessionService, runtime preflight, routes, and the offline end-to-end harness. Failed readiness attempts remain evidence rather than cache entries, destruction is bound to a stable safety snapshot rather than volatile progress, and Agent Panel, Hermes Nous, and Efficiency Nodes enter the manifest only through verified immutable artifacts.

**Tech Stack:** Python 3.13.12, aiohttp, SQLite, HMAC-SHA256, ComfyUI Core 0.29.0, frontend 1.47.10, browser-native JavaScript ES modules, Node test runner, Python unittest, immutable local-upload artifacts, and the existing fake Vast/provider boundaries.

## Authority and non-negotiable constraints

- Work only in /Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit on branch fix/vast-template-live-audit.
- Planning baseline is commit dd32a0e. The approved design is docs/superpowers/specs/2026-08-04-comfyui-vast-live-hardening-design.md.
- This plan authorizes repository-local implementation and offline tests only after the user says OK.
- It does not authorize a Vast create or destroy, offer search, worker publication, GitHub release, private-template mutation, external Desktop installation, Desktop restart, or paid field test.
- Never use the account Vast key in tests or product runtime. No test may contact Vast.
- Preserve the current published worker release and templates until a later explicit approval.
- Manual/no-limit mode never auto-destroys after a readiness, execution, or recovery error.
- A manual destroy confirmation must remain reviewed, explicit, irreversible, and independently verified through fresh complete inventory.
- Never weaken the native proxy to expose Agent Panel training, CivitAI proxy, Apps, manager, process, reload/restart, arbitrary filesystem, or arbitrary backend routes.
- Never trust mutable installed custom-node directories merely because they exist. Exact source revision, archive digest, file allowlist, wheel closure, and runtime compatibility must be certified first.
- Generic profile assets remain inert CSS and images. Agent Panel and Hermes executable JS/SVG travel only inside their identified UI package archives.
- Strict RED, observed failure, minimal GREEN, focused regression, and small commit applies to every task.
- Run repository tests through scripts/run_with_comfyui_python.sh. The final deterministic gate is scripts/check.sh.
- Before each commit inspect git status --short, git diff --cached --check, and git diff --cached. Stage only task-owned files.

## Parallel execution map

The implementation owner must keep one writer per file. Parallel workers may inspect shared code but may edit only their assigned files until the integration wave.

### Wave 1: independent primitives

- Worker A, Task 1: remote_worker/native_proxy.py, tests/python/test_worker_native_proxy.py, tests/python/test_worker_client.py, and the new transport integration harness only.
- Worker B, Task 3: cloud_run/repository.py and tests/python/test_repository.py only.
- Worker C, Task 6: cloud_run/comfy_host.py, cloud_run/certified_baseline.py, cloud_run/manifest.py, the lock/assets, and focused tests only.
- Integration owner, Task 8: cloud_run/desktop_profile.py and tests/python/test_desktop_profile.py only.

### Wave 2: independent persistence and worker installation

- Worker A, Task 2: cloud_run/readiness.py, cloud_run/job_repository.py, cloud_run/desktop_relay.py, and focused readiness tests. Do not edit SessionService.
- Worker B, Task 4: cloud_run/reconciler.py, cloud_run/lifecycle.py, and their focused tests. Do not edit SessionService.
- Worker C, Task 7: remote_worker/install.py, remote_worker/provision.py, and focused worker tests. It does not edit tests/python/test_readiness.py.
- Integration owner reviews all Wave 1 commits and resolves no cross-domain behavior yet.

### Wave 3: serialized integration

- Task 5 owns cloud_run/session_service.py and combines readiness retry with priority destruction.
- Task 9 owns cloud_run/resolver.py and cloud_run/routes.py runtime preflight wiring.
- Task 10 owns cloud_run/routes.py plus web/js/cloud-run.js and web/js/session-console.js.
- Task 11 owns the offline end-to-end harness and final cross-layer assertions.
- Task 12 runs the full gate and records the repository-local handoff.

No two workers edit cloud_run/session_service.py, cloud_run/routes.py, or the same test file concurrently.

---

### Task 0: Record a clean offline baseline

**Files:**

- Read: AGENTS.md
- Read: docs/superpowers/specs/2026-08-04-comfyui-vast-live-hardening-design.md
- Read: this plan
- No production edit

**Purpose:** Prove the implementation starts from the reviewed commit, a clean worktree, and zero provider mutation.

- [ ] Run:

    git status --short --branch
    git rev-parse HEAD
    scripts/check.sh

- [ ] Expected before implementation:

  - HEAD begins at dd32a0e or at a descendant containing only the committed plan.
  - Worktree is clean.
  - Existing 814 Python and 62 JavaScript tests pass.
  - No Vast command, network provider call, Desktop mutation, or external install occurs.

- [ ] If the baseline is not green, stop and diagnose the unrelated failure before editing production code.

No commit is created for this read-only task.

---

### Task 1: Repair the exact native HTTP and WebSocket contract

**Files:**

- Modify: remote_worker/native_proxy.py
- Modify: tests/python/test_worker_native_proxy.py
- Modify: tests/python/test_worker_client.py
- Create: tests/python/test_native_transport_integration.py

**Interfaces:**

- NativeRoutePolicy.classify(method, path_qs) returns an HTTP route for only exact GET /system_stats.
- AiohttpNativeTransport.websocket(path_qs) uses aiohttp's supported public call surface and a request-level redirect guard.
- The cross-layer harness uses a real WorkerClient envelope, WorkerApplication native boundary, NativeComfyProxy, AiohttpNativeTransport, and loopback aiohttp Comfy handler.

- [ ] Add NativeRoutePolicyTests.test_system_stats_allows_only_exact_get.

  Assert GET /system_stats is allowed. Assert POST, PUT, a body-bearing envelope, query strings, fragments, absolute URLs, encoded traversal, identity headers, Authorization, Cookie, Host, and X-Forwarded-For are rejected or stripped at the correct boundary.

- [ ] Add AiohttpNativeTransportTests.test_websocket_opens_and_closes_with_real_aiohttp_api and test_websocket_redirect_is_rejected_before_target_or_headers_are_reached.

  The first test starts a real loopback aiohttp WebSocket and proves one text frame plus a clean close. The second starts a redirect server and a target server and asserts the target receives zero requests and zero headers.

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_worker_native_proxy.NativeRoutePolicyTests.test_system_stats_allows_only_exact_get \
      tests.python.test_worker_native_proxy.AiohttpNativeTransportTests -v

  Expected failures: /system_stats is absent and ws_connect rejects max_redirects.

- [ ] Add /system_stats only to the existing GET-without-query allowlist. Do not add prefixes, query support, POST support, or identity forwarding.

- [ ] In AiohttpNativeTransport._client, install one session.request wrapper that always writes allow_redirects=False. Remove max_redirects from ws_connect. Preserve fixed loopback base URL, trust_env=False, bounded frames, autoclose=False, and generic sanitized errors.

- [ ] Add NativeTransportIntegrationTests.test_signed_system_stats_crosses_real_worker_policy_to_loopback_comfy and test_signed_websocket_crosses_worker_policy_and_redirect_cannot_escape.

  The harness may fake the native Comfy response, but may not fake route classification, HMAC envelope validation, HTTP transport, WebSocket transport, redirect behavior, or header confinement.

- [ ] Run GREEN:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_worker_native_proxy \
      tests.python.test_worker_client \
      tests.python.test_native_transport_integration -v

- [ ] Commit:

    git add remote_worker/native_proxy.py \
      tests/python/test_worker_native_proxy.py \
      tests/python/test_worker_client.py \
      tests/python/test_native_transport_integration.py
    git diff --cached --check
    git commit -m "fix: align native readiness transport contract"

---

### Task 2: Make readiness diagnostics typed and attempts retryable

**Files:**

- Modify: cloud_run/readiness.py
- Modify: cloud_run/desktop_relay.py
- Modify: cloud_run/job_repository.py
- Modify: cloud_run/repository.py only for the readiness schema migration; coordinate after Task 3
- Modify: tests/python/test_readiness.py
- Modify: tests/python/test_desktop_relay.py
- Modify: tests/python/test_job_repository.py

**Interfaces:**

- ReadinessCheck gains diagnostic_code: str or None.
- ReadinessReport gains attempt_number: positive int included in its canonical digest and record.
- JobRepository.current_readiness_report returns only the immutable successful report for an exact stable identity.
- JobRepository.latest_readiness_attempt and list_readiness_attempts expose append-only evidence.
- Schema version 14, applied after Task 3's version 13 migration, stores attempt_number, permits many failed attempts, and enforces at most one success for an exact identity.

- [ ] Add value tests:

  - test_failed_check_requires_allowlisted_diagnostic_code
  - test_success_and_not_required_reject_diagnostic_code
  - test_public_readiness_contains_only_check_name_status_code_and_safe_message
  - test_raw_exception_url_header_and_token_never_enter_report_or_payload

- [ ] Add repository tests:

  - test_failed_attempts_are_append_only_and_do_not_block_later_success
  - test_success_is_reused_but_failures_remain_queryable
  - test_v13_failed_report_migrates_without_blocking_retry
  - test_attempt_number_participates_in_report_digest

- [ ] Add DesktopRelayTests.test_readiness_classifies_route_http_websocket_profile_and_agent_failures.

  Required codes are native_route_rejected, native_http_status, native_websocket_handshake, profile_package_mismatch, agent_bridge_unavailable, and legacy_readiness_failure only for migrated evidence.

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_readiness \
      tests.python.test_desktop_relay \
      tests.python.test_job_repository -v

- [ ] Implement the strict ReadinessCheck contract.

  Passed and not_required checks carry diagnostic_code=None. Failed checks require one allowlisted code. Include the code in evidence and public payload, but never expose evidence details, URLs, headers, frames, or exceptions.

- [ ] Implement schema v14 after invoking the idempotent version 13 destroy-review migration from Task 3.

  Rebuild readiness_reports because SQLite cannot drop the existing UNIQUE identity constraint in place. Copy version 13 rows as attempt 1. The initializer must also support a direct version 12 to 13 to 14 upgrade. During migration, add diagnostic_code=None to passed/not-required checks and legacy_readiness_failure to failed checks before recomputing their canonical report digest if the record schema requires it. Create:

  - unique identity plus attempt_number;
  - a unique partial index for the stable identity WHERE ready = 1;
  - an index for stable identity ordered by attempt_number.

- [ ] Make save_readiness_report append each failed attempt and return the already committed exact success when one exists. current_readiness_report must query ready=1. latest_readiness_attempt and list_readiness_attempts must validate the same identity inputs and return deterministic attempt order.

- [ ] Run GREEN:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_readiness \
      tests.python.test_desktop_relay \
      tests.python.test_job_repository -v

- [ ] Commit after rebasing on Task 3's repository migration:

    git add cloud_run/readiness.py cloud_run/desktop_relay.py \
      cloud_run/job_repository.py cloud_run/repository.py \
      tests/python/test_readiness.py tests/python/test_desktop_relay.py \
      tests/python/test_job_repository.py
    git diff --cached --check
    git commit -m "fix: persist retryable typed readiness attempts"

---

### Task 3: Replace volatile destroy reviews with a stable safety snapshot

**Files:**

- Modify: cloud_run/repository.py
- Modify: tests/python/test_repository.py

**Interfaces:**

- DestroyReviewRecord fields:

    session_id: str
    expires_at: float
    managed_label: str
    instance_id: str or None
    residual_instance_ids: tuple[str, ...]
    active_job_id: str or None
    unverified_artifact_ids: tuple[str, ...]
    profile_id: str or None
    profile_revision: int or None

- SessionRepository.save_destroy_review(session_id, token_digest, expires_at, profile_id) persists this snapshot and the token digest.
- SessionRepository.consume_destroy_review_and_request_destroy(session_id, token_digest, now) validates and consumes it, then atomically persists destroy_requested=True and DESTROY_REQUESTED.
- Schema version 13 rebuilds destroy_reviews, invalidates all legacy version-bound reviews, and leaves the version 12 readiness table intact for Task 2's version 14 migration.

- [ ] Replace the obsolete version-bound expectation with:

  - test_destroy_review_survives_progress_only_session_version_change
  - test_destroy_review_rejects_changed_instance_or_residual_inventory
  - test_destroy_review_rejects_changed_active_job_or_unverified_output
  - test_destroy_review_rejects_changed_label_or_profile_revision
  - test_destroy_review_is_consumed_once_and_atomically_sets_destroy_requested
  - test_v12_destroy_reviews_are_invalidated_during_v13_migration
  - retain the expiry-boundary and raw-token-not-stored tests

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_repository.SessionRepositoryTests -v

  Expected: the current review is invalidated by every session version update and cannot persist destroy intent atomically.

- [ ] Implement the idempotent version 13 migration and rebuild destroy_reviews. Delete legacy version-bound review rows rather than inventing missing safety fields. Update schema_meta to 13 only after the rebuilt table and indexes commit.

- [ ] Assemble and compare the snapshot under one BEGIN IMMEDIATE transaction using sessions, jobs, transfers, manifests, and profile_revisions. save_destroy_review receives the exact profile_id derived by SessionService from the installed manifest; the repository selects MAX(profile_revisions.revision) for that profile both when saving and consuming. profile_sync_state is not the source of the latest local safe revision. Progress timestamps, retry counts, sanitized messages, and ordinary state/version changes are deliberately excluded.

- [ ] In consume_destroy_review_and_request_destroy:

  1. compare token in constant time;
  2. reject expiry or any safety-snapshot change;
  3. delete the one-use review;
  4. persist destroy_requested=1 and state=destroy_requested in the same transaction;
  5. return the reviewed record and updated CloudSession.

- [ ] Run GREEN:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_repository.SessionRepositoryTests -v

- [ ] Commit:

    git add cloud_run/repository.py tests/python/test_repository.py
    git diff --cached --check
    git commit -m "fix: bind GPU destruction to stable safety snapshot"

---

### Task 4: Build a priority teardown lane and verified external-absence recovery

**Files:**

- Modify: cloud_run/reconciler.py
- Modify: cloud_run/lifecycle.py
- Modify: tests/python/test_reconciler.py
- Modify: tests/python/test_lifecycle.py

**Interfaces:**

- SessionReconciler.preempt(session_id) cancels active reconciliation and delayed retry without starting another poll.
- CloudRunLifecycle.preempt_session(session_id) cancels session watchdog/recovery work before teardown.
- CloudRunLifecycle.finalize_verified_absence centralizes local terminal cleanup after complete inventory proves ID and label absent.

- [ ] Add:

  - test_preempt_cancels_inflight_reconciliation_and_pending_retry
  - test_preempt_is_idempotent_and_not_recorded_as_retryable_failure
  - test_recovery_finalizes_externally_destroyed_known_session_without_delete_or_create
  - test_external_absence_clears_secrets_and_preserves_verified_evidence
  - test_unavailable_inventory_or_invalid_row_never_finalizes_external_destruction
  - test_destroy_session_accepts_pre_persisted_destroy_requested_state

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_reconciler \
      tests.python.test_lifecycle -v

- [ ] Implement idempotent cancellation in SessionReconciler.preempt. It must await cancelled work, remove task bookkeeping, and never trigger the current before_teardown follow-up reconciliation.

- [ ] Extract lifecycle finalization so provider DELETE success and independently observed complete absence use the same path. Only a successful return from the existing strict cloud_run.vast.list_instances contract, which already exhausts pagination and rejects short/partial pages, may be treated as complete. Lifecycle._inventory must reject the entire observation if any row is not a dict; it must never filter an invalid row into a false empty result. Only a complete observation lacking the exact known ID, every residual ID, and the managed label may clear instance identity, provider/session capabilities, residual inventory, and billing_may_continue.

- [ ] In recover_sessions, use external absence finalization only for sessions with a known paid identity. Preserve the existing bounded ambiguous-create protocol for CREATING or RECONCILING_CREATE records without a known instance.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_reconciler \
      tests.python.test_lifecycle -v
    git add cloud_run/reconciler.py cloud_run/lifecycle.py \
      tests/python/test_reconciler.py tests/python/test_lifecycle.py
    git diff --cached --check
    git commit -m "fix: add priority teardown and absence recovery"

---

### Task 5: Integrate bounded readiness retry and durable manual destruction

**Files:**

- Modify: cloud_run/session_service.py
- Modify: tests/python/test_session_service.py

**Interfaces:**

- SessionService._certify_readiness uses at most six durable attempts within 60 seconds.
- SessionService.destroy persists reviewed destroy intent before reconciliation, profile, bridge, worker, or provider network work.
- Every long-running provisioning/readiness/recovery loop calls a small durable guard before its next remote operation.
- SessionService._ensure_teardown_task creates one in-process teardown task per session; destroy awaits it through asyncio.shield, and restart recovery resumes every persisted DESTROY_REQUESTED session.

- [ ] Add readiness tests:

  - test_readiness_failure_then_success_reprobes_and_activates_once
  - test_successful_readiness_is_reused_without_probe
  - test_readiness_exhausts_six_attempts_within_sixty_seconds
  - test_restart_resumes_remaining_attempt_budget_without_reset
  - test_readiness_retry_stops_immediately_after_destroy_intent

- [ ] Add destruction tests:

  - test_destroy_persists_intent_before_reconciler_profile_bridge_or_provider_work
  - test_profile_timeout_or_failure_never_prevents_provider_teardown
  - test_agent_bridge_revocation_failure_never_prevents_teardown
  - test_provision_poll_observes_destroy_intent_before_next_worker_request
  - test_changed_safety_snapshot_requires_a_new_review
  - test_http_cancellation_after_intent_leaves_recoverable_teardown

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_session_service -v

- [ ] Implement readiness retry using list_readiness_attempts:

  - derive the durable window start from attempt 1;
  - assign the next attempt number monotonically;
  - call the real validator each time so inventory and every check are fresh;
  - persist each attempt before deciding whether to sleep;
  - reuse only an exact successful report;
  - sleep through the injected clock/sleep boundary;
  - after six attempts or 60 seconds, record one terminal typed readiness failure;
  - in manual/no-limit mode, retain the instance and can_destroy state.

- [ ] Rewrite destroy order:

  1. consume the review and atomically persist destroy_requested;
  2. create or reuse one _ensure_teardown_task(session_id);
  3. await that task through asyncio.shield so HTTP cancellation cannot cancel teardown;
  4. inside the task, call reconciler.preempt and lifecycle.preempt_session;
  5. revoke Agent Panel in best effort;
  6. run sync_profile through asyncio.wait_for with a five-second maximum, best effort;
  7. call lifecycle destroy for the exact reviewed identity;
  8. finalize only from fresh complete inventory;
  9. abandon incomplete jobs/transfers only after confirmed absence.

  Keep the task in a private registry until it reaches a terminal result. On service restart, recover every persisted DESTROY_REQUESTED session through the same teardown body before ordinary provisioning/readiness recovery. A cancelled HTTP handler may return no result, but billing teardown continues.

- [ ] Add _raise_if_destroy_requested and call it before each new worker/provider network action in _apply_with_progress_polling, _apply_manifest, _certify_readiness, resume_session, native submission, and recovery entry points. Voluntary preemption must not trigger automatic terminal destruction.

- [ ] Run GREEN:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_session_service \
      tests.python.test_reconciler \
      tests.python.test_lifecycle \
      tests.python.test_readiness -v

- [ ] Commit:

    git add cloud_run/session_service.py tests/python/test_session_service.py
    git diff --cached --check
    git commit -m "fix: prioritize manual teardown over remote work"

---

### Task 6: Certify the immutable Desktop extension baseline

**Files:**

- Create: cloud_run/certified_baseline.py
- Create: cloud_run/certified_baseline.lock.json
- Create: cloud_run/baseline_assets/hermes-nous/__init__.py
- Create: cloud_run/baseline_assets/hermes-nous/web/hermes-nous.css
- Create: cloud_run/baseline_assets/hermes-nous/web/hermes-nous.js
- Modify: cloud_run/comfy_host.py
- Modify: cloud_run/manifest.py
- Create: tests/python/test_certified_baseline.py
- Modify: tests/python/test_comfy_host.py
- Modify: tests/python/test_manifest.py
- Create: tests/fixtures/certified-baseline/agent-panel-0.11.38
- Create: tests/fixtures/certified-baseline/hermes-nous
- Create: tests/fixtures/certified-baseline/efficiency-nodes-1.0.9

**Interfaces:**

- CertifiedBaselineResolution contains ui_packages, custom_nodes, local_artifacts, and a canonical digest.
- CertifiedBaselineResolver.resolve is async, accepts an injected HTTPS fetcher plus an application-private cache root, and returns the exact Agent Panel 0.11.38 UI package, content-addressed Hermes Nous UI package, and Efficiency Nodes 1.0.9 custom node, or raises a safe preflight error.
- The lock records canonical repository, immutable revision, permitted paths, source archive size/SHA-256, canonical web SHA-256, exact class set, exact wheel closure, protected distribution exclusions, and frontend 1.47.10.
- UiPackageSpec.revision accepts either one exact 40-hex Git commit or sha256:<64 hex> for a repository-owned content-addressed UI package. CustomNodeSpec remains 40-hex Git-commit-only.

- [ ] Add:

  - test_nested_cnr_package_is_not_identified_as_parent_checkout
  - test_resolves_exact_agent_01138_hermes_and_efficiency_109
  - test_rejects_missing_revision_archive_digest_or_wheel_closure
  - test_rejects_changed_allowed_file_or_extra_executable_file
  - test_agent_archive_contains_only_minimal_loader_and_reviewed_web_root
  - test_efficiency_never_uses_mutable_installed_directory
  - test_efficiency_proves_clip_interrogator_unused_before_excluding_it
  - test_protected_runtime_distribution_in_wheel_lock_is_rejected
  - test_lock_requires_frontend_14710
  - test_ui_revision_accepts_git_commit_or_explicit_sha256_only

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_comfy_host \
      tests.python.test_certified_baseline \
      tests.python.test_manifest -v

- [ ] Fix ComfyHost._git_identity so git rev-parse --show-toplevel must equal the candidate package root. A nested CNR directory must never inherit the parent ComfyUI checkout identity.

- [ ] Encode these already audited identities exactly in the lock:

  - Agent Panel repository: https://github.com/artokun/comfyui-mcp-panel
  - Agent Panel version/commit: 0.11.38 at e4de6a5a2e8fbcde166b5fe2983bf3404ca10e0b
  - Agent Panel Registry version ID: e4ad28a2-549e-4804-9927-b4699ad5ec0f
  - Agent Panel source URL: https://cdn.comfy.org/artokun/comfyui-agent-panel/0.11.38/node.zip
  - Agent Panel source size/SHA-256: 2,807,293 bytes / 81e33c49cd65cfb85cf42e604629e25e237f3903958329f015eeb785eb913df1
  - Agent Panel locked web tree: 67 files, 2,852,341 bytes, canonical SHA-256 8a49270675f193c65c998068b1ac92257bc28b168aa38ad9567f2d2fa771542d
  - Agent Panel curated tar size/SHA-256 from the repository's deterministic build_package_archive: 2,908,160 bytes / c0e05111db15e8bc040c63ff9457fc142326afab66532f942b631f268fb606be
  - Efficiency repository: https://github.com/jags111/efficiency-nodes-comfyui
  - Efficiency version/commit: 1.0.9 at 835bbe14627cccc871822e804c65c734960d3c6e
  - Efficiency Registry version ID: 03cae633-3466-451d-b479-4ad1e1fbba04
  - Efficiency source URL: https://cdn.comfy.org/jags111/efficiency-nodes-comfyui/1.0.9/node.zip
  - Efficiency source size/SHA-256: 131,210,938 bytes / 3061180cbe2afd1c8301875af9bff555e1462814020de791019162767bd64c8f
  - Efficiency locked JS tree: 18 files, 113,571 bytes, canonical SHA-256 b22af6b88ae5846edd8d69921c7958d0105d4e7703c861aa64e33ae61b02d250
  - Efficiency curated runtime tar: 36 files, 26,173,440 bytes, SHA-256 0fc239d03f09fc9a78a087b7a6514dafb7b0a614ed5f73ef24f22dd56006eebe
  - simpleeval wheel: simpleeval-1.0.7-py3-none-any.whl, 18,792 bytes, SHA-256 97ac271bfd8f2af9e7b9a36ceea67617f26fa873f9d5ae1922f64d4c1442534b
  - simpleeval source URL: https://files.pythonhosted.org/packages/0f/2f/f32aa85591882378bb43caa09363f3ed97df399369a5144c7f19f2275bc0/simpleeval-1.0.7-py3-none-any.whl

- [ ] Vendor the three user-approved Hermes files and assert their exact identity:

  - __init__.py: 163 bytes, SHA-256 2265b256b8cecdebef252f99fef03dbcdcd4ac29f089c1f0cbca3e3bde8f13be
  - web/hermes-nous.css: 17,083 bytes, SHA-256 cd55d3eeb3acff8ccc4cf0bdebf16a828da9d4ad9191f768a671697cfacc7c0d
  - web/hermes-nous.js: 15,050 bytes, SHA-256 da2b4f3ab8b60c6dfc0942f6a43066a0b3e032084b71f5f021a81e014e4f4a4d
  - canonical source tree SHA-256: 743a5a2505cab78c3502c0fcf50b83780790a5a5b14e7fc1be3e438a3ca7c82f
  - canonical web SHA-256: d07506b932fc68e7548281e5ad32944c5d6bb1d8ec3f97e5a693033889784275
  - deterministic curated tar size/SHA-256: 40,960 bytes / d332e736ed8d98dece91a697d359eddd2ee37b0ac8f2d2cd1898332adf78f23e
  - provenance repository: https://github.com/wuraaang/ComfyUI-Cloud-Run
  - revision: sha256:743a5a2505cab78c3502c0fcf50b83780790a5a5b14e7fc1be3e438a3ca7c82f

- [ ] Lock Efficiency's 40 class types exactly:

  KSampler (Efficient), KSampler Adv. (Efficient), KSampler SDXL (Eff.), Efficient Loader, Eff. Loader SDXL, LoRA Stacker, Control Net Stacker, Apply ControlNet Stack, Unpack SDXL Tuple, Pack SDXL Tuple, XY Plot, XY Input: Seeds++ Batch, XY Input: Add/Return Noise, XY Input: Steps, XY Input: CFG Scale, XY Input: Sampler/Scheduler, XY Input: Denoise, XY Input: VAE, XY Input: Prompt S/R, XY Input: Aesthetic Score, XY Input: Refiner On/Off, XY Input: Checkpoint, XY Input: Clip Skip, XY Input: LoRA, XY Input: LoRA Plot, XY Input: LoRA Stacks, XY Input: Control Net, XY Input: Control Net Plot, XY Input: Manual XY Entry, Manual XY Entry Info, Join XY Inputs of Same Type, Image Overlay, Noise Control Script, HighRes-Fix Script, Tiled Upscaler Script, LoRA Stack to String converter, Evaluate Integers, Evaluate Floats, Evaluate Strings, Simple Eval Examples.

- [ ] Use this reviewed Efficiency runtime allowlist:

  LICENSE, __init__.py, efficiency_nodes.py, tsc_utils.py, arial.ttf, node_settings.json, js/**, py/**, and workflows/SimpleEval_Node_Examples.txt. Exclude .github, images, example workflows, models/readme.md, README network instructions, and the pyproject example model URL.

  Static AST and byte-string checks must prove the allowed executable source contains no import, call, or reference to clip_interrogator before the resolver drops that unused upstream metadata dependency. The only added wheel is simpleeval 1.0.7. Any later source reference to clip_interrogator makes the lock invalid and preflight non-rentable.

  The resolver may download the two exact Registry archives and the one exact wheel into its content-addressed private cache during free preflight, but may not install them locally, mutate Desktop, publish anything, or contact Vast.

  Copy into the three repository fixtures only the smallest license-compatible reviewed loader/web/class metadata needed by deterministic offline tests. Record upstream license and provenance beside each fixture. Do not vendor credentials, caches, generated environments, model files, or an entire mutable Desktop directory.

- [ ] Certification checkpoint:

  If any exact HTTPS URL, version ID, immutable revision, byte length, SHA-256, permitted-path review, class set, Hermes file, or wheel hash differs, return baseline_unavailable. Do not derive trust from Manager cache metadata, an inherited parent Git checkout, or the mutable installed directory. Other tasks may continue, but paid preflight remains non-rentable.

- [ ] Implement deterministic archive materialization:

  - Agent Panel receives the 163-byte minimal WEB_DIRECTORY loader shape plus LICENSE and the locked web/** tree; never package the upstream __init__.py or py/** backend route files.
  - Hermes receives only its reviewed loader and web assets.
  - Efficiency receives only the runtime allowlist above and simpleeval 1.0.7; no example model URL or clip-interrogator dependency becomes an artifact.
  - Every archive and wheel is registered as ResolvedLocalArtifact and referenced through local-upload.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_comfy_host \
      tests.python.test_certified_baseline \
      tests.python.test_manifest -v
    git add cloud_run/certified_baseline.py \
      cloud_run/certified_baseline.lock.json cloud_run/baseline_assets \
      cloud_run/comfy_host.py cloud_run/manifest.py \
      tests/python/test_certified_baseline.py tests/python/test_comfy_host.py \
      tests/python/test_manifest.py \
      tests/fixtures/certified-baseline
    git diff --cached --check
    git commit -m "feat: certify Desktop extension baseline"

---

### Task 7: Measure UI installation and protect the worker runtime

**Files:**

- Modify: remote_worker/install.py
- Modify: remote_worker/provision.py
- Modify: tests/python/test_worker_install.py
- Modify: tests/python/test_worker_provision.py

**Interfaces:**

- UiInstallResult exposes package_id, measured web_sha256, and served extension paths.
- install_ui_package verifies measured installed content before the atomic swap.
- Wheel installation is one offline no-dependency-resolution command after protected-distribution validation.
- Worker readiness reports measured UI digests and served paths, never manifest claims copied back verbatim.

- [ ] Add:

  - test_ui_install_returns_measured_canonical_web_digest
  - test_ui_install_rejects_manifest_web_digest_mismatch
  - test_ui_install_rejects_unreviewed_executable_path
  - test_efficiency_wheels_install_offline_without_dependency_resolution
  - test_protected_core_wheel_is_rejected_before_pip
  - test_readiness_uses_installer_measurements_not_manifest_claims
  - test_all_locked_efficiency_classes_are_present_after_restart
  - test_expected_frontend_extension_paths_are_served
  - test_spoofed_ui_digest_cannot_pass_worker_readiness_measurement

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_worker_install \
      tests.python.test_worker_provision -v

- [ ] Define the canonical web digest over sorted relative path, normalized file mode, byte size, and content digest. Compute it after safe extraction and before atomic destination replacement.

- [ ] Define the exact protected distribution set:

    accelerate
    aiohttp
    comfyui
    comfyui-frontend-package
    numpy
    open-clip-torch
    pillow
    pip
    requests
    safetensors
    setuptools
    torch
    torchaudio
    torchvision
    transformers
    wheel

  Reject a locked wheel whose normalized distribution is in that set before any subprocess. For the certified baseline, also require the wheel distribution set to equal exactly {simpleeval}.

- [ ] Invoke pip once with this exact argv prefix and sorted absolute wheel paths appended:

    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        "--disable-pip-version-check",
        "--no-compile",
    ]

- [ ] In worker validation, require:

  - every locked Efficiency class in /object_info;
  - the expected Agent Panel, Hermes, and Efficiency extension paths;
  - measured UI digest equality;
  - unchanged pinned ComfyUI Core, frontend, Python, Torch, aiohttp, and worker release identity.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_worker_install \
      tests.python.test_worker_provision -v
    git add remote_worker/install.py remote_worker/provision.py \
      tests/python/test_worker_install.py \
      tests/python/test_worker_provision.py
    git diff --cached --check
    git commit -m "fix: verify installed baseline and runtime closure"

---

### Task 8: Capture same-origin backgrounds without widening profile execution

**Files:**

- Modify: cloud_run/desktop_profile.py
- Modify: tests/python/test_desktop_profile.py

**Interfaces:**

- A dedicated parser accepts only /api/view?filename=<safe>&type=input with optional unique subfolder=<safe>.
- The verified source image becomes backgrounds/<sha256>.<suffix> in the profile archive.
- Only the remote settings copy is rewritten to /api/view?filename=cloud-vast/backgrounds/<sha256>.<suffix>&type=input.

- [ ] Add success tests for filename at input root and filename plus subfolder=backgrounds. Assert content-addressed copy, stable digest, remote settings rewrite, and unchanged local settings file.

- [ ] Add a table-driven rejection test for duplicate keys, unknown keys, type=output, absolute paths, percent-encoded dot traversal, encoded slash, invalid percent encoding, backslash, unsupported suffix, symlink final component, symlink intermediate component, non-owned file, and escape outside input_root.

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_desktop_profile.DesktopProfileCaptureTests -v

- [ ] Implement strict URL parsing with urlsplit, strict percent decoding, duplicate-key detection, and component-by-component lstat/open validation. Preserve the existing absolute-input-path behavior.

- [ ] Do not add JS or SVG to _UI_ASSET_SUFFIXES. Those remain package-only executable assets.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_desktop_profile -v
    git add cloud_run/desktop_profile.py tests/python/test_desktop_profile.py
    git diff --cached --check
    git commit -m "fix: capture safe same-origin Desktop backgrounds"

---

### Task 9: Wire the certified baseline through production preflight and profile capture

**Files:**

- Modify: cloud_run/resolver.py
- Modify: cloud_run/routes.py
- Modify: tests/python/test_resolver.py
- Modify: tests/python/test_routes.py
- Modify: tests/python/test_manifest.py

**Interfaces:**

- DependencyResolver accepts baseline custom_nodes and baseline local_artifacts in the approved profile payload.
- _RuntimeResolver receives a CertifiedBaselineResolver and awaits it before DesktopProfileStore.capture.
- Production capture receives exactly baseline.ui_packages and no generic executable ui_assets.
- Missing or changed baseline makes preflight non-rentable before quote confirmation.

- [ ] Add:

  - test_certified_efficiency_is_required_even_for_core_only_workflow
  - test_baseline_and_workflow_package_collision_fails_closed
  - test_baseline_local_artifacts_are_registered_for_upload
  - test_runtime_preflight_discovers_certified_baseline_without_injected_ui_packages
  - test_missing_or_changed_baseline_blocks_rentability
  - test_agent_panel_is_required_from_production_profile
  - test_archive_wheel_web_or_class_variation_changes_manifest_digest

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_resolver \
      tests.python.test_routes \
      tests.python.test_manifest -v

- [ ] Extend the approved profile payload to exactly:

    ui_packages
    custom_nodes
    local_artifacts
    profile
    minimum_vram_gb

  Validate every value and dependency before merging. Resolve duplicate package IDs deterministically and fail on any unequal collision.

- [ ] In _RuntimeResolver:

  1. await the certified baseline resolver using the production HTTPS fetcher and the existing application-private artifact root;
  2. call profile_store.capture with baseline.ui_packages and ui_assets=();
  3. return the baseline custom nodes and local artifacts to DependencyResolver;
  4. register all resulting local uploads before a rentable result is exposed.

- [ ] Keep the existing manifest dataclasses apart from Task 6's explicit content-addressed UI revision form. UiPackageSpec, CustomNodeSpec, wheel, and profile records bind the baseline into manifest digest, quote digest, durable intent, provisioning, and readiness.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_resolver \
      tests.python.test_routes \
      tests.python.test_manifest \
      tests.python.test_desktop_profile -v
    git add cloud_run/resolver.py cloud_run/routes.py \
      tests/python/test_resolver.py tests/python/test_routes.py \
      tests/python/test_manifest.py
    git diff --cached --check
    git commit -m "feat: include certified Desktop baseline in preflight"

---

### Task 10: Keep destruction visible and render lifecycle phases honestly

**Files:**

- Modify: cloud_run/routes.py
- Modify: web/js/cloud-run.js
- Modify: web/js/session-console.js
- Modify: tests/python/test_routes.py
- Modify: tests/js/cloud-run-ui.test.mjs
- Modify: tests/js/session-console.test.mjs

**Interfaces:**

- Settings payload gains active_sessions_error: safe string or None.
- A failed detailed render returns a minimal durable safety card rather than deleting the session.
- Provisioning payload gains stall_active: bool and separates completed transfer/install from Desktop readiness.

- [ ] Add backend tests:

  - test_settings_preserves_minimal_destroy_card_when_detailed_rendering_fails
  - test_settings_reports_bounded_error_when_active_session_listing_fails
  - test_one_broken_session_does_not_hide_other_active_sessions
  - test_ready_provision_transaction_reports_installed_units_as_validated_and_stops_stall_clock

- [ ] Define the minimal safety card as exactly:

    session_id
    instance_id
    status
    billing_may_continue
    can_destroy
    rate
    error

  Derive it only from durable CloudSession fields. Do not include offer objects, credentials, URLs, workflow data, or raw exceptions.

- [ ] Add JavaScript tests:

  - settings fallback card remains destroyable after detailed polling failure
  - billable fallback card is selected without rental_outcome
  - active session error renders a bounded red warning
  - ready provisioning is never shown as a transfer stall while Desktop readiness is pending
  - null seconds_without_progress does not become zero

- [ ] Run RED:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_routes -v
    node --test tests/js/cloud-run-ui.test.mjs \
      tests/js/session-console.test.mjs

- [ ] Render each session independently. On detailed failure, preserve the safety card and set active_sessions_error to Active session details are temporarily unavailable.

- [ ] In cloud-run.js, select a session when billing_may_continue is true even if rental_outcome is absent. In session-console.js, use rate as the fallback hourly value and retain the Destroy control during polling/detail errors.

- [ ] For a valid provision transaction with state ready:

  - validated_units equals installed_units;
  - stall_active is false;
  - seconds_without_progress is None/null;
  - transfer/install text says completed;
  - native Desktop readiness shows its own attempt/check state;
  - no transfer-stall danger class is applied.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_routes -v
    node --test tests/js/cloud-run-ui.test.mjs \
      tests/js/session-console.test.mjs
    git add cloud_run/routes.py web/js/cloud-run.js \
      web/js/session-console.js tests/python/test_routes.py \
      tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
    git diff --cached --check
    git commit -m "fix: preserve teardown controls and honest phase status"

---

### Task 11: Replace synthetic readiness with a real offline end-to-end campaign

**Files:**

- Modify: tests/python/test_fake_desktop_bridge_integration.py
- Modify: tests/python/test_native_transport_integration.py
- Create: tests/js/certified-baseline-frontend.test.mjs
- Create: tests/fixtures/frontend-1.47.10/extension-api.mjs
- Create: tests/fixtures/frontend-1.47.10/README.md

**Interfaces:**

- The fake provider stays offline.
- The fake Comfy backend stays loopback.
- Route policy, worker HMAC boundary, controller/worker HTTP transport, WebSocket transport, profile capture, certified baseline resolution, worker installers, readiness persistence, and destroy-review repository contract are real.

- [ ] Replace the injected acme CSS/UiPackageSpec profile setup with CertifiedBaselineResolver fixtures.

- [ ] Add one campaign proving:

  1. core-only workflow still includes Agent Panel, Hermes, and Efficiency baseline;
  2. safe background is captured and applied;
  3. worker provisioning measures UI packages and validates Efficiency classes;
  4. first readiness attempt may fail and second succeeds;
  5. real GET /system_stats and /ws cross both policies;
  6. Agent Panel is required and its scoped bridge capabilities pass;
  7. CivitAI, training, Apps, manager, reload/restart, arbitrary backend, and credential forwarding remain rejected;
  8. detailed status failure leaves a destroyable card;
  9. progress after review does not invalidate destruction;
  10. confirmed destruction persists intent first and complete fake inventory absence finalizes billing false;
  11. local execution guard remains zero.

- [ ] Define tests/fixtures/frontend-1.47.10/extension-api.mjs as the exact minimal public extension surface used by the three locked entrypoints: app.registerExtension, app.extensionManager.registerSidebarTab, api.addEventListener, api.queuePrompt, settings get/set, graph/canvas access, and the DOM primitives already supplied by tests/js/fake-dom.mjs. README.md records that this is an API-contract fixture, not a copy of the frontend package.

- [ ] Create the frontend compatibility test using the locked real Agent/Hermes/Efficiency entrypoints and that named frontend 1.47.10 API fixture. Resolve their browser-absolute /scripts imports only to the fixture inside the test harness. It must fail if an entrypoint requires an unavailable API or attempts a forbidden backend route.

- [ ] Run RED before replacing the synthetic probe:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_fake_desktop_bridge_integration \
      tests.python.test_native_transport_integration -v
    node --test tests/js/certified-baseline-frontend.test.mjs

- [ ] Implement only harness changes needed to traverse production boundaries. Do not introduce test-only bypasses in production.

- [ ] Run GREEN and commit:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_fake_desktop_bridge_integration \
      tests.python.test_native_transport_integration -v
    node --test tests/js/certified-baseline-frontend.test.mjs
    git add tests/python/test_fake_desktop_bridge_integration.py \
      tests/python/test_native_transport_integration.py \
      tests/js/certified-baseline-frontend.test.mjs \
      tests/fixtures/frontend-1.47.10
    git diff --cached --check
    git commit -m "test: certify live hardening boundaries offline"

---

### Task 12: Verify the repository-local result and prepare the gated handoff

**Files:**

- Read all changed files and commits
- No external mutation

- [ ] Run focused suites once more:

    scripts/run_with_comfyui_python.sh -m unittest \
      tests.python.test_worker_native_proxy \
      tests.python.test_worker_client \
      tests.python.test_native_transport_integration \
      tests.python.test_readiness \
      tests.python.test_desktop_relay \
      tests.python.test_job_repository \
      tests.python.test_repository \
      tests.python.test_reconciler \
      tests.python.test_lifecycle \
      tests.python.test_session_service \
      tests.python.test_certified_baseline \
      tests.python.test_desktop_profile \
      tests.python.test_worker_install \
      tests.python.test_worker_provision \
      tests.python.test_resolver \
      tests.python.test_routes \
      tests.python.test_fake_desktop_bridge_integration -v

    node --test tests/js/certified-baseline-frontend.test.mjs \
      tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs

- [ ] Run the full deterministic gate:

    scripts/check.sh

- [ ] Inspect final scope:

    git status --short
    git log --oneline dd32a0e..HEAD
    git diff --check dd32a0e..HEAD
    git diff --stat dd32a0e..HEAD

- [ ] Use superpowers:requesting-code-review for a final review of security invariants, migration safety, cancellation ordering, and test realism. Address only evidence-backed findings through new RED/GREEN commits.

- [ ] Use superpowers:verification-before-completion. Report exact fresh test counts, exact remaining limitations, and whether the certified baseline checkpoint is fully satisfied.

- [ ] Stop before all external actions. The next gates remain separately authorized and ordered:

  1. Hermes-owned external Desktop installation;
  2. one Desktop restart/reconnect and free preflight;
  3. immutable worker build and publication;
  4. private Vast template replacement while retaining the old template;
  5. user-authored paid GO with one-instance, hourly, duration, and total-cost bounds;
  6. user-operated manual destruction;
  7. independent Vast inventory zero plus billing_may_continue=false verification.

## Completion definition

Repository-local implementation is complete only when every targeted test has been observed RED for the intended reason, then GREEN, scripts/check.sh passes freshly, the worktree contains only intentional commits, the certified baseline lock is fully proven, and no known deterministic campaign blocker remains.

It is not live-certified merely because offline tests pass. Live certification requires the later immutable release/template rotation and the separately authorized one-pod acceptance campaign described in the approved design.

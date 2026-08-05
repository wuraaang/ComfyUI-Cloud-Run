# Safe Vast Create Reconciliation and Five-Session Gold Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair the first paid Vast create boundary, make every rental outcome truthful and actionable, then prove the unchanged extension build with three inexpensive core-output sessions and two complete Wallpaper Outpaint FLUX Fill 4K sessions.

**Architecture:** The local controller remains the sole orchestrator. It sends the controller-owned boundary as a typed request environment, persists one create intent before one provider mutation, serializes paid creates across sessions, and reconciles ambiguous results by one unique managed label. The frontend renders typed outcomes and capabilities rather than English error matching. The existing immutable worker, release, private template, and lock remain unchanged. Every provider create and remote run, plus every normal successful-session destruction, is a separate human click in ComfyUI; controller-owned failure/deadline cleanup remains a safety fallback and never counts as campaign success.

**Tech stack:** Python 3.13, unittest, SQLite, aiohttp-compatible Vast transport, JavaScript node:test, ComfyUI Desktop frontend 1.47.10, immutable GitHub worker release, private Vast template 522713.

**Approved design:** docs/superpowers/specs/2026-08-02-ambiguous-vast-create-reconciliation-design.md

## Frozen baseline

Work only in:

    /Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit

The implementation branch is:

    fix/vast-template-live-audit

The following remote assets are already reviewed and must remain byte-for-byte unchanged:

- worker commit: 76f2fff05f05b2fc7372b8cdf507d84dc794abe8
- release: worker-v1-76f2fff05f05b2fc7372b8cdf507d84dc794abe8
- worker archive SHA-256: 844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5
- private Vast template ID: 522713
- template hash: 9d6822f9429822ee9e7339a804a549da
- protocol: 1
- loaded lock:

      /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/worker-release.json

The real Gold workflow is:

    /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/Wallpaper Outpaint FLUX Fill 4K.json

Its acceptance contract is 32 active canvas nodes, no custom nodes, one real LoadImage input, five native model records, 29,347,469,703 resolved transfer bytes from the already validated frozen input, 89 GiB expected disk with a 1 GiB output allowance, and a final PNG of 3840 by 2160 pixels. The disk baseline is `ceil(40 + 29347469703 / 2^30 + 1 + 20) = 89 GiB`; the earlier 88 GiB observation used the previous one-byte allowance and is not the Gold campaign value.

## Non-negotiable safety boundaries

- Never create or rent an instance through CLI, a direct API call, a test, browser automation, or an agent click.
- Never destroy a paid instance through CLI, a direct API call, browser automation, or an agent click. The human uses the reviewed ComfyUI Destroy GPU flow.
- Stop at every external authorization gate. A free preflight or read-only provider GET does not authorize a paid action.
- Never issue a second Vast create for one reviewed session. Never automatically choose a replacement offer.
- Keep one instance maximum across the entire account campaign.
- Stop the campaign at the first failed, ambiguous, unverified, or over-budget session.
- Never recreate, edit, or delete the private Vast template. Never publish a second template.
- Do not change remote_worker, the worker release, the archive, the lock, or the protocol for this controller/frontend repair.
- Do not restart ComfyUI until the offline repair is committed, reviewed, fully verified, and the human gives a new explicit restart authorization.
- Never infer provider absence from an unavailable, partial, rate-limited, or malformed inventory response.
- Never persist or expose a raw provider body, header, URL, API key, boundary token, HMAC secret, signed URL, or private input name. Provider machine/host/public-IP identities remain confined to the already private reviewed quote for anti-switch checks; never return, log, or publish them.
- Use official Vast documentation and the pinned official client whenever a provider contract is uncertain. Record contradictions instead of choosing silently.

## Milestones and truthful completion language

There are four milestones:

1. **Controller implemented:** focused tests pass. This is not live-ready and not Gold.
2. **Controller-ready:** the committed clean worktree passes one fresh scripts/check.sh, receives independent review, and the unchanged lock is revalidated. This is still not Gold.
3. **Smoke-certified:** S1, S2, and S3 each returned a verified core image and ended destroyed with a fresh full Vast inventory of zero. This validates the rental boundary, not FLUX.
4. **Gold-validated:** G1 and G2 each returned a verified real 3840 by 2160 image, the human accepted each visual result, every successful session was manually destroyed, and final full inventory is zero.

Five instance IDs, five create responses, five ready states, or one FLUX image
are insufficient. Acceptance needs five counted successes on one frozen
baseline; failures do not count, require diagnosis and a new GO, and remain in
the lifetime-attempt report. Any code, lock, template, release,
campaign-workflow content, or Gold-input content change after S1 resets the 3
plus 2 success counter to zero. Merely opening the other already frozen smoke
or Gold canvas does not.

## Mandatory skill sequence during execution

At the beginning, announce and use superpowers:executing-plans or superpowers:subagent-driven-development. Use superpowers:test-driven-development for every behavior change. Use superpowers:systematic-debugging before proposing any fix for an unexpected RED, provider anomaly, or live failure. Use certifying-comfyui-cloud-workflows for both the smoke fixture and the two real preflights. Use superpowers:verification-before-completion before every milestone claim. Use superpowers:requesting-code-review before the restart gate.

If a Goal exists, inspect it first and work within it. Do not create a Goal unless the user explicitly requests one.

---

### Task 0: Re-enter the existing worktree without changing live state

**Files:**

- Read: docs/superpowers/specs/2026-08-02-ambiguous-vast-create-reconciliation-design.md
- Read: this plan
- Read: cloud_run/vast.py
- Read: cloud_run/models.py
- Read: cloud_run/repository.py
- Read: cloud_run/service.py
- Read: cloud_run/lifecycle.py
- Read: cloud_run/routes.py
- Read: web/js/session-console.js
- Read: web/js/cloud-run.js

- [ ] **Step 1: Verify branch and preserve user work**

    git status --short --branch
    git log -5 --oneline --decorate

Expected: branch fix/vast-template-live-audit, the design commit ab37f59 in history, and only intentional plan/spec edits if the planning commit has not yet been made. If unrelated edits appear, preserve them and stop before overlapping files.

- [ ] **Step 2: Re-read the complete design and plan**

Read both documents to EOF. Do not implement from this summary alone.

- [ ] **Step 3: Confirm the implementation phase is free and offline**

No Vast create, destroy, offer acceptance, template mutation, lock mutation, release publication, ComfyUI restart, or local prompt is authorized by Tasks 0 through 12.

- [ ] **Step 4: Capture the immutable local proof without printing secrets**

Load the lock with cloud_run.worker_release.load_worker_release and assert the exact worker commit, archive digest, template hash, and protocol from Frozen baseline. Record only pass/fail and the public digests.

---

### Task 1: Send the typed Vast create environment and preserve safe causes

**Files:**

- Modify: cloud_run/constants.py
- Modify: cloud_run/vast.py
- Modify: tests/python/test_vast.py

**Provider-source decision:**

The live create-endpoint reference currently describes env as a Docker-flag string. The current API workflow guide describes an object, the pinned official CLI revision bc735648 parses CLI flags into an object before PUT, and its InstanceConfig model types env as dict[str, str]. Follow the workflow guide and official client object form, but keep the contradiction in code-test comments and do not call it the proven historical cause.

Authoritative sources:

- https://docs.vast.ai/api-reference/instances/create-instance
- https://docs.vast.ai/api-reference/creating-instances-with-api
- https://github.com/vast-ai/vast-cli/blob/bc7356483dd0f922ee17975c5357741f0d5d81fc/vast.py#L2477-L2534
- https://github.com/vast-ai/vast-cli/blob/bc7356483dd0f922ee17975c5357741f0d5d81fc/vastai/data/instance.py#L6-L31
- https://docs.vast.ai/api-reference/rate-limits-and-errors
- https://docs.vast.ai/api-reference/instances/show-instances

**Interfaces:**

    VAST_CREATE_FAILURE_CODES = frozenset({
        "configuration_rejected",
        "api_key_rejected",
        "offer_unavailable",
        "rate_limited",
        "retryable_http",
        "timeout",
        "connection",
        "tls",
        "server_disconnected",
        "invalid_response",
        "confirmation_interrupted",
        "transport_unknown",
    })

    VAST_CREATE_CONFIGURATION_REVISION = "typed-env-object-v1"

    class VastError(RuntimeError):
        code: str | None
        status: int | None
        retryable: bool

    def _worker_environment(boundary_token, session_id):
        return {
            "CLOUD_RUN_BOUNDARY_TOKEN": boundary_token,
            "CLOUD_RUN_SESSION_ID": session_id,
        }

The create payload contains exactly template_hash_id, label, disk, and the two-key env object. Keep template port configuration in the existing private template.

`VAST_CREATE_CONFIGURATION_REVISION` is a private, non-secret compatibility
identifier for the reviewed create-payload contract. It changes deliberately
only when a verified repair addresses a provider 400; changing comments or UI
does not bump it. A loaded differing revision is the typed backend proof needed
to lift an older configuration-rejected gate after the separately authorized
restart.

**Stable create classification:**

| Evidence | Safe code | Definitive absence? |
|---|---|---:|
| HTTP 400 | configuration_rejected | yes |
| HTTP 401 or 403 | api_key_rejected | yes |
| HTTP 404 or 410 | offer_unavailable | yes |
| HTTP 429 | rate_limited | no |
| HTTP 408, 409, or 5xx | retryable_http | no |
| recognized timeout | timeout | no |
| TLS/certificate failure | tls | no |
| server disconnect | server_disconnected | no |
| other recognized connection failure | connection | no |
| invalid or incomplete HTTP 200 | invalid_response | no |
| unknown transport exception | transport_unknown | no |

Classify specific TLS and disconnect subclasses before their broader connection superclass. Re-raise cancellation and keyboard interruption. The public message, str(error), and repr(error) must remain static and contain no provider bytes or submitted boundary values.

- [ ] **Step 1: Write the failing request-contract tests**

Update test_create_uses_only_the_injected_reviewed_project_template so env must equal the exact dictionary. Add failures for extra keys, malformed session IDs, malformed boundary tokens, and any request made after local validation fails.

- [ ] **Step 2: Write the failing safe-cause tests**

Add:

- test_create_transport_failures_have_stable_safe_codes
- test_invalid_success_response_is_ambiguous_and_sanitized
- test_create_error_repr_never_exposes_provider_or_boundary_markers
- extend test_create_maps_provider_status_without_echoing_response_details

Use hostile provider markers in bodies and exception text. Assert the marker and both secrets are absent from exception strings, repr, logs captured by the test, and serialized public structures.

- [ ] **Step 3: Write the failing full-inventory pagination tests**

The official v1 inventory endpoint is paginated with at most 25 rows. Add:

- test_list_instances_follows_every_next_token_before_returning
- test_list_instances_rejects_repeated_or_malformed_pagination
- test_list_instances_never_treats_429_or_partial_pages_as_empty
- test_list_instances_rejects_any_invalid_instance_row
- test_list_instances_requires_success_and_exact_page_counts
- test_list_instances_rejects_incoherent_totals_and_conflicting_duplicates
- test_list_instances_rejects_any_duplicate_id_across_pages
- test_list_instances_rejects_terminal_short_snapshot
- test_list_instances_proves_zero_only_from_one_complete_zero_snapshot

Require `limit=25`, pass each `after_token`, and follow `next_token` until it is
null. Every page must be an object with `success is True`, an `instances` list,
a non-boolean non-negative integer `instances_found` equal to that page's list
length, and one coherent non-negative integer `total_instances` across every
page. Every instance row must normalize successfully; never filter a malformed
row out. Reject any duplicate contract ID across pages, even if its fields are
identical, because overlap makes the snapshot unstable. A duplicate ID,
repeated token, invalid page, count mismatch, total mismatch, 429, or later-page
error fails the whole snapshot closed and returns no partial list. At terminal
`next_token is None`, require both the sum of every page's `instances_found`
and the number of unique normalized instances to equal `total_instances`.
Exact zero additionally requires all three values to be zero.

- [ ] **Step 4: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_vast.VastLifecycleRequestTests -v

Expected RED: env is still a string, VastError has no stable code, invalid 200 is not marked ambiguous, and inventory does not paginate.

- [ ] **Step 5: Implement the minimum transport change**

Keep response parsing and messages centralized. The implementation must not parse raw provider text to choose a safe code. Use HTTP status and local exception class only.

- [ ] **Step 6: Run GREEN**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_vast.VastLifecycleRequestTests -v

Expected: all tests in the class pass with no network access.

- [ ] **Step 7: Commit**

    git add cloud_run/constants.py cloud_run/vast.py tests/python/test_vast.py
    git commit -m "fix: send typed Vast create environment"

---

### Task 2: Persist create reconciliation and derive one truthful public matrix

**Files:**

- Modify: cloud_run/models.py
- Modify: cloud_run/repository.py
- Modify: cloud_run/capture.py
- Modify: cloud_run/settings.py
- Modify: cloud_run/session_service.py
- Modify: tests/python/test_models.py
- Modify: tests/python/test_repository.py
- Modify: tests/python/test_capture.py
- Modify: tests/python/test_settings.py
- Modify: tests/python/test_session_service.py

**Durable model additions:**

    SessionState.RECONCILING_CREATE

    CloudSession.failure_code: str | None
    CloudSession.create_reconcile_started_at: float | None
    CloudSession.create_empty_observations: int
    CloudSession.create_first_empty_at: float | None
    CloudSession.create_last_empty_at: float | None
    CloudSession.create_settings_revision: str | None
    CloudSession.create_configuration_revision: str | None
    CloudSession.remediation_verified_at: float | None
    CloudSession.remediation_revision: str | None
    CloudSession.execution_baseline_digest: str
    CloudSession.randomized_seed_node_ids: tuple[str, ...]

    CloudSession.record_create_empty_observation(*, now: float)
    CloudSession.create_absence_verified
    CloudSession.rental_outcome
    CloudSession.can_search_offers
    CloudSession.can_destroy
    CloudSession.can_verify_vast_access
    CloudSession.billing_may_continue
    CloudSession.blocks_new_rental

    certified_execution_baseline(capture) -> tuple[str, tuple[str, ...]]

Persist the following SQLite columns through create, migration, read, insert, save, and compare-and-swap update:

    failure_code TEXT
    create_reconcile_started_at REAL
    create_empty_observations INTEGER NOT NULL DEFAULT 0
    create_first_empty_at REAL
    create_last_empty_at REAL
    create_settings_revision TEXT
    create_configuration_revision TEXT
    remediation_verified_at REAL
    remediation_revision TEXT
    execution_baseline_digest TEXT
    randomized_seed_node_ids_json TEXT

Advance the shared tested schema version from 5 to 6 in this migration.

The observation count saturates at three, but every later successful full empty
snapshot still advances `create_last_empty_at`. Count zero requires both
timestamps null. A positive count requires finite ordered timestamps. Verified
absence requires at least three successful empty observations and last minus
first at least 120 seconds. Elapsed wall time alone never proves absence.

OfferQuote continues to read historical max_instance_creates equal to two, but new quotes will later accept only one.

`SettingsStore` adds a private UUID `api_key_revision`. It is created by the
legacy-settings migration and rotated only when an API key is explicitly
updated; unrelated price/VRAM edits preserve it. It is never returned or
logged. A 400/401/403 failure without typed remediation derives
`blocks_new_rental=true`; a revision string alone is insufficient unless the
repository's remediation transition validated the matching proof.

`certified_execution_baseline` hashes only the executable prompt output, queue
options, and the sorted normalized-seed node set. It derives seed nodes from an
exact active workflow/core-output pairing where class type is `KSampler`, the
workflow control is exactly `randomize`, and the executable seed is a valid
integer. It replaces only that numeric executable seed with a sentinel before
canonical hashing. Any ambiguous pairing fails closed. For the committed smoke
the set is empty; for the frozen Gold workflow it must be exactly `("3",)`.
Preflight, OfferQuote, and CloudSession persist the digest and node set.

- [ ] **Step 1: Write the failing state/evidence tests**

Add to LifecycleModelTests:

- test_reconciling_create_transitions_and_evidence_are_validated
- test_three_empty_observations_must_span_120_seconds
- test_saturated_empty_count_still_updates_last_empty_timestamp
- test_public_rental_outcome_and_capability_matrix_is_exact
- test_failed_session_with_uncleared_secrets_remains_unknown
- test_definitive_or_verified_absence_requires_cleared_secrets
- test_quote_and_session_repr_omit_private_provider_identity
- test_unremediated_configuration_and_api_failures_block_new_rental
- test_verify_access_capability_is_true_only_for_unremediated_api_key_failure
- test_remediation_revisions_are_private_and_strictly_validated
- test_execution_baseline_normalizes_only_certified_randomized_ksampler_seed
- test_execution_baseline_changes_for_every_other_prompt_or_queue_edit
- test_submit_job_accepts_only_a_seed_normalized_reviewed_baseline
- test_canvas_modified_after_preflight_issues_zero_worker_jobs

Cover every row of the capability matrix in the approved design. A normal ready session is active and billing_may_continue true without being an emergency. Creating and reconciling are unknown, searchable false, destroyable true. Failed plus absent is searchable only when its failure code permits a fresh offer.

- [ ] **Step 2: Write the failing migration/query tests**

Add to SessionRepositoryTests:

- test_create_reconciliation_evidence_survives_reopen
- test_legacy_database_migration_defaults_reconciliation_evidence
- test_recoverable_filter_retains_unknown_active_and_orphaned_confirming
- test_recoverable_filter_excludes_failed_absent
- test_recent_sessions_query_is_bounded
- test_remediation_and_execution_baseline_survive_legacy_migration_and_reopen
- test_api_key_revision_rotates_only_for_explicit_key_update_and_stays_private

Define:

    SessionRepository.list_recent(limit=20)

Reject booleans, non-integers, values below one, and values above 20. Query newest first in SQL, bound before model construction, then return chronological order for rendering.

- [ ] **Step 3: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_models.LifecycleModelTests \
      tests.python.test_repository.SessionRepositoryTests \
      tests.python.test_capture tests.python.test_settings \
      tests.python.test_session_service.PreflightTests \
      tests.python.test_session_service.ReusableSessionTests -v

Expected RED: missing state, fields, migration, public capabilities, and bounded history.

- [ ] **Step 4: Implement model and repository support**

Permit RECONCILING_CREATE to adopt one instance, fail, or enter destruction. Permit CONFIRMING to return to OFFER_SELECTED only for a blocked pre-mutation paid claim. Add the new fields to CloudSession.transition allowed changes.

Do not expose evidence timestamps/counts, remediation revisions, API-key
revision, idempotency keys, labels, private quote identities, or secrets in
public_payload. Mark the private machine, host, and public-IP quote fields repr
false while retaining them in private to_record persistence.

- [ ] **Step 5: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_models.LifecycleModelTests \
      tests.python.test_repository.SessionRepositoryTests \
      tests.python.test_capture tests.python.test_settings \
      tests.python.test_session_service.PreflightTests \
      tests.python.test_session_service.ReusableSessionTests -v
    git add cloud_run/models.py cloud_run/repository.py cloud_run/capture.py \
      cloud_run/settings.py cloud_run/session_service.py \
      tests/python/test_models.py tests/python/test_repository.py \
      tests/python/test_capture.py tests/python/test_settings.py \
      tests/python/test_session_service.py
    git commit -m "feat: persist ambiguous create reconciliation"

---

### Task 3: Serialize all paid create claims in one SQLite transaction

**Files:**

- Modify: cloud_run/repository.py
- Modify: cloud_run/service.py
- Modify: tests/python/test_repository.py
- Modify: tests/python/test_service.py

**Interfaces:**

    class PaidRentalConflict(RuntimeError):
        pass

    SessionRepository.claim_create_intent(
        session_id,
        *,
        now,
        provider_token,
        session_secret_hex,
    ) -> CloudSession

claim_create_intent must use one BEGIN IMMEDIATE transaction to:

1. reload a target in CONFIRMING;
2. load every other session, including terminal FAILED/DESTROYED history;
3. reject if another session derives rental_outcome unknown or active, has a
   non-null instance ID, retains any residual inventory ID, or derives
   blocks_new_rental from an unremediated 400/401/403;
4. transition the target to CREATING with both generated secrets;
5. commit before the provider PUT.

Do not implement a service-level check followed by a separate write. On conflict, return the target to OFFER_SELECTED without a provider call and raise a static PaidRentalConflict. A fresh manual Confirm click remains required after the conflict is gone.

New preview requests must accept max_instance_creates equal to one only. Historical quote rows containing two remain readable and can never trigger a replacement.

- [ ] **Step 1: Write the failing atomicity tests**

Add:

- test_atomic_create_claim_blocks_another_unknown_session
- test_atomic_create_claim_blocks_another_active_session
- test_failed_with_instance_blocks_create
- test_failed_with_residual_inventory_blocks_create
- test_unremediated_400_401_403_block_direct_create_claim
- test_preflight_search_and_reload_never_lift_remediation_block
- test_typed_newer_revision_proof_lifts_only_the_matching_blocker
- test_concurrent_sessions_issue_only_one_provider_create
- test_blocked_confirmation_returns_to_pre_mutation_state
- test_historical_create_limit_two_remains_readable_but_new_preview_rejects_two

Update:

- test_paid_session_create_limit_is_validated_before_offer_lookup
- test_paid_confirmation_persists_secret_and_intent_before_one_put
- test_concurrent_paid_confirmations_issue_exactly_one_create

- [ ] **Step 2: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_repository.SessionRepositoryTests \
      tests.python.test_service.CloudRunServiceTests -v

Expected RED: concurrent sessions can both pass the current non-transactional boundary and new previews still accept two.

- [ ] **Step 3: Implement the transactional claim**

Generate each 64-hex secret immediately before claim_create_intent. If the claim loses a concurrent update, reload and return the durable state. Never generate a second provider mutation from a duplicate confirmation.

- [ ] **Step 4: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_repository.SessionRepositoryTests \
      tests.python.test_service.CloudRunServiceTests -v
    git add cloud_run/repository.py cloud_run/service.py \
      tests/python/test_repository.py tests/python/test_service.py
    git commit -m "fix: serialize paid Vast create claims"

---

### Task 4: Persist explicit initial create outcomes, including Destroy versus PUT

**Files:**

- Modify: cloud_run/service.py
- Modify: tests/python/test_service.py

**Outcome algorithm:**

- One valid new_contract: attach that exact ID unless destruction was already requested.
- Definitive 400, 401, 403, 404, or 410: FAILED plus absent, preserve safe failure_code and the private settings/configuration revisions used, clear both secrets atomically, no inventory wait needed. A 400/401/403 remains a backend paid blocker until its matching typed remediation proof.
- Ambiguous result plus one exact labelled inventory match: adopt it with the original boundary.
- Ambiguous result plus multiple matches: fail closed, retain every residual ID, expose emergency destruction.
- Ambiguous result plus a successful empty inventory: enter RECONCILING_CREATE and record the first empty observation.
- Ambiguous result plus unavailable inventory: enter RECONCILING_CREATE with zero observations.

Always issue exactly one create_instance call.

If the human makes destroy_requested durable while PUT is in flight:

- a late new_contract is attached only to the destruction path;
- a definitive rejection finalizes DESTROYED without DELETE;
- an ambiguous result remains on the destruction path and records reconciliation evidence there;
- no completion may transition back to CREATING or RECONCILING_CREATE.

- [ ] **Step 1: Adapt existing ambiguous-create tests**

Adapt:

- test_ambiguous_initial_create_adopts_one_consumed_instance
- test_ambiguous_initial_session_create_fails_closed_on_multiple_matches
- test_destroy_requested_during_create_is_finished_by_the_creator

- [ ] **Step 2: Add failing terminal/race tests**

Add:

- test_ambiguous_empty_create_enters_reconciliation_without_retry
- test_inventory_outage_enters_reconciliation_without_empty_evidence
- test_every_definitive_create_rejection_clears_both_secrets
- test_configuration_and_api_rejections_persist_private_failure_revisions
- test_destroy_racing_ambiguous_create_never_returns_to_rental_state
- test_duplicate_confirmation_during_reconciliation_never_creates

- [ ] **Step 3: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_service.CloudRunServiceTests -v

- [ ] **Step 4: Implement one outcome reducer**

Centralize the state decision so normal completion, exception completion, and destroy-race completion use the same rules. Do not branch on error message text.

- [ ] **Step 5: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_service.CloudRunServiceTests -v
    git add cloud_run/service.py tests/python/test_service.py
    git commit -m "fix: persist explicit Vast create outcomes"

---

### Task 5: Reconcile for 120 seconds without entering the worker boot timeout

**Files:**

- Modify: cloud_run/constants.py
- Modify: cloud_run/lifecycle.py
- Modify: cloud_run/service.py
- Modify: cloud_run/session_service.py
- Modify: tests/python/test_lifecycle.py
- Modify: tests/python/test_service.py
- Modify: tests/python/test_session_service.py

**Constants:**

    CREATE_RECONCILE_REQUIRED_EMPTY_OBSERVATIONS = 3
    CREATE_RECONCILE_MIN_SPAN_SECONDS = 120
    CREATE_RECONCILE_POLL_TARGETS_SECONDS = (0, 15, 30, 60, 120)
    CREATE_RECONCILE_OUTAGE_POLL_SECONDS = 60

Official Vast rate-limit behavior is per endpoint and identity, reports 429, and currently provides no standard Retry-After header. Use one watchdog per session, the persisted target schedule, and no more than one inventory attempt per minute after an outage. Browser refresh must reuse the watchdog and never create a GET burst.

- [ ] **Step 1: Write the failing evidence-window tests**

Add to SessionLifecycleTests:

- test_empty_create_requires_three_snapshots_spanning_120_seconds
- test_empty_schedule_0_15_30_60_120_terminalizes_only_at_120
- test_inventory_outage_never_counts_as_empty
- test_rate_limited_inventory_never_counts_as_empty
- test_restart_preserves_first_and_last_empty_observations
- test_rapid_post_restart_reads_cannot_complete_reconciliation
- test_concurrent_refreshes_share_one_inventory_watchdog

- [ ] **Step 2: Write the failing restart/adoption tests**

Add:

- test_orphaned_confirming_terminalizes_without_inventory_or_create
- test_orphaned_creating_enters_reconciliation_from_original_timestamp
- test_late_unique_match_is_adopted_with_original_boundary
- test_multiple_late_matches_retain_every_residual

An orphaned CONFIRMING is pre-mutation, so it becomes FAILED plus absent with confirmation_interrupted and zero provider calls. An orphaned CREATING is ambiguous and must not become absent from elapsed time alone.

- [ ] **Step 3: Write the failing ambiguous-destroy tests**

Add:

- test_manual_destroy_remains_pending_until_empty_evidence_is_complete
- test_manual_destroy_destroys_one_late_match_exactly_once
- test_destroy_inventory_outage_remains_unknown

In ReusableSessionTests add:

- test_ambiguous_destroy_review_explains_late_instance_cleanup

The warning must say no instance ID is currently identified and that any delayed matching instance will be destroyed under this authorization.

- [ ] **Step 4: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_lifecycle.SessionLifecycleTests \
      tests.python.test_service.CloudRunServiceTests \
      tests.python.test_session_service.ReusableSessionTests -v

- [ ] **Step 5: Implement a separate reconciliation watchdog**

reconcile_session_once must distinguish inventory unavailable from a strictly
complete empty snapshot. Exercise the exact healthy schedule at 0, 15, 30, 60,
and 120 seconds: the counter saturates at three while `last_empty_at` continues
to advance, and the session remains nonterminal through 60 seconds before
terminalizing only at 120. RECONCILING_CREATE never calls
handle_session_boot_failure, never blacklists a host, and never searches or
creates. At qualifying absence, transition to FAILED plus absent, clear both
secrets and reconciliation evidence, and use the static verified-absence
message.

Destroy reconciliation shares the evidence helpers but remains DESTROY_REQUESTED or DESTROYING until terminal. One late match is deleted once, then full absence is verified.

- [ ] **Step 6: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_lifecycle.SessionLifecycleTests \
      tests.python.test_service.CloudRunServiceTests \
      tests.python.test_session_service.ReusableSessionTests -v
    git add cloud_run/constants.py cloud_run/lifecycle.py cloud_run/service.py \
      cloud_run/session_service.py tests/python/test_lifecycle.py \
      tests/python/test_service.py tests/python/test_session_service.py
    git commit -m "fix: reconcile ambiguous Vast creates safely"

---

### Task 6: Remove every automatic paid replacement path

**Files:**

- Modify: cloud_run/models.py
- Modify: cloud_run/lifecycle.py
- Modify: tests/python/test_models.py
- Modify: tests/python/test_lifecycle.py
- Modify: tests/python/test_fake_session_integration.py

Remove provider mutation producers from:

- handle_start_failure
- handle_session_boot_failure
- recovery of historical RETRYING attempts
- FAILED or DESTROYING transitions formerly used to create a replacement

These paths may destroy, verify absence, preserve a safe diagnostic, and terminalize. They may not blacklist for replacement, search offers, call create_instance, or consume max_instance_creates equal to two.

- [ ] **Step 1: Replace old replacement expectations**

Replace tests that expect automatic replacement, including:

- test_transient_failure_replaces_once_after_verified_destroy_and_blacklist
- test_only_boot_failure_replaces_once_after_inventory_absence
- test_attempt_replacement_rotates_boundary_before_create
- test_session_replacement_rotates_boundary_before_create

With:

- test_attempt_boot_failure_with_historical_limit_two_never_searches_or_creates
- test_session_boot_failure_with_historical_limit_two_never_searches_or_creates
- test_authentication_failure_destroys_without_replacement
- test_restart_never_resurrects_historical_retry_state
- test_no_state_transition_can_issue_a_second_paid_create

- [ ] **Step 2: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_models.LifecycleModelTests \
      tests.python.test_lifecycle.RecoveryAndReplacementTests \
      tests.python.test_lifecycle.SessionLifecycleTests \
      tests.python.test_fake_session_integration -v

Expected RED: legacy handlers still search/create a replacement.

- [ ] **Step 3: Delete mutation behavior, not historical readability**

Keep deserialization for max_instance_creates equal to two and legacy states. Convert historical RETRYING recovery to a safe terminal or destruction flow with zero create calls.

- [ ] **Step 4: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_models.LifecycleModelTests \
      tests.python.test_lifecycle.RecoveryAndReplacementTests \
      tests.python.test_lifecycle.SessionLifecycleTests \
      tests.python.test_fake_session_integration -v
    git add cloud_run/models.py cloud_run/lifecycle.py \
      tests/python/test_models.py tests/python/test_lifecycle.py \
      tests/python/test_fake_session_integration.py
    git commit -m "fix: remove automatic Vast replacements"

---

### Task 7: Expose active versus recent sessions and map paid conflicts to 409

**Files:**

- Modify: cloud_run/routes.py
- Modify: cloud_run/session_service.py
- Modify: cloud_run/repository.py
- Modify: cloud_run/settings.py
- Modify: tests/python/test_routes.py
- Modify: tests/python/test_session_service.py
- Modify: tests/python/test_repository.py
- Modify: tests/python/test_settings.py

The settings payload gains:

    {
      "active_sessions": [],
      "recent_sessions": []
    }

active_sessions contains only recoverable unknown or active sessions. recent_sessions comes from SessionRepository.list_recent(limit=20), is fully sanitized, and may contain terminal failed plus absent records so a reload preserves the last actionable result.

PaidRentalConflict maps to HTTP 409 with one static public message. It must be caught before generic service errors. Do not add a route that bypasses CloudRunService.confirm_session.

Add the explicit free recovery endpoint:

    POST /cloud-run/api/settings/verify-vast-access

It accepts no provider data. It loads the current private API key and its
revision, performs one Task 1 strict full inventory read, and only after success
calls `SessionRepository.mark_api_access_remediated(settings_revision, now)`.
That repository transaction may mark only terminal absent `api_key_rejected`
records whose secrets/instance/residuals are clear and whose failed settings
revision differs. Return only `verified=true` and `instance_count`; never rows,
key, revision, or provider text. A failed/partial/429 read writes no evidence.

At controller startup, record the loaded
`VAST_CREATE_CONFIGURATION_REVISION`. In one repository transaction mark only
terminal absent `configuration_rejected` records with clear private/provider
state and a different failed revision. The current revision itself remains
private. This startup write occurs only when the repaired controller is
actually loaded after the separately authorized restart; offline imports and
preflight do not touch the live database.

- [ ] **Step 1: Write the failing settings tests**

Update:

- SettingsRouteTests.test_get_returns_only_public_defaults
- SettingsRouteTests.test_put_saves_key_but_returns_only_public_settings

Add:

- test_get_separates_recoverable_active_sessions_from_bounded_recent_history
- test_settings_history_never_exposes_reconciliation_evidence_or_secrets
- test_verify_vast_access_requires_new_settings_revision_and_full_inventory
- test_verify_vast_access_failure_or_429_writes_no_remediation
- test_loaded_new_configuration_revision_remediates_only_safe_400_failures

Use more than 20 records. Assert the newest 20 only, chronological rendering order, and no labels, idempotency keys, provider tokens, HMAC secrets, quote machine/host/public-IP fields, or private evidence timestamps/counts.

Add SessionServiceTests.test_public_offer_search_omits_provider_identity. The
browser offer projection must omit machine_id, host_id, and public_ipaddr while
retaining offer ID, GPU/VRAM, price, reliability, transfer-speed/cost metrics,
and estimated transfer time. preview_session still revalidates the offer
server-side and stores those identities only in the private quote.

Add `test_exact_offer_revalidation_proves_private_safety_filters`. Assert the
backend-normalized offer contract rejects any non-on-demand, multi-GPU,
unverified, non-NVIDIA/amd64, insufficient compute-capability/CUDA offer before
review, while none of the private proof fields or identities is added merely to
make the browser checklist work.

- [ ] **Step 2: Write the failing direct-route gate test**

Add:

- PaidSessionRouteTests.test_confirm_returns_409_when_another_rental_is_active_or_unknown
- PaidSessionRouteTests.test_direct_confirm_route_cannot_bypass_inter_session_paid_gate
- PaidSessionRouteTests.test_direct_confirm_stays_409_before_typed_remediation_and_succeeds_after

Assert zero provider create calls and no reflection of request or provider markers.

- [ ] **Step 3: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_routes.SettingsRouteTests \
      tests.python.test_routes.PaidSessionRouteTests \
      tests.python.test_session_service.SessionServiceTests -v

Expected RED: recent_sessions is absent and the conflict is generic.

- [ ] **Step 4: Implement the bounded payload and error mapping**

Keep active selection repository-backed. Do not infer activity from state strings inside routes when CloudSession already derives rental_outcome.

- [ ] **Step 5: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_routes tests.python.test_session_service -v
    git add cloud_run/routes.py cloud_run/session_service.py \
      cloud_run/repository.py cloud_run/settings.py \
      tests/python/test_routes.py tests/python/test_session_service.py \
      tests/python/test_repository.py tests/python/test_settings.py
    git commit -m "feat: expose active and recent rental outcomes"

---

### Task 8: Drive the session console from typed capabilities

**Files:**

- Modify: web/js/session-console.js
- Modify: web/js/cloud-run-api.js
- Create: tests/js/cloud-run-api.test.mjs
- Modify: tests/js/session-console.test.mjs

**Exact ambiguous message:**

    GPU rental could not be confirmed. Cloud Run is checking Vast inventory.
    Do not start another rental yet.
    Billing status is not yet known; Vast may have created an instance.

**Exact verified-absence message:**

    GPU rental failed. Vast inventory confirms that no instance is active for
    this session. No Vast billing is active. Search again and choose another GPU.

Buttons use can_search_offers and can_destroy only. No action is inferred from error or failure_code text.

- [ ] **Step 1: Update fixtures and write the failing capability tests**

A ready fixture is rental_outcome active, billing_may_continue true, can_search_offers false, can_destroy true. A reviewed quote is not_started. Add:

- ambiguous rental outcome is red, polls, blocks paid controls, and keeps destroy available
- verified absence clears stale review and enables only a fresh offer search
- repeated fresh rentable preflight alone never clears a 400/401/403 gate
- API-key failure exposes only the explicit free Verify Vast access action;
  configuration failure has no client-side bypass
- Verify Vast access POSTs no body/provider data, then reloads settings/session;
  400, 404/410, unknown, active, and remediated states never show the button
- inventory outage never claims that Vast billing is inactive
- normal active session bills without rendering an emergency residual warning

- [ ] **Step 2: Write the failing output-allowance usability test**

The raw number field currently has no visible label. Require a visible label tied to cloud-run-output-allowance, help text containing both:

    16 MiB = 16777216
    1 GiB = 1073741824

Require inputMode numeric, an editable positive-integer value, and the exact integer sent to preflight. Keep blank meaning no explicit value only if the backend still rejects it safely; the live campaign always uses an explicit value.

- [ ] **Step 3: Run RED**

    node --test tests/js/session-console.test.mjs \
      tests/js/cloud-run-api.test.mjs

- [ ] **Step 4: Implement typed rendering and reset**

Add reconciling_create to sessionMessage and polling. Replace providerMutationMayHaveOccurred with can_destroy. Compute Search as:

    a rentable preflight exists
    AND no session exists or session.can_search_offers is true
    AND the console is not busy

Expose a canSearchOffers getter to the parent. Add clearPaidReview to erase the internal idempotency key, empty/hide quote and Confirm, and cancel any pending destroy review. Call it for rental_outcome absent.

When a new rentable preflight succeeds, clear the currently selected historical
session only when that terminal absent session already derives
`can_search_offers=true` (for example offer_unavailable, confirmation
interrupted, or verified empty reconciliation). Never clear a 400/401/403,
unknown, or active record this way, and keep every historical record in
recent_sessions. Workflow preflight proves workflow rentability, not Vast
credentials or instance-create configuration.

For 401/403, the UI instructs the user to correct settings and the operational
gate requires the explicit Verify Vast access click and its subsequent
successful authenticated full read-only inventory before a separately
authorized campaign can resume. The button calls only the typed Task 7 endpoint
through `cloud-run-api.js`, POSTs no body, then reloads settings and the exact
session before reevaluating capabilities. Render it solely from
`can_verify_vast_access`; never match `failure_code` or message text. Clear the
historical selection only after its refreshed `can_search_offers` is true. A
failed verification or 429 leaves every control/state unchanged. For 400, stop
the campaign:
resumption requires a demonstrated controller/configuration repair, offline
verification, any newly authorized backend reload, and a new exact paid GO.
This plan deliberately adds no generic "clear failure" button that could
bypass those proofs. A human must not work around either gate by deleting state.

Only unknown, or active plus failed/residual inventory, renders as red. A normal ready/running/harvesting active session gets the normal billing banner.

- [ ] **Step 5: Run GREEN and commit**

    node --test tests/js/session-console.test.mjs \
      tests/js/cloud-run-api.test.mjs
    git add web/js/session-console.js web/js/cloud-run-api.js \
      tests/js/session-console.test.mjs tests/js/cloud-run-api.test.mjs
    git commit -m "feat: gate session controls by rental outcome"

---

### Task 9: Require a fresh browser review and remove the misleading 1-or-2 selector

**Files:**

- Modify: web/js/cloud-run.js
- Modify: tests/js/cloud-run-ui.test.mjs
- Already modified in Task 8: web/js/cloud-run-api.js

The create limit is a product invariant, not a user-tunable field. Remove cloud-run-max-instance-creates and render static review text:

    Maximum provider creates for this review: 1.
    Cloud Run never rents a replacement automatically.

Every create-session request sends max_instance_creates equal to one.

Add a separately labelled bounded session-duration control with 30, 60, 90,
and 120 minute choices. It controls deadline.duration_seconds only. Keep 120
minutes as the product default; the campaign explicitly selects 30 minutes for
S1-S3 and 90 minutes for G1-G2. This makes the human-reviewed cost bound real
without weakening the existing separate acknowledgement for disabling a
deadline after ready.

- [ ] **Step 1: Write the failing manual-gate tests**

Add:

- ambiguous active session blocks search review and confirm after preflight
- reload prioritizes unknown or active sessions over recent absent history
- reload renders the latest failed absent session without an active billing banner
- verified absence clears browser offer and idempotency before another manual rental
- ready active session uses the normal paid banner while unknown and residual failures are red
- create review has one fixed create and no one-or-two selector
- reviewed duration sends exactly the selected finite deadline

For verified absence, prove there is no automatic call to offers, sessions, or confirm. The next legal sequence is Search, choose a new offer, Review, then a fresh manual Confirm with a new crypto.randomUUID value.

- [ ] **Step 2: Run RED**

    node --test tests/js/cloud-run-ui.test.mjs

- [ ] **Step 3: Implement browser reset and fixed create count**

Add clearBrowserPaidReview to clear exactly:

- selectedOffer
- sessionIdempotencyKey
- rendered offers
- Review enabled state

Call it for every absent outcome. Make setBusy and the Search handler respect sessionConsole.canSearchOffers so the parent cannot accidentally re-enable the shared button.

loadSettings selection order is:

1. newest unknown or active from active_sessions;
2. otherwise newest failed plus absent from recent_sessions;
3. never render the latter as an active paid session.

Keep all provider-derived values in textContent.

Translate only the fixed duration choices to seconds. Reject any DOM-forged
value rather than falling back to a longer duration.

- [ ] **Step 4: Run GREEN and commit**

    node --test tests/js/cloud-run-ui.test.mjs
    git add web/js/cloud-run.js tests/js/cloud-run-ui.test.mjs
    git commit -m "feat: require fresh review after failed rental"

---

### Task 10: Commit one zero-model smoke canvas and job-owned output evidence

**Files:**

- Create: tests/fixtures/cloud-run-core-output-smoke.json
- Create: scripts/validate_smoke_output.py
- Create: scripts/verify_session_output.py
- Create: scripts/run_with_comfyui_python.sh
- Create: tests/python/test_smoke_output_validation.py
- Create: tests/python/test_session_output_evidence.py
- Modify: cloud_run/repository.py
- Modify: cloud_run/job_repository.py
- Modify: cloud_run/relay.py
- Modify: cloud_run/routes.py
- Modify: scripts/check.sh
- Modify: tests/python/test_job_repository.py
- Modify: tests/python/test_relay.py
- Modify: tests/python/test_routes.py
- Modify if its allowlist requires it: tests/python/test_repository_contract.py
- Create: docs/superpowers/live-tests/five-session-evidence-template.md

The smoke workflow is a normal ComfyUI canvas:

    EmptyImage(
        width=512,
        height=512,
        batch_size=1,
        color=0x1267A3,
    )
      -> SaveImage(filename_prefix="cloud_run_core_smoke")

It contains two ComfyUI core nodes, no model loader, no custom node, no local input, and no provider-specific node. The expected pixel is RGB 18, 103, 163.

Use this exact logical workflow payload:

    {
      "last_node_id": 2,
      "last_link_id": 1,
      "nodes": [
        {
          "id": 1,
          "type": "EmptyImage",
          "pos": [0, 0],
          "size": [315, 130],
          "flags": {},
          "order": 0,
          "mode": 0,
          "inputs": [],
          "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": [1]}
          ],
          "properties": {"Node name for S&R": "EmptyImage"},
          "widgets_values": [512, 512, 1, 1206179]
        },
        {
          "id": 2,
          "type": "SaveImage",
          "pos": [420, 0],
          "size": [315, 58],
          "flags": {},
          "order": 1,
          "mode": 0,
          "inputs": [
            {"name": "images", "type": "IMAGE", "link": 1}
          ],
          "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": null}
          ],
          "properties": {"Node name for S&R": "SaveImage"},
          "widgets_values": ["cloud_run_core_smoke"]
        }
      ],
      "links": [[1, 1, 0, 2, 0, "IMAGE"]],
      "groups": [],
      "config": {},
      "extra": {"ds": {"scale": 1, "offset": [0, 0]}},
      "version": 0.4
    }

- [ ] **Step 1: Write the failing fixture contract test**

Parse the JSON and assert exact node IDs/types/link, zero model properties, zero
custom nodes, exact size/color/prefix, and logical canonical-workflow SHA-256
`e72b293f4d08e007623ea647b58c1733913e04261fb04eb9a96f7566895e2f30`
after ASCII JSON serialization with sorted keys and compact separators. Also
compute the completed fixture's raw-byte SHA-256 once, hard-code that separate
expected value in the fixture contract test and evidence template, and commit
both together; never label the canonical hash as the file hash. Compile it
through the same
capture/preflight fake path and require two core-resolved rows, transfer_bytes
zero, disk_gb 80, no mapping, and rentable true with an explicit 16 MiB
allowance. Record `capture.prompt_digest` separately: it identifies the exact
executable prompt submitted by one job, not the workflow file.

- [ ] **Step 2: Write the failing validator tests**

Define:

    validate_smoke_output(
        output_path,
        *,
        expected_size=(512, 512),
        expected_rgb=(18, 103, 163),
    )

The validator accepts one regular non-symlink PNG whose decoded dimensions and every RGB pixel match, hashes the file through an open descriptor, and returns one sanitized result. It rejects truncation, JPEG, wrong dimensions, one altered pixel, empty files, symlinks, and files that change during validation.

The direct validator CLI, always through the checked ComfyUI/Pillow interpreter,
is:

    scripts/run_with_comfyui_python.sh \
      scripts/validate_smoke_output.py OUTPUT_PATH

Success prints one line beginning:

    PASS format=PNG dimensions=512x512

and includes only output size and SHA-256, never the private storage root.

`run_with_comfyui_python.sh` reuses the exact interpreter resolution currently
embedded in `scripts/check.sh`: explicit `COMFYUI_PYTHON_COMMAND`, then the
normal Python only if it imports Pillow, then the known ComfyUI standalone-env
interpreters. Refactor `check.sh` to call the same wrapper so the logic cannot
drift. The wrapper fails if Pillow is unavailable and never prints its command
line or private arguments.

- [ ] **Step 3: Run RED**

    PYTHONDONTWRITEBYTECODE=1 scripts/run_with_comfyui_python.sh \
      -m unittest \
      tests.python.test_smoke_output_validation -v

- [ ] **Step 4: Add the exact fixture, validator, and evidence template**

The evidence template has five rows S1, S2, S3, G1, G2 and distinct columns for
raw workflow-file SHA-256, canonical workflow SHA-256, the session-specific
prompt digest, normalized executable-baseline digest/seed policy, frozen
controller/release, quote bounds,
distinct_provider_placement boolean, ready/job/output evidence, human destroy
acknowledgement, terminal state, and full-inventory-zero. A randomized Gold seed
may legitimately change the prompt digest between G1 and G2; each digest must
instead equal that fresh run's captured prompt and durable job record. Do not
include raw private identity columns.

- [ ] **Step 5: Persist output node provenance and write the failing ownership tests**

Add nullable `source_node_id`, `published_device`, and `published_inode` to the
transfer schema and `TransferRecord`. The shared schema is owned by
`cloud_run/repository.py`: use one transactional PRAGMA/ALTER legacy migration
there and advance the tested schema version from 6 to 7. Uploads, previews,
partial downloads, and legacy rows may leave these fields null. Every verified
remote output download must save the validated descriptor's exact node ID plus
the final regular file's `st_dev` and `st_ino` obtained from the same open
descriptor used for final hashing. Preserve all three across resume/reopen,
reject identity changes, and require them on the verified fast path and in
`published_artifact`. Thread the same node ID through every output
`save_transfer` branch. Expose only inert `node_id` in sanitized `outputs`, not
in the generic transfer projection. Test schema v7/legacy migration, node 2/9,
changed node identity, a same-byte file replacement with new device/inode, and
legacy non-output rows.

Define a read-only evidence command:

    scripts/run_with_comfyui_python.sh scripts/verify_session_output.py \
      --database DATA_DIRECTORY/attempts.sqlite3 \
      --output-root COMFYUI_OUTPUT_ROOT \
      --input-root COMFYUI_INPUT_ROOT \
      --session-id SESSION_ID \
      --job-id JOB_ID \
      --expected-prompt-digest PROMPT_DIGEST \
      --expected-manifest-digest MANIFEST_DIGEST \
      --expected-execution-baseline-digest BASELINE_DIGEST \
      --node-id 2 --kind smoke

For Gold add `--node-id 9 --kind gold --source-sha256
PRIVATE_FROZEN_INPUT_SHA256`. The helper must open SQLite explicitly with URI
`mode=ro`; it must not construct normal `JobRepository`, whose initializer may
chmod or migrate. Prove a missing database is not created and an existing
database's bytes, mode, schema, and mtime do not change. It must load the exact
job, require
`job.session_id`, prompt digest, manifest digest, and succeeded state to match;
rebuild `CompiledCapture` from `job.capture_json`, require its exact prompt
digest, recompute the seed-normalized executable baseline, and require the
expected/session/job baseline and randomized-node policy to agree;
select exactly one verified `download` transfer for the requested node; require
every other output download for the job to be verified; resolve the selected
regular non-symlink file beneath the exact output root; rehash it through an
open descriptor and compare the durable relay digest/device/inode/size.

For Gold, parse the exact manifest stored under `job.manifest_digest`, select
exactly one `kind="input"` artifact whose source is the exact
`local-upload:ARTIFACT_ID` and whose SHA-256 equals the frozen private source
hash, then load that exact `LocalArtifactRecord` by artifact ID. Do not require
an upload transfer attached to the inference job: provisioning is stored under
`bootstrap:SESSION_ID` or may already be installed. Rehash the local artifact
as a regular non-symlink file beneath the exact ComfyUI input root and compare
artifact ID, manifest digest, local-record digest, size, and file digest before
passing both private paths internally to `validate_gold_output`. For smoke call
`validate_smoke_output`. Gold passes `expected_size=(3840, 2160)`,
`expected_format="PNG"`, and `require_enlargement=True`, and requires the
returned `source_sha256` to equal the CLI's frozen private source hash. Print
only one sanitized PASS/FAIL result with kind, node ID, dimensions, output size,
output hashes, and `source_match=true`—never the private source hash, a private
path, input name, database content, or raw artifact descriptor.

Tests reject wrong session ownership, job ID, prompt/manifest digest, node ID,
multiple node matches, unverified additional output, legacy output with no node
provenance, missing/ambiguous manifest input, wrong local-upload identity,
source-hash mismatch, symlink/input-or-output path escape, changed
inode/content, a canvas changed after preflight, and any private marker
appearing on stdout/stderr. Cover both a
bootstrap transfer and an already-installed/no-job-upload case. This command,
not a guessed filesystem path or newest-file search, is the sole live evidence
selector.

- [ ] **Step 6: Certify the fixture without paid action**

Use certifying-comfyui-cloud-workflows. Verify the local pinned ComfyUI exposes EmptyImage and SaveImage as core, the canvas compiles normally, and preflight requires no model mapping or input transfer.

Add `tests/fixtures/cloud-run-core-output-smoke.json` to the exact public JSON
allowlist in `scripts/check.sh` and freeze that path in
`test_repository_contract.py`. Extend the existing synthetic Gold-validator
block to run smoke validation and the image-bearing session-evidence cases via
the shared ComfyUI Python wrapper. Those modules must fail, not skip, when
Pillow is unavailable under the selected interpreter; verification records
actual executed test counts with zero skips.

- [ ] **Step 7: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 scripts/run_with_comfyui_python.sh \
      -W error -m unittest \
      tests.python.test_smoke_output_validation \
      tests.python.test_session_output_evidence -v
    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_job_repository tests.python.test_relay \
      tests.python.test_routes \
      tests.python.test_repository_contract -v
    git add tests/fixtures/cloud-run-core-output-smoke.json \
      scripts/validate_smoke_output.py \
      scripts/verify_session_output.py \
      scripts/run_with_comfyui_python.sh scripts/check.sh \
      tests/python/test_smoke_output_validation.py \
      tests/python/test_session_output_evidence.py \
      cloud_run/repository.py cloud_run/job_repository.py \
      cloud_run/relay.py cloud_run/routes.py \
      tests/python/test_job_repository.py tests/python/test_relay.py \
      tests/python/test_routes.py \
      tests/python/test_repository_contract.py \
      docs/superpowers/live-tests/five-session-evidence-template.md
    git commit -m "test: add core Cloud Run smoke workflow"

If test_repository_contract.py did not change, omit it from git add rather than creating noise.

---

### Task 11: Prove the complete repaired flow against fake Vast and fake worker

**Files:**

- Modify: tests/python/test_fake_session_integration.py
- Modify as required by shared fakes: tests/python/test_service.py
- Modify as required by shared fakes: tests/python/test_lifecycle.py
- Modify: tests/js/cloud-run-ui.test.mjs
- Modify: tests/js/session-console.test.mjs

- [ ] **Step 1: Add one successful end-to-end fake session**

Drive capture, explicit preflight, offer review with one create, boundary-authenticated ready, current-canvas job, verified output retrieval, manual destroy review, destroy, and fresh full fake inventory zero. Assert:

- env is the exact two-key object;
- one create, one job, one destroy;
- installed_manifest_digest equals the preflight manifest;
- output artifact is local_verified with the expected source node ID, exact job
  ownership, and exact fresh prompt digest;
- job.capture_json recomputes to the session's reviewed executable baseline;
- terminal rental_outcome is absent;
- both secrets and residual inventory are cleared;
- no local prompt and no automatic second create.

- [ ] **Step 2: Add one ambiguous-create fake sequence**

Return an ambiguous create, then successful complete empty inventories at 0,
15, 30, 60, and 120 seconds, and assert:

- exactly one create;
- unknown and billing warning until the last qualifying read;
- Search/Review/Confirm blocked throughout;
- the saturated counter does not terminalize at 30 or 60, last_empty_at advances
  on every snapshot, and failed plus absent occurs only at 120;
- fresh Search enabled only after old offer and idempotency state are cleared;
- no destroy claim if the human did not authorize one.

Also capture a canvas, preflight/review it, then change one executable input
before Run. Require a static incompatibility error, zero worker job submissions,
and no new CloudJob. Repeat with only the certified Gold KSampler numeric seed
changed and require the normalized baseline to remain equal while the exact
prompt digest changes.

- [ ] **Step 3: Add an ambiguous-create manual-destroy fake sequence**

Race destroy_requested with the original create. Make a late labelled instance appear, require one delete, then a complete empty inventory. Assert no transition back to rental and no duplicate DELETE.

- [ ] **Step 4: Run RED**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_fake_session_integration -v
    node --test tests/js/session-console.test.mjs \
      tests/js/cloud-run-api.test.mjs tests/js/cloud-run-ui.test.mjs

- [ ] **Step 5: Make only integration-level corrections**

If a behavior failure reveals a design/code defect, invoke systematic-debugging and return to the owning TDD task. Do not patch the fake to hide a real failure.

- [ ] **Step 6: Run GREEN and commit**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_fake_session_integration -v
    node --test tests/js/session-console.test.mjs \
      tests/js/cloud-run-api.test.mjs tests/js/cloud-run-ui.test.mjs
    git add tests/python/test_fake_session_integration.py \
      tests/python/test_service.py tests/python/test_lifecycle.py \
      tests/js/cloud-run-ui.test.mjs tests/js/session-console.test.mjs
    git commit -m "test: cover safe create reconciliation end to end"

Omit unchanged files from git add.

---

### Task 12: Reach Controller-ready on one reviewed immutable commit

**Files:**

- Verify all changed controller, frontend, test, script, and documentation files
- Do not modify remote_worker, release metadata, template data, or the installed lock

- [ ] **Step 1: Inspect scope and static hygiene**

    git status --short --branch
    git diff ab37f59..HEAD --stat
    git diff --check
    git diff ab37f59..HEAD -- remote_worker

Expected: no remote_worker diff and no whitespace errors. ab37f59 is the approved-design baseline immediately before this plan and implementation series.

- [ ] **Step 2: Run the complete focused repair suite**

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
      tests.python.test_vast \
      tests.python.test_models \
      tests.python.test_repository \
      tests.python.test_service \
      tests.python.test_lifecycle \
      tests.python.test_session_service \
      tests.python.test_routes \
      tests.python.test_fake_session_integration -v
    PYTHONDONTWRITEBYTECODE=1 scripts/run_with_comfyui_python.sh \
      -W error -m unittest \
      tests.python.test_smoke_output_validation \
      tests.python.test_session_output_evidence \
      tests.python.test_gold_output_validation -v
    node --test tests/js/session-console.test.mjs \
      tests/js/cloud-run-api.test.mjs tests/js/cloud-run-ui.test.mjs

Expected: all pass, no network, no Vast mutation, no ComfyUI restart.

- [ ] **Step 3: Request independent code review before the global gate**

Use superpowers:requesting-code-review. Review specifically:

- atomicity of claim_create_intent;
- no second create path;
- empty versus unavailable inventory;
- 120-second persisted evidence;
- destroy versus PUT race;
- pagination completeness;
- session/job/prompt/node-owned output selection and path/hash safety;
- typed frontend gates;
- secret/provider-data non-disclosure;
- no worker/template/lock changes.

Apply valid findings with TDD and commit them. Re-run the affected focused suites.

- [ ] **Step 4: Commit any final review correction and require a clean tree**

    git status --short

Expected: empty output.

- [ ] **Step 5: Use verification-before-completion and run the final repository gate**

    scripts/check.sh

Required final line:

    [check] all checks passed

If it fails, do not describe Controller-ready. Invoke systematic-debugging, add a failing regression, fix, commit, and run one new final successful gate against the resulting clean commit.

- [ ] **Step 6: Revalidate immutable assets**

Assert the installed lock still contains the exact worker commit, archive SHA, template hash, and protocol from Frozen baseline. Verify git diff contains no remote worker, release, or template mutation.

- [ ] **Step 7: Freeze and publish the controller commit**

    CLOUD_RUN_CAMPAIGN_HEAD=$(git rev-parse HEAD)
    test -n "$CLOUD_RUN_CAMPAIGN_HEAD"
    git push origin fix/vast-template-live-audit

Do not force-push. Keep the existing PR draft. Record the public commit in the sanitized evidence template. This freezes the build for S1 through G2; any later code change resets the live counter.

Only now report Controller-ready. Do not report Smoke-certified, fixed in production, or Gold-validated.

---

### Task 13: Restart the one Desktop backend only after a new human GO

**External authorization gate:** Stop and ask for explicit permission to restart the existing ComfyUI Desktop backend. The committed controller change is the demonstrated cause. This permission is not permission to rent a GPU.

**Expected time after authorization:** approximately 3 to 8 minutes.

- [ ] **Step 1: Verify the existing owner and empty local queue**

    lsof -nP -iTCP:8188 -sTCP:LISTEN
    curl -fsS http://127.0.0.1:8188/queue

Require one listener owned by the existing /Applications/ComfyUI.app installation and no running or pending prompt. Do not start a Python backend or a second listener.

- [ ] **Step 2: Ask the human to restart through Desktop**

The human performs the normal Desktop/Agent Panel restart after giving the GO. Do not kill the process, recreate the application state, or change the lock.

- [ ] **Step 3: Verify the loaded controller and unchanged lock**

Require:

- ComfyUI responds on 127.0.0.1:8188;
- GET /queue remains empty;
- the installed custom_nodes/ComfyUI-Cloud-Run symlink resolves to the exact worktree and frozen campaign HEAD;
- GET /cloud-run/api/settings returns configured true and lifecycle_enabled true;
- the worker_release payload equals every Frozen baseline digest;
- active_sessions contains no unknown or active session;
- the frontend no longer exposes a 1-or-2 create selector;
- a synthetic read-only UI render shows typed outcome controls.

- [ ] **Step 4: Prove full Vast inventory zero read-only**

Use SettingsStore with the exact local data directory and vast.list_instances. Keep the API key inside the process and print only:

    vast_instance_count=0

The call must traverse every v1 page and accept zero only from the strict Task 1
contract: every page/row/count/total validates, the accumulated list is empty,
the coherent total is zero, and the terminal token is null. If authentication,
pagination, validation, 429, or transport fails, inventory is unknown and the
paid gate stays closed. Do not print the settings object or any instance record.

- [ ] **Step 5: Report the gate**

Tell the human either:

    Controller loaded, queue empty, lock exact, full Vast inventory zero.
    Free campaign preparation may begin; no rental is authorized yet.

or the exact failed read-only condition. Never say that the human can Confirm at this stage.

---

### Task 14: Certify both canvases and obtain one exact paid campaign budget

**External authorization gate:** Free capture, preflight, and offer search may be performed in the UI. Stop before every Confirm. The numeric campaign proposal below becomes authorization only if the human explicitly accepts it.

**Recommended authorization proposal (a ceiling/stop rule, not a promise that
5.00 USD will be sufficient):**

- at most five paid attempts under this initial authorization, exactly one
  create per reviewed session; any failed/ambiguous attempt stops and exhausts
  this authorization before another attempt;
- one instance at a time;
- S1-S3: on-demand, one NVIDIA GPU, minimum 8 GiB VRAM, maximum 0.20 USD/hour, finite 30-minute deadline, 16 MiB output allowance, 80 GiB disk;
- G1-G2: minimum setting 30 GiB VRAM and selected displayed VRAM at least 31.5 GiB, maximum 0.75 USD/hour, finite 90-minute deadline, 1 GiB output allowance, preflight-derived disk;
- quote-projected network cap 0.01 USD per smoke and 0.71 USD per Gold
  (`3 * 0.01 + 2 * 0.71 = 1.45 USD`);
- maximum total campaign spend 5.00 USD including active compute/storage and
  quoted bandwidth, with 1.00 USD held as an explicit reserve for worker image,
  container, and other unmodelled traffic;
- every Confirm, Run current canvas, and Destroy remains the human's manual click.

The compute/storage maximum under these proposed hourly/duration limits is:

    3 * (0.20 * 0.5) + 2 * (0.75 * 1.5) = 2.55 USD

For each quote, require finite displayed `inet_down_cost` and `inet_up_cost` and
calculate, in the units advertised by the quote:

    projected_network =
      (transfer_bytes / 1e9) * inet_down_cost
      + (output_allowance_bytes / 1e9) * inet_up_cost

Initialize a private
`cumulative_charged_or_conservative_upper_bound` at zero. After every paid
attempt—successful, rejected after mutation began, failed, ambiguous, or
deadline-cleaned—add the reliable read-only billed amount when safely available;
otherwise add that attempt's reviewed maximum active charge plus projected
network. Never drop failed-attempt cost. Before each Confirm, the gate is:

    cumulative_charged_or_conservative_upper_bound_for_all_prior_attempts
      + current_quote_max_active_charge
      + current_quote_projected_network
      + worst_case_compute_and_network_for_successes_still_needed_after_current
      + 1.00 USD reserve
      <= accepted_total_campaign_ceiling

The nominal 2.45 USD difference after compute/storage is therefore not treated
as unbounded network headroom: 1.00 USD is reserved first and the remaining
1.45 USD is allocated by the per-session network caps above. Those caps make
the future-success term calculable before each Confirm. A missing quote cost
closes the gate. The initial five-success reservation is the 2.55 USD compute
maximum plus all five per-session network caps and the reserve; each actual
quote must fit its reserved sub-cap.
Between sessions, use a read-only actual billing/account check when Vast exposes
one safely; otherwise retain the conservative upper bound. If
the ceiling cannot cover the next quote, stop and ask for a new explicit budget
rather than relaxing it.

- [ ] **Step 1: Ask for the exact budget GO**

Suggested text for the human to approve or edit:

    GO campagne réelle : 5 tentatives payantes maximum sous cette autorisation,
    strictement 1 création par session et 1 instance à la fois ; le premier
    échec arrête et épuise cette autorisation ; smokes <= 0,20 $/h et 30 min ;
    Gold <= 0,75 $/h et 90 min ; plafond total 5,00 $ bande passante et
    stockage compris, réseau projeté <= 0,01 $ par smoke et <= 0,71 $ par Gold,
    dont 1,00 $ de réserve explicite. J'accepte uniquement
    chaque quote présentée sous ces bornes. Chaque Confirm, Run et Destroy
    reste mon clic manuel.

Do not infer approval from earlier enthusiasm for the strategy. Require the exact accepted numbers before the first paid click. If the human changes a number, record the replacement and use it consistently.

If any paid attempt fails or remains ambiguous, record it and its cost/upper
bound in the lifetime attempt ledger and stop. Continuing after diagnosis
requires a new exact GO
that states the additional attempt count and either a replacement total spend
ceiling or an exact amount added to the prior ceiling; the
original five-attempt authorization cannot be silently reused. Counted
successes may be preserved only when controller HEAD, worker/release/archive,
template/lock/protocol, relevant workflow, and private input are unchanged. A
change to controller code or immutable assets resets the entire campaign to
zero; a workflow/input change resets the entire five-success campaign because
the frozen acceptance baseline changed.
The final report always gives total paid attempts as well as five counted
successes.

- [ ] **Step 2: Certify the committed smoke canvas through the normal UI**

The human imports tests/fixtures/cloud-run-core-output-smoke.json into ComfyUI. Capture the current canvas through the Cloud Run button and run free preflight with output allowance 16777216.

Require:

- exact committed raw fixture-file SHA-256 and exact logical canonical-workflow
  SHA-256 as two distinct values;
- the preflight capture's exact prompt digest recorded as diagnostic identity,
  plus the frozen executable-baseline digest and an empty randomized-seed set;
- EmptyImage and SaveImage resolved as core;
- zero model or custom-node mappings;
- transfer_bytes zero;
- disk_gb 80;
- rentable true.

Do not run the local queue button.

- [ ] **Step 3: Re-certify the exact Gold workflow read-only**

After the smoke check, load the exact Wallpaper workflow through ComfyUI and capture/preflight with output allowance 1073741824.

Require:

- raw workflow-file SHA-256 and canonical parsed-workflow SHA-256 frozen as two
  distinct values for both G1 and G2;
- the preflight prompt digest recorded as diagnostic identity, plus one frozen
  executable-baseline digest whose randomized-seed set is exactly node 3;
- 32 active nodes and no custom nodes;
- model records:
  - node 31 flux1-fill-dev.safetensors in diffusion_models;
  - node 32 ae.safetensors in vae;
  - node 34 clip_l.safetensors and t5xxl_fp8_e4m3fn.safetensors in text_encoders;
  - node 61 4x_foolhardy_Remacri.pth in upscale_models;
- all dependency rows resolved;
- the private LoadImage artifact resolved without printing its name/path;
- transfer_bytes exactly 29347469703 for the frozen workflow/input;
- disk_gb exactly 89 with the explicit 1 GiB output allowance (the prior
  88 GiB observation belonged to a one-byte allowance);
- rentable true.

If either exact value differs, diagnose the free preflight before S1 rather than silently rebasing the campaign. If either frozen workflow's content or the Gold input content changes after S1, reset the campaign to zero.

- [ ] **Step 4: Initialize the private campaign ledger**

Freeze:

- controller HEAD;
- worker/release/archive/template/protocol;
- smoke raw-file and canonical-workflow digests;
- Gold raw-file and canonical-workflow digests and private input hash;
- smoke/Gold executable-baseline digests and randomized-seed policies;
- each actual Run capture's exact prompt digest only after that capture, never
  as a cross-run workflow identity;
- accepted price, duration, and total limits;
- cumulative charged-or-conservative upper bound for every lifetime paid
  attempt, reserve, successes still needed, and remaining budget.

Keep raw machine_id and host_id only in ephemeral private comparison state. Publish only distinct_provider_placement true or false.

---

## Reusable live-session protocol

Tasks 15 through 19 repeat this protocol. It is written once here, but every checkbox is required for every session.

### Before Confirm

1. ComfyUI responds and /queue is empty.
2. No session derives unknown or active and no terminal session retains an
   instance ID or residual inventory.
3. A successful complete read-only Vast inventory returns exactly zero instances.
4. The correct frozen canvas is open.
5. A fresh capture and fresh rentable preflight have the exact frozen
   raw/canonical workflow identities, executable-baseline digest and seed
   policy, allowance, disk, and dependency counts.
6. The human saves the session's max price and min VRAM settings, clicks Search Vast GPUs, chooses an offer, selects the required 30- or 90-minute duration, and clicks the unpaid Review action.
7. The exact-offer backend revalidation, through its frozen tested
   normalization/filter contract, proves on-demand, one GPU, verified,
   NVIDIA/amd64, compute capability at least 7.5, and CUDA at least 12.9. The
   reviewed public quote proves only the sanitized GPU/VRAM, price, reliability
   at least 0.99, download at least 500 Mbps, disk, transfer estimate,
   bandwidth costs, and duration/budget bounds. Provider identities remain
   private.
8. Inspect machine_id and host_id only through the private reviewed quote and
   compare them with every earlier success. Prefer a new pair and discard a
   duplicate unpaid review while another eligible distinct quote exists. If no
   distinct pair can be found under every accepted price/safety bound, stop and
   ask for explicit authorization to relax placement diversity only; never
   relax it silently or weaken another bound.
9. The reviewed quote exposes one fixed create and no replacement option.
10. The finite deadline starts with the unpaid review, not the Confirm click.
    The quote is unexpired and the displayed hard deadline still has at least
    25 minutes remaining for a smoke or 80 minutes for a Gold. Otherwise
    discard the unpaid review and create a fresh one.
11. Only then say clearly: Tu peux cliquer une fois sur Confirm & rent this GPU.

### After the human clicks Confirm

Observe read-only:

- controller logs without raw provider bodies;
- GET /cloud-run/api/sessions/SESSION_ID;
- full Vast inventory through the safe read-only helper;
- durable session/job records without printing secrets.

Record timestamps for Confirm, contract ID assigned, instance status changes, first authenticated worker health, provisioning start/end, installed manifest, ready, job submit/start/harvest/success, output verification, Destroy click, and full inventory zero.

Send a concrete progress update at least every two minutes during live provisioning, and never leave a tool call without a user update for more than 60 seconds. Each update says elapsed time, current state, last new evidence, and a range rather than a false exact ETA.

Expected ranges, not promises:

- create response or explicit reconciliation: seconds to about 2 minutes;
- lightweight smoke ready: usually 5 to 20 minutes, hard deadline 30;
- Gold ready including roughly 29.35 GB: usually 10 to 60 minutes, hard deadline 90;
- smoke job/output: usually under 5 minutes;
- Gold job/output: workload-dependent, reassess from real progress.

A temporary connection refusal while the image starts can be retryable. A typed rejection, ambiguous create, exited/offline instance, authentication failure, OOM, invalid manifest, failed job, invalid output, ten minutes without any new progress evidence, or unavailable inventory stops the campaign. Invoke systematic-debugging and collect evidence before proposing a fix. Never rent the next GPU to see whether the error repeats.

Any failure after the paid click is recorded as a paid attempt even if Vast
later proves no instance active. It does not count as a success, and no further
attempt is covered until the human gives the new exact attempt/budget GO
described in Task 14.

If `billing_may_continue` is true or the outcome is unknown, capture the minimum
read-only failure evidence immediately. If controller-owned safety cleanup is
not already durably requested, ask the human to use the reviewed manual Destroy
flow without waiting out the session deadline. Monitor cleanup through terminal
absence and full inventory zero before deeper offline diagnosis. If destruction
itself is ambiguous, keep the campaign closed and continue reconciliation;
never claim billing stopped. Automatic or emergency manual cleanup is recorded
but does not count as a successful session.

### Run and output

Require controller-authenticated ready and installed_manifest_digest equal to
the frozen preflight before allowing the human run click. Recheck the finite
deadline immediately before Run and require at least 10 minutes remaining for
a smoke or 30 minutes for Gold. If the margin is smaller, do not run and do not
extend automatically: show the exact remaining time and ask for a new explicit
duration/budget authorization or proceed to manual destruction. An automatic
deadline destroy is only a safety fallback and can never count as a successful
session; acceptance still requires the human's reviewed manual Destroy.

Say:

    La session est authentifiée ready. Tu peux cliquer une fois sur
    Run current canvas on this GPU.

The human clicks. Require one new job to pass
captured/queued/running/harvesting/succeeded and every output artifact to be
local_verified. The backend must compare the fresh Run capture's normalized
executable baseline and seed policy with the reviewed session before making any
worker job call. Freeze that fresh capture's exact prompt digest and require the
durable job to carry it; never require G1 and G2 to share a prompt digest when
randomized seeds differ. Confirm relay SHA-256 equals
each local regular-file SHA-256 and that every artifact belongs to this new job,
not historical output. The Gold canvas can return both SaveImage node 9 and
PreviewImage node 66 artifacts; select the new SaveImage node 9 PNG through the
job-owned evidence helper and verify every additional output rather than
requiring exactly one total artifact.

Smoke validation uses `scripts/verify_session_output.py` with the exact database,
output root, session ID, job ID, fresh prompt digest, manifest digest, node 2,
and kind smoke. Require exit zero, PNG 512 by 512, exact RGB 18/103/163,
nonempty file, and matching durable/descriptor/file hashes.

Gold validation uses the same helper with node 9, kind gold, the private frozen
source SHA-256, expected PNG, expected 3840x2160, and required enlargement. The
helper resolves both private paths internally and invokes
`validate_gold_output.py`; neither path is printed. Require exit zero/PASS, a
new output hash, and a human visual confirmation that the actual outpaint is
coherent.

### Manual destroy and zero inventory

Only after output verification ask:

    L'image est revenue et sa vérification est passée. Clique maintenant
    manuellement sur Destroy GPU, relis l'avertissement, puis confirme Destroy.

The human performs both reviewed destruction clicks. Require:

- session status destroyed;
- rental_outcome absent;
- billing_may_continue false;
- provider token and session HMAC secret cleared;
- instance ID cleared according to the terminal model;
- no residual inventory;
- a new complete Vast inventory read returns zero.

Only then increment the success counter. If DELETE returned success but inventory is unavailable/nonzero, do not count it and do not open the next gate.

---

### Task 15: Run S1, the single live proof of the create env contract

**Paid authorization gate:** Require Task 14's exact GO and all Before Confirm checks. The human performs Confirm.

**Canvas:** tests/fixtures/cloud-run-core-output-smoke.json

**Bounds:** accepted smoke hourly limit, 30 minutes, 8 GiB minimum VRAM, 16 MiB output allowance, 80 GiB disk.

- [ ] **Step 1: Search for the cheapest eligible first provider placement**

Because there is no prior campaign host, any eligible pair is distinct. Prefer the cheapest offer under the accepted cap; do not relax reliability/download/architecture filters.

- [ ] **Step 2: Stop and ask for the first Confirm click**

State the exact GPU, displayed VRAM, dph_total, duration, maximum active charge,
bandwidth projection, remaining campaign budget, the paid-attempt number under
the current authorization, and success target S1 (1 of 5). Do not expose
host/machine IDs.

- [ ] **Step 3: Monitor create through authenticated ready**

This is the live confirmation of the object env contract. If Vast rejects configuration, preserve configuration_rejected and exact sanitized evidence. Do not switch back to a string or create a second instance speculatively.

- [ ] **Step 4: Ask for Run and verify the smoke PNG**

Follow the reusable protocol. Exactly one job and one local_verified output.

- [ ] **Step 5: Ask for Destroy and prove full inventory zero**

Follow the reusable protocol. Record S1 success only after terminal plus zero inventory.

**Checkpoint:** Report 1/5, actual elapsed phases, estimated charge, remaining budget, and expected next-step time. If anything failed, report 0/5 and stop.

---

### Task 16: Run S2 with the identical build and prefer a second placement

**Paid authorization gate:** S1 must be complete and inventory zero. The human performs a new Search, Review, Confirm, Run, and Destroy.

**Canvas and bounds:** identical to S1.

- [ ] **Step 1: Re-run fresh preflight and seek a new machine/host pair**

Prefer a pair distinct from S1. If none is eligible under the accepted bounds,
pause at the unpaid review gate and ask whether duplicate placement is
explicitly accepted. Do not alter the workflow to manufacture a new result;
freshness is proven by the new session/job/artifact record and timestamps.

- [ ] **Step 2: Execute the complete reusable protocol**

Require one create, authenticated ready, one new verified PNG, human Destroy, terminal session, and full inventory zero.

**Checkpoint:** Report 2/5 only with the complete evidence package. Otherwise stop at 1/5.

---

### Task 17: Run S3, prefer a third placement, and certify the rental boundary

**Paid authorization gate:** S2 must be complete and inventory zero. All actions remain new human clicks.

**Canvas and bounds:** identical to S1/S2.

- [ ] **Step 1: Seek a third machine/host pair**

Prefer a pair distinct from both earlier successes. If none is eligible under
the accepted bounds, use the same explicit diversity-relaxation gate before
Confirm.

- [ ] **Step 2: Execute the complete reusable protocol**

Require one create, authenticated ready, one new verified PNG, human Destroy, terminal session, and full inventory zero.

- [ ] **Step 3: Use verification-before-completion for Smoke-certified**

Audit S1-S3 evidence:

- same controller/worker/template/protocol and smoke digest;
- the actual distinct-provider placement count and each row's true/false value;
- three one-create sessions;
- three authenticated ready states;
- three new local_verified PNG artifacts passing exact pixel validation;
- three human destroy acknowledgements;
- zero inventory after each.

Only then report Smoke-certified and 3/5. This does not yet validate FLUX.

---

### Task 18: Run G1 with the exact Wallpaper Outpaint FLUX Fill 4K canvas

**Paid authorization gate:** Smoke-certified, inventory zero, remaining budget sufficient, and all Before Confirm checks. The human performs every action.

**Canvas:**

    /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/Wallpaper Outpaint FLUX Fill 4K.json

**Bounds:** accepted Gold hourly limit, 90 minutes, min setting 30 GiB and displayed VRAM at least 31.5 GiB, 1 GiB output allowance, exact preflight disk, approximately 29.35 GB dependency transfer.

- [ ] **Step 1: Load the exact frozen Gold workflow and rerun preflight**

Require the same raw-file, canonical-workflow, private-input, and manifest
digests from Task 14 and all five model records. Record this capture's own
prompt digest separately. Prefer a 31.8 GiB RTX 5090 or a suitable 48 GiB class
for speed, but accept only an offer satisfying every reviewed product filter
and budget. Never accept 24 GiB merely because it is cheaper.

- [ ] **Step 2: Prefer a fourth distinct machine/host pair and ask for Confirm**

Use the explicit diversity-relaxation gate if needed. State the lifetime paid
attempt number, success target G1 (4 of 5), and the worst-case remaining-budget
calculation.

- [ ] **Step 3: Monitor provisioning and model verification**

Require every artifact installed at its manifest path, no missing class/model, installed manifest exact, boundary authenticated, and ready before Run. If provider progress stops, collect logs; do not wait blindly to the hard deadline and do not launch another GPU.

- [ ] **Step 4: Ask for Run and validate the real image**

Require a new SaveImage node 9 PNG at 3840 by 2160, validator PASS, matching relay/local hashes, every additional output verified, and explicit human visual acceptance.

- [ ] **Step 5: Ask for Destroy and prove inventory zero**

Record G1 only after terminal destruction and a complete zero inventory.

**Checkpoint:** Report 4/5, actual transfer/ready/run/destroy timings, sanitized image verification, estimated charge, and remaining budget. One real success is still not final Gold.

---

### Task 19: Run G2, prefer a fifth placement, and certify final Gold

**Paid authorization gate:** G1 must be complete, inventory zero, unchanged build/workflow/input, and remaining budget sufficient. The human performs every action.

**Canvas and bounds:** identical to G1.

- [ ] **Step 1: Re-run fresh preflight and prefer a fifth machine/host pair**

Search for a pair distinct from all earlier successes. If none qualifies under
the accepted bounds, pause and require explicit diversity relaxation before
Confirm.

- [ ] **Step 2: Execute the complete reusable protocol**

Require one create, exact model provisioning, authenticated ready, one new job, a new SaveImage node 9 PNG at 3840 by 2160 with validator PASS, every additional output verified, human visual acceptance, manual Destroy, terminal session, and full inventory zero.

- [ ] **Step 3: Perform final verification-before-completion**

Audit all five rows:

- one frozen controller HEAD;
- unchanged worker release/archive/template/lock/protocol;
- three identical smoke raw-file/canonical-workflow digests and two identical
  Gold raw-file/canonical-workflow/input digests; the respective normalized
  executable baselines and seed policies match every job, while each job's
  prompt digest matches its own fresh capture without requiring randomized
  cross-run equality;
- five counted successful one-create sessions, never concurrent and never
  automatic, plus the separately reported total lifetime paid-attempt count;
- the actual distinct machine/host count and every sanitized
  distinct_provider_placement true/false result;
- five authenticated ready states;
- five successful new jobs and verified harvested outputs;
- three exact smoke PNG passes;
- two real Gold PNG structural passes plus human visual acceptance;
- five manual Destroy confirmations;
- session terminal and full Vast inventory zero after every run and once again at the end;
- accepted total budget not exceeded;
- no hidden/raw provider error and no secret disclosure.

If any item is missing, report the exact incomplete milestone and do not use Gold-validated.

- [ ] **Step 4: Record one sanitized PR #3 evidence comment**

Include:

- final controller commit and unchanged public release/template digests;
- the object/string documentation contradiction and that S1 was the live object-contract proof;
- 3/3 smoke and 2/2 Gold results;
- GPU classes, advertised public metrics, phase durations, transfer bytes, output sizes/hashes, and approximate charges;
- actual distinct-provider count and each sanitized placement result without
  raw identities;
- exactly one create for each successful session and the full paid-attempt
  count, plus exactly one successful Run and manual Destroy per counted row;
- final full inventory zero;
- no local prompt execution.

Do not include API keys, tokens, signed URLs, public IPs, raw provider responses, raw machine/host/instance IDs, private input names/paths, or the real image itself without separate publication approval. Keep the PR draft unless the user separately asks to merge.

- [ ] **Step 5: Report the actual final outcome**

Only now say:

    Gold-validated: 3 core smokes and 2 real FLUX 4K runs passed; every output
    was verified, every counted GPU was manually destroyed, and final complete
    Vast inventory is zero.

Append the actual distinct-placement count, total paid attempts, actual elapsed
time, and cost estimate. If the final inventory read is unavailable, continue
safe read-only verification and show the emergency Vast console action; do not
declare completion.

## Expected total timing

These are planning ranges, not promises:

- Tasks 1-11 implementation and focused verification: roughly 4 to 8 focused hours;
- independent review plus final repository gate: roughly 30 to 90 minutes;
- authorized Desktop restart/read-only gate: roughly 3 to 8 minutes;
- three smoke sessions: roughly 30 to 90 minutes total if hosts boot normally;
- two Gold sessions: roughly 45 minutes to 3 hours total depending on image pull, model transfer, inference, and provider performance.

At every live stage, report measured elapsed time and revise the remaining range from evidence. Cost clocks matter more than these estimates; stop rules and the human's finite deadline always win.

## Final scope statement

This plan completes the controller/frontend failure contract and validates the existing immutable worker/template path. It deliberately does not create a second template, publish a new worker, merge the PR, automate a paid click, or build a general automatic GPU recommender. The live preflight still proves exact model paths and disk, and the campaign deliberately sets the tested VRAM/performance class. A broader per-workflow GPU recommender remains separate product work after Gold.

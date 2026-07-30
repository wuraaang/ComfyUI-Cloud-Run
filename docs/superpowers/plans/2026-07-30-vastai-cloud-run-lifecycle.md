# Vast.ai Cloud Run Lifecycle Implementation Plan

> **For Codex:** Use `superpowers:executing-plans` to execute this plan task by
> task. Use strict TDD and `superpowers:verification-before-completion` before
> claiming completion.

**Goal:** Extend the existing preview-only `ComfyUI-Cloud-Run` package into a
safe Vast.ai lifecycle controller, while preserving the normal local ComfyUI
run button and placing a separate `Cloud Run` launcher immediately beside it.

**Architecture:** Keep all code inside the standalone custom-node package.
ComfyUI's frontend only talks to same-origin backend routes. The backend owns
the Vast credential, offer validation, durable attempt state, idempotency,
provider mutations, recovery, and cleanup. Use stdlib SQLite in the package
data directory for transactional attempt state; use ComfyUI's existing
`aiohttp` runtime for HTTP. No workflow transfer, model synchronization, custom
node resolution, other cloud provider, or Registry publication is included.

**Pinned host:** ComfyUI Core `0.29.0`, frontend `1.47.10`, Python `3.13.12`.

## Repository and source facts

- Implementation repository:
  `/Users/wuraaang/ComfyUI-Cloud-Run`
- Clean V0 commit:
  `a9a103e4da35d4bea13fef0417b3cc594eb6b045`
- Behavioral reference:
  `/Users/wuraaang/lora-dataset-studio`
- Reference commit:
  `de697caf9d607a29c72cebdc2794eecd6b147606`
- The reference worktree has unrelated user changes. Read committed source with
  `git show de697caf9d607a29c72cebdc2794eecd6b147606:<path>` and never edit it.
- LoRA Dataset Studio is PolyForm Noncommercial 1.0.0 while this package is MIT.
  Reimplement contracts and policies narrowly; do not copy source text. Record
  the source commit and behavioral references in `NOTICE`.
- The V0 package is not currently present under the active ComfyUI
  `custom_nodes/`; installation is a final local-integration step.
- `AGENTS.md` and `docs/project-state.md` still say “preview only” and prohibit
  provider mutations. The owner must explicitly authorize the lifecycle slice;
  then those repository instructions must be updated before production work.

## Reusable behavioral map

Use these committed LoRA Dataset Studio regions as behavioral references:

- `backend/app/services/vast_client.py:36`: offer search contract and quality
  filters.
- `backend/app/services/vast_client.py:95`: create contract.
- `backend/app/services/vast_client.py:122`: instance normalization.
- `backend/app/services/vast_client.py:141`: list/get/destroy and URL derivation.
- `backend/app/services/cloud_training.py:1061`: retryable failure allowlist.
- `backend/app/services/cloud_training.py:1296`: expiring host/IP blacklist.
- `backend/app/services/cloud_training.py:1370`: blacklist and bait-price filter.
- `backend/app/services/cloud_training.py:1406`: reliability-aware selection.
- `backend/app/services/cloud_training.py:1471`: leak-safe provisioning.
- `backend/app/services/cloud_training.py:1738`: truthful forced stop.
- `backend/app/services/cloud_training.py:1954`: boot reconciliation.
- `backend/app/services/cloud_training.py:2255`: durable boot watchdog.
- `backend/app/services/cloud_training.py:2700`: destruction-gated single retry.

Do not bring over Flask, SQLAlchemy, datasets, training, checkpoints, AI Toolkit,
or its template/image configuration.

## Task 0: Authorize the paid lifecycle contract

**Files:**

- Modify: `AGENTS.md`
- Modify: `docs/project-state.md`
- Modify: `README.md`
- Modify: `tests/python/test_no_mutation_surface.py`

1. Obtain explicit owner authorization for provider mutation routes and live
   lifecycle work.
2. Replace the stale preview-only prohibition with the approved lifecycle
   boundaries: no rental before confirmation, official ComfyUI template only,
   fake/offline certification first, and no real rental without a separate
   human GO.
3. Change the old “no mutations may exist” tests into an allowlist test for the
   exact Vast endpoints and methods needed by the lifecycle.
4. Run the targeted test and observe it fail before changing production code:

   ```sh
   python3 -m unittest tests.python.test_no_mutation_surface -v
   ```

## Task 1: Register one command and place the launcher beside local Run

**Files:**

- Modify: `web/js/cloud-run.js`
- Modify: `tests/js/fake-dom.mjs`
- Modify: `tests/js/cloud-run-ui.test.mjs`

1. Add failing tests for:
   - one stable command, `vast-cloud-run.open`;
   - the command and launcher opening the same existing dialog;
   - the launcher being adjacent to the local queue button;
   - no duplicate launcher after repeated hooks or DOM replacement;
   - an Extensions-menu fallback when the action bar cannot be found;
   - keyboard name, tooltip, focus, narrow viewport, and light/dark themes.
2. Register `commands` and `menuCommands` through `app.registerExtension`.
3. Prefer the installed frontend's extension action-bar API where it satisfies
   the required adjacency. Frontend `1.47.10` exposes `actionBarButtons`, but
   renders that public slot before the complete `ComfyActionbar`.
4. If exact placement to the right of local Run is still required, use a tiny,
   idempotent adapter anchored on the stable local
   `[data-testid="queue-button"]`. Observe only the required root, insert once,
   stop observing after success, and restart only if the action bar is replaced.
5. Never intercept `queuePrompt`, replace the local button, patch Vue, or edit
   frontend-package files.
6. Remove fixed-position launcher CSS after adjacency works.
7. Run:

   ```sh
   node --test tests/js/cloud-run-ui.test.mjs
   ```

## Task 2: Add durable attempt models and transactional persistence

**Files:**

- Create: `cloud_run/models.py`
- Create: `cloud_run/repository.py`
- Create: `tests/python/test_models.py`
- Create: `tests/python/test_repository.py`

1. Write failing transition tests for:
   `idle`, `searching`, `offer_selected`, `confirming`, `creating`, `starting`,
   `cancel_requested`, `destroying`, `retrying`, `ready`, `cancelled`, and
   `failed`.
2. Define a browser-safe attempt representation that never includes credentials
   or provider auth tokens.
3. Store attempts, quote snapshots, idempotency keys, unique Vast labels,
   instance IDs, host identity, retry count, cancellation intent, timestamps,
   and last sanitized error in stdlib SQLite under the private package data
   directory.
4. Enforce unique idempotency keys and one paid instance slot per attempt with
   database constraints and transactions.
5. Add crash/reopen and concurrent-writer tests.
6. Run:

   ```sh
   python3 -m unittest tests.python.test_models tests.python.test_repository -v
   ```

## Task 3: Expand the thin Vast client without leaking secrets

**Files:**

- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/constants.py`
- Modify: `tests/python/test_vast.py`

1. Add failing fake-session tests for search, create, list, get, destroy,
   timeouts, rate limits, partial bodies, 404 idempotence, and sanitization.
2. Keep the credential as an explicit backend-only input or provider; never put
   it in an exception, return value, label, URL, or log.
3. Implement normalized contracts for:
   `search_offers`, `create_instance`, `list_instances`, `get_instance`,
   `destroy_instance`, and `derive_base_url`.
4. Hard-code an allowlist containing only official ComfyUI template
   `57808457573e32120301649763d8e019`.
5. Use HTTPS, bounded connect/read/total timeouts, and normalized error classes.
6. Run:

   ```sh
   python3 -m unittest tests.python.test_vast -v
   ```

## Task 4: Reimplement offer policy independently

**Files:**

- Create: `cloud_run/offers.py`
- Create: `tests/python/test_offers.py`
- Modify: `cloud_run/vast.py`

1. Add failing table-driven tests for VRAM, price cap, on-demand, rentable,
   verified, one-GPU, reliability, network, and disk constraints.
2. Test identity matching by `machine_id`, `host_id`, and `public_ipaddr`.
3. Implement a private expiring blacklist in the package data directory.
4. Test and implement bait-price exclusion per GPU class and
   reliability-first choice within a small price window.
5. Guarantee deterministic behavior if optional Vast quality fields are absent.
6. Run:

   ```sh
   python3 -m unittest tests.python.test_offers -v
   ```

## Task 5: Implement quote revalidation, confirmation, and idempotent creation

**Files:**

- Create: `cloud_run/service.py`
- Modify: `cloud_run/routes.py`
- Create: `tests/python/test_service.py`
- Modify: `tests/python/test_routes.py`

1. Add failing tests that no create call happens during open, search, selection,
   or quote preview.
2. Persist a quote containing GPU, VRAM, price, offer ID, configured cap, and
   expiry.
3. At confirmation, re-search and require the same offer to remain eligible at
   a price no higher than the confirmed price.
4. Persist the attempt, idempotency key, and unique provider label before the
   paid call.
5. On an ambiguous create timeout, reconcile by the unique label; never issue a
   second create while the first outcome is unknown.
6. A duplicate request with the same idempotency key returns the same attempt.
7. Add only allowlisted, validated same-origin routes under `/cloud-run/api/`.
8. Run:

   ```sh
   python3 -m unittest tests.python.test_service tests.python.test_routes -v
   ```

## Task 6: Implement cancellation, readiness, and explicit destruction

**Files:**

- Create: `cloud_run/lifecycle.py`
- Modify: `cloud_run/service.py`
- Modify: `cloud_run/routes.py`
- Create: `tests/python/test_lifecycle.py`

1. Use a fake Vast client and fake clock for all tests.
2. Test cancellation:
   - before create: zero rentals;
   - while create is in flight: persist `cancel_requested`, discover the
     instance by label, then destroy it;
   - while starting: destroy and verify absence;
   - destroy failure: expose the instance ID and emergency action, never claim
     success.
3. Poll with bounded intervals and total boot deadline.
4. Derive and validate only expected Vast public endpoints before exposing
   **Open ComfyUI**.
5. Separate provider stop from irreversible destroy if Vast's current API
   supports both; otherwise document that this package's billing-safe terminal
   action is Destroy.
6. Verify inventory no longer contains the instance before declaring
   `cancelled` or `destroyed`.
7. Run:

   ```sh
   python3 -m unittest tests.python.test_lifecycle -v
   ```

## Task 7: Add boot recovery and exactly one safe replacement

**Files:**

- Modify: `cloud_run/lifecycle.py`
- Modify: `cloud_run/service.py`
- Modify: `__init__.py`
- Modify: `tests/python/test_lifecycle.py`

1. Add failing restart tests for every nonterminal state.
2. On backend start, list only package-managed labels and reconcile each against
   durable attempts. Never create a new instance merely because the backend
   restarted.
3. Classify only explicit transient boot/transport failures as replaceable.
   Never retry auth, quota, budget, validation, or configuration errors.
4. Before the one allowed replacement:
   - destroy the failed pod;
   - verify it is absent from inventory;
   - blacklist its host;
   - persist `retrying`;
   - then select a fresh offer and create once.
5. If destruction cannot be verified, keep the residual instance visible and
   create no replacement.
6. Run:

   ```sh
   python3 -m unittest tests.python.test_lifecycle -v
   ```

## Task 8: Turn the preview modal into a server-driven lifecycle UI

**Files:**

- Modify: `web/js/cloud-run.js`
- Modify: `tests/js/fake-dom.mjs`
- Modify: `tests/js/cloud-run-ui.test.mjs`

1. Add failing UI tests for all server states and mutable-button lockout.
2. Show a paid-rental confirmation with GPU, VRAM, price, offer ID, cap, and the
   official template before calling create.
3. Generate one idempotency key per confirmed attempt and reuse it across
   request retries.
4. Poll attempt state without persisting secrets or provider auth in browser
   storage.
5. Add **Cancel**, **Open ComfyUI**, and **Destroy** with explicit cost wording.
6. Keep residual instance IDs and emergency instructions visible on failure.
7. Run:

   ```sh
   node --test tests/js/cloud-run-ui.test.mjs
   ```

## Task 9: Harden the package and certify offline

**Files:**

- Modify: `scripts/check.sh`
- Modify: `README.md`
- Create: `NOTICE`
- Modify: `docs/project-state.md`
- Modify: `pyproject.toml` only if metadata needs attribution updates

1. Update the gate from “no mutations exist” to “only explicitly allowlisted
   Vast mutations exist.”
2. Add secret scanning, route allowlisting, state-transition checks, and a fake
   full-lifecycle integration test.
3. Document cost, secret storage, cancellation semantics, recovery, emergency
   destruction, uninstall behavior, calls made, and the fact that uninstalling
   never deletes a remote instance.
4. Record behavioral attribution and exact LoRA Dataset Studio commit in
   `NOTICE`, without incorporating PolyForm-licensed source.
5. Run the complete deterministic gate:

   ```sh
   scripts/check.sh
   ```

## Task 10: Install and perform local ComfyUI integration checks

**Files:**

- No core ComfyUI files.
- Install the package under the active ComfyUI `custom_nodes/` only after its
  repository gate passes.

1. Install by symlink or normal custom-node installation without replacing any
   existing custom node.
2. Restart ComfyUI and verify package load logs contain no credential.
3. Verify dark/light, keyboard, narrow width, exact adjacency, local Run
   unchanged, duplicate-hook behavior, and backend-unavailable fallback.
4. Repeat the full fake/offline lifecycle through the real browser/backend.
5. Do not publish to Registry.

## Task 11: Human-gated real Vast certification

This task is deliberately not autonomous.

1. Stop and request explicit human GO immediately before the first real rental.
2. Use a low hourly cap and one instance.
3. Verify ready URL, Cancel, and Destroy.
4. Confirm through fresh Vast inventory that no billed instance remains.
5. Record only sanitized results; never capture the credential.

## Completion criteria

- Local **Run/Exécuter** behaves exactly as before.
- **Cloud Run** is adjacent, unique, accessible, and opens the existing dialog.
- No provider mutation happens before an explicit paid confirmation.
- Duplicate confirmations create at most one managed instance.
- Cancellation and restart cannot orphan a silently billed instance.
- A replacement is created at most once and only after verified destruction.
- Full fake/offline tests and the repository gate pass.
- Real Vast certification remains pending unless the owner gives a separate GO.

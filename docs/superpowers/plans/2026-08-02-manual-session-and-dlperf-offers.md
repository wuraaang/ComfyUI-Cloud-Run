# Manual Session Duration and DLPerf Offer Ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user rent without choosing an automatic duration, make Search use the price and VRAM shown in the panel, and put stronger eligible Vast GPUs first using DLPerf.

**Architecture:** Keep all provider and lifecycle boundaries in place. Reuse the controller's existing `deadline.mode = "none"` contract, perform the existing settings update immediately before offer search, and carry Vast's optional `dlperf` through offer normalization into deterministic ordering and inert UI text. This is a local controller/frontend change; the immutable worker, Vast template, and release lock remain untouched.

**Tech Stack:** JavaScript ES modules and Node test runner, Python 3.13 `unittest`, ComfyUI extension frontend, Vast REST offer normalization.

**Approved design:** `docs/superpowers/specs/2026-08-02-manual-session-and-dlperf-offers-design.md`

## Global constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`.
- Do not rent, confirm, create, destroy, or otherwise mutate a Vast GPU instance.
- Do not publish or change a worker release, Vast template, or local release lock.
- Preserve price, VRAM, one-GPU, on-demand, rentable, verified, reliability, network, disk, architecture, CUDA, and compute-capability gates.
- Preserve the one-create review limit, idempotency, reconciliation, explicit paid confirmation, and verified destruction behavior.
- Use focused test commands. The unrelated stale exact Caddy assertion in the full baseline is not part of this change.
- Do not weaken a regression to obtain green output.

---

### Task 1: Make Search persist and use the visible settings

**Files:**

- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Contract:** Clicking Search after a rentable preflight sends a successful settings `PUT` containing the visible maximum price and whole-number minimum VRAM before it sends the offers `POST`. A validation or save failure sends no offer search.

- [ ] **Step 1: Add a focused failing UI regression**

Add a test that opens the panel, runs preflight, changes `cloud-run-max-price` and `cloud-run-min-vram`, clicks Search, and asserts:

```js
assert.deepEqual(
  calls.map(([endpoint, options]) => [endpoint, options.method ?? "GET"]),
  [
    ["/cloud-run/api/settings", "GET"],
    ["/cloud-run/api/captures", "POST"],
    ["/cloud-run/api/preflights", "POST"],
    ["/cloud-run/api/settings", "PUT"],
    ["/cloud-run/api/offers", "POST"],
  ],
);
assert.deepEqual(JSON.parse(settingsPut[1].body), {
  max_price_per_hour: 1.25,
  min_vram_gb: 48,
});
```

Also cover a rejected settings `PUT` and assert that the offers endpoint is never called.

- [ ] **Step 2: Run the focused test and observe RED**

Run:

```sh
node --test --test-name-pattern="Search persists visible settings" \
  tests/js/cloud-run-ui.test.mjs
```

Expected: `FAIL` because Search currently calls the offers endpoint directly.

- [ ] **Step 3: Share the existing validation/save path with Search**

In `mountCloudRun`, extract small local helpers that:

- parse and validate `priceInput` and `vramInput` with the existing bounds;
- build the settings payload and include a non-empty API key;
- apply a successful settings response to `configured`, `sessionConsole`, and the cleared API-key input.

Use them from both Save Settings and Search. In Search, persist the settings first, then call `cloudApi.searchOffers`. On validation or persistence failure, set a specific settings error and return without searching.

- [ ] **Step 4: Re-run the focused test and observe GREEN**

Run the Step 2 command again. Expected: the save-before-search regression passes.

---

### Task 2: Prefer and display provider DLPerf without weakening filters

**Files:**

- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_offers.py`
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `cloud_run/vast.py`
- Modify: `cloud_run/offers.py`
- Modify: `web/js/cloud-run.js`

**Contract:** Valid non-negative finite `dlperf` values survive normalization. Offers with valid DLPerf sort before missing values and higher DLPerf sorts first; existing connection/reliability/disk/price/id rules break ties. Missing or malformed DLPerf does not reject an otherwise eligible offer. Provider queries ask for DLPerf-descending candidates. Cards explain the independent DLPerf and network metrics.

- [ ] **Step 1: Add failing normalization and ranking regressions**

In `test_vast.py`, add valid, missing, negative, boolean, `NaN`, and infinite DLPerf cases. Expect valid values to normalize and invalid values to become `None`. Update the expected query orders to start with:

```python
["dlperf", "desc"]
```

In `test_offers.py`, extend `normalized_offer` with `dlperf=None` and add assertions that:

- DLPerf 80 beats DLPerf 40 even when the latter is cheaper;
- valid DLPerf 0 beats missing DLPerf;
- equal DLPerf retains the existing quality ordering;
- missing/invalid values remain deterministic and do not crash.

- [ ] **Step 2: Add a failing inert UI rendering regression**

Add `dlperf: 72.5` to an offer-card fixture and expect `DLPerf 72.5` in text. Extend the missing-metrics case to expect `DLPerf unavailable` and no `NaN`/`Infinity` leakage.

- [ ] **Step 3: Run the focused regressions and observe RED**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_offers.RankingPolicyTests \
  tests.python.test_vast.VastRequestTests \
  tests.python.test_vast.VastNormalizationAndErrorTests \
  -v

node --test --test-name-pattern="offer rendering" \
  tests/js/cloud-run-ui.test.mjs
```

Expected: failures show missing DLPerf normalization, ordering, provider order, and rendering.

- [ ] **Step 4: Implement optional DLPerf normalization and ordering**

In `cloud_run/vast.py`:

- parse `raw["dlperf"]` with the existing finite-number boundary;
- emit the number only when non-negative, otherwise `None`;
- use `offer_quality_key` rather than price-only sorting before truncation;
- prepend `["dlperf", "desc"]` to target and fallback provider orders.

In `cloud_run/offers.py`, prepend an optional-DLPerf key to `offer_quality_key`: present values first, then descending value, followed by the complete existing tuple. Treat booleans, negative numbers, non-numbers, `NaN`, and infinities as missing.

- [ ] **Step 5: Render DLPerf as provider performance information**

In `renderOffers`, use the existing finite non-negative display boundary and add either `DLPerf <number>` or `DLPerf unavailable` before the separate download metric. Keep all text in `textContent`.

- [ ] **Step 6: Re-run the focused regressions and observe GREEN**

Run the commands from Step 3 again. Expected: all focused Python and Node regressions pass.

---

### Task 3: Make manual destruction the default duration contract

**Files:**

- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Contract:** The first/default option is `No automatic limit — manual destruction`. It creates a review with `{mode: "none", duration_seconds: null}`. Listed finite values still create finite deadlines. Forged values create no paid review.

- [ ] **Step 1: Replace the bounded-default regression with explicit manual and finite cases**

Update the paid-review test to expect duration values:

```js
["none", "1800", "3600", "5400", "7200"]
```

Expect the default value `none`, the visible label `No automatic limit — manual destruction`, and this request body:

```js
deadline: { mode: "none", duration_seconds: null }
```

Return a matching no-limit quote fixture and assert that the review console says `no automatic limit` and billing continues until verified destruction. Add or retain a second assertion proving a listed value such as `5400` still maps to finite mode. Update the forged-value message to allow manual destruction or a listed duration.

- [ ] **Step 2: Run the duration regressions and observe RED**

Run:

```sh
node --test --test-name-pattern="paid review|session duration" \
  tests/js/cloud-run-ui.test.mjs
```

Expected: the old selector defaults to 7200 and always sends finite mode.

- [ ] **Step 3: Implement the exact selector and request mapping**

In `web/js/cloud-run.js`:

- add `"none" -> null` to `SESSION_DURATION_SECONDS`;
- add the first option with the approved label and default the select to `none`;
- reject only `undefined`, preserving `null` as the manual choice;
- send `mode: "none"` with `duration_seconds: null` for manual choice, otherwise the existing finite contract;
- replace bounded/short-lived review status copy with neutral paid-review wording that explicitly names manual destruction.

Do not change the backend or remote worker; both already implement the no-deadline contract.

- [ ] **Step 4: Re-run the duration regressions and observe GREEN**

Run the Step 2 command again. Expected: manual default, optional finite duration, and forged-value rejection all pass.

---

### Task 4: Verify the controller slice and load it for the real test

**Files:**

- Inspect all modified files.
- No worker/template/release file changes are expected.

- [ ] **Step 1: Run the relevant combined suites**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_offers \
  tests.python.test_vast \
  -v

node --test tests/js/cloud-run-ui.test.mjs tests/js/cloud-run-api.test.mjs
```

Expected: all relevant Python and JavaScript tests pass.

- [ ] **Step 2: Review source scope and syntax**

Run:

```sh
git diff --check
python3 -m py_compile cloud_run/offers.py cloud_run/vast.py
node --check web/js/cloud-run.js
git status --short
git diff --stat
git diff -- cloud_run/offers.py cloud_run/vast.py web/js/cloud-run.js \
  tests/python/test_offers.py tests/python/test_vast.py \
  tests/js/cloud-run-ui.test.mjs
```

Expected: only the plan, controller/frontend source, and focused regressions changed; no worker, template, lock, or provider state changed.

- [ ] **Step 3: Commit the verified implementation**

Run:

```sh
git add docs/superpowers/plans/2026-08-02-manual-session-and-dlperf-offers.md \
  cloud_run/offers.py cloud_run/vast.py web/js/cloud-run.js \
  tests/python/test_offers.py tests/python/test_vast.py \
  tests/js/cloud-run-ui.test.mjs
git diff --cached --check
git commit -m "feat: prefer powerful GPUs and manual sessions"
```

- [ ] **Step 4: Restart only the local ComfyUI controller**

Before restart, confirm local queue counts are `0 running / 0 pending` and fresh Vast inventory is zero using the existing read-only controller/provider paths. Restart the ComfyUI Desktop backend, wait for `/cloud-run/api/settings` on its active port, and verify the loaded extension reports the expected controller settings. Do not create a provider instance.

- [ ] **Step 5: Hand off the manual paid test boundary**

Report the active local URL and state clearly:

- Search now saves the displayed price and VRAM before fetching offers.
- DLPerf means GPU deep-learning performance; Mbps remains connection speed.
- `No automatic limit — manual destruction` is the default.
- With no automatic limit, billing stops only after the user presses Destroy and inventory returns to zero.
- No GPU was rented during implementation; the user may now perform the single real workflow/GPU test.

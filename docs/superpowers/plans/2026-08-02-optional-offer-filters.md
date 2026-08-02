# Optional Offer Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make price and VRAM independent optional filters so values such as `16`, `24`, `0.46`, and `0,46` search immediately without the other field blocking them.

**Architecture:** Keep the backend settings and Vast query contracts unchanged. The frontend maps blank price/VRAM fields to the backend's existing broad validated sentinels (`100` dollars/hour and `1` GB), renders those sentinels as blank on reload, parses French decimal commas, and routes Enter through the existing Search button. Entered values remain strict directional bounds: price is a ceiling and VRAM is a floor.

**Tech Stack:** JavaScript ES modules, ComfyUI extension DOM, Node.js test runner.

**Approved design:** `docs/superpowers/specs/2026-08-02-optional-offer-filters-design.md`

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`.
- Blank price means no user price filter and maps to `100`; blank VRAM means no user VRAM filter and maps to `1`.
- Entered price remains a maximum and entered VRAM remains a minimum; never silently relax either bound.
- Preserve provider safety filters, DLPerf ordering, preflight, paid review, one-create, manual duration, and verified destruction behavior.
- Do not modify backend, worker, template, release, or release lock files.
- Do not rent or mutate a Vast GPU instance.
- Use focused JavaScript verification; do not run the unrelated known-stale global Caddy assertion.

---

### Task 1: Parse and persist independent optional filters

**Files:**

- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Interfaces:**

- Consumes: `priceInput.value`, `vramInput.value`, existing `cloudApi.updateSettings(payload)`.
- Produces: `visibleSettingsPayload(): {payload: object|null, error: string|null}` with effective numeric bounds accepted by the unchanged settings API.

- [ ] **Step 1: Add failing optional-filter UI regressions**

Add focused tests that complete capture/preflight and assert these settings `PUT` bodies before the offers request:

```js
// Both blank: no user filters.
{
  max_price_per_hour: 100,
  min_vram_gb: 1,
}

// VRAM only.
{
  max_price_per_hour: 100,
  min_vram_gb: 24,
}

// French price only.
{
  max_price_per_hour: 0.46,
  min_vram_gb: 1,
}
```

For the French-price case, also assert:

```js
assert.equal(priceInput.type, "text");
assert.equal(priceInput.getAttribute("inputmode"), "decimal");
```

Open the panel with stored values `100` and `1` in the blank/blank case and assert both visible input values are empty strings.

- [ ] **Step 2: Add failing field-specific validation regressions**

Cover invalid price `0,4.6` with valid VRAM and invalid VRAM `24.5` with valid price. Each case must send neither settings `PUT` nor offers `POST` and must render exactly:

```text
Price must be blank or a number such as 0.46 (0,46 also works).
VRAM must be blank or a whole number such as 16 or 24.
```

- [ ] **Step 3: Run the focused regressions and observe RED**

Run:

```sh
node --test --test-name-pattern="optional offer filters|filter validation" \
  tests/js/cloud-run-ui.test.mjs
```

Expected: failures show blank values mapping to zero, comma price rejection, sentinel values displayed, and the old generic error copy.

- [ ] **Step 4: Implement tolerant optional parsing**

In `web/js/cloud-run.js`, add:

```js
const UNFILTERED_MAX_PRICE_PER_HOUR = 100;
const UNFILTERED_MIN_VRAM_GB = 1;
const INVALID_PRICE_MESSAGE =
  "Price must be blank or a number such as 0.46 (0,46 also works).";
const INVALID_VRAM_MESSAGE =
  "VRAM must be blank or a whole number such as 16 or 24.";
```

Change the price input to `type="text"`, set `inputmode="decimal"`, and label both fields as optional. Give them concise placeholders describing an empty field as unfiltered.

Replace `visibleSettingsPayload` with parsing that:

```js
function visibleSettingsPayload() {
  const rawPrice = String(priceInput.value ?? "").trim();
  let maxPrice = UNFILTERED_MAX_PRICE_PER_HOUR;
  if (rawPrice !== "") {
    if (!/^(?:\d+(?:[.,]\d+)?|[.,]\d+)$/.test(rawPrice)) {
      return { payload: null, error: INVALID_PRICE_MESSAGE };
    }
    maxPrice = Number(rawPrice.replace(",", "."));
    if (!Number.isFinite(maxPrice) || maxPrice < 0.01 || maxPrice > 100) {
      return { payload: null, error: INVALID_PRICE_MESSAGE };
    }
  }

  const rawVram = String(vramInput.value ?? "").trim();
  let minVram = UNFILTERED_MIN_VRAM_GB;
  if (rawVram !== "") {
    minVram = Number(rawVram);
    if (!Number.isInteger(minVram) || minVram < 1 || minVram > 1024) {
      return { payload: null, error: INVALID_VRAM_MESSAGE };
    }
  }

  const payload = {
    max_price_per_hour: maxPrice,
    min_vram_gb: minVram,
  };
  const key = apiKeyInput.value.trim();
  if (key) payload.api_key = key;
  return { payload, error: null };
}
```

Update Save and Search to display the returned field-specific `error`. In `loadSettings`, render maximum price `100` and minimum VRAM `1` as blank values; render all other stored values normally.

- [ ] **Step 5: Re-run the focused regressions and observe GREEN**

Run the Step 3 command again. Expected: all optional-filter and validation regressions pass.

---

### Task 2: Launch Search with Enter

**Files:**

- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Interfaces:**

- Consumes: a `keydown` event from either filter and the existing `searchButton.click()` behavior.
- Produces: one settings `PUT` followed by one offers `POST` when Enter is pressed after a rentable preflight.

- [ ] **Step 1: Add a failing Enter regression**

After a rentable preflight, leave price blank, set VRAM to `24`, dispatch this event on the VRAM input, and assert it is prevented:

```js
const event = {
  type: "keydown",
  key: "Enter",
  defaultPrevented: false,
  preventDefault() {
    this.defaultPrevented = true;
  },
};
await vramInput.dispatchEvent(event);
assert.equal(event.defaultPrevented, true);
```

Assert exactly one settings `PUT` with `{max_price_per_hour: 100, min_vram_gb: 24}` precedes exactly one offers `POST`. Repeat the key contract for the price input without creating duplicate requests from one event.

- [ ] **Step 2: Run the focused Enter regression and observe RED**

Run:

```sh
node --test --test-name-pattern="Enter searches optional filters" \
  tests/js/cloud-run-ui.test.mjs
```

Expected: failure because the filter inputs have no keydown search listener.

- [ ] **Step 3: Route Enter through the existing Search button**

Add one shared local listener after the Search click handler is registered:

```js
async function searchOnEnter(event) {
  if (event.key !== "Enter") return;
  event.preventDefault();
  await searchButton.click();
}
priceInput.addEventListener("keydown", searchOnEnter);
vramInput.addEventListener("keydown", searchOnEnter);
```

Do not duplicate save/search logic and do not bypass the preflight-disabled state.

- [ ] **Step 4: Re-run the focused Enter regression and observe GREEN**

Run the Step 2 command again. Expected: the Enter regression passes with one request sequence.

---

### Task 3: Verify, commit, and load the frontend correction

**Files:**

- Inspect: `web/js/cloud-run.js`
- Inspect: `tests/js/cloud-run-ui.test.mjs`
- Inspect: `docs/superpowers/plans/2026-08-02-optional-offer-filters.md`

- [ ] **Step 1: Run the relevant JavaScript suites**

Run:

```sh
node --test tests/js/cloud-run-ui.test.mjs tests/js/cloud-run-api.test.mjs
node --check web/js/cloud-run.js
git diff --check
```

Expected: every relevant JavaScript test passes, syntax exits zero, and the diff contains no whitespace errors.

- [ ] **Step 2: Review exact source scope**

Run:

```sh
git status --short
git diff --stat
git diff -- web/js/cloud-run.js tests/js/cloud-run-ui.test.mjs \
  docs/superpowers/plans/2026-08-02-optional-offer-filters.md
```

Expected: only the frontend, its focused regression, and this plan changed. No backend, worker, template, release, or lock file changed.

- [ ] **Step 3: Commit the verified correction**

Run:

```sh
git add web/js/cloud-run.js tests/js/cloud-run-ui.test.mjs \
  docs/superpowers/plans/2026-08-02-optional-offer-filters.md
git diff --cached --check
git commit -m "fix: make GPU offer filters optional"
```

- [ ] **Step 4: Restart local ComfyUI safely**

Confirm `0 running / 0 pending` in `/queue` and fresh Vast inventory count `0`. Restart the ComfyUI Desktop backend on port `8190`, wait for a new listening PID, then fetch `/extensions/ComfyUI-Cloud-Run/js/cloud-run.js` and verify it contains the optional price/VRAM messages and comma parser. Do not create any Vast instance.

- [ ] **Step 5: Hand off the real search test**

Report the active URL and these exact examples:

```text
VRAM 24, price blank  -> GPUs with at least 24 GB, any price inside system bounds
Price 0,46, VRAM blank -> GPUs at or below $0.46/h, any VRAM inside system bounds
Both blank             -> no user price/VRAM filter
```

State that results still depend on currently available Vast inventory and all hard reliability/network/hardware gates.

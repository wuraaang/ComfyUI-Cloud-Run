# Stable Vast Offer Review and Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revalidate the exact selected Vast.ai offer without depending on a rotating top-20 search, display sanitized review errors, and keep the `Cloud Run` launcher attached across ComfyUI action-bar rerenders.

**Architecture:** Preserve the existing custom-node web/backend extension. Broad search remains responsible only for listing candidates; a separate backend-only exact lookup handles quote preview and confirmation. The existing frontend mount record owns one durable, idempotent DOM observer, and same-origin JSON remains the only browser/backend boundary.

**Tech Stack:** Python 3.13 stdlib and `aiohttp`, ComfyUI decorator routes, browser-native JavaScript ES modules, Node's built-in test runner, Python `unittest`, local Git worktree.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`.
- Do not modify, import from, or depend on ComfyRelay or `/Users/wuraaang/comfyui-vast-cockpit`.
- Do not change the package architecture, graph-node surface, local Run behavior, or user-visible offer selectors.
- No live `/confirm` request, instance creation, cancellation, destruction, or other paid Vast action is authorized.
- Only official template `57808457573e32120301649763d8e019` may ever be created.
- Never log, return, print, screenshot, or commit the Vast API key or bearer header.
- Exact lookup must use `ask_contract_id`, then require the canonical returned `offer_id` to equal the requested ID.
- Never silently replace the selected offer.
- Render backend error text with `textContent`.
- Follow strict RED → verify failure → GREEN → verify pass for every behavior change.
- Push nothing to GitHub; the configured remote currently points at the rejected ComfyRelay repository.

---

### Task 0: Preserve the certified lifecycle baseline in local Git

**Files:**
- Commit existing: `AGENTS.md`
- Commit existing: `README.md`
- Commit existing: `NOTICE`
- Commit existing: `__init__.py`
- Commit existing: `cloud_run/constants.py`
- Commit existing: `cloud_run/lifecycle.py`
- Commit existing: `cloud_run/models.py`
- Commit existing: `cloud_run/offers.py`
- Commit existing: `cloud_run/repository.py`
- Commit existing: `cloud_run/routes.py`
- Commit existing: `cloud_run/service.py`
- Commit existing: `cloud_run/settings.py`
- Commit existing: `cloud_run/vast.py`
- Commit existing: `docs/project-state.md`
- Commit existing: `docs/superpowers/plans/2026-07-30-vastai-cloud-run-lifecycle.md`
- Commit existing: `pyproject.toml`
- Commit existing: `scripts/check.sh`
- Commit existing: `tests/js/cloud-run-ui.test.mjs`
- Commit existing: `tests/js/fake-dom.mjs`
- Commit existing: all current `tests/python/test_*.py`
- Commit existing: `web/js/cloud-run.js`

**Interfaces:**
- Consumes: the already certified dirty lifecycle implementation.
- Produces: a clean local baseline commit so each regression fix has an isolated diff.

- [ ] **Step 1: Re-run the baseline repository gate**

Run:

```sh
scripts/check.sh
```

Expected: 82 Python tests and 15 JavaScript tests pass; compile, syntax, secret, provider, route, and state allowlists pass.

- [ ] **Step 2: Verify whitespace and intended file scope**

Run:

```sh
git diff --check
git status --short
```

Expected: no whitespace errors; only the certified lifecycle files listed above are dirty or untracked.

- [ ] **Step 3: Stage only the certified lifecycle baseline**

Run:

```sh
git add -- \
  AGENTS.md README.md NOTICE __init__.py \
  cloud_run/constants.py cloud_run/lifecycle.py cloud_run/models.py \
  cloud_run/offers.py cloud_run/repository.py cloud_run/routes.py \
  cloud_run/service.py cloud_run/settings.py cloud_run/vast.py \
  docs/project-state.md \
  docs/superpowers/plans/2026-07-30-vastai-cloud-run-lifecycle.md \
  pyproject.toml scripts/check.sh \
  tests/js/cloud-run-ui.test.mjs tests/js/fake-dom.mjs \
  tests/python/test_fake_lifecycle_integration.py \
  tests/python/test_lifecycle.py tests/python/test_loader_and_routes.py \
  tests/python/test_models.py tests/python/test_no_mutation_surface.py \
  tests/python/test_offers.py tests/python/test_repository.py \
  tests/python/test_repository_contract.py tests/python/test_routes.py \
  tests/python/test_service.py tests/python/test_settings.py \
  tests/python/test_vast.py web/js/cloud-run.js
git diff --cached --check
```

Expected: the staged diff has no whitespace errors and contains no design or implementation-plan file from 2026-07-31.

- [ ] **Step 4: Commit the baseline locally**

Run:

```sh
git commit -m "feat: implement safe Vast cloud lifecycle"
git status --short
```

Expected: the commit succeeds and the worktree is clean.

---

### Task 1: Add an exact read-only Vast offer contract

**Files:**
- Modify: `tests/python/test_vast.py`
- Modify: `cloud_run/vast.py`
- Modify: `scripts/check.sh`

**Interfaces:**
- Consumes: `OFFER_SEARCH_URL`, `build_search_payload`, `normalize_offers`, `_run_with_session`, `_validate_offer_id`.
- Produces: `async get_offer(api_key, offer_id, max_price_per_hour, min_vram_gb, session=None, *, min_inet_down_mbps=0, min_disk_bw_mbps=0, min_reliability=MIN_RELIABILITY, verified_only=True, secure_cloud_only=False) -> dict | None`.

- [ ] **Step 1: Write the failing exact-lookup test**

Add this test to `VastRequestTests` in `tests/python/test_vast.py`. Use the same complete raw offer fields as the existing broad-search test:

```python
def test_exact_offer_lookup_uses_contract_id_and_requires_canonical_match(self):
    from cloud_run.vast import OFFER_SEARCH_URL, get_offer

    matching_offer = {
        "id": 42,
        "gpu_name": "RTX 4090",
        "gpu_ram": 24576,
        "dph_total": 0.42,
        "reliability2": 0.99,
        "rentable": True,
        "verified": True,
        "num_gpus": 1,
        "type": "ondemand",
    }
    matching_session = FakeSession(
        FakeResponse(200, {"offers": [matching_offer]})
    )

    selected = asyncio.run(
        get_offer(
            "synthetic-value",
            offer_id="42",
            max_price_per_hour=0.75,
            min_vram_gb=24,
            session=matching_session,
        )
    )

    self.assertEqual(selected["offer_id"], 42)
    url, request = matching_session.calls[0]
    self.assertEqual(url, OFFER_SEARCH_URL)
    self.assertEqual(request["json"]["ask_contract_id"], {"eq": 42})
    self.assertEqual(request["json"]["limit"], 1)
    self.assertEqual(request["json"]["dph_total"], {"lte": 0.75})
    self.assertEqual(request["json"]["gpu_ram"], {"gte": 24576})

    mismatched_session = FakeSession(
        FakeResponse(200, {"offers": [{**matching_offer, "id": 99}]})
    )
    mismatched = asyncio.run(
        get_offer(
            "synthetic-value",
            offer_id=42,
            max_price_per_hour=0.75,
            min_vram_gb=24,
            session=mismatched_session,
        )
    )
    self.assertIsNone(mismatched)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```sh
python3 -m unittest tests.python.test_vast.VastRequestTests.test_exact_offer_lookup_uses_contract_id_and_requires_canonical_match -v
```

Expected: FAIL with `ImportError` because `get_offer` does not exist.

- [ ] **Step 3: Make the shared HTTP helper accept an exact ID**

Change `_search_with_session` so it builds the request payload once and adds only this targeted constraint when an exact ID is supplied:

```python
async def _search_with_session(
    session,
    api_key,
    max_price_per_hour,
    min_vram_gb,
    search_options,
    *,
    offer_id=None,
):
    request_payload = build_search_payload(
        max_price_per_hour,
        min_vram_gb,
        **search_options,
    )
    if offer_id is not None:
        request_payload["ask_contract_id"] = {"eq": int(offer_id)}
        request_payload["limit"] = 1
```

Pass `json=request_payload` to the existing `session.post` call. Keep all existing status handling, response parsing, normalization, and sanitization unchanged.

- [ ] **Step 4: Implement the exact lookup**

Add this function immediately after `_validate_offer_id`:

```python
async def get_offer(
    api_key,
    offer_id,
    max_price_per_hour,
    min_vram_gb,
    session=None,
    *,
    min_inet_down_mbps=0,
    min_disk_bw_mbps=0,
    min_reliability=MIN_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError(
            "Vast API key is not configured."
        )
    identifier = _validate_offer_id(offer_id)
    options = {
        "min_inet_down_mbps": min_inet_down_mbps,
        "min_disk_bw_mbps": min_disk_bw_mbps,
        "min_reliability": min_reliability,
        "verified_only": verified_only,
        "secure_cloud_only": secure_cloud_only,
    }

    async def operation(client):
        offers = await _search_with_session(
            client,
            api_key.strip(),
            max_price_per_hour,
            min_vram_gb,
            options,
            offer_id=identifier,
        )
        return next(
            (
                offer
                for offer in offers
                if str(offer.get("offer_id")) == identifier
            ),
            None,
        )

    try:
        return await _run_with_session(operation, session)
    except OfferSearchError:
        raise
    except VastError:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None
```

- [ ] **Step 5: Extend the deterministic provider allowlist**

In `scripts/check.sh`, add `"get_offer"` to `allowed_provider_actions` and to the explicit name set used to collect public provider functions:

```python
allowed_provider_actions = {
    "create_instance",
    "destroy_instance",
    "get_instance",
    "get_offer",
    "list_instances",
    "search_offers",
}
```

The collector's explicit set becomes:

```python
{"get_offer", "search_offers", "rent_instance", "stop_instance"}
```

- [ ] **Step 6: Verify GREEN and the broad-search regression**

Run:

```sh
python3 -m unittest tests.python.test_vast -v
```

Expected: all Vast tests pass; the existing broad search still sends no `ask_contract_id` and still uses limit 20.

- [ ] **Step 7: Commit the exact client contract**

Run:

```sh
git add -- tests/python/test_vast.py cloud_run/vast.py scripts/check.sh
git diff --cached --check
git commit -m "fix: look up exact Vast offers"
```

Expected: one local commit containing only the client contract, its test, and gate allowlist.

---

### Task 2: Route quote preview and confirmation through exact lookup

**Files:**
- Modify: `tests/python/test_service.py`
- Modify: `tests/python/test_fake_lifecycle_integration.py`
- Modify: `cloud_run/service.py`

**Interfaces:**
- Consumes: `vast.get_offer`, `apply_offer_policy`, quote snapshot fields.
- Produces: `VastProvider.get_offer(...)` and `CloudRunService._eligible_offer(...)`.

- [ ] **Step 1: Give the service fake a distinct exact-lookup channel**

Change `FakeProvider.__init__` and add `get_offer` in `tests/python/test_service.py`:

```python
def __init__(self, searches=None, lookups=None):
    self.searches = list(searches or [[offer()]])
    self.lookups = list([offer()] if lookups is None else lookups)
    self.search_calls = []
    self.lookup_calls = []
    self.create_calls = []
    self.inventory_calls = []
    self.instances = []
    self.create_result = "instance-9"
    self.create_error = None
    self.on_create = None

async def get_offer(
    self,
    api_key,
    offer_id,
    *,
    max_price_per_hour,
    min_vram_gb,
):
    self.lookup_calls.append(
        (api_key, str(offer_id), max_price_per_hour, min_vram_gb)
    )
    if not self.lookups:
        return None
    if len(self.lookups) > 1:
        return self.lookups.pop(0)
    return self.lookups[0]
```

Keep the existing `search_offers`, mutation, and inventory fake methods.

- [ ] **Step 2: Write the failing rotating-list preview regression**

Add:

```python
def test_preview_uses_exact_lookup_when_offer_is_absent_from_broad_results(self):
    provider = FakeProvider(searches=[[]], lookups=[offer()])
    service = self.service(provider)

    preview = asyncio.run(
        service.preview_offer(
            offer_id=42,
            idempotency_key="idem-targeted-preview",
        )
    )

    self.assertEqual(preview.quote.offer_id, "42")
    self.assertEqual(provider.search_calls, [])
    self.assertEqual(len(provider.lookup_calls), 1)
    self.assertEqual(provider.lookup_calls[0][1], "42")
    self.assertEqual(provider.create_calls, [])
```

- [ ] **Step 3: Run the preview test and verify RED**

Run:

```sh
python3 -m unittest tests.python.test_service.CloudRunServiceTests.test_preview_uses_exact_lookup_when_offer_is_absent_from_broad_results -v
```

Expected: FAIL with `QuoteUnavailable` because preview still performs the empty broad search.

- [ ] **Step 4: Add the production provider adapter and eligible-offer helper**

Add to `VastProvider`:

```python
async def get_offer(
    self,
    api_key,
    offer_id,
    *,
    max_price_per_hour,
    min_vram_gb,
):
    return await vast.get_offer(
        api_key,
        offer_id=offer_id,
        max_price_per_hour=max_price_per_hour,
        min_vram_gb=min_vram_gb,
    )
```

Add to `CloudRunService`:

```python
async def _eligible_offer(self, identifier, settings):
    selected = await self.provider.get_offer(
        settings["api_key"],
        identifier,
        max_price_per_hour=settings["max_price_per_hour"],
        min_vram_gb=settings["min_vram_gb"],
    )
    if selected is None:
        return None
    eligible = apply_offer_policy(
        [selected],
        blacklist=self.blacklist,
        now=float(self.clock()),
    )
    return next(
        (
            offer
            for offer in eligible
            if str(offer.get("offer_id")) == str(identifier)
        ),
        None,
    )
```

Replace preview's broad search and list scan with:

```python
selected = await self._eligible_offer(identifier, settings)
```

Keep the existing `QuoteUnavailable` message and quote persistence unchanged.

- [ ] **Step 5: Verify the preview test is GREEN**

Run:

```sh
python3 -m unittest tests.python.test_service.CloudRunServiceTests.test_preview_uses_exact_lookup_when_offer_is_absent_from_broad_results -v
```

Expected: PASS with one exact lookup, no broad search, and no create call.

- [ ] **Step 6: Write the failing confirmation-path assertion**

Change `test_confirmation_revalidates_same_offer_and_never_accepts_price_rise` to use:

```python
provider = FakeProvider(
    searches=[[]],
    lookups=[offer(), offer(price=0.43)],
)
```

After the existing assertions, add:

```python
self.assertEqual(len(provider.lookup_calls), 2)
self.assertEqual(provider.search_calls, [])
```

- [ ] **Step 7: Run the confirmation test and verify RED**

Run:

```sh
python3 -m unittest tests.python.test_service.CloudRunServiceTests.test_confirmation_revalidates_same_offer_and_never_accepts_price_rise -v
```

Expected: FAIL because confirmation still calls broad `search_offers`, so only one exact lookup is recorded.

- [ ] **Step 8: Make confirmation reuse exact eligibility**

Replace the broad search at the start of `_revalidated_offer` with:

```python
selected = await self._eligible_offer(
    attempt.quote.offer_id,
    settings,
)
if selected is None:
    return None
if (
    str(selected.get("gpu_name")) == attempt.quote.gpu_name
    and float(selected.get("gpu_ram_gb", 0))
    >= attempt.quote.gpu_ram_gb
    and float(selected.get("dph_total", float("inf")))
    <= attempt.quote.dph_total
):
    return selected
return None
```

- [ ] **Step 9: Update existing offline fakes without changing their behavior**

Add this method to `OfflineVast` in
`tests/python/test_fake_lifecycle_integration.py`:

```python
async def get_offer(
    self,
    _api_key,
    offer_id,
    *,
    max_price_per_hour,
    min_vram_gb,
):
    if (
        str(self.offer["offer_id"]) == str(offer_id)
        and self.offer["dph_total"] <= max_price_per_hour
        and self.offer["gpu_ram_gb"] >= min_vram_gb
    ):
        return dict(self.offer)
    return None
```

In existing service tests that supply two broad results solely for preview and
confirmation, replace:

```python
FakeProvider(searches=[[offer()], [offer()]])
```

with:

```python
FakeProvider(lookups=[offer(), offer()])
```

Use the corresponding quoted price variant where that test intentionally
changes price.

- [ ] **Step 10: Verify all service and offline lifecycle tests**

Run:

```sh
python3 -m unittest \
  tests.python.test_service \
  tests.python.test_fake_lifecycle_integration -v
```

Expected: all tests pass; quote review records zero create calls, and fake confirmation still creates exactly once only inside the offline confirmation tests.

- [ ] **Step 11: Commit the service routing fix**

Run:

```sh
git add -- \
  tests/python/test_service.py \
  tests/python/test_fake_lifecycle_integration.py \
  cloud_run/service.py
git diff --cached --check
git commit -m "fix: revalidate selected Vast offer directly"
```

Expected: one local commit containing only exact lookup integration and fakes.

---

### Task 3: Surface sanitized quote errors in the dialog

**Files:**
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Interfaces:**
- Consumes: same-origin `{error: string}` JSON from route failures.
- Produces: a sanitized `Error.message` from `fetchJson`; quote status rendered with `textContent`.

- [ ] **Step 1: Write the failing quote-error UI test**

Add after the successful server-quote test:

```javascript
test("quote review displays the sanitized backend error", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [{
        offer_id: 42,
        gpu_name: "RTX 4090",
        gpu_ram_gb: 24,
        dph_total: 0.42,
        reliability: 0.99,
      }],
    }),
    jsonResponse(
      { error: "The selected Vast offer is no longer available." },
      { ok: false, status: 409 },
    ),
  ];

  mountCloudRun(document, async () => responses.shift());
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-preview-selection").click();

  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "The selected Vast offer is no longer available.",
  );
});
```

- [ ] **Step 2: Run the UI test and verify RED**

Run:

```sh
node --test --test-name-pattern="quote review displays" tests/js/cloud-run-ui.test.mjs
```

Expected: FAIL because the status contains `The selected offer could not be quoted.`.

- [ ] **Step 3: Sanitize every `fetchJson` failure path**

Replace `fetchJson` with:

```javascript
async function fetchJson(fetchImpl, endpoint, options) {
  let response;
  try {
    response = await fetchImpl(endpoint, options);
  } catch {
    throw new Error("request failed");
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("request failed");
  }

  if (!payload || typeof payload !== "object") {
    throw new Error("request failed");
  }
  if (!response.ok) {
    const message =
      typeof payload.error === "string" ? payload.error.trim() : "";
    throw new Error(message || "request failed");
  }
  return payload;
}
```

- [ ] **Step 4: Display only the helper's safe message**

Change the quote handler's failure tracking to:

```javascript
let failureMessage = null;
try {
  currentAttempt = await fetchJson(fetchImpl, QUOTES_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      offer_id: selectedOffer.offer_id,
      idempotency_key: idempotencyKey,
    }),
  });
} catch (error) {
  const message =
    error instanceof Error ? String(error.message).trim() : "";
  failureMessage =
    message && message !== "request failed"
      ? message
      : "The selected offer could not be quoted.";
} finally {
  setBusy(false);
  if (failureMessage) {
    status.textContent = failureMessage;
  }
}
```

- [ ] **Step 5: Verify GREEN and existing generic fallbacks**

Run:

```sh
node --test tests/js/cloud-run-ui.test.mjs
```

Expected: the new sanitized-error test and all existing frontend-unavailable and quote-success tests pass.

- [ ] **Step 6: Commit the error presentation fix**

Run:

```sh
git add -- tests/js/cloud-run-ui.test.mjs web/js/cloud-run.js
git diff --cached --check
git commit -m "fix: show safe Vast quote errors"
```

Expected: one local commit containing only JSON error handling and its UI test.

---

### Task 4: Keep the launcher attached after ComfyUI rerenders

**Files:**
- Modify: `tests/js/cloud-run-ui.test.mjs`
- Modify: `web/js/cloud-run.js`

**Interfaces:**
- Consumes: the stable local `[data-testid="queue-button"]` anchor and `document.body`.
- Produces: one persistent `MutationObserver`, one launcher element, and idempotent immediate-sibling placement.

- [ ] **Step 1: Write the failing action-bar replacement test**

Add after the late-button test:

```javascript
test("reattaches the same launcher after the action bar is replaced", () => {
  const document = new FakeDocument();
  const first = mountLocalRunButton(document);
  let mutationCallback = null;
  let disconnectCount = 0;
  const browserWindow = {
    MutationObserver: class {
      constructor(callback) {
        mutationCallback = callback;
      }

      observe(root, options) {
        assert.equal(root, document.body);
        assert.deepEqual(options, { childList: true, subtree: true });
      }

      disconnect() {
        disconnectCount += 1;
      }
    },
  };

  const launcher = mountCloudRun(
    document,
    async () => settingsResponse(),
    browserWindow,
  );
  assert.equal(typeof mutationCallback, "function");
  assert.deepEqual(first.actionbar.children, [first.queueGroup, launcher]);

  first.actionbar.remove();
  const second = mountLocalRunButton(document);
  mutationCallback();

  assert.equal(launcher.isConnected, true);
  assert.deepEqual(second.actionbar.children, [second.queueGroup, launcher]);
  assert.equal(disconnectCount, 0);

  mutationCallback();
  assert.deepEqual(second.actionbar.children, [second.queueGroup, launcher]);
});
```

- [ ] **Step 2: Run the replacement test and verify RED**

Run:

```sh
node --test --test-name-pattern="reattaches the same launcher" tests/js/cloud-run-ui.test.mjs
```

Expected: FAIL because an initially successful placement creates no persistent observer.

- [ ] **Step 3: Make placement idempotent**

Replace `placeLauncherBesideLocalRun` with:

```javascript
function placeLauncherBesideLocalRun(document, launcher) {
  const queueButton = document.querySelector?.('[data-testid="queue-button"]');
  const queueGroup = queueButton?.parentElement;
  const actionbar = queueGroup?.parentElement;
  if (!actionbar) return false;
  if (
    launcher.parentElement === actionbar &&
    queueGroup.nextSibling === launcher
  ) {
    return true;
  }
  actionbar.insertBefore(launcher, queueGroup.nextSibling);
  return true;
}
```

- [ ] **Step 4: Keep one observer alive for the mounted extension**

Replace `ensureLauncherPlacement` with:

```javascript
function ensureLauncherPlacement(browserWindow, document, mounted) {
  const placed = placeLauncherBesideLocalRun(document, mounted.launcher);
  if (
    !mounted.observer &&
    typeof browserWindow?.MutationObserver === "function"
  ) {
    mounted.observer = new browserWindow.MutationObserver(() => {
      placeLauncherBesideLocalRun(document, mounted.launcher);
    });
    mounted.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
  return placed;
}
```

In the existing late-local-button test, change the expected disconnect count
after successful placement from `1` to `0`.

- [ ] **Step 5: Verify GREEN and all launcher invariants**

Run:

```sh
node --test tests/js/cloud-run-ui.test.mjs
```

Expected: all UI tests pass, including initial placement, late placement, action-bar replacement, keyboard opening, Extensions fallback, and no duplicate insertion.

- [ ] **Step 6: Commit the launcher stability fix**

Run:

```sh
git add -- tests/js/cloud-run-ui.test.mjs web/js/cloud-run.js
git diff --cached --check
git commit -m "fix: persist Cloud Run launcher placement"
```

Expected: one local commit containing only observer/placement behavior and tests.

---

### Task 5: Certify the complete change without renting

**Files:**
- Verify: all repository files
- Verify served install: `/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run`

**Interfaces:**
- Consumes: local tests, installed symlink, read-only Vast search and quote routes.
- Produces: fresh evidence that the fixes pass and no paid provider mutation occurred.

- [ ] **Step 1: Run targeted Python and JavaScript suites together**

Run:

```sh
python3 -m unittest \
  tests.python.test_vast \
  tests.python.test_service \
  tests.python.test_fake_lifecycle_integration -v
node --test tests/js/cloud-run-ui.test.mjs
```

Expected: every targeted test passes with zero failures.

- [ ] **Step 2: Run the deterministic repository gate**

Run:

```sh
scripts/check.sh
```

Expected: all Python and JavaScript tests, compilation, syntax checks, secret scan, provider allowlist, route allowlist, and state allowlist pass.

- [ ] **Step 3: Inspect the final diff and installation link**

Run:

```sh
git diff --check
git status --short --branch
readlink /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run
```

Expected: no whitespace errors, no uncommitted implementation changes, and the symlink resolves to `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`.

- [ ] **Step 4: Verify the served JavaScript contains the durable observer**

Use the currently active ComfyUI Desktop port determined from its Python
command line. Request only the extension JavaScript and check for the stable
function names; do not request or print settings:

```sh
curl -fsS \
  http://127.0.0.1:8190/extensions/ComfyUI-Cloud-Run/js/cloud-run.js \
  | rg "ensureLauncherPlacement|failureMessage"
if curl -fsS \
  http://127.0.0.1:8190/extensions/ComfyUI-Cloud-Run/js/cloud-run.js \
  | rg -q "ask_contract_id"; then
  exit 1
fi
```

Expected: `ensureLauncherPlacement` and `failureMessage` are present, and the
backend-only `ask_contract_id` is absent from frontend JavaScript.

- [ ] **Step 5: Perform one read-only live Vast review after backend reload**

Restart only the active ComfyUI Python child through ComfyUI Desktop's normal
restart control so the backend imports the new Python module. Then:

1. Open `Cloud Run`.
2. Click `Search Vast GPUs`.
3. Select one currently listed offer.
4. Click `Review paid rental`.
5. Verify the exact GPU, VRAM, hourly price, offer ID, configured cap, and
   official template appear.
6. Do not click `Confirm & rent GPU`.

Expected: quote review succeeds or shows a specific sanitized eligibility
error; no instance appears in Vast inventory and no paid request is sent.

- [ ] **Step 6: Run final verification and record local commits**

Run:

```sh
scripts/check.sh
git log --oneline --decorate -6
git status --short --branch
```

Expected: the full gate passes freshly, the spec/plan/baseline/fix commits are
visible locally, and the worktree is clean. Do not push.

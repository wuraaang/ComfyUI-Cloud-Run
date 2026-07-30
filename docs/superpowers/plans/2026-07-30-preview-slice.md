# Cloud Run Preview Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable, web-only ComfyUI custom-node package that stores a write-only Vast key, searches matching Vast offers read-only, and previews a selected offer against the official ComfyUI template without any provider mutation path.

**Architecture:** The package root exports only the ComfyUI web-loader contract and registers three aiohttp handlers through `PromptServer.instance.routes`. Focused backend modules own settings persistence, request validation, and the single Vast bundles search. One dependency-free browser module mounts a persistent button and constructs the modal exclusively with DOM APIs and `textContent`.

**Tech Stack:** Python 3.9+ stdlib, host-provided aiohttp/ComfyUI APIs, browser DOM APIs, `unittest`, and Node.js `node:test`.

## Global Constraints

- Implement only the V0 preview slice in `AGENTS.md` and `docs/project-state.md`.
- Write only inside `/Users/wuraaang/ComfyUI-Cloud-Run`.
- Do not import from or depend on LoRA Dataset Studio or any other product.
- Vast provider access is limited to `POST https://console.vast.ai/api/v0/bundles/`.
- Never expose or log API-key material.
- Do not add create, rent, start, stop, destroy, `/asks`, or `/instances` provider behavior.
- Use strict TDD and retain concise RED/GREEN command evidence.
- Do not install into the pinned host, restart ComfyUI, call the real network, commit, or push.

---

### Task 1: Web-only loader and route surface

**Files:**
- Create: `__init__.py`
- Create: `cloud_run/__init__.py`
- Create: `cloud_run/routes.py`
- Test: `tests/python/test_loader_and_routes.py`

**Interfaces:**
- Produces: `WEB_DIRECTORY = "./web"`, empty node mappings, and `register_routes()`.
- Produces: decorators for `GET /cloud-run/api/settings`, `PUT /cloud-run/api/settings`, and `POST /cloud-run/api/offers`.

- [ ] **Step 1: Write the failing loader/route-contract test**

  Load the repository root as a package with fake `server.PromptServer` and `aiohttp.web` modules. Assert the three exports and the exact three decorated method/path pairs.

- [ ] **Step 2: Run test to verify RED**

  Run: `python3 -m unittest tests.python.test_loader_and_routes -v`

  Expected: failure because `__init__.py` and the route registration do not exist.

- [ ] **Step 3: Implement the minimum loader and decorators**

  Keep imports host-safe by resolving `PromptServer` and `aiohttp.web` inside `register_routes()`. The three handlers may initially return preview-shaped placeholder JSON; later tasks replace each behavior after their own failing tests.

- [ ] **Step 4: Run test to verify GREEN**

  Run the same command and require all loader assertions to pass.

### Task 2: Validated write-only settings

**Files:**
- Create: `cloud_run/constants.py`
- Create: `cloud_run/settings.py`
- Modify: `cloud_run/routes.py`
- Test: `tests/python/test_settings.py`
- Test: `tests/python/test_routes.py`

**Interfaces:**
- Produces: `SettingsStore`, `SettingsValidationError`, `validate_update(payload)`, and `public_settings(settings)`.
- Persists: `settings.json` under `COMFYUI_CLOUD_RUN_DATA_DIR` when set, otherwise under `folder_paths.get_user_directory()/comfyui-cloud-run`.
- Public shape: `configured`, `max_price_per_hour`, `min_vram_gb`, `official_template_id`, `official_template_name`, and `preview_only`.

- [ ] **Step 1: Write failing validation tests**

  Assert finite positive price bounds, integral VRAM bounds, optional non-empty string key handling, rejection of booleans/unknown keys, and normalized accepted values.

- [ ] **Step 2: Run validation tests to verify RED**

  Run: `python3 -m unittest tests.python.test_settings.SettingsValidationTests -v`

  Expected: import failure because the settings module does not exist.

- [ ] **Step 3: Implement validation and defaults**

  Use explicit constants for the official template, default filters, and input bounds. Never place the API key in validation exceptions.

- [ ] **Step 4: Run validation tests to verify GREEN**

  Run the same command and require all cases to pass.

- [ ] **Step 5: Write failing persistence/redaction tests**

  Save a synthetic key in a temporary directory, assert final file mode `0600`, atomic-temp cleanup, preservation when a later update omits the key, and an exact public response with no key or masked fragment.

- [ ] **Step 6: Run persistence tests to verify RED**

  Run the targeted persistence test class and confirm failure because storage is not implemented.

- [ ] **Step 7: Implement atomic persistence**

  Create the dedicated directory, write JSON through `mkstemp`, `fsync`, `chmod(0600)`, and `os.replace`, then enforce `0600` on the destination. Recover from missing/corrupt files using safe defaults.

- [ ] **Step 8: Run persistence tests to verify GREEN**

  Run the targeted class and then the full settings test module.

- [ ] **Step 9: Write failing GET/PUT route tests**

  Invoke captured handlers with fake requests and responses. Assert GET returns only the public fields, PUT reports sanitized `400` errors, and successful PUT never echoes key material.

- [ ] **Step 10: Implement GET/PUT handlers and verify GREEN**

  Parse JSON defensively, call `SettingsStore`, and return only fixed error messages/public settings.

### Task 3: Read-only Vast offer search

**Files:**
- Create: `cloud_run/vast.py`
- Modify: `cloud_run/routes.py`
- Test: `tests/python/test_vast.py`
- Modify: `tests/python/test_routes.py`
- Create: `tests/python/test_no_mutation_surface.py`

**Interfaces:**
- Produces: `build_search_payload(max_price_per_hour, min_vram_gb)`.
- Produces: `normalize_offers(payload, max_price_per_hour, min_vram_gb)`.
- Produces: `async search_offers(api_key, max_price_per_hour, min_vram_gb, session=None)`.
- Uses only `POST https://console.vast.ai/api/v0/bundles/`, a 30-second timeout, and limit 20.

- [ ] **Step 1: Write failing request-contract tests**

  With a fake aiohttp-shaped session, assert Bearer auth is server-side, body filters are `gpu_ram >= min*1024`, `reliability >= 0.95`, `rentable == true`, `dph_total <= max`, `num_gpus == 1`, verified-only, on-demand, and bounded limit.

- [ ] **Step 2: Run request tests to verify RED**

  Run: `python3 -m unittest tests.python.test_vast.VastRequestTests -v`

  Expected: import failure because the Vast module does not exist.

- [ ] **Step 3: Implement the single provider request**

  Lazily import host-provided aiohttp when no injected session exists. Do not read provider response bodies on HTTP errors and replace all transport/parse details with fixed public messages.

- [ ] **Step 4: Run request tests to verify GREEN**

  Run the same command and require captured URL, headers, body, and normalized output assertions to pass.

- [ ] **Step 5: Write failing normalization/error tests**

  Assert only the five allowed offer fields survive, GPU RAM converts MB to GiB, `reliability2` falls back correctly, invalid/mismatched records are discarded, price sorting is ascending, and synthetic secrets in transport/provider errors never escape.

- [ ] **Step 6: Implement normalization/error sanitation and verify GREEN**

  Validate remote scalar types, bound GPU names, locally reapply constraints, and raise only fixed `OfferSearchError` messages.

- [ ] **Step 7: Write failing offers-route and mutation-surface tests**

  Assert missing configuration is a sanitized client error, provider failure is a sanitized gateway error, success returns only `offers` plus `preview_only`, registered routes remain exactly three, and production sources contain no forbidden provider path.

- [ ] **Step 8: Implement the offers handler and verify GREEN**

  Read saved settings, call the single search function, and serialize only normalized records.

### Task 4: Persistent browser UI and local-only preview

**Files:**
- Create: `web/js/cloud-run.js`
- Create: `package.json`
- Create: `tests/js/fake-dom.mjs`
- Create: `tests/js/cloud-run-ui.test.mjs`

**Interfaces:**
- Produces: `mountCloudRun(document, fetchImpl)`, `renderOffers(...)`, and `renderSelectionPreview(...)`.
- Registers: `comfyui-cloud-run.preview` through the ComfyUI frontend extension API once available.
- Uses only `/cloud-run/api/settings` and `/cloud-run/api/offers`.

- [ ] **Step 1: Write a failing visible-button/modal test**

  Mount against the tiny fake DOM and assert an immediately visible button with exact text `Cloud Run`, stable id/test id, and a click-opened modal containing the exact preview banner and required controls.

- [ ] **Step 2: Run UI test to verify RED**

  Run: `node --test tests/js/cloud-run-ui.test.mjs`

  Expected: module-not-found failure because the frontend module does not exist.

- [ ] **Step 3: Implement the minimum mount/modal DOM**

  Construct every node with `createElement`, attributes, and `textContent`; inject static CSS through a style node. Register the extension with a bounded readiness retry.

- [ ] **Step 4: Run the targeted UI test to verify GREEN**

  Re-run Node tests and require the visible click path to pass.

- [ ] **Step 5: Write failing settings/search/rendering tests**

  Assert PUT omits a blank key, clears a supplied password after success, POST search renders malicious remote text inertly, offer rows display GPU/VRAM/price/reliability, and errors use text-only output.

- [ ] **Step 6: Implement settings/search/rendering and verify GREEN**

  Use same-origin `fetch`, JSON requests, fixed endpoint constants, and no browser storage, URL, or console credential path.

- [ ] **Step 7: Write a failing local-preview test**

  Select an offer, click `Preview selection`, assert the selected GPU/price, official template id, and `no instance was created` message appear, and assert the fetch call count does not increase.

- [ ] **Step 8: Implement local preview and verify GREEN**

  Update only local DOM state. Do not add any provider endpoint or request.

### Task 5: Package/docs and deterministic gate

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `LICENSE`
- Create: `.gitignore`
- Create: `scripts/check.sh`

**Interfaces:**
- Produces: one installable web-only custom-node package with no new runtime dependency.
- Produces: `scripts/check.sh` as the repository acceptance command.

- [ ] **Step 1: Write the gate with exact checks**

  Run Python unittest discovery, Node tests, Python compilation, JavaScript syntax validation, secret-pattern scanning, and production-source checks that forbid non-bundles Vast paths or mutation-shaped provider calls.

- [ ] **Step 2: Run the gate to expose missing metadata/docs**

  Add a deterministic repository-contract test if necessary so this run is RED for missing required package files rather than relying on manual observation.

- [ ] **Step 3: Add minimum metadata and documentation**

  Document preview-only behavior, install/use steps, backend key location and permissions, exact route surface, template id, test command, and explicit non-capabilities. Use MIT license text and concise future-registry metadata.

- [ ] **Step 4: Run targeted suites and then `scripts/check.sh`**

  Require exit code `0`, no network, no host installation, and no secret output.

### Task 6: Review and final evidence

**Files:**
- Inspect all changed files.

**Interfaces:**
- Produces: final changed-file list, exact verification output, RED/GREEN evidence, and blockers.

- [ ] **Step 1: Request independent read-only code review**

  Give a reviewer the complete contract and diff. Require findings prioritized by severity and forbid edits.

- [ ] **Step 2: Address valid findings through fresh RED/GREEN cycles**

  Add a failing regression test before each production correction, then re-run the targeted and full suites.

- [ ] **Step 3: Run fresh completion verification**

  Run: `scripts/check.sh`

  Then run: `git status --short` and `git diff --check`.

- [ ] **Step 4: Report without committing**

  State that repository checks passed only if the fresh output proves it. Do not claim host installation/browser success; Hermes owns that next step.

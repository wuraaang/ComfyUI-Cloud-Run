# Vast Omitted Offer Type Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore read-only Vast GPU search when Vast omits the redundant offer `type`, without weakening rejection of explicitly non-on-demand offers.

**Architecture:** Keep `normalize_offers` fail-closed unless its caller supplies the exact rental type from the request payload sent to Vast. The internal search transport supplies that trusted request context; explicit response values continue to override omission compatibility and must equal `ondemand`.

**Tech Stack:** Python 3, `unittest`, asynchronous Vast HTTP adapter, ComfyUI Desktop local HTTP API.

## Global Constraints

- Accept an omitted response `type` only when the actual originating request required `type=ondemand`.
- Reject explicit `type=null`, malformed values, and explicit non-on-demand values such as `bid`.
- Keep all other offer constraints, ranking, pricing settings, and workflow-fit behavior unchanged.
- Do not create or confirm a Vast session, publish a release, recreate a template, push commits, or expose credentials.
- Live verification stops after read-only offer search. The user will manually run and time any paid GPU test, without an automated stop timer added here.
- Use the existing linked worktree and the existing ComfyUI Desktop backend; never launch a second backend.

---

### Task 1: Add the regression contract and minimal compatibility fix

**Files:**
- Modify: `tests/python/test_vast.py`
- Modify: `cloud_run/vast.py`

**Interfaces:**
- Consumes: `build_search_payload(...) -> dict`, whose sent payload contains `"type": "ondemand"`.
- Produces: `normalize_offers(..., requested_rental_type=None) -> list[dict]`, with a safe default and explicit trusted request context.

- [ ] **Step 1: Write failing transport and normalization tests**

In `VastRequestTests.test_search_posts_exact_read_only_contract_and_normalizes_result`, remove `"type": "ondemand"` from the synthetic Vast response while retaining the existing assertion that both sent request payloads contain it.

Add this focused test to `VastNormalizationAndErrorTests`:

```python
def test_missing_response_type_requires_trusted_ondemand_request(self):
    from cloud_run.vast import normalize_offers

    raw = {
        "id": 10,
        "gpu_name": "RTX 5090",
        "gpu_ram": 32768,
        "dph_total": 0.25,
        "reliability": 0.99,
        "inet_down": 500,
        "disk_bw": 400,
        "disk_space": 80,
        "gpu_arch": "nvidia",
        "cpu_arch": "amd64",
        "cuda_max_good": 12.9,
        "compute_cap": 750,
        "num_gpus": 1,
        "rentable": True,
        "verification": "verified",
    }
    arguments = {
        "max_price_per_hour": 0.75,
        "min_vram_gb": 24,
    }

    self.assertEqual(normalize_offers({"offers": [raw]}, **arguments), [])
    accepted = normalize_offers(
        {"offers": [raw]},
        requested_rental_type="ondemand",
        **arguments,
    )
    self.assertEqual([offer["offer_id"] for offer in accepted], [10])
    self.assertEqual(
        normalize_offers(
            {"offers": [{**raw, "type": "bid"}]},
            requested_rental_type="ondemand",
            **arguments,
        ),
        [],
    )
    self.assertEqual(
        normalize_offers(
            {"offers": [{**raw, "type": None}]},
            requested_rental_type="ondemand",
            **arguments,
        ),
        [],
    )
```

- [ ] **Step 2: Run the focused tests and observe the regression**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_vast.VastRequestTests.test_search_posts_exact_read_only_contract_and_normalizes_result \
  tests.python.test_vast.VastNormalizationAndErrorTests.test_missing_response_type_requires_trusted_ondemand_request -v
```

Expected: failure because the current transport drops the response without `type`, plus an error because `requested_rental_type` does not exist yet.

- [ ] **Step 3: Implement the minimal request-context rule**

Extend the keyword-only parameters of `normalize_offers`:

```python
    requested_rental_type=None,
```

Normalize the trusted request context once before iterating offers:

```python
    request_guarantees_ondemand = (
        isinstance(requested_rental_type, str)
        and requested_rental_type.casefold() == "ondemand"
    )
```

For each raw offer, replace the unconditional response-type requirement with:

```python
        has_rental_type = "type" in raw
        rental_type_is_valid = (
            isinstance(rental_type, str)
            and rental_type.casefold() == "ondemand"
        ) or (not has_rental_type and request_guarantees_ondemand)
```

Use `or not rental_type_is_valid` in the existing rejection condition. In `_search_with_session`, pass the exact sent constraint:

```python
            requested_rental_type=request_payload.get("type"),
```

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_vast.VastRequestTests.test_search_posts_exact_read_only_contract_and_normalizes_result \
  tests.python.test_vast.VastNormalizationAndErrorTests.test_missing_response_type_requires_trusted_ondemand_request -v
```

Expected: two tests pass, with zero failures and zero errors.

- [ ] **Step 5: Commit the tested correction**

Run:

```sh
git add cloud_run/vast.py tests/python/test_vast.py
git diff --cached --check
git commit -m "fix: accept Vast offers with implied rental type"
```

Expected: one local commit containing only the regression tests and compatibility fix.

---

### Task 2: Verify the repository and the existing ComfyUI Desktop backend

**Files:**
- Verify: `cloud_run/vast.py`
- Verify: `tests/python/test_vast.py`
- Verify installed link: `/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run`

**Interfaces:**
- Consumes: the tested normalizer and the installed symlink to this worktree.
- Produces: fresh proof that the local Search endpoint returns sanitized offers and creates no session.

- [ ] **Step 1: Run the complete Vast adapter test module**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.python.test_vast -v
```

Expected: every Vast adapter test passes.

- [ ] **Step 2: Run the deterministic repository gate once**

Run:

```sh
scripts/check.sh
```

Expected: all repository checks pass. Do not rerun it unless this new change causes a failure that must be diagnosed.

- [ ] **Step 3: Confirm the installed extension points at the corrected worktree**

Run:

```sh
readlink /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run
git status --short --branch
```

Expected: the link resolves to `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit`, and the worktree has no uncommitted changes.

- [ ] **Step 4: Reload only the existing idle ComfyUI Desktop backend**

Confirm port `8188` belongs to the already running Desktop backend and verify `/queue` reports no running or pending prompt. Use ComfyUI Manager's restart route on that same port, then wait for `/cloud-run/api/settings` to become available again. Do not start another Python process.

Expected: the existing backend returns on port `8188`, with the Cloud Run extension loaded.

- [ ] **Step 5: Perform one read-only live offer search**

POST only the known rentable preflight identifier to the search route:

```sh
curl -fsS \
  -H 'Content-Type: application/json' \
  -d '{"preflight_id":"158b51e2-e21c-4d1e-b930-c35037004412"}' \
  http://127.0.0.1:8188/cloud-run/api/offers
```

Expected: HTTP 200 with a non-empty sanitized `offers` array. Then GET `/cloud-run/api/settings` and verify `active_sessions` remains empty. Do not call any `/sessions` route.

- [ ] **Step 6: Inspect final evidence**

Run:

```sh
git diff --check
git log --oneline --decorate -4
git status --short --branch
```

Expected: no whitespace errors, no uncommitted changes, and only local commits; nothing is pushed or published.

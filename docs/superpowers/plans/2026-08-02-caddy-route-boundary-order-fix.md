# Caddy Worker Boundary Ordering Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the reviewed Remote Worker gateway accept the exact controller-owned bearer token before stripping credentials, while preserving every existing session, HMAC, manifest, deadline, and destruction contract.

**Architecture:** Keep the approved controller-owned boundary from `docs/superpowers/specs/2026-08-02-controller-owned-worker-boundary-design.md`. Change no Python runtime behavior: wrap the existing Caddy handlers in one `route` block so Caddy preserves their written order—reject unauthorized requests, strip inbound credentials, set the internal authenticated marker, then proxy to the loopback worker. Lock the canonical Caddyfile text in a regression test because Caddy's normal directive sorting caused the paid failure.

**Tech Stack:** Caddyfile, Python 3.13 `unittest`, deterministic worker archives, `scripts/check.sh`.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on branch `fix/vast-template-live-audit`.
- Preserve the approved controller-owned `CLOUD_RUN_BOUNDARY_TOKEN`; do not restore `jupyter_token`, `JUPYTER_TOKEN`, or `OPEN_BUTTON_TOKEN` as credentials.
- Preserve the separate post-claim HMAC session secret and every existing worker route.
- Do not change Vast create logic, offer selection, workflow capture, manifest resolution, model transfer, ComfyUI execution, output provenance, deadline handling, or destruction logic.
- Do not rent, search paid offers, publish a GitHub release, create/update/delete a Vast template, rotate the local release lock, or restart ComfyUI during this implementation.
- The frozen worker `76f2fff05f05b2fc7372b8cdf507d84dc794abe8`, archive `844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5`, and template hash `9d6822f9429822ee9e7339a804a549da` remain historical failed evidence and must never be presented as fixed.
- A manual paid test is meaningful only after the fixed source commit has been published as a new immutable worker archive, bound to a new reviewed template/lock, and loaded by ComfyUI under separate authorization.
- Never weaken a regression or the repository baseline merely to obtain green output.

---

### Task 1: Lock and repair the Caddy handler order

**Files:**

- Modify: `tests/python/test_worker_gateway.py:506`
- Modify: `remote_worker/Caddyfile:1`

**Interfaces:**

- Consumes: `CLOUD_RUN_BOUNDARY_TOKEN`, external port `8765`, loopback worker `127.0.0.1:8766`.
- Produces: HTTP `401` for a missing or wrong bearer; for the exact bearer, a proxied request with `Authorization` removed, caller-supplied `X-Cloud-Run-Boundary` removed, and `X-Cloud-Run-Boundary: authenticated` set internally.

- [ ] **Step 1: Replace the permissive Caddyfile assertions with one canonical regression**

In `GatewayConfigurationTests.test_caddyfile_is_the_exact_external_bearer_boundary`, replace the individual `assertIn` checks with this exact assertion:

```python
    def test_caddyfile_is_the_exact_external_bearer_boundary(self):
        caddyfile = Path(__import__("remote_worker.gateway").gateway.__file__).with_name(
            "Caddyfile"
        )
        expected = """{
    admin off
    auto_https off
}

:8765 {
    route {
        @unauthorized not header Authorization "Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}"
        respond @unauthorized 401

        request_header -Authorization
        request_header -X-Cloud-Run-Boundary
        request_header X-Cloud-Run-Boundary authenticated
        reverse_proxy 127.0.0.1:8766
    }
}
"""
        self.assertEqual(caddyfile.read_text(encoding="utf-8"), expected)
```

The exact comparison is intentional: it prevents a later edit from moving header mutations outside `route` while leaving all old substring assertions green.

- [ ] **Step 2: Run the single regression and observe RED**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_gateway.GatewayConfigurationTests.test_caddyfile_is_the_exact_external_bearer_boundary \
  -v
```

Expected: `FAIL`; the diff shows that the current Caddyfile lacks the `route` wrapper. A syntax/import error is not the required red state and must be corrected before continuing.

- [ ] **Step 3: Apply the minimal production correction**

Replace `remote_worker/Caddyfile` with exactly:

```caddyfile
{
    admin off
    auto_https off
}

:8765 {
    route {
        @unauthorized not header Authorization "Bearer {$CLOUD_RUN_BOUNDARY_TOKEN}"
        respond @unauthorized 401

        request_header -Authorization
        request_header -X-Cloud-Run-Boundary
        request_header X-Cloud-Run-Boundary authenticated
        reverse_proxy 127.0.0.1:8766
    }
}
```

Do not touch `remote_worker/gateway.py`, `cloud_run/vast.py`, or `cloud_run/worker_client.py`. The live audit already proved both injected values matched the controller; changing token plumbing would add an untested variable to a one-line ordering defect.

- [ ] **Step 4: Re-run the single regression and observe GREEN**

Run the Step 2 command again.

Expected: `OK`, one test run.

- [ ] **Step 5: Move ignored Python cache bytes outside the reviewed worker tree**

The release builder intentionally rejects any `remote_worker/__pycache__` directory. Preserve an existing cache outside the worktree instead of deleting it:

```sh
if [ -d remote_worker/__pycache__ ]; then
  cache_hold=$(mktemp -d /tmp/comfy-cloud-run-worker-cache.XXXXXX)
  mv remote_worker/__pycache__ "$cache_hold/remote_worker-pycache"
fi
```

Expected: `remote_worker` contains only its reviewed source files. This is workspace hygiene, not a source edit; do not add cache files to Git.

- [ ] **Step 6: Verify the complete gateway boundary**

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.python.test_worker_gateway \
  tests.python.test_worker_server.WorkerApplicationTests.test_proxy_and_worker_bindings_are_exact_and_loopback_only \
  tests.python.test_worker_release \
  tests.python.test_worker_release_tools \
  -v
```

Expected: `Ran 48 tests` and `OK`; the worker remains loopback-only, the controller-owned token reaches only Caddy, provider-owned tokens remain rejected, and deterministic release tooling includes the corrected Caddyfile.

- [ ] **Step 7: Run the authoritative repository gate**

Run:

```sh
./scripts/check.sh
```

Expected: exit `0`; Python tests, fake reusable session, deterministic worker artifact/release, Smoke/Gold validators, Node tests, compilation, syntax checks, secret scan, route scan, and provider-boundary scan all pass.

- [ ] **Step 8: Review the exact source scope**

Run:

```sh
git status --short
git diff --check
git diff -- remote_worker/Caddyfile tests/python/test_worker_gateway.py
```

Expected: implementation changes are limited to the Caddyfile and its regression test. The already-committed plan is not part of the implementation diff.

- [ ] **Step 9: Commit the verified source repair**

Run:

```sh
git add remote_worker/Caddyfile tests/python/test_worker_gateway.py
git diff --cached --check
git commit -m "fix: preserve Caddy boundary handler order"
```

Expected: one source commit containing only the two reviewed files.

---

### Task 2: Produce a truthful manual-test handoff without external mutation

**Files:**

- Inspect: `docs/superpowers/live-tests/2026-08-02-five-session-live-audit.md`
- No repository file modification is required.

**Interfaces:**

- Consumes: the committed source repair and the historical stopped-audit evidence.
- Produces: exact source commit, verification output, and an explicit statement that the installed immutable worker/template baseline remains old until separately rotated.

- [ ] **Step 1: Verify the committed tree and record the source identity**

Run:

```sh
git status --short --branch
git rev-parse HEAD
git show --stat --oneline --summary HEAD
```

Expected: clean worktree; `HEAD` is `fix: preserve Caddy boundary handler order`; its stat names only `remote_worker/Caddyfile` and `tests/python/test_worker_gateway.py`.

- [ ] **Step 2: State the publication boundary precisely**

Report all of the following without publishing anything:

- source repair committed and `scripts/check.sh` result;
- no GPU, Vast offer, instance, template, GitHub release, local release lock, or ComfyUI process was touched;
- the historical frozen archive/template still contains the broken Caddyfile;
- a manual paid test against template hash `9d6822f9429822ee9e7339a804a549da` would repeat HTTP `401` and must not be attempted;
- the next authorized publication must build a new immutable archive from the exact repair commit, publish/review a corresponding template and lock, then load it before the human starts one manual Smoke run.

- [ ] **Step 3: Give the human the minimal manual acceptance checklist**

After the human has installed a separately authorized new baseline, require these observable states in order:

```text
queue:       0 running / 0 pending before launch
inventory:   0 before launch
ready:       yes; authenticated /worker/v1/health
job:         submitted once and terminal
image:       verified as belonging to that exact job
destroy:     confirmed immediately after result or error
inventory:   exactly 0 after destruction
```

The manual result is evidence only for the newly published worker/template identifiers. Do not relabel the four failed paid attempts or the old baseline as successful.

---

## Fifteen-Minute Execution Budget

```text
0–3 min   write exact regression and observe RED
3–5 min   add route block and observe focused GREEN
5–11 min  run gateway/release tests and scripts/check.sh
11–14 min inspect the two-file diff and commit
14–15 min hand off commit, verification, and publication/manual-test preconditions
```

If `scripts/check.sh` exposes an unrelated pre-existing failure, stop at the evidence: do not broaden the patch merely to meet the clock. If the failure is caused by the two-file change, diagnose that single cause before committing.

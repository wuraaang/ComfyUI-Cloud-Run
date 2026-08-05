# Provision Transaction Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the controller accept the exact authenticated `ready` provisioning transaction before it submits the same manifest again.

**Architecture:** Add one controller-side replay guard at the start of `SessionService._apply_manifest`. It queries the signed worker transaction, validates it with the existing strict manifest-aware validator, records its final monotonic progress, and returns only when it is already `ready`; every other state keeps the existing path.

**Tech Stack:** Python 3.12/3.13, asyncio, signed Remote Worker v1 protocol, ComfyUI Desktop, Vast.ai.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/vast-template-live-audit` on `fix/vast-template-live-audit`.
- Do not retransmit models, recreate or destroy the Vast instance, change the offer, mutate the worker filesystem, or publish a worker/template.
- Preserve strict payload validation, boundary authentication, HMAC session authentication, and monotonic progress storage.
- Per the user's urgent direction, do not run a broad automated test campaign; use the active paid session as the acceptance test.
- Restart only local `/Applications/ComfyUI.app`, after source modification, so browser polling stops while the old remote request finishes.

---

### Task 1: Reuse an authenticated ready transaction

**Files:**

- Modify: `cloud_run/session_service.py:2113`

**Interfaces:**

- Consumes: `worker.transaction(transaction_id)`, `_provision_payload_valid(payload, manifest)`, and `_record_provision_progress(...)`.
- Produces: an early `dict` result from `_apply_manifest` only for the exact `ready` transaction `provision-<manifest.digest>`.

- [ ] **Step 1: Add the minimal replay guard**

Insert this at the start of `_apply_manifest`, before source URL generation or `POST /worker/v1/manifests`:

```python
        transaction = getattr(worker, "transaction", None)
        if callable(transaction):
            try:
                existing = await transaction(
                    "provision-" + manifest.digest
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except WorkerBoundaryAuthenticationError:
                raise
            except Exception:
                existing = None
            if existing is not None:
                if not _provision_payload_valid(existing, manifest):
                    raise TerminalProvisioningError(
                        "Remote provisioning response was invalid."
                    )
                if existing["state"] == "ready":
                    self._record_provision_progress(
                        existing,
                        session=session,
                        manifest=manifest,
                        transfer_job_id=transfer_job_id,
                    )
                    return existing
```

This must not accept `applying`, `awaiting_upload`, `failed`, or `stalled`, and must not swallow a boundary-authentication failure.

- [ ] **Step 2: Review the exact source diff**

Run:

```sh
git diff --check -- cloud_run/session_service.py
git diff -- cloud_run/session_service.py
```

Expected: no whitespace error; the only new behavior is the strict ready replay guard. Preserve the already-present import fallback in the same dirty file.

### Task 2: Recover the live paid session

**Files:**

- Inspect: active ComfyUI data in `ComfyUI-Installs/ComfyUI/ComfyUI/user/comfyui-cloud-run/attempts.sqlite3`.
- No remote file modification.

**Interfaces:**

- Consumes: session `fb902755-68f9-4b39-afdc-01d3fe3d3c60` and its signed worker transaction.
- Produces: local session state `ready` without another model transfer.

- [ ] **Step 1: Quit only local ComfyUI Desktop**

Resolve the current bundle and process first, then ask macOS to quit bundle ID `com.todesktop.241012ess7yxs0e`. Confirm the local backend on `127.0.0.1:8188` exits. Do not stop the Vast instance.

- [ ] **Step 2: Wait for the existing remote transaction**

Use `SessionRepository` plus `WorkerClient.transaction` from a one-off local process. Poll only `GET /worker/v1/transactions/provision-<digest>`; do not call `apply_manifest`. Continue until the exact response is `state=ready`, `phase=ready`, transferred bytes equal total bytes, and both missing lists are empty.

- [ ] **Step 3: Reopen local ComfyUI Desktop**

Run:

```sh
open -a /Applications/ComfyUI.app
```

Wait for `127.0.0.1:8188/system_stats` to return HTTP 200. The corrected custom node is loaded through the existing worktree symlink.

- [ ] **Step 4: Perform one field reconciliation**

GET `/cloud-run/api/sessions/fb902755-68f9-4b39-afdc-01d3fe3d3c60` once. Expected: HTTP 200, local `state=ready`, remote final progress `29,347,469,703 / 29,347,469,703`, no missing dependency, and no new upload/model-transfer transaction.

- [ ] **Step 5: Record evidence and hand off GPU testing**

Append the confirmed cause and recovery result to `docs/superpowers/live-tests/2026-08-02-live-relaunch-diagnostics.md`. Verify no instance SSH key remains attached. Tell the user to run the workflow only after the session response is `ready`; remind them manual-duration billing continues until explicit destruction.

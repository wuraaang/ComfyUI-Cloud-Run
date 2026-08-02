# ComfyUI Cloud Run live relaunch diagnostics

Date: 2026-08-02 (Europe/Paris)

This is a sanitized engineering log for the manually authorized live rental.
It records observed failures, evidence, impact, and possible solutions. It does
not contain API keys, bearer tokens, HMAC secrets, signed URLs, or the worker
network address.

## Current safety state

- Exactly one Vast instance is present for session
  `fb902755-68f9-4b39-afdc-01d3fe3d3c60`.
- The instance identity is `46644959`, its provider state is `running`, and its
  hourly rate is approximately `0.824444 USD/h`.
- The session uses manual duration (`deadline_mode = none`), so billing can
  continue until explicit destruction.
- The Remote Worker health boundary is authenticated and the worker is claimed.
- No provisioning transaction, remote job, or output exists yet.
- Do not start another rental while this session remains active or unresolved.

## Problems observed

### 1. First rental review expired before confirmation

**Symptom:** session `a0073f19-9296-4cbd-9b8d-802255ffa029` ended in `failed`
with `The quote expired before confirmation.`

**Evidence:** Vast inventory was empty for that session, so it caused no active
rental and no continuing billing.

**Likely solution:** search again and confirm the newly reviewed offer before
its short-lived quote expires. The current session is the successful retry and
must not be duplicated.

### 2. Session detail endpoint rejects a valid stored manifest

**Symptom:**

```text
GET /cloud-run/api/sessions/fb902755-68f9-4b39-afdc-01d3fe3d3c60
HTTP 400: Stored session manifest is unavailable.
```

**Evidence:** the manifest row exists; its stored byte length is `3527`, its
SHA-256 equals the session manifest digest, it parses successfully from the
repository checkout, and the parsed manifest digest also matches. The failure
therefore is not missing or corrupt manifest data.

**Confirmed cause:** ComfyUI Desktop loads custom nodes under an isolated
package name and does not add each custom-node root to `sys.path`. Controller
code dynamically imports `remote_worker`, while the imported worker modules in
turn import top-level `cloud_run.manifest`. Those top-level names exist when
commands/tests run from the repository root, but not when ComfyUI loads the
extension as a nested package. The real Desktop path therefore fails before
`apply_manifest` is sent.

**Impact:** the provider instance reaches `running` and authenticates, but the
controller remains in `provisioning`; no model download begins and the paid GPU
is idle.

**Attempt 1 (insufficient):** add a package-relative fallback for the two
`remote_worker` imports in `cloud_run/session_service.py`. A loader-context
probe proved that this gets one layer further but still fails because
`remote_worker/provision.py` and `remote_worker/transfers.py` import the
top-level `cloud_run.manifest` name.

**Recommended minimal solution:** make those two worker modules import
`cloud_run.manifest` in both supported contexts: sibling-relative when nested
under ComfyUI, top-level fallback in the standalone worker artifact. Re-run the
same loader-context probe, restart only the local controller, then reconcile
the existing labelled instance. Do not create another instance.

**Longer-term solution:** move the manifest decoder and wheel-artifact identity
helper into a package-neutral shared module used by both controller and worker.
This is cleaner but too broad for the live billing incident.

**Rejected workaround:** inserting the custom-node directory globally into
`sys.path`. It would hide packaging errors and can create module-name collisions
with unrelated custom nodes.

### 3. ComfyUI Desktop did not automatically respawn its backend

**Symptom:** after terminating the old backend for a code reload, Desktop did
not reopen port `8190` within 60 seconds.

**Workaround used:** start the backend with the exact command and paths that
Desktop had used. Port `8190` returned and the existing Vast session remained
the only provider instance.

**Additional warning:** the manual backend reported that `comfyui.db` was
already locked, then continued with the server. This does not explain the Cloud
Run manifest failure, but the lock owner and normal Desktop restart path should
be cleaned up after the paid session is safely terminated.

**Possible solution:** use a supported Desktop restart action or terminate and
reopen the complete Desktop application once no paid provisioning request is
in flight. Avoid running two ComfyUI backends against the same database.

### 4. Remote ComfyUI startup exceeds the normal startup window

**Symptom:** all `29,347,469,703` dependency bytes transferred and the worker
reported zero missing artifacts and zero missing class types, but the
transaction remained in `comfyui_startup`/`environment_validation` beyond the
normal 180-second ComfyUI startup timeout. Worker status requests responded
intermittently while the apply operation was busy.

**Current interpretation:** dependency transfer succeeded. The unresolved
boundary is now specifically the internal ComfyUI process or its readiness
identity check, not Vast instance creation, gateway authentication, model
mapping, or model download.

**Diagnostic limitation:** the instance exposes SSH, but the local public key
was not authorized (`Permission denied (publickey)`). The read-only diagnostic
attempt made no remote change. ComfyUI stdout/stderr is currently directed to
`DEVNULL`, so the controller cannot expose the exact remote startup exception.

**Possible solutions:**

1. Persist a bounded, sanitized tail of remote ComfyUI startup logs and return
   a typed readiness failure instead of only `Remote provisioning failed.`
2. Expose safe process exit code, last successful readiness probe, and pinned
   identity mismatch fields through the authenticated worker transaction.
3. Prevent overlapping session GET requests from launching repeated apply or
   ComfyUI-start attempts; one durable provisioning owner should run while all
   polls remain read-only.
4. Add a separately authorized diagnostic SSH key to test templates, restricted
   to manual incident inspection, or provide an equivalent safe log endpoint.
5. After obtaining the typed cause, fix that cause rather than increasing the
   180-second timeout blindly. Increase the timeout only if evidence shows a
   healthy but consistently slower cold start.

### 5. The rented instance has no attached SSH key

**Evidence:** Vast's read-only instance SSH-key endpoint returned an empty key
set. Three local Ed25519 public keys exist, including a Vast-named key, but none
is attached to this instance. The SSH failure is therefore expected and is not
a host-network failure.

**Impact:** the controller can authenticate to the bounded worker API, but an
operator cannot inspect the internal ComfyUI process during a paid incident.

**Possible solution:** with separate explicit authorization, attach the
existing `id_ed25519_vast_lds.pub` key to this one active instance, use SSH only
for read-only process/log inspection, and detach it after diagnosis. The normal
create/template path should later ensure an approved diagnostic key is attached
when manual Gold-run evidence requires SSH. Never attach a key silently.

### 6. Reconciliation replayed an already-completed provision transaction

**Confirmed SSH evidence:** remote ComfyUI was already running on loopback port
`8188`, `/system_stats` returned HTTP 200, the RTX 4090 was visible, and the
persisted installed record contained all six validated artifacts and all 23
required class types. There was no OOM, GPU fault, missing model, or missing
node.

The first provision operation reached readiness, but its controller request
expired at the HTTP boundary. Every later session refresh submitted the same
manifest again. The worker reset that retry's in-memory progress to zero while
the controller correctly retained the monotonic final value
`29,347,469,703 / 29,347,469,703`. The resulting progress regression was
reported as `Remote provisioning response was invalid.` Repeated browser
polling then queued additional full validations.

**Implemented controller repair:** `SessionService._apply_manifest` now asks
for the exact signed `provision-<manifest digest>` transaction before posting
the manifest. It reuses the response only when the existing strict validator
accepts every field and the state and progress phase are both `ready`.
Authentication failures and every non-ready state retain their prior handling;
monotonic storage is not weakened.

### 7. The immutable on-start script is not reboot-safe

**Symptom after the authorized same-instance reboot:** Vast returned the
instance to `running`, preserved all data and port mappings, but the worker
gateway did not listen. Container logs showed:

```text
mkdir: cannot create directory '/opt/comfyui-cloud-run-bootstrap': File exists
```

`/root/onstart.sh` uses `mkdir -m 0700` for that fixed bootstrap directory and
therefore exits on every container reboot after the initial launch. The worker
archive and release lock were still present and unchanged.

**Live recovery:** the already-installed worker identity and release lock were
validated, then the existing reviewed gateway was launched with the original
PID 1 environment plus the pinned worker version and ComfyUI root. No remote
source, model, manifest, state file, or inventory record was edited. One signed
manifest apply returned `ready` in under 30 seconds.

**Durable solution:** make the generated on-start script reboot-idempotent. On
an existing bootstrap/install directory it must validate the immutable release
lock and installed layout, then exec the reviewed gateway; it must neither
blindly overwrite the install nor fail merely because the directory exists.

### 8. Successful readiness recovery

The same paid instance `46644959` now reports the following verified state:

```text
worker claim:       true
remote transaction: ready / ready
model bytes:        29,347,469,703 / 29,347,469,703
missing artifacts:  0
missing classes:    0
remote ComfyUI:      HTTP 200 on 127.0.0.1:8188
GPU:                 NVIDIA GeForce RTX 4090
local session:       ready
deadline mode:       none (manual destruction)
```

The local session reconciliation reused the ready transaction and did not
start another model transfer. Billing continues until explicit destruction.

## Expected user flow after repair

1. The existing worker receives the immutable preflight manifest.
2. It downloads or uploads the real model/input/custom-node artifacts and
   verifies exact size and SHA-256; nothing is faked.
3. It starts remote ComfyUI and validates `/object_info`.
4. The local session becomes `ready`.
5. The Cloud Run panel reveals **Run current canvas on this GPU**.
6. Clicking that button captures the current canvas without local execution
   and submits one remote job.
7. Billing continues because duration is manual until the user explicitly uses
   the reviewed destroy flow.

## Launch-speed improvement backlog

The live run separates controller defects from unavoidable cold-start work.
The controller/import/restart defects are local bugs and should add no launch
time after repair. The remaining dominant cost is transferring approximately
`27.3 GB` into an ephemeral pod. The observed rate was initially around
`25–30 MB/s`, so a double-digit-minute cold start is expected even with a fast
GPU.

Potential improvements, in descending expected impact:

1. Reuse one healthy paid session for sequential jobs so unchanged models are
   not transferred again.
2. Add a content-addressed persistent cache or provider volume near the worker;
   transfer only a verified delta into each session.
3. Rank offers on startup fitness as well as GPU compute: measured download,
   disk bandwidth, bandwidth price, reliability, and an estimated dependency
   transfer time should be visible before rental confirmation.
4. Offer an optional prewarmed image for a small curated model set. This trades
   image maintenance/storage cost for predictable startup.
5. Reduce dependency bytes where the workflow permits it (quantized/smaller
   exact model variants), without silently changing model identity.
6. Preserve bounded parallel downloads and instrument per-source throughput to
   distinguish provider network, source throttling, and disk bottlenecks.
7. Consider a more uniform cloud provider for users who prefer predictable
   startup over Vast's lower but heterogeneous marketplace pricing.

Vast itself is not the sole cause: marketplace hosts vary, and a selected
offer's network/disk can be mediocre independently of its GPU. However, no
provider can make a fresh ephemeral machine consume `27.3 GB` instantly. Cache,
session reuse, and network-aware offer ranking are the primary remedies.

## Next evidence to record

- loader-context import probe succeeds;
- session endpoint no longer returns the stored-manifest error;
- provisioning transaction shows real byte progress;
- session reaches `ready` and exposes the run button;
- one explicitly requested job reaches a terminal state;
- user reviews destruction, and complete Vast inventory returns exactly zero.

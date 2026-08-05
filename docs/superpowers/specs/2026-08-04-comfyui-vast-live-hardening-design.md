# ComfyUI Vast Live Hardening Design

Status: approved in conversation on 2026-08-04.

This specification authorizes documentation and later local implementation
only. It does not authorize a Vast create, worker publication, template
mutation, external Desktop installation, Desktop restart, or paid field test.
Those remain separate approval gates.

## Objective

Make the existing `Cloud Vast` controller reliably prepare one official
ComfyUI Desktop Remote Connection named `ComfyUI Vast`, with a certified
baseline of the user's approved Desktop extensions, while preserving manual
GPU lifetime control and fail-closed security.

The completed change must eliminate every deterministic defect observed in the
2026-08-04 paid campaign, make future failures typed and visible, and preserve
an independently verifiable path to stop Vast billing. It does not promise
that software can never encounter another error. It guarantees that readiness
is not claimed without end-to-end evidence and that a future error cannot hide
the instance or disable reviewed manual destruction.

## Evidence and confirmed gaps

The paid campaign proved that Vast create, worker authentication, immutable
release identity, ComfyUI process health, the complete 29,347,486,804-byte
transfer, model/input digests, required object classes, and profile transport
all succeeded. The session failed after provisioning and before Desktop relay
activation.

The following defects are confirmed from the frozen source and durable local
evidence:

1. Desktop readiness sends `GET /system_stats`, while `NativeRoutePolicy`
   rejects that exact route. The signed request is rejected locally before it
   reaches the pod.
2. The pod-side native WebSocket transport passes `max_redirects=0` to
   `aiohttp.ClientSession.ws_connect`, whose supported API does not accept
   that keyword. The exception is sanitized into a generic WebSocket failure.
3. A failed readiness report is stored under the same unique identity as a
   successful report would use. Subsequent recovery reuses the failure rather
   than making a bounded fresh attempt.
4. Runtime profile capture calls `DesktopProfileStore.capture` without
   approved UI package or UI asset inputs. The live profile therefore stored
   `ui_packages=[]`, treated Agent Panel as `not_required`, and omitted both
   Agent Panel and Hermes Nous assets.
5. The configured background is a safe same-origin `/api/view` URL. Profile
   capture copies only absolute filesystem image paths, so it retained the URL
   but did not include the referenced image.
6. A destroy review is bound to the entire volatile session version. Normal
   validating/provisioning progress invalidates the review between the two
   explicit user actions. Destruction also attempts profile synchronization
   before persisting a provider teardown result.
7. Settings payload assembly converts any active-session rendering error into
   `active_sessions=[]`, which can hide a still-billable session from the UI.
8. The provisioning display reports `validated_units=0` until the entire
   session becomes ready and continues a provisioning stall clock after the
   provision transaction is already ready. This made a Desktop-link failure
   look like a model-transfer failure.

The campaign did not reach a native prompt, Agent Panel batch, preview,
history, output harvest, reconnect, or second-job test. Those paths remain
unproved on a real pod.

## Product invariants

- `Cloud Vast` remains the lifecycle controller in the normal local Desktop
  environment.
- `ComfyUI Vast` remains an independent official Desktop Remote Connection,
  never a separate browser application.
- No provider create occurs without the existing fresh reviewed paid action.
- Manual/no-limit mode never destroys a pod merely because readiness,
  execution, or recovery failed.
- One reviewed manual destruction remains available in every state where
  billing may continue.
- Billing is reported stopped only after fresh complete Vast inventory proves
  both the managed instance ID and label absent.
- The local Desktop environment never submits a remote-role prompt to local
  `/prompt` or local GPU execution.
- Provider secrets, session capabilities, raw signed URLs, private workflow
  content, and raw worker errors never enter public payloads, logs, tests, or
  committed evidence.
- A package, model, profile, or worker identity change invalidates the free
  review before rental; there is no mutable fallback.

## Selected architecture: certified Desktop baseline

Every `ComfyUI Vast` session includes one immutable baseline in addition to
workflow-derived executable dependencies:

1. `comfyui-agent-panel` release `0.11.38` as an approved UI package;
2. `comfyui-hermes-nous-theme` as a content-addressed approved UI package;
3. `efficiency-nodes-comfyui` release `1.0.9` as an executable custom-node
   package.

Agent Panel and Hermes Nous are packaged from explicit safe entrypoints and
asset roots. Their archives are pinned by source identity, revision, size, and
SHA-256. Agent Panel's actual orchestrator stays on the Mac; the pod receives
only the approved frontend required for the scoped same-origin bridge. Its
general training, CivitAI proxy, Apps, manager, process, filesystem, and
arbitrary backend routes remain unreachable through the native proxy.

Efficiency Nodes is not copied from the mutable local directory. Preflight
resolves an immutable release/archive and an exact offline wheel set. Its
`clip-interrogator` and `simpleeval` closure must not replace the pinned
ComfyUI, frontend, Python, Torch, aiohttp, or worker dependencies. Package
metadata does not authorize example URLs or unsolicited model downloads.
Readiness validates the expected `NODE_CLASS_MAPPINGS` set and the pinned
frontend assets even when the current workflow does not reference those
classes.

Other executable custom nodes remain workflow-derived. A non-core class in the
current canvas must resolve to one immutable package and expected class set;
arbitrary local custom-node directories are never mirrored automatically.

The exact baseline archive digests and dependency closure become part of free
preflight, the visible quote digest, durable intent, manifest, readiness
identity, and immutable worker preparation. Any mismatch stops before paid
confirmation.

## Safe profile capture

Runtime profile discovery explicitly supplies the certified UI package specs
to `DesktopProfileStore.capture`. It no longer relies on empty defaults.

Loose profile UI assets remain limited to inert images and styles. Executable
JavaScript and SVG belonging to Agent Panel or Hermes Nous travel only inside
their separately identified, content-addressed UI package archives. This
prevents a workflow, settings file, or arbitrary local directory from
injecting executable frontend code under the generic profile allowlist.

Background capture supports both existing absolute input paths and the actual
same-origin ComfyUI form:

```text
/api/view?filename=<safe-relative-file>&type=input[&subfolder=<safe-relative-folder>]
```

The parser rejects duplicate parameters, unexpected parameters, encoded path
traversal, absolute paths, non-input types, unsupported image suffixes,
symlinks, non-owned files, and files outside the configured input root. It
copies the verified image content-addressably and rewrites only the remote
settings copy to `input/cloud-vast/backgrounds/<sha256>.<suffix>`. The local
setting remains unchanged.

The Hermes theme compatibility contract is tested against the actually pinned
frontend `1.47.10`. If the exact approved theme cannot pass against that
frontend, preflight refuses the certified baseline before rental; the worker
frontend is not silently upgraded as part of this repair.

## Native transport and readiness

### HTTP

`NativeRoutePolicy` permits exactly `GET /system_stats` with no query, body,
or forwarded identity. Every other existing fail-closed path rule remains
unchanged. A cross-layer test constructs the readiness envelope through the
real `WorkerClient`, passes it through the real worker policy, and reaches a
loopback native ComfyUI handler. Tests must prove POST, query, redirect,
encoded traversal, and credential forwarding remain rejected.

### WebSocket

The pod-side native transport removes the unsupported `max_redirects`
argument. It uses the same request-level redirect guard as the controller-side
transport so a WebSocket handshake cannot move to another origin or loopback
path. A real aiohttp test proves a normal `/ws?clientId=<id>` handshake opens
and closes, while a redirect is rejected before any signed or forwarded
header can move.

### Readiness attempts

Successful readiness reports remain immutable and reusable only for their
exact session, instance, worker release, manifest, profile revision, relay
origin, and inventory observation. Failed attempts remain append-only
diagnostic evidence but do not occupy the successful identity forever.

After provisioning becomes ready, the controller makes at most six readiness
attempts over a 60-second readiness window. A fresh attempt rechecks current
provider identity and every readiness item. A success commits once and
activates the relay. Exhaustion creates one terminal, typed readiness failure;
manual/no-limit mode retains the instance for reviewed user destruction.

Public diagnostics expose only bounded codes and check names such as
`native_route_rejected`, `native_http_status`, `native_websocket_handshake`,
`profile_package_mismatch`, or `agent_bridge_unavailable`. Raw URLs, headers,
frames, exceptions, and credentials remain private.

## Priority manual destruction lane

The two-step irreversible review and explicit data-loss acknowledgement remain
mandatory. The review token binds to a dedicated destruction snapshot rather
than the volatile whole-session version. The snapshot includes session ID,
managed label, instance ID, residual instance IDs, active-job identity,
unverified-output identities, and the latest safe profile revision.

Progress-only updates do not invalidate the token. An instance change, a new
unverified output, a different active job, a changed managed label, expiry, or
prior consumption invalidates it.

On final confirmation the controller atomically persists
`destroy_requested=true` before any remote network work. Every provisioning,
readiness, job, and recovery loop observes that durable intent before its next
network operation and yields to teardown. Agent capabilities are revoked
immediately. A best-effort final profile snapshot has a strict five-second
budget and can never prevent provider teardown after the user acknowledged
data loss.

The provider destroy targets the exact reviewed instance, then obtains fresh
complete inventory. Absence finalizes `destroyed`, clears the instance and
session secrets, abandons incomplete transfers/jobs, and sets
`billing_may_continue=false`. Unavailable inventory or any residual matching
ID/label retains the emergency warning and exact nonsecret instance identity.

If the user destroys the instance independently through Vast or the CLI, the
next read-only reconciliation recognizes fresh verified absence and performs
the same local finalization without attempting a second create or inventing a
successful provider response.

## Honest UI and observability

The lifecycle panel separates these phases and clocks:

- provider create/start;
- worker authentication;
- artifact transfer;
- package/profile installation;
- native Desktop readiness;
- active execution and output harvest.

Once the provision transaction is ready, its stall timer stops. Installed
artifacts are not presented as unvalidated merely because Desktop readiness is
pending. The UI shows the failed readiness check and retry count directly.

An active-session rendering failure no longer becomes an empty list. Settings
returns a bounded `active_sessions_error` while preserving a minimal durable
session safety card containing only session ID, instance ID, state,
`billing_may_continue`, `can_destroy`, rate, and sanitized error. The destroy
control remains enabled from durable state even if detailed polling, profile
sync, or readiness rendering fails.

The Vast CLI remains an operator-side independent observer and emergency tool,
not a product runtime dependency. Normal users still use the extension. CLI
inventory, state, logs, and destruction can corroborate or recover the system
without changing the controller's paid-action gate.

## Test strategy

Strict TDD applies to each defect: every production change begins with a
targeted test that fails for the observed reason.

The automated gate includes:

1. route-policy contract tests for `/system_stats` and all adjacent rejected
   variants;
2. real aiohttp HTTP and WebSocket loopback tests, including redirect refusal;
3. append-only failed readiness attempts, bounded retry, success caching, and
   restart recovery tests;
4. concurrent progress versus destroy-review tests proving progress cannot
   invalidate the review and final confirmation preempts worker activity;
5. failed/unavailable provider destroy tests proving residual billing remains
   visible;
6. external verified-absence reconciliation tests;
7. production runtime profile-discovery tests that start from the three actual
   approved package identities instead of injecting a fake CSS-only package;
8. Agent Panel frontend/bridge capability tests with forbidden backend paths;
9. Hermes settings, palette, package assets, and `/api/view` background
   round-trip tests;
10. Efficiency Nodes import, expected object classes, frontend hook loading,
    dependency-lock, and no-core-package-drift tests;
11. UI tests for phase-specific progress, active-session fallback, readiness
    diagnostics, and destruction while detailed polling is unavailable;
12. the full deterministic `scripts/check.sh` repository gate.

The offline end-to-end campaign must use the real route policy and real
transport adapters. A fake worker may supply a loopback ComfyUI backend, but it
may not bypass envelope classification, HTTP transport, WebSocket transport,
profile discovery, or the destroy-review repository contract.

## Delivery and paid acceptance gates

After repository tests pass, external installation into ComfyUI Desktop is a
separate Hermes-owned action. It requires a fresh local restart/reconnect
check showing the controller still starts, the local environment remains
usable, and the certified baseline is visible in free preflight.

Worker publication and private Vast template replacement are separate explicit
approvals. They produce a new immutable worker commit, archive digest, release,
and template ID; the current published release and old templates remain
unchanged.

A later paid acceptance requires a new human GO with one-instance, hourly-rate,
duration, and total-cost bounds. It must prove on the same instance:

1. fresh pre-inventory zero;
2. complete cold transfer and certified baseline installation;
3. HTTP, WebSocket, profile, background, Hermes, and Agent Panel readiness;
4. official `ComfyUI Vast` Remote Connection opening inside Desktop;
5. native Run and one Agent Panel batch on the pod GPU with local GPU unused;
6. native progress, preview, history, error, and verified output behavior;
7. reconnect without duplicate prompt or output;
8. manual reviewed destruction from a live/validating-capable UI;
9. fresh post-inventory zero and durable
   `billing_may_continue=false`.

No hot patch, replacement pod, second create, automatic terminal destroy in
manual mode, or expanded spend is permitted during that acceptance.

## Completion criteria

The implementation is complete only when all targeted red/green tests and the
full repository gate pass, the installed local build passes its separate
restart/reconnect gate, and no known deterministic blocker remains. The
product is live-certified only after the separately authorized paid acceptance
passes every criterion on one immutable release and ends with independently
verified zero inventory.

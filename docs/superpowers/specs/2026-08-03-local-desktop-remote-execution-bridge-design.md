# Local Desktop / Remote Execution Bridge Design

Status: the product design was approved during brainstorming on 2026-08-03.
This written specification still requires explicit approval before an
implementation plan may be written. It authorizes documentation only: it does
not authorize implementation, a Vast mutation, a template mutation, or a paid
live test.

## Product decision

`ComfyUI-Cloud-Run` will provide a `Cloud Vast` mode that makes a temporary
Vast.ai pod feel like the GPU backend of ComfyUI Desktop without moving the
user's normal desktop experience into a separate browser application.

The selected architecture uses ComfyUI Desktop's official support for
independent managed environments and Remote Connections. The existing local
Desktop environment remains the controller and source of truth. Once a user
has explicitly rented and prepared a pod, they launch an independent managed
environment named `ComfyUI Vast` beside the local one. It stays inside the
ComfyUI Desktop experience and is never opened as a Brave, Chrome, or other
browser tab. It is a functional, sanitized copy of the user's ComfyUI
environment:

- the current canvas and saved workflows are available;
- required executable custom nodes and models come from the pod;
- approved visual extensions, settings, palettes, and background image are
  mirrored from the Mac;
- Agent Panel remains usable through a narrowly scoped local bridge;
- the normal node menus, model selectors, progress, previews, history, errors,
  and outputs are the native ComfyUI experience;
- every prompt submitted in that environment executes on the pod GPU, never
  on the Mac GPU.

There is no special `Run Vast` action. Once readiness is proven, the ordinary
native Run, queue, and batch controls in `ComfyUI Vast` use the pod backend.
The same native controls in the local environment remain unchanged and use the
local backend. `Cloud Vast` is only the lifecycle control used to analyze,
rent, prepare, open, monitor, and destroy the remote environment.

Whether Desktop implements the two environments as two windows in one process
or as separate operating-system processes and Dock entries is not a product
requirement. The user contract is two independently usable ComfyUI Desktop
surfaces side by side, with no external browser and no separately packaged
Cloud Vast application.

The copy is functional rather than byte-for-byte. Databases, long-lived
credentials, account tokens, caches, absolute Mac paths, and unrelated user
data are never copied as part of the profile. Only an explicit, versioned
profile allowlist and the dependencies needed by the selected workflows are
mirrored. A gated artifact may separately use a narrowly scoped, short-lived
download capability under the artifact-source contract below.

The names are intentionally distinct:

- `Cloud Vast` is the lifecycle control in the local environment;
- `ComfyUI Vast` is the independent Desktop environment attached to the pod;
- `Run`, queue shortcuts, and batch actions are the unchanged native execution
  controls inside either environment.

## User outcome

The intended flow is:

1. The user builds or opens a workflow in the normal local ComfyUI Desktop
   environment.
2. The user opens the renamed `Cloud Vast` control.
3. A free, read-only preflight analyzes the exact current canvas, its nodes,
   models, inputs, UI profile, compatible offers, and expected preparation
   time. It compiles the canvas through the native frontend preparation path
   but never submits the prompt to local `/prompt` and never rents a GPU.
4. The user chooses an offer from a short explained list and sees the exact
   current quote.
5. The user performs one explicit paid action, `Louer et préparer`.
6. The controller persists the intent before creating anything, revalidates
   the quote, creates at most one Vast instance, provisions it, mirrors the
   safe profile, and verifies the resulting environment.
7. When all readiness checks pass, the independent `ComfyUI Vast` environment
   can be opened inside ComfyUI Desktop beside the local environment.
8. The user edits, runs, queues, or launches a batch with the ordinary native
   controls. Nodes illuminate, samplers advance, previews appear, native
   errors are visible, and completed images appear in the normal interface.
9. Persistent outputs are also downloaded, verified, and saved on the Mac.
   Workflow and safe settings changes are synchronized back to a versioned
   local copy.
10. The same healthy pod can run several sequential jobs. The user explicitly
    destroys it, or an optional duration chosen before rental triggers the
    already-authorized deadline teardown.
11. The product claims that billing ended only after a fresh Vast inventory
    proves that the instance no longer exists.

Normal use must require no Codex session, operator intervention, shell command,
manual model copy, or hand-edited path.

## Product principles

### Local-first, not SaaS

The product remains one `ComfyUI-Cloud-Run` custom-node package containing a
ComfyUI backend extension and web extension. It adds no graph node, separately
packaged desktop application, hosted frontend, or SaaS control plane.

Each user supplies their own Vast API key and, when relevant, their own
Hugging Face, Civitai, or R2 configuration. The project does not operate a
shared model bucket or hold users' provider credentials.

### Explicit money boundary

Offer discovery, dependency analysis, inventory reads, hashing, and local
diagnostics are non-rental operations. No Vast instance may be created or
mutated until the user confirms a fresh quote through the single paid action.
Price limits are hard limits and are never exceeded silently.

Development and field testing have an additional safety boundary: no create,
destroy, template change, or other Vast mutation may occur without a new,
explicit human `GO`, even if a test or previous session was already approved.

### Native ComfyUI behavior

The `ComfyUI Vast` environment uses the official Desktop Remote Connection
path and the native ComfyUI frontend against the remote backend. It does not
reproduce the canvas in a custom dashboard. The remote backend is therefore
the authority for node definitions, model lists, queue state, history,
previews, and native execution events.

### Reproducible dependencies

An environment is ready only when immutable release and artifact identities
have been validated. A filename, mutable branch, or successful download alone
is not proof that a dependency matches.

### Durable recovery

Browser closure, a local backend restart, a WebSocket interruption, or a
controller HTTP timeout must not lose a paid session, duplicate a prompt, or
turn a completed GPU execution into a failure. Persisted provider inventory,
worker state, and local SQLite state are reconciled rather than inferred from
one transient request.

## Architecture options considered

### Local canvas with event reinjection

The first recommendation kept the user in the original local canvas, captured
its compiled prompt, submitted it remotely, and translated remote events back
into local frontend state. It preserves one window, but it also requires this
extension to reproduce every native queue, model-list, object-info, preview,
history, and canvas event behavior. The original local backend would still be
the authority for node menus and models, so it could not honestly look like
the provisioned pod without a much broader emulation layer.

This remains useful as a possible lightweight headless mode, but it is not the
selected “like local” experience.

### Official Desktop managed environment

This is the selected design. It lets the real remote backend drive the real
ComfyUI frontend while keeping the experience in ComfyUI Desktop. Desktop's
independent-environment model lets the local and Vast environments remain open
side by side. A safe profile mirror supplies the user's canvas and appearance,
and a loopback relay keeps remote authentication out of the renderer.

The tradeoff is one additional managed Desktop environment and a one-time
official Remote Connection setup. It is accepted because node definitions,
model selectors, Agent Panel, batches, previews, progress, history, and
outputs then behave natively.

### Direct public URL or transparent local-origin proxy

A direct pod URL would require a browser-compatible authentication mechanism
and risks persisting a bearer token or signed URL in Desktop configuration. A
transparent proxy mixed into the original local ComfyUI origin would have to
decide whether every root API and WebSocket belongs to the local or remote
backend, creating route, authentication, and lifecycle ambiguity.

Both are rejected. The scoped loopback origin is dedicated to one remote
session and never mixes local and remote ComfyUI APIs.

## Scope and non-goals

The first version includes:

- `Cloud Run` renamed to `Cloud Vast` in the local interface;
- one official Desktop managed Remote Connection named `ComfyUI Vast`;
- a local loopback data-plane relay with no browser-visible remote secret;
- current-canvas bootstrap and safe profile mirroring;
- deterministic custom-node, model, and input provisioning;
- optional per-user R2 acceleration;
- native remote execution and durable local reconciliation;
- verified persistent output download;
- typed, sanitized error reporting and an exhaustive run journal;
- manual session reuse and explicitly authorized deadline teardown.

The first version does not include:

- a separately packaged web or desktop application;
- TanStack Start, a hosted portal, or a multi-user service;
- Convex as a runtime dependency;
- automatic selection of a single GPU without user review;
- support for arbitrary unpinned or non-reproducible custom nodes;
- copying the complete ComfyUI user directory or Desktop database;
- treating a Vast volume as a portable universal cache;
- exposing a CUDA device to the local operating system;
- modifying ComfyUI Desktop's private installation registry;
- an autonomous runtime agent allowed to repair code or spend money.

Compatibility automation for upstream ComfyUI changes is a separate follow-up
slice described near the end of this document.

## Meaning of “like local”

The `ComfyUI Vast` environment must provide the same user-facing capabilities
that depend on the active ComfyUI backend:

| Capability | Source in `ComfyUI Vast` |
| --- | --- |
| Canvas and current workflow | Mirrored local workflow, then the active Vast environment |
| Saved workflows | Versioned safe profile synchronization |
| Node types and node metadata | Pod `/object_info` and pinned custom nodes |
| Model dropdowns | Pod model directories and native ComfyUI APIs |
| Theme, palette, layout settings | Allowlisted settings mirror |
| Background photo | Mirrored input asset plus remote-only path rewrite |
| Agent Panel UI | Approved UI extension plus scoped Mac bridge |
| Queue, active node, sampler progress | Native remote ComfyUI events |
| Previews | Native temporary output events and view APIs |
| History and errors | Native remote history plus durable worker snapshot |
| Final images and video | Native display plus verified Mac download |

This contract does not mean that the original local environment dynamically
gains the pod's node definitions or remote model lists. Those belong to the
independent `ComfyUI Vast` environment inside Desktop. The local environment
continues to control rental, lifecycle, costs, and recovery.

## Architecture

The system has five cooperating boundaries:

1. the local ComfyUI Desktop environment and `Cloud Vast` controller extension;
2. the local ComfyUI backend and its SQLite control state;
3. a loopback-only session relay used by the official Remote Connection;
4. an authenticated, persistent worker gateway on the Vast pod;
5. the pod's pinned ComfyUI backend and GPU.

The request path is:

```text
Local Desktop controller
  -> free manifest and offer analysis
  -> explicit paid confirmation
  -> Vast lifecycle + pod provisioning

ComfyUI Vast Desktop environment
  -> 127.0.0.1 session relay
  -> authenticated worker gateway
  -> pod ComfyUI
  -> pod GPU

Worker snapshots + outputs
  -> local controller reconciliation
  -> local SQLite + verified Mac files
```

All controller HTTP routes remain under `/cloud-run/api/`. The official
Remote Connection needs a ComfyUI origin with native root paths such as
`/ws`, `/prompt`, `/history`, and `/view`, so the data-plane relay binds a
separate loopback origin and exposes only the scoped ComfyUI surface for the
active session. It is not a second product backend and has no provider
administration endpoints.

This is intentionally different from proxying local and remote APIs through
the ordinary local ComfyUI origin. The remote connection receives its own
origin, one active pod target, one session identity, and one explicit lifetime;
local and remote routes cannot be mixed accidentally.

## Desktop integration

### Official Remote Connection

ComfyUI Desktop already manages multiple independent ComfyUI environments and
supports an editable remote URL as one source type. It opens the selected
source as a Desktop application surface. The current official implementation
uses window launch mode, a shared browser partition, and automatic output
download support. Its content script observes remote execution and skips
temporary output descriptors during automatic download.

The extension does not rely on Desktop's private JSON installation registry.
The first version has one honest, one-time setup step: the user creates an
official managed Remote Connection named `ComfyUI Vast` and points it at the
stable loopback relay URL shown by the local controller. That environment
persists beside the user's local environment across sessions. Each future
rental reuses it; the relay changes its authenticated upstream only after a
session is ready.

On first setup the controller selects and persists one available loopback port.
It binds that same port on later launches so the saved Desktop connection stays
valid. If another process owns the port, it fails readiness with a precise
local-port error; it never silently changes the URL or falls back to a
non-loopback address.

If ComfyUI Desktop later exposes a supported deep link or public extension API
for creating and opening Remote Connections, the same contract can remove the
one-time setup without changing the architecture.

### Desktop lifecycle

The local controller uses Desktop's official backend lifecycle. It never
starts a second local ComfyUI backend against the same `comfyui.db`. Opening a
Remote Connection launches the independent remote Desktop environment and does
not require a second local backend.

Closing either Desktop surface must not destroy the paid instance or lose its
state. Reopening the local controller or `ComfyUI Vast` environment reconnects
to the persisted session. Only an explicit destroy request or a previously
authorized deadline controls teardown.

The first version binds exactly one `ComfyUI Vast` environment to at most one
active paid session. Desktop's ability to manage more environments does not
authorize multiple simultaneous pods or rentals.

### Native Run, queue, and batches

The mirrored web extension detects the signed remote-session role. In that
role it does not display rental controls, relabel Run, or create another
instance. The ordinary native Run button, queue shortcuts, and Agent Panel
batch actions invoke the frontend's normal preparation and submission path for
the active Vast canvas.

The relay observes every `/prompt` submission from that environment, including
native keyboard shortcuts and batch entries. Before forwarding each distinct
prompt it persists a client job identity, then binds the returned remote prompt
ID to the durable local intent and the worker's persistent record. A retry
reconciles that identity rather than blindly enqueueing twice.

The submitted prompt is compared with the session's validated dependency
manifest before it reaches the remote queue. A prompt already covered by the
manifest proceeds immediately. A compatible new model, input, or pinned node
with an approved source enters visible delta provisioning and the same durable
job intent is submitted automatically after validation. An unresolved,
untrusted, or runtime-incompatible dependency is rejected with a precise
preflight error and cannot bypass readiness. This applies equally to Agent
Panel edits and every prompt in a batch.

All prompt traffic in the `ComfyUI Vast` origin necessarily reaches the pod.
There is no route from that origin to local `/prompt`. A ComfyUI batch becomes
an ordered set of durable remote jobs and executes sequentially under the
session's existing one-at-a-time GPU rule.

## Canvas and profile synchronization

### Free canvas capture

Preflight invokes the installed local frontend's native graph-to-prompt
preparation without sending its result to the local queue. Capture includes
the full UI workflow and the executable API prompt while preserving the
frontend's handling of asynchronous widget serialization, promoted widgets,
virtual-node transforms, subgraphs, bypassed and muted nodes, queue options,
and `beforeQueued` behavior.

The extension does not reconstruct node inputs independently, depend on a
manually exported API-format file, or ask an AI model to translate the graph.
It computes a canonical digest over the captured prompt, workflow, and
dependency-relevant queue options. Immediately before paid confirmation it
captures again; a changed dependency manifest invalidates the quote and
returns to free analysis.

This local capture is for dependency resolution and the bootstrap revision. A
ready `ComfyUI Vast` environment recompiles its current canvas through native
Run, so later remote edits and seed behavior are authoritative for execution.

### Initial profile snapshot

Before rental, the local extension captures the current UI workflow using the
installed frontend's native serialization semantics. It also builds a safe
profile snapshot from an explicit allowlist:

- saved workflow JSON files;
- the current unsaved UI workflow as the session bootstrap workflow;
- safe ComfyUI frontend settings and color palettes;
- approved UI-only custom-node web assets pinned by release and digest;
- the selected background image and other referenced safe UI assets.

It explicitly excludes:

- `comfyui.db` and other databases;
- API keys, credentials, cookies, tokens, and signed URLs;
- browser storage copied wholesale;
- logs, caches, virtual environments, and Python bytecode;
- absolute Mac filesystem paths;
- unrelated user files and unapproved web extensions.

The snapshot is versioned and content-addressed. The Mac copy remains the
source of truth for durable user data.

### Current canvas bootstrap

The exact current canvas, including unsaved changes, is stored as a session
bootstrap revision. On the first open of the ready `ComfyUI Vast` environment,
the mirrored extension loads that revision through the supported frontend
graph loading path. It never overwrites a remote canvas that already has a
newer user edit.

The executable prompt is compiled only in the environment that will execute
it.
Consequently native widget serialization, seed changes, virtual-node
transforms, bypass state, subgraphs, and frontend queue behavior match the
installed remote frontend at the moment the user presses native Run or starts
a batch.

### Saved workflow and settings return path

Remote workflow saves and allowlisted setting changes receive monotonically
versioned revisions. The controller mirrors them to the Mac during the
session, on refresh, after restart, and once more before an authorized
teardown. Atomic rename prevents a partial file from replacing a valid local
copy.

When both local and remote copies changed from the same base revision, neither
silently wins. Both versions are preserved with clear local and Cloud Vast
labels, and the user chooses which version becomes current.

### Path normalization

The manifest records logical ComfyUI categories and normalized relative paths,
not absolute machine paths. A local reference such as a model under a Mac
`models/checkpoints` directory maps to the corresponding standard relative
path on the pod.

The currently configured background image is an absolute Mac path. The local
value remains unchanged. The image is copied into an allowlisted remote input
directory, and only the remote settings copy is rewritten to a normal ComfyUI
`/api/view` reference with `type=input`.

Users continue to see familiar filenames and categories. Path translation is
automatic and a filename collision with a different digest is a validation
error, not an implicit overwrite.

## Custom nodes and UI extensions

### Executable graph nodes

Every non-core class type in the workflow must resolve before rental to one
approved package with:

- an immutable repository revision or registry release;
- a compatible ComfyUI core and frontend range;
- a deterministic Python dependency installation contract;
- a package digest or release lock;
- an explicit expected set of node class types.

The pod installs and validates these packages before readiness. The final
`/object_info` response must expose every required class type. Arbitrary shell
instructions from workflow metadata, Agent Panel, or a model page are not an
installation plan.

If a custom node cannot be pinned, installs interactively, requires an
unsupported system service, or violates the security boundary, preflight marks
the workflow unsupported before the paid confirmation.

### UI-only profile

Prompt inspection cannot discover extensions that add no graph class. The
profile therefore maintains a separate user-approved list of UI-only packages
and their immutable web-asset digests.

Visual extensions are loaded only from that list. Their assets are supplied
by the trusted session package or verified against the profile digest. A pod
cannot replace an approved extension with different JavaScript and still pass
readiness.

### Agent Panel

The installed `comfyui-agent-panel` package is UI-only: it contributes web
assets and no executable graph node. The same approved release is present in
the `ComfyUI Vast` UI profile so the panel looks and behaves as it does locally.

Its actual agent/orchestrator remains on the Mac. The `ComfyUI Vast`
environment reaches it through a per-session bridge that:

- binds only to loopback;
- uses an unguessable, expiring session capability;
- accepts only the Agent Panel methods required by the approved integration;
- pins the target ComfyUI origin to the active Cloud Vast relay;
- rejects arbitrary local hosts, ports, paths, and filesystem access;
- never sends a long-lived local credential to pod code;
- expires when the session is destroyed or revoked.

Remote ComfyUI events remain native, so Agent Panel can observe the active
remote canvas, modify or improve its workflow, submit a normal run, and launch
a batch. When Agent Panel is present in the source profile, these capabilities
are mandatory readiness checks. If the scoped bridge cannot be established
safely, `ComfyUI Vast` remains not ready and reports the exact safe failure;
the system does not weaken loopback or credential security to make it appear
remotely.

## Dependency manifest and artifact sources

The preflight produces a content-addressed manifest containing:

- pinned ComfyUI core, frontend, worker, and extension releases;
- executable node packages and expected class types;
- approved UI-only packages and web-asset digests;
- models with category, relative path, byte size, and SHA-256;
- input media with relative path, byte size, and SHA-256;
- safe profile snapshot identity;
- disk requirement, hard minimum VRAM, and estimated transfer work;
- opaque local handles for optional credentials, never secret values.

Artifact identity is the digest, independent of transport. A record may offer
several source candidates in a deterministic preference order.

### Universal baseline

The baseline works without R2. Public or user-authorized Hugging Face and
Civitai artifacts resolve to immutable revisions or versions and the pod
downloads them directly. Gated sources use short-lived, session-scoped access
material supplied through the worker boundary and never persisted in logs or
returned to the browser.

Local-only and private artifacts use a resumable, digest-verified upload path.
The user sees their size and preparation estimate before rental.

### Optional per-user R2 cache

R2 is an accelerator, not a product requirement. A user may configure their
own private, content-addressed bucket. Public models, private models, inputs,
or compiled UI profile artifacts can be pre-positioned there and then fetched
by the pod over a fast network path.

R2 configuration and credentials remain local. The pod receives only the
minimum short-lived object access needed for the active manifest. A cache
object is trusted only after size and digest validation.

Uploading to R2 is an external write and may have provider costs. Automatic
read-only preflight never performs it. Cache population requires an explicit
user action or a previously saved opt-in policy that states the affected
bucket and cost boundary.

### Caches and reuse

The fastest path is reuse of the already-healthy session: matching artifacts
are not retransferred between sequential jobs. Within a session, all caches
are addressed by content and validated before use. A changed workflow receives
a free delta preflight:

- seed, prompt text, and parameter-only edits reuse the environment;
- new compatible models or inputs use a delta transfer;
- a compatible pinned node addition uses bounded delta provisioning;
- a core, runtime, or incompatible node change requires a new session and a
  new paid confirmation.

Vast volumes are not a first-version universal cache because a volume is tied
to one physical host and would constrain eligible offers. A later optional
accelerator may use them without changing artifact identity or correctness.

## Ten-minute readiness objective

The product must not promise that every cold 27 GiB workflow can transfer in
under ten minutes. The live audit moved 29,347,469,703 bytes, approximately
27.3 GiB, at roughly 25–30 MB/s; transfer alone therefore takes about 16–20
minutes on that path.

Instead, paid readiness uses the following measurable service objective:

- hashing, source resolution, compatibility analysis, and optional cache
  preparation happen before rental;
- the quote view shows cached bytes, remaining bytes, expected bandwidth, and
  a readiness estimate;
- paid confirmation is blocked when required artifacts are not source-ready;
- if the estimate exceeds ten minutes, the UI requires the user to prepare a
  cache or explicitly accept the longer estimate before rental;
- for a source-ready manifest accepted under the ten-minute objective, the
  target from successful Vast creation to the `ComfyUI Vast` environment being
  ready for native Run is at most ten minutes;
- warm-session and cold-session measurements are reported separately.

If actual preparation misses the accepted estimate, the UI shows the exact
phase, bytes, last successful probe, and reason. It never silently replaces
the instance, extends a price cap, or destroys the session unless that action
was already covered by an explicit user authorization.

## Offers, filters, and paid confirmation

### Filter semantics

- Empty user filters mean no user filter.
- The workflow derives a hard minimum for VRAM and disk.
- A user-entered VRAM value is an approximate preference. It cannot lower the
  workflow's hard minimum.
- A user-entered maximum price is a hard safety cap.
- Offers are ranked by fitness to the preference, compute performance and
  DLPerf, network, disk, reliability, and expected readiness.
- Every excluded offer has a visible reason.

The first version presents a small ranked shortlist and leaves final selection
to the user. It does not choose a paid GPU autonomously merely because it is
cheap.

### Atomic confirmation boundary

The quote view includes the exact offer identity, current hourly price,
estimated preparation time, disk, duration policy, and maximum authorized
cost. At `Louer et préparer`:

1. the controller creates a durable local intent and idempotency identity;
2. it refreshes inventory and the exact offer quote;
3. it compares the refreshed terms to the visible authorized terms;
4. it creates the instance only when they still match;
5. otherwise it records `quote_expired`, shows the replacement quote, and
   requires a new explicit confirmation.

An expired quote never falls through to a different offer. Refresh, restart,
or timeout reconciliation uses the durable intent and Vast inventory to avoid
a second create call.

There is one paid confirmation. After readiness, ordinary native Run, queue,
and batch actions submit work to the already-rented pod; they do not authorize
or trigger another rental.

This supersedes both earlier brainstorm ideas: the workflow is not submitted
automatically when provisioning finishes, and no special `Run Vast` control is
added. The user opens the ready `ComfyUI Vast` environment, inspects or edits
the canvas, and uses the same native controls as in any Desktop environment.

## Provisioning and readiness

The worker bootstrap is immutable and reboot-idempotent. If its installation
directory already exists, startup validates the release lock and layout, then
relaunches the gateway. It neither overwrites the installation nor fails on an
existing directory.

Provisioning transactions use a deterministic identity derived from the
manifest digest. A controller timeout retrieves and validates the existing
transaction before considering a replay. Progress is monotonic and an already
ready transaction cannot regress to zero transferred bytes.

The `ComfyUI Vast` environment remains unavailable for execution until one
consistent readiness result proves all of the following:

- the intended Vast instance exists and matches the durable session;
- the authenticated worker gateway is reachable;
- worker, ComfyUI core, and frontend release locks match;
- the ComfyUI process is healthy after a bounded startup sequence;
- all expected executable custom-node classes appear in `/object_info`;
- every required model and input has the expected relative path, size, and
  digest;
- the safe profile and approved UI assets match their digests;
- the current canvas bootstrap revision is available;
- the loopback relay is bound to the intended session and upstream;
- native HTTP and WebSocket probes succeed through the relay;
- when Agent Panel is present in the source profile, its scoped bridge and
  edit, run, and batch capabilities are verified;
- local execution has not been invoked.

Readiness is persisted. A refresh reports the same validated record rather
than restarting provisioning.

## Native execution and durable synchronization

### Two complementary event paths

The `ComfyUI Vast` environment consumes native ComfyUI HTTP and WebSocket
behavior through the relay. This provides immediate `executing`, `progress`,
`progress_state`, `progress_text`, preview, `executed`,
`execution_success`, and `execution_error` behavior in the normal canvas.

Separately, the worker records an ordered, persistent event log and job state.
This path provides recovery, audit, controller status, and output harvesting.
It does not replace or synthesize the normal UI event stream while connected.

### Atomic worker snapshot

The authenticated worker exposes one job snapshot operation. It reads one
persisted record or one database transaction and returns:

- job identity and remote prompt identity;
- current execution and harvest state;
- events strictly after the requested sequence cursor;
- the record's `last_sequence` from that same read;
- persistent output descriptors;
- typed, sanitized error data;
- creation and update timestamps.

State and events must never come from separate reads. A response whose events,
cursor, identity, or state fail strict validation is rejected and journaled.

### Local reconciliation

Local SQLite applies snapshots atomically and idempotently using
`(job_id, sequence)` as the event key. Reapplying the same snapshot changes
nothing. A later snapshot advances only monotonically.

A backend-owned, single-flight reconciler runs:

- while a session or job is active;
- on every relevant controller status read or refresh;
- after the local backend starts;
- after the Vast Desktop environment reconnects;
- before teardown and after output recovery.

A transient transport failure creates a sanitized synchronization warning and
retry schedule. It does not convert a remotely running or successful job into
an execution failure. Every background task observes its exception. No
`done.exception()` result may be consumed and discarded without durable state,
a safe log entry, and a retry or terminal classification.

### Job state separation

Remote GPU execution and local output recovery are separate dimensions. A
successful remote prompt can enter `harvesting` while files are downloaded.
The UI then says, in equivalent localized wording, “Execution succeeded —
retrieving outputs.” A failed download can be retried without rerunning the
GPU prompt.

Only one job executes at a time on a first-version session. Additional
submissions use the native queue and receive durable identities in order.

## Outputs and previews

Temporary descriptors with `type == "temp"`, including legitimate
`PreviewImage` results, are displayed through the native UI but ignored by
final harvesting. They are not persistent output failures.

Final harvesting accepts only strictly validated persistent descriptors with
`type == "output"`. It rejects traversal, absolute paths, unexpected roots,
malformed metadata, and a digest or size mismatch. A malformed persistent
descriptor produces a typed `invalid_output` error without changing a real
remote `execution_success` into an execution failure.

ComfyUI Desktop's official remote content script may automatically download
persistent outputs. The controller nevertheless verifies that each expected
file exists on the Mac with the correct size and SHA-256. If automatic
download is absent or incomplete, it uses an authenticated resumable fetch.

Files are written to a deterministic, safe Cloud Vast job directory with a
temporary name and atomic final rename. By default this directory is beneath
the configured local ComfyUI output root as
`cloud-vast/<session-id>/<job-id>`. Digest identity prevents duplicate copies
from two successful download paths. The local record stores the final Mac path
without exposing it to the remote worker.

## Errors and run journal

### Typed error contract

Errors have a stable safe code and phase. The first version distinguishes at
least:

- `validation_error`;
- `dependency_error`;
- `transfer_error`;
- `quote_expired`;
- `provider_error`;
- `provisioning_error`;
- `comfy_startup_error`;
- `execution_error`;
- `synchronization_error`;
- `harvest_error`;
- `invalid_output`;
- `worker_restart_error`;
- `lifecycle_error`.

The user-facing message explains the failed phase and safe next action. The
original typed cause remains available to the controller. Unknown exceptions
become `internal_error` with a correlation identity, not the misleading
generic message “Remote execution failed.”

### Sanitized diagnostics

The durable journal records:

- session, manifest, transaction, and job identities;
- phase and stable error code;
- start, update, and terminal timestamps;
- safe node identity when ComfyUI supplied one;
- process exit code and restart count;
- last health probe and its sanitized result;
- monotonic byte and event cursors;
- a small bounded tail of ComfyUI stdout and stderr;
- retry and provider-inventory reconciliation outcomes;
- output verification state;
- destruction and post-destruction inventory evidence.

The sanitizer removes provider keys, bearer tokens, cookies, signed URLs,
authorization headers, secret query parameters, credential values, sensitive
environment variables, and unrelated absolute local paths before persistence
or display. Output and log size limits are applied before storage. ComfyUI
stdout and stderr are never sent to `DEVNULL` without retaining the bounded,
sanitized diagnostic tail.

### Complete live-run error journal

The following sixteen findings are part of the product record and must each
have a regression test or an explicit field-test assertion.

| # | Observed fact or failure | Confirmed cause or current status | Required resolution |
| --- | --- | --- | --- |
| 1 | An offer expired before confirmation and no GPU was rented. | The quote was stale at the paid boundary. | Revalidate the exact quote atomically at the click; record `quote_expired` and require a new confirmation. |
| 2 | Every gateway request returned 401. | Caddy removed `Authorization` before verifying the bearer token. Commit `994f4a6` fixed handler order with a `route` block. | Preserve the fixed boundary and test authorized, missing, and invalid bearer cases without logging the token. |
| 3 | Imports failed in the real nested ComfyUI Desktop loader. | Top-level `cloud_run` and `remote_worker` assumptions did not hold. The branch contains the sibling-relative fallback. | Keep package-relative loading compatible with the real loader and never mutate global `sys.path`. |
| 4 | Desktop did not restart its backend after a manual stop; a manual launch then hit a `comfyui.db` lock. | Two lifecycle mechanisms competed for one local installation. | Use only the official Desktop lifecycle and never run concurrent local backends against the database. |
| 5 | The real model transfer completed: 29,347,469,703 bytes, about 27.3 GiB. | Models, class definitions, and artifacts on the pod were present and valid. | Preserve digest validation and use this measurement for honest cold-start estimates. |
| 6 | The worker sent ComfyUI stdout and stderr to `DEVNULL`. | The useful startup cause was erased. | Persist bounded, sanitized phase, exit, probe, and log-tail diagnostics. |
| 7 | Provisioning reached `ready`, but the controller HTTP call timed out; refreshes reapplied the manifest and regressed reported progress. | The controller did not first retrieve the durable `provision-<manifest digest>` transaction. Commit `a53929d` adds ready-transaction recovery. | Preserve strict transaction validation and monotonic replay tests. |
| 8 | Reboot ran `mkdir` on `/opt/comfyui-cloud-run-bootstrap` and failed because it already existed. | The on-start script assumed a fresh filesystem. | Make bootstrap reboot-idempotent: validate the immutable release lock and relaunch the gateway without overwrite. |
| 9 | The GPU workflow truly finished. | The worker recorded 158 events, all nodes completed, sampler 20/20, and a real `execution_success`. | Use this as proof that remote execution worked and keep execution success distinct from later synchronization or harvest failures. |
| 10 | Local synchronization froze at event 94. | The relay read `/events` and `/job` separately; a new event changed `last_sequence`, `_finish_remote_job` stopped, a scheduled task exception was swallowed, and ordinary refresh did not reconcile durable active states. | Add the atomic snapshot, idempotent cursor reconciliation, startup/refresh recovery, and visible sanitized synchronization errors. |
| 11 | A real success became a reported failure when history contained `SaveImage` and `PreviewImage`. | All outputs reached `ComfyProcess.output_path`, which accepts only `type == "output"`; the legitimate preview was `type == "temp"`. | Ignore temporary descriptors during final harvest, verify persistent outputs strictly, and never rewrite remote success as execution failure. |
| 12 | Worker failures all appeared as “Remote execution failed.” | `JobError`, `ComfyProcessError`, and unknown exceptions collapsed into one message. | Preserve typed safe error codes for validation, execution, synchronization, harvest, transfer, output, worker, and provider phases. |
| 13 | The Cloud Run panel showed almost no live information. | `_job_payload` forced `current_node`, `progress`, and `progress_text` to `None`, and the canvas was not fed native remote state. | Use native events in the `ComfyUI Vast` Desktop environment and mirror durable current state in the local controller panel. |
| 14 | Offer filters were confusing and could favor weak cheap GPUs. | Optional-filter behavior and ranking intent were not explicit. | Use workflow hard minima, optional user preferences, a hard price cap, performance/network/disk/reliability ranking, and visible exclusion reasons. |
| 15 | The path was preflight, search, selection, confirmation, wait, then a second Cloud Run action. | Provisioning and execution were exposed as one long controller wizard. | Run free preflight in the background, use one paid `Louer et préparer`, then open the ready `ComfyUI Vast` environment and use native Run or batch with no second paid confirmation. |
| 16 | Launch took much longer than expected. | Most delay was the real 27.3 GiB transfer at about 25–30 MB/s, not GPU execution; control-plane bugs added avoidable delay. | Pre-position sources, use content cache and delta transfer, rank network/disk, reuse healthy sessions, and report cold versus warm timing honestly. |

Earlier paid diagnostics also encountered authentication and reconciliation
failures. They do not change the latest verified safety state: the former
instance is destroyed, `billing_may_continue=false`, and fresh Vast inventory
is exactly zero. Older chronological live notes that mention an active
instance are historical, not current state.

## Provider lifecycle and billing proof

Vast create and destroy calls are idempotent controller operations reconciled
against fresh provider inventory. A network timeout after create is ambiguous,
not proof of failure; the controller searches for the durable attempt identity
before any retry. Destruction remains pending until inventory excludes the
instance and managed label.

The pod never receives the user's account-level Vast API key. It receives only
the session-scoped worker material and the already-approved self-termination
capability required by the existing deadline design.

A session can host several sequential jobs while its immutable runtime remains
compatible. It cannot silently switch offers, hosts, templates, core releases,
or billing terms. Replacement after a terminal host failure requires the
existing destruction gate and a user authorization that explicitly covers the
replacement terms.

If the user chooses no duration, the session is manual and remains rented
until an explicit destroy. The UI keeps rate, elapsed time, estimated spend,
and a prominent destroy control visible. Closing Desktop is never presented as
teardown.

## Convex decision

The first implementation has no Convex dependency. Local SQLite, persistent
worker state, atomic worker snapshots, and provider inventory reconciliation
are sufficient for the local-first product and keep binaries out of a metadata
control plane.

Convex can be useful later as an optional orchestrator adapter for:

- a durable phase journal visible from another device;
- reactive metadata state;
- watchdog, budget, and alert metadata;
- a scheduled teardown path when the Mac is offline.

It does not provide exactly-once semantics for Vast side effects. External
actions are side effects, are not automatically retried by default, and still
require idempotency identities plus fresh provider-inventory reconciliation.

Convex must not carry model files, workflows, previews, inputs, or outputs.
The relevant official limits are approximately 1 MiB for a workflow's total
function arguments and results and approximately 8 MiB for its journal. These
make Convex a metadata channel, not an artifact transport. The orchestration
boundary therefore exposes a small interface that a future Convex adapter can
implement without changing the worker or artifact protocols.

No Vast key may be stored in Convex without a separate security design and a
new human approval. The same applies to any hosted teardown credential.

Relevant official sources:

- [Convex Workflow](https://github.com/get-convex/workflow)
- [Scheduled functions](https://docs.convex.dev/scheduling/scheduled-functions)
- [Convex error handling](https://docs.convex.dev/functions/error-handling/)

## TanStack decision

TanStack is not needed for the first version. The existing ComfyUI web
extension already has the correct application shell, state boundary, and
native event APIs. Adding TanStack Start or a separate portal would create a
second product surface without solving provider idempotency, dependency
reproduction, remote events, or output recovery.

SaaS-quality practices are still required: explicit state machines, typed
contracts, deterministic tests, accessibility, safe retries, observability,
and clear cost boundaries. A future optional hosted monitoring portal may
evaluate TanStack Query independently.

## Security and privacy

The Vast host is treated as untrusted. Security requirements are:

- the Vast account key remains only in the local credential boundary;
- the renderer never receives remote bearer tokens or signed artifact URLs;
- the loopback relay binds only to `127.0.0.1` and rejects non-loopback host
  headers and origins;
- remote authentication is injected server-side by the relay;
- relay state is one session, one upstream, and one expiring capability;
- worker, bootstrap, custom-node, web-asset, wheel, model, input, and profile
  artifacts are pinned and digest-verified;
- profile synchronization uses a strict allowlist and atomic paths;
- private source access is scoped to the current artifact and session;
- logs and errors are sanitized before persistence and display;
- Agent Panel cannot become a general remote-to-local tunnel;
- controller and worker APIs reject path traversal and oversized payloads;
- no secret is placed in a workflow, URL displayed to the user, query string,
  output filename, or durable event payload.

The local relay must fail closed. If upstream identity, session identity,
certificate validation, or authorization cannot be proven, it exposes no
remote ComfyUI API and the Vast Desktop environment remains unavailable for
execution.

## Testing strategy

Implementation follows strict test-driven development. Every behavior begins
with a failing deterministic test, receives the smallest implementation that
passes, and is refactored only while the suite stays green.

`scripts/check.sh` remains the deterministic repository gate. Offline tests
use fake Vast inventory, fake worker snapshots, fake ComfyUI HTTP/WebSocket
events, temporary profile trees, and synthetic artifacts. They must never need
a real provider key or create a real instance.

Required automated coverage includes:

- exact quote expiry and create idempotency;
- no Vast mutation before explicit confirmation;
- loopback origin, host, auth injection, and route isolation;
- official Remote Connection compatibility fixtures;
- native `/prompt`, WebSocket, history, view, and object-info relay behavior;
- current-canvas bootstrap and conflict-safe profile synchronization;
- background image copy and remote-only path rewrite;
- executable-node and UI-only-package manifest separation;
- Agent Panel capability scope, expiry, workflow edit, native run, and
  multi-prompt batch behavior;
- compatible dependency delta preparation after a local or Agent Panel edit;
- source selection, digest mismatch, resumable transfer, and R2 opt-in;
- reboot-idempotent worker bootstrap;
- readiness validation for classes, models, profile, HTTP, and WebSocket;
- atomic snapshot consistency and idempotent cursor replay;
- swallowed-task-exception prevention and restart reconciliation;
- all native progress and terminal event shapes;
- `PreviewImage` temporary descriptor acceptance;
- strict `SaveImage` persistent descriptor and Mac digest verification;
- harvest retry after remote success without a second prompt submission;
- every typed error and sanitizer rule;
- manual and deadline lifecycle states;
- destroy reconciliation and inventory-backed billing proof.

## Paid field acceptance

A field test is allowed only after the implementation plan and implementation
are separately approved, all deterministic checks pass, credentials are
confirmed sanitized, pre-test Vast inventory is read-only and equals zero, and
the user gives a fresh explicit `GO` for the exact paid mutation.

One accepted field campaign must prove:

1. pre-test inventory contains no instance;
2. the displayed offer, rate, disk, duration, and intent match the created
   instance;
3. no instance exists before `Louer et préparer` is confirmed;
4. the official independent `ComfyUI Vast` environment opens inside Desktop,
   beside the local environment and never in an external browser;
5. the current canvas, saved workflows, palette, background image, approved UI
   extensions, and Agent Panel are visible; Agent Panel can safely modify the
   active canvas and prepare a batch;
6. expected custom-node classes and exact model digests are present on the
   pod, and model selectors show them;
7. local prompt submission and local GPU execution remain unused;
8. native Run and an Agent Panel batch execute the current Vast canvas on the
   pod GPU;
9. active nodes, sampler progress, progress text, previews, native errors, and
   `execution_success` behave in the normal interface;
10. a `SaveImage` persistent output is verified on the Mac while a
    `PreviewImage` temporary output is ignored by final harvesting;
11. closing and reopening the Vast Desktop environment catches up without a
    duplicate prompt;
12. restarting the local backend reconciles the active or completed job from
    the atomic worker snapshot;
13. a second compatible job reuses the healthy session and transfers only a
    real delta;
14. the journal contains useful phase, cursor, probe, and bounded log evidence
    with no secret;
15. readiness duration is measured and labeled as cold, pre-positioned, or
    warm rather than combined into one claim;
16. an explicit destroy removes the instance, a fresh inventory is exactly
    zero, and the final local state says `billing_may_continue=false`.

If any criterion fails, the campaign records the typed phase and preserves
safe recovery evidence. It does not hot-patch the paid pod, create a
replacement, or expand paid scope without another explicit human decision.

## Compatibility maintenance follow-up

Upstream ComfyUI and ComfyUI frontend changes can break extension APIs, queue
semantics, settings, or Desktop Remote Connection behavior. A later,
separately designed maintenance agent may watch each upstream commit and
release, run a compatibility matrix against pinned fixtures, and open a pull
request only when an intentional compatibility update is needed.

That agent must:

- have no Vast or artifact-provider credential;
- run deterministic tests without renting hardware;
- pin the upstream commit it evaluated;
- explain the detected contract change and affected compatibility range;
- update fixtures and code through reviewable tests;
- open a pull request but never merge, release, deploy, or rent autonomously;
- require human review for every compatibility change.

This follow-up preserves compatibility without placing an AI agent in the
runtime control or billing path.

## Authoritative implementation boundaries

Before implementation, the approved plan must decompose this design into
strict TDD tasks with exact files, test names, commands, checkpoints, and
rollback-safe commits. It must preserve these boundaries:

- all control routes remain under `/cloud-run/api/`;
- the loopback Remote Connection origin is a scoped data plane only;
- local Run is untouched;
- a native Run or batch from `ComfyUI Vast` can never queue locally;
- no provider mutation occurs without explicit confirmation and durable
  intent;
- no implementation or live paid test begins merely because this design was
  approved;
- `/Users/wuraaang/comfyui-vast-cockpit` is never modified, imported, or used
  as a source tree;
- no implementation begins until this written specification is explicitly
  approved, then an autonomous authoritative plan is written with
  `superpowers:writing-plans`, committed, and explicitly approved;
- every implementation slice uses strict TDD and ends with
  `scripts/check.sh`;
- a paid field test always needs a new exact human `GO`.

Official integration references:

- [ComfyUI Desktop and independent environments](https://github.com/Comfy-Org/Comfy-Desktop)
- [ComfyUI Desktop remote sources](https://github.com/Comfy-Org/Comfy-Desktop/blob/main/src/main/sources/remote.ts)
- [ComfyUI Desktop remote content script](https://github.com/Comfy-Org/Comfy-Desktop/blob/main/src/main/lib/comfyContentScript.ts)
- [ComfyUI frontend API](https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/scripts/api.ts)

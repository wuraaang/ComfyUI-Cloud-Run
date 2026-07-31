# Workflow-Derived Vast GPU Session Design

Status: proposed next product slice. This document changes no runtime behavior.
After written approval, the implementation plan must first replace the
repository's current slice contract in `AGENTS.md`; until then, the existing
official-template-only and no-workflow-transfer rules remain authoritative.

## Decision

Extend `ComfyUI-Cloud-Run` from a safe Vast rental lifecycle into a local
ComfyUI Desktop control plane for temporary remote GPU sessions.

ComfyUI Desktop remains the only canvas and the source of truth for workflows,
presets, input files, and dependency metadata. It does not execute a local
reference generation. A Vast instance reconstructs the environment required by
the current canvas, executes the exact prompt compiled by the installed
ComfyUI frontend, returns progress and results locally, and is then destroyed.

The selected dependency policy is hybrid:

1. every dependency must have a complete installation plan before the first
   paid mutation;
2. the rented pod validates the real installed environment;
3. an installation mismatch discovered only on the pod receives one bounded
   repair and at most one additional ComfyUI restart;
4. unresolved or stalled work remains subject to the session's explicit cost
   limit and destruction policy.

## User outcome

The user builds or edits any supported image or video workflow in the local
ComfyUI Desktop canvas and clicks the separate `Cloud Run` action. The extension
then:

1. prepares the canvas exactly as the local Run path would prepare it;
2. resolves nodes, models, and input files without renting hardware;
3. shows dependencies, disk, offer, rate, bandwidth, and session-limit details;
4. rents one explicitly confirmed Vast instance;
5. provisions and validates only the environment the workflow needs;
6. executes one or more generations during the same paid session;
7. displays remote progress, previews, errors, and outputs locally;
8. retrieves and verifies final files before a normal teardown;
9. destroys the instance and verifies fresh Vast inventory before claiming that
   Vast billing ended.

The experience resembles using the local canvas with a remote execution engine.
It does not expose a remote CUDA device to the Mac and does not require the
local machine to be capable of running the workflow.

## Existing baseline

The current branch already provides:

- a standalone web-only ComfyUI custom-node package;
- a stable `Cloud Run` action beside local Run without intercepting it;
- write-only Vast settings;
- read-only offer search and exact quote revalidation;
- durable paid-attempt identity and idempotency;
- one managed Vast create call after explicit confirmation;
- inventory-backed cancellation and destruction;
- restart reconciliation, host blacklisting, and one destruction-gated
  replacement;
- fake lifecycle, security, route, and frontend certification.

This design preserves those safety properties while splitting a paid rental
into one reusable session containing one or more remote jobs.

## Terms

### Session

One rented Vast instance with one immutable identity, rate, disk allocation,
deadline policy, remote-worker protocol version, and installed dependency set.
A session can execute sequential jobs until the user destroys it or a deadline
destroys it.

### Job

One compiled canvas submission within a session. A job owns its prompt digest,
input manifest, progress, remote prompt ID, outputs, retrieval state, and
sanitized error. A failed job does not automatically destroy a healthy session;
the user can edit the canvas and submit another job before the session ends.

### Dependency manifest

A content-addressed, browser-safe description of everything required to
execute a compiled prompt:

- ComfyUI core and frontend compatibility versions;
- executable node class types;
- custom-node repositories, immutable revisions, and installation metadata;
- model category, remote path, source, size, and SHA-256 digest;
- input assets, sizes, digests, and upload destinations;
- expected output budget;
- required secrets referenced by opaque local handles, never embedded values.

### Remote Worker

A small, versioned companion from the same repository that runs only inside
the Vast instance. It provisions a manifest, validates ComfyUI, relays native
ComfyUI execution, exposes authenticated status and artifact operations, and
enforces a session deadline. It never receives the user's account-level Vast
API key.

## Architecture

### 1. Canvas Adapter

The browser extension obtains the execution payload from the live local canvas.
It must reuse the same frontend preparation semantics as the installed local
Run path, including:

- widget `beforeQueued` behavior such as seed control;
- promoted widget controls;
- virtual-node `applyToGraph` transformations;
- asynchronous widget `serializeValue`;
- subgraph and link resolution;
- bypassed and muted node handling;
- the current full UI workflow serialization;
- supported queue options such as preview configuration.

The adapter produces both values returned by the frontend's queue compilation:

- `workflow`: the UI workflow metadata;
- `output`: the executable API prompt.

It also computes a canonical digest over the executable prompt and relevant
queue options. It must not use a manually exported API-format file, translate a
workflow with an AI model, reconstruct node inputs independently, or execute a
local generation.

The ordinary local Run action and request path remain unchanged. Cloud capture
is initiated only by `Cloud Run`.

### 2. Dependency Resolver

The local backend resolves the compiled prompt before any paid mutation.

Core node types are satisfied by the pinned ComfyUI release. Each non-core
class type must resolve to one custom-node package using, in order:

1. an existing user-approved mapping;
2. Comfy Registry metadata;
3. the installed package's Git origin and current commit;
4. optional metadata supplied by the Agent Panel integration;
5. an explicit one-time mapping supplied in the Cloud Run dialog.

Custom-node sources are GitHub or Comfy Registry packages pinned to an
immutable commit or release. The resolver records the package's Python
dependency installation contract. It never accepts arbitrary shell text from
the Agent Panel, workflow metadata, or a remote model source.

Model references come from the compiled prompt and ComfyUI's local asset/model
metadata. A resolved model record contains:

- the exact filename used by the prompt;
- ComfyUI model category and remote relative path;
- content size and SHA-256 digest;
- one approved source:
  - public Hugging Face or Civitai artifact;
  - gated Hugging Face or Civitai artifact using a local secret handle;
  - private Cloudflare R2 object;
  - a local private artifact explicitly approved for upload to R2.

The workflow alone is never assumed to contain a trustworthy download URL.
Mappings are stored locally and reused by digest. A filename collision with a
different digest is an error, not a cache hit.

Input images, video, masks, and other file-backed widgets receive the same
size, digest, destination, and upload treatment. Files are not uploaded during
free dependency analysis unless the user explicitly chooses to populate a
private R2 cache.

### 3. Vast Orchestrator

The orchestrator owns provider credentials, paid confirmation, durable session
state, offer identity, creation, reconciliation, and verified destruction.

It rents a project-owned, public, versioned Vast template derived from a pinned
official ComfyUI image. The template contains only the stable bootstrap needed
to start the authenticated Remote Worker. It contains no user secret, workflow,
model, or mutable dependency.

This is an intentional migration from the current official-template-only
create contract. The create contract is not switched to the project template
until its bootstrap, authentication, deadline enforcement, and teardown pass
the complete offline gate and the exact public artifact is pinned by immutable
digest.

The create contract allocates ephemeral container storage calculated from:

- pinned base environment allowance;
- unique model sizes;
- custom-node and Python environment allowance;
- input sizes;
- expected output allowance;
- 20 GB safety headroom.

The allocation is at least 80 GB. Every artifact that will be downloaded must
have an exact known size before confirmation. Output and temporary-workspace
allowances are conservative estimates derived from workflow media dimensions,
frame counts, and node categories; when they cannot be derived, the user must
approve an explicit positive allowance. The resulting disk allocation is fixed
in the paid quote and revalidated with the offer.

The provider lifecycle and remote execution lifecycle are independent:

- provider state answers whether an instance exists and may bill;
- remote-worker state answers what the instance is doing;
- job state answers what the current prompt is doing.

No remote status or closed browser is accepted as evidence that Vast billing
ended. Only a fresh provider inventory without the instance ID and managed
label can do that.

### 4. Remote Worker

The Remote Worker is authenticated by a per-session credential and remains
behind the template's authenticated network boundary. The local backend, not
the browser, communicates with it.

Provisioning proceeds as follows:

1. verify protocol and base-image compatibility;
2. receive the signed manifest;
3. reserve disk and reject insufficient capacity before large downloads;
4. install custom nodes at pinned immutable revisions;
5. install their declared Python dependencies;
6. download models and inputs concurrently;
7. write downloads to `.part` files with bounded retries and ranged resume;
8. verify declared sizes and SHA-256 digests;
9. atomically move verified files into their ComfyUI destinations;
10. start or restart ComfyUI;
11. compare native `/object_info` with every executable class type;
12. validate required model filenames and input destinations;
13. report a complete, sanitized readiness record.

The first remote validation failure may invoke one repair when every missing
item already has an approved source. Repair can reinstall a pinned package,
retry a verified artifact, or correct a known destination. It cannot select an
unapproved alternative, upgrade unrelated packages, or execute generated shell
commands. Repair permits one additional ComfyUI restart.

Restart budgets apply per provisioning transaction, not once for the entire
session. Initial provisioning or a later compatible manifest delta may perform
one planned restart after installing pinned custom-node content. A repair may
perform at most one additional restart for that same transaction.

Ten minutes without meaningful byte, install, health, or validation progress
marks provisioning or repair as stalled. The session remains governed by its
absolute deadline and explicit destruction controls.

The worker receives Vast's instance-scoped `CONTAINER_ID` and
`CONTAINER_API_KEY`. It may use them only to destroy its own instance when an
enabled absolute deadline expires. It cannot search offers, create another
instance, read billing data, or control any other instance.

### 5. Local Relay

The relay submits the already compiled prompt to the native remote ComfyUI
queue and maps remote events to the local Cloud Run session:

- execution start and queue position;
- current node and progress;
- preview frames when available;
- validation and execution errors;
- remote history and output descriptors;
- resumable output download progress.

The local canvas remains visible and editable. A debugging link may be shown
only after authenticated readiness and must point through the authenticated
local relay, never expose a remote bearer token or signed provider URL; normal
use does not require opening it.

Final files download to a private local `.part` path. Size and digest are
verified before an atomic rename into the configured local output destination.
The durable job record then marks that artifact `local_verified`.

## Preflight and paid confirmation

The preflight has three dependency states:

- `resolved`: immutable install or transfer plan exists;
- `mapping_required`: the user or an optional integration must approve a
  source;
- `unsupported`: the dependency cannot be safely provisioned by this version.

The first rental is unavailable unless every executable dependency and input
is `resolved`. This prevents paying for a pod while searching for a dependency
already known to be absent.

The paid confirmation shows:

- offer ID, GPU, VRAM, reliability, and host-quality signals;
- active hourly rate and allocated ephemeral storage;
- provider bandwidth prices when available;
- dependency and input download size;
- calculated disk allocation;
- enabled session duration and approximate maximum active/storage charge;
- template and Remote Worker version;
- the rule that bandwidth and marketplace pricing can make the estimate differ
  from the final provider charge;
- the irreversible destruction policy.

Confirmation revalidates the exact offer and refuses a price increase or
identity change. The session intent and idempotency key are durable before the
single create mutation.

## Reusable session behavior

V1 supports one sequential remote job at a time per session.

After a successful job the session returns to `ready`; it is not destroyed
automatically. The user can edit the local canvas and click `Cloud Run` again.
The resolver computes a manifest delta against the session's verified installed
set:

- an empty delta queues the job immediately;
- new models or inputs transfer without reinstalling existing content;
- new custom nodes provision at pinned revisions and may consume that manifest
  delta's one planned ComfyUI restart;
- incompatible dependency revisions require ending the current session and
  starting a newly confirmed one rather than mutating a working environment
  unpredictably.

Every new job still captures a fresh compiled payload. The system never reuses
an old prompt merely because the workflow filename is unchanged.

## Storage and caching

V1 does not create, attach, stop, or manage Vast volumes.

All Vast container data is disposable and is deleted with the instance.
Destroying the session ends its Vast container-storage billing. Later sessions
reconstruct their environment from approved sources.

Cloudflare R2 Standard is an optional external content-addressed cache:

- public models may continue to download directly from their canonical source;
- private or frequently reused artifacts may be stored under digest-based keys;
- long-lived R2 credentials remain write-only and local;
- the worker receives only short-lived, object-scoped signed operations;
- resumable range requests are used for large downloads;
- cache corruption or a wrong digest is rejected and never installed.

R2 improves repeated cold starts but is not required for public artifacts.
R2 cost and performance remain separate from Vast cost and host bandwidth.

## Duration, cost, and destruction controls

The default session limit is two hours. The user can configure another finite
limit or explicitly select `No automatic limit` for an individual rental.

For a finite limit:

- warnings appear 15 minutes and 5 minutes before expiry;
- the user can add 30 minutes, add one hour, or explicitly disable the limit;
- the new deadline is durably persisted and sent to the Remote Worker;
- both the local orchestrator and Remote Worker enforce the deadline;
- expiry uses the same verified destruction path as a manual request.

Selecting no limit requires a per-rental acknowledgement that billing can
continue until manual destruction, including when the Mac is off or
disconnected. The UI displays a persistent red `No automatic limit` warning.

The primary destructive control is labeled:

`Destroy GPU — stop all Vast billing`

It does not call Vast stop. A manual request requires:

1. opening a destruction confirmation;
2. reviewing instance identity, current state, and unverified remote artifacts;
3. acknowledging that remote data will be irreversibly lost;
4. activating the final `Destroy now` action.

Normal completion retrieves and verifies every expected output before
destruction. The absolute deadline is the final cost boundary: after bounded
retrieval retries, expiry may destroy the instance even when an artifact is not
verified locally. The job then remains failed with an explicit incomplete
artifact record and local diagnostics.

Destruction is not reported as complete until provider inventory proves that
the instance and managed label are absent. A failed verification retains the
instance ID, warns that billing may continue, and provides the Vast console
emergency action.

## Durable state and recovery

Local SQLite persists:

- sessions, quotes, deadlines, provider instance IDs, labels, and rates;
- immutable dependency manifests and installed-set records;
- jobs, prompt digests, remote prompt IDs, and lifecycle states;
- artifact transfer offsets, expected sizes, digests, and local paths;
- repair and restart counts;
- cancellation and destruction intent;
- sanitized failures and residual-billing evidence.

Relevant session states are:

```text
preflight
offer_selected
confirming
creating
bootstrapping
provisioning
validating
ready
running
harvesting
repairing
destroy_requested
destroying
destroyed
failed
```

A local restart first reconciles Vast inventory by managed label, adopts at
most one matching instance, reconnects to the worker when possible, resumes
artifact transfer, and re-enforces the durable deadline. It never blindly
creates a replacement.

An eligible host boot failure may use the existing single replacement policy,
but only after destruction and inventory absence of the first instance are
verified. Workflow execution errors, out-of-memory errors, missing user
mappings, budget decisions, and deterministic provisioning failures never
trigger an automatic second rental.

## Agent Panel boundary

The Agent Panel is optional. Cloud Run must remain installable and fully usable
without it.

When present, the panel may call a narrow local registration interface to
suggest:

- custom-node source URL and revision;
- model source URL, category, expected filename, size, and digest;
- a workflow-local dependency annotation.

Every suggestion passes through the same resolver, user-approval, origin,
revision, and digest rules as manual input. The panel cannot authorize a paid
action, expose stored secrets, submit arbitrary provisioning commands, bypass
preflight, or destroy an instance.

## Security

- Vast, R2, Hugging Face, and Civitai credentials remain write-only and
  backend-owned.
- Account-level Vast credentials never enter the remote instance.
- Short-lived remote credentials are scoped to one session or object.
- Browser responses contain no provider secret, signed private URL, or remote
  bearer token.
- Remote values are sanitized and rendered with `textContent`.
- Custom-node code is pinned to immutable revisions and displayed before paid
  confirmation.
- Model installation requires size and SHA-256 verification.
- Unknown redirects, origins, mutable branch heads, generated shell, and
  dependency source substitutions fail closed.
- Logs, screenshots, fixtures, error payloads, commits, and GitHub artifacts
  contain no secret.
- Local Run, the Agent Panel, and unrelated ComfyUI routes are not intercepted.

## User interface

The existing dialog becomes a session console with:

1. saved write-only provider and optional cache settings;
2. free dependency preflight with resolved, mapping-required, and unsupported
   rows;
3. offer search and complete paid confirmation;
4. provisioning phases and meaningful byte/install/validation progress;
5. current rate, elapsed time, approximate spend, deadline, and extension
   controls;
6. sequential job status, previews, errors, and locally verified outputs;
7. an optional authenticated remote-debug link;
8. the permanently available red destruction control.

The separate `Cloud Run` launcher remains adjacent to local Run. Local Run is
neither removed nor repurposed, although local execution is not part of Cloud
Run certification.

## Error behavior

### Before rental

Unresolved sources, unknown sizes, unsupported nodes, missing credentials,
insufficient disk, and invalid workflow compilation block confirmation without
a provider mutation.

### During boot or provisioning

Host failure follows the existing destruction-gated replacement policy.
Mapped installation failure receives one repair and, if needed, one additional
restart beyond the planned provisioning restart. Continued failure keeps the
instance visible, exposes the red destruction action, and remains subject to
the deadline.

### During execution

Native validation, execution, and out-of-memory errors are returned with safe
node context. A healthy session returns to `ready` so the user may edit the
canvas and submit a corrected job. These errors do not create a second
instance.

### During result retrieval

Downloads resume from durable offsets and verify before rename. Normal
destruction waits for verified expected outputs. A user-confirmed manual
destruction or absolute deadline may deliberately abandon unverified remote
artifacts after displaying or recording the loss.

### During teardown

A DELETE response is insufficient. Inventory must verify absence. Otherwise
the UI remains in a residual-billing failure state with the instance ID and
emergency Vast action.

## Test strategy

Implementation uses strict red-green TDD and keeps all automated provider work
offline or fake.

### Canvas tests

- Cloud capture invokes the same queue-preparation semantics as local Run.
- Virtual nodes, async widget serialization, seeds, promoted controls,
  subgraphs, bypass, inputs, and preview options produce an equivalent compiled
  payload without executing locally.
- Local Run remains byte-for-byte behaviorally unchanged.

### Resolver tests

- Core and custom node classification;
- Registry, Git origin, Agent Panel suggestion, and manual mapping precedence;
- immutable revision enforcement;
- model filename, category, source, size, and digest mapping;
- R2 private-artifact planning and credential redaction;
- unresolved and filename/digest collision failures before confirmation;
- deterministic disk and download estimates.

### Worker tests

- authenticated manifest acceptance;
- concurrent resumed downloads and digest rejection;
- atomic installation;
- pinned custom-node install contracts;
- native `/object_info` and model validation;
- one repair and at most one additional repair restart per provisioning
  transaction;
- instance-scoped deadline destruction;
- refusal of arbitrary commands and wrong protocol versions.

### Session and relay tests

- quote, create, provision, validate, run, harvest, return-to-ready, second job,
  and verified destroy;
- the second compatible job uses the same instance and does not redownload an
  existing model;
- a manifest delta installs only new compatible content;
- execution errors preserve a healthy session;
- output resume, digest verification, and atomic local rename;
- local restart adoption and deadline recovery;
- finite deadline extension and explicit no-limit mode;
- multi-step manual destruction and inventory absence;
- residual billing is never hidden.

### Repository gate

The deterministic gate includes Python and JavaScript tests, fake end-to-end
session certification, compilation, secret scanning, provider origin/method
allowlists, route/state contracts, and a public-artifact scan.

### Paid Gold certification

The manual Gold asset is the user's existing medium-complexity image-upscale
workflow that creates a desktop wallpaper, plus a user-supplied source image.
Neither personal workflow nor image is committed or uploaded publicly without
separate permission.

Gold certification is remote-only:

1. capture the existing local canvas without a local generation;
2. resolve every dependency and input before rental;
3. review and explicitly confirm one reliable offer;
4. provision and validate the remote environment;
5. execute the wallpaper upscale on Vast;
6. retrieve and verify the expected wallpaper dimensions and output file;
7. modify an execution parameter or approved input and submit a second job on
   the same session;
8. prove that the second job creates no second Vast instance and does not
   redownload unchanged models;
9. retrieve and verify the second result;
10. destroy the session;
11. confirm fresh Vast inventory contains no managed or residual instance;
12. record the provider credit delta without exposing account data.

Pixel identity with a local MPS run is not an acceptance criterion because no
local reference generation is performed. Acceptance proves exact canvas
compilation semantics, immutable dependency identity, successful native remote
execution, expected output structure and dimensions, local artifact integrity,
session reuse, and verified teardown.

Each paid Gold mutation requires an explicit human authorization that states
the allowed number of rentals and cost boundary.

## Release sequence

1. implement and certify the complete fake/offline session locally;
2. run the repository security and secret gate;
3. review the exact source and remote bootstrap artifact;
4. create the correctly named public GitHub repository and push the reviewed
   immutable commit;
5. pin the Vast bootstrap/template to that immutable public artifact;
6. perform the separately authorized paid Gold certification;
7. document measured provisioning time, transfer behavior, cost, and known
   compatibility limits;
8. publish a release only after teardown and inventory evidence pass.

The unrelated ComfyRelay remote is never used.

## Acceptance criteria

- ComfyUI Desktop remains the sole canvas and performs no Cloud Run reference
  generation.
- Cloud Run captures the same live frontend compilation semantics as local Run
  without altering local Run.
- Known unresolved dependencies block rental; pod-only mismatches receive one
  bounded repair.
- Any supported workflow with a complete immutable manifest can be provisioned
  without a workflow-specific shell script.
- V1 creates no Vast volume and leaves no Vast storage charge after verified
  destruction.
- One session executes at least two sequential compatible jobs without a
  second rental or redundant model download.
- Progress, errors, previews, and final outputs return to the local session
  console.
- Normal teardown waits for verified local outputs; manual destruction and the
  absolute deadline are explicit data-loss boundaries.
- The default two-hour limit is extensible or explicitly disableable, and a
  finite limit remains enforceable when the Mac disconnects.
- Manual destruction requires strengthened confirmation and never claims
  billing ended before inventory absence.
- Agent Panel annotations are useful but optional and cannot authorize paid or
  arbitrary remote actions.
- Offline gates pass before GitHub publication or paid certification.
- The remote-only wallpaper Gold executes twice on one session, retrieves both
  results, destroys the instance, and leaves Vast inventory empty.

## Non-goals

- exposing a remote GPU as a local CUDA, Metal, or PyTorch device;
- executing a local reference generation;
- supporting dependencies with no approved source, size, or immutable identity;
- arbitrary shell execution supplied by workflows, models, or AI agents;
- Vast volumes, stopped-instance persistence, or long-lived Vast storage;
- automatic mutation of unrelated local workflows, models, or custom nodes;
- concurrent jobs within one V1 session;
- other GPU providers;
- hiding provider bandwidth, storage, or residual-billing risk;
- publishing personal Gold assets without explicit permission.

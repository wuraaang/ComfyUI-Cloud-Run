# Workflow-Embedded Model Metadata Bridge Design

Status: approved product design. This document changes no runtime behavior.
Implementation still requires a separate written plan and red-green TDD.

## Relationship to the session design

This document narrows and amends the model-resolution and Agent Panel sections
of `2026-07-31-workflow-derived-vast-gpu-session-design.md`.

The earlier safety rule remains authoritative: a workflow-provided URL is not
trusted as an installation plan. The new rule is that native ComfyUI model
metadata is the first source candidate. Cloud Run must independently validate,
pin, size, and hash that candidate before it becomes a manifest artifact.

For provisioning failures, this document strengthens the earlier behavior.
After the already-authorized bounded retry or repair is exhausted, a terminal
bootstrap, model-transfer, or environment-validation failure requests verified
destruction immediately. Cloud Run does not intentionally keep an unusable pod
billing merely to preserve live debugging access. Durable sanitized diagnostics
remain available locally after destruction.

All other session, quote, deadline, destruction, and residual-billing rules in
the earlier design remain unchanged.

## Decision

Use ComfyUI's native workflow model metadata as the integration contract
between workflow producers, local ComfyUI missing-model detection, and Cloud
Run dependency resolution.

An Agent Panel-created or explicitly repaired workflow records model metadata
on the relevant loader nodes. This makes the workflow self-describing to the
pinned ComfyUI frontend and gives Cloud Run deterministic source candidates.
Cloud Run then converts those candidates into the existing immutable
`ArtifactSpec` contract during free preflight.

The first implementation slice supports public, ungated Hugging Face files.
It does not require R2, a local copy of a model, a manual workflow export, or a
workflow-specific remote script.

## User outcome

The user can construct a workflow locally without downloading its models. If
the workflow producer supplied complete native metadata:

1. ComfyUI detects selected model files that are absent locally;
2. the native missing-model panel can display its `Download All` action;
3. Cloud Run captures the currently open canvas without a local generation;
4. free preflight resolves every public Hugging Face candidate to immutable
   identity, exact size, SHA-256, and destination;
5. offer search and rental remain unavailable until all models and inputs are
   resolved;
6. after one explicit paid authorization, the worker downloads verified models
   into their standard ComfyUI directories and runs the captured prompt.

No user-authored dependency manifest is required.

## Considered approaches

### Selected: native annotations plus independent verification

The Agent Panel writes the same `properties.models` records used by official
ComfyUI workflows. ComfyUI consumes them locally. Cloud Run treats them as
untrusted candidates and upgrades them to an immutable manifest during
preflight.

This approach preserves native behavior, avoids duplicate workflow syntax, and
keeps the paid boundary fail-closed.

### Rejected: trust embedded URLs directly

Passing `resolve/main` URLs straight to a paid worker would preserve the local
ComfyUI experience but would permit source drift, filename collisions, unknown
sizes, and unverifiable downloads. It violates the existing immutable manifest
contract.

### Rejected: require a separate Cloud Run manifest

A second user-managed manifest could be deterministic, but it would duplicate
information already represented by ComfyUI and make workflow creation depend on
Cloud Run-specific authoring. It is retained only as a future import/export
diagnostic, not as the normal user path.

## Native workflow metadata contract

### Location

Each loader node that selects a model records candidates in
`node.properties.models`. The compiled prompt's selected static model input is
the execution requirement; the annotation supplies metadata for that selected
value.

Cloud Run reads workflow-level `models` entries for compatibility with native
ComfyUI, but per-node metadata is the required Agent Panel output because it
keeps provenance adjacent to the selection that uses it.

### Fields

A native record has this shape:

```json
{
  "name": "flux1-fill-dev.safetensors",
  "url": "https://huggingface.co/OWNER/REPO/resolve/main/PATH",
  "directory": "diffusion_models"
}
```

The contract is:

- `name` is the exact safe relative model name selected by the loader widget;
- `url` is an HTTPS source candidate accepted by native ComfyUI;
- `directory` is the exact ComfyUI model category, such as
  `diffusion_models`, `text_encoders`, `vae`, or `upscale_models`;
- `hash` and `hash_type` are optional for native compatibility, but either both
  are absent or `hash_type` is `sha256` and `hash` is exactly 64 lowercase hex
  characters that match preflight;
- absolute paths, traversal segments, embedded credentials, fragments, and
  non-HTTPS URLs are invalid;
- duplicate records for the same `(name, directory)` are allowed only when
  their normalized source and supplied digest agree.

The Agent Panel may use an official ComfyUI template, the ComfyUI Manager
catalog, or a direct Hugging Face lookup to discover a candidate. It must not
guess a source solely from a similar filename.

### Cloud-ready producer status

The Agent Panel may describe a workflow as `Cloud-ready` only when every active
static model input has a matching record with at least `name`, `url`, and
`directory`.

That status is advisory. It does not approve a mapping, validate a remote file,
authorize a rental, or override Cloud Run preflight.

The Agent Panel writes metadata while creating a workflow or after an explicit
repair request. It does not silently mutate unrelated existing workflows.

## Native ComfyUI behavior

The pinned ComfyUI frontend already:

1. scans active combo and asset widgets for model filenames;
2. derives a model directory from the loader type;
3. determines whether the selected filename is absent;
4. enriches the missing candidate from `properties.models` or compatible
   workflow-level metadata;
5. makes a candidate downloadable when it has both `url` and `directory`;
6. displays `Download All` when at least one eligible candidate exists.

Cloud Run must not fork that user-facing missing-model implementation or invent
a second annotation format.

Distribution behavior remains explicit: ComfyUI Desktop can use its local
download bridge to place files in model directories. A `localhost` browser
build may open the source downloads instead. This design guarantees native
detection and action visibility; automatic localhost server-side installation
remains the responsibility of ComfyUI Manager or the Agent Panel, not Cloud
Run.

## Architecture

### 1. Workflow producer

The Agent Panel or another compatible producer emits native model annotations
while constructing the workflow. It may resolve sources with network access,
but it stores no Hugging Face token, signed URL, arbitrary command, or secret in
the workflow.

No new privileged Agent Panel-to-Cloud Run API is required for model metadata.
The serialized workflow is the narrow interchange boundary.

This repository implements and certifies the consumer contract. Agent Panel
code remains in its own repository and is not modified through the mandatory
Cloud Run worktree. If its existing workflow-writing surface cannot emit these
properties, that producer change requires a separate spec, worktree, and test
plan. The Cloud Run integration can still be certified with a conforming
annotated fixture and an explicitly Agent Panel-repaired Gold workflow.

### 2. Canvas capture

The existing Canvas Adapter continues to capture the official queue payload:
`workflow`, `output`, and `queue_options`. Native annotations remain inside the
captured `workflow`, so the capture schema and executable prompt digest need no
new user-visible manifest field.

Capture must preserve node properties exactly while retaining the current
size, tree-safety, pinned-frontend, and no-local-execution validation.

### 3. Embedded metadata parser

A narrow backend parser recursively reads model records from top-level nodes
and subgraph definitions in the captured workflow. It retains each per-node
record's flattened execution identity and also builds a compatibility index for
workflow-level records keyed by normalized `(name, directory)`.

The compiled prompt remains authoritative for which models are required. The
running ComfyUI host's file-input metadata remains authoritative for which
prompt inputs are models and which category each loader expects. A compiled
requirement first matches metadata from the same flattened execution node and
selected value. A workflow-level record is a fallback only when its exact
`(name, directory)` key has one candidate. Stale annotations on inactive or
unreferenced nodes are ignored.

An exact required key must resolve to one normalized candidate. Missing or
conflicting candidates are `mapping_required`; they never trigger a network
download or paid mutation.

### 4. Hugging Face metadata client

During free preflight, a dedicated client parses each public Hugging Face
candidate into repository, revision, and file path. It then:

1. resolves a mutable branch or tag to one 40-character commit;
2. queries that exact revision for the exact file;
3. requires an exact positive byte size and content SHA-256;
4. confirms that the repository and file are public and ungated;
5. checks any embedded SHA-256 for equality;
6. constructs the canonical commit-pinned `resolve` URL accepted by
   `SourceSpec`.

The client follows only the narrowly allowlisted Hugging Face API and source
contract. A missing file, unrecognized response, gated repository, unavailable
digest, or source substitution fails closed. Large model bytes are not
downloaded during preflight.

Network access is isolated behind an injectable interface so automated tests
remain offline and deterministic.

### 5. Artifact resolver

The artifact resolver gains a source-first model path while preserving the
existing local-first behavior for input media and private artifacts.

For each required model:

- if a local file exists, its size and SHA-256 are measured as today;
- if a verified public source also exists, its identity must equal the local
  file or a filename/content collision blocks rental;
- if no local file exists, verified Hugging Face metadata supplies size,
  digest, and source without requiring a local download;
- the destination is always `models/<directory>/<name>`;
- identical destination/digest requirements are deduplicated;
- different digests for one destination are rejected.

The resulting model uses the existing `ArtifactSpec` and immutable
`SourceSpec(kind="huggingface")`. No worker protocol or manifest schema change
is required.

Input images, masks, and videos remain local artifacts: they must exist under
the approved ComfyUI input root, are hashed before rental, and use the existing
resumable local transfer when no R2 cache is configured.

### 6. Existing worker and lifecycle

The Remote Worker continues to receive only the immutable dependency manifest.
It downloads public Hugging Face models directly, verifies size and SHA-256
before atomic installation, validates the installed ComfyUI environment, and
then submits the captured native prompt.

The worker never sees the mutable annotation URL, Agent Panel authority, or a
provider account credential.

## Data flow

1. The Agent Panel selects a loader filename and writes its native metadata.
2. ComfyUI serializes the metadata with the current workflow.
3. Native missing-model scanning can expose `Download All` locally.
4. `Cloud Run` invokes official queue preparation and captures the live canvas.
5. The backend identifies static model inputs from the compiled prompt.
6. The metadata parser matches exact `(name, directory)` candidates.
7. The Hugging Face client pins and verifies each candidate without downloading
   model bytes.
8. The resolver produces dependency rows, exact total bytes, disk allocation,
   and immutable manifest artifacts.
9. Any non-resolved model or input keeps offer search and rental disabled.
10. After explicit confirmation, the existing lifecycle rents, provisions,
    executes, retrieves, and verifies destruction.

## User interface

The free preflight row for each model displays inert text for:

- exact selected name and ComfyUI destination;
- source repository and file path;
- immutable revision;
- exact size and abbreviated SHA-256;
- `resolved`, `mapping_required`, or `unsupported` state;
- a concise repair reason when not resolved.

The paid confirmation uses only the pinned values and total bytes produced by
preflight and links to the public source repository. Cloud Run does not infer
usage rights for the user.

Provisioning progress identifies the current phase and model without exposing
signed URLs or secrets: instance creation, worker startup, model transfer bytes,
digest verification, ComfyUI startup, environment validation, and ready.

## Error behavior

### Before rental

- missing native metadata: `mapping_required`, with node, input, model, and
  expected directory;
- annotation name or directory mismatch: `mapping_required`;
- conflicting annotations: `mapping_required`, with no automatic choice;
- malformed, private, gated, non-Hugging-Face, or unverifiable first-slice
  source: `unsupported`;
- local/source digest collision: hard preflight error;
- missing local input media: `unsupported` until the user selects an existing
  input;
- unknown size or SHA-256: rental remains disabled.

No case above performs a Vast mutation.

### After rental

A transient model transfer may use the existing bounded retry. A deterministic
digest mismatch is never installed. Once authorized retry, repair, or
replacement options are exhausted, terminal bootstrap, transfer, or
environment-validation failure immediately enters verified destruction.

If destroy or fresh-inventory verification fails, the UI remains in the
existing residual-billing failure state with the instance identity and manual
Vast recovery guidance.

A native workflow execution error on an otherwise healthy ready session retains
the earlier reusable-session behavior; it does not silently create a second
instance.

## Test strategy

Implementation follows strict red-green TDD.

### Metadata and capture tests

- capture preserves nested native `properties.models` without changing local
  Run behavior;
- flattened subgraph execution nodes match only their own selected metadata;
- exact selected name/category matches;
- stale unreferenced annotations are ignored;
- missing, mismatched, malformed, and conflicting annotations fail closed;
- identical annotations deduplicate deterministically;
- absolute paths, traversal, credentials, and unsupported URLs are rejected.

### Hugging Face client tests

- mutable revision resolves to a mocked immutable commit;
- exact public file produces canonical URL, size, and LFS SHA-256;
- an already pinned commit remains pinned;
- query normalization cannot change repository or path identity;
- redirects or API responses cannot substitute another origin;
- gated, private, missing, digest-less, size-less, and hash-mismatched files
  fail closed;
- all automated tests use fake responses and perform no external download.

### Resolver and manifest tests

- an annotated model absent locally resolves source-first;
- a matching local copy and public source resolve to one artifact;
- a local/source digest collision blocks rental;
- destinations cover `diffusion_models`, `text_encoders`, `vae`, and
  `upscale_models`;
- multiple prompt references deduplicate by destination and digest;
- local inputs retain current hashing and transfer behavior;
- manifest schema and worker protocol remain unchanged;
- dependency totals and disk estimates include source-first models.

### UI and lifecycle tests

- every unresolved row disables offer search and paid confirmation;
- resolved rows show only sanitized pinned metadata;
- progress reports current phase and transfer bytes;
- terminal provisioning failure requests destruction;
- residual-billing state is never hidden;
- fake end-to-end certification reaches ready, executes, retrieves, and
  verifies destroy with source-first model fixtures.

### Local integration proof

Before paid work, load an annotated model-absent fixture into the pinned local
ComfyUI frontend and verify that native missing-model detection exposes
`Download All`. Do not click it or download model bytes as part of this proof.

Run a real read-only Hugging Face preflight for the designated Gold workflow,
then record immutable revisions, exact sizes, SHA-256 values, destinations, and
total transfer size. This step performs no Vast mutation.

The repository's deterministic `scripts/check.sh` gate must pass twice
consecutively before requesting paid authorization.

## Designated Gold workflow

The initial manual acceptance workflow is the user's currently open
medium-complexity FLUX Fill wallpaper outpaint/upscale graph. Its active model
requirements are:

- `flux1-fill-dev.safetensors` in `diffusion_models`;
- `clip_l.safetensors` in `text_encoders`;
- `t5xxl_fp8_e4m3fn.safetensors` in `text_encoders`;
- `ae.safetensors` in `vae`;
- `4x_foolhardy_Remacri.pth` in `upscale_models`.

The graph uses pinned ComfyUI core/extras nodes and no external custom node.
The personal workflow and source image remain outside the repository. Before
rental, the live `LoadImage` selection must resolve to an existing private
input under the approved ComfyUI input root.

The first Gold preflight and run use public Hugging Face sources only. No R2,
gated model, Civitai source, Vast volume, or paid GPU action is introduced
without the separate authorization already required by the session design.

After every offline and free-network proof is green, Cloud Run asks once for a
paid authorization that states allowed instance count, maximum hourly price,
and absolute cost or duration boundary. It then follows the existing Gold
execution, output verification, immediate teardown, and fresh-inventory proof.

## Acceptance criteria

- Agent Panel-created workflows describe each active static model with native
  `name`, `url`, and `directory` metadata.
- The pinned local frontend recognizes those annotations and exposes its native
  missing-model download action without Cloud Run-specific workflow fields.
- Cloud Run captures the open canvas and requires no manual workflow or model
  manifest.
- A public annotated Hugging Face model can resolve without existing locally.
- Every rentable model has an immutable commit, exact positive size, lowercase
  SHA-256, canonical destination, and sanitized provenance.
- Missing or conflicting metadata, missing input media, mutable unresolved
  identity, and digest collisions block rental.
- The existing immutable manifest and worker protocol remain authoritative.
- No model bytes, input bytes, or paid provider resources are transferred
  during free preflight.
- Terminal provisioning failure stops intentional billing through immediate
  verified destruction after bounded recovery is exhausted.
- The complete fake gate and two consecutive `scripts/check.sh` runs pass
  before a separately authorized paid Gold run.
- Gold captures the live workflow, downloads all five verified models to their
  correct directories, produces one coherent verified output, destroys the
  instance, and confirms empty Vast inventory.

## Non-goals

- trusting workflow URLs without independent verification;
- guessing a model source from filename similarity;
- requiring a local model download before Cloud Run;
- implementing a second missing-model UI or workflow metadata format;
- guaranteeing server-side placement from the generic localhost browser
  download action;
- gated Hugging Face, Civitai, R2, or private-model support in the first Gold
  slice;
- custom-node discovery changes beyond the existing resolver;
- arbitrary shell instructions or secrets from the Agent Panel;
- automatic mutation of unrelated workflows;
- renting a GPU before all dependencies and inputs are resolved;
- performing the paid Gold run without its separate explicit authorization.

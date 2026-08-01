# ComfyUI Cloud Run

ComfyUI Cloud Run is a standalone, web-only ComfyUI custom-node package. It
keeps ComfyUI Desktop as the canvas and local control plane while all image or
video inference runs on one temporary Vast.ai GPU. It adds no graph node,
does not expose remote CUDA as local CUDA, and does not replace or intercept
the local **Run/Exécuter** action.

> **Paid Vast.ai rental:** capture, dependency preflight, offer search, and
> rental review do not rent hardware. **Confirm & rent GPU** can start hourly
> billing. Autonomous and repository checks are fake/offline only:
> no real Vast rental or Gold run has occurred.

The official ComfyUI Vast template hash
`027fba7753c024be019030fb42aed900` is an allowlisted source only for
`hash_id`, `use_ssh`, and `ssh_direct`; its mutable image and unrelated launch
fields are not copied. The private worker request instead pins the exact image
`docker.io/vastai/comfy@sha256:9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c`
with tag `v0.29.0-cuda-12.9-py312`.

## Install and compatibility

Place this repository at:

```text
<ComfyUI>/custom_nodes/ComfyUI-Cloud-Run
```

Restart ComfyUI. The package uses the Python standard library plus aiohttp and
Pillow supplied by ComfyUI. It is pinned for development to ComfyUI Core
`0.29.0`, frontend `1.47.10`, and local Python `3.13.12`. The remote lock uses
the exact Python `3.12` series string and remote health accepts only reports
beginning `3.12.`. Remote wheels are limited to compatible `cp312` or universal
`py3` wheels for `any` or Linux x86_64-family platforms; other interpreters,
ABIs, operating systems, and CPU architectures fail closed.

## From canvas to remote prompt

Cloud Run asks the installed frontend's official queue path to produce the
exact prompt compiled by ComfyUI. The Canvas Adapter therefore preserves:

- widget `beforeQueued` hooks and seed handling;
- promoted controls, virtual-node `applyToGraph`, and asynchronous
  `serializeValue`;
- subgraphs, bypass/mute, the complete UI workflow, executable API prompt, and
  preview options.

The adapter captures that native result and never posts that prompt to local
`/prompt`. Local **Run/Exécuter** remains untouched; no approximate workflow
conversion and no local reference generation are used.

## Free dependency preflight

Before any paid mutation, the backend resolves every executable node and
file-backed input. Core nodes are accepted from the pinned host. Custom-node
candidates are considered in this order:

1. an explicitly approved local mapping;
2. Comfy Registry metadata;
3. the installed package's canonical GitHub origin and exact commit;
4. an optional Agent Panel suggestion;
5. a manual Cloud Run mapping.

Every non-core custom node still requires explicit approval and a complete,
immutable package record. Agent Panel suggestions cannot approve a mapping,
authorize spend, read credentials, submit a shell command, or destroy a
session.

Models, images, video, masks, custom-node archives, and pinned Python wheels
must have an approved source, exact byte size, SHA-256, and safe destination
under `models/`, `input/`, or `custom_nodes/`. Approved sources are immutable
GitHub commits, Hugging Face commit URLs, Civitai model-version URLs, explicit
local uploads, or content-addressed R2 objects. Missing or unresolved material
makes the preflight non-rentable.

Disk is computed from the base environment, dependencies, inputs, output and
temporary allowance, plus 20 GiB headroom, with an 80 GiB minimum. If output
size cannot be derived from the prompt, the user must supply an explicit
allowance. V1 uses no Vast volume; all remote storage is temporary. Optional R2
is a content-addressed transfer cache, never persistent Vast storage.

## Paid session and reusable jobs

The paid boundary is deliberately separate from preflight:

1. search sanitized offers for a rentable preflight;
2. preview a session with a selected offer, idempotency key, disk, and deadline;
3. review the revalidated offer and maximum charge;
4. explicitly confirm the same durable session;
5. create at most one labelled Vast instance;
6. bootstrap, provision, verify hashes, start ComfyUI, and validate
   `/object_info`;
7. run jobs and relay progress, previews, history, and outputs locally.

Offer confirmation revalidates identity and price. Session confirmation and job
submission are idempotent. Restart recovery adopts matching managed inventory
instead of blindly creating another instance.

The paid review includes **Maximum total instance creates**, limited to `1` or
`2` and persisted in the immutable quote. The conservative default, including
legacy restored quotes, is `1`. A reviewed value of `2` authorizes at most one
replacement after verified destruction and fresh inventory absence. The
backend counts the initial create plus the durable retry count and enforces the
limit at confirmation and before any replacement offer search or create; a
duplicate or ambiguous request cannot replenish the budget.

A session accepts one job at a time but can run multiple jobs sequentially.
When the next job needs nothing new, execution starts immediately. A compatible
model or input causes only its transfer delta. A compatible new custom node
causes its delta plus one controlled ComfyUI restart. A changed installed
revision or runtime identity requires a new session. Upload and output download
resume from durable verified offsets after interruption.

The Local Relay accepts only authenticated worker events, bounds progress
watchdogs, sanitizes errors, and publishes an output only after exact size and
SHA-256 verification. Execution or OOM failure returns a healthy reusable
session to `ready`; provisioning gets one bounded repair, and host boot failure
gets at most one replacement after destruction and verified absence.

## Deadline, cost, and destruction

The default finite deadline is two hours. The session UI raises alerts at
15 and 5 minutes and supports reviewed extensions of 30 minutes or one hour.
Disabling the limit requires an explicit red-warning acknowledgement and a
successful sync to the worker.

The worker receives only its own `CONTAINER_ID` and `CONTAINER_API_KEY`. At an
expired finite deadline it persists destroy intent, allows only the bounded
retrieval grace, abandons incomplete outputs if necessary, and issues one
own-instance DELETE even if the Mac is disconnected.

**Destroy GPU — stop all Vast billing** is the only normal terminal action.
It uses a fresh destroy review and explicit data-loss acknowledgement. A DELETE
response is insufficient: a new account inventory must prove the managed
instance and label are absent. If inventory still shows a residual instance,
the UI says billing may continue and gives the exact emergency cleanup action.
Vast Stop is never exposed.

Normally the relay verifies and retrieves every result before destruction. A
manual destroy or absolute deadline may sacrifice an incomplete output to stop
billing. A future Gold run must destroy immediately after the first coherent
verified output.

## Secrets and network boundaries

- Vast, R2, Hugging Face, and Civitai account credentials remain in private
  backend settings. The settings response exposes configured booleans and
  browser-safe `worker_release`: either `null` or the exact validated
  `WorkerRelease.to_record()` from the service instance used by that request.
  It never returns the lock path, archive URL, credential, token, session
  secret, workflow, model, or private template payload.
- Settings and durable session/job/transfer state use a private local directory
  with `0700`/`0600` permissions. Browser storage is not used.
- Browser JavaScript calls same-origin routes only and renders untrusted values
  with `textContent`.
- Account-level Vast actions are limited to offer search/get, instance
  create/list/get/destroy.
- The worker binds to loopback behind the reviewed Caddy bearer boundary. Its
  only provider action is DELETE of its own numeric instance ID.
- Worker installation uses immutable archives and wheels, strict extraction,
  exact sizes and hashes, and fixed argv subprocesses. No workflow or agent can
  supply shell.
- External dependency origins are confined to Comfy Registry, canonical GitHub,
  immutable Hugging Face/Civitai sources, and the exact configured R2 host.

The Vast account calls are:

| Method | Provider endpoint | Purpose |
| --- | --- | --- |
| `POST` | `https://console.vast.ai/api/v0/bundles/` | Search filtered on-demand offers. |
| `PUT` | `https://console.vast.ai/api/v0/asks/{offer_id}/` | Create once with the reviewed template contract. |
| `GET` | `https://console.vast.ai/api/v1/instances/` | Reconcile managed inventory and labels. |
| `GET` | `https://console.vast.ai/api/v0/instances/{instance_id}/` | Poll one instance. |
| `DELETE` | `https://console.vast.ai/api/v0/instances/{instance_id}/` | Destroy, followed by inventory verification. |

## Same-origin API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/cloud-run/api/settings` | Return redacted settings, loaded worker release, and active sessions. |
| `PUT` | `/cloud-run/api/settings` | Validate and atomically save backend settings, then return the same browser-safe release projection. |
| `POST` | `/cloud-run/api/captures` | Persist the native compiled canvas capture. |
| `POST` | `/cloud-run/api/preflights` | Resolve nodes, artifacts, output allowance, and disk. |
| `PUT` | `/cloud-run/api/mappings/{mapping_id}` | Explicitly approve one immutable mapping candidate. |
| `POST` | `/cloud-run/api/integrations/agent-panel/suggestions` | Store one unapproved suggestion. |
| `POST` | `/cloud-run/api/cache/artifacts/{artifact_id}` | Explicitly populate optional R2 cache. |
| `POST` | `/cloud-run/api/offers` | Search and rank sanitized offers. |
| `POST` | `/cloud-run/api/sessions` | Persist a non-renting session preview. |
| `POST` | `/cloud-run/api/sessions/{session_id}/confirm` | Revalidate and create at most once. |
| `GET` | `/cloud-run/api/sessions/{session_id}` | Reconcile and return browser-safe state. |
| `POST` | `/cloud-run/api/sessions/{session_id}/jobs` | Idempotently submit one captured job. |
| `GET` | `/cloud-run/api/sessions/{session_id}/jobs/{job_id}` | Return job, output, and progress state. |
| `GET` | `/cloud-run/api/sessions/{session_id}/jobs/{job_id}/events` | Read durable events after a cursor. |
| `GET` | `/cloud-run/api/sessions/{session_id}/jobs/{job_id}/previews/{preview_id}` | Read one bounded preview. |
| `GET` | `/cloud-run/api/sessions/{session_id}/jobs/{job_id}/artifacts/{artifact_id}` | Read one locally verified result. |
| `PUT` | `/cloud-run/api/sessions/{session_id}/deadline` | Extend or explicitly disable the deadline. |
| `POST` | `/cloud-run/api/sessions/{session_id}/destroy-review` | Obtain a fresh multi-confirmation review. |
| `DELETE` | `/cloud-run/api/sessions/{session_id}` | Destroy and verify fresh inventory absence. |

## Offline certification and release boundary

Run:

```sh
scripts/check.sh
```

The gate runs all Python and Node tests, the two-job fake reusable session,
worker protocol/bootstrap tests, two byte-identical worker artifact builds,
synthetic Pillow corruption checks, compilation/syntax checks, exact
route/state/provider/subprocess allowlists, secret scanning, and a public
artifact scan. It performs no network request, credential lookup, provider
mutation, publication, or GPU rental.

The immutable Remote Worker release for commit
`d317e2f5b69725ae92fd0d3b1dc6273623cf2407` pins remote Python `3.13.12` and is
preserved unchanged as a historical rollback. It is incompatible with the new
Python `3.12` runtime. A fresh deterministic artifact and immutable release are
therefore required; review artifacts alone are never a live release identity.
See `docs/remote-worker-bootstrap-review.md`. There is deliberately no live
worker release lock in this repository.

The offline publication tools bind one immutable GitHub Release asset to the
repository, a 40-character lowercase commit, deterministic asset name, exact
byte size, SHA-256, protocol, and pinned runtime versions. Bootstrap accepts a
direct identity-encoded `200`, or exactly one manually validated `302` to the
fixed GitHub release-assets host. The temporary signed target is neither part
of the lock nor retained, persisted, logged, or returned.

The selected official runtime has these exact reviewed identities:

```text
image: docker.io/vastai/comfy@sha256:9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c
tag: v0.29.0-cuda-12.9-py312
linux/amd64 child: sha256:7a83c93be852db309d4be3e415cf38e186977c202638f1ef1b4a605a3bc49f0a
config: sha256:992e89c2d0641a6c894885d4246dc706911c7a02266f368337b41bf968eaaaf2
config size: 38584 bytes
```

The digest makes the selected runtime bytes immutable, but the config has no
`org.opencontainers.image.revision` label. Vast's successful public build at
source commit `46e032d852ece6edb2a2a477c5b9557cba6645bf` is correlation,
not a cryptographic source-revision binding. The publication gate requires an
explicit human provenance decision: before the sole template POST, accept this
narrower official-image evidence or authorize inspection of the digest-pinned
in-toto attestation. No template publication occurs while that decision is
open.

The deterministic `onstart` exports
`CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI` and directly executes
`/venv/main/bin/python` on the reviewed bootstrap. It does not invoke the image
entrypoint, Supervisor, portal/serverless tooling, or an official wrapper.
`remote_worker/gateway.py` then starts one fixed Caddy binary and the loopback
Python worker without a shell. Only Caddy receives the validated Jupyter
token. The worker receives an explicit runtime allowlist plus Vast's
own-instance ID and API key required by its independent deadline watchdog,
never the Jupyter token or a provider-account key. The supervisor terminates,
then boundedly reaps or kills, the sibling when either process exits. Caddy
exposes `:8765`, strips inbound authorization and boundary headers, and proxies
only to `127.0.0.1:8766`.

Every Vast offer must carry complete verified, rentable, one-GPU, on-demand
evidence, reliability of at least `0.99`, and finite provider-advertised
download bandwidth of at least `500` Mbps. A default search uses disjoint
target (`>= 1,000` Mbps) and fallback (`500–999` Mbps) queries, never relaxes
either floor, and ranks with download speed saturated at `1,000` Mbps before
reliability, disk bandwidth, price, and offer ID. Offer and paid-review views
show the advertised network/disk metrics and
`ceil(bytes * 8 / (Mbps * 1_000_000))` as a theoretical transfer lower bound,
not measured startup time. Exact-offer confirmation reapplies the hard policy
and rejects a drop below `min(reviewed Mbps, 1,000)` before any initial create.
Both replacement paths independently reapply the `0.99`/`500` floors before
selecting or creating from a fresh search result.

Every search and exact-offer revalidation also carries the fixed single-GPU
compatibility contract, and normalization rejects absent, wrongly typed,
non-finite, or below-floor evidence:

```json
{
  "gpu_arch": {"eq": "nvidia"},
  "cpu_arch": {"eq": "amd64"},
  "cuda_max_good": {"gte": 12.9},
  "compute_cap": {"gte": 750},
  "num_gpus": {"eq": 1}
}
```

The deterministic commands are:

```sh
python3 scripts/build_worker_release_bundle.py \
  --repository-root <repository-root> \
  --output-directory <owner-private-output-directory> \
  --worker-commit <40-lowercase-hex-commit>

python3 scripts/publish_worker_template.py \
  audit-base \
  --output-directory <owner-private-output-directory>

python3 scripts/render_worker_template.py \
  --repository-root <repository-root> \
  --output-directory <owner-private-output-directory> \
  --release-metadata <owner-private-release-metadata> \
  --base-template-audit <owner-private-base-template-audit>

python3 scripts/publish_worker_template.py \
  publish \
  --request-file <owner-private-template-request> \
  --output-directory <owner-private-output-directory>

python3 scripts/write_worker_release_lock.py \
  --output <owner-private-data-directory>/worker-release.json \
  --template-hash-id <32-lowercase-hex-template-id> \
  --release-metadata <owner-private-release-metadata>
```

All generation inputs and outputs stay outside the repository in existing
owner-private directories. Generated files use mode `0600`. The final local
lock is created atomically without overwrite and must round-trip through the
runtime loader before it is accepted.

The base audit records only `hash_id`, `use_ssh`, and `ssh_direct`; it records
neither image nor `runtype`/Jupyter fields. The renderer owns the exact image,
tag, and filters above. Its private request fixes `runtype=ssh`, `use_ssh=true`,
`ssh_direct=true`, `jup_direct=false`, `jupyter_dir=/workspace`,
`use_jupyter_lab=false`, empty registry credentials, `-p 8765:8765`, an
80 GiB recommendation, and `private=true`.

The template publisher is a single-purpose client for the fixed Vast template
endpoint. It validates that exact request before HTTP, projects the exact
compared fields from wildcard worker rows, includes `extra_filters` in
ambiguous-POST reconciliation and exact-hash readback, disables ambient proxies
and redirects, reads the owner-private API key without argv, and performs at
most one POST. It never prints payloads or credentials and exposes no update,
delete, arbitrary URL/method, offer, instance, or volume operation. Publication
also requires the encoded bootstrap bytes to equal the reviewed repository file
and the remote lock to be the exact canonical release contract. Automated
coverage injects only fake transports and synthetic keys.

At the 2026-08-01 source-review checkpoint, before live publication: No new
Python 3.12 Remote Worker release has been published. No new private
project-specific Vast template has been created. No local live
`worker-release.json` exists. No post-migration ComfyUI restart has occurred.
No Vast offer search has been performed. No paid Vast instance has been
created. No post-migration live workflow run has occurred.

Before a paid Gold run, a new human GO must state the maximum instance count,
maximum hourly price, and absolute duration or cost. The private Gold image and
workflow are never repository fixtures. The canonical public source repository
is https://github.com/wuraaang/ComfyUI-Cloud-Run. The unrelated ComfyRelay
remote is retained locally only as `comfy-relay-do-not-push` at
https://github.com/wuraaang/comfy-relay.git and must never receive a push.
Publishing repository source alone does not publish a worker artifact, create
a live release lock or project template, or authorize a paid Gold run; those
steps remain separately reviewed and gated.

## Uninstall

Destroy every managed instance and verify an empty Vast inventory before
uninstalling. Removing this custom node, its data directory, or ComfyUI itself
**does not destroy** any remote instance. If the package is unavailable, use the
Vast console for emergency destruction.

MIT. See `LICENSE` and `NOTICE`.

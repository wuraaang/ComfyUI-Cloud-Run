# Project state

Status: workflow-derived reusable sessions implemented and fake/offline
certified on the pinned local ComfyUI environment. The workflow-embedded model
metadata consumer bridge, native missing-model visibility, and the earlier
free, read-only metadata preflight are also complete. The official-image remote
runtime migration is implemented offline. Its post-migration real workflow
preflight, publication, restart, and every paid activity remain human-gated.

Source of truth:

- `AGENTS.md`;
- `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`;
- `docs/superpowers/plans/2026-07-31-workflow-derived-vast-gpu-session.md`;
- `docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md`;
- `docs/superpowers/plans/2026-07-31-workflow-embedded-model-metadata-bridge.md`;
- `docs/superpowers/specs/2026-08-01-official-comfy-worker-runtime-design.md`;
- `docs/superpowers/plans/2026-08-01-official-comfy-worker-runtime.md`.

For the selected image, remote Python, wheel platforms, hardware filters,
launch/onstart contract, and base-template audit schema, the 2026-08-01
documents supersede the corresponding Task 8 passages from 2026-07-31. The
earlier release/bootstrap/security and paid-action boundaries remain
authoritative.

## Implemented offline

- The Cloud Run launcher is adjacent to local `Run/Exécuter`; local queue
  behavior and Agent Panel coexistence remain intact.
- The Canvas Adapter captures the installed frontend's native compiled workflow
  and executable prompt, including widget hooks, seeds, promoted controls,
  virtual nodes, async serialization, subgraphs, bypass/mute, and preview
  options. It never calls local `/prompt`.
- Preflight resolves core/custom nodes, models, inputs, masks, and other
  file-backed dependencies before offer search. All non-core material is bound
  to immutable origin, exact size, SHA-256, and safe ComfyUI destination.
- Exact native model annotations classify active loader selections even when a
  model is absent locally or exposed through a schema-based `COMBO`. Public
  Hugging Face sources are resolved to immutable revisions, exact sizes, and
  SHA-256 values without downloading model content. Existing private inputs
  become verified local-upload artifacts without exposing their identity.
- Custom-node candidate precedence is approved mapping, Registry, installed Git
  identity, Agent suggestion, then manual mapping. Candidates require separate
  explicit approval; Agent suggestions have no spend, secret, shell, or
  lifecycle authority.
- Disk accounting includes base, dependencies, inputs, output/temporary
  allowance, 20 GiB headroom, and an 80 GiB floor. Output ambiguity blocks
  rental until an explicit allowance is supplied.
- Optional R2 cache operations are explicit, backend-signed, short-lived,
  resumable, content-addressed, and hash verified. There is no Vast volume.
- Session quote, paid confirmation, creation, reconciliation, deadline sync,
  destroy review, and inventory-verified destruction are durable and
  idempotent.
- One session supports sequential jobs, one at a time. Compatible dependency
  deltas reuse the instance; compatible custom-node additions trigger one
  controlled restart; incompatible installed identities require a new session.
- The versioned Remote Worker safely transfers and installs pinned material,
  validates ComfyUI `/object_info`, submits the native prompt, emits bounded
  events/previews, and enforces its finite deadline independently.
- The Local Relay resumes uploads and output downloads from durable offsets and
  publishes results only after exact size and SHA-256 verification.
- Provisioning has one bounded repair. A boot-host replacement occurs at most
  once and only after destroy plus fresh inventory absence. Execution/OOM
  failure returns a healthy session to ready.
- The UI exposes progress, reusable-session state, delta review, 15/5-minute
  warnings, +30/+60-minute extensions, explicit no-limit acknowledgement, and
  multi-confirmation `Destroy GPU — stop all Vast billing`.
- The complete fake system certifies one rental, two sequential jobs, delta-only
  transfer, restart recovery, resumed I/O, deadlines, residual inventory, one
  replacement, idempotency, and Agent Panel isolation.
- A deterministic source-only worker artifact, strict GitHub commit bootstrap,
  and synthetic-only Gold structural validator are implemented and gated.
- Offline release tooling now binds a deterministic source-only worker archive
  to an immutable GitHub Release asset name, a 40-character lowercase commit,
  exact size and SHA-256, protocol, and pinned runtime versions. Bootstrap
  accepts only the exact release identity and either a direct `200` or one
  validated `302` to the fixed GitHub release-assets host without retaining the
  signed target.
- Local ComfyUI Desktop remains pinned to Python `3.13.12`. The new remote lock
  uses the exact Python `3.12` series string, and health accepts only `3.12.`
  reports. Compatible custom-node wheels are limited to `cp312` or universal
  `py3` with compatible ABI and platform `any`, exact `linux_x86_64`, legacy
  manylinux x86_64 aliases, or `manylinux_2_5_x86_64` through
  `manylinux_2_39_x86_64`. The selected Ubuntu 24.04/glibc 2.39 runtime rejects
  future or malformed manylinux tags and `musllinux*`.
- The reviewed gateway supervises one fixed Caddy binary and the loopback
  Python worker. The sole candidate is the official Vast launch path
  `/opt/portal-aio/caddy_manager/caddy`; generic paths and symlinks fail
  closed. It isolates the Jupyter token to Caddy, passes the worker only an
  explicit environment allowlist, and boundedly terminates and reaps the
  sibling process when either child exits.
- Deterministic `onstart` gzip-compresses only the reviewed bootstrap before
  base64 encoding, remains below Vast's live `16384`-character limit, exports
  `CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI`, and executes
  `/venv/main/bin/python` directly. The canonical release lock remains raw
  base64. This adds no download and starts neither the image entrypoint nor
  Supervisor, portal/serverless tooling, or an official ComfyUI wrapper.
- Deterministic commands build the release bundle, render strict private Vast
  template inputs, and atomically write an owner-private `0600` local release
  lock without overwrite. Synthetic tests and the live private render exercise
  the same validation; the failed over-limit publication created no template or
  local release lock.
- A single-purpose Vast template publisher now performs only the exact base
  audit, exact-name absence check, at-most-once private-template create, and
  read-back comparison required by the reviewed contract. It fixes the HTTPS
  endpoint, disables redirects and ambient proxies, obtains the API key only
  from the validated owner-private settings file, exposes no generic or delete
  surface, and is tested exclusively with fake transports and synthetic keys.
  The base audit records only `hash_id`, `use_ssh`, and `ssh_direct`; it is not
  an image or `runtype`/Jupyter source. The private request fixes `runtype=ssh`,
  `use_ssh=true`, `ssh_direct=true`, `jup_direct=false`,
  `jupyter_dir=/workspace`, `use_jupyter_lab=false`, empty registry
  credentials, `-p 8765:8765`, recommended disk 80, and `private=true`.
  Before credential lookup or HTTP, it enforces the live `16384`-character
  limit, requires the exact deterministic gzip member for the reviewed
  bootstrap, and revalidates the raw canonical release lock.
- Renderer and publisher pin exactly
  `docker.io/vastai/comfy@sha256:9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c`
  with tag `v0.29.0-cuda-12.9-py312`. The linux/amd64 child is
  `sha256:7a83c93be852db309d4be3e415cf38e186977c202638f1ef1b4a605a3bc49f0a`;
  config is
  `sha256:992e89c2d0641a6c894885d4246dc706911c7a02266f368337b41bf968eaaaf2`
  at `38584 bytes`.
- The digest makes runtime bytes immutable, but the config has no
  `org.opencontainers.image.revision` label. The public Vast build at
  `46e032d852ece6edb2a2a477c5b9557cba6645bf` is correlation rather than a
  cryptographic source-revision binding. Before the sole POST, the publication
  gate requires an explicit human choice: accept that narrower official-image
  evidence or authorize the digest-pinned in-toto attestation review.
- Same-origin GET and PUT settings responses expose browser-safe
  `worker_release`: `null` without a validated loaded lock, otherwise exactly
  `WorkerRelease.to_record()` from the service instance used by the request.
  They expose no lock path, archive URL, credential, token, session secret,
  workflow, model, or private template payload.
- **Maximum total instance creates** is an immutable reviewed quote field
  limited to `1` or `2`, conservatively defaulted to `1` for legacy records,
  and enforced at confirmation and before any replacement offer search or
  create. The initial create plus durable retry count consumes the budget;
  recovery, idempotent retries, and ambiguous-create reconciliation never
  replenish it.
- Vast offer policy requires complete verified on-demand evidence,
  reliability `>= 0.99`, and finite provider-advertised download bandwidth
  `>= 500` Mbps. Disjoint target (`>= 1,000`) and fallback (`500–999`) queries
  feed one ranking whose speed component saturates at `1,000` Mbps; neither
  floor is relaxed automatically. Offers and paid review expose network/disk
  metrics and a preflight-byte-derived theoretical transfer lower bound.
  Confirmation reapplies the hard policy and rejects a material bandwidth
  downgrade before the initial create. Both replacement paths separately
  reapply the reliability/download floors before selecting or creating from a
  fresh search result. Every search and local offer normalization/revalidation
  also requires `gpu_arch=nvidia`, `cpu_arch=amd64`, `cuda_max_good>=12.9`,
  `compute_cap>=750`, and exactly one GPU. The template mirrors these as the
  five strictly typed `extra_filters` including `num_gpus=1`.

## Safety and release status

State recorded at the 2026-08-01 live `onstart` correction checkpoint, before
any separately authorized retry:

- The immutable worker release for
  `d317e2f5b69725ae92fd0d3b1dc6273623cf2407` pins Python `3.13.12` and remains
  unchanged as a historical rollback; it is not compatible with the selected
  Python `3.12` runtime.
- Immutable Python 3.12 Remote Worker releases for reviewed commits
  `005a4b018d9e9404640340d720fbeb43c10f19c2` and
  `f41409946bd756ce141651e651585a9077b0f809` were published and verified. The
  latter remains the newest published rollback point; this size correction is
  not yet released.
- The official base-template audit succeeded. The one authorized private
  template POST for `f41409946bd756ce141651e651585a9077b0f809` exceeded Vast's
  live `onstart` limit, created no discoverable template, and left its durable
  no-retry intent intact. No private project-specific Vast template exists.
- No local live `worker-release.json` exists.
- No post-migration ComfyUI restart has occurred.
- No Vast offer search has been performed.
- No paid Vast instance has been created.
- No post-migration live workflow run has occurred.
- No real Vast rental or Gold run has occurred.
- The native fixture exposed `Download All`, but it was not clicked. The free
  metadata preflight resolved exactly five public model sources and one
  verified local input; no model file was created. That historical evidence is
  not the still-pending post-migration real workflow preflight. Sanitized
  evidence is recorded in `docs/workflow-model-metadata-proof.md`.
- No new Vast provider mutation, upload, Registry publication, worker release,
  template publication, or GPU rental occurred during this migration.
- Public source repository: https://github.com/wuraaang/ComfyUI-Cloud-Run.
  The audited source commit and fetched archive evidence are recorded in
  `docs/remote-worker-bootstrap-review.md`.
- The unrelated legacy remote `comfy-relay-do-not-push` points to
  https://github.com/wuraaang/comfy-relay.git. ComfyRelay received no push from
  this publication.
- The private Gold source and workflow are not read by autonomous tests and are
  never repository fixtures.
- The official base-template allowlist remains
  `027fba7753c024be019030fb42aed900`, but supplies only the reviewed hash and SSH
  flags. It is neither the selected image nor a published project-worker
  release.

## Next gated action

At that source-review checkpoint, the authorized path first requires final
offline gates, review, and an explicit human decision on the image's incomplete
revision provenance. Only
then may separate authorization publish one new Python 3.12 immutable release,
create at most one exactly read-back private template, create the currently
absent no-overwrite local lock, and restart the pinned local ComfyUI. Readiness
must expose the exact loaded `worker_release`; execution then stops before the
real workflow is opened or captured. It still permits no offer search,
instance creation, workflow execution, or paid Gold action. A later paid Gold
GO must separately state all of:

1. maximum instance count;
2. maximum hourly price;
3. absolute maximum duration or total cost.

The first coherent, structurally verified and human-confirmed Gold output ends
the run: destroy the instance immediately and verify an empty fresh inventory.

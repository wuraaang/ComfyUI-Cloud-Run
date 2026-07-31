# Project state

Status: workflow-derived reusable sessions implemented and fake/offline
certified on the pinned local ComfyUI environment. The workflow-embedded model
metadata consumer bridge, native missing-model visibility, and the free,
read-only Gold preflight are also complete. All worker-template and real paid
activity remains human-gated.

Source of truth:

- `AGENTS.md`;
- `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`;
- `docs/superpowers/plans/2026-07-31-workflow-derived-vast-gpu-session.md`;
- `docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md`;
- `docs/superpowers/plans/2026-07-31-workflow-embedded-model-metadata-bridge.md`.

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
- The reviewed gateway supervises one fixed Caddy binary and the loopback
  Python worker. It isolates the Jupyter token to Caddy, passes the worker only
  an explicit environment allowlist, and boundedly terminates and reaps the
  sibling process when either child exits.
- Deterministic commands build the release bundle, render strict private Vast
  template inputs, and atomically write an owner-private `0600` local release
  lock without overwrite. These commands have been certified only with
  synthetic private inputs; they have not published or created live material.
- **Maximum total instance creates** is an immutable reviewed quote field
  limited to `1` or `2`, conservatively defaulted to `1` for legacy records,
  and enforced at confirmation and before any replacement offer search or
  create. The initial create plus durable retry count consumes the budget;
  recovery, idempotent retries, and ambiguous-create reconciliation never
  replenish it.

## Safety and release status

- No immutable Remote Worker release has been published.
- No private project-specific Vast template has been created.
- No local live `worker-release.json` exists.
- No Vast offer search has been performed.
- No paid Vast instance has been created.
- No live workflow run has occurred.
- No real Vast rental or Gold run has occurred.
- The native fixture exposed `Download All`, but it was not clicked. The free
  Gold preflight resolved exactly five public model sources and one verified
  local input; no model file was created. Sanitized evidence is recorded in
  `docs/workflow-model-metadata-proof.md`.
- No project-specific worker template has been published or pinned.
- No Vast provider mutation, upload, Registry publication, worker release, or
  GPU rental occurred.
- Public source repository: https://github.com/wuraaang/ComfyUI-Cloud-Run.
  The audited source commit and fetched archive evidence are recorded in
  `docs/remote-worker-bootstrap-review.md`.
- The unrelated legacy remote `comfy-relay-do-not-push` points to
  https://github.com/wuraaang/comfy-relay.git. ComfyRelay received no push from
  this publication.
- The private Gold source and workflow are not read by autonomous tests and are
  never repository fixtures.
- The official base-template allowlist remains
  `027fba7753c024be019030fb42aed900`; it is not a published project-worker
  release.

## Next gated action

The free boundary is complete. No offer search, provider mutation, release
publication, or paid Gold execution is authorized in the current run. A paid
Gold GO must separately state all of:

1. maximum instance count;
2. maximum hourly price;
3. absolute maximum duration or total cost.

The first coherent, structurally verified and human-confirmed Gold output ends
the run: destroy the instance immediately and verify an empty fresh inventory.

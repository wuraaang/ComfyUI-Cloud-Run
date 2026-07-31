# Project state

Status: workflow-derived reusable sessions implemented and fake/offline
certified on the pinned local ComfyUI Desktop environment. The exact reviewed
source commit has been published and its immutable GitHub archive audited. All
worker-template and real paid activity remains human-gated.

Source of truth:

- `AGENTS.md`;
- `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`;
- `docs/superpowers/plans/2026-07-31-workflow-derived-vast-gpu-session.md`.

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

## Safety and release status

- No real Vast rental or Gold run has occurred.
- No project-specific worker template has been published or pinned.
- No live `worker-release.json` exists.
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

The current bootstrap rejects both GitHub's observed redirect and the fetched
archive's metadata/full-repository layout. The next action is not template
creation: invoke `superpowers:brainstorming` for a narrowly scoped
bootstrap/publication correction, obtain explicit design approval, then use
`superpowers:writing-plans` and strict red-green TDD for a new immutable
commit. Redirect, extraction, allowlist, digest, and shell restrictions remain
fail-closed until that approved work is complete and reviewed.

Only after a corrected public artifact is manually reviewed may separate
authorization create a project-specific Vast template and its owner-private
`0600` release lock. A later paid Gold GO must still state all of:

1. maximum instance count;
2. maximum hourly price;
3. absolute maximum duration or total cost.

The first coherent, structurally verified and human-confirmed Gold output ends
the run: destroy the instance immediately and verify an empty fresh inventory.

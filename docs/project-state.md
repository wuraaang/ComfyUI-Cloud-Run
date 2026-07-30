# Project state

Status: managed lifecycle implemented, fake/offline certified, and installed
in the pinned local ComfyUI Desktop environment. Real paid rental remains
human-gated.

Source of truth: `AGENTS.md` plus
`docs/superpowers/plans/2026-07-30-vastai-cloud-run-lifecycle.md`.

Current lifecycle acceptance gate:

1. A clean standalone custom-node package loads in ComfyUI 0.29.0 without adding a graph node.
2. `Cloud Run` sits beside local `Run/Exécuter` and opens the existing dialog.
3. Local `Run/Exécuter` remains unchanged.
4. The credential stays write-only and all provider calls remain backend-owned.
5. A paid confirmation is required and revalidated before exactly one create.
6. Cancellation, destruction, restart reconciliation, and one destruction-gated
   replacement are durable and idempotent.
7. The complete fake/offline lifecycle and repository gate pass.
8. A real Vast.ai rental happens only after a separate human GO and ends with a
   fresh inventory proving that no managed billed instance remains.

Implemented offline:

- adjacent stable command/launcher without touching local `Run/Exécuter`;
- durable SQLite attempts, quote snapshots, idempotency, and unique labels;
- allowlisted Vast search/create/list/get/destroy contracts;
- expiring machine/host/IP blacklist and deterministic offer policy;
- cancellation during in-flight create and destruction verified by inventory;
- bounded boot watchdog, startup recovery, and one destruction-gated retry;
- server-driven UI for confirmation, polling, Cancel, Open, Destroy, and
  residual-instance emergency guidance;
- full fake lifecycle and repository security gate.
- local ComfyUI 0.29.0 / frontend 1.47.10 load certification, including the
  adjacent launcher, keyboard activation, compact responsive layout, extension
  fallback, clean reload, and Agent Panel coexistence.

The local integration check used an unconfigured credential path and made no
Vast provider call. Not yet performed: a paid Vast mutation or Registry
publication. Both remain outside autonomous execution.

Out of scope: workflow transfer, model downloads, custom-node resolution, other
providers, telemetry, Registry publication, and any autonomous live rental.

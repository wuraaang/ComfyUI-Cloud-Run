# AGENTS.md — ComfyUI Cloud Run

## Product

Build one standalone ComfyUI custom-node package named `ComfyUI-Cloud-Run`. It is a web/backend extension only: no graph node and no separate application.

Do not modify, import from, depend on, or reconstruct
`/Users/wuraaang/comfyui-vast-cockpit`. LoRA Dataset Studio may be inspected
read-only at commit `de697caf9d607a29c72cebdc2794eecd6b147606` to understand
its proven Vast client and lifecycle policies. Reimplement those contracts
narrowly for ComfyUI; do not import its Flask, SQLAlchemy, dataset, training,
checkpoint, or AI Toolkit code.

## Current slice: safe Vast.ai lifecycle

Extend the certified preview V0 into one safe vertical lifecycle:

- place a separate `Cloud Run` action immediately beside local `Run/Exécuter`;
- preserve the local run action without interception or behavior changes;
- keep the Vast credential write-only and backend-owned;
- search and rank offers without renting;
- show a complete paid confirmation before the first mutation;
- revalidate the offer and price, then create exactly one managed instance;
- persist and expose creation, boot, cancellation, failure, and ready states;
- support explicit cancellation and destruction with inventory verification;
- reconcile managed instances after restart;
- permit at most one replacement after a transient boot failure, and only after
  destruction of the first instance has been verified.

Only the official ComfyUI Vast template
`57808457573e32120301649763d8e019` may be created. No workflow transfer,
model synchronization, custom-node resolution, other provider, telemetry, or
Registry publication belongs to this slice.

## Paid-action gate

- No provider mutation occurs when opening the dialog, saving settings,
  searching, selecting, or previewing an offer.
- A create call requires a fresh explicit confirmation containing GPU, VRAM,
  hourly price, offer ID, configured cap, and template.
- No real Vast.ai rental without a separate human GO.
- Automated tests and local certification use fake/offline provider clients.
- Never hide or destroy a residual billed instance after an unverified cleanup;
  show its identifier and an emergency action instead.

## Engineering contract

- Codex is the exclusive implementation writer.
- Strict TDD: observe targeted tests fail before production code, then pass.
- Keep the package dependency-light; use ComfyUI/aiohttp facilities already available.
- Web-only loader contract: `WEB_DIRECTORY`, empty node mappings, decorator-style aiohttp route registration.
- Namespace backend routes under `/cloud-run/api/`.
- Render remote values with `textContent`, not HTML.
- Never log, return, screenshot, or commit the API key.
- Persist paid-attempt intent before provider mutation and enforce idempotency
  server-side.
- A duplicate browser request must never create a second instance.
- All implementation, tests, scripts, and runtime/build configuration stay inside this repository.
- `scripts/check.sh` is the deterministic repository gate.

## Pinned development host

- ComfyUI Core `0.29.0`
- frontend package `1.47.10`
- Python `3.13.12`
- local URL `http://127.0.0.1:8188`
- development install target is external to this repository and is handled by Hermes only after repository checks pass.

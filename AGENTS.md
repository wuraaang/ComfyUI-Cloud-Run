# AGENTS.md — ComfyUI Cloud Run

## Product

Build one standalone ComfyUI custom-node package named `ComfyUI-Cloud-Run`. It is a web/backend extension only: no graph node and no separate application.

Do not modify, import from, depend on, or reconstruct
`/Users/wuraaang/comfyui-vast-cockpit`. LoRA Dataset Studio may be inspected
read-only at commit `de697caf9d607a29c72cebdc2794eecd6b147606` to understand
its proven Vast client and lifecycle policies. Reimplement those contracts
narrowly for ComfyUI; do not import its Flask, SQLAlchemy, dataset, training,
checkpoint, or AI Toolkit code.

## Current slice: local Desktop / remote GPU bridge

Use ComfyUI Desktop's official independent environments to make one temporary
Vast pod the GPU backend of an official Remote Connection named `ComfyUI Vast`:

- the normal local Desktop environment remains independently usable and owns
  the `Cloud Vast` lifecycle control;
- `ComfyUI Vast` uses the pod's native node definitions, model lists, Run,
  queue, batch, progress, previews, history, errors, and outputs;
- There is no `Run Vast` button and a remote prompt has no route to local
  `/prompt` or local GPU execution;
- a safe versioned profile mirrors the canvas, workflows, appearance, approved
  UI extensions, and background without copying databases or credentials;
- the Agent Panel orchestrator remains on the Mac behind a scoped loopback
  bridge and is a mandatory readiness check when present;
- atomic worker snapshots and a backend-owned reconciler recover jobs, events,
  typed errors, and verified outputs across disconnects and restarts;
- one explicit `Louer et préparer` confirmation persists intent and revalidates
  the exact quote before any provider create.

No real Vast mutation without a fresh human GO. Automated implementation and
certification use fake providers only; worker publication, template changes,
external installation, and paid field testing remain separate approvals.

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

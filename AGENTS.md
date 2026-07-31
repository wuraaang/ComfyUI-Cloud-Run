# AGENTS.md — ComfyUI Cloud Run

## Product

Build one standalone ComfyUI custom-node package named `ComfyUI-Cloud-Run`. It is a web/backend extension only: no graph node and no separate application.

Do not modify, import from, depend on, or reconstruct
`/Users/wuraaang/comfyui-vast-cockpit`. LoRA Dataset Studio may be inspected
read-only at commit `de697caf9d607a29c72cebdc2794eecd6b147606` to understand
its proven Vast client and lifecycle policies. Reimplement those contracts
narrowly for ComfyUI; do not import its Flask, SQLAlchemy, dataset, training,
checkpoint, or AI Toolkit code.

## Current slice: workflow-derived Vast GPU sessions

Extend the certified Vast lifecycle into a local ComfyUI Desktop control plane
for temporary remote GPU sessions:

- capture the exact prompt compiled by the pinned frontend without posting it
  to local `/prompt` or running a local reference generation;
- resolve every dependency before the first paid mutation, with immutable
  custom-node revisions and exact artifact sizes and SHA-256 digests;
- rent one explicitly confirmed ephemeral Vast instance as a reusable session;
- provision and validate the repository-owned Remote Worker and native ComfyUI;
- execute one sequential job at a time and support compatible manifest deltas;
- relay progress, previews, errors, history, and verified outputs locally;
- enforce the finite deadline locally and from the worker;
- create no Vast volume and never use Stop as a billing terminal action;
- expose `Destroy GPU — stop all Vast billing` with strengthened confirmation
  and fresh-inventory absence verification.

The project template remains fail-closed until its public worker artifact,
bootstrap, authentication, deadline enforcement, and teardown have passed the
offline gate and an immutable release lock is reviewed. Automated work uses
fake providers only; live rental and publication require separate human
authorization.

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

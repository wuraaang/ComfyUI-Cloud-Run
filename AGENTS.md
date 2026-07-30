# AGENTS.md — ComfyUI Cloud Run

## Product

Build one standalone ComfyUI custom-node package named `ComfyUI-Cloud-Run`. It is a web/backend extension only: no graph node and no separate application.

This repository is new. Do not modify, import from, depend on, or reconstruct `/Users/wuraaang/comfyui-vast-cockpit`. LoRA Dataset Studio may be inspected read-only only to understand the already-proven Vast search contract. Reimplement narrowly; do not copy licensed code or training orchestration.

## Current slice: preview only

The user must see a persistent `Cloud Run` button in the real ComfyUI UI. Clicking it opens a small modal that:

- states clearly that preview mode cannot rent anything;
- accepts a Vast API key once through a password field;
- stores the key only in a backend-owned file with mode `0600` and never returns it;
- accepts a maximum hourly price and minimum VRAM;
- searches real Vast on-demand offers read-only;
- lists selectable offers with GPU, VRAM, hourly price, and reliability;
- lets the user preview/confirm a selected offer and the official ComfyUI template `57808457573e32120301649763d8e019` without creating an instance.

There must be no create, rent, start, destroy, or other provider-mutation route or code path in this slice. A browser click cannot rent a GPU.

## Engineering contract

- Codex is the exclusive implementation writer.
- Strict TDD: observe targeted tests fail before production code, then pass.
- Keep the package dependency-light; use ComfyUI/aiohttp facilities already available.
- Web-only loader contract: `WEB_DIRECTORY`, empty node mappings, decorator-style aiohttp route registration.
- Namespace backend routes under `/cloud-run/api/`.
- Render remote values with `textContent`, not HTML.
- Never log, return, screenshot, or commit the API key.
- All implementation, tests, scripts, and runtime/build configuration stay inside this repository.
- `scripts/check.sh` is the deterministic repository gate.

## Pinned development host

- ComfyUI Core `0.29.0`
- frontend package `1.47.10`
- Python `3.13.12`
- local URL `http://127.0.0.1:8188`
- development install target is external to this repository and is handled by Hermes only after repository checks pass.

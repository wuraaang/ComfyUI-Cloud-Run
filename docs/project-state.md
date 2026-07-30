# Project state

Status: V0 preview implementation authorized.

Source of truth: `AGENTS.md` plus Leo's approved minimal plan.

Current acceptance gate:

1. A clean standalone custom-node package loads in ComfyUI 0.29.0 without adding a graph node.
2. `Cloud Run` is immediately visible after restart.
3. Its modal saves write-only backend settings and performs a real read-only Vast offer search.
4. An offer can be selected and preview-confirmed against official ComfyUI template `57808457573e32120301649763d8e019`.
5. No route or code path can create, rent, start, stop, or destroy an instance.
6. Repository checks and a real browser click path pass.

Out of scope: instance rental, boot polling, Open ComfyUI, Destroy, watchdog/recovery, workflow transfer, model downloads, custom-node resolution, other providers, Registry publication.

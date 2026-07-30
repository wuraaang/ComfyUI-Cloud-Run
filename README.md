# ComfyUI Cloud Run

ComfyUI Cloud Run is a standalone, web-only ComfyUI custom-node package. It adds no graph nodes and no separate application.

> **Preview only — no instance will be rented.**

This first slice stores a write-only Vast API key on the ComfyUI backend, searches real on-demand Vast GPU offers, and previews a selected offer with the official ComfyUI template `57808457573e32120301649763d8e019`. It cannot create, rent, start, stop, or destroy a provider instance.

## Install

Place this repository at:

```text
<ComfyUI>/custom_nodes/ComfyUI-Cloud-Run
```

Restart ComfyUI. The package requires no additional Python dependency: it uses the stdlib plus the aiohttp and frontend facilities already provided by ComfyUI.

The development target for this slice is ComfyUI Core `0.29.0`, frontend package `1.47.10`, and Python `3.13.12`.

## Use

1. Click the persistent **Cloud Run** button.
2. Enter a Vast API key, maximum hourly price, and minimum VRAM.
3. Click **Save settings**. After a successful save, the password field is cleared.
4. Click **Search Vast GPUs**.
5. Select an offer and click **Preview selection**.

The final preview shows the selected GPU and price with the official template and explicitly confirms that no instance was created.

## Security and data

- The API key is sent only in the JSON body of the same-origin settings request.
- It is stored in `<ComfyUI user directory>/comfyui-cloud-run/settings.json`.
- The settings file is written atomically with mode `0600`; its dedicated directory is mode `0700`.
- `COMFYUI_CLOUD_RUN_DATA_DIR` can override the data directory for isolated development and tests.
- The key is write-only: backend responses contain only `configured: true|false` and never return a value, masked fragment, or provider credential.
- Remote offer values are reduced to five fields and rendered through DOM `textContent`.
- The only provider request is read-only offer search: `POST https://console.vast.ai/api/v0/bundles/`.

No provider `/asks` or `/instances` path exists in the runtime package.

## Local API

All routes are registered with ComfyUI's `PromptServer`:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/cloud-run/api/settings` | Return redacted preview settings. |
| `PUT` | `/cloud-run/api/settings` | Validate and atomically store settings. |
| `POST` | `/cloud-run/api/offers` | Search and sanitize matching Vast offers. |

There are no other Cloud Run backend routes in this slice.

## Repository checks

Run the deterministic local gate:

```sh
scripts/check.sh
```

It runs Python and Node tests, syntax compilation, secret-pattern checks, and forbidden provider-path/method checks. The tests use fake HTTP sessions and a tiny fake DOM; they do not read real credentials or make network calls.

## License

MIT. See `LICENSE`.

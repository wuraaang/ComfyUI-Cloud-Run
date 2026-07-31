# ComfyUI Cloud Run

ComfyUI Cloud Run is a standalone, web-only ComfyUI custom-node package. It
adds no graph nodes and does not replace or intercept ComfyUI's local
**Run/Exécuter** action.

> **Paid Vast.ai rental:** searching and reviewing are free, but
> **Confirm & rent GPU** can start hourly billing. The first real certification
> of this development branch still requires a separate human GO; repository
> tests and the current certification are fake/offline only.

Only the official ComfyUI Vast template
`027fba7753c024be019030fb42aed900` is allowed.

## Install

Place this repository at:

```text
<ComfyUI>/custom_nodes/ComfyUI-Cloud-Run
```

Restart ComfyUI. The package adds no Python dependency; it uses the stdlib plus
the aiohttp and frontend facilities supplied by ComfyUI.

The pinned development host is ComfyUI Core `0.29.0`, frontend `1.47.10`, and
Python `3.13.12`.

## Use and cost boundary

1. Click **Cloud Run**, immediately beside local **Run/Exécuter**.
2. Save a Vast API key, maximum hourly price, and minimum VRAM.
3. Click **Search Vast GPUs** and choose an offer.
4. Click **Review paid rental**. This asks the backend for a short-lived quote;
   it does not rent anything.
5. Verify the offer ID, GPU, VRAM, hourly rate, configured cap, and official
   template.
6. Click **Confirm & rent GPU** only if you accept hourly billing.
7. Use **Cancel** while provisioning or **Destroy — end Vast billing** when
   finished. A ready instance continues billing until destruction is confirmed.

No rental occurs when opening the dialog, saving settings, searching, selecting,
or requesting a quote. Confirmation revalidates the same offer at a price no
higher than the quoted price. A durable idempotency key and unique managed label
are saved before the provider call, so retrying the same browser request cannot
create a second instance.

## Secrets and local data

- The API key travels only in the same-origin settings JSON body.
- It is write-only: no backend response returns the value or a masked fragment.
- Settings are stored at
  `<ComfyUI user directory>/comfyui-cloud-run/settings.json`.
- Durable attempts are stored in `attempts.sqlite3`; the expiring bad-host list
  is `host-blacklist.json` in the same directory.
- Files use mode `0600` and the dedicated directory uses `0700`.
- `COMFYUI_CLOUD_RUN_DATA_DIR` overrides the data directory for isolated tests.
- Browser storage is never used for credentials or provider state.
- Provider values are reduced to browser-safe fields and rendered with DOM
  `textContent`.

## Cancellation, destruction, and recovery

Cancellation before creation produces zero rentals. If cancellation races an
in-flight create, intent is persisted first; the backend finds the instance by
its unique label, destroys it, and verifies fresh Vast inventory before saying
`cancelled`.

Vast stop is not exposed by this package. **Destroy** is the billing-safe
terminal action. A successful DELETE response alone is insufficient: inventory
must also prove that the managed instance and label are absent.

On ComfyUI startup, recovery reconciles durable attempts with only
`comfy-cloud-run-*` labels. Restarting never blindly creates a new instance. A
transient boot or transport failure may trigger exactly one replacement, but
only after the original instance is destroyed, absence is verified, and its
machine/host/IP is blacklisted. Authentication, quota, budget, validation, and
configuration failures never retry.

If cleanup cannot be verified, the UI stays failed, shows the residual instance
ID, warns that billing may continue, and displays an emergency instruction to
destroy that instance in the Vast.ai console. Never interpret a backend error
or closed ComfyUI window as proof that billing stopped.

## Uninstall

Before uninstalling, destroy every managed instance and verify the Vast
inventory is empty. Removing this custom node, deleting its data directory, or
stopping ComfyUI **does not destroy** any remote Vast instance. If the package
is already unavailable, use the Vast.ai console as the emergency cleanup path.

## Provider calls

The backend owns every Vast request:

| Method | Provider endpoint | Purpose |
| --- | --- | --- |
| `POST` | `https://console.vast.ai/api/v0/bundles/` | Search filtered on-demand offers. |
| `PUT` | `https://console.vast.ai/api/v0/asks/{offer_id}/` | Create once with the official template. |
| `GET` | `https://console.vast.ai/api/v1/instances/` | Reconcile inventory and labels. |
| `GET` | `https://console.vast.ai/api/v0/instances/{instance_id}/` | Poll readiness. |
| `DELETE` | `https://console.vast.ai/api/v0/instances/{instance_id}/` | Destroy, followed by inventory verification. |

The browser calls only same-origin ComfyUI routes.

## Local API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/cloud-run/api/settings` | Return redacted settings. |
| `PUT` | `/cloud-run/api/settings` | Validate and atomically save settings. |
| `POST` | `/cloud-run/api/offers` | Search and rank sanitized offers. |
| `POST` | `/cloud-run/api/quotes` | Persist a non-renting quote and idempotency key. |
| `GET` | `/cloud-run/api/attempts/{attempt_id}` | Reconcile and return browser-safe state. |
| `POST` | `/cloud-run/api/attempts/{attempt_id}/confirm` | Revalidate and create once. |
| `POST` | `/cloud-run/api/attempts/{attempt_id}/cancel` | Persist cancellation and clean up. |
| `DELETE` | `/cloud-run/api/attempts/{attempt_id}` | Explicitly destroy and verify absence. |

## Repository checks

Run the deterministic offline gate:

```sh
scripts/check.sh
```

It runs Python and Node tests, a complete fake lifecycle, compilation checks,
secret scanning, route/state allowlists, and provider-origin/method checks. It
does not read real credentials, contact Vast, rent hardware, or publish to the
Comfy Registry.

## Scope and license

Workflow transfer, model synchronization, custom-node resolution, other cloud
providers, telemetry, and Registry publication are out of scope.

MIT. See `LICENSE` and `NOTICE`.

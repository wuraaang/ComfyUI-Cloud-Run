# Manual Session Duration and DLPerf Offer Ordering Design

**Date:** 2026-08-02

**Status:** Approved for implementation

## Problem

The Cloud Run panel currently makes a finite session duration mandatory even though the controller and worker already support `deadline.mode = "none"`. It also searches with the last saved price and VRAM settings rather than the values visibly present in the form, which can make the returned offers appear unrelated to the user's current choices. Finally, eligible offers are ordered mainly by network quality and price; no GPU-performance signal is shown or used, so cheap and relatively weak cards can appear first.

## Goals

- Make `No automatic limit — manual destruction` the default session-duration choice.
- Keep the existing finite duration choices available for users who want an automatic deadline.
- Ensure Search persists and uses the price and minimum-VRAM values currently visible in the panel.
- Surface Vast's `dlperf` value and prefer higher-DLPerf GPUs among otherwise eligible offers.
- Preserve every existing hard eligibility, paid-review, one-create, reconciliation, and destruction boundary.
- Load the controller/frontend change locally so a human can perform the real GPU test.

## Non-goals

- Do not change Vast template, worker archive, release lock, or remote worker code.
- Do not weaken the network, reliability, architecture, CUDA, compute-capability, availability, disk, price, or VRAM filters.
- Do not rent a GPU or perform any paid provider mutation during implementation.
- Do not estimate GPU speed from model names or invent a benchmark when Vast does not supply one.

## Design

### Search uses the visible settings

The Save Settings button remains available. Search will additionally validate and persist the currently visible maximum hourly price and minimum VRAM before requesting offers. If an API key is present in the field, it follows the same existing save path. Search begins only after the settings update succeeds.

The sequence is therefore:

1. Validate the visible price and VRAM values.
2. `PUT /cloud-run/v1/settings` with those values and an entered API key, if any.
3. Refresh the panel's configured/settings state and clear the API-key input after a successful save.
4. `POST /cloud-run/v1/offers/search` with the current preflight identifier.

If validation or persistence fails, no offer search is sent and the existing error surface explains the failure. This removes the mismatch between what the form displays and what the server searches.

### GPU performance ordering

Offer normalization accepts Vast's optional `dlperf` field only when it is a finite, non-negative number. The normalized value is exposed to the frontend as `dlperf`; a missing or invalid value becomes `null` and does not make an otherwise eligible offer ineligible.

Eligible offers are ordered by:

1. offers with a valid DLPerf before offers without one;
2. DLPerf descending;
3. the existing quality ordering: target download speed, reliability, disk bandwidth, hourly price, then stable offer identifier.

Offer cards display `DLPerf: <value>` when available and `DLPerf: unavailable` otherwise. DLPerf is a provider benchmark estimate of deep-learning throughput, not an internet-speed measurement. Existing upload/download fields continue to describe connection speed separately.

The selected paid quote remains based on the existing contractual fields; DLPerf is discovery/ranking information and does not become a billing or execution guarantee.

### Manual duration by default

The duration selector gains a `none` option labelled `No automatic limit — manual destruction`, placed first and selected by default. Existing 30, 60, 90, and 120 minute choices remain.

The review request maps choices exactly:

- `none` -> `{ "mode": "none", "duration_seconds": null }`
- a listed finite duration -> `{ "mode": "finite", "duration_seconds": <listed value> }`

Unknown or forged values remain rejected. There is no silent fallback.

The paid review and session console explicitly state that a no-limit session keeps billing until the user presses Destroy and provider inventory confirms zero. The existing red warning, one-create guard, deterministic destroy acknowledgement, and lifecycle controls remain unchanged. Manual duration means no automatic time cutoff; it does not mean free usage or automatic stop when a workflow finishes.

## Error and safety behavior

- Search never proceeds with invalid or unsaved visible settings.
- Missing DLPerf never bypasses a hard eligibility filter and never causes a crash.
- Manual duration never weakens explicit paid confirmation or deterministic destruction.
- No automatic deadline is presented as a deliberate billing-risk choice.
- The implementation creates no Vast instance and leaves fresh provider inventory at zero.

## Verification and rollout

Use focused JavaScript UI tests for save-before-search and duration mapping, focused Python tests for normalization and ordering, then the relevant combined suites and syntax/diff checks. The known unrelated stale Caddy exact-text assertion is not part of this change.

Because the worker and API contract already support `deadline.mode = "none"`, deployment only requires restarting the local ComfyUI controller/frontend. No worker release, Vast template rotation, or lock change is required. The human may test a paid GPU only after the restarted panel is reachable and provider inventory is confirmed empty.

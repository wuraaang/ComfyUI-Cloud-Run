# Provision Transaction Resume Design

## Problem

The paid Vast instance is healthy: the RTX 4090 is visible, all six artifacts
(29,347,469,703 bytes) are present, ComfyUI answers on port 8188, and the
worker has already persisted a matching installed manifest and readiness
record. The local session nevertheless remains `provisioning`.

The controller retries `POST /worker/v1/manifests` after its 30-second HTTP
request expires. Each retry makes the worker start the same transaction again
and temporarily report zero transferred bytes. The controller has already
persisted the full byte count, correctly rejects that regression, and reports
`Remote provisioning response was invalid.` Repeated browser refreshes can
therefore keep an already-ready environment in a validation loop.

## Goal

Resume the exact signed provisioning transaction for the session and manifest
instead of replaying an already-completed manifest application. Do not alter
the manifest, trust remote files without worker validation, weaken monotonic
progress, retransmit models, recreate the instance, or mutate Vast inventory.

## Design

Before submitting a manifest, the controller asks the authenticated worker for
`provision-<manifest digest>`. A response is reusable only when the existing
strict payload validator accepts its exact transaction ID, manifest digest,
progress totals, restart counts, missing-item lists, and state.

If that validated transaction is `ready`, the controller records its monotonic
final progress and returns it immediately. If it is absent, malformed, still
applying, awaiting uploads, failed, or stalled, the current apply and recovery
behavior remains unchanged. Authentication failures are never swallowed.

This controller-side ready replay is deliberately minimal: it works with the
immutable worker already running on the paid pod and requires no remote hot
patch. It also prevents a later reconciliation from turning a completed
transaction into another 29.3 GB validation pass.

## Live recovery

After loading the corrected controller, wait until the existing signed remote
transaction reaches `ready`, then perform one normal session refresh. Success
requires the local session to become `ready`, the worker gateway to remain
authenticated, ComfyUI `/system_stats` to answer, no missing classes or
artifacts, and no additional model transfer. The manual session remains
unlimited until the user destroys it.

## Verification

The urgent acceptance test is the paid field session, per the user's request:
observe the signed transaction, reload the local extension once, refresh the
session once, and confirm `ready` before allowing a workflow submission. Avoid
a broad automated test campaign during this recovery.

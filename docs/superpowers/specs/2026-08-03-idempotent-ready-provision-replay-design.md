# Idempotent Provision Replay and Manual Destruction Design

Date: 2026-08-03 (Europe/Paris)

Status: AWAITING WRITTEN REVIEW

## Problem

Two identical manifest requests can overlap while the first remote provision is
still running. The worker serializes them, but the second request has already
passed the controller's early transaction check. After the first request stores
the exact transaction as `ready`, the queued request starts the same provision
again, overwrites durable progress with a new `applying` record, and can report
fewer transferred bytes than the controller has already verified. The
controller correctly rejects that regression as an invalid response and the
terminal safety path destroys the paid instance.

The live failure reached `29,347,486,774 / 29,347,486,774` bytes and
`comfyui_startup` before this safety path destroyed the instance. Final Vast
inventory was then verified as zero and the local session reported
`billing_may_continue=false`.

## Scope

Make one exact remote provision transaction idempotent after it has reached
`ready`, and ensure that a session configured for manual destruction is never
destroyed automatically because provisioning failed.

The change is limited to:

- `remote_worker/provision.py`;
- `cloud_run/lifecycle.py`;
- focused tests in `tests/python/test_worker_provision.py` and
  `tests/python/test_lifecycle.py`.

There is no controller-route redesign, SQLite migration, UI change, provider
action, Desktop restart, worker publication, template update, rental, push, or
pull request in this correction.

## Design

`Provisioner.apply_manifest()` keeps its existing per-worker asynchronous lock.
Inside that lock, after validating the requested manifest identity, required
class types, and source-URL container, it reads the exact durable transaction
`provision-<manifest digest>`.

If no transaction exists or its state is not `ready`, the current provisioning
flow remains unchanged.

If the exact transaction is already `ready`, the worker:

1. validates the stored transaction and installed readiness through the
   existing `transaction()` boundary;
2. requires the stored validated class types to equal the current normalized
   required class types;
3. returns the existing immutable `ProvisionResult` immediately.

This return occurs before creating a new progress tracker, reserving disk,
writing `applying`, transferring dependencies, installing code, or starting or
restarting ComfyUI. A class-type mismatch fails closed with the existing
sanitized provisioning error and leaves the ready transaction untouched.

### Manual destruction policy

`CloudRunLifecycle.destroy_session()` remains the single provider-destruction
boundary. When it receives a terminal provisioning error for a session whose
`deadline_mode` is `none`, and no explicit destroy has already been requested,
it records the session as `failed` and returns without calling Vast destruction
or inventory APIs. It preserves the instance identity and private session
credentials so the reviewed manual destruction flow can still target the exact
instance. `destroy_requested` remains false and the public state continues to
report that billing may continue.

The boot-timeout path uses this same boundary instead of destroying the
instance directly. Existing explicit destruction remains unchanged. A finite
deadline also remains unchanged because selecting that deadline is an explicit
advance instruction to destroy the instance when time expires.

The existing interface already renders a failed session with an active rental
as a danger state and exposes the reviewed **Destroy GPU** action, so no new UI
or setting is added.

## Tests

A concurrent regression test holds the first identical request during artifact
transfer, queues a second request, then releases the first. It proves that both
callers receive the same ready transaction while transfer, disk reservation,
installation, and ComfyUI startup occur exactly once. It also proves that the
durable transaction remains ready with complete progress.

A focused fail-closed test replays the same manifest with different required
class types. It proves that the worker rejects the inconsistent replay without
mutating the existing ready transaction or repeating provisioning work.

Lifecycle tests prove that a terminal provisioning error and a boot timeout in
manual mode leave the exact instance active, keep `destroy_requested=false`,
and require the existing reviewed manual destruction flow. Existing tests keep
proving that an explicit destroy and an explicitly configured finite deadline
still perform verified destruction.

After the red/green cycle, run the worker provisioning suite, the controller
session/lifecycle suites, and the complete offline certification twice. Both
worker hashes must remain identical within each certification run.

## Acceptance

- A queued duplicate cannot reset a ready transaction or regress progress.
- One exact manifest causes at most one real preparation on a worker.
- Non-ready retries retain their current behavior.
- Invalid or inconsistent replays fail closed without exposing secrets.
- A provisioning failure cannot destroy a manual-mode instance.
- A manual-mode failure reports that billing may continue until the user uses
  the reviewed destruction action.
- Explicit manual destruction and an explicitly armed finite deadline retain
  their current verified-destruction behavior.
- No external state changes occur during implementation or verification.

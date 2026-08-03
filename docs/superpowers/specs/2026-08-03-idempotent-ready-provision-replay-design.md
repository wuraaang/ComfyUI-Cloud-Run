# Idempotent Ready Provision Replay Design

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
`ready`.

The change is limited to:

- `remote_worker/provision.py`;
- focused tests in `tests/python/test_worker_provision.py`.

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

## Tests

A concurrent regression test holds the first identical request during artifact
transfer, queues a second request, then releases the first. It proves that both
callers receive the same ready transaction while transfer, disk reservation,
installation, and ComfyUI startup occur exactly once. It also proves that the
durable transaction remains ready with complete progress.

A focused fail-closed test replays the same manifest with different required
class types. It proves that the worker rejects the inconsistent replay without
mutating the existing ready transaction or repeating provisioning work.

After the red/green cycle, run the worker provisioning suite, the controller
session/lifecycle suites, and the complete offline certification twice. Both
worker hashes must remain identical within each certification run.

## Acceptance

- A queued duplicate cannot reset a ready transaction or regress progress.
- One exact manifest causes at most one real preparation on a worker.
- Non-ready retries retain their current behavior.
- Invalid or inconsistent replays fail closed without exposing secrets.
- No external state changes occur during implementation or verification.

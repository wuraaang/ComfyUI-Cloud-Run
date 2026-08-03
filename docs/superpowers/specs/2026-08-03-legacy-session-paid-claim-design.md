# Legacy session paid-claim compatibility

**Date:** 2026-08-03
**Status:** Design direction approved; written review pending

## Problem

`SessionRepository.claim_create_intent()` enforces the one-rental-at-a-time
boundary by scanning every historical session. It currently reconstructs each
complete `CloudSession`, which reconstructs its `OfferQuote` with the current
schema.

The live Desktop database contains nine terminal audit sessions whose quote
records predate required fields in the current schema. Those rows are still
valid audit evidence, but `OfferQuote.from_record()` rejects them. The exception
occurs after the new session has entered `confirming` and before provider tokens
are persisted or `VastProvider.create_instance()` can be called. The UI then
shows its generic confirmation failure while the session remains stranded in
`confirming`.

The observed attempt has no provider token, no session secret, no instance, no
residual inventory, and `billing_may_continue=false`.

## Required behavior

The paid-create claim must preserve the global one-rental guard without needing
to deserialize historical quote terms that are irrelevant to billing risk.

A historical row must block a new rental when any of these conditions holds:

- an instance identity is present;
- a provider boundary token or session secret is present;
- residual inventory is present or cannot be decoded safely;
- the state can represent a create, active rental, provisioning, execution, or
  destruction in progress;
- an API-key or configuration rejection has not received valid remediation
  evidence;
- the state or required billing-risk fields are malformed or unknown.

A legacy row may be ignored by the conflict guard only when primitive durable
fields prove that provider creation never started or that the row is terminal
and absent: no instance, no provider token, no session secret, no residual
inventory, and no unremediated blocking failure.

No historical row may be deleted, rewritten, or assigned fabricated quote
fields.

## Chosen design

Add a private repository predicate that evaluates only raw SQLite columns
needed for billing risk. It parses state, residual inventory, failure code, and
remediation evidence strictly. Any malformed or uncertain value returns
`true`, keeping the guard fail-closed. `claim_create_intent()` uses this
predicate instead of reconstructing every unrelated `CloudSession`.

Keep full current-schema reconstruction for the target session itself. The
target quote therefore remains strictly validated before a provider token can
be persisted.

Prevent stranded pre-provider state at two boundaries:

1. If the atomic claim raises before a provider token exists, return the target
   from `confirming` to `offer_selected` when that transition is still safe,
   then propagate the sanitized error.
2. During local session refresh, recover a sufficiently old `confirming`
   session with no provider token, no session secret, and no instance back to
   `offer_selected`. The quiet-period check avoids touching a claim currently
   executing. No provider inventory request is needed because the durable
   ordering proves `create_instance()` cannot have been called.

The current stranded live session will therefore become reviewable again after
the corrected controller is restarted and polled.

## Rejected alternatives

1. Migrate old quote JSON into the current schema. Missing reviewed terms would
   have to be fabricated, weakening audit integrity.
2. Delete or archive old sessions outside the durable database. This would
   destroy the evidence the controller is supposed to preserve.
3. Ignore every row that cannot be reconstructed. This could permit a second
   rental when a malformed legacy row still contains an active instance or
   uncertain billing evidence.

## TDD and verification

The first repository regression test inserts a terminal legacy quote that the
current `OfferQuote` parser rejects but whose raw billing fields prove absence.
It must fail with `Invalid paid offer quote` before implementation and then
allow the target claim.

Companion tests must prove that legacy rows with provider tokens, instances,
residual inventory, active states, malformed residual JSON, or unremediated
configuration failures still block. Service tests must prove both rollback on
a pre-provider claim exception and quiet-period recovery of a stranded
tokenless `confirming` session, while leaving a fresh confirmation untouched.

After targeted tests pass, run the complete offline gate twice, compare the
worker artifact byte-for-byte, run `git diff --check`, and commit implementation
separately from this design.

## External boundary

This correction authorizes no provider probe, rental, destruction, pod change,
Desktop restart, worker release, Vast template mutation, push, or PR. Those
actions require new precise human authorization after local certification.

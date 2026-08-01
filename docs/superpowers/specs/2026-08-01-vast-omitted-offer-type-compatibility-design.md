# Vast offer type compatibility design

## Context

The Vast search request explicitly restricts results to `type=ondemand`. Vast currently returns matching offers without repeating the `type` field in each response item. The response normalizer treats that omission as invalid, so the Cloud Run UI displays no offers even though Vast returned valid results.

## Decision

Keep normalization fail-closed by default and pass the request's rental-type constraint explicitly to it.

- If an offer contains `type`, it must be the string `ondemand`.
- If an offer omits `type`, accept it only when the originating request was explicitly constrained to `ondemand`.
- If there is no trusted request constraint, continue rejecting an offer with no `type`.
- Keep every other provider, quality, capacity, and workflow-fit check unchanged.

The search path will derive this context from the actual payload sent to Vast. It will not infer it from user input or from the response.

## Verification

Add regression tests proving that:

1. a Vast-shaped offer with no `type` is accepted when the sent request required `ondemand`;
2. the same offer is rejected when no request constraint is supplied;
3. an explicit non-ondemand value such as `bid` is rejected even when the request required `ondemand`.

Then run the focused tests and the repository verification gate. Finally, update the locally installed extension, restart only the existing ComfyUI Desktop backend, and verify that Search returns offers without creating a rental.

## Scope

This change does not alter pricing filters, offer ranking, session creation, API-key handling, releases, or the private Vast template. Searching remains read-only; a paid resource still requires a separate explicit confirmation.

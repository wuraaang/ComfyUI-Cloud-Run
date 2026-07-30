# Vast Offer Revalidation and Launcher Stability Design

## Goal

Make the existing `ComfyUI-Cloud-Run` extension reliably review the exact
Vast.ai offer selected by the user and keep its `Cloud Run` launcher beside
ComfyUI's local Run button across frontend rerenders.

This change stays inside the current `mission-hermes` architecture. It does
not alter or intercept local execution, remove selectors, add another
application, or integrate with ComfyRelay.

## Confirmed root causes

### Vast offer review

The offer list is a broad, unordered `/api/v0/bundles/` search capped at 20
rows. Vast rotates that result set aggressively. Quote preview and paid
confirmation currently repeat the same broad search and then look for the
selected ID inside those 20 rows. A valid offer can therefore be reported as
unavailable merely because it did not reappear in the next page.

Read-only live probes confirmed that an exact `id` filter does not return the
selected offer on this endpoint, while an exact `ask_contract_id` filter does.
The returned row still exposes its canonical `id`, which can be checked
against the requested offer ID.

### Launcher disappearance

The launcher is initially inserted next to
`[data-testid="queue-button"]`. Its `MutationObserver` disconnects after that
first successful insertion. When ComfyUI replaces the action bar during a
frontend rerender, the launcher remains attached to the discarded DOM and no
observer remains to attach it to the new action bar.

### Unhelpful review error

The backend already returns sanitized error messages. The frontend
`fetchJson` helper discards them on non-2xx responses and the quote handler
replaces every failure with one generic sentence, hiding the useful reason.

## Backend design

Keep broad offer search unchanged for the visible GPU list. Add one separate
read-only Vast contract for exact lookup:

```python
async def get_offer(
    api_key,
    offer_id,
    max_price_per_hour,
    min_vram_gb,
    session=None,
):
    ...
```

The request uses the existing HTTPS `/api/v0/bundles/` endpoint with:

```json
{
  "ask_contract_id": {"eq": 42},
  "limit": 1
}
```

It also retains the existing eligibility filters for price cap, VRAM,
reliability, rentability, verification, one GPU, and on-demand rental. The
response goes through the existing normalizer. The contract returns the one
normalized offer only when its canonical `offer_id` exactly equals the
requested ID; an empty or mismatched response returns no offer. Provider
errors remain sanitized and the API key remains backend-only.

`VastProvider` exposes the same exact lookup. `CloudRunService.preview_offer`
uses it to build the short-lived quote, and confirmation uses it to revalidate
that quote. The existing confirmation rules remain unchanged: the exact
offer, GPU class, minimum quoted VRAM, and a price no higher than the quote
must still match. The service must never silently select a replacement offer.

## Frontend design

`fetchJson` parses the JSON body before checking `response.ok`. For a failed
response it throws an `Error` containing `payload.error` only when that value
is a non-empty string; otherwise it uses the existing generic fallback. Quote
review displays that error through `textContent`, preserving the backend's
sanitization boundary.

Launcher placement keeps one observer for the document body for the lifetime
of the mounted extension. The placement function first checks whether the
launcher is already the immediate sibling after the local Run group. It moves
the existing launcher only when placement is absent or stale. The observer
therefore survives action-bar replacement without causing a mutation loop,
and the per-document mount record continues to prevent duplicate launchers or
dialogs.

The Extensions menu command remains available as the fallback when the action
bar is absent.

## Safety boundaries

- Opening the dialog, saving settings, searching, selecting, and reviewing a
  quote remain free of Vast provider mutations.
- No live `/confirm` request, instance creation, cancellation, destruction, or
  other paid action is authorized by this work.
- Only fake/offline providers exercise confirmation behavior in automated
  tests.
- A later real rental still requires a separate explicit human GO containing
  the exact GPU, VRAM, hourly price, offer ID, configured cap, and official
  template.
- The only permitted creation template remains
  `57808457573e32120301649763d8e019`.
- No API key, bearer header, or provider response secret may appear in logs,
  browser payloads, tests, screenshots, commits, or error text.

## Test strategy

Strict red-green TDD will cover three independent regressions:

1. Python Vast-client and service tests first fail because exact lookup is
   absent and preview/confirmation still depend on a broad rotating list.
   They then verify the `ask_contract_id` request, canonical-ID check,
   eligibility normalization, exact quote preview, price-rise rejection, and
   zero create calls during review.
2. A JavaScript test first fails because a sanitized HTTP 409 error is hidden.
   It then verifies that the backend message appears in the status element and
   that malformed or missing error payloads retain a generic fallback.
3. A JavaScript test first fails after replacing the entire local action bar.
   It then verifies that the same launcher is reattached immediately after the
   new local Run group, that the observer remains active, and that no duplicate
   launcher is created.

After targeted red-green cycles, `scripts/check.sh` is the full repository
gate. Local certification may restart ComfyUI and exercise only settings,
offer search, and quote review. The paid confirmation button may be inspected
but must not be activated.

## Acceptance criteria

- A currently valid selected offer can be reviewed even when it is absent from
  the next broad top-20 search.
- An expired, changed, ineligible, or mismatched offer is rejected without
  substitution or provider mutation.
- The review dialog shows a useful sanitized backend error when review fails.
- The `Cloud Run` launcher remains beside local Run after action-bar
  replacement, with exactly one launcher and one dialog.
- Local Run behavior and selectors are unchanged.
- The deterministic repository gate passes.
- No real Vast instance is created.

## Non-goals

This change does not redesign the lifecycle, add retry policies, transfer
workflows or models, synchronize custom nodes, support another provider,
publish a Registry package, select a different offer automatically, or push
the work to any GitHub repository.

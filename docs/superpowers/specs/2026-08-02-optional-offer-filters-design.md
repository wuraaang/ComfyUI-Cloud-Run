# Optional Offer Filters Design

**Date:** 2026-08-02

**Status:** Approved behavior, pending written-spec review

## Problem

The Cloud Run panel currently requires both Maximum hourly price and Minimum VRAM to contain valid values. Clearing either field blocks Search with one generic message, even when the other filter contains a useful value such as 16 or 24 GB. The price field also rejects the French decimal comma. This makes two independent offer filters behave like one mandatory form.

## Goals

- Let either filter be left blank without blocking Search.
- Treat a blank price as no user price filter and a blank VRAM value as no user VRAM filter.
- Accept a decimal point or comma for price, including values such as `0.46` and `0,46`.
- Treat entered VRAM as a minimum, not an exact GPU size: `16` accepts available GPUs with 16 GB or more; `24` accepts 24 GB or more.
- Treat entered price as a maximum, not an exact price: `0,46` accepts available offers at or below $0.46/h.
- Let Enter in either filter trigger the same Search action as the button.
- Preserve the current provider safety filters, DLPerf ordering, paid review, and manual-destruction behavior.

## Non-goals

- Do not fabricate an offer when Vast has no currently available candidate inside the entered bounds.
- Do not silently show GPUs below the entered VRAM minimum or above the entered price maximum.
- Do not rent, confirm, or otherwise mutate a Vast instance.
- Do not change the worker release, Vast template, or release lock.

## Design

### Independent optional filters

The two visible inputs become independent:

- Blank Maximum hourly price maps internally to the existing validated broad ceiling of `$100/h`.
- Blank Minimum VRAM maps internally to the existing validated broad floor of `1 GB`.
- An entered value replaces only its own broad bound.

These sentinel bounds reuse the existing backend contract and do not require a settings-schema or provider API change. On reload, the sentinel values render as blank fields, so the interface continues to mean “no user filter” rather than displaying implementation details.

### Directional approximate matching

“Approximately” means matching the available catalog in the safe direction:

- VRAM is a floor. A request for 23 GB may return a 24 GB GPU, but never a 16 GB GPU.
- Price is a ceiling. A request for $0.46/h may return $0.42/h, but never $0.50/h.

The existing hard provider gates remain unchanged. Eligible results continue to rank by DLPerf first, then the existing connection, reliability, disk, price, and stable-ID ordering. If no eligible offer exists inside the selected bounds, the panel says so without weakening them.

### Tolerant input and direct search

Price parsing trims whitespace and accepts exactly one decimal representation with either `.` or `,`. It rejects mixed separators, currency text, non-finite values, and values outside the backend's existing `0.01` to `100` range. VRAM remains a whole number from `1` to `1024` when present.

Validation messages identify the failing field instead of reporting that both are invalid. Examples explain the accepted format:

- `Price must be blank or a number such as 0.46 (0,46 also works).`
- `VRAM must be blank or a whole number such as 16 or 24.`

Pressing Enter in either filter invokes the existing Search button. Search still requires the free dependency preflight and still saves the effective bounds before requesting offers; no paid action occurs.

## Verification and rollout

Focused UI regressions will prove blank/blank, `24` with blank price, `0,46` with blank VRAM, field-specific invalid input, Enter-to-search, and unchanged strict bounds. The relevant JavaScript suite and syntax/diff checks run before commit. The local ComfyUI Desktop backend is then restarted and its served extension asset is checked for the new optional-filter behavior. Fresh Vast inventory and the local ComfyUI queue must remain zero throughout.

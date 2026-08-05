# Randomized seed profile revalidation fix

**Date:** 2026-08-03
**Status:** Approved by the user

## Problem

Immediately before paid confirmation, the controller captures the current
canvas again. A native ComfyUI `KSampler` configured with
`control_after_generate=randomize` changes both:

- the executable prompt `inputs.seed`; and
- the matching workflow widget seed stored in `bootstrap/current.json`.

The execution baseline correctly normalizes the executable seed, but the safe
Desktop profile still hashes the volatile workflow widget value. The profile
therefore receives a new revision and manifest digest. Paid revalidation treats
that digest change as a profile change and records `quote_expired`, even though
the dependencies and executable behavior are unchanged.

The first observed live attempt failed at this boundary before provider
creation. Its session had no instance and reported
`billing_may_continue=false`.

## Required behavior

Two captures that differ only in the generated value of an official, active
Comfy core `KSampler` seed whose control mode is `randomize` must produce the
same bootstrap profile identity. Paid confirmation must still be rejected for:

- a fixed-seed change;
- a prompt parameter or graph change;
- a model, custom-node, UI-package, setting, palette, background, or saved
  workflow change;
- an ambiguous, inactive, custom, malformed, or uncertified seed control.

The executable prompt is never rewritten. Native Run on `ComfyUI Vast` remains
authoritative and generates the real random seed on the pod.

## Chosen design

Add a capture helper that returns a deep-copied bootstrap workflow. It reuses
the same strict pairing policy as the certified execution baseline and replaces
only the volatile workflow widget seed of each certified randomized KSampler
with a valid deterministic numeric bootstrap value.

The local runtime resolver passes this canonical bootstrap workflow to the
safe Desktop profile store. All other workflow fields and all other profile
files remain byte-significant. The original capture and executable prompt stay
unchanged.

This keeps content addressing honest: the archive digest still describes its
exact bytes. It also avoids weakening paid revalidation or mutating a quote at
the final paid boundary.

## Rejected alternatives

1. Ignore the bootstrap profile hash during confirmation. This is too broad and
   could provision a stale canvas after a real UI change.
2. Replace the reviewed quote manifest with the latest manifest during
   confirmation. This preserves the transient displayed seed but adds an
   unnecessary state transition and race at the provider-create boundary.

## TDD and verification

The regression test must first fail with two otherwise identical captures in
which both native seed representations change. It must then pass after the
minimal implementation. Companion tests must prove that fixed seeds and other
workflow changes remain significant and that the original capture is not
mutated.

After targeted tests pass, run the repository's complete offline certification
twice, `git diff --check`, and the worker artifact digest comparison. Commit the
implementation separately from this design.

## Deployment boundary

The local fix does not authorize a push, worker release, Vast template change,
Desktop restart, provider probe, rental, destruction, or paid test. Any rollout
requires a new precise human GO after local certification.

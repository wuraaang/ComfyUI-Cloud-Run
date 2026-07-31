# Deterministic Destroy Review Token Design

Status: approved corrective design for the public-publication gate.

## Problem

`SessionService` currently creates its default destruction-review token with
`secrets.token_urlsafe(32)` and then validates the result with
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}`.

URL-safe Base64 can begin with `-` or `_`, while the identifier contract
requires an alphanumeric first character. The full gate therefore fails
intermittently at `SessionService.review_destroy()` even though the generated
token has sufficient entropy. A 10,000-sample diagnostic reproduced 331
rejections, all caused by leading URL-safe punctuation. No runtime or test file
had changed between the passing and failing gates.

## Required behavior

- The default review-token generator must always produce a value accepted by
  the existing identifier contract.
- The token must retain at least 256 bits of cryptographic randomness.
- The browser-visible review payload, stored SHA-256 digest, five-minute
  expiration, session-version binding, instance binding, output binding, and
  one-time consumption remain unchanged.
- An explicitly injected `review_token_factory` remains subject to the current
  strict type, length, and identifier validation. Invalid injected values must
  still fail closed.
- The correction must not add retries, logging, provider calls, fallback
  randomness, or a wider accepted-character grammar.

## Selected design

Change only the default factory from `secrets.token_urlsafe(32)` to
`secrets.token_hex(32)`.

`token_hex(32)` draws 32 random bytes and encodes them as 64 lowercase
hexadecimal characters. It therefore preserves 256 bits of entropy, always
starts with an alphanumeric character, is accepted by the existing identifier
contract, and needs no retry loop. Review tokens are opaque, so their encoded
representation is not a public compatibility contract.

The injected factory path is not normalized or repaired. This preserves the
existing fail-closed behavior for tests and callers that provide their own
factory.

## Alternatives rejected

### Prefix the URL-safe token

`"review-" + secrets.token_urlsafe(32)` would also guarantee an alphanumeric
first character and preserve entropy. It adds an unnecessary semantic prefix
to an opaque credential and retains an encoding whose leading-character
behavior caused the mismatch.

### Retry until validation succeeds

A bounded retry would mirror the Remote Worker's generated artifact IDs, but it
adds control flow and makes the review path depend on repeated randomness when
one encoding can satisfy the contract by construction. Retrying an injected
invalid factory would also change its current fail-closed behavior.

## TDD proof

Add one regression test to
`tests/python/test_session_service.py`:

1. patch the old URL-safe generator to return a value beginning with `_`;
2. patch `token_hex` to return a deterministic 64-character hexadecimal token;
3. call the real `SessionService.review_destroy()` using the existing session
   fixture;
4. assert that the review succeeds and exposes the deterministic hexadecimal
   token.

Before the production change, the service raises `DestroyConfirmationError`
because it calls the patched URL-safe generator; the regression test catches
that exception and deliberately fails with a precise assertion. After the
one-line factory change, it passes. The existing invalid factory and
destruction-confirmation tests continue to cover fail-closed validation and
one-time consumption.

Verification requires:

```sh
python3 -m unittest \
  tests.python.test_session_service.ReusableSessionTests.test_default_destroy_review_token_is_always_identifier_safe \
  -v
python3 -m unittest tests.python.test_session_service -v
scripts/check.sh
scripts/check.sh
git diff --check
```

Both complete gates must pass consecutively before the public GitHub
publication plan resumes.

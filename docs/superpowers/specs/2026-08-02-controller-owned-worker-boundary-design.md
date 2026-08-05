# Controller-Owned Remote Worker Boundary Design

**Date:** 2026-08-02  
**Status:** Approved  
**Scope:** Repair the paid Vast session boundary so the next immutable worker release can authenticate deterministically, provision the certified workflow, run it remotely, retrieve its output, and verify destruction.

## Incident evidence

The first paid run used session `24f18d6e-558c-4834-8e99-5e84fd33d001`, Vast instance `46539955`, offer `25507613`, and the private template hash `c9d083b55074441a53eff655797e0a6e`.

The evidence is consistent across the local database, the Vast API, the mapped worker port, and Vast daemon logs:

- the rental was created at 23:39:58 Europe/Paris;
- the pinned `vastai/comfy` image started pulling at 23:40:31;
- the image pull completed at 23:42:39;
- Vast reported the container running at 23:43:00;
- the mapped worker endpoint answered HTTP `401` while the instance was running;
- the token stored locally exactly matched Vast's returned `jupyter_token`, but that token was rejected at the HTTP boundary;
- no dependency transfer began and GPU utilization remained zero;
- the safety lifecycle destroyed the instance and Vast inventory was empty at 23:55:18;
- with `max_instance_creates=1`, no replacement rental was created.

The account API key is not the cause: it successfully searched offers, created the contract, read the instance, requested logs, and destroyed/reconciled the instance.

## Root cause boundary

The current controller obtains `jupyter_token` from the Vast instance record. The immutable worker gateway independently reads `JUPYTER_TOKEN` from its process environment. The implementation assumes those two provider-owned values form one stable credential. The paid run disproved that assumption.

Vast's current official base-image contract uses `OPEN_BUTTON_TOKEN` for portal bearer authentication, while its networking documentation still describes `JUPYTER_TOKEN` for Jupyter integration. Neither provider token is an appropriate project-owned worker protocol contract.

The exact provider documentation also states that request-level `env` values are merged with template `env` values and that request values win on conflicts:

- https://docs.vast.ai/api-reference/instances/create-instance
- https://github.com/vast-ai/base-image

## Decision

The local controller will own the Remote Worker boundary credential.

Before every Vast create request, it generates one independent 32-byte random value encoded as exactly 64 lowercase hexadecimal characters. It persists that value in the existing private `provider_token` session field before the network mutation. The field name remains unchanged in this focused repair to avoid a database migration; its meaning becomes “controller-owned worker boundary token.”

The create request adds exactly these runtime variables while retaining the private template's port-only environment:

```text
-e CLOUD_RUN_BOUNDARY_TOKEN=<64-lowercase-hex> -e CLOUD_RUN_SESSION_ID=<validated-session-id>
```

The template continues to contain only `-p 8765:8765`. It contains no static token, session identity, API key, workflow data, or model source. Vast merges the request environment with that template environment for the single instance.

The gateway reads only `CLOUD_RUN_BOUNDARY_TOKEN`. It passes that value only to the project-owned Caddy process. Caddy requires the same value in the bearer header, removes the header before proxying, adds the internal authenticated marker, and proxies only to the worker on `127.0.0.1:8766`. The Python worker never receives the boundary token.

The existing 32-byte `session_secret_hex` remains a separate HMAC secret. It is still delivered only by the authenticated claim request and protects post-claim protocol operations. The boundary token and HMAC secret must never be reused.

## Runtime data flow

1. The human confirms one reviewed paid quote.
2. Before the Vast `PUT`, the controller persists:
   - a fresh controller-owned boundary token;
   - a separate fresh HMAC session secret;
   - state `creating` and the immutable quote.
3. The Vast create request sends the reviewed template hash, label, disk size, boundary token environment, and expected session ID environment.
4. Recovery after an ambiguous create result adopts only the uniquely labelled instance and reuses the already-persisted boundary token. It never generates a different token for the same instance.
5. The worker bootstrap installs the immutable archive and starts the gateway.
6. The local controller derives the mapped port from the normalized instance record but ignores `jupyter_token` completely.
7. Health and claim use the persisted controller-owned token. Claim must match the injected session ID.
8. After claim, manifest, transfer, deadline, job, event, preview, and artifact calls continue to use the separate HMAC protocol.
9. Destruction clears both private secrets after Vast inventory proves the instance absent.

An explicitly authorized replacement creation uses a new boundary token persisted before the replacement `PUT`. It never reuses the destroyed instance's token. The total instance-create budget remains authoritative.

## Validation contract

The boundary token contract is exact:

```text
[0-9a-f]{64}
```

The session ID injected into Docker must satisfy the existing worker identifier contract:

```text
[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}
```

Invalid values fail locally before any Vast mutation. The generated Docker `env` fragment is deterministic and contains no quotes, whitespace inside values, shell operators, or caller-controlled variable names.

The local model, HTTP client, Vast transport, remote gateway, and tests share the same boundary-token validator and environment-variable names from `cloud_run.worker_protocol`. No fallback to `JUPYTER_TOKEN` or `OPEN_BUTTON_TOKEN` is accepted. Failing closed avoids silently returning to the provider ambiguity that caused the incident.

## Authentication failure behavior

HTTP `401` is a distinct, sanitized worker-boundary failure. A responding endpoint that rejects the exact controller-owned token is not treated as a slow model download.

`WorkerClient` raises a dedicated static authentication exception without parsing or echoing the response body. During bootstrap or recovery, `SessionService` converts it to a terminal provisioning error. The existing lifecycle then requests destruction immediately and verifies empty Vast inventory. Connection refusal, timeout, and an instance that is not yet running remain retryable until the existing boot deadline.

This prevents another fifteen-minute idle rental when the boundary configuration is wrong. No authentication failure authorizes a replacement unless the existing, separately reviewed replacement policy explicitly does so; this specific deterministic configuration failure is terminal.

## Offline proof

Implementation follows red-green TDD and must prove all of the following before publication:

- invalid tokens and session IDs are rejected before HTTP;
- the create request contains the exact merged environment fragment;
- the token and HMAC secret are both durable before the create call;
- duplicate confirmation performs one create and preserves one token;
- ambiguous-create recovery uses the original persisted token;
- a replacement rotates the token before its create call;
- instance `jupyter_token` is ignored even when present and intentionally wrong;
- the gateway and Caddy accept only `CLOUD_RUN_BOUNDARY_TOKEN`;
- the Python worker child does not receive the boundary token;
- a fake complete session reaches `ready` when Vast reports a mismatched `jupyter_token` but the controller-owned token matches;
- HTTP `401` is typed, sanitized, destroys once, and creates no replacement when the authorized maximum is one;
- public API payloads, exception strings, representations, logs, rendered templates, and committed files contain no generated secret;
- the complete `scripts/check.sh` gate passes from a clean committed tree.

## Immutable publication and local rotation

The old release and private template are historical evidence and must not be edited, overwritten, deleted, or reused as if fixed.

After the final code commit and complete verification:

1. push the exact reviewed branch head and update draft PR #3;
2. build the worker archive twice and require byte-identical artifacts and metadata;
3. create one new immutable GitHub worker release for the exact commit;
4. render and publish one new private Vast template whose `onstart` embeds that release;
5. read the template back and compare every security-relevant field;
6. preserve the previous local lock as a private rollback copy;
7. install the new `worker-release.json` atomically;
8. restart the existing ComfyUI Desktop backend once, without starting a second backend;
9. verify the new lock is loaded before any offer search.

GitHub release creation, Vast template creation, and the paid test remain explicit authorization gates. None is implied by offline implementation.

## Live acceptance

The final Gold test uses the saved workflow:

```text
/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/Wallpaper Outpaint FLUX Fill 4K.json
```

Its workflow ID is `f6a8a9d4-8763-4f73-aaae-06ee57580d9c`. The real input must remain selected. The test uses the real Cloud Run frontend capture and never posts the canvas to local `/prompt`.

The human chooses the offer and presses every paid confirmation, run, deadline, and destroy control. Use `max_instance_creates=1` and the human's saved hourly-price ceiling. For this test, honor the human's explicit choice to disable the automatic deadline through the frontend acknowledgement; the human remains responsible for timing and manual destruction. Observe the session through ComfyUI Desktop/Agent Panel and direct same-origin status endpoints.

Gold validation requires all of these outcomes:

- one Vast instance only;
- worker health returns authenticated JSON rather than `401`;
- session advances through `bootstrapping`, `provisioning`, `validating`, and `ready`;
- all manifest models and the real input transfer and verify;
- “Run current canvas on this GPU” submits one remote job;
- the workflow succeeds remotely;
- output bytes are retrieved and locally verified;
- no local ComfyUI prompt execution occurs;
- the user destroys the GPU;
- Vast inventory proves the labelled instance absent;
- timings for image start, worker readiness, dependency transfer, execution, retrieval, and destruction are recorded in the PR.

Only that complete result changes the workflow certification state from preflight-resolved to Gold-validated.

## Non-goals

- Do not redesign GPU recommendation or pricing policy.
- Do not add a provider-token fallback.
- Do not disable authentication on port `8765`.
- Do not mutate the old GitHub release or private template.
- Do not run the workflow locally.
- Do not create more than the explicitly authorized number of paid instances.
- Do not claim the fix is complete before the new immutable release passes the live Gold test and verified destruction.

# 2026-08-02 five-session Vast live audit

Status: **stopped safely; not Smoke-certified and not Gold-validated**.

This record contains no API key, boundary token, session secret, provider host,
machine identity, or public IP address. All costs are estimates computed as
hourly price multiplied by observed session lifetime.

## Frozen baseline

- Controller: `0c943fafbf632fa29d29b6d115a448dd7311b1bb`
- Worker: `76f2fff05f05b2fc7372b8cdf507d84dc794abe8`
- Worker archive:
  `844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5`
- Private template: `522713`
- Template hash: `9d6822f9429822ee9e7339a804a549da`
- Protocol: `1`
- ComfyUI core/frontend: `0.29.0` / `1.47.10`

## Paid attempts

| Attempt | Session / instance | Offer | UTC lifetime | Ready / job / image | Destruction | Estimated cost | Terminal evidence |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| S1 | `5e70ddca-b409-47b1-ad95-49830d0b7da0` / `46622835` | RTX 3060, 12 GB, `0.0755555556 USD/h` | 16:51:35–17:03:13, 697.902 s | no / no / no | `destroyed`, inventory zero | `0.014647 USD` | The Panel owner stopped at its 600 s bootstrap budget before the controller's reviewed 900 s budget; no provider error was persisted. |
| S2 | `11f1a1db-3df7-4076-9b90-93140185b4c7` / `46624160` | RTX 5060 Ti, 15.9 GB, `0.1881481481 USD/h` | 17:11:57–17:13:50, 112.496 s | no / no / no | `destroyed`, inventory zero | `0.005879 USD` | `Remote worker boundary authentication failed.` |
| S3 | `0fa1a1d2-fe49-4b2d-a3f3-297550e57e88` / `46627447` | RTX 3060, 12 GB, `0.0802962963 USD/h` | 17:53:03–18:00:05, 422.291 s | no / no / no | `destroyed`, inventory zero | `0.009419 USD` | `Remote worker boundary authentication failed.` |
| G1 | `c4c7961d-bd74-4f84-b3ce-93685e941d8f` / `46630757` | L40S, 45 GB, `0.6377777778 USD/h` | 18:37:47–18:42:17, 269.893 s | no / no / no | `destroyed`, inventory zero | `0.047814 USD` | `Remote worker boundary authentication failed.` |
| G2 | not created | none | none | no / no / no | no instance existed | `0 USD` | Preserved because the frozen worker deterministically rejects every bearer token before proxying. |

Cumulative estimated cost: approximately `0.077760 USD`. Four of the five
authorized creates were consumed. Every paid session used
`max_instance_creates=1`, had `retry_count=0`, and ended destroyed. No output
exists to hash or dimension-check, so none of the attempts is reported as a
success.

## Gold free preflight

- Workflow UUID: `f6a8a9d4-8763-4f73-aaae-06ee57580d9c`
- Capture: `8e2a41e9-763a-4d64-92b1-73cc9c2fabf2`
- Prompt digest:
  `931e8172961e2fd07397f6c4d9d75112f34189fa0d287f0d96beb8a968ec439e`
- Preflight: `2f9a5e52-b0ce-49a3-a252-633396e12b01`
- Manifest:
  `4004908b82aa155ab7fe57c45eb12d7b51980b7fab0564734d78bed65fc6689e`
- Result: rentable; five models and the real input resolved immutably;
  `29,347,469,703` transfer bytes; `88 GB` disk; `530,841,600` output
  allowance bytes; randomized seed node `3` only.

## Evidence-backed root cause

The G1 read-only observer compared the Vast `extra_env` values with the
controller row without emitting either secret. Both
`CLOUD_RUN_BOUNDARY_TOKEN` and `CLOUD_RUN_SESSION_ID` were present and matched
the controller exactly. Once Vast reported `running`, health checks made with
no token, the provider-declared token, and the controller token all returned
HTTP 401.

The published worker asset was downloaded again after the campaign. It was
exactly `57,007` bytes and its SHA-256 matched the frozen archive digest. Its
`remote_worker/Caddyfile` SHA-256 was
`9642b4ded8bfdb073a13799018e34751f0fd1913c41a519a199b0fddd52744f8`,
byte-for-byte equal to the file at the frozen worker commit.

Caddy's adapter puts the three `request_header` handlers before the matched
401 `respond` handler. Consequently it deletes `Authorization` before testing
the bearer matcher, making every request unauthorized. A local red/green
runtime reproduction against a loopback health server produced:

| Configuration | No auth | Exact bearer token |
| --- | ---: | ---: |
| Frozen Caddyfile | 401 | 401 |
| Same Caddyfile with the handlers wrapped in `route` | 401 | 200 |

The smallest source correction is therefore a `route` block preserving the
written order: reject a missing/wrong bearer first, then remove credentials,
add the internal boundary marker, and reverse-proxy to loopback.

That correction was deliberately not committed or deployed. The private Vast
template embeds the frozen archive URL, size, and digest in its `onstart`
release lock, exposes only container port `8765`, and the worker backend binds
only to `127.0.0.1:8766`. The reviewed create request can supply only the
template hash, label, disk, and the two worker environment values. Deploying
the correction therefore requires changing the worker commit, archive digest,
and private template, all explicitly prohibited by the campaign baseline.
Bypassing Caddy or injecting a runtime patch would weaken the reviewed
boundary and was rejected.

## Safe stop evidence

After G1 destruction and again during the blocker audit:

- ComfyUI queue: `0 running`, `0 pending`
- Active Cloud Run sessions: zero
- Non-destroyed campaign sessions: zero
- `billing_may_continue`: false for every completed session
- Complete Vast inventory: exactly zero instances
- Settings restored to `0.20 USD/h` and `8 GB` minimum VRAM
- Worktree was clean at controller HEAD before this evidence record

The fifth create must not be spent on the unchanged archive because it would
repeat a deterministic pre-ready failure. Continuing requires new explicit
authorization to replace the frozen worker/archive/template baseline and a
new paid-session allowance sufficient to rerun the failed Smoke and Gold
acceptance set. A sixth create remains forbidden under the current campaign.

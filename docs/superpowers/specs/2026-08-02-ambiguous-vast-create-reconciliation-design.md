# Ambiguous Vast Create Reconciliation Design

**Date:** 2026-08-02
**Status:** Approved for TDD implementation and a controlled 3 + 2 live rollout
**Scope:** Correct the Vast instance-create request contract and make every failed or ambiguous rental outcome explicit, safe, and manually recoverable before another paid confirmation.

## Goal

After the human presses the paid confirmation button, Cloud Run must always reach one of three truthful outcomes:

1. one exact Vast instance is identified and the session continues;
2. Vast definitively rejected the request and the user can choose another offer;
3. the create result is ambiguous, so Cloud Run blocks another rental while it reconciles the labelled inventory or carries out a manually requested cancellation.

No failure may trigger a second paid create automatically. No UI may claim
that billing stopped unless Vast definitively rejected the create before a
contract could exist, or the relevant Vast inventory is available and empty.

## Incident evidence

The earlier Gold attempt created Vast instance `46539955`. That request predated the controller-owned boundary injection and did not contain a request-level `env` field. Its later worker authentication failure proved that account authentication, offer acceptance, instance creation, inventory reads, and destruction could all work.

The latest attempt used session `ec0a4cbb-a51f-417b-8e7c-64bebbe77054` and offer `21212913`. The controller issued one create request, received no usable `new_contract`, and found no labelled instance during repeated inventory reads. The offer remained visible after the failure, so an unavailable offer is not the supported explanation. The session nevertheless remained `creating` for the 15-minute worker boot deadline and finally displayed `The authorized total instance-create limit was reached.`

The precise transport failure cannot be recovered. `cloud_run.vast.create_instance()` currently replaces the original exception type and message with one generic `VastError`, and `CloudRunService.confirm_session()` replaces every retryable create error with the same public text.

The review also found a request-contract mismatch introduced with the worker
boundary. The current controller sends instance `env` as a Docker-flag string.
Vast's current API workflow guide documents `env` as a JSON object, its
official CLI parses human Docker-flag syntax into an object before
`PUT /api/v0/asks/{offer_id}/`, and the CLI's current `InstanceConfig` model
types `env` as `dict[str, str]`. The controller will therefore follow that
object contract:

```json
{
  "env": {
    "CLOUD_RUN_BOUNDARY_TOKEN": "<64-lowercase-hex>",
    "CLOUD_RUN_SESSION_ID": "<validated-session-id>"
  }
}
```

The private template continues to store its fixed port mapping in template string form. Vast merges the request-level object into the template environment. No token or session identity is added to the template.

Authoritative references:

- <https://docs.vast.ai/api-reference/instances/create-instance>
- <https://docs.vast.ai/api-reference/creating-instances-with-api>
- <https://github.com/vast-ai/vast-cli/blob/bc7356483dd0f922ee17975c5357741f0d5d81fc/vast.py#L2477-L2534>
- <https://github.com/vast-ai/vast-cli/blob/bc7356483dd0f922ee17975c5357741f0d5d81fc/vastai/data/instance.py#L6-L31>

The sources are not unanimous: the generated create-endpoint reference still
describes `env` as a Docker-flag string. That contradiction is recorded rather
than hidden. The implementation follows the object form used by the workflow
guide and current official client, pins the client revision in its contract
test, and relies on a single manually authorized smoke rental for live
confirmation. No unauthorised create is used to probe the discrepancy.

This request mismatch is a plausible cause of the latest failure because that
was the first live create after request-level boundary injection. It is not
claimed as the proven incident cause because the original exception was
discarded.

## Decisions

### 1. Send the documented create payload

`_worker_environment()` returns an exact two-key dictionary rather than a string. Both values are validated before transport. The dictionary contains no port, quote, model, input, API key, or caller-controlled variable name.

The create request retains only the reviewed template hash, exact managed label, reviewed disk size, and boundary dictionary. The template, release, and local lock already in use remain immutable and are not recreated for this controller-only repair.

### 2. Preserve a safe failure classification

`VastError` carries a stable non-secret code in addition to its static public message:

- `configuration_rejected` for HTTP 400;
- `api_key_rejected` for HTTP 401/403;
- `offer_unavailable` for HTTP 404/410;
- `rate_limited` for HTTP 429;
- `retryable_http` for HTTP 408/409 and 5xx;
- `timeout`, `connection`, `tls`, or `server_disconnected` for recognized client transport failures;
- `invalid_response` for an unusable successful response;
- `confirmation_interrupted` for a recovered pre-mutation confirmation;
- `transport_unknown` only when no narrower safe classification exists.

Raw response bodies, headers, exception messages, URLs, tokens, and credentials are never persisted, returned, or logged. Tests assert that hostile provider markers cannot escape through `str`, `repr`, API payloads, or UI text.

HTTP 400, 401, 403, 404, and 410 are definitive failures and do not enter
create reconciliation. HTTP 408, 409, 429, 5xx, every recognized transport
failure, an invalid HTTP 200 response, and `transport_unknown` are ambiguous:
the provider may have accepted the mutation even though the controller cannot
identify the contract.

The safe code is persisted as `failure_code` so restart and UI rendering do not
depend on matching an English error string.

### 3. Separate create reconciliation from worker boot

Add durable session state `reconciling_create`. It means one paid create was attempted but no contract ID was received and current inventory does not yet establish the outcome.

It is not a worker boot state. It never enters `handle_session_boot_failure()`, never blacklists a host, never consumes a replacement budget, and never searches or creates another instance.

The transition is:

```text
offer_selected
  -> confirming
  -> creating
     -> bootstrapping          one exact contract ID returned or reconciled
     -> failed                 definitive rejection
     -> reconciling_create     response outcome ambiguous
```

`confirming` is durably pre-mutation: it contains no provider token, session
secret, instance identity, or reconciliation evidence. The transition to
`creating`, including both generated secrets, commits before the network call.
On startup, an orphaned `confirming` record therefore becomes `failed + absent`
with `failure_code=confirmation_interrupted`; it never resumes the paid create
without another fresh review and click.

### 4. Use one bounded, restart-safe reconciliation policy

The minimum empty-evidence span is 120 seconds. Reconciliation age is measured
from the durable transition into `reconciling_create`, but terminal absence
requires at least three successful empty inventory snapshots whose first and
last observations are themselves at least 120 seconds apart. Polling uses the
existing server watchdog with increasing intervals and does not treat HTTP 429
or an unavailable inventory as an empty result.

With a healthy inventory endpoint, checks target 0, 15, 30, 60, and 120
seconds from the first successful empty snapshot, so the normal actionable
result arrives just after two minutes. Concurrent browser refreshes share the
durable result and do not create parallel polling bursts. If inventory remains
unavailable, checks continue no more often than once per minute until the user
can be given a truthful outcome.

The repository persists this private reconciliation evidence:

```text
create_reconcile_started_at: float | null
create_empty_observations: integer
create_first_empty_at: float | null
create_last_empty_at: float | null
failure_code: safe enum | null
```

The database migration adds nullable/defaulted columns and leaves historical
sessions readable. Observation count is bounded and increments only after a
successful full inventory response. Verified absence requires all three of:

1. at least three successful empty observations;
2. `create_last_empty_at - create_first_empty_at >= 120`;
3. the last observation occurred after reconciliation began.

The bounded count may saturate at three, but every later successful full empty
snapshot updates `create_last_empty_at`. A "full empty snapshot" is deliberately
strict: every paginated response must have `success=true`, an `instances` list
whose every row validates, `instances_found == len(instances)`, one coherent
non-negative integer total across all pages, and a terminal null `next_token`.
Any duplicate contract ID across pages, repeated token, malformed row,
inconsistent count/total, or partial page makes inventory unavailable. At the
terminal token, both the sum of page `instances_found` values and the number of
unique normalized contracts must equal `total_instances`. Exact global zero
requires an empty accumulated list, both counts zero, and terminal null token.
No filtered, duplicated, short, partial, or malformed response can contribute
empty evidence.

Three rapid reads after a long process outage therefore cannot manufacture a
120-second evidence window. This evidence is not exposed as provider data and
is cleared when the session adopts an instance, reaches verified absence, or
is destroyed.

Each inventory result has one meaning:

- exactly one matching label: adopt its contract ID and continue at `bootstrapping`;
- more than one matching label: fail closed, retain every residual ID, and show the emergency destruction action;
- no match before the full evidence span: remain `reconciling_create`;
- elapsed time without sufficient successful snapshots: remain `reconciling_create`;
- successful empty evidence satisfying the full span: transition to `failed` with rental outcome `absent`, clear both private session secrets, and permit a fresh offer search;
- inventory unavailable: remain ambiguous, keep polling with backoff, and do not permit another rental.

The empty terminal message says that no active instance was detected for this session. It does not claim that an instance never existed.

Startup recovery applies the persisted evidence instead of discarding it. A
process crash can leave `creating` durable while the network call has no
surviving waiter. Recovery treats that state as ambiguous: it reconciles the
label, and if inventory is empty it enters `reconciling_create` using the
original `creating.updated_at` as `create_reconcile_started_at`. The 120-second
proof still depends on the persisted first and last successful empty reads,
not merely on elapsed wall time.

### 5. Make cancellation explicit and durable

While the result is ambiguous, the human may press the existing destruction control. The review must say that no instance is currently identified but that a delayed matching instance will be destroyed if it appears. The confirmed click durably records `destroy_requested=true`.

The click may race with the original `PUT`. The state transition and completion
rules are atomic:

- if create returns a contract after `destroy_requested` is durable, the
  controller attaches that ID only to the destruction path and destroys it
  exactly once;
- if create returns a definitive rejection, the session finalizes `destroyed`
  with no provider delete because the rejection proves no contract was made;
- if create becomes ambiguous, the session remains `destroy_requested` and
  stores reconciliation evidence there; it does not make an illegal transition
  back to `reconciling_create`;
- duplicate HTTP completions, refreshes, or destroy requests never produce a
  second create or a second required user confirmation.

After that authorization:

- a unique late matching instance is destroyed and absence is verified;
- multiple matches produce the existing residual-inventory emergency state;
- a qualifying sequence of empty inventory results finalizes `destroyed` and clears secrets;
- unavailable inventory keeps the session nonterminal and visibly uncertain.

The controller never uses a create call as a cancellation mechanism.

### 6. Expose a typed public rental outcome

The public session payload includes:

```text
rental_outcome: not_started | unknown | active | absent
failure_code: safe enum | null
can_search_offers: boolean
can_destroy: boolean
can_verify_vast_access: boolean
billing_may_continue: boolean
```

The values are derived from validated durable state, instance identity, residual inventory, and verified absence. They are not inferred from human-readable error text.

- `not_started`: no provider mutation has begun;
- `unknown`: a create or destroy outcome is still being reconciled;
- `active`: an exact or residual Vast contract may bill;
- `absent`: definitive pre-create rejection, verified empty create reconciliation, or verified destruction.

`billing_may_continue` is true for `unknown` and `active`, and false only for `not_started` or `absent`. This removes the current contradiction where `creating` says billing may have started while the payload reports false.

The exact capability matrix is:

| Durable condition | Outcome | Billing may continue | Search capability | Destroy capability |
|---|---|---:|---:|---:|
| `preflight` or `offer_selected` | `not_started` | false | true, subject to a current rentable preflight | false |
| live `confirming` before the durable create intent | `not_started` | false | false | false |
| `creating` or `reconciling_create` | `unknown` | true | false | true |
| identified instance from `bootstrapping` through `harvesting` | `active` | true | false | true |
| `destroy_requested` or `destroying` | `unknown` or `active` from inventory evidence | true | false | false while the request is already pending |
| `failed` with an instance or residual inventory | `active` | true | false | true |
| `failed` because of 400 | `absent` | false | false until a verified controller/config repair, any authorized reload, and a new campaign authorization | false |
| `failed` because of 401/403 | `absent` | false | false until corrected settings and a successful authenticated full inventory read | false |
| `failed` because of 404/410 | `absent` | false | true with a current rentable preflight | false |
| recovered `failed + confirmation_interrupted` | `absent` | false | true with a current rentable preflight | false |
| `failed` after the complete empty-evidence window | `absent` | false | true with a current rentable preflight | false |
| `destroyed` after verified absence | `absent` | false | true with a current rentable preflight | false |

The actual Search button requires both the session capability and the current
frontend `preflightId`. No capability is inferred from `error` text.

`can_verify_vast_access` is true only for terminal `failed + absent +
api_key_rejected` without remediation evidence. It is false for 400, 404/410,
unknown/active, and already remediated sessions. The explicit read-only button
is rendered solely from this capability, never by matching `failure_code` or
message text.

### 7. Give the user one unambiguous next action

During ambiguous creation, the UI displays:

```text
GPU rental could not be confirmed. Cloud Run is checking Vast inventory.
Do not start another rental yet.
Billing status is not yet known; Vast may have created an instance.
```

Search, offer review, and paid confirmation are disabled. Polling and the reviewed destruction control remain available.

The backend enforces the same paid-mutation gate. Before any create, one SQLite
transaction scans every other session, including terminal records, and rejects
the confirmation if any record derives rental outcome `unknown` or `active`,
has an attached instance ID, or retains residual inventory. A terminal
`failed` record with provider evidence therefore still blocks a rental. This
check is repository-backed and cannot be bypassed by calling the route
directly. Read-only inventory reconciliation remains allowed.

After verified absence, the UI displays:

```text
GPU rental failed. Vast inventory confirms that no instance is active for
this session. No Vast billing is active. Search again and choose another GPU.
```

The destruction and old confirmation controls are hidden. `cloud-run.js` clears
`selectedOffer`, `sessionIdempotencyKey`, the rendered offer list, and the
review control. `session-console.js` clears its internal idempotency key, hides
the quote/confirm panel, and clears any pending destroy review. `Search Vast
GPUs` is enabled from the still-valid resolved preflight; the next create
requires a new offer, review, idempotency key, and manual confirmation.

On dialog reload, a terminal `absent` session is rendered as the last failed
result, not as an active paid session. Session selection prioritizes `unknown`
or `active` records; an `absent` record never produces the generic
"billing ends only after verified destruction" banner.

`SessionRepository.list_recoverable()` excludes `failed + absent`, while
retaining every `unknown` or `active` record for watchdog recovery. The settings
payload exposes sanitized `active_sessions` from that recoverable set and a
separate `recent_sessions` history from a bounded `list_recent(limit=20)`
query. This preserves a terminal failure across dialog reload without loading
unbounded history or repeatedly running provider recovery against it.

The browser-facing offer search omits provider machine ID, host ID, and public
IP. The backend revalidates the selected offer and retains those identities
privately in the reviewed quote for anti-switch checks and sanitized campaign
diversity evidence.

Definitive errors retain their actionable reason. An unavailable offer says to search again; a rejected API key or configuration says to correct settings rather than misleadingly suggesting another GPU.

For a terminal absent configuration or API-key failure,
`can_search_offers=false` remains false on that historical session record. A
successful workflow preflight alone cannot prove that provider credentials or
the create payload were repaired and must not unlock another rental. A 401/403
gate requires corrected settings plus a successful authenticated read-only
full inventory. A 400 gate requires a demonstrated controller/configuration
repair, offline verification, any separately authorized restart needed to load
it, and a new paid-campaign authorization. The failed record remains in
`recent_sessions`; no frontend selection reset can bypass these gates.

These are backend gates, not merely UI advice. Each paid create records the
private API-key settings revision and `VAST_CREATE_CONFIGURATION_REVISION` used.
A terminal 400/401/403 record derives `blocks_new_rental=true` until typed,
durable remediation evidence is attached:

- updating the API key creates a new private settings revision; an explicit
  free `Verify Vast access` action must then complete one strict full inventory
  read with that revision before the repository may mark an older 401/403
  failure remediated;
- a 400 can be remediated only when a later controller with a deliberately
  changed create-configuration revision is actually loaded. Startup records
  that loaded revision, and may mark an older differing 400 revision remediated
  only after the repair passed the offline gate and the restart was separately
  authorized operationally.

The SQLite remediation update verifies terminal absence, cleared secrets, no
instance/residual IDs, the expected failure code, and a revision strictly
different from the failed one. `claim_create_intent()` scans every unremediated
blocker in its same `BEGIN IMMEDIATE` transaction. Search, preflight, browser
reload, and a direct confirm route cannot synthesize this evidence. Resuming
after either failure still needs a new exact paid-campaign GO.

### 8. Remove automatic paid replacement from this flow

Every new Vast create requires a fresh human confirmation. An ambiguous create, worker boot failure, authentication failure, or unavailable offer cannot consume a second create automatically.

New reusable sessions use a fixed create limit of one. Historical records with a limit of two remain readable, but the lifecycle does not use that value to issue a replacement. A second attempt is a new reviewed session created from a fresh offer and explicit click.

Both legacy replacement entry points, `handle_start_failure()` and
`handle_session_boot_failure()`, become destruction/terminalization paths only.
Recovery after restart cannot resurrect their former automatic offer search or
create behavior.

Every transition to rental outcome `absent`, including definitive rejection
after secrets were persisted in `creating`, clears `provider_token` and
`session_secret_hex` atomically.

This preserves the user's paid-action boundary and makes the previous `authorized total instance-create limit was reached` message irrelevant to create reconciliation.

### 9. Bind every counted job to its certified executable canvas

Raw workflow-file SHA, canonical parsed-workflow SHA, executable-baseline
digest, and prompt digest are four different identities. The first two freeze
the imported file. The executable-baseline digest hashes the compiled `output`
and queue options after normalizing only a backend-derived set of core
`KSampler` seed inputs whose workflow control is exactly `randomize`. The hash
envelope includes that sorted node-ID set, so enabling/disabling randomization
or changing any other executable input changes the baseline. The prompt digest
remains the exact unnormalized prompt for one capture/job and may differ between
two valid randomized runs.

Rentable preflight persists the executable-baseline digest and normalized seed
node set into the reviewed quote and session. `Run current canvas` still makes
one fresh capture, but before any remote job or manifest mutation the backend
recomputes the baseline and requires exact equality with the reviewed session.
For this campaign the smoke set is empty and the Gold set is exactly node `3`.
The fresh job then stores its own exact prompt digest. The evidence helper
recomputes the baseline from `job.capture_json`, checks it against the frozen
session value, and checks the job prompt digest against the same unnormalized
capture. Editing any node, model, text, input, parameter, queue option, or seed
mode after preflight therefore fails before remote submission; only the numeric
seed at an already certified randomized core KSampler may vary.

Harvesting persists each output descriptor's validated source node together
with the published file device/inode, size, and SHA-256. Live evidence opens the
SQLite database read-only, selects by exact session/job/prompt/baseline/node,
rehashes the same regular file under the approved output root, and for Gold
resolves the frozen source from the exact manifest `local-upload` artifact and
local artifact catalog under the approved input root. A newest-file or
caller-chosen output path is never acceptance evidence.

## Test contract

Implementation follows red-green TDD.

### Vast transport

- the request-level `env` is exactly the two-key object and never a string;
- malformed boundary values fail before HTTP;
- 400/401/403/404/410 remain definitive and specific;
- 408/409/429/5xx and recognized transport failures receive stable safe codes;
- no provider body, exception message, or secret appears publicly.
- inventory is usable only after every page, row, count, total, token, and
  success marker validates; a partial or malformed snapshot never proves zero.

### Durable model and repository

- `reconciling_create` transitions are explicit and validated;
- the state survives SQLite round trips and controller restart;
- reconciliation start, successful-empty count, last-empty time, and safe
  failure code survive SQLite round trips;
- first-empty time survives SQLite round trips and prevents rapid post-restart
  reads from satisfying the evidence span;
- `rental_outcome`, capabilities, and billing status match each state;
- terminal verified absence clears boundary and HMAC secrets.
- reviewed sessions persist one executable-baseline digest and the exact
  backend-derived randomized-seed node set.

### Service and lifecycle

- ambiguous create plus immediate empty inventory issues one create and enters reconciliation;
- restart with an orphaned pre-mutation `confirming` session terminalizes it as
  `confirmation_interrupted` and issues zero creates;
- duplicate confirmation issues no second create;
- a different session cannot confirm while any session is `unknown` or
  `active`, even through a direct route call;
- terminal failed sessions with an attached instance or residual inventory also
  block the atomic create claim;
- unremediated 400/401/403 failures block direct and UI confirmation; only a
  newer typed configuration revision or a newer settings revision with strict
  authenticated inventory proof lifts the matching blocker;
- one late match is adopted with the original boundary token;
- multiple matches retain residual IDs and emergency action;
- empty evidence before 120 seconds remains ambiguous;
- three qualifying empty reads spanning 120 seconds become terminal and actionable;
- unavailable or rate-limited inventory never counts as empty;
- restart preserves every observation and cannot shorten the evidence span;
- cancellation racing an in-flight create adopts a late ID directly into one
  verified destruction and never returns to a rental state;
- manual cancellation destroys a late unique match and verifies absence;
- all definitive 400/401/403/404/410 paths clear both private secrets;
- neither `handle_start_failure()` nor `handle_session_boot_failure()` searches
  or creates a replacement, including attempt and session records with
  historical `max_instance_creates=2`.
- a fresh Run capture with any non-seed executable change is rejected before a
  worker job, while only a numeric seed change at the certified randomized core
  KSampler preserves the executable baseline.
- output evidence rejects wrong job/node/baseline provenance, path escape,
  symlink or device/inode replacement, digest change, and ambiguous Gold input.

### Frontend

- ambiguous status is red, blocks search/review/confirm, and keeps Destroy available;
- verified absence shows the exact safe message, hides Destroy/Confirm, enables search, and clears the old selection and idempotency key;
- an inventory outage never displays `No Vast billing is active`;
- all provider-derived values remain inert `textContent`.
- repeated preflight never clears a configuration/API gate; the explicit
  read-only access verification and loaded-repair evidence drive the narrow
  recovery UI.
- the Verify Vast access button appears only from
  `can_verify_vast_access=true`, sends no provider data, and a failed/429 check
  changes no capability.

### Complete offline gate

Focused tests run first. The final committed controller repair must pass `scripts/check.sh` once from a clean worktree. Passing offline tests means controller-ready, not Gold-validated.

## Rollout and live acceptance

This repair changes the local controller and frontend only. It does not require a new worker release or a second Vast template. The existing release, private template `522713`, template hash, and local worker lock remain untouched.

After a clean offline gate, restarting the existing ComfyUI Desktop backend
requires a new explicit authorization. Before any paid action, the human must
also approve exact numeric limits for:

- maximum hourly price for the three lightweight sessions;
- maximum hourly price for the two FLUX sessions;
- maximum duration per session or one explicit total campaign budget.

The extension keeps a fixed limit of one instance create per reviewed session.
The human selects, reviews, confirms, runs, and destroys every session from the
ComfyUI interface. An agent may observe read-only state and recommend an offer,
but it never calls the create or destroy API and never clicks a paid control.

The rollout is a stop-on-first-failure sequence with one instance at a time:

1. three lightweight core-output sessions use a committed native
   `EmptyImage -> SaveImage` canvas with no models, preferably on three
   different `machine_id`/`host_id` pairs;
2. two full sessions use the exact Wallpaper Outpaint FLUX Fill 4K workflow,
   preferably on two further distinct hosts, with all five native model
   records resolved by preflight;
3. each session must reach controller-authenticated `ready`, execute the
   current canvas, return a newly harvested and independently verified image,
   receive a manual `Destroy GPU` click, become terminal, and finish with a
   successful full Vast inventory read containing zero instances;
4. a failed or ambiguous session does not count and exhausts the current
   attempt authorization. The campaign pauses for evidence-driven diagnosis,
   an offline repair when required, full re-verification, and a new exact human
   authorization for any additional attempt before it can continue. Earlier
   successful rows survive only while controller code, immutable assets,
   workflows, and inputs remain unchanged; otherwise the success counter
   resets.

Distinct provider placement is preferred evidence, not an excuse to weaken a
price or safety bound. If no new eligible pair exists under the accepted cap,
the campaign pauses and asks the human whether to authorize a duplicate. The
final report states the actual number of distinct placements and the total
paid attempts, while acceptance still requires five counted successes.

The three lightweight successes validate the create contract, labelled
reconciliation, worker authentication, current-canvas submission, harvesting,
and destruction without repeatedly transferring the approximately 29.35 GB
FLUX model set. The two final runs validate the real model metadata, transfer,
provisioning, inference, 4K output, and cleanup path. Five allocations alone
are not acceptance evidence.

Gold remains failed until the second real FLUX image is returned correctly,
all five successful-session evidence records are complete, the final session
is terminal, and a fresh Vast inventory read proves zero instances.

## Non-goals

- Automatically retrying a create or choosing another offer.
- Recreating or modifying the existing Vast template.
- Replicating Vast's own CLI or logging raw provider responses.
- Claiming a precise historical transport cause that the old code discarded.
- Redesigning GPU/VRAM recommendation or pricing policy.
- Claiming Gold validation from offline tests or instance creation alone.

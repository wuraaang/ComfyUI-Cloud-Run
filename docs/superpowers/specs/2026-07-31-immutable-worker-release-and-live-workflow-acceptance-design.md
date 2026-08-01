# Immutable Worker Release and Live Workflow Acceptance Design

Date: 2026-07-31

Status: approved through the original offline implementation and the
2026-08-01 connection-quality amendment. This document does not itself
authorize implementation, a GitHub release, a Vast template mutation, an
offer search, or a paid instance.

## Relationship to the existing designs

This design is a narrow continuation of:

- `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`;
- `docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md`;
- `docs/remote-worker-bootstrap-review.md`.

Those documents remain authoritative for canvas capture, dependency
resolution, worker authentication, provisioning, execution, deadlines,
verified outputs, and destruction. This continuation resolves the remaining
release bootstrap contradiction, makes the human instance-count authorization
machine-enforceable, defines a reusable connection-quality policy for every
workflow-derived Vast search, and defines the first real remote acceptance
run.

The older documents call the manual acceptance run `Gold`. In this continuation
it is called the **live workflow acceptance** or **live goal test** to avoid
confusion with speech-to-text transcription. Existing test and file names are
not renamed merely for terminology.

## Current proven state

At the start of this design:

- worktree: `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`;
- branch: `feat/vast-cloud-run-lifecycle`;
- required implementation ancestor:
  `1fc5ddc8e4e6569bb6af8642ee84846f91ca11b0`;
- GitHub source: `wuraaang/ComfyUI-Cloud-Run`;
- draft pull request: `#2`;
- the branch and GitHub head both point to the required ancestor;
- the complete offline gate passes at that ancestor;
- no live worker release, project template, private release lock, real GPU
  rental, or live workflow execution exists;
- GitHub immutable releases are currently disabled for the repository;
- the real private workflow input was not present in the isolated proof
  environment, so its synthetic substitute is not acceptable for the paid
  acceptance run;
- the five public model identities are proven, but their annotations were
  added only to the in-memory proof canvas and must be checked again on the
  actual live canvas.

## Problem

The deterministic worker builder produces a small, normalized archive that
contains only reviewed worker files. The current bootstrap instead accepts a
GitHub source-archive URL. GitHub answers that URL with a redirect to
`codeload.github.com`, and the resulting archive contains the complete
repository with GitHub-generated root prefixes and PAX metadata. The bootstrap
correctly rejects both the redirect and the archive shape.

Consequently, pushing source is not sufficient to boot a paid worker. Relaxing
the extractor to accept arbitrary repository archives would enlarge the supply
chain and discard the value of the deterministic worker-only builder.

A second mismatch exists at the paid authorization boundary. The lifecycle can
perform one automatic replacement after a boot-host failure. A human
authorization of “maximum one instance” therefore needs a persisted,
machine-enforced total-create limit; a conversational instruction alone is not
enough.

Finally, the first live test must use the actual workflow and actual private
input. The earlier 206-byte synthetic PNG explains the difference between the
five-model total of `29,347,330,907` bytes and the recorded synthetic proof
total of `29,347,331,113` bytes. Neither the previous total nor its disk
estimate may be reused for the real input.

## Considered release approaches

### Selected: deterministic immutable GitHub Release asset

Publish the exact output of `scripts/build_worker_artifact.py` as an asset on a
GitHub immutable release. The asset name, release tag, requested URL, template
lock, exact size, and SHA-256 all bind the worker commit.

This keeps extraction limited to the already reviewed worker-only archive,
avoids a second repository or container registry, and lets GitHub protect the
tag and asset from modification after publication. GitHub documents both
immutable releases and release-asset digest verification:

- https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases
- https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity

### Rejected: accept the complete codeload source archive

This would require accepting GitHub-generated metadata, a repository root
prefix, and every unrelated source file before filtering the reviewed worker
set. Exact size and digest would prevent substitution, but availability would
remain tied to generated archive bytes rather than the deterministic artifact.
It is a larger and less comprehensible bootstrap contract.

### Deferred: custom worker container image

A purpose-built image could contain the worker and gateway without a runtime
archive download. It would introduce a container build, registry publication,
image-digest policy, base-image rebuild process, and another release surface.
That is appropriate only after the first release-asset acceptance proves the
runtime contract.

## Immutable release identity

For a worker commit `<commit>` and deterministic archive digest `<sha256>`, the
release identity is exactly:

```text
tag:
worker-v1-<commit>

asset:
comfyui-cloud-run-worker-<commit>-<sha256>.tar.gz

browser download URL:
https://github.com/wuraaang/ComfyUI-Cloud-Run/releases/download/worker-v1-<commit>/comfyui-cloud-run-worker-<commit>-<sha256>.tar.gz
```

`<commit>` is 40 lowercase hexadecimal characters and `<sha256>` is 64
lowercase hexadecimal characters. No `latest`, branch name, shortened commit,
mutable tag, query string, alternate owner, alternate repository, or caller
provided filename is accepted.

The publication sequence is draft first, asset upload second, download and
byte verification third, and immutable publication last. Publication stops if
repository-level immutable releases are not enabled. GitHub currently exposes
that setting through `GET` and `PUT
/repos/{owner}/{repo}/immutable-releases` using API version `2026-03-10`.

The published release is verified with both:

```text
gh release verify worker-v1-<commit>
gh release verify-asset worker-v1-<commit> <downloaded-asset>
```

The GitHub API asset `size` and `digest` must also equal the locally built
values. GitHub documents `browser_download_url`, asset size, and `sha256:`
digest in its release-assets API:
https://docs.github.com/en/rest/releases/assets.

## Bootstrap redirect contract

The bootstrap continues to disable proxies and automatic redirects. It makes
one request to the exact browser download URL from the reviewed lock.

The only accepted responses are:

1. direct HTTP `200` from the exact requested `github.com` URL; or
2. one HTTP `302` whose absolute `Location` is HTTPS, has no user information,
   no explicit port, no fragment, and uses exactly
   `release-assets.githubusercontent.com`, followed by one direct HTTP `200`.

The second request also uses a no-redirect opener. A second redirect, relative
location, other status, other host, user information, explicit port, fragment,
non-identity content encoding, empty stream, oversize stream, size mismatch, or
digest mismatch rejects the release.

The temporary signed target exists only inside the transport call. It is not
returned in `DownloadStream`, persisted, logged, embedded in an exception,
sent to the browser, written into the worker state, or copied into the local
release lock. `DownloadStream` exposes only the original reviewed source URL,
a redirect count of zero or one, and the byte iterator.

The archive extraction contract remains unchanged: one gzip member, normalized
regular files and directories, bounded member and expanded sizes, no PAX,
sparse member, link, device, traversal, duplicate, unsafe mode, unknown file,
or layout mismatch.

## Stable template bootstrap

The project-specific Vast template is private and derived from the exact
official ComfyUI base-template hash
`027fba7753c024be019030fb42aed900`. Vast documents template creation and its
content-derived `hash_id` at:
https://docs.vast.ai/api-reference/templates/create-template.

Before template creation, a read-only lookup must return exactly one base
template with that hash. The reviewer records sanitized image, tag, runtime,
port, and launch fields. A missing, duplicate, secret-bearing, mutable, or
unexpected base result stops publication.

A deterministic repository script generates, into a private temporary
directory only:

- the exact worker release asset;
- sanitized release metadata;
- the remote bootstrap lock containing asset URL, commit, size, digest,
  protocol and version pins, and `/opt/comfyui-cloud-run` destination;
- the fixed template `onstart` payload;
- the private-template request body.

A second focused repository script is the only allowed authenticated template
transport. It uses the documented `GET` and `POST`
`https://console.vast.ai/api/v0/template/` operations, with exact
`select_filters`, `select_cols`, and `order_by` values for lookup. It accepts
the Vast API key only from the process environment or the existing
owner-private credential source, never from argv, and never prints a request,
response, authorization header, key, `onstart`, or base64 body. HTTPS is
fixed, redirects and ambient proxies are disabled, time and response sizes are
bounded, and responses are normalized into a small typed schema before use.
The script has no update, delete, arbitrary-URL, arbitrary-method, or generic
payload surface. Creation is attempted at most once. An ambiguous POST result
is followed only by one exact-name read-only reconciliation; it is never
blindly retried. These contracts follow Vast's documented template search,
creation, and API workflow:

- https://docs.vast.ai/api-reference/search/search-templates
- https://docs.vast.ai/api-reference/templates/create-template
- https://docs.vast.ai/api-reference/creating-and-using-templates-with-api

The fixed `onstart` payload contains the reviewed `remote_worker/bootstrap.py`
bytes and remote lock encoded as base64 constants. It creates one private
bootstrap directory with mode `0700`, writes both files with mode `0600`, sets
only the reviewed worker commit, and executes:

```text
python3 <private-bootstrap>/bootstrap.py <private-bootstrap>/release-lock.json
```

It contains no API key, Jupyter token, session identity, workflow, model URL,
input, arbitrary command, or caller-controlled shell fragment.

The Remote Worker remains loopback-only on port `8766`. A focused gateway
supervisor starts Caddy with fixed argv and the repository-owned Caddyfile on
external port `8765`. It accepts only a nonempty bounded `JUPYTER_TOKEN`, passes
that token only to the Caddy child environment, never logs it, and terminates
Caddy when the Python worker exits. Caddy receives no caller environment other
than the token and fixed private `HOME`, `XDG_CONFIG_HOME`, and `XDG_DATA_HOME`
paths. The Python worker receives only the explicit runtime allowlist plus the
Vast-injected `CONTAINER_ID` and `CONTAINER_API_KEY` required for its
own-instance deadline watchdog; it never receives `JUPYTER_TOKEN` or a
provider-account credential. Caddy's fixed configuration disables the admin
endpoint and automatic HTTPS.
At official Vast base-image source commit
`46e032d852ece6edb2a2a477c5b9557cba6645bf`, `Dockerfile.runtime` inherits the
stock base image and does not itself copy Caddy. The reviewed Supervisor
configuration invokes `/opt/supervisor-scripts/caddy.sh`, and that script
executes `/opt/portal-aio/caddy_manager/caddy` directly. This is exact launch
path evidence, not a claim about which inherited build layer installs the
binary. Before the worker release is rebuilt, a focused TDD change replaces
the incorrect generic `/usr/bin` and `/usr/local/bin` candidates with that one
regular executable path. The private-template audit must still bind an
immutable official image digest and pinned tag. Before creation it also requires
one executable provenance rule: the audited image must be the official
`docker.io/vastai/base-image` repository at a lowercase SHA-256 digest; a
digest-scoped Docker Registry manifest and its small content-addressed config
blob must verify byte-for-byte and contain
`org.opencontainers.image.source=https://github.com/vast-ai/base-image` plus
`org.opencontainers.image.revision=46e032d852ece6edb2a2a477c5b9557cba6645bf`.
Only that manifest and bounded config blob may be retrieved overnight; a tag,
manifest list without one unambiguous Linux/amd64 child, absent/conflicting
label, redirect, proxy, foreign registry/repository, image layer, SBOM guessed
from a tag, or unsigned free-form claim is insufficient. If this exact rule
cannot be satisfied, template creation stops and reports that single blocker
rather than pretending the source inspection describes the chosen image. Even
with this provenance, source evidence is not described as a live
filesystem measurement: the gateway rechecks the actual path, ownership,
regular-file type, and executable bit at boot and fails closed if the image no
longer matches. No paid probe is created merely to prove the path.

To keep Caddyfile environment expansion inert, the bounded token alphabet is
`[A-Za-z0-9._~+/=-]` and its UTF-8 length is 1 through 4096 bytes. A provider
token outside that contract fails closed before either child process starts.

The private template sets `private: true` and exposes only container port
`8765` through Vast's documented `env` Docker-flag field as the fixed value
`-p 8765:8765`. It uses Vast's documented `runtype: "ssh"` with
`use_ssh: true` and `ssh_direct: true`; legacy combined runtype strings and the
undocumented `ports` field are rejected. Jupyter-direct flags are false and
all three documented Docker-registry credential fields are explicit empty
strings. It sets no environment variable or user secret. Vast injects
per-instance credentials at runtime. The local owner-private
`worker-release.json` stores only the template hash, worker commit, archive
digest, versions, protocol, and port. It remains outside Git, is a regular file
owned by the current user with mode `0600`, and is loaded only after ComfyUI
restart.

## Machine-enforced provider-create budget

Session preview requires `max_instance_creates`, an integer in `{1, 2}`. It is
stored in `OfferQuote`, rendered in the paid review, included in the immutable
session record, and rechecked during confirmation.

The value counts total successful or ambiguous Vast create attempts for the
session, not concurrent inventory. Initial confirmation consumes one. A boot
failure may search and create a replacement only when the persisted limit is
two and the first instance has been destroyed with fresh inventory absence.
With a limit of one, the failed instance is destroyed and the session becomes
terminal without offer search or replacement creation.

Idempotent duplicate confirmation does not consume another unit. Recovery
adopts one matching labelled instance and never consumes a unit merely for
adoption. More than one matching instance remains a residual-billing failure.

The first live acceptance should use `max_instance_creates = 1` unless the
human explicitly authorizes two total creates.

## Connection-quality policy

The first real workflow has at least `29,347,330,907` public model bytes to
transfer. Host reliability and network throughput are different signals:
reliability is historical uptime/health, while Vast's `inet_down` field is the
advertised download bandwidth. A reliable host can still be too slow for this
workload.

The provider contracts are documented at:

- https://docs.vast.ai/api-reference/search/search-offers
- https://docs.vast.ai/cli/reference/search-instances
- https://docs.vast.ai/guides/instances/choosing/find-and-rent

Vast's Search Offers API page currently labels `inet_down` as `MB/s`, while
its CLI reference and marketplace offer guide label the same field as
`Mb/s`/Mbps. This design makes an explicit contract choice instead of silently
mixing the two: `inet_down_mbps` follows the CLI and user-facing marketplace
unit, which also matches the repository's existing normalized field name.
The theoretical estimate is valid only under that documented Mbps contract;
the UI identifies the value as provider-advertised and links no promise to it.

The policy is reusable for every workflow rather than special-cased to the
first canvas:

- keep only verified, rentable, one-GPU, on-demand offers;
- require reliability of at least `0.99`;
- require a finite `inet_down` of at least `500` Mbps;
- prefer `1,000` Mbps or more, without treating bandwidth above that target as
  increasingly valuable;
- keep the existing human maximum hourly-price, VRAM, disk, template, and
  instance-create boundaries;
- reject an absent or malformed reliability or download-bandwidth value;
- never lower either threshold automatically when no offer matches.

One default UI search performs exactly two bounded read-only Vast queries. The
target query requests `inet_down >= 1000` and orders by reliability, disk
bandwidth, then price and offer ID. The fallback query requests
`500 <= inet_down < 1000` and orders first by download speed, then by the same
reliability, disk, price, and ID tie-breakers. This aligns provider-side truncation with the shared local
quality policy: very fast rows above the saturated target cannot crowd out a
more reliable, faster-disk, cheaper target-class host, while the fallback query
still supplies the best 500--999 Mbps fallbacks. Both require
`reliability >= 0.99`, use the existing finite result limit, and are merged by
offer ID before local policy. A stricter caller floor below 1,000 raises the
fallback lower bound; a caller floor at or above 1,000 makes only the target
query because the fallback class is empty. Exact-offer revalidation remains
one ID-scoped query with the hard 500 Mbps floor.

Local normalization requires explicit verified, rentable, one-GPU, on-demand,
disk-space, reliability, and bandwidth evidence; verification accepts only the
documented boolean `verified == true` or string `verification == "verified"`
forms and rejects a conflict. Absence is not treated as success. Public helper
overrides may only make thresholds stricter and reject attempts to pass values
below the fixed floors. After normalization, merge, blacklist, and bait-price
removal, the shared deterministic quality key is:

1. `min(inet_down_mbps, 1000)` descending;
2. reliability descending;
3. disk bandwidth descending;
4. total hourly price ascending;
5. offer ID ascending.

Saturating the first key at `1,000` Mbps means a 1,000 Mbps host reaches the
startup target and is not displaced merely by a much more expensive 5,000
Mbps host. When no target-speed offer exists, the fastest eligible fallback
between 500 and 999 Mbps is shown first. The same key governs initial display
order and any separately authorized replacement; there is no second hidden
selection policy.

The offer list and paid review expose provider-advertised download Mbps, disk
MB/s, reliability, download/upload prices, and a raw-transfer lower bound:

```text
ceil(transfer_bytes * 8 / (inet_down_mbps * 1_000_000))
```

For the current model-only floor, that is about `470` seconds at 500 Mbps and
`235` seconds at 1,000 Mbps. The UI labels this as theoretical transfer time,
not startup time. Container/image loading, source throttling, congestion,
checksumming, disk writes, installation, and ComfyUI startup can only make the
observed duration longer.

`OfferQuote` persists the reviewed `inet_down_mbps` and `disk_bw_mbps`. Exact
offer confirmation repeats the provider lookup and all hard gates. It also
requires the current bandwidth to remain at least
`min(quoted_inet_down_mbps, 1000)`: a 1,200 Mbps quote may fall to 1,000 and
remain target-class, while a 1,000 Mbps quote falling to 999 or an 850 Mbps
fallback falling below 850 requires a new search and human review. It never
rents against a stale, slower quote.

Two alternatives are intentionally rejected. A hard 1,000 Mbps floor makes
the four-minute target clearer but can eliminate every otherwise safe offer.
Ranking without a 500 Mbps floor preserves marketplace availability but allows
unbounded model-transfer delay. The selected two-tier policy keeps a useful
floor while aiming for the target.

## Real workflow preflight

The real private workflow must be open in the pinned ComfyUI frontend. It is
captured through the existing Cloud Run action without local execution or
export into this repository. Its selected `LoadImage` input must resolve to the
actual intended private source under the approved ComfyUI input root. A
synthetic substitute blocks the live acceptance.

This is a deliberate human/browser boundary. Canvas state and in-memory native
annotations are owned by the live frontend; neither the backend nor a fresh
unattended Codex process can read or click them. Overnight work may finish the
code, release, template, local lock, and restart, but the human must return to
the browser, open/confirm the actual workflow and input, and invoke the Cloud
Run capture/preflight action. Filesystem scraping, browser-profile mutation,
synthetic exports, and local prompt execution are not substitutes.

Each active model loader must have one exact native `properties.models` record.
The five already proven public identities are reused only when the live
selected filenames and directories still match exactly. Any changed active
selection returns to `mapping_required`; no filename-based inference is
allowed.

Free preflight must freshly resolve:

- every active core/custom node;
- all five exact public models, if still selected;
- the actual private input;
- output allowance and ephemeral disk;
- the published worker release and project template lock.

The model-only byte floor is `29,347,330,907`. The real preflight transfer total
must equal that value plus the actual deduplicated private-input bytes and any
newly active artifact bytes. The previous `29,347,331,113` total and `89 GiB`
estimate are historical synthetic-proof values, not acceptance expectations.

Offer search remains disabled until every row is resolved and `rentable` is
true.

## Authorization boundaries

Permissions do not imply one another. The execution session must obtain each
applicable boundary explicitly:

1. **Implementation authorization:** edit code/tests/docs, commit, and push to
   the existing feature branch and draft PR.
2. **GitHub release authorization:** enable immutable releases and create the
   exact draft/published worker release asset.
3. **Vast template authorization:** create one private project-specific
   template and write its matching local private lock.
4. **Paid live-test authorization:** state maximum total instance creates,
   maximum hourly price, and absolute maximum duration or total cost.

The current plan-writing session authorizes none of these execution actions.

For unattended non-paid preparation, one fresh-session handoff may itemize the
implementation, release, and template permissions separately and condition
each later permission on all preceding gates. A release permission for a
future implementation commit is valid only for the unique final reviewed HEAD
produced by the listed tasks; any later commit, failed review/gate, collision,
or ambiguity cancels that permission. The handoff cannot preauthorize the
live-browser workflow capture or any paid action.

The paid authorization must be a new message in the execution session. For
example:

```text
GO test réel : maximum 1 création d’instance au total, maximum 0,50 $/h,
maximum 90 minutes. J’accepte les frais de bande passante et de stockage
affichés dans la quote. Détruire immédiatement après le premier résultat
cohérent ou tout échec terminal.
```

The shown values are examples, not standing authorization.

## Live acceptance flow

After all free and publication gates pass:

1. obtain the bounded paid authorization;
2. set the UI create limit, hourly limit, and finite deadline no higher than
   the authorized values;
3. run one offer search and select only an offer within every persisted bound;
4. preview and review the exact offer, release, manifest, disk, transfer,
   bandwidth, and deadline;
5. confirm the same durable session once;
6. provision the worker and all verified artifacts;
7. execute the captured prompt once;
8. retrieve and hash-verify the output locally;
9. run the structural validator and obtain human visual confirmation;
10. immediately destroy the instance;
11. fetch fresh Vast inventory and require no managed or residual instance;
12. record sanitized timing, transfer, cost, compatibility, result, and
    teardown evidence.

If output validation fails but the session remains healthy, a retry is allowed
only inside the same already authorized instance and deadline. A second
instance requires remaining machine-enforced create budget and explicit human
authorization that allowed it.

## Failure behavior

Before rental, any inconsistency stops without provider mutation.

After rental:

- bootstrap, gateway, deterministic provisioning, deadline, or release
  incompatibility is terminal and enters immediate verified destruction;
- a transient host boot failure follows the persisted create limit;
- an execution/OOM failure may retain a healthy instance only long enough for
  a same-instance decision inside the deadline;
- a first coherent output ends the acceptance run and triggers destruction;
- a failed or unverifiable DELETE keeps the instance identifier and emergency
  Vast-console action visible;
- the run is not complete until fresh inventory proves the managed ID and
  label absent.

No automatic action deletes a GitHub release or Vast template. Those are
separate destructive operations requiring separate approval.

## Test strategy

### Release transport

- exact release URL and embedded commit/digest accepted;
- direct `200` and one allowlisted `302` accepted;
- second redirect, wrong host/status/scheme/port/userinfo/fragment rejected;
- signed target never appears in the returned stream or errors;
- wrong size/digest and malformed archive still rejected.

### Gateway and template bundle

- exactly one allowlisted Caddy binary selected;
- fixed argv, fixed Caddyfile, minimal environment, and bounded shutdown;
- missing/duplicate binary and missing/invalid token rejected without logging;
- two bundle builds at one commit are byte-identical;
- generated template has one exact port and no secret or arbitrary field;
- bootstrap and remote lock bytes round-trip from the fixed `onstart` payload.

### Instance-create budget

- limit one destroys a failed boot and performs no search/create replacement;
- limit two preserves the existing single replacement behavior;
- invalid/missing limits block preview;
- duplicate preview/confirm and recovery never increase create count;
- quote/UI expose the exact persisted total-create limit.

### Connection quality

- the two bounded provider query tiers cover target and floor candidates
  without allowing raw speeds above the target to monopolize the result limit;
- provider queries and local normalization require reliability `>= 0.99` and
  advertised download bandwidth `>= 500` Mbps;
- missing, non-finite, below-floor, or incomplete provider constraint evidence
  fails closed, and explicit overrides cannot weaken the fixed floors;
- target saturation ranks 1,000 Mbps ahead of 999 Mbps but does not reward
  bandwidth beyond 1,000 Mbps before reliability, disk, and price;
- initial offer order and replacement selection use the same stable key;
- quote persistence and confirmation reject a material bandwidth downgrade;
- offer and paid-review UI show Mbps, disk MB/s, bandwidth prices, and the
  theoretical estimate without presenting it as measured startup time;
- an empty result stops without threshold relaxation, template mutation, or
  provider create.

### Repository and publication

- focused tests follow an observed red-green cycle;
- complete `scripts/check.sh` passes twice consecutively after code changes;
- code review covers redirects, shell/template generation, gateway secrets,
  release immutability, create limits, and residual billing;
- the fetched published asset equals local size and SHA-256 and passes the
  production bootstrap locally in a temporary destination.

### Live acceptance

- actual input, not a synthetic substitute;
- free preflight fully resolved before search;
- one captured prompt, no local execution;
- verified remote output and structural/human review;
- immediate destroy and fresh empty inventory.

## Non-goals

- accepting codeload source archives;
- arbitrary redirect following or CDN-host expansion;
- mutable GitHub releases, tags, assets, branches, or `latest` URLs;
- custom Docker-image publication;
- committing release archives, template payloads, private locks, credentials,
  workflows, inputs, outputs, instance IDs, or signed URLs;
- guessing a model or input from its filename;
- enabling automatic replacement beyond the human total-create limit;
- promising a four-minute startup or measuring throughput by renting a probe
  instance;
- an adaptive optimizer, per-workflow network settings, or silent quality
  fallback for the first acceptance;
- merging PR `#2`, publishing to Comfy Registry, or announcing general
  availability as part of the first live acceptance.

## Acceptance criteria

- The deterministic worker archive is served as a verified immutable GitHub
  Release asset bound to one commit and digest.
- The production bootstrap accepts the real asset through at most one narrowly
  allowlisted redirect and never exposes the signed target.
- The private Vast template contains only the fixed reviewed bootstrap and no
  secret or workflow-controlled command.
- The local private release lock matches the exact template, commit, archive,
  protocol, versions, and port.
- The persisted total-create limit cannot be exceeded by confirmation,
  replacement, retry, or recovery.
- Every searched, previewed, confirmed, or replacement offer satisfies the
  fixed reliability/download floors, uses the one shared target-saturated
  ranking, and exposes its reviewed connection metrics and theoretical
  transfer estimate.
- The actual workflow and actual private input pass a new free preflight.
- A separately bounded live run produces one coherent verified output or a
  sanitized failure, then destroys every managed instance.
- Fresh Vast inventory is empty before any completion claim.
- Public evidence contains no private path, workflow, input identity, secret,
  signed URL, settings payload, database content, or raw provider response.

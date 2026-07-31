# Immutable Worker Release and Live Workflow Acceptance Design

Date: 2026-07-31

Status: approved for implementation planning. This document does not authorize
its implementation, a GitHub release, a Vast template mutation, an offer
search, or a paid instance.

## Relationship to the existing designs

This design is a narrow continuation of:

- `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`;
- `docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md`;
- `docs/remote-worker-bootstrap-review.md`.

Those documents remain authoritative for canvas capture, dependency
resolution, worker authentication, provisioning, execution, deadlines,
verified outputs, and destruction. This continuation resolves the remaining
release bootstrap contradiction, makes the human instance-count authorization
machine-enforceable, and defines the first real remote acceptance run.

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
The supervisor accepts only one proven
absolute Caddy path from `/usr/bin/caddy` or `/usr/local/bin/caddy`; zero or two
matches fail closed. The official base-template audit must establish which one
exists before template creation.

To keep Caddyfile environment expansion inert, the bounded token alphabet is
`[A-Za-z0-9._~+/=-]` and its UTF-8 length is 1 through 4096 bytes. A provider
token outside that contract fails closed before either child process starts.

The private template exposes port `8765` and sets no user secret. Vast injects
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

## Real workflow preflight

The real private workflow must be open in the pinned ComfyUI frontend. It is
captured through the existing Cloud Run action without local execution or
export into this repository. Its selected `LoadImage` input must resolve to the
actual intended private source under the approved ComfyUI input root. A
synthetic substitute blocks the live acceptance.

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
- The actual workflow and actual private input pass a new free preflight.
- A separately bounded live run produces one coherent verified output or a
  sanitized failure, then destroys every managed instance.
- Fresh Vast inventory is empty before any completion claim.
- Public evidence contains no private path, workflow, input identity, secret,
  signed URL, settings payload, database content, or raw provider response.

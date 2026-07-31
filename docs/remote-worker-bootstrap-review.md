# Remote Worker bootstrap review

This document is the offline review handoff and public source-publication
record. It is not a release lock, Vast template, or permission to spend.

No immutable Remote Worker release has been published. No private
project-specific Vast template has been created. No local live
`worker-release.json` exists. No Vast offer search has been performed. No paid
Vast instance has been created. No live workflow run has occurred.

Publishing source alone does not create a worker release lock, authorize Vast
activity, or convert deterministic offline review material into a live
release.

## Public source publication evidence — 2026-07-31

Public source repository: https://github.com/wuraaang/ComfyUI-Cloud-Run

Published source commit:
`4627ffcd504cd3cbfdaa280921a9d09eba63f488`

The repository was verified `PUBLIC` with default branch `main`. At the source
publication boundary, both `main` and `feat/vast-cloud-run-lifecycle` resolved
to that exact commit. The unrelated ComfyRelay remote is retained locally as
`comfy-relay-do-not-push` at
https://github.com/wuraaang/comfy-relay.git; its feature-branch query returned
no ref and it received no publication push.

Fetched archive URL:
https://github.com/wuraaang/ComfyUI-Cloud-Run/archive/4627ffcd504cd3cbfdaa280921a9d09eba63f488.tar.gz

Fetched archive size: `356149 bytes`

Fetched archive SHA-256:
`bf7727f35e2ec32103cf7eeaf329e0e094406a2503fdec1241328b3322aa681b`

Observed redirect boundary: HTTP `302` from the requested `github.com` source
archive URL. The target is intentionally omitted. This historical source
archive used a host that is not accepted by the immutable GitHub Release asset
contract below.

The fetched archive had one complete gzip member with no trailing or
unconsumed bytes. Its tar contained 116 members under the single root
`ComfyUI-Cloud-Run-4627ffcd504cd3cbfdaa280921a9d09eba63f488`, with no
duplicate, unsafe path, symlink, hard link, device, or other special member.
All 116 members carried PAX headers; UID/GID were zero, owner/group were
`root`, modes were `0664` or `0775`, and the sole mtime was `1785488657`.
All 15 reviewed worker files were present, with zero reviewed files missing
and 88 additional repository files.

The deterministic review artifact remained `51590 bytes` with SHA-256
`783a8f180365f6401af69679ba7681401aa123c50d050ead47c4f6faeb8df06f`.
It is not byte-identical to the fetched full-repository archive and its digest
must not be copied into a live lock for those GitHub bytes.

Historical full-repository source-archive result: `FAIL`.

- `observed_final_url=FAIL`: the injected-stream audit observed the exact
  returned-URL check reject the codeload final URL. The production HTTPS
  transport is stricter still: its no-redirect handler rejects the initial
  `302` before accepting a codeload response.
- `same_url_archive_shape=FAIL`: with the returned URL simulated as equal, the
  same bytes still failed. The measured size, digest, and single gzip member
  were valid; archive-member validation rejects the observed PAX headers,
  `root` owner/group, nonzero mtime, and `0664`/`0775` modes.

Separately, code review proves that normalizing metadata alone would remain
insufficient: the commit root prefix and 88 additional repository files would
fail the installed-tree allowlist after extraction.

These failures are release-blocking evidence. They do not authorize weakening
redirect, archive, extraction, allowlist, digest, or shell restrictions.

No Vast provider mutation, worker release, tag, Registry publication, or GPU
rental occurred during this source-publication audit.

## Deterministic review artifact

Build from the repository root into an explicit path:

```sh
python3 scripts/build_worker_artifact.py \
  /absolute/review/path/comfyui-cloud-run-worker.tar.gz
```

Two consecutive complete gates must produce byte-identical archives and print
the same current size and SHA-256. Those gate values are offline review
evidence, not a published immutable release identity.

The builder normalizes member order, UID/GID, owner/group names, modes, mtime,
tar format, and gzip mtime. It rejects a missing or unknown file, symlink,
bytecode, VCS/test metadata, secret-like content, changed source, and oversized
input. The archive is review material only and is never written into the
repository.

Its exact members are:

```text
cloud_run/manifest.py
cloud_run/worker_protocol.py
remote_worker/Caddyfile
remote_worker/__init__.py
remote_worker/bootstrap.py
remote_worker/comfy.py
remote_worker/deadline.py
remote_worker/gateway.py
remote_worker/install.py
remote_worker/jobs.py
remote_worker/main.py
remote_worker/provision.py
remote_worker/server.py
remote_worker/state.py
remote_worker/template-policy.json
remote_worker/transfers.py
```

Any change to this list or to one byte of those files requires a fresh build,
digest, complete gate, source review, and update of this document before
publication.

## Fixed bootstrap contract

`remote_worker/bootstrap.py` accepts exactly one JSON object with:

```text
schema_version
archive_url
worker_commit
worker_archive_sha256
worker_archive_size_bytes
protocol_version
comfyui_core_version
comfyui_frontend_version
python_version
destination
```

The worker commit is exactly 40 lowercase hex characters. The immutable GitHub
Release asset URL is bound to the fixed repository, that commit, and the exact
archive SHA-256. Its only accepted shape is:

```text
https://github.com/wuraaang/ComfyUI-Cloud-Run/releases/download/worker-v1-{commit}/comfyui-cloud-run-worker-{same-commit}-{same-sha256}.tar.gz
```

The transport disables proxies and automatic redirects, requests identity
encoding, and accepts a direct `200` response or exactly one HTTP `302`
redirect to host `release-assets.githubusercontent.com`. Every other status,
host, redirect count, encoding, user-information, explicit port, or fragment
fails closed. The signed target exists only while making that second request
and is never retained, persisted, logged, or returned. The reviewed source URL,
redirect count of zero or one, exact byte size, and SHA-256 must match before
installation.

Extraction accepts one gzip member and bounded regular tar files/directories
only; it rejects absolute/traversal paths, duplicates, symlinks, devices,
sparse/PAX metadata, unsafe modes, excess members, and expansion beyond the
fixed limit.

The installed tree is atomically placed at `/opt/comfyui-cloud-run`. Bootstrap
then uses `os.execv` with only:

```text
<current-python> -m remote_worker.gateway \
  --state-directory /var/lib/comfyui-cloud-run
```

No environment, workflow, manifest, Agent Panel suggestion, or dependency
record can provide an alternate command or shell fragment.

The publication reviewer must verify that the exact immutable GitHub Release
asset bytes are the reviewed source-only member set and record those bytes'
actual size and digest in the private template bootstrap lock. An offline gate
digest must not be copied into a live lock unless the published bytes are
exactly the same artifact.

## Deterministic private publication inputs

Run these commands only with existing paths in an owner-private directory:

```sh
python3 scripts/build_worker_release_bundle.py \
  --repository-root <repository-root> \
  --output-directory <owner-private-output-directory> \
  --worker-commit <40-lowercase-hex-commit>

python3 scripts/render_worker_template.py \
  --repository-root <repository-root> \
  --output-directory <owner-private-output-directory> \
  --release-metadata <owner-private-release-metadata> \
  --base-template-audit <owner-private-base-template-audit>

python3 scripts/write_worker_release_lock.py \
  --output <owner-private-data-directory>/worker-release.json \
  --template-hash-id <32-lowercase-hex-template-id> \
  --release-metadata <owner-private-release-metadata>
```

The release builder produces a deterministic archive and sanitized metadata
whose tag, asset name, URL, commit, byte size, SHA-256, protocol, runtime
versions, and destination agree exactly. The renderer accepts only the fixed
audited base-template schema and deterministically creates the remote lock,
fixed bootstrap program, and template request with no embedded token, session,
workflow, model locator, signed target, or caller command.

Every input and output remains outside the repository. The output directory
must be owner-private and every generated file has mode `0600`. The local lock
writer accepts only a nonexistent target beneath an owner-private directory,
publishes by no-overwrite atomic hard link, synchronizes the directory, and
round-trips every field through `load_worker_release()` before success.

## Caddy and inbound boundary

`remote_worker/Caddyfile` listens on external container port `8765`. It requires
the Vast-provided Jupyter bearer token, strips `Authorization`, discards any
caller-supplied `X-Cloud-Run-Boundary`, adds the authenticated boundary marker,
and proxies only to `127.0.0.1:8766`.

`remote_worker/gateway.py` requires exactly one executable regular Caddy binary
from its two fixed absolute candidates, validates the token without disclosing
it, and starts Caddy plus the worker with fixed argv and no shell. Only Caddy's
minimal environment receives the token. The worker receives only an explicit
runtime allowlist. When either child exits, the supervisor terminates the
sibling, waits for the fixed bound, kills only after timeout, and reaps both
processes or fails with one static error.

The Python worker itself binds only to `127.0.0.1:8766`. Its route allowlist is:

```text
GET  /worker/v1/health
POST /worker/v1/claim
POST /worker/v1/manifests
GET  /worker/v1/transactions/{transaction_id}
PUT  /worker/v1/artifacts/{artifact_id}
GET  /worker/v1/artifacts/{artifact_id}
POST /worker/v1/jobs
GET  /worker/v1/jobs/{job_id}
GET  /worker/v1/jobs/{job_id}/events
GET  /worker/v1/jobs/{job_id}/previews/{preview_id}
PUT  /worker/v1/deadline
```

After one atomic claim, mutating requests require the session ID, timestamp,
nonce, body digest, and HMAC signature. Nonces and clock skew are bounded.

## Worker outbound surface

The reviewed worker can make only these outbound connections:

- bootstrap GET to the exact immutable GitHub Release asset in the lock, with
  at most the one reviewed release-assets redirect;
- ranged dependency GETs to the immutable manifest origin, or the exact
  backend-signed R2 URL, with redirect confinement to the original origin;
- loopback HTTP/WebSocket calls to its own ComfyUI on `127.0.0.1:8188`;
- one deadline-triggered own-instance DELETE to
  `https://console.vast.ai/api/v0/instances/{CONTAINER_ID}/`.

The deadline provider has no search, offer, create, list, get, stop, volume, or
arbitrary-instance capability. The own-instance DELETE URL is constructed from
the validated numeric `CONTAINER_ID`; only that instance's
`CONTAINER_API_KEY` is used. Destroy intent is persisted before the request.

## Evidence required before publication

A publication review must record all of the following:

1. a clean correct ComfyUI-Cloud-Run repository and immutable public commit;
2. confirmation that no push went to the ComfyRelay remote;
3. a fresh `scripts/check.sh` run, including the same artifact digest on two
   consecutive gates;
4. a manual diff of every member listed above;
5. bootstrap rejection evidence for mutable URLs, redirects, wrong
   size/digest, unsafe archives, extra lock fields, and shell fields;
6. Caddy header stripping, loopback binding, and worker-route review;
7. the exact fetched publication bytes, size, SHA-256, commit, and archive URL;
8. a new project-specific Vast template ID derived from the reviewed official
   base, with no embedded user secret or mutable dependency;
9. a private `0600` `worker-release.json` matching that template, commit,
   digest, protocol, versions, and port;
10. a separate paid-Gold GO stating maximum instances, hourly price, and
    absolute duration or cost.

Publishing an artifact or template is not itself authorization to rent a GPU.

## Total instance-create authorization

The paid review's **Maximum total instance creates** is an immutable integer
limited to `1` or `2`. Legacy records default to `1`; no recovery path infers a
larger value. The initial create consumes one, and the durable retry count
represents any consumed replacement. Confirmation checks the persisted value,
and boot-failure handling checks it after verified destruction and absence but
before any replacement offer search or create. Duplicate confirmation,
ambiguous-create reconciliation, and restart recovery do not replenish the
budget. The current offline tests use fake providers only.

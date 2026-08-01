# Remote Worker bootstrap review

This document is the offline review handoff and public source-publication
record. It is not a release lock, Vast template, or permission to spend.

The immutable Remote Worker release for commit
`d317e2f5b69725ae92fd0d3b1dc6273623cf2407` pins remote Python `3.13.12` and is
preserved unchanged as a historical rollback. It is incompatible with the new
official Python `3.12` runtime.

At the 2026-08-01 source-review checkpoint, before live publication: No new
Python 3.12 Remote Worker release has been published. No new private
project-specific Vast template has been created. No local live
`worker-release.json` exists. No post-migration ComfyUI restart has occurred.
No Vast offer search has been performed. No paid Vast instance has been
created. No post-migration live workflow run has occurred.

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
Those values are historical evidence for the old worker policy, not the
current Python `3.12` worker. The new release requires a fresh deterministic
build, size, SHA-256, and two identical final gates before publication.

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

The remote lock's `python_version` is the exact series string `3.12`, never a
patch value. Remote Comfy health accepts only reports beginning `3.12.` and
rejects Python 3.11, Python 3.13, and an unqualified `3.12`. Custom-node wheels
are limited to compatible `cp312` or universal `py3` wheels with compatible
ABIs and platform `any`, exact `linux_x86_64`, legacy manylinux x86_64 aliases,
or PEP 600 `manylinux_2_5_x86_64` through `manylinux_2_39_x86_64`. The selected
Ubuntu 24.04 runtime uses glibc 2.39, so future or malformed manylinux tags,
`musllinux*`, other interpreters, operating systems, and CPU architectures fail
closed. The local ComfyUI Desktop remains pinned separately to Python
`3.13.12`.

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

The deterministic `onstart` exports
`CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI` and directly invokes the
bootstrap with `/venv/main/bin/python`. It does not call the image entrypoint,
Supervisor, portal/serverless tooling, the official ComfyUI wrapper, or a
shell-derived executable. The installed worker tree is atomically placed at
`/opt/comfyui-cloud-run`; bootstrap then uses `os.execv` with only:

```text
/venv/main/bin/python -m remote_worker.gateway \
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

python3 scripts/publish_worker_template.py \
  audit-base \
  --output-directory <owner-private-output-directory>

python3 scripts/render_worker_template.py \
  --repository-root <repository-root> \
  --output-directory <owner-private-output-directory> \
  --release-metadata <owner-private-release-metadata> \
  --base-template-audit <owner-private-base-template-audit>

python3 scripts/publish_worker_template.py \
  publish \
  --request-file <owner-private-template-request> \
  --output-directory <owner-private-output-directory>

python3 scripts/write_worker_release_lock.py \
  --output <owner-private-data-directory>/worker-release.json \
  --template-hash-id <32-lowercase-hex-template-id> \
  --release-metadata <owner-private-release-metadata>
```

The release builder produces a deterministic archive and sanitized metadata
whose tag, asset name, URL, commit, byte size, SHA-256, protocol, runtime
versions, and destination agree exactly. The base audit records only `hash_id`,
`use_ssh`, and `ssh_direct`; it does not trust or retain base `runtype`,
`jupyter_dir`, image, tag, or unrelated provider fields. The renderer owns the
runtime identity and deterministically creates the remote lock, fixed bootstrap
program, and template request with no embedded token, session, workflow, model
locator, signed target, or caller command.

The exact private payload fixes `runtype=ssh`, `use_ssh=true`,
`ssh_direct=true`, `jup_direct=false`, `jupyter_dir=/workspace`,
`use_jupyter_lab=false`, empty registry credentials, `-p 8765:8765`, an 80 GiB
recommended disk, and `private=true`. It includes exactly these typed
single-GPU filters:

```json
{
  "gpu_arch": {"eq": "nvidia"},
  "cpu_arch": {"eq": "amd64"},
  "cuda_max_good": {"gte": 12.9},
  "compute_cap": {"gte": 750},
  "num_gpus": {"eq": 1}
}
```

The template publisher fixes the Vast template HTTPS endpoint and exposes only
base audit plus one private-template publication. It validates exact lookup and
request schemas, disables redirects and ambient proxies, bounds time and
response bytes, obtains the API key through a no-follow owner-private settings
read instead of argv, performs at most one POST, and reconciles ambiguity only
with one exact-name GET. Request validation, wildcard worker-row projection,
ambiguous-POST reconciliation, and exact-hash readback all compare the exact
image, tag, launch fields, and `extra_filters`. It has no update, delete,
arbitrary URL/method, offer, instance, or volume capability, and never prints a
request, response, authorization header, key, `onstart`, or base64 body. Before
any HTTP, publication decodes `onstart`, compares its bootstrap bytes with the
reviewed `remote_worker/bootstrap.py`, and validates the remote lock as the one
canonical immutable release contract.

Every input and output remains outside the repository. The output directory
must be owner-private and every generated file has mode `0600`. The local lock
writer accepts only a nonexistent target beneath an owner-private directory,
publishes by no-overwrite atomic hard link, synchronizes the directory, and
round-trips every field through `load_worker_release()` before success.

The same-origin GET and PUT settings responses expose browser-safe
`worker_release`: `null` when the service instance has no validated lock, or
the exact `WorkerRelease.to_record()` loaded on that same service instance.
They do not reload the file independently and never expose the lock path,
archive URL, credential, token, session secret, workflow, model, or private
template payload.

## Selected official image and provenance

The renderer and publisher accept exactly:

```text
image: docker.io/vastai/comfy@sha256:9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c
tag: v0.29.0-cuda-12.9-py312
linux/amd64 child: sha256:7a83c93be852db309d4be3e415cf38e186977c202638f1ef1b4a605a3bc49f0a
config: sha256:992e89c2d0641a6c894885d4246dc706911c7a02266f368337b41bf968eaaaf2
config size: 38584 bytes
```

The selected digest makes the runtime bytes immutable. It does not prove every
source revision: the config carries official Vast source and maintainer labels
but no `org.opencontainers.image.revision` label. Vast's successful public
build at source commit `46e032d852ece6edb2a2a477c5b9557cba6645bf` correlates
with the digest without cryptographically binding that revision. Before the
sole provider POST, the publication gate requires the human either to accept
this narrower official-image evidence or explicitly authorize inspection of
the digest-pinned in-toto attestation. No template is published while that
gate remains open.

## Caddy and inbound boundary

`remote_worker/Caddyfile` listens on external container port `8765`. It requires
the Vast-provided Jupyter bearer token, strips `Authorization`, discards any
caller-supplied `X-Cloud-Run-Boundary`, adds the authenticated boundary marker,
and proxies only to `127.0.0.1:8766`.

The following official Vast base-image source review is inherited launch-path
evidence, not the selected `vastai/comfy` image identity or complete provenance.
At source commit
`46e032d852ece6edb2a2a477c5b9557cba6645bf`, `Dockerfile.runtime` inherits its
stock base image, `caddy.conf` invokes `/opt/supervisor-scripts/caddy.sh`, and
that script directly executes `/opt/portal-aio/caddy_manager/caddy`. This
source review proves the launch path, not the live filesystem or the inherited
layer that installed the binary.

Reviewed public permalinks:

- `Dockerfile.runtime`: https://github.com/vast-ai/base-image/blob/46e032d852ece6edb2a2a477c5b9557cba6645bf/Dockerfile.runtime
- `caddy.sh`: https://github.com/vast-ai/base-image/blob/46e032d852ece6edb2a2a477c5b9557cba6645bf/ROOT/opt/supervisor-scripts/caddy.sh
- `caddy.conf`: https://github.com/vast-ai/base-image/blob/46e032d852ece6edb2a2a477c5b9557cba6645bf/ROOT/etc/supervisor/conf.d/caddy.conf

`remote_worker/gateway.py` requires exactly that one executable regular Caddy
binary, rejects symlinks and generic system paths, validates the token without
disclosing it, and starts Caddy plus the worker with fixed argv and no shell.
Only Caddy's minimal environment receives the token. The worker receives only
an explicit runtime allowlist plus Vast's own-instance `CONTAINER_ID` and
`CONTAINER_API_KEY` required by its deadline watchdog; it receives neither the
Jupyter token nor a provider-account key. When either child exits, the
supervisor terminates the sibling, waits for the fixed bound, kills only after
timeout, and reaps both processes or fails with one static error.

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
8. the exact selected image index, amd64 child, config digest/size, and the
   explicit human decision on its incomplete revision provenance;
9. a new project-specific Vast template ID whose base audit supplied only the
   reviewed hash and SSH flags, with exact private launch fields and five
   filters and no embedded user secret or mutable dependency;
10. a private `0600` `worker-release.json` matching that template, commit,
   digest, protocol, versions, and port, created only after exact readback;
11. a separate paid-Gold GO stating maximum instances, hourly price, and
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

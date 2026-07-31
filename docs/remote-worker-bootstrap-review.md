# Remote Worker bootstrap review

This document is the offline review handoff. It is not a release lock,
publication record, Vast template, or permission to spend.

No worker archive or project-specific Vast template has been published. The
repository intentionally contains no live `worker-release.json`. The currently
configured ComfyRelay remote is unrelated and must not receive this project.

## Deterministic review artifact

Build from the repository root into an explicit path:

```sh
python3 scripts/build_worker_artifact.py \
  /absolute/review/path/comfyui-cloud-run-worker.tar.gz
```

For the Task 18 source state, two consecutive builds produced byte-identical
51,590-byte archives with:

```text
SHA-256  783a8f180365f6401af69679ba7681401aa123c50d050ead47c4f6faeb8df06f
```

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

The worker commit is exactly 40 lowercase hex characters. The only accepted URL
shape is:

```text
https://github.com/{owner}/{repository}/archive/{same_commit}.tar.gz
```

The transport disables proxies and redirects, requests identity encoding,
streams into a private `.part`, and requires the final URL, exact byte size, and
SHA-256 to match the reviewed lock. Extraction accepts one gzip member and
bounded regular tar files/directories only; it rejects absolute/traversal
paths, duplicates, symlinks, devices, sparse/PAX metadata, unsafe modes, excess
members, and expansion beyond the fixed limit.

The installed tree is atomically placed at `/opt/comfyui-cloud-run`. Bootstrap
then uses `os.execv` with only:

```text
<current-python> -m remote_worker.main \
  --state-directory /var/lib/comfyui-cloud-run
```

No environment, workflow, manifest, Agent Panel suggestion, or dependency
record can provide an alternate command or shell fragment.

The publication reviewer must verify how the exact immutable GitHub archive
bytes map to the reviewed source-only member set and record that archive's
actual size and digest in the template bootstrap lock. The offline digest above
must not be copied into a live lock unless the fetched bytes are exactly the
same artifact.

## Caddy and inbound boundary

`remote_worker/Caddyfile` listens on external container port `8765`. It requires
the Vast-provided Jupyter bearer token, strips `Authorization`, discards any
caller-supplied `X-Cloud-Run-Boundary`, adds the authenticated boundary marker,
and proxies only to `127.0.0.1:8766`.

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

- bootstrap GET to the exact immutable `github.com` archive in the lock;
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

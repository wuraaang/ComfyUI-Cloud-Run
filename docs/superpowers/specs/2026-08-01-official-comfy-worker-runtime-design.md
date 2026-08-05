# Official Comfy Worker Runtime Design

**Date:** 2026-08-01

**Scope:** finish the free preparation needed before the real workflow preflight

**Status:** implementation direction approved; provider publication remains gated by the provenance decision below

**Precedence:** for the selected OCI image, remote Python series, wheel-platform compatibility, hardware filters, launch/onstart contract, and official-template audit schema, this design supersedes the corresponding Task 8 portions of the 2026-07-31 immutable-release design. The older release, bootstrap, Caddy, transport, immutability, secret, and paid-action contracts remain in force.

## Outcome

Use one immutable official Vast ComfyUI image as the generic single-GPU NVIDIA runtime. Do not build or publish a project Docker image for this release. The workflow remains responsible for declaring its custom nodes, wheels, models, and private inputs; the worker installs or transfers only those verified dependencies.

This change is the last repository/template preparation before the human opens the real workflow and runs the free Cloud Run capture/preflight. It does not authorize an offer search, instance creation, model transfer, workflow execution, or spending.

## Selected image

The template uses exactly:

```text
image: docker.io/vastai/comfy@sha256:9852fae86527d0be097ffcb90dc18368ff808bcbb7c41fbabd538bff3eb6ab9c
tag: v0.29.0-cuda-12.9-py312
linux/amd64 child: sha256:7a83c93be852db309d4be3e415cf38e186977c202638f1ef1b4a605a3bc49f0a
config: sha256:992e89c2d0641a6c894885d4246dc706911c7a02266f368337b41bf968eaaaf2
config size: 38584 bytes
```

The image contains ComfyUI `0.29.0` at commit `a8c44f9b2a0678ac4082e3529a3f43db7472acfe`, frontend `1.47.10`, Python series `3.12`, PyTorch `2.10.0` with CUDA 12.8 binaries, and the CUDA 12.9 runtime. Its immediate base is the official Vast PyTorch image whose 33 layer descriptors are the exact prefix of the selected image's 39 descriptors.

The exact digest makes the runtime bytes immutable. It does not by itself prove every source revision: the config has the official Vast source and maintainer labels but no `org.opencontainers.image.revision` label. The public successful Vast build at source commit `46e032d852ece6edb2a2a477c5b9557cba6645bf` correlates with the published digest, but that correlation is not a cryptographic source-revision binding. The original Task 8 label rule therefore remains unsatisfied. Before the single provider POST, the human must either accept this narrower official-image evidence or explicitly authorize verification of the digest-pinned in-toto attestation blob. No template is published while this gate is open.

## Launch contract

The private template continues to use Vast's SSH launch mode. In that mode Vast replaces the image entrypoint and runs `onstart` after its own SSH initialization. The project does not call the official image entrypoint and therefore does not start its Supervisor-managed ComfyUI or API wrapper.

The deterministic `onstart`:

1. installs the reviewed bootstrap and release lock under `/opt/comfyui-cloud-run-bootstrap`;
2. exports the fixed worker commit;
3. exports `CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI`;
4. executes `/venv/main/bin/python` on the bootstrap.

### Live Vast `onstart` size contract

The authorized live publication exposed a provider limit that was not present in
the create-template schema: Vast's read-only
`GET /api/v0/template/params/` reports a maximum `onstart` length of `16384`
characters. The rejected request contained `31872` ASCII characters, principally
the raw base64 form of the reviewed bootstrap. Publication must enforce the live
limit locally before its first HTTP request.

The renderer therefore gzip-compresses only the exact reviewed
`remote_worker/bootstrap.py` bytes with deterministic metadata before base64
encoding them. The small canonical release lock remains raw base64. The fixed
POSIX `onstart` decodes and inflates the bootstrap into its private file, decodes
the lock as before, applies the same modes and exports, and directly executes the
same `/venv/main/bin/python` command. It introduces no downloaded script, mutable
URL, alternate executable, image entrypoint, Supervisor process, or additional
provider action.

Publisher validation must recompute and require the exact deterministic gzip
member for the reviewed bootstrap, retain the existing exact canonical lock
round-trip, and reject any `onstart` longer than `16384` characters before a
credential lookup or HTTP request. Tests cover deterministic rendering, exact
inflate-to-source equality, tampered or non-canonical compressed bytes, and the
pre-HTTP size rejection. The image, tag, hardware filters, single-POST budget,
durable intent, private receipt, and launch command remain unchanged.

The worker launches exactly one native ComfyUI process on loopback port `8188`. Its gateway launches the already reviewed Caddy executable on external port `8765`. It neither starts Supervisor nor copies the baked ComfyUI tree to `/workspace`, avoiding a duplicate GPU process and an avoidable startup copy.

## Runtime identity

Local ComfyUI Desktop remains pinned to Python `3.13.12`. Only the remote worker release changes.

The remote lock records Python series `3.12`. The digest-pinned image determines the actual patch version, while the remote health check requires the reported version to start with `3.12.`. Remote custom-node wheel resolution accepts compatible `cp312` and universal `py3` wheels only for `any`, exact `linux_x86_64`, legacy manylinux x86_64 aliases, or PEP 600 `manylinux_2_5_x86_64` through `manylinux_2_39_x86_64`. The selected Ubuntu 24.04 runtime uses glibc 2.39, so future or malformed manylinux tags and `musllinux*` are rejected together with other interpreters, operating systems, and CPU architectures.

Because the published `d317e2f5b69725ae92fd0d3b1dc6273623cf2407` worker requires Python `3.13.12`, a new reviewed commit, deterministic worker archive, and immutable GitHub Release are mandatory. The existing release is preserved unchanged as historical rollback material and is not edited or deleted.

## Hardware compatibility contract

Every provider query and every local normalization/revalidation requires all existing price, reliability, network, disk, verified-host, and VRAM constraints plus:

```json
{
  "gpu_arch": {"eq": "nvidia"},
  "cpu_arch": {"eq": "amd64"},
  "cuda_max_good": {"gte": 12.9},
  "compute_cap": {"gte": 750},
  "num_gpus": {"eq": 1}
}
```

`compute_cap >= 750` follows Vast's official production ComfyUI floor. It excludes older CUDA architectures that are poor candidates for the image's current accelerated packages. `cuda_max_good >= 12.9` avoids relying on driver compatibility guesses. The four compatibility constraints plus `num_gpus == 1` appear as the private template's deterministic `extra_filters`, but backend validation remains authoritative because template filters only seed Vast's search UI.

This release deliberately supports one NVIDIA GPU on an amd64 host. AMD, arm64, fractional GPUs, multi-GPU scheduling, distributed execution, and video-specific multi-GPU extensions are later-version work.

## Template audit and request

The read-only audit of official template hash `027fba7753c024be019030fb42aed900` remains the authority for the documented `use_ssh=true` and `ssh_direct=true` flags only. A live GET on 2026-08-01 returned `runtype="jupyter"`, replacing the previously observed composite value. Vast's current API documentation lists `use_ssh` and `ssh_direct` as searchable template fields and separately defines the create-time launch contract. The audit therefore neither selects nor records the mutable `runtype` or `jupyter_dir`, and the renderer does not copy the official template's image or unrelated fields.

The project request independently emits and validates the exact selected image and tag above, fixed port mapping `-p 8765:8765`, fixed compatibility `extra_filters`, `runtype="ssh"`, `use_ssh=true`, `ssh_direct=true`, `jup_direct=false`, `jupyter_dir="/workspace"`, `use_jupyter_lab=false`, empty registry credentials, recommended disk `80`, and `private=true`.

The publication transport retains its single-POST budget, exact-name duplicate precheck, ambiguity reconciliation, exact-hash readback, full request comparison, proxy/redirect rejection, and secret-safe output. It adds `extra_filters` to the exact compared fields.

## Loaded-release readiness proof

The existing settings response cannot currently distinguish a process that loaded the reviewed lock from one that started without it. The free same-origin `GET` and `PUT /cloud-run/api/settings` responses therefore add one `worker_release` field. It is `null` when service construction did not load a valid lock; otherwise it is the exact `WorkerRelease.to_record()` value containing only schema, public template hash, worker commit/archive digest, protocol, pinned versions, and worker port. It never includes the lock path, release URL, provider credential, token, workflow, model, session, or private payload.

After restart, an exact non-null response proves that the running extension process loaded the expected lock. The existing `/extensions` listing plus byte equality of the served `cloud-run.js` proves that the extension and Cloud Run UI code are available. Visual mounting of the button remains a human/browser observation and is not simulated.

## Gates and stopping point

Implementation follows red-green TDD for runtime identity, wheel compatibility, offer filters, deterministic rendering, and publication readback. The complete repository gate runs twice on the final commit, followed by a new code review because the image/runtime migration is a new finding.

Only after those gates and an explicit provenance decision may execution create one new immutable GitHub Release and one new private Vast template, create the currently absent owner-private `worker-release.json`, and restart the pinned local ComfyUI. The session then verifies the loaded lock and Cloud Run UI without opening the workflow and stops before Task 9. If a lock unexpectedly appears before creation, the no-overwrite writer stops; this session does not rotate it automatically.

The remaining human checkpoint is to open or create the real workflow, ensure its native model metadata is present, select the real private input, and invoke the free Cloud Run capture/preflight. No offer search or paid GO is prepared in this design.

# Official Comfy Worker Runtime Implementation Plan

> **For Codex:** Execute continuously with `superpowers:executing-plans` and `superpowers:subagent-driven-development`. Each behavior change follows `superpowers:test-driven-development`. Use `superpowers:systematic-debugging` for unexpected failures and `superpowers:verification-before-completion` before every success claim.

**Goal:** Replace the blocked generic Vast base image with one immutable official ComfyUI runtime, enforce compatible single-GPU offers, and produce the reviewed release/template inputs needed for the real free workflow preflight.

**Architecture:** Keep the local Desktop pins and worker protocol unchanged. Pin the remote image by OCI index digest, run the baked ComfyUI directly from its venv and immutable workspace tree, represent remote Python as the verified `3.12` series, and enforce the same NVIDIA/amd64/CUDA/compute constraints at search, local revalidation, and template UI boundaries.

**Tech stack:** Python standard library, unittest, existing aiohttp runtime, GitHub CLI, reviewed Vast template transport.

---

## Global constraints

- Work only in `fix/vast-template-live-audit`; never modify the frozen `feat/vast-cloud-run-lifecycle` worktree.
- Use exactly the image, tag, child digest, config digest, paths, versions, and hardware floors in the companion design.
- Preserve every existing price, reliability, bandwidth, disk, verified-host, VRAM, one-GPU, transport, secret, deadline, and teardown constraint.
- Do not search live offers, create/rent an instance, transfer a model, execute a workflow, create a volume, or spend money.
- Do not publish a release or template until the complete gates, review, and explicit image-provenance decision succeed.
- Do not edit, replace, or delete the existing immutable release or any provider template.
- Stop after the local restart/readiness proof and before Task 9.

## Task 1: Enforce the hardware compatibility contract

**Files:**

- Modify: `tests/python/test_vast.py`
- Modify: `tests/python/test_offers.py`
- Modify: `cloud_run/vast.py`

1. Add valid `gpu_arch`, `cpu_arch`, `cuda_max_good`, and `compute_cap` fields to every valid fake offer.
2. Add table-driven rejection tests for absent, wrong-type, non-finite, and below-floor values.
3. Assert both bounded search payloads and the exact offer-ID revalidation contain the four new filters while preserving all old filters.
4. Run the focused tests and observe the intended failures.
5. Add the fixed filters to `build_search_payload()` and fail-closed validation to `normalize_offers()`.
6. Re-run the focused tests green.

## Task 2: Migrate the remote worker identity to the official image runtime

**Files:**

- Modify: `tests/python/test_worker_bootstrap.py`
- Modify: `tests/python/test_worker_provision.py`
- Modify: `tests/python/test_worker_release.py`
- Modify: `tests/python/test_worker_release_tools.py`
- Modify: `tests/python/test_artifacts.py`
- Modify: remote-release fixtures in service/lifecycle/fake integration tests
- Modify: `cloud_run/constants.py`
- Modify: `cloud_run/artifacts.py`
- Modify: `remote_worker/bootstrap.py`
- Modify: `remote_worker/comfy.py`
- Modify: `remote_worker/template-policy.json`

1. Change remote-release test fixtures from exact Python `3.13.12` to series `3.12`; leave `cloud_run/comfy_host.py` and its tests at local Python `3.13.12`.
2. Add failing health tests that accept `3.12.x` and reject `3.11.x`, `3.13.x`, and an unqualified `3.12` report.
3. Add failing wheel tests that accept compatible `cp312`/`py3` Linux wheels and reject `cp313` and incompatible ABIs/platforms.
4. Run the focused tests and observe the intended failures.
5. Implement the minimal remote constant, bootstrap lock, health prefix, policy, and wheel-tag changes.
6. Re-run all focused release/bootstrap/provision/artifact tests green.

## Task 3: Render and verify the exact official runtime template

**Files:**

- Modify: `tests/python/test_worker_release_tools.py`
- Modify: `tests/python/test_worker_template_api.py`
- Modify: `scripts/render_worker_template.py`
- Modify: `scripts/publish_worker_template.py`

1. Change the sanitized base-audit tests so the official template supplies only its identity plus exact `use_ssh` and `ssh_direct` flags; it is not an image or mutable `runtype` source.
2. Add failing renderer assertions for the exact official image/tag, the four fixed compatibility `extra_filters` plus `num_gpus == 1`, `/venv/main/bin/python`, and `CLOUD_RUN_COMFY_ROOT=/opt/workspace-internal/ComfyUI`.
3. Assert the generated `onstart` does not call the image entrypoint, Supervisor, the official ComfyUI wrapper, or a shell-derived executable.
4. Add failing publication tests requiring `extra_filters` in request validation, wildcard-row projection, duplicate reconciliation, and exact post-create readback.
5. Run the two focused test modules and observe the intended failures.
6. Implement the deterministic renderer and publisher changes, preserving the one-POST and secret-safe transport.
7. Re-run both focused modules green.

After the first authorized live audit, Vast returned `runtype="jupyter"` instead of the historical composite string while preserving both SSH flags. Add a regression test that excludes `runtype` and `jupyter_dir` from the base-audit query and record, keep the private request's exact `runtype="ssh"` and Jupyter flags unchanged, then re-run the real read-only audit before any publication.

## Task 4: Expose the exact loaded worker release

**Files:**

- Modify: `tests/python/test_routes.py`
- Modify: `cloud_run/routes.py`

1. Add failing settings-route tests requiring `worker_release == null` when service construction has no valid lock.
2. Add a failing route test whose supplied service contains a valid `WorkerRelease`; require exact equality with `release.to_record()` on the same-origin settings response.
3. Assert the response contains no lock path, archive URL, credential, token, session secret, workflow, model, or private template payload.
4. Run the focused route tests and observe the intended failures.
5. Add the minimal browser-safe projection from the service instance used by that request. Do not reload the file independently and do not weaken `WorkerRelease` validation.
6. Re-run route tests green.

## Task 5: Update truthful public documentation

**Files:**

- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify: `docs/project-state.md`
- Modify if required by the public contract: `tests/python/test_repository_contract.py`

1. Record the exact selected image identities and the distinction between immutable bytes and incomplete source-revision proof.
2. Record remote Python series `3.12`, direct venv/workspace startup, single-GPU constraints, and the retained local Python `3.13.12` pin.
3. State that release/template publication, local lock creation, restart, real workflow preflight, offer search, and GPU execution have not yet occurred.
4. Run documentation contracts and `git diff --check`.

## Task 6: Verify, review, and commit the implementation

1. Run every focused module changed above.
2. Run `scripts/check.sh` twice consecutively on the same tree.
3. Use `superpowers:requesting-code-review` because the runtime/image incompatibility and offer-filter gaps are new findings. Process demonstrated findings with `superpowers:receiving-code-review`, returning each fix to a failing regression first.
4. Re-run both full gates after any fix.
5. Stage only the intended files, run `git diff --cached --check`, and commit on `fix/vast-template-live-audit`.
6. Verify a clean worktree and record the exact reviewed commit. Do not merge PR `#2`.

## Task 7: Publication gate

1. Present the verified image evidence and its absent revision label. Obtain an explicit choice to accept that evidence or authorize verification of the digest-pinned attestation.
2. Reconfirm no release/tag/name collision and no existing project template using only the reviewed read-only commands.
3. Push the reviewed fix branch only if explicitly authorized.
4. Build the deterministic archive in a fresh private directory, verify it twice, create exactly one new draft GitHub Release targeting the reviewed commit, upload one asset, and publish it immutable only under explicit release authorization.
5. Verify the release, freshly download and verify the asset, then exercise the real bootstrap with `HttpsTransport` and a recording runner.
6. Audit the official Vast template read-only, render the exact private request, inspect it without printing private bodies, and create exactly one private template through `scripts/publish_worker_template.py` only under explicit template authorization.
7. Verify the exact readback and retain only the public private-template hash.

## Task 8: Create the local lock and stop before the real workflow

1. Reconfirm the expected local lock is absent, then create it with the existing no-overwrite writer. If a lock unexpectedly exists, stop rather than rotate or replace it.
2. Require parent mode `0700`, final mode `0600`, current ownership, regular-file/no-symlink checks, and exact metadata round-trip.
3. Restart only the pinned ComfyUI Desktop process and verify the exact worker commit/template hash are loaded and the Cloud Run UI/status endpoint is available.
4. Remove private temporary release/template material.
5. Stop before opening or capturing the workflow, before offer search, and before every paid action.

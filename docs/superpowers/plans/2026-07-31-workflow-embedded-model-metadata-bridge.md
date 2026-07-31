# Workflow-Embedded Model Metadata Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Cloud Run capture a workflow whose selected models are absent locally, resolve native ComfyUI `properties.models` annotations to verified immutable public Hugging Face artifacts during free preflight, provision them with useful sanitized progress, and immediately verified-destroy an unusable paid instance after deterministic provisioning failure.

**Architecture:** Keep the compiled prompt authoritative for active file requirements and treat workflow annotations only as untrusted source candidates. A recursive native-metadata index matches the exact flattened execution node, selected name, and ComfyUI directory; a narrowly allowlisted Hugging Face client pins and verifies candidates without downloading model bytes; the existing resolver converts verified candidates into the unchanged `ArtifactSpec`/`SourceSpec` manifest contract. The existing worker remains the installer and verifier. Backward-compatible observational status fields report direct worker downloads without changing `MANIFEST_SCHEMA_VERSION`, `PROTOCOL_VERSION`, request authority, or the immutable manifest. Deterministic terminal provisioning errors retain a sanitized durable diagnostic while the lifecycle immediately performs existing verified destruction.

**Tech Stack:** Python 3.13 standard library plus ComfyUI-provided `aiohttp`, immutable Cloud Run manifest/protocol v1, ComfyUI frontend 1.47.10 workflow JSON, vanilla browser JavaScript, Node's built-in test runner, SQLite, `unittest`, fake HTTP/provider boundaries, Hugging Face Hub read-only model-info API.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes` on `feat/vast-cloud-run-lifecycle`.
- Preserve approved design commit `8dda6e5` as an ancestor. Do not rebase, squash, rewrite, or clean unrelated user work.
- Before editing production code in every implementation task, read and follow `superpowers:test-driven-development`. Observe each specified regression fail for the intended reason before making it pass.
- If a failure is not the expected red state, stop that task and use `superpowers:systematic-debugging` before editing more code.
- This repository implements and certifies the Cloud Run consumer contract. Do not modify an Agent Panel repository here. If the existing Agent Panel producer cannot write native `node.properties.models`, report that as a separate repository/spec/worktree task.
- Keep the workflow parser read-only. Do not silently repair or mutate an unrelated workflow, and do not infer a source from a similar filename.
- Do not implement a second missing-model downloader or metadata format. The local proof certifies native ComfyUI action visibility only; generic localhost server-side placement remains outside Cloud Run.
- Leave custom-node discovery and approval behavior unchanged; this slice changes only static model source resolution.
- Support only public, ungated Hugging Face model repositories in this slice. Do not add Hugging Face credentials, gated access, Civitai, R2, arbitrary URLs, redirects, signed URLs, or URL guessing from filenames.
- Treat every workflow string and every remote JSON body as untrusted. Reject ambiguity, traversal, credentials, fragments, source substitution, missing immutable identity, unknown sizes, and missing digests before rental.
- Do not download model bytes during capture, free preflight, automated tests, or the local native-panel proof. Hugging Face network tests are fake and offline except for the separately identified read-only Gold preflight proof.
- Keep inputs local-first: an active image, mask, or video must already exist beneath the approved ComfyUI input root, must be hashed locally, and must use the existing verified local relay unless a separately approved cache source already exists.
- Keep `DependencyManifest`, `ArtifactSpec`, `SourceSpec`, `MANIFEST_SCHEMA_VERSION = 1`, and `PROTOCOL_VERSION = "1"` authoritative and unchanged. Optional provisioning progress is observational only and cannot approve a source, change a digest, or make a session rentable.
- Preserve backward compatibility with stored preflight rows and workers that omit the new optional progress object.
- Never expose a provider token, session secret, workflow candidate URL, local path, signed source URL, or raw remote error in a public payload or browser text.
- Do not create or edit a Vast template or a live `worker-release.json`. Do not publish an external worker release in this plan; the deterministic temporary archive exercised inside `scripts/check.sh` remains required test evidence.
- Do not call a Vast mutation endpoint, rent a GPU, transfer the private Gold input, click native `Download All`, or run the paid Gold workflow during implementation and free proof.
- Keep the user's workflow and input private. Do not commit their workflow JSON, input image, local paths, outputs, model files, credentials, settings, or database.
- A paid Gold run remains a separate authorization boundary. It requires a new explicit user message stating maximum instance count, maximum hourly price, and an absolute maximum duration or total cost.
- `scripts/check.sh` must pass twice consecutively after the complete implementation. Run `superpowers:verification-before-completion` before any completion claim and `superpowers:requesting-code-review` before the final handoff.

## Reference contracts

- Approved product design: `docs/superpowers/specs/2026-07-31-workflow-embedded-model-metadata-bridge-design.md`.
- Existing session/lifecycle design: `docs/superpowers/specs/2026-07-31-workflow-derived-vast-gpu-session-design.md`.
- Hugging Face's official client documents `model_info(repo_id, revision=REVISION, files_metadata=True)` as returning revision identity and per-file size/LFS metadata: <https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api>.
- The official client currently implements that read-only call as `GET /api/models/{repo_id}/revision/{revision}?blobs=true`; keep the origin and response contract explicit rather than importing a credential-aware Hub client: <https://github.com/huggingface/huggingface_hub/blob/main/src/huggingface_hub/hf_api.py>.

---

## File map

### New modules and tests

- `cloud_run/huggingface.py`: strict candidate URL parser and origin-confined, injectable, read-only Hugging Face metadata client.
- `cloud_run/model_metadata.py`: bounded recursive native workflow metadata parser and exact flattened-node lookup.
- `cloud_run/model_sources.py`: asynchronous coordinator from active static model requirements to verified `SourceSpec` records.
- `tests/python/test_huggingface.py`: fake-response URL, revision, size, digest, privacy, and origin tests.
- `tests/python/test_model_metadata.py`: flat, workflow-level, nested subgraph, stale, malformed, and conflict tests.
- `tests/python/test_model_sources.py`: deduplication, status mapping, and immutable source construction tests.
- `tests/fixtures/native-model-metadata-workflow.json`: synthetic model-absent native workflow used only to prove pinned-frontend recognition; it is never queued or downloaded.
- `docs/workflow-model-metadata-proof.md`: generated only after offline gates and free read-only proof produce exact evidence; never an empty scaffold.

### Existing resolver and UI files

- `cloud_run/artifacts.py`: shared static file-requirement enumeration and source-first model artifact path while retaining local input behavior.
- `cloud_run/resolver.py`: injected asynchronous workflow-model source resolution before synchronous artifact assembly.
- `cloud_run/routes.py`: runtime Hugging Face resolver wiring and sanitized public provisioning projection.
- `cloud_run/session_service.py`: preflight provenance, optional provisioning status validation, deterministic error type, and persisted progress.
- `cloud_run/job_repository.py`: durable bounded provisioning progress fields and latest-transaction lookup.
- `cloud_run/repository.py`: additive local SQLite migration for observational provisioning progress only.
- `web/js/session-console.js`: verified model provenance, destination/digest display, safe public repository link, and current-model progress.

### Existing worker and lifecycle files

- `remote_worker/provision.py`: bounded per-artifact transfer progress snapshots with no URL or secret.
- `remote_worker/transfers.py`: internal transfer-versus-digest-verification progress events; download semantics and verification remain unchanged.
- `remote_worker/server.py`: optional sanitized progress in existing manifest/transaction responses.
- `remote_worker/state.py`: validated atomic storage of the sanitized progress snapshot.
- `cloud_run/lifecycle.py`: immediate verified destruction and diagnostic preservation for typed terminal provisioning failures.

### Existing test coverage to extend

- `tests/python/test_artifacts.py`
- `tests/python/test_resolver.py`
- `tests/python/test_routes.py`
- `tests/python/test_session_service.py`
- `tests/python/test_job_repository.py`
- `tests/python/test_worker_provision.py`
- `tests/python/test_worker_server.py`
- `tests/python/test_lifecycle.py`
- `tests/python/test_fake_session_integration.py`
- `tests/python/test_no_mutation_surface.py`
- `tests/js/canvas-adapter.test.mjs`
- `tests/js/session-console.test.mjs`

---

### Task 1: Resolve and verify immutable public Hugging Face files

**Files:**
- Create: `cloud_run/huggingface.py`
- Create: `tests/python/test_huggingface.py`

**Interfaces:**

```python
class HuggingFaceError(RuntimeError):
    """A sanitized Hugging Face candidate or metadata failure."""


@dataclass(frozen=True)
class HuggingFaceReference:
    repository_id: str
    revision: str
    file_path: str


@dataclass(frozen=True)
class ResolvedHuggingFaceFile:
    repository_id: str
    file_path: str
    immutable_revision: str
    locator: str
    size_bytes: int
    sha256: str
```

Required callable signatures are `parse_huggingface_url(value: str) ->
HuggingFaceReference`, `HuggingFaceClient(*, session=None, timeout=30.0,
max_response_bytes=2 * 1024 * 1024)`, and
`HuggingFaceClient.resolve(reference: HuggingFaceReference, *,
expected_sha256: str | None = None) -> ResolvedHuggingFaceFile` (async).

- [ ] **Step 1: Invoke the TDD skill and write the URL-parser tests**

Cover one accepted mutable URL, one accepted 40-character revision, and table-driven rejection of HTTP, another host, user info, ports, fragments, percent escapes, duplicate or unknown query fields, absolute/traversal-like file segments, missing owner/repository/revision/file, and overlong values. Accept no query or exactly `download=true`; canonical output always removes the query.

Use this first positive assertion:

```python
reference = parse_huggingface_url(
    "https://huggingface.co/black-forest-labs/FLUX.1-Fill-dev/"
    "resolve/main/flux1-fill-dev.safetensors?download=true"
)
self.assertEqual(
    reference,
    HuggingFaceReference(
        repository_id="black-forest-labs/FLUX.1-Fill-dev",
        revision="main",
        file_path="flux1-fill-dev.safetensors",
    ),
)
```

- [ ] **Step 2: Run the parser test and observe the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_huggingface.HuggingFaceUrlTests -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cloud_run.huggingface'`.

- [ ] **Step 3: Implement the minimal strict parser**

Use `urllib.parse.urlsplit` and `parse_qsl`. Require exactly `https`, hostname `huggingface.co`, no user info or port, no fragment, no `%` or backslash anywhere, bounded ASCII path segments, exactly `<owner>/<repo>/resolve/<revision>/<file/path>`, and safe relative file parts. Preserve case; do not normalize a repository or filename by lowercasing it.

Start the module with these explicit bounds:

```python
HUGGINGFACE_ORIGIN = "https://huggingface.co"
MAX_HUGGINGFACE_RESPONSE_BYTES = 2 * 1024 * 1024
HUGGINGFACE_TIMEOUT_SECONDS = 30.0
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_REPO_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
_PATH_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}")
```

Do not follow redirects and do not accept `hf://`, `/blob/`, datasets, spaces, or custom Hub endpoints in this slice.

- [ ] **Step 4: Make the parser suite green**

Run:

```sh
python3 -m unittest \
  tests.python.test_huggingface.HuggingFaceUrlTests -v
```

Expected: all parser cases pass.

- [ ] **Step 5: Add fake-network red tests for immutable metadata**

Implement test-only `FakeResponse` and `FakeSession` objects compatible with `await session.get(request_url, allow_redirects=False, timeout=30.0)`. Record every requested URL and return bounded JSON bytes.

The mutable-revision happy path must require two calls:

```python
mutable = {
    "id": "example/public-model",
    "sha": "a" * 40,
    "private": False,
    "gated": False,
    "siblings": [],
}
pinned = {
    "id": "example/public-model",
    "sha": "a" * 40,
    "private": False,
    "gated": False,
    "siblings": [{
        "rfilename": "models/example.safetensors",
        "size": 4096,
        "lfs": {"sha256": "b" * 64, "size": 4096},
    }],
}
```

Assert that a mutable revision first resolves to the response `sha`, then the exact commit is queried with `blobs=true`. An already pinned commit makes only the exact commit query. Add failures for a non-200 response, redirect status, response URL different from the request, invalid JSON/UTF-8, body over 2 MiB, substituted repository `id`, substituted `sha`, `private is not False`, `gated is not False`, missing or duplicate exact sibling, non-LFS file, absent/invalid LFS size, absent/invalid LFS SHA-256, sibling/LFS size disagreement, and embedded-hash mismatch.

- [ ] **Step 6: Run the client tests and observe the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_huggingface.HuggingFaceClientTests -v
```

Expected: FAIL because `HuggingFaceClient` is not implemented.

- [ ] **Step 7: Implement the origin-confined metadata client**

Mirror the repository's `RegistryClient` ownership pattern for `aiohttp`: use an injected session in tests, otherwise create a bounded session with `trust_env=False`, close only the owned session, release responses, pass `allow_redirects=False`, require the final response URL to equal the exact request URL, and never send an authorization header. Read the body incrementally and abort after `MAX_HUGGINGFACE_RESPONSE_BYTES + 1`; checking `Content-Length` alone or calling an unbounded `response.read()` is insufficient.

Build only these API URLs:

```python
def _model_info_url(reference, revision, *, blobs):
    base = (
        HUGGINGFACE_ORIGIN
        + "/api/models/"
        + reference.repository_id
        + "/revision/"
        + quote(revision, safe="")
    )
    return base + ("?blobs=true" if blobs else "")
```

For mutable revisions, query once without blobs, require exact repository `id` and a lowercase 40-character `sha`, then query that exact commit with blobs. For an already pinned commit, query it once with blobs. The pinned response must repeat the exact repository `id` and same `sha`, be explicitly public and ungated, and contain one exact sibling whose positive LFS size and lowercase LFS SHA-256 agree with any duplicate size and any embedded expected digest.

Construct exactly:

```python
locator = (
    HUGGINGFACE_ORIGIN
    + "/"
    + reference.repository_id
    + "/resolve/"
    + immutable_revision
    + "/"
    + reference.file_path
)
```

Validate the result before returning it. Never issue a `HEAD` or `GET` against model bytes during preflight.

- [ ] **Step 8: Verify the complete isolated client and commit**

Run:

```sh
python3 -m unittest tests.python.test_huggingface -v
git diff --check
git add cloud_run/huggingface.py tests/python/test_huggingface.py
git diff --cached --check
git commit -m "feat: verify immutable Hugging Face model files"
```

Expected: all tests pass; the commit contains only the client and its offline tests.

---

### Task 2: Parse native ComfyUI model metadata with flattened node identity

**Files:**
- Create: `cloud_run/model_metadata.py`
- Create: `tests/python/test_model_metadata.py`

**Interfaces:**

```python
class ModelMetadataError(RuntimeError):
    """Native workflow model metadata is invalid or exceeds bounds."""


@dataclass(frozen=True)
class EmbeddedModelCandidate:
    node_id: str | None
    name: str
    directory: str
    reference: HuggingFaceReference
    expected_sha256: str | None


@dataclass(frozen=True)
class EmbeddedModelLookup:
    status: str
    candidate: EmbeddedModelCandidate | None
    reason: str | None
```

Required methods are `EmbeddedModelIndex.from_workflow(workflow: dict) ->
EmbeddedModelIndex` and `lookup(*, node_id: str, name: str, directory: str) ->
EmbeddedModelLookup`.

- [ ] **Step 1: Invoke TDD and write flat-node contract tests**

Use a workflow node with `widgets_values` selecting `example.safetensors` and this native property:

```python
"properties": {
    "models": [{
        "name": "example.safetensors",
        "url": (
            "https://huggingface.co/example/public-model/"
            "resolve/main/files/example.safetensors"
        ),
        "directory": "diffusion_models",
        "hash": "c" * 64,
        "hash_type": "sha256",
    }]
}
```

Assert exact node/name/directory lookup returns `candidate`; a different selected name or directory returns `mapping_required`; a stale record for another selected value is never returned; identical duplicates collapse; conflicting URL or hash duplicates return `mapping_required`; an exact malformed record returns `unsupported` without a network call.

- [ ] **Step 2: Add nested subgraph and workflow-level fallback tests**

Represent nested definitions with `workflow["definitions"]["subgraphs"]`. A root container node whose `type` is definition `outer` and ID `10`, containing a nested container ID `7` for definition `inner`, must make inner node ID `4` addressable only as `10:7:4`. Instantiate `outer` twice and prove the two execution paths remain independent.

Also prove:

- per-node exact metadata wins over a workflow-level exact fallback;
- workflow-level `models` is used only when `(name, directory)` has one normalized candidate;
- per-node metadata from another node is never used as fallback;
- unreferenced subgraph definitions and inactive nodes do not initiate lookup work;
- malformed workflow containers, cycles, excessive depth, excessive nodes, and excessive records fail closed with `ModelMetadataError`.

- [ ] **Step 3: Run the metadata tests and observe the red state**

Run:

```sh
python3 -m unittest tests.python.test_model_metadata -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cloud_run.model_metadata'`.

- [ ] **Step 4: Implement bounded recursive flattening and strict records**

Port the pinned frontend's execution identity rule, not its UI code:

1. index each subgraph definition by exact ID;
2. walk root nodes in stable order;
3. when a node `type` names a definition, descend with the node's flattened ID as prefix;
4. address a concrete node as `prefix:node_id` or its root `node_id`;
5. track the active definition stack to reject cycles;
6. enforce explicit maximum depth, total node, and model-record counts.

Validate each record as exactly `name`, `url`, `directory`, plus either neither hash field or both `hash` and `hash_type`. Require a safe relative `name`, category grammar `[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}`, `hash_type == "sha256"`, lowercase 64-hex hash, and `parse_huggingface_url(url)` success.

Keep malformed exact-key diagnostics in the index so `lookup()` can distinguish `unsupported` from a merely missing mapping. Deduplicate only candidates with equal normalized reference and equal non-null expected hashes; merge null/non-null hashes only when all supplied hashes agree.

- [ ] **Step 5: Verify deterministic lookup behavior and commit**

Run:

```sh
python3 -m unittest tests.python.test_model_metadata -v
python3 -m unittest tests.python.test_capture -v
git diff --check
git add cloud_run/model_metadata.py tests/python/test_model_metadata.py
git diff --cached --check
git commit -m "feat: parse native workflow model metadata"
```

Expected: all metadata and capture tests pass; no runtime network boundary changed.

---

### Task 3: Coordinate active model requirements with verified sources

**Files:**
- Create: `cloud_run/model_sources.py`
- Create: `tests/python/test_model_sources.py`
- Modify: `cloud_run/artifacts.py`
- Modify: `tests/python/test_artifacts.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class StaticFileRequirement:
    node_id: str
    class_type: str
    input_name: str
    metadata: FileInputMetadata
    value: object


@dataclass(frozen=True)
class ModelSourceResolution:
    status: str
    source: SourceSpec | None
    size_bytes: int | None
    sha256: str | None
    reason: str | None
```

Required callables are `static_file_requirements(capture, metadata) ->
tuple[StaticFileRequirement, ...]` and the async
`WorkflowModelSourceResolver(client).resolve(capture, *, requirements:
tuple[StaticFileRequirement, ...]) -> dict[tuple[str, str],
ModelSourceResolution]`.

- [ ] **Step 1: Invoke TDD and freeze current static-requirement behavior**

Add tests for `static_file_requirements()` using callable metadata, an object exposing `file_input_metadata`, and a mapping. Preserve compiled-output order and metadata insertion order. Include model, input, missing, linked/non-string, and invalid metadata cases.

- [ ] **Step 2: Observe the helper red state, then extract the enumeration once**

Run:

```sh
python3 -m unittest \
  tests.python.test_artifacts.StaticFileRequirementTests -v
```

Expected: FAIL because `static_file_requirements` does not exist.

Extract only the current output/metadata traversal from `resolve_artifacts()` into the new immutable requirement helper. Make `resolve_artifacts()` consume the helper internally so model-source and artifact paths cannot disagree about node IDs, class types, input names, or file categories.

Re-run:

```sh
python3 -m unittest tests.python.test_artifacts -v
```

Expected: all existing artifact tests plus the helper tests pass without behavior change.

- [ ] **Step 3: Write source-coordinator red tests with a fake client**

Create a `FakeHuggingFaceClient` whose async `resolve()` records references and returns configured `ResolvedHuggingFaceFile` values or raises `HuggingFaceError`.

Prove that the coordinator:

- looks up only active static requirements whose metadata kind is `model`;
- keys results by exact `(node_id, input_name)`;
- maps missing/mismatched/conflicting metadata to `mapping_required`;
- maps malformed or unverifiable Hugging Face metadata to `unsupported` with a static sanitized reason;
- creates `SourceSpec(kind="huggingface", locator=<commit URL>, immutable_revision=<commit>)` only for verified results;
- deduplicates identical normalized `(reference, expected_sha256)` requests across nodes;
- resolves distinct models concurrently with `asyncio.gather` while returning rows in requirement order;
- never passes a workflow URL through as a `SourceSpec` locator.

- [ ] **Step 4: Run and observe the coordinator red state**

Run:

```sh
python3 -m unittest tests.python.test_model_sources -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'cloud_run.model_sources'`.

- [ ] **Step 5: Implement the coordinator and validate every constructed source**

Build `EmbeddedModelIndex.from_workflow(capture.workflow)` once. For each static model string, call exact lookup with the compiled node ID, selected string, and host-derived category. Group verified candidates before creating tasks. Convert only `ResolvedHuggingFaceFile` into `SourceSpec`, then call `validate_dependency(source)`.

Use fixed public reasons such as:

```python
"Native model metadata is missing or ambiguous."
"Native model metadata is invalid."
"The public Hugging Face file could not be verified."
```

Do not include the candidate URL, HTTP response, repository error body, or local path in a reason.

- [ ] **Step 6: Verify coordinator, artifacts, and commit**

Run:

```sh
python3 -m unittest tests.python.test_model_sources -v
python3 -m unittest tests.python.test_artifacts -v
git diff --check
git add \
  cloud_run/artifacts.py \
  cloud_run/model_sources.py \
  tests/python/test_artifacts.py \
  tests/python/test_model_sources.py
git diff --cached --check
git commit -m "feat: resolve workflow model source candidates"
```

Expected: all tests pass; no local model requirement has changed yet.

---

### Task 4: Add the source-first model artifact path and runtime wiring

**Files:**
- Modify: `cloud_run/artifacts.py`
- Modify: `cloud_run/resolver.py`
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_artifacts.py`
- Modify: `tests/python/test_resolver.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/js/canvas-adapter.test.mjs`
- Create: `tests/fixtures/native-model-metadata-workflow.json`

**Interfaces:** retain the existing required arguments of
`resolve_artifacts()` and add keyword-only `model_sources=None` and
`requirements=None`. Retain the existing required arguments of
`DependencyResolver.__init__()` and add keyword-only
`model_source_resolver=None`.

- [ ] **Step 1: Invoke TDD and write source-first artifact regressions**

Add focused tests for:

1. an annotated verified model absent from every local root resolves to one model `ArtifactSpec`;
2. its destination is exactly `models/<directory>/<selected-relative-name>`;
3. a matching local file and verified source produce one artifact and one local record;
4. a local file whose size or SHA differs from the verified source returns `unsupported` and no artifact;
5. two references with equal destination/digest deduplicate;
6. two digests for one destination raise `ArtifactCollisionError`;
7. `diffusion_models`, `text_encoders`, `vae`, and `upscale_models` preserve their exact directories;
8. a missing native candidate may still use an existing approved local digest mapping, but an active malformed or conflicting candidate cannot be bypassed;
9. local input behavior remains byte-for-byte unchanged and a missing input remains `unsupported`.

- [ ] **Step 2: Run targeted tests and observe the intended red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_artifacts.ArtifactResolutionTests -v
```

Expected: the new absent-local verified-source test fails because the current resolver always calls `_find_asset()` for models.

- [ ] **Step 3: Implement the minimal source-first branch**

For a model requirement, compute safe relative name and destination before optional local lookup. Use the verified result as immutable identity when present. If a local file exists, hash it and require exact size/digest equality. If it does not exist, do not create a `ResolvedLocalArtifact`; build the existing `ArtifactSpec` directly from verified size, SHA-256, and source.

Keep this precedence explicit:

```text
invalid/conflicting active annotation -> blocked
verified workflow source + absent local model -> resolved
verified workflow source + matching local model -> resolved
verified workflow source + colliding local model -> blocked
missing annotation + local model + approved digest mapping -> existing behavior
missing annotation without approved identity -> mapping_required
input media -> existing mandatory-local behavior
```

Do not change `ArtifactSpec`, `SourceSpec`, collision rejection, local hashing, or disk calculation.

- [ ] **Step 4: Add asynchronous resolution before artifact assembly**

In `DependencyResolver.resolve_dependencies()`:

```python
requirements = static_file_requirements(capture, metadata)
model_sources = {}
if self.model_source_resolver is not None:
    model_sources = await self.model_source_resolver.resolve(
        capture,
        requirements=requirements,
    )
artifact_result = resolve_artifacts(
    capture,
    metadata=metadata,
    model_roots=model_roots,
    input_root=input_root,
    source_mappings=source_mappings,
    model_sources=model_sources,
    requirements=requirements,
)
```

Preserve the injected `None` behavior for existing unit tests and non-runtime callers.

- [ ] **Step 5: Wire the real read-only client only at the runtime boundary**

In `_RuntimeResolver.resolve_preflight()`, inject:

```python
model_source_resolver=WorkflowModelSourceResolver(
    HuggingFaceClient()
),
```

Keep `_runtime_resolution_context()` free of source mappings and credentials. Extend route tests by injecting a fake model-source resolver; no route test may reach the internet.

- [ ] **Step 6: Add a synthetic native fixture and prove capture preservation**

Create a loadable, unconnected, never-queued pinned-frontend workflow fixture whose active `UNETLoader` selects `cloud-run-native-proof.safetensors`. Its essential node contract is:

```json
{
  "last_node_id": 1,
  "last_link_id": 0,
  "nodes": [{
    "id": 1,
    "type": "UNETLoader",
    "pos": [0, 0],
    "size": [315, 82],
    "flags": {},
    "order": 0,
    "mode": 0,
    "inputs": [],
    "outputs": [{
      "name": "MODEL",
      "type": "MODEL",
      "links": null
    }],
    "properties": {
      "Node name for S&R": "UNETLoader",
      "models": [{
        "name": "cloud-run-native-proof.safetensors",
        "url": "https://huggingface.co/example/public-model/resolve/main/cloud-run-native-proof.safetensors",
        "directory": "diffusion_models"
      }]
    },
    "widgets_values": [
      "cloud-run-native-proof.safetensors",
      "default"
    ]
  }],
  "links": [],
  "groups": [],
  "config": {},
  "extra": {"ds": {"scale": 1, "offset": [0, 0]}},
  "version": 0.4
}
```

The deliberately synthetic source is sufficient for native missing-model UI recognition and must never be clicked or sent to live preflight. It is not evidence that the synthetic repository exists.

Extend `tests/js/canvas-adapter.test.mjs` with this fixture plus an in-memory nested workflow containing optional hashes, workflow-level `models`, and subgraph definitions. Capture through the existing adapter and assert deep equality of the captured `workflow` annotations. Also assert the existing `workflow`, `output`, `queue_options` top-level capture shape and prompt digest behavior remain unchanged.

Run:

```sh
node --test tests/js/canvas-adapter.test.mjs
python3 -m unittest \
  tests.python.test_artifacts \
  tests.python.test_resolver \
  tests.python.test_routes -v
```

Expected: all tests pass and no model bytes or provider calls occur.

- [ ] **Step 7: Verify manifest/protocol compatibility and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_manifest \
  tests.python.test_worker_protocol \
  tests.python.test_artifacts \
  tests.python.test_resolver \
  tests.python.test_routes -v
node --test tests/js/canvas-adapter.test.mjs
git diff --check
git add \
  cloud_run/artifacts.py \
  cloud_run/resolver.py \
  cloud_run/routes.py \
  tests/python/test_artifacts.py \
  tests/python/test_resolver.py \
  tests/python/test_routes.py \
  tests/js/canvas-adapter.test.mjs \
  tests/fixtures/native-model-metadata-workflow.json
git diff --cached --check
git commit -m "feat: resolve models without local copies"
```

Expected: schema/protocol tests pass unchanged; the commit contains only source-first resolution and capture/runtime wiring.

---

### Task 5: Expose sanitized pinned provenance and preserve rental gating

**Files:**
- Modify: `cloud_run/session_service.py`
- Modify: `web/js/session-console.js`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_fake_session_integration.py`
- Modify: `tests/js/session-console.test.mjs`

**Interface change:** add `source_locator: str | None = None` to `PreflightRow`. It is public only for a validated `huggingface` source and is optional when reading legacy stored rows.

- [ ] **Step 1: Invoke TDD and write preflight serialization regressions**

Add Python tests proving:

- a resolved Hugging Face artifact row exposes its canonical commit-pinned locator;
- a local-upload, R2, custom-node, unresolved, or malformed row cannot expose a locator;
- the locator and immutable revision are jointly validated through the existing `SourceSpec` contract;
- old stored rows without `source_locator` still load with `None`;
- new rows round-trip through `PreflightResult.public_payload()` and durable preflight storage;
- any non-resolved row still makes `rentable` false and blocks offer search/confirmation.

- [ ] **Step 2: Run Python regressions and observe the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.PreflightRowTests \
  tests.python.test_fake_session_integration -v
```

Expected: the new source-locator assertions fail because `PreflightRow` does not carry it.

- [ ] **Step 3: Implement the narrow public provenance field**

Set `source_locator=artifact.source.locator` only when `artifact.source.kind == "huggingface"`. In `PreflightRow.__post_init__`, require resolved status, 40-character revision, and successful validation of `SourceSpec(kind="huggingface", locator=self.source_locator, immutable_revision=self.immutable_revision)`. For every other source kind require `source_locator is None`.

Update `from_payload()` to accept exactly the old field set or the new field set; default the missing field to `None`. Do not loosen any other exact stored-payload validation.

- [ ] **Step 4: Write browser red tests for safe provenance rendering**

Extend the session-console tests with a resolved model row. Require visible exact destination, repository/path text, abbreviated SHA-256, immutable revision, exact formatted size, and one link to the public repository.

Add hostile rows with another host, user info, port, fragment, unexpected path, percent encoding, HTML text, and `javascript:`. Assert hostile values remain inert text and no anchor is created.

- [ ] **Step 5: Implement a defensive Hugging Face view helper**

Use `new URL(locator)` inside `try/catch`, then require protocol `https:`, hostname exactly `huggingface.co`, empty username/password/port/hash/search, path form `<owner>/<repo>/resolve/<40hex>/<file/path>`, and safe path components. Derive a repository link from only the validated first two path components.

Build every label with `textContent`. Build the link with `setAttribute`, `target="_blank"`, and `rel="noopener noreferrer"`. Do not use `innerHTML` or insert a raw URL into HTML.

Render destination and abbreviated digest only after strict validation:

```javascript
const digest = typeof item?.sha256 === "string"
  && /^[0-9a-f]{64}$/.test(item.sha256)
  ? item.sha256.slice(0, 12)
  : null;
```

- [ ] **Step 6: Verify UI, gating, persistence, and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service \
  tests.python.test_fake_session_integration -v
node --test tests/js/session-console.test.mjs
git diff --check
git add \
  cloud_run/session_service.py \
  web/js/session-console.js \
  tests/python/test_session_service.py \
  tests/python/test_fake_session_integration.py \
  tests/js/session-console.test.mjs
git diff --cached --check
git commit -m "feat: show verified model provenance"
```

Expected: safe provenance is visible, hostile data is inert, and every unresolved dependency still gates rental.

---

### Task 6: Report direct worker model-download progress without changing authority

**Files:**
- Modify: `remote_worker/provision.py`
- Modify: `remote_worker/transfers.py`
- Modify: `remote_worker/server.py`
- Modify: `remote_worker/state.py`
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/job_repository.py`
- Modify: `cloud_run/repository.py`
- Modify: `cloud_run/routes.py`
- Modify: `web/js/session-console.js`
- Modify: `tests/python/test_worker_provision.py`
- Modify: `tests/python/test_worker_transfers.py`
- Modify: `tests/python/test_worker_server.py`
- Modify: `tests/python/test_job_repository.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_routes.py`
- Modify: `tests/js/session-console.test.mjs`

**Observational payload:**

```json
{
  "phase": "model_transfer",
  "dependency_id": "model-<sha256>",
  "transferred_bytes": 1048576,
  "total_bytes": 8388608
}
```

The example shape is normative but the byte values in a valid payload must satisfy `0 <= transferred_bytes <= total_bytes`. `dependency_id` is an immutable artifact ID, never a filename or URL. Accepted phases are `dependency_transfer`, `model_transfer`, `digest_verification`, `comfyui_startup`, `environment_validation`, and `ready`.

- [ ] **Step 1: Invoke TDD and write worker progress red tests**

In worker tests, drive two fake artifacts through `WorkerArtifactProvider` and assert progress is monotonic, bounded, identifies only the current artifact ID, advances phase at transfer/digest verification/startup/environment validation, and ends at exact total bytes. Extend transfer-manager tests to prove it emits `transferring` before `verifying` and never reports verified completion before size/SHA validation. Assert transaction state contains no source URL, local path, token, secret, repository ID, or raw exception.

Add an old-shape compatibility test proving `ProvisionResult.payload()` without progress remains accepted by the controller.

- [ ] **Step 2: Run targeted worker tests and observe red**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_provision \
  tests.python.test_worker_transfers \
  tests.python.test_worker_server -v
```

Expected: new progress assertions fail because `_ProgressTracker` currently records only a timestamp and `ProvisionResult.payload()` omits progress.

- [ ] **Step 3: Implement bounded worker progress snapshots**

Give `_ProgressTracker` the manifest transfer catalog and a sanitized persistence callback. Track verified offset per artifact, aggregate with `sum(offsets.values())`, and reject regressions, unknown IDs, offsets beyond declared size, and invalid phases.

Change the internal transfer-manager callback from `(artifact_id, offset)` to
`(artifact_id, offset, event)`, where event is exactly `transferring`,
`verifying`, or `verified`. Emit `verifying` immediately before hashing the
complete staging file and `verified` only after size/SHA validation and atomic
installation. Change the provider callback to retain artifact identity and map
the internal event to a public phase:

```python
self.manager.progress = (
    lambda artifact_id, offset, event: progress(
        "digest_verification"
        if event == "verifying"
        else phase_by_artifact_id[artifact_id],
        artifact_id,
        offset,
    )
)
```

Build `phase_by_artifact_id` from the already validated transfer catalog: use
`model_transfer` when the artifact kind is `model` and
`dependency_transfer` otherwise. Update the already-installed fast path with
the same artifact ID and exact declared size. Persist on a phase/artifact
change, completion, or a bounded time/byte threshold so a large download does
not rewrite worker state for every network chunk. Set `comfyui_startup`
immediately before ensuring/restarting ComfyUI and `environment_validation`
immediately before `_validate()`. The final ready snapshot must report exact
total bytes and no current dependency.

Add optional `progress` to `ProvisionResult` and its payload. Keep all existing required fields and `PROTOCOL_VERSION` unchanged. The server must return only the validated sanitized snapshot; it must never copy arbitrary transaction-record fields.

- [ ] **Step 4: Add durable controller progress storage tests**

Extend local `provision_transactions` with nullable `phase`, nullable `current_dependency_id`, non-negative `transferred_bytes`, and non-negative `total_bytes`. Add an idempotent migration for existing databases, bump only the local database schema metadata, and test both a fresh database and a legacy table missing the new columns.

Add `record_provision_progress(*, transaction_id, session_id, job_id, manifest_digest, state, phase, current_dependency_id, transferred_bytes, total_bytes, last_progress_at, sanitized_error=None)` with an upsert keyed by transaction ID and `latest_provision_transaction(session_id)`. Validate identifiers, phase enum, finite timestamps, byte bounds, and sanitized errors at the repository boundary.

- [ ] **Step 5: Observe repository red, implement migration, and verify**

Run before implementation:

```sh
python3 -m unittest tests.python.test_job_repository -v
```

Expected: new progress persistence/migration tests fail.

After implementation, run the same command and expect all tests to pass. Do not store a workflow URL, signed URL, provider credential, or filename in these columns.

- [ ] **Step 6: Poll the existing worker transaction while apply is active**

In `_apply_manifest()`, start the existing `worker.apply_manifest(request)` as a task. While it is pending, poll the deterministic transaction ID `provision-<manifest digest>` through the existing signed `worker.transaction()` method at the configured bounded poll interval. Accept absence before the worker creates the record. Validate every returned old or extended payload, persist only the sanitized progress object, and always await/cancel and consume the apply task safely on cancellation or failure. Validate and persist the final apply response's progress as well, so a fast worker still records its `ready` snapshot even when no intermediate poll ran.

Keep the existing upload handshake and three-attempt bound. A progress response cannot satisfy ready state, alter required uploads, replace the final apply response, or extend a deadline.

Map `dependency_id` to `ArtifactSpec.logical_name` only inside `_session_payload()` using the locally stored immutable manifest. Expose `current_model` only when the ID belongs to a model artifact. Compute the public aggregate from the manifest's exact transfer total and bounded worker/local relay offsets; never expose the worker artifact URL.

- [ ] **Step 7: Render current phase/model and test hostile progress**

Retain existing instance-creation and worker-startup labels from session state. During provisioning show the validated worker phase, `transferred_bytes of total_bytes`, and safe current model name. Test a model name containing HTML characters remains text, an unknown phase falls back to the session phase, invalid bytes are ignored, and no URL appears.

- [ ] **Step 8: Verify old/new worker compatibility and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_worker_provision \
  tests.python.test_worker_transfers \
  tests.python.test_worker_server \
  tests.python.test_job_repository \
  tests.python.test_session_service \
  tests.python.test_routes -v
node --test tests/js/session-console.test.mjs
git diff --check
git add \
  remote_worker/provision.py \
  remote_worker/transfers.py \
  remote_worker/server.py \
  remote_worker/state.py \
  cloud_run/session_service.py \
  cloud_run/job_repository.py \
  cloud_run/repository.py \
  cloud_run/routes.py \
  web/js/session-console.js \
  tests/python/test_worker_provision.py \
  tests/python/test_worker_transfers.py \
  tests/python/test_worker_server.py \
  tests/python/test_job_repository.py \
  tests/python/test_session_service.py \
  tests/python/test_routes.py \
  tests/js/session-console.test.mjs
git diff --cached --check
git commit -m "feat: report verified provisioning progress"
```

Expected: both legacy and extended worker response tests pass; manifest/protocol constants remain unchanged.

---

### Task 7: Immediately verified-destroy deterministic terminal provisioning failures

**Files:**
- Modify: `cloud_run/session_service.py`
- Modify: `cloud_run/lifecycle.py`
- Modify: `tests/python/test_session_service.py`
- Modify: `tests/python/test_lifecycle.py`
- Modify: `tests/python/test_fake_lifecycle_integration.py`

**Interface:**

```python
class TerminalProvisioningError(SessionExecutionError):
    """A sanitized deterministic failure after bounded recovery is exhausted."""
```

- [ ] **Step 1: Invoke TDD and classify deterministic response failures**

Write targeted tests proving these post-response conditions raise `TerminalProvisioningError`:

- invalid provisioning response shape or identity;
- a required upload unknown to the immutable catalog;
- a required upload whose source is not `local-upload`;
- invalid progress or upload receipt identity/size/digest;
- worker state `failed` or `stalled` after its repair budget;
- a final transaction that is valid but not ready;
- remote deadline response inconsistent with the already-approved policy after provisioning.

Keep transport unavailability, cancellation, and keyboard interruption on their current paths so the lifecycle's bounded boot/replacement behavior remains available. Keep healthy native prompt execution errors as ordinary job failures on a reusable ready session.

- [ ] **Step 2: Run classification tests and observe red**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.SessionProvisioningTests -v
```

Expected: new assertions fail because every provisioning failure currently uses `SessionExecutionError`.

- [ ] **Step 3: Implement the minimal typed boundary**

Raise `TerminalProvisioningError` only after a deterministic worker response or locally verified identity contradiction. Preserve the existing static sanitized messages. Do not attach the caught exception or response body.

- [ ] **Step 4: Write lifecycle red tests for immediate verified destruction**

Make `reconcile_session_once()` raise a typed terminal error for a session with one known instance. Assert `wait_until_session_ready()` does not sleep to the boot deadline, invokes `destroy_instance` exactly once, invokes fresh `list_instances`, and ends:

```text
state = destroyed
instance_id = null
billing_may_continue = false
sanitized_error = original static provisioning diagnostic
```

Add failure cases where destroy raises, inventory is unavailable, or the labeled instance remains. Those must end `failed`, retain instance/residual inventory, set `billing_may_continue = true`, and replace the provisioning diagnostic with the existing urgent residual-billing guidance.

Also add a job-delta provisioning case: a typed terminal failure after a ready session requests the same verified destruction, while a normal ComfyUI execution error leaves the session ready and does not call the provider.

- [ ] **Step 5: Run lifecycle tests and observe red**

Run:

```sh
python3 -m unittest \
  tests.python.test_lifecycle \
  tests.python.test_fake_lifecycle_integration -v
```

Expected: immediate-destroy assertions fail because `wait_until_session_ready()` currently swallows the exception until timeout and successful destruction clears `sanitized_error`.

- [ ] **Step 6: Preserve diagnostics only for the automatic terminal path**

Change the internal finalizer signature to
`_finalize_session_destroyed(self, session, *, terminal_error=None)`. Its
existing transition logic writes `terminal_error` only on the automatic
terminal-provisioning path.

Thread that value only through automatic terminal-provisioning destruction. Normal user-requested destruction and deadline destruction retain existing clearing behavior. A residual-billing failure always overrides the diagnostic with urgent manual-recovery guidance.

Catch `TerminalProvisioningError` before the broad exception in `wait_until_session_ready()`, transition the session to failed with the static diagnostic, then call `destroy_session(session_id, terminal_error=diagnostic)` immediately. Add the equivalent explicit helper call for a typed provisioning failure while applying a compatible delta to a ready session.

- [ ] **Step 7: Verify lifecycle safety and commit**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service \
  tests.python.test_lifecycle \
  tests.python.test_fake_lifecycle_integration -v
git diff --check
git add \
  cloud_run/session_service.py \
  cloud_run/lifecycle.py \
  tests/python/test_session_service.py \
  tests/python/test_lifecycle.py \
  tests/python/test_fake_lifecycle_integration.py
git diff --cached --check
git commit -m "fix: destroy terminal provisioning failures"
```

Expected: successful verified destruction preserves the diagnostic without a billing warning; failed verification exposes the billing warning; healthy execution errors remain reusable.

---

### Task 8: Certify the bridge offline end to end

**Files:**
- Modify: `tests/python/test_fake_session_integration.py`
- Modify: `tests/python/test_no_mutation_surface.py`
- Modify as required by failures: only files already in Tasks 1-7

- [ ] **Step 1: Invoke TDD and add one complete fake source-first workflow**

Build the executable fake capture in Python from the committed synthetic fixture plus test-only compiled output. It must contain:

- a compiled core-only prompt;
- nested native `properties.models` annotations;
- one source-first model absent from local roots;
- one existing private input hashed through the local relay;
- fake Hugging Face mutable and pinned metadata responses;
- a fake worker that verifies the immutable manifest and reports sanitized progress;
- a fake Vast provider whose in-memory inventory can be proven empty.

Drive capture, free preflight, offer gating, explicit fake confirmation, ready state, one fake job/output retrieval, destroy review, destruction, and fresh empty inventory. Assert the manifest model URL is commit-pinned and the captured mutable annotation URL never reaches the worker.

- [ ] **Step 2: Prove free preflight performs no mutation or byte transfer**

Extend `test_no_mutation_surface` so capture and Hugging Face preflight may perform only fake read-only metadata calls. Assert zero provider create/destroy calls, zero worker model-byte calls, zero relay input upload, zero cache mutation, and no offer search until every row is resolved.

- [ ] **Step 3: Run the complete fake gate and repair only evidenced failures**

Run:

```sh
python3 -m unittest \
  tests.python.test_fake_session_integration \
  tests.python.test_fake_lifecycle_integration \
  tests.python.test_no_mutation_surface -v
```

Expected: all fake end-to-end and no-mutation tests pass. If a failure appears outside the new expected assertions, use systematic debugging and keep the correction in the task that owns the broken contract.

- [ ] **Step 4: Run focused cross-boundary suites**

Run:

```sh
python3 -m unittest \
  tests.python.test_huggingface \
  tests.python.test_model_metadata \
  tests.python.test_model_sources \
  tests.python.test_artifacts \
  tests.python.test_resolver \
  tests.python.test_manifest \
  tests.python.test_session_service \
  tests.python.test_worker_transfers \
  tests.python.test_worker_provision \
  tests.python.test_worker_server \
  tests.python.test_lifecycle \
  tests.python.test_routes -v
node --test \
  tests/js/canvas-adapter.test.mjs \
  tests/js/session-console.test.mjs
```

Expected: all focused tests pass offline.

- [ ] **Step 5: Commit the fake certification**

Run:

```sh
git diff --check
git add \
  tests/python/test_fake_session_integration.py \
  tests/python/test_no_mutation_surface.py
git diff --cached --check
git commit -m "test: certify workflow model metadata bridge"
```

If systematic debugging required an implementation correction, stage that correction with the owning tests and explain it in the commit body. Expected: no private workflow/input and no generated artifact are staged.

---

### Task 9: Perform only the free local and read-only network proofs

**Files:**
- Create after exact evidence exists: `docs/workflow-model-metadata-proof.md`
- Modify: `docs/project-state.md`

- [ ] **Step 1: Verify native metadata recognition without downloading**

Start the pinned local ComfyUI environment on port 8188 using the repository's documented development command. Load `tests/fixtures/native-model-metadata-workflow.json` through the normal frontend. Open the native missing-model panel and verify it lists `cloud-run-native-proof.safetensors` under `diffusion_models` and exposes `Download All`.

Do not click `Download All`. Confirm no model file appeared in the corresponding local model root before or after the check. Record only frontend/core versions, the generic test filename/directory, and the visible action; do not record a private path or browser profile.

- [ ] **Step 2: Capture the user's live Gold workflow without exporting it**

With the user's workflow already open, use the Cloud Run panel's normal capture action. Do not run the local workflow. Run free preflight only.

Expected active model rows are exactly:

```text
flux1-fill-dev.safetensors -> diffusion_models
clip_l.safetensors -> text_encoders
t5xxl_fp8_e4m3fn.safetensors -> text_encoders
ae.safetensors -> vae
4x_foolhardy_Remacri.pth -> upscale_models
```

The active `LoadImage` row must resolve to an existing private local input. If the workflow lacks native metadata, stop this proof with `mapping_required`; do not guess URLs and do not modify another repository. Report the exact missing producer contract.

- [ ] **Step 3: Run the real read-only Hugging Face preflight**

Allow only the five metadata API resolutions already implemented. Do not invoke offer search, session preview/confirm, cache population, native download, or any Vast route.

For every model require visible repository/path, 40-character immutable revision, positive exact size, 64-character SHA-256, canonical destination, and `resolved`. Confirm the aggregate transfer size equals the deduplicated manifest artifact sizes. Inspect the local model roots again and prove no model bytes were created.

- [ ] **Step 4: Write exact evidence, then scan it**

Create `docs/workflow-model-metadata-proof.md` only after Steps 1-3 produce real values. Include:

- date, pinned frontend/core versions, and commit under test;
- native missing-model action result without a local download;
- one table containing the five public model names, sanitized `owner/repository` plus file path, immutable revision, exact bytes, SHA-256, and destination;
- exact deduplicated transfer total;
- local private input reported only as `verified local input`, with size and digest omitted from public documentation;
- explicit statements: no model download, no R2, no template/release mutation, no offer search, no Vast mutation, no GPU, no paid charge;
- current stop: awaiting separate paid Gold authorization.

Do not include the captured workflow JSON, mutable candidate URLs, local paths, credentials, request headers, settings, database content, or private input identity.

Run:

```sh
rg -n \
  'hf_[A-Za-z0-9]{20,}|Bearer |jupyter|session_secret|/Users/|worker-release\.json' \
  docs/workflow-model-metadata-proof.md
git diff --check
```

Expected: `rg` exits 1 with no match and the diff has no whitespace errors.

- [ ] **Step 5: Update project state and commit only sanitized proof**

Update `docs/project-state.md` to state that the consumer bridge, offline suite, native missing-model visibility, and free read-only Gold preflight are complete; paid Gold remains not run and separately gated.

Run:

```sh
git add \
  docs/workflow-model-metadata-proof.md \
  docs/project-state.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: record free model metadata proof"
```

Expected: only sanitized documentation is committed; no runtime-generated or private file is staged.

---

### Task 10: Run final gates and stop at the paid boundary

**Files:**
- Verify: all implementation and test files from Tasks 1-9
- Do not create: Vast template, `worker-release.json`, model/input/output archives, or paid evidence

- [ ] **Step 1: Invoke verification-before-completion and inspect repository scope**

Run:

```sh
git status --short --branch
git diff --check
git log --oneline --decorate -12
git diff 8dda6e5..HEAD --stat
```

Expected: the worktree is clean, the approved design remains an ancestor, and every commit is limited to this bridge.

- [ ] **Step 2: Scan for unfinished prose and prohibited artifacts**

Run:

```sh
rg -n 'T''BD|T''ODO|implement l''ater|fill in d''etails' \
  cloud_run remote_worker web tests docs scripts
find . -type f \( \
  -name '*.safetensors' -o \
  -name '*.ckpt' -o \
  -name '*.pth' -o \
  -name 'worker-release.json' -o \
  -name '*.sqlite3' \
\) -print
```

Expected: no unfinished marker; no model, release-lock, or database artifact in the worktree.

- [ ] **Step 3: Run the complete gate twice consecutively**

Run:

```sh
scripts/check.sh
scripts/check.sh
```

Expected: both complete gates pass consecutively, including Python, Node, compilation, security, boundary, and public-artifact scans. Do not waive, skip, or filter a failure.

- [ ] **Step 4: Request code review and address only evidenced findings**

Read and follow `superpowers:requesting-code-review`. Review against the approved design and this plan, with special attention to URL/origin confinement, subgraph identity, collision handling, old worker/preflight compatibility, progress secrecy, rental gating, and residual-billing behavior.

For actionable findings, follow `superpowers:receiving-code-review`, add a red regression, make the minimal correction, commit it, and repeat both complete gates.

- [ ] **Step 5: Report readiness and stop**

Report:

- exact final commit;
- both consecutive gate results;
- free native-panel and Hugging Face preflight evidence;
- whether all five Gold models and the active private input resolve;
- no GPU/model download/R2/template/release/provider mutation occurred;
- the exact separate authorization still required: maximum instance count, maximum hourly price, and absolute maximum duration or total cost.

Stop. Do not search offers, create a session, confirm a rental, publish a worker release, or run Gold until the user supplies that new paid authorization.

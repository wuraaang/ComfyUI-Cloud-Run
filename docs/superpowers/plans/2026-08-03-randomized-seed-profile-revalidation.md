# Randomized Seed Profile Revalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a native randomized KSampler seed refresh from creating a new Desktop bootstrap profile and falsely expiring the reviewed paid quote.

**Architecture:** Centralize the existing strict randomized-seed pairing in the native capture contract. Derive a detached bootstrap workflow whose certified randomized seed widgets use a valid deterministic value, while leaving the executable prompt and original capture untouched; the runtime resolver alone passes that derived workflow to the content-addressed profile store.

**Tech Stack:** Python 3, frozen `CompiledCapture` contract, canonical JSON, `unittest`, content-addressed `DesktopProfileStore`.

## Global Constraints

- Normalize only an active official Comfy core `KSampler` paired with an executable `KSampler` and widget control exactly equal to `randomize`.
- Keep fixed, custom, inactive, malformed, ambiguous, and uncertified seed controls byte-significant or fail closed under the existing rules.
- Never rewrite the executable prompt or mutate the stored `CompiledCapture`.
- Preserve exact content addressing for every generated profile archive.
- Perform no push, release, Vast template mutation, Desktop restart, provider probe, rental, destruction, or paid test.

---

### Task 1: Add the seed-stable bootstrap capture contract

**Files:**
- Modify: `tests/python/test_capture.py`
- Modify: `cloud_run/capture.py`

**Interfaces:**
- Consumes: `CompiledCapture` and the existing strict KSampler/randomize pairing policy.
- Produces: `certified_bootstrap_workflow(capture: CompiledCapture) -> dict`, returning a detached workflow with only certified randomized seed widgets set to numeric `0`.

- [ ] **Step 1: Write the failing regression tests**

Add this import-surface helper beside `certified_execution_baseline` in
`tests/python/test_capture.py`:

```python
def certified_bootstrap_workflow(capture):
    if not hasattr(capture_contract, "certified_bootstrap_workflow"):
        raise AssertionError("certified_bootstrap_workflow is required")
    return capture_contract.certified_bootstrap_workflow(capture)
```

Add tests that change both native representations of the random seed and then
exercise the real profile store:

```python
def test_profile_bootstrap_normalizes_certified_random_seed_without_mutation(self):
    first_payload = native_capture()
    first_payload["workflow"]["nodes"][0]["widgets_values"] = [7, "randomize"]
    second_payload = copy.deepcopy(first_payload)
    second_payload["output"]["1"]["inputs"]["seed"] = 999
    second_payload["workflow"]["nodes"][0]["widgets_values"][0] = 999
    first = CompiledCapture.from_payload(first_payload)
    second = CompiledCapture.from_payload(second_payload)

    first_bootstrap = certified_bootstrap_workflow(first)
    second_bootstrap = certified_bootstrap_workflow(second)

    self.assertEqual(first_bootstrap, second_bootstrap)
    self.assertEqual(first_bootstrap["nodes"][0]["widgets_values"][0], 0)
    self.assertEqual(first.workflow["nodes"][0]["widgets_values"][0], 7)
    self.assertEqual(second.workflow["nodes"][0]["widgets_values"][0], 999)

    from cloud_run.desktop_profile import DesktopProfileStore

    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        (root / "user" / "default").mkdir(parents=True)
        (root / "input").mkdir()
        store = DesktopProfileStore(
            repository=JobRepository(root / "attempts.sqlite3"),
            private_root=root / "profiles",
        )
        first_profile = store.capture(
            user_root=root / "user",
            profile_name="default",
            input_root=root / "input",
            bootstrap_workflow=first_bootstrap,
        )
        second_profile = store.capture(
            user_root=root / "user",
            profile_name="default",
            input_root=root / "input",
            bootstrap_workflow=second_bootstrap,
        )

    self.assertEqual(first_profile.revision, 1)
    self.assertEqual(first_profile.archive_sha256, second_profile.archive_sha256)
    self.assertEqual(first_profile.bootstrap_digest, second_profile.bootstrap_digest)
```

Add a companion test that repeats the two-seed comparison with each of these
workflow changes and asserts that the returned workflows remain different:

```python
cases = (
    {"widgets_values": [7, "fixed"]},
    {"properties": {"cnr_id": "third-party"}},
    {"mode": 4},
    {"type": "KSamplerAdvanced"},
)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_capture.CompiledCaptureTests.test_profile_bootstrap_normalizes_certified_random_seed_without_mutation \
  tests.python.test_capture.CompiledCaptureTests.test_profile_bootstrap_keeps_uncertified_seed_values_significant -v
```

Expected: both tests fail only because
`certified_bootstrap_workflow is required`.

- [ ] **Step 3: Implement the shared strict pairing and bootstrap helper**

In `cloud_run/capture.py`, extract the existing pairing loop into a private
helper that returns sorted `(node_id, workflow_index)` pairs. Keep its current
ambiguity and invalid-seed errors unchanged. Use it in the existing execution
baseline and add:

```python
_CANONICAL_RANDOMIZED_BOOTSTRAP_SEED = 0


def certified_bootstrap_workflow(capture):
    """Return a detached seed-stable workflow for the Desktop bootstrap."""
    normalized_workflow = json.loads(canonical_json(capture.workflow))
    for _node_id, workflow_index in _certified_randomized_seed_pairs(capture):
        normalized_workflow["nodes"][workflow_index]["widgets_values"][0] = (
            _CANONICAL_RANDOMIZED_BOOTSTRAP_SEED
        )
    return normalized_workflow
```

Export `certified_bootstrap_workflow` through `__all__`. The existing
`certified_execution_baseline` must still replace only corresponding executable
prompt seeds with `_RANDOMIZED_SEED_SENTINEL` and return the same sorted node ID
tuple as before.

- [ ] **Step 4: Run the capture tests and verify GREEN**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest tests.python.test_capture -v
```

Expected: every capture test passes, including ambiguity, invalid seed, fixed
seed, custom-node, and immutability coverage.

---

### Task 2: Use the certified bootstrap in the local runtime resolver

**Files:**
- Modify: `cloud_run/routes.py`
- Modify: `tests/python/test_routes.py`

**Interfaces:**
- Consumes: `certified_bootstrap_workflow(capture)` from Task 1.
- Produces: a `DesktopProfileStore.capture` call whose `bootstrap_workflow` is seed-stable and whose remaining arguments are unchanged.

- [ ] **Step 1: Strengthen the existing runtime resolver test boundary**

Replace the ad-hoc `types.SimpleNamespace` capture in
`RuntimeResolverTests.test_injected_model_source_resolver_keeps_route_tests_offline`
with a real `CompiledCapture.from_payload(...)` containing the existing
`UNETLoader`, a matching workflow node, and frontend version `1.47.10`. This
ensures the route-to-capture contract is exercised rather than bypassed.

- [ ] **Step 2: Wire the helper into profile capture**

Change the capture import in `cloud_run/routes.py` to:

```python
from .capture import CaptureValidationError, certified_bootstrap_workflow
```

Change only the runtime profile argument:

```python
bootstrap_workflow=certified_bootstrap_workflow(observed_capture),
```

- [ ] **Step 3: Run the route and regression tests**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_capture \
  tests.python.test_routes.RuntimeResolverTests -v
```

Expected: all selected tests pass with no network or provider mutation.

---

### Task 3: Certify and commit the local fix

**Files:**
- Verify: the entire repository
- Commit: only `cloud_run/capture.py`, `cloud_run/routes.py`, `tests/python/test_capture.py`, and `tests/python/test_routes.py`

**Interfaces:**
- Consumes: the complete implementation from Tasks 1 and 2.
- Produces: one reviewed local implementation commit and two identical worker artifacts.

- [ ] **Step 1: Run the complete offline gate twice**

Run `scripts/check.sh` twice. Expected on each pass: every Python targeted
suite and all 62 JavaScript tests pass. Record the worker artifact SHA-256 from
each pass; the two new hashes must be identical to each other. They are expected
to differ from the old `7b999b...` artifact because source code changed.

- [ ] **Step 2: Check repository integrity**

Run:

```bash
git diff --check
git status --short --branch
```

Expected: no whitespace errors and only the four planned implementation/test
files modified before commit.

- [ ] **Step 3: Commit the implementation separately**

Run:

```bash
git add -- cloud_run/capture.py cloud_run/routes.py \
  tests/python/test_capture.py tests/python/test_routes.py
git diff --cached --check
git commit -m "fix: stabilize randomized seed profile revalidation"
```

- [ ] **Step 4: Verify the committed state without external mutation**

Run:

```bash
git status --short --branch
git log -2 --oneline
```

Expected: clean branch with the design commit followed by the implementation
commit. Stop and request a new exact GO before any deployment action.

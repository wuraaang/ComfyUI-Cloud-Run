# Public GitHub Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the audited ComfyUI-Cloud-Run source history to the correctly named public GitHub repository and record the exact behavior of GitHub's immutable commit archive without publishing a Vast template or renting a GPU.

**Architecture:** Treat publication as a fail-closed release boundary. Audit the exact reachable Git history, create an empty repository, replace the unsafe local `origin` before any push, publish one reviewed commit to `main` and the feature branch, then download and inspect the exact immutable GitHub archive. The archive evidence becomes the input to a separate bootstrap/template plan; it is never silently converted into a live release lock.

**Tech Stack:** Git, GitHub CLI 2.96+, GitHub REST metadata, POSIX shell, Python 3 standard library, unittest, ComfyUI's pinned Python/Pillow gate.

## Global Constraints

- Work only in `/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes`.
- Keep the implementation branch named `feat/vast-cloud-run-lifecycle`.
- The publication target is exactly `https://github.com/wuraaang/ComfyUI-Cloud-Run.git`.
- Never push ComfyUI-Cloud-Run content to `https://github.com/wuraaang/comfy-relay.git`.
- Publish an empty GitHub repository: do not ask GitHub to generate a README, license, `.gitignore`, workflow, or initial commit.
- Preserve the complete reachable history of the reviewed branch; do not rewrite, squash, rebase, or force-push it.
- Never commit or publish the private Gold source, its local path, its workflow, credentials, `settings.json`, `worker-release.json`, generated archives, model files, inputs, or outputs.
- Never read, modify, import, depend on, inspect for reconstruction, or reuse `/Users/wuraaang/comfyui-vast-cockpit`.
- Do not modify, stage, clean, or commit anything in `/Users/wuraaang/lora-dataset-studio`.
- A normal GitHub repository archive is not assumed to equal the deterministic 51,590-byte review artifact. Fetch, measure, hash, and inspect it first.
- Do not weaken redirect, archive, extraction, allowlist, digest, or shell restrictions during this plan.
- Do not create or edit a project-specific Vast template during this plan.
- Do not create a live private `worker-release.json` during this plan.
- Do not call any Vast provider mutation endpoint during this plan.
- No paid GPU may be rented without a later explicit GO stating maximum instance count, maximum hourly price, and absolute maximum duration or total cost.
- `scripts/check.sh` is the required repository gate before every success claim and immediately before every source push.

---

## File map

- `docs/superpowers/plans/2026-07-31-public-github-publication.md`: this executable publication plan and its checked-off evidence.
- `docs/superpowers/specs/2026-07-31-deterministic-destroy-review-token-design.md`: approved root-cause analysis and corrective token contract.
- `cloud_run/session_service.py`: default destruction-review token generation.
- `tests/python/test_session_service.py`: deterministic regression proof for the formerly intermittent gate.
- `tests/python/test_repository_contract.py`: repository-facing assertions for the public origin, legacy remote warning, and publication boundary.
- `README.md`: public installation, safety, publication, and paid-Gate truth.
- `docs/project-state.md`: current source-publication status and next human-gated action.
- `docs/remote-worker-bootstrap-review.md`: reviewed artifact versus fetched GitHub archive evidence and the explicit template stop.
- `.git/config`: worktree-shared local remote configuration; never committed.
- A temporary directory created with `mktemp -d`: exact downloaded GitHub archive and headers; deleted after evidence is recorded and never added to Git.

---

### Task 1: Commit the publication plan without changing runtime behavior

**Files:**
- Create: `docs/superpowers/plans/2026-07-31-public-github-publication.md`

**Interfaces:**
- Consumes: the approved workflow-derived session spec and corrective design ending at commit `132710b`.
- Produces: a reviewable, bounded checklist for the first public source publication.

- [ ] **Step 1: Verify plan placement and forbidden literals**

Run:

```sh
test -f docs/superpowers/plans/2026-07-31-public-github-publication.md
rg -n 'T''BD|T''ODO|implement l''ater|fill in d''etails' \
  docs/superpowers/plans/2026-07-31-public-github-publication.md
```

Expected: the file exists and `rg` exits 1 with no match.

- [ ] **Step 2: Verify the plan is the only worktree change**

Run:

```sh
git status --short
git diff --check
```

Expected: only the new plan is untracked and there is no whitespace error.

- [ ] **Step 3: Verify documentation contracts while preserving the known red gate**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
git diff --check
```

Expected: repository contract tests pass and there is no whitespace error. Do
not claim a green complete gate here: the preceding gate reproduced the
default destruction-review token failure documented in
`docs/superpowers/specs/2026-07-31-deterministic-destroy-review-token-design.md`.

- [ ] **Step 4: Commit only the plan**

Run:

```sh
git add docs/superpowers/plans/2026-07-31-public-github-publication.md
git diff --cached --check
git commit -m "docs: plan public GitHub publication"
```

Expected: one documentation commit; runtime source is unchanged.

---

### Task 2: Eliminate the intermittent destruction-review token failure

**Files:**
- Modify: `tests/python/test_session_service.py`
- Modify: `cloud_run/session_service.py`
- Verify: `docs/superpowers/specs/2026-07-31-deterministic-destroy-review-token-design.md`

**Interfaces:**
- Consumes: `SessionService.review_destroy(session_id: str) -> DestroyReview`, the existing `_IDENTIFIER` contract, and the approved corrective design.
- Produces: a default 256-bit hexadecimal review token that always satisfies the existing identifier grammar; injected factories retain strict validation.

- [ ] **Step 1: Invoke the TDD skill**

Read and follow `superpowers:test-driven-development` before editing production
code. The regression test must be observed failing for the documented reason
before `cloud_run/session_service.py` changes.

- [ ] **Step 2: Write the deterministic failing regression test**

Add this import to `tests/python/test_session_service.py`:

```python
from unittest import mock
```

Add this method to `ReusableSessionTests`:

```python
def test_default_destroy_review_token_is_always_identifier_safe(self):
    with mock.patch(
        "cloud_run.session_service.secrets.token_urlsafe",
        return_value="_" + "a" * 42,
    ):
        with mock.patch(
            "cloud_run.session_service.secrets.token_hex",
            return_value="b" * 64,
        ):
            try:
                review = asyncio.run(
                    self.service.review_destroy("session-1")
                )
            except DestroyConfirmationError:
                self.fail(
                    "Default review-token generation must always "
                    "satisfy its identifier contract."
                )

    self.assertEqual(review.token, "b" * 64)
```

This uses the real service, session repository, validation, digest persistence,
and review payload. Randomness is patched only to reproduce both old and
selected default encodings deterministically.

- [ ] **Step 3: Run the targeted test and verify the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.ReusableSessionTests.test_default_destroy_review_token_is_always_identifier_safe \
  -v
```

Expected: FAIL with `AssertionError: Default review-token generation must
always satisfy its identifier contract.` because the current default calls the
patched URL-safe generator and receives a token beginning with `_`.

- [ ] **Step 4: Make the minimal production correction**

In `SessionService.__init__`, replace only the default factory:

```python
self.review_token_factory = (
    review_token_factory
    or (lambda: secrets.token_hex(32))
)
```

Do not change `_IDENTIFIER`, `review_destroy()`, injected factory validation,
token storage, expiry, or consumption.

- [ ] **Step 5: Verify targeted and module-level green states**

Run:

```sh
python3 -m unittest \
  tests.python.test_session_service.ReusableSessionTests.test_default_destroy_review_token_is_always_identifier_safe \
  -v
python3 -m unittest tests.python.test_session_service -v
```

Expected: the regression test and every session-service test pass.

- [ ] **Step 6: Run two consecutive complete gates**

Run:

```sh
scripts/check.sh
scripts/check.sh
git diff --check
git status --short
```

Expected: both gates discover 302 Python tests, skip only the three generic
Pillow cases while passing them under ComfyUI's Python, pass 24 Node tests,
produce worker digest
`783a8f180365f6401af69679ba7681401aa123c50d050ead47c4f6faeb8df06f`,
and pass every boundary/public-artifact scan. Status contains only the intended
test and production files before commit.

- [ ] **Step 7: Commit the isolated bug fix**

Run:

```sh
git add tests/python/test_session_service.py cloud_run/session_service.py
git diff --cached --check
git commit -m "fix: make destroy review tokens format-safe"
```

Expected: one TDD commit and a clean worktree.

---

### Task 3: Audit the exact history that would become public

**Files:**
- Verify only: every blob and commit reachable from `HEAD`
- Do not inspect: unrelated local branches or forbidden external repositories

**Interfaces:**
- Consumes: the exact post-plan `HEAD`.
- Produces: a pass/fail publication decision for that immutable history.

- [ ] **Step 1: Reconfirm identity, clean state, and source-only scope**

Run:

```sh
git status --short
git branch --show-current
git rev-parse HEAD
git log --reverse --oneline HEAD
git remote -v
```

Expected: clean tree; branch `feat/vast-cloud-run-lifecycle`; one linear reviewed history rooted at `1f89d1b`; the only pre-publication remote still points to the forbidden ComfyRelay URL.

- [ ] **Step 2: List every reachable object and reject large files**

Run:

```sh
git rev-list --objects HEAD |
  git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' |
  awk '$1 == "blob" && $3 > 16777216 { print $3, $4 }'
```

Expected: no output.

- [ ] **Step 3: Scan every unique reachable blob without printing matched secret values**

Run this exact read-only audit:

```sh
python3 - <<'PY'
from __future__ import annotations
import re
import subprocess

rows = subprocess.run(
    ["git", "rev-list", "--objects", "HEAD"],
    check=True,
    stdout=subprocess.PIPE,
    text=True,
).stdout.splitlines()
blobs = {}
for row in rows:
    oid, *rest = row.split(" ", 1)
    kind = subprocess.run(
        ["git", "cat-file", "-t", oid],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    if kind == "blob" and rest:
        blobs.setdefault(oid, rest[0])

patterns = {
    "private key": re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:ghp|github_pat)_[A-Za-z0-9_]{20,}"),
    "Hugging Face token": re.compile(rb"hf_[A-Za-z0-9]{20,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "assigned provider secret": re.compile(
        rb"(?:VAST_API_KEY|R2_SECRET_ACCESS_KEY|CIVITAI_TOKEN|HF_TOKEN)"
        rb"\s*[:=]\s*['\"][^'\"]{12,}['\"]"
    ),
    "long bearer": re.compile(rb"Bearer\s+[A-Za-z0-9_=.\-]{32,}"),
}
findings = []
binary = []
for oid, path in blobs.items():
    content = subprocess.run(
        ["git", "cat-file", "-p", oid],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    if b"\0" in content:
        binary.append(path)
    for label, pattern in patterns.items():
        if pattern.search(content):
            findings.append((label, path))

print("reachable_unique_blobs=" + str(len(blobs)))
print("binary_blob_paths=" + str(len(set(binary))))
print("sensitive_hits=" + str(len(set(findings))))
for label, path in sorted(set(findings)):
    print("hit=" + label + " path=" + path)
raise SystemExit(1 if binary or findings else 0)
PY
```

Expected: exit 0, `binary_blob_paths=0`, and `sensitive_hits=0`. The audit prints paths and categories only if it fails; it never prints matched content.

- [ ] **Step 4: Verify forbidden artifact names and runtime-private files never entered history**

Run:

```sh
git rev-list --objects HEAD |
  rg -i '(^|/)(\.env|settings\.json|worker-release\.json|.*\.(jpg|jpeg|png|webp|gif|mp4|mov|safetensors|ckpt|pt|bin|tar|gz|zip))$'
```

Expected: `rg` exits 1 with no output.

- [ ] **Step 5: Run the complete gate once more**

Run:

```sh
scripts/check.sh
git status --short
```

Expected: the complete gate passes and the tree remains clean. Any finding stops publication; do not create the GitHub repository until it is resolved and re-audited.

---

### Task 4: Authenticate GitHub and create one empty public repository

**Files:**
- Modify locally: GitHub CLI credential store only if interactive reauthentication is required
- Create remotely: `wuraaang/ComfyUI-Cloud-Run`
- Do not create remotely: any generated initial commit or file

**Interfaces:**
- Consumes: a passing Task 3 audit and the user's explicit authorization to create the public repository.
- Produces: one empty, public, correctly named GitHub repository owned by `wuraaang`.

- [ ] **Step 1: Verify GitHub CLI identity**

Run:

```sh
gh --version
gh auth status -h github.com
```

Expected: GitHub CLI 2.96 or newer and an active authenticated `wuraaang` account. If the token is invalid, run:

```sh
gh auth login -h github.com --web --git-protocol https
gh auth status -h github.com
```

Expected: the browser/device flow is completed by the account owner and the second status succeeds. Never print a token and never use `gh auth status --show-token`.

- [ ] **Step 2: Prove the target does not already exist**

Run:

```sh
gh repo view wuraaang/ComfyUI-Cloud-Run \
  --json nameWithOwner,visibility,defaultBranchRef,url
```

Expected: not found. If it exists, stop before mutation and inspect its owner, visibility, default branch, and refs; never overwrite an existing repository.

- [ ] **Step 3: Create the empty public repository**

Run:

```sh
gh repo create wuraaang/ComfyUI-Cloud-Run \
  --public \
  --description "Run native ComfyUI workflows on temporary, explicitly confirmed Vast.ai GPU sessions."
```

Expected: `https://github.com/wuraaang/ComfyUI-Cloud-Run`; no README, license, `.gitignore`, Actions workflow, branch, or commit is generated by GitHub.

- [ ] **Step 4: Verify owner and visibility before configuring Git**

Run:

```sh
gh repo view wuraaang/ComfyUI-Cloud-Run \
  --json nameWithOwner,visibility,isEmpty,url
```

Expected: `nameWithOwner` is `wuraaang/ComfyUI-Cloud-Run`, visibility is `PUBLIC`, `isEmpty` is true, and the URL is exact.

---

### Task 5: Make an accidental ComfyRelay push impossible and document the public truth

**Files:**
- Modify locally: `.git/config`
- Modify: `tests/python/test_repository_contract.py`
- Modify: `README.md`
- Modify: `docs/project-state.md`
- Modify: `docs/remote-worker-bootstrap-review.md`

**Interfaces:**
- Consumes: the verified empty repository from Task 4.
- Produces: safe Git remotes and repository docs that accurately name the public source origin while retaining the legacy URL only as an explicit do-not-push warning.

- [ ] **Step 1: Rename the legacy remote and add the correct origin**

Run:

```sh
git remote rename origin comfy-relay-do-not-push
git remote add origin https://github.com/wuraaang/ComfyUI-Cloud-Run.git
git remote -v
```

Expected:

```text
comfy-relay-do-not-push https://github.com/wuraaang/comfy-relay.git (fetch)
comfy-relay-do-not-push https://github.com/wuraaang/comfy-relay.git (push)
origin https://github.com/wuraaang/ComfyUI-Cloud-Run.git (fetch)
origin https://github.com/wuraaang/ComfyUI-Cloud-Run.git (push)
```

Do not remove the legacy remote during this plan; its explicit name preserves provenance and makes accidental selection conspicuous.

- [ ] **Step 2: Write the failing documentation contract test**

Add this method to `RepositoryContractTests` in `tests/python/test_repository_contract.py`:

```python
def test_publication_docs_name_only_the_correct_source_origin(self):
    paths = (
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "docs" / "project-state.md",
        REPOSITORY_ROOT / "docs" / "remote-worker-bootstrap-review.md",
    )
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in paths
    )
    self.assertIn(
        "https://github.com/wuraaang/ComfyUI-Cloud-Run",
        combined,
    )
    self.assertIn("comfy-relay-do-not-push", combined)
    self.assertIn(
        "https://github.com/wuraaang/comfy-relay.git",
        combined,
    )
    self.assertNotIn(
        "configured Git remote currently points at the wrong",
        combined,
    )
```

- [ ] **Step 3: Run the targeted test and verify the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_repository_contract.RepositoryContractTests.test_publication_docs_name_only_the_correct_source_origin \
  -v
```

Expected: FAIL because the public origin and renamed legacy remote are not yet documented.

- [ ] **Step 4: Update public-facing documentation**

Make these exact factual changes:

- `README.md`: replace the pre-publication remote warning with the canonical public source URL; retain a warning that `comfy-relay-do-not-push` is unrelated; keep template publication and paid Gold explicitly pending.
- `docs/project-state.md`: mark source repository creation as complete but worker archive/template/Gold as pending; name both remotes and state that ComfyRelay received no push.
- `docs/remote-worker-bootstrap-review.md`: name the public source repository, retain the deterministic artifact as review-only, and state that no source push alone creates a worker release lock or authorizes Vast activity.

Do not claim that a branch, commit, fetched archive, template, or Gold run exists until the later task that verifies it.

- [ ] **Step 5: Run the targeted test and complete gate**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
scripts/check.sh
git diff --check
```

Expected: all repository contract tests and the complete gate pass.

- [ ] **Step 6: Commit the publication metadata**

Run:

```sh
git add tests/python/test_repository_contract.py README.md \
  docs/project-state.md docs/remote-worker-bootstrap-review.md
git diff --cached --check
git commit -m "docs: prepare public Cloud Run origin"
```

Expected: one focused commit; `.git/config` is not staged.

---

### Task 6: Re-audit and publish the exact reviewed commit

**Files:**
- Verify only: all files and history reachable from the new `HEAD`
- Create remotely: `main` and `feat/vast-cloud-run-lifecycle`

**Interfaces:**
- Consumes: the safe `origin`, passing gate, and publication-doc commit.
- Produces: two public refs pointing at the same exact reviewed commit, with `main` created first as the default branch.

- [ ] **Step 1: Repeat Task 3's complete history scan**

Run every command in Task 3 again against the new `HEAD`.

Expected: no binary/private/runtime artifact, no secret signature, no blob over 16 MiB, and a passing `scripts/check.sh`.

- [ ] **Step 2: Capture the exact candidate commit locally**

Run:

```sh
git rev-parse HEAD
git status --short
git remote get-url origin
git remote get-url comfy-relay-do-not-push
```

Expected: one 40-character commit, clean tree, correct public `origin`, and explicit legacy remote. Record the commit from stdout as `PUBLICATION_COMMIT` in the execution notes; do not use an abbreviated SHA in release evidence.

- [ ] **Step 3: Create public `main` with the exact candidate**

Run:

```sh
git push origin HEAD:refs/heads/main
```

Expected: a new `main` branch on `wuraaang/ComfyUI-Cloud-Run`. No force flag is allowed.

- [ ] **Step 4: Publish the named implementation branch**

Run:

```sh
git push --set-upstream origin \
  HEAD:refs/heads/feat/vast-cloud-run-lifecycle
```

Expected: the feature branch is created and tracks the correct `origin`; no request is sent to `comfy-relay-do-not-push`.

- [ ] **Step 5: Verify public refs, default branch, visibility, and commit**

Run:

```sh
gh repo view wuraaang/ComfyUI-Cloud-Run \
  --json nameWithOwner,visibility,defaultBranchRef,url
git ls-remote --heads origin main feat/vast-cloud-run-lifecycle
git ls-remote --heads comfy-relay-do-not-push feat/vast-cloud-run-lifecycle
```

Expected: repository is public, default branch is `main`, both new public branches resolve to `PUBLICATION_COMMIT`, and the legacy repository has no `feat/vast-cloud-run-lifecycle` ref. If the legacy query shows that ref, stop and report a potential prior leak; do not delete external data without separate authorization.

---

### Task 7: Audit the exact immutable GitHub commit archive

**Files:**
- Create temporarily outside Git: one archive, response-header files, and extracted listing
- Modify later: `docs/remote-worker-bootstrap-review.md`
- Never create in Git: a `.tar.gz`, fetched source tree, or live release lock

**Interfaces:**
- Consumes: the exact public `PUBLICATION_COMMIT`.
- Produces: measured URL/redirect/status/size/SHA-256/member/metadata evidence that determines the next bootstrap plan.

- [ ] **Step 1: Create a private temporary audit directory**

Run:

```sh
PUBLICATION_AUDIT_DIRECTORY="$(mktemp -d)"
chmod 700 "$PUBLICATION_AUDIT_DIRECTORY"
PUBLICATION_COMMIT="$(git rev-parse HEAD)"
PUBLICATION_ARCHIVE_URL="https://github.com/wuraaang/ComfyUI-Cloud-Run/archive/${PUBLICATION_COMMIT}.tar.gz"
```

Expected: a new `0700` temporary directory and a URL containing the exact 40-character commit.

- [ ] **Step 2: Record the no-follow response without accepting it**

Run:

```sh
curl --silent --show-error --head \
  --proto '=https' --tlsv1.2 \
  --output "$PUBLICATION_AUDIT_DIRECTORY/origin-headers.txt" \
  "$PUBLICATION_ARCHIVE_URL"
sed -n '1,40p' "$PUBLICATION_AUDIT_DIRECTORY/origin-headers.txt"
```

Expected: record the actual status and any `Location` host. Do not assume 200, do not copy cookies or authorization headers, and do not place the header file in the repository.

- [ ] **Step 3: Fetch the exact public bytes using curl's audited redirect handling**

Run:

```sh
curl --fail --silent --show-error --location \
  --proto '=https' --tlsv1.2 \
  --dump-header "$PUBLICATION_AUDIT_DIRECTORY/fetch-headers.txt" \
  --write-out '%{url_effective}\n' \
  --output "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz" \
  "$PUBLICATION_ARCHIVE_URL" \
  > "$PUBLICATION_AUDIT_DIRECTORY/final-url.txt"
wc -c "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz"
shasum -a 256 "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz"
sed -n '1p' "$PUBLICATION_AUDIT_DIRECTORY/final-url.txt"
```

Expected: one finite archive. Record exact byte size and SHA-256. `curl` is used only for publication evidence; these commands do not define what the stricter bootstrap accepts.

- [ ] **Step 4: Inspect every archive member and metadata without extracting**

Run:

```sh
python3 - "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz" <<'PY'
from __future__ import annotations
from pathlib import Path, PurePosixPath
import sys
import tarfile

path = Path(sys.argv[1])
with tarfile.open(path, "r:gz") as archive:
    members = archive.getmembers()
    print("members=" + str(len(members)))
    print("pax_members=" + str(sum(bool(item.pax_headers) for item in members)))
    print("symlink_members=" + str(sum(item.issym() or item.islnk() for item in members)))
    print("non_file_directory_members=" + str(sum(
        not item.isfile() and not item.isdir() for item in members
    )))
    roots = {
        PurePosixPath(item.name).parts[0]
        for item in members
        if PurePosixPath(item.name).parts
    }
    print("root_count=" + str(len(roots)))
    print("root=" + (next(iter(roots)) if len(roots) == 1 else "<multiple>"))
    print("uid_values=" + ",".join(str(value) for value in sorted({item.uid for item in members})))
    print("gid_values=" + ",".join(str(value) for value in sorted({item.gid for item in members})))
    print("mtime_values=" + str(len({item.mtime for item in members})))
    for item in members:
        print(item.name)
PY
```

Expected: a complete listing plus bounded metadata counts. Any symlink, hard link, device, multiple root, unsafe path, unexpected compression member, or archive parsing error is a release blocker.

- [ ] **Step 5: Compare the GitHub archive with the reviewed worker member set**

Run:

```sh
PUBLICATION_WORKER_ARTIFACT="$PUBLICATION_AUDIT_DIRECTORY/review-worker.tar.gz"
python3 scripts/build_worker_artifact.py "$PUBLICATION_WORKER_ARTIFACT"
wc -c "$PUBLICATION_WORKER_ARTIFACT"
shasum -a 256 "$PUBLICATION_WORKER_ARTIFACT"
python3 - "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz" "$PUBLICATION_WORKER_ARTIFACT" <<'PY'
from __future__ import annotations
from pathlib import PurePosixPath
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as public_archive:
    public_members = {
        "/".join(PurePosixPath(item.name).parts[1:])
        for item in public_archive.getmembers()
        if item.isfile() and len(PurePosixPath(item.name).parts) > 1
    }
with tarfile.open(sys.argv[2], "r:gz") as review_archive:
    reviewed_members = {
        item.name for item in review_archive.getmembers() if item.isfile()
    }
print("reviewed_members=" + str(len(reviewed_members)))
print("reviewed_missing_from_public=" + str(
    len(reviewed_members - public_members)
))
print("public_extra_files=" + str(len(public_members - reviewed_members)))
raise SystemExit(1 if reviewed_members - public_members else 0)
PY
```

Expected: all reviewed worker files exist byte-for-byte at the public commit, but the full GitHub source archive may have different bytes, metadata, a root prefix, redirects, and extra repository files. Never copy the deterministic review digest into a live lock unless the fetched archive itself is byte-identical.

- [ ] **Step 6: Exercise the current bootstrap against the fetched bytes without installing system-wide**

Run:

```sh
python3 - \
  "$PUBLICATION_AUDIT_DIRECTORY/github-commit.tar.gz" \
  "$PUBLICATION_AUDIT_DIRECTORY/final-url.txt" \
  "$PUBLICATION_AUDIT_DIRECTORY" \
  "$PUBLICATION_ARCHIVE_URL" \
  "$PUBLICATION_COMMIT" <<'PY'
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

from remote_worker.bootstrap import (
    Bootstrap,
    BootstrapError,
    DownloadStream,
)

archive_path = Path(sys.argv[1])
final_url = Path(sys.argv[2]).read_text(encoding="utf-8").strip()
audit_root = Path(sys.argv[3])
requested_url = sys.argv[4]
commit = sys.argv[5]
content = archive_path.read_bytes()


class ExactBytesTransport:
    def __init__(self, returned_url):
        self.returned_url = returned_url

    def stream(self, url):
        if url != requested_url:
            raise AssertionError("bootstrap requested an unexpected URL")
        midpoint = max(1, len(content) // 2)
        return DownloadStream(
            final_url=self.returned_url,
            chunks=(content[:midpoint], content[midpoint:]),
        )


class RecordingExec:
    def __init__(self):
        self.argv = None
        self.cwd = None

    def __call__(self, argv, *, cwd):
        self.argv = tuple(argv)
        self.cwd = Path(cwd)


def lock(destination):
    return {
        "schema_version": 1,
        "archive_url": requested_url,
        "worker_commit": commit,
        "worker_archive_sha256": hashlib.sha256(content).hexdigest(),
        "worker_archive_size_bytes": len(content),
        "protocol_version": "1",
        "comfyui_core_version": "0.29.0",
        "comfyui_frontend_version": "1.47.10",
        "python_version": "3.13.12",
        "destination": str(destination),
    }


def attempt(label, returned_url):
    destination = audit_root / ("installed-" + label)
    runner = RecordingExec()
    try:
        Bootstrap(
            transport=ExactBytesTransport(returned_url),
            exec_runner=runner,
            allowed_destination=destination,
        ).run(lock(destination))
    except BootstrapError:
        print(label + "=FAIL")
    else:
        print(label + "=PASS")
        print(label + "_exec_recorded=" + str(runner.argv is not None))


attempt("observed_final_url", final_url)
attempt("same_url_archive_shape", requested_url)
PY
```

Expected: two explicit PASS/FAIL lines. `observed_final_url` isolates the redirect boundary; `same_url_archive_shape` isolates the downloaded archive's metadata/layout from the redirect. A failure caused by the canonical GitHub redirect, root prefix, metadata, or extra files is expected evidence, not permission to weaken the bootstrap.

- [ ] **Step 7: Delete temporary publication bytes**

After recording only non-secret URL/status/size/digest/member-summary evidence in notes, run:

```sh
rm -rf "$PUBLICATION_AUDIT_DIRECTORY"
```

Expected: only the exact `mktemp -d` directory is deleted. Verify:

```sh
git status --short
```

Expected: clean tree.

---

### Task 8: Record publication evidence and establish the next hard gate

**Files:**
- Modify: `docs/remote-worker-bootstrap-review.md`
- Modify: `docs/project-state.md`
- Modify: `tests/python/test_repository_contract.py`

**Interfaces:**
- Consumes: exact remote refs and Task 7 archive measurements.
- Produces: a public source-publication record that cannot be mistaken for a worker release, template, or paid-test authorization.

- [ ] **Step 1: Write the failing publication-evidence contract**

Add assertions to `test_bootstrap_review_records_the_unpublished_worker_boundary` requiring these literal labels:

```python
for required_text in (
    "Public source repository:",
    "Published source commit:",
    "Fetched archive URL:",
    "Fetched archive size:",
    "Fetched archive SHA-256:",
    "Observed redirect boundary:",
    "Current bootstrap result:",
    "No project-specific Vast template has been created",
    "No live worker-release.json has been created",
    "No paid Gold run has occurred",
):
    with self.subTest(text=required_text):
        self.assertIn(required_text, review)
```

- [ ] **Step 2: Run the targeted test and verify the red state**

Run:

```sh
python3 -m unittest \
  tests.python.test_repository_contract.RepositoryContractTests.test_bootstrap_review_records_the_unpublished_worker_boundary \
  -v
```

Expected: FAIL because the measured evidence labels are absent.

- [ ] **Step 3: Record only exact verified facts**

In `docs/remote-worker-bootstrap-review.md`, add a dated source-publication section containing:

- canonical public repository URL;
- exact 40-character published commit;
- exact requested archive URL;
- observed redirect status and final host;
- exact fetched byte size;
- exact fetched SHA-256;
- member/root/metadata summary;
- deterministic worker artifact byte size and SHA-256;
- current bootstrap PASS/FAIL result and sanitized reason;
- explicit statements that there is no project-specific template, no live private lock in the repository, no Vast provider mutation, and no paid Gold run.

In `docs/project-state.md`, move public source publication to completed and make the next action conditional:

- if the current bootstrap rejects the real archive, invoke `superpowers:brainstorming` for a narrowly scoped bootstrap/publication design correction, obtain explicit design approval, then use `superpowers:writing-plans` and strict TDD;
- if the current bootstrap accepts the real archive, manually review every installed worker byte and request separate authorization before creating a project-specific Vast template.

Do not record temporary local paths, credentials, private Gold details, or a live lock.

- [ ] **Step 4: Run targeted and complete verification**

Run:

```sh
python3 -m unittest tests.python.test_repository_contract -v
scripts/check.sh
git diff --check
git status --short
```

Expected: all checks pass and only the three intended documentation/test files are changed.

- [ ] **Step 5: Commit and push publication evidence**

Run:

```sh
git add tests/python/test_repository_contract.py \
  docs/project-state.md docs/remote-worker-bootstrap-review.md
git diff --cached --check
git commit -m "docs: record public worker archive evidence"
scripts/check.sh
git push origin HEAD:refs/heads/main
git push origin HEAD:refs/heads/feat/vast-cloud-run-lifecycle
```

Expected: both public refs advance to the evidence commit; no force flag and no ComfyRelay push.

---

### Task 9: Final verification and stop before template or spend

**Files:**
- Verify only: repository and public GitHub metadata
- Do not create: template, release lock, release asset, tag, Registry entry, rental, or Gold output

**Interfaces:**
- Consumes: public source and archive evidence.
- Produces: a clean handoff for the next explicitly approved phase.

- [ ] **Step 1: Invoke completion verification**

Read and follow `superpowers:verification-before-completion` before making any completion claim.

- [ ] **Step 2: Verify local and remote identity**

Run:

```sh
git status --short
git branch --show-current
git rev-parse HEAD
git remote -v
gh repo view wuraaang/ComfyUI-Cloud-Run \
  --json nameWithOwner,visibility,defaultBranchRef,url
git ls-remote --heads origin main feat/vast-cloud-run-lifecycle
```

Expected: clean tree; correct feature branch; public repository; default `main`; both public refs at the final evidence commit; legacy remote present only under `comfy-relay-do-not-push`.

- [ ] **Step 3: Verify the ComfyUI development link**

Run:

```sh
readlink /Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/custom_nodes/ComfyUI-Cloud-Run
```

Expected:

```text
/Users/wuraaang/.worktrees/comfyui-cloud-run/mission-hermes
```

- [ ] **Step 4: Run the final gate with fresh evidence**

Run:

```sh
scripts/check.sh
git diff --check
git status --short
```

Expected: complete pass and clean tree.

- [ ] **Step 5: Report the exact stopping point**

Report:

1. public repository URL, visibility, default branch, and final commit;
2. confirmation that no push went to ComfyRelay;
3. fresh test counts and deterministic worker digest;
4. fetched GitHub archive URL, size, SHA-256, redirect boundary, and bootstrap result;
5. whether the next step is a bootstrap design correction or a separately authorized template review;
6. confirmation that no private Gold asset/workflow, secret, live lock, Vast template, provider mutation, GPU rental, or Comfy Registry publication occurred.

The following later sequence is deliberately outside this plan:

1. approve and implement any evidence-driven bootstrap correction with brainstorming, a design addendum, writing-plans, TDD, and a new immutable commit;
2. separately authorize and create the project-specific Vast template;
3. create the matching owner-private `0600` `worker-release.json` outside Git;
4. load/capture the user's actual ComfyUI workflow and complete free dependency preflight;
5. obtain a paid GO stating maximum instances, maximum hourly price, and absolute maximum duration or total cost;
6. rent at most the authorized instance count, execute the native workflow, retrieve and validate the output, destroy immediately after the first coherent result, and prove fresh Vast inventory is empty;
7. publish a GitHub release or Comfy Registry entry only after successful teardown evidence and a separate publication decision.

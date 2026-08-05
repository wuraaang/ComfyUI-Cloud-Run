# Legacy Session Paid-Claim Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a current paid-create claim coexist safely with terminal sessions whose historical quote JSON predates the current schema, while recovering any confirmation stranded before the provider boundary.

**Architecture:** Evaluate unrelated sessions through a strict raw-row billing-risk predicate instead of rebuilding obsolete quote objects. Keep full validation for the target session, roll back synchronous pre-provider claim failures, and use a short quiet period to recover an already stranded tokenless `confirming` session during local refresh.

**Tech Stack:** Python 3, SQLite transactions, frozen session state machine, `unittest`.

## Global Constraints

- Preserve every historical session and its original quote JSON unchanged.
- Treat malformed or uncertain billing-risk fields as blocking.
- Never pass the provider boundary unless the target current-format session is fully valid and its boundary token is durably persisted.
- Recover only `confirming` sessions with no provider token, session secret, instance, or residual inventory after the quiet period.
- Perform no provider probe, rental, destruction, pod mutation, Desktop restart, release, template mutation, push, or PR.

---

### Task 1: Make the global paid-claim guard legacy-safe

**Files:**
- Modify: `tests/python/test_repository.py`
- Modify: `cloud_run/repository.py`

**Interfaces:**
- Consumes: a raw `sqlite3.Row` selected with `_SESSION_COLUMNS`.
- Produces: `_stored_session_blocks_paid_claim(row) -> bool`, which returns `False` only when primitive durable fields prove absence.

- [ ] **Step 1: Add the failing production-history regression**

In `SessionRepositoryTests`, add a helper that persists a normal terminal
session and then replaces only its `quote_json` with a deliberately obsolete
record:

```python
def _insert_legacy_terminal_quote(self, sessions, *, session_id="legacy-session"):
    legacy = make_session(
        key="legacy-key-" + session_id,
        session_id=session_id,
        now=50.0,
    ).transition(
        SessionState.OFFER_SELECTED,
        now=51.0,
    ).transition(
        SessionState.DESTROYED,
        now=52.0,
    )
    sessions.create_or_get(legacy)
    with closing(sqlite3.connect(self.database_path)) as connection:
        connection.execute(
            "UPDATE sessions SET quote_json = ? WHERE session_id = ?",
            ('{"legacy_offer_id":"42"}', session_id),
        )
        connection.commit()
```

Add `test_legacy_terminal_quote_does_not_block_paid_claim`. It creates the
current confirming target, inserts the legacy terminal row, calls
`claim_create_intent`, and asserts `creating`, version `2`, and both supplied
dummy boundary values. It also re-reads the legacy `quote_json` directly and
asserts the bytes are unchanged.

- [ ] **Step 2: Add failing fail-closed companion cases**

For isolated databases, mutate one legacy row at a time with these raw changes:

```python
cases = (
    ("provider_token = ?", ("c" * 64,)),
    ("instance_id = ?", ("instance-legacy",)),
    ("residual_inventory_json = ?", ('["instance-legacy"]',)),
    ("residual_inventory_json = ?", ("not-json",)),
    ("state = ?", (SessionState.CREATING.value,)),
    (
        "failure_code = ?, remediation_verified_at = NULL",
        ("configuration_rejected",),
    ),
)
```

Each case must assert `PaidRentalConflict`, target state
`offer_selected`, no target boundary values, and no rewrite of the legacy
quote.

- [ ] **Step 3: Run the repository tests and verify RED**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_repository.SessionRepositoryTests.test_legacy_terminal_quote_does_not_block_paid_claim \
  tests.python.test_repository.SessionRepositoryTests.test_legacy_uncertain_billing_fields_still_block_paid_claim -v
```

Expected: the safe legacy case errors with `Invalid paid offer quote`, while
the blocking cases fail because they raise that parsing error instead of the
required `PaidRentalConflict`.

- [ ] **Step 4: Implement the raw fail-closed predicate**

In `cloud_run/repository.py`, import `VAST_CREATE_FAILURE_CODES` from
`cloud_run.constants` and add a private predicate with this decision order:

```python
_ABSENT_OR_PRE_PROVIDER_STATES = {
    SessionState.PREFLIGHT,
    SessionState.OFFER_SELECTED,
    SessionState.CONFIRMING,
    SessionState.FAILED,
    SessionState.DESTROYED,
}


def _stored_session_blocks_paid_claim(row):
    try:
        state = SessionState(row["state"])
        residual = json.loads(row["residual_inventory_json"] or "[]")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(residual, list):
        return True
    if (
        row["instance_id"] is not None
        or row["provider_token"] is not None
        or row["session_secret_hex"] is not None
        or residual
    ):
        return True
    failure_code = row["failure_code"]
    if failure_code is not None and failure_code not in VAST_CREATE_FAILURE_CODES:
        return True
    if failure_code in {"configuration_rejected", "api_key_rejected"}:
        verified = row["remediation_verified_at"]
        revision = row["remediation_revision"]
        if (
            isinstance(verified, bool)
            or not isinstance(verified, (int, float))
            or not math.isfinite(verified)
            or verified < float(row["created_at"])
            or not isinstance(revision, str)
            or not revision
        ):
            return True
    return state not in _ABSENT_OR_PRE_PROVIDER_STATES
```

Use this predicate for `other_rows` in `claim_create_intent()` instead of
calling `_row_to_session` on unrelated rows. Do not alter target-session
reconstruction or quote validation.

- [ ] **Step 5: Run all repository tests and verify GREEN**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest tests.python.test_repository -v
```

Expected: every repository test passes, including concurrent exact-one-claim
and typed remediation coverage.

---

### Task 2: Recover synchronous and already-stranded pre-provider confirmation

**Files:**
- Modify: `tests/python/test_service.py`
- Modify: `cloud_run/service.py`

**Interfaces:**
- Consumes: target session state and durable provider-boundary fields.
- Produces: rollback to `offer_selected` on claim exceptions and refresh-time recovery after `_CONFIRMING_RECOVERY_GRACE_SECONDS = 5.0`.

- [ ] **Step 1: Add a failing claim-exception rollback test**

Import `mock` from `unittest`. Create a quoted session with the real service,
patch only `self.session_repository.claim_create_intent` to raise
`ValueError("synthetic pre-provider failure")`, and call `confirm_session`.
Assert the exception propagates, the stored target is back in
`offer_selected`, both boundary values and instance remain `None`, and
`provider.create_calls == []`.

- [ ] **Step 2: Add failing quiet-period recovery tests**

Persist a quoted session transitioned to `confirming` at time `102.0` with no
boundary values. With a service clock of `108.0`, call `refresh_session` and
assert `offer_selected`, `billing_may_continue=false`, and no provider calls.

Repeat with a distinct session updated at `105.0` and a clock of `108.0`.
Assert it remains `confirming`, proving a fresh claim is not touched inside the
five-second quiet period.

- [ ] **Step 3: Run the service tests and verify RED**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_service.CloudRunServiceTests.test_pre_provider_claim_failure_returns_offer_for_review \
  tests.python.test_service.CloudRunServiceTests.test_refresh_recovers_only_quiet_tokenless_confirmation -v
```

Expected: the first test leaves the session `confirming`; the second test also
returns the old session as `confirming`.

- [ ] **Step 4: Implement rollback and refresh recovery**

In `cloud_run/service.py`, add:

```python
_CONFIRMING_RECOVERY_GRACE_SECONDS = 5.0
_PRE_PROVIDER_CONFIRMATION_ERROR = (
    "Paid confirmation stopped before Vast create; review the offer again."
)
```

At the start of `refresh_session`, after loading the session and a finite clock,
transition an old tokenless/instance-less/residual-free `confirming` session
back with `transition_if_state`. On a concurrent update, reload the session.

Around `claim_create_intent`, retain the existing
`ConcurrentSessionUpdate` behavior. For every other exception, attempt the same
state-conditional rollback with `_PRE_PROVIDER_CONFIRMATION_ERROR`; ignore only
rollback races and re-raise the original exception.

- [ ] **Step 5: Run targeted service and concurrency tests**

Run:

```bash
scripts/run_with_comfyui_python.sh -m unittest \
  tests.python.test_service.CloudRunServiceTests.test_pre_provider_claim_failure_returns_offer_for_review \
  tests.python.test_service.CloudRunServiceTests.test_refresh_recovers_only_quiet_tokenless_confirmation \
  tests.python.test_service.CloudRunServiceTests.test_blocked_confirmation_returns_to_pre_mutation_state \
  tests.python.test_service.CloudRunServiceTests.test_concurrent_paid_confirmations_issue_exactly_one_create \
  tests.python.test_service.CloudRunServiceTests.test_concurrent_sessions_issue_only_one_provider_create -v
```

Expected: all selected tests pass and provider create counts remain exactly as
asserted.

---

### Task 3: Certify and commit without external mutation

**Files:**
- Verify: complete repository
- Commit: `cloud_run/repository.py`, `cloud_run/service.py`, `tests/python/test_repository.py`, and `tests/python/test_service.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: one local implementation commit with two identical offline worker artifacts.

- [ ] **Step 1: Run the complete offline gate twice**

Run `scripts/check.sh` twice. Every discovered and targeted Python suite and all
JavaScript tests must pass. Record the worker artifact SHA-256 from both runs
and require exact equality.

- [ ] **Step 2: Check scope and whitespace**

Run:

```bash
git diff --check
git status --short --branch
git diff --name-only
```

Expected: only the four planned implementation/test files are modified.

- [ ] **Step 3: Commit the implementation separately**

Run:

```bash
git add -- cloud_run/repository.py cloud_run/service.py \
  tests/python/test_repository.py tests/python/test_service.py
git diff --cached --check
git commit -m "fix: preserve paid claims across legacy sessions"
```

- [ ] **Step 4: Verify clean local state and stop at the external boundary**

Run `git status --short --branch` and `git log -3 --oneline`. Preserve the
worktree and ask for explicit push and one Desktop restart before changing any
external state.

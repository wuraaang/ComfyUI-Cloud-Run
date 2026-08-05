# Agent Panel Five Paid GPU Fast Path

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans`, `superpowers:systematic-debugging`,
> `superpowers:verification-before-completion`, and
> `certifying-comfyui-cloud-workflows`.

**Goal:** Obtain three real Smoke successes and two real Gold successes through
the ComfyUI Agent Panel, with a verified image and verified Vast destruction
for every success.

**Architecture:** Use one immutable controller and one Vast instance at a time.
Local tests are limited to preventing duplicate paid creates, automatic
replacement, and false output claims. All remaining compatibility and runtime
claims come from the five real GPU sessions.

**Budget:** At most five paid Vast instances and USD 5.00 total. A failed or
ambiguous rental consumes one of the five. Smoke offers are capped at USD
0.20/hour for 30 minutes; Gold offers at USD 0.75/hour for 90 minutes. Destroy
immediately after evidence or failure. Never create a replacement automatically.

## Global Constraints

- The user's 2026-08-02 authorization permits the ComfyUI Agent Panel to
  search, rent, run, and destroy without further clicks for this campaign.
- One instance may exist at a time. A sixth create is forbidden.
- Never recreate or modify Vast template `522713`.
- Keep worker `76f2fff05f05b2fc7372b8cdf507d84dc794abe8`, archive
  `844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5`,
  template hash `9d6822f9429822ee9e7339a804a549da`, and protocol `1` unchanged.
- If inventory is unknown, assume billing may continue and perform no new
  create.
- Do not call a run successful without a job-owned verified image, terminal
  session, and a fresh complete Vast inventory of zero.

---

### Task 1: Reach the first real Smoke quickly

- [ ] Preserve the current dirty worktree and inspect every pending diff.
- [ ] Keep only the already-open safety changes: atomic single-create claim,
  exact two-key Vast environment, no automatic replacement, and output
  ownership evidence.
- [ ] Correct the three obsolete fake fixtures that mutate the reviewed canvas;
  do not weaken the production baseline check.
- [ ] Run the focused backend, output, and frontend suites. Require zero
  failures and `git diff --check` clean.
- [ ] Commit the controller once. Do not change worker/template/lock assets.
- [ ] Restart the existing ComfyUI Desktop backend once, with queue empty, and
  verify that the committed controller is loaded.
- [ ] Read Vast inventory completely and require zero before the first create.

Target: first paid Smoke create within 30 to 60 minutes. This is a target, not
a guarantee; Vast availability and model transfer are external.

### Task 2: Agent Panel control loop

- [ ] Connect to the official Panel WebSocket bridge at `127.0.0.1:9180` using
  a unique headless controller ID.
- [ ] List connected tabs. Attach only to the exact workflow tab; if none is
  connected, open or create one explicit workflow-scoped Panel session rather
  than guessing between homonymous tabs.
- [ ] Send one campaign instruction containing the immutable identifiers,
  budget, current run number, and stop conditions.
- [ ] Mirror and record Panel `turn`, `action`, `download_progress`, tool-call,
  and final `say` events. Independently poll sanitized Cloud Run session state,
  queue state, logs, and strict Vast inventory.
- [ ] Never let a Panel retry, replace, or create a second instance on its own.

### Task 3: Run S1, S2, and S3

For each Smoke session, sequentially:

- [ ] Load the committed zero-model core Smoke workflow.
- [ ] Run free preflight and require core `EmptyImage` and `SaveImage`, no
  models, no custom nodes, and the frozen Smoke digest.
- [ ] Select the cheapest compliant single-GPU offer at or below USD 0.20/hour.
- [ ] Confirm exactly one create and wait for authenticated `ready`.
- [ ] Run the current canvas once.
- [ ] Verify the returned PNG is 512x512 RGB with the certified pixel value and
  belongs to the exact session, job, prompt digest, and SaveImage node.
- [ ] Destroy the GPU immediately.
- [ ] Require terminal `destroyed`, `billing_may_continue=false`, and a fresh
  complete Vast inventory of zero before proceeding.

### Task 4: Run G1 and G2

For each Gold session, sequentially:

- [ ] Load the exact workflow
  `/Users/wuraaang/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/Wallpaper Outpaint FLUX Fill 4K.json`.
- [ ] Run free preflight and require all five models plus the real input to
  resolve immutably. Use the preflight GPU, VRAM, disk, transfer, and output
  allowance; do not guess hardware.
- [ ] Select one compliant offer at or below USD 0.75/hour and confirm exactly
  one create.
- [ ] Wait for authenticated `ready`, then execute the current canvas once.
- [ ] Retrieve and verify the real 3840x2160 output, its job ownership, digest,
  non-truncation, enlargement, and image-quality checks.
- [ ] Destroy immediately and require terminal absence plus strict Vast
  inventory zero.

### Task 5: Error-driven repair loop

At the first real error:

- [ ] Stop new creates and destroy any identified instance.
- [ ] Preserve the exact sanitized controller logs, Panel tool events, session
  row, job row, HTTP classification, and full inventory verdict.
- [ ] State one evidence-backed root cause.
- [ ] Add one regression for that observed cause, make the smallest production
  correction, rerun only the affected checks, commit, and restart once.
- [ ] Continue only if the five-instance and USD 5.00 caps still allow it.

Do not invent hypothetical fixes. If the fifth paid instance fails, report the
four-or-fewer proven successes and stop; a sixth instance needs a new explicit
authorization.

### Task 6: Completion proof

- [ ] Record all five session IDs, instance IDs, offer/GPU summaries, start/end
  times, hourly prices, verified image hashes/dimensions, and destruction
  evidence without secrets or private provider identities.
- [ ] Require S1, S2, S3, G1, and G2 all successful on the same immutable
  controller/worker/template baseline.
- [ ] Perform one final full Vast inventory read and require zero.
- [ ] Run `scripts/check.sh` against the exact final commit.
- [ ] Only then report the extension Gold-validated and complete the goal.


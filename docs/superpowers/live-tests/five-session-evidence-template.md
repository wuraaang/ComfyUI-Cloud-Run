# Five-session Vast Gold campaign evidence

This record is intentionally sanitized. Keep raw provider host and machine
identifiers, private paths, filenames, inputs, credentials, and provider bodies
out of this file. A checked result requires one manually confirmed rental, one
manually confirmed run, one manually reviewed destroy, a terminal controller
record, and a complete post-destroy Vast inventory of zero for that row.

## Frozen campaign contract

- Smoke fixture raw-file SHA-256:
  `c9124f764fe4335d1c150e78f108284dd9b560f268f54caf92b132ab045eaaba`
- Smoke canonical-workflow SHA-256:
  `e72b293f4d08e007623ea647b58c1733913e04261fb04eb9a96f7566895e2f30`
- Smoke normalized seed policy: no randomized seed nodes.
- Worker release:
  `worker-v1-76f2fff05f05b2fc7372b8cdf507d84dc794abe8`
- Worker archive SHA-256:
  `844a829be884f2cb1cba3b7d8818f2592d3d0c42f3f25e291e3834863f527ad5`
- Private Vast template ID: `522713`; template intent hash:
  `9d6822f9429822ee9e7339a804a549da`; protocol: `1`.
- Final controller HEAD, accepted monetary bounds, Gold workflow hashes,
  private-input hash, both executable-baseline digests, and actual run prompt
  digests are frozen privately immediately before S1. Do not place private
  input identity in this public template.

## Counted sessions

| Run | Kind | Raw workflow SHA-256 | Canonical workflow SHA-256 | Actual run prompt digest | Executable baseline / seed policy | Frozen controller / worker release | Reviewed quote bounds | Distinct provider placement | Ready, job, and owned-output evidence | Human Destroy acknowledgement | Terminal session | Complete Vast inventory zero |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | smoke | `c9124f…aaba` | `e72b293f…f30` | `<fresh run digest>` | `<smoke baseline>` / none | `<controller HEAD>` / `worker-v1-76f2fff…` | `<GPU, VRAM, dph, duration, max charge, network>` | first placement | `<ready/job/output evidence IDs and sanitized hashes>` | `<timestamp + yes>` | `<destroyed timestamp>` | `<timestamp + yes>` |
| S2 | smoke | `c9124f…aaba` | `e72b293f…f30` | `<fresh run digest>` | `<same smoke baseline>` / none | `<same controller>` / `worker-v1-76f2fff…` | `<GPU, VRAM, dph, duration, max charge, network>` | `<true, or explicit relaxation reference>` | `<ready/job/output evidence IDs and sanitized hashes>` | `<timestamp + yes>` | `<destroyed timestamp>` | `<timestamp + yes>` |
| S3 | smoke | `c9124f…aaba` | `e72b293f…f30` | `<fresh run digest>` | `<same smoke baseline>` / none | `<same controller>` / `worker-v1-76f2fff…` | `<GPU, VRAM, dph, duration, max charge, network>` | `<true, or explicit relaxation reference>` | `<ready/job/output evidence IDs and sanitized hashes>` | `<timestamp + yes>` | `<destroyed timestamp>` | `<timestamp + yes>` |
| G1 | Gold | `<private record>` | `<private record>` | `<fresh G1 digest>` | `<Gold baseline>` / randomized node 3 only | `<same controller>` / `worker-v1-76f2fff…` | `<GPU, VRAM, dph, duration, max charge, network>` | `<true, or explicit relaxation reference>` | `<ready/job/output evidence IDs, node 9, sanitized hashes, visual yes>` | `<timestamp + yes>` | `<destroyed timestamp>` | `<timestamp + yes>` |
| G2 | Gold | `<same private record>` | `<same private record>` | `<fresh G2 digest>` | `<same Gold baseline>` / randomized node 3 only | `<same controller>` / `worker-v1-76f2fff…` | `<GPU, VRAM, dph, duration, max charge, network>` | `<true, or explicit relaxation reference>` | `<ready/job/output evidence IDs, node 9, sanitized hashes, visual yes>` | `<timestamp + yes>` | `<destroyed timestamp>` | `<timestamp + yes>` |

The prompt digest is session-specific evidence. It is not interchangeable with
the raw workflow-file hash, canonical workflow hash, or normalized executable
baseline. G1 and G2 may have different prompt digests solely because node 3 is
the frozen randomized seed node.

## Lifetime paid-attempt ledger

Record every paid click, including failures that do not count toward the five
successes. Stop after any failure until a new exact attempt-and-budget
authorization is recorded.

| Attempt | Target | Outcome | Charged or conservative upper bound | Cumulative bound | Remaining authorized budget | Failure evidence / new GO reference |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | S1 | `<pending>` | `<amount>` | `<amount>` | `<amount>` | `<none or sanitized reference>` |

## Free certification evidence

- Local ComfyUI response: version `0.29.0`, required frontend `1.47.10`.
- `EmptyImage`: `python_module=nodes`, category `image`, output `IMAGE`.
- `SaveImage`: `python_module=nodes`, category `image`, output node, required
  `images` and `filename_prefix` inputs.
- This check is read-only. It does not constitute a paid attempt or a counted
  session.

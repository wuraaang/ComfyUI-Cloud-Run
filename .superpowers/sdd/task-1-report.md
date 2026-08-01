# Task 1 report: hardware compatibility contract

## Red evidence

Command:

```text
python3 -m unittest tests.python.test_vast tests.python.test_offers
```

Result before production edits: `Ran 34 tests`; `FAILED (failures=15, errors=1)`.

The intended failures showed that both search payload assertions lacked
`gpu_arch`, `cpu_arch`, `cuda_max_good`, and `compute_cap`, and that local
normalization accepted offers with absent, wrong-type, non-finite, and
below-floor hardware fields. The exact-offer assertion raised `KeyError:
'gpu_arch'` for the same missing payload filter.

## Changes

- Added fixed provider filters to `build_search_payload()`:
  `gpu_arch == nvidia`, `cpu_arch == amd64`, `cuda_max_good >= 12.9`, and
  `compute_cap >= 750`; the existing `num_gpus == 1` and all prior filters
  remain unchanged.
- Added fail-closed normalization checks for exact architecture values and
  finite numeric CUDA/compute-capability floors.
- Updated valid offer fixtures, asserted both bounded search payloads and the
  exact-ID revalidation payload, and added a table-driven invalid-hardware
  rejection matrix.

## Green evidence

Command:

```text
python3 -m unittest tests.python.test_vast tests.python.test_offers
```

Result: `Ran 34 tests in 0.022s`; `OK`.

`git diff --check` also completed with no output.

## Commit

`feat: enforce Vast hardware compatibility contract`

## Concerns

None. No live provider or network endpoint was invoked.

# Worker count, architecture, and Rust assessment

This expands the earlier three-worker comparison. All policies keep every baseline-selected test. Answer labels are used only after execution to verify outcomes.

## Challenge scoring model: all 30 patches

The existing scheduler already searches worker counts per patch. The objective is maximum worker load plus two seconds per nonempty worker. These are modeled costs, not measured pytest execution times.

| Fixed requested workers per patch | Longest-first modeled total (s) |
| ---: | ---: |
| 1 | 144.806 |
| 2 | 136.418 |
| 3 | 155.910 |
| 4 | 171.910 |
| 5 | 185.910 |
| 6 | 199.910 |
| 7 | 211.910 |
| 8 | 223.910 |

| Packing strategy, choosing cheapest worker count per patch | Modeled total (s) |
| --- | ---: |
| lpt | 121.100 |
| heap_lpt | 121.100 |
| fifo | 122.112 |
| shortest_first | 131.844 |
| module_affinity | 125.453 |
| class_affinity | 125.453 |

The existing 121.100-second plan matches the lower-bound certificate for every sample selection. More packing work cannot improve these modeled sample costs without changing the selected work or scoring assumptions.

## Real execution: one-, two-, and four-worker caps

One seeded, sequential run per configuration on a single four-CPU-quota cloud host. Each policy has an independent pinned sqlglot checkout. JUnit verifies the exact assigned test set, no skips/errors, and the expected failing test IDs. The main sweep is exploratory; confirmation measurements are separate below.

- **lpt:** longest-first fixed worker bins.
- **affinity:** group tests by module to preserve setup/cache reuse, then balance groups.
- **balanced:** longest-first duration-targeted microbatches in a shared queue, with enough initial batches to fill available slots. This improves on the earlier relay prototype's small-plan batching.

Worker-seconds below sums top-level command lifetimes. It is an occupancy proxy, not CPU seconds or a cloud bill; nested test subprocesses also consume resources. Requested caps can exceed actual peak workers when grouping leaves fewer jobs.

| Patch | Policy | Worker cap | Actual peak | Wall (s) | Worker-seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| 4d63 | affinity | 1 | 1 | 14.492 | 14.491 |
| 4d63 | affinity | 2 | 2 | 11.457 | 15.486 |
| 4d63 | affinity | 4 | 4 | 8.139 | 19.217 |
| 4d63 | balanced | 1 | 1 | 43.220 | 43.218 |
| 4d63 | balanced | 2 | 2 | 33.152 | 63.366 |
| 4d63 | balanced | 4 | 4 | 32.210 | 107.696 |
| 4d63 | lpt | 1 | 1 | 34.394 | 34.393 |
| 4d63 | lpt | 2 | 2 | 27.678 | 39.169 |
| 4d63 | lpt | 4 | 4 | 27.259 | 70.583 |
| ary6 | affinity | 1 | 1 | 7.198 | 7.198 |
| ary6 | affinity | 2 | 2 | 5.473 | 9.281 |
| ary6 | affinity | 4 | 4 | 5.287 | 10.007 |
| ary6 | balanced | 1 | 1 | 10.781 | 10.781 |
| ary6 | balanced | 2 | 2 | 9.300 | 12.973 |
| ary6 | balanced | 4 | 4 | 8.431 | 23.028 |
| ary6 | lpt | 1 | 1 | 10.235 | 10.235 |
| ary6 | lpt | 2 | 2 | 8.969 | 10.772 |
| ary6 | lpt | 4 | 4 | 11.093 | 24.855 |

### Observed latency/resource frontier

A point is listed if no other observed configuration for that patch uses both less wall time and fewer worker-seconds. This is an observed frontier, not a proof of production optimality.

- `4d63`: affinity cap 4: 8.139s wall, 19.217 worker-s; affinity cap 2: 11.457s wall, 15.486 worker-s; affinity cap 1: 14.492s wall, 14.491 worker-s.
- `ary6`: affinity cap 4: 5.287s wall, 10.007 worker-s; affinity cap 2: 5.473s wall, 9.281 worker-s; affinity cap 1: 7.198s wall, 7.198 worker-s.

### Fresh confirmation

These additional runs recheck the promising module-aware approach against longest-first ordering at the same worker count. A second patch includes known failures to verify retained detection.

| Patch | Policy | Worker cap | Wall (s) | Worker-seconds |
| --- | --- | ---: | ---: | ---: |
| 4d63 | affinity | 1 | 14.046 | 14.046 |
| 4d63 | affinity | 2 | 10.889 | 14.601 |
| 4d63 | affinity | 4 | 8.148 | 19.107 |
| 4d63 | lpt | 1 | 34.019 | 34.018 |
| 4d63 | lpt | 2 | 26.928 | 38.783 |
| 4d63 | lpt | 4 | 26.773 | 68.923 |
| ary6 | affinity | 1 | 6.673 | 6.672 |
| ary6 | affinity | 2 | 5.285 | 9.060 |
| ary6 | affinity | 4 | 5.415 | 10.347 |
| ary6 | lpt | 1 | 9.077 | 9.076 |
| ary6 | lpt | 2 | 8.620 | 10.361 |
| ary6 | lpt | 4 | 10.997 | 22.076 |

### Module-aware worker tradeoff across both passes

Medians combine the main and confirmation observations and are exploratory, not confidence intervals. For illustration, the final column adds a two-second charge per actual peak worker to measured wall time. It is a policy preference, not a cloud bill or the official modeled score.

| Patch | Worker cap | Median wall (s) | Median worker-seconds | Wall + 2s/peak worker |
| --- | ---: | ---: | ---: | ---: |
| 4d63 | 1 | 14.269 | 14.268 | 16.269 |
| 4d63 | 2 | 11.173 | 15.043 | 15.173 |
| 4d63 | 4 | 8.144 | 19.162 | 16.144 |
| ary6 | 1 | 6.935 | 6.935 | 8.935 |
| ary6 | 2 | 5.379 | 9.171 | 9.379 |
| ary6 | 4 | 5.351 | 10.177 | 13.351 |

## Existing persistent-worker implementations

One bounded run per configuration using installed pytest-xdist 3.8.0, the same selected test IDs, and explicit plugin loading. These figures are not repeated confidence estimates. Failed validation remains reported and is excluded from valid speed comparisons.

| Patch | Distribution | Workers | Wall (s) | Matches expected outcomes |
| --- | --- | ---: | ---: | --- |
| 4d63 | loadfile | 2 | 11.782 | yes |
| 4d63 | loadfile | 4 | 11.129 | yes |
| 4d63 | worksteal | 2 | 15.661 | NO — unexpected failure |
| 4d63 | worksteal | 4 | 15.667 | NO — unexpected failure |

Both work-stealing configurations produced an unexpected failure in `tests/test_generator.py::TestGenerator::test_generator_memory`. That test measures process-wide tracemalloc growth and asserts less than 1024 bytes. This makes background/ordering effects a plausible hypothesis; the exact allocation mechanism has not been isolated.

An unpatched pinned-source control, using the same 360 IDs and four work-stealing workers, returned 1 and recorded these failing IDs: `['tests/test_generator.py::TestGenerator::test_generator_memory']`. It executed the complete selected set without skips or collection errors. This confirms execution-mode sensitivity independent of the patch. Work stealing is not validated for this suite; isolating memory-sensitive tests and rerunning would be needed before adoption.

## Would Rust help?

Rust has not been benchmarked or implemented here. The preceding profiling does not justify a full rewrite.

The measured selector CPU is 0.504162s per 30-patch batch; modeled test cost is 121.100s. Even eliminating all selector CPU with zero replacement cost can reduce their combined total by only 0.4146%. This is a mathematical ceiling with unchanged test selection and model, not a predicted Rust speedup.

Eliminating the scheduler's assumed upper bound of 1ms saves at most 0.000822% of that combined total. A faster language does not itself remove pytest collection, fixture setup, Python test execution, or Python import-validation subprocesses.

Rewriting actual application/test hot paths is a different proposal and could change execution cost; its benefit and semantic compatibility are unmeasured. First profile those hot paths and compare a small native prototype against an equivalent Python baseline rather than extrapolating language benchmarks.

## Decision

Keep the current per-patch worker search for the challenge model. For real execution, module-aware grouping and worker-cap selection merit further validation. Choose a point on the measured latency/resource frontier according to actual capacity and cost requirements; neither maximum concurrency nor minimum worker count is universally best. Setup/cache reuse is a plausible explanation for ordering gains, but this experiment does not isolate the cause.

All results preserve the baseline's selected tests, not a proof of complete defect coverage beyond that baseline. The timing sample is small and single-host; representative repeated production workloads remain necessary before promotion.

Model data: `/workspace/blast-radius-results/model-sweep-final/model-sweep.json`. Real data: `/workspace/blast-radius-results/worker-sweep/worker-results.json`.

Verification: **34 unittest checks passed** under Python 3.13, including the expanded policy and model tests. `git diff --check` passed. The 18-condition custom sweep and 12-condition confirmation completed with exact expected outcomes. The unexpected xdist failures and unpatched control are retained above and in `experiments/results/`. Original source and earlier archives are unchanged.

# Cloud experiment results

Baseline: `3fd38f73ffca368c4a7b4c7472ea0b127f0b4bc3`. All candidates retain the baseline's selected tests. No sample answers are used to schedule tests.

## Selector CPU

Python 3.13.5; 15 measured fresh processes per variant after one warmup. Seeded shuffled order, no concurrent timed runs. CPU includes child import-check processes. The three copies also passed a separate simultaneous smoke run.

| Build | Median CPU (s) | Median wall (s) | Runtime map bytes |
| --- | ---: | ---: | ---: |
| baseline | 0.5042 | 0.5043 | 15,331,758 |
| optimized | 0.4969 | 0.4975 | 15,331,758 |
| prepared | 0.4929 | 0.4930 | 5,475,439 |

All outputs pass the official bundled sample scorer: 121.100 modeled seconds and zero misses, with identical selected sets. The small median CPU differences have overlapping observed timings; they do not establish a robust speedup. Indexed maps remain opt-in.

## Actual patched test execution

2 repetitions per patch/policy. Policies run in separate pinned sqlglot clones, sequentially in seeded shuffled order, with at most three top-level workers. Nested test workers also consume CPU. This table reports real wall time, excluding selector scans.

| Patch | Selected tests | Failing test IDs | Static median (s) | Priority median (s) | Relay median (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2jrq | 29 | 8 | 24.839 | 24.595 | 32.340 |
| 4d63 | 360 | 0 | 27.201 | 26.679 | 34.642 |
| ary6 | 10 | 10 | 9.362 | 8.990 | 9.192 |
| Sum of per-patch medians | | | 61.403 | 60.265 | 76.173 |

Every measured run was checked against JUnit: exactly the assigned test IDs ran, none skipped or errored, and failing IDs matched the bundled answers. Failures never canceled remaining validation.

### Test failure detection timestamps

These are retrospective timestamps from pytest events, read after the run. They measure when pytest reported a failure, not delivered notification latency. Raw executor results also retain command-completion observation time.

| Patch | Static median (s) | Priority median (s) | Relay median (s) |
| --- | ---: | ---: | ---: |
| 2jrq | 4.834 | 3.379 | 3.535 |
| 4d63 | none | none | none |
| ary6 | 1.718 | 1.554 | 1.690 |

## Interpretation and limitations

- Keep the existing static scheduler for the challenge: it already reaches the lower bound for these selected sample sets. Static output cannot use live results.
- Keep indexed maps optional. Their smaller representation is confirmed, but full-process startup gains are small compared with variance.
- Neutral risk priors were used because no representative historical failure dataset was available. These runs test scheduling mechanics, not the predictive accuracy of a trained model.
- Relay uses 32-test microbatches and a bounded work/risk heuristic. Small plans can become a single batch. Repeated collection and fixture setup can dominate; results do not establish an optimal relay configuration.
- Only three representative patches were executed, with two repetitions in the main experiment. Full-suite and production generalization remain unverified.
- Docker daemon access was unavailable. Selector builds reproduce its source extraction, map preparation, bytecode compilation and Python 3.13 entrypoint without claiming the image or the scorer's exact CPU/memory sandbox was tested.

## Artifacts and reruns

- Selector measurements: `/workspace/blast-radius-results/indexed-selectors/selector-results.json`.
- Execution measurements: `/workspace/blast-radius-results/streamed-execution/execution-results.json`.
- Per-batch logs, JUnit XML, event JSONL and complete executor results sit beside the execution measurements.
- Earlier `selectors/` measurements retain the rejected marshal-map experiment. An earlier `execution/` pilot was intentionally interrupted to add per-test timestamps; its partial results are excluded from the final comparison.
- Runnable isolated selector copies and sqlglot policy clones are retained in the results directories.
- See [experiments/README.md](../../experiments/README.md) for commands and contracts.

## Final verification

22 unittest checks passed under Python 3.13. Fresh final CLI smoke runs also passed for all three selector builds and all three execution policies on an additional patch. `git diff --check` passed. The original clone at `/workspace/blast-radius` was not modified by this implementation. Portable measurement snapshots are included in `experiments/results/`. These are single-cloud-host subprocess experiments; multi-host network scaling is unverified.

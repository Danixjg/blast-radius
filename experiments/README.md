# Coverage-preserving execution experiments

The production entrypoint remains `selector.py`; experimental execution is separate.
Every policy scans every input patch through the same hunter/catcher and retains its
selected tests. No probability authorizes omission. This preserves baseline selection,
not a proof that the baseline finds all possible bugs.

## Selector builds

Run with Python 3.13 (the repository's container runtime):

```sh
python3.13 experiments/benchmark_selectors.py \
  --baseline /workspace/blast-radius-baseline \
  --candidate /workspace/blast-radius-candidate \
  --output /workspace/blast-radius-results/indexed-selectors --repeats 15
```

This builds baseline, optimized-pickle, and indexed-map runtime directories,
precompiles sources as Docker does, and times fresh processes in seeded shuffled
order. Each variant gets one warmup. Measured runs never overlap. CPU includes
child import-check processes. Each output must preserve the exact selected test
set and pass the official bundled sample scorer with 121.100 modeled seconds
and zero misses. A separate concurrent smoke run verifies that the three copies
can operate together; its timings are excluded from the isolated comparison.
Use a fresh output directory to preserve prior experiments.

Indexed coverage masks are an opt-in build-time representation. The loader restores
the original schema and still supports original pickle/zlib maps. No coverage or
import checks are removed. Prepare a map separately with:

```sh
python3.13 experiments/prepare_map.py map.pkl /tmp/blast-radius-indexed.pkl
BR_MAP=/tmp/blast-radius-indexed.pkl BR_SRC=/path/to/packaged/src \
  BR_DATA=/path/to/sample python3.13 -S selector.py
make build MAP_FORMAT=indexed
```

The default container map remains pickle. `make build` needs access to Docker.

## Actual execution

`executor.py` accepts a JSON plan with `tasks` and a partitioning `workers` list:

```json
{
  "tasks": [
    {"id": "tests/test_parser.py::TestParser::test_union", "seconds": 0.031,
     "history": {"runs": 40, "failures": 2}}
  ],
  "workers": [["tests/test_parser.py::TestParser::test_union"]],
  "startup_seconds": 0.3,
  "minimum_workers": 1
}
```

Task risk is an explicit `risk` value, otherwise `(failures+1)/(runs+2)` from
supplied historical observations, otherwise a neutral 0.5. These are experimental
scores; the code does not claim calibration or assume independent test failures.
Use only history available before the evaluated patch, with representative full
validation to expose omitted failures. Never feed the current patch's answer
labels into its scheduling plan.

Modes:

- `static`: execute the supplied worker bins, preserving their order.
- `priority`: retain the same bins and sort each by risk divided by duration.
- `relay`: dispatch priority-sorted microbatches through a shared queue, with a
  bounded square-root concurrency heuristic using remaining work and mean risk.

All modes finish every assignment after failures. Relay has no posterior-learning
claim: it adapts to pending work and completions, while risks remain fixed for a
run. Its batching and concurrency formula are hypotheses to benchmark. Startup,
collection, fixture reuse, and nested subprocesses can outweigh parallelism.

To execute a plan with pytest and streamed failure timestamps:

```sh
/workspace/blast-radius-venv/bin/python experiments/executor.py \
  --plan plan.json --cwd /path/to/sqlglot --output /tmp/relay-run \
  --mode relay --max-workers 3 --batch-size 32 --timeout 180 \
  --command /workspace/blast-radius-venv/bin/python \
  /workspace/blast-radius-candidate/experiments/pytest_runner.py \
  --events '{events}' --report '{report}' '{tests}'
```

Per-batch logs, JUnit XML, event JSONL, and `result.json` retain the evidence.
`all_completed` covers command assignments. Runner-level completion must be
verified from JUnit; command exit 0 alone cannot establish that no tests skipped.
Timeouts kill the command process group; infrastructure errors are distinct from
test failures. Failure timestamps use the shared machine's monotonic clock.
Events are read after completion: this measures when pytest reported a failure,
not how quickly a remote notification was delivered.

For the end-to-end comparison, provision the pinned sqlglot checkout at
`eb11aec4be8cb2122afe713a4816662c3f0fb2fa` plus its test dependencies, then run:

```sh
/workspace/blast-radius-venv/bin/python experiments/benchmark_execution.py \
  --runtime /workspace/blast-radius-runtime \
  --selector-results /workspace/blast-radius-results/indexed-selectors \
  --output /workspace/blast-radius-results/streamed-execution \
  --python /workspace/blast-radius-venv/bin/python --repeats 2 --stream-events
```

This creates three independent runtime clones, applies each representative patch,
and runs policies sequentially in seeded shuffled order. JUnit must show every
selected test exactly once, no skips/errors, and exactly the expected failing
test IDs. Sample answers are consulted only after execution for verification.
Three patches cover a long executor test, broad conservative dialect selection,
and a smaller affected set. This is exploratory, not a full-suite or production
workload benchmark. Real execution timings exclude selector time, which is
reported independently. No historical model is trained on these sample labels.

The challenge accepts static worker lists, not a live executor. Microbatch
execution and early feedback therefore cannot improve its modeled objective
unless selected sets or worker assignments change. Keep these metrics separate.

## Verification

```sh
python3.13 -m unittest discover -s tests -v
git diff --check
```

The tests cover original/prepared map equality, sample-output parity, varied
scheduler workloads, risk ordering, completion barriers, streamed event validation,
infrastructure failures, descendant timeout cleanup, and JUnit evidence parsing.

## Expanded worker and architecture sweep

The original three-policy run held the worker cap at three. The expanded sweep
compares the worker/time/resource tradeoff explicitly:

```sh
python3.13 experiments/model_sweep.py \
  --selector-results /workspace/blast-radius-results/indexed-selectors \
  --output /workspace/blast-radius-results/model-sweep-new
/workspace/blast-radius-venv/bin/python experiments/worker_sweep.py \
  --runtime /workspace/blast-radius-runtime \
  --selector-results /workspace/blast-radius-results/indexed-selectors \
  --output /workspace/blast-radius-results/worker-sweep-new \
  --python /workspace/blast-radius-venv/bin/python --patches 4d63 ary6
```

The model compares six packing algorithms at all eight worker counts on every
sample patch. Real execution compares `lpt`, `affinity`, and `balanced` at worker
caps one, two, and four. `affinity` groups modules to retain setup/cache reuse;
`balanced` dispatches longest-first duration-targeted microbatches and guarantees
enough initial batches to occupy requested slots. It corrects the earlier relay's
small-plan underutilization. Probabilities do not omit any work.

The internal execution plans may contain more queued batches than workers; they
are not valid challenge submissions without converting them to at most eight
static worker lists. Wall time, top-level worker occupancy, nested child CPU,
and retrospective first-failure timestamps remain distinct measurements.

Fresh confirmation uses the same command with a new output directory and
`--policies lpt affinity`. Read the newer
[worker-count assessment](../docs/research/worker-architecture-rust.md) alongside
the earlier fixed-cap report. It also gives an Amdahl-style ceiling for a Rust
rewrite; no Rust performance measurement is claimed.

`benchmark_xdist.py` adds an existing persistent-worker implementation:

```sh
/workspace/blast-radius-venv/bin/python experiments/benchmark_xdist.py \
  --runtime /workspace/blast-radius-runtime \
  --selector-results /workspace/blast-radius-results/indexed-selectors \
  --output /workspace/blast-radius-results/xdist-new \
  --python /workspace/blast-radius-venv/bin/python --patches 4d63 \
  --workers 2 4 --distributions loadfile worksteal --repeats 1
```

This benchmark stops on outcome mismatch and preserves the failed result.
The observed work-stealing memory-test failure also reproduced on an unpatched
control via `check_xdist_control.py`; do not treat that mode as validated merely
because all selected IDs ran. No source/test assertion was weakened to make it pass.

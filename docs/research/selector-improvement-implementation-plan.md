# Selector improvement implementation plan

Status: proposed trials. Repository baseline: `3fd38f73ffca368c4a7b4c7472ea0b127f0b4bc3`. Test subject: sqlglot at `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.

Build a complete Rust selector alongside Python and evaluate it independently from changes to execution architecture. In parallel, validate module-aware grouping and worker allocation on a larger workload. Preserve required validation throughout: every patch is scanned, predictions do not remove required tests, and a passing verdict waits for completion.

The decision has three separate targets: a faster selector, a better challenge score, and more efficient real test execution. A candidate can improve one without improving the others. A small contribution to the challenge score does not rule out a useful improvement in selector throughput or memory use.

## Starting evidence

These observations determine which trials to prioritize; they are not production guarantees.

| Observation | Implication for the next trials |
| --- | --- |
| The Python selector used about 0.504 seconds of CPU per 30-patch batch. The scheduler itself used less than 1 ms. | Measure a full Rust port. A scheduler-only port is a small diagnostic, not a substitute for it. |
| The existing schedules cost 121.100 modeled seconds and meet a lower-bound certificate for every selected sample set. | More packing cannot improve those particular modeled plans. Test new workloads and actual execution separately. |
| At four workers, module-aware execution of the 360-test patch took 8.14 and 8.15 seconds; longest-first execution took 27.26 and 26.77 seconds. | Reproduce the ordering benefit on unseen patches and isolate the contribution of collection, fixtures, caches, and contention. |
| Module-aware median times at one, two, and four workers were 14.27, 11.17, and 8.14 seconds on the broad patch; 6.94, 5.38, and 5.35 seconds on the smaller patch. | Choose workers by workload and resource cost. Increasing concurrency is not a universal improvement. |
| Work stealing produced an extra memory-test failure, including on an unpatched control. | Outcome compatibility is an adoption gate. Retain the failure and investigate isolation before using that mode. |

The runtime measurements cover two representative patches with repeated observations, not a representative production history. Setup and cache reuse are plausible explanations for the grouping benefit; the cause has not been isolated. Details and raw-data references are in [the worker assessment](worker-architecture-rust.md) and [the earlier cloud comparison](cloud-experiment-results.md).

## Correctness contract

Use the Python selector and independent test outcomes for different purposes. Python is the compatibility reference for a language port. Independent full-suite outcomes establish whether either implementation omitted failures; Python parity alone cannot establish complete defect detection.

Every trial must satisfy the following:

1. Scan every patch. Never use the current patch's answer labels to select tests, assign workers, or estimate its risk.
2. Preserve the baseline-selected test set during language, storage, ordering, and execution trials. Keep unknown-change handling and conservative fallbacks. An unsupported Rust construct initially delegates to Python or widens validation; record the fallback and include its cost.
3. For the completed language-only port, require identical selected sets, worker assignments, and within-worker order on the compatibility corpus. Keep stable tie-breaking. Investigate every difference before comparing performance as an equivalent replacement.
4. Verify real execution from runner reports: exactly the assigned IDs ran, no unexpected duplicates, skips, or collection errors, and failing IDs agree with the reference outcomes. A successfully launched command is not proof of completed tests.
5. Continue mandatory validation after a failure. Report first-failure latency separately from time to finish all checks.
6. Preserve the challenge protocol: one result per patch, at most eight nonempty workers, no duplicate or unknown test numbers, integer-millisecond costs, and the existing miss definition. A scored miss occurs if **any** failing test is omitted, even if another failing test was found.

Missing/corrupt maps, catalog mismatches, malformed patches, parser errors, timeouts, and child-process failures must exercise explicit fallback behavior. Do not obtain speed by dropping import checks, changing assertions, excluding slow tests, or hiding an incompatible runner outcome.

## Implementation layout and comparison controls

Keep the existing Python clone as an immutable reference and create an isolated checkout for each candidate. The following paths are proposed additions, not existing Rust implementations:

| Surface | Responsibility |
| --- | --- |
| `native/selector-core/` | Patch application, source locations, dependency resolution, coverage masks, fallback decisions, and scheduling. |
| `native/selector-cli/` | Catalog/map loading, source lookup, Python-validator process management, JSONL output, and diagnostics. |
| `experiments/export_map.py` | Offline conversion of the trusted existing map into a language-neutral schema. |
| `experiments/parity/` | Canonical inputs, semantic traces, compatibility comparison, and regression fixtures. |
| Existing `experiments/` tools | Extend measurement, worker sweeps, live events, resource collection, and result summaries. |

Record the source commit or dirty-tree manifest for every binary and script, compiler/interpreter versions, dependency lockfiles, build flags, source/map/catalog hashes, machine limits, and exact commands. Preserve the existing map and earlier result directories. Use fresh result directories and write `in_progress`, `complete`, or `failed` status with the expected condition count.

Use a factorial comparison where changes could otherwise be confused:

- Compare Python and Rust with the **same** portable map representation.
- Compare fresh-process and persistent-process operation for **both** languages.
- Keep selected tests fixed while changing worker grouping or execution policy.
- Keep AST information equivalent when measuring Rust analysis independently of parser choice.

Retain the deployed Python format as an additional deployment control. A gain caused by a smaller map or warmer process must not be attributed solely to Rust.

## Trial sequence

### Trial 0 Establish a repeatable baseline and outcome corpus

Instrument time spent in startup, map decoding, patch parsing/application, AST parsing, dependency resolution, mask expansion, live import validation, scheduling, and output. Record child import-check CPU separately while including it in total CPU.

Keep the bundled 30-patch sample as a fixed regression set. Add explicitly labelled compatibility cases for signatures, decorators, lambdas, nested classes, import-time derivations, metaclasses, new/deleted/renamed files, fixture changes, syntax errors, import exceptions/hangs, unmapped tests, and malformed inputs. Include Unicode identifiers and strings, byte-versus-character column offsets, CRLF input, and multi-hunk changes.

Collect additional real patches with independent full-suite outcomes. Group related patches and mutants so variants of one change do not cross the development/holdout boundary. Synthetic cases can stress scaling or edge behavior; they cannot estimate natural bug prevalence. Do not treat unexecuted tests as passes.

**Deliverable:** a frozen input manifest, stage profile, independently sourced outcomes, and a parity runner. Do not proceed to promotion decisions if the workload cannot be reproduced.

### Trial 1 Port the scheduler as a small Rust integration check

Implement the current longest-first scheduler, stable tie-breaking, worker-count search, and cost arithmetic in Rust. Compare an in-process binding and a standalone CLI only if both deployment forms are useful. Include invocation and serialization costs in end-to-end measurements; report kernel timing separately.

Convert catalog durations into canonical integer milliseconds using the scorer's behavior and verify every catalog entry across languages. Do not assume Python and Rust floating-point rounding defaults are interchangeable.

Validate empty and zero-duration sets, ties, long indivisible tests, overflow handling, and seeded random workloads. Use independently calculated lower bounds and the official scorer as checks.

**Question:** can the toolchain and protocol reproduce behavior without creating overhead elsewhere? This trial does not answer whether the whole selector benefits from Rust. Continue the full-selector trials even if the tiny scheduling kernel shows no useful saving.

### Trial 2 Implement a portable map and native lookup engine

Export the map once at build time. Specify a versioned format with explicit integer widths/endianness, strings, stable test IDs, mask encoding, schema version, and source/catalog fingerprints. Avoid dependence on Python object serialization at Rust runtime. Verify the full decoded structure against the existing map, including fixture opens, import-time dependencies, class relationships, and subprocess coverage.

Compare pooled masks with dense and sparse representations using observed density. Measure decoded memory and load/lookup cost, not just file size. Reject incompatible versions, invalid indexes, truncation, and source mismatches through the defined fallback path.

Build a Python reader for the same format. Benchmark Python-original, Python-portable, and Rust-portable controls; conversion and compilation are build costs and are recorded separately from runtime.

**Deliverable:** schema, converter, two readers, exact round-trip tests, and load/lookup benchmarks. Choose the representation using the combined load, lookup, and memory results.

### Trial 3 Port the complete selection engine with an AST bridge

Implement patch parsing/application, change localization, name and class dependency traversal, coverage operations, conservative widening, and output in Rust. Initially obtain Python AST information through a bounded bridge with an explicit intermediate representation. Include node types, spans, byte offsets, scope paths, assignments, decorators, and import-time distinctions needed by the hunter.

Run the Python analysis engine against the same intermediate representation as a control. Account for bridge startup, parsing, data conversion, IPC, and memory in both implementations. This isolates native analysis benefits while keeping parser semantics controlled.

Emit diagnostic traces showing why tests were selected and which fallback occurred. A smaller selected set is a correctness discrepancy, not an automatic performance win. A larger set caused by unsupported syntax is recorded as an incomplete port and its full execution cost remains in the aggregate.

**Deliverable:** the complete Rust analysis pipeline with a clearly identified Python parsing dependency, full-corpus differential results, and an end-to-end profile. Do not call this a Python-free implementation.

### Trial 4 Complete the native Rust selector

Evaluate Rust-hosted Python parser candidates against the pinned source grammar and the compatibility corpus before selecting a dependency. Pin the chosen version. Replace the AST bridge while preserving the intermediate representation and semantic traces. Test source positions, decorators, signatures/defaults, nested definitions, annotations, matching constructs supported by the pinned Python version, and invalid syntax.

The finished Rust selector owns the full control path: input, map/source loading, patch analysis, dependency selection, catcher decisions, scheduling, and output. Preserve the current fresh Python subprocess for live import validation and the pristine-source control when needed. Importing modified Python code requires a Python execution environment; a Rust selector can manage that external validator without putting the hunter back in Python.

Measure the selector both with and without validator-required cases, then report the complete workload including them. Preserve the timeout and process-cleanup contract. Do not claim a speedup from bypassing the validator.

**Deliverable:** release-built Rust CLI, pinned dependencies, complete semantic parity, and a measured comparison against Python. Report any remaining Python dependency explicitly. A variant that also removes Python import validation is a separate semantic research trial, not part of the equivalent port.

### Trial 5 Compare process lifetime and concurrent patch throughput

Compare fresh Python, persistent Python, fresh Rust, and persistent Rust processes. For persistent operation, reuse immutable source ASTs/maps keyed by source, parser, schema, and catalog fingerprints. Apply each patch independently to the pinned base; prevent state from leaking between requests. Keep live import checks isolated initially.

Test request concurrency of one, two, and four within the current CPU quota, including bursts and long-import cases mixed with normal patches. Record queue delay, selector latency, requests/patches per second, total CPU per patch, memory, and recovery after malformed inputs or worker death. Repeated inputs may test cache mechanics but must be identified as repeated inputs.

**Question:** how much improvement comes from process/cache reuse, and how much remains attributable to Rust? Add stale-cache and cross-request-contamination checks before using persistent results for a passing decision.

### Trial 6 Expand worker and grouping experiments

Compare longest-first bins, module grouping, class grouping, and duration-targeted shared queues with identical test selections. Include persistent `loadfile` workers as an existing implementation control. Sweep worker caps one, two, three, and four on the current host. Test eight only on sufficient capacity or mark the run explicitly as oversubscribed; a larger worker count is not a larger machine.

Measure collection/import time, fixture setup/teardown, actual test time, command startup, CPU contention, and memory. Use bounded profiling runs to investigate why module grouping helped. Compare isolated sensitive tests with representative co-runs to distinguish setup reuse from order-dependent outcomes.

Before work stealing re-enters valid comparisons, isolate the observed memory-test sensitivity without weakening its assertion or omitting it. Recheck patched and unpatched controls, complete selected outcomes, and the full configuration. Preserve failing attempts in the report.

**Deliverable:** latency/resource frontiers by workload family. Fit a worker-cost predictor using prior measurements of grouping, setup, and contention; evaluate its choices on unseen patches. Do not select a winner using current-patch timing and present that as a predictive scheduler.

### Trial 7 Build a persistent relay with measured expansion decisions

Use long-lived workers and a central queue to avoid spawning pytest for every microbatch. Start with one host and then compare with the best static/grouped plan at the same total CPU and memory. Keep module/class affinity where possible, and split groups only when measured latency benefit outweighs additional setup and memory cost.

Sweep batch granularity, initial worker count, and expansion thresholds independently. Start with fixed durations and no risk predictor. Add a worker only when estimated completion-time reduction justifies its cost or a declared deadline requires it. Preserve a nonzero capacity floor, queue fairness, and completion barriers.

Add live failure/result delivery. Record worker detection, controller receipt, and final completion as different events; the earlier retrospective timestamps are not delivered-feedback measurements. Monotonic clocks can be compared within one host. Across hosts, measure controller-side arrival times and retain clock uncertainty rather than subtracting unrelated monotonic clocks.

Inject worker crashes, missing results, timeouts, and duplicate deliveries. Every required test must have a terminal outcome before success; retries must remain attributable by task and attempt ID.

**Deliverable:** a relay that actually retains worker state, streams observations, and completes mandatory checks. Compare it with static grouping and existing persistent-worker implementations, not only with the earlier per-batch subprocess prototype.

### Trial 8 Evaluate probability guided allocation in shadow mode

Train only on earlier, independently observed outcomes. Begin with simple historical failure-rate and change-family baselines before adding a larger model. Hold out later patches and related change families. Measure probability calibration, rare/unknown changes, severity, and model/inference cost.

Distinguish the probability that a patch has a test-revealed defect from the probability that a proposed set omits at least one failing test. Tests sharing a bug or fixture are correlated; do not multiply independent probabilities without evidence. Keep buggy-patch detection and all-failure coverage as separate evaluation metrics.

Compare neutral allocation, work/setup-aware allocation, and probability-aware allocation while preserving the same mandatory set. Risk may change ordering and capacity; it does not authorize dropping tests or issuing an early passing verdict. Low estimated risk must not starve a patch, and uncertain/unsupported changes must retain conservative coverage.

**Deliverable:** held-out calibration and an allocation comparison at equal resource budgets. Any later omission policy requires a separate explicit coverage decision and evidence beyond these trials. No omission policy is part of this plan.

### Trial 9 Test the relay across hosts

After local correctness gates pass, use a fixed total CPU/memory budget across one, two, and four hosts. Compare static shards, persistent affinity workers, and the relay. Account for artifact transfer, source/environment preparation, network latency, control traffic, startup, retries, and idle capacity. Amortize a prepared worker pool separately from cold infrastructure provisioning.

Use immutable source/dependency fingerprints and task leases, heartbeats, attempt IDs, and result deduplication. Exercise lost workers and coordinator restart. Compare measured completion and actual billed resource use; more machines or more allocated CPU must be visible rather than credited as a scheduling improvement.

**Deliverable:** a scale curve and a failure-recovery result. This is an operational executor experiment; the challenge still accepts only static plans with at most eight workers.

## Measurement protocol and decision rules

Keep four measurement boundaries explicit:

| Boundary | Primary measurements |
| --- | --- |
| Selector request | Cold/warm wall time, total CPU including helpers, queue delay, throughput, peak memory, fallback frequency. |
| Challenge submission | Official modeled cost, full container CPU, validity, and misses under the specified three-run scoring rule. |
| Real validation | Time to complete all mandatory checks, delivered first-failure latency, outcome equivalence, worker occupancy, total CPU, and memory. |
| Fleet | Throughput and tail latency at equal provisioned resources, network/provisioning overhead, recovery, and billed cost. |

Record process-tree or isolated-container memory with the collection method; a parent's RSS does not establish fleet memory. Count nested subprocesses and distinguish requested caps, actual peak workers, worker-seconds, and CPU seconds. Sum of top-level command lifetimes is an occupancy proxy, not a cloud bill.

Run challenge-conformance trials under the repository's container limits: two CPUs, 4 GB memory, 256 processes, no network, and a read-only application filesystem. For operational worker comparisons, hold machine resources constant and record the actual quota; the preceding cloud sweeps used a four-CPU quota and 16 GB memory limit. If the container environment cannot be reproduced, retain the native measurements and mark exact challenge-environment conformance as unverified.

For small selector trials, use one explicit warmup followed by at least 30 paired fresh-process blocks per finalist, with seeded randomized order and identical inputs. Separate cold filesystem-cache experiments from warm runs. Compile Rust in release mode; record build time and artifact size separately.

For execution, first screen the bounded diagnostic matrix, then run finalists on new labelled patches. A practical initial budget is three repetitions of the two diagnostic cases, three policies, and caps one/two/four; then at least ten unseen patches with paired repeats of the finalists. Freeze the finalist configurations before the holdout. These are planning counts, not sufficient evidence for a claimed rare miss rate. If full-suite labelling or compute exceeds the budget, report the incomplete layer rather than substituting passing selected tests as ground truth.

Use paired differences and confidence intervals with patches/change families as the unit for generalization. Do not count thousands of subtests or correlated mutants as independent patches. Measure tail latency with an adequate request sample rather than inferring p95 from two timings. Preserve every attempt and classify failures; do not retry until green and discard earlier outcomes.

The following are proposed decision thresholds to freeze before final evaluation:

- **Compatibility:** no unexplained selection, fallback, protocol, or outcome differences. A faster but incompatible candidate does not pass.
- **Selector service:** consider adoption for at least a 30% reduction in p95 selector latency, twice the throughput at equal resources, or 25% lower peak memory at equal service level. Choose the primary service objective before the holdout; preserve the other declared limits.
- **Challenge score:** require a repeatable improvement beyond measurement variation and the score's 0.1-second reporting granularity. Run the official scoring sequence separately from statistical diagnostics.
- **Execution:** prefer a validated Pareto improvement. As an initial materiality rule, target at least 15% lower completion latency at a fixed resource budget, or 20% less resource consumption with no more than 5% latency regression. These are proposed engineering thresholds, not user-specified SLAs.

For the challenge, retain `max(load) + 2 seconds × nonempty workers`. For operational planning, evaluate measured wall time against a declared worker/resource price, budget, or deadline. Publish the frontier if that preference is undecided. Do not silently equate an illustrative two-second worker penalty with actual billing.

## Promotion order and required outputs

Start with Trial 0. Trials 1–4 then deliver the full Rust selector; Trial 5 separates language effects from process lifetime. Trials 6–7 can proceed independently once outcome measurement is reliable. Probability and multi-host trials follow validated execution and representative historical data.

Promote components independently when useful: a portable map, a Python persistent service, module-aware execution, and a Rust CLI need not ship together. Keep a feature-flagged Python fallback for a candidate Rust rollout, and include fallback cost and frequency in the rollout results.

Each trial must produce its implementation/version manifest, reproducible command/configuration, raw timings and resource records, complete correctness/outcome evidence, and a decision marked supported, rejected, or inconclusive. The final recommendation must state the workload and objective it improves, the resource price used to choose worker counts, residual compatibility gaps, and rollback behavior.

The current roughly 0.5-second selector CPU versus 121.1 modeled seconds implies a roughly 0.4% ceiling on score improvement from eliminating selector CPU entirely, holding selected work and the scoring model fixed. That bound does **not** limit selector-only throughput gains or gains from better real execution. No Rust speedup has yet been measured; the staged full-port trials above are how to determine it.

## Reference implementation and evidence

- [Current selector](../../selector.py) and [scoring contract](../../SCORING.md).
- [Experiment commands and architecture contracts](../../experiments/README.md).
- [Worker architecture and Rust assessment](worker-architecture-rust.md).
- [Earlier selector and execution comparison](cloud-experiment-results.md).
- Existing raw measurement snapshots: `experiments/results/`. Keep their source manifests and failure records when creating new trials.

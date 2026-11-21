# Test selection, probability, and adaptive execution

Research date: 2026-11-21. Baseline: `3fd38f73ffca368c4a7b4c7472ea0b127f0b4bc3`. This is a design comparison, not an implementation or a benchmark of competing tools. External claims below use primary sources; mathematical models and the proposed relay are our own derivations.

## Decision

Keep scanning every patch. Preserve the current conservative selection as the initial coverage floor, and first use probabilities to prioritize feedback and allocate execution capacity. A probability model should not authorize omissions until it has independently measured all-failure coverage on representative, fully labelled patches. A relay that eventually completes required validation fits the user's reliability preference; a relay that stops after several passing tests does not automatically preserve it.

The earlier perfect-information comparison does not show that no-failure patches can be identified in advance. Its 26.749-second gap is a hindsight reference point, not a safe savings forecast.

## What this repository actually optimizes

The hunter combines per-test executed lines, opened files, instantiated classes, AST change locations, and import-time dependencies. The catcher broadens uncertain selections and checks imports in subprocesses. `schedule()` packs the selected set onto one to eight workers. This is already a hybrid dependency-based selector rather than a classifier that predicts whether an entire patch is buggy. The baseline is conservative by design, but sample success is not a general soundness proof. See [README](../../README.md), [analysis and fallbacks](../../selector.py#L454), [import check](../../selector.py#L723), and [scheduler](../../selector.py#L763).

The challenge expects one static list of worker assignments for every patch. It charges `maximum worker load + 2 seconds × workers`, plus container CPU, plus 300 seconds if **any** failing test is omitted. A patch for which one failing test is found can still be a scored miss if another failing test is absent. There is no protocol for receiving test results and submitting a revised plan. See [SCORING](../../SCORING.md).

The preceding audit reproduced 121.100 modeled seconds and zero misses on all 30 bundled patches. For each selected set, the schedule equals the lower bound `min_w(max(longest_test, ceil(total_work/w)) + 2000w)` in milliseconds. Thus this sample has no packing headroom without changing selected work. Six patches with no failures incur 25.892 seconds; that is the observed cost of conservative validation, not evidence it was avoidable beforehand. The audit recorded three selector runs at approximately 0.47–0.57 CPU seconds and less than 1 ms of scheduler CPU per sample batch. Evidence in this workspace: `/workspace/scratch/blast-radius-audit/analysis.json`, `timings.json`, and `output-{0,1,2}.jsonl`. These are local Python 3.13 measurements, not production latency or competing-tool results.

## Existing approaches compared with ours

| Approach | Evidence and mechanism | Comparison and useful lesson |
| --- | --- | --- |
| **pytest-testmon** | Tracks executed Python code using Coverage.py, updates its dependency database after execution, and reruns previously failing tests. Its `--testmon-noselect` mode prioritizes likely failures without deselecting tests. The documented dependency scope excludes static assets and external services. [Official documentation](https://www.testmon.org/) | Closest language-level comparison. Our explicit fixture-open and spawned-worker mapping address dependency categories requiring care. Its no-deselection mode is a concrete precedent for probability/order improvements without a new omission policy. No comparative speed claim is established. |
| **Ekstazi** | Records dynamic test dependencies on files, including executable code and resources; unchanged dependencies allow tests to be omitted. The author's evaluation concerns Java/JUnit projects. [Author dissertation, abstract and Chapter 3](https://users.ece.utexas.edu/~gligoric/papers/Gligoric15PhD.pdf) | Similar dependency foundation; ours is finer-grained and specialized to pinned sqlglot. Finer selection is beneficial only if its additional analysis remains correct and inexpensive. Safety claims depend on the modeled environment and dependency assumptions. |
| **STARTS** | Static class-level selection for Maven/Java, using type dependency reachability rather than runtime instrumentation. [Project](https://github.com/TestingResearchIllinois/starts), [authors' paper](https://mir.cs.uiuc.edu/~marinov/publications/LegunsenETAL17STARTS.pdf) | A broader static dependency closure could inform our catcher. This is an architectural comparison, not a Python replacement or proof that adding a graph makes selection complete. |
| **Azure Test Impact Analysis** | Includes impacted, new, and previously failing tests; unsupported changes fall back to all tests, with periodic full-run overrides. The cited product supports a constrained managed-code, single-machine setting. [Microsoft documentation](https://learn.microsoft.com/en-us/azure/devops/pipelines/test/test-impact-analysis?view=azure-devops) | Closely matches hunter/catcher behavior. Explicit unsupported-change rules and a separate full-validation policy are useful; its tooling is not a drop-in for this Python challenge. |
| **Meta predictive test selection** | Learns failure likelihood from change/test history. Its reported deployment caught over 99.9% of regressions while running about a third of transitively dependent tests; exhaustive testing still occurred before production deployment. [First-party description](https://engineering.fb.com/2018/11/21/developer-tools/predictive-test-selection/) | Supports the feasibility of risk-based selection at scale, not the same result here. Detecting a buggy change is weaker than selecting every failing test, which our scorer demands. Historical results and denominators are not transferable guarantees. |
| **Microsoft statistical selection** | A language-agnostic lightweight model evaluated on 22 large repositories reported 15–30% compute savings while reporting approximately 99% of buggy PRs. [Microsoft Research publication](https://www.microsoft.com/en-us/research/publication/data-driven-test-selection-at-scale/) | Relevant low-feature-cost baseline before complex ML. Again, buggy-PR detection and all-failure coverage are different metrics. The paper's savings are not an estimate for this repo. |
| **Develocity predictive selection** | Uses change/test history, selects all impacted tests during insufficient history, always includes recent new/changed/failed/flaky tests, and offers confidence profiles. Its relevant/remaining modes divide a changeset's tests across stages. [Official documentation](https://docs.develocity.ai/2026.2/using-develocity/predictive-test-selection/) | The closest product precedent for a relay: early likely failures followed by remaining validation. If merge safety is required, remaining tests must finish before merge rather than relying on a later nightly run. |
| **Bazel dynamic execution** | Races the same action locally and remotely, takes the first completed branch, and cancels the other. [Official documentation](https://bazel.build/remote/dynamic) | A latency-control mechanism, distinct from risk-based test selection. It can consume duplicate work and needs an actual execution system. It does not remove required tests or justify workers proportional to bug probability. |

## Probability model that retains patch scans

The following is our derivation, not a claim about one of the products. Let `x` be features obtained from scanning a patch, `F` its unknown set of failing tests, `S` the scheduled set, and `T(S,w)` its modeled execution cost. The expected challenge objective is:

```text
J(S,w | x) = scan_cost(x) + model_CPU(x)
             + T(S,w) + 300 × r(S | x)
r(S | x)   = P(F is not a subset of S | x)
```

If `B` means that at least one catalogued test fails, then:

```text
r(S | x) = P(B | x) × P(at least one failing test is omitted | B,x,S)
```

`P(B|x)` alone is insufficient. A high-risk patch can have one easy-to-cover failure or many failures across unrelated tests. A low predicted risk can also be model uncertainty. This definition concerns test-revealed regressions; bugs not detected by the available suite are outside the scorer's labels.

Under an expected-score policy, adding a test group `G` is worthwhile when its additional execution and CPU cost is less than `300 × [r(S)-r(S∪G)]`. Conversely, a 2-second saving that adds a one-percentage-point omission probability worsens expected cost by one second: `-2 + 300×0.01 = +1`. These are illustrative assumptions, not measured probabilities.

Given the preference for avoiding misses, use a coverage constraint first: preserve `S_baseline ⊆ S` initially. A later experiment could minimize time subject to a validated upper confidence bound on all-failure omission risk being below an explicitly chosen threshold. Preserving the baseline preserves its scheduled coverage, not an unproven guarantee that the baseline catches every possible defect. For real-world losses, the challenge's 300 seconds must be replaced by severity-dependent consequences, not treated as the true value of a missed defect.

Do not multiply independent pass probabilities across tests without justification. Tests sharing a fixture, parser path, or bug are correlated. Per-test marginal failure probabilities can provide a conservative union bound without an independence assumption: if `q_j=P(test j fails | x)`, then `r(S | x) ≤ sum_{j not in S} q_j`. Summing these marginals is not generally an exact patch-level risk or a complete resource policy.

## Relay network: useful interpretation and limits

Interpret “relay network” as a staged execution controller, rather than assuming a neural architecture:

1. **Every patch enters scan and classification.** Collect dependency, unknown-change, and estimated-work features. Unsupported changes escalate coverage immediately.
2. **First execution wave.** Run high-value tests early, preserving mandatory tests. This can shorten time to the first actionable regression in a real CI system.
3. **Expansion and continuation.** Dispatch the remaining baseline-selected tests and add broader tests when uncertainty, observed failures, or unsupported behavior warrants it. Passing a few tests alone does not establish safety.
4. **Completion barrier.** A passing merge verdict requires all mandatory validation to finish. Early failures may report a rejection sooner, but canceling work would conflict with this challenge's all-failing-test requirement.

A purely serial relay can delay clean-patch completion and pay repeated startup costs. Retain bounded parallelism, deadlines, and queue fairness; benchmark orchestration and data-transfer overhead. Update probabilities from observed outcomes only with a model that handles correlated evidence and execution-environment failures.

For the existing challenge, use precomputed risk only for static choices: no live result feedback is available, test order does not alter the modeled total, and workers cannot exceed eight. Building a distributed relay would therefore change the execution platform rather than improve the existing output protocol. In real CI, a relay can improve early feedback while retaining required coverage, but it needs separate measurements of first-failure latency and full-pass latency.

Worker counts should follow marginal benefit rather than a rule linear in bug probability. For divisible work `W` measured in seconds and this scorer's overhead, `T≈W/w+2w` has continuous optimum `w≈sqrt(W/2)`, clipped to one through eight and checked at integer values. Mandatory work does not disappear when predicted bug probability falls. Across competing patches with a fixed fleet `N`, minimizing idealized weighted latency `sum_i p_i W_i/w_i` subject to `sum_i w_i=N` gives `w_i ∝ sqrt(p_i W_i)`. That result assumes divisible work, no setup cost, no queueing, and no worker bounds; actual allocation must include these constraints and prevent starvation.

## Evidence needed before changing policy

- Keep full labels from every patch's required scan and independent full-suite validation. A model trained only on selected outcomes cannot observe omitted failures. Use shadow mode first, without affecting selection.
- Split evaluation chronologically and group variants of the same original patch. Do not train on sample answers and then report the same sample as a generalization result.
- Measure patch-level all-failure omission, per-test recall, buggy-patch detection, scan/inference CPU, worker cost, first-failure latency, full-pass latency, and uncertainty separately.
- Evaluate calibration and rare-change behavior by change family. The repository's generated mutants resemble sample mutations by design, so their bug prevalence and coverage cannot be assumed to represent natural development. See [mutation generator](../../build/mutgen.py).
- Zero observed misses on 30 patches does not establish negligible risk. Only 18 sample patches have failing tests. Under an illustrative IID Bernoulli model, the one-sided 95% upper conditional miss bound after zero misses in `n` independent faulty patches is `1−0.05^(1/n)`; for 18 it is about 15.33%. This curated sample is not representative production evidence, so this calculation illustrates the sample-size limitation rather than establishing a production bound. Correlated patches further weaken the simple model.
- Compare four policies on identical held-out inputs: current baseline, risk ordering with identical coverage, probability-based additions, and an experimental constrained omission policy. Only a future executor can fairly evaluate a feedback-driven relay.

Recommendation: prototype probability estimates in shadow mode and a coverage-preserving priority policy first. Retain the current scheduler for the challenge; it is already optimal for the measured sample selections. Consider a relay when moving to an executor that can stream outcomes and enforce completion barriers, rather than using it to infer that an apparently clean patch needs no further checks.

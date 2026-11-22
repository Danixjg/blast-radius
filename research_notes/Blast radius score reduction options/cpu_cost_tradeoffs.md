# CPU cost and CPU-vs-simulated-time trade-offs (current main 08aeb05, image br-eval:pr7)

All measurements were made by this researcher in Docker with the official flags (`--cpus 2 --memory 4096m --read-only --network none --user 65534`, the same flags as build/docker_bench.sh), using image br-eval:pr7. CPU is the container cgroup's `usage_usec`, as docker_bench.sh reads it. Per-phase CPU comes from a wrapper (wrap2.py) that reads the same cgroup counter around each selector function, with probes and verifier runs forced to run one at a time so each can be attributed. Running them serially changes wall time but not CPU. Simulated seconds and misses come from kit/score.py. Raw data: [summary.json](artifacts/cpu/summary.json); per-run stdout and stderr: artifacts/cpu/runs/; scripts: artifacts/cpu/wrap.py, artifacts/cpu/wrap2.py, artifacts/cpu/elig.py, artifacts/cpu/fwd.py, artifacts/cpu/sel_targeted.py.

**Noise warning (applies to every CPU number below):** during all runs the host was busy with another session's labelling job (`build/label.py` running `pytest -n 4` plus 4 worker processes at 75-93 % CPU each). Load average stayed between 6.4 and 9.7 on 8 logical CPUs. Under this load, absolute container CPU came out about 1.5-2.3x the quiet-machine figures in the project context (for example sample base median 4.51 s against about 2.0 s documented; holdout1 9.9-12.9 s against about 6.2 s). Runs were interleaved so all configs saw the same load. Rely on paired differences and shares more than absolute values. A "clean" estimate below is the contended figure scaled by about 0.5 (the observed ratio of documented to measured base CPU, 0.44-0.63). That scaling is an inference, not a measurement.

## Q1. How much CPU does each component use on sample, holdout1, holdout2 and mutants?

### Takeaway
Container CPU is a few seconds per set: about 2 s (sample), 6 s (holdout1 and holdout2) and 11 s (mutants) on a quiet machine. Nearly all of it is in child processes: the verifier and the import probes. The selector's own start-up, map loading and profile loading are negligible (about 0.15-0.18 s contended). No CPU cut can produce 74 s on any set; the most is the set's whole CPU, 2-11 s.

### Cited Findings
Phase CPU from wrap2, median of 3 runs per set, in contended seconds. All 4 sets reproduced main's simulated score exactly, with 0 misses ([summary.json](artifacts/cpu/summary.json) key `wrap2`):

| set (commits) | container total | outside Python + import selector + load_map + load_profile | analyze (all patches) | run_probes (incl. server start) | verify | simulated / misses |
|---|---:|---:|---:|---:|---:|---:|
| sample (30) | 4.65 | 0.18 (0.096 + 0.032 + 0.040 + 0.008) | 0.40 | 0.48 (1 fresh probe) | 3.62 (5 runs) | 97.076 / 0 |
| holdout1 (66) | 9.86 | 0.16 | 0.89 | 4.31 (17 probes, 16 forked; server start 0.17) | 4.46 (12 runs) | 618.608 / 0 |
| holdout2 (65) | 9.08 | 0.15 | 1.21 | 3.23 (15 probes, 14 forked; server start 0.14) | 4.76 (11 runs) | 545.397 / 0 |
| mutants (100) | 19.60 | 0.15 | 1.26 | 10.67 (33 probes: 20 forked, 13 fresh) | 7.35 (15 runs) | 1065.357 / 0 |

- Per-probe CPU by probe-server checkpoint (stage 0 = after `import sqlglot`; stages 1 and 2 = inside the dialect block; fresh = a new interpreter). Median contended s per probe: holdout1 stage0 0.236 (n=8), stage1 0.166 (n=4), stage2 0.152 (n=4), fresh 0.656 (n=1); holdout2 0.239 / 0.165 / 0.140 / 0.418; mutants stage0 0.297 (n=12), stage1 0.176 (n=2), stage2 0.137 (n=6), fresh 0.384 (n=13, 5.04 s in total). The 13 mutant fresh probes all patch modules that `import sqlglot` itself loads (sqlglot/expressions/*.py, tokens.py, errors.py, parsers/base.py, dialects/dialect.py, _typing.py), so the server cannot serve them. [summary.json](artifacts/cpu/summary.json) key `detail`.
- Import micro-benchmark inside the image (`python -I -S`, gc off, contended, median of 3): `import sqlglot` 0.156-0.173 s. Importing the other 182 sqlglot modules after it: 0.226 s. Importing one extra module after it: 0.010 s (dialects.tsql), 0.036 s (dialects.duckdb), 0.008 s (optimizer.qualify). [imp.py](artifacts/cpu/imp.py)
- Verifier runs cost about 0.20-0.27 s each for a 1-test run (start-up, the patched `import sqlglot`, test-module import). The largest was 1.68 s (23 tests, 838 ms recorded). A least-squares fit over all 129 measured runs: CPU ≈ 0.261 + 1.484 × (recorded test seconds) + 0.011 × (test files), contended. [summary.json](artifacts/cpu/summary.json) key `coef`
- Probe-server value, paired per round (noserver minus base, contended): holdout1 +2.8/+3.2/+4.4 s, holdout2 +8.5/+4.1/+0.2 s, mutants +4.1/+3.9/+3.7 s, sample about 0 (one probe, and the server does not start). The server saves about 3-4 s contended (about 1.5-2 s clean) per probe-heavy set. [summary.json](artifacts/cpu/summary.json) key `matrix`
- The selector's own analysis (Hunter.analyze over all patches) is 0.4-1.3 s contended, which is 10-20 ms per patch. Commits with empty selections (sample 11, holdout1 16, holdout2 18, mutants 9) already finish inside this cheap analysis.

### Inferences
- Approximate clean shares (contended × about 0.5): holdout1 is about 0.08 start-up/map, 0.45 analysis, 2.1 probes and 2.2 verifier. Mutants is about 0.6 analysis, 5.3 probes and 3.7 verifier. Probes and the verifier are about 85-90 % of CPU on every set except the sample, where the verifier alone is about 78 %.
- "Lazy loading" and start-up work are already small: import selector, load_map and load_profile together cost about 0.08 s contended. Little is left to win there (under 0.1 s per set).
- "Avoiding work for comment-only commits" is worth at most tens of milliseconds, because analysis is about 15 ms per patch and comment-only commits start no probe or verifier.

### Gaps
- No quiet-machine run was possible while this research ran, because another session's labelling job kept load at 6-10. Clean absolute numbers are inferred by scaling, not measured.
- holdout2u was not measured. Its patches are a subset of holdout2's (61 of 65), so it should behave like holdout2.

## Q2. Verifier: CPU cost per simulated second saved, the effect of relaxing its exclusions, and which exclusions are risky

### Takeaway
On the current rules the verifier saves 4.1-11.0 simulated s per set and costs 3.6-8.6 s contended CPU, about half of that clean. That is net +1.3 to +6.7 s clean, but only +3.4 to -1.4 s at today's contention level, so it is close to break-even on holdout1. Its size and cost caps never bind. What blocks more savings is the unsafe-test exclusions, and lifting them costs far more CPU than it saves, apart from one narrow case (test_generator_memory with fail-fast).

### Cited Findings
- Simulated time saved by the verifier (BR_VERIFY=0 against default, deterministic; misses stay 0): sample 104.077 → 97.076 (7.001 s), holdout1 622.702 → 618.608 (4.094 s), holdout2 553.860 → 545.397 (8.463 s), mutants 1076.405 → 1065.357 (11.048 s). [summary.json](artifacts/cpu/summary.json) key `matrix`
- The verifier's CPU, paired per round (base minus noverify, contended): sample 3.71/3.61/2.18, holdout1 5.40/5.67/5.49, holdout2 5.02/6.21/6.74, mutants 6.74/8.55/9.18. Medians: 3.6, 5.5, 6.2 and 8.6 s. That gives these simulated seconds saved per contended CPU second: sample 1.9, holdout1 0.74, holdout2 1.36, mutants 1.28. [summary.json](artifacts/cpu/summary.json)
- Median totals, contended (simulated + CPU), base against BR_VERIFY=0: sample 101.58 vs 104.88 (verifier helps by 3.3 s), holdout1 631.55 vs 629.97 (verifier costs 1.6 s), holdout2 559.08 vs 560.80 (helps by 1.7 s), mutants 1088.44 vs 1091.84 (helps by 3.4 s).
- Wasted runs, where every test failed and nothing was dropped: holdout1 10 of 12 runs (3.78 of 4.45 s verifier CPU); holdout2 4 of 11 (1.12 of 4.55 s); mutants 6 of 15 (1.93 of 7.36 s); sample 1 of 5 (0.33 of 3.61 s). On real-commit sets the verifier mostly confirms failures. The analysis has already pruned harmless tests, so the "half of edits break nothing" prior behind the `cost >= 2*(VERIFY_START + ms)` rule is too optimistic for holdout1. [summary.json](artifacts/cpu/summary.json) key `detail`
- Knob sweeps (Docker, 3 interleaved runs each). BR_VERIFY_MAX=5 and BR_VERIFY_START=0.1 leave the job list unchanged on sample and holdout1: same 5 and 12 runs, same simulated time. BR_VERIFY_START=1.0 turns the verifier off completely on both sets (simulated time equals BR_VERIFY=0). [summary.json](artifacts/cpu/summary.json) key `matrix`
- Oracle replay (selections after the probes, taken from the BR_VERIFY=0 Docker output; a verified commit drops exactly its selected tests that are not labelled failing; CPU from the fit above, contended). This model reproduces main's simulated time exactly on all 4 sets (97.076 / 618.608 / 545.397 / 1065.357), which supports the verifier-fidelity assumption. Relaxations:
  - No cost threshold, any cap up to unlimited: sample, holdout2 and mutants unchanged. holdout1 gains 1.02 s simulated for +2.6 s CPU (one extra run). Not worth it.
  - Verifying the verifiable subset when unsafe tests are present ("partial"), caps kept: sample -0.26 s simulated for +5.3 s CPU; holdout1 0 for +3.4; holdout2 -1.57 for +8.0; mutants -4.13 for +29.2. Clearly negative.
  - Partial, with no cap and no threshold: holdout1 -9.8 s simulated for +66 s CPU; holdout2 -32.7 for +238; mutants -106 for +570. Strongly negative.
  ([summary.json](artifacts/cpu/summary.json) key `scenarios`)
- Hindsight upper bound: verify only the commits that would be profitable with perfect knowledge of which pass. At a clean CPU factor of 0.5 the extra net gain is sample 2.0 s, holdout1 15.0 s, holdout2 19.5 s, mutants 58.7 s. At contended factor 1.0 it is 1.5, 6.5, 6.9 and 3.6 s. Nearly all of these commits are blocked by tests/test_optimizer.py and tests/test_executor.py (VERIFY_UNSAFE_FILES) or by test_generator_memory (VERIFY_UNSAFE_TESTS). No real rule can reach this bound, because it needs foresight. [summary.json](artifacts/cpu/summary.json) key `hindsight`
- tests/test_optimizer.py and tests/test_executor.py import duckdb and pandas at module top level and use ProcessPoolExecutor and multiprocessing.get_context (sqlglot checkout lines 1-13 of each file). The image installs only python-dateutil, tzdata, pytz and mypy-extensions (Dockerfile; build/verify-requirements.txt), so these modules cannot even import in the verifier. ([Dockerfile](https://github.com/Danixjg/blast-radius/blob/08aeb05/Dockerfile); [selector.py VERIFY_UNSAFE_FILES](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py))
- test_generator_memory is 6.686 recorded seconds. The selector comment says it fails under pytest-xdist "whenever many other tests fail", which an isolated run cannot reproduce ([selector.py lines 75-77](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py)). It is the only blocker on holdout2 h33708, h33720, h33764 and mutants m1x014, m1x028, m1x032, m1x034. Allowing it only when every other selected test passes, with the other tests run first and the run stopped on the first failure: holdout2 saves 18.6 s simulated for about 11.3 s CPU at the clean factor (net about +7.3); mutants saves 18.4 s for about 11.2 s (net about +7.2); holdout1 and sample have no such commits. At contended CPU (factor 1) both turn negative (about -4 s each). Without fail-fast it is +2.3 (holdout2) and -2.9 (mutants) at the clean factor. [summary.json](artifacts/cpu/summary.json) key `gm`

### Inferences
- Risk classification of the exclusions:
  - Risky (false "pass", so a miss): test_typing.py (reads package files from disk, so it sees unpatched sources); the `subproc` mask (subprocesses run unpatched code); the `opens` mask where a sqlglot source is read from disk; test_dialect_entry_points.py and test_dialect_imports.py (rewrite global registries, which can corrupt later tests in the same process); test_generator_memory, if dropped while many tests fail.
  - Merely conservative (the test would error, be kept and waste CPU): test_executor.py and test_optimizer.py without duckdb and pandas (unless those were installed, which brings process-pool and spawn risk); test_sqlglotc.py and test_integration_loader.py (files outside the image); `opens` of fixture files that are absent.
- The test_generator_memory relaxation has a logical safety argument. If every other selected test passes on the patch, few tests fail overall, unless the selection already misses failures, which would be a miss anyway. Its value depends on CPU price: positive at quiet-machine CPU, negative at today's contention. Its CPU estimate assumes the test costs about 1.5x its recorded 6.7 s; that was not measured.
- A cheaper verifier start-up would make more 1-test runs profitable and shrink the waste. For example, fork the verifier from a checkpoint that already ran `import sqlglot`, unittest and the pinned packages, when the patch touches none of those modules (16 of 17 holdout1 probes were servable this way). The patched `import sqlglot` is about 0.16 of the about 0.25 s contended cost of a 1-test run. Estimated saving: about 0.16 s × eligible runs minus about 0.2 s for the checkpoint process. That is about 1.7 s contended (0.9 clean) on holdout1 and about 2.2 s contended (1.1 clean) on mutants. This is an estimate, not measured.

### Gaps
- The CPU of test_generator_memory, and of other large tests, when run in isolation in the verifier was not measured. The relaxations above extrapolate from a fit over small runs (at most 0.84 s recorded).
- No rule was found that predicts in advance which verifier runs will all fail (the holdout1 waste). Analysis features were not examined.

## Q3. Marginal value of CPU: which knobs (BR_* env vars) move total = simulated + CPU down, and by how much

### Takeaway
No existing BR_* setting lowers the total by more than noise on every set. The default configuration is at or near the best setting of every knob. The only measured CPU cut that keeps behaviour identical needs code: targeted import probes, which import only the patched modules and the modules that import them. It cut 1.6-3.2 s contended (about 0.8-1.6 s clean) per probe-heavy set, with identical verdicts, simulated time and misses.

### Cited Findings
Median of 3 interleaved Docker runs per cell. Δ total is against base in the same set. CPU is contended. ([summary.json](artifacts/cpu/summary.json) key `matrix`)

| knob | sample Δsim / ΔCPU | holdout1 Δsim / ΔCPU | holdout2 Δsim / ΔCPU | mutants Δsim / ΔCPU | verdict |
|---|---|---|---|---|---|
| BR_VERIFY=0 | +7.001 / -3.6 | +4.094 / -5.5 | +8.463 / -6.2 | +11.048 / -8.6 | worse except holdout1 under contention |
| BR_PROBE_SERVER=0 | 0 / ~0 | 0 / +2.8 to +4.4 | 0 / +0.2 to +8.5 | 0 / +3.7 to +4.1 | worse |
| BR_PROBE_CHECKPOINTS=0 | 0 / ~0 | 0 / +2.9, -1.5, +1.1 (noise) | not run | not run | no measurable gain; per-probe data suggests about +0.6 s contended |
| BR_VERIFY_MAX=5 | 0 / noise | 0 / noise | not run | not run | same job list, no effect |
| BR_VERIFY_START=0.1 | 0 / noise | 0 / noise | not run | not run | same job list, no effect |
| BR_VERIFY_START=1.0 | +7.001 / -3.5 | +4.094 / -7.8 | not run | not run | equals verify off |

- BR_BUDGET, BR_RESERVE and BR_PROBE_TIMEOUT only matter when probes hang or the batch nears its 150 s budget. No run here came close (wall 3-9 s per run), so changing them would not change the outcome on these sets. [selector.py lines 45-51](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py)
- BR_EQ_SHORT_CIRCUIT acts at analysis time. RESULTS.md credits it with holdout1 654.997 → 618.608 s and other sets unchanged ([RESULTS.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md)). Its CPU sits inside "analyze", 0.9 s contended for all of holdout1.
- Targeted probes, tested here as a measurement-only copy of selector.py mounted over /app/selector.py. The copy builds the probe's module list from a forward-import map recorded at runtime with the host venv: one fresh interpreter per module, recording which sqlglot modules it loads. In the image that map could live in map.pkl, where its build is free. A probe then imports `sqlglot`, the patched modules and every module whose import loads one of them; modules not in the map are always imported. Results, median of 3 interleaved runs against the unmodified image ([summary.json](artifacts/cpu/summary.json) key `targeted`):
  - holdout1: probe CPU 4.76 → 2.39 s; container CPU 11.12 → 8.62 s (-2.5); simulated 618.608 both; 0 misses both.
  - holdout2: probes 3.63 → 2.08 s; container 11.26 → 9.63 s (-1.6); simulated 545.397 both; 0 misses.
  - mutants: probes 10.97 → 7.86 s; container 19.92 → 16.74 s (-3.2); simulated 1065.357 both; 0 misses.
  - Every probe verdict, and every "import probe broken" or lazy-load log line, was identical. Examples of reverse dependencies: dialects.duckdb → only itself; dialects.postgres → materialize, redshift, risingwave; dialects.mysql → 8 modules. ([fwd.json](artifacts/cpu/fwd.json), [sel_targeted.py](artifacts/cpu/sel_targeted.py))
- Simulated time lost to catch-all commits, for context only (selection scope, not CPU): holdout1 7 commits × 36.817 s = 257.7 s (oracle 189.7 s for them); holdout2 4 commits = 147.3 s (oracle 73.0); mutants 3 commits = 110.5 s (oracle 40.0); sample 0.

### Inferences
- The marginal rate at the scorer is 1 CPU second = 1 simulated second. The verifier buys simulated seconds at about 0.5-1.4 CPU s each (clean to contended), so it is worth having overall, but its holdout1 wins are marginal. The probe server and the checkpoints are pure CPU savings and are already on.
- Ranking of CPU-side options per set, in clean seconds (contended in brackets). Clean figures are about 0.5x contended unless stated:
  - Targeted probes: holdout1 -1.2 (-2.5), holdout2 -0.8 (-1.6), mutants -1.6 (-3.2), sample 0. Measured; no simulated change.
  - Verifier forked from an `import sqlglot` checkpoint: holdout1 about -0.9, mutants about -1.1, holdout2 about -0.8, sample about -0.4 (estimate).
  - test_generator_memory with fail-fast: holdout2 about -7 net, mutants about -7 net (clean); about +4 net worse at today's contention (estimate; riskiest of the three).
  - Everything else (lazy loading, comment-only shortcuts, start-up): under 0.1 s.
- Realistic combined CPU-side gain: about 2-3 s clean per holdout set, about 3 s on mutants and under 1 s on the sample. The test_generator_memory trade, if adopted, adds about 7 s on holdout2 and mutants. **A 74 s cut is not reachable from CPU or verifier trades on any set.** The whole quiet-machine CPU is about 2 / 6 / 6 / 11 s. The gap to the oracle is selection precision: about 309 s on holdout1, 311 s on holdout2 and 738 s on mutants. 74 s is plausible only on holdout1, holdout2 or mutants, and only through selection changes, not CPU.

### Gaps
- Targeted probes were validated on holdout1, holdout2 and mutants only (65 probes in total). A patch that breaks an unrelated module only through runtime side effects, such as registries mutated at import time, would be missed by the targeted list. No such case occurs in these sets.
- BR_PROBE_CHECKPOINTS, BR_VERIFY_MAX and BR_VERIFY_START were swept on sample and holdout1 only. Oracle replay shows the caps also do not bind on holdout2 and mutants.

## Q4. Measurement noise

### Takeaway
Under today's host load, a single Docker run's CPU varies by up to ±40 % (holdout1 base 7.5-13.3 s). Reliable CPU claims need paired, interleaved runs. Medians of 3 can separate effects of 2 s or more, but not sub-second ones such as the checkpoint count.

### Cited Findings
- Base runs: sample 2.89-5.64 s across 7 runs; holdout1 7.47-13.25 s across 6 runs; holdout2 8.14-13.89 s across 6 runs; mutants 19.32-23.98 s across 6 runs. Simulated time was identical in every run (deterministic). [summary.json](artifacts/cpu/summary.json)
- Runs with identical behaviour on holdout1 (base, BR_VERIFY_MAX=5 and BR_VERIFY_START=0.1 run the same 12 verifier jobs and 17 probes) gave medians of 12.94, 10.74 and 8.79 s, a 4 s spread caused only by changing load.
- With the official wrapper (plain `python -S -c`) and with the wrap.py instrumentation, sample CPU was indistinguishable: plain 4.83/5.22/5.36 against wrapped 5.64/5.33/4.05. The instrumentation overhead is within noise.

### Inferences
- The CPU numbers above should be re-measured on a quiet machine before any decision that turns on 1-2 s, for example whether the verifier pays off on holdout1 or whether the test_generator_memory trade is positive.

### Gaps
- No quiet-machine run was possible during this research, because of another session's concurrent labelling job.

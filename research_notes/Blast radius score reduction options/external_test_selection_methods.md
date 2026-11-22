# External regression-test-selection (RTS) and test-impact-analysis methods not yet in the repo, and which could cut seconds here

Scope note: all "measured" numbers below were produced by me on this machine (WSL, not Docker) using the read-only main checkout (selector.py at 08aeb05) and the labelled sets. I ran the real `selector.py` (verifier included) once per set. That reproduces the Docker simulated numbers exactly: sample 97.076, holdout1 618.608, holdout2 545.397, holdout2u 534.169, mutants 1065.357, all with 0 misses. Scripts and raw outputs are in `artifacts/external_methods/` (breakdown.json, analyze.py, bounds.py, mkmapB.py, replay.py, stage.py, expA2.py, dropsave.py, h33758.py, lb.py). I took no CPU measurements in Docker. CPU figures given here are process CPU on the host and are indicative only.

## Q1. What does the repository already implement, and where do the remaining seconds go?

### Takeaway
The repo already has the main academic RTS ideas: per-test line coverage that also covers spawned pool workers (testmon/Ekstazi-like dynamic dependencies, at a finer level), value-level dictionary-read and operand-fact profiles, AST-equality / same-program rules (a Reks/FineRTS-style "behaviour-preserving change" filter), an import probe with a fork server, an in-container verifier that runs selected tests on the patch, and optimal packing. What is left is concentrated in two places. (a) Three or four very long tests that end up in selections where they do not fail: test_execute_tpcds 21.454 s, test_max_nodes 28.817 s, test_generator_memory 6.686 s. (b) "Run everything" after a failed import probe.

### Cited Findings
- Inventory: the hunter maps statement, lambda, signature, dict-entry, class-attribute, `is`/`==` and fixture edits to tests through map.pkl (line coverage incl. ProcessPoolExecutor workers and setUpClass, fixture opens, classes instantiated, import-time lines) and profile.pkl (dict value reads with provenance, pristine operand facts, unobserved tests). AST-equal files, docstring/annotation-only edits, new unread functions and `import <startup module> as <fresh>` select nothing. The catcher covers byte-level compile, pytest collection files and the import probe (one `-I -S` interpreter that imports every module, plus a fork-server checkpoint chain). The verifier runs small stdlib-only selections on the patched source and drops tests that pass. LPT packing is used for 1..8 workers — [README.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/README.md)
- The plan already lists, as internal next steps: unread new imports (~63 s/set), a new-conftest classification (~69 s/set), an investigation of `is`/`==` swaps without profile evidence (~43 s/set), probe CPU work, and an "import only the reverse import closure" probe idea (not started) — [docs/next-steps-plan.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/docs/next-steps-plan.md)
- RESULTS.md already names the remaining structural cost on one holdout: import failures that turn into "run everything", `is`/`==` swaps and code edits whose selection includes the 21 s test_execute_tpcds, and class-attribute string edits read by many tests — [RESULTS.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md)
- Measured gap to the oracle (only the failing tests, packed): sample 97.076 vs 94.351 (2.7 s); holdout1 618.608 vs 309.807; holdout2 545.397 vs 234.161; holdout2u 534.169 vs 222.933; mutants 1065.357 vs 326.938 — [breakdown.json / analyze.py](artifacts/external_methods/breakdown.json)
- Measured upper bound, "remove the five heavy tests from every selection where they do not fail": holdout1 −188.1 s (test_execute_tpcds alone −118.9, test_max_nodes −22.1, test_generator_memory −14.1); holdout2/2u −185.0 (tpcds −53.9, max_nodes −45.3, genmem −40.7); mutants −417.9 (tpcds −186.3, max_nodes −95.5, genmem −46.6); sample 0 — [bounds.py](artifacts/external_methods/bounds.py)
- Measured excess over the oracle for commits where a failed import probe made the selector run everything: holdout1 68.1 s (h12112 33.5, h12162 23.2, h12151 11.4); holdout2/2u 37.5 s (h33712 26.1, h33744 11.4); mutants 70.4 s (m2x029 34.8, m2x018 26.2, m2x021 9.4); sample 0 — [breakdown.json](artifacts/external_methods/breakdown.json)
- Measured: the LPT schedule is already optimal or nearly so. Its total gap to the per-commit lower bound min_k(max(longest test, ceil(sum/k)) + 2k) is 0.000 s (sample, holdout1), 0.003 s (holdout2/2u) and 0.017 s (mutants). Changing packing or the number of workers gains nothing — [lb.py](artifacts/external_methods/lb.py)

### Inferences
- On the sample, 74 s cannot be reached: the whole gap to the oracle is 2.7 s. A 74 s cut can only come from a holdout-like or mutant-like set. On those, the long-test and probe-failure buckets each exceed 74 s.
- Because scheduling is optimal, every remaining second comes from selection precision (or from CPU). The worker-count and packing trade-off is used up.

### Gaps
- I have no labelled hidden-set data, so I cannot say which set the board's hidden set resembles. The sample (30 patches) looks much "easier" than the generated holdouts.

## Q2. Which established techniques are missing here, and what does the literature report for them?

### Takeaway
Missing techniques that fit this repo's measured cost structure: (1) Ekstazi-style dynamic module (file) dependencies per test, used when an import fails; (2) a coverage-recording fix that Ekstazi/testmon-style tools treat as basic (do not charge import-time execution in spawned workers to the test); (3) differential "carving"/stage-checkpoint execution (Elbaum et al.) for the very long tests; (4) data/fixture-entry-level dependencies (finer than file granularity, in the spirit of HyRTS/FineRTS); (5) scope-aware name dependencies (NameRTS, 2026, Python-specific); (6) observed-type invariants for operator swaps. Predictive/ML selection (Facebook, mutation-trained models) and checked-coverage pruning are well documented but unsafe under a 300 s miss penalty.

### Cited Findings
- Ekstazi tracks, at run time, the files each test depends on. It needs no integration with version control, was evaluated on 615 revisions of 32 projects, and chose file granularity because "collecting only the classes is not safe" — [Gligoric et al., ISSTA 2015](https://dl.acm.org/doi/10.1145/2771783.2771784); [Gligoric thesis, RTS: Theory and Practice](https://core.ac.uk/download/pdf/158311081.pdf)
- Ekstazi# (.NET port) cut regression-testing time by 43.70% on 11 open-source projects and 65.26% on a Microsoft project — [Vasic et al., FSE 2017](https://dl.acm.org/doi/10.1145/3106237.3117763)
- STARTS (static, class-level, through a type-dependency graph's transitive closure) selects on average 35.2% of tests, with end-to-end time 81.0% of RetestAll. A follow-up shows that reflection breaks the safety of static RTS — [Legunsen et al., ASE 2017](https://www.cs.cornell.edu/~legunsen/pubs/LegunsenETAL17STARTS.pdf); [Shi et al., reflection-aware static RTS](https://mir.cs.illinois.edu/marinov/publications/ShiETAL19ReflectionAwareRTS.pdf)
- HyRTS combines method-level and file-level dependencies. Finer granularity gives more precision, coarser gives less overhead — [Zhang, ICSE 2018](https://lingming.cs.illinois.edu/publications/icse2018.pdf)
- FineRTS manually catalogued 29 kinds of change (13 findings, 11 of them semantics-modifying) that do not require re-running the tests they affect, added this reasoning to Ekstazi and STARTS, and evaluated it on 1,150 versions of 23 projects — [Liu et al., ISSTA 2023](https://doi.org/10.1145/3597926.3598086)
- Reks (refactoring-aware RTS) skips about 33% of the tests a refactoring-unaware technique would run, on 100 refactoring tasks from 37 projects. The authors use it only in pre-submit testing because of safety concerns — [Wang et al., ICSE 2018](https://kaiyuanw.github.io/papers/paper9-icse18.pdf)
- NameRTS (Python): a bipartite graph of code elements and names. A test is selected if a modified element is reachable from the names it uses, with no call graph. It skips 69.90% of test files, cuts end-to-end time by 45.59%, and is safe on 99.6% of commits, against 76.6% for the file-level BabelRTS — [arXiv 2605.25356 (ISSTA 2026)](https://arxiv.org/abs/2605.25356)
- Assertion-level (statement-level) selection inside test code cut test time by about 63% on 11 subjects, and was 7–38% faster than two state-of-the-art tools — [Gu & Mesbah, arXiv 2403.16001](https://arxiv.org/abs/2403.16001)
- pytest-testmon stores, per test, CRC checksums of the code blocks (functions/methods/classes) it executed, and re-runs a test when a block's checksum changes — [testmon.org](https://www.testmon.org/blog/determining-affected-tests/)
- Differential unit tests (DUTs): record the unit's inputs and outputs during a system test ("carving"), then replay the unit on a new version and compare — [Elbaum et al., FSE 2006 / TSE 2009](https://digitalcommons.unl.edu/cgi/viewcontent.cgi?article=1004&context=csearticles)
- Shadow symbolic execution runs the old and new versions together and produces tests where they diverge. Regression verification proves two close versions equivalent, an undecidable problem in general — [Imperial SRG, Shadow](https://srg.doc.ic.ac.uk/projects/shadow/); [Godlin & Strichman, STVR](https://ofers.dds.technion.ac.il/publications/stvr12.pdf)
- Facebook's predictive test selection halves testing infrastructure cost while still reporting more than 95% of individual test failures and more than 99.9% of faulty changes, so it misses up to ~5% of failures by design — [Machalica et al., ICSE-SEIP 2019](https://arxiv.org/abs/1810.05286)
- Combining ML models (trained on mutation-analysis data) with Ekstazi/STARTS selects 25.34% / 21.44% fewer tests than those tools alone, trading safety for precision — [Zhang et al., AST 2022](https://dl.acm.org/doi/10.1145/3524481.3527230)
- Google's TAP cannot regression-test each change individually at Google scale, which motivates batching and prediction — [Memon et al., ICSE-SEIP 2017](https://research.google.com/pubs/pub45861.html)
- Checked coverage counts only executed statements that influence a checked value (a dynamic slice to the oracle) — [Schuler & Zeller, STVR 2013](https://www.st.cs.uni-saarland.de/publications/files/schuler-stvr-2013.pdf). But 26.3% of PIT mutant kills were detected only through crashes, not assertion failures, and 18.2% of covered branch outcomes were "activated but undetected" — [Veritas, arXiv 2610.33708](https://arxiv.org/html/2610.33708)

### Inferences
- Test prioritisation with early stopping (from the Yoo & Harman survey family) does not apply: the selector emits one static schedule per commit and receives no outcomes, so there is nothing to stop on.
- Test-suite minimisation does not apply either: the score counts a miss when any failing test is left out, so "redundant" tests cannot be dropped.
- Checked-coverage pruning would be unsafe here for the same reason Veritas reports: crashes (exceptions) fail tests without passing through assertions.

### Gaps
- I could not read the full texts of FineRTS, HyRTS, Ekstazi, STARTS or Machalica et al. (ACM DL returned 403; the PDFs could not be rendered here). Numbers above come from abstracts, search-indexed summaries or author pages, as cited.
- The Yoo & Harman survey ([doi:10.1002/stvr.430](https://doi.org/10.1002/stvr.430)) was identified but not read.

## Q3. For each candidate: expected gain per set, miss risk, CPU cost, and fit with the container (stdlib, 2 CPUs, no network, 150 s, offline maps allowed)

### Takeaway
Two candidates were measured directly with 0 misses on all five labelled sets. **B. Child-process import-time exclusion** in the coverage map: −27.3 s on holdout1 and −98.4 s on mutants, 0 on the others, no runtime CPU. **C. Stage-checkpoint differential check for test_execute_tpcds**: the fingerprints were unchanged on 11 of 12 candidate patches, worth about 51/31/41 s gross on holdout1/holdout2u/mutants at about 3.5–6 s CPU per check. Three more have measured or label-derived upper bounds: **A. Ekstazi-style dynamic module dependencies** when the probe fails (≤68/37.5/70 s), **F1. scope-aware name dependencies** (h33758: −34.0 s on holdout2/2u), and **D. fixture-entry dependencies** (−15.3 s on holdout2/2u and mutants).

### Cited Findings
- **B. Do not charge import-time execution in spawned pool workers to the test (coverage-map fix; Ekstazi/testmon principle of per-test dependencies).**
  - How it works: build/covplugin.py prewarms (imports every sqlglot module before tracing) only in the pytest parent. In spawned ProcessPoolExecutor children, `_child_record` restarts monitoring per task, so lazily imported modules' def/class/module lines are recorded as that test's lines — [covplugin.py](https://github.com/Danixjg/blast-radius/blob/08aeb05/build/covplugin.py)
  - Measured evidence of contamination: 3,804 of test_execute_tpcds's 9,176 recorded sqlglot lines are import-time lines. All 54 recorded lines in generators/athena.py and all 39 in dialects/athena.py are import-time lines — [chk.py](artifacts/external_methods/chk.py)
  - Simulation: for the 227 tests in test_executor.py/test_optimizer.py, I cleared map bits on import-time lines outside function, lambda and comprehension bodies (6,760 line entries), then ran the full selector with verifier. Results: holdout1 618.608 → 591.357 (h12117 25.64→13.16, h12135 25.45→10.69); mutants 1065.357 → 966.918 (seven commits, e.g. m1x030/m1x046 25.45→6.79); sample, holdout2 and holdout2u unchanged; **0 misses on all sets** — [mkmapB.py + rerun](artifacts/external_methods/mkmapB.py)
  - Risk: low. The pool tests get the same treatment as every non-pool test already gets in the parent; import-time edits still go through the probe and the name/class rules. CPU: 0 at run time. The real fix is a one-line prewarm in `_child_setup` plus `make map`, and it must be re-validated because profile.pkl (dictionary reads in children) may have the same contamination.
- **A. Ekstazi-style dynamic module dependencies when the import probe fails.**
  - Today any probe failure runs all 1,620 tests (36.817 s). The probe already reports the failing module, e.g. `['sqlglot.dialects.teradata', "TypeError ..."]` — [replay log](artifacts/external_methods/replay.py)
  - Bound (oracle on probe-failed leaf modules): holdout1 −68.1, holdout2/2u −37.5, mutants −70.4, sample 0. Probe failures inside core modules such as dialect.py or datatypes.py already have oracle = everything (h12149, h12120) — [breakdown.json](artifacts/external_methods/breakdown.json)
  - A static substitute is unsafe (Ekstazi's argument reproduced). A static reverse-import closure plus the map's per-file coverage, instantiated classes and subprocess tests missed failing tests on 6 of 10 probe-failure commits. h12162 (snowflake) missed 729 of 1,088, because tests/dialects/test_dialect.py does `from sqlglot.dialects import Snowflake` through lazy `__getattr__` and every dialect test module imports Validator from it. Smaller misses included test_enum, test_generator_memory (it iterates all Dialects), test_to_char and test_dialect_imports — [expA2.py](artifacts/external_methods/expA2.py)
  - Required offline map: per test and per test-module collection, the set of sqlglot modules requested. Record it by wrapping builtins.__import__ and importlib.import_module (this also catches cached imports), in pool children too, and always keep subprocess tests. At run time, let the probe continue past the first failure and list every module that fails; select the tests that request any failing module. CPU: about 0 extra (the probe already runs). Risk: low to medium (order-independent if requests are recorded, not loads).
- **C. Stage-checkpoint differential execution for test_execute_tpcds (DUT/"carving" idea, Elbaum et al.).**
  - test_execute_tpcds runs `execute()` = `optimize(...)` → `Plan` → PythonExecutor in spawned workers and compares with DuckDB results on `transpile(sql, write="duckdb")` — [sqlglot tests/test_executor.py, sqlglot/executor/__init__.py at the pin](/home/danny/blast-radius/work/sqlglot/tests/test_executor.py)
  - Experiment: fingerprint, for the 57 executed TPC-DS queries, sha256(optimized SQL + repr + every node's type + plan steps). Pristine runs are deterministic (2 runs, 0 differences). On 12 candidate patches where tpcds was selected without failing, 11 gave **identical** fingerprints (h12100, h12156, h12158, h12134, h33735, h33748, m2x002, m2x019, m2x022, m2x014, m1x065). h33746 (is_number or→and) changed all 57 and would keep the test. CPU per patched check 3.5–6.2 s (host, includes ~0.3 s import) — [stage.py](artifacts/external_methods/stage.py)
  - Gross saving from dropping tpcds where the check passes: holdout1 51.3 s (h12100 15.2, h12156 18.6, h12158 17.5); holdout2/2u 30.6 (h33735 13.4, h33748 17.2); mutants 41.2 (m2x002 15.2, m2x019 12.9, m2x022 13.0). Net of about 4.5 s CPU per check: about 38 / 21 / 28 s — [dropsave.py](artifacts/external_methods/dropsave.py)
  - Safety needs two more pieces: (i) offline stage-split coverage, so that a patched line executed during planning or execution (executor/*, generators/python.py, Table loading) keeps the test; (ii) the DuckDB transpile string in the fingerprint, which my experiment did not include, so m2x014 (duckdb generator) and h12134 do not count yet. The fixtures and the TPC-DS schema must be shipped inside map.pkl (offline build is allowed). Residual risk: global state mutated during optimize that only matters later.
- **D. Fixture-entry-level dependency (finer than Ekstazi's file granularity).**
  - test_execute_tpcds reads `_, sql, _` from each tpc-ds.sql pair and never uses the "expected optimized" half. h33743 and m1x000 edit only expected-optimized text, yet select tpcds because the test opened the file — [patches + tests/helpers.py load_sql_fixture_pairs](/home/danny/blast-radius/work/sqlglot/tests/helpers.py)
  - Gain: −15.3 s on holdout2/2u (h33743 25.45→10.12) and −15.3 s on mutants (m1x000). CPU about 0 (re-split both fixture versions with the same ";" logic and compare the (meta, sql) lists) — [dropsave.py](artifacts/external_methods/dropsave.py)
  - Risk: low if limited to tests whose use of each tuple field is recorded or hand-specified, and if the whole list (count, meta, sql) must match.
- **F1. Scope-aware name dependencies (NameRTS-style) for removed or changed module-level imports.**
  - h33758 removes `from functools import reduce` in generators/tsql.py and the current selector picks 1,592 tests (36.8 s). Only lines 3 and 715 of that module read `reduce`. Selecting the tests that ran line 715 gives 4 tests, all 3 failing tests included, cost 2.813 s: **−34.0 s on holdout2 and holdout2u** — [h33758.py](artifacts/external_methods/h33758.py)
  - Must also keep: static `from <module> import <name>` importers, `getattr`/`globals()` users, star-importers of the module and `__all__`. CPU about 0. Risk: low to medium.
- **F2. Observed-type invariants (Daikon-style) for `is`↔`==` swaps without profile evidence.** h33727 (`escapes is None`→`== None` in tokenizer_core) currently runs everything for 0 failures: bound −36.8 s on holdout2/2u. Overlaps with plan Step 3 — [breakdown.json](artifacts/external_methods/breakdown.json); [next-steps-plan.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/docs/next-steps-plan.md)
- **E. Sub-test (per-dialect) verification of test_generator_memory (assertion/subtest-level granularity, cf. Gu & Mesbah).** The test loops over all Dialects. A class-body edit in one dialect only affects that subTest, so the verifier could run that one subTest (about 1/35 of 6.7 s) when every other selected test also passes. Bound for commits labelled with zero failures: holdout1 −8.5, holdout2u −15.6, mutants −34.3 s. Risk: medium. The labels show this test failing as an xdist/tracemalloc artifact "whenever many tests fail", and that cannot be reproduced locally — [dropsave.py](artifacts/external_methods/dropsave.py); [README.md verifier notes](https://github.com/Danixjg/blast-radius/blob/08aeb05/README.md)
- **Rejected: predictive/ML selection (Machalica; mutation-trained models).** Dropping tpcds saves about 23 s and costs 300 s × p(failure), so it only breaks even below p ≈ 7.7%. The measured failure rate of tpcds when it is selected is 6/16 (holdout1), 4/11 (holdout2), 6/25 (mutants), i.e. 24–38% — [bounds.py](artifacts/external_methods/bounds.py); [Machalica et al.](https://arxiv.org/abs/1810.05286)
- **Rejected: test_max_nodes shortcuts.** The test parses a 10,000 × 100 expression and asserts a ParseError message. It failed in 10 of 66 holdout1 commits, there is no cheap equivalent run, and checked-coverage pruning is unsafe because failures arrive as exceptions — [sqlglot tests/test_parser.py](/home/danny/blast-radius/work/sqlglot/tests/test_parser.py); [Veritas](https://arxiv.org/html/2610.33708)

### Inferences
- B, D and F1 cost no runtime CPU and are the cheapest safe wins. A also costs about 0 CPU but needs a new offline map. C is the only candidate that spends real CPU (about 4–6 s per check on 2 CPUs), so it needs a gate: run it only when tpcds is in the selection and the patched lines are outside the execution stage.
- The gains overlap only a little. B changed h12117/h12135 and seven mutants; C, D and F1 act on other commits (C's candidates were chosen from B's output).

### Gaps
- B was simulated by editing map bits, not by rebuilding map.pkl/profile.pkl with a child prewarm. The profile may hold the same contamination and give more gains (unmeasured).
- C's safety argument depends on stage-split coverage, which I did not build. The DuckDB transpile string was not fingerprinted.
- A's real selection could not be measured without recording module requests over the full suite (I did not run the suite, to avoid disturbing other researchers' CPU measurements).
- None of these was run in Docker. The CPU numbers are from the host.

## Q4. Ranked shortlist of experiments and where a 74 s cut is realistic

### Takeaway
A 74 s cut is realistic on **mutants** (B alone: −98.4 s measured, 0 misses) and on **holdout1** (B −27.3 measured + C about −38 net, plus A up to −68.1). On **holdout2/holdout2u** it needs three or more methods combined (F1 −34.0 + A ≤−37.5 + D −15.3 + C about −21 net, F2 ≤−36.8). It is **impossible on the public sample**, whose whole gap to the oracle is 2.7 s.

### Cited Findings
- Per-set gains in seconds (measured = full selector rerun or exact rescoring; bound = label-derived upper limit):

| # | Experiment | sample | holdout1 | holdout2 / 2u | mutants | runtime CPU | miss risk |
|---|---|---:|---:|---:|---:|---|---|
| 1 | B child-import exclusion in map (rebuild with prewarm in pool children) | 0 (measured) | −27.3 (measured) | 0 (measured) | −98.4 (measured) | 0 | low |
| 2 | F1 scope-aware name deps for import edits | 0 | 0 (not seen) | −34.0 (measured selection) | 0 (not seen) | ~0 | low–med |
| 3 | A dynamic module-request deps on probe failure | 0 | ≤−68.1 (bound) | ≤−37.5 (bound) | ≤−70.4 (bound) | ~0 | low–med |
| 4 | D fixture-entry deps (tpc-ds expected-only edits) | 0 | 0 | −15.3 | −15.3 | ~0 | low |
| 5 | C tpcds stage-checkpoint fingerprint | 0 | −51.3 gross / ~−38 net | −30.6 gross / ~−21 net | −41.2 gross / ~−28 net | 3.5–6.2 s per check | med (needs stage-split cov.) |
| 6 | F2 type-invariant `is`/`==` | 0 | — | ≤−36.8 (bound) | — | 0 | med |
| 7 | E per-dialect genmem subtest verify | 0 | ≤−8.5 | ≤−15.6 | ≤−34.3 | ~0.3–0.5 s per check | med |

  Sources: [breakdown.json, mkmapB.py, stage.py, dropsave.py, h33758.py](artifacts/external_methods/)
- Baselines these gains apply to (current main, Docker): sample 97.076, holdout1 618.608, holdout2 545.397, holdout2u 534.169, mutants 1065.357 (all 0 misses). My host reruns reproduce the simulated parts exactly — [task context; breakdown.json](artifacts/external_methods/breakdown.json)

### Inferences
- Recommended order: (1) B, a one-line covplugin change plus a map/profile rebuild, gated by validate on every set and a fresh holdout; (2) F1 and D, static and with no CPU; (3) A, a new offline map plus a probe that lists all failing modules; (4) C, only after stage-split coverage exists and with a cost gate; (5) F2/E as investigations.
- If B, F1, D and A behave as measured or bounded, holdout2u would drop by roughly 34 + 15 + up to 37 ≈ 70–87 s, and holdout1 by roughly 27 + up to 68 (+38 with C), with no added CPU except C.
- Follow the repo's gate: freeze each rule, then score a fresh-seed holdout, because B's and C's evidence comes from the same labelled sets.

### Gaps
- Combined effects were not simulated jointly (except B on its own). The figures are additive estimates over mostly disjoint commits.
- No evidence on how closely the board's hidden set resembles the generated holdouts or the sample.

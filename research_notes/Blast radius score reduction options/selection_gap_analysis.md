# Selection gap analysis: where current main (08aeb05) over-selects, per commit and per rule

All numbers below come from in-process replays of current main's selector made for this note. The replay is the same flow as `selector.main()`: analyze, import probes, lazy-load check, verifier. It runs with an instrumented `Hunter` that records which method produced each selected test. Script: [driver.py](artifacts/selection-gap/driver.py). Per-commit dumps: [*.json](artifacts/selection-gap/). Attribution: [analyze.py](artifacts/selection-gap/analyze.py) gives [rows.json](artifacts/selection-gap/rows.json). The replay matches the Docker numbers for br-eval:pr7 exactly on all five sets: sample 97.076, holdout1 618.608, holdout2 545.397, holdout2u 534.169, mutants 1065.357, with 0 misses. Oracle = only the failing tests, packed with the selector's own `schedule()` (max load + 2 s per worker). Source code references are to [selector.py](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py) at 08aeb05.

## Q1. Oracle cost per set and the split of the gap into harmless / breaking / full runs

### Takeaway
The gap to the oracle is about 2.7 s on the sample, about 309 to 311 s on each holdout and 738 s on mutants. A 74 s cut is impossible on the sample, because the whole gap there is 2.725 s. It is realistic on holdout2/holdout2u, and plausible on mutants. On holdout1 the safe options measured here reach only about 15 s.

### Cited Findings
- Per-set gap from the replay (simulated − oracle). Columns: (a) harmless commits (0 failing tests) that still select tests, (b) breaking commits that over-select, (c) full runs (the catcher selects all 1,620 tests, 36.817 s) — [rows.json](artifacts/selection-gap/rows.json), [analyze.py](artifacts/selection-gap/analyze.py):

| set | simulated | oracle | gap | (a) harmless: n / s | (b) breaking: n / s | (c) full runs: n / s |
|---|---:|---:|---:|---|---|---|
| sample (30) | 97.076 | 94.351 | 2.725 | 12 / 2.466 | 18 / 0.259 | 0 / 0 |
| holdout1 (66) | 618.608 | 309.807 | 308.801 | 24 / 93.235 | 35 / 147.508 | 7 / 68.058 |
| holdout2 (65) | 545.397 | 234.161 | 311.236 | 26 / 64.981 | 35 / 171.944 | 4 / 74.311 |
| holdout2u (61) | 534.169 | 222.933 | 311.236 | 24 / 64.981 | 33 / 171.944 | 4 / 74.311 |
| mutants (100) | 1065.357 | 326.938 | 738.419 | 30 / 335.264 | 67 / 332.742 | 3 / 70.413 |

- Holdout2u's gap is identical to holdout2's (311.236 s). The 4 commits that holdout2 has and holdout2u lacks all have a gap of 0 — [rows.json](artifacts/selection-gap/rows.json).
- Full runs come from two causes. One is an import probe that returns "broken": holdout1 4 commits / 68.058 s, holdout2 2 / 37.494 s, mutants 3 / 70.413 s. The other is the hunter returning ALL: holdout1 3 / 0 s, because those commits really fail all tests; holdout2 2 / 36.817 s, all of it from h33727 — [rows.json](artifacts/selection-gap/rows.json).
- The full-run commits with a probe failure fail fewer tests than the full suite. h12112 (a missing import added to generators/singlestore.py) fails 37 tests, oracle 3.36 s. h33712 (generators/oracle.py) fails 137, oracle 10.69 s. h12162 fails 1,088 tests and h12151 fails 61 — [base2 dumps](artifacts/selection-gap/holdout1.base2.json).
- The sample's whole gap sits in 3 harmless commits that keep 2.466 s in total (func_mask), plus 0.259 s from one breaking commit (resolve_stmt). All of them fall into the "verifier-eligible patch, but the selection contains tests the verifier cannot run" bucket (2 commits / 2.73 s) — [vchk.py](artifacts/selection-gap/vchk.py).

### Inferences
- Simulated time on the sample can drop by at most 2.725 s. Any 74 s target has to be judged on holdout2/holdout2u (311 s gap) or mutants (738 s gap), not on the board's public sample.
- On the holdouts, over-selection on breaking commits (b) is the largest bucket: 148 to 172 s per set. (a) and (c) are each worth 65 to 93 s.

### Gaps
- Docker CPU was not re-measured, as the task instructed. The CPU for the verifier experiment below is an estimate: 0.35 s start-up plus the tests' recorded seconds, the same model as `VERIFY_START_MS`.

## Q2. Which selector paths and patch categories account for the most seconds

### Takeaway
Three slow tests are responsible for a large share of the gap when they are selected but do not fail: `test_execute_tpcds` (21.45 s), `test_max_nodes` (28.8 s) and `test_generator_memory` (6.7 s). They pull in 141.6 s of holdout1's gap, 130.4 s of holdout2's and 356.7 s of the mutants'. By selector path, the main sources are the import-time class-attribute path (`resolve_stmt`, then `resolve_name`, then `inst_mask`/`group_mask`/`fallback`) and line coverage in `func_mask`. Probe-broken full runs come third.

### Cited Findings
- Gap by patch category (holdout meta.json "category"; answers "kind" for the other sets). The largest buckets on holdout1: c_full:import 68.06, a_harmless:classbody 58.79, b_breaking:code 45.90, b_breaking:classbody 42.50, b_breaking:multi 30.91, a_harmless:dictvalue 25.64, b_breaking:helper 14.88, b_breaking:dictvalue 13.32. On holdout2: b_breaking:code 48.04, b_breaking:import 45.96, c_full:import 37.49, c_full:eq 36.82, b_breaking:dictvalue 30.97, a_harmless:code 25.45, a_harmless:classbody 22.19, b_breaking:fixture 21.22, b_breaking:classbody 15.97. Zero-gap categories on every holdout: comment, cookie, setup, fixture-only (mostly), harmless collection/conftest, harmless import — [analyze.py output](artifacts/selection-gap/rows.json).
- Gap by dominant path (the chain whose removal saves the most, from instrumented Hunter call trees):
  - holdout1: resolve_stmt chains 120.25 s (breaking 86.72 + harmless 33.53), func_mask 111.69 s (breaking 60.78 + harmless 50.91), probe-broken full runs 68.06 s, analyze_other (fixture whitespace) 4.11 s, collection (renamed test module, `importers_of_test_module`) 2.69 s, eq_swap_mask 2.01 s.
  - holdout2: resolve_stmt 124.34 s (includes resolve_name 11.36), func_mask 75.60 s, probe broken 37.49 s, hunter ALL 36.82 s, analyze_other 32.95 s (fixture 28.90 + whitespace 4.04), eq_swap_mask 4.05 s.
  - mutants: resolve_stmt 499.8 s across its sub-chains, func_mask 146.5 s, probe broken 70.41 s, analyze_other 21.73 s.
  - Source: [rows.json](artifacts/selection-gap/rows.json).
- Heavy tests (catalog seconds): 1490 `test_parser.py::TestParser::test_max_nodes` 28.817 s, 1106 `test_executor.py::TestExecutor::test_execute_tpcds` 21.454 s, 1224 `test_generator.py::TestGenerator::test_generator_memory` 6.686 s. The next one, test_execute_tpch, takes 3.863 s — [heavy.py](artifacts/selection-gap/heavy.py), [tests.json](file:///home/danny/blast-radius/work/holdout1/data/tests.json).
- Simulated seconds saved by removing these tests from selections where they do not fail (an oracle-knowledge upper bound):

| set | test_execute_tpcds | test_max_nodes | test_generator_memory |
|---|---|---|---|
| holdout1 | 127.67 s (8 commits) | — | 13.90 s (4) |
| holdout2 | 45.89 s (3) | 43.89 s (3) | 40.65 s (9) |
| mutants | 187.75 s (12) | 122.30 s (7) | 46.61 s (8) |

  Source: [heavy.py](artifacts/selection-gap/heavy.py).
- How often each heavy test fails when selected (non-full commits):
  - test_execute_tpcds really fails in some of them: 1 of 9 on holdout1, 2 of 7 on holdout2u, 5 of 22 on mutants, 2 of 2 on the sample. Dropping it is unsafe.
  - test_max_nodes fails in 0 of 10. Its only failing labels are commits where 1,600 or more tests fail.
  - test_generator_memory fails in 0 of 33 selections. It does appear in labels of commits with 61 or more failures (h12151, m2x018, h33712 and larger), which matches RESULTS.md's note that it fails under xdist whenever many tests fail.
  - Sources: [fl.py](artifacts/selection-gap/fl.py), [heavy2.py](artifacts/selection-gap/heavy2.py), [RESULTS.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md).
- `test_generator_memory` is selected almost entirely through `inst_mask`/`group_mask`: the "tests that instantiated this class or its dialect family" rule, reached when `resolve_stmt` treats a class-attribute name as read at import time (`unknown`). It comes through span or file coverage in only 3 cases. The test only calls `Dialect.get_or_raise(name).generator()` 1,100 times per dialect and asserts that tracemalloc growth stays under 1,024 bytes — [heavy3.py](artifacts/selection-gap/heavy3.py), [selector.py:843-863](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py), [tests/test_generator.py:19-38](https://github.com/tobymao/sqlglot/blob/eb11aec4be8cb2122afe713a4816662c3f0fb2fa/tests/test_generator.py).
- `test_execute_tpcds` is mostly selected through plain line coverage (`func_mask > span_mask`, 11 of 29 non-failing selections), so the edited line is one it really runs. It is in `VERIFY_UNSAFE_FILES` (duckdb/pandas, process pools), so the verifier never touches it — [heavy3.py](artifacts/selection-gap/heavy3.py), [selector.py:65-70](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py).
- The verifier decided nothing on any holdout commit with a gap of 1.5 s or more. Of the commits with a positive gap that edit only existing sqlglot modules, the selections contain at least one test the verifier cannot run in 13 on holdout1 (196.78 s of gap), 17 on holdout2 (201.87 s) and 47 on mutants (646.28 s). Only 1 commit (holdout1, 1.02 s) was skipped purely by the cost policy — [vchk.py](artifacts/selection-gap/vchk.py).

### Inferences
- Most of the remaining gap is not a broad rule selecting hundreds of cheap tests: the scheduler packs those onto 8 workers almost for free. It comes from one slow test landing in a selection. A commit's cost is at least the slowest selected test + 2 s, so including tpcds puts it at 23.45 s or more and including max_nodes at 30.8 s or more. Rules that keep these three tests out are worth far more than rules that trim cheap tests.

### Gaps
- The attribution counterfactual (removing one chain while keeping the others) gives 0 for most resolve_stmt sub-chains, because they overlap. The "dominant path" therefore uses the chain with the largest saving, and the seconds are per commit, not additive per rule.

## Q3. Top-N commits, safe narrower rules, and the 300 s miss risk

### Takeaway
The top 10 commits hold 238 of 309 s (holdout1), 228 of 311 s (holdout2/2u) and 325 of 738 s (mutants). Three re-entry points that are not in the repository combine to −74.7 s on holdout2 and holdout2u with 0 misses on all five sets. The same combination gives −54.5 on mutants, −15.2 on holdout1 and 0 on the sample.

### Cited Findings
Top 10 gap commits per set (from [rows.json](artifacts/selection-gap/rows.json)):

holdout1:
| commit | class | category/kind | gap s | oracle s | fail/selected | dominant selector path (or full-run reason) | heavy non-failing tests selected | patch note |
|---|---|---|---:|---:|---|---|---|---|
| h12112 | full | import | 33.46 | 3.36 | 37/1620 | probe broken | - | import of a missing module |
| h12117 | harmless | dictvalue | 25.64 | 0.00 | 0/357 | analyze_py > resolve_stmt | tpcds+gen_memory | dict value line 69: "%Istrict" -> '%Istrict_' |
| h12100 | harmless | classbody | 25.45 | 0.00 | 0/104 | analyze_py > func_mask | tpcds | swap 'True'->'False' line 1083 |
| h12135 | harmless | classbody | 25.45 | 0.00 | 0/27 | analyze_py > func_mask | tpcds+gen_memory | num '2'->'3' line 143 |
| h12134 | breaking | classbody | 23.45 | 2.00 | 1/146 | analyze_py > resolve_stmt | tpcds+gen_memory | swap 'True'->'False' line 39 |
| h12162 | full | import | 23.24 | 13.58 | 1088/1620 | probe broken | - | removed import line 16 |
| h12156 | breaking | code | 23.06 | 2.40 | 1/9 | analyze_py > func_mask | tpcds | swap 'is'->'is not' line 386 |
| h12158 | breaking | code | 21.83 | 3.63 | 4/21 | analyze_py > func_mask | tpcds | swap 'and'->'or' line 946 |
| h12153 | breaking | multi | 21.27 | 6.18 | 3/492 | analyze_py > resolve_stmt | tpcds+gen_memory | dict value line 108: TokenType.DSTAR -> TokenType.TIMESTAMP_ |
| h12146 | breaking | helper | 14.88 | 10.57 | 26/85 | analyze_py > func_mask | tpcds | num '2'->'3' line 50 |

holdout2 (holdout2u has the same top 10):
| commit | class | category/kind | gap s | oracle s | fail/selected | dominant selector path (or full-run reason) | heavy non-failing tests selected | patch note |
|---|---|---|---:|---:|---|---|---|---|
| h33727 | full | eq | 36.82 | 0.00 | 0/1620 | hunter ALL | - | is->== line 1321 |
| h33758 | breaking | import | 34.60 | 2.22 | 3/1592 | analyze_py > resolve_stmt | max_nodes+tpcds+gen_memory | removed import line 3 |
| h33712 | full | import | 26.13 | 10.69 | 137/1620 | probe broken | - | import of a missing module |
| h33746 | breaking | code | 26.07 | 10.74 | 160/1567 | analyze_py > func_mask | max_nodes+tpcds | swap 'or'->'and' line 928 |
| h33748 | harmless | code | 25.45 | 0.00 | 0/53 | analyze_py > func_mask | tpcds | swap '-'->'+' line 376 |
| h33735 | breaking | code | 21.95 | 3.50 | 6/228 | analyze_py > func_mask | tpcds | swap 'True'->'False' line 81 |
| h33743 | breaking | fixture | 21.22 | 4.23 | 1/61 | analyze_other | tpcds | inserted token line 2958 |
| h33759 | harmless | classbody | 12.90 | 0.00 | 0/320 | analyze_py > resolve_stmt | gen_memory | str '"FMDDD"'->'"FMDDX"' line 46 |
| h33740 | breaking | import | 11.36 | 25.45 | 37/1545 | analyze_py > resolve_stmt > resolve_name | max_nodes+gen_memory | removed import line 5 |
| h33744 | full | import | 11.36 | 25.45 | 60/1620 | probe broken | - | removed import line 5 |

mutants:
| commit | class | category/kind | gap s | oracle s | fail/selected | dominant selector path (or full-run reason) | heavy non-failing tests selected | patch note |
|---|---|---|---:|---:|---|---|---|---|
| m1x055 | harmless | mutant | 36.82 | 0.00 | 0/1586 | analyze_py > func_mask > fallback | max_nodes+tpcds+gen_memory |  |
| m2x005 | harmless | mutant | 36.82 | 0.00 | 0/1568 | analyze_py > (resolve_stmt > resolve_name)* > resolve_stmt > fallback | max_nodes+tpcds |  |
| m2x025 | harmless | mutant | 36.82 | 0.00 | 0/1615 | analyze_py > (resolve_stmt > resolve_name)* > resolve_stmt > resolve_name | max_nodes+tpcds+gen_memory |  |
| m2x029 | full | mutant | 34.81 | 2.01 | 29/1620 | probe broken | - |  |
| m1x064 | breaking | mutant | 33.95 | 2.06 | 1/762 | analyze_py > resolve_stmt | max_nodes+tpcds |  |
| m2x008 | breaking | mutant | 33.95 | 2.06 | 1/762 | analyze_py > resolve_stmt | max_nodes+tpcds |  |
| m2x000 | harmless | mutant | 32.82 | 0.00 | 0/153 | analyze_py > resolve_stmt > resolve_name | max_nodes |  |
| m2x019 | harmless | mutant | 27.12 | 0.00 | 0/312 | analyze_py > func_mask | tpcds |  |
| m2x018 | full | mutant | 26.24 | 10.58 | 76/1620 | probe broken | - |  |
| m1x030 | harmless | mutant | 25.45 | 0.00 | 0/12 | analyze_py > resolve_stmt | tpcds |  |

Measured re-entry points (replays of the full selector flow with one change each; misses counted against every labelled set):

1. **`is`/`==` swaps compared with None in the profiler (h33727, 36.817 s on holdout2 and holdout2u).**
   - The patch is `escapes is None` → `escapes == None` at tokenizer_core.py:1321. The hunter returns ALL because the profile's import-time fact for that site is `(False, None, False)`. `eq` is None because `set.__eq__` is not in `_PURE_EQ`, and `eq_swap_mask` then returns ALL ("an evaluation while modules import would change") — [cmpchk.py](artifacts/selection-gap/cmpchk.py), [selector.py:1213-1218](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py), [build/brprofile.py:371-426](https://github.com/Danixjg/blast-radius/blob/08aeb05/build/brprofile.py).
   - `escapes` is `set[str]`. The 754 tests that ran the line all have that same fact; 9 tests have `(True, True, True)` — [cmpchk.py](artifacts/selection-gap/cmpchk.py), [tokenizer_core.py:601-635](https://github.com/tobymao/sqlglot/blob/eb11aec4be8cb2122afe713a4816662c3f0fb2fa/sqlglot/tokenizer_core.py).
   - Replacing the fact with `(False, False, False)` in memory makes h33727 select 0 tests (saving 36.817 s), with no probe needed — [eqsim.py](artifacts/selection-gap/eqsim.py).
   - Proposed rule, build time only: in `brprofile.eq_pure`, also treat as pure the case where one operand is `None` and the other's exact type is list, tuple, dict, set or frozenset. Their `__eq__` returns NotImplemented for a non-container right operand without visiting elements, and the comparison then falls back to identity.
   - Risk: low. The change only adds facts where equality equals identity, and the selector still decides per edit. The profile must be rebuilt, and the parity tests in build/test_profiler.py must be re-run.
2. **Narrowing `inst_mask` for import-time class-attribute edits whose edited statement contains no call or lambda.**
   - In `resolve_stmt`, when a name is `unknown` (read while importing) and the edit is in a class body, today's code adds every test that instantiated the class or its dialect family (`inst`). The experiment uses `s1 & inst` instead (readers of the name that also instantiated the class), at every derivation depth, but only when the edited top-level statement has no `Call`/`Lambda` node.
   - Results with 0 misses: holdout1 618.608 → 606.761 (−11.85), holdout2 545.397 → 514.213 (−31.18), holdout2u 534.169 → 502.985 (−31.18), mutants 1065.357 → 1010.816 (−54.54), sample unchanged — [driver7.py](artifacts/selection-gap/driver7.py).
   - Without the call guard: holdout2 −40.48, mutants −57.42, but holdout1 gets **1 miss** (h12134: `explode_projection_to_unnest(1, unnest_map=True→False)` in TRANSFORMS, missed test 807; holdout1 rises to 902.419) — [driver6.py](artifacts/selection-gap/driver6.py).
   - With the call guard restricted to depth 0 only: holdout1 −7.09, holdout2 −3.84, mutants 0 — [driver5.py](artifacts/selection-gap/driver5.py).
   - Risk: medium. The guard was shaped after seeing one miss, so it is in-sample. It needs a fresh-seed holdout, as docs/next-steps-plan.md's gate requires.
3. **Leaving out `test_generator_memory` when only inst/name chains select it (not span/file coverage) and the selection has at most 500 tests.**
   - Measured as a post-process on main: holdout1 −14.09 (7 commits), holdout2/2u −33.97 (9), mutants −46.62 (11), 0 misses. Every affected commit fails at most 37 tests — [pp.py](artifacts/selection-gap/pp.py), [pp2.py](artifacts/selection-gap/pp2.py).
   - On top of re-entry 2 it adds only −3.34 (holdout1) and −6.69 (holdout2) — [combo.py](artifacts/selection-gap/combo.py).
   - Risk: medium. The labels show the test fails "whenever many tests fail" (61+), so a breaking commit with many failures could carry it in its official label. The ≤500-test guard is a proxy for that.
4. **Combination of 1 + 2 + 3** — [combo.py](artifacts/selection-gap/combo.py):

| set | base | combined | delta |
|---|---:|---:|---:|
| sample | 97.076 | 97.076 | 0.000 |
| holdout1 | 618.608 | 603.418 | −15.190 |
| holdout2 | 545.397 | 470.710 | −74.687 |
| holdout2u | 534.169 | 459.482 | −74.687 |
| mutants | 1065.357 | 1010.812 | −54.545 |

   All five sets have 0 misses.
5. **Narrowing probe-broken full runs (not safe as tried).**
   - The tried rule replaces "run everything" with file_mask ∪ group_mask ∪ subprocess tests of the patched module. Coverage held on only 2 of 9 probe-failure commits: h33712 (saves 26.1 s) and m2x029.
   - It missed 1 test on h12112 (`tests/dialects/test_dialect.py::TestDialect::test_enum`, which loads every dialect), 1 on m2x018, and 7 on h12151 and h33744 each (test_executor tests), with many more missed on the commits where most of the suite fails.
   - Sources: [driver2.py](artifacts/selection-gap/driver2.py), [probe_alt.py](artifacts/selection-gap/probe_alt.py).
   - A viable version would also need every test that enumerates all dialects, plus the importers of the failing module. The ceiling is 68 s (holdout1), 37.5 s (holdout2) and 70.4 s (mutants), but only part of it is reachable (h12162 and h12149 fail more than 1,000 tests).
6. **Removing `group_mask` entirely (rejected as low value).** Holdout1 −1.13, holdout2 −4.06, mutants −1.83, 0 misses — [driver2.py EXP=nogroup](artifacts/selection-gap/driver2.py).
7. **Partial verification: verify the verifiable subset and keep the unverifiable tests (rejected, net negative).**
   - With a 1.5 s cap: simulated saving 0 to 4.1 s per set against an estimated 2.5 to 18.8 s of CPU (net −2.5 to −14.7).
   - With a 15 s cap: net −24 to −69 s — [partial.py](artifacts/selection-gap/partial.py).
   - The heavy unverifiable tests keep each commit's cost floor in place, so dropping cheap tests saves almost nothing.

### Inferences
- The largest single items that remain, each 21 to 37 s, are of three kinds:
  - harmless edits on lines that test_execute_tpcds really runs (h12100, h12135, h33748, m1x030, m1x046: about 25.45 s each);
  - import-time fallbacks in core modules (h33746 core.py, h33758 removed import, m1x055, m2x005, m2x025: 26 to 37 s);
  - probe-broken full runs.
  Line coverage and the probe cannot narrow these. Narrowing them needs value-level evidence, such as a build-time (uncharged) mutation-sensitivity record for the three heavy tests. That idea is untested.

### Gaps
- Re-entry 2's guard and re-entry 3's ≤500 threshold were chosen on the same labelled data that judges them. No fresh holdout was generated or labelled in this task, so the 0-miss result is in-sample.
- Re-entry 1 was simulated by editing one profile fact in memory. Rebuilding the whole profile with the brprofile change may add facts at other sites, which could only lower selections further. That was not measured.

## Q4. Ideas in docs/next-steps-plan.md and RESULTS.md: done vs open

### Takeaway
Steps 1, 2, the short-circuit part of 3, and 4.1/4.2 are already in main. Step 3's "extend profile coverage" and Step 4 option 3 are still open. The three measured re-entry points above appear in neither document.

### Cited Findings
- Step 1 (unread new module-level imports, about 63 s/set) is done: RESULTS.md lists "`import <start-up module> as <fresh name>` select nothing", and the selector has `unread_cached_import` and `unread_new_function`. In this replay, harmless "import" commits on holdout1 cost 0 s — [RESULTS.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md), [selector.py:1124-1169](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py), [rows.json](artifacts/selection-gap/rows.json).
- Step 2 (new `conftest.py`, about 69 s/set) is done through `fixture_only_conftest`. Harmless "collection" commits cost 0 s on holdout2/2u — [selector.py:1480,1673-1675](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py), [rows.json](artifacts/selection-gap/rows.json).
- Step 3 (is/== swaps) is partly done. The short-circuit rule was added (holdout1 654.997 → 618.608) and validated on 24 structural swap sites. Not done: "extending the profile's coverage" for sites without evidence. Re-entry 1 above is one concrete case of that (`eq` None for container operands) — [RESULTS.md "Short-circuited is/== swaps"](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md), [docs/next-steps-plan.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/docs/next-steps-plan.md).
- Step 4 (probe CPU): 4.1 and a safe 4.2 are done. "Option 3 below is not started" (import only the reverse import closure). Skipping the interpreter teardown of the probe child was explicitly not taken — [docs/next-steps-plan.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/docs/next-steps-plan.md).
- The verifier is done but never verifies `test_generator_memory` or VERIFY_UNSAFE_FILES — [RESULTS.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/RESULTS.md), [selector.py:62-77](https://github.com/Danixjg/blast-radius/blob/08aeb05/selector.py).
- Deferred in the plan: the Rust port and the worker/grouping trials — [docs/next-steps-plan.md](https://github.com/Danixjg/blast-radius/blob/08aeb05/docs/next-steps-plan.md).

### Inferences
- The plan's three big precision items (63, 69 and 43 s) are spent. What remains is concentrated in the heavy-test and import-time class-attribute paths measured above.

### Gaps
- validation 3, feature, fresh and the hand-built demo sets that RESULTS.md cites were not replayed here (outside the assigned sets). Their 0-miss status under re-entries 2 and 3 is unknown.

# Results

Every number below was measured on **Windows 11** (no Docker or Linux runner was available; see
"Not verified"). Runs use `build/bench.py`/`build/compare.py`: the selector runs from an `/app`
laid out like the image (for rev2/rev3 the `/app` produced by running the Dockerfile's own
COPY/RUN chain, `build/emulate_build.py --worktree`), in a fresh empty temp dir with a clean
environment, three runs per (version, set). Each run's stdout is scored by the kit's own
`score.py`. CPU is user+system time of the whole process tree (Job Object accounting, including
exited probe processes). "Total" is simulated + CPU of the middle run, as the board computes it.

## Versions

### Comparison with merged PR #3

Three fresh Windows runs per version used the official scorer and included whole-process-tree CPU.
The integrated selector produced byte-identical schedules to rev3 with the plain map on all six
benchmark sets. Totals below are medians in seconds.

| Set | Merged main (`421acf5`) | This PR | Missed commits (main → this PR) |
| --- | ---: | ---: | ---: |
| Official sample (30) | 121.4 | 119.7 | 0 → 0 |
| Holdout 1 (66) | 2642.0 | 784.4 | 7 → 0 |
| Holdout 2 unique (61) | 799.7 | 702.1 | 1 → 0 |
| Targeted validation (27) | 3640.6 | 871.7 | 12 → 0 |

Windows skipped the original import probes on main because symlink creation required additional
privileges. The baseline with working, copy-based probes is included below. Rev3 was revised
after examining a miss in holdout 2. One patch with an incomplete label was excluded.

### Earlier versions

| name | what |
| --- | --- |
| baseline | `3fd38f7` as shipped. On Windows its import probe never runs (`os.symlink` needs privileges). |
| baseline+probes | `3fd38f7` with the symlink overlay replaced by copies (measurement only): its behaviour where probes work, as on Linux. Copying inflates its Windows CPU. |
| correctness | `c8a81ea` selector without a profile: recall/deadline fixes; `is`/`==` always on line coverage; no dictionary-entry coverage. |
| frozen | `c8a81ea` (frozen candidate `37b8897` + Dockerfile fix; selector and maps byte-identical). |
| rev2 | profiler regressions fixed, version-2 profile rebuilt; frozen 21:48 (SHA-256 recorded) before any holdout-2 score was looked at. |
| rev3 | rev2 + two recall fixes found through holdout 2's one miss (removed/added line located by its first code character; any star-import edit selects all). |

## Labels

`build/label.py`: one patch at a time on a clean LF checkout of the pin, the whole suite with
`SKIP_INTEGRATION=0` and `--continue-on-collection-errors`, an incremental failure record, and no
label unless every catalogued test reported. Fidelity against the official sample answers:

* original configuration (8 xdist workers, default pools, idle machine): **30/30** exact;
* final configuration (4 workers, `PYTHON_CPU_COUNT=2`, 9 GB job memory cap): **30/30** exact.

Sets: holdout 1 (seed 20261121, 66 patches, 42 breaking, 0 excluded); feature (4 patches, kept
separate); holdout 2 (seed 31337, 66 patches: 65 scored, 38 breaking, 1 excluded as incomplete:
`h33729`, a memory bomb that hit the cap and the 900 s suite timeout); holdout 2 unique (61 scored,
36 breaking: the 4 patches byte-identical to holdout 1 removed, `h33724`=`h12101`,
`h33730`=`h12131`, `h33739`=`h12133`, `h33762`=`h12132`); validation 3 (27 independent patches
aimed at rev3's two fixes, generated after holdout 2).

## Main comparison (identical inputs, 3 runs each)

| set | version | simulated (s) | misses | missed commits | CPU median (s) | total median (s) |
| --- | --- | --- | --- | --- | --- | --- |
| sample (official answers) | baseline | 121.100 | 0 | - | 0.266 | 121.4 |
| | baseline+probes | 121.100 | 0 | - | 2.984 | 124.1 |
| | correctness | 171.341 | 0 | - | 1.328 | 172.7 |
| | frozen | 109.549 | 0 | - | 1.344 | 110.9 |
| | rev2 | 118.245 | 0 | - | 1.359 | 119.6 |
| holdout 1 (66) | baseline | 2641.427 | 7 | h12101 h12112 h12120 h12139 h12141 h12151 h12162 | 0.516 | 2641.9 |
| | baseline+probes | 1795.553 | 4 | h12101 h12120 h12139 h12141 | 13.297 | 1808.8 |
| | correctness | 745.058 | 0 | - | 6.156 | 751.2 |
| | frozen | 701.636 | 0 | - | 5.922 | 707.6 |
| | rev2 | 713.140 | 0 | - | 5.938 | 719.1 |
| feature (4) | baseline / +probes / correctness | 76.652 | 0 | - | 0.13 / 2.84 / 1.16 | 76.8 / 79.5 / 77.8 |
| | frozen | 9.483 | 0 | - | 1.203 | 10.7 |
| | rev2 | 18.411 | 0 | - | 1.125 | 19.5 |
| holdout 2 (65) | baseline | 1105.571 | 2 | h33724 h33749 | 0.781 | 1106.4 |
| | baseline+probes | 1131.702 | 2 | h33724 h33749 | 13.453 | 1145.2 |
| | correctness | 946.252 | 1 | h33749 | 6.438 | 952.7 |
| | frozen | 896.300 | 1 | h33749 | 6.172 | 902.5 |
| | rev2 | 905.228 | 1 | h33749 | 6.203 | 911.4 |
| holdout 2 unique (61) | baseline | 798.833 | 1 | h33749 | 0.766 | 799.6 |
| | baseline+probes | 824.964 | 1 | h33749 | 13.703 | 838.7 |
| | correctness | 935.024 | 1 | h33749 | 6.375 | 941.4 |
| | frozen | 885.072 | 1 | h33749 | 6.031 | 891.1 |
| | rev2 | 894.000 | 1 | h33749 | 6.141 | 900.1 |

### rev3 and the independent validation set (3 runs each)

| set | version | simulated (s) | misses | CPU median (s) | total median (s) |
| --- | --- | --- | --- | --- | --- |
| sample | rev3 | 118.245 | 0 | 1.359 | 119.6 |
| holdout 1 | rev3 | 776.358 | 0 | 6.953 | 783.3 |
| feature | rev3 | 18.411 | 0 | 1.484 | 19.9 |
| holdout 2 | rev3 | 741.078 | 0 | 7.594 | 748.7 |
| holdout 2 unique | rev3 | 693.460 | 0 | 6.859 | 700.3 |
| validation 3 (27, 13 breaking) | baseline | 3640.473 | 12 | 0.172 | 3640.6 |
| | baseline+probes, correctness, frozen, rev2 | 3349.836 | 11 | 0.83 to 1.63 | 3350.7 to 3351.5 |
| | rev3 | 868.992 | 0 | 2.125 | 871.1 |

rev3's holdout-2 rescue of `h33749` is **in-sample** (the two fixes were found by analysing that
miss). Validation 3 was generated afterwards to test them on patches nobody had seen: removal of
each of the other 20 star-import lines, the 3 module/class-level statements with a trailing comment,
and 4 commented import lines.

### Newly missed and rescued commits (individually)

| set | comparison | rescued | newly missed |
| --- | --- | --- | --- |
| holdout 1 | frozen vs baseline+probes | h12101 (nested conftest), h12120 (coding cookie), h12139, h12141 (failing conftest) | none |
| holdout 1 | frozen vs baseline | the 4 above + h12112, h12151, h12162 (imports broken at load: probes) | none |
| holdout 1 | rev2, rev3 vs frozen | - | none |
| holdout 2 | frozen, rev2 vs baseline(+probes) | h33724 (nested conftest, repeat of h12101) | none |
| holdout 2 | rev2 vs frozen | - | none |
| holdout 2 | rev3 vs frozen/rev2 | h33749 (removed `from sqlglot.expressions.properties import *`; 1600 failing; in-sample) | none |
| validation 3 | rev3 vs every other version | v3star00-05, v3star12, v3star13 (star imports), v3imp02, v3imp03 (commented imports), v3tcm00 (commented class-level statement) | none |

### h33729 (excluded, one-sided)

The patch (`build_scope` returns `traverse_scope(...)[+1]`) made the suite exhaust memory; the
capped run reached the 9 GB job limit and the 900 s suite timeout, so it is **incomplete** and not
scored. Its 67 failure ids recorded before the kill are kept. Every version (baseline, frozen, rev2,
rev3) selects the same 329 tests and covers 66 of the 67. The omitted one,
`tests/test_parser.py::TestParser::test_max_nodes`, never executes `scope.py` in the coverage map
and is the test whose xdist worker crashed in the earlier, uncapped attempt: most likely a casualty
of the memory exhaustion, not of the edit, but this cannot be confirmed here.

### Where the cost goes

* Increments on holdout 1 (frozen-era profile): correctness 745.058, + is/== evidence only
  741.786 (-3.3), + key coverage only 704.908 (-40.2), both (frozen) 701.636. On the sample the
  is/== evidence is the big one (-50.2: 2haz, 66ks); on held-out data key coverage dominates.
* Frozen vs baseline+probes on holdout 2 unique (885.1 vs 825.0, same single miss): two harmless
  new `tests/conftest.py` files select all tests in scope (+68.8: the rule cannot tell harmless from
  breaking conftest content), and is/== swaps (+42.9: the baseline's unsound text rule happened to
  be right on all five, one costs a full run here) outweigh key-coverage savings (-35.5).
* rev2 vs frozen (+8.7 to +11.5 per set): the one unobserved test (kept by every dictionary-entry
  narrowing), and dictionary entries that changed trust between profile builds.
* rev3 vs rev2 (+63.2 on holdout 1): the holdout generator's "harmless extra import" adds
  `import os as _unused_os  # noqa`. Before the location fix such a line was located nowhere and
  selected nothing (right only by luck); now the existing conservative rule for a new module-level
  name without readers selects the tests that ran the file. The same blindness is what made every
  earlier version miss `h33749` and 11 of 27 validation patches.

## Profiler regressions (counterexamples that reproduced on c8a81ea)

| counterexample | c8a81ea | fixed recorder | affected trusted sites in the pinned profile |
| --- | --- | --- | --- |
| `{1: "first", True: "second"}`, also via `**spread` | read of key 1 credited to entry 0 / to the dead literal | credited to the winning entry / to the spread source's own key | 0 collisions at the pin |
| consumer behind `type(d) is dict` | zero reads recorded, nothing flagged | static audit (0 such checks in sqlglot) + plain-vs-instrumented divergence on run-time lines | 1 unobserved test (dispatch-cache timing), kept by every narrowing |
| `obj != None` with side-effecting `__eq__`, inherited `object.__ne__` | recorder ran `__eq__`; site judged swap-safe | `!=` pure only if `__eq__` and `__ne__` are builtin on both sides; facts recorded, edit decided at run time | 2 comparison sites: `dialect.py:1145 result is not None` (1231 tests; `Dialect` defines `__eq__`), `simplify.py:244` (1 test) |

Also: copies/bulk reads during a test make the entries untrusted (all 1399 `TRANSFORMS` entries,
which the dispatch cache copies); two pristine order-dependent tests are documented
(`build/order_dependent.txt`, each reproduced with plain pytest). Trust also moves between builds
with scheduling (the rule "read by a test that mutated a tracked dict"): 66 entries lost and 166
gained trust versus the frozen profile, all gained ones per-key lookup tables with recorded
readers and no value carrier. Regression tests: `build/test_profiler.py` (16), `build/test_selector.py` (37).

## Deadline and output checks (Windows)

* 45-patch batch (30 sample + 5 sleeping + 5 busy-looping + 5 harmless import-time patches):
  valid, 0 misses, 43 s wall, 43 s CPU, 74 KB stdout, at most 8 workers, unique valid numbers.
* 70-patch worst case (30 sample + 40 busy-looping imports): 145 s wall under the 150 s budget
  (36 probes timed out, 6 skipped, all of those commits run everything), every line valid.
* Every benchmark run above: one JSON line per commit, <= 8 workers, unique known numbers,
  stdout far below 2 MB (checked by bench.py independently of score.py).

## Packaging (emulated)

`build/emulate_build.py` runs the Dockerfile's own COPY/RUN chain: download of sqlglot at the pin,
SHA-256 check of all 253 files against `build/sqlglot.manifest`, decompression, byte-compilation:
about 3 to 10 s. The resulting `/app` holds exactly the scored selector/profile bytes and its entry point
logs `profile loaded: version 2 ... 1183 trusted dictionary entries, 783 comparison sites, 1
unobserved tests`. Committed tree about 1.8 MB.

## Not verified

No Docker or WSL was available, so none of these ran: `docker build` of this repository, the
official-limits container runs (`--cpus 2 --memory 4096m --read-only --network none
--user 65534`, `/tmp` 64 MB), the container's cgroup CPU (`make measure`), timings on linux/amd64.
All CPU numbers are Windows process-tree CPU; the scorer's Linux CPU will differ.

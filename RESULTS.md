# Results

Simulated numbers were measured on **Windows 11** and are exact; the public sample of this round was
also run in the real linux/amd64 Docker image under the official limits (see below). Windows runs use
`build/bench.py`/`build/compare.py`: the selector runs from an `/app`
laid out like the image (for rev2/rev3 the `/app` produced by running the Dockerfile's own
COPY/RUN chain, `build/emulate_build.py --worktree`), in a fresh empty temp dir with a clean
environment, three runs per (version, set). Each run's stdout is scored by the kit's own
`score.py`. CPU is user+system time of the whole process tree (Job Object accounting, including
exited probe processes). "Total" is simulated + CPU of the middle run, as the board computes it.

## This round: selection cost (`fcc7d3a` → this version)

Changes, each in [README.md](README.md): the import probe decides `test_lazy_load`; the
profile's "unobserved" rule only flags tests whose instrumented run missed plain lines
(`profile.pkl` re-merged from the saved coverage passes: identical apart from `unobserved`, now
empty); a verifier runs small, stdlib-only selections on the patched source and drops tests that
pass; AST-equal edits, docstring/postponed-annotation/`TYPE_CHECKING`-only edits, whole new
functions under fresh names and `import <start-up module> as <fresh name>` select nothing; dict
edits whose new values cannot raise skip the probe; the root `pyproject.toml` counts only through
its validity and `tool.pytest` table; a renamed or new test module importing a module collection
imports anyway no longer selects everything.

Simulated seconds / misses (official scorer; selection is deterministic, so one run per set):

| set | rev3 (`fcc7d3a`) | this version | oracle |
| --- | --- | --- | --- |
| public sample (30) | 118.245 / 0 | 97.076 / 0 | 94.351 |
| holdout 1 (66) | 776.358 / 0 | 654.997 / 0 | 309.807 |
| holdout 2 (65) | 741.078 / 0 | 545.397 / 0 | 234.161 |
| holdout 2 unique (61) | 693.460 / 0 | 534.169 / 0 | 222.933 |
| validation 3 (27) | 868.992 / 0 | 867.704 / 0 | 365.754 |
| feature (4) | 18.411 / 0 | 4.007 / 0 | 4.007 |
| fresh (6, labelled earlier, never used for tuning) | 100.086 / 0 | 99.718 / 0 | 64.381 |
| same-program edits (10, hand-built rule demos) | 266.486 / 0 | 0.000 / 0 | 0.000 |
| unread additions (5, hand-built rule demos) | 130.542 / 0 | 0.000 / 0 | 0.000 |
| **all** | **3713.658** / 0 | **2803.068** / 0 | |

The two hand-built sets are **rule demonstrations, not held-out evidence**: their patches were
written to contain exactly the edit categories the new rules excuse, then labelled with no failures.
Zero seconds on them shows the rules fire, not that they generalize. The independent check is a
fresh-seed holdout generated and labelled only after the selector and profile were frozen; see the
next section. Both demo sets were labelled like the holdouts (`build/label.py`, whole suite, 4
workers, capped pools; a pristine run first): every patch `complete` with zero failures.
Same-program:
in-line spacing, parentheses and line splits (`helper.ensure_list`), `0x0`/`0b0`/`0o1` spellings
(`tokenizer_core`), quote style (Snowflake `STRING_ESCAPES`), a function and a class docstring
(the latter read by `expressions/core.py` at class creation), a return annotation
(`parser._parse_statement`), a `TYPE_CHECKING` import, a module variable annotation, a comment and
reformatting inside a test. Unread additions: a new annotated function (`helper`), a new async
function (`parser`), argument annotations (`generator.sql`), a `TYPE_CHECKING` import
(`expressions/core`), `pyproject.toml` `line-length`.

Public sample with CPU (Windows, `build/bench.py`, whole process tree, 3 runs each): rev3 median
total 120.5 s (CPU 2.2 s), this version 100.4 s (CPU 3.3 s: 5 verifier runs of about 0.4 s
each; 2 probes instead of 4). In the real Docker image (`make sample`, then `make measure` with three
fresh containers and cgroup CPU) this version scored 97.076 s / 0 misses with CPU 2.08, 2.12 and
2.10 s across the three runs (median total 99.18 s). The simulated reduction is confirmed under the
official limits; the CPU figure is the container's, not a Windows process-tree estimate. An earlier
measurement of 3.45/3.75/2.98 s was taken while the independent holdout was being labelled on the
same machine and is not comparable.

**Verifier fidelity.** For every labelled patch that only edits existing sqlglot modules, all
verifiable selected tests plus all verifiable labelled failures were run by the verifier (no cost
policy) and compared with the labels: sample 20 agreeing failures / 21 agreeing passes, holdout 1
1148 / 1438, holdout 2 165 / 806, validation 3 1046 / 32, feature 2 / 1, fresh 177 / 55, with
**0 false passes and 0 false failures**. Two environment gaps found on the way are closed: without
`dateutil` sqlglot's simplifier stops folding date arithmetic (a pristine Postgres test failed),
and `test_generator_memory` (tracemalloc counts pytest-xdist's I/O thread) fails in labels whenever
many tests fail, which an isolated run cannot reproduce; it is never verified.

## Independent holdout (frozen artifact)

To check that the gains above are not fitted to the sets used while writing the rules, the selector
and profile were frozen first: `selector.py` SHA-256
`068f644a9f0302aef1d59eb555fafd6a8e2c00327ca02006ba98404e9a3c5b3e`, `profile.pkl` SHA-256
`d306c5b42d9767fbef629f12dbb1922c030dec4b7dde75b2154d961ba120967b`. Neither file changed after
the labels were made. `build/holdout.py --seed 8675309 --n 66` then generated 66 patches (a seed
used by neither the earlier holdouts nor the parity sets); `build/label.py` ran the full suite on a
pristine worktree with 4 workers: pristine `complete` with 0 failures, all 66 labels `complete`,
43 breaking. The selector's output was scored against those labels by the kit's `score.py`:

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| rev3 (`fcc7d3a`) | 670.708 | 0 |
| this version (frozen) | 571.500 | 0 |
| this version with PR #5's probe server merged | 574.340 | 0 |

So the independent set shows a 14.8 % reduction with no misses, rather than the near-zero change on
the small earlier "fresh" set. The merge with PR #5 costs 2.84 s here: a forked probe does not
report the `test_lazy_load` observation, so that test is kept conservatively. On the public sample
the two versions are effectively tied in Docker (97.076 s / 0 misses; median totals 99.18 s for
this PR and 99.23 s for the merge, CPU 2.10 and 2.15 s). If both PRs ship, the holdout interaction
should be handled explicitly rather than assumed free.

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

Verified in Docker for this version: `docker build`, `make sample` (official limits: `--cpus 2
--memory 4096m --read-only --network none --user 65534`, `/tmp` 64 MB, 256 pids) and `make measure`
(three fresh containers, cgroup CPU), including the verifier's runs as `nobody` with the hash-pinned
site-packages installed by the Dockerfile. The simulated score is 97.076 s / 0 misses and the median
total is 99.18 s; the verifier did run inside the image (five test batches on the sample).

Still emulated rather than run in Docker: the holdouts, validation, feature and demo sets, and the
Windows comparison versions. Their CPU figures are Windows process-tree CPU and are not comparable
to the container's cgroup CPU. If the image's packages ever differ from the pinned versions, the
verifier refuses to run (version check) and selections stand.

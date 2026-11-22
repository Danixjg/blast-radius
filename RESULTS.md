# Results

## Build-time import index (follow-up to the import-time rules)

The image build writes `/app/import-sites.json`: where the pinned sources import each sqlglot module
(import statements under every module they may import, lazy package attributes, and imports by
computed name under their constant target, their constant prefix, their package's lazy table, or
for any module). When a patched import fails, the selector looks the failed modules up instead of
scanning and parsing the source tree; without a valid index for this source tree it rebuilds the
same index in-process. It also keeps tests that opened a failed module's source file (from the
first version of this index, `c6ce054`); with the computed-name rule in place this adds no test on
any labelled set.

Selections are **byte-identical** to the source-scanning selector on all 15 labelled sets (512
commits) and on the fresh import-failure set (30). Windows process-tree CPU, same packaging,
`map.pkl`, `profile.pkl` and interpreter, three alternating runs per version (`build/bench.py`; a
labelling job was running at the same time, so absolute times are high):

| set | source scan, median CPU (runs) | index, median CPU (runs) | simulated | misses |
| --- | ---: | ---: | ---: | ---: |
| 30 import failures (`work/impval`) | 56.281 s (50.219-59.656) | 19.328 s (18.891-19.438) | 517.671 | 0 / 0 |
| public sample (30) | 2.422 s (2.312-2.844) | 2.297 s (2.281-2.688) | 97.076 | 0 / 0 |

About 1.2 s of CPU less per commit whose probe fails; no change elsewhere.

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
fresh containers and cgroup CPU) this version scored 97.076 s / 0 misses with CPU 2.00, 2.02 and
1.99 s across the three runs (median total 99.08 s) after the merge with PR #5. Before that merge
the same sample scored 97.076 s / 0 misses with median total 99.75 s. The simulated reduction is confirmed under the
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
`068f644a9f0302aef1d59eb555fafd6a8e2c00327ca02006ba98404e9a3c5b3e` at the time of generation,
and `profile.pkl` SHA-256
`d306c5b42d9767fbef629f12dbb1922c030dec4b7dde75b2154d961ba120967b`. An adversarial test later
found a false negative in the lazy-test rule (below); the one-line fix changed the selector to
`9a95adfba9c68605ccb940f3a8ff749d32800098fb25a96ea5af3836124d2d72`. The fix came from that test,
not from holdout results, and both holdouts were re-scored with the corrected selector.
`build/holdout.py --seed 8675309 --n 66` then generated 66 patches (a seed
used by neither the earlier holdouts nor the parity sets); `build/label.py` ran the full suite on a
pristine worktree with 4 workers: pristine `complete` with 0 failures, all 66 labels `complete`,
43 breaking. The selector's output was scored against those labels by the kit's `score.py`:

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| rev3 (`fcc7d3a`) | 670.708 | 0 |
| this version (frozen) | 571.500 | 0 |

So the independent set shows a 14.8 % reduction with no misses, rather than the near-zero change on
the small earlier "fresh" set. A temporary merge with PR #5 was measured and discarded: it scored
574.340 s with 0 misses here, 2.84 s worse, because a forked probe cannot report the
`test_lazy_load` observation and keeps that test. On the public sample the two were tied in Docker
(97.076 s / 0 misses; median totals 99.18 s and 99.23 s).

PR #5 has since been merged into `main`, and this branch now merges `main` with that cause
removed. A probe the server serves starts from a checkpoint whose recorded modules its patch does
not touch, so the patched `import sqlglot` behaves exactly as the pristine one and
`test_lazy_load`'s verdict is the pristine pass. Only fresh probes, which import `sqlglot`
themselves, replay the assertion. On Windows (no fork, so no server) selections are byte-identical
to the pre-merge branch on all eight labelled sets. The merged version has not been re-measured in
Docker or on the independent holdout; the server path needs Linux.

## Second independent holdout (frozen merged artifact)

The merged branch was frozen before this set existed: commit `a654094`, `selector.py` SHA-256
`9134f268071812b95436ab2d818d06b6aef769fae9faad4d8b6a507e7725d902`, `profile.pkl`
`d306c5b42d9767fbef629f12dbb1922c030dec4b7dde75b2154d961ba120967b`. Then
`build/holdout.py --seed 271828 --n 66` generated 66 patches with a seed used by no earlier set.
`build/label.py` ran the full suite per patch, on Windows with 4 workers. The pristine run was
`complete` with 0 failures. 65 patches were `complete` (37 breaking). One generated patch was
corrupt; `git apply` refused it, so it is left out. Scored by the kit's `score.py`:

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| rev3 (`fcc7d3a`) | 619.830 | 0 |
| this branch (frozen `a654094`) | 491.085 | 0 |

That is a 20.8 % reduction with no misses. The oracle (only the failing tests, packed) is 269.140 s.

Most of the remaining 221.9 s is structural:

- three import failures (a missing module, two removed imports) that the probe turns into
  "run everything" (62.8 s over the oracle);
- `is`/`==` swaps and code edits whose selection includes the 21 s `test_execute_tpcds`;
- class-attribute string edits read by many tests.

### Additional pre-merge holdout (seed 42424242)

A third set was generated from the pre-merge frozen selector
(`9a95adfba9c68605ccb940f3a8ff749d32800098fb25a96ea5af3836124d2d72`, same profile hash). It is
not the shipping artifact — PR #5 was merged afterwards — but it is a further check that the gains
are not seed-specific. 66 patches, 40 breaking, pristine `complete` with 0 failures, all 66 labels
`complete`:

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| rev3 (`fcc7d3a`) | 799.615 | 0 |
| pre-merge version | 610.261 | 0 |

## Third independent holdout (frozen follow-up artifact)

PR #7's code was frozen before this set existed: commit `209864a`, `selector.py` SHA-256
`2647cb4306b4f68e27a9375ad24f8f2bdb673c2361b5745356b5e794818a9a13`, same `profile.pkl` as above.
Then `build/holdout.py --seed 314159 --n 66` generated the set, and `build/label.py` ran the full
suite per patch. The pristine run had 0 failures, and all 66 patches were `complete` (39 breaking):

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| rev3 (`fcc7d3a`) | 644.613 | 0 |
| #6 (`caa13a4`) | 501.325 | 0 |
| #7, short-circuit rule and compile skip (`209864a`) | 501.325 | 0 |

That is a 22.2 % reduction with no misses. The oracle is 210.215 s. No swap in this set sits behind
an `and`/`or`, so the follow-up's rule leaves it unchanged. Across the three independent holdouts
(seeds 8675309, 271828, 314159, all frozen first), the reduction against rev3 is 14.8 %, 20.8 % and
22.2 %, with zero misses on each.

## Short-circuited `is`/`==` swaps (follow-up to the frozen branch)

For an `is`↔`==` swap with profile evidence, the selector keeps three groups:

- tests with an operand fact the swap changes;
- unobserved tests;
- tests that ran the line but were never seen evaluating the comparison.

An audit over every labelled swap found the third group non-empty only at
`tokenizer_core.py:793` (`if self._comments and token_type == TokenType.SEMICOLON and
self.tokens:`). There, 1,520 tests ran the line and only short-circuited at `self._comments`; none
failed. The narrowing is semantic: if the comparison follows the first operand of an `and`/`or`
chain, a test that ran the line in both recorded orders without ever evaluating it short-circuited
before it, and the swap cannot reach it. Tests with changing facts and unobserved tests stay.

Because that rule came from one case, it was validated on sites chosen by structure alone. A
script picked 24 swap sites (seed 4242): profiled; an `and`/`or` operand after the first; at least
5 short-circuiting tests; no fact the swap changes; at most two per file. One of the 24 is the site
above, so it is in-sample. Each was swapped and labelled by full-suite runs. All 24 were `complete`
with **zero failures**, so no short-circuiting test failed anywhere.

| set | rule off | rule on | misses |
| --- | ---: | ---: | ---: |
| structural swap set (24) | 281.755 | 113.179 | 0 / 0 |
| holdout 1 (66) | 654.997 | 618.608 | 0 / 0 |
| holdout 2, holdout 4, validation 3, sample, feature, fresh | unchanged | unchanged | 0 / 0 |

On four of the 24, the edited line holds two comparisons (`… is True and … is True`). The swap
cannot be pinned to one comparison with profile evidence, so line coverage applies as before.

The same follow-up skips the full compile of a patched module whose AST equals the pristine one
(comments, layout, literal spelling). The decoding check runs first: the existing latin-1 cookie
test caught a first version where unchanged lines were assumed to mean unchanged text. The
selector process's own CPU on the sample (Windows, median of 5) went from 0.312 to 0.281 s, with
identical selections on every set.

## Import time and coverage: four rules (follow-up to #7)

None of these edits occurs in the public sample, which stays at 97.076 / 0. Each rule was
validated on 30 fresh, structure-chosen patches labelled by full-suite runs after the selector was
frozen by SHA-256. Two first attempts failed; they were fixed by general rules and re-validated on
new sets with new seeds.

### One-line lambdas (a correctness fix for main)

A call of a lambda reports no `sys.monitoring` line event for the lambda's first line, because its
RESUME instruction sits there. The coverage map therefore never sees who calls a lambda written on
one line, such as `"ISNULL": lambda args: build_coalesce(args=args, is_null=True)` in `FUNCTIONS`,
`TRANSFORMS` entries and closure factories. It only sees whoever created the lambda, which for a
dict entry is the import. On main, an edit inside such a lambda selected almost nothing. An edit on
a lambda's first line is now judged by what encloses it: per-entry dict readers, attribute readers
and instances, or the import-time function analysis. sqlglot has 716 such mutation sites.

Validation (`work/lam`, frozen `7cf5db09…` = `5a211a2`): token mutations of build/holdout.py's
kinds inside one-line lambdas, a seeded sample of 30, at most 2 per file. 30 labels `complete`, 29
breaking, 46 failing tests in all.

| selector | simulated seconds | misses | failing tests omitted |
| --- | ---: | ---: | ---: |
| main (`08aeb05`) | 8656.711 | 28 | 43 of 46 |
| this version | 520.328 | 0 | 0 |

### Tests that only imported a module (deferred runs)

`pushdown_predicates` imports Athena and Presto inside the function for an `isinstance` check. The
pool workers of TPC-DS/TPC-H execution and of the optimizer fixture checks import those modules,
plus Hive, Trino and MySQL, while a test runs. Coverage therefore shows `test_execute_tpcds`
(21.45 s) running every class attribute of those dialects. For a plain `NAME = value` edit in a
sqlglot module whose values build without side effects, tests that only ran the statement while
their process imported the module are no longer selected. Readers and instances are selected as
before.

Validation (`work/dfr`, frozen `31ff5286…`): value mutations on plain assignments where the rule
changes the selection, 30 of 78 candidates. 30 `complete`, 14 breaking. Every patch drops 2-4 pool
tests, and none of the dropped tests fails.

| selector | simulated seconds | misses |
| --- | ---: | ---: |
| main | 764.184 | 0 |
| this version | 292.756 | 0 |

### Failed imports: the tests that can reach the module

A probe that finds modules failing to import used to mean "run everything". Now only a failing
`import sqlglot` does. For other modules (the probe lists each one, including those that fail only
because they import a failing one), the selection is:

- tests that ran their code, imported them while running, or created their classes or their
  dialect's (dialect, parser, generator, tokenizer and typing modules of one name);
- tests that execute an import statement naming them: module level in a test module or helper means
  the whole file and its importers; inside a function, whoever ran it; `if TYPE_CHECKING:` is
  ignored;
- lazy package attributes: `from sqlglot.dialects import Snowflake`, and the `sqlglot.optimizer`
  `_LAZY_ATTRS` table;
- imports by computed name: `importlib.import_module`, `__import__`, runpy and `exec_module`, by
  constant name, by constant prefix, or with any target;
- bulk dialect loaders: tests that ran `Dialect._try_load` or created classes of at least 80 % of
  the dialects.

A failing module never runs, so its own edits add nothing more. The verifier is not used on such
commits.

| validation set | main | frozen candidate | misses |
| --- | ---: | ---: | ---: |
| `work/impval` (30, frozen `40a40883…`), **failed** | 1104.510 | 1962.916 | 0 / **4** |
| `work/impval2` (30, 29 breaking, frozen `e0536360…` = `e67b7c2`) | 1104.510 | 546.717 | 0 / 0 |

The four misses in the first set were all `test_typing`. It walks `sqlglot.typing` with
`pkgutil.iter_modules` and imports each module by name. That led to the computed-name rule.

### Removed import lines: the module's own readers

A removed one-line module-level import of a standard-library module, or of a module that
`import sqlglot` loads anyway, selects the tests that ran a line of that module reading the removed
names. It falls back in four cases: the names are bound elsewhere; another file takes them from the
module (`from M import name`, `M.name`, `from M import *`); something scans its namespace
(`subclasses(__name__, …)`, `getmembers`, `vars`, `dir`); or a read sits on a lambda's first line.
The import is probed.

| validation set | main | frozen candidate | misses |
| --- | ---: | ---: | ---: |
| `work/rmimp` (30, frozen `1586b06f…`), **failed** | 1016.319 | 1166.308 | 0 / **3** |
| `work/rmimp2` (30, 21 breaking, frozen `7cf5db09…` = `5a211a2`) | 1019.419 | 341.554 | 0 / 0 |

The three misses in the first set were reads inside one-line `FUNCTIONS` lambdas. Investigating
them uncovered the lambda blind spot above.

### All labelled sets

| set | main (`08aeb05`) | this version | misses |
| --- | ---: | ---: | ---: |
| public sample (30) | 97.076 | 97.076 | 0 / 0 |
| holdout 1 (66) | 618.608 | 551.231 | 0 / 0 |
| holdout 2 (65) | 545.397 | 464.316 | 0 / 0 |
| holdout 2 unique (61) | 534.169 | 453.088 | 0 / 0 |
| independent holdout seed 271828 (65) | 491.085 | 434.951 | 0 / 0 |
| independent holdout seed 314159 (66) | 501.325 | 465.185 | 0 / 0 |
| validation 3 (27) | 867.704 | 858.341 | 0 / 0 |
| fresh (6) | 99.718 | 75.587 | 0 / 0 |
| short-circuit swaps (24), same-program (10), unread additions (5), feature (4) | unchanged | unchanged | 0 / 0 |

The holdouts (1, 2, and the seed 271828 and 314159 sets) were examined while these rules were
designed, so here they are regression checks; the fresh evidence is the four validation sets
above. Selector CPU on the sample is unchanged within timer
resolution. A commit whose probe fails spends extra CPU reading the source tree; the build-time
import index in the follow-up PR removes most of that.

## SQL fixture edits that change only expected outputs (follow-up to the import index)

A fixture file read through `tests/helpers.py`'s `load_sql_fixture_pairs` is a list of records:
metadata, input SQL and expected output. Before this change, an edit selected every test that opened
the file. For the TPC-H and TPC-DS fixtures that includes `test_execute_tpch` (3.9 s) and
`test_execute_tpcds` (21.5 s). Both execute the inputs and never read the expected outputs.

For a patch that only edits such a fixture, both versions are parsed with the pinned loader. That is
the pristine `tests/helpers.py`, which uses only the standard library, fed the patched text in
memory. The projection applies when both versions parse and every record's metadata and input SQL
are unchanged, in the same number and order. In that case only expected outputs changed. The test
code is then followed from each loader call to every place that unpacks its records, through:

- for loops and comprehensions, including through `enumerate`;
- stored `self`/`cls` attributes, and method parameters that receive the records;
- unpacking of `records[i]`.

A test that opened the file is dropped only if, at every such place it ran, the expected field is
unpacked into `_`. Every opener stays in any of these cases:

- a record use the analysis does not follow (another call, `len()`, a bare subscript);
- a loader failure, which is a setup error for every reader;
- changed inputs, metadata, count or order;
- another file in the same patch.

Validation (`work/fixval`, selector frozen `d0cb0dcd…` = `300f7f0`): 30 edits of the TPC-H/TPC-DS
fixtures, each classified with the pinned loader itself: 18 expected-output token edits, 4
input-SQL edits, 3 metadata toggles, 2 malformed metadata lines and 3 reordered record pairs. All
30 labels are `complete`, and 24 patches break tests.

| kind | patches (breaking) | before (#9) | after | selections changed | misses |
| --- | ---: | ---: | ---: | ---: | ---: |
| expected-output edits | 18 (18) | 458.172 | 143.399 | 18 | 0 / 0 |
| input SQL edits | 4 (4) | 101.816 | 101.816 | 0 | 0 / 0 |
| malformed metadata | 2 (2) | 50.908 | 50.908 | 0 | 0 / 0 |
| metadata toggles | 3 (0) | 76.362 | 76.362 | 0 | 0 / 0 |
| reordered records | 3 (0) | 76.362 | 76.362 | 0 | 0 / 0 |

Regression checks: holdout 2 goes from 464.316 to 447.102, from its one expected-output fixture edit
(`h33743`, earlier 25.454 s and now 8.240 s, with its labelled failure kept). Holdout 2 unique goes
from 453.088 to 435.874. All other sets are unchanged, with 0 misses.

## Adversarial checks

Targeted tests were added for the three rules most likely to be overfitted:

- **AST-equal shortcuts.** When a test reads `__doc__`, `__annotations__` or calls
  `get_type_hints`, a docstring/annotation change still selects it; existing cases cover eager
  annotations, class-level annotations, decorators, `TYPE_CHECKING` switches, shadowed builtins and
  side-effecting defaults.
- **Lazy-test rule.** A probe that only counted dialect classes was a false negative: appending
  `builtins.print = lambda *a, **k: None` to `sqlglot/__init__.py` keeps one class but makes the
  test's `assert "Success" in stdout` fail. The probe now replays that output assertion too. With
  the fix, the patch selects the test; before the fix it selected nothing. This is the one change to
  `selector.py` after the first holdout was generated. In the merge with `main` the replay goes
  further: `Success` is printed through the probe's own (possibly patched) `print` and `stdout`, the
  end of that output is marked on fd 1 with a raw write, and the parent applies the test's stdout
  assertion to the bytes before the marker, decoded strictly as UTF-8 like the test's text-mode
  capture. Replaying `print` into a swapped-in buffer would miss a patch that redirects
  `sys.stdout` at import, or one that writes undecodable bytes; both now keep the test.
- **`unobserved` narrowing.** `test_profiler.py` checks that a plain-only run-time line marks a test
  unobserved, and that a superset instrumented path stays observed while still being reported as a
  divergence. The profile tracks value provenance, so equal line sets are not proof of equal value
  reads; this remains a documented limitation rather than a validated guarantee.

After the fix, the first holdout still scores 571.500 s / 0 misses and the public sample still
scores 97.076 s / 0 misses in Docker (median total 99.08 s after the PR #5 merge).

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

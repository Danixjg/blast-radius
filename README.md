# blast-radius

Test selection and scheduling for the Blast Radius track: for every patch to sqlglot (pinned at
`eb11aec4`), pick every test the patch can break and pack them onto up to 8 workers as cheaply as
possible. A missed failing test costs 300 s; the CPU the container uses is charged too.

## How it works

Everything runs from [selector.py](selector.py) (standard library only). The work is split
between a **hunter**, which reasons precisely from build-time maps, and a **catcher**, which
widens to a safe superset whenever that reasoning runs out.

### Maps (tracked files, built from the pinned commit)

- [map.pkl](map.pkl): per-test line coverage (including spawned `ProcessPoolExecutor` workers
  and `setUpClass`), fixture files opened, sqlglot classes instantiated, and what runs while
  sqlglot imports.
  The selector decodes the packed line and name sections on demand. It also accepts plain coverage maps.
- [profile.pkl](profile.pkl): a runtime profile recorded with sqlglot compiled through an AST
  rewriting import hook ([build/brprofile.py](build/brprofile.py)):
  - for every literal dictionary assigned in a module or class body, which tests read the
    **value** of each entry, following aliases (`self.numeric_escapes = self.NUMERIC_ESCAPES`),
    subclasses and derived dictionaries (`{**Base.SHOW_PARSERS, ...}`) back to the literal entry
    that supplied the value. Provenance is replayed with real dict semantics (`1` and `True`
    collide; a spread value is credited to its source's own key). Membership checks (`in`,
    iteration over keys, trie building) are not value reads. An entry is untrusted (ignored)
    when its value is read while a module imports, its dictionary is mutated, it is copied or
    bulk-read during a test (`dict(d)`, `{**d}`, `items()`: the copy may reach other tests
    unseen), its key has no stable identity, a spread cannot be followed, or sqlglot contains an
    exact-type test against `dict` (none at the pin);
  - for every `is`/`==`/`is not`/`!=` comparison against `None`, `True`, `False` or an
    ALL_CAPS member, **pristine operand facts** per test: identity, the result of side-effect-free
    equality, and whether `!=` is side-effect free (`__eq__` and `__ne__` builtin on both types).
    The selector decides each incoming operator edit from these facts at run time; the profile
    holds no edit outcomes;
  - **unobserved** tests: a plain and an instrumented run of the same serial order are compared;
    a test that executed a run-time line plainly but not instrumented took a path the recorder
    never saw and is kept wherever the profile would narrow a selection (none at the pin). A test
    whose instrumented run executed everything the plain run did plus more (the pin's one
    divergence: a pool worker happened to build the generator's dispatch cache during
    `test_tpcds`) was recorded on a superset of its path and stays observed; the divergence is
    still written to `profile.pkl.divergence.json`.

  The instrumented suite must pass (the forward pass cleanly; a reverse pass may only show the
  documented order-dependent pristine failures, [build/order_dependent.txt](build/order_dependent.txt)).
  The entry point logs `profile loaded: version 2 ...` when it uses the profile.

### Hunter

| Edit lands in | Tests selected |
| --- | --- |
| a statement inside a function | tests that ran it (compound statements: the header) |
| a lambda | tests that ran the lambda |
| a signature, decorator or new line in a function | tests that ran the function |
| the value of an existing entry of a literal dict, with side-effect-free old and new values (constants, names, attribute chains, lambdas, closure-factory calls) | tests the profile saw reading that entry (plus unobserved tests); the import is probed only when building the new value could raise (a name or attribute), since a trusted entry is read by nothing while modules import |
| a file whose AST is unchanged (comments, layout, quoting, literal spelling `0x10`/`16`, parentheses, line breaks) | nothing: Python compiles the same program |
| only docstrings, annotations of function arguments/returns or of non-class variables in a module with `from __future__ import annotations`, or the body of an unchanged `if TYPE_CHECKING:` | readers of `__doc__`/`__annotations__` (none at the pin); class-level annotations stay significant (dataclasses, NamedTuple, TypedDict read them) |
| a whole new module-level function under a fresh name (not a builtin, not mentioned on any import-time or test-run line, not in the module; no decorators, import-safe defaults, postponed annotations; no dunder, `test*` or pytest/unittest hook names) | nothing: defining it cannot fail and nothing can look it up (sqlglot's namespace scans collect classes and `*_sql` methods only) |
| `import os as _fresh` at module level, of a module every interpreter has loaded at start-up | nothing: a `sys.modules` hit binds a name nobody reads |
| a `from X import *` line (added, removed, changed) | every test: it re-exports an open set of names looked up at run time (`exp.Properties`) |
| any other class attribute / module constant / import | tests reading the names it binds that created the class (or a subclass/dialect), following import-time derivations; the import is probed unless the old and new values are constants, nothing that runs while modules import reads the name, and the class is a plain Dialect/Parser/Generator/Tokenizer-family class (no Enum, NamedTuple, TypedDict, dataclass or decorator processing its body) |
| `is` ↔ `==` / `is not` ↔ `!=` only | tests with an operand fact the swap would change, tests that ran the line but were never seen evaluating it, and unobserved tests that ran it; without profile evidence for that exact comparison, line coverage |
| a fixture file | tests that opened it |
| comments, blank lines (outside strings, file still compiles to the same text) | nothing |

### Catcher

- Every patched `.py` file is compiled from **bytes** (BOM, coding cookie, symbol-table and
  code-generation errors). A file that no longer compiles, or whose declared encoding changes
  what the bytes mean, selects its own tests plus importers (test module) or everything (sqlglot).
- **pytest collection**: `conftest.py` at the repository root or under `tests/`, a test package
  `__init__.py`, `pytest.ini`/`tox.ini`/`setup.cfg`, the pytest table or validity of
  `pyproject.toml`, and new root modules that shadow stdlib/third-party names select their whole
  collection scope (added, modified, deleted or renamed). Module-level edits of a test module
  select the module and every test module that imports it; added, deleted and renamed test files
  are handled the same way, and a new test module with side-effecting top-level code, or one that
  imports a `tests.*` module collection would not import anyway, selects all. The root
  `pyproject.toml` counts only through its validity and its `tool.pytest` table.
- **Import probe**: when import-time code changed, one fresh interpreter (`-I -S`) imports
  *every* sqlglot module with the *combined* patch served from memory through a meta-path finder
  (no overlay directory or symlinks; deleted modules raise). Any failure, a timeout, a probe that
  did not load the patched source, or a probe that could not start means: run every test.
  The probe imports `sqlglot` first and replays `test_lazy_load`'s assertions: a fresh
  `import sqlglot` creates exactly one dialect class, and `print('Success')` then reaches stdout
  (printed through the probe's own, possibly patched, `print` and `stdout`; the bytes up to a raw
  end marker must decode as UTF-8 and contain `Success`, as the test's text capture requires).
  That test, which coverage cannot see because it starts its own interpreter, is added only when
  an assertion fails or no probe runs.
- **Probe server**: when two or more probes patch only sqlglot modules, a chain of checkpoint
  processes starts. The first, started like a probe, imports `sqlglot` (the first module every
  probe imports); two more are forks that keep importing in the probe's own order up to evenly
  spaced points of the `sqlglot.dialects.*` block, where most import time goes. Each records every
  module loaded or looked for so far and forks one child per probe. A probe goes to the deepest
  checkpoint whose recorded modules its patch does not touch, so the child starts from exactly the
  state a fresh probe reaches after importing that prefix, and runs the same code from there (exit
  hooks and non-daemon threads included). Anything else, or any trouble with a checkpoint, uses a
  fresh probe. Probes with identical files run once. `build/probe_parity.py` checks verdict parity
  against fresh probes on any patch directories: 117/117 identical on the sample plus five generated
  66-patch sets. Whole-run CPU fell 2.1-2.3 s per 66-patch set (0.7 s of it from the two extra
  checkpoints) and 0.14 s on the sample, with identical output. `BR_PROBE_SERVER=0` turns it off;
  `BR_PROBE_CHECKPOINTS` sets the number of extra checkpoints (default 2). A probe the
  server serves needs no `test_lazy_load` verdict of its own: its patch touches nothing the
  pristine `import sqlglot` loaded or looked for, so that import behaves exactly as pristine.
- **Verifier**: when a patch only edits existing sqlglot modules and its selection is small
  (expected CPU `0.35 s + recorded seconds` below half the schedule cost: half of such edits
  break nothing) and made only of tests that can run here (no `test_executor.py` /
  `test_optimizer.py` process pools, no subprocesses, fixture files, source reads, global
  registry rewrites or allocation-counting tests), one fresh interpreter runs exactly those tests
  on the patched source with unittest (one suite in catalogue order, class/module fixtures as
  under pytest). A test is dropped only if it started, raised nothing (no failure, error, failing
  subtest, skip, expected failure or unexpected success) and finished within
  `max(1 s, 5 × recorded + 0.5 s)`; everything else, including tests that never report, stays.
  The image installs the labelling environment's exact `python-dateutil`, `tzdata`, `pytz`,
  `mypy-extensions` and `typing-extensions` wheels (pinned by sha256,
  [build/verify-requirements.txt](build/verify-requirements.txt)) because sqlglot's date folding
  and some test modules take different paths without them; the verifier refuses to run unless
  exactly those versions are importable. Checked against every labelled patch: no test it
  reports as passing is labelled failing.
- **Deadline**: the batch has one monotonic budget (150 s, inside both the 3 min practice and
  4.5 min final limits). Up to two probes run at a time, each bounded by `min(8 s, budget left
  minus a 5 s reserve)`; a probe that cannot start in time is skipped. Skipped or unfinished probes
  make that commit run everything, and every commit always gets a valid line.
- A removed or added line is located by its first code character (a trailing comment is not
  part of any statement node, so `x = 1  # noqa` used to resolve to nothing at module/class level).
- Internal errors widen, never crash; unmapped tests are kept whenever anything is selected.

### Scheduler

Longest-processing-time packing for each worker count from 1 to 8, keeping the cheapest
`max(load) + 2 s × workers` (unchanged).

## Results

See [RESULTS.md](RESULTS.md) for every number and how it was produced. Simulated seconds / misses,
official scorer. The public sample was also run in the real linux/amd64 image under the official
limits; the other sets were measured with the Windows emulation of that image.

| set | baseline+probes (`3fd38f7`, Windows copy-based probes) | frozen (`c8a81ea`) | rev3 (`fcc7d3a`) | this version |
| --- | --- | --- | --- | --- |
| public sample (30, oracle 94.351) | 121.100 / 0 | 109.549 / 0 | 118.245 / 0 | 97.076 / 0 |
| holdout 1 (66, independent labels) | 1795.553 / 4 | 701.636 / 0 | 776.358 / 0 | 654.997 / 0 |
| holdout 2 unique (61) | 824.964 / 1 | 885.072 / 1 | 693.460 / 0 | 534.169 / 0 |
| validation (27, aimed at rev3's two fixes) | 3349.836 / 11 | 3349.836 / 11 | 868.992 / 0 | 867.704 / 0 |
| same-program edits (10, hand-built rule demos: layout, literal spelling, docstrings, annotations, `TYPE_CHECKING`) | - | - | 266.486 / 0 | 0.000 / 0 |
| unread additions (5, hand-built rule demos: new functions, annotations, `pyproject.toml`) | - | - | 130.542 / 0 | 0.000 / 0 |
| independent holdout (66, seed 8675309, labelled after freeze) | - | - | 670.708 / 0 | 571.500 / 0 |
| independent holdout (65, seed 271828, frozen merged artifact) | - | - | 619.830 / 0 | 491.085 / 0 |

A third, pre-merge holdout (seed 42424242) scored 799.615 → 610.261 s with 0 misses; see
[RESULTS.md](RESULTS.md).

The two hand-built sets were constructed to contain the edit categories the new rules excuse. They
show the rules fire, but they are **not held-out evidence**; the independent check is the fresh-
seed holdout in [RESULTS.md](RESULTS.md), labelled after the selector and profile were frozen.

Public sample with CPU: rev3 120.5 s total (2.2 s CPU) on Windows; this version 100.4 s (3.3 s CPU)
on Windows and, in the real Docker image (`make sample` / `make measure`), 97.076 s / 0 misses with
median total 99.08 s (2.00 s median cgroup CPU) after merging PR #5; the pre-merge fix measured 99.75 s.

## Reproducing

Python 3.13 and `uv`; everything local lives in `work/` (ignored), labels never enter the image.

```sh
make checkout venv          # pinned sqlglot (LF line endings), venv from kit/requirements.lock
make test                   # selector regression tests (build/test_selector.py; also build/test_profiler.py)
make local                  # /app laid out like the image, 3 runs, CPU of the process tree, official scorer
make map profile            # rebuild map.pkl / profile.pkl (refuses unclean passes or untraced tests)
make holdout                # fresh holdout, labelled by running the whole suite (build/label.py)
make sample measure         # with Docker: official limits; measure = 3 fresh containers + cgroup CPU
python build/emulate_build.py <python3.13>   # the Dockerfile's RUN chain without Docker
```

The Docker build fetches sqlglot from its own repository at the pinned commit and verifies every
file against [build/sqlglot.manifest](build/sqlglot.manifest); sqlglot's MIT `LICENSE` is kept at
`/app/src/LICENSE`.

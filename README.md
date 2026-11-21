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
  - **unobserved** tests: a plain and an instrumented run of the same serial order must execute
    the same run-time lines per test; any test that diverges (one at the pin: a dispatch-cache
    timing effect) is kept wherever the profile would narrow a selection.

  The instrumented suite must pass (the forward pass cleanly; a reverse pass may only show the
  documented order-dependent pristine failures, [build/order_dependent.txt](build/order_dependent.txt)).
  The entry point logs `profile loaded: version 2 ...` when it uses the profile.

### Hunter

| Edit lands in | Tests selected |
| --- | --- |
| a statement inside a function | tests that ran it (compound statements: the header) |
| a lambda | tests that ran the lambda |
| a signature, decorator or new line in a function | tests that ran the function |
| the value of an existing entry of a literal dict, with side-effect-free old and new values (constants, names, attribute chains, lambdas, closure-factory calls) | tests the profile saw reading that entry, plus the subprocess test and unobserved tests; the import is still probed |
| a `from X import *` line (added, removed, changed) | every test: it re-exports an open set of names looked up at run time (`exp.Properties`) |
| any other class attribute / module constant / import | tests reading the names it binds that created the class (or a subclass/dialect), following import-time derivations |
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
  are handled the same way, and a new test module with side-effecting top-level code selects all.
- **Import probe**: when import-time code changed, one fresh interpreter (`-I -S`) imports
  *every* sqlglot module with the *combined* patch served from memory through a meta-path finder
  (no overlay directory or symlinks; deleted modules raise). Any failure, a timeout, a probe that
  did not load the patched source, or a probe that could not start means: run every test.
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

See [RESULTS.md](RESULTS.md) for every number and how it was produced (Windows only; no Linux
Docker run was possible). Simulated seconds / misses, official scorer, 3 runs each:

| set | baseline+probes (`3fd38f7`, Windows copy-based probes) | frozen (`c8a81ea`) | this version |
| --- | --- | --- | --- |
| public sample (30) | 121.100 / 0 | 109.549 / 0 | 118.245 / 0 |
| holdout 1 (66, independent labels) | 1795.553 / 4 | 701.636 / 0 | 776.358 / 0 |
| holdout 2 unique (61) | 824.964 / 1 | 885.072 / 1 | 693.460 / 0 (its rescue is in-sample) |
| validation (27, aimed at the last two fixes) | 3349.836 / 11 | 3349.836 / 11 | 868.992 / 0 |

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

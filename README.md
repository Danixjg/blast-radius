# blast-radius

Test selection and scheduling for the Blast Radius track: for every patch to sqlglot (pinned at
`eb11aec4`), pick every test the patch can break and pack them onto up to 8 workers as cheaply as
possible.

## How it works

The work is split between a **hunter** and a **catcher**, both in [selector.py](selector.py)
(standard library only; a normal batch uses well under 2 s of CPU). A patch that hangs at import
time costs one 30 s import-check timeout, still far less than the 300 s miss it prevents.

### Hunter: precise selection from a coverage map

[map.pkl](map.pkl) is a per-test map of the pinned sqlglot recorded from its own test suite
(see [build/](build/)):

- the lines each test runs, including work done inside spawned `ProcessPoolExecutor` workers
  (the optimizer and executor tests do most of their work there) and class-level setup
  (`setUpClass`), which is credited to every test of the class;
- the fixture files each test opens;
- the sqlglot classes each test instantiates (expressions, dialects, tokenizers, parsers,
  generators);
- what runs while sqlglot imports, and where each name is read at import time.

For each edited line the hunter finds the exact AST node the edit touches (using the changed
column range, not just the line) and asks the map who can observe it:

| Edit lands in | Tests selected |
| --- | --- |
| a statement inside a function | tests that ran that statement (for compound statements, its header) |
| a lambda (e.g. a `TRANSFORMS` entry) | tests that ran the lambda |
| a signature, decorator, or a new line in a function | tests that ran the function |
| a class attribute `A` of class `C` | tests that ran code reading `A` **and** created a `C` (or a subclass, or its dialect), following import-time derivations such as `TRANSFORMS = {**Base.TRANSFORMS, ...}` |
| a module constant | tests that ran code reading it, plus derivations |
| a fixture file | tests that opened it |
| comments, blank lines | nothing |
| `is` ↔ `==` (or `is not` ↔ `!=`) against `None` or a member of a sqlglot class deriving from `Enum` (`enum_names` in the map, e.g. `NormalizationStrategy.UPPERCASE`) | nothing; `True`/`False` and other ALL-CAPS attributes such as `Drill.DATE_FORMAT` are excluded |

A test that never reaches the edited statement runs exactly as before, so line coverage is a
sound filter for edits inside functions. The catcher handles everything else.

### Catcher: the safety net

A miss costs 300 s, more than an entire typical batch, so whenever the hunter cannot reason
precisely the catcher widens to a conservative superset:

- syntax errors, deleted or renamed files, missing sources: every test;
- names read by code that runs while modules import (metaclasses, `__init_subclass__`, helpers),
  or that cannot be followed: every test that touches the class or file;
- **live import check**: when import-time code changed, the patched modules are imported in a
  sandbox (symlink overlay in `/tmp`, using the build-time bytecode). If the import raises or hangs,
  every test fails, so every test is selected; a control import from the pristine source confirms
  the failure is caused by the patch rather than the environment;
- tests that spawn a subprocess Python (coverage cannot see inside) whenever import-time code changes;
- any internal error: widen, never crash. Tests missing from the map are kept whenever anything
  is selected.

### Scheduler

Longest-processing-time packing for each worker count from 1 to 8, keeping the cheapest
`max(load) + 2 s × workers`.

## Results

| Set | Commits | Misses | Simulated |
| --- | --- | --- | --- |
| starter (all tests, 3 workers) | 30 | 0 | ~1,200 s |
| this selector, sample | 30 | 0 | 121.1 s |
| oracle (exact failing tests) | 30 | 0 | 94.4 s |

The rest of the gap to the oracle is mostly class-attribute edits read by every test of a
dialect (e.g. a Snowflake tokenizer setting), and the 21 s `test_execute_tpcds` where it
really runs the edited code.

Recall was also checked on mutants built to resemble the sample: operator, constant and
boolean flips in code and class bodies, and corrupted fixture lines. Each was labelled by
running the full suite (`build/mutgen.py`). `build/validate.py` replays the sample and every
labelled mutant in-process in a few seconds.

## Rebuilding the map

The map is committed because the challenge allows maps built from the pinned commit. To
regenerate it you need Python 3.13 and `uv`:

```sh
make map      # venv from requirements.lock, clone sqlglot at the pin, 2 coverage passes, build map.pkl + src.tar.gz
make sample   # docker build + run on the sample under the scorer's limits, then score
```

The map is stored packed: its two large sections (`lines` per file, `uses` per name) hold one
pickle per key, which the selector decodes only when a patch reaches that file or name. On the
sample this cut the selector process's CPU by about 0.07 s and its peak memory from 80 to 58 MB,
with byte-identical output. `selector.unpack_map` restores the plain form; plain maps still load.
Check the format with `python3 -m unittest discover -s tests` (after `make kit` and `make local`
for the sample parity test).

`src.tar.gz` holds sqlglot's sources and test modules at the pinned commit (MIT licensed). The
selector reads them to apply patches and to run the import check.

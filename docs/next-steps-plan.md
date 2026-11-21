# Next steps: implementation plan

Baseline: `main` at `7664ada`. Sample: 118.245 s simulated, 0 misses. All numbers in
`RESULTS.md` were measured on Windows; Linux numbers below were measured in a cloud container
with the repo's container-like layout (no Docker), so they are indicative, not board-exact.

## Where the score goes now

| Component | Sample (30) | Holdout 1 (66), rev3 | Source |
| --- | ---: | ---: | --- |
| Simulated test time | 118.2 s | 776.4 s | `RESULTS.md` |
| Selector CPU, total | ~1.7 s (Linux) | 6.95 s (Windows) | measured / `RESULTS.md` |
| ↳ import probes | ~1.3 s (4 × ~0.32 s) | most of it | measured |
| ↳ selector process | ~0.4 s | — | measured |

Each probe imports every sqlglot module in a fresh interpreter: about 0.15 s of sqlglot module
execution, 0.05 s of standard-library imports, and the rest interpreter start and I/O.

`RESULTS.md` attributes much larger amounts to three conservative selection rules (seconds of
simulated time, per set):

| Rule | Cost | Where measured |
| --- | ---: | --- |
| New module-level name with no readers (e.g. `import os as _unused_os  # noqa`) selects every test that ran the file | +63.2 | holdout 1, rev3 vs rev2 |
| Any new `tests/conftest.py` selects all tests in its scope | +68.8 | holdout 2 unique |
| `is`/`==` swaps without profile evidence fall back to line coverage | +42.9 | holdout 2 unique |

Therefore: **selection precision is worth 10–50× more than CPU work**, but any precision change
risks a 300 s miss. CPU work is safe, but small. The plan does both, in the order below, behind
one evaluation gate.

## Gate for every change

A change is accepted only when all of these hold:

1. **Zero new misses** on every labelled set: sample, holdout 1, holdout 2, holdout 2 unique,
   feature, validation 3, plus the mutant corpus in `work/mutants` (`build/validate.py`).
2. Simulated total does not increase on any set, and drops on at least one.
3. Total = simulated + whole-tree CPU, median of 3 runs under the container limits
   (`make sample`, `build/docker_bench.sh`), improves beyond the 0.1 s score granularity.
4. Tests first: each rule change starts with failing unit cases in `build/test_selector.py`
   (harmless and breaking variants of the same edit), then the code.
5. The new rule is frozen before scoring a **fresh** holdout (new seed), so the decision is not
   tuned on the data that judges it. Any miss found there is in-sample once fixed and needs another
   fresh set to confirm, as `RESULTS.md` did for rev3.

## Step 0 — Linux evaluation harness (prerequisite)

Every precision step needs labelled patches. The labelled sets from `RESULTS.md` are not in the
repository.

- **Get the labels.** Either commit the existing `work/` label outputs (patches + `answers.json`
  per set, a few MB) or regenerate them: `build/holdout.py` with seeds 20261121 and 31337, then
  `build/label.py` (full suite per patch, roughly hours on 2–4 CPUs).
- **Unblock sqlglot in the cloud container.** `build/fetch_sqlglot.py` needs `codeload.github.com`
  or `github.com`, which this environment's network policy currently refuses (HTTP 403). Allow
  those hosts, or provide the pinned tarball. PyPI is already reachable for the pytest venv.
- **First Linux container run.** `make sample` and `build/docker_bench.sh` on a Docker host. No
  result has been verified in the actual Linux image yet; the 64 MB `/tmp` tmpfs, the 256-process
  limit and parallel probes on 2 CPUs should be checked once.
- **Deliverable:** a single command that runs every set 3× and writes a per-set
  simulated/misses/CPU table, so each later step is one diff against it.

## Step 1 — Unread new module-level imports (precision, ~63 s/set)

Today a new top-level binding with no readers selects every test that ran its file. For an
import, the only ways it can change outcomes are:

- the import fails or hangs (the probe already covers this, and it runs for import-time edits);
- importing it has side effects (third-party or sqlglot modules can register things);
- the bound name shadows or is read by something (star-export, `__all__`, a later `del`,
  `globals()`, attribute access on the module from elsewhere).

Proposed rule: a new module-level `import X [as Y]` or `from X import Z [as Y]` selects nothing
beyond the probe when **all** of these hold: X is a standard-library module (fixed allow-list for
the pinned 3.13); the bound name is not read anywhere in the file, not in `__all__`, and not
star-exported (leading underscore, or the module defines `__all__`); no name it binds is
referenced in the coverage map's `uses` for that module; and the probe returns `ok`.

Tests first: harmless (`import os as _unused_os`), shadowing (`import re as exp`), star-exported
(`import json` in a module that is star-imported), failing (`import nonexistent_mod`), and
side-effect (`import sqlglot.dialects.duckdb`) variants. Only the first must select nothing.

## Step 2 — New `conftest.py` classification (precision, ~69 s/set)

The selector image has only the standard library, so it cannot import a conftest (they import
`pytest`). Use a conservative static rule instead. A new `conftest.py` selects nothing beyond the
tests of a probe-free check when its top level contains only:

- imports of `pytest` or standard-library modules;
- function definitions decorated with `@pytest.fixture` **without** `autouse=True`, whose names no
  test in scope requests (test function parameters and `usefixtures` marks, read from the test
  ASTs in `/app/src/tests`) and that do not override an existing fixture name;
- docstrings and comments.

Any `pytest_*` hook, `pytest_plugins`, `collect_ignore`, top-level statement with effects, or
unparseable source keeps today's "everything in scope". Edits to an existing conftest stay on the
current path.

Tests first: the holdout generator's harmless conftest cases, plus an autouse fixture, a hook, a
fixture overriding a used name, and a syntax error.

## Step 3 — `is`/`==` swaps (precision, ~43 s/set; investigate first)

Rev3 uses the runtime profile's recorded operand facts where it has them and falls back to line
coverage otherwise. First measure on the labelled sets how many swaps hit the fallback and why
(site not profiled, operands not observed, mixed identity results). Then decide between extending
the profile's coverage (build-time cost only) and narrowing the fallback. Output of this step is a
measured breakdown and a proposal, not code.

## Step 4 — Probe CPU (safe, ~0.15–0.25 s per probe)

All three options keep the probe's verdict semantics. Compare them at equal outcomes:

1. **Deduplicate probes.** Commits with identical combined patched files share one probe result.
2. **Fork server.** Start one zygote process that preloads the standard-library modules sqlglot
   needs (about 0.05 s per probe saved), then fork one child per probe that installs the in-memory
   finder and imports sqlglot. The selector process uses threads, so fork from the dedicated zygote,
   not from the parent. Child CPU still counts, but only the part after the fork.
3. **Import only what the patch can affect.** Import the patched modules plus every module that
   imports them, transitively (reverse closure from the static import graph), instead of all
   modules. This is a semantic change, because a patched module could break an unrelated module by
   mutating shared state at import time. So it is accepted only if probe verdicts are identical to
   the full-import probe on every labelled patch and mutant, and every probe that fails under full
   import also fails under the closure.

Timeouts, the 150 s budget, process-group cleanup and "anything but ok runs everything" stay
unchanged. Tests: verdict parity on a fixture set of broken and hanging patches, plus a zygote
crash that must degrade to today's fresh-process probe.

## Step 5 — Write up and decide

For each step: diff against the Step 0 table, a supported/rejected/inconclusive decision, and a
`RESULTS.md` section. Ship steps independently.

## Deferred, with reasons

- **Rust port of the selector.** The selector process is now about 0.4 s of a ~120 s sample
  score, and the probes must stay Python because they import Python. The ceiling is under 0.4%.
  Revisit only for selector throughput as a service, not for the challenge score.
- **Worker/grouping and relay trials (Trials 6–9 of the earlier plan).** They improve real test
  execution, which the challenge only models. Worth doing if real CI time becomes the goal.

## Risks

- Labels are the bottleneck: without Step 0, Steps 1–3 cannot be judged safely, and a single miss
  costs 300 s, more than all CPU in a 66-patch set.
- The holdout generator's categories shape what "harmless" looks like. Rules tuned to its
  templates may not match the official hidden set; keep each rule's conditions syntactic and
  conservative, and confirm with a fresh seed.
- Static conftest analysis can be wrong about fixture usage through indirect requests
  (`request.getfixturevalue`); treat any such call in scope as "everything in scope".

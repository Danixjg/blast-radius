# Labelled patch sets

Full-suite labels for patches against sqlglot `eb11aec4`, used by `make eval` (build/evalsets.py)
when `work/` does not hold its own copy.

| set | made by | patches | scored | breaking |
| --- | --- | ---: | ---: | ---: |
| holdout1 | `build/holdout.py --seed 20261121` | 66 | 66 | 42 |
| holdout2 | `build/holdout.py --seed 31337` | 66 | 65 | 38 |
| mutants | `build/mutgen.py` (70 general + 30 class-body) | 100 | 100 | 70 |

Holdout layout: `commits/*.patch`, `labels/*.json` (build/label.py output, `_pristine.json` is
the clean-tree run), `meta.json` (generator categories), and `data/` + `answers.json` (written by
build/answers.py in the kit scorer's format). `h33729` is left out of holdout2's answers: its run hit
the memory cap (incomplete label). Mutants: `<id>.patch` + `<id>.json` with failing test ids;
`COLLECT::<path>` means that module or package failed to collect.

Labelled on Linux with 4 xdist workers, `PYTHON_CPU_COUNT=2` and the venv on PATH; the counts match
RESULTS.md. These sets were used to develop and tune rules, so they are in-sample for anything
written after them: a new rule needs a fresh-seed holdout labelled after the rule is frozen.

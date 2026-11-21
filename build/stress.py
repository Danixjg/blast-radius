"""Build a deadline stress batch: the sample commits plus import-time patches, some of which hang.

usage: stress.py <pristine_sqlglot> <out_dir> [--sleep N] [--busy N] [--normal N]

<out_dir>/commits holds the 30 sample patches plus `sleepK` (module-level time.sleep), `busyK`
(module-level busy loop, burns CPU until killed) and `impK` (harmless import-time constants)
patches in distinct dialect modules; tests.json is the sample catalogue. answers.json lists the
sample answers and, for the hangs, every test (an import that never finishes fails them all);
the harmless import-time patches break nothing (they only add an unused module constant).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "build"))
from holdout import git_diff  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--sleep", type=int, default=5)
    ap.add_argument("--busy", type=int, default=5)
    ap.add_argument("--normal", type=int, default=5)
    a = ap.parse_args()
    root, out = Path(a.root), Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    (out / "commits").mkdir(parents=True)
    sample = R / "kit" / "sample"
    shutil.copyfile(sample / "tests.json", out / "tests.json")
    answers = json.loads((sample / "answers.json").read_text())["commits"]
    for p in (sample / "commits").glob("*.patch"):
        shutil.copyfile(p, out / "commits" / p.name)
    tests = json.loads((sample / "tests.json").read_text())["tests"]
    every = [t["number"] for t in tests]
    mods = sorted(p.relative_to(root).as_posix() for p in (root / "sqlglot" / "dialects").glob("*.py")
                  if p.name not in ("__init__.py", "dialect.py"))
    k = 0
    for kind, n, tail, failing in (("sleep", a.sleep, "\nimport time as _t\n_t.sleep(600)\n", every),
                                   ("busy", a.busy, "\nwhile True:\n    pass\n", every),
                                   ("imp", a.normal, "\n_BR_STRESS_CONSTANT = 1\n", [])):
        for i in range(n):
            rel = mods[k % len(mods)]
            k += 1
            src = (root / rel).read_bytes().decode("utf-8")
            (out / "commits" / f"{kind}{i}.patch").write_text(git_diff(rel, src, src + tail), encoding="utf-8", newline="\n")
            answers[f"{kind}{i}"] = {"failing": failing, "kind": f"stress-{kind}"}
    (out / "answers.json").write_text(json.dumps({"commits": answers}), encoding="utf-8")
    print(f"{len(answers)} commits -> {out}")


if __name__ == "__main__":
    main()

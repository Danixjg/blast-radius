"""Build tpcds-check.json, the record selector.py's tpcds result check reads (see TPCDS_CHECK).

    work/venv/bin/python build/record_tpcds.py --sqlglot work/sqlglot --scratch work/tpcds-rec

1. Runs test_execute_tpcds on the pinned, unpatched checkout under build/tpcds_boundary.py, which
   profiles the pytest process and every spawned pool worker. "outside" lists, per sqlglot file,
   the (co_firstlineno, co_name) of each code object entered with no optimize() frame on its
   thread: an edit in any of them can reach the test by another route than optimize()'s result.
2. Runs selector.TPCDS_CHECK_CODE on the pristine source under three hash seeds; all must agree,
   and that hash is the "fingerprint" a patched run must reproduce.
3. Records the sha256 of every sqlglot/*.py file: the selector trusts the line numbers above only
   for files that are byte-identical to these.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import selector as S  # noqa: E402

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def check_checkout(source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    if git("rev-parse", "HEAD") != PIN or git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"expected a clean sqlglot checkout at {PIN}")


def record_outside(source, scratch):
    for p in glob.glob(str(scratch / "proc-*.json")):
        os.unlink(p)
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(ROOT / "build"), TPCDS_SRC=str(source), TPCDS_OUT=str(scratch))
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tpcds_boundary",
                          "--timeout", "1800", S.TPCDS_ID], cwd=source, env=env, capture_output=True, text=True)
    (scratch / "pytest.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    if run.returncode != 0 or "1 passed" not in run.stdout:
        raise SystemExit(f"the traced test run did not pass; see {scratch / 'pytest.log'}")
    outside, inside, procs = set(), set(), 0
    for p in glob.glob(str(scratch / "proc-*.json")):
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        outside |= {tuple(k) for k in d["outside"]}
        inside |= {tuple(k) for k in d["inside"]}
        procs += 1
    if procs < 2 or not inside:
        raise SystemExit(f"expected the pytest process and pool workers to report, got {procs} processes")
    by_file = {}
    for rel, line, name in sorted(outside):
        by_file.setdefault(rel, []).append([line, name])
    return by_file, len(inside - outside), procs


def pristine_fingerprint():
    reps = [S.tpcds_report({}, 120, isolate=False, env_extra={"PYTHONHASHSEED": seed}) for seed in ("0", "1", "2")]
    if any(r is None or "fingerprint" not in r for r in reps):
        raise SystemExit(f"the check did not run on the pristine source: {reps}")
    if len({(r["fingerprint"], r["executed"]) for r in reps}) != 1:
        raise SystemExit("the check's hash depends on the hash seed; it cannot be compared across runs")
    return reps[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlglot", required=True, type=Path)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--output", default=ROOT / "tpcds-check.json", type=Path)
    args = ap.parse_args()
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("use Python 3.13 with the kit's locked test dependencies")
    source = args.sqlglot.resolve()
    check_checkout(source)
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    outside, inside_only, procs = record_outside(source, scratch)
    S.SRC = source
    rep = pristine_fingerprint()
    sources = {p.relative_to(source).as_posix(): S.source_digest(p.read_bytes())
               for p in sorted((source / "sqlglot").rglob("*.py"))}
    record = {"version": 1, "sqlglot": PIN, "test": S.TPCDS_ID, "fingerprint": rep["fingerprint"],
              "executed": rep["executed"], "outside": outside, "sources": sources}
    pending = args.output.with_suffix(args.output.suffix + ".tmp")
    pending.write_text(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(args.output)
    print(f"{procs} processes, {sum(map(len, outside.values()))} code objects outside optimize(), "
          f"{inside_only} only inside; {rep['executed']} queries, check {rep['cpu']:.2f}s CPU -> {args.output}")


if __name__ == "__main__":
    main()

"""Rebuild map.pkl and/or profile.pkl from the pinned sqlglot, refusing to trust a bad run.

usage: buildmaps.py [--what map|profile|both] [--out-dir DIR] [--workers N]

Needs work/sqlglot checked out at the pin (clean) and work/venv with kit/requirements.lock.
Every coverage pass runs the whole suite with build/covplugin.py and build/failplugin.py, in
forward and reverse order (state cached by one test must not hide a read from the next). A pass
counts only if pytest exits 0 and failplugin's record shows every catalogued test reporting and
none failing; mkmap.py / mkprofile.py re-check those records and that every test was traced
before they write anything, and they write atomically. On any failure nothing is replaced.

  map      2 plain coverage passes  -> map.pkl     (lines, fixtures, classes, import-time facts)
  profile  2 instrumented passes    -> profile.pkl (dictionary-entry reads, is/== agreement)
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("WORK", R / "work"))
SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
TESTS = R / "kit" / "sample" / "tests.json"


def venv_python():
    for cand in (WORK / "venv" / "bin" / "python", WORK / "venv" / "Scripts" / "python.exe"):
        if cand.exists():
            return str(cand)
    sys.exit("work/venv is missing: create it with uv and kit/requirements.lock")


def check_checkout(tree):
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != SHA:
        sys.exit(f"{tree} is at {head}, not the pinned {SHA}")
    dirty = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        sys.exit(f"{tree} has local modifications:\n{dirty}")


def run_pass(py, tree, cov_out, runs, rev, profile, workers, name=None):
    name = name or f"{'profile' if profile else 'map'}-{rev}"
    fail_out = runs / name
    shutil.rmtree(fail_out, ignore_errors=True)
    sep = ";" if os.name == "nt" else ":"
    # 2-process pools and a memory cap for the whole run, as in label.py: the tests' default pools
    # spawn one interpreter per CPU per xdist worker, which can exhaust a desktop machine. Pool size
    # does not change what is recorded (work is attributed per submitted task).
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=f".{sep}{R / 'build'}", COV_ROOT=str(tree), COV_OUT=str(cov_out),
               COV_REVERSE=str(rev), COV_PROFILE="1" if profile else "0", FAIL_OUT=str(fail_out),
               PYTHON_CPU_COUNT=os.environ.get("LABEL_CPU_COUNT", "2"))
    log = runs / f"{name}.log"
    cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "covplugin", "-p", "failplugin"]
    if workers != "serial":
        cmd += ["-n", str(workers)]
    cmd += ["tests"]
    sys.path.insert(0, str(R / "build"))
    import label
    with open(log, "wb") as f:
        if os.name == "nt":
            job = label._Job(int(os.environ.get("BUILD_MEMORY_MB", "12000")))
            p = subprocess.Popen(cmd, cwd=tree, env=env, stdout=f, stderr=subprocess.STDOUT, creationflags=0x4)
            job.adopt(p)
            try:
                rc = p.wait()
            finally:
                print(f"  peak memory {job.peak_mb()} MB", flush=True)
                job.close()
        else:
            rc = subprocess.run(cmd, cwd=tree, env=env, stdout=f, stderr=subprocess.STDOUT,
                                preexec_fn=label._limit_address_space).returncode
    tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:]
    print(f"pass {name}: exit {rc} {tail}", flush=True)
    # a reverse pass may exit 1 for the documented order-dependent pristine failures only;
    # mkmap.py / mkprofile.py check the exact set from the failplugin record
    if rc != 0 and not (rev == 1 and rc == 1):
        print(log.read_text(encoding="utf-8", errors="replace")[-3000:], file=sys.stderr)
        sys.exit(f"coverage pass failed (exit {rc}); nothing was replaced")
    return fail_out / "events.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=("map", "profile", "both"), default="both")
    ap.add_argument("--out-dir", default=str(R))
    ap.add_argument("--workers", default="auto")
    a = ap.parse_args()
    py = venv_python()
    tree = WORK / "sqlglot"
    check_checkout(tree)
    for pc in tree.rglob("__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    runs = WORK / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    out_dir = Path(a.out_dir)
    for what in (("map", "profile") if a.what == "both" else (a.what,)):
        profile = what == "profile"
        cov_out = WORK / f"cov-{what}"
        shutil.rmtree(cov_out, ignore_errors=True)
        markers = [run_pass(py, tree, cov_out, runs, rev, profile, a.workers) for rev in (0, 1)]
        flags = [x for m in markers for x in ("--pristine-ok", str(m))]
        if profile:
            # divergence check: one serial order, plain and instrumented; a test whose executed
            # lines differ took another path under instrumentation (e.g. `type(d) is dict`)
            div = []
            for kind, prof in (("plain", False), ("instr", True)):
                d = WORK / f"cov-div-{kind}"
                shutil.rmtree(d, ignore_errors=True)
                mk = run_pass(py, tree, d, runs, 0, prof, "serial", name=f"div-{kind}")
                flags += ["--pristine-ok", str(mk)] if prof else []
                div.append(str(d))
            cmd = [py, str(R / "build" / "mkprofile.py"), str(cov_out), str(TESTS), str(out_dir / "profile.pkl"),
                   *flags, "--divergence", *div]
        else:
            cmd = [py, str(R / "build" / "mkmap.py"), str(cov_out), str(tree), str(TESTS), str(out_dir / "map.pkl"), *flags]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            sys.exit(f"{what}: merge refused (exit {rc}); nothing was replaced")
        print(f"{what}: written to {out_dir}", flush=True)


if __name__ == "__main__":
    main()

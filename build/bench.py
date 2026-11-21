"""End-to-end benchmark: run a packaged selector like the container does and score it with the
official kit scorer.

usage:
  bench.py app   <app_dir> [--src SQLGLOT_CHECKOUT | --tar SRC_TAR_GZ] [--selector F] [--map F]
  bench.py run   <app_dir> <data_dir> <answers.json> [--runs N] [--label L] [--out DIR]
                 [--env K=V ...] [--python EXE]

`app` lays out /app the way the Dockerfile does: selector.py, a decompressed map.pkl, src/ with
sqlglot/ and tests/*.py (plus LICENSE), byte-compiled, then made read-only.

`run` starts the selector in a fresh, empty temp dir with a clean environment (HOME and TMP point
there, no site-packages, -S), captures stdout, and measures for every run separately:
  simulated  - official score.py on the saved stdout (exact, includes 300 s per miss)
  misses     - missed commit ids, from score.py's evaluate_run
  cpu        - user+system CPU of the whole process tree (Linux: RUSAGE_CHILDREN of the waited
               tree; Windows: a Job Object's accounting, which includes exited descendants)
  wall       - monotonic seconds from start to exit
  bytes      - stdout size (the run is rejected above 2 MB)
plus independent format checks (one JSON line per commit, <= 8 workers, unique known numbers).
The summary reports the median total (simulated + CPU) the way the board takes the middle run.
"""
import argparse
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from pathlib import Path

R = Path(__file__).resolve().parent.parent
KIT = R / "kit"
STDOUT_LIMIT = 2 * 1024 * 1024


def load_scorer():
    spec = importlib.util.spec_from_file_location("kit_score", KIT / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- app layout


def _copy_tree_filtered(src_root, dst_root):
    src_root, dst_root = Path(src_root), Path(dst_root)
    for top in ("sqlglot",):
        shutil.copytree(src_root / top, dst_root / top, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for p in sorted((src_root / "tests").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(src_root)
        (dst_root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst_root / rel)
    if (src_root / "LICENSE").exists():
        shutil.copyfile(src_root / "LICENSE", dst_root / "LICENSE")


def make_app(app, src=None, tar=None, selector=None, map_=None, python=None, profile=None):
    app = Path(app)
    if app.exists():
        for p in app.rglob("*"):
            try:
                os.chmod(p, 0o700 if p.is_dir() else 0o600)
            except OSError:
                pass
        shutil.rmtree(app)
    app.mkdir(parents=True)
    shutil.copyfile(selector or R / "selector.py", app / "selector.py")
    raw = Path(map_ or R / "map.pkl").read_bytes()
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    (app / "map.pkl").write_bytes(raw)
    if profile:
        raw = Path(profile).read_bytes()
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        (app / "profile.pkl").write_bytes(raw)
    if tar:
        with tarfile.open(tar) as t:
            t.extractall(app / "src", filter="data")
    else:
        _copy_tree_filtered(src or R / "work" / "sqlglot", app / "src")
    py = python or sys.executable
    subprocess.run([py, "-m", "compileall", "-q", str(app / "src" / "sqlglot"), str(app / "selector.py")], check=True)
    # read-only like the image's runtime filesystem
    for p in app.rglob("*"):
        if p.is_file():
            os.chmod(p, 0o444)
    print("app ready:", app)


# ---------------------------------------------------------------- CPU of a process tree


class _WinJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll")
        self.k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.job = self.k32.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("CreateJobObject failed")

        class Info(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
        self.Info = Info

    def assign_and_resume(self, proc):
        h = int(proc._handle)
        if not self.k32.AssignProcessToJobObject(self.job, h):
            raise OSError("AssignProcessToJobObject failed: %d" % self.ct.get_last_error())
        self.ntdll.NtResumeProcess(h)

    def cpu_us(self):
        info = self.Info()
        ok = self.k32.QueryInformationJobObject(self.job, 1, self.ct.byref(info), self.ct.sizeof(info), None)
        if not ok:
            raise OSError("QueryInformationJobObject failed")
        return (info.TotalUserTime + info.TotalKernelTime) // 10, info.TotalProcesses


def run_once(app, data, python, extra_env, timeout):
    app, data = Path(app).resolve(), Path(data).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="br-bench-"))
    out_path, err_path = tmp / "stdout.jsonl", tmp / "stderr.txt"
    env = {"HOME": str(tmp), "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp),
           "PYTHONDONTWRITEBYTECODE": "1", "BR_DATA": str(data), "PATH": os.environ.get("PATH", "")}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\WINDOWS")
    env.update(extra_env)
    code = f"import sys; sys.path.insert(0, {str(app)!r}); import selector; selector.main()"
    cmd = [python, "-S", "-c", code]
    nprocs = None
    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        if os.name == "nt":
            job = _WinJob()
            t0 = time.monotonic()
            p = subprocess.Popen(cmd, cwd=tmp, env=env, stdout=fo, stderr=fe, creationflags=0x4)  # CREATE_SUSPENDED
            job.assign_and_resume(p)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                rc = "timeout"
            wall = time.monotonic() - t0
            cpu, nprocs = job.cpu_us()
        else:
            import resource
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            t0 = time.monotonic()
            p = subprocess.Popen(cmd, cwd=tmp, env=env, stdout=fo, stderr=fe)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
                rc = "timeout"
            wall = time.monotonic() - t0
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = round((r1.ru_utime - r0.ru_utime + r1.ru_stime - r0.ru_stime) * 1e6)
    stdout = out_path.read_bytes()
    stderr = err_path.read_text(encoding="utf-8", errors="replace")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"rc": rc, "stdout": stdout, "stderr": stderr, "cpu_us": cpu, "wall_s": wall, "procs": nprocs}


def check_format(stdout, tests, commits):
    """Independent of score.py: the properties the brief asks to verify, returned as problems."""
    problems = []
    if len(stdout) >= STDOUT_LIMIT:
        problems.append(f"stdout {len(stdout)} bytes >= 2 MB")
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return problems + ["not utf-8"]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    nums = {t["number"] for t in tests}
    seen = set()
    for i, ln in enumerate(lines, 1):
        try:
            v = json.loads(ln)
        except ValueError:
            problems.append(f"line {i} not json")
            continue
        if not isinstance(v, dict) or set(v) != {"commit", "workers"}:
            problems.append(f"line {i} wrong shape")
            continue
        c = v["commit"]
        if c in seen:
            problems.append(f"line {i} repeats {c}")
        seen.add(c)
        ws = [w for w in v["workers"] if w]
        if len(ws) > 8:
            problems.append(f"{c}: {len(ws)} workers")
        flat = [x for w in ws for x in w]
        if any(type(x) is not int or x not in nums for x in flat):
            problems.append(f"{c}: unknown or non-integer test number")
        if len(flat) != len(set(flat)):
            problems.append(f"{c}: repeated test number")
    if seen != set(commits):
        problems.append(f"commits answered {len(seen)} != {len(commits)}")
    return problems


def run(args):
    score = load_scorer()
    data = Path(args.data)
    tests = json.loads((data / "tests.json").read_text("utf-8"))["tests"]
    answers = json.loads(Path(args.answers).read_text("utf-8"))["commits"]
    commits = sorted(p.stem for p in (data / "commits").glob("*.patch"))
    if set(commits) != set(answers):
        sys.exit("answers do not cover the same commits as the patches")
    extra = dict(kv.split("=", 1) for kv in args.env)
    python = args.python or sys.executable
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for i in range(args.runs):
        r = run_once(args.app, data, python, extra, args.timeout)
        ev = score.evaluate_run(r["stdout"], tests, answers)
        probs = check_format(r["stdout"], tests, commits)
        rec = {"label": args.label, "run": i, "rc": r["rc"], "valid": bool(ev["ok"]) and not probs and r["rc"] == 0,
               "reject": None if ev["ok"] else ev, "format_problems": probs,
               "simulated_ms": ev.get("simulated_milliseconds"), "missed": ev.get("missed_commits"),
               "cpu_us": r["cpu_us"], "wall_s": round(r["wall_s"], 3), "stdout_bytes": len(r["stdout"]),
               "procs": r["procs"]}
        if rec["simulated_ms"] is not None:
            rec["total_us"] = score.run_total_microseconds(rec["simulated_ms"], rec["cpu_us"])
        records.append(rec)
        if out_dir:
            (out_dir / f"{args.label}-run{i}.jsonl").write_bytes(r["stdout"])
            (out_dir / f"{args.label}-run{i}.err").write_text(r["stderr"], encoding="utf-8")
        print(json.dumps({k: v for k, v in rec.items()}), flush=True)
    ok = [r for r in records if r["valid"]]
    summary = {"label": args.label, "runs": len(records), "valid_runs": len(ok)}
    if ok:
        totals = sorted(r["total_us"] for r in ok)
        mid = totals[(len(totals) - 1) // 2]   # the middle run (lower middle for an even count)
        summary.update({
            "simulated_s": [r["simulated_ms"] / 1000 for r in ok],
            "misses": [len(r["missed"]) for r in ok],
            "missed": sorted({c for r in ok for c in r["missed"]}),
            "cpu_s": [round(r["cpu_us"] / 1e6, 3) for r in ok],
            "wall_s": [r["wall_s"] for r in ok],
            "stdout_bytes": [r["stdout_bytes"] for r in ok],
            "median_total_s": (mid + 50000) // 100000 / 10,
            "median_cpu_s": round(statistics.median(r["cpu_us"] for r in ok) / 1e6, 3),
        })
    print("SUMMARY " + json.dumps(summary), flush=True)
    if out_dir:
        with open(out_dir / "summary.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")
    return 0 if len(ok) == len(records) else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("app")
    a.add_argument("app")
    a.add_argument("--src")
    a.add_argument("--tar")
    a.add_argument("--selector")
    a.add_argument("--map")
    a.add_argument("--python")
    a.add_argument("--profile", help="profile.pkl to ship (default: none)")
    r = sub.add_parser("run")
    r.add_argument("app")
    r.add_argument("data")
    r.add_argument("answers")
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--label", default="run")
    r.add_argument("--out")
    r.add_argument("--env", action="append", default=[])
    r.add_argument("--python")
    r.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()
    if args.cmd == "app":
        make_app(args.app, args.src, args.tar, args.selector, args.map, args.python, args.profile)
        return 0
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

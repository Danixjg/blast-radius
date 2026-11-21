"""Label patches by running sqlglot's full test suite on them, refusing to guess.

usage: label.py <sqlglot_git_tree> <tests.json> <out_dir> [patch ...]   (no patch: pristine check)

Each patch is applied with `git apply` to a clean checkout of the pinned commit, the whole suite
runs under xdist with build/failplugin.py, and the tree is reset afterwards. The label records:

  status   complete          finish event, exit status 0/1, every catalogued test accounted for
           collection_abort  pytest stopped before running tests because a conftest failed to
                             load; every test in that conftest's directory fails
           incomplete        anything else: killed, crashed, interrupted, internal error, tests
                             that never reported. NEVER usable as a zero-failure label.
           unappliable       git apply refused the patch
  failing  test ids that failed (any phase, any subtest) or sit in a module/package that failed to
           collect (only meaningful for complete / collection_abort)

A pristine run (no patch) must come back complete with zero failures before labels are trusted.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

BUILD = Path(__file__).resolve().parent
PY = sys.executable
SUITE_TIMEOUT = float(os.environ.get("LABEL_TIMEOUT", "900"))
LAST_PEAK_MB = None
# resource exhaustion on the labelling machine: such runs are relabelled, never counted. A broken
# process pool on its own is not one: a patch can crash pool workers (deep recursion, for example)
# and the harness counts those tests as failing. Exhaustion shows as an explicit out-of-memory
# signal or as the run's peak memory reaching the job cap.
ENV_ERR = re.compile(r"MemoryError|Not enough memory|OutOfMemory|paging file is too small|Too many open files|"
                     r"Resource temporarily unavailable|No space left on device")
CONFTEST_ERR = re.compile(r"ImportError while loading conftest '([^']+)'|while loading conftest '([^']+)'")


def _git(tree, *args, check=True):
    return subprocess.run(["git", "-C", str(tree), *args], check=check, capture_output=True, text=True)


def reset(tree):
    _git(tree, "reset", "--hard", "-q")
    _git(tree, "clean", "-fdq", "-e", "__pycache__")


def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()


# Hard cap on the memory of one whole suite run (pytest, xdist workers and every process pool they
# spawn). A patch can make a test allocate without bound; the cap turns that into MemoryError
# inside the run (recorded as an environment failure, never a label) instead of exhausting the
# machine. Windows: a Job Object with a job-wide commit limit and kill-on-close (if this script
# dies, the whole run dies with it). Elsewhere: an address-space limit per process.
SUITE_MEMORY_MB = int(os.environ.get("LABEL_MEMORY_MB", "9000"))


class _Job:
    """Windows Job Object: job-wide memory limit, kill on close, peak memory readout."""

    def __init__(self, limit_mb):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        k32 = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.job = k32.CreateJobObjectW(None, None)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.Extended = Extended
        info = Extended()
        info.Basic.LimitFlags = 0x200 | 0x2000   # JOB_OBJECT_LIMIT_JOB_MEMORY | KILL_ON_JOB_CLOSE
        info.JobMemoryLimit = limit_mb * 1024 * 1024
        if not k32.SetInformationJobObject(self.job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("SetInformationJobObject failed: %d" % ctypes.get_last_error())

    def adopt(self, proc):
        h = int(proc._handle)
        if not self.k32.AssignProcessToJobObject(self.job, h):
            raise OSError("AssignProcessToJobObject failed: %d" % self.ct.get_last_error())
        self.ct.WinDLL("ntdll").NtResumeProcess(h)

    def peak_mb(self):
        info = self.Extended()
        self.k32.QueryInformationJobObject(self.job, 9, self.ct.byref(info), self.ct.sizeof(info), None)
        return info.PeakJobMemoryUsed // (1024 * 1024)

    def close(self):
        self.k32.CloseHandle(self.job)


def _limit_address_space():
    import resource
    lim = SUITE_MEMORY_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (lim, lim))


def run_suite(tree, workers=8):
    tree = Path(tree).resolve()
    res = tree / ".labelrun"
    shutil.rmtree(res, ignore_errors=True)
    sep = ";" if os.name == "nt" else ":"
    # PYTHON_CPU_COUNT (Python 3.13+) sizes the tests' default ProcessPoolExecutors: on a 12-thread
    # machine each optimizer/executor pool would spawn 12 interpreters per xdist worker (>12 GB of
    # commit at 4 workers). 2 matches the scorer's 2-core containers; results do not depend on it
    # (checked against the official sample answers).
    env = dict(os.environ, SKIP_INTEGRATION="0", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=f"{tree}{sep}{BUILD}", FAIL_OUT=str(res),
               PYTHON_CPU_COUNT=os.environ.get("LABEL_CPU_COUNT", "2"))
    cmd = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "failplugin", "-n", str(workers),
           "--timeout", "120", "--continue-on-collection-errors", "tests"]
    job = None
    if os.name == "nt":
        job = _Job(SUITE_MEMORY_MB)
        kw = {"creationflags": 0x4}   # CREATE_SUSPENDED: joined to the job before it runs anything
    else:
        kw = {"start_new_session": True, "preexec_fn": _limit_address_space}
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    if job is not None:
        job.adopt(p)
    try:
        out, _ = p.communicate(timeout=SUITE_TIMEOUT)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        out, _ = p.communicate()
        rc = "timeout"
    finally:
        if job is not None:
            global LAST_PEAK_MB
            LAST_PEAK_MB = job.peak_mb()
            job.close()   # kill-on-close: no process of this run outlives it
    events = []
    ev_file = res / "events.jsonl"
    if ev_file.exists():
        for ln in ev_file.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(ln))
            except ValueError:
                pass   # a torn last line from a killed run
    shutil.rmtree(res, ignore_errors=True)
    return rc, out.decode("utf-8", errors="replace"), events, time.monotonic() - t0


def _scope_tests(scope, catalog_ids):
    """Catalogued test ids inside a collection scope (a module nodeid or a directory)."""
    scope = scope.replace("\\", "/").rstrip("/")
    if scope in ("", "."):
        return set(catalog_ids)
    return {t for t in catalog_ids if t.split("::")[0] == scope or t.startswith(scope + "/")}


def classify(rc, out, events, catalog_ids, touched_test_files, tree):
    fails = {e["nodeid"] for e in events if e["event"] == "fail"}
    collect = [e["nodeid"] for e in events if e["event"] == "collect_error"]
    done = {e["nodeid"] for e in events if e["event"] == "done"}
    finish = [e for e in events if e["event"] == "finish"]
    failing = {t for t in fails if t in catalog_ids}
    in_collect_err = set()
    for c in collect:
        in_collect_err |= _scope_tests(c.split("::")[0], catalog_ids)
    failing |= in_collect_err
    info = {"rc": rc, "collect_errors": collect, "extra_fail_ids": sorted(fails - set(catalog_ids))}

    m = CONFTEST_ERR.search(out)
    if not finish and m:
        path = Path(m.group(1) or m.group(2))
        try:
            rel = path.resolve().relative_to(Path(tree).resolve()).parent.as_posix()
        except ValueError:
            rel = ""
        info.update(status="collection_abort", abort_scope=rel)
        return sorted(_scope_tests(rel, catalog_ids)), info
    if not finish or rc not in (0, 1) or finish[-1]["exitstatus"] not in (0, 1):
        info.update(status="incomplete", reason=f"rc={rc} finish={bool(finish)}", tail=out[-600:])
        return sorted(failing), info
    env = ENV_ERR.search(out)
    at_cap = LAST_PEAK_MB is not None and LAST_PEAK_MB >= 0.95 * SUITE_MEMORY_MB
    if env or at_cap:
        # the run ran out of something (memory, processes, files): its failures say nothing
        why = env.group(0) if env else f"peak {LAST_PEAK_MB} MB reached the {SUITE_MEMORY_MB} MB cap"
        info.update(status="incomplete", reason=f"environment failure: {why}", tail=out[-600:])
        return sorted(failing), info
    crashed = sorted({e["nodeid"] for e in events if e["event"] == "fail" and e.get("when") not in ("setup", "call", "teardown")})
    if crashed:
        # an xdist worker died under the test (on Windows pytest-timeout's thread method kills
        # the worker): a timeout here depends on this machine's load, so it is not a label
        info.update(status="incomplete", reason=f"worker crash/timeout in {len(crashed)} tests", crashed=crashed[:20])
        return sorted(failing), info
    unaccounted = set(catalog_ids) - done - in_collect_err
    # a test whose own file the patch touched may legitimately vanish (renamed/removed test)
    vanished = {t for t in unaccounted if t.split("::")[0] in touched_test_files}
    unaccounted -= vanished
    info["vanished"] = sorted(vanished)
    if unaccounted:
        info.update(status="incomplete", reason=f"{len(unaccounted)} tests never reported",
                    unaccounted=sorted(unaccounted)[:20])
        return sorted(failing), info
    info["status"] = "complete"
    return sorted(failing), info


def touched_files(patch_text):
    files = set()
    for ln in patch_text.splitlines():
        m = re.match(r"diff --git a/(\S+) b/(\S+)", ln)
        if m:
            files.update(m.groups())
    return files


def label_patch(tree, patch_path, catalog_ids, workers=8):
    tree = Path(tree).resolve()
    reset(tree)
    text = Path(patch_path).read_text(encoding="utf-8") if patch_path else ""
    if text:
        ap = subprocess.run(["git", "-C", str(tree), "apply", "--whitespace=nowarn", str(Path(patch_path).resolve())],
                            capture_output=True, text=True)
        if ap.returncode != 0:
            reset(tree)
            return {"status": "unappliable", "failing": [], "error": ap.stderr[-400:]}
    try:
        rc, out, events, secs = run_suite(tree, workers)
    finally:
        reset(tree)
    failing, info = classify(rc, out, events, catalog_ids, touched_files(text), tree)
    info["failing"] = failing
    info["seconds"] = round(secs, 1)
    info["peak_mb"] = LAST_PEAK_MB
    return info


def catalog(tests_json):
    d = json.loads(Path(tests_json).read_text(encoding="utf-8"))
    return [t["id"] for t in d["tests"]]


def main():
    tree, tests_json, out_dir, *patches = sys.argv[1:]
    ids = catalog(tests_json)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for pc in list(Path(tree).rglob("__pycache__")):
        shutil.rmtree(pc, ignore_errors=True)
    workers = int(os.environ.get("LABEL_WORKERS", "8"))
    pristine = label_patch(tree, None, ids, workers)
    print("pristine", pristine["status"], len(pristine["failing"]), pristine["seconds"], flush=True)
    (out / "_pristine.json").write_text(json.dumps(pristine, indent=1))
    if pristine["status"] != "complete" or pristine["failing"]:
        sys.exit("pristine run is not a complete zero-failure run; refusing to label")
    for p in patches:
        pid = Path(p).stem
        dst = out / f"{pid}.json"
        if dst.exists():
            continue
        lab = label_patch(tree, p, ids, workers)
        lab["patch"] = Path(p).name
        dst.write_text(json.dumps(lab, indent=1))
        print(pid, lab["status"], len(lab["failing"]), lab.get("seconds"), flush=True)


if __name__ == "__main__":
    main()

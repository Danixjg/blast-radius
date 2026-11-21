"""Probe parity: every import probe the selector would run, fresh and through the probe server.

usage: probe_parity.py <commits_dir> [<commits_dir> ...] [--timeout S] [--repeat N] [--checkpoints K]

For each patch the hunter decides whether it needs an import probe. Every such probe runs as a
fresh interpreter and through the probe server; the verdicts must be identical (exit status 1
otherwise). It prints the mismatches, how many probes were eligible for the server, and the CPU
(user+system, all descendants) of each path. No labels needed: the reference is the fresh probe.
Uses the committed map/profile and the pinned checkout (BR_SRC, default work/sqlglot).
"""
import argparse
import collections
import glob
import os
import resource
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402


def child_cpu():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def positive_int(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--timeout", type=float, default=S.PROBE_TIMEOUT_S)
    ap.add_argument("--repeat", type=positive_int, default=1)
    ap.add_argument("--checkpoints", type=int, default=S.PROBE_CHECKPOINTS)
    return ap.parse_args(argv)


def main():
    a = parse_args()
    S.SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot")).resolve()
    data = S.load_map()
    h = S.Hunter(data, S.load_profile(data["tests"]))
    jobs = {}
    for d in a.dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.patch"))):
            try:
                _, probe = h.analyze(Path(p).read_bytes().decode("utf-8", "surrogateescape"))
            except Exception as e:
                print("hunter failed:", p, repr(e), file=sys.stderr)
                continue
            if probe is not None:
                jobs[p] = probe
    print(f"{len(jobs)} probe jobs from {len(a.dirs)} director{'y' if len(a.dirs) == 1 else 'ies'}")

    bad, cpu_fresh, cpu_server = [], 0.0, 0.0
    for _ in range(a.repeat):
        c0 = child_cpu()
        fresh = {k: S.run_probe(f, a.timeout) for k, f in jobs.items()}
        c1 = child_cpu()
        server = S.ProbeServer.start(extra=a.checkpoints)
        if server is None:
            sys.exit("probe server unavailable")
        served = {k: S.run_probe(f, a.timeout, server=server) for k, f in jobs.items()}
        eligible = sum(server.eligible(f) for f in jobs.values())
        forked = server.forked
        per_stage = [st.forked for st in server.stages]
        server.close()
        c2 = child_cpu()
        cpu_fresh += c1 - c0
        cpu_server += c2 - c1
        bad += [(k, fresh[k], served[k]) for k in jobs if fresh[k] != served[k]]

    print("verdicts (fresh):", dict(collections.Counter(fresh.values())))
    print(f"eligible for the server: {eligible}/{len(jobs)}, forked: {forked}, per checkpoint: {per_stage}")
    n = a.repeat
    print(f"CPU per pass: fresh {cpu_fresh / n:.3f} s, server {cpu_server / n:.3f} s, "
          f"saving {(cpu_fresh - cpu_server) / n:.3f} s ({(cpu_fresh - cpu_server) / max(cpu_fresh, 1e-9):.0%})")
    for k, f, s in bad:
        print("MISMATCH", k, "fresh", f, "server", s)
    print("parity:", "FAIL" if bad else "OK")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

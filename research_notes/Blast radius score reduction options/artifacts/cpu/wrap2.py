
import sys, time, json, os
def cg():
    with open('/sys/fs/cgroup/cpu.stat') as f:
        for l in f:
            if l.startswith('usage_usec'): return int(l.split()[1]) / 1e6
C0 = cg()
sys.path.insert(0, '/app')
import selector as S
C1 = cg()
T = {"import_selector": C1 - C0}
cat = json.load(open('/data/tests.json'))['tests']
SEC = {t['id']: t['seconds'] for t in cat}
def wrap(obj, name, label):
    f = getattr(obj, name)
    def g(*a, **k):
        c = cg()
        try: return f(*a, **k)
        finally: T[label] = T.get(label, 0) + cg() - c
    setattr(obj, name, g)
for n in ('load_map', 'load_profile'): wrap(S, n, n)
wrap(S.Hunter, '__init__', 'hunter_init'); wrap(S.Hunter, 'analyze', 'analyze')
_rp = S.run_probes
S.run_probes = lambda jobs, parallel=1, lazy=None: _rp(jobs, 1, lazy)
wrap(S, 'run_probes', 'run_probes')
_rv = S.run_verifications
S.run_verifications = lambda jobs, parallel=1: _rv(jobs, 1)
wrap(S, 'verify', 'verify')
_ss = S.ProbeServer.start.__func__
def ss(cls, extra=None):
    c = cg(); r = _ss(cls, extra); T['server_start'] = cg() - c; return r
S.ProbeServer.start = classmethod(ss)
_sc = S.ProbeServer.close
def sc(self):
    c = cg(); _sc(self); T['server_close'] = cg() - c
S.ProbeServer.close = sc
_vr = S.verify_report
VR = []
def vr(files, tests, timeout):
    c = cg(); t0 = time.monotonic(); rep = _vr(files, tests, timeout)
    VR.append({"n": len(tests), "ms": round(sum(SEC.get(i, 0) for i, _ in tests) * 1000), "cpu": round(cg() - c, 4),
               "wall": round(time.monotonic() - t0, 3), "npass": sum(v == "pass" for v in (rep or {}).values()),
               "files": sorted({i.split('::')[0] for i, _ in tests})})
    return rep
S.verify_report = vr
_pr = S.probe_report
PR = []
def pr(files, timeout, server=None):
    el = server is not None and server.eligible(files)
    st = server.stage_for(files) if el else None
    c = cg(); res = _pr(files, timeout, server)
    PR.append({"stage": st, "res": res[0], "cpu": round(cg() - c, 4), "files": sorted(files)}); return res
S.probe_report = pr
S.main()
C2 = cg()
print("WRAP2 " + json.dumps({"phases": T, "total_in_python": C2 - C0, "verify_runs": VR, "probes": PR}), file=sys.stderr)

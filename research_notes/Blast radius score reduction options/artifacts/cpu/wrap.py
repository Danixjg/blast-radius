
import sys, time, resource, json, os
def ru():
    s = resource.getrusage(resource.RUSAGE_SELF); c = resource.getrusage(resource.RUSAGE_CHILDREN)
    return [s.ru_utime + s.ru_stime, c.ru_utime + c.ru_stime, time.monotonic()]
R0 = ru()
sys.path.insert(0, '/app')
import selector as S
R1 = ru()
T = {"import_selector": [R1[0]-R0[0], 0, R1[2]-R0[2], 1]}
def wrap(obj, name, label):
    f = getattr(obj, name)
    def g(*a, **k):
        s0 = ru()
        try:
            return f(*a, **k)
        finally:
            s1 = ru(); d = T.setdefault(label, [0, 0, 0, 0])
            for i in range(3): d[i] += s1[i] - s0[i]
            d[3] += 1
    setattr(obj, name, g)
wrap(S, 'load_map', 'load_map')
wrap(S, 'load_profile', 'load_profile')
wrap(S.Hunter, '__init__', 'hunter_init')
wrap(S.Hunter, 'analyze', 'analyze')
wrap(S, 'run_probes', 'run_probes')
wrap(S, 'verify', 'verify')
wrap(S, 'schedule', 'schedule')
_vr = S.verify_report
VR = []
def vr(files, tests, timeout):
    t0 = time.monotonic(); rep = _vr(files, tests, timeout)
    VR.append([len(tests), round(time.monotonic()-t0, 3), rep is not None]); return rep
S.verify_report = vr
_pr = S.probe_report
PR = []
def pr(files, timeout, server=None):
    t0 = time.monotonic(); el = server is not None and server.eligible(files)
    res = _pr(files, timeout, server)
    PR.append([el, res[0], round(time.monotonic()-t0, 3)]); return res
S.probe_report = pr
S.main()
R2 = ru()
print("WRAP " + json.dumps({"phases": T, "self_total": R2[0], "children_total": R2[1], "wall": R2[2]-R0[2],
      "verify_runs": VR, "probes": PR}), file=sys.stderr)


import sys, os, json, math, inspect, functools
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
name, data_dir, ans_path, out = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
S.SRC = Path(os.environ.get("BR_SRC")); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"; S.DATA = data_dir
S.BUDGET_S = 10**6
CUR = {"logs": None, "stack": None, "calls": None}
_orig_log = S.log
def mylog(*a):
    s = " ".join(str(x) for x in a)
    if CUR["logs"] is not None: CUR["logs"].append(s)
    _orig_log(*a)
S.log = mylog
SKIP = {"__init__", "source", "tree"}
def wrap(nm, f):
    @functools.wraps(f)
    def w(*a, **k):
        st = CUR["stack"]
        if st is None: return f(*a, **k)
        rec = {"n": nm, "ch": [], "m": 0}
        (st[-1]["ch"] if st else CUR["calls"]).append(rec)
        st.append(rec)
        try:
            r = f(*a, **k)
        finally:
            st.pop()
        v = r[0] if isinstance(r, tuple) and r and isinstance(r[0], int) and not isinstance(r[0], bool) else r
        if isinstance(v, int) and not isinstance(v, bool): rec["m"] = v
        elif v is None and nm in ("eq_swap_mask","dict_entry_mask"): rec["none"] = True
        return r
    return w
for nm, v in list(vars(S.Hunter).items()):
    if nm in SKIP or not inspect.isfunction(v): continue
    setattr(S.Hunter, nm, wrap(nm, v))
EXP = os.environ.get("EXP", "")
if "nogroup" in EXP:
    S.Hunter.group_mask = lambda self, rel: 0
data = S.load_map()
h = S.Hunter(data, S.load_profile(data["tests"]))
catalog = json.loads((data_dir/"tests.json").read_text())["tests"]
ms = {t["number"]: int(round(t["seconds"]*1000)) for t in catalog}
num_of = {t["id"]: t["number"] for t in catalog}
idx_to_num = [num_of.get(t) for t in data["tests"]]
known = {n for n in idx_to_num if n is not None}
unmapped = [n for n in ms if n not in known]
answers = json.loads(ans_path.read_text())["commits"]
def nums_of(mask):
    s = set(); bits = mask & h.ALL
    while bits:
        b = bits & -bits; n = idx_to_num[b.bit_length()-1]
        if n is not None: s.add(n)
        bits ^= b
    return s
# origin attribution
def origins(calls, final_mask):
    res = {}  # test idx bit -> chain
    def walk(rec, chain):
        ch = chain + [rec["n"]]
        childm = 0
        for c in rec["ch"]:
            childm |= c["m"]
        own = rec["m"] & ~childm & final_mask
        if own:
            res.setdefault(">".join(ch), 0)
            res[">".join(ch)] |= own
        for c in rec["ch"]:
            walk(c, ch)
    for r in calls: walk(r, [])
    return res
recs = {}; selected = {}; probes = {}; lazy_pending = set(); verify_files = {}
for cid in sorted(answers):
    text = (data_dir/"commits"/f"{cid}.patch").read_bytes().decode("utf-8","surrogateescape")
    CUR.update(logs=[], stack=[], calls=[])
    mask, probe = h.analyze(text)
    calls, logs = CUR["calls"], CUR["logs"]
    CUR.update(logs=None, stack=None, calls=None)
    rec = {"logs": logs, "full": False}
    if mask and mask == h.ALL:
        selected[cid] = None; rec["full"] = True; rec["full_reason"] = "hunter ALL"
    else:
        nums = (set(unmapped) if mask else set()) | nums_of(mask)
        selected[cid] = nums
        org = origins(calls, mask)
        # chain -> test numbers (a bit can be own in several chains; keep all)
        rec["chains"] = {k: sorted(nums_of(v)) for k, v in org.items()}
        rec["unmapped_added"] = bool(mask) and bool(unmapped)
    if probe is not None:
        probes[cid] = probe
        if h.lazy_pending: lazy_pending.add(cid)
    if h.verify_files is not None: verify_files[cid] = h.verify_files
    rec["probe"] = probe is not None
    recs[cid] = rec
CUR["logs"] = []
if probes:
    lazy_seen = {}
    outc = S.run_probes(probes, lazy=lazy_seen)
    for cid, res in outc.items():
        recs[cid]["probe_res"] = res
        if res != "ok":
            selected[cid] = None; recs[cid]["full"] = True; recs[cid]["full_reason"] = "probe " + str(res)
            alt = 0
            for rel in probes[cid]:
                if rel.startswith("sqlglot/"):
                    alt |= h.file_mask(rel) | h.group_mask(rel) | h.classes_of_module(rel) if hasattr(h, "classes_of_module") else h.file_mask(rel) | h.group_mask(rel)
            alt |= h.d["subproc"]
            altn = nums_of(alt) | set(unmapped)
            fl = set(answers[cid]["failing"])
            recs[cid]["alt_probe"] = {"n": len(altn), "cost": S.schedule([(n, ms[n]) for n in altn])[0], "missed": len(fl - altn), "nfail": len(fl)}
        elif cid in lazy_pending and selected.get(cid) is not None:
            if lazy_seen.get(cid) is not True:
                add = {idx_to_num[i] for i in range(h.n) if h.lazy_mask >> i & 1} - {None}
                recs[cid]["lazy_added"] = sorted(add)
                selected[cid].update(add); selected[cid].update(unmapped)
pre = {c: (None if v is None else set(v)) for c, v in selected.items()}
if verify_files:
    S.verify(h, catalog, selected, verify_files, ms, idx_to_num)
glob_logs = CUR["logs"]
allc = S.schedule(list(ms.items()))[0]
for cid, rec in recs.items():
    fail = set(answers[cid]["failing"])
    sel = selected[cid]
    selnums = set(ms) if sel is None else sel
    p = pre[cid]; pnums = set(ms) if p is None else p
    rec["kind"] = answers[cid].get("kind","")
    rec["nfail"] = len(fail); rec["nsel"] = len(selnums)
    rec["cost"] = allc if sel is None else S.schedule([(n, ms[n]) for n in sel])[0]
    rec["cost_pre_verify"] = allc if p is None else S.schedule([(n, ms[n]) for n in p])[0]
    rec["verified"] = (p is not None and sel is not None and p != sel) or any(cid in l and "verifier" in l for l in glob_logs)
    rec["oracle"] = S.schedule([(n, ms[n]) for n in fail])[0]
    rec["miss"] = bool(fail - selnums)
    rec["extra_s"] = sum(ms[n] for n in selnums - fail)
    rec["final"] = sorted(selnums) if sel is not None else None
    rec["failing"] = sorted(fail)
json.dump({"set": name, "recs": recs, "glob_logs": glob_logs, "all_cost": allc, "ms": ms,
           "unmapped": unmapped}, open(out, "w"))
tot = sum(r["cost"] for r in recs.values()) + 300000*sum(r["miss"] for r in recs.values())
print(name, len(recs), "sim", tot/1000, "oracle", sum(r["oracle"] for r in recs.values())/1000, "misses", sum(r["miss"] for r in recs.values()))

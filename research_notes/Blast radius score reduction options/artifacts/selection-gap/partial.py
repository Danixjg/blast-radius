
import json, sys, os
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
S.SRC = Path(os.environ["BR_SRC"]); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"
data = S.load_map(); h = S.Hunter(data, S.load_profile(data["tests"]))
RD = sys.argv[2]
W = "/home/danny/blast-radius/work"
sets = {"sample": str(R/"kit/sample"), "holdout1": W+"/holdout1/data", "holdout2": W+"/holdout2/data", "holdout2u": W+"/sets/holdout2u/data", "mutants": W+"/sets/mutants/data"}
for cap in (1500, 5000, 15000):
  for name, dd in sets.items():
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    cat = json.load(open(dd+"/tests.json"))["tests"]; num_of = {t["id"]: t["number"] for t in cat}
    idx_of = {num_of[t]: i for i, t in enumerate(data["tests"]) if t in num_of}
    sim_save = cpu = 0; n = 0
    for cid, r in d["recs"].items():
        if r["full"] or r["cost"] - r["oracle"] <= 0: continue
        text = open(f"{dd}/commits/{cid}.patch", encoding="utf-8", errors="surrogateescape").read()
        h.analyze(text)
        if h.verify_files is None: continue
        sel = set(r["final"]); fail = set(r["failing"])
        unv = {x for x in sel if x not in idx_of or h.unverifiable >> idx_of[x] & 1}
        ver = sel - unv
        if not unv or not ver: continue
        vms = sum(ms[x] for x in ver)
        if vms > cap: continue
        cost_after_expected_half = S.schedule([(x, ms[x]) for x in unv])[0]
        pass   # same policy shape as VERIFY
        new = unv | (ver & fail)
        sim_save += r["cost"] - S.schedule([(x, ms[x]) for x in new])[0]; cpu += 350 + vms; n += 1
    print("cap", cap/1000, name, "commits", n, "sim saved", sim_save/1000, "est CPU", cpu/1000, "net", round((sim_save-cpu)/1000,2))

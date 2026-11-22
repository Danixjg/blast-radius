
import json, sys, os
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
S.SRC = Path(os.environ["BR_SRC"]); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"
data = S.load_map(); h = S.Hunter(data, S.load_profile(data["tests"]))
RD = sys.argv[2]
W = "/home/danny/blast-radius/work"
sets = {"sample": str(R/"kit/sample"), "holdout1": W+"/holdout1/data", "holdout2": W+"/holdout2/data", "holdout2u": W+"/sets/holdout2u/data", "mutants": W+"/sets/mutants/data"}
for name, dd in sets.items():
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    cat = json.load(open(dd+"/tests.json"))["tests"]; num_of = {t["id"]: t["number"] for t in cat}
    idx_of = {num_of[t]: i for i, t in enumerate(data["tests"]) if t in num_of}
    buckets = {"eligible_all_verifiable_skipped_by_cost": [0,0,0], "eligible_has_unverifiable": [0,0,0], "not_eligible_patch": [0,0,0]}
    for cid, r in d["recs"].items():
        if r["full"] or r["cost"] - r["oracle"] <= 0: continue
        text = open(f"{dd}/commits/{cid}.patch", encoding="utf-8", errors="surrogateescape").read()
        h.analyze(text)
        gap = r["cost"] - r["oracle"]
        sel = set(r["final"])
        if h.verify_files is None: k = "not_eligible_patch"
        elif any(n not in idx_of or h.unverifiable >> idx_of[n] & 1 for n in sel): k = "eligible_has_unverifiable"
        else: k = "eligible_all_verifiable_skipped_by_cost"
        b = buckets[k]; b[0] += 1; b[1] += gap; b[2] += sum(ms[n] for n in sel)
    print(name, {k: (v[0], round(v[1]/1000,2), "test-s", round(v[2]/1000,2)) for k, v in buckets.items()})

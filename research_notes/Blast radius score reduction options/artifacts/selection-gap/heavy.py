
import json, sys, collections
sys.path.insert(0, sys.argv[1]); import selector as S
RD = sys.argv[2]
cat = json.load(open(sys.argv[3]))["tests"]
heavy = sorted(cat, key=lambda t: -t["seconds"])[:12]
print("heaviest tests:", [(t["number"], t["id"], t["seconds"]) for t in heavy])
H = {t["number"] for t in cat if t["seconds"] >= 5}
idof = {t["number"]: t["id"] for t in cat}
for name in ["sample","holdout1","holdout2","holdout2u","mutants"]:
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    agg = collections.Counter(); n = collections.Counter(); gap_tot = 0
    for cid, r in d["recs"].items():
        gap = r["cost"] - r["oracle"]; gap_tot += gap
        if r["full"] or gap <= 0: continue
        fail = set(r["failing"]); extra = set(r["final"]) - fail
        hv = sorted(extra & H, key=lambda x: -ms[x])
        if hv:
            # cost without the heavy non-failing tests
            c2 = S.schedule([(x, ms[x]) for x in set(r["final"]) - set(hv)])[0]
            agg[idof[hv[0]]] += r["cost"] - c2; n[idof[hv[0]]] += 1
    print(name, "gap", gap_tot/1000, "saved if heavy (>=5s) non-failing tests dropped:", {k: (n[k], round(v/1000,2)) for k, v in agg.most_common()})

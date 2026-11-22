
import json, sys
sys.path.insert(0, sys.argv[1]); import selector as S
RD = sys.argv[2]
for name in ["holdout1","holdout2u","mutants"]:
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    L = []
    for cid, r in d["recs"].items():
        if r["full"] or 1224 not in r["final"]: continue
        direct = any(1224 in v and k.split(">")[-1] in ("span_mask","file_mask") for k, v in r["chains"].items())
        if direct: continue
        new = set(r["final"]) - {1224}
        L.append((cid, r["nfail"], r["nsel"], round((r["cost"] - S.schedule([(n, ms[n]) for n in new])[0])/1000, 2)))
    print(name, sorted(L, key=lambda x: -x[3]))

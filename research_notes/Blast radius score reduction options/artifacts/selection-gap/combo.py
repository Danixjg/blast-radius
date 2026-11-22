
import json, sys
sys.path.insert(0, sys.argv[1]); import selector as S
RD = sys.argv[2]
for name in ["sample","holdout1","holdout2","holdout2u","mutants"]:
    base = json.load(open(f"{RD}/{name}.json"))
    d = json.load(open(f"{RD}/{name}.insttop.json")); ms = {int(k): v for k, v in d["ms"].items()}
    b0 = sum(r["cost"] + 300000*r["miss"] for r in base["recs"].values())
    tot = 0; mem = 0; eqf = 0
    for cid, r in d["recs"].items():
        c = r["cost"]
        if not r["full"] and 1224 in r["final"] and r["nsel"] <= 500 and not any(1224 in v and k.split(">")[-1] in ("span_mask","file_mask") for k, v in r["chains"].items()):
            c2 = S.schedule([(n, ms[n]) for n in set(r["final"]) - {1224}])[0]
            if 1224 in r["failing"]: c2 += 300000
            mem += c - c2; c = c2
        if cid == "h33727":
            eqf += c; c = 0
        tot += c + 300000*r["miss"]
    print(name, "base", b0/1000, "insttop+mem+eqNone", tot/1000, "delta", round((tot-b0)/1000,3), "(mem part", round(mem/1000,2), "eq part", round(eqf/1000,2), ")")

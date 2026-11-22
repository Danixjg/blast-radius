
import json, sys, collections
RD = sys.argv[1]
for t in (1490, 1106, 1224):
    for name in ["holdout1","holdout2u","mutants","sample"]:
        d = json.load(open(f"{RD}/{name}.json"))
        sel = fail = 0; cids=[]
        for cid, r in d["recs"].items():
            if r["full"]: continue
            if t in r["final"]:
                sel += 1
                if t in r["failing"]: fail += 1
                else: cids.append(cid)
            elif t in r["failing"]: fail += 100
        print(t, name, "selected(non-full)", sel, "failing", fail, cids[:20])


import json, sys, collections
sys.path.insert(0, sys.argv[1]); import selector as S
RD = sys.argv[2]
rows = json.load(open(f"{RD}/rows.json"))
for name in ["sample","holdout1","holdout2","holdout2u","mutants"]:
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    save = 0; nm = 0; miss = 0
    for cid, r in d["recs"].items():
        if r["full"] or 1224 not in r["final"]: continue
        direct = any(1224 in v and ("span_mask" in k.split(">")[-1] or "file_mask" in k.split(">")[-1]) for k, v in r["chains"].items())
        if direct: continue
        new = set(r["final"]) - {1224}
        save += r["cost"] - S.schedule([(n, ms[n]) for n in new])[0]; nm += 1
        if 1224 in r["failing"]: miss += 1
    rs = sorted(rows[name], key=lambda x: -x["gap"])
    g = sum(x["gap"] for x in rs)
    top10 = sum(x["gap"] for x in rs[:10])
    print(name, "drop generator_memory when only via inst/name chains:", nm, "commits", save/1000, "s, misses", miss, "| top10 share", round(top10/1000,2), "of", g/1000)
rs = sorted(rows["mutants"], key=lambda x: -x["gap"])[:15]
for x in rs:
    d = json.load(open(f"{RD}/mutants.json"))["recs"][x["cid"]]
    print(x["cid"], x["cls"], round(x["gap"]/1000,2), "or", x["oracle"]/1000, "nf", x["nfail"], "ns", x["nsel"], x["dom"], x["full_reason"], [n for n in (1490,1106,1224) if d["final"] and n in d["final"]])

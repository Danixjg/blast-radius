
import json, sys, collections
RD = sys.argv[1]
def short(ch):
    parts = ch.split(">")
    out=[]
    for p in parts:
        if p in ("analyze","analyze_file","_site_mask"): continue
        if out and out[-1]==p: continue
        out.append(p)
    # compress repeated resolve_stmt>resolve_name
    s=">".join(out)
    while "resolve_stmt>resolve_name>resolve_stmt>resolve_name" in s:
        s=s.replace("resolve_stmt>resolve_name>resolve_stmt>resolve_name","resolve_stmt>resolve_name")
    return s
for t in (1490, 1106, 1224):
    c = collections.Counter()
    for name in ["holdout1","holdout2u","mutants"]:
        d = json.load(open(f"{RD}/{name}.json"))
        for cid, r in d["recs"].items():
            if r["full"] or t not in r["final"] or t in r["failing"]: continue
            labs = sorted({short(k) for k, v in r.get("chains",{}).items() if t in v})
            c[" | ".join(labs)] += 1
    print(t); [print("  ", n, k) for k, n in c.most_common()]

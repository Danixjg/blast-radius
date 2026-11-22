
import json, sys, collections
sys.path.insert(0, sys.argv[1]); import selector as S
RD = sys.argv[2]
metas = {"holdout1": "/home/danny/blast-radius/work/holdout1/meta.json", "holdout2": "/home/danny/blast-radius/work/holdout2/meta.json"}
def short(ch):
    parts = ch.split(">")
    keep = [p for p in parts if p not in ("analyze","analyze_file","span_mask","file_mask","_site_mask","inst_mask","group_mask")]
    return ">".join(keep) or parts[-1]
out = {}
for name in ["sample","holdout1","holdout2","holdout2u","mutants"]:
    d = json.load(open(f"{RD}/{name}.json")); ms = {int(k): v for k, v in d["ms"].items()}
    meta = json.load(open(metas[name])) if name in metas else {}
    rows = []
    for cid, r in d["recs"].items():
        gap = r["cost"] - r["oracle"]
        cat = meta.get(cid, {}).get("category", r["kind"])
        note = meta.get(cid, {}).get("note", "")
        files = meta.get(cid, {}).get("files", [])
        if r["full"]:
            cls = "c_full"
        elif r["nfail"] == 0:
            cls = "a_harmless"
        else:
            cls = "b_breaking"
        # chain attribution for non-full
        dom = None; rule_save = {}
        if not r["full"] and r.get("chains"):
            fail = set(r["failing"]); final = set(r["final"])
            chains = {short(k): set() for k in r["chains"]}
            for k, v in r["chains"].items(): chains[short(k)] |= set(v)
            for k, v in chains.items():
                v &= final
                rest = set()
                for k2, v2 in chains.items():
                    if k2 != k: rest |= v2
                keep = (rest & final) | fail | (final - set().union(*chains.values()))
                c = S.schedule([(n, ms[n]) for n in keep])[0]
                rule_save[k] = (r["cost"] - c, len(v - fail))
            dom = max(rule_save, key=lambda k: rule_save[k][0]) if rule_save else None
        rows.append(dict(cid=cid, cls=cls, cat=cat, note=note, files=files, gap=gap, cost=r["cost"], pre=r["cost_pre_verify"],
                         oracle=r["oracle"], nfail=r["nfail"], nsel=r["nsel"], full_reason=r.get("full_reason"), probe=r.get("probe_res"),
                         verified=r["verified"], dom=dom, rule_save=rule_save, logs=r["logs"][:4], lazy=bool(r.get("lazy_added"))))
    out[name] = rows
json.dump(out, open(f"{RD}/rows.json","w"))


import json, sys, os
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
S.SRC = Path(os.environ["BR_SRC"]); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"
data = S.load_map(); h = S.Hunter(data, S.load_profile(data["tests"]))
W = "/home/danny/blast-radius/work"
cases = [("holdout1","h12112", W+"/holdout1/data", W+"/holdout1/answers.json"),("holdout2","h33744",W+"/holdout2/data",W+"/holdout2/answers.json"),("holdout2","h33712",W+"/holdout2/data",W+"/holdout2/answers.json"),("mutants","m2x018",W+"/sets/mutants/data",W+"/sets/mutants/answers.json"),("holdout2","h33727",W+"/holdout2/data",W+"/holdout2/answers.json")]
for name,cid,dd,ap in cases:
    cat = json.load(open(dd+"/tests.json"))["tests"]; idn = {t["number"]: t["id"] for t in cat}
    num_of = {t["id"]: t["number"] for t in cat}
    idx_of = {num_of[t]: i for i, t in enumerate(data["tests"]) if t in num_of}
    text = open(f"{dd}/commits/{cid}.patch").read()
    print("=====", name, cid); print(text[:900])
    fails = json.load(open(ap))["commits"][cid]["failing"]
    files = S.parse_patch(text)
    alt = h.d["subproc"]
    for fp in files:
        rel = fp.old or fp.new
        alt |= h.file_mask(rel) | h.group_mask(rel)
    miss = [idn[n] for n in fails if n not in idx_of or not (alt >> idx_of[n] & 1)]
    print("missed by alt:", miss[:10])
    if cid == "h33727":
        m, p = h.analyze(text); print("mask==ALL", m == h.ALL)

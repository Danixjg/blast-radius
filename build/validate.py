"""Fast in-process validation (one Hunter, no subprocess per run, no data-dir copies).

usage: validate.py [map.pkl]
Scores the sample commits and every labelled mutant in work/mutants: misses, and simulated
cost including commits that break nothing. Based on a script by the repo owner.
"""
import glob
import json
import math
import os
import pickle
import sys
import zlib

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R)
import selector as S  # noqa: E402

S.SRC = S.Path(os.environ.get("BR_SRC", os.path.join(R, "work/sqlglot")))
raw = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, "map.pkl"), "rb").read()
try:
    raw = zlib.decompress(raw)
except zlib.error:
    pass
d = pickle.loads(raw)
cat = json.load(open(os.path.join(R, "kit/sample/tests.json")))["tests"]
num_of = {t["id"]: t["number"] for t in cat}
ms = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in cat}
h = S.Hunter(d)
idx_to_num = [num_of.get(tid) for tid in d["tests"]]
known = {n for n in idx_to_num if n is not None}
unmapped = [n for n in num_of.values() if n not in known]

cases = []   # (name, patch text, failing numbers)
answers = json.load(open(os.path.join(R, "kit/sample/answers.json")))["commits"]
for cid, a in sorted(answers.items()):
    cases.append(("sample", cid, open(os.path.join(R, f"kit/sample/commits/{cid}.patch")).read(), set(a["failing"])))
for lf in sorted(glob.glob(os.path.join(R, "work/mutants/*.json"))):
    mid = os.path.basename(lf)[:-5]
    pf = lf[:-5] + ".patch"
    if os.path.exists(pf):
        lab = json.load(open(lf))["failing"]
        cases.append(("mutant", mid, open(pf).read(), {num_of[x] for x in lab if x in num_of}))

totals = {}
for group, cid, text, failing in cases:
    mask = h.analyze(text)
    nums = {idx_to_num[i] for i in range(h.n) if mask >> i & 1} - {None}
    if mask:
        nums.update(unmapped)
    miss = failing - nums
    cost, _ = S.schedule([(x, ms[x]) for x in nums])
    t = totals.setdefault(group, [0, 0, 0, 0])
    t[0] += 1
    t[1] += cost + (300000 if miss else 0)
    t[2] += bool(miss)
    t[3] += bool(failing)
    if miss:
        print("MISS", group, cid, "failing", len(failing), "selected", len(nums), "missed", sorted(miss)[:10])
for g, (n, cost, misses, breaking) in totals.items():
    print(f"{g}: n={n} breaking={breaking} misses={misses} simulated={cost / 1000:.3f}s avg={cost / 1000 / n:.2f}s")

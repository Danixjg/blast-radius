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
with open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, "map.pkl"), "rb") as f:
    raw = f.read()
try:
    raw = zlib.decompress(raw)
except zlib.error:
    pass
d = pickle.loads(raw)
def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


cat = json.loads(read(os.path.join(R, "kit/sample/tests.json")))["tests"]
num_of = {t["id"]: t["number"] for t in cat}
ms = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in cat}
h = S.Hunter(d)
idx_to_num = [num_of.get(tid) for tid in d["tests"]]
known = {n for n in idx_to_num if n is not None}
unmapped = [n for n in num_of.values() if n not in known]

nums_in_file = {}
for t in cat:
    nums_in_file.setdefault(t["file"], set()).add(t["number"])


def label_numbers(mid, labels):
    """Failing test ids -> numbers. A module that fails to collect fails every test in it."""
    out = set()
    for x in labels:
        if x.startswith("COLLECT::"):
            path = x[len("COLLECT::"):].split("::")[0]
            hit = {n for f, ns in nums_in_file.items() if f == path or f.startswith(path.rstrip("/") + "/") for n in ns}
            if not hit:
                print("WARN", mid, "collection failure matches no catalogued test:", x)
            out |= hit
        elif x in num_of:
            out.add(num_of[x])
        else:
            print("WARN", mid, "labelled test not in catalogue:", x)
    return out


cases = []   # (group, id, patch text, failing numbers)
answers = json.loads(read(os.path.join(R, "kit/sample/answers.json")))["commits"]
for cid, a in sorted(answers.items()):   # sample answers already hold test numbers
    cases.append(("sample", cid, read(os.path.join(R, f"kit/sample/commits/{cid}.patch")), set(a["failing"])))
for lf in sorted(glob.glob(os.path.join(R, "work/mutants/*.json"))):
    mid = os.path.basename(lf)[:-5]
    pf = lf[:-5] + ".patch"
    if not os.path.exists(pf):
        print("WARN", mid, "has labels but no patch; skipped")
        continue
    cases.append(("mutant", mid, read(pf), label_numbers(mid, json.loads(read(lf))["failing"])))

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

"""Fast in-process replay (one Hunter, real import probes) over labelled patch sets.

usage: validate.py [--map map.pkl] [--profile profile.pkl|none] [--set DIR ...] [-v]

Default sets: the sample (official answers). A --set DIR needs DIR/data/commits + DIR/answers.json
(build/answers.py writes them from label.py labels; incomplete labels are never included).
Prints misses and simulated cost per set. End-to-end numbers (CPU, wall, official scorer) come
from build/bench.py; this is for quick iteration only.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=str(R / "map.pkl"))
    ap.add_argument("--profile", default=str(R / "profile.pkl"))
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    S.SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot"))
    S.MAP = Path(a.map)
    S.PROFILE = Path(a.profile) if a.profile != "none" else Path(os.devnull + ".none")
    S.BUDGET_S = 10 ** 6   # replay: no batch deadline
    data = S.load_map()
    h = S.Hunter(data, S.load_profile(data["tests"]))
    sets = [("sample", R / "kit" / "sample", R / "kit" / "sample" / "answers.json")]
    for d in a.set:
        d = Path(d)
        sets.append((d.name, d / "data", d / "answers.json"))
    for name, data_dir, ans_path in sets:
        cat = json.loads((data_dir / "tests.json").read_text(encoding="utf-8"))["tests"]
        num_of = {t["id"]: t["number"] for t in cat}
        ms = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in cat}
        idx_to_num = [num_of.get(t) for t in data["tests"]]
        known = {n for n in idx_to_num if n is not None}
        unmapped = [n for n in num_of.values() if n not in known]
        answers = json.loads(ans_path.read_text(encoding="utf-8"))["commits"]
        total = misses = 0
        for cid, ans in sorted(answers.items()):
            text = (data_dir / "commits" / f"{cid}.patch").read_bytes().decode("utf-8", "surrogateescape")
            mask, probe = h.analyze(text)
            nums = {idx_to_num[i] for i in range(h.n) if mask >> i & 1} - {None}
            if mask:
                nums.update(unmapped)
            res = "-"
            if probe is not None:
                res = S.run_probe(probe, S.PROBE_TIMEOUT_S)
                if res != "ok":
                    nums = set(num_of.values())
            cost, _ = S.schedule([(x, ms[x]) for x in nums])
            miss = set(ans["failing"]) - nums
            total += cost + (300000 if miss else 0)
            misses += bool(miss)
            if miss or a.v:
                print(f"{'MISS' if miss else 'ok  '} {name} {cid} {ans.get('kind', '')}: failing {len(ans['failing'])} "
                      f"selected {len(nums)} cost {cost / 1000:.3f}s probe {res}" + (f" missed {sorted(miss)[:8]}" if miss else ""))
        print(f"{name}: n={len(answers)} misses={misses} simulated={total / 1000:.3f}s")


if __name__ == "__main__":
    main()

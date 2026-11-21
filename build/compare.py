"""Run every app on every labelled set with build/bench.py and print one comparison table.

usage: compare.py --app NAME=DIR [...] --set NAME=DATA_DIR,ANSWERS [...] [--runs 3] [--out DIR] [--python EXE]

Each (app, set) pair gets `--runs` fresh runs scored by the kit's score.py; the table shows the
simulated seconds, misses (with ids), median CPU of the whole process tree and the median total
(simulated + CPU) the way the board takes the middle run. All apps see identical inputs.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", action="append", required=True)
    ap.add_argument("--set", action="append", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(R / "work" / "compare"))
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in a.set:
        sname, rest = s.split("=", 1)
        data, answers = rest.split(",", 1)
        for app in a.app:
            aname, adir = app.split("=", 1)
            cmd = [sys.executable, str(R / "build" / "bench.py"), "run", adir, data, answers, "--runs", str(a.runs),
                   "--label", f"{aname}-{sname}", "--out", str(out / sname / aname), "--python", a.python]
            res = subprocess.run(cmd, capture_output=True, text=True)
            summ = [ln for ln in res.stdout.splitlines() if ln.startswith("SUMMARY ")]
            if not summ:
                print(res.stdout[-2000:], res.stderr[-2000:], file=sys.stderr)
                sys.exit(f"bench failed for {aname} on {sname}")
            d = json.loads(summ[-1][8:])
            d.update(app=aname, set=sname)
            rows.append(d)
            print(json.dumps(d), flush=True)
    (out / "table.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print()
    print("| set | version | valid runs | simulated (s) | misses | missed commits | CPU median (s) | total median (s) |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for d in rows:
        sim = sorted(set(d.get("simulated_s", [])))
        miss = sorted(set(d.get("misses", [])))
        print(f"| {d['set']} | {d['app']} | {d['valid_runs']}/{d['runs']} | {', '.join(f'{x:.3f}' for x in sim)} | "
              f"{', '.join(map(str, miss))} | {' '.join(d.get('missed', [])) or '-'} | {d.get('median_cpu_s', '-')} | "
              f"{d.get('median_total_s', '-')} |")


if __name__ == "__main__":
    main()

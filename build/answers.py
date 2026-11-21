"""Turn label.py labels into a data dir + answers.json the kit's score.py accepts.

usage: answers.py <set_dir> [--tests kit/sample/tests.json]

<set_dir>/commits/*.patch and <set_dir>/labels/*.json -> <set_dir>/data/{commits,tests.json} and
<set_dir>/answers.json. Only patches whose label is `complete` or `collection_abort` are scored;
every other patch (incomplete, unappliable, unlabelled) is left out and listed, never treated as
breaking nothing.
"""
import json
import shutil
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent


def main():
    args = sys.argv[1:]
    tests_json = R / "kit" / "sample" / "tests.json"
    if "--tests" in args:
        i = args.index("--tests")
        tests_json = Path(args[i + 1])
        del args[i:i + 2]
    d = Path(args[0])
    cat = json.loads(tests_json.read_text(encoding="utf-8"))["tests"]
    num = {t["id"]: t["number"] for t in cat}
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    data = d / "data"
    shutil.rmtree(data, ignore_errors=True)
    (data / "commits").mkdir(parents=True)
    shutil.copyfile(tests_json, data / "tests.json")
    answers, skipped = {}, {}
    for p in sorted((d / "commits").glob("*.patch")):
        lf = d / "labels" / f"{p.stem}.json"
        if not lf.exists():
            skipped[p.stem] = "unlabelled"
            continue
        lab = json.loads(lf.read_text(encoding="utf-8"))
        if lab.get("status") not in ("complete", "collection_abort"):
            skipped[p.stem] = f"{lab.get('status')}: {lab.get('reason', lab.get('error', ''))}"[:120]
            continue
        answers[p.stem] = {"failing": sorted(num[t] for t in lab["failing"] if t in num),
                           "kind": meta.get(p.stem, {}).get("category", "unknown")}
        shutil.copyfile(p, data / "commits" / p.name)
    (d / "answers.json").write_text(json.dumps({"commits": answers}, indent=1), encoding="utf-8")
    breaking = sum(1 for a in answers.values() if a["failing"])
    print(f"{len(answers)} scored ({breaking} breaking), {len(skipped)} left out")
    for k, v in skipped.items():
        print("  left out", k, v)


if __name__ == "__main__":
    main()

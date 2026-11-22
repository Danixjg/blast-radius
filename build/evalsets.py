"""One command for the Step 0 table: every labelled set, every app, 3 runs each.

usage: evalsets.py [--baseline REF] [--runs N] [--out DIR] [--only NAME ...]

Sets (whichever exist; missing ones are listed and skipped, never faked):
  sample           kit/sample (official answers)
  holdout1         work/holdout1, else labels/holdout1 (build/holdout.py --seed 20261121, build/label.py)
  holdout2         work/holdout2, else labels/holdout2 (--seed 31337)
  holdout2u        holdout2 minus patches byte-identical to a holdout1 patch
  mutants          work/mutants, else labels/mutants (*.patch + *.json, build/mutgen.py labels)
  work/sets/<name> any extra set dir holding data/commits, data/tests.json and answers.json

Apps: "candidate" is the working tree (selector.py, map.pkl, profile.pkl). With --baseline REF a
second app is built from those three files at REF, so every row is a direct comparison.
Each (app, set) runs through build/compare.py, i.e. build/bench.py and the kit's score.py.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "build"))
from bench import APP_DATA_FILES  # noqa: E402

W = R / "work"
SAMPLE = R / "kit" / "sample"
APP_FILES = ("selector.py", "map.pkl", "profile.pkl")


def catalog(tests_json=SAMPLE / "tests.json"):
    return json.loads(Path(tests_json).read_text(encoding="utf-8"))["tests"]


def expand_failing(labels, cat):
    """Test ids -> catalogue numbers. COLLECT::<path> (a module or package that failed to collect)
    fails every catalogued test under that path, as the scorer counts it. Unknown ids are returned
    separately so the caller can refuse to score them silently."""
    num = {t["id"]: t["number"] for t in cat}
    out, unknown = set(), []
    for lab in labels:
        if lab.startswith("COLLECT::"):
            path = lab[len("COLLECT::"):].rstrip("/")
            hit = {n for i, n in num.items() if i.split("::", 1)[0] == path or i.startswith(path + "/")}
            if not hit:
                unknown.append(lab)
            out |= hit
        elif lab in num:
            out.add(num[lab])
        else:
            unknown.append(lab)
    return sorted(out), unknown


def write_set(dst, patches, answers):
    """patches: {id: Path}; answers: {id: {"failing": [...], "kind": str}}."""
    shutil.rmtree(dst, ignore_errors=True)
    (dst / "data" / "commits").mkdir(parents=True)
    shutil.copyfile(SAMPLE / "tests.json", dst / "data" / "tests.json")
    for cid, p in patches.items():
        shutil.copyfile(p, dst / "data" / "commits" / f"{cid}.patch")
    (dst / "answers.json").write_text(json.dumps({"commits": answers}, indent=1), encoding="utf-8")


def mutant_set(src, dst):
    cat = catalog()
    patches, answers, bad = {}, {}, []
    for lf in sorted(src.glob("*.json")):
        pf = lf.with_suffix(".patch")
        if not pf.exists():
            bad.append(f"{lf.stem}: no patch")
            continue
        failing, unknown = expand_failing(json.loads(lf.read_text(encoding="utf-8"))["failing"], cat)
        if unknown:
            bad.append(f"{lf.stem}: unknown labels {unknown[:3]}")
            continue
        patches[lf.stem] = pf
        answers[lf.stem] = {"failing": failing, "kind": "mutant"}
    write_set(dst, patches, answers)
    return len(answers), bad


def unique_set(src, ref, dst):
    """src minus commits whose patch bytes equal some commit in ref."""
    seen = {hashlib.sha256(p.read_bytes()).hexdigest() for p in (ref / "data" / "commits").glob("*.patch")}
    ans = json.loads((src / "answers.json").read_text(encoding="utf-8"))["commits"]
    keep, dropped = {}, []
    for cid in ans:
        p = src / "data" / "commits" / f"{cid}.patch"
        if hashlib.sha256(p.read_bytes()).hexdigest() in seen:
            dropped.append(cid)
        else:
            keep[cid] = p
    write_set(dst, keep, {c: ans[c] for c in keep})
    return len(keep), dropped


def set_dir(name):
    """work/<name> when it is there (local labelling), else the committed copy in labels/."""
    return W / name if (W / name).exists() else R / "labels" / name


def discover():
    sets, notes = [], []
    sets.append(("sample", SAMPLE, SAMPLE / "answers.json"))
    for name in ("holdout1", "holdout2"):
        d = set_dir(name)
        if (d / "answers.json").exists() and json.loads((d / "answers.json").read_text())["commits"]:
            sets.append((name, d / "data", d / "answers.json"))
        else:
            notes.append(f"{name}: not labelled yet (make holdout / build/label.py)")
    if any(s[0] == "holdout1" for s in sets) and any(s[0] == "holdout2" for s in sets):
        n, dropped = unique_set(set_dir("holdout2"), set_dir("holdout1"), W / "sets" / "holdout2u")
        notes.append(f"holdout2u: {n} commits ({len(dropped)} identical to holdout1 removed: {' '.join(dropped)})")
        sets.append(("holdout2u", W / "sets" / "holdout2u" / "data", W / "sets" / "holdout2u" / "answers.json"))
    if set_dir("mutants").is_dir():
        n, bad = mutant_set(set_dir("mutants"), W / "sets" / "mutants")
        notes.append(f"mutants: {n} commits" + (f"; skipped {len(bad)}: {bad[:5]}" if bad else ""))
        sets.append(("mutants", W / "sets" / "mutants" / "data", W / "sets" / "mutants" / "answers.json"))
    for d in sorted((W / "sets").glob("*/")) if (W / "sets").is_dir() else []:
        if d.name not in ("holdout2u", "mutants") and (d / "answers.json").exists():
            sets.append((d.name, d / "data", d / "answers.json"))
    return sets, notes


def build_app(name, files_dir, out):
    py = W / "venv" / "bin" / "python"
    dst = out / "apps" / name
    shutil.rmtree(dst, ignore_errors=True)
    subprocess.run([str(py), str(R / "build" / "bench.py"), "app", str(dst), "--src", str(W / "sqlglot"),
                    "--selector", str(files_dir / "selector.py"), "--map", str(files_dir / "map.pkl"),
                    "--profile", str(files_dir / "profile.pkl")], check=True)
    return dst


def files_at(ref, out):
    d = out / "ref" / ref.replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    for f in APP_FILES:
        blob = subprocess.run(["git", "-C", str(R), "show", f"{ref}:{f}"], capture_output=True, check=True).stdout
        (d / f).write_bytes(blob)
    # the optional data files bench.make_app picks up next to the selector: shipped when the ref has
    # them, so the baseline app is the one that ref builds (older refs simply lack some)
    for f in APP_DATA_FILES:
        (d / f).unlink(missing_ok=True)
        got = subprocess.run(["git", "-C", str(R), "show", f"{ref}:{f}"], capture_output=True)
        if got.returncode == 0:
            (d / f).write_bytes(got.stdout)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default=str(W / "eval"))
    ap.add_argument("--only", action="append", default=[])
    a = ap.parse_args()
    out = Path(a.out)
    sets, notes = discover()
    if a.only:
        sets = [s for s in sets if s[0] in a.only]
    for n in notes:
        print("#", n, flush=True)
    apps = [("candidate", build_app("candidate", R, out))]
    if a.baseline:
        apps.insert(0, ("baseline", build_app("baseline", files_at(a.baseline, out), out)))
    cmd = [sys.executable, str(R / "build" / "compare.py"), "--runs", str(a.runs), "--out", str(out),
           "--python", str(W / "venv" / "bin" / "python")]
    for name, d in apps:
        cmd += ["--app", f"{name}={d}"]
    for name, data, ans in sets:
        cmd += ["--set", f"{name}={data},{ans}"]
    sys.exit(subprocess.run(cmd).returncode)


if __name__ == "__main__":
    main()

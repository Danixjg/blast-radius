"""Record integer comparison observations on the pinned, unpatched sqlglot suite.

Run with the Python 3.13 environment from the sample kit lock, for example:

    ../venv2/Scripts/python.exe -B build/record_boundaries.py --sqlglot ../sqlglot \
        --scratch ../boundary-evidence

No patches or test-failure labels are inputs. A test is safe at a site only when every recorded
evaluation was an unequal pair of exact ints and all test eligibility checks passed.
"""

import argparse
import json
import os
import pickle
import subprocess
import sys
import zlib
from itertools import chain
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
sys.path.insert(0, str(ROOT))
from selector import source_digest  # noqa: E402   # the selector checks these digests at run time


def check_checkout(source):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()

    if git("rev-parse", "HEAD") != PIN or git("status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"expected a clean sqlglot checkout at {PIN}")


def record(source, scratch, observations):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(source), str(ROOT / "build"))),
               SKIP_INTEGRATION="0", PYTHON_CPU_COUNT="2", PYTHONDONTWRITEBYTECODE="1",
               BOUNDARY_ROOT=str(source), BOUNDARY_OUT=str(observations))
    observations.unlink(missing_ok=True)
    with (scratch / "pytest.log").open("wb") as log:
        subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "-p", "observe_boundaries", "--timeout", "120", "tests"],
                       cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def load_map(path):
    raw = path.read_bytes()
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    return pickle.loads(raw)


def bind_sources(profile, source):
    """Version 2: record the pin and the sha256 of every file that has sites. Sites are keyed by
    line number; the selector applies a file's rows only while its source has the same digest."""
    sources = {rel: source_digest((source / rel).read_bytes()) for rel in profile["sites"]}
    return {"version": 2, "sqlglot": PIN, "tests": profile["tests"], "sources": sources,
            "sites": profile["sites"]}


def make_profile(observations, coverage, tests):
    if observations["exitstatus"] != 0:
        raise SystemExit("refusing observations from a failed pytest run")
    if tests != coverage["tests"]:
        raise SystemExit("test catalogue and coverage map test IDs differ")
    reports = observations["reports"]
    missing = [test for test in tests if reports.get(test) != {"setup": "passed", "call": "passed", "teardown": "passed"}]
    if missing:
        raise SystemExit(f"refusing an incomplete run: {len(missing)} catalogue tests lack a full pass, e.g. {missing[:3]}")

    indexes = {test: 1 << i for i, test in enumerate(tests)}

    def mask(ids):
        return sum(indexes.get(test, 0) for test in set(ids))

    excluded = coverage["subproc"]
    for path, readers in coverage["opens"].items():
        # Test-module reads in the map include collection and are already checked by the
        # observer. Conservatively exclude tests that previously read a non-source resource.
        if not path.endswith((".py", ".pyc")):
            excluded |= readers

    sites = {}
    line_cache = {}
    for rel, line, observed_good, observed_bad, outside_test in observations["sites"]:
        if outside_test or not observed_good:
            continue
        if rel not in line_cache:
            lines = coverage["lines"].get(rel, {})
            line_cache[rel] = pickle.loads(lines) if isinstance(lines, bytes) else lines
        hit = line_cache[rel].get(line, 0)
        seen = mask(chain(observed_good, observed_bad))
        good = hit & mask(observed_good) & ~(excluded | mask(observed_bad))
        if good:
            sites.setdefault(rel, {})[str(line)] = [f"{good:x}", f"{(hit | seen) & ~good:x}"]
    return {"version": 1, "tests": tests, "sites": sites}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sqlglot", required=True, type=Path)
    ap.add_argument("--scratch", required=True, type=Path)
    ap.add_argument("--tests", default=ROOT / "kit" / "sample" / "tests.json", type=Path)
    ap.add_argument("--map", default=ROOT / "map.pkl", type=Path)
    ap.add_argument("--output", default=ROOT / "order-boundaries.json", type=Path)
    ap.add_argument("--reuse-observations", action="store_true", help="merge the completed run in --scratch")
    ap.add_argument("--bind-existing", action="store_true",
                    help="upgrade an existing version 1 --output to version 2 without re-recording")
    args = ap.parse_args()
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("use Python 3.13 with the kit's locked test dependencies")
    source = args.sqlglot.resolve()
    check_checkout(source)
    if args.bind_existing:
        old = json.loads(args.output.read_text(encoding="utf-8"))
        if old.get("version") != 1:
            raise SystemExit("--bind-existing expects a version 1 profile")
        profile = bind_sources(old, source)
        args.output.write_text(json.dumps(profile, separators=(",", ":")) + "\n", encoding="utf-8")
        print(f"bound {len(profile['sources'])} files -> {args.output}")
        return
    scratch = args.scratch.resolve()
    scratch.mkdir(exist_ok=True, parents=True)
    observations = scratch / "observations.json"
    if not args.reuse_observations:
        record(source, scratch, observations)
    tests = [test["id"] for test in json.loads(args.tests.read_text(encoding="utf-8"))["tests"]]
    profile = make_profile(json.loads(observations.read_text(encoding="utf-8")), load_map(args.map), tests)
    profile = bind_sources(profile, source)
    if not profile["sites"]:
        raise SystemExit("refusing to publish an empty boundary profile")
    pending = args.output.with_suffix(args.output.suffix + ".tmp")
    pending.write_text(json.dumps(profile, separators=(",", ":")) + "\n", encoding="utf-8")
    pending.replace(args.output)
    print(f"{sum(map(len, profile['sites'].values()))} sites in {len(profile['sites'])} files -> {args.output}")


if __name__ == "__main__":
    main()

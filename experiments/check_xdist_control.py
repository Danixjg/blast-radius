"""One unpatched control for an observed work-stealing memory-test failure."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from benchmark_execution import junit_outcomes
from benchmark_xdist import run_pytest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--selector-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("use a fresh output directory")
    output.mkdir(parents=True)
    root = output / "sqlglot-unpatched"
    subprocess.run(["git", "clone", "--shared", str(args.runtime.resolve()), str(root)],
                   check=True, capture_output=True)
    inputs = args.selector_results.resolve()
    catalog = json.loads((inputs / "kit/sample/tests.json").read_text())["tests"]
    by_num = {test["number"]: test["id"] for test in catalog}
    assignment = next(row for row in map(json.loads,
        (inputs / "baseline/output-0.jsonl").read_text().splitlines()) if row["commit"] == "4d63")
    ids = [by_num[n] for n in sorted(n for batch in assignment["workers"] for n in batch)]
    report = output / "junit.xml"
    command = [args.python, "-m", "pytest", "-p", "xdist.plugin", "-n4", "--dist=worksteal",
               "-q", "-p", "no:cacheprovider", "--tb=short", f"--junitxml={report}", *ids]
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", SKIP_INTEGRATION="0")
    result = run_pytest(command, root, env, output / "pytest.log")
    outcomes = junit_outcomes([{"report": str(report)}]) if report.exists() else {}
    result.update(control="unpatched pinned source; same 360 IDs; four work-stealing workers",
                  complete_test_set=set(outcomes) == set(ids),
                  failed=[tid for tid, status in outcomes.items() if status == "failed"],
                  errors=[tid for tid, status in outcomes.items() if status == "error"],
                  skipped=[tid for tid, status in outcomes.items() if status == "skipped"])
    (output / "control-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

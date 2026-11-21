"""Compare persistent pytest-xdist workers without changing test selections.

No benchmark starts on import. Run only while the cloud CPU is otherwise idle.
Published labels are loaded after each execution solely to verify outcomes.
"""

import argparse
import json
import os
from pathlib import Path
import random
import resource
import signal
import subprocess
import time

from benchmark_execution import junit_outcomes


PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def run_pytest(command, root, env, log_path, timeout=240):
    """Capture whole pytest-process-tree CPU; terminate descendants on timeout."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    timed_out = False
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = process.wait()
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    wall = time.perf_counter() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"wall_seconds": wall,
            "child_cpu_seconds": after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
            "returncode": code, "timed_out": timed_out}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--selector-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--patches", nargs="+", default=["4d63", "ary6"])
    parser.add_argument("--workers", nargs="+", type=int, default=[2, 4])
    parser.add_argument("--distributions", nargs="+", choices=["loadfile", "worksteal"],
                        default=["loadfile", "worksteal"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0 or any(not 1 <= w <= 8 for w in args.workers):
        parser.error("positive repeats/timeout and worker counts 1 through 8 are required")
    args.runtime = args.runtime.resolve()
    args.selector_results = args.selector_results.resolve()
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty directory")
    args.output.mkdir(parents=True, exist_ok=True)
    data = args.selector_results / "kit/sample"
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    by_number = {test["number"]: test for test in catalog}
    selections = {row["commit"]: row["workers"] for row in map(
        json.loads, (args.selector_results / "baseline/output-0.jsonl").read_text().splitlines())}
    if any(patch not in selections or not (data / f"commits/{patch}.patch").is_file()
           for patch in args.patches):
        parser.error("every patch must have baseline selection and patch input")
    settings = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "SKIP_INTEGRATION": "0"}
    env = dict(os.environ, **settings)
    evidence = {"status": "in_progress", "seed": 4107, "env": settings,
                "expected_runs": args.repeats * len(args.patches) * len(args.workers) * len(args.distributions),
                "runs": [], "current_run": None,
                "cpu_note": "CPU aggregates the controller subprocess and waited xdist descendants; it is not fleet occupancy.",
                "latency_note": "No first-failure timestamps are measured by this benchmark.",
                "selection_note": "Every architecture executes the same baseline-selected IDs; labels are verification-only."}
    result_path = args.output / "xdist-results.json"

    def save():
        result_path.write_text(json.dumps(evidence, indent=2) + "\n")

    save()
    try:
        roots = {}
        for distribution in args.distributions:
            root = args.output / f"sqlglot-{distribution}"
            subprocess.run(["git", "clone", "--shared", str(args.runtime), str(root)],
                           check=True, capture_output=True)
            roots[distribution] = root
        rng = random.Random(4107)
        for iteration in range(args.repeats):
            for patch in args.patches:
                conditions = [(dist, workers) for dist in args.distributions for workers in args.workers]
                rng.shuffle(conditions)
                for distribution, workers in conditions:
                    root = roots[distribution]
                    selected = sorted(number for batch in selections[patch] for number in batch)
                    if len(selected) != len(set(selected)):
                        raise RuntimeError(f"Duplicate baseline-selected test: {patch}")
                    ids = [by_number[number]["id"] for number in selected]
                    plan = {"iteration": iteration, "patch": patch, "distribution": distribution,
                            "pytest_worker_count": workers, "tests": len(ids)}
                    evidence["current_run"] = plan
                    save()
                    # Reset only tracked files in these fresh task-owned clones.
                    subprocess.run(["git", "reset", "--hard", PIN], cwd=root,
                                   check=True, capture_output=True)
                    subprocess.run(["git", "apply", str(data / f"commits/{patch}.patch")],
                                   cwd=root, check=True, capture_output=True)
                    run_dir = args.output / f"run-{iteration}-{patch}-{distribution}-{workers}"
                    run_dir.mkdir()
                    report = run_dir / "junit.xml"
                    log = run_dir / "pytest.log"
                    command = [args.python, "-m", "pytest", "-p", "xdist.plugin", f"-n{workers}",
                               f"--dist={distribution}", "-q", "-p", "no:cacheprovider", "--tb=short",
                               f"--junitxml={report}", *ids]
                    if ids:
                        result = run_pytest(command, root, env, log, args.timeout)
                        outcomes = junit_outcomes([{"report": str(report)}]) if report.exists() else {}
                    else:
                        result = {"wall_seconds": 0, "child_cpu_seconds": 0, "returncode": 0,
                                  "timed_out": False}
                        outcomes = {}
                    # Ground truth is read after planning and execution, never to
                    # choose tests, order them, assign workers, or tune a policy.
                    labels = json.loads((data / "answers.json").read_text())["commits"][patch]["failing"]
                    expected_failures = {by_number[number]["id"] for number in labels}
                    failures = {tid for tid, outcome in outcomes.items() if outcome == "failed"}
                    complete = (not result["timed_out"] and result["returncode"] in (0, 1)
                                and set(outcomes) == set(ids)
                                and not any(value in ("error", "skipped") for value in outcomes.values())
                                and failures == expected_failures)
                    row = dict(plan, **result, failures=len(failures),
                               actual_execution_verified=complete, report=str(report), log=str(log))
                    evidence["runs"].append(row)
                    save()
                    print(json.dumps(row), flush=True)
                    if not complete:
                        raise RuntimeError(f"Execution differs from complete labelled selection: {plan}; see {log}")
        evidence.update(status="complete", current_run=None)
        save()
    except BaseException as exc:
        evidence.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save()
        raise


if __name__ == "__main__":
    main()

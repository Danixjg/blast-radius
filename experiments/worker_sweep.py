"""Measure worker/time/resource tradeoffs with identical tests in every policy."""
import argparse
import json
import os
from pathlib import Path
import random
import resource
import subprocess

from benchmark_execution import junit_outcomes
from executor import run_plan
from policies import make_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--selector-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--policies", nargs="+", default=["lpt", "affinity", "balanced"])
    parser.add_argument("--patches", nargs="+", default=["2jrq", "4d63", "ary6"])
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    args.runtime = args.runtime.resolve()
    args.selector_results = args.selector_results.resolve()
    args.output = args.output.resolve()
    if args.repeats < 1 or any(w < 1 or w > 8 for w in args.workers):
        parser.error("positive repeats and worker counts from 1 through 8 required")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("use a fresh output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    data = args.selector_results / "kit/sample"
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    by_num = {test["number"]: test for test in catalog}
    assignments = {row["commit"]: row["workers"] for row in map(json.loads,
        (args.selector_results / "baseline/output-0.jsonl").read_text().splitlines())}
    roots = {}
    for policy in args.policies:
        root = args.output / f"sqlglot-{policy}"
        subprocess.run(["git", "clone", "--shared", str(args.runtime), str(root)],
                       check=True, capture_output=True)
        roots[policy] = root
    env_settings = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "SKIP_INTEGRATION": "0"}
    os.environ.update(env_settings)
    runner = Path(__file__).with_name("pytest_runner.py").resolve()
    command = [args.python, str(runner), "--events", "{events}", "--report", "{report}", "{tests}"]
    conditions = [(iteration, patch, workers, policy)
                  for iteration in range(args.repeats) for patch in args.patches
                  for workers in args.workers for policy in args.policies]
    random.Random(9211).shuffle(conditions)
    evidence = dict(status="in_progress", expected_runs=len(conditions), runs=[],
                    env=env_settings, seed=9211, host_cpu_limit=Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
                    notes=["All policies preserve identical selected tests. No answer labels enter planning.",
                           "Worker-seconds is the sum of top-level command lifetimes, not CPU time or a cloud bill.",
                           "Failures are timestamped retrospectively; commands continue to completion.",
                           "One cloud host; requested workers may include nested test subprocesses."])
    for iteration, patch, workers, policy in conditions:
        root = roots[policy]
        subprocess.run(["git", "reset", "--hard", "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"],
                       cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(data / f"commits/{patch}.patch")],
                       cwd=root, check=True, capture_output=True)
        selected = {num for worker in assignments[patch] for num in worker}
        tasks = [dict(id=by_num[num]["id"], seconds=by_num[num]["seconds"])
                 for num in sorted(selected)]
        plan = make_plan(tasks, workers, policy)
        output = args.output / f"run-{iteration}-{patch}-{policy}-w{workers}"
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        result = run_plan(plan, command, str(root), mode="static", max_workers=workers,
                          timeout=240, output_dir=output)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        outcomes = junit_outcomes(result["batches"])
        if not result["all_completed"] or set(outcomes) != {test["id"] for test in tasks}:
            raise RuntimeError(f"Incomplete execution: {output}")
        if any(status in ("error", "skipped") for status in outcomes.values()):
            raise RuntimeError(f"Errors/skips invalidate comparison: {output}")
        expected = json.loads((data / "answers.json").read_text())["commits"][patch]["failing"]
        if {tid for tid, status in outcomes.items() if status == "failed"} != {by_num[n]["id"] for n in expected}:
            raise RuntimeError(f"Label mismatch: {output}")
        row = dict(iteration=iteration, patch=patch, workers=workers, policy=policy,
                   tests=len(outcomes), failures=len(expected), wall_seconds=result["wall_seconds"],
                   worker_seconds=sum(batch["finished_seconds"]-batch["started_seconds"]
                                      for batch in result["batches"]),
                   child_cpu_seconds=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
                   peak_workers=result["peak_workers"], batches=len(result["batches"]),
                   first_failure_seconds=result["first_failure_seconds"], actual_execution_verified=True)
        evidence["runs"].append(row)
        (args.output / "worker-results.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps(row), flush=True)
    evidence["status"] = "complete"
    (args.output / "worker-results.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()

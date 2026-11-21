"""Compare actual patched sqlglot execution; no answer labels enter scheduling."""
import argparse
import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET

from executor import run_plan


def junit_outcomes(batches):
    outcomes = {}
    for batch in batches:
        path = Path(batch["report"])
        if not path.exists():
            raise RuntimeError(f"Missing execution evidence: {path}")
        for case in ET.parse(path).iter("testcase"):
            # Match the canonical catalog IDs, including nested class names.
            classname = case.attrib.get("classname", "")
            parts = classname.split(".")
            split = next((i for i, part in enumerate(parts) if part.startswith("Test")), len(parts))
            tid = "/".join(parts[:split]) + ".py"
            if split < len(parts):
                tid += "::" + "::".join(parts[split:])
            tid += "::" + case.attrib["name"]
            if tid in outcomes:
                raise RuntimeError(f"Test executed twice: {tid}")
            outcomes[tid] = ("error" if case.find("error") is not None else
                             "failed" if case.find("failure") is not None else
                             "skipped" if case.find("skipped") is not None else "passed")
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--selector-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--stream-events", action="store_true")
    parser.add_argument("--patches", nargs="+", default=["2jrq", "4d63", "ary6"])
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.runtime = args.runtime.resolve()
    args.selector_results = args.selector_results.resolve()
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty directory to preserve earlier evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    data = args.selector_results / "kit/sample"
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    by_num = {t["number"]: t for t in catalog}
    selections = {r["commit"]: r["workers"] for r in map(json.loads,
                  (args.selector_results / "baseline/output-0.jsonl").read_text().splitlines())}
    # These labels are read only after run_plan; no training on evaluation answers.
    labels_path = data / "answers.json"
    modes = ("static", "priority", "relay")
    roots = {}
    for mode in modes:
        root = args.output / f"sqlglot-{mode}"
        if not root.exists():
            subprocess.run(["git", "clone", "--shared", str(args.runtime), str(root)],
                           check=True, capture_output=True)
        roots[mode] = root
    env_settings = {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "SKIP_INTEGRATION": "0"}
    os.environ.update(env_settings)
    command = [args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
               "--junitxml={report}"]
    if args.stream_events:
        command = [args.python, str(Path(__file__).with_name("pytest_runner.py").resolve()),
                   "--events", "{events}", "--report", "{report}", "{tests}"]
    rows = []
    evidence = dict(runs=rows, env=env_settings, seed=4107, max_workers=3,
                    relay_batch_size=32, risk_source="neutral prior; no historical labels available",
                    expected_runs=args.repeats * len(args.patches) * len(modes),
                    status="in_progress",
                    note=("Failure latency uses retrospective pytest event timestamps, read after completion."
                          if args.stream_events else "Failure latency is observed at batch completion.") +
                         " Full tests verified using JUnit.")
    rng = random.Random(4107)
    for iteration in range(args.repeats):
        for patch in args.patches:
            order = list(modes)
            rng.shuffle(order)
            for mode in order:
                root = roots[mode]
                # Only reset tracked files in these task-owned runtime clones.
                subprocess.run(["git", "reset", "--hard", "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"],
                               cwd=root, check=True, capture_output=True)
                subprocess.run(["git", "apply", str(data / f"commits/{patch}.patch")],
                               cwd=root, check=True, capture_output=True)
                workers = [[by_num[n]["id"] for n in worker] for worker in selections[patch]]
                selected = {n for worker in selections[patch] for n in worker}
                plan = dict(tasks=[dict(id=by_num[n]["id"], seconds=by_num[n]["seconds"])
                                   for n in sorted(selected)], workers=workers,
                            startup_seconds=.3, minimum_workers=1)
                output = args.output / f"run-{iteration}-{patch}-{mode}"
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                result = run_plan(plan, command, str(root), mode=mode, max_workers=3,
                                  timeout=180, output_dir=output, batch_size=32)
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                outcomes = junit_outcomes(result["batches"])
                expected = {t["id"] for t in plan["tasks"]}
                if set(outcomes) != expected or any(v in ("error", "skipped") for v in outcomes.values()):
                    raise RuntimeError(f"Incomplete runner validation: {patch} {mode}: {outcomes}")
                if not result["all_completed"]:
                    raise RuntimeError(f"Executor infrastructure failure: {patch} {mode}")
                failing_ids = {tid for tid, status in outcomes.items() if status == "failed"}
                labels = json.loads(labels_path.read_text())["commits"][patch]["failing"]
                expected_failures = {by_num[n]["id"] for n in labels}
                if failing_ids != expected_failures:
                    raise RuntimeError(f"Environment differs from labelled failures for {patch}: "
                                       f"extra={failing_ids-expected_failures}, missing={expected_failures-failing_ids}")
                row = dict(iteration=iteration, patch=patch, mode=mode,
                           wall_seconds=result["wall_seconds"],
                           child_cpu_seconds=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
                           first_failure_seconds=result["first_failure_seconds"],
                           first_failure_observed_seconds=result["first_failure_observed_seconds"],
                           tests=len(outcomes), failures=len(failing_ids),
                           batches=len(result["batches"]), peak_workers=result["peak_workers"],
                           actual_execution_verified=True)
                rows.append(row)
                (args.output / "execution-results.json").write_text(json.dumps(
                    evidence, indent=2) + "\n")
                print(json.dumps(row), flush=True)
    summary = {mode: {"aggregate_wall_seconds": sum(statistics.median(
        r["wall_seconds"] for r in rows if r["patch"] == patch and r["mode"] == mode)
        for patch in args.patches)} for mode in modes}
    evidence.update(status="complete", summary=summary)
    (args.output / "execution-results.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

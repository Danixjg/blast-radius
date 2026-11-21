"""Compare coverage-preserving worker policies using catalog-duration models only.

No tests run here. Labels are read after planning, solely to verify misses.
"""
import argparse
import collections
import heapq
import json
import math
from pathlib import Path

POLICIES = ("lpt", "heap_lpt", "fifo", "shortest_first", "module_affinity", "class_affinity")


def plan(tasks, count, policy):
    """Tasks carry number, id, file, and integer ms; return complete nonempty bins."""
    if not tasks:
        return []
    count = min(count, len(tasks))
    bins, loads = [[] for _ in range(count)], [0] * count
    if policy in ("module_affinity", "class_affinity"):
        groups = collections.defaultdict(list)
        for task in tasks:
            key = task["file"] if policy == "module_affinity" else task["id"].rsplit("::", 1)[0]
            groups[key].append(task)
        jobs = sorted(groups.values(), key=lambda group: (-sum(t["ms"] for t in group), group[0]["number"]))
    else:
        ordered = list(tasks)
        if policy in ("lpt", "heap_lpt"):
            ordered.sort(key=lambda t: (-t["ms"], t["number"]))
        elif policy == "shortest_first":
            ordered.sort(key=lambda t: (t["ms"], t["number"]))
        elif policy != "fifo":
            raise ValueError("unknown policy")
        jobs = [[t] for t in ordered]
    heap = [(0, i) for i in range(count)]
    for job in jobs:
        if policy == "heap_lpt":
            load, worker = heapq.heappop(heap)
        else:
            worker = min(range(count), key=lambda i: (loads[i], i))
            load = loads[worker]
        bins[worker].extend(t["number"] for t in job)
        loads[worker] = load + sum(t["ms"] for t in job)
        if policy == "heap_lpt":
            heapq.heappush(heap, (loads[worker], worker))
    return [batch for batch in bins if batch]


def metrics(workers, durations):
    loads = [sum(durations[n] for n in worker) for worker in workers if worker]
    total = sum(loads)
    count = len(loads)
    longest = max((durations[n] for worker in workers for n in worker), default=0)
    lower = max(longest, math.ceil(total / count)) + 2000 * count if count else 0
    return dict(work_ms=total, max_load_ms=max(loads, default=0), worker_count=count,
                score_ms=max(loads, default=0) + 2000 * count,
                worker_count_lower_bound_ms=lower,
                imbalance_ms=max(loads, default=0) - min(loads, default=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selector-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.selector_results.resolve()
    output = args.output.resolve()
    if output.exists():
        parser.error("choose a fresh output directory to preserve prior results")
    data = root / "kit/sample"
    catalog = json.loads((data / "tests.json").read_text())["tests"]
    durations = {t["number"]: round(t["seconds"] * 1000) for t in catalog}
    entries = [json.loads(line) for line in (root / "baseline/output-0.jsonl").read_text().splitlines()]
    commits = [entry["commit"] for entry in entries]
    expected_commits = {p.stem for p in (data / "commits").glob("*.patch")}
    if len(commits) != len(set(commits)) or set(commits) != expected_commits:
        raise ValueError("baseline must cover each patch exactly once")
    rows, baseline = [], []
    for entry in entries:
        selected = [n for batch in entry["workers"] for n in batch]
        if len(selected) != len(set(selected)) or not set(selected).issubset(durations):
            raise ValueError("invalid or duplicate baseline test assignments")
        selected_set = set(selected)
        tasks = [dict(t, ms=durations[t["number"]]) for t in catalog if t["number"] in selected_set]
        work = sum(durations[n] for n in selected)
        longest = max((durations[n] for n in selected), default=0)
        optimum_bound = min((max(longest, math.ceil(work / w)) + 2000 * w
                             for w in range(1, min(8, len(tasks)) + 1)), default=0)
        baseline.append(dict(commit=entry["commit"], optimal_lower_bound_ms=optimum_bound,
                             **metrics(entry["workers"], durations)))
        for policy in POLICIES:
            for count in range(1, 9):
                workers = plan(tasks, count, policy)
                flattened = [n for batch in workers for n in batch]
                if len(flattened) != len(selected) or set(flattened) != selected_set:
                    raise RuntimeError("policy changed the required selection")
                rows.append(dict(commit=entry["commit"], policy=policy, requested_workers=count,
                                 workers=workers, optimal_lower_bound_ms=optimum_bound,
                                 **metrics(workers, durations)))
    # Evaluation labels deliberately enter only after every policy has been planned.
    labels = json.loads((data / "answers.json").read_text())["commits"]
    for row in rows:
        scheduled = {n for batch in row["workers"] for n in batch}
        row["miss"] = bool(set(labels[row["commit"]]["failing"]) - scheduled)
        row["score_with_penalty_ms"] = row["score_ms"] + 300000 * row["miss"]
    aggregate = []
    best = []
    for policy in POLICIES:
        for count in range(1, 9):
            chosen = [r for r in rows if r["policy"] == policy and r["requested_workers"] == count]
            aggregate.append(dict(policy=policy, requested_workers=count,
                                  score_ms=sum(r["score_ms"] for r in chosen),
                                  misses=sum(r["miss"] for r in chosen),
                                  max_load_ms=sum(r["max_load_ms"] for r in chosen),
                                  workers=sum(r["worker_count"] for r in chosen)))
        chosen = [min((r for r in rows if r["policy"] == policy and r["commit"] == commit),
                      key=lambda r: (r["score_ms"], r["requested_workers"])) for commit in commits]
        best.append(dict(policy=policy, score_ms=sum(r["score_ms"] for r in chosen),
                         misses=sum(r["miss"] for r in chosen),
                         chosen_workers={r["commit"]: r["requested_workers"] for r in chosen}))
    baseline_seconds = sum(r["score_ms"] for r in baseline) / 1000
    selector_report = json.loads((root / "selector-results.json").read_text())
    cpu_seconds = selector_report["summary"]["baseline"]["cpu_seconds"]
    total_seconds = baseline_seconds + cpu_seconds
    result = dict(measurement_kind="modeled execution cost; no test execution and no Rust benchmark",
                  policies=list(POLICIES), commits=len(commits), baseline=baseline,
                  baseline_score_ms=sum(r["score_ms"] for r in baseline),
                  baseline_lower_bound_ms=sum(r["optimal_lower_bound_ms"] for r in baseline),
                  fixed_worker_totals=aggregate, per_patch_best_worker_totals=best, rows=rows,
                  rust_bounds=dict(selector_cpu_seconds=cpu_seconds, modeled_execution_seconds=baseline_seconds,
                                   baseline_total_seconds=total_seconds,
                                   eliminating_all_selector_cpu_max_total_time_reduction_percent=100 * cpu_seconds / total_seconds,
                                   scheduler_cpu_upper_seconds_assumed=.001,
                                   eliminating_scheduler_cpu_max_total_time_reduction_percent=100 * .001 / total_seconds,
                                   note="Amdahl-style upper bounds assuming zero replacement CPU and unchanged selections; not measured Rust performance. Scheduler bound uses the preceding audit's <1 ms per sample batch, not a new measurement."),
                  notes=["All policies preserve each patch's complete baseline-selected set.",
                         "Model uses supplied catalog durations and 2 seconds per nonempty worker.",
                         "Affinity setup/cache savings are absent from this scorer and not invented here.",
                         "Optimal lower bounds are certificates when attained, not claims that every instance is solved.",
                         "Labels verify omissions after planning; they never influence worker assignment."])
    output.mkdir(parents=True)
    (output / "model-sweep.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("baseline_score_ms", "baseline_lower_bound_ms", "per_patch_best_worker_totals", "rust_bounds")}, indent=2))


if __name__ == "__main__":
    main()

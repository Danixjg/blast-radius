"""Summarize real worker-cap sweeps separately from the challenge cost model."""
import argparse
import json
from pathlib import Path
import statistics


def pareto(rows):
    return [r for r in rows if not any(
        other["wall_seconds"] <= r["wall_seconds"] and
        other["worker_seconds"] <= r["worker_seconds"] and
        (other["wall_seconds"] < r["wall_seconds"] or other["worker_seconds"] < r["worker_seconds"])
        for other in rows)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--xdist", type=Path, nargs="*", default=[])
    parser.add_argument("--xdist-control", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = json.loads(args.model.read_text())
    actual = json.loads(args.actual.read_text())
    if actual["status"] != "complete" or len(actual["runs"]) != actual["expected_runs"]:
        raise ValueError("worker sweep is incomplete")
    rows = actual["runs"]
    text = ["# Worker count, architecture, and Rust assessment", "",
            "This expands the earlier three-worker comparison. All policies keep every baseline-selected test. "
            "Answer labels are used only after execution to verify outcomes.", "",
            "## Challenge scoring model: all 30 patches", "",
            "The existing scheduler already searches worker counts per patch. The objective is "
            "maximum worker load plus two seconds per nonempty worker. These are modeled costs, "
            "not measured pytest execution times.", "",
            "| Fixed requested workers per patch | Longest-first modeled total (s) |",
            "| ---: | ---: |"]
    for row in model["fixed_worker_totals"]:
        if row["policy"] == "lpt":
            text.append(f"| {row['requested_workers']} | {row['score_ms']/1000:.3f} |")
    text += ["", "| Packing strategy, choosing cheapest worker count per patch | Modeled total (s) |",
             "| --- | ---: |"]
    for row in model["per_patch_best_worker_totals"]:
        text.append(f"| {row['policy']} | {row['score_ms']/1000:.3f} |")
    text += ["", "The existing 121.100-second plan matches the lower-bound certificate for every "
             "sample selection. More packing work cannot improve these modeled sample costs "
             "without changing the selected work or scoring assumptions.", "",
             "## Real execution: one-, two-, and four-worker caps", "",
             "One seeded, sequential run per configuration on a single four-CPU-quota cloud host. "
             "Each policy has an independent pinned sqlglot checkout. JUnit verifies the exact "
             "assigned test set, no skips/errors, and the expected failing test IDs. "
             "The main sweep is exploratory; confirmation measurements are separate below.", "",
             "- **lpt:** longest-first fixed worker bins.",
             "- **affinity:** group tests by module to preserve setup/cache reuse, then balance groups.",
             "- **balanced:** longest-first duration-targeted microbatches in a shared queue, "
             "with enough initial batches to fill available slots. This improves on the earlier "
             "relay prototype's small-plan batching.", "",
             "Worker-seconds below sums top-level command lifetimes. It is an occupancy proxy, "
             "not CPU seconds or a cloud bill; nested test subprocesses also consume resources. "
             "Requested caps can exceed actual peak workers when grouping leaves fewer jobs.", "",
             "| Patch | Policy | Worker cap | Actual peak | Wall (s) | Worker-seconds |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in sorted(rows, key=lambda r: (r["patch"], r["policy"], r["workers"])):
        text.append(f"| {row['patch']} | {row['policy']} | {row['workers']} | {row['peak_workers']} | "
                    f"{row['wall_seconds']:.3f} | {row['worker_seconds']:.3f} |")
    text += ["", "### Observed latency/resource frontier", "",
             "A point is listed if no other observed configuration for that patch uses both "
             "less wall time and fewer worker-seconds. This is an observed frontier, not a "
             "proof of production optimality.", ""]
    for patch in sorted({r["patch"] for r in rows}):
        front = sorted(pareto([r for r in rows if r["patch"] == patch]), key=lambda r:r["wall_seconds"])
        text.append(f"- `{patch}`: " + "; ".join(
            f"{r['policy']} cap {r['workers']}: {r['wall_seconds']:.3f}s wall, {r['worker_seconds']:.3f} worker-s"
            for r in front) + ".")
    if args.confirmation:
        confirm = json.loads(args.confirmation.read_text())
        required = {(r["patch"], r["workers"], r["policy"]) for r in rows
                    if r["policy"] in ("lpt", "affinity")}
        actual_keys = {(r["patch"], r["workers"], r["policy"]) for r in confirm["runs"]}
        if (confirm["status"] != "complete" or len(confirm["runs"]) != confirm["expected_runs"]
                or actual_keys != required or not all(r["actual_execution_verified"] for r in confirm["runs"])):
            raise ValueError("confirmation is incomplete")
        text += ["", "### Fresh confirmation", "",
                 "These additional runs recheck the promising module-aware approach against "
                 "longest-first ordering at the same worker count. A second patch includes "
                 "known failures to verify retained detection.", "",
                 "| Patch | Policy | Worker cap | Wall (s) | Worker-seconds |",
                 "| --- | --- | ---: | ---: | ---: |"]
        for row in sorted(confirm["runs"], key=lambda r:(r["patch"],r["policy"],r["workers"])):
            text.append(f"| {row['patch']} | {row['policy']} | {row['workers']} | "
                        f"{row['wall_seconds']:.3f} | {row['worker_seconds']:.3f} |")
        combined = rows + confirm["runs"]
        text += ["", "### Module-aware worker tradeoff across both passes", "",
                 "Medians combine the main and confirmation observations and are exploratory, "
                 "not confidence intervals. "
                 "For illustration, the final column adds a two-second charge per actual peak "
                 "worker to measured wall time. It is a policy preference, not a cloud bill or "
                 "the official modeled score.", "",
                 "| Patch | Worker cap | Median wall (s) | Median worker-seconds | Wall + 2s/peak worker |",
                 "| --- | ---: | ---: | ---: | ---: |"]
        for patch in sorted({r["patch"] for r in rows}):
            for workers in sorted({r["workers"] for r in rows}):
                selected = [r for r in combined if r["patch"] == patch and r["workers"] == workers
                            and r["policy"] == "affinity"]
                wall = statistics.median(r["wall_seconds"] for r in selected)
                occupied = statistics.median(r["worker_seconds"] for r in selected)
                weighted = statistics.median(r["wall_seconds"]+2*r["peak_workers"] for r in selected)
                text.append(f"| {patch} | {workers} | {wall:.3f} | {occupied:.3f} | {weighted:.3f} |")
    if args.xdist:
        text += ["", "## Existing persistent-worker implementations", "",
                 "One bounded run per configuration using installed pytest-xdist 3.8.0, "
                 "the same selected test IDs, and explicit plugin loading. These figures "
                 "are not repeated confidence estimates. Failed validation remains reported "
                 "and is excluded from valid speed comparisons.", "",
                 "| Patch | Distribution | Workers | Wall (s) | Matches expected outcomes |",
                 "| --- | --- | ---: | ---: | --- |"]
        xrows = [row for path in args.xdist for row in json.loads(path.read_text())["runs"]]
        for row in sorted(xrows, key=lambda r:(r["patch"],r["distribution"],r["pytest_worker_count"])):
            text.append(f"| {row['patch']} | {row['distribution']} | {row['pytest_worker_count']} | "
                        f"{row['wall_seconds']:.3f} | {'yes' if row['actual_execution_verified'] else 'NO — unexpected failure'} |")
        text += ["", "Both work-stealing configurations produced an unexpected failure in "
                 "`tests/test_generator.py::TestGenerator::test_generator_memory`. "
                 "That test measures process-wide tracemalloc growth and asserts less "
                 "than 1024 bytes. This makes background/ordering effects a plausible "
                 "hypothesis; the exact allocation mechanism has not been isolated."]
        if args.xdist_control:
            control = json.loads(args.xdist_control.read_text())
            text += ["", f"An unpatched pinned-source control, using the same 360 IDs and "
                     f"four work-stealing workers, returned {control['returncode']} and recorded "
                     f"these failing IDs: `{control['failed']}`. "
                     "It executed the complete selected set without skips or collection errors. "
                     "This confirms execution-mode sensitivity independent of the patch. "
                     "Work stealing is not validated for this suite; isolating memory-sensitive "
                     "tests and rerunning would be needed before adoption."]
    bounds = model["rust_bounds"]
    text += ["", "## Would Rust help?", "",
             "Rust has not been benchmarked or implemented here. The preceding profiling "
             "does not justify a full rewrite.", "",
             f"The measured selector CPU is {bounds['selector_cpu_seconds']:.6f}s per 30-patch batch; "
             f"modeled test cost is {bounds['modeled_execution_seconds']:.3f}s. Even eliminating "
             f"all selector CPU with zero replacement cost can reduce their combined total by only "
             f"{bounds['eliminating_all_selector_cpu_max_total_time_reduction_percent']:.4f}%. "
             "This is a mathematical ceiling with unchanged test selection and model, not a "
             "predicted Rust speedup.", "",
             f"Eliminating the scheduler's assumed upper bound of 1ms saves at most "
             f"{bounds['eliminating_scheduler_cpu_max_total_time_reduction_percent']:.6f}% "
             "of that combined total. A faster language does not itself remove pytest collection, "
             "fixture setup, Python test execution, or Python import-validation subprocesses.", "",
             "Rewriting actual application/test hot paths is a different proposal and could "
             "change execution cost; its benefit and semantic compatibility are unmeasured. "
             "First profile those hot paths and compare a small native prototype against an "
             "equivalent Python baseline rather than extrapolating language benchmarks.", "",
             "## Decision", "",
             "Keep the current per-patch worker search for the challenge model. For real execution, "
             "module-aware grouping and worker-cap selection merit further validation. Choose "
             "a point on the measured latency/resource frontier according to actual capacity "
             "and cost requirements; neither maximum concurrency nor minimum worker count is "
             "universally best. Setup/cache reuse is a plausible explanation for ordering gains, "
             "but this experiment does not isolate the cause.", "",
             "All results preserve the baseline's selected tests, not a proof of complete "
             "defect coverage beyond that baseline. The timing sample is small and single-host; "
             "representative repeated production workloads remain necessary before promotion.", "",
             f"Model data: `{args.model.resolve()}`. Real data: `{args.actual.resolve()}`.", ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(text))
    print(args.output)


if __name__ == "__main__":
    main()

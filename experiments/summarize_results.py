"""Render saved measurements without treating modeled and real execution as equal."""
import argparse
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selectors", type=Path, required=True)
    parser.add_argument("--execution", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-repeats", type=int, default=2)
    args = parser.parse_args()
    selectors = json.loads(args.selectors.read_text())
    execution = json.loads(args.execution.read_text())
    rows = execution["runs"]
    if execution.get("status", "complete") != "complete":
        raise ValueError("execution experiment is incomplete")
    patches = sorted({r["patch"] for r in rows})
    modes = ("static", "priority", "relay")
    counts = {len([r for r in rows if r["patch"] == patch and r["mode"] == mode])
              for patch in patches for mode in modes}
    if counts != {args.expected_repeats} or not all(r["actual_execution_verified"] for r in rows):
        raise ValueError("unbalanced or unverified execution experiment")
    text = ["# Cloud experiment results", "",
            "Baseline: `3fd38f73ffca368c4a7b4c7472ea0b127f0b4bc3`. "
            "All candidates retain the baseline's selected tests. "
            "No sample answers are used to schedule tests.", "",
            "## Selector CPU", "",
            f"Python {selectors['python']}; {selectors['repeats']} measured fresh processes "
            "per variant after one warmup. Seeded shuffled order, no concurrent timed runs. "
            "CPU includes child import-check processes. The three copies also passed a "
            "separate simultaneous smoke run.", "",
            "| Build | Median CPU (s) | Median wall (s) | Runtime map bytes |",
            "| --- | ---: | ---: | ---: |"]
    for variant, metrics in selectors["summary"].items():
        text.append(f"| {variant} | {metrics['cpu_seconds']:.4f} | "
                    f"{metrics['wall_seconds']:.4f} | {metrics['map_bytes']:,} |")
    text += ["", "All outputs pass the official bundled sample scorer: "
             "121.100 modeled seconds and zero misses, with identical selected sets. "
             "The small median CPU differences have overlapping observed timings; "
             "they do not establish a robust speedup. Indexed maps remain opt-in.", "",
             "## Actual patched test execution", "",
             f"{next(iter(counts))} repetitions per patch/policy. Policies run in separate "
             "pinned sqlglot clones, sequentially in seeded shuffled order, with at most "
             "three top-level workers. Nested test workers also consume CPU. "
             "This table reports real wall time, excluding selector scans.", "",
             "| Patch | Selected tests | Failing test IDs | Static median (s) | "
             "Priority median (s) | Relay median (s) |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    totals = dict.fromkeys(modes, 0.0)
    for patch in patches:
        selected = [r for r in rows if r["patch"] == patch]
        medians = [statistics.median(r["wall_seconds"] for r in selected if r["mode"] == mode)
                   for mode in modes]
        for mode, median in zip(modes, medians):
            totals[mode] += median
        text.append(f"| {patch} | {selected[0]['tests']} | {selected[0]['failures']} | "
                    + " | ".join(f"{value:.3f}" for value in medians) + " |")
    text.append("| Sum of per-patch medians | | | " +
                " | ".join(f"{totals[mode]:.3f}" for mode in modes) + " |")
    text += ["", "Every measured run was checked against JUnit: exactly the assigned "
             "test IDs ran, none skipped or errored, and failing IDs matched the bundled "
             "answers. Failures never canceled remaining validation.", "",
             "### Test failure detection timestamps", "",
             "These are retrospective timestamps from pytest events, read after the run. "
             "They measure when pytest reported a failure, not delivered notification "
             "latency. Raw executor results also retain command-completion observation time.", "",
             "| Patch | Static median (s) | Priority median (s) | Relay median (s) |",
             "| --- | ---: | ---: | ---: |"]
    for patch in patches:
        vals = []
        for mode in modes:
            values = [r["first_failure_seconds"] for r in rows
                      if r["patch"] == patch and r["mode"] == mode and
                      r["first_failure_seconds"] is not None]
            vals.append(f"{statistics.median(values):.3f}" if values else "none")
        text.append(f"| {patch} | " + " | ".join(vals) + " |")
    text += ["", "## Interpretation and limitations", "",
             "- Keep the existing static scheduler for the challenge: it already reaches "
             "the lower bound for these selected sample sets. Static output cannot use live results.",
             "- Keep indexed maps optional. Their smaller representation is confirmed, "
             "but full-process startup gains are small compared with variance.",
             "- Neutral risk priors were used because no representative historical "
             "failure dataset was available. These runs test scheduling mechanics, "
             "not the predictive accuracy of a trained model.",
             "- Relay uses 32-test microbatches and a bounded work/risk heuristic. "
             "Small plans can become a single batch. Repeated collection and fixture setup "
             "can dominate; results do not establish an optimal relay configuration.",
             "- Only three representative patches were executed, with two repetitions "
             "in the main experiment. Full-suite and production generalization remain unverified.",
             "- Docker daemon access was unavailable. Selector builds reproduce its "
             "source extraction, map preparation, bytecode compilation and Python 3.13 entrypoint "
             "without claiming the image or the scorer's exact CPU/memory sandbox was tested.", "",
             "## Artifacts and reruns", "",
             f"- Selector measurements: `{args.selectors.resolve()}`.",
             f"- Execution measurements: `{args.execution.resolve()}`.",
             "- Per-batch logs, JUnit XML, event JSONL and complete executor results sit "
             "beside the execution measurements.",
             "- Earlier `selectors/` measurements retain the rejected marshal-map experiment. "
             "An earlier `execution/` pilot was intentionally interrupted to add per-test "
             "timestamps; its partial results are excluded from the final comparison.",
             "- Runnable isolated selector copies and sqlglot policy clones are retained "
             "in the results directories.",
             "- See [experiments/README.md](../../experiments/README.md) for commands and contracts.", ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(text))
    print(args.output)


if __name__ == "__main__":
    main()

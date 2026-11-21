"""Experimental coverage-preserving command executor; never skips on a prediction.

Static and priority modes run one command per supplied worker bin. Relay mode
dispatches configurable microbatches from a shared priority queue and adjusts concurrency.
Commands must execute all supplied IDs (do not use pytest -x). Batch completion
does not independently prove that a third-party runner executed every test.
"""

import argparse
import collections
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time


def _number(value, name, low=0, high=math.inf):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return float(value)


def _validated(plan):
    tasks = {}
    for task in plan["tasks"]:
        tid = task["id"]
        if not isinstance(tid, str) or not tid or tid in tasks:
            raise ValueError("task IDs must be nonempty unique strings")
        seconds = _number(task.get("seconds", 0), "seconds")
        # A history prior is optional; outcomes from the current patch must not
        # be used to generate its initial prediction.
        history = task.get("history")
        if "risk" in task:
            risk = _number(task["risk"], "risk", high=1)
        elif history is not None:
            runs = _number(history["runs"], "history runs")
            failures = _number(history["failures"], "history failures", high=runs)
            risk = (failures + 1) / (runs + 2)  # Beta(1,1) prior
        else:
            risk = .5
        tasks[tid] = {"id": tid, "seconds": seconds, "risk": risk}
    workers = plan.get("workers", [list(tasks)] if tasks else [])
    flat = [tid for batch in workers for tid in batch]
    if len(flat) != len(set(flat)) or set(flat) != set(tasks):
        raise ValueError("worker bins must partition the complete task set")
    return tasks, [list(batch) for batch in workers if batch]


def _argv(command, ids, report="report.xml", events="events.jsonl"):
    command = [arg.replace("{report}", str(report)).replace("{events}", str(events))
               for arg in command]
    if "{tests}" in command:
        args = []
        for arg in command:
            args.extend(ids if arg == "{tests}" else [arg])
        return args
    if any("{test}" in arg for arg in command):
        if len(ids) != 1:
            raise ValueError("{test} requires singleton jobs; use {tests} for bins")
        return [arg.replace("{test}", ids[0]) for arg in command]
    return list(command) + ids


def _terminate(process):
    """Kill the timed-out command and its descendants on POSIX."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def run_plan(plan, command, cwd, mode="static", max_workers=8, timeout=300,
             output_dir="executor-output", batch_size=1):
    """Execute every assignment and return timings plus auditable batch records.

    `completed` means a command returned normally, including a test-failure exit.
    Timeout/launch failures make all_completed false. `failed` contains exact IDs
    from singleton jobs and validated runner events; other failed multi-test jobs
    have ambiguous attribution in failed_batches/possibly_failed. No early
    cancellation follows test failures.
    """
    tasks, workers = _validated(plan)
    if mode not in ("static", "priority", "relay"):
        raise ValueError("mode must be static, priority, or relay")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 8:
        raise ValueError("max_workers must be an integer from 1 through 8")
    _number(timeout, "timeout", low=.001)
    if not command or any(not isinstance(arg, str) for arg in command):
        raise ValueError("command must be a nonempty argv list")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    setup = _number(plan.get("startup_seconds", .05), "startup_seconds", low=.000001)
    floor = plan.get("minimum_workers", 1)
    if isinstance(floor, bool) or not isinstance(floor, int) or not 1 <= floor <= max_workers:
        raise ValueError("minimum_workers must be an integer within worker limits")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    priority = lambda tid: (-tasks[tid]["risk"] / max(tasks[tid]["seconds"], .001), tid)
    if mode == "relay":
        ordered = sorted(tasks, key=priority)
        batches = [ordered[i:i + batch_size] for i in range(0, len(ordered), batch_size)]
    elif mode == "priority":
        batches = [sorted(batch, key=priority) for batch in workers]
    else:
        batches = workers
    # Validate substitution before any command is launched.
    for batch in batches:
        _argv(command, batch)
    pending = collections.deque(batches)
    active = []
    records = []
    start = time.perf_counter()
    first_failure = None
    first_error = None
    peak = 0
    concurrency = []

    def target_workers():
        if mode != "relay" or not pending:
            return max_workers
        remaining = [tid for batch in pending for tid in batch]
        work = sum(tasks[tid]["seconds"] for tid in remaining)
        mean_risk = sum(tasks[tid]["risk"] for tid in remaining) / len(remaining)
        # Experimental heuristic, not a calibrated safety/optimality claim.
        # Nonzero risk floor ensures low-risk work still receives resources.
        target = math.ceil(math.sqrt(work * (.25 + .75 * mean_risk) / setup))
        return max(floor, min(max_workers, target))

    try:
        while pending or active:
            target = target_workers()
            while pending and len(active) < target:
                ids = pending.popleft()
                number = len(records)
                log_path = output / f"batch-{number:04d}.log"
                report_path = output / f"batch-{number:04d}.xml"
                events_path = output / f"batch-{number:04d}.events.jsonl"
                report_path.unlink(missing_ok=True)
                events_path.unlink(missing_ok=True)
                record = {"tasks": ids, "started_seconds": time.perf_counter() - start,
                          "log": str(log_path), "report": str(report_path), "events": str(events_path),
                          "returncode": None, "timed_out": False,
                          "infrastructure_error": None}
                records.append(record)
                log = log_path.open("wb")
                try:
                    proc = subprocess.Popen(_argv(command, ids, report_path, events_path), cwd=cwd,
                                            stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=os.name == "posix")
                except OSError as exc:
                    log.close()
                    record["infrastructure_error"] = str(exc)
                    record["finished_seconds"] = time.perf_counter() - start
                    if first_error is None:
                        first_error = record["finished_seconds"]
                    continue
                active.append((proc, time.perf_counter(), log, record))
                peak = max(peak, len(active))
                concurrency.append({"seconds": time.perf_counter() - start,
                                    "active": len(active), "target": target})
            for item in list(active):
                proc, began, log, record = item
                code = proc.poll()
                if code is None and time.perf_counter() - began >= timeout:
                    _terminate(proc)
                    record["timed_out"] = True
                    code = proc.returncode
                if code is not None:
                    log.close()
                    record["returncode"] = code
                    if code not in (0, 1) and not record["timed_out"]:
                        record["infrastructure_error"] = f"Command exited {code}; validation incomplete"
                    record["finished_seconds"] = time.perf_counter() - start
                    active.remove(item)
                    if (record["timed_out"] or record["infrastructure_error"]) and first_error is None:
                        first_error = record["finished_seconds"]
                    if code == 1 and not record["infrastructure_error"] and not record["timed_out"] and first_failure is None:
                        first_failure = record["finished_seconds"]
            if active:
                time.sleep(.002)
    finally:
        # KeyboardInterrupt or unexpected controller failures must not leave
        # commands running in the cloud after the caller has lost supervision.
        for proc, _, log, _ in active:
            _terminate(proc)
            log.close()
    completed = [tid for r in records if not r["timed_out"] and not r["infrastructure_error"]
                 for tid in r["tasks"]]
    failed_batches = [r["tasks"] for r in records if r["returncode"] == 1
                      and not r["timed_out"] and not r["infrastructure_error"]]
    # An instrumented runner can timestamp failures inside a multi-test batch.
    # Reading after completion avoids polling file contents in the timing loop;
    # observed timestamps remain distinct from when the runner detected failure.
    first_failure_observed = first_failure
    failure_events = []
    for record in records:
        try:
            lines = Path(record["events"]).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
                timestamp = event["monotonic"]
                if (event["event"] != "test_failure" or event["test"] not in record["tasks"]
                        or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                        or not math.isfinite(timestamp)
                        or timestamp < start + record["started_seconds"]
                        or timestamp > start + record["finished_seconds"]):
                    continue
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
            failure_events.append({"test": event["test"], "seconds": timestamp - start,
                                   "events": record["events"]})
    if failure_events:
        event_first = min(event["seconds"] for event in failure_events)
        first_failure = min(first_failure, event_first) if first_failure is not None else event_first
    wall_seconds = time.perf_counter() - start
    if failure_events and first_failure_observed is None:
        first_failure_observed = wall_seconds
    result = {"mode": mode, "wall_seconds": wall_seconds,
              "first_failure_seconds": first_failure,
              "first_failure_observed_seconds": first_failure_observed,
              "failure_events": failure_events,
              "first_infrastructure_error_seconds": first_error,
              "completed": completed,
              "failed": sorted({batch[0] for batch in failed_batches if len(batch) == 1}
                               | {event["test"] for event in failure_events}),
              "possibly_failed": [tid for batch in failed_batches for tid in batch],
              "failed_batches": failed_batches,
              "timed_out": [tid for r in records if r["timed_out"] for tid in r["tasks"]],
              "infrastructure_errors": [r for r in records if r["infrastructure_error"]],
              "batches": records, "peak_workers": peak, "concurrency": concurrency,
              "all_completed": len(completed) == len(tasks),
              "coverage_note": "Completion covers command assignments; runner-level execution must be verified separately.",
              "first_failure_note": ("Runner event timestamp where available, with command-completion fallback for other batches; events read after all batches."
                                     if failure_events else
                                     "Observed at command completion; multi-test batches have no per-test failure timestamps.")}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--output", required=True, help="Directory for logs and result.json")
    parser.add_argument("--mode", choices=("static", "priority", "relay"), default="static")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    try:
        result = run_plan(json.loads(Path(args.plan).read_text()), args.command,
                          args.cwd, args.mode, args.max_workers, args.timeout,
                          args.output, args.batch_size)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result))
    return 0 if result["all_completed"] and not result["failed_batches"] and not result["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

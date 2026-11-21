"""Pure, coverage-preserving execution plans for architecture comparisons.

Plans carry the complete selected task set. They make no claims about calibrated
bug probability and never use risk to omit a test. The queued batch policies can
produce more command batches than execution slots; run them with the executor's
static mode and max_workers equal to the requested capacity.
"""

from collections import defaultdict
import math


def _validate(tasks, workers):
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ValueError("workers must be an integer from 1 through 8")
    result = []
    seen = set()
    for task in tasks:
        tid = task["id"]
        if not isinstance(tid, str) or not tid or tid in seen:
            raise ValueError("task IDs must be nonempty unique strings")
        seconds = task.get("seconds", 0)
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError("task seconds must be finite and nonnegative")
        seen.add(tid)
        result.append((tid, seconds))
    return result


def lpt_bins(tasks, workers):
    """Longest-processing-time assignment to a fixed number of command bins.

    Equal duration tasks retain input order; equally loaded bins prefer their
    original index. At most workers nonempty bins are returned.
    """
    items = _validate(tasks, workers)
    count = min(workers, len(items))
    bins = [[] for _ in range(count)]
    loads = [0] * count
    for tid, seconds in sorted(items, key=lambda item: -item[1]):
        slot = min(range(count), key=lambda i: (loads[i], len(bins[i]), i))
        bins[slot].append(tid)
        loads[slot] += seconds
    return bins


def affinity_bins(tasks, workers):
    """Keep module IDs together, assigning largest module work groups first.

    Module grouping preserves the opportunity for module/class fixture reuse
    within each command. It can increase load imbalance for a large module.
    """
    items = _validate(tasks, workers)
    groups = defaultdict(list)
    for tid, seconds in items:
        groups[tid.split("::", 1)[0]].append((tid, seconds))
    count = min(workers, len(groups))
    bins = [[] for _ in range(count)]
    loads = [0] * count
    ordered = sorted(groups.values(), key=lambda group: -sum(seconds for _, seconds in group))
    for group in ordered:
        slot = min(range(count), key=lambda i: (loads[i], len(bins[i]), i))
        bins[slot].extend(tid for tid, _ in group)
        loads[slot] += sum(seconds for _, seconds in group)
    return bins


def _batches(tasks, workers, batch_seconds, short_first):
    items = _validate(tasks, workers)
    if (isinstance(batch_seconds, bool) or not isinstance(batch_seconds, (int, float))
            or not math.isfinite(batch_seconds) or batch_seconds <= 0):
        raise ValueError("batch_seconds must be finite and positive")
    items.sort(key=lambda item: item[1] if short_first else -item[1])
    batches = []
    load = 0
    for tid, seconds in items:
        if not batches or (load + seconds > batch_seconds and batches[-1]):
            batches.append([])
            load = 0
        batches[-1].append(tid)
        load += seconds
    # Tiny/zero tasks may naturally form only one batch. Explicitly split until
    # enough batches can occupy the requested slots, without duplicating work.
    minimum = min(workers, len(items))
    while len(batches) < minimum:
        slot = max(range(len(batches)), key=lambda i: len(batches[i]))
        batch = batches[slot]
        split = max(1, len(batch) // 2)
        batches[slot:slot + 1] = [batch[:split], batch[split:]]
    return batches


def balanced_batches(tasks, workers, batch_seconds=2.0):
    """Longest-first shared queue, bounded by target duration except long tests.

    At least min(workers, task count) nonempty command batches are returned. An
    indivisible test longer than batch_seconds is a singleton batch.
    """
    return _batches(tasks, workers, batch_seconds, short_first=False)


def shortfirst_batches(tasks, workers, batch_seconds=2.0):
    """Duration-only shortest-first control; retains the identical selected set."""
    return _batches(tasks, workers, batch_seconds, short_first=True)


def make_plan(tasks, workers, policy):
    """Build a plan accepted by executor.run_plan(mode='static')."""
    builders = {"lpt": lpt_bins, "affinity": affinity_bins,
                "balanced": balanced_batches, "shortfirst": shortfirst_batches}
    if policy not in builders:
        raise ValueError(f"unknown execution policy: {policy}")
    tasks = list(tasks)
    batches = builders[policy](tasks, workers)
    return {"tasks": [dict(task) for task in tasks], "workers": batches,
            "minimum_workers": workers, "policy": policy}

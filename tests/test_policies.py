"""Selection and resource invariants for experimental architecture comparisons."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "policies", Path(__file__).resolve().parents[1] / "experiments/policies.py")
policies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policies)


def task(tid, seconds):
    return {"id": tid, "seconds": seconds, "risk": .5}


class PolicyTests(unittest.TestCase):
    def assert_partition(self, tasks, batches):
        ids = [item for batch in batches for item in batch]
        self.assertEqual(sorted(ids), sorted(t["id"] for t in tasks))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(batches))

    def test_every_policy_preserves_all_inputs_without_mutation(self):
        tasks = [task(f"mod{i % 3}.py::test_{i}", (i % 7) / 10) for i in range(29)]
        snapshot = repr(tasks)
        for policy in ("lpt", "affinity", "balanced", "shortfirst"):
            for workers in (1, 2, 4, 8):
                with self.subTest(policy=policy, workers=workers):
                    plan = policies.make_plan(tasks, workers, policy)
                    self.assert_partition(tasks, plan["workers"])
                    self.assertEqual(plan["minimum_workers"], workers)
                    self.assertEqual(plan["tasks"], tasks)
        self.assertEqual(repr(tasks), snapshot)

    def test_lpt_has_known_optimum_for_independent_example(self):
        tasks = [task(str(i), value) for i, value in enumerate([8, 7, 6, 5, 4, 4])]
        bins = policies.lpt_bins(tasks, 2)
        duration = {t["id"]: t["seconds"] for t in tasks}
        loads = [sum(duration[tid] for tid in batch) for batch in bins]
        self.assertEqual(max(loads), 17)  # Total 34 / two workers is a lower bound.
        self.assertEqual(len(bins), 2)

    def test_affinity_keeps_each_module_in_one_command(self):
        tasks = [task("a.py::C::one", 10), task("a.py::D::two", 1),
                 task("b.py::one", 5), task("c.py::one", 4)]
        bins = policies.affinity_bins(tasks, 3)
        self.assert_partition(tasks, bins)
        locations = {tid: i for i, batch in enumerate(bins) for tid in batch}
        self.assertEqual(locations["a.py::C::one"], locations["a.py::D::two"])
        self.assertLessEqual(len(bins), 3)

    def test_balanced_keeps_long_task_early_and_parallelism(self):
        tasks = [task(f"test_{i}", .01) for i in range(28)] + [task("long", 21)]
        bins = policies.balanced_batches(tasks, 4, batch_seconds=2)
        self.assert_partition(tasks, bins)
        self.assertEqual(bins[0], ["long"])
        self.assertGreaterEqual(len(bins), 4)

    def test_balanced_batch_target_and_more_batches_than_workers(self):
        tasks = [task(f"test_{i}", .8) for i in range(20)]
        bins = policies.balanced_batches(tasks, 2, batch_seconds=2)
        self.assertGreater(len(bins), 2)
        self.assertTrue(all(len(batch) <= 2 for batch in bins))
        self.assert_partition(tasks, bins)

    def test_shortfirst_control_does_not_use_risk(self):
        tasks = [dict(task("long", 5), risk=1), dict(task("short", .1), risk=0)]
        self.assertEqual(policies.shortfirst_batches(tasks, 2), [["short"], ["long"]])

    def test_empty_and_zero_duration_inputs(self):
        for policy in ("lpt", "affinity", "balanced", "shortfirst"):
            self.assertEqual(policies.make_plan([], 8, policy)["workers"], [])
            tasks = [task(str(i), 0) for i in range(29)]
            bins = policies.make_plan(tasks, 8, policy)["workers"]
            self.assert_partition(tasks, bins)
            if policy in ("balanced", "shortfirst"):
                self.assertGreaterEqual(len(bins), 8)

    def test_invalid_inputs_rejected(self):
        for workers in (0, 9, True, 1.5):
            with self.assertRaises(ValueError):
                policies.make_plan([], workers, "lpt")
        for tasks in ([task("a", -1)], [task("a", float("nan"))],
                      [task("a", 1), task("a", 2)], [task("", 1)]):
            with self.assertRaises(ValueError):
                policies.make_plan(tasks, 2, "balanced")
        with self.assertRaises(ValueError):
            policies.make_plan([], 2, "unknown")
        for target in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                policies.balanced_batches([], 2, target)


if __name__ == "__main__":
    unittest.main()

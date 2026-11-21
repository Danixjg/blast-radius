import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from model_sweep import POLICIES, metrics, plan


def task(number, ms, file="tests/a.py"):
    return dict(number=number, ms=ms, file=file, id=f"{file}::TestCase::test_{number}")


class ModelSweepTests(unittest.TestCase):
    def test_known_balanced_optimum(self):
        tasks = [task(i, ms) for i, ms in enumerate([4000, 3000, 2000, 1000])]
        bins = plan(tasks, 2, "lpt")
        result = metrics(bins, {t["number"]: t["ms"] for t in tasks})
        self.assertEqual(result["max_load_ms"], 5000)
        self.assertEqual(result["score_ms"], 9000)
        self.assertEqual(result["score_ms"], result["worker_count_lower_bound_ms"])

    def test_every_policy_preserves_selection(self):
        tasks = [task(i, ms, f"tests/{i % 3}.py") for i, ms in enumerate([0, 1, 5, 20, 3000])]
        for policy in POLICIES:
            for count in range(1, 9):
                bins = plan(tasks, count, policy)
                self.assertCountEqual([n for batch in bins for n in batch], range(5))
                self.assertLessEqual(len(bins), count)
                self.assertEqual(plan([], count, policy), [])

    def test_heap_implementation_matches_array_lpt(self):
        rng = random.Random(913)
        for _ in range(50):
            tasks = [task(i, rng.randrange(200)) for i in range(rng.randrange(1, 50))]
            for count in (1, 2, 4, 8):
                self.assertEqual(plan(tasks, count, "heap_lpt"), plan(tasks, count, "lpt"))

    def test_module_group_stays_together(self):
        tasks = [task(0, 4000, "tests/a.py"), task(1, 1000, "tests/a.py"),
                 task(2, 3000, "tests/b.py")]
        bins = plan(tasks, 2, "module_affinity")
        self.assertTrue(any(set(batch) == {0, 1} for batch in bins))
        self.assertTrue(any(set(batch) == {2} for batch in bins))

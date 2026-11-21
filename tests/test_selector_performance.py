"""Coverage-preserving optimizations and prepared-map compatibility.

Run with python3.13 -m unittest discover -s tests -p test_selector_performance.py.
Set BR_TEST_BASELINE to an untouched selector.py for differential sample verification.
"""
import importlib.util
import os
from pathlib import Path
import pickle
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import selector as S


class SelectorPerformanceTests(unittest.TestCase):
    def test_sparse_and_dense_mask_expansion(self):
        catalog = [24, None, 100, 24, 0, 900]
        for mask in range(1 << (len(catalog) + 1)):
            expected = {catalog[i] for i in range(len(catalog)) if mask & (1 << i)} - {None}
            self.assertEqual(S.selected_numbers(mask, catalog), expected)
        self.assertEqual(S.selected_numbers(7, []), set())

    def test_schedule_known_optima_and_integrity(self):
        # The longest job makes 25.454 seconds a matching lower bound with two workers.
        cases = [([], 0), ([(2, 0)], 2000), ([(4, 1000), (2, 1000)], 4000),
                 ([(4, 21454), (2, 7000)], 25454)]
        for tests, optimum in cases:
            with self.subTest(tests=tests):
                cost, workers = S.schedule(tests)
                self.assertEqual(cost, optimum)
                self.assertCountEqual([n for worker in workers for n in worker], [n for n, _ in tests])
                self.assertLessEqual(len(workers), 8)

    def test_map_formats_and_version_guard(self):
        original = S.MAP
        mask = 1 << 1600
        data = {"tests": ["a"], "lines": {"x": {3: mask, 4: mask}}, "sets": {"a"},
                "tuple": (None, 12), "opens": {"fixture": mask}, "classes": {"C": 0},
                "name_mask": {"foo": mask}, "subproc": mask}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                S.MAP = Path(tmp) / "map"
                for content in [pickle.dumps(data), zlib.compress(pickle.dumps(data)), S.serialize_map(data)]:
                    S.MAP.write_bytes(content)
                    self.assertEqual(S.load_map(), data)
                S.MAP.write_bytes(S.PREPARED_MAP_MAGIC + pickle.dumps({"version": 99}))
                with self.assertRaisesRegex(ValueError, "unsupported prepared map version"):
                    S.load_map()
                broken = {"version": 2, "masks": [1], "data": {"lines": {"x": {3: -1}}}}
                S.MAP.write_bytes(S.PREPARED_MAP_MAGIC + pickle.dumps(broken))
                with self.assertRaisesRegex(ValueError, "invalid mask index"):
                    S.load_map()
        finally:
            S.MAP = original

    def test_real_map_round_trip(self):
        data = pickle.loads(zlib.decompress((ROOT / "map.pkl").read_bytes()))
        original = S.MAP
        try:
            with tempfile.TemporaryDirectory() as tmp:
                S.MAP = Path(tmp) / "prepared"
                S.MAP.write_bytes(S.serialize_map(data))
                self.assertEqual(S.load_map(), data)
        finally:
            S.MAP = original

    def test_scheduler_preserves_baseline_on_varied_workloads(self):
        path = Path(os.environ.get("BR_TEST_BASELINE", ROOT.parent / "blast-radius-baseline" / "selector.py"))
        if not path.exists():
            self.skipTest("set BR_TEST_BASELINE to an untouched baseline selector.py")
        spec = importlib.util.spec_from_file_location("baseline_selector", path)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        rng = random.Random(901)
        for _ in range(200):
            tests = [(i, rng.randrange(30000)) for i in range(rng.randrange(50))]
            self.assertEqual(S.schedule(tests), baseline.schedule(tests))

    def test_sample_selection_and_output_parity(self):
        baseline = Path(os.environ.get("BR_TEST_BASELINE", ROOT.parent / "blast-radius-baseline" / "selector.py"))
        if not baseline.exists():
            self.skipTest("set BR_TEST_BASELINE to an untouched baseline selector.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with tarfile.open(ROOT / "src.tar.gz") as t:
                t.extractall(tmp / "src", filter="data")
            with tarfile.open(ROOT / "blast-radius-sample.tar.gz") as t:
                t.extractall(tmp / "kit", filter="data")
            raw = (ROOT / "map.pkl").read_bytes()
            data = pickle.loads(zlib.decompress(raw))
            maps = [raw, pickle.dumps(data), S.serialize_map(data)]
            env = dict(os.environ, BR_DATA=str(tmp / "kit/sample"), BR_SRC=str(tmp / "src"),
                       BR_MAP=str(tmp / "map"), PYTHONDONTWRITEBYTECODE="1")
            (tmp / "map").write_bytes(raw)
            result = subprocess.run([sys.executable, "-S", str(baseline)], env=env, capture_output=True, check=True)
            expected = result.stdout
            for content in maps:
                (tmp / "map").write_bytes(content)
                result = subprocess.run([sys.executable, "-S", str(ROOT / "selector.py")], env=env,
                                        capture_output=True, check=True)
                self.assertEqual(result.stdout, expected)
                self.assertNotIn(b"catcher runs everything", result.stderr)


if __name__ == "__main__":
    unittest.main()

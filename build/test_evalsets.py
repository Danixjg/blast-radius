"""Unit tests for build/evalsets.py set preparation (no selector runs)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evalsets as E  # noqa: E402

CAT = [{"id": "tests/test_a.py::A::test_1", "number": 1},
       {"id": "tests/test_a.py::A::test_2", "number": 2},
       {"id": "tests/dialects/test_b.py::B::test_1", "number": 3},
       {"id": "tests/test_ab.py::C::test_1", "number": 4}]


class ExpandFailing(unittest.TestCase):
    def test_plain_ids(self):
        self.assertEqual(E.expand_failing(["tests/test_a.py::A::test_2"], CAT), ([2], []))

    def test_collect_module_fails_every_test_in_it_and_nothing_with_a_shared_prefix(self):
        self.assertEqual(E.expand_failing(["COLLECT::tests/test_a.py"], CAT), ([1, 2], []))

    def test_collect_package_fails_everything_below(self):
        self.assertEqual(E.expand_failing(["COLLECT::tests/dialects"], CAT), ([3], []))

    def test_unknown_labels_are_reported_not_dropped(self):
        self.assertEqual(E.expand_failing(["tests/test_z.py::Z::t", "COLLECT::tests/nope.py"], CAT),
                         ([], ["tests/test_z.py::Z::t", "COLLECT::tests/nope.py"]))


class UniqueSet(unittest.TestCase):
    def test_drops_only_byte_identical_patches(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            for d, files in (("ref", {"r1": "same\n", "r2": "other\n"}), ("src", {"s1": "same\n", "s2": "new\n"})):
                (t / d / "data" / "commits").mkdir(parents=True)
                for cid, body in files.items():
                    (t / d / "data" / "commits" / f"{cid}.patch").write_text(body)
                (t / d / "answers.json").write_text(json.dumps({"commits": {c: {"failing": []} for c in files}}))
            n, dropped = E.unique_set(t / "src", t / "ref", t / "out")
            self.assertEqual((n, dropped), (1, ["s1"]))
            self.assertEqual(list(json.loads((t / "out" / "answers.json").read_text())["commits"]), ["s2"])
            self.assertTrue((t / "out" / "data" / "tests.json").exists())


if __name__ == "__main__":
    unittest.main()

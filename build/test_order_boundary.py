"""Regression tests for the optional pristine order-boundary profile."""
import difflib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selector as S


REL = "sqlglot/example.py"
TESTS = [f"tests/test_example.py::test_{i}" for i in range(6)]
SOURCE = "def choose(a, b):\n    if a < b:\n        return 1\n    return 2\n"


def diff(rel, old, new):
    return f"diff --git a/{rel} b/{rel}\n" + "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))


class OrderBoundaryTest(unittest.TestCase):
    def hunter(self, source=SOURCE, line=2, subproc=0, unobserved=0, imp=()):
        data = {
            "tests": TESTS, "lines": {REL: {line: 31}}, "classes": {}, "imp_lines": {REL: imp},
            "opens": {}, "subproc": subproc, "name_mask": {}, "uses": {}, "name_imp": set(),
        }
        h = S.Hunter(data, {"unobserved": unobserved})
        h._src[REL] = source
        return h

    def observations(self, line=2, good="3b", bad="2", tests=TESTS):
        self.artifact.read_text.return_value = json.dumps(
            {"version": 1, "tests": tests, "sites": {REL: {str(line): [good, bad]}}}
        )

    def setUp(self):
        self.artifact = Mock()
        p = patch.object(S, "ORDER_BOUNDARIES", self.artifact)
        p.start()
        self.addCleanup(p.stop)
        self.observations()

    def test_drop_only_line_covered_good_bits(self):
        h = self.hunter(subproc=8, unobserved=16)
        result = h.analyze(diff(REL, SOURCE, SOURCE.replace("<", "<=")))
        self.assertEqual(result, (30, None))
        self.artifact.read_text.assert_called_once()

    def test_both_strictness_directions(self):
        for a, b in (("<", "<="), ("<=", "<"), (">", ">="), (">=", ">")):
            with self.subTest(a=a, b=b):
                src = SOURCE.replace("< b", f"{a} 7")
                self.observations(good="1f", bad="0")
                self.assertEqual(self.hunter(src).analyze(diff(REL, src, src.replace(a, b))), (0, None))

    def test_mixed_and_import_time_edits_keep_original_selection(self):
        compare = SOURCE.replace("<", "<=")
        mixed = diff(REL, SOURCE, compare.replace("return 2", "return 3"))
        another = "sqlglot/another.py"
        other_src = "k = 1\n"
        h = self.hunter()
        h._src[another] = other_src
        self.assertEqual(h.analyze(diff(REL, SOURCE, compare) + diff(another, other_src, "# note\nk = 1\n"))[0], 31)
        self.assertEqual(self.hunter().analyze(mixed)[0], 31)
        self.assertEqual(self.hunter(imp=(2,)).analyze(diff(REL, SOURCE, compare))[0], 31)
        self.artifact.read_text.assert_not_called()

    def test_globals_free_names_and_non_standalone_comparisons(self):
        cases = (
            ("def choose(a):\n    if a < global_value:\n        return 1\n", 2),
            ("def outer(b):\n    def inner(a):\n        if a < b:\n            return 1\n", 3),
            (SOURCE.replace("a < b", "a < b and a"), 2),
            (SOURCE.replace("a < b", "a < b < 7"), 2),
            (SOURCE.replace("a < b", "a < True"), 2),
            (SOURCE.replace("a < b", "a.value < b"), 2),
        )
        for src, line in cases:
            with self.subTest(src=src):
                self.observations(line=line, good="1f", bad="0")
                self.assertEqual(self.hunter(src, line).analyze(diff(REL, src, src.replace("<", "<=", 1)))[0], 31)

    def test_missing_bad_and_unmapped_observations_fall_back(self):
        compare = diff(REL, SOURCE, SOURCE.replace("<", "<="))
        for good, bad, tests in (("ff", "0", TESTS), ("1f", "oops", TESTS),
                                 ("0", "0", TESTS), ("1f", "0", TESTS[::-1])):
            with self.subTest(good=good, bad=bad, tests=tests):
                self.observations(good=good, bad=bad, tests=tests)
                self.assertEqual(self.hunter().analyze(compare)[0], 31)
        self.artifact.read_text.side_effect = FileNotFoundError()
        self.assertEqual(self.hunter().analyze(compare)[0], 31)
        self.artifact.read_text.reset_mock()
        h = self.hunter()
        h.order_boundary_has_unmapped = True
        self.assertEqual(h.analyze(compare)[0], 31)
        self.artifact.read_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()

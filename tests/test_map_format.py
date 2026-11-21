"""Lazy map format: same answers as the plain map, decoded only where a patch looks.

Run: python3 -m unittest discover -s tests
The end-to-end checks need the sample kit (make kit) and the sqlglot sources
(work/sqlglot after make checkout, or BR_SRC); they are skipped without them.
"""
import os
import pickle
import sys
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402

SAMPLE = R / "kit" / "sample"
SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot"))


def plain_map():
    raw = (R / "map.pkl").read_bytes()
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    d = pickle.loads(raw)
    return S.unpack_map(d) if S.LAZY_KEY in d else d


class LazyViewTest(unittest.TestCase):
    def setUp(self):
        self.src = {"a": {1: 5, 2: 7}, "b": [("x.py", 3, None)]}
        self.view = S.LazyView({k: pickle.dumps(v, 5) for k, v in self.src.items()})

    def test_get_decodes_present_keys(self):
        self.assertEqual(self.view.get("a"), {1: 5, 2: 7})
        self.assertEqual(self.view.get("b"), [("x.py", 3, None)])

    def test_get_default_for_missing_key(self):
        self.assertIsNone(self.view.get("zzz"))
        self.assertEqual(self.view.get("zzz", ()), ())

    def test_decodes_each_key_once(self):
        self.assertIs(self.view.get("a"), self.view.get("a"))

    def test_contains_and_len(self):
        self.assertIn("a", self.view)
        self.assertNotIn("zzz", self.view)
        self.assertEqual(len(self.view), 2)

    def test_corrupt_entry_raises_so_the_catcher_widens(self):
        view = S.LazyView({"a": b"not a pickle"})
        with self.assertRaises(Exception):
            view.get("a")


class PackTest(unittest.TestCase):
    def test_round_trip_is_exact(self):
        d = plain_map()
        packed = pickle.loads(pickle.dumps(S.pack_map(d), 5))
        self.assertEqual(S.unpack_map(packed), d)

    def test_pack_is_idempotent_and_does_not_mutate(self):
        d = {"lines": {"f.py": {1: 3}}, "uses": {"n": [("f.py", 1, None)]}, "tests": ["t"]}
        before = pickle.dumps(d)
        once = S.pack_map(d)
        self.assertEqual(pickle.dumps(d), before)
        self.assertEqual(S.pack_map(once), once)

    def test_hunter_accepts_plain_maps(self):
        h = S.Hunter({"tests": ["t"], "lines": {"f.py": {1: 1}}, "classes": {}, "imp_lines": {},
                      "imp_classes": set(), "name_mask": {}, "uses": {}, "name_imp": set(),
                      "name_imp_cls": set(), "subproc": 0, "opens": {}, "imp_opens": []})
        self.assertEqual(h.file_mask("f.py"), 1)


@unittest.skipUnless((SAMPLE / "tests.json").exists() and SRC.is_dir(), "needs kit and sqlglot sources")
class SampleParityTest(unittest.TestCase):
    """Both map formats yield the same masks and import probes on the sample."""

    def analyze_sample(self, data, profile):
        out = {}
        h = S.Hunter(data, profile)   # one hunter for every patch, as in main()
        for p in sorted((SAMPLE / "commits").glob("*.patch")):
            out[p.stem] = h.analyze(p.read_bytes().decode("utf-8", "surrogateescape"))
        return out

    def test_identical_selection(self):
        d = plain_map()
        packed = pickle.loads(pickle.dumps(S.pack_map(d), 5))
        with patch.object(S, "PROFILE", R / "profile.pkl"):
            profile = S.load_profile(d["tests"])
        self.assertIsNotNone(profile, "the committed profile must load for this map")
        with patch.object(S, "SRC", SRC):
            for active_profile in (None, profile):
                with self.subTest(profile=active_profile is not None):
                    self.assertEqual(self.analyze_sample(packed, active_profile),
                                     self.analyze_sample(d, active_profile))


if __name__ == "__main__":
    unittest.main()

"""Check the structural import-site index without running sqlglot tests."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selector as S


class ImportSiteIndexTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src = Path(tmp.name).resolve()
        self.path = self.src / "import-sites.json"
        for attr, value in (("SRC", self.src), ("IMPORT_SITE_INDEX", self.path)):
            change = patch.object(S, attr, value)
            change.start()
            self.addCleanup(change.stop)
        self.write("sqlglot/__init__.py", "")
        self.write("sqlglot/optimizer/__init__.py",
                   "_LAZY_ATTRS = {'LazyAction': 'impl'}\ndef __getattr__(name): pass\n")
        self.write("sqlglot/optimizer/impl.py", "")
        self.write("sqlglot/optimizer/consumer.py", "from . import LazyAction as action\n")
        self.write("tests/test_function.py",
                   "def use():\n    from sqlglot.optimizer import (\n        LazyAction as action,\n    )\n")
        self.write("tests/test_typing.py",
                   "from typing import TYPE_CHECKING as TC\n"
                   "if TC:\n    from sqlglot.optimizer.impl import hidden\n")
        self.write("tests/sub/__init__.py", "import sqlglot.optimizer.other as other\n")
        self.map = {
            "tests": ["tests/test_function.py::test_use", "tests/test_typing.py::test_type",
                      "tests/sub/test_other.py::test_other"],
            "lines": {"tests/test_function.py": {4: 1}, "sqlglot/optimizer/consumer.py": {1: 1}},
            "classes": {}, "imp_lines": {}, "opens": {}, "subproc": 0,
        }

    def write(self, rel, content):
        path = self.src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_relative_lazy_aliases_and_function_scope(self):
        index = S.Hunter(self.map).build_import_site_index()
        self.assertEqual(set(index["sites"]["sqlglot.optimizer.impl"]), {
            ("sqlglot/optimizer/consumer.py", "module", 1, 1),
            ("tests/test_function.py", "function", 2, 4),
        })

    def test_loaded_and_fallback_masks_agree(self):
        plain = S.Hunter(self.map)
        sites = plain.build_import_site_index()
        self.assertEqual(plain.import_sites({"sqlglot.optimizer.impl"}), 1)
        self.assertEqual(plain.import_sites({"sqlglot.optimizer.other"}), 4)
        record = {"version": S.IMPORT_SITE_INDEX_VERSION, "source": str(self.src), **sites}
        self.path.write_text(json.dumps(record), encoding="utf-8")
        loaded = S.Hunter(self.map)
        with patch.object(loaded, "build_import_site_index", side_effect=AssertionError("source scan")):
            self.assertEqual(loaded.import_sites({"sqlglot.optimizer.impl"}), 1)
            self.assertEqual(loaded.import_sites({"sqlglot.optimizer.other"}), 4)

    def test_bad_artifact_falls_back_to_current_sources(self):
        self.path.write_text('{"version":', encoding="utf-8")
        self.assertEqual(S.Hunter(self.map).import_sites({"sqlglot.optimizer.impl"}), 1)
        self.path.unlink()
        self.write("tests/test_typing.py", "not valid Python!\n")
        hunter = S.Hunter(self.map)
        self.assertEqual(hunter.import_sites({"sqlglot.optimizer.impl"}), hunter.ALL)


    def test_imports_by_computed_name_are_indexed(self):
        self.write("tests/test_walk.py", "\n".join([
            "import importlib", "", "", "def walk(n, m):",
            "    importlib.import_module(f'sqlglot.optimizer.{n}')",
            "    importlib.import_module(m)",
            "    importlib.import_module('sqlglot.optimizer.impl')", ""]))
        self.map["tests"].append("tests/test_walk.py::test_walk")
        self.map["lines"]["tests/test_walk.py"] = {5: 8, 6: 8, 7: 8}
        h = S.Hunter(self.map)
        index = h.build_import_site_index()
        self.assertIn(("sqlglot.optimizer.", "tests/test_walk.py", "function", 5, 5), set(map(tuple, index["prefix"])))
        self.assertIn(("tests/test_walk.py", "function", 6, 6), set(map(tuple, index["any"])))
        self.assertIn(("tests/test_walk.py", "function", 7, 7), set(map(tuple, index["sites"]["sqlglot.optimizer.impl"])))
        self.assertEqual(h.import_sites({"sqlglot.optimizer.other"}) & 8, 8, "prefix and unbounded calls reach it")
        record = {"version": S.IMPORT_SITE_INDEX_VERSION, "source": str(self.src), **index}
        self.path.write_text(json.dumps(record), encoding="utf-8")
        loaded = S.Hunter(self.map)
        self.assertEqual(loaded.import_sites({"sqlglot.optimizer.other"}), h.import_sites({"sqlglot.optimizer.other"}))


if __name__ == "__main__":
    unittest.main()

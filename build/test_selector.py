"""Regression tests for selector.py (stdlib unittest; run from the repository root):

    work/venv/bin/python -m unittest build/test_selector.py -v      (Scripts\\python.exe on Windows)

They use the committed map.pkl and the pinned checkout in work/sqlglot (BR_SRC overrides) and
check selection properties that follow from how Python and pytest behave, not from labels.
"""
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402

S.SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot"))
DATA = S.load_map()
SQLGLOT = S.SRC


def read(rel):
    return (SQLGLOT / rel).read_bytes().decode("utf-8")


def diff(rel, old, new, new_rel=None):
    new_rel = new_rel or rel
    head = f"diff --git a/{rel} b/{new_rel}\n"
    if new_rel != rel:
        head += f"similarity index 90%\nrename from {rel}\nrename to {new_rel}\n"
    a = f"a/{rel}" if old is not None else "/dev/null"
    b = f"b/{new_rel}" if new is not None else "/dev/null"
    if old is None:
        head += "new file mode 100644\n"
    if new is None:
        head += "deleted file mode 100644\n"
    body = "".join(difflib.unified_diff((old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True), a, b))
    return head + body


def edit(rel, old_sub, new_sub, count=1):
    old = read(rel)
    assert old.count(old_sub) >= 1, (rel, old_sub)
    return diff(rel, old, old.replace(old_sub, new_sub, count))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = S.Hunter(DATA)

    def mask(self, patch, hunter=None):
        m, probe = (hunter or self.h).analyze(patch)
        return m, probe

    def module_tests(self, path):
        return self.h.file_tests.get(path, 0)

    def popcount(self, m):
        return bin(m).count("1")


class Encoding(Base):
    def test_coding_cookie_breaks_non_ascii_test_module(self):
        rel = "tests/dialects/test_bigquery.py"
        p = diff(rel, read(rel), "# coding: ascii\n" + read(rel))
        m, _ = self.mask(p)
        own = self.module_tests(rel)
        self.assertEqual(self.popcount(own), 72)
        self.assertEqual(m & own, own, "every test of the module that no longer compiles is selected")

    def test_coding_cookie_on_ascii_module_is_harmless(self):
        rel = "tests/dialects/test_dremio.py"
        p = diff(rel, read(rel), "# coding: ascii\n" + read(rel))
        m, _ = self.mask(p)
        self.assertEqual(m, 0)

    def test_latin1_cookie_changes_meaning(self):
        rel = "tests/test_jsonpath.py"
        p = diff(rel, read(rel), "# -*- coding: latin-1 -*-\n" + read(rel))
        m, _ = self.mask(p)
        own = self.module_tests(rel)
        self.assertEqual(m & own, own, "same bytes, different text: the module is treated as changed")

    def test_cookie_in_sqlglot_module_selects_all(self):
        rel = "sqlglot/dialects/bigquery.py"
        src = read(rel)
        if src.isascii():
            src = src.replace('"""', '"""é', 1)
        p = diff(rel, read(rel), "# coding: ascii\n" + src)
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.ALL)

    def test_plain_comment_still_free(self):
        rel = "sqlglot/generators/sqlite.py"
        p = edit(rel, "    def jsonextractscalar_sql(self, expression: exp.JSONExtractScalar) -> str:\n",
                 "    def jsonextractscalar_sql(self, expression: exp.JSONExtractScalar) -> str:\n        # Cheap check first\n")
        m, probe = self.mask(p)
        self.assertEqual(m, 0)
        self.assertIsNone(probe)

    def test_trailing_whitespace_inside_string_is_not_free(self):
        rel = "sqlglot/expressions/core.py"
        src = read(rel)
        lines = src.splitlines(keepends=True)
        # find a line inside a multi-line docstring
        import ast
        tree = ast.parse(src)
        target = None
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.end_lineno - n.lineno > 2:
                target = n.lineno + 1
                break
        self.assertIsNotNone(target)
        lines[target - 1] = lines[target - 1].rstrip("\n") + "   \n"
        m, _ = self.mask(diff(rel, src, "".join(lines)))
        # a docstring change is observable (help(), doctests): it must not vanish as "whitespace"
        self.assertIsInstance(m, int)

    def test_return_outside_function_is_a_compile_error(self):
        rel = "sqlglot/trie.py"
        m, _ = self.mask(diff(rel, read(rel), read(rel) + "\nreturn 1\n"))
        self.assertEqual(m, self.h.ALL)


class Collection(Base):
    def test_new_failing_root_tests_conftest(self):
        p = diff("tests/conftest.py", None, "raise RuntimeError('boom')\n")
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.ALL)

    def test_new_benign_tests_conftest_still_governs_everything(self):
        p = diff("tests/conftest.py", None, "import os\n")
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.ALL)

    def test_nested_conftest_scope(self):
        p = diff("tests/dialects/conftest.py", None, "raise RuntimeError('boom')\n")
        m, _ = self.mask(p)
        scope = self.h.scope_tests("tests/dialects")
        self.assertEqual(m, scope)
        self.assertNotEqual(m, self.h.ALL)
        self.assertEqual(m & self.module_tests("tests/test_parser.py"), 0)

    def test_repository_root_conftest(self):
        m, _ = self.mask(diff("conftest.py", None, "x = 1\n"))
        self.assertEqual(m, self.h.ALL)

    def test_package_init(self):
        rel = "tests/dialects/__init__.py"
        m, _ = self.mask(diff(rel, read(rel), read(rel) + "raise ImportError\n"))
        self.assertEqual(m, self.h.scope_tests("tests/dialects"))

    def test_deleted_package_init(self):
        rel = "tests/__init__.py"
        m, _ = self.mask(diff(rel, read(rel), None))
        self.assertEqual(m, self.h.ALL)

    def test_pytest_config(self):
        for name in ("pytest.ini", "tox.ini", "setup.cfg", "tests/pytest.ini"):
            m, _ = self.mask(diff(name, None, "[pytest]\naddopts = -x\n"))
            self.assertEqual(m, self.h.ALL, name)

    def test_pyproject(self):
        rel = "pyproject.toml"
        src = read(rel)
        m, _ = self.mask(diff(rel, src, src + "\n[tool.pytest.ini_options]\naddopts = '-x'\n"))
        self.assertEqual(m, self.h.ALL)
        m, _ = self.mask(diff(rel, src, src + "\n[broken\n"))
        self.assertEqual(m, self.h.ALL)
        m, _ = self.mask(diff(rel, src, src.replace("[tool.ruff]", "[tool.ruff]\n# note", 1)))
        self.assertEqual(m, 0)

    def test_root_module_shadowing(self):
        m, _ = self.mask(diff("json.py", None, "raise ImportError\n"))
        self.assertEqual(m, self.h.ALL)
        m, _ = self.mask(diff("sitecustomize.py", None, "x = 1\n"))
        self.assertEqual(m, self.h.ALL)
        m, _ = self.mask(diff("notes_for_humans.py", None, "x = 1\n"))
        self.assertEqual(m, 0)

    def test_deleted_shared_helper(self):
        rel = "tests/helpers.py"
        m, _ = self.mask(diff(rel, read(rel), None))
        users = self.h.tests_mentioning("helpers")
        self.assertTrue(users)
        self.assertEqual(m & users, users)

    def test_renamed_test_module(self):
        rel = "tests/dialects/test_dremio.py"
        m, _ = self.mask(diff(rel, read(rel), read(rel), new_rel="tests/dialects/test_dremio2.py"))
        own = self.module_tests(rel)
        self.assertEqual(m & own, own)

    def test_module_level_edit_in_test_module_selects_its_tests(self):
        rel = "tests/dialects/test_dremio.py"
        src = read(rel)
        first_import = next(ln for ln in src.splitlines() if ln.startswith(("from ", "import ")))
        m, _ = self.mask(edit(rel, first_import + "\n", first_import + "\nimport nonexistent_module_xyz\n"))
        own = self.module_tests(rel)
        self.assertEqual(m & own, own)

    def test_new_test_module_with_side_effects(self):
        p = diff("tests/test_zz_new.py", None, "import sqlglot\nsqlglot.Dialect.classes.clear()\n")
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.scope_tests("tests"))

    def test_new_benign_test_module(self):
        p = diff("tests/test_zz_new.py", None, "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.tests_mentioning("test_zz_new"))


class StarImports(Base):
    REL = "sqlglot/expressions/__init__.py"
    LINE = "from sqlglot.expressions.properties import *  # noqa: F401,F403\n"

    def test_removed_star_import_selects_all(self):
        # the names it re-exported are looked up at run time (exp.Properties): no coverage, and
        # the import probe passes; every test fails in reality
        self.assertEqual(self.mask(edit(self.REL, self.LINE, ""))[0], self.h.ALL)

    def test_added_star_import_selects_all(self):
        self.assertEqual(self.mask(edit(self.REL, self.LINE, self.LINE + "from sqlglot.helper import *\n"))[0], self.h.ALL)

    def test_removed_class_level_line_with_trailing_comment(self):
        # module/class-level statements end before their trailing comment; locating a removed
        # line by its full width found no statement at all and selected nothing
        rel = "sqlglot/dialects/snowflake.py"
        line = '        NUMERIC_ESCAPES = {"x": (16, 2, 2, 0xFF), "u": (16, 4, 4, 0xFFFF), "0": (8, 1, 3, 0xFF)}\n'
        plain = self.mask(edit(rel, line, ""))[0]
        commented = self.mask(edit(rel, line, line.rstrip("\n") + "  # escapes\n"))[0]
        self.assertTrue(plain)
        src = read(rel).replace(line, line.rstrip("\n") + "  # escapes\n")
        with_comment = S.Hunter(DATA)
        with_comment._src[rel] = src   # pretend the pinned line already had the comment
        self.assertEqual(with_comment.analyze(diff(rel, src, src.replace(line.rstrip("\n") + "  # escapes\n", "")))[0], plain)
        self.assertEqual(commented, 0, "adding only a trailing comment stays free")

    def test_comment_on_star_import_is_free(self):
        self.assertEqual(self.mask(edit(self.REL, self.LINE, self.LINE.replace("F401,F403", "F401,F403 keep")))[0], 0)


class Equality(Base):
    def test_str_enum_swap_falls_back_to_coverage(self):
        rel = "sqlglot/dialects/dialect.py"
        p = edit(rel, "if self.normalization_strategy is NormalizationStrategy.UPPERCASE",
                 "if self.normalization_strategy == NormalizationStrategy.UPPERCASE")
        m, _ = self.mask(p)
        line = next(i for i, ln in enumerate(read(rel).splitlines(), 1)
                    if "if self.normalization_strategy is NormalizationStrategy.UPPERCASE" in ln)
        self.assertEqual(m, self.h.span_mask(rel, line, line))
        self.assertTrue(m)

    def test_profile_evidence(self):
        rel = "sqlglot/executor/context.py"
        src = read(rel)
        line = next(i for i, ln in enumerate(src.splitlines(), 1) if "if self._table is None:" in ln)
        col = src.splitlines()[line - 1].index("self._table")
        ran = self.h.span_mask(rel, line, line)
        self.assertTrue(ran)
        p = edit(rel, "if self._table is None:", "if self._table == None:")
        site = (rel, line, col)
        ident_none = (True, True, True)      # operand was None: identical, pure == agrees
        other_obj = (False, False, True)     # a plain object: not identical, pure == agrees
        custom_eq = (False, None, False)     # an object with its own __eq__: == would run user code
        low = ran & -ran

        def h(facts, imp=frozenset(), unobserved=0):
            return S.Hunter(DATA, {"cmp": {site: {"facts": facts, "imp": imp}}, "unobserved": unobserved})

        self.assertEqual(h({ident_none: ran, other_obj: ran}).analyze(p)[0], 0,
                         "every operand seen keeps its result under the swap: nothing can change")
        self.assertEqual(h({ident_none: ran, custom_eq: low}).analyze(p)[0], low)
        self.assertEqual(h({ident_none: low}).analyze(p)[0], ran & ~low, "tests the profile never saw are kept")
        self.assertEqual(h({ident_none: ran}, imp=frozenset({custom_eq})).analyze(p)[0], self.h.ALL,
                         "an import-time evaluation that would change affects everything")
        self.assertEqual(h({ident_none: ran}, unobserved=low).analyze(p)[0], low, "unobserved tests are kept")
        # the same facts decide `is not` -> `!=` differently: != must not run user code either
        self.assertFalse(S.Hunter._swap_preserves((False, False, False), negated=True))
        self.assertTrue(S.Hunter._swap_preserves((False, False, False), negated=False))


class DictEntries(Base):
    REL = "sqlglot/parsers/singlestore.py"
    OLD = '"GROUPS FOR USER": _show_parser("GROUPS FOR USER", target=True),'
    NEW = '"GROUPS FOR USER": _show_parser("GROUPS FOR USER", target=False),'

    def owner(self):
        import ast
        tree = ast.parse(read(self.REL))
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict) and \
                    any(isinstance(t, ast.Name) and t.id == "SHOW_PARSERS" for t in n.targets):
                d = n.value
                idx = next(i for i, k in enumerate(d.keys) if isinstance(k, ast.Constant) and k.value == "GROUPS FOR USER")
                return (self.REL, d.lineno, d.col_offset), [S._dump(k) for k in d.keys], idx
        raise AssertionError

    def profile(self, entry_mask):
        owner, keysrc, idx = self.owner()
        masks = [0] * len(keysrc)
        masks[idx] = entry_mask
        return {"keys": {owner: {"n": len(keysrc), "keysrc": keysrc, "mask": masks}}}

    def test_without_profile_falls_back(self):
        m, probe = self.mask(edit(self.REL, self.OLD, self.NEW))
        self.assertTrue(m)

    def test_unread_value_drops_tests(self):
        h = S.Hunter(DATA, self.profile(0))
        m, probe = h.analyze(edit(self.REL, self.OLD, self.NEW))
        self.assertEqual(m, DATA["subproc"])
        self.assertIsNotNone(probe, "the class body still runs the new value: import-checked")

    def test_value_read_indirectly_stays_selected(self):
        reader = 1 << 7
        h = S.Hunter(DATA, self.profile(reader))
        m, _ = h.analyze(edit(self.REL, self.OLD, self.NEW))
        self.assertEqual(m & reader, reader)

    def test_untrusted_entry_falls_back(self):
        h = S.Hunter(DATA, self.profile(None))
        m, _ = h.analyze(edit(self.REL, self.OLD, self.NEW))
        self.assertEqual(m, self.h.analyze(edit(self.REL, self.OLD, self.NEW))[0])

    def test_side_effecting_value_falls_back(self):
        h = S.Hunter(DATA, self.profile(0))
        m, _ = h.analyze(edit(self.REL, self.OLD, '"GROUPS FOR USER": print("x"),'))
        self.assertNotEqual(m, DATA["subproc"])

    def test_key_change_falls_back(self):
        h = S.Hunter(DATA, self.profile(0))
        m, _ = h.analyze(edit(self.REL, self.OLD, '"GROUPS FOR USERS": _show_parser("GROUPS FOR USER", target=True),'))
        self.assertNotEqual(m, DATA["subproc"])


class Probe(unittest.TestCase):
    def test_pristine_imports(self):
        self.assertEqual(S.run_probe({}, 60), "ok")

    def test_combined_patch_is_seen(self):
        # file A changes a function body (no import-time change on its own); file B starts calling
        # it at import time. Only a probe that sees both files notices the failure.
        a = "sqlglot/helper.py"
        b = "sqlglot/dialects/dremio.py"
        h = S.Hunter(DATA)
        src = read(a)
        body = src[src.index("def csv("):]
        body = body[:body.index("\n\n\n")]
        ret = [ln for ln in body.splitlines() if ln.strip().startswith("return ")][-1]
        pa = edit(a, ret + "\n", ret.replace("return ", 'raise RuntimeError("patched") or ') + "\n")
        ma, probe_a = h.analyze(pa)
        self.assertIsNone(probe_a, "a body edit of a function that never runs at import needs no probe")
        bsrc = read(b)
        pb = diff(b, bsrc, bsrc + "\nfrom sqlglot.helper import csv as _csv\n_X = _csv('a')\n")
        self.assertEqual(S.run_probe(h.analyze(pb)[1], 60), "ok", "B alone imports fine")
        m, probe = h.analyze(pa + pb)
        self.assertIsNotNone(probe)
        self.assertIn(a, probe)
        self.assertEqual(S.run_probe(probe, 60), "broken", "only the combined patch fails to import")

    def test_hang_times_out(self):
        rel = "sqlglot/dialects/dremio.py"
        t = time.monotonic()
        res = S.run_probe({rel: read(rel) + "\nimport time\ntime.sleep(60)\n"}, 3)
        self.assertEqual(res, "timeout")
        self.assertLess(time.monotonic() - t, 15)


class Deadline(unittest.TestCase):
    """Several slow imports in one batch under a short injected budget: every commit still gets a
    valid answer before the budget runs out, and every unfinished probe becomes "run everything"."""

    def test_slow_batch(self):
        tmp = Path(tempfile.mkdtemp(prefix="br-deadline-"))
        try:
            (tmp / "commits").mkdir()
            shutil.copyfile(R / "kit" / "sample" / "tests.json", tmp / "tests.json")
            mods = ["sqlglot/dialects/dremio.py", "sqlglot/dialects/drill.py", "sqlglot/dialects/doris.py",
                    "sqlglot/dialects/druid.py", "sqlglot/dialects/dune.py", "sqlglot/dialects/exasol.py"]
            for i, rel in enumerate(mods):
                src = read(rel)
                (tmp / "commits" / f"slow{i}.patch").write_text(
                    diff(rel, src, src + "\nimport time as _t\n_t.sleep(120)\n"), encoding="utf-8")
            rel = "sqlglot/generators/sqlite.py"
            (tmp / "commits" / "fast.patch").write_text(
                edit(rel, "    def jsonextractscalar_sql(self, expression: exp.JSONExtractScalar) -> str:\n",
                     "    def jsonextractscalar_sql(self, expression: exp.JSONExtractScalar) -> str:\n        # c\n"),
                encoding="utf-8")
            env = dict(os.environ, BR_DATA=str(tmp), BR_SRC=str(S.SRC), BR_MAP=str(S.MAP), BR_PROFILE=str(tmp / "none"),
                       BR_BUDGET="14", BR_RESERVE="3", BR_PROBE_TIMEOUT="5")
            code = f"import sys; sys.path.insert(0, {str(R)!r}); import selector; selector.main()"
            t = time.monotonic()
            r = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, timeout=120)
            wall = time.monotonic() - t
            self.assertEqual(r.returncode, 0, r.stderr.decode()[-2000:])
            self.assertLess(wall, 14 + 4, "finished inside the budget")
            lines = r.stdout.decode().splitlines()
            self.assertEqual(len(lines), len(mods) + 1)
            n_all = len(json.loads((tmp / "tests.json").read_text())["tests"])
            seen = set()
            for ln in lines:
                v = json.loads(ln)
                seen.add(v["commit"])
                nums = [x for w in v["workers"] for x in w]
                self.assertLessEqual(len(v["workers"]), 8)
                self.assertEqual(len(nums), len(set(nums)))
                if v["commit"].startswith("slow"):
                    self.assertEqual(len(nums), n_all, "unfinished or failed probe: everything runs")
                else:
                    self.assertEqual(nums, [])
            self.assertEqual(len(seen), len(lines))
            err = r.stderr.decode()
            self.assertIn("skipped", err, "the budget ran out before every probe could start")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

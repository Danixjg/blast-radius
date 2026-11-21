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
        # other tables change what packaging tools read, never what pytest or sqlglot read here
        m, _ = self.mask(diff(rel, src, src.replace("line-length = 100", "line-length = 120", 1)))
        self.assertEqual(m, 0)
        m, _ = self.mask(diff(rel, src, src + "\n[tool.pytest]\ntimeout = 5\n"))
        self.assertEqual(m, self.h.ALL, "pytest 9 native table")
        m, _ = self.mask(diff(rel, src, src.replace("line-length = 100", "line-length = 120", 1)) +
                         diff("sqlglot/helper.py", read("sqlglot/helper.py"), read("sqlglot/helper.py")))
        self.assertEqual(m, 0, "also inside a multi-file patch")

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
        # its `from tests.dialects.test_dialect import Validator` imports a module collection
        # imports anyway: the rename does not reach every test
        self.assertNotEqual(m, self.h.scope_tests("tests"))
        self.assertEqual(m & ~(own | self.h.tests_mentioning("test_dremio") | self.h.tests_mentioning("test_dremio2")), 0)

    def test_new_test_module_importing_uncollected_helper(self):
        # tests/gen_fixtures.py is neither collected nor imported by a catalogued module: importing
        # it from a new test module runs code collection otherwise never runs
        self.assertIsNotNone(S.Hunter(DATA).source("tests/gen_fixtures.py"))
        self.assertFalse(self.h.collected_anyway("tests.gen_fixtures"))
        self.assertTrue(self.h.collected_anyway("tests.dialects.test_dialect"))
        self.assertTrue(self.h.collected_anyway("tests.helpers"))
        self.assertFalse(self.h.collected_anyway("tests.no_such_module"))
        p = diff("tests/test_zz_new.py", None,
                 "import unittest\nfrom tests.gen_fixtures import rewrite_fixtures\n\n\nclass T(unittest.TestCase):\n"
                 "    def test_x(self):\n        pass\n")
        m, _ = self.mask(p)
        self.assertEqual(m, self.h.scope_tests("tests"))

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


class SameProgram(unittest.TestCase):
    """same_ast: equal programs apart from positions; loosely, apart from docstrings, postponed
    annotations outside class bodies and TYPE_CHECKING-only code."""

    @staticmethod
    def check(old, new):
        import ast
        p = S.parse_patch(diff("sqlglot/x.py", old, new))[0]
        a, b = ast.parse(old), ast.parse(new)
        changes = S.line_map(p.hunks)
        (of, otc), (nf, ntc) = S.runtime_facts(a), S.runtime_facts(b)
        return S.same_ast(a, b, changes), S.same_ast(a, b, changes, loose=(of and nf, otc & ntc))

    BASE = ("from __future__ import annotations\n\nimport typing as t\n\nif t.TYPE_CHECKING:\n    import os\n\n\n"
            "X: int = 1\n\n\ndef f(a: int, b=16) -> int:\n    \"\"\"Doc.\"\"\"\n    s = 'q' 'r'\n    return (a + b) * 2\n\n\n"
            "class C:\n    \"\"\"Class doc.\"\"\"\n    y: int = 2\n    if True:\n        z: int = 3\n\n    def m(self, v: str) -> str:\n"
            "        return v\n")

    def same(self, old, new, strict, loose):
        self.assertEqual(self.check(old, new), (strict, loose), new)

    def test_layout_and_spelling(self):
        b = self.BASE
        self.same(b, b.replace("return (a + b) * 2", "return ( a+b )*2"), True, True)
        self.same(b, b.replace("return (a + b) * 2", "return (\n        a + b\n    ) * 2"), True, True)
        self.same(b, b.replace("b=16", "b=0x10"), True, True)
        self.same(b, b.replace("s = 'q' 'r'", 's = "qr"'), True, True)
        self.same(b, b.replace("    return v\n", "    return v  # same\n"), True, True)
        self.same(b, "# header\n" + b + "\n\n# trailer\n", True, True)

    def test_docstring_and_annotation_readers_are_kept(self):
        """The loose AST shortcut still selects tests that read the changed data as data."""
        d = dict(DATA)
        d["name_mask"] = dict(DATA["name_mask"])
        rel = "sqlglot/helper.py"
        src = read(rel)
        old = '    """Returns the value in `seq` at position `index`, or `None` if `index` is out of bounds."""'
        assert src.count(old) == 1
        patch = diff(rel, src, src.replace(old, '    """Changed docstring."""'))
        for name in ("__doc__", "__annotations__", "get_type_hints"):
            d["name_mask"][name] = 1 << 2
            m, probe = S.Hunter(d).analyze(patch)
            self.assertTrue(m >> 2 & 1, name)

    def test_value_changes(self):
        b = self.BASE
        self.same(b, b.replace("b=16", "b=16.0"), False, False)
        self.same(b, b.replace("X: int = 1", "X: int = True"), False, False)
        self.same(b, b.replace("* 2", "* 3"), False, False)
        self.same(b, b.replace("    return v\n", "    return v\n\n    def n(self):\n        pass\n"), False, False)
        self.same(b, b.replace("s = 'q' 'r'", "s = 'qr '"), False, False)

    def test_docstrings(self):
        b = self.BASE
        self.same(b, b.replace('"""Doc."""', '"""Other doc."""'), False, True)
        self.same(b, b.replace('"""Class doc."""', '"""Changed."""'), False, True)
        self.same(b, b.replace('    """Doc."""\n', ''), False, True)
        self.same(b, b.replace('"""Doc."""', 'f"""Doc."""'), False, False)

    def test_annotations(self):
        b = self.BASE
        self.same(b, b.replace("def f(a: int, b=16) -> int:", "def f(a: str, b=16) -> str:"), False, True)
        self.same(b, b.replace("def m(self, v: str) -> str:", "def m(self, v: bytes) -> bytes:"), False, True)
        self.same(b, b.replace("X: int = 1", "X: str = 1"), False, True)
        self.same(b, b.replace("    y: int = 2", "    y: t.ClassVar[int] = 2"), False, False)   # dataclass fields
        self.same(b, b.replace("        z: int = 3", "        z: str = 3"), False, False)          # still class level
        eager = b.replace("from __future__ import annotations\n", "")
        self.same(eager, eager.replace("def f(a: int, b=16) -> int:", "def f(a: str, b=16) -> int:"), False, False)

    def test_type_checking_blocks(self):
        b = self.BASE
        self.same(b, b.replace("    import os\n", "    import os\n    import sys\n"), False, True)
        self.same(b, b.replace("if t.TYPE_CHECKING:", "if True:"), False, False)
        not_typing = b.replace("import typing as t", "import other as t")
        self.same(not_typing, not_typing.replace("    import os\n", "    import os\n    import sys\n"), False, False)

    def test_unread_new_function(self):
        h = S.Hunter(DATA)
        rel = "sqlglot/helper.py"
        src = read(rel)
        anchor = "\n\ndef seq_get("
        assert anchor in src

        def add(code):
            return h.analyze(diff(rel, src, src.replace(anchor, "\n\n" + code + "\n\n" + anchor, 1)))

        self.assertEqual(add("def _fresh_helper(x: int, y: str = 'a') -> int:\n    return x\n"), (0, None))
        self.assertEqual(add("async def _fresh_coro(x=(1, 2)):\n    return x\n"), (0, None))
        for code in ("def len(x):\n    return 0\n",                       # shadows a builtin
                     "def parse_one(x):\n    return x\n",                 # a name code looks up
                     "def ensure_list(x):\n    return x\n",               # redefinition
                     "def test_new():\n    return 1\n",                   # collected as a test
                     "def setup_module():\n    pass\n",                   # module hook
                     "def __getattr__(name):\n    return 1\n",            # module attribute protocol
                     "@t.no_type_check\ndef _fresh_decorated(x):\n    return x\n",
                     "def _fresh_default(x=print('import time')):\n    return x\n"):
            m, _ = add(code)
            self.assertTrue(m, code)

    def test_unread_cached_import(self):
        h = S.Hunter(DATA)
        for rel, anchor in (("tests/helpers.py", "import os\n"),
                            ("sqlglot/parsers/dune.py", "from sqlglot.parsers.trino import TrinoParser\n")):
            src = read(rel)
            assert anchor in src

            def add(line):
                return h.analyze(diff(rel, src, src.replace(anchor, anchor + line, 1)))

            self.assertEqual(add("import os as _unused_os  # noqa\n"), (0, None), rel)
            self.assertEqual(add("import sys as _unused_sys, stat as _unused_stat\n"), (0, None), rel)
            for line in ("import json as _unused_json\n",      # not loaded at start-up: its code would run
                         "import os as path\n",                # a name code reads
                         "import os as len\n",                 # shadows a builtin
                         "from os import sep as _unused_sep\n"):
                m, _ = add(line)
                self.assertTrue(m, (rel, line))

    def test_quiet_constant_edit_skips_the_probe(self):
        h = S.Hunter(DATA)

        def run(rel, old, new):
            src = read(rel)
            assert src.count(old) == 1, (rel, old)
            return h.analyze(diff(rel, src, src.replace(old, new)))

        m, probe = run("sqlglot/dialects/tsql.py", "    WEEK_OFFSET = -1\n", "    WEEK_OFFSET = -2\n")
        self.assertTrue(m, "run-time readers stay selected")
        self.assertIsNone(probe, "no import-time reader, constant value: the import cannot change")
        self.assertFalse(h.lazy_pending)
        for rel, old, new, why in (
                ("sqlglot/dialects/tsql.py", "    WEEK_OFFSET = -1\n", "    WEEK_OFFSET = SOMETHING\n", "a name can fail"),
                ("sqlglot/dialects/tsql.py", "        QUOTES = [\"'\", '\"']\n", "        QUOTES = [\"'\"]\n",
                 "read by Tokenizer.__init_subclass__ at import"),
                ("sqlglot/dialects/dialect.py", '    DIALECT = ""\n', '    DIALECT = "x"\n', "an Enum body"),
        ):
            m, probe = run(rel, old, new)
            self.assertIsNotNone(probe, why)

    def test_selector_skips_same_program_edits(self):
        h = S.Hunter(DATA)
        rel = "sqlglot/helper.py"
        src = read(rel)
        anchor = "    if isinstance(value, tuple):\n        return list(value)\n"
        assert anchor in src
        m, probe = h.analyze(diff(rel, src, src.replace(anchor, "    if isinstance( value,tuple ):\n        return list( value )\n")))
        self.assertEqual((m, probe), (0, None))
        m, _ = h.analyze(diff(rel, src, src.replace(anchor, "    if isinstance(value, tuple):\n        return list(value)[:1]\n")))
        self.assertTrue(m, "a real change still selects the tests that ran it")


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
        self.assertEqual(m, 0)
        self.assertIsNone(probe, "same closure factory, constant arguments: building it cannot raise")
        self.assertFalse(h.lazy_pending)

    def test_value_that_can_raise_is_probed(self):
        h = S.Hunter(DATA, self.profile(0))
        for new in ('"GROUPS FOR USER": _show_parser("GROUPS FOR USER", target=TARGET_FLAG),',   # a name
                    '"GROUPS FOR USER": _show_parser(exp.Show.NAME, target=False),'):          # an attribute
            m, probe = h.analyze(edit(self.REL, self.OLD, new))
            self.assertEqual(m, 0)
            self.assertIsNotNone(probe, new)
            self.assertTrue(h.lazy_pending, "the fresh-interpreter test waits for the probe's verdict")
            self.assertEqual(h.lazy_mask, DATA["subproc"])

    def test_import_safe(self):
        import ast
        h = S.Hunter(DATA)
        rel = "sqlglot/parsers/singlestore.py"
        safe = ["1", "-2", "'x'", "(16, 4, 4, 0xFFFF)", "[None, True]", "{'a': (1, 2)}", "lambda self, e: self.f(e)"]
        unsafe = ["X", "a.b", "1 / 0", "-x", "f()", "(1, *x)", "{**d}", "{k: 1}", "lambda self, e=X: e", "not 1"]
        for s in safe:
            self.assertTrue(h.import_safe(rel, ast.parse(s, mode="eval").body, None), s)
        for s in unsafe:
            self.assertFalse(h.import_safe(rel, ast.parse(s, mode="eval").body, None), s)
        call = ast.parse('_show_parser("X", target=False)', mode="eval").body
        old = ast.parse('_show_parser("X", target=True)', mode="eval").body
        other = ast.parse('_other_factory("X", target=True)', mode="eval").body
        self.assertTrue(h.import_safe(rel, call, old))
        self.assertFalse(h.import_safe(rel, call, other), "a different callee may not be bound yet")
        self.assertFalse(h.import_safe(rel, call, None))

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

    def test_lazy_assertion(self):
        """The probe replays test_lazy_load: a fresh `import sqlglot` must create one dialect class."""
        self.assertEqual(S.probe_report({}, 60), ("ok", True))
        rel = "sqlglot/__init__.py"
        eager = {rel: read(rel) + "\nfrom sqlglot.dialects.duckdb import DuckDB as _DuckDB\n"}
        self.assertEqual(S.probe_report(eager, 60), ("ok", False), "a second dialect class appears")
        rel = "sqlglot/dialects/snowflake.py"
        self.assertEqual(S.probe_report({rel: read(rel) + "\n_X = 1\n"}, 60), ("ok", True),
                         "a lazily loaded dialect never runs during `import sqlglot`")
        self.assertEqual(S.probe_report({rel: read(rel) + "\nraise RuntimeError\n"}, 60), ("broken", None))
        init = "sqlglot/__init__.py"
        silenced = {init: read(init) + "\nimport builtins\nbuiltins.print = lambda *a, **k: None\n"}
        self.assertEqual(S.probe_report(silenced, 60), ("ok", False), "print no longer reaches stdout")


def run_main(patches, env_extra=None):
    """Run selector.main() on {cid: patch} with the sample catalogue; {cid: set of test numbers}."""
    tmp = Path(tempfile.mkdtemp(prefix="br-main-"))
    try:
        (tmp / "commits").mkdir()
        shutil.copyfile(R / "kit" / "sample" / "tests.json", tmp / "tests.json")
        for cid, text in patches.items():
            (tmp / "commits" / f"{cid}.patch").write_text(text, encoding="utf-8")
        env = dict(os.environ, BR_DATA=str(tmp), BR_SRC=str(S.SRC), BR_MAP=str(S.MAP), BR_PROFILE=str(S.PROFILE))
        env.update(env_extra or {})
        code = f"import sys; sys.path.insert(0, {str(R)!r}); import selector; selector.main()"
        r = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, timeout=300)
        assert r.returncode == 0, r.stderr.decode()[-2000:]
        out = {}
        for ln in r.stdout.decode().splitlines():
            v = json.loads(ln)
            out[v["commit"]] = {n for w in v["workers"] for n in w}
        return out, r.stderr.decode()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def number_of(test_id):
    for t in json.loads((R / "kit" / "sample" / "tests.json").read_text())["tests"]:
        if t["id"] == test_id:
            return t["number"]
    raise KeyError(test_id)


class LazyTest(unittest.TestCase):
    def test_probe_decides_the_fresh_interpreter_test(self):
        lazy = number_of(S.LAZY_TEST)
        rel = "sqlglot/__init__.py"
        src = read(rel)
        anchor = 'pretty = False\n'
        assert anchor in src
        patches = {
            # a module constant nothing reads: import-time, probed, the lazy assertion still holds
            "quiet": diff(rel, src, src.replace(anchor, anchor + "_UNUSED_CONSTANT = 1\n")),
            # same place, but `import sqlglot` now loads a dialect: test_lazy_load would fail
            "eager": diff(rel, src, src.replace(anchor, anchor + "from sqlglot.dialects import duckdb as _duck\n")),
        }
        out, err = run_main(patches, {"BR_VERIFY": "0"})
        self.assertNotIn(lazy, out["quiet"], err[-3000:])
        self.assertIn(lazy, out["eager"], err[-3000:])

    def test_silenced_print_keeps_the_lazy_test(self):
        """The probe must replay the test's stdout assertion, not just the class count."""
        lazy = number_of(S.LAZY_TEST)
        rel = "sqlglot/__init__.py"
        src = read(rel)
        patches = {"silent": diff(rel, src,
                                  src + "\nimport builtins\nbuiltins.print = lambda *a, **k: None\n")}
        out, err = run_main(patches, {"BR_VERIFY": "0"})
        self.assertIn(lazy, out["silent"], err[-3000:])


class Verifier(unittest.TestCase):
    """Running catalogue tests on the patched source: only a clean, timely pass counts."""
    T = "tests/dialects/test_dremio.py::TestDremio::test_date_add"
    REL = "sqlglot/parsers/dremio.py"
    RET = "            return expression_class(this=date_arg, expression=interval_arg)\n"

    def test_pristine_pass(self):
        self.assertEqual(S.verify_report({}, [(self.T, 5.0)], 60), {self.T: "pass"})

    def test_broken_code_is_not_a_pass(self):
        src = read(self.REL)
        assert self.RET in src
        bad = {self.REL: src.replace(self.RET, '            raise ValueError("patched")\n')}
        self.assertEqual(S.verify_report(bad, [(self.T, 5.0)], 60), {self.T: "error"})

    def test_failed_import_is_not_a_pass(self):
        rel = "sqlglot/dialects/dremio.py"
        rep = S.verify_report({rel: read(rel) + "\nraise ImportError('patched')\n"}, [(self.T, 5.0)], 60)
        # the dialect module loads lazily inside the test, which then errors
        self.assertEqual(rep, {self.T: "error"})

    def test_unknown_test_is_not_a_pass(self):
        t = "tests/dialects/test_dremio.py::TestDremio::test_does_not_exist"
        self.assertEqual(S.verify_report({}, [(t, 5.0)], 60), {t: "uncollected"})

    def test_slow_test_is_not_a_pass(self):
        src = read(self.REL)
        head = "    def _builder(args):\n"
        assert head in src
        slow = {self.REL: src.replace(head, head + "        import time\n        time.sleep(0.6)\n")}
        self.assertEqual(S.verify_report(slow, [(self.T, 1.0)], 60), {self.T: "slow"})

    def test_outcomes(self):
        """Every unittest outcome other than a clean pass is reported as such; a test whose class
        setup failed never reports (the caller keeps it)."""
        tmp = Path(tempfile.mkdtemp(prefix="br-verify-"))
        old_src = S.SRC
        try:
            (tmp / "sqlglot").mkdir()
            (tmp / "sqlglot" / "__init__.py").write_text("")
            (tmp / "tests").mkdir()
            (tmp / "tests" / "__init__.py").write_text("")
            (tmp / "tests" / "test_fake.py").write_text(
                "import unittest\n\n"
                "class TestFake(unittest.TestCase):\n"
                "    def test_pass(self):\n        pass\n"
                "    def test_subfail(self):\n"
                "        for i in range(3):\n"
                "            with self.subTest(i=i):\n"
                "                self.assertNotEqual(i, 1)\n"
                "    def test_skip(self):\n        self.skipTest('no')\n"
                "    @unittest.expectedFailure\n    def test_xfail(self):\n        self.fail()\n"
                "    @unittest.expectedFailure\n    def test_xpass(self):\n        pass\n"
                "    def test_fail(self):\n        self.fail()\n"
                "    def test_error(self):\n        raise RuntimeError\n\n"
                "class TestBrokenSetup(unittest.TestCase):\n"
                "    @classmethod\n    def setUpClass(cls):\n        raise RuntimeError\n"
                "    def test_never(self):\n        pass\n")
            S.SRC = tmp
            f = "tests/test_fake.py::TestFake::"
            names = ["test_pass", "test_subfail", "test_skip", "test_xfail", "test_xpass", "test_fail", "test_error"]
            tests = [(f + n, 5.0) for n in names] + [("tests/test_fake.py::TestBrokenSetup::test_never", 5.0)]
            rep = S.verify_report({}, tests, 60)
            self.assertEqual(rep, {f + "test_pass": "pass", f + "test_subfail": "fail", f + "test_skip": "skip",
                                   f + "test_xfail": "xfail", f + "test_xpass": "xpass", f + "test_fail": "fail",
                                   f + "test_error": "error"})
        finally:
            S.SRC = old_src
            shutil.rmtree(tmp, ignore_errors=True)

    def test_harmless_patch_runs_nothing_and_breaking_keeps_failures(self):
        src = read(self.REL)
        line = "            date_arg, interval_arg = args\n"
        assert line in src
        harmless = diff(self.REL, src, src.replace(line, line + "            _unused = None\n"))
        breaking = diff(self.REL, src, src.replace(self.RET, '            raise ValueError("patched")\n'))
        out, err = run_main({"harmless": harmless, "breaking": breaking})
        self.assertIn("harmless verifier: ran", err)
        self.assertEqual(out["harmless"], set(), err[-3000:])
        self.assertIn(number_of(self.T), out["breaking"], err[-3000:])
        out0, _ = run_main({"breaking": breaking}, {"BR_VERIFY": "0"})
        self.assertLessEqual(out["breaking"], out0["breaking"], "verification only ever removes tests")


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

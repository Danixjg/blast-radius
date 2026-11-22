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

    NEW_BODY = "\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"

    def new_module_mask(self, imports):
        return self.mask(diff("tests/test_zz_new.py", None, "import unittest\n" + imports + self.NEW_BODY))[0]

    def test_new_test_module_importing_uncollected_helper_by_any_spelling(self):
        # every spelling below runs tests/gen_fixtures.py, which collection otherwise never runs
        for imports in ("from tests import gen_fixtures\n",
                        "from tests import helpers, gen_fixtures\n",
                        "import tests.gen_fixtures\n",
                        "import tests.gen_fixtures as g\n",
                        "from . import gen_fixtures\n",
                        "from .gen_fixtures import rewrite_fixtures\n"):
            self.assertEqual(self.new_module_mask(imports), self.h.scope_tests("tests"), imports)

    def test_new_test_module_importing_collected_modules_stays_narrow(self):
        # modules collection imports anyway, in every spelling: a cache hit, not new code
        for imports in ("from tests.dialects import test_dialect\n",
                        "from tests import helpers\n",
                        "import tests.helpers\n",
                        "from .dialects.test_dialect import Validator\n",
                        "from . import helpers\n"):
            self.assertNotEqual(self.new_module_mask(imports), self.h.scope_tests("tests"), imports)

    def test_new_test_module_unresolvable_relative_import(self):
        # beyond the top-level package: no module to vouch for, so it counts as unknown code
        self.assertEqual(self.new_module_mask("from ... import something\n"), self.h.scope_tests("tests"))

    CONFTEST = "import pytest\n\n\n@pytest.fixture\ndef {name}():\n    raise RuntimeError('boom')\n"

    def test_new_conftest_with_an_unrequested_fixture_selects_nothing(self):
        m, _ = self.mask(diff("tests/conftest.py", None, self.CONFTEST.format(name="unused_fixture_xyz")))
        self.assertEqual(m, 0)

    def test_new_conftest_overriding_a_builtin_fixture(self):
        # pytest and its plugins request these themselves (tmpdir requests tmp_path, for example),
        # so no mention in the test sources can vouch that nothing uses the override
        for name in ("tmp_path", "tmp_path_factory", "monkeypatch", "capsys", "caplog", "worker_id",
                     "testrun_uid", "cache", "recwarn", "pytestconfig", "record_property", "doctest_namespace"):
            m, _ = self.mask(diff("tests/conftest.py", None, self.CONFTEST.format(name=name)))
            self.assertEqual(m, self.h.ALL, name)


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

    def test_short_circuited_tests_dropped_only_behind_an_and_or(self):
        """EQ_SHORT_CIRCUIT: a test that ran the line but was never seen evaluating a comparison
        placed after the first operand of an and/or chain short-circuited before it. A standalone
        comparison has no such excuse: unseen tests stay."""
        old = S.EQ_SHORT_CIRCUIT
        try:
            rel = "sqlglot/tokenizer_core.py"
            src = read(rel)
            anchor = "if self._comments and token_type == TokenType.SEMICOLON and self.tokens:"
            assert anchor in src
            line = next(i for i, ln in enumerate(src.splitlines(), 1) if anchor in ln)
            col = src.splitlines()[line - 1].index("token_type == TokenType.SEMICOLON")
            ran = self.h.span_mask(rel, line, line)
            low = ran & -ran
            prof = {"cmp": {(rel, line, col): {"facts": {(True, True, True): low}, "imp": frozenset()}}}
            p = edit(rel, anchor, anchor.replace("token_type == TokenType", "token_type is TokenType"))
            S.EQ_SHORT_CIRCUIT = False
            self.assertEqual(S.Hunter(DATA, prof).analyze(p)[0], ran & ~low, "off: unseen tests kept")
            S.EQ_SHORT_CIRCUIT = True
            self.assertEqual(S.Hunter(DATA, prof).analyze(p)[0], 0, "on: short-circuited tests dropped")
            bad = dict(prof["cmp"][(rel, line, col)], facts={(False, None, False): low})
            self.assertEqual(S.Hunter(DATA, {"cmp": {(rel, line, col): bad}}).analyze(p)[0], low,
                             "a fact the swap changes still selects its tests")
            # a standalone comparison (the first, always evaluated operand) keeps unseen tests
            rel2 = "sqlglot/executor/context.py"
            src2 = read(rel2)
            line2 = next(i for i, ln in enumerate(src2.splitlines(), 1) if "if self._table is None:" in ln)
            col2 = src2.splitlines()[line2 - 1].index("self._table")
            ran2 = self.h.span_mask(rel2, line2, line2)
            low2 = ran2 & -ran2
            prof2 = {"cmp": {(rel2, line2, col2): {"facts": {(True, True, True): low2}, "imp": frozenset()}}}
            p2 = edit(rel2, "if self._table is None:", "if self._table == None:")
            self.assertEqual(S.Hunter(DATA, prof2).analyze(p2)[0], ran2 & ~low2)
        finally:
            S.EQ_SHORT_CIRCUIT = old


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


class ImportOnlyRuns(Base):
    """deferred_runs: a test whose coverage holds a class attribute's lines only because its
    process imported the module during the test (pool workers import Hive, Presto, Trino, MySQL
    and Athena through pushdown_predicates) is not selected for a change to that attribute."""

    TPCDS = "tests/test_executor.py::TestExecutor::test_execute_tpcds"

    def bit(self, test_id):
        return 1 << DATA["tests"].index(test_id)

    def stmt(self, rel, old, new, line_text):
        import ast
        src = read(rel)
        assert src.count(old) == 1, (rel, old)
        otree, ntree = ast.parse(src), ast.parse(src.replace(old, new))
        line = src[:src.index(line_text)].count("\n") + 1
        site = S.locate(otree, line, 0, 10 ** 9, precise=False)
        return site, otree, ntree

    def test_class_attribute_value_drops_tests_that_only_imported_the_module(self):
        rel = "sqlglot/dialects/hive.py"
        self.assertTrue(self.h.span_mask(rel, 1, 1) & self.bit(self.TPCDS), "TPC-DS's pool imports hive.py")
        m, probe = self.mask(edit(rel, '        "E": "%a",\n', '        "E": "%a_",\n'))
        self.assertFalse(m & self.bit(self.TPCDS), "TPC-DS never reads Hive.TIME_MAPPING")
        self.assertTrue(m & self.bit("tests/dialects/test_hive.py::TestHive::test_time"), "readers stay")
        self.assertIsNotNone(probe, "TIME_MAPPING is read while the class is created: the import is probed")

    def test_lambda_lines_keep_their_runners(self):
        # a one-line lambda's body shares its line with the dict entry: running that line can mean
        # calling the lambda, so whoever ran it stays (here TPC-DS, which only imported the module)
        rel = "sqlglot/parsers/hive.py"
        old = '        "BASE64": exp.ToBase64.from_arg_list,\n'
        site, otree, ntree = self.stmt(rel, old, old.replace("ToBase64", "FromBase64"), old)
        keep = self.h.deferred_runs(rel, site.cls_path, site.stmt, otree, ntree)
        self.assertIsNotNone(keep, "a plain binding whose value builds quietly")
        src = read(rel).splitlines()
        lam1 = src.index('        "COLLECT_LIST": lambda args: exp.ArrayAgg(this=seq_get(args, 0), nulls_excluded=True),') + 1
        lam2 = src.index('        "CONCAT_WS": lambda args, dialect: exp.ConcatWs(') + 1
        lambdas = self.h.span_mask(rel, lam1, lam1) | self.h.span_mask(rel, lam2, lam2 + 5)
        self.assertTrue(lambdas & self.bit(self.TPCDS), "TPC-DS's import ran the one-line lambda's line")
        self.assertEqual(keep & lambdas, lambdas, "every lambda body line keeps its runners")
        # whoever imported the module also ran the one-line lambdas' lines: nothing is dropped here
        self.assertEqual(keep, self.h.span_mask(rel, site.span[0], site.span[1]))

    def test_only_plain_bindings_with_quiet_values(self):
        rel = "sqlglot/dialects/hive.py"
        old = "    COERCES_TO = defaultdict(set, deepcopy(TypeAnnotator.COERCES_TO))\n"
        site, otree, ntree = self.stmt(rel, old, old.replace("set", "list"), old)
        self.assertIsNone(self.h.deferred_runs(rel, site.cls_path, site.stmt, otree, ntree),
                          "building it calls code that is not a closure factory")
        old = '        "E": "%a",\n'
        site, otree, ntree = self.stmt(rel, old, '        "E": print("x"),\n', old)
        self.assertIsNone(self.h.deferred_runs(rel, site.cls_path, site.stmt, otree, ntree), "new value runs code")
        site, otree, ntree = self.stmt(rel, old, '        "E": "%a_",\n', old)
        self.assertEqual(self.h.deferred_runs(rel, site.cls_path, site.stmt, otree, ntree), 0)
        self.assertIsNone(self.h.deferred_runs("tests/x.py", site.cls_path, site.stmt, otree, ntree),
                          "test modules run at collection: other rules decide")


class FailedImports(Base):
    """import_failure_mask: when the probe reports modules whose patched import fails (but not
    sqlglot itself), the tests that can reach them instead of every test."""

    def ids(self, m):
        return {t for i, t in enumerate(DATA["tests"]) if t and m >> i & 1}

    def test_verdict_carries_the_failed_modules(self):
        rep = {"failed": [["sqlglot.dialects.oracle", "ModuleNotFoundError: x"], ["sqlglot.parsers.oracle", "E"]],
               "unserved": [], "lazy": True}
        res = S._verdict("noise\n@@BR-PROBE@@" + json.dumps(rep) + "\n")
        self.assertEqual(res, "broken")
        self.assertEqual(res.failed, ("sqlglot.dialects.oracle", "sqlglot.parsers.oracle"))
        rep["failed"].append("garbled")
        self.assertEqual(S._verdict("\n@@BR-PROBE@@" + json.dumps(rep)).failed, (), "unreadable: nothing to narrow on")

    def test_unbounded_failures(self):
        self.assertIsNone(self.h.import_failure_mask(["sqlglot", "sqlglot.dialects.oracle"]), "sqlglot itself: all")
        self.assertIsNone(self.h.import_failure_mask([]))
        self.assertIsNone(self.h.import_failure_mask(["tests.helpers"]))

    def test_dialect_failure(self):
        ids = self.ids(self.h.import_failure_mask(["sqlglot.dialects.oracle", "sqlglot.parsers.oracle"]))
        self.assertIn("tests/dialects/test_oracle.py::TestOracle::test_oracle", ids)
        for t in ("tests/dialects/test_dialect.py::TestDialect::test_enum",                 # Dialect.classes
                  "tests/test_generator.py::TestGenerator::test_generator_memory"):         # every Dialects member
            self.assertIn(t, ids, "bulk dialect loaders reach any dialect module")
        self.assertLess(len(ids), len(DATA["tests"]) // 4)

    def test_dynamic_typing_import_failure_keeps_source_reader(self):
        # TestTyping reads each source to find its parent, then imports the module by name.
        ids = self.ids(self.h.import_failure_mask(["sqlglot.typing.redshift"]))
        self.assertIn("tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata", ids)

    def test_module_level_import_in_a_test_module_fails_its_importers(self):
        # tests/dialects/test_dialect.py does `from sqlglot.dialects import BigQuery, Hive, Snowflake,
        # Spark2` at module level, and every dialect test module imports its Validator
        ids = self.ids(self.h.import_failure_mask(["sqlglot.dialects.snowflake"]))
        self.assertIn("tests/dialects/test_athena.py::TestAthena::test_athena", ids)

    def test_function_level_import_fails_its_runners(self):
        # pushdown_predicates imports Athena inside the function
        ids = self.ids(self.h.import_failure_mask(["sqlglot.dialects.athena"]))
        self.assertIn("tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates", ids)

    def test_selector_narrows_a_broken_probe(self):
        tmp = Path(tempfile.mkdtemp(prefix="br-failed-import-"))
        try:
            (tmp / "commits").mkdir()
            shutil.copyfile(R / "kit" / "sample" / "tests.json", tmp / "tests.json")
            for cid, rel in (("dialect", "sqlglot/dialects/oracle.py"), ("core", "sqlglot/helper.py")):
                src = read(rel)
                (tmp / "commits" / f"{cid}.patch").write_text(
                    diff(rel, src, src + "\nimport sqlglot_does_not_exist_xyz\n"), encoding="utf-8")
            env = dict(os.environ, BR_DATA=str(tmp), BR_SRC=str(S.SRC), BR_MAP=str(S.MAP), BR_PROFILE=str(tmp / "none"),
                       BR_VERIFY="0")
            code = f"import sys; sys.path.insert(0, {str(R)!r}); import selector; selector.main()"
            r = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr.decode()[-2000:])
            catalog = json.loads((tmp / "tests.json").read_text())["tests"]
            by_id = {t["id"]: t["number"] for t in catalog}
            got = {}
            for ln in r.stdout.decode().splitlines():
                v = json.loads(ln)
                got[v["commit"]] = {x for w in v["workers"] for x in w}
            self.assertEqual(len(got["core"]), len(catalog), "sqlglot itself fails to import: everything")
            self.assertLess(len(got["dialect"]), len(catalog) // 4)
            for t in ("tests/dialects/test_oracle.py::TestOracle::test_oracle",
                      "tests/test_generator.py::TestGenerator::test_generator_memory"):
                self.assertIn(by_id[t], got["dialect"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_imports_by_computed_name(self):
        import ast
        # test_typing walks sqlglot/typing with pkgutil and imports each module by name
        ids = self.ids(self.h.import_failure_mask(["sqlglot.typing.sqlite", "sqlglot.dialects.sqlite"]))
        self.assertIn("tests/test_typing.py::TestTyping::test_no_redundant_expression_metadata", ids)
        src = ("import importlib\n\ndef f(n, m):\n    importlib.import_module(f'sqlglot.typing.{n}')\n"
               "    importlib.import_module(m)\n    __import__('sqlglot.lineage')\n"
               "    importlib.import_module(f'{__name__}.{n}')\n")
        got = [(c.lineno, scope, tgt) for c, scope, tgt in S._dynamic_imports(ast.parse(src), "sqlglot.optimizer")]
        self.assertEqual(sorted(got), [(4, "function", ("prefix", "sqlglot.typing.")), (5, "function", ("any",)),
                                       (6, "function", ("exact", "sqlglot.lineage")),
                                       (7, "function", ("prefix", "sqlglot.optimizer."))])

    def test_import_scopes(self):
        import ast
        src = ("import typing as t\nimport sqlglot.a\nif t.TYPE_CHECKING:\n    from sqlglot import b\n"
               "def f():\n    from sqlglot.dialects import Snowflake\nclass C:\n    import sqlglot.c\n")
        got = [(st.lineno, scope) for st, scope in S._imports(ast.parse(src))]
        self.assertEqual(got, [(1, "module"), (2, "module"), (4, "type_checking"), (6, "function"), (8, "module")])
        st = ast.parse("from sqlglot.dialects import Snowflake as S\n").body[0]
        self.assertTrue({"sqlglot", "sqlglot.dialects", "sqlglot.dialects.snowflake"} <=
                        S._imported_modules(st, "tests/x.py"))


class OneLineLambdas(Base):
    """A call of a lambda reports no line event for its first line (sys.monitoring, CPython 3.12+),
    so coverage of that line only names whoever created the lambda."""

    def test_monitoring_reports_no_first_line_of_a_called_lambda(self):
        import sys as _sys
        if not hasattr(_sys, "monitoring"):
            self.skipTest("no sys.monitoring")
        mon = _sys.monitoring
        tool = next(i for i in range(6) if mon.get_tool(i) is None)
        code = compile("F = {'a': lambda x: x + 1}\n", "lam_probe.py", "exec")
        ns = {}
        exec(code, ns)
        hits = []
        mon.use_tool_id(tool, "lam-probe")
        try:
            mon.register_callback(tool, mon.events.LINE,
                                  lambda c, ln: hits.append(ln) if c.co_filename == "lam_probe.py" else None)
            mon.set_events(tool, mon.events.LINE)
            ns["F"]["a"](1)
        finally:
            mon.set_events(tool, 0)
            mon.register_callback(tool, mon.events.LINE, None)
            mon.free_tool_id(tool)
        self.assertEqual(hits, [], "the premise of locate()'s lambda rule")

    def test_edit_on_a_dict_entry_lambda_selects_its_readers(self):
        rel = "sqlglot/parsers/tsql.py"
        old = '"ISNULL": lambda args: build_coalesce(args=args, is_null=True),'
        m, _ = self.mask(edit(rel, old, old.replace("is_null=True", "is_null=False")))
        self.assertTrue(m & (1 << DATA["tests"].index("tests/dialects/test_tsql.py::TestTSQL::test_isnull")),
                        "the parser test calling FUNCTIONS['ISNULL'] (line coverage shows only the import)")


class RemovedImports(Base):
    """removed_import_mask: a removed module-level import line whose names nothing else binds or
    takes from the module selects the module's own readers of those names."""

    def test_stdlib_import_selects_the_modules_readers(self):
        rel = "sqlglot/generators/tsql.py"
        m, probe = self.mask(edit(rel, "from functools import reduce\n", ""))
        src = read(rel).splitlines()
        line = next(i for i, ln in enumerate(src, 1) if "reduce(" in ln)
        readers = self.h.span_mask(rel, line, line)
        self.assertTrue(readers)
        self.assertEqual(m & readers, readers)
        self.assertLess(self.popcount(m), 3 * self.popcount(readers) + 10, "only the module's own readers")
        self.assertIsNotNone(probe, "a read while the module imports would fail the import: probed")

    def test_namespace_scans_and_reexports_fall_back(self):
        import ast
        for rel, line in (
                # sqlglot.expressions builds its registries with subclasses(__name__, ...)
                ("sqlglot/expressions/__init__.py", "from sqlglot.expressions.array import _ExplodeOuter  # noqa: F401,E402\n"),
                # sqlglot.planner is not loaded by `import sqlglot`: dropping this import loads less
                ("sqlglot/executor/python.py", "from sqlglot import exp, planner, tokens\n")):
            src = read(rel)
            otree, ntree = ast.parse(src), ast.parse(src.replace(line, ""))
            st = next(s for s in otree.body if isinstance(s, (ast.Import, ast.ImportFrom)) and
                      ast.get_source_segment(src, s) == line.split("  #")[0].rstrip("\n"))
            self.assertIsNone(self.h.removed_import_mask(rel, st, otree, ntree), rel)

    def test_name_still_bound_falls_back(self):
        import ast
        rel = "sqlglot/generators/tsql.py"
        src = read(rel)
        new = src.replace("from functools import reduce\n", "").replace("\n\ndef ", "\n\nreduce = None\n\n\ndef ", 1)
        otree, ntree = ast.parse(src), ast.parse(new)
        st = next(s for s in otree.body if isinstance(s, ast.ImportFrom) and s.module == "functools")
        self.assertIsNone(self.h.removed_import_mask(rel, st, otree, ntree))


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
        # redirecting stdout also fails the test's stdout assertion; replaying print into a fresh
        # buffer would not notice (the probe's own report then has nowhere to go either)
        redirected = {init: read(init) + "\nimport os as _o, sys as _s\n_s.stdout = open(_o.devnull, 'w')\n"}
        self.assertNotEqual(S.probe_report(redirected, 60), ("ok", True))
        # the test decodes its capture as text: bytes that are not UTF-8 make it error
        undecodable = {init: read(init) + "\nimport sys as _s\n_s.stdout.buffer.write(b'\\xff\\xfe')\n"}
        self.assertEqual(S.probe_report(undecodable, 60), ("ok", False))
        noisy = {init: read(init) + "\nprint('hello from import')\n"}
        self.assertEqual(S.probe_report(noisy, 60), ("ok", True), "other output is harmless")

    def test_server_served_probe_keeps_the_pristine_lazy_verdict(self):
        """A probe served by the probe server starts after a pristine `import sqlglot` (its patch
        touches nothing that import loaded or looked for), so the assertion holds as pristine."""
        class FakeServer:
            def __init__(self, res):
                self.res = res

            def eligible(self, files):
                return True

            def run(self, files, timeout):
                return self.res

        self.assertEqual(S.probe_report({"sqlglot/dialects/druid.py": "x = 1\n"}, 5, FakeServer("ok")), ("ok", True))
        self.assertEqual(S.probe_report({}, 5, FakeServer("broken")), ("broken", None))
        self.assertEqual(S.probe_report({}, 60, FakeServer(None)), ("ok", True), "no verdict: probed fresh")

    def test_run_probes_reports_lazy_verdicts(self):
        rel = "sqlglot/__init__.py"
        eager = {rel: read(rel) + "\nfrom sqlglot.dialects.duckdb import DuckDB as _DuckDB\n"}
        lazy = {}
        self.assertEqual(S.run_probes({"a": {}, "b": eager, "c": dict(eager)}, lazy=lazy), {"a": "ok", "b": "ok", "c": "ok"})
        self.assertEqual(lazy, {"a": True, "b": False, "c": False}, "identical jobs share one probe")


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

    def test_class_and_module_teardown_errors_are_not_a_pass(self):
        """Errors after a test's own stopTest (tearDownClass, class cleanups, tearDownModule) reach
        unittest on a placeholder, not on a test; pytest charges them to the last test of the class
        or module. No test the error could belong to may be reported as a pass."""
        tmp = Path(tempfile.mkdtemp(prefix="br-verify-"))
        old_src = S.SRC
        try:
            (tmp / "sqlglot").mkdir()
            (tmp / "sqlglot" / "__init__.py").write_text("")
            (tmp / "tests").mkdir()
            (tmp / "tests" / "__init__.py").write_text("")
            (tmp / "tests" / "test_td.py").write_text(
                "import unittest\n\n"
                "class TestTeardown(unittest.TestCase):\n"
                "    @classmethod\n    def tearDownClass(cls):\n        raise RuntimeError\n"
                "    def test_a(self):\n        pass\n\n"
                "class TestCleanup(unittest.TestCase):\n"
                "    @classmethod\n    def setUpClass(cls):\n        cls.addClassCleanup(cls.boom)\n"
                "    @staticmethod\n    def boom():\n        raise RuntimeError\n"
                "    def test_b(self):\n        pass\n")
            (tmp / "tests" / "test_tdm.py").write_text(
                "import unittest\n\n"
                "def tearDownModule():\n    raise RuntimeError\n\n"
                "class TestModule(unittest.TestCase):\n"
                "    def test_c(self):\n        pass\n")
            S.SRC = tmp
            tests = [("tests/test_td.py::TestTeardown::test_a", 5.0), ("tests/test_td.py::TestCleanup::test_b", 5.0),
                     ("tests/test_tdm.py::TestModule::test_c", 5.0)]
            rep = S.verify_report({}, tests, 60)
            for t, _ in tests:
                self.assertNotEqual(rep.get(t), "pass", (t, rep))
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

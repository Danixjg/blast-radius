"""Blast Radius selector: a hunter and a catcher.

hunter  - reads each patch, pins every edit to the exact AST node it touches (a statement in
          a function, a lambda, a class attribute, a module constant, a dictionary entry, a
          fixture file) and maps it to the tests that can observe it, using a per-test coverage
          map of the pinned sqlglot recorded at build time (lines run per test, including spawned
          pool workers and class-level setup; fixture files opened; classes instantiated) and an
          optional runtime profile of pristine facts (which tests read each literal dictionary
          entry; which operand facts each test saw at `is`/`==` comparisons, from which an
          operator edit is decided here at run time).
catcher - the safety net behind the hunter. Whenever precise reasoning runs out (files that do
          not compile under Python's byte-level rules, pytest collection files, deleted files,
          code that runs while modules import, names it cannot follow, missing sources) it widens
          to a conservative superset, and for import-time edits it imports every sqlglot module
          from the combined patch in a sandbox: if anything raises or hangs, every test is run.
Finally the selected tests are packed onto 1..8 workers to minimise max(load) + 2 s/worker.

The whole batch runs against one monotonic deadline: probes are bounded by what is left, and a
probe that cannot finish in time (or is inconclusive) turns that commit into "run everything".

Standard library only. stdout carries exactly one JSON line per commit; logs go to stderr.
"""

import ast
import io
import json
import os
import pickle
import re
import subprocess
import sys
import threading
import time
import tokenize
from pathlib import Path

T0 = time.monotonic()
HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BR_DATA", "/data"))
SRC = Path(os.environ.get("BR_SRC", HERE / "src"))
MAP = Path(os.environ.get("BR_MAP", HERE / "map.pkl"))
PROFILE = Path(os.environ.get("BR_PROFILE", HERE / "profile.pkl"))
WORKER_MS = 2000
MAX_WORKERS = 8
# The batch must finish inside the run's wall limit (3 min practice, 4.5 min final). Probes only
# ever take this long when patched imports hang, so the budget is deliberately well inside both.
BUDGET_S = float(os.environ.get("BR_BUDGET", "150"))
RESERVE_S = float(os.environ.get("BR_RESERVE", "5"))          # kept back to schedule and emit
# a probe normally takes 0.3 s (import every sqlglot module, patched ones from source); anything
# far slower is treated as a hang, which fails every test anyway, so the commit runs everything
PROBE_TIMEOUT_S = float(os.environ.get("BR_PROBE_TIMEOUT", "8"))
MIN_PROBE_S = 1.0                                               # not worth starting below this
PROBE_PARALLEL = max(1, min(2, os.cpu_count() or 1))
# extra probe-server checkpoints inside the dialect block (see probe_stages); 0 = after `sqlglot` only
PROBE_CHECKPOINTS = int(os.environ.get("BR_PROBE_CHECKPOINTS", "2"))
# Verification: when a patch only edits existing sqlglot modules and its selection is small and
# made only of tests that can run here (stdlib only: no process pools, subprocesses, fixture files
# or source-file reads), those tests are run on the patched source and every test that ran and
# passed is dropped (a harmless edit then runs nothing). The CPU this costs is charged, so a
# selection is verified only when half its schedule cost (the share of edits that break nothing)
# exceeds the expected CPU: a fixed start-up cost plus the tests' recorded seconds.
VERIFY = os.environ.get("BR_VERIFY", "1") != "0"
VERIFY_MAX_MS = int(float(os.environ.get("BR_VERIFY_MAX", "1.5")) * 1000)
VERIFY_START_MS = int(float(os.environ.get("BR_VERIFY_START", "0.35")) * 1000)
VERIFY_UNSAFE_FILES = {
    "tests/test_executor.py", "tests/test_optimizer.py",   # duckdb/pandas, spawned process pools
    "tests/test_typing.py",                               # reads package files from disk
    "tests/test_sqlglotc.py", "tests/test_integration_loader.py",   # files outside the image
    "tests/test_dialect_entry_points.py", "tests/test_dialect_imports.py",   # rewrite global registries
}
# tracemalloc counts every thread's allocations: under pytest-xdist the worker's I/O thread makes
# this test fail whenever many other tests fail, which a run here cannot reproduce
VERIFY_UNSAFE_TESTS = {"tests/test_generator.py::TestGenerator::test_generator_memory"}
# build/verify-requirements.txt installs exactly these (kit/requirements.lock versions); without
# them sqlglot's date folding (dateutil) and pytz-importing test modules would run differently
VERIFY_ENV = {"python-dateutil": "4.0.0", "tzdata": "2026.6", "pytz": "2026.6", "mypy-extensions": "1.2.0",
              "typing-extensions": "4.16.0"}
DIALECT_DIRS = ("dialects", "parsers", "generators", "typing", "tokenizers")
CORE_MODULES = {"sqlglot", "sqlglot.tokens", "sqlglot.tokenizer_core", "sqlglot.parser", "sqlglot.generator",
                "sqlglot.dialects.dialect", "sqlglot.expressions.core", "sqlglot.helper", "sqlglot.trie"}
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)
if hasattr(ast, "TryStar"):
    COMPOUND = COMPOUND + (ast.TryStar,)
# pytest reads these at start-up; a broken or changed one can fail every test
PYTEST_CONFIG = {"pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "pyproject.toml", "tox.ini", "setup.cfg"}
TEST_FILE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")
# runs `python -c "import sqlglot; assert len(sqlglot.Dialect._classes) == 1; print('Success')"`;
# the import probe performs the same import first, in a fresh interpreter, and reports the check
LAZY_TEST = "tests/dialects/test_dialect.py::TestDialect::test_lazy_load"
# top-level names a new file at the repository root (first on sys.path in the test run) shadows
SHADOW_EXTRA = {"sitecustomize", "usercustomize", "pytest", "_pytest", "pluggy", "xdist", "execnet", "duckdb",
                "pandas", "numpy", "dateutil", "pytz", "iniconfig", "packaging", "pytest_timeout", "sqlglot",
                "tests", "conftest", "sqlglotrs", "sqlglotc", "typing_extensions", "psutil"}


def log(*a):
    print(*a, file=sys.stderr)


# Fixtures pytest 9 and the installed plugins (xdist, timeout) define. pytest reserves `request`
# even for unused fixtures; the others can be requested by pytest's own code or plugins (tmpdir
# requests tmp_path, for example), which no mention in the test sources can rule out.
PYTEST_FIXTURES = frozenset((
    "request", "cache", "capsys", "capsysbinary", "capfd", "capfdbinary", "capteesys", "caplog",
    "doctest_namespace", "pytestconfig", "record_property", "record_xml_attribute",
    "record_testsuite_property", "recwarn", "subtests", "tmpdir", "tmpdir_factory", "tmp_path",
    "tmp_path_factory", "monkeypatch", "pytester", "testdir", "linecomp", "LineMatcher",
    "worker_id", "testrun_uid",
))


def module_of(rel):
    m = rel[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


def remaining():
    return T0 + BUDGET_S - time.monotonic()


# ============================================================================ patches


class FilePatch:
    __slots__ = ("old", "new", "hunks")

    def __init__(self, old=None, new=None):
        self.old, self.new, self.hunks = old, new, []


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip(p):
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_patch(text):
    files, cur = [], None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", ln)
            cur = FilePatch(*(m.groups() if m else (None, None)))
            files.append(cur)
        elif cur is not None and ln.startswith("rename from "):
            cur.old = ln[12:].strip()
        elif cur is not None and ln.startswith("rename to "):
            cur.new = ln[10:].strip()
        elif cur is not None and ln.startswith("deleted file"):
            cur.new = None
        elif cur is not None and ln.startswith("new file"):
            cur.old = None
        elif ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            if cur is None or cur.hunks:
                cur = FilePatch()
                files.append(cur)
            cur.old, cur.new = _strip(ln[4:]), _strip(lines[i + 1][4:])
            i += 1
        elif cur is not None and ln.startswith("@@"):
            m = HUNK.match(ln)
            if m:
                os_, ol, ns, nl = int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1)
                body, o, n = [], 0, 0
                i += 1
                while i < len(lines) and (o < ol or n < nl):
                    b = lines[i] or " "
                    if b[0] == "\\":
                        i += 1
                        continue
                    if b[0] == " ":
                        o, n = o + 1, n + 1
                    elif b[0] == "-":
                        o += 1
                    elif b[0] == "+":
                        n += 1
                    else:
                        break
                    body.append(b)
                    i += 1
                while i < len(lines) and lines[i].startswith("\\"):
                    i += 1
                cur.hunks.append((os_, ol, ns, nl, body))
                continue
        i += 1
    return files


def apply_hunks(old_text, hunks):
    """The patched text, or None when the context does not match."""
    old = old_text.splitlines(keepends=True)
    out, pos = [], 0
    for os_, ol, _, _, body in hunks:
        start = os_ - 1 if ol else os_
        if start < pos:
            return None
        out.extend(old[pos:start])
        pos = start
        for b in body:
            t, s = b[0], b[1:]
            if t in " -":
                if pos >= len(old) or old[pos].rstrip("\r\n") != s.rstrip("\r\n"):
                    return None
                if t == " ":
                    out.append(old[pos])
                pos += 1
            else:
                out.append(s + "\n")
    out.extend(old[pos:])
    return "".join(out)


def line_map(hunks):
    """(old lines changed, new lines changed, old -> new line number of an unchanged line)."""
    ochanged, nchanged, ctx, bounds = set(), set(), {}, []
    for os_, ol, ns, nl, body in hunks:
        o, n = (os_ if ol else os_ + 1), (ns if nl else ns + 1)
        first = o
        for b in body:
            if b[0] == " ":
                ctx[o] = n
                o, n = o + 1, n + 1
            elif b[0] == "-":
                ochanged.add(o)
                o += 1
            else:
                nchanged.add(n)
                n += 1
        bounds.append((first, o - 1, n - o))   # old lines after this hunk move by n - o

    def to_new(line):
        if line in ctx:
            return ctx[line]
        if line in ochanged:
            return None
        shift = 0
        for lo, hi, after in bounds:
            if line > hi:
                shift = after
            else:
                break
        return line + shift
    return ochanged, nchanged, to_new


_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _without_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        return body[1:]
    return body


def runtime_facts(tree):
    """(postponed annotations, names that mean typing.TYPE_CHECKING) of a module."""
    future, tc = False, set()
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.level == 0:
            if st.module == "__future__" and any(a.name == "annotations" for a in st.names):
                future = True
            if st.module == "typing":
                tc.update(a.asname or a.name for a in st.names if a.name == "TYPE_CHECKING")
        elif isinstance(st, ast.Import):
            tc.update((a.asname or a.name) + ".TYPE_CHECKING" for a in st.names if a.name == "typing")
    return future, frozenset(tc)


def _tc_test(node, tc):
    if isinstance(node, ast.Name):
        return node.id in tc
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and \
        f"{node.value.id}.{node.attr}" in tc


def same_ast(a, b, changes, loose=None, in_class=False):
    """True when old node a and new node b are the same program text-independently: equal ASTs
    apart from positions. With loose=(future, tc) also apart from what never runs or is only stored
    as data nobody in sqlglot reads: docstrings (__doc__), annotations of function arguments and
    returns and of non-class variables when annotations are postponed (stored as strings; class-level
    ones stay: dataclasses, NamedTuple and TypedDict read them), and the body of an unchanged
    `if TYPE_CHECKING:` (False at run time). A pair of nodes lying wholly in unchanged lines at
    matching places of the diff is equal by construction, so only the changed regions are compared."""
    if type(a) is not type(b):
        return False
    if not isinstance(a, ast.AST):
        if isinstance(a, list):
            return len(a) == len(b) and all(same_ast(x, y, changes, loose, in_class) for x, y in zip(a, b))
        if isinstance(a, (float, complex)):
            return repr(a) == repr(b)
        return a == b
    ochanged, nchanged, to_new = changes
    lo, hi = getattr(a, "lineno", None), getattr(a, "end_lineno", None)
    if lo is not None and hi is not None and getattr(b, "end_lineno", None) is not None:
        if to_new(lo) == b.lineno and to_new(hi) == b.end_lineno and a.col_offset == b.col_offset and \
                a.end_col_offset == b.end_col_offset and not any(lo <= c <= hi for c in ochanged) and \
                not any(b.lineno <= c <= b.end_lineno for c in nchanged):
            return True
    skip = ()
    if loose is not None:
        future, tc = loose
        if future and isinstance(a, ast.arg):
            skip = ("annotation", "type_comment")
        elif future and isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
            skip = ("returns", "type_comment")
        elif future and isinstance(a, ast.AnnAssign) and not in_class:
            skip = ("annotation",)
        elif isinstance(a, ast.If) and _tc_test(a.test, tc) and same_ast(a.test, b.test, changes):
            skip = ("body",)
    for field in a._fields:
        if field in skip:
            continue
        x, y = getattr(a, field, None), getattr(b, field, None)
        if loose is not None and field == "body" and isinstance(a, _DOC_OWNERS):
            x, y = _without_docstring(x), _without_docstring(y)
        # class level reaches through if/try/with blocks of a class body and stops at functions
        if isinstance(a, ast.ClassDef):
            inner = in_class or field == "body"
        elif isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner = False
        else:
            inner = in_class
        if not same_ast(x, y, changes, loose, inner):
            return False
    return True


def edits(hunks):
    """Yields (kind, old_line, new_line, old_text, new_text) per edited line.

    kind 'replace': an old line swapped 1:1 for a new one; 'remove': an old line dropped;
    'add': a new line inserted (old_line is the old line it was inserted before)."""
    for os_, _, ns, _, body in hunks:
        o, n, i = os_, ns, 0
        while i < len(body):
            if body[i][0] == " ":
                o, n, i = o + 1, n + 1, i + 1
                continue
            minus, plus = [], []
            while i < len(body) and body[i][0] == "-":
                minus.append((o, body[i][1:]))
                o, i = o + 1, i + 1
            while i < len(body) and body[i][0] == "+":
                plus.append((n, body[i][1:]))
                n, i = n + 1, i + 1
            if minus and len(minus) == len(plus):
                for (ol, ot), (nl, nt) in zip(minus, plus):
                    yield "replace", ol, nl, ot, nt
            else:
                for ol, ot in minus:
                    yield "remove", ol, None, ot, None
                for nl, nt in plus:
                    yield "add", o, nl, None, nt


def changed_cols(a, b):
    """Column range [c0, c1) of a that differs from b."""
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    return p, max(p + 1, len(a) - s)


def compile_check(new_bytes, rel):
    """(tree, problem): parse and fully compile the patched file the way the interpreter would
    import it (BOM, coding cookie, symbol table and code generation errors included), and make sure
    the text this module analyses is the text Python will run."""
    try:
        tree = ast.parse(new_bytes, rel)
        compile(tree, rel, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, UnicodeError, MemoryError, RecursionError) as e:
        return None, f"does not compile: {e!r}"[:200]
    try:
        enc, _ = tokenize.detect_encoding(io.BytesIO(new_bytes).readline)
    except SyntaxError as e:
        return None, f"bad encoding declaration: {e!r}"[:200]
    if enc not in ("utf-8", "utf-8-sig") and not new_bytes.isascii():
        try:
            as_python = new_bytes.decode(enc)
        except (LookupError, UnicodeError):
            return None, "undecodable"
        if as_python.lstrip("\ufeff") != new_bytes.decode("utf-8", "surrogateescape").lstrip("\ufeff"):
            return None, f"declared encoding {enc} changes the source text"
    return tree, None


# Swapping `is` <-> `==` (or `is not` <-> `!=`) is only harmless when, for the operands that
# actually reach the comparison, identity and equality agree and equality has no side effects.
# The text alone cannot show that (overloaded __eq__, str- or int-backed enums), so the decision
# is made per edit from pristine operand facts the profile recorded at that comparison (identity,
# the result of side-effect-free equality, whether != is side-effect free); see eq_swap_mask.
_EQ_OP = re.compile(r"\bis not\b|\bis\b|==|!=")
_EQ_PAIRS = {("is", "=="), ("==", "is"), ("is not", "!="), ("!=", "is not")}


def eq_swap_cols(a, b):
    """Column of the operator when only `is`<->`==` / `is not`<->`!=` changed between the lines."""
    ma, mb = list(_EQ_OP.finditer(a)), list(_EQ_OP.finditer(b))
    if len(ma) != 1 or len(mb) != 1:
        return None
    if (ma[0].group(), mb[0].group()) not in _EQ_PAIRS:
        return None
    if a[:ma[0].start()] != b[:mb[0].start()] or a[ma[0].end():] != b[mb[0].end():]:
        return None
    return ma[0].start(), ma[0].end()


# ============================================================================ AST helpers


def start_of(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", ())])


def _contains(node, line, c0, c1):
    s = getattr(node, "lineno", None)
    if s is None:
        return None   # transparent node (arguments, comprehension, ...)
    if isinstance(node, DEFS):
        s = start_of(node)
        if s < line <= node.end_lineno or (s == line and (s != node.lineno or node.col_offset <= c0)):
            return True
        return False
    e = node.end_lineno
    if not (s <= line <= e):
        return False
    if s == line and node.col_offset > c0:
        return False
    if e == line and node.end_col_offset < c1:
        return False
    return True


def chain_at(tree, line, c0=0, c1=10 ** 9):
    """Nodes from the module down to the innermost node holding line[c0:c1]."""
    chain = [tree]
    node = tree
    while True:
        nxt = _find_child(node, line, c0, c1)
        if nxt is None:
            return chain
        chain.append(nxt)
        node = nxt


def _find_child(node, line, c0, c1):
    for ch in ast.iter_child_nodes(node):
        r = _contains(ch, line, c0, c1)
        if r:
            return ch
        if r is None:
            sub = _find_child(ch, line, c0, c1)
            if sub is not None:
                return sub
    return None


def line_cols(text):
    """Columns that identify a whole removed/added line: its first code character. (The full
    line would include a trailing comment, which no statement node spans, so a module- or
    class-level statement like `x = 1  # note` was never found.)"""
    t = text.rstrip()
    c0 = len(t) - len(t.lstrip())
    return c0, c0 + 1


def assigned_names(st):
    names = []
    if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    names.append(n.id)
                elif isinstance(n, ast.Attribute):
                    names.append(n.attr)
    elif isinstance(st, DEFS):
        names.append(st.name)
    elif isinstance(st, (ast.Import, ast.ImportFrom)):
        names.extend((a.asname or a.name).split(".")[0] for a in st.names)
    elif isinstance(st, COMPOUND):
        for n in ast.walk(st):
            if n is not st and isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Import, ast.ImportFrom) + DEFS):
                names.extend(assigned_names(n))
    return names


def star_import(st):
    """The statement is, or contains, `from X import *`."""
    return any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(st))


def is_docstring(st):
    return isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str)


class Site:
    """Where one edit lands."""

    __slots__ = ("kind", "fn", "span", "cls_path", "stmt", "func_path")

    def __init__(self, kind, fn=None, span=None, cls_path=(), stmt=None, func_path=()):
        self.kind = kind            # 'func' | 'stmt' | 'none'
        self.fn = fn                # innermost function-like node
        self.span = span            # (a, b) lines whose coverage decides it
        self.cls_path = cls_path    # enclosing class names (outermost first)
        self.stmt = stmt            # class-body / module-level statement
        self.func_path = func_path  # qualified path of defs/classes down to fn


def locate(tree, line, c0, c1, precise=True):
    chain = chain_at(tree, line, c0, c1)
    fn_i = None
    for i in range(len(chain) - 1, 0, -1):
        if isinstance(chain[i], FUNC):
            fn_i = i
            break
    path, cls = [], []
    for node in chain[1: (fn_i + 1) if fn_i is not None else len(chain)]:
        if isinstance(node, ast.ClassDef):
            path.append(node.name)
            cls.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            path.append(node.name)
            cls = []   # classes inside a function are not module attributes
    if fn_i is not None:
        fn = chain[fn_i]
        whole = (start_of(fn) if not isinstance(fn, ast.Lambda) else fn.lineno, fn.end_lineno)
        if isinstance(fn, ast.Lambda):
            return Site("func", fn, (fn.lineno, fn.end_lineno), tuple(cls), func_path=tuple(path))
        inner = chain[fn_i + 1:]
        stmts = [n for n in inner if isinstance(n, ast.stmt)]
        if not stmts or not precise:
            # signature, decorator or the def itself, or imprecise mode
            return Site("func", fn, whole, tuple(cls), func_path=tuple(path))
        st = stmts[-1]
        if isinstance(st, DEFS):
            span = (start_of(st), st.end_lineno)
        elif isinstance(st, COMPOUND):
            # the change sits in the header (the deeper statements would have been found)
            span = (st.lineno, max(st.lineno, line))
        else:
            span = (st.lineno, st.end_lineno)
        return Site("func", fn, span, tuple(cls), func_path=tuple(path))
    # class body or module level: the statement right below the innermost class (or module)
    k = max(i for i, n in enumerate(chain) if isinstance(n, (ast.Module, ast.ClassDef)))
    if k + 1 < len(chain) and isinstance(chain[k + 1], ast.stmt):
        st = chain[k + 1]
    elif isinstance(chain[k], ast.ClassDef):
        st = chain[k]   # an edit in the class header (bases, keywords): the class itself changes
    else:
        return Site("none")
    return Site("stmt", span=(start_of(st), st.end_lineno), cls_path=tuple(cls), stmt=st, func_path=tuple(path))


def find_def(tree, path):
    """The FunctionDef/ClassDef at a qualified path, or None."""
    node = tree
    for name in path:
        nxt = None
        for ch in ast.walk(node) if node is tree else ast.iter_child_nodes(node):
            if isinstance(ch, DEFS) and ch.name == name:
                nxt = ch
                break
        if nxt is None:
            return None
        node = nxt
    return node


def _dump(node):
    return ast.dump(node, include_attributes=False) if node is not None else None


def _body_stmt(tree, cls_path, target_dump):
    """The unique statement in a class body (or the module) assigning exactly this target."""
    owner = find_def(tree, cls_path) if cls_path else tree
    if owner is None:
        return None
    hits = []
    for st in owner.body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1:
            t = st.targets[0]
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            t = st.target
        else:
            continue
        if _dump(t) == target_dump:
            hits.append(st)
    return hits[0] if len(hits) == 1 else None


# ============================================================================ map format

# A packed map stores its two big sections as one pickle per key, so a run decodes only the
# files and names its patches touch. Unpickling all ~200k coverage masks and ~150k use tuples
# up front was most of the map's load time. Plain (unpacked) maps still load unchanged.
LAZY_KEY = "lazy"
LAZY_SECTIONS = ("lines", "uses")


class LazyView:
    """Read-only mapping over {key: pickled value}; decodes each value once, on first use."""

    __slots__ = ("_raw", "_done")

    def __init__(self, raw):
        self._raw, self._done = raw, {}

    def get(self, key, default=None):
        try:
            return self._done[key]
        except KeyError:
            blob = self._raw.get(key)
            if blob is None:
                return default
            v = self._done[key] = pickle.loads(blob)
            return v

    def __contains__(self, key):
        return key in self._raw

    def __len__(self):
        return len(self._raw)


def pack_map(data):
    """Plain map -> packed map (new dict; the input is left alone). Packing twice is a no-op."""
    if LAZY_KEY in data:
        return data
    out = dict(data)
    for k in LAZY_SECTIONS:
        out[k] = {key: pickle.dumps(v, protocol=pickle.HIGHEST_PROTOCOL) for key, v in data[k].items()}
    out[LAZY_KEY] = LAZY_SECTIONS
    return out


def unpack_map(data):
    """Packed map -> plain map, for tools that want every section decoded."""
    if LAZY_KEY not in data:
        return data
    out = {k: v for k, v in data.items() if k != LAZY_KEY}
    for k in data[LAZY_KEY]:
        out[k] = {key: pickle.loads(b) for key, b in data[k].items()}
    return out


# ============================================================================ hunter


class Hunter:
    def __init__(self, data, profile=None):
        if LAZY_KEY in data:
            data = dict(data)
            for k in data[LAZY_KEY]:
                data[k] = LazyView(data[k])
        self.d = data
        self.n = len(data["tests"])
        self.ALL = (1 << self.n) - 1
        self.lines = data["lines"]
        self.classes = data["classes"]
        self.imp = {f: set(v) for f, v in data["imp_lines"].items()}
        self._src, self._tree, self._group, self._mention = {}, {}, {}, {}
        self.file_tests = {}
        for i, t in enumerate(data["tests"]):
            if t is None:
                continue   # untraced at build time: main() keeps it as an unmapped test
            f = t.split("::")[0]
            self.file_tests[f] = self.file_tests.get(f, 0) | (1 << i)
        self.test_files = sorted(self.file_tests)
        self.prof = profile or {}
        self.keys = self.prof.get("keys", {})
        self.cmp = self.prof.get("cmp", {})
        self.unobserved = self.prof.get("unobserved", 0)
        self.import_time = False
        self.probe_files = {}
        self.need_probe = False
        self.lazy_pending = False
        self._kd_memo = {}
        self._fresh_memo, self._nchanged = {}, set()
        # the one fresh-interpreter test whose assertion the import probe replays exactly
        self.lazy_mask = 0
        for i, t in enumerate(data["tests"]):
            if t == LAZY_TEST:
                self.lazy_mask = 1 << i
        # tests the verifier must not run here (see VERIFY_UNSAFE_FILES); untraced ones too
        bad = data["subproc"]
        for f, m in data["opens"].items():
            # fixtures are not in the image; a sqlglot source read from disk would be unpatched
            if f.startswith("tests/fixtures/") or not f.endswith((".py", ".pyc")) or \
                    (f.startswith("sqlglot/") and not f.endswith(".pyc")):
                bad |= m
        for f, m in self.file_tests.items():
            if f in VERIFY_UNSAFE_FILES:
                bad |= m
        for i, t in enumerate(data["tests"]):
            if t in VERIFY_UNSAFE_TESTS:
                bad |= 1 << i
        self.unverifiable = bad
        self.verify_files = None

    # ---------------------------------------------------------------- sources
    def source(self, rel):
        if rel not in self._src:
            try:
                self._src[rel] = (SRC / rel).read_bytes().decode("utf-8", "surrogateescape")
            except Exception:
                self._src[rel] = None
        return self._src[rel]

    def tree(self, rel):
        if rel not in self._tree:
            src = self.source(rel)
            try:
                self._tree[rel] = ast.parse(src) if src is not None else None
            except SyntaxError:
                self._tree[rel] = None
        return self._tree[rel]

    # ---------------------------------------------------------------- masks
    def span_mask(self, rel, a, b):
        lm = self.lines.get(rel)
        m = 0
        if lm:
            for ln in range(a, b + 1):
                m |= lm.get(ln, 0)
        return m

    def file_mask(self, rel):
        m = 0
        for v in self.lines.get(rel, {}).values():
            m |= v
        return m

    def scope_tests(self, d):
        """Tests collected from directory d (repository-relative, '' for everything)."""
        d = d.strip("/")
        if d in ("", "."):
            return self.ALL
        m = 0
        for f, v in self.file_tests.items():
            if f == d or f.startswith(d + "/"):
                m |= v
        return m

    def group_mask(self, rel):
        parts = rel.split("/")
        if len(parts) != 3 or parts[0] != "sqlglot" or parts[1] not in DIALECT_DIRS:
            return 0
        name = parts[2][:-3]
        if name not in self._group:
            prefixes = tuple(f"sqlglot.{d}.{name}:" for d in DIALECT_DIRS)
            m = 0
            for k, v in self.classes.items():
                if k.startswith(prefixes):
                    m |= v
            self._group[name] = m
        return self._group[name]

    def inst_mask(self, rel, cls_path):
        """Tests that instantiated this class (or an enclosing one, or its dialect family)."""
        mod = module_of(rel)
        m = 0
        for i in range(1, len(cls_path) + 1):
            m |= self.classes.get(f"{mod}:{'.'.join(cls_path[:i])}", 0)
        return m | self.group_mask(rel)

    def built_at_import(self, rel, cls_path):
        mod = module_of(rel)
        if mod in CORE_MODULES:
            return True
        imp = self.d["imp_classes"]
        for i in range(1, len(cls_path) + 1):
            if f"{mod}:{'.'.join(cls_path[:i])}" in imp:
                return True
        parts = rel.split("/")
        if len(parts) == 3 and parts[1] in DIALECT_DIRS:
            prefixes = tuple(f"sqlglot.{d}.{parts[2][:-3]}:" for d in DIALECT_DIRS)
            return any(k.startswith(prefixes) for k in imp)
        return False

    def fallback(self, rel, cls_path=()):
        """catcher: everything that runs this file or creates its classes."""
        self.import_time = True
        return self.file_mask(rel) | self.inst_mask(rel, cls_path)

    # ---------------------------------------------------------------- import-time names
    def resolve_name(self, name, own, visited, depth):
        """(mask, unknown): tests running code that reads name, followed through import-time
        derivations (class attributes built from it, default arguments, decorators)."""
        m = self.d["name_mask"].get(name, 0)
        unknown = False
        uses = self.d["uses"].get(name, ())
        if len(uses) > 600:
            return m, True
        for rel, ln, info in uses:
            if rel == own[0] and own[1] <= ln <= own[2]:
                continue
            if info[0] == "f":
                key = (rel, info[1])
                if key not in visited:
                    visited.add(key)
                    m |= self.span_mask(rel, info[1], info[2])
                continue
            if info[0] != "s":
                unknown = True
                continue
            _, cls_path, a, b, names, doc = info
            key = (rel, a)
            if key in visited:
                continue
            visited.add(key)
            if depth >= 5:
                unknown = True
                continue
            m |= self.resolve_stmt(rel, cls_path, names, (a, b), doc, visited, depth + 1)
        return m, unknown

    def resolve_stmt(self, rel, cls_path, names, span, doc, visited, depth=0):
        """Tests affected by a changed class-body or module-level statement."""
        self.import_time = True
        reexec = self.span_mask(rel, span[0], span[1]) if span else 0
        if doc:
            return reexec | self.d["name_mask"].get("__doc__", 0)
        if not names:
            return reexec | self.fallback(rel, cls_path)
        inst = self.inst_mask(rel, cls_path) if cls_path else 0
        built = self.built_at_import(rel, cls_path) if cls_path else True
        own = (rel, span[0], span[1]) if span else (rel, 0, 0)
        res = reexec
        for name in names:
            s1, unknown = self.resolve_name(name, own, visited, depth)
            if name in self.d["name_imp_cls"] or (name in self.d["name_imp"] and built):
                unknown = True   # read while importing: class creation, helpers, early instances
            if unknown or not s1:
                res |= inst if inst else (s1 | self.fallback(rel, cls_path))
            else:
                res |= (s1 & inst) if inst else s1
        return res

    # ---------------------------------------------------------------- one python file
    def func_mask(self, rel, site, visited):
        fn = site.fn
        a, b = site.span
        m = self.span_mask(rel, a, b)
        if isinstance(fn, ast.Lambda):
            return m
        imp = self.imp.get(rel, ())
        if imp and any(ln in imp for ln in range(fn.body[0].lineno, fn.end_lineno + 1)):
            # this function runs while modules import: follow everything it feeds
            self.import_time = True
            got, unknown = self.resolve_name(fn.name, (rel, start_of(fn), fn.end_lineno), visited, 0)
            if unknown or not got or fn.name in self.d["name_imp"]:
                got |= self.fallback(rel, site.cls_path)
            m |= got
        if site.span[0] < fn.body[0].lineno:
            # signature or decorator: evaluated at import, seen by every caller
            self.import_time = True
            m |= self.span_mask(rel, start_of(fn), fn.end_lineno)
        return m

    def new_member(self, rel, name, cls_path, visited):
        """A def or class attribute that did not exist before (e.g. an override)."""
        self.import_time = True
        s1, unknown = self.resolve_name(name, (rel, 0, 0), visited, 0)
        unknown = unknown or name in self.d["name_imp_cls"]
        inst = self.inst_mask(rel, cls_path) if cls_path else 0
        if s1 and inst and not unknown:
            return s1 & inst
        return s1 | (inst if inst else self.fallback(rel, cls_path))

    def analyze_py(self, rel, fp):
        """Mask for a modified .py file, or ALL when it cannot be reasoned about."""
        old = self.source(rel)
        if old is None:
            log(rel, "no source: catcher takes all")
            return self.ALL
        new = apply_hunks(old, fp.hunks)
        if new is None:
            log(rel, "patch does not apply: catcher takes all")
            return self.ALL
        ntree, problem = compile_check(new.encode("utf-8", "surrogateescape"), rel)
        if problem:
            log(rel, problem)
            return self.broken_file(rel)
        otree = self.tree(rel)
        if otree is None:
            return self.ALL
        changes = line_map(fp.hunks)
        self._nchanged, self._fresh_memo = changes[1], {}
        try:
            # comments, layout, quoting, literal spelling, parentheses: Python compiles the same
            # program (only line numbers move, which no test observes), nothing runs differently
            if same_ast(otree, ntree, changes):
                return 0
            # docstrings, postponed annotations, TYPE_CHECKING-only code: what changes is data
            # nothing in sqlglot reads (__doc__, __annotations__), so only code reading it can tell
            (of, otc), (nf, ntc) = runtime_facts(otree), runtime_facts(ntree)
            if same_ast(otree, ntree, changes, loose=(of and nf, otc & ntc)):
                nm = self.d["name_mask"]
                return nm.get("__doc__", 0) | nm.get("__annotations__", 0) | nm.get("get_type_hints", 0) | \
                    nm.get("get_annotations", 0)
        except RecursionError:
            pass
        visited = set()
        m = 0
        before, self.import_time = self.import_time, False
        self._kd_memo = {}
        for kind, ol, nl, ot, nt in edits(fp.hunks):
            if kind == "replace":
                if ot.rstrip() == nt.rstrip():
                    if not self._in_string(otree, ol, True):
                        continue
                    m |= self._site_mask(rel, locate(otree, ol, *line_cols(ot)), visited, otree, ntree)
                    continue
                if self._comment_change(ot, nt, otree, ol):
                    continue
                cols = eq_swap_cols(ot, nt)
                if cols is not None:
                    got = self.eq_swap_mask(rel, otree, ol, cols)
                    if got is not None:
                        m |= got
                        continue
                c0, c1 = changed_cols(ot, nt)
                site = locate(otree, ol, c0, c1)
                m |= self._site_mask(rel, site, visited, otree, ntree)
            elif kind == "remove":
                if not ot.strip() or (ot.strip().startswith("#") and not self._in_string(otree, ol)):
                    continue
                c0, c1 = line_cols(ot)
                m |= self._site_mask(rel, locate(otree, ol, c0, c1, precise=False), visited, otree, ntree)
            else:
                if not nt.strip() or (nt.strip().startswith("#") and not self._in_string(ntree, nl)):
                    continue
                m |= self._added(rel, otree, ntree, ol, nl, nt, visited)
        if self.import_time and rel.startswith("sqlglot/"):
            self.need_probe = True
        if self.import_time and rel.startswith("tests/"):
            # module-level code of a test file runs at collection: its own tests and its importers
            m |= self.file_tests.get(rel, 0) | self.importers_of_test_module(rel)
        self.import_time = self.import_time or before
        return m

    def broken_file(self, rel):
        """A file that will not compile (or whose bytes mean something else to Python)."""
        if rel.startswith("tests/") and os.path.basename(rel) not in ("conftest.py", "__init__.py"):
            # only that test module (and whatever imports it) fails to collect
            return self.file_tests.get(rel, 0) | self.importers_of_test_module(rel)
        if os.path.basename(rel) == "conftest.py" or rel.endswith("/__init__.py") and rel.startswith("tests/"):
            return self.scope_tests(os.path.dirname(rel))
        return self.ALL

    def _site_mask(self, rel, site, visited, otree=None, ntree=None):
        if site.kind == "func":
            return self.func_mask(rel, site, visited)
        if site.kind == "stmt":
            st = site.stmt
            if star_import(st):
                return self.star_import_changed(rel)
            if ntree is not None:
                got = self.dict_entry_mask(rel, site, otree, ntree)
                if got is not None:
                    return got
            before = self.import_time
            got = self.resolve_stmt(rel, site.cls_path, assigned_names(st), site.span, is_docstring(st), visited)
            if ntree is not None and self.quiet_constant_edit(rel, site.cls_path, st, otree, ntree):
                self.import_time = before   # importing behaves exactly as before: nothing to probe
            return got
        return 0

    SAFE_BASE_SUFFIXES = ("Dialect", "Parser", "Generator", "Tokenizer")

    def quiet_constant_edit(self, rel, cls_path, st, otree, ntree):
        """`NAME = <constant>` -> `NAME = <constant>` in a sqlglot module or class body, where nothing
        that runs while modules import reads NAME (no import-time function line mentions it, and
        every import-time statement mentioning it only binds it). Building a constant cannot raise,
        and the class-creation hooks sqlglot has (_Dialect, Expr and Tokenizer __init_subclass__)
        read attributes by name, so the import does exactly what it did; only run-time readers,
        which resolve_stmt selects, can tell. Classes on the path must have no decorators or
        metaclass keywords and only Dialect/Parser/Generator/Tokenizer-family bases (Enum,
        NamedTuple, TypedDict or dataclass bodies are processed generically at class creation)."""
        if not rel.startswith("sqlglot/"):
            return False
        if isinstance(st, ast.Assign) and len(st.targets) == 1:
            target = st.targets[0]
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            target = st.target
        else:
            return False
        if not isinstance(target, ast.Name):
            return False
        nst = _body_stmt(ntree, cls_path, _dump(target))
        if nst is None or type(nst) is not type(st) or nst.value is None or \
                _body_stmt(otree, cls_path, _dump(target)) is not st:
            return False
        if isinstance(st, ast.AnnAssign) and _dump(st.annotation) != _dump(nst.annotation):
            return False
        if not (self.import_safe(rel, st.value, None) and self.import_safe(rel, nst.value, st.value)):
            return False
        if cls_path and not (self._plain_class_chain(otree, cls_path) and self._plain_class_chain(ntree, cls_path)):
            return False
        name = target.id
        if name in self.d["name_imp"] or name in self.d["name_imp_cls"]:
            return False
        return all(self._pure_binding(urel, ln, name) for urel, ln, _ in self.d["uses"].get(name, ()))

    def _plain_class_chain(self, tree, cls_path):
        node = tree
        for name in cls_path:
            cls = next((c for c in node.body if isinstance(c, ast.ClassDef) and c.name == name), None)
            if cls is None or cls.decorator_list or cls.keywords:
                return False
            for b in cls.bases:
                last = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else None
                if last is None or not last.endswith(self.SAFE_BASE_SUFFIXES):
                    return False
            node = cls
        return True

    def _pure_binding(self, urel, line, name):
        """The module- or class-level statement of urel holding this line only binds name (a
        simple assignment whose value does not mention it)."""
        node = self.tree(urel)
        st = None
        while node is not None:
            nxt = None
            for s in node.body:
                if s.lineno <= line <= s.end_lineno:
                    if isinstance(s, ast.ClassDef) and s.body and s.body[0].lineno <= line:
                        nxt = s
                    else:
                        st = s
                    break
            node = nxt
        if isinstance(st, ast.Assign) and len(st.targets) == 1:
            target, value = st.targets[0], st.value
        elif isinstance(st, ast.AnnAssign):
            target, value = st.target, st.value
        else:
            return False
        if not isinstance(target, ast.Name) or target.id != name:
            return False
        return value is None or not any((isinstance(n, ast.Name) and n.id == name) or
                                        (isinstance(n, ast.Attribute) and n.attr == name) for n in ast.walk(value))

    def star_import_changed(self, rel):
        """`from X import *` re-exports an open set of names (sqlglot.expressions is built this
        way); adding, removing or changing one can drop or shadow names that are only looked up at
        run time (`exp.Properties`), which neither coverage nor the import probe can see."""
        log(rel, "star import changed: catcher takes all")
        self.import_time = True
        return self.ALL

    def _added(self, rel, otree, ntree, ol, nl, nt, visited):
        c0, c1 = line_cols(nt)
        site = locate(ntree, nl, c0, c1, precise=False)
        if site.kind == "func" and site.func_path and not site.cls_path and \
                self.unread_new_function(rel, ntree, site.func_path[0]):
            return 0
        if site.kind == "func" and not isinstance(site.fn, ast.Lambda):
            ofn = find_def(otree, site.func_path)
            if ofn is not None and isinstance(ofn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                osite = Site("func", ofn, (start_of(ofn), ofn.end_lineno), site.cls_path, func_path=site.func_path)
                return self.func_mask(rel, osite, visited)
            return self.new_member(rel, site.fn.name, site.cls_path, visited)
        if site.kind == "func":
            # inside a lambda: judge by the old code around the insertion point
            m = 0
            for a in (ol - 1, ol):
                if a >= 1:
                    s = locate(otree, a, 0, 10 ** 9, precise=False)
                    m |= self._site_mask(rel, s, visited)
            return m
        if site.kind == "stmt":
            st = site.stmt
            if star_import(st):
                return self.star_import_changed(rel)
            if not site.cls_path and self.unread_cached_import(rel, ntree, st):
                return 0
            names = assigned_names(st)
            span = (max(1, ol - 1), ol)
            if isinstance(st, DEFS) and find_def(otree, site.func_path) is None:
                return self.new_member(rel, st.name, site.cls_path[:-1] if isinstance(st, ast.ClassDef) else site.cls_path, visited)
            return self.resolve_stmt(rel, site.cls_path, names, span, is_docstring(st), visited)
        return 0

    def unread_new_function(self, rel, ntree, name):
        """A module-level def the patch adds whole, under a name nothing can look up: defining it
        cannot fail (no decorators, import-safe defaults, postponed or no annotations) and no code
        reaches it. sqlglot's namespace scans (helper.subclasses, Dialect lookup, *_sql dispatch)
        only collect classes and methods, and a fresh name shadows nothing, also not through star
        imports. Names collectors give meaning to (dunders, test*, pytest/unittest module hooks)
        are excluded."""
        key = (rel, name)
        if key in self._fresh_memo:
            return self._fresh_memo[key]
        ok = False
        fn = next((st for st in ntree.body if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                   st.name == name), None)
        if fn is not None and not fn.decorator_list and self.fresh_name(rel, name):
            nchanged = self._nchanged
            whole = all(ln in nchanged for ln in range(start_of(fn), fn.end_lineno + 1))
            a = fn.args
            safe_defaults = all(self.import_safe(rel, d, None) for d in a.defaults + [d for d in a.kw_defaults if d])
            annotated = fn.returns is not None or any(x.annotation is not None for x in
                                                      a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x)
            ok = whole and safe_defaults and (not annotated or runtime_facts(ntree)[0]) and \
                not getattr(fn, "type_params", None)
        self._fresh_memo[key] = ok
        return ok

    MODULE_HOOKS = {"setUpModule", "tearDownModule", "setup_module", "teardown_module", "setup_function",
                    "teardown_function", "setup", "teardown", "load_tests"}
    # loaded by every interpreter that runs the suite (pytest, the subprocess test, spawned pool
    # workers) before any test module: CPython's start-up with site enabled
    STARTUP_MODULES = {"os", "os.path", "sys", "abc", "codecs", "io", "stat", "time", "errno", "builtins",
                       "marshal", "genericpath", "posixpath", "zipimport", "site"}

    def unread_cached_import(self, rel, ntree, st):
        """`import os as _x` added at module level: importing a module that is already loaded runs
        no code (a sys.modules hit), and a fresh name binds nothing anyone reads."""
        if not isinstance(st, ast.Import) or not any(st is s for s in ntree.body):
            return False
        if not all(ln in self._nchanged for ln in range(st.lineno, st.end_lineno + 1)):
            return False
        for a in st.names:
            bound = a.asname or a.name.split(".")[0]
            if a.name not in self.STARTUP_MODULES or not self.fresh_name(rel, bound):
                return False
        return True

    def fresh_name(self, rel, name):
        import builtins
        if name.startswith(("__", "test", "pytest")) or name in self.MODULE_HOOKS or hasattr(builtins, name):
            return False
        d = self.d
        if name in d["name_mask"] or name in d["uses"] or name in d["name_imp"] or name in d["name_imp_cls"]:
            return False
        return re.search(r"\b%s\b" % re.escape(name), self.source(rel) or name) is None

    @staticmethod
    def _in_string(tree, line, whole=False):
        """The line lies inside a multi-line string (whole=True: anywhere in one, ends included)."""
        for node in chain_at(tree, line):
            if isinstance(node, (ast.Constant, ast.JoinedStr)) and node.lineno != node.end_lineno:
                if node.lineno < line <= node.end_lineno or (whole and node.lineno <= line <= node.end_lineno):
                    return True
        return False

    def _comment_change(self, ot, nt, otree, ol):
        """Only a trailing comment was added, removed or edited."""
        a, b = ot.rstrip(), nt.rstrip()
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        code = short.split("#")[0].rstrip() if "#" in short and "'" not in short and '"' not in short else short
        if not long_.startswith(code):
            return False
        rest_long = long_[len(code):].strip()
        rest_short = short[len(code):].strip()
        return (not rest_long or rest_long.startswith("#")) and (not rest_short or rest_short.startswith("#")) \
            and not self._in_string(otree, ol, True)

    # ---------------------------------------------------------------- runtime-profile shortcuts
    def eq_swap_mask(self, rel, otree, line, cols):
        """Tests that can tell an `is`<->`==` swap at this comparison apart, or None when the
        profile has no evidence for this exact comparison."""
        if not self.cmp:
            return None
        node = None
        for n in chain_at(otree, line, cols[0], cols[1]):
            if isinstance(n, ast.Compare):
                node = n
        if node is None or len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)):
            return None
        if node.lineno != line or node.end_lineno != line:
            return None
        rec = self.cmp.get((rel, node.lineno, node.col_offset))
        if rec is None:
            return None
        negated = isinstance(node.ops[0], (ast.IsNot, ast.NotEq))
        if not all(self._swap_preserves(f, negated) for f in rec["imp"]):
            return self.ALL   # an evaluation while modules import would change
        seen = bad = 0
        for fact, mask in rec["facts"].items():
            seen |= mask
            if not self._swap_preserves(fact, negated):
                bad |= mask
        ran = self.span_mask(rel, line, line)
        # kept: tests with an operand fact the swap changes, tests that ran the line but were
        # never seen evaluating it, and tests whose instrumented run took a different path
        return bad | (ran & ~seen) | (ran & self.unobserved)

    @staticmethod
    def _swap_preserves(fact, negated):
        """Decided here, per edit, from pristine operand facts (identical, eq, ne_ok): swapping
        `is`<->`==` (negated: `is not`<->`!=`) leaves this evaluation unchanged only when the
        equality involved runs side-effect-free builtin code and agrees with identity."""
        identical, eq, ne_ok = fact
        if eq is None or eq != identical:
            return False
        return ne_ok if negated else True

    def dict_entry_mask(self, rel, site, otree, ntree):
        """Per-entry coverage for a literal dictionary whose entries only changed value.

        Applies when the old and new statements assign the same target, the dictionaries have the
        same keys and spreads in the same order, every changed value is an expression whose
        construction has no side effects, and the profile vouches that each changed entry's value
        is only ever read by the tests it lists (through aliases, subclasses and dictionaries
        derived from it). Returns None to fall back to the class-attribute logic."""
        st = site.stmt
        if not self.keys or not isinstance(st, (ast.Assign, ast.AnnAssign)) or not isinstance(st.value, ast.Dict):
            return None
        key = (rel, st.lineno)
        if key in self._kd_memo:
            got = self._kd_memo[key]
            return 0 if got is not None else None   # already counted once for this statement
        got = self._dict_entry_mask(rel, site, st, ntree)
        self._kd_memo[key] = got
        return got

    def _dict_entry_mask(self, rel, site, st, ntree):
        target = st.targets[0] if isinstance(st, ast.Assign) else st.target
        if isinstance(st, ast.Assign) and len(st.targets) != 1:
            return None
        nst = _body_stmt(ntree, site.cls_path, _dump(target))
        if nst is None or type(nst) is not type(st) or not isinstance(nst.value, ast.Dict):
            return None
        if isinstance(st, ast.AnnAssign) and _dump(st.annotation) != _dump(nst.annotation):
            return None
        od, nd = st.value, nst.value
        if len(od.keys) != len(nd.keys):
            return None
        owner = self.keys.get((rel, od.lineno, od.col_offset))
        if owner is None or owner["n"] != len(od.keys):
            return None
        changed = []
        for i, (ok, nk, ov, nv) in enumerate(zip(od.keys, nd.keys, od.values, nd.values)):
            if _dump(ok) != _dump(nk):
                return None
            if owner["keysrc"][i] != _dump(ok):
                return None   # the profile describes a different dictionary
            if _dump(ov) != _dump(nv):
                if ok is None:
                    return None   # a spread changed
                changed.append(i)
        if not changed:
            return None
        mask = 0
        quiet = rel.startswith("sqlglot/")
        for i in changed:
            if not (self.safe_value(rel, od.values[i]) and self.safe_value(rel, nd.values[i])):
                return None
            em = owner["mask"][i]
            if em is None:
                return None   # entry not trusted (read at import time, dynamic, unknown key)
            mask |= em
            quiet = quiet and self.import_safe(rel, nd.values[i], od.values[i])
        if not quiet:
            # the new value is evaluated when the class body runs and could raise (a name or
            # attribute that does not exist): probe the import (analyze() then decides the
            # fresh-interpreter test from the probe, as for any import-time edit)
            self.import_time = True
        # else: building the new value cannot raise, and a trusted entry's value is read by nothing
        # while modules import, so importing behaves exactly as before: no probe, and
        # test_lazy_load (which only imports sqlglot) cannot notice the edit
        return mask | self.unobserved

    def import_safe(self, rel, node, old, depth=0):
        """Evaluating node (a dictionary value in a class or module body) cannot raise and runs no
        code beyond building it: constants and containers of them, unary signs on numbers, lambdas
        whose defaults are such, and a closure-factory call with constant arguments to the very
        name the old value called (bound at that point of the import, as the pristine import shows)."""
        if depth > 12:
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(not isinstance(e, ast.Starred) and self.import_safe(rel, e, None, depth + 1) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(k is not None and isinstance(k, ast.Constant) and self.import_safe(rel, v, None, depth + 1)
                       for k, v in zip(node.keys, node.values))
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.op, (ast.USub, ast.UAdd)) and isinstance(node.operand, ast.Constant) and \
                type(node.operand.value) in (int, float, complex)
        if isinstance(node, ast.Lambda):
            a = node.args
            return all(self.import_safe(rel, d, None, depth + 1)
                       for d in a.defaults + [d for d in a.kw_defaults if d is not None])
        if isinstance(node, ast.Call):
            return isinstance(node.func, ast.Name) and isinstance(old, ast.Call) and \
                isinstance(old.func, ast.Name) and old.func.id == node.func.id and \
                all(self.import_safe(rel, x, None, depth + 1) for x in node.args) and \
                all(k.arg is not None and self.import_safe(rel, k.value, None, depth + 1) for k in node.keywords) and \
                self.closure_factory_call(rel, node, depth)
        return False

    def safe_value(self, rel, node, depth=0):
        """Building this value cannot run code with side effects (beyond failing outright, which
        the import probe catches)."""
        if depth > 12:
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.Attribute):
            return self.safe_value(rel, node.value, depth + 1) and isinstance(node.value, (ast.Name, ast.Attribute))
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(self.safe_value(rel, e, depth + 1) for e in node.elts) and not any(isinstance(e, ast.Starred) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(k is not None and self.safe_value(rel, k, depth + 1) for k in node.keys) and \
                all(self.safe_value(rel, v, depth + 1) for v in node.values)
        if isinstance(node, ast.UnaryOp):
            return isinstance(node.operand, ast.Constant)
        if isinstance(node, ast.BinOp):
            return isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)
        if isinstance(node, ast.Lambda):
            a = node.args
            return all(self.safe_value(rel, d, depth + 1) for d in a.defaults + [d for d in a.kw_defaults if d is not None])
        if isinstance(node, ast.Call):
            return self.closure_factory_call(rel, node, depth)
        return False

    def closure_factory_call(self, rel, call, depth):
        """A call to a sqlglot function that only builds and returns a nested function, with
        arguments that bind to its signature, cannot have side effects."""
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            return False
        if not all(self.safe_value(rel, a, depth + 1) for a in call.args) or \
                not all(self.safe_value(rel, k.value, depth + 1) for k in call.keywords):
            return False
        if not isinstance(call.func, ast.Name):
            return False
        fn = self._resolve_function(rel, call.func.id)
        if fn is None or fn.decorator_list:
            return False
        body = fn.body[1:] if fn.body and is_docstring(fn.body[0]) else fn.body
        if not body or not isinstance(body[-1], ast.Return) or not isinstance(body[-1].value, (ast.Name, ast.Lambda)):
            return False
        for s in body[:-1]:
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) or s.decorator_list:
                return False
            if any(not isinstance(d, ast.Constant) for d in s.args.defaults + [d for d in s.args.kw_defaults if d]):
                return False
        if isinstance(body[-1].value, ast.Name) and body[-1].value.id not in {s.name for s in body[:-1]}:
            return False
        return self._binds(fn.args, call)

    @staticmethod
    def _binds(a, call):
        pos = a.posonlyargs + a.args
        if len(call.args) > len(pos) and a.vararg is None:
            return False
        given = {p.arg for p in pos[:len(call.args)]}
        names = {p.arg for p in a.args + a.kwonlyargs}
        for k in call.keywords:
            if k.arg in given or (k.arg not in names and a.kwarg is None):
                return False
            given.add(k.arg)
        required = [p.arg for p in pos[:len(pos) - len(a.defaults)]]
        required += [p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
        return all(r in given for r in required)

    def _resolve_function(self, rel, name, depth=0):
        """The module-level def a name refers to in rel (directly or via `from sqlglot.x import`)."""
        tree = self.tree(rel)
        if tree is None or depth > 3:
            return None
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)) and st.name == name:
                return st
        for st in tree.body:
            if isinstance(st, ast.ImportFrom) and st.level == 0 and (st.module or "").startswith("sqlglot"):
                for al in st.names:
                    if (al.asname or al.name) == name:
                        base = st.module.replace(".", "/")
                        for cand in (base + ".py", base + "/__init__.py"):
                            if (SRC / cand).exists():
                                return self._resolve_function(cand, al.name, depth + 1)
        return None

    # ---------------------------------------------------------------- other files
    def analyze_other(self, rel):
        m = self.d["opens"].get(rel, 0)
        if rel in self.d["imp_opens"]:
            return self.ALL
        if not m and rel.startswith("tests/"):
            m = self.tests_mentioning(os.path.splitext(os.path.basename(rel))[0]) or self.ALL
        return m

    def tests_mentioning(self, word):
        if word not in self._mention:
            m = 0
            for f in self.test_files:
                if word in (self.source(f) or ""):
                    m |= self.file_tests[f]
            self._mention[word] = m
        return self._mention[word]

    def importers_of_test_module(self, rel):
        return self.tests_mentioning(module_of(rel).rsplit(".", 1)[-1])

    # ---------------------------------------------------------------- pytest collection
    def collection_scope(self, rel):
        """The directory whose tests a conftest.py / package __init__.py at rel governs, else None.
        `pytest tests` loads conftest.py from the repository root and from tests/ downwards."""
        d, base = os.path.dirname(rel), os.path.basename(rel)
        in_tests = d == "tests" or d.startswith("tests/")
        if base == "conftest.py" and (d == "" or in_tests):
            return d
        if base == "__init__.py" and in_tests:
            return d
        return None

    def collection_source_unchanged(self, fp, rel):
        """Check that a collection file still compiles and has the same positioned AST."""
        old = self.source(rel)
        if old is None or not fp.hunks:
            return False
        new = apply_hunks(old, fp.hunks)
        if new is None:
            return False
        ntree, problem = compile_check(new.encode("utf-8", "surrogateescape"), rel)
        otree = self.tree(rel)
        return not problem and otree is not None and ast.dump(otree, include_attributes=True) == \
            ast.dump(ntree, include_attributes=True)

    def fixture_only_conftest(self, rel):
        """A test-package conftest that only registers ordinary opt-in fixtures."""
        source = self.probe_files.get(rel)
        if source is None:
            return False
        tree, problem = compile_check(source.encode("utf-8", "surrogateescape"), rel)
        if problem:
            return False
        # Existing empty package initializers ensure pytest importing the conftest neither imports
        # a new package nor prepends a different root to sys.path.
        directory = os.path.dirname(rel)
        while directory:
            init = self.source(directory + "/__init__.py")
            if init is None or init.strip():
                return False
            directory = os.path.dirname(directory)
        names = set()
        body = tree.body[1:] if ast.get_docstring(tree) is not None else tree.body
        if body:
            first, *body = body
            if not isinstance(first, ast.Import) or [(n.name, n.asname) for n in first.names] != [("pytest", None)]:
                return False
        for stmt in body:
            if not isinstance(stmt, ast.FunctionDef) or stmt.name.startswith(("_", "pytest")) or stmt.name in names:
                return False
            args = stmt.args
            if args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg or stmt.returns or stmt.type_params:
                return False
            if len(stmt.decorator_list) != 1:
                return False
            decorator = stmt.decorator_list[0]
            if isinstance(decorator, ast.Call):
                if decorator.args or any(k.arg != "autouse" or not isinstance(k.value, ast.Constant) or
                                         k.value.value is not False for k in decorator.keywords):
                    return False
                decorator = decorator.func
            if not isinstance(decorator, ast.Attribute) or decorator.attr != "fixture" or \
                    not isinstance(decorator.value, ast.Name) or decorator.value.id != "pytest":
                return False
            if stmt.name in PYTEST_FIXTURES:   # pytest and its plugins request these themselves
                return False
            names.add(stmt.name)

        # The pinned tests use unittest. Require evidence from all files, including helpers,
        # that none integrates with pytest's fixture or plugin manager, or imports conftest.
        # A mention may be harmless; it still disqualifies this shortcut.
        if any(self.source(p) is None for p in self.test_files):
            return False
        for path in (SRC / "tests").rglob("*.py"):
            text = self.source(path.relative_to(SRC).as_posix())
            if text is None or any(word in text for word in ("pytest", "conftest", "getfixturevalue", *names)):
                return False
        return True

    def analyze_root_file(self, fp, path):
        """A file outside sqlglot/ and tests/."""
        parts = path.split("/")
        name = None
        if len(parts) == 1 and path.endswith(".py"):
            name = path[:-3]
        elif len(parts) == 2 and parts[1] == "__init__.py":
            name = parts[0]
        if name and (name in sys.stdlib_module_names or name in SHADOW_EXTRA):
            return self.ALL   # shadows a module every test imports (the root is on sys.path)
        return self.analyze_other(path)

    def collection_file_readers(self, rel):
        return self.ALL if rel in self.d["imp_opens"] else self.d["opens"].get(rel, 0)

    @staticmethod
    def pytest_config_path(path):
        directory, base = os.path.dirname(path), os.path.basename(path)
        return base in PYTEST_CONFIG and (directory in ("", "tests") or directory.startswith("tests/"))

    def pyproject_pytest_unchanged(self, fp):
        """The root pyproject.toml still parses (pytest refuses to start otherwise) and its
        tool.pytest table (ini_options, or pytest 9's native table) is unchanged in type and value:
        nothing else in the file reaches a test run that imports sqlglot from the checkout."""
        try:
            import tomllib
        except ImportError:
            return False
        old = self.source("pyproject.toml")
        new = apply_hunks(old, fp.hunks) if old is not None else None
        if new is None:
            return False
        try:
            o, n = tomllib.loads(old), tomllib.loads(new)
        except ValueError:
            return False
        return pickle.dumps(o.get("tool", {}).get("pytest")) == pickle.dumps(n.get("tool", {}).get("pytest"))

    def pytest_config_unchanged(self, fp, rel):
        """Compare same-path config edits, preserving anything pytest could interpret differently."""
        old = self.source(rel)
        if old is None or not fp.hunks:
            return False
        new = apply_hunks(old, fp.hunks)
        if new is None:
            return False
        try:
            old.encode("utf-8")
            new.encode("utf-8")
            if rel.endswith(".toml"):
                import tomllib
                # Compare typed values. Python equality alone would equate true/1 and 0.0/-0.0.
                return pickle.dumps(tomllib.loads(old)) == pickle.dumps(tomllib.loads(new))
        except (ImportError, ValueError):
            return False

        # iniconfig ignores whole comment lines. Preserve actual blank lines, which other
        # consumers of setup.cfg or tox.ini may preserve inside multiline values.
        def entries(text):
            return [ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", ";"))]

        return entries(old) == entries(new)

    # ---------------------------------------------------------------- one patch
    def analyze(self, patch_text):
        """(mask, probe): the selected tests, and the combined patch to import-check (or None)."""
        self.import_time = False
        self.need_probe = False
        self.probe_files = {}
        files = parse_patch(patch_text)
        self.probe_files = self.combined(files)
        # the verifier may run tests on this patch only when it just edits existing sqlglot modules
        self.verify_files = None
        if files and all(fp.old and fp.old == fp.new and fp.old.startswith("sqlglot/") and fp.old.endswith(".py")
                         and self.probe_files.get(fp.old) is not None for fp in files):
            self.verify_files = {fp.old: self.probe_files[fp.old] for fp in files}
        m = 0
        for fp in files:
            rel = fp.old or fp.new
            if rel is None:
                continue
            try:
                m |= self.analyze_file(fp, isolated=len(files) == 1)
            except Exception as e:   # catcher: never crash, widen instead
                log(rel, "analysis error, catcher takes all:", repr(e))
                m |= self.ALL
            if m == self.ALL:
                break
        probe = None
        if self.need_probe and m != self.ALL:
            probe = {rel: text for rel, text in self.probe_files.items()
                     if rel.endswith(".py") and rel.startswith(("sqlglot/", "tests/"))}
        # Tests that start their own interpreter (test_lazy_load: `import sqlglot` must create
        # exactly one dialect class) are invisible to coverage, so any import-time edit used to
        # select them. The probe replays that very import in a fresh interpreter with the patch and
        # checks the assertion; such a test now waits for the probe's verdict (lazy_pending) and is
        # added here only when no probe will run or the test is not one the probe can replay.
        self.lazy_pending = False
        if self.import_time:
            sub = self.d["subproc"] & ~m
            if sub and probe is not None and sub == self.lazy_mask:
                self.lazy_pending = True
            else:
                m |= sub
        return m, probe

    def combined(self, files):
        """rel -> patched text (None: deleted) for every .py file the patch touches, so the
        import probe sees the whole patch, not just the file that triggered it."""
        out = {}
        for fp in files:
            if fp.old and fp.old != fp.new and fp.old.endswith(".py"):
                out[fp.old] = None
        for fp in files:
            if not fp.new or not fp.new.endswith(".py"):
                continue
            base = "" if fp.old is None else self.source(fp.old)
            text = apply_hunks(base, fp.hunks) if base is not None else None
            if text is not None:
                out[fp.new] = text
        return out

    def analyze_file(self, fp, isolated=False):
        old, new = fp.old, fp.new
        rel = old or new
        paths = [p for p in (old, new) if p]
        # pytest configuration at the root or on the way down to tests/ decides rootdir and options
        if any(self.pytest_config_path(p) for p in paths):
            if isolated and old == new and self.pytest_config_unchanged(fp, rel):
                return self.collection_file_readers(rel)
            if old == new == "pyproject.toml" and self.pyproject_pytest_unchanged(fp):
                return self.collection_file_readers(rel)
            log(rel, "pytest configuration: catcher takes all")
            return self.ALL
        # pytest collection files govern every test below them
        scopes = {self.collection_scope(p) for p in paths} - {None}
        if scopes:
            if isolated and old == new and self.collection_source_unchanged(fp, rel):
                return self.collection_file_readers(rel)
            if isolated and old is None and rel.startswith("tests/") and os.path.basename(rel) == "conftest.py" and \
                    self.fixture_only_conftest(rel):
                return 0
            m = 0
            for s in scopes:
                m |= self.scope_tests(s)
            return m
        if not rel.startswith(("sqlglot/", "tests/")):
            m = 0
            for p in paths:
                m |= self.analyze_root_file(fp, p)
            return m
        if new is None or (old and old != new):
            if rel.startswith("sqlglot/"):
                log(rel, "deleted or renamed: catcher takes all")
                return self.ALL
            m = 0
            if old.endswith(".py"):
                # the old module's tests vanish and everything importing it fails
                m |= self.file_tests.get(old, 0) | self.importers_of_test_module(old)
                if new and new.endswith(".py"):
                    m |= self.new_test_file(new, fp)
                    if new in self.file_tests:
                        m |= self.file_tests[new]
            else:
                m |= self.analyze_other(old)
                if new:
                    m |= self.analyze_other(new)
            return m
        if old is None:
            if new.endswith(".py"):
                if new.startswith("tests/"):
                    return self.new_test_file(new, fp)
                # a new sqlglot module is only seen by discovery (entry points, package walks)
                self.import_time = True
                self.need_probe = True
                return self.fallback(os.path.dirname(new) + "/__init__.py") | self.d["subproc"]
            return self.analyze_other(new)
        if rel.endswith(".py"):
            return self.analyze_py(rel, fp)
        return self.analyze_other(rel)

    def new_test_file(self, rel, fp):
        """A test-tree .py file that did not exist before."""
        text = self.probe_files.get(rel)
        m = self.tests_mentioning(module_of(rel).rsplit(".", 1)[-1])
        if text is None:
            return self.scope_tests("tests")
        tree, problem = compile_check(text.encode("utf-8", "surrogateescape"), rel)
        if problem:
            return m   # it fails to collect on its own; its tests are not catalogued
        if TEST_FILE.search(rel):
            # collected in every worker: module-level code with side effects can reach any test
            for st in tree.body:
                if isinstance(st, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)) or is_docstring(st):
                    continue
                if isinstance(st, ast.ClassDef) and not st.decorator_list and not st.keywords and \
                        all(isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Pass)) or is_docstring(s) or
                            (isinstance(s, (ast.Assign, ast.AnnAssign)) and self.safe_value(rel, s.value or ast.Constant(None)))
                            for s in st.body):
                    continue
                if isinstance(st, (ast.Assign, ast.AnnAssign)) and st.value is not None and self.safe_value(rel, st.value):
                    continue
                return self.scope_tests("tests")
            for st in tree.body:
                mods = self.imported_test_modules(rel, st)
                if mods is None or not all(self.collected_anyway(x) for x in mods):
                    return self.scope_tests("tests")   # imports run other test modules' code
        return m

    def imported_test_modules(self, rel, st):
        """The tests.* modules a top-level statement of the new file `rel` imports, in any spelling:
        `import tests.a.b` (tests, tests.a, tests.a.b), `from tests.a import b` and the relative
        `from . import b` / `from .b import x` (the package, plus b wherever b is a submodule).
        [] for anything else; None when the statement cannot be resolved to modules."""
        if isinstance(st, ast.Import):
            out = []
            for alias in st.names:
                parts = alias.name.split(".")
                if parts[0] == "tests":
                    out += [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
            return out
        if not isinstance(st, ast.ImportFrom):
            return []
        if st.level:
            package = module_of(rel).split(".")[:-1]
            if st.level > len(package):
                return None   # beyond the top-level package
            base = package[:len(package) - st.level + 1]
            parts = base + (st.module.split(".") if st.module else [])
        else:
            parts = (st.module or "").split(".")
        if parts[0] != "tests":
            return []
        if any(alias.name == "*" for alias in st.names):
            return None
        module = ".".join(parts)
        out = [".".join(parts[:i]) for i in range(1, len(parts) + 1)]
        for alias in st.names:
            sub = module + "." + alias.name
            path = sub.replace(".", "/")
            if self.source(path + ".py") is not None or self.source(path + "/__init__.py") is not None:
                out.append(sub)
        return out

    def collected_anyway(self, module):
        """A tests.* module whose code pytest's collection runs whether or not a new file imports
        it: an existing test module (collected), a package initializer, or a helper that an existing
        test module imports at module level. Importing it again is a cache hit."""
        rel = module.replace(".", "/")
        for cand in (rel + ".py", rel + "/__init__.py"):
            if self.source(cand) is None:
                continue
            if cand in self.file_tests or cand.endswith("/__init__.py"):
                return True
            pat = re.compile(r"^(from %s import |import %s\b)" % (re.escape(module), re.escape(module)), re.M)
            return any(pat.search(self.source(f) or "") for f in self.test_files)
        return False


# ============================================================================ catcher: live import check

# Runs in a fresh interpreter (-I -S). Reads {"src", "files", "modules"} from stdin, serves the
# patched files from memory through a meta-path finder placed in front of the normal one (no
# overlay directory, no symlinks), imports every sqlglot module, and reports what failed.
# _probe is shared word for word with the probe server below, so both run the same steps.
# The first module is `sqlglot`, alone, as test_lazy_load's fresh interpreter imports it; when
# _probe imports it itself it also reports that test's assertion ("lazy": one dialect class).
# A probe the server serves starts after a pristine `import sqlglot` (its patch touches nothing
# that import loaded or looked for), so it reports nothing there and the caller knows it holds.
PROBE_LIB = r'''
import gc
gc.disable()   # importing sqlglot creates many long-lived objects: collecting them only costs CPU
import importlib, importlib.abc, importlib.util, json, os, sys
sys.dont_write_bytecode = True


def _lazy_check():
    # test_lazy_load runs `import sqlglot; assert len(sqlglot.Dialect._classes) == 1;
    # print('Success')` in a fresh interpreter and asserts "Success" in its captured stdout (decoded
    # as text). The count is checked here; 'Success' goes through this process's own print and
    # stdout, which the patch may have rebound or redirected, and the end of that output is marked
    # on fd 1 directly so the caller can apply the stdout assertion to exactly those bytes.
    try:
        if len(sys.modules["sqlglot"].Dialect._classes) != 1:
            return False
        print("Success")
        sys.stdout.flush()
        os.write(1, b"\n@@BR-LAZY-END@@\n")
    except BaseException:
        return False
    return True


def _probe(spec):
    src = spec["src"]
    if sys.path[:1] != [src]:
        sys.path.insert(0, src)
    patched = {}
    for rel, text in spec["files"].items():
        m = rel[:-3].replace("/", ".")
        pkg = m.endswith(".__init__")
        patched[m[:-9] if pkg else m] = (rel, pkg, text)

    class _Loader(importlib.abc.SourceLoader):
        def __init__(self, path, data):
            self.path, self.data = path, data

        def get_filename(self, fullname):
            return self.path

        def get_data(self, path):
            if path == self.path:
                return self.data
            with open(path, "rb") as f:
                return f.read()

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            hit = patched.get(name)
            if hit is None:
                return None
            rel, pkg, text = hit
            if text is None:
                raise ModuleNotFoundError("No module named %r (deleted by the patch)" % name, name=name)
            full = os.path.join(src, *rel.split("/"))
            s = importlib.util.spec_from_loader(name, _Loader(full, text.encode("utf-8", "surrogateescape")),
                                                origin=full, is_package=pkg)
            if pkg:
                s.submodule_search_locations = [os.path.dirname(full)]
            s.has_location = True
            return s

    sys.meta_path.insert(0, _Finder())
    failed = []
    lazy = None
    fresh = "sqlglot" not in sys.modules
    mods = spec["modules"]
    if "sqlglot" in mods:
        mods = ["sqlglot"] + [m for m in mods if m != "sqlglot"]
    for m in mods:
        try:
            importlib.import_module(m)
        except BaseException as e:
            failed.append([m, (type(e).__name__ + ": " + str(e))[:300]])
        if m == "sqlglot" and fresh:
            lazy = _lazy_check()
    unserved = [m for m, (rel, pkg, text) in patched.items()
                if text is not None and m in sys.modules and not isinstance(getattr(sys.modules[m], "__loader__", None), _Loader)]
    return "\n@@BR-PROBE@@" + json.dumps({"failed": failed, "unserved": unserved, "lazy": lazy}) + "\n"
'''

PROBE_CODE = PROBE_LIB + r'''
_spec = json.loads(sys.stdin.buffer.read().decode("utf-8", "surrogateescape"))
sys.stdout.write(_probe(_spec))
sys.stdout.flush()
'''

# The probe server: a chain of checkpoint processes. The first, started like a probe, imports
# the first module of the probe order (`sqlglot`) exactly as every probe does first. Each later
# checkpoint is a fork of the previous one that keeps importing, in the probe's own order, up to
# its boundary. Every checkpoint records every module loaded or looked for so far, then forks one
# child per request; a child continues with _probe from that point. A probe is sent to a checkpoint
# only when its patch touches none of those modules: a fresh probe would have reached the very same
# state after importing that prefix, so the verdict is the same. Anything else is not sent here.
# Protocol (JSON lines): config on stdin {"src", "stages", "fds"}; per checkpoint, requests
# {"id", "spec"} on its own pipe; replies {"ready", "pid", "loaded", "attempted"}, then
# {"id", "pid"} when a child starts and {"id", "out"} when it has exited.
SERVER_CODE = PROBE_LIB + r'''
import select


class _Seen(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.names = set()

    def find_spec(self, name, path=None, target=None):
        self.names.add(name)
        return None


def _emit(fd, obj):
    data = (json.dumps(obj) + "\n").encode("utf-8", "surrogateescape")
    while data:
        data = data[os.write(fd, data):]


_cfg = b""
while True:
    _chunk = os.read(0, 1 << 16)
    if not _chunk:
        break
    _cfg += _chunk
os.close(0)
_cfg = json.loads(_cfg.decode("utf-8", "surrogateescape"))
_fds = _cfg["fds"]
sys.path.insert(0, _cfg["src"])
_seen = _Seen()
sys.meta_path.insert(0, _seen)
_stage, _next_pid = 0, None
while True:
    _bad = []
    for _m in _cfg["stages"][_stage]:
        try:
            importlib.import_module(_m)
        except BaseException:
            _bad.append(_m)
    _IN, _OUT = _fds[_stage]
    _emit(_OUT, {"ready": not _bad, "pid": os.getpid(), "loaded": sorted(sys.modules),
                 "attempted": sorted(_seen.names)})
    if _bad:
        os._exit(1)
    if _stage + 1 < len(_fds):
        _pid = os.fork()
        if _pid == 0:
            os.close(_IN)
            os.close(_OUT)
            _stage += 1
            continue
        _next_pid = _pid
        for _a, _b in _fds[_stage + 1:]:
            os.close(_a)
            os.close(_b)
    break
sys.meta_path.remove(_seen)


def _child(req, rfd, wfd):
    # The child: own session (so a timeout kills its whole group), stdout to its own pipe,
    # then the same _probe as a fresh interpreter, ending with a normal interpreter exit so exit
    # hooks and non-daemon threads behave exactly as they do in a fresh probe.
    os.setsid()
    os.close(rfd)
    for fd in list(_children):
        os.close(fd)
    os.close(_IN)
    os.close(_OUT)
    os.dup2(wfd, 1)
    os.close(wfd)
    sys.stdout.write(_probe(req["spec"]))
    sys.stdout.flush()
    sys.exit(0)


_buf = b""
_children = {}     # read fd -> [id, pid, bytes]
_exited = []       # [id, pid, bytes] whose stdout closed, waiting for the process to exit
_open = True
while _open or _children or _exited:
    _wait = ([_IN] if _open else []) + list(_children)
    if _wait:
        _ready = select.select(_wait, [], [], 0.02 if _exited else None)[0]
    else:
        _ready = []
        select.select([], [], [], 0.02)
    for _fd in _ready:
        if _fd == _IN:
            _chunk = os.read(_IN, 1 << 16)
            if not _chunk:
                _open = False
                continue
            _buf += _chunk
            while b"\n" in _buf:
                _line, _buf = _buf.split(b"\n", 1)
                _req = json.loads(_line.decode("utf-8", "surrogateescape"))
                _r, _w = os.pipe()
                _pid = os.fork()
                if _pid == 0:
                    _child(_req, _r, _w)
                os.close(_w)
                _children[_r] = [_req["id"], _pid, b""]
                _emit(_OUT, {"id": _req["id"], "pid": _pid})
        else:
            _chunk = os.read(_fd, 1 << 16)
            if _chunk:
                _children[_fd][2] += _chunk
                continue
            os.close(_fd)
            _exited.append(_children.pop(_fd))
    for _job in list(_exited):
        try:
            _done = os.waitpid(_job[1], os.WNOHANG)[0] != 0
        except ChildProcessError:
            _done = True
        if _done:
            _exited.remove(_job)
            _emit(_OUT, {"id": _job[0], "out": _job[2].decode("utf-8", "replace")})
# a checkpoint gives no verdict of its own: wait for the next one (so its CPU is accounted to us)
# and leave without the interpreter's teardown
if _next_pid is not None:
    try:
        os.waitpid(_next_pid, 0)
    except ChildProcessError:
        pass
os._exit(0)
'''


# The verifier's prelude, run in a fresh interpreter (site-packages on: VERIFY_ENV lives there):
# reads a JSON spec from stdin ({"src", "files", ...}) and installs, for the whole run, the same
# in-memory finder for the patched files as _probe; unpatched modules load from src.
PATCHED_IMPORTS = r'''
import gc
gc.disable()   # importing sqlglot creates many long-lived objects: collecting them only costs CPU
import importlib, importlib.abc, importlib.util, json, os, sys
sys.dont_write_bytecode = True
spec = json.loads(sys.stdin.buffer.read().decode("utf-8", "surrogateescape"))
src = spec["src"]
sys.path.insert(0, src)
patched = {}
for rel, text in spec["files"].items():
    m = rel[:-3].replace("/", ".")
    pkg = m.endswith(".__init__")
    patched[m[:-9] if pkg else m] = (rel, pkg, text)

class _Loader(importlib.abc.SourceLoader):
    def __init__(self, path, data):
        self.path, self.data = path, data
    def get_filename(self, fullname):
        return self.path
    def get_data(self, path):
        if path == self.path:
            return self.data
        with open(path, "rb") as f:
            return f.read()

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        hit = patched.get(name)
        if hit is None:
            return None
        rel, pkg, text = hit
        if text is None:
            raise ModuleNotFoundError("No module named %r (deleted by the patch)" % name, name=name)
        full = os.path.join(src, *rel.split("/"))
        s = importlib.util.spec_from_loader(name, _Loader(full, text.encode("utf-8", "surrogateescape")),
                                            origin=full, is_package=pkg)
        if pkg:
            s.submodule_search_locations = [os.path.dirname(full)]
        s.has_location = True
        return s

sys.meta_path.insert(0, _Finder())
'''

# The verifier: runs catalogue tests ({"tests": [[id, limit_s], ...]}, catalogue order) on the
# patched source with unittest, as one suite in one process (class and module fixtures run once,
# in order, as under pytest), and reports each test's outcome: "pass" only for a test that started,
# raised nothing (no failure, error, failing subtest, skip, expected failure or unexpected success)
# and finished within its limit. The caller drops "pass" tests and keeps everything else, including
# tests that never reported.
VERIFY_CODE = PATCHED_IMPORTS + r'''
import time, unittest
try:
    # the labelling environment's packages that change sqlglot's or the tests' code paths (checked
    # by the first run of a batch; the image does not change between runs)
    if spec["env"]:
        from importlib.metadata import version as _version
        for _name, _want in spec["env"].items():
            if _version(_name) != _want:
                raise RuntimeError("%s %s != %s" % (_name, _version(_name), _want))
        import dateutil.relativedelta, pytz  # noqa: F401
except BaseException as e:
    sys.stdout.write("\n@@BR-VERIFY@@" + json.dumps({"__env__": repr(e)[:300]}) + "\n")
    sys.stdout.flush()
    os._exit(0)
os.chdir(src)
report, by_test = {}, {}
poisoned = []   # test-id prefixes whose class or module fixture raised; "" covers every test

def mark(test, value):
    key = by_test.get(id(test))
    if key is not None and report.get(key[0], "pass") == "pass":
        report[key[0]] = value

def unowned(test):
    """Errors from tearDownClass, class cleanups, setUpModule/tearDownModule and module cleanups
    arrive on a placeholder whose description ends in "(module.Class)" or "(module)", after its
    tests reported; pytest charges them to a test of that class or module. No test in that scope
    counts as a pass, earlier or later; an unreadable scope covers every test."""
    if id(test) in by_test:
        return False
    desc = str(getattr(test, "description", "")).strip()
    scope = ""
    if desc.endswith(")") and "(" in desc:
        parts = desc[desc.rindex("(") + 1:-1].split(".")
        for i in range(len(parts), 0, -1):
            path = "/".join(parts[:i]) + ".py"
            if os.path.isfile(path):
                scope = path + "::" + "".join(p + "::" for p in parts[i:])
                break
    poisoned.append(scope)
    for k, v in list(report.items()):
        if k.startswith(scope) and v in ("pass", "slow"):
            report[k] = "error"
    return True

class _Result(unittest.TestResult):
    def startTest(self, test):
        super().startTest(test)
        self._t = time.perf_counter()
    def stopTest(self, test):
        super().stopTest(test)
        key = by_test.get(id(test))
        if key is not None:
            if any(key[0].startswith(p) for p in poisoned):
                report.setdefault(key[0], "error")
            report.setdefault(key[0], "slow" if time.perf_counter() - self._t > key[1] else "pass")
    def addError(self, test, err):
        super().addError(test, err)
        if not unowned(test):
            mark(test, "error")
    def addFailure(self, test, err):
        super().addFailure(test, err)
        if not unowned(test):
            mark(test, "fail")
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            mark(test, "fail")
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        mark(test, "skip")
    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        mark(test, "xfail")
    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        mark(test, "xpass")

try:
    import sqlglot  # noqa: F401  (every test module starts here; a failure shows up per module)
except BaseException:
    pass
suite, mods, keep = unittest.TestSuite(), {}, []
for tid, limit in spec["tests"]:
    parts = tid.split("::")
    if len(parts) != 3 or not parts[0].endswith(".py"):
        report[tid] = "unknown"
        continue
    name = parts[0][:-3].replace("/", ".")
    if name not in mods:
        try:
            mods[name] = importlib.import_module(name)
        except BaseException:
            mods[name] = None
    klass = getattr(mods[name], parts[1], None) if mods[name] is not None else None
    if not (isinstance(klass, type) and issubclass(klass, unittest.TestCase) and
            callable(getattr(klass, parts[2], None))):
        report[tid] = "uncollected"
        continue
    test = klass(parts[2])
    by_test[id(test)] = (tid, float(limit))
    keep.append(test)
    suite.addTest(test)
gc.freeze()   # what the imports built stays; the tests run with the collector on, as usual
gc.enable()
try:
    suite.run(_Result())
except BaseException:
    pass
sys.stdout.write("\n@@BR-VERIFY@@" + json.dumps(report) + "\n")
sys.stdout.flush()
os._exit(0)
'''


_MODULES = None


def all_modules():
    """Every importable sqlglot module of the pinned source (the probe imports them all)."""
    global _MODULES
    if _MODULES is None:
        mods = []
        root = SRC / "sqlglot"
        for dirpath, dirnames, fnames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in sorted(fnames):
                if fn.endswith(".py"):
                    rel = os.path.relpath(os.path.join(dirpath, fn), SRC).replace(os.sep, "/")
                    m = module_of(rel)
                    if "sqlglotc" in m or m.endswith("__main__"):
                        continue
                    mods.append(m)
        _MODULES = mods
    return _MODULES


def _kill(p):
    try:
        if os.name != "nt":
            os.killpg(p.pid, 9)
        else:
            p.kill()
    except OSError:
        pass
    try:
        p.kill()
    except OSError:
        pass


def _probe_modules(files):
    mods = list(all_modules())
    for rel, text in files.items():
        if rel.startswith("sqlglot/"):
            m = module_of(rel)
            if text is None:
                if m in mods:
                    mods.remove(m)
            elif m not in mods:
                mods.append(m)
    return mods


def _probe_env():
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")}
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\WINDOWS")
    cwd = os.environ.get("TMPDIR") or ("/tmp" if os.path.isdir("/tmp") else None)
    return env, cwd


def _verdict(text):
    k = text.rfind("\n@@BR-PROBE@@")
    if k < 0:
        log("probe produced no report")
        return "error"
    try:
        rep = json.loads(text[k + len("\n@@BR-PROBE@@"):].strip())
    except ValueError:
        return "error"
    if rep.get("unserved"):
        log("probe did not load patched modules:", rep["unserved"][:3])
        return "error"
    if rep.get("failed"):
        log("probe: patched import fails:", rep["failed"][0])
        return "broken"
    return "ok"


def probe_stages(mods, extra):
    """Checkpoint stages for the probe server: consecutive slices of the probe order. The first is
    the first module alone; `extra` more end at evenly spaced points of the sqlglot.dialects.*
    block, where most import time goes (each dialect builds its tokenizer, parser and generator)."""
    stages = [list(mods[:1])]
    block = [i for i, m in enumerate(mods) if m.startswith("sqlglot.dialects.")]
    if extra <= 0 or not block:
        return stages
    first, last = block[0], block[-1] + 1
    ends = sorted({first + (last - first) * i // (extra + 1) for i in range(1, extra + 1)})
    start = 1
    for end in ends:
        if end > start:
            stages.append(list(mods[start:end]))
            start = end
    return stages


class _Stage:
    """The client end of one checkpoint process."""

    def __init__(self, idx, wfile, rfile):
        self.idx, self.wfile, self.rfile = idx, wfile, rfile
        self.loaded, self.attempted = set(), set()
        self.pid = None
        self.ok = False
        self.dead = False
        self.forked = 0
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.waiters = {}
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def usable(self):
        return self.ready.is_set() and self.ok and not self.dead

    def _read(self):
        try:
            first = self.rfile.readline()
            if first:
                msg = json.loads(first)
                self.loaded, self.attempted = set(msg["loaded"]), set(msg["attempted"])
                self.pid, self.ok = msg["pid"], bool(msg["ready"])
            self.ready.set()
            for line in self.rfile:
                msg = json.loads(line)
                with self.lock:
                    q = self.waiters.get(msg.get("id"))
                if q is not None:
                    q.put(msg)
        except Exception as e:
            log(f"probe server stage {self.idx} reader:", repr(e))
        self.dead = True
        self.ready.set()
        with self.lock:
            for q in self.waiters.values():
                q.put({"dead": True})


class ProbeServer:
    """Client for SERVER_CODE. Any trouble with a checkpoint makes run() return None, and the
    caller falls back to a fresh probe; a probe child that times out is killed with its group."""

    START_TIMEOUT_S = 10.0

    def __init__(self, proc, stages):
        self.proc, self.stages = proc, stages
        self._lock = threading.Lock()
        self._next = 0

    @property
    def forked(self):
        return sum(s.forked for s in self.stages)

    @staticmethod
    def eligible_for(files, loaded, attempted):
        for rel, text in files.items():
            if not rel.startswith("sqlglot/") or not rel.endswith(".py"):
                return False
            m = module_of(rel)
            if m in loaded or m in attempted:
                return False
        return True

    def stage_for(self, files):
        """Index of the deepest usable checkpoint that may serve these files, or None."""
        for s in reversed(self.stages):
            if s.usable() and self.eligible_for(files, s.loaded, s.attempted):
                return s.idx
        return None

    def eligible(self, files):
        return self.stage_for(files) is not None

    def wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        for s in self.stages:
            s.ready.wait(max(0.0, deadline - time.monotonic()))
        return all(s.usable() for s in self.stages)

    @classmethod
    def start(cls, extra=None):
        if os.environ.get("BR_PROBE_SERVER", "1") == "0" or not hasattr(os, "fork") or os.name == "nt":
            return None
        if extra is None:
            extra = PROBE_CHECKPOINTS
        stages = probe_stages(all_modules(), extra)
        env, cwd = _probe_env()
        parent_ends, child_ends = [], []
        try:
            for _ in stages:
                req_r, req_w = os.pipe()
                rep_r, rep_w = os.pipe()
                parent_ends.append((req_w, rep_r))
                child_ends.append((req_r, rep_w))
            proc = subprocess.Popen([sys.executable, "-I", "-S", "-c", SERVER_CODE], stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=cwd,
                                    start_new_session=True, pass_fds=[fd for pair in child_ends for fd in pair])
        except Exception as e:
            log("probe server could not start:", repr(e))
            for pair in parent_ends + child_ends:
                for fd in pair:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            return None
        for a, b in child_ends:
            os.close(a)
            os.close(b)
        clients = [_Stage(i, os.fdopen(w, "wb"), os.fdopen(r, "rb")) for i, (w, r) in enumerate(parent_ends)]
        server = cls(proc, clients)
        try:
            proc.stdin.write(json.dumps({"src": str(SRC), "stages": stages,
                                         "fds": [list(p) for p in child_ends]}).encode())
            proc.stdin.close()
        except Exception as e:
            log("probe server failed:", repr(e))
        clients[0].ready.wait(cls.START_TIMEOUT_S)
        if not clients[0].usable():
            log("probe server not ready; fresh probes only")
            server.close()
            return None
        return server

    def run(self, files, timeout):
        """Verdict, or None when no checkpoint can serve (the caller probes fresh)."""
        import queue
        idx = self.stage_for(files)
        if idx is None:
            return None
        st = self.stages[idx]
        deadline = time.monotonic() + timeout
        q = queue.Queue()
        with self._lock:
            self._next += 1
            rid = self._next
        with st.lock:
            st.waiters[rid] = q
            try:
                spec = {"src": str(SRC), "files": files, "modules": _probe_modules(files)}
                st.wfile.write((json.dumps({"id": rid, "spec": spec}) + "\n").encode("utf-8", "surrogateescape"))
                st.wfile.flush()
            except Exception as e:
                log(f"probe server stage {idx} write failed:", repr(e))
                st.dead = True
                st.waiters.pop(rid, None)
                return None
        try:
            pid = None
            while True:
                try:
                    msg = q.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    if pid is None:
                        log(f"probe server stage {idx} did not start a child in time")
                        st.dead = True
                        return None
                    try:
                        os.killpg(pid, 9)
                    except OSError:
                        pass
                    with st.lock:
                        st.forked += 1
                    return "timeout"
                if msg.get("dead"):
                    return None
                if "pid" in msg:
                    pid = msg["pid"]
                elif "out" in msg:
                    with st.lock:
                        st.forked += 1
                    return _verdict(msg["out"])
        finally:
            with st.lock:
                st.waiters.pop(rid, None)

    def close(self):
        for s in self.stages:
            try:
                s.wfile.close()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            _kill(self.proc)
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        for s in self.stages:
            s.reader.join(timeout=2)
            try:
                s.rfile.close()
            except Exception:
                pass

    def kill_for_test(self):
        _kill(self.proc)
        self.proc.wait(timeout=5)
        for s in self.stages:
            s.reader.join(timeout=5)
            s.dead = True


_PROBE_LAZY = {}   # id(files) -> lazy verdict of the last run_probe on that job (read by run_probes)


def run_probe(files, timeout, server=None):
    """'ok' | 'broken' | 'timeout' | 'error' for importing every sqlglot module with the patch."""
    res, lazy = probe_report(files, timeout, server)
    _PROBE_LAZY[id(files)] = lazy
    return res


def probe_report(files, timeout, server=None):
    """(outcome, lazy): run_probe's outcome, and whether test_lazy_load's assertion holds after the
    patched `import sqlglot` in a fresh interpreter (None when not known)."""
    if server is not None and server.eligible(files):
        start = time.monotonic()
        res = server.run(files, timeout)
        if res is not None:
            # served after a pristine `import sqlglot`: the patch touches nothing that import loaded
            # or looked for, so that import, and the assertion on it, behave exactly as pristine
            return res, (True if res == "ok" else None)
        timeout = max(MIN_PROBE_S, timeout - (time.monotonic() - start))
    payload = json.dumps({"src": str(SRC), "files": files, "modules": _probe_modules(files)}).encode(
        "utf-8", "surrogateescape")
    env, cwd = _probe_env()
    kw = {"start_new_session": True} if os.name != "nt" else {}
    try:
        p = subprocess.Popen([sys.executable, "-I", "-S", "-c", PROBE_CODE], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=cwd, **kw)
    except Exception as e:
        log("probe could not start:", repr(e))
        return "error", None
    try:
        out, _ = p.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        try:
            p.communicate(timeout=5)
        except Exception:
            pass
        return "timeout", None
    except Exception as e:
        _kill(p)
        log("probe failed:", repr(e))
        return "error", None
    res = _verdict(out.decode("utf-8", "replace"))
    return res, (_lazy_of(out) if res == "ok" else None)


def _lazy_of(raw):
    """test_lazy_load's verdict from a fresh probe's stdout bytes: the report's "lazy" field (the
    class count, and 'Success' printed without error), then the test's own stdout assertion on
    the bytes printed up to the probe's end marker, decoded strictly as the test's text-mode
    capture would. None if the probe did not import `sqlglot` itself."""
    text = raw.decode("utf-8", "replace")
    k = text.rfind("\n@@BR-PROBE@@")
    try:
        rep = json.loads(text[k + len("\n@@BR-PROBE@@"):].strip()) if k >= 0 else None
    except ValueError:
        return None
    lazy = rep.get("lazy") if isinstance(rep, dict) else None
    if lazy is not True:
        return lazy if isinstance(lazy, bool) else None
    j = raw.find(b"\n@@BR-LAZY-END@@\n")
    if j < 0:
        return False
    try:
        return "Success" in raw[:j].decode("utf-8")
    except UnicodeDecodeError:
        return False


def run_probes(jobs, parallel=PROBE_PARALLEL, lazy=None):
    """jobs: {key: files}. Returns {key: outcome}. Every probe gets at most what is left of the
    batch budget (minus the reserve for scheduling and output); one that cannot start in time is
    'skipped'. The caller treats anything but 'ok' as "run every test". Jobs with identical files
    are probed once. When two or more jobs patch only sqlglot modules, a probe server is started
    and serves the jobs it is eligible for (see SERVER_CODE). If `lazy` is a dict, it receives
    {key: test_lazy_load verdict} (see probe_report) for the jobs whose probe was 'ok'."""
    lazies = {}
    groups = {}
    for key, files in jobs.items():
        groups.setdefault(json.dumps(files, sort_keys=True), []).append(key)
    unique = {keys[0]: jobs[keys[0]] for keys in groups.values()}
    results = {}
    order = list(unique)
    lock = threading.Lock()
    server = None
    if sum(all(r.startswith("sqlglot/") for r in f) for f in unique.values()) >= 2:
        server = ProbeServer.start()

    def worker():
        while True:
            with lock:
                if not order:
                    return
                key = order.pop(0)
            left = remaining() - RESERVE_S
            lz = None
            if left < MIN_PROBE_S:
                res = "skipped"
            else:
                res = run_probe(unique[key], min(PROBE_TIMEOUT_S, left), server=server)
                lz = _PROBE_LAZY.pop(id(unique[key]), None) if res == "ok" else None
            with lock:
                results[key] = res
                lazies[key] = lz

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(parallel, len(order)))]
    for t in threads:
        t.start()
    for t in threads:
        # never wait past the budget: a stuck probe is abandoned (and its commit runs everything)
        t.join(max(0.0, remaining() - RESERVE_S + PROBE_TIMEOUT_S + 6))
    if server is not None:
        if all(not t.is_alive() for t in threads):
            server.close()
        log(f"probe server: {server.forked} of {len(unique)} probes forked")
    out = {}
    with lock:   # a probe still running past the budget counts as skipped
        for keys in groups.values():
            for key in keys:
                out[key] = results.get(keys[0], "skipped")
                if lazy is not None:
                    lazy[key] = lazies.get(keys[0])
    return out


def verify_report(files, tests, timeout):
    """{catalogue id: outcome} from running tests ([(id, limit_s)]) on the patched source in a
    fresh interpreter (VERIFY_CODE), or None when the run did not finish and report."""
    global _ENV_CONFIRMED
    payload = json.dumps({"src": str(SRC), "files": files, "tests": tests,
                          "env": {} if _ENV_CONFIRMED else VERIFY_ENV}).encode("utf-8", "surrogateescape")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "BR_"))}
    env["SKIP_INTEGRATION"] = "0"   # as the suite was labelled
    kw = {"start_new_session": True} if os.name != "nt" else {}
    cwd = os.environ.get("TMPDIR") or ("/tmp" if os.path.isdir("/tmp") else None)
    try:
        # site-packages on (no -S): the pinned VERIFY_ENV packages live there
        p = subprocess.Popen([sys.executable, "-I", "-c", VERIFY_CODE], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd=cwd, **kw)
    except Exception as e:
        log("verifier could not start:", repr(e))
        return None
    try:
        out, _ = p.communicate(payload, timeout=timeout)
    except Exception:
        _kill(p)
        try:
            p.communicate(timeout=5)
        except Exception:
            pass
        return None
    text = out.decode("utf-8", "replace")
    k = text.rfind("\n@@BR-VERIFY@@")
    if k < 0:
        return None
    try:
        rep = json.loads(text[k + len("\n@@BR-VERIFY@@"):].strip())
    except ValueError:
        return None
    if not isinstance(rep, dict):
        return None
    if "__env__" not in rep:
        _ENV_CONFIRMED = True
    return rep


_ENV_CONFIRMED = False   # a verifier run of this batch found exactly the VERIFY_ENV packages


def run_verifications(jobs, parallel=PROBE_PARALLEL):
    """jobs: {key: (files, tests, timeout)} -> {key: report or None}, inside the batch budget."""
    results = {}
    order = list(jobs)
    lock = threading.Lock()
    unavailable = []

    def worker():
        while True:
            with lock:
                if not order:
                    return
                key = order.pop(0)
            files, tests, limit = jobs[key]
            left = remaining() - RESERVE_S
            rep = None
            if left >= limit and not unavailable:
                rep = verify_report(files, tests, min(limit, left))
                if rep is not None and "__env__" in rep:
                    log("verifier environment differs from the labelled one, verification off:", rep["__env__"])
                    unavailable.append(True)
                    rep = None
            with lock:
                results[key] = rep

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(parallel, len(order)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, remaining() - RESERVE_S + 6))
    with lock:   # a verifier still running past the budget counts as no report
        return {key: results.get(key) for key in jobs}


# ============================================================================ scheduling


def schedule(tests):
    """tests: [(number, ms)] -> (cost_ms, workers) minimising max load + WORKER_MS per worker."""
    if not tests:
        return 0, []
    durs = sorted(tests, key=lambda x: (-x[1], x[0]))
    total = sum(ms for _, ms in durs)
    best = None
    for w in range(1, min(MAX_WORKERS, len(durs)) + 1):
        if best is not None and max(durs[0][1], -(-total // w)) + WORKER_MS * w >= best[0]:
            break
        loads, bins = [0] * w, [[] for _ in range(w)]
        for num, ms in durs:
            i = loads.index(min(loads))
            loads[i] += ms
            bins[i].append(num)
        cost = max(loads) + WORKER_MS * w
        if best is None or cost < best[0]:
            best = (cost, [b for b in bins if b])
    return best


# ============================================================================ main


def _load(path):
    raw = Path(path).read_bytes()
    if raw[:1] == b"x" or raw[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        import zlib
        raw = zlib.decompress(raw)
    return pickle.loads(raw)


def load_map():
    return _load(MAP)


def load_profile(tests):
    """The runtime profile, if present and built for the same test list."""
    try:
        if not PROFILE.exists():
            return None
        prof = _load(PROFILE)
        if prof.get("version") != 2 or prof.get("tests") != tests or not prof.get("complete"):
            log("profile ignored: wrong version, another test list, or incomplete")
            return None
        trusted = sum(m is not None for o in prof["keys"].values() for m in o["mask"])
        log(f"profile loaded: version 2 from {PROFILE}, {trusted} trusted dictionary entries, "
            f"{len(prof['cmp'])} comparison sites, {bin(prof.get('unobserved', 0)).count('1')} unobserved tests")
        return prof
    except Exception as e:
        log("profile unavailable:", repr(e))
        return None


def verify(hunter, catalog, selected, verify_files, ms_by_num, idx_to_num):
    """Run the selections worth running here (see VERIFY) and drop the tests that passed."""
    id_of = {t["number"]: t["id"] for t in catalog}
    idx_of = {n: i for i, n in enumerate(idx_to_num) if n is not None}
    jobs = {}
    for cid, files in verify_files.items():
        nums = selected.get(cid)
        if not nums or any(n not in idx_of or hunter.unverifiable >> idx_of[n] & 1 for n in nums):
            continue
        ms = sum(ms_by_num[n] for n in nums)
        cost = schedule([(n, ms_by_num[n]) for n in nums])[0]
        if ms > VERIFY_MAX_MS or cost < 2 * (VERIFY_START_MS + ms):
            continue
        # a test far slower than recorded may time out where the suite is labelled: kept
        tests = [(id_of[n], max(1.0, 5 * ms_by_num[n] / 1000 + 0.5)) for n in sorted(nums)]
        jobs[cid] = (files, tests, 3.0 + sum(lim for _, lim in tests))
    if not jobs:
        return
    for cid, rep in run_verifications(jobs).items():
        if rep is None:
            log(cid, "verifier: no report, selection kept")
            continue
        kept = {n for n in selected[cid] if rep.get(id_of[n]) != "pass"}
        log(cid, f"verifier: ran {len(selected[cid])} tests on the patch, {len(kept)} kept",
            sorted({v for v in rep.values() if v != "pass"}) or "")
        selected[cid] = kept


def main():
    catalog = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))["tests"]
    patches = sorted((DATA / "commits").glob("*.patch"))
    ms_by_num = {t["number"]: int(round(t["seconds"] * 1000)) for t in catalog}
    all_nums = [t["number"] for t in catalog]
    hunter = None
    try:
        data = load_map()
        hunter = Hunter(data, load_profile(data["tests"]))
        num_of = {t["id"]: t["number"] for t in catalog}
        idx_to_num = [num_of.get(tid) for tid in data["tests"]]
        known = {n for n in idx_to_num if n is not None}
        unmapped = [n for n in all_nums if n not in known]
    except Exception as e:
        log("map unavailable, catcher runs everything:", repr(e))

    selected, probes, lazy_pending, verify_files = {}, {}, set(), {}
    for p in patches:
        cid = p.stem
        try:
            if hunter is None:
                raise RuntimeError("no hunter")
            if remaining() < RESERVE_S:
                raise RuntimeError("out of time")
            text = p.read_bytes().decode("utf-8", "surrogateescape")
            mask, probe = hunter.analyze(text)
            if mask and mask == hunter.ALL:
                selected[cid] = None
            else:
                nums = set(unmapped) if mask else set()
                bits = mask & hunter.ALL
                while bits:
                    bit = bits & -bits
                    num = idx_to_num[bit.bit_length() - 1]
                    if num is not None:
                        nums.add(num)
                    bits ^= bit
                selected[cid] = nums
            if probe is not None:
                probes[cid] = probe
                if hunter.lazy_pending:
                    lazy_pending.add(cid)
            if hunter.verify_files is not None:
                verify_files[cid] = hunter.verify_files
        except Exception as e:
            log(cid, "hunter failed, catcher runs everything:", repr(e))
            selected[cid] = None

    if probes:
        lazy_seen = {}
        outcome = run_probes(probes, lazy=lazy_seen)
        for cid, res in outcome.items():
            if res != "ok":
                log(cid, f"import probe {res}: catcher takes all")
                selected[cid] = None
            elif cid in lazy_pending and selected.get(cid) is not None:
                if lazy_seen.get(cid) is True:
                    log(cid, "probe: fresh `import sqlglot` still creates one dialect class")
                else:
                    log(cid, "probe: fresh `import sqlglot` changed: keeping", LAZY_TEST)
                    selected[cid].update(n for n in (idx_to_num[i] for i in range(hunter.n)
                                                     if hunter.lazy_mask >> i & 1) if n is not None)
                    selected[cid].update(unmapped)

    if VERIFY and hunter is not None and verify_files:
        try:
            verify(hunter, catalog, selected, verify_files, ms_by_num, idx_to_num)
        except Exception as e:   # the selections stand as they are
            log("verification failed, selections kept:", repr(e))

    out, total = [], 0
    all_schedule = None
    for p in patches:
        cid = p.stem
        nums = selected.get(cid)
        if nums is None:
            if all_schedule is None:
                all_schedule = schedule(list(ms_by_num.items()))
            cost, workers = all_schedule
        else:
            cost, workers = schedule([(n, ms_by_num[n]) for n in nums])
        total += cost
        log(f"{cid}: {sum(map(len, workers))} tests, {len(workers)} workers, {cost / 1000:.3f}s")
        out.append(json.dumps({"commit": cid, "workers": workers}, separators=(",", ":")))
    sys.stdout.write("".join(line + "\n" for line in out))
    sys.stdout.flush()
    log(f"predicted schedule total {total / 1000:.3f}s over {len(out)} commits, "
        f"{len(probes)} probes, {time.monotonic() - T0:.2f}s wall")


if __name__ == "__main__":
    main()

"""Blast Radius selector: a hunter and a catcher.

hunter  - reads each patch, pins every edit to the exact AST node it touches (a statement in
          a function, a lambda, a class attribute, a module constant, a fixture file) and maps
          it to the tests that can observe it, using a per-test coverage map of the pinned
          sqlglot that was recorded at build time (lines run per test, including spawned pool
          workers and class-level setup; fixture files opened; classes instantiated).
catcher - the safety net behind the hunter. Whenever precise reasoning runs out (syntax
          errors, deleted files, code that runs while modules import, names it cannot follow,
          missing sources) it widens to a conservative superset, and for import-time edits it
          actually imports the patched modules in a sandbox: if that raises, every test fails.
Finally the selected tests are packed onto 1..8 workers to minimise max(load) + 2 s/worker.

Standard library only. stdout carries exactly one JSON line per commit; logs go to stderr.
"""

import ast
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BR_DATA", "/data"))
SRC = Path(os.environ.get("BR_SRC", HERE / "src"))
MAP = Path(os.environ.get("BR_MAP", HERE / "map.pkl"))
WORKER_MS = 2000
MAX_WORKERS = 8
DIALECT_DIRS = ("dialects", "parsers", "generators", "typing", "tokenizers")
CORE_MODULES = {"sqlglot", "sqlglot.tokens", "sqlglot.tokenizer_core", "sqlglot.parser", "sqlglot.generator",
                "sqlglot.dialects.dialect", "sqlglot.expressions.core", "sqlglot.helper", "sqlglot.trie"}
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
COMPOUND = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)
if hasattr(ast, "TryStar"):
    COMPOUND = COMPOUND + (ast.TryStar,)


def log(*a):
    print(*a, file=sys.stderr)


def module_of(rel):
    m = rel[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


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


# Static semantic equivalence, not a patch lookup: None and enum members are
# singletons, so an identity test and an equality test against one agree.
# True/False are deliberately excluded: 1 == True but 1 is not True.
_EQ_OP = re.compile(r"\bis not\b|\bis\b|==|!=")
_EQ_SINGLETON = re.compile(r"^None\b|^(?:[A-Za-z_][\w]*\.)*([A-Za-z_]\w*)\.([A-Z][A-Z0-9_]*)\b")
ENUM_NAMES = set()   # sqlglot classes deriving from Enum (from the map); only their members are singletons
_EQ_PAIRS = {("is", "=="), ("==", "is"), ("is not", "!="), ("!=", "is not")}


def _equivalent_op_change(a, b):
    """Only `is` <-> `==` (or `is not` <-> `!=`) against None / an enum member changed."""
    ma, mb = list(_EQ_OP.finditer(a)), list(_EQ_OP.finditer(b))
    if len(ma) != 1 or len(mb) != 1:
        return False
    if (ma[0].group(), mb[0].group()) not in _EQ_PAIRS:
        return False
    if a[:ma[0].start()] != b[:mb[0].start()] or a[ma[0].end():] != b[mb[0].end():]:
        return False
    m = _EQ_SINGLETON.match(a[ma[0].end():].strip())
    if not m:
        return False
    # None, or a member of a class known to be an Enum (not just any UPPER_CASE attribute:
    # Drill.DATE_FORMAT is a str, exp.UnixToTime.SECONDS an expression)
    return m.group(1) is None or m.group(1) in ENUM_NAMES


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
    t = text.rstrip()
    return len(t) - len(t.lstrip()), max(len(t), len(t) - len(t.lstrip()) + 1)


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


# ============================================================================ hunter


class Hunter:
    def __init__(self, data):
        self.d = data
        self.n = len(data["tests"])
        self.ALL = (1 << self.n) - 1
        self.lines = data["lines"]
        self.classes = data["classes"]
        self.imp = {f: set(v) for f, v in data["imp_lines"].items()}
        self._src, self._tree, self._group = {}, {}, {}
        self.import_time = False
        self.to_import = {}
        ENUM_NAMES.clear()
        ENUM_NAMES.update(data.get("enum_names", ()))

    # ---------------------------------------------------------------- sources
    def source(self, rel):
        if rel not in self._src:
            try:
                self._src[rel] = (SRC / rel).read_text(encoding="utf-8")
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
        old = self.source(rel)
        if old is None:
            log(rel, "no source: catcher takes all")
            return self.ALL
        new = apply_hunks(old, fp.hunks)
        if new is None:
            log(rel, "patch does not apply: catcher takes the file")
            return self.fallback(rel) | self.d["subproc"]
        try:
            ntree = ast.parse(new)
        except (SyntaxError, ValueError):
            log(rel, "syntax error: everything importing it fails")
            return self.ALL
        otree = self.tree(rel)
        if otree is None:
            return self.ALL
        visited = set()
        m = 0
        before, self.import_time = self.import_time, False
        for kind, ol, nl, ot, nt in edits(fp.hunks):
            if kind == "replace":
                if ot.rstrip() == nt.rstrip() or self._comment_change(ot, nt, otree, ol) or _equivalent_op_change(ot, nt):
                    continue
                c0, c1 = changed_cols(ot, nt)
                site = locate(otree, ol, c0, c1)
                m |= self._site_mask(rel, site, visited)
            elif kind == "remove":
                if not ot.strip() or (ot.strip().startswith("#") and not self._in_string(otree, ol)):
                    continue
                c0, c1 = line_cols(ot)
                m |= self._site_mask(rel, locate(otree, ol, c0, c1, precise=False), visited)
            else:
                if not nt.strip() or (nt.strip().startswith("#") and not self._in_string(ntree, nl)):
                    continue
                m |= self._added(rel, otree, ntree, ol, nl, nt, visited)
        if self.import_time and rel.startswith("sqlglot/"):
            self.to_import[rel] = new
        self.import_time = self.import_time or before
        return m

    def _site_mask(self, rel, site, visited):
        if site.kind == "func":
            return self.func_mask(rel, site, visited)
        if site.kind == "stmt":
            st = site.stmt
            return self.resolve_stmt(rel, site.cls_path, assigned_names(st), site.span, is_docstring(st), visited)
        return 0

    def _added(self, rel, otree, ntree, ol, nl, nt, visited):
        c0, c1 = line_cols(nt)
        site = locate(ntree, nl, c0, c1, precise=False)
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
            names = assigned_names(st)
            span = (max(1, ol - 1), ol)
            if isinstance(st, DEFS) and find_def(otree, site.func_path) is None:
                return self.new_member(rel, st.name, site.cls_path[:-1] if isinstance(st, ast.ClassDef) else site.cls_path, visited)
            return self.resolve_stmt(rel, site.cls_path, names, span, is_docstring(st), visited)
        return 0

    @staticmethod
    def _in_string(tree, line):
        for node in chain_at(tree, line):
            if isinstance(node, (ast.Constant, ast.JoinedStr)) and node.lineno < line <= node.end_lineno:
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
            and not self._in_string(otree, ol)

    # ---------------------------------------------------------------- other files
    def analyze_other(self, rel):
        m = self.d["opens"].get(rel, 0)
        if rel in self.d["imp_opens"]:
            return self.ALL
        if not m and rel.startswith("tests/"):
            m = self.tests_mentioning(os.path.splitext(os.path.basename(rel))[0]) or self.ALL
        return m

    def tests_mentioning(self, word):
        files = {t.split("::")[0] for t in self.d["tests"]}
        hit = {f for f in files if word in (self.source(f) or "")}
        m = 0
        for i, t in enumerate(self.d["tests"]):
            if t.split("::")[0] in hit:
                m |= 1 << i
        return m

    # ---------------------------------------------------------------- one patch
    def analyze(self, patch_text):
        self.import_time = False
        self.to_import = {}
        m = 0
        for fp in parse_patch(patch_text):
            rel = fp.old or fp.new
            if rel is None or not rel.startswith(("sqlglot/", "tests/")):
                continue
            try:
                if fp.new is None or (fp.old and fp.old != fp.new):
                    log(rel, "deleted or renamed: catcher takes all")
                    m |= self.ALL
                elif fp.old is None:
                    if rel.endswith(".py"):
                        # a new module is only seen by discovery (entry points, package walks)
                        m |= self.fallback(os.path.dirname(rel) + "/__init__.py") | self.d["subproc"]
                        if rel.startswith("tests/"):
                            m |= self.tests_mentioning(os.path.splitext(os.path.basename(rel))[0])
                    else:
                        m |= self.analyze_other(rel)
                elif rel.endswith(".py"):
                    m |= self.analyze_py(rel, fp)
                    if rel.startswith("tests/") and self.import_time:
                        # shared test helpers: everything importing them
                        m |= self.tests_mentioning(module_of(rel).rsplit(".", 1)[-1])
                else:
                    m |= self.analyze_other(rel)
            except Exception as e:   # catcher: never crash, widen instead
                log(rel, "analysis error, catcher widens:", repr(e))
                m |= self.fallback(rel) | self.d["subproc"] if rel.endswith(".py") else self.ALL
        if self.import_time:
            m |= self.d["subproc"]
        if self.to_import and m != self.ALL and not import_ok(self.to_import):
            log("patched modules fail to import: catcher takes all")
            m = self.ALL
        return m


# ============================================================================ catcher: live import check


def _overlay(root, patched):
    """Mirror SRC/sqlglot under root with symlinks; only patched files are written."""
    need = set()
    for rel in patched:
        d = os.path.dirname(rel)
        while d:
            need.add(d)
            d = os.path.dirname(d)

    def build(rel_dir):
        os.makedirs(os.path.join(root, rel_dir), exist_ok=True)
        for entry in os.listdir(SRC / rel_dir):
            sub = f"{rel_dir}/{entry}"
            dst = os.path.join(root, sub)
            if sub in patched:
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(patched[sub])
            elif sub in need:
                build(sub)
            else:
                os.symlink(SRC / sub, dst)

    build("sqlglot")


def import_ok(patched):
    """False only when importing the patched modules raises."""
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="br-", dir="/tmp" if os.path.isdir("/tmp") else None)
        _overlay(tmp, patched)
        code = "import importlib, sys; [importlib.import_module(m) for m in sys.argv[1:]]"
        env = {"PYTHONPATH": tmp, "PYTHONDONTWRITEBYTECODE": "1", "HOME": tmp, "PATH": os.environ.get("PATH", "")}
        cmd = [sys.executable, "-S", "-c", code, *[module_of(p) for p in patched]]
        try:
            r = subprocess.run(cmd, cwd=tmp, env=env, timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            failed = r.returncode != 0
            detail = r.stderr.decode(errors="replace").strip().splitlines()[-1:] if failed else []
        except subprocess.TimeoutExpired:
            # A hang at import time fails every test just as a raise does.
            failed, detail = True, ["import timed out"]
        if failed:
            log("import check failed:", detail)
            # make sure the unpatched modules import here, or the failure says nothing
            env["PYTHONPATH"] = str(SRC)
            # cwd must not be the overlay: -c puts the cwd first on sys.path
            try:
                r0 = subprocess.run(cmd, cwd=str(SRC), env=env, timeout=30,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                return True   # control hangs too: an environment problem, not the patch
            return r0.returncode != 0
        return True
    except Exception as e:
        log("import check skipped:", repr(e))
        return True
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


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


def load_map():
    raw = MAP.read_bytes()
    if raw[:1] == b"x" or raw[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        import zlib
        raw = zlib.decompress(raw)
    return pickle.loads(raw)


def main():
    catalog = json.loads((DATA / "tests.json").read_text())["tests"]
    patches = sorted((DATA / "commits").glob("*.patch"))
    ms_by_num = {t["number"]: int(round(t["seconds"] * 1000)) for t in catalog}
    all_nums = [t["number"] for t in catalog]
    hunter = None
    try:
        data = load_map()
        hunter = Hunter(data)
        num_of = {t["id"]: t["number"] for t in catalog}
        idx_to_num = [num_of.get(tid) for tid in data["tests"]]
        known = {n for n in idx_to_num if n is not None}
        unmapped = [n for n in all_nums if n not in known]
    except Exception as e:
        log("map unavailable, catcher runs everything:", repr(e))
    out, total = [], 0
    for p in patches:
        try:
            if hunter is None:
                raise RuntimeError("no hunter")
            mask = hunter.analyze(p.read_text(encoding="utf-8", errors="replace"))
            nums = {idx_to_num[i] for i in range(hunter.n) if (mask >> i) & 1} - {None}
            if mask:
                nums.update(unmapped)   # tests the map never saw: keep them
        except Exception as e:
            log(p.stem, "hunter failed, catcher runs everything:", repr(e))
            nums = set(all_nums)
        cost, workers = schedule([(n, ms_by_num[n]) for n in nums])
        total += cost
        log(f"{p.stem}: {len(nums)} tests, {len(workers)} workers, {cost / 1000:.3f}s")
        out.append(json.dumps({"commit": p.stem, "workers": workers}, separators=(",", ":")))
    sys.stdout.write("".join(line + "\n" for line in out))
    sys.stdout.flush()
    log(f"predicted schedule total {total / 1000:.3f}s over {len(out)} commits")


if __name__ == "__main__":
    main()

"""Merge per-process coverage pickles into the selector's map.

usage: mkmap.py <cov_dir> <sqlglot_root> <tests.json> <out.pkl>

Everything is keyed by test id (the selector maps ids to numbers at run time) and masks are
Python ints with one bit per test.
"""
import ast
import glob
import io
import json
import os
import pickle
import sys
import tokenize
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import selector as S  # noqa: E402

cov_dir, root, tests_json, out = sys.argv[1:5]
test_ids = [t["id"] for t in json.load(open(tests_json))["tests"]]
tindex = {tid: i for i, tid in enumerate(test_ids)}

lines = defaultdict(lambda: defaultdict(int))
opens = defaultdict(int)
classes = defaultdict(int)
imp_lines = defaultdict(set)
imp_opens, imp_classes = set(), set()
groups = {}
seen, unknown = 0, set()


def add(i, ls, os_, cl):
    bit = 1 << i
    for fn, ln in ls:
        lines[fn][ln] |= bit
    for p in os_:
        opens[p] |= bit
    for c in cl:
        classes[c] |= bit


for path in sorted(glob.glob(os.path.join(cov_dir, "cov-*.pkl"))):
    d = pickle.load(open(path, "rb"))
    files = d["files"]
    for fi, ln in d["imp_lines"]:
        imp_lines[files[fi]].add(ln)
    imp_opens |= d["imp_opens"]
    imp_classes |= d.get("imp_classes", set())
    for nodeid, (ls, os_, cl, setup) in d["results"].items():
        i = tindex.get(nodeid)
        if i is None:
            unknown.add(nodeid)
            continue
        seen |= 1 << i
        add(i, [(files[fi], ln) for fi, ln in ls], os_, cl)
        g = groups.setdefault(nodeid.rsplit("::", 1)[0], [0, set(), set(), set()])
        g[0] |= 1 << i
        g[1].update((files[fi], ln) for fi, ln in setup[0])
        g[2].update(setup[1])
        g[3].update(setup[2])

# class-level setup (setUpClass, class fixtures) serves every test of the class
for gm, gl, go, gc in groups.values():
    for fn, ln in gl:
        lines[fn][ln] |= gm
    for p in go:
        opens[p] |= gm
    for c in gc:
        classes[c] |= gm

# work done in spawned pool workers, attributed to the submitting test
for path in sorted(glob.glob(os.path.join(cov_dir, "child-*.pkl"))):
    with open(path, "rb") as f:
        while True:
            try:
                nodeid, ls, os_, cl = pickle.load(f)
            except EOFError:
                break
            i = tindex.get(nodeid)
            if i is None:
                unknown.add(str(nodeid))
                continue
            add(i, ls, os_, cl)

missing = [test_ids[i] for i in range(len(test_ids)) if not (seen >> i) & 1]
print("tests without coverage:", len(missing), missing[:5], file=sys.stderr)
print("unknown nodeids:", len(unknown), sorted(unknown)[:5], file=sys.stderr)

name_mask = defaultdict(int)   # name -> tests running a line that mentions it
uses = defaultdict(list)       # name -> [(file, line, info)] mentions evaluated at import time
name_imp, name_imp_cls = set(), set()
subproc = 0
walk = list(os.walk(os.path.join(root, "sqlglot"))) + list(os.walk(os.path.join(root, "tests")))
for dirpath, _, fnames in walk:
    for fn in sorted(fnames):
        if not fn.endswith(".py"):
            continue
        full = os.path.join(dirpath, fn)
        rel = os.path.relpath(full, root)
        with open(full, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lm = lines.get(rel, {})
        imp = imp_lines.get(rel, set())
        body_lines, inst_lines = set(), set()

        def visit(node, cls):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.ClassDef):
                    meta = any(isinstance(b, ast.Name) and b.id == "type" for b in ch.bases)
                    visit(ch, None if meta else ch)
                elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inst = (cls is not None and ch.args.args and ch.args.args[0].arg == "self"
                            and ch.name not in ("__init_subclass__", "__new__", "__set_name__")
                            and not any(isinstance(d, ast.Name) and d.id in ("classmethod", "staticmethod")
                                        for d in ch.decorator_list))
                    rng = range(ch.body[0].lineno, ch.end_lineno + 1)
                    body_lines.update(rng)
                    if inst:
                        inst_lines.update(rng)
                    visit(ch, None)
                else:
                    visit(ch, cls)

        visit(tree, None)
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.NAME:
                continue
            ln, col = tok.start
            name = tok.string
            m = lm.get(ln)
            if m:
                name_mask[name] |= m
            if ln in body_lines:
                if ln in imp:
                    name_imp.add(name)
                    if ln not in inst_lines:
                        name_imp_cls.add(name)
                continue
            site = S.locate(tree, ln, col, col + len(name))
            if site.kind == "func":
                fnode = site.fn
                if isinstance(fnode, ast.Lambda):
                    b = fnode.body
                    inside = (b.lineno, b.col_offset) <= (ln, col) and (ln, col) < (b.end_lineno, b.end_col_offset)
                    if inside:
                        continue   # a lambda body: runs when called
                uses[name].append((rel, ln, ("f", S.start_of(fnode), fnode.end_lineno)))
            elif site.kind == "stmt":
                st = site.stmt
                uses[name].append((rel, ln, ("s", site.cls_path, site.span[0], site.span[1],
                                             tuple(S.assigned_names(st)), S.is_docstring(st))))
        if rel.startswith("tests/") and "subprocess" in src:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "subprocess" in (ast.get_source_segment(src, node) or ""):
                    for ln in range(node.body[0].lineno, node.end_lineno + 1):
                        subproc |= lm.get(ln, 0)

# classes deriving from Enum, resolved by simple name through sqlglot's own class graph
defs_of = {}   # class name -> [bases of each definition]
for dirpath, _, fnames in os.walk(os.path.join(root, "sqlglot")):
    for fn in fnames:
        if fn.endswith(".py"):
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                tree_ = ast.parse(fh.read())
            for node in ast.walk(tree_):
                if isinstance(node, ast.ClassDef):
                    defs_of.setdefault(node.name, []).append([ast.unparse(b).split(".")[-1] for b in node.bases])
bases_of = {k: [b for bases in v for b in bases] for k, v in defs_of.items()}
ENUM_ROOTS = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}


def _is_enum(name, seen=()):
    if name in ENUM_ROOTS:
        return True
    return any(b not in seen and _is_enum(b, seen + (name,)) for b in bases_of.get(name, ()))


def _def_is_enum(bases):
    return any(b in ENUM_ROOTS or _is_enum(b) for b in bases)


# a name defined both as an enum and as something else is not trusted: every definition must be one
enum_names = {n for n, defs in defs_of.items() if all(_def_is_enum(bases) for bases in defs)}
print("enum classes:", len(enum_names), sorted(enum_names)[:12], file=sys.stderr)

data = {
    "enum_names": enum_names,
    "tests": test_ids,
    "lines": {f: dict(v) for f, v in lines.items()},
    "opens": dict(opens),
    "classes": dict(classes),
    "imp_lines": {f: sorted(v) for f, v in imp_lines.items()},
    "imp_opens": sorted(p for p in imp_opens if not p.endswith((".py", ".pyc"))),
    "imp_classes": imp_classes,
    "name_mask": dict(name_mask),
    "uses": dict(uses),
    "name_imp": name_imp,
    "name_imp_cls": name_imp_cls,
    "subproc": subproc,
}
import zlib
blob = zlib.compress(pickle.dumps(S.pack_map(data), protocol=pickle.HIGHEST_PROTOCOL), 9)
open(out, "wb").write(blob)
print("map bytes", len(blob), "files", len(lines), "classes", len(classes), "imp_opens", data["imp_opens"][:5], file=sys.stderr)

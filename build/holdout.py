"""Generate a fresh holdout of patches against the pinned sqlglot (labels come from label.py).

usage: holdout.py <pristine_sqlglot> <out_dir> [--seed N] [--n N]

Writes <out_dir>/commits/<id>.patch and <out_dir>/meta.json ({id: {category, files, note}}).
Nothing here knows what the selector does; categories follow the brief: harmless and breaking
edits across code, class bodies (dictionary values, constants), is/== swaps, fixtures, comments
and encoding declarations, test setup and shared helpers, imports, pytest collection (conftest,
package __init__, new/renamed test modules) and multi-file changes. Every patch is a plain
`git diff` against the pinned commit; labels are produced by running the whole suite.
"""
import argparse
import ast
import difflib
import glob
import io
import json
import os
import random
import re
import tokenize
from pathlib import Path

SWAP = {"and": "or", "or": "and", "<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==",
        "True": "False", "False": "True", "+": "-", "-": "+", "is": "is not", "in": "not in"}


def git_diff(rel, old, new, new_rel=None):
    new_rel = new_rel or rel
    head = f"diff --git a/{rel} b/{new_rel}\n"
    if old is None:
        head += "new file mode 100644\n"
    if new is None:
        head += "deleted file mode 100644\n"
    if new_rel != rel:
        head += f"similarity index 95%\nrename from {rel}\nrename to {new_rel}\n"
    a = f"a/{rel}" if old is not None else "/dev/null"
    b = f"b/{new_rel}" if new is not None else "/dev/null"
    body = "".join(difflib.unified_diff((old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True), a, b))
    if new_rel != rel and not body:
        return head
    return head + body


class Gen:
    def __init__(self, root, rng):
        self.root = Path(root)
        self.rng = rng
        self.py = sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in (self.root / "sqlglot").rglob("*.py")
                         if "__pycache__" not in p.parts and "sqlglotc" not in p.parts)
        self.tests = sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in (self.root / "tests").rglob("test_*.py"))
        self.fixtures = sorted(str(p.relative_to(self.root)).replace(os.sep, "/") for p in (self.root / "tests" / "fixtures").rglob("*")
                               if p.is_file() and p.suffix in (".sql", ".csv", ".json", ".py"))

    def read(self, rel):
        return (self.root / rel).read_bytes().decode("utf-8")

    def choice(self, xs):
        return self.rng.choice(xs)

    # ---------------------------------------------------------------- helpers
    def _regions(self, src):
        """Line sets: function bodies, class bodies (outside functions), module level."""
        tree = ast.parse(src)
        fn, cls = set(), set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                b = n.body if isinstance(n.body, list) else [n.body]
                fn.update(range(b[0].lineno, n.end_lineno + 1))
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                cls.update(ln for ln in range(n.lineno, n.end_lineno + 1) if ln not in fn)
        return tree, fn, cls

    def token_mutation(self, rel, region):
        src = self.read(rel)
        try:
            tree, fn, cls = self._regions(src)
        except SyntaxError:
            return None
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        cands = []
        for i, t in enumerate(toks):
            ln = t.start[0]
            if region == "func" and ln not in fn:
                continue
            if region == "class" and ln not in cls:
                continue
            if t.start[0] != t.end[0]:
                continue
            if t.type in (tokenize.NAME, tokenize.OP) and t.string in SWAP:
                if t.string == "is" and i + 1 < len(toks) and toks[i + 1].string == "not":
                    continue
                if t.string == "in" and i > 0 and toks[i - 1].string in ("not", "for"):
                    continue
                cands.append(("swap", t))
            elif t.type == tokenize.NUMBER and re.fullmatch(r"\d+", t.string):
                cands.append(("num", t))
            elif t.type == tokenize.STRING and region == "class" and len(t.string) > 3 and t.string[0] in "\"'":
                cands.append(("str", t))
        self.rng.shuffle(cands)
        lines = src.splitlines(keepends=True)
        for kind, t in cands[:20]:
            (r, c), (_, c2) = t.start, t.end
            line = lines[r - 1]
            if kind == "swap":
                rep = SWAP[t.string]
            elif kind == "num":
                rep = str(int(t.string) + self.choice([1, -1] if t.string != "0" else [1]))
            else:
                q = t.string[0]
                inner = t.string[1:-1]
                if not inner or "\\" in inner:
                    continue
                rep = q + inner[:-1] + ("X" if inner[-1] != "X" else "Y") + q
            new_line = line[:c] + rep + line[c2:]
            new = "".join(lines[:r - 1] + [new_line] + lines[r:])
            try:
                compile(new, rel, "exec")
            except SyntaxError:
                continue
            return git_diff(rel, src, new), f"{kind} {t.string!r}->{rep!r} line {r}"
        return None

    def eq_swap(self, rel):
        src = self.read(rel)
        pat = re.compile(r"(\S) (is not|is|==|!=) (None|[A-Za-z_][\w.]*\.[A-Z][A-Z0-9_]+)\b")
        lines = src.splitlines(keepends=True)
        hits = [(i, m) for i, ln in enumerate(lines) for m in pat.finditer(ln)
                if not ln.lstrip().startswith("#") and len(list(pat.finditer(ln))) == 1]
        if not hits:
            return None
        i, m = self.choice(hits)
        rep = {"is": "==", "==": "is", "is not": "!=", "!=": "is not"}[m.group(2)]
        ln = lines[i]
        new_line = ln[:m.start(2)] + rep + ln[m.end(2):]
        new = "".join(lines[:i] + [new_line] + lines[i + 1:])
        try:
            compile(new, rel, "exec")
        except SyntaxError:
            return None
        return git_diff(rel, src, new), f"{m.group(2)}->{rep} line {i + 1}"

    def dict_value(self, rel):
        """Change one value under an existing key of a literal dict in a class/module body."""
        src = self.read(rel)
        tree = ast.parse(src)
        cands = []
        for n in ast.walk(tree):
            body = n.body if isinstance(n, (ast.Module, ast.ClassDef)) else []
            for st in body:
                if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Dict):
                    for k, v in zip(st.value.keys, st.value.values):
                        if k is not None and v.lineno == v.end_lineno and k.lineno == v.lineno:
                            cands.append(v)
        if not cands:
            return None
        lines = src.splitlines(keepends=True)
        for _ in range(10):
            v = self.choice(cands)
            seg = lines[v.lineno - 1][v.col_offset:v.end_col_offset]
            if isinstance(v, ast.Constant) and isinstance(v.value, bool):
                rep = str(not v.value)
            elif isinstance(v, ast.Constant) and isinstance(v.value, int):
                rep = str(v.value + 1)
            elif isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value:
                rep = repr(v.value + "_")
            elif isinstance(v, ast.Attribute):
                # another member of the same namespace taken from a sibling value
                sib = [c for c in cands if isinstance(c, ast.Attribute) and ast.unparse(c.value) == ast.unparse(v.value)
                       and c.attr != v.attr]
                if not sib:
                    continue
                rep = ast.unparse(self.choice(sib))
            elif isinstance(v, ast.Call) and v.keywords:
                kw = self.choice(v.keywords)
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                    rep = seg.replace(f"{kw.arg}={kw.value.value}", f"{kw.arg}={not kw.value.value}", 1)
                else:
                    continue
            elif isinstance(v, ast.Tuple) and v.elts and isinstance(v.elts[0], ast.Constant) and isinstance(v.elts[0].value, int):
                rep = "(" + ", ".join([str(v.elts[0].value + 1)] + [ast.unparse(e) for e in v.elts[1:]]) + ("," if len(v.elts) == 1 else "") + ")"
            else:
                continue
            ln = lines[v.lineno - 1]
            new = "".join(lines[:v.lineno - 1] + [ln[:v.col_offset] + rep + ln[v.end_col_offset:]] + lines[v.lineno:])
            if new == src:
                continue
            try:
                compile(new, rel, "exec")
            except SyntaxError:
                continue
            return git_diff(rel, src, new), f"dict value line {v.lineno}: {seg[:40]} -> {rep[:40]}"
        return None

    def fixture(self, rel, harmless):
        src = self.read(rel)
        lines = src.splitlines(keepends=True)
        idx = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("#", "--"))]
        if not idx:
            return None
        i = self.choice(idx)
        body = lines[i].rstrip("\r\n")
        eol = lines[i][len(body):] or "\n"
        if harmless:
            # a blank line between statements of a fixture that separates entries by blank lines
            # is not necessarily harmless; the label decides
            new_line = body + "  " + eol
            note = "trailing spaces"
        else:
            words = body.split(" ")
            j = self.rng.randrange(len(words) + 1)
            words.insert(j, self.choice(["42", "x", "NULL", "0", "1", "AND"]))
            new_line = " ".join(words) + eol
            note = "inserted token"
        new = "".join(lines[:i] + [new_line] + lines[i + 1:])
        return git_diff(rel, src, new), f"{note} line {i + 1}"

    def comment(self, rel, cookie=None):
        src = self.read(rel)
        lines = src.splitlines(keepends=True)
        if cookie:
            return git_diff(rel, src, f"# -*- coding: {cookie} -*-\n" + src), f"coding {cookie}"
        tree, fn, cls = self._regions(src)
        cands = [i for i in fn if i - 1 < len(lines) and lines[i - 1].strip() and not lines[i - 1].rstrip().endswith(("\\", ",", "(", "["))]
        strings = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Constant, ast.JoinedStr)) and n.lineno != n.end_lineno:
                strings.update(range(n.lineno, n.end_lineno + 1))
        cands = [i for i in cands if i not in strings and i + 1 not in strings]
        if not cands:
            return None
        i = self.choice(cands)
        ind = len(lines[i - 1]) - len(lines[i - 1].lstrip())
        new = "".join(lines[:i] + [" " * ind + "# " + self.choice(["note", "TODO: revisit", "keep in sync"]) + "\n"] + lines[i:])
        try:
            compile(new, rel, "exec")
        except SyntaxError:
            return None
        return git_diff(rel, src, new), f"comment after line {i}"

    def import_edit(self, rel, kind):
        src = self.read(rel)
        tree = ast.parse(src)
        lines = src.splitlines(keepends=True)
        imps = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and n.lineno == n.end_lineno
                and not (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
        if kind == "remove":
            if not imps:
                return None
            n = self.choice(imps)
            new = "".join(lines[:n.lineno - 1] + lines[n.lineno:])
            note = f"removed import line {n.lineno}"
        elif kind == "missing":
            pos = imps[-1].lineno if imps else 0
            new = "".join(lines[:pos] + ["import sqlglot_does_not_exist_xyz\n"] + lines[pos:])
            note = "import of a missing module"
        else:
            pos = imps[-1].lineno if imps else 0
            new = "".join(lines[:pos] + ["import os as _unused_os  # noqa\n"] + lines[pos:])
            note = "harmless extra import"
        try:
            compile(new, rel, "exec")
        except SyntaxError:
            return None
        return git_diff(rel, src, new), note


def build(root, out, seed, n):
    rng = random.Random(seed)
    g = Gen(root, rng)
    out = Path(out)
    (out / "commits").mkdir(parents=True, exist_ok=True)
    meta = {}
    helpers = ["tests/helpers.py", "tests/dialects/test_dialect.py"]
    setups = ["tests/test_executor.py", "tests/test_optimizer.py", "tests/test_lineage.py"]
    plan = (["code"] * 12 + ["classbody"] * 6 + ["dictvalue"] * 7 + ["eq"] * 5 + ["fixture"] * 6 + ["fixture-ws"] * 2
            + ["comment"] * 3 + ["cookie"] * 3 + ["setup"] * 3 + ["helper"] * 3 + ["import"] * 5
            + ["collection"] * 6 + ["multi"] * 5)
    rng.shuffle(plan)
    plan = plan[:n]
    counter = 0

    def emit(cat, res, files):
        nonlocal counter
        if res is None:
            return False
        patch, note = res
        pid = f"h{seed % 1000:03d}{counter:02d}"
        counter += 1
        (out / "commits" / f"{pid}.patch").write_text(patch, encoding="utf-8", newline="\n")
        meta[pid] = {"category": cat, "files": files, "note": note}
        return True

    def one(cat):
        for _ in range(30):
            if cat == "code":
                rel = g.choice(g.py)
                r = g.token_mutation(rel, "func")
            elif cat == "classbody":
                rel = g.choice(g.py)
                r = g.token_mutation(rel, "class")
            elif cat == "dictvalue":
                rel = g.choice([p for p in g.py if "/dialects/" in p or "/parsers/" in p or "/generators/" in p or "/tokenizers/" in p] or g.py)
                r = g.dict_value(rel)
            elif cat == "eq":
                rel = g.choice(g.py)
                r = g.eq_swap(rel)
            elif cat in ("fixture", "fixture-ws"):
                rel = g.choice([f for f in g.fixtures if f.endswith(".sql")])
                r = g.fixture(rel, harmless=cat == "fixture-ws")
            elif cat == "comment":
                rel = g.choice(g.py + g.tests)
                r = g.comment(rel)
            elif cat == "cookie":
                rel = g.choice(g.py + g.tests)
                r = g.comment(rel, cookie=g.choice(["ascii", "latin-1", "utf-8"]))
            elif cat == "setup":
                rel = g.choice(setups)
                r = g.token_mutation(rel, "func")
            elif cat == "helper":
                rel = g.choice(helpers)
                r = g.token_mutation(rel, "func") if rng.random() < 0.7 else g.import_edit(rel, "harmless")
            elif cat == "import":
                rel = g.choice(g.py)
                r = g.import_edit(rel, g.choice(["remove", "remove", "missing", "harmless"]))
            elif cat == "collection":
                kind = g.choice(["conftest-bad", "conftest-ok", "nested-conftest", "init", "rename", "newtest"])
                if kind == "conftest-bad":
                    rel, r = "tests/conftest.py", (git_diff("tests/conftest.py", None, "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _boom():\n    raise RuntimeError('broken conftest')\n"), kind)
                elif kind == "conftest-ok":
                    rel, r = "tests/conftest.py", (git_diff("tests/conftest.py", None, "import pytest\n\n\n@pytest.fixture\ndef unused_fixture():\n    return 1\n"), kind)
                elif kind == "nested-conftest":
                    rel = "tests/dialects/conftest.py"
                    r = (git_diff(rel, None, "raise ImportError('nested conftest')\n"), kind)
                elif kind == "init":
                    rel = "tests/dialects/__init__.py"
                    r = (git_diff(rel, g.read(rel), g.read(rel) + "import sqlglot_missing_pkg_xyz\n"), kind)
                elif kind == "rename":
                    rel = g.choice(g.tests)
                    new_rel = rel[:-3] + "_renamed.py"
                    r = (git_diff(rel, g.read(rel), g.read(rel), new_rel=new_rel), f"rename {rel}")
                else:
                    rel = "tests/test_zz_holdout.py"
                    r = (git_diff(rel, None, "import unittest\n\nimport sqlglot\n\n\nclass TestNew(unittest.TestCase):\n    def test_new(self):\n        self.assertTrue(sqlglot.parse_one('select 1'))\n"), kind)
            elif cat == "multi":
                parts, files = [], []
                for sub in rng.sample(["code", "classbody", "fixture", "comment", "dictvalue", "import"], 2):
                    for _ in range(20):
                        if sub in ("code", "classbody"):
                            f = g.choice(g.py)
                            rr = g.token_mutation(f, "func" if sub == "code" else "class")
                        elif sub == "fixture":
                            f = g.choice([x for x in g.fixtures if x.endswith(".sql")])
                            rr = g.fixture(f, harmless=False)
                        elif sub == "comment":
                            f = g.choice(g.py)
                            rr = g.comment(f)
                        elif sub == "dictvalue":
                            f = g.choice(g.py)
                            rr = g.dict_value(f)
                        else:
                            f = g.choice(g.py)
                            rr = g.import_edit(f, "harmless")
                        if rr and f not in files:
                            parts.append(rr)
                            files.append(f)
                            break
                if len(parts) == 2:
                    return emit(cat, ("".join(p for p, _ in parts), " + ".join(nt for _, nt in parts)), files)
                continue
            else:
                raise ValueError(cat)
            if emit(cat, r, [rel]):
                return True
        return False

    for cat in plan:
        one(cat)
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{len(meta)} patches -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--seed", type=int, default=20261121)
    ap.add_argument("--n", type=int, default=80)
    a = ap.parse_args()
    build(a.root, a.out, a.seed, a.n)

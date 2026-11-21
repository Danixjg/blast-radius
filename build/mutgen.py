"""Generate mutation patches resembling the sample set and label them by running the full suite.

usage: mutgen.py <pristine_root> <work_git_checkout> <out_dir> <count> <seed>

Labels come from build/label.py: a run that was interrupted, crashed, timed out or left tests
unreported is stored with status "incomplete" and is never a zero-failure label
(build/answers.py and build/validate.py only score complete labels).
"""
import difflib, io, json, os, random, re, subprocess, sys, tokenize, glob, shutil

pristine, work, out, count, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
rng = random.Random(seed)
os.makedirs(out, exist_ok=True)
py_files = [f for f in glob.glob(os.path.join(pristine, "sqlglot/**/*.py"), recursive=True) if "__init__" not in f]
fx_files = glob.glob(os.path.join(pristine, "tests/fixtures/**/*.sql"), recursive=True)
SWAP = {"and": "or", "or": "and", "is": "==", "==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">",
        "True": "False", "False": "True", "+": "-", "-": "+"}

MODE = os.environ.get("MUT_MODE", "any")
import ast as _ast


def _body_lines(src):
    out = set()
    for n in _ast.walk(_ast.parse(src)):
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.Lambda)):
            b = n.body if isinstance(n.body, list) else [n.body]
            out.update(range(b[0].lineno, n.end_lineno + 1))
    return out


def mutate_py(src):
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    if MODE == "classbody":
        bl = _body_lines(src)
        toks = [t for t in toks if t.start[0] not in bl]
    lines = src.splitlines(keepends=True)
    cands = []
    for i, t in enumerate(toks):
        if t.type in (tokenize.NAME, tokenize.OP) and t.string in SWAP:
            cands.append(("swap", t))
        elif t.type == tokenize.NUMBER and re.fullmatch(r"\d+", t.string):
            cands.append(("num", t))
        elif t.type == tokenize.NAME and t.string == "not" and i + 1 < len(toks):
            cands.append(("not", t))
    if not cands:
        return None
    kind, t = rng.choice(cands)
    (r, c), (_, c2) = t.start, t.end
    line = lines[r - 1]
    if t.start[0] != t.end[0]:
        return None
    if kind == "swap":
        rep = SWAP[t.string]
    elif kind == "num":
        rep = str(int(t.string) + rng.choice([1, -1] if t.string != "0" else [1]))
    else:
        rep = ""
        c2 = c2 + 1 if line[c2:c2 + 1] == " " else c2
    new_line = line[:c] + rep + line[c2:]
    lines[r - 1] = new_line
    new = "".join(lines)
    try:
        compile(new, "x", "exec")
    except SyntaxError:
        return None
    return new

def mutate_sql(src):
    lines = src.splitlines(keepends=True)
    idx = [i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith(("#", "--"))]
    if not idx:
        return None
    i = rng.choice(idx)
    body = lines[i].rstrip("\r\n")
    eol = lines[i][len(body):]
    words = body.split(" ")
    j = rng.randrange(len(words) + 1)
    words.insert(j, rng.choice(["42", "x", "NULL", "0", "1"]))
    lines[i] = " ".join(words) + eol   # keep the line break
    return "".join(lines)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import label  # noqa: E402  (shared labeller: exit status, completeness, incremental record)

CATALOG = label.catalog(os.environ.get("MUT_TESTS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit", "sample", "tests.json")))


done = 0
attempt = 0
while done < count:
    attempt += 1
    use_fx = bool(fx_files) and rng.random() < (0.27 if MODE == "any" else 0.0)
    path = rng.choice(fx_files if use_fx else py_files)
    rel = os.path.relpath(path, pristine)
    src = open(path, encoding="utf-8").read()
    new = mutate_sql(src) if use_fx else mutate_py(src)
    if not new or new == src:
        continue
    diff = "".join(difflib.unified_diff(src.splitlines(keepends=True), new.splitlines(keepends=True), "a/" + rel, "b/" + rel))
    patch = f"diff --git a/{rel} b/{rel}\n" + diff
    mid = f"m{seed}x{done:03d}"
    pfile = os.path.join(out, mid + ".patch")
    with open(pfile, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(patch)
    # <work> must be a clean git checkout of the pin: the patch is applied with git apply and the
    # tree reset afterwards. Only a complete run is a label; anything else is recorded as such.
    lab = label.label_patch(work, pfile, CATALOG)
    lab["file"] = rel
    json.dump(lab, open(os.path.join(out, mid + ".json"), "w"), indent=1)
    print(mid, rel, lab["status"], len(lab["failing"]), flush=True)
    done += 1

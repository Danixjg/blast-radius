"""Record, for every sqlglot module, which sqlglot modules importing it loads (probe_imports.json).

usage: mkimports.py <pinned_sqlglot_tree> <out.json>

Each module is imported alone in a fresh interpreter run the way the import probe runs
(-I -S, the tree first on sys.path), and every sqlglot module in sys.modules afterwards is
recorded (sqlglot's own package __init__ included). The selector's targeted probe imports a
module only if its record contains a patched module. Standard library only; deterministic.
"""
import json
import subprocess
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402

CODE = """import sys, json, importlib
sys.path.insert(0, sys.argv[1])
try:
    importlib.import_module(sys.argv[2])
except BaseException:
    pass
print(json.dumps(sorted(m for m in sys.modules if m == "sqlglot" or m.startswith("sqlglot."))))
"""


def main():
    tree, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    S.SRC = tree
    fwd = {}
    for m in S.all_modules():
        r = subprocess.run([sys.executable, "-I", "-S", "-c", CODE, str(tree), m], capture_output=True, text=True,
                           timeout=120)
        lines = r.stdout.strip().splitlines()
        if r.returncode != 0 or not lines:
            sys.exit(f"{m}: import record failed: {r.stderr[-500:]}")
        fwd[m] = json.loads(lines[-1])
    head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out.write_text(json.dumps({"sqlglot": head, "fwd": fwd}, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(fwd)} modules -> {out}")


if __name__ == "__main__":
    main()

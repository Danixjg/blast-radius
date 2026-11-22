
import sys, os, json, subprocess
src = sys.argv[1]
sys.path.insert(0, sys.argv[2]); import selector as S
from pathlib import Path
S.SRC = Path(src)
mods = S.all_modules()
code = "import sys,json,importlib; sys.path.insert(0,%r)\ntry: importlib.import_module(sys.argv[1])\nexcept BaseException: pass\nprint(json.dumps(sorted(m for m in sys.modules if m=='sqlglot' or m.startswith('sqlglot.'))))" % src
fwd = {}
for m in mods:
    out = subprocess.run([sys.executable, '-I', '-S', '-c', code, m], capture_output=True, text=True).stdout
    fwd[m] = json.loads(out.strip().splitlines()[-1])
json.dump({"mods": mods, "fwd": fwd}, open(sys.argv[3], 'w'))
print(len(mods))

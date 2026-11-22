
import sys, os, ast
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
S.SRC = Path(os.environ["BR_SRC"]); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"
data = S.load_map(); h = S.Hunter(data, S.load_profile(data["tests"]))
ks = [k for k in h.cmp if k[0]=="sqlglot/tokenizer_core.py" and k[1]==1321]
print(ks)
for k in ks:
    rec = h.cmp[k]; print("imp", rec["imp"]); print("facts", {f: bin(m).count("1") for f, m in rec["facts"].items()})
print("ran", bin(h.span_mask("sqlglot/tokenizer_core.py",1321,1321)).count("1"), "ALL", h.n)

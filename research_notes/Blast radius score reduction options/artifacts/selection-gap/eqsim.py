
import sys, os, json
from pathlib import Path
R = Path(sys.argv[1]); sys.path.insert(0, str(R))
import selector as S
S.SRC = Path(os.environ["BR_SRC"]); S.MAP = R/"map.pkl"; S.PROFILE = R/"profile.pkl"
data = S.load_map(); h = S.Hunter(data, S.load_profile(data["tests"]))
k = ("sqlglot/tokenizer_core.py", 1321, 41)
rec = h.cmp[k]
rec["imp"] = frozenset({(False, False, False)})
rec["facts"] = {((False, False, False) if f == (False, None, False) else f): m for f, m in rec["facts"].items()}
text = open("/home/danny/blast-radius/work/holdout2/data/commits/h33727.patch").read()
m, p = h.analyze(text)
print("mask bits", bin(m).count("1"), "probe", p is not None)

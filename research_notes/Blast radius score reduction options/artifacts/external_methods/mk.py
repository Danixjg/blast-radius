
import sys; sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
S.MAP=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl')
d=S.load_map()
for k,v in d.items():
    print(k, type(v).__name__, (len(v) if hasattr(v,'__len__') else v))
    if hasattr(v,'get') and hasattr(v,'__len__'):
        try:
            ks=list(v.keys())[:3]
        except Exception:
            ks=list(getattr(v,'raw',{}).keys())[:3]
        print('   keys', ks, [str(v.get(x))[:150] for x in ks])

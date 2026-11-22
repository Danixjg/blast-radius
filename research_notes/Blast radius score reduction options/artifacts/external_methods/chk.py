
import sys,pickle
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
d=S.unpack_map(S._load("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl'))
i=[k for k,t in enumerate(d['tests']) if t and t.endswith('test_execute_tpcds')][0]
tot=imp=0; files=0
for rel,lm in d['lines'].items():
    if not rel.startswith('sqlglot/'): continue
    il=set(d['imp_lines'].get(rel,()))
    ls=[ln for ln,m in lm.items() if m>>i&1]
    if ls: files+=1
    tot+=len(ls); imp+=len([l for l in ls if l in il])
print('tpcds lines',tot,'of which import-time lines',imp,'files',files)
for rel in ['sqlglot/generators/athena.py','sqlglot/dialects/athena.py']:
    il=set(d['imp_lines'].get(rel,())); ls=[ln for ln,m in d['lines'][rel].items() if m>>i&1]
    print(rel,len(ls),'in imp',len([l for l in ls if l in il]))

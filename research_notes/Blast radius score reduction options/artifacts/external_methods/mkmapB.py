
import sys,pickle,ast
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
SRC=Path('/home/danny/blast-radius/work/sqlglot')
raw=S._load("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl')
d=S.unpack_map(raw)
T=[i for i,t in enumerate(d['tests']) if t and t.split('::')[0] in ('tests/test_executor.py','tests/test_optimizer.py')]
clear=0
for i in T: clear|=1<<i
keep_all=~clear
changed=0
for rel,lm in d['lines'].items():
    if not rel.startswith('sqlglot/'): continue
    imp=set(d['imp_lines'].get(rel,()))
    if not imp: continue
    try: tree=ast.parse((SRC/rel).read_text())
    except Exception: continue
    infunc=set()
    for n in ast.walk(tree):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            for b in n.body:
                infunc.update(range(b.lineno,(b.end_lineno or b.lineno)+1))
        elif isinstance(n,ast.Lambda):
            infunc.update(range(n.lineno,(n.end_lineno or n.lineno)+1))
        elif isinstance(n,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            infunc.update(range(n.lineno,(n.end_lineno or n.lineno)+1))
    for ln in list(lm):
        if ln in imp and ln not in infunc and lm[ln] & clear:
            lm[ln] &= keep_all; changed+=1
print('tests',len(T),'lines changed',changed)
pickle.dump(S.pack_map(d),open('/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/research/external_methods/mapB.pkl','wb'),protocol=pickle.HIGHEST_PROTOCOL)

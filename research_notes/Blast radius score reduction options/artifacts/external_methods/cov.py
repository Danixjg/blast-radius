
import sys,json; sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
S.SRC=Path('/home/danny/blast-radius/work/sqlglot'); S.MAP=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl')
d=S.load_map(); h=S.Hunter(d,None)
ti={t.split('::')[-1]:i for i,t in enumerate(d['tests'])}
for name in ['test_execute_tpcds','test_max_nodes','test_generator_memory']:
    i=ti[name]; files={}
    for rel in list(d['lines'].keys()):
        if not rel.startswith('sqlglot/'): continue
        import pickle
        lm=d['lines'][rel]
        lm=pickle.loads(lm) if isinstance(lm,bytes) else lm
        n=sum(1 for ln,m in lm.items() if int(m)>>i&1)
        if n: files[rel]=n
    print(name, len(files), sum(files.values()), sorted(files.items(), key=lambda x:-x[1])[:12])
    print('  dialect-ish files:', len([f for f in files if any(x in f for x in ('dialects/','generators/','parsers/','tokenizers/'))]))
    print('  athena', {k:v for k,v in files.items() if 'athena' in k})

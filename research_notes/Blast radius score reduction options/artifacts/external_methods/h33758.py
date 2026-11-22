
import sys,json,re
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
S.SRC=Path('/home/danny/blast-radius/work/sqlglot'); S.MAP=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl')
d=S.load_map(); h=S.Hunter(d,None)
rel='sqlglot/generators/tsql.py'
src=(S.SRC/rel).read_text().splitlines()
lines=[i+1 for i,l in enumerate(src) if re.search(r'\breduce\b',l)]
print('lines using reduce',lines)
m=0
for ln in lines: m|=h.span_mask(rel,ln,ln)
ids={d['tests'][i] for i in range(h.n) if m>>i&1}
D="/home/danny/blast-radius/work/sets/holdout2u/data"; A="/home/danny/blast-radius/work/sets/holdout2u/answers.json"
cat=json.load(open(D+'/tests.json'))['tests']; ms={t['number']:round(t['seconds']*1000) for t in cat}
f=json.load(open(A))['commits']['h33758']['failing']
fid=[t['id'] for t in cat if t['number'] in f]
sel=[t['number'] for t in cat if t['id'] in ids]
print('selected',len(ids),'failing',fid,'covered',all(x in ids for x in fid),'cost',S.schedule([(x,ms[x]) for x in sel])[0]/1000)

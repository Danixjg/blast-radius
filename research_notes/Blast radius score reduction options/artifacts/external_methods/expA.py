
import json,sys,ast,os
sys.path.insert(0, "/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
S.SRC=Path('/home/danny/blast-radius/work/sqlglot'); S.MAP=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/map.pkl'); S.PROFILE=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/profile.pkl')
data=S.load_map(); h=S.Hunter(data,S.load_profile(data['tests']))
src=S.SRC
mods={}
for p in list(src.glob('sqlglot/**/*.py'))+list(src.glob('tests/**/*.py')):
    rel=p.relative_to(src).as_posix(); m=rel[:-3].replace('/','.')
    if m.endswith('.__init__'): m=m[:-9]
    mods[m]=rel
imp={}
for m,rel in mods.items():
    try: t=ast.parse((src/rel).read_text())
    except Exception: continue
    pkg=m if rel.endswith('__init__.py') else m.rsplit('.',1)[0]
    s=set()
    for n in ast.walk(t):
        if isinstance(n,ast.Import):
            for a in n.names: s.add(a.name)
        elif isinstance(n,ast.ImportFrom):
            base=n.module or ''
            if n.level:
                parts=pkg.split('.'); parts=parts[:len(parts)-(n.level-1)]
                base='.'.join(parts+([base] if base else []))
            s.add(base)
            for a in n.names: s.add(base+'.'+a.name)
    imp[m]={x for x in s if x in mods}
    # parent packages implicitly imported
    parts=m.split('.')
rev={}
for m,s in imp.items():
    for x in s: rev.setdefault(x,set()).add(m)
def closure(m):
    seen={m}; st=[m]
    while st:
        x=st.pop()
        for y in rev.get(x,()):
            if y not in seen: seen.add(y); st.append(y)
    return seen
idx={t:i for i,t in enumerate(data['tests'])}
for spec in sys.argv[1:]:
    setname,d,a,cid,rel=spec.split('|')
    cat=json.load(open(d+'/tests.json'))['tests']; ms={t['number']:round(t['seconds']*1000) for t in cat}
    num2id={t['number']:t['id'] for t in cat}
    f=set(json.load(open(a))['commits'][cid]['failing'])
    m=rel[:-3].replace('/','.')
    cl=closure(m)
    mask=0
    testfiles=set()
    for x in cl:
        r=mods[x]
        if r.startswith('tests/'): testfiles.add(r)
        else: mask|=h.file_mask(r)
    selids={data['tests'][i] for i in range(h.n) if mask>>i&1}
    sel={n for n,t in num2id.items() if t in selids or t.split('::')[0] in testfiles or t not in idx}
    missed=f-sel
    cost=S.schedule([(x,ms[x]) for x in sel])[0]; orc=S.schedule([(x,ms[x]) for x in f])[0]
    allc=S.schedule(list(ms.items()))[0]
    print(setname,cid,rel,'closure',len(cl),'sel',len(sel),'fail',len(f),'missed',len(missed),[num2id[x] for x in list(missed)[:4]],'cost',cost/1000,'oracle',orc/1000,'all',allc/1000, 'sqlglot' in cl)


import sys,json,math,os
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
from pathlib import Path
import selector as S
S.SRC=Path('/home/danny/blast-radius/work/sqlglot'); S.BUDGET_S=10**6
S.PROFILE=Path("/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro"+'/profile.pkl')
mapp=sys.argv[1]; outp=sys.argv[2]; specs=sys.argv[3:]
S.MAP=Path(mapp)
data=S.load_map(); h=S.Hunter(data,S.load_profile(data['tests']))
probe_cache={}
res={}
for spec in specs:
    name,d,a=spec.split('|')
    cat=json.load(open(d+'/tests.json'))['tests']; num_of={t['id']:t['number'] for t in cat}
    ms={t['number']:math.floor(t['seconds']*1000+0.5) for t in cat}
    idx_to_num=[num_of.get(t) for t in data['tests']]
    unmapped=[n for n in num_of.values() if n not in set(idx_to_num)]
    ans=json.load(open(a))['commits']; per={}
    for cid,an in sorted(ans.items()):
        text=open(d+'/commits/'+cid+'.patch','rb').read().decode('utf-8','surrogateescape')
        mask,probe=h.analyze(text)
        nums={idx_to_num[i] for i in range(h.n) if mask>>i&1}-{None}
        if mask: nums.update(unmapped)
        if probe is not None:
            key=repr(sorted(probe.items())) if isinstance(probe,dict) else repr(probe)
            if key not in probe_cache: probe_cache[key]=S.run_probe(probe,S.PROBE_TIMEOUT_S)
            if probe_cache[key]!='ok': nums=set(num_of.values())
        cost,_=S.schedule([(x,ms[x]) for x in nums]); miss=bool(set(an['failing'])-nums)
        per[cid]=dict(cost=cost/1000,miss=miss,sel=sorted(nums))
    res[name]=per
json.dump(res,open(outp,'w'))
for k,v in res.items(): print(k, round(sum(x['cost'] for x in v.values()),3), sum(x['miss'] for x in v.values()))


import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
import importlib.util
sets=json.loads(sys.argv[1]); D=Path(sys.argv[2])
an=json.loads((D/'analysis.json').read_text())
spec=importlib.util.spec_from_file_location('sel', Path(sys.argv[3])/'selector.py')
res={}
for name,(data,ans) in sets.items():
    cat=json.loads(Path(data,'tests.json').read_text())['tests']
    ms={t['number']:int(round(t['seconds']*1000)) for t in cat}
    idof={t['number']:t['id'] for t in cat}
    answers=json.loads(Path(ans).read_text())['commits']
    rows=an[name]
    tot_all=sum(ms.values())
    agg=dict(commits=len(rows), empty=0, nonempty=0, worker_s=0, makespan_s=0, bottleneck_single=0,
             bottleneck_single_cost=0, bottleneck_nonfailing=0, bottleneck_nonfailing_excess=0, kdist={}, catchall=0,
             sel_tests=[], sel_total_s=[], cost_minus_oracle=0, oracle=0, cur=0)
    bott={}
    for r in rows:
        agg['cur']+=r['cur']; agg['oracle']+=r['oracle']
        if not r['n']:
            agg['empty']+=1; continue
        agg['nonempty']+=1
        agg['kdist'][r['k']]=agg['kdist'].get(r['k'],0)+1
        agg['worker_s']+=2000*r['k']; agg['makespan_s']+=r['cur']-2000*r['k']
        agg['sel_tests'].append(r['n']); agg['sel_total_s'].append(r['total']/1000)
        if r['n']>=len(ms)*0.9: agg['catchall']+=1
        mk=r['cur']-2000*r['k']
        if mk==r['max']:
            agg['bottleneck_single']+=1; agg['bottleneck_single_cost']+=r['cur']
    # per commit: the longest selected test, is it failing?
    out=[l for l in (D/(name+'.out')).read_text().splitlines()]
    per=[]
    for line in out:
        o=json.loads(line); nums=[n for w in o['workers'] for n in w]
        if not nums: continue
        top=max(nums,key=lambda n:ms[n]); fail=set(answers[o['commit']]['failing'])
        r=next(x for x in rows if x['cid']==o['commit'])
        mk=r['cur']-2000*r['k']
        # cost if all non-failing tests with duration > some failing-max were dropped: = oracle-ish
        per.append(dict(cid=o['commit'],n=len(nums),k=r['k'],cur=r['cur'],oracle=r['oracle'],top=idof[top],top_ms=ms[top],
            top_failing=top in fail, nfail=len(fail), single_bottleneck= mk==r['max']))
        bott[idof[top]]=bott.get(idof[top],0)+1
    agg['top_bottleneck_tests']=sorted(bott.items(),key=lambda x:-x[1])[:8]
    agg['per']=per
    agg['catalog_total_s']=tot_all/1000; agg['catalog_n']=len(ms)
    agg['longest_tests']=sorted(((ms[n]/1000,idof[n]) for n in ms),reverse=True)[:8]
    res[name]=agg
json.dump(res,open(D/'decomp.json','w'))
# timing of selector.schedule
sel=importlib.util.module_from_spec(spec)
import os; os.environ['BR_DATA']=sets['holdout1'][0]
spec.loader.exec_module(sel)
cat=json.loads(Path(sets['holdout1'][0],'tests.json').read_text())['tests']
allt=[(t['number'],int(round(t['seconds']*1000))) for t in cat]
t0=time.process_time()
for _ in range(20): c,w=sel.schedule(allt)
print('catchall schedule', (time.process_time()-t0)/20*1000,'ms', c, len(w), len(allt))
t0=time.process_time()
n=0
for name in sets:
    cat=json.loads(Path(sets[name][0],'tests.json').read_text())['tests']; ms={t['number']:int(round(t['seconds']*1000)) for t in cat}
    for line in (D/(name+'.out')).read_text().splitlines():
        o=json.loads(line); nums=[x for w in o['workers'] for x in w]; sel.schedule([(x,ms[x]) for x in nums]); n+=1
print('all set schedules', (time.process_time()-t0)*1000,'ms for',n,'commits')

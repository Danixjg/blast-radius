
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
sets=json.loads(sys.argv[1]); D=Path(sys.argv[2])
import importlib.util
spec=importlib.util.spec_from_file_location('sel', Path(sys.argv[3])/'selector.py'); sel=importlib.util.module_from_spec(spec); spec.loader.exec_module(sel)
an=json.loads((D/'analysis.json').read_text())
cost=lambda nums,ms: sel.schedule([(n,ms[n]) for n in nums])[0]
for name,(data,ans) in sets.items():
    cat=json.loads(Path(data,'tests.json').read_text())['tests']
    ms={t['number']:int(round(t['seconds']*1000)) for t in cat}; idof={t['number']:t['id'] for t in cat}
    answers=json.loads(Path(ans).read_text())['commits']
    s_top1=s_long=s_k1=0; extra_workers_gain=0; brk=[]; nk=0; fewer_more=0
    long={n for n in ms if ms[n]>=3000}
    catchall_cost=0; catchall_oracle=0; dist={'0':0,'1':0,'2-10':0,'11-100':0,'101-1000':0,'>1000':0}
    for line in (D/(name+'.out')).read_text().splitlines():
        o=json.loads(line); S=[n for w in o['workers'] for n in w]; F=set(answers[o['commit']]['failing'])
        n=len(S); dist['0' if n==0 else '1' if n==1 else '2-10' if n<=10 else '11-100' if n<=100 else '101-1000' if n<=1000 else '>1000']+=1
        if not S: continue
        c=cost(S,ms); r=next(x for x in an[name] if x['cid']==o['commit'])
        if len(S)>=1500: catchall_cost+=c; catchall_oracle+=r['oracle']
        top=max(S,key=lambda x:ms[x])
        if top not in F:
            s_top1+=c-cost([x for x in S if x!=top],ms); brk.append((o['commit'],idof[top],ms[top]/1000,(c-cost([x for x in S if x!=top],ms))/1000))
        s_long+=c-cost([x for x in S if not (x in long and x not in F)],ms)
        extra_workers_gain+= r['curve'][0]-c
        if r['k']>1: nk+=1
    print(json.dumps(dict(set=name, drop_top_if_nonfailing_s=s_top1/1000, drop_all_nonfailing_ge3s=s_long/1000,
        gain_from_multiworker_vs_k1=extra_workers_gain/1000, commits_k_gt1=nk, catchall_cost=catchall_cost/1000, catchall_oracle=catchall_oracle/1000, dist=dist,
        biggest_nonfail_bottlenecks=sorted(brk,key=lambda x:-x[3])[:6])))

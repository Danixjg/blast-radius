
import json,sys
sys.path.insert(0, "/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
import selector as S
HEAVY=['test_max_nodes','test_execute_tpcds','test_generator_memory','test_execute_tpch','test_optimized_tpch']
res={}
for k,d,a in [(x.split('|')[0],x.split('|')[1],x.split('|')[2]) for x in sys.argv[1:]]:
  cat=json.load(open(d+'/tests.json'))['tests']; ms={t['number']:round(t['seconds']*1000) for t in cat}
  hv={t['number']:t['id'].split('::')[-1] for t in cat if t['id'].split('::')[-1] in HEAVY}
  ans=json.load(open(a))['commits']
  tot=0; per={h:0 for h in HEAVY}; dropall=0; allx=0; nall=0; ncommit_heavy={h:0 for h in HEAVY}; heavyfail={h:0 for h in HEAVY}
  for l in open('/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/research/external_methods/'+k+'.out'):
    l=json.loads(l); cid=l['commit']; sel=set(x for w in l['workers'] for x in w); f=set(ans[cid]['failing'])
    c=S.schedule([(x,ms[x]) for x in sel])[0]; tot+=c
    for n,h in hv.items():
      if n in f: heavyfail[h]+=1
      if n in sel and n not in f:
        ncommit_heavy[h]+=1
        per[h]+=c-S.schedule([(x,ms[x]) for x in sel-{n}])[0]
    s2=sel-(set(hv)-f); dropall+=c-S.schedule([(x,ms[x]) for x in s2])[0]
    if len(sel)==len(ms):
      nall+=1; allx+=c-S.schedule([(x,ms[x]) for x in f])[0]
  res[k]=dict(total=tot/1000,drop_each={h:round(per[h]/1000,1) for h in HEAVY},commits_selecting_unneeded=ncommit_heavy,heavy_ever_failing=heavyfail,drop_all_heavy=round(dropall/1000,1),n_all=nall,all_excess=round(allx/1000,1))
print(json.dumps(res,indent=1))

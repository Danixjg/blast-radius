
import json,sys,math
sys.path.insert(0, "/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
import selector as S
data_dir, ans_path, out_path, err_path = sys.argv[1:5]
cat=json.load(open(data_dir+'/tests.json'))['tests']
ms={t['number']:round(t['seconds']*1000) for t in cat}
fid={t['number']:t['id'] for t in cat}
ans=json.load(open(ans_path))['commits']
lines=[json.loads(l) for l in open(out_path) if l.strip()]
err=open(err_path).read().splitlines()
allc=S.schedule(list(ms.items()))[0]
rows=[]
for l in lines:
  cid=l['commit']; ws=l['workers']; sel=set(x for w in ws for x in w)
  cost=max((sum(ms[x] for x in w) for w in ws if w),default=0)+2000*len([w for w in ws if w])
  f=set(ans[cid]['failing']); miss=bool(f-sel)
  orc=S.schedule([(x,ms[x]) for x in f])[0]
  notes=[e for e in err if e.startswith(cid+' ') or e.startswith(cid+':')]
  # biggest unneeded tests
  extra=sorted(sel-f,key=lambda x:-ms[x])[:3]
  rows.append(dict(cid=cid,kind=ans[cid].get('kind',''),cost=cost/1000,oracle=orc/1000,excess=(cost-orc)/1000,nsel=len(sel),nfail=len(f),miss=miss,all=len(sel)==len(ms),extra=[(fid[x],ms[x]/1000) for x in extra],notes=[n[len(cid):][:140] for n in notes][:4]))
rows.sort(key=lambda r:-r['excess'])
print(json.dumps(dict(total=sum(r['cost'] for r in rows),oracle=sum(r['oracle'] for r in rows),allcost=allc/1000,rows=rows)))

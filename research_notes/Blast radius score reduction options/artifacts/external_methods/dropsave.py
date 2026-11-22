
import json,sys
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
import selector as S
for spec in sys.argv[1:]:
    k,d,a,cids,name=spec.split('|')
    cat=json.load(open(d+'/tests.json'))['tests']; ms={t['number']:round(t['seconds']*1000) for t in cat}
    num={t['id'].split('::')[-1]:t['number'] for t in cat}
    rows={json.loads(l)['commit']:json.loads(l) for l in open('/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/research/external_methods/'+k+'.B.out')}
    ans=json.load(open(a))['commits']; tot=0
    for cid in cids.split(','):
        sel={x for w in rows[cid]['workers'] for x in w}
        c=S.schedule([(x,ms[x]) for x in sel])[0]; c2=S.schedule([(x,ms[x]) for x in sel-{num[name]}])[0]
        fails=[t['id'] for t in cat if t['number'] in ans[cid]['failing']][:3]
        tot+=c-c2; print(k,cid,name,round(c/1000,2),'->',round(c2/1000,2),'failing:',fails)
    print(k,name,'TOTAL',tot/1000)

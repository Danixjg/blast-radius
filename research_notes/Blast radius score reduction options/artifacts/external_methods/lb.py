
import json,sys
sys.path.insert(0,"/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/main-ro")
import selector as S
for spec in sys.argv[1:]:
    k,d,a=spec.split('|')
    cat=json.load(open(d+'/tests.json'))['tests']; ms={t['number']:round(t['seconds']*1000) for t in cat}
    gap=0
    for l in open('/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/research/external_methods/'+k+'.B.out'):
        l=json.loads(l); sel=[x for w in l['workers'] for x in w]
        if not sel: continue
        c=S.schedule([(x,ms[x]) for x in sel])[0]
        tot=sum(ms[x] for x in sel); mx=max(ms[x] for x in sel)
        lb=min(max(mx,-(-tot//kk))+2000*kk for kk in range(1,9))
        gap+=c-lb
    print(k,'LPT minus lower bound (s):',gap/1000)

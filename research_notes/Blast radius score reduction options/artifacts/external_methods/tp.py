
import json,sys,re
for spec in sys.argv[1:]:
    k,d,a=spec.split('|')
    cat=json.load(open(d+'/tests.json'))['tests']; num={t['id'].split('::')[-1]:t['number'] for t in cat}
    tp=num['test_execute_tpcds']; mn=num['test_max_nodes']; gm=num['test_generator_memory']
    ans=json.load(open(a))['commits']
    for l in open('/tmp/claude-1000/-home-danny-blast-radius/f9459cfe-fac7-4d0e-80c3-4adcd1755994/scratchpad/research/external_methods/'+k+'.B.out'):
        l=json.loads(l); sel={x for w in l['workers'] for x in w}; f=set(ans[l['commit']]['failing'])
        if len(sel)==len(cat): continue
        tags=[n for n,x in (('tpcds',tp),('maxnodes',mn),('genmem',gm)) if x in sel and x not in f]
        if tags:
            p=open(d+'/commits/'+l['commit']+'.patch').read()
            files=re.findall(r'^\+\+\+ b/(\S+)',p,re.M)
            print(k,l['commit'],tags,files,'nfail',len(f))

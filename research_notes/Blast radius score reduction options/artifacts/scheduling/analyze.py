
import json, sys, math, heapq, itertools, time
from pathlib import Path
W=2000
sets=json.loads(sys.argv[1])
def lpt(d,k):
    loads=[0]*k; h=[(0,i) for i in range(k)]
    for x in sorted(d,reverse=True):
        l,i=heapq.heappop(h); heapq.heappush(h,(l+x,i))
    return max(l for l,_ in h)
def cur(d,k):  # selector: same as LPT with ties by index
    loads=[0]*k
    for x in sorted(d,reverse=True):
        i=loads.index(min(loads)); loads[i]+=x
    return max(loads)
def kk(d,k):  # Karmarkar-Karp multiway largest differencing
    h=[]
    for j,x in enumerate(d):
        t=[x]+[0]*(k-1); heapq.heappush(h,(-x,j,t))
    cnt=len(d)
    while len(h)>1:
        _,_,a=heapq.heappop(h); _,_,b=heapq.heappop(h)
        a=sorted(a,reverse=True); b=sorted(b)
        c=sorted([a[i]+b[i] for i in range(k)],reverse=True)
        cnt+=1; heapq.heappush(h,(-(c[0]-c[-1]),cnt,c))
    return max(h[0][2])
def ffd_fits(d,k,cap):
    bins=[]
    for x in d:
        for i in range(len(bins)):
            if bins[i]+x<=cap: bins[i]+=x; break
        else:
            if len(bins)==k: return False
            bins.append(x)
    return True
def multifit(d,k,it=12):
    d=sorted(d,reverse=True); s=sum(d)
    lo=max(d[0],-(-s//k)); hi=max(d[0],2*-(-s//k))
    for _ in range(it):
        if hi-lo<=0: break
        m=(lo+hi)//2
        if ffd_fits(d,k,m): hi=m
        else: lo=m+1
    return hi if ffd_fits(d,k,hi) else lpt(d,k)
def exact(d,k,ub,tl=2.0):
    # branch and bound: min makespan, given an upper bound ub (makespan); returns (best, proven)
    d=sorted(d,reverse=True); s=sum(d)
    lb=max(d[0],-(-s//k))
    if k>1 and len(d)>k: lb=max(lb,d[k-1]+d[k])
    if ub<=lb: return ub,True
    best=[ub]; t0=time.time(); timed=[False]
    loads=[0]*k
    suffix=[0]*(len(d)+1)
    for i in range(len(d)-1,-1,-1): suffix[i]=suffix[i+1]+d[i]
    def rec(i):
        if best[0]<=lb: return
        if time.time()-t0>tl: timed[0]=True; return
        if i==len(d):
            m=max(loads)
            if m<best[0]: best[0]=m
            return
        # bound: remaining must fit under best-1
        cap=best[0]-1
        free=sum(cap-l for l in loads if l<cap)
        if free<suffix[i]: return
        seen=set()
        for j in sorted(range(k),key=lambda j:loads[j]):
            if loads[j] in seen: continue
            seen.add(loads[j])
            if loads[j]+d[i]>cap: continue
            loads[j]+=d[i]; rec(i+1); loads[j]-=d[i]
    sys.setrecursionlimit(100000)
    rec(0)
    return best[0], (not timed[0]) or best[0]<=lb
out={}
for name,(data,ans) in sets.items():
    cat=json.loads(Path(data,'tests.json').read_text())['tests']
    ms={t['number']:int(round(t['seconds']*1000)) for t in cat}
    answers=json.loads(Path(ans).read_text())['commits']
    rows=[]
    for line in Path(sys.argv[2],name+'.out').read_text().splitlines():
        o=json.loads(line); cid=o['commit']; ws=o['workers']
        nums=[n for w in ws for n in w]; d=[ms[n] for n in nums]
        c0=(max(sum(ms[n] for n in w) for w in ws)+W*len(ws)) if ws else 0
        r={'cid':cid,'n':len(nums),'total':sum(d),'max':max(d) if d else 0,'k':len(ws),'cur':c0,
           'miss': bool(set(answers[cid]['failing'])-set(nums))}
        fail=[ms[n] for n in answers[cid]['failing']]
        r['oracle']=0
        if fail:
            r['oracle']=min(max(fail[0] if False else 0,0)+0 for _ in [0]) # placeholder
        if d:
            best={'lb':None,'lpt':None,'kk':None,'mf':None,'opt':None}
            lbv=min(max(max(d),-(-sum(d)//k))+W*k for k in range(1,min(8,len(d))+1))
            res={}
            for alg,f in (('cur',cur),('lpt',lpt),('kk',kk),('mf',multifit)):
                vals=[(f(d,k)+W*k,k) for k in range(1,min(8,len(d))+1)]
                res[alg]=min(vals)
            # exact
            ov=[]; proven=True
            for k in range(1,min(8,len(d))+1):
                lbk=max(max(d),-(-sum(d)//k))+W*k
                ubk=min(res['kk'][0],res['mf'][0],res['lpt'][0],res['cur'][0])
                if lbk>=ubk: continue
                ub=min(cur(d,k),kk(d,k),multifit(d,k),lpt(d,k))
                v,p=exact(d,k,ub,tl=1.0 if len(d)<400 else 0.2); proven&=p
                ov.append((v+W*k,k))
            ov.append(min(res.values()))
            r.update(lb=lbv,lpt=res['lpt'][0],kk=res['kk'][0],mf=res['mf'][0],opt=min(ov)[0],optk=min(ov)[1],proven=proven,
                     curk_chk=res['cur'][0])
            # cost per k with current heuristic
            r['curve']=[cur(d,k)+W*k for k in range(1,min(8,len(d))+1)]
        # oracle: only failing tests, optimal
        if fail:
            r['oracle']=min(exact(fail,k,lpt(fail,k))[0]+W*k for k in range(1,min(8,len(fail))+1))
        rows.append(r)
    out[name]=rows
json.dump(out,open(Path(sys.argv[2],'analysis.json'),'w'))
for name,rows in out.items():
    S=lambda key: sum(r.get(key) or 0 for r in rows)/1000
    print(name, 'n',len(rows),'cur',S('cur'),'opt',S('opt'),'lb',S('lb'),'lpt',S('lpt'),'kk',S('kk'),'mf',S('mf'),'oracle',S('oracle'),
          'unproven',sum(1 for r in rows if r.get('n') and not r['proven']),'misses',sum(r['miss'] for r in rows))

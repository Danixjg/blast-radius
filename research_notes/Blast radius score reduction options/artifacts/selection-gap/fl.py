import json,sys
for n,p in zip(sys.argv[1::2], sys.argv[2::2]):
    a=json.load(open(p))['commits']
    for t in (1224,1490,1106):
        c=[(k,len(v['failing'])) for k,v in a.items() if t in v['failing']]
        print(n,t,len(c),c[:8])

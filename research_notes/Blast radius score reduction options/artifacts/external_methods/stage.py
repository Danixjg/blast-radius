
import sys, time, hashlib, json, os
src=sys.argv[1]; sys.path.insert(0, src)
t0=time.process_time()
from sqlglot import parse_one
from sqlglot.optimizer import optimize
from sqlglot.planner import Plan
from tests.helpers import load_sql_fixture_pairs, TPCDS_SCHEMA
out=[]
for meta, sql, _ in load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"):
    if not meta.get("execute"):
        out.append('noexec'); continue
    try:
        e=optimize(parse_one(sql), TPCDS_SCHEMA, leave_tables_isolated=True)
        types=[(type(n).__name__, n.type.sql() if getattr(n,'type',None) is not None else None) for n in e.walk()]
        p=Plan(e)
        steps=sorted((type(s).__name__, s.name, repr(sorted(map(str,getattr(s,'projections',[]) or [])))) for s in p.dag)
        fp=hashlib.sha256((e.sql()+repr(e)+repr(types)+repr(steps)).encode()).hexdigest()
    except Exception as ex:
        fp='ERR '+type(ex).__name__+str(ex)[:80]
    out.append(fp)
print(json.dumps({'cpu':time.process_time()-t0,'n':len(out),'fps':out}))

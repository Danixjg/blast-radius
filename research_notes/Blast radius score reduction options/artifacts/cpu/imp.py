
import time, sys, importlib, os, json
sys.path.insert(0, '/app/src')
import gc; gc.disable()
sys.dont_write_bytecode = True
t0 = time.process_time()
import sqlglot
t1 = time.process_time()
mods = []
root = '/app/src/sqlglot'
for d, dn, fn in os.walk(root):
    dn[:] = sorted(x for x in dn if x != '__pycache__')
    for f in sorted(fn):
        if f.endswith('.py'):
            rel = os.path.relpath(os.path.join(d, f), '/app/src')[:-3].replace('/', '.')
            if rel.endswith('.__init__'): rel = rel[:-9]
            if 'sqlglotc' in rel or rel.endswith('__main__'): continue
            mods.append(rel)
which = sys.argv[1]
if which == 'all':
    for m in mods:
        try: importlib.import_module(m)
        except BaseException: pass
elif which != 'none':
    importlib.import_module(which)
t2 = time.process_time()
print(json.dumps({"which": which, "import_sqlglot": round(t1-t0,4), "rest": round(t2-t1,4), "nmods": len(mods), "loaded_after_sqlglot": None}))

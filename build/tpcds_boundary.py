"""pytest plugin: which sqlglot functions run outside optimize() during one test.

Loaded by build/record_tpcds.py (-p tpcds_boundary, build/ on PYTHONPATH). It profiles every thread
of the pytest process and of every spawned ProcessPoolExecutor worker from the moment this module
is imported: in a worker that is before the pool's initializer arguments are unpickled, so the
worker's sqlglot imports are seen too. For each sqlglot code object it records whether it was
entered (a call or a generator resume) with no optimize() frame on that thread's stack. Each
process writes $TPCDS_OUT/proc-<pid>.json; record_tpcds.py merges them.
"""
import atexit
import json
import os
import sys
import threading

SRC = os.environ.get("TPCDS_SRC")
OUT = os.environ.get("TPCDS_OUT")
OPTIMIZE = ("sqlglot/optimizer/optimizer.py", "optimize")

_outside, _inside = set(), set()
_local = threading.local()
_keys = {}   # code object -> (rel, firstlineno, name) or None (not sqlglot)


def _key(co):
    try:
        return _keys[co]
    except KeyError:
        fn = os.path.abspath(co.co_filename)
        k = None
        if fn.startswith(_PKG):
            k = (os.path.relpath(fn, SRC).replace(os.sep, "/"), co.co_firstlineno, co.co_name)
        _keys[co] = k
        return k


def _prof(frame, event, arg):
    try:
        _record(frame, event)
    except Exception:   # interpreter shutdown can clear this module's globals under the profiler
        pass


def _record(frame, event):
    if event == "call":
        k = _key(frame.f_code)
        if k is None:
            return
        depth = getattr(_local, "depth", 0)
        if (k[0], k[2]) == OPTIMIZE:
            _local.depth = depth + 1
            _inside.add(k)
        elif depth:
            _inside.add(k)
        else:
            _outside.add(k)
    elif event == "return" and (_key(frame.f_code) or ("",))[0::2] == OPTIMIZE:
        _local.depth = getattr(_local, "depth", 1) - 1


def _dump():
    path = os.path.join(OUT, "proc-%d.json" % os.getpid())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"outside": sorted(_outside), "inside": sorted(_inside)}, f)
    os.replace(tmp, path)


def _child_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        _dump()


def _child_init(initializer, initargs):
    if initializer is not None:
        initializer(*initargs)
    _dump()


def _patch_pools():
    import concurrent.futures as cf
    import concurrent.futures.process as cfp

    base = cfp.ProcessPoolExecutor

    class TracedPool(base):
        def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
            super().__init__(max_workers, mp_context, _child_init, (initializer, initargs), **kw)

        def submit(self, fn, /, *args, **kwargs):
            return super().submit(_child_call, fn, *args, **kwargs)

    cfp.ProcessPoolExecutor = TracedPool
    cf.ProcessPoolExecutor = TracedPool


if SRC and OUT:
    _PKG = os.path.join(os.path.abspath(SRC), "sqlglot") + os.sep
    _patch_pools()
    threading.setprofile_all_threads(_prof)
    sys.setprofile(_prof)
    atexit.register(_dump)


def pytest_unconfigure(config):
    if SRC and OUT:
        _dump()

"""pytest plugin: per-test line coverage, fixture opens and class instantiations.

Build-time only. Writes one pickle per process into $COV_OUT.
"""
import functools
import os
import pickle
import sys

ROOT = os.path.realpath(os.environ.get("COV_ROOT", os.getcwd())) + os.sep
OUT = os.environ["COV_OUT"]
REVERSE = os.environ.get("COV_REVERSE") == "1"
mon = sys.monitoring
TOOL = mon.COVERAGE_ID

_files = {}          # filename -> index
_cur_lines = None    # set of (fidx, line) for the running test
_cur_opens = None
_cur_types = None
_imp_lines = set()
_mod_lines = {}       # test module nodeid -> lines executed while collecting it
_mod_opens = {}
_in_collect = None
_imp_opens = set()
_results = {}        # nodeid -> (lines, opens, classes)
_mro_cache = {}
_cur_test = None
_cur_setup = None
_child_ready = False


def _fidx(fn):
    i = _files.get(fn)
    if i is None:
        i = _files[fn] = len(_files)
    return i


def _line_cb(code, line):
    fn = code.co_filename
    if ROOT and fn.startswith(ROOT):
        s = _cur_lines if _cur_lines is not None else _imp_lines
        if s is not None:
            s.add((_fidx(fn), line))
    return mon.DISABLE


def _audit(event, args):
    if event == "open":
        p = args[0]
        if isinstance(p, (str, bytes, os.PathLike)):
            try:
                p = os.path.realpath(os.fsdecode(p))
            except Exception:
                return
            if p.startswith(ROOT):
                (_cur_opens if _cur_opens is not None else _imp_opens).add(p[len(ROOT):])


def _wrap_init(cls):
    orig = cls.__init__

    @functools.wraps(orig)
    def __init__(self, *a, **k):
        s = _cur_types
        if s is not None:
            s.add(type(self))
        orig(self, *a, **k)

    cls.__init__ = __init__


def _mro_names(tp):
    r = _mro_cache.get(tp)
    if r is None:
        r = _mro_cache[tp] = frozenset(
            f"{c.__module__}:{c.__qualname__}" for c in tp.__mro__ if (getattr(c, "__module__", None) or "").startswith("sqlglot")
        )
    return r


def pytest_configure(config):
    global _imp_lines
    mon.use_tool_id(TOOL, "covmap")
    mon.register_callback(TOOL, mon.events.LINE, _line_cb)
    mon.set_events(TOOL, mon.events.LINE)
    sys.addaudithook(_audit)
    sys.path.insert(0, os.getcwd())
    _patch_pools()
    _prewarm()
    # from here on, lines outside a test or a module collection are not recorded
    _imp_lines = _imp_lines
    global _IMP_DONE
    _IMP_DONE = set(_imp_lines)


def pytest_make_collect_report(collector):
    global _cur_lines, _cur_opens
    import pytest
    if not isinstance(collector, pytest.Module):
        yield
        return
    _cur_lines, _cur_opens = set(), set()
    mon.restart_events()
    try:
        yield
    finally:
        _mod_lines[collector.nodeid] = _cur_lines
        _mod_opens[collector.nodeid] = _cur_opens
        _cur_lines = _cur_opens = None


pytest_make_collect_report.hookwrapper = True


# ---- child processes (spawned ProcessPoolExecutor workers)


def _child_setup():
    global _child_ready
    if _child_ready:
        return
    _child_ready = True
    mon.use_tool_id(TOOL, "covmap-child")
    mon.register_callback(TOOL, mon.events.LINE, _line_cb)
    mon.set_events(TOOL, mon.events.LINE)
    sys.addaudithook(_audit)
    try:
        from sqlglot.dialects.dialect import Dialect
        from sqlglot.expressions.core import Expr
        from sqlglot.generator import Generator
        from sqlglot.parser import Parser
        from sqlglot.tokens import Tokenizer

        for c in (Expr, Generator, Parser, Tokenizer, Dialect):
            if not getattr(c.__init__, "_cov_wrapped", False):
                _wrap_init(c)
                c.__init__._cov_wrapped = True
    except Exception as e:  # noqa
        print("child wrap failed", e, file=sys.stderr)


def _child_record(test_id, fn, args, kwargs):
    global _cur_lines, _cur_opens, _cur_types
    _child_setup()
    _cur_lines, _cur_opens, _cur_types = set(), set(), set()
    mon.restart_events()
    try:
        result = fn(*args, **kwargs)
        try:
            pickle.dumps(result)  # the pool pickles results after we return; trace that too
        except Exception:
            pass
        return result
    finally:
        lines, opens, types = _cur_lines, _cur_opens, _cur_types
        _cur_lines = _cur_opens = _cur_types = None
        files = {i: fn_[len(ROOT):] for fn_, i in _files.items()}
        classes = set()
        for tp in types:
            classes |= _mro_names(tp)
        rec = (test_id, {(files[f], l) for f, l in lines}, opens, classes)
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"child-{os.getpid()}.pkl"), "ab") as f:
            pickle.dump(rec, f)


def _traced_call(test_id, fn, *args, **kwargs):
    return _child_record(test_id, fn, args, kwargs)


def _traced_init(test_id, fn, *args):
    if fn is not None:
        return _child_record(test_id, fn, args, {})


def _patch_pools():
    import concurrent.futures.process as cfp

    P = cfp.ProcessPoolExecutor
    orig_submit, orig_init = P.submit, P.__init__

    def submit(self, fn, /, *args, **kwargs):
        return orig_submit(self, _traced_call, _cur_test, fn, *args, **kwargs)

    def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
        if initializer is not None:
            initargs = (_cur_test, initializer) + tuple(initargs)
            initializer = _traced_init
        orig_init(self, max_workers, mp_context, initializer, initargs, **kw)

    P.submit = submit
    P.__init__ = __init__


_IMP_CLASSES = set()


def _prewarm():
    global _cur_types
    import importlib
    import pkgutil

    import sqlglot
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.expressions.core import Expr
    from sqlglot.generator import Generator
    from sqlglot.parser import Parser
    from sqlglot.tokens import Tokenizer

    for c in (Expr, Generator, Parser, Tokenizer, Dialect):
        _wrap_init(c)
    _cur_types = set()

    for m in pkgutil.walk_packages(sqlglot.__path__, "sqlglot."):
        if "sqlglotc" in m.name or m.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(m.name)
        except Exception as e:  # noqa
            print("prewarm import failed", m.name, e, file=sys.stderr)
    for tp in _cur_types:
        _IMP_CLASSES.update(_mro_names(tp))
    _cur_types = None


def pytest_collection_modifyitems(session, config, items):
    if REVERSE:
        items.reverse()


def pytest_runtest_protocol(item, nextitem):
    global _cur_lines, _cur_opens, _cur_types, _cur_test, _cur_setup
    _cur_lines, _cur_opens, _cur_types = set(), set(), set()
    _cur_setup = None
    _cur_test = item.nodeid
    mon.restart_events()
    try:
        yield
    finally:
        lines, opens, types = _cur_lines, _cur_opens, _cur_types
        _cur_lines = _cur_opens = _cur_types = None
        classes = set()
        for tp in types:
            classes |= _mro_names(tp)
        mod = item.nodeid.split("::")[0]
        lines |= _mod_lines.get(mod, set())
        opens |= _mod_opens.get(mod, set())
        sl, so, st = _cur_setup or (set(), set(), set())
        scl = set()
        for tp in st:
            scl |= _mro_names(tp)
        _results[item.nodeid] = (lines, opens, classes, (sl, so, scl))


pytest_runtest_protocol.hookwrapper = True


def pytest_runtest_setup(item):
    global _cur_setup
    try:
        yield
    finally:
        _cur_setup = (set(_cur_lines or ()), set(_cur_opens or ()), set(_cur_types or ()))


pytest_runtest_setup.hookwrapper = True


def pytest_unconfigure(config):
    mon.set_events(TOOL, 0)
    mon.free_tool_id(TOOL)
    wid = os.environ.get("PYTEST_XDIST_WORKER", "main")
    os.makedirs(OUT, exist_ok=True)
    files = [None] * len(_files)
    for fn, i in _files.items():
        files[i] = fn[len(ROOT):]
    with open(os.path.join(OUT, f"cov-{wid}-{'r' if REVERSE else 'f'}.pkl"), "wb") as f:
        pickle.dump({"files": files, "imp_classes": _IMP_CLASSES, "imp_lines": _IMP_DONE, "imp_opens": _imp_opens, "results": _results}, f)

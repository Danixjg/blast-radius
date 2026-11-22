"""Build-time runtime profile of the pinned sqlglot: literal-dictionary reads and is/== agreement.

Loaded by covplugin when COV_PROFILE=1 (and by spawned pool workers, which import covplugin
before any sqlglot module). sqlglot modules are compiled through an import hook that rewrites:

  * every dictionary display assigned directly in a module or class body,
        NAME = {k1: v1, **Base.NAME, k2: v2}
    into
        NAME = __br_kd_end__(__br_kd_begin__(owner), {k1: v1, **Base.NAME, k2: v2},
                             (("k", k1, 0), ("s", Base.NAME), ("k", k2, 2)))
    which returns a KeyDict (a dict subclass) that records, per test, which keys' VALUES are
    read. Membership (`in`, keys(), iteration over keys, len) is not a value read. Reading
    every value (items(), values(), copy(), ==, repr, pickling, {**d}, dict(d), ...) records the
    whole dictionary. The trailing tuple re-evaluates the (side-effect free) keys and spread
    sources, so each key of the result knows which literal entry or which source dictionary
    supplied its value (provenance through subclasses and derived dictionaries).

  * every single comparison `a is b`, `a is not b`, `a == b`, `a != b` where one side is None,
    True, False or an ALL_CAPS name/attribute (enum-member style), into
        __br_cmp__(site, op, a, b)
    which returns the original result and records pristine operand facts only: whether the
    operands are identical, what `a == b` evaluates to when that runs side-effect-free builtin
    code, and whether `a != b` would too. The selector decides each incoming edit from these facts
    at run time; the profile holds no edit outcomes.

Provenance is replayed with real dict semantics (equal keys such as 1 and True collide and keep
the first key object), and a value taken from a spread is credited to the source dictionary under
the source's own retained key. Exact-type tests against dict in sqlglot (a KeyDict would take the
other branch) are recorded; mkprofile then trusts no entry. Paths a dict subclass changes outside
sqlglot are caught empirically by comparing a plain and an instrumented run of the same order.

Anything that cannot be followed is recorded as such and makes the affected entries untrusted:
values read while a module executes (class creation, metaclasses: they end up in derived
structures), mutation of a tracked dictionary, keys that have no stable identity, spreads of
something other than a plain name/attribute, and modules that were imported without the hook.
"""
import ast
import builtins
import copy
import enum
import importlib.abc
import importlib.machinery
import os
import sys

ROOT = None          # repository root with a trailing separator
STATE = None


def _rel(path):
    p = os.path.realpath(path)
    if ROOT and p.startswith(ROOT):
        return p[len(ROOT):].replace(os.sep, "/")
    return None


class Rec:
    """What one test (or one module collection, or one class setup) observed.

    reads    {(owner, keyid)}  values read from tracked dictionaries
    allread  {owner}           every value of a tracked dictionary read (items(), copy, ...)
    cmp      {(site, fact)}    pristine operand facts at instrumented comparisons (see cmp())
    mutated  {owner}           tracked dictionaries this context mutated
    """
    __slots__ = ("reads", "allread", "cmp", "mutated")

    def __init__(self):
        self.reads, self.allread, self.cmp, self.mutated = set(), set(), set(), set()

    def merge(self, o):
        self.reads |= o.reads
        self.allread |= o.allread
        self.cmp |= o.cmp
        self.mutated |= o.mutated

    def copy(self):
        r = Rec()
        r.merge(self)
        return r

    def dump(self):
        return (self.reads, self.allread, self.cmp, self.mutated)


class State:
    def __init__(self):
        self.cur = None             # Rec of the running test / collection, None outside
        self.importing = 0          # > 0 while a sqlglot module body executes
        self.frames = []            # derivation frames of dictionary displays being built
        self.imp = Rec()            # observed while importing or outside any test
        self.dynamic = set()        # owners mutated after construction
        self.prov = {}              # owner -> {keyid: set(winners)}
        self.untrusted = set()      # owners whose provenance could not be established
        self.owners = {}            # owner -> [ast.dump(key) or None per entry]
        self.sites = set()          # instrumented comparison sites
        self.uninstrumented = set()  # sqlglot modules loaded without the hook (pool workers)
        self.exact_checks = set()   # (file, line) of exact-type tests a KeyDict would fail
        self.collisions = set()     # (owner, retained keyid, colliding keyid) seen while building


def _target():
    s = STATE
    if s.importing or s.cur is None:
        return s.imp
    return s.cur


# ---------------------------------------------------------------- stable key identity

_KID = {}


def keyid(k):
    try:
        ck = (type(k), k)
        r = _KID.get(ck)
        if r is not None:
            return r
    except TypeError:
        ck = None
    t = type(k)
    if t in (str, int, float, bytes, bool, type(None)):
        r = (t.__name__, k)
    elif isinstance(k, enum.Enum):
        r = ("enum", t.__module__, t.__qualname__, k.name)
    elif isinstance(k, type):
        r = ("cls", k.__module__, k.__qualname__)
    elif t is tuple:
        sub = tuple(keyid(x) for x in k)
        r = None if any(x is None for x in sub) else ("tuple", sub)
    elif callable(k) and getattr(k, "__qualname__", None) and getattr(k, "__module__", None):
        r = ("fn", k.__module__, k.__qualname__)
    else:
        r = None
    if ck is not None and r is not None:
        _KID[ck] = r
    return r


# ---------------------------------------------------------------- tracked dictionaries


class KeyDict(dict):
    __slots__ = ("_br_owner", "_br_keys")

    # ---- value reads
    def _read(self, k):
        owner = getattr(self, "_br_owner", None)
        if owner is None:
            return
        fr = STATE.frames
        if fr:
            pend = fr[-1]["pending"].get(id(self))
            if pend and pend[-1] == k:   # one element of a {**d} spread into a tracked display
                pend.pop()
                return
        try:
            stored = self._br_keys[k]
        except (KeyError, TypeError, AttributeError):
            stored = k
        _target().reads.add((owner, keyid(stored)))

    def _all(self):
        owner = getattr(self, "_br_owner", None)
        if owner is not None:
            _target().allread.add(owner)

    def _mutate(self):
        owner = getattr(self, "_br_owner", None)
        if owner is not None:
            STATE.dynamic.add(owner)
            _target().mutated.add(owner)
            self._all()

    def __getitem__(self, k):
        v = dict.__getitem__(self, k)
        self._read(k)
        return v

    def get(self, k, default=None):
        if dict.__contains__(self, k):
            self._read(k)
            return dict.__getitem__(self, k)
        return default

    def keys(self):
        fr = STATE.frames
        if getattr(self, "_br_owner", None) is not None:
            if fr:
                # dict_merge / {**d} building a tracked display: keys() then d[key] for each key
                fr[-1]["pending"][id(self)] = list(reversed(list(dict.keys(self))))
                fr[-1]["consumed"].append(self)
            else:
                # anywhere else keys() is how dict(d), {**d}, d2.update(d) and f(**d) copy (sqlglot
                # never calls .keys() on these tables at run time): a copy whose consumers we
                # cannot follow, so it counts as reading every value
                self._all()
        return dict.keys(self)

    def __iter__(self):
        return dict.__iter__(self)

    def values(self):
        self._all()
        return dict.values(self)

    def items(self):
        self._all()
        return dict.items(self)

    def copy(self):
        self._all()
        return dict.copy(self)

    def __eq__(self, other):
        self._all()
        return dict.__eq__(self, other)

    def __ne__(self, other):
        self._all()
        return dict.__ne__(self, other)

    __hash__ = None

    def __repr__(self):
        self._all()
        return dict.__repr__(self)

    def __or__(self, other):
        self._all()
        return dict.__or__(self, other)

    def __ror__(self, other):
        self._all()
        return dict.__ror__(self, other)

    def __reduce_ex__(self, proto):
        self._all()
        return (dict, (dict(dict.items(self)),))

    def __copy__(self):
        self._all()
        return dict.copy(self)

    def __deepcopy__(self, memo):
        self._all()
        return copy.deepcopy(dict(dict.items(self)), memo)

    # ---- mutation
    def __setitem__(self, k, v):
        self._mutate()
        dict.__setitem__(self, k, v)

    def __delitem__(self, k):
        self._mutate()
        dict.__delitem__(self, k)

    def pop(self, *a):
        self._mutate()
        return dict.pop(self, *a)

    def popitem(self):
        self._mutate()
        return dict.popitem(self)

    def clear(self):
        self._mutate()
        dict.clear(self)

    def update(self, *a, **k):
        self._mutate()
        dict.update(self, *a, **k)

    def setdefault(self, k, default=None):
        self._mutate()
        return dict.setdefault(self, k, default)

    def __ior__(self, other):
        self._mutate()
        return dict.__ior__(self, other)


def kd_begin(owner):
    STATE.frames.append({"owner": owner, "pending": {}, "consumed": []})
    return owner


def kd_end(token, result, entries):
    s = STATE
    frame = None
    while s.frames:
        f = s.frames.pop()
        if f["owner"] == token:
            frame = f
            break
    owner = token
    simple_sources = []
    # Replay the display's insertions into a real dict, keyed by the key OBJECTS, so equal keys
    # collide exactly as they do in the result ({1: "a", True: "b"} keeps key 1 with value "b"):
    # the winner of each retained key is the last entry that set its value. A value that came
    # from a spread source is credited to that source under the source's own retained key.
    win = {}
    untrusted = False

    def note_collision(k):
        # an equal key with another identity is already in place (1 vs True, a str-enum member vs
        # its string): the retained key object stays, only the value changes
        for old in win:
            if old == k and keyid(old) != keyid(k):
                s.collisions.add((owner, keyid(old), keyid(k)))
                break

    for ent in entries:
        tag = ent[0]
        try:
            if tag in ("k", "s") and win:
                for k in ((ent[1],) if tag == "k" else (dict.keys(ent[1]) if isinstance(ent[1], dict) else ())):
                    if k in win:
                        note_collision(k)
            if tag == "k":
                win[ent[1]] = ("lit", ent[2])
            elif tag == "s":
                src = ent[1]
                simple_sources.append(src)
                if isinstance(src, KeyDict) and getattr(src, "_br_owner", None) is not None:
                    for k in dict.keys(src):
                        win[k] = ("from", src._br_owner, keyid(k))
                elif isinstance(src, dict):
                    for k in dict.keys(src):
                        win[k] = ("opaque",)
                else:
                    untrusted = True
            else:   # an unknown key or a spread we could not re-evaluate
                untrusted = True
        except TypeError:   # unhashable key: the display itself would have failed
            untrusted = True
    winners = {}
    for k, w in win.items():
        kid = keyid(k)
        if kid is None or (w[0] == "from" and w[2] is None):
            untrusted = True
            continue
        winners[kid] = w
    if set(map(keyid, dict.keys(result))) != set(winners) and not untrusted:
        untrusted = True   # the replay does not reproduce the result: do not guess
    if frame is not None:
        # tracked dictionaries consumed while building this display other than through its
        # simple spreads (one keys() pass per spread) flowed somewhere we cannot follow: their
        # values count as read at import time
        for kd in frame["consumed"]:
            uses = sum(1 for c in frame["consumed"] if c is kd)
            spreads = sum(1 for src in simple_sources if src is kd)
            if uses > spreads:
                o = getattr(kd, "_br_owner", None)
                if o is not None:
                    s.imp.allread.add(o)
    if untrusted:
        s.untrusted.add(owner)
    p = s.prov.setdefault(owner, {})
    for kid, w in winners.items():
        p.setdefault(kid, set()).add(w)
    kd = KeyDict(result)
    kd._br_owner = owner
    kd._br_keys = {k: k for k in dict.keys(result)}
    return kd


# ---------------------------------------------------------------- comparisons

# Equality implementations that run no Python code, have no side effects and return a bool
# (or NotImplemented, after which the reflected builtin is tried). object.__ne__ is NOT enough on
# its own: it calls type(a).__eq__ and inverts it, so != is only side-effect free when __eq__ is.
_PURE_EQ = {object.__eq__, str.__eq__, int.__eq__, float.__eq__, bytes.__eq__, type.__eq__, type(None).__eq__}
_PURE_NE = {object.__ne__, str.__ne__, int.__ne__, float.__ne__, bytes.__ne__, type.__ne__, type(None).__ne__}


def _slot(tp, name):
    try:
        return getattr(tp, name)
    except Exception:
        return None


def eq_pure(a, b):
    """a == b runs only builtin, side-effect-free code (forward and reflected)."""
    return _slot(type(a), "__eq__") in _PURE_EQ and _slot(type(b), "__eq__") in _PURE_EQ


def ne_pure(a, b):
    """a != b runs only builtin, side-effect-free code: __ne__ AND __eq__ of both types."""
    return eq_pure(a, b) and _slot(type(a), "__ne__") in _PURE_NE and _slot(type(b), "__ne__") in _PURE_NE


def _impl(tp, name):
    """Qualified name of the method Python calls for `name` on instances of tp, or None."""
    f = _slot(tp, name)
    if f is None:
        return None
    owner = getattr(f, "__objclass__", None)
    mod = getattr(f, "__module__", None) or getattr(owner, "__module__", None)
    qual = getattr(f, "__qualname__", None)
    return f"{mod}.{qual}" if mod and qual else None


def cmp(site, op, a, b):
    """op 0: is, 1: is not, 2: ==, 3: !=. Returns exactly what the original comparison returns.

    Records one pristine operand fact per evaluation, never an edit outcome:
        (identical, eq, ne_ok)            or, against None with a non-builtin equality,
        (identical, eq, ne_ok, impl)
    identical  a is b
    eq         the bool a == b evaluates to, when that evaluation runs only side-effect-free
               builtin code (computed here only in that case, so the recorder adds no side
               effects); None otherwise
    ne_ok      a != b would also run only side-effect-free builtin code (then a != b is not eq)
    impl       when exactly one operand is None and eq is None: the qualified names of the
               __eq__ and __ne__ the other operand's type supplies (nothing is called to get
               them), so the selector can decide from that code what `x == None` would do
    The selector decides at run time, from these facts, whether a given operator edit can change
    the result or the side effects for each test."""
    if op == 0:
        r = a is b
    elif op == 1:
        r = a is not b
    elif op == 2:
        r = a == b
    else:
        r = a != b
    identical = a is b
    pure = eq_pure(a, b)
    ne_ok = pure and ne_pure(a, b)
    if op == 2:
        eq = r if (pure and type(r) is bool) else None
    elif op == 3:
        eq = (not r) if (ne_ok and type(r) is bool) else None
    else:
        eq = None
        if pure:
            v = a == b
            eq = v if type(v) is bool else None
    fact = (identical, eq, ne_ok)
    if eq is None and (a is None) != (b is None):
        other = type(b if a is None else a)
        fact += ((_impl(other, "__eq__"), _impl(other, "__ne__")),)
    _target().cmp.add((site, fact))
    return r


# ---------------------------------------------------------------- source rewriting

_SINGLETON_CONST = (type(None), bool)


def _is_caps(name):
    return name.isupper() and any(c.isalpha() for c in name)


def _singleton_like(node):
    if isinstance(node, ast.Constant):
        return node.value is None or isinstance(node.value, bool)
    if isinstance(node, ast.Attribute):
        return _is_caps(node.attr)
    if isinstance(node, ast.Name):
        return _is_caps(node.id)
    return False


def _simple(node):
    """Re-evaluating it has no side effects: a constant, a name, an attribute chain, a tuple."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return _simple(node.value) and isinstance(node.value, (ast.Name, ast.Attribute))
    if isinstance(node, ast.Tuple):
        return all(_simple(e) for e in node.elts)
    return False


def _load(node):
    n = copy.deepcopy(node)
    for x in ast.walk(n):
        if hasattr(x, "ctx"):
            x.ctx = ast.Load()
    return n


_OPS = {ast.Is: 0, ast.IsNot: 1, ast.Eq: 2, ast.NotEq: 3}


class Rewriter(ast.NodeTransformer):
    def __init__(self, rel):
        self.rel = rel

    def _body(self, body):
        out = []
        for st in body:
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Dict):
                st.value = self._wrap_dict(st.value)
            out.append(st)
        return out

    def visit_Module(self, node):
        node.body = self._body(node.body)
        return node

    def visit_ClassDef(self, node):
        node.decorator_list = [self.visit(d) for d in node.decorator_list]
        node.bases = [self.visit(b) for b in node.bases]
        node.keywords = [self.visit(k) for k in node.keywords]
        node.body = self._body(node.body)
        return node

    def _wrap_dict(self, d):
        owner = (self.rel, d.lineno, d.col_offset)
        STATE.owners[owner] = [ast.dump(k, include_attributes=False) if k is not None else None for k in d.keys]
        entries = []
        for i, (k, v) in enumerate(zip(d.keys, d.values)):
            if k is None:
                if _simple(v):
                    entries.append(ast.Tuple([ast.Constant("s"), _load(v)], ast.Load()))
                else:
                    entries.append(ast.Tuple([ast.Constant("x")], ast.Load()))
            elif _simple(k):
                entries.append(ast.Tuple([ast.Constant("k"), _load(k), ast.Constant(i)], ast.Load()))
            else:
                entries.append(ast.Tuple([ast.Constant("u"), ast.Constant(i)], ast.Load()))
        call = ast.Call(
            func=ast.Name("__br_kd_end__", ast.Load()),
            args=[ast.Call(ast.Name("__br_kd_begin__", ast.Load()), [ast.Constant(owner)], []), d,
                  ast.Tuple(entries, ast.Load())],
            keywords=[])
        ast.copy_location(call, d)
        return ast.fix_missing_locations(call)

    @staticmethod
    def _names_dict(n):
        return (isinstance(n, ast.Name) and n.id == "dict") or (isinstance(n, ast.Attribute) and n.attr == "dict") or \
            (isinstance(n, (ast.Tuple, ast.List, ast.Set)) and any(Rewriter._names_dict(e) for e in n.elts))

    @staticmethod
    def _exact_type(n):
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "type" and len(n.args) == 1) or \
            (isinstance(n, ast.Attribute) and n.attr == "__class__")

    def visit_Compare(self, node):
        # audit: an exact-type test against dict takes a different path for a KeyDict, so a
        # consumer behind it cannot be observed (mkprofile then refuses to trust any entry)
        sides = [node.left, *node.comparators]
        if any(self._exact_type(s) for s in sides) and any(self._names_dict(s) for s in sides):
            STATE.exact_checks.add((self.rel, node.lineno))
        self.generic_visit(node)
        if len(node.ops) != 1 or type(node.ops[0]) not in _OPS:
            return node
        a, b = node.left, node.comparators[0]
        if not (_singleton_like(a) or _singleton_like(b)):
            return node
        site = (self.rel, node.lineno, node.col_offset)
        STATE.sites.add(site)
        call = ast.Call(ast.Name("__br_cmp__", ast.Load()),
                        [ast.Constant(site), ast.Constant(_OPS[type(node.ops[0])]), a, b], [])
        return ast.copy_location(call, node)


class _Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        data = self.get_data(path)
        rel = _rel(path)
        tree = ast.parse(data, path)
        tree = Rewriter(rel).visit(tree)
        ast.fix_missing_locations(tree)
        return compile(tree, path, "exec", dont_inherit=True)

    def exec_module(self, module):
        module.__br_instrumented__ = True
        STATE.importing += 1
        try:
            super().exec_module(module)
        finally:
            STATE.importing -= 1


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if not (name == "sqlglot" or name.startswith("sqlglot.")) or "sqlglotc" in name:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is None or not spec.origin or not spec.origin.endswith(".py") or _rel(spec.origin) is None:
            return spec
        spec.loader = _Loader(name, spec.origin)
        return spec


def install(root):
    """Install the hook; must run before any sqlglot module is imported in this process."""
    global ROOT, STATE
    if STATE is not None:
        return
    ROOT = os.path.realpath(root) + os.sep
    STATE = State()
    for m in list(sys.modules):
        if m == "sqlglot" or m.startswith("sqlglot."):
            STATE.uninstrumented.add(m)
    builtins.__br_kd_begin__ = kd_begin
    builtins.__br_kd_end__ = kd_end
    builtins.__br_cmp__ = cmp
    sys.meta_path.insert(0, _Finder())


def check_instrumented():
    """sqlglot modules this process loaded without the rewrite."""
    for name, mod in list(sys.modules.items()):
        if (name == "sqlglot" or name.startswith("sqlglot.")) and "sqlglotc" not in name and mod is not None:
            f = getattr(mod, "__file__", None) or ""
            if f.endswith(".py") and not getattr(mod, "__br_instrumented__", False):
                STATE.uninstrumented.add(name)
    return STATE.uninstrumented


def static_dump():
    """Process-independent facts: rewritten dictionaries and comparison sites, provenance."""
    return {"owners": STATE.owners, "sites": STATE.sites, "prov": STATE.prov, "untrusted": STATE.untrusted,
            "dynamic": STATE.dynamic, "imp": STATE.imp.dump(), "uninstrumented": check_instrumented(),
            "exact_checks": STATE.exact_checks, "collisions": STATE.collisions}

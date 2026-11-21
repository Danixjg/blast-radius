"""Merge the runtime-profile parts of instrumented coverage runs into profile.pkl.

usage: mkprofile.py <cov_dir> <tests.json> <out.pkl> --pristine-ok <marker> [...]
                    [--divergence <plain_serial_cov_dir> <instrumented_serial_cov_dir>]

Each marker is the events file of an instrumented pass (build/failplugin.py) and must show a
complete run in which every catalogued test reported and none failed (a reverse pass may show
only the documented order-dependent pristine failures): a profile recorded while the
instrumentation broke tests describes a different program. The profile is refused (no file
written, exit 1) unless every catalogued test has a record and every sqlglot module, in every
process including pool workers, was loaded through the rewriting hook.

Passing the suite under instrumentation is not enough on its own: a consumer that tests
`type(d) is dict` (or any other path a dict subclass changes) may quietly skip a read. Two checks
cover that:
  * static: the rewriter records exact-type tests against dict in every instrumented module; any
    such site disables every dictionary entry (all masks None);
  * dynamic (--divergence): the same serial test order is run once plain and once instrumented;
    every test whose executed sqlglot/tests lines differ is "unobserved" and the selector keeps
    it wherever the profile would narrow a selection.

Output (zlib-compressed pickle), version 2:
  tests       catalogue ids in order (bit i of every mask is tests[i])
  complete    True
  unobserved  tests whose instrumented run took a different path (kept by every narrowing)
  keys        {(file, line, col) of a dict display: {"n", "keysrc", "mask"}}
              mask[i] = tests that read the value of literal entry i, directly, through aliases or
              through dictionaries that inherit the entry; None when entry i cannot be trusted
              (value read at import time, dictionary mutated, key without stable identity, a test
              that mutates tracked dictionaries read it, the dictionary was copied or bulk-read
              during a test so its values may reach other tests unseen, or an exact-dict check
              exists).
  cmp         {(file, line, col) of a comparison: {"facts": {fact: mask}, "imp": frozenset(facts)}}
              pristine operand facts (identical, eq, ne_ok) per test and at import time; the
              selector decides per edit at run time which facts an operator swap would change.
"""
import glob
import json
import os
import pickle
import sys
import zlib
from collections import defaultdict


def _known_failures():
    """Order-dependent pristine failures a reverse pass may show (build/order_dependent.txt)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_dependent.txt")
    with open(path, encoding="utf-8") as f:
        return {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}


class Refused(Exception):
    pass


def fail(msg):
    raise Refused(msg)


def check_marker(path):
    """The pass finished, nothing failed to collect, and the only failures (reverse passes only)
    are the documented order-dependent pristine ones. Returns the ids that reported."""
    ev = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    fin = [e for e in ev if e["event"] == "finish"]
    fails = {e["nodeid"] for e in ev if e["event"] == "fail"}
    reverse = os.path.basename(os.path.dirname(path)).endswith("-1")
    if not fin or fin[-1]["exitstatus"] not in ((0, 1) if reverse else (0,)):
        fail(f"{path}: instrumented pass did not finish cleanly")
    if any(e["event"] == "collect_error" for e in ev):
        fail(f"{path}: collection errors in the pristine suite")
    unexpected = fails - (_known_failures() if reverse else set())
    if unexpected:
        fail(f"{path}: the instrumented pristine suite has failures: {sorted(unexpected)[:3]}")
    if fin[-1]["exitstatus"] == 1 and not fails:
        fail(f"{path}: exit status 1 without a recorded failure")
    return {e["nodeid"] for e in ev if e["event"] == "done"}


class Resolver:
    """Follows provenance from a read of (owner, keyid) to the literal entries that supplied the
    value: ("lit", i) is entry i of that owner; ("from", src, src_kid) continues in the source
    dictionary under the source's own retained key; ("opaque",) supplied by no literal entry."""

    def __init__(self, prov):
        self.prov = prov
        self._memo, self._all = {}, {}

    def resolve(self, owner, kid, depth=0):
        key = (owner, kid)
        if key in self._memo:
            return self._memo[key]
        self._memo[key] = frozenset()   # cycle guard
        out = set()
        if depth < 50:
            for w in self.prov.get(owner, {}).get(kid, ()):
                if w[0] == "lit":
                    out.add((owner, w[1]))
                elif w[0] == "from":
                    out |= self.resolve(w[1], w[2], depth + 1)
        self._memo[key] = frozenset(out)
        return self._memo[key]

    def resolve_all(self, owner):
        if owner not in self._all:
            out = set()
            for kid in self.prov.get(owner, {}):
                out |= self.resolve(owner, kid)
            self._all[owner] = frozenset(out)
        return self._all[owner]

    def entries_of(self, rec):
        out = set()
        for o, kid in rec[0]:
            out |= self.resolve(o, kid)
        for o in rec[1]:
            out |= self.resolve_all(o)
        return out


def _new_rec():
    return [set(), set(), set(), set()]


def _add(into, rec):
    for a, b in zip(into, rec):
        a |= b


def load_profile_parts(cov_dir, tindex):
    """Per-test records (class setup credited to the class), merged static facts."""
    static = {"owners": {}, "sites": set(), "prov": defaultdict(lambda: defaultdict(set)), "untrusted": set(),
              "uninstrumented": set(), "dynamic": set(), "exact_checks": set(), "collisions": set(), "imp": _new_rec()}
    recs = defaultdict(_new_rec)
    groups = defaultdict(lambda: [0, _new_rec()])
    seen = 0

    def merge_static(st):
        static["owners"].update(st["owners"])
        static["sites"] |= st["sites"]
        for o, kv in st["prov"].items():
            for k, ws in kv.items():
                static["prov"][o][k] |= set(ws)
        for name in ("untrusted", "uninstrumented", "dynamic", "exact_checks", "collisions"):
            static[name] |= set(st.get(name, ()))
        _add(static["imp"], st["imp"])

    parts = sorted(glob.glob(os.path.join(cov_dir, "cov-*.pkl")))
    if not parts:
        fail(f"no coverage parts in {cov_dir}")
    for path in parts:
        with open(path, "rb") as f:
            d = pickle.load(f)
        if "prof" not in d:
            fail(f"{path}: not an instrumented pass (COV_PROFILE=1)")
        merge_static(d["prof"]["static"])
        for nodeid, (rec, setup) in d["prof"]["results"].items():
            i = tindex.get(nodeid)
            if i is None:
                continue
            seen |= 1 << i
            _add(recs[i], rec)
            g = groups[nodeid.rsplit("::", 1)[0]]
            g[0] |= 1 << i
            _add(g[1], setup)
    for path in sorted(glob.glob(os.path.join(cov_dir, "prof-child-*.pkl"))):
        with open(path, "rb") as f:
            while True:
                try:
                    test_id, rec, st = pickle.load(f)
                except EOFError:
                    break
                if st is not None:
                    merge_static(st)
                i = tindex.get(test_id)
                if i is None:
                    _add(static["imp"], rec)   # pool work outside any test: treat as import time
                    continue
                _add(recs[i], rec)
    for gmask, grec in groups.values():
        for i in range(len(tindex)):
            if gmask >> i & 1:
                _add(recs[i], grec)
    return recs, static, seen


def test_lines(cov_dir, tindex):
    """test index -> frozenset of (file, line) it executed (main process, class setup, workers)."""
    out = defaultdict(set)
    groups = defaultdict(lambda: [set(), set()])
    for path in sorted(glob.glob(os.path.join(cov_dir, "cov-*.pkl"))):
        with open(path, "rb") as f:
            d = pickle.load(f)
        files = d["files"]
        for nodeid, (ls, _os, _cl, setup) in d["results"].items():
            i = tindex.get(nodeid)
            if i is None:
                continue
            out[i] |= {(files[fi], ln) for fi, ln in ls}
            g = groups[nodeid.rsplit("::", 1)[0]]
            g[0].add(i)
            g[1] |= {(files[fi], ln) for fi, ln in setup[0]}
    for members, lines in groups.values():
        for i in members:
            out[i] |= lines
    for path in sorted(glob.glob(os.path.join(cov_dir, "child-*.pkl"))):
        with open(path, "rb") as f:
            while True:
                try:
                    nodeid, ls, _os, _cl = pickle.load(f)
                except EOFError:
                    break
                i = tindex.get(nodeid)
                if i is not None:
                    out[i] |= set(ls)
    return {i: frozenset(v) for i, v in out.items()}


def import_lines(cov_dir):
    """Lines executed while sqlglot's modules import (the main process imports them all first)."""
    out = set()
    for path in sorted(glob.glob(os.path.join(cov_dir, "cov-*.pkl"))):
        with open(path, "rb") as f:
            d = pickle.load(f)
        files = d["files"]
        out |= {(files[fi], ln) for fi, ln in d.get("imp_lines", ())}
    return out


def divergence(plain_dir, instr_dir, tindex):
    """Tests whose RUN-TIME lines differ between a plain and an instrumented run of the same serial
    order: the instrumentation changed their path, so their records cannot be trusted.

    Lines that run while modules import are left out of the comparison: in spawned pool workers a
    dialect module is imported by whichever task needs it first, so which test those lines land in
    varies between two plain runs (measured: 7 tests differ plain/plain on all lines, 0 on run-time
    lines). That loses nothing: everything read or compared while a module imports is already
    untrusted (dictionary entries) or decided for all tests (comparisons) by the recorder."""
    a, b = test_lines(plain_dir, tindex), test_lines(instr_dir, tindex)
    excl = import_lines(plain_dir) | import_lines(instr_dir)
    bad = {}
    for i in set(a) | set(b):
        x, y = a.get(i, frozenset()) - excl, b.get(i, frozenset()) - excl
        if x != y:
            bad[i] = (sorted(x - y)[:5], sorted(y - x)[:5])
    missing = [i for i in range(len(tindex)) if i not in a or i not in b]
    return bad, missing


def build(cov_dir, tests, markers, div=None):
    tindex = {t: i for i, t in enumerate(tests)}
    ALL = (1 << len(tests)) - 1
    if not markers:
        fail("no --pristine-ok marker: refusing to trust an unverified run")
    for mk in markers:
        done = check_marker(mk)
        missing = [t for t in tests if t not in done]
        if missing:
            fail(f"{mk}: {len(missing)} catalogued tests never reported, e.g. {missing[:3]}")
    recs, st, seen = load_profile_parts(cov_dir, tindex)
    missing = [tests[i] for i in range(len(tests)) if not seen >> i & 1]
    if missing:
        fail(f"{len(missing)} catalogued tests have no profile record, e.g. {missing[:3]}")
    if st["uninstrumented"]:
        fail(f"sqlglot modules loaded without instrumentation: {sorted(st['uninstrumented'])[:5]}")

    unobserved, div_report = 0, {}
    if div is not None:
        bad, absent = divergence(div[0], div[1], tindex)
        if absent:
            fail(f"divergence runs miss {len(absent)} tests, e.g. {[tests[i] for i in absent[:3]]}")
        for i, diff in bad.items():
            unobserved |= 1 << i
            div_report[tests[i]] = diff

    res = Resolver(st["prov"])
    bad_entries = set(res.entries_of(st["imp"]))
    dynamic = set(st["imp"][3]) | st["dynamic"]
    bulk = set()
    for i, rec in recs.items():
        if rec[3]:
            # a test that mutates tracked dictionaries can carry values between them unseen
            dynamic |= rec[3]
            bad_entries |= res.entries_of(rec)
        for o in rec[1]:
            # copied or bulk-read during a test (dict(d), {**d}, items(), ==, repr, pickling): the
            # copy may feed structures other tests consume without a read we could record
            bulk.add(o)
            bad_entries |= res.resolve_all(o)
    bad_owners = st["untrusted"] | dynamic
    emask = defaultdict(int)
    for i, rec in recs.items():
        bit = 1 << i
        for e in res.entries_of(rec):
            emask[e] |= bit
    keys, n_trusted, n_total = {}, 0, 0
    for owner, keysrc in st["owners"].items():
        masks = []
        for idx, ks in enumerate(keysrc):
            n_total += 1
            if ks is None or st["exact_checks"] or owner in bad_owners or (owner, idx) in bad_entries:
                masks.append(None)
            else:
                masks.append(emask.get((owner, idx), 0))
                n_trusted += 1
        keys[owner] = {"n": len(keysrc), "keysrc": keysrc, "mask": masks}

    facts = defaultdict(lambda: defaultdict(int))
    for i, rec in recs.items():
        bit = 1 << i
        for site, fact in rec[2]:
            facts[site][fact] |= bit
    imp_facts = defaultdict(set)
    for site, fact in st["imp"][2]:
        imp_facts[site].add(fact)
    cmp = {s: {"facts": dict(facts.get(s, {})), "imp": frozenset(imp_facts.get(s, ()))} for s in st["sites"]}
    data = {"version": 2, "tests": tests, "complete": True, "unobserved": unobserved, "keys": keys, "cmp": cmp,
            "exact_checks": sorted(st["exact_checks"]), "collisions": sorted(st["collisions"], key=repr)}
    summary = (f"profile: {len(st['owners'])} dictionaries, {n_trusted}/{n_total} entries trusted, {len(dynamic)} mutated, "
               f"{len(st['untrusted'])} without provenance, {len(st['exact_checks'])} exact-dict checks; "
               f"{len(st['sites'])} comparison sites; {bin(unobserved).count('1')} tests unobserved")
    return data, summary, div_report


def main():
    args = sys.argv[1:]
    markers, div = [], None
    while "--pristine-ok" in args:
        i = args.index("--pristine-ok")
        markers.append(args[i + 1])
        del args[i:i + 2]
    if "--divergence" in args:
        i = args.index("--divergence")
        div = (args[i + 1], args[i + 2])
        del args[i:i + 3]
    cov_dir, tests_json, out = args
    tests = [t["id"] for t in json.load(open(tests_json, encoding="utf-8"))["tests"]]
    try:
        data, summary, div_report = build(cov_dir, tests, markers, div)
    except Refused as e:
        print("mkprofile: REFUSED:", e, file=sys.stderr)
        sys.exit(1)
    blob = zlib.compress(pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL), 9)
    with open(out + ".tmp", "wb") as f:
        f.write(blob)
    os.replace(out + ".tmp", out)
    if div_report:
        with open(out + ".divergence.json", "w", encoding="utf-8") as f:
            json.dump(div_report, f, indent=1, default=list)
    print(summary + f"; {len(blob)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()

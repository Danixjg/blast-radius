"""Regression tests for the runtime profiler (build/brprofile.py, build/mkprofile.py) and the
selector's use of it. Run from the repository root:

    work/venv/bin/python -m unittest build/test_profiler.py -v      (Scripts\\python.exe on Windows)

Three counterexamples that reproduced on c8a81ea:
  1. key collisions: {1: "first", True: "second"}[1] is "second", but the read was credited to
     the first literal slot (also through **spread);
  2. a consumer behind `type(d) is dict` takes another path once d is a KeyDict, so the
     instrumented run records no read although the pristine run reads the value;
  3. `obj != None` with a side-effecting __eq__ and an inherited object.__ne__ runs user code,
     but object.__ne__ was treated as pure.
"""
import ast
import builtins
import json
import os
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R / "build"))
sys.path.insert(0, str(R))
import brprofile as B  # noqa: E402
import mkprofile as M  # noqa: E402
import selector as S  # noqa: E402

S.SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot"))

REL = "sqlglot/fake_module.py"


def fresh():
    B.STATE = B.State()
    builtins.__br_kd_begin__ = B.kd_begin
    builtins.__br_kd_end__ = B.kd_end
    builtins.__br_cmp__ = B.cmp


def instrument(src, rel=REL):
    """Compile src through the profiler's rewriter and execute it as an import would."""
    tree = B.Rewriter(rel).visit(ast.parse(src))
    ast.fix_missing_locations(tree)
    ns = {"__name__": rel[:-3].replace("/", ".")}
    B.STATE.importing += 1
    try:
        exec(compile(tree, rel, "exec"), ns)
    finally:
        B.STATE.importing -= 1
    return ns


def as_test(fn, *a):
    """Run fn as one test would and return (result, record)."""
    B.STATE.cur = B.Rec()
    try:
        return fn(*a), B.STATE.cur.dump()
    finally:
        B.STATE.cur = None


def owner_at(line, col=4):
    return (REL, line, col)


class KeyCollisions(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_literal_collision_credits_the_slot_that_won(self):
        ns = instrument('D = {1: "first", True: "second"}\n')
        v, rec = as_test(lambda: ns["D"][1])
        self.assertEqual(v, "second")
        self.assertEqual(M.Resolver(B.STATE.prov).entries_of(rec), {(owner_at(1), 1)})
        v, rec = as_test(lambda: ns["D"][True])
        self.assertEqual(M.Resolver(B.STATE.prov).entries_of(rec), {(owner_at(1), 1)})
        self.assertEqual(B.STATE.collisions, {(owner_at(1), ("int", 1), ("bool", True))}, "collisions are logged")

    def test_collision_through_spread_after_literal(self):
        ns = instrument('BASE = {1: "x"}\nD = {True: "y", **BASE}\n')
        v, rec = as_test(lambda: ns["D"][True])
        self.assertEqual(v, "x")
        # the value came from BASE's entry 0, not from D's dead literal slot
        self.assertEqual(M.Resolver(B.STATE.prov).entries_of(rec), {((REL, 1, 7), 0)})

    def test_collision_through_spread_before_literal(self):
        ns = instrument('BASE = {1: "x"}\nE = {**BASE, True: "z"}\n')
        v, rec = as_test(lambda: ns["E"][1])
        self.assertEqual(v, "z")
        self.assertEqual(M.Resolver(B.STATE.prov).entries_of(rec), {(owner_at(2), 1)})

    def test_chain_of_spreads_with_collision(self):
        ns = instrument('A = {True: "a"}\nBB = {**A}\nC = {1.0: "c0", **BB}\n')
        v, rec = as_test(lambda: ns["C"][1])
        self.assertEqual(v, "a")
        self.assertEqual(M.Resolver(B.STATE.prov).entries_of(rec), {((REL, 1, 4), 0)})


class UnobservableConsumers(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_type_check_skips_the_read(self):
        """The counterexample itself: a consumer behind `type(d) is dict` reads the pristine
        dict but not the KeyDict, so the instrumented run records nothing."""
        ns = instrument('T = {"k": 1}\n')

        def consumer(d):
            return d["k"] if type(d) is dict else None

        self.assertEqual(consumer(dict(ns["T"])), 1, "pristine path reads the value")
        v, rec = as_test(consumer, ns["T"])
        self.assertIsNone(v)
        self.assertEqual(rec[0], set(), "no read is recorded: the recorder alone cannot see this consumer")

    def test_static_audit_finds_exact_dict_checks(self):
        instrument('T = {"k": 1}\n\n\ndef consumer(d):\n    if type(d) is dict:\n        return d["k"]\n'
                   '    return d.__class__ == dict or type(d) in (dict, list)\n')
        self.assertEqual(B.STATE.exact_checks, {(REL, 5), (REL, 7)})

    def _cov_dir(self, root, name, tests, lines_by_test, static, recs, imp=()):
        d = Path(root) / name
        d.mkdir()
        files = sorted({f for ls in lines_by_test.values() for f, _ in ls} | {f for f, _ in imp})
        fi = {f: i for i, f in enumerate(files)}
        results = {t: ({(fi[f], ln) for f, ln in lines_by_test[t]}, set(), set(), (set(), set(), set())) for t in tests}
        out = {"files": files, "imp_classes": set(), "imp_lines": {(fi[f], ln) for f, ln in imp}, "imp_opens": set(),
               "results": results}
        if static is not None:
            out["prof"] = {"results": {t: (recs[t], (set(), set(), set(), set())) for t in tests}, "static": static}
        with open(d / "cov-main-f.pkl", "wb") as f:
            pickle.dump(out, f)
        return str(d)

    def _marker(self, root, tests):
        d = Path(root) / "profile-0"
        d.mkdir()
        ev = [{"event": "start"}] + [{"event": "done", "nodeid": t, "outcome": "passed"} for t in tests] + \
             [{"event": "finish", "exitstatus": 0, "collected": len(tests), "reported": len(tests)}]
        (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
        return str(d / "events.jsonl")

    def _build(self, plain_lines, instr_lines, imp=(), other=lambda T: None):
        tests = ["tests/test_a.py::A::test_reads", "tests/test_a.py::A::test_other"]
        ns = instrument('T = {"k": 1, "j": 2}\n')
        _, rec0 = as_test(lambda: ns["T"]["j"])
        _, rec1 = as_test(other, ns["T"])
        static = B.static_dump()
        with tempfile.TemporaryDirectory() as root:
            instr = self._cov_dir(root, "instr", tests, instr_lines, static, {tests[0]: rec0, tests[1]: rec1}, imp)
            plain = self._cov_dir(root, "plain", tests, plain_lines, None, None, imp)
            mk = self._marker(root, tests)
            data, _, report = M.build(instr, tests, [mk], (plain, instr))
        return data, report

    def test_divergence_marks_the_test_unobserved(self):
        same = {"tests/test_a.py::A::test_reads": {("sqlglot/x.py", 1)}, "tests/test_a.py::A::test_other": {("sqlglot/x.py", 1)}}
        diverged = dict(same, **{"tests/test_a.py::A::test_other": {("sqlglot/x.py", 1), ("sqlglot/x.py", 9)}})
        data, report = self._build(diverged, same)
        self.assertEqual(data["unobserved"], 0b10)
        self.assertIn("tests/test_a.py::A::test_other", report)
        owner = next(iter(data["keys"]))
        self.assertEqual(data["keys"][owner]["mask"], [0, 0b01], "entry 'j' is read by test 0 only")
        data, report = self._build(same, same)
        self.assertEqual(data["unobserved"], 0)

    def test_divergence_superset_path_stays_observed(self):
        """Instrumented run executed every plain line plus more (a pool worker that happened to
        build a cache during this test): its records cover the plain path, so it is observed. The
        divergence is still reported."""
        same = {"tests/test_a.py::A::test_reads": {("sqlglot/x.py", 1)}, "tests/test_a.py::A::test_other": {("sqlglot/x.py", 1)}}
        more = dict(same, **{"tests/test_a.py::A::test_other": {("sqlglot/x.py", 1), ("sqlglot/x.py", 9)}})
        data, report = self._build(same, more)
        self.assertEqual(data["unobserved"], 0)
        self.assertIn("tests/test_a.py::A::test_other", report)
        swapped = dict(same, **{"tests/test_a.py::A::test_other": {("sqlglot/x.py", 9)}})
        data, _ = self._build(same, swapped)
        self.assertEqual(data["unobserved"], 0b10, "a plain line the instrumented run missed")

    def test_divergence_ignores_import_time_lines_only(self):
        """A module body imported by a different task (pool scheduling) is not a changed path;
        a different run-time line still is."""
        base = {"tests/test_a.py::A::test_reads": {("sqlglot/x.py", 1)}, "tests/test_a.py::A::test_other": {("sqlglot/x.py", 1)}}
        moved = dict(base, **{"tests/test_a.py::A::test_other": {("sqlglot/x.py", 1), ("sqlglot/d.py", 2)}})
        data, _ = self._build(moved, base, imp={("sqlglot/d.py", 2)})
        self.assertEqual(data["unobserved"], 0)
        data, _ = self._build(moved, base, imp={("sqlglot/d.py", 3)})
        self.assertEqual(data["unobserved"], 0b10)

    def test_runtime_copy_makes_entries_untrusted(self):
        same = {"tests/test_a.py::A::test_reads": set(), "tests/test_a.py::A::test_other": set()}
        data, _ = self._build(same, same, other=lambda T: dict(T))
        owner = next(iter(data["keys"]))
        self.assertEqual(data["keys"][owner]["mask"], [None, None], "a copy may reach other tests unseen")
        for copier in (lambda T: {**T}, lambda T: (lambda **kw: kw)(**T), lambda T: list(T.items())):
            fresh()
            data, _ = self._build(same, same, other=copier)
            self.assertEqual(data["keys"][next(iter(data["keys"]))]["mask"], [None, None])
        fresh()
        data, _ = self._build(same, same, other=lambda T: ("k" in T, len(T), sorted(T)))
        self.assertEqual(data["keys"][next(iter(data["keys"]))]["mask"], [0, 0b01], "membership is not a copy")

    def test_exact_check_disables_every_entry(self):
        instrument('def consumer(d):\n    return type(d) is dict\n', rel="sqlglot/other.py")
        same = {"tests/test_a.py::A::test_reads": set(), "tests/test_a.py::A::test_other": set()}
        data, _ = self._build(same, same)
        self.assertTrue(data["exact_checks"])
        self.assertTrue(all(m is None for o in data["keys"].values() for m in o["mask"]))

    def test_selector_keeps_unobserved_tests(self):
        rel = "sqlglot/parsers/singlestore.py"
        src = (S.SRC / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        st = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                  and any(isinstance(t, ast.Name) and t.id == "SHOW_PARSERS" for t in n.targets))
        d = st.value
        keysrc = [S._dump(k) for k in d.keys]
        prof = {"keys": {(rel, d.lineno, d.col_offset): {"n": len(keysrc), "keysrc": keysrc, "mask": [0] * len(keysrc)}},
                "unobserved": 1 << 5}
        h = S.Hunter(S.load_map(), prof)
        old = '"GROUPS FOR USER": _show_parser("GROUPS FOR USER", target=True),'
        patch = "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n".format(rel)
        import difflib
        patch += "".join(list(difflib.unified_diff(src.splitlines(True), src.replace(old, old.replace("True", "False")).splitlines(True)))[2:])
        m, _ = h.analyze(patch)
        self.assertEqual(m & (1 << 5), 1 << 5)


class Comparisons(unittest.TestCase):
    SRC = ('class Obj:\n'
           '    calls = 0\n\n'
           '    def __eq__(self, other):\n'
           '        Obj.calls += 1\n'
           '        return False\n\n'
           '    __hash__ = object.__hash__\n\n\n'
           'def is_not(o):\n'
           '    return o is not None\n\n\n'
           'def ne(o):\n'
           '    return o != None\n\n\n'
           'def plain_is(o):\n'
           '    return o is None\n')

    def setUp(self):
        fresh()
        self.ns = instrument(self.SRC)

    def facts(self, rec, line):
        return {f for (s, f) in rec[2] if s[1] == line}

    def test_inherited_ne_is_not_pure(self):
        self.assertFalse(B.ne_pure(self.ns["Obj"](), None))
        self.assertTrue(B.ne_pure(object(), None))

    def test_recorder_adds_no_side_effects(self):
        o = self.ns["Obj"]()
        r, rec = as_test(self.ns["is_not"], o)
        self.assertTrue(r)
        self.assertEqual(self.ns["Obj"].calls, 0, "recording `o is not None` must not call __eq__")
        fact = self.facts(rec, 12)
        self.assertEqual(fact, {(False, None, False)})
        self.assertFalse(S.Hunter._swap_preserves(next(iter(fact)), negated=True),
                         "`is not` -> `!=` would run the side-effecting __eq__")

    def test_ne_original_cannot_drop_its_side_effects(self):
        o = self.ns["Obj"]()
        r, rec = as_test(self.ns["ne"], o)
        self.assertEqual(self.ns["Obj"].calls, 1, "the original != runs __eq__ exactly once")
        fact = next(iter(self.facts(rec, 16)))
        self.assertFalse(S.Hunter._swap_preserves(fact, negated=True))

    def test_builtin_operands_are_decided_at_run_time(self):
        _, rec = as_test(self.ns["plain_is"], None)
        fact = next(iter(self.facts(rec, 20)))
        self.assertEqual(fact, (True, True, True))
        self.assertTrue(S.Hunter._swap_preserves(fact, negated=False))
        _, rec = as_test(self.ns["plain_is"], "x")
        fact = next(iter(self.facts(rec, 20)))
        self.assertEqual(fact, (False, False, True))
        self.assertTrue(S.Hunter._swap_preserves(fact, negated=False))

    def test_str_enum_member_against_raw_string(self):
        ns = instrument('import enum\n\n\nclass E(str, enum.Enum):\n    UPPER = "UPPER"\n\n\n'
                        'def f(x):\n    return x is E.UPPER\n')
        _, rec = as_test(ns["f"], "UPPER")
        fact = next(iter({f for (s, f) in rec[2]}))
        self.assertEqual(fact, (False, True, True))
        self.assertFalse(S.Hunter._swap_preserves(fact, negated=False), "'UPPER' == E.UPPER but is not identical")


if __name__ == "__main__":
    unittest.main()

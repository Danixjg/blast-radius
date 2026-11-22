"""Pytest instrumentation for pristine, single-line integer ordering conditions."""

import ast
import builtins
import importlib.abc
import importlib.machinery
import json
import os
import re
import symtable
import sys
import threading
import unittest
from pathlib import Path

import pytest


ROOT = Path(os.environ["BOUNDARY_ROOT"]).resolve()
OUT = Path(os.environ["BOUNDARY_OUT"])
ORDERING = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)
AUDIT = {"open", "subprocess.Popen", "os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn", "os.startfile",
         "os.system", "_winapi.CreateProcess"}
SITES = set()
BLOCKED = set()
FACTS = {}
REPORTS = {}
CURRENT = None
ACTIVE = False
MAIN_THREAD = threading.get_ident()


def observe(site, left, right):
    if not ACTIVE or threading.get_ident() != MAIN_THREAD:
        BLOCKED.add(site)
    else:
        states = CURRENT[1]
        safe = type(left) is int and type(right) is int and left != right
        states[site] = states.get(site, True) and safe
    return right


def audit(event, args):
    if event in AUDIT and CURRENT is not None:
        CURRENT[2] = False


class Rewriter(ast.NodeTransformer):
    def __init__(self, rel, source):
        self.rel = rel
        self.lines = source.splitlines()
        self.scopes = [symtable.symtable(source, rel, "exec")]

    def _visit_scope(self, node):
        parent = self.scopes[-1]
        children = [] if parent is None else [
            child for child in parent.get_children()
            if child.get_name() == node.name and child.get_lineno() == node.lineno
        ]
        self.scopes.append(children[0] if len(children) == 1 else None)
        node.body = [self.visit(statement) for statement in node.body]
        self.scopes.pop()
        return node

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope
    visit_ClassDef = _visit_scope

    @staticmethod
    def _operand(node, scope):
        if isinstance(node, ast.Constant):
            return type(node.value) is int
        if isinstance(node, ast.Name):
            symbol = scope.lookup(node.id)
            return symbol.is_local() and not symbol.is_global() and not symbol.is_nonlocal()
        return False

    def visit_If(self, node):
        self.generic_visit(node)
        t = node.test
        scope = self.scopes[-1]
        if scope is None or scope.get_type() != symtable.SymbolTableType.FUNCTION or not isinstance(t, ast.Compare):
            return node
        if len(t.ops) != 1 or not isinstance(t.ops[0], ORDERING) or t.lineno != node.lineno or t.end_lineno != node.lineno:
            return node
        if not re.match(r"\s*if\s", self.lines[node.lineno - 1]) or node.body[0].lineno == node.lineno:
            return node
        left, right = t.left, t.comparators[0]
        if not self._operand(left, scope) or not self._operand(right, scope):
            return node
        site = (self.rel, node.lineno)
        SITES.add(site)
        # Keep the actual comparison in its original frame. Reading an initialized local twice
        # is side-effect-free, and the observer returns the same right operand it received.
        call = ast.Call(ast.Name("__br_observe_order_boundary__", ast.Load()), [ast.Constant(site), left, right], [])
        t.comparators[0] = ast.copy_location(call, right)
        return node


class Loader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        path = self.get_filename(fullname)
        source = importlib.util.decode_source(self.get_data(path))
        rel = Path(path).resolve().relative_to(ROOT).as_posix()
        tree = ast.fix_missing_locations(Rewriter(rel, source).visit(ast.parse(source, path)))
        return compile(tree, path, "exec", dont_inherit=True)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and isinstance(spec.loader, importlib.machinery.SourceFileLoader):
            origin = Path(spec.origin).resolve()
            if origin.is_relative_to(ROOT / "sqlglot"):
                spec.loader = Loader(fullname, spec.origin)
        return spec


if any(name == "sqlglot" or name.startswith("sqlglot.") for name in sys.modules):
    raise RuntimeError("sqlglot imported before boundary instrumentation")
builtins.__br_observe_order_boundary__ = observe
sys.meta_path.insert(0, Finder())
sys.addaudithook(audit)


def eligible(item):
    cls = item.cls
    if not isinstance(cls, type) or not issubclass(cls, unittest.TestCase):
        return False
    if any(getattr(cls, hook).__func__ is not getattr(unittest.TestCase, hook).__func__
           for hook in ("setUpClass", "tearDownClass")):
        return False
    return cls.setUp is unittest.TestCase.setUp and cls.tearDown is unittest.TestCase.tearDown


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global CURRENT
    CURRENT = [item.nodeid, {}, eligible(item)]
    try:
        yield
    finally:
        nodeid, states, usable = CURRENT
        FACTS[nodeid] = (states, usable)
        CURRENT = None


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    global ACTIVE
    ACTIVE = True
    try:
        yield
    finally:
        ACTIVE = False


def pytest_runtest_logreport(report):
    REPORTS.setdefault(report.nodeid, {})[report.when] = report.outcome


def pytest_sessionfinish(session, exitstatus):
    good, bad = {}, {}
    for nodeid, (states, usable) in FACTS.items():
        complete = REPORTS.get(nodeid) == {"setup": "passed", "call": "passed", "teardown": "passed"}
        for site, safe in states.items():
            sink = good if safe and usable and complete else bad
            sink.setdefault(site, []).append(nodeid)
    OUT.write_text(json.dumps({"exitstatus": exitstatus, "reports": REPORTS,
                               "sites": [[rel, line, sorted(good.get((rel, line), [])),
                                          sorted(bad.get((rel, line), [])), (rel, line) in BLOCKED]
                                         for rel, line in sorted(SITES)]}), encoding="utf-8")

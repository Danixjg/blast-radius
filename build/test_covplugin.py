"""Tests for build/covplugin.py (stdlib unittest; run from the repository root):

    work/venv/bin/python -m unittest build/test_covplugin.py -v

They run a small pytest session under the plugin against the pinned checkout in work/sqlglot
(BR_SRC overrides) and read the per-process coverage pickles it writes.
"""
import ast
import glob
import os
import pickle
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

R = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot")).resolve()

POOL_TEST = textwrap.dedent('''
    import multiprocessing
    import unittest
    from concurrent.futures import ProcessPoolExecutor


    def work():
        import sqlglot
        # the athena dialect and generator load lazily, inside this task
        return sqlglot.transpile("SELECT CAST(x AS TEXT) FROM t", write="athena")[0]


    class TestPool(unittest.TestCase):
        def test_pool(self):
            with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn")) as pool:
                self.assertIn("SELECT", pool.submit(work).result())
''')


def import_time_lines(path):
    """Lines of statements that run when the module body runs: module level and class bodies,
    not function or lambda bodies."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out, body = set(), set()

    def visit(node, in_func):
        for child in ast.iter_child_nodes(node):
            func = in_func or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(child, ast.stmt):
                (body if func else out).add(child.lineno)
            visit(child, func)

    visit(tree, False)
    return out, body


class PoolWorkerCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="br-covplugin-"))
        (cls.tmp / "test_pool_cov.py").write_text(POOL_TEST, encoding="utf-8")
        cls.out = cls.tmp / "cov"
        env = dict(os.environ, COV_ROOT=str(SRC), COV_OUT=str(cls.out), COV_PROFILE="0", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join([str(SRC), str(R / "build")]), PYTHON_CPU_COUNT="2")
        cls.proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "covplugin",
                                  "-p", "no:xdist", "test_pool_cov.py"], cwd=cls.tmp, env=env,
                                 capture_output=True, text=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def child_lines(self):
        lines = set()
        for path in glob.glob(str(self.out / "child-*.pkl")):
            with open(path, "rb") as f:
                while True:
                    try:
                        test_id, ls, _opens, _classes = pickle.load(f)
                    except EOFError:
                        break
                    if test_id.endswith("test_pool"):
                        lines |= ls
        return lines

    def test_session_passes_and_the_child_records(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout[-3000:] + self.proc.stderr[-3000:])
        self.assertTrue(self.child_lines(), "the pool task's lines were recorded")

    def test_lazily_imported_module_bodies_are_not_charged_to_the_test(self):
        lines = self.child_lines()
        for rel in ("sqlglot/dialects/athena.py", "sqlglot/generators/athena.py"):
            imp, _ = import_time_lines(SRC / rel)
            charged = sorted(ln for f, ln in lines if f == rel and ln in imp)
            self.assertEqual(charged, [], f"{rel}: import-time lines recorded as the test's own")

    def test_function_bodies_the_task_ran_are_still_recorded(self):
        lines = self.child_lines()
        _, body = import_time_lines(SRC / "sqlglot/generators/athena.py")
        self.assertTrue({ln for f, ln in lines if f == "sqlglot/generators/athena.py"} & body,
                        "athena generator function lines the task executed")


if __name__ == "__main__":
    unittest.main()

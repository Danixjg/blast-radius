"""Probe server: forked probes must return exactly the verdict a fresh probe returns.

    python3 -m unittest build/test_probe_server.py -v

Needs the pinned checkout in work/sqlglot (BR_SRC overrides) and os.fork (Linux/macOS).
"""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402

S.SRC = Path(os.environ.get("BR_SRC", R / "work" / "sqlglot")).resolve()
LEAF = "sqlglot/dialects/dremio.py"
CORE = "sqlglot/helper.py"


def read(rel):
    return (S.SRC / rel).read_bytes().decode("utf-8")


class Eligibility(unittest.TestCase):
    loaded = {"sqlglot", "sqlglot.helper", "sqlglot.dialects", "re", "typing"}
    attempted = {"sqlglot.optional_thing"}

    def ok(self, files):
        return S.ProbeServer.eligible_for(files, self.loaded, self.attempted)

    def test_leaf_edit(self):
        self.assertTrue(self.ok({LEAF: "x = 1\n"}))

    def test_no_files(self):
        self.assertTrue(self.ok({}))

    def test_loaded_module_is_not_eligible(self):
        self.assertFalse(self.ok({CORE: "x = 1\n"}))

    def test_package_init_of_loaded_package(self):
        self.assertFalse(self.ok({"sqlglot/dialects/__init__.py": "x = 1\n"}))

    def test_deleting_a_loaded_module(self):
        self.assertFalse(self.ok({CORE: None}))

    def test_deleting_an_unloaded_module(self):
        self.assertTrue(self.ok({LEAF: None}))

    def test_module_the_preload_tried_to_import(self):
        self.assertFalse(self.ok({"sqlglot/optional_thing.py": "x = 1\n"}))

    def test_file_outside_sqlglot(self):
        self.assertFalse(self.ok({"tests/conftest.py": "x = 1\n"}))
        self.assertFalse(self.ok({"typing.py": "x = 1\n"}))

    def test_one_bad_file_spoils_the_job(self):
        self.assertFalse(self.ok({LEAF: "x = 1\n", CORE: "x = 1\n"}))


@unittest.skipUnless(hasattr(os, "fork") and (S.SRC / "sqlglot").is_dir(), "needs fork and sqlglot sources")
class Parity(unittest.TestCase):
    """Each case runs fresh and forked; the verdicts must match the expected one."""

    @classmethod
    def setUpClass(cls):
        cls.server = S.ProbeServer.start()
        assert cls.server is not None, "probe server failed to start"

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def both(self, files, expected, timeout=30):
        self.assertTrue(self.server.eligible(files), "case must exercise the forked path")
        before = self.server.forked
        forked = S.run_probe(files, timeout, server=self.server)
        self.assertEqual(self.server.forked, before + 1, "the forked path was used")
        fresh = S.run_probe(files, timeout)
        self.assertEqual((fresh, forked), (expected, expected))

    def test_pristine(self):
        self.both({}, "ok")

    def test_harmless_leaf_edit(self):
        self.both({LEAF: read(LEAF) + "\n_UNUSED = 1\n"}, "ok")

    def test_leaf_raises_at_import(self):
        self.both({LEAF: read(LEAF) + "\nraise RuntimeError('patched')\n"}, "broken")

    def test_leaf_syntax_error(self):
        self.both({LEAF: read(LEAF) + "\ndef (:\n"}, "broken")

    def test_deleted_leaf(self):
        fresh = S.run_probe({LEAF: None}, 30)
        self.assertEqual(S.run_probe({LEAF: None}, 30, server=self.server), fresh)

    def test_combined_patch(self):
        # the leaf calls a core helper at import time; the helper is patched to raise. The core
        # file makes the job ineligible, so it must fall back to a fresh probe and still fail.
        src = read(CORE)
        body = src[src.index("def csv("):]
        body = body[:body.index("\n\n\n")]
        ret = [ln for ln in body.splitlines() if ln.strip().startswith("return ")][-1]
        files = {CORE: src.replace(ret, ret.replace("return ", 'raise RuntimeError("patched") or ')),
                 LEAF: read(LEAF) + "\nfrom sqlglot.helper import csv as _csv\n_X = _csv('a')\n"}
        self.assertFalse(self.server.eligible(files))
        before = self.server.forked
        self.assertEqual(S.run_probe(files, 30, server=self.server), "broken")
        self.assertEqual(self.server.forked, before, "ineligible: fresh probe")

    def test_hang_times_out_and_server_survives(self):
        files = {LEAF: read(LEAF) + "\nimport time\ntime.sleep(60)\n"}
        t = time.monotonic()
        self.assertEqual(S.run_probe(files, 3, server=self.server), "timeout")
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(S.run_probe({}, 30, server=self.server), "ok", "server still serves")

    def test_exit_hook_hang_is_a_timeout_both_ways(self):
        files = {LEAF: read(LEAF) + "\nimport atexit, time\natexit.register(time.sleep, 60)\n"}
        self.both(files, "timeout", timeout=4)

    def test_non_daemon_thread_is_a_timeout_both_ways(self):
        files = {LEAF: read(LEAF) + "\nimport threading, time\nthreading.Thread(target=time.sleep, args=(60,)).start()\n"}
        self.both(files, "timeout", timeout=4)

    def test_stdout_noise_does_not_hide_the_report(self):
        self.both({LEAF: read(LEAF) + "\nprint('noise')\n"}, "ok")


@unittest.skipUnless(hasattr(os, "fork") and (S.SRC / "sqlglot").is_dir(), "needs fork and sqlglot sources")
class Fallback(unittest.TestCase):
    def test_dead_server_falls_back_to_fresh(self):
        server = S.ProbeServer.start()
        self.assertIsNotNone(server)
        server.kill_for_test()
        files = {LEAF: read(LEAF) + "\nraise RuntimeError('patched')\n"}
        self.assertEqual(S.run_probe(files, 30, server=server), "broken")
        self.assertEqual(S.run_probe({}, 30, server=server), "ok")
        server.close()

    def test_disabled_by_environment(self):
        with patch.dict(os.environ, {"BR_PROBE_SERVER": "0"}):
            self.assertIsNone(S.ProbeServer.start())


class Dedupe(unittest.TestCase):
    def test_identical_jobs_probe_once(self):
        calls = []

        def fake(files, timeout, server=None):
            calls.append(files)
            return "broken" if "bad" in files else "ok"

        jobs = {"a": {"x.py": "1"}, "b": {"x.py": "1"}, "c": {"bad": "2"}, "d": {"bad": "2"}, "e": {}}
        with patch.object(S, "run_probe", fake), patch.object(S.ProbeServer, "start", lambda *a, **k: None):
            out = S.run_probes(jobs)
        self.assertEqual(out, {"a": "ok", "b": "ok", "c": "broken", "d": "broken", "e": "ok"})
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()

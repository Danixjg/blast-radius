import importlib.util
import json
import pathlib
import os
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("executor", ROOT / "experiments/executor.py")
executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(executor)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.script = self.root / "worker.py"
        self.script.write_text(
            "import sys,time\n"
            "failed=False\n"
            "for item in sys.argv[1:]:\n"
            " time.sleep(.015)\n"
            " print(item,flush=True)\n"
            " failed |= item == 'bad'\n"
            "sys.exit(int(failed))\n"
        )
        self.plan = {"tasks": [{"id": x, "seconds": .015, "risk": p}
                               for x, p in [("a", .1), ("bad", .9), ("b", .1)]],
                     "workers": [["a", "bad"], ["b"]]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_mode(self, mode, plan=None, command=None, timeout=2):
        return executor.run_plan(plan or self.plan,
                                 command or [sys.executable, str(self.script)],
                                 self.root, mode, 2, timeout, self.root / mode)

    def test_every_mode_completes_every_assignment_after_failure(self):
        for mode in ("static", "priority", "relay"):
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertTrue(result["all_completed"])
                self.assertEqual(sorted(result["completed"]), ["a", "b", "bad"])
                self.assertIsNotNone(result["first_failure_seconds"])
                self.assertGreaterEqual(result["wall_seconds"], result["first_failure_seconds"])
                self.assertTrue(result["failed_batches"])
                self.assertLessEqual(result["peak_workers"], 2)

    def test_priority_changes_order_without_dropping_tasks(self):
        result = self.run_mode("priority")
        self.assertEqual(result["batches"][0]["tasks"], ["bad", "a"])

    def test_relay_has_exact_singleton_failures_and_completion_barrier(self):
        result = self.run_mode("relay")
        self.assertEqual(result["failed"], ["bad"])
        self.assertEqual(sum(len(x["tasks"]) for x in result["batches"]), 3)

    def test_timeout_is_incomplete_validation_and_continues(self):
        result = self.run_mode("relay", command=[sys.executable, "-c", "import time;time.sleep(2)"], timeout=.04)
        self.assertFalse(result["all_completed"])
        self.assertEqual(sorted(result["timed_out"]), ["a", "b", "bad"])
        self.assertEqual(result["completed"], [])

    def test_empty_plan(self):
        result = self.run_mode("relay", plan={"tasks": [], "workers": []})
        self.assertTrue(result["all_completed"])
        self.assertEqual(result["peak_workers"], 0)

    def test_invalid_plans_rejected_before_launch(self):
        invalid = [
            {"tasks": [{"id": "a", "risk": float("nan"), "seconds": 1}]},
            {"tasks": [{"id": "a", "risk": 2, "seconds": 1}]},
            {"tasks": [{"id": "a", "seconds": -1}]},
            {"tasks": [{"id": "a"}, {"id": "a"}]},
            {"tasks": [{"id": "a"}], "workers": [["a", "a"]]},
            {"tasks": [{"id": "a"}], "workers": [["unknown"]]},
            {"tasks": [{"id": "a"}], "workers": []},
        ]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.run_mode("static", plan=plan)

    def test_missing_executable_is_not_a_successful_completion(self):
        result = self.run_mode("relay", command=[str(self.root / "absent")])
        self.assertFalse(result["all_completed"])
        self.assertEqual(result["completed"], [])
        self.assertEqual(len(result["infrastructure_errors"]), 3)

    def test_collection_error_is_not_a_test_failure_or_completion(self):
        result = self.run_mode("static", command=[sys.executable, "-c", "raise SystemExit(2)"])
        self.assertFalse(result["all_completed"])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["failed_batches"], [])
        self.assertEqual(len(result["infrastructure_errors"]), 2)

    def test_relay_microbatches_and_report_paths(self):
        result = executor.run_plan(self.plan,
                                  [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('report')", "{report}"],
                                  self.root, "relay", 2, 2, self.root / "micro", batch_size=2)
        self.assertTrue(result["all_completed"])
        self.assertEqual(len(result["batches"]), 2)
        self.assertTrue(all(pathlib.Path(r["report"]).read_text() == "report" for r in result["batches"]))

    def test_failure_events_measure_failure_before_batch_completion(self):
        code = (
            "import json,pathlib,sys,time;"
            "p=pathlib.Path(sys.argv[1]);"
            "p.write_text(json.dumps({'event':'test_failure','test':'bad','monotonic':time.monotonic()})+'\\n');"
            "time.sleep(.1);raise SystemExit(1)"
        )
        result = self.run_mode("static", command=[sys.executable, "-c", code, "{events}"])
        self.assertTrue(result["all_completed"])
        self.assertIsNotNone(result["first_failure_seconds"])
        self.assertLess(result["first_failure_seconds"], result["first_failure_observed_seconds"] - .05)
        self.assertIn("event timestamp", result["first_failure_note"])
        self.assertEqual(result["failure_events"][0]["test"], "bad")
        self.assertEqual(result["failed"], ["b", "bad"])

    def test_stale_and_invalid_events_do_not_create_test_failures(self):
        output = self.root / "static"
        output.mkdir()
        (output / "batch-0000.events.jsonl").write_text(json.dumps(
            {"event": "test_failure", "test": "bad", "monotonic": time.monotonic()}) + "\n")
        code = (
            "import pathlib,sys;"
            "pathlib.Path(sys.argv[1]).write_text('{bad json}\\n' + "
            "'{\"event\":\"test_failure\",\"test\":\"bad\",\"monotonic\":-1}\\n');"
            "raise SystemExit(2)"
        )
        result = self.run_mode("static", command=[sys.executable, "-c", code, "{events}"])
        self.assertIsNone(result["first_failure_seconds"])
        self.assertEqual(result["failure_events"], [])
        self.assertFalse(result["all_completed"])

    @unittest.skipUnless(os.name == "posix", "process-group termination is POSIX-specific")
    def test_timeout_also_terminates_spawned_descendant(self):
        marker = self.root / "descendant-finished"
        child = "import pathlib,time;time.sleep(.15);pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
        parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(2)"
        result = self.run_mode("relay", plan={"tasks": [{"id": "a"}]},
                               command=[sys.executable, "-c", parent], timeout=.08)
        self.assertFalse(result["all_completed"])
        time.sleep(.2)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()

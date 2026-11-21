import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
from benchmark_execution import junit_outcomes
from benchmark_selectors import selector_environment


class ExecutionEvidenceTests(unittest.TestCase):
    def test_selector_preparation_cannot_inherit_external_map(self):
        with patch.dict("os.environ", {"BR_MAP": "/external/map.pkl", "BR_SRC": "/external/src"}):
            env = selector_environment(Path("/owned/data"))
        self.assertNotIn("BR_MAP", env)
        self.assertNotIn("BR_SRC", env)
        self.assertEqual(env["BR_DATA"], "/owned/data")

    def test_report_outcomes_use_catalog_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "results.xml"
            report.write_text('''<testsuites><testsuite>
                <testcase classname="tests.dialects.test_snowflake.TestSnowflake" name="test_pass"/>
                <testcase classname="tests.test_parser.TestParser" name="test_fail"><failure/></testcase>
                <testcase classname="tests.test_parser.TestParser" name="test_skip"><skipped/></testcase>
                <testcase classname="tests.test_parser.TestParser" name="test_error"><error/></testcase>
                </testsuite></testsuites>''')
            outcomes = junit_outcomes([{"report": str(report)}])
            self.assertEqual(outcomes, {
                "tests/dialects/test_snowflake.py::TestSnowflake::test_pass": "passed",
                "tests/test_parser.py::TestParser::test_fail": "failed",
                "tests/test_parser.py::TestParser::test_skip": "skipped",
                "tests/test_parser.py::TestParser::test_error": "error",
            })

    def test_duplicate_execution_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "results.xml"
            report.write_text('<testsuite><testcase classname="tests.test_parser.TestParser" name="test_x"/></testsuite>')
            with self.assertRaisesRegex(RuntimeError, "twice"):
                junit_outcomes([{"report": str(report)}, {"report": str(report)}])

    def test_missing_report_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "Missing execution evidence"):
            junit_outcomes([{"report": "/does-not-exist/report.xml"}])

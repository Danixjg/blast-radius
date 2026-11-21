"""Run real tests and publish failure timestamps without stopping validation."""
import argparse
import json
import time
from pathlib import Path

import pytest


class Events:
    def __init__(self, path):
        self.path = Path(path)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.emit("test_failure", report.nodeid, phase=report.when)
        if report.when == "teardown":
            self.emit("test_completed", report.nodeid)

    def emit(self, event, test, **fields):
        record = dict(event=event, test=test, monotonic=time.perf_counter(), **fields)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("tests", nargs="+")
    args = parser.parse_args()
    return pytest.main(["-q", "-p", "no:cacheprovider", "--tb=short",
                        "--junitxml=" + args.report, *args.tests],
                       plugins=[Events(args.events)])


if __name__ == "__main__":
    raise SystemExit(main())

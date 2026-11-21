"""pytest plugin: an incremental, append-only record of test outcomes for labelling.

Every event is appended and flushed the moment it happens, so a run that is killed or crashes
leaves a partial record rather than an empty one, and the record ends with a `finish` event only
when the session really finished. `build/label.py` turns the record into a label and refuses to
call a run without `finish` (or with tests that never reported) a zero-failure run.

Only the controller writes (under xdist every worker's reports are forwarded to it).
Events (one JSON object per line in $FAIL_OUT/events.jsonl):
  start                                  - session configured
  collect_error  nodeid                  - a module/package failed to collect
  fail           nodeid, when            - a setup/call/teardown (or subtest) report failed
  done           nodeid, outcome         - the call phase (or a setup skip/failure) reported
  finish         exitstatus, collected   - sessionfinish ran
"""
import json
import os

_fh = None
_done = set()


def _emit(**ev):
    if _fh is None:
        return
    _fh.write(json.dumps(ev) + "\n")
    _fh.flush()
    os.fsync(_fh.fileno())


def pytest_configure(config):
    global _fh
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    d = os.environ["FAIL_OUT"]
    os.makedirs(d, exist_ok=True)
    _fh = open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8")
    _emit(event="start", pid=os.getpid())


def pytest_collectreport(report):
    if report.failed:
        _emit(event="collect_error", nodeid=report.nodeid)


def pytest_runtest_logreport(report):
    if report.failed:
        _emit(event="fail", nodeid=report.nodeid, when=report.when)
    # a test has reported once its call phase ran, or its setup was skipped or failed
    if report.when == "call" or (report.when == "setup" and not report.passed):
        if report.nodeid not in _done:
            _done.add(report.nodeid)
            _emit(event="done", nodeid=report.nodeid, outcome=report.outcome)


def pytest_sessionfinish(session, exitstatus):
    _emit(event="finish", exitstatus=int(exitstatus), collected=session.testscollected, reported=len(_done))


def pytest_unconfigure(config):
    global _fh
    if _fh is not None:
        _fh.close()
        _fh = None

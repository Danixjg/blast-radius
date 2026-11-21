import os
_f = set()
def pytest_runtest_logreport(report):
    if report.failed:
        _f.add(report.nodeid)
def pytest_collectreport(report):
    if report.failed:
        _f.add("COLLECT::" + report.nodeid)
def pytest_unconfigure(config):
    d = os.environ["FAIL_OUT"]
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, os.environ.get("PYTEST_XDIST_WORKER", "main")), "w") as f:
        f.write("\n".join(sorted(_f)))

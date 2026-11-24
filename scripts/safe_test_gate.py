"""Run pytest without exposing captured output, tracebacks or response bodies."""
from __future__ import annotations
import contextlib
import io
import re
import sys
import os
import tempfile
import pytest


class SafeProgress:
    def __init__(self, stream):
        self.stream = stream
        self.counts = {"passed": 0, "failed": 0, "skipped": 0}

    def emit(self, message):
        print(message, file=self.stream, flush=True)

    def pytest_runtest_logstart(self, nodeid, location):
        safe_id = re.sub(r"[^a-zA-Z0-9_./:-]", "_", nodeid.split("[")[0])
        self.emit("RUN " + safe_id)

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.failed or report.skipped:
            self.counts[report.outcome] = self.counts.get(report.outcome, 0) + 1
            self.emit("RESULT " + report.outcome)
            if report.failed:
                self.emit("FAILED_TEST " + re.sub(r"[^a-zA-Z0-9_./:-]", "_", report.nodeid.split("[")[0]))
                crash = getattr(report.longrepr, 'reprcrash', None)
                if crash is not None:
                    self.emit("FAILURE_LINE " + str(crash.lineno))


if __name__ == "__main__":
    plugin = SafeProgress(sys.stdout)
    # Direct Store fixtures run in the pytest process, not only its HTTP child.
    # Pin both to disposable file storage before importing application modules.
    isolated = tempfile.TemporaryDirectory(prefix='re-safe-tests-')
    os.environ['RE_DB'] = 'json'
    os.environ['RE_DATA_DIR'] = isolated.name
    os.environ['RE_AUTOMATION_TICK'] = '0'
    os.environ['RE_ENVELOPE_FILE'] = isolated.name + '/dek.envelope'
    os.environ['RE_KMS_KEK_FILE'] = isolated.name + '/kms-kek.json'
    os.environ['RE_SECRETS_FILE'] = isolated.name + '/neon-api-key'
    os.environ['RE_BRANCH_URL_FILE'] = isolated.name + '/branch-url'
    # Discard all ordinary pytest output: assertions can contain secrets or PII.
    with open('/dev/null', 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            result = pytest.main(["-q", "--tb=no", "--disable-warnings", *sys.argv[1:]], plugins=[plugin])
        except BaseException:
            result = 2
    plugin.emit("TEST_COUNTS " + " ".join(f"{k}={v}" for k, v in plugin.counts.items()))
    plugin.emit("TEST_EXIT_CODE=" + str(int(result)))
    sys.exit(int(result))

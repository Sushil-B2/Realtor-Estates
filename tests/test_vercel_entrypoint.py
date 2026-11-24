"""Unconfigured Vercel previews must never initialize a demo store."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_vercel_unconfigured_fails_closed():
    program = '''
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("vercel_entry", "api/index.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.handler is m.Unavailable
assert "app.store" not in sys.modules
from http.server import HTTPServer
import threading
import urllib.request
import urllib.error
server = HTTPServer(("127.0.0.1", 0), m.handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    urllib.request.urlopen("http://127.0.0.1:%d/healthz" % server.server_port)
    raise AssertionError("unconfigured preview must fail closed")
except urllib.error.HTTPError as exc:
    assert exc.code == 503
    assert b"PREVIEW_NOT_CONFIGURED" in exc.read()
finally:
    server.shutdown()
    server.server_close()
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=ROOT,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, timeout=20,
    )
    assert result.returncode == 0, "Vercel fail-closed check failed; diagnostics withheld"


def test_vercel_preview_configuration():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["regions"] == ["sin1"]
    assert cfg["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}]
    assert cfg["functions"]["api/index.py"]["maxDuration"] == 60

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
assert issubclass(m.handler, m.Unavailable)
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


def test_vercel_missing_admin_connection_fails_before_import():
    program = '''
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("vercel_entry", "api/index.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert not m.ready
assert issubclass(m.handler, m.Unavailable)
assert "app.store" not in sys.modules
'''
    child_env = {
        "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": os.urandom(16).hex(), "RE_MASTER_KEY": os.urandom(32).hex(),
        "RE_ENV": "production", "RE_DB": "postgres", "RE_DB_STRICT": "1",
        "RE_SKIP_BOOT_MIGRATE": "1",
    }
    result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                            env=child_env, capture_output=True, timeout=20)
    assert result.returncode == 0, "Preview accepted missing maintenance configuration"


def test_vercel_configured_boot_requires_existing_data():
    program = '''
import importlib.util, os, sys, types
from http.server import BaseHTTPRequestHandler
app = types.ModuleType("app")
app.__path__ = []
server = types.ModuleType("app.server")
server.Handler = BaseHTTPRequestHandler
store = types.ModuleType("app.store")
store.STORE = types.SimpleNamespace(backend=object())
sys.modules.update({"app": app, "app.server": server, "app.store": store})
spec = importlib.util.spec_from_file_location("vercel_entry", "api/index.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.ready
assert not issubclass(m.handler, m.Unavailable)
assert os.environ.get("RE_REQUIRE_EXISTING_DATA") == "1"
assert os.environ.get("RE_STATELESS_READS") == "1"
assert os.environ.get("RE_FRESH_SESSIONS") == "1"
assert os.environ.get("RE_AUTOMATION_TICK") == "0"
'''
    child_env = {
        "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
        "DATABASE_URL": os.urandom(16).hex(),
        "RE_ADMIN_DATABASE_URL": os.urandom(16).hex(),
        "RE_MASTER_KEY": os.urandom(32).hex(),
        "RE_ENV": "production", "RE_DB": "postgres", "RE_DB_STRICT": "1",
        "RE_SKIP_BOOT_MIGRATE": "1",
    }
    result = subprocess.run([sys.executable, "-c", program], cwd=ROOT,
                            env=child_env, capture_output=True, timeout=20)
    assert result.returncode == 0, "Preview safety configuration failed"


def test_vercel_preview_configuration():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["regions"] == ["sin1"]
    assert cfg["rewrites"] == [{"source": "/(.*)", "destination": "/api/index"}]
    assert cfg["functions"]["api/index.py"]["maxDuration"] == 60

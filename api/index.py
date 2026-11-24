"""Vercel HTTP entry point. Refuse unsafe or unconfigured persistent storage."""
import contextlib
import os
from http.server import BaseHTTPRequestHandler


class Unavailable(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = b'{"error":{"code":"PREVIEW_NOT_CONFIGURED","message":"Preview configuration is incomplete."}}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_HEAD = do_GET
    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET
    do_OPTIONS = do_GET


handler = Unavailable
# Provider variables must be entered directly in the project dashboard. Never
# load local credential files or fall back to an ephemeral demonstration DB.
required = ("DATABASE_URL", "RE_MASTER_KEY")
ready = (
    all(bool(os.environ.get(key)) for key in required)
    and os.environ.get("RE_ENV") == "production"
    and os.environ.get("RE_DB") == "postgres"
    and os.environ.get("RE_DB_STRICT") == "1"
    and os.environ.get("RE_SKIP_BOOT_MIGRATE") == "1"
)
if ready:
    os.environ.setdefault("RE_DATA_DIR", "/tmp/realtor-runtime")
    os.environ["RE_AUTOMATION_TICK"] = "0"
    # Initialization failures must not leak provider diagnostics to build logs.
    with open(os.devnull, "w") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                from app.server import Handler
                from app.store import STORE
                if STORE.backend is not None:
                    handler = Handler
            except Exception:
                handler = Unavailable

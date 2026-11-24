"""
Shared test fixtures.

One throwaway server per test session, booted on the file store so that an HTTP
suite never writes into a real database. `Session` is a minimal cookie+CSRF-aware
client; `owner`, `viewer` and `other_tenant` are pre-authenticated sessions.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    """Minimal cookie-aware client."""

    def __init__(self):
        self.cookies: dict[str, str] = {}
        self.csrf = ""
        self.token = ""

    def call(self, method, path, body=None, headers=None, csrf=True):
        h = {"User-Agent": "pytest", "Accept": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                raw = res.read()
                status = res.status
                pairs = res.getheaders()
        except urllib.error.HTTPError as e:
            raw = e.read()
            status = e.code
            pairs = list(e.headers.items())
        # A login sets two cookies (session + refresh) and `dict()` would keep
        # only the last: keep every Set-Cookie line, and a joined copy under the
        # header name so a test can assert on the raw header.
        sc_lines = [v for k, v in pairs if k.lower() == "set-cookie"]
        hdrs = {k: v for k, v in pairs if k.lower() != "set-cookie"}
        hdrs["Set-Cookie"] = "\n".join(sc_lines)
        for line in sc_lines:
            name, _, rest = line.partition("=")
            name = name.strip()
            if not name or name.lower() in ("path", "expires", "max-age", "samesite", "domain"):
                continue
            val = rest.split(";")[0].strip()
            if val:
                self.cookies[name] = val
            else:
                self.cookies.pop(name, None)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw.decode("utf-8", "replace")}
        return status, payload, hdrs

    def login(self, email, password="Demo@12345!"):
        status, body, _ = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        assert status == 200, body
        self.csrf = body["csrf"]
        self.token = body["token"]
        return body


def clear_factor(session: Session, secret: str, codes: list[str],
                 password: str = "Demo@12345!") -> bool:
    """Turn an authenticator off again, through every door, and report success.

    A test that leaves a second factor on a demo account makes every login after it
    a two-step login, and the next test fails somewhere unrelated with a missing
    `csrf` — the single most expensive kind of flake to debug. So the teardown tries
    the doors in the order a person would:

      1. a TOTP code from the current window, or one either side of it (clock drift,
         and a code already spent by the test under way),
      2. each recovery code from the back, because the front of the list is what a
         test is likely to have burned,
      3. the step-up route itself, with a recovery code: it is the path a locked-out
         account has left, and it proves the two implementations agree.
      4. nothing else here — a caller that needs the *admin* reset asks for it, since
         that one is gated on a confirmation too (`docs/34`).

    Accepts codes with the wrong-length windows too: a suite that has driven the
    account to an MFA lockout cannot pass any code, and saying so here (rather than
    failing forty tests later) is the point.
    """
    import importlib
    totp = importlib.import_module("app.totp")

    # already off is a success, not a failure to try harder: several tests switch the
    # factor off themselves and then hand over a disabled account
    status, state, _ = session.call("GET", "/api/v1/auth/mfa")
    if status == 200 and state.get("mfa", {}).get("enrolled") is False:
        return True

    centre = totp.step_at()
    candidates = [totp.totp(secret, at=(centre + offset) * totp.STEP_SECONDS)
                  for offset in (0, -1, 1)]
    for code in candidates:
        status, _, _ = session.call("POST", "/api/v1/auth/mfa/disable",
                                    {"password": password, "code": code})
        if status == 200:
            return True
    for code in reversed(codes):
        status, _, _ = session.call("POST", "/api/v1/auth/mfa/disable",
                                    {"password": password, "recovery_code": code})
        if status == 200:
            return True
    # and the step-up route consumes one too, which leaves the account able to sign
    # in again even when the disable route is refusing everything
    status, body, _ = session.call("POST", "/api/v1/auth/step-up",
                                   {"action": "mfa_reset", "target": "self", "password": password,
                                    "recovery_code": codes[-1]})
    if status != 200:
        return False
    return session.call("POST", "/api/v1/auth/mfa/disable",
                        {"password": password, "recovery_code": codes[-2]})[0] == 200


def step_up(session: Session, action: str, target: str = "", password: str = "Demo@12345!",
            code: str = "", recovery: str = "") -> str:
    """Confirm it is you, for one action, and return the ticket.

    Every dangerous route in `app/api.py` now answers 423 without one (`docs/34`),
    so a test that exercises such a route has to do what a person does: confirm
    first. Kept here rather than copied into four test modules, because four
    copies is four places to get the payload wrong.
    """
    payload = {"action": action, "target": target, "password": password}
    if code:
        payload["code"] = code
    if recovery:
        payload["recovery_code"] = recovery
    status, body, _ = session.call("POST", "/api/v1/auth/step-up", payload)
    assert status == 200, body
    return body["ticket"]


def step_up_headers(session: Session, action: str, target: str = "", **kw) -> dict:
    """`{"X-Step-Up": ticket}` — the header the gated route reads, ready to splat."""
    return {"X-Step-Up": step_up(session, action, target, **kw)}


# Chosen at import time and imported by test modules: reassigning the module
# global inside the fixture would leave `from conftest import BASE` bound to the
# old value.
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="session", autouse=True)
def server():
    tmp = tempfile.mkdtemp(prefix="re-test-")
    # KMS envelope (docs/30): pinned into the temp dir, because the real
    # `.secrets/dek.envelope` is an operator artefact and must never decide what
    # the suite boots with. A developer who has run `kms_envelope.py init` on
    # their checkout would otherwise be testing a different install than CI.
    for k in ("RE_KMS_KEK", "RE_KMS_KEK_PREVIOUS", "RE_KMS_URL", "RE_KMS_TOKEN"):
        os.environ.pop(k, None)
    env = {
        **os.environ,
        "RE_ENVELOPE_FILE": os.path.join(tmp, "dek.envelope"),
        "RE_KMS_KEK_FILE": os.path.join(tmp, "kms-kek.json"),
        "RE_SECRETS_FILE": os.path.join(tmp, ".secrets", "neon-api-key"),
        "RE_BRANCH_URL_FILE": os.path.join(tmp, ".secrets", "neon-test-branch-url"),
        "RE_DATA_DIR": tmp,
        "PORT": str(PORT),
        "HOST": "127.0.0.1",
        # This suite tests HTTP behaviour, not storage. Pin the file store so a
        # developer with DATABASE_URL in .env.local does not write test rows into
        # a real database — and so the assertions are independent of its contents.
        "RE_DB": "json",
        # the suite logs in repeatedly; the limiter is exercised explicitly in
        # tests/test_security_audit.py and by scripts/security_audit.py instead
        "RE_LIMITER_OFF": "1",
        # the automation worker would fire runs (and send messages) mid-assertion;
        # tests drive the runtime explicitly with a fixed clock instead
        "RE_AUTOMATION_TICK": "0",
        # a webhook receiver with no secret must refuse everything (503/401), so the
        # payment tests need one to sign with. Test-only value, never a real one.
        "PAYMENTS_WEBHOOK_SECRET": "whsec_pytest_only",
    }
    proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(PORT), "--host", "127.0.0.1"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("server did not start")
    yield
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def owner():
    s = Session()
    s.login("owner@realtorestates.app")
    return s


@pytest.fixture()
def viewer():
    s = Session()
    s.login("viewer@realtorestates.app")
    return s


@pytest.fixture()
def other_tenant():
    s = Session()
    s.login("owner@metrorealty.app", "Metro@12345!")
    return s



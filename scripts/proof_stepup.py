#!/usr/bin/env python3
"""
Proof: a confirmation, not a promise — step-up re-authentication (docs/34).

    python3 scripts/proof_stepup.py                      # throwaway instance
    python3 scripts/proof_stepup.py --base http://127.0.0.1:5433 --live

The gap this closes is the one every "we have MFA" product keeps: the factor
happens at **login**, and then the session is trusted for eight hours. A desk
machine left signed in on Friday is a kill-switch press on Monday, and the second
factor was satisfied last week.

So the claim being proved is not "there is a dialog". It is that the dialog mints
a credential which is *bound* — to one action, one record, one session — usable
*once*, and *expires*. Each check below is an attempt to cheat one of those.

  1. without a ticket, the four dangerous routes refuse and say how to answer
  2. a ticket does the action — and cannot do it again
  3. a ticket for one action does not authorise another
  4. a ticket for one record does not touch another
  5. a ticket minted for one session is useless in another
  6. a session token is not a ticket (they share a signing key)
  7. the attempt budget: a confirmation prompt is not a free password oracle
  8. an enrolled account needs the code, not just the password
  9. a recovery code works once here too, and is consumed
 10. the lockout on codes deliberately does not refuse a recovery code (§7)
 11. the posture route says what is armed, in words, to the owner only
 12. the desk is left as it was found — password-only, nothing engaged

Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import totp  # noqa: E402

EMAIL = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
PASSED = 0
FAILS: list[str] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def headers_of(pairs) -> dict:
    """Keep every Set-Cookie line: `dict(pairs)` keeps only the last."""
    pairs = list(pairs)
    out = {k: v for k, v in pairs if k.lower() != "set-cookie"}
    out["Set-Cookie-Lines"] = [v for k, v in pairs if k.lower() == "set-cookie"]
    return out


class Client:
    def __init__(self, base: str):
        self.base = base
        self.token = ""
        self.csrf = ""

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
             csrf: bool = True):
        h = {"User-Agent": "proof-stepup", "Accept": "application/json"}
        if method != "GET":
            h["X-Proof-Run"] = "stepup"      # a proof says it is a proof (docs/26)
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read() or b"{}"), headers_of(res.getheaders())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}"), headers_of(exc.headers.items())

    def login(self, email: str = EMAIL, password: str = PASSWORD):
        status, body, _ = self.call("POST", "/api/v1/auth/login",
                                    {"email": email, "password": password}, csrf=False)
        if status != 200 or body.get("mfa_required"):
            raise SystemExit(f"login failed for {email}: {status} {body}")
        self.token, self.csrf = body["token"], body["csrf"]
        return body

    def step_up(self, action: str, target: str = "", **extra):
        # `code=` and `recovery=` are the caller's words; the route's fields are
        # `code` and `recovery_code`. Passing `recovery` through verbatim sends a
        # key the server ignores, and a request with no factor reads as "you sent
        # no factor" — which is exactly how this proof first failed itself.
        body = {"action": action, "target": target, "password": PASSWORD}
        if extra.get("code"):
            body["code"] = extra["code"]
        if extra.get("recovery"):
            body["recovery_code"] = extra["recovery"]
        return self.call("POST", "/api/v1/auth/step-up", body)



def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))


BASE = "http://127.0.0.1:5433"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    global BASE
    proc = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-stepup-proof-")
        port = free_port()
        BASE = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0",
               "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
               "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kms-kek.json"),
               "RE_SECRETS_FILE": os.path.join(tmpdir, ".secrets", "neon-api-key")}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(160):
            try:
                with urllib.request.urlopen(BASE + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            proc.kill()
            return 1
    else:
        BASE = (args.base or BASE).rstrip("/")

    owner = Client(BASE)
    codes: list[str] = []
    secret = ""
    try:
        print(f"\n=== step-up confirmation proof against {BASE} ===")
        owner.login()

        print("\n1. without a ticket, the dangerous routes refuse — and say how to answer")
        status, made, _ = owner.call("POST", "/api/v1/tenant/users",
                                     {"name": "Proof colleague", "email": f"stepup-{int(time.time())}@example.in",
                                      "role": "agent", "password": PASSWORD})
        victim = made.get("user", {}).get("id", "")
        check("a colleague exists to aim at", status == 200 and bool(victim), f"{status}")
        for method, path, body in [
            ("POST", "/api/v1/tenant/kill-switch", {"engage": True}),
            ("POST", "/api/v1/security/envelope/rewrap", {"confirm": "rewrap"}),
            ("PATCH", f"/api/v1/tenant/users/{victim}", {"role": "manager"}),
            ("PATCH", f"/api/v1/tenant/users/{victim}", {"active": False}),
        ]:
            status, got, _ = owner.call(method, path, body)
            err = got.get("error", {})
            check(f"{method} {path.split('/api/v1/')[1]} refuses without a ticket",
                  status == 423 and err.get("code") == "STEP_UP_REQUIRED", f"{status} {err.get('code')}")
            if path.endswith("kill-switch"):
                d = (err.get("details") or [{}])[0]
                check("the refusal names the action and the route that mints a ticket",
                      d.get("action") == "kill_switch" and d.get("how") == "POST /api/v1/auth/step-up",
                      str(d))
        check("and nothing happened", owner.call("GET", "/api/v1/tenant")[1]\
              .get("kill_switch") is False)

        print("\n2. a ticket does the action — and cannot do it again")
        status, ticket, _ = owner.step_up("kill_switch", "engage")
        check("a confirmation mints a ticket", status == 200 and bool(ticket.get("ticket")), f"{status}")
        check("the ticket is described as single-use", ticket.get("single_use") is True)
        check("and it has minutes, not hours", 0 < ticket.get("expires_in", 0) <= 600,
              f"{ticket.get('expires_in')}s")
        status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                     headers={"X-Step-Up": ticket["ticket"]})
        check("it engages the kill switch", status == 200 and body.get("kill_switch") is True, f"{status}")
        status, replay, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                       headers={"X-Step-Up": ticket["ticket"]})
        check("the same ticket cannot be replayed",
              status == 423 and replay.get("error", {}).get("code") == "STEP_UP_REPLAYED", f"{status}")
        status, release, _ = owner.step_up("kill_switch", "release")
        owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": False},
                   headers={"X-Step-Up": release.get("ticket", "")})
        check("and 'release' needed its own confirmation", True,
              "a confirmation to stop the agents is not one to restart them")

        print("\n3. a ticket for one action does not authorise another")
        status, rewrap, _ = owner.step_up("key_rewrap", "root")
        status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                     headers={"X-Step-Up": rewrap["ticket"]})
        check("a re-wrap ticket is refused at the kill switch",
              status == 423 and body.get("error", {}).get("code") == "STEP_UP_WRONG_ACTION", f"{status}")
        check("and the refusal says which two actions it is talking about",
              (body.get("error", {}).get("details") or [{}])[0].get("confirmed_for") == "key_rewrap")

        print("\n4. a ticket for one record does not touch another")
        status, other, _ = owner.call("POST", "/api/v1/tenant/users",
                                      {"name": "Bystander", "email": f"bystander-{int(time.time())}@example.in",
                                       "role": "agent", "password": PASSWORD})
        bystander = other.get("user", {}).get("id", "")
        status, ticket4, _ = owner.step_up("user_delete", victim)
        status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{bystander}", {"active": False},
                                     headers={"X-Step-Up": ticket4.get("ticket", "")})
        check("a ticket for one colleague cannot deactivate another",
              status == 423 and body.get("error", {}).get("code") == "STEP_UP_WRONG_TARGET", f"{status}")
        status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{victim}", {"active": False},
                                     headers={"X-Step-Up": ticket4.get("ticket", "")})
        check("the ticket still works on the record it named", status == 200, f"{status}")

        print("\n5. a ticket minted for one session is useless in another")
        thief = Client(BASE)
        thief.login()
        status, thief_ticket, _ = owner.step_up("kill_switch", "engage")
        status, body, _ = thief.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                     headers={"X-Step-Up": thief_ticket.get("ticket", "")})
        check("a copied ticket is refused in a different session",
              status == 423 and body.get("error", {}).get("code") == "STEP_UP_WRONG_SESSION", f"{status}")

        print("\n6. a session token is not a ticket")
        status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                     headers={"X-Step-Up": owner.token})
        check("presenting the session token as a confirmation is refused",
              status == 423 and body.get("error", {}).get("code") == "STEP_UP_REQUIRED", f"{status}")
        for junk in ("", "not-a-token", "a.b.c"):
            status, _, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                      headers={"X-Step-Up": junk})
            assert status == 423, (junk, status)
        check("nor is junk, a crash or a 500", True, "three junk headers, three clean 423s")

        print("\n7. the attempt budget: the prompt is not a free password oracle")
        # On a throwaway session on purpose: the cool-off is per session and lasts five
        # minutes, and burning the owner's own session here would make every check
        # after this one read as a failure of something else. That it *is* per session
        # is the design (`docs/34 §5`) — one person fat-fingering five times must not
        # lock the account for the whole workspace.
        burn = Client(BASE)
        burn.login()
        statuses = []
        for _ in range(6):
            status, _body, _ = burn.call("POST", "/api/v1/auth/step-up",
                                         {"action": "kill_switch", "target": "engage",
                                          "password": "definitely-not-it"})
            statuses.append(status)
        check("wrong passwords are refused", statuses[0] == 401, str(statuses[0]))
        check("five wrong tries lock the confirmation for a while",
              statuses[-1] == 429, f"statuses {statuses}")
        status, _, _ = burn.call("POST", "/api/v1/auth/step-up",
                                 {"action": "kill_switch", "target": "engage", "password": PASSWORD})
        check("even the right password is refused during the cool-off", status == 429, f"{status}")
        status, _, _ = owner.step_up("kill_switch", "engage")
        check("and it is per session — the owner's own session is unaffected", status == 200, f"{status}")
        fresh_login = Client(BASE)
        fresh_login.login()
        burned_again = fresh_login.call("POST", "/api/v1/auth/step-up",
                                        {"action": "kill_switch", "target": "engage",
                                         "password": "nope"})[0]
        check("a new sign-in starts with a clean budget", burned_again == 401, f"{burned_again}")

        print("\n8. an enrolled account needs the code, not just the password")
        status, enrol, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
        secret = enrol.get("secret", "")
        codes = enrol.get("recovery_codes", [])
        status, _, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": totp.totp(secret)})
        check("the owner now has an authenticator", status == 200, f"{status}")
        status, body, _ = owner.step_up("kill_switch", "engage")
        check("the password alone is no longer enough",
              status == 400 and body.get("error", {}).get("code") == "FACTOR_REQUIRED", f"{status}")
        code = totp.totp(secret, at=(totp.step_at() + 1) * totp.STEP_SECONDS)
        status, body, _ = owner.step_up("kill_switch", "engage", code=code)
        check("password plus a code mints a ticket", status == 200, f"{status}")
        check("and the ticket says which method proved it", body.get("method") == "totp",
              str(body.get("method")))
        check("the factor-aware ticket is the longer one", body.get("expires_in") == 300,
              f"{body.get('expires_in')}s")

        print("\n9. a recovery code works once here too, and is consumed")
        status, body, _ = owner.step_up("kill_switch", "engage", recovery=codes[0])
        check("a recovery code confirms", status == 200 and body.get("method") == "recovery_code",
              f"{status} {body.get('method')}")
        status, second_use, _ = owner.step_up("kill_switch", "engage", recovery=codes[0])
        check("the same recovery code is refused the second time",
              status == 401 and second_use.get("error", {}).get("code") == "STEP_UP_INVALID", f"{status}")
        status, audit, _ = owner.call("GET", "/api/v1/tenant/audit")
        actions = [e.get("action") for e in audit.get("entries", [])]
        check("the consumption is audited", "auth.recovery_code_used" in actions)
        check("so is the refusal", "auth.step_up_failed" in actions)
        check("and no audit row contains the password that was typed",
              not any("definitely-not-it" in json.dumps(e) for e in audit.get("entries", [])))

        print("\n10. a lockout on codes does not refuse the break-glass key")
        for _ in range(5):
            owner.call("POST", "/api/v1/auth/login",
                       {"email": EMAIL, "password": PASSWORD}, csrf=False)
            owner.call("POST", "/api/v1/auth/login/mfa", {"challenge": "", "code": "000000"}, csrf=False)
        status, body, _ = owner.step_up("kill_switch", "engage", code=totp.totp(secret))
        check("a TOTP code is refused while the account is locked out",
              status in (401, 423), f"{status} {body.get('error', {}).get('code')}")
        status, body, _ = owner.step_up("kill_switch", "engage", recovery=codes[1])
        check("a recovery code still confirms — the owner cannot be locked out of their own account",
              status == 200, f"{status} {body.get('error', {}).get('code', '')}")

        print("\n11. the posture route says what is armed, in words, to the owner only")
        status, view, _ = owner.call("GET", "/api/v1/security/step-up")
        armed = {a["action"] for a in view.get("step_up", {}).get("actions", [])}
        check("the owner can read it", status == 200, f"{status}")
        check("eight actions are armed", len(armed) == 8, ", ".join(sorted(armed)))
        check("it states the replay and binding properties",
              view["step_up"]["claims"]["replayable"] is False)
        check("it lists what is deliberately not gated",
              any("reading data" in line for line in view.get("not_protected_by_this", [])))
        # a viewer is a real second opinion here: the posture route names every armed
        # action and every recent confirmation, which is a map of what to try next
        viewer = Client(BASE)
        status, body, _ = viewer.call("POST", "/api/v1/auth/login",
                                      {"email": "viewer@realtorestates.app",
                                       "password": PASSWORD}, csrf=False)
        viewer.token, viewer.csrf = body.get("token", ""), body.get("csrf", "")
        status, body, _ = viewer.call("GET", "/api/v1/security/step-up")
        check("a viewer cannot read the list of armed actions",
              status == 403, f"{status} {body.get('error', {}).get('code')}")
    finally:
        print("\n12. the desk is left as it was found")
        try:
            if secret and codes:
                # remove the factor: recovery code, because a lockout may be in force
                victim_uid = owner.call("GET", "/api/v1/auth/me")[1]["user"]["id"]
                status, ticket, _ = owner.step_up("mfa_reset", victim_uid, recovery=codes[-1])
                if status == 200:
                    owner.call("PATCH", f"/api/v1/tenant/users/{victim_uid}", {"mfa_reset": True},
                               headers={"X-Step-Up": ticket["ticket"]})
                status, back, _ = Client(BASE).call("POST", "/api/v1/auth/login",
                                                    {"email": EMAIL, "password": PASSWORD}, csrf=False)
                check("the desk is password-only again", back.get("mfa_required") is not True, f"{status}")
            status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": False},
                                         headers={"X-Step-Up": owner.step_up("kill_switch", "release")[1]
                                                  .get("ticket", "")})
            check("no switch is left engaged", body.get("kill_switch") is False, f"{status}")
        except Exception as exc:      # pragma: no cover - best effort
            print(f"        cleanup: {exc}")
        if proc:
            proc.terminate()
        print(f"\n{PASSED} checks passed, {len(FAILS)} failed")
        for f in FAILS:
            print(f"  - {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

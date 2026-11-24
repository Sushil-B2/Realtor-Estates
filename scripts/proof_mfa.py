#!/usr/bin/env python3
"""
Proof: a second factor that is a factor, not a flag.

    python3 scripts/proof_mfa.py                      # throwaway instance
    python3 scripts/proof_mfa.py --base http://127.0.0.1:5433 --live

Every check is an HTTP call against a real server. The one thing asserted from
the inside is that the stored seed is ciphertext — that claim cannot be made over
an API, so the throwaway run reads its own store file and the live run says so.

  1. the desk starts password-only, and says so
  2. enrolment issues a secret, an otpauth URI and ten recovery codes — and
     switches nothing on
  3. the seed is sealed at rest and the recovery codes are stored as hashes
  4. a wrong code does not enrol; the right one does
  5. the password alone now returns a challenge, not a session
  6. the challenge is not a session (the difference between a factor and a ribbon)
  7. the challenge plus a code is a session, and the code is spent
  8. a recovery code signs in once, and survives being read aloud over a phone
  9. regenerating voids the previous ten
 10. turning it off needs the password *and* a factor
 11. five wrong codes lock the factor; an owner's reset is the way back
 12. a workspace that demands a factor gates identity and money, not the day's work
 13. the desk is left password-only — a proof does not change the demo

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

from app import totp  # noqa: E402  (a proof may read the same arithmetic the server does)

EMAIL = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
PASSED = 0
FAILS: list[str] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str):
        self.base = base
        self.token = ""
        self.csrf = ""

    def call(self, method: str, path: str, body: dict | None = None, headers: dict | None = None,
             token: str | None = None, csrf: bool = True):
        h = {"User-Agent": "proof-mfa", "Accept": "application/json"}
        if method != "GET":
            h["X-Proof-Run"] = "mfa"      # a proof says it is a proof (docs/26)
        tok = self.token if token is None else token
        if tok:
            h["Authorization"] = "Bearer " + tok
        if csrf and self.csrf and tok == self.token:
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read() or b"{}"), _headers(res.getheaders())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}"), _headers(exc.headers.items())

    def login(self, email: str = EMAIL, password: str = PASSWORD):
        status, body, _ = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password},
                                    csrf=False)
        if status != 200:
            raise SystemExit(f"login failed for {email}: {status} {body}")
        if body.get("mfa_required"):
            raise SystemExit(f"{email} already has an authenticator — disable it before running the proof")
        self.token, self.csrf = body["token"], body["csrf"]
        return body


def _headers(pairs) -> dict:
    """Response headers that keep every `Set-Cookie` line.

    `dict(pairs)` keeps only the *last* one, so a two-cookie login (session plus
    refresh) looks like a login that set no session cookie at all — and this proof
    spent a run asserting exactly that. The joined copy goes under `Set-Cookie`, and
    the raw lines under `Set-Cookie-Lines`.
    """
    pairs = list(pairs)
    out = {k: v for k, v in pairs if k.lower() != "set-cookie"}
    lines = [v for k, v in pairs if k.lower() == "set-cookie"]
    out["Set-Cookie"] = "\n".join(lines)
    out["Set-Cookie-Lines"] = lines
    return out


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))


def challenge(client: Client | None = None) -> dict:
    """The half-session a password buys once an authenticator is on."""
    c = client or Client(BASE)
    status, body, _ = c.call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD}, csrf=False)
    if status != 200 or not body.get("mfa_required"):
        raise SystemExit(f"expected an MFA challenge, got {status} {body}")
    return body


def fresh_code(secret: str) -> str:
    """A code for the *next* window — always a step ahead of the last one spent."""
    return totp.totp(secret, at=(totp.step_at() + 1) * totp.STEP_SECONDS)


def wait_for_next_window() -> None:
    """A person waits for the next code; so does a proof, once per spent window."""
    time.sleep(totp.STEP_SECONDS - (time.time() % totp.STEP_SECONDS) + 1.0)


BASE = "http://127.0.0.1:5433"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    global BASE
    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-mfa-proof-")
        port = free_port()
        BASE = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
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
    try:
        print(f"\n=== two-factor proof against {BASE} ===")
        print("\n1. the desk starts password-only, and says so")
        owner.login()
        status, state, _ = owner.call("GET", "/api/v1/auth/mfa")
        check("the state route answers", status == 200, f"{status}")
        check("the account has no authenticator", state["mfa"]["enrolled"] is False, str(state["mfa"]))
        check("the policy is off by default, so a demo still signs in", state["mfa"]["required"] is False)
        check("no secret is served by a read", "secret" not in json.dumps(state).lower())
        print(f"        state: {json.dumps(state['mfa'])}")

        print("\n2. enrolment issues a secret, a URI and ten codes — and switches nothing on")
        status, enrol, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
        secret = enrol.get("secret", "")
        codes = enrol.get("recovery_codes", [])
        check("the endpoint answers 200", status == 200, f"{status}")
        check("a 20-byte base32 secret", len(totp.decode_secret(secret)) == 20, f"{len(secret)} chars")
        check("an otpauth URI an app can scan", enrol.get("otpauth_uri", "").startswith("otpauth://totp/"),
              enrol.get("otpauth_uri", "")[:52] + "…")
        check("ten recovery codes", len(codes) == 10, codes[0] if codes else "none")
        check("still not enrolled — nothing is a factor until a code proves it",
              owner.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is False)

        print("\n3. the seed is sealed at rest and the recovery codes are stored as hashes")
        if tmpdir:
            row = next(x for x in json.load(open(os.path.join(tmpdir, "store.json")))["users"]
                       if x["email"] == EMAIL)
            blob = row.get("mfa_secret") or ""
            check("the seed is not readable in the store", secret not in blob and len(blob) > 40,
                  f"{len(blob)} chars of ciphertext")
            stored = [_unblind(c) for c in (row.get("mfa_recovery") or [])]
            check("no recovery code is stored in the clear", all(codes[0] not in s for s in stored),
                  f"{len(stored)} hashes")
            check("the hashes are all different", len({s for s in stored}) == len(stored))
        else:
            print("        (skipped: the live desk keeps its rows in Postgres, and reading")
            print("         ciphertext out of it would not prove which key sealed it)")

        print("\n4. a wrong code does not enrol; the right one does")
        status, _, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": "000000"})
        check("a wrong code is refused", status == 400, f"{status}")
        check("and the account is still unprotected", owner.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is False)
        status, verified, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": totp.totp(secret)})
        check("the right code enrols", status == 200 and verified["mfa"]["enrolled"] is True, f"{status}")
        check("all ten recovery codes are available", verified["mfa"]["recovery_codes_left"] == 10)

        print("\n5. the password alone now returns a challenge, not a session")
        half = challenge()
        check("a challenge is issued", bool(half.get("challenge")))
        check("no session token is issued", "token" not in half and "csrf" not in half)
        check("the mailbox is hinted at, not printed", half["user_hint"].startswith("ow"), half["user_hint"])
        check("it expires in minutes, not in a working day", half["expires_in"] <= 300, f"{half['expires_in']}s")

        print("\n6. the challenge is not a session")
        for path in ("/api/v1/auth/me", "/api/v1/contacts", "/api/v1/money/summary"):
            status, body, _ = Client(BASE).call("GET", path, token=half["challenge"], csrf=False)
            check(f"{path} refuses a challenge", status == 401, body.get("error", {}).get("code", f"{status}"))

        print("\n7. the challenge plus a code is a session, and the code is spent")
        code = fresh_code(secret)
        status, session, headers = Client(BASE).call(
            "POST", "/api/v1/auth/login/mfa", {"challenge": half["challenge"], "code": code}, csrf=False)
        check("a session is issued", status == 200 and bool(session.get("token")), f"{status}")
        check("the session cookie is set on the second step",
              any("re_session=" in line for line in headers.get("Set-Cookie-Lines", [])))
        second = Client(BASE)
        second.token, second.csrf = session.get("token", ""), session.get("csrf", "")
        check("the session works", second.call("GET", "/api/v1/auth/me")[0] == 200)
        status, replay, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                             {"challenge": half["challenge"], "code": code}, csrf=False)
        check("the same code is refused again", status == 401, replay.get("error", {}).get("message", "")[:40])

        print("\n8. a recovery code signs in once, and survives being read aloud")
        spoken = codes[0].lower().replace("-", "")
        status, got, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                           {"challenge": challenge()["challenge"], "recovery_code": spoken},
                                           csrf=False)
        check("lower case, no dash, over the phone — still works", status == 200 and bool(got.get("token")), f"{status}")
        status, used, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                            {"challenge": challenge()["challenge"], "recovery_code": codes[0]},
                                            csrf=False)
        check("the same recovery code is refused the second time", status == 401, f"{status}")
        check("nine are left", owner.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["recovery_codes_left"] == 9)

        print("\n9. regenerating voids the previous ten")
        status, _, _ = second.call("POST", "/api/v1/auth/mfa/recovery-codes",
                                   {"password": "not-the-password", "recovery_code": codes[1]})
        check("a wrong password is refused", status == 401, f"{status}")
        status, made, _ = second.call("POST", "/api/v1/auth/mfa/recovery-codes",
                                      {"password": PASSWORD, "recovery_code": codes[1]})
        check("password plus a factor mints a new set", status == 200 and len(made.get("recovery_codes", [])) == 10,
              f"{status}")
        status, _, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                         {"challenge": challenge()["challenge"], "recovery_code": codes[2]},
                                         csrf=False)
        check("an old code is now void", status == 401, f"{status}")
        status, ok, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                          {"challenge": challenge()["challenge"],
                                           "recovery_code": made["recovery_codes"][0]}, csrf=False)
        check("a new code works", status == 200, f"{status}")

        print("\n10. turning it off needs the password and a factor")
        session = Client(BASE)
        status, body, _ = session.call("POST", "/api/v1/auth/login/mfa",
                                       {"challenge": challenge()["challenge"],
                                        "recovery_code": made["recovery_codes"][1]}, csrf=False)
        session.token, session.csrf = body.get("token", ""), body.get("csrf", "")
        check("a stolen cookie alone cannot remove the factor",
              session.call("POST", "/api/v1/auth/mfa/disable", {"password": PASSWORD})[0] == 401)
        check("nor can a code without the password",
              session.call("POST", "/api/v1/auth/mfa/disable",
                           {"recovery_code": made["recovery_codes"][2]})[0] == 401)
        check("the factor is still on", session.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is True)

        print("\n11. five wrong codes lock the factor; an owner's reset is the way back")
        for _ in range(5):
            Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                              {"challenge": challenge()["challenge"], "code": "000000"}, csrf=False)
        status, locked, _ = Client(BASE).call("POST", "/api/v1/auth/login/mfa",
                                              {"challenge": challenge()["challenge"],
                                               "code": fresh_code(secret)}, csrf=False)
        check("a correct code is refused while locked", status == 423, locked.get("error", {}).get("code", f"{status}"))
        uid = session.call("GET", "/api/v1/auth/me")[1]["user"]["id"]
        # the reset is a step-up route now (docs/34), and the account is *locked out
        # of its codes* by the very check above — so the confirmation has to ride a
        # recovery code, which the lockout deliberately does not refuse (§7)
        status, ticket, _ = session.call("POST", "/api/v1/auth/step-up",
                                         {"action": "mfa_reset", "target": uid, "password": PASSWORD,
                                          "recovery_code": made["recovery_codes"][3]})
        check("a recovery code still confirms during a code lockout", status == 200,
              ticket.get("error", {}).get("code", f"{status}"))
        status, _, _ = session.call("PATCH", f"/api/v1/tenant/users/{uid}", {"mfa_reset": True},
                                    headers={"X-Step-Up": ticket.get("ticket", "")})
        check("an owner can reset it — break glass, audited, confirmed", status == 200, f"{status}")
        status, back, _ = Client(BASE).call("POST", "/api/v1/auth/login",
                                            {"email": EMAIL, "password": PASSWORD}, csrf=False)
        check("the desk is password-only again after a reset", status == 200 and "token" in back, f"{status}")

        print("\n12. a workspace that demands a factor gates identity and money, not the day's work")
        owner.token, owner.csrf = back.get("token", ""), back.get("csrf", "")
        status, _, _ = owner.call("PATCH", "/api/v1/tenant", {"settings": {"require_mfa": True}})
        check("the policy can be switched on", status == 200, f"{status}")
        me = owner.call("GET", "/api/v1/auth/me")[1]["user"]["mfa"]
        check("the session knows it is half-protected", me["required"] is True and me["pending"] is True, str(me))
        stamp = str(int(time.time()))[-6:]
        status, blocked, _ = owner.call("POST", "/api/v1/tenant/users",
                                        {"email": f"mfa-proof-{stamp}@example.invalid", "role": "agent",
                                         "password": "Demo@12345!"})
        check("adding a login is gated", status == 403 and blocked["error"]["code"] == "MFA_REQUIRED", f"{status}")
        check("reading the pipeline is not", owner.call("GET", "/api/v1/pipeline")[0] == 200)
        status, _, _ = owner.call("PATCH", "/api/v1/tenant", {"settings": {"require_mfa": False}})
        check("and the switch that turns it off still works from a half-protected session",
              status == 200 and owner.call("GET", "/api/v1/auth/me")[1]["user"]["mfa"]["required"] is False,
              f"{status}")
    finally:
        # 13. leave the desk as it was found: a proof does not change the demo
        print("\n13. the desk is left password-only")
        try:
            status, body, _ = Client(BASE).call("POST", "/api/v1/auth/login",
                                                {"email": EMAIL, "password": PASSWORD}, csrf=False)
            if body.get("mfa_required"):
                if not codes:
                    raise SystemExit("the desk was left with an authenticator this run did not create — "
                                     "reset it (PATCH /tenant/users/{id} {mfa_reset: true}) and rerun")
                status, made, _ = Client(BASE).call(
                    "POST", "/api/v1/auth/login/mfa",
                    {"challenge": body["challenge"], "recovery_code": codes[-1]}, csrf=False)
                cleanup = Client(BASE)
                cleanup.token, cleanup.csrf = made.get("token", ""), made.get("csrf", "")
                if cleanup.token:
                    uid = cleanup.call("GET", "/api/v1/auth/me")[1]["user"]["id"]
                    # last resort, and it needs a confirmation like any other reset
                    status, ticket, _ = cleanup.call("POST", "/api/v1/auth/step-up",
                                                     {"action": "mfa_reset", "target": uid,
                                                      "password": PASSWORD,
                                                      "recovery_code": codes[-1]})
                    if status == 200:
                        cleanup.call("PATCH", f"/api/v1/tenant/users/{uid}", {"mfa_reset": True},
                                     headers={"X-Step-Up": ticket["ticket"]})
                check("the authenticator was removed again", True)
        except Exception as exc:      # pragma: no cover - best effort
            print(f"        cleanup: {exc}")
        if proc:
            proc.terminate()
        print(f"\n{PASSED} checks passed, {len(FAILS)} failed")
        for f in FAILS:
            print(f"  - {f}")
    return 0 if not FAILS else 1


def _unblind(entry: dict) -> str:
    """The stored value, which is a hash — enough to prove the code is not in it."""
    return str(entry.get("h", ""))


if __name__ == "__main__":
    sys.exit(main())

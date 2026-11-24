#!/usr/bin/env python3
"""
Proof: a session you can see, rotate and end.

    python3 scripts/proof_sessions.py                      # throwaway instance
    python3 scripts/proof_sessions.py --base http://127.0.0.1:5433 --live

Ten sections, all HTTP. The throwaway run additionally proves the two things an
API cannot show from outside — that the stored refresh token is a hash, and that
a token really does expire — by reading its own store file and by running the
clock in seconds.

  1. a login sets two cookies, and the refresh one travels nowhere else
  2. the response body carries no long-lived credential
  3. the device list names the browser and marks this device
  4. every refresh rotates the token and keeps the session
  5. a replayed refresh token ends the session for *both* holders
  6. ending one device kills its token on the very next request
  7. "sign out all other devices" keeps the one in your hand
  8. another user's session does not exist rather than being forbidden
  9. signing out ends the row, the token and the refresh
 10. an expired token is refreshed; an expired refresh ends the session

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

EMAIL = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
FIREFOX = "Mozilla/5.0 (Android 15; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0"
RUN = f"sessions-{int(time.time())}"
PASSED = 0
FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """A browser-shaped client: it keeps a cookie jar and shows a User-Agent."""

    def __init__(self, base: str, ua: str = CHROME):
        self.base = base
        self.ua = ua
        self.cookies: dict[str, str] = {}
        self.token = ""
        self.csrf = ""
        self.last_headers: list[tuple[str, str]] = []

    def call(self, method: str, path: str, body=None, headers: dict | None = None):
        h = {"User-Agent": self.ua, "Accept": "application/json"}
        if method != "GET":
            # a proof says it is a proof: the session rows this run opens are
            # stamped, so `scripts/reset_demo.py --purge` can take them away again
            h["X-Proof-Run"] = RUN
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw, status, pairs = res.read(), res.status, res.getheaders()
        except urllib.error.HTTPError as exc:
            raw, status, pairs = exc.read(), exc.code, list(exc.headers.items())
        self.last_headers = pairs
        for line in [v for k, v in pairs if k.lower() == "set-cookie"]:
            name, _, rest = line.partition("=")
            value = rest.split(";")[0].strip()
            if value:
                self.cookies[name.strip()] = value
            else:
                self.cookies.pop(name.strip(), None)
        try:
            return status, json.loads(raw or b"{}")
        except Exception:
            return status, {"_raw": raw.decode("utf-8", "replace")}

    def login(self, email: str = EMAIL, password: str = PASSWORD, query: str = ""):
        status, body = self.call("POST", "/api/v1/auth/login" + query, {"email": email, "password": password})
        if status != 200 or body.get("mfa_required"):
            raise SystemExit(f"login failed: {status} {body}")
        self.token, self.csrf = body["token"], body["csrf"]
        return body

    def cookie_line(self, name: str) -> str:
        for line in [v for k, v in self.last_headers if k.lower() == "set-cookie"]:
            if line.startswith(name + "="):
                return line
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if args.live:
        base = (args.base or "http://127.0.0.1:5433").rstrip("/")
    else:
        tmpdir = tempfile.mkdtemp(prefix="re-sessions-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(160):
            try:
                urb = urllib.request.urlopen(base + "/healthz", timeout=1)
                if urb.status == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            proc.kill()
            return 1

    try:
        print(f"\n=== session proof against {base} ===")
        print("\n1. a login sets two cookies, and the refresh one travels nowhere else")
        owner = Client(base)
        body = owner.login()
        check("a session cookie is set", "re_session" in owner.cookies)
        check("a refresh cookie is set", "re_refresh" in owner.cookies)
        print("        session:", owner.cookie_line("re_session")[:90] + "…")
        print("        refresh:", owner.cookie_line("re_refresh")[:80] + "…")
        check("the session cookie is for the whole site",
              "Path=/" in owner.cookie_line("re_session") and "HttpOnly" in owner.cookie_line("re_session"))
        check("the refresh cookie is scoped to the auth routes, not the site",
              "Path=/api/v1/auth" in owner.cookie_line("re_refresh"), owner.cookie_line("re_refresh")[:40])
        check("and it is HttpOnly and SameSite=Strict",
              "HttpOnly" in owner.cookie_line("re_refresh") and "SameSite=Strict" in owner.cookie_line("re_refresh"))
        check("the access token is short-lived", body["expires_in"] <= 3600, f"{body['expires_in']}s")
        check("the session is long-lived", body["refresh_expires_in"] >= 86400,
              f"{body['refresh_expires_in'] // 86400}d")

        print("\n2. the response body carries no long-lived credential")
        check("no refresh token in the body", "refresh_token" not in body)
        asked = Client(base).login(query="?refresh=body")
        check("a CLI can ask for it explicitly, and is warned",
              bool((asked or {}).get("refresh_token")) and "keystore" in asked.get("refresh_warning", ""))

        print("\n3. the device list names the browser and marks this device")
        status, listed = owner.call("GET", "/api/v1/auth/sessions")
        mine = [s for s in listed.get("sessions", []) if s["current"]]
        check("the list answers", status == 200 and len(mine) == 1, f"{status}")
        check("the label is recognisable", "Chrome" in mine[0]["device"]["label"], mine[0]["device"]["label"])
        check("the address is masked to a network",
              mine[0]["ip"] == "" or mine[0]["ip"].endswith("x") or mine[0]["ip"].endswith("::"), mine[0]["ip"])
        # the *rows*, not the envelope (which carries `refresh_ttl`, a number)
        rows_blob = json.dumps(listed.get("sessions", [])).lower()
        check("it holds no credential",
              all(w not in rows_blob for w in ("refresh_hash", "refresh_token", "user_agent", "password")),
              f"{len(rows_blob.splitlines())} row(s)")
        check("it says how long each half lives", listed["access_ttl"] and listed["refresh_ttl"],
              f"{listed['access_ttl']}s / {listed['refresh_ttl']}s")

        print("\n4. every refresh rotates the token and keeps the session")
        first_refresh = owner.cookies["re_refresh"]
        first_access = owner.token
        status, rotated = owner.call("POST", "/api/v1/auth/refresh", {})
        check("a new access token", status == 200 and rotated["token"] != first_access, f"{status}")
        check("a new refresh token", owner.cookies["re_refresh"] != first_refresh)
        check("the same session", rotated["session"]["id"] == body["session"]["id"])
        owner.token, owner.csrf = rotated["token"], rotated["csrf"]
        check("the new access token works", owner.call("GET", "/api/v1/auth/me")[0] == 200)
        if tmpdir:
            row = next(r for r in json.load(open(os.path.join(tmpdir, "store.json")))["sessions"]
                       if r["id"] == body["session"]["id"])
            check("the stored refresh token is a hash, not the token",
                  first_refresh not in json.dumps(row) and owner.cookies["re_refresh"] not in json.dumps(row),
                  f"{len(row.get('refresh_hash', ''))} hex chars")
            check("the superseded token is remembered for reuse detection",
                  len(row.get("refresh_history") or []) == 1, f"{len(row.get('refresh_history') or [])} kept")

        print("\n5. a replayed refresh token ends the session for *both* holders")
        stolen = first_refresh
        # the thief replays the token the legitimate client already rotated past
        status, reused = Client(base).call("POST", "/api/v1/auth/refresh", {},
                                           headers={"X-Refresh-Token": stolen})
        check("the replay is recognised, not merely refused",
              status == 401 and reused.get("error", {}).get("code") == "REFRESH_REUSED",
              reused.get("error", {}).get("message", "")[:46])
        status, dead = owner.call("GET", "/api/v1/auth/me")
        check("and the session is dead for the real holder too — we cannot tell which is which",
              status == 401, dead.get("error", {}).get("code", f"{status}"))

        print("\n6. ending one device kills its token on the very next request")
        keeper = Client(base)
        keeper.login()
        doomed = Client(base, ua=FIREFOX)
        doomed.login()
        their_sid = [s for s in doomed.call("GET", "/api/v1/auth/sessions")[1]["sessions"] if s["current"]][0]["id"]
        check("the other device works before", doomed.call("GET", "/api/v1/auth/me")[0] == 200)
        status, ended = keeper.call("DELETE", f"/api/v1/auth/sessions/{their_sid}")
        check("the session is ended", status == 200 and ended["session"]["revoked"] is True, f"{status}")
        check("its next request fails", doomed.call("GET", "/api/v1/auth/me")[0] == 401)
        check("and so does its refresh token",
              Client(base).call("POST", "/api/v1/auth/refresh", {},
                                headers={"X-Refresh-Token": doomed.cookies.get("re_refresh", "")})[0] == 401)
        check("this device is untouched", keeper.call("GET", "/api/v1/auth/me")[0] == 200)

        print("\n7. \"sign out all other devices\" keeps the one in your hand")
        for _ in range(2):
            Client(base, ua=FIREFOX).login()
        before = keeper.call("GET", "/api/v1/auth/sessions")[1]["count"]
        status, killed = keeper.call("POST", "/api/v1/auth/sessions/revoke-others", {})
        after = keeper.call("GET", "/api/v1/auth/sessions")[1]
        check("the others are ended", status == 200 and killed["revoked"] >= 2,
              f"{before} → {after['count']}")
        check("exactly this device is left", after["count"] == 1 and after["sessions"][0]["current"] is True)
        check("and it still works", keeper.call("GET", "/api/v1/auth/me")[0] == 200)

        print("\n8. another user's session does not exist rather than being forbidden")
        metro = Client(base)
        metro.login("owner@metrorealty.app", "Metro@12345!")
        metro_sid = [s for s in metro.call("GET", "/api/v1/auth/sessions")[1]["sessions"] if s["current"]][0]["id"]
        status, err = keeper.call("DELETE", f"/api/v1/auth/sessions/{metro_sid}")
        check("404, not 403 — a 403 would confirm the id", status == 404, err.get("error", {}).get("code"))
        check("the other workspace's session is fine", metro.call("GET", "/api/v1/auth/me")[0] == 200)

        print("\n9. signing out ends the row, the token and the refresh")
        leaving = Client(base)
        leaving.login()
        refresh_held = leaving.cookies["re_refresh"]
        access_held = leaving.token
        status, out = leaving.call("POST", "/api/v1/auth/logout", {})
        check("logout answers", status == 200 and out.get("session_ended") is True, f"{status}")
        check("the access token is refused",
              Client(base).call("GET", "/api/v1/auth/me", headers={"Authorization": "Bearer " + access_held})[0] == 401)
        check("the refresh token is refused",
              Client(base).call("POST", "/api/v1/auth/refresh", {},
                                headers={"X-Refresh-Token": refresh_held})[0] == 401)
        check("both cookies are cleared", all(
            any(f"{n}=;" in ln and "Max-Age=0" in ln for ln in leaving.last_headers and
                [v for k, v in leaving.last_headers if k.lower() == "set-cookie"])
            for n in ("re_session", "re_refresh")))

        print("\n10. the clock")
        if args.live:
            print("        (skipped: the live desk runs 30-minute access tokens and 30-day")
            print("         sessions; the throwaway run boots them in seconds and proves")
            print("         expiry there — see tests/test_sessions.py group D as well)")
        else:
            short = boot_short_lived()
            try:
                client = Client(short)
                client.login()
                check("a token works while it is young", client.call("GET", "/api/v1/auth/me")[0] == 200)
                time.sleep(3)
                check("and is refused when it is old", client.call("GET", "/api/v1/auth/me")[0] == 401)
                status, fresh = client.call("POST", "/api/v1/auth/refresh", {})
                check("the refresh cookie renews it without a password",
                      status == 200 and fresh.get("expires_in") == 2, f"{status}")
                client.token, client.csrf = fresh["token"], fresh["csrf"]
                check("the renewed token works", client.call("GET", "/api/v1/auth/me")[0] == 200)
                time.sleep(9)
                status, gone = client.call("POST", "/api/v1/auth/refresh", {})
                check("when the refresh token's own life ends, so does the session",
                      status == 401 and gone["error"]["code"] == "SESSION_EXPIRED",
                      gone.get("error", {}).get("code", f"{status}"))
            finally:
                short_proc = SHORT_PROC.pop("proc", None)
                if short_proc:
                    short_proc.terminate()
    finally:
        if proc:
            proc.terminate()
        print(f"\n{PASSED} checks passed, {len(FAILS)} failed")
        for f in FAILS:
            print(f"  - {f}")
    return 0 if not FAILS else 1


SHORT_PROC: dict = {}


def boot_short_lived() -> str:
    """A second throwaway server whose token lifetimes are seconds."""
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="re-sessions-clock-")
    env = {**os.environ, "RE_DATA_DIR": tmp, "RE_DB": "json", "RE_LIMITER_OFF": "1",
           "RE_AUTOMATION_TICK": "0", "RE_ACCESS_TTL": "2", "RE_REFRESH_TTL": "8"}
    p = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SHORT_PROC["proc"] = p
    base = f"http://127.0.0.1:{port}"
    for _ in range(160):
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=1).status == 200:
                return base
        except Exception:
            time.sleep(0.25)
    raise SystemExit("short-lived server did not start")


if __name__ == "__main__":
    sys.exit(main())

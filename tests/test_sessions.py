"""
Sessions as records: refresh rotation, the device list, and revocation.

Four things are tested, because four things can be wrong:

  A. the cookie and the body — two cookies on login, the refresh one scoped to the
     auth routes, and no long-lived credential in a JSON response
  B. rotation — one refresh token per use, and the *reuse* of a rotated one ends
     the session instead of merely being refused
  C. the device list — where am I signed in, can I end one, can I end the rest,
     and can I see somebody else's
  D. the clock — an access token that expires is refreshed rather than re-logged-in,
     and a refresh token that expires ends the session

Group D needs short lifetimes, so it runs against its own throwaway server with
`RE_ACCESS_TTL` and `RE_REFRESH_TTL` set to seconds. Everything else runs against
the suite's shared server.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import security as sec  # noqa: E402
from conftest import ROOT, Session  # noqa: E402

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
FIREFOX_ANDROID = ("Mozilla/5.0 (Android 15; Mobile; rv:132.0) Gecko/132.0 "
                   "Firefox/132.0")


def login(ua: str = "", email: str = OWNER, password: str = PASSWORD, query: str = "") -> Session:
    """A fresh client, signed in, with both cookies in its jar."""
    s = Session()
    status, body, _ = s.call("POST", "/api/v1/auth/login" + query,
                             {"email": email, "password": password},
                             headers={"User-Agent": ua} if ua else None, csrf=False)
    assert status == 200, body
    assert not body.get("mfa_required"), "these tests assume a password-only account"
    s.token, s.csrf = body["token"], body["csrf"]
    s.body = body
    return s


# ===========================================================================
# A. the cookie and the body
# ===========================================================================
def test_a_login_sets_two_cookies_and_the_refresh_one_is_narrowly_scoped():
    s = login(ua=CHROME)
    assert s.cookies.get("re_session") and s.cookies.get("re_refresh")
    raw = s.body
    assert raw["expires_in"] == sec.SESSION_TTL // 16 or raw["expires_in"] <= 3600
    assert raw["refresh_expires_in"] >= 24 * 3600
    # the refresh token is delivered by cookie only
    assert "refresh_token" not in raw
    assert raw["session"]["current"] is True


def test_the_refresh_cookie_is_scoped_and_the_header_says_so():
    s = Session()
    _, body, headers = s.call("POST", "/api/v1/auth/login",
                              {"email": OWNER, "password": PASSWORD},
                              headers={"User-Agent": CHROME}, csrf=False)
    lines = [ln for ln in headers["Set-Cookie"].split("\n") if "re_refresh=" in ln]
    assert lines, headers["Set-Cookie"]
    line = lines[0]
    assert "Path=/api/v1/auth" in line, "a refresh token must not travel to /contacts"
    assert "HttpOnly" in line and "SameSite=Strict" in line
    assert "Max-Age=" in line and int(line.split("Max-Age=")[1].split(";")[0]) > 24 * 3600
    session_line = [ln for ln in headers["Set-Cookie"].split("\n") if "re_session=" in ln][0]
    assert "Path=/" in session_line and "HttpOnly" in session_line


def test_a_client_without_a_cookie_jar_can_ask_for_the_token_explicitly():
    s = Session()
    _, body, _ = s.call("POST", "/api/v1/auth/login?refresh=body",
                        {"email": OWNER, "password": PASSWORD}, csrf=False)
    assert body.get("refresh_token") and "keystore" in body.get("refresh_warning", "")
    # ... and it works as a bearer of the second half
    bare = Session()
    status, refreshed, _ = bare.call("POST", "/api/v1/auth/refresh", {},
                                     headers={"X-Refresh-Token": body["refresh_token"]},
                                     csrf=False)
    assert status == 200 and refreshed["token"] != body["token"]


# ===========================================================================
# B. rotation, and what a replayed token means
# ===========================================================================
def test_each_refresh_token_works_exactly_once():
    s = login(ua=CHROME)
    first = s.cookies["re_refresh"]
    status, rotated, _ = s.call("POST", "/api/v1/auth/refresh", {}, csrf=False)
    assert status == 200 and rotated["token"] != s.body["token"]
    assert s.cookies["re_refresh"] != first, "the refresh token must rotate, not repeat"
    # the new access token works and carries the same session
    s.token, s.csrf = rotated["token"], rotated["csrf"]
    assert s.call("GET", "/api/v1/auth/me")[0] == 200
    assert s.call("GET", "/api/v1/auth/sessions")[1]["sessions"][0]["current"] is True


def test_a_replayed_refresh_token_ends_the_session_instead_of_just_failing():
    """The whole point of rotation: two holders of one credential is a breach."""
    s = login(ua=CHROME)
    stolen = s.cookies["re_refresh"]
    status, rotated, _ = s.call("POST", "/api/v1/auth/refresh", {}, csrf=False)
    assert status == 200
    live_access = rotated["token"]
    # the legitimate client is now on the new token; the thief presents the old one
    thief = Session()
    status, body, _ = thief.call("POST", "/api/v1/auth/refresh", {},
                                 headers={"X-Refresh-Token": stolen}, csrf=False)
    assert status == 401 and body["error"]["code"] == "REFRESH_REUSED", body
    # and the session is dead for *everybody*, including the holder of the newest
    # token: that is the honest outcome, because we cannot tell which one is which
    dead = Session()
    status, body, _ = dead.call("GET", "/api/v1/auth/me",
                                headers={"Authorization": "Bearer " + live_access}, csrf=False)
    assert status == 401 and body["error"]["code"] in ("SESSION_ENDED", "UNAUTHENTICATED")


def test_a_garbage_refresh_token_is_refused_without_touching_anything():
    s = login(ua=CHROME)
    status, body, _ = Session().call("POST", "/api/v1/auth/refresh", {},
                                     headers={"X-Refresh-Token": "not-a-real-token"}, csrf=False)
    assert status == 401 and body["error"]["code"] == "REFRESH_INVALID"
    # the real session is untouched
    assert s.call("GET", "/api/v1/auth/me")[0] == 200


def test_no_cookie_and_no_header_is_a_refusal_not_a_500():
    status, body, _ = Session().call("POST", "/api/v1/auth/refresh", {}, csrf=False)
    assert status == 401 and body["error"]["code"] == "REFRESH_INVALID"


def test_a_rotated_away_token_is_remembered_for_several_rotations():
    s = login(ua=CHROME)
    first = s.cookies["re_refresh"]
    for _ in range(3):
        assert s.call("POST", "/api/v1/auth/refresh", {}, csrf=False)[0] == 200
    # four rotations later the first token is still recognisable as *ours* —
    # a refusal would tell a thief to try again tomorrow; recognition ends it
    status, body, _ = Session().call("POST", "/api/v1/auth/refresh", {},
                                     headers={"X-Refresh-Token": first}, csrf=False)
    assert status == 401 and body["error"]["code"] == "REFRESH_REUSED"


# ===========================================================================
# C. the device list
# ===========================================================================
def test_the_device_list_names_the_browser_and_marks_this_device():
    s = login(ua=CHROME)
    status, body, _ = s.call("GET", "/api/v1/auth/sessions")
    assert status == 200
    mine = [x for x in body["sessions"] if x["current"]]
    assert len(mine) == 1
    assert "Chrome" in mine[0]["device"]["label"] and "macOS" in mine[0]["device"]["label"], mine[0]
    assert mine[0]["amr"] == ["pwd"]
    assert body["access_ttl"] and body["refresh_ttl"]


def test_two_devices_are_two_rows_and_neither_leaks_a_credential():
    a = login(ua=CHROME)
    b = login(ua=FIREFOX_ANDROID)
    sessions = a.call("GET", "/api/v1/auth/sessions")[1]["sessions"]
    labels = " ".join(x["device"]["label"] for x in sessions)
    assert "Chrome" in labels and "Firefox" in labels, labels
    assert sum(1 for x in sessions if x["current"]) == 1
    blob = json.dumps(sessions).lower()
    for forbidden in ("refresh", "hash", "user_agent", "token"):
        assert forbidden not in blob, f"the device list leaked a {forbidden}"
    # the addresses are masked to a network, not a location
    for x in sessions:
        assert x["ip"] == "" or x["ip"].endswith(("x", "::")), x["ip"]


def test_ending_one_device_kills_its_token_on_the_very_next_request():
    a = login(ua=CHROME)
    b = login(ua=FIREFOX_ANDROID)
    # b's own id, read from b: the suite's shared server holds sessions from other
    # tests, so "the other one in the list" is not necessarily b
    theirs = [x for x in b.call("GET", "/api/v1/auth/sessions")[1]["sessions"] if x["current"]][0]
    assert theirs["id"] in [x["id"] for x in a.call("GET", "/api/v1/auth/sessions")[1]["sessions"]]
    target = theirs
    assert b.call("GET", "/api/v1/auth/me")[0] == 200
    status, body, _ = a.call("DELETE", f"/api/v1/auth/sessions/{target['id']}")
    assert status == 200 and body["ok"] and body["session"]["revoked"] is True, body
    # no waiting for expiry, no re-login: the row is the authority
    status, err, _ = b.call("GET", "/api/v1/auth/me")
    assert status == 401 and err["error"]["code"] in ("SESSION_ENDED", "UNAUTHENTICATED")
    # and this device is untouched
    assert a.call("GET", "/api/v1/auth/me")[0] == 200


def test_revoke_others_keeps_the_device_in_your_hand():
    mine = login(ua=CHROME)
    for _ in range(2):
        login(ua=FIREFOX_ANDROID)
    assert mine.call("GET", "/api/v1/auth/sessions")[1]["count"] >= 3
    status, body, _ = mine.call("POST", "/api/v1/auth/sessions/revoke-others", {})
    assert status == 200 and body["revoked"] >= 2
    left = mine.call("GET", "/api/v1/auth/sessions")[1]
    assert left["count"] == 1 and left["sessions"][0]["current"] is True
    assert mine.call("GET", "/api/v1/auth/me")[0] == 200


def test_another_users_session_does_not_exist_rather_than_being_forbidden(other_tenant):
    mine = login(ua=CHROME)
    theirs = login(ua=FIREFOX_ANDROID, email="owner@metrorealty.app", password="Metro@12345!")
    their_sid = theirs.call("GET", "/api/v1/auth/sessions")[1]["sessions"][0]["id"]
    status, body, _ = mine.call("DELETE", f"/api/v1/auth/sessions/{their_sid}")
    # 404, not 403: a 403 would confirm that the id is real
    assert status == 404, body
    assert theirs.call("GET", "/api/v1/auth/me")[0] == 200
    # and the lists are separate
    assert their_sid not in json.dumps(mine.call("GET", "/api/v1/auth/sessions")[1])


def test_signing_out_ends_the_row_and_its_refresh_token():
    s = login(ua=CHROME)
    refresh = s.cookies["re_refresh"]
    assert s.call("POST", "/api/v1/auth/logout", {})[0] == 200
    status, body, _ = Session().call("GET", "/api/v1/auth/me",
                                     headers={"Authorization": "Bearer " + s.body["token"]}, csrf=False)
    assert status == 401
    status, body, _ = Session().call("POST", "/api/v1/auth/refresh", {},
                                     headers={"X-Refresh-Token": refresh}, csrf=False)
    assert status == 401 and body["error"]["code"] in ("SESSION_EXPIRED", "REFRESH_INVALID")


def test_the_logout_response_clears_both_cookies():
    s = login(ua=CHROME)
    _, _, headers = s.call("POST", "/api/v1/auth/logout", {})
    cleared = headers["Set-Cookie"]
    assert "re_session=;" in cleared and "re_refresh=;" in cleared
    assert "Max-Age=0" in cleared


def test_a_token_without_a_session_is_refused():
    """Tokens minted before sessions existed have no `sid`: there is no row to
    revoke, and "which session is this?" has no answer, so they do not work."""
    me = Session()
    me.login(OWNER)
    who = me.call("GET", "/api/v1/auth/me")[1]
    # a real user and tenant, signed with the real key — only `sid` is missing
    legacy = sec.issue_token({"sub": who["user"]["email"], "uid": who["user"]["id"],
                              "tid": who["tenant"]["id"], "role": who["user"]["role"]})
    status, body, _ = Session().call("GET", "/api/v1/auth/me",
                                     headers={"Authorization": "Bearer " + legacy}, csrf=False)
    assert status == 401 and body["error"]["code"] == "SESSION_REQUIRED"


def test_a_script_is_named_as_a_script_not_as_a_browser():
    s = Session()
    s.call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD},
           headers={"User-Agent": "python-urllib/3.13"}, csrf=False)
    row = s.call("GET", "/api/v1/auth/sessions")[1]["sessions"][0]
    assert row["device"]["kind"] == "script" and row["device"]["label"] == "Script"


# ===========================================================================
# D. the clock — its own server, because lifetimes are seconds there
# ===========================================================================
@pytest.fixture(scope="module")
def fleeting():
    """A server where an access token lives 2 s and a refresh token 8 s."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    tmp = tempfile.mkdtemp(prefix="re-sessions-")
    env = {**os.environ, "RE_DATA_DIR": tmp, "RE_DB": "json", "RE_LIMITER_OFF": "1",
           "RE_AUTOMATION_TICK": "0", "RE_ACCESS_TTL": "2", "RE_REFRESH_TTL": "8"}
    proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("short-ttl server did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


class Fleeting:
    """A client bound to the short-TTL server (conftest.Session is bound to its own)."""

    def __init__(self, base: str, ua: str = "python-urllib/3.13"):
        self.base = base
        self.ua = ua
        self.cookies: dict[str, str] = {}
        self.token = ""
        self.csrf = ""

    def call(self, method, path, body=None, headers=None):
        h = {"User-Agent": self.ua, "Accept": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                raw, status, pairs = res.read(), res.status, res.getheaders()
        except urllib.error.HTTPError as e:
            raw, status, pairs = e.read(), e.code, list(e.headers.items())
        for line in [v for k, v in pairs if k.lower() == "set-cookie"]:
            name, _, rest = line.partition("=")
            val = rest.split(";")[0].strip()
            if val:
                self.cookies[name.strip()] = val
            else:
                self.cookies.pop(name.strip(), None)
        try:
            return status, json.loads(raw or b"{}")
        except Exception:
            return status, {"_raw": raw.decode("utf-8", "replace")}

    def login(self, email: str = OWNER, password: str = PASSWORD):
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password})
        assert status == 200, body
        self.token, self.csrf = body["token"], body["csrf"]
        return body


def test_an_expired_access_token_is_refreshed_rather_than_re_logged_in(fleeting):
    client = Fleeting(fleeting)
    client.login()
    assert client.call("GET", "/api/v1/auth/me")[0] == 200
    time.sleep(3)
    status, body = client.call("GET", "/api/v1/auth/me")
    assert status == 401, "a 2-second access token must be dead after 3 seconds"
    status, refreshed = client.call("POST", "/api/v1/auth/refresh", {})
    assert status == 200 and refreshed["expires_in"] == 2
    client.token, client.csrf = refreshed["token"], refreshed["csrf"]
    assert client.call("GET", "/api/v1/auth/me")[0] == 200


def test_the_device_list_reports_the_lifetime_it_actually_has(fleeting):
    client = Fleeting(fleeting)
    client.login()
    body = client.call("GET", "/api/v1/auth/sessions")[1]
    assert body["access_ttl"] == 2 and body["refresh_ttl"] == 8


def test_an_expired_refresh_token_ends_the_session(fleeting):
    client = Fleeting(fleeting)
    client.login()
    time.sleep(9)
    status, body = client.call("POST", "/api/v1/auth/refresh", {})
    assert status == 401 and body["error"]["code"] == "SESSION_EXPIRED", body

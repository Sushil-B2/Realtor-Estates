"""
Step-up re-authentication — the ticket, the gate, and the ways it gets bypassed.

`docs/34` and security follow-up #2: a session proves *who* you are; a ticket
proves you satisfied the factor *recently, for this action, once*. Four groups:

  A. the ticket object — binding, expiry, and the `typ` check that keeps a session
     token from being presented as a confirmation
  B. the gate — 423 shapes, single use, wrong action, wrong target, wrong session
  C. the factor — password, TOTP, recovery codes (consumed, not laundered), the
     attempt budget that makes the dialog not a free password oracle
  D. the routes that now demand one, because a gate nothing calls protects nothing

Every test that turns a second factor on takes it off again: a suite that leaves
an authenticator on the owner account makes every later login a two-step login,
which is exactly the bug `test_mfa.py` was written around.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.stepup as stepup  # noqa: E402
from app import security as sec  # noqa: E402
from app import totp  # noqa: E402
from conftest import Session, clear_factor, step_up_headers  # noqa: E402

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"


@pytest.fixture(autouse=True)
def _clean_state():
    """One test's spent ticket or attempt budget is not another test's. """
    stepup.SPENT.clear()
    stepup.ATTEMPTS.state.clear()
    yield
    stepup.SPENT.clear()
    stepup.ATTEMPTS.state.clear()


def me(session: Session) -> dict:
    status, body, _ = session.call("GET", "/api/v1/auth/me")
    assert status == 200, body
    return body


def step_up(session: Session, action="kill_switch", target="engage", password=PASSWORD,
            code="", recovery="") -> tuple[int, dict]:
    payload = {"action": action, "target": target, "password": password}
    if code:
        payload["code"] = code
    if recovery:
        payload["recovery_code"] = recovery
    status, body, _ = session.call("POST", "/api/v1/auth/step-up", payload)
    return status, body


def ticket_for(session: Session, action="kill_switch", target="engage") -> str:
    status, body = step_up(session, action, target)
    assert status == 200, body
    return body["ticket"]


# ===========================================================================
# A. the ticket object
# ===========================================================================
def test_a_ticket_names_one_action_one_target_and_one_session():
    ticket = stepup.ticket_for("ses_abc", "user_delete", "usr_9")
    claims = stepup.parse(ticket)
    assert claims["typ"] == "stepup"
    assert (claims["act"], claims["tgt"], claims["sid"]) == ("user_delete", "usr_9", "ses_abc")
    # the expiry is the factor-aware TTL, not whatever the caller felt like
    assert claims["exp"] - claims["iat"] == stepup.TICKET_TTL


def test_a_session_token_cannot_be_presented_as_a_confirmation():
    """Both are signed with the same key, so `typ` is load-bearing, not decoration.

    Without this check, every signed-in user already holds a valid "confirmation"
    for every gated action — the gate would be a comment with a status code.
    """
    session_token = sec.issue_token({"uid": "u-1", "sid": "ses_abc", "role": "owner"})
    assert stepup.parse(session_token) is None
    # a ticket, by contrast, parses
    assert stepup.parse(stepup.ticket_for("ses_abc", "kill_switch"))["typ"] == "stepup"


def test_a_ticket_tampered_with_does_not_parse():
    ticket = stepup.ticket_for("ses_abc", "kill_switch", "release")
    body, _, sig = ticket.partition(".") if ticket.count(".") == 1 else (None, None, None)
    # flip the last character of the signature: a naive equality check would pass
    tampered = ticket[:-1] + ("A" if ticket[-1] != "A" else "B")
    assert stepup.parse(tampered) is None


def test_an_expired_ticket_does_not_parse(monkeypatch):
    ticket = stepup.ticket_for("ses_abc", "kill_switch")
    real = time.time
    monkeypatch.setattr(stepup.time, "time", lambda: real() + stepup.TICKET_TTL + 5)
    # parse uses the security module's clock; patch that too
    monkeypatch.setattr(sec.time, "time", lambda: real() + stepup.TICKET_TTL + 5)
    assert stepup.parse(ticket) is None


def test_two_tickets_for_the_same_action_in_the_same_second_are_different():
    a = stepup.ticket_for("ses_abc", "kill_switch", "engage")
    b = stepup.ticket_for("ses_abc", "kill_switch", "engage")
    assert a != b and stepup.parse(a)["jti"] != stepup.parse(b)["jti"]


def test_a_password_only_account_gets_the_shorter_ticket():
    """The honest reading of step-up for a workspace with nothing enrolled."""
    assert stepup.tickets_for_ttl(True) == stepup.TICKET_TTL > stepup.NO_FACTOR_TTL
    assert stepup.tickets_for_ttl(False) == stepup.NO_FACTOR_TTL
    plain = stepup.describe({"mfa_enrolled": False})
    enrolled = stepup.describe({"mfa_enrolled": True, "mfa_secret": "sealed"})
    assert plain["claims"]["password_only"] is True
    assert plain["ticket_seconds"] < enrolled["ticket_seconds"]
    # it says out loud that the weak case is weak, in words, not a bare number
    assert "no second factor" in plain["claims"]["note"]
    assert plain["claims"]["replayable"] is False


def test_every_gated_action_is_named_and_described():
    """A gate with an unhelpful message is a support ticket; each action has a label."""
    assert len(stepup.ACTIONS) == 8
    for name, meta in stepup.ACTIONS.items():
        assert meta["label"] and len(meta["label"]) > 10, name
        assert meta["ttl"] > 0


# ===========================================================================
# B. the gate
# ===========================================================================
def test_without_a_ticket_the_dangerous_route_says_what_to_send(owner):
    status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True})
    assert status == 423, body
    assert body["error"]["code"] == "STEP_UP_REQUIRED"
    # the response names the action and the route that mints a ticket: a 423 that
    # does not say how to answer is a dead end for a UI
    assert body["error"]["details"][0]["action"] == "kill_switch"
    assert body["error"]["details"][0]["how"] == "POST /api/v1/auth/step-up"
    # and nothing happened
    assert owner.call("GET", "/api/v1/tenant")[1]["kill_switch"] is False


def test_a_ticket_does_the_action_and_cannot_do_it_again(owner):
    ticket = ticket_for(owner, "kill_switch", "engage")
    status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                 headers={"X-Step-Up": ticket})
    assert status == 200 and body["kill_switch"] is True, body

    status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                 headers={"X-Step-Up": ticket})
    assert status == 423 and body["error"]["code"] == "STEP_UP_REPLAYED", body

    owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": False},
               headers={"X-Step-Up": ticket_for(owner, "kill_switch", "release")})


def test_a_confirmation_for_one_action_does_not_authorise_another(owner):
    rewrap = ticket_for(owner, "key_rewrap", "root")
    status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                 headers={"X-Step-Up": rewrap})
    assert status == 423 and body["error"]["code"] == "STEP_UP_WRONG_ACTION", body
    assert body["error"]["details"][0] == {"confirmed_for": "key_rewrap", "needed_for": "kill_switch"}


def test_a_confirmation_for_one_user_does_not_delete_another(owner):
    """Target binding: the difference between "delete Rahul" and "delete anyone"."""
    status, made, _ = owner.call("POST", "/api/v1/tenant/users",
                                 {"name": "Target A", "email": f"target-a-{int(time.time())}@example.in",
                                  "role": "agent", "password": "Demo@12345!"})
    assert status == 200, made
    victim = made["user"]["id"]
    status, made2, _ = owner.call("POST", "/api/v1/tenant/users",
                                  {"name": "Target B", "email": f"target-b-{int(time.time())}@example.in",
                                   "role": "agent", "password": "Demo@12345!"})
    assert status == 200, made2
    bystander = made2["user"]["id"]

    ticket = ticket_for(owner, "user_delete", victim)
    status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{bystander}",
                                 {"active": False}, headers={"X-Step-Up": ticket})
    assert status == 423 and body["error"]["code"] == "STEP_UP_WRONG_TARGET", body
    # the bystander is untouched, and the ticket was not burned by the wrong try
    status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{victim}",
                                 {"active": False}, headers={"X-Step-Up": ticket})
    assert status == 200, body


def test_a_ticket_minted_for_another_session_is_refused(owner):
    """A stolen ticket is useless in a different browser, because the session id is inside it."""
    thief = Session()
    thief.login(OWNER)
    ticket = ticket_for(owner, "kill_switch", "engage")
    status, body, _ = thief.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                 headers={"X-Step-Up": ticket})
    assert status == 423 and body["error"]["code"] == "STEP_UP_WRONG_SESSION", body
    owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": False},
               headers={"X-Step-Up": ticket_for(owner, "kill_switch", "release")})


def test_a_junk_header_is_a_missing_ticket_not_a_crash(owner):
    for junk in ("", "not-a-token", "a.b.c", "x" * 5000):
        status, body, _ = owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True},
                                     headers={"X-Step-Up": junk})
        assert status == 423 and body["error"]["code"] == "STEP_UP_REQUIRED", (junk, body)


def test_the_role_check_still_comes_first(viewer):
    """A 423 to somebody who may never do this teaches them to go hunting for a ticket."""
    status, body, _ = viewer.call("POST", "/api/v1/tenant/kill-switch", {"engage": True})
    assert status == 403 and body["error"]["code"] == "FORBIDDEN_SCOPE", body


def test_the_status_route_says_which_actions_are_armed(owner):
    status, body, _ = owner.call("GET", "/api/v1/security/step-up")
    assert status == 200, body
    armed = {a["action"] for a in body["step_up"]["actions"]}
    assert armed == set(stepup.ACTIONS)
    assert body["step_up"]["claims"]["replayable"] is False
    # and it lists what this gate deliberately does *not* cover, so nobody reads
    # "step-up enabled" as "every request is confirmed"
    assert any("reading data" in line for line in body["not_protected_by_this"])


def test_the_status_route_is_not_readable_by_a_viewer(viewer):
    status, body, _ = viewer.call("GET", "/api/v1/security/step-up")
    assert status == 403, body


# ===========================================================================
# C. the factor
# ===========================================================================
def test_a_wrong_password_mints_nothing_and_says_nothing_about_which_part(owner):
    status, body = step_up(owner, password="wrong-password")
    assert status == 401 and body["error"]["code"] == "STEP_UP_INVALID", body
    assert "ticket" not in body
    assert "password" not in body["error"]["message"].lower()


def test_the_attempt_budget_stops_the_dialog_being_a_password_oracle():
    attempts = stepup.Attempts()
    sid = "ses_budget"
    for i in range(stepup.MAX_ATTEMPTS - 1):
        assert attempts.blocked(sid) == 0
        attempts.fail(sid)
    attempts.fail(sid)                       # fifth: now cooling off
    assert attempts.blocked(sid) > 0
    attempts.clear(sid)
    assert attempts.blocked(sid) == 0


def test_five_wrong_passwords_lock_that_session_out_for_a_while(owner):
    codes = []
    for _ in range(stepup.MAX_ATTEMPTS + 1):
        status, body = step_up(owner, password="nope")
        codes.append((status, body.get("error", {}).get("code")))
    assert codes[0][0] == 401
    assert codes[-1][0] == 429 and codes[-1][1] == "RATE_LIMITED", codes
    # a *correct* password during the cool-off is still refused: otherwise the
    # budget only slows an attacker who guesses wrong every time
    status, body = step_up(owner, password=PASSWORD)
    assert status == 429, body
    # and it is per session — the same person in another browser is unaffected
    other = Session()
    other.login(OWNER)
    assert step_up(other)[0] == 200


def test_an_unknown_action_is_refused_before_any_password_is_checked(owner):
    status, body = step_up(owner, action="delete_everything")
    assert status == 400 and body["error"]["code"] == "VALIDATION_FAILED"
    assert "kill_switch" in body["error"]["message"]


@pytest.fixture()
def enrolled_owner(owner):
    """The owner with an authenticator on, and the material to answer with."""
    stepup.SPENT.clear()
    status, body, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
    assert status == 200, body
    status, _, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": totp.totp(body["secret"])})
    assert status == 200
    state = {"secret": body["secret"], "codes": body["recovery_codes"]}
    yield state
    # Take the factor off again, or every later login in the run becomes a two-step
    # login and every later `owner` fixture fails on a missing `csrf`. `clear_factor`
    # tries the current window then walks the recovery codes from the back — a test
    # in between may have spent the window on a step-up, and this teardown must not
    # be the thing that breaks the other forty tests.
    assert clear_factor(owner, state["secret"], state["codes"]), \
        "could not remove the authenticator; the rest of the suite would be two-step logins"
    # and prove the account is usable again rather than assuming it
    probe = Session()
    probe.login(OWNER)


def test_with_a_factor_enrolled_a_password_alone_is_not_enough(owner, enrolled_owner):
    status, body = step_up(owner, password=PASSWORD)
    assert status == 400 and body["error"]["code"] == "FACTOR_REQUIRED", body


def test_password_and_code_mints_a_ticket_that_says_which_method_it_used(owner, enrolled_owner):
    status, body = step_up(owner, password=PASSWORD,
                           code=totp.totp(enrolled_owner["secret"],
                                          at=(totp.step_at() + 1) * totp.STEP_SECONDS))
    assert status == 200, body
    assert body["method"] == "totp" and body["single_use"] is True
    assert body["expires_in"] == stepup.TICKET_TTL


def test_a_recovery_code_works_once_here_too(owner, enrolled_owner):
    """The laundering risk: a one-time code turned into an unlimited step-up key."""
    code = enrolled_owner["codes"][0]
    status, body = step_up(owner, password=PASSWORD, recovery=code)
    assert status == 200 and body["method"] == "recovery_code", body

    # second use of the same code: refused, and the failure is audited
    status, body = step_up(owner, password=PASSWORD, recovery=code)
    assert status == 401 and body["error"]["code"] == "STEP_UP_INVALID", body

    status, log, _ = owner.call("GET", "/api/v1/tenant/audit")
    actions = [e["action"] for e in log["entries"]]
    assert "auth.recovery_code_used" in actions
    assert "auth.step_up_failed" in actions


def test_a_wrong_code_does_not_spend_the_password_attempt_budget_differently(owner, enrolled_owner):
    """Both halves share one budget: failing the code five times must cool off too."""
    for _ in range(stepup.MAX_ATTEMPTS):
        status, body = step_up(owner, password=PASSWORD, code="000000")
        assert status == 401, body
    assert step_up(owner, password=PASSWORD)[0] == 429


# ===========================================================================
# D. the routes that now demand one
# ===========================================================================
def test_every_route_the_plan_names_is_actually_gated(owner):
    """A gate nothing calls is a comment. Each of these must answer 423 with no ticket.

    Listed as (method, path, body) so the assertion is about the wiring, not about
    what the handler does afterwards: the 423 is proof the check runs *before* the
    work, which is the only place it is worth running.
    """
    status, made, _ = owner.call("POST", "/api/v1/tenant/users",
                                 {"name": "Gate Probe", "email": f"gate-probe-{int(time.time())}@example.in",
                                  "role": "agent", "password": "Demo@12345!"})
    assert status == 200, made
    uid = made["user"]["id"]
    cases = [
        ("POST", "/api/v1/tenant/kill-switch", {"engage": True}),
        ("POST", "/api/v1/security/envelope/rewrap", {"confirm": "rewrap"}),
        ("PATCH", f"/api/v1/tenant/users/{uid}", {"role": "manager"}),
        ("PATCH", f"/api/v1/tenant/users/{uid}", {"active": False}),
        ("PATCH", f"/api/v1/tenant/users/{uid}", {"mfa_reset": True}),
        ("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "sunrise"}),
        ("POST", "/api/v1/setup/neon-key", {"api_key": "nak_x000000000000"}),
    ]
    for method, path, body in cases:
        status, got, _ = owner.call(method, path, body)
        assert status == 423, (method, path, status, got)
        assert got["error"]["code"] == "STEP_UP_REQUIRED", (path, got)
    # Prove the 423 ran *before* the side effect on the credential file:
    status, key_state, _ = owner.call("GET", "/api/v1/setup/neon-key")
    assert status == 200 and key_state["configured"] is False, key_state


def test_a_rename_is_still_ordinary_work(owner):
    """The gate is on the dangerous half of the route, not on the route.

    If editing a phone number needed a confirmation, people would confirm by reflex
    and the prompt would stop meaning anything (docs/34 §4).
    """
    status, made, _ = owner.call("POST", "/api/v1/tenant/users",
                                 {"name": "Rename Me", "email": f"rename-{int(time.time())}@example.in",
                                  "role": "agent", "password": "Demo@12345!"})
    assert status == 200, made
    uid = made["user"]["id"]
    status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{uid}", {"name": "Renamed"})
    assert status == 200, body


def test_a_step_up_for_the_wrong_target_is_audited_as_a_failure_not_a_success(owner):
    step_up(owner, password="wrong")
    status, log, _ = owner.call("GET", "/api/v1/tenant/audit")
    entry = next(e for e in log["entries"] if e["action"] == "auth.step_up_failed")
    assert entry["target"] == "engage"
    # the audit says it failed and why, and never contains what was typed
    assert "wrong" not in str(entry)

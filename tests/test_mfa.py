"""
Two-factor authentication — the arithmetic, the state machine, and the session.

Three groups, because three different things can be wrong:

  A. the TOTP arithmetic (RFC 4226 / RFC 6238 reference vectors, the ±1 window,
     the single-use rule) — pure functions, no server
  B. enrolment and the second step of a login — HTTP against the throwaway server
  C. the ways a second factor is usually defeated: a challenge token reused as a
     session, a code replayed, a recovery code that works twice, a self-service
     "turn MFA off" that needs nothing but a stolen cookie, an admin who can mark
     an account as protected without enrolling anything

Every test here leaves the shared demo accounts as it found them: the `enrolled`
fixture takes the factor off again, because a suite that leaves an authenticator
on the owner account makes every later login in the run a two-step login.
"""
from __future__ import annotations

import sys
import os
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import totp  # noqa: E402
from conftest import Session, clear_factor, step_up_headers  # noqa: E402

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"


# ===========================================================================
# A. the arithmetic
# ===========================================================================
RFC4226_HOTP = [
    (0, "755224"), (1, "287082"), (2, "359152"), (3, "969429"), (4, "338314"),
    (5, "254676"), (6, "287922"), (7, "162583"), (8, "399871"), (9, "520489"),
]
# RFC 6238 appendix B, SHA-1 rows (the SHA-256/512 rows are different secrets)
RFC6238_TOTP = [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
]
RFC6238_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # base32 of b"12345678901234567890"


@pytest.mark.parametrize("counter,expected", RFC4226_HOTP)
def test_hotp_matches_the_rfc4226_reference_vectors(counter, expected):
    assert totp.hotp(RFC6238_SECRET, counter, digits=6) == expected


@pytest.mark.parametrize("at,expected", RFC6238_TOTP)
def test_totp_matches_the_rfc6238_reference_vectors(at, expected):
    # 8 digits in the RFC; the app uses 6, so compare on the same truncation
    assert totp.totp(RFC6238_SECRET, at=at, digits=8) == expected


def test_a_code_is_six_digits_in_a_thirty_second_step():
    secret = totp.new_secret()
    code = totp.totp(secret)
    assert len(code) == totp.DIGITS == 6 and code.isdigit()
    assert totp.totp(secret, at=990) == totp.totp(secret, at=990 + 29)
    assert totp.totp(secret, at=990) != totp.totp(secret, at=990 + 30)


def test_the_window_accepts_one_step_either_side_and_nothing_beyond():
    secret = totp.new_secret()
    now = 1_700_000_010
    this_step = totp.step_at(now)
    for offset in (-1, 0, 1):
        code = totp.totp(secret, at=(this_step + offset) * totp.STEP_SECONDS)
        verdict = totp.verify(secret, code, at=now)
        assert verdict["ok"] and verdict["step"] == this_step + offset, verdict
    for offset in (-2, 2, 10):
        code = totp.totp(secret, at=(this_step + offset) * totp.STEP_SECONDS)
        assert totp.verify(secret, code, at=now)["ok"] is False


def test_a_code_already_used_is_refused_even_inside_its_window():
    secret = totp.new_secret()
    now = 1_700_000_010
    code = totp.totp(secret, at=now)
    first = totp.verify(secret, code, at=now)
    assert first["ok"]
    again = totp.verify(secret, code, at=now, last_step=first["step"])
    assert again["ok"] is False and again["reason"] == "code_replayed"
    # ... and a code from a window *after* the last one is still fine
    later = totp.totp(secret, at=now + totp.STEP_SECONDS)
    assert totp.verify(secret, later, at=now + totp.STEP_SECONDS, last_step=first["step"])["ok"]


def test_garbage_is_a_wrong_code_not_a_crash():
    secret = totp.new_secret()
    for bad in ("", "   ", "12345", "abcdef", "1234567", None):
        verdict = totp.verify(secret, bad if bad is not None else "")
        assert verdict["ok"] is False and verdict["reason"] in ("code_malformed", "code_incorrect")


def test_a_secret_that_is_not_base32_is_reported_as_such():
    with pytest.raises(totp.TotpError) as exc:
        totp.verify("not a secret!", "123456")
    assert exc.value.code in ("INVALID_SECRET", "MFA_NOT_ENROLLED")


def test_new_secrets_are_20_bytes_of_base32_and_never_repeat():
    secrets = {totp.new_secret() for _ in range(200)}
    assert len(secrets) == 200
    one = next(iter(secrets))
    assert len(totp.decode_secret(one)) == totp.SECRET_BYTES == 20
    assert set(one) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_the_provisioning_uri_carries_the_parameters_an_app_needs():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "owner@realtorestates.app")
    assert uri.startswith("otpauth://totp/")
    for frag in ("secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "issuer=Sell.Do",
                 "algorithm=SHA1", "digits=6", "period=30"):
        assert frag in uri
    # the label is percent-encoded, so a ':' in an email cannot fork the label
    assert "owner%40realtorestates.app" in uri


def test_recovery_codes_are_ten_readable_pairs_that_normalise_to_one_form():
    codes = totp.new_recovery_codes()
    assert len(codes) == totp.RECOVERY_CODES == 10
    for c in codes:
        assert len(c) == 11 and c[5] == "-"
        assert set(c.replace("-", "")) <= set(totp.RECOVERY_ALPHABET)
    # no 0/O/1/I: these get read aloud over a phone and typed by a person
    assert not set("01IO") & set(totp.RECOVERY_ALPHABET)
    assert len({totp.normalise_recovery_code(c) for c in codes}) == 10
    one = codes[3]
    assert totp.normalise_recovery_code(one) == totp.normalise_recovery_code(one.lower())
    assert totp.normalise_recovery_code(one) == totp.normalise_recovery_code(one.replace("-", ""))


def test_two_recovery_codes_are_never_the_same_secret():
    """They are stored as blind indices, and `blind_index` keeps only the digits of
    a value with no "@" in it — so two digit-free codes used to hash identically
    and one would unlock the other. The suite found this as a flake; this test
    makes it a failure."""
    from app import security as sec
    a = sec.blind_token(totp.normalise_recovery_code("ABCDE-FGHJK"))
    b = sec.blind_token(totp.normalise_recovery_code("MNOPQ-RSTUV"))
    assert a != b
    # the primitive that strips non-digits is exactly what must not be used here
    assert sec.blind_index("ABCDE-FGHJK") == sec.blind_index("MNOPQ-RSTUV")
    # and a large sample has no repeats
    codes = totp.new_recovery_codes(200)
    hashes = {sec.blind_token(totp.normalise_recovery_code(c)) for c in codes}
    assert len(hashes) == 200


def test_drift_reports_how_far_the_phone_clock_is_out():
    now = 1_700_000_010
    centre = totp.step_at(now)
    assert totp.drift(centre, at=now) == 0
    assert totp.drift(centre + 2, at=now) == 2
    assert totp.drift(centre - 1, at=now) == -1


# ===========================================================================
# B. enrolment and a two-step login
# ===========================================================================
@pytest.fixture()
def enrolled(owner):
    """An owner session with an authenticator on it, and the material to use it."""
    status, body, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
    assert status == 200, body
    state = {"session": owner, "secret": body["secret"], "codes": body["recovery_codes"],
             "uri": body["otpauth_uri"], "uid": None}
    status, verified, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": totp.totp(body["secret"])})
    assert status == 200, verified
    yield state
    # take it off again, with the last recovery code — no waiting for a window,
    # and if that fails, break the glass from the owner's own admin route
    fresh = Session()
    try:
        fresh.login(OWNER)
    except Exception:
        pass
    if fresh.token:
        status, _, _ = fresh.call("POST", "/api/v1/auth/mfa/disable",
                                  {"password": PASSWORD, "recovery_code": state["codes"][-1]})
        if status == 200:
            return
    # `fresh` is a Session object either way, but only a *signed-in* one has cookies:
    # a login to an account with a factor returns a challenge, so `fresh.token` is
    # the test for "this client can act", not "this object exists"
    client = fresh if fresh.token else owner
    assert clear_factor(client, state["secret"], state["codes"]), \
        "the fixture could not turn the factor off again — every later login is a two-step login"


def challenge_for(email: str = OWNER, password: str = PASSWORD) -> dict:
    """The body the password half of a login returns once an authenticator is on."""
    status, body, headers = Session().call("POST", "/api/v1/auth/login",
                                           {"email": email, "password": password}, csrf=False)
    assert status == 200 and body.get("mfa_required"), body
    assert body["challenge"], body
    return body


def sign_in_with_code(secret: str, challenge: dict, tries: int = 2) -> tuple[Session, dict]:
    """A full two-step login with a TOTP code.

    Codes are single-use *within* their 30-second window, so a test that needs a
    second login in the same window would be asking for a code the server has
    already seen. A person waits for the next one; so does this (never longer
    than one window, and only once).
    """
    last = None
    for attempt in range(tries):
        if attempt:
            time.sleep(totp.STEP_SECONDS - (time.time() % totp.STEP_SECONDS) + 0.5)
        session = Session()
        body = challenge if attempt == 0 else challenge_for()
        code = totp.totp(secret, at=(totp.step_at() + 1) * totp.STEP_SECONDS)
        status, got, headers = session.call("POST", "/api/v1/auth/login/mfa",
                                            {"challenge": body["challenge"], "code": code}, csrf=False)
        if status == 200:
            session.token, session.csrf = got["token"], got["csrf"]
            return session, headers
        last = (status, got)
    raise AssertionError(f"a code was refused twice: {last}")


def sign_in_with_recovery(recovery: str, challenge: dict | None = None) -> Session:
    session = Session()
    body = challenge or challenge_for()
    status, got, _ = session.call("POST", "/api/v1/auth/login/mfa",
                                  {"challenge": body["challenge"], "recovery_code": recovery}, csrf=False)
    assert status == 200, got
    session.token, session.csrf = got["token"], got["csrf"]
    return session


def _owner_id(session: Session) -> str:
    return session.call("GET", "/api/v1/auth/me")[1]["user"]["id"]


def test_the_state_route_says_what_is_true_not_what_was_configured(owner):
    status, body, _ = owner.call("GET", "/api/v1/auth/mfa")
    assert status == 200
    assert body["mfa"]["enrolled"] is False
    assert body["digits"] == 6 and body["period"] == 30
    # nothing about a secret is ever served
    assert "secret" not in str(body)


def test_enrolling_hands_over_one_secret_and_ten_codes_but_switches_nothing_on(owner):
    status, body, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
    assert status == 200 and len(body["recovery_codes"]) == 10
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    _, state, _ = owner.call("GET", "/api/v1/auth/mfa")
    assert state["mfa"]["enrolled"] is False, "a secret nobody has scanned must not be a factor yet"
    # re-opening the dialog before scanning replaces the pending secret — and says so
    status, again, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
    assert status == 200 and again["replaced_pending"] is True
    assert "void" in again["warning"] and again["secret"] != body["secret"]


def test_a_wrong_code_does_not_enrol_and_the_right_one_does(owner):
    _, body, _ = owner.call("POST", "/api/v1/auth/mfa/enrol", {})
    status, err, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": "000000"})
    assert status == 400 and err["error"]["code"] == "MFA_CODE_INVALID"
    assert owner.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is False
    status, ok, _ = owner.call("POST", "/api/v1/auth/mfa/verify", {"code": totp.totp(body["secret"])})
    assert status == 200 and ok["mfa"]["enrolled"] is True
    assert ok["mfa"]["recovery_codes_left"] == 10
    # The account now has a factor, so the break-glass reset itself needs the factor
    # (`docs/34`: a confirmation that is not confirmed is a stolen cookie away). The
    # recovery code is the right factor here — it is outside the 30-second window
    # and outside the replay guard, and it is what a person with a lost phone uses.
    uid = _owner_id(owner)
    owner.call("PATCH", f"/api/v1/tenant/users/{uid}", {"mfa_reset": True},
               headers=step_up_headers(owner, "mfa_reset", uid, recovery=body["recovery_codes"][-1]))
    owner2 = Session()
    owner2.login(OWNER)   # leaves nothing behind for the next test


def test_a_password_alone_now_returns_a_challenge_instead_of_a_session(enrolled):
    fresh = Session()
    status, body, headers = fresh.call("POST", "/api/v1/auth/login",
                                       {"email": OWNER, "password": PASSWORD}, csrf=False)
    assert status == 200
    assert body["mfa_required"] is True
    assert "token" not in body and "csrf" not in body
    assert body["methods"] == ["totp", "recovery_code"]
    assert body["user_hint"].startswith("ow") and "@" in body["user_hint"], "the hint must not dox the mailbox"
    assert "re_session=" not in (headers.get("Set-Cookie") or ""), "no cookie may be set before the code"
    assert body["challenge"] and body["expires_in"] <= 300


def test_the_challenge_cannot_be_used_as_a_session(enrolled):
    """The single most important test in this file.

    The challenge is signed by the same key and names the same user, so any door
    that accepts a token without asking what kind of token it is would accept a
    password as a session (docs/27).
    """
    fresh = Session()
    _, body, _ = fresh.call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD}, csrf=False)
    challenge = body["challenge"]
    for path in ("/api/v1/auth/me", "/api/v1/contacts", "/api/v1/tenant", "/api/v1/money/summary"):
        status, err, _ = fresh.call("GET", path, headers={"Authorization": "Bearer " + challenge}, csrf=False)
        assert status == 401, f"{path} accepted a two-factor challenge as a session"
    assert err["error"]["code"] in ("MFA_CHALLENGE_NOT_A_SESSION", "UNAUTHENTICATED")


def test_the_challenge_plus_a_code_is_a_session_and_the_code_is_spent(enrolled):
    challenge = challenge_for()
    code = totp.totp(enrolled["secret"], at=(totp.step_at() + 1) * totp.STEP_SECONDS)
    fresh, headers = sign_in_with_code(enrolled["secret"], challenge)
    assert "re_session=" in (headers.get("Set-Cookie") or ""), "the second step must set the cookie"
    assert fresh.call("GET", "/api/v1/auth/me")[0] == 200
    me = fresh.call("GET", "/api/v1/auth/me")[1]
    assert me["user"]["mfa"]["enrolled"] is True
    # the same code does not work again — on the same challenge or a new one
    replay = Session()
    status, again, _ = replay.call("POST", "/api/v1/auth/login/mfa",
                                   {"challenge": challenge["challenge"], "code": code}, csrf=False)
    assert status == 401 and "already been used" in again["error"]["message"]


def test_an_expired_or_forged_challenge_is_refused(enrolled):
    for bad in ("", "not-a-token", "a.b.c"):
        status, body, _ = Session().call("POST", "/api/v1/auth/login/mfa",
                                         {"challenge": bad, "code": "123456"}, csrf=False)
        assert status == 401 and body["error"]["code"] == "MFA_CHALLENGE_INVALID"


def test_a_recovery_code_signs_in_once_and_survives_shouting(enrolled):
    codes = enrolled["codes"]
    # read aloud, typed in lower case, without the dash: still the same code
    fresh = sign_in_with_recovery(codes[0].lower().replace("-", ""))
    second = Session()
    body = challenge_for()
    status, err, _ = second.call("POST", "/api/v1/auth/login/mfa",
                                 {"challenge": body["challenge"], "recovery_code": codes[0]}, csrf=False)
    assert status == 401, f"a recovery code is a one-time secret, not a password ({status})"
    assert fresh.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["recovery_codes_left"] == 9


def test_regenerating_recovery_codes_voids_the_previous_ten(enrolled):
    """New codes need the password *and* a factor. A recovery code counts as the
    factor here, because losing the phone is precisely when people need new ones."""
    fresh = sign_in_with_recovery(enrolled["codes"][1])
    status, err, _ = fresh.call("POST", "/api/v1/auth/mfa/recovery-codes",
                                {"password": "wrong-password", "recovery_code": enrolled["codes"][2]})
    assert status == 401, "the password is the point of this route"
    status, made, _ = fresh.call("POST", "/api/v1/auth/mfa/recovery-codes",
                                 {"password": PASSWORD, "recovery_code": enrolled["codes"][2]})
    assert status == 200, made
    assert len(made["recovery_codes"]) == 10 and made["replaced"] == 10
    left = fresh.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["recovery_codes_left"]
    assert made["recovery_codes"] != enrolled["codes"], "the endpoint returned the old set"
    # the old set is void, including the code just used and its neighbours
    for spent in (enrolled["codes"][2], enrolled["codes"][3]):
        login = Session()
        _, body, _ = login.call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD}, csrf=False)
        status, got, _ = login.call("POST", "/api/v1/auth/login/mfa",
                                    {"challenge": body["challenge"], "recovery_code": spent}, csrf=False)
        assert status == 401, (f"{spent} still opened a session; codes_left={left}\n"
                               f"  old: {enrolled['codes'][:4]}\n"
                               f"  new: {made['recovery_codes'][:4]}\n  got: {got}")
    # the new set works
    login = Session()
    _, body, _ = login.call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD}, csrf=False)
    status, session, _ = login.call("POST", "/api/v1/auth/login/mfa",
                                    {"challenge": body["challenge"],
                                     "recovery_code": made["recovery_codes"][0]}, csrf=False)
    assert status == 200 and session["token"]
    _, state, _ = login.call("GET", "/api/v1/auth/mfa")
    assert state["mfa"]["recovery_codes_left"] == 9


# ===========================================================================
# C. the ways a second factor gets defeated
# ===========================================================================
def test_turning_it_off_needs_the_password_and_a_factor(enrolled):
    fresh = sign_in_with_recovery(enrolled["codes"][1])
    spare = enrolled["codes"][2]
    # a stolen cookie alone cannot remove the factor
    assert fresh.call("POST", "/api/v1/auth/mfa/disable", {"password": PASSWORD})[0] == 401
    assert fresh.call("POST", "/api/v1/auth/mfa/disable", {"recovery_code": spare})[0] == 401
    assert fresh.call("POST", "/api/v1/auth/mfa/disable", {"password": "nope", "recovery_code": spare})[0] == 401
    # still on
    assert fresh.call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is True
    # both, and only both, switch it off
    status, body, _ = fresh.call("POST", "/api/v1/auth/mfa/disable",
                                 {"password": PASSWORD, "recovery_code": spare})
    assert status == 200 and body["mfa"]["enrolled"] is False
    _, state, _ = fresh.call("GET", "/api/v1/auth/mfa")
    assert state["mfa"]["recovery_codes_left"] == 0, "the codes go with the factor"
    # and the desk is password-only again
    back = Session()
    back.login(OWNER)
    assert back.call("GET", "/api/v1/auth/me")[0] == 200


def test_the_flag_cannot_be_flipped_by_patching_a_user_row(enrolled):
    """`mfa_enabled` was a writable boolean: an admin could assert a factor that
    did not exist. It is gone from the patchable set, and the reset is a
    deliberate, owner-only, audited act instead."""
    owner_id = _owner_id(enrolled["session"])
    status, body, _ = enrolled["session"].call("PATCH", f"/api/v1/tenant/users/{owner_id}",
                                              {"mfa_enabled": False})
    assert status == 200
    assert enrolled["session"].call("GET", "/api/v1/auth/mfa")[1]["mfa"]["enrolled"] is True, \
        "a patch must not be able to lie about, or remove, a real factor"
    row = [u for u in enrolled["session"].call("GET", "/api/v1/tenant/users")[1]["users"]
           if u["id"] == owner_id][0]
    assert row["mfa_enrolled"] is True


def test_five_bad_codes_lock_the_factor_even_with_the_right_password(enrolled):
    fresh = Session()
    for _ in range(5):
        _, body, _ = fresh.call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD}, csrf=False)
        status, _, _ = fresh.call("POST", "/api/v1/auth/login/mfa",
                                  {"challenge": body["challenge"], "code": "000000"}, csrf=False)
        assert status == 401
    body = challenge_for()
    code = totp.totp(enrolled["secret"], at=(totp.step_at() + 1) * totp.STEP_SECONDS)
    status, err, _ = fresh.call("POST", "/api/v1/auth/login/mfa",
                                {"challenge": body["challenge"], "code": code}, csrf=False)
    assert status == 423, f"expected a lockout, got {status} {err}"
    assert err["error"]["code"] == "ACCOUNT_LOCKED"
    # the owner's own admin route is the way back in, and it clears the lock
    owner_id = _owner_id(enrolled["session"])
    # the way back in is a *recovery code*, not a TOTP code: the account is locked
    # out of its codes by design, and a lockout that also refused the break-glass
    # key would let anyone lock the owner out of their own account (docs/34 §7)
    enrolled["session"].call("PATCH", f"/api/v1/tenant/users/{owner_id}", {"mfa_reset": True},
                             headers=step_up_headers(enrolled["session"], "mfa_reset", owner_id,
                                                     recovery=enrolled["codes"][-1]))
    status, back, _ = Session().call("POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD}, csrf=False)
    assert status == 200 and "token" in back, "after a reset the demo is password-only again"


def test_only_an_owner_may_reset_someone_elses_factor(owner):
    """`mfa_reset` is a break-glass key: an admin who runs the user directory still
    may not use it, and a manager cannot even reach the route."""
    stamp = int(time.time())
    email = f"mfa-admin-{stamp}@example.invalid"
    created = owner.call("POST", "/api/v1/tenant/users",
                         {"email": email, "role": "admin", "password": "Demo@12345!"})
    assert created[0] == 200, created
    admin = Session()
    admin.login(email)
    assert admin.call("GET", "/api/v1/tenant/users")[0] == 200, "an admin runs the directory"
    target = admin.call("GET", "/api/v1/auth/me")[1]["user"]["id"]
    # With a confirmation in hand an admin still fails — on the *role*, which is the
    # point of this test. Without one they would fail on the 423 first, and a test
    # that asserts the wrong refusal proves nothing about the one it names.
    status, body, _ = admin.call("PATCH", f"/api/v1/tenant/users/{target}", {"mfa_reset": True},
                                 headers=step_up_headers(admin, "mfa_reset", target))
    assert status == 403 and "owner" in body["error"]["message"].lower()
    # a manager cannot reach the directory at all
    manager = Session()
    manager.login("manager@realtorestates.app")
    status, body, _ = manager.call("PATCH", f"/api/v1/tenant/users/{target}", {"mfa_reset": True})
    assert status == 403 and body["error"]["code"] == "FORBIDDEN_SCOPE"


def test_a_workspace_can_demand_a_factor_before_dangerous_actions(owner, viewer):
    """`require_mfa` is enforced on the actions that change who exists, what the
    workspace is, or where money goes — not on reading the pipeline."""
    assert owner.call("PATCH", "/api/v1/tenant", {"settings": {"require_mfa": True}})[0] == 200
    try:
        me = owner.call("GET", "/api/v1/auth/me")[1]
        assert me["user"]["mfa"]["required"] is True
        assert me["user"]["mfa"]["pending"] is True
        status, body, _ = owner.call("POST", "/api/v1/tenant/users",
                                     {"email": f"mfa-gate-{int(time.time())}@example.invalid",
                                      "role": "agent", "password": "Demo@12345!"})
        assert status == 403 and body["error"]["code"] == "MFA_REQUIRED"
        # the day's work is not blocked: reads and ordinary writes still work
        assert owner.call("GET", "/api/v1/contacts?limit=1")[0] == 200
        assert viewer.call("GET", "/api/v1/pipeline")[0] == 200
    finally:
        owner.call("PATCH", "/api/v1/tenant", {"settings": {"require_mfa": False}})
    assert owner.call("GET", "/api/v1/auth/me")[1]["user"]["mfa"]["required"] is False
    assert owner.call("POST", "/api/v1/tenant/users",
                      {"email": f"mfa-gate-{int(time.time())}b@example.invalid",
                       "role": "agent", "password": "Demo@12345!"})[0] == 200


def test_enrolment_is_per_account_not_per_workspace(enrolled, viewer):
    """The viewer's session must not inherit the owner's factor."""
    state = viewer.call("GET", "/api/v1/auth/mfa")[1]
    assert state["mfa"]["enrolled"] is False
    login = Session()
    _, body, _ = login.call("POST", "/api/v1/auth/login",
                            {"email": "viewer@realtorestates.app", "password": PASSWORD}, csrf=False)
    assert "token" in body and not body.get("mfa_required")

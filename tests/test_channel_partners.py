"""
Channel partners: registration claims, protection windows, earnings, payouts.

The interesting failures here are not crashes — they are a partner reading the
whole CRM, a partner approving their own claim, a claim that never expires, or
commission shown as if it were a promise. Each of those is a test below.
"""
from __future__ import annotations

import time

import pytest

from conftest import BASE, Session, step_up_headers  # noqa: F401
from app import partners as cpm

DAY = 86400


def a_contact(**over):
    c = {"id": "cnt_1", "name": "Buyer", "created_at": 1_800_000_000 - 400 * DAY}
    c.update(over)
    return c


def a_reg(**over):
    r = {
        "id": "cpr_1",
        "cp_user_id": "usr_cp1",
        "contact_id": "cnt_1",
        "status": "approved",
        "protection_until": 1_800_000_000 + 30 * DAY,
    }
    r.update(over)
    return r


# ---------------------------------------------------------------------------
# 1. the decision
# ---------------------------------------------------------------------------
def test_a_free_number_is_a_new_claim_with_a_window():
    out = cpm.decide(cp_id="usr_cp1", contact=None, registration=None, other_registration=None, at=1_800_000_000)
    assert out["outcome"] == "new"
    assert out["protected_until"] == 1_800_000_000 + cpm.PROTECTION_DAYS * DAY


def test_the_same_partner_registering_twice_is_idempotent():
    out = cpm.decide(cp_id="usr_cp1", contact=a_contact(), registration=a_reg(), other_registration=None, at=1_800_000_000)
    assert out["outcome"] == "duplicate"
    assert out["registration_id"] == "cpr_1"


def test_another_partners_live_claim_is_a_conflict_with_days_left():
    out = cpm.decide(cp_id="usr_cp2", contact=a_contact(), registration=None,
                     other_registration=a_reg(cp_user_id="usr_cp1"), at=1_800_000_000)
    assert out["outcome"] == "conflict"
    assert out["protection_days_left"] == 30


def test_an_expired_claim_releases_the_buyer():
    stale = a_reg(cp_user_id="usr_cp1", protection_until=1_800_000_000 - 60)
    out = cpm.decide(cp_id="usr_cp2", contact=a_contact(), registration=None,
                     other_registration=stale, at=1_800_000_000)
    assert out["outcome"] == "new"
    assert out["released_from"] == "cpr_1"


def test_the_houses_own_lead_cannot_be_claimed():
    out = cpm.decide(cp_id="usr_cp1", contact=a_contact(created_at=1_800_000_000 - 3 * DAY),
                     registration=None, other_registration=None, at=1_800_000_000)
    assert out["outcome"] == "existing_lead"
    assert out["contact_age_days"] == 3


def test_registration_expiry_is_derived_not_stored():
    reg = a_reg(protection_until=1_800_000_000 - 1)
    assert cpm.registration_state(reg, 1_800_000_000) == "expired"
    assert cpm.registration_state(a_reg(), 1_800_000_000) == "approved"
    assert cpm.registration_state(a_reg(status="rejected"), 1_800_000_000) == "rejected"


def test_commission_percentage_is_clamped_not_trusted():
    assert cpm.clamp_pct(None) == cpm.DEFAULT_CP_PCT
    assert cpm.clamp_pct("7.5") == 7.5
    with pytest.raises(cpm.PartnerError):
        cpm.clamp_pct(40)
    with pytest.raises(cpm.PartnerError):
        cpm.clamp_pct("lots")


def test_protection_window_is_bounded():
    assert cpm.protection_until(0, 0) == 1 * DAY        # clamped up to a day
    assert cpm.protection_until(0, 5000) == 365 * DAY   # and down to a year


# ---------------------------------------------------------------------------
# 2. earnings
# ---------------------------------------------------------------------------
def test_recorded_commission_beats_an_estimate():
    deal = {
        "id": "deal_1", "contact_id": "cnt_1", "stage": "won", "value": 5_000_000,
        "commission": {"rate_pct": 2.0, "channel_partner_id": "usr_cp1", "channel_partner_pct": 12.0,
                       "channel_partner_paise": 12_000_00, "gross_commission": 100_000},
    }
    row = cpm.earnings_rows([deal], {"cnt_1"}, "usr_cp1", 10.0)[0]
    assert row["basis"] == "recorded"
    assert row["amount"] == 12_000.0             # 12% of a ₹1,00,000 gross, as recorded
    assert row["payout_status"] == "pending"


def test_a_deal_in_progress_is_an_estimate_and_says_so():
    deal = {"id": "deal_2", "contact_id": "cnt_1", "stage": "negotiation", "value": 4_000_000, "commission": {}}
    row = cpm.earnings_rows([deal], {"cnt_1"}, "usr_cp1", 10.0)[0]
    assert row["basis"] == "expected"
    assert row["amount"] == 8_000.0              # 10% of a 2% gross = ₹8,000
    assert row["due"] is False


def test_deals_for_other_buyers_are_excluded():
    deal = {"id": "deal_3", "contact_id": "cnt_other", "stage": "won", "value": 4_000_000, "commission": {}}
    assert cpm.earnings_rows([deal], {"cnt_1"}, "usr_cp1", 10.0) == []


def test_summary_splits_due_pending_and_pipeline():
    rows = [
        {"due": True, "amount": 1000.0, "payout_status": "pending"},
        {"due": True, "amount": 500.0, "payout_status": "paid"},
        {"due": False, "amount": 250.0, "payout_status": "not_due"},
    ]
    s = cpm.earnings_summary(rows)
    assert s["due_total"] == 1500.0 and s["paid_total"] == 500.0
    assert s["pending_total"] == 1000.0 and s["pipeline_estimate"] == 250.0


def test_only_a_completed_visit_is_credited():
    visits = [
        {"contact_id": "cnt_1", "status": "visited"},
        {"contact_id": "cnt_1", "status": "revisit"},
        {"contact_id": "cnt_1", "status": "scheduled"},
        {"contact_id": "cnt_1", "status": "dropped"},
        {"contact_id": "cnt_2", "status": "visited"},
    ]
    assert cpm.credit_visit_count(visits, {"cnt_1"}) == 2


# ---------------------------------------------------------------------------
# 3. over HTTP
# ---------------------------------------------------------------------------
@pytest.fixture()
def partner_one():
    s = Session()
    s.login("partner@nandedproperty.in", "Partner@12345!")
    return s


@pytest.fixture()
def partner_two():
    s = Session()
    s.login("partner@saibrokers.in", "Partner@12345!")
    return s


@pytest.fixture()
def manager():
    s = Session()
    s.login("manager@realtorestates.app")
    return s


_SEQ = 0


def _new_number() -> str:
    """A number no other test (or millisecond) can collide with."""
    global _SEQ
    _SEQ += 1
    return "+9199" + str(int(time.time() * 1000))[-6:] + f"{_SEQ % 100:02d}"


def test_the_partner_login_is_confined_to_the_partner_portal(partner_one):
    for path in ("/api/v1/contacts", "/api/v1/pipeline", "/api/v1/tenant/audit",
                 "/api/v1/crm/summary", "/api/v1/leads/sla", "/api/v1/cp/partners"):
        status, body, _ = partner_one.call("GET", path)
        assert status == 403, (path, status, body)
        assert body["error"]["code"] == "FORBIDDEN_SCOPE"
    # ... but their own portal works
    for path in ("/api/v1/cp/me", "/api/v1/cp/leads", "/api/v1/cp/visits", "/api/v1/cp/earnings"):
        assert partner_one.call("GET", path)[0] == 200, path


def test_a_partner_sees_only_their_own_buyers(partner_one, partner_two):
    n1, n2 = _new_number(), _new_number()
    a = partner_one.call("POST", "/api/v1/cp/register", {"name": "Buyer One", "phone": n1})[1]
    b = partner_two.call("POST", "/api/v1/cp/register", {"name": "Buyer Two", "phone": n2})[1]
    assert a["outcome"] == "created" and b["outcome"] == "created"

    mine = partner_one.call("GET", "/api/v1/cp/leads")[1]["leads"]
    ids = {r["contact_id"] for r in mine}
    assert a["contact_id"] in ids and b["contact_id"] not in ids


def test_registering_the_same_buyer_twice_is_idempotent_for_one_partner_and_a_conflict_for_the_other(
        partner_one, partner_two):
    number = _new_number()
    first = partner_one.call("POST", "/api/v1/cp/register", {"name": "Shared Buyer", "phone": number})[1]
    again = partner_one.call("POST", "/api/v1/cp/register", {"name": "Shared Buyer", "phone": number})
    assert again[0] == 200 and again[1]["outcome"] == "duplicate"
    assert again[1]["registration"]["id"] == first["registration"]["id"]

    status, body, _ = partner_two.call("POST", "/api/v1/cp/register", {"name": "Shared Buyer", "phone": number})
    assert status == 409 and body["error"]["code"] == "CP_LEAD_CONFLICT"


def test_a_lead_already_in_the_house_pipeline_cannot_be_claimed(partner_one, owner):
    fresh = _new_number()
    created = owner.call("POST", "/api/v1/contacts", {"name": "House Lead", "phone": fresh})
    assert created[0] == 200, created
    status, body, _ = partner_one.call("POST", "/api/v1/cp/register", {"name": "House Lead", "phone": fresh})
    assert status == 409 and body["error"]["code"] == "CP_LEAD_ALREADY_IN_PIPELINE"


def test_only_a_partner_can_use_the_partner_register_endpoint(owner):
    status, body, _ = owner.call("POST", "/api/v1/cp/register", {"name": "X", "phone": _new_number()})
    assert status == 403


def test_a_claim_needs_a_manager_to_approve_and_then_protects_the_buyer(partner_one, partner_two, manager, owner):
    number = _new_number()
    reg = partner_one.call("POST", "/api/v1/cp/register", {"name": "Needs Approval", "phone": number})[1]["registration"]
    assert reg["status"] == "pending"

    # an agent cannot decide
    agent = Session()
    agent.login("agent@realtorestates.app")
    assert agent.call("POST", f"/api/v1/cp/registrations/{reg['id']}/decide", {"decision": "approve"})[0] == 403

    status, body, _ = manager.call("POST", f"/api/v1/cp/registrations/{reg['id']}/decide", {"decision": "approve"})
    assert status == 200, body
    approved = body["registration"]
    assert approved["status"] == "approved" and approved["protection_until"] > time.time()
    assert approved["protection_until"] - time.time() > 80 * DAY

    # now the second partner is locked out
    status, body, _ = partner_two.call("POST", "/api/v1/cp/register", {"name": "Needs Approval", "phone": number})
    assert status == 409 and body["error"]["code"] == "CP_LEAD_CONFLICT"

    # and it cannot be decided twice
    status, body, _ = manager.call("POST", f"/api/v1/cp/registrations/{reg['id']}/decide", {"decision": "reject"})
    assert status == 409 and body["error"]["code"] == "ALREADY_DECIDED"


def test_a_rejected_claim_leaves_the_buyer_claimable(partner_one, partner_two, manager):
    number = _new_number()
    reg = partner_one.call("POST", "/api/v1/cp/register", {"name": "Reject Me", "phone": number})[1]["registration"]
    status, body, _ = manager.call("POST", f"/api/v1/cp/registrations/{reg['id']}/decide",
                                   {"decision": "reject", "reason": "buyer asked to be contacted by us directly"})
    assert status == 200 and body["registration"]["status"] == "rejected"
    assert "contacted by us" in body["registration"]["reason"]
    status, body, _ = partner_two.call("POST", "/api/v1/cp/register", {"name": "Reject Me", "phone": number})
    assert status == 200 and body["outcome"] == "created"


def test_earnings_and_payouts_are_scoped_and_permissioned(partner_one, owner, manager):
    earnings = partner_one.call("GET", "/api/v1/cp/earnings")[1]
    assert "summary" in earnings and "earnings" in earnings
    for row in earnings["earnings"]:
        assert row["basis"] in ("recorded", "expected")
        assert row["amount"] >= 0

    payouts = owner.call("GET", "/api/v1/cp/payouts")[1]
    assert "payouts" in payouts
    # a partner cannot see the house-side payout ledger at all
    assert partner_one.call("GET", "/api/v1/cp/payouts")[0] == 403

    won = [p for p in payouts["payouts"] if p["status"] == "pending"]
    if won:
        deal_id = won[0]["deal_id"]
        # an agent, a manager and a partner may not pay
        agent = Session(); agent.login("agent@realtorestates.app")
        assert agent.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "x"})[0] == 403
        assert manager.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "x"})[0] == 403
        assert partner_one.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "x"})[0] == 403
        status, body, _ = owner.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "UTR-99881"})
        assert status == 200, body
        assert body["payout"]["status"] == "paid"
        after = owner.call("GET", "/api/v1/cp/payouts")[1]["payouts"]
        assert next(p for p in after if p["deal_id"] == deal_id)["reference"] == "UTR-99881"


def test_paying_a_deal_that_is_not_closed_is_refused(owner):
    pipeline = owner.call("GET", "/api/v1/pipeline")[1]
    open_deal = next(d for rows in pipeline["columns"].values() for d in rows if d.get("stage") not in ("won",))
    status, body, _ = owner.call("POST", f"/api/v1/cp/payouts/{open_deal['id']}/pay", {"reference": "x"})
    assert status in (409,)
    assert body["error"]["code"] in ("DEAL_NOT_CLOSED", "NO_PARTNER_CREDIT")


def test_creating_a_partner_returns_the_password_once_and_it_works(owner):
    email = f"cp{int(time.time() * 1000) % 10**8}@example.in"
    status, body, _ = owner.call("POST", "/api/v1/cp/partners", {
        "name": "Test Partner", "email": email, "firm": "Test Realty", "commission_pct": 9.5,
        "protection_days": 45, "phone": "9876543210",
    })
    assert status == 200, body
    temp = body["temp_password"]
    assert len(temp) >= 10
    partner = body["partner"]
    assert partner["commission_pct"] == 9.5 and partner["protection_days"] == 45

    listing = owner.call("GET", "/api/v1/cp/partners")[1]["partners"]
    assert any(p["user_id"] == partner["user_id"] for p in listing)
    assert temp not in str(listing)
    assert "password_hash" not in str(listing)

    s = Session()
    body = s.login(email, temp)
    assert body["user"]["role"] == "channel_partner"
    assert s.call("GET", "/api/v1/cp/me")[0] == 200


def test_partner_commission_inputs_are_validated(owner):
    stamp = int(time.time() * 1000)
    assert owner.call("POST", "/api/v1/cp/partners",
                      {"name": "Bad Pct", "email": f"bad{stamp}a@example.in", "commission_pct": 99})[0] == 400
    assert owner.call("POST", "/api/v1/cp/partners",
                      {"name": "Bad Days", "email": f"bad{stamp}b@example.in", "protection_days": 9999})[0] == 400
    assert owner.call("POST", "/api/v1/cp/partners",
                      {"name": "Fine", "email": f"bad{stamp}c@example.in", "commission_pct": 5, "protection_days": 45})[0] == 200


def test_a_partner_seat_cannot_be_created_or_converted_through_the_user_routes(owner, partner_one):
    me = partner_one.call("GET", "/api/v1/cp/me")[1]["partner"]
    status, body, _ = owner.call("POST", "/api/v1/tenant/users",
                                 {"email": f"x{int(time.time())}@example.in", "role": "channel_partner"})
    assert status == 400
    # a role change is step-up gated (docs/34), so the confirmation comes first and
    # the *seat rule* is what refuses it — 400, not 423
    status, body, _ = owner.call("PATCH", f"/api/v1/tenant/users/{me['user_id']}", {"role": "agent"},
                                 headers=step_up_headers(owner, "role_change", me["user_id"]))
    assert status == 400, body


def test_another_tenant_sees_nothing_of_our_partners(owner, other_tenant):
    regs = owner.call("GET", "/api/v1/cp/registrations")[1]["registrations"]
    assert regs
    theirs = other_tenant.call("GET", "/api/v1/cp/registrations")[1]["registrations"]
    assert theirs == []
    status, _, _ = other_tenant.call("POST", f"/api/v1/cp/registrations/{regs[0]['id']}/decide", {"decision": "approve"})
    assert status == 404


def test_the_audit_log_records_the_whole_claim_lifecycle(owner, partner_one, manager):
    number = _new_number()
    reg = partner_one.call("POST", "/api/v1/cp/register", {"name": "Audit Buyer", "phone": number})[1]["registration"]
    manager.call("POST", f"/api/v1/cp/registrations/{reg['id']}/decide", {"decision": "approve"})
    actions = [e["action"] for e in owner.call("GET", "/api/v1/tenant/audit")[1]["entries"]]
    assert "cp.registration_created" in actions
    assert "cp.registration_approved" in actions

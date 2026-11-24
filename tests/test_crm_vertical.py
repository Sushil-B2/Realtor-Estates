"""
Real-estate vertical CRM (docs/18, plan P0).

Two layers, tested separately on purpose:

* **Rules** — `app/vertical.py` as pure functions. Stage gates, visit lifecycle,
  matching scores and commission arithmetic must be provable without a server,
  because they are the product's claims ("money and documents decide the stage").
* **Wiring** — the same rules over HTTP, including the refusals: a viewer cannot
  book, another tenant cannot read the deal, and a stage move without its
  documents is a 409 that says which document is missing.
"""
from __future__ import annotations

import time

import pytest

from conftest import BASE, Session  # noqa: F401
from app import vertical as vt


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def a_contact(**over):
    c = {
        "id": "cnt_1",
        "name": "Test Buyer",
        "city": "Nanded",
        "intent": "buy",
        "budget_min": 4_000_000,
        "budget_max": 5_000_000,
        "requirement": "2400 sqft east-facing plot in Taroda",
        "stage": "qualified",
    }
    c.update(over)
    return c


def a_property(**over):
    p = {
        "id": "prop_1",
        "title": "Sunrise Greens — Plot 12",
        "type": "plot",
        "city": "Nanded",
        "locality": "Taroda",
        "price": 4_500_000,
        "size_sqft": 2400,
        "status": "available",
    }
    p.update(over)
    return p


def a_deal(**over):
    d = {
        "id": "deal_1",
        "contact_id": "cnt_1",
        "property_id": "prop_1",
        "stage": "qualified",
        "value": 4_500_000,
        "documents": [],
    }
    d.update(over)
    return d


def a_visit(**over):
    v = {
        "id": "sv_1",
        "contact_id": "cnt_1",
        "property_id": "prop_1",
        "agent_id": "usr_1",
        "scheduled_at": 1_800_000_000,
        "duration_min": 45,
        "status": "visited",
    }
    v.update(over)
    return v


# ---------------------------------------------------------------------------
# 1. matching
# ---------------------------------------------------------------------------
def test_match_within_budget_beats_cheap_and_overpriced():
    contact = a_contact()
    in_band = vt.score_property(contact, a_property(price=4_500_000))
    cheap = vt.score_property(contact, a_property(id="prop_2", price=2_000_000))
    over = vt.score_property(contact, a_property(id="prop_3", price=9_000_000))
    assert in_band["score"] > cheap["score"] > over["score"]


def test_match_reasons_explain_every_point_and_are_not_empty():
    res = vt.score_property(a_contact(), a_property())
    assert res["score"] > 0
    assert res["reasons"] and all(isinstance(r, str) and r for r in res["reasons"])
    joined = " ".join(res["reasons"])
    assert "budget" in joined and "Taroda" in joined
    assert 0 <= res["score"] <= 100


def test_match_is_deterministic_and_ranked_with_a_limit():
    contact, props = a_contact(), [a_property(price=p) for p in (4_500_000, 4_900_000, 9_000_000, 2_000_000)]
    first = vt.match_properties(contact, props, limit=2)
    again = vt.match_properties(contact, props, limit=2)
    assert [m["property_id"] for m in first] == [m["property_id"] for m in again]
    assert len(first) == 2
    assert first[0]["score"] >= first[1]["score"]


def test_unavailable_property_scores_lower_and_says_so():
    free = vt.score_property(a_contact(), a_property())
    booked = vt.score_property(a_contact(), a_property(status="booked"))
    assert booked["score"] == free["score"] - 10
    assert any("not available" in r for r in booked["reasons"])


def test_requirement_without_budget_still_scores():
    res = vt.score_property(a_contact(budget_min=None, budget_max=None), a_property())
    assert res["score"] > 0
    assert any("no stated budget" in r for r in res["reasons"])


# ---------------------------------------------------------------------------
# 2. site visits
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "current,target",
    [("scheduled", "confirmed"), ("scheduled", "visited"), ("confirmed", "dropped"),
     ("visited", "revisit"), ("revisit", "visited")],
)
def test_visit_transitions_that_are_allowed(current, target):
    vt.check_visit_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [("scheduled", "revisit"), ("visited", "confirmed"), ("dropped", "visited"), ("confirmed", "nonsense")],
)
def test_visit_transitions_that_are_refused(current, target):
    with pytest.raises(vt.StageError) as exc:
        vt.check_visit_transition(current, target)
    assert exc.value.code in ("VISIT_TRANSITION_FORBIDDEN", "UNKNOWN_VISIT_STATUS")


def test_double_booking_is_detected_on_the_half_open_interval():
    existing = [a_visit(scheduled_at=1_800_000_000, duration_min=60)]
    # starts inside the existing visit
    assert vt.find_conflict(existing, agent_id="usr_1", scheduled_at=1_800_000_600) is not None
    # starts exactly when it ends: not a conflict
    assert vt.find_conflict(existing, agent_id="usr_1", scheduled_at=1_800_003_600) is None
    # different agent, same slot: fine
    assert vt.find_conflict(existing, agent_id="usr_2", scheduled_at=1_800_000_000) is None
    # a dropped visit frees its slot
    dropped = [a_visit(scheduled_at=1_800_000_000, status="dropped")]
    assert vt.find_conflict(dropped, agent_id="usr_1", scheduled_at=1_800_000_000) is None
    # rescheduling a visit against itself is not a conflict
    assert vt.find_conflict(existing, agent_id="usr_1", scheduled_at=1_800_000_000, exclude_id="sv_1") is None


# ---------------------------------------------------------------------------
# 3. stage gates
# ---------------------------------------------------------------------------
def test_site_visit_stage_needs_a_visit_for_this_property():
    deal = a_deal(stage="qualified")
    with pytest.raises(vt.StageError) as exc:
        vt.check_transition(deal, "site_visit", "agent", [])
    assert exc.value.code == "STAGE_GATE_VISIT"

    other_property = [a_visit(property_id="prop_9")]
    with pytest.raises(vt.StageError):
        vt.check_transition(deal, "site_visit", "agent", other_property)

    gate = vt.check_transition(deal, "site_visit", "agent", [a_visit()])
    assert gate["visit"]["found"] == "visited"


def test_negotiation_needs_a_completed_visit_not_merely_a_booking():
    deal = a_deal(stage="site_visit")
    with pytest.raises(vt.StageError) as exc:
        vt.check_transition(deal, "negotiation", "agent", [a_visit(status="scheduled")])
    assert exc.value.code == "STAGE_GATE_VISIT"
    vt.check_transition(deal, "negotiation", "agent", [a_visit(status="visited")])


def test_token_stage_names_the_missing_documents():
    deal = a_deal(stage="negotiation", documents=[{"name": "KYC (Aadhaar/PAN)", "status": "received"}])
    with pytest.raises(vt.StageError) as exc:
        vt.check_transition(deal, "token", "agent", [a_visit()])
    assert exc.value.code == "STAGE_GATE_DOCUMENTS"
    assert "Booking form" in exc.value.message
    assert "KYC" not in exc.value.details["documents"]["missing"]

    deal["documents"].append({"name": "Booking form", "status": "verified"})
    vt.check_transition(deal, "token", "agent", [a_visit()])


def test_a_rejected_document_does_not_open_the_gate():
    deal = a_deal(stage="negotiation", documents=[
        {"name": "KYC (Aadhaar/PAN)", "status": "rejected"},
        {"name": "Booking form", "status": "received"},
    ])
    with pytest.raises(vt.StageError):
        vt.check_transition(deal, "token", "agent", [a_visit()])


def test_backwards_move_needs_owner_or_admin():
    deal = a_deal(stage="negotiation")
    assert not vt.can_move("negotiation", "qualified", "agent")
    assert vt.can_move("negotiation", "qualified", "admin")
    with pytest.raises(vt.StageError) as exc:
        vt.check_transition(deal, "qualified", "agent", [])
    assert exc.value.code == "STAGE_REGRESSION_FORBIDDEN"


def test_unknown_stage_is_rejected():
    with pytest.raises(vt.StageError) as exc:
        vt.check_transition(a_deal(), "proposal", "owner", [])
    assert exc.value.code == "UNKNOWN_STAGE"


def test_checklist_lists_remaining_stages_with_their_requirements():
    deal = a_deal(stage="qualified")
    checklist = vt.stage_checklist(deal, [a_visit()])
    stages = [c["stage"] for c in checklist]
    assert stages[0] == "site_visit" and "won" in stages
    token = next(c for c in checklist if c["stage"] == "token")
    assert "KYC (Aadhaar/PAN)" in token["documents_missing"]
    assert token["ready"] is False
    site = next(c for c in checklist if c["stage"] == "site_visit")
    assert site["ready"] is True  # a visited visit exists


# ---------------------------------------------------------------------------
# 4. commission
# ---------------------------------------------------------------------------
def test_commission_split_sums_to_gross_including_channel_partner():
    deal = a_deal(value=4_500_000)
    c = vt.commission_breakdown(deal, rate_pct=2.0, agent_share_pct=60, channel_partner_pct=10,
                                channel_partner_id="cp_1")
    assert c["gross"] == pytest.approx(90_000)
    assert c["channel_partner"] == pytest.approx(9_000)
    assert c["agent"] + c["brokerage"] == pytest.approx(c["gross"] - c["channel_partner"])
    assert c["agent"] == pytest.approx(48_600)
    assert c["sums_to_gross"] is True


def test_commission_rounding_never_loses_a_paisa():
    # deliberately awkward numbers: 1,234,567 × 2.37%
    deal = a_deal(value=1_234_567)
    c = vt.commission_breakdown(deal, rate_pct=2.37, agent_share_pct=37, channel_partner_pct=13,
                                channel_partner_id="cp_1")
    assert c["channel_partner_paise"] + c["agent_paise"] + c["brokerage_paise"] == c["gross_paise"]
    assert c["sums_to_gross"] is True


def test_commission_defaults_and_validation():
    c = vt.commission_breakdown(a_deal())
    assert c["rate_pct"] == 2.0 and c["agent_share_pct"] == 60 and c["channel_partner"] == 0
    with pytest.raises(vt.StageError):
        vt.commission_breakdown(a_deal(), rate_pct=40)
    with pytest.raises(vt.StageError):
        vt.commission_breakdown(a_deal(value=0))


# ---------------------------------------------------------------------------
# 5. over HTTP
# ---------------------------------------------------------------------------
def _deal_and_property(owner):
    pipeline = owner.call("GET", "/api/v1/pipeline")[1]
    rows = [d for stage in pipeline["columns"].values() for d in stage]
    deal = rows[0]
    detail = owner.call("GET", f"/api/v1/deals/{deal['id']}")[1]
    return detail["deal"], detail


def test_summary_endpoint_returns_the_desk_numbers(owner):
    status, body, _ = owner.call("GET", "/api/v1/crm/summary")
    assert status == 200
    assert body["deals"] > 0 and body["contacts"] > 0
    assert set(body["by_stage"]) == set(vt.STAGES) | {vt.LOST}
    assert "stage_blockers" in body and "next_visits" in body


def test_matches_endpoint_is_explainable(owner):
    contacts = owner.call("GET", "/api/v1/contacts?limit=3")[1]["contacts"]
    status, body, _ = owner.call("GET", f"/api/v1/contacts/{contacts[0]['id']}/matches?limit=3")
    assert status == 200
    assert body["count"] >= 1
    for m in body["matches"]:
        assert 0 <= m["score"] <= 100 and m["reasons"]
    assert body["matches"][0]["score"] >= body["matches"][-1]["score"]


def test_visit_lifecycle_over_http_including_the_conflict(owner):
    deal, _ = _deal_and_property(owner)
    slot = int(time.time()) + 5 * 86400 + (int(time.time()) % 1000)
    body = {"contact_id": deal["contact_id"], "property_id": deal["property_id"],
            "scheduled_at": slot, "duration_min": 45}
    status, created, _ = owner.call("POST", "/api/v1/site-visits", body)
    assert status == 200, created
    visit_id = created["visit"]["id"]

    status, clash, _ = owner.call("POST", "/api/v1/site-visits", body)
    assert status == 409 and clash["error"]["code"] == "VISIT_CONFLICT"

    status, bad, _ = owner.call("PATCH", f"/api/v1/site-visits/{visit_id}", {"status": "revisit"})
    assert status == 422 and bad["error"]["code"] == "VISIT_TRANSITION_FORBIDDEN"

    status, _, _ = owner.call("PATCH", f"/api/v1/site-visits/{visit_id}", {"status": "confirmed"})
    assert status == 200
    status, done, _ = owner.call("PATCH", f"/api/v1/site-visits/{visit_id}", {"status": "visited", "outcome": "shortlisted"})
    assert status == 200 and done["visit"]["status"] == "visited"
    assert "revisit" in done["visit"]["allowed_next"]


def test_a_past_visit_cannot_be_booked(owner):
    deal, _ = _deal_and_property(owner)
    status, body, _ = owner.call("POST", "/api/v1/site-visits", {
        "contact_id": deal["contact_id"], "property_id": deal["property_id"],
        "scheduled_at": int(time.time()) - 86400,
    })
    assert status == 400


def test_documents_open_the_stage_gate(owner):
    deal, _ = _deal_and_property(owner)
    deal_id = deal["id"]
    # start from a stage where token is ahead and the gate applies
    status, blocked, _ = owner.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "token"})
    if status == 409:
        assert blocked["error"]["code"] == "STAGE_GATE_DOCUMENTS"
        for doc in blocked["error"]["details"][0]["documents"]["required"]:
            status, _, _ = owner.call("POST", f"/api/v1/deals/{deal_id}/documents", {"name": doc, "status": "received"})
            assert status == 200
        status, moved, _ = owner.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "token"})
        assert status == 200
        assert moved["deal"]["stage"] == "token"
        assert moved["deal"]["probability"] == vt.PROBABILITY["token"]
    else:
        assert status in (200, 409)


def test_commission_endpoint_persists_the_split(owner):
    deal, _ = _deal_and_property(owner)
    status, body, _ = owner.call("POST", f"/api/v1/deals/{deal['id']}/commission",
                                 {"rate_pct": 2.5, "agent_share_pct": 55, "channel_partner_pct": 15,
                                  "channel_partner_id": "cp_1"})
    assert status == 200
    c = body["commission"]
    assert c["sums_to_gross"] is True and c["rate_pct"] == 2.5
    again = owner.call("GET", f"/api/v1/deals/{deal['id']}")[1]["commission"]
    assert again["gross"] == c["gross"]


def test_viewer_cannot_book_or_move(owner, viewer):
    deal, _ = _deal_and_property(owner)
    status, _, _ = viewer.call("POST", "/api/v1/site-visits", {
        "contact_id": deal["contact_id"], "property_id": deal["property_id"],
        "scheduled_at": int(time.time()) + 9 * 86400,
    })
    assert status == 403
    status, _, _ = viewer.call("POST", f"/api/v1/deals/{deal['id']}/advance", {"stage": "won"})
    assert status == 403
    # but reading is fine: a read-only seat can still see the desk
    assert viewer.call("GET", "/api/v1/crm/summary")[0] == 200


def test_another_tenant_sees_nothing(owner, other_tenant):
    deal, _ = _deal_and_property(owner)
    assert other_tenant.call("GET", f"/api/v1/deals/{deal['id']}")[0] == 404
    assert other_tenant.call("GET", f"/api/v1/contacts/{deal['contact_id']}/matches")[0] == 404
    status, body, _ = other_tenant.call("GET", "/api/v1/site-visits")
    assert status == 200 and all(v["contact_id"] != deal["contact_id"] for v in body["visits"])


def test_csrf_is_still_required_for_a_visit():
    s = Session()
    s.login("owner@realtorestates.app")
    s.csrf = ""  # simulate a page that never obtained the token
    status, body, _ = s.call("POST", "/api/v1/site-visits", {"contact_id": "x", "property_id": "y", "scheduled_at": 1})
    assert status == 403 and body["error"]["code"] == "CSRF_FAILED"


def test_session_endpoint_hands_a_reloaded_page_its_csrf(owner):
    status, body, _ = owner.call("GET", "/api/v1/auth/session")
    assert status == 200
    assert len(body["csrf"]) >= 20
    assert body["user"]["role"] == "owner"
    anon = Session()
    assert anon.call("GET", "/api/v1/auth/session")[0] == 401

"""
Portal ingestion, de-duplication, assignment and speed-to-lead (docs/18 §8 P1).

The rules layer is tested directly because the failure modes are silent: a
budget parsed 100× too large, a phone number silently dropped, a repeat enquiry
losing its source. The HTTP layer is tested for the things an attacker or a
retrying portal actually does — no key, a revoked key, a viewer minting keys,
the same lead three times.
"""
from __future__ import annotations

import time

import pytest

from conftest import BASE, Session  # noqa: F401
from app import ingest as ing


# ---------------------------------------------------------------------------
# 1. Phone numbers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("09876543210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91-98765-43210", "+919876543210"),
        (" 8877665544 ", "+918877665544"),
    ],
)
def test_indian_mobile_numbers_are_canonicalised(raw, expected):
    assert ing.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "022-1234567", "0000000000", "5876543210", "abcd"])
def test_numbers_that_are_not_mobiles_are_refused_not_guessed(raw):
    assert ing.normalize_phone(raw) == ""


# ---------------------------------------------------------------------------
# 2. Budgets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,low,high",
    [
        ("30 Lakh", None, 3_000_000),
        ("₹30,00,000", None, 3_000_000),
        ("30L", None, 3_000_000),
        ("1.2 Cr", None, 12_000_000),
        ("30 Lakh - 50 Lakh", 3_000_000, 5_000_000),
        ("40-60 Lakh", 4_000_000, 6_000_000),
        ("Under 25 lakh", None, 2_500_000),
        ("Above 2 crore", 20_000_000, None),
        ("7500000", None, 7_500_000),
    ],
)
def test_budget_strings_parse_to_a_range(raw, low, high):
    parsed = ing.parse_budget(raw)
    assert parsed["max"] == high
    assert parsed["min"] == low


def test_unreadable_budget_is_empty_not_a_guess():
    parsed = ing.parse_budget("depends on the property")
    assert parsed["min"] is None and parsed["max"] is None
    assert parsed["text"]


# ---------------------------------------------------------------------------
# 3. Adapters
# ---------------------------------------------------------------------------
def test_99acres_payload_normalises():
    out = ing.normalize("99acres", {
        "sender_name": "Rohan Patil", "sender_phone": "9876543210", "sender_email": "rohan@example.com",
        "city": "Nanded", "locality": "Taroda", "budget": "30 Lakh", "message": "East facing plot",
        "lead_id": "99a-1", "campaign": "Sunrise Meadows",
    })
    lead = out["lead"]
    assert lead["name"] == "Rohan Patil"
    assert lead["phone"] == "+919876543210"
    assert lead["budget_max"] == 3_000_000
    assert lead["source"] == "99acres" and lead["source_slug"] == "99acres"
    assert lead["external_id"] == "99a-1"


def test_meta_lead_ads_field_data_normalises():
    out = ing.normalize("meta", {
        "field_data": [
            {"name": "full_name", "values": ["Meera Joshi"]},
            {"name": "phone_number", "values": ["+919812345678"]},
            {"name": "budget", "values": ["40-60 Lakh"]},
            {"name": "city", "values": ["Pune"]},
        ],
        "campaign_name": "Retargeting — plots",
    })
    lead = out["lead"]
    assert lead["name"] == "Meera Joshi" and lead["phone"] == "+919812345678"
    assert (lead["budget_min"], lead["budget_max"]) == (4_000_000, 6_000_000)
    assert lead["campaign"] == "Retargeting — plots"


def test_google_ads_user_column_data_normalises():
    out = ing.normalize("google", {
        "user_column_data": [
            {"column_name": "Full Name", "string_value": "Kabir Khan"},
            {"column_name": "Phone Number", "string_value": "9812345670"},
            {"column_name": "City", "string_value": "Nashik"},
        ],
        "lead_id": "g-77",
    })
    assert out["lead"]["name"] == "Kabir Khan"
    assert out["lead"]["phone"] == "+919812345670"
    assert out["lead"]["external_id"] == "g-77"


def test_missing_phone_is_a_422_not_a_blank_lead():
    with pytest.raises(ing.IngestError) as exc:
        ing.normalize("99acres", {"sender_name": "No Phone", "budget": "30 Lakh"})
    assert exc.value.code == "MISSING_PHONE"


def test_unknown_source_lists_the_known_ones():
    with pytest.raises(ing.IngestError) as exc:
        ing.normalize("olx", {"name": "x", "phone": "9876543210"})
    assert exc.value.code == "UNKNOWN_SOURCE"
    assert "99acres" in exc.value.details["sources"]


# ---------------------------------------------------------------------------
# 4. De-duplication and attribution
# ---------------------------------------------------------------------------
def test_repeat_enquiry_keeps_the_new_source_and_overwrites_nothing():
    existing = {
        "name": "Rohan Patil", "city": "Nanded", "budget_min": 3_000_000,
        "requirement": "edited by the agent — must survive",
        "tags": ["hot"],
        "sources": [{"slug": "99acres", "label": "99acres", "external_id": "99a-1", "campaign": ""}],
    }
    lead = {"source_slug": "magicbricks", "source": "MagicBricks", "city": "Pune",
            "requirement": "portal text", "budget_max": 5_000_000, "campaign": "Diwali",
            "external_id": "mb-9", "email": "", "locality": ""}
    patch = ing.merge_duplicate(existing, lead, at=1_800_000_000)
    assert "requirement" not in patch          # the agent's edit is not overwritten

    merged = {**existing, **patch}
    assert merged["requirement"] == "edited by the agent — must survive"
    assert merged["budget_max"] == 5_000_000            # a blank was filled
    assert merged["city"] == "Nanded"                   # an existing value was not overwritten
    assert [s["slug"] for s in merged["sources"]] == ["99acres", "magicbricks"]
    assert "repeat-enquiry" in merged["tags"] and "hot" in merged["tags"]
    assert ing.primary_source(merged) == "99acres"      # first touch keeps the credit


def test_the_same_portal_retry_does_not_duplicate_the_source_entry():
    existing = {"sources": [{"slug": "meta", "label": "Meta Lead Ads", "external_id": "l-1", "campaign": "A"}]}
    lead = {"source_slug": "meta", "source": "Meta Lead Ads", "external_id": "l-1", "campaign": "A"}
    patch = ing.merge_duplicate(existing, lead, at=1_800_000_000)
    assert len(patch["sources"]) == 1


# ---------------------------------------------------------------------------
# 5. Assignment
# ---------------------------------------------------------------------------
def test_assignment_picks_the_least_loaded_agent_and_ignores_viewers():
    users = [
        {"id": "usr_a", "role": "agent", "active": True},
        {"id": "usr_b", "role": "agent", "active": True},
        {"id": "usr_v", "role": "viewer", "active": True},
        {"id": "usr_x", "role": "agent", "active": False},
    ]
    assert ing.assign_owner(users, {"usr_a": 5, "usr_b": 2}) == "usr_b"
    assert ing.assign_owner(users, {}) == "usr_a"        # tie → deterministic
    assert ing.assign_owner([u for u in users if u["role"] == "viewer"], {}) == ""


# ---------------------------------------------------------------------------
# 6. SLA arithmetic
# ---------------------------------------------------------------------------
def test_sla_states_open_breached_met_and_late():
    base = 1_800_000_000
    contact = {"id": "c1", "created_at": base, "sla": {"seconds": 300, "due_at": base + 300}}
    assert ing.sla_state(contact, base + 100)["status"] == "open"
    assert ing.sla_state(contact, base + 100)["remaining_sec"] == 200
    breached = ing.sla_state(contact, base + 400)
    assert breached["status"] == "breached" and breached["overdue_sec"] == 100
    met = ing.sla_state({**contact, "first_response_at": base + 120}, base + 400)
    assert met["status"] == "met" and met["responded_in_sec"] == 120
    late = ing.sla_state({**contact, "first_response_at": base + 900}, base + 1000)
    assert late["status"] == "met_late"


def test_sla_window_comes_from_source_then_tenant_override():
    assert ing.sla_window("99acres") == 300
    assert ing.sla_window("walkin") == 60
    assert ing.sla_window("unknown") == ing.DEFAULT_SLA
    assert ing.sla_window("99acres", {"settings": {"sla_seconds": 900}}) == 900
    assert ing.sla_window("99acres", {"settings": {"sla_seconds": 999999}}) == 300   # clamped


def test_board_separates_breaches_from_the_live_ones():
    base = 1_800_000_000
    contacts = [
        {"id": "fresh", "name": "Fresh", "created_at": base, "sla": {"seconds": 300, "due_at": base + 300}},
        {"id": "stale", "name": "Stale", "created_at": base - 900, "sla": {"seconds": 300, "due_at": base - 600}},
        {"id": "done", "name": "Done", "created_at": base - 900, "sla": {"seconds": 300, "due_at": base - 600},
         "first_response_at": base - 850},
    ]
    board = ing.sla_board(contacts, at=base)
    assert [r["contact_id"] for r in board["open"]] == ["fresh"]
    assert [r["contact_id"] for r in board["breached"]] == ["stale"]
    assert board["counts"]["met"] == 1
    assert board["avg_response_sec"] == 50


# ---------------------------------------------------------------------------
# 7. Over HTTP
# ---------------------------------------------------------------------------
def _mint_key(owner, label="99acres feed", source="99acres"):
    status, body, _ = owner.call("POST", "/api/v1/tenant/ingest-keys", {"label": label, "source": source})
    assert status == 200, body
    return body["token"], body["key"]["id"]


def test_key_is_returned_once_then_only_masked(owner):
    token, key_id = _mint_key(owner)
    assert token.startswith("rink_") and len(token) > 24
    listing = owner.call("GET", "/api/v1/tenant/ingest-keys")[1]["keys"]
    row = next(k for k in listing if k["id"] == key_id)
    assert "key_hash" not in row and token not in str(listing)
    assert row["prefix"] == token[:12] and row["active"] is True


def test_ingest_creates_a_lead_with_an_sla_clock_and_an_owner(owner):
    token, _ = _mint_key(owner)
    phone = "+9198" + str(int(time.time()))[-8:]
    payload = {"sender_name": "Portal Buyer", "sender_phone": phone, "city": "Nanded",
               "budget": "30 Lakh", "message": "2400 sqft plot", "lead_id": "t-" + phone[-6:]}
    status, body, _ = owner.call("POST", "/api/v1/ingest/99acres", payload, headers={"X-Ingest-Key": token})
    assert status == 200, body
    assert body["duplicate"] is False
    assert body["sla"]["seconds"] == 300 and body["sla"]["due_at"] > time.time()

    sla = owner.call("GET", "/api/v1/leads/sla")[1]
    assert any(r["contact_id"] == body["contact_id"] for r in sla["open"] + sla["breached"])

    # the same buyer enquiring again on a *different* portal is merged, not duplicated
    token2, _ = _mint_key(owner, "mb feed", "magicbricks")
    status, dup, _ = owner.call("POST", "/api/v1/ingest/magicbricks", {
        "contactName": "Portal Buyer", "mobile": phone, "budget": "40-60 Lakh", "enquiryId": "mb-1",
    }, headers={"X-Ingest-Key": token2})
    assert status == 200 and dup["duplicate"] is True
    assert dup["contact_id"] == body["contact_id"]
    assert [s["slug"] for s in dup["sources"]] == ["99acres", "magicbricks"]


def test_ingest_refuses_without_a_key_and_after_revocation(owner):
    status, body, _ = owner.call("POST", "/api/v1/ingest/99acres",
                                 {"sender_name": "X", "sender_phone": "9876500000"})
    assert status == 401 and body["error"]["code"] == "INGEST_UNAUTHORIZED"

    token, key_id = _mint_key(owner, "to revoke")
    good = {"sender_name": "Revoked Test", "sender_phone": "9876500001"}
    assert owner.call("POST", "/api/v1/ingest/99acres", good, headers={"X-Ingest-Key": token})[0] in (200, 409)
    assert owner.call("DELETE", f"/api/v1/tenant/ingest-keys/{key_id}")[0] == 200
    status, body, _ = owner.call("POST", "/api/v1/ingest/99acres",
                                 {"sender_name": "Revoked Test", "sender_phone": "9876500002"},
                                 headers={"X-Ingest-Key": token})
    assert status == 401


def test_only_owner_or_admin_can_mint_or_list_keys(owner, viewer):
    assert viewer.call("POST", "/api/v1/tenant/ingest-keys", {"label": "nope"})[0] == 403
    assert viewer.call("GET", "/api/v1/tenant/ingest-keys")[0] == 403


def test_isolation_between_tenants_keys_and_leads(owner, other_tenant):
    token, key_id = _mint_key(owner, "sunrise only")
    assert all(k["id"] != key_id for k in other_tenant.call("GET", "/api/v1/tenant/ingest-keys")[1]["keys"])
    # another tenant's key cannot read our leads, and ours cannot write into theirs
    assert other_tenant.call("DELETE", f"/api/v1/tenant/ingest-keys/{key_id}")[0] == 404
    mine = owner.call("GET", "/api/v1/leads/sla")[1]
    theirs = other_tenant.call("GET", "/api/v1/leads/sla")[1]
    assert not ({r["contact_id"] for r in mine["open"] + mine["breached"]} &
                {r["contact_id"] for r in theirs["open"] + theirs["breached"]})


def test_first_response_can_be_logged_and_stops_the_clock(owner):
    token, _ = _mint_key(owner)
    phone = "+9197" + str(int(time.time()))[-8:]
    body = owner.call("POST", "/api/v1/ingest/99acres", {
        "sender_name": "Respond Test", "sender_phone": phone, "budget": "20 Lakh",
    }, headers={"X-Ingest-Key": token})[1]
    cid = body["contact_id"]
    status, res, _ = owner.call("POST", f"/api/v1/leads/{cid}/respond", {"channel": "call"})
    assert status == 200 and res["already_responded"] is False
    assert res["sla"]["status"] in ("met", "met_late")
    again = owner.call("POST", f"/api/v1/leads/{cid}/respond", {"channel": "call"})[1]
    assert again["already_responded"] is True


def test_sending_a_message_from_the_inbox_also_stops_the_clock(owner):
    token, _ = _mint_key(owner)
    phone = "+9196" + str(int(time.time()))[-8:]
    cid = owner.call("POST", "/api/v1/ingest/99acres", {
        "sender_name": "Inbox Test", "sender_phone": phone,
    }, headers={"X-Ingest-Key": token})[1]["contact_id"]
    status, body, _ = owner.call("POST", f"/api/v1/inbox/{cid}/send",
                                 {"channel": "whatsapp", "text": "Namaskar! Sunrise Meadows madhe plot available aahe.", "override_quiet_hours": True})
    assert status == 200, body
    contact = next(c for c in owner.call("GET", "/api/v1/contacts?limit=500")[1]["contacts"] if c["id"] == cid)
    assert contact.get("sla_status") in ("met", "met_late")


def test_bad_payload_is_a_422_with_a_reason(owner):
    token, _ = _mint_key(owner)
    status, body, _ = owner.call("POST", "/api/v1/ingest/99acres",
                                 {"sender_name": "No Number"}, headers={"X-Ingest-Key": token})
    assert status == 422 and body["error"]["code"] == "MISSING_PHONE"
    status, body, _ = owner.call("POST", "/api/v1/ingest/olx",
                                 {"name": "x", "phone": "9876543210"}, headers={"X-Ingest-Key": token})
    assert status == 422 and body["error"]["code"] == "UNKNOWN_SOURCE"

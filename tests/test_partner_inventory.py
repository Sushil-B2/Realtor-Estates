"""
The partner's view of the stock (gap G4).

A partner could register a buyer and watch a protection clock, but could not see
what the brokerage had to sell — so the claim was a formality. Opening the
catalogue is where a CP portal leaks, and these tests are about the line:

* only stock that can actually be sold is listed;
* the seller, the seller's margin and *other buyers* never appear;
* a commission rate is never invented — it is their rate, applied to a gross rate
  the ledger can support, and labelled with the basis.

The pure tests pin the rules; the HTTP tests pin that the route is partner-only,
scoped to one tenant, and that the payload carries nothing it should not.
"""
from __future__ import annotations

import pytest

from conftest import Session
from app import partners as cpm


@pytest.fixture()
def partner():
    s = Session()
    s.login("partner@nandedproperty.in", "Partner@12345!")
    return s


# --------------------------------------------------------------- pure rules
def _unit(**kw):
    return {"id": "unt_x", "unit_no": "1A", "status": "available", **kw}


def _prop(**kw):
    return {"id": "prp_x", "title": "Test Residency", "city": "Nanded", "locality": "Taroda",
            "type": "apartment", "category": "residential", "price": 5000000, "status": "available",
            "rera": "P1-2024-0001", "docs": ["Title search report"], "attributes": {}, **kw}


def test_availability_counts_states_and_nothing_else():
    units = [_unit(), _unit(status="blocked"), _unit(status="booked"), _unit(status="sold")]
    avail = cpm.availability(units)
    assert avail == {"total": 4, "available": 1, "blocked": 1, "booked": 1, "sold": 1}


def test_display_status_says_what_a_buyer_would_ask():
    assert cpm.display_status(_prop(), {"total": 3, "available": 3}) == "available"
    assert cpm.display_status(_prop(), {"total": 3, "available": 1}) == "limited"
    assert cpm.display_status(_prop(), {"total": 3, "available": 0}) == "sold_out"
    # a property with no unit list is sold whole, and its own status decides
    assert cpm.display_status(_prop(), {"total": 0, "available": 0}) == "available"
    assert cpm.display_status(_prop(status="sold"), {"total": 0, "available": 0}) == "off_market"


def test_only_sellable_stock_is_listed_unless_asked():
    dead = (_prop(id="p1", status="sold"), {"total": 0, "available": 0})
    assert cpm.is_listable(*dead) is False
    assert cpm.is_listable(*dead, include_unavailable=True) is True
    assert cpm.is_listable(_prop(), {"total": 2, "available": 1}) is True


def test_a_property_with_no_rera_is_not_advertisable():
    """RERA s.3 puts the penalty on whoever published — often the partner."""
    assert cpm.marketing_ready(_prop())["ready"] is True
    bare = cpm.marketing_ready(_prop(rera=""))
    assert bare["ready"] is False and "RERA registration number" in bare["missing"]
    assert "price" in cpm.marketing_ready(_prop(rera="", price=0, pricing={}))["missing"]


def test_houselike_attributes_are_dropped_from_marketing_copy():
    prop = _prop(attributes={"beds": 3, "ownership": "Freehold", "seller_phone": "9000000000",
                             "internal_notes": "motivated seller", "commission": "3%"})
    public = cpm.public_attributes(prop)
    assert public == {"beds": 3, "ownership": "Freehold"}


def test_no_comparable_deal_means_no_rate_is_invented():
    assert cpm.implied_gross_rate([]) == (None, "no comparable recorded deal")
    assert cpm.implied_gross_rate([{"value": 5000000}])[0] is None
    rate, basis = cpm.implied_gross_rate([
        {"value": 5000000, "commission": {"gross": 100000}},
        {"value": 10000000, "commission": {"gross": 200000}},
    ])
    assert rate == 2.0 and "median of 2" in basis


def test_the_estimate_is_their_rate_of_the_gross_rate():
    row = cpm.inventory_row(_prop(), [_unit()], cp_pct=2.5, gross_rate=2.0,
                            rate_basis="median of 2 recorded deal(s)")
    # 2.5% of a 2% commission on ₹50,00,000 = ₹2,500
    assert row["commission"]["estimate"] == 2500.0
    assert "your 2.5%" in row["commission"]["basis"]
    # no rate → no number, and the basis says so
    blank = cpm.inventory_row(_prop(), [_unit()], cp_pct=2.5, gross_rate=None, rate_basis="no comparable")
    assert blank["commission"]["estimate"] is None
    assert "confirms it when you register" in blank["commission"]["basis"]


def test_filters_are_exact_and_an_unknown_city_matches_nothing():
    rows = [
        {"title": "A", "city": "Nanded", "type": "apartment", "price": 100, "deal_kind": "sale"},
        {"title": "B", "city": "Pune", "type": "shop", "price": 900, "deal_kind": "rent"},
    ]
    assert len(cpm.apply_filters(rows, city="nanded")) == 1
    assert len(cpm.apply_filters(rows, ptype="shop")) == 1
    assert len(cpm.apply_filters(rows, min_price=200)) == 1
    assert len(cpm.apply_filters(rows, max_price=500)) == 1
    assert cpm.apply_filters(rows, q="pun")[0]["title"] == "B"
    assert cpm.apply_filters(rows, city="Delhi") == []


# ------------------------------------------------------------------- routes
def test_a_partner_sees_the_stock(partner):
    status, body, _ = partner.call("GET", "/api/v1/cp/inventory")
    assert status == 200, body
    assert body["count"] > 0
    assert body["inventory"], "a partner with no stock cannot sell"
    row = body["inventory"][0]
    for key in ("id", "title", "city", "price", "availability", "commission", "marketing"):
        assert key in row
    assert body["commission_basis"]["your_share_pct"] > 0
    assert body["withheld"], "we must say what is withheld, not just withhold it"


def test_the_seller_and_the_other_buyers_are_not_in_the_payload(partner):
    body = partner.call("GET", "/api/v1/cp/inventory")[1]
    blob = str(body).lower()
    for forbidden in ("phone_enc", "seller_phone", "seller_name", "floor_price", "token_amount",
                      "contact_id", "buyer_phone"):
        assert forbidden not in blob, f"{forbidden} leaked into the catalogue"
    row = body["inventory"][0]
    assert "booking" not in row and "history" not in row


def test_a_booked_unit_is_not_sellable_on_the_detail_page(owner, partner):
    """The unit exists and is visible — but only one of it is for sale."""
    listed = partner.call("GET", "/api/v1/cp/inventory?include_unavailable=1")[1]
    pid = None
    for row in listed["inventory"]:
        detail = partner.call("GET", f"/api/v1/cp/inventory/{row['id']}")[1]
        units = detail["property"]["units"]
        if any(u["status"] in ("booked", "blocked", "sold") for u in units):
            pid = row["id"]
            break
    assert pid, "the demo tenant has a booked unit and it should still be listed"
    units = partner.call("GET", f"/api/v1/cp/inventory/{pid}")[1]["property"]["units"]
    for u in units:
        assert "booking" not in u and "history" not in u
        assert u["sellable"] == (u["status"] == "available")
    assert any(u["status"] != "available" for u in units)


def test_whole_property_listings_are_one_unit_not_zero(partner):
    detail = None
    for row in partner.call("GET", "/api/v1/cp/inventory")[1]["inventory"]:
        d = partner.call("GET", f"/api/v1/cp/inventory/{row['id']}")[1]["property"]
        if not d["units"]:
            detail = d
            break
    assert detail, "the demo has a property with no unit list"
    assert detail["sellable_as"] == "whole property"
    assert detail["sellable_units"] == 1


def test_filters_and_facets_agree(partner):
    body = partner.call("GET", "/api/v1/cp/inventory")[1]
    city = body["facets"]["cities"][0]["value"]
    filtered = partner.call("GET", f"/api/v1/cp/inventory?city={city}")[1]
    assert filtered["count"] >= 1
    assert all(r["city"] == city for r in filtered["inventory"])
    assert partner.call("GET", "/api/v1/cp/inventory?city=Atlantis")[1]["count"] == 0


def test_the_portal_belongs_to_partners_only(owner):
    for who in (owner,):
        status, body, _ = who.call("GET", "/api/v1/cp/inventory")
        assert status == 403 and body["error"]["code"] == "FORBIDDEN_SCOPE"
    agent = Session()
    agent.login("agent@realtorestates.app")
    status, body, _ = agent.call("GET", "/api/v1/cp/inventory")
    assert status == 403 and body["error"]["code"] == "FORBIDDEN_SCOPE"


def test_another_tenants_property_is_not_found(partner, other_tenant):
    """Tenant scoping is the store\'s, not the filter\'s: a property we do not own is a 404.

    The probe uses a partner *of the other tenant* asking for our property id, so the
    test adds no stock to a shared demo (a created property broke an inventory-count
    assertion elsewhere — a test that pollutes the fixture it shares is a bug).
    """
    ours = partner.call("GET", "/api/v1/cp/inventory")[1]["inventory"]
    assert ours, "we need at least one property of our own for this to mean anything"
    made = other_tenant.call("POST", "/api/v1/cp/partners", {
        "email": "inventory-probe@metrorealty.in", "name": "Probe Partner",
        "firm": "Metro Probe", "commission_pct": 2.0,
    })
    if made[0] == 409:  # a previous test in the session already made it
        pytest.skip("probe partner already created this session")
    assert made[0] in (200, 201), made[1]
    theirs = Session()
    theirs.call("POST", "/api/v1/auth/login", {
        "email": "inventory-probe@metrorealty.in", "password": made[1]["temp_password"]}, csrf=False)
    status, body, _ = theirs.call("GET", f"/api/v1/cp/inventory/{ours[0]['id']}")
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"


def test_an_unknown_property_is_a_404_not_an_empty_page(partner):
    status, body, _ = partner.call("GET", "/api/v1/cp/inventory/prp_doesnotexist")
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"

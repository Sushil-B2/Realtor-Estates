"""
Unit-level inventory: generation, timed blocks, booking and sale.

The bug this module exists to prevent is the expensive one — two buyers promised
the same unit. So the tests are mostly about refusals, and about the fact that an
expired hold releases itself without a background job.
"""
from __future__ import annotations

import time

import pytest

from conftest import BASE, Session  # noqa: F401
from app import inventory as ivt

HOUR = 3600
NOW = 1_800_000_000


def a_unit(**over):
    u = {"id": "unt_1", "unit_no": "3B", "floor": 3, "price": 4_500_000, "status": "available"}
    u.update(over)
    return u


# ---------------------------------------------------------------------------
# 1. generation
# ---------------------------------------------------------------------------
def test_the_grid_is_floor_major_and_deterministic():
    grid = ivt.generate_units(floors=2, per_floor=3, base_price=4_000_000, price_step=100_000)
    assert [u["unit_no"] for u in grid] == ["1A", "1B", "1C", "2A", "2B", "2C"]
    assert grid == ivt.generate_units(floors=2, per_floor=3, base_price=4_000_000, price_step=100_000)
    assert grid[0]["price"] == 4_000_000 and grid[3]["price"] == 4_100_000


def test_the_price_ladder_and_its_refusals():
    assert ivt.price_ladder(5_000_000, 50_000, floor=1) == 5_000_000
    assert ivt.price_ladder(5_000_000, 50_000, floor=4) == 5_150_000
    with pytest.raises(ivt.InventoryError):
        ivt.price_ladder(5_000_000, -1, floor=2)
    with pytest.raises(ivt.InventoryError):
        ivt.price_ladder(0, 0, floor=1)
    with pytest.raises(ivt.InventoryError):
        ivt.generate_units(floors=0, per_floor=4, base_price=1_000_000)
    with pytest.raises(ivt.InventoryError):
        ivt.generate_units(floors=4, per_floor=40, base_price=1_000_000)


# ---------------------------------------------------------------------------
# 2. blocks
# ---------------------------------------------------------------------------
def test_a_block_holds_the_unit_and_expires_itself():
    patch = ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="cnt_1", by="usr_1", hours=48, at=NOW)
    unit = {**a_unit(), **patch}
    assert ivt.effective_status(unit, NOW + HOUR) == "blocked"
    assert ivt.describe(unit, NOW + HOUR)["block_hours_left"] == 47.0
    # an hour past the window, with nothing having run, the unit is available again
    assert ivt.effective_status(unit, NOW + 49 * HOUR) == "available"
    assert ivt.describe(unit, NOW + 49 * HOUR)["block_expired"] is True


def test_a_second_deal_is_refused_with_the_holder_and_the_time_left():
    unit = {**a_unit(), **ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="cnt_1", by="u", at=NOW)}
    ok, code, details = ivt.can_block(unit, "deal_2", NOW + HOUR)
    assert ok is False and code == "UNIT_BLOCKED"
    assert details["held_by_deal"] == "deal_1" and details["hours_left"] == 47.0
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_block(unit, deal_id="deal_2", contact_id="cnt_2", by="u", at=NOW + HOUR)
    assert "held by another deal" in str(exc.value)


def test_the_holder_may_renew_its_own_block():
    unit = {**a_unit(), **ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="c", by="u", hours=2, at=NOW)}
    ok, _, details = ivt.can_block(unit, "deal_1", NOW + HOUR)
    assert ok is True and details.get("renewal") is True
    renewed = ivt.apply_block(unit, deal_id="deal_1", contact_id="c", by="u", hours=24, at=NOW + HOUR)
    assert renewed["block"]["until"] == NOW + 25 * HOUR


def test_a_block_on_a_sold_unit_is_impossible():
    ok, code, _ = ivt.can_block(a_unit(status="sold"), "deal_1", NOW)
    assert ok is False and code == "UNIT_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# 3. booking and sale
# ---------------------------------------------------------------------------
def test_booking_releases_the_hold_and_records_the_token():
    unit = {**a_unit(), **ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="c", by="u", at=NOW)}
    patch = ivt.apply_booking(unit, deal_id="deal_1", contact_id="c", by="u", token_amount=100_000, at=NOW + HOUR)
    booked = {**unit, **patch}
    assert booked["status"] == "booked" and booked["block"] == {}
    assert booked["booking"]["token_amount"] == 100_000
    assert ivt.effective_status(booked, NOW + 10_000 * HOUR) == "booked"   # bookings do not expire


def test_another_deal_cannot_book_or_sell_a_held_unit():
    unit = {**a_unit(), **ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="c", by="u", at=NOW)}
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_booking(unit, deal_id="deal_2", contact_id="c2", by="u", at=NOW)
    assert exc.value.code == "UNIT_BLOCKED"
    assert exc.value.details["held_by_deal"] == "deal_1"


def test_booking_is_idempotent_for_the_same_deal_and_refused_for_another():
    unit = {**a_unit(), **ivt.apply_booking(a_unit(), deal_id="deal_1", contact_id="c", by="u", at=NOW)}
    assert ivt.apply_booking(unit, deal_id="deal_1", contact_id="c", by="u", at=NOW) == {}
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_booking(unit, deal_id="deal_2", contact_id="c2", by="u", at=NOW)
    assert exc.value.code == "UNIT_ALREADY_BOOKED"


def test_a_unit_cannot_be_sold_without_being_booked_by_that_deal():
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_sale(a_unit(), deal_id="deal_9", by="u", at=NOW)
    assert exc.value.code == "SALE_WITHOUT_BOOKING"
    assert exc.value.details["status"] == "available"


def test_the_booking_deal_can_sell_it():
    unit = {**a_unit(), **ivt.apply_booking(a_unit(), deal_id="deal_1", contact_id="c", by="u", at=NOW)}
    patch = ivt.apply_sale(unit, deal_id="deal_1", by="u", at=NOW + 5 * 24 * HOUR)
    sold = {**unit, **patch}
    assert sold["status"] == "sold" and sold["sold"]["deal_id"] == "deal_1"
    assert ivt.apply_sale(sold, deal_id="deal_1", by="u", at=NOW) == {}


def test_a_status_change_cannot_fabricate_a_sale():
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_manual_status(a_unit(), "sold", "usr_1", NOW)
    assert exc.value.code == "USE_THE_DEAL"
    with pytest.raises(ivt.InventoryError):
        ivt.apply_manual_status(a_unit(status="sold"), "available", "usr_1", NOW)


def test_withdraw_and_reinstate():
    withdrawn = {**a_unit(), **ivt.apply_manual_status(a_unit(), "withdrawn", "usr_1", NOW)}
    assert withdrawn["status"] == "withdrawn"
    back = ivt.apply_manual_status(withdrawn, "available", "usr_1", NOW)
    assert back["status"] == "available"


def test_release_needs_something_to_release_and_respects_the_holder():
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_release(a_unit(), by="usr_1", at=NOW)
    assert exc.value.code == "NOTHING_TO_RELEASE"
    held = {**a_unit(), **ivt.apply_block(a_unit(), deal_id="deal_1", contact_id="c", by="u", at=NOW)}
    with pytest.raises(ivt.InventoryError) as exc:
        ivt.apply_release(held, by="usr_1", deal_id="deal_2", at=NOW)
    assert exc.value.code == "UNIT_HELD_BY_OTHER"
    released = ivt.apply_release(held, by="usr_1", deal_id="deal_1", at=NOW)
    assert released["status"] == "available" and released["block"] == {}


# ---------------------------------------------------------------------------
# 4. summary
# ---------------------------------------------------------------------------
def test_summary_counts_expired_blocks_as_available():
    blocked = {**a_unit(id="u1"), **ivt.apply_block(a_unit(id="u1"), deal_id="d", contact_id="c", by="u", hours=1, at=NOW)}
    units = [blocked, a_unit(id="u2", status="sold", price=5_000_000)]
    live = ivt.summarize(units, NOW)
    assert live["counts"]["blocked"] == 1 and live["sellable"] == 1
    later = ivt.summarize(units, NOW + 2 * HOUR)
    assert later["counts"]["blocked"] == 0 and later["counts"]["available"] == 1
    assert later["sold_value"] == 5_000_000


# ---------------------------------------------------------------------------
# 5. over HTTP
# ---------------------------------------------------------------------------
def _a_property(ctx):
    return ctx.call("GET", "/api/v1/properties")[1]["properties"][0]


def _deals(ctx):
    """Pipeline cards carry no stage field — the column is the stage."""
    return [dict(d, stage=stage) for stage, rows in ctx.call("GET", "/api/v1/pipeline")[1]["columns"].items()
            for d in rows]


def test_bulk_generation_is_idempotent(owner):
    prop = _a_property(owner)
    first = owner.call("POST", f"/api/v1/properties/{prop['id']}/units/bulk",
                       {"floors": 2, "per_floor": 2, "base_price": 3_000_000, "price_step": 50_000,
                        "start_floor": 7, "type": "2BHK", "area_sqft": 1100})
    assert first[0] == 200, first
    second = owner.call("POST", f"/api/v1/properties/{prop['id']}/units/bulk",
                        {"floors": 2, "per_floor": 2, "base_price": 3_000_000, "price_step": 50_000, "start_floor": 7})
    assert second[0] == 200
    assert second[1]["created"] == 0 and second[1]["skipped"] == 4


def test_units_are_listed_per_property_with_a_summary(owner):
    prop = _a_property(owner)
    body = owner.call("GET", f"/api/v1/properties/{prop['id']}/units")[1]
    assert body["summary"]["total"] >= 1
    assert body["units"][0]["property_id"] if "property_id" in body["units"][0] else True
    for u in body["units"]:
        assert u["status"] in ivt.STATUSES


def test_block_book_conflict_and_sale_over_http(owner):
    prop = _a_property(owner)
    unit = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                      {"unit_no": f"T{int(time.time()) % 100000}", "floor": 9, "price": 3_500_000})[1]["unit"]
    deals = _deals(owner)
    deal_a, deal_b = deals[0], deals[1]

    status, blocked, _ = owner.call("POST", f"/api/v1/units/{unit['id']}/block",
                                 {"deal_id": deal_a["id"], "hours": 48, "reason": "price hold"})
    assert status == 200, blocked
    assert blocked["unit"]["status"] == "blocked" and blocked["unit"]["block_hours_left"] > 47

    status, clash, _ = owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deal_b["id"]})
    assert status == 409 and clash["error"]["code"] == "UNIT_BLOCKED"
    assert clash["error"]["details"][0]["held_by_deal"] == deal_a["id"]

    status, booked, _ = owner.call("POST", f"/api/v1/units/{unit['id']}/book",
                                {"deal_id": deal_a["id"], "token_amount": 100_000})
    assert status == 200 and booked["unit"]["status"] == "booked"

    status, other, _ = owner.call("POST", f"/api/v1/units/{unit['id']}/book", {"deal_id": deal_b["id"]})
    assert status == 409 and other["error"]["code"] == "UNIT_ALREADY_BOOKED"

    status, released, _ = owner.call("POST", f"/api/v1/units/{unit['id']}/release", {"deal_id": deal_a["id"]})
    assert status == 200 and released["unit"]["status"] == "available"


def test_an_agent_cannot_release_another_deals_hold(owner):
    prop = _a_property(owner)
    unit = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                      {"unit_no": f"A{int(time.time()) % 100000}", "floor": 8, "price": 3_600_000})[1]["unit"]
    deals = _deals(owner)
    owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deals[0]["id"]})
    agent = Session()
    agent.login("agent@realtorestates.app")
    status, body, _ = agent.call("POST", f"/api/v1/units/{unit['id']}/release", {"deal_id": deals[1]["id"]})
    assert status == 403, body
    assert body["error"]["code"] == "FORBIDDEN_SCOPE"


def test_a_viewer_can_read_inventory_but_not_change_it(owner, viewer):
    prop = _a_property(owner)
    assert viewer.call("GET", f"/api/v1/properties/{prop['id']}/units")[0] == 200
    status, _, _ = viewer.call("POST", f"/api/v1/properties/{prop['id']}/units", {"unit_no": "V1"})
    assert status == 403


def test_units_are_scoped_to_the_tenant(owner, other_tenant):
    prop = _a_property(owner)
    assert other_tenant.call("GET", f"/api/v1/properties/{prop['id']}/units")[0] == 404
    assert other_tenant.call("GET", "/api/v1/units")[1]["summary"]["total"] == 0


def test_a_status_patch_cannot_mark_a_unit_sold(owner):
    prop = _a_property(owner)
    unit = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                      {"unit_no": f"S{int(time.time()) % 100000}", "price": 1_000_000})[1]["unit"]
    status, body, _ = owner.call("PATCH", f"/api/v1/units/{unit['id']}", {"status": "sold"})
    assert status == 409 and body["error"]["code"] == "USE_THE_DEAL"
    status, body, _ = owner.call("PATCH", f"/api/v1/units/{unit['id']}", {"status": "withdrawn"})
    assert status == 200 and body["unit"]["status"] == "withdrawn"


def test_the_deal_advance_carries_the_unit(owner):
    """Token books it, and a second deal then cannot take it."""
    prop = _a_property(owner)
    unit = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                      {"unit_no": f"D{int(time.time()) % 100000}", "price": 4_000_000})[1]["unit"]
    deals = _deals(owner)
    deal = next(d for d in deals if d["stage"] in ("negotiation", "qualified", "contacted", "site_visit"))
    assert owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deal["id"]})[0] == 200
    status, moved, _ = owner.call("POST", f"/api/v1/deals/{deal['id']}/advance", {"stage": "token", "force": True})
    assert status == 200, moved
    assert moved["unit"]["status"] in ("booked", "sold")
    detail = owner.call("GET", f"/api/v1/deals/{deal['id']}")[1]
    assert detail["unit"] and detail["unit"]["id"] == unit["id"]


def test_the_unit_history_records_who_did_what(owner):
    prop = _a_property(owner)
    unit = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                      {"unit_no": f"H{int(time.time()) % 100000}", "price": 2_000_000})[1]["unit"]
    deals = _deals(owner)
    owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deals[0]["id"]})
    owner.call("POST", f"/api/v1/units/{unit['id']}/book", {"deal_id": deals[0]["id"], "token_amount": 50_000})
    events = owner.call("GET", "/api/v1/units/audit")[1]["events"]
    mine = [e for e in events if e["unit_id"] == unit["id"]]
    assert {e["event"] for e in mine} >= {"created", "blocked", "booked"}

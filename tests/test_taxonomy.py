"""
The property taxonomy: five categories, five forms, one vocabulary.

Two failure modes are being defended against. The first is a form so generic it
accepts a warehouse with no clear height. The second is a unit bug that reads
"2 acre" as "2 sqft", or silently converts a bigha that means something else 60
km away. So the pure-function tests come first, then the HTTP surface, then the
matching that decides what an agent actually phones about.
"""
from __future__ import annotations

import pytest

from conftest import BASE, Session  # noqa: F401
from app import taxonomy as tax


# ---------------------------------------------------------------------------
# 1. categories are genuinely different
# ---------------------------------------------------------------------------
def test_five_categories_each_with_its_own_field_set():
    cats = {c["slug"]: c for c in tax.categories()}
    assert set(cats) == {"residential", "commercial", "industrial", "land", "hospitality"}
    names = {slug: {f["name"] for f in c["fields"]} for slug, c in cats.items()}
    # the fields that make each category itself
    assert {"bedrooms", "bathrooms", "furnishing", "facing"} <= names["residential"]
    assert {"seats", "cabins", "fitout", "lock_in_months", "cam_per_sqft_month"} <= names["commercial"]
    assert {"clear_height_m", "floor_load_mt_sqm", "dock_levellers", "flooring"} <= names["industrial"]
    assert {"survey_number", "occupant_class", "encumbrance", "na_order", "irrigation"} <= names["land"]
    assert {"keys", "star_class", "banquet_seating", "fnb_licence"} <= names["hospitality"]
    # and they do not leak into each other
    assert "clear_height_m" not in names["residential"]
    assert "bedrooms" not in names["commercial"]
    assert "seats" not in names["land"]
    # no two categories share an identical form
    forms = [frozenset(n) for n in names.values()]
    assert len(set(forms)) == len(forms)


def test_types_are_scoped_to_their_category():
    cats = {c["slug"]: c for c in tax.categories()}
    res = {t["slug"] for t in cats["residential"]["types"]}
    ind = {t["slug"] for t in cats["industrial"]["types"]}
    assert {"apartment", "villa", "penthouse", "studio"} <= res
    assert {"warehouse", "factory", "cold_storage"} <= ind
    assert not res & ind
    with pytest.raises(tax.TaxonomyError):
        tax.validate("residential", "warehouse", {})
    with pytest.raises(tax.TaxonomyError):
        tax.validate("nonsense", "apartment", {})


def test_deal_kinds_differ_by_category():
    cats = {c["slug"]: c for c in tax.categories()}
    assert cats["residential"]["deal_kinds"] == ["sale", "rent"]
    assert cats["commercial"]["deal_kinds"] == ["sale", "lease"]
    assert cats["land"]["deal_kinds"] == ["sale"]
    with pytest.raises(tax.TaxonomyError) as e:
        tax.normalize_property({"category": "land", "type": "agricultural_land", "deal_kind": "rent",
                                "area": {"value": 2, "unit": "acre"}, "pricing": {"amount": 5000000},
                                "attributes": {"land_use": "Agricultural"}})
    assert e.value.code == "DEAL_KIND_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# 2. units and money
# ---------------------------------------------------------------------------
def test_area_units_normalise_to_sqft():
    assert tax.normalize_area(1, "sqft") == 1.0
    assert tax.normalize_area(1, "sqm") == pytest.approx(10.76, abs=0.01)
    assert tax.normalize_area(1, "sqyd") == 9.0
    assert tax.normalize_area(1, "acre") == 43560.0
    assert tax.normalize_area(1, "hectare") == pytest.approx(107639, abs=1)
    assert tax.normalize_area(1, "are") == pytest.approx(1076.39, abs=0.01)
    assert tax.normalize_area(1, "guntha") == 1089.0
    assert tax.normalize_area(1, "cent") == 435.6
    # 1 acre = 40 guntha, the check a broker would do on the back of an envelope
    assert tax.normalize_area(40, "guntha") == pytest.approx(43560)


def test_bigha_is_refused_with_a_reason():
    with pytest.raises(tax.TaxonomyError) as e:
        tax.normalize_area(2, "bigha")
    assert e.value.code == "AREA_UNIT_REFUSED"
    assert "district" in e.value.message
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_area(2, "gunta")          # a typo must not become a hectare
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_area(0, "sqft")
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_area("lots", "sqft")


def test_price_bases_all_reduce_to_a_comparable_number():
    lease = tax.normalize_property({
        "category": "commercial", "type": "office", "deal_kind": "lease",
        "area": {"value": 5000, "unit": "sqft"},
        "pricing": {"basis": "per_month_per_sqft", "amount": 65, "lock_in_months": 36},
        "attributes": {"fitout": "Fully Fitted"},
    })
    m = tax.price_metrics(lease)
    assert m["monthly"] == 325_000 and m["annual"] == 3_900_000 and m["total"] is None
    assert m["per_sqft"] == 65

    sale = tax.normalize_property({
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "area": {"value": 2, "unit": "acre"}, "pricing": {"amount": 87_120_000},
        "attributes": {"bedrooms": 3, "bathrooms": 2},
    })
    assert tax.price_metrics(sale)["per_sqft"] == 1000

    per_acre = tax.normalize_property({
        "category": "land", "type": "agricultural_land", "deal_kind": "sale",
        "area": {"value": 5.5, "unit": "acre"}, "pricing": {"basis": "per_acre", "amount": 3_200_000},
        "attributes": {"land_use": "Agricultural"},
    })
    assert tax.price_metrics(per_acre)["total"] == pytest.approx(17_600_000)

    seats = tax.normalize_property({
        "category": "commercial", "type": "co_working", "deal_kind": "lease",
        "area": {"value": 3800, "unit": "sqft"},
        "pricing": {"basis": "per_seat_month", "amount": 7500},
        "attributes": {"fitout": "Plug and Play", "seats": 40},
    })
    assert tax.price_metrics(seats)["monthly"] == 300_000


def test_rent_is_compared_monthly_and_sale_in_totals():
    prop = {"deal_kind": "rent", "area": {"sqft": 985},
            "pricing": {"basis": "per_month", "amount": 18000}}
    assert tax.comparable(prop, "rent") == (18000.0, "per month")
    # a tenant with a ₹20,000/month budget must not be shown "over budget 99,900%"
    spec = tax.normalize_requirement({"category": "residential", "deal_kind": "rent", "budget_max": 20000})
    scored = tax.score_requirement({**prop, "category": "residential", "type": "apartment",
                                    "attributes": {}, "status": "available"}, spec)
    assert any("within budget" in r for r in scored["reasons"]), scored["reasons"]


def test_a_rate_typed_into_the_total_field_is_caught():
    with pytest.raises(tax.TaxonomyError) as e:
        tax.normalize_property({
            "category": "industrial", "type": "warehouse", "deal_kind": "lease",
            "area": {"value": 20000, "unit": "sqft"},
            "pricing": {"basis": "per_month_per_sqft", "amount": 4_500_000},
            "attributes": {"clear_height_m": 12, "floor_load_mt_sqm": 6},
        })
    assert "looks like a total" in e.value.message


# ---------------------------------------------------------------------------
# 3. validation is per category
# ---------------------------------------------------------------------------
def test_each_category_refuses_its_own_missing_fields():
    cases = [
        ("residential", "apartment", {"bedrooms": 2}, "Bathrooms"),
        ("commercial", "office", {}, "Fit-out"),
        ("industrial", "warehouse", {"clear_height_m": 12}, "Floor load"),
        ("land", "agricultural_land", {"survey_number": "Gat 1"}, "Land use"),
    ]
    for category, type_, attrs, expect in cases:
        _, errors = tax.validate(category, type_, attrs)
        assert any(expect.lower() in e.lower() for e in errors), (category, errors)


def test_fields_from_another_category_are_rejected_not_ignored():
    _, errors = tax.validate("industrial", "warehouse", {
        "clear_height_m": 12, "floor_load_mt_sqm": 6, "bedrooms": 3, "facing": "East"})
    assert any("Unknown field" in e for e in errors)
    assert "bedrooms" in " ".join(errors) and "facing" in " ".join(errors)


def test_enum_and_number_fields_are_coerced_and_bounded():
    clean, errors = tax.validate("residential", "apartment", {
        "bedrooms": "3", "bathrooms": 2, "facing": "north-east", "furnishing": "semi-furnished",
        "amenities": "Lift, Power Backup , ", "power_backup": "yes"})
    assert not errors
    assert clean["bedrooms"] == 3 and clean["bathrooms"] == 2
    assert clean["facing"] == "North-East" and clean["furnishing"] == "Semi-Furnished"
    assert clean["amenities"] == ["Lift", "Power Backup"] and clean["power_backup"] is True
    _, errors = tax.validate("residential", "apartment", {"bedrooms": 3, "bathrooms": 2, "facing": "Up"})
    assert any("Facing" in e for e in errors)
    _, errors = tax.validate("residential", "apartment", {"bedrooms": 99, "bathrooms": 2})
    assert any("Bedrooms" in e for e in errors)


def test_loose_portal_words_resolve_to_a_category_and_type():
    assert tax.resolve_type("godown") == ("industrial", "warehouse")
    assert tax.resolve_type("3 bhk flat") == ("residential", "apartment")
    assert tax.resolve_type("agricultural land") == ("land", "agricultural_land")
    assert tax.resolve_type("office space for rent") == ("commercial", "office")
    assert tax.resolve_type("mall shop") == ("commercial", "mall_unit")
    assert tax.resolve_type("marriage hall") == ("hospitality", "banquet")


def test_bhk_in_the_type_string_fills_the_layout_fields():
    clean = tax.normalize_property({
        "type": "3 bhk flat", "deal_kind": "sale", "area": {"value": 1250, "unit": "sqft"},
        "pricing": {"amount": 6_500_000}, "attributes": {"facing": "East"}})
    assert clean["category"] == "residential" and clean["type"] == "apartment"
    assert clean["attributes"]["bedrooms"] == 3 and clean["attributes"]["bathrooms"] == 2


def test_a_lease_gets_a_deposit_and_lock_in_by_default():
    lease = tax.normalize_property({
        "category": "industrial", "type": "warehouse", "deal_kind": "lease",
        "area": {"value": 20000, "unit": "sqft"}, "pricing": {"amount": 24},
        "attributes": {"clear_height_m": 12, "floor_load_mt_sqm": 6}})
    assert lease["pricing"]["basis"] == "per_month_per_sqft"
    assert lease["pricing"]["deposit_months"] == 3 and lease["pricing"]["lock_in_months"] == 12


def test_legacy_rows_are_upgraded_not_dropped():
    old = {"id": "prp_old", "title": "Godavari Heights — 3BHK 903", "type": "apartment",
           "price": 6_200_000, "size_sqft": 1240}
    up = tax.hydrate(dict(old))
    assert up["category"] == "residential" and up["deal_kind"] == "sale"
    assert up["attributes"]["bedrooms"] == 3
    assert up["area"] == {"value": 1240.0, "unit": "sqft", "sqft": 1240.0}
    assert up["pricing"] == {"basis": "total", "amount": 6_200_000.0}
    legacy_land = tax.hydrate({"title": "Farm land", "type": "land", "price": 11_000_000, "size_sqft": 5227})
    assert legacy_land["category"] == "land" and legacy_land["type"] == "agricultural_land"


# ---------------------------------------------------------------------------
# 4. requirement scoring
# ---------------------------------------------------------------------------
def _flat(**over):
    prop = {"id": "prp_1", "title": "Flat", "category": "residential", "type": "apartment",
            "deal_kind": "sale", "city": "Nanded", "status": "available",
            "area": {"sqft": 1250},
            "attributes": {"bedrooms": 3, "bathrooms": 2, "facing": "East",
                           "furnishing": "Semi-Furnished", "amenities": ["Lift", "Power Backup"]},
            "pricing": {"basis": "total", "amount": 6_500_000}}
    prop.update(over)
    return prop


def test_scoring_rewards_a_match_and_explains_itself():
    spec = tax.normalize_requirement({"category": "residential", "types": ["apartment"], "deal_kind": "sale",
                                      "budget_max": 7_000_000, "bedrooms_min": 3, "facing": ["East"],
                                      "area_min": 1100, "area_unit": "sqft"})
    good = tax.score_requirement(_flat(), spec)
    assert good["score"] >= 90
    assert any("within budget" in r for r in good["reasons"])
    over = tax.score_requirement(_flat(pricing={"basis": "total", "amount": 9_000_000}), spec)
    assert over["score"] < good["score"]
    assert any("over budget" in r for r in over["reasons"])


def test_a_missing_must_have_caps_the_score():
    spec = tax.normalize_requirement({"category": "residential", "types": ["apartment"],
                                      "must_have": ["gym", "power backup"]})
    scored = tax.score_requirement(_flat(), spec)
    assert scored["score"] <= 85
    assert any("missing: gym" in r for r in scored["reasons"])
    assert any("has: power backup" in r for r in scored["reasons"])


def test_a_wrong_category_never_outranks_a_right_one():
    spec = tax.normalize_requirement({"category": "industrial", "types": ["warehouse"], "deal_kind": "lease",
                                      "budget_max": 2_000_000})
    warehouse = {"id": "prp_w", "title": "Warehouse", "category": "industrial", "type": "warehouse",
                 "deal_kind": "lease", "status": "available", "area": {"sqft": 60000},
                 "attributes": {"clear_height_m": 12, "floor_load_mt_sqm": 6},
                 "pricing": {"basis": "per_month_per_sqft", "amount": 24}}
    flat = _flat(id="prp_f")
    rows = tax.rank([flat, warehouse], spec, limit=5)
    assert rows[0]["property_id"] == "prp_w"
    assert rows[1]["score"] <= 60


def test_requirement_validation_refuses_nonsense():
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_requirement({"category": "commercial", "types": ["villa"]})
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_requirement({"category": "residential", "budget_min": 9_000_000, "budget_max": 8_000_000})
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_requirement({"category": "land", "area_min": 1, "area_unit": "bigha"})
    with pytest.raises(tax.TaxonomyError):
        tax.normalize_requirement({"category": "land", "deal_kind": "swap"})


# ---------------------------------------------------------------------------
# 5. HTTP surface
# ---------------------------------------------------------------------------
def test_the_catalogue_is_served_from_the_same_tables(owner):
    status, body, _ = owner.call("GET", "/api/v1/taxonomy")
    assert status == 200, body
    assert body["count"] == 5
    assert set(body["field_counts"]) == {"residential", "commercial", "industrial", "land", "hospitality"}
    assert body["refused_units"][0]["unit"] == "bigha"
    assert "per_seat_month" in body["price_bases"]
    units = {u["unit"] for u in body["area_units"]}
    assert {"sqft", "sqm", "acre", "guntha", "cent"} <= units
    # every category carries the required-field list the UI needs to mark asterisks
    for cat in body["categories"]:
        assert cat["required_fields"] or cat["required_any"], cat["slug"]
    hosp = [c for c in body["categories"] if c["slug"] == "hospitality"][0]
    assert set(hosp["required_any"]) == {"keys", "beds", "banquet_seating"}


def test_search_spans_categories_and_explains_rank(owner):
    status, body, _ = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "industrial", "types": ["warehouse"], "deal_kind": "lease",
                                  "budget_max": 2_000_000, "limit": 5})
    assert status == 200, body
    assert body["count"] >= 1
    top = body["results"][0]["property"]
    assert top["category"] == "industrial" and top["type"] == "warehouse"
    assert top["attributes"]["clear_height_m"] >= 10
    assert body["results"][0]["reasons"]

    status, body, _ = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "residential", "types": ["apartment"], "deal_kind": "sale"})
    assert status == 200 and body["count"] >= 1
    assert all(r["property"]["category"] == "residential" for r in body["results"])


def test_a_product_word_searches_the_product_not_the_title(owner):
    status, body, _ = owner.call("POST", "/api/v1/properties/search", {"q": "godown"})
    assert status == 200, body
    assert body["requirement"]["category"] == "industrial"
    assert body["count"] >= 1
    assert all(r["property"]["type"] == "warehouse" for r in body["results"])


def test_money_renders_in_indian_grouping():
    assert tax.inr(8_000_000) == "₹80,00,000"
    assert tax.inr(6_500_000) == "₹65,00,000"
    assert tax.inr(43_560) == "₹43,560"
    assert tax.inr(None) == "—"


def test_search_filters_on_attributes_with_ranges(owner):
    status, body, _ = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "industrial", "attributes": {"clear_height_m": {"min": 10}},
                                  "limit": 10})
    assert status == 200, body
    assert body["count"] >= 1
    for row in body["results"]:
        assert row["property"]["attributes"]["clear_height_m"] >= 10
    status, body, _ = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "hospitality", "attributes": {"fnb_licence": True}})
    assert status == 200 and all(r["property"]["attributes"]["fnb_licence"] is True for r in body["results"])


def test_search_refuses_an_impossible_requirement(owner):
    status, body, _ = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "land", "area_min": 1, "area_unit": "bigha"})
    assert status == 422 and body["error"]["code"] == "AREA_UNIT_REFUSED"
    status, body, _ = owner.call("POST", "/api/v1/properties/search", {"category": "spaceship"})
    assert status == 404 and body["error"]["code"] == "UNKNOWN_CATEGORY"


def test_create_requires_the_categorys_own_fields(owner):
    base = {"title": "Test warehouse", "city": "Nanded", "locality": "MIDC",
            "category": "industrial", "type": "warehouse", "deal_kind": "lease",
            "area": {"value": 15000, "unit": "sqft"}, "pricing": {"amount": 21}}
    status, body, _ = owner.call("POST", "/api/v1/properties", base)
    assert status == 422 and "Clear height" in body["error"]["message"], body
    ok = {**base, "attributes": {"clear_height_m": 10.5, "floor_load_mt_sqm": 5, "dock_levellers": 4}}
    status, body, _ = owner.call("POST", "/api/v1/properties", ok)
    assert status == 201 or status == 200, body
    prop = body["property"]
    assert prop["category"] == "industrial" and prop["area"]["sqft"] == 15000
    assert prop["money"]["monthly"] == 315_000
    status, body, _ = owner.call("POST", "/api/v1/properties",
                                 {**base, "type": "apartment"})
    assert status == 422 and body["error"]["code"] == "UNKNOWN_TYPE"


def test_a_saved_requirement_turns_into_a_shortlist(owner):
    status, body, _ = owner.call("GET", "/api/v1/contacts?limit=5")
    assert status == 200
    contact_id = body["contacts"][0]["id"]
    req = {"category": "residential", "types": ["apartment"], "deal_kind": "sale",
           "bedrooms_min": 2, "budget_max": 8_000_000, "city": "Pune"}
    status, body, _ = owner.call("POST", f"/api/v1/contacts/{contact_id}/requirement", req)
    assert status == 200, body
    assert body["requirement"]["category"] == "residential"
    assert "BHK" in body["summary"] or "apartment" in body["summary"]
    assert body["count"] >= 1
    assert all(m["property"]["category"] == "residential" for m in body["matches"])
    # the requirement is stored, not just echoed
    status, body2, _ = owner.call("GET", f"/api/v1/contacts/{contact_id}")
    assert status == 200
    assert body2["contact"]["requirement_spec"]["bedrooms_min"] == 2
    status, body, _ = owner.call("POST", f"/api/v1/contacts/{contact_id}/requirement", {"budget_max": 100})
    assert status == 400 and body["error"]["code"] == "VALIDATION_FAILED"
    status, body, _ = owner.call("POST", "/api/v1/contacts/cnt_missing/requirement", req)
    assert status == 404


def test_the_new_routes_are_authenticated_and_scoped(viewer, other_tenant, owner):
    status, _, _ = Session().call("GET", "/api/v1/taxonomy")
    assert status == 401
    # a viewer may read the catalogue and search, but not write inventory
    status, _, _ = viewer.call("GET", "/api/v1/taxonomy")
    assert status == 200
    status, _, _ = viewer.call("POST", "/api/v1/properties", {
        "title": "x", "category": "residential", "type": "apartment", "deal_kind": "sale",
        "area": {"value": 900, "unit": "sqft"}, "pricing": {"amount": 4000000},
        "attributes": {"bedrooms": 2, "bathrooms": 1}})
    assert status == 403
    # another tenant sees none of this firm's inventory
    status, other, _ = other_tenant.call("POST", "/api/v1/properties/search", {"limit": 50})
    assert status == 200
    assert other["count"] == 0 and other["candidates"] == 0
    status, ours, _ = owner.call("POST", "/api/v1/properties/search", {"limit": 50})
    assert status == 200 and ours["count"] >= 20
    theirs = {r["property"]["id"] for r in other["results"]}
    assert not theirs & {r["property"]["id"] for r in ours["results"]}

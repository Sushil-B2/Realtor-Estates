"""
Tests for `v1.35.0` — Complete Indian / Maharashtra Government, Revenue (`7/12`),
Statutory Cost-Sheet, IGR `e-Registration` / `iSarita` SRO, Home-Loan / RERA CLP,
`E-Ferfar` Talathi Mutation & Marketing Verified Trust Pack (`Tracks G-1`..`G-6`).
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from app import api, govt_sales_cycle as gsc, security as sec
from app.store import Store


@pytest.fixture()
def env_v135():
    sec.LIMITER.hits.clear()
    with tempfile.TemporaryDirectory(prefix="re_test_v135_") as td:
        store = Store(os.path.join(td, "store.json"))
        tid = store.db["tenants"][0]["id"]
        _, login, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "198.51.100.131", None,
        )
        hdr = {"authorization": f"Bearer {login['token']}"}
        yield store, tid, hdr


def _make_prop_and_deal(store: Store, tid: str, hdr: dict, *, price: int = 6_000_000) -> tuple[str, str]:
    _, p_res, _ = api.handle(
        store, "POST", "/api/v1/properties", {},
        {
            "title": "Sahyadri Vista NA Layout",
            "city": "Nanded",
            "locality": "Taroda Naka",
            "type": "3 bhk flat",
            "deal_kind": "sale",
            "area": {"value": 1650, "unit": "sqft"},
            "pricing": {"amount": price},
            "attributes": {"facing": "East", "rera_id": "P51100088112"},
        },
        hdr, "198.51.100.131", None,
    )
    pid = p_res["property"]["id"]
    _, c_res, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Sneha Deshmukh", "phone": "+919822110099", "city": "Nanded", "consent": {"status": "granted"}},
        hdr, "198.51.100.131", None,
    )
    cid = c_res["contact"]["id"]
    deal = store.insert(
        "deals", tid,
        {
            "title": "Sneha Deshmukh — Unit 302",
            "contact_id": cid,
            "property_id": pid,
            "value": price,
            "stage": "negotiation",
            "probability": 50,
            "documents": [],
        },
        prefix="dl",
    )
    did = deal["id"]
    store.insert("site_visits", tid, {"contact_id": cid, "property_id": pid, "deal_id": did, "status": "visited", "scheduled_at": 1760000000})
    api.handle(store, "POST", f"/api/v1/deals/{did}/documents", {}, {"name": "KYC (Aadhaar/PAN)", "status": "verified"}, hdr, "198.51.100.131", None)
    api.handle(store, "POST", f"/api/v1/deals/{did}/documents", {}, {"name": "Booking form", "status": "received"}, hdr, "198.51.100.131", None)
    api.handle(store, "POST", f"/api/v1/deals/{did}/documents", {}, {"name": "Sale agreement draft", "status": "received"}, hdr, "198.51.100.131", None)
    return pid, did


def test_g1_revenue_vault_class2_stale_7_12_and_boja_blockers(env_v135):
    store, tid, hdr = env_v135
    pid, did = _make_prop_and_deal(store, tid, hdr)
    today_iso = dt.date.today().isoformat()
    stale_iso = (dt.date.today() - dt.timedelta(days=40)).isoformat()

    # 1. Class-II land without Collector permission blocks token booking
    st, res, _ = api.handle(
        store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
        {
            "satbara_7_12": {
                "occupant_class": "class_2",
                "collector_transfer_permission_ref": "",
                "nazrana_paid": False,
                "gat_or_survey_no": "Gat 44/1",
                "issued_at": stale_iso,
                "itar_hakk_boja": ["SBI Crop Charge ₹8L"],
                "bank_noc_obtained": False,
            },
        },
        hdr, "198.51.100.131", None,
    )
    assert st == 200
    assert res["clearance"]["can_accept_token"] is False
    assert any(w["code"] == "SATBARA_STALE_OVER_30D" for w in res["clearance"]["warnings"])

    st_tok, body_tok, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/advance", {}, {"stage": "token"}, hdr, "198.51.100.131", None)
    assert st_tok == 409
    assert body_tok["error"]["code"] == "CLASS_2_COLLECTOR_PERMISSION_REQUIRED"

    # 2. Class-I with active unreleased Boja allows token, but blocks agreement!
    api.handle(
        store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
        {
            "satbara_7_12": {
                "occupant_class": "class_1",
                "gat_or_survey_no": "Gat 44/1",
                "issued_at": today_iso,
                "itar_hakk_boja": ["SBI Crop Charge ₹8L"],
                "bank_noc_obtained": False,
            },
        },
        hdr, "198.51.100.131", None,
    )
    st_tok2, _, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/advance", {}, {"stage": "token"}, hdr, "198.51.100.131", None)
    assert st_tok2 == 200
    st_agr, body_agr, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/advance", {}, {"stage": "agreement"}, hdr, "198.51.100.131", None)
    assert st_agr == 409
    assert body_agr["error"]["code"] == "UNRELEASED_BOJA_ENCUMBRANCE"

    # 3. Bank NOC + fresh 7/12 clears agreement gate
    api.handle(
        store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
        {
            "satbara_7_12": {
                "occupant_class": "class_1",
                "gat_or_survey_no": "Gat 44/1",
                "issued_at": today_iso,
                "itar_hakk_boja": ["SBI Crop Charge ₹8L"],
                "bank_noc_obtained": True,
            },
            "na_order": {"order_no": "COL/NND/44/2026", "verified": True},
        },
        hdr, "198.51.100.131", None,
    )
    st_agr_ok, _, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/advance", {}, {"stage": "agreement"}, hdr, "198.51.100.131", None)
    assert st_agr_ok == 200


def test_g2_statutory_cost_sheet_zones_asr_women_concession_and_194ia():
    # Below ₹50L in council zone: no 194-IA TDS, reg fee 1% uncapped below ₹30L
    s_low = gsc.compute_statutory_cost_sheet({
        "agreement_value_inr": 2_400_000,
        "local_body_zone": "municipal_council",
        "property_gst_category": "na_plot_or_oc_ready",
    })
    assert s_low["effective_stamp_duty_pct"] == 5.0
    assert s_low["stamp_duty_inr"] == 120_000
    assert s_low["registration_fee_inr"] == 24_000
    assert s_low["tds_194ia"]["applicable"] is False
    assert s_low["tds_194ia"]["tds_amount_inr"] == 0

    # Metro zone + ASR uplift + Sole Female concession (-1%: 7% -> 6%) + ≥ ₹50L TDS
    s_fem = gsc.compute_statutory_cost_sheet({
        "agreement_value_inr": 7_000_000,
        "asr_ready_reckoner_inr": 7_500_000,
        "local_body_zone": "municipal_corp_metro",
        "buyer_gender_scheme": "sole_or_joint_female",
        "property_gst_category": "standard_under_construction",
    })
    assert s_fem["asr_higher_than_agreement"] is True
    assert s_fem["assessable_market_value_inr"] == 7_500_000
    assert s_fem["effective_stamp_duty_pct"] == 6.0
    assert s_fem["stamp_duty_inr"] == 450_000
    assert s_fem["registration_fee_inr"] == 30_000  # capped at ₹30k
    assert s_fem["gst_inr"] == 350_000  # 5% of agreement value
    assert s_fem["tds_194ia"]["applicable"] is True
    assert s_fem["tds_194ia"]["tds_amount_inr"] == 70_000
    assert s_fem["tds_194ia"]["net_payable_to_seller_inr"] == 6_930_000

    # Commercial property does NOT receive residential women concession
    s_comm = gsc.compute_statutory_cost_sheet({
        "agreement_value_inr": 6_000_000,
        "local_body_zone": "municipal_corp_tier2",
        "buyer_gender_scheme": "sole_or_joint_female",
        "property_gst_category": "commercial_under_construction",
    })
    assert s_comm["women_concession_pct"] == 0.0
    assert s_comm["effective_stamp_duty_pct"] == 6.0
    assert s_comm["gst_pct"] == 12.0


def test_g3_isarita_witness_gate_eregistration_and_index2_sync(env_v135):
    store, tid, hdr = env_v135
    pid, did = _make_prop_and_deal(store, tid, hdr, price=5_500_000)
    today_iso = dt.date.today().isoformat()
    api.handle(
        store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
        {"satbara_7_12": {"occupant_class": "class_1", "issued_at": today_iso, "itar_hakk_boja": []}},
        hdr, "198.51.100.131", None,
    )

    # Physical SRO mode without two witnesses & without Form 26QB stays unready
    _, r1, _ = api.handle(
        store, "PUT", f"/api/v1/deals/{did}/registration", {},
        {
            "mode": "isarita_sro_physical",
            "gras_challan_no": "MHGRAS2026991100",
            "stamp_duty_paid_inr": 330_000,
            "reg_fee_paid_inr": 30_000,
        },
        hdr, "198.51.100.131", None,
    )
    assert r1["govt_registration"]["ready_for_execution"] is False
    assert "TWO_WITNESSES_REQUIRED" in r1["govt_registration"]["pending_gates"]
    assert "TDS_26QB_ACK_REQUIRED" in r1["govt_registration"]["pending_gates"]

    st_blk, _, _ = api.handle(
        store, "POST", f"/api/v1/deals/{did}/registration/index-ii", {},
        {"registered_doc_no": "NND-1-9901-2026", "index_ii_ref": "IDX2-9901"},
        hdr, "198.51.100.131", None,
    )
    assert st_blk == 409

    # Provide both witnesses + Form 26QB -> ready -> complete Index-II
    _, r2, _ = api.handle(
        store, "PUT", f"/api/v1/deals/{did}/registration", {},
        {
            "mode": "isarita_sro_physical",
            "gras_challan_no": "MHGRAS2026991100",
            "stamp_duty_paid_inr": 330_000,
            "reg_fee_paid_inr": 30_000,
            "form_26qb_ack_no": "26QB20268811",
            "witness_1_name": "Mahesh Joshi",
            "witness_2_name": "Prakash Jadhav",
            "appointment_at": today_iso,
        },
        hdr, "198.51.100.131", None,
    )
    assert r2["govt_registration"]["ready_for_execution"] is True
    st_ok, idx_res, _ = api.handle(
        store, "POST", f"/api/v1/deals/{did}/registration/index-ii", {},
        {"registered_doc_no": "NND-1-9901-2026", "index_ii_ref": "IDX2-9901"},
        hdr, "198.51.100.131", None,
    )
    assert st_ok == 200
    doc_names = {d["name"] for d in idx_res["deal"]["documents"]}
    assert {"Stamp duty receipt", "Registration appointment", "Registered sale deed"} <= doc_names


def test_g4_apf_home_loan_and_rera_clp_architect_gate(env_v135):
    store, tid, hdr = env_v135
    pid, did = _make_prop_and_deal(store, tid, hdr, price=5_000_000)

    _, apf, _ = api.handle(
        store, "POST", f"/api/v1/properties/{pid}/bank-apf", {},
        {"bank_name": "HDFC Bank", "apf_code": "HDFC-NND-209", "max_ltv_pct": 80},
        hdr, "198.51.100.131", None,
    )
    assert apf["apf"]["apf_code"] == "HDFC-NND-209"
    _, apf_get, _ = api.handle(store, "GET", f"/api/v1/properties/{pid}/bank-apf", {}, None, hdr, "198.51.100.131", None)
    assert len(apf_get["bank_apf"]) == 1

    _, clp_init, _ = api.handle(
        store, "POST", f"/api/v1/deals/{did}/clp/init", {},
        {"schedule_kind": "rera_building_7stage"},
        hdr, "198.51.100.131", None,
    )
    assert sum(m["amount_inr"] for m in clp_init["clp_schedule"]) == 5_000_000

    # Token milestone needs no architect cert; slabs milestone requires it
    st_tok, _, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/clp/demand", {}, {"milestone_code": "token"}, hdr, "198.51.100.131", None)
    assert st_tok == 200

    st_slabs_blk, blk_body, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/clp/demand", {}, {"milestone_code": "slabs"}, hdr, "198.51.100.131", None)
    assert st_slabs_blk == 409
    assert blk_body["error"]["code"] == "ARCHITECT_CERTIFICATE_REQUIRED"

    st_slabs_ok, ok_body, _ = api.handle(
        store, "POST", f"/api/v1/deals/{did}/clp/demand", {},
        {"milestone_code": "slabs", "architect_cert_ref": "FORM1-ARCH-SLAB-04"},
        hdr, "198.51.100.131", None,
    )
    assert st_slabs_ok == 200
    assert ok_body["milestone"]["amount_inr"] == 1_250_000


def test_g5_and_g6_eferfar_buyer_portal_and_verified_trust_pack(env_v135):
    store, tid, hdr = env_v135
    pid, did = _make_prop_and_deal(store, tid, hdr, price=4_500_000)

    # Unverified property only gets MAHARERA_VERIFIED (from attributes.rera_id), NOT 7/12 or NA or Title
    _, pack_initial, _ = api.handle(store, "GET", f"/api/v1/properties/{pid}/marketing-trust-pack", {}, None, hdr, "198.51.100.131", None)
    init_codes = {b["code"] for b in pack_initial["verified_badges"]}
    assert init_codes == {"MAHARERA_VERIFIED"}

    # Complete E-Ferfar 15-day notice + handover and verify on Buyer Portal
    _, hnd, _ = api.handle(
        store, "PUT", f"/api/v1/deals/{did}/handover", {},
        {
            "ferfar_entry_no": "FF-7712",
            "circle_officer_certified": True,
            "updated_7_12_or_property_card_issued": True,
            "namuna8_municipal_tax_transferred": True,
            "mseb_meter_transferred": True,
            "possession_letter_issued": True,
        },
        hdr, "198.51.100.131", None,
    )
    assert hnd["post_reg_handover"]["completion_pct"] == 100
    assert hnd["post_reg_handover"]["ferfar_notice_clears_at"] > hnd["post_reg_handover"]["ferfar_applied_at"]

    sec.LIMITER.hits.clear()
    _, bp, _ = api.handle(store, "POST", f"/api/v1/deals/{did}/buyer-portal", {}, {"buyer_residency": "nri"}, hdr, "198.51.100.131", None)
    st_p, p_res, _ = api.handle(store, "GET", f"/api/v1/public/buyer-portal/{bp['token']}", {}, None, {}, "198.51.100.132", None)
    assert st_p == 200
    assert p_res["portal"]["post_reg_handover"]["ferfar_entry_no"] == "FF-7712"
    assert p_res["portal"]["post_reg_handover"]["completion_pct"] == 100

    # Cross-tenant isolation check -> 404
    other_tid = store.db["tenants"][1]["id"]
    other_prop = store.insert("properties", other_tid, {"title": "Other Tenant Plot", "city": "Pune"}, prefix="prp")
    st_cross, _, _ = api.handle(store, "GET", f"/api/v1/properties/{other_prop['id']}/revenue-vault", {}, None, hdr, "198.51.100.131", None)
    assert st_cross == 404

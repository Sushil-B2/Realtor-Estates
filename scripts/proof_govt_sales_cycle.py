#!/usr/bin/env python3
"""
End-to-end proof for `v1.35.0` (`docs/46-GOVT-REVENUE-EREGISTRATION-LOAN-MUTATION-AND-TRUST-PACK.md`):
  1. Track G-1: Land & Revenue Vault (`7/12` 30-day freshness, Class-II Collector gate, active Boja blocker)
  2. Track G-2: Maharashtra Statutory Cost-Sheet Generator (ASR floor, Women -1% concession, ₹30k Reg cap, §194-IA TDS)
  3. Track G-3: IGR `e-Registration` & `iSarita` SRO Desk (GRAS challan, Form 26QB gate, biometric checklist, Index-II sync)
  4. Track G-4: Bank APF Registry, Buyer Home Loan & RERA CLP Milestone Demands (Architect Certificate gate)
  5. Track G-5: Post-Registration `E-Ferfar` Talathi Mutation, Possession Handover & Live `#/buyer-portal/<token>`
  6. Track G-6: Marketing Govt.-Verified Trust Badges & All-In Cost Pack on Public Funnels (`/f/<slug>`)
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import api, security as sec
from app.store import Store


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    sec.LIMITER.hits.clear()
    with tempfile.TemporaryDirectory(prefix="re_proof_v135_") as td:
        store = Store(os.path.join(td, "store.json"))
        tid = store.db["tenants"][0]["id"]

        _, login, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "198.51.100.121", None,
        )
        hdr = {"authorization": f"Bearer {login['token']}"}
        today_iso = dt.date.today().isoformat()
        stale_iso = (dt.date.today() - dt.timedelta(days=45)).isoformat()

        # Create a clean test property + contact + site_visit + deal
        _, prop_res, _ = api.handle(
            store, "POST", "/api/v1/properties", {},
            {
                "title": "Godavari Crest NA Plots",
                "city": "Nanded",
                "locality": "Wajegaon",
                "type": "3 bhk flat",
                "deal_kind": "sale",
                "area": {"value": 1800, "unit": "sqft"},
                "pricing": {"amount": 6000000},
                "attributes": {"facing": "East", "rera_id": "P51100049821"},
            },
            hdr, "198.51.100.121", None,
        )
        pid = prop_res["property"]["id"]

        _, contact_res, _ = api.handle(
            store, "POST", "/api/v1/contacts", {},
            {
                "name": "Smt. Kavita Kulkarni",
                "phone": "+919823445566",
                "city": "Nanded",
                "consent": {"status": "granted"},
            },
            hdr, "198.51.100.121", None,
        )
        cid = contact_res["contact"]["id"]

        deal_row = store.insert(
            "deals",
            tid,
            {
                "title": "Kavita Kulkarni — Plot 14",
                "contact_id": cid,
                "property_id": pid,
                "value": 6000000,
                "stage": "negotiation",
                "probability": 50,
                "documents": [],
            },
            prefix="dl",
        )
        did = deal_row["id"]
        store.insert("site_visits", tid, {"contact_id": cid, "property_id": pid, "deal_id": did, "status": "visited", "scheduled_at": 1760000000})
        api.handle(store, "POST", f"/api/v1/deals/{did}/documents", {}, {"name": "KYC (Aadhaar/PAN)", "status": "verified"}, hdr, "198.51.100.121", None)
        api.handle(store, "POST", f"/api/v1/deals/{did}/documents", {}, {"name": "Booking form", "status": "received"}, hdr, "198.51.100.121", None)

        # ------------------------------------------------------------------
        # 1. Track G-1 — Land & Revenue Vault (`7/12`, Class-II, Boja)
        # ------------------------------------------------------------------
        _, v_blocked, _ = api.handle(
            store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
            {
                "satbara_7_12": {
                    "occupant_class": "class_2",
                    "collector_transfer_permission_ref": "",
                    "nazrana_paid": False,
                    "gat_or_survey_no": "Gat 88/1A",
                    "issued_at": stale_iso,
                    "itar_hakk_boja": ["DCC Bank Nanded ₹12L"],
                    "bank_noc_obtained": False,
                },
            },
            hdr, "198.51.100.121", None,
        )
        st_tok_blk, tok_blk_body, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/advance", {},
            {"stage": "token"},
            hdr, "198.51.100.121", None,
        )
        _, v_clear, _ = api.handle(
            store, "PUT", f"/api/v1/properties/{pid}/revenue-vault", {},
            {
                "satbara_7_12": {
                    "occupant_class": "class_1",
                    "gat_or_survey_no": "Gat 88/1A",
                    "issued_at": today_iso,
                    "itar_hakk_boja": [],
                    "bank_noc_obtained": True,
                },
                "khata_8a": {"khata_no": "8A-712", "verified": True},
                "na_order": {"order_no": "COL/NND/NA-19/2026", "verified": True},
                "mojni_demarcation": {"case_no": "MOJ/2026/44", "boundary_stones_fixed": True, "verified": True},
                "title_search": {"years_searched": 30, "advocate_name": "Adv. Patil & Co.", "clear_marketable_title": True},
            },
            hdr, "198.51.100.121", None,
        )
        st_tok_ok, _, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/advance", {},
            {"stage": "token"},
            hdr, "198.51.100.121", None,
        )
        record(
            "track_g1_revenue_vault_7_12_class2_and_boja_gates",
            v_blocked["clearance"]["can_accept_token"] is False
            and st_tok_blk == 409
            and tok_blk_body["error"]["code"] == "CLASS_2_COLLECTOR_PERMISSION_REQUIRED"
            and v_clear["clearance"]["can_execute_agreement"] is True
            and st_tok_ok == 200,
            f"blocked={tok_blk_body['error']['code']}, cleared_7_12_days={v_clear['clearance']['satbara_age_days']}",
        )

        # ------------------------------------------------------------------
        # 2. Track G-2 — Statutory Cost-Sheet Generator (ASR, Women -1%, 194-IA)
        # ------------------------------------------------------------------
        _, cs_res, _ = api.handle(
            store, "POST", "/api/v1/govt/cost-sheet", {},
            {
                "agreement_value_inr": 6000000,
                "asr_ready_reckoner_inr": 6400000,
                "local_body_zone": "municipal_corp_tier2",
                "buyer_gender_scheme": "sole_or_joint_female",
                "property_gst_category": "na_plot_or_oc_ready",
            },
            hdr, "198.51.100.121", None,
        )
        cs = cs_res["cost_sheet"]
        record(
            "track_g2_statutory_cost_sheet_asr_women_and_194ia",
            cs["asr_higher_than_agreement"] is True
            and cs["assessable_market_value_inr"] == 6400000
            and cs["effective_stamp_duty_pct"] == 5.0
            and cs["stamp_duty_inr"] == 320000
            and cs["registration_fee_inr"] == 30000
            and cs["tds_194ia"]["applicable"] is True
            and cs["tds_194ia"]["tds_amount_inr"] == 60000
            and cs["tds_194ia"]["net_payable_to_seller_inr"] == 5940000,
            f"stamp=₹{cs['stamp_duty_inr']:,}, reg=₹{cs['registration_fee_inr']:,}, tds=₹{cs['tds_194ia']['tds_amount_inr']:,}",
        )

        # ------------------------------------------------------------------
        # 3. Track G-3 — IGR e-Registration, GRAS, Form 26QB & Index-II
        # ------------------------------------------------------------------
        api.handle(
            store, "PUT", f"/api/v1/deals/{did}/registration", {},
            {"mode": "e_registration_builder_office", "gras_challan_no": ""},
            hdr, "198.51.100.121", None,
        )
        st_idx_blk, _, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/registration/index-ii", {},
            {"registered_doc_no": "NND-1-5510-2026", "index_ii_ref": "IDX2-NND-5510"},
            hdr, "198.51.100.121", None,
        )
        _, reg_ready, _ = api.handle(
            store, "PUT", f"/api/v1/deals/{did}/registration", {},
            {
                "mode": "e_registration_builder_office",
                "sro_office_name": "SRO Nanded-1",
                "buyer_gender_scheme": "sole_or_joint_female",
                "gras_challan_no": "MHGRAS2026008812",
                "stamp_duty_paid_inr": 300000,
                "reg_fee_paid_inr": 30000,
                "form_26qb_ack_no": "26QB2026771199",
                "aadhaar_biometric_ready": True,
            },
            hdr, "198.51.100.121", None,
        )
        st_idx_ok, idx_ok, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/registration/index-ii", {},
            {"registered_doc_no": "NND-1-5510-2026", "index_ii_ref": "IDX2-NND-5510"},
            hdr, "198.51.100.121", None,
        )
        doc_names = {d["name"] for d in (idx_ok["deal"].get("documents") or []) if d.get("status") == "verified"}
        record(
            "track_g3_igr_eregistration_gras_26qb_and_index2",
            st_idx_blk == 409
            and reg_ready["govt_registration"]["ready_for_execution"] is True
            and st_idx_ok == 200
            and "Registered sale deed" in doc_names
            and "Stamp duty receipt" in doc_names,
            f"doc={idx_ok['govt_registration']['registered_doc_no']}, synced_docs={len(doc_names)}",
        )

        # ------------------------------------------------------------------
        # 4. Track G-4 — Bank APF, Home Loan & RERA CLP Architect Gate
        # ------------------------------------------------------------------
        _, apf_res, _ = api.handle(
            store, "POST", f"/api/v1/properties/{pid}/bank-apf", {},
            {"bank_name": "SBI", "apf_code": "SBI-NND-APF-774", "max_ltv_pct": 80},
            hdr, "198.51.100.121", None,
        )
        _, loan_res, _ = api.handle(
            store, "PUT", f"/api/v1/deals/{did}/home-loan", {},
            {"funding_mode": "bank_loan", "bank_name": "SBI", "apf_code": "SBI-NND-APF-774", "stage": "sanctioned", "sanctioned_amount_inr": 4500000},
            hdr, "198.51.100.121", None,
        )
        api.handle(
            store, "POST", f"/api/v1/deals/{did}/clp/init", {},
            {"schedule_kind": "rera_building_7stage"},
            hdr, "198.51.100.121", None,
        )
        st_clp_blk, clp_blk, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/clp/demand", {},
            {"milestone_code": "plinth"},
            hdr, "198.51.100.121", None,
        )
        st_clp_ok, clp_ok, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/clp/demand", {},
            {"milestone_code": "plinth", "architect_cert_ref": "RERA-FORM1-ARCH-2026-09"},
            hdr, "198.51.100.121", None,
        )
        record(
            "track_g4_bank_apf_home_loan_and_rera_clp_architect_gate",
            len(apf_res["bank_apf"]) == 1
            and loan_res["home_loan"]["stage"] == "sanctioned"
            and st_clp_blk == 409
            and clp_blk["error"]["code"] == "ARCHITECT_CERTIFICATE_REQUIRED"
            and st_clp_ok == 200
            and clp_ok["milestone"]["amount_inr"] == 900000,
            f"apf={apf_res['apf']['apf_code']}, plinth_demand=₹{clp_ok['milestone']['amount_inr']:,}",
        )

        # ------------------------------------------------------------------
        # 5. Track G-5 — E-Ferfar Mutation, Handover & Buyer Portal
        # ------------------------------------------------------------------
        _, hnd_res, _ = api.handle(
            store, "PUT", f"/api/v1/deals/{did}/handover", {},
            {
                "ferfar_entry_no": "FF-2026-1120",
                "circle_officer_certified": True,
                "updated_7_12_or_property_card_issued": True,
                "namuna8_municipal_tax_transferred": True,
                "mseb_meter_transferred": True,
                "snag_list_cleared": True,
                "possession_letter_issued": True,
            },
            hdr, "198.51.100.121", None,
        )
        sec.LIMITER.hits.clear()
        _, bp_mint, _ = api.handle(
            store, "POST", f"/api/v1/deals/{did}/buyer-portal", {},
            {"buyer_residency": "resident_indian", "ttl_days": 30},
            hdr, "198.51.100.121", None,
        )
        _, bp_view, _ = api.handle(
            store, "GET", f"/api/v1/public/buyer-portal/{bp_mint['token']}", {},
            None, {}, "198.51.100.122", None,
        )
        portal = bp_view["portal"]
        record(
            "track_g5_eferfar_talathi_mutation_handover_and_buyer_portal",
            hnd_res["post_reg_handover"]["completion_pct"] == 100
            and portal["revenue_clearance"]["can_execute_agreement"] is True
            and portal["govt_registration"]["status"] == "registered"
            and portal["post_reg_handover"]["circle_officer_certified"] is True
            and portal["post_reg_handover"]["completion_pct"] == 100,
            f"ferfar={portal['post_reg_handover']['ferfar_entry_no']}, completion={portal['post_reg_handover']['completion_pct']}%",
        )

        # ------------------------------------------------------------------
        # 6. Track G-6 — Marketing Trust Pack & Public Funnel SSR Badges
        # ------------------------------------------------------------------
        _, pack_res, _ = api.handle(
            store, "GET", f"/api/v1/properties/{pid}/marketing-trust-pack", {},
            None, hdr, "198.51.100.121", None,
        )
        _, fnl_res, _ = api.handle(
            store, "POST", "/api/v1/funnels", {},
            {
                "name": "Godavari Crest Clear-Title Funnel",
                "slug": "godavari-crest-verified",
                "property_id": pid,
                "headline": "Own a 100% Clear-Title Property in Nanded",
                "sub": "MahaRERA + Collector NA + Fresh 7/12 Verified",
            },
            hdr, "198.51.100.121", None,
        )
        fid = fnl_res["funnel"]["id"]
        api.handle(store, "PATCH", f"/api/v1/funnels/{fid}", {}, {"published": True}, hdr, "198.51.100.121", None)
        f_status, f_html = api.public_funnel_page(store, "godavari-crest-verified")
        badge_codes = {b["code"] for b in pack_res["verified_badges"]}
        record(
            "track_g6_marketing_verified_trust_pack_and_public_funnel",
            pack_res["badge_count"] == 5
            and {"MAHARERA_VERIFIED", "NA_COLLECTOR_VERIFIED", "FRESH_7_12_ZERO_BOJA", "TITLE_30YR_CLEAR", "BANK_APF_APPROVED"} <= badge_codes
            and f_status == 200
            and sec.esc("Fresh 7/12 (<30d) · Zero Boja") in f_html
            and "Transparent All-In Buyer Cost Sheet" in f_html,
            f"badges={pack_res['badge_count']}, funnel_ssr_status={f_status}",
        )

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nPROOF SUMMARY: {passed}/{len(checks)} checks passed.")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

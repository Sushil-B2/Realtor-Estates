#!/usr/bin/env python3
"""
End-to-end proof for `v1.34.0` (`docs/45-PHASE4-PLATFORM-AND-VERTICAL-POLISH.md`):
  1. Phase 4.1: On-Device Client PII Pre-Screening & Lead Pre-Scoring (`RE.onDevicePrescreen` + Server Verify)
  2. Phase 4.2: AP2 / ACP Agentic Commerce Mandates (Cap Enforcement, Idempotent Settlement & Custody Receipt)
  3. Phase 4.3: GCC (`AE` DLD Trakheesi) & SEA (`SG` CEA + ABSD) Regional Gates & Data-Residency Pinning
  4. Phase 4.4: Crypto-Agility & Hybrid Post-Quantum Envelope (`v2:hybrid-sp80056c-chacha20hmac:` + Downgrade Defence)
  5. G5 Polish: Property Photo Cover Pinning / Reordering + SSRF-Guarded Floor-Plan & Walkthrough Slots
  6. G7 Polish: Isolated Disposable `proof-mode` Sandbox Workspaces + Real-Tenant Teardown Refusal
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import api, media as md, phase4_platform as p4, security as sec
from app.store import Store


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    sec.LIMITER.hits.clear()
    with tempfile.TemporaryDirectory(prefix="re_proof_v134_") as td:
        store = Store(os.path.join(td, "store.json"))
        tid = store.db["tenants"][0]["id"]

        _, login, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "198.51.100.111", None,
        )
        hdr = {"authorization": f"Bearer {login['token']}"}

        # ------------------------------------------------------------------
        # 1. Phase 4.1 — On-Device PII Pre-Screening & Server Verification
        # ------------------------------------------------------------------
        _, mc, _ = api.handle(store, "GET", "/api/v1/prescreen/model-card", {}, None, hdr, "198.51.100.111", None)
        _, p_raw, _ = api.handle(
            store, "POST", "/api/v1/prescreen/verify", {},
            {"text": "Nanded plot visit this Saturday, budget 48 Lakh, PAN ABCDE1234F, Aadhaar 4567 8901 2345"},
            hdr, "198.51.100.111", None,
        )
        _, p_clean, _ = api.handle(
            store, "POST", "/api/v1/prescreen/verify", {},
            {"text": p_raw["prescreen"]["clean_text"], "runtime": "webgpu"},
            hdr, "198.51.100.111", None,
        )
        record(
            "phase4_ondevice_prescreen_and_pii_redaction",
            mc["model_card"]["zero_network_roundtrip"] is True
            and p_raw["prescreen"]["client_already_redacted"] is False
            and "[REDACTED_PAN]" in p_raw["prescreen"]["clean_text"]
            and "[REDACTED_AADHAAR]" in p_raw["prescreen"]["clean_text"]
            and p_clean["prescreen"]["client_already_redacted"] is True
            and p_clean["prescreen"]["tier"] == "hot",
            f"score={p_clean['prescreen']['score']} tier={p_clean['prescreen']['tier']}",
        )

        # ------------------------------------------------------------------
        # 2. Phase 4.2 — AP2 / ACP Agentic Commerce Mandates & Settlement
        # ------------------------------------------------------------------
        avail_unit = next(u for u in store.all("units", tid) if u.get("status") == "available")
        deal = store.all("deals", tid)[0]
        st_m, m_res, _ = api.handle(
            store, "POST", "/api/v1/commerce/mandates", {},
            {"deal_id": deal["id"], "unit_id": avail_unit["id"], "allowed_action": "book", "max_amount_inr": 100_000},
            hdr, "198.51.100.111", None,
        )
        m_tok = m_res["mandate_token"]
        st_over, _, _ = api.handle(
            store, "POST", "/api/v1/commerce/settle", {},
            {"mandate_token": m_tok, "action": "book", "amount_inr": 150_000},
            hdr, "198.51.100.111", None,
        )
        st_settle, s_res, _ = api.handle(
            store, "POST", "/api/v1/commerce/settle", {},
            {"mandate_token": m_tok, "action": "book", "amount_inr": 50_000, "idempotency_ref": "idem_ap2_1"},
            hdr, "198.51.100.111", None,
        )
        st_idem, s_idem, _ = api.handle(
            store, "POST", "/api/v1/commerce/settle", {},
            {"mandate_token": m_tok, "action": "book", "amount_inr": 50_000, "idempotency_ref": "idem_ap2_1"},
            hdr, "198.51.100.111", None,
        )
        st_tamper, _, _ = api.handle(
            store, "POST", "/api/v1/commerce/settle", {},
            {"mandate_token": m_tok[:-3] + "ZZZ", "action": "book", "amount_inr": 50_000},
            hdr, "198.51.100.111", None,
        )
        record(
            "phase4_ap2_mandate_cap_idempotent_settlement_and_receipt",
            st_m == 200
            and st_over == 422
            and st_settle == 200
            and s_res["settlement"]["unit_status"] == "booked"
            and s_res["receipt_number"].startswith("RC-AP2-")
            and st_idem == 200 and s_idem["settlement_signature"] == s_res["settlement_signature"]
            and st_tamper == 401,
            f"over_cap={st_over} settled={st_settle} receipt={s_res['receipt_number']}",
        )

        # ------------------------------------------------------------------
        # 3. Phase 4.3 — GCC (`AE`) & SEA (`SG`) Regional Gates & Pinning
        # ------------------------------------------------------------------
        api.handle(store, "PATCH", "/api/v1/regions/pin", {}, {"region_code": "AE"}, hdr, "198.51.100.111", None)
        _, ae_missing, _ = api.handle(
            store, "POST", "/api/v1/regions/compliance-check", {},
            {"property": {"title": "Dubai Marina Tower", "price": 2_500_000}},
            hdr, "198.51.100.111", None,
        )
        _, ae_ok, _ = api.handle(
            store, "POST", "/api/v1/regions/compliance-check", {},
            {"property": {"title": "Dubai Marina Tower", "price": 2_500_000, "trakheesi_permit": "TRK-98765432"}},
            hdr, "198.51.100.111", None,
        )
        _, ae_leak, _ = api.handle(
            store, "POST", "/api/v1/regions/compliance-check", {},
            {"property": {"trakheesi_permit": "TRK-98765432"}, "target_export_zone": "ap-south-1"},
            hdr, "198.51.100.111", None,
        )
        api.handle(store, "PATCH", "/api/v1/regions/pin", {}, {"region_code": "SG"}, hdr, "198.51.100.111", None)
        _, sg_for, _ = api.handle(
            store, "POST", "/api/v1/regions/compliance-check", {},
            {"property": {"title": "Orchard Condo", "price": 2_000_000, "cea_licence": "L3008022J"}, "buyer_profile": "foreigner"},
            hdr, "198.51.100.111", None,
        )
        api.handle(store, "PATCH", "/api/v1/regions/pin", {}, {"region_code": "IN"}, hdr, "198.51.100.111", None)
        record(
            "phase4_gcc_trakheesi_sea_cea_absd_and_residency_pin",
            ae_missing["compliance"]["code"] == "TRAKHEESI_PERMIT_REQUIRED"
            and ae_ok["compliance"]["compliant"] is True
            and ae_ok["compliance"]["dld_transfer_fee_aed"] == 100_000.0
            and ae_leak["compliance"]["code"] == "RESIDENCY_PIN_VIOLATION"
            and sg_for["compliance"]["absd_pct"] == 60.0,
            f"ae_fee={ae_ok['compliance']['dld_transfer_fee_aed']} sg_absd={sg_for['compliance']['absd_pct']}%",
        )

        # ------------------------------------------------------------------
        # 4. Phase 4.4 — Crypto-Agility & Hybrid Post-Quantum v2 Upgrade
        # ------------------------------------------------------------------
        _, ca_before, _ = api.handle(store, "GET", "/api/v1/security/crypto-agility", {}, None, hdr, "198.51.100.111", None)
        st_upg, upg_res, _ = api.handle(store, "POST", "/api/v1/security/crypto-agility/upgrade", {}, {}, hdr, "198.51.100.111", None)
        st_cnt, cnt_res, _ = api.handle(store, "GET", "/api/v1/contacts", {}, None, hdr, "198.51.100.111", None)
        sample_v2 = p4.seal_hybrid_v2("+919800112233", aad=tid)
        downgrade_blocked = False
        try:
            # Stripping `v2:hybrid-...:` prefix and attempting v1 unseal must fail integrity!
            sec.unseal(sample_v2.split(":", 2)[2], aad=tid)
        except ValueError:
            downgrade_blocked = True
        record(
            "phase4_hybrid_pq_v2_envelope_upgrade_and_downgrade_defence",
            ca_before["field_ciphertexts"]["v1_classical"] > 0
            and st_upg == 200
            and upg_res["post_upgrade"]["field_ciphertexts"]["v2_share_pct"] == 100.0
            and st_cnt == 200 and len(cnt_res["contacts"]) > 0
            and downgrade_blocked is True,
            f"upgraded_fields={upg_res['upgraded_fields']} v2_share={upg_res['post_upgrade']['field_ciphertexts']['v2_share_pct']}%",
        )

        # ------------------------------------------------------------------
        # 5. G5 Polish — Photo Cover Pinning / Reordering + Floor-Plan Slots
        # ------------------------------------------------------------------
        fake_rows = [
            {"object_key": "derived/properties/p1/100-a-card.webp", "variant": "card", "width": 960, "height": 640},
            {"object_key": "derived/properties/p1/200-b-card.webp", "variant": "card", "width": 960, "height": 640},
            {"object_key": "derived/properties/p1/300-c-card.webp", "variant": "card", "width": 960, "height": 640},
        ]
        reordered = md.gallery(fake_rows, photo_order=["200-b", "300-c", "100-a"], pinned_cover_id="100-a")
        prop0 = store.all("properties", tid)[0]
        api.handle(
            store, "PATCH", f"/api/v1/properties/{prop0['id']}/media-order", {},
            {"pinned_cover_id": "100-a", "photo_order": ["100-a", "200-b", "300-c"]},
            hdr, "198.51.100.111", None,
        )
        st_fp_bad, _, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop0['id']}/floor-plans", {},
            {"title": "Bad Tour", "walkthrough_url": "https://127.0.0.1:8080/tour"},
            hdr, "198.51.100.111", None,
        )
        st_fp_ok, fp_ok, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop0['id']}/floor-plans", {},
            {"title": "3 BHK East Vastu Plan", "carpet_sqft": 1240, "balcony_sqft": 110, "orientation": "East"},
            hdr, "198.51.100.111", None,
        )
        record(
            "g5_photo_cover_pinning_reordering_and_floor_plans",
            [p["photo_id"] for p in reordered] == ["100-a", "200-b", "300-c"]
            and reordered[0]["is_cover"] is True
            and st_fp_bad == 422
            and st_fp_ok == 200 and fp_ok["floor_plan"]["carpet_sqft"] == 1240,
            f"order={[p['photo_id'] for p in reordered]} ssrf_tour={st_fp_bad}",
        )

        # ------------------------------------------------------------------
        # 6. G7 Polish — Isolated Disposable `proof-mode` Sandbox Workspaces
        # ------------------------------------------------------------------
        st_pt, pt_res, _ = api.handle(
            store, "POST", "/api/v1/demo/proof-tenants", {},
            {"label": "CI Ephemeral Sandbox", "ttl_hours": 12},
            hdr, "198.51.100.111", None,
        )
        ptid = pt_res["proof_tenant"]["id"]
        sb_hdr = {"authorization": f"Bearer {pt_res['sandbox_access_token']}"}
        _, sb_contacts, _ = api.handle(store, "GET", "/api/v1/contacts", {}, None, sb_hdr, "198.51.100.111", None)
        st_refuse_real, _, _ = api.handle(
            store, "DELETE", f"/api/v1/demo/proof-tenants/{tid}", {}, None, hdr, "198.51.100.111", None,
        )
        st_destroy, d_res, _ = api.handle(
            store, "DELETE", f"/api/v1/demo/proof-tenants/{ptid}", {}, None, hdr, "198.51.100.111", None,
        )
        record(
            "g7_disposable_proof_workspace_isolation_and_teardown",
            st_pt == 200
            and len(sb_contacts["contacts"]) == 1  # isolated from sunrise's 100 contacts!
            and st_refuse_real == 403
            and st_destroy == 200 and d_res["removed_rows"] >= 5,
            f"sandbox_contacts={len(sb_contacts['contacts'])} real_refused={st_refuse_real} removed={d_res['removed_rows']}",
        )

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nPROOF SUMMARY: {passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

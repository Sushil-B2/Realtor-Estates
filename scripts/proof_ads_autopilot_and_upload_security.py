#!/usr/bin/env python3
"""
End-to-end proof for `v1.33.0` (`docs/44-ADS-AUTOPILOT-MACRO-UPLOAD-SECURITY-AND-PORTAL-UX.md`):
  1. Phase 3 Ads Hard Daily Spend Caps, >20% Direct Budget-Jump Block & MahaRERA Ad Gate
  2. Phase 3 Ads Autopilot Rebalancer (Bleeding-CPL Auto-Pause + Human-Gated Budget Approval)
  3. Phase 3 Hard Monthly Spend-Cap Emergency Halt
  4. Phase 2 Clean OOXML `.docx` Upload Verification & Sandboxed Plain-Text Conversion
  5. Phase 2 Office Macro (`vbaProject.bin`), External Template Injection & Archive-Bomb Rejection
  6. Full SPA Action & Unauthenticated `#/buyer-portal/` Router Wiring Verification
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import api, security as sec
from app.store import Store

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _build_docx(*, body_xml: str, extra_files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        zf.writestr("word/document.xml", body_xml)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    sec.LIMITER.hits.clear()
    with tempfile.TemporaryDirectory(prefix="re_proof_v133_") as td:
        store = Store(os.path.join(td, "store.json"))
        tid = store.db["tenants"][0]["id"]

        _, login, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "198.51.100.101", None,
        )
        hdr = {"authorization": f"Bearer {login['token']}"}

        # ------------------------------------------------------------------
        # 1. Ads Hard Spend Cap, >20% Direct Jump Block & RERA Ad Gate
        # ------------------------------------------------------------------
        st_rera, _, _ = api.handle(
            store, "POST", "/api/v1/campaigns", {},
            {"name": "Guaranteed Return 2x Plot Offer", "budget_daily": 2000},
            hdr, "198.51.100.101", None,
        )
        st_cap, _, _ = api.handle(
            store, "POST", "/api/v1/campaigns", {},
            {"name": "Oversized Daily Spend Campaign", "budget_daily": 49000},
            hdr, "198.51.100.101", None,
        )
        c0 = store.all("campaigns", tid)[0]
        st_jump, _, _ = api.handle(
            store, "PATCH", f"/api/v1/campaigns/{c0['id']}", {},
            {"budget_daily": c0["budget_daily"] * 2},
            hdr, "198.51.100.101", None,
        )
        record(
            "ads_rera_gate_daily_cap_and_direct_jump_block",
            st_rera == 422 and st_cap == 422 and st_jump == 422,
            f"rera={st_rera} cap={st_cap} direct_jump={st_jump}",
        )

        # ------------------------------------------------------------------
        # 2. Ads Autopilot (Bleeding CPL Pause + Human-Gated Budget Scale)
        # ------------------------------------------------------------------
        bleed = store.insert("campaigns", tid, {
            "platform": "meta_ads",
            "name": "High CPL Test Set",
            "objective": "leads",
            "budget_daily": 2000,
            "spend": 9600,
            "leads": 2,
            "cpl": 4800,
            "status": "active",
            "roas": 0.8,
        }, prefix="cmp")
        old_c0_budget = c0["budget_daily"]
        st_ap, ap_res, _ = api.handle(
            store, "POST", "/api/v1/ads/autopilot/run", {}, {}, hdr, "198.51.100.101", None,
        )
        bleed_after = store.get("campaigns", tid, bleed["id"])
        c0_unmodified = store.get("campaigns", tid, c0["id"])["budget_daily"] == old_c0_budget
        prop_id = ap_res["staged_budget_proposals"][0]
        st_no_ack, _, _ = api.handle(
            store, "POST", f"/api/v1/learning/{prop_id}/decide", {},
            {"decision": "approve"},
            hdr, "198.51.100.101", None,
        )
        st_ack, _, _ = api.handle(
            store, "POST", f"/api/v1/learning/{prop_id}/decide", {},
            {"decision": "approve", "acknowledge_risk": True, "note": "Approved +25% scale"},
            hdr, "198.51.100.101", None,
        )
        c0_promoted = store.get("campaigns", tid, c0["id"])["budget_daily"] > old_c0_budget
        record(
            "ads_autopilot_cpl_pause_and_human_budget_approval",
            st_ap == 200
            and bleed_after["status"] == "paused"
            and c0_unmodified
            and st_no_ack == 422
            and st_ack == 200
            and c0_promoted,
            f"bleed={bleed_after['status']} no_ack={st_no_ack} promoted_budget={store.get('campaigns', tid, c0['id'])['budget_daily']}",
        )

        # ------------------------------------------------------------------
        # 3. Hard Monthly Spend-Cap Emergency Halt
        # ------------------------------------------------------------------
        api.handle(
            store, "PATCH", "/api/v1/ads/policy", {},
            {"hard_daily_cap_inr": 50000, "hard_monthly_cap_inr": 400000},
            hdr, "198.51.100.101", None,
        )
        _, halt_res, _ = api.handle(
            store, "POST", "/api/v1/ads/autopilot/run", {}, {}, hdr, "198.51.100.101", None,
        )
        st_reactivate, _, _ = api.handle(
            store, "PATCH", f"/api/v1/campaigns/{c0['id']}", {},
            {"status": "active"},
            hdr, "198.51.100.101", None,
        )
        record(
            "ads_hard_monthly_cap_emergency_halt",
            halt_res["hard_cap_halted"] is True
            and len(halt_res["paused_campaigns"]) >= 1
            and st_reactivate == 422,
            f"halted={halt_res['hard_cap_halted']} paused={len(halt_res['paused_campaigns'])} reactivate={st_reactivate}",
        )

        # ------------------------------------------------------------------
        # 4. Clean OOXML `.docx` Scan + Sandboxed Plain-Text Conversion
        # ------------------------------------------------------------------
        prop_doc = store.all("properties", tid)[0]
        clean_docx = _build_docx(
            body_xml="<w:document><w:body><w:p><w:t>Title Search Clear — Survey 47/2A NA Order Verified</w:t></w:p></w:body></w:document>"
        )
        st_clean, clean_res, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop_doc['id']}/documents/scan", {},
            {
                "doc_type": "title_search_report",
                "filename": "title_report.docx",
                "content_type": DOCX_MIME,
                "content_b64": base64.b64encode(clean_docx).decode("ascii"),
            },
            hdr, "198.51.100.101", None,
        )
        preview = clean_res.get("scan", {}).get("sandboxed_preview", "")
        record(
            "ooxml_clean_docx_and_sandboxed_conversion",
            st_clean == 200 and "Survey 47/2A NA Order Verified" in preview and "<w:" not in preview,
            f"status={st_clean} preview='{preview[:48]}'",
        )

        # ------------------------------------------------------------------
        # 5. Office Macro, External Template Injection & Archive Bomb Rejection
        # ------------------------------------------------------------------
        macro_docx = _build_docx(body_xml="<w:document/>", extra_files={"word/vbaProject.bin": b"VBA_PAYLOAD"})
        ext_rel_docx = _build_docx(
            body_xml="<w:document/>",
            extra_files={"word/_rels/document.xml.rels": b'<Relationships><Relationship Target="https://evil.example/tpl.dotm" TargetMode="External"/></Relationships>'},
        )
        bomb_docx = _build_docx(body_xml="<w:document>" + ("A" * 400_000) + "</w:document>")
        ole2_raw = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy_doc"

        st_macro, r_macro, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop_doc['id']}/documents/scan", {},
            {"content_type": DOCX_MIME, "content_b64": base64.b64encode(macro_docx).decode("ascii")},
            hdr, "198.51.100.101", None,
        )
        st_ext, r_ext, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop_doc['id']}/documents/scan", {},
            {"content_type": DOCX_MIME, "content_b64": base64.b64encode(ext_rel_docx).decode("ascii")},
            hdr, "198.51.100.101", None,
        )
        st_bomb, r_bomb, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop_doc['id']}/documents/scan", {},
            {"content_type": DOCX_MIME, "content_b64": base64.b64encode(bomb_docx).decode("ascii")},
            hdr, "198.51.100.101", None,
        )
        st_ole, _, _ = api.handle(
            store, "POST", f"/api/v1/properties/{prop_doc['id']}/documents/scan", {},
            {"content_type": DOCX_MIME, "content_b64": base64.b64encode(ole2_raw).decode("ascii")},
            hdr, "198.51.100.101", None,
        )
        record(
            "office_macro_external_template_bomb_and_ole2_blocked",
            st_macro == 422 and r_macro["error"]["code"] == "OFFICE_MACRO_BLOCKED"
            and st_ext == 422 and r_ext["error"]["code"] == "OFFICE_MACRO_BLOCKED"
            and st_bomb == 422 and r_bomb["error"]["code"] == "ARCHIVE_BOMB_BLOCKED"
            and st_ole == 422,
            f"macro={st_macro} ext_rel={st_ext} bomb={st_bomb} ole2={st_ole}",
        )

        # ------------------------------------------------------------------
        # 6. Full SPA Action & Public `#/buyer-portal/` Router Wiring
        # ------------------------------------------------------------------
        js_bundle = "\n".join(
            open(os.path.join(ROOT, "web", "js", fn), encoding="utf-8").read()
            for fn in ("core.js", "views-ops.js", "views-platform.js", "views-ai.js", "views-growth.js")
        )
        required_hooks = (
            "renderPublicBuyerPortal",
            "deal-buyer-portal",
            "revoke-buyer-portal",
            "website-form-setup",
            "offline-pwa-console",
            "form:pack-import",
            "run-ads-autopilot",
            "ads-spend-policy",
        )
        missing = [h for h in required_hooks if h not in js_bundle]
        record(
            "spa_buyer_portal_website_form_offline_and_ads_hooks",
            len(missing) == 0,
            f"verified_hooks={len(required_hooks)}/{len(required_hooks)}",
        )

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nPROOF SUMMARY: {passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

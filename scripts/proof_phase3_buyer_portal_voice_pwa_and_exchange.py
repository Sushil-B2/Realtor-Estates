#!/usr/bin/env python3
"""
End-to-end proof for `v1.32.0` (`docs/43-BUYER-PORTAL-INBOUND-VOICE-OFFLINE-PWA-EXCHANGE-AND-DEPLOY.md`):
  1. Option A: FEMA (Non-Debt Instruments) Rules 2019 Eligibility Engine
  2. Option A: Buyer & NRI Self-Serve Portal Pass (`bp1_...`) + Instant Revocation
  3. Option B: Inbound AI Voice Receptionist (`mr`/`hi`/`en`) + Diarised Sentiment
     Trajectory & Human Escalation + HMAC Webhook Verification
  4. Option C: Web Push Subscription SSRF Guard + Idempotent Field-Agent Offline Sync
  5. Option D: Cryptographically Signed Skill & Funnel Pack Export, Tamper Rejection
     & Cross-Workspace Human-Gated Import
  6. Option E: External Website Form Lead Ingest (`POST /api/v1/ingest/website`) &
     Turnkey Deployment Artefact Verification (`Dockerfile`, `deploy/`)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import api, config as cfg, security as sec, sessions as sess
from app.store import Store


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    sec.LIMITER.hits.clear()
    with tempfile.TemporaryDirectory(prefix="re_proof_v132_") as td:
        store = Store(os.path.join(td, "store.json"))
        tid_a = store.db["tenants"][0]["id"]
        tid_b = store.db["tenants"][1]["id"]

        _, login_a, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "198.51.100.91", None,
        )
        hdr_a = {"authorization": f"Bearer {login_a['token']}"}

        b_owner = next(u for u in store.db["users"] if u.get("tenant_id") == tid_b and u.get("role") == "owner")
        s_row, _ = sess.open_session(store, tid_b, b_owner["id"], ip="198.51.100.92", ua="proof-v132")
        tok_b = sec.issue_token({"sub": b_owner["email"], "uid": b_owner["id"], "tid": tid_b, "role": "owner", "sid": s_row["id"]})
        hdr_b = {"authorization": f"Bearer {tok_b}"}

        # ------------------------------------------------------------------
        # 1. Option A — FEMA Rule 24 Eligibility Matrix
        # ------------------------------------------------------------------
        _, f_agri, _ = api.handle(
            store, "POST", "/api/v1/fema/check", {},
            {"buyer_residency": "nri", "property_category": "agricultural", "acquisition_mode": "purchase"},
            hdr_a, "198.51.100.91", None,
        )
        _, f_inh, _ = api.handle(
            store, "POST", "/api/v1/fema/check", {},
            {"buyer_residency": "oci", "property_category": "farmhouse", "acquisition_mode": "inheritance"},
            hdr_a, "198.51.100.91", None,
        )
        _, f_res, _ = api.handle(
            store, "POST", "/api/v1/fema/check", {},
            {"buyer_residency": "nri", "property_category": "residential", "existing_repatriable_residential_count": 2},
            hdr_a, "198.51.100.91", None,
        )
        _, f_fn, _ = api.handle(
            store, "POST", "/api/v1/fema/check", {},
            {"buyer_residency": "foreign_citizen_non_oci", "property_category": "residential"},
            hdr_a, "198.51.100.91", None,
        )
        record(
            "option_a_fema_rule24_eligibility_matrix",
            f_agri["fema"]["status"] == "PROHIBITED"
            and f_inh["fema"]["status"] == "ELIGIBLE_INHERITANCE_ONLY"
            and f_res["fema"]["status"] == "ELIGIBLE"
            and f_res["fema"]["repatriation_cap_reached"] is True
            and f_fn["fema"]["status"] == "RBI_APPROVAL_REQUIRED",
            f"agri={f_agri['fema']['status']} res_cap={f_res['fema']['repatriation_cap_reached']}",
        )

        # ------------------------------------------------------------------
        # 2. Option A — Buyer & NRI Self-Serve Portal Pass + Revocation
        # ------------------------------------------------------------------
        deal = store.all("deals", tid_a)[0]
        st_bp, bp_res, _ = api.handle(
            store, "POST", f"/api/v1/deals/{deal['id']}/buyer-portal", {},
            {"buyer_residency": "nri", "ttl_days": 30},
            hdr_a, "198.51.100.91", None,
        )
        bp_token = bp_res["token"]
        raw_not_stored = bp_token not in json.dumps(store.get("deals", tid_a, deal["id"]))
        st_view, view_res, _ = api.handle(
            store, "GET", f"/api/v1/public/buyer-portal/{bp_token}", {}, None, {}, "198.51.100.91", None,
        )
        st_tamper, _, _ = api.handle(
            store, "GET", f"/api/v1/public/buyer-portal/{bp_token[:-2]}ZZ", {}, None, {}, "198.51.100.91", None,
        )
        api.handle(store, "DELETE", f"/api/v1/deals/{deal['id']}/buyer-portal", {}, None, hdr_a, "198.51.100.91", None)
        st_revoked, _, _ = api.handle(
            store, "GET", f"/api/v1/public/buyer-portal/{bp_token}", {}, None, {}, "198.51.100.91", None,
        )
        record(
            "option_a_buyer_portal_pass_and_revocation",
            st_bp == 200
            and raw_not_stored
            and st_view == 200
            and view_res["portal"]["deal"]["id"] == deal["id"]
            and st_tamper == 401
            and st_revoked == 401,
            f"view={st_view} tamper={st_tamper} revoked={st_revoked}",
        )

        # ------------------------------------------------------------------
        # 3. Option B — Inbound Voice Receptionist + Diarised Sentiment + Escalation
        # ------------------------------------------------------------------
        st_v1, v1_res, _ = api.handle(
            store, "POST", "/api/v1/voice/inbound", {},
            {"caller_name": "Prashant More", "phone": "+919822334455", "language": "mr",
             "utterance": "Plot 47 साठी शनिवारी site visit ठेवायची आहे."},
            hdr_a, "198.51.100.91", None,
        )
        st_v2, v2_res, _ = api.handle(
            store, "POST", "/api/v1/voice/inbound", {},
            {"caller_name": "Upset Buyer", "phone": "+919822334466", "language": "en",
             "turns": [{"speaker": "caller", "text": "Terrible delay! Connect me to a human manager right now!"}]},
            hdr_a, "198.51.100.91", None,
        )
        os.environ["VOICE_WEBHOOK_SECRET"] = "voice_wh_sec_v132"
        cfg._LOADED = False
        wh_body = {"tenant_id": tid_a, "caller_name": "Webhook Caller", "phone": "+919822334477", "language": "hi"}
        raw_wh = json.dumps(wh_body).encode("utf-8")
        st_wh_bad, _, _ = api.handle(
            store, "POST", "/api/v1/webhooks/voice-inbound", {}, wh_body,
            {"x-voice-signature": "bad"}, "198.51.100.91", None, raw_wh,
        )
        good_wh_sig = hmac.new(b"voice_wh_sec_v132", raw_wh, hashlib.sha256).hexdigest()
        st_wh_ok, _, _ = api.handle(
            store, "POST", "/api/v1/webhooks/voice-inbound", {}, wh_body,
            {"x-voice-signature": good_wh_sig}, "198.51.100.91", None, raw_wh,
        )
        record(
            "option_b_inbound_voice_diarisation_sentiment_and_webhook",
            st_v1 == 200
            and v1_res["activity"]["ai_disclosed"] is True
            and v1_res["sentiment_trajectory"]["escalated_to_human"] is False
            and st_v2 == 200
            and v2_res["sentiment_trajectory"]["escalated_to_human"] is True
            and v2_res["task"]["priority"] == "high"
            and st_wh_bad == 401
            and st_wh_ok == 200,
            f"escalated={v2_res['sentiment_trajectory']['escalated_to_human']} wh_bad={st_wh_bad} wh_ok={st_wh_ok}",
        )

        # ------------------------------------------------------------------
        # 4. Option C — Web Push SSRF Guard & Idempotent Offline Sync
        # ------------------------------------------------------------------
        st_p_bad, _, _ = api.handle(
            store, "POST", "/api/v1/push/subscribe", {},
            {"endpoint": "https://127.0.0.1:9000/push", "keys": {"p256dh": "x", "auth": "y"}},
            hdr_a, "198.51.100.91", None,
        )
        st_p_ok, p_ok_res, _ = api.handle(
            store, "POST", "/api/v1/push/subscribe", {},
            {"endpoint": "https://fcm.googleapis.com/fcm/send/sub_demo_1", "keys": {"p256dh": "k1", "auth": "k2"}},
            hdr_a, "198.51.100.91", None,
        )
        c_any = store.all("contacts", tid_a)[0]
        t_any = store.all("tasks", tid_a)[0]
        batch = [
            {"client_mutation_id": "mut_field_01", "kind": "note.add", "contact_id": c_any["id"], "text": "Visited plot boundary stone — clear access road."},
            {"client_mutation_id": "mut_field_02", "kind": "task.complete", "task_id": t_any["id"]},
        ]
        st_s1, s1_res, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, {"mutations": batch}, hdr_a, "198.51.100.91", None)
        st_s2, s2_res, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, {"mutations": batch}, hdr_a, "198.51.100.91", None)
        record(
            "option_c_push_ssrf_guard_and_idempotent_offline_sync",
            st_p_bad == 422
            and st_p_ok == 200
            and st_s1 == 200 and s1_res["applied"] == 2
            and st_s2 == 200 and s2_res["applied"] == 0 and s2_res["deduplicated"] == 2,
            f"push_loopback={st_p_bad} first_applied={s1_res['applied']} retry_dedup={s2_res['deduplicated']}",
        )

        # ------------------------------------------------------------------
        # 5. Option D — Signed Skill & Funnel Pack Export + Cross-Tenant Import
        # ------------------------------------------------------------------
        fn_a = store.all("funnels", tid_a)[0]
        ag_a = store.all("agents", tid_a)[0]
        st_ex, ex_res, _ = api.handle(
            store, "POST", "/api/v1/exchange/export", {},
            {"name": "Marathwada Plot Pack", "funnel_ids": [fn_a["id"]], "agent_ids": [ag_a["id"]]},
            hdr_a, "198.51.100.91", None,
        )
        pack = ex_res["pack"]
        tampered = {**pack, "name": "Tampered Pack"}
        st_imp_bad, _, _ = api.handle(
            store, "POST", "/api/v1/exchange/import", {}, {"pack": tampered}, hdr_b, "198.51.100.91", None,
        )
        st_imp_ok, imp_ok, _ = api.handle(
            store, "POST", "/api/v1/exchange/import", {}, {"pack": pack}, hdr_b, "198.51.100.91", None,
        )
        imp_funnel = store.get("funnels", tid_b, imp_ok["imported_funnels"][0])
        record(
            "option_d_signed_pack_exchange_and_tamper_rejection",
            st_ex == 200
            and st_imp_bad == 422
            and st_imp_ok == 200
            and imp_funnel["published"] is False
            and len(imp_ok["staged_skill_proposals"]) >= 1,
            f"tamper={st_imp_bad} funnels={len(imp_ok['imported_funnels'])} staged_skills={len(imp_ok['staged_skill_proposals'])}",
        )

        # ------------------------------------------------------------------
        # 6. Option E — External Website Form Lead Ingest + Deployment Kit
        # ------------------------------------------------------------------
        st_ik, ik_res, _ = api.handle(
            store, "POST", "/api/v1/tenant/ingest-keys", {},
            {"label": "External Website Contact Form", "source": "website"},
            hdr_a, "198.51.100.91", None,
        )
        rink_tok = ik_res["token"]
        st_ing, ing_res, _ = api.handle(
            store, "POST", "/api/v1/ingest/website", {},
            {
                "name": "Website Visitor Patil",
                "phone": "+91 98237 78899",
                "email": "patil@website.in",
                "city": "Nanded",
                "budget": "65 Lakh",
                "message": "Enquiring from external website form",
                "utm_campaign": "hero-form",
            },
            {"x-ingest-key": rink_tok}, "198.51.100.91", None,
        )
        deploy_files = [
            os.path.join(ROOT, "Dockerfile"),
            os.path.join(ROOT, "deploy", "Caddyfile.example"),
            os.path.join(ROOT, "deploy", "realtor-estates.service"),
            os.path.join(ROOT, "deploy", "vercel.json.example"),
            os.path.join(ROOT, "deploy", "netlify.toml.example"),
            os.path.join(ROOT, "deploy", "website-form-connectors", "nextjs-submit-lead.mjs"),
            os.path.join(ROOT, "deploy", "website-form-connectors", "php-submit-lead.php"),
        ]
        record(
            "option_e_website_form_ingest_and_deploy_kit",
            st_ik in (200, 201)
            and st_ing == 200
            and ing_res["ok"] is True
            and ing_res["duplicate"] is False
            and all(os.path.isfile(p) for p in deploy_files),
            f"contact={ing_res.get('contact_id')} deploy_files={len(deploy_files)}/7",
        )

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nPROOF SUMMARY: {passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

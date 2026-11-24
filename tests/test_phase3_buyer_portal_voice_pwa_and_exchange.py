"""
Tests for `v1.32.0` (`docs/43-BUYER-PORTAL-INBOUND-VOICE-OFFLINE-PWA-EXCHANGE-AND-DEPLOY.md`):
  - Option A: FEMA Rule 24 Eligibility + Buyer/NRI Self-Serve Portal Pass & Revocation
  - Option B: Inbound AI Voice Receptionist + Diarised Sentiment Trajectory & Escalation
  - Option C: Web Push SSRF Guard + Idempotent Field-Agent Offline Sync
  - Option D: Signed Skill & Funnel Pack Export / Tamper Rejection / Cross-Tenant Import
  - Option E: External Website Form Lead Ingest (`X-Ingest-Key`) & Deploy Kit Presence
"""
from __future__ import annotations

import os
import pytest

from app import api, neon, security as sec, sessions as sess
from app.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def v132_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid_a = store.db["tenants"][0]["id"]
    tid_b = store.db["tenants"][1]["id"]
    _, login_a, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "198.51.100.95", None,
    )
    b_owner = next(u for u in store.db["users"] if u.get("tenant_id") == tid_b and u.get("role") == "owner")
    s_row, _ = sess.open_session(store, tid_b, b_owner["id"], ip="198.51.100.96", ua="pytest-v132")
    tok_b = sec.issue_token({"sub": b_owner["email"], "uid": b_owner["id"], "tid": tid_b, "role": "owner", "sid": s_row["id"]})
    return store, tid_a, tid_b, {"authorization": f"Bearer {login_a['token']}"}, {"authorization": f"Bearer {tok_b}"}


def test_fema_eligibility_and_buyer_portal_pass(v132_ctx):
    store, tid_a, _, hdr_a, _ = v132_ctx
    _, f_proh, _ = api.handle(
        store, "POST", "/api/v1/fema/check", {},
        {"buyer_residency": "nri", "property_category": "agricultural"},
        hdr_a, "198.51.100.95", None,
    )
    assert f_proh["fema"]["status"] == "PROHIBITED"

    deal = store.all("deals", tid_a)[0]
    st_bp, bp, _ = api.handle(
        store, "POST", f"/api/v1/deals/{deal['id']}/buyer-portal", {},
        {"buyer_residency": "nri", "ttl_days": 15},
        hdr_a, "198.51.100.95", None,
    )
    assert st_bp == 200
    st_get, view, _ = api.handle(store, "GET", f"/api/v1/public/buyer-portal/{bp['token']}", {}, None, {}, "198.51.100.95", None)
    assert st_get == 200 and view["portal"]["deal"]["id"] == deal["id"]

    api.handle(store, "DELETE", f"/api/v1/deals/{deal['id']}/buyer-portal", {}, None, hdr_a, "198.51.100.95", None)
    assert api.handle(store, "GET", f"/api/v1/public/buyer-portal/{bp['token']}", {}, None, {}, "198.51.100.95", None)[0] == 401


def test_inbound_voice_receptionist_and_offline_pwa_sync(v132_ctx):
    store, tid_a, _, hdr_a, _ = v132_ctx
    st_v, v_res, _ = api.handle(
        store, "POST", "/api/v1/voice/inbound", {},
        {"caller_name": "Escalated Caller", "phone": "+919822667788", "language": "en",
         "turns": [{"speaker": "caller", "text": "Angry about delay, connect me to human manager!"}]},
        hdr_a, "198.51.100.95", None,
    )
    assert st_v == 200
    assert v_res["sentiment_trajectory"]["escalated_to_human"] is True
    assert v_res["task"]["priority"] == "high"

    # Push SSRF check + Offline idempotent sync
    assert api.handle(store, "POST", "/api/v1/push/subscribe", {}, {"endpoint": "https://127.0.0.1/p"}, hdr_a, "198.51.100.95", None)[0] == 422
    assert api.handle(store, "POST", "/api/v1/push/subscribe", {}, {"endpoint": "https://fcm.googleapis.com/fcm/send/abc"}, hdr_a, "198.51.100.95", None)[0] == 200

    c = store.all("contacts", tid_a)[0]
    muts = [{"client_mutation_id": "m_sync_1", "kind": "note.add", "contact_id": c["id"], "text": "Field visit note"}]
    _, s1, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, {"mutations": muts}, hdr_a, "198.51.100.95", None)
    _, s2, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, {"mutations": muts}, hdr_a, "198.51.100.95", None)
    assert s1["applied"] == 1 and s2["applied"] == 0 and s2["deduplicated"] == 1


def test_signed_pack_exchange_and_website_form_ingest(v132_ctx):
    store, tid_a, tid_b, hdr_a, hdr_b = v132_ctx
    fn_a = store.all("funnels", tid_a)[0]
    ag_a = store.all("agents", tid_a)[0]
    _, ex, _ = api.handle(
        store, "POST", "/api/v1/exchange/export", {},
        {"name": "Verified Pack", "funnel_ids": [fn_a["id"]], "agent_ids": [ag_a["id"]]},
        hdr_a, "198.51.100.95", None,
    )
    pack = ex["pack"]
    assert api.handle(store, "POST", "/api/v1/exchange/import", {}, {"pack": {**pack, "name": "Evil"}}, hdr_b, "198.51.100.95", None)[0] == 422
    st_imp, imp, _ = api.handle(store, "POST", "/api/v1/exchange/import", {}, {"pack": pack}, hdr_b, "198.51.100.95", None)
    assert st_imp == 200 and len(imp["imported_funnels"]) == 1

    _, ik, _ = api.handle(store, "POST", "/api/v1/tenant/ingest-keys", {}, {"label": "Site Form", "source": "website"}, hdr_a, "198.51.100.95", None)
    st_in, in_res, _ = api.handle(
        store, "POST", "/api/v1/ingest/website", {},
        {"name": "Form Lead", "phone": "9823112233", "budget": "45 Lakh"},
        {"x-ingest-key": ik["token"]}, "198.51.100.95", None,
    )
    assert st_in == 200 and in_res["ok"] is True
    assert os.path.isfile(os.path.join(ROOT, "Dockerfile"))

"""
Tests for `v1.31.0` (`docs/42-FINANCE-CONSENT-MANAGER-AND-A2A.md`):
  - G1 Multi-day `wait` step & `seven-day-drip-nurture` playbook
  - G2 Token refund + unit release, §34 GST Credit Note, Form 16A settlement & GSTR-1 export
  - Phase 2 DPDP §6(7)–(9) Registered Consent Manager webhook & directive bridge
  - Phase 3 Multi-Agent A2A Specialist Delegation with `a2a1_...` attenuated per-hop tokens
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json

import pytest

from app import api, automations as autm, config as cfg, neon, security as sec
from app.store import Store


@pytest.fixture()
def v131_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    tenant = store.get("tenants", tid, tid)
    st = dict(tenant.get("settings") or {})
    st["gstin"] = "27AABCS1429B1Z1"
    st["state"] = "Maharashtra"
    store.update("tenants", tid, tid, {"settings": st})
    _, login_a, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "198.51.100.85", None,
    )
    return store, tid, {"authorization": f"Bearer {login_a['token']}"}


def test_multiday_wait_step_and_7day_drip_playbook(v131_ctx):
    store, tid, hdr = v131_ctx
    tenant = store.get("tenants", tid, tid)
    ist = datetime.timezone(datetime.timedelta(minutes=330))
    t0 = int(datetime.datetime(2026, 11, 18, 11, 0, 0, tzinfo=ist).timestamp())
    for a in store.all("automations", tid):
        store.update("automations", tid, a["id"], {"enabled": False})

    st_pb, pb_res, _ = api.handle(
        store, "POST", "/api/v1/automations/playbooks/seven-day-drip-nurture/install",
        {}, {}, hdr, "198.51.100.85", None,
    )
    assert st_pb == 201 or st_pb == 200
    drip_id = pb_res["automation"]["id"]

    c = store.insert(
        "contacts", tid,
        {
            "name": "Meenal Kulkarni",
            "phone_enc": sec.seal("+919823451234", aad=tid),
            "phone_hash": sec.blind_index("+919823451234"),
            "email_enc": sec.seal("", aad=tid),
            "email_hash": "",
            "requirement": "2BHK flat",
            "stage": "new",
            "created_at": t0 - 10,
            "consent": {"status": "granted", "purpose": "enquiry", "channel": "whatsapp", "captured_at": t0 - 10},
        },
        prefix="cnt",
    )
    eng = autm.Runtime(store, now_fn=lambda: t0)
    eng.plan(tid, at=t0, automation_id=drip_id)
    r0 = eng.advance(eng.due(tid, at=t0)[0], tenant, at=t0)
    assert r0["step_index"] == 2
    assert r0["status"] == "pending"
    assert eng.due(tid, at=t0 + 86400) == []

    t2 = int(r0["next_at"]) + 60
    r2 = eng.advance(eng.due(tid, at=t2)[0], tenant, at=t2)
    assert r2["step_index"] == 4
    t7 = int(r2["next_at"]) + 60
    r7 = eng.advance(eng.due(tid, at=t7)[0], tenant, at=t7)
    assert r7["status"] == "ok"


def test_refunds_credit_notes_collection_and_gstr1(v131_ctx, monkeypatch):
    store, tid, hdr = v131_ctx
    monkeypatch.setenv("PAYMENTS_WEBHOOK_SECRET", "pay_sec_test_v131")
    cfg._LOADED = False

    unit = next(u for u in store.all("units", tid) if u.get("status") == "available")
    tok_rupees = int(unit.get("token_amount") or 50_000)
    tok_paise = tok_rupees * 100
    buyer = store.insert(
        "contacts", tid,
        {
            "name": "Deccan Developers LLP",
            "phone_enc": sec.seal("+919823459999", aad=tid),
            "phone_hash": sec.blind_index("+919823459999"),
            "email_enc": sec.seal("", aad=tid),
            "email_hash": "",
            "gstin": "27AABCD1234E1Z9",
            "state": "Maharashtra",
            "is_business": True,
            "stage": "token",
            "consent": {"status": "granted", "purpose": "deal", "channel": "whatsapp"},
        },
        prefix="cnt",
    )
    deal = store.insert(
        "deals", tid,
        {"title": "Deccan Plot Deal", "contact_id": buyer["id"], "unit_id": unit["id"], "stage": "token", "value": 4_000_000, "token_amount": tok_rupees},
        prefix="dl",
    )
    evt = {
        "event": "payment.captured",
        "event_id": "evt_v131_t1",
        "payload": {"payment": {"entity": {
            "id": "pay_v131_t1", "amount": tok_paise, "currency": "INR", "method": "upi",
            "notes": {"tenant_id": tid, "deal_id": deal["id"], "unit_id": unit["id"], "contact_id": buyer["id"], "kind": "token"},
        }}},
    }
    raw = json.dumps(evt).encode()
    sig = hmac.new(b"pay_sec_test_v131", raw, hashlib.sha256).hexdigest()
    st_w, w_res, _ = api.handle(store, "POST", "/api/v1/webhooks/payments", {}, evt, {"x-razorpay-signature": sig}, "198.51.100.85", None, raw)
    assert st_w == 200
    pid = w_res["payment"]["id"]
    rcpt = next(i for i in store.all("invoices", tid) if i.get("payment_id") == pid)

    # Credit note on custody receipt refused
    st_bad, _, _ = api.handle(store, "POST", f"/api/v1/invoices/{rcpt['id']}/credit-note", {}, {}, hdr, "198.51.100.85", None)
    assert st_bad == 422

    # Refund releases unit
    st_rf, rf_res, _ = api.handle(store, "POST", f"/api/v1/payments/{pid}/refund", {}, {"reason": "Cancel"}, hdr, "198.51.100.85", None)
    assert st_rf == 200 and rf_res["unit_released"] is True
    assert store.get("units", tid, unit["id"])["status"] == "available"

    # Commission invoice -> partial credit note -> Form 16A settlement -> GSTR-1
    _, inv_r, _ = api.handle(store, "POST", f"/api/v1/deals/{deal['id']}/invoice", {}, {"rate_pct": 2.0}, hdr, "198.51.100.85", None)
    inv = inv_r["invoice"]
    st_cn, cn_r, _ = api.handle(store, "POST", f"/api/v1/invoices/{inv['id']}/credit-note", {}, {"taxable_paise": 1_000_000}, hdr, "198.51.100.85", None)
    assert st_cn == 200
    rem = (int(inv["total_paise"]) - int(cn_r["credit_note"]["total_paise"])) - int(inv["tds"]["withheld_paise"])
    st_col, col_r, _ = api.handle(
        store, "POST", f"/api/v1/invoices/{inv['id']}/collect", {},
        {"amount_paise": rem, "utr": "UTR9988", "tds_certificate_no": "16A-7788"},
        hdr, "198.51.100.85", None,
    )
    assert st_col == 200 and col_r["invoice"]["status"] == "paid"

    st_g, g_res, _ = api.handle(store, "GET", "/api/v1/money/gstr1", {"period": ["all"], "format": ["csv"]}, None, hdr, "198.51.100.85", None)
    assert st_g == 200
    assert len(g_res["gstr1"]["b2b"]) >= 1
    assert len(g_res["gstr1"]["cdnr"]) == 1
    assert g_res["gstr1"]["custody_excluded"]["included_in_taxable_turnover"] is False


def test_consent_manager_webhook_and_a2a_delegation(v131_ctx, monkeypatch):
    store, tid, hdr = v131_ctx
    monkeypatch.setenv("CONSENT_MANAGER_WEBHOOK_SECRET", "cm_sec_v131_live")
    cfg._LOADED = False

    contact = store.all("contacts", tid)[0]
    cm_body = {
        "tenant_id": tid,
        "directive_id": "cm_dir_101",
        "consent_manager_id": "meity-cm.in",
        "action": "grant",
        "contact_id": contact["id"],
        "purpose": "site_visit_updates",
    }
    raw = json.dumps(cm_body).encode()
    assert api.handle(store, "POST", "/api/v1/webhooks/consent-manager", {}, cm_body, {"x-consent-manager-signature": "bad"}, "198.51.100.85", None, raw)[0] == 401
    good_sig = hmac.new(b"cm_sec_v131_live", raw, hashlib.sha256).hexdigest()
    st_ok, cm_res, _ = api.handle(store, "POST", "/api/v1/webhooks/consent-manager", {}, cm_body, {"x-consent-manager-signature": good_sig}, "198.51.100.85", None, raw)
    assert st_ok == 200
    assert cm_res["acknowledgement"]["signature"].startswith("sig1_")

    agent = next(a for a in store.all("agents", tid) if "knowledge:read" in (a.get("scopes") or []))
    st_del, del_res, _ = api.handle(
        store, "POST", f"/api/v1/agents/{agent['id']}/delegate", {},
        {
            "specialist": "title_search",
            "task": "Verify Plot 14 MahaRERA: P51100048291",
            "requested_scopes": ["knowledge:read", "messages:send", "billing:write"],
        },
        hdr, "198.51.100.85", None,
    )
    assert st_del == 200
    assert del_res["hop_claims"]["scopes"] == ["knowledge:read"]
    assert api.handle(
        store, "POST", f"/api/v1/agents/{agent['id']}/delegate", {},
        {"specialist": "title_search", "task": "Plot 14", "hop_depth": 3},
        hdr, "198.51.100.85", None,
    )[0] == 422

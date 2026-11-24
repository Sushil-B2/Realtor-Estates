"""
Tests for `v1.29.0` Sprint S3 Compliance Launch Pack, Sprint S4 Crash-Safe
Reliability / Chaos Drill / SaaS Billing / 5 Cost Drivers, and Sprint S5
Partner Attestation (`docs/40`).
"""
from __future__ import annotations

import datetime
import os
import signal
import subprocess
import sys
import time

import pytest

from app import api, automations as autm, compliance as comp_mod, neon, security as sec
from app.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def ctx_env(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    slug = store.db["tenants"][0]["slug"]
    _, login_a, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "198.51.100.91", None,
    )
    return store, tid, slug, {"authorization": f"Bearer {login_a['token']}"}


def test_s3_privacy_pack_grievance_sla_minor_gate_and_dsr(ctx_env):
    store, tid, slug, hdr = ctx_env

    # Public privacy notice + full compliance pack
    st_pub, pub_n, _ = api.handle(store, "GET", f"/api/v1/public/privacy-notice/{slug}", {}, None, {}, "198.51.100.92", None)
    assert st_pub == 200
    assert pub_n["notice"]["notice_version"].startswith("v3.2")

    st_pack, pack, _ = api.handle(store, "GET", "/api/v1/compliance/pack", {}, None, hdr, "198.51.100.91", None)
    assert st_pack == 200
    assert len(pack["dpia"]["assessments"]) == 4

    # Public grievance intake + 30-day statutory SLA resolution
    st_g, g_res, _ = api.handle(
        store, "POST", f"/api/v1/public/grievance/{slug}", {},
        {"name": "Sneha Kulkarni", "subject": "Data copy request", "description": "Please share my DSR export."},
        {}, "198.51.100.92", None,
    )
    assert st_g == 200
    gid = g_res["ticket_id"]
    st_gp, gp_res, _ = api.handle(
        store, "PATCH", f"/api/v1/compliance/grievances/{gid}", {},
        {"status": "resolved", "resolution_note": "DSR bundle delivered."},
        hdr, "198.51.100.91", None,
    )
    assert st_gp == 200
    assert gp_res["grievance"]["status"] == "resolved"

    # DPDP §9 minor age gate on public funnel
    funnel = next(f for f in store.all("funnels", tid) if f.get("published"))
    st_kid, kid_res, _ = api.handle(
        store, "POST", f"/api/v1/public/funnel/{funnel['slug']}/submit", {},
        {"name": "Minor User", "phone": "+919800011122", "consent": True, "age": 15},
        {}, "198.51.100.92", None,
    )
    assert st_kid == 422
    assert kid_res["error"]["code"] == "MINOR_PROCESSING_REFUSED"

    # Adult funnel submission -> DSR export + Rule-4 consent artefact + human score override
    st_ad, ad_res, _ = api.handle(
        store, "POST", f"/api/v1/public/funnel/{funnel['slug']}/submit", {},
        {"name": "Rohan Deshpande", "phone": "+919833445566", "consent": True, "age": 29},
        {}, "198.51.100.92", None,
    )
    assert st_ad == 200
    cid = ad_res["contact_id"]

    st_ov, ov_res, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/score-override", {},
        {"score": 91, "reason": "Ready buyer with plot token cheque."},
        hdr, "198.51.100.91", None,
    )
    assert st_ov == 200
    assert ov_res["contact"]["score"] == 91

    st_dsr, dsr, _ = api.handle(store, "GET", f"/api/v1/contacts/{cid}/dsr-export", {}, None, hdr, "198.51.100.91", None)
    assert st_dsr == 200
    assert dsr["bundle"]["automated_scoring_explanation"]["human_override_applied"] is True


def test_s3_consent_withdrawal_propagation_and_erasure_certificate(ctx_env):
    store, tid, _, hdr = ctx_env
    st_c, created, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Prachi Jadhav", "phone": "+919844556677", "email": "prachi@example.in", "consent": {"status": "granted"}},
        hdr, "198.51.100.91", None,
    )
    assert st_c == 200
    cid = created["contact"]["id"]

    run_row = store.insert(
        "runs", tid,
        {
            "type": "automation_run",
            "automation_id": "aut_1",
            "subject_kind": "contact",
            "subject_id": cid,
            "status": "deferred",
            "step_index": 0,
            "next_at": int(time.time()) + 120,
            "context": {"contact": {"id": cid, "name": "Prachi Jadhav"}},
        },
        prefix="run",
    )
    # Withdraw consent -> immediate propagation blocks queued run
    st_w, w_res, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/consent", {},
        {"status": "withdrawn"},
        hdr, "198.51.100.91", None,
    )
    assert st_w == 200
    assert w_res["propagation"]["runs_cancelled_count"] == 1
    assert store.get("runs", tid, run_row["id"])["status"] == "blocked"

    # Erase contact -> cryptographic certificate + blind-index suppression tombstone
    st_e, e_res, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/erase", {},
        {"reason": "DPDP §8(5) erasure"},
        hdr, "198.51.100.91", None,
    )
    assert st_e == 200
    eid = e_res["certificate"]["id"]
    st_v, v_res, _ = api.handle(store, "GET", f"/api/v1/compliance/erasures/{eid}/verify", {}, None, hdr, "198.51.100.91", None)
    assert st_v == 200
    assert v_res["verification"]["valid"] is True

    # Re-creating or CSV-importing the erased phone is refused
    st_dup, dup_body, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Prachi Reimport", "phone": "+919844556677"},
        hdr, "198.51.100.91", None,
    )
    assert st_dup == 422
    assert dup_body["error"]["code"] == "SUPPRESSED_BY_ERASURE"

    st_imp, imp_body, _ = api.handle(
        store, "POST", "/api/v1/leads/import", {},
        {"rows": [{"name": "Prachi CSV", "phone": "+919844556677"}]},
        hdr, "198.51.100.91", None,
    )
    assert st_imp == 200
    assert imp_body["accepted"] == 0
    assert imp_body["rejected"][0]["reason"] == "suppressed_by_erasure"


def test_s4_chaos_sigkill_worker_dlq_billing_and_s5_attestation(tmp_path, ctx_env):
    store, tid, _, hdr = ctx_env
    ist = datetime.timezone(datetime.timedelta(minutes=330))
    t0 = int(datetime.datetime(2026, 11, 17, 12, 0, 0, tzinfo=ist).timestamp())

    # 1. Real SIGKILL mid-step chaos test on an isolated store file
    chaos_file = str(tmp_path / "chaos.json")
    cs = Store(chaos_file)
    ctid = cs.db["tenants"][0]["id"]
    for a in cs.all("automations", ctid):
        cs.update("automations", ctid, a["id"], {"enabled": False})
    auto = cs.insert(
        "automations", ctid,
        {
            "name": "Two-Step Campaign",
            "trigger": "lead.created",
            "enabled": True,
            "steps": [
                {"type": "send_whatsapp", "text": "Hello {{contact.name}} from {{tenant.name}}"},
                {"type": "create_task", "title": "Call {{contact.name}}", "due_min": 20},
            ],
        },
        prefix="aut",
    )
    cids = []
    for i in range(3):
        ph = f"+91970001110{i}"
        c = cs.insert(
            "contacts", ctid,
            {
                "name": f"Chaos Lead {i}",
                "phone_enc": sec.seal(ph, aad=ctid),
                "phone_hash": sec.blind_index(ph),
                "email_enc": sec.seal("", aad=ctid),
                "email_hash": "",
                "stage": "new",
                "created_at": t0 - 20,
                "consent": {"status": "granted", "purpose": "enquiry", "channel": "whatsapp", "captured_at": t0 - 20},
            },
            prefix="cnt",
        )
        cids.append(c["id"])

    code = f"""
import os, signal, sys
sys.path.insert(0, {ROOT!r})
from app.store import Store
from app import automations as autm
st = Store({chaos_file!r})
tid = {ctid!r}
t0 = {t0}
tenant = st.get("tenants", tid, tid)
n = 0
def hook(run, idx, eff):
    global n
    n += 1
    if n == 3:  # Run 1 (2 steps) + Run 2 Step 0 (WhatsApp sent, step_index NOT yet updated!)
        os.kill(os.getpid(), signal.SIGKILL)
eng = autm.Runtime(st, now_fn=lambda: t0, worker_id="w1", lease_ttl_sec=15, after_effect_hook=hook)
eng.plan(tid, at=t0, automation_id={auto['id']!r})
for r in eng.due(tid, at=t0):
    eng.advance(r, tenant, at=t0)
"""
    p = subprocess.Popen([sys.executable, "-c", code])
    assert p.wait(timeout=15) == -signal.SIGKILL

    cs2 = Store(chaos_file)
    ctenant = cs2.get("tenants", ctid, ctid)
    eng2 = autm.Runtime(cs2, now_fn=lambda: t0 + 20, worker_id="w2")
    eng2.plan(ctid, at=t0 + 20, automation_id=auto["id"])
    for r in eng2.due(ctid, at=t0 + 20):
        eng2.advance(r, ctenant, at=t0 + 20)

    for cid in cids:
        convs = [cv for cv in cs2.all("conversations", ctid) if cv.get("contact_id") == cid]
        msgs = [m for cv in convs for m in (cv.get("messages") or []) if m.get("dir") == "out"]
        tasks = [t for t in cs2.all("tasks", ctid) if t.get("contact_id") == cid]
        assert len(msgs) == 1
        assert len(tasks) == 1

    # 2. SaaS GST billing + 5 cost drivers + S5 partner attestation
    st_pl, _, _ = api.handle(store, "POST", "/api/v1/billing/plan", {}, {"plan": "scale"}, hdr, "198.51.100.91", None)
    assert st_pl == 200
    st_inv, inv_res, _ = api.handle(store, "POST", "/api/v1/billing/invoices/generate", {}, {}, hdr, "198.51.100.91", None)
    assert st_inv == 200
    assert inv_res["invoice"]["plan"] == "scale"
    assert inv_res["invoice"]["gst_paise"] > 0

    st_cd, cd_res, _ = api.handle(store, "GET", "/api/v1/billing/cost-drivers", {}, None, hdr, "198.51.100.91", None)
    assert st_cd == 200
    assert len(cd_res["drivers"]) == 5

    for pr in store.all("proposals", tid):
        if pr.get("status") == "pending":
            store.update("proposals", tid, pr["id"], {"created_at": int(time.time()) - 900})
    st_att, att_res, _ = api.handle(store, "POST", "/api/v1/compliance/attestations", {}, {}, hdr, "198.51.100.91", None)
    assert st_att == 200
    assert att_res["attestation"]["gate_passed"] is True

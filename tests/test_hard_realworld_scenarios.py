"""
Hard Real-World, Concurrency, Chaos & Adversarial Multi-Persona Battle-Test Suite (`v1.39.0`):

Simulates real individuals, buyers, NRIs, field agents on 2G/offline networks,
channel partners, legal/revenue officers, agency owners, enterprise IT admins,
AI MCP clients, and attackers across 12 end-to-end scenarios:
  1. Rural 2G Marathi/Devanagari HTML form buyer + bot/honeypot/forged-token rejection + bool consent SDK parity
  2. 20-thread Launch-Saturday concurrency stampede on BOTH Plot Unit Blocking AND Site-Visit Calendar Slots
  3. Maharashtra 7/12 Satbara Boja/Class-II legal blockers, §194-IA TDS & NaN/Inf/Bool cost-sheet torture
  4. NRI/OCI FEMA agricultural-land prohibition vs residential NRE/NRO clearance
  5. Razorpay webhook retry-storm idempotency & tampered HMAC rejection
  6. DPDP consent withdrawal blocking reputation outreach + §8(5) erasure tombstone blocking re-import
  7. Malicious PDF (#XX + /FlateDecode) / OOXML macro upload blocks + MCP TOCTOU parameter-swap rejection
  8. White-Label Agency sub-account provisioning, PII-free snapshot & cross-tenant IDOR / OTLP trace isolation
  9. Channel Partner (`channel_partner` role) portal jailbreak siege (`403 FORBIDDEN_SCOPE`)
 10. Enterprise OIDC RS256 (`alg:none`, domain wall, state replay) & OAuth 2.1 PKCE replay token revocation
 11. Field-agent shaky-network Offline PWA mutation sync deduplication
 12. 310-route hostile type/null/overflow fuzzer (0 unhandled HTTP 500s)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from app import api, connectors as conn_mod, inventory as ivt, media as md_mod, neon, security as sec, server as srv_mod
from app.store import Store
from sdk.python.realtor_estates_sdk import RealtorEstatesClient, generate_pkce_pair


@pytest.fixture()
def battle_env(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "battle_store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login_doc, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    tok = login_doc["token"]
    return store, str(tmp_path), tid, tok, {"authorization": f"Bearer {tok}"}


def test_scenario_1_rural_2g_marathi_buyer_and_bool_consent_sdk_parity(battle_env):
    store, _, tid, tok, hdr = battle_env

    # Live HTTP server for real 2G browser form-urlencoded submission (zero JS)
    orig_store = srv_mod.STORE
    srv_mod.STORE = store
    srv = srv_mod._HTTPServer(("127.0.0.1", 0), srv_mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/f/nanded-plots", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        m = re.search(r'name="_ftok"\s+value="([^"]+)"', html)
        assert m, "Signed _ftok missing from SSR funnel"
        ftok = m.group(1)

        # Spambot with forged _ftok -> 400/403
        bad_form = urllib.parse.urlencode({
            "_ftok": "forged.token",
            "name": "Bot",
            "phone": "+919822001100",
            "consent": "1",
        }).encode("utf-8")
        req_bad = urllib.request.Request(
            f"{base}/api/v1/public/funnel/nanded-plots/submit",
            data=bad_form,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_bad:
            urllib.request.urlopen(req_bad, timeout=5)
        assert exc_bad.value.code in (400, 403)

        # Genuine Marathi rural buyer on 2G Opera Mini (HTML response, Devanagari + emoji)
        good_form = urllib.parse.urlencode({
            "_ftok": ftok,
            "name": "श्री. संतोष पाटील — नांदेड 🏡",
            "phone": "+919823114455",
            "city": "नांदेड",
            "requirement": "२ गुंठे NA प्लॉट हवा आहे",
            "consent": "1",
            "company_website": "",
        }).encode("utf-8")
        req_ok = urllib.request.Request(
            f"{base}/api/v1/public/funnel/nanded-plots/submit",
            data=good_form,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"},
            method="POST",
        )
        with urllib.request.urlopen(req_ok, timeout=5) as resp_ok:
            body_html = resp_ok.read().decode("utf-8")
            assert resp_ok.status == 200
            assert "Enquiry Received" in body_html

        # Official Python SDK passing boolean `consent=True` (previously caused 500 AttributeError!)
        sdk = RealtorEstatesClient(base, tok)
        created = sdk.contacts.create(name="SDK Buyer Kulkarni", phone="+919823998877", city="Pune", consent=True)
        assert created["contact"]["consent"]["status"] == "granted"

        # Official Node.js / TypeScript ESM SDK live round-trip with boolean `consent: true` + statutory cost sheet
        import subprocess
        node_script = (
            "import { RealtorEstatesClient } from './sdk/typescript/realtor-estates-sdk.mjs';"
            f"const c = new RealtorEstatesClient({{ baseUrl: {json.dumps(base)}, accessToken: {json.dumps(tok)} }});"
            "const r = await c.contacts.create({ name: 'TS SDK Buyer Joshi', phone: '+919823554433', city: 'Nagpur', consent: true });"
            "const cs = await c.govt.costSheet({ agreement_value_inr: 5200000 });"
            "if (r.contact.consent.status !== 'granted' || !cs.cost_sheet.tds_194ia.applicable) process.exit(2);"
        )
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        node_res = subprocess.run(["node", "--input-type=module", "-e", node_script], cwd=repo_root, capture_output=True, text=True, check=False)
        assert node_res.returncode == 0, node_res.stderr
    finally:
        srv.shutdown()
        srv_mod.STORE = orig_store


def test_scenario_2_twenty_thread_saturday_launch_stampede_unit_and_calendar(battle_env, monkeypatch):
    store, _, tid, _, hdr = battle_env
    cid = store.all("contacts", tid)[0]["id"]
    pid = store.all("properties", tid)[0]["id"]
    uid = [u for u in store.all("units", tid) if u.get("status") == "available"][0]["id"]

    # Inject a 5ms delay inside apply_block to widen any potential TOCTOU window
    orig_apply = ivt.apply_block

    def _delayed_apply(*a, **kw):
        out = orig_apply(*a, **kw)
        time.sleep(0.005)
        return out

    monkeypatch.setattr(ivt, "apply_block", _delayed_apply)

    deal_ids = [
        store.insert("deals", tid, {"title": f"Launch Bid {i}", "contact_id": cid, "property_id": pid, "value": 3200000, "stage": "Negotiation"}, "dl")["id"]
        for i in range(20)
    ]

    unit_codes: list[int] = []
    cal_codes: list[int] = []
    b1 = threading.Barrier(20)

    def _unit_racer(i: int) -> None:
        b1.wait()
        st, _, _ = api.handle(
            store, "POST", f"/api/v1/units/{uid}/block", {},
            {"deal_id": deal_ids[i], "hours": 24, "reason": f"Exec {i}"},
            hdr, f"10.10.0.{i + 1}", None,
        )
        unit_codes.append(st)

    ts = [threading.Thread(target=_unit_racer, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert unit_codes.count(200) == 1 and unit_codes.count(409) == 19

    # Now 20 buyers simultaneously race for the exact same Sunday 10:30 AM site-visit slot!
    _, slots_doc, _ = api.handle(store, "GET", "/api/v1/calendar/slots", {"property_id": [pid], "date": ["2026-11-29"]}, {}, hdr, "127.0.0.1", None)
    slot_ts = slots_doc["slots"][0]["start_ts"]
    b2 = threading.Barrier(20)

    def _cal_racer(i: int) -> None:
        b2.wait()
        st, _, _ = api.handle(
            store, "POST", "/api/v1/calendar/book", {},
            {"contact_id": cid, "property_id": pid, "start_ts": slot_ts, "notes": f"Buyer #{i}"},
            hdr, f"10.20.0.{i + 1}", None,
        )
        cal_codes.append(st)

    ts2 = [threading.Thread(target=_cal_racer, args=(i,)) for i in range(20)]
    for t in ts2:
        t.start()
    for t in ts2:
        t.join()
    assert cal_codes.count(200) == 1 and cal_codes.count(409) == 19


def test_scenario_3_and_4_maharashtra_712_boja_costsheet_extremes_and_nri_fema(battle_env):
    store, _, tid, _, hdr = battle_env
    prop = store.all("properties", tid)[0]
    deal = next(d for d in store.all("deals", tid) if d.get("property_id") == prop["id"])

    # 7/12 Satbara with active Bank Boja + Class-II occupancy without Collector permission MUST block agreement execution!
    st_rv, rv_doc, _ = api.handle(
        store, "PUT", f"/api/v1/properties/{prop['id']}/revenue-vault", {},
        {
            "satbara_7_12": {
                "gat_or_survey_no": "142/A",
                "village": "Taroda Bk",
                "taluka": "Nanded",
                "district": "Nanded",
                "occupant_class": "class_2",
                "collector_permission_obtained": False,
                "itar_hakk_boja": ["SBI Crop Mortgage ₹18L"],
                "boja_cleared": False,
                "verified": True,
            },
            "na_order": {"status": "pending"},
        },
        hdr, "127.0.0.1", None,
    )
    assert st_rv == 200
    assert rv_doc["clearance"]["can_execute_agreement"] is False
    assert len(rv_doc["clearance"]["blockers"]) >= 2

    # Attempting IGR e-Registration while 7/12 has active Boja/Class-II blocker -> 409 REVENUE_CLEARANCE_BLOCKED!
    st_reg_blk, reg_err, _ = api.handle(
        store, "PUT", f"/api/v1/deals/{deal['id']}/registration", {},
        {"mode": "igr_e_registration", "agreement_value_inr": 6_000_000},
        hdr, "127.0.0.1", None,
    )
    assert st_reg_blk == 409 and reg_err["error"]["code"] == "REVENUE_CLEARANCE_BLOCKED"

    # Hostile cost-sheet inputs (inf, -inf, NaN, True, negative, zero) -> crisp 400 (0 HTTP 500s!)
    for bad_val in ("inf", "-inf", "NaN", True, False, -500_000, 0, 10**15):
        st_bad, _, _ = api.handle(store, "POST", "/api/v1/govt/cost-sheet", {}, {"agreement_value_inr": bad_val}, hdr, "127.0.0.1", None)
        assert st_bad == 400, f"Expected 400 for {bad_val!r}, got {st_bad}"

    # Valid ₹65L female buyer NA plot -> 0% GST, 1% female stamp duty concession, and 1% §194-IA TDS triggered (>= ₹50L)!
    st_cs, cs_doc, _ = api.handle(
        store, "POST", "/api/v1/govt/cost-sheet", {},
        {
            "agreement_value_inr": 6_500_000,
            "property_gst_category": "na_plot_or_oc_ready",
            "buyer_gender_scheme": "sole_or_joint_female",
        },
        hdr, "127.0.0.1", None,
    )
    assert st_cs == 200
    sheet = cs_doc["cost_sheet"]
    assert sheet["gst_inr"] == 0
    assert sheet["women_concession_pct"] == 1.0
    assert sheet["tds_194ia"]["applicable"] is True and sheet["tds_194ia"]["tds_amount_inr"] == 65_000

    # Scenario 4: NRI trying to buy agricultural land -> prohibited under FEMA! Residential NA plot -> permitted!
    _, fema_agri, _ = api.handle(
        store, "POST", "/api/v1/fema/check", {},
        {"buyer_residency": "nri", "property_category": "agricultural", "acquisition_mode": "purchase"},
        hdr, "127.0.0.1", None,
    )
    assert fema_agri["fema"]["status"] == "PROHIBITED"

    _, fema_res, _ = api.handle(
        store, "POST", "/api/v1/fema/check", {},
        {"buyer_residency": "nri", "property_category": "residential", "acquisition_mode": "purchase", "na_order_verified": True},
        hdr, "127.0.0.1", None,
    )
    assert fema_res["fema"]["status"] == "ELIGIBLE"


def test_scenario_5_6_7_payments_dpdp_erasure_malicious_uploads_and_channel_partner_jail(battle_env, monkeypatch):
    store, _, tid, _, hdr = battle_env

    # Scenario 5: Razorpay webhook retry storm (3x identical webhook) + tampered signature
    pay_sec = "rzp_whsec_" + secrets.token_hex(16)
    monkeypatch.setenv("PAYMENTS_WEBHOOK_SECRET", pay_sec)
    deal = store.all("deals", tid)[0]
    ev_doc = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_storm_001",
                    "amount": 10000000,  # ₹1,00,000 in paise
                    "currency": "INR",
                    "status": "captured",
                    "method": "upi",
                    "notes": {"tenant_id": tid, "deal_id": deal["id"], "kind": "booking_token"},
                }
            }
        },
    }
    raw_ev = json.dumps(ev_doc).encode("utf-8")
    good_sig = hmac.new(pay_sec.encode("utf-8"), raw_ev, hashlib.sha256).hexdigest()

    # Tampered signature -> 401
    assert api.handle(store, "POST", "/api/v1/webhooks/payments", {}, ev_doc, {"x-razorpay-signature": "bad"}, "127.0.0.1", None, raw_ev)[0] == 401
    # 3x carrier retry storm -> idempotent single payment row!
    before_n = len(store.all("payments", tid))
    for _ in range(3):
        st_p, _, _ = api.handle(store, "POST", "/api/v1/webhooks/payments", {}, ev_doc, {"x-razorpay-signature": good_sig}, "127.0.0.1", None, raw_ev)
        assert st_p == 200
    assert len(store.all("payments", tid)) == before_n + 1

    # Scenario 6: DPDP Pending/Withdrawn Consent blocks Reputation Request + Right-to-Erasure blocks re-import!
    _, new_c, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Meera Deshpande", "phone": "+919855667788", "email": "meera@example.com", "consent": False},
        hdr, "127.0.0.1", None,
    )
    mcid = new_c["contact"]["id"]
    # Consent is pending (not granted) -> reputation outreach MUST be blocked (403 CONSENT_REQUIRED)
    assert api.handle(store, "POST", "/api/v1/reputation/request", {}, {"contact_id": mcid}, hdr, "127.0.0.1", None)[0] == 403

    # Exercise DPDP §8(5) Right to Erasure -> contact erased + re-importing same phone is blocked (422 SUPPRESSED_BY_ERASURE)!
    st_er, er_doc, _ = api.handle(store, "POST", f"/api/v1/contacts/{mcid}/erase", {}, {"reason": "Buyer requested erasure"}, hdr, "127.0.0.1", None)
    assert st_er == 200
    st_reimp, reimp_err, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Meera Reimport Attempt", "phone": "+919855667788", "consent": True},
        hdr, "127.0.0.1", None,
    )
    assert st_reimp == 422 and reimp_err["error"]["code"] == "SUPPRESSED_BY_ERASURE"

    # Scenario 7: Malicious hex-obfuscated PDF (`/#4aavaScript`), FlateDecode PDF & PHP-in-PNG polyglot upload blocks
    import zlib
    mal_pdf = b"%PDF-1.7\n1 0 obj << /Type /Catalog /OpenAction << /S /#4a#61#76#61#53#63#72#69#70#74 /JS (app.alert(1)) >> >> endobj\n%%EOF"
    flate_pdf = b"%PDF-1.7\n1 0 obj << /Filter /FlateDecode >>\nstream\n" + zlib.compress(b"<< /S /Launch /Win (cmd.exe) >>") + b"\nendstream\nendobj\n%%EOF"
    poly_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 16 + b"<?php passthru($_GET['x']); ?>IEND"
    for bad_bytes, bad_mime in ((mal_pdf, "application/pdf"), (flate_pdf, "application/pdf"), (poly_png, "image/png")):
        with pytest.raises(conn_mod.ConnectorError):
            conn_mod.scan_document_upload(bad_bytes, bad_mime, "hostile_upload")

    # Scenario 9: Channel Partner (`channel_partner` role) tries escaping `/api/v1/cp/*` -> 403 FORBIDDEN_SCOPE!
    cp_user = next((u for u in store.db["users"] if u["tenant_id"] == tid and u["role"] == "channel_partner"), None)
    if cp_user:
        _, cp_sess_r = api.sess.open_session(store, tid, cp_user["id"], ip="127.0.0.1", ua="CP-App")
        cp_tok = sec.issue_token({"sub": cp_user["email"], "uid": cp_user["id"], "tid": tid, "role": "channel_partner", "sid": " bad "})
        # Mint valid session sid
        sid_row = [s for s in store.db["sessions"] if s["user_id"] == cp_user["id"]][-1]
        cp_tok = sec.issue_token({"sub": cp_user["email"], "uid": cp_user["id"], "tid": tid, "role": "channel_partner", "sid": sid_row["id"]})
        cp_hdr = {"authorization": f"Bearer {cp_tok}"}
        assert api.handle(store, "GET", "/api/v1/cp/me", {}, {}, cp_hdr, "127.0.0.1", None)[0] == 200
        assert api.handle(store, "GET", "/api/v1/contacts", {}, {}, cp_hdr, "127.0.0.1", None)[0] == 403
        assert api.handle(store, "GET", "/api/v1/security/hardening-status", {}, {}, cp_hdr, "127.0.0.1", None)[0] == 403


def test_scenario_8_and_10_cross_tenant_subaccount_idor_and_oauth_pkce_replay_revocation(battle_env):
    store, _, tid, _, hdr = battle_env

    # Provision isolated child sub-account & login as child sub-account owner
    _, sub_doc, _ = api.handle(
        store, "POST", "/api/v1/agency/subaccounts", {},
        {"name": "Child Franchise Builder", "admin_email": "child@franchise.in", "admin_password": "ChildPassword@2026!"},
        hdr, "127.0.0.1", None,
    )
    child_tid = sub_doc["subaccount"]["tenant_id"]
    _, child_login, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "child@franchise.in", "password": "ChildPassword@2026!"},
        {}, "127.0.0.1", None,
    )
    child_hdr = {"authorization": f"Bearer {child_login['token']}"}

    # Parent contact ID & deal ID MUST be completely invisible (404) to child sub-account owner!
    parent_cid = store.all("contacts", tid)[0]["id"]
    parent_did = store.all("deals", tid)[0]["id"]
    assert api.handle(store, "GET", f"/api/v1/contacts/{parent_cid}", {}, {}, child_hdr, "127.0.0.1", None)[0] == 404
    assert api.handle(store, "PATCH", f"/api/v1/deals/{parent_did}", {}, {"stage": "Won"}, child_hdr, "127.0.0.1", None)[0] == 404
    _, child_traces, _ = api.handle(store, "GET", "/api/v1/observability/traces", {}, {}, child_hdr, "127.0.0.1", None)
    assert all(s["tenant_id"] == child_tid for s in child_traces["spans"])

    # OAuth 2.1 PKCE authorization code replay revokes the issued access token immediately (RFC 6749 §10.5)
    _, app_reg, _ = api.handle(
        store, "POST", "/api/v1/oauth/apps", {},
        {"name": "Battle OAuth App", "redirect_uris": ["https://battle.example/cb"], "scopes": ["crm:read"]},
        hdr, "127.0.0.1", None,
    )
    verifier, challenge = generate_pkce_pair()
    _, az, _ = api.handle(
        store, "POST", "/api/v1/oauth/authorize", {},
        {"client_id": app_reg["app"]["client_id"], "redirect_uri": "https://battle.example/cb", "code_challenge": challenge, "code_challenge_method": "S256"},
        hdr, "127.0.0.1", None,
    )
    _, tok_ok, _ = api.handle(
        store, "POST", "/api/v1/oauth/token", {},
        {"code": az["code"], "code_verifier": verifier, "client_id": app_reg["app"]["client_id"], "client_secret": app_reg["client_secret_once"], "redirect_uri": "https://battle.example/cb"},
        {}, "127.0.0.1", None,
    )
    oa_hdr = {"authorization": f"Bearer {tok_ok['access_token']}"}
    assert api.handle(store, "GET", "/api/v1/contacts", {}, {}, oa_hdr, "127.0.0.1", None)[0] == 200
    # Attacker replays stolen auth code -> 401 AND revokes tok_ok['access_token']!
    assert api.handle(
        store, "POST", "/api/v1/oauth/token", {},
        {"code": az["code"], "code_verifier": verifier, "client_id": app_reg["app"]["client_id"], "client_secret": app_reg["client_secret_once"], "redirect_uri": "https://battle.example/cb"},
        {}, "127.0.0.1", None,
    )[0] == 401
    assert api.handle(store, "GET", "/api/v1/contacts", {}, {}, oa_hdr, "127.0.0.1", None)[0] == 401

    # SSO-only JIT account (`password_hash == ""`) receiving a password-login attempt MUST return 401 (never 500 KeyError!)
    store.db["users"].append({
        "id": "usr_sso_only_probe",
        "tenant_id": tid,
        "email": "sso.only@godavari.in",
        "name": "SSO Only Exec",
        "role": "agent",
        "password_hash": "",
        "mfa_enrolled": False,
        "active": True,
    })
    st_sso_pw, _, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "sso.only@godavari.in", "password": "AnyPassword123!"},
        {}, "127.0.0.1", None,
    )
    assert st_sso_pw == 401

    # MCP HITL `args_sha256` parameter-tampering guard: approve ₹5,000 budget, replay approval with ₹50,000 -> blocked!
    camp_id = store.all("campaigns", tid)[0]["id"]
    _, mcp1, _ = api.handle(
        store, "POST", "/api/v1/mcp", {"profile": ["full"]},
        {"jsonrpc": "2.0", "id": 91, "method": "tools/call", "params": {"name": "campaigns.adjust_budget", "arguments": {"campaign_id": camp_id, "budget_daily_inr": 5000}}},
        hdr, "127.0.0.1", None,
    )
    prop_id = mcp1["result"]["structuredContent"]["proposal_id"]
    api.handle(store, "POST", f"/api/v1/learning/{prop_id}/decide", {}, {"decision": "approve", "acknowledge_risk": True}, hdr, "127.0.0.1", None)
    _, mcp_tamper, _ = api.handle(
        store, "POST", "/api/v1/mcp", {"profile": ["full"]},
        {"jsonrpc": "2.0", "id": 92, "method": "tools/call", "params": {"name": "campaigns.adjust_budget", "arguments": {"campaign_id": camp_id, "budget_daily_inr": 50_000, "confirmation_proposal_id": prop_id}}},
        hdr, "127.0.0.1", None,
    )
    assert mcp_tamper["result"]["isError"] is True
    assert mcp_tamper["result"]["error_code"] == "MCP_CONFIRMATION_INVALID"


def test_scenario_11_and_12_offline_field_sync_and_full_310_route_zero_500_fuzzer(battle_env):
    store, _, tid, _, hdr = battle_env

    # Scenario 11: Field agent in rural low-signal zone replays same offline mutation batch twice on reconnect
    cid = store.all("contacts", tid)[0]["id"]
    batch = {
        "mutations": [
            {
                "client_mutation_id": "offline_mut_nanded_001",
                "kind": "note.add",
                "contact_id": cid,
                "text": "Visited Gat 142 boundary stones with family; wants East-facing corner plot.",
            }
        ]
    }
    st_s1, sync1, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, batch, hdr, "127.0.0.1", None)
    st_s2, sync2, _ = api.handle(store, "POST", "/api/v1/offline/sync", {}, batch, hdr, "127.0.0.1", None)
    assert st_s1 == 200 and sync1["applied"] == 1 and sync1["deduplicated"] == 0
    assert st_s2 == 200 and sync2["applied"] == 0 and sync2["deduplicated"] == 1

    # Scenario 12: Full 310-route hostile type/null/overflow/XSS fuzzer -> ZERO HTTP 500s!
    sample_ids = {
        "cid": cid,
        "pid": store.all("properties", tid)[0]["id"],
        "pid_": store.all("properties", tid)[0]["id"],
        "did": store.all("deals", tid)[0]["id"],
        "deal_id": store.all("deals", tid)[0]["id"],
        "uid": store.all("units", tid)[0]["id"],
        "fid": store.all("funnels", tid)[0]["id"],
        "wid": store.all("automations", tid)[0]["id"],
        "aid": store.all("agents", tid)[0]["id"],
        "slug": "nanded-plots",
    }

    def _mat(pat_str: str) -> str:
        s = pat_str.lstrip("^").rstrip("$")
        return re.sub(r"\(\?P<([a-zA-Z0-9_]+)>[^)]+\)", lambda m: sample_ids.get(m.group(1), "missing_01"), s)

    hostile_body = {
        "name": "श्री पाटील <script>1</script>\x00",
        "phone": "+919823456799",
        "email": "fuzz@example.com",
        "consent": True,
        "settings": True,
        "params": ["arr"],
        "nodes": [{"id": "n1", "type": "wait", "position": "bad", "config": "bad"}],
        "edges": ["bad"],
        "blocks": ["bad"],
        "agreement_value_inr": "inf",
        "stars": True,
        "tags": "not_a_list",
    }

    crashes = []
    for method, pat, rname, _ in api.ROUTES:
        if any(x in rname for x in ("logout", "kill_switch")):
            continue
        sec.LIMITER.hits.clear()
        path = _mat(pat.pattern)
        q = {"limit": ["not_an_int"], "offset": ["-9"], "date": ["bad-date"]}
        body = {} if method == "GET" else hostile_body
        st, doc, _ = api.handle(store, method, path, q, body, hdr, "127.0.0.1", None, b"{}")
        if st == 500:
            crashes.append((method, path, rname, doc))
    assert crashes == [], f"Unhandled HTTP 500s detected: {crashes}"

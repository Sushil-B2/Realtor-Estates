"""
Tests for `v1.38.0` White-Label Agency SaaS Mode, Visual Node-DAG Workflow &
Funnel Block Builder, 2-Way Calendar (`iCal` + Anti-Double-Booking),
`SPF/DKIM/DMARC` Deliverability Doctor & Reputation Suite (`docs/49`).
"""
from __future__ import annotations

import pytest

from app import api, neon, security as sec
from app.store import Store


@pytest.fixture()
def ags_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "ags_store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login_doc, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    return store, tid, {"authorization": f"Bearer {login_doc['token']}"}


def test_white_label_dns_verification_subaccounts_pii_free_snapshot_and_rebilling(ags_ctx):
    store, tid, hdr = ags_ctx

    # CSS injection in primary_color is rejected
    st_bad_css, _, _ = api.handle(
        store, "POST", "/api/v1/agency/whitelabel", {},
        {"brand_name": "Apex Builders", "primary_color": "#0f6b52;background:url(//evil)"},
        hdr, "127.0.0.1", None,
    )
    assert st_bad_css == 400

    # Valid white-label configuration + HMAC DNS TXT verification
    st_wl, wl_doc, _ = api.handle(
        store, "POST", "/api/v1/agency/whitelabel", {},
        {
            "brand_name": "Apex Realty Cloud",
            "primary_color": "#1d4ed8",
            "custom_domain": "crm.apexrealty.in",
            "rebilling_markup_pct": 30.0,
        },
        hdr, "127.0.0.1", None,
    )
    assert st_wl == 200 and wl_doc["white_label"]["domain_verified"] is False
    expected_txt = wl_doc["white_label"]["dns_txt_value"]

    assert api.handle(
        store, "POST", "/api/v1/agency/whitelabel/verify-dns", {},
        {"dns_txt_records": ["re-domain-verify=wrong"]}, hdr, "127.0.0.1", None,
    )[0] == 400
    st_ver, ver_doc, _ = api.handle(
        store, "POST", "/api/v1/agency/whitelabel/verify-dns", {},
        {"dns_txt_records": [expected_txt]}, hdr, "127.0.0.1", None,
    )
    assert st_ver == 200 and ver_doc["white_label"]["domain_verified"] is True

    # Provision isolated child sub-account + apply PII-free snapshot
    st_sub, sub_doc, _ = api.handle(
        store, "POST", "/api/v1/agency/subaccounts", {},
        {
            "name": "Apex Nanded Phase 2",
            "admin_email": "nanded2@apexrealty.in",
            "admin_password": "SubAccount@2026!",
            "plan": "growth",
        },
        hdr, "127.0.0.1", None,
    )
    assert st_sub == 200
    child_tid = sub_doc["subaccount"]["tenant_id"]
    assert child_tid != tid

    st_snap, snap_doc, _ = api.handle(
        store, "POST", "/api/v1/agency/snapshots/apply", {},
        {"subaccount_tenant_id": child_tid}, hdr, "127.0.0.1", None,
    )
    assert st_snap == 200 and snap_doc["pii_copied"] == 0
    assert snap_doc["cloned_funnels"] >= 1 and snap_doc["cloned_workflows"] >= 1
    assert len(store.all("contacts", child_tid)) == 0

    # Cross-tenant snapshot application to an unrelated workspace MUST fail closed (403)
    other_tid = store.db["tenants"][1]["id"]
    assert api.handle(
        store, "POST", "/api/v1/agency/snapshots/apply", {},
        {"subaccount_tenant_id": other_tid}, hdr, "127.0.0.1", None,
    )[0] == 403

    # Rebilling rollup computes 30% markup & 18% GST
    st_reb, reb_doc, _ = api.handle(store, "GET", "/api/v1/agency/rebilling", {}, {}, hdr, "127.0.0.1", None)
    assert st_reb == 200 and reb_doc["subaccount_count"] == 1
    assert reb_doc["net_agency_margin_inr"] > 0


def test_visual_dag_cycle_ssrf_guards_and_composable_funnel_blocks(ags_ctx):
    store, tid, hdr = ags_ctx

    # Cyclic DAG (n2 -> n3 -> n2) MUST be rejected
    st_cyc, cyc_err, _ = api.handle(
        store, "POST", "/api/v1/workflows/dag/validate", {},
        {
            "nodes": [
                {"id": "n1", "type": "trigger", "config": {"event": "lead.created"}},
                {"id": "n2", "type": "send_whatsapp", "config": {"text": "Hello"}},
                {"id": "n3", "type": "wait", "config": {"minutes": 30}},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n3", "target": "n2"},
            ],
        },
        hdr, "127.0.0.1", None,
    )
    assert st_cyc == 400 and cyc_err["error"]["code"] == "DAG_CYCLE_DETECTED"

    # SSRF metadata webhook node MUST be rejected
    st_ssrf, ssrf_err, _ = api.handle(
        store, "POST", "/api/v1/workflows/dag/validate", {},
        {
            "nodes": [
                {"id": "n1", "type": "trigger"},
                {"id": "n2", "type": "webhook", "config": {"url": "https://169.254.169.254/latest/meta-data/"}},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        },
        hdr, "127.0.0.1", None,
    )
    assert st_ssrf == 400 and ssrf_err["error"]["code"] == "DAG_UNSAFE_WEBHOOK_URL"

    # Valid acyclic DAG saves onto automation workflow
    wid = store.all("automations", tid)[0]["id"]
    st_dag, dag_doc, _ = api.handle(
        store, "POST", f"/api/v1/workflows/{wid}/dag", {},
        {
            "nodes": [
                {"id": "n1", "type": "trigger", "config": {"event": "lead.created"}},
                {"id": "n2", "type": "condition", "config": {"field": "budget_inr", "op": ">=", "value": 2500000}},
                {"id": "n3", "type": "send_whatsapp", "config": {"text": "Sharing verified 7/12 & layout."}},
                {"id": "n4", "type": "create_task", "config": {"title": "Schedule plot visit", "due_min": 45}},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3", "condition_branch": "true"},
                {"source": "n3", "target": "n4"},
            ],
        },
        hdr, "127.0.0.1", None,
    )
    assert st_dag == 200 and dag_doc["compiled"]["max_depth"] == 4

    # Composable funnel blocks escape XSS in public SSR funnel HTML
    funnel = store.all("funnels", tid)[0]
    store.update("funnels", tid, funnel["id"], {"published": True})
    st_blk, _, _ = api.handle(
        store, "POST", f"/api/v1/funnels/{funnel['id']}/blocks", {},
        {
            "blocks": [
                {
                    "type": "rera_qr_trust_badge",
                    "heading": "MahaRERA P51100099999 <script>alert(1)</script>",
                    "body": "7/12 clear title & NA order verified.",
                    "badge": "Verified",
                }
            ]
        },
        hdr, "127.0.0.1", None,
    )
    assert st_blk == 200
    st_pub, html = api.public_funnel_page(store, funnel["slug"])
    assert st_pub == 200
    assert "&lt;script&gt;alert(1)&lt;&#x2F;script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_calendar_ical_anti_double_booking_deliverability_and_reputation(ags_ctx):
    store, tid, hdr = ags_ctx
    cid = next(c["id"] for c in store.all("contacts", tid) if (c.get("consent") or {}).get("status") == "granted")
    pid = store.all("properties", tid)[0]["id"]

    # Import external busy block + check available slots
    ics_in = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        "UID:ext-1\r\nSUMMARY:Architect Review\r\n"
        "DTSTART:20261120T090000\r\nDTEND:20261120T100000\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    st_imp, imp_doc, _ = api.handle(store, "POST", "/api/v1/calendar/ical/import", {}, {"ics_text": ics_in}, hdr, "127.0.0.1", None)
    assert st_imp == 200 and imp_doc["imported_busy_events"] == 1

    st_slots, slots_doc, _ = api.handle(
        store, "GET", "/api/v1/calendar/slots",
        {"property_id": [pid], "date": ["2026-11-20"]}, {}, hdr, "127.0.0.1", None,
    )
    assert st_slots == 200
    assert slots_doc["slots"][0]["available"] is False  # 09:00 IST blocked by imported VEVENT!
    open_slot = next(s for s in slots_doc["slots"] if s["available"])

    # Book open slot -> 200; double-book exact same slot -> 409 SLOT_ALREADY_BOOKED
    st_b1, b1_doc, _ = api.handle(
        store, "POST", "/api/v1/calendar/book", {},
        {"contact_id": cid, "property_id": pid, "start_ts": open_slot["start_ts"], "notes": "Line1\r\nBEGIN:VALARM"},
        hdr, "127.0.0.1", None,
    )
    assert st_b1 == 200 and "BEGIN:VCALENDAR" in b1_doc["ics_invite"]
    assert "\r\nBEGIN:VALARM" not in b1_doc["ics_invite"]  # RFC 5545 newline injection escaped!

    st_b2, b2_err, _ = api.handle(
        store, "POST", "/api/v1/calendar/book", {},
        {"contact_id": cid, "property_id": pid, "start_ts": open_slot["start_ts"]},
        hdr, "127.0.0.1", None,
    )
    assert st_b2 == 409 and b2_err["error"]["code"] == "SLOT_ALREADY_BOOKED"

    # SPF/DKIM/DMARC doctor catches +all open relay vs clean config
    _, bad_dns, _ = api.handle(
        store, "POST", "/api/v1/email/deliverability/check", {},
        {"domain": "bad.example.com", "spf_records": ["v=spf1 +all"]},
        hdr, "127.0.0.1", None,
    )
    assert any(f["code"] == "SPF_OPEN_RELAY_PLUS_ALL" for f in bad_dns["findings"])

    _, good_dns, _ = api.handle(
        store, "POST", "/api/v1/email/deliverability/check", {},
        {
            "domain": "mail.sunriseestates.in",
            "spf_records": ["v=spf1 include:_spf.realtorestates.app -all"],
            "dkim_txt": "v=DKIM1; k=rsa; p=" + ("A" * 200),
            "dmarc_txt": "v=DMARC1; p=reject; rua=mailto:dmarc@sunriseestates.in",
        },
        hdr, "127.0.0.1", None,
    )
    assert good_dns["score"] == 100 and good_dns["ready_for_bulk_campaigns"] is True

    # Reputation loop creates escalation task on 2★ detractor while still returning public review URL (no review gating)
    _, req_doc, _ = api.handle(store, "POST", "/api/v1/reputation/request", {}, {"contact_id": cid, "property_id": pid}, hdr, "127.0.0.1", None)
    _, resp_doc, _ = api.handle(
        store, "POST", "/api/v1/reputation/respond", {},
        {"request_id": req_doc["request"]["id"], "stars": 2, "nps_score": 3, "comment": "Cab arrived 20m late"},
        hdr, "127.0.0.1", None,
    )
    assert resp_doc["escalation_task_id"] is not None
    assert resp_doc["public_review_url"].startswith("https://")

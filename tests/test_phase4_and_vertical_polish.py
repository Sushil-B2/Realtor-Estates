"""
Tests for `v1.34.0` (`docs/45-PHASE4-PLATFORM-AND-VERTICAL-POLISH.md`):
  - Phase 4.1: On-Device PII Pre-Screening & Lead Scoring
  - Phase 4.2: AP2 / ACP Agentic Commerce Mandates & Settlement
  - Phase 4.3: GCC (AE Trakheesi) & SEA (SG CEA + ABSD) Regional Gates & Pinning
  - Phase 4.4: Crypto-Agility & Hybrid Post-Quantum v2 Envelope
  - G5 & G7: Photo Cover Pinning / Floor-Plan Slots & Disposable Proof Workspaces
"""
from __future__ import annotations

import pytest

from app import api, media as md, neon, security as sec
from app.store import Store


@pytest.fixture()
def v134_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "198.51.100.115", None,
    )
    return store, tid, {"authorization": f"Bearer {login['token']}"}


def test_prescreen_and_ap2_agentic_settlement(v134_ctx):
    store, tid, hdr = v134_ctx
    _, ps, _ = api.handle(
        store, "POST", "/api/v1/prescreen/verify", {},
        {"text": "Pune 3 BHK site visit urgent budget 1.2 Cr PAN ABCDE1234F"},
        hdr, "198.51.100.115", None,
    )
    assert "[REDACTED_PAN]" in ps["prescreen"]["clean_text"]
    assert ps["prescreen"]["tier"] == "hot"

    unit = next(u for u in store.all("units", tid) if u.get("status") == "available")
    deal = store.all("deals", tid)[0]
    st_m, m, _ = api.handle(
        store, "POST", "/api/v1/commerce/mandates", {},
        {"deal_id": deal["id"], "unit_id": unit["id"], "allowed_action": "hold", "max_amount_inr": 0},
        hdr, "198.51.100.115", None,
    )
    assert st_m == 200
    st_s, s, _ = api.handle(
        store, "POST", "/api/v1/commerce/settle", {},
        {"mandate_token": m["mandate_token"], "action": "hold", "amount_inr": 0},
        hdr, "198.51.100.115", None,
    )
    assert st_s == 200 and s["settlement"]["unit_status"] == "blocked"


def test_regions_crypto_agility_and_g5_g7_polish(v134_ctx):
    store, tid, hdr = v134_ctx
    # AE Trakheesi + Residency Pin
    api.handle(store, "PATCH", "/api/v1/regions/pin", {}, {"region_code": "AE"}, hdr, "198.51.100.115", None)
    _, bad_ae, _ = api.handle(store, "POST", "/api/v1/regions/compliance-check", {}, {"property": {"price": 1_000_000}}, hdr, "198.51.100.115", None)
    assert bad_ae["compliance"]["code"] == "TRAKHEESI_PERMIT_REQUIRED"
    api.handle(store, "PATCH", "/api/v1/regions/pin", {}, {"region_code": "IN"}, hdr, "198.51.100.115", None)

    # Hybrid v2 envelope upgrade
    _, upg, _ = api.handle(store, "POST", "/api/v1/security/crypto-agility/upgrade", {}, {}, hdr, "198.51.100.115", None)
    assert upg["post_upgrade"]["field_ciphertexts"]["v2_share_pct"] == 100.0
    assert api.handle(store, "GET", "/api/v1/contacts", {}, None, hdr, "198.51.100.115", None)[0] == 200

    # G5 gallery ordering + G7 proof sandbox lifecycle
    rows = [
        {"object_key": "derived/properties/p/1-card.webp", "variant": "card"},
        {"object_key": "derived/properties/p/2-card.webp", "variant": "card"},
    ]
    assert md.gallery(rows, pinned_cover_id="1")[0]["photo_id"] == "1"

    _, pt, _ = api.handle(store, "POST", "/api/v1/demo/proof-tenants", {}, {"label": "Sandbox"}, hdr, "198.51.100.115", None)
    ptid = pt["proof_tenant"]["id"]
    assert api.handle(store, "DELETE", f"/api/v1/demo/proof-tenants/{tid}", {}, None, hdr, "198.51.100.115", None)[0] == 403
    assert api.handle(store, "DELETE", f"/api/v1/demo/proof-tenants/{ptid}", {}, None, hdr, "198.51.100.115", None)[0] == 200

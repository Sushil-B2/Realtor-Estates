"""
Tests for `v1.33.0` (`docs/44-ADS-AUTOPILOT-MACRO-UPLOAD-SECURITY-AND-PORTAL-UX.md`):
  - Phase 3 Ads Autopilot with hard daily/monthly spend caps & human budget approval
  - Phase 2 OOXML `.docx`/`.xlsx` macro, external-relationship & archive-bomb blocking + sandboxed preview
"""
from __future__ import annotations

import base64
import io
import zipfile
import pytest

from app import api, neon, security as sec
from app.store import Store

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_docx(doc_xml: str, extra: dict[str, bytes] | None = None) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", doc_xml)
        for k, v in (extra or {}).items():
            zf.writestr(k, v)
    return base64.b64encode(buf.getvalue()).decode("ascii")


@pytest.fixture()
def v133_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "198.51.100.105", None,
    )
    return store, tid, {"authorization": f"Bearer {login['token']}"}


def test_ads_autopilot_hard_caps_and_human_budget_gate(v133_ctx):
    store, tid, hdr = v133_ctx
    c0 = store.all("campaigns", tid)[0]
    old_budget = c0["budget_daily"]

    # Direct >20% jump is blocked with APPROVAL_REQUIRED
    st_jump, r_jump, _ = api.handle(
        store, "PATCH", f"/api/v1/campaigns/{c0['id']}", {},
        {"budget_daily": old_budget * 2}, hdr, "198.51.100.105", None,
    )
    assert st_jump == 422 and r_jump["error"]["code"] == "APPROVAL_REQUIRED"

    # Autopilot stages high-risk proposal without touching budget_daily directly
    st_ap, ap, _ = api.handle(store, "POST", "/api/v1/ads/autopilot/run", {}, {}, hdr, "198.51.100.105", None)
    assert st_ap == 200 and len(ap["staged_budget_proposals"]) >= 1
    assert store.get("campaigns", tid, c0["id"])["budget_daily"] == old_budget

    pid = ap["staged_budget_proposals"][0]
    assert api.handle(store, "POST", f"/api/v1/learning/{pid}/decide", {}, {"decision": "approve"}, hdr, "198.51.100.105", None)[0] == 422
    assert api.handle(
        store, "POST", f"/api/v1/learning/{pid}/decide", {},
        {"decision": "approve", "acknowledge_risk": True}, hdr, "198.51.100.105", None,
    )[0] == 200
    assert store.get("campaigns", tid, c0["id"])["budget_daily"] > old_budget


def test_ooxml_macro_bomb_and_sandboxed_preview(v133_ctx):
    store, tid, hdr = v133_ctx
    pid = store.all("properties", tid)[0]["id"]

    clean_b64 = _make_docx("<w:document><w:p>Collector NA Sanction Verified</w:p></w:document>")
    st_ok, res_ok, _ = api.handle(
        store, "POST", f"/api/v1/properties/{pid}/documents/scan", {},
        {"content_type": DOCX_MIME, "content_b64": clean_b64}, hdr, "198.51.100.105", None,
    )
    assert st_ok == 200 and "Collector NA Sanction Verified" in res_ok["scan"]["sandboxed_preview"]

    macro_b64 = _make_docx("<w:document/>", {"word/vbaProject.bin": b"EVIL_VBA"})
    st_m, res_m, _ = api.handle(
        store, "POST", f"/api/v1/properties/{pid}/documents/scan", {},
        {"content_type": DOCX_MIME, "content_b64": macro_b64}, hdr, "198.51.100.105", None,
    )
    assert st_m == 422 and res_m["error"]["code"] == "OFFICE_MACRO_BLOCKED"

    bomb_b64 = _make_docx("<w:document>" + ("Z" * 350_000) + "</w:document>")
    st_b, res_b, _ = api.handle(
        store, "POST", f"/api/v1/properties/{pid}/documents/scan", {},
        {"content_type": DOCX_MIME, "content_b64": bomb_b64}, hdr, "198.51.100.105", None,
    )
    assert st_b == 422 and res_b["error"]["code"] == "ARCHIVE_BOMB_BLOCKED"

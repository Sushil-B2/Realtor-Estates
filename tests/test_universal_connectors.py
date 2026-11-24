"""
Tests for `v1.28.0` Universal Provider, Tool & Inbound Webhook Fabric (`docs/39`):
  - Zero-blockage Voice Local Twin (playable RIFF/WAVE + qualification transcript)
    & inbound WhatsApp simulator
  - Hot-swappable `custom_http` JSON-template + dot-path connectors across
    Voice, AI/LLM, WhatsApp, SMS, Payments, and Custom Agent Tools
  - Dynamic SSRF-safe egress allowlist (public HTTPS + wildcards vs private/metadata wall)
  - Universal Inbound Webhooks (`/api/v1/webhooks/custom/<hook_id>`)
  - Sprint S2 Document-Pack upload magic-byte & malware/active-PDF scanner
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import api, connectors as conn_mod, neon, security as sec
from app.store import Store


class _UniversalUpstream(BaseHTTPRequestHandler):
    log_message = lambda *a, **kw: None  # noqa: E731
    calls: list = []

    def _json(self, code: int, doc: dict) -> None:
        raw = json.dumps(doc).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        self.calls.append((self.path, body, {k.lower(): v for k, v in self.headers.items()}))
        if self.path == "/voice":
            return self._json(200, {"session": {"id": "bolna_call_42"}, "status": "queued"})
        if self.path == "/llm":
            return self._json(200, {"choices": [{"message": {"content": "Verified plot details shared."}}]})
        if self.path == "/wa":
            return self._json(200, {"messageId": "gupshup_wa_99"})
        if self.path == "/pay":
            return self._json(200, {"order_id": "cf_live_101", "status": "ACTIVE"})
        if self.path == "/tool":
            return self._json(200, {"title_status": "clear"})
        self._json(404, {"error": "not_found"})


@pytest.fixture()
def mock_srv():
    _UniversalUpstream.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _UniversalUpstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", _UniversalUpstream
    finally:
        srv.shutdown()


@pytest.fixture()
def store_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login_a, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    return store, tid, {"authorization": f"Bearer {login_a['token']}"}


def test_zero_blockage_local_twin_wav_and_simulator(store_ctx, monkeypatch):
    store, tid, hdr = store_ctx
    for k in ("VOICE_API_URL", "SARVAM_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cid = store.all("contacts", tid)[0]["id"]
    store.update("contacts", tid, cid, {"consent": {"status": "granted"}})

    st, doc, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
        {"language": "mr", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st == 200
    assert doc["activity"]["duration_sec"] == 48
    assert "AI Disclosure" in doc["activity"]["transcript"]
    assert doc["activity"]["recording"]["content_type"] == "audio/wav"

    wav = conn_mod.synthesize_wav_clip(600)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"

    st_sim, sim_doc, _ = api.handle(
        store, "POST", "/api/v1/connectors/simulate", {},
        {"kind": "whatsapp_inbound", "contact_id": cid},
        hdr, "127.0.0.1", None,
    )
    assert st_sim == 200
    assert sim_doc["window_24h_open"] is True


def test_hotswap_custom_http_connectors_and_agent_tools(mock_srv, store_ctx):
    base, mock_cls = mock_srv
    store, tid, hdr = store_ctx
    cid = store.all("contacts", tid)[0]["id"]
    store.update("contacts", tid, cid, {"consent": {"status": "granted"}, "last_inbound_at": 9999999999})

    v_key = "vk_" + secrets.token_hex(16)
    st_c, c_doc, _ = api.handle(
        store, "POST", "/api/v1/connectors", {},
        {
            "category": "voice",
            "preset": "bolna",
            "name": "Bolna Prod",
            "slug": "bolna_prod",
            "endpoint_url": f"{base}/voice",
            "auth_scheme": "bearer",
            "api_key": v_key,
            "body_template": {"recipient": "{{phone}}", "lang": "{{language}}"},
            "response_id_path": "session.id",
        },
        hdr, "127.0.0.1", None,
    )
    assert st_c == 200
    assert v_key not in json.dumps(c_doc)

    st_v, v_doc, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
        {"language": "mr", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_v == 200
    assert v_doc["dispatch"]["mode"] == "live"
    assert v_doc["dispatch"]["provider_call_id"] == "bolna_call_42"

    # AI + WhatsApp + Payments + Custom Tool connectors
    api.handle(store, "POST", "/api/v1/connectors", {}, {"category": "ai", "preset": "openai_compat", "name": "OpenRouter", "endpoint_url": f"{base}/llm", "api_key": "ok_" + secrets.token_hex(16)}, hdr, "127.0.0.1", None)
    api.handle(store, "POST", "/api/v1/connectors", {}, {"category": "whatsapp", "preset": "gupshup", "name": "Gupshup WA", "endpoint_url": f"{base}/wa", "api_key": "gk_" + secrets.token_hex(16)}, hdr, "127.0.0.1", None)
    api.handle(store, "POST", "/api/v1/connectors", {}, {"category": "payments", "preset": "cashfree", "name": "Cashfree", "endpoint_url": f"{base}/pay", "api_key": "ck_" + secrets.token_hex(16), "key_id_public": "CF_ID"}, hdr, "127.0.0.1", None)
    api.handle(store, "POST", "/api/v1/connectors", {}, {"category": "tool", "preset": "custom_http", "name": "Title Check", "slug": "title_check", "endpoint_url": f"{base}/tool", "auth_scheme": "none", "risk": "read"}, hdr, "127.0.0.1", None)

    st_wa, wa_doc, _ = api.handle(store, "POST", f"/api/v1/inbox/{cid}/send", {}, {"channel": "whatsapp", "text": "Hi", "override_quiet_hours": True}, hdr, "127.0.0.1", None)
    assert st_wa == 200 and wa_doc["message"]["provider_message_id"] == "gupshup_wa_99"

    st_dr, dr_doc, _ = api.handle(store, "POST", f"/api/v1/inbox/{cid}/ai-draft", {}, {}, hdr, "127.0.0.1", None)
    assert st_dr == 200 and "Verified plot details" in dr_doc["draft"]

    deal_id = store.all("deals", tid)[0]["id"]
    st_po, po_doc, _ = api.handle(store, "POST", "/api/v1/payments/orders", {}, {"deal_id": deal_id, "amount_paise": 1_000_000}, hdr, "127.0.0.1", None)
    assert st_po == 200 and po_doc["checkout"]["order_id"] == "cf_live_101"


def test_dynamic_egress_ssrf_and_inbound_custom_webhook_and_doc_scanner(store_ctx):
    store, tid, hdr = store_ctx

    assert api.handle(store, "POST", "/api/v1/security/egress-allowlist", {}, {"host": "api.nextgen2029.io"}, hdr, "127.0.0.1", None)[0] == 200
    assert api.handle(store, "POST", "/api/v1/security/egress-allowlist", {}, {"host": "127.0.0.1"}, hdr, "127.0.0.1", None)[0] == 400
    assert api.handle(store, "POST", "/api/v1/security/egress-allowlist", {}, {"host": "169.254.169.254"}, hdr, "127.0.0.1", None)[0] == 400
    assert api.handle(store, "POST", "/api/v1/security/egress-allowlist", {}, {"host": "cluster.local"}, hdr, "127.0.0.1", None)[0] == 400

    # Custom inbound webhook
    _, ih, _ = api.handle(
        store, "POST", "/api/v1/connectors/inbound-hooks", {},
        {"name": "Form Hook", "target_action": "lead.capture", "field_map": {"name": "u.n", "phone": "u.p", "text": "u.m"}},
        hdr, "127.0.0.1", None,
    )
    hid = ih["inbound_hook"]["id"]
    sec_tok = ih["signing_secret_once"]
    body = {"u": {"n": "Kiran Patil", "p": "+919855667788", "m": "Interested in corner plot"}}
    raw = json.dumps(body).encode("utf-8")
    sig = hmac.new(sec_tok.encode(), raw, hashlib.sha256).hexdigest()
    st_wh, wh_doc, _ = api.handle(store, "POST", f"/api/v1/webhooks/custom/{hid}", {}, body, {"x-webhook-signature": sig}, "127.0.0.1", None, raw)
    assert st_wh == 200 and wh_doc["contact_id"].startswith("cnt_")

    # S2 document scanner
    pid = store.all("properties", tid)[0]["id"]
    exe_b64 = base64.b64encode(b"MZ\x90\x00executable").decode()
    pdf_b64 = base64.b64encode(b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF").decode()
    assert api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"content_type": "application/pdf", "content_b64": exe_b64}, hdr, "127.0.0.1", None)[0] == 422
    assert api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"content_type": "application/pdf", "content_b64": pdf_b64}, hdr, "127.0.0.1", None)[0] == 200

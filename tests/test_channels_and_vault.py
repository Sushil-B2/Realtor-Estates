"""
Tests for `v1.27.0` (`docs/38`):
  - Secret-manager / Vault runtime puller (`app/config.py`: Doppler, Infisical v3,
    HashiCorp KV-v2, production HTTPS enforcement)
  - Neon Auth session bridge (`app/neon.py` + `app/api.py`: Better Auth `/get-session`
    `200 null` rejection, active session normalisation, Stack Auth `/users/me`
    compatibility, `RE_AUTH_MODE=neon` local-login disablement & logout cache eviction)
  - Sprint S1 channel adapters (`app/channels.py` + `app/api.py` + `app/server.py`):
      * WhatsApp Cloud API v21.0: 24h window gate, template registry, plain-text
        hub challenge over live HTTP, `X-Hub-Signature-256` inbound + receipts
      * TRAI DLT SMS: 19-digit DLT template registry, `{#var#}` pattern check,
        gateway dispatch, signed DLR webhook
      * Voice telephony: DPDP/quiet-hours gate, outbound initiation, signed
        completion webhook
      * Outbound Razorpay `/v1/orders` Basic-Auth order creation + fail-closed `502`
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import conftest
import pytest

from app import api, channels, config as cfg, neon, security as sec
from app.store import Store


class _CarrierMock(BaseHTTPRequestHandler):
    log_message = lambda *a, **kw: None  # noqa: E731
    mode: str = "ok"
    calls: list = []
    auth_session: dict | None = None

    def _json(self, status: int, doc) -> None:
        raw = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/get-session":
            auth = self.headers.get("Authorization", "")
            if self.auth_session and auth == f"Bearer {self.auth_session['token']}":
                return self._json(200, {
                    "session": {"id": "nsess_01", "userId": "nusr_01", "expiresAt": "2099-01-01T00:00:00.000Z"},
                    "user": {"id": "nusr_01", "email": self.auth_session["email"], "name": "Cloud User", "emailVerified": True},
                })
            # Real Better Auth returns HTTP 200 with JSON `null` when unauthenticated!
            return self._json(200, None)
        if self.path == "/vault/doppler":
            return self._json(200, {"secrets": {"SMS_SENDER_ID": {"computed": "DPPLR1"}}})
        if self.path == "/vault/infisical":
            return self._json(200, {"secrets": [{"secretKey": "SMS_SENDER_ID", "secretValue": "INFS01"}]})
        if self.path == "/vault/kv2":
            return self._json(200, {"data": {"data": {"SMS_SENDER_ID": "HKVV02"}}})
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        self.calls.append((self.path, body, dict(self.headers)))
        if self.mode == "fail500":
            return self._json(500, {"error": "upstream_down"})
        if self.path == "/sign-out":
            self.__class__.auth_session = None
            return self._json(200, {"success": True})
        if self.path.endswith("/messages"):
            return self._json(200, {"messages": [{"id": f"wamid.t_{len(self.calls)}"}]})
        if self.path == "/sms":
            return self._json(200, {"message_id": f"sms.t_{len(self.calls)}", "status": "submitted"})
        if self.path == "/voice":
            return self._json(200, {"call_id": f"vcl.t_{len(self.calls)}", "status": "initiated"})
        if self.path == "/v1/orders":
            return self._json(200, {"id": f"order_RzpTest{len(self.calls):03d}", "status": "created"})
        self._json(404, {"error": "not_found"})


@pytest.fixture()
def mock_upstream():
    _CarrierMock.mode = "ok"
    _CarrierMock.calls = []
    _CarrierMock.auth_session = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CarrierMock)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", _CarrierMock
    finally:
        srv.shutdown()
        cfg._VAULT_VALUES.clear()
        neon.invalidate_auth_cache()


@pytest.fixture()
def isolated_store(tmp_path):
    store = Store(str(tmp_path / "store.json"))
    tid = store.db["tenants"][0]["id"]
    _, login_doc, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    return store, tid, login_doc["token"]


def test_secret_manager_loader_dialects_and_prod_https(mock_upstream, monkeypatch):
    base, _ = mock_upstream
    monkeypatch.delenv("SMS_SENDER_ID", raising=False)
    for path, expected in (("/vault/doppler", "DPPLR1"), ("/vault/infisical", "INFS01"), ("/vault/kv2", "HKVV02")):
        res = cfg.load_from_secret_manager(f"{base}{path}", "tok_test_12345678", production=False)
        assert res["SMS_SENDER_ID"] == expected
        assert cfg.get("SMS_SENDER_ID") == expected
        rep = cfg.secrets_report()
        entry = next(e for e in rep["secrets"] if e["name"] == "SMS_SENDER_ID")
        assert entry["source"] == "vault"

    with pytest.raises(RuntimeError, match="https://"):
        cfg.load_from_secret_manager(f"{base}/vault/kv2", "tok_test_12345678", production=True)


def test_neon_auth_rejects_200_null_and_enforces_single_auth_mode(mock_upstream, isolated_store, monkeypatch):
    base, mock_cls = mock_upstream
    store, tid, local_tok = isolated_store
    monkeypatch.setenv("NEON_AUTH_BASE_URL", base)
    neon.invalidate_auth_cache()

    # 1. Invalid token -> upstream returns HTTP 200 `null` -> MUST return None
    assert neon.verify_session_token("bad_token", use_cache=False) is None

    # 2. Valid Better Auth session mapped to owner@realtorestates.app
    n_tok = "ntok_" + secrets.token_urlsafe(18)
    mock_cls.auth_session = {"token": n_tok, "email": "owner@realtorestates.app"}
    verified = neon.verify_session_token(n_tok, use_cache=False)
    assert verified is not None
    assert verified["email"] == "owner@realtorestates.app"
    assert verified["_source"] == "neon-auth"

    # 3. Under RE_AUTH_MODE=neon: local login is 403 and local tokens are 401
    monkeypatch.setenv("RE_AUTH_MODE", "neon")
    st_login, err_login, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    assert st_login == 403
    assert err_login["error"]["code"] == "LOCAL_AUTH_DISABLED"

    st_local, _, _ = api.handle(
        store, "GET", "/api/v1/auth/me", {}, {},
        {"authorization": f"Bearer {local_tok}"}, "127.0.0.1", None,
    )
    assert st_local == 401

    # 4. Neon Auth token authenticates API, and logout revokes upstream + cache
    st_me, me_doc, _ = api.handle(
        store, "GET", "/api/v1/auth/me", {}, {},
        {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None,
    )
    assert st_me == 200
    assert me_doc["user"]["email"] == "owner@realtorestates.app"

    st_out, _, _ = api.handle(
        store, "POST", "/api/v1/auth/logout", {}, {},
        {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None,
    )
    assert st_out == 200
    st_after, _, _ = api.handle(
        store, "GET", "/api/v1/auth/me", {}, {},
        {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None,
    )
    assert st_after == 401


def test_whatsapp_24h_window_templates_and_signed_webhooks(mock_upstream, isolated_store, monkeypatch):
    base, mock_cls = mock_upstream
    store, tid, tok = isolated_store
    wh_secret = "whsec_" + secrets.token_hex(16)
    verify_tok = "vtok_" + secrets.token_hex(10)
    monkeypatch.setenv("WHATSAPP_API_BASE", f"{base}/v21.0")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "1055443322")
    monkeypatch.setenv("WHATSAPP_TOKEN", "EAALIVE_" + secrets.token_hex(16))
    monkeypatch.setenv("WHATSAPP_APP_SECRET", wh_secret)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", verify_tok)
    hdr = {"authorization": f"Bearer {tok}"}

    # Fresh contact -> 24h window is closed
    _, c_doc, _ = api.handle(
        store, "POST", "/api/v1/contacts", {},
        {"name": "Rohan Joshi", "phone": "+919811223344", "consent": {"status": "granted"}},
        hdr, "127.0.0.1", None,
    )
    cid = c_doc["contact"]["id"]

    st_closed, err_doc, _ = api.handle(
        store, "POST", f"/api/v1/inbox/{cid}/send", {},
        {"channel": "whatsapp", "text": "Free-form when closed", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_closed == 422
    assert err_doc["error"]["code"] == "WHATSAPP_WINDOW_CLOSED"

    # Register a custom workspace template and send it outside 24h window
    st_reg, reg_doc, _ = api.handle(
        store, "POST", "/api/v1/channels/whatsapp/templates", {},
        {"name": "plot_launch_invite", "category": "UTILITY", "body": "Namaste {{1}}, phase {{2}} plots are open."},
        hdr, "127.0.0.1", None,
    )
    assert st_reg == 200
    assert reg_doc["template"]["param_count"] == 2

    st_tpl, tpl_doc, _ = api.handle(
        store, "POST", f"/api/v1/inbox/{cid}/send", {},
        {
            "channel": "whatsapp",
            "template_name": "plot_launch_invite",
            "template_params": ["Rohan", "II"],
            "override_quiet_hours": True,
        },
        hdr, "127.0.0.1", None,
    )
    assert st_tpl == 200
    assert tpl_doc["delivery"]["mode"] == "live"
    wamid = tpl_doc["message"]["provider_message_id"]

    # Live HTTP GET check against in-process `app.server.Handler` for plain-text Meta challenge
    from app import server as server_mod
    edge = server_mod._HTTPServer(("127.0.0.1", 0), server_mod.Handler)
    t_edge = threading.Thread(target=edge.serve_forever, daemon=True)
    t_edge.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{edge.server_address[1]}/api/v1/webhooks/whatsapp"
            f"?hub.mode=subscribe&hub.verify_token={verify_tok}&hub.challenge=424242"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("text/plain")
            assert resp.read().decode("utf-8") == "424242"
    finally:
        edge.shutdown()

    # Signed inbound webhook opens 24h window + marks outbound template `delivered`
    wh_doc = {
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": "919811223344", "profile": {"name": "Rohan"}}],
                    "messages": [{"id": "wamid.in_01", "from": "919811223344", "timestamp": str(int(time.time())), "type": "text", "text": {"body": "Interested"}}],
                    "statuses": [{"id": wamid, "status": "delivered", "timestamp": str(int(time.time()))}],
                }
            }]
        }]
    }
    raw_wh = json.dumps(wh_doc).encode("utf-8")
    assert api.handle(store, "POST", "/api/v1/webhooks/whatsapp", {}, wh_doc, {"x-hub-signature-256": "sha256=bad"}, "127.0.0.1", None, raw_wh)[0] == 401

    good_sig = "sha256=" + hmac.new(wh_secret.encode(), raw_wh, hashlib.sha256).hexdigest()
    st_wh, wh_out, _ = api.handle(store, "POST", "/api/v1/webhooks/whatsapp", {}, wh_doc, {"x-hub-signature-256": good_sig}, "127.0.0.1", None, raw_wh)
    assert st_wh == 200
    assert wh_out == {"ok": True, "inbound": 1, "statuses": 1}

    # Free-form reply now succeeds inside the open 24h window
    st_open, open_doc, _ = api.handle(
        store, "POST", f"/api/v1/inbox/{cid}/send", {},
        {"channel": "whatsapp", "text": "Great, sending brochure!", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_open == 200
    assert open_doc["message"]["window_24h_open"] is True


def test_trai_dlt_sms_registry_and_dlr_webhook(mock_upstream, isolated_store, monkeypatch):
    base, _ = mock_upstream
    store, tid, tok = isolated_store
    sms_key = "smsk_" + secrets.token_hex(16)
    monkeypatch.setenv("SMS_API_URL", f"{base}/sms")
    monkeypatch.setenv("SMS_API_KEY", sms_key)
    monkeypatch.setenv("SMS_SENDER_ID", "SNRSUN")
    hdr = {"authorization": f"Bearer {tok}"}

    cid = store.all("contacts", tid)[0]["id"]
    store.update("contacts", tid, cid, {"consent": {"status": "granted"}})

    # Register workspace DLT template
    dlt_id = "1107179999000012345"
    st_reg, _ = api.handle(
        store, "POST", "/api/v1/channels/sms/templates", {},
        {"dlt_template_id": dlt_id, "name": "booking_hold", "sender_id": "SNRSUN", "pattern": "Plot {#var#} held for {#var#}. - Sunrise"},
        hdr, "127.0.0.1", None,
    )[:2]
    assert st_reg == 200

    # Mismatched copy is refused before carrier call
    st_bad, err_bad, _ = api.handle(
        store, "POST", f"/api/v1/inbox/{cid}/send", {},
        {"channel": "sms", "dlt_template_id": dlt_id, "text": "Freeform copy that violates DLT", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_bad == 422
    assert err_bad["error"]["code"] == "DLT_TEMPLATE_MISMATCH"

    # Matching copy dispatches live and DLR updates status
    st_ok, ok_doc, _ = api.handle(
        store, "POST", f"/api/v1/inbox/{cid}/send", {},
        {"channel": "sms", "dlt_template_id": dlt_id, "text": "Plot A-14 held for 48 hours. - Sunrise", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_ok == 200
    mid = ok_doc["message"]["provider_message_id"]

    dlr = {"provider_message_id": mid, "status": "delivered", "tenant_id": tid}
    raw = json.dumps(dlr).encode("utf-8")
    sig = hmac.new(sms_key.encode(), raw, hashlib.sha256).hexdigest()
    st_dlr, dlr_doc, _ = api.handle(store, "POST", "/api/v1/webhooks/sms", {}, dlr, {"x-sms-signature": sig}, "127.0.0.1", None, raw)
    assert st_dlr == 200
    assert dlr_doc["updated"] == 1


def test_voice_call_and_razorpay_live_and_fail_closed(mock_upstream, isolated_store, monkeypatch):
    base, mock_cls = mock_upstream
    store, tid, tok = isolated_store
    v_sec = "vsec_" + secrets.token_hex(16)
    monkeypatch.setenv("VOICE_API_URL", f"{base}/voice")
    monkeypatch.setenv("SARVAM_API_KEY", "srvm_" + secrets.token_hex(16))
    monkeypatch.setenv("VOICE_WEBHOOK_SECRET", v_sec)
    monkeypatch.setenv("RAZORPAY_API_BASE", base)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_key001")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzpsec_" + secrets.token_hex(16))
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    hdr = {"authorization": f"Bearer {tok}"}

    cid = store.all("contacts", tid)[0]["id"]
    store.update("contacts", tid, cid, {"consent": {"status": "granted"}})

    st_v, v_doc, _ = api.handle(
        store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
        {"language": "mr", "override_quiet_hours": True},
        hdr, "127.0.0.1", None,
    )
    assert st_v == 200
    call_id = v_doc["dispatch"]["provider_call_id"]

    v_wh = {"tenant_id": tid, "provider_call_id": call_id, "outcome": "answered", "duration_sec": 65, "transcript": "Buyer confirmed Saturday site visit."}
    raw_v = json.dumps(v_wh).encode("utf-8")
    sig_v = hmac.new(v_sec.encode(), raw_v, hashlib.sha256).hexdigest()
    st_vwh, vwh_doc, _ = api.handle(store, "POST", "/api/v1/webhooks/voice", {}, v_wh, {"x-voice-signature": sig_v}, "127.0.0.1", None, raw_v)
    assert st_vwh == 200
    assert vwh_doc["activity"]["duration_sec"] == 65
    assert vwh_doc["activity"]["recording"]["stored_in"] == "inline"

    # Live Razorpay order creation -> 200, then upstream outage -> fail-closed 502
    deal_id = store.all("deals", tid)[0]["id"]
    st_p, p_doc, _ = api.handle(
        store, "POST", "/api/v1/payments/orders", {},
        {"deal_id": deal_id, "kind": "token", "amount_paise": 2_500_000},
        hdr, "127.0.0.1", None,
    )
    assert st_p == 200
    assert p_doc["checkout"]["provider_call"] == "live"
    assert p_doc["checkout"]["order_id"].startswith("order_RzpTest")

    mock_cls.mode = "fail500"
    st_fail, err_fail, _ = api.handle(
        store, "POST", "/api/v1/payments/orders", {},
        {"deal_id": deal_id, "kind": "token", "amount_paise": 2_500_000},
        hdr, "127.0.0.1", None,
    )
    assert st_fail == 502
    assert err_fail["error"]["code"] == "PAYMENT_PROVIDER_ERROR"

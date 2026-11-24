#!/usr/bin/env python3
"""
End-to-end proof for `v1.27.0` (`docs/38`):
  1. Live Neon Auth (Better Auth `/get-session`, `/sign-up/email`, `/sign-out`)
     against `NEON_AUTH_BASE_URL`, proving:
       - `HTTP 200 null` on invalid tokens is rejected (`None`),
       - a live Neon Auth session token authenticates `/api/v1/auth/me` under
         `RE_AUTH_MODE=neon` while local password login is disabled (`403`),
       - `POST /api/v1/auth/logout` revokes the session upstream and evicts cache.
  2. Secret-Manager / Vault runtime puller (`docs/14` #1) across Doppler,
     Infisical v3, and Vault KV-v2 JSON dialects (in-memory, zero disk writes).
  3. Phase 1 S1 carrier adapters + signed webhooks:
       - WhatsApp Cloud API v21.0: 24h customer-service window gate, approved
         templates, plain-text hub challenge, `X-Hub-Signature-256` inbound +
         delivery/read receipts.
       - TRAI DLT SMS: 19-digit DLT template registry, `{#var#}` pattern check,
         live gateway dispatch, signed DLR webhook.
       - Voice Telephony: outbound call initiation + signed completion webhook
         archiving WAV clip to Neon S3 (`recordings/tenants/<tid>/…`).
  4. Outbound Razorpay `/v1/orders` Basic-Auth order creation echoing `notes`.

Zero secret values are printed or written to disk.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RE_DB", "json")

from app import api, channels, config as cfg, neon, security as sec  # noqa: E402
from app.store import Store, nid, now  # noqa: E402


class _MockUpstreamHandler(BaseHTTPRequestHandler):
    log_message = lambda *a, **kw: None  # noqa: E731
    state: dict = {
        "wa_calls": [],
        "sms_calls": [],
        "voice_calls": [],
        "rzp_calls": [],
        "vault_dialect": "doppler",
        "vault_token": "",
    }

    def _reply(self, code: int, doc: dict) -> None:
        raw = json.dumps(doc).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.startswith("/vault"):
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {self.state['vault_token']}":
                return self._reply(401, {"error": "unauthorized"})
            d = self.state["vault_dialect"]
            if d == "doppler":
                return self._reply(200, {"secrets": {"SMS_SENDER_ID": {"computed": "VAULT1"}}})
            if d == "infisical":
                return self._reply(200, {"secrets": [{"secretKey": "SMS_SENDER_ID", "secretValue": "INFIS1"}]})
            return self._reply(200, {"data": {"data": {"SMS_SENDER_ID": "KVV201"}}})
        self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path.endswith("/messages"):
            self.state["wa_calls"].append(body)
            return self._reply(200, {"messaging_product": "whatsapp", "messages": [{"id": f"wamid.live_{len(self.state['wa_calls'])}"}]})
        if self.path == "/sms/send":
            self.state["sms_calls"].append(body)
            return self._reply(200, {"message_id": f"sms.live_{len(self.state['sms_calls'])}", "status": "submitted"})
        if self.path == "/voice/calls":
            self.state["voice_calls"].append(body)
            return self._reply(200, {"call_id": f"vcl.live_{len(self.state['voice_calls'])}", "status": "initiated"})
        if self.path == "/v1/orders":
            self.state["rzp_calls"].append({
                "auth": self.headers.get("Authorization", "").startswith("Basic "),
                "body": body,
            })
            return self._reply(200, {"id": f"order_liveRzp{len(self.state['rzp_calls']):04d}", "status": "created"})
        self._reply(404, {"error": "not_found"})


def _extract_neon_session_token(resp_headers, resp_body: dict, jar: http.cookiejar.CookieJar) -> str:
    for ck in jar:
        if "session_token" in ck.name and ck.value:
            return ck.value
    tok = str(resp_body.get("token") or (resp_body.get("session") or {}).get("token") or "")
    return tok


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    s3_cleanup_keys: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))

    tmp_dir = tempfile.TemporaryDirectory(prefix="re_proof_v127_")
    store = Store(os.path.join(tmp_dir.name, "store.json"))
    tid = store.db["tenants"][0]["id"]
    owner = next(u for u in store.db["users"] if u["tenant_id"] == tid and u["role"] == "owner")

    # Start loopback upstream server for Vault + Meta + DLT SMS + Voice + Razorpay
    vault_tok = "vtok_" + secrets.token_urlsafe(18)
    _MockUpstreamHandler.state["vault_token"] = vault_tok
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _MockUpstreamHandler)
    up_port = upstream.server_address[1]
    up_base = f"http://127.0.0.1:{up_port}"
    t_up = threading.Thread(target=upstream.serve_forever, daemon=True)
    t_up.start()

    saved_env = dict(os.environ)
    try:
        # ------------------------------------------------------------------
        # 1. Secret-Manager / Vault Runtime Loader (`docs/14` #1)
        # ------------------------------------------------------------------
        os.environ.pop("SMS_SENDER_ID", None)
        for dialect, expected in (("doppler", "VAULT1"), ("infisical", "INFIS1"), ("vault", "KVV201")):
            _MockUpstreamHandler.state["vault_dialect"] = dialect
            loaded = cfg.load_from_secret_manager(f"{up_base}/vault", vault_tok, dialect, production=False)
            got = cfg.get("SMS_SENDER_ID", "")
            rep = cfg.secrets_report()
            src = next(e["source"] for e in rep["secrets"] if e["name"] == "SMS_SENDER_ID")
            record(f"vault_loader_{dialect}", loaded.get("SMS_SENDER_ID") == expected and got == expected and src == "vault", f"source={src}")

        prod_rejected = False
        try:
            cfg.load_from_secret_manager(f"{up_base}/vault", vault_tok, "vault", production=True)
        except RuntimeError:
            prod_rejected = True
        record("vault_loader_prod_https_enforced", prod_rejected, "plain http:// refused in production")
        cfg._VAULT_VALUES.clear()

        # ------------------------------------------------------------------
        # 2. Live Neon Auth Cloud Proof (`Backend plan #2`)
        # ------------------------------------------------------------------
        if neon.auth_configured():
            neon.invalidate_auth_cache()
            null_res = neon.verify_session_token("forged_invalid_token_" + secrets.token_hex(8), use_cache=False)
            record("neon_auth_rejects_200_null", null_res is None, "Better Auth 200 `null` -> None")

            auth_base = cfg.get("NEON_AUTH_BASE_URL", "").rstrip("/")
            proof_email = f"proof.{int(time.time())}.{secrets.token_hex(3)}@realtorestates.example"
            proof_pw = "ProofNeon#" + secrets.token_urlsafe(12)
            # Provision matching CRM user in isolated in-memory store
            crm_user = store.insert(
                "users",
                tid,
                {
                    "name": "Neon Auth Proof Operator",
                    "email": proof_email,
                    "role": "admin",
                    "password_hash": sec.hash_password(proof_pw),
                    "active": True,
                },
            )
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            signup_req = urllib.request.Request(
                auth_base + "/sign-up/email",
                data=json.dumps({"email": proof_email, "password": proof_pw, "name": "Neon Proof"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json", "Origin": auth_base},
                method="POST",
            )
            with opener.open(signup_req, timeout=10) as resp:
                signup_doc = json.loads(resp.read().decode("utf-8"))
                n_tok = _extract_neon_session_token(resp.headers, signup_doc, jar)

            verified = neon.verify_session_token(n_tok, use_cache=False)
            record(
                "neon_auth_live_introspection",
                bool(verified and verified.get("email") == proof_email and verified.get("_source") == "neon-auth"),
                f"verified={bool(verified)}",
            )

            os.environ["RE_AUTH_MODE"] = "neon"
            st_login, _, _ = api.handle(
                store, "POST", "/api/v1/auth/login", {},
                {"email": owner["email"], "password": "any"}, {}, "127.0.0.1", None
            )
            record("neon_auth_mode_disables_local_login", st_login == 403, f"status={st_login}")

            st_me, me_doc, _ = api.handle(
                store, "GET", "/api/v1/auth/me", {}, {},
                {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None
            )
            record(
                "neon_auth_mode_authenticates_api",
                st_me == 200 and (me_doc.get("user") or {}).get("id") == crm_user["id"],
                f"status={st_me} role={(me_doc.get('user') or {}).get('role')}",
            )

            st_out, out_doc, _ = api.handle(
                store, "POST", "/api/v1/auth/logout", {}, {},
                {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None
            )
            st_after, _, _ = api.handle(
                store, "GET", "/api/v1/auth/me", {}, {},
                {"authorization": f"Bearer {n_tok}"}, "127.0.0.1", None
            )
            record(
                "neon_auth_logout_revokes_upstream",
                st_out == 200 and st_after == 401,
                f"logout={st_out} post_logout_me={st_after}",
            )
            os.environ["RE_AUTH_MODE"] = "local"
        else:
            record("neon_auth_configured", False, "NEON_AUTH_BASE_URL missing")

        # ------------------------------------------------------------------
        # 3. Sprint S1 Carrier Adapters + Webhooks + Razorpay `/v1/orders`
        # ------------------------------------------------------------------
        os.environ["RE_AUTH_MODE"] = "local"
        # Configure live adapters against loopback mock carrier endpoints
        wa_secret = "whsec_wa_" + secrets.token_urlsafe(20)
        wa_verify = "verify_" + secrets.token_urlsafe(12)
        sms_key = "smskey_" + secrets.token_urlsafe(20)
        voice_key = "srvm_" + secrets.token_urlsafe(20)
        voice_wh_sec = "vwh_" + secrets.token_urlsafe(20)
        rzp_id = "rzp_live_proof01"
        rzp_sec = "rzpsec_" + secrets.token_urlsafe(20)

        os.environ.update({
            "WHATSAPP_API_BASE": f"{up_base}/v21.0",
            "WHATSAPP_PHONE_ID": "109988776655",
            "WHATSAPP_TOKEN": "EAAPROOF_" + secrets.token_urlsafe(20),
            "WHATSAPP_VERIFY_TOKEN": wa_verify,
            "WHATSAPP_APP_SECRET": wa_secret,
            "SMS_API_URL": f"{up_base}/sms/send",
            "SMS_API_KEY": sms_key,
            "SMS_SENDER_ID": "SNRSUN",
            "SMS_ENTITY_ID": "1101500000000099881",
            "VOICE_API_URL": f"{up_base}/voice/calls",
            "SARVAM_API_KEY": voice_key,
            "VOICE_WEBHOOK_SECRET": voice_wh_sec,
            "RAZORPAY_API_BASE": up_base,
            "RAZORPAY_KEY_ID": rzp_id,
            "RAZORPAY_KEY_SECRET": rzp_sec,
        })

        # Create a local owner session via the real `/api/v1/auth/login` route
        _, login_doc, _ = api.handle(
            store, "POST", "/api/v1/auth/login", {},
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            {}, "127.0.0.1", None,
        )
        owner_tok = login_doc["token"]
        auth_hdr = {"authorization": f"Bearer {owner_tok}"}

        # Create a fresh consented contact with NO prior inbound message (24h window CLOSED)
        contact_phone = "+919823456789"
        st_c, c_doc, _ = api.handle(
            store, "POST", "/api/v1/contacts", {},
            {
                "name": "Meera Kulkarni",
                "phone": contact_phone,
                "city": "Pune",
                "consent": {"status": "granted", "source": "web_form"},
            },
            auth_hdr, "127.0.0.1", None,
        )
        cid = c_doc["contact"]["id"]

        # 3a. Free-form WhatsApp outside 24h window -> 422 WHATSAPP_WINDOW_CLOSED
        st_w_closed, err_closed, _ = api.handle(
            store, "POST", f"/api/v1/inbox/{cid}/send", {},
            {"channel": "whatsapp", "text": "Hello outside 24h", "override_quiet_hours": True},
            auth_hdr, "127.0.0.1", None,
        )
        record(
            "whatsapp_24h_window_blocks_freeform",
            st_w_closed == 422 and (err_closed.get("error") or {}).get("code") == "WHATSAPP_WINDOW_CLOSED",
            f"status={st_w_closed}",
        )

        # 3b. Approved WhatsApp template succeeds outside 24h window
        st_w_tpl, tpl_doc, _ = api.handle(
            store, "POST", f"/api/v1/inbox/{cid}/send", {},
            {
                "channel": "whatsapp",
                "template_name": "missed_call_recovery",
                "template_params": ["Meera", "11:15 AM"],
                "override_quiet_hours": True,
            },
            auth_hdr, "127.0.0.1", None,
        )
        wamid_out = ((tpl_doc.get("message") or {}).get("provider_message_id")) or ""
        record(
            "whatsapp_template_live_dispatch",
            st_w_tpl == 200 and wamid_out.startswith("wamid.live_")
            and _MockUpstreamHandler.state["wa_calls"][-1]["type"] == "template",
            f"wamid={wamid_out}",
        )

        # 3c. Hub challenge verification + forged webhook rejection + signed inbound message
        st_ch, ch_doc, _ = api.handle(
            store, "GET", "/api/v1/webhooks/whatsapp",
            {"hub.mode": ["subscribe"], "hub.verify_token": [wa_verify], "hub.challenge": ["774411"]},
            {}, {}, "127.0.0.1", None,
        )
        record("whatsapp_webhook_challenge", st_ch == 200 and ch_doc.get("_plain_text") == "774411")

        inbound_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "109988776655"},
                        "contacts": [{"wa_id": "919823456789", "profile": {"name": "Meera"}}],
                        "messages": [{
                            "id": "wamid.inbound_proof_1",
                            "from": "919823456789",
                            "timestamp": str(now()),
                            "type": "text",
                            "text": {"body": "Yes, please share the RERA pack!"},
                        }],
                        "statuses": [{
                            "id": wamid_out,
                            "status": "read",
                            "timestamp": str(now()),
                        }],
                    }
                }]
            }]
        }
        raw_in = json.dumps(inbound_payload).encode("utf-8")
        st_bad_sig, _, _ = api.handle(
            store, "POST", "/api/v1/webhooks/whatsapp", {}, inbound_payload,
            {"x-hub-signature-256": "sha256=" + "0" * 64}, "127.0.0.1", None, raw_in,
        )
        good_sig = "sha256=" + hmac.new(wa_secret.encode(), raw_in, hashlib.sha256).hexdigest()
        st_good_sig, wh_res, _ = api.handle(
            store, "POST", "/api/v1/webhooks/whatsapp", {}, inbound_payload,
            {"x-hub-signature-256": good_sig}, "127.0.0.1", None, raw_in,
        )
        record(
            "whatsapp_webhook_hmac_inbound_and_receipt",
            st_bad_sig == 401 and st_good_sig == 200 and wh_res.get("inbound") == 1 and wh_res.get("statuses") == 1,
            f"forged={st_bad_sig} signed={st_good_sig}",
        )

        # 3d. Now that buyer messaged inbound (<24h), free-form WhatsApp succeeds!
        st_w_open, open_doc, _ = api.handle(
            store, "POST", f"/api/v1/inbox/{cid}/send", {},
            {"channel": "whatsapp", "text": "Sharing the 7/12 & MahaRERA certificate right away.", "override_quiet_hours": True},
            auth_hdr, "127.0.0.1", None,
        )
        record(
            "whatsapp_24h_window_opens_after_inbound",
            st_w_open == 200 and (open_doc.get("message") or {}).get("window_24h_open") is True
            and _MockUpstreamHandler.state["wa_calls"][-1]["type"] == "text",
            f"status={st_w_open}",
        )

        # 3e. TRAI DLT SMS: unregistered rejected -> registered DLT template dispatched + DLR webhook
        st_sms_bad, sms_err, _ = api.handle(
            store, "POST", f"/api/v1/inbox/{cid}/send", {},
            {"channel": "sms", "text": "Unregistered promo blast!", "override_quiet_hours": True},
            auth_hdr, "127.0.0.1", None,
        )
        st_sms_ok, sms_ok_doc, _ = api.handle(
            store, "POST", f"/api/v1/inbox/{cid}/send", {},
            {
                "channel": "sms",
                "dlt_template_id": "1107170000000010002",
                "text": "Namaste Meera, we missed your call. Reply YES for RERA doc pack of Green Valley Plots. - Sunrise Land",
                "override_quiet_hours": True,
            },
            auth_hdr, "127.0.0.1", None,
        )
        sms_msg_id = ((sms_ok_doc.get("message") or {}).get("provider_message_id")) or ""
        dlr_body = {"provider_message_id": sms_msg_id, "status": "delivered", "tenant_id": tid}
        dlr_raw = json.dumps(dlr_body).encode("utf-8")
        dlr_sig = "sha256=" + hmac.new(sms_key.encode(), dlr_raw, hashlib.sha256).hexdigest()
        st_dlr, dlr_res, _ = api.handle(
            store, "POST", "/api/v1/webhooks/sms", {}, dlr_body,
            {"x-sms-signature": dlr_sig}, "127.0.0.1", None, dlr_raw,
        )
        record(
            "trai_dlt_sms_gate_dispatch_and_dlr",
            st_sms_bad == 422 and st_sms_ok == 200 and sms_msg_id.startswith("sms.live_")
            and st_dlr == 200 and dlr_res.get("updated") == 1,
            f"blocked={st_sms_bad} live={st_sms_ok} dlr={st_dlr}",
        )

        # 3f. Voice call initiation + signed voice completion webhook -> Neon S3 recording archive
        st_vc, vc_doc, _ = api.handle(
            store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
            {"language": "mr", "override_quiet_hours": True},
            auth_hdr, "127.0.0.1", None,
        )
        v_call_id = ((vc_doc.get("dispatch") or {}).get("provider_call_id")) or ""
        wav_stub = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"
        v_wh_body = {
            "tenant_id": tid,
            "provider_call_id": v_call_id,
            "outcome": "answered",
            "duration_sec": 74,
            "recording_b64": base64.b64encode(wav_stub).decode("ascii"),
            "recording_content_type": "audio/wav",
        }
        v_wh_raw = json.dumps(v_wh_body).encode("utf-8")
        v_wh_sig = "sha256=" + hmac.new(voice_wh_sec.encode(), v_wh_raw, hashlib.sha256).hexdigest()
        st_vwh, vwh_doc, _ = api.handle(
            store, "POST", "/api/v1/webhooks/voice", {}, v_wh_body,
            {"x-voice-signature": v_wh_sig}, "127.0.0.1", None, v_wh_raw,
        )
        rec_meta = ((vwh_doc.get("activity") or {}).get("recording")) or {}
        if rec_meta.get("object_key"):
            s3_cleanup_keys.append(rec_meta["object_key"])
        expected_store = "object_storage" if neon.storage_configured() else "inline"
        record(
            "voice_call_and_recording_webhook",
            st_vc == 200 and v_call_id.startswith("vcl.live_") and st_vwh == 200
            and rec_meta.get("stored_in") == expected_store,
            f"call={v_call_id} stored_in={rec_meta.get('stored_in')}",
        )

        # 3g. Outbound Razorpay `/v1/orders` Basic-Auth order creation
        deal_id = store.all("deals", tid)[0]["id"]
        st_po, po_doc, _ = api.handle(
            store, "POST", "/api/v1/payments/orders", {},
            {"deal_id": deal_id, "kind": "token", "amount_paise": 5_000_000},
            auth_hdr, "127.0.0.1", None,
        )
        checkout = po_doc.get("checkout") or {}
        record(
            "razorpay_live_order_creation",
            st_po == 200 and checkout.get("provider_call") == "live"
            and str(checkout.get("order_id") or "").startswith("order_liveRzp")
            and _MockUpstreamHandler.state["rzp_calls"][-1]["auth"] is True,
            f"order_id={checkout.get('order_id')}",
        )

    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        upstream.shutdown()
        for key in s3_cleanup_keys:
            try:
                neon.delete_object(key)
            except Exception:
                pass

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"\nPROOF SUMMARY: {passed}/{total} PASS")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

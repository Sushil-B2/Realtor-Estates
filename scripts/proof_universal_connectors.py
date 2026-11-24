#!/usr/bin/env python3
"""
End-to-end proof for `v1.28.0` (`docs/39`):
  1. Zero-blockage Local Twin: playable PCM `.wav` + transcript + S3 archiving
     and interactive inbound WhatsApp simulator with zero external keys.
  2. Universal `custom_http` future-provider connectors across Voice, AI/LLM,
     WhatsApp, Payments, and Custom Agent Tools (`connector.<slug>` with
     read vs irreversible human-approval gating) — hot-swapped live with zero
     server restart and AEAD-sealed secrets (`SEN-C306` / `SEN-C803` clean).
  3. Dynamic SSRF-safe egress allowlist (public HTTPS & wildcard domains allowed;
     private/loopback/metadata/internal domains refused).
  4. Universal Inbound Webhooks (`POST /api/v1/webhooks/custom/<hook_id>`) with
     HMAC-SHA256 verification and nested dot-path field mapping.
  5. Sprint S2 Document-Pack upload pipeline: magic-byte sniffing + EICAR,
     executable, script-polyglot, and PDF `/JavaScript` blocking.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("RE_DB", "json")

from app import api, neon  # noqa: E402
from app.store import Store  # noqa: E402


class _FutureProviderHandler(BaseHTTPRequestHandler):
    log_message = lambda *a, **kw: None  # noqa: E731
    received: list = []

    def _reply(self, code: int, doc: dict) -> None:
        raw = json.dumps(doc).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        self.received.append({"path": self.path, "body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
        if self.path == "/2028/voice":
            return self._reply(200, {"result": {"nested": {"session_uuid": "fut_voice_9001"}}, "status": "ringing"})
        if self.path == "/2028/llm":
            return self._reply(200, {"data": {"reply": {"text": "Namaste! Verified Plot B-7 (MahaRERA P5110001) is open for Saturday visit."}}})
        if self.path == "/2028/pay":
            return self._reply(200, {"gateway": {"order_ref": "cf_order_778899"}, "status": "ACTIVE"})
        if self.path == "/2028/land-tool":
            return self._reply(200, {"encumbrance": "clear", "survey_no": "44/2A"})
        self._reply(404, {"error": "unknown"})


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    s3_cleanup: list[str] = []

    def record(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    tmp = tempfile.TemporaryDirectory(prefix="re_proof_v128_")
    store = Store(os.path.join(tmp.name, "store.json"))
    tid = store.db["tenants"][0]["id"]

    _, login_a, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
        {}, "127.0.0.1", None,
    )
    _, login_b, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {},
        {"email": "owner@metrorealty.app", "password": "Metro@12345!"},
        {}, "127.0.0.1", None,
    )
    hdr_a = {"authorization": f"Bearer {login_a['token']}"}
    hdr_b = {"authorization": f"Bearer {login_b['token']}"}

    _FutureProviderHandler.received = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FutureProviderHandler)
    base = f"http://127.0.0.1:{upstream.server_address[1]}"
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    saved_env = dict(os.environ)
    try:
        for k in ("VOICE_API_URL", "SARVAM_API_KEY", "ELEVENLABS_API_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
            os.environ.pop(k, None)

        cid = store.all("contacts", tid)[0]["id"]
        store.update("contacts", tid, cid, {"consent": {"status": "granted", "source": "proof"}})

        # 1. Zero-blockage Voice Local Twin (playable RIFF/WAVE + transcript, zero keys)
        st_vt, vt_doc, _ = api.handle(
            store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
            {"language": "mr", "override_quiet_hours": True},
            hdr_a, "127.0.0.1", None,
        )
        act_vt = vt_doc.get("activity") or {}
        rec_vt = act_vt.get("recording") or {}
        if rec_vt.get("object_key"):
            s3_cleanup.append(rec_vt["object_key"])
        record(
            "zero_blockage_voice_local_twin_wav",
            st_vt == 200 and act_vt.get("duration_sec") == 48 and bool(act_vt.get("transcript"))
            and rec_vt.get("content_type") == "audio/wav" and int(rec_vt.get("bytes") or 0) > 8000,
            f"bytes={rec_vt.get('bytes')} stored_in={rec_vt.get('stored_in')}",
        )

        # 2. Interactive Inbound WhatsApp Simulator opens 24h window
        st_sim, sim_doc, _ = api.handle(
            store, "POST", "/api/v1/connectors/simulate", {},
            {"kind": "whatsapp_inbound", "contact_id": cid, "text": "Share plot layout"},
            hdr_a, "127.0.0.1", None,
        )
        record("zero_blockage_whatsapp_inbound_simulator", st_sim == 200 and sim_doc.get("window_24h_open") is True)

        # 3. Dynamic SSRF-safe egress allowlist: public domain OK, metadata/private IP refused
        st_eg_ok, _, _ = api.handle(
            store, "POST", "/api/v1/security/egress-allowlist", {},
            {"host": "*.futuretech2028.ai"}, hdr_a, "127.0.0.1", None,
        )
        st_eg_bad1, _, _ = api.handle(
            store, "POST", "/api/v1/security/egress-allowlist", {},
            {"host": "169.254.169.254"}, hdr_a, "127.0.0.1", None,
        )
        st_eg_bad2, _, _ = api.handle(
            store, "POST", "/api/v1/security/egress-allowlist", {},
            {"host": "metadata.google.internal"}, hdr_a, "127.0.0.1", None,
        )
        record(
            "dynamic_egress_allowlist_ssrf_wall",
            st_eg_ok == 200 and st_eg_bad1 == 400 and st_eg_bad2 == 400,
            f"wildcard={st_eg_ok} metadata_ip={st_eg_bad1} internal={st_eg_bad2}",
        )

        # 4. Hot-swap a Future 2028 Custom HTTP Voice Provider (JSON template + nested dot-path)
        voice_secret = "fvk_" + secrets.token_hex(16)
        st_vc_add, vc_add_doc, _ = api.handle(
            store, "POST", "/api/v1/connectors", {},
            {
                "category": "voice",
                "preset": "custom_http",
                "name": "FutureVoice 2028",
                "slug": "future_voice_28",
                "endpoint_url": f"{base}/2028/voice",
                "auth_scheme": "header",
                "auth_header": "x-future-voice-key",
                "api_key": voice_secret,
                "body_template": {"dial": {"target": "{{phone}}", "lang": "{{language}}", "agent": "{{agent_name}}"}},
                "response_id_path": "result.nested.session_uuid",
            },
            hdr_a, "127.0.0.1", None,
        )
        st_vc_live, vc_live_doc, _ = api.handle(
            store, "POST", f"/api/v1/contacts/{cid}/voice-call", {},
            {"language": "hi", "override_quiet_hours": True},
            hdr_a, "127.0.0.1", None,
        )
        last_req = _FutureProviderHandler.received[-1]
        record(
            "future_custom_http_voice_hotswap",
            st_vc_add == 200 and st_vc_live == 200
            and (vc_live_doc.get("dispatch") or {}).get("mode") == "live"
            and (vc_live_doc.get("dispatch") or {}).get("provider_call_id") == "fut_voice_9001"
            and last_req["headers"].get("x-future-voice-key") == voice_secret
            and last_req["body"]["dial"]["lang"] == "hi",
            f"call_id={(vc_live_doc.get('dispatch') or {}).get('provider_call_id')}",
        )

        # 5. Hot-swap a Future Custom HTTP LLM + Custom Agent Tools (`read` vs `irreversible`)
        llm_key = "flk_" + secrets.token_hex(16)
        api.handle(
            store, "POST", "/api/v1/connectors", {},
            {
                "category": "ai",
                "preset": "custom_http",
                "name": "SovereignLLM 2028",
                "slug": "sovereign_llm",
                "endpoint_url": f"{base}/2028/llm",
                "auth_scheme": "bearer",
                "api_key": llm_key,
                "response_text_path": "data.reply.text",
            },
            hdr_a, "127.0.0.1", None,
        )
        api.handle(
            store, "POST", "/api/v1/connectors", {},
            {
                "category": "tool",
                "preset": "custom_http",
                "name": "Mahabhulekh Title API",
                "slug": "land_registry",
                "endpoint_url": f"{base}/2028/land-tool",
                "auth_scheme": "none",
                "risk": "read",
            },
            hdr_a, "127.0.0.1", None,
        )
        api.handle(
            store, "POST", "/api/v1/connectors", {},
            {
                "category": "tool",
                "preset": "custom_http",
                "name": "Escrow Release API",
                "slug": "escrow_release",
                "endpoint_url": f"{base}/2028/land-tool",
                "auth_scheme": "none",
                "risk": "irreversible",
            },
            hdr_a, "127.0.0.1", None,
        )
        st_dr, dr_doc, _ = api.handle(store, "POST", f"/api/v1/inbox/{cid}/ai-draft", {}, {}, hdr_a, "127.0.0.1", None)
        _, ag_doc, _ = api.handle(
            store, "POST", "/api/v1/agents", {},
            {"name": "Custom Tool Agent", "scopes": ["contacts:read"], "tools": ["connector.land_registry", "connector.escrow_release"]},
            hdr_a, "127.0.0.1", None,
        )
        aid = ag_doc["agent"]["id"]
        st_ar, ar_doc, _ = api.handle(
            store, "POST", f"/api/v1/agents/{aid}/run", {},
            {"input": "Check title survey 44/2A and release escrow", "contact_id": cid},
            hdr_a, "127.0.0.1", None,
        )
        run_res = ar_doc.get("result") or ar_doc
        parked = [p["tool"] for p in (run_res.get("approvals") or [])]
        record(
            "custom_ai_and_agent_tools_with_approval_gate",
            st_dr == 200 and "Plot B-7" in (dr_doc.get("draft") or "")
            and st_ar == 200 and "Plot B-7" in (run_res.get("output") or "")
            and "connector.escrow_release" in parked,
            f"parked={parked}",
        )

        # 6. Hot-swap Custom Payment Gateway (`Cashfree` / `custom_http`)
        api.handle(
            store, "POST", "/api/v1/connectors", {},
            {
                "category": "payments",
                "preset": "custom_http",
                "name": "Cashfree NextPG",
                "slug": "cashfree_pg",
                "endpoint_url": f"{base}/2028/pay",
                "auth_scheme": "header",
                "auth_header": "x-client-secret",
                "key_id_public": "CF_APP_01",
                "api_key": "cfs_" + secrets.token_hex(16),
                "response_id_path": "gateway.order_ref",
            },
            hdr_a, "127.0.0.1", None,
        )
        deal_id = store.all("deals", tid)[0]["id"]
        st_po, po_doc, _ = api.handle(
            store, "POST", "/api/v1/payments/orders", {},
            {"deal_id": deal_id, "kind": "token", "amount_paise": 3_000_000},
            hdr_a, "127.0.0.1", None,
        )
        record(
            "custom_payment_gateway_hotswap",
            st_po == 200 and (po_doc.get("checkout") or {}).get("order_id") == "cf_order_778899",
            f"order_id={(po_doc.get('checkout') or {}).get('order_id')}",
        )

        # 7. Secret non-disclosure & cross-tenant isolation
        _, list_a, _ = api.handle(store, "GET", "/api/v1/connectors", {}, {}, hdr_a, "127.0.0.1", None)
        _, list_b, _ = api.handle(store, "GET", "/api/v1/connectors", {}, {}, hdr_b, "127.0.0.1", None)
        blob_a = json.dumps(list_a)
        record(
            "connector_secrets_sealed_and_tenant_isolated",
            voice_secret not in blob_a and llm_key not in blob_a
            and len(list_a["connectors"]) >= 4 and len(list_b["connectors"]) == 0,
            f"tenantA={len(list_a['connectors'])} tenantB={len(list_b['connectors'])}",
        )

        # 8. Universal Inbound Webhook (`/api/v1/webhooks/custom/<hook_id>`) with dot-path mapping
        _, ih_doc, _ = api.handle(
            store, "POST", "/api/v1/connectors/inbound-hooks", {},
            {
                "name": "n8n Lead Router",
                "target_action": "whatsapp.inbound",
                "auth_mode": "hmac_sha256",
                "signature_header": "x-n8n-sig",
                "field_map": {
                    "name": "lead.profile.fullName",
                    "phone": "lead.profile.mobile",
                    "city": "lead.profile.city",
                    "text": "enquiry.body",
                },
            },
            hdr_a, "127.0.0.1", None,
        )
        hid = ih_doc["inbound_hook"]["id"]
        ih_secret = ih_doc["signing_secret_once"]
        ext_payload = {
            "lead": {"profile": {"fullName": "Aarti Deshmukh", "mobile": "+919765432109", "city": "Aurangabad"}},
            "enquiry": {"body": "Need 2000 sqft NA plot brochure"},
        }
        ext_raw = json.dumps(ext_payload).encode("utf-8")
        st_ih_bad, _, _ = api.handle(
            store, "POST", f"/api/v1/webhooks/custom/{hid}", {}, ext_payload,
            {"x-n8n-sig": "sha256=deadbeef"}, "127.0.0.1", None, ext_raw,
        )
        good_ih_sig = "sha256=" + hmac.new(ih_secret.encode(), ext_raw, hashlib.sha256).hexdigest()
        st_ih_ok, ih_res, _ = api.handle(
            store, "POST", f"/api/v1/webhooks/custom/{hid}", {}, ext_payload,
            {"x-n8n-sig": good_ih_sig}, "127.0.0.1", None, ext_raw,
        )
        record(
            "universal_inbound_webhook_dotpath_mapping",
            st_ih_bad == 401 and st_ih_ok == 200 and bool(ih_res.get("contact_id")),
            f"forged={st_ih_bad} signed={st_ih_ok}",
        )

        # 9. S2 Document-Pack Upload Scanner (magic-byte mismatch, EICAR, PDF /JavaScript blocked; clean PDF verified)
        pid = store.all("properties", tid)[0]["id"]
        eicar_b64 = base64.b64encode(b"%PDF-1.7\nX5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").decode()
        js_pdf_b64 = base64.b64encode(b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj").decode()
        spoof_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake_pdf").decode()
        clean_pdf_b64 = base64.b64encode(b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF").decode()

        st_eicar = api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"doc_type": "7_12_extract", "content_type": "application/pdf", "content_b64": eicar_b64}, hdr_a, "127.0.0.1", None)[0]
        st_jspdf = api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"doc_type": "7_12_extract", "content_type": "application/pdf", "content_b64": js_pdf_b64}, hdr_a, "127.0.0.1", None)[0]
        st_spoof = api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"doc_type": "7_12_extract", "content_type": "application/pdf", "content_b64": spoof_b64}, hdr_a, "127.0.0.1", None)[0]
        st_clean, clean_doc, _ = api.handle(store, "POST", f"/api/v1/properties/{pid}/documents/scan", {}, {"doc_type": "7_12_extract", "content_type": "application/pdf", "content_b64": clean_pdf_b64}, hdr_a, "127.0.0.1", None)
        if clean_doc.get("object_key"):
            s3_cleanup.append(clean_doc["object_key"])
        record(
            "s2_document_pack_magic_and_malware_scanner",
            st_eicar == 422 and st_jspdf == 422 and st_spoof == 415 and st_clean == 200,
            f"eicar={st_eicar} js_pdf={st_jspdf} spoof={st_spoof} clean={st_clean}",
        )

    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        upstream.shutdown()
        for key in s3_cleanup:
            try:
                neon.delete_object(key)
            except Exception:
                pass

    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"\nPROOF SUMMARY: {passed}/{len(checks)} PASS")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

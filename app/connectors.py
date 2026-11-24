"""
Universal Provider, Custom Tool, Inbound Webhook & Upload-Guard Fabric (`docs/39`).

Eliminates provider lock-in and credential blockages across the entire platform:

  1. **Hot-swappable Tenant Connector Profiles (`connector_profiles`)**:
     Every capability slot (`ai`, `voice`, `whatsapp`, `sms`, `payments`, `tool`)
     accepts either a named preset (Sarvam, ElevenLabs, Vapi, Bolna, Retell,
     Bland, Exotel, Twilio, Meta Cloud, Gupshup, Wati, MSG91, Kaleyra, Razorpay,
     Cashfree, Stripe, OpenAI/OpenRouter/Groq/Ollama, Anthropic, Gemini, Neon AI
     Gateway) OR **`custom_http`** — a universal JSON-template + dot-path
     extractor that speaks to any API invented today or in the future without
     modifying Python code.
  2. **3-Tier Resolution (Zero Blockage)**:
     `(1) Active Tenant Connector` → `(2) Vault / Process Env` → `(3) Built-in
     Full-Fidelity Local Twin` (including synthesized playable PCM `.wav` audio
     + qualification transcripts for voice calls, interactive WhatsApp/SMS
     simulation, and deterministic domain AI).
  3. **Universal Inbound Webhooks (`inbound_hooks`)**:
     Per-tenant `/api/v1/webhooks/custom/<hook_id>` endpoints with sealed HMAC
     or header secrets and dot-path field mapping into leads, inbox threads,
     calls, or deal notes.
  4. **Document-Pack Magic-Byte & Malware/Active-Content Scanner (`S2`)**:
     Verifies true file magic bytes against declared MIME type and blocks EICAR,
     executables (`MZ`/`ELF`), HTML `<script>` polyglots, and PDF `/JavaScript`
     or `/Launch` actions before storage.

All tenant secrets are sealed at rest with `sec.seal(..., aad=f"{tid}:conn:{id}")`
and never returned in API responses (`SEN-C306`, `SEN-C803`). Stdlib-only (`D2`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from . import config as cfg
from . import neon
from . import security as sec

HTTP_TIMEOUT = 12.0
CATEGORIES = ("ai", "voice", "whatsapp", "sms", "payments", "tool")
AUTH_SCHEMES = ("bearer", "basic", "header", "query", "none")

# ---------------------------------------------------------------------------
# Provider Preset Catalogue (named presets + universal `custom_http` dialect)
# ---------------------------------------------------------------------------
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    # --- AI / LLM ---
    "openai_compat": {
        "category": "ai",
        "label": "OpenAI-Compatible (OpenAI / OpenRouter / Groq / DeepSeek / Ollama / vLLM)",
        "default_url": "https://api.openai.com/v1/chat/completions",
        "auth_scheme": "bearer",
    },
    "anthropic": {
        "category": "ai",
        "label": "Anthropic Messages API (Claude)",
        "default_url": "https://api.anthropic.com/v1/messages",
        "auth_scheme": "header",
        "auth_header": "x-api-key",
    },
    "gemini": {
        "category": "ai",
        "label": "Google Gemini generateContent",
        "default_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        "auth_scheme": "header",
        "auth_header": "x-goog-api-key",
    },
    "sarvam_chat": {
        "category": "ai",
        "label": "Sarvam AI Sovereign LLM (India)",
        "default_url": "https://api.sarvam.ai/v1/chat/completions",
        "auth_scheme": "header",
        "auth_header": "api-subscription-key",
    },
    "neon_ai_gateway": {
        "category": "ai",
        "label": "Neon AI Gateway",
        "default_url": "",
        "auth_scheme": "bearer",
    },
    # --- Voice / Telephony ---
    "sarvam": {
        "category": "voice",
        "label": "Sarvam AI Bulbul / Telephony (India)",
        "default_url": "https://api.sarvam.ai/call/v1/outbound",
        "auth_scheme": "header",
        "auth_header": "api-subscription-key",
    },
    "elevenlabs": {
        "category": "voice",
        "label": "ElevenLabs Conversational AI",
        "default_url": "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
        "auth_scheme": "header",
        "auth_header": "xi-api-key",
    },
    "vapi": {
        "category": "voice",
        "label": "Vapi Voice Agents",
        "default_url": "https://api.vapi.ai/call/phone",
        "auth_scheme": "bearer",
    },
    "bolna": {
        "category": "voice",
        "label": "Bolna Voice AI (India)",
        "default_url": "https://api.bolna.dev/call",
        "auth_scheme": "bearer",
    },
    "retell": {
        "category": "voice",
        "label": "Retell AI Voice",
        "default_url": "https://api.retellai.com/v2/create-phone-call",
        "auth_scheme": "bearer",
    },
    "bland": {
        "category": "voice",
        "label": "Bland AI Voice",
        "default_url": "https://api.bland.ai/v1/calls",
        "auth_scheme": "header",
        "auth_header": "authorization",
    },
    "exotel": {
        "category": "voice",
        "label": "Exotel Cloud Telephony (India)",
        "default_url": "https://api.exotel.com/v1/Accounts/Calls/connect.json",
        "auth_scheme": "basic",
    },
    "twilio": {
        "category": "voice",
        "label": "Twilio Programmable Voice",
        "default_url": "https://api.twilio.com/2010-04-01/Calls.json",
        "auth_scheme": "basic",
    },
    # --- WhatsApp ---
    "meta_cloud": {
        "category": "whatsapp",
        "label": "Meta WhatsApp Cloud API v21.0",
        "default_url": "https://graph.facebook.com/v21.0",
        "auth_scheme": "bearer",
    },
    "gupshup": {
        "category": "whatsapp",
        "label": "Gupshup WhatsApp BSP",
        "default_url": "https://api.gupshup.io/wa/api/v1/msg",
        "auth_scheme": "header",
        "auth_header": "apikey",
    },
    "wati": {
        "category": "whatsapp",
        "label": "WATI WhatsApp API",
        "default_url": "https://api.whatsapp.com/v1/messages",
        "auth_scheme": "bearer",
    },
    "interakt": {
        "category": "whatsapp",
        "label": "Interakt WhatsApp BSP",
        "default_url": "https://api.whatsapp.com/v1/public/message",
        "auth_scheme": "basic",
    },
    # --- SMS (TRAI DLT) ---
    "dlt_generic": {
        "category": "sms",
        "label": "TRAI DLT Generic HTTPS Gateway",
        "default_url": "https://control.msg91.com/api/v5/flow",
        "auth_scheme": "header",
        "auth_header": "authkey",
    },
    "msg91": {
        "category": "sms",
        "label": "MSG91 Flow DLT SMS (India)",
        "default_url": "https://control.msg91.com/api/v5/flow",
        "auth_scheme": "header",
        "auth_header": "authkey",
    },
    "kaleyra": {
        "category": "sms",
        "label": "Kaleyra Cloud SMS (India)",
        "default_url": "https://api.kaleyra.io/v1/messages",
        "auth_scheme": "header",
        "auth_header": "api-key",
    },
    "fast2sms": {
        "category": "sms",
        "label": "Fast2SMS DLT Route (India)",
        "default_url": "https://www.fast2sms.com/dev/bulkV2",
        "auth_scheme": "header",
        "auth_header": "authorization",
    },
    # --- Payments ---
    "razorpay": {
        "category": "payments",
        "label": "Razorpay Orders API (India)",
        "default_url": "https://api.razorpay.com/v1/orders",
        "auth_scheme": "basic",
    },
    "cashfree": {
        "category": "payments",
        "label": "Cashfree Payments PG v2023-08-01",
        "default_url": "https://api.cashfree.com/pg/orders",
        "auth_scheme": "header",
        "auth_header": "x-client-secret",
    },
    "stripe": {
        "category": "payments",
        "label": "Stripe PaymentIntents / Checkout",
        "default_url": "https://api.stripe.com/v1/payment_intents",
        "auth_scheme": "bearer",
    },
    # --- Universal Custom HTTP (works across ANY category + Custom Agent Tools) ---
    "custom_http": {
        "category": "*",
        "label": "Universal Custom HTTP / Future Provider (JSON Template + Dot-Path)",
        "default_url": "",
        "auth_scheme": "bearer",
    },
}


class ConnectorError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# JSON Template Renderer & Dot-Path Extractor (Future-Proof Dialect)
# ---------------------------------------------------------------------------
_VAR_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render_json_template(template: Any, variables: dict[str, Any]) -> Any:
    """Safely substitute `{{var}}` inside dict/list/str structures without JSON injection."""
    if isinstance(template, dict):
        return {str(k): render_json_template(v, variables) for k, v in template.items()}
    if isinstance(template, list):
        return [render_json_template(x, variables) for x in template]
    if isinstance(template, str):
        full = _VAR_TOKEN_RE.fullmatch(template.strip())
        if full and full.group(1) in variables:
            # Preserve int/float/dict/list types when the whole value is `{{amount_paise}}`
            return variables[full.group(1)]
        return _VAR_TOKEN_RE.sub(lambda m: str(variables.get(m.group(1), "")), template)
    return template


def extract_dot_path(doc: Any, path: str) -> Any:
    """Extract nested JSON fields via dot path like `choices.0.message.content`."""
    if not path or doc is None:
        return None
    cur = doc
    for part in str(path).strip().split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            if not part.isdigit():
                return None
            idx = int(part)
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        else:
            return None
    return cur


# ---------------------------------------------------------------------------
# Encrypted Tenant Storage Helpers
# ---------------------------------------------------------------------------
def connector_aad(tid: str, conn_id: str) -> str:
    return f"{tid}:conn:{conn_id}"


def hook_aad(tid: str, hook_id: str) -> str:
    return f"{tid}:inhook:{hook_id}"


def list_connectors(tenant: dict | None) -> list[dict[str, Any]]:
    rows = ((tenant or {}).get("settings") or {}).get("connector_profiles") or []
    return [dict(r) for r in rows if isinstance(r, dict) and r.get("id")]


def active_connector(tenant: dict | None, category: str) -> dict[str, Any] | None:
    rows = [r for r in list_connectors(tenant) if r.get("category") == category and r.get("enabled", True)]
    if not rows:
        return None
    # Prefer explicitly marked primary, else most recently updated
    rows.sort(key=lambda r: (1 if r.get("is_primary") else 0, int(r.get("updated_at") or r.get("created_at") or 0)), reverse=True)
    return rows[0]


def unseal_connector_secrets(tid: str, row: dict[str, Any]) -> tuple[str, str]:
    aad = connector_aad(tid, str(row["id"]))
    s1 = sec.unseal(str(row.get("secret_enc") or ""), aad=aad) if row.get("secret_enc") else ""
    s2 = sec.unseal(str(row.get("secret2_enc") or ""), aad=aad) if row.get("secret2_enc") else ""
    return s1, s2


def public_connector(row: dict[str, Any]) -> dict[str, Any]:
    """Safe representation for API/UI (`SEN-C306` clean: zero secret strings or 32+ hex)."""
    return {
        "id": row["id"],
        "name": row.get("name", ""),
        "slug": row.get("slug", ""),
        "category": row.get("category", ""),
        "preset": row.get("preset", "custom_http"),
        "endpoint_url": row.get("endpoint_url", ""),
        "auth_scheme": row.get("auth_scheme", "bearer"),
        "auth_header": row.get("auth_header", ""),
        "key_id_public": row.get("key_id_public", ""),
        "model": row.get("model", ""),
        "headers": row.get("headers") or {},
        "body_template": row.get("body_template"),
        "response_id_path": row.get("response_id_path", ""),
        "response_text_path": row.get("response_text_path", ""),
        "risk": row.get("risk", "read"),
        "enabled": bool(row.get("enabled", True)),
        "is_primary": bool(row.get("is_primary", True)),
        "has_secret": bool(row.get("secret_enc")),
        "secret_fp": str(row.get("secret_fp") or "")[:10],
        "last_test_ok": row.get("last_test_ok"),
        "last_test_at": row.get("last_test_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_inbound_hooks(tenant: dict | None) -> list[dict[str, Any]]:
    rows = ((tenant or {}).get("settings") or {}).get("inbound_hooks") or []
    return [dict(r) for r in rows if isinstance(r, dict) and r.get("id")]


def public_inbound_hook(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name", ""),
        "target_action": row.get("target_action", "lead.capture"),
        "auth_mode": row.get("auth_mode", "hmac_sha256"),
        "signature_header": row.get("signature_header", "x-webhook-signature"),
        "field_map": row.get("field_map") or {},
        "enabled": bool(row.get("enabled", True)),
        "secret_fp": str(row.get("secret_fp") or "")[:10],
        "deliveries": int(row.get("deliveries") or 0),
        "last_received_at": row.get("last_received_at"),
        "endpoint_path": f"/api/v1/webhooks/custom/{row['id']}",
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Universal Outbound HTTP Executor (SSRF-guarded + Auth-scheme aware)
# ---------------------------------------------------------------------------
def execute_connector_http(
    tenant: dict | None,
    row: dict[str, Any],
    variables: dict[str, Any],
    *,
    default_payload: dict[str, Any] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> dict[str, Any]:
    tid = str((tenant or {}).get("id") or "")
    url = str(row.get("endpoint_url") or "").strip()
    if not url:
        raise ConnectorError(422, "CONNECTOR_URL_MISSING", "Connector endpoint_url is empty.")

    allowed_hosts = sec.tenant_egress_hosts(tenant)
    ok, reason = sec.ssrf_check(
        url,
        strict_allowlist=True,
        extra_hosts=allowed_hosts,
        allow_loopback_dev=True,
        resolve_dns=not (not cfg.PRODUCTION and url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))),
    )
    if not ok:
        raise ConnectorError(
            400,
            "EGRESS_BLOCKED",
            f"Outbound URL blocked by SSRF/egress guard ({reason}). Allowlist the public HTTPS domain in Integrations first.",
        )

    secret1, secret2 = unseal_connector_secrets(tid, row)
    scheme = str(row.get("auth_scheme") or "bearer").lower()
    hdrs: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    for hk, hv in (row.get("headers") or {}).items():
        if re.fullmatch(r"[A-Za-z0-9\-]{1,64}", str(hk)):
            hdrs[str(hk)] = str(hv)[:256]

    target_url = url
    if scheme == "bearer" and secret1:
        hdrs["Authorization"] = f"Bearer {secret1}"
    elif scheme == "basic":
        user_part = str(row.get("key_id_public") or secret2 or "").strip()
        pw_part = secret1
        tok_b64 = base64.b64encode(f"{user_part}:{pw_part}".encode("utf-8")).decode("ascii")
        hdrs["Authorization"] = f"Basic {tok_b64}"
    elif scheme == "header" and secret1:
        hname = str(row.get("auth_header") or "X-Api-Key").strip()
        hdrs[hname] = secret1
        if row.get("key_id_public"):
            hdrs.setdefault("x-client-id", str(row["key_id_public"]))
    elif scheme == "query" and secret1:
        qparam = urllib.parse.urlencode({str(row.get("auth_header") or "api_key"): secret1})
        sep = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{sep}{qparam}"

    tmpl = row.get("body_template")
    wire = render_json_template(tmpl, variables) if isinstance(tmpl, dict) and tmpl else (default_payload or variables)

    req = urllib.request.Request(
        target_url,
        data=json.dumps(wire).encode("utf-8"),
        headers=hdrs,
        method="POST",
    )
    try:
        with sec.safe_urlopen(
            req,
            timeout=timeout,
            extra_hosts=allowed_hosts,
            allow_loopback_dev=True,
            strict_allowlist=True,
        ) as resp:
            raw_resp = resp.read().decode("utf-8", "replace")
            status_code = resp.status
    except urllib.error.HTTPError as exc:
        snip = cfg.redact(exc.read(300).decode("utf-8", "replace") if getattr(exc, "fp", None) else str(exc))[:160]
        if "redirect" in str(exc).lower():
            raise ConnectorError(400, "EGRESS_BLOCKED", f"Provider '{row.get('name')}' redirect blocked by SSRF guard ({exc.code}).")
        raise ConnectorError(502, "CONNECTOR_UPSTREAM_ERROR", f"Provider '{row.get('name')}' HTTP {exc.code}: {snip}")
    except ValueError as exc:
        if "EGRESS_BLOCKED" in str(exc):
            raise ConnectorError(400, "EGRESS_BLOCKED", str(exc))
        raise ConnectorError(502, "CONNECTOR_UNREACHABLE", f"Provider '{row.get('name')}' unreachable: {cfg.redact(str(exc))[:140]}")
    except Exception as exc:
        raise ConnectorError(502, "CONNECTOR_UNREACHABLE", f"Provider '{row.get('name')}' unreachable: {cfg.redact(str(exc))[:140]}")

    try:
        parsed = json.loads(raw_resp) if raw_resp.strip() else {}
    except Exception:
        parsed = {"raw_text": raw_resp[:1000]}

    ext_id = extract_dot_path(parsed, str(row.get("response_id_path") or "")) if row.get("response_id_path") else None
    ext_txt = extract_dot_path(parsed, str(row.get("response_text_path") or "")) if row.get("response_text_path") else None
    return {
        "http_status": status_code,
        "json": parsed,
        "extracted_id": str(ext_id) if ext_id is not None else "",
        "extracted_text": str(ext_txt) if ext_txt is not None else "",
    }


# ---------------------------------------------------------------------------
# Universal AI / LLM Completion Router (Connector → Env/Gateway → Local Twin)
# ---------------------------------------------------------------------------
def _call_openai_compat(url: str, key: str, messages: list[dict], model: str, extra_headers: dict[str, str] | None = None, tenant: dict | None = None) -> dict[str, Any]:
    allowed = sec.tenant_egress_hosts(tenant)
    ok, reason = sec.ssrf_check(
        url,
        strict_allowlist=True,
        extra_hosts=allowed,
        allow_loopback_dev=True,
        resolve_dns=not (not cfg.PRODUCTION and url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))),
    )
    if not ok:
        return {"ok": False, "error": reason}
    hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    if extra_headers:
        hdrs.update(extra_headers)
    body = {"model": model or "gpt-4o-mini", "messages": messages, "temperature": 0.3, "max_tokens": 500}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=hdrs, method="POST")
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=allowed, allow_loopback_dev=True, strict_allowlist=True) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        txt = sec.sanitize_ai_output((((doc.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
        return {"ok": bool(txt), "text": str(txt), "model": doc.get("model") or model}
    except Exception as exc:
        return {"ok": False, "error": cfg.redact(str(exc))[:140]}


def _call_anthropic(url: str, key: str, messages: list[dict], model: str, tenant: dict | None = None) -> dict[str, Any]:
    allowed = sec.tenant_egress_hosts(tenant)
    ok, reason = sec.ssrf_check(
        url,
        strict_allowlist=True,
        extra_hosts=allowed,
        allow_loopback_dev=True,
        resolve_dns=not (not cfg.PRODUCTION and url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))),
    )
    if not ok:
        return {"ok": False, "error": reason}
    sys_txt = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    user_msgs = [{"role": "user" if m.get("role") != "assistant" else "assistant", "content": m["content"]}
                 for m in messages if m.get("role") != "system"]
    body: dict[str, Any] = {"model": model or "claude-sonnet-4-20250514", "max_tokens": 500, "messages": user_msgs or [{"role": "user", "content": "Hello"}]}
    if sys_txt:
        body["system"] = sys_txt
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=allowed, allow_loopback_dev=True, strict_allowlist=True) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        txt = sec.sanitize_ai_output(((doc.get("content") or [{}])[0]).get("text") or "")
        return {"ok": bool(txt), "text": str(txt), "model": doc.get("model") or model}
    except Exception as exc:
        return {"ok": False, "error": cfg.redact(str(exc))[:140]}


def _call_gemini(url: str, key: str, messages: list[dict], tenant: dict | None = None) -> dict[str, Any]:
    allowed = sec.tenant_egress_hosts(tenant)
    ok, reason = sec.ssrf_check(
        url,
        strict_allowlist=True,
        extra_hosts=allowed,
        allow_loopback_dev=True,
        resolve_dns=not (not cfg.PRODUCTION and url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))),
    )
    if not ok:
        return {"ok": False, "error": reason}
    combined = "\n\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
    body = {"contents": [{"parts": [{"text": combined}]}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=allowed, allow_loopback_dev=True, strict_allowlist=True) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
        txt = sec.sanitize_ai_output(extract_dot_path(doc, "candidates.0.content.parts.0.text") or "")
        return {"ok": bool(txt), "text": str(txt), "model": "gemini"}
    except Exception as exc:
        return {"ok": False, "error": cfg.redact(str(exc))[:140]}


def complete_ai(
    tenant: dict | None,
    messages: list[dict[str, str]],
    *,
    model: str = "",
) -> dict[str, Any]:
    """Try `(1) Tenant AI Connector` → `(2) Neon AI Gateway / Env Keys` → `ok: False`.

    `F-05` Hardening: All outbound messages are scrubbed of secrets & regulated PII
    and untrusted user prompts are delimited before crossing the external gateway boundary.
    """
    safe_messages, redaction_meta = sec.sanitize_outbound_ai_messages(messages)
    conn = active_connector(tenant, "ai")
    if conn:
        tid = str((tenant or {}).get("id") or "")
        preset = str(conn.get("preset") or "openai_compat")
        key, _ = unseal_connector_secrets(tid, conn)
        chosen_model = str(conn.get("model") or model or "")
        url = str(conn.get("endpoint_url") or PROVIDER_PRESETS.get(preset, {}).get("default_url") or "").strip()
        if preset == "anthropic" and url and key:
            res = _call_anthropic(url, key, safe_messages, chosen_model, tenant=tenant)
            if res.get("ok"):
                return {**res, "provider": f"connector:{conn.get('slug') or preset}", "redaction": redaction_meta}
        elif preset == "gemini" and url and key:
            res = _call_gemini(url, key, safe_messages, tenant=tenant)
            if res.get("ok"):
                return {**res, "provider": f"connector:{conn.get('slug') or preset}", "redaction": redaction_meta}
        elif preset == "sarvam_chat" and url and key:
            res = _call_openai_compat(url, key, safe_messages, chosen_model or "sarvam-m", {"api-subscription-key": key}, tenant=tenant)
            if res.get("ok"):
                return {**res, "provider": f"connector:{conn.get('slug') or preset}", "redaction": redaction_meta}
        elif preset == "custom_http":
            sys_p = "\n".join(m["content"] for m in safe_messages if m.get("role") == "system")
            usr_p = "\n".join(m["content"] for m in safe_messages if m.get("role") != "system")
            try:
                out = execute_connector_http(
                    tenant,
                    conn,
                    {"prompt": usr_p, "system_prompt": sys_p, "model": chosen_model or "default", "messages": safe_messages},
                    default_payload={"model": chosen_model or "default", "messages": safe_messages, "prompt": usr_p},
                )
                txt = sec.sanitize_ai_output(
                    out["extracted_text"]
                    or extract_dot_path(out["json"], "choices.0.message.content")
                    or extract_dot_path(out["json"], "text")
                    or extract_dot_path(out["json"], "output")
                    or ""
                )
                if txt:
                    return {"ok": True, "text": str(txt), "provider": f"connector:{conn.get('slug') or 'custom'}", "model": chosen_model, "redaction": redaction_meta}
            except ConnectorError:
                pass
        elif url and key:
            res = _call_openai_compat(url, key, safe_messages, chosen_model, tenant=tenant)
            if res.get("ok"):
                return {**res, "provider": f"connector:{conn.get('slug') or preset}", "redaction": redaction_meta}

    # Tier 2: Process / Vault environment keys
    if neon.ai_configured():
        r = neon.ai_chat(safe_messages, model=model)
        if r.get("ok") and (r.get("text") or "").strip():
            return {"ok": True, "text": sec.sanitize_ai_output(r["text"].strip()), "provider": "neon-ai-gateway", "model": r.get("model", ""), "redaction": redaction_meta}

    oai_key = cfg.get("OPENAI_API_KEY", "").strip()
    if oai_key and not cfg.is_placeholder(oai_key):
        r = _call_openai_compat(cfg.get("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/") + "/chat/completions", oai_key, safe_messages, model or "gpt-4o-mini", tenant=tenant)
        if r.get("ok"):
            return {**r, "provider": "env:openai", "redaction": redaction_meta}

    ant_key = cfg.get("ANTHROPIC_API_KEY", "").strip()
    if ant_key and not cfg.is_placeholder(ant_key):
        r = _call_anthropic("https://api.anthropic.com/v1/messages", ant_key, safe_messages, model or "claude-sonnet-4-20250514", tenant=tenant)
        if r.get("ok"):
            return {**r, "provider": "env:anthropic", "redaction": redaction_meta}

    gem_key = cfg.get("GOOGLE_API_KEY", "").strip()
    if gem_key and not cfg.is_placeholder(gem_key):
        r = _call_gemini("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent", gem_key, safe_messages, tenant=tenant)
        if r.get("ok"):
            return {**r, "provider": "env:gemini", "redaction": redaction_meta}

    return {"ok": False}


# ---------------------------------------------------------------------------
# Zero-Blockage Voice Local Twin (Playable PCM `.wav` + Qualification Transcript)
# ---------------------------------------------------------------------------
def synthesize_wav_clip(duration_ms: int = 1200, freq_hz: float = 440.0) -> bytes:
    """Generate a real playable 8 kHz 8-bit mono PCM WAV audio clip in pure Python."""
    sample_rate = 8000
    n_samples = max(400, int(sample_rate * max(200, min(duration_ms, 5000)) / 1000))
    pcm = bytearray(n_samples)
    for i in range(n_samples):
        t = i / sample_rate
        # Gentle two-chime cadence + speech-envelope modulation so browser `<audio>` plays cleanly
        f = freq_hz if t < (duration_ms / 2000.0) else freq_hz * 1.25
        env = 0.35 * (0.5 + 0.5 * math.sin(2.0 * math.pi * 5.0 * t))
        sample = int(128 + 127 * env * math.sin(2.0 * math.pi * f * t))
        pcm[i] = max(0, min(255, sample))
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,          # PCM chunk size
        1,           # PCM format
        1,           # mono
        sample_rate,
        sample_rate, # byte rate (8-bit mono)
        1,           # block align
        8,           # bits per sample
        b"data",
        data_size,
    )
    return header + bytes(pcm)


def build_local_voice_transcript(contact: dict, tenant: dict, agent_name: str, language: str = "mr") -> str:
    buyer = (contact.get("name") or "Buyer").split()[0]
    city = contact.get("city") or "Nanded"
    req = contact.get("requirement") or "NA sanctioned plot"
    firm = tenant.get("name") or "Sunrise Land Developers"
    return (
        f"[{agent_name} · AI Disclosure ({language})]: Namaste {buyer}, this is {agent_name}, an automated AI "
        f"assistant calling from {firm} on a recorded line.\n"
        f"[{buyer}]: Yes, I enquired about {req} in {city}.\n"
        f"[{agent_name}]: Confirmed — 7/12 extract, NA order, and MahaRERA pack are ready. "
        f"Shall I reserve a Saturday 11:00 AM site visit slot and share the pack on WhatsApp?\n"
        f"[{buyer}]: Yes, Saturday morning works. Send the documents on WhatsApp."
    )


# ---------------------------------------------------------------------------
# S2 & Phase 2 Document-Pack Magic-Byte Sniffing, Macro/Bomb Rejection & Sandboxed Conversion
# ---------------------------------------------------------------------------
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "audio/wav": (b"RIFF",),
    _DOCX_MIME: (b"PK\x03\x04",),
    _XLSX_MIME: (b"PK\x03\x04",),
}
_EICAR_SUBSTR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR"
_DANGEROUS_PDF_TOKENS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/OpenAction",
    b"/EmbeddedFiles",
    b"/RichMedia",
    b"/XFA",
    b"/AA",
    b"/SubmitForm",
    b"/ImportData",
)
_PDF_HEX_NAME_RE = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_STREAM_RE = re.compile(rb"stream(?:\r\n|\n)(.*?)(?:\r\n|\n)?endstream", re.DOTALL)
_POLYGLOT_IMAGE_TOKENS = (
    b"<?php",
    b"<?=",
    b"<script",
    b"<iframe",
    b"phar://",
)


def _normalize_pdf_names(data: bytes) -> bytes:
    """Decode `#XX` hex-escaped PDF name tokens (`F-08`, e.g. `/Java#53cript` -> `/JavaScript`)."""
    return _PDF_HEX_NAME_RE.sub(lambda m: bytes([int(m.group(1), 16)]), data)


def _inspect_pdf_deep(raw_bytes: bytes) -> None:
    """Inspect both normalized raw PDF dictionaries and bounded `/FlateDecode` streams (`F-08`)."""
    import zlib

    normalized_top = _normalize_pdf_names(raw_bytes)
    for tok in _DANGEROUS_PDF_TOKENS:
        # Match token followed by PDF delimiter/whitespace/end so `/JSON` is not confused with `/JS`
        pat = re.escape(tok) + rb"(?=[\s/<>\[\]()%]|$)"
        if re.search(pat, normalized_top):
            raise ConnectorError(
                422,
                "MALICIOUS_FILE_BLOCKED",
                f"PDF contains prohibited active action ({tok.decode('ascii', 'ignore')}).",
            )

    total_decompressed = 0
    stream_count = 0
    for m in _PDF_STREAM_RE.finditer(raw_bytes):
        stream_count += 1
        if stream_count > 64:
            raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "PDF exceeds maximum inspectable stream count (64).")
        comp = m.group(1)
        if not comp:
            continue
        # Inspect raw stream first, then attempt bounded zlib/Flate decompression
        chunks_to_check = [comp]
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                dobj = zlib.decompressobj(wbits)
                dec = dobj.decompress(comp, 524_288 + 1)
                if len(dec) > 524_288:
                    raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "PDF Flate stream exceeds 512 KB decompression safety ceiling.")
                if len(dec) > 16_384 and (len(dec) / max(1, len(comp))) > 50.0:
                    raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "PDF Flate stream decompression ratio exceeds 50:1 bomb threshold.")
                total_decompressed += len(dec)
                if total_decompressed > 4 * 1024 * 1024:
                    raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "PDF total decompressed stream size exceeds 4 MB safety ceiling.")
                chunks_to_check.append(dec)
                break
            except ConnectorError:
                raise
            except Exception:
                continue

        for buf in chunks_to_check:
            if _EICAR_SUBSTR in buf:
                raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "Compressed PDF stream blocked by antivirus signature (EICAR).")
            norm_buf = _normalize_pdf_names(buf)
            for tok in _DANGEROUS_PDF_TOKENS:
                pat = re.escape(tok) + rb"(?=[\s/<>\[\]()%]|$)"
                if re.search(pat, norm_buf):
                    raise ConnectorError(
                        422,
                        "MALICIOUS_FILE_BLOCKED",
                        f"Compressed PDF stream contains prohibited active action ({tok.decode('ascii', 'ignore')}).",
                    )


def _inspect_image_polyglot(raw_bytes: bytes, mime: str) -> None:
    """Validate image container integrity and reject embedded PHP/HTML/script polyglots (`F-08`)."""
    low = raw_bytes.lower()
    for bad in _POLYGLOT_IMAGE_TOKENS:
        if bad in low:
            raise ConnectorError(
                422,
                "MALICIOUS_FILE_BLOCKED",
                "Image contains embedded script/PHP/polyglot payload.",
            )
    if mime == "image/png":
        if len(raw_bytes) < 24 or raw_bytes[12:16] != b"IHDR" or b"IEND" not in raw_bytes[-64:]:
            raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "PNG container lacks valid IHDR/IEND chunk structure.")
    elif mime == "image/jpeg":
        if len(raw_bytes) < 4 or b"\xff\xd9" not in raw_bytes[-32:]:
            raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "JPEG container lacks valid EOI marker.")
_DANGEROUS_OOXML_PARTS = (
    "vbaproject.bin",
    "vbadata.xml",
    "macrosheets/",
    "activex/",
    "embeddings/",
)


def _sandboxed_pdf_text(raw_bytes: bytes) -> str:
    """Pure-stdlib, zero-exec plain-text extraction from literal PDF text runs."""
    chunks: list[str] = []
    for m in re.finditer(rb"\(([^()\\]{2,160})\)", raw_bytes[:131072]):
        txt = m.group(1).decode("latin-1", errors="ignore")
        cleaned = re.sub(r"[^\x20-\x7E]+", " ", txt).strip()
        if cleaned:
            chunks.append(cleaned)
        if sum(len(c) for c in chunks) >= 600:
            break
    return " ".join(chunks)[:600]


def _inspect_and_convert_ooxml(raw_bytes: bytes, mime: str) -> str:
    """Inspect OOXML ZIP container for macros, OLE, external template injection & archive bombs,
    returning a sandboxed plain-text preview (`<= 600` chars)."""
    import io as _io
    import zipfile as _zf

    try:
        zf = _zf.ZipFile(_io.BytesIO(raw_bytes), "r")
    except Exception:
        raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "Uploaded archive is not a valid OOXML document.")

    with zf:
        infos = zf.infolist()
        if not infos or len(infos) > 120:
            raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "OOXML archive exceeds maximum member count (120).")
        total_uncompressed = sum(max(0, int(i.file_size)) for i in infos)
        if total_uncompressed > 10 * 1024 * 1024:
            raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", "OOXML uncompressed size exceeds 10 MB safety ceiling.")
        ratio = total_uncompressed / max(1, len(raw_bytes))
        if total_uncompressed > 65536 and ratio > 40.0:
            raise ConnectorError(422, "ARCHIVE_BOMB_BLOCKED", f"OOXML compression ratio ({ratio:.1f}:1) exceeds 40:1 limit.")

        names_lower = {i.filename.lower(): i.filename for i in infos}
        if "[content_types].xml" not in names_lower:
            raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "ZIP container lacks [Content_Types].xml.")
        if mime == _DOCX_MIME and "word/document.xml" not in names_lower:
            raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "Declared .docx is missing word/document.xml.")
        if mime == _XLSX_MIME and "xl/workbook.xml" not in names_lower:
            raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "Declared .xlsx is missing xl/workbook.xml.")

        for info in infos:
            fn = info.filename
            low = fn.lower()
            if ".." in fn or fn.startswith(("/", "\\")):
                raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "Archive path traversal entry detected.")
            if any(tok in low for tok in _DANGEROUS_OOXML_PARTS) or low.endswith(".bin"):
                raise ConnectorError(
                    422,
                    "OFFICE_MACRO_BLOCKED",
                    f"Macro/OLE active content ('{fn}') is prohibited in uploaded Office documents.",
                )
            if low.endswith(".rels") and info.file_size <= 262144:
                rel_bytes = zf.read(info)
                if re.search(rb'TargetMode\s*=\s*["\']External["\']', rel_bytes, flags=re.IGNORECASE):
                    raise ConnectorError(
                        422,
                        "OFFICE_MACRO_BLOCKED",
                        "External relationship / remote template injection blocked in Office document.",
                    )

        # Sandboxed plain-text conversion (strip XML tags, no external DTD/entity resolution)
        preview_xml = b""
        if mime == _DOCX_MIME and "word/document.xml" in names_lower:
            preview_xml = zf.read(names_lower["word/document.xml"])[:262144]
        elif mime == _XLSX_MIME and "xl/sharedstrings.xml" in names_lower:
            preview_xml = zf.read(names_lower["xl/sharedstrings.xml"])[:262144]
        if preview_xml:
            if b"<!ENTITY" in preview_xml.upper():
                raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "XML entity expansion (XXE) blocked in Office document.")
            text = re.sub(rb"<[^>]+>", b" ", preview_xml).decode("utf-8", errors="ignore")
            return re.sub(r"\s+", " ", text).strip()[:600]
        return ""


def scan_document_upload(raw_bytes: bytes, declared_mime: str, filename: str = "") -> dict[str, Any]:
    """Verify magic bytes, reject polyglots/executables/EICAR/active-JS PDFs/Office macros/bombs,
    and emit a sandboxed plain-text preview (`PLANS.md` S2 & `docs/09` Phase 2)."""
    mime = (declared_mime or "").split(";")[0].strip().lower()
    if not raw_bytes:
        raise ConnectorError(400, "EMPTY_UPLOAD", "Uploaded file is empty.")
    if len(raw_bytes) > 5 * 1024 * 1024:
        raise ConnectorError(413, "PAYLOAD_TOO_LARGE", "Document exceeds 5 MB scan limit.")

    # 1. Malware / executable / RTF OLE / script polyglot signatures
    head = raw_bytes[:4096]
    if _EICAR_SUBSTR in raw_bytes:
        raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "Upload blocked by antivirus signature (EICAR).")
    if raw_bytes.startswith((b"MZ", b"\x7fELF", b"#!", b"\xd0\xcf\x11\xe0")):
        raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "Executable or legacy OLE2 binary containers are prohibited.")
    if raw_bytes.startswith(b"{\\rtf") and re.search(rb"\\obj(?:data|ocx|emb)\b", raw_bytes, flags=re.IGNORECASE):
        raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "RTF embedded OLE object blocked.")
    if re.search(rb"<\s*(script|iframe|object|embed)\b", head, flags=re.IGNORECASE):
        raise ConnectorError(422, "MALICIOUS_FILE_BLOCKED", "Active HTML/SVG script content detected in upload.")

    # 2. Magic-byte MIME verification
    allowed_prefixes = _MAGIC_SIGNATURES.get(mime)
    if not allowed_prefixes:
        raise ConnectorError(
            415,
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"Unsupported document MIME '{mime}'. Allowed: PDF, DOCX, XLSX, PNG, JPEG, WebP, WAV.",
        )
    if not any(raw_bytes.startswith(sig) for sig in allowed_prefixes):
        raise ConnectorError(
            415,
            "MIME_MAGIC_MISMATCH",
            f"File magic bytes do not match declared MIME type '{mime}'.",
        )
    if mime == "image/webp" and raw_bytes[8:12] != b"WEBP":
        raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "RIFF container is not a valid WebP image.")
    if mime == "audio/wav" and raw_bytes[8:12] != b"WAVE":
        raise ConnectorError(415, "MIME_MAGIC_MISMATCH", "RIFF container is not a valid WAV recording.")

    # 3. Active-content PDF / OOXML macro & bomb / Image polyglot inspection + sandboxed conversion
    sandboxed_preview = ""
    if mime == "application/pdf":
        _inspect_pdf_deep(raw_bytes)
        sandboxed_preview = _sandboxed_pdf_text(raw_bytes)
    elif mime in (_DOCX_MIME, _XLSX_MIME):
        sandboxed_preview = _inspect_and_convert_ooxml(raw_bytes, mime)
    elif mime in ("image/png", "image/jpeg", "image/webp"):
        _inspect_image_polyglot(raw_bytes, mime)

    return {
        "clean": True,
        "verified_mime": mime,
        "bytes": len(raw_bytes),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "filename": re.sub(r"[^A-Za-z0-9._\-]", "_", (filename or "document"))[:120],
        "sandboxed_preview": sandboxed_preview,
    }

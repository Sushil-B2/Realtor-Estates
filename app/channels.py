"""
Phase 1 S1 real channel & payment-gateway adapters (`docs/38`):

  1. WhatsApp Cloud API v21.0:
     - Approved template registry + parameter rendering
     - 24-hour customer-service window gate (`WHATSAPP_WINDOW_CLOSED`)
     - Live Graph API HTTPS dispatch (`WHATSAPP_TOKEN` + `WHATSAPP_PHONE_ID`)
     - Hub challenge verification (`GET /webhooks/whatsapp`) + `X-Hub-Signature-256`
       HMAC over raw bytes (`POST /webhooks/whatsapp`) for inbound messages and
       delivery/read status callbacks (`sent` → `delivered` → `read` / `failed`).
  2. TRAI DLT-registered SMS:
     - 19-digit DLT template registry + `{#var#}` pattern validation + 6-char
       alpha sender header (`SNRSUN`)
     - Live HTTPS SMS gateway dispatch (`SMS_API_URL` + `SMS_API_KEY`)
     - Signed delivery-receipt webhook (`POST /webhooks/sms`).
  3. Voice Telephony Provider (`Sarvam` / `ElevenLabs`):
     - Outbound qualification call dispatch (`VOICE_API_URL` + provider key)
     - Signed call-completion webhook (`POST /webhooks/voice`) that feeds S3
       recording archiving (`recordings/tenants/<tid>/…`) and `voice_minutes`.
  4. Razorpay Orders API (`/v1/orders`):
     - Outbound Basic-Auth order creation (`RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET`)
       echoing workspace/deal/unit `notes` back into webhook reconciliation.

Stdlib-only (`D2`). Every adapter reports `mode: "live"` when credentials are
present and `mode: "simulated"` when absent — never pretending a simulated send
reached a carrier, and never leaking secret material in errors or responses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import urlparse

from . import config as cfg
from . import connectors as conn_mod
from . import security as sec

HTTP_TIMEOUT = 10.0
WHATSAPP_WINDOW_SECONDS = 86_400


class ChannelError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _present(name: str, min_len: int = 8) -> bool:
    v = (cfg.get(name, "") or "").strip()
    if not v or len(v) < min_len:
        return False
    if min_len >= 16 and cfg.is_placeholder(v):
        return False
    return True


def _allow_endpoint_url(url: str, label: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return url.strip()
    if not cfg.PRODUCTION and parsed.scheme == "http" and host in ("127.0.0.1", "localhost", "::1"):
        return url.strip()
    raise ChannelError(502, "INVALID_PROVIDER_URL", f"{label} must be an https:// URL.")


def _digits_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        digits = "91" + digits
    return digits


# ===========================================================================
# 1. WhatsApp Cloud API (v21.0)
# ===========================================================================
BUILTIN_WHATSAPP_TEMPLATES: dict[str, dict[str, Any]] = {
    "site_visit_confirm": {
        "name": "site_visit_confirm",
        "language": "en",
        "category": "UTILITY",
        "body": "Namaste {{1}}, your site visit for {{2}} is confirmed for {{3}}. Reply 1 to reschedule.",
        "param_count": 3,
        "status": "approved",
    },
    "missed_call_recovery": {
        "name": "missed_call_recovery",
        "language": "en",
        "category": "UTILITY",
        "body": "Namaste {{1}}, we missed your call at {{2}}. Shall we share the RERA document pack & price sheet here?",
        "param_count": 2,
        "status": "approved",
    },
    "rera_doc_pack": {
        "name": "rera_doc_pack",
        "language": "en",
        "category": "UTILITY",
        "body": "Namaste {{1}}, here is the verified 7/12, NA order & MahaRERA ({{2}}) document pack for {{3}}.",
        "param_count": 3,
        "status": "approved",
    },
    "token_receipt": {
        "name": "token_receipt",
        "language": "en",
        "category": "UTILITY",
        "body": "Thank you {{1}} — booking receipt {{2}} for INR {{3}} has been recorded.",
        "param_count": 3,
        "status": "approved",
    },
}


def whatsapp_configured() -> bool:
    return _present("WHATSAPP_TOKEN", 16) and _present("WHATSAPP_PHONE_ID", 4)


def whatsapp_templates_for(tenant: dict | None) -> dict[str, dict[str, Any]]:
    merged = {k: dict(v) for k, v in BUILTIN_WHATSAPP_TEMPLATES.items()}
    custom = ((tenant or {}).get("settings") or {}).get("whatsapp_templates") or []
    if isinstance(custom, list):
        for item in custom:
            if isinstance(item, dict) and item.get("name"):
                merged[str(item["name"])] = dict(item)
    return merged


def render_whatsapp_template(tpl: dict[str, Any], params: list[str]) -> str:
    expected = int(tpl.get("param_count") or 0)
    if len(params) != expected:
        raise ChannelError(
            422,
            "WHATSAPP_TEMPLATE_PARAMS",
            f"Template '{tpl['name']}' expects {expected} parameter(s), got {len(params)}.",
        )
    text = str(tpl.get("body") or "")
    for idx, val in enumerate(params, start=1):
        text = text.replace(f"{{{{{idx}}}}}", str(val)[:160])
    return text


def whatsapp_window_open(conv: dict | None, contact: dict | None, now_ts: int | None = None) -> tuple[bool, int]:
    """Return `(is_open, last_inbound_at)` for Meta's 24-hour customer-service window."""
    ref = int(now_ts if now_ts is not None else time.time())
    last_in = int((contact or {}).get("last_inbound_at") or 0)
    if conv and isinstance(conv.get("messages"), list):
        for m in conv["messages"]:
            if m.get("dir") == "in" and m.get("channel", "whatsapp") in ("whatsapp", ""):
                last_in = max(last_in, int(m.get("at") or 0))
    if last_in <= 0:
        return False, 0
    return (ref - last_in) <= WHATSAPP_WINDOW_SECONDS, last_in


def send_whatsapp(
    to_phone: str,
    text: str = "",
    *,
    template_name: str = "",
    template_params: list[str] | None = None,
    language: str = "",
    tenant: dict | None = None,
    conv: dict | None = None,
    contact: dict | None = None,
    strict_window: bool = False,
) -> dict[str, Any]:
    digits = _digits_phone(to_phone)
    if len(digits) < 10:
        raise ChannelError(422, "INVALID_RECIPIENT_PHONE", "Contact has no valid phone number for WhatsApp.")

    win_open, last_in = whatsapp_window_open(conv, contact)
    wa_conn = conn_mod.active_connector(tenant, "whatsapp")
    live = bool(wa_conn) or whatsapp_configured()
    enforce_win = bool(
        live
        or strict_window
        or ((tenant or {}).get("settings") or {}).get("enforce_whatsapp_24h_window")
    )

    rendered = text
    tpl_obj: dict[str, Any] | None = None
    params = [str(x)[:160] for x in (template_params or [])]

    if template_name:
        templates = whatsapp_templates_for(tenant)
        tpl_obj = templates.get(template_name)
        if not tpl_obj or tpl_obj.get("status", "approved") != "approved":
            raise ChannelError(422, "WHATSAPP_TEMPLATE_UNKNOWN", f"WhatsApp template '{template_name}' is not approved.")
        rendered = render_whatsapp_template(tpl_obj, params)
    elif enforce_win and not win_open:
        raise ChannelError(
            422,
            "WHATSAPP_WINDOW_CLOSED",
            "More than 24 hours have elapsed since the buyer's last inbound WhatsApp message. "
            "Supply an approved `template_name` to message outside the 24-hour window.",
        )

    if wa_conn:
        preset = str(wa_conn.get("preset") or "custom_http")
        vars_map = {
            "to": digits,
            "phone": "+" + digits,
            "text": rendered,
            "template_name": template_name,
            "language": language or (str(tpl_obj.get("language") or "en") if tpl_obj else "en"),
            "tenant_id": str((tenant or {}).get("id") or ""),
            "contact_id": str((contact or {}).get("id") or ""),
            "contact_name": str((contact or {}).get("name") or ""),
        }
        default_wire: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": digits,
            "type": "template" if tpl_obj else "text",
            "text": {"body": rendered},
            "template_name": template_name or None,
            "parameters": params,
        }
        try:
            out = conn_mod.execute_connector_http(tenant, wa_conn, vars_map, default_payload=default_wire)
        except conn_mod.ConnectorError as exc:
            raise ChannelError(exc.status, exc.code, exc.message)
        msg_id = (
            out["extracted_id"]
            or conn_mod.extract_dot_path(out["json"], "messages.0.id")
            or conn_mod.extract_dot_path(out["json"], "messageId")
            or conn_mod.extract_dot_path(out["json"], "id")
            or f"wamid.conn_{uuid.uuid4().hex[:12]}"
        )
        return {
            "mode": "live",
            "provider": f"connector:{wa_conn.get('slug') or preset}",
            "provider_message_id": str(msg_id),
            "status": "sent",
            "window_24h_open": win_open,
            "last_inbound_at": last_in,
            "template": template_name or None,
            "rendered_text": rendered,
        }

    if not live:
        return {
            "mode": "simulated",
            "provider": "whatsapp-cloud-sim",
            "provider_message_id": f"wamid.sim_{uuid.uuid4().hex[:14]}",
            "status": "sent",
            "window_24h_open": win_open,
            "last_inbound_at": last_in,
            "template": template_name or None,
            "rendered_text": rendered,
        }

    base = _allow_endpoint_url(
        cfg.get("WHATSAPP_API_BASE", "https://graph.facebook.com/v21.0").rstrip("/"),
        "WHATSAPP_API_BASE",
    )
    phone_id = cfg.get("WHATSAPP_PHONE_ID", "").strip()
    token = cfg.get("WHATSAPP_TOKEN", "").strip()
    if tpl_obj:
        lang_code = language or str(tpl_obj.get("language") or "en")
        components = []
        if params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params],
            })
        wire: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": digits,
            "type": "template",
            "template": {
                "name": tpl_obj["name"],
                "language": {"code": lang_code},
                "components": components,
            },
        }
    else:
        wire = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": digits,
            "type": "text",
            "text": {"preview_url": False, "body": rendered},
        }

    req = urllib.request.Request(
        f"{base}/{phone_id}/messages",
        data=json.dumps(wire).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=sec.tenant_egress_hosts(tenant), allow_loopback_dev=True) as resp:
            resp_doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_snip = cfg.redact(exc.read(300).decode("utf-8", "replace"))[:140]
        raise ChannelError(502, "WHATSAPP_PROVIDER_ERROR", f"WhatsApp Graph API HTTP {exc.code}: {err_snip}")
    except Exception as exc:
        raise ChannelError(502, "WHATSAPP_PROVIDER_ERROR", f"WhatsApp Graph API unreachable: {cfg.redact(str(exc))[:120]}")

    wamid = ((resp_doc.get("messages") or [{}])[0]).get("id") or f"wamid.{uuid.uuid4().hex[:14]}"
    return {
        "mode": "live",
        "provider": "whatsapp-cloud",
        "provider_message_id": str(wamid),
        "status": "sent",
        "window_24h_open": win_open,
        "last_inbound_at": last_in,
        "template": template_name or None,
        "rendered_text": rendered,
    }


def verify_whatsapp_challenge(query: dict[str, list[str]]) -> str:
    expected = (cfg.get("WHATSAPP_VERIFY_TOKEN", "") or "").strip()
    if not expected:
        raise ChannelError(503, "WEBHOOK_NOT_CONFIGURED", "WHATSAPP_VERIFY_TOKEN is not configured.")
    mode = ((query.get("hub.mode") or [""])[0]).strip()
    tok = ((query.get("hub.verify_token") or [""])[0]).strip()
    challenge = ((query.get("hub.challenge") or [""])[0]).strip()
    if mode != "subscribe" or not tok or not hmac.compare_digest(tok.encode(), expected.encode()):
        raise ChannelError(403, "INVALID_VERIFY_TOKEN", "Webhook verification token rejected.")
    return challenge


def verify_hmac_sha256(raw_body: bytes, signature_header: str, secret_vars: tuple[str, ...]) -> bool:
    """Verify `sha256=<hex>` or bare `<hex>` HMAC-SHA256 over `raw_body`."""
    secret = ""
    for var in secret_vars:
        cand = (cfg.get(var, "") or "").strip()
        if cand and not cfg.is_placeholder(cand):
            secret = cand
            break
    if not secret or not signature_header:
        return False
    provided = signature_header.strip()
    if provided.lower().startswith("sha256="):
        provided = provided[7:].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided.lower(), expected.lower())


def parse_whatsapp_webhook(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Extract normalized `inbound` buyer messages and `statuses` receipts."""
    inbound: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for entry in (payload.get("entry") or []):
        if not isinstance(entry, dict):
            continue
        for ch in (entry.get("changes") or []):
            val = (ch or {}).get("value") if isinstance(ch, dict) else None
            if not isinstance(val, dict):
                continue
            meta = val.get("metadata") or {}
            phone_number_id = str(meta.get("phone_number_id") or "")
            profiles = {
                str(ct.get("wa_id") or ""): str(((ct.get("profile") or {}).get("name")) or "")
                for ct in (val.get("contacts") or [])
                if isinstance(ct, dict)
            }
            for msg in (val.get("messages") or []):
                if not isinstance(msg, dict):
                    continue
                wa_from = str(msg.get("from") or "")
                mtype = str(msg.get("type") or "text")
                body_txt = ""
                if mtype == "text":
                    body_txt = str((msg.get("text") or {}).get("body") or "")
                elif mtype == "button":
                    body_txt = str((msg.get("button") or {}).get("text") or "")
                elif mtype == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    body_txt = str(reply.get("title") or "")
                else:
                    body_txt = f"[{mtype}]"
                try:
                    ts = int(msg.get("timestamp") or time.time())
                except (TypeError, ValueError):
                    ts = int(time.time())
                inbound.append({
                    "wamid": str(msg.get("id") or ""),
                    "from_phone": "+" + _digits_phone(wa_from) if wa_from else "",
                    "profile_name": profiles.get(wa_from, ""),
                    "text": body_txt[:2000],
                    "at": ts,
                    "phone_number_id": phone_number_id,
                    "tenant_id": str(val.get("tenant_id") or ""),
                })
            for st in (val.get("statuses") or []):
                if not isinstance(st, dict):
                    continue
                try:
                    ts = int(st.get("timestamp") or time.time())
                except (TypeError, ValueError):
                    ts = int(time.time())
                errs = st.get("errors") or []
                err_code = str(errs[0].get("code")) if errs and isinstance(errs[0], dict) else ""
                statuses.append({
                    "wamid": str(st.get("id") or ""),
                    "status": str(st.get("status") or "").lower(),
                    "at": ts,
                    "error_code": err_code,
                    "phone_number_id": phone_number_id,
                    "tenant_id": str(val.get("tenant_id") or ""),
                })
    return {"inbound": inbound, "statuses": statuses}


# ===========================================================================
# 2. TRAI DLT-registered SMS
# ===========================================================================
BUILTIN_DLT_TEMPLATES: dict[str, dict[str, Any]] = {
    "1107170000000010001": {
        "dlt_template_id": "1107170000000010001",
        "name": "visit_confirm",
        "sender_id": "SNRSUN",
        "category": "service_implicit",
        "pattern": "Namaste {#var#}, your site visit for {#var#} is scheduled on {#var#}. - Sunrise Land",
    },
    "1107170000000010002": {
        "dlt_template_id": "1107170000000010002",
        "name": "missed_call",
        "sender_id": "SNRSUN",
        "category": "service_implicit",
        "pattern": "Namaste {#var#}, we missed your call. Reply YES for RERA doc pack of {#var#}. - Sunrise Land",
    },
    "1107170000000010003": {
        "dlt_template_id": "1107170000000010003",
        "name": "rera_pack",
        "sender_id": "SNRSUN",
        "category": "service_implicit",
        "pattern": "RERA {#var#} doc pack for {#var#} shared with {#var#}. - Sunrise Land",
    },
}

_DLT_ID_RE = re.compile(r"^\d{12,20}$")
_SENDER_ID_RE = re.compile(r"^[A-Za-z0-9]{6}$")


def sms_configured() -> bool:
    return _present("SMS_API_KEY", 16) and _present("SMS_API_URL", 8)


def dlt_templates_for(tenant: dict | None) -> dict[str, dict[str, Any]]:
    merged = {k: dict(v) for k, v in BUILTIN_DLT_TEMPLATES.items()}
    custom = ((tenant or {}).get("settings") or {}).get("dlt_templates") or []
    if isinstance(custom, list):
        for item in custom:
            if isinstance(item, dict) and item.get("dlt_template_id"):
                merged[str(item["dlt_template_id"])] = dict(item)
    return merged


def _dlt_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    parts = pattern.split("{#var#}")
    # TRAI DLT rule: each `{#var#}` substitutes 1..30 printable chars (up to 60 for URLs/names)
    escaped = [re.escape(p) for p in parts]
    return re.compile("^" + r".{1,60}".join(escaped) + "$", flags=re.DOTALL)


def validate_dlt_sms(
    text: str,
    dlt_template_id: str,
    sender_id: str,
    templates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tid = (dlt_template_id or "").strip()
    if not _DLT_ID_RE.match(tid):
        raise ChannelError(
            422,
            "DLT_TEMPLATE_REQUIRED",
            "TRAI DLT requires a registered 12–20 digit `dlt_template_id` for outbound SMS.",
        )
    tpl = templates.get(tid)
    if not tpl:
        raise ChannelError(
            422,
            "DLT_TEMPLATE_UNREGISTERED",
            f"DLT template '{tid}' is not in this workspace's registered DLT ledger.",
        )
    hdr = (sender_id or tpl.get("sender_id") or cfg.get("SMS_SENDER_ID", "") or "SNRSUN").strip().upper()
    if not _SENDER_ID_RE.match(hdr):
        raise ChannelError(422, "DLT_INVALID_HEADER", "DLT sender_id header must be exactly 6 alphanumeric characters.")
    rx = _dlt_pattern_to_regex(str(tpl.get("pattern") or ""))
    if not rx.match(text or ""):
        raise ChannelError(
            422,
            "DLT_TEMPLATE_MISMATCH",
            f"SMS text does not match approved DLT pattern for '{tpl.get('name', tid)}'.",
        )
    return {"dlt_template_id": tid, "sender_id": hdr, "template_name": tpl.get("name", "")}


def send_sms(
    to_phone: str,
    text: str,
    *,
    dlt_template_id: str = "",
    sender_id: str = "",
    tenant: dict | None = None,
    strict_dlt: bool = False,
) -> dict[str, Any]:
    digits = _digits_phone(to_phone)
    if len(digits) < 10:
        raise ChannelError(422, "INVALID_RECIPIENT_PHONE", "Contact has no valid phone number for SMS.")

    sms_conn = conn_mod.active_connector(tenant, "sms")
    live = bool(sms_conn) or sms_configured()
    enforce = bool(
        (live and (not sms_conn or sms_conn.get("preset") != "twilio_sms"))
        or strict_dlt
        or dlt_template_id
        or ((tenant or {}).get("settings") or {}).get("enforce_dlt_templates")
    )
    dlt_meta: dict[str, Any] = {
        "dlt_template_id": dlt_template_id or None,
        "sender_id": (sender_id or cfg.get("SMS_SENDER_ID", "") or "SNRSUN").strip().upper()[:6],
    }
    if enforce:
        dlt_meta = validate_dlt_sms(text, dlt_template_id, sender_id, dlt_templates_for(tenant))

    if sms_conn:
        pe_id = (cfg.get("SMS_ENTITY_ID", "") or ((tenant or {}).get("settings") or {}).get("dlt_entity_id") or "").strip()
        vars_map = {
            "to": digits,
            "phone": "+" + digits,
            "text": text,
            "message": text,
            "sender_id": dlt_meta["sender_id"],
            "pe_id": pe_id,
            "dlt_template_id": dlt_meta.get("dlt_template_id") or "",
            "tenant_id": str((tenant or {}).get("id") or ""),
        }
        try:
            out = conn_mod.execute_connector_http(tenant, sms_conn, vars_map, default_payload=vars_map)
        except conn_mod.ConnectorError as exc:
            raise ChannelError(exc.status, exc.code, exc.message)
        msg_id = (
            out["extracted_id"]
            or conn_mod.extract_dot_path(out["json"], "message_id")
            or conn_mod.extract_dot_path(out["json"], "request_id")
            or conn_mod.extract_dot_path(out["json"], "id")
            or f"sms.conn_{uuid.uuid4().hex[:12]}"
        )
        return {
            "mode": "live",
            "provider": f"connector:{sms_conn.get('slug') or sms_conn.get('preset')}",
            "provider_message_id": str(msg_id),
            "status": str(conn_mod.extract_dot_path(out["json"], "status") or "submitted"),
            **dlt_meta,
        }

    if not live:
        return {
            "mode": "simulated",
            "provider": "dlt-sms-sim",
            "provider_message_id": f"sms.sim_{uuid.uuid4().hex[:12]}",
            "status": "sent",
            **dlt_meta,
        }

    api_url = _allow_endpoint_url(cfg.get("SMS_API_URL", ""), "SMS_API_URL")
    api_key = cfg.get("SMS_API_KEY", "").strip()
    pe_id = (cfg.get("SMS_ENTITY_ID", "") or ((tenant or {}).get("settings") or {}).get("dlt_entity_id") or "").strip()
    payload = {
        "to": digits,
        "sender_id": dlt_meta["sender_id"],
        "pe_id": pe_id,
        "dlt_template_id": dlt_meta["dlt_template_id"],
        "message": text,
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=sec.tenant_egress_hosts(tenant), allow_loopback_dev=True) as resp:
            resp_doc = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        snip = cfg.redact(exc.read(300).decode("utf-8", "replace"))[:140]
        raise ChannelError(502, "SMS_PROVIDER_ERROR", f"DLT SMS gateway HTTP {exc.code}: {snip}")
    except Exception as exc:
        raise ChannelError(502, "SMS_PROVIDER_ERROR", f"DLT SMS gateway unreachable: {cfg.redact(str(exc))[:120]}")

    msg_id = str(resp_doc.get("message_id") or resp_doc.get("id") or f"sms.{uuid.uuid4().hex[:12]}")
    return {
        "mode": "live",
        "provider": "dlt-sms-gateway",
        "provider_message_id": msg_id,
        "status": str(resp_doc.get("status") or "submitted"),
        **dlt_meta,
    }


# ===========================================================================
# 3. Voice Telephony Provider (`Sarvam` default / `ElevenLabs` upsell)
# ===========================================================================
def voice_provider_name() -> str:
    if _present("SARVAM_API_KEY", 16):
        return "sarvam"
    if _present("ELEVENLABS_API_KEY", 16):
        return "elevenlabs"
    return "local-voice-sim"


def voice_configured() -> bool:
    return _present("VOICE_API_URL", 8) and (_present("SARVAM_API_KEY", 16) or _present("ELEVENLABS_API_KEY", 16))


def start_voice_call(
    to_phone: str,
    *,
    tenant_id: str,
    contact_id: str,
    activity_id: str,
    agent_name: str,
    language: str = "mr",
    tenant: dict | None = None,
) -> dict[str, Any]:
    digits = _digits_phone(to_phone)
    if len(digits) < 10:
        raise ChannelError(422, "INVALID_RECIPIENT_PHONE", "Contact has no valid phone number for voice calls.")
    v_conn = conn_mod.active_connector(tenant, "voice")
    if v_conn:
        preset = str(v_conn.get("preset") or "custom_http")
        vars_map = {
            "to": "+" + digits,
            "phone": "+" + digits,
            "digits": digits,
            "provider": preset,
            "language": language,
            "agent_name": agent_name,
            "ai_disclosure_required": True,
            "recording_enabled": True,
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "activity_id": activity_id,
        }
        default_payload = {
            "to": "+" + digits,
            "provider": preset,
            "language": language,
            "agent_name": agent_name,
            "ai_disclosure_required": True,
            "recording_enabled": True,
            "metadata": {"tenant_id": tenant_id, "contact_id": contact_id, "activity_id": activity_id},
        }
        try:
            out = conn_mod.execute_connector_http(tenant, v_conn, vars_map, default_payload=default_payload)
        except conn_mod.ConnectorError as exc:
            raise ChannelError(exc.status, exc.code, exc.message)
        cid_ext = (
            out["extracted_id"]
            or conn_mod.extract_dot_path(out["json"], "call_id")
            or conn_mod.extract_dot_path(out["json"], "id")
            or conn_mod.extract_dot_path(out["json"], "execution_id")
            or f"vcl.conn_{uuid.uuid4().hex[:12]}"
        )
        return {
            "mode": "live",
            "provider": f"connector:{v_conn.get('slug') or preset}",
            "provider_call_id": str(cid_ext),
            "status": str(conn_mod.extract_dot_path(out["json"], "status") or "initiated"),
        }
    prov = voice_provider_name()
    if not voice_configured():
        return {
            "mode": "simulated",
            "provider": prov,
            "provider_call_id": f"vcl.sim_{uuid.uuid4().hex[:12]}",
            "status": "completed",
        }
    api_url = _allow_endpoint_url(cfg.get("VOICE_API_URL", ""), "VOICE_API_URL")
    api_key = cfg.get("SARVAM_API_KEY", "").strip() if prov == "sarvam" else cfg.get("ELEVENLABS_API_KEY", "").strip()
    payload = {
        "to": "+" + digits,
        "provider": prov,
        "language": language,
        "agent_name": agent_name,
        "ai_disclosure_required": True,
        "recording_enabled": True,
        "metadata": {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "activity_id": activity_id,
        },
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=sec.tenant_egress_hosts(tenant), allow_loopback_dev=True) as resp:
            resp_doc = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        snip = cfg.redact(exc.read(300).decode("utf-8", "replace"))[:140]
        raise ChannelError(502, "VOICE_PROVIDER_ERROR", f"Voice provider HTTP {exc.code}: {snip}")
    except Exception as exc:
        raise ChannelError(502, "VOICE_PROVIDER_ERROR", f"Voice provider unreachable: {cfg.redact(str(exc))[:120]}")

    return {
        "mode": "live",
        "provider": prov,
        "provider_call_id": str(resp_doc.get("call_id") or resp_doc.get("id") or f"vcl.{uuid.uuid4().hex[:12]}"),
        "status": str(resp_doc.get("status") or "initiated"),
    }


# ===========================================================================
# 4. Outbound Razorpay Orders API (`/v1/orders`)
# ===========================================================================
def razorpay_configured() -> bool:
    return _present("RAZORPAY_KEY_ID", 8) and _present("RAZORPAY_KEY_SECRET", 16)


def create_razorpay_order(
    *,
    amount_paise: int,
    receipt: str,
    notes: dict[str, str],
    tenant: dict | None = None,
) -> dict[str, Any]:
    """Create a live payment order via Tenant Connector OR Razorpay `/v1/orders`."""
    pay_conn = conn_mod.active_connector(tenant, "payments")
    if pay_conn:
        preset = str(pay_conn.get("preset") or "custom_http")
        clean_notes = {str(k)[:32]: str(v)[:120] for k, v in notes.items()}
        vars_map = {
            "amount_paise": int(amount_paise),
            "amount_inr": round(int(amount_paise) / 100.0, 2),
            "currency": "INR",
            "receipt": str(receipt)[:40],
            "order_id": str(receipt)[:40],
            "notes": clean_notes,
            "tenant_id": str((tenant or {}).get("id") or ""),
        }
        default_wire = {
            "amount": int(amount_paise),
            "currency": "INR",
            "receipt": str(receipt)[:40],
            "notes": clean_notes,
        }
        try:
            out = conn_mod.execute_connector_http(tenant, pay_conn, vars_map, default_payload=default_wire)
        except conn_mod.ConnectorError as exc:
            raise ChannelError(exc.status, exc.code, exc.message)
        oid = (
            out["extracted_id"]
            or conn_mod.extract_dot_path(out["json"], "id")
            or conn_mod.extract_dot_path(out["json"], "order_id")
            or f"order_conn_{uuid.uuid4().hex[:10]}"
        )
        return {
            "mode": "live",
            "provider": f"connector:{pay_conn.get('slug') or preset}",
            "order_id": str(oid),
            "key_id": str(pay_conn.get("key_id_public") or ""),
            "status": str(conn_mod.extract_dot_path(out["json"], "status") or "created"),
        }
    if not razorpay_configured():
        return {
            "mode": "simulated",
            "provider": "none",
            "order_id": "",
            "key_id": "",
        }
    base = _allow_endpoint_url(
        cfg.get("RAZORPAY_API_BASE", "https://api.razorpay.com").rstrip("/"),
        "RAZORPAY_API_BASE",
    )
    key_id = cfg.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = cfg.get("RAZORPAY_KEY_SECRET", "").strip()
    basic = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("ascii")
    wire = {
        "amount": int(amount_paise),
        "currency": "INR",
        "receipt": str(receipt)[:40],
        "notes": {str(k)[:32]: str(v)[:120] for k, v in notes.items()},
    }
    req = urllib.request.Request(
        f"{base}/v1/orders",
        data=json.dumps(wire).encode("utf-8"),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=HTTP_TIMEOUT, extra_hosts=sec.tenant_egress_hosts(tenant), allow_loopback_dev=True) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        snip = cfg.redact(exc.read(300).decode("utf-8", "replace"))[:140]
        raise ChannelError(502, "PAYMENT_PROVIDER_ERROR", f"Razorpay /v1/orders HTTP {exc.code}: {snip}")
    except Exception as exc:
        raise ChannelError(502, "PAYMENT_PROVIDER_ERROR", f"Razorpay /v1/orders unreachable: {cfg.redact(str(exc))[:120]}")

    rzp_id = str(doc.get("id") or "")
    if not rzp_id:
        raise ChannelError(502, "PAYMENT_PROVIDER_ERROR", "Razorpay /v1/orders returned no order id.")
    return {
        "mode": "live",
        "provider": "razorpay",
        "order_id": rzp_id,
        "key_id": key_id,
        "status": str(doc.get("status") or "created"),
    }


def status_summary(tenant: dict | None = None) -> dict[str, Any]:
    wa_conn = conn_mod.active_connector(tenant, "whatsapp")
    sms_conn = conn_mod.active_connector(tenant, "sms")
    v_conn = conn_mod.active_connector(tenant, "voice")
    pay_conn = conn_mod.active_connector(tenant, "payments")
    ai_conn = conn_mod.active_connector(tenant, "ai")
    wa_live = bool(wa_conn) or whatsapp_configured()
    sms_live = bool(sms_conn) or sms_configured()
    v_live = bool(v_conn) or voice_configured()
    pay_live = bool(pay_conn) or razorpay_configured()
    ai_live = bool(ai_conn) or _present("NEON_AI_GATEWAY_KEY", 16) or _present("OPENAI_API_KEY", 16) or _present("ANTHROPIC_API_KEY", 16) or _present("GOOGLE_API_KEY", 16)
    return {
        "ai": {
            "configured": ai_live,
            "mode": "live" if ai_live else "local-twin",
            "active_connector": wa_or_slug(ai_conn),
        },
        "whatsapp": {
            "configured": wa_live,
            "mode": "live" if wa_live else "simulated",
            "active_connector": wa_or_slug(wa_conn),
            "webhook_verify_configured": _present("WHATSAPP_VERIFY_TOKEN", 4),
            "webhook_hmac_configured": _present("WHATSAPP_APP_SECRET", 16) or _present("RE_WEBHOOK_SIGNING_SECRET", 16),
            "window_seconds": WHATSAPP_WINDOW_SECONDS,
            "enforce_24h_window": bool(
                wa_live or ((tenant or {}).get("settings") or {}).get("enforce_whatsapp_24h_window")
            ),
            "templates": len(whatsapp_templates_for(tenant)),
        },
        "sms": {
            "configured": sms_live,
            "mode": "live" if sms_live else "simulated",
            "active_connector": wa_or_slug(sms_conn),
            "sender_id": (cfg.get("SMS_SENDER_ID", "") or "SNRSUN").strip().upper()[:6],
            "entity_id_configured": _present("SMS_ENTITY_ID", 8),
            "enforce_dlt_templates": bool(
                sms_live or ((tenant or {}).get("settings") or {}).get("enforce_dlt_templates")
            ),
            "dlt_templates": len(dlt_templates_for(tenant)),
        },
        "voice": {
            "configured": v_live,
            "mode": "live" if v_live else "local-twin",
            "provider": f"connector:{v_conn.get('slug') or v_conn.get('preset')}" if v_conn else voice_provider_name(),
            "active_connector": wa_or_slug(v_conn),
            "webhook_hmac_configured": _present("VOICE_WEBHOOK_SECRET", 16) or _present("RE_WEBHOOK_SIGNING_SECRET", 16),
        },
        "payments": {
            "orders_configured": pay_live,
            "orders_mode": "live" if pay_live else "simulated",
            "active_connector": wa_or_slug(pay_conn),
            "webhook_configured": _present("PAYMENTS_WEBHOOK_SECRET", 16),
        },
        "auth": {
            "mode": (cfg.get("RE_AUTH_MODE", "local") or "local").lower(),
            "neon_auth_configured": _present("NEON_AUTH_BASE_URL", 4),
        },
    }


def wa_or_slug(conn: dict | None) -> str:
    if not conn:
        return ""
    return str(conn.get("slug") or conn.get("preset") or conn.get("name") or "")

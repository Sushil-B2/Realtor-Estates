"""
`v1.32.0` Approved Post-PRD Expansion Pack (`docs/43`):
  - Option A: Buyer & NRI Self-Serve Portal (`bp1_...` revocable passes) +
              FEMA (Non-Debt Instruments) Rules 2019 Eligibility Engine
  - Option B: Phase 3 Inbound Voice Receptionist + Diarised Call Transcripts &
              Sentiment Trajectory (`mr`/`hi`/`en`)
  - Option C: PWA Field-Agent Idempotent Offline Sync Queue + Web Push Registry
  - Option D: Cryptographically Signed Skill & Funnel Template Pack Exchange
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import uuid
from typing import Any

from . import ai_governance as aigov
from . import security as sec


def _now() -> int:
    return int(time.time())


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sign_blob(domain: str, obj: Any) -> str:
    mac = hmac.new(sec.KEYS["audit"], domain.encode("utf-8") + b":" + _canonical(obj), hashlib.sha256).digest()
    return "sig1_" + base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


# ===========================================================================
# Option A — FEMA Eligibility Engine & Buyer / NRI Self-Serve Portal
# ===========================================================================
RESIDENCY_CLASSES = ("resident_indian", "nri", "oci", "foreign_citizen_non_oci")
PROHIBITED_NRI_CATEGORIES = frozenset({"agricultural", "farmhouse", "plantation"})


def evaluate_fema_eligibility(
    *,
    buyer_residency: str,
    property_category: str,
    acquisition_mode: str = "purchase",
    existing_repatriable_residential_count: int = 0,
    na_order_verified: bool = True,
) -> dict[str, Any]:
    """Evaluate buyer eligibility under FEMA (Non-Debt Instruments) Rules 2019."""
    res = str(buyer_residency or "resident_indian").strip().lower()
    cat = str(property_category or "residential").strip().lower()
    mode = str(acquisition_mode or "purchase").strip().lower()
    if res not in RESIDENCY_CLASSES:
        raise ValueError(f"Unsupported residency '{res}'. Expected one of {RESIDENCY_CLASSES}.")

    if res == "resident_indian":
        notes = ["Resident Indian acquisition permitted under general law."]
        if cat == "agricultural":
            notes.append("Verify state agricultural-tenancy eligibility (e.g. Maharashtra Tenancy & Agricultural Lands Act).")
        return {
            "residency": res,
            "property_category": cat,
            "acquisition_mode": mode,
            "status": "ELIGIBLE",
            "code": "RESIDENT_PERMITTED",
            "rbi_prior_approval_required": False,
            "permitted_payment_channels": ["INR_DOMESTIC_BANK", "UPI", "RTGS_NEFT"],
            "prohibited_payment_modes": ["CASH_ABOVE_20000_269SS"],
            "repatriation_cap_reached": False,
            "notes": notes,
        }

    if res == "foreign_citizen_non_oci":
        return {
            "residency": res,
            "property_category": cat,
            "acquisition_mode": mode,
            "status": "RBI_APPROVAL_REQUIRED",
            "code": "FEMA_FOREIGN_NATIONAL_RESTRICTED",
            "rbi_prior_approval_required": True,
            "permitted_payment_channels": ["NRO_WITH_RBI_APPROVAL"],
            "prohibited_payment_modes": ["TRAVELLERS_CHEQUE", "FOREIGN_CURRENCY_NOTES", "UNAPPROVED_DIRECT_REMITTANCE"],
            "repatriation_cap_reached": False,
            "notes": [
                "Foreign nationals of non-Indian origin resident outside India cannot purchase immovable property in India under FEMA NDI Rules 2019 (leases <= 5 years excepted) without prior RBI approval."
            ],
        }

    # NRI or OCI cardholder — FEMA (Non-Debt Instruments) Rules 2019, Rule 24
    if cat in PROHIBITED_NRI_CATEGORIES:
        if mode == "inheritance":
            return {
                "residency": res,
                "property_category": cat,
                "acquisition_mode": mode,
                "status": "ELIGIBLE_INHERITANCE_ONLY",
                "code": "FEMA_NDI_RULE24_INHERITANCE",
                "rbi_prior_approval_required": False,
                "permitted_payment_channels": [],
                "prohibited_payment_modes": ["TRAVELLERS_CHEQUE", "FOREIGN_CURRENCY_NOTES"],
                "repatriation_cap_reached": False,
                "notes": [
                    "Rule 24(c): NRI/OCI may acquire agricultural/plantation/farmhouse only by inheritance from a person resident in India."
                ],
            }
        return {
            "residency": res,
            "property_category": cat,
            "acquisition_mode": mode,
            "status": "PROHIBITED",
            "code": "FEMA_NDI_RULE24_PROHIBITED",
            "rbi_prior_approval_required": True,
            "permitted_payment_channels": [],
            "prohibited_payment_modes": ["ALL_WITHOUT_RBI_PERMISSION"],
            "repatriation_cap_reached": False,
            "notes": [
                "FEMA (Non-Debt Instruments) Rules 2019 Rule 24: NRIs and OCIs are prohibited from purchasing agricultural land, plantation property, or a farmhouse in India without specific prior Reserve Bank of India (RBI) approval."
            ],
        }

    if cat == "plot" and not na_order_verified:
        return {
            "residency": res,
            "property_category": cat,
            "acquisition_mode": mode,
            "status": "HOLD_PENDING_NA_ORDER",
            "code": "NA_CONVERSION_REQUIRED_FOR_NRI",
            "rbi_prior_approval_required": False,
            "permitted_payment_channels": ["NRE", "NRO", "FCNR_B"],
            "prohibited_payment_modes": ["TRAVELLERS_CHEQUE", "FOREIGN_CURRENCY_NOTES"],
            "repatriation_cap_reached": False,
            "notes": [
                "Plot lacks verified Collector NA (Non-Agricultural) conversion order; hold NRI/OCI booking until NA order is verified so transaction does not breach FEMA agricultural land prohibition."
            ],
        }

    cap_hit = cat == "residential" and int(existing_repatriable_residential_count) >= 2
    notes = [
        "Permitted under FEMA (Non-Debt Instruments) Rules 2019 Rule 24(a): NRI/OCI may purchase residential or commercial immovable property in India.",
        "Consideration must arrive through banking channels in India or NRE / FCNR(B) / NRO accounts — travellers' cheques and foreign currency notes are strictly prohibited.",
    ]
    if cap_hit:
        notes.append("FEMA repatriation ceiling reached: repatriation of sale proceeds out of foreign exchange is restricted to at most 2 residential properties.")
    return {
        "residency": res,
        "property_category": cat,
        "acquisition_mode": mode,
        "status": "ELIGIBLE",
        "code": "FEMA_NDI_RULE24_PERMITTED",
        "rbi_prior_approval_required": False,
        "permitted_payment_channels": ["NRE", "NRO", "FCNR_B", "INWARD_BANKING_REMITTANCE"],
        "prohibited_payment_modes": ["TRAVELLERS_CHEQUE", "FOREIGN_CURRENCY_NOTES"],
        "repatriation_cap_reached": cap_hit,
        "notes": notes,
    }


def issue_buyer_portal_pass(
    store,
    tenant: dict[str, Any],
    deal_id: str,
    *,
    ttl_days: int = 30,
    buyer_residency: str = "resident_indian",
    actor: str = "",
) -> dict[str, Any]:
    tid = tenant["id"]
    deal = store.get("deals", tid, deal_id)
    if not deal:
        raise LookupError("Deal not found.")
    if buyer_residency not in RESIDENCY_CLASSES:
        raise ValueError(f"Unsupported buyer_residency '{buyer_residency}'.")
    ts = _now()
    days = max(1, min(180, int(ttl_days)))
    exp = ts + days * 86400
    jti = "bpp_" + base64.urlsafe_b64encode(os.urandom(9)).decode("ascii").rstrip("=")
    claims = {"v": 1, "tid": tid, "did": deal_id, "jti": jti, "iat": ts, "exp": exp}
    raw = _canonical(claims)
    b64_c = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    mac = hmac.new(sec.KEYS["session"], b"buyer-portal-v1:" + raw, hashlib.sha256).digest()[:20]
    b64_m = base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
    token = f"bp1_{b64_c}.{b64_m}"

    meta = {
        "pass_jti": jti,
        "issued_at": ts,
        "expires_at": exp,
        "buyer_residency": buyer_residency,
        "revoked": False,
        "issued_by": actor,
    }
    store.update("deals", tid, deal_id, {"buyer_portal": meta})
    return {
        "token": token,
        "portal_api_path": f"/api/v1/public/buyer-portal/{token}",
        "expires_at": exp,
        "buyer_residency": buyer_residency,
    }


def revoke_buyer_portal_pass(store, tid: str, deal_id: str, *, actor: str = "") -> dict[str, Any]:
    deal = store.get("deals", tid, deal_id)
    if not deal:
        raise LookupError("Deal not found.")
    meta = dict(deal.get("buyer_portal") or {})
    meta["revoked"] = True
    meta["revoked_at"] = _now()
    meta["revoked_by"] = actor
    store.update("deals", tid, deal_id, {"buyer_portal": meta})
    return {"revoked": True, "deal_id": deal_id}


def resolve_buyer_portal_view(store, token: str, *, now_ts: int | None = None) -> dict[str, Any]:
    if not token or not token.startswith("bp1_") or "." not in token:
        raise PermissionError("INVALID_PORTAL_PASS")
    body_b64, mac_b64 = token[4:].split(".", 1)
    try:
        raw = base64.urlsafe_b64decode(body_b64 + "=" * (-len(body_b64) % 4))
        got_mac = base64.urlsafe_b64decode(mac_b64 + "=" * (-len(mac_b64) % 4))
        claims = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PermissionError("INVALID_PORTAL_PASS") from exc

    exp_mac = hmac.new(sec.KEYS["session"], b"buyer-portal-v1:" + raw, hashlib.sha256).digest()[:20]
    if not hmac.compare_digest(got_mac, exp_mac):
        raise PermissionError("INVALID_PORTAL_PASS")

    ts = _now() if now_ts is None else int(now_ts)
    if int(claims.get("exp") or 0) < ts:
        raise PermissionError("EXPIRED_PORTAL_PASS")

    tid = str(claims.get("tid") or "")
    did = str(claims.get("did") or "")
    tenant = store.get("tenants", tid, tid)
    deal = store.get("deals", tid, did) if tenant else None
    if not tenant or not deal:
        raise PermissionError("INVALID_PORTAL_PASS")

    bp_meta = deal.get("buyer_portal") or {}
    if bp_meta.get("revoked") or bp_meta.get("pass_jti") != claims.get("jti"):
        raise PermissionError("REVOKED_PORTAL_PASS")

    prop = store.get("properties", tid, deal.get("property_id", "")) or {}
    unit = store.get("units", tid, deal.get("unit_id", "")) or {}
    contact = store.get("contacts", tid, deal.get("contact_id", "")) or {}

    raw_docs = deal.get("documents") or prop.get("documents") or {}
    if isinstance(raw_docs, list):
        docs = {str(d.get("name")): d.get("status", "received") for d in raw_docs if isinstance(d, dict) and d.get("name")}
    elif isinstance(raw_docs, dict):
        docs = dict(raw_docs)
    else:
        docs = {}
    na_ok = bool(docs.get("na_order", prop.get("na_order", True))) if prop.get("category") != "agricultural" else False
    fema = evaluate_fema_eligibility(
        buyer_residency=str(bp_meta.get("buyer_residency") or "resident_indian"),
        property_category=str(prop.get("category") or "residential"),
        na_order_verified=na_ok,
    )

    receipts = [
        {
            "number": i.get("number"),
            "type": i.get("type"),
            "issued_at": i.get("issued_at"),
            "total": i.get("total"),
            "status": i.get("status"),
        }
        for i in store.all("invoices", tid)
        if i.get("deal_id") == did and i.get("type") in ("receipt", "refund_voucher")
    ]
    payments = [
        {
            "kind": p.get("kind"),
            "amount": p.get("amount"),
            "status": p.get("status"),
            "method": p.get("method"),
            "at": p.get("at"),
        }
        for p in store.all("payments", tid)
        if p.get("deal_id") == did
    ]
    visits = [
        {
            "scheduled_at": v.get("scheduled_at"),
            "status": v.get("status"),
        }
        for v in store.all("site_visits", tid)
        if v.get("deal_id") == did or (contact.get("id") and v.get("contact_id") == contact.get("id"))
    ]
    from . import govt_sales_cycle as gsc_mod

    revenue_clearance = gsc_mod.evaluate_revenue_clearance(prop) if prop else {}
    govt_registration = deal.get("govt_registration") or {}
    home_loan = deal.get("home_loan") or {}
    clp_schedule = deal.get("clp_schedule") or {}
    post_reg_handover = deal.get("post_reg_handover") or {}

    return {
        "schema": "realtor-estates.buyer-portal.v1",
        "workspace": {"name": tenant.get("name"), "slug": tenant.get("slug")},
        "buyer": {"name": contact.get("name", "Valued Buyer"), "residency": bp_meta.get("buyer_residency", "resident_indian")},
        "deal": {
            "id": deal["id"],
            "title": deal.get("title"),
            "stage": deal.get("stage"),
            "value": deal.get("value"),
            "documents": docs,
        },
        "property": {
            "title": prop.get("title", ""),
            "city": prop.get("city", ""),
            "category": prop.get("category", "residential"),
            "rera_id": prop.get("rera") or prop.get("rera_id") or "",
            "area_sqft": prop.get("area_sqft") or prop.get("carpet_area") or 0,
        },
        "unit": {
            "unit_no": unit.get("unit_no", ""),
            "status": unit.get("status", ""),
        },
        "fema_eligibility": fema,
        "revenue_clearance": revenue_clearance,
        "govt_registration": govt_registration,
        "home_loan": home_loan,
        "clp_schedule": clp_schedule,
        "post_reg_handover": post_reg_handover,
        "receipts": receipts,
        "payments": payments,
        "site_visits": visits,
        "expires_at": bp_meta.get("expires_at"),
    }


# ===========================================================================
# Option B — Phase 3 Inbound Voice Receptionist & Diarised Sentiment
# ===========================================================================
POSITIVE_LEX = ("interested", "visit", "saturday", "sunday", "book", "ready", "great", "thanks", "हो", "नक्की", "भेटीसाठी", "हां", "ज़रूर")
NEGATIVE_LEX = ("angry", "fraud", "cancel", "delay", "terrible", "human", "manager", "supervisor", "stop", "complaint", "तक्रार", "नको", "शिकायत", "बंद")

AI_DISCLOSURE_TURN = {
    "mr": "नमस्कार, मी मीरा — {workspace} ची AI स्वागत सहाय्यक बोलत आहे. हा संवाद गुणवत्तेसाठी नोंदवला जाऊ शकतो.",
    "hi": "नमस्ते, मैं मीरा — {workspace} की AI रिसेप्शनिस्ट बोल रही हूँ। यह कॉल गुणवत्ता के लिए रिकॉर्ड की जा सकती है।",
    "en": "Hello, I am Meera, the AI receptionist for {workspace}. This call may be recorded for quality and compliance.",
}


def score_utterance_sentiment(text: str) -> float:
    low = (text or "").lower()
    pos = sum(1 for w in POSITIVE_LEX if w in low)
    neg = sum(1 for w in NEGATIVE_LEX if w in low)
    raw = (pos * 0.35) - (neg * 0.5)
    return round(max(-1.0, min(1.0, raw)), 2)


def process_inbound_voice_call(
    store,
    tenant: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "voice-receptionist",
) -> dict[str, Any]:
    tid = tenant["id"]
    ts = _now()
    lang = str(payload.get("language") or "mr").strip().lower()
    if lang not in ("mr", "hi", "en"):
        lang = "mr"
    caller_name = sec.clean(str(payload.get("caller_name") or "Inbound Caller"), 80)
    phone = str(payload.get("phone") or "+919822001122").strip()
    if not sec.valid_phone(phone):
        raise ValueError("INVALID_PHONE: Provide a valid caller phone number.")

    raw_turns = payload.get("turns")
    disclosure = AI_DISCLOSURE_TURN[lang].format(workspace=tenant.get("name", "Realtor Estates"))
    diarised: list[dict[str, Any]] = [
        {
            "turn": 0,
            "speaker": "receptionist",
            "start_ms": 0,
            "end_ms": 3200,
            "text": disclosure,
            "ai_disclosed": True,
            "sentiment": 0.1,
        }
    ]
    cursor_ms = 3400
    if isinstance(raw_turns, list) and raw_turns:
        for idx, item in enumerate(raw_turns[:20], start=1):
            spk = "caller" if str((item or {}).get("speaker") or "caller").lower() in ("caller", "buyer", "user") else "receptionist"
            txt = sec.redact_secrets(sec.clean(str((item or {}).get("text") or ""), 400))
            dur = max(1200, min(15000, int((item or {}).get("duration_ms") or 2800)))
            diarised.append({
                "turn": idx,
                "speaker": spk,
                "start_ms": cursor_ms,
                "end_ms": cursor_ms + dur,
                "text": txt,
                "sentiment": score_utterance_sentiment(txt),
            })
            cursor_ms += dur + 200
    else:
        caller_utterance = sec.redact_secrets(sec.clean(str(payload.get("utterance") or "Plot 47 साठी शनिवारी site visit ठेवायची आहे."), 400))
        diarised.append({
            "turn": 1,
            "speaker": "caller",
            "start_ms": 3400,
            "end_ms": 6800,
            "text": caller_utterance,
            "sentiment": score_utterance_sentiment(caller_utterance),
        })
        diarised.append({
            "turn": 2,
            "speaker": "receptionist",
            "start_ms": 7000,
            "end_ms": 10200,
            "text": "नक्कीच! तुमची नोंद घेतली असून आमचे सल्लागार ७/१२ व MahaRERA माहितीसह संपर्क करतील.",
            "sentiment": 0.35,
        })
        cursor_ms = 10200

    caller_scores = [t["sentiment"] for t in diarised if t["speaker"] == "caller"] or [0.0]
    opening = caller_scores[0]
    closing = caller_scores[-1]
    min_score = min(caller_scores)
    all_caller_text = " ".join(t["text"].lower() for t in diarised if t["speaker"] == "caller")
    human_asked = any(w in all_caller_text for w in ("human", "manager", "supervisor", "तक्रार", "शिकायत"))
    escalate = bool(min_score <= -0.35 or human_asked)

    trajectory = {
        "opening_score": opening,
        "closing_score": closing,
        "delta": round(closing - opening, 2),
        "min_score": min_score,
        "escalated_to_human": escalate,
    }

    # Resolve or create contact via tenant-scoped blind index (`F-07`)
    existing = sec.find_by_blind_index(store, "contacts", tid, "phone_hash", phone)
    if existing:
        contact = existing[0]
        store.update("contacts", tid, contact["id"], {"last_activity_at": ts})
    else:
        contact = store.insert(
            "contacts",
            tid,
            {
                "name": caller_name,
                "phone_enc": sec.seal(phone, aad=tid),
                "phone_hash": sec.blind_index(phone, tenant_id=tid),
                "email_enc": sec.seal("", aad=tid),
                "email_hash": "",
                "source": "Inbound AI Receptionist",
                "stage": "new",
                "score": 68 if closing >= 0 else 45,
                "requirement": diarised[1]["text"][:160] if len(diarised) > 1 else "Inbound call enquiry",
                "consent": {
                    "status": "granted",
                    "purpose": "inbound_telephone_enquiry",
                    "channel": "voice",
                    "captured_at": ts,
                    "evidence": f"inbound_voice:{ts}",
                },
                "last_activity_at": ts,
            },
            prefix="cnt",
        )

    act = store.insert(
        "activities",
        tid,
        {
            "contact_id": contact["id"],
            "type": "call",
            "direction": "in",
            "outcome": "escalated" if escalate else "answered_by_ai",
            "language": lang,
            "ai_disclosed": True,
            "duration_sec": max(1, round(cursor_ms / 1000)),
            "diarised_turns": diarised,
            "sentiment_trajectory": trajectory,
            "text": f"[Inbound Receptionist · {lang}] {diarised[1]['text'][:180] if len(diarised) > 1 else ''}",
            "actor": actor,
            "at": ts,
        },
        prefix="act",
    )

    task = store.insert(
        "tasks",
        tid,
        {
            "contact_id": contact["id"],
            "title": (
                f"URGENT human callback — negative/escalation sentiment ({min_score}) for {contact.get('name')}"
                if escalate
                else f"Confirm site-visit slot from inbound call — {contact.get('name')}"
            )[:120],
            "priority": "high" if escalate else "medium",
            "due_at": ts + (900 if escalate else 3600),
            "done": False,
        },
        prefix="tsk",
    )
    return {
        "contact_id": contact["id"],
        "activity": act,
        "task": task,
        "sentiment_trajectory": trajectory,
        "diarised_turn_count": len(diarised),
    }


# ===========================================================================
# Option C — PWA Field-Agent Offline Sync Queue & Web Push Subscriptions
# ===========================================================================
STANDARD_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
    "notify.windows.com",
)


def register_push_subscription(store, tenant: dict[str, Any], user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    tid = tenant["id"]
    endpoint = str(payload.get("endpoint") or "").strip()
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("INVALID_PUSH_ENDPOINT: Web Push endpoint must be an https:// URL.")
    host = parsed.hostname.lower()
    if not any(host == h or host.endswith("." + h) for h in STANDARD_PUSH_HOSTS):
        ok_ssrf, reason = sec.ssrf_check(endpoint)
        if not ok_ssrf:
            raise ValueError(f"PUSH_SSRF_BLOCKED: {reason}")

    keys_obj = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    sub_id = "psh_" + hashlib.sha256(f"{user.get('id')}:{endpoint}".encode("utf-8")).hexdigest()[:10]
    sealed_keys = sec.seal(json.dumps(keys_obj), aad=f"{tid}:push:{sub_id}")

    settings = dict(tenant.get("settings") or {})
    subs = [s for s in list(settings.get("push_subscriptions") or []) if s.get("id") != sub_id]
    entry = {
        "id": sub_id,
        "user_id": user.get("id", ""),
        "host": host,
        "endpoint_Seal": sealed_keys,
        "endpoint_enc": sec.seal(endpoint, aad=f"{tid}:push:{sub_id}"),
        "topics": [str(t)[:40] for t in (payload.get("topics") or ["sla_breach", "approvals"])[:6]],
        "created_at": _now(),
    }
    subs.insert(0, entry)
    settings["push_subscriptions"] = subs[:50]
    store.update("tenants", tid, tid, {"settings": settings})
    return {
        "id": sub_id,
        "user_id": entry["user_id"],
        "host": host,
        "topics": entry["topics"],
        "created_at": entry["created_at"],
    }


def list_push_subscriptions(tenant: dict[str, Any], *, user_id: str = "") -> list[dict[str, Any]]:
    subs = list((tenant.get("settings") or {}).get("push_subscriptions") or [])
    out = []
    for s in subs:
        if user_id and s.get("user_id") != user_id:
            continue
        out.append({
            "id": s.get("id"),
            "user_id": s.get("user_id"),
            "host": s.get("host"),
            "topics": s.get("topics", []),
            "created_at": s.get("created_at"),
        })
    return out


def remove_push_subscription(store, tenant: dict[str, Any], sub_id: str, *, user_id: str = "", is_admin: bool = False) -> bool:
    tid = tenant["id"]
    settings = dict(tenant.get("settings") or {})
    subs = list(settings.get("push_subscriptions") or [])
    kept = [
        s for s in subs
        if not (s.get("id") == sub_id and (is_admin or s.get("user_id") == user_id))
    ]
    if len(kept) == len(subs):
        return False
    settings["push_subscriptions"] = kept
    store.update("tenants", tid, tid, {"settings": settings})
    return True


def sync_offline_mutations(
    store,
    tenant: dict[str, Any],
    user: dict[str, Any],
    mutations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replay queued field-agent offline mutations idempotently by `client_mutation_id`."""
    tid = tenant["id"]
    ts = _now()
    actor = user.get("email") or user.get("id") or "field-agent"
    settings = dict(tenant.get("settings") or {})
    seen_ids = set(settings.get("offline_mutation_ids") or [])
    results: list[dict[str, Any]] = []

    for item in (mutations or [])[:50]:
        if not isinstance(item, dict):
            continue
        cmid = sec.clean(str(item.get("client_mutation_id") or ""), 80)
        kind = str(item.get("kind") or "").strip().lower()
        if not cmid:
            results.append({"client_mutation_id": "", "status": "rejected", "reason": "missing client_mutation_id"})
            continue
        if cmid in seen_ids:
            results.append({"client_mutation_id": cmid, "status": "deduplicated"})
            continue

        if kind == "note.add":
            cid = str(item.get("contact_id") or "").strip()
            text = sec.clean(str(item.get("text") or ""), 500)
            contact = store.get("contacts", tid, cid) if cid else None
            if not contact or not text:
                results.append({"client_mutation_id": cmid, "status": "rejected", "reason": "invalid contact_id or empty note"})
                continue
            act = store.insert("activities", tid, {
                "contact_id": cid,
                "type": "note",
                "text": text,
                "actor": actor,
                "at": int(item.get("queued_at") or ts),
                "offline_mutation_id": cmid,
            }, prefix="act")
            store.update("contacts", tid, cid, {"last_activity_at": ts})
            seen_ids.add(cmid)
            results.append({"client_mutation_id": cmid, "status": "applied", "record_id": act["id"]})

        elif kind == "visit.outcome":
            vid = str(item.get("visit_id") or "").strip()
            v_status = str(item.get("status") or "visited").strip()
            notes = sec.clean(str(item.get("notes") or ""), 400)
            visit = store.get("site_visits", tid, vid) if vid else None
            if not visit or v_status not in ("confirmed", "visited", "revisit", "no_show", "dropped"):
                results.append({"client_mutation_id": cmid, "status": "rejected", "reason": "invalid visit_id or status"})
                continue
            store.update("site_visits", tid, vid, {
                "status": v_status,
                "outcome_notes": notes,
                "updated_at": ts,
                "offline_mutation_id": cmid,
            })
            seen_ids.add(cmid)
            results.append({"client_mutation_id": cmid, "status": "applied", "record_id": vid})

        elif kind == "task.complete":
            tid_row = str(item.get("task_id") or "").strip()
            task = store.get("tasks", tid, tid_row) if tid_row else None
            if not task:
                results.append({"client_mutation_id": cmid, "status": "rejected", "reason": "task not found"})
                continue
            store.update("tasks", tid, tid_row, {"done": True, "updated_at": ts, "offline_mutation_id": cmid})
            seen_ids.add(cmid)
            results.append({"client_mutation_id": cmid, "status": "applied", "record_id": tid_row})

        else:
            results.append({"client_mutation_id": cmid, "status": "rejected", "reason": f"unsupported kind '{kind}'"})

    settings["offline_mutation_ids"] = list(seen_ids)[-500:]
    store.update("tenants", tid, tid, {"settings": settings})
    return {
        "processed": len(results),
        "applied": sum(1 for r in results if r["status"] == "applied"),
        "deduplicated": sum(1 for r in results if r["status"] == "deduplicated"),
        "results": results,
    }


# ===========================================================================
# Option D — Signed Skill & Funnel Template Pack Exchange
# ===========================================================================
def export_template_pack(
    store,
    tenant: dict[str, Any],
    *,
    funnel_ids: list[str] | None = None,
    agent_ids: list[str] | None = None,
    pack_name: str = "Verified RERA Pack",
    actor: str = "",
) -> dict[str, Any]:
    tid = tenant["id"]
    funnels_out: list[dict[str, Any]] = []
    for fid in (funnel_ids or [])[:10]:
        f = store.get("funnels", tid, str(fid))
        if not f:
            continue
        headline = str(f.get("headline") or f.get("name") or "")
        sub = str(f.get("subhead") or f.get("description") or "")
        blob = f"{headline} {sub}"
        if sec.inspect_text(blob)["action"] in ("block", "quarantine") or aigov.inspect_rera_advertising(blob):
            raise ValueError(f"PACK_GUARDRAIL_FAILED: Funnel '{f.get('name')}' failed RERA/guardrail pre-export check.")
        funnels_out.append({
            "name": sec.clean(str(f.get("name") or "Funnel Template"), 100),
            "headline": sec.clean(headline, 160),
            "subhead": sec.clean(sub, 240),
            "cta": sec.clean(str(f.get("cta") or "Request RERA Document Pack"), 80),
        })

    skills_out: list[dict[str, Any]] = []
    for aid in (agent_ids or [])[:10]:
        ag = store.get("agents", tid, str(aid))
        if not ag:
            continue
        for sk in (ag.get("skills") or [])[:10]:
            sname = sec.clean(str(sk.get("name") or "Skill"), 100)
            srule = sec.clean(str(sk.get("rule") or sk.get("description") or sname), 300)
            if sec.inspect_text(srule)["action"] in ("block", "quarantine") or aigov.inspect_rera_advertising(srule):
                raise ValueError(f"PACK_GUARDRAIL_FAILED: Skill '{sname}' failed RERA/guardrail pre-export check.")
            skills_out.append({
                "department": ag.get("department", "sales"),
                "name": sname,
                "rule": srule,
                "version": int(sk.get("version") or 1),
            })

    manifest = {
        "schema": "realtor-estates.template-pack.v1",
        "pack_id": "tpk_" + uuid.uuid4().hex[:10],
        "name": sec.clean(pack_name, 100),
        "exported_at": _now(),
        "exported_by_workspace": tenant.get("slug", ""),
        "funnels": funnels_out,
        "skills": skills_out,
    }
    return {**manifest, "signature": _sign_blob("template-pack-v1", manifest)}


def import_template_pack(
    store,
    tenant: dict[str, Any],
    pack: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    tid = tenant["id"]
    if not isinstance(pack, dict) or pack.get("schema") != "realtor-estates.template-pack.v1":
        raise ValueError("PACK_SCHEMA_INVALID: Expected 'realtor-estates.template-pack.v1'.")
    sig = str(pack.get("signature") or "")
    manifest = {k: v for k, v in pack.items() if k != "signature"}
    expected_sig = _sign_blob("template-pack-v1", manifest)
    if not sig or not hmac.compare_digest(sig, expected_sig):
        raise ValueError("PACK_SIGNATURE_INVALID: Template pack signature verification failed.")

    imported_funnels: list[str] = []
    for f in (manifest.get("funnels") or [])[:10]:
        blob = f"{f.get('name', '')} {f.get('headline', '')} {f.get('subhead', '')}"
        if sec.inspect_text(blob)["action"] in ("block", "quarantine") or aigov.inspect_rera_advertising(blob):
            raise ValueError("PACK_GUARDRAIL_FAILED: Funnel content blocked by guardrail/RERA check.")
        slug_base = "".join(ch if ch.isalnum() else "-" for ch in str(f.get("name") or "pack-funnel").lower()).strip("-")[:32] or "pack-funnel"
        doc = store.insert("funnels", tid, {
            "name": sec.clean(str(f.get("name") or "Imported Funnel"), 100),
            "slug": f"{slug_base}-{uuid.uuid4().hex[:6]}",
            "headline": sec.clean(str(f.get("headline") or ""), 160),
            "subhead": sec.clean(str(f.get("subhead") or ""), 240),
            "cta": sec.clean(str(f.get("cta") or "Get Details"), 80),
            "published": False,  # always imported as draft requiring human publish
            "submissions": 0,
            "imported_from_pack": manifest.get("pack_id"),
            "created_at": _now(),
        }, prefix="fnl")
        imported_funnels.append(doc["id"])

    agents = store.all("agents", tid)
    target_agent = agents[0] if agents else None
    staged_proposals: list[str] = []
    for sk in (manifest.get("skills") or [])[:10]:
        prop = store.insert("proposals", tid, {
            "agent_id": target_agent["id"] if target_agent else "",
            "agent_name": target_agent.get("name", "") if target_agent else str(sk.get("agent_role") or "Workspace Agent"),
            "title": f"[Pack Import] {sec.clean(str(sk.get('name') or 'Skill'), 80)}",
            "observation": f"Imported from signed pack {manifest.get('pack_id')}",
            "risk": "medium",
            "status": "pending",  # never bypasses human learning gate (D9)!
            "skill_diff": {"add": sec.clean(str(sk.get("rule") or ""), 300)},
            "created_at": _now(),
            "proposed_by": actor,
        }, prefix="prp")
        sh = aigov.run_shadow_eval(store, tid, prop)
        if not sh.get("safe_to_promote"):
            store.remove("proposals", tid, prop["id"])
            raise ValueError(f"PACK_GUARDRAIL_FAILED: Skill '{sk.get('name')}' failed shadow benchmark.")
        staged_proposals.append(prop["id"])

    return {
        "pack_id": manifest.get("pack_id"),
        "imported_funnels": imported_funnels,
        "staged_skill_proposals": staged_proposals,
    }


# =============================================================================
# Phase 3 — Ads Autopilot with Hard Spend Caps & Human Budget Approval
# =============================================================================
DEFAULT_ADS_POLICY: dict[str, Any] = {
    "hard_daily_cap_inr": 50_000,
    "hard_monthly_cap_inr": 1_000_000,
    "max_cpl_inr": 2_500,
    "min_scale_roas": 3.0,
    "require_approval_above_pct": 20,
}


def get_ads_policy(tenant: dict[str, Any]) -> dict[str, Any]:
    raw = dict(tenant.get("ads_policy") or {})
    return {
        "hard_daily_cap_inr": max(1_000, min(5_000_000, int(raw.get("hard_daily_cap_inr") or DEFAULT_ADS_POLICY["hard_daily_cap_inr"]))),
        "hard_monthly_cap_inr": max(5_000, min(50_000_000, int(raw.get("hard_monthly_cap_inr") or DEFAULT_ADS_POLICY["hard_monthly_cap_inr"]))),
        "max_cpl_inr": max(100, min(100_000, int(raw.get("max_cpl_inr") or DEFAULT_ADS_POLICY["max_cpl_inr"]))),
        "min_scale_roas": max(1.0, min(20.0, float(raw.get("min_scale_roas") or DEFAULT_ADS_POLICY["min_scale_roas"]))),
        "require_approval_above_pct": max(1, min(500, int(raw.get("require_approval_above_pct") or DEFAULT_ADS_POLICY["require_approval_above_pct"]))),
    }


def update_ads_policy(store: Any, tenant: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = {**get_ads_policy(tenant), **(patch or {})}
    clean_policy = get_ads_policy({"ads_policy": merged})
    if clean_policy["hard_monthly_cap_inr"] < clean_policy["hard_daily_cap_inr"]:
        raise ValueError("ADS_INVALID_POLICY: hard_monthly_cap_inr must be >= hard_daily_cap_inr.")
    store.update("tenants", tenant["id"], tenant["id"], {"ads_policy": clean_policy})
    return clean_policy


def check_campaign_spend_headroom(
    store: Any,
    tenant: dict[str, Any],
    *,
    campaign_id: str = "",
    proposed_daily_inr: int = 0,
) -> dict[str, Any]:
    policy = get_ads_policy(tenant)
    campaigns = store.all("campaigns", tenant["id"])
    total_spend = sum(int(c.get("spend") or 0) for c in campaigns)
    other_active_daily = sum(
        int(c.get("budget_daily") or 0)
        for c in campaigns
        if c.get("status") in ("active", "draft") and c.get("id") != campaign_id
    )
    projected_daily = other_active_daily + int(proposed_daily_inr or 0)
    return {
        "policy": policy,
        "total_spend_inr": total_spend,
        "other_daily_inr": other_active_daily,
        "projected_daily_inr": projected_daily,
        "daily_cap_exceeded": projected_daily > policy["hard_daily_cap_inr"],
        "monthly_cap_exceeded": total_spend >= policy["hard_monthly_cap_inr"],
    }


def stage_campaign_budget_request(
    store: Any,
    tenant: dict[str, Any],
    campaign: dict[str, Any],
    proposed_budget_daily: int,
    *,
    reason: str = "",
    actor: str = "autopilot",
) -> dict[str, Any]:
    tid = tenant["id"]
    proposed = int(proposed_budget_daily)
    current = int(campaign.get("budget_daily") or 0)
    if proposed <= current:
        raise ValueError("ADS_BUDGET_NOT_INCREASED: Proposed budget must exceed current daily budget.")

    headroom = check_campaign_spend_headroom(
        store, tenant, campaign_id=campaign["id"], proposed_daily_inr=proposed
    )
    if headroom["monthly_cap_exceeded"]:
        raise ValueError("ADS_MONTHLY_SPEND_CAP_EXCEEDED: Workspace cumulative spend reached hard monthly cap.")
    if headroom["daily_cap_exceeded"]:
        raise ValueError(
            f"ADS_HARD_SPEND_CAP_EXCEEDED: Projected daily spend ({headroom['projected_daily_inr']}) exceeds hard daily cap ({headroom['policy']['hard_daily_cap_inr']})."
        )

    for existing in store.all("proposals", tid):
        if (
            existing.get("kind") == "ads_budget_increase"
            and existing.get("campaign_id") == campaign["id"]
            and existing.get("status") == "pending"
        ):
            return existing

    pct_up = round(((proposed - max(1, current)) / max(1, current)) * 100.0, 1)
    prop = store.insert("proposals", tid, {
        "kind": "ads_budget_increase",
        "campaign_id": campaign["id"],
        "campaign_name": campaign.get("name", ""),
        "current_budget_daily": current,
        "proposed_budget_daily": proposed,
        "agent_id": "",
        "agent_name": "Ads Autopilot",
        "title": f"[Ads Budget +{pct_up}%] {campaign.get('name', 'Campaign')}: ₹{current} → ₹{proposed}/day",
        "observation": sec.clean(reason or f"ROAS {campaign.get('roas', 0)}x, CPL ₹{campaign.get('cpl', 0)} within ceiling.", 300),
        "risk": "high",
        "status": "pending",
        "skill_diff": {"budget_daily": proposed},
        "created_at": _now(),
        "proposed_by": actor,
    }, prefix="prp")
    return prop


def run_ads_autopilot(
    store: Any,
    tenant: dict[str, Any],
    *,
    actor: str = "autopilot",
) -> dict[str, Any]:
    """Evaluate active campaigns against hard spend caps, CPL ceiling & ROAS scaling rules.

    Safety invariants (`docs/09` Phase 3):
      - Autopilot MAY pause bleeding campaigns or enforce the hard monthly cap immediately.
      - Autopilot NEVER increases `budget_daily` autonomously — every scale-up is staged
        in `proposals` (`risk = "high"`, `status = "pending"`) requiring human approval.
    """
    tid = tenant["id"]
    policy = get_ads_policy(tenant)
    campaigns = store.all("campaigns", tid)
    total_spend = sum(int(c.get("spend") or 0) for c in campaigns)

    paused_ids: list[str] = []
    staged_proposals: list[dict[str, Any]] = []
    notes: list[str] = []

    if total_spend >= policy["hard_monthly_cap_inr"]:
        for c in campaigns:
            if c.get("status") == "active":
                store.update("campaigns", tid, c["id"], {
                    "status": "paused",
                    "autopilot_note": "monthly_hard_cap_reached",
                })
                paused_ids.append(c["id"])
        notes.append(f"Hard monthly spend cap (₹{policy['hard_monthly_cap_inr']}) reached; {len(paused_ids)} active campaign(s) paused.")
        return {
            "policy": policy,
            "total_spend_inr": total_spend,
            "hard_cap_halted": True,
            "paused_campaigns": paused_ids,
            "staged_budget_proposals": [],
            "notes": notes,
        }

    for c in campaigns:
        if c.get("status") != "active":
            continue
        cpl = int(c.get("cpl") or 0)
        leads = int(c.get("leads") or 0)
        spend = int(c.get("spend") or 0)
        roas = float(c.get("roas") or 0.0)
        cur_budget = int(c.get("budget_daily") or 0)

        if (cpl > policy["max_cpl_inr"] and leads >= 1) or (leads == 0 and spend > 2 * policy["max_cpl_inr"]):
            store.update("campaigns", tid, c["id"], {
                "status": "paused",
                "autopilot_note": "cpl_ceiling_breached",
            })
            paused_ids.append(c["id"])
            notes.append(f"Paused '{c.get('name')}' (CPL ₹{cpl} > ceiling ₹{policy['max_cpl_inr']}).")
            continue

        if roas >= policy["min_scale_roas"] and (cpl == 0 or cpl <= policy["max_cpl_inr"]):
            headroom = check_campaign_spend_headroom(store, tenant, campaign_id=c["id"], proposed_daily_inr=cur_budget)
            avail = policy["hard_daily_cap_inr"] - headroom["other_daily_inr"]
            target = min(avail, int(round(cur_budget * 1.25)))
            if target > cur_budget:
                prop = stage_campaign_budget_request(
                    store,
                    tenant,
                    c,
                    target,
                    reason=f"Autopilot scale recommendation: ROAS {roas}x >= {policy['min_scale_roas']}x, CPL ₹{cpl} <= ₹{policy['max_cpl_inr']}.",
                    actor=actor,
                )
                staged_proposals.append(prop)

    return {
        "policy": policy,
        "total_spend_inr": total_spend,
        "hard_cap_halted": False,
        "paused_campaigns": paused_ids,
        "staged_budget_proposals": [p["id"] for p in staged_proposals],
        "notes": notes,
    }


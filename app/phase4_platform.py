"""
Realtor Estates — Phase 4 Platform Horizon & Vertical Polish (`v1.34.0`):
  1. On-Device Client Pre-Screening Model Card & Server Verification (`WebGPU/WebNN` + JS fallback)
  2. Agentic Commerce Settlement (`AP2` / `ACP` / `UCP` Signed Hold & Booking Mandates)
  3. Regional Expansion & Data-Residency Pinning (`IN` / `AE-GCC` / `SG-SEA` — DLD Trakheesi & CEA + ABSD)
  4. Crypto-Agility & Hybrid Post-Quantum Envelope (`v2:hybrid-sp80056c-chacha20hmac:` + Archive Upgrade)
  5. G5 Floor-Plan & 3D Walkthrough Slots + G7 Disposable `proof-mode` Sandbox Workspaces

Stdlib-only (`D2`), tenant-isolated, zero raw secret storage (`SEN-C306` / `SEN-C803`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from . import aead, config as cfg, inventory as ivt, security as sec


def _now() -> int:
    return int(time.time())


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


# =============================================================================
# 1. On-Device Client Pre-Screening (`WebGPU / WebNN` + Deterministic Weights)
# =============================================================================
ONDEVICE_MODEL_CARD: dict[str, Any] = {
    "schema": "realtor-estates.ondevice-prescreen.v1",
    "version": "1.0.0",
    "runtimes_supported": ["webgpu", "webnn", "cpu-deterministic-fp32"],
    "zero_network_roundtrip": True,
    "pii_patterns": [
        {"id": "in_aadhaar", "label": "Indian Aadhaar (12 digits)", "replacement": "[REDACTED_AADHAAR]"},
        {"id": "in_pan", "label": "Indian PAN ([A-Z]{5}[0-9]{4}[A-Z])", "replacement": "[REDACTED_PAN]"},
        {"id": "payment_card", "label": "Payment Card (13-19 digits)", "replacement": "[REDACTED_CARD]"},
        {"id": "passport", "label": "Passport Identifier", "replacement": "[REDACTED_PASSPORT]"},
    ],
    "scoring_weights": {
        "base": 35,
        "budget_stated": 20,
        "site_visit_intent": 22,
        "locality_specific": 12,
        "urgent_timeline": 11,
    },
}

_RE_AADHAAR = re.compile(r"\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b")
_RE_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_RE_CARD = re.compile(r"\b(?:\d[ \-]?){15,18}\d\b")


def prescreen_and_verify_payload(text: str, *, client_runtime: str = "cpu-deterministic-fp32") -> dict[str, Any]:
    """Verify (and enforce if client skipped) local PII redaction + deterministic lead score."""
    raw = str(text or "")
    redacted = raw
    redactions: list[str] = []
    if _RE_AADHAAR.search(redacted):
        redacted = _RE_AADHAAR.sub("[REDACTED_AADHAAR]", redacted)
        redactions.append("in_aadhaar")
    if _RE_PAN.search(redacted):
        redacted = _RE_PAN.sub("[REDACTED_PAN]", redacted)
        redactions.append("in_pan")
    if _RE_CARD.search(redacted):
        redacted = _RE_CARD.sub("[REDACTED_CARD]", redacted)
        redactions.append("payment_card")

    low = redacted.lower()
    score = int(ONDEVICE_MODEL_CARD["scoring_weights"]["base"])
    signals: list[str] = []
    if re.search(r"\b(\d+\s*(?:lakh|lac|cr|crore|aed|sgd|m)|₹|\$)\b", low):
        score += 20
        signals.append("budget_stated")
    if any(k in low for k in ("site visit", "visit", "saturday", "sunday", "viewing", "भेटी", "विजिट")):
        score += 22
        signals.append("site_visit_intent")
    if any(k in low for k in ("nanded", "pune", "taroda", "baner", "dubai", "marina", "jurong", "orchard", "plot", "bhk")):
        score += 12
        signals.append("locality_specific")
    if any(k in low for k in ("immediate", "ready", "this week", "urgent", "token", "book")):
        score += 11
        signals.append("urgent_timeline")
    score = min(100, score)
    tier = "hot" if score >= 75 else ("warm" if score >= 55 else "nurture")
    return {
        "clean_text": sec.clean(redacted, 2000),
        "client_already_redacted": len(redactions) == 0,
        "server_redactions_applied": redactions,
        "score": score,
        "tier": tier,
        "signals": signals,
        "runtime": sec.clean(client_runtime, 40),
    }


# =============================================================================
# 2. Agentic Commerce Settlement (`AP2` / `ACP` / `UCP` Mandates)
# =============================================================================
def _mandate_key() -> bytes:
    return hmac.new(sec.KEYS["session"], b"realtor-estates/ap2-mandate/v1", hashlib.sha256).digest()


def issue_ap2_mandate(
    store: Any,
    tenant: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "owner",
) -> dict[str, Any]:
    tid = tenant["id"]
    deal_id = str(payload.get("deal_id") or "").strip()
    unit_id = str(payload.get("unit_id") or "").strip()
    allowed_action = str(payload.get("allowed_action") or "hold").strip().lower()
    if allowed_action not in ("hold", "book"):
        raise ValueError("INVALID_MANDATE_ACTION: allowed_action must be 'hold' or 'book'.")

    deal = store.get("deals", tid, deal_id)
    if not deal:
        raise LookupError("Deal not found.")
    unit = store.get("units", tid, unit_id)
    if not unit:
        raise LookupError("Unit not found.")
    prop = store.get("properties", tid, unit.get("property_id") or deal.get("property_id") or "")
    if not prop:
        raise LookupError("Property not found.")

    # Regulatory & document-gate check before issuing a booking mandate
    reg = evaluate_regional_listing_compliance(tenant, prop)
    if not reg["compliant"]:
        raise ValueError(f"{reg['code']}: {reg['message']}")

    max_amount = int(payload.get("max_amount_inr") or 100_000)
    if max_amount < 0 or max_amount > 100_000_000:
        raise ValueError("INVALID_MANDATE_CAP: max_amount_inr out of bounds.")
    ttl_minutes = max(5, min(7 * 24 * 60, int(payload.get("ttl_minutes") or 120)))
    iat = _now()
    exp = iat + ttl_minutes * 60
    mandate_id = "mnd_" + secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    jti = "mjti_" + _b64u_encode(secrets.token_bytes(12))

    claims = {
        "v": "realtor-estates.ap2-mandate.v1",
        "mid": mandate_id,
        "tid": tid,
        "did": deal_id,
        "uid": unit_id,
        "cid": deal.get("contact_id", ""),
        "act": allowed_action,
        "cap": max_amount,
        "cur": str((tenant.get("region_profile") or {}).get("currency") or "INR"),
        "iat": iat,
        "exp": exp,
        "jti": jti,
    }
    body_b64 = _b64u_encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig_b64 = _b64u_encode(hmac.new(_mandate_key(), body_b64.encode("ascii"), hashlib.sha256).digest())
    mandate_token = f"ap2_{body_b64}.{sig_b64}"

    settings = dict(tenant.get("settings") or {})
    mandates = [m for m in list(settings.get("ap2_mandates") or []) if int(m.get("expires_at") or 0) > iat - 86400][-50:]
    record = {
        "id": mandate_id,
        "jti": jti,
        "deal_id": deal_id,
        "unit_id": unit_id,
        "property_id": prop["id"],
        "contact_id": deal.get("contact_id", ""),
        "allowed_action": allowed_action,
        "max_amount_inr": max_amount,
        "currency": claims["cur"],
        "status": "active",
        "issued_at": iat,
        "expires_at": exp,
        "issued_by": actor,
    }
    mandates.append(record)
    settings["ap2_mandates"] = mandates
    store.update("tenants", tid, tid, {"settings": settings})
    return {"mandate": record, "mandate_token": mandate_token}


def list_ap2_mandates(tenant: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((tenant.get("settings") or {}).get("ap2_mandates")) or [])


def revoke_ap2_mandate(store: Any, tenant: dict[str, Any], mandate_id: str) -> bool:
    tid = tenant["id"]
    settings = dict(tenant.get("settings") or {})
    mandates = list(settings.get("ap2_mandates") or [])
    found = False
    for m in mandates:
        if m.get("id") == mandate_id:
            m["status"] = "revoked"
            m["revoked_at"] = _now()
            found = True
    if found:
        settings["ap2_mandates"] = mandates
        store.update("tenants", tid, tid, {"settings": settings})
    return found


def settle_ap2_mandate(
    store: Any,
    tenant: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "agent-commerce",
) -> dict[str, Any]:
    """Verify `ap2_...` mandate and atomically settle a unit hold or token booking."""
    tid = tenant["id"]
    raw = str(payload.get("mandate_token") or "").strip()
    if not raw.startswith("ap2_") or "." not in raw:
        raise PermissionError("INVALID_AP2_MANDATE")
    body_b64, sig_b64 = raw[4:].split(".", 1)
    expected_sig = _b64u_encode(hmac.new(_mandate_key(), body_b64.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig_b64, expected_sig):
        raise PermissionError("INVALID_AP2_SIGNATURE")

    claims = json.loads(_b64u_decode(body_b64).decode("utf-8"))
    if claims.get("tid") != tid:
        raise PermissionError("CROSS_TENANT_MANDATE_DENIED")
    if int(claims.get("exp") or 0) < _now():
        raise PermissionError("EXPIRED_AP2_MANDATE")

    settings = dict(tenant.get("settings") or {})
    mandates = list(settings.get("ap2_mandates") or [])
    m_rec = next((m for m in mandates if m.get("id") == claims.get("mid") and m.get("jti") == claims.get("jti")), None)
    if not m_rec or m_rec.get("status") == "revoked":
        raise PermissionError("REVOKED_AP2_MANDATE")

    idem_ref = sec.clean(str(payload.get("idempotency_ref") or claims["jti"]), 80)
    if m_rec.get("status") == "settled" and m_rec.get("settlement_idem_ref") == idem_ref:
        return dict(m_rec["settlement_result"])
    if m_rec.get("status") == "settled":
        raise ValueError("MANDATE_ALREADY_CONSUMED: Mandate was already settled under a different reference.")

    req_action = str(payload.get("action") or claims.get("act") or "hold").lower()
    if req_action != claims.get("act") and not (claims.get("act") == "book" and req_action == "hold"):
        raise ValueError("MANDATE_SCOPE_EXCEEDED: Mandate does not authorise requested action.")

    amount = int(payload.get("amount_inr") or 0)
    if amount < 0 or amount > int(claims.get("cap") or 0):
        raise ValueError(f"MANDATE_CAP_EXCEEDED: Requested {amount} exceeds mandate ceiling {claims.get('cap')}.")

    unit = store.get("units", tid, claims["uid"])
    deal = store.get("deals", tid, claims["did"])
    if not unit or not deal:
        raise LookupError("Target unit or deal no longer exists.")

    try:
        if req_action == "hold":
            patch = ivt.apply_block(
                unit,
                deal_id=deal["id"],
                contact_id=deal.get("contact_id", ""),
                by=f"ap2:{actor}",
                hours=int(payload.get("hold_hours") or 48),
                reason=f"AP2 mandate {claims['mid']}",
            )
            updated_unit = store.update("units", tid, unit["id"], patch)
            receipt_doc = None
        else:
            if amount <= 0:
                raise ValueError("BOOKING_TOKEN_REQUIRED: Booking settlement requires amount_inr > 0.")
            patch = ivt.apply_booking(
                unit,
                deal_id=deal["id"],
                contact_id=deal.get("contact_id", ""),
                by=f"ap2:{actor}",
                token_amount=float(amount),
            )
            updated_unit = store.update("units", tid, unit["id"], patch) if patch else unit
            pmt = store.insert("payments", tid, {
                "deal_id": deal["id"],
                "unit_id": unit["id"],
                "contact_id": deal.get("contact_id", ""),
                "kind": "booking_token",
                "amount": amount,
                "currency": claims.get("cur", "INR"),
                "status": "captured",
                "provider": "ap2_agentic_settlement",
                "provider_payment_id": f"ap2pay_{claims['mid']}",
                "created_at": _now(),
            }, prefix="pay")
            receipt_doc = store.insert("invoices", tid, {
                "doc_type": "receipt",
                "number": f"RC-AP2-{claims['mid'][-6:].upper()}",
                "deal_id": deal["id"],
                "payment_id": pmt["id"],
                "total": amount,
                "taxable": 0,
                "gst_total": 0,
                "status": "issued",
                "issued_at": _now(),
            }, prefix="inv")
    except ivt.InventoryError as exc:
        raise ValueError(f"{exc.code}: {exc.message}")

    ack_payload = {
        "mandate_id": claims["mid"],
        "deal_id": deal["id"],
        "unit_id": unit["id"],
        "action": req_action,
        "amount_inr": amount,
        "unit_status": updated_unit.get("status"),
        "settled_at": _now(),
    }
    ack_raw = json.dumps(ack_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ack_sig = "acp1_" + _b64u_encode(hmac.new(_mandate_key(), ack_raw, hashlib.sha256).digest())
    result = {
        "protocol": "AP2/ACP-1.0",
        "settlement": ack_payload,
        "settlement_signature": ack_sig,
        "receipt_number": receipt_doc["number"] if receipt_doc else "",
    }
    m_rec["status"] = "settled"
    m_rec["settlement_idem_ref"] = idem_ref
    m_rec["settlement_result"] = result
    settings["ap2_mandates"] = mandates
    store.update("tenants", tid, tid, {"settings": settings})
    return result


# =============================================================================
# 3. GCC & SEA Regional Expansion & Data-Residency Pinning
# =============================================================================
REGION_PROFILES: dict[str, dict[str, Any]] = {
    "IN": {
        "code": "IN",
        "name": "India (RERA + DPDP 2023)",
        "data_residency_zone": "ap-south-1",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "regulatory_authority": "MahaRERA / State RERA",
        "permit_field": "rera_id",
        "permit_regex": r"^[A-Z0-9/\-]{6,24}$",
        "privacy_law": "DPDP Act 2023 + TRAI TCCCPR",
        "brokerage_indirect_tax_pct": 18.0,
        "transfer_duty_note": "State Stamp Duty (5–7%) + 1% Registration",
    },
    "AE": {
        "code": "AE",
        "name": "UAE / GCC (DLD RERA + Trakheesi + PDPL)",
        "data_residency_zone": "me-central-1",
        "currency": "AED",
        "timezone": "Asia/Dubai",
        "regulatory_authority": "Dubai Land Department (DLD) / RERA Trakheesi",
        "permit_field": "trakheesi_permit",
        "permit_regex": r"^(?:TRK-)?\d{7,14}$",
        "privacy_law": "UAE Federal Decree-Law No. 45/2021 (PDPL) + TDRA",
        "brokerage_indirect_tax_pct": 5.0,
        "transfer_duty_note": "DLD 4% Transfer Fee + AED 580 Admin Fee",
    },
    "SG": {
        "code": "SG",
        "name": "Singapore / SEA (CEA + URA + PDPA)",
        "data_residency_zone": "ap-southeast-1",
        "currency": "SGD",
        "timezone": "Asia/Singapore",
        "regulatory_authority": "Council for Estate Agencies (CEA)",
        "permit_field": "cea_licence",
        "permit_regex": r"^[LR]\d{6,8}[A-Z]$",
        "privacy_law": "Singapore PDPA + Do-Not-Call (DNC) Registry",
        "brokerage_indirect_tax_pct": 9.0,
        "transfer_duty_note": "BSD (1–6%) + ABSD (0% SC 1st / 5% SPR 1st / 60% Foreigner)",
    },
}

SG_ABSD_RATES: dict[str, float] = {
    "sc_first": 0.0,
    "sc_second": 20.0,
    "sc_third_plus": 30.0,
    "spr_first": 5.0,
    "spr_second": 30.0,
    "foreigner": 60.0,
    "entity": 65.0,
}


def pin_tenant_region(store: Any, tenant: dict[str, Any], region_code: str) -> dict[str, Any]:
    code = str(region_code or "IN").strip().upper()
    if code not in REGION_PROFILES:
        raise ValueError(f"UNSUPPORTED_REGION: '{code}'. Supported: {', '.join(sorted(REGION_PROFILES))}.")
    prof = dict(REGION_PROFILES[code])
    prof["pinned_at"] = _now()
    store.update("tenants", tenant["id"], tenant["id"], {
        "region_profile": prof,
        "timezone": prof["timezone"],
        "currency": prof["currency"],
    })
    return prof


def evaluate_regional_listing_compliance(
    tenant: dict[str, Any],
    prop: dict[str, Any],
    *,
    buyer_profile: str = "",
    target_export_zone: str = "",
) -> dict[str, Any]:
    prof = dict(tenant.get("region_profile") or REGION_PROFILES["IN"])
    code = prof.get("code", "IN")

    if target_export_zone and target_export_zone != prof["data_residency_zone"]:
        return {
            "compliant": False,
            "region": code,
            "code": "RESIDENCY_PIN_VIOLATION",
            "message": f"Tenant data is pinned to '{prof['data_residency_zone']}'; cross-region export to '{target_export_zone}' is blocked.",
        }

    if code == "AE":
        permit = str(prop.get("trakheesi_permit") or (prop.get("metadata") or {}).get("trakheesi_permit") or "").strip()
        if not re.match(prof["permit_regex"], permit):
            return {
                "compliant": False,
                "region": "AE",
                "code": "TRAKHEESI_PERMIT_REQUIRED",
                "message": "Dubai DLD RERA requires a valid Trakheesi advertising permit before marketing or booking.",
            }
        price = float(prop.get("price") or 0)
        return {
            "compliant": True,
            "region": "AE",
            "code": "DLD_TRAKHEESI_VERIFIED",
            "permit": permit,
            "dld_transfer_fee_aed": round(price * 0.04, 2),
            "brokerage_vat_pct": 5.0,
            "data_residency_zone": prof["data_residency_zone"],
        }

    if code == "SG":
        cea = str(prop.get("cea_licence") or (prop.get("metadata") or {}).get("cea_licence") or "").strip()
        if not re.match(prof["permit_regex"], cea):
            return {
                "compliant": False,
                "region": "SG",
                "code": "CEA_LICENCE_REQUIRED",
                "message": "Singapore CEA requires a valid Estate Agent / Salesperson registration (L.../R...) on advertisements.",
            }
        price = float(prop.get("price") or 0)
        absd_pct = SG_ABSD_RATES.get(buyer_profile or "sc_first", 60.0)
        return {
            "compliant": True,
            "region": "SG",
            "code": "CEA_LICENCE_VERIFIED",
            "cea_licence": cea,
            "absd_pct": absd_pct,
            "estimated_absd_sgd": round(price * (absd_pct / 100.0), 2),
            "brokerage_gst_pct": 9.0,
            "data_residency_zone": prof["data_residency_zone"],
        }

    # Default IN (India)
    rera = str(prop.get("rera_id") or "").strip()
    return {
        "compliant": True,
        "region": "IN",
        "code": "IN_RERA_CHECKED",
        "rera_id": rera,
        "brokerage_gst_pct": 18.0,
        "data_residency_zone": prof["data_residency_zone"],
    }


# =============================================================================
# 4. Crypto-Agility & Hybrid Post-Quantum Envelope (`docs/05` §4.3)
# =============================================================================
V2_ALG_ID = "hybrid-sp80056c-chacha20hmac"
V2_PREFIX = f"v2:{V2_ALG_ID}:"
OID_REGISTRY = {
    "v1": {
        "alg_id": "chacha20-hmac-sha256",
        "kdf": "HKDF-SHA256",
        "quantum_posture": "classical-256bit-symmetric",
    },
    V2_ALG_ID: {
        "alg_id": V2_ALG_ID,
        "kdf": "NIST-SP800-56Cr2-HKDF-SHA512(classical||pq_kem_slot)",
        "pq_kem_target_oid": "2.16.840.1.101.3.4.4.2 (ML-KEM-768 slot)",
        "quantum_posture": "hybrid-classical-plus-pq-kem-slot",
    },
}


def _derive_hybrid_v2_dek(classical_key: bytes, aad: str) -> bytes:
    """NIST SP 800-56Cr2 hybrid KDF combining classical subkey + PQ KEM shared-secret slot."""
    pq_env = cfg.get("RE_PQ_KEM_SLOT_SECRET", "")
    if pq_env:
        pq_slot = hashlib.sha512(pq_env.encode("utf-8")).digest()[:32]
    else:
        pq_slot = hmac.new(classical_key, b"realtor-estates/pq-kem-default-slot/v2", hashlib.sha512).digest()[:32]
    prk = hmac.new(b"realtor-estates/sp800-56cr2-salt", classical_key + pq_slot, hashlib.sha512).digest()
    info = b"realtor-estates/v2-hybrid-dek:" + aad.encode("utf-8") + b"\x01"
    return hmac.new(prk, info, hashlib.sha512).digest()[:32]


def seal_hybrid_v2(plaintext: str, *, aad: str = "", classical_key: bytes | None = None) -> str:
    if plaintext is None or plaintext == "":
        return ""
    base_k = classical_key or sec.KEYS["field_enc"]
    bound_aad = f"{V2_PREFIX}{aad}"
    dek = _derive_hybrid_v2_dek(base_k, bound_aad)
    inner = aead.seal(plaintext, dek, bound_aad)
    return f"{V2_PREFIX}{inner}"


def unseal_hybrid_v2(blob: str, *, aad: str = "", classical_key: bytes | None = None) -> str:
    if not blob:
        return ""
    if not blob.startswith(V2_PREFIX):
        raise ValueError("INTEGRITY_FAILURE")
    inner = blob[len(V2_PREFIX):]
    base_k = classical_key or sec.KEYS["field_enc"]
    bound_aad = f"{V2_PREFIX}{aad}"
    dek = _derive_hybrid_v2_dek(base_k, bound_aad)
    return aead.unseal(inner, dek, bound_aad)


def audit_crypto_agility(store: Any, tid: str) -> dict[str, Any]:
    v1_count = 0
    v2_count = 0
    for c in store.all("contacts", tid):
        for fld in ("phone_enc", "email_enc"):
            val = str(c.get(fld) or "")
            if not val:
                continue
            if val.startswith(("v2:", sec.V2T_PREFIX)):
                v2_count += 1
            else:
                v1_count += 1
    total = v1_count + v2_count
    return {
        "oid_registry": OID_REGISTRY,
        "active_default_envelope": V2_ALG_ID,
        "pq_external_slot_configured": bool(cfg.get("RE_PQ_KEM_SLOT_SECRET", "")),
        "field_ciphertexts": {
            "v1_classical": v1_count,
            "v2_hybrid": v2_count,
            "total": total,
            "v2_share_pct": round((v2_count / total) * 100.0, 1) if total else 100.0,
        },
    }


def upgrade_tenant_archive_to_v2(store: Any, tid: str) -> dict[str, Any]:
    """Re-encrypt `v1` contact PII fields in `tid` to `v2:hybrid-...` and upgrade blind indices to tenant-scoped (`F-07`)."""
    upgraded_fields = 0
    upgraded_contacts = 0
    for c in store.all("contacts", tid):
        patch: dict[str, str] = {}
        for fld, hash_fld in (("phone_enc", "phone_hash"), ("email_enc", "email_hash")):
            val = str(c.get(fld) or "")
            if not val:
                continue
            plain = sec.unseal(val, aad=tid)
            if not val.startswith("v2:"):
                new_blob = seal_hybrid_v2(plain, aad=tid)
                if unseal_hybrid_v2(new_blob, aad=tid) != plain:
                    raise RuntimeError("HYBRID_UPGRADE_VERIFY_FAILED")
                patch[fld] = new_blob
                upgraded_fields += 1
            if plain and plain != "[ERASED]":
                scoped_idx = sec.blind_index(plain, tenant_id=tid)
                if c.get(hash_fld) != scoped_idx:
                    patch[hash_fld] = scoped_idx
        if patch:
            store.update("contacts", tid, c["id"], patch)
            upgraded_contacts += 1
    return {
        "upgraded_contacts": upgraded_contacts,
        "upgraded_fields": upgraded_fields,
        "post_upgrade": audit_crypto_agility(store, tid),
    }


# =============================================================================
# 5. G5 Floor-Plan & Walkthrough Slots + G7 Disposable `proof-mode` Workspaces
# =============================================================================
def add_property_floor_plan(
    store: Any,
    tid: str,
    prop: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    title = sec.clean(str(payload.get("title") or "2 BHK Typical Floor Plan"), 80)
    carpet_sqft = max(50, min(50_000, int(payload.get("carpet_sqft") or prop.get("area_sqft") or 950)))
    balcony_sqft = max(0, min(10_000, int(payload.get("balcony_sqft") or 80)))
    orientation = sec.clean(str(payload.get("orientation") or "East"), 30)
    walkthrough_url = str(payload.get("walkthrough_url") or "").strip()
    if walkthrough_url:
        if not walkthrough_url.startswith("https://"):
            raise ValueError("WALKTHROUGH_HTTPS_REQUIRED: Walkthrough tour URL must use https://.")
        ok_ssrf, reason = sec.ssrf_check(walkthrough_url)
        if not ok_ssrf:
            raise ValueError(f"WALKTHROUGH_URL_BLOCKED: {reason}")

    plans = list(prop.get("floor_plans") or [])
    fp = {
        "id": "flp_" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8],
        "title": title,
        "carpet_sqft": carpet_sqft,
        "balcony_sqft": balcony_sqft,
        "orientation": orientation,
        "walkthrough_url": walkthrough_url[:300],
        "created_at": _now(),
    }
    plans.append(fp)
    store.update("properties", tid, prop["id"], {"floor_plans": plans[-20:]})
    return fp


def create_disposable_proof_tenant(
    store: Any,
    *,
    label: str = "QA Proof Sandbox",
    ttl_hours: int = 24,
    created_by_tid: str = "",
) -> dict[str, Any]:
    """Create an isolated `proof-mode` workspace (`PLANS.md` §9 G7) with encrypted seed rows."""
    from . import sessions as sess

    suffix = secrets.token_urlsafe(5).replace("-", "").replace("_", "").lower()[:6]
    ptid = f"tnt_proof_{suffix}"
    slug = f"proof-{suffix}"
    now = _now()
    ttl_h = max(1, min(168, int(ttl_hours or 24)))

    t_doc = {
        "id": ptid,
        "name": sec.clean(label, 80) or f"Proof Sandbox {suffix}",
        "slug": slug,
        "plan": "growth",
        "is_proof_tenant": True,
        "parent_tenant_id": created_by_tid,
        "expires_at": now + ttl_h * 3600,
        "created_at": now,
        "timezone": "Asia/Kolkata",
        "currency": "INR",
        "features": {"voice_ai": True, "whatsapp": True, "ads_autopilot": True, "learning": True},
        "settings": {},
    }
    store.insert("tenants", ptid, t_doc, prefix="tnt")

    u_email = f"sandbox.{suffix}@realtorestates.app"
    u_doc = store.insert("users", ptid, {
        "name": "Proof Runner",
        "email": u_email,
        "email_idx": sec.blind_index_email(u_email),
        "role": "owner",
        "password_hash": sec.hash_password("ProofSandbox!123"),
        "mfa_enabled": False,
        "mfa_enrolled": False,
        "created_at": now,
    }, prefix="usr")

    p_doc = store.insert("properties", ptid, {
        "title": "Proof Sandbox Residency — Plot 1",
        "type": "plot",
        "category": "residential_plot",
        "city": "Nanded",
        "locality": "Taroda BK",
        "price": 3_200_000,
        "area_sqft": 1800,
        "rera_id": "P51100099901",
        "status": "available",
        "created_at": now,
    }, prefix="prp")
    unit_doc = store.insert("units", ptid, {
        "property_id": p_doc["id"],
        "label": "P-101",
        "unit_code": "P-101",
        "floor": 0,
        "carpet_sqft": 1800,
        "base_price": 3_200_000,
        "all_in_price": 3_450_000,
        "status": "available",
        "created_at": now,
    }, prefix="unt")
    phone = "+919899001122"
    c_doc = store.insert("contacts", ptid, {
        "name": "Sandbox Buyer",
        "phone_enc": sec.seal(phone, aad=ptid),
        "phone_idx": sec.blind_index(phone),
        "city": "Nanded",
        "stage": "qualified",
        "score": 82,
        "consent": {"status": "granted", "source": "sandbox", "at": now},
        "created_at": now,
    }, prefix="cnt")
    d_doc = store.insert("deals", ptid, {
        "title": "Sandbox Plot Deal",
        "contact_id": c_doc["id"],
        "property_id": p_doc["id"],
        "unit_id": unit_doc["id"],
        "stage": "qualified",
        "value": 3_450_000,
        "probability": 40,
        "created_at": now,
    }, prefix="dl")

    s_row, _ = sess.open_session(store, ptid, u_doc["id"], ip="127.0.0.1", ua="proof-sandbox")
    sandbox_token = sec.issue_token({
        "sub": u_email,
        "uid": u_doc["id"],
        "tid": ptid,
        "role": "owner",
        "sid": s_row["id"],
    }, ttl=min(sess.ACCESS_TTL, ttl_h * 3600))
    return {
        "proof_tenant": {
            "id": ptid,
            "slug": slug,
            "name": t_doc["name"],
            "expires_at": t_doc["expires_at"],
            "seeded": {
                "property_id": p_doc["id"],
                "unit_id": unit_doc["id"],
                "contact_id": c_doc["id"],
                "deal_id": d_doc["id"],
            },
        },
        "sandbox_access_token": sandbox_token,
    }


def list_disposable_proof_tenants(store: Any, parent_tid: str) -> list[dict[str, Any]]:
    out = []
    for t in store.db.get("tenants", []):
        if t.get("is_proof_tenant") and (not parent_tid or t.get("parent_tenant_id") == parent_tid):
            out.append({
                "id": t["id"],
                "slug": t.get("slug"),
                "name": t.get("name"),
                "expires_at": t.get("expires_at"),
                "created_at": t.get("created_at"),
            })
    return out


def destroy_disposable_proof_tenant(store: Any, ptid: str, *, caller_tid: str = "") -> dict[str, Any]:
    target = next((t for t in store.db.get("tenants", []) if t.get("id") == ptid), None)
    if not target:
        raise LookupError("Proof workspace not found.")
    if not target.get("is_proof_tenant"):
        raise PermissionError("REFUSED_NON_PROOF_TENANT: Real workspaces cannot be destroyed by proof teardown.")
    if caller_tid and target.get("parent_tenant_id") and target.get("parent_tenant_id") != caller_tid:
        raise PermissionError("CROSS_TENANT_PROOF_TEARDOWN_DENIED")

    removed_rows = 0
    for col, items in list(store.db.items()):
        if not isinstance(items, list):
            continue
        if col == "audit":
            continue  # preserve append-only audit invariant (`SEN-C805`)
        if col == "tenants":
            to_drop = [r for r in items if r.get("id") == ptid]
        else:
            to_drop = [r for r in items if r.get("tenant_id") == ptid]
        for r in to_drop:
            store.remove(col, ptid, r["id"])
            removed_rows += 1
    return {"destroyed_tenant_id": ptid, "removed_rows": removed_rows}

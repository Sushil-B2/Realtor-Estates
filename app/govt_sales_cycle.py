"""
Realtor Estates — Complete Indian / Maharashtra Government, Revenue & Post-Sales
Lifecycle Engine (`v1.35.0`, `docs/46-GOVT-REVENUE-EREGISTRATION-LOAN-MUTATION-AND-TRUST-PACK.md`):

  Track G-1: Land & Revenue Vault (`7/12` Satbara, `8A` Khata, Urban Property Card,
             Collector NA Order, Mojni Demarcation, 30-Year Title Search) with
             30-day 7/12 freshness clock, Class-II Collector-permission gate &
             active Boja / Itar-Hakk blocker.
  Track G-2: Statutory Cost-Sheet Engine (Ready Reckoner / ASR floor, Maharashtra
             Stamp Duty zones, 1% Women Homebuyer Concession, 1% Registration capped
             at ₹30,000, GST category rules & §194-IA 1% Buyer TDS >= ₹50L).
  Track G-3: IGR `e-Registration` (Builder Office) & `iSarita` SRO Execution Desk
             (GRAS e-SBTR challan validator, Form 26QB TDS gate, Day-of-Registration
             witness pack & Index-II completion).
  Track G-4: Project Bank APF Registry, Buyer Home-Loan Pipeline & RERA
             Construction-Linked Payment (CLP) Milestones gated by Architect certs.
  Track G-5: Post-Registration `E-Ferfar` Talathi Mutation (15-day statutory notice
             countdown + Circle Officer certification), Namuna-8 / Municipal tax,
             MSEB Mahavitaran transfer & Possession Letter handover.
  Track G-6: Marketing Govt.-Verified Trust Badges & Ready-to-Share Buyer Cost Pack.

Stdlib-only (`D2`), tenant-isolated, zero raw secret storage (`SEN-C306` / `SEN-C803`).
"""
from __future__ import annotations

import datetime
import secrets
import time
from typing import Any

from . import security as sec


def _now() -> int:
    return int(time.time())


def _parse_ts(val: Any, default: int = 0) -> int:
    if isinstance(val, (int, float)) and val > 0:
        return int(val)
    s = str(val or "").strip()
    if not s:
        return default
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y"):
        try:
            dt = datetime.datetime.strptime(s[:19], fmt).replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return default


# =============================================================================
# Track G-1 — Land & Revenue Vault (`7/12`, `8A`, Property Card, NA, Mojni, Title)
# =============================================================================
SATBARA_FRESHNESS_DAYS = 30
OCCUPANT_CLASSES = ("class_1", "class_2", "govt_lessee", "urban_cts")


def upsert_revenue_vault(
    store: Any,
    tid: str,
    prop: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "legal",
) -> dict[str, Any]:
    existing = dict(prop.get("revenue_vault") or {})
    s_in = dict(existing.get("satbara_7_12") or {})
    if isinstance(payload.get("satbara_7_12"), dict):
        p7 = payload["satbara_7_12"]
        occ = str(p7.get("occupant_class") or s_in.get("occupant_class") or "class_1").strip().lower()
        if occ not in OCCUPANT_CLASSES:
            raise ValueError(f"INVALID_OCCUPANT_CLASS: '{occ}' must be one of {OCCUPANT_CLASSES}.")
        boja_raw = p7.get("itar_hakk_boja", s_in.get("itar_hakk_boja", []))
        boja_list = [sec.clean(str(x), 120) for x in (boja_raw if isinstance(boja_raw, list) else []) if str(x).strip()][:20]
        kab_raw = p7.get("kabjedar_names", s_in.get("kabjedar_names", []))
        kab_list = [sec.clean(str(x), 80) for x in (kab_raw if isinstance(kab_raw, list) else []) if str(x).strip()][:20]
        s_in = {
            "gat_or_survey_no": sec.clean(str(p7.get("gat_or_survey_no", s_in.get("gat_or_survey_no", prop.get("survey_number", "")))), 60),
            "hissa_no": sec.clean(str(p7.get("hissa_no", s_in.get("hissa_no", ""))), 30),
            "village": sec.clean(str(p7.get("village", s_in.get("village", prop.get("locality", "")))), 60),
            "taluka": sec.clean(str(p7.get("taluka", s_in.get("taluka", prop.get("city", "Nanded")))), 60),
            "district": sec.clean(str(p7.get("district", s_in.get("district", prop.get("city", "Nanded")))), 60),
            "occupant_class": occ,
            "collector_transfer_permission_ref": sec.clean(str(p7.get("collector_transfer_permission_ref", s_in.get("collector_transfer_permission_ref", ""))), 80),
            "nazrana_paid": bool(p7.get("nazrana_paid", s_in.get("nazrana_paid", False))),
            "kabjedar_names": kab_list,
            "itar_hakk_boja": boja_list,
            "bank_noc_obtained": bool(p7.get("bank_noc_obtained", s_in.get("bank_noc_obtained", False))),
            "issued_at": _parse_ts(p7.get("issued_at", s_in.get("issued_at")), _now()),
        }

    k8_in = dict(existing.get("khata_8a") or {})
    if isinstance(payload.get("khata_8a"), dict):
        pk = payload["khata_8a"]
        k8_in = {
            "khata_no": sec.clean(str(pk.get("khata_no", k8_in.get("khata_no", ""))), 40),
            "total_holding_sqm": float(pk.get("total_holding_sqm", k8_in.get("total_holding_sqm", 0)) or 0),
            "verified": bool(pk.get("verified", k8_in.get("verified", False))),
        }

    pc_in = dict(existing.get("property_card") or {})
    if isinstance(payload.get("property_card"), dict):
        pp = payload["property_card"]
        pc_in = {
            "cts_no": sec.clean(str(pp.get("cts_no", pc_in.get("cts_no", ""))), 40),
            "ward_office": sec.clean(str(pp.get("ward_office", pc_in.get("ward_office", ""))), 60),
            "verified": bool(pp.get("verified", pc_in.get("verified", False))),
        }

    na_in = dict(existing.get("na_order") or {})
    if isinstance(payload.get("na_order"), dict):
        pn = payload["na_order"]
        na_in = {
            "section": sec.clean(str(pn.get("section", na_in.get("section", "44"))), 20),
            "order_no": sec.clean(str(pn.get("order_no", na_in.get("order_no", ""))), 80),
            "order_date": sec.clean(str(pn.get("order_date", na_in.get("order_date", ""))), 20),
            "sanction_authority": sec.clean(str(pn.get("sanction_authority", na_in.get("sanction_authority", "Collector / Town Planning"))), 80),
            "verified": bool(pn.get("verified", na_in.get("verified", False))),
        }

    mj_in = dict(existing.get("mojni_demarcation") or {})
    if isinstance(payload.get("mojni_demarcation"), dict):
        pm = payload["mojni_demarcation"]
        mj_in = {
            "case_no": sec.clean(str(pm.get("case_no", mj_in.get("case_no", ""))), 60),
            "boundary_stones_fixed": bool(pm.get("boundary_stones_fixed", mj_in.get("boundary_stones_fixed", False))),
            "verified": bool(pm.get("verified", mj_in.get("verified", False))),
        }

    ts_in = dict(existing.get("title_search") or {})
    if isinstance(payload.get("title_search"), dict):
        pt = payload["title_search"]
        ts_in = {
            "years_searched": max(0, min(100, int(pt.get("years_searched", ts_in.get("years_searched", 30)) or 0))),
            "advocate_name": sec.clean(str(pt.get("advocate_name", ts_in.get("advocate_name", ""))), 80),
            "certificate_date": sec.clean(str(pt.get("certificate_date", ts_in.get("certificate_date", ""))), 20),
            "clear_marketable_title": bool(pt.get("clear_marketable_title", ts_in.get("clear_marketable_title", False))),
        }

    vault = {
        "satbara_7_12": s_in,
        "khata_8a": k8_in,
        "property_card": pc_in,
        "na_order": na_in,
        "mojni_demarcation": mj_in,
        "title_search": ts_in,
        "updated_at": _now(),
        "updated_by": actor,
    }
    upd = store.update("properties", tid, prop["id"], {
        "revenue_vault": vault,
        "na_order": bool(na_in.get("verified")),
    })
    return evaluate_revenue_clearance(upd or {**prop, "revenue_vault": vault})


def evaluate_revenue_clearance(prop: dict[str, Any], *, now_ts: int | None = None) -> dict[str, Any]:
    now = int(now_ts if now_ts is not None else _now())
    vault = dict(prop.get("revenue_vault") or {})
    s7 = dict(vault.get("satbara_7_12") or {})
    na = dict(vault.get("na_order") or {})
    ts = dict(vault.get("title_search") or {})
    pc = dict(vault.get("property_card") or {})

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    issued_at = int(s7.get("issued_at") or 0)
    age_days = round((now - issued_at) / 86400.0, 1) if issued_at > 0 else None
    fresh_7_12 = bool(issued_at > 0 and age_days is not None and age_days <= SATBARA_FRESHNESS_DAYS)

    if issued_at == 0 and not pc.get("verified"):
        warnings.append({
            "code": "REVENUE_RECORD_MISSING",
            "message": "Neither 7/12 extract nor Urban Property Card (Malmatta Patrak) is recorded yet.",
        })
    elif issued_at > 0 and not fresh_7_12:
        warnings.append({
            "code": "SATBARA_STALE_OVER_30D",
            "message": f"7/12 extract is {age_days} days old (> {SATBARA_FRESHNESS_DAYS}d); pull a fresh Mahabhulekh 7/12 before agreement/registration.",
        })

    occ = s7.get("occupant_class", "class_1")
    if occ in ("class_2", "govt_lessee"):
        has_perm = bool(s7.get("collector_transfer_permission_ref")) and bool(s7.get("nazrana_paid"))
        if not has_perm:
            blockers.append({
                "code": "CLASS_2_COLLECTOR_PERMISSION_REQUIRED",
                "message": "Land is Occupant Class II / Govt Lessee (Bhogwatdar Varg-2). Collector transfer permission + Nazrana payment is mandatory before booking.",
            })

    boja = list(s7.get("itar_hakk_boja") or [])
    if boja and not s7.get("bank_noc_obtained"):
        blockers.append({
            "code": "UNRELEASED_BOJA_ENCUMBRANCE",
            "message": f"Active encumbrance (Itar Hakk / Boja) recorded ({', '.join(boja[:3])}) without Bank Release / NOC.",
        })

    ptype = str(prop.get("type") or prop.get("category") or "").lower()
    land_use = str(prop.get("land_use") or "").lower()
    is_non_agri_plot = ("plot" in ptype or "land" in ptype) and "agricultural" not in land_use and "farm" not in land_use
    na_verified = bool(na.get("verified") or prop.get("na_order") is True)
    if is_non_agri_plot and not na_verified:
        warnings.append({
            "code": "NA_ORDER_UNVERIFIED",
            "message": "Non-Agricultural (NA) Collector conversion order is not yet marked verified.",
        })

    title_30yr_clear = bool(ts.get("clear_marketable_title") and int(ts.get("years_searched") or 0) >= 30)

    can_accept_token = not any(b["code"] == "CLASS_2_COLLECTOR_PERMISSION_REQUIRED" for b in blockers)
    can_execute_agreement = len(blockers) == 0 and (fresh_7_12 or bool(pc.get("verified")))

    return {
        "property_id": prop.get("id", ""),
        "can_accept_token": can_accept_token,
        "can_execute_agreement": can_execute_agreement,
        "fresh_7_12": fresh_7_12,
        "satbara_age_days": age_days,
        "na_verified": na_verified,
        "zero_unreleased_boja": not (boja and not s7.get("bank_noc_obtained")),
        "title_30yr_clear": title_30yr_clear,
        "blockers": blockers,
        "warnings": warnings,
        "vault": vault,
    }


# =============================================================================
# Track G-2 — Statutory Cost Sheet (ASR / Stamp Duty / Women 1% / Reg / 194-IA)
# =============================================================================
STAMP_DUTY_ZONES: dict[str, dict[str, Any]] = {
    "municipal_corp_metro": {"label": "Metro Municipal Corp (Pune / MMR / Nagpur)", "rate_pct": 7.0},
    "municipal_corp_tier2": {"label": "Tier-2 Municipal Corp (Nanded NWCMC / Sambhajinagar / Latur)", "rate_pct": 6.0},
    "municipal_council": {"label": "Nagar Parishad / Municipal Council", "rate_pct": 5.0},
    "gram_panchayat_rural": {"label": "Gram Panchayat / Rural Revenue Zone", "rate_pct": 5.0},
}

GST_CATEGORIES: dict[str, float] = {
    "na_plot_or_oc_ready": 0.0,
    "affordable_under_construction": 1.0,
    "standard_under_construction": 5.0,
    "commercial_under_construction": 12.0,
}

TDS_194IA_THRESHOLD_INR = 5_000_000  # ₹50,00,000


def _safe_nonneg_inr(raw: Any, field: str, *, max_inr: int = 500_000_000_000) -> int:
    import math

    if isinstance(raw, bool):
        raise ValueError(f"INVALID_{field.upper()}: boolean is not a valid monetary amount.")
    try:
        fval = float(raw or 0)
    except (TypeError, ValueError):
        raise ValueError(f"INVALID_{field.upper()}: '{raw}' is not a valid number.")
    if not math.isfinite(fval) or fval < 0 or fval > max_inr:
        raise ValueError(f"INVALID_{field.upper()}: amount must be finite and between 0 and ₹{max_inr:,}.")
    return int(round(fval))


def compute_statutory_cost_sheet(payload: dict[str, Any]) -> dict[str, Any]:
    agreement_val = _safe_nonneg_inr(payload.get("agreement_value_inr"), "agreement_value")
    if agreement_val <= 0:
        raise ValueError("INVALID_AGREEMENT_VALUE: agreement_value_inr must be > 0.")
    asr_val = _safe_nonneg_inr(payload.get("asr_ready_reckoner_inr"), "asr_ready_reckoner")
    assessable = max(agreement_val, asr_val)

    zone = str(payload.get("local_body_zone") or "municipal_corp_tier2").strip().lower()
    if zone not in STAMP_DUTY_ZONES:
        raise ValueError(f"INVALID_STAMP_ZONE: '{zone}'.")
    base_sd_pct = float(STAMP_DUTY_ZONES[zone]["rate_pct"])

    gst_cat = str(payload.get("property_gst_category") or "na_plot_or_oc_ready").strip().lower()
    if gst_cat not in GST_CATEGORIES:
        raise ValueError(f"INVALID_GST_CATEGORY: '{gst_cat}'.")
    gst_pct = GST_CATEGORIES[gst_cat]

    buyer_scheme = str(payload.get("buyer_gender_scheme") or "standard").strip().lower()
    is_commercial = gst_cat == "commercial_under_construction" or bool(payload.get("is_commercial", False))
    women_concession_pct = 1.0 if (buyer_scheme == "sole_or_joint_female" and not is_commercial) else 0.0
    effective_sd_pct = max(1.0, base_sd_pct - women_concession_pct)

    stamp_duty_inr = int(round(assessable * (effective_sd_pct / 100.0)))
    # Maharashtra Registration Act schedule: 1% of assessable value, capped at ₹30,000 above ₹30L
    reg_fee_inr = min(30_000, int(round(assessable * 0.01))) if assessable > 3_000_000 else int(round(assessable * 0.01))
    gst_inr = int(round(agreement_val * (gst_pct / 100.0)))
    other_charges_inr = _safe_nonneg_inr(payload.get("other_charges_inr"), "other_charges")

    tds_applicable = assessable >= TDS_194IA_THRESHOLD_INR
    tds_194ia_inr = int(round(agreement_val * 0.01)) if tds_applicable else 0
    net_payable_to_seller_inr = agreement_val - tds_194ia_inr

    total_govt_taxes_inr = stamp_duty_inr + reg_fee_inr + gst_inr
    all_in_outflow_inr = agreement_val + total_govt_taxes_inr + other_charges_inr

    advisories: list[str] = []
    if asr_val > agreement_val:
        advisories.append(
            f"Ready Reckoner / ASR valuation (₹{asr_val:,}) exceeds Agreement Value; Stamp Duty & Registration are calculated on ASR (Income Tax §43CA / §56(2)(x) check advised)."
        )
    if women_concession_pct > 0:
        advisories.append(
            "1% Maharashtra Women Homebuyer Stamp Duty Concession applied — subsequent resale within 15 years is restricted to female buyers to avoid differential duty clawback."
        )
    if tds_applicable:
        advisories.append(
            f"Consideration >= ₹50,00,000: Buyer must deduct 1% TDS (₹{tds_194ia_inr:,}) u/s 194-IA via Form 26QB within 30 days and issue Form 16B to Seller."
        )

    return {
        "agreement_value_inr": agreement_val,
        "asr_ready_reckoner_inr": asr_val,
        "assessable_market_value_inr": assessable,
        "asr_higher_than_agreement": asr_val > agreement_val,
        "local_body_zone": zone,
        "zone_label": STAMP_DUTY_ZONES[zone]["label"],
        "base_stamp_duty_pct": base_sd_pct,
        "women_concession_pct": women_concession_pct,
        "effective_stamp_duty_pct": effective_sd_pct,
        "stamp_duty_inr": stamp_duty_inr,
        "registration_fee_inr": reg_fee_inr,
        "property_gst_category": gst_cat,
        "gst_pct": gst_pct,
        "gst_inr": gst_inr,
        "other_charges_inr": other_charges_inr,
        "total_govt_taxes_inr": total_govt_taxes_inr,
        "all_in_buyer_outflow_inr": all_in_outflow_inr,
        "tds_194ia": {
            "applicable": tds_applicable,
            "rate_pct": 1.0 if tds_applicable else 0.0,
            "tds_amount_inr": tds_194ia_inr,
            "net_payable_to_seller_inr": net_payable_to_seller_inr,
            "forms_required": ["Form 26QB", "Form 16B"] if tds_applicable else [],
        },
        "advisories": advisories,
    }


# =============================================================================
# Track G-3 — IGR `e-Registration` & `iSarita` SRO Execution Desk
# =============================================================================
REGISTRATION_MODES = ("e_registration_builder_office", "isarita_sro_physical")


def upsert_registration_desk(
    store: Any,
    tid: str,
    deal: dict[str, Any],
    prop: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "crm-legal",
) -> dict[str, Any]:
    # Enforce Track G-1 revenue clearance before scheduling registration
    rev = evaluate_revenue_clearance(prop)
    if not rev["can_execute_agreement"]:
        codes = ", ".join(b["code"] for b in rev["blockers"]) or "STALE_OR_MISSING_REVENUE_RECORD"
        raise ValueError(f"REVENUE_CLEARANCE_BLOCKED: Clear land/title blockers ({codes}) before registration.")

    cur = dict(deal.get("govt_registration") or {})
    mode = str(payload.get("mode", cur.get("mode", "isarita_sro_physical"))).strip().lower()
    if mode not in REGISTRATION_MODES:
        raise ValueError(f"INVALID_REGISTRATION_MODE: '{mode}'.")

    val_inr = max(1, int(deal.get("value") or prop.get("price") or 1))
    zone = str(payload.get("local_body_zone") or cur.get("local_body_zone") or "municipal_corp_tier2")
    scheme = str(payload.get("buyer_gender_scheme") or cur.get("buyer_gender_scheme") or "standard")
    cost = compute_statutory_cost_sheet({
        "agreement_value_inr": val_inr,
        "asr_ready_reckoner_inr": int(payload.get("asr_ready_reckoner_inr", cur.get("asr_ready_reckoner_inr", 0)) or 0),
        "local_body_zone": zone,
        "buyer_gender_scheme": scheme,
        "property_gst_category": str(payload.get("property_gst_category") or cur.get("property_gst_category") or "na_plot_or_oc_ready"),
    })

    gras_no = sec.clean(str(payload.get("gras_challan_no", cur.get("gras_challan_no", ""))), 40).upper()
    sd_paid = int(payload.get("stamp_duty_paid_inr", cur.get("stamp_duty_paid_inr", 0)) or 0)
    rf_paid = int(payload.get("reg_fee_paid_inr", cur.get("reg_fee_paid_inr", 0)) or 0)
    form_26qb = sec.clean(str(payload.get("form_26qb_ack_no", cur.get("form_26qb_ack_no", ""))), 40).upper()

    gras_verified = bool(len(gras_no) >= 8 and sd_paid >= cost["stamp_duty_inr"] and rf_paid >= cost["registration_fee_inr"])
    tds_ok = (not cost["tds_194ia"]["applicable"]) or bool(len(form_26qb) >= 6)

    w1 = sec.clean(str(payload.get("witness_1_name", cur.get("witness_1_name", ""))), 80)
    w2 = sec.clean(str(payload.get("witness_2_name", cur.get("witness_2_name", ""))), 80)
    aadhaar_otp_ready = bool(payload.get("aadhaar_biometric_ready", cur.get("aadhaar_biometric_ready", False)))
    sro_office = sec.clean(str(payload.get("sro_office_name", cur.get("sro_office_name", "SRO Nanded-1"))), 80)
    token_no = sec.clean(str(payload.get("isarita_token_no", cur.get("isarita_token_no", ""))), 40)
    appt_at = _parse_ts(payload.get("appointment_at", cur.get("appointment_at")), 0)

    ready_gates: list[str] = []
    if not gras_verified:
        ready_gates.append("GRAS_CHALLAN_OR_DUTY_SHORTFALL")
    if not tds_ok:
        ready_gates.append("TDS_26QB_ACK_REQUIRED")
    if mode == "isarita_sro_physical" and (not w1 or not w2):
        ready_gates.append("TWO_WITNESSES_REQUIRED")
    if mode == "e_registration_builder_office" and not aadhaar_otp_ready:
        ready_gates.append("AADHAAR_BIOMETRIC_DEVICE_REQUIRED")

    checklist = [
        "Original PAN Card & Aadhaar Card (Buyer + Seller POA)",
        f"GRAS e-SBTR / e-Challan ({gras_no or 'Pending'}) — Stamp Duty ₹{cost['stamp_duty_inr']:,} + Reg ₹{cost['registration_fee_inr']:,}",
        "Fresh 7/12 (<30 days) + NA Sanction Copy + Sanctioned Layout Map",
    ]
    if cost["tds_194ia"]["applicable"]:
        checklist.append(f"Income Tax Form 26QB Challan Ack ({form_26qb or 'Mandatory >= ₹50L'}) — 1% TDS ₹{cost['tds_194ia']['tds_amount_inr']:,}")
    if mode == "isarita_sro_physical":
        checklist.append(f"2 Witnesses with original Aadhaar/PAN ({w1 or 'Witness 1'}, {w2 or 'Witness 2'}) at {sro_office}")
    else:
        checklist.append("UIDAI Biometric Thumb Scanner + Linked Mobile for Aadhaar OTP e-Sign")

    reg_doc = {
        "mode": mode,
        "sro_office_name": sro_office,
        "isarita_token_no": token_no,
        "appointment_at": appt_at,
        "gras_challan_no": gras_no,
        "stamp_duty_paid_inr": sd_paid,
        "reg_fee_paid_inr": rf_paid,
        "gras_verified": gras_verified,
        "form_26qb_ack_no": form_26qb,
        "tds_194ia_verified": tds_ok,
        "witness_1_name": w1,
        "witness_2_name": w2,
        "aadhaar_biometric_ready": aadhaar_otp_ready,
        "ready_for_execution": len(ready_gates) == 0,
        "pending_gates": ready_gates,
        "cost_sheet": cost,
        "day_of_registration_checklist": checklist,
        "status": cur.get("status") if cur.get("status") == "registered" else ("ready" if len(ready_gates) == 0 else "preparing"),
        "registered_doc_no": cur.get("registered_doc_no", ""),
        "index_ii_ref": cur.get("index_ii_ref", ""),
        "registered_at": cur.get("registered_at", 0),
        "updated_at": _now(),
        "updated_by": actor,
    }

    # Sync deal.documents for `app/vertical.py` stage gates when GRAS & appointment are set
    docs = list(deal.get("documents") or [])
    have_names = {d.get("name") for d in docs if isinstance(d, dict)}
    if gras_verified and "Stamp duty receipt" not in have_names:
        docs.append({"name": "Stamp duty receipt", "status": "verified", "ref": gras_no, "at": _now()})
    if (appt_at > 0 or mode == "e_registration_builder_office") and "Registration appointment" not in have_names:
        docs.append({"name": "Registration appointment", "status": "verified", "ref": token_no or mode, "at": _now()})

    store.update("deals", tid, deal["id"], {"govt_registration": reg_doc, "documents": docs})
    return reg_doc


def complete_registration_index2(
    store: Any,
    tid: str,
    deal: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor: str = "crm-legal",
) -> dict[str, Any]:
    reg = dict(deal.get("govt_registration") or {})
    if not reg.get("ready_for_execution"):
        raise ValueError(
            f"REGISTRATION_NOT_READY: Pending statutory gates ({', '.join(reg.get('pending_gates') or ['SETUP_REQUIRED'])})."
        )
    doc_no = sec.clean(str(payload.get("registered_doc_no") or ""), 60).upper()
    idx2 = sec.clean(str(payload.get("index_ii_ref") or ""), 60).upper()
    if len(doc_no) < 4 or len(idx2) < 4:
        raise ValueError("INDEX_II_REQUIRED: Both registered_doc_no and index_ii_ref are required.")

    now = _now()
    reg.update({
        "status": "registered",
        "registered_doc_no": doc_no,
        "index_ii_ref": idx2,
        "registered_at": now,
        "updated_by": actor,
    })
    docs = list(deal.get("documents") or [])
    if not any(isinstance(d, dict) and d.get("name") == "Registered sale deed" for d in docs):
        docs.append({"name": "Registered sale deed", "status": "verified", "ref": doc_no, "at": now})

    # Auto-seed Track G-5 E-Ferfar & Handover tracker if not yet present
    handover = dict(deal.get("post_reg_handover") or {})
    if not handover:
        handover = {
            "ferfar_entry_no": "",
            "ferfar_applied_at": 0,
            "ferfar_notice_clears_at": 0,
            "circle_officer_certified": False,
            "updated_7_12_or_property_card_issued": False,
            "namuna8_municipal_tax_transferred": False,
            "mseb_meter_transferred": False,
            "snag_list_cleared": False,
            "possession_letter_issued": False,
            "completion_pct": 0,
        }
    store.update("deals", tid, deal["id"], {
        "govt_registration": reg,
        "documents": docs,
        "post_reg_handover": handover,
    })
    return {"govt_registration": reg, "post_reg_handover": handover}


# =============================================================================
# Track G-4 — Bank APF, Buyer Home-Loan Pipeline & RERA CLP Demand Engine
# =============================================================================
LOAN_STAGES = (
    "login",
    "sanctioned",
    "legal_technical_clear",
    "tripartite_noc_signed",
    "disbursed",
)


def add_project_apf(store: Any, tid: str, prop: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    bank = sec.clean(str(payload.get("bank_name") or ""), 60)
    apf_code = sec.clean(str(payload.get("apf_code") or ""), 40).upper()
    if not bank or not apf_code:
        raise ValueError("APF_FIELDS_REQUIRED: bank_name and apf_code are required.")
    entry = {
        "id": "apf_" + secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:8],
        "bank_name": bank,
        "apf_code": apf_code,
        "branch": sec.clean(str(payload.get("branch") or ""), 60),
        "rm_name": sec.clean(str(payload.get("rm_name") or ""), 60),
        "max_ltv_pct": max(50, min(90, int(payload.get("max_ltv_pct") or 80))),
        "added_at": _now(),
    }
    apf_list = [a for a in list(prop.get("bank_apf") or []) if a.get("apf_code") != apf_code]
    apf_list.append(entry)
    store.update("properties", tid, prop["id"], {"bank_apf": apf_list[-20:]})
    return entry


def upsert_home_loan(store: Any, tid: str, deal: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    cur = dict(deal.get("home_loan") or {})
    mode = str(payload.get("funding_mode", cur.get("funding_mode", "bank_loan"))).strip().lower()
    if mode not in ("self_funded", "bank_loan"):
        raise ValueError("INVALID_FUNDING_MODE: must be 'self_funded' or 'bank_loan'.")
    stage = str(payload.get("stage", cur.get("stage", "login"))).strip().lower()
    if stage not in LOAN_STAGES:
        raise ValueError(f"INVALID_LOAN_STAGE: '{stage}' not in {LOAN_STAGES}.")
    loan = {
        "funding_mode": mode,
        "bank_name": sec.clean(str(payload.get("bank_name", cur.get("bank_name", "SBI"))), 60),
        "apf_code": sec.clean(str(payload.get("apf_code", cur.get("apf_code", ""))), 40),
        "loan_file_no": sec.clean(str(payload.get("loan_file_no", cur.get("loan_file_no", ""))), 40),
        "stage": stage,
        "sanctioned_amount_inr": max(0, int(payload.get("sanctioned_amount_inr", cur.get("sanctioned_amount_inr", 0)) or 0)),
        "disbursed_amount_inr": max(0, int(payload.get("disbursed_amount_inr", cur.get("disbursed_amount_inr", 0)) or 0)),
        "disbursement_utr": sec.clean(str(payload.get("disbursement_utr", cur.get("disbursement_utr", ""))), 50),
        "updated_at": _now(),
    }
    store.update("deals", tid, deal["id"], {"home_loan": loan})
    return loan


def init_rera_clp_schedule(
    store: Any,
    tid: str,
    deal: dict[str, Any],
    *,
    schedule_kind: str = "rera_building_7stage",
) -> list[dict[str, Any]]:
    total_val = max(1, int(deal.get("value") or 1))
    if schedule_kind == "na_plot_3stage":
        template = [
            ("token", "Booking Token (Earnest)", 20, False),
            ("agreement", "Agreement to Sell Execution", 30, False),
            ("registration", "Sale Deed Registration & Mojni Handover", 50, False),
        ]
    else:
        template = [
            ("token", "Booking Token (Max 10% pre-agreement per RERA §13)", 10, False),
            ("agreement", "Post-Registered Agreement to Sell", 20, False),
            ("plinth", "Plinth Level Completion", 15, True),
            ("slabs", "RCC Superstructure Slabs Completion", 25, True),
            ("brickwork", "Internal Walls, Plaster & Flooring", 15, True),
            ("mep_lifts", "External Plumbing, Lifts & Terrace", 10, True),
            ("oc_possession", "Occupancy Certificate (OC) & Possession", 5, True),
        ]
    milestones: list[dict[str, Any]] = []
    allocated = 0
    for idx, (code, label, pct, needs_cert) in enumerate(template):
        amt = (total_val - allocated) if idx == len(template) - 1 else int(round(total_val * (pct / 100.0)))
        allocated += amt
        milestones.append({
            "code": code,
            "label": label,
            "share_pct": pct,
            "amount_inr": amt,
            "requires_architect_cert": needs_cert,
            "architect_cert_ref": "",
            "status": "pending",
            "demanded_at": 0,
        })
    store.update("deals", tid, deal["id"], {"clp_schedule": milestones})
    return milestones


def raise_clp_milestone_demand(
    store: Any,
    tid: str,
    deal: dict[str, Any],
    milestone_code: str,
    *,
    architect_cert_ref: str = "",
) -> dict[str, Any]:
    schedule = list(deal.get("clp_schedule") or [])
    if not schedule:
        schedule = init_rera_clp_schedule(store, tid, deal)
    target = next((m for m in schedule if m.get("code") == milestone_code), None)
    if not target:
        raise LookupError(f"Milestone '{milestone_code}' not found on deal.")
    cert = sec.clean(architect_cert_ref, 80)
    if target.get("requires_architect_cert") and not cert:
        raise ValueError(
            f"ARCHITECT_CERTIFICATE_REQUIRED: RERA construction milestone '{target['label']}' requires an Architect / Structural Engineer completion certificate before raising buyer demand."
        )
    target["architect_cert_ref"] = cert
    target["status"] = "demanded"
    target["demanded_at"] = _now()
    store.update("deals", tid, deal["id"], {"clp_schedule": schedule})
    return target


# =============================================================================
# Track G-5 — Post-Registration `E-Ferfar` Mutation & Possession Handover
# =============================================================================
FERFAR_NOTICE_DAYS = 15


def upsert_post_reg_handover(
    store: Any,
    tid: str,
    deal: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    cur = dict(deal.get("post_reg_handover") or {})
    ferfar_no = sec.clean(str(payload.get("ferfar_entry_no", cur.get("ferfar_entry_no", ""))), 40).upper()
    applied_at = _parse_ts(payload.get("ferfar_applied_at", cur.get("ferfar_applied_at")), _now() if ferfar_no else 0)
    notice_clears_at = (applied_at + FERFAR_NOTICE_DAYS * 86400) if applied_at > 0 else 0

    co_cert = bool(payload.get("circle_officer_certified", cur.get("circle_officer_certified", False)))
    updated_712 = bool(payload.get("updated_7_12_or_property_card_issued", cur.get("updated_7_12_or_property_card_issued", False)))
    namuna8 = bool(payload.get("namuna8_municipal_tax_transferred", cur.get("namuna8_municipal_tax_transferred", False)))
    mseb = bool(payload.get("mseb_meter_transferred", cur.get("mseb_meter_transferred", False)))
    snag = bool(payload.get("snag_list_cleared", cur.get("snag_list_cleared", False)))
    possession = bool(payload.get("possession_letter_issued", cur.get("possession_letter_issued", False)))

    steps_bool = [
        bool(ferfar_no),
        co_cert,
        updated_712,
        namuna8,
        mseb,
        possession,
    ]
    pct = int(round((sum(1 for b in steps_bool if b) / len(steps_bool)) * 100))

    handover = {
        "ferfar_entry_no": ferfar_no,
        "ferfar_applied_at": applied_at,
        "ferfar_notice_clears_at": notice_clears_at,
        "circle_officer_certified": co_cert,
        "updated_7_12_or_property_card_issued": updated_712,
        "namuna8_municipal_tax_transferred": namuna8,
        "mseb_meter_transferred": mseb,
        "snag_list_cleared": snag,
        "possession_letter_issued": possession,
        "completion_pct": pct,
        "handover_completed_at": _now() if possession and updated_712 else int(cur.get("handover_completed_at") or 0),
        "updated_at": _now(),
    }
    store.update("deals", tid, deal["id"], {"post_reg_handover": handover})
    return handover


# =============================================================================
# Track G-6 — Marketing Govt.-Verified Trust Badges & Buyer Cost Pack
# =============================================================================
def compute_marketing_trust_pack(
    prop: dict[str, Any],
    *,
    local_body_zone: str = "municipal_corp_tier2",
) -> dict[str, Any]:
    rev = evaluate_revenue_clearance(prop)
    badges: list[dict[str, str]] = []

    attrs = prop.get("attributes") if isinstance(prop.get("attributes"), dict) else {}
    rera = str(prop.get("rera_id") or prop.get("rera") or attrs.get("rera_id") or "").strip()
    if rera:
        badges.append({"code": "MAHARERA_VERIFIED", "label": f"MahaRERA Reg: {rera}"})
    if rev["na_verified"]:
        badges.append({"code": "NA_COLLECTOR_VERIFIED", "label": "Collector NA Order Verified"})
    if rev["fresh_7_12"] and rev["zero_unreleased_boja"] and rev["can_accept_token"]:
        badges.append({"code": "FRESH_7_12_ZERO_BOJA", "label": "Fresh 7/12 (<30d) · Zero Boja"})
    if rev["title_30yr_clear"]:
        badges.append({"code": "TITLE_30YR_CLEAR", "label": "30-Year Clear Title Search"})
    apf_banks = [a.get("bank_name") for a in (prop.get("bank_apf") or []) if a.get("bank_name")]
    if apf_banks:
        badges.append({"code": "BANK_APF_APPROVED", "label": f"Loan Approved: {', '.join(apf_banks[:3])}"})

    price = max(100_000, int(prop.get("price") or 2_500_000))
    ptype = str(prop.get("type") or prop.get("category") or "").lower()
    gst_cat = "na_plot_or_oc_ready" if ("plot" in ptype or prop.get("possession") == "Ready to Move") else "standard_under_construction"
    sample_cost = compute_statutory_cost_sheet({
        "agreement_value_inr": price,
        "local_body_zone": local_body_zone,
        "property_gst_category": gst_cat,
    })
    women_saving_inr = int(round(price * 0.01)) if gst_cat != "commercial_under_construction" else 0

    badge_line = " · ".join(b["label"] for b in badges) or "Documents shared prior to site visit"
    whatsapp_pitch = (
        f"*{prop.get('title', 'Project')}* ({prop.get('locality', '')}, {prop.get('city', '')})\n"
        f"Verified Clearances: {badge_line}\n"
        f"Base Consideration: ₹{price:,} | Govt Stamp+Reg+GST: ₹{sample_cost['total_govt_taxes_inr']:,} | All-In: ₹{sample_cost['all_in_buyer_outflow_inr']:,}"
        + (f" (Women Buyer Stamp Concession saves ₹{women_saving_inr:,})" if women_saving_inr else "")
    )

    return {
        "property_id": prop.get("id", ""),
        "verified_badges": badges,
        "badge_count": len(badges),
        "revenue_clearance": {
            "can_accept_token": rev["can_accept_token"],
            "can_execute_agreement": rev["can_execute_agreement"],
        },
        "sample_cost_sheet": sample_cost,
        "whatsapp_brochure_copy": whatsapp_pitch,
    }

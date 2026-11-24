"""
Portal ingestion — the front door of the business.

Every Indian brokerage's leads arrive from 99acres, MagicBricks, Housing.com,
NoBroker, Meta lead ads and Google Ads, each with its own field names, its own
budget format ("30 Lakh", "1.2 Cr", "₹30,00,000") and its own idea of a phone
number. docs/18 §4 lists two consequences that decide conversion:

* **Source must survive to booking.** Cost-per-closure is computed per portal,
  so a lead that is de-duplicated must *keep the new source on the record*
  rather than being dropped — otherwise the portal that actually produced the
  buyer gets no credit and the budget moves to the wrong channel.
* **Speed-to-lead is the metric.** Research puts a 5-minute first response at
  multiples of a later one, so every ingested lead carries an SLA clock from the
  second it lands, and the clock is measured against the first *outbound*
  message, not against a stage change.

This module is pure: adapters, normalisation, de-duplication merge, assignment
and SLA arithmetic. The routes in app/api.py do the storage and the audit.
"""
from __future__ import annotations

import re
import time
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 1. Adapters
# ---------------------------------------------------------------------------
# Aliases are matched case-insensitively after flattening, so a portal can
# change its casing without breaking us. Order matters: first hit wins.
ADAPTERS: dict[str, dict[str, Any]] = {
    "99acres": {
        "label": "99acres",
        "flat": False,
        "name": ("sender_name", "name", "Name", "contact_name"),
        "phone": ("sender_phone", "mobile", "phone", "Mobile", "contact_number"),
        "email": ("sender_email", "email", "Email"),
        "city": ("city", "City"),
        "locality": ("locality", "locality_name", "Locality"),
        "budget": ("budget", "Budget", "budget_range"),
        "property_type": ("property_type", "propertyType", "type"),
        "requirement": ("message", "requirement", "Comment", "comments"),
        "campaign": ("campaign", "project", "Project"),
        "external_id": ("lead_id", "id", "enquiry_id"),
    },
    "magicbricks": {
        "label": "MagicBricks",
        "flat": False,
        "name": ("contactName", "name", "FirstName", "senderName"),
        "phone": ("mobile", "Mobile", "phone", "contactNumber"),
        "email": ("email", "Email"),
        "city": ("city", "City"),
        "locality": ("locality", "Locality"),
        "budget": ("budget", "budget_range", "Budget"),
        "property_type": ("propertyType", "property_type"),
        "requirement": ("requirement", "message", "remarks"),
        "campaign": ("projectName", "campaign"),
        "external_id": ("enquiryId", "leadId", "id"),
    },
    "housing": {
        "label": "Housing.com",
        "flat": False,
        "name": ("fullName", "name", "userName"),
        "phone": ("phone", "mobile", "contactNo"),
        "email": ("email", "userEmail"),
        "city": ("city", "cityName"),
        "locality": ("locality", "localityName"),
        "budget": ("budget", "budgetMax", "budget_max"),
        "property_type": ("propertyType", "type"),
        "requirement": ("message", "requirement", "comments"),
        "campaign": ("project", "campaign"),
        "external_id": ("leadId", "id"),
    },
    "nobroker": {
        "label": "NoBroker",
        "flat": False,
        "name": ("name", "fullName", "user_name"),
        "phone": ("phone", "mobile", "contact"),
        "email": ("email",),
        "city": ("city",),
        "locality": ("locality", "area"),
        "budget": ("budget", "rent", "price"),
        "property_type": ("property_type", "type"),
        "requirement": ("requirement", "message"),
        "campaign": ("campaign",),
        "external_id": ("id", "lead_id"),
    },
    # Meta lead ads: {"field_data": [{"name": "full_name", "values": ["..."]}, …]}
    "meta": {
        "label": "Meta Lead Ads",
        "flat": True,
        "name": ("full_name", "name", "first_name"),
        "phone": ("phone_number", "phone", "mobile"),
        "email": ("email",),
        "city": ("city",),
        "locality": ("locality", "area"),
        "budget": ("budget", "budget_range"),
        "property_type": ("property_type", "type"),
        "requirement": ("requirement", "message", "custom_question"),
        "campaign": ("campaign_name", "ad_name"),
        "external_id": ("leadgen_id", "id"),
    },
    # Google Ads lead forms: {"user_column_data": [{"column_name": "Full Name", "string_value": "..."}]}
    "google": {
        "label": "Google Ads",
        "flat": True,
        "name": ("Full Name", "full_name", "name"),
        "phone": ("Phone Number", "phone_number", "phone"),
        "email": ("Email", "user_email"),
        "city": ("City", "city"),
        "locality": ("Locality", "locality"),
        "budget": ("Budget", "budget"),
        "property_type": ("Property Type", "property_type"),
        "requirement": ("Requirement", "requirement", "message"),
        "campaign": ("Campaign", "campaign_name"),
        "external_id": ("Lead ID", "lead_id", "id"),
    },
    "website": {
        "label": "Website",
        "flat": False,
        "name": ("name", "full_name"),
        "phone": ("phone", "mobile"),
        "email": ("email",),
        "city": ("city",),
        "locality": ("locality",),
        "budget": ("budget", "budget_max"),
        "property_type": ("property_type", "type"),
        "requirement": ("requirement", "message"),
        "campaign": ("campaign", "utm_campaign"),
        "external_id": ("id", "form_id"),
    },
}


def sources() -> list[dict]:
    return [{"slug": slug, "label": a["label"]} for slug, a in ADAPTERS.items()]


class IngestError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# 2. Normalisation
# ---------------------------------------------------------------------------
def _flatten(payload: Any, out: dict | None = None) -> dict:
    """Reduce portal payloads to a case-insensitive name → scalar map.

    Meta and Google both wrap answers in arrays of {name/column_name, values/
    string_value}; everything else is flat JSON or form-encoded. One pass that
    handles all three keeps the adapters declarative.
    """
    out = {} if out is None else out
    if isinstance(payload, dict):
        # Meta
        fields = payload.get("field_data")
        if isinstance(fields, list):
            for entry in fields:
                if not isinstance(entry, dict):
                    continue
                key = entry.get("name")
                vals = entry.get("values")
                if key and isinstance(vals, list) and vals:
                    _put(out, key, vals[0])
                elif key:
                    _put(out, key, entry.get("value"))
        # Google
        columns = payload.get("user_column_data")
        if isinstance(columns, list):
            for entry in columns:
                if not isinstance(entry, dict):
                    continue
                key = entry.get("column_name")
                if key:
                    _put(out, key, entry.get("string_value", entry.get("value")))
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                _flatten(value, out)
            else:
                _put(out, key, value)
    elif isinstance(payload, list):
        for item in payload:
            _flatten(item, out)
    return out


def _put(out: dict, key: Any, value: Any) -> None:
    if not isinstance(key, str) or value in (None, "", []):
        return
    if key.lower() in out:            # first non-empty wins
        return
    out[key.lower()] = value if isinstance(value, (int, float, bool)) else str(value).strip()


def pick(flat: dict, aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = flat.get(alias.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def normalize_phone(raw: str) -> str:
    """Indian mobile numbers, whatever the portal thinks a number looks like."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]
    if len(digits) != 10 or digits[0] not in "6789":
        return ""                     # not an Indian mobile: refuse rather than guess
    return "+91" + digits


LAKH = 100_000
CRORE = 10_000_000
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(cr|crore|lakh|lac|lk|l|k|thousand)?", re.I)


def _single_amount(text: str) -> int | None:
    # Commas are group separators in every Indian portal budget ("₹30,00,000"),
    # never decimals — strip them before matching or "30,00,000" reads as 30.
    text = text.replace(",", "")
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        value = float(re.sub(r"[^\d.]", "", m.group(1)) or 0)
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    if unit in ("cr", "crore"):
        value *= CRORE
    elif unit in ("lakh", "lac", "lk", "l"):
        value *= LAKH
    elif unit in ("k", "thousand"):
        value *= 1_000
    return int(round(value)) or None


def parse_budget(raw: Any) -> dict:
    """`"30 Lakh - 50 Lakh"`, `"₹1.2 Cr"`, `"30,00,000"`, `"Under 30L"` → range.

    Portals are wildly inconsistent here and a wrong budget silently corrupts
    matching, so the parse is conservative: what cannot be read is returned as
    empty with a warning, never as a guess.
    """
    text = str(raw or "").strip()
    if not text:
        return {"min": None, "max": None, "text": ""}
    cleaned = re.sub(r"(rs\.?|inr|₹|/-)", " ", text, flags=re.I).strip()
    parts = re.split(r"\s*(?:-|–|to)\s*", cleaned)
    if len(parts) >= 2:
        low = _single_amount(parts[0])
        high = _single_amount(parts[1])
        # "1.2 - 1.5 Cr": the unit may live only on the second number
        if low and high and low < high / 100:
            unit = re.search(r"(cr|crore|lakh|lac|lk)", parts[1], re.I)
            if unit:
                low *= CRORE if unit.group(1).lower().startswith("cr") else LAKH
        if low and high and low > high:
            low, high = high, low
        return {"min": low, "max": high, "text": text}
    amount = _single_amount(cleaned)
    if amount is None:
        return {"min": None, "max": None, "text": text}
    if re.search(r"(under|below|upto|up to|max)", cleaned, re.I):
        return {"min": None, "max": amount, "text": text}
    if re.search(r"(above|over|min)", cleaned, re.I):
        return {"min": amount, "max": None, "text": text}
    return {"min": None, "max": amount, "text": text}


def normalize(source: str, payload: dict) -> dict:
    """Portal payload → the canonical lead this app stores, plus warnings."""
    adapter = ADAPTERS.get(source)
    if not adapter:
        raise IngestError("UNKNOWN_SOURCE", f"Unknown source '{source}'.", {"sources": list(ADAPTERS)})

    flat = _flatten(payload)
    name = pick(flat, adapter["name"])[:80]
    phone = normalize_phone(pick(flat, adapter["phone"]))
    email = pick(flat, adapter["email"])[:254]
    budget = parse_budget(pick(flat, adapter["budget"]))

    warnings = []
    if not name:
        raise IngestError("MISSING_NAME", "The payload has no name for the lead.")
    if not phone:
        raise IngestError(
            "MISSING_PHONE",
            "No valid Indian mobile number in the payload — the lead cannot be contacted or deduplicated.",
            {"raw_phone": pick(flat, adapter["phone"])[:24]},
        )
    if email and "@" not in email:
        warnings.append("email looked invalid and was dropped")
        email = ""

    lead = {
        "name": name,
        "phone": phone,
        "email": email,
        "city": pick(flat, adapter["city"])[:60],
        "locality": pick(flat, adapter["locality"])[:80],
        "requirement": pick(flat, adapter["requirement"])[:200],
        "property_type": pick(flat, adapter["property_type"])[:40],
        "budget_min": budget["min"],
        "budget_max": budget["max"],
        "budget_text": budget["text"][:40],
        "campaign": pick(flat, adapter["campaign"])[:80],
        "external_id": pick(flat, adapter["external_id"])[:80],
        "source": adapter["label"],
        "source_slug": source,
    }
    if not lead["city"] and lead["locality"]:
        lead["city"] = ""             # locality without city stays in the requirement
    if budget["text"] and budget["min"] is None and budget["max"] is None:
        warnings.append(f"budget text '{budget['text'][:30]}' could not be read")
    return {"lead": lead, "warnings": warnings, "flat_keys": sorted(flat.keys())}


# ---------------------------------------------------------------------------
# 3. De-duplication and source attribution
# ---------------------------------------------------------------------------
FILL_BLANKS = ("email", "city", "locality", "requirement", "property_type", "budget_min", "budget_max", "campaign")


def merge_duplicate(existing: dict, lead: dict, at: int | None = None) -> dict:
    """What a repeat enquiry from the same phone should change on the record.

    Fill blanks only — an agent's edited requirement is never overwritten by a
    portal retry — but *always* append the source, because the second portal is
    what produced this enquiry and the cost-per-closure maths depends on it.
    """
    at = int(at if at is not None else time.time())
    patch: dict[str, Any] = {}
    for field in FILL_BLANKS:
        if not existing.get(field) and lead.get(field):
            patch[field] = lead[field]

    history = list(existing.get("sources") or [])
    key = (lead.get("source_slug"), lead.get("external_id") or "", lead.get("campaign") or "")
    known = {(h.get("slug"), h.get("external_id") or "", h.get("campaign") or "") for h in history if isinstance(h, dict)}
    if key not in known:
        history.append(
            {
                "slug": lead.get("source_slug", ""),
                "label": lead.get("source", ""),
                "campaign": lead.get("campaign", ""),
                "external_id": lead.get("external_id", ""),
                "at": at,
            }
        )
    patch["sources"] = history[-20:]

    tags = list(existing.get("tags") or [])
    if "repeat-enquiry" not in tags:
        tags.append("repeat-enquiry")
    patch["tags"] = tags
    return patch


def primary_source(contact: dict) -> str:
    """First touch wins for attribution, latest touch is kept in `sources`."""
    history = [h for h in (contact.get("sources") or []) if isinstance(h, dict)]
    if history:
        return history[0].get("label") or contact.get("source", "")
    return contact.get("source", "")


# ---------------------------------------------------------------------------
# 4. Assignment
# ---------------------------------------------------------------------------
ASSIGNABLE_ROLES = ("agent", "manager")


def assign_owner(users: Iterable[dict], load: dict[str, int], exclude: str = "") -> str:
    """Round-robin by current load: fewest open deals, then fewest contacts.

    Deliberately *load*-based rather than a rotating pointer: an agent who was
    ill for a week should not still be catching up from a pointer position that
    forgot about it. Ties break on id so two servers assign identically.
    """
    candidates = [
        u for u in users
        if u.get("active", True) and u.get("role") in ASSIGNABLE_ROLES and u.get("id") != exclude
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda u: (load.get(u["id"], 0), u["id"]))
    return candidates[0]["id"]


# ---------------------------------------------------------------------------
# 5. Speed-to-lead
# ---------------------------------------------------------------------------
# Seconds allowed before the first outbound message. Five minutes for paid
# portals is the number the research keeps landing on; a walk-in is standing in
# front of you, so it gets one minute.
SLA_POLICY: dict[str, int] = {
    "99acres": 300,
    "magicbricks": 300,
    "housing": 300,
    "nobroker": 300,
    "meta": 300,
    "google": 300,
    "website": 600,
    "walkin": 60,
    "referral": 900,
    "csv": 3600,
}
DEFAULT_SLA = 600


def sla_window(source_slug: str, tenant: dict | None = None) -> int:
    override = ((tenant or {}).get("settings") or {}).get("sla_seconds")
    if isinstance(override, int) and 30 <= override <= 86400:
        return override
    return SLA_POLICY.get(source_slug, DEFAULT_SLA)


def sla_state(contact: dict, at: int | None = None) -> dict:
    """Where this lead stands against its clock. `open`, `breached`, `met`, `met_late`.

    Measured from the contact's own `sla.due_at` when present so a policy change
    does not retroactively rewrite history.
    """
    at = int(at if at is not None else time.time())
    window = (contact.get("sla") or {}).get("seconds") or sla_window(
        (contact.get("sources") or [{}])[-1].get("slug", "") if contact.get("sources") else "", None
    )
    due = (contact.get("sla") or {}).get("due_at") or int(contact.get("created_at") or at) + int(window)
    responded = contact.get("first_response_at")
    if responded:
        gap = int(responded) - int(contact.get("created_at") or responded)
        return {
            "status": "met" if gap <= int(window) else "met_late",
            "due_at": due,
            "seconds": window,
            "responded_in_sec": gap,
            "remaining_sec": 0,
        }
    remaining = due - at
    return {
        "status": "open" if remaining > 0 else "breached",
        "due_at": due,
        "seconds": window,
        "responded_in_sec": None,
        "remaining_sec": max(0, remaining),
        "overdue_sec": max(0, -remaining),
    }


def sla_board(contacts: Iterable[dict], at: int | None = None, limit: int = 40) -> dict:
    """Newest-first list of leads still inside their window, plus breaches."""
    at = int(at if at is not None else time.time())
    rows = []
    for c in contacts:
        state = sla_state(c, at)
        rows.append(
            {
                "contact_id": c.get("id"),
                "name": c.get("name"),
                "source": primary_source(c),
                "owner_id": c.get("owner_id", ""),
                "created_at": c.get("created_at"),
                **state,
            }
        )
    breached = [r for r in rows if r["status"] == "breached"]
    open_ = [r for r in rows if r["status"] == "open"]
    met = [r for r in rows if r["status"].startswith("met")]
    breached.sort(key=lambda r: -(r.get("overdue_sec") or 0))
    open_.sort(key=lambda r: r.get("remaining_sec") or 0)
    met_gaps = [r["responded_in_sec"] for r in met if r.get("responded_in_sec") is not None]
    return {
        "breached": breached[:limit],
        "open": open_[:limit],
        "counts": {
            "breached": len(breached),
            "open": len(open_),
            "met": len(met),
            "met_within_window": len([g for g in met_gaps if g <= DEFAULT_SLA]),
        },
        "avg_response_sec": int(sum(met_gaps) / len(met_gaps)) if met_gaps else None,
        "window_sec": DEFAULT_SLA,
    }

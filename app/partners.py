"""
Channel partners — the broker network that actually sells Indian real estate.

docs/18 §4 #3 and §6 #5: Sell.Do, LeadSquared and Zoho have no CP module, and
Grovia's headline feature is exactly this. What a CP module has to get right is
not the login screen; it is three rules:

1. **Registration is a claim with a clock.** A partner registers a buyer; the
   claim is protected for a window (90 days by default) during which no other
   partner can register the same number. Without a clock, whoever shouts last
   owns the buyer, and every payout becomes an argument.
2. **The house's own leads are not claimable.** A buyer who already came in
   through the brokerage's portal feed is *already* the house's lead; letting a
   partner "register" them would convert a 0% sale into a 2% commission on
   demand. That is refused, and an owner can override deliberately.
3. **Commission is shown from the deal, not from a spreadsheet.** A partner sees
   per-deal rows with the basis for each amount — recorded in the deal's
   commission ledger, or expected from their rate — and a payout state that only
   a manager can move.

The functions here are pure: the routes do storage, audit and scoping.
"""
from __future__ import annotations

import time
from typing import Iterable

PROTECTION_DAYS = 90
DEFAULT_CP_PCT = 2.0          # share of gross commission, not of the sale value
MIN_CP_PCT, MAX_CP_PCT = 0.0, 15.0

STATUSES = ("pending", "approved", "rejected", "expired")
OPEN_REGISTRATION = ("pending", "approved")


class PartnerError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def clamp_pct(value: float | None) -> float:
    if value is None:
        return DEFAULT_CP_PCT
    try:
        pct = float(value)
    except (TypeError, ValueError):
        raise PartnerError("INVALID_COMMISSION", "Commission percentage must be a number.")
    if not (MIN_CP_PCT <= pct <= MAX_CP_PCT):
        raise PartnerError("INVALID_COMMISSION", f"Partner share must be between {MIN_CP_PCT} and {MAX_CP_PCT}% of gross.")
    return round(pct, 2)


def protection_until(at: int, days: int | None = None) -> int:
    days = PROTECTION_DAYS if days is None else max(1, min(365, int(days)))
    return int(at) + days * 86400


def registration_state(reg: dict, at: int | None = None) -> str:
    """`approved` registrations expire on their own; nobody has to run a job.

    Derived rather than stored so a missed cron cannot silently extend a claim.
    """
    at = int(at if at is not None else time.time())
    status = reg.get("status", "pending")
    if status == "approved" and reg.get("protection_until") and int(reg["protection_until"]) <= at:
        return "expired"
    return status


def decide(
    *,
    cp_id: str,
    contact: dict | None,
    registration: dict | None,
    other_registration: dict | None,
    at: int | None = None,
) -> dict:
    """May this partner claim this buyer? Returns {outcome, reason, protected_until}.

    `outcome` is one of:
      * `new`            — no such contact yet; create and register
      * `duplicate`      — this partner already registered this buyer (idempotent)
      * `conflict`       — another partner holds a live claim
      * `existing_lead`  — the buyer is already the house's own lead
    """
    at = int(at if at is not None else time.time())

    if registration and registration.get("cp_user_id") == cp_id:
        return {
            "outcome": "duplicate",
            "reason": "You already registered this buyer.",
            "protected_until": registration.get("protection_until"),
            "registration_id": registration.get("id"),
        }

    if other_registration and other_registration.get("cp_user_id") != cp_id:
        state = registration_state(other_registration, at)
        if state in OPEN_REGISTRATION:
            return {
                "outcome": "conflict",
                "reason": "This buyer is registered by another partner.",
                "protected_until": other_registration.get("protection_until"),
                "protection_days_left": max(0, (int(other_registration.get("protection_until") or at) - at) // 86400),
            }
        if state == "expired":
            return {"outcome": "new", "reason": "The previous claim expired; you may register this buyer.",
                    "released_from": other_registration.get("id")}

    if contact and not other_registration and not registration:
        created = int(contact.get("created_at") or at)
        return {
            "outcome": "existing_lead",
            "reason": "This number is already in the brokerage's own pipeline and cannot be claimed.",
            "contact_age_days": max(0, (at - created) // 86400),
        }

    return {"outcome": "new", "reason": "Claim created.", "protected_until": protection_until(at)}


def credit_visit_count(visits: Iterable[dict], contact_ids: set[str]) -> int:
    """Trips the partner is credited with: the buyer actually turned up."""
    return len([v for v in visits if v.get("contact_id") in contact_ids and v.get("status") in ("visited", "revisit")])


def _money(paise: int) -> float:
    return round(paise / 100, 2)


def earnings_rows(deals: Iterable[dict], approved_contacts: set[str], cp_id: str, cp_pct: float) -> list[dict]:
    """One row per deal whose buyer is credited to this partner.

    `basis` tells the partner where the number came from: `recorded` means the
    house already computed a commission ledger with this partner as beneficiary
    (authoritative); `expected` is an estimate at their rate, clearly labelled so
    it is never mistaken for a promise.
    """
    rows = []
    for deal in deals:
        if deal.get("contact_id") not in approved_contacts:
            continue
        commission = deal.get("commission") or {}
        value = float(deal.get("value") or 0)
        rate = float(commission.get("rate_pct") or 2.0)
        gross_paise = int(round(value * rate / 100 * 100))
        recorded = commission.get("channel_partner_id") == cp_id and commission.get("channel_partner_paise") is not None
        amount_paise = int(commission.get("channel_partner_paise") or 0) if recorded else int(round(gross_paise * cp_pct / 100))
        rows.append(
            {
                "deal_id": deal.get("id"),
                "title": deal.get("title"),
                "stage": deal.get("stage"),
                "deal_value": value,
                "rate_pct": rate,
                "cp_pct": commission.get("channel_partner_pct") if recorded else cp_pct,
                "gross_commission": _money(gross_paise),
                "amount": _money(amount_paise),
                "basis": "recorded" if recorded else "expected",
                "payout_status": (deal.get("cp_payout") or {}).get("status", "not_due" if deal.get("stage") != "won" else "pending"),
                "paid_on": (deal.get("cp_payout") or {}).get("paid_at"),
                "reference": (deal.get("cp_payout") or {}).get("reference", ""),
                "due": deal.get("stage") == "won",
            }
        )
    rows.sort(key=lambda r: (not r["due"], -(r["deal_value"] or 0)))
    return rows


def earnings_summary(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    due = [r for r in rows if r["due"]]
    return {
        "deals": len(rows),
        "due_deals": len(due),
        "due_total": round(sum(r["amount"] for r in due), 2),
        "paid_total": round(sum(r["amount"] for r in rows if r["payout_status"] == "paid"), 2),
        "pending_total": round(sum(r["amount"] for r in rows if r["payout_status"] == "pending"), 2),
        "pipeline_estimate": round(sum(r["amount"] for r in rows if not r["due"]), 2),
    }


# ==========================================================================
# What a partner may see of the inventory
# ==========================================================================
# A partner who cannot see the stock cannot sell it — that was gap G4: a partner
# could register a buyer and watch a clock, but had no idea what the brokerage
# actually had. Opening the catalogue, though, is where a CP portal leaks. Three
# rules, and every one of them is a test:
#
# 1. **Only stock that can be sold.** A property appears if it has at least one
#    free unit (or, for a property without a unit list, is itself `available`).
#    A `hold` is someone else's 48 hours, `booked` is someone else's buyer, `sold`
#    is history — showing them to a partner produces calls that end in an apology.
# 2. **The seller is not for sale.** No seller name or phone, no internal notes,
#    no negotiation margin, and no buyer identities: units are shown by label and
#    state, never with the `booking` block that names the deal and the buyer.
# 3. **No commission rate is invented.** A partner's share is a percentage of the
#    brokerage's *gross commission*, which is fixed per mandate. The portal shows
#    their rate and, if the ledger has a comparable deal, an estimate derived from
#    it — explicitly labelled, and absent when there is nothing to derive it from.
WITHHELD = (
    {"what": "the seller's name and phone", "why": "the brokerage holds the mandate; enquiries come through us"},
    {"what": "the seller's floor and negotiation margin", "why": "a partner who knows the floor sells the floor"},
    {"what": "buyer identities, bookings and token amounts", "why": "someone else's buyer is not a lead you may call"},
    {"what": "internal notes, lead scores and call recordings", "why": "they are about our prospects, not your stock"},
    {"what": "other partners' claims and their buyers", "why": "the claim register is confidential by design"},
)

# a property whose unit list has never been filled in is sold as a whole
WHOLE_PROPERTY_FREE = ("available",)
UNIT_FREE = ("available",)
MARKETING_PRIVATE_ATTRS = ("floor", "margin", "cost", "internal", "notes", "seller",
                           "seller_phone", "seller_name", "commission", "payout")


def availability(units: Iterable[dict]) -> dict:
    """Counts by state — the only thing a partner learns about other buyers."""
    counts = {"total": 0, "available": 0, "blocked": 0, "booked": 0, "sold": 0}
    for u in units:
        counts["total"] += 1
        state = str(u.get("status") or "")
        if state in counts:
            counts[state] += 1
    return counts


def display_status(prop: dict, avail: dict | None = None) -> str:
    """`available` · `limited` · `sold_out` · `off_market` — what a buyer would ask."""
    avail = avail or {"total": 0, "available": 0}
    if avail["total"]:
        free, total = avail["available"], avail["total"]
        if free == 0:
            return "sold_out"
        return "available" if free == total else "limited"
    return "available" if str(prop.get("status")) in WHOLE_PROPERTY_FREE else "off_market"


def is_listable(prop: dict, avail: dict | None = None, include_unavailable: bool = False) -> bool:
    state = display_status(prop, avail)
    if state == "available" or state == "limited":
        return True
    return bool(include_unavailable)


def marketing_ready(prop: dict) -> dict:
    """May this be advertised at all?

    Maharashtra's RERA (s.3) makes advertising an unregistered project an offence,
    and the penalty falls on whoever published it — which, on a partner's Instagram,
    is the partner. So we tell them, before they publish, what is missing.
    """
    missing = []
    if not str(prop.get("rera") or "").strip():
        missing.append("RERA registration number")
    if not (prop.get("price") or (prop.get("pricing") or {}).get("amount")):
        missing.append("price")
    if not str(prop.get("title") or "").strip():
        missing.append("title")
    if not str(prop.get("city") or "").strip():
        missing.append("city")
    return {"ready": not missing, "missing": missing}


def public_attributes(prop: dict) -> dict:
    """`attributes` with anything that looks like house business removed.

    Denylist, not allowlist: the taxonomy (docs/22) adds fields per category and a
    partner should get the new ones without a code change — but a key named
    `seller_phone` is never marketing copy.
    """
    out = {}
    for k, v in (prop.get("attributes") or {}).items():
        low = str(k).lower()
        if any(bad in low for bad in MARKETING_PRIVATE_ATTRS):
            continue
        out[k] = v
    return out


def implied_gross_rate(deals: Iterable[dict]) -> tuple[float | None, str]:
    """The brokerage's commission rate, inferred from its own ledger.

    Returns (rate, basis). No comparable deal → (None, "no comparable") and the
    portal says so rather than inventing 2% and being wrong on a ₹2 crore plot.
    """
    pairs = []
    for d in deals:
        gross = (d.get("commission") or {}).get("gross")
        value = d.get("value")
        if gross and value and float(value) > 0:
            pairs.append((float(gross), float(value)))
    if not pairs:
        return None, "no comparable recorded deal"
    # one deal is an anecdote; the median of what we have is at least not cherry-picked
    rates = sorted(g / v for g, v in pairs)
    mid = rates[len(rates) // 2] if len(rates) % 2 else (rates[len(rates) // 2 - 1] + rates[len(rates) // 2]) / 2
    return round(mid * 100, 2), f"median of {len(pairs)} recorded deal(s), gross commission ÷ sale value"


def inventory_row(prop: dict, units: Iterable[dict], cp_pct: float,
                  gross_rate: float | None, rate_basis: str) -> dict:
    """One catalogue row: enough to sell, nothing that is ours."""
    units = list(units)
    avail = availability(units)
    price = prop.get("price") or (prop.get("pricing") or {}).get("amount") or 0
    estimate = None
    if price and gross_rate is not None:
        estimate = round(float(price) * gross_rate / 100.0 * cp_pct / 100.0, 2)
    row = {
        "id": prop["id"],
        "title": prop.get("title"),
        "city": prop.get("city"),
        "locality": prop.get("locality"),
        "category": prop.get("category"),
        "type": prop.get("type"),
        "deal_kind": prop.get("deal_kind"),
        "rera": prop.get("rera") or "",
        "status": display_status(prop, avail),
        "availability": avail,
        "price": price,
        "pricing": prop.get("pricing") or {},
        "size_sqft": prop.get("size_sqft"),
        "area": prop.get("area") or {},
        "coordinates": prop.get("coordinates") or [],
        "attributes": public_attributes(prop),
        "documents": [d for d in (prop.get("docs") or []) if isinstance(d, str)],
        "marketing": marketing_ready(prop),
        "commission": {
            "your_share_pct": cp_pct,
            "estimate": estimate,
            "basis": (f"your {cp_pct}% of a gross commission at ~{gross_rate}% of the sale "
                      f"value ({rate_basis})") if estimate is not None else
                     (f"your {cp_pct}% of the gross commission fixed per deal "
                      f"({rate_basis}) — the brokerage confirms it when you register a buyer"),
        },
    }
    if prop.get("status") and prop["status"] != "available":
        row["house_note"] = ("Not currently sellable." if avail["available"] == 0
                             else "Some units are held or booked; only free units can be sold.")
    return row


def apply_filters(rows: Iterable[dict], city: str = "", ptype: str = "", deal_kind: str = "",
                  min_price: int = 0, max_price: int = 0, q: str = "") -> list[dict]:
    """Plain filters with plain semantics: an unknown value matches nothing."""
    out = []
    q = (q or "").strip().lower()
    city_l, type_l = (city or "").strip().lower(), (ptype or "").strip().lower()
    for r in rows:
        if city_l and str(r.get("city") or "").lower() != city_l:
            continue
        if type_l and str(r.get("type") or "").lower() != type_l:
            continue
        if deal_kind and str(r.get("deal_kind") or "").lower() != deal_kind.lower():
            continue
        price = float(r.get("price") or 0)
        if min_price and price < min_price:
            continue
        if max_price and price > max_price:
            continue
        if q and q not in " ".join(str(r.get(k) or "") for k in ("title", "city", "locality", "type")).lower():
            continue
        out.append(r)
    return out

"""
Real-estate vertical rules — the part that makes this a *real-estate* CRM.

Three things live here, and they are deliberately pure functions over plain
dicts so they can be tested without a server, a store or a tenant:

1. **The deal stage machine.** Which stages exist, what a deal must have in hand
   before it may enter one, and who may move it backwards.
2. **Property matching.** Ranking properties against a buyer's stated
   requirement, with a *reason per point* — an unexplained score is a black box
   and agents will not trust it.
3. **Site visits.** The lifecycle states a visit moves through, and the
   double-booking rule.

Why a stage *gate* and not just a stage label: the market research (docs/18)
found that "stops at booking" is the standard weakness of the incumbent Indian
CRMs. A pipeline that lets a deal reach `registration` with no registered deed
attached is a progress bar, not a record. Here, entering a stage requires the
documents that stage implies — and an owner can override, loudly, in the audit
log.

Money: commission is computed as a *split*, because the trade is a brokerage:
gross → channel-partner share (if any) → agent share / brokerage share. Every
number is rounded once, at the end, in paise, so the parts always sum to the
whole.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 1. Deal stages
# ---------------------------------------------------------------------------
# Forward order. `lost` is terminal and sits outside the sequence.
STAGES: tuple[str, ...] = (
    "new",
    "contacted",
    "qualified",
    "site_visit",
    "negotiation",
    "token",
    "agreement",
    "registration",
    "won",
)
LOST = "lost"
STAGE_ORDER = {name: i for i, name in enumerate(STAGES)}
PROBABILITY = {
    "new": 10,
    "contacted": 20,
    "qualified": 45,
    "site_visit": 65,
    "negotiation": 80,
    "token": 88,
    "agreement": 94,
    "registration": 98,
    "won": 100,
    "lost": 0,
}

# Documents a deal must hold (status generally "received" or "verified") before it
# may enter the stage on the left. Names are deliberately human — they appear in
# the UI's checklist and in the 409 that blocks the move.
STAGE_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "token": ("KYC (Aadhaar/PAN)", "Booking form"),
    "agreement": ("Sale agreement draft",),
    "registration": ("Stamp duty receipt", "Registration appointment"),
    "won": ("Registered sale deed",),
}

# Stages that require physical evidence the client showed up.
STAGE_REQUIRES_VISIT = {"site_visit": "scheduled", "negotiation": "visited"}

DOC_OK = {"received", "verified"}


class StageError(ValueError):
    """A stage move the business rules refuse. Carries machine-readable detail."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def can_move(current: str, target: str, role: str) -> bool:
    if target == LOST:
        return True
    if target not in STAGE_ORDER:
        return False
    if current not in STAGE_ORDER:
        return True                      # repairing legacy data
    if STAGE_ORDER[target] >= STAGE_ORDER[current]:
        return True
    return role in ("owner", "admin")    # backwards is an admin action, audited


def missing_documents(deal: dict, target: str) -> list[str]:
    required = STAGE_DOCUMENTS.get(target, ())
    if not required:
        return []
    held = {
        d.get("name")
        for d in (deal.get("documents") or [])
        if isinstance(d, dict) and d.get("status") in DOC_OK
    }
    return [name for name in required if name not in held]


def _visit_evidence(visits: Iterable[dict], deal: dict) -> dict | None:
    """Best visit for this deal: same contact, and same property when known."""
    best = None
    for v in visits:
        if v.get("contact_id") != deal.get("contact_id"):
            continue
        prop = deal.get("property_id")
        if prop and v.get("property_id") and v["property_id"] != prop:
            continue
        if best is None or _visit_rank(v) > _visit_rank(best):
            best = v
    return best


def _visit_rank(visit: dict) -> int:
    return {"dropped": 0, "scheduled": 1, "confirmed": 2, "revisit": 3, "visited": 4}.get(
        visit.get("status", ""), 0
    )


def check_transition(deal: dict, target: str, role: str, visits: Iterable[dict]) -> dict:
    """Validate a stage move. Raises StageError; returns a dict on success.

    Returns the gate that was checked and satisfied, so the API can tell the
    client *why* the move was allowed — the same information that will be in the
    audit record.
    """
    current = deal.get("stage", "new")
    if target not in PROBABILITY:
        raise StageError("UNKNOWN_STAGE", f"Unknown stage '{target}'.")
    if not can_move(current, target, role):
        raise StageError(
            "STAGE_REGRESSION_FORBIDDEN",
            f"Moving backwards from '{current}' to '{target}' needs an owner or admin.",
            {"current": current, "target": target},
        )

    gate: dict[str, Any] = {"from": current, "to": target}

    evidence_needed = STAGE_REQUIRES_VISIT.get(target)
    if evidence_needed:
        visit = _visit_evidence(visits, deal)
        ok = visit is not None and _visit_rank(visit) >= _visit_rank({"status": evidence_needed})
        gate["visit"] = {"required": evidence_needed, "found": (visit or {}).get("status")}
        if not ok:
            raise StageError(
                "STAGE_GATE_VISIT",
                f"'{target}' needs a site visit at least '{evidence_needed}' for this contact and "
                f"property; found '{(visit or {}).get('status', 'none')}'.",
                gate,
            )

    missing = missing_documents(deal, target)
    gate["documents"] = {"required": list(STAGE_DOCUMENTS.get(target, ())), "missing": missing}
    if missing:
        raise StageError(
            "STAGE_GATE_DOCUMENTS",
            f"'{target}' needs {len(missing)} document(s) first: {', '.join(missing)}.",
            gate,
        )
    return gate


def stage_checklist(deal: dict, visits: Iterable[dict]) -> list[dict]:
    """Every stage ahead of the deal with what it still needs — for the UI."""
    out = []
    visits = list(visits)
    for stage in STAGES:
        if STAGE_ORDER[stage] <= STAGE_ORDER.get(deal.get("stage", "new"), -1):
            continue
        missing = missing_documents(deal, stage)
        need = STAGE_REQUIRES_VISIT.get(stage)
        visit = _visit_evidence(visits, deal) if need else None
        visit_ok = True if not need else (visit is not None and _visit_rank(visit) >= _visit_rank({"status": need}))
        out.append(
            {
                "stage": stage,
                "documents_missing": missing,
                "visit_required": need,
                "visit_ok": visit_ok,
                "ready": not missing and visit_ok,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 2. Property matching
# ---------------------------------------------------------------------------
TYPE_WORDS = {
    "plot": ("plot", "land", "site", "na plot", "gunte"),
    "flat": ("flat", "apartment", "2bhk", "3bhk", "1bhk", "bhk"),
    "villa": ("villa", "bungalow", "row house", "rowhouse"),
    "shop": ("shop", "commercial", "office", "showroom"),
    "farm": ("farm", "agricultural", "agri"),
}
SQFT_RE = re.compile(r"(\d[\d,]{2,6})\s*(?:sq\.?\s*ft|sqft|square\s*feet)", re.I)


def _rupees(amount: float) -> str:
    """Indian short form: 2.4L, 1.25Cr — how the trade actually speaks."""
    if amount >= 1e7:
        return f"₹{amount / 1e7:.2f}Cr".replace(".00Cr", "Cr")
    if amount >= 1e5:
        return f"₹{amount / 1e5:.1f}L".replace(".0L", "L")
    return f"₹{amount:,.0f}"


def _wanted_types(contact: dict) -> set[str]:
    text = " ".join(
        str(contact.get(k) or "") for k in ("requirement", "intent", "notes", "tags")
    ).lower()
    found = set()
    for ptype, words in TYPE_WORDS.items():
        if any(w in text for w in words):
            found.add(ptype)
    return found


def score_property(contact: dict, prop: dict) -> dict:
    """0–100 with a reason per component. Deterministic and explainable."""
    score = 0
    reasons: list[str] = []
    price = float(prop.get("price") or 0)
    bmin = contact.get("budget_min")
    bmax = contact.get("budget_max")

    if bmax:
        bmax = float(bmax)
        bmin = float(bmin) if bmin else bmax * 0.6
        if bmin <= price <= bmax:
            score += 35
            reasons.append(f"within budget: {_rupees(price)} in {_rupees(bmin)}–{_rupees(bmax)}")
        elif price < bmin:
            # Cheaper than they said they could spend: still buyable, but it may
            # be the wrong thing (a plot, not the flat they wanted). Penalty grows
            # with the distance from the bottom of their band.
            penalty = min(15.0, 15.0 * (1 - price / bmin))
            score += int(round(35 - penalty))
            reasons.append(f"below the stated range: {_rupees(price)} vs {_rupees(bmin)}–{_rupees(bmax)}")
        elif price <= bmax * 1.1:
            score += 25
            reasons.append(f"slightly over budget: {_rupees(price)} vs {_rupees(bmax)} (+{((price / bmax) - 1) * 100:.0f}%)")
        else:
            reasons.append(f"over budget by {((price / bmax) - 1) * 100:.0f}%: {_rupees(price)} vs {_rupees(bmax)}")
    else:
        score += 15
        reasons.append("no stated budget: neutral score")

    requirement = str(contact.get("requirement") or "").lower()
    locality = str(prop.get("locality") or "").lower()
    city = str(prop.get("city") or "").lower()
    if locality and locality in requirement:
        score += 25
        reasons.append(f"locality '{prop.get('locality')}' named in the requirement")
    elif city and (city in requirement or city == str(contact.get("city") or "").lower()):
        score += 18
        reasons.append(f"city matches ({prop.get('city')})")
    else:
        reasons.append(f"location not mentioned ({prop.get('locality') or prop.get('city')})")

    wanted = _wanted_types(contact)
    if wanted and str(prop.get("type") or "").lower() in wanted:
        score += 20
        reasons.append(f"type '{prop.get('type')}' matches the stated requirement")
    elif wanted:
        reasons.append(f"type '{prop.get('type')}' differs from requirement ({', '.join(sorted(wanted))})")
    else:
        score += 8
        reasons.append("no stated property type: partial credit")

    m = SQFT_RE.search(str(contact.get("requirement") or ""))
    if m and prop.get("size_sqft"):
        wanted_sqft = int(m.group(1).replace(",", ""))
        actual = float(prop["size_sqft"])
        if wanted_sqft * 0.8 <= actual <= wanted_sqft * 1.25:
            score += 10
            reasons.append(f"size {actual:,.0f} sqft near the requested {wanted_sqft:,} sqft")
        else:
            reasons.append(f"size {actual:,.0f} sqft outside the requested {wanted_sqft:,} sqft ±20%")

    status = str(prop.get("status") or "").lower()
    if status == "available":
        score += 10
        reasons.append("available now")
    else:
        reasons.append(f"not available ({status or 'unknown'})")

    return {
        "property_id": prop.get("id"),
        "title": prop.get("title"),
        "score": min(100, score),
        "reasons": reasons,
        "property": prop,
    }


def match_properties(contact: dict, properties: Iterable[dict], limit: int = 5) -> list[dict]:
    scored = [score_property(contact, p) for p in properties]
    scored.sort(key=lambda r: (-r["score"], str(r.get("title") or "")))
    return scored[:limit]


# ---------------------------------------------------------------------------
# 3. Site visits
# ---------------------------------------------------------------------------
VISIT_STATUSES = ("scheduled", "confirmed", "visited", "revisit", "dropped")
VISIT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "scheduled": ("confirmed", "visited", "dropped"),
    "confirmed": ("visited", "dropped"),
    "visited": ("revisit",),
    "revisit": ("visited", "dropped"),
    "dropped": (),
}
DEFAULT_DURATION_MIN = 45


def check_visit_transition(current: str, target: str) -> None:
    if target not in VISIT_STATUSES:
        raise StageError("UNKNOWN_VISIT_STATUS", f"Unknown visit status '{target}'.")
    if target == current:
        return
    if target not in VISIT_TRANSITIONS.get(current, ()):
        raise StageError(
            "VISIT_TRANSITION_FORBIDDEN",
            f"A visit cannot go from '{current}' to '{target}'.",
            {"current": current, "allowed": list(VISIT_TRANSITIONS.get(current, ()))},
        )


def find_conflict(
    visits: Iterable[dict],
    *,
    agent_id: str,
    scheduled_at: int,
    duration_min: int = DEFAULT_DURATION_MIN,
    exclude_id: str = "",
) -> dict | None:
    """Overlapping visit for the same agent. Half-open intervals, seconds.

    A visit that was dropped no longer occupies the slot — otherwise a cancelled
    visit would block the calendar forever.
    """
    start = int(scheduled_at)
    end = start + int(duration_min) * 60
    for v in visits:
        if v.get("id") == exclude_id:
            continue
        if v.get("agent_id") != agent_id:
            continue
        if v.get("status") == "dropped":
            continue
        v_start = int(v.get("scheduled_at") or 0)
        v_end = v_start + int(v.get("duration_min") or DEFAULT_DURATION_MIN) * 60
        if start < v_end and v_start < end:
            return v
    return None


def visit_window(day_start: int, day_end: int) -> tuple[int, int]:
    return int(day_start), int(day_end)


# ---------------------------------------------------------------------------
# 4. Commission
# ---------------------------------------------------------------------------
def commission_breakdown(
    deal: dict,
    *,
    rate_pct: float | None = None,
    agent_share_pct: float | None = None,
    channel_partner_id: str = "",
    channel_partner_pct: float | None = None,
) -> dict:
    """Gross commission, channel-partner cut, then agent/brokerage split.

    All arithmetic in paise (integers) with a single rounding step per recipient,
    and the remainder assigned to the brokerage so the four numbers always sum to
    the gross. Percentage inputs are clamped to sane ranges rather than trusted.
    """
    value = float(deal.get("value") or 0)
    existing = deal.get("commission") or {}
    rate = float(rate_pct if rate_pct is not None else existing.get("rate_pct", 2.0))
    agent_pct = float(agent_share_pct if agent_share_pct is not None else existing.get("agent_share_pct", 60))
    cp_pct = float(channel_partner_pct if channel_partner_pct is not None else existing.get("channel_partner_pct", 0))
    cp_id = channel_partner_id or existing.get("channel_partner_id", "")

    if not (0 <= rate <= 25):
        raise StageError("INVALID_COMMISSION", "rate_pct must be between 0 and 25.")
    if not (0 <= agent_pct <= 100):
        raise StageError("INVALID_COMMISSION", "agent_share_pct must be between 0 and 100.")
    if not (0 <= cp_pct <= 100):
        raise StageError("INVALID_COMMISSION", "channel_partner_pct must be between 0 and 100.")

    if value <= 0:
        raise StageError("DEAL_HAS_NO_VALUE", "The deal has no value; commission cannot be computed.")

    paise = lambda rupees: int(round(rupees * 100))  # noqa: E731
    gross = paise(value * rate / 100)
    cp_amount = 0 if not cp_id else int(round(gross * cp_pct / 100))
    remainder = gross - cp_amount
    agent_amount = int(round(remainder * agent_pct / 100))
    brokerage_amount = remainder - agent_amount

    return {
        "deal_value": int(round(value)),
        "rate_pct": rate,
        "agent_share_pct": agent_pct,
        "channel_partner_pct": cp_pct if cp_id else 0,
        "channel_partner_id": cp_id,
        "gross": gross / 100,
        "channel_partner": cp_amount / 100,
        "agent": agent_amount / 100,
        "brokerage": brokerage_amount / 100,
        "gross_paise": gross,
        "channel_partner_paise": cp_amount,
        "agent_paise": agent_amount,
        "brokerage_paise": brokerage_amount,
        "sums_to_gross": (cp_amount + agent_amount + brokerage_amount) == gross,
        "payout_status": existing.get("payout_status", "pending"),
    }

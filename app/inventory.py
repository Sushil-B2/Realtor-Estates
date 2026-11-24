"""
Unit-level inventory — because "the property is available" is not a unit.

A project has units; a unit is the thing that gets blocked, booked and sold. Every
double-booking complaint in a real-estate office is the same bug: the CRM models
the project, two agents promise the same flat, and the second buyer finds out at
registration. docs/18 §4 #4 and §6 #6.

Three rules, encoded here as pure functions:

1. **A block is a timed reservation with an owner.** `block(unit, deal, hours)`
   holds the unit for one deal until an expiry — the "48-hour price hold" the
   funnel pages already advertise. A block that outlives a cron job is a lie, so
   expiry is *derived* at read time: an expired block reads as `available`
   without anything having to run.
2. **Only one deal may hold a unit.** A second block is refused with the holder
   and the time remaining, not "conflict" — the agent needs to know who to call.
3. **State only moves forward.** available → blocked → booked → sold, with
   release back to available from blocked/booked (a cancelled booking), and
   `withdrawn` for a unit the developer pulls. Nothing may skip booking on the
   way to sold, because a sale with no token is a data-entry accident.
"""
from __future__ import annotations

import time
from typing import Iterable

STATUSES = ("available", "blocked", "booked", "sold", "withdrawn")
DEFAULT_BLOCK_HOURS = 48
MAX_BLOCK_HOURS = 24 * 30

# Which transitions a manual operator may make. `sold` is only reachable through
# `sell()` and `booked` only through `book()`, so a status PATCH cannot fabricate
# a sale.
MANUAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "available": ("withdrawn",),
    "blocked": ("available", "withdrawn"),
    "booked": ("available", "withdrawn"),
    "sold": (),
    "withdrawn": ("available",),
}


class InventoryError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# 1. Generation
# ---------------------------------------------------------------------------
def price_ladder(base_price: float, step: float, floor: int, ground_floor: int = 1) -> int:
    """Indian projects price by floor; a negative step is refused rather than
    producing cheaper upper floors by accident."""
    if base_price <= 0:
        raise InventoryError("INVALID_PRICE", "Base price must be positive.")
    if step < 0:
        raise InventoryError("INVALID_PRICE", "The per-floor step cannot be negative.")
    return int(round(base_price + step * max(0, int(floor) - int(ground_floor))))


def generate_units(
    *,
    floors: int,
    per_floor: int,
    base_price: float,
    price_step: float = 0,
    start_floor: int = 1,
    type_: str = "2BHK",
    area_sqft: float = 0,
    facing_cycle: tuple[str, ...] = ("East", "West", "North", "South"),
) -> list[dict]:
    """A deterministic grid: floor-major, `A/B/C…` per floor.

    Deterministic matters: two people clicking "generate" must not produce two
    different sets of units with the same numbers.
    """
    if not (1 <= int(floors) <= 60):
        raise InventoryError("INVALID_GRID", "floors must be between 1 and 60.")
    if not (1 <= int(per_floor) <= 12):
        raise InventoryError("INVALID_GRID", "per_floor must be between 1 and 12.")
    out = []
    for f in range(int(start_floor), int(start_floor) + int(floors)):
        for i in range(int(per_floor)):
            letter = chr(ord("A") + i)
            out.append(
                {
                    "unit_no": f"{f}{letter}",
                    "floor": f,
                    "type": type_[:24],
                    "area_sqft": int(area_sqft or 0),
                    "price": price_ladder(base_price, price_step, f, int(start_floor)),
                    "facing": facing_cycle[i % len(facing_cycle)],
                    "status": "available",
                }
            )
    return out


# ---------------------------------------------------------------------------
# 2. Reading state
# ---------------------------------------------------------------------------
def effective_status(unit: dict, at: int | None = None) -> str:
    """An expired block reads as available; nobody has to sweep the table."""
    at = int(at if at is not None else time.time())
    status = unit.get("status", "available")
    if status == "blocked":
        block = unit.get("block") or {}
        if block.get("until") and int(block["until"]) <= at:
            return "available"
    return status


def block_owner(unit: dict, at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    if effective_status(unit, at) == "blocked":
        return unit.get("block") or {}
    return {}


def describe(unit: dict, at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    out = {**unit, "status": status}
    block = unit.get("block") or {}
    if status == "blocked":
        out["block_hours_left"] = max(0, round((int(block.get("until") or at) - at) / 3600, 1))
    else:
        out["block_hours_left"] = 0
    out["block_expired"] = bool(unit.get("status") == "blocked" and status == "available")
    return out


# ---------------------------------------------------------------------------
# 3. Transitions
# ---------------------------------------------------------------------------
def can_block(unit: dict, deal_id: str, at: int | None = None) -> tuple[bool, str, dict]:
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    if status == "available":
        return True, "", {}
    if status == "blocked":
        block = unit.get("block") or {}
        if block.get("deal_id") == deal_id:
            return True, "", {"renewal": True}
        return False, "UNIT_BLOCKED", {
            "held_by_deal": block.get("deal_id"),
            "held_by_contact": block.get("contact_id"),
            "until": block.get("until"),
            "hours_left": max(0, round((int(block.get("until") or at) - at) / 3600, 1)),
        }
    return False, "UNIT_NOT_AVAILABLE", {"status": status}


def apply_block(unit: dict, *, deal_id: str, contact_id: str, by: str, hours: int = DEFAULT_BLOCK_HOURS,
                reason: str = "", at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    hours = max(1, min(MAX_BLOCK_HOURS, int(hours or DEFAULT_BLOCK_HOURS)))
    ok, code, details = can_block(unit, deal_id, at)
    if not ok:
        raise InventoryError(code, _block_message(code, details), details)
    history = list(unit.get("history") or [])
    history.append({"event": "blocked", "by": by, "at": at, "deal_id": deal_id, "hours": hours})
    return {
        "status": "blocked",
        "block": {"deal_id": deal_id, "contact_id": contact_id, "by": by, "at": at,
                  "until": at + hours * 3600, "hours": hours, "reason": reason[:120]},
        "history": history[-30:],
    }


def _block_message(code: str, details: dict) -> str:
    if code == "UNIT_BLOCKED":
        return (f"This unit is held by another deal until "
                f"{time.strftime('%d %b %H:%M', time.localtime(int(details.get('until') or 0)))} "
                f"({details.get('hours_left')} h left).")
    return f"This unit is {details.get('status')}, not available."


def apply_release(unit: dict, *, by: str, deal_id: str | None = None, at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    if status not in ("blocked", "booked"):
        raise InventoryError("NOTHING_TO_RELEASE", f"This unit is {status}; there is nothing to release.")
    holder = (unit.get("block") or {}).get("deal_id") or (unit.get("booking") or {}).get("deal_id")
    if deal_id and holder and holder != deal_id:
        raise InventoryError("UNIT_HELD_BY_OTHER", "Another deal holds this unit; releasing it is a manager action.")
    history = list(unit.get("history") or [])
    history.append({"event": "released", "from": status, "by": by, "at": at, "deal_id": holder or ""})
    return {"status": "available", "block": {}, "booking": {}, "history": history[-30:]}


def apply_booking(unit: dict, *, deal_id: str, contact_id: str, by: str, token_amount: float = 0,
                  at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    if status == "booked":
        holder = (unit.get("booking") or {}).get("deal_id")
        if holder == deal_id:
            return {}
        raise InventoryError("UNIT_ALREADY_BOOKED", "This unit is already booked by another deal.",
                             {"held_by_deal": holder})
    if status == "sold":
        raise InventoryError("UNIT_SOLD", "This unit is sold.")
    if status == "withdrawn":
        raise InventoryError("UNIT_WITHDRAWN", "This unit has been withdrawn from sale.")
    if status == "blocked":
        holder = (unit.get("block") or {}).get("deal_id")
        if holder and holder != deal_id:
            raise InventoryError("UNIT_BLOCKED", "Another deal has this unit on hold.",
                                 {"held_by_deal": holder, "until": (unit.get("block") or {}).get("until")})
    history = list(unit.get("history") or [])
    history.append({"event": "booked", "by": by, "at": at, "deal_id": deal_id})
    return {
        "status": "booked",
        "block": {},
        "booking": {"deal_id": deal_id, "contact_id": contact_id, "at": at,
                    "token_amount": max(0.0, float(token_amount or 0)), "by": by},
        "history": history[-30:],
    }


def apply_sale(unit: dict, *, deal_id: str, by: str, at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    booked_by = (unit.get("booking") or {}).get("deal_id")
    if status == "sold":
        return {}
    if status != "booked" or booked_by != deal_id:
        raise InventoryError(
            "SALE_WITHOUT_BOOKING",
            "A unit can only be sold through the deal that booked it — book it first.",
            {"status": status, "booked_by": booked_by},
        )
    history = list(unit.get("history") or [])
    history.append({"event": "sold", "by": by, "at": at, "deal_id": deal_id})
    return {"status": "sold", "sold": {"deal_id": deal_id, "at": at, "by": by}, "history": history[-30:]}


def apply_manual_status(unit: dict, target: str, by: str, at: int | None = None) -> dict:
    """Withdraw/reinstate. Deliberately cannot reach `booked` or `sold`."""
    at = int(at if at is not None else time.time())
    status = effective_status(unit, at)
    if target in ("booked", "sold"):
        raise InventoryError("USE_THE_DEAL", "Booking and selling happen through a deal, not a status change.")
    if target not in MANUAL_TRANSITIONS.get(status, ()):
        raise InventoryError("INVALID_TRANSITION", f"Cannot move a unit from '{status}' to '{target}'.",
                             {"allowed": list(MANUAL_TRANSITIONS.get(status, ()))})
    history = list(unit.get("history") or [])
    history.append({"event": f"status→{target}", "from": status, "by": by, "at": at})
    patch = {"status": target, "history": history[-30:]}
    if status == "withdrawn" and target == "available":
        patch["block"] = {}
    return patch


# ---------------------------------------------------------------------------
# 4. Summary
# ---------------------------------------------------------------------------
def summarize(units: Iterable[dict], at: int | None = None) -> dict:
    at = int(at if at is not None else time.time())
    counts = {s: 0 for s in STATUSES}
    value = {s: 0 for s in STATUSES}
    for u in units:
        status = effective_status(u, at)
        counts[status] = counts.get(status, 0) + 1
        value[status] = value.get(status, 0) + int(u.get("price") or 0)
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "value": value,
        "sellable": counts["available"] + counts["blocked"],
        "availability_pct": round(100 * (counts["available"] + counts["blocked"]) / total, 1) if total else 0.0,
        "unsold_value": value["available"] + value["blocked"] + value["booked"],
        "sold_value": value["sold"],
    }

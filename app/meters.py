"""
Usage that is measured, not narrated.

The workspace settings card has shown `voice_minutes: 4820` and
`whatsapp_messages: 18422` since the first release. Neither number was read from
anything: they were two literals in a route, printed as if they were usage. A
customer on a usage-based plan would have been invoiced from a decoration.

That is the same defect as `mfa_enabled` (docs/27) and the same lesson: **a number
shown to a customer must be counted by the system that spends it, in the same
place the work happens, or it is a claim and not a measurement.**

So:

* A meter is **derived** from the rows that already record the work — an AI run is
  a `runs` row, an outbound WhatsApp message is a message inside a conversation, a
  voice minute is a logged call. Nothing new to keep in sync, and no way for the
  panel to disagree with the desk.
* Where no row exists (storage bytes, a credit, a refund), the meter is a
  **ledger**: append-only `usage_ledger` rows, idempotent on `(meter, source_id)`.
* A period is a calendar month in the tenant's timezone, because "this month" on
  an Indian invoice means IST and a UTC month would bill the 1st of the month's
  first five and a half hours to the wrong invoice.
* A plan has caps; a write that would cross one is refused with `402
  QUOTA_EXCEEDED` naming the meter and the reset date, *before* the work is done.
* A month that has ended can be **closed**: a snapshot is written, so the invoice
  can still be reconstructed after a demo purge has deleted the rows it came from.
  A purge must not be able to shrink an invoice.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone as dt_timezone

from . import security as sec

# The product is Indian (RERA, DPDP, GST). A tiny map rather than a dependency:
# a wrong-by-30-minutes month boundary is a billing defect, and tzdata is a
# dependency this platform does not take.
TZ_OFFSETS = {"Asia/Kolkata": 330, "Asia/Calcutta": 330, "UTC": 0, "Etc/UTC": 0}
DEFAULT_TZ = "Asia/Kolkata"
MONEY_UNIT = "µUSD"          # provider spend, kept as integer micro-dollars


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------- time & periods
def offset_minutes(tz: str) -> int:
    return TZ_OFFSETS.get(tz or "", TZ_OFFSETS[DEFAULT_TZ])


def period_of(at: int | None = None, tz: str = DEFAULT_TZ) -> str:
    ts = now() if at is None else int(at)
    local = datetime.fromtimestamp(ts, dt_timezone(timedelta(minutes=offset_minutes(tz))))
    return local.strftime("%Y-%m")


def period_bounds(period: str, tz: str = DEFAULT_TZ) -> tuple[int, int]:
    """First and last second of a period, as epoch seconds."""
    start = datetime.strptime(period + "-01", "%Y-%m-%d").replace(tzinfo=dt_timezone(timedelta(minutes=offset_minutes(tz))))
    year, month = start.year + (start.month // 12), start.month % 12 + 1
    end = start.replace(year=year, month=month)
    return int(start.timestamp()), int(end.timestamp()) - 1


def shift_period(period: str, months: int) -> str:
    year, month = int(period[:4]), int(period[5:7])
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def period_label(period: str) -> str:
    return datetime.strptime(period + "-01", "%Y-%m-%d").strftime("%B %Y")


def next_reset_at(period: str, tz: str = DEFAULT_TZ) -> int:
    """When the counter goes back to zero — the date a customer is told."""
    return period_bounds(period, tz)[1] + 1


# ------------------------------------------------------------------- the meters
def _runs(rows: list[dict], period: str, tz: str) -> list[dict]:
    start, end = period_bounds(period, tz)
    return [r for r in rows if start <= int(r.get("created_at") or 0) <= end]


def _messages(rows: list[dict], period: str, tz: str, channel: str) -> list[dict]:
    start, end = period_bounds(period, tz)
    out = []
    for conv in rows:
        for msg in conv.get("messages") or []:
            if msg.get("dir") != "out":
                continue
            if (msg.get("channel") or conv.get("channel")) != channel:
                continue
            if start <= int(msg.get("at") or 0) <= end:
                out.append(msg)
    return out


def _call_minutes(rows: list[dict], period: str, tz: str) -> tuple[int, int]:
    """Started minutes, billed per call, and how many calls came from a legacy field.

    Per call, not per month, because that is how a call is sold: rounding the
    month's total to the next minute would under-bill every workspace by up to a
    minute and hide the rule from the customer. Two 30-second calls are two
    minutes — which is what the telco charges and therefore what we bill.
    """
    start, end = period_bounds(period, tz)
    total = legacy = 0
    import re
    for row in rows:
        if row.get("type") not in ("call", "ai_call", "voice"):
            continue
        at = int(row.get("at") or row.get("created_at") or 0)
        if not (start <= at <= end):
            continue
        if row.get("duration_sec") is not None:
            seconds = int(row.get("duration_sec") or 0)
        else:
            match = re.search(r"(\d+)\s*m\s*(\d+)\s*s", str(row.get("text") or ""))
            if not match:
                continue
            seconds = int(match.group(1)) * 60 + int(match.group(2))
            legacy += 1
        total += (seconds + 59) // 60
    return total, legacy


# name -> (label, unit, kind, cap key or None) — kind: flow (per month) or stock.
METERS: dict[str, dict] = {
    "ai_runs": {"label": "AI runs executed", "unit": "runs", "kind": "flow",
                "why": "a run that was blocked or skipped costs nothing and is not counted"},
    "ai_runs_blocked": {"label": "AI runs blocked by a guardrail", "unit": "runs", "kind": "flow",
                        "capped": False,
                        "why": "reported so a cap cannot hide a guardrail doing its job"},
    "ai_spend_micros": {"label": "AI provider spend", "unit": MONEY_UNIT, "kind": "flow"},
    "whatsapp_out": {"label": "WhatsApp messages sent", "unit": "messages", "kind": "flow"},
    "sms_out": {"label": "SMS messages sent", "unit": "messages", "kind": "flow"},
    "voice_minutes": {"label": "Voice minutes", "unit": "minutes", "kind": "flow"},
    "contacts": {"label": "Contacts stored", "unit": "contacts", "kind": "stock"},
    "storage_mb": {"label": "Media stored", "unit": "MB", "kind": "stock", "ledger_only": True,
                   "why": "the image pipeline reports bytes into the ledger; there is no row to derive"},
}

# A plan is a promise about limits. `None` means unlimited, and is written rather
# than omitted so "we forgot" and "there is no cap" cannot look the same.
PLAN_CAPS: dict[str, dict[str, int | None]] = {
    "trial": {"contacts": 250, "ai_runs": 300, "ai_runs_blocked": None, "ai_spend_micros": 5_000_000,
              "whatsapp_out": 500, "sms_out": 200, "voice_minutes": 60, "storage_mb": 256},
    "starter": {"contacts": 2_000, "ai_runs": 2_000, "ai_runs_blocked": None, "ai_spend_micros": 40_000_000,
                "whatsapp_out": 5_000, "sms_out": 2_000, "voice_minutes": 500, "storage_mb": 2_048},
    "growth": {"contacts": 25_000, "ai_runs": 25_000, "ai_runs_blocked": None, "ai_spend_micros": 400_000_000,
               "whatsapp_out": 50_000, "sms_out": 20_000, "voice_minutes": 5_000, "storage_mb": 20_480},
    "scale": {"contacts": 250_000, "ai_runs": 250_000, "ai_runs_blocked": None, "ai_spend_micros": 4_000_000_000,
              "whatsapp_out": 500_000, "sms_out": 200_000, "voice_minutes": 50_000, "storage_mb": 204_800},
}
DEFAULT_PLAN = "trial"


def caps_for(plan: str) -> dict[str, int | None]:
    return dict(PLAN_CAPS.get((plan or DEFAULT_PLAN).lower(), PLAN_CAPS[DEFAULT_PLAN]))


class PeriodClosed(Exception):
    """A write aimed at a month that has already been frozen."""

    def __init__(self, period: str):
        self.period = period
        self.message = (
            f"{period_label(period)} is closed and cannot be adjusted. Post the correction to the "
            f"current month and name {period} in the reason."
        )
        super().__init__(self.message)


class QuotaExceeded(Exception):
    """A write that would cross a plan cap. The API turns this into a 402."""

    def __init__(self, meter: str, used: int, cap: int, period: str, tz: str):
        self.meter = meter
        self.used = used
        self.cap = cap
        self.period = period
        meta = METERS.get(meter, {})
        label = meta.get("label", meter)
        # A flow resets and telling the customer when is the useful part. A stock
        # does not reset — telling somebody their stored contacts come back on the
        # 1st would be a lie, so the message has to say what actually helps.
        self.resets_at = None if meta.get("kind") == "stock" else next_reset_at(period, tz)
        if self.resets_at:
            when = datetime.fromtimestamp(
                self.resets_at, dt_timezone(timedelta(minutes=offset_minutes(tz)))).strftime("%d %b %Y")
            self.message = f"{label} for this plan: {used:,} of {cap:,} used. It resets on {when}."
        else:
            self.message = (f"{label} for this plan: {used:,} of {cap:,}. That is a ceiling, not a "
                            f"monthly allowance — raise the plan, or archive what is no longer needed.")
        super().__init__(self.message)


# ------------------------------------------------------------------- measuring
def tenant_tz(tenant: dict) -> str:
    return str((tenant.get("settings") or {}).get("timezone") or DEFAULT_TZ)


def ledger_sum(store, tenant_id: str, meter: str, period: str) -> int:
    if "usage_ledger" not in store.db:
        return 0
    return sum(int(r.get("qty") or 0) for r in store.all("usage_ledger", tenant_id)
               if r.get("meter") == meter and r.get("period") == period)


# A run reaches a provider — and costs money — unless a guardrail stopped it
# before the call. `partial` means the model answered and a tool awaited approval;
# `held` means the answer was withheld after it was paid for. Both are billed.
COUNTED_STATUSES = ("ok", "partial", "complete", "held")
BLOCKED_STATUS = "blocked"
UNCOUNTED_STATUSES = ("skipped", "pending", "refused")


def derive(store, tenant_id: str, period: str, tz: str) -> dict[str, int]:
    """What the rows say. Every number here is read from work that already happened."""
    runs = _runs(store.all("runs", tenant_id), period, tz)
    executed = [r for r in runs if r.get("status") in COUNTED_STATUSES]
    blocked = [r for r in runs if r.get("status") == BLOCKED_STATUS]
    whatsapp = _messages(store.all("conversations", tenant_id), period, tz, "whatsapp")
    sms = _messages(store.all("conversations", tenant_id), period, tz, "sms")
    minutes, _legacy = _call_minutes(store.all("activities", tenant_id), period, tz)
    return {
        "ai_runs": len(executed),
        "ai_runs_blocked": len(blocked),
        "ai_spend_micros": int(round(sum(float(r.get("cost_usd") or 0) for r in executed) * 1_000_000)),
        "whatsapp_out": len(whatsapp),
        "sms_out": len(sms),
        "voice_minutes": minutes,                  # per call, a part-minute is billed
        "contacts": len(store.all("contacts", tenant_id)),
        "storage_mb": 0,                            # ledger only (see METERS)
    }


def used(store, tenant: dict, meter: str, period: str) -> int:
    tz = tenant_tz(tenant)
    base = derive(store, tenant["id"], period, tz).get(meter, 0)
    return base + ledger_sum(store, tenant["id"], meter, period)


def _status(used_value: int, cap: int | None) -> str:
    if cap is None:
        return "unmetered"
    if used_value >= cap:
        return "over"
    return "near" if used_value >= cap * 0.8 else "ok"


def report(store, tenant: dict, period: str | None = None) -> dict:
    """Everything a usage panel (or an invoice run) needs for one period."""
    tz = tenant_tz(tenant)
    period = period or period_of(tz=tz)
    derived = derive(store, tenant["id"], period, tz)
    caps = caps_for(tenant.get("plan", DEFAULT_PLAN))
    rows = []
    for name, meta in METERS.items():
        base = derived.get(name, 0)
        led = ledger_sum(store, tenant["id"], name, period)
        total = base + led
        cap = None if meta.get("capped") is False else caps.get(name)
        rows.append({
            "meter": name,
            "label": meta["label"],
            "unit": meta["unit"],
            "kind": meta["kind"],
            "used": total,
            "measured": base,
            "adjusted": led,
            "cap": cap,
            "remaining": None if cap is None else max(0, cap - total),
            "pct": None if not cap else round(100 * total / cap, 1),
            "status": _status(total, cap),
            "why": meta.get("why", ""),
        })
    snap = snapshot_of(store, tenant["id"], period)
    return {
        "period": period,
        "label": period_label(period),
        "timezone": tz,
        "plan": tenant.get("plan", DEFAULT_PLAN),
        "meters": rows,
        "over": [r["meter"] for r in rows if r["status"] == "over"],
        "near": [r["meter"] for r in rows if r["status"] == "near"],
        "resets_at": next_reset_at(period, tz),
        "current": period == period_of(tz=tz),
        "closed": bool(snap),
        "closed_at": (snap or {}).get("closed_at"),
    }


def series(store, tenant: dict, periods: int = 6) -> list[dict]:
    """The last N months, oldest first. A closed month reports its snapshot."""
    tz = tenant_tz(tenant)
    current = period_of(tz=tz)
    out = []
    for back in range(periods - 1, -1, -1):
        period = shift_period(current, -back)
        snap = snapshot_of(store, tenant["id"], period)
        if snap:
            out.append({"period": period, "label": period_label(period), "closed": True,
                        "meters": snap.get("meters", {})})
        else:
            live = report(store, tenant, period)
            out.append({"period": period, "label": period_label(period), "closed": False,
                        "meters": {r["meter"]: r["used"] for r in live["meters"]}})
    return out


# ------------------------------------------------------------------ enforcement
def enforce(store, tenant: dict, meter: str, add: int = 1, period: str | None = None) -> None:
    """Refuse a write that would cross the plan's cap — before the work is done."""
    tz = tenant_tz(tenant)
    period = period or period_of(tz=tz)
    cap = caps_for(tenant.get("plan", DEFAULT_PLAN)).get(meter)
    if cap is None or METERS.get(meter, {}).get("capped") is False:
        return
    current = used(store, tenant, meter, period)
    if current + add > cap:
        raise QuotaExceeded(meter, current, cap, period, tz)


# A derive per write is a scan of runs, messages and calls. Correct and cheap at
# demo scale; a bad idea at a hundred thousand rows. The memo is the honest hedge
# and the doc says what the real answer is (counters, not scans).
_WARN_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
WARN_TTL = 20


def warnings(store, tenant: dict, period: str | None = None) -> dict[str, str]:
    """A header, not a modal: 80% of an allowance should not interrupt the work."""
    if not tenant or not tenant.get("id"):
        return {}
    key = (tenant["id"], period or "")
    hit = _WARN_CACHE.get(key)
    if hit and time.time() - hit[0] < WARN_TTL:
        return hit[1]
    try:
        data = report(store, tenant, period)
    except Exception:
        return {}
    near = [r for r in data["meters"] if r["status"] in ("near", "over")]
    header = {}
    if near:
        worst = max(near, key=lambda r: (r["pct"] or 0))
        header = {"X-Usage-Warning": f"{worst['meter']} {worst['pct']}% of {worst['cap']:,} {worst['unit']}"}
    _WARN_CACHE[key] = (time.time(), header)
    return header


# ---------------------------------------------------------------------- ledger
def record(store, tenant_id: str, meter: str, qty: int, *, source_id: str, reason: str = "",
           at: int | None = None, period: str | None = None, tz: str = DEFAULT_TZ) -> dict:
    """Append a ledger row, once per (meter, source_id).

    The source id is what makes this safe to retry: a webhook redelivery, a proof
    run, a failed response that was retried by the client — all of them arrive
    twice, and a meter that counts them twice is a meter that overbills.
    """
    if meter not in METERS:
        raise ValueError(f"Unknown meter '{meter}'.")
    at = now() if at is None else int(at)
    period = period or period_of(at, tz)
    # A closed month is an invoice. A correction that arrives after the close is
    # posted to the current month, with the old period named in its reason — it
    # never silently rewrites a number somebody has already been billed for.
    if snapshot_of(store, tenant_id, period):
        raise PeriodClosed(period)
    # store.all, not store.db: with per-request database reads the in-memory
    # working set holds no ledger rows, and a duplicate would be billed twice.
    for row in store.all("usage_ledger", tenant_id):
        if (row.get("tenant_id") == tenant_id and row.get("meter") == meter
                and row.get("source_id") == source_id):
            return {**row, "_duplicate": True}
    _WARN_CACHE.clear()
    return store.insert("usage_ledger", tenant_id, {
        "meter": meter,
        "qty": int(qty),
        "source_id": source_id,
        "reason": reason[:200],
        "at": at,
        "period": period,
    }, prefix="usg")


def ledger(store, tenant_id: str, period: str | None = None, meter: str = "") -> list[dict]:
    rows = [r for r in store.all("usage_ledger", tenant_id) if r.get("tenant_id") == tenant_id]
    if period:
        rows = [r for r in rows if r.get("period") == period]
    if meter:
        rows = [r for r in rows if r.get("meter") == meter]
    return sorted(rows, key=lambda r: int(r.get("at") or 0), reverse=True)


# ------------------------------------------------------------------ snapshots
def snapshot_of(store, tenant_id: str, period: str) -> dict | None:
    for row in store.all("usage_snapshots", tenant_id):
        if row.get("tenant_id") == tenant_id and row.get("period") == period:
            return row
    return None


def close(store, tenant: dict, period: str, *, by: str = "") -> tuple[dict, bool]:
    """Freeze a finished month. Returns (snapshot, already_closed)."""
    tz = tenant_tz(tenant)
    if period >= period_of(tz=tz):
        raise ValueError("Only a month that has ended can be closed.")
    existing = snapshot_of(store, tenant["id"], period)
    if existing:
        return existing, True
    live = report(store, tenant, period)
    snap = store.insert("usage_snapshots", tenant["id"], {
        "period": period,
        "label": period_label(period),
        "plan": tenant.get("plan", DEFAULT_PLAN),
        "meters": {r["meter"]: r["used"] for r in live["meters"]},
        "caps": {r["meter"]: r["cap"] for r in live["meters"]},
        "closed_at": now(),
        "closed_by": by,
    }, prefix="usg")
    return snap, False


def invoice_lines(store, tenant: dict, period: str) -> list[dict]:
    """What a usage-based invoice would bill for, from the snapshot if there is one.

    This is the reason snapshots exist: a purge deletes rows, and a purge must not
    change what was already invoiced.
    """
    snap = snapshot_of(store, tenant["id"], period)
    tz = tenant_tz(tenant)
    if snap:
        meters, source = snap.get("meters", {}), "snapshot"
    else:
        meters = {r["meter"]: r["used"] for r in report(store, tenant, period)["meters"]}
        source = "live"
    caps = caps_for(snap.get("plan") if snap else tenant.get("plan", DEFAULT_PLAN))
    lines = []
    for name, meta in METERS.items():
        quantity = int(meters.get(name) or 0)
        cap = caps.get(name)
        lines.append({
            "meter": name, "label": meta["label"], "unit": meta["unit"],
            "quantity": quantity,
            "included": cap,
            "overage": max(0, quantity - cap) if cap else 0,
            "period": period, "source": source,
        })
    return lines


def summary_for_tenant_row(store, tenant: dict) -> dict:
    """The four numbers the workspace card shows, for *this* month, measured."""
    tz = tenant_tz(tenant)
    period = period_of(tz=tz)
    derived = derive(store, tenant["id"], period, tz)
    return {
        "period": period,
        "period_label": period_label(period),
        "contacts": derived["contacts"],
        "agents": len(store.all("agents", tenant["id"])),
        "voice_minutes": derived["voice_minutes"] + ledger_sum(store, tenant["id"], "voice_minutes", period),
        "whatsapp_messages": derived["whatsapp_out"] + ledger_sum(store, tenant["id"], "whatsapp_out", period),
        "ai_runs": derived["ai_runs"] + ledger_sum(store, tenant["id"], "ai_runs", period),
        "measured_from": "rows",
    }


def fingerprint(tenant_id: str, period: str) -> str:
    """A short, stable id for a period's figures — useful in a support ticket."""
    return sec.sha256_hex(f"{tenant_id}:{period}")[:12]


# ---------------------------------------------------------------------------
# Sprint S4 — SaaS Subscription Billing & Five Cost-Drivers Dashboard
# (`docs/09` §2, §3, §4, §9 & `docs/40`)
# ---------------------------------------------------------------------------
PLAN_PRICING_PAISE: dict[str, dict[str, int]] = {
    "trial": {
        "monthly_paise": 0,
        "seats_included": 2,
        "msg_overage_paise": 90,       # ₹0.90 / message
        "voice_overage_paise": 600,    # ₹6.00 / minute
        "ai_run_overage_paise": 50,    # ₹0.50 / AI run
    },
    "starter": {
        "monthly_paise": 999_900,      # ₹9,999 / month
        "seats_included": 3,
        "msg_overage_paise": 90,       # ₹0.90 / message
        "voice_overage_paise": 600,    # ₹6.00 / minute
        "ai_run_overage_paise": 45,
    },
    "growth": {
        "monthly_paise": 2_499_900,    # ₹24,999 / month
        "seats_included": 10,
        "msg_overage_paise": 70,       # ₹0.70 / message
        "voice_overage_paise": 500,    # ₹5.00 / minute
        "ai_run_overage_paise": 35,
    },
    "scale": {
        "monthly_paise": 5_999_900,    # ₹59,999 / month
        "seats_included": 30,
        "msg_overage_paise": 50,       # ₹0.50 / message
        "voice_overage_paise": 400,    # ₹4.00 / minute
        "ai_run_overage_paise": 25,
    },
    "enterprise": {
        "monthly_paise": 15_000_000,   # ₹1,50,000 / month
        "seats_included": 999,
        "msg_overage_paise": 40,
        "voice_overage_paise": 320,
        "ai_run_overage_paise": 20,
    },
}

# Blended unit COGS assumptions from `docs/09` §3 (in paise)
COGS_PAISE = {
    "voice_per_min": 320,       # ₹3.20 / min blended
    "whatsapp_per_msg": 85,     # ₹0.85 / marketing/utility message
    "sms_per_msg": 22,          # ₹0.22 / DLT SMS
    "ai_per_run": 35,           # ₹0.35 / LLM/agent run
    "infra_monthly": 115_000,   # ₹1,150 / mo compute + storage + monitoring share
}


def pricing_for(plan: str) -> dict[str, int]:
    key = (plan or DEFAULT_PLAN).strip().lower()
    return dict(PLAN_PRICING_PAISE.get(key, PLAN_PRICING_PAISE["starter"]))


def subscription_preview(store, tenant: dict, period: str | None = None) -> dict:
    """Compute base SaaS plan + metered overages + 18% GST for `period`."""
    tz = tenant_tz(tenant)
    period = period or period_of(tz=tz)
    plan = (tenant.get("plan") or DEFAULT_PLAN).lower()
    pricing = pricing_for(plan)
    raw_lines = invoice_lines(store, tenant, period)

    rate_map = {
        "whatsapp_out": pricing["msg_overage_paise"],
        "sms_out": pricing["msg_overage_paise"],
        "voice_minutes": pricing["voice_overage_paise"],
        "ai_runs": pricing["ai_run_overage_paise"],
    }

    line_items: list[dict] = [
        {
            "code": f"plan_{plan}",
            "description": f"Realtor Estates {plan.title()} Plan ({period_label(period)})",
            "qty": 1,
            "unit_paise": pricing["monthly_paise"],
            "amount_paise": pricing["monthly_paise"],
            "sac": "998314",
        }
    ]
    overage_paise = 0
    for ln in raw_lines:
        ov = int(ln.get("overage") or 0)
        unit_rate = rate_map.get(ln["meter"], 0)
        amt = ov * unit_rate
        overage_paise += amt
        if ov > 0 and unit_rate > 0:
            line_items.append({
                "code": f"overage_{ln['meter']}",
                "description": f"{ln['label']} overage ({ov:,} {ln['unit']} above {ln['included']:,})",
                "qty": ov,
                "unit_paise": unit_rate,
                "amount_paise": amt,
                "sac": "998314",
            })

    subtotal_paise = pricing["monthly_paise"] + overage_paise
    tenant_state = str((tenant.get("settings") or {}).get("state") or "Maharashtra").strip().lower()
    intra_state = tenant_state in ("maharashtra", "mh", "27")
    if intra_state:
        cgst_paise = int(round(subtotal_paise * 0.09))
        sgst_paise = int(round(subtotal_paise * 0.09))
        igst_paise = 0
    else:
        cgst_paise = 0
        sgst_paise = 0
        igst_paise = int(round(subtotal_paise * 0.18))
    gst_paise = cgst_paise + sgst_paise + igst_paise
    total_paise = subtotal_paise + gst_paise

    return {
        "tenant_id": tenant["id"],
        "plan": plan,
        "period": period,
        "period_label": period_label(period),
        "seats_included": pricing["seats_included"],
        "seats_active": sum(1 for u in store.all("users", tenant["id"]) if u.get("active", True)),
        "base_paise": pricing["monthly_paise"],
        "overage_paise": overage_paise,
        "subtotal_paise": subtotal_paise,
        "tax": {
            "sac": "998314",
            "rate_pct": 18,
            "intra_state": intra_state,
            "cgst_paise": cgst_paise,
            "sgst_paise": sgst_paise,
            "igst_paise": igst_paise,
            "total_gst_paise": gst_paise,
        },
        "total_paise": total_paise,
        "total_inr": round(total_paise / 100.0, 2),
        "line_items": line_items,
        "meter_lines": raw_lines,
    }


def generate_subscription_invoice(store, tenant: dict, period: str | None = None, *, by: str = "") -> tuple[dict, bool]:
    """Idempotently issue a SaaS usage+subscription GST invoice for `(tenant_id, period)`.
    Returns `(invoice_row, already_existed)`."""
    tid = tenant["id"]
    tz = tenant_tz(tenant)
    period = period or period_of(tz=tz)
    billing_key = f"saas:{tid}:{period}"
    for existing in store.all("invoices", tid):
        if existing.get("billing_key") == billing_key:
            return existing, True

    preview = subscription_preview(store, tenant, period)
    seq = len(store.all("invoices", tid)) + 1
    inv_no = f"RE-SAAS-{period.replace('-', '')}-{seq:04d}"
    row = store.insert(
        "invoices",
        tid,
        {
            "kind": "saas_subscription",
            "billing_key": billing_key,
            "invoice_no": inv_no,
            "period": period,
            "period_label": preview["period_label"],
            "plan": preview["plan"],
            "status": "issued" if preview["total_paise"] > 0 else "zero_due",
            "subtotal_paise": preview["subtotal_paise"],
            "gst_paise": preview["tax"]["total_gst_paise"],
            "total_paise": preview["total_paise"],
            "amount_inr": preview["total_inr"],
            "tax": preview["tax"],
            "line_items": preview["line_items"],
            "issued_at": now(),
            "issued_by": by or "billing_engine",
            "usage_fingerprint": fingerprint(tid, period),
        },
        prefix="inv",
    )
    return row, False


def cost_drivers_report(store, tenant: dict, period: str | None = None) -> dict:
    """Compute the Five Weekly Cost Drivers (`docs/09` §4) + Gross Margin (`docs/09` §3/§9)."""
    import statistics

    tid = tenant["id"]
    tz = tenant_tz(tenant)
    period = period or period_of(tz=tz)
    ts = now()
    derived = derive(store, tid, period, tz)

    voice_min = derived["voice_minutes"] + ledger_sum(store, tid, "voice_minutes", period)
    wa_out = derived["whatsapp_out"] + ledger_sum(store, tid, "whatsapp_out", period)
    sms_out = derived["sms_out"] + ledger_sum(store, tid, "sms_out", period)
    ai_runs = derived["ai_runs"] + ledger_sum(store, tid, "ai_runs", period)
    total_msgs = wa_out + sms_out

    contacts = store.all("contacts", tid)
    qualified_stages = {"qualified", "site_visit", "negotiation", "token", "agreement", "registration", "won"}
    qualified_leads = sum(1 for c in contacts if c.get("stage") in qualified_stages and not c.get("erased"))
    deals = store.all("deals", tid)
    closed_deals = sum(1 for d in deals if d.get("stage") in ("registration", "won"))
    active_convs = sum(1 for cv in store.all("conversations", tid) if (cv.get("messages") or []))

    # 1. Voice minutes per qualified lead (target <= 4.2, critical > 6.0)
    v_per_ql = round(voice_min / max(1, qualified_leads), 2)
    v_status = "critical" if v_per_ql > 6.0 else ("watch" if v_per_ql > 4.2 else "healthy")

    # 2. Messages per closed deal (rising means nurture drift)
    m_per_deal = round(total_msgs / max(1, closed_deals), 2)
    m_status = "critical" if m_per_deal > 150 else ("watch" if m_per_deal > 75 else "healthy")

    # 3. Agent run cost per conversation (INR)
    ai_cost_inr = round((ai_runs * COGS_PAISE["ai_per_run"]) / 100.0, 2)
    cost_per_conv = round(ai_cost_inr / max(1, active_convs), 2)
    c_status = "critical" if cost_per_conv > 25.0 else ("watch" if cost_per_conv > 12.0 else "healthy")

    # 4. Human approval queue age (target < 4.0 h, alert > 6.0 h)
    pending_props = [p for p in store.all("proposals", tid) if p.get("status") == "pending"]
    ages_h = [max(0.0, (ts - int(p.get("created_at") or ts)) / 3600.0) for p in pending_props]
    median_age_h = round(statistics.median(ages_h), 2) if ages_h else 0.0
    max_age_h = round(max(ages_h), 2) if ages_h else 0.0
    q_status = "critical" if median_age_h > 6.0 else ("watch" if median_age_h >= 4.0 else "healthy")

    # 5. REDLINE findings per release (target == 0 critical)
    rl_runs = sorted(
        [r for r in store.all("runs", tid) if r.get("type") == "redline"],
        key=lambda r: -int(r.get("created_at") or 0),
    )
    latest_rl = rl_runs[0] if rl_runs else None
    rl_failed = int((latest_rl or {}).get("failed") or 0)
    rl_score = int((latest_rl or {}).get("score") if latest_rl and latest_rl.get("score") is not None else 100)
    rl_status = "critical" if rl_failed > 0 else "healthy"

    # Unit economics (`docs/09` §3)
    plan = (tenant.get("plan") or DEFAULT_PLAN).lower()
    sub = subscription_preview(store, tenant, period)
    # For trial workspaces, show representative Growth plan benchmark alongside actual revenue
    effective_rev_paise = sub["subtotal_paise"] or PLAN_PRICING_PAISE["growth"]["monthly_paise"]
    voice_cogs = voice_min * COGS_PAISE["voice_per_min"]
    wa_cogs = wa_out * COGS_PAISE["whatsapp_per_msg"]
    sms_cogs = sms_out * COGS_PAISE["sms_per_msg"]
    ai_cogs = ai_runs * COGS_PAISE["ai_per_run"]
    infra_cogs = COGS_PAISE["infra_monthly"]
    pg_cogs = int(round(effective_rev_paise * 0.02))
    total_cogs_paise = voice_cogs + wa_cogs + sms_cogs + ai_cogs + infra_cogs + pg_cogs
    gm_paise = effective_rev_paise - total_cogs_paise
    gm_pct = round((gm_paise / max(1, effective_rev_paise)) * 100.0, 1)

    return {
        "period": period,
        "period_label": period_label(period),
        "plan": plan,
        "drivers": [
            {
                "id": "voice_minutes_per_qualified_lead",
                "label": "Voice minutes per qualified lead",
                "value": v_per_ql,
                "unit": "min/lead",
                "target": "<= 4.2",
                "danger_ceiling": 6.0,
                "status": v_status,
                "inputs": {"voice_minutes": voice_min, "qualified_leads": qualified_leads},
            },
            {
                "id": "messages_per_closed_deal",
                "label": "Messages per closed deal",
                "value": m_per_deal,
                "unit": "msgs/deal",
                "target": "<= 75",
                "danger_ceiling": 150,
                "status": m_status,
                "inputs": {"outbound_messages": total_msgs, "closed_deals": closed_deals},
            },
            {
                "id": "agent_run_cost_per_conversation",
                "label": "Agent run cost per conversation",
                "value": cost_per_conv,
                "unit": "INR/conv",
                "target": "<= 12.0",
                "danger_ceiling": 25.0,
                "status": c_status,
                "inputs": {"ai_runs": ai_runs, "ai_cost_inr": ai_cost_inr, "active_conversations": active_convs},
            },
            {
                "id": "approval_queue_age_hours",
                "label": "Human approval queue median age",
                "value": median_age_h,
                "max_hours": max_age_h,
                "unit": "hours",
                "target": "< 4.0",
                "danger_ceiling": 6.0,
                "status": q_status,
                "inputs": {"pending_approvals": len(pending_props)},
            },
            {
                "id": "redline_findings",
                "label": "REDLINE findings (latest run)",
                "value": rl_failed,
                "score": rl_score,
                "unit": "findings",
                "target": "0",
                "danger_ceiling": 1,
                "status": rl_status,
                "inputs": {"has_run": latest_rl is not None},
            },
        ],
        "unit_economics": {
            "plan": plan,
            "benchmark_mode": sub["subtotal_paise"] == 0,
            "revenue_inr": round(effective_rev_paise / 100.0, 2),
            "actual_billed_subtotal_inr": round(sub["subtotal_paise"] / 100.0, 2),
            "cogs_breakdown_inr": {
                "voice": round(voice_cogs / 100.0, 2),
                "whatsapp": round(wa_cogs / 100.0, 2),
                "sms": round(sms_cogs / 100.0, 2),
                "ai_runs": round(ai_cogs / 100.0, 2),
                "infra_share": round(infra_cogs / 100.0, 2),
                "payment_gateway_2pct": round(pg_cogs / 100.0, 2),
            },
            "total_cogs_inr": round(total_cogs_paise / 100.0, 2),
            "gross_margin_inr": round(gm_paise / 100.0, 2),
            "gross_margin_pct": gm_pct,
            "meets_60pct_gate": gm_pct >= 60.0,
        },
    }


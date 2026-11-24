"""
The automation runtime — triggers that fire, steps that run, and a record of both.

Why this module exists: `docs/18` found that the defensible product is the deal's
journey, and `PLANS.md` G1 found the embarrassing hole — the CRM could *describe*
an automation ("new portal lead → 5-min WhatsApp + AI call") but nothing ever ran
one. The PRD's number-one job, "answer every enquiry in 90 seconds without a human
waking up", was clocked and never delivered.

Three design decisions, each with a cost we accept:

1. **Triggers are evaluated from the data, not pushed by an event bus.** A scan
   asks "which leads were created in the last five minutes and have no outbound
   touch?" That is slower than an event and far more robust: a dropped webhook, a
   restarted process or a missed tick cannot lose work, because the work is
   re-derivable from the rows. Cost: a scan interval bounds latency (15 s here,
   against a 300 s SLA).
2. **Idempotency is a key, not a lock.** Every run carries
   `automation_id:subject_id:occurrence` and is refused if that key already
   exists. Two workers, a retried request and a replay all collapse to one run —
   which is what makes the tick safe to run on more than one instance.
3. **Compliance gates run before the step, not inside the transport.** Consent,
   quiet hours, the tenant kill switch, DND and a per-contact frequency cap are
   evaluated by the runner; a blocked step is recorded with its reason and, where
   the block is temporal (quiet hours), *deferred* rather than dropped.

What this deliberately is not: a general workflow engine. There is no loop, no
branch, no user-authored expression. Steps are a short allowlist of typed actions
with declarative parameters, because every step type is a compliance decision and
a workflow DSL is a way to hide one.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Callable, Iterable

from . import meters as ut

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------
TRIGGERS: dict[str, dict] = {
    "lead.created": {
        "label": "A new lead arrives",
        "lookback_sec": 300,
        "why": "The 90-second promise: the first outbound touch is what wins the deal.",
    },
    "lead.unanswered": {
        "label": "A lead has had no outbound touch",
        "lookback_sec": 600,
        "why": "Speed-to-lead escalation when the first window has already been missed.",
    },
    "lead.stale": {
        "label": "A lead has gone quiet",
        "lookback_sec": 14 * 86400,
        "why": "Re-engagement is the cheapest pipeline in a brokerage.",
    },
    "visit.confirmed": {
        "label": "A site visit is confirmed",
        "lookback_sec": 3600,
        "why": "Documents before the visit; a land buyer's first question is the title.",
    },
    "visit.reminder_24h": {
        "label": "A visit is due in the next 24 hours",
        "lookback_sec": 86400,
        "why": "Show-rate is decided the evening before, not on the morning.",
    },
    "visit.no_show": {
        "label": "A buyer did not turn up",
        "lookback_sec": 7 * 86400,
        "why": "A no-show needs a human apology and a new slot within hours.",
    },
    "unit.hold_expiring": {
        "label": "A unit hold expires within 24 hours",
        "lookback_sec": 86400,
        "why": "An expiring hold is inventory about to be re-promised.",
    },
    "call.missed": {
        "label": "Someone rang us and nobody picked up",
        "lookback_sec": 3600,
        "why": "A buyer who dialled is the hottest lead in the system: they chose the phone over a form.",
    },
    "deal.closed": {
        "label": "A deal closed two days ago",
        "lookback_sec": 30 * 86400,
        "why": "A review asked while the keys are still new is asked at peak goodwill; at possession it is admin.",
    },
}

STEP_TYPES: dict[str, dict] = {
    "send_whatsapp": {"label": "Send WhatsApp", "outbound": True, "channel": "whatsapp", "needs": ("text",)},
    "send_sms": {"label": "Send SMS", "outbound": True, "channel": "sms", "needs": ("text",)},
    "voice_call": {"label": "Place AI voice call", "outbound": True, "channel": "voice", "needs": ("text",)},
    "send_document": {"label": "Send document pack", "outbound": True, "channel": "whatsapp", "needs": ("document",)},
    "create_task": {"label": "Create a task for a human", "outbound": False, "needs": ("title",)},
    "assign_owner": {"label": "Assign an owner (round-robin)", "outbound": False, "needs": ()},
    "notify_team": {"label": "Notify the team", "outbound": False, "needs": ("text",)},
    "set_stage": {"label": "Move the deal stage", "outbound": False, "needs": ("stage",)},
    "wait": {"label": "Wait / multi-day delay", "outbound": False, "needs": ("duration",)},
}

RUN_STATUSES = ("pending", "ok", "blocked", "deferred", "dead", "skipped")

DEFAULT_TICK_SECONDS = 15
MAX_ATTEMPTS = 4
BACKOFF_SEC = (30, 120, 600, 1800)
FREQUENCY_CAP_PER_DAY = 6      # outbound messages to one contact in 24 h
CAP_WINDOW_SEC = 86400
QUIET_HOUR_DEFER_SEC = 300
TICK_BUDGET_SEC = 10.0         # a tick is a request, not a batch job


class AutomationError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# Time-of-day windows, evaluated in the tenant's timezone
# ---------------------------------------------------------------------------
def _hhmm(raw: Any, default_min: int) -> int:
    try:
        text = str(raw).strip()
        hour, minute = text.split(":")[:2]
        return (int(hour) % 24) * 60 + (int(minute) % 60)
    except Exception:
        return default_min


def tenant_clock(tenant: dict, at: int) -> tuple[int, int]:
    """(hour, minute) in the tenant's timezone for an epoch second."""
    tz_name = ((tenant or {}).get("settings") or {}).get("timezone") or "Asia/Kolkata"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        local = datetime.fromtimestamp(at, ZoneInfo(tz_name))
        return local.hour, local.minute
    except Exception:
        lt = time.localtime(at)
        return lt.tm_hour, lt.tm_min


def quiet_window(tenant: dict) -> tuple[int, int, str]:
    settings = (tenant or {}).get("settings") or {}
    start_raw, end_raw = (settings.get("quiet_hours") or ["21:00", "09:00"])[:2]
    return _hhmm(start_raw, 21 * 60), _hhmm(end_raw, 9 * 60), f"{start_raw}–{end_raw}"


def in_quiet_hours(tenant: dict, at: int) -> tuple[bool, str]:
    start, end, window = quiet_window(tenant)
    hour, minute = tenant_clock(tenant, at)
    mins = hour * 60 + minute
    inside = (mins >= start or mins < end) if start > end else (start <= mins < end)
    return inside, f"{window} {(tenant.get('settings') or {}).get('timezone', 'Asia/Kolkata')} (now {hour:02d}:{minute:02d})"


def seconds_until_window_opens(tenant: dict, at: int) -> int:
    """How long until quiet hours end, so a blocked send can be deferred not lost."""
    start, end, _ = quiet_window(tenant)
    hour, minute = tenant_clock(tenant, at)
    mins = hour * 60 + minute
    if not (mins >= start or mins < end):
        return 0
    delta = (end - mins) % (24 * 60) or (24 * 60)
    return max(60, delta * 60)


MAX_WAIT_SECONDS = 90 * 86400   # 90-day ceiling on declarative drip waits


def parse_wait_seconds(step: dict) -> int:
    """Parse declarative `duration` (`'2d'`, `'48h'`, `'30m'`, `'120s'`, or integer minutes)
    into bounded delay seconds (`60 .. 7,776,000`)."""
    raw = step.get("duration")
    if raw is None or str(raw).strip() == "":
        raise AutomationError("STEP_INVALID", "wait step requires 'duration' (e.g. '2d', '12h', '30m').")
    text = str(raw).strip().lower()
    mult = 60
    num_str = text
    if text.endswith("d"):
        mult, num_str = 86400, text[:-1]
    elif text.endswith("h"):
        mult, num_str = 3600, text[:-1]
    elif text.endswith("m"):
        mult, num_str = 60, text[:-1]
    elif text.endswith("s"):
        mult, num_str = 1, text[:-1]
    try:
        val = float(num_str.strip())
    except ValueError:
        raise AutomationError("STEP_INVALID", f"Invalid wait duration '{raw}'. Use e.g. '2d', '24h', or '30m'.")
    sec_val = int(round(val * mult))
    if sec_val < 60 or sec_val > MAX_WAIT_SECONDS:
        raise AutomationError(
            "STEP_INVALID",
            f"Wait duration must be between 60s and 90d (got {sec_val}s).",
        )
    return sec_val


# ---------------------------------------------------------------------------
# Trigger evaluation — pure functions over rows
# ---------------------------------------------------------------------------
def _age_ok(epoch: Any, at: int, lookback: int) -> bool:
    try:
        value = int(epoch)
    except (TypeError, ValueError):
        return False
    return at - lookback <= value <= at + 60


def evaluate(trigger: str, rows: dict, tenant: dict, at: int) -> list[dict]:
    """Return the subjects a trigger currently fires for.

    Every subject is `{subject_id, kind, occurrence, context}`. `occurrence` is the
    part of the idempotency key that makes "one reminder per visit" different from
    "one reminder per day for the same visit".
    """
    spec = TRIGGERS.get(trigger)
    if not spec:
        return []
    lookback = spec["lookback_sec"]
    out: list[dict] = []

    if trigger == "lead.created":
        for contact in rows.get("contacts", []):
            if not _age_ok(contact.get("created_at"), at, lookback):
                continue
            out.append({"subject_id": contact["id"], "kind": "contact", "occurrence": "first-touch",
                        "context": {"contact": contact}})

    elif trigger == "lead.unanswered":
        for contact in rows.get("contacts", []):
            if contact.get("first_response_at"):
                continue
            created = int(contact.get("created_at") or 0)
            if created and at - created >= lookback and at - created <= lookback * 6:
                out.append({"subject_id": contact["id"], "kind": "contact", "occurrence": "escalate",
                            "context": {"contact": contact}})

    elif trigger == "lead.stale":
        for contact in rows.get("contacts", []):
            last = int(contact.get("last_activity_at") or contact.get("created_at") or 0)
            if not last or contact.get("stage") in ("won", "lost"):
                continue
            if at - last >= lookback:
                week = time.strftime("%G-W%V", time.gmtime(at))
                out.append({"subject_id": contact["id"], "kind": "contact", "occurrence": f"stale-{week}",
                            "context": {"contact": contact}})

    elif trigger == "visit.confirmed":
        for visit in rows.get("site_visits", []):
            if visit.get("status") != "confirmed":
                continue
            stamp = int((visit.get("history") or [{}])[-1].get("at") or visit.get("updated_at") or visit.get("created_at") or 0)
            if _age_ok(stamp, at, lookback):
                out.append({"subject_id": visit["id"], "kind": "site_visit", "occurrence": "confirmed",
                            "context": {"visit": visit}})

    elif trigger == "visit.reminder_24h":
        for visit in rows.get("site_visits", []):
            if visit.get("status") not in ("scheduled", "confirmed"):
                continue
            when = int(visit.get("scheduled_at") or 0)
            if when and 0 <= when - at <= 86400:
                day = time.strftime("%Y-%m-%d", time.gmtime(when))
                out.append({"subject_id": visit["id"], "kind": "site_visit", "occurrence": f"reminder-{day}",
                            "context": {"visit": visit}})

    elif trigger == "visit.no_show":
        for visit in rows.get("site_visits", []):
            if visit.get("status") != "no_show":
                continue
            stamp = int(visit.get("updated_at") or visit.get("created_at") or 0)
            if _age_ok(stamp, at, lookback):
                out.append({"subject_id": visit["id"], "kind": "site_visit", "occurrence": "no-show",
                            "context": {"visit": visit}})

    elif trigger == "call.missed":
        # A missed call is only missed if nobody called back: the moment an
        # outbound message exists after it, the recovery is in hand and a second
        # "sorry we missed you" is noise.
        replied: dict[str, int] = {}
        for conv in rows.get("conversations", []):
            for msg in conv.get("messages") or []:
                if msg.get("dir") == "out":
                    cid = conv.get("contact_id", "")
                    replied[cid] = max(replied.get(cid, 0), int(msg.get("at") or 0))
        contacts = {c["id"]: c for c in rows.get("contacts", [])}
        for activity in rows.get("activities", []):
            if activity.get("type") != "call" or activity.get("direction") != "in":
                continue
            if activity.get("outcome") not in ("missed", "no_answer", "voicemail"):
                continue
            rang_at = int(activity.get("at") or 0)
            if not _age_ok(rang_at, at, lookback):
                continue
            contact = contacts.get(activity.get("contact_id", ""))
            if not contact or replied.get(contact["id"], 0) >= rang_at:
                continue
            out.append({"subject_id": activity["id"], "kind": "call",
                        "occurrence": f"call-{activity['id']}",
                        "context": {"contact": contact, "call": activity}})

    elif trigger == "deal.closed":
        ready_after = 2 * 86400   # ask once the paperwork is done, not on the day
        for deal in rows.get("deals", []):
            if deal.get("stage") != "won":
                continue
            closed = int(deal.get("closed_at") or deal.get("updated_at") or 0)
            if not closed:
                continue
            age = at - closed
            if not (ready_after <= age <= lookback):
                continue
            contact = next((c for c in rows.get("contacts", []) if c["id"] == deal.get("contact_id")), None)
            out.append({"subject_id": deal["id"], "kind": "deal",
                        "occurrence": f"closed-{deal['id']}",
                        "context": {"contact": contact, "deal": deal}})

    elif trigger == "unit.hold_expiring":
        for unit in rows.get("units", []):
            block = unit.get("block") or {}
            until = int(block.get("until") or 0)
            if unit.get("status") == "blocked" and until and 0 <= until - at <= 86400:
                out.append({"subject_id": unit["id"], "kind": "unit", "occurrence": f"hold-{until}",
                            "context": {"unit": unit}})

    return out


def idempotency_key(automation_id: str, subject_id: str, occurrence: str) -> str:
    return f"{automation_id}:{subject_id}:{occurrence}"


# ---------------------------------------------------------------------------
# Run construction
# ---------------------------------------------------------------------------
def new_run(automation: dict, subject: dict, at: int) -> dict:
    return {
        "automation_id": automation["id"],
        "automation_name": automation.get("name", ""),
        "trigger": automation.get("trigger", ""),
        "subject_id": subject["subject_id"],
        "subject_kind": subject["kind"],
        "occurrence": subject["occurrence"],
        "idempotency_key": idempotency_key(automation["id"], subject["subject_id"], subject["occurrence"]),
        "status": "pending",
        "step_index": 0,
        "attempts": 0,
        "next_at": at,
        "created_at": at,
        "last_error": "",
        "trace": [],
    }


def gate(step: dict, tenant: dict, contact: dict | None, at: int) -> tuple[str, str]:
    """Compliance gate for one step. Returns (verdict, reason).

    Verdicts: `allow`, `defer` (temporal — quiet hours), `block` (never, for this
    contact), `skip` (non-outbound step that has nothing to do).
    """
    meta = STEP_TYPES.get(step.get("type"))
    if not meta:
        return "block", f"Unknown step type '{step.get('type')}'"
    if (tenant or {}).get("kill_switch"):
        return "block", "Tenant kill switch is on"
    if not meta["outbound"]:
        return "allow", ""
    if contact is None:
        return "block", "Outbound step has no contact"
    consent = (contact.get("consent") or {})
    if consent.get("status") != "granted":
        return "block", f"Consent is {consent.get('status') or 'missing'}"
    if consent.get("dnd"):
        return "block", "Contact is on DND"
    in_quiet, window = in_quiet_hours(tenant, at)
    if in_quiet:
        return "defer", f"Quiet hours ({window})"
    return "allow", ""


def _inr(value: Any) -> str:
    """Indian grouping — ₹1,10,00,000, not ₹11,000,000. Labels only, never math."""
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return ""
    if amount <= 0:
        return ""
    text = str(amount)
    head, tail = text[:-3], text[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


def render(template: str, context: dict) -> str:
    """Narrow substitution — `{{contact.name}}`, `{{visit.when}}`. No expressions."""
    contact = context.get("contact") or {}
    visit = context.get("visit") or {}
    unit = context.get("unit") or {}
    values = {
        "contact.name": str(contact.get("name") or "there"),
        "contact.city": str(contact.get("city") or ""),
        "contact.requirement": str(contact.get("requirement") or ""),
        "visit.when": time.strftime("%d %b %H:%M", time.gmtime(int(visit.get("scheduled_at") or 0)))
        if visit.get("scheduled_at") else "your slot",
        "visit.status": str(visit.get("status") or ""),
        "unit.no": str(unit.get("unit_no") or ""),
        "unit.holds_until": time.strftime("%d %b %H:%M", time.gmtime(int((unit.get("block") or {}).get("until") or 0)))
        if (unit.get("block") or {}).get("until") else "",
        "call.when": time.strftime("%d %b %H:%M", time.gmtime(int((context.get("call") or {}).get("at") or 0)))
        if (context.get("call") or {}).get("at") else "just now",
        "deal.value": _inr((context.get("deal") or {}).get("value")),
        "tenant.name": str(context.get("tenant_name") or ""),
    }
    text = str(template or "")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text[:900]


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------
class Runtime:
    """Executes due runs against a store. No threads, no I/O — the caller ticks it."""

    def __init__(
        self,
        store: Any,
        now_fn: Callable[[], int] | None = None,
        *,
        worker_id: str = "",
        lease_ttl_sec: int = 30,
        after_effect_hook: Callable[[dict, int, dict], None] | None = None,
    ):
        self.store = store
        self.now = now_fn or (lambda: int(time.time()))
        self.worker_id = worker_id or ("wrk_" + uuid.uuid4().hex[:8])
        self.lease_ttl_sec = max(5, int(lease_ttl_sec))
        self.after_effect_hook = after_effect_hook

    # -- helpers ---------------------------------------------------------
    def _rows(self, tenant_id: str) -> dict:
        return {
            "contacts": self.store.all("contacts", tenant_id),
            "site_visits": self.store.all("site_visits", tenant_id),
            "units": self.store.all("units", tenant_id),
            "deals": self.store.all("deals", tenant_id),
            "activities": self.store.all("activities", tenant_id),
            "conversations": self.store.all("conversations", tenant_id),
        }

    def _refresh(self, tenant_id: str) -> None:
        """Re-read the rows a tick decides on.

        Only relevant when the store is shared: writes reach Postgres, reads come
        from a boot-time snapshot, so without this a second process cannot see
        that an occurrence was already claimed. Stores without a backend (a single
        process owning a JSON file) are authoritative on their own.
        """
        colls = ("automations", "runs", "contacts", "site_visits", "units", "deals",
                 "activities", "conversations", "tasks")
        many = getattr(self.store, "reload_many", None)
        try:
            if callable(many):
                many(colls, tenant_id)       # one query, not eight
                return
            reload = getattr(self.store, "reload", None)
            if not callable(reload):
                return
            for coll in colls:
                reload(coll, tenant_id)
        except Exception:  # a refresh failure must not silently disable triage
            return

    def _claim_run(self, tenant_id: str, run: dict) -> dict | None:
        """Insert the run, or report that somebody else already owns the occurrence.

        The in-memory key check in `plan` catches the ordinary repeat. This catches
        the race it cannot: two processes, one occurrence, one `db/008` row.
        """
        try:
            return self.store.insert("runs", tenant_id, {**run, "type": "automation_run"},
                                     prefix="run")
        except Exception as exc:
            text = f"{exc.__class__.__name__} {exc}".lower()
            if "unique" in text or "duplicate" in text or "idempotency" in text:
                return None
            raise

    def _existing_keys(self, tenant_id: str) -> set[str]:
        return {r.get("idempotency_key", "") for r in self.store.all("runs", tenant_id)}

    def _contact(self, tenant_id: str, contact_id: str) -> dict | None:
        return self.store.get("contacts", tenant_id, contact_id)

    def _outbound_recent(self, tenant_id: str, contact_id: str, since: int) -> int:
        count = 0
        for conv in self.store.all("conversations", tenant_id):
            if conv.get("contact_id") != contact_id:
                continue
            for msg in conv.get("messages") or []:
                if msg.get("dir") == "out" and int(msg.get("at") or 0) >= since:
                    count += 1
        return count

    # -- phases ----------------------------------------------------------
    def plan(self, tenant_id: str, at: int | None = None, automation_id: str = "") -> list[dict]:
        """Materialise new runs for every enabled automation whose trigger fires."""
        at = at or self.now()
        self._refresh(tenant_id)
        tenant = self.store.get("tenants", tenant_id, tenant_id) or {}
        rows = self._rows(tenant_id)
        seen = self._existing_keys(tenant_id)
        created: list[dict] = []
        for automation in self.store.all("automations", tenant_id):
            if not automation.get("enabled"):
                continue
            if automation_id and automation["id"] != automation_id:
                continue
            for subject in evaluate(automation.get("trigger", ""), rows, tenant, at):
                run = new_run(automation, subject, at)
                if run["idempotency_key"] in seen:
                    continue
                seen.add(run["idempotency_key"])
                run["context"] = subject.get("context", {})
                stored = self._claim_run(tenant_id, run)
                if stored:
                    created.append(stored)
        return created

    def due(self, tenant_id: str, at: int | None = None, limit: int = 200) -> list[dict]:
        at = at or self.now()
        self._refresh(tenant_id)
        rows = [
            r for r in self.store.all("runs", tenant_id)
            if r.get("type") == "automation_run"
            and r.get("status") in ("pending", "deferred")
            and int(r.get("next_at") or 0) <= at
            and (
                int(r.get("lease_until") or 0) <= at
                or r.get("lease_worker") == self.worker_id
            )
        ]
        rows.sort(key=lambda r: (int(r.get("next_at") or 0), str(r.get("id"))))
        return rows[: int(limit)]

    def execute(self, run: dict, tenant: dict, at: int | None = None) -> dict:
        """Advance one run by one step. Returns the updated run."""
        at = at or self.now()
        tenant_id = run["tenant_id"]
        fresh = self.store.get("runs", tenant_id, run["id"])
        if fresh:
            run = fresh
        if int(run.get("lease_until") or 0) > at and run.get("lease_worker") not in ("", None, self.worker_id):
            return run
        run = self.store.update("runs", tenant_id, run["id"], {
            "lease_worker": self.worker_id,
            "lease_until": at + self.lease_ttl_sec,
        }) or run

        automation = self.store.get("automations", tenant_id, run.get("automation_id", "")) or {}
        steps = automation.get("steps") or []
        trace = list(run.get("trace") or [])

        if not automation.get("enabled"):
            return self._settle(run, "skipped", at, "Automation disabled", trace)

        index = int(run.get("step_index") or 0)
        if index >= len(steps):
            return self._settle(run, "ok", at, "", trace)

        step = steps[index] if isinstance(steps[index], dict) else {"type": steps[index]}
        context = dict(run.get("context") or {})
        contact = None
        if run.get("subject_kind") == "contact":
            contact = self._contact(tenant_id, run["subject_id"])
        elif run.get("subject_kind") == "site_visit":
            visit = next((v for v in self.store.all("site_visits", tenant_id)
                          if v["id"] == run["subject_id"]), None)
            if visit:
                context.setdefault("visit", visit)
                contact = self._contact(tenant_id, visit.get("contact_id", ""))
        elif run.get("subject_kind") in ("call", "deal"):
            contact = self._contact(tenant_id, (context.get("contact") or {}).get("id", ""))
        elif run.get("subject_kind") == "unit":
            unit = next((u for u in self.store.all("units", tenant_id)
                         if u["id"] == run["subject_id"]), None)
            if unit:
                context.setdefault("unit", unit)
                block = unit.get("block") or {}
                contact = self._contact(tenant_id, block.get("contact_id", ""))
        context["contact"] = context.get("contact") or contact or {}
        context["tenant_name"] = tenant.get("name", "")

        recent = self._outbound_recent(tenant_id, (contact or {}).get("id", ""), at - CAP_WINDOW_SEC) \
            if contact else 0
        verdict, reason = gate(step, tenant, contact, at)
        if verdict == "block":
            trace.append({"step": index, "type": step.get("type"), "at": at,
                          "verdict": "block", "reason": reason})
            return self._settle(run, "blocked", at, reason, trace)
        if verdict == "defer":
            trace.append({"step": index, "type": step.get("type"), "at": at,
                          "verdict": "defer", "reason": reason})
            next_at = at + min(seconds_until_window_opens(tenant, at) or QUIET_HOUR_DEFER_SEC,
                               QUIET_HOUR_DEFER_SEC)
            return self.store.update("runs", tenant_id, run["id"], {
                "status": "deferred", "next_at": next_at, "trace": trace,
                "last_error": reason, "updated_at": at,
            }) or run

        # frequency cap: outbound, per contact, per day
        meta = STEP_TYPES.get(step.get("type")) or {}
        if meta.get("outbound") and recent >= FREQUENCY_CAP_PER_DAY:
            reason = f"Frequency cap reached ({recent} outbound messages in 24 h)"
            trace.append({"step": index, "type": step.get("type"), "at": at,
                          "verdict": "defer", "reason": reason})
            return self.store.update("runs", tenant_id, run["id"], {
                "status": "deferred", "next_at": at + 3600, "trace": trace,
                "last_error": reason, "updated_at": at,
            }) or run

        step_key = f"{run['id']}:step:{index}"
        try:
            effect = self._apply(step, meta, context, tenant, contact, run, at, step_key=step_key)
        except AutomationError as exc:
            return self._fail(run, tenant, at, exc.message, trace)
        except Exception as exc:  # a step must never take the tick down
            return self._fail(run, tenant, at, f"{type(exc).__name__}: {exc}", trace)

        if callable(self.after_effect_hook):
            self.after_effect_hook(run, index, effect)

        trace.append({"step": index, "type": step.get("type"), "at": at,
                      "verdict": "allow", "effect": effect, "step_key": step_key})
        next_index = index + 1
        status = "ok" if next_index >= len(steps) else "pending"
        next_at = int(effect.get("resume_at") or at) if (status == "pending" and isinstance(effect, dict)) else at
        return self.store.update("runs", tenant_id, run["id"], {
            "status": status, "step_index": next_index, "attempts": 0,
            "next_at": next_at, "trace": trace, "updated_at": at, "last_error": "",
            "last_effect": effect, "lease_until": 0, "lease_worker": "",
        }) or run

    def advance(self, run: dict, tenant: dict, at: int | None = None, max_steps: int = 20) -> dict:
        """Run steps until the run settles, defers, parks on a multi-day wait, blocks or dies.

        A tick drains a run rather than advancing it one step: the whole point of
        the runtime is latency, and a three-step playbook that advances one step
        per 15-second tick would miss the five-minute SLA it exists to protect.
        """
        at = at or self.now()
        for _ in range(int(max_steps)):
            run = self.execute(run, tenant, at)
            if run.get("status") in ("ok", "blocked", "dead", "skipped", "deferred"):
                break
            if int(run.get("next_at") or 0) > at:
                break
        return run

    def _settle(self, run: dict, status: str, at: int, reason: str, trace: list) -> dict:
        updated = self.store.update("runs", run["tenant_id"], run["id"], {
            "status": status, "last_error": reason, "trace": trace, "updated_at": at,
            "lease_until": 0, "lease_worker": "",
        })
        return updated or run

    def _fail(self, run: dict, tenant: dict, at: int, reason: str, trace: list) -> dict:
        attempts = int(run.get("attempts") or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            trace.append({"step": run.get("step_index"), "at": at, "verdict": "dead", "reason": reason})
            return self.store.update("runs", run["tenant_id"], run["id"], {
                "status": "dead", "attempts": attempts, "last_error": reason,
                "trace": trace, "updated_at": at, "lease_until": 0, "lease_worker": "",
            }) or run
        backoff = BACKOFF_SEC[min(attempts, len(BACKOFF_SEC)) - 1]
        trace.append({"step": run.get("step_index"), "at": at, "verdict": "retry",
                      "reason": reason, "in_sec": backoff})
        return self.store.update("runs", run["tenant_id"], run["id"], {
            "status": "pending", "attempts": attempts, "next_at": at + backoff,
            "last_error": reason, "trace": trace, "updated_at": at,
            "lease_until": 0, "lease_worker": "",
        }) or run

    # -- effects ---------------------------------------------------------
    def _apply(self, step: dict, meta: dict, context: dict, tenant: dict,
               contact: dict | None, run: dict, at: int, *, step_key: str = "") -> dict:
        tenant_id = run["tenant_id"]
        kind = step.get("type")
        step_key = step_key or f"{run['id']}:step:{int(run.get('step_index') or 0)}"
        det_msg_id = "msg_" + hashlib.sha256(step_key.encode("utf-8")).hexdigest()[:10]

        if kind in ("send_whatsapp", "send_sms", "voice_call", "send_document"):
            text = render(step.get("text") or step.get("template") or "", context)
            if not text:
                raise AutomationError("STEP_INVALID", f"{kind} needs text or a template")
            if kind == "send_document":
                doc = str(step.get("document") or "document pack")
                text = f"[{doc}] " + text
            channel = meta["channel"]

            # Crash-safe step idempotency (Sprint S4): if an earlier worker committed
            # the outbound message and died before advancing `run.step_index`, never
            # double-send or double-bill on retry/takeover.
            conv = next((c for c in self.store.all("conversations", tenant_id)
                         if c.get("contact_id") == (contact or {}).get("id")), None)
            if conv:
                already = next(
                    (m for m in (conv.get("messages") or [])
                     if m.get("step_key") == step_key or m.get("id") == det_msg_id),
                    None,
                )
                if already:
                    return {
                        "channel": channel,
                        "message_id": already["id"],
                        "guardrail": already.get("guardrail", "allow"),
                        "deduplicated": True,
                    }

            # The autonomous path spends the same allowance as the manual one. If a
            # plan is out of messages, the run is *held* with a reason instead of
            # sending what nobody has paid for (docs/29).
            meter = {"whatsapp": "whatsapp_out", "sms": "sms_out"}.get(channel)
            if meter:
                try:
                    ut.enforce(self.store, tenant, meter)
                except ut.QuotaExceeded as exc:
                    raise AutomationError("QUOTA_EXCEEDED", exc.message,
                                          {"meter": exc.meter, "used": exc.used, "cap": exc.cap,
                                           "resets_at": exc.resets_at})
            guard = self._guard(text)
            if guard["action"] == "block":
                raise AutomationError("GUARDRAIL_BLOCKED", "Message blocked by the output guardrail",
                                      {"findings": guard.get("findings")})
            message = {
                "id": det_msg_id,
                "step_key": step_key,
                "dir": "out", "channel": channel, "text": text, "at": at,
                "ai": True, "automation": True, "run_id": run["id"],
                "status": "sent", "guardrail": guard["action"],
                "actor": f"Automation · {run.get('automation_name', '')}"[:80],
            }
            if conv:
                self.store.update("conversations", tenant_id, conv["id"], {
                    "messages": list(conv.get("messages") or []) + [message],
                    "last_at": at, "channel": channel,
                })
            else:
                self.store.insert("conversations", tenant_id, {
                    "contact_id": contact["id"], "channel": channel, "unread": 0,
                    "messages": [message], "last_at": at,
                }, prefix="cnv")
            patch: dict = {"last_activity_at": at}
            if not contact.get("first_response_at"):
                patch["first_response_at"] = at
                patch["responded_by"] = "automation"
                patch["responded_channel"] = channel
            self.store.update("contacts", tenant_id, contact["id"], patch)
            self.store.insert("activities", tenant_id, {
                "contact_id": contact["id"], "type": channel, "text": text[:280],
                "at": at, "actor": f"Automation · {run.get('automation_name', '')}"[:80],
                "step_key": step_key,
                **({"duration_sec": 60} if channel == "voice" else {}),
            }, prefix="act")
            from . import realtime as rt
            rt.HUB.publish(
                tenant_id,
                "inbox.message",
                contact["id"],
                {"channel": channel, "dir": "out", "ai": True, "automation": True},
                collections=("conversations", "contacts"),
            )
            return {"channel": channel, "message_id": message["id"],
                    "guardrail": guard["action"],
                    "first_response": "first_response_at" in patch}

        if kind == "create_task":
            existing_task = next(
                (t for t in self.store.all("tasks", tenant_id) if t.get("step_key") == step_key),
                None,
            )
            if existing_task:
                return {"task_id": existing_task["id"], "deduplicated": True}
            task = self.store.insert("tasks", tenant_id, {
                "title": render(step.get("title") or "Follow up", context)[:120],
                "due_at": at + int(step.get("due_min", 120)) * 60,
                "owner_id": (contact or {}).get("owner_id") or "",
                "done": False,
                "priority": str(step.get("priority") or "medium"),
                "contact_id": (contact or {}).get("id", ""),
                "source_run": run["id"],
                "step_key": step_key,
            }, prefix="tsk")
            return {"task_id": task["id"]}

        if kind == "assign_owner":
            if not contact:
                raise AutomationError("STEP_INVALID", "assign_owner needs a contact")
            users = [u for u in self.store.all("users", tenant_id) if u.get("role") == "agent" and u.get("active")]
            if not users:
                raise AutomationError("NO_AGENT", "No active agents to assign")
            load: dict[str, int] = {u["id"]: 0 for u in users}
            for c in self.store.all("contacts", tenant_id):
                if c.get("owner_id") in load and c.get("stage") not in ("won", "lost"):
                    load[c["owner_id"]] += 1
            chosen = min(users, key=lambda u: (load.get(u["id"], 0), str(u["id"])))
            self.store.update("contacts", tenant_id, contact["id"], {"owner_id": chosen["id"]})
            return {"owner_id": chosen["id"], "owner_name": chosen.get("name", "")}

        if kind == "notify_team":
            existing_act = next(
                (a for a in self.store.all("activities", tenant_id) if a.get("step_key") == step_key),
                None,
            )
            if existing_act:
                return {"notified": True, "deduplicated": True}
            text = render(step.get("text") or "", context)
            self.store.insert("activities", tenant_id, {
                "contact_id": (contact or {}).get("id", ""), "type": "note",
                "text": ("[automation] " + text)[:280], "at": at,
                "actor": f"Automation · {run.get('automation_name', '')}"[:80],
                "step_key": step_key,
            }, prefix="act")
            return {"notified": True, "text": text[:120]}

        if kind == "set_stage":
            if not contact:
                raise AutomationError("STEP_INVALID", "set_stage needs a contact")
            stage = str(step.get("stage") or "").strip()
            if stage not in ("new", "contacted", "qualified", "site_visit", "negotiation", "token",
                             "agreement", "registration", "won", "lost"):
                raise AutomationError("STEP_INVALID", f"Unknown stage '{stage}'")
            self.store.update("contacts", tenant_id, contact["id"], {"stage": stage, "last_activity_at": at})
            return {"stage": stage}

        if kind == "wait":
            delay_sec = parse_wait_seconds(step)
            resume_at = at + delay_sec
            if step.get("align_to_window", True):
                in_q, _ = in_quiet_hours(tenant, resume_at)
                if in_q:
                    resume_at += seconds_until_window_opens(tenant, resume_at)
            return {
                "waited_sec": delay_sec,
                "resume_at": resume_at,
                "duration": str(step.get("duration") or ""),
                "aligned_to_window": resume_at != (at + delay_sec),
            }

        raise AutomationError("STEP_INVALID", f"Unsupported step type '{kind}'")

    def _guard(self, text: str) -> dict:
        try:
            from . import security as sec

            return sec.inspect_text(text)
        except Exception:
            return {"action": "allow", "findings": []}

    # -- the tick --------------------------------------------------------
    def tick(self, tenant_id: str, at: int | None = None, limit: int = 200,
             dry_run: bool = False, budget_sec: float = TICK_BUDGET_SEC) -> dict:
        """Plan, then execute every due run. Returns a summary a UI can render.

        The tick has a **wall-clock budget** as well as a count limit. Deferred runs
        come back all at once — quiet hours end, and every hold reminder in the
        backlog becomes due in the same second — and a tick that then writes a few
        hundred runs one round trip at a time stops being a request and becomes a
        time-out. Work left over is picked up by the next tick, which is safe
        precisely because runs are idempotent and durable.
        """
        at = at or self.now()
        tenant = self.store.get("tenants", tenant_id, tenant_id) or {}
        planned = [] if dry_run else self.plan(tenant_id, at)
        due = self.due(tenant_id, at, limit=limit)
        counts = {"planned": len(planned), "due": len(due), "ok": 0, "blocked": 0,
                  "deferred": 0, "dead": 0, "pending": 0, "skipped": 0}
        results = []
        if dry_run:
            for run in due:
                results.append({"run_id": run["id"], "automation": run.get("automation_name"),
                                "subject_id": run.get("subject_id"), "status": "would-run"})
            return {"at": at, "dry_run": True, "counts": counts, "results": results}

        started = time.monotonic()
        carried = 0
        for position, run in enumerate(due):
            if budget_sec and position and time.monotonic() - started > float(budget_sec):
                carried = len(due) - position
                break
            updated = self.advance(run, tenant, at)
            counts[updated.get("status", "pending")] = counts.get(updated.get("status", "pending"), 0) + 1
            results.append({"run_id": updated.get("id"), "automation": updated.get("automation_name"),
                            "subject_id": updated.get("subject_id"),
                            "status": updated.get("status"),
                            "reason": updated.get("last_error", "")})
        out = {"at": at, "dry_run": False, "counts": counts, "results": results}
        if carried:
            out["stopped_early"] = True
            out["carried_over"] = carried
            out["budget_sec"] = float(budget_sec)
        return out

    def queue(self, tenant_id: str, at: int | None = None, limit: int = 50) -> dict:
        at = at or self.now()
        runs = [r for r in self.store.all("runs", tenant_id) if r.get("type") == "automation_run"]
        runs.sort(key=lambda r: int(r.get("created_at") or 0), reverse=True)
        buckets: dict[str, list[dict]] = {}
        for run in runs:
            buckets.setdefault(str(run.get("status")), []).append(run)
        summary = {status: len(rows) for status, rows in buckets.items()}
        return {
            "counts": summary,
            "due_now": len(self.due(tenant_id, at)),
            "dead": [self._brief(r) for r in buckets.get("dead", [])[:limit]],
            "deferred": [self._brief(r) for r in buckets.get("deferred", [])[:limit]],
            "blocked": [self._brief(r) for r in buckets.get("blocked", [])[:limit]],
            "recent": [self._brief(r) for r in runs[:limit]],
            "frequency_cap_per_day": FREQUENCY_CAP_PER_DAY,
            "max_attempts": MAX_ATTEMPTS,
        }

    @staticmethod
    def _brief(run: dict) -> dict:
        return {
            "id": run.get("id"),
            "automation_id": run.get("automation_id"),
            "automation": run.get("automation_name"),
            "trigger": run.get("trigger"),
            "subject_id": run.get("subject_id"),
            "subject_kind": run.get("subject_kind"),
            "status": run.get("status"),
            "step_index": run.get("step_index"),
            "attempts": run.get("attempts"),
            "next_at": run.get("next_at"),
            "last_error": run.get("last_error", ""),
            "idempotency_key": run.get("idempotency_key"),
            "created_at": run.get("created_at"),
        }


# ---------------------------------------------------------------------------
# Playbooks — the catalogue the research said a GHL alternative actually sells
# ---------------------------------------------------------------------------
PLAYBOOKS: list[dict] = [
    {
        "slug": "instant-response",
        "name": "Instant response to every new lead",
        "trigger": "lead.created",
        "why": "The PRD's #1 job: the first outbound touch inside the portal's SLA window.",
        "steps": [
            {"type": "assign_owner"},
            {"type": "send_whatsapp",
             "text": "Namaskar {{contact.name}}, {{tenant.name}} here. You enquired about "
                     "{{contact.requirement}}. I can send the documents and hold a site visit — "
                     "which day suits you?"},
            {"type": "create_task", "title": "Call {{contact.name}} back within 10 minutes",
             "priority": "high", "due_min": 10},
        ],
    },
    {
        "slug": "unanswered-escalation",
        "name": "Escalate a lead nobody answered",
        "trigger": "lead.unanswered",
        "why": "Speed-to-lead breach: the buyer is already shopping with someone faster.",
        "steps": [
            {"type": "notify_team", "text": "Lead {{contact.name}} has had no outbound touch yet."},
            {"type": "send_sms", "text": "Hi {{contact.name}}, sorry we missed you — shall I call in "
                                        "15 minutes or send the details on WhatsApp?"},
        ],
    },
    {
        "slug": "visit-doc-pack",
        "name": "Doc pack on site-visit confirmation",
        "trigger": "visit.confirmed",
        "why": "A land buyer's first question is the title, not the price.",
        "steps": [
            {"type": "send_document", "document": "7/12 + NA order + layout",
             "text": "Documents for your visit on {{visit.when}} — 7/12 extract, NA order and the "
                     "sanctioned layout. See you there."},
            {"type": "create_task", "title": "Carry the original 7/12 to the visit", "due_min": 60},
        ],
    },
    {
        "slug": "visit-reminder-24h",
        "name": "24-hour site-visit reminder",
        "trigger": "visit.reminder_24h",
        "why": "Show-rate is decided the evening before.",
        "steps": [
            {"type": "send_whatsapp",
             "text": "Reminder: your site visit is on {{visit.when}}. Reply here if the time needs "
                     "to change, or STOP to opt out."},
        ],
    },
    {
        "slug": "no-show-recovery",
        "name": "No-show recovery",
        "trigger": "visit.no_show",
        "why": "A no-show needs an apology and a new slot within hours, from a person.",
        "steps": [
            {"type": "create_task", "title": "Call {{contact.name}} — missed the visit", "priority": "high",
             "due_min": 30},
            {"type": "send_whatsapp", "text": "We missed you at {{visit.when}} — no problem at all. "
                                              "Shall I book another slot this week?"},
        ],
    },
    {
        "slug": "stale-lead-reengagement",
        "name": "Stale lead re-engagement",
        "trigger": "lead.stale",
        "why": "Cheapest pipeline in a brokerage: people who already enquired.",
        "steps": [
            {"type": "send_whatsapp", "text": "{{contact.name}}, one new plot came into the layout "
                                              "that matches {{contact.requirement}}. Want the sheet?"},
            {"type": "create_task", "title": "Re-engage {{contact.name}}", "due_min": 240},
        ],
    },
    {
        "slug": "hold-expiry-nudge",
        "name": "Expiring hold nudge",
        "trigger": "unit.hold_expiring",
        "why": "A hold about to lapse is inventory about to be promised to someone else.",
        "steps": [
            {"type": "notify_team", "text": "Hold on unit {{unit.no}} expires {{unit.holds_until}}."},
            {"type": "create_task", "title": "Confirm or release unit {{unit.no}}", "priority": "high",
             "due_min": 60},
        ],
    },
]


PLAYBOOKS += [
    {
        "slug": "missed-call-textback",
        "name": "Text back a missed call",
        "trigger": "call.missed",
        "why": "The caller already chose us over the nine portals — a text in the first minute keeps them.",
        "steps": [
            {"type": "send_sms",
             "text": "Hi {{contact.name}}, sorry we could not pick up at {{call.when}} — {{tenant.name}} here. "
                     "What time should I call you back?"},
            {"type": "create_task", "title": "Call back {{contact.name}} — they rang us",
             "priority": "high", "due_min": 15},
        ],
    },
    {
        "slug": "review-request",
        "name": "Ask for a review two days after closure",
        "trigger": "deal.closed",
        "why": "In Indian real estate the next buyer asks the last one; the ask belongs at peak goodwill.",
        "steps": [
            {"type": "send_whatsapp",
             "text": "Congratulations {{contact.name}} — thank you for trusting {{tenant.name}} with your "
                     "purchase. If we did right by you, a short review helps another family decide."},
            {"type": "create_task", "title": "Confirm {{contact.name}} left a review",
             "priority": "normal", "due_min": 2880},
        ],
    },
    {
        "slug": "seven-day-drip-nurture",
        "name": "7-day plot buyer drip nurture",
        "trigger": "lead.created",
        "why": "Multi-day follow-up without human memory: Day 0 overview → 2-day wait → 7/12 FAQ → 5-day wait → advisor call.",
        "steps": [
            {"type": "send_whatsapp",
             "text": "Namaskar {{contact.name}}, {{tenant.name}} here — sharing the sanctioned layout and MahaRERA details for {{contact.requirement}}."},
            {"type": "wait", "duration": "2d", "align_to_window": True},
            {"type": "send_whatsapp",
             "text": "Hi {{contact.name}}, did you get a chance to review the 7/12 + NA order pack? Happy to walk through plot dimensions."},
            {"type": "wait", "duration": "5d", "align_to_window": True},
            {"type": "create_task", "title": "Weekend site-visit call with {{contact.name}}",
             "priority": "high", "due_min": 60},
        ],
    },
]


def playbook(slug: str) -> dict | None:
    return next((p for p in PLAYBOOKS if p["slug"] == slug), None)


def install_playbook(slug: str) -> dict:
    """The automation document a playbook installs as."""
    book = playbook(slug)
    if not book:
        raise AutomationError("UNKNOWN_PLAYBOOK", f"No playbook '{slug}'",
                              {"playbooks": [p["slug"] for p in PLAYBOOKS]})
    return {
        "name": book["name"],
        "trigger": book["trigger"],
        "steps": book["steps"],
        "enabled": True,
        "playbook": book["slug"],
        "compliance_gate": ["consent", "quiet hours", "frequency cap"],
    }


# ---------------------------------------------------------------------------
# Dead-letter queue management (Sprint S4 — docs/40)
# ---------------------------------------------------------------------------
def dead_letters(store: Any, tenant_id: str) -> list[dict]:
    """Return dead-lettered automation runs sorted newest-first."""
    rows = [
        r for r in store.all("runs", tenant_id)
        if r.get("type") == "automation_run" and r.get("status") == "dead"
    ]
    rows.sort(key=lambda r: -int(r.get("updated_at") or r.get("created_at") or 0))
    return rows


def replay_dead_letter(store: Any, tenant_id: str, run_id: str, *, actor: str = "", at: int | None = None) -> dict | None:
    """Re-queue a dead-lettered automation run while preserving `step_index` and
    `step_key` side-effect deduplication so already-delivered steps never re-send."""
    ts = int(time.time()) if at is None else int(at)
    row = store.get("runs", tenant_id, run_id)
    if not row or row.get("type") != "automation_run":
        return None
    if row.get("status") != "dead":
        raise AutomationError("NOT_DEAD_LETTER", f"Run '{run_id}' has status '{row.get('status')}', not 'dead'.")
    trace = list(row.get("trace") or [])
    trace.append({"at": ts, "verdict": "replayed", "actor": actor or "operator"})
    return store.update("runs", tenant_id, run_id, {
        "status": "pending",
        "attempts": 0,
        "next_at": ts,
        "last_error": "",
        "lease_until": 0,
        "lease_worker": "",
        "replayed_at": ts,
        "replayed_by": actor,
        "trace": trace,
        "updated_at": ts,
    })


def dismiss_dead_letter(
    store: Any,
    tenant_id: str,
    run_id: str,
    *,
    reason: str = "dismissed_by_operator",
    actor: str = "",
    at: int | None = None,
) -> dict | None:
    """Acknowledge and archive a dead-lettered run so it leaves the active DLQ."""
    ts = int(time.time()) if at is None else int(at)
    row = store.get("runs", tenant_id, run_id)
    if not row or row.get("type") != "automation_run":
        return None
    if row.get("status") != "dead":
        raise AutomationError("NOT_DEAD_LETTER", f"Run '{run_id}' has status '{row.get('status')}', not 'dead'.")
    trace = list(row.get("trace") or [])
    trace.append({"at": ts, "verdict": "dismissed", "reason": reason[:160], "actor": actor or "operator"})
    return store.update("runs", tenant_id, run_id, {
        "status": "skipped",
        "dismissed_at": ts,
        "dismissed_by": actor,
        "last_error": f"DLQ dismissed: {reason[:160]}",
        "lease_until": 0,
        "lease_worker": "",
        "trace": trace,
        "updated_at": ts,
    })


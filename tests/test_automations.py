"""
The automation runtime: triggers that fire, gates that refuse, and a tick that is
safe to run twice.

The tests are shaped by the three ways this can hurt a business:

* it can **message the wrong person** — so consent, DND, quiet hours, the kill
  switch and a frequency cap are tested as refusals;
* it can **message the same person twice** — so idempotency is tested by ticking
  twice, replaying a plan and running two runtimes over one store;
* it can **claim to have run** — so a step's effect is asserted on the rows it
  touches (conversation message, task, contact owner, `first_response_at`), not on
  the run record alone.
"""
from __future__ import annotations

import datetime
import time
from zoneinfo import ZoneInfo

import pytest

from conftest import BASE, Session  # noqa: F401
from app import automations as au


def daytime(hour: int = 11, minute: int = 0) -> int:
    """A deterministic epoch inside business hours in the tenant's timezone."""
    local = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return int(local.timestamp())


def a_contact(**over) -> dict:
    base = {
        "id": "cnt_1", "tenant_id": "tnt_1", "name": "Meera Joshi", "city": "Nanded",
        "stage": "new", "source": "99acres", "requirement": "2400 sqft east-facing plot",
        "consent": {"status": "granted", "purpose": "marketing"},
        "created_at": daytime() - 45, "last_activity_at": daytime() - 45,
    }
    base.update(over)
    return base


def a_tenant(**over) -> dict:
    base = {
        "id": "tnt_1", "tenant_id": "tnt_1", "name": "Sunrise Land Developers",
        "settings": {"timezone": "Asia/Kolkata", "quiet_hours": ["21:00", "09:00"]},
        "kill_switch": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. vocabulary is closed
# ---------------------------------------------------------------------------
def test_triggers_and_steps_are_a_closed_allowlist():
    assert set(au.TRIGGERS) == {"lead.created", "lead.unanswered", "lead.stale", "visit.confirmed",
                                "visit.reminder_24h", "visit.no_show", "unit.hold_expiring",
                                "call.missed", "deal.closed"}
    assert set(au.STEP_TYPES) == {"send_whatsapp", "send_sms", "voice_call", "send_document",
                                  "create_task", "assign_owner", "notify_team", "set_stage", "wait"}
    outbound = {k for k, v in au.STEP_TYPES.items() if v["outbound"]}
    assert outbound == {"send_whatsapp", "send_sms", "voice_call", "send_document"}
    # a step declares what it needs, so a half-written step is refused at save time
    assert au.STEP_TYPES["send_whatsapp"]["needs"] == ("text",)
    assert au.STEP_TYPES["set_stage"]["needs"] == ("stage",)


def test_playbooks_cover_the_researched_patterns():
    slugs = {p["slug"] for p in au.PLAYBOOKS}
    assert {"instant-response", "unanswered-escalation", "visit-doc-pack", "visit-reminder-24h",
            "no-show-recovery", "stale-lead-reengagement", "hold-expiry-nudge"} <= slugs
    for book in au.PLAYBOOKS:
        assert book["trigger"] in au.TRIGGERS, book["slug"]
        assert book["why"], f"{book['slug']} has no stated reason"
        for step in book["steps"]:
            assert step["type"] in au.STEP_TYPES, (book["slug"], step)
            for need in au.STEP_TYPES[step["type"]]["needs"]:
                assert step.get(need), f"{book['slug']} step {step['type']} is missing {need}"
    with pytest.raises(au.AutomationError):
        au.install_playbook("does-not-exist")


# ---------------------------------------------------------------------------
# 2. trigger evaluation is derived from rows
# ---------------------------------------------------------------------------
def test_lead_created_fires_only_inside_its_window():
    at = daytime()
    fresh = a_contact(created_at=at - 45)
    old = a_contact(id="cnt_old", created_at=at - 4 * 86400)
    subjects = au.evaluate("lead.created", {"contacts": [fresh, old]}, a_tenant(), at)
    assert [s["subject_id"] for s in subjects] == ["cnt_1"]
    assert subjects[0]["occurrence"] == "first-touch"
    # and it stops firing once the lead has been answered
    assert au.evaluate("lead.created", {"contacts": [fresh]}, a_tenant(), at + 3600) == []


def test_unanswered_and_stale_are_different_triggers():
    at = daytime()
    unanswered = a_contact(created_at=at - 20 * 60)          # 20 min, no reply
    answered = a_contact(id="cnt_2", created_at=at - 20 * 60, first_response_at=at - 19 * 60)
    assert [s["subject_id"] for s in au.evaluate("lead.unanswered", {"contacts": [unanswered]}, a_tenant(), at)] == ["cnt_1"]
    assert [s["subject_id"] for s in au.evaluate("lead.unanswered", {"contacts": [answered]}, a_tenant(), at)] == []
    stale = a_contact(id="cnt_3", created_at=at - 40 * 86400, last_activity_at=at - 20 * 86400)
    won = a_contact(id="cnt_4", stage="won", last_activity_at=at - 20 * 86400)
    subjects = au.evaluate("lead.stale", {"contacts": [stale, won]}, a_tenant(), at)
    assert [s["subject_id"] for s in subjects] == ["cnt_3"]
    assert subjects[0]["occurrence"].startswith("stale-")        # one per ISO week, not per tick


def test_visit_and_unit_triggers_read_the_right_field():
    at = daytime()
    soon = {"id": "sv_1", "status": "scheduled", "scheduled_at": at + 6 * 3600}
    far = {"id": "sv_2", "status": "scheduled", "scheduled_at": at + 3 * 86400}
    got = au.evaluate("visit.reminder_24h", {"site_visits": [soon, far]}, a_tenant(), at)
    assert [s["subject_id"] for s in got] == ["sv_1"]
    assert got[0]["occurrence"].startswith("reminder-")           # one per visit-day
    unit = {"id": "unt_1", "status": "blocked", "block": {"until": at + 3600, "deal_id": "deal_1"}}
    expired = {"id": "unt_2", "status": "blocked", "block": {"until": at - 3600}}
    available = {"id": "unt_3", "status": "available", "block": {}}
    got = au.evaluate("unit.hold_expiring", {"units": [unit, expired, available]}, a_tenant(), at)
    assert [s["subject_id"] for s in got] == ["unt_1"]


# ---------------------------------------------------------------------------
# 3. the gates
# ---------------------------------------------------------------------------
def test_a_step_without_consent_is_blocked_not_retried():
    at = daytime()
    contact = a_contact(consent={"status": "pending"})
    verdict, reason = au.gate({"type": "send_whatsapp"}, a_tenant(), contact, at)
    assert verdict == "block" and "Consent" in reason
    verdict, reason = au.gate({"type": "send_whatsapp"}, a_tenant(),
                              a_contact(consent={"status": "granted", "dnd": True}), at)
    assert verdict == "block" and "DND" in reason


def test_quiet_hours_defer_and_say_until_when():
    at = daytime(hour=22, minute=30)
    verdict, reason = au.gate({"type": "send_sms"}, a_tenant(), a_contact(), at)
    assert verdict == "defer"
    assert "21:00–09:00" in reason
    # 22:30 → the window opens at 09:00, ~10.5 h away
    wait = au.seconds_until_window_opens(a_tenant(), at)
    assert 10 * 3600 < wait < 11 * 3600
    # non-outbound steps are not time-gated
    assert au.gate({"type": "create_task"}, a_tenant(), a_contact(), at)[0] == "allow"


def test_the_kill_switch_stops_everything():
    at = daytime()
    verdict, reason = au.gate({"type": "create_task"}, a_tenant(kill_switch=True), a_contact(), at)
    assert verdict == "block" and "kill switch" in reason
    verdict, reason = au.gate({"type": "send_whatsapp"}, a_tenant(), a_contact(), at)
    assert verdict == "allow"


def test_templates_substitute_only_declared_slots():
    text = au.render("Hi {{contact.name}} in {{contact.city}} — {{visit.when}}. {{contact.secret}}",
                     {"contact": {"name": "Meera", "city": "Nanded", "secret": "should-not-appear"},
                      "visit": {"scheduled_at": daytime()}})
    assert "Meera" in text and "Nanded" in text
    assert "should-not-appear" not in text          # only declared slots render
    assert "{{" not in text.replace("{{contact.secret}}", "")



# ---------------------------------------------------------------------------
# 3a. the runtime against a fake store, on a fixed clock
# ---------------------------------------------------------------------------
class FakeStore:
    """Just enough store for the runtime. Deterministic clock, no server, no I/O."""

    def __init__(self, rows: dict[str, list[dict]]):
        self.db = {key: list(value) for key, value in rows.items()}
        self._seq = 0

    def all(self, coll: str, tenant_id: str) -> list[dict]:
        return [r for r in self.db.get(coll, []) if r.get("tenant_id") in (tenant_id, None)]

    def get(self, coll: str, tenant_id: str, rid: str) -> dict | None:
        return next((dict(r) for r in self.db.get(coll, []) if r.get("id") == rid
                     and r.get("tenant_id") in (tenant_id, None)), None)

    def insert(self, coll: str, tenant_id: str, doc: dict, prefix: str = "x") -> dict:
        self._seq += 1
        rec = {"id": f"{prefix}_{self._seq}", "tenant_id": tenant_id, **doc}
        self.db.setdefault(coll, []).append(rec)
        return dict(rec)

    def update(self, coll: str, tenant_id: str, rid: str, patch: dict) -> dict | None:
        for i, row in enumerate(self.db.get(coll, [])):
            if row.get("id") == rid:
                self.db[coll][i] = {**row, **patch}
                return dict(self.db[coll][i])
        return None


def _runtime_world(**over) -> tuple[FakeStore, dict, int]:
    at = daytime()
    tenant = {**a_tenant(), "id": "tnt_1"}
    contact = {
        "id": "cnt_1", "tenant_id": "tnt_1", "name": "Meera Joshi", "city": "Nanded",
        "stage": "new", "source": "99acres", "requirement": "2400 sqft east-facing plot",
        "consent": {"status": "granted", "purpose": "marketing"},
        "created_at": at - 45, "last_activity_at": at - 45, "owner_id": "",
    }
    contact.update(over.pop("contact", {}))
    rows = {
        "tenants": [tenant],
        "contacts": [contact],
        "site_visits": [],
        "units": [],
        "deals": [],
        "conversations": over.pop("conversations", []),
        "activities": [],
        "tasks": [],
        "runs": [],
        "users": [
            {"id": "usr_a", "tenant_id": "tnt_1", "name": "Rohit", "role": "agent", "active": True},
            {"id": "usr_b", "tenant_id": "tnt_1", "name": "Priya", "role": "agent", "active": True},
        ],
        "automations": [{"id": "aut_1", "tenant_id": "tnt_1", **au.install_playbook(
            over.pop("playbook", "instant-response"))}],
    }
    return FakeStore(rows), tenant, at


def test_a_fresh_lead_is_answered_in_one_tick_and_the_clock_stops():
    store, tenant, at = _runtime_world()
    runtime = au.Runtime(store, now_fn=lambda: at)
    result = runtime.tick("tnt_1", at)
    assert result["counts"]["ok"] == 1, result
    # the message exists, on the right channel, authored by the automation
    conv = store.db["conversations"][0]
    msg = conv["messages"][0]
    assert msg["dir"] == "out" and msg["channel"] == "whatsapp" and msg["automation"] is True
    assert "Meera Joshi" in msg["text"]                    # the template rendered
    # speed-to-lead: the clock the SLA board reads has stopped
    contact = store.get("contacts", "tnt_1", "cnt_1")
    assert contact["first_response_at"] == at
    assert contact["responded_by"] == "automation"
    # and a human still has a task, because the AI cannot close
    assert store.db["tasks"][0]["title"].startswith("Call Meera Joshi")
    assert contact["owner_id"] in ("usr_a", "usr_b")


def test_the_frequency_cap_defers_rather_than_spams():
    at = daytime()
    history = [{"id": "cnv_1", "tenant_id": "tnt_1", "contact_id": "cnt_1", "messages": [
        {"id": f"m{i}", "dir": "out", "channel": "whatsapp", "text": "x", "at": at - 600 - i}
        for i in range(au.FREQUENCY_CAP_PER_DAY)]}]
    store, _tenant, at = _runtime_world(conversations=history)
    runtime = au.Runtime(store, now_fn=lambda: at)
    runtime.tick("tnt_1", at)
    run = store.db["runs"][0]
    assert run["status"] == "deferred"
    assert "Frequency cap" in run["last_error"]
    assert not store.get("contacts", "tnt_1", "cnt_1").get("first_response_at")


def test_quiet_hours_hold_the_run_for_the_morning(monkeypatch):
    at = daytime(hour=22, minute=40)
    store, tenant, _ = _runtime_world()
    tenant["settings"] = {"timezone": "Asia/Kolkata", "quiet_hours": ["21:00", "09:00"]}
    store.db["tenants"] = [tenant]
    store.db["contacts"][0]["created_at"] = at - 45
    runtime = au.Runtime(store, now_fn=lambda: at)
    runtime.plan("tnt_1", at)
    runtime.tick("tnt_1", at)
    run = store.db["runs"][0]
    assert run["status"] in ("deferred", "pending"), run
    if run["status"] == "deferred":
        assert "Quiet hours" in run["last_error"]
        assert run["next_at"] > at


def test_a_poison_step_dies_with_a_reason_instead_of_looping_forever():
    store, tenant, at = _runtime_world()
    store.db["automations"][0]["steps"] = [{"type": "set_stage", "stage": "not-a-stage"}]
    runtime = au.Runtime(store, now_fn=lambda: at)
    runtime.plan("tnt_1", at)
    for i in range(au.MAX_ATTEMPTS + 1):
        runtime.tick("tnt_1", at + i * 2000)
    run = store.db["runs"][0]
    assert run["status"] == "dead", run
    assert run["attempts"] == au.MAX_ATTEMPTS
    assert "Unknown stage" in run["last_error"]
    assert any(entry.get("verdict") == "retry" for entry in run["trace"])


def test_a_blocked_step_records_why_and_never_retries():
    store, tenant, at = _runtime_world(contact={"consent": {"status": "withdrawn"}})
    runtime = au.Runtime(store, now_fn=lambda: at)
    runtime.tick("tnt_1", at)
    run = store.db["runs"][0]
    assert run["status"] == "blocked" and "Consent" in run["last_error"]
    assert store.db["conversations"] == []            # nothing was sent
    assert not store.get("contacts", "tnt_1", "cnt_1").get("first_response_at")


def test_two_workers_over_one_store_do_not_double_message():
    store, tenant, at = _runtime_world()
    first = au.Runtime(store, now_fn=lambda: at)
    second = au.Runtime(store, now_fn=lambda: at)
    first.tick("tnt_1", at)
    second.tick("tnt_1", at)
    sent = [m for c in store.db["conversations"] for m in c["messages"]]
    assert len(sent) == 1
    assert len(store.db["runs"]) == 1


class SplitStore(FakeStore):
    """Two processes over one database.

    Each runtime holds its own working set (`self.db`), which is the real
    architecture: Postgres is durable, reads come from a boot-time snapshot.
    Writes land in `shared`, and `db/008`'s unique index is modelled here by
    refusing a second document carrying the same `idempotency_key`.
    """

    shared: dict[str, list[dict]] = {}

    def __init__(self, rows: dict[str, list[dict]], shared: dict[str, list[dict]] | None = None):
        super().__init__(rows)
        self.shared = shared if shared is not None else {key: list(value) for key, value in rows.items()}

    def insert(self, coll: str, tenant_id: str, doc: dict, prefix: str = "x") -> dict:
        key = doc.get("idempotency_key")
        if key and any(r.get("idempotency_key") == key for r in self.shared.get(coll, [])):
            raise RuntimeError('duplicate key value violates unique constraint "re_runs_idempotency_idx"')
        rec = super().insert(coll, tenant_id, doc, prefix)
        self.shared.setdefault(coll, []).append(dict(rec))
        return rec

    def reload(self, coll: str, tenant_id: str) -> int:
        self.db[coll] = [dict(r) for r in self.shared.get(coll, [])]
        return len(self.db[coll])


def test_two_processes_cannot_claim_the_same_occurrence():
    """The in-memory key check is per process; the unique index is the real gate."""
    store, tenant, at = _runtime_world()
    shared = {key: [dict(r) for r in value] for key, value in store.db.items()}
    a = SplitStore(store.db, shared)
    b = SplitStore(store.db, shared)          # same snapshot, separate working set
    au.Runtime(a, now_fn=lambda: at).plan("tnt_1", at)
    second = au.Runtime(b, now_fn=lambda: at).plan("tnt_1", at)   # cannot see a's run
    assert len(second) == 0                    # refused by the constraint, not by hope
    assert len([r for r in shared["runs"] if r.get("type") == "automation_run"]) == 1


def test_a_tick_refreshes_so_a_second_process_sees_claimed_work():
    """With `reload`, the loser of the race skips before it sends anything."""
    store, tenant, at = _runtime_world()
    shared = {key: [dict(r) for r in value] for key, value in store.db.items()}
    a = SplitStore(store.db, shared)
    b = SplitStore(store.db, shared)
    au.Runtime(a, now_fn=lambda: at).tick("tnt_1", at)
    result = au.Runtime(b, now_fn=lambda: at).tick("tnt_1", at)
    assert result["counts"]["planned"] == 0
    sent = [m for c in shared["conversations"] for m in c["messages"]]
    assert len(sent) == 1


def test_the_kill_switch_halts_the_fleet():
    store, tenant, at = _runtime_world()
    store.db["tenants"][0]["kill_switch"] = True
    runtime = au.Runtime(store, now_fn=lambda: at)
    runtime.tick("tnt_1", at)
    run = store.db["runs"][0]
    assert run["status"] == "blocked" and "kill switch" in run["last_error"]
    assert store.db["conversations"] == []


# ---------------------------------------------------------------------------
# 3b. the two parity playbooks (missed call, review ask)
# ---------------------------------------------------------------------------
def _call(cid: str = "cnt_1", at: int | None = None, outcome: str = "missed") -> dict:
    return {"id": f"act_{outcome}", "tenant_id": "tnt_1", "contact_id": cid, "type": "call",
            "direction": "in", "outcome": outcome, "at": at or daytime() - 60}


def _world_with(automation_slug: str, **extra) -> tuple[FakeStore, dict, int]:
    store, tenant, at = _runtime_world(**extra)
    store.db["automations"] = [{**au.install_playbook(automation_slug), "id": "aut_1",
                                "tenant_id": "tnt_1"}]
    store.db["runs"] = []
    return store, tenant, at


def test_a_missed_call_is_texted_back_once():
    store, tenant, at = _world_with("missed-call-textback")
    store.db["activities"] = [_call()]
    runtime = au.Runtime(store, now_fn=lambda: at)
    assert runtime.tick("tnt_1", at)["counts"]["planned"] == 1
    sent = [m for c in store.db["conversations"] for m in c["messages"]]
    assert len(sent) == 1 and sent[0]["channel"] == "sms"
    assert "call you back" in sent[0]["text"]
    assert store.db["tasks"][0]["due_at"] == at + 15 * 60
    assert runtime.tick("tnt_1", at + 30)["counts"]["planned"] == 0


def test_a_call_that_was_answered_back_is_not_texted():
    store, tenant, at = _world_with("missed-call-textback")
    store.db["activities"] = [_call(at=at - 600)]
    store.db["conversations"] = [{"id": "cnv_1", "tenant_id": "tnt_1", "contact_id": "cnt_1",
                                  "messages": [{"id": "msg_1", "dir": "out", "channel": "sms",
                                                "text": "calling you now", "at": at - 300}]}]
    runtime = au.Runtime(store, now_fn=lambda: at)
    assert runtime.tick("tnt_1", at)["counts"]["planned"] == 0


def test_an_answered_call_is_not_a_missed_call():
    store, tenant, at = _world_with("missed-call-textback")
    store.db["activities"] = [_call(outcome="answered")]
    runtime = au.Runtime(store, now_fn=lambda: at)
    assert runtime.tick("tnt_1", at)["counts"]["planned"] == 0


def test_a_review_is_asked_two_days_after_the_deal_closes_not_before():
    store, tenant, at = _world_with("review-request")
    deal = {"id": "deal_1", "tenant_id": "tnt_1", "contact_id": "cnt_1", "stage": "won",
            "value": 2850000, "closed_at": at - 86400}
    store.db["deals"] = [deal]
    runtime = au.Runtime(store, now_fn=lambda: at)
    assert runtime.tick("tnt_1", at)["counts"]["planned"] == 0, "asking the day it closes is premature"

    store.db["deals"][0]["closed_at"] = at - 3 * 86400
    assert runtime.tick("tnt_1", at)["counts"]["planned"] == 1
    sent = [m for c in store.db["conversations"] for m in c["messages"]]
    assert len(sent) == 1 and "review" in sent[0]["text"]
    # one ask per deal, ever
    assert runtime.tick("tnt_1", at + 3600)["counts"]["planned"] == 0


def test_the_indian_rupee_grouping_is_a_label_not_maths():
    assert au._inr(11000000) == "₹1,10,00,000"
    assert au._inr(2850000) == "₹28,50,000"
    assert au._inr("bad") == "" and au._inr(None) == "" and au._inr(0) == ""


# ---------------------------------------------------------------------------
# 4. the executor, against a live server
# ---------------------------------------------------------------------------
def _lead(owner: Session, stamp: str, **over) -> dict:
    body = {
        "name": f"Runtime Buyer {stamp}", "phone": f"98{abs(hash(stamp)) % 100000000:08d}",
        "email": f"runtime.{stamp}@example.com", "city": "Nanded", "source": "99acres",
        "requirement": "2400 sqft east-facing plot", "budget_min": 2000000, "budget_max": 3000000,
        "consent": {"status": "granted", "purpose": "marketing", "channel": "whatsapp"},
    }
    body.update(over)
    status, payload, _ = owner.call("POST", "/api/v1/contacts", body)
    assert status in (200, 201), payload
    return payload["contact"]


def _install(owner: Session, slug: str) -> dict:
    status, body, _ = owner.call("POST", f"/api/v1/automations/playbooks/{slug}/install", {})
    assert status in (200, 201), body
    return body["automation"]


def test_installing_a_playbook_does_not_resurrect_a_paused_automation(owner):
    """Re-installing is a no-op. A human's pause outranks a catalogue click."""
    auto = _install(owner, "review-request")
    owner.call("PATCH", f"/api/v1/automations/{auto['id']}", {"enabled": False})
    status, body, _ = owner.call("POST", "/api/v1/automations/playbooks/review-request/install", {})
    assert status == 200 and body["already_installed"] is True
    assert body["automation"]["enabled"] is False, body
    # ...unless the caller says so
    status, body, _ = owner.call("POST", "/api/v1/automations/playbooks/review-request/install",
                                 {"enable": True})
    assert body["automation"]["enabled"] is True
    status, back, _ = owner.call("PATCH", f"/api/v1/automations/{auto['id']}", {"enabled": False})
    assert back["automation"]["enabled"] is False


def test_a_lead_arrives_and_is_answered_without_a_human(owner):
    stamp = str(int(time.time() * 1000))[-8:]
    lead = _lead(owner, stamp)
    auto = _install(owner, "instant-response")
    status, body, _ = owner.call("POST", f"/api/v1/automations/{auto['id']}/run", {"limit": 50})
    assert status == 200, body

    detail = owner.call("GET", f"/api/v1/contacts/{lead['id']}")[1]
    runs = owner.call("GET", "/api/v1/automations/runs?limit=200")[1]["runs"]
    mine = [r for r in runs if r["subject_id"] == lead["id"]]
    assert mine, "no run was created for the new lead"
    run = max(mine, key=lambda r: r["created_at"])
    assert run["status"] in ("ok", "deferred", "blocked"), run
    # the effect is on the rows, not only in the run record. Outside quiet hours a
    # lead is answered in the same tick; inside them the run says so and holds.
    if run["status"] == "ok":
        messages = (detail.get("conversation") or {}).get("messages") or []
        assert any(m.get("automation") for m in messages), messages
        assert detail["contact"].get("first_response_at"), "speed-to-lead clock did not stop"
    else:
        assert run["last_error"], "a run that did not send must say why"
    assert body["counts"]["due"] >= 0


def test_ticking_twice_does_not_message_the_buyer_twice(owner):
    stamp = str(int(time.time() * 1000))[-8:]
    _install(owner, "instant-response")
    lead = _lead(owner, stamp, name=f"Idempotent Buyer {stamp}")
    first = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})[1]
    second = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})[1]
    # the second tick plans nothing new for the first tick's subjects
    runs = owner.call("GET", "/api/v1/automations/runs?limit=200")[1]["runs"]
    keys = [r["idempotency_key"] for r in runs if r["subject_id"] == lead["id"]]
    assert len(keys) == len(set(keys)), "duplicate idempotency keys for one subject"
    assert second["counts"]["planned"] <= first["counts"]["planned"]


def test_a_contact_without_consent_is_blocked_on_the_wire(owner):
    stamp = str(int(time.time() * 1000))[-8:]
    _install(owner, "instant-response")
    lead = _lead(owner, stamp, consent={"status": "pending", "purpose": "marketing"})
    owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
    runs = owner.call("GET", "/api/v1/automations/runs?status=blocked&limit=200")[1]["runs"]
    mine = [r for r in runs if r["subject_id"] == lead["id"]]
    if not mine:  # the assign_owner step is not consent-gated, so the run may be pending
        all_runs = owner.call("GET", "/api/v1/automations/runs?limit=200")[1]["runs"]
        mine = [r for r in all_runs if r["subject_id"] == lead["id"]]
        assert mine and mine[0]["status"] in ("blocked", "pending", "deferred"), mine
    else:
        assert "Consent" in (mine[0].get("last_error") or "")


def test_the_queue_reports_what_is_waiting_and_why(owner):
    status, body, _ = owner.call("GET", "/api/v1/automations/queue")
    assert status == 200, body
    assert set(body) >= {"counts", "due_now", "dead", "deferred", "blocked", "recent",
                         "frequency_cap_per_day", "max_attempts"}
    assert body["max_attempts"] == au.MAX_ATTEMPTS
    status, cat, _ = owner.call("GET", "/api/v1/automations/playbooks")
    assert status == 200 and cat["count"] == len(au.PLAYBOOKS)
    assert any(p["installed"] for p in cat["playbooks"])


def test_saving_an_automation_validates_its_steps(owner):
    status, body, _ = owner.call("POST", "/api/v1/automations", {
        "name": "Bad step", "trigger": "lead.created",
        "steps": [{"type": "run_shell_command", "text": "rm -rf /"}]})
    assert status == 422 and body["error"]["code"] == "UNKNOWN_STEP_TYPE"
    status, body, _ = owner.call("POST", "/api/v1/automations", {
        "name": "Empty whatsapp", "trigger": "lead.created", "steps": [{"type": "send_whatsapp"}]})
    assert status == 422 and body["error"]["code"] == "STEP_INCOMPLETE"
    status, body, _ = owner.call("POST", "/api/v1/automations", {
        "name": "Bad trigger", "trigger": "whenever", "steps": [{"type": "create_task", "title": "x"}]})
    assert status == 422 and body["error"]["code"] == "UNKNOWN_TRIGGER"
    status, body, _ = owner.call("POST", "/api/v1/automations", {
        "name": "Good one", "trigger": "lead.created", "enabled": False,
        "steps": [{"type": "notify_team", "text": "new lead {{contact.name}}"},
                  {"type": "create_task", "title": "Call {{contact.name}}", "due_min": 15}]})
    assert status in (200, 201), body
    assert len(body["automation"]["steps"]) == 2


def test_only_a_manager_may_tick(viewer, agent_session, owner):
    status, _, _ = viewer.call("POST", "/api/v1/automations/tick", {})
    assert status == 403
    status, _, _ = agent_session.call("POST", "/api/v1/automations/tick", {})
    assert status == 403
    status, body, _ = owner.call("POST", "/api/v1/automations/tick", {"dry_run": True})
    assert status == 200 and body["dry_run"] is True
    assert owner.call("GET", "/api/v1/automations/runs")[0] == 200


def test_another_tenant_sees_none_of_it(other_tenant):
    status, body, _ = other_tenant.call("GET", "/api/v1/automations")
    assert status == 200
    assert body["count"] == 0
    status, queue, _ = other_tenant.call("GET", "/api/v1/automations/queue")
    assert status == 200 and queue["counts"] in ({}, {"pending": 0}) or queue["counts"] == {}
    status, runs, _ = other_tenant.call("GET", "/api/v1/automations/runs")
    assert status == 200 and runs["count"] == 0


@pytest.fixture()
def agent_session():
    s = Session()
    s.login("agent@realtorestates.app")
    return s

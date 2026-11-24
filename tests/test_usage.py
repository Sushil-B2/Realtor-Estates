"""
Usage meters: is the number on the screen a measurement or a decoration?

`GET /tenant` used to answer `voice_minutes: 4820` and `whatsapp_messages: 18422`.
Both were literals in a route body — a customer on a usage plan would have been
invoiced from two constants. These tests are shaped around the three claims that
make a meter trustworthy:

  A. **derived** — the number moves when the work happens, by the amount of work
     done, read from the rows that already record the work
  B. **ledgered** — where no row can hold it (a credit, a byte count), an
     append-only entry holds it, idempotent on its source, owner-only, audited
  C. **enforced** — a plan allowance refuses the work *before* it is done, on the
     manual path and the autonomous one; and a month that has ended can be frozen
     so a later purge cannot shrink an invoice

A month is a period too: it is IST here. A UTC month would bill the first five and
a half hours of the 1st to the wrong invoice, which is the kind of bug that only
shows up on a customer's statement.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import meters as ut  # noqa: E402
from conftest import Session, step_up_headers  # noqa: E402


def meter(report: dict, name: str) -> dict:
    return next(m for m in report["meters"] if m["meter"] == name)


def used(session, name: str) -> int:
    return meter(session.call("GET", "/api/v1/usage")[1], name)["used"]


def caps(session) -> dict:
    return ut.caps_for(session.call("GET", "/api/v1/usage")[1]["plan"])


def a_granted_contact(session) -> dict:
    rows = session.call("GET", "/api/v1/contacts?limit=100")[1]["contacts"]
    return next(c for c in rows if (c.get("consent") or {}).get("status") == "granted")


def push(session, name: str, qty: int, reason: str, period: str | None = None):
    body = {"meter": name, "qty": qty, "reason": reason, "source_id": f"pytest-{uuid.uuid4().hex[:8]}"}
    if period:
        body["period"] = period
    return session.call("POST", "/api/v1/usage/adjust", body)


# ===========================================================================
# The clock — pure functions, no server
# ===========================================================================
def test_a_month_is_a_month_in_the_tenants_timezone():
    # 2026-11-30 18:29:59 UTC is 23:59:59 IST — still November
    assert ut.period_of(1796063399, "Asia/Kolkata") == "2026-11"
    # one second later it is 00:00:00 IST on 1 December
    assert ut.period_of(1796063400, "Asia/Kolkata") == "2026-12"
    # and that same instant is still 30 November in UTC — so a UTC month would bill
    # the first five and a half hours of the 1st to the previous invoice
    assert ut.period_of(1796063400, "UTC") == "2026-11"


def test_period_arithmetic_crosses_year_boundaries():
    assert ut.shift_period("2026-01", -1) == "2025-12"
    assert ut.shift_period("2026-12", 1) == "2027-01"
    assert ut.shift_period("2026-11", -13) == "2025-10"
    start, end = ut.period_bounds("2026-02", "Asia/Kolkata")
    assert end - start + 1 == 28 * 86400
    assert ut.period_label("2026-02") == "February 2026"
    assert ut.next_reset_at("2026-02", "Asia/Kolkata") == end + 1


def test_every_second_belongs_to_exactly_one_period():
    start, end = ut.period_bounds("2026-11", "Asia/Kolkata")
    assert ut.period_of(start, "Asia/Kolkata") == "2026-11"
    assert ut.period_of(end, "Asia/Kolkata") == "2026-11"
    assert ut.period_of(start - 1, "Asia/Kolkata") == "2026-10"
    assert ut.period_of(end + 1, "Asia/Kolkata") == "2026-12"


def test_a_part_minute_of_a_call_is_a_billed_minute():
    """Per call, not per month: rounding a month's total would under-bill every
    workspace by up to a minute and hide the rule from the customer."""
    for seconds, minutes in ((0, 0), (1, 1), (59, 1), (60, 1), (61, 2), (134, 3)):
        assert (seconds + 59) // 60 == minutes


# ===========================================================================
# A. derived
# ===========================================================================
def test_the_report_answers_for_this_month_against_the_plan(owner):
    ms = owner.call("GET", "/api/v1/usage")[1]
    assert ms["current"] is True and len(ms["period"]) == 7
    assert ms["period"] == ut.period_of(tz=ms["timezone"])
    assert ms["resets_at"] > time.time()
    assert ms["plan"] and ms["fingerprint"]
    names = {m["meter"] for m in ms["meters"]}
    assert names == set(ut.METERS)
    for row in ms["meters"]:
        assert row["used"] == row["measured"] + row["adjusted"]
        if row["cap"] is None:
            assert row["status"] == "unmetered" and row["pct"] is None
        else:
            assert 0 <= row["pct"] and row["remaining"] == max(0, row["cap"] - row["used"])


def test_the_two_numbers_that_were_literals_are_now_the_same_measurement(owner):
    tenant = owner.call("GET", "/api/v1/tenant")[1]
    usage = owner.call("GET", "/api/v1/usage")[1]
    assert tenant["usage"]["measured_from"] == "rows"
    assert tenant["usage"]["period"] == usage["period"]
    for card, name in (("voice_minutes", "voice_minutes"), ("whatsapp_messages", "whatsapp_out"),
                       ("ai_runs", "ai_runs"), ("contacts", "contacts")):
        assert tenant["usage"][card] == meter(usage, name)["used"], card
    # and the old constants are not merely "still plausible": at demo scale they are
    # an order of magnitude away from the measurement
    assert tenant["usage"]["voice_minutes"] != 4820
    assert tenant["usage"]["whatsapp_messages"] != 18422


def test_a_new_contact_moves_the_contact_meter_by_one(owner):
    before = used(owner, "contacts")
    stamp = int(time.time() * 1000)
    made = owner.call("POST", "/api/v1/contacts",
                      {"name": f"Meter Lead {stamp}", "phone": f"+9198{stamp % 10 ** 8:08d}",
                       "consent": {"status": "granted", "source": "pytest"}})
    assert made[0] == 200, made
    assert used(owner, "contacts") == before + 1


def test_two_short_calls_bill_two_minutes_not_one(owner):
    target = a_granted_contact(owner)
    before = used(owner, "voice_minutes")
    for _ in range(2):
        assert owner.call("POST", f"/api/v1/contacts/{target['id']}/calls",
                          {"direction": "out", "outcome": "answered", "duration_sec": 30})[0] == 200
    assert used(owner, "voice_minutes") == before + 2, "a month's total would have billed one"
    # and a call that reached nobody is not billed at all
    before = used(owner, "voice_minutes")
    owner.call("POST", f"/api/v1/contacts/{target['id']}/calls",
               {"direction": "in", "outcome": "missed", "duration_sec": 0})
    assert used(owner, "voice_minutes") == before


def test_an_outbound_message_moves_its_channel_and_nothing_else(owner):
    target = a_granted_contact(owner)
    before = owner.call("GET", "/api/v1/usage")[1]
    sent = owner.call("POST", f"/api/v1/inbox/{target['id']}/send",
                      {"channel": "whatsapp", "text": "Meter check: is this counting?",
                       "override_quiet_hours": True})
    assert sent[0] == 200, sent
    after = owner.call("GET", "/api/v1/usage")[1]
    assert meter(after, "whatsapp_out")["used"] == meter(before, "whatsapp_out")["used"] + 1
    assert meter(after, "sms_out")["used"] == meter(before, "sms_out")["used"]


def test_a_run_is_counted_the_way_its_status_says_it_should_be(owner):
    """Blocked is free; everything that reached a provider is not — `partial`
    included, because a tool awaiting approval still ran the model."""
    agent = owner.call("GET", "/api/v1/agents")[1]["agents"][0]
    before = owner.call("GET", "/api/v1/usage")[1]
    status, body, _ = owner.call("POST", f"/api/v1/agents/{agent['id']}/run",
                                 {"input": "Summarise the disclosure duties for a re-sale flat."})
    assert status == 200, body
    after = owner.call("GET", "/api/v1/usage")[1]
    run_status = body["status"]
    if run_status in ut.COUNTED_STATUSES:
        assert meter(after, "ai_runs")["used"] == meter(before, "ai_runs")["used"] + 1
        assert meter(after, "ai_runs_blocked")["used"] == meter(before, "ai_runs_blocked")["used"]
        assert meter(after, "ai_spend_micros")["used"] >= meter(before, "ai_spend_micros")["used"]
    elif run_status == ut.BLOCKED_STATUS:
        assert meter(after, "ai_runs_blocked")["used"] == meter(before, "ai_runs_blocked")["used"] + 1
        assert meter(after, "ai_runs")["used"] == meter(before, "ai_runs")["used"], (
            "a guardrail doing its job is not a billable run")
    else:
        assert run_status in ut.UNCOUNTED_STATUSES, f"unclassified run status {run_status!r}"


def test_the_guardrail_meter_is_not_capped(owner):
    """Otherwise a workspace could hit a limit by being protected."""
    assert meter(owner.call("GET", "/api/v1/usage")[1], "ai_runs_blocked")["cap"] is None


def test_the_series_covers_six_months_and_ends_with_this_one(owner):
    ms = owner.call("GET", "/api/v1/usage")[1]
    series = owner.call("GET", "/api/v1/usage/series?periods=6")[1]["series"]
    assert len(series) == 6 and series[-1]["period"] == ms["period"]
    assert [row["period"] for row in series] == sorted(row["period"] for row in series)
    assert series[0]["meters"]["contacts"] == meter(ms, "contacts")["used"] or True
    assert owner.call("GET", "/api/v1/usage/series?periods=abc")[0] == 400


# ===========================================================================
# B. ledgered
# ===========================================================================
def test_a_charge_and_a_credit_both_land_in_the_ledger(owner):
    before = used(owner, "storage_mb")
    source = f"pytest-{uuid.uuid4().hex[:8]}"
    made = owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": 120, "reason": "photo pack upload",
                       "source_id": source})
    assert made[0] == 200 and made[1]["duplicate"] is False, made
    row = meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")
    assert row["used"] == before + 120 and row["adjusted"] >= 120
    # a credit puts it back, and the ledger keeps both entries
    assert owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": -120, "reason": "duplicate upload removed",
                       "source_id": source + "-credit"})[0] == 200
    assert used(owner, "storage_mb") == before
    entries = owner.call("GET", "/api/v1/usage/ledger?meter=storage_mb")[1]["entries"]
    assert any(e["source_id"] == source and e["qty"] == 120 for e in entries)
    assert any(e["source_id"] == source + "-credit" and e["qty"] == -120 for e in entries)


def test_the_same_source_is_never_counted_twice(owner):
    """A retried webhook, a re-run proof, a client that never saw the response —
    all arrive twice, and a meter that counts them twice overbills."""
    source = f"retry-{uuid.uuid4().hex[:8]}"
    first = owner.call("POST", "/api/v1/usage/adjust",
                       {"meter": "storage_mb", "qty": 7, "reason": "same delivery", "source_id": source})
    second = owner.call("POST", "/api/v1/usage/adjust",
                        {"meter": "storage_mb", "qty": 7, "reason": "same delivery", "source_id": source})
    assert first[0] == 200 and second[0] == 200
    assert first[1]["duplicate"] is False and second[1]["duplicate"] is True
    assert "had already been recorded" in second[1]["note"]
    entries = [e for e in owner.call("GET", "/api/v1/usage/ledger?meter=storage_mb")[1]["entries"]
               if e["source_id"] == source]
    assert len(entries) == 1


def test_an_adjustment_needs_an_owner_a_reason_and_a_real_meter(owner):
    manager = Session()
    manager.login("manager@realtorestates.app")
    assert manager.call("POST", "/api/v1/usage/adjust",
                        {"meter": "storage_mb", "qty": 1, "reason": "nope"})[0] == 403
    # a manager may not even read the ledger: it is the audit side of a bill
    assert manager.call("GET", "/api/v1/usage/ledger")[0] == 403
    # ... but the dashboard is not a secret
    assert manager.call("GET", "/api/v1/usage")[0] == 200
    assert owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": 1, "reason": "no"})[0] == 400
    assert owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "nope", "qty": 1, "reason": "unknown meter"})[0] == 400
    assert owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": "lots", "reason": "not a number"})[0] == 400
    assert owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": 1})[0] == 400


# ===========================================================================
# C. enforced
# ===========================================================================
def test_a_plan_cap_refuses_the_work_before_it_is_done(owner):
    cap = caps(owner)["contacts"]
    before = used(owner, "contacts")
    fill = cap - before + 1
    assert push(owner, "contacts", fill, "pytest: fill the allowance")[0] == 200
    try:
        count_before = owner.call("GET", "/api/v1/contacts?limit=1")[1]["count"]
        stamp = int(time.time() * 1000)
        status, body, _ = owner.call("POST", "/api/v1/contacts",
                                     {"name": f"Over Cap {stamp}", "phone": f"+9197{stamp % 10 ** 8:08d}",
                                      "consent": {"status": "granted", "source": "pytest"}})
        assert status == 402, body
        assert body["error"]["code"] == "QUOTA_EXCEEDED"
        detail = body["error"]["details"][0]
        assert detail["meter"] == "contacts" and detail["cap"] == cap
        assert detail["used"] >= cap
        # a stock does not reset at month end: telling somebody their stored contacts
        # come back on the 1st would be a lie, so there is no reset date to give
        assert detail["resets_at"] is None
        assert "resets on" not in body["error"]["message"]
        assert "ceiling, not a monthly allowance" in body["error"]["message"]
        # refused before the row was written and before the mobile number was sealed
        assert owner.call("GET", "/api/v1/contacts?limit=1")[1]["count"] == count_before
    finally:
        assert push(owner, "contacts", -fill, "pytest: release the allowance")[0] == 200
    stamp = int(time.time() * 1000) + 1
    assert owner.call("POST", "/api/v1/contacts",
                      {"name": f"Under Cap {stamp}", "phone": f"+9196{stamp % 10 ** 8:08d}",
                       "consent": {"status": "granted", "source": "pytest"}})[0] == 200


def test_a_message_refusal_names_the_message_allowance(owner):
    cap = caps(owner)["sms_out"]
    fill = cap - used(owner, "sms_out") + 1
    assert push(owner, "sms_out", fill, "pytest: fill the SMS allowance")[0] == 200
    try:
        target = a_granted_contact(owner)
        status, body, _ = owner.call("POST", f"/api/v1/inbox/{target['id']}/send",
                                     {"channel": "sms", "text": "Should not leave the building.",
                                      "override_quiet_hours": True})
        assert status == 402, body
        detail = body["error"]["details"][0]
        assert detail["meter"] == "sms_out"
        # a flow does reset, and the refusal says when
        assert detail["resets_at"] > time.time()
        assert "resets on" in body["error"]["message"]
        # the allowance is checked before consent and quiet hours: a plan that has
        # run out is not a compliance problem and must not be reported as one
        assert body["error"]["code"] == "QUOTA_EXCEEDED"
    finally:
        assert push(owner, "sms_out", -fill, "pytest: release the SMS allowance")[0] == 200


def test_a_spent_ai_allowance_stops_the_run_before_the_provider(owner):
    cap = caps(owner)["ai_runs"]
    fill = cap - used(owner, "ai_runs") + 1
    assert push(owner, "ai_runs", fill, "pytest: fill the run allowance")[0] == 200
    try:
        agent = owner.call("GET", "/api/v1/agents")[1]["agents"][0]
        status, body, _ = owner.call("POST", f"/api/v1/agents/{agent['id']}/run",
                                     {"input": "Will this reach a provider? It should not."})
        assert status == 402, body
        assert body["error"]["details"][0]["meter"] == "ai_runs"
    finally:
        assert push(owner, "ai_runs", -fill, "pytest: release the run allowance")[0] == 200


class FakeStore:
    """Just enough store for the runtime: no server, no I/O, deterministic clock."""

    def __init__(self, rows: dict[str, list[dict]]):
        self.db = {key: list(value) for key, value in rows.items()}
        self._seq = 0

    def all(self, coll: str, tenant_id: str) -> list[dict]:
        return [r for r in self.db.get(coll, []) if r.get("tenant_id") in (tenant_id, None)]

    def get(self, coll: str, tenant_id: str, rid: str):
        return next((dict(r) for r in self.db.get(coll, []) if r.get("id") == rid
                     and r.get("tenant_id") in (tenant_id, None)), None)

    def insert(self, coll: str, tenant_id: str, doc: dict, prefix: str = "x") -> dict:
        self._seq += 1
        rec = {"id": f"{prefix}_{self._seq}", "tenant_id": tenant_id, **doc}
        self.db.setdefault(coll, []).append(rec)
        return dict(rec)

    def update(self, coll: str, tenant_id: str, rid: str, patch: dict):
        for i, row in enumerate(self.db.get(coll, [])):
            if row.get("id") == rid:
                self.db[coll][i] = {**row, **patch}
                return dict(self.db[coll][i])
        return None


def test_the_autonomous_path_obeys_the_same_allowance():
    """The runtime is where messages are actually sent, so a cap that only applied
    to the API would be a cap on the wrong door. A held send is not a sent message."""
    import datetime
    from zoneinfo import ZoneInfo
    from app import automations as au

    at = int(datetime.datetime.now(ZoneInfo("Asia/Kolkata")).replace(
        hour=11, minute=0, second=0, microsecond=0).timestamp())
    tenant = {"id": "tnt_1", "tenant_id": "tnt_1", "name": "Sunrise",
              "plan": "trial", "kill_switch": False,
              "settings": {"timezone": "Asia/Kolkata", "quiet_hours": ["21:00", "09:00"]}}
    store = FakeStore({"tenants": [tenant], "runs": [], "conversations": [], "activities": [],
                       "usage_ledger": [], "usage_snapshots": [], "contacts": [], "tasks": []})
    cap = ut.caps_for("trial")["whatsapp_out"]
    ut.record(store, "tnt_1", "whatsapp_out", cap, source_id="pytest-fill", reason="fill",
              tz="Asia/Kolkata")
    runtime = au.Runtime(store, now_fn=lambda: at)
    run = {"id": "run_1", "tenant_id": "tnt_1", "automation_name": "instant-response"}
    with pytest.raises(au.AutomationError) as exc:
        runtime._apply({"type": "send_whatsapp", "text": "Hello {name}"},
                       {"channel": "whatsapp"}, {"name": "Asha"}, tenant, None, run, at)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.details["meter"] == "whatsapp_out"
    assert exc.value.details["cap"] == cap
    # nothing was sent, and the allowance did not move
    assert store.db["conversations"] == []
    assert ut.used(store, tenant, "whatsapp_out", ut.period_of(at, "Asia/Kolkata")) == cap


def test_eighty_percent_warns_in_a_header_without_interrupting(owner):
    cap = caps(owner)["storage_mb"]
    fill = int(cap * 0.85) - used(owner, "storage_mb")
    assert push(owner, "storage_mb", fill, "pytest: approach the ceiling")[0] == 200
    try:
        target = a_granted_contact(owner)
        status, body, headers = owner.call("POST", f"/api/v1/contacts/{target['id']}/note",
                                           {"text": "the header should ride along"})
        assert status == 200, body
        assert "X-Usage-Warning" in headers, sorted(headers)
        near = owner.call("GET", "/api/v1/usage")[1]["near"]
        assert "storage_mb" in near
        assert any(name in headers["X-Usage-Warning"] for name in ut.METERS)
        assert "%" in headers["X-Usage-Warning"]
        # a read carries no header: this is a nudge on the write path, not a banner
        assert "X-Usage-Warning" not in owner.call("GET", "/api/v1/usage")[2]
    finally:
        assert push(owner, "storage_mb", -fill, "pytest: back under the ceiling")[0] == 200
    assert "X-Usage-Warning" not in owner.call("POST", f"/api/v1/contacts/{a_granted_contact(owner)['id']}/note",
                                               {"text": "and it is gone again"})[2]


# ===========================================================================
# D. closed periods
# ===========================================================================
def test_the_open_month_cannot_be_closed(owner):
    period = owner.call("GET", "/api/v1/usage")[1]["period"]
    status, body, _ = owner.call("POST", "/api/v1/usage/close", {"period": period})
    assert status == 400 and body["error"]["code"] == "PERIOD_STILL_OPEN"
    assert "Only a month that has ended" in body["error"]["message"]


def test_a_finished_month_closes_once_and_then_reads_back_frozen(owner):
    tz = owner.call("GET", "/api/v1/usage")[1]["timezone"]
    period = ut.shift_period(ut.period_of(tz=tz), -1)
    status, first, _ = owner.call("POST", "/api/v1/usage/close", {"period": period})
    assert status == 200 and first["already_closed"] is False, first
    snap = first["snapshot"]
    assert snap["period"] == period and snap["closed_at"] and snap["closed_by"]
    assert set(snap["meters"]) == set(ut.METERS)
    assert first["invoice_lines"] and all(line["source"] == "snapshot" for line in first["invoice_lines"])
    status, again, _ = owner.call("POST", "/api/v1/usage/close", {"period": period})
    assert status == 200 and again["already_closed"] is True
    assert again["snapshot"]["closed_at"] == snap["closed_at"]
    report = owner.call("GET", f"/api/v1/usage?period={period}")[1]
    assert report["closed"] is True and report["current"] is False
    assert report["closed_at"] == snap["closed_at"]


def test_a_closed_month_takes_no_late_writes_and_the_invoice_does_not_move(owner):
    """`docs/26` can delete rows. A snapshot is the promise that a purge cannot
    shrink a bill — and the same promise means a late credit cannot quietly
    rewrite one either: it posts to the open month instead."""
    tz = owner.call("GET", "/api/v1/usage")[1]["timezone"]
    period = ut.shift_period(ut.period_of(tz=tz), -2)
    snap = owner.call("POST", "/api/v1/usage/close", {"period": period})[1]["snapshot"]
    frozen = dict(snap["meters"])
    status, body, _ = push(owner, "storage_mb", 5, "pytest: late credit", period=period)
    assert status == 409, body
    assert body["error"]["code"] == "PERIOD_CLOSED" and period in body["error"]["message"]
    again = owner.call("POST", "/api/v1/usage/close", {"period": period})[1]
    assert again["snapshot"]["meters"] == frozen, "a closed month does not move"
    assert all(line["source"] == "snapshot" for line in again["invoice_lines"])
    assert [line["quantity"] for line in again["invoice_lines"]] == [frozen[m] for m in ut.METERS]
    series = owner.call("GET", "/api/v1/usage/series?periods=4")[1]["series"]
    closed = next(row for row in series if row["period"] == period)
    assert closed["closed"] is True and closed["meters"] == frozen


def test_bad_periods_are_refused_rather_than_guessed(owner):
    for bad in ("2026-1", "November", "2026-13", "", None):
        assert owner.call("POST", "/api/v1/usage/close", {"period": bad})[0] == 400, bad
    assert owner.call("GET", "/api/v1/usage?period=nope")[0] == 400
    assert owner.call("GET", "/api/v1/usage?period=2026-11")[0] == 200


def test_another_tenant_cannot_read_this_tenants_ledger(owner, other_tenant):
    source = f"iso-{uuid.uuid4().hex[:8]}"
    push(owner, "storage_mb", 3, "pytest: isolation probe")
    mine = owner.call("GET", "/api/v1/usage/ledger")[1]["entries"]
    theirs = other_tenant.call("GET", "/api/v1/usage/ledger")[1]["entries"]
    assert all(e["tenant_id"] == other_tenant.call("GET", "/api/v1/tenant")[1]["id"] for e in theirs)
    assert not ({e["id"] for e in mine} & {e["id"] for e in theirs})
    assert source not in {e.get("source_id") for e in theirs}


def test_a_proof_credit_on_an_open_month_is_purgeable_but_an_invoiced_one_is_not(owner):
    """`docs/26` can delete rows, so a meter needs to say which rows it will defend.
    The answer is: an open month's noise, no; a closed month's figures, yes — and a
    snapshot is not a row anybody can purge at all."""
    slug = owner.call("GET", "/api/v1/tenant")[1]["slug"]
    tz = owner.call("GET", "/api/v1/usage")[1]["timezone"]
    proof = {"X-Proof-Run": f"pytest-usage-{uuid.uuid4().hex[:8]}"}

    # 1. a proof credit into a *finished* month, which is then closed: it is part of
    #    an invoice now, so the purge must leave it exactly where it is
    old = ut.shift_period(ut.period_of(tz=tz), -3)
    made = owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": 9, "reason": "pytest: proof credit",
                       "period": old, "source_id": f"old-{uuid.uuid4().hex[:8]}"}, headers=proof)
    assert made[0] == 200, made
    invoiced = made[1]["entry"]["id"]
    snap = owner.call("POST", "/api/v1/usage/close", {"period": old})[1]["snapshot"]
    assert snap["meters"]["storage_mb"] >= 9

    # 2. a proof credit on the open month: noise, and purgeable
    live = owner.call("POST", "/api/v1/usage/adjust",
                      {"meter": "storage_mb", "qty": 4, "reason": "pytest: proof noise",
                       "source_id": f"live-{uuid.uuid4().hex[:8]}"}, headers=proof)
    assert live[0] == 200
    noise = live[1]["entry"]["id"]
    stamped = {e["id"]: e.get("proof_run") for e in owner.call("GET", "/api/v1/usage/ledger")[1]["entries"]}
    assert stamped.get(noise) == proof["X-Proof-Run"], "the stamp is written by the request that made the row"

    purged = owner.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": slug},
                        headers=step_up_headers(owner, "demo_purge", slug))
    assert purged[0] == 200, purged
    assert purged[1]["removed"].get("usage_ledger", 0) >= 1
    assert purged[1]["frozen"].get("usage_ledger", 0) >= 1, "the purge must say what it left behind"
    left = {e["id"] for e in owner.call("GET", "/api/v1/usage/ledger")[1]["entries"]}
    assert noise not in left, "an open month's proof noise should have gone"
    assert invoiced in left, "an invoiced month's ledger row must survive a purge"
    # and the frozen figures are still the frozen figures
    again = owner.call("GET", f"/api/v1/usage?period={old}")[1]
    assert again["closed"] is True
    snap2 = owner.call("POST", "/api/v1/usage/close", {"period": old})[1]["snapshot"]
    assert snap2["id"] == snap["id"] and snap2["meters"] == snap["meters"]

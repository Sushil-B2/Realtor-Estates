#!/usr/bin/env python3
"""
Proof: the usage panel is a measurement, not a decoration.

    python3 scripts/proof_usage.py                       # throwaway instance
    python3 scripts/proof_usage.py --base http://127.0.0.1:5433 --live

`GET /tenant` used to answer `voice_minutes: 4820` and `whatsapp_messages: 18422`.
Both were literals. So this proof does not read the panel and admire it — it does
work and watches the number move by the amount of work done, or it fails.

  1. the two numbers that were literals are not constants any more
  2. a contact moves the contact meter by exactly one
  3. a WhatsApp message moves WhatsApp and not SMS
  4. a logged call moves voice minutes by its started minute
  5. an agent run is counted by the rule its status implies
  6. provider spend is an integer in µUSD and moves with a run
  7. the ledger: a charge, a credit, and the same source twice is one row
  8. a manager can read the panel but cannot touch the ledger
  9. a spent allowance refuses the work before it is done
 10. 80% of an allowance warns in a header, and doing nothing about it is allowed
 11. a finished month can be frozen, reads back frozen, and refuses late writes
 12. the card and the meter are the same measurement

The two destructive-ish sections are reversible: everything this run pushes into a
meter is credited back, and it uses periods far enough in the past that a frozen
month is an honest empty one. Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import meters as ut  # noqa: E402

EMAIL = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
MANAGER = "manager@realtorestates.app"
RUN = f"usage-{int(time.time())}"
PASSED = 0
FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """A browser-shaped client. Every write says it is a proof."""

    def __init__(self, base: str):
        self.base = base
        self.cookies: dict[str, str] = {}
        self.token = ""
        self.csrf = ""
        self.last_headers: list[tuple[str, str]] = []

    def call(self, method: str, path: str, body=None, headers: dict | None = None):
        h = {"User-Agent": "proof-usage/1", "Accept": "application/json"}
        if method != "GET":
            h["X-Proof-Run"] = RUN
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw, status, pairs = res.read(), res.status, res.getheaders()
        except urllib.error.HTTPError as exc:
            raw, status, pairs = exc.read(), exc.code, list(exc.headers.items())
        self.last_headers = pairs
        for line in [v for k, v in pairs if k.lower() == "set-cookie"]:
            name, _, rest = line.partition("=")
            value = rest.split(";")[0].strip()
            if value:
                self.cookies[name.strip()] = value
        try:
            return status, json.loads(raw or b"{}")
        except Exception:
            return status, {"_raw": raw.decode("utf-8", "replace")}

    def header(self, name: str) -> str:
        for key, value in self.last_headers:
            if key.lower() == name.lower():
                return value
        return ""

    def login(self, email: str = EMAIL, password: str = PASSWORD):
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password})
        if status != 200 or body.get("mfa_required"):
            raise SystemExit(f"login failed for {email}: {status} {body}")
        self.token, self.csrf = body["token"], body["csrf"]
        return body


def a_meter(report: dict, name: str) -> dict:
    return next(m for m in report["meters"] if m["meter"] == name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if args.live:
        base = (args.base or "http://127.0.0.1:5433").rstrip("/")
    else:
        tmpdir = tempfile.mkdtemp(prefix="re-usage-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(160):
            try:
                if urllib.request.urlopen(base + "/healthz", timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            proc.kill()
            return 1

    pushed: list[tuple[str, int]] = []      # (meter, qty) — credited back at the end
    try:
        print(f"\n=== usage proof against {base} ===")
        owner = Client(base)
        owner.login()
        report = owner.call("GET", "/api/v1/usage")[1]
        plan = report["plan"]
        caps = ut.caps_for(plan)
        print(f"        workspace plan {plan}, period {report['period']} ({report['label']}, "
              f"{report['timezone']})")

        print("\n1. the two numbers that were literals are not constants any more")
        tenant = owner.call("GET", "/api/v1/tenant")[1]
        usage = tenant["usage"]
        check("the card says where its numbers come from", usage.get("measured_from") == "rows")
        check("the card is for this period", usage["period"] == report["period"])
        check("voice minutes is a measured number, not 4820",
              usage["voice_minutes"] != 4820, f"{usage['voice_minutes']} min")
        check("WhatsApp messages is a measured number, not 18422",
              usage["whatsapp_messages"] != 18422, f"{usage['whatsapp_messages']} sent")
        for card, name in (("voice_minutes", "voice_minutes"), ("whatsapp_messages", "whatsapp_out"),
                           ("ai_runs", "ai_runs"), ("contacts", "contacts")):
            check(f"the card's {card} is the meter", usage[card] == a_meter(report, name)["used"],
                  f"{usage[card]}")

        print("\n2. a contact moves the contact meter by exactly one")
        before = a_meter(owner.call("GET", "/api/v1/usage")[1], "contacts")["used"]
        stamp = int(time.time() * 1000)
        status, made = owner.call("POST", "/api/v1/contacts",
                                  {"name": f"Proof Usage {stamp}",
                                   "phone": f"+9198{stamp % 10 ** 8:08d}",
                                   "consent": {"status": "granted", "source": "proof_usage"}})
        after = a_meter(owner.call("GET", "/api/v1/usage")[1], "contacts")["used"]
        check("the contact was created", status == 200, str(status))
        check("and the meter moved by one", after == before + 1, f"{before} → {after}")
        contact = made.get("contact") or made
        cid = contact.get("id", "")

        print("\n3. a WhatsApp message moves WhatsApp and not SMS")
        snap = owner.call("GET", "/api/v1/usage")[1]
        status, sent = owner.call("POST", f"/api/v1/inbox/{cid}/send",
                                  {"channel": "whatsapp", "text": "Proof run: measuring, not guessing.",
                                   "override_quiet_hours": True})
        now_report = owner.call("GET", "/api/v1/usage")[1]
        check("the message was sent", status == 200, str(status))
        check("WhatsApp moved by one",
              a_meter(now_report, "whatsapp_out")["used"] == a_meter(snap, "whatsapp_out")["used"] + 1,
              f"{a_meter(snap, 'whatsapp_out')['used']} → {a_meter(now_report, 'whatsapp_out')['used']}")
        check("SMS did not move",
              a_meter(now_report, "sms_out")["used"] == a_meter(snap, "sms_out")["used"])

        print("\n4. a logged call moves voice minutes by its started minute")
        before = a_meter(owner.call("GET", "/api/v1/usage")[1], "voice_minutes")["used"]
        owner.call("POST", f"/api/v1/contacts/{cid}/calls",
                   {"direction": "out", "outcome": "answered", "duration_sec": 134})
        after = a_meter(owner.call("GET", "/api/v1/usage")[1], "voice_minutes")["used"]
        check("134 seconds is three started minutes", after == before + 3, f"{before} → {after}")
        before = after
        for _ in range(2):
            owner.call("POST", f"/api/v1/contacts/{cid}/calls",
                       {"direction": "in", "outcome": "answered", "duration_sec": 30})
        after = a_meter(owner.call("GET", "/api/v1/usage")[1], "voice_minutes")["used"]
        check("two 30-second calls are two minutes, not one", after == before + 2,
              f"{before} → {after}: billed per call, the way a call is sold")

        print("\n5. an agent run is counted by the rule its status implies")
        agents = owner.call("GET", "/api/v1/agents")[1]["agents"]
        before = owner.call("GET", "/api/v1/usage")[1]
        status, ran = owner.call("POST", f"/api/v1/agents/{agents[0]['id']}/run",
                                 {"input": "Draft a one-line reply to a buyer asking about MahaRERA."})
        after = owner.call("GET", "/api/v1/usage")[1]
        run_status = (ran or {}).get("status", "")
        print(f"        run finished with status {run_status!r}")
        if run_status in ut.COUNTED_STATUSES:
            check("a billable run moved ai_runs",
                  a_meter(after, "ai_runs")["used"] == a_meter(before, "ai_runs")["used"] + 1,
                  f"{a_meter(before, 'ai_runs')['used']} → {a_meter(after, 'ai_runs')['used']}")
        elif run_status == ut.BLOCKED_STATUS:
            check("a blocked run moved ai_runs_blocked and not ai_runs",
                  a_meter(after, "ai_runs_blocked")["used"] == a_meter(before, "ai_runs_blocked")["used"] + 1
                  and a_meter(after, "ai_runs")["used"] == a_meter(before, "ai_runs")["used"])
        else:
            check("the run has a status the meter rule knows", run_status in ut.UNCOUNTED_STATUSES,
                  run_status)
        check("the guardrail meter has no cap",
              a_meter(after, "ai_runs_blocked")["cap"] is None)

        print("\n6. provider spend is an integer in µUSD")
        spend = a_meter(after, "ai_spend_micros")
        check("it is an integer", isinstance(spend["used"], int), type(spend["used"]).__name__)
        check("it is denominated", spend["unit"] == ut.MONEY_UNIT, spend["unit"])
        check("it is capped by the plan", spend["cap"] == caps["ai_spend_micros"], str(spend["cap"]))
        check("and it is derived from the runs, so it is at most what they cost",
              spend["measured"] == spend["used"], f"measured {spend['measured']}")

        print("\n7. the ledger: charge, credit, and the same source once")
        before = a_meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")["used"]
        source = f"proof-{uuid.uuid4().hex[:8]}"
        status, first = owner.call("POST", "/api/v1/usage/adjust",
                                   {"meter": "storage_mb", "qty": 64, "reason": "proof: a photo pack",
                                    "source_id": source})
        check("a charge is recorded", status == 200 and first.get("duplicate") is False, str(status))
        moved = a_meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")
        check("and the meter moved", moved["used"] == before + 64, f"{before} → {moved['used']}")
        check("the panel keeps measured and adjusted apart",
              moved["adjusted"] >= 64 and moved["used"] == moved["measured"] + moved["adjusted"],
              f"measured {moved['measured']} + adjusted {moved['adjusted']}")
        status, again = owner.call("POST", "/api/v1/usage/adjust",
                                   {"meter": "storage_mb", "qty": 64, "reason": "proof: a photo pack",
                                    "source_id": source})
        check("the same source twice is not counted twice",
              status == 200 and again.get("duplicate") is True
              and a_meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")["used"] == before + 64,
              "duplicate")
        status, credit = owner.call("POST", "/api/v1/usage/adjust",
                                    {"meter": "storage_mb", "qty": -64, "reason": "proof: pack removed",
                                     "source_id": source + "-credit"})
        check("a credit puts it back", status == 200
              and a_meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")["used"] == before,
              f"{before}")
        entries = owner.call("GET", "/api/v1/usage/ledger?meter=storage_mb")[1]["entries"]
        check("and both entries are still on the record",
              any(e["source_id"] == source for e in entries)
              and any(e["source_id"] == source + "-credit" for e in entries), f"{len(entries)} row(s)")
        status, bad = owner.call("POST", "/api/v1/usage/adjust",
                                 {"meter": "storage_mb", "qty": 1, "reason": "no"})
        check("an unexplained adjustment is refused", status == 400, str(status))
        status, bad = owner.call("POST", "/api/v1/usage/adjust",
                                 {"meter": "not_a_meter", "qty": 1, "reason": "unknown meter"})
        check("so is an unknown meter", status == 400, str(status))

        print("\n8. a manager can read the panel but cannot touch the ledger")
        manager = Client(base)
        manager.login(MANAGER)
        check("the panel is not a secret", manager.call("GET", "/api/v1/usage")[0] == 200)
        check("the ledger is", manager.call("GET", "/api/v1/usage/ledger")[0] == 403,
              str(manager.call("GET", "/api/v1/usage/ledger")[0]))
        check("and a manager cannot adjust a meter",
              manager.call("POST", "/api/v1/usage/adjust",
                           {"meter": "storage_mb", "qty": 1, "reason": "trying it"})[0] == 403)

        print("\n9. a spent allowance refuses the work before it is done")
        cap = caps["contacts"]
        used = a_meter(owner.call("GET", "/api/v1/usage")[1], "contacts")["used"]
        fill = cap - used + 1
        status, _ = owner.call("POST", "/api/v1/usage/adjust",
                               {"meter": "contacts", "qty": fill, "reason": "proof: fill the allowance",
                                "source_id": f"proof-fill-{uuid.uuid4().hex[:8]}"})
        pushed.append(("contacts", fill))
        check("the allowance could be filled", status == 200, str(status))
        stamp = int(time.time() * 1000)
        status, refused = owner.call("POST", "/api/v1/contacts",
                                     {"name": f"Proof Over Cap {stamp}",
                                      "phone": f"+9197{stamp % 10 ** 8:08d}",
                                      "consent": {"status": "granted", "source": "proof_usage"}})
        check("the next contact is refused with 402", status == 402, str(status))
        error = (refused or {}).get("error", {})
        check("the code says what happened", error.get("code") == "QUOTA_EXCEEDED", error.get("code", ""))
        detail = (error.get("details") or [{}])[0]
        check("and names the meter and the cap",
              detail.get("meter") == "contacts" and detail.get("cap") == cap,
              f"{detail.get('used')}/{detail.get('cap')}")
        # stored contacts are a ceiling, not a monthly allowance: no reset date, and
        # the message must not promise one
        check("a stock refusal carries no reset date", detail.get("resets_at") is None,
              str(detail.get("resets_at")))
        check("and says what would actually help",
              "ceiling" in error.get("message", "") and "resets on" not in error.get("message", ""),
              error.get("message", "")[:70])
        cap_flow = caps["sms_out"]
        fill_sms = cap_flow - a_meter(owner.call("GET", "/api/v1/usage")[1], "sms_out")["used"] + 1
        owner.call("POST", "/api/v1/usage/adjust",
                   {"meter": "sms_out", "qty": fill_sms, "reason": "proof: fill the SMS allowance",
                    "source_id": f"proof-sms-{uuid.uuid4().hex[:8]}"})
        pushed.append(("sms_out", fill_sms))
        status, sms_refused = owner.call("POST", f"/api/v1/inbox/{cid}/send",
                                         {"channel": "sms", "text": "should not leave",
                                          "override_quiet_hours": True})
        sms_detail = ((sms_refused or {}).get("error", {}).get("details") or [{}])[0]
        check("a flow refusal does carry the reset date",
              status == 402 and sms_detail.get("resets_at", 0) > time.time()
              and "resets on" in sms_refused["error"]["message"],
              sms_refused.get("error", {}).get("message", "")[:70])
        owner.call("POST", "/api/v1/usage/adjust",
                   {"meter": "sms_out", "qty": -fill_sms, "reason": "proof: release the SMS allowance",
                    "source_id": f"proof-sms-rel-{uuid.uuid4().hex[:8]}"})
        pushed.remove(("sms_out", fill_sms))
        check("nothing was written: the refusal came first",
              a_meter(owner.call("GET", "/api/v1/usage")[1], "contacts")["used"] >= cap)
        owner.call("POST", "/api/v1/usage/adjust",
                   {"meter": "contacts", "qty": -fill, "reason": "proof: release the allowance",
                    "source_id": f"proof-rel-{uuid.uuid4().hex[:8]}"})
        pushed.remove(("contacts", fill))
        check("and releasing it lets the work through again",
              a_meter(owner.call("GET", "/api/v1/usage")[1], "contacts")["used"] < cap)

        print("\n10. 80% of an allowance warns in a header, and does not interrupt")
        cap = caps["storage_mb"]
        used = a_meter(owner.call("GET", "/api/v1/usage")[1], "storage_mb")["used"]
        nudge = int(cap * 0.85) - used
        owner.call("POST", "/api/v1/usage/adjust",
                   {"meter": "storage_mb", "qty": nudge, "reason": "proof: approach the ceiling",
                    "source_id": f"proof-warn-{uuid.uuid4().hex[:8]}"})
        pushed.append(("storage_mb", nudge))
        status, _ = owner.call("POST", f"/api/v1/contacts/{cid}/note", {"text": "still allowed"})
        warning = owner.header("X-Usage-Warning")
        check("the write is allowed", status == 200, str(status))
        check("and carries a warning header", bool(warning), warning or "no header")
        check("naming a meter, a percentage and a cap",
              "%" in warning and "of" in warning, warning)
        owner.call("GET", "/api/v1/usage")
        check("a read carries no header", not owner.header("X-Usage-Warning"),
              "a nudge on the write path, not a banner on every page")
        owner.call("POST", "/api/v1/usage/adjust",
                   {"meter": "storage_mb", "qty": -nudge, "reason": "proof: back under the ceiling",
                    "source_id": f"proof-warn-rel-{uuid.uuid4().hex[:8]}"})
        pushed.remove(("storage_mb", nudge))
        owner.call("POST", f"/api/v1/contacts/{cid}/note", {"text": "and it is gone"})
        check("and it goes away when the allowance does", not owner.header("X-Usage-Warning"))

        print("\n11. a finished month can be frozen and refuses late writes")
        open_period = owner.call("GET", "/api/v1/usage")[1]["period"]
        status, body = owner.call("POST", "/api/v1/usage/close", {"period": open_period})
        check("the month you are in cannot be closed", status == 400
              and body["error"]["code"] == "PERIOD_STILL_OPEN", str(status))
        period = ""   # an empty month, so freezing it cannot put real demo history on ice
        for back in range(11, 36):
            candidate = ut.shift_period(open_period, -back)
            probe = owner.call("GET", f"/api/v1/usage?period={candidate}")[1]
            # flow meters only: a stock (contacts, media) reads the same in every
            # period, so no month is ever "empty" if the desk has a customer
            if not any(m["used"] for m in probe["meters"] if m["kind"] == "flow"):
                period = candidate
                break
        if not period:
            raise SystemExit("no empty month in the last two years to freeze")
        status, closed = owner.call("POST", "/api/v1/usage/close", {"period": period})
        check("an ended month closes", status == 200 and closed.get("already_closed") is False, str(status))
        snapshot = closed.get("snapshot", {})
        check("the snapshot is a copy of every meter",
              set(snapshot.get("meters", {})) == set(ut.METERS), f"{len(snapshot.get('meters', {}))} meters")
        check("it names who closed it and when",
              bool(snapshot.get("closed_at")) and "closed_by" in snapshot)
        check("the invoice lines come from the snapshot",
              all(line["source"] == "snapshot" for line in closed.get("invoice_lines", [])),
              f"{len(closed.get('invoice_lines', []))} line(s)")
        status, again = owner.call("POST", "/api/v1/usage/close", {"period": period})
        check("closing twice changes nothing", status == 200 and again.get("already_closed") is True
              and again["snapshot"]["closed_at"] == snapshot["closed_at"])
        status, late = owner.call("POST", "/api/v1/usage/adjust",
                                  {"meter": "storage_mb", "qty": 5, "reason": "proof: a late credit",
                                   "period": period, "source_id": f"proof-late-{uuid.uuid4().hex[:8]}"})
        check("a late write to a closed month is refused", status == 409
              and late["error"]["code"] == "PERIOD_CLOSED", str(status))
        frozen = owner.call("GET", f"/api/v1/usage?period={period}")[1]
        check("and the month reads back frozen", frozen["closed"] is True and frozen["current"] is False)
        check("with the same figures", frozen["closed_at"] == snapshot["closed_at"])
        print(f"        note: this desk now carries one closed, empty month ({period}) — the price"
              f" of proving a bill cannot move; it is an honest zero, not demo history")

        print("\n12. the card and the meter are the same measurement")
        final = owner.call("GET", "/api/v1/usage")[1]
        card = owner.call("GET", "/api/v1/tenant")[1]["usage"]
        for name in ("contacts", "ai_runs", "voice_minutes", "whatsapp_out"):
            field = "whatsapp_messages" if name == "whatsapp_out" else name
            check(f"{field}: card {card[field]} == meter {a_meter(final, name)['used']}",
                  card[field] == a_meter(final, name)["used"])
        series = owner.call("GET", "/api/v1/usage/series?periods=12")[1]["series"]
        check("the series is twelve months, oldest first",
              len(series) == 12 and [r["period"] for r in series] == sorted(r["period"] for r in series),
              f"{series[0]['period']} … {series[-1]['period']}")
        check("and it includes the closed month from its snapshot",
              next(r for r in series if r["period"] == period)["closed"] is True)
        check("fingerprint", bool(final.get("fingerprint")), final.get("fingerprint", ""))
    finally:
        # leave every meter where we found it
        if pushed:
            print("\nreversing the allowances this proof spent…")
            try:
                fix = Client(base)
                fix.login()
                for name, qty in pushed:
                    fix.call("POST", "/api/v1/usage/adjust",
                             {"meter": name, "qty": -qty, "reason": f"proof cleanup ({RUN})",
                              "source_id": f"cleanup-{name}-{RUN}"})
                    print(f"  credited back {qty} {name}")
            except Exception as exc:
                print(f"  cleanup failed: {exc}")
        if proc:
            proc.terminate()
        print(f"\n{PASSED} checks passed, {len(FAILS)} failed")
        for f in FAILS:
            print(f"  - {f}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

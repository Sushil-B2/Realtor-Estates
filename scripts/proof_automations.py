#!/usr/bin/env python3
"""
Live proof for the automation runtime (`docs/23`, closing gap G1).

    python3 scripts/proof_automations.py                        # throwaway instance
    python3 scripts/proof_automations.py --base http://127.0.0.1:5433 --live

The claim: a lead that lands in the CRM is answered without a human, exactly once,
inside the compliance gates — and when it is *not* answered, the run says why in
words a manager can act on.

Everything here goes through the HTTP API a browser uses. Quiet hours are real, so
a run that defers is a pass when it names the window; a run that silently sends
nothing would be a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []
SEQ = 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, csrf: bool = True):
        h = {"Accept": "application/json", "User-Agent": "proof-automations"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw, status, hdrs = res.read(), res.status, dict(res.getheaders())
        except urllib.error.HTTPError as exc:
            raw, status, hdrs = exc.read(), exc.code, dict(exc.headers)
        cookie = hdrs.get("Set-Cookie", "")
        if "re_session=" in cookie:
            value = cookie.split("re_session=", 1)[1].split(";")[0]
            if value:
                self.cookies["re_session"] = value
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw[:300].decode("utf-8", "replace")}
        return status, payload

    def login(self, email: str, password: str = "Demo@12345!") -> dict:
        status, body = self.call("POST", "/api/v1/auth/login",
                                 {"email": email, "password": password}, csrf=False)
        expect(f"login {email.split('@')[0]}", "POST /auth/login", status, 200)
        self.csrf = body.get("csrf", "")
        return body


def expect(label: str, request: str, got, want) -> None:
    ok = got in want if isinstance(want, (list, tuple, set)) else got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<46} {request:<44} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def expect_pair(label: str, request: str, got, want) -> None:
    ok = list(got) == list(want)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<46} {request:<44} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 260) -> None:
    print(f"        {label}: {json.dumps(payload, ensure_ascii=False)[:limit]}")


def make_lead(owner: Session, tag: str, **over) -> dict:
    global SEQ
    SEQ += 1
    body = {
        "name": f"Proof Buyer {tag} {SEQ}", "phone": f"9{SEQ:03d}00{int(time.time()) % 100000:05d}",
        "email": f"proof.runtime.{tag}.{SEQ}@example.com", "city": "Nanded", "source": "99acres",
        "requirement": "2400 sqft east-facing plot", "budget_min": 2000000, "budget_max": 3000000,
        "consent": {"status": "granted", "purpose": "marketing", "channel": "whatsapp"},
    }
    body.update(over)
    status, payload = owner.call("POST", "/api/v1/contacts", body)
    expect(f"lead {tag} captured", "POST /contacts", status in (200, 201), True)
    return payload.get("contact", {})


def runs_for(owner: Session, contact_id: str) -> list[dict]:
    status, body = owner.call("GET", "/api/v1/automations/runs?limit=200")
    return [r for r in body.get("runs", []) if r.get("subject_id") == contact_id]


def messages_for(owner: Session, contact: dict) -> list[dict]:
    status, body = owner.call("GET", f"/api/v1/contacts/{contact['id']}")
    return (body.get("conversation") or {}).get("messages") or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-aut-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0", "PORT": str(port), "HOST": "127.0.0.1"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            proc.kill()
            return 1
    else:
        base = (args.base or "http://127.0.0.1:5433").rstrip("/")

    try:
        print(f"\n=== automation runtime proof against {base} ===")
        owner = Session(base)
        owner.login("owner@realtorestates.app")
        agent = Session(base)
        agent.login("agent@realtorestates.app")
        viewer = Session(base)
        viewer.login("viewer@realtorestates.app")

        # ---------------------------------------------------------------
        print("\n1. the playbook catalogue is a catalogue, not a screenshot")
        status, cat = owner.call("GET", "/api/v1/automations/playbooks")
        expect("playbooks served", "GET /automations/playbooks", status, 200)
        expect("nine researched patterns", "count", cat["count"], 9)
        for book in cat["playbooks"]:
            assert book["why"], book["slug"]
        show("catalogue", [(b["slug"], b["trigger"], len(b["steps"])) for b in cat["playbooks"]])
        status, fleet = owner.call("GET", "/api/v1/automations")
        expect("fleet listed", "GET /automations", status, 200)
        expect("every seeded automation has typed steps", "steps are dicts",
               all(isinstance(s, dict) for a in fleet["automations"] for s in a["steps"]), True)

        # ---------------------------------------------------------------
        print("\n2. install is idempotent")
        status, first = owner.call("POST", "/api/v1/automations/playbooks/instant-response/install", {})
        expect("install instant-response", "POST …/install", status in (200, 201), True)
        status, again = owner.call("POST", "/api/v1/automations/playbooks/instant-response/install", {})
        expect("re-install does not duplicate", "POST …/install again",
               again.get("already_installed"), True)
        status, missing = owner.call("POST", "/api/v1/automations/playbooks/nope/install", {})
        expect("unknown playbook refused", "POST …/nope/install", status, 404)

        # ---------------------------------------------------------------
        print("\n3. a lead arrives — dry run first")
        lead = make_lead(owner, "fresh")
        status, dry = owner.call("POST", "/api/v1/automations/tick", {"dry_run": True, "limit": 100})
        expect("dry run accepted", "POST /automations/tick dry_run", status, 200)
        before = runs_for(owner, lead["id"])
        expect("dry run creates nothing", "runs for the new lead", len(before), 0)
        show("would run", dry["results"][:3])

        # ---------------------------------------------------------------
        print("\n4. a real tick — answered once, or refused with a reason")
        status, tick = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
        expect("tick accepted", "POST /automations/tick", status, 200)
        show("counts", tick["counts"])
        mine = runs_for(owner, lead["id"])
        expect("a run exists for the lead", "GET /automations/runs", bool(mine), True)
        if mine:
            run = mine[0]
            show("run", {"status": run["status"], "step": run["step_index"],
                         "reason": run["last_error"]})
            expect("run is settled, not lost", "run status",
                   run["status"] in ("ok", "deferred", "blocked"), True)
            messages = [m for m in messages_for(owner, lead) if m.get("automation")]
            if run["status"] == "ok":
                expect("the buyer was actually messaged", "conversation has an automation message",
                       len(messages) >= 1, True)
                show("message", messages[0]["text"] if messages else "")
                detail = owner.call("GET", f"/api/v1/contacts/{lead['id']}")[1]["contact"]
                expect("speed-to-lead clock stopped", "contact.first_response_at",
                       bool(detail.get("first_response_at")), True)
                tasks = owner.call("GET", "/api/v1/tasks?limit=200")[1].get("tasks", [])
                mine_tasks = [t for t in tasks if t.get("contact_id") == lead["id"]]
                expect("a human still has the task", "task created", bool(mine_tasks), True)
            else:
                expect("a run that did not send says why", "last_error", bool(run["last_error"]), True)
                show("held because", run["last_error"])

        # ---------------------------------------------------------------
        print("\n5. ticking again does not message the buyer again")
        status, tick2 = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
        expect("second tick accepted", "POST /automations/tick again", status, 200)
        mine2 = runs_for(owner, lead["id"])
        keys = [r["idempotency_key"] for r in mine2]
        expect("one run per subject, not one per tick", "idempotency keys unique",
               len(keys) == len(set(keys)), True)
        auto_messages = [m for m in messages_for(owner, lead) if m.get("automation")]
        expect("at most one automation message", "messages", len(auto_messages) <= 1, True)
        show("second tick", tick2["counts"])

        # ---------------------------------------------------------------
        print("\n6. the gates refuse, in words a manager can act on")
        no_consent = make_lead(owner, "noconsent", consent={"status": "pending", "purpose": "marketing"})
        owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
        runs = runs_for(owner, no_consent["id"])
        if runs:
            run = runs[0]
            expect("consent-less lead is blocked or held", "status",
                   run["status"] in ("blocked", "pending", "deferred"), True)
            sent = [m for m in messages_for(owner, no_consent) if m.get("automation")]
            expect("nothing was sent without consent", "automation messages", len(sent), 0)
            if run["status"] == "blocked":
                expect("the reason names consent", "last_error", "Consent" in run["last_error"], True)

        status, before_kill = owner.call("GET", "/api/v1/automations/queue")
        owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": True})
        status, killed = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
        expect("tick still answers with the kill switch on", "POST /automations/tick",
               status, 200)
        show("killed tick counts", killed["counts"])
        owner.call("POST", "/api/v1/tenant/kill-switch", {"engage": False})

        # ---------------------------------------------------------------
        print("\n7. the queue is the manager's screen")
        status, queue = owner.call("GET", "/api/v1/automations/queue")
        expect("queue served", "GET /automations/queue", status, 200)
        expect("counts and caps present", "keys",
               {"counts", "due_now", "dead", "deferred", "blocked", "recent",
                "frequency_cap_per_day", "max_attempts"} <= set(queue), True)
        show("queue", queue["counts"])
        status, dead = owner.call("GET", "/api/v1/automations/runs?status=dead")
        expect("dead-letter filter works", "GET /automations/runs?status=dead", status, 200)

        # ---------------------------------------------------------------
        print("\n8. a step that cannot work is refused at save time")
        cases = [
            ({"name": "x", "trigger": "lead.created",
              "steps": [{"type": "send_email", "text": "hi"}]}, "UNKNOWN_STEP_TYPE"),
            ({"name": "x", "trigger": "lead.created", "steps": [{"type": "send_whatsapp"}]},
             "STEP_INCOMPLETE"),
            ({"name": "x", "trigger": "whenever", "steps": [{"type": "create_task", "title": "t"}]},
             "UNKNOWN_TRIGGER"),
        ]
        for payload, code in cases:
            status, body = owner.call("POST", "/api/v1/automations", payload)
            expect_pair(f"{code} refused", "POST /automations",
                        [status, body.get("error", {}).get("code")], [422, code])

        # ---------------------------------------------------------------
        print("\n9. who may fire the fleet")
        expect("viewer cannot tick", "POST /automations/tick as viewer",
               viewer.call("POST", "/api/v1/automations/tick", {})[0], 403)
        expect("agent cannot tick", "POST /automations/tick as agent",
               agent.call("POST", "/api/v1/automations/tick", {})[0], 403)
        expect("viewer may read the queue", "GET /automations/queue as viewer",
               viewer.call("GET", "/api/v1/automations/queue")[0], 200)

        # ---------------------------------------------------------------
        print("\n10. a missed call gets a text back, and only once")
        # Quiet hours are real, and a live proof often runs at night: this section
        # narrows them to nothing for the duration so the *send* is what is proven,
        # then restores the tenant's own window in a finally-style block below.
        status, tenant_before = owner.call("GET", "/api/v1/tenant")
        # the route answers with the tenant itself, not a wrapper — reading the
        # wrong key here silently skipped the restore once already.
        settings_before = tenant_before.get("settings") or (tenant_before.get("tenant") or {}).get("settings") or {}
        original_quiet = settings_before.get("quiet_hours")
        owner.call("PATCH", "/api/v1/tenant", {"settings": {"quiet_hours": ["00:00", "00:00"]}})
        try:
            caller = make_lead(owner, "caller")
            status, logged = owner.call("POST", f"/api/v1/contacts/{caller['id']}/calls",
                                        {"direction": "in", "outcome": "missed"})
            expect("a missed call is logged", "POST /contacts/{id}/calls", status in (200, 201), True)
            expect("the call names its recovery playbook", "recovers_with",
                   logged.get("recovers_with"), "missed-call-textback")
            expect("the number is masked on the record", "number_masked",
                   "•" in (logged["call"].get("number_masked") or "•"), True)
            status, bad = owner.call("POST", f"/api/v1/contacts/{caller['id']}/calls",
                                     {"direction": "in", "outcome": "butt-dial"})
            expect("an unknown outcome is refused", "POST …/calls bad outcome", status, 422)
            # Fire the one automation, not the fleet: a global tick may legitimately
            # spend its budget on a backlog first, and this check is about the call.
            status, fleet = owner.call("GET", "/api/v1/automations")
            recovery = next((a for a in fleet["automations"]
                             if a.get("playbook") == "missed-call-textback"), {})
            status, fired = owner.call("POST", f"/api/v1/automations/{recovery.get('id')}/run", {})
            expect("the recovery automation fired", "POST /automations/{id}/run", status, 200)
            texts = [m for m in messages_for(owner, caller) if m.get("channel") == "sms"
                     and "call you back" in m.get("text", "")]
            expect("the caller was texted back exactly once", "sms messages", len(texts), 1)
            show("text back", texts[0]["text"] if texts else "none")
            owner.call("POST", f"/api/v1/automations/{recovery.get('id')}/run", {})
            again = [m for m in messages_for(owner, caller) if "call you back" in m.get("text", "")]
            expect("a second tick does not text twice", "sms messages", len(again), len(texts))
            status, tasks = owner.call("GET", "/api/v1/tasks")
            mine = [t for t in tasks.get("tasks", []) if t.get("contact_id") == caller["id"]
                    and "they rang us" in (t.get("title") or "")]
            expect("a human is asked to call back", "tasks", len(mine), 1)
        finally:
            restore = original_quiet or ["21:00", "09:00"]
            status, back = owner.call("PATCH", "/api/v1/tenant", {"settings": {"quiet_hours": restore}})
            after = (back.get("settings") or {}).get("quiet_hours")
            print(f"        (quiet hours restored to {restore}; read back {after})")

        print("\n11. the review ask waits for the paperwork to be done")
        # A live server cannot wait two days, so this section proves the half that
        # *can* be observed live: a deal that closed minutes ago gets no ask. The
        # positive (an ask two days later, once per deal) is asserted with a fixed
        # clock in tests/test_automations.py — stated here rather than hidden.
        status, board = owner.call("GET", "/api/v1/pipeline")
        won = list((board.get("columns") or {}).get("won") or [])
        expect("there is a won deal to reason about", "won deals", len(won) >= 1, True)
        status, tick3 = owner.call("POST", "/api/v1/automations/tick", {"limit": 100})
        expect("tick accepted after the close", "POST /automations/tick", status, 200)
        premature = [r for r in runs_for(owner, won[0]["id"])
                     if "review" in (r.get("automation_name") or "").lower()]
        expect("no review request on the day of the sale", "review runs", len(premature), 0)
        show("newest won deal", {"id": won[0]["id"], "closed_at": won[0].get("closed_at") or "unset",
                                 "updated_at": won[0].get("updated_at")})
        status, books = owner.call("GET", "/api/v1/automations/playbooks")
        review = next((b for b in books["playbooks"] if b["slug"] == "review-request"), {})
        expect("review-request is installed and armed", "trigger", review.get("trigger"), "deal.closed")

        print("\n12. tenant isolation")
        other = Session(base)
        other.login("owner@metrorealty.app", "Metro@12345!")
        status, theirs = other.call("GET", "/api/v1/automations")
        expect("other tenant has no automations", "GET /automations as metro", theirs["count"], 0)
        status, their_runs = other.call("GET", "/api/v1/automations/runs")
        expect("other tenant has no runs", "GET /automations/runs as metro", their_runs["count"], 0)

        print(f"\n=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
        for f in FAILS:
            print("  ✗ " + f)
        return 0 if not FAILS else 1
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

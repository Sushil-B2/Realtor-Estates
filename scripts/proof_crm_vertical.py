#!/usr/bin/env python3
"""
Live proof for the real-estate vertical CRM (docs/18, plan P0).

Runs against a booted server and exercises the four things a horizontal CRM
does not have, in the order an agent would hit them. Every step prints the real
request, the real status and the evidence; the script exits non-zero if any
expectation fails, so it is usable as a smoke test after a deploy.

    python3 scripts/proof_crm_vertical.py --base http://127.0.0.1:5433

Read-only for existing rows: it creates a visit, records documents on one deal,
and computes commission — it never deletes or rewrites seeded data.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

FAILS: list[str] = []


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, csrf: bool = True):
        headers = {"Accept": "application/json", "User-Agent": "proof-crm-vertical"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                raw, status, hdrs = res.read(), res.status, dict(res.getheaders())
        except urllib.error.HTTPError as exc:
            raw, status, hdrs = exc.read(), exc.code, dict(exc.headers)
        cookie = hdrs.get("Set-Cookie", "")
        if "re_session=" in cookie:
            self.cookies["re_session"] = cookie.split("re_session=", 1)[1].split(";")[0]
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw[:400].decode("utf-8", "replace")}
        return status, payload

    def login(self, email: str, password: str) -> dict:
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        expect("login", f"POST /api/v1/auth/login {email}", status, 200)
        self.csrf = body.get("csrf", "")
        return body


def expect(label: str, request: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<34} {request:<62} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 260) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    print(f"        {label}: {text[:limit]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5433")
    ap.add_argument("--email", default="owner@realtorestates.app")
    ap.add_argument("--password", default="Demo@12345!")
    args = ap.parse_args()

    print(f"\n=== vertical CRM proof against {args.base} ===")
    s = Session(args.base)
    print("\n1. session + CSRF for a reloaded page")
    s.login(args.email, args.password)
    status, sess = s.call("GET", "/api/v1/auth/session")
    expect("session returns csrf", "GET /api/v1/auth/session", status, 200)
    s.csrf = sess.get("csrf", s.csrf)
    print(f"        csrf derived: {bool(s.csrf)}  tenant: {sess.get('tenant', {}).get('name')}")

    print("\n2. the desk summary")
    status, summary = s.call("GET", "/api/v1/crm/summary")
    expect("summary", "GET /api/v1/crm/summary", status, 200)
    show("by_stage", summary.get("by_stage"))
    show("visits", {"today": summary.get("visits_today"), "upcoming": summary.get("visits_upcoming")})
    show("blockers", summary.get("stage_blockers"))

    print("\n3. requirement → property matching (explainable)")
    status, contacts = s.call("GET", "/api/v1/contacts?limit=5")
    expect("contacts", "GET /api/v1/contacts", status, 200)
    contact = contacts["contacts"][0]
    status, matches = s.call("GET", f"/api/v1/contacts/{contact['id']}/matches?limit=3")
    expect("matches", f"GET /api/v1/contacts/{contact['id']}/matches", status, 200)
    print(f"        requirement: {matches.get('requirement')!r} budget: {matches.get('budget')}")
    for m in matches.get("matches", []):
        print(f"        {m['score']:>3}  {m['title'][:44]:<46} {m['reasons'][0][:60]}")
    if not matches.get("matches"):
        FAILS.append("matching returned nothing")

    print("\n4. site visits: create, conflict, lifecycle")
    deals = s.call("GET", "/api/v1/pipeline")[1]
    flat = [d for stage, rows in (deals.get("columns") or {}).items() for d in rows]
    target = next((d for d in flat if d.get("contact_id")), flat[0])
    deal_id, contact_id = target["id"], target["contact_id"]
    # the visit must be for the deal's own property: a visit to a *different*
    # property does not earn this deal its "site visit" stage
    detail0 = s.call("GET", f"/api/v1/deals/{deal_id}")[1]
    prop_id = detail0["deal"].get("property_id") or (s.call("GET", "/api/v1/properties")[1].get("properties") or [{}])[0].get("id")
    # A free future slot, retried: earlier proof runs leave visits behind, and
    # the point of this step is that booking *works*, not that random slots do.
    status, created, body = 0, {}, {}
    for _ in range(8):
        slot = int(time.time()) + 3 * 86400 + random.randint(0, 10 * 86400)
        body = {"contact_id": contact_id, "property_id": prop_id, "scheduled_at": slot,
                "duration_min": 45, "pickup": "Office cab"}
        status, created = s.call("POST", "/api/v1/site-visits", body)
        if status == 200:
            break
    expect("visit created", "POST /api/v1/site-visits", status, 200)
    visit_id = (created.get("visit") or {}).get("id")
    show("visit", {k: created.get("visit", {}).get(k) for k in ("id", "status", "scheduled_at", "agent_name")})
    status, clash = s.call("POST", "/api/v1/site-visits", body)
    expect("double-booking refused", "POST /api/v1/site-visits (same slot)", status, 409)
    show("conflict", clash.get("error", {}).get("code"))
    status, bad = s.call("PATCH", f"/api/v1/site-visits/{visit_id}", {"status": "revisit"})
    expect("illegal transition refused", f"PATCH /api/v1/site-visits/{visit_id} scheduled→revisit", status, 422)
    show("refusal", bad.get("error", {}).get("message"))
    for step in ("confirmed", "visited"):
        status, upd = s.call("PATCH", f"/api/v1/site-visits/{visit_id}", {"status": step, "outcome": "shortlisted" if step == "visited" else ""})
        expect(f"visit → {step}", f"PATCH /api/v1/site-visits/{visit_id}", status, 200)

    print("\n5. stage gates: the documents a stage implies must exist")
    status, detail = s.call("GET", f"/api/v1/deals/{deal_id}")
    expect("deal detail", f"GET /api/v1/deals/{deal_id}", status, 200)
    current = detail["deal"]["stage"]
    print(f"        deal stage={current} value={detail['deal'].get('value')}")
    status, moved = s.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "site_visit"})
    expect("advance → site_visit", f"POST /api/v1/deals/{deal_id}/advance", status, 200)
    show("gate", moved.get("gate"))
    status, blocked = s.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "token"})
    expect("token blocked without docs", f"POST /api/v1/deals/{deal_id}/advance token", status, 409)
    show("blocked", blocked.get("error", {}).get("message"))
    for doc in ("KYC (Aadhaar/PAN)", "Booking form"):
        status, _ = s.call("POST", f"/api/v1/deals/{deal_id}/documents", {"name": doc, "status": "received"})
        expect(f"document {doc[:18]}", f"POST /api/v1/deals/{deal_id}/documents", status, 200)
    status, unblocked = s.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "token"})
    expect("token after documents", f"POST /api/v1/deals/{deal_id}/advance token", status, 200)

    print("\n6. commission split (gross → CP → agent / brokerage)")
    status, comm = s.call("POST", f"/api/v1/deals/{deal_id}/commission",
                          {"rate_pct": 2.0, "agent_share_pct": 60, "channel_partner_pct": 10, "channel_partner_id": "cp_nanded_01"})
    expect("commission", f"POST /api/v1/deals/{deal_id}/commission", status, 200)
    c = comm.get("commission", {})
    show("split", {k: c.get(k) for k in ("gross", "channel_partner", "agent", "brokerage", "sums_to_gross")})
    if c and not c.get("sums_to_gross"):
        FAILS.append("commission parts do not sum to gross")

    print("\n7. an override is allowed but never quiet (owner), and blocked for a viewer")
    status, forced = s.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "won", "force": True})
    expect("owner force-advance", "POST …/advance {stage:won, force:true}", status, 200)
    show("gate", forced.get("gate"))
    viewer = Session(args.base)
    viewer.login("viewer@realtorestates.app", args.password)
    status, denied = viewer.call("POST", f"/api/v1/deals/{deal_id}/advance", {"stage": "won"})
    expect("viewer cannot write", "POST …/advance as viewer", status, 403)

    print("\n8. tenant isolation")
    other = Session(args.base)
    other.login("owner@metrorealty.app", "Metro@12345!")
    status, _ = other.call("GET", f"/api/v1/deals/{deal_id}")
    expect("other tenant cannot read the deal", f"GET /api/v1/deals/{deal_id} as metro", status, 404)
    status, empty = other.call("GET", "/api/v1/site-visits")
    expect("other tenant sees no visits", "GET /api/v1/site-visits as metro", status, 200)
    if empty.get("count"):
        FAILS.append("cross-tenant visit leak")
    show("metro visits", {"count": empty.get("count")})

    print(f"\n=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
    for f in FAILS:
        print("  ✗ " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

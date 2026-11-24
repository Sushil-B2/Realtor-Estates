#!/usr/bin/env python3
"""
Live proof for portal ingestion, de-duplication, assignment and speed-to-lead
(docs/18 §8 P1). Runs against a booted server; exits non-zero on any failure.

    python3 scripts/proof_portal_ingest.py --base http://127.0.0.1:5433

It creates one ingest key, two portal leads from the same phone number on two
different portals, then revokes the key. Nothing is deleted, and the key token is
never printed in full — only its prefix, the same masking the API does.
"""
from __future__ import annotations

import argparse
import json
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

    def call(self, method: str, path: str, body=None, headers=None, csrf: bool = True):
        h = {"Accept": "application/json", "User-Agent": "proof-portal-ingest"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=h,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
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

    def login(self, email: str, password: str) -> dict:
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        expect("login", f"POST /auth/login {email}", status, 200)
        self.csrf = body.get("csrf", "")
        return body


def expect(label: str, request: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<36} {request:<58} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 300) -> None:
    print(f"        {label}: {json.dumps(payload, ensure_ascii=False)[:limit]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5433")
    ap.add_argument("--email", default="owner@realtorestates.app")
    ap.add_argument("--password", default="Demo@12345!")
    args = ap.parse_args()

    print(f"\n=== portal ingestion proof against {args.base} ===")
    owner = Session(args.base)
    owner.login(args.email, args.password)

    # A phone number that is new on every run, so the duplicate test is real.
    suffix = str(int(time.time()))[-6:]
    phone_plain = "9" + str(int(time.time()))[-9:]          # exactly 10 digits
    phone_e164 = "+91" + phone_plain

    print("\n1. mint an ingest key (returned once, stored hashed)")
    status, minted = owner.call("POST", "/api/v1/tenant/ingest-keys", {"label": "99acres production", "source": "99acres"})
    expect("key minted", "POST /tenant/ingest-keys", status, 200)
    token = minted.get("token", "")
    key_id = (minted.get("key") or {}).get("id", "")
    print(f"        token prefix: {token[:12]}… (len {len(token)}) endpoint: {minted.get('endpoint')}")
    status, listing = owner.call("GET", "/api/v1/tenant/ingest-keys")
    expect("listing", "GET /tenant/ingest-keys", status, 200)
    leaked = token in json.dumps(listing)
    expect("key material not in listing", "token absent from GET response", leaked, False)
    show("keys", [{k: r.get(k) for k in ("label", "prefix", "count", "active")} for r in listing.get("keys", [])[:3]])

    print("\n2. a portal posts a lead (messy payload: ₹ and spaces in the number)")
    payload = {
        "sender_name": "Portal Proof Buyer", "sender_phone": f"{phone_plain[:5]} {phone_plain[5:]}",
        "sender_email": "proof@example.com", "city": "Nanded", "locality": "Taroda",
        "budget": "₹30,00,000", "message": "2400 sqft east facing plot", "lead_id": f"99a-{suffix}",
        "campaign": "Sunrise Meadows — Phase 2",
    }
    status, created = owner.call("POST", "/api/v1/ingest/99acres", payload, headers={"X-Ingest-Key": token})
    expect("lead ingested", "POST /ingest/99acres", status, 200)
    show("result", created)
    contact_id = created.get("contact_id", "")
    if not created.get("sla", {}).get("due_at"):
        FAILS.append("no SLA clock on the new lead")
    if not created.get("assigned_to"):
        FAILS.append("lead was not assigned to an agent")

    print("\n3. the same buyer enquires on a second portal → merged, not duplicated")
    status, dup = owner.call("POST", "/api/v1/ingest/magicbricks", {
        "contactName": "Portal Proof Buyer", "mobile": phone_e164,
        "budget": "40-60 Lakh", "enquiryId": f"mb-{suffix}",
    }, headers={"X-Ingest-Key": token})
    expect("repeat enquiry", "POST /ingest/magicbricks (same phone)", status, 200)
    show("merged", {"duplicate": dup.get("duplicate"), "contact_id": dup.get("contact_id"),
                    "sources": [s.get("slug") for s in (dup.get("sources") or [])]})
    expect("same contact", "duplicate contact_id equals first", dup.get("contact_id"), contact_id)
    expect("duplicate flag", "duplicate", dup.get("duplicate"), True)

    print("\n4. the clock: it is running, then it is stopped")
    status, board = owner.call("GET", "/api/v1/leads/sla")
    expect("sla board", "GET /leads/sla", status, 200)
    open_ids = [r["contact_id"] for r in board.get("open", [])]
    expect("new lead inside its window", "contact present in open list", contact_id in open_ids, True)
    show("counts", board.get("counts"))
    status, responded = owner.call("POST", f"/api/v1/leads/{contact_id}/respond", {"channel": "call"})
    expect("first reply logged", f"POST /leads/{contact_id}/respond", status, 200)
    show("sla", responded.get("sla"))
    status, again = owner.call("POST", f"/api/v1/leads/{contact_id}/respond", {"channel": "call"})
    expect("second reply is idempotent", "POST …/respond again", again.get("already_responded"), True)

    print("\n5. keys are refused when wrong or revoked")
    status, _ = owner.call("POST", "/api/v1/ingest/99acres", payload)
    expect("no key", "POST /ingest/99acres without key", status, 401)
    status, body = owner.call("POST", "/api/v1/ingest/99acres", payload, headers={"X-Ingest-Key": "rink_not-a-real-key-000000"})
    expect("wrong key", "POST /ingest/99acres with a fake key", status, 401)
    show("error", body.get("error", {}).get("code"))
    status, _ = owner.call("DELETE", f"/api/v1/tenant/ingest-keys/{key_id}")
    expect("key revoked", f"DELETE /tenant/ingest-keys/{key_id}", status, 200)
    status, _ = owner.call("POST", "/api/v1/ingest/99acres", payload, headers={"X-Ingest-Key": token})
    expect("revoked key refused", "POST /ingest/99acres with the revoked key", status, 401)

    print("\n6. who may do what")
    viewer = Session(args.base)
    viewer.login("viewer@realtorestates.app", args.password)
    status, _ = viewer.call("POST", "/api/v1/tenant/ingest-keys", {"label": "nope"})
    expect("viewer cannot mint", "POST /tenant/ingest-keys as viewer", status, 403)
    other = Session(args.base)
    other.login("owner@metrorealty.app", "Metro@12345!")
    status, body = other.call("GET", "/api/v1/leads/sla")
    theirs = {r["contact_id"] for r in body.get("open", []) + body.get("breached", [])}
    expect("other tenant cannot see our lead", "contact absent from metro's SLA board", contact_id in theirs, False)
    status, _ = other.call("DELETE", f"/api/v1/tenant/ingest-keys/{key_id}")
    expect("other tenant cannot revoke our key", f"DELETE /tenant/ingest-keys/{key_id} as metro", status, 404)

    print("\n7. audit trail")
    status, audit = owner.call("GET", "/api/v1/tenant/audit")
    actions = [e.get("action") for e in (audit.get("entries") or [])]
    for action in ("lead.ingested", "lead.duplicate", "lead.first_response", "ingest_key.created", "ingest_key.revoked"):
        expect(f"audited: {action}", "GET /tenant/audit contains the action", action in actions, True)
    print(f"        chain: {audit.get('chain')}")

    print(f"\n=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
    for f in FAILS:
        print("  ✗ " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

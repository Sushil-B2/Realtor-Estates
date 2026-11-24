#!/usr/bin/env python3
"""
Live proof for the channel-partner portal (docs/18 §4 #3, §6 #5).

    python3 scripts/proof_channel_partners.py                     # boots a throwaway, seeded server
    python3 scripts/proof_channel_partners.py --base http://127.0.0.1:5433 --live

The default mode is deliberate: the interesting parts (an approved claim, a
seeded closed deal with a partner share, a payout) need data that a long-lived
database may not have, so the proof boots its own instance on a temporary data
directory and shows the whole lifecycle. `--base ... --live` runs the same checks
against a deployment and skips the payout section if there is nothing to pay.

Prints only prefixes and amounts; never a password or a full token beyond the one
temporary password it immediately uses to sign in.
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
        h = {"Accept": "application/json", "User-Agent": "proof-channel-partners"}
        if method != "GET":
            # a proof says it is a proof, so a demo can be reseeded (docs/26)
            h["X-Proof-Run"] = f"partners-{int(time.time())}"
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
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

    def login(self, email: str, password: str, label: str = "") -> dict:
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        expect(f"login {label or email}", f"POST /auth/login {email}", status, 200)
        self.csrf = body.get("csrf", "")
        return body


def expect(label: str, request: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<40} {request:<54} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 280) -> None:
    print(f"        {label}: {json.dumps(payload, ensure_ascii=False)[:limit]}")


_seq = 0


def new_number() -> str:
    global _seq
    _seq += 1
    return "+9198" + str(int(time.time() * 1000))[-6:] + f"{_seq % 100:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true", help="run against --base and skip the payout section")
    args = ap.parse_args()

    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-cp-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "PORT": str(port), "HOST": "127.0.0.1"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        print(f"\n=== channel-partner proof against {base} ===")
        owner = Session(base)
        owner.login("owner@realtorestates.app", "Demo@12345!", "owner")
        manager = Session(base)
        manager.login("manager@realtorestates.app", "Demo@12345!", "manager")
        agent = Session(base)
        agent.login("agent@realtorestates.app", "Demo@12345!", "agent")

        print("\n1. the house creates a partner (temporary password shown once)")
        stamp = int(time.time())
        status, created = owner.call("POST", "/api/v1/cp/partners", {
            "name": "Proof Property Mart", "email": f"proof{stamp}@partner.in",
            "firm": "Proof Property Mart", "commission_pct": 11.5, "protection_days": 90, "phone": "9876543210",
        })
        expect("partner created", "POST /cp/partners", status, 200)
        partner_meta = created.get("partner", {})
        show("partner", {k: partner_meta.get(k) for k in ("firm", "commission_pct", "protection_days", "active")})
        cp1 = Session(base)
        cp1.login(f"proof{stamp}@partner.in", created.get("temp_password", ""), "new partner")

        status, made2 = owner.call("POST", "/api/v1/cp/partners", {
            "name": "Rival Brokers", "email": f"rival{stamp}@partner.in", "firm": "Rival Brokers",
            "commission_pct": 10, "protection_days": 90,
        })
        expect("second partner created", "POST /cp/partners", status, 200)
        cp2 = Session(base)
        cp2.login(f"rival{stamp}@partner.in", made2.get("temp_password", ""), "rival partner")

        print("\n2. a partner login is confined to the partner portal")
        for path in ("/api/v1/contacts", "/api/v1/deals/deal_x", "/api/v1/tenant/audit", "/api/v1/crm/summary"):
            status, body = cp1.call("GET", path)
            expect(f"blocked {path}", f"GET {path} as partner", status, 403)
        status, me = cp1.call("GET", "/api/v1/cp/me")
        expect("own portal works", "GET /cp/me", status, 200)
        show("counts", me.get("counts"))

        print("\n3. register a buyer: a claim with a clock")
        number = new_number()
        status, reg = cp1.call("POST", "/api/v1/cp/register",
                               {"name": "Claimed Buyer", "phone": number, "city": "Nanded",
                                "requirement": "2400 sqft plot", "budget_max": 4_500_000})
        expect("claim submitted", "POST /cp/register", status, 200)
        show("registration", {"id": (reg.get("registration") or {}).get("id"), "status": (reg.get("registration") or {}).get("status")})
        status, dup = cp1.call("POST", "/api/v1/cp/register", {"name": "Claimed Buyer", "phone": number})
        expect("same partner, same buyer", "POST /cp/register again", dup.get("outcome"), "duplicate")

        status, blocked = cp2.call("POST", "/api/v1/cp/register", {"name": "Claimed Buyer", "phone": number})
        expect("rival partner refused", "POST /cp/register as rival", status, 409)
        show("refusal", blocked.get("error", {}).get("code"))

        print("\n4. only a manager can approve; approval creates the protection window")
        reg_id = reg["registration"]["id"]
        status, _ = agent.call("POST", f"/api/v1/cp/registrations/{reg_id}/decide", {"decision": "approve"})
        expect("agent cannot approve", "POST …/decide as agent", status, 403)
        status, decided = manager.call("POST", f"/api/v1/cp/registrations/{reg_id}/decide", {"decision": "approve"})
        expect("manager approves", "POST …/decide as manager", status, 200)
        approved = decided.get("registration", {})
        days_left = round((approved.get("protection_until", 0) - time.time()) / 86400)
        show("protection", {"status": approved.get("status"), "days_left": days_left})
        expect("protection window is ~90 days", "protection_until - now", 89 <= days_left <= 90, True)

        status, _ = manager.call("POST", f"/api/v1/cp/registrations/{reg_id}/decide", {"decision": "reject"})
        expect("cannot decide twice", "POST …/decide again", status, 409)

        print("\n5. a rejected claim releases the buyer")
        other = new_number()
        reg2 = cp1.call("POST", "/api/v1/cp/register", {"name": "Releasable", "phone": other})[1]["registration"]
        status, rej = manager.call("POST", f"/api/v1/cp/registrations/{reg2['id']}/decide",
                                   {"decision": "reject", "reason": "buyer already speaking to the house"})
        expect("claim rejected", "POST …/decide reject", status, 200)
        status, retaken = cp2.call("POST", "/api/v1/cp/register", {"name": "Releasable", "phone": other})
        expect("rival may now claim", "POST /cp/register after rejection", status, 200)

        print("\n6. the house's own lead is not claimable")
        house_number = new_number()
        status, house = owner.call("POST", "/api/v1/contacts", {"name": "House Owned", "phone": house_number})
        expect("house lead created", "POST /contacts", status, 200)
        status, refusal = cp1.call("POST", "/api/v1/cp/register", {"name": "House Owned", "phone": house_number})
        expect("claim refused", "POST /cp/register for a house lead", status, 409)
        show("refusal", refusal.get("error", {}).get("code"))

        print("\n7. what the partner can see of their own business")
        status, leads = cp1.call("GET", "/api/v1/cp/leads")
        expect("own buyers", "GET /cp/leads", status, 200)
        show("leads", {"count": leads.get("count"),
                       "statuses": sorted({row["status"] for row in leads.get("leads", [])}),
                       "visits_credited": [row["visits_credited"] for row in leads.get("leads", [])]})
        # a buyer only the rival ever touched must be invisible to me
        rivals_only = new_number()
        cp2.call("POST", "/api/v1/cp/register", {"name": "Rival Only", "phone": rivals_only})
        mine = {row["name"] for row in cp1.call("GET", "/api/v1/cp/leads")[1].get("leads", [])}
        expect("partners do not see each other's buyers", "rival-only buyer absent from my list",
               "Rival Only" in mine, False)
        # live credits never overlap
        live = lambda sess: {r["contact_id"] for r in sess.call("GET", "/api/v1/cp/leads")[1]["leads"]
                             if r["status"] == "approved"}
        expect("live claims are exclusive", "approved contact sets disjoint", bool(live(cp1) & live(cp2)), False)
        status, earnings = cp1.call("GET", "/api/v1/cp/earnings")
        expect("earnings", "GET /cp/earnings", status, 200)
        show("summary", earnings.get("summary"))
        status, _ = cp1.call("GET", "/api/v1/cp/payouts")
        expect("partner cannot read the payout ledger", "GET /cp/payouts as partner", status, 403)

        print("\n8. the catalogue: a partner can see the stock, and only the stock")
        status, inv = cp1.call("GET", "/api/v1/cp/inventory")
        expect("a partner may read the catalogue", "GET /cp/inventory", status, 200)
        show("catalogue", {"count": inv.get("count"), "listed": inv.get("listed"),
                           "basis": inv.get("commission_basis")})
        expect("there is stock to sell", "count > 0", (inv.get("count") or 0) > 0, True)
        # the seller and the other buyers are never in the payload
        blob = json.dumps(inv).lower()
        leaks = [w for w in ("phone_enc", "seller_phone", "seller_name", "token_amount",
                             "contact_id", "floor_price") if w in blob]
        expect("no seller, no other buyer, no floor", "payload keyword scan", leaks, [])
        # only free stock is listed by default
        dead = [r for r in inv.get("inventory", []) if r.get("status") in ("sold_out", "off_market")]
        expect("sold-out and off-market are not listed", "statuses in the default list",
               [r["status"] for r in dead], [])
        # a booked unit is visible but not sellable, and the detail page says which
        row = next((r for r in inv.get("inventory", []) if r["availability"]["total"]), None)
        if row:
            detail = cp1.call("GET", f"/api/v1/cp/inventory/{row['id']}")[1]["property"]
            units = detail.get("units", [])
            expect("units are described by label and state", "detail units",
                   [u["sellable"] for u in units if u["status"] != "available"],
                   [False] * len([u for u in units if u["status"] != "available"]))
            expect("a unit carries no booking block", "detail unit keys",
                   [k for u in units for k in ("booking", "history") if k in u], [])
            show("detail", {"title": detail.get("title"), "sellable": detail.get("sellable_units"),
                            "of": len(units), "advertisable": detail.get("marketing")})
        # the commission estimate is labelled, never invented
        est = inv.get("commission_basis", {})
        expect("the partner's own rate is shown", "your_share_pct", est.get("your_share_pct", 0) > 0, True)
        cell = next((r["commission"] for r in inv.get("inventory", []) if r["commission"]["estimate"] is not None), None)
        if cell:
            expect("the estimate says what it is a share of", "commission.basis",
                   "your" in cell["basis"] and "gross" in cell["basis"], True)
        # the house cannot use the partner's door
        for who, sess in (("owner", owner), ("agent", agent)):
            status, _ = sess.call("GET", "/api/v1/cp/inventory")
            expect(f"{who} cannot read the partner catalogue", f"GET /cp/inventory as {who}", status, 403)

        print("\n9. money: the house pays, and only an owner/admin may")
        payouts = owner.call("GET", "/api/v1/cp/payouts")[1]
        pending = [p for p in payouts.get("payouts", []) if p["status"] == "pending"]
        if pending:
            deal_id = pending[0]["deal_id"]
            show("pending", {"partner": pending[0]["partner"], "amount": pending[0]["amount"]})
            for who, sess in (("agent", agent), ("manager", manager), ("partner", cp1)):
                status, _ = sess.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "NOPE"})
                expect(f"{who} cannot pay", f"POST …/pay as {who}", status, 403)
            status, paid = owner.call("POST", f"/api/v1/cp/payouts/{deal_id}/pay", {"reference": "UTR-PROOF-0001"})
            expect("owner marks paid", "POST …/pay as owner", status, 200)
            show("payout", paid.get("payout"))
            after = owner.call("GET", "/api/v1/cp/payouts")[1]["payouts"]
            row = next((p for p in after if p["deal_id"] == deal_id), {})
            expect("ledger updated", "payout status", row.get("status"), "paid")
            expect("reference recorded", "payout reference", row.get("reference"), "UTR-PROOF-0001")
        else:
            if args.live:
                print("        (no pending payout on this deployment — section skipped)")
            else:
                FAILS.append("a freshly seeded instance should have a pending partner payout")

        print("\n10. audit trail")
        actions = [e.get("action") for e in owner.call("GET", "/api/v1/tenant/audit")[1].get("entries", [])]
        for action in ("cp.partner_created", "cp.registration_created", "cp.registration_approved",
                       "cp.registration_rejected"):
            expect(f"audited: {action}", "GET /tenant/audit", action in actions, True)
        if pending:
            expect("audited: cp.payout_paid", "GET /tenant/audit", "cp.payout_paid" in actions, True)

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

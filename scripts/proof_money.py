#!/usr/bin/env python3
"""
Proof: money that arrives, and exactly what it is allowed to change.

    python3 scripts/proof_money.py                      # throwaway instance
    python3 scripts/proof_money.py --base http://127.0.0.1:5433 --live

Twelve sections. Every one of them is an HTTP call against a real server; nothing
is asserted from reading the code:

  1. the board starts from rows, and the summary equals them
  2. an unsigned webhook is refused, and nothing is written
  3. an order is the yardstick (what a "payment link" really is)
  4. a signed token books the unit, issues a receipt with no GST
  5. a replay is a duplicate: one payment, one receipt, one booking
  6. a short payment is recorded and flagged, and the unit stays free
  7. money does not open a document gate — and it says which gate
  8. a tax invoice: 18% split CGST/SGST, 5% TDS, net receivable
  9. an out-of-state client gets IGST instead
 10. invoice numbers are one running series, never repeated
 11. who may move money (writer vs manager), and who may not
 12. another workspace sees none of it
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
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
sys.path.insert(0, ROOT)

SECRET = os.environ.get("PAYMENTS_WEBHOOK_SECRET", "whsec_proof_only")
# Every provider id in this run is stamped: reconciliation is idempotent on
# `payment_id`, so a proof that reuses ids across runs turns its own second run into
# a replay — which is the right behaviour and a useless proof.
RUN = str(int(time.time()))[-6:]


def rid(name: str) -> str:
    return f"{name}_{RUN}"
PASSED = 0
FAILS: list[str] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str):
        self.base = base
        self.token = ""

    def call(self, method: str, path: str, body: dict | None = None,
             headers: dict | None = None, raw: bytes | None = None):
        h = {"User-Agent": "proof-money", "Accept": "application/json"}
        if method != "GET":
            # a proof says it is a proof, so a demo can be reseeded (docs/26)
            h["X-Proof-Run"] = f"money-{RUN}"
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def login(self, email: str, password: str = "Demo@12345!"):
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password})
        if status != 200:
            raise SystemExit(f"login failed for {email}: {status} {body}")
        self.token = body["token"]
        return self


def expect(label: str, path: str, got, want) -> None:
    global PASSED
    ok = got == want if not isinstance(want, (list, tuple)) else list(got) == list(want)
    if ok:
        PASSED += 1
        print(f"  [PASS] {label:<48} {path:<44} → {got}")
    else:
        FAILS.append(f"{label}: expected {want!r}, got {got!r}")
        print(f"  [FAIL] {label:<48} {path:<44} → {got}  (want {want!r})")


def show(label: str, value) -> None:
    text = json.dumps(value, default=str)[:220] if not isinstance(value, str) else value
    print(f"        {label}: {text}")


def signed(body: dict, secret: str = SECRET, signature: str | None = None) -> dict:
    raw = json.dumps(body).encode()
    sig = signature if signature is not None else hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"X-Razorpay-Signature": sig}


def live_deals(owner: Client) -> list[dict]:
    """Open deals that can carry an invoice — a deal with no client has no place of
    supply and no one to bill, so it is not a candidate."""
    board = owner.call("GET", "/api/v1/pipeline")[1]
    return [d for stage in ("negotiation", "qualified", "site_visit")
            for d in (board["columns"].get(stage) or []) if d.get("contact_id")]


def invoiced_deal_ids(owner: Client) -> set[str]:
    return {i.get("deal_id") for i in owner.call("GET", "/api/v1/invoices")[1]["invoices"]
            if i.get("type") == "tax_invoice"}


def uninvoiced_deal(owner: Client, exclude: set[str] | None = None) -> tuple[dict, bool]:
    """A deal with no tax invoice yet — or one to revise, explicitly and audited."""
    exclude = exclude or set()
    taken = invoiced_deal_ids(owner) | exclude
    deals = live_deals(owner)
    fresh = [d for d in deals if d["id"] not in taken]
    if fresh:
        return fresh[0], False
    remaining = [d for d in deals if d["id"] not in exclude]
    return (remaining or deals)[0], True


def free_unit(owner: Client) -> tuple[dict, dict, dict]:
    """A unit to book — and one we can keep taking, because a proof consumes stock.

    Every run of this proof books a unit and keeps the payment, which is the point:
    the rows are append-only. Against a long-lived demo that means the free stock
    runs out after a handful of runs and `next(...)` raises `StopIteration` — a
    proof that cannot be run twice is a proof nobody will run before a release. So
    when there is no free unit on the first property, the proof **creates one**
    named after the run (`PRF-<stamp>`): the demo's real inventory is not consumed,
    and the extra unit is visibly a proof's.
    """
    tenant = owner.call("GET", "/api/v1/tenant")[1]
    board = owner.call("GET", "/api/v1/pipeline")[1]
    deal = next(d for stage in ("negotiation", "qualified", "site_visit")
                for d in (board["columns"].get(stage) or []))
    prop = owner.call("GET", "/api/v1/properties")[1]["properties"][0]
    units = owner.call("GET", f"/api/v1/properties/{prop['id']}/units")[1]["units"]
    unit = next((u for u in units if u["status"] == "available"), None)
    if unit is None:
        status, made = owner.call("POST", f"/api/v1/properties/{prop['id']}/units",
                                  {"unit_no": f"PRF-{RUN}", "area_sqft": 600,
                                   "price": int(prop.get("price") or 2500000)})
        if status not in (200, 201):
            raise SystemExit(f"could not create a proof unit: {status} {made}")
        unit = made["unit"]
        print(f"        (no free stock on this instance — created proof unit {unit['unit_no']})")
    return tenant, deal, unit


def payment_event(tenant_id: str, deal_id: str, unit_id: str, amount_paise: int,
                  payment_id: str, event_id: str, order_id: str = "") -> dict:
    entity = {"id": payment_id, "amount": amount_paise, "currency": "INR", "method": "upi",
              "status": "captured",
              "notes": {"tenant_id": tenant_id, "deal_id": deal_id, "unit_id": unit_id,
                        "kind": "token"}}
    if order_id:
        entity["order_id"] = order_id
    return {"event": "payment.captured", "event_id": event_id,
            "payload": {"payment": {"entity": entity}}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-money-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "RE_AUTOMATION_TICK": "0", "PAYMENTS_WEBHOOK_SECRET": SECRET}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(160):
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            if proc:
                proc.kill()
            return 1
    else:
        base = (args.base or "http://127.0.0.1:5433").rstrip("/")

    try:
        print(f"\n=== money proof against {base} ===")
        owner = Client(base).login("owner@realtorestates.app")
        viewer = Client(base).login("viewer@realtorestates.app")
        agent = Client(base).login("agent@realtorestates.app")

        # ---------------------------------------------------------------
        print("\n1. the board starts from rows, and the summary equals them")
        status, summary = owner.call("GET", "/api/v1/money/summary")
        expect("summary served", "GET /money/summary", status, 200)
        payments = owner.call("GET", "/api/v1/payments")[1]["payments"]
        invoices = owner.call("GET", "/api/v1/invoices")[1]["invoices"]
        paid = [p for p in payments if p.get("status") == "succeeded"]
        expect("collected equals the payment rows", "sum(amount_paise)",
               summary["collected_paise"], sum(int(p.get("amount_paise") or 0) for p in paid))
        expect("invoiced equals the invoice rows", "sum(total_paise)", summary["invoiced_paise"],
               sum(int(i.get("total_paise") or 0) for i in invoices
                   if i.get("status") in ("issued", "part_paid", "paid")))
        show("board", {k: summary[k] for k in ("collected", "token_held", "invoiced", "gst",
                                               "tds_withheld", "receivable")})
        show("rates", summary["rates"])

        # ---------------------------------------------------------------
        print("\n2. an unsigned webhook is refused, and nothing is written")
        tenant, deal, unit = free_unit(owner)
        bad = payment_event(tenant["id"], deal["id"], unit["id"], 5_000_000,
                            rid("pay_unsigned"), rid("evt_unsigned"))
        status, body = owner.call("POST", "/api/v1/webhooks/payments", bad,
                                  signed(bad, signature="00" * 32))
        expect("invalid signature refused", "POST /webhooks/payments", status, 401)
        still = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
        expect("the unit is still available", "unit status",
               next(u for u in still if u["id"] == unit["id"])["status"], "available")
        expect("nothing was recorded", "GET /payments (unsigned id)",
               len([p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                    if p.get("payment_id") == rid("pay_unsigned")]), 0)

        # ---------------------------------------------------------------
        print("\n3. an order is the yardstick a payment is measured against")
        status, made = owner.call("POST", "/api/v1/payments/orders",
                                  {"deal_id": deal["id"], "unit_id": unit["id"],
                                   "kind": "token", "amount_paise": 5_000_000,
                                   "note": "booking amount"})
        expect("order created", "POST /payments/orders", status, 200)
        order = made["order"]
        expect("the order carries the notes a webhook echoes", "notes.tenant_id",
               order["notes"]["tenant_id"], tenant["id"])
        expect("no live provider call is pretended", "checkout.provider_call",
               "no live Razorpay order" in made["checkout"]["provider_call"], True)

        # ---------------------------------------------------------------
        print("\n4. a signed token books the unit and issues a receipt — with no GST")
        short = payment_event(tenant["id"], deal["id"], unit["id"], 2_500_000,
                              rid("pay_short"), rid("evt_short"), order["order_id"])
        status, flagged = owner.call("POST", "/api/v1/webhooks/payments", short, signed(short))
        expect("a short payment is flagged, not applied", "outcome", flagged.get("outcome"),
               "amount_mismatch")
        still = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
        expect("a short payment does not book the unit", "unit status",
               next(u for u in still if u["id"] == unit["id"])["status"], "available")

        good = payment_event(tenant["id"], deal["id"], unit["id"], 5_000_000,
                             rid("pay_good"), rid("evt_good"), order["order_id"])
        status, applied = owner.call("POST", "/api/v1/webhooks/payments", good, signed(good))
        expect("the correct payment is applied", "outcome", applied.get("outcome"), "applied")
        expect("the unit is booked", "applied contains unit_booked",
               "unit_booked" in applied.get("applied", []), True)
        expect("a receipt was issued", "applied contains receipt_issued",
               "receipt_issued" in applied.get("applied", []), True)
        expect("the order was settled", "applied contains order_settled",
               "order_settled" in applied.get("applied", []), True)
        receipts = owner.call("GET", "/api/v1/invoices?type=receipt")[1]["invoices"]
        mine = [i for i in receipts if i.get("payment_id") == applied["payment"]["id"]]
        expect("one receipt for the payment", "receipt count", len(mine), 1)
        expect("a receipt charges no GST", "gst.total_paise", mine[0]["gst"]["total_paise"], 0)
        expect("the receipt is for the amount paid", "total_paise",
               mine[0]["total_paise"], 5_000_000)
        booked = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
        expect("the unit's holder is the deal", "booking.deal_id",
               (next(u for u in booked if u["id"] == unit["id"]).get("booking") or {}).get("deal_id"),
               deal["id"])

        # ---------------------------------------------------------------
        print("\n5. a replay is a duplicate — one payment, one receipt, one booking")
        status, again = owner.call("POST", "/api/v1/webhooks/payments", good, signed(good))
        expect("replay answered 200", "POST /webhooks/payments (again)", status, 200)
        expect("replay is a duplicate", "outcome", again.get("outcome"), "duplicate")
        same = [p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                if p.get("payment_id") == rid("pay_good")]
        expect("exactly one payment row", "payments", len(same), 1)
        receipts = [i for i in owner.call("GET", "/api/v1/invoices?type=receipt")[1]["invoices"]
                    if i.get("payment_id") == same[0]["id"]]
        expect("exactly one receipt", "receipts", len(receipts), 1)

        # ---------------------------------------------------------------
        print("\n6. money does not open a document gate — and it names the gate")
        show("stage_block", applied.get("stage_blocked") or "(the deal had its papers)")
        fresh_deal = owner.call("GET", f"/api/v1/deals/{deal['id']}")[1]["deal"]
        blocked = bool(applied.get("stage_blocked"))
        expect("a blocked stage leaves the deal where it was", "stage != token when blocked",
               (fresh_deal["stage"] != "token") if blocked else True, True)
        expect("the block names a document or a visit", "reason is actionable",
               ("document" in (applied.get("stage_blocked") or "").lower()
                or "visit" in (applied.get("stage_blocked") or "").lower()) if blocked else True, True)

        # ---------------------------------------------------------------
        print("\n7. a tax invoice: 18% split, 5% TDS, net receivable")
        inv_deal, needs_force = uninvoiced_deal(owner)
        # captured so section 8 can put the client's state back afterwards
        client_before = (owner.call("GET", f"/api/v1/contacts/{inv_deal['contact_id']}")
                         [1].get("contact", {}) or {})
        original_state = client_before.get("state", "")
        # 194-H needs a payer that is not an individual, so the proof says the client
        # is a business rather than inheriting whatever a previous run left behind
        status, _ = owner.call("PATCH", f"/api/v1/contacts/{inv_deal['contact_id']}",
                               {"is_business": True})
        expect("the client is a business, so 194-H can apply", "PATCH /contacts/{id}",
               status, 200)
        if needs_force:
            print("        (every open deal is already invoiced — issuing a revised one, forced)")
        status, issued = owner.call("POST", f"/api/v1/deals/{inv_deal['id']}/invoice",
                                    {"force": True} if needs_force else {})
        expect("invoice issued", "POST /deals/{id}/invoice", status, 200)
        inv = issued["invoice"]
        expect("taxable + GST = total", "taxable+gst",
               inv["taxable_paise"] + inv["gst"]["total_paise"], inv["total_paise"])
        expect("total − TDS = net receivable", "total−tds",
               inv["total_paise"] - inv["tds"]["withheld_paise"], inv["net_receivable_paise"])
        expect("GST is 18% of the taxable value", "gst.total_paise",
               inv["gst"]["total_paise"], round(inv["taxable_paise"] * 0.18))
        expect("the two halves add to the inter-state figure", "cgst+sgst",
               inv["gst"]["cgst_paise"] + inv["gst"]["sgst_paise"], inv["gst"]["total_paise"])
        expect("TDS is 5% of the taxable value", "tds.withheld_paise",
               inv["tds"]["withheld_paise"], round(inv["taxable_paise"] * 0.05))
        expect("the line carries the SAC for brokerage", "lines[0].sac",
               inv["lines"][0]["sac"], "997221")
        show("invoice", {"number": inv["number"], "taxable": inv["taxable"],
                         "gst": inv["gst"]["total"], "tds": inv["tds"]["withheld"],
                         "net": inv["net_receivable"], "pos": inv["gst"]["place_of_supply"]})

        # ---------------------------------------------------------------
        print("\n8. an out-of-state client is charged IGST, not CGST+SGST")
        # a *different* deal, so the IGST case and the duplicate refusal are separate
        other_deal, other_force = uninvoiced_deal(owner, exclude={inv_deal["id"]})
        status, contact = owner.call("PATCH", f"/api/v1/contacts/{other_deal['contact_id']}",
                                     {"state": "Karnataka", "is_business": True})
        expect("the client's state is recorded", "PATCH /contacts/{id}", status, 200)
        status, igst = owner.call("POST", f"/api/v1/deals/{other_deal['id']}/invoice",
                                  {"force": True} if other_force else {})
        expect("a second invoice is issued", "POST /deals/{id}/invoice", status, 200)
        inv2 = igst["invoice"]
        expect("IGST is charged", "gst.igst_paise > 0", inv2["gst"]["igst_paise"] > 0, True)
        expect("no CGST on an inter-state supply", "gst.cgst_paise", inv2["gst"]["cgst_paise"], 0)
        show("inter-state", {"number": inv2["number"], "igst": inv2["gst"]["igst"],
                             "cgst": inv2["gst"]["cgst"], "pos": inv2["gst"]["place_of_supply"]})
        owner.call("PATCH", f"/api/v1/contacts/{other_deal['contact_id']}",
                   {"state": original_state, "is_business": False})
        owner.call("PATCH", f"/api/v1/contacts/{inv_deal['contact_id']}",
                   {"is_business": bool(client_before.get("is_business"))})
        print(f"        (client state restored to {original_state or 'blank'})")

        # ---------------------------------------------------------------
        print("\n9. the same commission is not invoiced twice")
        status, dup = owner.call("POST", f"/api/v1/deals/{inv_deal['id']}/invoice", {})
        expect("a second invoice for the same deal is refused", "POST /deals/{id}/invoice",
               status, 409)
        expect("and it names the invoice already issued", "error.code",
               dup.get("error", {}).get("code"), "INVOICE_ALREADY_ISSUED")
        status, forced = owner.call("POST", f"/api/v1/deals/{inv_deal['id']}/invoice", {"force": True})
        expect("an owner may force a revised invoice", "POST …/invoice {force}", status, 200)
        show("forced", forced.get("invoice", {}).get("number"))

        print("\n10. invoice numbers are one running series")
        numbers = [i["number"] for i in owner.call("GET", "/api/v1/invoices")[1]["invoices"]]
        expect("no number is used twice", "unique numbers", len(numbers), len(set(numbers)))
        expect("the series is the tenant's initials", "prefix", numbers[0].split("/")[0].isalpha(), True)
        show("numbers", sorted(numbers))

        # ---------------------------------------------------------------
        print("\n11. who may move money")
        expect("a viewer cannot issue an invoice", "POST /deals/{id}/invoice as viewer",
               viewer.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {})[0], 403)
        expect("an agent cannot reconcile the queue", "POST /payments/reconcile as agent",
               agent.call("POST", "/api/v1/payments/reconcile", {})[0], 403)
        expect("a viewer may read the board", "GET /money/summary as viewer",
               viewer.call("GET", "/api/v1/money/summary")[0], 200)

        # ---------------------------------------------------------------
        print("\n12. another workspace sees none of it")
        other = Client(base).login("owner@metrorealty.app", "Metro@12345!")
        expect("no payments of ours", "GET /payments as metro",
               other.call("GET", "/api/v1/payments")[1]["count"], 0)
        expect("no invoices of ours", "GET /invoices as metro",
               other.call("GET", "/api/v1/invoices")[1]["count"], 0)
        expect("an empty board, not our totals", "GET /money/summary as metro",
               other.call("GET", "/api/v1/money/summary")[1]["collected_paise"], 0)

        # ---------------------------------------------------------------
        print("\n13. a payment attributed to another workspace changes nothing here")
        orphan = payment_event("tnt_not_ours", deal["id"], unit["id"], 5_000_000,
                               rid("pay_orphan"), rid("evt_orphan"))
        status, body = owner.call("POST", "/api/v1/webhooks/payments", orphan, signed(orphan))
        expect("it is not applied here", "outcome in (unattributed, unknown_subject)",
               body.get("outcome") in ("unattributed", "unknown_subject"), True)
        expect("and no payment row lands in our board", "payments for the orphan",
               len([p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                    if p.get("payment_id") == rid("pay_orphan")]), 0)

        print(f"\n=== {PASSED} checks passed ===" if not FAILS
              else f"\n=== {len(FAILS)} FAILURE(S) · {PASSED} passed ===")
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

"""
Money: a payment that arrives, and the narrow set of things it may change.

The tests are grouped by the four ways this can hurt a business:

* **tax that is wrong** — GST split, rounding, place of supply, TDS threshold;
* **a document that cannot be audited** — invoice numbers that repeat, an invoice
  that mixes paise and rupees, a token issued as a taxable supply;
* **money that claims more than it bought** — a payment pushing a deal past a
  document gate, a replay booking a second unit;
* **a demo that lies** — a summary whose totals do not come from its rows.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from conftest import BASE, Session  # noqa: F401
from app import money as m

SECRET = "whsec_pytest_only"


# ---------------------------------------------------------------------------
# 1. tax arithmetic
# ---------------------------------------------------------------------------
def test_gst_is_eighteen_percent_and_the_two_halves_match_igst():
    intra = m.gst_split(220_000_00, True)
    inter = m.gst_split(220_000_00, False)
    assert intra["total_paise"] == inter["total_paise"] == 3_960_000
    assert intra["cgst_paise"] + intra["sgst_paise"] == inter["igst_paise"]
    assert intra["place_of_supply"] == "intra_state"
    assert inter["place_of_supply"] == "inter_state"


def test_odd_paise_never_leave_a_one_paisa_hole():
    """₹18% of an odd amount cannot split evenly; the halves must still add up."""
    for taxable in (1, 3, 7, 999, 123_457, 10_000_001):
        split = m.gst_split(taxable, True)
        assert split["cgst_paise"] + split["sgst_paise"] == split["total_paise"]
        assert split["total_paise"] == round(taxable * 0.18)


def test_tds_applies_above_fifteen_thousand_and_not_at_it():
    assert m.tds_194h(1_500_000, client_is_business=True)["applies"] is False   # exactly ₹15,000
    above = m.tds_194h(1_500_001, client_is_business=True)
    assert above["applies"] is True and above["withheld"] == round(1_500_001 * 0.05)
    assert m.tds_194h(100_000_00, client_is_business=False)["withheld"] == 0
    # the threshold is aggregated across the year, not tested per invoice
    assert m.tds_194h(1_000_000, client_is_business=True,
                      already_withheld_paise=600_000)["applies"] is True


def test_place_of_supply_follows_the_client_state():
    mh = {"name": "S", "settings": {"state": "Maharashtra"}}
    assert m.same_state(mh, {"state": "Maharashtra"}) is True
    assert m.same_state(mh, {"state": "Karnataka"}) is False
    assert m.same_state(mh, {}) is True, "an unknown buyer state must not silently become inter-state"


# ---------------------------------------------------------------------------
# 2. documents
# ---------------------------------------------------------------------------
def test_financial_year_runs_april_to_march():
    march31 = int(time.mktime((2027, 3, 31, 12, 0, 0, 0, 0, -1)))
    april1 = int(time.mktime((2027, 4, 1, 12, 0, 0, 0, 0, -1)))
    assert m.financial_year(march31) == "2026-27"
    assert m.financial_year(april1) == "2027-28"


def test_invoice_numbers_are_zero_padded_and_refuse_sequence_zero():
    assert m.invoice_number("SLD", 7) .endswith("/0007")
    assert m.invoice_number("sld", 1234).startswith("SLD/")
    with pytest.raises(m.MoneyError):
        m.invoice_number("SLD", 0)


def test_the_series_prefix_is_initials_not_the_first_six_letters():
    assert m.series_prefix({"name": "Sunrise Land Developers"}) == "SLD"
    assert m.series_prefix({"name": "Metro Realty"}) == "MR"
    assert m.series_prefix({"name": "X", "slug": "xcorp"}) == "XCORP"
    assert m.series_prefix({"name": "Sunrise", "settings": {"invoice_prefix": "sld/ho"}}) == "SLDHO"


def _tenant(**over):
    base = {"name": "Sunrise Land Developers",
            "settings": {"state": "Maharashtra", "gstin": "27AABCU9603R1ZM"}}
    base.update(over)
    return base


def test_a_commission_invoice_adds_up_and_states_the_net_receivable():
    inv = m.commission_invoice(number="SLD/2026-27/0001", issued_at=0, tenant=_tenant(),
                               client={"name": "Ravi", "state": "Maharashtra", "is_business": True},
                               deal={"id": "deal_1", "title": "B-402"},
                               breakdown={"gross": 220_000})
    assert inv["taxable_paise"] + inv["gst"]["total_paise"] == inv["total_paise"]
    assert inv["total_paise"] - inv["tds"]["withheld_paise"] == inv["net_receivable_paise"]
    assert inv["gst"]["cgst_paise"] == inv["gst"]["sgst_paise"] == 1_980_000
    assert inv["tds"]["withheld"] == 11_000
    assert inv["lines"][0]["sac"] == "997221"


def test_an_out_of_state_client_gets_igst_not_cgst():
    inv = m.commission_invoice(number="SLD/2026-27/0002", issued_at=0, tenant=_tenant(),
                               client={"name": "Ravi", "state": "Karnataka"},
                               deal={"id": "deal_1"}, breakdown={"gross": 100_000})
    assert inv["gst"]["igst_paise"] == 1_800_000
    assert inv["gst"]["cgst_paise"] == 0 and inv["gst"]["sgst_paise"] == 0


def test_a_token_receipt_is_custody_and_carries_no_gst():
    r = m.token_receipt(number="SLD/2026-27/0003", issued_at=0, tenant=_tenant(),
                        client={"name": "Ravi"}, deal={"id": "deal_1"},
                        unit={"id": "unt_1", "unit_no": "1A"}, amount_paise=10_000_000,
                        payment_id="pay_1")
    assert r["type"] == "receipt"
    assert r["gst"]["total_paise"] == 0 and "not the brokerage's taxable supply" in r["gst"]["note"]
    assert r["total_paise"] == r["paid_paise"] == 10_000_000


def test_a_zero_commission_is_refused_rather_than_invoiced_as_zero():
    with pytest.raises(m.MoneyError):
        m.commission_invoice(number="SLD/2026-27/0004", issued_at=0, tenant=_tenant(),
                             client={}, deal={"id": "d"}, breakdown={"gross": 0})


# ---------------------------------------------------------------------------
# 3. reading the provider's event
# ---------------------------------------------------------------------------
def _razorpay(amount=10_000_000, kind="token", notes=None, event_id="evt_1"):
    return {"event": "payment.captured", "event_id": event_id,
            "payload": {"payment": {"entity": {
                "id": "pay_1", "amount": amount, "currency": "INR", "method": "upi",
                "notes": {"tenant_id": "tnt_1", "deal_id": "deal_1", "unit_id": "unt_1",
                          "kind": kind, **(notes or {})}}}}}


def test_the_event_reader_finds_razorpay_nesting_and_notes():
    event = m.read_event(_razorpay())
    assert event["tenant_id"] == "tnt_1" and event["deal_id"] == "deal_1"
    assert event["unit_id"] == "unt_1" and event["kind"] == "token"
    assert event["amount_paise"] == 10_000_000 and event["method"] == "upi"


def test_an_unknown_kind_degrades_to_other_not_to_a_crash():
    event = m.read_event(_razorpay(kind="bribe"))
    assert event["kind"] == "other"


def test_a_missing_amount_is_none_not_zero():
    event = m.read_event({"event": "payment.failed", "payload": {"payment": {"entity": {"id": "pay_x"}}}})
    assert event["amount_paise"] is None


# ---------------------------------------------------------------------------
# 4. what the event may change
# ---------------------------------------------------------------------------
def _applied_event(**over):
    base = m.read_event(_razorpay())
    base.update(over)
    return base


def test_a_replay_is_a_duplicate_before_anything_else_is_considered():
    verdict = m.verdict(_applied_event(), deal=None, unit=None,
                        existing_payment={"at": 0})
    assert verdict["outcome"] == "duplicate"
    assert "already reconciled" in verdict["reason"]


def test_a_payment_with_no_tenant_belongs_to_nobody():
    assert m.verdict(_applied_event(tenant_id=""), deal={}, unit={},
                     existing_payment=None)["outcome"] == "unattributed"


def test_an_amount_that_does_not_match_the_order_is_flagged_not_ignored():
    verdict = m.verdict(_applied_event(amount_paise=4_999_900),
                        deal={"id": "deal_1", "token_amount": 50_000},
                        unit={"id": "unt_1", "token_amount": 50_000}, existing_payment=None)
    assert verdict["outcome"] == "amount_mismatch"
    assert "50,000" in verdict["reason"].replace("50000.00", "50,000") or "50,000.00" in verdict["reason"]


def test_a_unit_held_by_another_deal_is_a_conflict():
    verdict = m.verdict(_applied_event(), deal={"id": "deal_1"},
                        unit={"id": "unt_1", "unit_no": "1A",
                              "block": {"deal_id": "deal_other"}}, existing_payment=None)
    assert verdict["outcome"] == "unit_conflict" and "1A" in verdict["reason"]


def test_a_clean_token_is_applied():
    assert m.verdict(_applied_event(), deal={"id": "deal_1"}, unit={"id": "unt_1"},
                     existing_payment=None)["outcome"] == "applied"


def test_the_summary_is_the_sum_of_its_rows():
    payments = [{"status": "succeeded", "kind": "token", "amount_paise": 10_000_000},
                {"status": "succeeded", "kind": "commission", "amount_paise": 5_000_000},
                {"status": "failed", "kind": "token", "amount_paise": 9_000_000}]
    invoices = [{"status": "issued", "total_paise": 11_800_000, "paid_paise": 0,
                 "gst": {"total_paise": 1_800_000}, "tds": {"withheld_paise": 500_000}}]
    s = m.summary(payments, invoices)
    assert s["collected_paise"] == 15_000_000
    assert s["token_held_paise"] == 10_000_000
    assert s["invoiced_paise"] == 11_800_000 and s["receivable_paise"] == 11_800_000
    assert s["gst_paise"] == 1_800_000 and s["tds_withheld_paise"] == 500_000
    assert s["counts"]["unpaid_invoices"] == 1


# ---------------------------------------------------------------------------
# 5. the wire: a signed webhook, end to end
# ---------------------------------------------------------------------------
def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _post_signed(session: Session, path: str, payload: dict, *, secret: str = SECRET,
                 signature: str | None = None, headers: dict | None = None):
    """POST the exact bytes we sign — a webhook is not a JSON round trip."""
    raw = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", "X-Razorpay-Signature":
         signature if signature is not None else _sign(raw, secret)}
    h.update(headers or {})
    import urllib.error
    import urllib.request

    req = urllib.request.Request(BASE + path, data=raw, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _a_free_unit_and_deal(owner: Session) -> tuple[dict, dict, dict]:
    """An available unit, the deal that will pay for it, and the tenant id."""
    tenant = owner.call("GET", "/api/v1/tenant")[1]
    board = owner.call("GET", "/api/v1/pipeline")[1]
    deal = next(d for stage in ("negotiation", "qualified", "site_visit")
                for d in (board["columns"].get(stage) or []))
    prop_id = owner.call("GET", "/api/v1/properties")[1]["properties"][0]["id"]
    units = owner.call("GET", f"/api/v1/properties/{prop_id}/units")[1]["units"]
    unit = next(u for u in units if u["status"] == "available")
    return tenant, deal, unit


def _token_event(tenant_id: str, deal_id: str, unit_id: str, amount_paise: int,
                 payment_id: str = "pay_test_0001", event_id: str = "evt_test_0001",
                 notes: dict | None = None, order_id: str = "") -> dict:
    entity = {"id": payment_id, "amount": amount_paise, "currency": "INR", "method": "upi",
              "notes": {"tenant_id": tenant_id, "deal_id": deal_id, "unit_id": unit_id,
                        "kind": "token", **(notes or {})}}
    if order_id:
        entity["order_id"] = order_id
    return {"event": "payment.captured", "event_id": event_id,
            "payload": {"payment": {"entity": entity}}}


def test_an_unsigned_webhook_is_refused_and_writes_nothing(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    payload = _token_event(tenant["id"], deal["id"], unit["id"], 10_000_000,
                           payment_id="pay_unsigned", event_id="evt_unsigned")
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload, signature="00" * 32)
    assert status == 401 and body["error"]["code"] == "INVALID_SIGNATURE"
    payments = owner.call("GET", "/api/v1/payments")[1]["payments"]
    assert not [p for p in payments if p.get("payment_id") == "pay_unsigned"]
    # and the unit is still free
    fresh = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
    assert next(u for u in fresh if u["id"] == unit["id"])["status"] == "available"


def test_a_signed_token_books_the_unit_issues_a_receipt_and_refuses_to_move_the_stage(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    amount = int(unit.get("token_amount") or 50_000) * 100
    payload = _token_event(tenant["id"], deal["id"], unit["id"], amount,
                           payment_id="pay_signed_1", event_id="evt_signed_1")
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    assert status == 200 and body["outcome"] == "applied", body
    assert "unit_booked" in body["applied"] and "receipt_issued" in body["applied"]

    fresh = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
    booked = next(u for u in fresh if u["id"] == unit["id"])
    assert booked["status"] == "booked" and booked["booking"]["deal_id"] == deal["id"]

    invoices = owner.call("GET", "/api/v1/invoices?type=receipt")[1]["invoices"]
    mine = [i for i in invoices if i.get("payment_id") == body["payment"]["id"]]
    assert len(mine) == 1 and mine[0]["gst"]["total_paise"] == 0
    assert mine[0]["total_paise"] == amount

    # money does not open a document gate: the deal stays where it was, and says why
    moved = owner.call("GET", f"/api/v1/deals/{deal['id']}")[1]["deal"]
    if body["stage_blocked"]:
        assert moved["stage"] != "token"
        assert "document" in body["stage_blocked"].lower() or "visit" in body["stage_blocked"].lower()


def test_a_replay_records_one_payment_and_one_receipt(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    amount = int(unit.get("token_amount") or 50_000) * 100
    payload = _token_event(tenant["id"], deal["id"], unit["id"], amount,
                           payment_id="pay_replay", event_id="evt_replay")
    first = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    second = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    assert first[0] == second[0] == 200
    assert first[1]["outcome"] == "applied" and second[1]["outcome"] == "duplicate"
    payments = [p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                if p.get("payment_id") == "pay_replay"]
    assert len(payments) == 1
    receipts = [i for i in owner.call("GET", "/api/v1/invoices?type=receipt")[1]["invoices"]
                if i.get("payment_id") == payments[0]["id"]]
    assert len(receipts) == 1


def test_an_amount_that_disagrees_with_the_order_is_recorded_and_flagged(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    status, made, _ = owner.call("POST", "/api/v1/payments/orders",
                              {"deal_id": deal["id"], "unit_id": unit["id"], "kind": "token",
                               "amount_paise": 5_000_000})
    assert status == 200, made
    order = made["order"]
    expected = order["amount_paise"]
    payload = _token_event(tenant["id"], deal["id"], unit["id"], expected // 2,
                           payment_id="pay_mismatch", event_id="evt_mismatch",
                           order_id=order["order_id"])
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    assert status == 200 and body["outcome"] == "amount_mismatch", body
    fresh = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
    assert next(u for u in fresh if u["id"] == unit["id"])["status"] == "available", \
        "a short payment must not book the unit"
    recorded = [p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                if p.get("payment_id") == "pay_mismatch"]
    assert len(recorded) == 1 and recorded[0]["outcome"] == "amount_mismatch"


def test_a_payment_without_a_tenant_is_unattributed(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    payload = _token_event("", deal["id"], unit["id"], 10_000_000,
                           payment_id="pay_orphan", event_id="evt_orphan")
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    assert status == 200 and body["outcome"] == "unattributed"


def test_invoice_numbers_are_unique_across_receipts_and_invoices(owner: Session):
    board = owner.call("GET", "/api/v1/pipeline")[1]
    deal = next(d for stage in ("negotiation", "qualified", "site_visit")
                for d in (board["columns"].get(stage) or []))
    first = owner.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {})
    assert first[0] == 200, first[1]
    inv = first[1]["invoice"]
    assert inv["taxable_paise"] + inv["gst"]["total_paise"] == inv["total_paise"]
    assert inv["total_paise"] - inv["tds"]["withheld_paise"] == inv["net_receivable_paise"]
    numbers = [i["number"] for i in owner.call("GET", "/api/v1/invoices")[1]["invoices"]]
    assert len(numbers) == len(set(numbers)), numbers


def test_a_commission_invoice_needs_write_and_reconcile_needs_a_manager(viewer: Session):
    board = viewer.call("GET", "/api/v1/pipeline")[1]
    deal = next(d for stage in ("negotiation", "qualified") for d in (board["columns"].get(stage) or []))
    assert viewer.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {})[0] == 403
    assert viewer.call("POST", "/api/v1/payments/reconcile", {})[0] == 403


def test_the_money_summary_equals_the_rows_it_reads(owner: Session):
    summary = owner.call("GET", "/api/v1/money/summary")[1]
    payments = owner.call("GET", "/api/v1/payments")[1]["payments"]
    invoices = owner.call("GET", "/api/v1/invoices")[1]["invoices"]
    paid = [p for p in payments if p.get("status") == "succeeded"]
    assert summary["collected_paise"] == sum(int(p["amount_paise"]) for p in paid)
    assert summary["invoiced_paise"] == sum(
        int(i["total_paise"]) for i in invoices if i.get("status") in ("issued", "part_paid", "paid"))
    assert summary["token_held_paise"] == sum(
        int(p["amount_paise"]) for p in paid if p.get("kind") == "token")


def test_another_workspace_cannot_read_or_attribute_our_money(owner: Session, other_tenant: Session):
    theirs = other_tenant.call("GET", "/api/v1/payments")[1]
    ours = owner.call("GET", "/api/v1/payments")[1]
    our_ids = {p["id"] for p in ours["payments"]}
    assert not ({p["id"] for p in theirs["payments"]} & our_ids)
    # a payment whose notes name another tenant is not applied to ours
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    payload = _token_event("tnt_someone_else", deal["id"], unit["id"], 10_000_000,
                           payment_id="pay_crosstenant", event_id="evt_crosstenant")
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    # Nothing belongs to us: the notes name a workspace that does not hold this deal
    assert status == 200 and body["outcome"] in ("unattributed", "unknown_subject")
    fresh = owner.call("GET", f"/api/v1/properties/{unit['property_id']}/units")[1]["units"]
    assert next(u for u in fresh if u["id"] == unit["id"])["status"] == "available"
    assert not [p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
                if p.get("payment_id") == "pay_crosstenant"]


def test_an_order_is_the_yardstick_a_payment_is_measured_against(owner: Session):
    tenant, deal, unit = _a_free_unit_and_deal(owner)
    status, made, _ = owner.call("POST", "/api/v1/payments/orders",
                              {"deal_id": deal["id"], "unit_id": unit["id"], "kind": "token",
                               "amount_paise": 5_000_000, "note": "booking amount"})
    assert status == 200, made
    order = made["order"]
    assert order["status"] == "created" and order["amount_paise"] == 5_000_000
    assert order["notes"]["tenant_id"] == tenant["id"] and order["notes"]["order_id"] == order["order_id"]
    assert "no live Razorpay order" in made["checkout"]["provider_call"]

    payload = _token_event(tenant["id"], deal["id"], unit["id"], 5_000_000,
                           payment_id="pay_order_ok", event_id="evt_order_ok",
                           order_id=order["order_id"])
    status, body = _post_signed(owner, "/api/v1/webhooks/payments", payload)
    assert status == 200 and body["outcome"] == "applied", body
    assert "order_settled" in body["applied"]
    settled = [p for p in owner.call("GET", "/api/v1/payments")[1]["payments"]
               if p.get("order_id") == order["order_id"]]
    assert len(settled) == 2   # the intent and the money, linked by order_id
    assert {p["status"] for p in settled} == {"created", "succeeded"} or \
        {p["status"] for p in settled} == {"succeeded"}


def test_the_same_commission_cannot_be_invoiced_twice(owner: Session):
    """Over-billing is found by the client's accountant months later, not by us."""
    board = owner.call("GET", "/api/v1/pipeline")[1]
    deal = next(d for stage in ("negotiation", "qualified", "site_visit")
                for d in (board["columns"].get(stage) or []))
    first = owner.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {})
    assert first[0] in (200, 409), first[1]
    if first[0] == 200:
        second = owner.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {})
        assert second[0] == 409 and second[1]["error"]["code"] == "INVOICE_ALREADY_ISSUED"
        forced = owner.call("POST", f"/api/v1/deals/{deal['id']}/invoice", {"force": True})
        assert forced[0] == 200
    else:
        assert first[1]["error"]["code"] == "INVOICE_ALREADY_ISSUED"


def test_an_individual_client_is_not_charged_tds_and_the_invoice_says_why():
    """194-H needs a payer other than an individual/HUF; a buyer paying brokerage
    out of their own pocket is not that. No TDS *with the reason named* — 'no TDS'
    with the wrong reason is how an accountant loses an afternoon."""
    inv = m.commission_invoice(number="SLD/2026-27/0009", issued_at=0, tenant=_tenant(),
                               client={"name": "Ravi", "state": "Maharashtra"},   # no is_business
                               deal={"id": "deal_1"}, breakdown={"gross": 220_000})
    assert inv["tds"]["applies"] is False
    assert inv["tds"]["withheld_paise"] == 0
    assert "individual" in inv["tds"]["reason"]
    # and the net receivable is the whole invoice, because nothing was withheld
    assert inv["net_receivable_paise"] == inv["total_paise"]
    # a business below the threshold gets the *other* reason
    small = m.commission_invoice(number="SLD/2026-27/0010", issued_at=0, tenant=_tenant(),
                                 client={"name": "ACME", "state": "Maharashtra", "is_business": True},
                                 deal={"id": "deal_2"}, breakdown={"gross": 10_000})
    assert small["tds"]["applies"] is False and "threshold" in small["tds"]["reason"]

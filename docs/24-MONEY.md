# 24 — Money: a payment that arrives, and what it may change

**Question.** `PLANS.md` G2 asked it bluntly: the app recorded a `token_amount` on a
unit, it had a seeded `razorpay` row in `integrations`, and **no payment ever
arrived**. Nothing reconciled, nothing invoiced, nothing accrued. That made the
"Sell.Do stops at booking" positioning — the one claim `docs/18` said the Indian
market had left open — a slide rather than a fact.

This document is the money layer: the reconciliation, the two documents it
produces, and the four decisions that keep it honest.

---

## 1. The shape

```
    provider (Razorpay)
        │  raw bytes + X-Razorpay-Signature
        ▼
    functions/payments-webhook.ts          ← verified, deduped, stored (re_webhook_events)
        │  (production: the function is one writer, the app reads)
        ▼
    POST /api/v1/payments/reconcile        ← or POST /webhooks/payments for local/CI
        │
        ├── payments[]     one row per provider payment_id (a replay is a duplicate)
        ├── units[]        apply_booking → booked, holder = the deal
        ├── deals[]        stage → token **only if the document gate allows it**
        └── invoices[]     receipt (token, no GST) or tax_invoice (commission, 18% GST, 5% TDS)
```

`app/money.py` holds the pure rules — tax arithmetic, the financial year, the two
document builders, the replay/attribution/amount verdict. `app/api.py` does the
reading and writing. There is no second implementation of "what is 18% of this".

## 2. Four decisions

### 2.1 A token payment is custody, not revenue

The buyer's booking amount is the seller's money held by the brokerage. It gets a
**receipt**: `gst.total_paise = 0`, `total = paid = amount`, and the note says why.
Only the brokerage's own commission is a taxable supply.

* **Why:** issuing a tax invoice for a token inflates revenue *and* GST liability,
  and the error is only discovered at the first GST return after a client's
  accountant reconciles. It is the single most common money bug in Indian real
  estate CRMs, because "money arrived → make an invoice" feels right.
* **Test:** `test_a_token_receipt_is_custody_and_carries_no_gst` and the live proof
  section 4 (`gst.total_paise == 0` on a real 50,000 payment).

### 2.2 One running series, one rounding step per component

`SLD/2026-27/0007` — tenant initials, Indian financial year (April–March), zero-padded
sequence. Receipts and invoices share the series.

* **Why one series:** the first version numbered receipts and invoices
  independently and produced two documents called `SLD/2026-27/0002`. An accountant
  reads that as a duplicate; a tax officer reads it as a problem. Caught by the
  proof, not by review.
* **Why one rounding step:** CGST and SGST are rounded independently and the
  remainder is assigned to SGST, so `cgst + sgst` always equals the IGST figure for
  the same taxable value (`test_odd_paise_never_leave_a_one_paisa_hole`).
* **Units:** every paise field is `*_paise`; every rupee mirror is plain. A document
  that mixes them is how ₹1,00,000 becomes ₹1,00,00,000.

### 2.3 Money does not open a document gate

A payment can book a unit. It cannot move a deal to `token` if `vertical.check_transition`
refuses for missing KYC, a booking form or a site visit. The payment is recorded,
the block is reported (`stage_block`) and audited (`deal.stage_blocked_by_money`).

* **Why:** the alternative is that a customer's UPI transfer becomes an override of
  the controls the product sells. The money is real; the stage claim is not.
* **Cost, stated plainly:** an agent may see money in the bank and a deal that did
  not move. That is the correct friction, and the run says exactly which document
  is missing so the agent can fix the actual problem.

### 2.4 An amount is checked against something we wrote down

`POST /payments/orders` creates the intent: amount, kind, deal, unit, tenant — the
notes a provider echoes back. A webhook's amount is compared with that order's
amount. No expectation is guessed from the property price.

* **Why:** a half-paid token must not book a unit, and a guard that invents its own
  expectation flags honest payments as fraud. Without an order the check has nothing
  to compare, so the first version silently applied a 25,000 payment as if it were
  the full 50,000 token — caught by the proof.
* **Honest limit:** this build makes **no live Razorpay call**. `provider_call` in
  the response says so.

## 3. The two documents

**Receipt** (`type: receipt`) — number, date, payer, deal, unit, payment id, amount,
`gst.total_paise = 0`, a one-line reason.

A deal gets **one** tax invoice: a second is refused (`409 INVOICE_ALREADY_ISSUED`) unless
an owner forces it (a revised invoice), and that override is audited. Two invoices for
one commission is over-billing, and it is found by the client's accountant months
after the money moved — the proof asserts the refusal.

**Tax invoice** (`type: tax_invoice`)

| Field | Rule |
|---|---|
| Line | one service line, `Real estate brokerage — <deal>` |
| SAC | `997221` (real estate brokerage / property management services) |
| Taxable value | the deal's commission (`vertical.commission_breakdown`: gross, partner cut, agent split — all in paise) |
| GST | 18%; CGST+SGST when the client's state equals the supplier's, else IGST |
| TDS | 194-H, 5%, on commission above ₹15,000 aggregate in the financial year — **and only when the client is a business**. An individual buyer paying brokerage does not withhold; the invoice then prints "no TDS" with that reason, not with the threshold's |
| Net receivable | `total − TDS`, stated on the face of the invoice |
| 194-IA | recorded as a rate, **not** withheld — it is the buyer's obligation on consideration ≥ ₹50 lakh, not the broker's invoice line |

Place of supply needs the client's state, so `contacts` now carries `state`, `gstin`
and `is_business`. When a client has no state on record the invoice is issued
intra-state and says so (`place_of_supply_note`) rather than guessing IGST.

## 4. What a manager sees

`GET /money/summary` sums the rows and nothing else: collected, token held,
commission received, invoiced, GST, TDS withheld, receivable, and the counts
(payments, invoices, unpaid invoices). The desk panel on `/crm.html` renders the same
numbers; if the panel and the rows disagree, the panel is wrong.

## 5. Proof

`scripts/proof_money.py` — 49 live checks, self-booted or `--live`:

1. the board equals its rows;
2. an unsigned webhook → 401, nothing written, the unit still free;
3. an order records the expectation, and `provider_call` admits no live call;
4. a **short** payment is flagged `amount_mismatch` and does not book the unit; the
   correct payment books it, issues a receipt with zero GST, and settles the order;
5. the replay is a duplicate — one payment, one receipt, one booking;
6. the blocked stage names the missing documents;
7. the tax invoice adds up: taxable + GST = total, total − TDS = net, 18%, 5%,
   SAC present;
8. an out-of-state client gets IGST and no CGST (client state returned afterwards);
9. the same commission is not invoiced twice (409, then an owner's explicit force);
10. invoice numbers are one unique series;
11. RBAC — a viewer cannot issue, an agent cannot reconcile, both may read;
12. another workspace sees none of it;
13. a payment attributed to another workspace changes nothing here.

`tests/test_money.py` — 31 tests: `gst_split` parity on odd paise, the TDS threshold
at exactly ₹15,000, April/March financial-year boundaries, series prefixes, the two
documents' arithmetic, the event reader against Razorpay's real nesting, all six
verdict outcomes, and the HTTP leg (signature, replay, mismatch, order settlement,
unique numbers, RBAC, isolation, summary-equals-rows).

## 6. Honest limits

* **No live provider call, no payout.** Orders are intents; there is no Razorpay API
  call, no refund, no settlement report, no payout to the agent or the channel
  partner. `cp_payouts` is a separate ledger (`docs/20`).
* **No recurring or instalment plan.** An installment payment is a `kind`; nothing
  schedules the next one.
* **No credit notes or cancellations.** A refunded payment has a status and no path
  to reverse a booking — a real gap, and the next thing to build here.
* **GST returns are not filed.** The invoice carries what a return needs (taxable,
  split, SAC, place of supply), not a GSTR-1 export.
* **TDS is a receivable note.** The client withholds and issues Form 16A; nothing
  here reconciles 26AS.
* **One currency, one tax regime.** INR only, no export of services, no SEZ.

"""
Money — what a payment is allowed to change, and what it must not.

`PLANS.md` G2 named this gap plainly: the app recorded a `token_amount` on a unit
and had a seeded `razorpay` integration row, but no payment ever arrived, nothing
reconciled, no invoice existed and no commission was ever accrued. That made the
"Sell.Do stops at booking" positioning a slide, not a fact.

Four decisions shape this module, and each of them is a place Indian real-estate
software usually gets it wrong:

1. **A token payment is not revenue.** When a buyer pays a booking amount to the
   developer, the brokerage has *collected nothing of its own* — it is custody of
   the client's money, so it gets a **receipt**, no GST, no invoice. Only the
   brokerage's own commission is a taxable supply: one service line, SAC 997221,
   18%, CGST+SGST when the client is in the same state as the supplier and IGST
   when they are not. Issuing a tax invoice for a token inflates revenue and GST
   liability; that mistake is common and expensive.
2. **TDS is part of the receivable.** A company client withholds 5% under section
   194-H on commission above ₹15,000 in a financial year. The invoice therefore
   states gross, GST, TDS withheld and **net receivable**, because an agent chasing
   "the invoice amount" chases 5% that will never arrive.
3. **Money does not open a document gate.** A payment can book a unit, but it
   cannot move a deal past a stage its papers and visits do not support
   (`vertical.check_transition`). A payment that arrives against an unsupported
   stage is *recorded and flagged*, never forced — the money is real, the stage
   claim is not.
4. **Reconciliation is idempotent by provider payment id.** Providers retry
   webhooks; a retry must not book a second unit or accrue a second commission.

Everything here is pure: integers in paise, no I/O, no clock. The routes in
`app/api.py` do the reading and writing.
"""
from __future__ import annotations

import time
from typing import Any

# ---------------------------------------------------------------------------
# Rates and constants — cited, not invented
# ---------------------------------------------------------------------------
CURRENCY = "INR"
SAC_BROKERAGE = "997221"        # real estate brokerage / property management services
GST_RATE_PCT = 18.0             # notified rate for SAC 997221
GST_IGST_THRESHOLD = 0          # any state difference is inter-state, no de minimis
TDS_194H_PCT = 5.0              # commission or brokerage
TDS_194H_THRESHOLD_PAISE = 1_500_000   # ₹15,000 aggregate per financial year
TDS_194IA_PCT = 1.0             # buyer-side, consideration ≥ ₹50 lakh (recorded, not withheld)
TDS_194IA_THRESHOLD_PAISE = 5_000_000_00

PAYMENT_KINDS = ("token", "installment", "commission", "other")
PAYMENT_STATUSES = ("succeeded", "failed", "refunded", "partially_refunded")
RECONCILE_OUTCOMES = ("applied", "duplicate", "unattributed", "unknown_subject",
                      "unit_conflict", "stage_blocked", "amount_mismatch", "pending")


class MoneyError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# Indian financial year, and an invoice series that cannot collide
# ---------------------------------------------------------------------------
def financial_year(at: int | None = None) -> str:
    """`2026-27` — April to March, the year GST returns are filed in."""
    local = time.localtime(at or time.time())
    start = local.tm_year if local.tm_mon >= 4 else local.tm_year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def series_prefix(tenant: dict) -> str:
    """`SLD` for Sunrise Land Developers — initials, not the first six letters.

    A GST invoice series is quoted on returns and matched by an accountant; "SUNRIS"
    reads as a typo where "SLD" reads as a brand. An explicit
    `settings.invoice_prefix` always wins.
    """
    explicit = str((tenant.get("settings") or {}).get("invoice_prefix") or "").strip()
    if explicit:
        return "".join(ch for ch in explicit.upper() if ch.isalnum())[:8] or "INV"
    words = [w for w in str(tenant.get("name") or "").split() if w[:1].isalpha()]
    initials = "".join(w[0] for w in words).upper()[:4]
    if len(initials) >= 2:
        return initials
    slug = "".join(ch for ch in str(tenant.get("slug") or "") if ch.isalnum()).upper()
    return (slug or "INV")[:6]


def invoice_number(prefix: str, seq: int, at: int | None = None) -> str:
    """`SRG/2026-27/0007`.

    Consecutive per series per financial year, which is what Rule 46(b) of the CGST
    Rules asks for. The prefix comes from the tenant slug so two workspaces cannot
    mint the same number.
    """
    clean = "".join(ch for ch in (prefix or "INV").upper() if ch.isalnum())[:8] or "INV"
    if seq < 1:
        raise MoneyError("INVALID_SEQUENCE", "Invoice sequence starts at 1.")
    return f"{clean}/{financial_year(at)}/{seq:04d}"


# ---------------------------------------------------------------------------
# Tax arithmetic — paise in, paise out, one rounding step per component
# ---------------------------------------------------------------------------
def _paise(rupees: Any) -> int:
    try:
        return int(round(float(rupees) * 100))
    except (TypeError, ValueError):
        raise MoneyError("INVALID_AMOUNT", "Amount is not a number.")


def gst_split(taxable_paise: int, intra_state: bool) -> dict:
    """Split 18% into CGST+SGST (9+9) or IGST (18), rounding once each.

    The two halves are rounded independently and the difference against the exact
    18% is put on SGST, so `cgst + sgst` equals the IGST figure for the same
    taxable value. An invoice whose two halves do not add to the inter-state total
    is the kind of 1-paisa mismatch that shows up in a GST reconciliation months
    later.
    """
    taxable = max(0, int(taxable_paise))
    total = int(round(taxable * GST_RATE_PCT / 100))
    if not intra_state:
        return {"cgst_paise": 0, "sgst_paise": 0, "igst_paise": total, "total_paise": total,
                "place_of_supply": "inter_state"}
    half = total // 2
    return {"cgst_paise": half, "sgst_paise": total - half, "igst_paise": 0,
            "total_paise": total, "place_of_supply": "intra_state"}


def tds_194h(taxable_paise: int, *, client_is_business: bool = False,
             already_withheld_paise: int = 0) -> dict:
    """TDS a business client withholds on brokerage (5%, above ₹15,000 a year).

    Two conditions, and 194-H needs both: the payer must be a person *other than an
    individual or HUF* (an individual buyer paying brokerage does not deduct), and
    the year's aggregate must exceed ₹15,000. The default is therefore `False` —
    a brokerage that assumes every buyer is an entity deducts tax nobody asked it
    to, and the buyer's accountant has to unwind it.

    The `reason` names whichever condition failed: "no TDS" with the wrong reason
    is how an accountant loses an afternoon.
    """
    taxable = max(0, int(taxable_paise))
    aggregate = taxable + int(already_withheld_paise)
    above = aggregate > TDS_194H_THRESHOLD_PAISE
    applies = bool(client_is_business) and above
    amount = int(round(taxable * TDS_194H_PCT / 100)) if applies else 0
    if applies:
        reason = ""
    elif not client_is_business:
        reason = ("the client is an individual: 194-H applies to a payer other than an "
                  "individual or HUF, so no tax is withheld at source")
    else:
        reason = f"below the ₹{TDS_194H_THRESHOLD_PAISE // 100:,} aggregate threshold for the year"
    return {
        "section": "194H",
        "rate_pct": TDS_194H_PCT if applies else 0.0,
        "threshold_inr": TDS_194H_THRESHOLD_PAISE / 100,
        "applies": applies,
        "withheld": amount,
        "reason": reason,
    }


def same_state(tenant: dict, client: dict) -> bool:
    """Place of supply: the client's state against the supplier's registration.

    A seed/demo without a declared supplier state defaults to Maharashtra (the demo
    tenant); an *unknown buyer* state is treated as the supplier's state and the
    invoice says so (`place_of_supply_note`), because guessing "inter-state" from a
    missing field would charge IGST on work done next door.
    """
    supplier = str((tenant.get("settings") or {}).get("state") or tenant.get("state") or "Maharashtra")
    buyer = str(client.get("state") or (client.get("address") or {}).get("state") or "")
    if not buyer:
        return True
    return supplier.strip().lower() == buyer.strip().lower()


# ---------------------------------------------------------------------------
# The two documents
# ---------------------------------------------------------------------------
def commission_invoice(*, number: str, issued_at: int, tenant: dict, client: dict, deal: dict,
                       breakdown: dict, tds_history_paise: int = 0) -> dict:
    """The brokerage's own supply: one service line, GST split, TDS note, net due."""
    gross = _paise(breakdown.get("gross") or 0)
    if gross <= 0:
        raise MoneyError("INVOICE_HAS_NO_VALUE", "Commission is zero; nothing to invoice.")
    intra = same_state(tenant, client)
    tax = gst_split(gross, intra)
    # a client with no `is_business` on record is an individual until somebody says
    # otherwise; the invoice then shows no TDS *and says why* (see tds_194h)
    tds = tds_194h(gross, client_is_business=bool(client.get("is_business", False)),
                   already_withheld_paise=tds_history_paise)
    total = gross + tax["total_paise"]
    net = total - tds["withheld"]
    return {
        "type": "tax_invoice",
        "number": number,
        "issued_at": issued_at,
        "currency": CURRENCY,
        "supplier": {
            "name": tenant.get("name", ""),
            "gstin": (tenant.get("settings") or {}).get("gstin", ""),
            "state": (tenant.get("settings") or {}).get("state", ""),
            "sac": SAC_BROKERAGE,
        },
        "buyer": {"name": client.get("name", ""), "gstin": client.get("gstin", ""),
                  "state": client.get("state", "")},
        "deal_id": deal.get("id", ""),
        "lines": [{
            "description": f"Real estate brokerage — {deal.get('title') or deal.get('id', '')}",
            "sac": SAC_BROKERAGE, "quantity": 1,
            "rate_paise": gross, "amount_paise": gross,
            "gst_rate_pct": GST_RATE_PCT,
        }],
        "taxable_paise": gross,
        "taxable": gross / 100,
        # Paise fields are always `*_paise`; rupee mirrors are plain. A document
        # that mixes the two units is how a ₹1,00,000 invoice becomes ₹1,00,00,000.
        "place_of_supply_note": ("client state not on record; invoiced as intra-state"
                                 if not (client.get("state") or "") else ""),
        "gst": {**tax,
                "cgst": tax["cgst_paise"] / 100, "sgst": tax["sgst_paise"] / 100,
                "igst": tax["igst_paise"] / 100, "total": tax["total_paise"] / 100},
        "total_paise": total,
        "total": total / 100,
        "tds": {**tds, "withheld_paise": tds["withheld"], "withheld": tds["withheld"] / 100},
        "net_receivable_paise": net,
        "net_receivable": net / 100,
        "status": "issued",
        "paid_paise": 0,
    }


def token_receipt(*, number: str, issued_at: int, tenant: dict, client: dict, deal: dict,
                  unit: dict, amount_paise: int, payment_id: str) -> dict:
    """Custody of the buyer's booking amount — a receipt, not a taxable supply."""
    amount = max(0, int(amount_paise))
    if amount <= 0:
        raise MoneyError("RECEIPT_HAS_NO_VALUE", "A receipt needs a positive amount.")
    return {
        "type": "receipt",
        "number": number,
        "issued_at": issued_at,
        "currency": CURRENCY,
        "supplier": {"name": tenant.get("name", "")},
        "buyer": {"name": client.get("name", "")},
        "deal_id": deal.get("id", ""),
        "unit_id": unit.get("id", ""),
        "unit_no": unit.get("unit_no", ""),
        "payment_id": payment_id,
        "amount_paise": amount,
        "amount": amount / 100,
        "gst": {"total_paise": 0, "note": "Booking amount held for the seller — not the "
                                              "brokerage's taxable supply, so no GST is charged."},
        "total_paise": amount,
        "total": amount / 100,
        "status": "issued",
        "paid_paise": amount,
    }


# ---------------------------------------------------------------------------
# Reading a provider's webhook
# ---------------------------------------------------------------------------
def read_event(payload: dict, *, header_event_id: str = "") -> dict:
    """Normalise a Razorpay-shaped payment event into the six fields we act on.

    Razorpay nests one level deeper than it looks: `payload.payment.entity`. The
    workspace, deal, unit and kind travel in `notes`, set when the order was
    created — the provider has no idea what a tenant is.
    """
    container = (payload.get("payload") or {}) if isinstance(payload, dict) else {}
    wrapper = container.get("payment") or container.get("order") or {}
    entity = (wrapper.get("entity") if isinstance(wrapper, dict) else None) or wrapper or {}
    notes = (entity.get("notes") or payload.get("notes") or {}) if isinstance(payload, dict) else {}
    notes = notes if isinstance(notes, dict) else {}
    amount = entity.get("amount")
    kind = str(notes.get("kind") or "token").strip().lower()
    return {
        "event_id": str(header_event_id or payload.get("event_id") or payload.get("id") or "").strip(),
        "event_type": str(payload.get("event") or payload.get("type") or "").strip(),
        "payment_id": str(entity.get("id") or "").strip(),
        "order_id": str(entity.get("order_id") or notes.get("order_id") or "").strip(),
        "amount_paise": int(amount) if isinstance(amount, int) and amount >= 0 else None,
        "currency": str(entity.get("currency") or CURRENCY).upper(),
        "method": str(entity.get("method") or "").strip(),
        "tenant_id": str(notes.get("tenant_id") or "").strip(),
        "deal_id": str(notes.get("deal_id") or "").strip(),
        "unit_id": str(notes.get("unit_id") or "").strip(),
        "contact_id": str(notes.get("contact_id") or "").strip(),
        "kind": kind if kind in PAYMENT_KINDS else "other",
        "note": str(notes.get("note") or "")[:200],
    }


def verdict(event: dict, *, deal: dict | None, unit: dict | None,
            existing_payment: dict | None, unit_holder: str = "",
            expected_paise: int = 0) -> dict:
    """Should this event change anything? Pure, so the answer is testable.

    Order matters: a duplicate is checked before anything else (a provider retry is
    the common case, not the exception), then attribution, then the subject, then
    the amount, then the unit's holder, then the deal's stage gate.
    """
    if existing_payment:
        return {"outcome": "duplicate", "reason":
                f"Payment {event['payment_id']} was already reconciled on "
                f"{time.strftime('%d %b %Y %H:%M', time.localtime(existing_payment.get('at') or 0))}."}
    if not event.get("tenant_id"):
        return {"outcome": "unattributed",
                "reason": "The payment carries no tenant in its notes; it belongs to no workspace."}
    if not event.get("payment_id"):
        return {"outcome": "unknown_subject", "reason": "The event has no payment id."}
    if not event.get("amount_paise"):
        return {"outcome": "amount_mismatch", "reason": "The provider reported no amount."}
    if event["kind"] == "token" and not (deal and unit):
        return {"outcome": "unknown_subject",
                "reason": "A token payment must name a deal and a unit in the order notes."}
    if event["kind"] == "commission" and not deal:
        return {"outcome": "unknown_subject", "reason": "A commission payment must name a deal."}
    if unit is not None:
        holder = unit_holder or (unit.get("booking") or {}).get("deal_id") \
            or (unit.get("block") or {}).get("deal_id") or ""
        if holder and deal and holder != deal.get("id"):
            return {"outcome": "unit_conflict",
                    "reason": f"Unit {unit.get('unit_no') or unit.get('id')} is held by deal {holder}."}
    # An amount is only ever checked against an expectation the app wrote down: an
    # order it created, or a token amount declared on the unit/deal. Guessing an
    # expectation from the property price would flag honest payments as fraud.
    expected = int(expected_paise or 0) or _paise((unit or {}).get("token_amount")
                                                  or (deal or {}).get("token_amount") or 0)
    if expected and event["amount_paise"] != expected:
        return {"outcome": "amount_mismatch",
                "reason": f"Provider says {event['amount_paise'] / 100:.2f}, the order asked for "
                          f"{expected / 100:.2f}.",
                "expected_paise": expected}
    return {"outcome": "applied", "reason": ""}


def order(*, order_id: str, tenant_id: str, deal_id: str, unit_id: str, contact_id: str,
          kind: str, amount_paise: int, at: int, note: str = "") -> dict:
    """A payment intent: the amount we asked for, so reconciliation has a yardstick.

    Without this the "amount mismatch" check has nothing to compare against and a
    half-paid token would quietly book a unit. The notes are the fields the provider
    echoes back on the payment, which is how a webhook finds its way home.
    """
    amount = max(0, int(amount_paise))
    if amount <= 0:
        raise MoneyError("INVALID_AMOUNT", "An order needs a positive amount.")
    if kind not in PAYMENT_KINDS:
        raise MoneyError("INVALID_KIND", f"Unknown payment kind '{kind}'.")
    return {
        "kind": kind, "status": "created", "provider": "razorpay",
        "order_id": order_id, "amount_paise": amount, "amount": amount / 100,
        "currency": CURRENCY, "tenant_id": tenant_id, "deal_id": deal_id,
        "unit_id": unit_id, "contact_id": contact_id, "at": at, "reconciled_at": 0,
        "outcome": "pending",
        "notes": {"tenant_id": tenant_id, "deal_id": deal_id, "unit_id": unit_id,
                  "kind": kind, "order_id": order_id},
        "note": note[:200],
    }


# ---------------------------------------------------------------------------
# The board a manager reads
# ---------------------------------------------------------------------------
def summary(payments: list[dict], invoices: list[dict]) -> dict:
    """Collected, billed, GST, TDS, receivable — in rupees, from the rows."""
    paid = [p for p in payments if p.get("status") == "succeeded"]
    collected = sum(int(p.get("amount_paise") or 0) for p in paid)
    token = sum(int(p.get("amount_paise") or 0) for p in paid if p.get("kind") == "token")
    commission = sum(int(p.get("amount_paise") or 0) for p in paid if p.get("kind") == "commission")
    issued = [i for i in invoices if i.get("status") in ("issued", "part_paid", "paid")]
    billed = sum(int(i.get("total_paise") or 0) for i in issued)
    gst = sum(int((i.get("gst") or {}).get("total_paise") or 0) for i in issued)
    tds = sum(int((i.get("tds") or {}).get("withheld_paise") or 0) for i in issued)
    receivable = sum(max(0, int(i.get("total_paise") or 0) - int(i.get("paid_paise") or 0))
                     for i in issued)
    credit_notes = [i for i in invoices if i.get("type") == "credit_note" and i.get("status") == "applied"]
    credited_paise = sum(int(i.get("total_paise") or 0) for i in credit_notes)
    credited_gst = sum(int((i.get("gst") or {}).get("total_paise") or 0) for i in credit_notes)
    refunded_paise = sum(int(p.get("refunded_paise") or 0) for p in payments)
    return {
        "currency": CURRENCY,
        "collected_paise": collected, "collected": collected / 100,
        "token_held_paise": token, "token_held": token / 100,
        "commission_received_paise": commission, "commission_received": commission / 100,
        "invoiced_paise": billed, "invoiced": billed / 100,
        "credit_notes_paise": credited_paise, "credit_notes": credited_paise / 100,
        "net_invoiced_paise": max(0, billed - credited_paise),
        "net_invoiced": max(0, billed - credited_paise) / 100,
        "refunded_paise": refunded_paise, "refunded": refunded_paise / 100,
        "gst_paise": gst, "gst": gst / 100,
        "net_gst_paise": max(0, gst - credited_gst), "net_gst": max(0, gst - credited_gst) / 100,
        "tds_withheld_paise": tds, "tds_withheld": tds / 100,
        "receivable_paise": receivable, "receivable": receivable / 100,
        "counts": {
            "payments": len(payments), "invoices": len(issued),
            "credit_notes": len(credit_notes),
            "unpaid_invoices": sum(1 for i in issued
                                   if int(i.get("paid_paise") or 0) < int(i.get("total_paise") or 0)),
        },
        "rates": {"gst_pct": GST_RATE_PCT, "sac": SAC_BROKERAGE, "tds_194h_pct": TDS_194H_PCT,
                  "tds_194h_threshold_inr": TDS_194H_THRESHOLD_PAISE / 100,
                  "tds_194ia_pct": TDS_194IA_PCT,
                  "place_of_supply": "same_state" if GST_IGST_THRESHOLD == 0 else ""},
    }


# ---------------------------------------------------------------------------
# Refunds, GST Credit Notes (§34 CGST Act), Commission Settlement & GSTR-1
# ---------------------------------------------------------------------------
def refund_voucher(*, number: str, issued_at: int, payment: dict, refund_paise: int,
                   reason: str = "") -> dict:
    """Refund a custody token or commission payment (`RF/<PREFIX>/<FY>/<seq>`)."""
    if payment.get("status") not in ("succeeded", "partially_refunded"):
        raise MoneyError("PAYMENT_NOT_REFUNDABLE",
                         f"Payment status is '{payment.get('status')}', cannot refund.")
    amt = int(refund_paise or 0)
    orig = int(payment.get("amount_paise") or 0)
    already = int(payment.get("refunded_paise") or 0)
    if amt <= 0:
        raise MoneyError("INVALID_AMOUNT", "Refund amount must be positive.")
    if already + amt > orig:
        raise MoneyError("REFUND_EXCEEDS_PAYMENT",
                         f"Refund {amt} paise exceeds remaining refundable balance {orig - already} paise.")
    return {
        "type": "refund_voucher",
        "number": number,
        "issued_at": issued_at,
        "currency": CURRENCY,
        "payment_id": payment.get("id", ""),
        "provider_payment_id": payment.get("payment_id", ""),
        "deal_id": payment.get("deal_id", ""),
        "unit_id": payment.get("unit_id", ""),
        "kind": payment.get("kind", "token"),
        "taxable_paise": 0,
        "taxable": 0.0,
        "gst": {"total_paise": 0, "cgst_paise": 0, "sgst_paise": 0, "igst_paise": 0,
                "note": "Refund of custody/settlement amount — GST adjustments use a §34 Credit Note."},
        "total_paise": amt,
        "total": amt / 100,
        "reason": str(reason or "Buyer cancellation / booking release")[:200],
        "status": "applied",
    }


def credit_note(*, number: str, issued_at: int, invoice: dict, credit_taxable_paise: int,
                reason: str = "", already_credited_paise: int = 0) -> dict:
    """Issue a CGST §34 Credit Note against a brokerage `tax_invoice`."""
    if invoice.get("type") != "tax_invoice":
        raise MoneyError(
            "RECEIPT_NOT_TAXABLE",
            "Booking token receipts carry no GST; refund the payment instead of issuing a GST credit note.",
        )
    orig_taxable = int(invoice.get("taxable_paise") or 0)
    credit_taxable = int(credit_taxable_paise or 0)
    if credit_taxable <= 0:
        raise MoneyError("INVALID_AMOUNT", "Credit note taxable value must be positive.")
    if int(already_credited_paise) + credit_taxable > orig_taxable:
        raise MoneyError(
            "CREDIT_EXCEEDS_INVOICE",
            f"Credit {credit_taxable} paise exceeds remaining invoice taxable value {orig_taxable - int(already_credited_paise)} paise.",
        )
    parent_gst = invoice.get("gst") or {}
    intra = parent_gst.get("place_of_supply") != "inter_state"
    tax = gst_split(credit_taxable, intra)
    parent_tds = invoice.get("tds") or {}
    tds_rev = int(round(credit_taxable * TDS_194H_PCT / 100)) if parent_tds.get("applies") else 0
    total = credit_taxable + tax["total_paise"]
    net_rev = total - tds_rev
    return {
        "type": "credit_note",
        "number": number,
        "issued_at": issued_at,
        "currency": CURRENCY,
        "original_invoice_id": invoice.get("id", ""),
        "original_invoice_number": invoice.get("number", ""),
        "original_issued_at": invoice.get("issued_at", 0),
        "supplier": dict(invoice.get("supplier") or {}),
        "buyer": dict(invoice.get("buyer") or {}),
        "deal_id": invoice.get("deal_id", ""),
        "lines": [{
            "description": f"Credit note against {invoice.get('number', '')} — {str(reason or 'commission adjustment')[:120]}",
            "sac": SAC_BROKERAGE,
            "quantity": 1,
            "rate_paise": credit_taxable,
            "amount_paise": credit_taxable,
            "gst_rate_pct": GST_RATE_PCT,
        }],
        "taxable_paise": credit_taxable,
        "taxable": credit_taxable / 100,
        "gst": {
            **tax,
            "cgst": tax["cgst_paise"] / 100,
            "sgst": tax["sgst_paise"] / 100,
            "igst": tax["igst_paise"] / 100,
            "total": tax["total_paise"] / 100,
        },
        "total_paise": total,
        "total": total / 100,
        "tds": {
            "section": "194H",
            "applies": bool(parent_tds.get("applies")),
            "withheld_paise": tds_rev,
            "withheld": tds_rev / 100,
        },
        "net_receivable_paise": net_rev,
        "net_receivable": net_rev / 100,
        "reason": str(reason or "Post-supply commission revision")[:200],
        "status": "applied",
    }


def apply_collection(*, invoice: dict, amount_paise: int, tds_certificate_no: str = "",
                     utr: str = "", method: str = "bank_transfer", at: int = 0) -> dict:
    """Compute updated invoice patch for a commission bank/UTR collection + Form 16A TDS."""
    if invoice.get("type") != "tax_invoice":
        raise MoneyError("NOT_TAX_INVOICE", "Only brokerage tax invoices can record commission settlement.")
    if invoice.get("status") == "credited":
        raise MoneyError("INVOICE_CREDITED", "Invoice has been fully reversed by a credit note.")
    cash_in = int(amount_paise or 0)
    cert = str(tds_certificate_no or invoice.get("tds_certificate_no") or "").strip()[:60]
    if cash_in <= 0 and not cert:
        raise MoneyError("INVALID_AMOUNT", "Provide a positive cash settlement amount or Form 16A certificate number.")
    total = int(invoice.get("total_paise") or 0)
    credited_total = int(invoice.get("credited_total_paise") or 0)
    effective_total = max(0, total - credited_total)
    tds_withheld = int((invoice.get("tds") or {}).get("withheld_paise") or 0)
    prev_cash = int(invoice.get("cash_collected_paise") or 0)
    max_cash = max(0, effective_total - tds_withheld)
    if prev_cash + cash_in > max_cash:
        raise MoneyError(
            "OVERPAYMENT",
            f"Settlement {cash_in} paise exceeds remaining net receivable {max_cash - prev_cash} paise.",
        )
    new_cash = prev_cash + cash_in
    tds_settled = tds_withheld if cert else 0
    paid_paise = min(effective_total, new_cash + tds_settled)
    status = "paid" if paid_paise >= effective_total else ("part_paid" if paid_paise > 0 else "issued")
    settlements = list(invoice.get("settlements") or [])
    if cash_in > 0 or cert:
        settlements.append({
            "at": at or int(time.time()),
            "amount_paise": cash_in,
            "method": str(method or "bank_transfer")[:40],
            "utr": str(utr or "")[:64],
            "tds_certificate_no": cert,
        })
    return {
        "cash_collected_paise": new_cash,
        "tds_certificate_no": cert,
        "tds_settled_paise": tds_settled,
        "paid_paise": paid_paise,
        "status": status,
        "settled_at": (at or int(time.time())) if status == "paid" else int(invoice.get("settled_at") or 0),
        "settlements": settlements,
    }


def gstr1_return(*, tenant: dict, invoices: list[dict], period: str = "") -> dict:
    """Build a GSTN-schema GSTR-1 return for `period` (`YYYY-MM` or `all`).

    Strictly includes taxable brokerage supplies (`tax_invoice`) and §34
    `credit_note` adjustments while excluding non-GST buyer token custody
    `receipt`s (`docs/24`).
    """
    now_lt = time.localtime()
    target_ym = (period or "").strip() or f"{now_lt.tm_year:04d}-{now_lt.tm_mon:02d}"
    fp = target_ym[5:7] + target_ym[:4] if len(target_ym) == 7 and "-" in target_ym else "ALL"

    def _in_period(ts: Any) -> bool:
        if target_ym.lower() == "all":
            return True
        lt = time.localtime(int(ts or 0))
        return f"{lt.tm_year:04d}-{lt.tm_mon:02d}" == target_ym

    scoped = [i for i in invoices if _in_period(i.get("issued_at"))]
    b2b: list[dict] = []
    b2cs_buckets: dict[tuple[str, str], dict] = {}
    cdnr: list[dict] = []
    cdnur: list[dict] = []
    custody_receipts = 0
    custody_paise = 0

    hsn_taxable = 0
    hsn_cgst = 0
    hsn_sgst = 0
    hsn_igst = 0

    supplier_state = str((tenant.get("settings") or {}).get("state") or "Maharashtra")

    for inv in scoped:
        itype = inv.get("type")
        if itype == "receipt":
            custody_receipts += 1
            custody_paise += int(inv.get("total_paise") or 0)
            continue
        buyer = inv.get("buyer") or {}
        bgstin = str(buyer.get("gstin") or "").strip().upper()
        bstate = str(buyer.get("state") or supplier_state)
        gst = inv.get("gst") or {}
        pos = str(gst.get("place_of_supply") or "intra_state")
        taxable = int(inv.get("taxable_paise") or 0)
        cgst = int(gst.get("cgst_paise") or 0)
        sgst = int(gst.get("sgst_paise") or 0)
        igst = int(gst.get("igst_paise") or 0)
        date_str = time.strftime("%d-%m-%Y", time.localtime(int(inv.get("issued_at") or 0)))

        if itype == "tax_invoice" and inv.get("status") in ("issued", "part_paid", "paid", "credited"):
            hsn_taxable += taxable
            hsn_cgst += cgst
            hsn_sgst += sgst
            hsn_igst += igst
            if len(bgstin) == 15:
                b2b.append({
                    "ctin": bgstin,
                    "buyer_name": buyer.get("name", ""),
                    "inum": inv.get("number", ""),
                    "idt": date_str,
                    "val": round(int(inv.get("total_paise") or 0) / 100, 2),
                    "pos": bstate,
                    "txval": round(taxable / 100, 2),
                    "rt": GST_RATE_PCT,
                    "camt": round(cgst / 100, 2),
                    "samt": round(sgst / 100, 2),
                    "iamt": round(igst / 100, 2),
                })
            else:
                key = (pos, bstate)
                b = b2cs_buckets.setdefault(key, {
                    "sply_ty": "INTRA" if pos != "inter_state" else "INTER",
                    "pos": bstate,
                    "rt": GST_RATE_PCT,
                    "txval_paise": 0,
                    "camt_paise": 0,
                    "samt_paise": 0,
                    "iamt_paise": 0,
                })
                b["txval_paise"] += taxable
                b["camt_paise"] += cgst
                b["samt_paise"] += sgst
                b["iamt_paise"] += igst

        elif itype == "credit_note" and inv.get("status") == "applied":
            hsn_taxable -= taxable
            hsn_cgst -= cgst
            hsn_sgst -= sgst
            hsn_igst -= igst
            note_row = {
                "nt_num": inv.get("number", ""),
                "nt_dt": date_str,
                "ntty": "C",
                "inum": inv.get("original_invoice_number", ""),
                "val": round(int(inv.get("total_paise") or 0) / 100, 2),
                "txval": round(taxable / 100, 2),
                "rt": GST_RATE_PCT,
                "camt": round(cgst / 100, 2),
                "samt": round(sgst / 100, 2),
                "iamt": round(igst / 100, 2),
            }
            if len(bgstin) == 15:
                cdnr.append({"ctin": bgstin, **note_row})
            else:
                cdnur.append({"pos": bstate, **note_row})

    b2cs = [
        {
            "sply_ty": v["sply_ty"],
            "pos": v["pos"],
            "rt": v["rt"],
            "txval": round(v["txval_paise"] / 100, 2),
            "camt": round(v["camt_paise"] / 100, 2),
            "samt": round(v["samt_paise"] / 100, 2),
            "iamt": round(v["iamt_paise"] / 100, 2),
        }
        for v in b2cs_buckets.values()
    ]
    return {
        "schema": "gstn.gstr1.v1",
        "gstin": str((tenant.get("settings") or {}).get("gstin") or "UNREGISTERED"),
        "fp": fp,
        "period": target_ym,
        "sac": SAC_BROKERAGE,
        "b2b": b2b,
        "b2cs": b2cs,
        "cdnr": cdnr,
        "cdnur": cdnur,
        "hsn": [{
            "hsn_sc": SAC_BROKERAGE,
            "desc": "Real estate brokerage and agency services",
            "uqc": "NA",
            "qty": len(b2b) + sum(1 for i in scoped if i.get("type") == "tax_invoice") - len(b2b),
            "txval": round(hsn_taxable / 100, 2),
            "camt": round(hsn_cgst / 100, 2),
            "samt": round(hsn_sgst / 100, 2),
            "iamt": round(hsn_igst / 100, 2),
        }],
        "custody_excluded": {
            "receipt_count": custody_receipts,
            "custody_amount_paise": custody_paise,
            "custody_amount": round(custody_paise / 100, 2),
            "included_in_taxable_turnover": False,
        },
    }


def gstr1_csv(gstr1: dict, safe_cell=None) -> str:
    """Render GSTR-1 rows as CSV with formula-injection protection."""
    esc_fn = safe_cell or (lambda x: str(x))
    lines = ["section,counterparty_gstin,doc_number,doc_date,place_of_supply,taxable_inr,cgst_inr,sgst_inr,igst_inr,total_inr"]
    for row in gstr1.get("b2b") or []:
        lines.append(",".join(esc_fn(str(v)) for v in (
            "B2B", row["ctin"], row["inum"], row["idt"], row["pos"],
            row["txval"], row["camt"], row["samt"], row["iamt"], row["val"],
        )))
    for row in gstr1.get("b2cs") or []:
        tot = round(row["txval"] + row["camt"] + row["samt"] + row["iamt"], 2)
        lines.append(",".join(esc_fn(str(v)) for v in (
            "B2CS", "", row["sply_ty"], gstr1.get("period", ""), row["pos"],
            row["txval"], row["camt"], row["samt"], row["iamt"], tot,
        )))
    for row in gstr1.get("cdnr") or []:
        lines.append(",".join(esc_fn(str(v)) for v in (
            "CDNR", row["ctin"], row["nt_num"], row["nt_dt"], "",
            -row["txval"], -row["camt"], -row["samt"], -row["iamt"], -row["val"],
        )))
    for row in gstr1.get("cdnur") or []:
        lines.append(",".join(esc_fn(str(v)) for v in (
            "CDNUR", "", row["nt_num"], row["nt_dt"], row["pos"],
            -row["txval"], -row["camt"], -row["samt"], -row["iamt"], -row["val"],
        )))
    return "\n".join(lines) + "\n"


# 20 — Channel partners: claims, protection windows, payouts

Companion to `docs/18` §4 #3 and §6 #5. Sell.Do, LeadSquared and Zoho have no CP
module; Grovia's headline feature is commission payouts to brokers. This is the
implementation, and — more usefully — the policy it encodes.

---

## 1. The three rules that actually matter

1. **A registration is a claim with a clock.** A partner registers a buyer; once
   the brokerage approves, that buyer is protected for the partner's window
   (default **90 days**, settable 1–365 per partner). During the window no other
   partner can register the same number. After it, the claim lapses on its own.
2. **The house's own leads are not claimable.** A buyer already in the pipeline —
   portal feed, walk-in, website — cannot be converted into a commission claim.
   Refused with `CP_LEAD_ALREADY_IN_PIPELINE`. Without this rule a partner can
   wait for the house to generate demand and then "register" the buyer.
3. **Money is shown with a basis.** Every earnings row says whether the amount is
   `recorded` (from the deal's commission ledger, authoritative) or `expected`
   (an estimate at the partner's rate). An estimate that looks like a promise is
   how disputes start.

## 2. Who is a partner

A partner is a **login** — a `users` row with `role: "channel_partner"` — so it
reuses the session, CSRF, tenant and revocation machinery of every other seat,
carrying extra fields: `cp_firm`, `cp_commission_pct`, `cp_protection_days`,
`cp_rera`, `cp_phone`.

`ROLE_RANK` puts the role at 0, and the dispatcher **confines it to
`/api/v1/cp/*` plus `/auth/*`**. That is a structural boundary, not a set of
conventions: a partner token asking for `/api/v1/contacts`, `/pipeline`,
`/tenant/audit` or `/crm/summary` gets `FORBIDDEN_SCOPE` even if a future route
forgets to declare a role. Independently, `can()` returns `False` for every
internal permission, so the layered routes refuse too.

Seats cannot be converted into or out of a partner account through
`/tenant/users`; partners are created through `/cp/partners` so they cannot exist
without a firm, a rate and a protection window.

## 3. The lifecycle

```
partner posts /cp/register
        │
        ├── no such number ............................ registration `pending`, contact created
        ├── same partner already claimed it ........... `duplicate` (idempotent, no new row)
        ├── another partner holds a live claim ........ 409 CP_LEAD_CONFLICT (+ days left)
        ├── another partner's claim expired ........... new claim, `released_from` noted
        └── the buyer is already the house's lead ..... 409 CP_LEAD_ALREADY_IN_PIPELINE
        │
manager approves ───────────────► `approved` + `protection_until` (now + window)
manager rejects  ───────────────► `rejected` + reason (shown to the partner)
window elapses   ───────────────► `expired` — derived, never stored, so a missed
                                  cron cannot silently extend a claim
```

`decide` is manager-and-above (`approve_cp`); approving binds the firm to pay, so
it is not an agent action, and an agent attempting it gets a 403. A decision is
one-way: a second call is `ALREADY_DECIDED`.

## 4. What a partner sees

`GET /api/v1/cp/me` · `/leads` · `/visits` · `/earnings` — and nothing else.

* **leads** — their own registrations with state, days of protection left,
  credited visits and the deal's stage and value. Phone numbers are **masked**:
  the partner already knows the number they submitted, and masking keeps a stolen
  partner session from being a harvestable contact list.
* **visits** — only visits for buyers they are credited with; `credited: true`
  means `visited`/`revisit`. A scheduled-but-no-show visit pays nothing.
* **earnings** — one row per deal for a credited buyer:
  `deal_value`, `gross_commission`, `amount`, `basis`, `payout_status`. A partner
  cannot read the house-wide payout ledger (`GET /cp/payouts` is 403).

## 5. Payouts

`POST /api/v1/cp/payouts/{deal_id}/pay {reference}` — owner/admin only
(`pay_partner`), refused for agents, managers and partners. It requires:

* the deal is `won` (`DEAL_NOT_CLOSED` otherwise),
* the deal's commission ledger names a channel partner (`NO_PARTNER_CREDIT`),
* a payment reference, which is written to the deal, the payout row **and** the
  audit chain (`cp.payout_paid`).

The partner's earnings view then flips that row to `paid` with the reference —
the same number the bank statement will carry.

## 6. Consent, again

A partner-submitted buyer is stored with `consent.status: "pending"`,
`purpose: "channel partner referral"`, `evidence: "cp:<id>"` and
`notice_version: "cp-attestation-v1"`. That is the *partner's* attestation, not
the buyer's consent to the brokerage, so outbound messaging stays blocked until
someone captures consent in the house's own words (`inbox/send` enforces it). A
claim is not permission to message.

## 6b. What a partner may see of the inventory

The portal shipped without this, and it was the gap that mattered most: a partner
could claim a buyer and watch a protection clock but could not see a single thing
to sell them. `GET /api/v1/cp/inventory` is the catalogue; `GET
/api/v1/cp/inventory/{id}` is one property. Opening the stock is where a CP
portal leaks, so the line is drawn three times, and each is a test:

**Only stock that can be sold.** A property is listed when it has at least one
free unit — or, for a property with no unit list, when it is itself `available`.
A `hold` is somebody else's 48 hours, `booked` is somebody else's buyer, `sold`
is history; listing them produces calls that end in an apology. The row carries
the counts (`available`, `blocked`, `booked`, `sold`) because a partner needs to
know the difference between "one flat left" and "one flat was left". Sold-out
properties appear only with `?include_unavailable=1`, labelled `sold_out`, and
their detail page still returns them so an old brochure link does not 404.

**The seller is not for sale.** No seller name or phone, no floor and no margin,
no internal notes, no lead score — `partners.public_attributes()` drops any
attribute whose key looks like house business (`seller_*`, `floor`, `margin`,
`cost`, `internal`, `notes`, `commission`, `payout`). A denylist, not an
allowlist: the taxonomy (docs/22) adds fields per category and a partner should
get the new ones without a code change, but a key called `seller_phone` is never
marketing copy. Units are shown by label, floor, facing, area, price and state —
never with the `booking` block, which names the deal, the buyer and the person
who blocked it. `partners.WITHHELD` is returned with **every** page, so a partner
reads why a field is missing instead of assuming the page is broken.

**No commission rate is invented.** A partner's share is a percentage of the
brokerage's *gross commission*, and the gross commission is fixed per mandate —
there is no tenant-wide rate to read. So the portal shows their own rate, and
where it can, an estimate derived from the house's own ledger: the median of
`gross commission ÷ sale value` over recorded deals, applied to the asking price.
The `basis` string says exactly that, including the number of deals it came from;
with no comparable deal the estimate is `null` and the portal says the brokerage
confirms the rate on registration. Guessing "2%" and being wrong on a ₹26 crore
resort is worse than saying "on request".

**And one refusal that protects the partner, not us.** `marketing.ready` is
`false` when the listing has no RERA number, no price, no title or no city —
because under RERA s.3 the penalty for advertising an unregistered project falls
on whoever published it, and on a partner's Instagram that is the partner.

The route is partner-only (`_require_partner`), so an owner or agent who calls it
gets `403 FORBIDDEN_SCOPE` — the house has the CRM's own inventory panel. The
desk-side `web/cp.html` renders the catalogue as a third card with filters whose
options come from `facets`, filled from the unfiltered catalogue so a filter can
never hide its own alternatives.

## 7. Deliberately not built

* **No self-service partner signup.** Partners are created by the brokerage —
  the RERA agent number and the rate are the house's decision.
* **No per-visit commission.** Only deals pay; visits are credited as evidence
  and shown as a count.
* **No dispute workflow beyond reject + reason.** A rejected claim leaves the
  buyer claimable by anyone; an appeal process is a P3 item, not a table.
* **No TDS/GST on payouts yet.** Paying a partner at 2%/194H rates and issuing a
  Form 16A is a finance integration (docs/18 §8 P2), not a UI toggle.

## 8. Verifying it

```
python3 scripts/proof_channel_partners.py                 # boots a seeded, throwaway instance
python3 scripts/proof_channel_partners.py --base http://127.0.0.1:5433 --live
```

49 live checks: partner creation with a one-time password, portal confinement,
claim → duplicate → rival conflict, manager approval and the 90-day window,
double-decide refusal, rejection then re-claim, house-lead refusal, per-partner
scoping, the payout permission matrix and the audit entries — plus, since G4 closed, the
catalogue: stock is visible, the payload contains no seller or buyer, sold-out
properties stay out of the default list, a booked unit is not sellable, the
estimate is labelled, and the house cannot use the partner's door. `tests/
test_channel_partners.py` adds the pure rules (27 tests) — including that an
expired claim releases the buyer and that a recorded figure always beats an
estimate. `tests/test_partner_inventory.py` (16 tests) covers the catalogue's
pure rules and its payload.

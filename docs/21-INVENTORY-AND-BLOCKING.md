# 21 — Unit inventory, holds, booking and sale

docs/18 §4 #4 and §6 #6. "The property is available" is not a unit, and every
double-booking complaint in a brokerage is the same bug: the CRM modelled the
project, two agents promised the same flat, and the second buyer found out at
registration.

---

## 1. The model

A **unit** belongs to a property: `unit_no`, `floor`, `type`, `area_sqft`,
`price`, `facing`, `status`, and — this is the part that matters — a `block`, a
`booking`, a `sold` record and an append-only `history[]`.

| Status | Meaning | How it is reached |
|---|---|---|
| `available` | sellable now | created, released, or an expired hold |
| `blocked` | held for **one** deal until an expiry | `POST /units/{id}/block` |
| `booked` | token received, unit committed | `POST /units/{id}/book`, or the deal reaching `token` |
| `sold` | registered | `POST` the deal's advance to `registration`/`won` |
| `withdrawn` | pulled by the developer | `PATCH /units/{id}` |

## 2. Three rules, and why each one exists

**A hold is timed.** `block(unit, deal, hours)` — default **48 hours**, which is
the "48-hour price hold" the funnel pages already advertise. Expiry is **derived
at read time**: an expired hold reads as `available` with `block_expired: true`
and no job has to run. A hold that survives on a cron schedule is a promise the
system cannot keep.

**Only one deal holds a unit.** A second block is a 409 `UNIT_BLOCKED` whose
details name `held_by_deal`, `held_by_contact`, `until` and `hours_left` — an
agent standing with a buyer needs to know who to call, not that something is
"unavailable". The holder may renew its own hold (`renewal: true`).

**State only moves forward.** `available → blocked → booked → sold`, with release
back to `available` from `blocked`/`booked` (a cancelled booking) and
`withdrawn` for a pulled unit. A status `PATCH` cannot reach `booked` or `sold`
(`USE_THE_DEAL`) — a sale with no token payment is a data-entry accident, not a
shortcut. `sell()` refuses a unit that was not booked *by that deal*
(`SALE_WITHOUT_BOOKING`).

## 3. The deal carries the unit

If a deal has a `unit_id`, advancing it moves the unit with it, **before** the
stage write so a refusal leaves both untouched:

* `→ token` books the unit (and records the token amount if one was set);
* `→ registration` / `→ won` sells it, and refuses if this deal did not book it.

So a pipeline board and an inventory grid can never disagree: the stage that
means "money taken" is the stage that takes the unit off the market.

## 4. Generation

`POST /properties/{id}/units/bulk` builds a deterministic floor grid —
floor-major, `A/B/C…` per floor, price laddered by `price_step` per floor above
`start_floor`. Deterministic on purpose: two people clicking "generate" must not
produce two different sets of units with the same numbers. Re-running skips unit
numbers that already exist and reports `created` / `skipped`.

## 5. Endpoints

| Method | Path | Scope |
|---|---|---|
| GET | `/properties/{id}/units` | read — units, summary, default hold hours |
| POST | `/properties/{id}/units` | write — one unit |
| POST | `/properties/{id}/units/bulk` | write — a grid |
| GET | `/units?status=` | read — across properties, with summary |
| PATCH | `/units/{id}` | write — price, area, facing, withdraw/reinstate |
| POST | `/units/{id}/block` | write — `{deal_id, hours, reason}` |
| POST | `/units/{id}/release` | write — own hold, or manager+ for anyone else's |
| POST | `/units/{id}/book` | write — `{deal_id, token_amount}` |
| GET | `/units/audit` | write — who held/booked/sold what, newest first |

Releasing another deal's hold is refused for agents (`FORBIDDEN_SCOPE`) and
allowed for managers and above — otherwise an agent can quietly free a unit a
colleague has already promised to a buyer.

## 6. In the console

The desk's **Inventory** panel: pick a property, see the ledger tiles (available /
on hold / booked / sold with values), and a grid of units coloured by state. Each
unit carries `hold`, `book`, `release`, `withdraw`/`reinstate` actions that apply
to the deal open in the drawer, or ask which deal when none is open. A hold shows
its remaining hours and the deal that owns it.

## 7. What is deliberately not here

* **No per-unit documents or price revisions.** A cost sheet per unit belongs
  with document automation (P2), not in the unit row.
* **No waitlist.** A released unit simply becomes available; brokers keep a
  waitlist in a notebook and out of scope.
* **No automatic hold on every enquiry.** Holds cost inventory; they are an
  explicit act by an agent or manager, with a reason field.

## 8. Verifying it

```
python3 scripts/proof_inventory.py                       # seeded throwaway instance
python3 scripts/proof_inventory.py --base http://127.0.0.1:5433 --live
```

20 live checks, all passing on both: grid generation and idempotency, hold →
booking → refusal matrix, a status change that cannot fake a sale, agent
permission on release, the deal advance carrying the unit, unit history, and
tenant isolation. `tests/test_inventory.py` adds 24 tests, most of them about
refusals and about a hold releasing itself with nothing having run.

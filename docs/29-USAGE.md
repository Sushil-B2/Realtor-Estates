# 29 — Usage meters: what a workspace actually used

> **Gap G8, item 3.** `GET /api/v1/tenant` used to answer `voice_minutes: 4820` and
> `whatsapp_messages: 18422`. Neither number was measured. Both were literals in
> the route, and `ai_runs` was a sum over the seeded `agents.runs` counters, which
> nothing ever reset and no invoice ever read. A customer on a usage plan would
> have been billed from three constants in a source file.

This document is the contract for the meters: where each number comes from, when a
plan refuses work, and what happens to a month that has already been invoiced.

---

## 1. The three claims a meter has to earn

A number on a billing screen is worth exactly what its provenance is worth. So
every meter here has to be:

| claim | what it means | where it is enforced |
|---|---|---|
| **derived** | the number is read from the rows that already record the work, so it moves when the work happens and by the amount of work done | `meters.derive` |
| **ledgered** | where no row can hold it (a credit, a byte count), an append-only entry holds it: idempotent on its source, owner-only, audited | `meters.record`, `POST /usage/adjust` |
| **enforced** | a plan's allowance refuses the work *before* it is done — on the manual path and the autonomous one — and a finished month can be frozen so a later purge cannot shrink the invoice | `meters.enforce`, `meters.close` |

Anything that cannot meet all three is not a meter and is not on the panel.

---

## 2. A period is a month in the workspace's timezone

```
period     "YYYY-MM"
timezone   tenant.settings.timezone, default Asia/Kolkata
bounds     [period_bounds(p)[0], period_bounds(p)[1]] — the first and last second
reset      next_reset_at(p) = the first second of the next month
```

`TZ_OFFSETS` holds fixed offsets (`Asia/Kolkata` +05:30, `UTC` 0). Fixed rather
than tz-database-driven on purpose: a billing period must not move because a
jurisdiction changed a daylight-saving rule, and India has none. An unknown zone
falls back to IST rather than raising, because a meter that fails closed at month
end is worse than one that bills in the documented default.

The consequence is visible and tested: `1795881000`-era instants near a boundary
belong to different periods in IST and UTC. A UTC month would bill the first five
and a half hours of the 1st to the *previous* invoice. `derive` filters rows by
`at`/`created_at` inside the bounds, so a row is in exactly one period.

`period_bounds("2026-02")` is 28 days, `period_bounds("2026-11")` is 30, and
`shift_period` crosses year boundaries (`2026-01 → 2025-12`).

---

## 3. The meters

| meter | unit | kind | source |
|---|---|---|---|
| `ai_runs` | runs | flow | `runs` with a counted status (below) |
| `ai_runs_blocked` | runs | flow | `runs` with `status == "blocked"` — **uncapped on purpose** |
| `ai_spend_micros` | µUSD | flow | `Σ runs.cost_usd × 1e6` over counted runs |
| `whatsapp_out` | messages | flow | outbound `messages` in `conversations`, channel `whatsapp` |
| `sms_out` | messages | flow | the same, channel `sms` |
| `voice_minutes` | minutes | flow | `activities` of type `call`/`ai_call`/`voice`, `(seconds + 59) // 60` **per call** |
| `contacts` | contacts | stock | `contacts` rows |
| `storage_mb` | MB | stock | **ledger only** — the image pipeline reports bytes |

A **flow** is per-period usage. A **stock** is a level (`contacts`, `storage_mb`):
it does not reset at month end, its cap is a ceiling rather than an allowance, and
it therefore reads the **same in every period** — the November report and the
August report both show today's contact count, because that is what "stored" means.
A snapshot freezes a stock at its level on the day it was closed, which is the only
honest thing a closed month can say about a number that keeps moving.

### Counted, blocked, uncounted

```
COUNTED_STATUSES   ("ok", "partial", "complete", "held")
BLOCKED_STATUS     "blocked"
UNCOUNTED_STATUSES ("skipped", "pending", "refused")
```

The rule is *did it reach a provider*. `partial` (a tool awaited approval) and
`held` (an answer withheld after it was paid for) both spent money and are billed.
`blocked` (a guardrail, a kill switch, a spend cap) never reached a provider: it is
reported under `ai_runs_blocked` and costs nothing. `skipped`/`pending` are the
runtime's queue states and belong to neither.

`ai_runs_blocked` has **no cap**, and that is deliberate: a workspace must not be
able to hit a limit by being protected, and a capped guardrail meter would let a
plan hide a guardrail doing its job.

Voice is billed **per call**, per started minute: 134 seconds is 3 minutes, and two
30-second calls are 2 minutes — not the 1 minute a month's rounded total would
give. Rounding the total would under-bill every workspace by up to a minute a month
and hide the rule from the customer, and a call is sold by the call, not by the
month. A zero-length call (a missed call that never connected) is not billed.
Legacy activity rows with a `"2m 14s"` text and no `duration_sec` are parsed rather
than read as zero, because those rows are real calls that were really paid for.

### Plans

`PLAN_CAPS` — `trial`, `starter`, `growth`, `scale`. `None` means unlimited and is
written rather than omitted, so "we forgot to set a cap" and "there is no cap"
cannot look the same. An unknown plan falls back to `trial`: a typo must not be an
unlimited plan.

---

## 4. The ledger

`usage_ledger` is append-only. A row is
`{meter, qty, source_id, reason, at, period}`; `qty` is signed, so a credit is a
negative row and nothing is ever edited.

* **Idempotent on `(tenant_id, meter, source_id)`.** A webhook redelivery, a re-run
  proof, a client that never saw the response all arrive twice. A meter that counts
  them twice overbills. The second call returns `duplicate: true` and adds nothing.
* **Owner-only, reasoned, audited.** `POST /api/v1/usage/adjust` requires
  `manage_tenant` *and* the `owner` role, a `reason` of at least four characters,
  and a known meter. It appends `usage.adjusted` to the audit chain. A manager may
  read the dashboard but not the ledger: the dashboard is a gauge, the ledger is
  the audit side of a bill.
* **Readable.** `GET /api/v1/usage/ledger?meter=&period=` returns the newest 200
  rows. A mistaken credit is corrected by another row, which is only trustworthy if
  the first one is still visible.

`report` shows `used = measured + adjusted`, and never only the total: a number
that is 90 % credits should not look like a measured 90 %.

---

## 5. Enforcement

`meters.enforce(store, tenant, meter, add=1)` raises `QuotaExceeded` when
`used + add > cap`. The API maps it to **`402 QUOTA_EXCEEDED`** with
`details: [{meter, used, cap, period, resets_at}]`, before `except ApiError`.
There is deliberately no `429` and no `403`: this is not rate limiting and not a
permission problem, it is the plan's allowance.

**A flow resets; a stock does not.** So the refusal has to say different things,
and `resets_at` is `null` for a stock:

```
flow   SMS messages sent for this plan: 20,001 of 20,000 used. It resets on 01 Dec 2026.
stock  Contacts stored for this plan: 25,001 of 25,000. That is a ceiling, not a
       monthly allowance — raise the plan, or archive what is no longer needed.
```

Telling somebody their stored contacts come back on the 1st would be a lie, and a
customer who acts on it will wait a month for a door that never opens.

| site | meter | checked |
|---|---|---|
| `POST /api/v1/contacts` | `contacts` | before the number is sealed or the row written |
| `POST /api/v1/inbox/{id}/send` | `whatsapp_out` / `sms_out` | **before** consent and quiet hours — a plan that has run out is not a compliance problem and must not be reported as one |
| `POST /api/v1/agents/{id}/run` | `ai_runs` | before the provider call |
| automation runtime `_apply` | `whatsapp_out` / `sms_out` | the autonomous path spends the same allowance; a miss raises `AutomationError("QUOTA_EXCEEDED")` and the run is **held**, not sent |

A held run is the point of the design: the runtime's job is to send, so a cap that
only applied to the API would be a cap on the wrong door. Nothing is sent, the run
records why, and the message goes out when the month turns over or the plan is
raised.

**Warnings.** At 80 % of any cap, a mutating response carries

```
X-Usage-Warning: storage_mb 85.0% of 20,480 MB
```

A header, not a modal: an allowance at 80 % should not interrupt work. Reads carry
no header. `warnings()` is memoised for `WARN_TTL = 20` seconds and the memo is
cleared by any `record`, because a per-write `derive` is a scan of `runs`,
`conversations` and `activities`. **That memo is a hedge, not a design**: the real
answer is incrementing counters at write time, and this document says so rather
than letting a cache look like a solution.

---

## 6. Closed periods

`POST /api/v1/usage/close {period}` copies the period's figures into
`usage_snapshots`: `{period, label, plan, meters, caps, closed_at, closed_by}`.
It is `manage_tenant`, audited, and **idempotent** — a second close returns
`already_closed: true` and the same snapshot.

* The **open month cannot be closed** (`400 PERIOD_STILL_OPEN`). A period that is
  still accumulating has no final figures; "closing" it would be a snapshot of a
  number that is going to change.
* A closed period **reads back frozen**: `report` shows `closed: true` and the
  snapshot's `closed_at`, and `series` reports the snapshot's meters instead of a
  live recompute.
* A late adjustment aimed at a closed period is **refused** (`409 PERIOD_CLOSED`)
  and must be posted to the open month with the old period named in the reason.
  Rewriting a figure somebody has already been billed for is not a correction.
* `invoice_lines` reads the snapshot when there is one and reports
  `source: "snapshot"`, with `overage = max(0, quantity − included)`.

This is what makes the meters compatible with `docs/26`: a purge deletes rows, and
a bill must not be shrinkable by a purge. `usage_snapshots` is in
`proofing.NEVER_PURGE`; `usage_ledger` is purgeable *only* for an open period and
the purge reports the rows it refused to touch under `frozen`.

---

## 7. Routes

| method | path | who | purpose |
|---|---|---|---|
| `GET` | `/api/v1/usage?period=` | any member | this period's meters against the plan, `fingerprint`, `over`, `near`, `resets_at` |
| `GET` | `/api/v1/usage/series?periods=1..24` | any member | the last N months, oldest first, closed months from snapshot |
| `GET` | `/api/v1/usage/ledger?meter=&period=` | `manage_tenant` | adjustments and ledger-only meters, newest first |
| `POST` | `/api/v1/usage/close` | `manage_tenant` | freeze a finished month; returns the snapshot and invoice lines |
| `POST` | `/api/v1/usage/adjust` | **owner** | credit or charge a meter; requires `reason`, idempotent on `source_id` |

`GET /api/v1/tenant` now carries `usage` from `summary_for_tenant_row`: the four
numbers the workspace card shows, for this month, measured — plus
`measured_from: "rows"` so a client can tell a measurement from a decoration.

---

## 8. What is *not* in this item

* **No payments.** `invoice_lines` says what a usage-based invoice *would* bill.
  Nothing here charges a card, and nothing here is a GST document (`docs/16` owns
  tax, and its totals are in paise).
* **No counters.** Every read is a scan of three collections. Correct, cheap at
  demo scale, and the known scaling debt named in §5.
* **`storage_mb` has no producer yet.** `property_media` is empty in the demo, so
  the meter is ledger-only until the image pipeline reports bytes.
* **Not a quota *manager*.** Caps are code (`PLAN_CAPS`), not a per-tenant override
  table. A negotiated allowance is a ledger credit, which is visible, audited and
  reversible — and that is the right primitive until an override table earns its
  keep.
* **Nothing in the runtime counts `ai_spend_micros`.** It is derived, and the plan
  cap on provider spend is enforced by `agents`' own spend cap (`docs/09` §…), not
  by `meters.enforce`.

---

## 9. The tests

`tests/test_usage.py` (26 functions) is organised by the three claims:

* **clock** — IST month boundaries, every second in exactly one period, year
  crossings, started minutes per call.
* **derived** — the card and the meter are the same measurement, and the old
  literals 4820/18422 are gone; a contact moves `contacts` by one; a WhatsApp
  message moves `whatsapp_out` and not `sms_out`; two 30-second calls are two
  minutes and a missed call is none; a run is counted by the rule its status
  implies.
* **ledgered** — charge and credit both land and are both visible; the same
  `source_id` twice is one row; a manager cannot adjust or read the ledger; bad
  meters, bad quantities and missing reasons are `400`.
* **enforced** — a spent contacts / SMS / AI allowance refuses with `402` before
  anything is written or sealed, and the stock refusal carries no reset date while
  the flow refusal does; the automation runtime holds the send instead; 80 % warns
  in a header on writes only, and the header goes away.
* **closed** — the open month cannot close; a finished month closes once and reads
  back frozen; a late write to it is `409`; a purge removes open-month proof noise
  and leaves an invoiced row, reporting it under `frozen`.

`scripts/proof_usage.py` is the live counterpart (62 checks live, 47 on a
throwaway instance): it measures, enforces and reports against a running server
without trusting any of it, and it reverses every allowance it spends. What it
cannot reverse is a *side effect* — a message it sent and a call it logged stay on
the desk (`docs/26` reports those rows but does not delete them), and the meters go
on counting them, because the work happened. That is the meter being right, not the
demo being dirty.

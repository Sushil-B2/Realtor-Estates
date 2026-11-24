# 16 — Two and three: the scheduled sweep and the payments webhook

The photo pipeline (`docs/15`) was the first Neon Function here. This document
covers the other two, which are deployed from the same `neon.ts`:

| slug | kind | trigger / caller | owns |
|---|---|---|---|
| `propertyphotos` | upload handler | `storage_object_created` → `properties/*` | `re_property_media` |
| `compliancesweep` | scheduled job | `schedule`, `0 2 * * *` UTC | `re_compliance_runs` |
| `paymentswebhook` | public HTTP endpoint | Razorpay (HMAC-signed) | `re_webhook_events` |

Slugs are `^[a-z0-9]{1,20}$` — no hyphens. `@neon/config` rejects anything else,
and it rejects it locally, before a deploy is attempted: running `neon.ts`
through `defineConfig` is cheaper than discovering a naming rule from a 400.

Each function gets its own table and the app gets SELECT only. That keeps the
single-writer rule that governs `re_documents` (`docs/12`) intact, and it means
the web tier cannot forge a clean sweep or invent a payment.

---

## 1. `compliancesweep` — the nightly run

02:00 UTC = 07:30 IST, before the working day, so a finding is on screen when the
office opens.

It does three things, in order of how much it can actually fix:

1. **Orphan media purge (destructive, but only over its own data).** A photo
   deleted at the source leaves its derived variants behind — the known
   limitation of `docs/15 §8`. The sweep finds `re_property_media` rows whose
   source object is gone, deletes the derived objects and the rows. Live media is
   untouched, and a transient storage error never justifies a delete: only a real
   `404`/`NotFound` counts as "the source is gone".
2. **Retention evidence (read-only).** Per retention class — call recordings
   365 d, chat 540 d, audit 2555 d, the numbers the app itself advertises — how
   many rows exist and how old the oldest is. It reports; it does not delete.
   Documents belong to the app's single writer, and an audit log is append-only:
   a cron job must not be the thing that removes them.
3. **Audit-chain integrity (read-only).** Every `re_audit` row carries
   `prev_hash`. The check compares each row's `prev_hash` with the previous row's
   `hash` **for the same tenant** — the property the chain actually promises —
   and needs no knowledge of the hash function.

### The gap that is not a finding

The first version also flagged gaps in `seq`. The live table proved it wrong:
`seq` is a global `bigserial` shared by every tenant (480 rows, span 2…486), so
per-tenant partitioning reports phantom gaps, and genuinely missing values come
from rolled-back transactions — a sequence never reuses a number. Gaps are now
counted and labelled informational (`global_seq_gaps`), and `ok` depends only on
broken links and duplicate `seq` values. A check that cries wolf gets ignored,
which is worse than one that is narrow and trusted.

### Output

```json
{
  "ok": true, "status": "ok", "run_id": "run_b275d92aadd2f36557bd",
  "audit": {"checked": 480, "broken_links": 0, "duplicate_seq": 0,
            "global_seq_gaps": 1, "head_seq": "486", "ok": true},
  "media": {"scanned": 3, "purgedSources": 1, "purgedObjects": 3, "purgedRows": 3},
  "retention": [{"class": "chat_days", "rows": 14, "oldest_at": 1794862085, "past_horizon": false}]
}
```

One row per run in `re_compliance_runs`, so "did it run, and what did it see" is
answerable months later without log access. Read it back at
`GET /api/v1/compliance/runs` (needs `manage_tenant`; the numbers are
platform-wide aggregates, so the gate matters).

## 2. `paymentswebhook` — an endpoint that bills

The app already declared `razorpay` but had no inbound handler. This is the
receiver, and the rules it follows are the ones receivers usually get wrong:

- **Sign the raw bytes.** The HMAC is computed over the exact body received,
  before any JSON parsing. Parse-then-reserialise changes key order and
  whitespace and every signature fails.
- **Constant-time compare, equal lengths only.** `timingSafeEqual` throws on a
  length mismatch; the code checks length first, so a 2-character signature is a
  `401`, not a `500`.
- **At-least-once delivery.** Dedupe on `(provider, event_id)` with a unique
  index — in the database, not a read-then-write race. A retry is a no-op and
  still answers **200**: a 4xx on a duplicate makes a provider retry forever.
- **Never persist an unauthenticated body.** A bad signature gets `401` and
  writes nothing, so the endpoint cannot be used to fill the database.
- **Cap the body** (64 KiB) before buffering it.
- **Attribute the tenant.** The provider has no idea which workspace a payment
  belongs to — it travels in the order's `notes`. The function copies
  `notes.tenant_id` into a real column (`db/007`), so the app filters on a column
  and an event that could not be attributed is visible to **no** workspace.

Rotation is supported without a code change: `PAYMENTS_WEBHOOK_SECRET` and an
optional `PAYMENTS_WEBHOOK_SECRET_PREV` are both accepted, and each stored row
keeps a key **fingerprint** (first 8 hex of SHA-256 of the secret) — never the
secret — so a rotation is auditable.

Read them back at `GET /api/v1/webhooks/events` (needs `manage_tenant`,
tenant-scoped).

## 3. Proofs

```bash
npm run functions:dev     # all three functions on 127.0.0.1:5434
npm run sweep:proof       # plants a real orphan, runs the sweep, checks the purge
npm run webhook:proof     # signs a real payment.captured, replays it, forges it
npm run photos:proof      # the upload pipeline (docs/15)
```

`sweep:proof` is deliberately destructive-but-honest: it uploads a photo, lets
the pipeline derive three variants, **deletes the source object**, then asserts
the sweep removed exactly those three objects and three rows and left the six
live rows alone.

Measured on this branch:

```
» 3. deliver the schedule trigger      → 200, run_b275d92aadd2f36557bd in 4417 ms
» 4. orphans gone, live media intact   ✓ 0 rows / 3 objects deleted / 6 rows untouched
» 5. recorded                          ✓ status=ok, audit ok=true, purged 3 rows
» 6. no trigger header                 ✓ 401 missing_header
» 7. storage-shaped delivery           ✓ 400 unsupported_trigger_type

» webhook: signed → 200 recorded · retry → 200 duplicate · tampered → 401
»          unsigned → 401 · 2-char signature → 401 (no 500) · 65 KiB → 413
»          row: INR 1250000, pay_254b2092, tenant tnt_9889c8d9f0
```

## 4. What is still not deployed

`neon deploy` needs an authenticated CLI, and this sandbox cannot complete the
browser OAuth flow. Everything is ready: `neon.ts` validates against
`@neon/config`, all three proofs pass against the live branch, and the two
webhook/sweep tables exist with the grants the app needs. What is missing is the
key — §7 of `docs/15` has the exact commands, and `.env.local` already has a
labelled placeholder line for it.

## 5. Deliberate non-goals

- **The sweep does not delete documents or audit rows.** See above: the single
  writer and the append-only log stay the app's business. A retention *action*
  belongs behind an owner decision, not a cron expression.
- **The webhook does not mutate the deal pipeline.** It records the verified
  event; reconciling it against a deal is application logic with its own rules
  (partial payments, refunds, idempotency across stages) and deserves its own
  design rather than being smuggled into a webhook handler.
- **No queue.** Both functions are request/response within the 15-minute limit;
  the sweep's per-run caps (25 orphan sources, 20 000 audit rows) are what keep
  that true.

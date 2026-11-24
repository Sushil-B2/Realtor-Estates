# 23 — Automation runtime: triggers that fire, gates that hold

**Question.** `PLANS.md` G1 named the hole plainly: the CRM could *describe* an
automation ("new portal lead → 5-min WhatsApp + AI call") but nothing ever ran
one. The PRD's number-one job — answer every enquiry in 90 seconds without a human
waking up — was clocked (`docs/19`) and never delivered. This document is the
runtime that closes it, and the reasoning behind three decisions that shape it.

---

## 1. The shape

```
                     ┌────────────── scan() ──────────────┐
  contacts/visits/   │  triggers evaluated from the rows  │→  runs[]  (idempotency key)
  units/deals  ──────┤  lead.created · visit.reminder_24h │
                     │  unit.hold_expiring · …            │
                     └────────────────────────────────────┘
                                     ↓
  gate(step)  ── consent · DND · quiet hours · kill switch · frequency cap
                                     ↓
  step effects ── conversation message · task · owner · team note · stage
                                     ↓
  runs[].trace  ── who fired, what ran, why it did not, when it retries
```

`app/automations.py` holds the whole thing as pure rules plus one `Runtime` class
that takes a store and a clock. The HTTP surface is five routes; the worker is a
thread in `app/server.py` that ticks every 15 s.

## 2. Three decisions, with their costs

### 2.1 Triggers are evaluated from data, not pushed by an event bus

A scan asks "which leads were created in the last five minutes and have no
outbound touch?" — it does not wait to be told.

* **Why:** a dropped webhook, a restarted process, or a tick that never happened
  cannot lose work, because the work is re-derivable from the rows. This matters
  most for exactly the leads the SLA exists to protect: the ones arriving while
  the deployment is happening.
* **Cost:** latency is bounded by the tick interval (15 s) rather than being
  instant — acceptable against a 300 s portal SLA, not acceptable for in-call
  actions, which stay on the request path.
* **Consequence:** `scan` must be cheap and side-effect-free. It reads four
  collections and returns subjects; nothing is sent during a scan.

### 2.2 Idempotency is a key, not a lock

Every run carries `automation_id:subject_id:occurrence`, and a run is refused if
the key already exists.

* `occurrence` is what makes "one reminder per visit-day" different from "one
  reminder per tick": `reminder-2026-11-19`, `stale-2026-W47`, `hold-<epoch>`.
  A plain "did it once" flag would mute a repeating need forever; a plain timestamp
  would fire on every tick.
* **Cost:** the key must be unique somewhere the two processes can both see. That
  is a unique index, not a dictionary: `db/008_run_idempotency.sql` puts one on
  `(tenant_id, doc->>'idempotency_key') where collection = 'runs'`, and the loser
  of a race gets a constraint violation, which the runtime reads as "somebody
  else already claimed this occurrence" and skips (`Runtime._claim_run`).
* **Consequence:** two app instances, a retried HTTP tick and a worker tick all
  converge on one run. That is the property that makes a 15 s tick safe — and it
  is the property that *fails* if the in-memory check is trusted alone, because
  reads come from a boot-time snapshot. A tick therefore re-reads the rows it is
  about to decide on (`Store.reload` → `Runtime._refresh`, "when stale reads
  change a decision, don't read stale").

### 2.3 A tick is cheap, a session is not shareable

The worker is a second thread in the same process, and one psycopg connection is
one session with one transaction at a time. Sharing it made the demo fail in the
least obvious way available: the worker's `set_config('app.tenant_id', …)` landed
inside a login's insert, RLS refused the audit row for belonging to another
workspace, and the aborted transaction then made every later statement fail with
"current transaction is aborted" — one 500 became an outage until restart.

So: the worker builds its **own** `Store` (its own connection), and `_run` holds a
re-entrant lock around every statement plus a rollback on failure. Both are
tested without a database in `tests/test_db_threading.py`. A pool is the right
answer at scale (`docs/02 §5`); a session per thread plus a lock is the right
answer for one process with a background worker.

### 2.4 The gates run before the step, not inside the transport

Consent, DND, quiet hours, the tenant kill switch and a frequency cap are
evaluated by the runner and recorded as a verdict.

| Gate | Verdict | Records |
|---|---|---|
| Consent not `granted` | `block` | `Consent is pending` |
| DND set on the contact | `block` | `Contact is on DND` |
| Tenant kill switch on | `block` | `Tenant kill switch is on` |
| Inside quiet hours | `defer` | `Quiet hours (21:00–09:00 Asia/Kolkata (now 22:00))` |
| ≥ 6 outbound in 24 h | `defer` | `Frequency cap reached (6 outbound messages in 24 h)` |
| Output guardrail blocks the text | `block` (via step error) | guardrail findings |

* **Why the transport does not decide:** a WhatsApp BSP will happily send at
  midnight; the obligation is ours, and the *reason* is what a manager needs to
  see. A blocked send is not an error to be retried — it is a decision.
* **Why `defer` is separate from `block`:** quiet hours end. The run keeps its
  `step_index` and wakes with `next_at` set, so the message goes out at 09:00
  without a human remembering to send it. A block never wakes.

## 3. Vocabulary (closed, on purpose)

**Triggers (9):** `lead.created`, `lead.unanswered`, `lead.stale` (14 d),
`visit.confirmed`, `visit.reminder_24h`, `visit.no_show`, `unit.hold_expiring`,
`call.missed` (inbound call, no answer, nobody has replied since), `deal.closed` (won ≥ 48 h ago).

**Steps (8):** `send_whatsapp`, `send_sms`, `voice_call`, `send_document`,
`create_task`, `assign_owner`, `notify_team`, `set_stage`.

No loops, no branches, no expressions, no user-authored code. A step declares what
it needs (`send_whatsapp` needs `text`), so an incomplete step is a `422` at save
time — `STEP_INCOMPLETE` — rather than a run that dies at 3 a.m. Templates render a
fixed slot list only (`{{contact.name}}`, `{{visit.when}}`, `{{unit.no}}`,
`{{tenant.name}}`); anything else is left alone, so a template cannot read a field
the contract never published.

**Playbooks (9)** — the catalogue the `open-agency-os` research said a GHL
alternative actually sells: instant response, unanswered escalation, doc pack on
visit confirmation, 24-hour reminder, no-show recovery, stale re-engagement,
expiring hold nudge, **missed-call text-back**, **review ask after closure**. Each
carries its `why`, and installing is idempotent — and **not** a resurrection: if a
human paused an automation, re-installing reports `already_installed` with
`enabled: false` unless the caller passes `{"enable": true}`. Found on the live demo,
where a catalogue click silently re-armed a duplicate the migration had paused.

Two of these needed a signal the CRM did not record, so `POST /contacts/{id}/calls`
now logs a call with a direction and an outcome (`answered`, `missed`, `no_answer`,
`voicemail`, `busy`) — the event a telephony webhook delivers. That route is a log,
not a dialler. The review ask has a rule that cannot be seen on a fresh demo: a deal
that closed minutes ago gets **no** ask; it fires 48 h later, once per deal. The
live proof asserts the first half and `tests/test_automations.py` asserts the second
on a fixed clock.

Terminal states: `ok`, `blocked`, `dead` (4 attempts, 30 s → 30 min backoff),
`skipped` (automation paused mid-run), `deferred` (recoverable). Every run keeps a
`trace` of `{step, type, at, verdict, reason}`.

## 4. What a tick actually does

```
POST /api/v1/automations/tick   {"dry_run": true|false, "limit": 100, "budget_sec": 10}
```

1. `plan()` — for each enabled automation, evaluate its trigger, mint runs whose
   idempotency key is new.
2. `due()` — pending/deferred runs whose `next_at <= now`.
3. `advance()` — run steps until the run settles, defers, blocks or dies. A tick
   drains a run rather than advancing it one step: a three-step playbook advancing
   one step per 15 s tick would miss the five-minute window it exists to protect.
4. Counts returned: `planned, due, ok, blocked, deferred, dead, pending, skipped` — plus
   `stopped_early` and `carried_over` when the tick ran out of **wall-clock budget**
   (10 s). Deferred work comes back all at once: quiet hours end and every reminder in
   the backlog is due in the same second. A tick that then writes a few hundred runs,
   one round trip at a time, stops being a request and becomes a timeout. The rest is
   picked up by the next tick, which is safe only because runs are durable and
   idempotent.

`POST /api/v1/automations/{id}/run` fires one automation for the subjects its
trigger currently matches — the demo and the proof script use it to avoid waiting
for a tick.

The worker (`RE_AUTOMATION_TICK`, default 15 s, `0` disables) ticks every tenant.
The test suite pins it off so counts stay deterministic and `tick` is driven
explicitly with a fixed clock.

## 5. Proof

`scripts/proof_automations.py` — 12 sections, self-booted or `--live`:

* catalogue + fleet are typed steps, not descriptions;
* install is idempotent, unknown playbook is a 404;
* a lead arrives → dry run sends nothing → a real tick creates a run;
* if the run sent, the **conversation has an automation message**, the contact's
  `first_response_at` is set (the speed-to-lead clock the dashboard reads), and a
  human still has a task; if it did not send, the run **says why**;
* ticking again creates no second run and sends no second message;
* a consent-less lead gets no message, ever;
* the kill switch is exercised and released;
* the queue reports counts, due-now, dead/deferred/blocked and the caps;
* three malformed automations are refused at save time with the right codes;
* a missed call is logged, named its recovery playbook, texted back at most once,
  and an unknown outcome is refused with a 422;
* a deal won minutes ago is not asked for a review (the 48-hour rule), while the
  playbook is armed on `deal.closed`;
* RBAC (viewer/agent cannot tick, viewer can read) and tenant isolation.

`tests/test_automations.py` — 30 tests, including a fake-store runtime suite on a
fixed clock: message effects, frequency cap deferral, quiet-hour hold, a poison
step dying after 4 attempts with its reason, a consent block that never retries,
two workers over one store producing one message, and the kill switch.

## 6. Honest limits

* **Transports are simulated.** `send_whatsapp` writes a conversation message with
  channel `whatsapp`; no BSP is called. The gate order and the record are real; the
  delivery receipt is not (`docs/09` Phase 1 S1).
* **One step per tick per subject, no parallel fan-out.** Fine at brokerage volume
  (hundreds of leads/day); a queue replaces it before 100k/month.
* **No user-authored delays between steps** (`wait 2 days`). Deferral exists
  (`next_at`), but the only delays are the gate's and the retry's; multi-day
  nurture sequences need a scheduler, which is a Phase 1 item.
* **`lead.unanswered` is intentionally narrow** — a 10-minute-to-1-hour window.
  Beyond that the stale/portal-SLA views are the right surface, not another
  WhatsApp.
* **The worker is in-process.** Two instances are safe because of idempotency
  keys and the `db/008` index, but neither is a scheduler: no leader election, no
  missed-tick catch-up beyond what the data re-derives. Every tick re-reads its
  inputs, which is correct and also a full scan of the runs collection per tenant
  — fine at hundreds of runs, not at hundreds of thousands.
* **A tick is O(due) round trips.** Each step is two or three writes, so a large
  backlog is slow even with the budget: the tick returns honestly with
  `carried_over`, but the work still has to happen. Batching the writes per run is
  the next optimisation, and a queue is the real answer (`docs/02 §5`).
* **A read model that snapshots at boot.** Writes reach Postgres immediately and
  reads are served from memory, so a second process's writes appear after a
  restart for every screen *except* the tick, which refreshes deliberately. This
  is a demo-scale trade (`docs/02 §5`), not a design anyone should copy.

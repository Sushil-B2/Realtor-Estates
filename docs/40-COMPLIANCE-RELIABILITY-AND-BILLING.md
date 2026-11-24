# 40 · Sprint S3 Compliance Launch Pack, S4 Crash-Safe Reliability & SaaS Billing, and S5 Partner Attestation (`v1.29.0`)

`v1.29.0` completes engineering for **Sprint S3** (DPDP Act 2023 / RERA / TRAI
Compliance Launch Pack), **Sprint S4** (Crash-Safe Step Idempotency, Worker
Leases, Dead-Letter Replay, SaaS Subscription GST Billing & Five Cost-Drivers
Dashboard), and **Sprint S5** (Signed Beta Partner Security & Approval-Queue SLA
Attestations).

---

## 1. Sprint S3 — Compliance Launch Pack (`app/compliance.py`, `docs/07`)

1. **DPDP §5 Versioned Privacy Notice (`v3.2-2026-11`), DPIA & Dynamic `[R13]` Sub-Processor Register (`GET /api/v1/compliance/pack`, `GET /api/v1/public/privacy-notice/{slug}`, `POST/DELETE /api/v1/compliance/subprocessors`):**
   - Dynamically lists every active workspace AI/voice/messaging/payment
     connector (`connector_profiles`) so third-party processor disclosure never
     drifts from runtime configuration.
   - Ships a structured 4-domain **DPIA** (`dpia_voice`, `dpia_scoring`,
     `dpia_messaging`, `dpia_uploads`) documenting inherent risk, technical
     controls, and residual risk.
2. **DPDP §8(9) Grievance Redressal Officer & 30-Day Statutory SLA Ledger (`PATCH /api/v1/compliance/officer`, `GET/POST/PATCH /api/v1/compliance/grievances`, `POST /api/v1/public/grievance/{slug}`):**
   - Stores grievance tickets in the `grievances` collection with automatic
     `30 * 86400` statutory SLA clocks, requester PII sealed at rest, and
     overdue alerting.
3. **DPDP §9 Children's Data Guard:**
   - `POST /api/v1/public/funnel/{slug}/submit` refuses minor (`age < 18` or
     `age_under_18=true`) submissions with `422 MINOR_PROCESSING_REFUSED`.
4. **DPDP §6(6) Immediate Consent Withdrawal Propagation:**
   - Withdrawing consent (`POST /api/v1/contacts/{cid}/consent` with
     `status: "withdrawn"`) immediately blocks pending/deferred automation
     `runs` (`CONSENT_WITHDRAWN_PROPAGATED`) and closes open automated tasks.
5. **DPDP §8(5) Right to Erasure + Signed Certificate + Blind-Index Suppression Tombstone (`POST /api/v1/contacts/{cid}/erase`, `GET /api/v1/compliance/erasures/{eid}/verify`):**
   - Purges/redacts personal data across `contacts` (AEAD-sealed `[ERASED]`
     sentinel preserving `SEN-C804`), `conversations`, `activities`, S3 call
     `recordings`, `runs`, `tasks`, and `site_visits`.
   - Retains only irreversible blind-index suppression hashes (`erasures`
     collection) enforced at `POST /api/v1/contacts`, `POST /api/v1/leads/import`,
     and `POST /api/v1/public/funnel/{slug}/submit` (`422 SUPPRESSED_BY_ERASURE`).
   - Mints a tamper-evident `HMAC-SHA256(HKDF:audit)` certificate (`sig1_...`).
6. **DSR Portability Bundle, Rule-4 Consent Artefact & Human Score Override (`GET /api/v1/contacts/{cid}/dsr-export`, `GET /api/v1/contacts/{cid}/consent-artefact`, `POST /api/v1/contacts/{cid}/score-override`):**
   - Machine-readable portability JSON, 7-year signed Consent Manager artefact,
     and audited human override over advisory AI lead scores.

---

## 2. Sprint S4 — Crash-Safe Reliability, DLQ, SaaS Billing & Five Cost Drivers (`app/automations.py`, `app/meters.py`)

1. **Step-Level Idempotency Keys & Worker Leases (`app/automations.py`):**
   - Every step derives `step_key = "<run_id>:step:<index>"` and checks
     `conversations`, `tasks`, and `activities` before committing side effects,
     paired with a per-worker `lease_until` claim lock.
   - **Chaos Drill Proved (`SIGKILL` `-9`):** Killing Worker 1 mid-campaign
     immediately after sending WhatsApp on Lead #3 (before `run.step_index`
     updates) followed by Worker 2 takeover results in **`[1, 1, 1, 1, 1]`**
     messages and **`[1, 1, 1, 1, 1]`** tasks (`dedup_hits = 1`, **zero
     duplicate sends**).
2. **Dead-Letter Queue (`GET /api/v1/automations/dead-letters`, `POST .../replay`, `POST .../dismiss`):**
   - Exposes runs that exhausted `MAX_ATTEMPTS = 4` with step-idempotent replay
     and audited dismissal in **`/#/automation`**.
3. **SaaS Subscription GST Billing & Five Cost Drivers (`GET /api/v1/billing/subscription`, `POST /api/v1/billing/plan`, `POST /api/v1/billing/invoices/generate`, `GET /api/v1/billing/cost-drivers`):**
   - Wires measured usage + `docs/09` §2 plan tiers (`starter`, `growth`,
     `scale`, `enterprise`) + metered overages + 18% GST (`SAC 998314`,
     intra-state CGST/SGST or inter-state IGST) into idempotent SaaS invoices.
   - Surfaces all **5 Weekly Cost Drivers (`docs/09` §4)** and live **Gross
     Margin %** in **`/#/analytics`**.

---

## 3. Sprint S5 — Partner Security & SLA Attestations (`POST /api/v1/compliance/attestations`)

- Runs the live tenant REDLINE suite + measures pending human-approval queue
  median age against the `< 4.0 h` SLA, minting a signed `sig1_` attestation.

---

## 4. Verification (`scripts/proof_s3_s4_compliance_and_reliability.py`)

- **`10/10 PASS`** end-to-end (including real subprocess `SIGKILL` chaos drill).

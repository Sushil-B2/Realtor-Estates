# 42 · Multi-Day Drip `wait` Steps, Refunds / §34 GST Credit Notes / GSTR-1, DPDP Registered Consent Manager & A2A Specialist Delegation (`v1.31.0`)

`v1.31.0` closes the remaining engineering items across `PLANS.md` §9 **G1** &
**G2**, `docs/09` §5 **Phase 2** (Registered Consent Manager bridge), and
`docs/09` §5 **Phase 3** (Multi-Agent A2A specialist delegation with per-hop
tokens) while preserving zero external-provider blockage.

---

## 1. G1 Closure — Crash-Safe Multi-Day `wait` Step & 7-Day Drip Playbook (`app/automations.py`)

- **`wait` step (`{"type": "wait", "duration": "2d", "align_to_window": true}`):**
  Parses declarative durations (`60 s` to `90 d`), aligns wake-ups outside
  tenant quiet hours when `align_to_window` is enabled, advances `step_index`,
  persists `next_at = resume_at` in Postgres/JSON, and yields cleanly without
  spinning a worker thread or needing an external broker.
- **Playbook `seven-day-drip-nurture`:** Day 0 layout WhatsApp → `2d` wait →
  Day 2 7/12 + NA FAQ → `5d` wait → Day 7 advisor call task.

---

## 2. G2 Complete Closure — Refunds, §34 GST Credit Notes, Form 16A Settlement & GSTR-1 (`app/money.py`)

- **Token Refund & Unit Release (`POST /api/v1/payments/{pid}/refund`):**
  Mints a sequential `RF-<SERIES>/<FY>/<seq>` refund voucher (`gst: 0`) and
  automatically releases the booked unit back to `available` on full token
  refund.
- **CGST §34 Credit Note (`POST /api/v1/invoices/{iid}/credit-note`):**
  Refuses non-taxable buyer custody `receipt`s (`422 RECEIPT_NOT_TAXABLE`) and
  excess credits (`422 CREDIT_EXCEEDS_INVOICE`), reversing exact intra-state
  (`CGST+SGST`) or inter-state (`IGST`) tax and 194-H TDS on a
  `CN-<SERIES>/<FY>/<seq>` credit note.
- **Commission Settlement & Form 16A Reconciliation (`POST /api/v1/invoices/{iid}/collect`):**
  Records bank/UTR cash receipts alongside Form 16A TDS certificates so net
  receivable + withheld 194-H tax reconciles cleanly to `status = "paid"`.
- **GSTN GSTR-1 Return Export (`GET /api/v1/money/gstr1?period=YYYY-MM&format=json|csv`):**
  Produces `b2b`, `b2cs`, `cdnr`, `cdnur`, and `hsn` (`SAC 997221`) tables while
  proving buyer custody token `receipt`s are excluded from taxable turnover
  (`custody_excluded.included_in_taxable_turnover = false`).

---

## 3. Phase 2 — DPDP §6(7)–(9) Registered Consent Manager Bridge (`app/compliance.py`)

- **`POST /api/v1/webhooks/consent-manager`** (HMAC-SHA256 verified via
  `X-Consent-Manager-Signature`), **`POST /api/v1/compliance/consent-manager/directives`**,
  and **`GET /api/v1/compliance/consent-manager/status`**.
- Processes MeitY / Account-Aggregator Consent Artefact directives (`grant`,
  `modify`, `withdraw`, `erasure_request`) by contact ID or blind-indexed
  phone/email, deduplicates `directive_id` replays, halts queued outbound runs
  on `withdraw`, and returns a `sig1_...` signed acknowledgement receipt.

---

## 4. Phase 3 — Multi-Agent A2A Specialist Delegation (`app/ai_governance.py`)

- **`GET /api/v1/agents/specialists`** & **`POST /api/v1/agents/{aid}/delegate`**:
  Delegates sub-tasks to `title_search`, `valuation_comps`, or
  `document_translation` (Marathi / Hindi / English) under an HMAC-signed
  `a2a1_...` per-hop capability token (`hop_depth <= 2`, `120 s` TTL, strict
  scope attenuation `requested_scopes ∩ parent_scopes − FORBIDDEN_DELEGATION_SCOPES`).
- Routes via live custom `a2a_*` connectors (`docs/39`) when configured, or
  executes immediately on the deterministic local specialist twin.

---

## 5. Verification (`scripts/proof_finance_consent_manager_and_a2a.py`)

- **`6/6 PASS`** end-to-end.

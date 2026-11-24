# 01 — Product Requirements Document

**Product:** Realtor Estates (working name) — Real Estate Revenue OS
**Version:** 1.1 (vertical scope merged 18 November 2026 — see §4.1a)
**Date:** 17–18 November 2026
**Owner:** Product / Founder
**Status:** Approved for build; commercial launch gated on counsel review (`docs/07-COMPLIANCE.md`)

---

## 1. One-paragraph thesis

Real-estate businesses in India run on 8–14 disconnected tools: a portal subscription, a spreadsheet,
a WhatsApp Business number on someone's phone, a telecaller's diary, a Google Ads account someone's
cousin manages, and a document folder on a laptop. Salesforce sells them enterprise CRM they will never
configure; GoHighLevel sells them a funnel builder with no compliance for TRAI, DPDP or RERA. **Realtor
Estates is the single tenant-aware operating system for the business**: capture → consent → qualify →
nurture → visit → negotiate → book → document, with AI agents that do the repetitive work behind hard
guardrails, and an owner who can see and stop everything.

## 2. Who it is for

| Persona | Context | Job to be done | Success looks like |
|---|---|---|---|
| **Developer-owner** (primary buyer) | 5–60 staff, ₹20–200 Cr annual sales, 2–10 projects | "I want every lead answered in 90 seconds and I want to know what my team and my AI are doing." | 20%+ lift in site visits; owner spends <15 min/day in ops; zero compliance scares. |
| **Sales head / manager** | Runs 6–25 agents, lives in the pipeline | "Give me a pipeline I can trust and stop my team double-selling plots." | One source of truth; stage discipline; forecast within ±15%. |
| **Sales agent** | On-site, Android phone, patchy 2G | "Tell me who to call next and give me the documents to send." | Next-best-action queue; doc pack in one tap; works offline. |
| **Marketing lead** | Runs ads + social + portal listings | "Stop wasting spend on leads that never answer." | CPL down, visit-rate up, attribution closed to booking. |
| **Owner's accountant / compliance** | Reviews invoices and risk | "Prove consent, prove disclosure, prove retention." | Exportable consent ledger + audit chain + retention report. |
| **NRI buyer** (end customer, not a user) | Overseas, WhatsApp-first | "Is the title clean, and can I buy this at all?" | Doc pack before travel; FEMA eligibility flagged. |

## 3. Jobs to be done (ranked)

1. **Answer every enquiry in under 90 seconds, in the buyer's language**, without a human waking up. *(Currently: 4–26 hours. This is the single biggest revenue leak in Indian real estate.)*
2. **Convert a lead into a site visit with the documents already in hand.** Land buyers are risking ₹15L–₹2Cr; "is the title clean?" outranks "when can I visit?".
3. **Never double-sell inventory.** Two agents commit the same plot on one weekend and the developer's reputation is gone.
4. **Run outbound without getting the customer's DLT registration cancelled.** One systemic TRAI violation can disconnect a header.
5. **Know today's numbers without asking anyone.** Leads, visits, bookings, spend, CPL, show-rate, holds expiring.
6. **Stop the AI from doing something expensive, illegal or embarrassing** — and prove you stopped it.
7. **Improve the AI without a data scientist.** The agents learn from their own transcripts, humans approve the learnings.
8. **Sell the next project from the last project's data** — segmentation, referral, re-engagement.

## 4. Scope

### 4.1 In scope (v1.0 = this build)

| Domain | Included |
|---|---|
| Tenancy | Self-serve signup, owner role, teammate invites, 5 roles, per-tenant feature flags, kill switch |
| CRM | Contacts, deals, pipeline stages, tasks, notes, timeline, explainable lead scoring, dedupe by blind index |
| Consent | Purpose-bound consent ledger, withdrawal, evidence string, notice versioning, consent-gap reporting |
| Communication | Unified inbox across WhatsApp/SMS/voice/web-chat (simulated transports), templates, quiet hours, AI drafting |
| AI agents | 5 seeded department agents, tool allowlist + scopes, spend caps, dry-run console, voice call simulation in mr/hi/en |
| Learning | Proposal queue, evidence, risk grading, human approval, version bump, decision history |
| Lead sources | Partner-API extraction (99acres/MagicBricks), Meta/Google lead webhooks, public-business-listing crawler, CSV import with formula neutralisation |
| Growth | Server-rendered funnels + brochure website, social scheduling (publish human-gated), paid campaign management with spend guardrails |
| Analytics | KPI overview, 12-month series, funnel, source economics, forecast with 95% interval, recommendations |
| Platform | Integrations catalogue + webhook contract, SSE live rail, audit ledger, security console, PWA install + offline shell |
| Security | Field encryption, scrypt, signed sessions, CSRF, CSP, RBAC, SSRF allowlist, guardrail inspector, REDLINE agent |

### 4.1a In scope since v1.6.0 — the real-estate vertical

Added after the market research in `docs/18` (which found that a CRM with only a pipeline is a
GoHighLevel clone with worse marketing). Each item has its own doc, its own proof script and its own
requirement IDs in §5.9.

| Domain | Included | Doc |
|---|---|---|
| Site visits | Slot, agent, pickup, lifecycle (scheduled → confirmed → visited / revisit / dropped / no-show), conflict detection | `docs/18` |
| Matching | Explainable requirement → property ranking with signed drivers | `docs/18`, `docs/22` |
| Stage gates | Deal cannot advance without its documents; commission ledger on gross commission | `docs/18` |
| Portal ingestion | Seven adapters, `X-Ingest-Key`, dedupe with source attribution, SLA clock | `docs/19` |
| Speed to lead | Per-source response windows, breach reporting | `docs/19` |
| Channel partners | Separate login, claim with protection window, approvals, payouts | `docs/20` |
| Unit inventory | Unit-level rows, timed holds, booking, sale tied to the deal | `docs/21` |
| Property taxonomy | Five categories with their own required fields, units and price bases | `docs/22` |

**Still out of scope for this slice** (tracked as gaps G1–G8 in `PLANS.md` §9): automation *execution*
(the model and CRUD exist, no runner), payment collection and GST invoicing, deployed Functions,
property photos in the desk, and the buyer portal.

### 4.2 Explicitly out of scope for v1.0

- Native mobile apps (PWA only; Play-Store TWA is a phase-3 option).
- Real payment collection (Razorpay adapter is present, journey not built).
- e-Sign / e-Registry integrations.
- Multi-language UI chrome (content is mr/hi/en; UI is English).
- Marketplace of third-party templates.
- Accounting/GST invoicing (Zoho Books is a connector, not a build).

### 4.3 Deliberately refused

- **Scraping portals against their terms of service.** It is the fastest way to lose the business and it harms the customers we claim to protect. We do lawful extraction (partner APIs, lead webhooks, public business listings) and say so in the UI.
- **Autonomous outbound calling without consent and disclosure.** Non-negotiable, both legally and reputationally.
- **"Fully autonomous agent" mode as a default.** It exists as an owner-only flag, off by default, because the failure mode is a mass-message incident, not a bug report.

## 5. Functional requirements

Requirement IDs are referenced by tests and by `changelog.md`.

### 5.1 Tenancy & identity

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-01 | Self-serve signup creates a tenant and an owner | New tenant isolated from day one; owner gets `kill_switch` permission |
| FR-02 | Five roles: owner, admin, manager, agent, viewer | Server-side role rank per route; `RL-203` proves a viewer cannot write |
| FR-03 | Owner controls: users, feature flags, kill switch, audit | Only owner can toggle `autonomous_execution` and `kill_switch`; both audit-logged |
| FR-04 | Session security | HttpOnly, SameSite=Strict, 8h TTL, `jti` revocation, 6-failure lockout |
| FR-05 | Role change forces re-authentication | Changing a role invalidates existing sessions |

### 5.2 CRM & pipeline

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-10 | Contact record with encrypted PII | Phone/email unreadable in the datastore (`RT-004`); masked for roles without `pii:read` |
| FR-11 | Dedupe without decryption | HMAC blind index catches formatting and zero-width noise (`RL-309`) |
| FR-12 | Explainable lead score 0–100 | Score decomposes into drivers with signed impact; next-best-action string |
| FR-13 | Pipeline with server-recomputed probability | Stage change cascades contact → deal; audit entry written |
| FR-14 | Timeline of every touch | Calls, messages, notes, stage moves, consent events in one feed |

### 5.3 Consent & compliance gates (hard requirements)

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-20 | No outbound contact without granted consent | `422 CONSENT_MISSING` (`RL-403`) |
| FR-21 | Quiet hours 09:00–21:00 IST, server-enforced | `451 COMPLIANCE_BLOCKED` outside window (`RL-404`) |
| FR-22 | AI disclosure cannot be disabled | `451` if a tenant tries (`FR-22` test in `tests/`) |
| FR-23 | Unlawful lead sources hard-blocked | `451` for ToS-violating sources, attempt audit-logged (`RL-406`) |
| FR-24 | RERA claim set | Agent cannot invent price, area, possession, approval status |
| FR-25 | Retention policy visible and enforced | Recordings 12 months, chat 18 months, audit 7 years |

### 5.4 AI agents

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-30 | Agent = role + scopes + tools + model + guardrails + spend cap | Manifest shows every tool with risk class and authorisation state |
| FR-31 | Tool calls revalidate scopes per call | `RT-012`/`RL-504` — no tool outside declared scopes |
| FR-32 | Fail-closed guardrails | Blocked or quarantined input ⇒ zero model output, zero tool calls (`RL-505`) |
| FR-33 | Irreversible & outbound actions park for human approval | `RL-503` |
| FR-34 | Voice agent discloses AI in the first turn | `RL-506` |
| FR-35 | Code-switching mr/hi/en | Simulated Marathi call returns Devanagari turns + extracted slots |
| FR-36 | Data-residency-aware model routing | High-sensitivity → on-prem; medium → India-resident; foreign providers require owner consent |
| FR-37 | Spend caps and circuit breakers | `RL-408`; kill switch halts execution (`RL-701`) |

### 5.5 Learning loop

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-40 | Agents propose, humans decide | Proposal has observation, evidence, risk, skill diff, checklist |
| FR-41 | High/critical risk needs explicit acknowledgement | `422 ACKNOWLEDGEMENT_REQUIRED` (tested) |
| FR-42 | Critical risk requires owner | Non-owner approve → `403` |
| FR-43 | Promotion is versioned and reversible | Skill version bump recorded with promoter and timestamp |
| FR-44 | Rejections are retained with reasons | Decision history table with reviewer + note |

### 5.6 Growth

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-50 | Funnels render without JS | `/f/<slug>` returns escaped HTML with consent checkbox + honeypot |
| FR-51 | Public submissions create consented leads | Duplicate phone merges; audit entry written |
| FR-52 | Social publishing is human-gated | `403` for agent role; publish requires manager+ |
| FR-53 | Campaign budget changes are guarded | >5× increase → `422 APPROVAL_REQUIRED` |
| FR-54 | JEV research cannot publish | Research-only toolset |

### 5.7 Analytics

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-60 | Forecast with interval and error metric | `low ≤ value ≤ high` for every horizon point (tested) |
| FR-61 | Source economics incl. CAC | CPL, conversion, CAC, scale/hold/fix verdict |
| FR-62 | Recommendations are advisory | Each has confidence + impact; nothing auto-executes |

### 5.9 Real-estate vertical (added v1.6.0 → v1.10.0)

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-80 | A site visit is a record with a lifecycle, not a stage label | Double-booking is refused `409` with the conflicting visit; every transition audited |
| FR-81 | A deal cannot claim a stage it cannot document | Token needs KYC + booking form; agreement needs the draft; registration needs stamp-duty receipt + appointment; `won` needs the registered deed |
| FR-82 | Commission is a ledger, not a field | Gross, agent share and channel-partner share recorded per deal; partner amount is a % of **gross commission** |
| FR-83 | Portal leads arrive with their source intact and a clock | `X-Ingest-Key` required; dedupe on phone blind index; source history preserved to booking; SLA breach visible |
| FR-84 | A channel partner has their own door | Partner login resolves to `channel_partner` (rank 0) and is gated by `_require_partner()`; claims carry a 1–365 day protection window |
| FR-85 | A unit can only be promised once | Second hold/booking is `409` naming `held_by_deal` + `hours_left`; holds self-release by derivation; a sale without a booking is refused |
| FR-86 | A property carries the fields its category actually has | Five categories; a field from another category is `422`; `bigha` is refused with the reason; area normalises to sqft; seven price bases reduce to one comparable number |
| FR-87 | A saved requirement produces a ranked, explained shortlist | Score 0–100 with a reason per component; a missing must-have caps the score; wrong category cannot outrank right category |

### 5.8 Security operations

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-70 | Posture dashboard from live state | 24 weighted checks, pass/fail with evidence |
| FR-71 | Audit chain verification endpoint | `chain.ok` true, head hash returned |
| FR-72 | Guardrail lab | Inspector returns score, action, findings, OWASP mapping, redaction |
| FR-73 | External red-team harness | 53 checks, 8 categories, machine-readable report |
| FR-74 | SSRF-safe webhooks | Private ranges and non-allowlisted hosts rejected at creation |

## 6. Non-functional requirements

| Area | Target | Measured by |
|---|---|---|
| API latency (p95) | < 120 ms for reads on seeded data | server logs; synthetic load in phase 2 |
| Voice turn latency (p95) | ≤ 900 ms; target p50 ≤ 650 ms | per-tenant edge telemetry |
| First meaningful paint | < 1.8 s on 4G mid-range Android; shell < 250 KB transferred | Lighthouse / real-device test |
| Offline | Shell loads; public pages cached; mutations fail with a clear message | manual device test |
| Availability | 99.9% monthly (phase 2 target) | uptime monitor |
| RPO / RTO | 5 min / 1 h (phase 2 with Postgres PITR) | restore drill |
| Accessibility | WCAG 2.2 AA for console; 4.5:1 contrast; keyboard-complete | axe + manual pass |
| Data residency | India primary; no PII offshore without consent | architecture review + routing tests |
| Dependency surface | Zero third-party runtime deps in the app tier | `RT-019` |

## 7. Success metrics

**North star:** confirmed site visits per 100 captured leads.

| Metric | Baseline (typical) | v1.0 target | Guardrail metric |
|---|---|---|---|
| First response time (lead → first touch) | 4–26 h | < 90 s median | Opt-out rate < 2% |
| Lead → site visit | 8–14% | ≥ 20% | Complaint rate < 0.05% |
| Show-rate (visit attended / booked) | 45–60% | ≥ 70% | No-show reason capture |
| AI-handled conversation share | 0% | 50–70% | Escalation-to-human success ≥ 95% |
| Skills promoted after human review | n/a | ≥ 2 per agent per quarter | Zero unauthorised promotions |
| Security findings (external suite) | unknown | 0 critical/high open at release | Mean time to remediate < 72 h |
| Time-to-first-value (new tenant) | weeks | < 45 min | Support tickets per tenant |

## 8. Release criteria (definition of done for v1.0)

1. `pytest tests/` green.
2. `redline/redline.py` returns 0 failures against a clean seeded instance.
3. `RT-*` in-process suite returns score ≥ 95.
4. No `critical`/`high` dependency or configuration finding open.
5. Every hard requirement (FR-20…FR-25, FR-32, FR-33, FR-41) has an automated test.
6. Demo tenant reproducible from a clean `data/` directory.
7. Counsel sign-off on consent notice text and disclosure script.
8. Every vertical proof script passes against the running instance **and** the deployed instance
   (`scripts/proof_{crm_vertical,portal_ingest,channel_partners,inventory,taxonomy}.py --live`).
9. `python3 scripts/doc_facts.py --check` is green — no document quotes a stale count.

## 9. Risks and mitigations (product level)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Customer's DLT/PE registration gets penalised by an AI campaign | Medium | Severe (their business) | Compliance gates in the API; posture screen; pre-flight dry run; frequency caps |
| AI hallucinates a price or RERA claim | Medium | Severe (legal) | Claim-set constraint; templates for regulated content; human approval for outbound marketing |
| Mass-message incident from a runaway agent | Low | Severe | Spend caps, rate limits, outbound approval, kill switch, REDLINE checks |
| Data breach of buyer PII | Low | Severe | Field encryption, tenant-bound AAD, blind index, minimal surface, egress allowlist |
| Buyer distrust of AI calls | Medium | Medium | Mandatory disclosure, instant human handoff, language matching |
| Tool sprawl returns (customers keep their old CRM too) | Medium | Medium | Zero-integration completeness; CSV/webhook in and out; sync-first mindset |
| Key-person dependency on one model vendor | Medium | Medium | Provider abstraction + residency-aware router + local fallback |

## 10. Open questions (tracked in PLANS.md)

1. Pricing: per-seat + usage, or per-project success fee? *(Decision needed before GTM.)*
2. Do we resell WhatsApp BSP capacity or require the customer's own WABA?
3. Who owns the learned-skill library — tenant-private or shared anonymised pool? *(Recommendation: tenant-private by default, opt-in pooling with differential privacy.)*
4. Regional expansion order: Maharashtra → Gujarat/Rajasthan plotted belts, or metro apartments first?
5. Broker/CP channel: read-only portal for channel partners in v1.1?

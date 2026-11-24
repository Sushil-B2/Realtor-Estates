# 09 — Roadmap & Economics

## 1. The one-line business model

Sell the *staff* the broker cannot hire — a voice qualifier, a WhatsApp follow-up agent, an ops
assistant, and a research analyst — at a price below one junior's salary, on software margins.

A tier-2 Indian brokerage pays ₹18k–25k/month for one tele-caller who works 8 hours, forgets to follow
up, and cannot handle Marathi + Hindi + English. Growth plan is ₹24,999/month and does the work of
roughly two of them, 24×7, with an audit trail. That is the whole pitch. The CRM is the moat, not the hook.

---

## 2. Packaging

| Plan | Price (INR/mo) | Seats | Included | Overage |
|---|---|---|---|---|
| **Starter** | 9,999 | 3 | CRM, pipeline, 1 funnel, 1 website page, 500 AI messages, 100 voice minutes, WhatsApp (tenant's own WABA) | ₹0.9/msg, ₹6/min |
| **Growth** | 24,999 | 10 | + 4 agents, lead scoring, forecasting, ads autopilot, social scheduler, 3,000 messages, 600 voice min, approval workflows | ₹0.7/msg, ₹5/min |
| **Scale** | 59,999 | 30 | + unlimited funnels, MCP gateway, custom agents, private model route, 12,000 messages, 2,500 voice min, SSO, priority support, quarterly red-team report | ₹0.5/msg, ₹4/min |
| **Enterprise** | from 1,50,000 | unlimited | Dedicated region/tenant, BYO KMS, on-prem voice, custom SLAs, annual REDLINE attestation | negotiated |

Add-ons: extra voice agents (₹4,999/agent), JEV content engine (₹7,999), REDLINE continuous (₹9,999),
white-label PWA (₹14,999), WhatsApp template management (₹2,999).

**Why usage-based on messages/voice but flat on everything else:** messaging and telephony have real
marginal cost; CRM seats do not. Meters only where the bill is honest.

## 3. Unit economics (Growth plan, representative tenant)

| Line | Monthly |
|---|---|
| Revenue | ₹24,999 |
| Voice: 600 min × ₹3.2 blended | −₹1,920 |
| WhatsApp: 3,000 messages × ₹0.85 (India marketing rate) | −₹2,550 |
| LLM/agent runs: ~6,000 runs × ₹0.35 | −₹2,100 |
| Infra share (compute, storage, egress, monitoring) | −₹1,150 |
| Payment gateway (2%) | −₹500 |
| **Gross margin** | **₹16,779 ≈ 67%** |

Sensitivity: voice at 3× list price (the "real cost" trap in `05` §7) drops GM to ~55%. A 5× blended-cost
scenario in telephony still leaves the plan profitable — which is why the provider layer is an adapter,
not a commitment.

CAC assumption (India, inside sales + partner channel): ₹28,000–45,000. Payback 2–3 months at Growth.
Logo churn modelled 2.2%/mo for Starter, 1.1%/mo for Growth+ once the CRM holds their pipeline.

## 4. Cost drivers we watch weekly

1. **Voice minutes per qualified lead** — the single most dangerous number. Target ≤ 4.2 min/lead.
2. **Messages per closed deal** — rising means the nurture is drifting, not working.
3. **Agent run cost per conversation** — a long context window is a silent budget leak.
4. **Human approval queue age** — a proxy for whether the guardrail is usable or just obstructive.
5. **REDLINE findings per release** — a leading indicator of engineering discipline.

## 5. Roadmap

### Phase 0 — Demo build (**done**, this repo)
Working PWA, tenant isolation, encrypted PII, guardrail pipeline, learning gate, 5 agents, **311 API
routes**, **666 collected tests** + 53 REDLINE checks + 48 Sentinel checks, Postgres/Neon with RLS, 3 deployed Neon Functions, S3 object-storage export & recording archiving, cross-instance SSE fan-out, live Neon Auth session bridge, Sprint S1 carrier/Razorpay protocol adapters, Universal Provider & Webhook Fabric (`docs/39`), Sprint S3/S4/S5 Compliance Launch Pack, Crash-Safe Step Idempotency & SaaS Billing (`docs/40`), Dual-LLM Quarantined Intake, Canary Auto-Rollback & Observability (`docs/41`), Multi-Day Drip `wait` Steps, Refunds/§34 Credit Notes/GSTR-1, DPDP Registered Consent Manager & A2A Specialist Delegation (`docs/42`), Buyer/NRI Self-Serve Portal + FEMA Engine, Inbound AI Voice Receptionist, Offline PWA Sync, Signed Pack Exchange & Deployment Kit (`docs/43`), Ads Autopilot with Hard Spend Caps & OOXML Macro/Archive-Bomb Upload Security (`docs/44`), Phase 4 On-Device Pre-Screening, AP2 Agentic Commerce, GCC/SEA Regional Pinning & Hybrid PQ v2 Envelope (`docs/45`), Complete Government, Revenue (`7/12`), Statutory Cost-Sheet, IGR `e-Registration`, Loan/CLP, `E-Ferfar` Mutation & Marketing Trust Pack (`docs/46`), Zero-Trust Security Hardening `F-01`..`F-10` (`docs/47`), Governed Native MCP Server, OpenAPI 3.1, OAuth 2.1 PKCE & Multi-Language SDKs (`docs/48`), White-Label Agency SaaS Mode, Visual Node-DAG Builder, 2-Way Calendar & Deliverability Suite (`docs/49`), Stateless W3C OTLP Observability, Enterprise OIDC/SSO & Automated `sa11y` WCAG 2.2 AA Gate (`docs/50`), and an append-only audit
chain, and the real-estate vertical the horizontal CRMs do not model (site-visit lifecycle, document
gates, commission ledger, portal ingestion with an SLA clock, channel-partner portal, unit inventory
with timed holds, five-category property taxonomy). Deterministic seeded data. Honest limitations
documented in `README.md`.

**What Phase 0→3 still leaves to external credentials / ops:** no external queue broker (automations fire from an
in-process 15 s tick with persisted multi-day `wait` steps, per-worker claim leases, and step-level idempotency keys),
and third-party WhatsApp/SMS/voice/Razorpay adapters default to zero-spend `simulated` / Local Twin mode until live
commercial provider keys are configured (`docs/38`, `docs/39`).

### Phase 1 — Production shell (Q1 2027, 10–12 weeks)
- Postgres + RLS replacing the JSON store; Redis for rate limits and SSE fan-out.
- Real WhatsApp Cloud API + SMS (DLT-registered templates) + one voice provider behind the adapter.
- KMS envelope encryption; per-tenant DEK; key rotation runbook.
- Auth hardening: TOTP MFA, refresh tokens, device list, session revoke-all.
- Object storage for documents; signed URLs; virus scanning on upload (ClamAV-compatible).
- Observability: structured logs, traces, per-tenant usage meters, alerting on §4 metrics.
- **Gate:** external pen test; DPDP notice + privacy policy; sub-processor DPAs signed.

### Phase 2 — The compliance moat (Q2 2027, 12 weeks)
- Consent Manager integration (DPDP Phase 2 deadline was Nov 2026 — be early).
- Dual-LLM document intake (privileged planner + quarantined reader).
- Canary + shadow-run learning promotion with automatic rollback.
- Inventory holds with TTL and race-safe acquisition.
- Upload security: type sniffing, macro rejection, sandboxed conversion.
- **Gate:** SOC 2 Type I readiness; DPIA published to tenants; REDLINE ≥ 100 with zero critical findings across two consecutive releases.

### Phase 3 — Multi-agent operations (Q3–Q4 2027)
- A2A delegation to specialist agents (title search, valuation, document translation) with per-hop tokens.
- Voice: inbound receptionist, real call recording + diarised transcripts, sentiment over time.
- Ads autopilot with hard spend caps and human approval on budget increases.
- Marketplace for tenant-authored skills and funnels (reviewed, signed, versioned).
- **Gate:** 50 paying tenants on Growth+; < 1.5% net revenue churn.

### Phase 4 — Platform (2028+)
- On-device pre-screening (WebGPU/WebNN): PII redaction and lead scoring without a round trip.
- Agentic commerce: hold and booking tokens settled agent-to-API (AP2/ACP/UCP).
- Regional expansion: GCC and SEA, region-pinned tenants.
- Post-quantum crypto migration for long-lived records (hybrid KEM, `05` §4).

## 6. Build vs buy (decided)

| Capability | Decision | Why |
|---|---|---|
| CRM/pipeline | **Build** | It is the system of record and the switching cost |
| WhatsApp/voice transport | **Buy** | Commodity, regulated, needs provider relationships |
| LLM | **Buy, multi-provider** | Route by task; never single-vendor |
| Voice stack | **Buy, adapter-isolated** | Prices falling 5×/year; owning is a trap |
| Guardrails | **Build** | This *is* the product |
| Learning gate | **Build** | No vendor sells accountable learning |
| Ads APIs | **Buy** | Platform APIs only |
| Analytics | **Build thin, buy heavy later** | Dashboards first, warehouse when volume demands |
| REDLINE | **Build** | Proof is the differentiator |

## 7. Go-to-market (India first)

1. **Beachhead:** Nanded / Marathwada + one tier-2 cluster. Narrow geography, marathi-first voice, plot inventory.
2. **Channel:** channel-partner brokers and RERA consultants who already sell to developers; 20% recurring revenue share for 12 months.
3. **Proof asset:** the REDLINE report and the approval-queue screenshot. Compliance is a sales weapon against every "AI does whatever you say" competitor.
4. **Land:** Growth plan with a free funnel build + 100 free voice minutes in the first month.
5. **Expand:** seats, then agents, then JEV content.

## 8. Pricing risks

| Risk | Response |
|---|---|
| Voice COGS spike | Adapter swap; price per-minute overage; cap free minutes |
| Provider changes terms (WhatsApp pricing, model TOS) | Multi-provider routing; the same message through SMS fallback |
| Customers want "fully autonomous" | Sell faster approvals, not absent ones — refusal is a positioning choice |
| A competitor undercuts on flat pricing | Compete on margin-of-error (audit, compliance, red-team evidence), not on price |
| Regulatory cost rising | Compliance is already a product surface; raise Enterprise value, not Starter price |

## 9. Metrics that decide the next phase

| Metric | Phase gate |
|---|---|
| Activation: funnel published + first agent run within 7 days | > 60% of signups |
| Time-to-first-qualified-lead | < 30 min |
| Agent acceptance rate (drafts approved unchanged) | > 70% |
| Approval queue median age | < 4 h |
| Qualified lead → site visit | > 25% |
| Login retention (weekly) | > 45% of seats |
| Gross margin per tenant | > 60% |
| Critical REDLINE findings | 0 at every release |

## 10. Honest economics caveat

The demo has no paying customers, no COGS history, and no churn data. The numbers above are a model with
stated assumptions, not a forecast. The two assumptions that would break it: (a) voice cost per
qualified lead above 6 minutes, (b) approval fatigue pushing agents into bypass mode. Both are
instrumented from day one precisely because they are the failure modes.

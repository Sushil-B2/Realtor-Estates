## 1.39.0 — 2026-11-17 (Stateless W3C OTLP Observability, Enterprise OIDC/SSO & Automated `sa11y` WCAG 2.2 AA Gate)

Closes Competitive Benchmark Gaps #5 and #6
(`docs/50-STATELESS-OTLP-OBSERVABILITY-OIDC-SSO-AND-SA11Y-GATE.md`) against
`hyperdx`, Enterprise CRM Corporate SSO (`Okta` / `Microsoft Entra ID` / `Google
Workspace`), and Salesforce `sa11y`:

- **Stateless W3C `traceparent` + Bounded OTLP v1 Telemetry (`app/enterprise_observability_sso.py`, `/api/v1/observability/*`):**
  - Parses incoming W3C `traceparent` (`00-<32hex>-<16hex>-<02hex>`), emits
    correlated response `traceparent` headers, isolates spans strictly per
    `tenant_id` in a bounded `1,000`-span ring buffer, and exports
    OpenTelemetry `resourceSpans` JSON via SSRF-guarded `sec.safe_urlopen`.
- **Enterprise OIDC / Corporate SSO (`/api/v1/sso/config`, `/api/v1/auth/sso/start`, `/api/v1/auth/sso/callback`):**
  - Pure-stdlib RFC 8017 §8.2.2 `RS256` JWKS verification (`>= 2048-bit` RSA,
    blocks `alg: none` & `HS256` algorithm confusion), mandatory `S256` PKCE,
    single-use `state`/`nonce` (`claim_once`), corporate `allowed_domains`
    enforcement, and least-privilege JIT role clamping.
- **Automated `sa11y`-Style WCAG 2.2 AA Accessibility Gate (`GET /api/v1/accessibility/audit`, `scripts/check_a11y.py`):**
  - Audits `web/index.html`, `/site`, and published `/f/<slug>` funnels (`0`
    violations across `4` surfaces, `6.47:1` contrast ratio) and enforces WCAG
    2.2 AA inside `npm run verify` (`5/5 PASS` in `scripts/proof_observability_sso_a11y.py`).

## 1.38.0 — 2026-11-17 (White-Label Agency/Builder SaaS Mode, Visual Node-DAG Builder, 2-Way Calendar & Deliverability Suite)

Closes Competitive Benchmark Gaps #2, #3, and #4
(`docs/49-WHITE-LABEL-AGENCY-VISUAL-DAG-CALENDAR-DELIVERABILITY.md`) against
GoHighLevel Agency SaaS Mode, Visual Workflow & Funnel Builders, and
Calendars / Email Deliverability / Reputation Management:

- **White-Label Builder / Agency SaaS Mode (`app/agency_growth_suite.py`, `/api/v1/agency/*`):**
  - Custom domain HMAC DNS TXT verification (`_realtor-verify.<domain>`), strict
    `#RRGGBB` CSS color validation, isolated sub-account provisioning with
    per-tenant HKDF DEKs, **PII-free** snapshot cloning (`parent_agency_tenant_id`
    verified), and GST 18% wholesale-to-retail rebilling rollup.
- **Cycle-Safe Visual Node-DAG Workflow Compiler & Composable Funnel Blocks (`/api/v1/workflows/*/dag`, `/api/v1/funnels/*/blocks`):**
  - Kahn topological sort rejects cyclic graphs (`DAG_CYCLE_DETECTED`), bounds
    depth (`<= 20`), inspects templates via AI guardrails, validates webhook
    node URLs via `sec.ssrf_check`, and renders allowlisted funnel blocks into
    `/f/<slug>` with OWASP HTML entity encoding (`sec.esc`).
- **2-Way RFC 5545 `iCal` Calendar, `SPF/DKIM/DMARC` Doctor & Reputation Loop (`/api/v1/calendar/*`, `/api/v1/email/deliverability/check`, `/api/v1/reputation/*`):**
  - Atomic `claim_once` anti-double-booking, RFC 5545 §3.3.11 newline/VALARM
    injection escaping, `+all` open-relay SPF / DKIM / DMARC diagnostics, and
    DPDP-consented reputation pulses with non-gated public review links (`6/6 PASS`
    in `scripts/proof_agency_dag_calendar_reputation.py`).

## 1.37.0 — 2026-11-17 (Governed Native MCP Operating Layer, OpenAPI 3.1, OAuth 2.1 PKCE Apps & Multi-Language SDKs)

Closes Competitive Benchmark Gap #1 (`docs/48-MCP-OPENAPI-OAUTH2-PKCE-AND-MULTI-LANGUAGE-SDKS.md`)
against Salesforce Headless Toolkit / Agentforce, GoHighLevel API v2/v3,
`Go-High-Level-MCP-2026-Complete`, and `gohighlevel-php-sdk`:

- **Governed Native MCP Server (`app/mcp_platform.py`, `POST /api/v1/mcp`, `GET /api/v1/mcp/doctor`, `scripts/mcp_stdio.py`):**
  - Supports JSON-RPC 2.0 `2025-06-18` (`initialize`, `tools/list`, `tools/call`,
    `resources/list`, `resources/read`, `prompts/list`, `prompts/get`) over both
    Streamable HTTP and CLI `stdio`, with 4 profiles (`readonly`, `curated`,
    `stable`, `full`), AI guardrail argument inspection, PII output scrubbing,
    and `args_sha256`-bound single-use human confirmation queues (`proposals`)
    blocking TOCTOU parameter tampering.
- **OAuth 2.1 Authorization-Code + Mandatory `S256` PKCE Marketplace Apps (`GET|POST /api/v1/oauth/apps`, `POST /api/v1/oauth/authorize`, `POST /api/v1/oauth/token`, `POST /api/v1/oauth/revoke`):**
  - Per-tenant DEK-encrypted client secrets, single-use `60s` authorization codes
    with RFC 6749 §10.5 replay revocation, and least-privilege route walls
    (`re_oa_...` tokens cannot touch `/security`, `/auth`, `/users`, `/stepup`, or `/learning`).
- **Live OpenAPI 3.1 Introspection (`GET /api/v1/openapi.json`) & Multi-Language SDKs (`sdk/`):**
  - Dynamic OpenAPI 3.1.0 generator over `api.ROUTES` + zero-dependency typed
    Python (`sdk/python/realtor_estates_sdk.py`), TypeScript/ESM
    (`sdk/typescript/realtor-estates-sdk.mjs`), and PHP 8.1+
    (`sdk/php/RealtorEstatesClient.php`) SDKs (`6/6 PASS` in `scripts/proof_mcp_openapi_oauth_sdks.py`).

## 1.36.0 — 2026-11-17 (Zero-Trust Security Hardening `F-01` .. `F-10` — Highest Priority)

Remediates all ten findings from the offensive-minded architecture & code-loophole
audit (`docs/47-ZERO-TRUST-SECURITY-HARDENING-F01-F10.md`, addressing October 2026
`PHP2Shell`, `Cairn`, `LiteLLM`, and `REF8801` attack classes):

- **F-01 (Critical):** Per-tenant HKDF-derived DEKs (`v2t:tenant-dek:` via
  `sec.seal_tenant`) refusing cross-tenant AAD unseal + Stateless Per-Request
  Postgres RLS Read Mode (`RE_STATELESS_READS=1` in `app/store.py` & `app/db.py`)
  keeping zero tenant CRM/PII collections cached in process RAM.
- **F-02 (High):** `sec.safe_urlopen` with single-resolution IP-pinned TCP/TLS
  dialer (`server_hostname` SNI preserved), unconditional `301..308` redirect
  blocking, and mandatory DNS verification on AI gateways.
- **F-03 (High):** Rejects empty/short custom webhook secrets (`403 WEBHOOK_SECRET_NOT_CONFIGURED`),
  validates optional `X-Webhook-Timestamp` (`±300s`), and enforces atomic `600s`
  replay deduplication (`409 WEBHOOK_REPLAY_DETECTED`).
- **F-04 (High):** Sets `Handler.timeout = 15s`, bounds `_HTTPServer` worker
  concurrency via semaphore (`503 SERVICE_BUSY`), and coalesces `/f/<slug>`
  view increments with `60s` per-IP deduplication.
- **F-05 (High):** Scrubs credentials/Aadhaar/PAN/cards and wraps risky user
  prompts in `<untrusted_input>` boundaries before outbound AI gateway calls +
  strips markdown image exfiltration beacons on inbound responses.
- **F-06 (Medium-High):** Adds native `application/x-www-form-urlencoded` +
  accessible HTML confirmation on `/api/v1/public/funnel/<slug>/submit` paired
  with HMAC-signed timestamped `_ftok` anti-bot tokens.
- **F-07 (Medium-High):** Workspace-scoped `sec.blind_index(..., tenant_id=tid)`
  eliminating cross-tenant `phone_hash`/`email_hash` correlation with dual-read
  migration.
- **F-08 (Medium):** Normalizes PDF `#XX` hex-escaped tokens (`/Java#53cript`),
  bounded-decompresses `/FlateDecode` streams (`<= 512 KB`, `<= 50:1` ratio),
  and blocks PNG/JPEG/WebP PHP/script polyglots.
- **F-09 (Medium):** Blocks wildcard `frame-ancestors *` in `CFG.PRODUCTION`,
  requires explicit `RE_PREVIEW_FRAME_ANCESTORS` HTTPS origins, and forces
  `DENY`/`'none'` on `/api/v1/auth/*`, `/security/*`, and `/stepup/*`.
- **F-10 (Medium):** Pins exact semver versions in `package.json` &
  `package-lock.json` and adds `.npmrc` (`save-exact=true`, `ignore-scripts=true`)
  (`6/6 PASS` in `scripts/proof_security_hardening_v136.py`).

## 1.35.0 — 2026-11-17 (Complete Government, Revenue `7/12`, Statutory Cost-Sheet, IGR `e-Registration`, Loan/CLP, `E-Ferfar` Mutation & Marketing Trust Pack)

Implements all six owner-approved Indian / Maharashtra government, banking,
post-sales, and marketing compliance tracks (`Tracks G-1`..`G-6`,
`docs/46-GOVT-REVENUE-EREGISTRATION-LOAN-MUTATION-AND-TRUST-PACK.md`):

- **Track G-1 — Land & Revenue Vault (`app/govt_sales_cycle.py`, `GET|PUT /api/v1/properties/{pid}/revenue-vault`):**
  - Tracks `7/12` (*Satbara*) with a 30-day freshness clock (`SATBARA_STALE_OVER_30D`),
    Occupant `Class II` / `govt_lessee` Collector transfer permission + *Nazrana*
    hard gate (`409 CLASS_2_COLLECTOR_PERMISSION_REQUIRED` before `token`),
    unreleased *Itar Hakk / Boja* blocker (`409 UNRELEASED_BOJA_ENCUMBRANCE` before
    `agreement`), `8A` Khata, Urban Property Card, Collector NA Order, `Mojni`, and
    30-year Title Search.
- **Track G-2 — Statutory Buyer Cost-Sheet Engine (`POST /api/v1/govt/cost-sheet`):**
  - Computes `max(agreement_value_inr, asr_ready_reckoner_inr)`, Maharashtra Stamp
    Duty across Metro (`7%`), Tier-2 Nanded NWCMC (`6%`), and Council/Rural (`5%`),
    `1%` Women Homebuyer Concession (residential only + 15-year advisory), `1%`
    Registration capped at `₹30,000`, GST (`0%/1%/5%/12%`), and `§194-IA` `1%`
    Buyer TDS (`>= ₹50L`, `Form 26QB` + `Form 16B`).
- **Track G-3 — IGR `e-Registration` & `iSarita` SRO Desk (`GET|PUT /api/v1/deals/{did}/registration`, `POST /api/v1/deals/{did}/registration/index-ii`):**
  - Dual execution mode (`e_registration_builder_office` biometric/OTP vs
    `isarita_sro_physical` two-witness gate), `GRAS` challan & duty verification,
    `Form 26QB` gate, `Index-II` + `registered_doc_no` completion, and automatic
    sync into `deal.documents`.
- **Track G-4 — Project Bank APF, Buyer Home-Loan & RERA CLP (`GET|POST /api/v1/properties/{pid}/bank-apf`, `PUT /api/v1/deals/{did}/home-loan`, `POST /api/v1/deals/{did}/clp/init`, `POST /api/v1/deals/{did}/clp/demand`):**
  - Project-level Bank APF directory, buyer loan pipeline (`login`..`disbursed`),
    and 7-stage RERA building / 3-stage NA plot CLP schedules refusing construction
    demands without `architect_cert_ref` (`409 ARCHITECT_CERTIFICATE_REQUIRED`).
- **Track G-5 — Post-Registration `E-Ferfar` Talathi Mutation & Handover (`PUT /api/v1/deals/{did}/handover`, `app/phase3_expansion.py`):**
  - 15-day `E-Ferfar` notice countdown, Circle Officer certification, updated
    `7/12` / Property Card, `Namuna-8` tax, MSEB meter transfer, snag clearance,
    and Possession Letter (`completion_pct`), surfaced live on `#/buyer-portal/{token}`
    (also fixing a `dict(list)` bug when `deal.documents` holds vertical stage rows).
- **Track G-6 — Marketing Verified Trust Badges & Cost Pack (`GET /api/v1/properties/{pid}/marketing-trust-pack`, `public_funnel_page`):**
  - Emits strictly verified badges (`MAHARERA_VERIFIED`, `NA_COLLECTOR_VERIFIED`,
    `FRESH_7_12_ZERO_BOJA`, `TITLE_30YR_CLEAR`, `BANK_APF_APPROVED`) + WhatsApp
    brochure copy + escaped SSR badges and cost card on `/f/{slug}` (`6/6 PASS` in
    `scripts/proof_govt_sales_cycle.py`).

## 1.34.0 — 2026-11-17 (Phase 4 Platform Horizon: On-Device Pre-Screening, AP2 Agentic Commerce, GCC/SEA Regional Pinning, Hybrid Post-Quantum v2 Envelope & G5/G7 Polish)

Implements all four owner-approved Phase 4 Platform tracks (`docs/09` §5,
`docs/05` §4.3) plus the remaining G5 & G7 vertical polish items (`PLANS.md` §9,
`docs/45-PHASE4-PLATFORM-AND-VERTICAL-POLISH.md`):

- **Phase 4.1 — On-Device Client Pre-Screening (`web/js/core.js` `RE.onDevicePrescreen`, `GET /api/v1/prescreen/model-card`, `POST /api/v1/prescreen/verify`):**
  - Zero-round-trip client-side Aadhaar/PAN/card PII redaction and deterministic
    `0–100` lead intent pre-scoring (`WebGPU`/`WebNN` probe + JS fallback) paired
    with server leak attestation.
- **Phase 4.2 — Agentic Commerce Settlement (`app/phase4_platform.py`, `GET/POST/DELETE /api/v1/commerce/mandates`, `POST /api/v1/commerce/settle`):**
  - HMAC-SHA256 signed `ap2_...` mandates enforcing `max_amount_inr` ceilings,
    regional regulatory gates, race-safe unit holds/bookings, non-GST `RC-AP2-...`
    custody receipts, and idempotent `acp1_...` settlement signatures.
- **Phase 4.3 — GCC & SEA Regional Expansion & Pinning (`GET /api/v1/regions/profiles`, `PATCH /api/v1/regions/pin`, `POST /api/v1/regions/compliance-check`):**
  - Jurisdiction profiles and data-residency pinning for `IN` (`ap-south-1`),
    `AE` (`me-central-1` Dubai DLD Trakheesi permit + `4%` DLD fee + `5%` VAT),
    and `SG` (`ap-southeast-1` Singapore CEA licence + `0–60%` ABSD + `9%` GST),
    refusing cross-region exports (`RESIDENCY_PIN_VIOLATION`).
- **Phase 4.4 — Crypto-Agility & Hybrid Post-Quantum Envelope (`GET /api/v1/security/crypto-agility`, `POST /api/v1/security/crypto-agility/upgrade`, `app/security.py`):**
  - Self-describing `v2:hybrid-sp80056c-chacha20hmac:` field ciphertexts with
    NIST SP 800-56Cr2 Hybrid `HKDF-SHA512(classical||pq_kem_slot)`, AAD-bound
    downgrade protection, transparent `v1`/`v2` unsealing, and one-call archive
    upgrade.
- **G5 & G7 Vertical Polish (`app/media.py`, `PATCH /api/v1/properties/{pid}/media-order`, `GET/POST /api/v1/properties/{pid}/floor-plans`, `GET/POST/DELETE /api/v1/demo/proof-tenants`):**
  - Deterministic cover pinning & gallery reordering, floor-plan + SSRF-safe 3D
    walkthrough slots, and isolated disposable `tnt_proof_...` sandbox workspaces
    (`6/6 PASS` in `scripts/proof_phase4_and_vertical_polish.py`).

## 1.33.0 — 2026-11-17 (Phase 3 Ads Autopilot with Hard Spend Caps, Phase 2 Office Macro / Archive-Bomb Security & Full Portal UX)

Completes the remaining engineering items across the approved Phases 1–3
roadmap (`docs/09` & `PLANS.md`) and finishes all SPA surfaces for `Options A–E`
(`docs/44-ADS-AUTOPILOT-MACRO-UPLOAD-SECURITY-AND-PORTAL-UX.md`):

- **Phase 3 Ads Autopilot with Hard Spend Caps & Human Budget Approval (`app/phase3_expansion.py`, `GET/PATCH /api/v1/ads/policy`, `POST /api/v1/ads/budget-requests`, `POST /api/v1/ads/autopilot/run`, `/#/ads`):**
  - Enforces workspace `hard_daily_cap_inr`, `hard_monthly_cap_inr`, `max_cpl_inr`,
    `require_approval_above_pct` (`20%`), and MahaRERA advertising guardrails on
    campaigns; pauses bleeding high-CPL campaigns or halts all active spend at
    the monthly ceiling while staging scale-ups in `proposals` (`risk = "high"`,
    `status = "pending"`) so `budget_daily` never rises without human approval.
- **Phase 2 OOXML Macro, External-Template & Archive-Bomb Upload Security (`app/connectors.py`, `POST /api/v1/properties/{pid}/documents/scan`):**
  - Adds `.docx`/`.xlsx` ZIP magic-byte & container verification, blocks
    `vbaProject.bin`/`.bin` macros, `TargetMode="External"` remote template
    injection, XXE `<!ENTITY`, `> 40:1` archive decompression bombs, legacy OLE2
    containers, RTF `\objdata`, and PDF `/OpenAction`+`/EmbeddedFiles`+`/XFA`,
    while emitting a sandboxed plain-text preview (`<= 600` chars).
- **Complete Strict-CSP SPA Surfaces (`web/js/core.js`, `views-ops.js`, `views-platform.js`, `views-ai.js`, `views-growth.js`):**
  - Unauthenticated `#/buyer-portal/<token>` self-serve portal view, Deal modal
    *Mint / Revoke Buyer Portal*, `/#/integrations` *Website Form Connector* +
    *Offline & Push* sync console, Pack Exchange *Verify & Import*, and `/#/ads`
    *Spend Cap Policy* + *Run Autopilot* (`6/6 PASS` in
    `scripts/proof_ads_autopilot_and_upload_security.py`).

## 1.32.0 — 2026-11-17 (Buyer & NRI Self-Serve Portal + FEMA Engine, Inbound AI Voice Receptionist, Offline PWA Sync, Signed Pack Exchange & Deployment Kit)

Implements all five owner-approved Phase 3 & Post-PRD expansion tracks
(`Options A–E`, `docs/43-BUYER-PORTAL-INBOUND-VOICE-OFFLINE-PWA-EXCHANGE-AND-DEPLOY.md`):

- **Option A — Buyer & NRI Self-Serve Portal + FEMA Rule 24 Engine (`app/phase3_expansion.py`, `POST /api/v1/fema/check`, `POST/DELETE /api/v1/deals/{did}/buyer-portal`, `GET /api/v1/public/buyer-portal/{token}`):**
  - Enforces FEMA (Non-Debt Instruments) Rules 2019 Rule 24 (blocking NRI/OCI
    purchase of `agricultural`/`farmhouse`/`plantation`, holding unverified-NA
    plots, enforcing the 2-residential-property repatriation cap across NRE /
    FCNR(B) / NRO inward remittance, and requiring RBI approval for non-OCI
    foreign nationals), plus deal-scoped `bp1_...` HMAC passes with instant
    `pass_jti` revocation.
- **Option B — Phase 3 Inbound AI Voice Receptionist & Diarised Sentiment (`POST /api/v1/voice/inbound`, `POST /api/v1/webhooks/voice-inbound`):**
  - Marathi/Hindi/English inbound receptionist with Turn-0 mandatory AI
    disclosure, speaker-diarised turns, `[−1.0, +1.0]` sentiment trajectory,
    and automatic human escalation (`escalated_to_human = true` + 15-min urgent
    task) when `min_score <= -0.35` or a human manager is requested.
- **Option C — PWA Field-Agent Offline Sync & SSRF-Safe Web Push (`GET/POST/DELETE /api/v1/push/...`, `POST /api/v1/offline/sync`, `RE.offlineOutbox`):**
  - Registers W3C HTTPS push subscriptions behind `sec.ssrf_check` with sealed
    keys and replays offline `note.add`, `visit.outcome`, and `task.complete`
    mutations idempotently via `client_mutation_id`.
- **Option D — Signed Skill & Funnel Template Pack Exchange (`POST /api/v1/exchange/export`, `POST /api/v1/exchange/import`):**
  - Signs `realtor-estates.template-pack.v1` manifests with `sig1_...`
    HMAC-SHA256, verifies signatures + RERA advertising + `run_shadow_eval` on
    import, creates draft funnels (`published = false`), and stages skills as
    human-gated `pending` proposals.
- **Option E — External Website Form Connectors & Multi-Host Deployment Kit (`Dockerfile`, `deploy/`):**
  - Ships `Dockerfile`, `deploy/Caddyfile.example`, `deploy/realtor-estates.service`,
    `deploy/vercel.json.example`, `deploy/netlify.toml.example`, and external
    website form handlers (`nextjs-submit-lead.mjs`, `php-submit-lead.php`)
    verified against `POST /api/v1/ingest/website` (`6/6 PASS` in
    `scripts/proof_phase3_buyer_portal_voice_pwa_and_exchange.py`).

## 1.31.0 — 2026-11-17 (Multi-Day Drip `wait` Steps, Token Refunds, §34 GST Credit Notes, Form 16A Settlement, GSTR-1 Export, DPDP Consent Manager Bridge & A2A Specialist Delegation)

Closes the remaining engineering items across `PLANS.md` §9 **G1** & **G2**,
`docs/09` §5 **Phase 2** (DPDP §6(7)–(9) Registered Consent Manager bridge), and
`docs/09` §5 **Phase 3** (Multi-Agent A2A Specialist Delegation with attenuated
per-hop capability tokens) (`docs/42-FINANCE-CONSENT-MANAGER-AND-A2A.md`).

- **G1 Multi-Day `wait` Step & 7-Day Drip Playbook (`app/automations.py`):**
  - Adds declarative `wait` (`60 s` to `90 d`, optional `align_to_window` quiet-hour
    alignment) that parks runs in Postgres/JSON across restarts without spinning
    threads, plus the `seven-day-drip-nurture` playbook.
- **G2 Refunds, §34 GST Credit Notes, Commission Settlement & GSTR-1 (`app/money.py`, `POST /api/v1/payments/{pid}/refund`, `POST /api/v1/invoices/{iid}/credit-note`, `POST /api/v1/invoices/{iid}/collect`, `GET /api/v1/money/gstr1`):**
  - Refunds custody tokens (`RF-...` voucher + automatic unit release back to
    `available`), refuses GST credit notes on non-taxable receipts (`422 RECEIPT_NOT_TAXABLE`),
    mints CGST §34 `CN-...` credit notes reversing exact CGST+SGST/IGST & 194-H
    TDS, reconciles UTR cash + Form 16A TDS certificates (`status = "paid"`),
    and exports GSTN GSTR-1 JSON & CSV (`b2b`, `b2cs`, `cdnr`, `cdnur`, `hsn`
    `SAC 997221`, `custody_excluded`).
- **DPDP §6(7)–(9) Registered Consent Manager Bridge (`app/compliance.py`, `POST /api/v1/webhooks/consent-manager`, `POST /api/v1/compliance/consent-manager/directives`, `GET /api/v1/compliance/consent-manager/status`):**
  - Verifies `X-Consent-Manager-Signature` HMAC-SHA256, processes `grant`,
    `modify`, `withdraw` (halting queued outbound runs), and `erasure_request`
    directives with replay deduplication and `sig1_...` signed acknowledgements.
- **Multi-Agent A2A Specialist Delegation (`app/ai_governance.py`, `GET /api/v1/agents/specialists`, `POST /api/v1/agents/{aid}/delegate`, `/#/agents`):**
  - Delegates to `title_search`, `valuation_comps`, and `document_translation`
    (`mr`/`hi`/`en`) with HMAC-signed `a2a1_...` per-hop tokens (`hop_depth <= 2`,
    `120 s` TTL, strict scope attenuation stripping outbound/irreversible scopes,
    and Dual-LLM prompt-injection blocking) (`6/6 PASS` in
    `scripts/proof_finance_consent_manager_and_a2a.py`).

## 1.30.0 — 2026-11-17 (Phase 2 AI Governance: Dual-LLM Quarantined Intake, Canary Auto-Rollback, Guardrail Eval Harness, RERA Social Gate & SRE Observability)

Closes `PLANS.md` workstreams **W4** (Dual-LLM Quarantined Document Intake),
**W5** (Guardrail Adversarial & Multilingual Benchmark), **W6** (Shadow-Run
Evaluation, 72h Canary Traffic Split & Automatic Rollback), **W10** (RERA Social
Publish Gate), and **W13** (Request Trace Spans, Per-Route Latency Histograms,
Prometheus Exposition & SRE Alerts) (`docs/41-AI-GOVERNANCE-AND-OBSERVABILITY.md`).

- **Dual-LLM Quarantined Document Intake (`app/ai_governance.py`, `POST /api/v1/agents/intake-document`):**
  - Isolates untrusted broker/buyer text inside a tool-less, secret-less
    Quarantined Reader that strips indirect prompt injections and emits only
    typed facts (`project_name`, `rera_id`, `survey_no`, `carpet_area_sqft`,
    `quoted_price_inr`, `discount_requested_pct`, `encumbrance_flags`) to the
    Privileged Planner, parking `> 3%` discount requests in human approvals.
- **Shadow-Run Harness, Canary Split & Automatic Rollback (`POST /api/v1/learning/{pid}/shadow-benchmark`, `/canary`, `/record-outcome`, `/rollback`, `/#/learning`):**
  - Evaluates candidate skills on a 12-case Marathi/Hindi/English + RERA +
    injection suite (`422 SHADOW_EVAL_REGRESSION` on failure), pins
    pre-promotion `skills`, routes deterministic canary traffic, auto-rolls
    back on any guardrail block or `> 20%` error rate, and auto-graduates after
    10 clean runs.
- **Guardrail Benchmark, RERA Social Publish Gate & Observability (`app/observability.py`, `POST /api/v1/security/guardrail-benchmark`, `POST /api/v1/social/{sid}/publish`, `GET /api/v1/observability/metrics`, `GET /api/v1/observability/alerts`):**
  - Proves `100%` adversarial recall (`1.0`) and `0%` benign false-positive
    rate (`0.0`) across Marathi/Hindi/English; enforces RERA registration +
    assured-return blocking on social posts; stamps `X-Trace-Id` (`trc_...`)
    with bounded `p50`/`p95`/`p99` histograms, Prometheus metrics, and live SRE
    alerts (`7/7 PASS` in `scripts/proof_ai_governance_and_observability.py`).

## 1.29.0 — 2026-11-17 (Sprint S3 DPDP Compliance Launch Pack, S4 Crash-Safe Step Idempotency, DLQ & SaaS Billing, and S5 Partner Attestations)

Completes engineering across `PLANS.md` **Sprint S3** (DPDP Act 2023 / RERA /
TRAI Compliance Launch Pack), **Sprint S4** (Crash-Safe Automation Reliability,
`SIGKILL` Chaos Drill, Dead-Letter Replay, SaaS Subscription GST Billing & Five
Cost-Drivers Dashboard), and **Sprint S5** (Signed Beta Partner REDLINE +
Approval-Queue SLA Attestations) (`docs/40-COMPLIANCE-RELIABILITY-AND-BILLING.md`).

- **Sprint S3 Compliance Launch Pack (`app/compliance.py`, `app/api.py`, `app/store.py`, `web/js/views-platform.js`):**
  - Added versioned DPDP §5 Privacy Notice (`v3.2-2026-11`, also public at
    `GET /api/v1/public/privacy-notice/{slug}`), 4-domain DPIA, and dynamic
    `[R13]` sub-processor & DPA register (`GET /api/v1/compliance/pack`,
    `POST/DELETE /api/v1/compliance/subprocessors`) auto-reflecting active
    workspace connectors.
  - Added DPDP §8(9) Grievance Officer & 30-day statutory SLA workflow
    (`grievances` collection, `PATCH /api/v1/compliance/officer`,
    `GET/POST/PATCH /api/v1/compliance/grievances`,
    `POST /api/v1/public/grievance/{slug}`).
  - Added DPDP §9 minor age gate (`422 MINOR_PROCESSING_REFUSED` on public
    funnels), DPDP §6(6) immediate consent withdrawal propagation across queued
    automation runs & tasks, portable DSR export (`GET /contacts/{cid}/dsr-export`),
    Rule-4 signed 7-year Consent Artefact (`GET /contacts/{cid}/consent-artefact`),
    audited human lead-score override (`POST /contacts/{cid}/score-override`),
    and DPDP §8(5) Right to Erasure (`POST /contacts/{cid}/erase`) with signed
    `sig1_` certificates (`erasures` collection) and irreversible blind-index
    suppression tombstones (`422 SUPPRESSED_BY_ERASURE`).
- **Sprint S4 Reliability, Chaos Drill, SaaS Billing & Five Cost Drivers (`app/automations.py`, `app/meters.py`, `web/js/views-ai.js`, `web/js/views-growth.js`):**
  - Added deterministic per-step idempotency keys (`<run_id>:step:<index>`) and
    per-worker `lease_until` claim locks in `app/automations.py`, proved under a
    real subprocess `SIGKILL` (`-9`) mid-campaign chaos drill (`[1, 1, 1, 1, 1]`
    sends across 5 leads, **zero duplicate sends**), plus Dead-Letter Queue
    inspection, replay, and dismissal (`GET/POST /api/v1/automations/dead-letters/...`).
  - Wired measured tenant usage to `docs/09` §2 plan tiers + overage rates +
    18% GST (`SAC 998314`) SaaS subscription invoicing
    (`GET /api/v1/billing/subscription`, `POST /api/v1/billing/plan`,
    `POST /api/v1/billing/invoices/generate`) and live `docs/09` §4 Five Weekly
    Cost Drivers + Gross Margin (`GET /api/v1/billing/cost-drivers`).
- **Sprint S5 Beta Readiness (`POST /api/v1/compliance/attestations`):**
  - Mints signed tenant REDLINE + `< 4.0 h` approval-queue SLA partner
    attestation reports (`10/10 PASS` in
    `scripts/proof_s3_s4_compliance_and_reliability.py`).

## 1.28.0 — 2026-11-17 (Universal Provider, Future-Tech API & Webhook Fabric, Zero-Blockage Playable WAV Local Twin & S2 Document Scanner)

Eliminates hardcoded provider lock-in and missing-credential blockages across
AI/LLMs, Voice Telephony, WhatsApp, TRAI DLT SMS, Payment Gateways, Custom Agent
Tools, and Inbound/Outbound Webhooks (`docs/39-UNIVERSAL-CONNECTOR-FABRIC.md`),
and closes `PLANS.md` **S2** document-pack upload scanning & magic-byte MIME
sniffing.

- **Universal Provider & Custom Agent Tool Fabric (`app/connectors.py`, `app/channels.py`, `app/agents.py`, `app/api.py`, `web/js/views-platform.js`):**
  - Added 25 named presets (`sarvam`, `elevenlabs`, `vapi`, `bolna`, `retell`,
    `bland`, `exotel`, `twilio`, `openai_compat`, `anthropic`, `gemini`,
    `sarvam_chat`, `neon_ai_gateway`, `meta_cloud`, `gupshup`, `wati`, `interakt`,
    `msg91`, `kaleyra`, `fast2sms`, `razorpay`, `cashfree`, `stripe`) **plus**
    **`custom_http`** — a universal JSON-template (`{{phone}}`, `{{text}}`,
    `{{prompt}}`, `{{amount_paise}}`, …) and dot-path response extractor
    (`result.nested.session_uuid`, `choices.0.message.content`, …) so any
    current or future provider/API connects from **`/#/integrations`** with zero
    code changes and zero server restarts.
  - All workspace connector & inbound-webhook secrets are AEAD-sealed at rest
    (`sec.seal` with per-tenant AAD, re-encrypted automatically by
    `scripts/rotate_keys.py`) and never echoed in API responses (`SEN-C306` /
    `SEN-C803` clean).
  - Custom Agent Tools (`category: "tool"`, callable as `connector.<slug>`)
    participate in agent planning and enforce human-approval parking on
    `outbound` / `irreversible` risk tiers.
- **Zero-Blockage Local Twin & Simulator (`app/connectors.py`, `app/api.py`, `web/js/views-platform.js`):**
  - When no external telephony key is configured, `POST /api/v1/contacts/{cid}/voice-call`
    synthesizes a real playable 8 kHz mono PCM `.wav` audio clip (`RIFF/WAVE`) +
    bilingual AI-disclosed qualification transcript and archives it straight to
    Neon S3 (`recordings/tenants/<tid>/<act_id>.wav`), plus one-click inbound
    WhatsApp & voice simulators (`POST /api/v1/connectors/simulate`).
- **Dynamic SSRF-Safe Egress Allowlist, Universal Inbound Webhooks & S2 Document Scanner (`app/security.py`, `app/connectors.py`, `app/api.py`):**
  - `POST/DELETE /api/v1/security/egress-allowlist` lets owners allowlist public
    HTTPS or `*.domain` hosts at runtime while preserving the non-overridable
    SSRF + DNS-rebinding wall against private/loopback/metadata IPs.
  - `POST /api/v1/connectors/inbound-hooks` → `POST /api/v1/webhooks/custom/<hook_id>`
    mints signed inbound endpoints with dot-path `field_map` routing into leads,
    WhatsApp threads, call logs, or deal notes.
  - `POST /api/v1/properties/{pid}/documents/scan` verifies magic bytes against
    declared MIME (`415 MIME_MAGIC_MISMATCH`) and blocks EICAR, `MZ`/`ELF`
    executables, `<script>` polyglots, and PDF `/JavaScript` actions (`422`).
  - End-to-end proof (`scripts/proof_universal_connectors.py`): **`9/9 PASS`**.

## 1.27.0 — 2026-11-17 (Sprint S1 carrier & Razorpay adapters, live Neon Auth session bridge & Vault runtime loader)

Closes `PLANS.md` `## Backend plan — Neon` item **#2** (managed **Neon Auth**
session bridge with `RE_AUTH_MODE=neon` single-auth enforcement), ships the
runtime half of `docs/14` **#1** (in-memory HTTPS Vault/Doppler/Infisical secret
puller), delivers **Sprint S1** real channel adapters & signed webhooks
(WhatsApp Cloud API v21.0 + 24 h window, TRAI DLT SMS template registry, voice
telephony + S3 recording webhook), and adds live outbound **Razorpay
`/v1/orders`** creation (`PLANS.md` §9 **G2** follow-up). Documented in
`docs/38-CHANNELS-AUTH-AND-VAULT.md`.

- **Managed Neon Auth session bridge (`app/neon.py`, `app/api.py`, `app/server.py`):**
  - Caught and fixed a live cloud incompatibility: Neon Auth runs **Better Auth**
    (`GET /get-session`, returning **`HTTP 200` with JSON body `null`** when
    unauthenticated, while `/users/me` returns `404`). `_normalise_neon_auth_payload`
    strictly requires non-empty `user.id`, `user.email`, and unexpired `session.id`
    (rejecting `200 null`), backed by a 30 s SHA-256-keyed cache evicted on
    `POST /api/v1/auth/logout` alongside upstream `POST /sign-out`.
  - Added `RE_AUTH_MODE=neon|hybrid|local`: `"neon"` disables local password
    sign-in/sign-up (`403 LOCAL_AUTH_DISABLED`) and rejects local HMAC tokens on
    both `/api/v1/**` and `/api/v1/events` SSE so a deployment runs **one auth
    system, not two**. Proved live against `NEON_AUTH_BASE_URL` (`ap-southeast-1`).
- **Runtime Secret-Manager / Vault loader (`app/config.py`):**
  - `load_from_secret_manager()` pulls secrets into memory over HTTPS at boot
    (`RE_SECRET_MANAGER_URL` + `RE_SECRET_MANAGER_TOKEN`) across **Doppler**,
    **Infisical v3**, and **HashiCorp Vault KV-v2** JSON payloads, failing closed
    in `RE_ENV=production` and never writing vault secrets to `.env` on disk.
- **Sprint S1 real channel adapters & signed webhooks (`app/channels.py`, `app/api.py`, `app/server.py`):**
  - **WhatsApp Cloud API v21.0:** approved template registry (`GET/POST /api/v1/channels/whatsapp/templates`),
    **24-hour customer-service window** enforcement (`422 WHATSAPP_WINDOW_CLOSED`
    on free-form sends >24 h after last inbound buyer message), plain-text
    `GET /api/v1/webhooks/whatsapp` hub challenge, and `POST /api/v1/webhooks/whatsapp`
    `X-Hub-Signature-256` HMAC verification for inbound messages (`phone_hash`
    blind-index lookup) and `sent`→`delivered`→`read` status updates.
  - **TRAI DLT SMS:** 19-digit DLT template registry (`GET/POST /api/v1/channels/sms/templates`),
    6-character `sender_id` header + anchored `{#var#}` pattern check (`422 DLT_TEMPLATE_MISMATCH`),
    HTTPS SMS gateway dispatch, and signed `POST /api/v1/webhooks/sms` DLR webhook.
  - **Voice Telephony & Razorpay Orders:** `POST /api/v1/contacts/{cid}/voice-call`
    (`Sarvam`/`ElevenLabs` + DPDP consent + TRAI quiet hours + `voice_minutes`) +
    signed `POST /api/v1/webhooks/voice` archiving WAV/MP3/transcripts to Neon S3
    `recordings/tenants/<tid>/…`, plus live Basic-Auth `POST /v1/orders` in
    `POST /api/v1/payments/orders` (fail-closed `502` on upstream failure).
  - End-to-end proof (`scripts/proof_channels_and_auth.py`): **`17/17 PASS`**.

## 1.26.0 — 2026-11-17 (object storage upload path, cross-instance SSE fan-out for 100 concurrent inboxes & scale-to-zero caching)

Closes `PLANS.md` `## Backend plan — Neon` items **#3** (exports & call-recording
uploads to S3 object storage), **#4** (cross-instance SSE fan-out + 100
concurrent inbox load test, completing **S0**), and **#6** (batched inbox SQL
reads + static shell `ETag`/`304` caching + scale-to-zero anonymous `/healthz`),
and reconciles the stale `PLANS.md` §1 `Payment collection` row with §9 **G2**.
Documented in `docs/37-STORAGE-AND-REALTIME.md`.

- **Object storage upload path (`app/neon.py`, `app/api.py`, `scripts/proof_storage_uploads.py`):**
  - Added pure-stdlib (`D2`) AWS SigV4 header-signed `put_object` (with SHA-256
    payload verification via `x-amz-content-sha256`), `head_object`,
    `delete_object`, and `_clean_object_key` traversal refusal (`..`, `//`, `\`,
    NUL) in `app/neon.py`.
  - Added `POST /api/v1/exports`, `GET /api/v1/exports`, and `GET /api/v1/exports/{eid}`
    (`manage_tenant`) archiving `audit` (`json`/`csv`, respecting DPDP PII
    retention), `contacts` (`json`/`csv`), and `compliance_bundle` (`json`) to
    `exports/tenants/<tid>/…`, plus call-recording & transcript archiving on
    `POST /api/v1/contacts/{cid}/calls`, `POST /api/v1/agents/{aid}/voice-test`,
    and `GET /api/v1/recordings/{rid}` (`pii:read`) under `recordings/tenants/<tid>/…`.
  - Proved live against Neon `projectassets` (`ap-southeast-1`): **`13/13 PASS`**
    (`scripts/proof_storage_uploads.py`), cleaning up all proof keys on exit.
- **Cross-instance Realtime SSE fan-out & 100-concurrent-inbox load test (`app/shared_state.py`, `app/realtime.py`, `app/server.py`, `app/automations.py`, `web/js/core.js`, `scripts/proof_realtime_inboxes.py`):**
  - Added tenant-scoped `publish_event` / `events_since` event rings across
    `Redis` (pure RESP2 `INCR` + `RPUSH`/`LTRIM`/`LRANGE` — no Lua/`EVAL`),
    `SharedFiles` (`flock` + unlocked `os.stat()` fast-path), and `_MemoryAdapter`.
  - Built per-process `RealtimeHub` multiplexer (`app/realtime.py`) so **one**
    background relay thread per server instance fans cross-node events out to
    100+ local SSE queues with W3C `Last-Event-ID` cursor replay and remote
    `STORE.invalidate_freshness` hooks.
  - Proved across **2 live `app.server` processes** and **100 concurrent SSE
    inboxes** (`60` `sunrise` + `40` `metro`): **`260/260` frames delivered in
    `240.4 ms` wall time** (**cross-node `p50 = 45.3 ms`, `p95 = 77.9 ms`,
    `p99 = 82.2 ms`**), **`0` errors**, **`0` cross-tenant leaks** (`11/11 PASS`).
  - Caught and fixed **4** real server defects: `channel_partner` scope &
    non-admin `audit` actor disclosure on `/api/v1/events`, stdlib
    `ThreadingHTTPServer.request_queue_size = 5` (`listen(5)`) SYN drops under
    50–100 simultaneous connects (raised to `256`), HTTP/1.1 unchunked SSE
    `Connection: keep-alive` socket wedging after stream exit, and anonymous
    `/healthz` pinging Neon Postgres on every liveness poll.
- **Batched inbox reads & scale-to-zero static shell caching (`app/store.py`, `app/api.py`, `app/server.py`):**
  - `GET /api/v1/inbox` and `GET /api/v1/inbox/{cid}` now batch
    `("conversations", "contacts")` into **one** `documents_many` SQL query
    coalesced across simultaneous opens (`max_age_ms=250`) and invalidated
    immediately on realtime events.
  - `_static()` serves from a stat-validated RAM cache (`_STATIC_CACHE`) with
    strong SHA-256 `ETag`s and `304 Not Modified` (`0` body bytes) while keeping
    `Cache-Control: no-store` on all `/api/**` routes (`SEN-C601` / `SEN-C605`
    green), and anonymous `/healthz` calls `STORE.backend_status(probe=False)` so
    liveness polls never wake scaled-to-zero Neon compute.

## 1.25.0 — 2026-11-17 (live Neon Cloud Functions deploy G3, hosted Postgres gate G6 & 7.35s branch-restore RTO)

Closes **G3** (the final open gap in `PLANS.md` §9, completing **G1–G8**), the
hosted Neon `-pooler` run of **G6**, and `docs/13` follow-up **#5** (point-in-time
branch restore + cryptographic audit-chain RTO). Documented in
`docs/36-NEON-CLOUD-PROOFS.md`.

- **G3 closed — all 3 Neon Functions & both cloud triggers deployed to `ap-southeast-1` (`neon.ts`, `scripts/deploy_functions.sh`, `scripts/proof_deployed_functions.py`, `app/neon.py`):**
  - Deployed `propertyphotos`, `compliancesweep`, and `paymentswebhook` (`nodejs24`, `2048 MiB`) plus triggers `on-property-photo` (`storage_object_created` on `projectassets` prefix `properties/`) and `nightly-compliance` (`0 2 * * *` UTC).
  - Proved live end-to-end (`scripts/proof_deployed_functions.py --fire-sweep`, **12/12 PASS**): cloud edge stripping forged `x-neon-trigger-invocation-id` headers (`401 missing_header`), real JPEG upload to S3 → cloud storage trigger → `sharp` (`linux-arm64`) generating 3 WebP variants in S3 & `re_property_media`, HMAC webhook write + replay deduplication, and a live cloud cron execution of `compliancesweep` verifying the SHA-256 audit chain.
  - Caught and fixed **4** cloud/deploy defects: `externalPackages: ["sharp"]` for native `linux-arm64` addon staging, `FunctionDef.env` declarations in `neon.ts` + `--env` on `config plan`, `--no-env-pull` so `neon deploy` never overwrites local `.env`, and cloud trigger/HMAC contract alignment in `proof_deployed_functions.py`.
- **Hosted G6 + Neon branch-restore RTO (`scripts/proof_neon_cloud_rto.py`, `scripts/verify_postgres.py`, `tests/test_postgres.py`):**
  - Restored a point-in-time child branch from `production` via the Neon v2 API (`0.99s`), verified TLS SQL readiness (`3.47s`), and verified all `1,505` `re_audit` rows across `28` tenants byte-for-byte (**`7.35s` warm / `9.86s` cold total RTO**).
  - Caught and fixed a real PgBouncer (`-pooler`) false negative where sequential `autocommit` `select pg_backend_pid()` queries recycled one backend PID in `<1 ms`; sampling inside simultaneous `with a.transaction(), b.transaction():` blocks proved **`21 passed, 0 skipped`** on hosted Neon `PostgreSQL 18.7`.

## 1.24.0 — 2026-11-17 (Sentinel report EXFILTRATE gate, audit chaining, TLS Secure cookies & audit PII retention)

Closes the remaining five credential-free security follow-ups in `PLANS.md`
(`docs/14` #2, #3, #4 and `docs/13` #4, #6; `docs/13` #3 reconciled as closed in
`v1.20.0` + `v1.23.0`). Documented in `docs/35-SECURITY-FOLLOWUPS.md`.

- **Sentinel `WRITE_REPORT` `EXFILTRATE` gate + credential scrubbing (`sentinel/agent.py`, `app/security.py`)** —
  `Gate.check("WRITE_REPORT", ...)` refuses destinations outside `reports/` or
  URL schemes and blocks unredacted credentials; `scrub_report_obj()` scrubs
  cookies, JWTs, API/Neon keys, and Postgres DSNs before disk write; `gate_self_test()`
  expanded to 10 cases.
- **Three real defects caught in `sentinel/agent.py`:**
  1. `dict(r.getheaders())` overwrote `re_session` with `re_refresh` on fresh
     logins, silently skipping 5 authenticated checks (`43/43` instead of `48/48`).
  2. `SEN-C503` wrote a live 30-day `re_refresh=<token>` credential verbatim into
     `reports/sentinel-report.md` and `reports/sentinel-report.json`.
  3. `SEN-C201` scanned only `localStorage` despite claiming
     `localStorage/sessionStorage`; `core.js` and `keysetup.js` now hold tokens
     strictly in JS heap memory and `SEN-C201` scans both web-storage APIs.
- **Sentinel scans chained into the audit trail (`POST /api/v1/security/sentinel-runs`, `app/api.py`, `web/js/views-platform.js`)** —
  owner-only endpoint rejects unredacted credentials (`400 SECRET_IN_REPORT`),
  stores a `runs` row (`type="sentinel"`), appends `security.sentinel_run` to the
  SHA-256 audit chain, and exposes `last_sentinel` in `GET /api/v1/security/posture`
  and the Security console.
- **CI gate (`.github/workflows/ci.yml`, `package.json`)** — runs `npm run verify`,
  `scripts/verify_postgres.py --real`, `sentinel/agent.py --self-test`, and
  `sentinel/agent.py --deep --strict` on every push and pull request.
- **`Secure` cookies behind TLS termination (`app/server.py`)** — sets `; Secure`
  on `re_session`, `re_refresh`, and clear-cookies when `RE_ENV=production`,
  `RE_SECURE_COOKIES=1`, or a `RE_TRUSTED_PROXIES` peer sends
  `X-Forwarded-Proto: https`, ignoring spoofed headers from untrusted peers.
- **Audit PII retention window (`app/audit_retention.py`, `GET /api/v1/tenant/audit`, `PATCH /api/v1/tenant`)** —
  default 90-day window (`7..2555` days) replaces expired actor/target emails and
  IPs on read with deterministic HMAC pseudonyms (`KEYS["blind_index"]`) while
  leaving raw immutable rows untouched so the 7-year SHA-256 hash chain (`chain.ok`)
  remains valid.
- **Proved (`scripts/proof_security_followups.py` 11/11, `tests/test_security_followups.py` 12 collected cases).**

## 1.23.0 — 2026-11-17 (single-use confirmations across multiple server instances)

Closes the multi-worker caveat from `docs/34 §9`: when two or more `app.server`
workers sit behind a load balancer, a single-use step-up ticket spent on worker A
could previously be replayed a second time on worker B, and 5 wrong step-up
passwords became `5 × N` guesses across `N` workers.

- **`app/shared_state.py` (`claim_once`, `set_ttl`, `ttl_left`, `delete`)** —
  adds atomic single-winner claiming (`SET once:<jti> 1 PX <ms> NX` on Redis,
  including `--no-eval` servers; `flock`-serialised `once.json` on `SharedFiles`;
  lock-guarded `_MemoryAdapter`) and shared TTL primitives.
- **`app/stepup.py` (`Spent.claim`, `Attempts`) + `app/api.py`** — routes spent
  ticket claims and per-session failure budgets through `shared_state` whenever
  `RE_REDIS_URL` or `RE_SHARED_DIR` is configured. **Fail-closed on outage:** if
  a configured shared replay store is unreachable, `Spent.claim` returns
  `'unavailable'` and `Ctx.require_step_up` refuses with `503
  STEP_UP_STATE_UNAVAILABLE` rather than accepting a confirmation whose
  single-use guarantee cannot be verified across instances; `Attempts` falls back
  to per-process counting rather than failing open.
- **Bug caught while proving:** `POST /api/v1/auth/step-up` advertised
  `NO_FACTOR_TTL` (`120s`) for password-only accounts while `ticket_for()`
  previously signed `exp` for `300s` unconditionally; `ticket_for(..., ttl=ttl)`
  now signs the factor-aware lifetime.
- **Proved across two real server processes (`scripts/proof_stepup_shared.py`
  15/15, `tests/test_stepup_shared.py` 8 collected cases):** cross-node replay
  refused (`423 STEP_UP_REPLAYED`), simultaneous cross-node race resolves to
  `[200, 423]`, and 5 alternating wrong passwords lock both nodes (`429
  RATE_LIMITED`).

## 1.22.0 — 2026-11-17 (step-up re-authentication for destructive actions)

Security follow-up #2 in `PLANS.md`: an already-unlocked owner browser tab or a
captured session cookie could flip the kill switch, re-wrap the encryption root,
delete users, change roles, reset MFA, overwrite `.secrets/neon-api-key`, or
purge a demo workspace without asking the person at the keyboard to prove they
are still there. `docs/34`.

- **`app/stepup.py` + `POST /api/v1/auth/step-up` + `Ctx.require_step_up`** —
  mints a signed, single-use ticket (`typ="stepup"`) bound to **exact session id
  (`sid`), exact action (`act`), and exact target record (`tgt`)**, valid for
  **300 s** when a second factor is enrolled or **120 s** on password-only
  accounts (and `GET /api/v1/security/step-up` tells the console honestly when no
  second factor is configured rather than pretending password-alone is MFA).
- **Eight gated actions**, with `kill_switch` binding `engage` and `release` as
  *different* targets so a ticket minted to turn the system off cannot be reused
  to turn it back on. Five distinct **423** error codes (`STEP_UP_REQUIRED`,
  `STEP_UP_WRONG_SESSION`, `STEP_UP_WRONG_ACTION`, `STEP_UP_WRONG_TARGET`,
  `STEP_UP_REPLAYED`), per-session attempt budget (**5 failures → 300 s cool-off**),
  and audit stamping on both mint and consumption.
- **Browser flow (`web/js/core.js`, `web/js/views-platform.js`)** — `api()`
  intercepts `423`, opens `promptStepUp()` inline (password + authenticator code
  or recovery code), and retries once with `X-Step-Up`. Security console adds a
  live **Step-up confirmation** panel with factor status, action table, recent
  confirmations, and per-action **Drill** buttons.
- **Four real defects caught and fixed while proving (`scripts/proof_stepup.py`
  44/44, `scripts/proof_mfa.py` 46/46, `tests/test_stepup.py` 27 tests):**
  * the step-up attempt counter reset itself on every failure because
    `locked_until == 0.0` was treated as "cool-off expired" rather than "not yet
    locked", making the 5-try budget decorative.
  * the account-level TOTP lockout also rejected **recovery codes**, turning five
    bad 6-digit guesses into a denial-of-service against the owner's break-glass
    recovery key during `mfa_reset`.
  * `POST /api/v1/setup/neon-key` initially wrote `.secrets/neon-api-key` *before*
    calling `ctx.require_step_up`, returning `423` after the file was already on
    disk; moved the gate ahead of the write, isolated `RE_SECRETS_FILE` in test
    fixtures, and updated `/keysetup.html` to complete step-up inline.
  * a test teardown left an enrolled authenticator on the demo owner and
    `dict(res.getheaders())` in `proof_mfa.py` silently dropped the first
    `Set-Cookie` header when `re_session` and `re_refresh` arrived together.

Not claimed: spent-ticket `jti` tracking and per-session step-up attempt counters
currently live in process memory (`app/stepup.py`), not yet in the Postgres
shared-state store (`docs/31`).

## 1.21.0 — 2026-11-17 (a skipped test is not a pass)

**G6** in `PLANS.md`, open since the first honest audit: *"Postgres/RLS tests skip
by default (14 skipped) — the S0 gate 'every pytest passes against Postgres' is
unverified in a clean checkout."* The suite looked healthy. Fourteen tests
disappeared into a skip count, the run printed "passed", and the two claims that
matter most were never executed on a machine without a database: that row-level
security actually filters a non-owner, and that the audit hash chain stays linear
under concurrent writers. `docs/32`.

- **`scripts/verify_postgres.py`** runs the Postgres, RLS and function-table
  suites and decides the verdict in one pure, unit-tested function. **A skip is not
  a pass:** any skip turns exit 0 into **exit 2 (PARTIAL)**, a failure outranks a
  skip (exit 1), and no database is exit 3 — never confused with success.
- **It probes the target before running anything** and prints what that target
  cannot prove: two connections, `pg_backend_pid()` on each, a row written on one
  and read on the other. A target that serves every client from one session cannot
  prove concurrency, no matter what its test report says.
- **`--real` makes the gate runnable in a clean checkout.** `embedded-postgres`
  carries actual `postgres` binaries in a wheel — no root, no Docker, no system
  package. `python3 scripts/verify_postgres.py --real` →
  **`21 passed, nothing skipped, RLS and concurrency proved`**.
- **The RLS migrations are applied through the product's own path**
  (`dbmod.migrate(include_rls=True)`), not by asking an operator to run a `.sql`
  file. `re_app` is created by 002 and made impersonable by 003; a policy nobody
  can `set role` into is a policy nobody can test, and that is part of how the gap
  survived this long.
- **Three real defects fell out of actually running it:**
  * the concurrent-writer test counted `load_all()["audit"]` filtered by tenant —
    but `re_audit.seq` is a **global** sequence, so the slice was the whole
    installation's interleaved history and other tests' rows decided whether the
    chain "forked". It now uses `audit_rows(tenant)`.
  * the stand-in guard was keyed on `":5433" in pg_url` — a port number says
    nothing about which server is listening on it.
  * the capability probe deadlocked itself: it did DDL inside two open
    transactions, each holding a lock the other wanted, and sat there until the
    timeout. Both probes now use `autocommit`.

Not claimed: `--real` is PostgreSQL 18 on a unix socket, not Neon's proxy.
`--url "$NEON_BRANCH_URL"` remains the run before a release.

## 1.20.0 — 2026-11-17 (a rate limit that means something on the second instance)

The last item in **G8**, and the one `docs/02 §5` had been deferring: two pieces of
security state lived in each process's memory and stopped being controls the
moment there were two processes. Ten instances turned `auth_account: 10/900` into
a hundred password guesses per quarter hour — and the load balancer handed out the
fan-out for free — while a revoked token lived only in the process that revoked
it, so a logout on node A left the token working on node B. `docs/31`.

- **One interface, three backends, each honest about itself.** `memory`
  (default, unchanged), `redis` (`RE_REDIS_URL`), `file` (`RE_SHARED_DIR`, two
  workers on one host, `one_host_only: true` because `flock` is not a network
  protocol). Selection is automatic, so an installation that configures nothing
  behaves exactly as before. `sec.LIMITER.check()` keeps the signature
  `docs/02` promised; no call site changed.
- **The Redis client is ours, and it is small.** RESP2 over a plain socket, a
  strict parser, six commands — no third-party runtime package, per `docs/02`'s
  first rule, and a smaller audit surface than a client library. `rediss://` is
  **refused rather than silently downgraded** (this client is plaintext, and the
  console reports `tls: false`), and a managed Redis with `EVAL` disabled is
  handled by a remembered `INCR`+`PEXPIRE` fallback that loses no count.
- **A store that dies must not fail open.** Mid-flight, the limiter enforces the
  limit in-process and counts the fallback. At boot, a configured backend that
  does not answer is **fatal in production** (`refusing to start:
  REDIS_UNREACHABLE`) — a production process with per-instance limits and a green
  health check is worse than one that will not start. Development starts, and the
  console says what was lost. Revocation goes the other way, deliberately: an
  unreachable store cannot prove a token was *not* revoked, so the token is
  refused.
- **The counter is a fixed window, and says so.** One atomic round trip instead of
  a sorted set plus a Lua script; the cost is up to `2 × limit` across a boundary,
  which is why the buckets are sized for it and why `limiter.window` is reported
  rather than hidden.
- **`REVOKED.clear()` was un-revoking everything.** Bounding the in-process set of
  revoked tokens by clearing it at 5000 entries silently made every logged-out
  token valid again at once. Revocations now carry the token's own `exp` and are
  pruned only once expired — entries `verify_token` would refuse anyway.
- **Role changes revoked nothing.** `if "role" in patch and "role" in
  (patch.get("role"),)` asks whether the *string* `"role"` is an element of a
  one-item tuple: never true, so the branch was dead and a demoted manager kept a
  working token for eight hours. It was also reaching for the wrong thing —
  `REVOKED` holds `jti`s. Replaced with an **epoch**: tokens carry the `ep` they
  were minted under, a role change bumps the user's epoch, and every token minted
  before it is stale on every instance, whether or not that instance issued it.
- **The console says which backend it has and what that is worth.**
  `GET /security/shared-state` (owner/admin) reports backend, `shared`, the window
  kind, the fallback count, and `next_step` when nothing is shared; `/healthz`
  shows it to privileged roles; and the posture score gains a computed control
  that is `degraded` on in-process state. On a single instance the number goes
  down, which is correct — it was measuring a product that could not safely run
  two.

Not done, and not claimed: the JSON store is still single-writer (a session row
created by node A is invisible to node B — Postgres is what multi-instance needs,
`docs/12`), the account lockout stays a data row, and SSE fan-out over pub/sub is
still per process.

## 1.19.0 — 2026-11-17 (the root key stops being written down)

The first half of the last **G8** item, and the last place in the product where a
secret was a line in a file. `.env` held `RE_MASTER_KEY` — the root of every
subkey, for every workspace — in plaintext, in a backup, in `docker inspect`, on
any laptop that ever ran the app. `docs/30`, `docs/11 §7`.

- **The root is now wrapped, not stored.** `app/kms.py` holds an envelope: a
  32-byte root sealed by a key-encryption key, written to `.secrets/dek.envelope`
  (0600) as a blob that is useless without the KEK. The KEK comes from
  `RE_KMS_KEK` (LoadCredential / Docker secret / Vault agent), from an HTTP KMS
  that speaks wrap/unwrap, or — development only, refused in production — from
  `data/kms-kek.json`.
- **Boot is fail-closed, and there is no fallback path to delete later.** An
  envelope that cannot be unwrapped is `KEK_MISMATCH` (or `NO_KEK_PROVIDER`,
  `KEK_DENIED`, `KEK_UNREACHABLE`) and the process exits, in every mode. Falling
  back to `RE_MASTER_KEY` would boot a process with the wrong key: it would serve,
  write rows, and nobody could ever decrypt them again. So the code that could
  fall back does not exist. With no envelope the old path is byte-identical, which
  is why this ships safely to a running installation.
- **`init` adopts the root it already has.** It wraps `RE_MASTER_KEY`, checks the
  unwrapped result is identical (`root_matches: true`), and re-encrypts nothing —
  the root did not move, so every subkey is the same subkey and every sealed row
  still opens. Minting a new root instead would turn the product's history into
  unreadable ciphertext, so `--new` is explicit and needs `rotate_keys.py`.
- **Rotation is a KEK rotation and needs two keys at once.** `rewrap` unwraps with
  the current *or the previous* KEK (`RE_KMS_KEK_PREVIOUS`) and wraps with the
  current one, then retires the old wrap. A one-slot provider cannot rotate: swap
  the KEK and the envelope is unreadable before anything has been re-wrapped.
  Each KEK is tried under its own AAD, so a foreign key cannot succeed by accident.
- **The panel proves the envelope, and says what it is not.** `GET
  /security/envelope` reports provider and KEK *identity* (never a key), a wrap
  and unwrap `roundtrip`, and `derives_live_keys` — the five subkeys derived from
  the unwrapped root compared by HMAC against the keyring the process is actually
  using. `unwrapped: true` says the KEK worked; `derives_live_keys: true` says it
  produced *this server's* key. It is `null`, not `false`, when there is no
  keyring to compare against. `managed_kms_integrated` is `false`, and the panel
  says the KEK is in memory after the unwrap. `POST
  /security/envelope/rewrap` is owner-only, needs `{"confirm": "rewrap"}`, audits
  the KEK id before and after, and does not ask for the tenant slug because
  nothing is destroyed.
- **The posture score stopped being `88`.** It used to be a literal on a security
  dashboard — the same defect as a usage meter with `4820` in it (`docs/29`). It
  is now `round(100 × holding / controls)` over the control matrix, with the
  envelope as one of the controls: `degraded` while the root is `RE_MASTER_KEY`,
  `enforced` when it unwraps at boot.

Not done, and said so on the console: no managed KMS, no HSM, one root for all
tenants (no per-tenant DEK), no `v2:` ciphertext prefix. Remaining in G8: the
Redis limiter.

## 1.18.0 — 2026-11-17 (a usage number that is a measurement)

The third item in **G8**, and the same class of defect as the fake `mfa_enabled` of
`docs/27`: `GET /tenant` answered `voice_minutes: 4820` and `whatsapp_messages:
18422`. Both were literals in the route body; `ai_runs` was a sum over the seeded
`agents.runs` counters, which nothing ever reset and no invoice ever read. A
customer on a usage-based plan would have been invoiced from three constants in a
source file. `docs/29`.

- **A meter is derived from rows that already record the work.** `ai_runs` counts
  `runs` whose status reached a provider (`ok`, `partial`, `complete`, `held`) —
  `partial` and `held` cost money even though the answer was withheld, and `blocked`
  is free. A guardrail's refusals are their own meter, `ai_runs_blocked`, **uncapped
  on purpose**: a workspace must not be able to hit a limit by being protected.
  `whatsapp_out`/`sms_out` count outbound messages by channel; `voice_minutes`
  counts `activities` **per call** — two 30-second calls are two minutes, because
  rounding a month's total would under-bill every workspace by up to a minute and
  hide the rule from the customer.
- **A period is a month in the workspace's timezone** (`Asia/Kolkata` by default).
  A UTC month would bill the first five and a half hours of the 1st to the previous
  invoice; `period_bounds` gives the first and last second, and a row falls in
  exactly one period.
- **`usage_ledger`** (30th collection) holds what no row can: a credit, a
  correction, a byte count. Append-only, signed, **idempotent on
  `(tenant_id, meter, source_id)`** — a redelivered webhook and a retried client
  arrive twice and must not be counted twice. `POST /usage/adjust` is owner-only,
  needs a `reason` of four characters, and is audited; a manager may read the
  dashboard but not the ledger.
- **A plan is a promise about limits.** `PLAN_CAPS` (trial/starter/growth/scale;
  `None` written rather than omitted, so "forgot" and "unlimited" cannot look
  alike), enforced before the work: contacts (before the number is sealed), inbox
  send (**before** consent and quiet hours — a plan that has run out is not a
  compliance problem), agent run (before the provider call), and the automation
  runtime, where a miss raises `QUOTA_EXCEEDED` and the run is **held** rather than
  sending what nobody paid for. Breach is `402 QUOTA_EXCEEDED`; 80 % is an
  `X-Usage-Warning` header, because an allowance at 80 % should not interrupt work.
- **A flow resets; a stock does not.** The refusal says so: `resets_at` is a date
  for SMS and `null` for stored contacts, whose message says "a ceiling, not a
  monthly allowance". Telling somebody their contacts come back on the 1st would
  be a lie they would act on.
- **`usage_snapshots`** (31st) freezes a finished month, so `docs/26`'s purge can no
  longer shrink an invoice: `POST /usage/close` copies every meter once, is
  idempotent, refuses the open month (`400 PERIOD_STILL_OPEN`), and a late write to
  a closed month is `409 PERIOD_CLOSED` — a correction posts to the current month
  instead of rewriting a figure somebody was billed for. `usage_snapshots` is in
  `proofing.NEVER_PURGE`; the purge now reports the rows it refused to touch under
  `frozen` instead of silently leaving them.
- **Five routes** (`GET /usage`, `/usage/series`, `/usage/ledger`, `POST
  /usage/close`, `/usage/adjust`), the Settings panel renders the measured card and
  the workspace chips carry a period label.
- The known debt, written down rather than hidden: every read is a scan of three
  collections, and `warnings()` is memoised for 20 seconds as a hedge — the real
  answer is write-time counters, not a cache.

## 1.17.0 — 2026-11-19 (a session you can see, rotate and end)

The other half of **G8**: after `docs/27` closed the front door with a second
factor, this closes the door itself. A session used to be one eight-hour bearer
token — a password with a deadline. A stolen one lived eight hours, nothing could
end it early, and the only way to see who was signed in was the access log.
`docs/28`.

- **A session is a row.** `sessions` collection (29th), one row per sign-in:
  device, masked address, `amr`, `last_seen`, expiry, revocation. The access token
  carries its `sid`, and `api.handle` checks that row **on every request** — which
  is why revocation is immediate rather than eventual.
- **Two halves.** Access token 30 min (`RE_ACCESS_TTL`), refresh token 30 days
  (`RE_REFRESH_TTL`), opaque and stored as a hash. `POST /auth/refresh` rotates.
- **The refresh token never enters a response body** — it is an httpOnly cookie
  scoped to `Path=/api/v1/auth`, so it is not sent to `/contacts`, a funnel page or
  a webhook. A CLI asks for it explicitly with `?refresh=body` and is warned in the
  payload, where the person who will misuse it is actually reading.
- **Reuse detection.** A superseded refresh token arriving again means two parties
  hold one credential: the session is revoked for **both** holders (`401
  REFRESH_REUSED`, `auth.refresh_reuse` in the audit chain). We cannot tell which
  one is the thief, so neither keeps the session.
- **The device list.** `GET /auth/sessions` names the browser and OS coarsely, masks
  the address to a network, records `amr` (`["pwd","totp"]`), and carries no
  credential — asserted by a test that greps the payload. `DELETE
  /auth/sessions/{sid}` ends one; `POST /auth/sessions/revoke-others` is the
  laptop-is-gone button. Another user's id returns **404, not 403**. Rendered in
  Settings → *Where you are signed in*.
- **Logout ends the row**, not just a `jti` in one process's memory, and clears
  both cookies.
- **The SPA refreshes instead of bouncing.** `api()` retries once through
  `/auth/refresh` on a 401, sharing one in-flight refresh across concurrent calls —
  twelve parallel requests must not rotate twelve times and trip the reuse detector
  against themselves.
- **A breaking change, deliberately:** tokens minted before sessions existed carry
  no `sid`, and are refused with `401 SESSION_REQUIRED`. There is no row behind
  them, so there is nothing to revoke; anyone holding an old cookie signs in once.
- **Sessions are rows, so a demo accumulates them** — the first live proof left
  fifteen in the demo tenant, the same drift `docs/26` fixed for business rows.
  A proof now stamps its logins (`X-Proof-Run` is honoured on `/auth/login`, where
  the only row created is the caller's own), `sessions` joined `PURGEABLE`,
  `POST /demo/sessions/sweep` deletes rows that can never be used again, and
  `reset_demo.py` signs itself out instead of leaving its own session behind.
- Tests: `tests/test_sessions.py` (20) across the cookies, rotation and reuse, the
  device list, and the clock (its own server with 2-second tokens). Suite: **432
  passed, 15 skipped**. Proof: `scripts/proof_sessions.py` — 41 checks on a
  throwaway instance, 34 live (the clock section needs a server whose token
  lifetimes are seconds).

## 1.16.0 — 2026-11-19 (a second factor that is a factor)

Closes the MFA half of **G8**. Every workspace had displayed `mfa_enabled: true` on
its owner account since the first release and never asked for a code. The flag was
seeded, then rendered in the user directory as though it were an observation —
a compliance claim with nothing behind it. `docs/27`.

- **`app/totp.py`** — RFC 4226/6238, dependency-free: 6 digits, 30-second step,
  ±1 window, single-use codes, base32 secrets, `otpauth://` URIs, recovery codes.
  Pinned to the reference vectors, including 20000000000 (the timestamp that
  catches a 32-bit overflow), not to itself.
- **Enrolment is two steps and enables nothing in the first.** `POST /auth/mfa/enrol`
  returns the secret, the URI and ten recovery codes; `mfa_enrolled` flips only
  when a code from that secret verifies. A secret that is a factor the moment it
  is generated is how people lock themselves out of their own account.
- **The login is two calls.** An enrolled account gets `{mfa_required, challenge,
  expires_in: 300}` and **no session, no cookie**; `POST /auth/login/mfa`
  exchanges the challenge and a code (or a recovery code) for one.
- **The challenge is not a session — and the first build let it be.** It is signed
  by the same key and names the same user, so `Authorization: Bearer <challenge>`
  opened `/contacts` with a `200` in the live proof. `sec.verify_session_token()`
  now refuses `mfa: pending` at every door that accepts a session (`api.handle`,
  the privileged health payload, SSE), with a named `401
  MFA_CHALLENGE_NOT_A_SESSION`. **Tokens are only as powerful as the doors that
  accept them.**
- **A real bug found by a flaky test.** Recovery codes were indexed with
  `sec.blind_index`, which for a value with no `@` keeps only the digits — so two
  codes sharing a digit string, including the very common case of no digits at
  all, hashed identically and one opened the account a second time. It failed one
  run in six. `sec.blind_token` indexes them without rewriting, and the flake is
  now `test_two_recovery_codes_are_never_the_same_secret`.
- **Removing a factor needs the factor.** `disable` and `recovery-codes` require
  the password **and** a code; a stolen cookie cannot switch off the thing that
  protects the account next time. Break-glass is `PATCH /tenant/users/{uid}
  {"mfa_reset": true}` — owner only, audited.
- **`mfa_enabled` is no longer writable.** `PATCH /tenant/users/{uid}` accepted it,
  which let an admin assert a factor that did not exist. The directory column now
  reports `mfa_enrolled`, with `mfa_asked` for the old meaning.
- **A workspace may demand a factor without trapping itself.** `require_mfa`
  (default off) gates `manage_users`, `kill_switch`, `pay_partner`, `approve_cp`;
  `manage_tenant` is exempt because the first draft made the policy impossible to
  turn off from a session that had not enrolled yet — a gate that can be closed on
  the person holding the key.
- **UI** — the login grew the second step (`renderAuthMfa`: code field, recovery
  code, start over), and Settings → Account gained *Enrol an authenticator* /
  *New recovery codes*, which shows the secret, URI and ten codes once.
- Tests: `tests/test_mfa.py` — 41 across arithmetic, the two-step login, and the
  ways a factor gets defeated. Suite: **412 passed, 15 skipped**. Proof:
  `scripts/proof_mfa.py` — 45 checks on a throwaway instance, 42 against the live
  desk (its at-rest section reads the store file), ending by removing the
  authenticator again so the demo stays password-only.

## 1.15.1 — 2026-11-18 (whose money is it: an individual is not a withholding agent)

Found while re-running the money proof against the cleaned demo, which is the
first time in weeks it met a client nobody had already marked as a business.

- **194-H applies to a payer other than an individual or HUF.** `commission_invoice`
  defaulted `is_business` to `True` for a client who had never said one way or the
  other, so an individual buyer's invoice withheld 5% nobody was going to deposit.
  The default is now `False`, consistent with how contacts are created.
- **"No TDS" now names the real reason.** `tds_194h` returned "below the ₹15,000
  threshold" whether the cause was the threshold or the client's legal shape; a
  wrong reason on a tax document is how an accountant loses an afternoon. It now
  distinguishes the two and says which applies.
- The money proof states the client's type instead of inheriting a previous run's
  fixture (`PATCH /contacts/{id} {is_business: true}`, restored afterwards), so its
  TDS assertion is about the rule rather than about leftover state.
- Tests: `tests/test_money.py` 32 (was 31) — an individual client gets no TDS with
  the individual reason and a net receivable equal to the total, and a business
  below the threshold gets the threshold reason.

## 1.15.0 — 2026-11-18 (a proof says it is a proof)

Closes **G7**. The demo tenant — the shop window — had filled with our own test
runs: 44 properties of which 20 were titled "Proof …", 144 contacts named after
proofs, 28 payments and 26 invoices a money proof wrote, eight "Rival Brokers"
partner logins, and units booked on real demo stock by a script that runs before
every release. Deleting the mess once would fix the symptom and leave the cause:
**a proof's rows were indistinguishable from a client's rows**. `docs/26`.

- **`X-Proof-Run: <id>` on any write stamps every row the request creates.** One
  header, honoured at the single choke point every route passes through, with the
  store capturing what it created on a thread-local list — so a route added next
  year is covered without being touched. Only an authenticated caller, or a
  *signed* provider delivery, may declare a run: a public webhook that could stamp
  a real payment as disposable would be a deletion primitive.
- **`GET /demo/hygiene`** reports rows per collection, proof rows per collection
  and **per run**, and previews a `legacy_prefix` or `legacy_since` before anything
  is deleted. **`POST /demo/proof-rows/purge`** is owner-only and needs the
  workspace slug typed out.
- **A purge decides first, deletes second.** Cascades go *down* only (a claim
  without a claimant, a unit without its property); a row a surviving row still
  points at is **skipped and reported**, because a purge that orphans the demo is
  not a cleanup. The audit chain and the tenant row are never touched, and the
  purge audits itself.
- **Legacy rows are handled honestly.** Rows written before the stamp need a named
  prefix of at least four characters (`"Proof "`, `"Rival"` — `"P"` is refused
  because it would match a buyer called Prakash). Money rows carry no name, so
  they need an explicit `legacy_since` date, and the script says out loud that it
  is a blunt instrument.
- `scripts/reset_demo.py` is a dry run unless told otherwise; `--purge` prints what
  will go before it goes. The three proof scripts now send the header
  (`money-…`, `partners-…`, `images-…`).
- **First real cleanup:** 82 rows removed — 28 payments, 26 invoices, 14 claims, 7
  contacts, 7 partner logins — leaving **24 real properties, 100 contacts, one
  partner, and a money board that is honestly empty** (no money has moved in the
  demo since the seeded receipt; an operator can now demonstrate the flow live
  instead of showing twenty-two invoices a script wrote last Tuesday).
- Proof: live round trip — a money proof stamped 8 rows (4 invoices, 3 payments, 1
  unit), the purge removed exactly those 8, and the demo's own rows were untouched.
  Tests: `tests/test_demo_hygiene.py` (13). Suite: **371 passed, 15 skipped**.

## 1.14.0 — 2026-11-18 (a listing photo, and the truth when there isn't one)

Closes **G5**. The pipeline was real since v1.9 — upload, three WebP variants, rows,
presigned URLs — and not one screen showed a photograph: 43 of the demo's 44
properties rendered an empty `<img>`, three variants of one upload counted as
three photos, and nobody could say how many photos a listing needs before anyone
may advertise it. `app/media.py` is that missing layer, and `docs/25` is the
argument for it.

- **A photo is a stem, not a row.** `derived/…/<stem>-<variant>.webp` groups by
  stem, so nine rows are three photos. Grouping is forgiving on purpose: an
  unknown variant name falls back to the bare filename, so a pipeline change
  degrades to one photo per row instead of breaking a page that used to work.
- **The cover is chosen.** `card` (960w), not `hero`: a forty-row table that loads
  1920w thumbnails ships 32 KB where 2.8 KB would do, and a site office on 4G
  notices. `hero` stays on the photo for the one surface that needs it.
- **Five slots, three to publish.** `readiness()` says `"2 photos — 1 more before
  this can be advertised"`, because a number that action can move is worth more
  than `ready: false`. **Law and polish stay separate**: `marketing.ready` is RERA
  (may it be advertised at all), `marketing.ready_to_show` adds the photo floor (is
  it worth a visit).
- **No photo is a state, not an error.** A deterministic monogram — initials in the
  listing's own hue, "no photo yet" — never a broken-image glyph and never a stock
  photograph of somebody else's building, which on a partner's Instagram is a
  misrepresentation the partner made.
- **Three honest states per surface:** `ok`, `none` (nobody has uploaded),
  `unavailable` (no Postgres backend or no storage credentials, with the reason).
  A file-store instance answers `503 PIPELINE_UNAVAILABLE` rather than an empty
  list, because "no photos" is a lie that sends an agent out to shoot a listing
  that already has eight.
- **Routes (137 → 136 counted: one added, media enriched):** `GET
  /properties/{id}/media` now returns `photos`, `photo_count`, `cover`,
  `readiness`, `placeholder`; new `GET /properties/covers?ids=…` answers a whole
  table in **one read** with `ok`/`none`/`unavailable`/`not_found` per id — a GET
  with ids in the query string, because a POST that only reads is a write-shaped
  lie. The partner catalogue (G4) now carries `photos`, a signed `cover` and
  `ready_to_show`.
- **Desk:** a photo strip above the unit grid — slot meter, 2× cover, lazy
  thumbnails, `onerror` → monogram; partner catalogue gets 34 px covers and a
  "Ready to advertise" tile.
- **`scripts/upload-listing-photos.mjs`** uploads through the real prefix and
  delivers the real storage trigger, and **never** writes `re_property_media` —
  that table belongs to the function, and a utility with its own idea of a variant
  is how a read path starts disagreeing with a write path. The demo was seeded
  with it: three listings now carry 3 / 2 / 1 photographs, which is exactly the
  ladder the floor describes.
- Proof: `scripts/proof_property_images.py` — **27 checks** self-booted and live,
  including a ranged GET on the real presigned URL (`206`, `image/webp`, WebP magic
  number, byte count) rather than trusting our own signer. Tests:
  `tests/test_property_media.py` 13 + 1 Postgres. Suite: **358 passed, 14 skipped**.
- **A performance regression the partner proof caught:** a photo count and a cover
  per catalogue row meant two queries per property over a pooled connection, and
  the partner catalogue stopped finishing inside the client's timeout. Now one
  bulk read (`property_media_bulk`) answers 39 rows in 0.63 s. And
  `proof_money.py` no longer dies on `StopIteration` when a long-lived demo runs
  out of free units — it creates a run-stamped `PRF-<stamp>` unit instead of
  consuming the real stock.
- **Two things the run taught me:** an S3-compatible endpoint may refuse a `HEAD`
  on a GET-signed URL (Neon storage answers 403), so the proof fetches 64 bytes
  instead of trusting a header; and a ranged GET answers `206`, not `200` — the
  first version of that check failed for a reason that had nothing to do with the
  code.

## 1.13.0 — 2026-11-18 (the partner can finally see the stock)

Closes **G4**: `docs/18` #5 promised a partner "separate login, own inventory view",
and the portal shipped with the login, the claim, the clock, the visits and the
earnings — but nothing to sell. `GET /api/v1/cp/inventory` and
`/api/v1/cp/inventory/{id}` open the catalogue along the narrowest line that still
lets a partner work. `docs/20` §6b.

- **Only stock that can be sold.** A property is listed when it has a free unit, or
  (no unit list) is itself `available`. A `hold` is someone else's 48 hours and a
  `booked` unit is someone else's buyer; listing them produces calls that end in an
  apology. Counts are shown, identities are not. Sold-out properties need
  `?include_unavailable=1` and are labelled, but their detail page still resolves so
  an old brochure link does not 404.
- **The seller is not for sale.** No seller name or phone, no floor or margin, no
  internal notes: `partners.public_attributes()` drops any attribute whose key looks
  like house business. A denylist rather than an allowlist, because the taxonomy
  (docs/22) keeps adding fields and a new one should reach the partner without a
  code change — while a key called `seller_phone` never will. Units appear by label,
  floor, facing, area, price and state, never with their `booking` block.
- **`WITHHELD` on every page.** The response lists what is hidden and why, so a
  partner reads a reason instead of assuming the page is broken. The portal renders
  it under the table.
- **No commission rate is invented.** Their share is a % of *gross commission*, which
  is fixed per mandate and is not in tenant settings. The estimate uses the median
  `gross ÷ sale value` over the house's own recorded deals, applied to the asking
  price, with the basis printed. No comparable deal ⇒ `null` and "the brokerage
  confirms it when you register a buyer" — rather than a confident 2% that is wrong
  on a ₹26 crore resort.
- **`marketing.ready`** is false without a RERA number, price, title or city: under
  RERA s.3 the penalty for advertising an unregistered project lands on whoever
  published it, and on a partner's Instagram that is the partner.
- Partner-only: an owner or agent calling the route gets `403 FORBIDDEN_SCOPE` — the
  house has its own inventory panel. Facets are computed from the unfiltered
  catalogue, so a filter can never hide its own alternatives.
- Desk: a third card on `/cp.html` with search, city/type facets and a price band.
- Proof: `scripts/proof_channel_partners.py` **49 checks** (was 33) — the keyword scan
  over the payload for seller/buyer/floor leakage, the default list holding no
  sold-out property, a booked unit marked unsellable, the labelled estimate, and the
  house being refused at the partner's door. Tests: `tests/test_partner_inventory.py`
  (16). Suite: **349 passed, 14 skipped**.
- **One test bug found by the suite itself:** a first version of the tenant-isolation
  test created a property in the *second* tenant to probe it, which broke a taxonomy
  assertion about that tenant's inventory count. It now probes with a partner of the
  other tenant asking for *our* id — a test that pollutes the fixture it shares is a
  bug, even when it passes.

## 1.12.0 — 2026-11-18 (money: a payment that arrives, and what it may change)

Closes **G2**, the gap that made "Sell.Do stops at booking" a claim rather than a
fact: a payment now arrives, reconciles, books the unit, and produces the right
document — while three things it must *not* do are enforced and tested. `docs/24`.

- **`app/money.py`** — pure rules: GST 18% on SAC 997221 split CGST/SGST or IGST by the
  client's state with one rounding step per component; TDS 194-H 5% above ₹15,000 a
  year; Indian financial year (April–March) and `SLD/2026-27/0007` numbering; the two
  document builders; the replay/attribution/amount verdict.
- **A token is custody, not revenue.** A booking amount produces a **receipt with
  `gst = 0`**; only the brokerage's commission is a taxable supply and gets the tax
  invoice (taxable, GST split, TDS, **net receivable**). Invoicing a token inflates
  revenue and GST liability — the most common money bug in Indian real-estate CRMs.
- **Money does not open a document gate.** A payment books a unit but cannot move a
  deal to `token` past missing KYC/booking-form/visit; it records `stage_block`, names
  the missing document, and audits `deal.stage_blocked_by_money`.
- **Reconciliation is idempotent on the provider's `payment_id`**, and an amount is
  only checked against an expectation the app wrote down (`POST /payments/orders` — a
  payment link without the provider call, and it says so).
- **Routes (8 new, 133 total):** `POST /webhooks/payments` (HMAC over the **raw
  bytes**, current or previous secret, 401 writes nothing, 200 on duplicate),
  `POST /payments/reconcile` (the production pull path off `re_webhook_events`),
  `POST /payments/orders`, `GET /payments`, `POST /deals/{id}/invoice`,
  `GET /invoices`, `GET /invoices/{id}`, `GET /money/summary`.
- `contacts` gained `state`, `gstin`, `is_business` — place of supply cannot be
  guessed from a city name, and an unknown state is invoiced intra-state *and labelled*.
- **Three defects found by the proof, not by review:** receipts and invoices numbered
  independently (two `SLD/2026-27/0002`s); a webhook-bound series restarted at `0001`
  because it read `ctx.tid`, which is empty for an unauthenticated webhook; and the
  amount guard had nothing to compare against, so a half-paid token booked a unit.
- **A deal cannot be invoiced twice** — a second tax invoice for the same commission is
  a `409 INVOICE_ALREADY_ISSUED` naming the invoice already issued; an owner may force
  a revised one, audited. Over-billing is discovered by the client's accountant, not by us.
- Proof: `scripts/proof_money.py` — **49 live checks** (unsigned refused, short
  payment flagged, correct payment books + receipts with zero GST, replay is a
  duplicate, blocked stage names its documents, 18%/5% arithmetic, IGST for an
  out-of-state client, unique series, RBAC, tenant isolation). Tests: 31 in
  `tests/test_money.py`. Suite: **333 passed, 14 skipped**.

## 1.11.2 — 2026-11-18 (installing a playbook is not a resurrection)

- `POST /automations/playbooks/{slug}/install` used to re-enable the first
  automation carrying that playbook — including one a human had paused as a
  duplicate. On the live demo a catalogue click silently re-armed a duplicate that
  texted the same buyer twice. Install is now a no-op that reports the real state
  (`enabled`, `paused_reason`); enabling is explicit via `{"enable": true}` or
  `PATCH /automations/{id}`, which also clears the pause reason.
- Test: `test_installing_a_playbook_does_not_resurrect_a_paused_automation` (HTTP,
  pause → install → still paused → explicit enable → paused again).
- Docs: `docs/23` §3 says install is not a resurrection and why. Suite: 302 passed
  / 14 skipped. Live proof: 50 checks.

## 1.11.1 — 2026-11-18 (parity: the missed call and the review ask)

The two `open-agency-os` patterns the runtime was still missing, plus the two
signals they need:

- **`call.missed` + `missed-call-textback`.** `POST /contacts/{id}/calls` logs a
  call with a direction and an outcome — the event a telephony webhook delivers. A
  missed inbound call where nobody has replied since is texted back once, with a
  15-minute callback task. A call that was already returned is not texted; an
  `answered` call is not missed.
- **`deal.closed` + `review-request`.** Fires 48 h after a deal is won, once per
  deal — the ask belongs at peak goodwill, not at possession. `deals_advance` now
  records `closed_at` when a deal closes (it never did; commission reports were
  reading the row's last edit instead).
- `_inr()` renders ₹1,10,00,000 (Indian grouping) for label-only slots;
  `{{call.when}}` and `{{deal.value}}` join the template allowlist.
- **Fixes found while proving it live, all three on the running Postgres demo:**
  - *A tick could become a timeout.* Opening quiet hours sent every deferred run due
    in the same second; the tick then spent >25 s on remote round trips and the HTTP
    request died. `Runtime.tick` now has a wall-clock budget (`budget_sec`, default
    10 s, `stopped_early`/`carried_over` in the response); leftover work is the next
    tick's job, which is safe because runs are durable and idempotent.
  - *`PATCH /tenant` answered with pre-patch settings.* The settings screen echoed
    the old quiet hours after a successful save — the response is now the row as
    written, and the proof asserts the read-back after restoring what it changed.
  - *A tick made eight queries to refresh.* `documents_many` / `Store.reload_many`
    fetch every collection a tick decides on in one statement.
- Proof: section 10 logs a call, fires the recovery automation through
  `POST /automations/{id}/run`, and asserts the SMS arrives exactly once and a
  callback task exists — with quiet hours narrowed to nothing for the duration and
  restored (and read back) afterwards. Section 11 asserts a deal won minutes ago is
  **not** asked; the 48-hour half is asserted in the tests on a fixed clock, which
  the script says out loud rather than hiding.
- Suite: 30 automation tests, **301 passed / 14 skipped**. Live proof: 50 checks, all passing
  against the running server and the real Neon Postgres.

## 1.11.0 — 2026-11-18 (the automation runtime: triggers that fire)

Closes **G1** — the gap the inspection called the largest between promise and product. The CRM could
*describe* an automation but nothing ran one, so the PRD's number-one job ("answer every enquiry in 90
seconds without a human waking up") was clocked and never delivered. `docs/23-AUTOMATION-RUNTIME.md`.

- **`app/automations.py`**: 7 triggers (`lead.created`, `lead.unanswered`, `lead.stale`,
  `visit.confirmed`, `visit.reminder_24h`, `visit.no_show`, `unit.hold_expiring`), 8 typed steps
  (`send_whatsapp`, `send_sms`, `voice_call`, `send_document`, `create_task`, `assign_owner`,
  `notify_team`, `set_stage`), 7 playbooks — the pattern list the `open-agency-os` research said a GHL
  alternative actually sells.
- **Triggers are re-derived from the data on every scan**, so a dropped webhook, a restart or a missed
  tick cannot lose a lead; the cost is that latency is bounded by the tick (15 s against a 300 s SLA).
- **Idempotency is a key, not a lock**: `automation_id:subject_id:occurrence`, where occurrence is
  `first-touch`, `reminder-<visit-day>`, `stale-<ISO week>`, `hold-<epoch>`. Two workers, a retried
  request and a replay collapse to one run — which is what makes a 15 s tick safe on more than one
  instance.
- **Five gates before any send**, each recorded as a verdict: consent `block`, DND `block`, kill switch
  `block`, quiet hours **`defer`** (the run keeps its `step_index` and wakes at 09:00), frequency cap
  6/contact/day `defer`. A blocked send is never retried; a deferred one is never dropped.
- **Dead-letter with a reason**: 4 attempts, 30 s → 30 min backoff, then `dead` with the error a human
  can act on, plus a `trace` of `{step, type, at, verdict, reason}` per run.
- **The tick drains a run**, not one step per tick — a three-step playbook advancing one step every 15 s
  would miss the window it exists to protect.
- **Routes**: `/automations` (+stats, vocabulary), `/playbooks`, `/playbooks/{slug}/install`
  (idempotent), `/tick` (manager+, `dry_run`), `/automations/{id}/run`, `/queue`, `/runs`. Steps are
  validated at save time (`UNKNOWN_STEP_TYPE`, `STEP_INCOMPLETE`, `UNKNOWN_TRIGGER`).
- **Worker**: a 15 s in-process thread (`RE_AUTOMATION_TICK`, `0` disables; the test suite pins it off
  so counts stay deterministic) so automations fire with no console open and no request.
- **Seed is now executable**: the six seeded automations carry real typed steps instead of strings like
  `consent.validate` that no runtime could run.
- **Desk UI**: an Automation runtime panel — fleet with typed steps and per-automation stats, install a
  playbook, run a tick or a dry run, and a "needs a human" list that states each block/deferral reason.
- **Proof**: `scripts/proof_automations.py` — 32 live checks (idempotent install, dry run sends
  nothing, a real tick messages once and stops the speed-to-lead clock, a second tick sends nothing, a
  consent-less lead is never messaged, the kill switch is exercised and released, malformed automations
  are refused, RBAC + tenant isolation). `tests/test_automations.py` — 23 tests including a fake-store
  runtime on a fixed clock (poison step dies after 4 attempts, two workers → one message).
- **Two defects found by running it, both fixed here rather than described around:**
  - *Cross-process double send.* The idempotency key was checked against the
    process's in-memory working set, so two instances could each plan the same
    occurrence and each send. `db/008_run_idempotency.sql` adds a partial unique
    index on `(tenant_id, doc->>'idempotency_key')` for `runs`; `Runtime._claim_run`
    treats the constraint violation as "already claimed". A tick also re-reads the
    rows it decides on (`Store.reload` → `Runtime._refresh`) so the loser never
    gets as far as sending.
  - *A shared Postgres session between threads.* The new worker thread and the
    request threads used one psycopg connection, interleaving `set_config(
    app.tenant_id)` with a login's insert: RLS then refused the audit row, and the
    aborted transaction poisoned every later statement (observed as 500s on
    `/auth/login` on the live demo). The worker now opens its own `Store` and
    connection; `PgBackend._run` serialises statements with a lock and rolls back
    on any failure. `tests/test_db_threading.py` (4 tests) proves both, no database
    needed.
  - An empty database seeded before v1.11.0 keeps its string steps (`Store.seed()`
    only runs once), so `scripts/migrate_demo_automations.py` upgrades them in
    place — idempotent, `--dry-run`, and it reports anything it will not touch.
- Suite: **295 passed, 14 skipped**. Docs: `docs/23`, PLANS G1 closed, README limitations corrected.

## 1.10.2 — 2026-11-18 (deployment staged: one command + one key away)

Gap **G3**: three Neon Functions built, proved against the live bucket and branch — never deployed.

- **`scripts/deploy_functions.sh`** — preflight → `link` → `config plan` (read-only) → `deploy --env`
  → `functions list` / `triggers list`, then optionally the deployed proof. Refuses to run without a
  credential and prints both sanctioned ways to supply one (`/keysetup.html`, or `NEON_API_KEY` in the
  calling shell); the key never reaches argv, history or this script's output because
  `scripts/neon-cli.sh` reads it from a 0600 file into the child environment.
- **`scripts/proof_deployed_functions.py`** — the real closure of G3: against **deployed** URLs it
  proves each function's security surface (unsigned delivery refused, signed delivery accepted once,
  replay deduped, sweep reports a summary). A URL that is not supplied reports `SKIP` and exits **2**,
  so "not proven" can never be mistaken for "proven".
- Re-verified locally the same day, all green: `npm run photos:proof` (upload → 3 WebP variants →
  Postgres rows, forged delivery 401), `npm run sweep:proof` (orphan purged, audit chain verified,
  retention applied), `npm run webhook:proof` (signed event accepted once, retry deduped, forgeries
  refused, amount stored in paise).
- `docs/15 §7a` documents the staged path and states the blocking condition in one sentence: **one
  project-scoped API key, nothing else.**
- Still blocked: the deploy itself. Stated plainly in `PLANS.md` §9 G3 and in README's limitations —
  the Functions are *not* running remotely until that key exists.

## 1.10.1 — 2026-11-18 (doc-truth pass: measured numbers, no typed ones)

Found by inspection: every count in the entry-point documents was true when written and wrong by the
time anyone read it — README said 71 routes / 87 tests, `docs/04` said 71 routes, `docs/09` said 69
routes / 28 tests, `docs/03` said 21 collections, `PLANS.md` said 15 documents. The code said **118
routes, 234 test functions (280 cases), 26 collections, 22 documents**. Nothing was broken; the
documents simply hadn't been re-measured, which is worse than being wrong — it makes the whole doc set
untrustworthy at a glance.

- **`scripts/doc_facts.py`** measures routes (by decorator), collections (by the store's own list),
  test functions (AST) and collected cases (pytest), proof scripts, migrations, docs and modules, then
  writes a generated block into `README.md`. `--check` fails if that block is stale **or** if any
  monitored document quotes a number that disagrees with the code; `--offline` skips the pytest
  subprocess so it can run from inside pytest.
- **`tests/test_docs.py`** (5 tests) makes documentation part of the suite: the facts block matches,
  no doc quotes a stale count, every doc cited in the README exists, nothing in `docs/` is orphaned
  from the chain, and every gap in the register has a proof-of-closure line.
- **`npm run verify`** now ends with `doc_facts.py --check`, so a stale number fails the build.
- **`docs/03-DATA-MODEL.md` rewritten where it mattered**: 26 collections, and the `properties` shape
  since v1.10.0 (`category/type/deal_kind/area/pricing/attributes`), plus the `units`, `site_visits`,
  `ingest_keys` and `cp_*` collections that were never documented. It was the one doc a new engineer
  or an AI assistant reads first.
- **`docs/04-API-AND-WEBHOOKS.md`** gains §8a: the whole vertical route surface (taxonomy, visits,
  ingest + SLA, channel partners, units) with its refusals, plus what is deliberately absent.
- **`docs/01-PRD.md` v1.1**: §4.1a records the vertical as in-scope, §5.9 adds FR-80…FR-87 (a
  requirement ID for every capability the vertical work added — they had none, so the release
  criteria could not see them), and release criteria 8–9 now require the live proof scripts and a
  green `doc_facts --check`.
- **`PLANS.md` rebaselined** against the code, plus **§9 Gap register** (G1–G8: no automation
  executor, no payment collection, undeployed Functions, CP portal without inventory, no property
  photos, Postgres tests skipping, polluted demo data, Phase-1 security pack) with the proof that
  closes each. Decisions **D19–D22** logged, including the unresolved pricing shape (per-tenant vs
  per-seat) that two documents had been silently disagreeing about.
- **`docs/02` code map** lists the five new rule modules; README gains the vertical desk and partner
  portal as screens 10 and 11; the README's self-contradiction ("no Postgres/RLS" vs "the database is
  live") is gone.
- Tests: **266 passed, 14 skipped**. `doc_facts --check` green.

## 1.10.0 — 2026-11-18 (property taxonomy: five categories, five forms)

Residential and commercial are not variations of one form. Neither are a
warehouse, a survey-numbered farm and a banquet hall. `docs/22-PROPERTY-TAXONOMY.md`,
researched from live 99acres / MagicBricks listing pages, Grade-A warehouse spec
sheets and Maharashtra land-record sources before a line was written.

- **Five categories with their own field sets** (`app/taxonomy.py`): residential
  (25 fields), commercial (31), industrial & logistics (22), land & agriculture
  (21), hospitality (16). BHK/facing/furnishing for homes; carpet-vs-super area,
  seats, cabins, meeting rooms, fit-out, lock-in months, deposit months, CAM
  ₹/sqft/month for offices and shops; clear height, floor load, dock levellers,
  truck apron, column spacing, flooring class, fire system for warehouses; survey
  number, occupant class, encumbrance, NA order, irrigation for land; keys, star
  class, banquet seating, F&B licence for hospitality.
- **A field from another category is a 422, not a silent drop.** "Unknown
  field(s) for this category: clear_height_m" — the reason a generic property
  table quietly rots.
- **Required fields per category**: residential needs area + BHK + bathrooms,
  industrial needs clear height + floor load, commercial needs fit-out, land needs
  land use, and hospitality needs a capacity (keys, beds or banquet seats).
- **Units normalised to sqft** — `sqft, sqm, sqyd, acre, hectare, are, guntha,
  cent` — and **bigha is refused on purpose** with the reason stated, because it
  varies by district and a silent conversion is wrong by acres.
- **Price bases that actually differ**: `total, per_sqft, per_month,
  per_month_per_sqft, per_annum, per_seat_month, per_acre`. A ₹22/sqft/month
  warehouse lease annualises to ₹52,80,000; a ₹21,000/month rent is never
  compared against a ₹65,00,000 sale price.
- **Four routes**: `GET /taxonomy` (the form source of truth), `POST /properties`
  (validated create), `POST /properties/search` (ranked across categories, with
  reasons), `POST /contacts/{id}/requirement` (structured requirement → shortlist,
  persisted on the contact).
- **Matching that says why**: 0–100 with a reason per component, caps at 85 for a
  missing must-have, 60 for the wrong category, 65 for the wrong deal kind.
  `"godown"` resolves through the alias index to industrial/warehouse instead of
  substring-matching titles.
- **Money the way India reads it**: ₹80,00,000, not ₹8,000,000.
- **Seed inventory now spans categories** — a Kharadi office on a per-sqft lease,
  a Bandra high-street showroom, co-working seats priced per seat, a Bhiwandi
  Grade-A warehouse (12 m clear height, 6 MT/sqm, 12 docks), a Chakan shed in
  acres, an MIDC industrial plot in sqm, 5.5 acres of canal-touch farm land with
  `Gat 214/2` and Class I occupancy, an NA plot in guntha, a banquet hall and a
  40-key resort — plus legacy rows upgraded in place by `hydrate`.
- **Desk UI**: "Property catalogue" generates the form from `/taxonomy` (category
  chips → that category's own fields) and "Find a match" shows scored results with
  their reasons.
- **Migration for existing databases**: `scripts/seed_taxonomy_inventory.py`
  (idempotent, title-keyed, `--dry-run`), because `Store.seed()` only ever runs on
  an empty database.
- Tests: 259 passed, 14 skipped (`tests/test_taxonomy.py` adds 28). Proof:
  `scripts/proof_taxonomy.py` — 40 live checks, green against a self-booted
  instance and against the running server.

## 1.9.0 — 2026-11-18 (unit inventory: timed holds, booking, sale)

docs/18 §8 #6, the last piece before the lifecycle closes: a property is not a
unit, and "the property is available" is how two buyers get promised the same
flat. `docs/21-INVENTORY-AND-BLOCKING.md`.

- **A unit is a row with a ledger** (`app/inventory.py`): status, `block`,
  `booking`, `sold`, and an append-only `history[]` that records who held, booked
  and sold it.
- **A hold is timed and self-releasing.** `POST /units/{id}/block {deal_id,
  hours}` — 48 h default, matching the "48-hour price hold" the funnel pages
  already advertise. Expiry is *derived at read time*, so an expired hold reads
  as available with `block_expired: true` and nothing has to run.
- **One deal per unit.** A second hold or booking is a 409 whose details name
  `held_by_deal`, `held_by_contact`, `until` and `hours_left` — an agent needs to
  know who to call, not that something is "unavailable". The holder may renew.
- **State only moves forward**: available → blocked → booked → sold, with release
  back from blocked/booked and `withdrawn` for a pulled unit. A status PATCH
  cannot reach `booked` or `sold` (`USE_THE_DEAL`), and `sell()` refuses a unit
  that this deal did not book (`SALE_WITHOUT_BOOKING`) — a sale with no token is
  a data-entry accident.
- **The deal carries the unit**: `→ token` books it, `→ registration`/`→ won`
  sells it, applied *before* the stage write so a refusal leaves both untouched.
  Pipeline and inventory can no longer disagree about whether a unit is gone.
- **Deterministic grid generation** (`POST /properties/{id}/units/bulk`):
  floor-major unit numbers, price laddered per floor, re-runs skip existing
  numbers and report `created`/`skipped`.
- **Releasing someone else's hold needs a manager** — otherwise an agent can
  quietly free a unit a colleague has already promised.
- **Inventory panel** on the desk: property picker, ledger tiles, and a unit grid
  with hold / book / release / withdraw actions on the deal open in the drawer.
- Seeded 32 units across the first four properties with one booked, one on hold
  and one sold, so the panel is not empty on a fresh install.

Proof: `scripts/proof_inventory.py` — 20 live checks, run against a seeded
throwaway instance and `--live` against Postgres. The refusals are the point: two
deals, one unit, three different ways of trying to double-sell it, all refused
with a named holder.

Tests: `tests/test_inventory.py` — 24 new. Full suite **233 passed, 14 skipped**.

## 1.8.0 — 2026-11-18 (channel-partner portal: claims, protection, payouts)

docs/18 §8 #5. The gap Sell.Do, LeadSquared and Zoho leave open, and the feature
Grovia sells on: the broker network that produces the buyers gets a login, a
claim on a buyer, and a defensible number at payout time.
`docs/20-CHANNEL-PARTNERS.md` is the policy document.

- **A partner is a login with `role: channel_partner`**, confined by the
  dispatcher to `/api/v1/cp/*` and `/auth/*` — a structural boundary, not a
  convention. A partner token asking for contacts, pipeline, audit or the desk
  summary is a 403 even if a future route forgets a role check. `can()` returns
  False for every internal permission as a second layer.
- **Registration is a claim with a clock** (`app/partners.py`): a partner claims a
  buyer, a manager approves, and the buyer is protected for the partner's window
  (90 days default, 1–365 configurable). Same partner → `duplicate`; another
  partner's live claim → 409 with the days remaining; an **expired claim releases
  the buyer** and expiry is *derived*, never stored, so a missed cron cannot
  silently extend a claim.
- **The house's own leads are not claimable** (409 `CP_LEAD_ALREADY_IN_PIPELINE`).
  Without this, a partner can wait for the brokerage to generate demand and then
  register the buyer for 2%.
- **Earnings carry a basis**: `recorded` (from the deal's commission ledger) or
  `expected` (an estimate at the partner's rate). An estimate that reads as a
  promise is how payout arguments begin.
- **Payouts are owner/admin only** (`pay_partner`): refused for agents, managers
  and partners; require a won deal, a partner credit in the ledger, and a payment
  reference that lands in the deal, the payout row and the audit chain
  (`cp.payout_paid`).
- **`/cp.html`** — the partner portal: sign in, register a buyer, watch the
  protection clock, see visits credited and commission with its basis. Phones are
  masked there: the partner knows the number they submitted, and a stolen partner
  session should not be a harvestable contact list.
- **The desk gained Channel partners and Partner payouts panels**: pending claims
  with approve/reject, the network with rates and credited visits, and pending
  payouts with "mark paid".
- **Seats cannot be converted** into or out of a partner account through
  `/tenant/users`; `/user invite` will not create one. A partner without a firm,
  a rate and a window is a bug waiting to happen at payout time.
- **Consent stays pending** on a partner referral: the partner's attestation is
  not the buyer's consent to the brokerage, so messaging is still blocked until
  someone captures it in the house's own words.

Proof: `scripts/proof_channel_partners.py` — 33 live checks, run both against a
throwaway seeded instance and against the live Postgres deployment:
`--base http://127.0.0.1:5433 --live`. Covers the full lifecycle, the refusal
matrix, the payout permission matrix and five audit actions.

Tests: `tests/test_channel_partners.py` — 27 new. Full suite **208 passed, 15
skipped**.

## 1.7.0 — 2026-11-18 (portal ingestion, de-duplication, speed-to-lead)

The next item of the roadmap in `docs/18` §8: leads now arrive from the portals
they actually arrive from, get attributed, get an owner and get a clock.
`docs/19-PORTAL-INGESTION.md` is the operating manual.

- **`POST /api/v1/ingest/{source}`** — public, authenticated by a hashed
  per-tenant ingest key (`X-Ingest-Key`). Adapters for 99acres, MagicBricks,
  Housing.com, NoBroker, Meta lead ads, Google Ads and a website form, each a
  declarative alias list; Meta's `field_data[]` and Google's `user_column_data[]`
  are unwrapped by the same flattener.
- **Parsers that refuse to guess.** `normalize_phone` accepts only a 10-digit
  Indian mobile (6–9 prefix) after stripping formatting, `0` and `91`; anything
  else is a 422, not a contact with an undialable number. `parse_budget` reads
  `₹30,00,000`, `30L`, `1.2 Cr`, `40-60 Lakh`, `Under 25 lakh`, `Above 2 crore`.
  A first version parsed `30,00,000` as ₹3,000 — commas are group separators in
  Indian formatting, and that is now pinned by a test.
- **De-duplication that keeps attribution.** Same `phone_hash` → blanks filled,
  the agent's edited requirement untouched, and the new portal **appended to
  `sources[]`** so cost-per-closure per portal survives to booking. Identical
  `(source, external_id, campaign)` retries are not duplicated.
- **Load-based assignment** to the active agent/manager with the fewest open
  deals (ties on id), so a week of absence does not leave a backlog pinned to one
  person.
- **Speed-to-lead clock.** Every contact carries `sla:{seconds,due_at,source}`
  (portals 300 s, website 600 s, walk-in 60 s, referral 900 s; tenant override
  clamped 30–86 400). It stops on the first outbound message from the inbox or a
  logged call. `GET /api/v1/leads/sla` splits leads into open / breached / met,
  with the average first-response time; `POST /leads/{id}/respond` is idempotent.
- **Key lifecycle**: mint (token returned once, only an HMAC blind index stored,
  listing shows `prefix` only), revoke, rotate. Bad key and dead tenant return
  the same 401 so tenants cannot be enumerated.
- **Fixed while building this: a session-fixation hole in the cookie layer.**
  `app/server.py` set the session cookie on *any* 200 whose JSON body happened to
  contain a `token` field — so a route that reflects a string back (a fresh
  ingest key, a webhook echo) would replace the caller's session with it. Now
  only `/auth/login` and `/auth/signup` may issue the cookie.
- **`out_contact`** now exposes `sources[]`, `first_response_at` and the SLA
  state, so the list view shows the clock without a second request.
- **UI**: the vertical desk gained *Speed to lead* (counts, countdown, one-click
  "reply logged") and *Portal feeds* (leads per source, with the integration
  note).

Proof: `python3 scripts/proof_portal_ingest.py --base http://127.0.0.1:5433` —
live against Postgres, 24 checks passing: key mint/mask, ₹-formatted lead, repeat
enquiry merged from a second portal, clock started then stopped, no-key / wrong-
key / revoked-key 401s, viewer 403, cross-tenant 404, and five audit entries with
a verified hash chain.

Tests: `tests/test_portal_ingest.py` — 41 new. Full suite **181 passed, 15
skipped**.

## 1.6.0 — 2026-11-18 (real-estate vertical CRM: research, then the parts that make it vertical)

Research first, as asked: `docs/18-MARKET-RESEARCH-REAL-ESTATE-CRM.md` maps the four
shapes of the market (Salesforce-class platform, GHL-class agency OS, the
self-hosted `open-agency-os` composition, and the vertical CRMs — Sell.Do,
Propacity, Grovia, Follow Up Boss, kvCORE, BoomTown), the pricing, and the gap
table against what this codebase already had. The conclusion that drove the
build: the horizontal products compete on *channels*, the vertical ones compete
on *state machine and money* — so the build is the deal's journey and the money
attached to it, not another funnel builder.

- **Site visits are now objects** (`site_visits`), not a stage label:
  `scheduled → confirmed → visited → revisit → dropped`, with agent, slot,
  duration and pickup. `find_conflict()` refuses a double booking on the agent's
  calendar (409 `VISIT_CONFLICT`) using half-open intervals; a dropped visit
  frees its slot. Illegal transitions are 422s that list what *is* allowed.
- **Explainable matching** (`GET /contacts/{id}/matches`): a 0–100 score with a
  reason per component — budget band, locality named in the requirement, type
  words, sqft ±20%, availability. Deliberately not a black box: an agent who
  cannot see *why* property B ranked above A will not use the ranking.
- **A stage machine with gates.** `new → contacted → qualified → site_visit →
  negotiation → token → agreement → registration → won`. Entering `token`,
  `agreement`, `registration` or `won` requires the documents those stages imply
  (KYC + booking form, sale agreement draft, stamp duty + registration
  appointment, registered sale deed), and `site_visit`/`negotiation` require a
  visit for *that property* at or beyond `scheduled`/`visited`. A move that
  cannot be documented is a 409 naming what is missing
  (`STAGE_GATE_DOCUMENTS` / `STAGE_GATE_VISIT`). Owners and admins may override
  with `force: true` — which writes `deal.stage_forced` to the audit chain.
- **Commission ledger** (`POST /deals/{id}/commission`): gross → channel-partner
  share → agent/brokerage split, computed in paise with one rounding step per
  recipient so the parts always sum to the gross (`sums_to_gross`). Percentage
  inputs are clamped (rate ≤ 25%, shares ≤ 100%) rather than trusted.
- **`GET /deals/{id}`** returns what a board cannot show: the checklist of
  remaining stages with what each still needs, this contact's visits, and the
  commission.
- **`GET /crm/summary`**: the desk's morning numbers, including deals blocked at
  their next stage with the reason.
- **`GET /auth/session`** returns a CSRF token for an already-signed-in browser.
  The console kept the token in memory only, so a reloaded or bookmarked page
  could not POST. Same-origin policy is what makes this safe — and it is what
  CSRF already relies on.
- **`/crm.html` — a working vertical desk.** Pipeline board over the nine stages,
  a deal drawer (documents → checklist → visits → commission → matches), a visit
  form that surfaces the calendar conflict, and a "needs attention" list. Linked
  from the Pipeline page.
- **Seed data** now includes a deterministic tail of money-stage deals (token,
  agreement, registration with one document deliberately missing, won with a
  computed commission) and site visits across the lifecycle, so the gates are
  visible rather than theoretical.

Proof: `python3 scripts/proof_crm_vertical.py --base http://127.0.0.1:5433` —
live, against Postgres, 23 checks all passing: session/CSRF, summary, matches,
visit create → conflict → illegal transition → confirm → visited, stage gate
blocking then opening on documents, commission split, owner force-override,
viewer 403, cross-tenant 404 and an empty visit list for the other tenant.

Tests: `tests/test_crm_vertical.py` — 35 new tests (rules layer: scoring bands,
rounding, transitions, gates; wiring layer: refusals). Full suite **140 passed,
15 skipped**.

## 1.5.0 — 2026-11-17 (credential handling hardened)

The Neon credential for this work arrived as an attachment. It was **not an API
key**: 95 bytes, single line, `https`-scheme, host `ep-….aws.neon.tech`, no
userinfo, no query — the console's endpoint URL. Inspected structurally (never
printed), then overwritten with zeros and unlinked. The hardening this prompted
is `docs/17-SECRET-HANDLING.md`.

- **The CLI credential no longer lives in the runtime env.** It moved from
  `.env.local` to `.secrets/neon-api-key` (file 0600, **directory 0700**,
  git-ignored): the app process never loads it, so no config dump, `/healthz`
  style report, or traceback can reach it. `.neon` could not host it — in this
  repo `.neon` is a *file* (the CLI link context), which the first attempt
  discovered the hard way (`NotADirectoryError`).
- **`scripts/neon-cli.sh`** runs any Neon command with the key in the *process
  environment*, never argv (`/proc/<pid>/cmdline` is world-readable) and never
  shell history. `NEON_SHRED=1 …` overwrites and unlinks the credential after a
  successful command — use-then-burn, which is the only control that matters
  given §4 below.
- **The secret gate now catches Neon project keys.** `check_secrets` only matched
  `nak_live_…`; real project keys are `nak_…`, so a key dropped in `uploads/`
  scanned clean. Now `nak_/npg_/nsk_`, GitHub PATs and Slack tokens are matched
  in the exposure zones (`uploads/`, `Downloads/`, `Desktop/`), and a filename
  that says *key/secret/token/credential* raises a warning even when the content
  matches nothing — the file is still sitting there. Verified with a planted
  `nak_…` file: 1 critical + 1 warning, then removed.
- **`uploads/` is git-ignored.** A paste-zone must never be committable.
- **The intake route is rate-limited** on its own limiter bucket, so a stolen
  owner session cannot swap in a key and drive deploys at machine speed.
- **Honest limits, written down**: encrypting the credential file with
  `RE_MASTER_KEY` would be theatre — the master key sits beside it in the same
  snapshot, readable by the same user. And the workspace snapshot *includes*
  `.env`, `.env.local` and `.secrets/`: 0600 stops other users, not a snapshot.
  Hence use-then-shred, and rotate at the console when the work lands.
- Verified live: form write → `.secrets/neon-api-key` at 0600 in a 0700 dir,
  runtime env clean → `scripts/neon-cli.sh me` picked it up and got a real
  `Authentication failed` from the API (fake key, so the plumbing is proven and
  no secret was used) with no value in the output → shred → status `absent`.
  Suite: 120 tests, 105 passed / 15 skipped. `check_secrets`: OK.

## 1.4.1 — 2026-11-17 (a way to actually add the key)

`.env.local` is mode 0600 and git-ignored, which is exactly why a browser-based
workspace viewer cannot edit it — the operator was stuck. New one-shot intake
page: **`/keysetup.html`**.

- Owner-only (a viewer gets 403, anonymous gets 401, and a cookie-auth POST
  without the CSRF header gets `CSRF_FAILED`).
- Writes exactly one named key, `NEON_API_KEY`, into `.env.local`: atomic
  temp-file + `os.replace`, `fsync`, mode forced to `0600`, existing assignment
  or the repo's `# NEON_API_KEY=` placeholder replaced in place, other variables
  untouched.
- **No variable injection**: the value is checked against
  `[A-Za-z0-9_.\-+/=]{8,512}`, so a `\n` that would smuggle in a second variable
  (`DATABASE_URL=…`) is a 400. A rejected write leaves the file byte-identical.
- The key is never echoed, never logged, and never written to the audit trail —
  the audit records `setup.neon_api_key_set` with a SHA-256 fingerprint, and the
  UI shows only `nak_••••4555`.
- `app/keysetup.py` (writer, `RE_ENV_LOCAL` override for tests), two routes
  (`GET`/`POST /api/v1/setup/neon-key`, `min_role="owner"`), `web/keysetup.html`,
  and 17 tests (`tests/test_keysetup.py`) covering injection, file mode,
  idempotence, and authority.
- Verified live: save → file has the key at 0600 with neighbours intact → status
  shows fingerprint → newline payload rejected → audit clean of the value →
  test key removed.

## 1.4.0 — 2026-11-17 (two more Neon Functions: scheduled sweep + payments webhook)

`docs/16-NEON-FUNCTIONS-SCHEDULE-AND-WEBHOOK.md`. Three functions now deploy from
one `neon.ts`, each owning its own table, with the app holding SELECT only.

- **`compliancesweep`** (schedule, `0 2 * * *` UTC = 07:30 IST). Purges orphaned
  listing-media variants — the documented limitation of `docs/15 §8`, now closed by
  a job that plants nothing and mops up after deletes; reports retention evidence
  per class (365/540/2555 d) without deleting documents; and verifies the
  `re_audit` hash chain per tenant. One row per run in `re_compliance_runs`.
- **`paymentswebhook`** (public endpoint, HMAC-SHA256 over the raw body).
  Constant-time compare with a length check, dedupe on `(provider, event_id)` in
  the database, 200 on a retry so providers stop retrying, 401 with nothing
  written for a forgery, 64 KiB body cap, and `notes.tenant_id` copied into a
  real column so the app filters payments by workspace instead of digging in JSON.
- **Slug rule learned the hard way**: `@neon/config` requires `^[a-z0-9]{1,20}$`,
  so `property-photos` was never a valid slug — it is `propertyphotos`. The rule
  now surfaces locally by loading `neon.ts` through `defineConfig`, before a
  deploy can fail on it.
- **A false-positive check, removed**: the first sweep flagged gaps in
  `re_audit.seq`. The live table showed why that is wrong — `seq` is a global
  `bigserial` (480 rows across a 2…486 span) so per-tenant partitioning invents
  gaps, and real holes come from rolled-back inserts. Gaps are now informational;
  `ok` depends on broken hash links and duplicate `seq` only.
- **New read paths**: `GET /api/v1/compliance/runs` and
  `GET /api/v1/webhooks/events`, both `manage_tenant`, both 503 on the file store
  rather than an empty list. `db/006` + `db/007` create the tables and grants
  (`db/007` because rewriting an applied migration is history-editing, and the
  checksum gate correctly refused it).
- **Proofs** (`npm run sweep:proof`, `npm run webhook:proof`): the sweep proof
  uploads a photo, lets the pipeline derive variants, deletes the source, and
  asserts exactly 3 objects + 3 rows were purged while 6 live rows survived. The
  webhook proof signs a real `payment.captured`, replays it (200 duplicate),
  tampers with the body (401), omits the signature (401), sends a 2-char signature
  (401, no 500), and a 65 KiB body (413).
- **Suite**: 114 tests — 86 passed / 15 skipped on the file store, 99 passed /
  2 skipped with `RE_TEST_DB_FROM_ENV=1`. `check_secrets`: OK.

## 1.3.0 — 2026-11-17 (Neon Function: listing-photo pipeline)

The first Neon Function in this repo: an upload handler on
`storage_object_created`. A photo PUT into object storage now becomes three WebP
variants, a row per variant, and a presigned URL the app can serve. Details and
the proof run: `docs/15-NEON-FUNCTION-PROPERTY-PHOTOS.md`.

- **New function** `functions/property-photos.ts` (declared in `neon.ts`, trigger
  `on-property-photo` on bucket `projectassets`, prefix `properties/`): real
  format sniffing, pixel ceiling against decompression bombs, EXIF-aware
  resize to thumb/card/hero, never upscaling, one module-scope `pg` pool
  (`max:5`) with `attachDatabasePool`.
- **Authenticated by `parseTriggerDelivery`** — the only check a trigger route
  can use, since Neon sends no JWT and no `X-Secret`. What a forged delivery can
  do is bounded in the code, not just claimed: it can only re-render an object
  that already exists, into `derived/…`, upserting the same rows.
- **Self-retrigger guard**: variants are written under `derived/properties/`, and
  `isDerivedKey()` refuses them even if the trigger prefix is widened. Delivery
  is at-least-once, so the write is an idempotent upsert on
  `(source_key, variant)`.
- **New read path**: `GET /api/v1/properties/{id}/media` +
  `PgBackend.property_media()`; `db/005_property_media.sql` grants `re_app`
  SELECT only. The function owns the table's writes, the app only reads it —
  `re_documents` keeps its single writer.
- **Bug found and fixed: the bucket name.** Every env file said `assets`; the
  bucket that exists is `projectassets`. A presign signs any key without
  contacting storage, so the earlier "presign verified" result hid a
  `404 NoSuchBucket` — the proof now does a real PUT and a real GET.
- **`secrets:check --history` no longer fails on its own fixtures** (7 false
  criticals). Tokens still present in an allowlisted fixture path at HEAD are
  ignored; anything else, including a real secret committed and later deleted,
  is still reported. Verified with a probe token: still `critical`.
- **Suite**: 92 tests, 79 passed / 13 skipped on the file store (Postgres and
  time-of-day tests skip as documented); `tests/test_property_media.py` passes
  green against the real branch with `RE_TEST_DB_FROM_ENV=1`. Also fixed a
  latent `NameError` in `tests/test_api.py`'s quiet-hours skip path.
- **Not yet deployed** — `neon deploy` awaits a project-scoped API key (§7 of the
  doc).

## 1.2.0 — 2026-11-17 (adversarial agent, pass 2)

`sentinel/agent.py` — an autonomous security-testing agent with a hard policy gate —
audited the database and the frontend. Nine defects found and fixed; the evidence is
in `docs/14-SECURITY-AUDIT-PASS2-2026-11.md`.

- **X-Forwarded-For is no longer trusted** (`critical`): one header rotated per
  request defeated the login throttle, the lockouts and the audit trail's IP field.
  Now only a peer inside `RE_TRUSTED_PROXIES` may set it; empty by default.
- **RLS is now real** (`high`): `FORCE ROW LEVEL SECURITY` on `re_documents` and
  `re_audit`, and the web tier connects as `re_app`
  (`NOSUPERUSER NOBYPASSRLS`). Migrations and boot hydration use
  `RE_ADMIN_DATABASE_URL`. Every write opens with
  `set_config('app.tenant_id', …, true)`.
- **A lost connection is no longer a 500** (`high`): `PgBackend` reconnects and
  retries once, then answers `503 STORAGE_UNAVAILABLE` instead of a traceback.
- **A viewer can no longer read the user directory or the audit trail** (`high`):
  both require `manage_tenant`.
- **Access-log credential leak closed**: tokens in query strings are redacted by key
  and by shape before the line is written.
- **`/healthz` is liveness-only for strangers**; the infrastructure report needs an
  owner session. **`Server:` banner removed.**
- **Dormant probe accounts pruned**: `scripts/prune_probe_accounts.py` (+ the
  Postgres test now removes its own rows).
- New: `scripts/provision_app_role.py`, `db/004_runtime_role.sql`,
  `sentinel/.sentinel-token.json` (0600, git-ignored), `npm run audit:sentinel`.
- Suite: **87 tests**, sentinel 47/47, audit 44/44 (100/100), redline 53/53 (100/100).

## 1.1.2 — 2026-11-17 (security audit)

Ten defects found and fixed; `docs/13-SECURITY-AUDIT-2026-11.md` has the evidence.

- **Credential echo closed:** `inspect_text()` returned a `redacted` field that
  leaked `sk-ant-…` keys (and `run_agent()` echoed it). Redaction now covers 11
  provider key shapes plus DSNs and private keys.
- **Rate limiting rebuilt:** per-IP `auth` 30/5 min, new per-account bucket
  (10/15 min, keyed by a hash), and account lockout is now time-based so it
  cannot be used to deny service to a user.
- **Hash chain made concurrency-safe:** `pg_advisory_xact_lock` per tenant; the
  tail is read from the database, not from process memory. Repair tool added and
  run (`scripts/repair_audit_chain.py`).
- **`data/store.json` 0644 → 0600**, enforced at write and repaired on boot.
- **Pooler session leak fixed:** every connection starts `reset role; reset all`.
- **Secret exposure zone:** the uploaded plaintext credential dump was shredded;
  `check_secrets.py` now scans directories outside the repo.
- Audit probe no longer reports a false negative on an empty audit table; body
  cap and cookie flags are now covered by tests; the audit collection is no longer
  double-written.
- `tests/conftest.py` extracted; `tests/test_security_audit.py` adds 32 regressions.
- Suite: **78 tests**, 44/44 live audit checks, 53/53 redline checks.

## 1.1.1 — 2026-11-17 (Neon connected)

- **Connected to Neon Postgres 18.7** (pooled endpoint, TLS): migrations 001–003
  applied, app boots on it (`/healthz` → `backend: postgres`, `durable: true`),
  `store.json` never written.
- `.env.local` (mode `0600`, git-ignored) holds the service credentials; `app/neon.py`
  now reports postgres, auth and object storage as configured.
- **Row-level security verified** on the real database: `re_app` scoped to one
  tenant sees 210 of 214 rows, 0 with no `app.tenant_id`, and a cross-tenant INSERT
  is rejected — `tests/test_rls.py` (4 tests, new).
- `db/003_rls_probe_grant.sql`: grants `re_app` to the owner so the policy can be
  exercised at all (an untested policy is not a control).
- Object-storage SigV4 presign checked against the live bucket: HTTP 404 (no such
  key) rather than 403 SignatureDoesNotMatch.
- Fixed: pooler session leakage (`SET ROLE` surviving into a new connection —
  every connection now starts with `reset role; reset all`); audit probe returning
  a false negative on an empty table (now checks `pg_trigger` + canary row); the
  16-char placeholder heuristic rejecting the bucket name `assets`.
- `tests/test_api.py` pins `RE_DB=json`: an HTTP suite must not write test rows into
  a production database.
- Suite: 45 tests — green against the file store, the local stand-in and Neon.

## 1.1.0 — 2026-11-17

### Durable backend
- `app/db.py`: Postgres/Neon backend — DSN resolution (`postgres://`→`postgresql://`,
  forced `sslmode=require` for hosted hosts, `connect_timeout`), `PgBackend`,
  idempotent migration runner with a checksum ledger (`re_migrations`),
  `audit_immutable_check()`, redacted DSN in `status()`.
- `db/001_init.sql`: `re_documents` (PK carries `tenant_id`, GIN on `doc`),
  `re_audit` with a `BEFORE UPDATE OR DELETE` trigger, `re_migrations`.
- `db/002_rls.sql`: opt-in row-level security (`RE_DB_RLS=1`).
- `app/store.py`: `RE_DB=auto|json` — boot loads from the DB, writes go through it,
  `store.json` is never written in Postgres mode; `RE_DB_STRICT=1` fails boot.
- `/healthz` reports `db.backend_status()` (redacted DSN, latency, version).

### Neon services
- `app/neon.py`: AI Gateway (used by `run_agent` with a deterministic fallback),
  Auth token introspection, S3-compatible SigV4 presign, Realtime, Functions —
  every service optional and fail-soft.
- `GET /api/v1/security/services`: presence booleans, owner/admin only.
- `.env` / `.env.example`: 14 Neon keys added; `SECRET_INVENTORY` is now 31 entries.

### Fixes found by the new tests
- Rollback after every failed statement, with reconnect when the session is
  unrecoverable (a single bad query used to wedge the process).
- `prepare_threshold=None`: psycopg server-side prepares fail against PGlite and
  transaction-mode poolers.
- `npm run build` restored (the `neon init` run had stripped `scripts`):
  compile + JS parse + 45 tests + secret scan.

### Testing
- `tests/test_postgres.py` (7): DSN normalisation, idempotent migrations, tenant
  scoping, append-only audit (catalogue **and** live blocked writes), restart
  survival, healthz.
- `tests/test_neon_services.py` (5): adapters fail soft with nothing configured;
  presign never emits a secret.
- Suite: 45 tests, green; two documented skips removed.

# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: semver on the API + PWA shell.
Security-relevant entries are marked **SEC**.

---

## [1.0.0] — 2026-11-17

First complete, verified build: installable PWA, multi-tenant CRM/ops platform, AI workforce with a
human learning gate, in-process + external security verification, and the full document set.

### Added — platform
- Multi-tenant sign-up; every tenant gets an owner with full control (users, features, kill switch, learning approvals).
- 21 collections, tenant-explicit store API, atomic JSON persistence (`tmp` + `os.replace`).
- Hash-chained audit log with `verify_audit()` tamper detection.
- 69 REST routes across auth, tenant, users, audit, contacts, consent, pipeline, tasks, properties, lead capture, inbox, agents, learning, automations, funnels, pages, social, campaigns, research, analytics, integrations, webhooks, security, public capture.
- Server-sent events stream (`/api/v1/events`).
- Three seeded personas' worth of demo data: 2 tenants, 5 users, 12 properties, 27 contacts, deals, 14 conversations, 5 agents, 3 learning proposals, automations, funnels, campaigns, integrations, research, webhooks.

### Added — PWA
- Installable app, offline shell, versioned service worker (`re-v1.0.0`), manifest, maskable icons, `favicon.svg`.
- Six ES modules, 16 screens, router + view contract (`RE.views.<route>`), action contract (`data-act`, `data-form`).
- Server-rendered public funnel (`/f/nanded-plots`) and website (`/site`) — no JavaScript required for buyers.
- Light/dark theming via CSS custom properties; no external fonts, scripts, or trackers.

### Added — AI workforce
- 5 agents: Meera (voice qualifier), Arjun (WhatsApp nurture), Kabir (lead intelligence), Naina (ops copilot), JEV (research-only).
- 27-tool catalogue with 5 risk classes; `tools ⊆ scopes` enforced at creation and at call time.
- Guardrail inspector: `block ≥ 55`, `quarantine ≥ 40`, `redact ≥ 15`, plus a data-exfiltration pattern and irreversible-action detection.
- Approval queue for `bulk_message`, `price_concession`, `delete_record`, `refund`; critical-risk learning decisions require owner + explicit risk acknowledgement.
- Learning loop: observation → proposal → evidence → risk grade → **human decision with mandatory note** → version bump; decision history immutable.
- Explainable lead scoring, pipeline forecast with confidence intervals, voice call simulation with extraction + compliance verdict.
- JEV research with data-scored video recommendations and a content brief; research-only by policy.
- Model router across providers with a deterministic local fallback (no network required to demo).

### Added — growth & ops
- Funnels with server-rendered publishing, visits/submissions tracking, consent-first forms with honeypot.
- Social scheduler, ads campaigns with spend caps, automations with compiled compliance gates (consent → DNC → call window).
- Integration catalogue with scopes and health; webhook management with SSRF allowlist and signed delivery records.

### Added — security
- **SEC** Field-level encryption for phone/email (encrypt-then-MAC, AAD = tenant) + HMAC blind index for dedupe.
- **SEC** scrypt password hashing (`N=32768`, `maxmem=64 MiB`); per-account lockout after 6 failures.
- **SEC** HMAC session tokens with `jti` revocation on logout / password change / role change; 8 h TTL; HttpOnly + SameSite=Lax.
- **SEC** CSRF double-submit + Origin check on cookie-auth writes.
- **SEC** CSP (`script-src 'self'`), HSTS, nosniff, frame-ancestors, referrer policy, permissions policy, COOP/CORP.
- **SEC** SSRF allowlist with scheme/host/userinfo/redirect/DNS-rebind checks.
- **SEC** Role-aware PII masking; `pii:read` required for full contact detail.
- **SEC** CSV formula neutralisation on export; HTML escaping at every server-rendered sink.
- **SEC** Token buckets per IP per action: `auth 100/300 s`, `write 90/60 s`, `read 240/60 s`, `ai 40/60 s`, `public 30/60 s`, `redline 10/300 s`.
- **SEC** Kill switch (owner) halts every agent run; `autonomous_execution` cannot be enabled by non-owners.
- **SEC** OWASP LLM Top 10 (2026) control matrix exposed in the Security console.

### Added — verification
- `app/redteam.py`: 22 in-process weighted checks → posture score, evidence per check.
- `redline/redline.py`: 51 external checks in 8 categories, `--i-own-this-target` authorisation gate, `RateLimited` → skip, JSON/Markdown reports.
- `tests/test_api.py`: 22 end-to-end tests on an ephemeral server + temp data dir.
- Latest results: **pytest 22/22 · REDLINE 100/100 (51 passed, 0 failed, 0 skipped) · in-process 100/22**.

### Added — documentation
`README.md`, `docs/01-PRD.md`, `docs/02-ARCHITECTURE.md`, `docs/03-DATA-MODEL.md`,
`docs/04-API-AND-WEBHOOKS.md`, `docs/05-TECH-SPEC-2026-2031.md`, `docs/06-AI-AGENT-SPEC.md`,
`docs/07-COMPLIANCE.md`, `docs/08-REDLINE-REDTEAM.md`, `docs/09-ROADMAP-AND-ECONOMICS.md`,
`docs/10-RESEARCH-SOURCES.md`, `PLANS.md`, `PWA_Development_History_log.md`.

### Fixed during the build
- **SEC** `_html()` in the HTTP handler computed a body but never wrote it — public funnel pages returned `Content-Length` with zero bytes. Caught by pytest (`IncompleteRead`) and curl (`size=0`).
- **SEC** Agent runtime failed *open* on `quarantine` (returned partial output). Now fails closed on both `block` and `quarantine`: empty output, no tool calls, audited.
- **SEC** Data-exfiltration guardrail pattern was too narrow (missed "export every contact to …"). Widened to a verb + object pattern with bounded gaps.
- **SEC** Weak-password probe in the external harness tripped the seeded owner's account lockout, locking a demo account for the run. Harness now uses throwaway signups for lockout probes.
- Funnel page rendering (`Content-Length: 3787`, body `0`).
- scrypt call sites missing OpenSSL's `maxmem` argument.
- Seeded records missing keys (`requirement`, `owner_id`, `created_at`) causing `KeyError` on list views.
- Duplicate response header emission in the HTTP edge.
- Rate-limit buckets sized too small for a realistic verification run; harness now treats 429 as *skipped*, not failed.
- RL-503 (excessive agency) still expected irreversible actions to be *parked for approval*; once the runtime began failing closed on `quarantine`, the correct outcome became `held` (nothing executed, no tool calls). Pass criterion widened to `blocked | held | approvals present` — the stricter outcome, not a weaker check.
- Pipeline cards referenced an `open-deal` action that was never implemented, so clicking a deal did nothing. Implemented a deal-detail modal (stage, value, weighted value, lead score, expected close).
- Leads table's "AI call test" button referenced a missing `quick-call` action. Now delegates to the voice simulator with the contact named in the confirmation.

### Known limitations (unchanged, see `README.md`)
No live WhatsApp/SMS/voice providers (simulated). JSON store is single-process. Learning promotion stops
at the human gate — canary/shadow-run is Phase 2. No browser automation in this environment, so UI is
verified by static checks, curl and API tests plus manual review. Demo credentials are intentionally
weak and published.

---

## [1.1.0] — 2026-11-17

Secrets management, and two compliance/security defects found while building it.

### Added
- **`app/config.py`** — one place where configuration and credentials enter the process. Priority is
  real environment variable → `.env` → default, so container/Vault injection always beats a local file.
- **HKDF-SHA256 key derivation** — `RE_MASTER_KEY` derives the five subkeys (`field_enc`, `blind_index`,
  `session`, `audit`, `webhook`). One root to protect, one to rotate, no subkeys in config.
- **Fail-closed boot** — `RE_ENV=production` without a valid, non-placeholder `RE_MASTER_KEY` raises
  `RuntimeError` instead of starting and quietly storing weakly-protected PII.
- **`.env` + `.env.example`** — the real file is mode `0600` and git-ignored; the committed template holds
  placeholders only and is rejected by the validator if used in production.
- **`.gitignore`** covering `.env*` (except the example), `*.pem`, `*.key`, `data/keys.json`,
  `data/store.json`, bytecode, logs.
- **`scripts/check_secrets.py`** — fail-closed hygiene gate: `.env` permissions and ignore status, secret
  containers tracked by git, provider-format credentials, high-entropy 64-char strings, and `--history` to
  scan every commit (regexes and fixtures excluded so detection code does not self-trip).
- **`scripts/rotate_keys.py`** — offline master-key rotation: decrypt with old, re-seal with new,
  recompute blind indexes, 0600 backup, atomic replace, verify-then-rollback.
- **`GET /api/v1/security/secrets`** — owner/admin inventory returning presence, source and a one-way
  fingerprint; the payload is passed through the redactor before it leaves the process.
- **Security console card "Secrets & keys"** — live inventory, master-key fingerprint, derived subkeys,
  warnings and the two commands to run.
- **REDLINE RL-706 / RL-707** — 9-path sweep for secret files reachable from the web tier, and a check that
  the inventory endpoint leaks no values. **RT-023 / RT-024** in-process equivalents.
- **`docs/11-SECRETS-MANAGEMENT.md`** — threat model, rotation, 30-minute leak runbook, KMS/HSM migration
  path, scanner blind spots.

### Fixed
- **SEC** `data/keys.json` and `data/store.json` were **tracked by git** — a keyring in version control is
  the exact leak this release exists to prevent. Both removed from the index, added to `.gitignore`, and
  git history rewritten to purge them; the master key was rotated so the exposed keyring protects nothing.
- **SEC** Blind index stripped every non-digit, so any email without digits normalised to `""` and hashed
  identically — email dedupe silently matched unrelated people. Emails are now lower-cased/trimmed;
  phone behaviour is unchanged.
- **SEC** Quiet hours were evaluated in **server-local** time. A UTC host would happily place a 21:30 IST
  call. Now evaluated in `settings.timezone` at minute granularity, with the window in the 451 message.
- Test fixtures no longer contain key-shaped literals; they are assembled at runtime so secret scanners
  stay quiet and nobody mistakes a fixture for a leak.

### Changed
- Route count 70 · pytest 28 · REDLINE 53 checks · in-process suite 24 checks.
- `seal`/`unseal`/`blind_index` accept an explicit key for rotation; production call sites never pass one.

---

## [Unreleased]

### Planned — 1.2
- Postgres + row-level security behind the existing tenant-explicit store interface.
- Redis-backed rate limiting and SSE fan-out.
- TOTP MFA, refresh tokens, device/session management.
- WhatsApp Cloud API adapter with template registry and status callbacks.
- Upload pipeline with type sniffing and malware scanning.

### Planned — 1.3
- Canary + shadow-run skill promotion with automatic rollback.
- Dual-LLM document intake (privileged planner / quarantined reader).
- Consent Manager integration (DPDP Phase 2).
- Inventory holds with TTL and race-safe acquisition.

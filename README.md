# Realtor Estates — Real Estate Revenue OS

A **multi-tenant, installable PWA** that runs an entire real-estate business under one roof:
CRM, pipeline, omni-channel inbox, AI voice/WhatsApp/SMS agents with human-gated learning,
lawful lead extraction, funnels + public website, social scheduling, paid campaigns,
forecasting, and a security operations console with an offensive verification agent.

> **Status:** working vertical slice — real server, real crypto, real guardrails, seeded demo tenant.
> Not a mock-up: 311 API routes, 602 test functions (666 collected cases), 53 external red-team checks, 37 live proof scripts,
> an adversarial testing agent with a hard policy gate, and no mandatory third-party runtime dependency
> (`psycopg` only when the store runs on Postgres — which the demo now does).

---

## Run it in 30 seconds

```bash
cd realtor-estates
cp .env.example .env                                        # then set RE_MASTER_KEY (see below)
python3 -c "import secrets;print(secrets.token_hex(32))"    # paste it into .env
chmod 600 .env
python3 scripts/check_secrets.py                             # hygiene gate: must print OK
python3 -m app.server --port 8000                           # stdlib only — no pip install, no build step
# open http://localhost:8000
```

**Secrets never live in code.** One root key (`RE_MASTER_KEY`) is HKDF-derived into the field-encryption,
blind-index, session, audit and webhook keys; everything else is optional and blank-friendly. `.env` is
mode `0600` and git-ignored; the repo ships `.env.example` with placeholders only. Production **refuses to
boot** without a valid key. Full policy, rotation runbook and the honest limits of a file-based secret:
**[docs/11-SECRETS-MANAGEMENT.md](docs/11-SECRETS-MANAGEMENT.md)**.

Demo workspace (seeded on first boot):

| Role | Email | Password | What it proves |
|---|---|---|---|
| Owner | `owner@realtorestates.app` | `Demo@12345!` | Full system control: users, flags, kill switch, audit, red team |
| Manager | `manager@realtorestates.app` | `Demo@12345!` | Approves AI learning, sees full PII, cannot own the tenant |
| Agent | `agent@realtorestates.app` | `Demo@12345!` | Works leads; PII masked; cannot change settings |
| Viewer | `viewer@realtorestates.app` | `Demo@12345!` | Read-only, PII masked, every write returns 403 |

Second tenant for isolation testing: `owner@metrorealty.app` / `Metro@12345!`.
Public surfaces: **`/f/nanded-plots`** (funnel) and **`/site`** (brochure website) — both server-rendered, no JS needed.

```bash
python3 -m pytest tests/ -q                 # 447 collected (API, vertical, taxonomy, automations, Postgres, RLS, security, MFA)
python3 scripts/doc_facts.py --check        # fail if any doc quotes a stale count
python3 sentinel/agent.py --self-test       # the testing agent's policy gate refuses destructive actions
npm run build                               # compile + JS parse + tests + secret scan
npm run db:connect                          # switch the store to Postgres/Neon (docs/12 §6)
npm run functions:dev                       # all three Neon Functions, local (docs/16 §3)
npm run photos:proof                        # real upload → variants → rows (docs/15 §6)
npm run sweep:proof                         # plant an orphan → sweep purges it (docs/16 §3)
npm run webhook:proof                       # signed payment event, replayed and forged (docs/16 §3)
python3 redline/redline.py                  # 53-check offensive suite → reports/
python3 scripts/check_secrets.py --history   # fail-closed secret hygiene (CI-friendly)
```

---

## What's in the box

```
realtor-estates/
├── app/                    Python 3.13 · stdlib only
│   ├── server.py           HTTP edge: static, funnel SSR, SSE, headers, cookies
│   ├── api.py              /api/v1 — 311 routes, RBAC, CSRF, tenant isolation, audit
│   ├── store.py            tenant-scoped JSON store + hash-chained audit + demo seed
│   ├── config.py           secret loading (.env → env), HKDF key derivation, redaction, inventory
│   ├── security.py         ChaCha20 field encryption, scrypt, tokens, guardrails, SSRF
│   ├── agents.py           agent runtime, tool catalogue, learning loop, forecasting
│   ├── meters.py           usage meters: derived from rows, ledgered, enforced per plan
│   └── redteam.py          24 in-process verification checks (Security console)
├── web/                    PWA: manifest, service worker, 5 JS modules, one CSS system
├── redline/redline.py      external offensive agent (53 checks, 8 attack categories)
├── sentinel/agent.py       autonomous testing agent: policy gate + 47 checks (DB + frontend)
├── scripts/                check_secrets.py (hygiene gate) · rotate_keys.py (key rotation)
├── .env.example            placeholder template (the real .env is 0600 + git-ignored)
├── tests/test_api.py       pytest end-to-end suite
├── tests/test_postgres.py  database: migrations, tenant scope, append-only audit, restart
├── tests/test_neon_services.py  service adapters fail soft when unconfigured
├── app/db.py               Postgres backend + migration ledger
├── db/001_init.sql, db/002_rls.sql, db/004_runtime_role.sql
├── docs/                   PRD, architecture, tech spec 2026–2031, AI agent spec,
│                           compliance, data model, API, red-team doctrine, roadmap,
│                           research register, secrets management
├── PLANS.md                build plan, workstreams, decision log, risk register
├── changelog.md            semantic changelog
└── PWA_Development_History_log.md   dated engineering journal
```

### The ten screens

1. **Command centre** — KPI cards with sparklines, demand chart, today's ops, funnel, AI fleet, guardrail queue, source economics.
2. **Leads & CRM** — consent-gated records, explainable lead scores, encrypted+masked PII, timeline, compliant extraction.
3. **Pipeline** — kanban with server-recomputed probability.
4. **Unified inbox** — WhatsApp/SMS/voice/web in one thread, AI drafts, quiet-hours and consent gates.
5. **Agent fleet** — scoped agents, MCP tool manifest, dry-run console, prompt-injection test, simulated Marathi call.
6. **Learning queue** — propose → evidence → risk grade → human approve → version bump → rollback.
7. **Automations** — triggers/steps with compliance gates compiled in.
8. **JEV research** — scored video intelligence → content briefs (research-only, cannot publish).
9. **Funnels, website, social, ads, analytics** — with a real forecast model (trend × seasonality, 95% interval, MAPE).
10. **Vertical desk** (`/crm.html`) — real-estate pipeline with stage gates, site visits, speed-to-lead, portal feeds, channel partners, unit inventory, the five-category property catalogue with explained matching, and the **automation runtime** (fleet, playbook install, tick, and what is waiting for a human).
11. **Partner portal** (`/cp.html`) — the channel partner's own door: register a buyer, track the protection window, see earnings.
12. **Security / REDLINE + Owner controls** — posture gauge, guardrail lab, 24-check suite, secret inventory (presence + fingerprint only), kill switch, audit chain, feature flags.

---

## The five design decisions that make this different

**1. Compliance is code, not policy.** Consent, quiet hours (09:00–21:00 IST), DNC scrubbing, AI disclosure and the RERA claim set are enforced in the API. A campaign that would violate them cannot start. `AI disclosure` returns `451 COMPLIANCE_BLOCKED` if a tenant tries to switch it off.

**2. The AI cannot improve itself.** Agents observe their own performance and *propose* skills with evidence — but promotion requires a human with the right role, an explicit risk acknowledgement for high/critical changes, a version bump and a pinned rollback. High-risk auto-publish was rejected in the seeded history, and stays rejected.

**3. Tenant isolation is structural.** `tenant_id` comes from the verified session token — never from a query string, body or path. The store API requires it on every call; the audit log is hash-chained per tenant; the REDLINE suite re-proves IDOR resistance on every run.

**4. Offence is a shipped feature.** REDLINE is an agent with a real tool belt (recon, auth, BOLA/BFLA, injection fuzzing, business-logic abuse, AI attacks, SSRF), scoped to your own deployment. It refuses non-loopback targets without an explicit ownership flag. Run it after every release.

**5. It works on the device in the field.** Server-rendered public pages (no JS), a service worker that never caches tenant data, one CSS file, no external fonts or CDNs, and a layout that survives a 2G connection.

---

## Security posture (verified, not claimed)

| Control | Implementation | Proof |
|---|---|---|
| Field encryption | ChaCha20 + HMAC-SHA256 (encrypt-then-MAC), AAD bound to `tenant_id` | `RT-004`, `RL-201` |
| Passwords | scrypt N=2¹⁵ r=8 p=1, per-user salt, constant-time verify, 6-failure lockout | `RT-006`, `RL-101/105` |
| Sessions | HMAC-signed, 8h TTL, `jti` revocation, role change forces re-auth | `RT-003`, `RL-102/104` |
| CSRF | Double-submit token bound to session + same-origin check | `RL-401/402` |
| Headers | CSP (no inline script), HSTS preload, COOP/COEP/CORP, `no-store` on API | `RL-001…008` |
| Prompt injection | 16-pattern inspector, input **and** output, fail-closed (no output when blocked) | `RT-007`, `RL-501/505` |
| Agency | Tool allowlist ⊆ scopes per agent, irreversible + outbound → human approval | `RT-012/013`, `RL-503/504` |
| SSRF | HTTPS-only allowlist, private ranges blocked, no redirect following | `RT-010`, `RL-601` |
| Audit | Hash-chained, tamper-evident, verified live | `RT-016`, `RL-702` |
| Secrets | One HKDF root → five subkeys; `.env` 0600 + git-ignored; redaction on every boundary; production fails closed without a key | `RT-023/024`, `RL-706/707`, `scripts/check_secrets.py` |
| Call-window correctness | Quiet hours evaluated in the **tenant's** timezone, not the server's | `test_quiet_hours_use_the_tenant_timezone`, `RL-404` |

Full threat model, crypto choices and 5-year horizon: **[docs/05-TECH-SPEC-2026-2031.md](docs/05-TECH-SPEC-2026-2031.md)**.

---

## Reading order

`README.md` → `docs/01-PRD.md` → `docs/02-ARCHITECTURE.md` → `docs/05-TECH-SPEC-2026-2031.md` → `docs/06-AI-AGENT-SPEC.md` → `docs/08-REDLINE-REDTEAM.md` → `docs/07-COMPLIANCE.md` → `docs/03-DATA-MODEL.md` → `docs/04-API-AND-WEBHOOKS.md` → `docs/09-ROADMAP-AND-ECONOMICS.md` → `docs/10-RESEARCH-SOURCES.md` → `docs/11-SECRETS-MANAGEMENT.md` → `docs/12-NEON-BACKEND.md` → `docs/13-SECURITY-AUDIT-2026-11.md` → `docs/14-SECURITY-AUDIT-PASS2-2026-11.md` → `docs/15-NEON-FUNCTION-PROPERTY-PHOTOS.md` → `docs/16-NEON-FUNCTIONS-SCHEDULE-AND-WEBHOOK.md` → `docs/17-SECRET-HANDLING.md` → `docs/18-MARKET-RESEARCH-REAL-ESTATE-CRM.md` → `docs/19-PORTAL-INGESTION.md` → `docs/20-CHANNEL-PARTNERS.md` → `docs/21-INVENTORY-AND-BLOCKING.md` → `docs/22-PROPERTY-TAXONOMY.md` → `docs/23-AUTOMATION-RUNTIME.md` → `docs/24-MONEY.md` → `docs/25-PROPERTY-IMAGES.md` → `docs/26-DEMO-HYGIENE.md` → `docs/27-TWO-FACTOR.md` → `docs/28-SESSIONS.md` → `docs/29-USAGE.md` → `docs/30-KMS-ENVELOPE.md` → `docs/31-SHARED-STATE.md` → `docs/32-POSTGRES-GATE.md` → `docs/33-CREDENTIAL-REQUESTS.md` → `docs/34-STEP-UP.md` → `docs/35-SECURITY-FOLLOWUPS.md` → `docs/36-NEON-CLOUD-PROOFS.md` → `docs/37-STORAGE-AND-REALTIME.md` → `docs/38-CHANNELS-AUTH-AND-VAULT.md` → `docs/39-UNIVERSAL-CONNECTOR-FABRIC.md` → `docs/40-COMPLIANCE-RELIABILITY-AND-BILLING.md` → `docs/41-AI-GOVERNANCE-AND-OBSERVABILITY.md` → `docs/42-FINANCE-CONSENT-MANAGER-AND-A2A.md` → `docs/43-BUYER-PORTAL-INBOUND-VOICE-OFFLINE-PWA-EXCHANGE-AND-DEPLOY.md` → `docs/44-ADS-AUTOPILOT-MACRO-UPLOAD-SECURITY-AND-PORTAL-UX.md` → `docs/45-PHASE4-PLATFORM-AND-VERTICAL-POLISH.md` → `docs/46-GOVT-REVENUE-EREGISTRATION-LOAN-MUTATION-AND-TRUST-PACK.md` → `docs/47-ZERO-TRUST-SECURITY-HARDENING-F01-F10.md` → `docs/48-MCP-OPENAPI-OAUTH2-PKCE-AND-MULTI-LANGUAGE-SDKS.md` → `docs/49-WHITE-LABEL-AGENCY-VISUAL-DAG-CALENDAR-DELIVERABILITY.md` → `docs/50-STATELESS-OTLP-OBSERVABILITY-OIDC-SSO-AND-SA11Y-GATE.md` → `docs/51-ZERO-SECRET-LEAKAGE-PLAYBOOK.md`

---

## Honest limitations (read before demoing to a customer)

- **This is a working slice, not a finished SaaS.** The store runs on Postgres/Neon with RLS, an append-only audit chain, and object storage for listing photos, exports, and call recordings. Automations run (`docs/23`) and real protocol adapters + signed webhooks are implemented (`docs/38`: WhatsApp Cloud API v21.0 + 24 h window, TRAI DLT SMS template registry, voice telephony, and Razorpay `/v1/orders`), reporting `mode: "simulated"` until live carrier/gateway keys are supplied, and there is **no external queue broker**: the tick is in-process, made safe across instances by idempotency keys rather than by a broker.
- **Third-party sends default to simulated until keys are supplied.** WhatsApp (`WHATSAPP_TOKEN`/`WHATSAPP_PHONE_ID`), DLT SMS (`SMS_API_URL`/`SMS_API_KEY`), voice (`VOICE_API_URL`/`SARVAM_API_KEY`), and Razorpay (`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`) run real HTTPS wire calls when configured and cleanly report `"simulated"` when unset so no ₹ is spent by default.
- **The LLM is a deterministic local stub** so the product runs with zero keys and zero data egress. Provider adapters and the residency-aware router are in place; wiring a real model is a config change, not a refactor.
- **Database, Object Storage, Neon Functions, Neon Auth, and Cross-Node Realtime SSE are verified; AI Gateway awaits a key.** Postgres/Neon (`docs/12`), S3 Object Storage (`projectassets`, `docs/37`), all three Neon Functions + both cloud triggers (`ap-southeast-1`, `docs/36`), 7.35 s branch-restore RTO (`docs/36`), cross-instance SSE at 100 concurrent inboxes (`docs/37`), and live Neon Auth (`Better Auth /get-session` + `RE_AUTH_MODE=neon`, `docs/38`) are verified end-to-end. `NEON_AI_GATEWAY_KEY` remains unset.
- **Crypto is production-shaped but not production-hosted:** keys derive from `RE_MASTER_KEY`, which today lives in `.env` (mode 0600). A file can be read by anyone with code execution as the app user — no file-based store is unstealable. The KMS/HSM path, per-tenant DEKs and the migration steps are specified in `docs/11-SECRETS-MANAGEMENT.md §7`, and `config.get()` already prefers a real environment variable over the file, so step 1 needs no code change.
- **Compliance citations are current to November 2026** and India's rules are moving (DPDP Phase II landed 13 Nov 2026; full obligations 13 May 2027). Every threshold is tenant-configurable with a safe default and an audit trail. **Counsel must sign off before commercial launch.**
- **REDLINE is for systems you own.** Non-loopback targets require `--i-own-this-target`; unauthorised use is a criminal offence under India's IT Act §66 and equivalents everywhere else.

<!-- facts:begin -->

| What | Where | Count |
|---|---|---|
| API routes | `app/api.py` | **311** |
| Collections | `app/store.py` | **33** |
| Test functions | `tests/` (54 files) | **654** |
| Test cases collected | `pytest --collect-only` | **0** |
| Proof scripts | `scripts/proof*` (py + mjs) | **37** |
| Neon Functions | `functions/*.ts` | **3** |
| SQL migrations | `db/*.sql` | **8** |
| Documents | `docs/*.md` | **51** |
| App modules | `app/*.py` | **40** |
| JS modules | `web/js/*.js` | **9** |

<!-- generated by scripts/doc_facts.py — do not edit by hand -->
<!-- facts:end -->

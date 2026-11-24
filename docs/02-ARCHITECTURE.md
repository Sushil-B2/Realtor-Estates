# 02 — Architecture

## 1. Principles

1. **Single-tenant-per-request thinking.** Every request is answered *inside* one tenant's world. Cross-tenant reads are not "restricted" — they are unrepresentable: the store API requires a `tenant_id` on every call and the value comes from the session.
2. **The UI is not a security boundary.** Every rule that matters (roles, consent, quiet hours, scopes, approvals) is enforced where the data lives.
3. **Boring where it counts, clever where it pays.** JSON store and hand-rolled SVG are boring; guardrail pipelines and the learning loop are where the product is differentiated.
4. **No dependency you cannot audit.** Zero third-party runtime packages in the app tier. When Postgres and Redis arrive, they arrive pinned, signed and with an SBOM.
5. **Every external thing is behind an adapter** so it can be swapped, disabled or killed.

## 2. Runtime topology (as built)

```
                       ┌─────────────────────────────────────────────┐
  Browser (PWA) ──────▶│  ThreadingHTTPServer  (0.0.0.0:8000)        │
  Buyer (no JS)  ──────▶│                                             │
                       │  ├── static  web/*  (traversal-guarded)    │
                       │  ├── SSR     /f/:slug, /site               │
                       │  ├── SSE     /api/v1/events                │
                       │  └── JSON    /api/v1/*  ──▶ api.handle()   │
                       │                                             │
                       │        ┌──────────────────────────────┐     │
                       │        │ session → tenant → role →    │     │
                       │        │ CSRF → rate limit → handler  │     │
                       │        └───────────┬──────────────────┘     │
                       │                    │                         │
                       │   ┌────────────────┼─────────────────┐      │
                       │   ▼                ▼                 ▼      │
                       │ security.py     agents.py          store.py │
                       │ crypto          runtime/tools      JSON +   │
                       │ tokens          forecast           audit    │
                       │ guardrails      learning                    │
                       │   └────────────────┼─────────────────┘      │
                       │                    ▼                         │
                       │              redteam.py (24 checks)          │
                       └─────────────────────────────────────────────┘
```

**Threading model:** one thread per connection (bounded by the OS); the store uses a single `RLock` so writes are serialised and atomic (`tmp` + `os.replace`). This is deliberate: correctness first, throughput later. The lock is per-process, which is exactly why the JSON store cannot serve two processes — documented as the first thing to replace.

## 3. Request lifecycle

```
1. Parse path/query, enforce body limits (1 MB), Content-Type must be application/json for bodies
2. Resolve route (regex table, 71 routes) — 404 early, no reflection
3. Rate limit by (bucket, client_ip) → 429 + Retry-After
4. Authenticate: Bearer header or re_session cookie → HMAC verify → exp → jti revocation
5. Load user + tenant; reject if inactive; enforce minimum role for the route
6. For cookie-auth writes: CSRF double-submit + Origin/Host match
7. Handler: validate input (allowlist), apply domain rules, mutate store
8. Audit append (hash chain) before responding
9. Response: JSON with security headers, no-store; errors return {code, message, trace_id}
```

Steps 3–6 live in one function (`api.handle`) so a new endpoint cannot accidentally skip them.
That is the single most important structural decision in the codebase.

## 4. Domain modules

| Module | Responsibility | Key invariants |
|---|---|---|
| **Identity** | tenants, users, roles, sessions | role rank checked server-side; role change ⇒ re-auth |
| **CRM** | contacts, deals, pipeline, tasks, activities | PII encrypted; masked by role; dedupe by blind index; stage cascade |
| **Consent** | consent ledger per contact | no consent ⇒ no outbound (422); withdrawal honoured instantly |
| **Inventory** (phase 2) | properties, plots, holds with TTL | optimistic concurrency; expiry alerts (hourly job) |
| **Conversation** | threads, messages, channels | consent + quiet hours + template approval before send |
| **Agents** | definitions, scopes, tools, runs, approvals | manifest ⊆ scopes; irreversible ⇒ approval; fail-closed |
| **Learning** | proposals, evidence, risk, decisions | human gate; version bump; critical ⇒ owner |
| **Growth** | funnels, pages, social, campaigns, research | publish human-gated; budgets guarded; research≠publish |
| **Analytics** | series, funnel, forecast, insights | explainable; intervals always present |
| **Platform** | integrations, webhooks, audit, security | SSRF allowlist; signed deliveries; live posture |

## 5. Migration path to production infrastructure

| Today | Target | Migration mechanic |
|---|---|---|
| `store.py` JSON | Postgres 17 + RLS | one table per collection; `tenant_id NOT NULL`; RLS policy `USING (tenant_id = current_setting('app.tenant')::uuid)`; store interface already takes `tenant_id` explicitly |
| In-process rate limits | Redis token bucket | same `RateLimiter.check()` signature |
| Audit in JSON | `audit_log` partitioned + hash trigger | chain fields identical; add WORM export to object lock |
| Field crypto in-process | KMS envelope | `seal`/`unseal` signatures unchanged; add `v2:` ciphertext prefix |
| SSE from memory | Redis pub/sub → SSE/WebSocket | event shape identical |
| Simulated transports | WhatsApp Cloud API, SMS gateway, voice provider | adapters already isolated in `agents.PROVIDERS` + integration catalogue |
| Local stub model | provider router | `route_model()` decides; call site unchanged |
| Single process | API pods + worker pool | job contract: `{id, type, payload, idempotency_key}` |

## 6. Scaling notes

- **Reads:** dashboard aggregates are cheap now; in Postgres they become materialised views refreshed every 5 min plus an on-demand path.
- **Writes:** the hot path is messages and activities → partition by month, index `(tenant_id, created_at desc)`.
- **Agent fan-out:** model calls are the expensive resource; per-tenant concurrency caps + queue with priority (inbound reply > outbound campaign).
- **Multi-region:** India primary; GCC secondary for NRI-heavy tenants; residency router already keys off tenant region.

## 7. Failure modes and degradation ladder

| Failure | Behaviour |
|---|---|
| Model provider 5xx | Deterministic template replies; agent marked degraded; alert |
| Voice provider down | Call attempts queue; WhatsApp fallback offered; no silent drop |
| Store write fails | 500 with trace id; no partial audit; client retries with idempotency key (phase 2) |
| Rate limiter saturates | 429 with `Retry-After`; UI backs off and shows a banner |
| Kill switch engaged | All agent runs return `blocked`; outbound sends refused; UI shows a red banner |
| Offline client | Shell loads from cache; API calls fail fast with an explicit message; public pages served from cache |

## 8. Security architecture boundaries

| Boundary | Trust | Controls |
|---|---|---|
| Browser ↔ API | Untrusted | TLS, CSP, CSRF, session token, input validation, rate limits |
| Buyer ↔ public pages | Untrusted | Escaped SSR, honeypot, per-IP limits, consent enforcement server-side |
| API ↔ store | Trusted-ish | tenant parameter mandatory, encryption at rest, audit |
| Agent ↔ tools | Untrusted content, trusted channel | allowlist, scopes, approvals, result size caps |
| Tenant ↔ tenant | Hard boundary | session-derived tenancy + blind index + per-tenant keys (target) |
| Platform ↔ third parties | Untrusted | egress allowlist, signed payloads, no redirects, no metadata endpoints |

## 9. Code map for reviewers

```
app/server.py     431 lines   HTTP edge — read this first
app/api.py       3465 lines   routes + handlers; scan for @route decorators (118)
app/taxonomy.py   857 lines   five property categories, units, price bases, requirement scoring
app/store.py     1320 lines   tenant-scoped store, audit chain, demo seed
app/db.py         770 lines   Postgres backend, migration ledger, RLS plumbing
app/security.py   606 lines   crypto, tokens, guardrails, SSRF, escaping
app/ingest.py     496 lines   portal adapters, dedupe, SLA policy
app/agents.py     459 lines   tool catalogue, inspector wiring, forecast, voice sim
app/vertical.py   432 lines   stage gates, visits, matching, commission rules
app/config.py     343 lines   secret loading, HKDF derivation, redaction, inventory
app/redteam.py    342 lines   24 in-process checks
app/inventory.py  280 lines   unit statuses, grid generation, timed holds
app/neon.py       241 lines   AI Gateway / Auth / Realtime / Storage adapters
app/keysetup.py   216 lines   operator key intake (/keysetup.html)
app/partners.py   181 lines   channel-partner policy: protection window, pct clamp, earnings
redline/redline.py            external offensive harness (53 checks)
sentinel/agent.py             autonomous testing agent with a policy gate (47 checks)
web/js/*.js       7 modules  core, ops, ai, growth, platform, crm, cp
functions/*.ts     3 Neon Functions: photo pipeline, compliance sweep, payments webhook
```

The five `app/*.py` modules added by the vertical work (`vertical`, `ingest`, `partners`,
`inventory`, `taxonomy`) are **pure rule modules**: they take data in and return decisions, and hold
no I/O. That is why the 40-odd rules they encode are testable without a server, and why the API
handlers stay thin enough to audit.

Start at `app/server.py` → `api.handle` → any handler → `security.py`. Everything else is composition.

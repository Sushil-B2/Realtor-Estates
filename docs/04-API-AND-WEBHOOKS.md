# 04 — API & Webhooks

Base: `/api/v1`. 311 routes. JSON in, JSON out. All tenant-scoped routes are authorised from the
session, never from the payload.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Auth | `Authorization: Bearer <token>` **or** `re_session` cookie (HttpOnly, SameSite=Lax, Max-Age 28800) |
| CSRF | Cookie-auth writes require `X-CSRF-Token` = the `csrf` value returned by login, plus a same-origin `Origin` |
| Content type | Bodies must be `application/json`; anything else → 415 |
| Body limit | 1 MB → 413 |
| Errors | `{"error": {"code","message","details":[],"trace_id"}}` — no stack traces, no field names from the DB |
| Errors are typed | `VALIDATION_FAILED`, `UNAUTHENTICATED`, `FORBIDDEN_SCOPE`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`, `BLOCKED_BY_POLICY`, `UPSTREAM_UNAVAILABLE`, `UNSUPPORTED_SOURCE` |
| Pagination | `?limit=` (1–500), `?cursor=` where lists are unbounded |
| Caching | Tenant data: `Cache-Control: no-store`. Public pages: `max-age=60, stale-while-revalidate=300` |
| Audit | Every mutating route appends to the hash-chained audit log before responding |
| Idempotency | Send endpoints accept `idempotency_key`; replays return the original result (phase 2 enforced store-side) |

## 2. Auth

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/signup` | Creates tenant + owner (rate limit `auth`). Email uniqueness is per tenant |
| `POST` | `/auth/login` | Returns `{token, csrf, user, tenant}`; 6 failed attempts per account ⇒ locked; generic error text either way. **An enrolled account gets no session and no cookie**: `{mfa_required: true, challenge, methods, expires_in}` (`docs/27`) |
| `POST` | `/auth/login/mfa` | Second step: `{challenge, code} \| {challenge, recovery_code}` → `{token, csrf, user, mfa, tenant}`. A challenge is not a session — every session door refuses one with `401 MFA_CHALLENGE_NOT_A_SESSION` |
| `GET` | `/auth/mfa` | `{enrolled, asked, required, pending, recovery_codes_left, enrolled_at}` — what is *true*, not what was configured |
| `POST` | `/auth/mfa/enrol` | `{secret, otpauth_uri, recovery_codes[10]}`; switches nothing on until a code verifies |
| `POST` | `/auth/mfa/verify` | `{code}` — the only route that can set `mfa_enrolled` |
| `POST` | `/auth/mfa/disable` | `{password}` **and** (`code` \| `recovery_code`): a stolen cookie cannot remove the factor |
| `POST` | `/auth/mfa/recovery-codes` | `{password}` + a factor → ten fresh codes; the previous ten are void |
| `POST` | `/auth/refresh` | `{token, csrf, expires_in, session}` from the `re_refresh` cookie (or `X-Refresh-Token`). **Single-use**: it rotates, and a *superseded* token ends the session with `401 REFRESH_REUSED` |
| `GET` | `/auth/sessions` | The device list: one row per sign-in, this one marked, addresses masked, no credential in the payload (`docs/28`) |
| `DELETE` | `/auth/sessions/{sid}` | End one device; its token fails on the very next request. Another user's id is `404`, not `403` |
| `POST` | `/auth/sessions/revoke-others` | Keep this device, end the rest |
| `POST` | `/demo/sessions/sweep` | Delete ended session rows (revoked or past expiry); live sessions are nobody else's to tidy |
| `POST` | `/auth/logout` | Ends the session row, revokes `jti`, clears **both** cookies |
| `GET` | `/auth/me` | Identity, role, scopes, `mfa` state, tenant features |

```bash
curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"owner@realtorestates.app","password":"Demo@12345!"}'
```

## 3. Tenant & users

| Method | Path | Role | Notes |
|---|---|---|---|
| `GET` | `/tenant` | any | tenant, settings, features, kill-switch state |
| `PATCH` | `/tenant` | manager+ | name, timezone, quiet hours, currency, disclosure |
| `PATCH` | `/tenant/features` | manager+ | feature flags; `autonomous_execution` is **owner-only** |
| `POST` | `/tenant/kill-switch` | owner | `{"on": true}` halts all agent execution |
| `GET` | `/tenant/users` | manager+ | |
| `POST` | `/tenant/users` | manager+ | invite; cannot grant `owner` |
| `PATCH` | `/tenant/users/{uid}` | manager+ | role/active; cannot modify an owner unless you are one; role change forces re-auth |
| `GET` | `/tenant/audit` | manager+ | paginated chain with `hash`/`prev_hash` |

## 4. CRM

| Method | Path | Notes |
|---|---|---|
| `GET` | `/contacts` | filters: `stage`, `q`, `source`, `owner_id`; PII masked without `pii:read` |
| `POST` | `/contacts` | dedupe by blind index → 409 `CONFLICT`; unconsented imports are rejected |
| `GET` `PATCH` | `/contacts/{cid}` | `full=true` honours `pii:read` |
| `POST` | `/contacts/{cid}/consent` | `{status, purpose, channel, notice_version, evidence}` → ledger |
| `POST` | `/contacts/{cid}/note` | appends an activity |
| `GET` | `/pipeline` | deals grouped by stage + totals |
| `PATCH` | `/deals/{did}` | stage/value/probability; stage moves write activity + event |
| `GET` `POST` | `/tasks` · `PATCH /tasks/{tid}` | |
| `GET` | `/properties` | inventory with docs + RERA ids |

## 5. Lead capture

| Method | Path | Notes |
|---|---|---|
| `POST` | `/leads/scrape` | `{"source": "...", "query": "..."}`. LinkedIn and direct portal scraping are **blocked (451 `UNSUPPORTED_SOURCE`)**; allowed sources are documented and lawful |
| `GET` | `/leads/jobs` | job history with counts and consent status |
| `POST` | `/leads/import` | CSV/text import; consent column required; formula-injection-safe |
| `GET` | `/leads/scoring` | explainable score with factors |

## 6. Conversations

| Method | Path | Notes |
|---|---|---|
| `GET` | `/inbox` | threads with unread counts |
| `GET` | `/inbox/{cid}` | messages; masked if no `pii:read` |
| `POST` | `/inbox/{cid}/send` | `{channel, text, idempotency_key}`. Refused with 422 if consent ≠ granted, outside the call window, or the guardrail blocks |
| `POST` | `/inbox/{cid}/ai-draft` | draft only — never sends |

## 7. Agents

| Method | Path | Notes |
|---|---|---|
| `GET` `POST` | `/agents` | create requires `tools ⊆ scopes`; unknown tools 400 |
| `PATCH` | `/agents/{aid}` | status/scopes/tools; scope widening is audited |
| `GET` | `/agents/{aid}/tools` | resolved catalogue with risk class per tool |
| `POST` | `/agents/{aid}/run` | `{input, context}` → `{status, output, tool_calls, guardrail, cost_usd}`; statuses `ok\|partial\|blocked\|held` |
| `POST` | `/agents/{aid}/voice-test` | simulated call: disclosure, extraction, compliance verdict |
| `POST` | `/agents/{aid}/propose` | learning proposal from observations |

## 8. Learning gate

| Method | Path | Notes |
|---|---|---|
| `GET` | `/learning` | proposals with evidence, risk, status history |
| `POST` | `/learning/{pid}/decide` | `{decision: approve\|reject, note, acknowledge_risk}`. `note` is mandatory. `critical` risk requires `owner` + `acknowledge_risk: true`. Approving bumps the agent's skill version |

## 8a. The real-estate vertical (v1.6.0 → v1.10.0)

Four passes of `docs/18` §8, each with a doc and a proof script. Route groups, all tenant-scoped
unless marked public.

**Property taxonomy (docs/22)**

| Method | Path | Notes |
|---|---|---|
| `GET` | `/taxonomy` | Five categories → types → field metadata, area units, refused units, price bases. The desk UI generates its forms from this; nothing is hard-coded client-side |
| `POST` | `/properties` | Validated create. A field that belongs to another category → `422 PROPERTY_INVALID`; `bigha` → `422 AREA_UNIT_REFUSED` |
| `POST` | `/properties/search` | Ranked 0–100 with a reason per component. `category`/`types`/`city`/`attributes` are hard filters; budget and area are scored |
| `POST` | `/contacts/{id}/requirement` | Structured requirement persisted on the contact + the shortlist it buys |

**Site visits & matching (docs/18 P0)**

| Method | Path | Notes |
|---|---|---|
| `GET`/`POST` | `/site-visits` | Conflict detection is half-open on `[scheduled_at, +duration)`; a clash is `409` with the conflicting visit |
| `PATCH` | `/site-visits/{id}` | Lifecycle: scheduled → confirmed → visited / revisit / dropped / no-show |
| `GET` | `/contacts/{id}/matches` | Explainable requirement → property ranking |

**Portal ingestion & speed-to-lead (docs/19)**

| Method | Path | Notes |
|---|---|---|
| `POST` | `/ingest/{source}` | **Public**, authenticated by `X-Ingest-Key` (`rink_…`), seven adapters, dedupe on the phone blind index with source attribution preserved |
| `GET` | `/leads/sla` | Per-lead clock against the source's window (300 s portals → 3600 s web) |
| `POST`/`GET`/`DELETE` | `/tenant/ingest-keys` | Issue (shown once), list, revoke |

**Channel partners (docs/20)**

| Method | Path | Notes |
|---|---|---|
| `GET` | `/cp/me`, `/cp/leads`, `/cp/visits`, `/cp/earnings` | Partner-scoped views; `_require_partner()` gates them (a partner's role rank is 0) |
| `GET` | `/properties/{id}/media` | Listing photos: rows, plus photos grouped by stem, the cover variant, the publish floor and a placeholder tile (docs/25) |
| `GET` | `/demo/hygiene` | Owner/admin: what rows came from a proof run, with `legacy_prefix` / `legacy_since` previews (docs/26) |
| `POST` | `/demo/proof-rows/purge` | Owner only, slug typed in the body: delete stamped rows, cascade down, never orphan a survivor |
| `GET` | `/properties/covers?ids=…` | Covers for up to 200 properties in one read; per id `ok` / `none` / `unavailable` / `not_found` |
| `GET` | `/cp/inventory`, `/cp/inventory/{id}` | What a partner may sell: free stock only, seller and buyers stripped, no invented commission rate (docs/20 §6b) |
| `POST` | `/cp/register` | Claims a buyer; 90-day protection default (1–365) |
| `GET`/`POST`/`PATCH` | `/cp/partners`, `/cp/registrations`, `/cp/registrations/{id}/decide` | Approval binds the firm to pay — manager minimum |
| `GET`/`POST` | `/cp/payouts`, `/cp/payouts/{deal_id}/pay` | Owner/admin only; amount is the partner % of **gross commission**, never of deal value |

**Unit inventory (docs/21)**

| Method | Path | Notes |
|---|---|---|
| `GET`/`POST` | `/properties/{id}/units`, `/properties/{id}/units/bulk` | Deterministic floor-major grid; re-runs skip existing numbers |
| `GET`/`PATCH` | `/units`, `/units/{id}` | Ledger view; a status PATCH cannot reach `booked`/`sold` (`USE_THE_DEAL`) |
| `POST` | `/units/{id}/block`, `/release`, `/book` | 48 h default hold (max 720), self-releasing by derivation; a second hold is `409` naming the holder |
| `GET` | `/units/audit` | Unit history across the tenant |

**Automation runtime (docs/23)**

| Method | Path | Role | Notes |
|---|---|---|---|
| `GET` | `/automations` | any | Fleet with typed `step_meta`, trigger labels and per-automation run stats; also returns the trigger/step vocabulary |
| `POST` | `/automations` | write | Steps are validated on save: unknown type → `422 UNKNOWN_STEP_TYPE`, missing `text`/`title`/`stage` → `422 STEP_INCOMPLETE`, unknown trigger → `422 UNKNOWN_TRIGGER` |
| `PATCH` | `/automations/{id}` | write | Enable/pause, rename, re-trigger, replace steps |
| `GET` | `/automations/playbooks` | any | The seven researched playbooks, each with its `why` and whether it is installed |
| `POST` | `/automations/playbooks/{slug}/install` | write | Idempotent — re-installing returns `already_installed: true` |
| `POST` | `/automations/tick` | manager+ | One tick, `{dry_run, limit}`. A tick sends messages, hence the role gate |
| `POST` | `/automations/{id}/run` | manager+ | Fire one automation for the subjects its trigger currently matches |
| `GET` | `/automations/queue` | any | Counts by status, `due_now`, dead/deferred/blocked lists, caps |
| `GET` | `/automations/runs` | any | Run history with the last four trace entries; `?status=dead` etc. |

**Money (docs/24)**

| Method | Path | Role | Notes |
|---|---|---|---|
| `POST` | `/webhooks/payments` | public, HMAC | Razorpay-shaped event. The signature is over the **raw bytes** (`X-Razorpay-Signature`, current or previous secret); a bad signature is a 401 that writes nothing. Answers 200 for a duplicate so the provider stops retrying |
| `POST` | `/payments/reconcile` | manager+ | Pulls events the payments Neon Function verified into `re_webhook_events` and applies them. The production path; idempotent on `payment_id` |
| `POST` | `/payments/orders` | write | Creates a payment intent (a "payment link" without the provider call): the amount, deal, unit and kind the provider will echo back in `notes` |
| `GET` | `/payments` | any | Payments and intents, newest first, `?kind=token` |
| `POST` | `/deals/{id}/invoice` | write | Issues the brokerage's tax invoice: SAC 997221, 18% split CGST/SGST vs IGST by the client's state, TDS 194-H noted, net receivable stated |
| `GET` | `/invoices` · `/invoices/{id}` | any | Documents; `?type=receipt` for booking receipts |
| `GET` | `/money/summary` | any | Collected, token held, invoiced, GST, TDS, receivable — every figure summed from rows |

**Known absent (deliberate, tracked in `PLANS.md` §9):** no payment collection route (a token is recorded,
not received), no invoice/GST route, no multi-day `wait` step in a playbook, and the transports behind
`send_whatsapp`/`send_sms`/`voice_call` are simulated — the gates and the record are real, the delivery
receipt is not.

## 9. Growth

| Method | Path | Notes |
|---|---|---|
| `GET` `POST` `PATCH` | `/funnels` | publish is human-gated (manager+) and audited |
| `GET` `PATCH` | `/pages` | website pages |
| `GET` `POST` `PATCH` | `/social` | schedule; publish requires approval |
| `GET` `POST` `PATCH` | `/campaigns` | budget caps enforced against `spend_cap_usd` |
| `GET` `POST` | `/research` | JEV run → scored videos + brief; research-only |
| `GET` `POST` `PATCH` | `/automations` | step graphs with compiled compliance gates |

## 10. Analytics

| Method | Path | Returns |
|---|---|---|
| `GET` | `/analytics/overview` | KPIs, series, funnel, channel mix, agent usage |
| `GET` | `/analytics/forecast` | projection with confidence interval + assumptions |
| `GET` | `/analytics/insights` | ranked insights, each with the evidence that produced it |

## 11. Integrations, webhooks, security

| Method | Path | Notes |
|---|---|---|
| `GET` | `/integrations` | catalogue with scopes and health |
| `POST` | `/integrations/{slug}/connect` · `/disconnect` | never returns tokens; writes an audit entry |
| `GET` `POST` | `/webhooks` | URL must pass the SSRF allowlist; response includes `secret_hint`, never the secret |
| `GET` | `/security/posture` | posture score, control matrix, OWASP LLM mapping |
| `POST` | `/security/inspect` | run the guardrail inspector on text (billed to the `ai` bucket) |
| `POST` | `/security/redline` | in-process 24-check suite, owner/admin, `redline` bucket |
| `GET` | `/security/secrets` | owner/admin secret inventory: presence, source, one-way fingerprint — never a value |

## 12. Public (unauthenticated)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/public/funnel/{slug}/submit` | form capture; honeypot; per-IP `public` bucket; writes consent ledger; returns only a reference id |
| `GET` | `/f/{slug}` (HTML) | server-rendered funnel, escaped, AI-friendly semantics |
| `GET` | `/site` (HTML) | tenant website |
| `GET` | `/api/v1/events` (SSE) | live activity stream for the app shell |

## 13. Rate limits (token buckets, per IP + bucket)

| Bucket | Budget | Applies to |
|---|---|---|
| `auth` | 100 / 300 s | signup, login |
| `write` | 90 / 60 s | all mutating tenant routes |
| `read` | 240 / 60 s | GET routes |
| `ai` | 40 / 60 s | agent runs, drafts, inspect |
| `public` | 30 / 60 s | public submissions |
| `redline` | 10 / 300 s | in-process red-team runs |

429 responses carry `Retry-After`. Consumers must back off; a well-behaved client never sees a 429 in normal use.

## 14. Webhooks

### 14.1 Events
`contact.created` · `contact.consent_changed` · `deal.stage_changed` · `appointment.confirmed` ·
`message.sent` · `message.failed` · `agent.run_blocked` · `learning.proposal_decided` ·
`redline.run_completed` · `integration.health_changed`

Payload envelope:

```json
{
  "id": "evt_9f2c1a4b7d",
  "type": "deal.stage_changed",
  "tenant": "sunrise",
  "at": 1794928977,
  "data": { "deal_id": "deal_72d6d9375c", "from": "site_visit", "to": "negotiation" },
  "attempt": 1
}
```

No PII in the envelope: `data` carries ids, never phone numbers or emails. Consumers fetch details
through the API with their own credentials. This is deliberate — a webhook to a partner must not be a
PII leak channel.

### 14.2 Delivery
- Signed with `X-RE-Signature: t=<unix>,v1=<hex hmac>` over `<t>.<raw body>`, HMAC-SHA256 with the endpoint secret.
- Consumers must reject timestamps older than 5 minutes (replay defence).
- Retries: 1 m, 5 m, 30 m, 2 h, 6 h, 24 h — then the endpoint is marked `degraded` and the owner is notified.
- 10 consecutive failures ⇒ auto-disable + audit entry.
- Delivery log keeps status code, latency and attempt count — never the request body.

### 14.3 URL policy (SSRF)
Allowlisted hosts only, HTTPS only, no IP literals, no userinfo, no redirects followed, DNS resolved
and re-checked before connect, loopback/RFC1918/IMDS/link-local rejected. Failure ⇒ 400
`BLOCKED_BY_POLICY` with the reason (no internal address echoed back).

### 14.4 Inbound webhooks (phase 2)
`/api/v1/inbound/{provider}` with per-provider signature verification (WhatsApp `X-Hub-Signature-256`,
Stripe-Style `t/v1`), replay window, body-hash dedupe, and constant-time comparison.

## 15. Versioning & deprecation

- Breaking change ⇒ `/api/v2`; additive changes ship in place.
- Deprecation: 90 days notice via `Sunset` + `Deprecation` headers, changelog entry, and a tenant-wide banner.
- Every response carries `X-RE-Request-Id` for support and log correlation.

## 16. SDK surface (thin clients)

`RE.views.*` in the PWA calls exactly these endpoints. There is no private back-channel: anything the UI
can do, a script with the same credentials can do — and can be tested by REDLINE. That symmetry is what
makes the harness meaningful.

## 17. Government, Revenue (`7/12`), `e-Registration`, Loan/CLP & `E-Ferfar` (`v1.35.0`, `docs/46`)

- `GET|PUT /api/v1/properties/{pid}/revenue-vault` — Maharashtra `7/12` (30-day freshness), `8A`, Property Card, Collector NA, `Mojni`, 30-yr Title Search, Class-II & *Boja* gates.
- `POST /api/v1/govt/cost-sheet` — Ready Reckoner (ASR) floor, Stamp Duty zones, `1%` Women Concession, `₹30k`-capped Registration Fee, GST & `§194-IA` `1%` TDS (`>= ₹50L`).
- `GET|PUT /api/v1/deals/{did}/registration` & `POST /api/v1/deals/{did}/registration/index-ii` — IGR `e-Registration` / `iSarita` SRO desk, GRAS challan, `Form 26QB` gate & `Index-II` completion.
- `GET|POST /api/v1/properties/{pid}/bank-apf`, `PUT /api/v1/deals/{did}/home-loan`, `POST /api/v1/deals/{did}/clp/init` & `POST /api/v1/deals/{did}/clp/demand` — Bank APF registry, home-loan tracker & Architect-certified RERA CLP demands.
- `PUT /api/v1/deals/{did}/handover` & `GET /api/v1/properties/{pid}/marketing-trust-pack` — `E-Ferfar` Talathi mutation + possession handover (surfaced on `#/buyer-portal/{token}`) and Govt.-verified marketing trust badges + SSR funnel cost sheet (`/f/{slug}`).


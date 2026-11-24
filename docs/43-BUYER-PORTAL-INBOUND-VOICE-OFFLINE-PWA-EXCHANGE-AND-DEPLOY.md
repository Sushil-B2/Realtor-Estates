# 43 · Buyer & NRI Self-Serve Portal, Inbound Voice Receptionist, Offline PWA Sync, Signed Pack Exchange & Deployment Kit (`v1.32.0`)

`v1.32.0` implements all five owner-approved Phase 3 & Post-PRD expansion tracks (`Options A–E`) in a single stdlib-only, strict-CSP, zero-regression release.

---

## 1. Option A — Buyer & NRI Self-Serve Portal + FEMA Eligibility Engine

### FEMA (Non-Debt Instruments) Rules 2019 Rule 24 (`POST /api/v1/fema/check`)
- **Prohibited NRI / OCI Purchase (`PROHIBITED`):** Blocks direct purchase of `agricultural`, `farmhouse`, or `plantation` properties (`FEMA_NDI_RULE24_PROHIBITED`), preventing unlawful earnest-money intake.
- **Inheritance Exception (`ELIGIBLE_INHERITANCE_ONLY`):** Permits inheritance from an eligible resident under Rule 24(b) with non-repatriable NRO sale proceeds.
- **NA Conversion Gate (`HOLD_NA_VERIFICATION`):** Holds NRI/OCI plot bookings until the Collector Non-Agricultural (NA) conversion order is verified.
- **Repatriation Cap (`ELIGIBLE` + `repatriation_cap_reached`):** Enforces the 2-residential-property sale-proceed repatriation ceiling across NRE / FCNR(B) / NRO inward remittance channels (forbidding cash / foreign credit card settlement).
- **Foreign Citizens (`RBI_APPROVAL_REQUIRED`):** Requires prior Reserve Bank of India approval for non-OCI foreign nationals.

### Deal-Scoped Buyer Portal Passes (`bp1_...`)
- `POST /api/v1/deals/{did}/buyer-portal` mints an HMAC-SHA256 signed `bp1_...` pass (`ttl_days` 1–90). Only `pass_jti` is persisted on `deal.buyer_portal` (zero raw credential storage).
- `GET /api/v1/public/buyer-portal/{token}` resolves a strict deal-scoped self-serve view (first name, unit carpet/all-in breakdown, MahaRERA registration & verify link, official receipts, payment links, site visits, and FEMA checklist) with zero cross-deal or cross-tenant leakage.
- `DELETE /api/v1/deals/{did}/buyer-portal` revokes the pass immediately (`401 REVOKED_PORTAL_PASS`).

---

## 2. Option B — Phase 3 Inbound AI Voice Receptionist & Diarised Sentiment Trajectory

- `POST /api/v1/voice/inbound` (operator/twin) and `POST /api/v1/webhooks/voice-inbound` (`VOICE_WEBHOOK_SECRET` HMAC-SHA256 verified) handle inbound calls in Marathi (`mr`), Hindi (`hi`), and English (`en`).
- **Turn-0 Mandatory AI Disclosure (`D10`):** Prepends the language-matched AI disclosure turn if absent.
- **Speaker Diarisation & Sentiment Trajectory:** Scores each caller turn across `[−1.0, +1.0]`, computing `opening_score`, `closing_score`, `min_score`, and `delta`.
- **Automatic Human Escalation:** Triggers `escalated_to_human = true` and creates an urgent 15-minute high-priority callback task whenever `min_score <= -0.35` or the caller requests a human manager.

---

## 3. Option C — PWA Field-Agent Offline Sync & Web Push Registry

- **SSRF-Hardened Web Push (`GET/POST/DELETE /api/v1/push/...`):** Validates W3C HTTPS push service hosts and runs `sec.ssrf_check` (blocking loopback/RFC1918/metadata targets) while sealing `p256dh` + `auth` keys under `sec.seal(..., aad=tid)`.
- **Idempotent Offline Mutation Replay (`POST /api/v1/offline/sync` + `RE.offlineOutbox`):** Deduplicates field-agent offline mutations (`note.add`, `visit.outcome`, `task.complete`) via per-tenant `client_mutation_id` ledger and auto-flushes `re_offline_outbox` on browser `online` events.

---

## 4. Option D — Cryptographically Signed Skill & Funnel Pack Exchange

- `POST /api/v1/exchange/export` emits a `realtor-estates.template-pack.v1` JSON manifest signed with `sig1_...` HMAC-SHA256 after scanning every funnel and skill through `sec.inspect_text` and `aigov.inspect_rera_advertising`.
- `POST /api/v1/exchange/import` verifies the HMAC signature in constant time, re-runs prompt-injection + MahaRERA advertising + `aigov.run_shadow_eval` checks, imports funnels as drafts (`published = false`), and stages imported skills as `pending` proposals (`D9` human-gated learning).

---

## 5. Option E — External Website Form Connectors & Turnkey Deployment Kit

- **External Website Lead Forms:** Any external site (Hostinger, Bluehost, IONOS, Vercel, Netlify, Lovable, Bolt, WordPress) posts leads to `POST /api/v1/ingest/website` using a workspace-scoped `X-Ingest-Key: rink_...` minted via `POST /api/v1/tenant/ingest-keys`. Ready-to-drop backend handlers ship in:
  - `deploy/website-form-connectors/nextjs-submit-lead.mjs`
  - `deploy/website-form-connectors/php-submit-lead.php`
- **Multi-Host Production Configs:**
  - `Dockerfile` (non-root Python 3.13 container with `/healthz` check)
  - `deploy/Caddyfile.example` (`flush_interval -1` for SSE `/api/v1/events`)
  - `deploy/realtor-estates.service` (`systemd` unit for Ubuntu VPS)
  - `deploy/vercel.json.example` & `deploy/netlify.toml.example` (CDN SPA proxy rewrites)

---

## Verification

```bash
RE_DB=json python3 scripts/proof_phase3_buyer_portal_voice_pwa_and_exchange.py
RE_DB=json python3 -m pytest tests/test_phase3_buyer_portal_voice_pwa_and_exchange.py -q
```

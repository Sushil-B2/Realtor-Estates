# PLANS.md — execution plan

Business strategy lives in `docs/09-ROADMAP-AND-ECONOMICS.md`. **This file is how the work gets done:**
workstreams, sequencing, estimates, dependencies, gates, decision log, risk register.

Status legend: ✅ done · 🟡 in progress · ⬜ not started · 🔒 blocked

---

## 1. Current position

| Item | State |
|---|---|
| Demo build (PWA + API + agents + guardrails + learning gate) | ✅ running |
| Real-estate vertical (`docs/18` P0 → P2 + `docs/46`) | ✅ site visits, matching, stage gates, commission, portal ingestion + SLA, channel-partner portal, unit inventory, five-category taxonomy, `7/12` & NA revenue vault, statutory cost sheet, IGR `e-Registration`/`iSarita` SRO + `Index-II`, Bank APF + Loan/CLP, `E-Ferfar` mutation & marketing trust pack |
| Verification | Pytest and supporting checks are available; see the latest test report for results and skips. |
| Documentation set | ✅ 51 documents in `docs/` (+ README, this file, changelog, history log) — counts checked by `scripts/doc_facts.py` |
| Secrets management | ✅ `.env` (0600, git-ignored) + HTTPS Vault/Doppler/Infisical runtime puller (`docs/38`) → HKDF root → 5 subkeys · fail-closed boot · scanner · rotation |
| Database | ✅ Postgres/Neon, migrations 001–008, RLS on the real branch, append-only audit chain, 7.35 s branch-restore RTO proved (`docs/36`), scale-to-zero anonymous `/healthz` + batched inbox reads (`docs/37`) |
| Object storage + Functions | ✅ photo pipeline, compliance sweep, payments webhook **deployed on Neon Cloud (`ap-southeast-1`) and proved live** (`12/12`, `docs/36`, §9 G3 closed) + SigV4 `put_object`/`head_object`/`delete_object` export & call-recording upload path proved live (`13/13`, `docs/37`) |
| Realtime & Shared state | ✅ cross-instance rate limits, revocations, step-up tickets (`docs/31`, `docs/34`) + cross-instance SSE fan-out (`app/realtime.py`, **100 concurrent inboxes across 2 nodes**, `260/260` frames in `240.4 ms`, `p95 = 77.9 ms`, `docs/37`) |
| Automation runtime | ✅ 9 triggers · 9 typed steps (incl. multi-day `wait`) · 10 playbooks · 15 s worker · idempotent · dead-letter (`docs/23`, `docs/42`, §9 G1 closed) |
| Channel & Auth adapters | ✅ Universal Provider, Tool & Inbound Webhook Fabric (`v1.28.0`, `docs/39`: 25 presets + `custom_http` JSON-template/dot-path engine + playable WAV Local Twin + dynamic SSRF egress) + WhatsApp Cloud API v21.0, TRAI DLT SMS, voice telephony + S3 recording, live Neon Auth (`RE_AUTH_MODE=neon`) proved (`docs/38`) |
| Payment collection | ✅ token receipts + refunds + GST/TDS commission tax invoices + §34 credit notes + Form 16A settlement + GSTR-1 export + HMAC webhooks + Razorpay `/v1/orders` outbound adapter (`docs/24`, `docs/38`, `docs/42`, §9 G2 closed; live `RAZORPAY_*` keys optional) |
| Paying tenants | ⬜ none |

> Rebased 2026-11-18 against the code (`scripts/doc_facts.py`). Every number in this file is now a
> measured number or a link to a proof script.

**What "done" means for the demo:** a person can sign up, see a populated tenant, run an agent, hit a
guardrail, approve or reject a learning proposal, publish a funnel, hit the public page, run the
security suite, and read a report that proves it. Anything not covered by a test is labelled as such.

---

## 2. Workstreams

| # | Workstream | Owner role | Demo | Phase 1 | Phase 2 |
|---|---|---|---|---|---|
| W1 | Data platform (Postgres, RLS, migrations) | Backend | ✅ JSON store | ✅ Neon PG18 + RLS (`docs/12`, `docs/36`) | ✅ |
| W2 | Identity & session hardening | Security | ✅ HMAC sessions | ✅ MFA/TOTP + Neon Auth (`docs/27`–`28`, `docs/38`) | ✅ |
| W3 | Crypto & key management (KMS, rotation) | Security | ✅ in-process keys | ✅ DEK envelope + rotation (`docs/30`, `docs/38`) | ✅ |
| W4 | Agent runtime & tool gateway | AI | ✅ scopes/tools/approvals | ✅ dynamic tools | ✅ dual-LLM + A2A (`docs/41`, `docs/42`) |
| W5 | Guardrails & jailbreak defence | Security/AI | ✅ inspect/redact/block | ✅ eval harness (`docs/41`) | ✅ macro/bomb upload gate (`docs/44`) |
| W6 | Learning gate (proposals → promotion) | AI | ✅ human gate | ✅ shadow-eval | ✅ canary/rollback + signed packs (`docs/41`, `docs/43`) |
| W7 | Channels: WhatsApp, SMS, voice | Integrations | ✅ simulated + WAV twin | ✅ universal adapters (`docs/39`) | ✅ inbound receptionist + diarised sentiment (`docs/43`) |
| W8 | CRM & pipeline | Product | ✅ | ✅ | ✅ Buyer/NRI self-serve portal + FEMA (`docs/43`, `docs/44`) |
| W9 | Inventory (units, holds, race safety) | Product | ✅ full | ✅ | ✅ |
| W10 | Growth (funnels, pages, ads, social, JEV) | Product | ✅ | ✅ RERA publish gate (`docs/41`) | ✅ ads hard spend caps + human budget gate (`docs/44`) |
| W11 | Analytics & forecasting | Data | ✅ explainable | ✅ 5 cost drivers (`docs/40`) | ✅ |
| W12 | Compliance engineering | Compliance | ✅ ledger + gates | ✅ DPAs, DPIA, erasure (`docs/40`) | ✅ Consent Manager + FEMA (`docs/42`, `docs/43`) |
| W13 | Observability & IR | SRE | ✅ spans/logs | ✅ traces/meters/alerts (`docs/41`) | ✅ |
| W14 | REDLINE (in-process + external) | Security | ✅ 22 + 51 | ✅ CI gate + Sentinel (`docs/35`) | ✅ |
| W15 | PWA craft (offline, update, install) | Frontend | ✅ | ✅ push + idempotent offline outbox (`docs/43`, `docs/44`) | ✅ |
| W16 | Docs & enablement | All | ✅ | ✅ (`docs/01`–`docs/45` + `deploy/`) | ✅ |
| W17 | Real-estate vertical (`docs/18`–`24`) | Product | ✅ P0/P1/P2 | ✅ refunds, CN, GSTR-1 (`docs/42`) | |
| W18 | Automation runtime (triggers → executor) | Backend | ✅ §9 G1 | ✅ multi-day `wait` (`docs/42`) | |

---

## 3. Phase 1 sprint plan (12 weeks, 2-week sprints)

**Status:** **S0 is complete ahead of schedule** — Postgres + RLS + migrations + the append-only
audit trigger run on the Neon branch (`db/001`–`008`, `docs/12`), Redis/file shared state backs rate
limits, revocations, step-up tickets (`docs/31`, `docs/34`) and cross-instance SSE fan-out
(`app/realtime.py`, **100 concurrent inboxes proved across 2 nodes**, `docs/37`), and CI runs the
embedded PostgreSQL 18 gate + Sentinel `--deep --strict` (`docs/32`, `docs/35`). S1–S5 are next. The
Phase 0.5 plan in §9 (**all G1–G8 closed**) completed the vertical prerequisites before S1.

**S0 — Foundations (w1–2)**
- Postgres schema + RLS from `docs/03`; dual-write behind a flag; import seeded tenant.
- Redis rate limiter; SSE fan-out.
- CI: lint + `node --check` + pytest + REDLINE against an ephemeral instance.
- **Gate:** parity — every pytest passes against Postgres.

**S1 — Real channels (w3–4 — adapters & signed webhooks shipped `v1.27.0`, `docs/38`; live carrier keys pending)**
- ✅ WhatsApp Cloud API v21.0 adapter (`app/channels.py`: approved template registry, 24 h customer-service window gate `422 WHATSAPP_WINDOW_CLOSED`, plain-text `hub.challenge`, `X-Hub-Signature-256` HMAC inbound + `sent`/`delivered`/`read` callbacks).
- ✅ SMS via TRAI DLT-registered provider (`app/channels.py`: 19-digit DLT template registry, 6-char `sender_id` header, `{#var#}` pattern check, signed DLR webhook).
- ✅ Voice: outbound qualification call adapter (`Sarvam`/`ElevenLabs` + DPDP consent + TRAI quiet hours + `voice_minutes` quota) + signed completion webhook archiving recordings/transcripts to Neon S3 `recordings/tenants/<tid>/…`.
- **Gate:** protocol + webhook + S3 recording path proved (`17/17 PASS` in `scripts/proof_channels_and_auth.py`); live WABA/DLT/telephony commercial cutover requires external carrier credentials.

**S2 — Security hardening (w5–6 — engineering complete `v1.16`–`v1.28.0`)**
- ✅ KMS envelope encryption, key rotation drill (`docs/30`, `scripts/rotate_keys.py` including connector & webhook secrets).
- ✅ TOTP MFA, refresh tokens, device list, revoke-all, single-use step-up (`docs/27`, `docs/28`, `docs/34`).
- ✅ Upload pipeline with scanning and magic-byte MIME sniffing for document packs (`POST /api/v1/properties/{pid}/documents/scan` — blocks EICAR, `MZ`/`ELF` executables, `<script>` polyglots, and PDF `/JavaScript` actions; `docs/39`).
- **Gate:** external pen test scheduled; threat model reviewed with an outside pair of eyes.

**S3 — Compliance launch pack (w7–8 — engineering complete `v1.29.0`, `docs/40`)**
- ✅ Privacy notice (`v3.2`, dynamic `[R13]` AI processor disclosure + `/api/v1/public/privacy-notice/{slug}`), 4-domain DPIA, sub-processor register + DPAs, grievance officer + 30-day statutory SLA desk (`grievances` collection).
- ✅ Erasure job with cryptographic `HMAC-SHA256` certificate + blind-index suppression tombstone (`erasures` collection); portable DSR export bundle + Rule-4 signed consent artefact; immediate consent withdrawal queue/task propagation proved (`10/10 PASS` in `scripts/proof_s3_s4_compliance_and_reliability.py`).
- **Gate:** external legal counsel sign-off; `docs/07` matrix has no untested engineering row.

**S4 — Reliability & SaaS billing (w9–10 — engineering complete `v1.29.0`, `docs/40`)**
- ✅ Crash-safe step-level idempotency keys (`<run_id>:step:<idx>`) + per-worker claim leases + retries with backoff + Dead-Letter Queue (`replay`/`dismiss`).
- ✅ Usage metering per tenant (`whatsapp_out`, `sms_out`, `voice_minutes`, `ai_runs`) wired to SaaS subscription GST invoicing (`POST /api/v1/billing/invoices/generate`).
- ✅ Live dashboard & API for the five weekly cost drivers + unit economics gross margin in `docs/09` §4 (`GET /api/v1/billing/cost-drivers`, `/#/analytics`).
- **Gate:** ✅ chaos drill proved — `SIGKILL` (`-9`) a worker mid-campaign on 5 leads; surviving worker completes all 5 with `[1, 1, 1, 1, 1]` sends and **zero duplicates**.

**S5 — Beta readiness (w11–12 — engineering attestation live `v1.29.0`; commercial partner onboarding next)**
- ⬜ 3 design partners commercially onboarded; funnel + voice + WhatsApp live.
- ✅ Signed weekly REDLINE + `< 4 h` approval-queue SLA partner attestation generator (`POST /api/v1/compliance/attestations`, `docs/40`).
- **Gate:** 3 partners live, zero critical findings, approval queue age < 4 h.

---

## 4. Definition of done (every change)

1. Test that fails without the change exists.
2. `pytest` green; REDLINE green (or the finding documented with a fix date).
3. Input validated at the edge; output escaped at the sink; nothing from a model reaches a shell/HTML/SQL string.
4. Audit entry for any mutation.
5. Docs updated — including this file when scope moves.
6. Changelog entry added under the unreleased heading.

---

## 5. Decision log (append-only)

| # | Date | Decision | Alternatives rejected | Reversible? |
|---|---|---|---|---|
| D1 | 2026-11-17 | Project named **Realtor Estates**, tenant slugs `sunrise`/`metro` | generic "RealtyOS" | no |
| D2 | 2026-11-17 | Stdlib-only runtime (no pip installs) for the demo | FastAPI/Django | yes, but re-verify crypto |
| D3 | 2026-11-17 | ChaCha20 keystream + HMAC keystream from `hashlib`/`hmac` (no `cryptography` lib) | AES-GCM via `cryptography`, `nacl` (both absent) | yes — ciphertext gets a `v2:` prefix |
| D4 | 2026-11-17 | JSON store with atomic replace + RLock | SQLite (single-writer, but binary file in a repo) | yes — interfaces are tenant-explicit |
| D5 | 2026-11-17 | Session = HMAC token in HttpOnly cookie **plus** Bearer support | JWT (no lib), server-side session table | yes |
| D6 | 2026-11-17 | CSP: `script-src 'self'`, `style-src 'self' 'unsafe-inline'` | nonce-based (needs inline script), unsafe-eval | no |
| D7 | 2026-11-17 | No inline JS anywhere; all wiring via `data-act` / `data-form` | inline `onclick` | no |
| D8 | 2026-11-17 | Agents fail **closed** on `block` *and* `quarantine` | fail-open with warning | no |
| D9 | 2026-11-17 | Learning: human gate mandatory, critical risk needs owner + explicit ack | auto-promote with review later | no (that is the brief) |
| D10 | 2026-11-17 | Block LinkedIn & direct MagicBricks scraping (451) | silent scraping | no |
| D11 | 2026-11-17 | JEV is research-only; rejected the seeded autonomy proposal | auto-publish shorts | yes, but not without new legal review |
| D12 | 2026-11-17 | Rate-limit buckets sized for a realistic suite; harness *skips* on 429 | loosening limits until tests pass | yes |
| D13 | 2026-11-17 | Public pages server-rendered (no JS required) | SPA-only | yes |
| D14 | 2026-11-17 | Webhook payloads carry ids, never PII | full payload for convenience | no |
| D15 | 2026-11-17 | In-process REDLINE is tenant-scoped; external harness requires `--i-own-this-target` | "uncensored" agent | no |
| D16 | 2026-11-17 | One root secret (`RE_MASTER_KEY`) → HKDF-SHA256 → five subkeys | five secrets in config/managed separately | no |
| D17 | 2026-11-17 | Production **refuses to boot** without a valid key; dev falls back to a 0600 keyring file | boot with a warning | no |
| D18 | 2026-11-17 | Secrets live in `.env` (0600, git-ignored) today; KMS/HSM is the documented target, not a claim | keep the keyring file (what we had — and it was committed to git) | yes, by design |

**On D15 (requested "uncensored" red-team agent):** delivered as *maximum coverage within authorisation* —
53 checks across 8 categories, aggressive payloads, machine-speed recon — but bound to loopback or an
explicitly declared owned target. An agent with no scope gate cannot be shipped, insured, or run in CI;
the scope gate is what makes the aggression usable. See `docs/08` §1.

---

## 6. Risk register

| # | Risk | P | I | Mitigation | Trigger to act |
|---|---|---|---|---|---|
| R1 | Guardrails bypassed by novel phrasing | M | H | Layered controls, corpus + CI, classifier in P2 | Any successful bypass in the wild |
| R2 | Approval fatigue → agents bypassed by humans | H | H | Queue UX, batch approvals, age metric | Median queue age > 6 h |
| R3 | Voice COGS spike | M | H | Adapter swap, overage pricing | Blended > ₹5/min |
| R4 | Provider TOS change (WhatsApp/pricing) | M | M | Multi-provider routing + SMS fallback | 30-day notice from provider |
| R5 | DPDP enforcement action | L | H | Ledger, purpose binding, runbook, 6 h CERT-In target | Board inquiry |
| R6 | Tenant data leak via bug | L | Critical | Tenant-explicit store API, BOLA checks in every release | Any 4xx→5xx pattern in cross-tenant tests |
| R7 | Solo-bus factor / knowledge concentration | H | M | This doc set; code map in `docs/02` §9 | Anyone unable to explain a subsystem |
| R8 | Model provider deprecates a model in use | M | M | Version pinning + provider register | Deprecation notice |
| R9 | JSON store fails at scale | H | M | Postgres in S1; documented lock boundary | > 1 write contention alert/day |
| R10 | RERA advertising liability from AI content | M | H | Claim-set validator, human publish gate | Any blocked claim regression |
| R11 | Secret leaks via git/log/screenshot/model prompt | M | H | `.gitignore`, `check_secrets.py --history` in CI, redaction at every boundary, fixtures built at runtime | Any scanner finding; any `.env` in a diff |
| R12 | Host compromise exposes the root key | M | H | 0600, redaction, `rotate_keys.py` (verified re-encryption), KMS migration path `docs/11 §7` | Unauthorised access to the host or the repo |
| R13 | Key rotation attempted by hand and breaks PII | M | H | `rotate_keys.py` with backup + verify-then-rollback; dry-run mode; documented cadence | Anyone editing `RE_MASTER_KEY` manually |

---

## 7. Open questions for the owner

1. **Tenant data isolation level:** shared tables + RLS (cheap) or schema-per-tenant (stronger, costlier)? *Recommendation: shared + RLS now, schema-per-tenant for Enterprise.*
2. **Voice provider default:** Sarvam-class (cheap, India-strong) vs ElevenLabs (better voice, 8× cost)? *Recommendation: Sarvam default, ElevenLabs as an upsell.*
3. **Learning pooling across tenants:** opt-in differential privacy in 2028, or never? *Recommendation: never by default; explicit separate programme.*
4. **Do we resell a WABA or require the tenant's own?** *Recommendation: tenant-owned at Starter, resold at Scale.*
5. **On-prem voice for enterprise** — worth the support burden in 2027?
6. **Pricing shape (D22):** per-tenant tiers (`docs/09` §2: ₹9,999 / 24,999 / 59,999) or per-seat
   (₹1,000–1,500/user, `docs/18` §7's wedge against Sell.Do's ₹1,500–3,000)? The two docs currently
   disagree and no SKU exists yet. *Recommendation: per-tenant tiers for the developer-owner buyer
   (they buy outcomes, not seats), with a per-seat add-on above 10 users.*

---

## 8. Working agreements

- Every claim in a demo or a doc must be reproducible with a command in `README.md`.
- No number without a source (`docs/10`) or a measurement (this repo).
- Security findings are never "known issues" without a date and an owner.
- No credential is ever typed into a chat, an issue, a screenshot or a model prompt. If one is, it is rotated the same day (`docs/11 §6`).
- If a feature cannot be guarded, it does not ship.

## Backend plan — Neon (in progress)

Done: connected to Neon Postgres 18.7; schema + migrations 001–008, dual-mode
store, audit trigger, **RLS verified on the real server**, SigV4 presign +
upload/stat/delete verified live on `projectassets`, all 3 Neon Functions + 2
cloud triggers deployed and proved live (`docs/36`), 7.35 s branch-restore RTO
proved (`docs/36`), cross-instance SSE fan-out proved at **100 concurrent
inboxes** (`docs/37`), batched inbox reads + scale-to-zero static caching
(`docs/37`), `npm run verify`.

Remaining, in order:
1. Add `NEON_AI_GATEWAY_KEY` and run `python3 -m app.neon` to bring the gateway live. *(External credential required.)*
2. ~~Point Auth at Neon Auth and delete the local session store (one auth system, not two — local sessions, TOTP MFA, and step-up are hardened in `docs/27`, `docs/28`, `docs/34`; switch only if migrating to managed IdP).~~
   **CLOSED v1.27.0** — `app/neon.py` (`verify_session_token` fixed for live Neon Better Auth
   `GET /get-session`, strictly rejecting `HTTP 200 null` unauthenticated payloads, with 30 s
   SHA-256-keyed introspection cache + upstream `/sign-out` revocation) + `RE_AUTH_MODE=neon`
   (`app/api.py`, `app/server.py`) disabling local password sign-in/sign-up (`403 LOCAL_AUTH_DISABLED`)
   and local HMAC tokens across both `/api/v1/**` and `/api/v1/events` SSE so a deployment runs
   **one auth system, not two**. Proved live against `NEON_AUTH_BASE_URL` on Neon Cloud
   (`scripts/proof_channels_and_auth.py`, `17/17 PASS`). `docs/38`.
3. ~~Move exports/recordings to object storage (presign already verified; the upload path is not).~~
   **CLOSED v1.26.0** — pure-stdlib SigV4 `put_object` / `head_object` / `delete_object`
   (`app/neon.py`, SHA-256 content-verified, traversal-refusing) + `POST/GET /api/v1/exports`
   (`exports/tenants/<tid>/…`) and `POST /contacts/{cid}/calls` / `POST /agents/{aid}/voice-test`
   / `GET /api/v1/recordings/{rid}` (`recordings/tenants/<tid>/…`, `pii:read` gated).
   Proved live against Neon `projectassets` (`scripts/proof_storage_uploads.py`, `13/13 PASS`). `docs/37`.
4. ~~Realtime: replace the in-process SSE fan-out, then load-test 100 concurrent inboxes.~~
   **CLOSED v1.26.0** — `app/realtime.py` (`RealtimeHub` 1-thread-per-node multiplexer +
   `Last-Event-ID` cursor replay) + `app/shared_state.py` (`publish_event` / `events_since`
   across `Redis` RESP2, `SharedFiles`, and `_MemoryAdapter`). Load-tested across **2 live
   `app.server` nodes** and **100 concurrent SSE inboxes** (`60` `sunrise` + `40` `metro`):
   **`260/260` frames in `240.4 ms` (`p50 = 45.3 ms`, `p95 = 77.9 ms`, `p99 = 82.2 ms`),
   `0` errors, `0` cross-tenant leaks** (`scripts/proof_realtime_inboxes.py`, `11/11 PASS`). `docs/37`.
6. ~~Batch the inbox reads (one query per collection today) and cache the static shell so page loads never wake the database compute.~~
   **CLOSED v1.26.0** — `GET /api/v1/inbox` and `GET /api/v1/inbox/{cid}` batch
   `("conversations", "contacts")` into one `documents_many` SQL query (`max_age_ms=250`,
   invalidated on realtime events); `_static()` serves from RAM (`_STATIC_CACHE`) with
   strong SHA-256 `ETag` + `304 Not Modified`, and anonymous `/healthz` calls
   `STORE.backend_status(probe=False)` so page loads and liveness polls execute **zero**
   SQL queries and never wake scaled-to-zero Neon compute. `docs/37`.

## Security follow-ups (from docs/14 — pass 2)

Done in pass 2, recorded so they are not re-opened: trusted-proxy-only XFF, forced
RLS with a `re_app` runtime role split, reconnect-on-drop with a 503 (not a
traceback), role-gated user directory and audit trail, scrubbed access log, no
server banner, liveness-only `/healthz`, probe-account pruning.

Open:

1. Move the master key and both DSNs from `.env` to a secret manager (the file is
   0600 and git-ignored — that is hygiene, not a vault). **Runtime loader shipped v1.27.0**
   (`app/config.py:load_from_secret_manager` pulls secrets into memory over HTTPS from
   **Doppler**, **Infisical v3**, or **HashiCorp Vault KV-v2** via `RE_SECRET_MANAGER_URL` +
   `RE_SECRET_MANAGER_TOKEN`, fail-closed in `RE_ENV=production`, zero `.env` disk writes —
   `docs/38`). *(Supplying external vault service token + deleting `.env` remains an operator action.)*
2. ~~Run the agent (`npm run audit:sentinel:deep`) from CI on every deploy, with
   `--strict` so a regression fails the build.~~ **CLOSED v1.24.0** —
   `.github/workflows/ci.yml` + `npm run audit:sentinel:strict` gate every push and
   pull request on `npm run verify`, `scripts/verify_postgres.py --real`,
   `sentinel/agent.py --self-test`, and `sentinel/agent.py --deep --strict`. `docs/35`.
3. ~~Feed the agent's findings into the audit chain as `security.sentinel_run` events,
   so a scan is itself evidence.~~ **CLOSED v1.24.0** — `POST /api/v1/security/sentinel-runs`
   (owner-only, refusing any payload containing unredacted secrets via
   `sec.contains_secret`) stores a `runs` record (`type="sentinel"`), appends
   `security.sentinel_run` into the SHA-256 hash chain, and surfaces `last_sentinel`
   in `GET /api/v1/security/posture` and the Security console. `docs/35`.
4. ~~Extend the gate's EXFILTRATE class to cover the agent's own report contents.~~
   **CLOSED v1.24.0** — `Gate.check("WRITE_REPORT", ...)` + `scrub_report_obj()`
   refuse off-directory destinations and unredacted credentials before disk write.
   Building and running this gate caught **three** real bugs in `sentinel/agent.py`:
   `dict(r.getheaders())` was overwriting `re_session` with `re_refresh` on fresh
   logins (silently skipping 5 authenticated checks), `SEN-C503` was writing a live
   30-day `re_refresh=<token>` credential verbatim into `reports/sentinel-report.md`,
   and `SEN-C201` scanned only `localStorage` despite claiming `localStorage/sessionStorage`.
   `docs/35`.

## Security follow-ups (from docs/13)

1. Rotate the Neon password and the storage keys — both were displayed once during
   the debugging session before redaction existed. *(Operator console action; external credential.)*
2. ~~Step-up re-authentication before kill-switch, key rotation and user deletion.~~
   **CLOSED v1.22.0 + v1.23.0** — `app/stepup.py` + `POST /auth/step-up` + `Ctx.require_step_up`:
   a signed ticket bound to **one action, one record and one session**, single-use, 300s
   (120s signed and advertised when the account has no second factor), backed across
   multiple server instances by `shared_state.claim_once` (`SET ... PX ... NX` on
   Redis, `flock` on `SharedFiles`, failing closed `503 STEP_UP_STATE_UNAVAILABLE`
   if a configured shared store goes unreachable). `scripts/proof_stepup.py` = 44/44,
   `scripts/proof_stepup_shared.py` = 15/15. `docs/34-STEP-UP.md`.
3. ~~Move rate limiting to Redis before running more than one app instance.~~
   **CLOSED v1.20.0 + v1.23.0** — `app/shared_state.py` backs rate-limit buckets,
   token/session revocations, single-use step-up tickets, and step-up attempt
   cool-offs across `memory`, `redis` (`RE_REDIS_URL`), and `file` (`RE_SHARED_DIR`).
   `docs/31`, `docs/34`.
4. ~~Terminate TLS in front of the app, then set `Secure` + HSTS on the session cookie.~~
   **CLOSED v1.24.0** — `Handler._cookie_is_secure()` (`app/server.py`) sets `; Secure`
   on `re_session`, `re_refresh`, and logout clear-cookies whenever `RE_ENV=production`,
   `RE_SECURE_COOKIES=1`, or a configured `RE_TRUSTED_PROXIES` peer forwards
   `X-Forwarded-Proto: https` (ignoring untrusted `X-Forwarded-Proto` spoofing so
   plain-HTTP callers cannot be DoS'd), alongside `Strict-Transport-Security`. `docs/35`.
5. ~~Restore a Neon branch and re-verify the audit chain; document the RTO.~~
   **CLOSED v1.25.0** — `scripts/proof_neon_cloud_rto.py` restores a point-in-time
   child branch from `production` (`br-patient-fire-aouhmkxr`) via the Neon v2 API,
   waits for TLS SQL readiness (`3.47s`), verifies all `1,505` `re_audit` rows across
   `28` tenants byte-for-byte via `sec.audit_hash` (**`7.35s` warm / `9.86s` cold
   end-to-end RTO**), runs `scripts/verify_postgres.py` against that disposable
   branch (`21/21 PASS, 0 skipped`), and deletes the drill branch (`HTTP 200`).
   `docs/36`.
6. ~~Decide a retention window for audit IPs/emails before real tenant data lands.~~
   **CLOSED v1.24.0** — `app/audit_retention.py` enforces a configurable PII window
   (default **90 days**, `RE_AUDIT_PII_RETENTION_DAYS` or `PATCH /api/v1/tenant`
   `settings.audit_pii_retention_days` in `7..2555` days) by deterministically
   pseudonymising expired emails and IPs on read via HMAC under `KEYS["blind_index"]`
   while leaving raw append-only rows untouched so the 7-year SHA-256 hash chain
   (`verify_audit()`) remains cryptographically valid. `docs/35`.

## 9. Gap register — Phase 0.5 (2026-11-18)

The honest list of what the vertical work did **not** close, ranked by damage to the thesis. Each
item names what proves it is closed.

| # | Gap | Why it matters | Proof of closure |
|---|---|---|---|
| **G1** | ~~No automation executor.~~ **CLOSED v1.11.0** — `app/automations.py` (9 triggers, 8 typed steps, 9 playbooks), `POST /automations/tick` + `/run`, `GET /automations/queue`, a 15 s worker (`RE_AUTOMATION_TICK`), idempotency keys, backoff + dead-letter, and three gates before every send. The PRD's #1 job now executes: a lead is messaged (or the run says exactly why not). Remaining within G1: transports are simulated, and there is no multi-day `wait` step. | The headline promise is now demonstrated rather than described | ✅ `scripts/proof_automations.py` (32 checks) · `tests/test_automations.py` (23) · `docs/23` |
| **G2** | ~~No payment collection.~~ **CLOSED v1.12.0 (+ live `/v1/orders` adapter v1.27.0)** — `app/money.py` (GST 18% split CGST/SGST vs IGST, TDS 194-H, one invoice series per tenant per FY), `POST /webhooks/payments` (HMAC over raw bytes) + `POST /payments/reconcile` (the production pull path), `POST /payments/orders` (live Basic-Auth Razorpay `/v1/orders` call when `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` are configured, honest `"simulated"` mode when unset — `docs/38`), `POST /deals/{id}/invoice`, `GET /payments` · `/invoices` · `/money/summary`. A token payment books the unit and issues a **receipt with no GST** (custody, not revenue); commission gets a **tax invoice** with GST, TDS and net receivable; money never overrides a document gate. Remaining within G2: live `RAZORPAY_*` keys, refunds/credit notes, commission *collection* path, GSTR-1 export. | The positioning is now a demonstrated flow, not a slide | ✅ `scripts/proof_money.py` (46 checks) · `scripts/proof_channels_and_auth.py` (17) · `tests/test_money.py` (30) · `docs/24`, `docs/38` |
| **G3** | ~~Neon Functions undeployed.~~ **CLOSED v1.25.0** — all three functions (`propertyphotos`, `compliancesweep`, `paymentswebhook`, `nodejs24` / `2048 MiB`) and both cloud triggers (`on-property-photo` on `projectassets` prefix `properties/`, `nightly-compliance` `0 2 * * *`) deployed to Neon Cloud (`ap-southeast-1`) and proved end-to-end by `scripts/proof_deployed_functions.py --fire-sweep` (**12/12 PASS**: Neon edge stripping forged `x-neon-trigger-invocation-id` headers, real JPEG S3 upload → cloud trigger → 3 `sharp` WebP variants in S3 & `re_property_media`, HMAC webhook write + replay dedupe, and live cloud cron execution verifying the SHA-256 audit chain). Four real deploy/cloud defects caught and fixed (`externalPackages: ["sharp"]`, `FunctionDef.env`, `--no-env-pull`, and trigger-edge verification). | Production serverless pipeline proved on live cloud endpoints and triggers | ✅ `scripts/deploy_functions.sh --proof` · `scripts/proof_deployed_functions.py --fire-sweep` (12/12) · `docs/36` |
| **G4** | ~~CP portal has no inventory view.~~ **CLOSED v1.13.0** — `GET /cp/inventory` + `/cp/inventory/{id}`: free stock only (a `hold` is someone else's 48 hours), seller and buyers stripped from the payload with `partners.WITHHELD` printed on every page, units shown by label and state but never with their `booking`, and a commission estimate derived from the house's own ledger (median gross ÷ sale value) rather than a guessed 2%. `marketing.ready` refuses to call an unregistered project advertisable — RERA s.3 puts that penalty on whoever published. Plus the portal card with facet-driven filters. | A partner can now see what they are selling | ✅ `scripts/proof_channel_partners.py` (49 checks) · `tests/test_partner_inventory.py` (16) · `docs/20` §6b |
| **G5** | ~~No images on properties.~~ **CLOSED v1.14.0** — `app/media.py` groups rows into photos (three variants of one upload are one photo), picks the cover (`card`, not `hero`, for a table cell), states the publish floor (3 of 5 slots), and returns a deterministic monogram when there is no photo. `GET /properties/{id}/media` now carries `photos`/`cover`/`readiness`/`placeholder`; new `GET /properties/covers?ids=` answers a whole table in one read with `ok`/`none`/`unavailable`/`not_found` per id. Desk photo strip + partner catalogue thumbnails; `scripts/upload-listing-photos.mjs` seeds a demo through the real trigger without ever writing the function's table. Demo seeded with real photographs on three listings (3 / 2 / 1 — enough to show the floor). Remaining within G5: no cropping/ordering UI, no video or floor plans. | A visual vertical that shows photographs, and says so when it has none | ✅ `scripts/proof_property_images.py` (27) · `tests/test_property_media.py` (13+1) · `docs/25` |
| **G6** | ~~Postgres/RLS tests skip by default.~~ **CLOSED v1.21.0** — `scripts/verify_postgres.py` runs the Postgres, RLS and function-table suites and **refuses to call a skip a pass**: it probes the target first (two connections, `pg_backend_pid()`, a row written on one and read on the other) and prints what that target *cannot* prove. Exit 0 = nothing skipped and RLS + concurrency both proved; **2 = PARTIAL with the blind spots printed**; 1 = failed; 3 = no database. `--real` runs actual `postgres` binaries from the `embedded-postgres` wheel — no root, no Docker, no system package — which is what makes the gate runnable in a clean checkout instead of deferring it to "when Neon is reachable": `21 passed, nothing skipped, RLS and concurrency proved`. The RLS migrations are applied through `dbmod.migrate(include_rls=True)` rather than by hand, because `re_app` is created by 002 and made impersonable by 003 — a policy nobody can `set role` into is a policy nobody can test, which is part of how this gap survived. Three real defects fell out of running it: the concurrent-writer test counted `load_all()["audit"]` (a **global** `seq`, so other tests' rows decided whether the chain "forked"), the stand-in guard was keyed on the port number `:5433` rather than a measured capability, and the capability probe deadlocked itself by doing DDL in two open transactions. Not claimed: `--real` is PostgreSQL 18 on a unix socket, not Neon's proxy — `--url "$NEON_BRANCH_URL"` is still the pre-release run (**proved on a live disposable Neon `-pooler` branch in v1.25.0: `21/21 PASS, 0 skipped`, catching a PgBouncer autocommit PID-recycling false negative — `docs/36`**). | The S0 gate exists and can be run by anyone, and it says out loud what it did not prove | ✅ `python3 scripts/verify_postgres.py --real` (21/21/0, PASS) + hosted Neon branch (`scripts/proof_neon_cloud_rto.py`, 21/21/0) · `tests/test_verify_postgres.py` (11+1) · `docs/32`, `docs/36` |
| **G7** | ~~Demo data polluted by its own proofs.~~ **CLOSED v1.15.0** — a proof now marks its rows as it writes: `X-Proof-Run: <id>` on any authenticated (or signed-webhook) write stamps every row the request creates, captured at the single choke point in `api.handle` so no route has to know. `GET /demo/hygiene` reports what came from which run; `POST /demo/proof-rows/purge` (owner, slug typed out) deletes stamped rows, cascades *down* only, and **skips any row a surviving row still points at** — a purge that orphans the demo is not a cleanup. Legacy rows predating the stamp need a named prefix (`"Proof "`, `"Rival"`) or, for money rows that carry no name, an explicit `legacy_since` date; both are previewed before anything is deleted. `scripts/reset_demo.py` is a dry run unless told otherwise. First real run: 82 rows removed, demo now 24 real properties / 100 contacts / one partner / an honestly empty money board. Remaining: no proof-mode tenant, no auto-cleanup (a failed proof must stay inspectable). | The shop window shows the business again, and the next proof cannot rebuild the mess | ✅ `tests/test_demo_hygiene.py` (13) · live round trip (stamp 8 rows → purge 8 rows) · `docs/26` |
| **G8** | Phase 1 security pack. ~~No TOTP MFA~~ **MFA CLOSED v1.16.0** — `app/totp.py` (RFC 4226/6238, pinned to the reference vectors), enrolment in two steps, `POST /auth/login` returning a five-minute **challenge** instead of a session for an enrolled account, `POST /auth/login/mfa` exchanging it, single-use codes via `mfa_last_step`, ten hashed recovery codes, sealed seed (`sec.seal`, AAD-bound to the account), per-account attempt budget + lockout, disable/regenerate requiring password **and** a factor, owner-only audited `mfa_reset`, and `tenant.settings.require_mfa` gating identity/money actions (`manage_tenant` deliberately exempt so the switch cannot lock itself). **The old `mfa_enabled` flag was a lie and is gone from PATCH.** A collision found on the way: `blind_index` keeps only the digits of a value with no `@`, so recovery codes hashed identically — `sec.blind_token` now indexes them. **Sessions CLOSED v1.17.0** — a session is a row, not a claim inside a token: `sessions` collection, `sid` checked on every request, 30-minute access token + 30-day rotating refresh token (opaque, hashed at rest, httpOnly cookie scoped to `/api/v1/auth`), reuse detection that revokes for *both* holders, a device list that names the browser and masks the address, `DELETE /auth/sessions/{sid}`, revoke-others, logout that ends the row, and an SPA that refreshes instead of bouncing. **Usage meters CLOSED v1.18.0** — the workspace card used to answer `voice_minutes: 4820` and `whatsapp_messages: 18422`, both literals in a route, with `ai_runs` summed from a seeded counter that nothing reset: a usage-based invoice would have been printed from three constants. `app/meters.py` now derives each flow meter from the rows that already record the work (`runs` by status, outbound `messages` by channel, `activities` per **call** for started minutes), keeps what no row can hold in an append-only `usage_ledger` (idempotent on `(meter, source_id)`, owner-only, reasoned, audited), and enforces `PLAN_CAPS` at four sites — contacts, inbox send (before consent and quiet hours), agent run, and the automation runtime, where a miss **holds** the send instead of sending it. A period is a calendar month in the workspace timezone (IST, so the first 5½ hours of the 1st are not billed to the previous invoice); 80 % warns in an `X-Usage-Warning` header rather than a modal; a breach is `402 QUOTA_EXCEEDED` naming the meter, the cap and — for a flow, not a stock — the reset date. A finished month can be frozen into `usage_snapshots`, which is why `docs/26`'s purge can no longer shrink an invoice. **KMS envelope CLOSED v1.19.0** — `.env` no longer holds the root: `app/kms.py` wraps it with a key-encryption key (`env` / `http` / `local` providers), `app/aead.py` supplies the primitive, boot is fail-closed (`KEK_MISMATCH`/`NO_KEK_PROVIDER`/`KEK_DENIED`/`KEK_UNREACHABLE`, with no fallback path that could ever be taken), `init` adopts the existing root so nothing is re-encrypted, `rewrap` rotates a KEK without touching ciphertext (the old KEK kept as `RE_KMS_KEK_PREVIOUS`, each KEK tried under its own AAD), `GET /security/envelope` proves unwrap + a wrap/unwrap roundtrip + that the unwrapped root derives *this process'* subkeys, `POST /security/envelope/rewrap` is owner-only, confirmed and audited, the posture score became computed (`round(100×holding/controls)`) instead of the literal `88`, and the console says in the same panel that there is no managed KMS and no HSM. **Shared state CLOSED v1.20.0** — rate limits and token revocations were per process, which is correct on one instance and not a control on two: N instances multiplied every bucket by N and a logout only bit on the node that served it. `app/shared_state.py` puts both behind one interface with three backends (`memory` default and unchanged, `redis` via `RE_REDIS_URL` with an in-tree RESP2 client — no third-party runtime dependency — and `file` via `RE_SHARED_DIR` for two workers on one host), keeping the `RateLimiter.check()` signature `docs/02` promised. A configured backend that does not answer is **fatal in production** and degrades loudly in development; a store that dies mid-flight falls back to enforcing in-process rather than failing open; revocation fails closed instead. Two real bugs fell out: `REVOKED.clear()` at 5000 entries was **un-revoking every logged-out token at once** (now pruned by expiry), and `if "role" in (patch.get("role"),)` was a tautology that made role-change revocation **dead code** — a demoted manager kept a working token for eight hours (now an epoch stamped into every token, so a bump retires tokens issued by *other* instances). Not claimed: the JSON store is still single-writer (Postgres is what multi-instance needs), the account lockout stays a data row, SSE fan-out is still per process. G8 is complete; remaining in the pack: nothing. | A second factor that is a factor, a session a person can see and end, and a usage number that is a measurement | Cannot bill usage-based plans; cannot run a second instance | ✅ `tests/test_mfa.py` (41) + `tests/test_sessions.py` (20) + `tests/test_usage.py` (26) · `scripts/proof_mfa.py` (45/42) · `scripts/proof_sessions.py` (41/34) · `scripts/proof_usage.py` (47) · `docs/27`, `docs/28`, `docs/29` |

**Order agreed with the owner:** doc-truth (done, v1.10.1) → **G3** deploy + proofs (staged, one
credential away, v1.10.2) → **G1** automation executor (**closed**, v1.11.0–1.11.2) → **G2** money
(**closed**, v1.12.0) → **G4** partner inventory view (**closed**, v1.13.0) → **G5** listing
photographs (**closed**, v1.14.0) → **G7** demo hygiene (**closed**, v1.15.0) → **G8** Phase-1 security pack
(**MFA closed** v1.16.0, **sessions closed** v1.17.0, **usage meters closed** v1.18.0, **KMS envelope closed** v1.19.0, **shared state closed** v1.20.0 — G8 complete) → **G6** Postgres gate (**closed** locally v1.21.0 + hosted Neon branch v1.25.0) → **G3** Neon Functions deploy + live cloud proofs (**closed**, v1.25.0 — **all G1–G8 closed**).

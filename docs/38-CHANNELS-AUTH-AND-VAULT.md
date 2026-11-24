# 38 · Sprint S1 Carrier Adapters, Managed Neon Auth & Vault Runtime (`v1.27.0`)

This document records the architecture, protocol contracts, security invariants,
and live cloud proofs for **`v1.27.0`**, closing:

1. **`PLANS.md` Backend plan — Neon #2** — Managed **Neon Auth** session bridge
   (`app/neon.py`, `app/api.py`, `app/server.py`), proved end-to-end against live
   Neon Auth (`ap-southeast-1`).
2. **`docs/14` #1 (runtime half)** — In-memory HTTPS **Secret-Manager / Vault**
   puller (`app/config.py`) supporting **Doppler**, **Infisical v3**, and
   **HashiCorp Vault KV-v2** with zero `.env` disk writes.
3. **`PLANS.md` Sprint S1** — Production **WhatsApp Cloud API v21.0** (24-hour
   customer-service window + templates + signed webhooks), **TRAI DLT SMS**
   (19-digit template registry + `{#var#}` pattern check + signed DLR webhook),
   and **Voice Telephony** (`Sarvam` / `ElevenLabs` + signed recording webhook
   into Neon S3 `recordings/tenants/<tid>/…`).
4. **`PLANS.md` §9 G2 follow-up** — Live outbound **Razorpay `/v1/orders`**
   Basic-Auth order creation (`app/channels.py`, `app/api.py`).

---

## 1. Critical Cloud Catch — Neon Auth (Better Auth) `HTTP 200 null`

During live endpoint verification against `NEON_AUTH_BASE_URL` on Neon Cloud:

| Endpoint | HTTP Status (invalid token) | Body |
| --- | ---: | --- |
| `GET /users/me` *(Stack Auth)* | `404` | Not Found |
| `GET /v1/users/me` *(Stack Auth)* | `404` | Not Found |
| `GET /get-session` *(Better Auth)* | **`200`** | **`null`** |

Neon Cloud's managed auth service runs **Better Auth** (`/get-session`,
`/sign-up/email`, `/sign-in/email`, `/sign-out`). Because Better Auth returns
**`HTTP 200` with JSON body `null`** when unauthenticated, naive HTTP status
checking would either crash (`NoneType`) or cause an authentication bypass.

`app/neon.py` (`_normalise_neon_auth_payload` + `verify_session_token`) enforces:

- `isinstance(payload, dict)` with non-empty `user.id`, `user.email`, and active
  `session.id` (plus ISO `session.expiresAt > now`), returning `None` on `200 null`.
- `30s` bounded introspection cache keyed by `SHA-256("re-neon-auth:" + token)`
  (never storing raw tokens in cache keys), evicted immediately on
  `POST /api/v1/auth/logout` alongside upstream `POST {NEON_AUTH_BASE_URL}/sign-out`.
- **`RE_AUTH_MODE=neon`** disables local password sign-in/sign-up (`403 LOCAL_AUTH_DISABLED`)
  and rejects local HMAC tokens on both `/api/v1/**` and `/api/v1/events` SSE so
  a deployment runs **one auth system, not two**.

---

## 2. Runtime Secret-Manager / Vault Puller (`docs/14` #1)

`app/config.py:load_from_secret_manager()` hydrates secrets into `_VAULT_VALUES`
at boot when `RE_SECRET_MANAGER_URL` + `RE_SECRET_MANAGER_TOKEN` are set:

- **Precedence:** `process env` > `vault` (in-memory) > `.env.local` > `.env`.
- **Supported JSON dialects:** Doppler (`{"secrets": {"K": {"computed": "V"}}}` or
  flat JSON), Infisical v3 (`{"secrets": [{"secretKey", "secretValue"}]}`), and
  HashiCorp Vault KV v1/v2 (`{"data": {"data": {...}}}`).
- **Production fail-closed:** `RE_ENV=production` refuses plain `http://` vault
  URLs and aborts startup if a configured secret manager fails to respond.
- **Auditability:** `GET /api/v1/security/secrets` reports `"source": "vault"` and
  `secret_manager` metadata without ever exposing secret values.

---

## 3. Phase 1 S1 Carrier & Payment Adapters (`app/channels.py`)

| Surface | Endpoints | Compliance & Security Invariants |
| --- | --- | --- |
| **WhatsApp Cloud API v21.0** | `POST /api/v1/inbox/{cid}/send`, `GET/POST /api/v1/channels/whatsapp/templates`, `GET/POST /api/v1/webhooks/whatsapp` | Enforces Meta's **24-hour customer-service window** (`422 WHATSAPP_WINDOW_CLOSED` on free-form text >24h after buyer's last inbound message; approved `template_name` allowed anytime); plain-text `hub.challenge` echo; `X-Hub-Signature-256` HMAC over `ctx.raw` bytes; inbound `phone_hash` blind-index lookup + `sent`→`delivered`→`read` status updates. |
| **TRAI DLT SMS** | `POST /api/v1/inbox/{cid}/send`, `GET/POST /api/v1/channels/sms/templates`, `POST /api/v1/webhooks/sms` | Validates 12–20 digit `dlt_template_id`, 6-char `sender_id` header (`SNRSUN`), and anchored `{#var#}` pattern regex (`422 DLT_TEMPLATE_MISMATCH`) before carrier submission; signed DLR webhook. |
| **Voice Telephony** | `POST /api/v1/contacts/{cid}/voice-call`, `POST /api/v1/webhooks/voice` | Enforces DPDP consent, TRAI quiet hours (`451`), `voice_minutes` quota (`402`), and `ai_disclosure_required=True`; signed completion webhook archives WAV/MP3/transcript to Neon S3 `recordings/tenants/<tid>/<act_id>.*`. |
| **Razorpay Orders** | `POST /api/v1/payments/orders` | Live Basic-Auth `POST /v1/orders` when `RAZORPAY_KEY_ID` + `RAZORPAY_KEY_SECRET` are set, echoing `tenant_id`/`deal_id`/`unit_id`/`kind` in `notes`; fails closed `502 PAYMENT_PROVIDER_ERROR` on upstream failure; honest `"simulated"` mode when unset. |
| **Readiness Overview** | `GET /api/v1/channels/status` | Per-channel `"live"` vs `"simulated"` mode, template counts, and auth mode — zero secret material exposed. |

---

## 4. End-to-End Proof Results (`scripts/proof_channels_and_auth.py`)

Executed against live Neon Auth (`ap-southeast-1`) + live Neon S3 (`projectassets`) +
loopback carrier/vault protocol servers:

- **`17/17 PASS`** (`0` secret leaks, `0` residual S3 proof objects):
  - `vault_loader_doppler`, `vault_loader_infisical`, `vault_loader_vault`, `vault_loader_prod_https_enforced`
  - `neon_auth_rejects_200_null`, `neon_auth_live_introspection`, `neon_auth_mode_disables_local_login`, `neon_auth_mode_authenticates_api`, `neon_auth_logout_revokes_upstream`
  - `whatsapp_24h_window_blocks_freeform`, `whatsapp_template_live_dispatch`, `whatsapp_webhook_challenge`, `whatsapp_webhook_hmac_inbound_and_receipt`, `whatsapp_24h_window_opens_after_inbound`
  - `trai_dlt_sms_gate_dispatch_and_dlr`
  - `voice_call_and_recording_webhook` (`stored_in=object_storage`)
  - `razorpay_live_order_creation`

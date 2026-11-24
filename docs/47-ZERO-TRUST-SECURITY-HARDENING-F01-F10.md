# 47 — Zero-Trust Security Hardening (`F-01` .. `F-10`, `v1.36.0`)

Remediates all ten findings from the offensive-minded architecture & code-loophole
audit, incorporating lessons from the October 2026 `PHP2Shell` cross-worker
memory disclosure (`CVE-2026-109618`..`115560`), `Cairn` autonomous probing,
`LiteLLM` gateway compromise, and `Apex Accelerator` (`REF8801`) supply-chain
intrusions.

## Controls Shipped

| ID | Severity | Remediation | Primary Modules |
|---|---|---|---|
| **F-01** | Critical | Per-tenant HKDF DEKs (`v2t:tenant-dek:` via `sec.seal_tenant` / `sec.derive_tenant_key`) + Stateless Per-Request Postgres RLS Read Mode (`RE_STATELESS_READS=1`) keeping zero tenant CRM/PII collections cached in `Store.db` RAM | `app/security.py`, `app/db.py`, `app/store.py` |
| **F-02** | High | `sec.safe_urlopen` with single-resolution IP-pinned TCP/TLS dialer (`server_hostname` SNI preserved), unconditional `301..308` redirect blocking, and mandatory DNS verification on AI gateways | `app/security.py`, `app/connectors.py`, `app/channels.py` |
| **F-03** | High | Reject empty/short custom webhook secrets (`403 WEBHOOK_SECRET_NOT_CONFIGURED`), verify optional `X-Webhook-Timestamp` (`±300s`), and enforce atomic `600s` replay deduplication (`409 WEBHOOK_REPLAY_DETECTED`) | `app/api.py`, `app/security.py` |
| **F-04** | High | `Handler.timeout = 15s` Slowloris defense, bounded `_HTTPServer` request semaphore (`503 SERVICE_BUSY` on saturation), and coalesced 60s-deduplicated `/f/<slug>` view buffering | `app/server.py` |
| **F-05** | High | `sec.sanitize_outbound_ai_messages` scrubbing credentials/Aadhaar/PAN/cards and wrapping risky prompts in `<untrusted_input>` boundaries before LLM gateways + `sec.sanitize_ai_output` stripping markdown image exfil beacons | `app/security.py`, `app/connectors.py` |
| **F-06** | Medium-High | Native `application/x-www-form-urlencoded` handling on `/api/v1/public/funnel/<slug>/submit` with accessible HTML confirmation + HMAC-signed timestamped `_ftok` anti-bot token | `app/server.py`, `app/api.py`, `app/security.py` |
| **F-07** | Medium-High | Workspace-scoped `sec.blind_index(..., tenant_id=tid)` preventing cross-tenant `phone_hash`/`email_hash` correlation, paired with zero-downtime `sec.find_by_blind_index` dual-read migration | `app/security.py`, `app/api.py`, `app/compliance.py`, `app/phase4_platform.py` |
| **F-08** | Medium | PDF `#XX` hex-escaped token normalization (`/Java#53cript`), bounded `/FlateDecode` stream decompression inspection (`<= 512 KB/stream`, `<= 50:1` ratio), and PNG/JPEG/WebP polyglot blocking | `app/connectors.py` |
| **F-09** | Medium | `_frame_ancestors_directive` blocking wildcard framing in `CFG.PRODUCTION`, honoring only explicit `RE_PREVIEW_FRAME_ANCESTORS` HTTPS origins, and forcing `DENY` / `'none'` on `/api/v1/auth/*`, `/security/*`, `/stepup/*` | `app/server.py` |
| **F-10** | Medium | Exact semver dependency pinning in `package.json` & `package-lock.json` + `.npmrc` (`save-exact=true`, `ignore-scripts=true`, `audit=true`) | `package.json`, `.npmrc`, `app/security_hardening.py` |

## API & Verification

- `GET /api/v1/security/hardening-status` — live control posture across `F-01`..`F-10`.
- `POST /api/v1/security/hardening-self-test` — executes 10 live non-destructive verification probes (`10/10` pass).
- Proof: `python3 scripts/proof_security_hardening_v136.py` (`npm run hardening:proof`).

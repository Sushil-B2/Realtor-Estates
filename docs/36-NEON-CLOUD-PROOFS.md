# 36 — Live Neon Cloud Proofs: Deployed Functions (G3), Hosted Postgres Gate (G6) & Branch Restore RTO (`v1.25.0`)

Closes the three live Neon Cloud milestones in `PLANS.md`:
1. **Gap G3 (`PLANS.md` §9)** — all three Neon Functions and both cloud triggers deployed to `ap-southeast-1` (`broad-sound-07229115`, branch `production` / `br-patient-fire-aouhmkxr`) and proved end-to-end (`12/12 PASS`).
2. **Hosted G6 pre-release check (`PLANS.md` §9)** — `scripts/verify_postgres.py` proved **`21 passed, 0 skipped`** on a real disposable hosted Neon branch (`PostgreSQL 18.7` through Neon's `-pooler`).
3. **`docs/13` follow-up #5** — point-in-time Neon branch restore + full cryptographic SHA-256 audit-chain verification measured at **`7.35s` total RTO** (`9.86s` cold) across `1,505` audit rows and `28` tenants.

---

## 1. Secret intake hygiene (`1time.io` → `.secrets/neon-api-key`)

The project-scoped Neon API key arrived via a single-view client-side AES-256-GCM
link (`1time.io`), was redeemed directly into `.secrets/neon-api-key` (`mode 0600`,
git-ignored) without printing a single character to stdout/stderr, and was verified
immediately burned (`404` on second read).

---

## 2. Gap G3 closed — Deployed Neon Functions & Cloud Triggers

### Deployed inventory (`nodejs24` · `2048 MiB` · `ap-southeast-1`)

| Slug | Source | Invocation URL | Cloud Trigger |
|---|---|---|---|
| `propertyphotos` | `functions/property-photos.ts` | `https://br-patient-fire-aouhmkxr-propertyphotos.compute.c-2.ap-southeast-1.aws.neon.tech/` | `on-property-photo` (`trigger-e04867e9-6d5d-400a-975d-9d8a8d07ee77`, `storage_object_created` on `projectassets`, prefix `properties/`) |
| `compliancesweep` | `functions/compliance-sweep.ts` | `https://br-patient-fire-aouhmkxr-compliancesweep.compute.c-2.ap-southeast-1.aws.neon.tech/` | `nightly-compliance` (`trigger-599200bd-7afb-4fac-b584-03b95ba9b82b`, `schedule` `0 2 * * *` UTC / `07:30` IST) |
| `paymentswebhook` | `functions/payments-webhook.ts` | `https://br-patient-fire-aouhmkxr-paymentswebhook.compute.c-2.ap-southeast-1.aws.neon.tech/` | Public HTTPS ingress (HMAC-SHA256 `x-razorpay-signature` / `x-webhook-signature`) |

### Live cloud verification (`scripts/proof_deployed_functions.py --fire-sweep` — 12/12 PASS)

1. **`propertyphotos`**:
   - `GET` readiness probe returns `200`.
   - External forged `POST` with client-supplied `x-neon-trigger-invocation-id` is refused (`401 missing_header`) because Neon's cloud compute gateway strips trigger headers on external ingress.
   - Uploading a real `640×480` JPEG to `projectassets/properties/<id>/cloud-proof.jpg` fires the cloud `on-property-photo` trigger, runs `sharp` on `linux-arm64`, writes `card`, `hero`, and `thumb` `.webp` objects to S3, and upserts all 3 rows into `re_property_media` (all cleaned up in `finally`).
2. **`paymentswebhook`**:
   - `GET` readiness reports `configured: true`.
   - Unsigned and bad-HMAC `POST` deliveries return `401`.
   - Valid HMAC-SHA256 delivery returns `200 {"duplicate": false}`, persists exact paise (`1250000`) to `re_webhook_events`, and deduplicates replay (`200 {"duplicate": true}`) via `re_webhook_events_dedupe_idx` (cleaned up via `RE_ADMIN_DATABASE_URL`, confirming `re_app` cannot `DELETE` from `re_webhook_events`).
3. **`compliancesweep`**:
   - `GET` readiness returns `200`; external forged schedule `POST` returns `401 missing_header`.
   - Creating a live cloud schedule trigger fires `compliancesweep` on Neon's scheduler, verifies the entire Postgres SHA-256 audit chain (`audit.ok == true`), and records `status = 'ok'` in `re_compliance_runs` (trigger and proof row cleaned up in `finally`).

---

## 3. Five real defects caught only by running against real Neon Cloud

| # | Where | Defect | Fix |
|---|---|---|---|
| 1 | `neon.ts` | `propertyphotos` imported `sharp` (a native `.node` addon) without `externalPackages: ["sharp"]`, which bundles cleanly under esbuild and crashes at cloud invoke | Added `externalPackages: ["sharp"]` so `@neon/config` stages the `linux-arm64` glibc binary |
| 2 | `neon.ts` & `scripts/deploy_functions.sh` | Custom function secrets (`PAYMENTS_WEBHOOK_SECRET`, `NEON_STORAGE_*`) were in `.env.local` without `FunctionDef.env` declarations, leaving `process.env.PAYMENTS_WEBHOOK_SECRET` unset in cloud; `config plan` lacked `--env` | Declared `FunctionDef.env` (`required: "default-branch"`) in `neon.ts` and passed `--env "$ENV_FILE"` to `config plan` |
| 3 | `scripts/deploy_functions.sh` | `neon deploy` defaults to `--env-pull=true`, which overwrites the local `.env` (`RE_MASTER_KEY`, least-privilege `re_app` `DATABASE_URL`) after deploy | Added `--no-env-pull -y` |
| 4 | `scripts/proof_deployed_functions.py` | Pre-cloud draft tried to sign external `POST` trigger calls with `NEON_TRIGGER_SECRET` and signed `paymentswebhook` with `NEON_TRIGGER_SECRET` instead of `PAYMENTS_WEBHOOK_SECRET` | Rewrote proof to test Neon's real edge header stripping (`401 missing_header`) + real S3 upload & cron triggers + `PAYMENTS_WEBHOOK_SECRET` |
| 5 | `scripts/verify_postgres.py` & `tests/test_postgres.py` | Sampled `pg_backend_pid()` sequentially in `autocommit` mode; through Neon's `-pooler` (PgBouncer), the first connection returned its backend to the pool in `<1 ms` and the second received the **same** idle PID, falsely classifying Neon `-pooler` as single-session PGlite (`20 passed, 1 skipped`) | Sample `pg_backend_pid()` while **both** connections hold simultaneous open read transactions (`with a.transaction(), b.transaction():`) → **`21 passed, 0 skipped`** |

---

## 4. Point-in-time branch restore RTO & hosted G6 (`scripts/proof_neon_cloud_rto.py` — 5/5 PASS)

Measured against project `broad-sound-07229115` (`ap-southeast-1`, parent `production` / `br-patient-fire-aouhmkxr`):

| Phase | Run 1 (cold) | Run 2 (warm) | What is proved |
|---|---|---|---|
| 1. `POST /branches` + RW endpoint | `0.91s` | `0.99s` | Instant copy-on-write branch creation on Neon storage |
| 2. TLS Postgres `SELECT 1` ready | `3.36s` | `3.47s` | Compute endpoint booted and accepting SSL + channel-bound connections |
| 3. Full `re_audit` SHA-256 verification (`1,505` rows, `28` tenants) | **`9.86s`** | **`7.35s`** | **End-to-end Recovery Time Objective (RTO < 10s)** with zero broken links |
| 4. Hosted `scripts/verify_postgres.py` on disposable Neon branch | — | **`21 passed, 0 skipped`** | Full schema, RLS (`re_app` `NOBYPASSRLS` + `FORCE ROW LEVEL SECURITY`), function tables, and 8-thread concurrent `append_audit` serialization proved through Neon's `-pooler` (`PostgreSQL 18.7`) |
| 5. `DELETE /branches/{id}` cleanup | `HTTP 200` | `HTTP 200` | Ephemeral drill branch destroyed cleanly |

---

## 5. Proof commands

```bash
scripts/deploy_functions.sh --proof
python3 -u scripts/proof_deployed_functions.py --fire-sweep \
  --photos-url  "$NEON_PHOTOS_FUNCTION_URL" \
  --webhook-url "$NEON_WEBHOOK_FUNCTION_URL" \
  --sweep-url   "$NEON_SWEEP_FUNCTION_URL"
python3 -u scripts/proof_neon_cloud_rto.py
```

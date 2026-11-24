# 12 — Neon / Postgres Backend

> Status: **connected.** The production database is
> `ep-wandering-waterfall-aoniw0i3-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb`
> (PostgreSQL 18.7, pooled endpoint, TLS required), the app boots on it, and the
> checks below were run against it — not against a stand-in. The last section lists
> exactly what is still unverified (AI Gateway key, Realtime, Functions).

---

## 1. What changed

| Before | Now |
|---|---|
| All state in `data/store.json`, flushed on write | Same code path, but `DATABASE_URL` switches `Store` to Postgres: boot loads from the DB, writes go through it, `store.json` is never written |
| No migrations | `db/001_init.sql` + a checksum ledger (`re_migrations`); `migrate()` is idempotent |
| Audit chain in JSON only | `re_audit` table with an `BEFORE UPDATE OR DELETE` trigger — history cannot be edited by the application, or by anyone with SQL |
| No tenant isolation at the database layer | `db/002_rls.sql` (opt-in, `RE_DB_RLS=1`): row-level security keyed on `current_setting('app.tenant_id')` |
| No provider integrations | `app/neon.py`: AI Gateway, Auth introspection, object storage (SigV4 presign), Realtime, Functions — all optional, all fail-soft |

Files: `app/db.py` (backend), `app/store.py` (dual-mode), `db/*.sql`,
`tests/test_postgres.py`, `tests/test_neon_services.py`, `app/neon.py`,
`scripts/pg-local.mjs` (local stand-in).

## 2. Environment contract

Resolution order is **process env > `.env.local` > `.env`** (`app/config.py`).

| Variable | Effect |
|---|---|
| `DATABASE_URL` | Activates Postgres. `POSTGRES_URL` and `NEON_DATABASE_URL` are accepted as aliases. `postgres://` is normalised to `postgresql://`, hosted hosts get `sslmode=require`, `connect_timeout=10` is appended |
| `RE_DB` | `auto` (default) = Postgres if `DATABASE_URL` is set, else JSON. `json` forces the file store |
| `RE_DB_STRICT` | `1` = fail boot instead of silently falling back to JSON |
| `RE_DB_RLS` | `1` = also apply `db/002_rls.sql` |
| `RE_DATA_DIR` | Directory for the JSON store (`data/` by default) |
| `NEON_AI_GATEWAY_URL` / `_KEY` / `_MODEL` | Agent model calls route through the gateway; on any failure the deterministic local model is used |
| `NEON_AUTH_BASE_URL` | Bearer-token introspection for Neon Auth sessions |
| `NEON_STORAGE_*` | S3-compatible bucket for exports/recordings |
| `NEON_REALTIME_URL` / `_TOKEN` | Realtime fan-out for the inbox |
| `NEON_FUNCTIONS_URL` | Deployed Neon Functions base URL (the `hello` smoke function) |

No value from any of these is ever returned by an API response, written to a log,
or placed in a model prompt. `GET /api/v1/security/services` reports presence as
booleans only; `GET /api/v1/security/secrets` reports presence + source +
one-way fingerprint.

## 3. Schema

```sql
re_documents (collection text, tenant_id text, id text, doc jsonb,
              created_at bigint, updated_at bigint,
              primary key (collection, tenant_id, id))
              + GIN index on doc

re_audit     (seq bigserial, tenant_id text, id text unique, actor, action,
              target, meta jsonb, at bigint, prev_hash, hash)
              + trigger re_audit_no_update BEFORE UPDATE OR DELETE

re_migrations(version text primary key, applied_at timestamptz, checksum text)
```

`re_documents` is deliberately a document table: the domain objects in
`docs/03-DATA-MODEL.md` are tenant-scoped JSON, and a per-collection table today
would be five thousand lines of DDL that the product has not yet earned. The
primary key already carries `tenant_id`, so a later normalisation can be done
collection by collection without a data migration.

## 4. Verification — what was actually measured

```bash
npm run db:connect                # connect + migrate + probe (reads .env.local)
npm run verify                    # 45 tests, secret scan
RE_TEST_DATABASE_URL="$(grep ^DATABASE_URL= .env.local | cut -d= -f2-)" npm run verify:pg
```

Against the Neon project (11 Nov 2026):

| Claim | Evidence |
|---|---|
| Connects over TLS to the pooled endpoint | `ping ok=True 464 ms`, server `PostgreSQL 18.7 (a21afb5) aarch64` |
| Migrations 001–003 applied idempotently | `✓ applied 001_init.sql`, then `none (already up to date)` on re-run |
| App boots on Neon and reloads from it | live `GET /healthz` → `{"backend":"postgres","durable":true,"documents":225}`, `store.json written: False` |
| Reads and writes work end-to-end | `/api/v1/contacts` 200, `POST /api/v1/contacts` 200 `cnt_47a95cca7f`, `/api/v1/tenant/audit` `chain.ok true` |
| Audit is append-only in the database | `audit trigger present=True update_blocked=True delete_blocked=True`; the blocked writes are retried inside SAVEPOINTs |
| Row-level security actually filters | `tests/test_rls.py` (4 tests) — `re_app` scoped to tenant A sees 210 of 214 rows, sees 0 with no `app.tenant_id`, and a cross-tenant INSERT is rejected |
| Object-storage presign is correct | presigned GET → **HTTP 404** (no such key), not 403 SignatureDoesNotMatch; a wrong signature would be 403 |
| Restart survival, tenant scope, idempotency | `tests/test_postgres.py` (7 tests) — green against Neon and against the local stand-in |

### Defects this integration found and fixed

1. **A single failed statement wedged the process.** Every backend method rolls
   back on error; `_rollback()` reconnects when the session cannot be recovered.
2. **`prepared statement "_pg3_0" already exists`.** psycopg's server-side
   prepares break against PGlite and against transaction-mode poolers. Connections
   open with `prepare_threshold=None`.
3. **Session state leaking through the pooler.** A `SET ROLE re_app` issued by an
   earlier client came back on a *new* connection to the `-pooler` endpoint, so the
   app briefly ran as the restricted role and DDL failed with "permission denied for
   schema public". Every connection now starts with `reset role; reset all`.
4. **An empty table made the audit probe lie.** `UPDATE … WHERE seq = (SELECT min(seq) …)`
   matches zero rows on a fresh database, so no row trigger fires and the probe
   reported "not append-only". It now checks `pg_trigger` *and* writes a canary row
   before attempting the tamper.
5. **`"assets"` was treated as a missing value.** The 16-character placeholder
   heuristic (right for keys) flagged a real bucket name; non-secret fields now use
   `min_len=1`.
6. **Quoted/`,`-suffixed values.** The uploaded env file wrapped values in quotes and
   came from a TS object dump; `.env.local` is now written as plain dotenv and
   `normalise_dsn` strips quotes defensively.

## 5. Still unverified — read before trusting the rest

| Item | State | Why / what to do |
|---|---|---|
| AI Gateway calls | **not exercised** | `NEON_AI_GATEWAY_URL` is set but `NEON_AI_GATEWAY_KEY` is empty. Agents fall back to the deterministic model and record `model_provider: local-deterministic`. Add the key to `.env.local`; `python3 -m app.neon` then smoke-tests it |
| Neon Auth introspection | **verified (`v1.27.0`)** | Fixed `app/neon.py:verify_session_token` for live Neon Better Auth `GET /get-session` (rejecting `HTTP 200 null` unauthenticated payloads) + `RE_AUTH_MODE=neon` single-auth bridge; proved live against `NEON_AUTH_BASE_URL` on `ap-southeast-1` (`scripts/proof_channels_and_auth.py`, `docs/38`) |
| Realtime | **verified (`v1.26.0`)** | `app/realtime.py` + `app/shared_state.py` (`Redis` RESP2 / `SharedFiles` / `_MemoryAdapter` event rings) replace per-socket `STORE` polling; proved across 2 server processes & **100 concurrent SSE inboxes** (`260/260` frames in `240.4 ms`, `p95 = 77.9 ms`, `0` cross-tenant leaks) in `scripts/proof_realtime_inboxes.py` (`docs/37`) |
| Neon Functions | **deployed & proved (`v1.25.0`)** | `propertyphotos`, `compliancesweep`, and `paymentswebhook` + 2 cloud triggers deployed on `ap-southeast-1` (`12/12` live cloud checks in `scripts/proof_deployed_functions.py --fire-sweep`, `docs/36`) |
| Object storage upload path | **verified (`v1.26.0`)** | Pure-stdlib SigV4 `put_object` / `head_object` / `delete_object` (`app/neon.py`) + `POST/GET /exports` and `GET /recordings/{id}` proved live against `projectassets` (`13/13` checks in `scripts/proof_storage_uploads.py`, `docs/37`) |
| Backup / PITR / branch restore | **verified (`v1.25.0`)** | `scripts/proof_neon_cloud_rto.py` restored a point-in-time branch via Neon v2 API, verified all `1,505` `re_audit` rows across `28` tenants (**`7.35 s` warm / `9.86 s` cold RTO**), and ran `verify_postgres.py` (`21/21 PASS`, `docs/36`) |
| Cost behaviour under load | **hardened (`v1.26.0`)** | Anonymous `/healthz` skips `backend.ping()` (`probe=False`) and `_static()` serves from RAM with `ETag`/`304 Not Modified` so page loads and liveness checks never wake scaled-to-zero compute (`docs/37`). Watch the Neon usage page under real tenant traffic before promising a price |
| Local stand-in parity | **partial** | PGlite accepted the same DDL and enforced RLS, but it has no TLS and a single session — it is a convenience, not a mirror |

## 6. Activation runbook — three paths, pick one

**Path A was used**: the operator supplied the Neon credentials, they were written to
`.env.local` (mode `0600`, git-ignored) as plain dotenv, and `npm run db:connect`
verified them. Paths B and C remain available for a different machine or a
re-provision.

### Path A — you already have a DSN (used here)

Create the project in the Neon console, copy the **pooled** connection string, and
put it in a file rather than in a chat message:

```bash
printf 'DATABASE_URL=%s\n' 'postgresql://user:pass@ep-xxx.aws.neon.tech/neondb?sslmode=require' > .env.local
chmod 600 .env.local
npm run db:connect        # validates, migrates, probes the audit trigger, re-writes .env.local
npm run build             # 45 tests
RE_TEST_DATABASE_URL="$(grep ^DATABASE_URL= .env.local | cut -d= -f2-)" npm run verify:pg
```

`scripts/connect-db.py` normalises the URL (`postgres://`→`postgresql://`, forced
`sslmode=require` on `*.neon.tech`), applies `db/001_init.sql` idempotently, refuses
to continue if the append-only audit trigger is missing, writes the result with mode
`0600`, and never prints the password. Flags: `--rls-apply`, `--seed`,
`--no-write`, `--env-file`.

### Path B — API key, non-interactive deploy

```bash
# add NEON_API_KEY=… to .env, or export it in the shell used for these two commands
NEON_API_KEY="$KEY" npx -y neon@latest deploy -y --update-existing   # provisions + pulls env
npm run db:connect && npm run build
```

### Path C — interactive

```bash
npx -y neon@latest login && npx -y neon@latest link && npx -y neon@latest deploy -y
```
Then continue at `npm run db:connect`.

### After any path

1. `python3 -m app.db status` → `dsn_configured: true`, host `ep-…neon.tech`.
2. Restart the app; `/healthz` must say `"backend": "postgres"`.
3. `RE_DB_RLS=1 npm run db:migrate` and a cross-tenant probe — RLS is the only
   isolation guarantee that survives an application bug.
4. `RE_TEST_DATABASE_URL=<dsn> npm run verify:pg` — the same 7 tests, now against Neon.
5. Rotate the DSN in `.env.local` if it was ever visible in a shell history,
   screenshot or chat (`scripts/rotate_keys.py`, `docs/11 §6`).

## 7. Cost note

Neon's free tier covers a demo project (0.5 GB storage, one primary compute that
scales to zero). The scale risk is not storage but **compute wake-ups**: a PWA
polling `/healthz` every 30 s keeps the compute hot and burns the monthly hours.
The client already polls at 60 s with backoff, and `/healthz` is a `SELECT 1`.
For a live deployment put the API behind a CDN-cached static shell so page loads
never touch the database, and batch `/api/v1/inbox` reads (they are one query per
collection today).

## 8. Honest residual risk

- A DSN in `.env` is readable by anyone who achieves code execution as the app
  user, and by anyone who can read the file (mode `0600` narrows this, it does
  not close it). Rotation is `scripts/rotate_keys.py` + Neon's console;
  per-tenant DEKs and a KMS are specified in `docs/11 §7` and not built.
- The audit trigger protects history from `UPDATE`/`DELETE`. It does not stop a
  superuser from dropping the trigger. `chain.ok` in `/api/v1/tenant/audit`
  detects rewriting after the fact; it cannot prevent it.
- Multi-tenant isolation is enforced in the application today. RLS exists but is
  unexercised, so a single missed `where tenant_id = …` is still a real risk until
  step 7 above has been run against a real server with tests.

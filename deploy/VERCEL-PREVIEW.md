# Dedicated Realtor Estates preview

Authorized target ONLY: realtor-estates / prj_E7SGbITHWxBXaGpVPYQgLqd9UUtY, team team_s0uBpvnoysN85mFkEn2qqjO7. No sibling project or team-wide settings may be changed.

## Prepared locally, not cloud-validated

api/index.py adapts the existing BaseHTTPRequestHandler for Vercel. vercel.json routes requests to it, includes app/web/db and selects sin1. requirements.txt supplies the PostgreSQL driver. Automated local checks cover unconfigured HTTP 503 and configuration shape only; they do not certify a configured cloud deployment, packaging, authentication, persistence or SSE behavior.

The candidate runtime refuses to initialize without explicit production mode, strict PostgreSQL mode, separate runtime and maintenance connection settings, a master key and migration skipping. It requires existing tenant data and does not seed an apparently empty Preview database. Failure to open a configured maintenance connection fails closed rather than using the runtime connection for hydration. Startup failures yield a generic 503; neither this guard nor a visible tenant proves the maintenance role can read all required data. Cloud behavior and privilege checks remain unverified.

Serverless instances do not share memory. The adapter therefore forces per-request database reads for tenant records (`RE_STATELESS_READS=1`) and database-checked sessions (`RE_FRESH_SESSIONS=1`). Each authenticated request re-reads its session record. Revocation, touch and refresh rotation are single-statement updates, and rotation is compare-and-set. A local real PostgreSQL 17 test with the project migrations and the restricted runtime role showed the old boot-snapshot mode hiding a contact created on another instance and accepting a replayed refresh token. The candidate settings fixed both in that test. This local test does not prove Neon pooling, Vercel routing, latency or the deployed configuration. Users, ingest keys and other routing records are still cached per instance, and access tokens already issued remain valid until their sessions are checked or they expire.

Running the full test suite on that local database in this mode found two older paths that read only the in-memory copy: usage metering and proof-workspace teardown. Both now read through the store. The remaining failures in that run were tests that expect the file store, one test whose fake backend has no session read, and one test that depends on list order. A manual check confirmed the agent approval guardrail held outbound actions for every seeded agent in both modes.

## Before enabling the backend

1. Upload these changes to integration/crm-and-marketing-website only. Never merge or deploy main automatically.
2. Use a separate preview database/storage environment. Provision schema using the project's documented migration process before setting migration skipping. No schema migration has been run by this change.
3. Enter DATABASE_URL, RE_ADMIN_DATABASE_URL and RE_MASTER_KEY directly into this project's dashboard as Sensitive preview variables. Use a restricted runtime role and a separate maintenance connection to the same isolated Preview database. Never paste values in chat, source, commands or connector arguments. Do not reuse another project's secrets or production data without explicit review.
4. Required non-secret settings: RE_ENV=production, RE_DB=postgres, RE_DB_STRICT=1, RE_SKIP_BOOT_MIGRATE=1. The adapter disables in-process automation workers. Scheduled automation is not provided by this adapter.
5. Deploy the verified integration branch as a preview in the authorized project only. Keep default preview access protection enabled. A configuration-gated 503 is expected before setup and is not a working CRM release.
6. Validate packaging, private login, cookie/CSRF behavior, tenant isolation, durable writes and public funnel/deep-link routing. Streaming may be limited by the 60-second invocation duration; verify client reconnection. Test client-IP/rate-limiting behind Vercel before public exposure; do not trust arbitrary forwarded headers.
7. Production promotion and main merge remain separate steps requiring review.

Deployment state must be checked separately. A successful static build does not prove backend readiness or multi-instance session safety.

# Dedicated Realtor Estates preview

Authorized target ONLY: realtor-estates / prj_E7SGbITHWxBXaGpVPYQgLqd9UUtY, team team_s0uBpvnoysN85mFkEn2qqjO7. No sibling project or team-wide settings may be changed.

## Prepared locally, not cloud-validated

api/index.py adapts the existing BaseHTTPRequestHandler for Vercel. vercel.json routes requests to it, includes app/web/db and selects sin1. requirements.txt supplies the PostgreSQL driver. Automated local checks cover unconfigured HTTP 503 and configuration shape only; they do not certify a configured cloud deployment, packaging, authentication, persistence or SSE behavior.

The runtime refuses to initialize without explicit production mode, strict PostgreSQL mode, a database URL, a master key and migration skipping. Startup failures yield a generic 503, not credentials or a JSON/demo-store fallback. The shared server no longer prints raw exception tracebacks on request failures.

## Before enabling the backend

1. Upload these changes to integration/crm-and-marketing-website only. Never merge or deploy main automatically.
2. Use a separate preview database/storage environment. Provision schema using the project's documented migration process before setting migration skipping. No schema migration has been run by this change.
3. Enter DATABASE_URL and RE_MASTER_KEY directly into this project's dashboard as Sensitive preview variables. Never paste values in chat, source, commands or connector arguments. Configure any additional application/provider requirements there too. Do not reuse another project's secrets or production data without explicit review.
4. Required non-secret settings: RE_ENV=production, RE_DB=postgres, RE_DB_STRICT=1, RE_SKIP_BOOT_MIGRATE=1. The adapter disables in-process automation workers. Scheduled automation is not provided by this adapter.
5. Deploy the verified integration branch as a preview in the authorized project only. Keep default preview access protection enabled. A configuration-gated 503 is expected before setup and is not a working CRM release.
6. Validate packaging, private login, cookie/CSRF behavior, tenant isolation, durable writes and public funnel/deep-link routing. Streaming may be limited by the 60-second invocation duration; verify client reconnection. Test client-IP/rate-limiting behind Vercel before public exposure; do not trust arbitrary forwarded headers.
7. Production promotion and main merge remain separate steps requiring review.

The new project currently has no Git connection and no deployment performed by this preparation. A successful static build does not prove backend readiness.

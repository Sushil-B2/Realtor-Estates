# Sentinel agent report

Target: `http://127.0.0.1:8000` · modules: authz, frontend, inject, session, transport, agent, db, xff
Checks: 47/47 passed · requests: 237 · prohibited actions refused: 0 · report redactions: 0
Audit chain: recorded (7e11600d40e4e5b9…)

No findings.

## Policy gate self-test (evidence)

| action class | action | expected | outcome |
|---|---|---|---|
| DESTRUCTIVE | `DELETE /api/v1/tenant/users/usr_1` | refused | refused (deleting or editing a human account is destructive) |
| ANALYZE | `POST /api/v1/tenant/kill-switch` | refused | refused (enabling the tenant kill switch is a destructive tenant-wide) |
| EXFILTRATE | `POST /x` | refused | refused (the agent may not send data off-host) |
| ANALYZE | `GET https://evil.example/steal` | refused | refused (host 'evil.example' is outside the authorised scope) |
| VOLUMETRIC | `GET /api/v1/contacts` | refused | refused (burst of 5000 exceeds the denial-of-service ceiling) |
| WRITE_REPORT | `/tmp/outside-sentinel.json` | refused | refused (EXFILTRATE: report path '/tmp/outside-sentinel.json' is outs) |
| WRITE_REPORT | `/home/user/realtor-estates/reports/sentinel-report.json` | refused | refused (EXFILTRATE: report content contains unredacted credential ma) |
| WRITE_REPORT | `/home/user/realtor-estates/reports/sentinel-report.json` | allowed | allowed |
| OBSERVE | `GET /api/v1/contacts` | allowed | allowed |
| EXECUTE | `POST /api/v1/contacts` | allowed | allowed |

## Every check

| id | check | result | detail |
|---|---|---|---|
| SEN-C301 | a viewer is refused privileged reads (users, audit, secrets, integrations) | PASS | 0 readable: [] |
| SEN-C306 | integration and webhook payloads carry no credential material | PASS | 0 secret-shaped value(s): [] |
| SEN-C305 | contact PII is masked for a viewer | PASS | 0 unmasked phone(s) |
| SEN-C302 | a viewer cannot create privileged objects | PASS | created in: [] |
| SEN-C303 | cross-tenant object access is denied on every collection | PASS | 0 leaked: [] |
| SEN-C304 | no tenant route answers an anonymous GET | PASS | 0 open: [] |
| SEN-C201 | no credential is written to localStorage/sessionStorage | PASS | keys=['re_offline_outbox'] |
| SEN-C202 | the service worker never caches /api/ responses | PASS | api branch is network-only |
| SEN-C203 | CSP forbids inline/eval script | PASS | csp=default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font- |
| SEN-C204 | external links use rel=noopener/noreferrer | PASS | 0 link(s) without rel=noopener |
| SEN-C205 | no eval/new Function/document.write sinks | PASS | sinks=none |
| SEN-C206 | postMessage listeners verify event.origin | PASS | listeners=[] origin-checked=False |
| SEN-C207/js/core.js.map | /js/core.js.map is not served | PASS | status=404 |
| SEN-C207/app.js.map | /app.js.map is not served | PASS | status=404 |
| SEN-C207/.git/HEAD | /.git/HEAD is not served | PASS | status=404 |
| SEN-C207/web/ | /web/ is not served | PASS | status=404 |
| SEN-C207/package.json | /package.json is not served | PASS | status=404 |
| SEN-C208 | password inputs declare autocomplete policy | PASS | 0 password input(s) without autocomplete |
| SEN-C401 | SQL/JSON injection does not reach the database | PASS | [] |
| SEN-C402 | duplicate JSON keys are handled safely | PASS | 409 |
| SEN-C403 | deeply nested JSON is rejected, not crashed | PASS | 400 |
| SEN-C405 | stored CRLF cannot inject a response header | PASS | headers=['Date', 'Content-Type', 'Content-Length', 'Content-Security-Policy', 'Strict-Transport-Security'] |
| SEN-C406 | public page rendering escapes stored HTML | PASS | escaped |
| SEN-C501 | unsigned/alg=none tokens are rejected | PASS | no forged variant accepted |
| SEN-C502 | a new session token is issued on each login | PASS | distinct |
| SEN-C503 | session cookie has HttpOnly + SameSite | PASS | re_session=[redacted]; Path=/; HttpOnly; SameSite=Strict; Max-Age=1800 |
| SEN-C504 | a token in the query string does not authenticate | PASS | 401 |
| SEN-C505 | weak passwords are refused at signup | PASS | 400 {'error': {'code': 'VALIDATION_FAILED', 'message': "'org_name' is required.", 'd |
| SEN-C601 | authenticated responses are Cache-Control: no-store | PASS | no-store, private |
| SEN-C602 | no server/framework banner is disclosed | PASS | [] |
| SEN-C603 | CORS does not reflect an arbitrary origin | PASS | ACAO=(absent) |
| SEN-C604 | unexpected verbs are refused on collection routes | PASS | [] |
| SEN-C605 | a JSON response cannot be cached as a static asset | PASS | 404 |
| SEN-C606 | funnel slug resolution cannot traverse | PASS | 404 |
| SEN-C607 | 404s do not leak a traceback or path | PASS | {"error": {"code": "NOT_FOUND", "message": "No such endpoint.", "trace_id": "trc_ac20193dd5"}} |
| SEN-C608 | unauthenticated /healthz discloses no infrastructure detail | PASS | status=200 leaked=[] |
| SEN-C701 | prompt-injection text never returns secrets | PASS | set() |
| SEN-C702 | an agent run never echoes a credential | PASS | canary present |
| SEN-C703 | irreversible tools are parked for approval, not executed | PASS | [] |
| SEN-C801 | RLS is enabled on the document table | PASS | {'re_documents': (True, True), 're_audit': (True, True)} |
| SEN-C802 | the application role cannot bypass RLS | PASS | superuser=False bypassrls=False |
| SEN-C803 | no credential is stored in any row | PASS | documents=0 audit=0 |
| SEN-C804 | contact PII is encrypted at rest | PASS | 0/0 plaintext |
| SEN-C806 | the runtime role sees nothing without a tenant context | PASS | visible=0 tenant_ctx='' |
| SEN-C805 | the audit table refuses UPDATE | PASS | update succeeded |
| SEN-C101 | spoofed X-Forwarded-For cannot dodge the login throttle | PASS | 34 attempts, statuses=[401, 429] |
| SEN-C102 | a never-seen X-Forwarded-For still hits the exhausted bucket | PASS | status=429 retry-after='266' |

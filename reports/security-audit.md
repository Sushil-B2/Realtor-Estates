# Security audit report

Generated: 2026-11-17 17:36:03 · score **100/100** · 44 checks

| id | check | severity | result | detail |
|---|---|---|---|---|
| SA-101 | secret files are 0600 (group/other cannot read) | critical | PASS |  |
| SA-102 | no secret-bearing file is tracked by git | critical | PASS | [] |
| SA-103 | secret files and source paths are not served by the web tier | critical | PASS |  |
| SA-104 | no API response contains a live secret value | critical | PASS |  |
| SA-105 | static assets contain no secrets | critical | PASS |  |
| SA-106 | error responses carry no stack trace, path or DSN | high | PASS | [] |
| SA-107 | secret-shaped input is redacted in inspection output | high | PASS | raw canary echoed back |
| SA-108 | server logs contain no secret values | high | PASS |  |
| SA-109 | no clipboard write can carry a credential | high | PASS | clipboard calls=0 risky=[] |
| SA-110 | security headers present on success and on 404 | medium | PASS |  |
| SA-111 | healthz reports the host but masks the password | high | PASS | masked |
| SB-201 | SQL injection payloads do not reach the database as SQL | critical | PASS |  |
| SB-202 | user input is stored/returned as data, not markup | high | PASS |  |
| SB-203 | path traversal cannot escape the web root | critical | PASS |  |
| SB-204 | no response-header injection via field values | high | PASS | headers clean |
| SB-205 | prototype-pollution keys are inert | medium | PASS | ignored |
| SB-206 | a user cannot assign themselves a higher role | critical | PASS | rejected or ignored |
| SB-207 | state-changing request without a CSRF token is rejected | high | PASS | accepted (403) |
| SB-208 | tenant data requires a session | critical | PASS | [] |
| SB-210 | session cookie is HttpOnly + SameSite | high | PASS | status=200 cookie=re_session=eyJzdWIiOiJ2aWV3ZXJAcmVhbHRvcmVzdGF0ZXMuYXBwIiwidWlkIjoidXNyX2EzODFiN2RjOGQiLCJ0aWQiOiJ0bnR |
| SB-209 | repeated failed logins are rate limited | high | PASS | statuses=[401, 429] |
| SB-211 | cross-tenant object access is denied | critical | PASS | denied |
| SB-212 | webhook URLs are validated (no SSRF to metadata/localhost) | high | PASS | rejected (400) |
| SB-213 | oversized request bodies are rejected | medium | PASS | status=413 |
| SB-215 | a tampered session token is rejected | critical | PASS | status=401 |
| SB-216 | a viewer cannot reach owner-only operations | critical | PASS | [] |
| SB-217 | authenticated API responses are marked no-store | medium | PASS | Cache-Control=no-store, private |
| SB-218 | the public funnel endpoint is rate limited | medium | PASS | statuses=[422, 429] leads created=0 |
| SB-219 | the event stream requires a session | high | PASS | anonymous GET /api/v1/events → 401 |
| SB-220 | a forged Host header does not appear in responses | low | PASS | ignored |
| SB-214 | malformed JSON returns 4xx, not 5xx | medium | PASS | status=400 |
| SC-301 | the application's database leg is TLS | critical | PASS | libpq ssl_in_use=True server_side_ssl=False (pooler terminates TLS) |
| SC-302 | application role is not a superuser | high | PASS | superuser=False createdb=True createrole=True |
| SC-303 | audit table is protected by an enabled UPDATE/DELETE trigger | critical | PASS | triggers=[('O', 'CREATE TRIGGER re_audit_no_update BEFORE')] |
| SC-304 | row-level security is enabled with a tenant policy | high | PASS | enabled=True forced=False policies=1 |
| SC-305 | no credential is stored in document or audit rows | critical | PASS | documents=0 audit=0 |
| SC-306 | contact PII is encrypted at rest | high | PASS | 0/79 contacts have a plaintext phone/email column |
| SC-307 | audit chain is linear and hashes verify | critical | PASS | 0 break(s) in 336 rows |
| SC-308 | every query is parameterised (no string-built SQL) | critical | PASS | 0 risky call(s) |
| SD-401 | an agent never echoes a credential it was given | critical | PASS | redacted |
| SD-402 | the model prompt carries no loaded secret | critical | PASS | [] |
| SD-403 | personal data is minimised before it reaches a model | high | PASS | no raw PII |
| SD-404 | agent guardrails run before the model (kill switch respected) | high | PASS | run_agent stops before any model call when kill_switch/status/spend-cap trip |
| SD-405 | no credential is ever placed in a prompt by the app | high | PASS | no code path interpolates SECRET_INVENTORY values into prompts |

Full context, root causes and residual risk: `docs/13-SECURITY-AUDIT-2026-11.md`.

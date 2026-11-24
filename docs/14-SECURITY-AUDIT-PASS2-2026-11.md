# Security audit — pass 2 (adversarial agent), 2026-11-17

Pass 1 (`docs/13-SECURITY-AUDIT-2026-11.md`) was a checklist run by a human-written
harness. Pass 2 is different in one specific way: the testing tool is an **agent**
that decides what to probe, and it is built so that it cannot be talked into doing
something harmful with the authority it holds.

Two things were delivered:

1. `sentinel/agent.py` — an autonomous security-testing agent with a **hard policy
   gate** (Python, not a prompt).
2. Every defect it found was fixed in the same pass, then re-tested until clean.

| harness | scope | result |
|---|---|---|
| `sentinel/agent.py` | 47 checks, 8 modules, DB + frontend focus | **47/47, 0 findings** |
| `scripts/security_audit.py` | 44 live checks + static scan | **100/100, 44 passed** |
| `redline/redline.py` | 53 offensive checks, 8 attack categories | **100/100, 53 passed** |
| `pytest tests` | 87 tests | **85 passed, 2 skipped** (86/1 against Neon) |

---

## 1. The agent, and why the gate is not a prompt

Guidance used (read before writing the code): OWASP *AI Agent Security Cheat Sheet*
(least-privilege tools, scoped allowlists, output validation), OWASP Agentic Top-10
test matrix (ASI01–ASI10), the agentic-SOC guardrail pattern (classify every action
by impact and reversibility; evidence before action; human approval for the
irreversible), and SP-047 (sandboxed execution, kill switch, promotion gates).

The design that came out of it:

```
perceive (recon) → decide (module) → gate (policy) → act (HTTP tool)
                                  → verify (evidence) → record (finding)
```

Action classes, and who may do what unsupervised:

| class | example | allowed |
|---|---|---|
| OBSERVE | `GET /api/v1/contacts` | yes |
| ANALYZE | multi-request probing, inference | yes, logged |
| EXECUTE | create a record the agent owns, then clean it up | yes |
| RECOMMEND | propose a fix | yes |
| DESTRUCTIVE | delete a user, enable the kill switch, change a campaign budget | **refused** |
| VOLUMETRIC | > 200 requests in one burst | **refused** |
| EXFILTRATE | send anything off-host | **refused** |

Refusals are not errors to be swallowed: the gate raises, the run records a
`blocked` event, and the event is published in the report. `--self-test` proves the
gate refuses what it must before the agent is allowed near the target:

```
» policy gate self-test
  [OK] DESTRUCTIVE DELETE /api/v1/tenant/users/usr_1        → refused (deleting or editing a human account is destructive)
  [OK] ANALYZE     POST /api/v1/tenant/kill-switch          → refused (enabling the tenant kill switch is a destructive tenant-wide)
  [OK] EXFILTRATE  POST /x                                  → refused (the agent may not send data off-host)
  [OK] ANALYZE     GET https://evil.example/steal           → refused (host 'evil.example' is outside the authorised scope)
  [OK] VOLUMETRIC  GET /api/v1/contacts                     → refused (burst of 5000 exceeds the denial-of-service ceiling)
  [OK] OBSERVE     GET /api/v1/contacts                     → allowed
  [OK] EXECUTE     POST /api/v1/contacts                    → allowed
gate self-test: pass
```

The same table is embedded in `reports/sentinel-report.md` on every run, so the
evidence ships with the artefact instead of living in a terminal buffer.

Run it:

```bash
python3 sentinel/agent.py --self-test     # prove the gate first
python3 sentinel/agent.py                 # 7 modules, ~50 requests
python3 sentinel/agent.py --deep          # adds the limiter-exhaustion module
python3 sentinel/agent.py --strict        # exit 1 on any finding (CI)
```

`--deep` spends the whole per-IP auth budget (34 logins) to prove that a forged
`X-Forwarded-For` cannot buy a fresh bucket. It runs last, and logins are throttled
for ~5 minutes afterwards — by design, that *is* the control under test.

---

## 2. Findings

Severity is what it costs if exploited, not how hard it was to find.

### SEN-001 — `X-Forwarded-For` was trusted unconditionally · critical

`app/server.py::client_ip()` returned `X-Forwarded-For.split(",")[0]` whenever the
header was present. That single line was the input to the rate limiter, the
per-account lockout and the audit trail's `ip` field.

Reproduction (agent, `xff` module): 14 failed logins, each carrying a different
forged address → `[401, 401, …]`, no throttle. With the fix, the same loop hits the
30/300 bucket at attempt 31 regardless of the header, and a *never-seen* address
still gets `429` + `Retry-After` (the second check exists because rotating headers
is the obvious way to defeat a naive fix).

```python
# before
fwd = self.headers.get("x-forwarded-for", "")
if fwd: return fwd.split(",")[0].strip()[:45]
return self.client_address[0]

# after — the peer address is the only fact we own
if TRUSTED_PROXIES and _peer_is_trusted(peer):
    return fwd[-1][:45]          # last hop added by *our* proxy
return peer
```

`RE_TRUSTED_PROXIES` is a CIDR list, empty by default (nothing is trusted until an
operator says so). Tests: `test_xff_is_ignored_unless_the_peer_is_a_trusted_proxy`,
`test_trusted_proxy_list_is_honoured_when_configured`.

### SEN-002 — row-level security was enabled but not forced · medium
### SEN-003 — the web tier connected as a role with `BYPASSRLS` · high

These two are one finding in practice: `re_documents` had
`relrowsecurity = true` and a tenant policy, and the application connected as
`neondb_owner` (`rolbypassrls = true`). For the connection that actually served
HTTP, the policy did nothing. One missing `where tenant_id = …` would have been a
cross-tenant leak, with the schema looking careful the whole time.

Fix, in three parts:

* `db/004_runtime_role.sql` — `FORCE ROW LEVEL SECURITY` on `re_documents` **and**
  `re_audit`, policies for `re_app` only, `re_audit` still append-only by grant
  (`select, insert` — no update, no delete).
* `scripts/provision_app_role.py` — creates/rotates `re_app` as
  `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE` and writes two DSNs to
  `.env` (0600): `DATABASE_URL` → `re_app` (the web tier),
  `RE_ADMIN_DATABASE_URL` → owner (migrations and boot hydration only).
* `app/db.py` — every write and every tenant-scoped read now opens its transaction
  with `select set_config('app.tenant_id', %s, true)`. Transaction-local, so a
  pooled server connection cannot inherit the previous request's tenant.

Verified live by the agent (`db` module, connecting as the runtime role):

```
SEN-C801 RLS is enabled on the document table        PASS
SEN-C802 the application role cannot bypass RLS      PASS  (superuser=False bypassrls=False)
SEN-C806 the runtime role sees nothing without a tenant context  PASS  (visible=0)
SEN-C805 the audit table refuses UPDATE              PASS
```

The honest caveat: the owner role can still see everything, because a DBA can drop
the policy anyway. The control is that the **web tier no longer holds that
credential**. Boot hydration is a genuine cross-tenant read and is named as such in
the code.

### SEN-004 — a dropped pooled connection became a 500 and a traceback · high

Found in the server log during the first agent excursion, not by a probe: Neon's
pooler recycled the server connection mid-run, and `PgBackend` had no reconnect on
the write path. Every subsequent write returned `500` — including `auth/login` — and
printed a psycopg traceback to stderr.

Fix: `PgBackend._run(fn)` wraps each database operation, reconnects once on
`OperationalError`/`InterfaceError`, retries, and then raises `StorageUnavailable`,
which the edge maps to `503 STORAGE_UNAVAILABLE` with a trace id and no detail.
Apart from the outage itself, a recycled connection is normal infrastructure
behaviour and should never be an unhandled exception.

### SEN-005 — session tokens were written verbatim to the access log · medium

`log_message()` scrubbed `+91`, `re_session=` and `Bearer ` but passed the request
line through otherwise. A single `GET /api/v1/contacts?token=eyJ…` (anything that
followed an old link) put a live credential in a file nobody thinks of as a
credential store.

Fix: the request line is now treated as attacker-controlled input — sensitive query
keys are redacted by name, JWTs by shape, and the result passes through
`security.redact_secrets()` before it is written.

### SEN-006 — `Server: RealtorEstates/1.0 Python/3.13.18` · low

A product name and the exact interpreter build. Optional per RFC 9110, so the
handler now overrides `send_response()` to emit `Date` and nothing else.
Test: `test_server_banner_is_not_disclosed`.

### SEN-007 — the public health endpoint mapped the backend · medium

`/healthz` was unauthenticated and returned the DSN host
(`ep-…-pooler.c-2.ap-southeast-1.aws.neon.tech`), the PostgreSQL build, the
document count and whether a master key was loaded. That is a reconnaissance
report, and it contradicts the premise that nothing about the deployment is public.

Fix: anonymous callers get `{ok, service, time, db: {backend, durable}}`. The
operator view (latency, version, counts, masked DSN) needs an owner/admin session.
`SA-111` in the audit harness now asserts the *absence* of those fields instead of
the masked password.

### SEN-008 — a read-only seat could enumerate users and the audit trail · high

`GET /api/v1/tenant/users` and `GET /api/v1/tenant/audit` had no role check: a
`viewer` session could list every colleague's email, role, MFA state and last login,
plus the audit trail with actor names and source IPs.

Fix: both require `ctx.require("manage_tenant")` (owner/admin). The UI already
renders a restricted team page for other roles, so nothing in the product breaks.
Tests: `test_user_directory_and_audit_require_an_admin_seat`,
`test_viewer_sees_masked_phone_numbers`.

### SEN-009 — audited databases accumulate dormant owner accounts · medium

The redline harness signs up a throwaway tenant to test lockout and weak-password
rejection. Nothing deleted them: after four runs the production database held four
`owner` accounts (`lockout-probe-*@example.invalid`) whose passwords nobody knew,
plus one unencrypted synthetic contact row from a Postgres test.

Fix: `scripts/prune_probe_accounts.py` (report by default, `--apply` to delete;
refuses any tenant that also holds a non-probe user), the Postgres test now asserts
its own rows are gone, and the agent's PII check ignores synthetic test tenants.
The four probe tenants were pruned in this pass.

---

## 3. False positives the agent reported, and what was done about them

A scanner that cries wolf trains people to ignore it, so each of these was resolved
by fixing the *test*, and the reason is recorded here rather than buried in a diff.

| false positive | why it was wrong | resolution |
|---|---|---|
| "viewer can read a contact" | a viewer is entitled to read contacts; the role model only forbids PII | check rewritten as the privileged-surface matrix + a PII-masking check |
| "token in query string authenticates" | the probe client sent its own `Authorization` header | probe uses a credential-free client |
| "healthz leaks the DSN" (audit harness) | the harness called it with an owner token | probe made anonymous |
| "no new token on login / cookie missing" | the auth bucket was exhausted; the server answered 429 | these checks now *skip* with an explicit message instead of failing |
| "integrations are privileged" | the payload is provider name, category and health — no credential material | reclassified; a new check asserts the payload carries nothing secret-shaped (`SEN-C306`) |
| "10 redline failures" | the cached-token path set the bearer but not the cookie, so every cookie-authenticated write was 403 | harness bug fixed (cookie seeded, CSRF derived locally) |

---

## 4. Residual risk (unchanged, stated plainly)

* A compromised host is a compromised system. The `.env` file is a file: `0600`,
  git-ignored, never served, never logged — not a vault. Production should move the
  master key and the two DSNs to a secret manager and rotate with
  `scripts/rotate_keys.py`.
* The owner DSN can read every tenant. That is what a DBA role is. It no longer
  exists in the process that serves HTTP.
* In-memory read cache: one writer process assumed. Two API pods are safe for
  durability, not yet for cache coherence.
* Rate limiting is per process (in-memory buckets). Two pods = two budgets.
* The agent tests what it can reach: HTTP surface, source, and the database as the
  runtime role. It does not test the cloud account, DNS, or the network path.

---

## 5. Reproduce

```bash
python3 sentinel/agent.py --self-test      # gate refuses the prohibited classes
python3 sentinel/agent.py --deep           # 47 checks, DB + frontend
python3 scripts/security_audit.py          # 44 checks
python3 redline/redline.py                 # 53 checks
python3 scripts/prune_probe_accounts.py    # no dormant probe tenants
RE_TEST_DB_FROM_ENV=1 python3 -m pytest tests -q
```

Order matters: `--deep` spends the auth bucket, so run it last or restart the
server. `RE_AUDIT_TOKEN` / `SENTINEL_TOKEN` / `RE_REDLINE_TOKEN` (or the cached
token files, `0600`, git-ignored) skip the login POST entirely.

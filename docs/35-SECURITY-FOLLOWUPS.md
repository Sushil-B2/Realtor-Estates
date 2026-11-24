# 35 — Sentinel Report Gate, Audit Chaining, TLS Cookie Posture & Audit PII Retention (`v1.24.0`)

Closes the five remaining credential-free security follow-ups from `PLANS.md`
(`docs/14` follow-ups #2, #3, #4 and `docs/13` follow-ups #4, #6; `docs/13` #3 was
closed in `v1.20.0` + `v1.23.0`).

---

## 1. What was still open — and the three real defects caught while closing it

Before `v1.24.0`, five credential-free items remained open in `PLANS.md`:

| Source | Follow-up | Closed by |
|---|---|---|
| `docs/14` #4 | Extend Sentinel's `EXFILTRATE` gate to cover the agent's own report files | `Gate.check("WRITE_REPORT", ...)` + `scrub_report_obj()` in `sentinel/agent.py` |
| `docs/14` #3 | Record Sentinel scans in the tamper-evident audit chain as `security.sentinel_run` | `POST /api/v1/security/sentinel-runs` (`app/api.py`) + `Sentinel.report()` |
| `docs/14` #2 | Run Sentinel `--deep --strict` + Postgres gate in CI so regressions fail the build | `.github/workflows/ci.yml` + `npm run audit:sentinel:strict` |
| `docs/13` #4 | Set `; Secure` on auth cookies behind TLS termination without allowing plain-HTTP DoS | `Handler._cookie_is_secure()` in `app/server.py` |
| `docs/13` #6 | Configure an audit PII retention window for emails and IPs without breaking the 7-year SHA-256 hash chain | `app/audit_retention.py` + `GET /api/v1/tenant/audit` + `PATCH /api/v1/tenant` |

### Three real defects uncovered in `sentinel/agent.py`

Inspecting and running `sentinel/agent.py` on a clean checkout uncovered **three**
defects that made `docs/14` follow-up #4 immediately concrete:

1. **Multi-header `Set-Cookie` overwrite dropped `re_session` on fresh logins.**
   `HttpClient.call` converted response headers via `dict(r.getheaders())`. Since
   `v1.17.0` (`docs/28`), `POST /api/v1/auth/login` emits **two** `Set-Cookie`
   headers (`re_session=...` followed by `re_refresh=...`). `dict(...)` kept only
   the last header (`re_refresh`), so `self.cookies["re_session"]` stayed empty on
   any fresh checkout without a cached `.sentinel-token.json`, silently skipping
   5 authenticated checks (`43/43` instead of `48/48`).
2. **Live 30-day `re_refresh` credential leaked verbatim into `reports/sentinel-report.md`.**
   Because `SEN-C503` wrote `hdrs.get("Set-Cookie", "")[:100]` directly into check
   detail, `reports/sentinel-report.md` and `reports/sentinel-report.json` contained
   a live 30-day `re_refresh=<token>` credential in cleartext on disk.
3. **`SEN-C201` claimed `"no credential is written to localStorage/sessionStorage"` while scanning only `localStorage`.**
   `core.js` and `keysetup.js` now keep Bearer/CSRF state strictly in JavaScript
   heap memory (`RE.state.token`, zero web storage), and `SEN-C201` now scans both
   `localStorage` and `sessionStorage`.

---

## 2. `WRITE_REPORT` `EXFILTRATE` gate & credential scrubbing (`sentinel/agent.py`, `app/security.py`)

- `app.security.contains_secret(text)` and `app.security.redact_credentials(text)`
  detect and scrub high-signal credentials (`re_session=`, `re_refresh=`, JWTs,
  Bearer tokens, `nak_` / `npg_` Neon keys, `sk-ant-` / `sk-` API keys, Postgres
  DSNs with passwords, and PEM private keys) without clobbering SQL identifiers
  such as `re_document` or `re_audit`.
- `SEN-C503` now preserves all `Set-Cookie` lines (`Set-Cookie-Lines`), selects the
  `re_session=` line specifically, and scrubs its value before recording check
  detail (`re_session=[redacted]; Path=/; HttpOnly; SameSite=Strict; Max-Age=1800`).
- `Sentinel.report()` runs `scrub_report_obj()` over the entire JSON and Markdown
  report and then passes both outputs through `Gate.check("WRITE_REPORT", ...)`:
  - **Off-directory destination** (anything outside `reports/` or any URL scheme) →
    refused (`Blocked`, `EXFILTRATE`).
  - **Unredacted credential in report content** → refused (`Blocked`, `EXFILTRATE`).
- `gate_self_test()` now exercises **10** policy cases (including 3 `WRITE_REPORT`
  cases).

---

## 3. Tamper-evident `security.sentinel_run` audit events (`app/api.py`)

- **`POST /api/v1/security/sentinel-runs`** (`owner` only):
  1. Rejects (`400 SECRET_IN_REPORT`) any payload where `sec.contains_secret(...)`
     or raw cookie patterns match — the audit chain can never become a secret sink.
  2. Writes a `runs` row (`type="sentinel"`) with check counts, severity breakdown,
     module list, `report_sha256`, and `report_redactions`.
  3. Appends `security.sentinel_run` into the workspace's SHA-256 hash chain and
     returns `run_id`, `audit_id`, and `audit_hash`.
- **`GET /api/v1/security/posture`** and the Security console now surface
  `last_sentinel` alongside `last_redline`.
- `Sentinel.report()` posts automatically when authenticated (pass `--no-audit` for
  dry offline runs).

---

## 4. `Secure` cookies behind TLS termination (`app/server.py`)

`Handler._cookie_is_secure()` appends `; Secure` to `re_session`, `re_refresh`, and
logout clear-cookies whenever any of three conditions holds:

1. `RE_ENV=production` (`CFG.PRODUCTION`),
2. `RE_SECURE_COOKIES=1` (`true` / `yes`), or
3. the TCP peer is in `RE_TRUSTED_PROXIES` and forwarded `X-Forwarded-Proto: https`.

**Why untrusted `X-Forwarded-Proto` is ignored:** if plain-HTTP mode honoured a
client-supplied `X-Forwarded-Proto: https` from an untrusted peer, any caller could
force `; Secure` cookies that browsers on `http://` refuse to send back.

---

## 5. Configurable audit PII retention without breaking the SHA-256 chain (`app/audit_retention.py`)

### Why raw rows cannot be `UPDATE`d or `DELETE`d at day 90

1. `re_audit` enforces database-level immutability (`SEN-C805` verifies `UPDATE` fails).
2. `sec.audit_hash(entry)` hashes `actor`, `target`, and `meta`. Mutating an aged
   row in storage would invalidate its hash and break `verify_audit()` for every
   subsequent row across the 7-year (`2555`-day) archive.

### Keyed presentation pseudonymisation

- Default PII window: **90 days** (`RE_AUDIT_PII_RETENTION_DAYS`, overridable per
  workspace via `PATCH /api/v1/tenant` → `settings.audit_pii_retention_days`,
  bounded to `7..2555` days).
- Entries within the window return unchanged (`pii_redacted: False`).
- Entries older than `audit_pii_retention_days` replace email `actor` / `target`
  fields and IP/email fields inside `meta` with deterministic HMAC-SHA256 pseudonyms
  keyed by `KEYS["blind_index"]` (e.g. `redacted-actor:f6248f649d`,
  `redacted-ip:db301f66b0`, `pii_redacted: True`).
- Two expired actions by the same actor or IP share the same pseudonym so incident
  timelines still correlate without exposing personal emails or home IPs in API
  responses or the UI.
- **Honest limit:** raw append-only rows in storage and backups remain untouched so
  the 7-year SHA-256 hash chain verifies byte-for-byte (`chain.ok == True`). True
  cryptographic erasure at day 90 would require encrypting audit PII under rotating
  period subkeys and destroying expired subkeys.

---

## 6. Proof commands

```bash
python3 sentinel/agent.py --self-test
python3 -m pytest tests/test_security_followups.py -q
python3 -u scripts/proof_security_followups.py
```

# 13 — Security Audit, 17 November 2026

Scope: the whole running system — web tier, API, database, agents, secrets handling.
Method: two independent sweeps plus regression tests, all reproducible:

```bash
python3 scripts/security_audit.py     # 44 live checks against http://127.0.0.1:8000
python3 redline/redline.py            # 53 external offensive checks
python3 -m pytest tests/ -q           # 78 tests (32 security regressions)
python3 scripts/check_secrets.py --history
```

**Result: 44/44 live audit checks pass, 53/53 redline checks pass, 78 tests pass.**
Ten real defects were found and fixed in this pass; none were "known issues".

---

## 1. What was actually broken

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | critical | **A credential given to an agent came back in the response.** `inspect_text()` returned a field named `redacted` that only masked PII — `sk-ant-…` passed straight through, and `run_agent()` echoed it in `guardrail.redacted`. | `redact_secrets()` now runs on that field; `inspect_text()` applies it; `run_agent()` scrubs the echo again. |
| 2 | critical | **Redaction missed most real key shapes.** Only `sk_/pk_/ghp_/xoxb_` style tokens matched. `sk-ant-…`, `sk-live-…`, `npg_…`, `nak_live_…`, `nsk_live_…`, `AIza…`, JWTs, `AKIA…`, private-key blocks and `postgresql://user:pass@` all survived. | A `SECRET_PATTERNS` table of 11 shapes, applied before PII masking. |
| 3 | high | **`data/store.json` was mode 0644** — every tenant record readable by any local user. | Written `0600` from the first byte; an existing file is tightened on boot. |
| 4 | high | **Login throttling was too weak and could be weaponised.** 100 attempts/5 min per IP; the only per-account brake was a permanent 6-failure lockout — so anyone who knew an address could deny service to that user forever, and spraying many accounts from one host was cheap. | Per-IP `auth` bucket tightened to 30/5 min; new per-account bucket (10 per 15 min, keyed by `sha256(email)` so no address is stored); lockout is now **time-based** (`locked_until`, 15 min, self-clearing). |
| 5 | high | **The audit chain forked under concurrent writers.** Two logins at once chained to the same tail, so `/api/v1/tenant/audit` reported `ok:false` — indistinguishable from tampering. | `append_audit()` takes `pg_advisory_xact_lock(hashtext(tenant_id))` and reads the tail **from the database**, not from process memory. `scripts/repair_audit_chain.py` rebuilds a fork that already happened (it has been run: 116 rows, now linear). |
| 6 | high | **The audit probe reported a false negative on an empty table** (`UPDATE … WHERE seq = (SELECT min(seq)…)` matches nothing ⇒ trigger never fires). Looked like the append-only guard was missing. | Probe now checks `pg_trigger` *and* writes a canary row before attempting the tamper — inside a savepoint, rolled back, so it leaves no residue in the log. |
| 7 | medium | **Session state leaked through the Neon pooler.** A `SET ROLE re_app` from an earlier client survived into a new connection; the app briefly ran as a restricted role and DDL failed. | Every connection starts with `reset role; reset all`. |
| 8 | medium | **No cap was documented on body size** (the cap existed at 1 MB but nothing tested it, and there is no reverse proxy in front of this server). | Cap is asserted by a regression test (413 above 1 MB) plus 415 for a non-JSON content type. |
| 9 | medium | **Repository scan cried wolf.** The secret scanner flagged an AWS *documentation* example key in a test, which trains people to ignore it. | The test uses a synthetic key; the scanner's exposure zones now cover paths outside the repo too. |
| 10 | critical (exposure) | **A plaintext copy of every credential sat outside the repository** — the uploaded `.env` dump (`RE_MASTER_KEY`, webhook secret, DB password, storage keys). Git-ignore protects the repo, not its neighbours. | File overwritten with random bytes and unlinked; `check_secrets.py` now scans `/home/user/uploads`, `Downloads`, `Desktop` for secret containers and secret-shaped values. |

Two further fixes came from the same pass: the `audit` collection was being written twice (to `re_documents` *and* `re_audit`), which destroyed chain ordering; and `neon._present()` treated the bucket name `assets` as a placeholder because it applied a 16-character secret heuristic to configuration.

## 2. What was verified clean (evidence, not opinion)

**Secrets**
- No secret-bearing file is tracked by git; `git ls-files` has only `.env.example`.
- 20 HTTP probes for `.env`, `.env.local`, `data/store.json`, `data/keys.json`, `app/config.py`, `.git/config`, `uploads/env.txt`, encoded traversal variants — all 404/401.
- 20 owner API endpoints and 10 static assets scanned for 8 secret shapes and every live secret value: zero hits.
- `/healthz` reports the host with the password masked (`postgresql://neondb_owner:***@…`).
- Error responses carry no stack trace, filesystem path or DSN.
- **Clipboard:** the web tier has *no* `navigator.clipboard` call at all, so nothing can put a credential on the clipboard; a regression test fails the build if one is ever added with a token in scope.
- Static assets are CSP-restricted (`default-src 'self'`), so a stolen secret cannot be exfiltrated to a third-party origin by injected script.

**Injection & application flaws**
- 7 SQL-injection payloads × 3 endpoints: no 5xx, no SQL error text. `app/db.py` contains no string-built SQL (asserted statically).
- XSS payloads round-trip as JSON data (`Content-Type: application/json`), and every `${…}` inside `innerHTML` in the SPA is either literal, the `icon()` helper, or wrapped in `esc()` — checked mechanically, not by eye.
- 8 path-traversal variants blocked; CRLF in a field cannot inject a header; `__proto__`/`constructor` keys are inert; role escalation via `PATCH /auth/me` refused.
- CSRF token required on every mutation; no tenant route answers without a session; cross-tenant object access denied; tampered session tokens rejected.
- Webhook registration refuses `169.254.169.254` (SSRF allowlist enforced at registration and at send).
- Public funnel endpoint is throttled per IP; the SSE stream requires a session; oversized bodies get 413; malformed JSON gets 400; authenticated responses are `Cache-Control: no-store`.

**Database**
- The application's leg to Postgres is TLS (`libpq ssl_in_use = true`). Note: `pg_stat_ssl` reports `ssl=false` because Neon's **pooler terminates TLS** and its internal leg to the compute is unencrypted — that is Neon's network, not ours, and it is why the check reads libpq rather than the catalogue.
- Append-only audit trigger present **and enabled** (`tgenabled='O'`) for `UPDATE` and `DELETE`; live tamper attempt blocked.
- Row-level security enabled with a tenant policy; `re_app` sees 210 of 214 rows when scoped and 0 when unscoped; cross-tenant INSERT rejected.
- No credential appears in `re_documents` or `re_audit` (regex over `jsonb`); contact phone/email are stored as ciphertext + blind index, not plaintext.
- Audit chain linear, all hashes verify, including after two concurrent writers.

**AI agents**
- A credential fed to an agent is not echoed in the output, the trace or the guardrail block.
- The prompt sent to a model contains no loaded secret; raw PII (phone, email) is not passed through — the agent gets an opaque `contact_id`.
- Kill switch, status gate and spend cap are checked *before* any model call.

## 3. Residual risk — what an audit cannot fix

1. **A file-based secret is only as safe as the host.** `.env` and `.env.local` are `0600`, but root, the app user, and anything running as either can read them. Closing this needs KMS/HSM + per-tenant DEKs (`docs/11 §7`), which is designed and not built.
2. **The Neon password was displayed during this session** (it appeared in a driver error message before redaction was added). **Rotate it** in the Neon console; the DSN in `.env.local` then needs updating. Same for the storage access/secret keys.
3. **The owner role is powerful by design.** There is no four-eyes control on destructive operations, and no step-up authentication (re-auth) before kill-switch or key rotation.
4. **Rate limiting is in-process.** Two app instances = two counters. Production needs Redis (or Neon's own limiter) or the effective budget doubles.
5. **No WAF, no reverse proxy, no HSTS in front of this server.** It binds `0.0.0.0` and speaks plain HTTP; `Secure` on the session cookie is therefore not set (it would break the local preview). Terminate TLS in front, then add `Secure` + HSTS.
6. **Backups are untested.** Neon PITR exists; nobody has restored a branch and verified the audit chain after a restore.
7. **The audit log records IPs and emails** — operational PII with no retention limit. Define a retention window (`docs/07`) before a real tenant's data lands in it.
8. **`/api/v1/security/inspect` still returns a truncated echo** of the input (now redacted). It is useful for operators; it is also an oracle if ever exposed without a session.

## 4. Reproduce this audit

```bash
npm start &                                # or the live preview
python3 scripts/security_audit.py          # 44 checks → reports/security-audit.json
RE_AUDIT_TOKEN=<owner token> python3 scripts/security_audit.py   # skip login throttling
python3 redline/redline.py                 # 53 offensive checks
python3 -m pytest tests/ -q                # includes 32 audit regressions
python3 scripts/check_secrets.py --history # repo + history + exposure zones
```

`npm run build` runs the tests and the secret scan; the live sweep needs a running
server, so it stays a deliberate step. A clean report is a lower bound on safety,
not a certificate: it proves these 44 behaviours, on this build, on this day.

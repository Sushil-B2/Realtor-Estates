# AGENTS.md — Mandatory Zero-Secret-Exposure & Deployment Protocol

> **READ BEFORE EXECUTING ANY COMMAND OR TOOL CALL IN THIS REPOSITORY.**
> Tool inputs, `bash` stdout/stderr, and connector payloads are visible in operator chat logs and session transcripts. Every agent, developer, and automation script working on **Realtor Estates** MUST obey the rules below unconditionally.

---

## 1. Non-Negotiable Secret-Handling Rules (Zero Exceptions)

1. **NEVER read, print, echo, or inspect secret files in tool output:**
   - Forbidden commands/targets: `cat .env*`, `read_file` on `.env` / `.env.local` / `.secrets/*`, `env`, `printenv`, `set`, `export -p`, `git diff` on untracked env files, or `grep` without `-l` / `--files-with-matches` against `.env*`.
   - When checking whether an environment variable is configured, inspect **boolean presence only**, NEVER the value or a credential prefix. Do not display fingerprints unless explicitly justified and authorized; the scanner omits them.
2. **NEVER pass plaintext secrets, DSNs, OAuth client secrets, or API keys in connector tool arguments:**
   - Provider/cloud secrets (`DATABASE_URL`, `RE_ADMIN_DATABASE_URL`, `NEON_STORAGE_ACCESS_KEY`, `NEON_STORAGE_SECRET_KEY`, `RE_MASTER_KEY`, `RE_WEBHOOK_SIGNING_SECRET`, `PAYMENTS_WEBHOOK_SECRET`, OAuth keys) must be created/rotated **directly by the operator in the provider dashboard** with **Sensitive / Write-Only** mode enabled (`Production` + `Preview` only; `Development` disabled).
   - Automated deployments may update **only non-secret public build metadata** (`RE_BUNDLE_KEY`, `RE_BUNDLE_SHA256`, `RE_SKIP_BOOT_MIGRATE`, `RE_STATELESS_READS`).
3. **NEVER hardcode or embed credentials in code, tests, HTML/JS, comments, or docs:**
   - Use `app.config.get("VAR_NAME")` on the server, never literal fallback keys.
   - Run `python3 scripts/check_secrets.py` before every bundle build, commit, or deployment.
4. **Sanitize all error handling in scripts:**
   - Catch exceptions and log `err.name` / `type(exc).__name__` + HTTP status code only — never print raw connection strings, request headers, or full exception reprs that could echo DSNs or tokens.

---

## 2. Safe Deployment Workflow (`realtor-estates` Only)

1. **Project Isolation — updated by explicit user authorization:**
   - Target **only** Vercel project `realtor-estates` (`prj_E7SGbITHWxBXaGpVPYQgLqd9UUtY`) in team `team_s0uBpvnoysN85mFkEn2qqjO7`.
   - This new dedicated project supersedes the earlier `realtor-estates-pwa` deployment target. Do not modify that older project either.
   - **Never** modify, redeploy, or alter ANY other Vercel project or team-wide shared setting/variable. Do not copy or retrieve secrets from sibling projects.
   - No automatic deployment of GitHub `main`: use the verified integration branch and prepare a project-scoped preview first.
2. **Silent Bundle Build & Upload:**
   - Run `node scripts/safe_bundle_upload.mjs` — it executes `scripts/check_secrets.py`, builds a clean allowlisted tarball in `/tmp` (excluding `.env*`, `.secrets/`, `data/`, `node_modules/`, `.git/`), uploads to Neon Object Storage in-memory without echoing credentials, and prints **only** `BUNDLE_KEY`, `BUNDLE_SHA256`, and `BUNDLE_BYTES`.
3. **Pre-Deploy Quality Gates:**
   - `python3 scripts/check_secrets.py` (`0` critical findings)
   - `node scripts/check-js.mjs` (`0` syntax errors)
   - `python3 scripts/check_a11y.py` (`0` WCAG 2.2 AA violations)
   - `python3 scripts/safe_test_gate.py` (all suites green; isolated temporary file storage for both in-process fixtures and HTTP subprocess; diagnostic payloads suppressed)

## 3. Contract-First, Evidence-Based Delivery

- Preserve existing tenant isolation, encryption, consent, auditability, RERA and outbound-communication controls. Do not weaken them to make a test pass. Security- or compliance-sensitive changes require human review before release.
- Work in small vertical slices. Before a feature changes a data model, API or UI contract, document the applicable migration, request/response behavior, interaction and acceptance tests. Bug fixes need only the contracts relevant to that fix; do not force an unnecessary schema migration or framework rewrite.
- Separate simulated integrations from live behavior in the UI and API; never imply an external message, publication, spend, booking or payment happened without provider-confirmed evidence. Do not enable external actions without explicit authorization and consent.
- Test normal, invalid, authorization, tenant-isolation and failure paths in disposable environments. Record pass, fail, skip and blocked results, test environment and remaining limitations. A mock, static accessibility check or healthy status alone cannot certify a live service or WCAG conformance.
- Verify both keyboard/touch-accessible and pointer interaction for new workflow controls, with clear error recovery. For pipeline transitions, server authorization and stage gates remain authoritative; any optimistic update must roll back on rejection.
- Review third-party licenses before reuse. Prefer original implementations of patterns; never copy code into this repository without license compatibility and attribution review.
- Update the work ledger with exact scope, tested commit, changes, skips and next actions. Do not claim zero defects or guaranteed zero leakage. Keep Preview and production changes within the isolation rules above.

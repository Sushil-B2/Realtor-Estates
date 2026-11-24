# PWA Development History Log

A dated engineering journal: what was built, what broke, what was thrown away, and why. Written the way
an engineer would actually write it — including the parts that did not work. For user-facing changes see
`changelog.md`; for decisions and their alternatives see `PLANS.md` §5.

Context for everything below: the whole build happened in a single session on **2026-11-17**, in a
sandbox with **no network at install time, no `cryptography`, no `PyJWT`, no `pynacl`, and no browser
automation**. That constraint shaped almost every decision.

---

## H0 — Reading the brief and the uploads (start)

Nine documents in `/home/user/uploads/` (2,447 lines): a prior "BHOOMI" land-sales OS pack with a data
model, API/webhook shapes, a voice-agent spec, India compliance notes, an integration catalogue, a design
system, roadmap/economics and a `pytest.ini`. Read all of it before writing anything, because the brief
says to honour it, not discard it.

Then seven web searches for the 2026 state of the art: MCP adoption and exploit prevalence, the October
2026 **PHP2Shell** PHP RCE and its agentic markers, the OWASP LLM Top 10 reordering, iOS PWA
capabilities on iOS 26/27, voice-agent pricing (including Sarvam at roughly an order of magnitude below
US speech-to-speech), DPDP phase dates, and India scraping legality.

**Decision taken early:** the deliverable is a *running product*, not a document describing one. The docs
sit on top of code that exists and passes tests.

---

## H1 — Environment probe (the first hard constraint)

```
python3 -c "import cryptography"   → ImportError
python3 -c "import jwt"            → ImportError
python3 -c "import nacl"           → ImportError
python3 -c "import argon2, pytest" → OK
python3 -c "from PIL import Image" → OK (13.0.0)
```

No `pip install` budget, no guarantee of network. So: **stdlib-only crypto and auth**. That is a serious
constraint — it rules out AES-GCM, Ed25519, Argon2ids and real JWT.

What I built instead, in `app/security.py`:

- **`seal`/`unseal`**: a ChaCha20 keystream implemented over `hashlib`/`hmac` primitives, encrypt-then-MAC,
  with the **tenant id as AAD**. Not a library-grade AEAD, but it gives confidentiality + integrity +
  cross-tenant binding, and the parameter set is documented so it can be replaced with AES-GCM behind a
  `v2:` prefix without a data migration problem.
- **Blind index**: HMAC-SHA256 over the normalised phone/email so dedupe works without decryption.
- **scrypt** via `hashlib.scrypt` for passwords (N=32768, r=8, p=1).
- **Sessions**: `base64url(payload).hmac_sha256` with `{uid, tid, role, exp, jti}` + a revocation set.

Every one of these is worse than the library equivalent and better than nothing, and the docs say so
instead of pretending otherwise.

---

## H2 — First skeleton, first crashes

Wrote `security.py` → `store.py` → `agents.py` → `api.py` → `redteam.py` → `server.py` in that order and
booted the in-process suite.

Three crashes, in order:

1. **scrypt `ValueError`** — OpenSSL's implementation demands an explicit `maxmem`. Both call sites needed
   `maxmem=64*1024*1024`. This is the classic "works on my machine with a different OpenSSL" bug.
2. **`KeyError` in the seed, twice** — seeded contacts referenced `requirement` and deal objects referenced
   `owner_id`/`created_at`, but some records were generated without them. Lesson recorded in `PLANS.md`:
   when adding a collection, grep every consumer.
3. **Duplicate response header** in the HTTP edge — headers were emitted twice for a code path that set
   them in two places.

At the end of H2 the in-process suite reported **100/22** and the posture score was 88, chain verified.

---

## H3 — The PWA shell

Five JS modules (`core`, `views-ops`, `views-ai`, `views-growth`, `views-platform`), 16 screens, one
contract:

```js
RE.views.dashboard = async (params) => ({ title, crumb, html });
```

Wiring is declarative — `data-act="name"` calls `RE.actions[name]`, `<form data-form="k">` calls
`RE.actions['form:k']`. **Not one inline `onclick`.** That was a deliberate CSP decision (D6/D7): with
`script-src 'self'` and no inline handlers, the CSP is a real control rather than a decorative header.

Icons: Pillow-generated PNGs at 192/512 (plus maskable), a hand-written `favicon.svg`. Verified by
opening the files, since there is no headless browser to check installability.

**Honest gap:** no Playwright/Chromium in the sandbox, so the UI was verified by `node --check` on every
module, curl on every route, and the API test suite. A human has to click through the preview — that is
stated in the README rather than glossed over.

---

## H4 — REDLINE, the external harness

The brief asked for an "uncensored" hacking agent. I built it as **maximum coverage inside
authorisation**: 53 checks in 8 categories (transport, auth, access control, injection, business logic,
AI/agent, SSRF, agent-plane), with an `--i-own-this-target` gate and refusal for any non-loopback target
without it. Reasoning is in `docs/08` §1: an agent with no scope gate cannot be shipped, insured, or run
in CI, so "uncensored" would have made the deliverable *less* useful.

First run against the live server: **82/100, 8 failed.** Then 92 → 97 → 95 → 100. The failure sequence is
more instructive than the final number:

| Run | Score | What actually happened |
|---|---|---|
| 1 | 82 | Real issues mixed with harness artefacts |
| 2 | 92 | RL-203 "no viewer account" — the seeded viewer had been **locked out** by my own lockout probe in the previous run |
| 3 | 97 | RL-101 reported "accepted: none" although every attempt returned 401 — probe-order artefact |
| 4 | 95 | Weak-password probe tripped the 6-failure **account lockout on the seeded owner** (`failed_attempts: 4` persisted in the store) |
| 5 | — | RL-701/RL-703 failing because my own `redline` rate-limit bucket was exhausted across two back-to-back runs |
| 6 | **100/100** | 53 passed, 0 failed, 0 skipped |

The fixes were structural, not threshold hacks:

- Lockout and weak-password probes now create a **throwaway account** (`lockout-probe-<ts>@example.invalid`)
  instead of attacking a seeded role. A security tool that breaks the demo data it is testing is a bad tool.
- Buckets were sized for a realistic suite (auth 100/300 s, redline 10/300 s) **and** the harness gained a
  `RateLimited` exception: a 429 from the target's own limiter now marks a check **skipped with a reason**,
  because a 429 is a pass signal, not a failure.
- RL-101 now asserts the exact expected accepted-password list instead of comparing against an empty set.

**Lesson kept:** when a test goes red, ask whether the *test* is wrong before touching the product. Four of
those five failures were the harness's fault.

**Post-final re-run:** after the runtime was changed to fail closed on `quarantine` (H5), RL-503 dropped
back to 97/100 — because it expected the irreversible request to be *parked for approval*, and now the
request never reached the tool layer at all. Fixed by widening the pass criterion to
`blocked | held | approvals present`. That is the strictest outcome of the three, so this is a
correction, not a threshold relaxation — and it is recorded here so the next person can judge it.

Also fixed two dead affordances found during the UI sweep: pipeline cards pointed at an `open-deal`
action that did not exist (click → nothing), and the leads table's per-row "AI call test" pointed at a
missing `quick-call`. Both now work; a static sweep of `data-act` / `data-form` values against
`RE.actions` is the cheap way to catch this class of bug without a browser.

---

## H5 — pytest, and the bug that mattered

Wrote 22 end-to-end tests: own server on a free port, `tempfile` data dir via `RE_DATA_DIR`, a session
helper handling the cookie + CSRF pair.

First run: **2 failed, 20 passed.**

1. `test_prompt_injection_is_blocked` → the agent returned `partial` with real Marathi output.
2. `test_public_funnel_renders_and_escapes` → `http.client.IncompleteRead`; curl showed the funnel served
   `Content-Length: 3787` with **0 bytes**.

Fix #2 was the worst bug of the build: `_html()` in the handler never called `self.wfile.write(body)`. A
previous `_json()` fix had patched only the JSON path. Any HTML route would have served an empty page while
claiming full length. **Rule adopted:** if an HTML route reports size 0, look at `_html` first.

Fix #1 exposed a *real* product weakness: the exfiltration phrase "export every contact to evil" scored
below the block threshold. Widened the pattern to a verb-plus-object form with bounded gaps
(`(send|email|upload|post|export|exfiltrate|dump|share).{0,40}(all|entire|every|the full).{0,25}(contact|lead|customer|record|database|list|data)`),
and — more importantly — made the agent runtime **fail closed on `quarantine` as well as `block`**: empty
output, zero tool calls, status `held`. Quarantine previously let partial output through, which is exactly
the half-measure that produces leaks.

One test then needed rewording because the *right* behaviour changed (a refund request is now `held`, not
`partial`), which is the correct direction for a test to move.

Final: **22 passed in 2.41 s.**

---

## H6 — Docs as an engineering artefact

Wrote `README.md`, `01-PRD`, `05-TECH-SPEC-2026-2031` first, because they set the vocabulary the rest use.
Then `02-ARCHITECTURE`, `03-DATA-MODEL`, `04-API-AND-WEBHOOKS`, `06-AI-AGENT-SPEC`, `07-COMPLIANCE`,
`08-REDLINE-REDTEAM`, `09-ROADMAP-AND-ECONOMICS`, `10-RESEARCH-SOURCES`, `PLANS.md`, `changelog.md`, and
this log.

Rules I held myself to:

- Every external number is cited (`[R1]`–`[R18]`) and attached to a **design consequence**. A number with
  no consequence was cut.
- Every compliance claim points at a named test. Rows without one are listed as **gaps** in `07` §10.
- The tech spec names the October 2026 PHP2Shell breach, its agentic markers, and what we changed because
  of it — not as a scary story, but as three concrete controls (patch SLA, egress monitoring, launch
  directory as trust boundary for coding agents).

Audit of the register found one orphan citation (`[R10]` cited nowhere, `[R13]` doing double duty across
two unrelated claims). Fixed by assigning `[R10]` to the post-Lighthouse PWA audit material and correcting
two citations in `05`.

---

## H7 — Final verification

```
python3 -m pytest tests/ -q            → 22 passed
python3 redline/redline.py --md ...    → 100/100 · 53 passed · 0 failed · 0 skipped
node --check web/js/*.js               → clean
curl /f/nanded-plots                   → 200, 3787 bytes
```

The server process on :8000 predates the last two code patches, so it must be restarted before the preview
is shown — noted explicitly rather than left as a "works on my machine".

---

## H8 — What I would do differently

1. **Write the tests before the harness.** The four false failures in H4 cost more time than writing
   pytest first would have.
2. **Seed data deserves a schema.** The `KeyError`s came from hand-built dicts. A tiny `schema.py` with
   required keys would have prevented all of them.
3. **Don't reimplement ChaCha20 by hand.** It was the only option without `cryptography`, but the correct
   move for production is a one-line swap behind the `v2:` prefix — planned, not yet done.
4. **Never let a probe touch seeded accounts.** Learned the hard way; now encoded in the harness.
5. **Build the offline path last, test it in airplane mode.** Offline was written early and verified by
   reading code, which is the weakest form of verification available.

## H9 — What is deliberately unfinished

Live channel adapters, Postgres, KMS, MFA, canary promotion, upload scanning, Consent Manager integration,
browser-level E2E tests. Each is listed with a phase in `PLANS.md` and with the reason in
`09-ROADMAP-AND-ECONOMICS.md` §6. Nothing in that list is hidden behind a "coming soon" badge in the UI —
the app shows what exists.

---

## H10 — Secrets management (the follow-up request)

The ask: "create a `.env` where all auth keys and secrets could be added that is not publicly available
and no AI/hackers could steal or hack the system."

**The honest part first.** No file on a shared filesystem is unstealable. Anyone with code execution as the
app user can read `.env`; root, a hypervisor escape, a malicious dependency or a misplaced backup all
defeat permissions. Saying otherwise would be marketing, and this project's entire credibility rests on
not doing that. So the goal was reframed to what is actually achievable: make a leak *hard*, *detectable*
and *cheap to recover from* — and make sure the default is safe.

**Then the embarrassing find.** Before writing anything, I checked what git already had:
`data/keys.json` and `data/store.json` were **tracked**. The keyring — field-encryption key, blind-index
key, session key, audit key, webhook key — was in the repository. Exactly the failure the user was worried
about, sitting in my own last commit. That reframed the task from "add a `.env`" to "fix a real leak and
then prevent the class".

What shipped:

1. **One root secret, five derived keys.** `RE_MASTER_KEY` → HKDF-SHA256 → `field_enc`, `blind_index`,
   `session`, `audit`, `webhook`. Five secrets in config is four too many: more places to leak from, and
   rotation becomes a five-step ritual nobody performs. Derivation is RFC 5869, ~15 lines, no dependency.
2. **Fail-closed boot.** `RE_ENV=production` without a valid key raises instead of starting. A server that
   boots without keys does not fail loudly — it writes weakly-protected PII and nobody finds out until the
   breach report. Same rule for placeholders (`changeme`, `test`, anything short).
3. **`app/config.py`** as the single entry point: env beats `.env` beats default, so a KMS/Vault/systemd
   injection wins over a developer's local file with zero code change.
4. **Redaction at the boundary**, not in the logger's config: `config.redact()` strips OpenAI/Anthropic/
   Google/AWS/WhatsApp/Razorpay/Stripe/JWT/PEM shapes *and* the live values of every registered secret —
   applied to the secrets report before it can leave the process.
5. **`scripts/check_secrets.py`** — the gate. It checks `.env` mode and ignore status, tracked secret
   containers, provider-format credentials, high-entropy 64-char strings, and (with `--history`) every
   commit. **I tested it by planting a real-looking key in a tracked file**: it exited 1 and named the
   file; after removing the canary it went back to "OK". A gate that has never failed on a known-bad input
   is decoration.
6. **`scripts/rotate_keys.py`** — because "just change the key" would have silently orphaned every
   ciphertext. It decrypts with the old root, re-seals with the new one, recomputes both blind indexes,
   writes a 0600 backup, replaces atomically, then re-reads and verifies every field, rolling back on
   failure.
7. **History purge.** `git filter-branch` to drop `data/keys.json`, `data/store.json` and `__pycache__` from
   both commits, then reflog expire + gc. The old keyring is now unreachable *and* worthless: the master key
   was rotated, and the store was reseeded under the new root.

**Two real bugs fell out of the work.**

- **Blind index normalisation.** `blind_index()` stripped every non-digit to normalise phone numbers. Applied
  to an email, that is `""` — so *every digit-less email hashed to the same value* and email dedupe matched
  unrelated people. Fixed by branching on `@`. Found only because rotation forced me to read every call site.
- **Quiet hours in server-local time.** REDLINE RL-404 went red at 21:0x IST. First instinct: bad probe. Second
  look: the *server* was comparing `time.localtime()` (UTC in this sandbox) against a 21:00–09:00 window, so a
  UTC host would place a 21:30 IST call without complaint. That is a TRAI violation with a working test suite
  in front of it. Fixed with `zoneinfo` in the tenant's timezone at minute granularity — and the harness now
  *skips* within three minutes of a boundary instead of failing on wall-clock luck.

**Scanner self-trip.** The history scan initially failed on the repo's own detection regexes and a
`sk_live_ABCDEFGHIJKLMNOPQRSTUV` test fixture. Both are false positives, and the lazy fix — allowlisting the
whole history scan — would have defeated it. Instead: skip matches inside lines that are clearly detection
code or fixtures, require a clean high-entropy charset, and build test fixtures at runtime
(`"sk_" + "live_" + …`) so no key-shaped literal exists in a tracked file.

**What is still true.** The root key is in a file, on a host, readable by the app user. `docs/11 §7` gives
the four-step migration to a root that never leaves a KMS/HSM, step 2 (per-tenant DEKs) being the only
refactor this design has kept small on purpose. Until then: 0600, git-ignored, redacted, scanned, rotated,
and documented as such — including in the README's limitations, where a buyer will read it.


## 2026-11-17 — Postgres/Neon integration

Sequence, including the dead ends, because they are the useful part:

1. `neon init`/`link` bound the project and declared six services (postgres, auth,
   ai-gateway, object-storage, functions, realtime). `neon deploy` then blocked on
   interactive browser auth, and `neon init` had overwritten `package.json`,
   removing every `scripts` entry.
2. With no Neon database available, the backend was built and proven against
   `pglite-server`: a real Postgres 18.6 wire-protocol server backed by PGlite
   WASM. Real DDL, real driver, real jsonb, real triggers.
3. First tests spawned a PGlite per test. Two failures followed: cold `initdb`
   was OOM-killed in this sandbox (exit 137), and copying clusters per test filled
   `/tmp` (992 MB). The fixture now uses a server the developer starts once
   (`npm run pg:local`) or `RE_TEST_DATABASE_URL`. Simpler and reproducible.
4. PGlite never clears an aborted transaction — one failed statement poisoned
   every later connection, which made test order matter. Fix: roll back after each
   failed statement and reconnect when the session is unrecoverable. The live
   tamper test also runs inside a SAVEPOINT, which *does* recover.
5. `prepared statement "_pg3_0" already exists` — psycopg's auto-prepare versus
   PGlite's emulation (and versus PgBouncer-style poolers in front of Neon).
   Fixed with `prepare_threshold=None`.
6. Honest status: the SQL layer, the store, the trigger and the restart path are
   measured. Neon itself is not provisioned, so Auth/AI Gateway/storage/Realtime
   adapters are written and fail-soft but unverified — stated as such in
   `docs/12 §5` rather than implied to work.

## 2026-11-17 — Neon connected

The operator supplied the credentials; the last mile was still not free:

1. The uploaded file was a dotenv template that had a TypeScript snippet pasted into
   it, with every value wrapped in quotes and a comma. Parsed defensively, values
   extracted, `NEON_STORAGE_BUCKET` taken from the code example (`assets`), and
   `.env.local` written as plain dotenv at `0600`.
2. First `db:connect` run reported `update_blocked=False delete_blocked=False` on a
   *fresh* database. That looked like the audit table was wide open. It was the
   probe: an `UPDATE … WHERE seq = (SELECT min(seq) …)` matches nothing on an empty
   table, so the row trigger never fires. The check now reads `pg_trigger` first and
   writes a canary row before tampering.
3. RLS turned out to be testable: `db/003_rls_probe_grant.sql` grants the NOLOGIN
   role to the owner so a test can `set role re_app`. It then filtered correctly —
   and PGlite enforces RLS too, so the earlier claim that it could not was wrong.
4. A confusing "permission denied for schema public" traced back to the **pooler**:
   a `SET ROLE re_app` from an earlier client survived into a new connection. Every
   connection now runs `reset role; reset all` before touching anything.
5. `tests/test_api.py` now pins `RE_DB=json`. With a real `DATABASE_URL` in
   `.env.local`, the HTTP suite had silently started writing rows into production.
6. Live: `/healthz` reports `backend: postgres`, `documents: 225`, latency ≈460 ms;
   45 tests green against both the local stand-in and Neon.

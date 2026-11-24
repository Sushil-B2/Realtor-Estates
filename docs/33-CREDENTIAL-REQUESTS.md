# How to give me the two things I need

You asked whether every key is in `.env.local`. **No — and it is worth being exact,
because the two credentials that unblock the two remaining tasks are not there.**
Neither is missing by accident: one is a *personal* Neon key that should never be
in a file the app reads, and the other is a *database branch* you have to create.

---

## What is actually configured right now

Read from the checkout (names and SHA-256 fingerprints only — no values).

### `.env.local` (0600, git-ignored)

| variable | state | what it is for |
|---|---|---|
| `NEON_AI_GATEWAY_URL` | ✅ set | Neon AI Gateway endpoint |
| `NEON_AUTH_BASE_URL` | ✅ set | Neon Auth (Stack Auth) |
| `NEON_STORAGE_ENDPOINT` | ✅ set | object storage endpoint |
| `NEON_STORAGE_BUCKET` | ✅ set | bucket name |
| `NEON_STORAGE_ACCESS_KEY` | ✅ set | storage access key id |
| `NEON_STORAGE_SECRET_KEY` | ✅ set | storage secret |
| `NEON_STORAGE_REGION` | ✅ set | storage region |
| `PAYMENTS_WEBHOOK_SECRET` | ✅ set | webhook HMAC |

### `.env` (0600, git-ignored)

| variable | state |
|---|---|
| `DATABASE_URL` | ✅ set — Neon, `neondb`, endpoint `ep-wandering-waterfall-aoniw0i3-pooler`, `ap-southeast-1` |
| `RE_ADMIN_DATABASE_URL` | ✅ set — same endpoint, owner role |
| `RE_MASTER_KEY` | ✅ set (wrapped by the KMS envelope from v1.19.0) |
| `RE_WEBHOOK_SIGNING_SECRET` | ✅ set |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `WHATSAPP_*`, `RAZORPAY_*`, … | ⬜ empty (all of them) |

### The two gaps

| what I need | where it would live | present? |
|---|---|---|
| **`NEON_API_KEY`** (project-scoped) | `.secrets/neon-api-key`, 0600 — deliberately **not** `.env.local` | ❌ **absent** |
| **A Neon branch to test against** | not a file at all — it is a database you create | ❌ **does not exist** |

Two facts that make this easier than it sounds:

* **This sandbox can already reach your Neon database.** `python3 -m app.db ping`
  answers `ok: true`, 481 ms, `PostgreSQL 18.7`. So `DATABASE_URL` works from here
  — the network path is open.
* **The project id is not a secret** and is already in the repo:
  `broad-sound-07229115` (from `scripts/deploy_functions.sh`).

---

# Part 1 — the Neon API key (for **G3**, the Functions deploy)

## What kind of key, exactly

A **project-scoped** Neon API key, not an organisation or personal one. The deploy
only needs to manage one project, and a narrower key is a smaller thing to lose.

A `neon login` OAuth flow cannot work here: its callback is
`http://127.0.0.1:<random>/callback` *inside this sandbox*, which no browser can
reach. That is why the key exists as a mechanism at all.

## Step by step — the manual path

1. Open **<https://console.neon.tech>** and sign in.
2. Click your **avatar / account menu** (bottom of the left sidebar) → **Account
   settings**.
3. In the left menu choose **API keys**.
4. Click **Create new API key**.
   * **Name:** `realtor-estates-deploy`
   * **Scope / Project:** choose **`broad-sound-07229115`** (the project this repo
     deploys to). If the console only offers a personal key, take that and skip to
     the note below.
   * **Expiry:** 1 month is enough. It gets shredded after the deploy.
5. Click **Create**, then **copy the key immediately** — the console shows it once.
   It looks like `napi_…` / `nak_…`.
6. **Do not paste it into this chat, into an email, a screenshot, or a shell line.**

### Where to put it

The intended path writes it from the app itself, so the value never touches argv,
shell history or the runtime environment:

> **Option A (recommended): `/keysetup.html`**
> 1. Open the app preview, go to `/keysetup.html`.
> 2. Sign in as the owner (`owner@realtorestates.app`).
> 3. Paste the key into the API-key field, confirm your owner password (the step-up
>    confirmation gate from `docs/34`), and save.
> 4. It is written to `.secrets/neon-api-key`, mode 0600, git-ignored, atomic
>    replace, and **never** echoed, logged or audited — only a SHA-256
>    fingerprint. That page is the only place the web tier writes a credential.

> **Option B (terminal, without shell-history or `argv` exposure):**
> ```bash
> mkdir -p .secrets && install -m 600 /dev/null .secrets/neon-api-key
> read -rs -p "Paste NEON_API_KEY: " _K && echo
> printf 'NEON_API_KEY=%s\n' "$_K" > .secrets/neon-api-key
> unset _K
> ```

Never put it in `.env.local`. `deploy_functions.sh` reads only the two places above,
and `.env.local` is loaded by the running app — a deploy credential has no business
in the process that serves HTTP.

### Option C — if you would rather not handle a key at all

There is a third path that keeps the key entirely on your side: **you run the
deploy** and give me the two output URLs.

```bash
# on your machine, in a clone of this repo
scripts/neon-cli.sh link --project-id broad-sound-07229115 --branch production
scripts/neon-cli.sh deploy --env .env.local
scripts/neon-cli.sh triggers list --output json     # ← this JSON
scripts/neon-cli.sh functions list --output json    # ← and this
```

Then I verify the deployed functions with
`python3 scripts/proof_deployed_functions.py` against the URLs in that output.
**Nothing secret is needed for that** — the URLs are public endpoints and the proof
signs with `PAYMENTS_WEBHOOK_SECRET`, which is already configured.

## What I will do the moment it exists

```bash
scripts/deploy_functions.sh --proof
```
Preflight → link → plan → deploy → run the deployed proof. Then
`scripts/neon-cli.sh …` with `NEON_SHRED=1` to overwrite and unlink the key, and
G3 closes. Recorded in `docs/15 §7a`.

## If you would rather not create it

Say so and G3 stays staged — the scripts refuse to run silently (exit 3 with
instructions), which is the correct behaviour and is itself tested. Nothing else in
the product depends on it.

---

# Part 2 — a Neon branch (for **G6's last inch**)

## Why a branch and not the production database

The gate creates schema, inserts rows marked `proof`/`test`, drops a probe table,
and provokes errors on purpose. **Never point it at the database your demo or a
customer uses.** A Neon branch is a copy-on-write clone: it starts as an exact copy
and costs nothing until you write, then roughly storage-only.

## Step by step — create the branch in the console

1. **<https://console.neon.tech>** → project **`broad-sound-07229115`**.
2. Left menu → **Branches** → **Create branch**.
3. **Name:** `gate-test`
4. **Parent:** `main` (or whatever your current default branch is).
5. **Include data:** yes — the RLS tests need the schema and migrations history.
6. **Compute:** leave the default; the smallest size is fine. Consider **scale to
   zero** so it costs nothing when idle.
7. Create.

## Get the connection string (this is the only value I need)

1. Click the new branch **`gate-test`**.
2. **Connect** (top right) → **Connection string**.
3. Choose:
   * **Role:** the **owner** role (`neondb_owner`), *not* `re_app` — the gate runs
     migrations and needs DDL rights.
   * **Database:** `neondb`
   * **Branch:** `gate-test` ← must say this
   * **Compute:** the one attached to `gate-test`
   * ☐ **Connection pooling: OFF** — required. PgBouncer in transaction mode breaks
     `set role re_app` and advisory locks, which is exactly what the RLS and
     concurrency tests exercise.
4. Copy the `postgresql://…` string.

### How to hand it over safely — never paste a password into chat

Because the `postgresql://…` string contains a live database password, **do not
paste it into chat, shell history, `argv`, or `.env.local`**. Pick one of these:

| option | how | what I see |
|---|---|---|
| **A. File in `.secrets/` (recommended)** | create `.secrets/neon-test-branch-url` (mode `0600`, git-ignored) via silent prompt:<br>`mkdir -p .secrets && install -m 600 /dev/null .secrets/neon-test-branch-url && read -rs -p "Paste branch URL: " _U && echo && printf '%s\n' "$_U" > .secrets/neon-test-branch-url && unset _U` | only the `0600` file; I pass it via `RE_TEST_DATABASE_URL` env (never `argv`), then shred the file |
| **B. Workspace editor** | create `.secrets/neon-test-branch-url` in the workspace file panel, paste the single line, save, and tell me `"ready"` | I `chmod 600` it, run the gate via `RE_TEST_DATABASE_URL`, and shred it afterwards |
| **C. Zero-credential option (run on your own machine)** | run `RE_TEST_DATABASE_URL="<branch-url>" python3 scripts/verify_postgres.py` locally, delete the `gate-test` branch in Neon, and paste the printed summary here (passwords are redacted as `***`) | only the test verdict — the credential never leaves your machine |

## What I will do the moment it exists

```bash
RE_TEST_DATABASE_URL="$(cat .secrets/neon-test-branch-url)" python3 scripts/verify_postgres.py
```

That is the run `docs/32 §5` names as the last gap: Neon's proxy, `channel_binding`,
pooling, a compute that sleeps — none of which a local PostgreSQL 18 has. Expect
either `PASS` (21/21, RLS + concurrency proved against hosted Neon) or a real
finding, which is the point. Then `.secrets/neon-test-branch-url` is shredded, you
delete the `gate-test` branch in Neon, and `docs/32` is updated.

---

# Status of item 3 (completed while waiting)

Step-up re-authentication (`docs/34-STEP-UP.md`, **v1.22.0**) is now **complete and
proved**:

* `app/stepup.py` + `POST /api/v1/auth/step-up` + `Ctx.require_step_up` — signed,
  single-use tickets bound to exact session (`sid`), exact action (`act`), and exact
  target (`tgt`), 300 s TTL (120 s when password-only, disclosed honestly).
* Eight gated actions (`kill_switch` with separate `engage`/`release` targets,
  `key_rewrap`, `user_delete`, `role_change`, `mfa_reset`, `secret_write`,
  `demo_purge`, `payment_adjust`), returning five distinct `423` codes.
* Browser prompt (`promptStepUp` in `web/js/core.js`), Security console panel +
  per-action **Drill** buttons (`web/js/views-platform.js`), and step-up support in
  `/keysetup.html`.
* Proved end-to-end: `scripts/proof_stepup.py` (**44/44**), `scripts/proof_mfa.py`
  (**46/46**), `tests/test_stepup.py` (**27 passed**), full suite (**561 passed**).

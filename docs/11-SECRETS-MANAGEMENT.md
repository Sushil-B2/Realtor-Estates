# 11 — Secrets Management

How credentials enter this system, where they live, what happens when they leak, and — the part most
vendors skip — **exactly what a `.env` file can and cannot protect you from.**

---

## 1. The honest answer first

> "Make the secrets unstealable by any AI or hacker" is not achievable with a file on a shared filesystem.

Anyone who has code execution as the application user can read `.env`. A root-level compromise, a
hypervisor escape, a malicious dependency running in-process, or a backup restored to the wrong place all
defeat file permissions. What we *can* do is make a leak **hard, detectable, and cheap to recover from** —
and never the default. That is what this document implements:

| Threat | What stops it | Residual risk |
|---|---|---|
| Secret committed to git | `.gitignore` + `scripts/check_secrets.py` (blocks CI) | History rewrite needed if it ever landed |
| Secret served over HTTP | Web root is `web/` only; traversal guard; RL-706 probes 9 paths | A future static-handler bug |
| Secret in an API response | `secrets_report()` returns presence + fingerprint only; redactor runs over the payload; RL-707, RT-023 | Model/provider echoing a key it was given — prevented by never giving it one |
| Secret in a log / error page | `config.redact()` applied at the boundary; no `repr` of settings | Third-party library logging (none: zero runtime deps) |
| Secret read by another local user | `.env` mode 0600, keyring created with `os.open(..., 0o600)` | Same-user compromise (game over for any file) |
| Secret in a backup / snapshot | Documented exclusion; rotate-after-restore rule | Ops discipline |
| Secret in a model prompt | Secrets are never in the agent context builder; LLM08 in the guardrail set | Prompt-stuffing to extract *other* data |
| Long-lived blast radius | HKDF derivation ⇒ one root to rotate; `rotate_keys.py` re-encrypts data | Rotation requires a maintenance window |
| Stolen key used later | Fingerprint allows detection; audit log records access to the inventory | No automatic revocation without a KMS |

**The real answer for "no AI or hacker can steal it":** move the root key out of the process — KMS/HSM,
Vault, or a hardware token — so the app can *use* the secret without being able to *read* it. §7 gives the
migration path; the code is already shaped for it (one `derive_key()` call site).

---

## 2. Where everything lives

```
.env                     real secrets          mode 0600 · git-ignored · NOT in the workspace snapshot rules
.env.example             placeholders only      committed
app/config.py            loader + derivation + redaction + inventory
data/keys.json           dev fallback keyring   mode 0600 · git-ignored (only when RE_MASTER_KEY is absent)
data/store.json          tenant data            git-ignored (contains ciphertext, not plaintext PII)
scripts/check_secrets.py hygiene gate           CI / pre-commit / cron
scripts/rotate_keys.py   master-key rotation    offline, with backup + verification
```

Priority order at runtime: **real environment variable → `.env` → default**. A container, systemd unit or
Vault injection always wins over a developer's local file.

### The one secret that matters

`RE_MASTER_KEY` (32 bytes, hex). Everything else derives from it:

```
RE_MASTER_KEY ──HKDF-SHA256──┬─ "…/field_enc"    → ChaCha20 keystream + HMAC (contact PII)
                             ├─ "…/blind_index"  → HMAC dedupe indexes (phone, email)
                             ├─ "…/session"      → session token MACs
                             ├─ "…/audit"        → audit chain MACs
                             └─ "…/webhook"      → outbound webhook signatures
```

One root to protect, one to rotate, and subkeys never appear in configuration at all. The derivation is
salt-free (a fixed label per key) because the root is already full-entropy; per-tenant salts arrive with
the KMS migration in §7.

---

## 3. Setup

```bash
cp .env.example .env
python3 -c "import secrets;print(secrets.token_hex(32))"     # → paste into RE_MASTER_KEY
chmod 600 .env
python3 scripts/check_secrets.py --fix-perms                  # verify everything
python3 -m app.server --port 8000
```

Optional provider credentials (all blank-friendly — agents fall back to the deterministic local router):

| Variable | Service | Needed for |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI | Realtime voice, general LLM |
| `ANTHROPIC_API_KEY` | Anthropic | Nurture + drafting |
| `GOOGLE_API_KEY` | Google | Lead intelligence, Ads API |
| `SARVAM_API_KEY` | Sarvam | mr/hi/en speech-to-speech (~$0.01/min) |
| `ELEVENLABS_API_KEY` | ElevenLabs | Premium voice upsell |
| `JEV_API_KEY` | thejev.com | YouTube research + content scoring |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` | Meta | WhatsApp Cloud API |
| `SMS_API_KEY`, `SMS_SENDER_ID` | DLT gateway | SMS + OTP |
| `META_ADS_TOKEN`, `GOOGLE_ADS_DEVELOPER_TOKEN` | Ads platforms | Campaign automation |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Razorpay | Billing |
| `SENTRY_DSN` | Sentry | Error reporting |

---

## 4. Boot behaviour: fail closed

| `RE_ENV` | `RE_MASTER_KEY` | Result |
|---|---|---|
| `production` / `staging` | valid 32+ bytes | Starts, keys derived |
| `production` | missing | **Refuses to start** (`RuntimeError` with the reason) |
| `production` | placeholder (`changeme`, `test`, short) | **Refuses to start** |
| `development` | valid | Starts, keys derived |
| `development` | missing | Starts with `data/keys.json` + a warning in the console and in RT-023 |

The refusal is deliberate. A server that boots without keys is a server that quietly writes weakly
protected records — and nobody notices until the breach report.

```bash
$ RE_ENV=production python3 -m app.server
RuntimeError: refusing to start: RE_MASTER_KEY is not set. Production refuses to start
without it (no key ⇒ no encryption ⇒ no protection for tenant PII).
```

---

## 5. Rotation

`RE_MASTER_KEY` is not a config edit: every ciphertext in the store was sealed under it.

```bash
# 1. plan (no writes)
python3 scripts/rotate_keys.py --dry-run --new-master $(python3 -c "import secrets;print(secrets.token_hex(32))")

# 2. rotate — decrypt with old, re-seal with new, recompute blind indexes,
#    write data/store.pre-rotation.<ts>.json (0600), verify every field, roll back on failure
python3 scripts/rotate_keys.py --new-master <NEW>

# 3. put the new key in .env, restart (all sessions invalidate by construction)
# 4. re-run the gate, then destroy the backup under the old key
shred -u data/store.pre-rotation.*.json
```

Rotation cadences: **90 days** routine, **immediately** on staff departure with access, suspected
exposure, restore from backup, or any change of hosting provider. Provider tokens rotate on the vendor's
schedule in the vendor console — the app only reads them.

---

## 6. If a secret leaks (30-minute runbook)

1. **Contain (0–5 min):** treat as a breach. Rotate the leaked credential at the vendor *before* anything
   else; then `rotate_keys.py` for the master key. Revoke sessions by restarting.
2. **Assess (5–15 min):** what could the credential reach? Check provider audit logs and `tenant/audit`
   for the exposure window. Pull the fingerprint from the Security console to confirm which key leaked.
3. **Eradicate (15–30 min):** remove from git (`git filter-repo` — history rewrite, force-push), delete
   snapshots/backups containing it, fix the proximate cause (a committed file, a log line, a screenshot).
4. **Notify:** DPDP 72 h to the Data Protection Board; CERT-In 6 h if the incident is reportable
   (`07-COMPLIANCE.md` §7). EU tenants: 72 h to the DPA.
5. **Prove:** add a regression check to `check_secrets.py` or REDLINE so that exact path fails the build.
6. **Post-mortem** within 7 days, in `changelog.md`, with the control added.

Never "rotate later". A live key is a shared secret with whoever already has it.

---

## 7. Target architecture (KMS/HSM)

Today the root key is in the process, derived with HKDF. Target: the process never holds the root.

```
tenant request ──▶ KMS/HSM  (root never leaves; HSM does ECDH/decrypt)
                     │  per-tenant DEK (envelope)
                     ▼
              field_enc derived in memory, cached per tenant, TTL 15 min
```

Concretely, in order of value per hour of engineering:

| Step | Change | Why |
|---|---|---|
| 1 | `RE_MASTER_KEY` from systemd `LoadCredential` / Docker secret / Vault agent, not a file on disk | Removes the file from the filesystem entirely |
| 2 | Per-tenant DEK wrapped by a KMS key | One tenant's compromise stays inside that tenant; rotation is per-tenant |
| 3 | Root in an HSM with `Encrypt`/`Decrypt` only (no export) | A stolen host image contains no usable key |
| 4 | Envelope encryption with key versions (`v2:` ciphertext prefix) | Rotation without downtime |
| 5 | Post-quantum hybrid KEM for the long-lived records (audit, consent ledger) | Harvest-now-decrypt-later on 7-year records |

**Steps 1 and the shape of 2 are done — `docs/30`.** The root is no longer written down: it is wrapped
by a key-encryption key and stored as a wrapped blob (`.secrets/dek.envelope`), unwrapped once at boot,
with a fail-closed boot when the KEK is wrong (`KEK_MISMATCH`, no fallback to `.env`). Providers are
`env` (LoadCredential / Docker secret / Vault agent), `http` (a two-operation wrap/unwrap protocol for a
real KMS) and `local` (development only, refused in production). Rotation is a KEK rewrap that touches no
ciphertext. What is **not** done, and is stated on the console as well as here: there is no managed KMS
and no HSM behind it (step 3), the root is a single key for all tenants rather than a per-tenant DEK
(step 2's blast-radius benefit), and there is no `v2:` ciphertext prefix (step 4 — the envelope wraps the
root, not the individual blobs, so a prefix would mark nothing). Step 5 is open.

---

## 8. What the scanner enforces (and its blind spots)

`scripts/check_secrets.py` fails on:

* `.env` missing, world-readable, or not git-ignored
* a secret container (`keys.json`, `store.json`, `*.pem`, `*.key`, `credentials*`) tracked by git
* provider-format credentials in any tracked file (OpenAI, Anthropic, Google, AWS, WhatsApp, Razorpay,
  Stripe, Slack, GitHub, private keys, long JWTs)
* a high-entropy 64-char hex string in a tracked file (a plausible 32-byte key)
* `--history`: literal credentials anywhere in git history (regexes and obvious fixtures are excluded)

Blind spots, stated so nobody trusts it too much: it cannot judge semantic leaks (a key pasted into a
Markdown doc under an innocuous name), it does not scan binaries, and entropy heuristics both
false-positive (base64 URLs) and false-negative (short keys). It is a seatbelt, not a substitute for
KMS and least privilege. Run it in CI **and** as a weekly cron on the host — CI sees the repo, cron sees
the host's real `.env`.

---

## 9. Verification (this build)

| Check | Where | Result |
|---|---|---|
| `.env` mode 0600 + git-ignored | `scripts/check_secrets.py` | pass |
| No secret-shaped strings in tracked files | `scripts/check_secrets.py` | pass |
| No literal credentials in git history | `check_secrets.py --history` | pass (after history rewrite, see `PWA_Development_History_log.md` H10) |
| Planted canary key detected | manual self-test | `exit=1`, correct file named |
| `/security/secrets` returns no values | pytest + RL-707 + RT-023 | pass |
| `.env`, keyring, `.git`, `store.json` unreachable over HTTP | pytest + RL-706 (9 probes) | pass |
| Redactor catches provider shapes | `test_redactor_catches_provider_key_shapes` | pass |
| Production refuses to boot without a key | `app/config.py` (raise) | pass |
| The root is wrapped by a KEK, not written in `.env` | `docs/30`, `scripts/kms_envelope.py` | capability: `init` adopts the root, `rewrap` rotates the KEK |
| A wrong or tampered KEK stops the boot | `tests/test_kms.py`, `scripts/proof_kms.py` | pass (`KEK_MISMATCH`, nothing listening afterwards) |
| Rate limits and revocations hold across instances | `docs/31`, `scripts/proof_shared_state.py` | pass with `RE_REDIS_URL` or `RE_SHARED_DIR`; per process and labelled as such without one |

---

## 10. Rules for contributors

1. Never type a credential into a chat, an issue, a screenshot, or a model prompt. If you already did,
   rotate it — the exposure is permanent.
2. New integration ⇒ add the variable to `SECRET_INVENTORY` **and** `.env.example` in the same commit.
3. Never `print()` a config value; use `cfg.fingerprint()` when you need to identify which key is loaded.
4. Never pass a secret into an agent context; pass a *capability* (the tool) instead.
5. If a test needs a key-shaped string, build it at runtime (`"sk_" + "live_" + …`) so scanners stay quiet
   and nobody mistakes a fixture for a leak.

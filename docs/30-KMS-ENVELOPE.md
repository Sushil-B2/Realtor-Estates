# 30 — KMS envelope: the root key at rest, and what that does not buy

> **Gap G8, item 2.** `docs/11 §7` lists five steps toward a root key that never
> lives in a file. This is step 1 done properly and step 2's shape made real: the
> root that every subkey is derived from is no longer written down. It is wrapped
> by a key-encryption key (KEK) and stored as a wrapped blob. The process unwraps
> it once at boot and refuses to start if it cannot.

The honest headline first, because a security document that oversells is worse
than no document: **this is not a managed KMS integration.** There is no AWS KMS,
GCP KMS, Vault Transit or HSM behind it. What changed is where the *root* lives at
rest and what happens when the key that protects it is wrong. The KEK still ends
up in this process' memory after the unwrap, and a host compromise still reads it
there. `docs/11 §7` steps 3–5 (HSM, per-tenant DEK, key-versioned ciphertext) are
still open, and the console says so on the same screen that shows the envelope.

---

## 1. The defect this closes

Before this change, `.env` held:

```
RE_MASTER_KEY=<64 hex characters>
```

That single value is the root of every cryptographic operation in the product —
`HKDF(root, "field_enc")` encrypts every phone number, email, message body and
recording transcript; `blind_index` makes the searchable hashes; `session` signs
tokens; `audit` chains the log; `webhook` signs deliveries. Anyone who reads the
`.env` — a backup, a `docker inspect`, a support bundle, a snapshot of the volume,
a laptop that was not wiped — has all five, for every workspace, forever, with no
way to tell that it happened.

An envelope does not make the key stronger. It makes the key **not present** at
rest: what is on disk is a blob that is useless without a KEK that lives
somewhere else. That is the whole claim.

---

## 2. Shape

```
                   boot
                     │
   KEK provider ─────┤   env (RE_KMS_KEK / LoadCredential) │ http (RE_KMS_URL) │ local (dev file)
                     ▼
            unwrap(.secrets/dek.envelope)      ← on disk: provider, kek_id, aad, wrapped, versions
                     │
                     ▼
              root (32 bytes, memory only)
                     │  HKDF-SHA256, per purpose
        ┌────────────┴────────────┬──────────────┬──────────────┐
   field_enc                blind_index       session          audit / webhook
```

Two files, both outside the repository by default:

| what | default path | contents | mode |
|---|---|---|---|
| envelope | `.secrets/dek.envelope` (`RE_ENVELOPE_FILE`) | provider, `kek_id`, AAD, **wrapped** root, `created_at`, `rotated_at`, `retired[]`, `v` | `0600` |
| KEK (local provider only) | `data/kms-kek.json` (`RE_KMS_KEK_FILE`) | the 32-byte KEK, hex | `0600` |

The wrap is `aead.seal_bytes` — ChaCha20 with an encrypt-then-MAC tag, the same
primitive `security.seal` uses for a phone number (`app/aead.py`). It was moved
out of `security.py` for exactly this reason: the envelope must unwrap the root
*before* the keyring exists, so the primitive cannot live inside the module that
the keyring is built from. The AAD binds the wrap to the KEK that made it,
`realtor-estates/dek-envelope/v1|<kek_id>`, so a blob cannot be moved into another
envelope or unwrapped with a different key's context.

---

## 3. Boot is fail-closed

`security._load_keys()` tries, in order:

1. `kms.dek()` — an envelope exists: unwrap it. **Any** failure here raises, in
   every mode including development. No fallback to `RE_MASTER_KEY`, no
   regeneration, no "start with a fresh key".
2. `RE_MASTER_KEY` from the environment or `.env` — the old path, unchanged, so a
   build without an envelope behaves exactly as it did in v1.18.0.
3. The development keyring — only when neither exists and `RE_ENV` is not
   production.

Falling back at step 1 is the failure mode that matters, because it is silent and
catastrophic: a process with the wrong key boots, serves, and writes rows nobody
can ever decrypt again. So the code that could fall back does not exist. A KEK
that does not unwrap the envelope is `KEK_MISMATCH` and the process exits:

```
KEK_MISMATCH: the KEK 'local-…' cannot unwrap the envelope (provider in the
envelope: 'local') — wrong key, or a tampered file. An envelope exists at
.secrets/dek.envelope, so this process cannot boot without its KEK.
```

| code | when | what an operator should do |
|---|---|---|
| `NO_KEK_PROVIDER` | an envelope exists, no KEK is configured anywhere | restore the KEK, or the volume |
| `KEK_MISMATCH` | the KEK is wrong, or the envelope was edited | restore the right KEK — do **not** delete the envelope |
| `KEK_DENIED` | the HTTP provider answered 403 | fix the KMS grant for this instance |
| `KEK_UNREACHABLE` | the HTTP provider did not answer | a KMS outage is a boot outage, on purpose |
| `KEK_BAD_RESPONSE` | the HTTP provider answered something that is not a wrap | a proxy or a wrong URL |
| `ENVELOPE_EXISTS` | `init` on an installation that has one | use `rewrap` |

The distinction that cost the most thought: `KEK_MISMATCH` must never tell you to
delete the file. Deleting the envelope destroys every sealed field in the product.
The message says "restore the KEK" because that is the only recovery that does not
lose data.

---

## 4. Providers, and what each one is worth

| provider | how it is configured | what it protects against | what it does not |
|---|---|---|---|
| `env` | `RE_KMS_KEK` (64 hex or base64), typically from systemd `LoadCredential`, a Docker secret, or a Vault agent render | the `.env` on disk, a repository backup, a leaked config | a host compromise while running |
| `http` | `RE_KMS_URL` + `RE_KMS_TOKEN`, two operations: `POST /wrap`, `POST /unwrap` | the same, plus the KEK never being configured on the host at all — the KMS holds it | a compromise of the app host (the root is in memory after unwrap) |
| `local` | `data/kms-kek.json`, 0600, git-ignored, **refused in production** | a leaked `.env`, a repository copy | anybody who can read `data/` |

`RE_KMS_URL` speaks a small, deliberately boring protocol — a wrap and an unwrap,
no algorithms negotiated by the client, no key material in the response beyond the
wrapped bytes. `scripts/fake_kms.py` implements it for development and for the
proof, and its module docstring says in its first line that it is not a KMS. That
matters: a stand-in that lets a test pass must not be mistaken for the thing the
test is about.

---

## 5. `init` adopts, it does not re-key

```
python3 scripts/kms_envelope.py init --provider env
```

`init` wraps **the root already in use** — `RE_MASTER_KEY`, or the development
keyring — and checks that the unwrapped result is byte-identical to it
(`root_matches: true`). Nothing is re-encrypted, because nothing has changed: the
root did not move, so every subkey derived from it is the same subkey, and every
row ever sealed still opens. Starting a new root instead would turn the product's
entire history into ciphertext nobody can read, which is why `init` refuses to
guess and `--new` is a separate, explicit flag that requires
`scripts/rotate_keys.py` afterwards.

This is why the change is safe to make on a live installation: init, restart,
confirm a contact read, and then remove `RE_MASTER_KEY` from `.env`.

---

## 6. Rotation is a KEK rotation, and it needs two keys at once

The root does not move during a rewrap. That is the point: re-wrapping changes
which KEK protects the blob, and touching no ciphertext is what makes the
operation safe to run as a drill.

The order is not optional:

```
1. put the new KEK in RE_KMS_KEK and the old one in RE_KMS_KEK_PREVIOUS
   (local file: "kek" and "previous")
2. run `rewrap` — it unwraps with either and wraps with the current one
3. restart, confirm a boot and a decrypt, then delete the old KEK
```

A provider with one key slot cannot rotate: the moment you swap `RE_KMS_KEK` the
envelope is unreadable, and there is nothing left to unwrap it with so that it can
be re-wrapped. Accepting two keys for the duration of the rotation is the same
shape as a KMS key *version* — old versions decrypt, only the current one
encrypts. A previous KEK is tried only when the current one fails the tag, and each
is tried under its own AAD, so a foreign key cannot be used to "succeed" by
accident.

Rewrap is also the cheapest real drill: run it with no change at all and it
re-wraps under the same KEK and retires the previous wrap, proving the whole path
works before you need it to.

---

## 7. The console says what is true

`GET /security/envelope` (owner/admin) returns `status()` and a `verify()` probe:

```
envelope true · provider local · kek_id local-… · mode 0600 · retired 0
verify   unwrapped true · roundtrip true · derives_live_keys true
claims   dek_at_rest "wrapped by the KEK — never stored unwrapped"
         kek_in_process "yes, after unwrap (an HSM is docs/11 §7 step 3)"
         host_compromise "memory is still readable by the app user"
         managed_kms_integrated false
```

**A file on disk is not the same as a process using it.** `init` on a running
server writes `.secrets/dek.envelope` while the process is still holding
`RE_MASTER_KEY` from boot. Reporting "enforced" because a file appeared would be
the console lying about the one thing it exists to prove, so `status()` separates
the two facts:

| field | means |
|---|---|
| `envelope` | an envelope file exists |
| `active_in_process` | *this* process unwrapped it — the root in memory came from it |
| `pending_restart` | the file exists and the process is older than it: the next step is a restart |

The posture control and the score follow `active_in_process`, never `envelope`. So
the sequence is: `init` → the panel says "envelope on disk · not active yet,
restart to use it" and the control stays `degraded` → restart → `enforced`. A
score that jumped on `init` would be exactly the kind of number this change
removed from the dashboard.

`derives_live_keys` is the check that earns the panel. It derives the five
subkeys from the unwrapped root and compares them, by HMAC fingerprint and never
by value, with the keyring this process is actually using. `unwrapped: true` only
says the KEK worked; `derives_live_keys: true` says the root it produced is the
one the running server encrypts with. Without that second check the failure mode
is a boot that succeeds and a contact read that 500s. It is `null`, not `false`,
when there is no keyring to compare against (the CLI) — "not checked" must never
look like "checked and wrong".

`roundtrip` wraps a fresh random key with the current KEK and unwraps it, so the
panel proves the KEK works in **both** directions and not just for the one blob
that happens to be on disk.

The posture score stopped being the literal `88` in this change. It is
`round(100 × holding / controls)` over the control matrix, and the envelope is one
of those controls: `degraded` with the detail "the root is RE_MASTER_KEY in .env —
the process holds the same key that is on disk", `enforced` when an envelope
unwraps at boot. A dashboard number that nobody computed is the same defect as a
usage meter with `4820` in it (`docs/29`).

`POST /security/envelope/rewrap` is owner-only, requires `{"confirm": "rewrap"}`,
and audits `security.envelope_rewrapped` with the KEK id before and after. The
tenant slug is *not* required, deliberately: nothing is destroyed, so the
destructive-action ceremony would be theatre.

---

## 8. What this does **not** do

Written down so nobody reads the previous sections as a stronger claim than they
are:

- **No managed KMS is integrated.** `managed_kms_integrated: false`, on the panel
  and in the API. The `http` provider is a protocol for *plugging in* a KMS; it is
  not a KMS.
- **No HSM.** The root is in this process' memory after the unwrap, and readable by
  the app user. `docs/11 §7` step 3.
- **One root for all tenants.** A per-tenant DEK (step 2) is not part of this
  change; the envelope wraps the single root that the product already had. The
  improvement is at-rest exposure, not blast radius.
- **No `v2:` ciphertext prefix.** Key versions were considered and rejected here:
  the envelope wraps the root, not the individual field blobs, so a prefix would
  have been a marker with nothing behind it. Rotation without downtime — the
  reason `docs/11 §7` step 4 asks for it — is served by the KEK rewrap instead.
- **The development keyring is unchanged.** A build with no envelope and no
  `RE_MASTER_KEY` still generates `data/keys.json`, still refused in production.
- **`.env` in this workspace still contains `RE_MASTER_KEY`.** The envelope is a
  capability, not a migration: an operator runs `init`, restarts, confirms a read,
  then deletes the line. Removing it before the restart would take the product
  down.

---

## 9. How it is proved

| check | where |
|---|---|
| primitive: tamper, wrong key, wrong context, wrong length all raise `INTEGRITY_FAILURE` | `tests/test_kms.py` §A |
| `init` adopts the current root and re-encrypts nothing | `test_init_adopts_the_current_root_and_reencrypts_nothing` |
| envelope is 0600 and contains no key, raw or base64 | `test_the_envelope_file_is_0600_and_holds_no_key` |
| a wrong KEK is `KEK_MISMATCH` and says it cannot boot | `test_a_wrong_kek_is_a_mismatch_not_a_fallback` |
| rewrap under a new KEK with the old one as `previous`, then the old one deleted | `test_rewrap_under_a_new_kek_changes_the_kek_id` |
| `verify` reports `derives_live_keys: false` when the envelope is not the running root | `test_verify_catches_an_envelope_that_does_not_match_the_running_keys` |
| a real server booted with an envelope decrypts a row sealed without one | `tests/test_kms.py` §D |
| an envelope written after boot reads as `pending_restart` and `degraded`, not `enforced` | `test_a_server_that_booted_without_an_envelope_says_pending_restart` |
| rewrap is owner-only, needs a confirmation, and is audited | §D |
| the whole story end to end, against real servers, including two boots that must die | `python3 scripts/proof_kms.py` (52 checks) |

The proof script boots five separate servers: one without an envelope, one with it,
two that must fail to boot (wrong KEK, tampered blob — it checks that nothing is
listening afterwards, so a fallback path would be caught), and one with no
`RE_MASTER_KEY` anywhere in its environment or env file, which can only boot from
the envelope.

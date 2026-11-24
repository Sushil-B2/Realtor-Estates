# 17 — Handling a credential that arrives by hand

A deploy key is the single most dangerous string in this project: it can create
branches, change `neon.ts` policy, deploy code, and read backups. This document
records how one is received, where it lives, what actually protects it, and —
honestly — what does not.

Written after a real attempt: the credential supplied for the Neon Functions work
was attached as `uploads/neon_API_Key.txt` and turned out to be the project's
**endpoint URL**, not an API key (see §6). The file was shredded on sight; the
checks below are what stopped it from mattering.

---

## 1. Where the credential lives

```
.secrets/neon-api-key        0600, directory git-ignored, NOT loaded by the app
```

Not `.env.local`. That file is the **runtime** environment — it is read into the
app process at boot, surfaces in `/api/v1/security/posture` style reports, and is
one careless `print(config)` away from a traceback. A credential the *CLI* needs
has no reason to be inside a process that serves HTTP, so it gets its own file and
its own blast radius.

*(`.neon` could not be used for this: in this repo `.neon` is a regular file — the
CLI's branch-link context — not a directory.)*

## 2. How it gets there — three routes, all one-way

| Route | When to use it | Notes |
|---|---|---|
| `/keysetup.html` (owner form) | the file viewer cannot edit dotfiles | owner-only, CSRF-checked, rate-limited, value never echoed |
| `scripts/neon-cli.sh` | each CLI call | loads the key into the process env from the file; never argv, never history |
| `export NEON_API_KEY=…` | CI | a real secret manager's job; the wrapper honours an existing value |

The form writes **one named key**, validates the value against
`[A-Za-z0-9_.\-+/=]{8,512}`, and does an atomic `write → fsync → os.replace →
chmod 600`. A `\n` in the value is a 400 — otherwise a caller could inject a
second variable (`DATABASE_URL=postgres://attacker/…`) into the file.

## 3. What protects it

- **File mode 0600** — no other user on the box can read it.
- **Git-ignored, twice**: `.secrets/` and `uploads/` are both in `.gitignore`, so
  neither a credential file nor the paste-zone it arrived in can be committed.
- **A gate that looks**: `npm run secrets:check` scans tracked files, git history,
  **and the exposure zones** (`uploads/`, `Downloads/`, `Desktop/`) — the places a
  pasted key actually lands. It now recognises `nak_…` project keys (the previous
  pattern only matched the older `nak_live_…` form, which is exactly how an
  attachment could sit unnoticed), GitHub PATs, Slack tokens, and Postgres URLs
  with embedded passwords.
- **No echo, no log, no audit**: the intake route answers with a SHA-256
  fingerprint and a masked form (`nak_••••4555`); the audit row records
  `setup.neon_api_key_set` plus that fingerprint. Access logs record paths, with
  query tokens redacted by key and by shape.
- **Never in argv**: `ps` and `/proc/<pid>/cmdline` are world-readable, so a key
  passed as a CLI argument is published to every local user. The wrapper exports
  it into the environment of the one process instead.
- **Rate-limited**: the write path has its own limiter bucket, so a stolen owner
  session cannot slot in a key and drive deploys at machine speed.

## 4. What does *not* protect it — say this out loud

- **Encryption at rest here would be theatre.** The app has a master key
  (`RE_MASTER_KEY`) and could AES-GCM the credential file — but that master key
  sits in `.env` beside it, in the same snapshot, readable by the same user. Any
  attacker who can read one can read the other. Real encryption-at-rest means a
  KMS/HSM with a key the box cannot hold; that is `docs/11 §7`, not something a
  Python function can fake.
- **The workspace snapshot includes this directory.** Files under `/home/user`
  persist between turns, and snapshots exclude a short list of credential paths
  that does not include `.env`, `.env.local` or `.secrets/`. Mode 0600 stops other
  *users*; it does not stop a snapshot. The mitigation is not a permission bit —
  it is **not keeping the key around**.
- **A project-scoped key is still powerful.** It can deploy. Scope it as tightly
  as the console allows and give it an expiry.

## 5. The posture for a key you hand over

1. **Use it, then shred it.** `NEON_SHRED=1 scripts/neon-cli.sh deploy --env .env.local`
   overwrites the file with zeros and unlinks it after a successful command. The
   fingerprint is what stays.
2. **Rotate after the work lands** (console → Account settings → API keys →
   Revoke), so the value's useful life is the length of the task, not the length
   of the project.
3. **Re-mint rather than re-use** if a key ever sat somewhere its mode was not
   0600 — including this sandbox, including a chat message.

Exact commands, and what each one does, are in `docs/15 §7`.

## 6. The incident this document came from

The attachment was 95 bytes on a single line, starting `https`, with a host of
the form `ep-….aws.neon.tech`, three path segments, **no userinfo, no query
string, no `=` at all**. That is the endpoint URL from the console's *Connect*
pane — useful for a `DATABASE_URL` by hand, useless as a CLI credential, and not
a secret in the way an API key is (it identifies an endpoint; it does not
authorise anything).

It was handled the same way a real key would be: inspected **structurally**
(scheme, host shape, presence of credentials, character classes) without ever
printing the value, then overwritten with zeros and unlinked.

The lesson is in the gate, not the incident: a file named `…API_Key.txt` can
contain something else entirely, so *name-based* trust is worthless in both
directions. Verify by shape, and shred either way.

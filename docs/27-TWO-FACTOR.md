# 27 — Two-factor authentication: a factor, not a flag

Every workspace built on this platform has shown `mfa_enabled: true` on its owner
account since the first release. No code was ever asked for at sign-in. The flag
was set by a seed script because an owner *should* have two factors, and then
displayed on the user directory as though it were a fact.

That is worse than not having MFA, because it is a compliance claim with nothing
behind it: an auditor reads the column, an insurer prices the risk on it, and a
password — typed once into a shared site-office laptop — opens the workspace.

This document is about replacing a boolean with a protocol, and about the two
places such work usually goes wrong: the factor that can be removed by whoever
stole the cookie, and the half-session that is quietly accepted as a session.

---

## 1. Three facts, kept apart

The bug began by conflating three different questions into one field. They are
now three fields with three different answers, and only one of them is a claim
about security:

| field | question | who sets it |
|---|---|---|
| `user.mfa_enrolled` | *has an authenticator been verified for this account?* | only `POST /auth/mfa/verify` |
| `user.mfa_enabled` | *is this account asked to enrol?* | seeded for owners; not writable by anyone |
| `tenant.settings.require_mfa` | *does this workspace enforce enrolment?* | owner, via `PATCH /tenant` (off by default) |

`mfa_enrolled` is the only field that changes what a login does. `GET /tenant/users`
returns `mfa_enabled` as an alias of `mfa_enrolled` — the column now means what its
header says — and adds `mfa_asked` for the old meaning, so nothing has to be
misread.

**The self-service flag is gone.** `PATCH /tenant/users/{uid}` used to accept
`{"mfa_enabled": true}`. An admin — or a user patching their own row — could
assert a factor that did not exist. The key is no longer read: the only
administrative way to touch a factor is `{"mfa_reset": true}`, which *removes* one,
requires the `owner` role, and is audited (see §7).

---

## 2. The arithmetic

`app/totp.py` — dependency-free RFC 4226 (HOTP) and RFC 6238 (TOTP), because the
platform ships with zero mandatory third-party runtime dependencies and a TOTP
implementation is 60 lines of HMAC, not a reason to add one.

- 6 digits, 30-second step, SHA-1 (what every authenticator app assumes).
- Secrets are 20 random bytes, base32, unpadded — `SECRET_BYTES = 20`.
- `verify()` accepts **±1 step** (`WINDOW = 1`): three chances per 30 seconds,
  which absorbs clock drift and still leaves an online guess at 1-in-333,333 per
  window.
- Comparison is `hmac.compare_digest`. A wrong code returns
  `{ok: False, reason: …}` and never raises; only a *structural* problem (no
  secret, malformed secret) raises `TotpError`, because a verifier that throws on
  a wrong password is a verifier whose callers forget to catch.

It is pinned to the reference vectors, not to itself: RFC 4226's ten HOTP values
and RFC 6238's six SHA-1 timestamps (including 20000000000, the one that catches a
signed-integer overflow on 32-bit time) are asserted in `tests/test_mfa.py`.

### A code is single-use

The window is ±1 step, so for up to 90 seconds an attacker who observes a code —
shoulder-surfing, a screenshot, a phishing page that relays it — could replay it.
`user.mfa_last_step` records the last step accepted; a code at or below it is
refused (`reason: code_replayed`). This is RFC 6238 §5.2's recommendation and the
difference between "a code works" and "a code works once".

The visible cost is real and is stated in the API answer: after enrolling, the
code you have just used is spent, so if the next sign-in happens inside the same
30-second window it is refused until the next code arrives. `verify` says so in
`code_spent` and in the note it returns.

---

## 3. Enrolment is two steps, and turns nothing on in the first

The classic way to lock a user out of their own account is to enable 2FA at the
moment the secret is generated: the phone was never scanned, the tab was closed
before the recovery codes were saved, and the next sign-in demands a code that
does not exist anywhere.

```
POST /auth/mfa/enrol    → { secret, otpauth_uri, recovery_codes[10] }   enrolled = false
POST /auth/mfa/verify   → { code }                                      enrolled = true
```

`otpauth://totp/Sell.Do%3Aowner%40example.in?secret=…&issuer=Sell.Do&algorithm=SHA1&digits=6&period=30`

The label is percent-encoded and the issuer appears twice — as the label prefix
and as a parameter — because Google Authenticator reads the parameter and others
read the label, and a mismatch shows the user two differently-named entries for
one account.

Re-opening the dialog before scanning replaces the *pending* secret rather than
refusing; the answer says `replaced_pending: true` and warns that the previous QR
is void, because a silently invalidated QR is a support call.

### The seed is encrypted at rest

`mfa_secret` is stored as `sec.seal(secret, aad=f"mfa:{tenant_id}:{user_id}")` —
ChaCha20 + HMAC-SHA256 under the field key, bound to the account it belongs to. A
blob copied onto another user's row does not unseal, so a database dump cannot be
turned into a whole-workspace bypass with one UPDATE.

### Recovery codes are hashed, and they are not phone numbers

Ten codes, 10 characters each from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` — no
`0/O/1/I`, because these get read down a telephone line — displayed as
`XXXXX-XXXXX`, stored as blind indices with a `used_at` stamp.

**This is where the suite caught a real bug.** The codes were first indexed with
`sec.blind_index`, which is correct for a phone number and catastrophic for
anything else: with no `@` in the value it keeps only the digits
(`re.sub(r"\D", "", v)`). A recovery code is mostly letters, so two codes with the
same digit string — including the very common case of no digits at all — hashed
**identically**, and one code opened the account a second time. It surfaced as a
test that failed one run in six; the fix is `sec.blind_token`, which upper-cases
and strips and does nothing else, and the flake is now a test that asserts two
digit-free codes hash differently (§ `test_two_recovery_codes_are_never_the_same_secret`).

A normaliser sits in front of the comparison so that a code typed in lower case,
without the dash, or with `0` where `O` belongs, still opens the account. A
recovery code that fails because someone typed `O` for `0` is a recovery code that
does not recover.

---

## 4. The login is now two calls, and the first one issues nothing

```
POST /auth/login          { email, password }
    ├── no authenticator  → 200 { token, csrf, user, mfa }        (as before)
    └── enrolled          → 200 { mfa_required: true, challenge, methods,
                                  expires_in: 300, user_hint }

POST /auth/login/mfa      { challenge, code | recovery_code }
    └── 200 { token, csrf, user, mfa: {…}, tenant: {…} }
```

The challenge is a signed token with a **five-minute** TTL carrying
`{"mfa": "pending"}`, the user id, the tenant id and the role. It is deliberately
*not* a session, and it is issued with no `Set-Cookie`. The mailbox is hinted
(`ow•••@realtorestates.app`) so a user recognises the account without the response
confirming an address to whoever typed the password.

`sessions.issue_token()` grew a `ttl` argument for this, because an MFA challenge
that lives as long as a session is a session.

---

## 5. The half-session must not be a session

This is the part that is easy to get wrong, and the first version of this code did.

The challenge is signed by the **same key** as a session token and names the
**same user**, because that is the cheapest way to carry "this person proved a
password, not a factor". Any door that accepts a token by verifying the signature
and nothing else therefore accepts the challenge. The live proof caught it: with
`Authorization: Bearer <challenge>` the desk answered `GET /contacts` with `200`.

The fix is one function and one habit:

```python
def verify_session_token(token: str) -> dict | None:
    claims = verify_token(token)
    if not claims or claims.get("mfa") == "pending":
        return None
    return claims
```

Every door that accepts a *session* now calls `verify_session_token`:
`api.handle` (which returns a specific `401 MFA_CHALLENGE_NOT_A_SESSION` rather
than a generic refusal), the health payload that reveals the database version for
privileged roles, and the SSE stream. `verify_token` remains the low-level
primitive for the one route that *must* accept a challenge —
`POST /auth/login/mfa` itself.

The rule stated plainly, because it generalises to every token the platform will
ever mint: **a token is only as powerful as the door that accepts it, so the door
has to know which kind of token it is looking at.**

---

## 6. Limits that make guessing hopeless

| limit | value | why |
|---|---|---|
| accepted window | ±1 step | clock drift, not entropy |
| code length | 6 digits | 10⁶ space, 3 accepted per 30 s |
| `mfa_account` bucket | 6 / 900 s per account | 18 chances per 15 min < 0.002 % of the codes in play |
| account lock | after 5 wrong codes, 900 s | independent of the limiter, so it holds when the limiter is off |
| challenge TTL | 300 s | six digits take seconds; five minutes is already generous |
| replay guard | `mfa_last_step` | a spent code is spent |

The per-account bucket is separate from the login throttle on purpose: a password
spray and a code spray are different attacks with different budgets, and the code
budget has to be far tighter than the code space or the second factor is a
six-digit lottery with unlimited tickets.

---

## 7. Removing a factor needs the factor

`POST /auth/mfa/disable` requires `password` **and** (`code` or `recovery_code`).
A stolen session cannot remove the thing that protects the account next time —
which is the entire point of the second factor, and the reason "turn off 2FA" is
the first button a session-thief looks for.

`POST /auth/mfa/recovery-codes` (ten fresh codes, previous ten void) requires the
password plus a factor too, and accepts an *unused recovery code* as that factor:
losing the phone is precisely when new codes are wanted.

`PATCH /tenant/users/{uid}` `{"mfa_reset": true}` is the break-glass key — an
employee left, a phone was destroyed, the recovery printout is in a drawer in a
different city. It is **owner-only**, audited as `user.updated` with the reset in
the patch, and it clears the enrolment, the codes, the lock and the failure count
in one write.

---

## 8. A workspace may demand a factor — but never trap itself

`tenant.settings.require_mfa` (default **off**, so the demo still signs in with a
password) is enforced in `Ctx.require`, which is the same choke point as role
checks:

```python
MFA_SENSITIVE = ("manage_users", "kill_switch", "pay_partner", "approve_cp")
```

A half-protected session — a workspace that demands a factor, and a session that
has not enrolled one — keeps working for the day's work (reading the pipeline,
writing a note, uploading a photo) and is refused (`403 MFA_REQUIRED`) on the
actions that change *who exists*, *where money goes*, or *whether the system runs
at all*.

`manage_tenant` is deliberately **not** on that list. A first draft included it,
and the test suite found the trap immediately: with `require_mfa` on, the session
could no longer reach the route that turns `require_mfa` off, so switching the
policy on locked the owner out of the switch. A gate that can be closed on the
person holding the key is not a gate, it is a wall with the door painted on it.
Quiet hours and a data region are not worth that risk; identity and money are.

---

## 9. What the operator sees

- **Login** — the second step is a screen of its own (`web/js/core.js`
  `renderAuthMfa`): the code field, a link to a recovery code, and "start over".
  The challenge lives in memory only; a refresh returns to the password.
- **Settings → Account** — `Two-factor: authenticator enrolled` with *New recovery
  codes*, or *password only* with *Enrol an authenticator*. Enrolment shows the
  secret, the `otpauth://` URI and the ten codes **once**, with the code field to
  finish — the two-step conversation, in the UI.
- **User directory** — the `MFA` column reads `mfa_enrolled`, so it no longer
  claims a factor that was never enrolled.

---

## 10. Testing it

`tests/test_mfa.py` — 41 tests in three groups, because three different things can
break:

- **the arithmetic** — RFC 4226/6238 vectors, the ±1 window, the replay rule, the
  secret's shape, the URI's parameters, the recovery alphabet and its
  normaliser, drift reporting.
- **enrolment and the two-step login** — nothing enabled by a secret alone; a wrong
  code refused; a challenge instead of a session; the cookie set only on the
  second step; the code spent; a recovery code accepted once and tolerant of
  shouting; regeneration voiding the old set.
- **the ways a factor gets defeated** — a challenge used as a session, a code
  replayed, a recovery code reused, a stolen cookie turning the factor off, an
  admin asserting a factor through PATCH, a manager reaching the reset, five
  wrong codes and no lockout.

`scripts/proof_mfa.py` — 45 checks against a throwaway server, 42 against the live
desk (the at-rest section reads the store file, which the Postgres-backed demo
does not keep). It ends by removing the authenticator again: **a proof does not
change the demo**, the same rule v1.15.0 established for rows.

The suite's own flakiness paid for itself here. A test that fails one run in six
is not a bad test; it is a test that found a collision in a primitive everybody
trusted.

---

## 11. Known limits — stated, not hidden

- **No WebAuthn / passkeys.** TOTP is phishable: a relay page can ask for the code
  and use it inside the 30-second window. The replay guard does not help against a
  real-time relay. WebAuthn (platform authenticator, origin-bound) is the correct
  answer and is not in this build.
- **No device trust / "remember this browser".** Every login asks for a code. That
  is a deliberate default (the alternative is a long-lived cookie that quietly
  becomes a second factor), but it is friction a real desk will ask about.
- **Recovery codes are hashed with the field keyring, not a KMS envelope.** Rotating
  `RE_MASTER_KEY` therefore invalidates every unused recovery code — which is
  survivable (the codes are reissued from a session) but must be in the rotation
  runbook, and the KMS envelope in G8's remaining work should carry them.
- **No admin visibility of who is *not* enrolled** beyond the directory column, and
  no "enforce from a date" transition for a team that needs a grace period.
- **Email/SMS fallback is absent on purpose.** An SMS code is a second factor that
  a SIM swap defeats, and the platform has no BSP contract (`docs/23`).

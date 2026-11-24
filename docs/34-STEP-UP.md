# 34 — Step-up: a session says who you are, a ticket says you meant it

> **Follow-up #2 from `docs/13`.** *"Step-up re-authentication before kill-switch,
> key rotation and user deletion."*
>
> The gap is the one every product that says "we have two-factor" quietly has. The
> factor happens at **sign-in**. After that the session is trusted for everything,
> for as long as it lasts. A desk machine left signed in on Friday is a kill-switch
> press on Monday, and the second factor was satisfied last week.

This document is in plain language on purpose. It is about one prompt, and the
prompt has to be explainable to the person who will be pressing it.

---

## 1. The problem, for a non-technical reader

**The analogy.** Your office has a door with a keycard. You tap in at 9 a.m. and the
door stays unlocked for you all day — that is a session. Now imagine the fire-alarm
test button, the safe combination, and the "delete an employee's account" screen are
also *inside* that door, sitting on the wall, with no extra check.

Anyone who walked in behind you at 9:05 — or found your desk at lunch — can press
all three. The keycard proved you were you *this morning*. It did not prove that the
person pressing the button right now is you, or that they meant to press it.

**What step-up adds.** For four specific things, the system asks again — right then,
at the moment of pressing — for something only you have. It then gives the browser a
very small, very short-lived permission slip, good for **that one press** and nothing
else.

| | A session | A step-up ticket |
|---|---|---|
| answers | *who* you are | *you are here, now, and you mean this* |
| lasts | 8 hours | 2–5 minutes |
| reusable | yes, for everything | **no — one action, once** |
| if stolen | dangerous | close to useless (wrong action, wrong record, wrong browser) |

The permission slip is called a **ticket**. It never replaces your session — it sits
beside it for a few minutes, for one action, and is thrown away when used.

---

## 2. What it actually is, technically

```
POST /api/v1/auth/step-up      {action, target, password, code | recovery_code}
   → 200 {ticket, action, target, method, expires_in, single_use: true}
   → 400 FACTOR_REQUIRED / VALIDATION_FAILED
   → 401 STEP_UP_INVALID
   → 429 RATE_LIMITED            (five wrong tries, per session, five-minute cool-off)

POST /api/v1/tenant/kill-switch  {engage: true}   with header:  X-Step-Up: <ticket>
   → 200                                   first use
   → 423 STEP_UP_REQUIRED                  no ticket
   → 423 STEP_UP_WRONG_SESSION             a ticket minted in another session
   → 423 STEP_UP_WRONG_ACTION              a ticket for a different action
   → 423 STEP_UP_WRONG_TARGET              a ticket for a different record
   → 423 STEP_UP_REPLAYED                  a ticket already used
```

A ticket is a signed payload — `app/stepup.py` — carrying five things that are the
whole design:

| claim | meaning | what it stops |
|---|---|---|
| `typ: "stepup"` | this is a confirmation, not a session | presenting your own session token as the answer |
| `sid` | the session it was minted in | copying it into another browser |
| `act` | the one action it authorises | "delete this user" authorising "engage the kill switch" |
| `tgt` | the one record it covers (a user id, `engage`/`release`, a slug) | deactivating a *different* colleague |
| `jti` + spent set | one use | a confirmation that can be replayed |

`exp` is the sixth: **300 seconds** when the account has a second factor, **120
seconds** when it does not. That difference is deliberate and it is stated out loud
in the console rather than hidden — see §4.

**A ticket is spent in the same lock that checks it.** Two requests arriving in the
same millisecond with the same ticket cannot both succeed; one wins and one gets
`STEP_UP_REPLAYED`.

---

## 3. Which actions are gated, and which are deliberately not

Eight actions, and the target each one is bound to:

| action | route | target |
|---|---|---|
| `kill_switch` | `POST /tenant/kill-switch` | `engage` **or** `release` |
| `key_rewrap` | `POST /security/envelope/rewrap` | `root` |
| `user_delete` | `PATCH /tenant/users/{id}` with `active: false` | the user id |
| `role_change` | `PATCH /tenant/users/{id}` with `role` | the user id |
| `mfa_reset` | `PATCH /tenant/users/{id}` with `mfa_reset: true` | the user id |
| `secret_write` | `POST /setup/neon-key` | `neon_api_key` |
| `demo_purge` | `POST /demo/proof-rows/purge` | the workspace slug |
| `payment_adjust` | usage-ledger adjustment | the tenant id |

Note `engage` and `release` are *different targets*. A confirmation to stop the
agents is not a confirmation to start them again — the person who is allowed to
press the panic button is not automatically allowed to un-press it.

**Not gated, on purpose:**

- **reading data.** A confirmation on a read is a prompt nobody reads, and a prompt
  nobody reads trains people to click through prompts.
- **ordinary writes** — a note, a stage change, a phone number, a rename. That is
  the day's work; the session is for it.
- **anything a token does** without a person present (background workers).

The security view lists these out loud, under *"Deliberately not gated"*, so that
"step-up is on" is never read as "every request is confirmed".

---

## 4. The honest weakness: an account with no second factor

Most workspaces have not enrolled anybody yet. The options were: (a) refuse the
action entirely, (b) pretend a password is two factors, or (c) let it through, say
so, and charge less for it.

This is (c), and it is visible in three places — the ticket response, the console
panel, and the audit trail:

```
"claims": {
  "password_only": true,
  "note": "this account has no second factor: a step-up ticket is proven by the
           password alone and lasts 120s — enrol an authenticator (docs/27) to make
           it a factor",
  "replayable": false
}
```

What a password-only ticket still buys you is real, and worth saying plainly: a
stolen session cookie cannot produce one, because it does not know the password. It
*is* weaker than a ticket that needed a code, and the panel says "password only"
rather than "protected".

---

## 5. The attempt budget — and the lockout that would have been a DoS

`POST /auth/step-up` accepts a password, so it is a place to guess one. Five wrong
tries per **session** earns a **300-second** cool-off. Per session, not per account:
one person fat-fingering five times must not lock the whole workspace out of the kill
switch.

Writing the tests found two defects in this, both worth recording because both were
silent:

**Bug 1 — the counter never counted.** `fail()` reset the failure count whenever
`time.time() >= locked_until`, and `locked_until` was `0.0` for "not locked yet", so
every failure reset the count to zero before incrementing it. `MAX_ATTEMPTS` was
unreachable and the budget was decoration. The state is now
`[failures, locked_until]` and "no lock" is *not* the same as "expired lock".

**Bug 2 — a lockout that locked out the owner.** The account-level lockout (five
wrong codes at sign-in) refused **every** code, including **recovery codes** — so
anyone who could reach the sign-in form could lock the only person who may reset the
factor out of their own factor. The break-glass key broke exactly when it was needed.
A recovery code is 52 bits of randomness: guessing is not the attack, the lock does
not apply to it. `_mfa_check_user(ctx, u, recovery=...)` exempts it, and the lockout
message now says so.

**Bug 3 — the teardown that poisoned forty tests.** A test fixture that enrolled an
authenticator and then failed to remove it turned every later login in the run into
a two-step login; forty unrelated tests failed on a missing `csrf`. The fixtures now
take the factor off through three doors (current window, recovery codes, and the
step-up route's own consumption) and **assert** that it worked, rather than hoping.

**Bug 4 — a 423 returned *after* the side effect.** `setup_neon_key_write` initially
wrote `.secrets/neon-api-key` and *then* called `ctx.require_step_up(...)`, so an
unconfirmed request received `423 STEP_UP_REQUIRED` after its key was already on
disk. The gate now runs before any write, `tests/conftest.py` isolates
`RE_SECRETS_FILE` to the temp directory, and `tests/test_stepup.py` asserts that
`configured` remains `False` after the `423`.

**Bug 5 — password-only tickets carried the full 300 s signature.** `auth_step_up`
computed `NO_FACTOR_TTL` (120 s) and reported it in `expires_in`, while `ticket_for`
signed `exp` with `TICKET_TTL` (300 s). `ticket_for(..., ttl=ttl)` now signs the
actual factor-aware lifetime (`tests/test_stepup_shared.py`).

---

## 6. Why the confirmation route stamps its own rows

The proof-row machinery (`docs/26`) marks what a request created so a purge can
delete it later. Two rows this route writes are marks on the caller's user row — a
consumed recovery code, and a spent TOTP step. Left unstamped, a proof run leaves
permanent "used" marks on a demo account. Stamped *automatically*, a confirmation
would become a deletion primitive: mint it with `X-Proof-Run` set, and a purge could
then be aimed at a credential row in someone's account.

So it is stamped **explicitly, in the handler, and only when the caller declared a
proof run** — and the route is excluded from the automatic stamping in
`api.handle()`. There is a test that a proof run's step-up leaves no litter and a
test that nothing in a purge can reach a login.

---

## 7. What the person sees

The browser has one prompt, in `web/js/core.js`, and every gated view works without
knowing it exists:

```
api('/api/v1/tenant/kill-switch', …)
  → 423 STEP_UP_REQUIRED
  → promptStepUp()    password, and a code if the account has one
  → POST /api/v1/auth/step-up → ticket
  → the same call retried once, with X-Step-Up
```

The prompt names the action in words ("Stop or restart the agents for this
workspace"), explains that nothing changes until it succeeds, warns that five wrong
tries costs five minutes, and offers *"Lost the phone? Use a recovery code"*. On an
account with no authenticator it says so, says the ticket lasts two minutes, and
links to enrolment.

The **security** view carries a *Step-up confirmation* panel: which eight actions
are armed, what the ticket is worth here, whether this account has a real factor, a
**drill button** per action that mints a ticket and throws it away (proving the
whole path without changing anything), the last ten confirmations, and the
"deliberately not gated" list.

---

## 8. Proving it

```
python3 scripts/proof_stepup.py             # throwaway instance, 44 checks
python3 scripts/proof_stepup_shared.py      # two real server nodes + Redis/file, 15 checks
python3 scripts/proof_stepup.py --live      # against a running desk
python3 -m pytest tests/test_stepup.py tests/test_stepup_shared.py
```

The proof is written as a list of attempted cheats, because "there is a dialog" is
not a claim worth making:

1. no ticket → 423, and the refusal says which action and which route mints one
2. a ticket performs the action — and is refused on the second use
3. a ticket for `key_rewrap` is refused at the kill switch
4. a ticket for one colleague cannot deactivate another
5. a ticket minted in one session is refused in another
6. the session token itself is not a ticket, and neither is junk
7. five wrong passwords → 429, even for the right password afterwards, per session only
8. an enrolled account needs the code, not just the password
9. a recovery code works once here too, and the consumption is audited
10. a code lockout does **not** refuse a recovery code
11. the posture route is owner/admin only, and states the binding properties
12. the desk is left password-only, with no switch engaged

`RESULT` is 44 of 44 with nothing skipped. `tests/test_stepup.py` adds the unit-level
cases the proof cannot reach (tampered signature, expired ticket, two tickets in the
same second, the attempt-budget state machine, the wrong-AAD unseal).

---

## 9. What this does not do

- **It is not a factor for accounts without one.** §4. The fix is enrolment
  (`docs/27`), not another prompt.
- **Multi-instance replay & attempt budgets (`v1.23.0`).** By default (`memory`),
  spent ticket `jti`s and the 5-try session cool-off live in process memory. When
  `RE_REDIS_URL` (multi-host) or `RE_SHARED_DIR` (single-host workers) is configured
  (`docs/31`), `Spent.claim()` uses an atomic single-winner claim (`SET … PX … NX` on
  Redis, `flock` on `SharedFiles`) and fails **closed** (`503 STEP_UP_STATE_UNAVAILABLE`)
  if the shared store cannot be reached, while `Attempts` shares the 5-try budget
  across workers and falls back to per-process counting on outage. Proved across two
  real `app.server` instances in `scripts/proof_stepup_shared.py` (15/15).
- **Nothing is gated by *time since login*.** A session that has been open for seven
  hours gets the same prompt as one opened seven seconds ago. That is intentional —
  the prompt is about intent, not fatigue — but a "re-confirm every N hours" policy
  is a different control and is not implemented.

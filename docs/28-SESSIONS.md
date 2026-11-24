# 28 — Sessions: a credential you can see, rotate and end

Until v1.17 a session was one eight-hour bearer token. It was signed, it was
HttpOnly, and it was, in every practical sense, a password with a deadline:

- **A stolen token lived for eight hours.** Revoking meant adding a `jti` to a
  `set()` in one process's memory. A restart forgot it; a second instance never
  knew it; a stolen cookie and the victim's browser were indistinguishable.
- **Nobody could answer "where am I signed in?"** — the question every user asks
  the afternoon a laptop is left in a cab. The only record was the access log.
- **Theft was invisible.** With no refresh token there is no rotation, and with no
  rotation there is no way to notice that one credential is being used by two
  parties.

`docs/27` closed the front door with a second factor. This document is about the
door itself: what a session *is*, how long each half lives, and how it ends.

---

## 1. Two halves and a row

```
login ──▶ sessions row (sid) ──▶ access token    HMAC, 30 min, carries sid
                 │
                 └───────────▶ refresh token     opaque, 30 days, hashed at rest
                                          │
                                 POST /auth/refresh
                                          │
                              rotate: new token, old one remembered
                                          │
                       old one used again ──▶ reuse ⇒ the session ends
```

| | access token | refresh token |
|---|---|---|
| shape | signed, base64 JSON + HMAC | 32 random bytes, base64url |
| lifetime | **30 minutes** (`RE_ACCESS_TTL`) | **30 days** (`RE_REFRESH_TTL`) |
| where | `re_session` cookie, `Path=/`, HttpOnly, SameSite=Strict | `re_refresh` cookie, **`Path=/api/v1/auth`**, HttpOnly, SameSite=Strict |
| in the body | yes (`token`), because the SPA needs the CSRF that derives from it | **no** — see §2 |
| checked by | signature + *the session row* | lookup of its hash |

**The access token looks stateless and is not.** It carries `sid`, and
`api.handle` looks that id up in the `sessions` collection on every request. That
lookup is the entire reason revocation is immediate: `DELETE /auth/sessions/{sid}`
takes effect on the next request rather than at the next login. The cost is a dict
lookup against an in-memory store; the alternative was an eight-hour window in
which a stolen cookie works and the owner cannot stop it.

**The refresh token is opaque** — it is looked up, not parsed. A signed refresh
token would still have to be revoked by id, so a signature would buy length and
nothing else. It is stored as `sec.blind_token(...)`: a hash, not the token.

---

## 2. The refresh token never appears in a response body

A long-lived credential in JSON is a credential one XSS away from exfiltration —
and an XSS that can read `localStorage` can read a fetch response too. So the
refresh token is delivered **as an httpOnly cookie and nowhere else**, and a
browser never sees its value.

That is a problem for a script, which has no cookie jar. The answer is explicit,
not implicit:

```bash
POST /api/v1/auth/login?refresh=body
→ { …, "refresh_token": "…",
     "refresh_warning": "Returned in the body because you asked. Browsers must not:
                         keep it in the platform keystore, never in local storage." }
```

The warning is in the payload rather than only in this document because the person
who will misuse it is the person reading the response.

**The cookie is path-scoped to `/api/v1/auth`.** A refresh token that travels with
every request is an access token with a longer life: it would be sent to the
contact list, the funnel pages, the public webhook and any third-party script that
manages to make a same-site request. Scoped this way it is sent to exactly one
route.

---

## 3. Rotation, and why reuse ends everything

Every successful refresh returns a new refresh token and retires the old one.
The retired hash moves into `refresh_history` (bounded at four rotations).

If a token arrives that matches a **historical** hash, two parties hold the same
credential — the legitimate holder already rotated past it. The session is revoked
on the spot, audited as `auth.refresh_reuse`, and the response is:

```
401 REFRESH_REUSED
"This session was ended: a refresh token was used twice. Sign in again."
```

**Both holders are signed out, and that is the honest outcome.** We cannot tell
which one is the thief; the safe assumption is neither is trustworthy. A refusal
without revocation would be worse than useless — it would tell an attacker to come
back tomorrow, while the real user never learns their token was copied.

The accepted cost: a client that loses a response (a dropped connection right
after the rotation) has to sign in again. Rotation with reuse detection trades a
rare re-login for the ability to notice a breach at all.

---

## 4. The device list

`GET /auth/sessions`

```json
{"sessions": [{"id": "ses_…", "device": {"label": "Chrome on macOS", "browser": "Chrome",
                                          "os": "macOS", "kind": "browser"},
               "ip": "203.0.113.x", "created_at": 1795090000, "last_seen": 1795090300,
               "expires_at": 1797682000, "amr": ["pwd", "totp"], "mfa": true,
               "current": true, "revoked": false}],
 "count": 1, "access_ttl": 1800, "refresh_ttl": 2592000}
```

- **`device.label`** is coarse on purpose: a browser, an operating system, a kind
  (`browser` / `mobile` / `script`). The list exists so a person can say *that one
  is not me*, which needs recognition, not a fingerprint.
- **`ip` is masked** to a /24 (or a short IPv6 prefix). Enough to recognise the
  office; not a location trail.
- **Nothing credentialed is in it.** No refresh hash, no user agent string, no
  token — asserted by a test that greps the serialised payload.
- **`amr`** records *how* the session was authenticated (`["pwd"]`,
  `["pwd","totp"]`, `["pwd","recovery_code"]`), so "this device is signed in" can
  be checked for "…and it did the second factor".

`DELETE /auth/sessions/{sid}` ends one. `POST /auth/sessions/revoke-others` is the
button you press when the laptop is gone — it keeps the session you are holding.
Another user's session returns **404, not 403**: a 403 would confirm that the id
is real, and session ids are not something to enumerate.

Both appear in **Settings → Where you are signed in**, with a per-row *Sign out*
and one *Sign out all other devices*.

---

## 5. Signing out

`POST /auth/logout` ends the session row (every token issued from it dies with
it), revokes the presented `jti` as well, and clears **both** cookies. Before this
change it wrote an audit line and trusted the client to throw its token away.

---

## 6. The clock

| event | what happens |
|---|---|
| access token expires (30 min) | the API answers 401; the SPA calls `/auth/refresh` once, retries the request, and the user never sees it |
| refresh token expires (30 days) | 401 `SESSION_EXPIRED`; a full sign-in is required |
| session revoked / signed out | 401 `SESSION_ENDED` on the very next request |
| token from before sessions existed | 401 `SESSION_REQUIRED` — there is no row, so there is nothing to revoke, and "which session is this?" has no answer |

The SPA side is one wrapper in `web/js/core.js`: `api()` retries once through
`refreshSession()` and only falls back to the sign-in screen when the refresh
itself fails. Concurrent 401s share a single in-flight refresh (`refreshing`), so
a dashboard that fires twelve requests at once does not rotate twelve times — which
would trip the reuse detector against itself.

---

## 7. Sessions are rows, so a demo accumulates them

A login is now a row, and this project runs a lot of logins: 41 MFA tests, 20
session tests, six proof scripts, `reset_demo.py`, the sentinel agent. The first
live proof left fifteen session rows in the demo tenant — the same drift `docs/26`
fixed for business rows, in a new collection.

Three answers, all small:

1. **A proof stamps its sessions.** `X-Proof-Run` is honoured on
   `POST /auth/login` too, because a login is public only in the sense that the
   caller has no session yet: the single row it creates is the caller's own, so
   stamping it is not a deletion primitive anyone could aim at somebody else.
   `sessions` joined `PURGEABLE`, so `scripts/reset_demo.py --purge` takes them
   away with the rest.
2. **`POST /demo/sessions/sweep`** deletes rows that can never be used again —
   revoked, or past their refresh expiry. Live sessions are somebody's browser and
   are not a demo's to tidy. `reset_demo.py` calls it.
3. **`reset_demo.py` signs itself out.** It logged in to do its work and left its
   own session behind, which is precisely the drift it exists to clean.

---

## 8. What this is not

- **No "remember this device".** Every sign-in is a new row. Combined with `docs/27`
  that means a code on every login, which a real desk will ask about; the honest
  answer is WebAuthn with a per-device credential, not a long-lived cookie.
- **No per-device re-authentication for sensitive actions.** The factor is at
  login, not at the moment of a payout.
- **No server-side session store for the access token** beyond the in-memory
  collection. Two instances share the rows (Postgres), so revocation is shared —
  but `sec.REVOKED` (the `jti` set) is still per process, and the Redis limiter in
  G8's remaining work is where that belongs.
- **No idle timeout.** A session lives until its refresh token expires, however
  rarely it is used. A 30-day session on a shared site-office machine is a real
  risk and deserves a shorter default per role, or an idle rule.
- **`last_seen` is written at most once a minute** per session (`TOUCH_EVERY`), so
  the device list can be up to a minute stale. That is deliberate: a write per
  request would make a read-only dashboard a write workload.

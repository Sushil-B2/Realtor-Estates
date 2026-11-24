# 31 — Shared state: a rate limit that spans instances, and a logout that sticks

> **Gap G8, final item.** `docs/02 §5` has listed "in-process rate limits → Redis
> token bucket, same `RateLimiter.check()` signature" for a long time. The reason
> was never throughput. Two pieces of security state lived in each process's
> memory, and both stop being security controls the moment there is more than one
> process:
>
> * **A rate limit in one process's memory is not a rate limit.** Ten instances
>   turn `auth_account: 10/900` into a hundred password guesses per quarter hour,
>   and the load balancer hands the attacker the fan-out for free.
> * **A revoked token lives in the process that revoked it.** Log out on node A
>   and the token still works on node B for the rest of its life.

The signature `docs/02` promised is kept: `sec.LIMITER.check(bucket, identity)`
still returns `(allowed, remaining, retry_after)` and every call site is
unchanged. What changed is where the count comes from, and what the console is
allowed to say about it.

---

## 1. Three backends, each honest about itself

| `RE_SHARED_STATE` | where the state lives | survives a restart | shared by instances |
|---|---|---|---|
| `memory` (default) | this process | no | no |
| `redis` | Redis at `RE_REDIS_URL` | yes | yes, across hosts |
| `file` | `RE_SHARED_DIR` on a shared filesystem | no | **only on one host** |

Selection is automatic when unset: `RE_REDIS_URL` implies `redis`, `RE_SHARED_DIR`
implies `file`, and neither means `memory` — so an existing installation behaves
exactly as it did before, and nothing new is required to upgrade.

`file` exists because "two workers on one box" is a real deployment — gunicorn
with `--workers 4`, or the two servers this repository's proof scripts boot — and
it needs no new service. It is not a Redis replacement and the console says so:
`one_host_only: true`, because `flock` is not a network protocol and NFS locking
is emulated and unreliable. Its timestamps are wall-clock, so a wide NTP jump can
expire or extend a window.

---

## 2. The Redis client is ours, and it is small

`docs/02`'s first rule is zero third-party runtime packages in the app tier. So
the protocol is implemented in `app/shared_state.py`: RESP2 over a plain socket,
a strict parser, and the six commands the limiter and the revocation list need
(`INCR`, `PEXPIRE`, `PTTL`, `SET … PX`, `EXISTS`, `EVAL` of one script). That is a
smaller auditable surface than a client library and its dependency tree, and it is
proved against a real socket rather than a mock (`scripts/fake_redis.py`).

Two decisions worth stating:

* **`rediss://` is refused, not silently downgraded.** This client is plaintext.
  Put it on a private network or a unix socket (`unix:///run/redis.sock`), and the
  console reports `tls: false` rather than leaving the reader to assume otherwise.
* **`EVAL` may be unavailable.** A managed Redis with scripting disabled answers
  `NOSCRIPT`; the client notices once, remembers, and falls back to `INCR` +
  `PEXPIRE` + `PTTL` without losing a count. Both paths are tested.

The counter is a **fixed window** rather than the in-memory sliding one, and the
kind is reported (`limiter.window`). A fixed window is one atomic round trip; a
sliding window is a sorted set written and trimmed per request — three commands
and a race that needs Lua to close. The cost is that a caller can send up to
`2 × limit` across a window boundary, which is exactly why the buckets are sized
as they are and why the change is visible in the API rather than buried.

---

## 3. A store that dies must not fail open

If the shared store becomes unreachable mid-flight, `RateLimiter.check` falls back
to enforcing the limit **in this process** and counts the fallback
(`limiter.fallbacks`), printing one line. The alternative — allow everything
because the counter is unavailable — turns a Redis outage into an open door.

The boot is stricter than the runtime. In production, a configured backend that
does not answer is **fatal**:

```
refusing to start: REDIS_UNREACHABLE: cannot reach Redis at cache:6379: …
```

A production process with per-instance limits and a green health check is worse
than one that refuses to start, because nobody goes looking. In development the
process starts, the console shows `backend: memory` with an `error`, and the
warning says what was lost: *"falling back to per-process state; PII-adjacent
limits are now multiplied by the number of instances"*.

For revocation the rule is the opposite by design: an unreachable store cannot
prove a token was **not** revoked, so `verify_token` refuses the token
(fail-closed). A user can be asked to sign in again; a token *should* have been
dead is not recoverable.

---

## 4. Two bugs found while moving this

**`REVOKED.clear()` un-revoked everything.** The in-process set of revoked tokens
was bounded by clearing it at 5000 entries. That traded a memory leak for
something worse: every token that had been logged out became valid again, at once,
silently. Revocations now carry the token's own `exp` and are pruned only once
expired — entries `verify_token` would refuse anyway — so live revocations are
never dropped.

**Role changes revoked nothing.** The line was:

```python
if "role" in patch and "role" in (patch.get("role"),):
    sec.REVOKED.add(uid)
```

The second test asks whether the *string* `"role"` is an element of a one-item
tuple containing the new role. It is never true, so the branch never ran — a
manager demoted to viewer kept a working token for eight hours. It was also
reaching for the wrong thing: `REVOKED` holds `jti`s, and a user id would never
match one, which the dead branch conveniently hid.

The replacement is an **epoch**. Every token carries the epoch it was minted under
(`ep`), and a role change bumps the user's epoch, so every token minted before it
becomes stale — on every instance, whether or not that instance issued the token:

```
token.ep  <  current_epoch(uid)   →  refused
```

That is the only shape that works for "invalidate tokens issued by *other*
processes", and it is stored in the same shared backend as the counter.

---

## 5. What this does not fix

* **The JSON store is still single-writer.** A session row created by node A is
  invisible to node B, because the JSON backend loads at boot and does not reload.
  Multi-instance deployments run Postgres, where the row is shared and `A`'s
  logout already bites on `B`. This document covers the revocation *state*, which
  is what the limiter work owned; the storage backend is `docs/12`.
* **The account lockout is a row, not a shared counter.** Six failed logins lock
  an account for fifteen minutes via `users.locked_until` — which is shared, and is
  in fact the strongest cross-instance control in the product. It is separate from
  the limiter and unchanged here.
* **No SSE fan-out over Redis.** `docs/02 §5` pairs the limiter with pub/sub for
  the live stream. The limiter half is done; the stream is still per process, and
  a client connected to node A does not see an event raised on node B. Not claimed
  anywhere in the console.
* **No token bucket, no burst.** A fixed window is coarser than a token bucket for
  a caller that bursts and then goes quiet. Buckets are sized for the coarse
  version; if a bucket needs precision it needs a Lua script, and that is a
  deliberate future change rather than an accident.
* **`RE_LIMITER_OFF` remains refused in production** (`docs/13`), unchanged.

---

## 6. Where it is visible

```
GET /api/v1/security/shared-state          owner/admin
    shared_state   backend, shared, decided_by, claims{shared_between_instances,
                   survives_restart, one_host_only, tls}, info (server version)
    limiter        backend, shared, window (fixed|sliding), fallbacks, buckets
    revocation     shared, backend, local_entries
    next_step      what to do if `shared` is false
    why_it_matters the two sentences at the top of this document

GET /healthz                               owner/admin see `shared_state`
```

The posture score gains a fifth control, computed like all the others: **"Shared
rate-limit and session state"** is `enforced` when the backend is shared and
`degraded` when it is not, with the detail naming the consequence. On a single
instance the number goes *down* — which is correct. It was measuring a product
that could not safely run two.

---

## 7. How it is proved

| check | where |
|---|---|
| RESP2 against a real socket: types, `INCR`, `PTTL`, `SET … PX`, `EXISTS` | `tests/test_shared_state.py` §A |
| `EVAL` refused → `INCR`/`PEXPIRE` fallback, no count lost | `test_a_refused_eval_falls_back_to_incr_and_expire` |
| `rediss://` refused; unreachable host is an error; a non-Redis port is rejected | §A |
| the file backend counts across two handles, expires a window, survives a restart | §B |
| a dead store still enforces in-process | `test_a_dead_store_falls_back_to_enforcing_in_process` |
| pruning never drops a live revocation | `test_revoked_tokens_are_pruned_by_expiry_and_never_cleared` |
| a role change retires every token, and only that user's | §C |
| the dead-code tautology cannot come back | `test_a_role_change_actually_reaches_the_epoch_bump` |
| **two real servers**: ten failures on A refuse B, over `file` and over `redis` | §D |
| the control: with `memory`, B stays fresh | `test_the_same_test_would_fail_without_sharing` |
| a logout on A is seen by another process | `test_a_logout_on_one_instance_is_visible_to_another_process` |
| production refuses to boot; development degrades and says so | §E |
| the whole story, 50 checks, real servers | `python3 scripts/proof_shared_state.py` |

The two-server tests are the point of the whole change: a unit test that shares an
object proves the object is shared; only two processes prove the product is.

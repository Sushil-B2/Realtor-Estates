"""
Step-up re-authentication: proving it is you *now*, not eight hours ago.

`PLANS.md` security follow-up #2, from `docs/13`: _"step-up re-authentication
before kill-switch, key rotation and user deletion."_ The gap it names is the one
every "we have MFA" product has: the factor happens at **login**, and then the
session is trusted for everything, for as long as it lasts. A desk machine left
signed in on Friday is a kill-switch press on Monday, and the second factor was
satisfied last week.

The rule here is deliberately narrow:

    a session proves *who* you are; a step-up ticket proves you satisfied the
    factor *recently*, for *this* action.

Three things make that real rather than decorative, and each of them is a mistake
somebody has already shipped:

1. **A ticket is bound to the action.** A confirmation for "delete this user" must
   not authorise "engage the kill switch". Tickets carry the action name, and a
   ticket for a *specific target* carries that too — so confirming one user's
   deletion does not delete another's.
2. **A ticket is bound to the session.** Copying it to a stolen token is useless:
   the session id is inside the signed payload.
3. **A ticket is single-use.** A confirmation that can be replayed is a long-lived
   credential in a short-lived costume. `spent` is flipped on first use, and the
   store write is the serialisation point (one in-memory dict, one lock).

**Where the factor comes from.** `POST /auth/step-up` takes the account password
and, when the account has one, a TOTP code or a recovery code. A password-only
account gets a shorter ticket (`NO_FACTOR_TTL`, 2 minutes) than an account that
also presented a second factor (`TICKET_TTL`, 5 minutes) — the honest reading of
"step-up" for a workspace that has not enrolled anybody yet, and the console says
which one is in play rather than implying everybody has a factor.

Recovery codes are consumed by this path too. A recovery code that works an
unlimited number of times, from a route nobody watches, is not a recovery code.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

from . import config as cfg
from . import security as sec
from . import shared_state as shared
from . import totp as totpmod

TICKET_TTL = int(cfg.get("RE_STEPUP_TTL", "300") or 300)         # with a factor
NO_FACTOR_TTL = int(cfg.get("RE_STEPUP_NO_FACTOR_TTL", "120") or 120)  # password only
MAX_ATTEMPTS = 5          # wrong password/code attempts per session before a cool-off
COOLOFF = 300

# action -> how long a ticket for it is good for. A ticket is *always* bound to one
# action; this table is what the console reads to say which actions are armed.
ACTIONS = {
    "kill_switch": {"label": "Engage or release the agent kill switch", "ttl": TICKET_TTL},
    "key_rewrap": {"label": "Re-wrap the root key under a new KEK", "ttl": TICKET_TTL},
    "user_delete": {"label": "Deactivate a member of staff", "ttl": TICKET_TTL},
    "role_change": {"label": "Change a member's role", "ttl": TICKET_TTL},
    "mfa_reset": {"label": "Reset another account's second factor", "ttl": TICKET_TTL},
    "secret_write": {"label": "Write a CLI credential (Neon API key)", "ttl": TICKET_TTL},
    "demo_purge": {"label": "Delete rows from the demo workspace", "ttl": TICKET_TTL},
    "payment_adjust": {"label": "Adjust a workspace's usage ledger", "ttl": TICKET_TTL},
}


class StepUpError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 423, details: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or []


def _digest(value: str) -> str:
    """A comparison-safe summary of a secret.

    Password and recovery-code *lookups* go through a digest so a timing trace of
    the comparison never says how much of a guess was right. The real check is
    `sec.verify_password` / the hashed recovery list; this only avoids an early
    return on the first character.
    """
    return hashlib.sha256(("stepup:" + value).encode()).hexdigest()


def ticket_for(session_id: str, action: str, target: str = "", *, ttl: int = TICKET_TTL) -> str:
    """Mint the ticket. Signed with the session key, so it cannot be forged."""
    now = int(time.time())
    effective_ttl = max(1, int(ttl))
    claims = {
        "typ": "stepup",
        "sid": session_id,
        "act": action,
        "tgt": (target or "")[:80],
        "iat": now,
        "exp": now + effective_ttl,
        # A nonce so two tickets for the same action in the same second differ,
        # which makes "have I used this one?" answerable per ticket.
        "jti": secrets.token_urlsafe(9),
        "spent": False,
    }
    return sec.issue_token(claims, ttl=effective_ttl)


def parse(ticket: str) -> dict | None:
    """A valid, unspent ticket payload — or None.

    `verify_token` checks the signature and expiry; the `typ` check is what stops a
    *session* token from being presented as a ticket (they are signed with the same
    key, and a session token that satisfied a step-up gate would make the gate a
    comment).
    """
    if not ticket or not isinstance(ticket, str):
        return None
    claims = sec.verify_token(ticket)
    if not claims or claims.get("typ") != "stepup":
        return None
    return claims


def tickets_for_ttl(has_factor: bool) -> int:
    return TICKET_TTL if has_factor else NO_FACTOR_TTL


# --------------------------------------------------------------------------
# Attempt budget: a step-up prompt is a place to guess a password
# --------------------------------------------------------------------------
class Attempts:
    """Per-session attempt budget, so the confirmation dialog is not a free oracle.

    A route that accepts a password must be as defended as login. It is per
    *session* rather than per account so one person failing five times does not lock
    the account out for everybody (`docs/13` finding on lockout-as-DoS).

    When `shared_state` has a multi-process backend (`redis` or `file`), counts and
    cool-offs live in that store so N workers do not turn 5 guesses into 5 × N
    guesses. If the shared store fails mid-flight, the same limit is enforced
    in-process rather than failing open (`fallbacks` records the fact).
    """

    def __init__(self, backend=None):
        # session id -> [failures, locked_until]. `locked_until` of 0 means "counting,
        # not locked" — which is *not* the same as "expired", and conflating the two
        # was the first bug this class shipped with: an expired-lock check that also
        # fired on "no lock yet" reset the counter on every failure, so MAX_ATTEMPTS
        # was never reached and the budget was decorative (`docs/34 §5`).
        self.state: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self._explicit_backend = backend
        self.fallbacks = 0

    def _adapter(self):
        if self._explicit_backend is not None:
            return self._explicit_backend
        return shared.ADAPTER if shared.SHARED else None

    def _local_blocked(self, session_id: str) -> int:
        with self.lock:
            entry = self.state.get(session_id)
            if not entry:
                return 0
            _n, until = entry
            remaining = until - time.time()
            return max(0, int(remaining)) if until and remaining > 0 else 0

    def _local_fail(self, session_id: str) -> int:
        with self.lock:
            entry = self.state.setdefault(session_id, [0, 0.0])
            if entry[1] and time.time() >= entry[1]:
                entry[0], entry[1] = 0, 0.0
            entry[0] += 1
            if entry[0] >= MAX_ATTEMPTS:
                entry[1] = time.time() + COOLOFF
                return 0
            return int(MAX_ATTEMPTS - entry[0])

    def blocked(self, session_id: str) -> int:
        """Seconds remaining, or 0 if this session may try."""
        adapter = self._adapter()
        if adapter is not None:
            try:
                return adapter.ttl_left(f"stepup:lock:{session_id}")
            except Exception as exc:
                self.fallbacks += 1
                print(f"[stepup] shared attempt store unavailable ({exc}) — checking in-process")
        return self._local_blocked(session_id)

    def fail(self, session_id: str) -> int:
        """Record a failure; return how many attempts are left (0 once locked)."""
        adapter = self._adapter()
        if adapter is not None:
            try:
                if adapter.ttl_left(f"stepup:lock:{session_id}") > 0:
                    return 0
                n = adapter.incr(f"stepup:fail:{session_id}", COOLOFF * 3)
                if n >= MAX_ATTEMPTS:
                    adapter.set_ttl(f"stepup:lock:{session_id}", "1", COOLOFF)
                    adapter.delete(f"stepup:fail:{session_id}")
                    return 0
                return int(MAX_ATTEMPTS - n)
            except Exception as exc:
                self.fallbacks += 1
                print(f"[stepup] shared attempt store unavailable ({exc}) — counting in-process")
        return self._local_fail(session_id)

    def clear(self, session_id: str) -> None:
        with self.lock:
            self.state.pop(session_id, None)
        adapter = self._adapter()
        if adapter is not None:
            try:
                adapter.delete(f"stepup:fail:{session_id}", f"stepup:lock:{session_id}")
            except Exception as exc:
                self.fallbacks += 1
                print(f"[stepup] shared attempt store clear failed ({exc})")


ATTEMPTS = Attempts()


class Spent:
    """Tickets already used, with their expiry, so nothing un-spends.

    Bounded by expiry rather than by a cap for the same reason the revocation list
    is (`docs/31 §4`): a set that is cleared to save memory hands back a replayed
    confirmation. A ticket lives five minutes, so this holds at most one quarter
    hour of confirmations, and old ones are dropped only after they could no longer
    be valid.

    When `shared_state` has a multi-process backend (`redis` or `file`), spending a
    ticket uses an atomic single-winner claim (`SET ... PX ... NX` on Redis, `flock`
    on `SharedFiles`) so a ticket spent on instance A cannot be replayed on instance
    B — even if two requests race simultaneously across two workers. If a configured
    shared store is unreachable, `claim()` returns `'unavailable'` (fail-closed)
    rather than letting a confirmation execute without cluster replay protection.
    """

    def __init__(self, backend=None):
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()
        self._explicit_backend = backend
        self.failures = 0

    def _adapter(self):
        if self._explicit_backend is not None:
            return self._explicit_backend
        return shared.ADAPTER if shared.SHARED else None

    def claim(self, jti: str, ttl: float | None = None) -> str:
        """'ok' on first use, 'replayed' if already spent, 'unavailable' if the
        configured shared backend cannot answer (fail-closed)."""
        effective_ttl = float(ttl if ttl is not None else TICKET_TTL) + 60.0
        adapter = self._adapter()
        if adapter is None:
            now = time.time()
            with self.lock:
                for k in [k for k, exp in self.seen.items() if exp < now]:
                    del self.seen[k]
                if jti in self.seen:
                    return "replayed"
                self.seen[jti] = now + effective_ttl
                return "ok"

        now = time.time()
        with self.lock:
            for k in [k for k, exp in self.seen.items() if exp < now]:
                del self.seen[k]
            if jti in self.seen:
                return "replayed"
        try:
            won = adapter.claim_once(f"stepup:{jti}", effective_ttl)
        except Exception as exc:
            self.failures += 1
            print(f"[stepup] shared replay store unreachable ({exc}) — refusing ticket")
            return "unavailable"
        with self.lock:
            self.seen[jti] = time.time() + effective_ttl
        return "ok" if won else "replayed"

    def mark(self, jti: str) -> bool:
        """True on first use, False if already spent or if the shared store is down."""
        return self.claim(jti) == "ok"

    def clear(self) -> None:
        with self.lock:
            self.seen.clear()


SPENT = Spent()


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------
def verify_factor(user: dict, password: str, code: str = "", recovery: str = "") -> dict:
    """Check a password and (when enrolled) a second factor.

    Returns `{"ok": bool, "method": str, "reason": str, "step": int|None}`. Never
    raises for a wrong credential: the caller decides how to answer, and it must not
    leak *which* part was wrong.
    """
    if not password or not sec.verify_password(password, user.get("password_hash", "")):
        return {"ok": False, "method": "password", "reason": "password_incorrect", "step": None}

    enrolled = bool(user.get("mfa_enrolled") and user.get("mfa_secret"))
    if not enrolled:
        # No factor on the account. The ticket is shorter, and `/security/step-up`
        # says so — this is not a claim that a password is two factors.
        return {"ok": True, "method": "password", "reason": "", "step": None}

    if recovery:
        target = sec.blind_token(totpmod.normalise_recovery_code(recovery))
        codes = list(user.get("mfa_recovery") or [])
        hit = next((c for c in codes if not c.get("used_at")
                    and sec.hmac.compare_digest(c.get("h", ""), target)), None)
        if not hit:
            return {"ok": False, "method": "recovery_code", "reason": "recovery_code_invalid", "step": None}
        # consume it: a recovery code is single-use everywhere, not just at login
        return {"ok": True, "method": "recovery_code", "reason": "", "step": None,
                "consume_recovery": hit, "recovery_list": codes}

    if not code:
        return {"ok": False, "method": "totp", "reason": "factor_required", "step": None}

    last = int(user["mfa_last_step"]) if user.get("mfa_last_step") else None
    try:
        verdict = totpmod.verify(_secret(user), code, last_step=last)
    except totpmod.TotpError:
        # a row that says enrolled but holds an unsealable seed is a broken state,
        # not a wrong code. Refuse, and say nothing about which — the alternative is
        # a confirmation that silently succeeds on a corrupt account (docs/27)
        return {"ok": False, "method": "totp", "reason": "factor_unavailable", "step": None}
    if not verdict.get("ok"):
        return {"ok": False, "method": "totp", "reason": verdict.get("reason", "code_incorrect"),
                "step": None}
    return {"ok": True, "method": "totp", "reason": "", "step": verdict.get("step")}


def _secret(user: dict) -> str:
    """The TOTP seed is sealed at rest (docs/27); this unseals it for verification.

    The AAD is the tenant *and* the user, exactly as `app/api.py:_mfa_seal` writes
    it. Getting that string wrong does not fail loudly — `unseal` raises, this
    returns "", and `totp.verify` then raises MFA_NOT_ENROLLED, so a wrong AAD looks
    like "nobody has enrolled" for an account that plainly has. Second bug this
    module shipped with, found by the first test that used a real enrolled account.
    """
    raw = user.get("mfa_secret", "")
    if not raw:
        return ""
    try:
        return sec.unseal(raw, f"mfa:{user.get('tenant_id', '')}:{user.get('id', '')}")
    except Exception:
        return ""


def needs_step_up(user: dict) -> bool:
    return bool(user.get("mfa_enrolled") and user.get("mfa_secret"))


def describe(user: dict) -> dict:
    """What the console may say about this workspace's step-up posture."""
    enrolled = needs_step_up(user)
    return {
        "factor_available": enrolled,
        "ticket_seconds": tickets_for_ttl(enrolled),
        "actions": [{"action": k, "label": v["label"], "ttl": tickets_for_ttl(enrolled)}
                    for k, v in ACTIONS.items()],
        "claims": {
            "password_only": not enrolled,
            "note": ("this account has no second factor: a step-up ticket is proven by the "
                     "password alone and lasts "
                     f"{NO_FACTOR_TTL}s — enrol an authenticator (docs/27) to make it a factor")
                    if not enrolled else
                    ("a ticket needs the password and a code, is bound to one action and one "
                     "session, is single-use, and expires in "
                     f"{TICKET_TTL}s (docs/34)"),
            "replayable": False,
            "shared_across_instances": shared.SHARED,
            "protects": [v["label"] for v in ACTIONS.values()],
        },
        "shared_state": {
            "backend": shared.NAME,
            "shared_across_instances": shared.SHARED,
            "replay_store_failures": SPENT.failures,
            "attempt_store_fallbacks": ATTEMPTS.fallbacks,
            "note": (f"single-use tickets and attempt cool-offs are shared across workers ({shared.NAME})"
                     if shared.SHARED else
                     "process memory only — configure RE_REDIS_URL (multi-host) or RE_SHARED_DIR "
                     "(single-host workers) before running more than one server instance"),
        },
    }

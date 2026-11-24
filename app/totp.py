"""
TOTP (RFC 6238) — the second factor, implemented rather than imported.

The desk has displayed `mfa_enabled: true` for owner and admin accounts since the
first release, and nothing has ever verified a code: a flag, not a factor. That is
worse than no MFA, because it is a claim a buyer can read on a settings page.

Why no dependency: a TOTP verifier is forty lines of HMAC and base32, it is the
kind of code that must not have supply-chain risk, and the RFC publishes test
vectors — so it can be *proved* rather than trusted. `tests/test_mfa.py` runs the
RFC 4226 and 6238 vectors verbatim.

The four rules that make a second factor real, and that most implementations get
wrong:

1. **A window, not a clock.** Verification accepts ±1 step (30 s) so a phone whose
   clock is 20 seconds fast is not locked out — but no wider, or a code stays valid
   for minutes and the factor stops being a factor.
2. **A code is single-use.** The step that produced an accepted code is recorded
   (`mfa_last_step`); the same code, or any code from an earlier step, is refused
   even inside the window. Without this, a code read over somebody's shoulder works
   twice.
3. **Constant-time comparison.** `hmac.compare_digest`, always.
4. **Recovery codes are single-use and hashed.** Ten of them, generated once,
   shown once, stored as blind indices under the keyring — never in plaintext, so a
   database dump does not hand over a second factor.

Pure: no storage, no HTTP, no clock of its own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
STEP_SECONDS = 30
WINDOW = 1                     # ±1 step
SECRET_BYTES = 20              # RFC 4226 recommends 160 bits
RECOVERY_CODES = 10
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I
ISSUER = "Sell.Do"


class TotpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- secrets
def new_secret() -> str:
    """A fresh base32 secret, no padding — what every authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def decode_secret(secret: str) -> bytes:
    cleaned = (secret or "").strip().replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding, casefold=True)
    except Exception:
        raise TotpError("INVALID_SECRET", "That is not a base32 TOTP secret.")


def provisioning_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """The `otpauth://` URI an authenticator app scans.

    The issuer appears twice — as a label prefix and as a parameter — because
    Google Authenticator reads the parameter and others read the label, and a
    mismatch shows the user two differently-named entries for one account.
    """
    label = quote(f"{issuer}:{account}", safe="")
    params = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                        "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{params}"


def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """Human-readable, unambiguous, single-use."""
    out = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def normalise_recovery_code(code: str) -> str:
    """What the user typed → the canonical form, with the characters they confused
    fixed. A recovery code that fails because somebody typed `O` for `0` is a
    recovery code that does not recover."""
    cleaned = "".join(c for c in (code or "").upper() if c.isalnum())
    cleaned = cleaned.replace("O", "0").replace("I", "1").replace("B", "8")
    # 0/1/8 are not in the alphabet; map them back to what they look like
    cleaned = cleaned.replace("0", "O").replace("1", "I").replace("8", "B")
    return f"{cleaned[:5]}-{cleaned[5:]}" if len(cleaned) >= 10 else cleaned


# ------------------------------------------------------------------- codes
def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 §5.3 — the dynamic-truncation dance, verbatim."""
    mac = hmac.new(decode_secret(secret), struct.pack(">Q", int(counter)), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def step_at(at: int | None = None, step_seconds: int = STEP_SECONDS) -> int:
    return int((time.time() if at is None else at) // step_seconds)


def totp(secret: str, at: int | None = None, digits: int = DIGITS) -> str:
    return hotp(secret, step_at(at), digits)


def codes_for_window(secret: str, at: int | None = None, window: int = WINDOW) -> list[tuple[int, str]]:
    """(step, code) for the accepted window, nearest step first."""
    centre = step_at(at)
    return [(centre + delta, totp(secret, (centre + delta) * STEP_SECONDS))
            for delta in range(-window, window + 1)]


def verify(secret: str, code: str, *, at: int | None = None, window: int = WINDOW,
           last_step: int | None = None) -> dict:
    """Check a code and say which step it belonged to.

    Returns `{ok, step, reason}` — never raises for a wrong code, because a wrong
    code is an expected outcome and a verifier that throws is a verifier whose
    callers forget to catch. Raises only for a structural problem (a malformed
    secret, no secret at all) that means the caller has a bug.
    """
    if not secret:
        raise TotpError("MFA_NOT_ENROLLED", "No authenticator has been enrolled for this account.")
    presented = "".join(c for c in str(code or "") if c.isdigit())
    if len(presented) != DIGITS:
        return {"ok": False, "step": None, "reason": "code_malformed"}
    centre = step_at(at)
    if last_step is not None and centre + window <= int(last_step):
        # every code still valid is at or before one we have already spent
        return {"ok": False, "step": None, "reason": "code_replayed"}
    for step, expected in codes_for_window(secret, at, window):
        if hmac.compare_digest(expected, presented):
            if last_step is not None and step <= int(last_step):
                return {"ok": False, "step": None, "reason": "code_replayed"}
            return {"ok": True, "step": step, "reason": ""}
    return {"ok": False, "step": None, "reason": "code_incorrect"}


def drift(presented_step: int, at: int | None = None) -> int:
    """How many steps ahead of (or behind) us the client's clock is."""
    return int(presented_step) - step_at(at)

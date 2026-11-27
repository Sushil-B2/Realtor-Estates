"""
Sessions as records — access tokens, rotating refresh tokens, a device list.

Until v1.17 a "session" was an eight-hour bearer token and a set: the token was
valid for as long as it said it was, nothing could end it early, and the only way
to see who was signed in to a workspace was to read the access log. Three
consequences, in the order they hurt:

1. **A stolen token lived eight hours.** Revoking meant remembering a `jti` in one
   process's memory; a restart forgot it, and a second instance never knew.
2. **Nobody could answer "where am I signed in?"** — the question every SaaS user
   asks the day they lose a laptop.
3. **Theft was invisible.** With no refresh token there is no rotation, and with
   no rotation there is no way to notice that one credential is being used twice.

The shape now:

```
login ──▶ session row (sid)  ──▶ access token   (HMAC, 30 min, carries sid)
                │
                └──────────────▶ refresh token  (opaque, 30 days, hashed at rest)
                                          │
                                    POST /auth/refresh
                                          │
                            rotate: new refresh, old one remembered
                                          │
                        old one used again ──▶ reuse ⇒ revoke the session
```

**The access token stays a signed, stateless-looking string** — it is cheap to
check and it carries the role — but `api.handle` looks its `sid` up in the
`sessions` collection on every request. That lookup is what makes revocation
immediate rather than eventual. A session row is small and the store is in memory,
so the cost is a dict lookup, and the benefit is that `DELETE /auth/sessions/{sid}`
works on the very next request instead of the next login.

**The refresh token is opaque** (32 random bytes, base64url) rather than signed,
because it is looked up rather than parsed: a signed token would have to be
revoked by id anyway, so the signature would only add length.

**Rotation with reuse detection** is the part that earns its complexity. Every
rotation moves the hash into `refresh_history` (bounded, oldest dropped). If a
token arrives that matches a *historical* hash, two people hold the same
credential: the legitimate holder already rotated past it. The session is revoked
on the spot and the event is audited as `auth.refresh_reuse` — the account is then
forced to a fresh login, which is the correct outcome for both the thief and the
victim.
"""
from __future__ import annotations

import base64
import re
import secrets
import time
from typing import Any

from . import config as cfg
from . import security as sec

def _seconds(name: str, default: int) -> int:
    """A configured lifetime, or the default.

    `.env.example` ships both knobs *empty*, and `int("")` at import time would be
    a boot failure caused by copying the example — so an empty or malformed value
    means "use the default", not "crash".
    """
    raw = str(cfg.get(name, "") or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# Short enough that a stolen access token is a nuisance, long enough that the SPA
# is not refreshing on every click. The knob exists so a test can prove expiry.
ACCESS_TTL = _seconds("RE_ACCESS_TTL", 30 * 60)
REFRESH_TTL = _seconds("RE_REFRESH_TTL", 30 * 86400)
REFRESH_COOKIE = "re_refresh"
# How many superseded hashes stay readable for reuse detection. Four rotations is
# several hours of normal use; beyond that the row would grow without bound.
HISTORY = 4
TOUCH_EVERY = 60          # seconds between last_seen writes


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- devices
def describe_device(ua: str) -> dict:
    """A label a person recognises on a device list, from the User-Agent.

    Deliberately coarse. The list exists so somebody can say "that one is not me",
    which needs a browser, an operating system and a rough kind — not a fingerprint.
    """
    raw = (ua or "").strip()
    low = raw.lower()
    if not raw:
        return {"label": "Unknown client", "browser": "", "os": "", "kind": "unknown"}
    if low.startswith(("curl/", "wget/", "python-requests", "python-urllib", "httpx/", "proof-")):
        return {"label": "Script", "browser": raw.split("/")[0][:24], "os": "", "kind": "script"}

    browser = ""
    for needle, name in (("edg/", "Edge"), ("opr/", "Opera"), ("chrome/", "Chrome"),
                         ("firefox/", "Firefox"), ("safari/", "Safari"), ("okhttp", "OkHttp")):
        if needle in low:
            browser = name
            break
    os_name = ""
    for needle, name in (("windows", "Windows"), ("iphone", "iOS"), ("ipad", "iPadOS"),
                         ("android", "Android"), ("mac os", "macOS"), ("cros", "ChromeOS"),
                         ("linux", "Linux")):
        if needle in low:
            os_name = name
            break
    kind = "mobile" if "mobile" in low else "browser"
    label = " ".join(x for x in (browser or "Browser", "on", os_name) if x).strip()
    return {"label": label[:60], "browser": browser, "os": os_name, "kind": kind}


def mask_ip(ip: str) -> str:
    """Enough of an address to recognise the office, not enough to be a location."""
    ip = (ip or "").strip()
    if not ip:
        return ""
    if ":" in ip:                                  # IPv6: keep the first 3 groups
        return ":".join(ip.split(":")[:3]) + "::"
    parts = ip.split(".")
    return ".".join(parts[:3]) + ".x" if len(parts) == 4 else ip


# ------------------------------------------------------------- refresh tokens
def new_refresh_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _hash(token: str) -> str:
    # `blind_token`, not `blind_index`: a refresh token is neither a phone number
    # nor an email, and blind_index would keep only its digits (docs/27 §3).
    return sec.blind_token(token)


def open_session(store, tenant_id: str, user_id: str, *, ip: str = "", ua: str = "",
                 amr: list[str] | None = None) -> tuple[dict, str]:
    """Create a session row and the refresh token that belongs to it."""
    token = new_refresh_token()
    row = store.insert("sessions", tenant_id, {
        "user_id": user_id,
        "refresh_hash": _hash(token),
        "refresh_history": [],
        "family_id": "fam_" + secrets.token_hex(6),
        "device": describe_device(ua),
        "user_agent": (ua or "")[:200],
        "ip_created": ip,
        "ip_last": ip,
        "amr": list(amr or ["pwd"]),
        "created_at": now(),
        "last_seen": now(),
        "expires_at": now() + REFRESH_TTL,
        "revoked_at": 0,
        "revoked_reason": "",
    }, prefix="ses")
    return row, token


# ------------------------------------------------------- fresh-session mode
class RefreshConflict(Exception):
    """The presented refresh token was superseded or ended by another request."""


def _fresh(store) -> bool:
    """Read and write sessions through the database rather than a cached copy.

    Needed when several instances serve one workspace (serverless): a cached
    row can miss a revocation made elsewhere. On with `RE_FRESH_SESSIONS=1`
    (support for it is then required, so a gap fails closed) and by default
    with per-request database reads. JSON and single-process modes are unchanged.
    """
    backend = getattr(store, "backend", None)
    if backend is None:
        return False
    flag = (cfg.get("RE_FRESH_SESSIONS", "") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(getattr(store, "stateless_reads", False)) and callable(getattr(backend, "merge_document", None))


def _remember(store, doc: dict | None) -> dict | None:
    """Replace this instance's cached copy with the row the database returned."""
    if not doc:
        return None
    doc = dict(doc)
    with store.lock:
        rows = store.db["sessions"]
        for i, cached in enumerate(rows):
            if cached.get("id") == doc.get("id") and cached.get("tenant_id") == doc.get("tenant_id"):
                rows[i] = doc
                break
        else:
            rows.append(doc)
    return dict(doc)


def _merge(store, row: dict, patch: dict, *, expect_hash: str | None = None,
           require_live: bool = False) -> dict | None:
    """Write only the changed fields in one statement; None if a condition failed."""
    patch = {**patch, "updated_at": now()}
    doc = store.backend.merge_document("sessions", row["tenant_id"], row["id"], patch,
                                       expect_hash=expect_hash, require_live=require_live)
    return _remember(store, doc)


def refresh_tenant(store, tenant_id: str) -> None:
    """Fresh-session mode: reload a tenant's sessions before listing or ending them."""
    if tenant_id and _fresh(store):
        store.reload("sessions", tenant_id)


def by_refresh(store, token: str) -> dict | None:
    """The live session this refresh token belongs to, or None."""
    if not token:
        return None
    target = _hash(token)
    if _fresh(store):
        doc, current = store.backend.find_session_by_refresh_hash(target)
        return _remember(store, doc) if doc and current else None
    for row in store.db["sessions"]:
        if sec.hmac.compare_digest(row.get("refresh_hash", ""), target):
            return row
    return None


def reuse_of(store, token: str) -> dict | None:
    """A *superseded* refresh token — the signature of a stolen credential."""
    if not token:
        return None
    target = _hash(token)
    if _fresh(store):
        doc, current = store.backend.find_session_by_refresh_hash(target)
        return _remember(store, doc) if doc and not current else None
    for row in store.db["sessions"]:
        for old in row.get("refresh_history") or []:
            if sec.hmac.compare_digest(old.get("h", ""), target):
                return row
    return None


def alive(row: dict) -> bool:
    return bool(row) and not row.get("revoked_at") and int(row.get("expires_at") or 0) > now()


def rotate(store, row: dict, *, ip: str = "", ua: str = "") -> tuple[dict, str]:
    """Hand back a new refresh token and retire the presented one.

    The retired hash is kept for `HISTORY` rotations so that a replay can be
    recognised rather than merely refused — a refusal tells the thief to try
    again tomorrow; a revocation tells them the credential is dead.
    """
    token = new_refresh_token()
    history = [{"h": row.get("refresh_hash", ""), "at": now()}] + list(row.get("refresh_history") or [])
    patch = {
        "refresh_hash": _hash(token),
        "refresh_history": history[:HISTORY],
        "ip_last": ip or row.get("ip_last", ""),
        "last_seen": now(),
    }
    if ua and ua != row.get("user_agent"):
        patch["user_agent"] = ua[:200]
    if _fresh(store):
        # Compare-and-set: only the holder of the current hash may rotate it.
        updated = _merge(store, row, patch, expect_hash=row.get("refresh_hash", ""), require_live=True)
        if not updated:
            raise RefreshConflict("refresh token superseded")
        return updated, token
    updated = store.update("sessions", row["tenant_id"], row["id"], patch) or {**row, **patch}
    return updated, token


def revoke(store, row: dict, reason: str, *, by: str = "") -> dict:
    """End a session. Returns the row as written — a caller that reports `revoked`
    from its own stale copy reports the opposite of what it just did."""
    sid = row.get("id", "")
    if sid:
        try:
            from . import shared_state as shared
            if shared.SHARED:
                shared.ADAPTER.revoke(f"sid:{sid}", float(REFRESH_TTL))
        except Exception:
            pass
    ended = {"revoked_at": now(), "revoked_reason": reason[:60], "revoked_by": by[:40]}
    if _fresh(store):
        updated = _merge(store, row, ended, require_live=True)
        if updated:
            return updated
        # Already ended elsewhere: keep the original reason and report the stored row.
        current = store.backend.get_document("sessions", row["tenant_id"], row["id"])
        return _remember(store, current) or {**row, **ended}
    return store.update("sessions", row["tenant_id"], row["id"], ended) or row


def revoke_all(store, tenant_id: str, user_id: str, reason: str, *, keep_sid: str = "",
               by: str = "") -> int:
    killed = 0
    refresh_tenant(store, tenant_id)
    for row in list(store.db["sessions"]):
        if row.get("tenant_id") != tenant_id or row.get("user_id") != user_id:
            continue
        if row["id"] == keep_sid or row.get("revoked_at"):
            continue
        revoke(store, row, reason, by=by)
        killed += 1
    return killed


def active(store, sid: str, tenant_id: str = "") -> dict | None:
    """The session behind an access token, if it is still alive."""
    if not sid:
        return None
    if sec.is_revoked(f"sid:{sid}"):
        return None
    if _fresh(store):
        # Never trust a cached copy: another instance may have ended it. A
        # storage failure propagates (generic 503) instead of using the cache.
        if not tenant_id:
            return None
        doc = _remember(store, store.backend.get_document("sessions", tenant_id, sid))
        return doc if doc and alive(doc) else None
    for row in store.db["sessions"]:
        if row.get("id") == sid:
            return row if alive(row) else None
    # Multi-instance Postgres hydration: if node A minted the session after
    # node B booted, reload this tenant's session rows once on cache miss.
    if tenant_id and getattr(store, "backend", None) is not None:
        try:
            store.reload("sessions", tenant_id)
        except Exception:
            return None
        for row in store.db["sessions"]:
            if row.get("id") == sid:
                return row if alive(row) else None
    return None


def touch(store, row: dict, *, ip: str = "") -> None:
    """`last_seen` is a write per request otherwise — once a minute is plenty."""
    if now() - int(row.get("last_seen") or 0) < TOUCH_EVERY:
        return
    if _fresh(store):
        _merge(store, row, {"last_seen": now(), "ip_last": ip or row.get("ip_last", "")}, require_live=True)
        return
    store.update("sessions", row["tenant_id"], row["id"],
                 {"last_seen": now(), "ip_last": ip or row.get("ip_last", "")})


def public(row: dict, current_sid: str = "") -> dict:
    """What a device list may show. Never the refresh hash, never the user agent."""
    return {
        "id": row["id"],
        "device": row.get("device") or {"label": "Unknown client"},
        "ip": mask_ip(row.get("ip_last") or row.get("ip_created", "")),
        "created_at": row.get("created_at"),
        "last_seen": row.get("last_seen"),
        "expires_at": row.get("expires_at"),
        "amr": row.get("amr") or [],
        "mfa": "totp" in (row.get("amr") or []),
        "current": bool(current_sid) and row["id"] == current_sid,
        "revoked": bool(row.get("revoked_at")),
    }


def label_cookie(ua: str) -> str:
    """A stable, non-identifying label for the cookie name in a log line."""
    return re.sub(r"[^A-Za-z0-9]+", "-", describe_device(ua).get("label", "client")).strip("-")[:40]


def ttl_report() -> dict[str, Any]:
    return {"access_ttl": ACCESS_TTL, "refresh_ttl": REFRESH_TTL, "cookie": REFRESH_COOKIE}

"""
Audit PII retention without breaking the SHA-256 hash chain (docs/35).

`PLANS.md` security follow-up #6 (from `docs/13`):
    "Decide a retention window for audit IPs/emails before real tenant data lands."

Why a naive `DELETE` or `UPDATE` on old audit rows is wrong here:
  1. `re_audit` has a database trigger (`SEN-C805`) that refuses UPDATE and DELETE.
  2. `sec.audit_hash(entry)` commits to `actor`, `target`, and `meta`. Mutating an
     old row's `actor` or `meta.ip` in storage invalidates its hash and breaks
     `verify_audit()` for every row after it across the 7-year (`2555`-day) archive.

So the retention window works by **keyed presentation pseudonymisation**:
  * Raw rows in storage are never mutated, so `verify_audit()` continues to verify
    the entire 7-year cryptographic chain byte-for-byte.
  * When an audit entry is older than `audit_pii_retention_days` (default **90
    days**, configurable per workspace in `7..2555` days or via
    `RE_AUDIT_PII_RETENTION_DAYS`), personal identifiers (`actor` email, email
    `target`, and IP/email fields inside `meta`) are replaced on read with stable
    HMAC pseudonyms keyed by `KEYS["blind"]` (e.g. `redacted-actor:8f3a1c9210`,
    `redacted-ip:4d2e9b10aa`).
  * Stable keyed pseudonyms let an investigator correlate that the *same* actor or
    IP performed a sequence of historical events without exposing personal emails
    or home IP addresses in the console or API responses indefinitely.

Honest limit (stated in `/api/v1/tenant/audit` and `docs/35`): because the raw
append-only table is untouched to preserve the hash chain, the database volume and
backups still hold the original values until the 7-year archive window ends. True
cryptographic erasure at day 90 would require encrypting audit PII under rotating
period subkeys and destroying expired subkeys.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any

from . import config as cfg
from . import security as sec

ARCHIVE_DAYS = 2555
MIN_PII_DAYS = 7
MAX_PII_DAYS = ARCHIVE_DAYS
DEFAULT_PII_DAYS = max(
    MIN_PII_DAYS,
    min(MAX_PII_DAYS, int(cfg.get("RE_AUDIT_PII_RETENTION_DAYS", "90") or 90)),
)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_IP_KEYS = {"ip", "client_ip", "source_ip", "remote_ip", "peer_ip"}
_EMAIL_KEYS = {"email", "actor_email", "user_email", "target_email"}


def validate_days(raw: Any) -> int:
    """Validate a workspace `audit_pii_retention_days` setting (`7..2555`)."""
    if isinstance(raw, bool):
        raise ValueError("audit_pii_retention_days must be an integer between 7 and 2555.")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError("audit_pii_retention_days must be an integer between 7 and 2555.")
    if days < MIN_PII_DAYS or days > MAX_PII_DAYS:
        raise ValueError(f"audit_pii_retention_days must be between {MIN_PII_DAYS} and {MAX_PII_DAYS} days.")
    return days


def pii_days_for(tenant: dict | None) -> int:
    raw = ((tenant or {}).get("settings") or {}).get("audit_pii_retention_days")
    if raw is None:
        return DEFAULT_PII_DAYS
    try:
        return validate_days(raw)
    except ValueError:
        return DEFAULT_PII_DAYS


def pseudonym(kind: str, value: str) -> str:
    clean = (value or "").strip().lower()
    tag = hmac.new(sec.KEYS["blind_index"], f"audit-{kind}:{clean}".encode("utf-8"), hashlib.sha256).hexdigest()[:10]
    return f"redacted-{kind}:{tag}"


def _looks_like_email(val: Any) -> bool:
    return isinstance(val, str) and bool(_EMAIL_RE.match(val.strip()))


def _scrub_meta(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _IP_KEYS and isinstance(v, str) and v.strip():
                out[k] = pseudonym("ip", v)
            elif kl in _EMAIL_KEYS and isinstance(v, str) and v.strip():
                out[k] = pseudonym("email", v)
            elif _looks_like_email(v):
                out[k] = pseudonym("email", v)
            else:
                out[k] = _scrub_meta(v)
        return out
    if isinstance(obj, list):
        return [_scrub_meta(item) for item in obj]
    if _looks_like_email(obj):
        return pseudonym("email", obj)
    return obj


def present_entry(entry: dict, *, retention_days: int, now_ts: int | None = None) -> dict:
    """Return a copy of `entry` with personal identifiers pseudonymised if expired."""
    current = int(time.time()) if now_ts is None else int(now_ts)
    age_s = max(0, current - int(entry.get("at") or 0))
    if age_s <= retention_days * 86400:
        return {**entry, "pii_redacted": False}

    copied = dict(entry)
    actor = str(copied.get("actor") or "")
    if _looks_like_email(actor):
        copied["actor"] = pseudonym("actor", actor)
    target = str(copied.get("target") or "")
    if _looks_like_email(target):
        copied["target"] = pseudonym("target", target)
    if isinstance(copied.get("meta"), dict):
        copied["meta"] = _scrub_meta(copied["meta"])
    copied["pii_redacted"] = True
    return copied


def present_entries(entries: list[dict], tenant: dict | None,
                    *, now_ts: int | None = None) -> tuple[list[dict], dict]:
    days = pii_days_for(tenant)
    out = [present_entry(e, retention_days=days, now_ts=now_ts) for e in entries]
    redacted_count = sum(1 for e in out if e.get("pii_redacted"))
    return out, {
        "pii_retention_days": days,
        "archive_days": ARCHIVE_DAYS,
        "redacted_count": redacted_count,
        "mode": "keyed_hmac_pseudonym_on_read",
        "chain_intact": True,
        "honest_limit": (
            "raw append-only rows in storage are untouched so the 7-year SHA-256 "
            "hash chain remains verifiable; expired emails and IPs are pseudonymised "
            "at read/export time rather than erased from database backups"
        ),
    }

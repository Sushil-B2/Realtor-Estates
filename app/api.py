"""
Realtor Estates — REST API (/api/v1).

Design invariants (enforced here, not in the UI):
  * tenant_id always comes from the verified session token — never from a
    query string, path segment or request body.
  * every state-changing request needs a CSRF token bound to the session
    (double-submit) and, when an Origin header is present, must be same-origin.
  * roles are checked server-side per route; the UI never decides access.
  * PII is decrypted only for roles holding `pii:read`; everyone else gets a
    masked value. Writes are allowlisted per field (no mass assignment).
  * every mutation lands in the hash-chained audit log.
"""
from __future__ import annotations

import base64
import csv
import datetime
import hashlib
import io
import json
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, quote

from . import neon
from . import ai_governance as aigov
from . import phase3_expansion as p3_mod
from . import phase4_platform as p4_mod
from . import govt_sales_cycle as gsc_mod
from . import security_hardening as hard_mod
from . import mcp_platform as mcp_mod
from . import channels
from . import compliance as comp_mod
from . import connectors as conn_mod
from . import observability as obs
from . import realtime as rt
from . import vertical as vt
from . import ingest as ing
from . import media as md
from . import proofing as pf
from . import totp as totpmod
from . import sessions as sess
from . import stepup
from .stepup import SPENT
from . import audit_retention as aud_ret
from . import meters as ut
from . import kms as kms_ctl
from . import shared_state as shared
from . import partners as cpm
from . import inventory as ivt
from . import taxonomy as tax
from . import automations as autm
from . import money as money_rules
from . import agents as ag
from . import redteam
from . import config as cfg
from . import security as sec
from . import agency_growth_suite as ags_mod
from . import enterprise_observability_sso as eos_mod
from .store import STORE, Store, nid, now

ROLE_RANK = {"channel_partner": 0, "viewer": 1, "agent": 2, "manager": 3, "admin": 4, "owner": 5}
# A partner is a login, but only into the partner portal. Every route it may
# reach is under this prefix; anything else is a 403, including routes it
# would otherwise pass a role check for.
CP_ROUTE_PREFIX = "/api/v1/cp/"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []


ROUTES: list[tuple[str, re.Pattern, str, int]] = []


def route(method: str, pattern: str, min_role: str | None = None, public: bool = False, bucket: str | None = None):
    def deco(fn: Callable):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn.__name__, 0))
        fn._min_role = min_role
        fn._public = public
        fn._bucket = bucket
        return fn

    return deco


class Ctx:
    def __init__(self, store: Store, method: str, path: str, query: dict, body: dict, headers: dict,
                 client_ip: str, raw: bytes = b""):
        self.store = store
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.headers = headers
        self.client_ip = client_ip
        self.claims: dict = {}
        self.tenant: dict = {}
        self.user: dict = {}
        self.params: dict = {}
        self.now = now()
        # the raw session token, kept so a route can derive the caller's CSRF
        # token (GET /api/v1/auth/session) without re-reading the cookie
        self.raw_token: str = ""
        # the exact bytes received. A webhook signature is over the raw body —
        # re-serialising parsed JSON changes key order and whitespace and every
        # signature then fails (the payments function learned this first, docs/15).
        self.raw: bytes = raw
        # set when the caller declares itself a proof run (docs/26): every row the
        # request creates is stamped with it, so a demo can be reseeded without
        # guessing which rows were ours
        self.proof_run: str = ""
        # true when the workspace demands MFA and this session has not enrolled —
        # the session works, the dangerous actions do not (docs/27)
        self.mfa_pending: bool = False
        # the `sessions` row behind this access token (docs/28): what makes a
        # revoke take effect on the next request rather than the next login
        self.session: dict = {}
        self.user_agent: str = str(headers.get("user-agent", ""))[:200]

    # ---- identity ----
    @property
    def tid(self) -> str:
        return self.claims.get("tid", "")

    @property
    def role(self) -> str:
        return self.claims.get("role", "viewer")

    def can(self, action: str) -> bool:
        r = self.role
        return {
            "pii:read": r in ("owner", "admin", "manager"),
            "write": r in ("owner", "admin", "manager", "agent"),
            "manage_users": r in ("owner", "admin"),
            "manage_tenant": r in ("owner", "admin"),
            "decide_learning": r in ("owner", "admin", "manager"),
            "run_redline": r in ("owner", "admin"),
            "kill_switch": r == "owner",
            # channel partners: approving a claim binds the firm to pay a
            # commission, so it is a manager decision; paying one is not.
            "approve_cp": r in ("owner", "admin", "manager"),
            "pay_partner": r in ("owner", "admin"),
        }.get(action, False)

    # Actions a half-authenticated session may not take. Any workspace can demand
    # MFA; until it is enrolled, the session keeps working for the day's work but
    # cannot change who exists, what the workspace is, or where money goes.
    # `manage_tenant` is deliberately NOT in this list. A workspace that demands a
    # factor must still be able to *stop* demanding one from a session that has not
    # enrolled yet — otherwise turning the policy on locks the owner out of the
    # switch that turns it off, which is the same self-lockout this file spends
    # docs/27 warning about. Identity, money and the kill switch are gated; a
    # quiet-hours setting is not.
    MFA_SENSITIVE = ("manage_users", "kill_switch", "pay_partner", "approve_cp")

    def require_step_up(self, action: str, target: str = "") -> dict:
        """Demand a fresh confirmation for one dangerous action, and spend it.

        Call this *after* `require()`: the role check answers "may this person do
        this at all", this answers "did they prove it is them, recently, for this".
        A 423 rather than a 403, because it is not a permissions problem — the
        answer is a confirmation, and the response says which one to send.

        The ticket is single-use: it is marked spent here, in the same lock that
        read it, so two concurrent requests cannot both ride one confirmation.
        """
        ticket = sec.clean(self.headers.get("x-step-up", ""), 4096)
        claims = stepup.parse(ticket)
        wanted = stepup.ACTIONS.get(action, {})
        if not claims:
            raise ApiError(423, "STEP_UP_REQUIRED",
                           f"Confirm it is you before this action: {wanted.get('label', action)}.",
                           details=[{"action": action, "how": "POST /api/v1/auth/step-up",
                                     "send_as": "X-Step-Up ticket on this request"}])
        if claims.get("sid") != self.session.get("id", ""):
            # signed by the same key as a session, so this is the check that stops a
            # ticket minted for another session (a stolen one) from being replayed here
            raise ApiError(423, "STEP_UP_WRONG_SESSION",
                           "That confirmation belongs to a different session. Confirm again.")
        if claims.get("act") != action:
            raise ApiError(423, "STEP_UP_WRONG_ACTION",
                           f"That confirmation was for '{claims.get('act')}', not '{action}'.",
                           details=[{"confirmed_for": claims.get("act"), "needed_for": action}])
        if (claims.get("tgt") or "") != (target or "")[:80]:
            raise ApiError(423, "STEP_UP_WRONG_TARGET",
                           "That confirmation was for a different record. Confirm again.")
        verdict = SPENT.claim(claims["jti"])
        if verdict == "unavailable":
            raise ApiError(503, "STEP_UP_STATE_UNAVAILABLE",
                           "Shared replay store is unreachable — refusing to accept a single-use "
                           "confirmation when cross-instance replay cannot be checked.")
        if verdict != "ok":
            raise ApiError(423, "STEP_UP_REPLAYED",
                           "That confirmation has already been used. Confirm again — each one "
                           "authorises exactly one action.")
        return claims

    def require(self, action: str) -> None:
        if self.mfa_pending and action in self.MFA_SENSITIVE:
            raise ApiError(403, "MFA_REQUIRED",
                           "This workspace requires two-factor authentication: enrol an "
                           "authenticator app before using this action.")
        if not self.can(action):
            raise ApiError(403, "FORBIDDEN_SCOPE", f"Role '{self.role}' lacks '{action}'.")

    # ---- helpers ----
    def audit(self, action: str, target: str = "", meta: dict | None = None) -> None:
        self.store.audit(self.tid, self.user.get("email", self.claims.get("sub", "system")), action, target, meta)

    def body_str(self, key: str, max_len: int = 2000, required: bool = False) -> str:
        v = sec.clean(self.body.get(key, ""), max_len)
        if required and not v:
            raise ApiError(400, "VALIDATION_FAILED", f"'{key}' is required.")
        return v

    def body_int(self, key: str, default: int = 0, lo: int | None = None, hi: int | None = None) -> int:
        try:
            v = int(self.body.get(key, default))
        except Exception:
            raise ApiError(400, "VALIDATION_FAILED", f"'{key}' must be an integer.")
        if lo is not None and v < lo:
            raise ApiError(400, "VALIDATION_FAILED", f"'{key}' must be >= {lo}.")
        if hi is not None and v > hi:
            raise ApiError(400, "VALIDATION_FAILED", f"'{key}' must be <= {hi}.")
        return v

    def qint(self, key: str, default: int, hi: int = 500) -> int:
        try:
            return max(1, min(hi, int(self.query.get(key, [str(default)])[0])))
        except Exception:
            return default


# ==========================================================================
# Serializers
# ==========================================================================
def mask_phone(p: str) -> str:
    digits = re.sub(r"\D", "", p or "")
    if len(digits) < 6:
        return "•••"
    return f"+{digits[:2]} {digits[2:4]}••• ••{digits[-2:]}"


def mask_email(e: str) -> str:
    if "@" not in (e or ""):
        return "•••"
    user, dom = e.split("@", 1)
    return f"{user[:2]}•••@{dom}"


def _parse_hhmm(value: str, default_min: int) -> int:
    try:
        h, m = str(value).split(":")[:2]
        return (int(h) % 24) * 60 + (int(m) % 60)
    except Exception:
        return default_min


def tenant_now(tenant: dict) -> tuple[int, int]:
    """Local (tenant-timezone) hour and minute.

    Compliance windows are legal obligations in the *tenant's* timezone, not the
    server's. A box running in UTC must still refuse a 21:30 IST call, so the
    window is evaluated in `settings.timezone` (default Asia/Kolkata).
    """
    tz_name = ((tenant or {}).get("settings") or {}).get("timezone") or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo

        local = datetime.datetime.now(ZoneInfo(tz_name))
        return local.hour, local.minute
    except Exception:
        lt = time.localtime()
        return lt.tm_hour, lt.tm_min


def in_quiet_hours(tenant: dict) -> tuple[bool, str]:
    """True when the tenant-local clock is inside quiet hours (start-inclusive)."""
    settings = (tenant or {}).get("settings") or {}
    start_raw, end_raw = (settings.get("quiet_hours") or ["22:00", "08:00"])[:2]
    start = _parse_hhmm(start_raw, 22 * 60)
    end = _parse_hhmm(end_raw, 8 * 60)
    hour, minute = tenant_now(tenant)
    mins = hour * 60 + minute
    inside = (mins >= start or mins < end) if start > end else (start <= mins < end)
    return inside, f"{start_raw}–{end_raw} {settings.get('timezone', 'Asia/Kolkata')} (now {hour:02d}:{minute:02d})"


def out_contact(c: dict, ctx: Ctx, full: bool = False) -> dict:
    phone = sec.unseal(c.get("phone_enc", ""), aad=c["tenant_id"])
    email = sec.unseal(c.get("email_enc", ""), aad=c["tenant_id"])
    show = ctx.can("pii:read")
    rec = {
        "id": c["id"],
        "name": c["name"],
        "phone": phone if (show and full) else mask_phone(phone),
        "email": email if (show and full) else mask_email(email),
        "city": c.get("city"),
        "source": c.get("source"),
        "stage": c.get("stage"),
        "score": c.get("score"),
        "intent": c.get("intent"),
        "budget_min": c.get("budget_min"),
        "budget_max": c.get("budget_max"),
        "requirement": c.get("requirement"),
        # the structured requirement (docs/22) — category, types, budget, must-haves
        "requirement_spec": c.get("requirement_spec"),
        "owner_id": c.get("owner_id"),
        "tags": c.get("tags", []),
        "value": c.get("value"),
        "language": c.get("language"),
        "consent": c.get("consent", {}),
        # source history (first touch keeps the attribution) and the speed-to-lead
        # clock, so a list view can show both without a second request
        "sources": c.get("sources", []),
        "first_response_at": c.get("first_response_at"),
        "sla": (lambda st: {"status": st["status"], "due_at": st["due_at"], "remaining_sec": st.get("remaining_sec"),
                            "overdue_sec": st.get("overdue_sec"), "responded_in_sec": st.get("responded_in_sec")})(
            ing.sla_state(c, now())
        ),
        "created_at": c.get("created_at"),
        "last_activity_at": c.get("last_activity_at"),
    }
    rec["sla_status"] = rec["sla"]["status"]
    return rec


def out_agent(a: dict) -> dict:
    return {
        "id": a["id"],
        "name": a["name"],
        "role": a["role"],
        "department": a["department"],
        "model": a["model"],
        "temperature": a.get("temperature"),
        "status": a["status"],
        "channels": a.get("channels", []),
        "scopes": a.get("scopes", []),
        "tools": a.get("tools", []),
        "skills": a.get("skills", []),
        "guardrails": a.get("guardrails", {}),
        "spend_usd": a.get("spend_usd", 0),
        "runs": a.get("runs", 0),
        "success_rate": a.get("success_rate", 0),
    }


# ==========================================================================
# Auth
# ==========================================================================
@route("POST", r"/api/v1/auth/signup", public=True, bucket="auth")
def auth_signup(ctx: Ctx) -> dict:
    if neon.auth_mode() == "neon":
        raise ApiError(
            403,
            "LOCAL_AUTH_DISABLED",
            "Local password accounts are disabled while RE_AUTH_MODE=neon; sign up through Neon Auth.",
        )
    org = ctx.body_str("org_name", 80, required=True)
    name = ctx.body_str("name", 80, required=True)
    email = ctx.body_str("email", 254, required=True).lower()
    password = str(ctx.body.get("password", ""))
    if not sec.valid_email(email):
        raise ApiError(400, "VALIDATION_FAILED", "Enter a valid work email.")
    if len(password) < 10:
        raise ApiError(422, "WEAK_PASSWORD", "Password must be at least 10 characters.")
    if any(u["email"] == email for u in ctx.store.db["users"]):
        raise ApiError(409, "DUPLICATE_ACCOUNT", "That email already has an account.")

    tid = nid("tnt")
    slug = re.sub(r"[^a-z0-9]+", "-", org.lower()).strip("-")[:40] or "tenant"
    ctx.store.db["tenants"].append(
        {
            "id": tid,
            "tenant_id": tid,
            "name": org,
            "slug": slug,
            "plan": "trial",
            "region": "ap-south-1",
            "currency": "INR",
            "created_at": now(),
            "settings": {
                "timezone": "Asia/Kolkata",
                "quiet_hours": ["21:00", "09:00"],
                "ai_disclosure": True,
                "data_region": "India",
                "cross_border_ok": False,
            },
            "features": {
                "ai_voice": True,
                "ai_whatsapp": True,
                "lead_scraper": False,
                "predictive_forecast": True,
                "ads_autopilot": False,
                "social_scheduler": True,
                "public_funnels": True,
                "mcp_gateway": True,
                "autonomous_execution": False,
                "redline_agent": True,
            },
            "kill_switch": False,
        }
    )
    user = ctx.store.insert(
        "users",
        tid,
        {
            "name": name,
            "email": email,
            "role": "owner",
            "password_hash": sec.hash_password(password),
            "active": True,
            "mfa_enabled": False,
            "failed_attempts": 0,
            "last_login": now(),
        },
    )
    ctx.store.audit(tid, email, "tenant.created", tid, {"org": org})
    ctx.store.audit(tid, email, "user.role_assigned", user["id"], {"role": "owner"})
    issued = _start_session(ctx, user, {"id": tid}, amr=["pwd"])
    return {
        **issued,
        "user": {"id": user["id"], "name": name, "email": email, "role": "owner"},
        "tenant": {"id": tid, "name": org, "slug": slug, "plan": "trial"},
    }


@route("POST", r"/api/v1/auth/login", public=True, bucket="auth")
def auth_login(ctx: Ctx) -> dict:
    if neon.auth_mode() == "neon":
        raise ApiError(
            403,
            "LOCAL_AUTH_DISABLED",
            "Local password sign-in is disabled while RE_AUTH_MODE=neon; authenticate via Neon Auth.",
        )
    email = ctx.body_str("email", 254).lower()
    password = str(ctx.body.get("password", ""))[:200]
    users = [u for u in ctx.store.db["users"] if u["email"] == email]

    # Per-account throttle, applied *before* the account lookup so it covers
    # non-existent addresses too (otherwise spraying/harvesting is only bounded by
    # the per-IP bucket). Keyed by a hash: the limiter never stores an email.
    account_key = sec.sha256_hex(email)[:32]
    allowed, _remaining, retry = sec.LIMITER.check("auth_account", account_key)
    if not allowed:
        # No audit row here: writing one per blocked attempt hands an attacker an
        # unbounded write primitive against the append-only log. The IP and the
        # hashed account key are already in the throttler's own counters.
        raise ApiError(429, "RATE_LIMITED", f"Too many attempts for this account. Retry in {retry}s.")

    if len(users) != 1:
        # constant work regardless of account existence
        sec.hash_password(secrets_placeholder())
        raise ApiError(401, "UNAUTHENTICATED", "Email or password is incorrect.")
    u = users[0]

    # Time-based lockout: it must expire on its own, otherwise anyone who knows an
    # email address can deny service to that user indefinitely.
    locked_until = int(u.get("locked_until") or 0)
    if locked_until > now():
        raise ApiError(423, "ACCOUNT_LOCKED", f"Account locked for {locked_until - now()}s after failed attempts.")
    if not u.get("active", True):
        raise ApiError(403, "ACCOUNT_DISABLED", "This user has been deactivated by the owner.")
    pw_hash = str(u.get("password_hash") or u.get("pw") or "")
    if not pw_hash:
        sec.hash_password(secrets_placeholder())
        raise ApiError(401, "UNAUTHENTICATED", "Password login is disabled for this SSO account; sign in with Enterprise SSO.")
    if not sec.verify_password(password, pw_hash):
        failures = u.get("failed_attempts", 0) + 1
        patch = {"failed_attempts": failures}
        if failures >= 6:
            patch["locked_until"] = now() + 900  # 15 minutes, then it clears itself
            patch["failed_attempts"] = 0
        ctx.store.update("users", u["tenant_id"], u["id"], patch)
        ctx.store.audit(u["tenant_id"], email, "auth.login_failed", u["id"], {"ip": ctx.client_ip, "failures": failures})
        raise ApiError(401, "UNAUTHENTICATED", "Email or password is incorrect.")
    # A verified authenticator changes the shape of a login: the password is only
    # half of it. No session is issued here — a short-lived challenge is, which the
    # second step exchanges for one (docs/27).
    if u.get("mfa_enrolled") and u.get("mfa_secret"):
        challenge = sec.issue_token({"sub": u["email"], "uid": u["id"], "tid": u["tenant_id"],
                                     "role": u["role"], "mfa": "pending"}, ttl=MFA_CHALLENGE_TTL)
        ctx.store.audit(u["tenant_id"], email, "auth.mfa_challenge", u["id"], {"ip": ctx.client_ip})
        return {
            "mfa_required": True,
            "challenge": challenge,
            "methods": ["totp", "recovery_code"],
            "expires_in": MFA_CHALLENGE_TTL,
            "user_hint": _mask_email(u["email"]),
        }

    ctx.store.update("users", u["tenant_id"], u["id"],
                     {"failed_attempts": 0, "locked_until": 0, "last_login": now()})
    tenant = next((t for t in ctx.store.db["tenants"] if t["id"] == u["tenant_id"]), {})
    ctx.store.audit(u["tenant_id"], email, "auth.login", u["id"], {"ip": ctx.client_ip, "role": u["role"]})
    issued = _start_session(ctx, u, tenant, amr=["pwd"])
    r_role = u.get("role", "viewer")
    perms = {
        "pii:read": r_role in ("owner", "admin", "manager"),
        "write": r_role in ("owner", "admin", "manager", "agent"),
        "manage_users": r_role in ("owner", "admin"),
        "manage_tenant": r_role in ("owner", "admin"),
        "decide_learning": r_role in ("owner", "admin", "manager"),
        "run_redline": r_role in ("owner", "admin"),
        "kill_switch": r_role == "owner",
    }
    mfa_st = _mfa_state(u, tenant)
    return {
        **issued,
        "user": {
            "id": u["id"],
            "name": u["name"],
            "email": u["email"],
            "role": u["role"],
            "mfa_enabled": u.get("mfa_enabled"),
            "mfa_enrolled": bool(u.get("mfa_enrolled")),
            "mfa": mfa_st,
            "permissions": perms,
        },
        "mfa": mfa_st,
        "tenant": {
            "id": tenant.get("id"),
            "name": tenant.get("name"),
            "slug": tenant.get("slug"),
            "plan": tenant.get("plan"),
            "features": tenant.get("features", {}),
            "settings": tenant.get("settings", {}),
            "kill_switch": tenant.get("kill_switch", False),
        },
    }


def _start_session(ctx: Ctx, user: dict, tenant: dict, *, amr: list[str] | None = None) -> dict:
    """Open a session row and mint the access token that points at it.

    Every route that signs somebody in comes through here — password, MFA second
    step, signup — so the device list cannot disagree with the logins that filled
    it, and so "how long is a session" is answered in exactly one place.
    """
    row, refresh = sess.open_session(ctx.store, user["tenant_id"], user["id"],
                                     ip=ctx.client_ip, ua=ctx.user_agent, amr=amr)
    _stash_refresh(refresh)
    token = sec.issue_token({"sub": user["email"], "uid": user["id"], "tid": user["tenant_id"],
                             "role": user["role"], "sid": row["id"]}, ttl=sess.ACCESS_TTL)
    ctx.store.audit(user["tenant_id"], user["email"], "auth.session_started", row["id"],
                    {"ip": ctx.client_ip, "device": (row.get("device") or {}).get("label"),
                     "amr": amr or ["pwd"]})
    body = {
        "token": token,
        "csrf": sec.csrf_token(token),
        "expires_in": sess.ACCESS_TTL,
        "refresh_expires_in": sess.REFRESH_TTL,
        "session": sess.public(row, row["id"]),
    }
    # The refresh token is delivered as an httpOnly cookie and *not* in the body:
    # a long-lived credential in a JSON response is a credential one XSS away from
    # being exfiltrated. A native or CLI client asks for it explicitly.
    if (ctx.query.get("refresh", [""])[0] or "").lower() == "body":
        body["refresh_token"] = refresh
        body["refresh_warning"] = ("Returned in the body because you asked. Browsers must not: keep it "
                                   "in the platform keystore, never in local storage.")
    return body


# The refresh token travels from the route to the cookie layer through this, not
# through the response body (docs/28). One request per thread, cleared at entry.
_COOKIE_JAR = threading.local()


def take_refresh_cookie() -> str:
    """Hand the server the refresh token for this request, once."""
    token = getattr(_COOKIE_JAR, "refresh", "")
    _COOKIE_JAR.refresh = ""
    return token


def _stash_refresh(token: str) -> None:
    _COOKIE_JAR.refresh = token or ""


def secrets_placeholder() -> str:
    import secrets as _s

    return _s.token_urlsafe(16)


# ==========================================================================
# Usage meters (docs/29)
# ==========================================================================
@route("GET", r"/api/v1/usage")
def usage_report(ctx: Ctx) -> dict:
    """What this workspace has actually used this month, against its plan."""
    period = sec.clean(ctx.query.get("period", [""])[0], 7) or None
    if period and not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ApiError(400, "VALIDATION_FAILED", "period must look like 2026-11.")
    data = ut.report(ctx.store, ctx.tenant, period)
    data["fingerprint"] = ut.fingerprint(ctx.tid, data["period"])
    return data


@route("GET", r"/api/v1/usage/series")
def usage_series(ctx: Ctx) -> dict:
    try:
        periods = max(1, min(24, int(ctx.query.get("periods", ["6"])[0])))
    except (TypeError, ValueError):
        raise ApiError(400, "VALIDATION_FAILED", "periods must be a number.")
    return {"series": ut.series(ctx.store, ctx.tenant, periods), "timezone": ut.tenant_tz(ctx.tenant)}


@route("GET", r"/api/v1/usage/ledger")
def usage_ledger(ctx: Ctx) -> dict:
    """Adjustments and ledger-only meters, newest first — the audit side of a bill."""
    ctx.require("manage_tenant")
    period = sec.clean(ctx.query.get("period", [""])[0], 7) or None
    meter = sec.clean(ctx.query.get("meter", [""])[0], 40)
    rows = ut.ledger(ctx.store, ctx.tid, period, meter)
    return {"entries": rows[:200], "count": len(rows)}


@route("POST", r"/api/v1/usage/close")
def usage_close(ctx: Ctx) -> dict:
    """Freeze a month that has ended, so a later purge cannot shrink the invoice."""
    ctx.require("manage_tenant")
    period = sec.clean(ctx.body.get("period", ""), 7)
    if not re.fullmatch(r"\d{4}-\d{2}", period or ""):
        raise ApiError(400, "VALIDATION_FAILED", "period must look like 2026-11.")
    try:
        snap, already = ut.close(ctx.store, ctx.tenant, period, by=ctx.user.get("email", ""))
    except ValueError as exc:
        raise ApiError(400, "PERIOD_STILL_OPEN", str(exc))
    if not already:
        ctx.audit("usage.period_closed", period, {"meters": snap.get("meters")})
    return {"snapshot": snap, "already_closed": already,
            "invoice_lines": ut.invoice_lines(ctx.store, ctx.tenant, period)}


@route("POST", r"/api/v1/usage/adjust")
def usage_adjust(ctx: Ctx) -> dict:
    """A credit, a correction, or a meter only a ledger can hold.

    Owner-only and audited, because this is the one route that can change a number
    a customer will be invoiced for. It appends; it never edits: a mistaken
    adjustment is corrected by another row, which is why the ledger is readable.
    """
    ctx.require("manage_tenant")
    if ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner can adjust a meter.")
    meter = sec.clean(ctx.body.get("meter", ""), 40)
    if meter not in ut.METERS:
        raise ApiError(400, "VALIDATION_FAILED", f"Unknown meter. One of: {', '.join(ut.METERS)}.")
    try:
        qty = int(ctx.body.get("qty"))
    except (TypeError, ValueError):
        raise ApiError(400, "VALIDATION_FAILED", "qty must be an integer (negative for a credit).")
    if abs(qty) > 10 ** 9:
        raise ApiError(400, "VALIDATION_FAILED", "qty is out of range.")
    reason = sec.clean(ctx.body.get("reason", ""), 200)
    if len(reason) < 4:
        raise ApiError(400, "VALIDATION_FAILED", "A reason is required — an unexplained credit is a leak.")
    source = sec.clean(ctx.body.get("source_id", ""), 60) or f"adj_{nid('adj')}"
    row = ut.record(ctx.store, ctx.tid, meter, qty, source_id=source, reason=reason,
                    period=sec.clean(ctx.body.get("period", ""), 7) or None,
                    tz=ut.tenant_tz(ctx.tenant))
    if row.get("_duplicate"):
        return {"ok": True, "duplicate": True, "entry": row,
                "note": "That source_id had already been recorded; nothing was added twice."}
    ctx.audit("usage.adjusted", meter, {"qty": qty, "reason": reason, "source_id": source})
    return {"ok": True, "duplicate": False, "entry": row, "usage": ut.report(ctx.store, ctx.tenant, row["period"])}


# ==========================================================================
# Two-factor authentication (docs/27)
# ==========================================================================
# The desk has shown `mfa_enabled: true` for owner and admin accounts since the
# first release and never verified a code: a flag, not a factor. Three facts are
# now kept separate, because conflating them is what produced the flag:
#   user.mfa_enrolled        an authenticator has been verified for this account
#   user.mfa_enabled         this account is *asked* to enrol (seeded for owners)
#   tenant.settings.require_mfa  the workspace enforces enrolment on safe-critical
#                                actions — off by default, so a demo still works
MFA_CHALLENGE_TTL = 300          # five minutes to type six digits
MFA_LOCKOUT_SECONDS = 900
MFA_MAX_FAILURES = 5


def _mask_email(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    return f"{name[:2]}•••@{domain}" if domain else "•••"


def _mfa_state(user: dict, tenant: dict) -> dict:
    required = bool((tenant.get("settings") or {}).get("require_mfa"))
    enrolled = bool(user.get("mfa_enrolled"))
    return {
        "enrolled": enrolled,
        "asked": bool(user.get("mfa_enabled")),
        "required": required,
        "pending": required and not enrolled,
        # codes already spent are gone: count what is left, not what was issued
        "recovery_codes_left": sum(1 for c in (user.get("mfa_recovery") or []) if not c.get("used_at")),
        "enrolled_at": user.get("mfa_enrolled_at") or None,
    }


def _mfa_seal(secret: str, tid: str, uid: str) -> str:
    """The seed is a credential: sealed at rest, and bound to the account it belongs to.

    A copied blob pasted onto another user's row will not unseal — the AAD names
    the tenant and the user (docs/27).
    """
    return sec.seal(secret, aad=f"mfa:{tid}:{uid}")


def _mfa_secret(user: dict) -> str:
    blob = user.get("mfa_secret") or ""
    if not blob:
        return ""
    try:
        return sec.unseal(blob, aad=f"mfa:{user['tenant_id']}:{user['id']}")
    except Exception:
        return ""


def _mfa_codes_hashed(codes: list[str]) -> list[dict]:
    """Stored as blind indices: a database dump must not hand over a second factor."""
    return [{"h": sec.blind_token(totpmod.normalise_recovery_code(c)), "used_at": 0} for c in codes]


def _mfa_verify(secret: str, code: str, last_step: int | None = None) -> dict:
    """totp.verify, with the module's own error turned into an API error.

    A row that says `mfa_enrolled` but holds no usable secret is a broken state,
    not a wrong code: say so, and let recovery codes and an admin reset be the way
    out (docs/27 § "What if the phone is lost").
    """
    try:
        return totpmod.verify(secret, code, last_step=last_step)
    except totpmod.TotpError as exc:
        raise ApiError(409, exc.code, exc.message) from exc


def _body_recovery(ctx: Ctx) -> str:
    """The recovery code a request offered, if any — so the lockout can exempt it."""
    return sec.clean(ctx.body.get("recovery_code", "") or "", 20)


def _mfa_check_user(ctx: Ctx, u: dict, *, recovery: str = "") -> None:
    """MFA attempt budget, per account and per IP — tighter than the code space.

    **A recovery code is not subject to the lockout.** The lock exists to stop
    somebody guessing a six-digit code, which they can do at machine speed; a
    recovery code is 52 bits of randomness, so guessing is not the attack. Refusing
    it during a lock would mean anyone who can reach the login form could lock the
    only person who may reset the factor out of *their own* factor — a
    denial-of-service dressed as a security control, and the break-glass key
    breaking exactly when it is needed. Found by a test whose teardown could not
    turn an authenticator off again after the lockout test had tripped the lock
    (`docs/34 §7`).
    """
    key = sec.sha256_hex(u["id"])[:32]
    allowed, _left, retry = sec.LIMITER.check("mfa_account", key)
    if not allowed:
        raise ApiError(429, "RATE_LIMITED", f"Too many codes for this account. Retry in {retry}s.")
    if recovery:
        return
    if int(u.get("mfa_locked_until") or 0) > now():
        raise ApiError(423, "ACCOUNT_LOCKED",
                       f"Two-factor locked for {int(u.get('mfa_locked_until')) - now()}s after failed codes. "
                       "A recovery code still works.")


def _mfa_failed(ctx: Ctx, u: dict, reason: str) -> None:
    failures = int(u.get("mfa_failures") or 0) + 1
    patch: dict = {"mfa_failures": failures}
    if failures >= MFA_MAX_FAILURES:
        patch = {"mfa_failures": 0, "mfa_locked_until": now() + MFA_LOCKOUT_SECONDS}
    ctx.store.update("users", u["tenant_id"], u["id"], patch)
    ctx.store.audit(u["tenant_id"], u.get("email", ""), "auth.mfa_failed", u["id"],
                    {"reason": reason, "failures": failures, "ip": ctx.client_ip})


def _mfa_success(ctx: Ctx, u: dict, step: int | None, method: str) -> None:
    patch: dict = {"mfa_failures": 0, "mfa_locked_until": 0, "mfa_last_used": now()}
    if step is not None:
        patch["mfa_last_step"] = int(step)   # a code is single-use
    ctx.store.update("users", u["tenant_id"], u["id"], patch)
    ctx.store.audit(u["tenant_id"], u.get("email", ""), "auth.mfa_verified", u["id"],
                    {"method": method, "ip": ctx.client_ip})


@route("POST", r"/api/v1/auth/login/mfa", public=True, bucket="mfa")
def auth_login_mfa(ctx: Ctx) -> dict:
    """Exchange a challenge + a code for a session — the second half of the login."""
    challenge = str(ctx.body.get("challenge", ""))[:4096]
    claims = sec.verify_token(challenge)
    if not claims or claims.get("mfa") != "pending":
        raise ApiError(401, "MFA_CHALLENGE_INVALID", "That challenge has expired. Sign in again.")
    u = next((x for x in ctx.store.db["users"]
              if x["id"] == claims.get("uid") and x["tenant_id"] == claims.get("tid")), None)
    if not u or not u.get("active", True):
        raise ApiError(401, "UNAUTHENTICATED", "Session no longer valid.")
    _mfa_check_user(ctx, u, recovery=_body_recovery(ctx))

    code = str(ctx.body.get("code", ""))[:20]
    recovery = str(ctx.body.get("recovery_code", ""))[:20]
    step: int | None = None
    method = "totp"

    if recovery:
        method = "recovery_code"
        target = sec.blind_token(totpmod.normalise_recovery_code(recovery))
        codes = list(u.get("mfa_recovery") or [])
        hit = next((c for c in codes if sec.hmac.compare_digest(c.get("h", ""), target) and not c.get("used_at")), None)
        if not hit:
            _mfa_failed(ctx, u, "recovery_code_invalid")
            raise ApiError(401, "MFA_CODE_INVALID", "That recovery code is not valid, or has been used.")
        hit["used_at"] = now()
        ctx.store.update("users", u["tenant_id"], u["id"], {"mfa_recovery": codes})
    else:
        verdict = _mfa_verify(_mfa_secret(u), code,
                                 last_step=int(u["mfa_last_step"]) if u.get("mfa_last_step") else None)
        if not verdict["ok"]:
            _mfa_failed(ctx, u, verdict["reason"])
            raise ApiError(401, "MFA_CODE_INVALID",
                           "That code is not valid." if verdict["reason"] == "code_incorrect"
                           else "That code has already been used. Wait for the next one.")
        step = verdict["step"]

    _mfa_success(ctx, u, step, method)
    tenant = next((t for t in ctx.store.db["tenants"] if t["id"] == u["tenant_id"]), {})
    ctx.store.update("users", u["tenant_id"], u["id"], {"last_login": now()})
    ctx.store.audit(u["tenant_id"], u["email"], "auth.login", u["id"],
                    {"ip": ctx.client_ip, "role": u["role"], "mfa": method})
    issued = _start_session(ctx, u, tenant, amr=["pwd", method])
    return {
        **issued,
        "user": {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"],
                 "mfa_enabled": u.get("mfa_enabled"), "mfa_enrolled": True},
        "mfa": _mfa_state({**u, "mfa_enrolled": True}, tenant),
        "tenant": {"id": tenant.get("id"), "name": tenant.get("name"),
                   "slug": tenant.get("slug"), "plan": tenant.get("plan")},
    }


@route("POST", r"/api/v1/auth/step-up", bucket="auth_account")
def auth_step_up(ctx: Ctx) -> dict:
    """Prove it is you *now*, and get a ticket for one dangerous action.

    The gate this feeds is `Ctx.require_step_up`: a session says who you are for
    eight hours, a ticket says you satisfied the factor for this action, for five
    minutes, once (`docs/34`).

    Throttled on the same per-account bucket as login, because a prompt that takes
    a password is a place to guess one.
    """
    sid = ctx.session.get("id", "")
    if not sid:
        raise ApiError(401, "SESSION_REQUIRED", "Sign in again.")
    wait = stepup.ATTEMPTS.blocked(sid)
    if wait:
        raise ApiError(429, "RATE_LIMITED",
                       f"Too many failed confirmations. Try again in {wait}s.")
    action = sec.clean(ctx.body.get("action", ""), 40)
    if action not in stepup.ACTIONS:
        raise ApiError(400, "VALIDATION_FAILED",
                       f"Unknown action. One of: {', '.join(sorted(stepup.ACTIONS))}.")
    # A target-scoped action (deleting *this* user) must be confirmed against that
    # record, not held open for any record of the same kind.
    target = sec.clean(ctx.body.get("target", ""), 80)
    password = str(ctx.body.get("password", ""))[:200]
    code = sec.clean(ctx.body.get("code", ""), 20)
    recovery = sec.clean(ctx.body.get("recovery_code", ""), 20)

    verdict = stepup.verify_factor(ctx.user, password, code, recovery)
    if not verdict["ok"]:
        left = stepup.ATTEMPTS.fail(sid)
        # which part failed is not said: "password incorrect" and "code incorrect"
        # together would let an attacker split the two factors
        # target, not action, in the target column: an audit row whose target says
        # "kill_switch" is a row an investigator has to guess at ("which switch?")
        ctx.store.audit(ctx.tid, ctx.user.get("email", ""), "auth.step_up_failed",
                        target or action,
                        {"action": action, "ip": ctx.client_ip, "reason": verdict["reason"]})
        if verdict["reason"] == "factor_required":
            raise ApiError(400, "FACTOR_REQUIRED",
                           "This account has a second factor: send `code` or `recovery_code` too.")
        raise ApiError(401, "STEP_UP_INVALID" if left else "STEP_UP_BLOCKED",
                       "That confirmation was not accepted." if left else
                       "Too many failed confirmations. Try again in a few minutes.")

    # A consumed recovery code stays consumed (docs/27): this path may not launder
    # a one-time credential into a reusable one.
    if verdict.get("consume_recovery"):
        hit = verdict["consume_recovery"]
        hit["used_at"] = now()
        codes = verdict["recovery_list"]
        ctx.store.update("users", ctx.tid, ctx.user["id"], {"mfa_recovery": codes})
        ctx.store.audit(ctx.tid, ctx.user.get("email", ""), "auth.recovery_code_used",
                        ctx.user["id"], {"ip": ctx.client_ip, "where": "step_up"})
    elif verdict.get("step"):
        ctx.store.update("users", ctx.tid, ctx.user["id"], {"mfa_last_step": verdict["step"]})

    if ctx.proof_run:
        for coll, tid, rid in stamped:
            if coll in pf.PURGEABLE:
                ctx.store.update(coll, tid, rid, {"proof_run": ctx.proof_run})

    stepup.ATTEMPTS.clear(sid)
    ttl = stepup.tickets_for_ttl(stepup.needs_step_up(ctx.user))
    ticket = stepup.ticket_for(sid, action, target, ttl=ttl)
    ctx.store.audit(ctx.tid, ctx.user.get("email", ""), "auth.step_up", target or action,
                    {"action": action, "method": verdict["method"], "ttl": ttl,
                     "ip": ctx.client_ip})
    return {
        "ok": True,
        "ticket": ticket,
        "action": action,
        "target": target,
        "method": verdict["method"],
        "expires_in": ttl,
        "single_use": True,
        "send_as": "X-Step-Up header on the request that performs the action",
        "note": ("password only — this account has no second factor, so the ticket is the "
                 "weaker kind and lasts " f"{ttl}s (docs/34 §2)"
                 if not stepup.needs_step_up(ctx.user) else
                 f"password + {verdict['method']}; bound to this action and this session"),
    }


@route("GET", r"/api/v1/security/step-up")
def security_step_up(ctx: Ctx) -> dict:
    """Which actions need a fresh confirmation, and what a ticket is worth here.

    Owner/admin, like every other posture view. It states the weaker case as the
    weaker case: an account with no second factor gets a shorter, password-only
    ticket and the panel says so rather than implying everybody has a factor.
    """
    ctx.require("manage_tenant")
    state = stepup.describe(ctx.user)
    recent_raw = [r for r in ctx.store.all("audit", ctx.tid)
                  if r.get("action") in ("auth.step_up", "auth.step_up_failed")][-10:]
    recent, _ = aud_ret.present_entries(recent_raw, ctx.tenant)
    return {
        "step_up": state,
        "this_session": {
            "id": ctx.session.get("id", ""),
            "amr": ctx.session.get("amr", []),
            "attempts_blocked_for": stepup.ATTEMPTS.blocked(ctx.session.get("id", "")),
        },
        "recent": [{"action": (r.get("meta") or {}).get("action") or r.get("target", ""),
                    "target": r.get("target", ""), "event": r.get("action"),
                    "at": r.get("at"), "actor": r.get("actor")} for r in reversed(recent)],
        "not_protected_by_this": [
            "reading data — a step-up gate on a read is a prompt nobody reads",
            "ordinary writes (a note, a stage change) — the session covers the day's work",
            "anything a token can do without a person present",
        ],
    }


@route("GET", r"/api/v1/auth/mfa")
def auth_mfa_status(ctx: Ctx) -> dict:
    return {"mfa": _mfa_state(ctx.user, ctx.tenant),
            "issuer": totpmod.ISSUER, "digits": totpmod.DIGITS,
            "period": totpmod.STEP_SECONDS, "window": totpmod.WINDOW}


@route("POST", r"/api/v1/auth/mfa/enrol")
def auth_mfa_enrol(ctx: Ctx) -> dict:
    """Start enrolment: a secret, an otpauth URI, and ten recovery codes.

    Nothing is enabled yet. A secret that is enabled the moment it is generated is
    the classic way people lock themselves out — the phone was never scanned, the
    next login demands a code nobody can produce. `mfa_enrolled` flips only when a
    code from this secret has been verified.
    """
    if ctx.user.get("mfa_enrolled"):
        raise ApiError(409, "MFA_ALREADY_ENROLLED",
                       "This account already has an authenticator. Disable it first to re-enrol.")
    # Re-opening the dialog before scanning is common (closed the tab, lost the QR).
    # Replacing a *pending* secret is fine; doing it silently is not, because the
    # first QR becomes a trap — say so in the answer.
    replaced = bool(ctx.user.get("mfa_secret"))
    secret = totpmod.new_secret()
    codes = totpmod.new_recovery_codes()
    ctx.store.update("users", ctx.tid, ctx.user["id"], {
        "mfa_secret": _mfa_seal(secret, ctx.tid, ctx.user["id"]),
        "mfa_recovery": _mfa_codes_hashed(codes),   # hashes; the plaintext goes to the user once
        "mfa_started_at": now(),
        "mfa_enrolled": False,
    })
    ctx.audit("auth.mfa_enrol_started", ctx.user["id"])
    return {
        "secret": secret,
        "otpauth_uri": totpmod.provisioning_uri(secret, ctx.user["email"]),
        "recovery_codes": codes,
        "replaced_pending": replaced,
        "warning": ("The previous QR code is now void — only this secret will work."
                    if replaced else ""),
        "next": "POST /api/v1/auth/mfa/verify with one code from the app to finish",
        "note": "The secret and the recovery codes are shown once. Store the codes offline.",
    }


@route("POST", r"/api/v1/auth/mfa/verify")
def auth_mfa_verify(ctx: Ctx) -> dict:
    """Finish enrolment (or re-confirm) with a code from the authenticator."""
    if ctx.user.get("mfa_enrolled"):
        raise ApiError(409, "MFA_ALREADY_ENROLLED", "Already enrolled. Disable first to re-enrol.")
    secret = _mfa_secret(ctx.user)
    if not secret:
        raise ApiError(400, "MFA_NOT_STARTED", "Call POST /auth/mfa/enrol first.")
    _mfa_check_user(ctx, ctx.user, recovery=_body_recovery(ctx))
    verdict = _mfa_verify(secret, str(ctx.body.get("code", ""))[:10])
    if not verdict["ok"]:
        _mfa_failed(ctx, ctx.user, verdict["reason"])
        raise ApiError(400, "MFA_CODE_INVALID", "That code is not valid — check the phone's clock.")
    at = now()
    ctx.store.update("users", ctx.tid, ctx.user["id"], {
        "mfa_enrolled": True, "mfa_enabled": True, "mfa_enrolled_at": at,
        "mfa_last_step": int(verdict["step"]), "mfa_failures": 0, "mfa_locked_until": 0,
    })
    ctx.audit("auth.mfa_enrolled", ctx.user["id"])
    return {"mfa": _mfa_state({**ctx.user, "mfa_enrolled": True, "mfa_enrolled_at": at}, ctx.tenant),
            "code_spent": int(verdict["step"]),
            "note": ("Enrolled. The code you just used is spent — the next sign-in needs the "
                     "next window (up to 30 s), and that is the point of single-use codes.")}


@route("POST", r"/api/v1/auth/mfa/disable")
def auth_mfa_disable(ctx: Ctx) -> dict:
    """Turning off a second factor needs the password *and* a factor.

    A stolen session should not be able to remove the thing that protects the
    account next time.
    """
    password = str(ctx.body.get("password", ""))[:200]
    if not sec.verify_password(password, ctx.user["password_hash"]):
        raise ApiError(401, "UNAUTHENTICATED", "Password is incorrect.")
    if not ctx.user.get("mfa_enrolled"):
        raise ApiError(400, "MFA_NOT_ENROLLED", "There is no authenticator to remove.")
    _mfa_check_user(ctx, ctx.user, recovery=_body_recovery(ctx))
    code = str(ctx.body.get("code", ""))[:10]
    recovery = str(ctx.body.get("recovery_code", ""))[:20]
    if recovery:
        target = sec.blind_token(totpmod.normalise_recovery_code(recovery))
        ok = any(sec.hmac.compare_digest(c.get("h", ""), target) and not c.get("used_at")
                 for c in (ctx.user.get("mfa_recovery") or []))
    else:
        ok = _mfa_verify(_mfa_secret(ctx.user), code,
                            last_step=int(ctx.user["mfa_last_step"]) if ctx.user.get("mfa_last_step") else None)["ok"]
    if not ok:
        _mfa_failed(ctx, ctx.user, "disable_code_invalid")
        raise ApiError(401, "MFA_CODE_INVALID", "That code is not valid.")
    ctx.store.update("users", ctx.tid, ctx.user["id"], {
        "mfa_enrolled": False, "mfa_secret": "", "mfa_recovery": [],
        "mfa_last_step": 0, "mfa_enrolled_at": 0, "mfa_failures": 0, "mfa_locked_until": 0,
    })
    ctx.audit("auth.mfa_disabled", ctx.user["id"])
    return {"mfa": _mfa_state({**ctx.user, "mfa_enrolled": False}, ctx.tenant),
            "note": "The authenticator was removed. Recovery codes are void."}


@route("POST", r"/api/v1/auth/mfa/recovery-codes")
def auth_mfa_regenerate_recovery(ctx: Ctx) -> dict:
    """Ten fresh recovery codes; the previous ten stop working immediately.

    Guarded by the password as well as a code: regenerating is exactly what an
    attacker with a stolen session would want to do to keep access after the
    owner notices, and exactly what a user does after losing the printout.
    """
    if not ctx.user.get("mfa_enrolled"):
        raise ApiError(400, "MFA_NOT_ENROLLED", "Enrol an authenticator first.")
    if not sec.verify_password(str(ctx.body.get("password", ""))[:200], ctx.user["password_hash"]):
        raise ApiError(401, "UNAUTHENTICATED", "Password is incorrect.")
    _mfa_check_user(ctx, ctx.user, recovery=_body_recovery(ctx))
    codes = totpmod.new_recovery_codes()
    recovery = str(ctx.body.get("recovery_code", ""))[:20]
    if recovery:
        # Losing the phone is exactly when new codes are wanted, so an unused
        # recovery code is a valid second factor here — and is consumed like one.
        target = sec.blind_token(totpmod.normalise_recovery_code(recovery))
        kept = list(ctx.user.get("mfa_recovery") or [])
        hit = next((c for c in kept if sec.hmac.compare_digest(c.get("h", ""), target) and not c.get("used_at")), None)
        if not hit:
            _mfa_failed(ctx, ctx.user, "recovery_code_invalid")
            raise ApiError(401, "MFA_CODE_INVALID", "That recovery code is not valid, or has been used.")
        hit["used_at"] = now()
        ctx.store.update("users", ctx.tid, ctx.user["id"], {"mfa_recovery": _mfa_codes_hashed(codes)})
    else:
        verdict = _mfa_verify(_mfa_secret(ctx.user), str(ctx.body.get("code", ""))[:10],
                              last_step=int(ctx.user["mfa_last_step"]) if ctx.user.get("mfa_last_step") else None)
        if not verdict["ok"]:
            _mfa_failed(ctx, ctx.user, verdict["reason"])
            raise ApiError(401, "MFA_CODE_INVALID", "That code has not been accepted.")
        ctx.store.update("users", ctx.tid, ctx.user["id"],
                         {"mfa_recovery": _mfa_codes_hashed(codes), "mfa_last_step": int(verdict["step"])})
    ctx.audit("auth.mfa_recovery_regenerated", ctx.user["id"])
    return {"recovery_codes": codes, "replaced": 10,
            "note": "The previous codes are void. Store these offline."}


# ==========================================================================
# Sessions: refresh, device list, revocation (docs/28)
# ==========================================================================
@route("POST", r"/api/v1/auth/refresh", public=True, bucket="auth")
def auth_refresh(ctx: Ctx) -> dict:
    """Trade a refresh token for a new access token — and a new refresh token.

    The refresh token is read from the httpOnly cookie, or from `X-Refresh-Token`
    for a client that has no cookie jar. It is single-use: this call rotates it,
    and presenting the old one afterwards revokes the session (see below).
    """
    token = ""
    for part in str(ctx.headers.get("cookie", "")).split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k == sess.REFRESH_COOKIE:
                token = v
    token = token or str(ctx.headers.get("x-refresh-token", ""))[:200]

    row = sess.by_refresh(ctx.store, token)
    if row is None:
        # Not a live token. Is it one we already rotated away? Then two parties
        # hold the same credential, and the safe move is to end the session.
        dead = sess.reuse_of(ctx.store, token)
        if dead:
            sess.revoke(ctx.store, dead, "refresh_reuse")
            ctx.store.audit(dead["tenant_id"], "", "auth.refresh_reuse", dead["id"],
                            {"ip": ctx.client_ip, "device": (dead.get("device") or {}).get("label")})
            raise ApiError(401, "REFRESH_REUSED",
                           "This session was ended: a refresh token was used twice. Sign in again.")
        raise ApiError(401, "REFRESH_INVALID", "Sign in again.")
    if not sess.alive(row):
        raise ApiError(401, "SESSION_EXPIRED", "That session has ended. Sign in again.")

    user = ctx.store.get("users", row["tenant_id"], row["user_id"])
    if not user or not user.get("active", True):
        sess.revoke(ctx.store, row, "user_inactive")
        raise ApiError(401, "SESSION_EXPIRED", "That account is no longer active.")

    try:
        row, new_token = sess.rotate(ctx.store, row, ip=ctx.client_ip, ua=ctx.user_agent)
    except sess.RefreshConflict:
        # Another request rotated or ended this token first (possibly on another
        # instance). Same rule as a replay: end the session rather than guess.
        sess.revoke(ctx.store, row, "refresh_reuse")
        ctx.store.audit(row["tenant_id"], "", "auth.refresh_reuse", row["id"],
                        {"ip": ctx.client_ip, "device": (row.get("device") or {}).get("label"),
                         "concurrent": True})
        raise ApiError(401, "REFRESH_REUSED",
                       "This session was ended: a refresh token was used twice. Sign in again.")
    _stash_refresh(new_token)
    token_now = sec.issue_token({"sub": user["email"], "uid": user["id"], "tid": row["tenant_id"],
                                 "role": user["role"], "sid": row["id"]}, ttl=sess.ACCESS_TTL)
    body = {
        "token": token_now,
        "csrf": sec.csrf_token(token_now),
        "expires_in": sess.ACCESS_TTL,
        "refresh_expires_in": max(0, int(row.get("expires_at") or 0) - now()),
        "session": sess.public(row, row["id"]),
        "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]},
    }
    if (ctx.query.get("refresh", [""])[0] or "").lower() == "body":
        body["refresh_token"] = new_token
    return body


@route("GET", r"/api/v1/auth/sessions")
def auth_sessions_list(ctx: Ctx) -> dict:
    """Where am I signed in? One row per device, this one marked."""
    sess.refresh_tenant(ctx.store, ctx.tid)
    rows = [r for r in ctx.store.db["sessions"]
            if r.get("tenant_id") == ctx.tid and r.get("user_id") == ctx.user["id"]]
    live = [r for r in rows if sess.alive(r)]
    live.sort(key=lambda r: (r["id"] != ctx.session.get("id"), -(r.get("last_seen") or 0)))
    return {
        "sessions": [sess.public(r, ctx.session.get("id", "")) for r in live],
        "count": len(live),
        "access_ttl": sess.ACCESS_TTL,
        "refresh_ttl": sess.REFRESH_TTL,
    }


@route("DELETE", r"/api/v1/auth/sessions/(?P<sid>[\w]+)")
def auth_session_revoke(ctx: Ctx) -> dict:
    """End one device. Works on the very next request, not on the next login."""
    sid = ctx.params["sid"]
    sess.refresh_tenant(ctx.store, ctx.tid)
    row = next((r for r in ctx.store.db["sessions"]
                if r["id"] == sid and r.get("tenant_id") == ctx.tid), None)
    # Another user's session is not "forbidden", it does not exist: a 403 would
    # confirm the id, and session ids are not a thing to enumerate.
    if not row or row.get("user_id") != ctx.user["id"]:
        raise ApiError(404, "NOT_FOUND", "No such session.")
    already = bool(row.get("revoked_at"))
    if not already:
        row = sess.revoke(ctx.store, row, "user_revoked", by=ctx.user["id"])
        ctx.audit("auth.session_revoked", sid, {"device": (row.get("device") or {}).get("label"),
                                                "self": sid == ctx.session.get("id", "")})
    return {"ok": True, "session": sess.public(row, ctx.session.get("id", "")), "already_ended": already}


@route("POST", r"/api/v1/auth/sessions/revoke-others")
def auth_sessions_revoke_others(ctx: Ctx) -> dict:
    """The button you press when the laptop is gone: keep this one, end the rest."""
    killed = sess.revoke_all(ctx.store, ctx.tid, ctx.user["id"], "user_revoked_others",
                             keep_sid=ctx.session.get("id", ""), by=ctx.user["id"])
    ctx.audit("auth.sessions_revoked_others", ctx.user["id"], {"count": killed})
    return {"ok": True, "revoked": killed}


@route("POST", r"/api/v1/demo/sessions/sweep")
def demo_sessions_sweep(ctx: Ctx) -> dict:
    """Delete session rows that can never be used again.

    A login is a row, so a demo that runs fifty proof scripts has fifty rows. Only
    rows that are *ended* — revoked, or past their refresh expiry — are removed;
    a live session is somebody's browser and is not a demo's to tidy.
    """
    ctx.require("manage_tenant")
    removed = 0
    live = 0
    sess.refresh_tenant(ctx.store, ctx.tid)
    for row in list(ctx.store.db["sessions"]):
        if row.get("tenant_id") != ctx.tid:
            continue
        if sess.alive(row):
            live += 1
            continue
        ctx.store.remove("sessions", ctx.tid, row["id"])
        removed += 1
    ctx.audit("demo.sessions_swept", ctx.tid, {"removed": removed, "live": live})
    return {"ok": True, "removed": removed, "live": live}


@route("POST", r"/api/v1/auth/logout")
def auth_logout(ctx: Ctx) -> dict:
    """Signing out ends the session row — every token issued from it dies with it."""
    row = ctx.session or {}
    neon_revoked = False
    if row.get("source") == "neon-auth":
        neon_revoked = neon.auth_sign_out(ctx.raw_token)
    elif row:
        sess.revoke(ctx.store, row, "logout", by=ctx.user.get("id", ""))
    sec.revoke(ctx.raw_token)
    ctx.audit("auth.logout", ctx.user.get("id", ""), {"session": row.get("id", ""), "source": row.get("source", "local")})
    return {"ok": True, "session_ended": bool(row) or neon_revoked}


@route("GET", r"/api/v1/auth/me")
def auth_me(ctx: Ctx) -> dict:
    t = ctx.tenant
    return {
        "user": {
            "id": ctx.user["id"],
            "name": ctx.user["name"],
            "email": ctx.user["email"],
            "role": ctx.user["role"],
            "mfa_enabled": bool(ctx.user.get("mfa_enrolled")),
            "mfa": _mfa_state(ctx.user, ctx.tenant),
            "permissions": {
                a: ctx.can(a)
                for a in ("pii:read", "write", "manage_users", "manage_tenant", "decide_learning", "run_redline", "kill_switch")
            },
        },
        "tenant": {
            "id": t.get("id"),
            "name": t.get("name"),
            "slug": t.get("slug"),
            "plan": t.get("plan"),
            "features": t.get("features", {}),
            "settings": t.get("settings", {}),
            "kill_switch": t.get("kill_switch", False),
        },
    }


@route("GET", r"/api/v1/auth/session")
def auth_session(ctx: Ctx) -> dict:
    """CSRF token for an already-authenticated browser.

    The token is an HMAC of the session token, so it could only be derived by
    the server — but the console keeps it in memory and loses it on reload, which
    left a bookmarked page unable to POST anything. Returning it on a GET is the
    standard fix (Django's ensure_csrf_cookie does the same): a cross-origin page
    cannot read this JSON because of the same-origin policy, which is exactly the
    property CSRF relies on. The cookie stays HttpOnly and SameSite.
    """
    token = ctx.raw_token
    if not token:
        raise ApiError(401, "UNAUTHENTICATED", "Sign in first.")
    return {
        "csrf": sec.csrf_token(token),
        "user": {"id": ctx.user["id"], "name": ctx.user["name"], "email": ctx.user["email"],
                 "role": ctx.user["role"], "mfa_enrolled": bool(ctx.user.get("mfa_enrolled"))},
        "mfa": _mfa_state(ctx.user, ctx.tenant),
        "permissions": {a: ctx.can(a) for a in ("write", "pii:read", "manage_tenant", "manage_users")},
        "tenant": {"id": ctx.tenant.get("id"), "name": ctx.tenant.get("name"), "slug": ctx.tenant.get("slug")},
    }


# ==========================================================================
# Tenant / owner controls
# ==========================================================================
@route("GET", r"/api/v1/tenant")
def tenant_get(ctx: Ctx) -> dict:
    t = ctx.tenant
    return {
        "id": t["id"],
        "name": t.get("name"),
        "slug": t.get("slug"),
        "plan": t.get("plan"),
        "settings": t.get("settings", {}),
        "features": t.get("features", {}),
        "kill_switch": t.get("kill_switch", False),
        "region": t.get("region"),
        "created_at": t.get("created_at"),
        # measured, not narrated: these four numbers used to be two literals and a
        # seeded counter here (docs/29)
        "usage": ut.summary_for_tenant_row(ctx.store, ctx.tenant),
    }


@route("PATCH", r"/api/v1/tenant")
def tenant_patch(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    allowed = {"timezone", "quiet_hours", "ai_disclosure", "data_region", "cross_border_ok",
               "call_window", "dlt_series",
               # a workspace may demand an authenticator before it will accept
               # changes to who exists, what it is, or where money goes (docs/27)
               "require_mfa",
               # personal-data retention window for emails/IPs in audit presentations (docs/35)
               "audit_pii_retention_days"}
    patch = {k: v for k, v in sec.pick(ctx.body.get("settings", {}), allowed).items()}
    if "audit_pii_retention_days" in patch:
        try:
            patch["audit_pii_retention_days"] = aud_ret.validate_days(patch["audit_pii_retention_days"])
        except ValueError as exc:
            raise ApiError(400, "VALIDATION_FAILED", str(exc))
    t = ctx.tenant
    settings = {**t.get("settings", {}), **patch}
    if settings.get("ai_disclosure") is False:
        raise ApiError(451, "COMPLIANCE_BLOCKED", "AI disclosure cannot be disabled — it is a hard regulatory gate.")
    ctx.store.db["tenants"] = [
        ({**x, "settings": settings, "name": sec.clean(ctx.body["name"], 80)} if x["id"] == ctx.tid and ctx.body.get("name") else {**x, "settings": settings} if x["id"] == ctx.tid else x)
        for x in ctx.store.db["tenants"]
    ]
    ctx.store.save()
    ctx.audit("settings.updated", ctx.tid, {"changed": list(patch)})
    # Answer with the row as written, not with the request's pre-patch snapshot:
    # a settings screen that echoes the old quiet hours after a successful save is
    # how an operator concludes the save failed and does it twice.
    ctx.tenant = ctx.store.get("tenants", ctx.tid, ctx.tid) or ctx.tenant
    return tenant_get(ctx)


@route("PATCH", r"/api/v1/tenant/features")
def tenant_features(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    patch = sec.pick(ctx.body, [
        "ai_voice", "ai_whatsapp", "lead_scraper", "predictive_forecast", "ads_autopilot",
        "social_scheduler", "public_funnels", "mcp_gateway", "autonomous_execution", "redline_agent",
    ])
    if patch.get("autonomous_execution") and ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only the owner can enable autonomous execution.")
    for t in ctx.store.db["tenants"]:
        if t["id"] == ctx.tid:
            t["features"] = {**t.get("features", {}), **{k: bool(v) for k, v in patch.items()}}
    ctx.store.save()
    ctx.audit("features.updated", ctx.tid, patch)
    return {"features": next(t for t in ctx.store.db["tenants"] if t["id"] == ctx.tid)["features"]}


@route("POST", r"/api/v1/tenant/kill-switch")
def tenant_kill(ctx: Ctx) -> dict:
    ctx.require("kill_switch")
    engage = bool(ctx.body.get("engage", True))
    # Target is the state being asked for, not the tenant: a confirmation to *stop*
    # the agents is not a confirmation to start them again (docs/34 §3).
    ctx.require_step_up("kill_switch", "engage" if engage else "release")
    for t in ctx.store.db["tenants"]:
        if t["id"] == ctx.tid:
            t["kill_switch"] = engage
    ctx.store.save()
    ctx.audit("kill_switch.toggled", ctx.tid, {"engage": engage})
    return {"kill_switch": engage}


@route("GET", r"/api/v1/tenant/users")
def tenant_users(ctx: Ctx) -> dict:
    # The user directory carries colleague emails, roles, MFA state and login
    # times — an org chart with a target list attached. It was readable by any
    # session, including a viewer (SEN-008). Owner/admin only; the team page in
    # the UI already renders a restricted view for everyone else.
    ctx.require("manage_tenant")
    rows = ctx.store.all("users", ctx.tid)
    return {
        "users": [
            {
                "id": u["id"],
                "name": u["name"],
                "email": u["email"],
                "role": u["role"],
                "active": u.get("active", True),
                "mfa_enabled": bool(u.get("mfa_enrolled")),   # what the column claims
                "mfa_enrolled": bool(u.get("mfa_enrolled")),
                "mfa_asked": bool(u.get("mfa_enabled")),
                "last_login": u.get("last_login"),
                "created_at": u.get("created_at"),
            }
            for u in rows
        ]
    }


@route("POST", r"/api/v1/tenant/users")
def tenant_invite(ctx: Ctx) -> dict:
    ctx.require("manage_users")
    email = ctx.body_str("email", 254, required=True).lower()
    role = ctx.body_str("role", 20) or "agent"
    name = ctx.body_str("name", 80) or email.split("@")[0]
    # `channel_partner` is deliberately not invit-able here: a partner is created
    # through /cp/partners so it carries a firm, a commission rate and a
    # protection window. A bare partner seat with none of those is a bug waiting
    # to happen at payout time.
    if role not in ("admin", "manager", "agent", "viewer"):
        raise ApiError(400, "VALIDATION_FAILED", "Role must be admin, manager, agent or viewer.")
    if not sec.valid_email(email):
        raise ApiError(400, "VALIDATION_FAILED", "Invalid email.")
    if any(u["email"] == email for u in ctx.store.db["users"]):
        raise ApiError(409, "DUPLICATE_ACCOUNT", "User already exists.")
    temp = ctx.body_str("password", 100) or "Welcome@2027!"
    u = ctx.store.insert(
        "users",
        ctx.tid,
        {
            "name": name,
            "email": email,
            "role": role,
            "password_hash": sec.hash_password(temp),
            "active": True,
            "mfa_enabled": False,
            "failed_attempts": 0,
            "invited_by": ctx.user["id"],
        },
        prefix="usr",
    )
    ctx.audit("user.invited", u["id"], {"email": email, "role": role})
    return {"user": {"id": u["id"], "email": email, "role": role, "temp_password": temp if not ctx.body.get("password") else None}}


@route("PATCH", r"/api/v1/tenant/users/(?P<uid>[\w]+)")
def tenant_user_patch(ctx: Ctx) -> dict:
    ctx.require("manage_users")
    uid = ctx.params["uid"]
    # Four different dangerous things arrive on this one route, and each gets its
    # own confirmation targeted at *this* user id — so a ticket to change a role is
    # not a ticket to delete, and a ticket for one colleague is not a ticket for
    # another (docs/34 §3). Ungated: a rename, a phone number, an avatar.
    if ctx.body.get("mfa_reset") is True:
        ctx.require_step_up("mfa_reset", uid)
    if "role" in ctx.body:
        ctx.require_step_up("role_change", uid)
    if "active" in ctx.body and not ctx.body.get("active"):
        ctx.require_step_up("user_delete", uid)
    target = ctx.store.get("users", ctx.tid, uid)
    if not target:
        raise ApiError(404, "NOT_FOUND", "User not found in this workspace.")
    if target["role"] == "owner" and ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner can modify the owner account.")
    patch = {}
    if "role" in ctx.body:
        role = sec.clean(ctx.body["role"], 20)
        if role not in ROLE_RANK:
            raise ApiError(400, "VALIDATION_FAILED", "Unknown role.")
        if (role == "channel_partner") != (target.get("role") == "channel_partner"):
            raise ApiError(400, "VALIDATION_FAILED",
                           "A seat cannot be converted into or out of a channel-partner account; use /cp/partners.")
        if role == "owner" and ctx.role != "owner":
            raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner can grant ownership.")
        patch["role"] = role
    if "active" in ctx.body:
        patch["active"] = bool(ctx.body["active"])
    # NOT here: `mfa_enabled` used to be a writable boolean on this route, which
    # let an admin (or a user patching their own row) assert that an account had a
    # second factor when it had none — a compliance claim with nothing behind it.
    # Enrolment is now the only way `mfa_enrolled` becomes true (docs/27), and the
    # one administrative action that touches it is a deliberate break-glass reset.
    if ctx.body.get("mfa_reset") is True:
        if ctx.role != "owner":
            raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner can reset another account's second factor.")
        patch.update({"mfa_enrolled": False, "mfa_secret": "", "mfa_recovery": [],
                      "mfa_last_step": 0, "mfa_enrolled_at": 0,
                      "mfa_failures": 0, "mfa_locked_until": 0})
    was_role = target["role"]
    ctx.store.update("users", ctx.tid, uid, patch)
    # A role change has to bite on every instance, not just this one, or a demoted
    # user keeps the old role wherever the load balancer sends them. There is no
    # single token to revoke here — every live token for the user is now wrong — so
    # this bumps the user's own epoch, which `verify_token` compares against
    # (docs/31).
    #
    # The previous line was `if "role" in patch and "role" in (patch.get("role"),)`,
    # and the second test is never true: `"role" in ("manager",)` asks whether the
    # *string* "role" is one of the elements. It was dead code, so `REVOKED.add(uid)`
    # never ran — a demoted manager kept a working token for eight hours.
    if patch.get("role") and patch["role"] != was_role:
        sec.bump_epoch(uid)
    ctx.audit("user.updated", uid, patch)
    return {"ok": True, "user": {**{k: target[k] for k in ("id", "name", "email", "role")}, **patch}}


@route("GET", r"/api/v1/tenant/audit")
def tenant_audit(ctx: Ctx) -> dict:
    # Audit entries name actors, targets and source IPs. Same reasoning as the
    # user directory: a read-only seat has no business enumerating it (SEN-008).
    ctx.require("manage_tenant")
    rows = sorted(ctx.store.all("audit", ctx.tid), key=lambda r: r["at"], reverse=True)[:200]
    verify = ctx.store.verify_audit(ctx.tid)
    presented, retention = aud_ret.present_entries(rows, ctx.tenant)
    return {"entries": presented, "chain": verify, "retention": retention}


# ==========================================================================
# Compliance & data exports — archived to object storage (`docs/37`)
# ==========================================================================
def _build_export_payload(ctx: Ctx, kind: str, fmt: str) -> tuple[bytes, str, int]:
    """Render `(raw_bytes, content_type, row_count)` for one tenant export."""
    if kind == "audit":
        rows = sorted(ctx.store.all("audit", ctx.tid), key=lambda r: r["at"], reverse=True)[:1000]
        verify = ctx.store.verify_audit(ctx.tid)
        presented, retention = aud_ret.present_entries(rows, ctx.tenant)
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "at", "actor", "action", "target", "hash", "prev_hash"])
            for r in presented:
                writer.writerow([
                    r.get("id", ""),
                    r.get("at", 0),
                    r.get("actor", ""),
                    r.get("action", ""),
                    r.get("target", ""),
                    r.get("hash", ""),
                    r.get("prev_hash", ""),
                ])
            return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", len(presented)
        doc = {
            "tenant_id": ctx.tid,
            "kind": "audit",
            "generated_at": now(),
            "chain": verify,
            "retention": retention,
            "entries": presented,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", len(presented)

    if kind == "contacts":
        contacts = [out_contact(c, ctx) for c in ctx.store.all("contacts", ctx.tid)]
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["id", "name", "phone", "email", "stage", "score", "city", "source", "consent_status"])
            for c in contacts:
                writer.writerow([
                    c.get("id", ""),
                    c.get("name", ""),
                    c.get("phone", ""),
                    c.get("email", ""),
                    c.get("stage", ""),
                    c.get("score", 0),
                    c.get("city", ""),
                    c.get("source", ""),
                    (c.get("consent") or {}).get("status", "pending"),
                ])
            return buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", len(contacts)
        doc = {
            "tenant_id": ctx.tid,
            "kind": "contacts",
            "generated_at": now(),
            "count": len(contacts),
            "contacts": contacts,
        }
        return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", len(contacts)

    # kind == "compliance_bundle"
    if fmt != "json":
        raise ApiError(400, "VALIDATION_FAILED", "compliance_bundle export supports format='json' only.")
    audit_rows = sorted(ctx.store.all("audit", ctx.tid), key=lambda r: r["at"], reverse=True)[:1000]
    presented_audit, retention = aud_ret.present_entries(audit_rows, ctx.tenant)
    contacts_raw = ctx.store.all("contacts", ctx.tid)
    consent_counts: dict[str, int] = {"granted": 0, "withdrawn": 0, "pending": 0}
    for c in contacts_raw:
        st = (c.get("consent") or {}).get("status", "pending")
        consent_counts[st] = consent_counts.get(st, 0) + 1
    doc = {
        "tenant_id": ctx.tid,
        "tenant_slug": ctx.tenant.get("slug", ""),
        "kind": "compliance_bundle",
        "generated_at": now(),
        "audit_chain": ctx.store.verify_audit(ctx.tid),
        "audit_retention": retention,
        "consent_summary": {"total_contacts": len(contacts_raw), "by_status": consent_counts},
        "audit_entries": presented_audit,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", len(presented_audit)


def _export_with_url(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if k != "inline_preview"}
    out["download_url"] = ""
    out["expires_in"] = 0
    if rec.get("stored_in") == "object_storage" and rec.get("object_key") and neon.storage_configured():
        try:
            out["download_url"] = neon.presign_get(rec["object_key"], expires=900)
            out["expires_in"] = 900
        except Exception:
            out["download_url"] = ""
    elif rec.get("inline_preview") is not None:
        out["inline_preview"] = rec["inline_preview"]
    return out


@route("POST", r"/api/v1/exports")
def exports_create(ctx: Ctx) -> dict:
    """Build a tenant export and upload it to S3-compatible object storage."""
    ctx.require("manage_tenant")
    kind = (ctx.body_str("kind", 32) or "compliance_bundle").lower()
    if kind not in ("audit", "contacts", "compliance_bundle"):
        raise ApiError(400, "VALIDATION_FAILED", "kind must be 'audit', 'contacts' or 'compliance_bundle'.")
    fmt = (ctx.body_str("format", 12) or "json").lower()
    if fmt not in ("json", "csv"):
        raise ApiError(400, "VALIDATION_FAILED", "format must be 'json' or 'csv'.")
    require_s3 = bool(ctx.body.get("require_object_storage"))
    if require_s3 and not neon.storage_configured():
        raise ApiError(503, "STORAGE_NOT_CONFIGURED", "Object storage is not configured on this instance.")

    raw_bytes, ctype, row_count = _build_export_payload(ctx, kind, fmt)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    exp_id = nid("exp")
    ext = "csv" if fmt == "csv" else "json"
    object_key = f"exports/tenants/{ctx.tid}/{exp_id}.{ext}"

    doc: dict[str, Any] = {
        "id": exp_id,
        "type": "export",
        "kind": kind,
        "format": fmt,
        "content_type": ctype,
        "bytes": len(raw_bytes),
        "sha256": digest,
        "row_count": row_count,
        "created_by": ctx.user.get("email", ""),
        "status": "completed",
    }
    if neon.storage_configured():
        try:
            up = neon.put_object(object_key, raw_bytes, content_type=ctype)
        except Exception as exc:
            raise ApiError(502, "STORAGE_UPLOAD_FAILED", f"Object storage upload failed: {cfg.redact(str(exc))[:120]}")
        doc["stored_in"] = "object_storage"
        doc["object_key"] = up["key"]
        doc["bucket"] = up["bucket"]
    else:
        doc["stored_in"] = "inline"
        doc["object_key"] = ""
        doc["inline_preview"] = raw_bytes[:16384].decode("utf-8", "replace")

    rec = ctx.store.insert("runs", ctx.tid, doc, prefix="exp")
    ctx.audit("export.created", rec["id"], {
        "kind": kind,
        "format": fmt,
        "bytes": len(raw_bytes),
        "stored_in": doc["stored_in"],
        "sha256_prefix": digest[:16],
    })
    return {"export": _export_with_url(rec)}


@route("GET", r"/api/v1/exports")
def exports_list(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    ctx.store.reload_many(("runs",), ctx.tid, max_age_ms=250)
    rows = [r for r in ctx.store.all("runs", ctx.tid) if r.get("type") == "export"]
    rows.sort(key=lambda r: -int(r.get("created_at") or 0))
    limit = ctx.qint("limit", 50, 200)
    return {"exports": [_export_with_url(r) for r in rows[:limit]], "count": len(rows)}


@route("GET", r"/api/v1/exports/(?P<eid>[\w]+)")
def exports_get(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    eid = ctx.params["eid"]
    rec = ctx.store.get("runs", ctx.tid, eid)
    if not rec and getattr(ctx.store, "backend", None) is not None:
        ctx.store.reload("runs", ctx.tid)
        rec = ctx.store.get("runs", ctx.tid, eid)
    if not rec or rec.get("type") != "export":
        raise ApiError(404, "NOT_FOUND", "Export not found.")
    ctx.audit("export.downloaded", eid, {"kind": rec.get("kind", ""), "stored_in": rec.get("stored_in", "")})
    return {"export": _export_with_url(rec)}


# ==========================================================================
# CRM
# ==========================================================================
@route("GET", r"/api/v1/contacts")
def contacts_list(ctx: Ctx) -> dict:
    rows = ctx.store.all("contacts", ctx.tid)
    q = (ctx.query.get("q", [""])[0] or "").lower()
    stage = ctx.query.get("stage", [""])[0]
    if q:
        rows = [
            c
            for c in rows
            if q in c["name"].lower()
            or q in (c.get("city") or "").lower()
            or q in (c.get("requirement") or "").lower()
            or q in (c.get("source") or "").lower()
        ]
    if stage:
        rows = [c for c in rows if c.get("stage") == stage]
    rows.sort(key=lambda c: (c.get("score") or 0), reverse=True)
    rows = rows[: ctx.qint("limit", 100, 500)]
    return {"contacts": [out_contact(c, ctx) for c in rows], "count": len(rows)}


@route("POST", r"/api/v1/contacts")
def contacts_create(ctx: Ctx) -> dict:
    ctx.require("write")
    # the plan's contact allowance, checked before anything is sealed or written
    ut.enforce(ctx.store, ctx.tenant, "contacts")
    name = ctx.body_str("name", 80, required=True)
    phone = ctx.body_str("phone", 20, required=True)
    if not sec.valid_phone(phone):
        raise ApiError(400, "VALIDATION_FAILED", "Enter a valid phone number (E.164 or local).")
    email_val = ctx.body_str("email", 254)
    if comp_mod.is_suppressed(ctx.store, ctx.tid, phone=phone, email=email_val):
        raise ApiError(422, "SUPPRESSED_BY_ERASURE", "This phone or email is on the DPDP §8(5) erasure suppression list and cannot be re-imported.")
    if sec.find_by_blind_index(ctx.store, "contacts", ctx.tid, "phone_hash", phone):
        raise ApiError(409, "DUPLICATE_LEAD", "A contact with this number already exists.")
    raw_consent = ctx.body.get("consent")
    if isinstance(raw_consent, bool):
        consent = {"status": "granted" if raw_consent else "pending"}
    elif isinstance(raw_consent, str):
        consent = {"status": "granted" if raw_consent.strip().lower() in ("granted", "true", "yes", "1") else "pending"}
    elif isinstance(raw_consent, dict):
        consent = raw_consent
    else:
        consent = {}
    raw_tags = ctx.body.get("tags")
    clean_tags = [sec.clean(t, 24) for t in raw_tags][:6] if isinstance(raw_tags, list) else []
    doc = {
        "name": name,
        "phone_enc": sec.seal(phone, aad=ctx.tid),
        "phone_hash": sec.blind_index(phone, tenant_id=ctx.tid),
        "email_enc": sec.seal(ctx.body_str("email", 254), aad=ctx.tid),
        "email_hash": sec.blind_index(ctx.body_str("email", 254), tenant_id=ctx.tid),
        "city": ctx.body_str("city", 60),
        # Place of supply for GST notice 18%/IGST depends on the client's state, so
        # it has to be a real field rather than something guessed from the city name.
        "state": ctx.body_str("state", 40),
        "gstin": sec.clean(ctx.body.get("gstin", ""), 20),
        "is_business": bool(ctx.body.get("is_business")),
        "source": ctx.body_str("source", 40) or "Manual",
        "stage": "new",
        "score": 50,
        "intent": "medium",
        "requirement": ctx.body_str("requirement", 200),
        "budget_min": ctx.body_int("budget_min", 0, 0),
        "budget_max": ctx.body_int("budget_max", 0, 0),
        "tags": clean_tags,
        "language": "mr",
        "owner_id": ctx.user["id"],
        "consent": {
            "status": "granted" if consent.get("status") == "granted" else "pending",
            "purpose": sec.clean(consent.get("purpose", "enquiry"), 80),
            "channel": sec.clean(consent.get("channel", "manual"), 40),
            "captured_at": now(),
            "notice_version": "v3.1",
            "evidence": f"manual-{ctx.user['id']}",
        },
        "value": ctx.body_int("budget_max", 0, 0),
        "last_activity_at": now(),
    }
    c = ctx.store.insert("contacts", ctx.tid, doc, prefix="cnt")
    scoring = ag.score_lead(c)
    ctx.store.update("contacts", ctx.tid, c["id"], {"score": scoring["score"]})
    ctx.store.insert("activities", ctx.tid, {"contact_id": c["id"], "type": "created", "text": f"Lead captured from {doc['source']}", "actor": ctx.user["name"], "at": now()}, prefix="act")
    ctx.audit("contact.created", c["id"], {"source": doc["source"]})
    rt.HUB.publish(ctx.tid, "inbox.contact", c["id"], {"source": doc["source"]}, collections=("contacts",))
    return {"contact": out_contact(ctx.store.get("contacts", ctx.tid, c["id"]), ctx), "scoring": scoring}


@route("GET", r"/api/v1/contacts/(?P<cid>[\w]+)")
def contacts_get(ctx: Ctx) -> dict:
    c = ctx.store.get("contacts", ctx.tid, ctx.params["cid"])
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found in this workspace.")
    deals = [d for d in ctx.store.all("deals", ctx.tid) if d.get("contact_id") == c["id"]]
    conv = next((x for x in ctx.store.all("conversations", ctx.tid) if x.get("contact_id") == c["id"]), None)
    return {
        "contact": out_contact(c, ctx),
        "deals": deals,
        "conversation": conv,
        "scoring": ag.score_lead(c),
        "timeline": sorted([a for a in ctx.store.all("activities", ctx.tid) if a.get("contact_id") == c["id"]], key=lambda a: -a["at"])[:40],
    }


@route("PATCH", r"/api/v1/contacts/(?P<cid>[\w]+)")
def contacts_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    cid = ctx.params["cid"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found in this workspace.")
    allowed = {"name", "city", "source", "stage", "requirement", "intent", "budget_min",
               "budget_max", "tags", "value", "language", "state", "gstin", "is_business"}
    patch = sec.pick(ctx.body, allowed)
    if "stage" in patch and patch["stage"] not in (*vt.STAGES, vt.LOST):
        raise ApiError(400, "VALIDATION_FAILED", "Unknown stage.")
    if "phone" in ctx.body and ctx.can("pii:read"):
        ph = sec.clean(ctx.body["phone"], 20)
        if not sec.valid_phone(ph):
            raise ApiError(400, "VALIDATION_FAILED", "Invalid phone.")
        patch["phone_enc"] = sec.seal(ph, aad=ctx.tid)
        patch["phone_hash"] = sec.blind_index(ph, tenant_id=ctx.tid)
    patch = {k: (sec.clean(v, 200) if isinstance(v, str) else v) for k, v in patch.items()}
    patch["last_activity_at"] = now()
    updated = ctx.store.update("contacts", ctx.tid, cid, patch)
    if "stage" in patch:
        for d in ctx.store.all("deals", ctx.tid):
            if d.get("contact_id") == cid:
                ctx.store.update("deals", ctx.tid, d["id"], {"stage": patch["stage"]})
    ctx.audit("contact.updated", cid, {"fields": list(patch)})
    return {"contact": out_contact(updated, ctx)}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/consent")
def contacts_consent(ctx: Ctx) -> dict:
    ctx.require("write")
    cid = ctx.params["cid"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    status = ctx.body_str("status", 20)
    if status not in ("granted", "withdrawn", "pending"):
        raise ApiError(400, "VALIDATION_FAILED", "status must be granted, withdrawn or pending.")
    consent = {
        "status": status,
        "purpose": sec.clean(ctx.body.get("purpose", "site visit follow-up"), 80),
        "channel": sec.clean(ctx.body.get("channel", "manual"), 40),
        "captured_at": now(),
        "notice_version": comp_mod.NOTICE_VERSION,
        "evidence": f"{ctx.user['email']}:{now()}",
    }
    ctx.store.update("contacts", ctx.tid, cid, {"consent": consent})
    propagation = None
    if status == "withdrawn":
        propagation = comp_mod.propagate_consent_withdrawal(
            ctx.store, ctx.tid, cid, actor=ctx.user.get("email", "operator")
        )
        ctx.audit("consent.withdrawal_propagated", cid, {
            "runs_cancelled": propagation["runs_cancelled_count"],
            "tasks_closed": propagation["tasks_closed_count"],
        })
    ctx.audit("consent." + status, cid, consent)
    return {"consent": consent, "propagation": propagation}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/note")
def contacts_note(ctx: Ctx) -> dict:
    ctx.require("write")
    text = ctx.body_str("text", 1000, required=True)
    a = ctx.store.insert("activities", ctx.tid, {"contact_id": ctx.params["cid"], "type": "note", "text": text, "actor": ctx.user["name"], "at": now()}, prefix="act")
    ctx.store.update("contacts", ctx.tid, ctx.params["cid"], {"last_activity_at": now()})
    ctx.audit("contact.note", ctx.params["cid"], {"len": len(text)})
    return {"activity": a}


@route("GET", r"/api/v1/pipeline")
def pipeline(ctx: Ctx) -> dict:
    ctx.store.prefetch(("deals", "contacts"), ctx.tid)
    deals = ctx.store.all("deals", ctx.tid)
    contacts = {c["id"]: c for c in ctx.store.all("contacts", ctx.tid)}
    # The board shows the real-estate sequence, token → agreement → registration
    # included: a brokerage deal that "won" before registration is a data error.
    stages = list(vt.STAGES)
    cols = {s: [] for s in stages}
    for d in deals:
        c = contacts.get(d.get("contact_id"), {})
        cols.setdefault(d.get("stage", "new"), []).append(
            {
                "id": d["id"],
                "title": d.get("title"),
                "value": d.get("value"),
                "probability": d.get("probability"),
                "contact_id": d.get("contact_id"),
                "contact_name": c.get("name"),
                "score": c.get("score"),
                "expected_close": d.get("expected_close"),
                "owner_id": d.get("owner_id"),
            }
        )
    return {"stages": stages, "columns": cols, "total_value": sum(d.get("value", 0) for d in deals)}


@route("PATCH", r"/api/v1/deals/(?P<did>[\w]+)")
def deals_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    d = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not d:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    patch = sec.pick(ctx.body, {"stage", "value", "probability", "expected_close"})
    if "stage" in patch:
        patch["probability"] = vt.PROBABILITY.get(patch["stage"], d.get("probability", 20))
    upd = ctx.store.update("deals", ctx.tid, d["id"], patch)
    ctx.audit("deal.updated", d["id"], patch)
    return {"deal": upd}


@route("GET", r"/api/v1/tasks")
def tasks_list(ctx: Ctx) -> dict:
    return {"tasks": sorted(ctx.store.all("tasks", ctx.tid), key=lambda t: t.get("due_at", 0))}


@route("POST", r"/api/v1/tasks")
def tasks_create(ctx: Ctx) -> dict:
    ctx.require("write")
    t = ctx.store.insert(
        "tasks",
        ctx.tid,
        {
            "title": ctx.body_str("title", 140, required=True),
            "due_at": int(ctx.body.get("due_at") or now() + 86400),
            "owner_id": ctx.user["id"],
            "contact_id": sec.clean(ctx.body.get("contact_id", ""), 40),
            "done": False,
            "priority": sec.clean(ctx.body.get("priority", "medium"), 10),
        },
        prefix="tsk",
    )
    ctx.audit("task.created", t["id"], {"title": t["title"]})
    return {"task": t}


@route("PATCH", r"/api/v1/tasks/(?P<tid_>[\w]+)")
def tasks_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    t = ctx.store.update("tasks", ctx.tid, ctx.params["tid_"], sec.pick(ctx.body, {"done", "title", "priority"}))
    if not t:
        raise ApiError(404, "NOT_FOUND", "Task not found.")
    return {"task": t}


@route("GET", r"/api/v1/properties")
def properties_list(ctx: Ctx) -> dict:
    rows = ctx.store.all("properties", ctx.tid)
    return {"properties": rows, "count": len(rows)}


@route("GET", r"/api/v1/properties/(?P<pid_>[\w]+)/media")
def property_media(ctx: Ctx) -> dict:
    """Derived listing photos for one property.

    The rows are written by the `property-photos` Neon Function, which is
    triggered by an upload to `properties/<id>/…` in object storage. This route
    is the read side only: it never writes, so the single-writer read cache that
    governs `re_documents` is untouched. Variants are private objects, so each
    entry carries a short-lived presigned URL instead of a public link.
    """
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid_"])
    if not prop:
        # Tenant scoping first: an id from another workspace is simply not found.
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    backend = getattr(ctx.store, "backend", None)
    if backend is None:
        raise ApiError(
            503,
            "PIPELINE_UNAVAILABLE",
            "Listing media is stored by the Postgres backend; this instance is running on the file store.",
        )
    rows = backend.property_media(prop["id"], ctx.tid)
    media = []
    for row in rows:
        item = {
            "variant": row.get("variant", ""),
            "key": row.get("object_key", ""),
            "width": row.get("width"),
            "height": row.get("height"),
            "bytes": int(row.get("bytes") or 0),
            "checksum": row.get("checksum", ""),
            "updated_at": row.get("updated_at"),
            "url": "",
        }
        if neon.storage_configured():
            try:
                item["url"] = neon.presign_get(item["key"], expires=900)
            except Exception:
                item["url"] = ""   # never fail a read because signing failed
        media.append(item)
    # rows are variants; a person counts photos. The gallery groups them, picks
    # one variant per photo to load and marks the cover (docs/25).
    photos = md.gallery(rows, photo_order=prop.get("photo_order"), pinned_cover_id=prop.get("pinned_cover_id", ""))
    for photo in photos:
        for slot, key in (("url", photo["key"]), ("hero_url", photo["hero_key"])):
            if slot == "url" and not key:
                continue
            photo[slot] = ""
            if key and neon.storage_configured():
                try:
                    photo[slot] = neon.presign_get(key, expires=900)
                except Exception:
                    photo[slot] = ""
    return {
        "property_id": prop["id"],
        "media": media,
        "count": len(media),
        "photos": photos,
        "photo_count": len(photos),
        "cover": photos[0] if photos else None,
        "readiness": md.readiness(rows),
        "placeholder": md.placeholder(prop),
    }


# ---------------------------------------------------------------------------
# Records written by the Neon Functions (schedule + webhook).
#
# Both are read-only here on purpose: the functions own their tables, the app
# reads them. Nothing in the web tier can forge a "clean sweep" or invent a
# payment event.
# ---------------------------------------------------------------------------
# ==========================================================================
# Real-estate vertical CRM (docs/18)
#
# Three objects the horizontal CRMs do not model — the site visit, the
# requirement-to-property match, and the commission — plus a stage machine that
# refuses to let a deal claim progress it cannot document. Rules live in
# app/vertical.py as pure functions; these routes are the wiring and the audit.
# ==========================================================================
def _visit_payload(ctx: Ctx, visit: dict) -> dict:
    contacts = {c["id"]: c for c in ctx.store.all("contacts", ctx.tid)}
    props = {p["id"]: p for p in ctx.store.all("properties", ctx.tid)}
    users = {u["id"]: u for u in ctx.store.all("users", ctx.tid)}
    out = dict(visit)
    out["contact_name"] = (contacts.get(visit.get("contact_id")) or {}).get("name", "")
    out["property_title"] = (props.get(visit.get("property_id")) or {}).get("title", "")
    out["agent_name"] = (users.get(visit.get("agent_id")) or {}).get("name", "")
    out["allowed_next"] = list(vt.VISIT_TRANSITIONS.get(visit.get("status", ""), ()))
    return out


@route("GET", r"/api/v1/contacts/(?P<cid>[\w]+)/matches")
def contact_matches(ctx: Ctx) -> dict:
    """Ranked, explainable property shortlist for one buyer requirement."""
    contact = ctx.store.get("contacts", ctx.tid, ctx.params["cid"])
    if not contact:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    limit = ctx.qint("limit", 5, 20)
    matches = vt.match_properties(contact, ctx.store.all("properties", ctx.tid), limit=limit)
    ctx.audit("match.viewed", contact["id"], {"returned": len(matches)})
    return {
        "contact_id": contact["id"],
        "contact_name": contact.get("name", ""),
        "requirement": contact.get("requirement", ""),
        "budget": {"min": contact.get("budget_min"), "max": contact.get("budget_max")},
        "matches": matches,
        "count": len(matches),
    }


# ==========================================================================
# Property taxonomy (docs/22)
#
# Residential, commercial, industrial, land and hospitality are five different
# products. These four routes are the whole surface: the catalogue a form is
# generated from, creation that refuses an incoherent listing, search that says
# why it ranked something, and a saved requirement that turns a phone note into
# a shortlist.
# ==========================================================================
def _tax_guard(exc: "tax.TaxonomyError") -> ApiError:
    status = 404 if exc.code == "UNKNOWN_CATEGORY" else 422
    details = list((exc.details.get("errors") or [])) if isinstance(exc.details, dict) else []
    return ApiError(status, exc.code, exc.message, details)


def _property_card(prop: dict) -> dict:
    pricing = tax.price_metrics(prop)
    return {
        "id": prop.get("id"),
        "title": prop.get("title"),
        "category": prop.get("category"),
        "type": prop.get("type"),
        "deal_kind": prop.get("deal_kind"),
        "city": prop.get("city"),
        "locality": prop.get("locality"),
        "status": prop.get("status"),
        "area": prop.get("area"),
        "attributes": prop.get("attributes") or {},
        "pricing": prop.get("pricing"),
        "money": {"basis": pricing["basis"], "amount": pricing["amount"], "total": pricing["total"],
                  "monthly": pricing["monthly"], "per_sqft": pricing["per_sqft"],
                  "annual": pricing["annual"], "deposit_months": pricing["deposit_months"],
                  "lock_in_months": pricing["lock_in_months"]},
        "rera": prop.get("rera") or (prop.get("attributes") or {}).get("rera_id"),
    }


@route("GET", r"/api/v1/taxonomy")
def taxonomy_catalogue(ctx: Ctx) -> dict:
    """The five categories, their types and their own field sets — form source of truth."""
    cats = tax.categories()
    return {
        "categories": cats,
        "count": len(cats),
        "area_units": [{"unit": u, "sqft": v} for u, v in tax.AREA_UNITS.items()],
        "refused_units": [{"unit": u, "why": w} for u, w in tax.REFUSED_UNITS.items()],
        "price_bases": list(tax.PRICE_BASES),
        "deal_kinds": list(tax.DEAL_KINDS),
        "field_counts": {c["slug"]: len(c["fields"]) for c in cats},
    }


@route("POST", r"/api/v1/properties")
def properties_create(ctx: Ctx) -> dict:
    """Create a property. The category decides which fields are legal and required."""
    ctx.require("write")
    payload = dict(ctx.body)
    payload.setdefault("city", ctx.body_str("city", 60))
    payload.setdefault("locality", ctx.body_str("locality", 80))
    payload.setdefault("title", ctx.body_str("title", 160))
    if not payload.get("title"):
        raise ApiError(400, "VALIDATION_FAILED", "'title' is required.")
    try:
        clean = tax.normalize_property(payload)
    except tax.TaxonomyError as exc:
        raise _tax_guard(exc)
    prop = ctx.store.insert("properties", ctx.tid, {
        "title": payload["title"],
        "city": payload["city"],
        "locality": payload["locality"],
        "status": ctx.body_str("status", 24) or "available",
        "docs": ctx.body.get("docs") if isinstance(ctx.body.get("docs"), list) else [],
        "source": "manual",
        **clean,
    }, prefix="prp")
    prop["price"] = clean["pricing"]["amount"]
    prop["size_sqft"] = (clean["area"] or {}).get("sqft")
    ctx.audit("property.created", prop["id"],
              {"category": clean["category"], "type": clean["type"], "deal_kind": clean["deal_kind"]})
    return {"property": _property_card(prop)}


def _search_filters(rows: list[dict], spec: dict, raw: dict) -> list[dict]:
    """Hard filters only where the user stated a fact; budgets stay soft (scored)."""
    out = []
    for prop in rows:
        if spec.get("category") and prop.get("category") != spec["category"]:
            continue
        if spec.get("types") and prop.get("type") not in spec["types"]:
            continue
        if spec.get("localities"):
            hay = f"{(prop.get('locality') or '').lower()} {(prop.get('city') or '').lower()}"
            if not any(loc in hay for loc in spec["localities"]):
                continue
        wanted = raw.get("attributes")
        if isinstance(wanted, dict):
            attrs = prop.get("attributes") or {}
            keep = True
            for key, want in wanted.items():
                if key not in attrs:
                    keep = False
                    break
                if isinstance(want, dict):
                    lo, hi = want.get("min"), want.get("max")
                    try:
                        have = float(attrs[key])
                    except (TypeError, ValueError):
                        keep = False
                        break
                    if (lo is not None and have < float(lo)) or (hi is not None and have > float(hi)):
                        keep = False
                        break
                elif isinstance(want, bool):
                    if bool(attrs[key]) != want:
                        keep = False
                        break
                elif str(attrs[key]).lower() != str(want).lower():
                    keep = False
                    break
            if not keep:
                continue
        out.append(prop)
    return out


@route("POST", r"/api/v1/properties/search")
def properties_search(ctx: Ctx) -> dict:
    """Search inventory in any category, ranked against a structured requirement."""
    try:
        spec = tax.normalize_requirement(ctx.body)
    except tax.TaxonomyError as exc:
        raise _tax_guard(exc)
    text = (ctx.body_str("q", 80) or "").strip().lower()
    named_product = False
    if text:
        resolved = tax.resolve_type(text)
        if resolved:
            # "godown" is a product, not a word to find in a title: use it as a
            # category/type filter and drop the substring search.
            named_product = True
            spec["category"] = spec["category"] or resolved[0]
            if not spec["types"] and spec["category"] == resolved[0]:
                spec["types"] = [resolved[1]]
    limit = max(1, min(50, int(ctx.body.get("limit") or 12)))

    rows = [tax.hydrate(p) for p in ctx.store.all("properties", ctx.tid)]
    if text and not named_product:
        rows = [p for p in rows
                if text in f"{p.get('title','')} {p.get('locality','')} {p.get('city','')}".lower()]
    kept = _search_filters(rows, spec, ctx.body)
    ranked = tax.rank(kept, spec, limit=limit)
    results = [{"score": r["score"], "reasons": r["reasons"], "property": _property_card(r["property"])}
               for r in ranked]
    ctx.audit("taxonomy.searched", "",
              {"category": spec["category"], "types": spec["types"], "deal_kind": spec["deal_kind"],
               "candidates": len(kept), "returned": len(results)})
    return {
        "requirement": spec,
        "results": results,
        "count": len(results),
        "candidates": len(kept),
        "scanned": len(rows),
        "categories_scanned": sorted({p.get("category") or "residential" for p in rows}),
        "note": "Budgets and areas are scored, not filtered: a listing 10% over budget still shows, "
                "with the gap stated in its reasons.",
    }


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/requirement")
def contact_requirement(ctx: Ctx) -> dict:
    """Save a structured requirement on a contact and return the shortlist it buys."""
    ctx.require("write")
    contact = ctx.store.get("contacts", ctx.tid, ctx.params["cid"])
    if not contact:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    try:
        spec = tax.normalize_requirement(ctx.body)
    except tax.TaxonomyError as exc:
        raise _tax_guard(exc)
    if not spec["category"]:
        raise ApiError(400, "VALIDATION_FAILED",
                       "'category' is required: " + ", ".join(tax.CATEGORIES) + ".")

    patch: dict = {"requirement_spec": spec}
    summary = _requirement_summary(spec)
    if summary:
        patch["requirement"] = summary
    if spec["budget_max"]:
        patch["budget_min"] = int(spec["budget_min"] or 0)
        patch["budget_max"] = int(spec["budget_max"])
    contact = ctx.store.update("contacts", ctx.tid, contact["id"], patch) or contact

    rows = _search_filters([tax.hydrate(p) for p in ctx.store.all("properties", ctx.tid)], spec, ctx.body)
    ranked = tax.rank(rows, spec, limit=max(1, min(20, int(ctx.body.get("limit") or 5))))
    ctx.audit("contact.requirement_set", contact["id"],
              {"category": spec["category"], "types": spec["types"], "deal_kind": spec["deal_kind"],
               "budget_max": spec["budget_max"]})
    return {
        "contact_id": contact["id"],
        "contact_name": contact.get("name", ""),
        "requirement": spec,
        "summary": contact.get("requirement", ""),
        "matches": [{"score": r["score"], "reasons": r["reasons"], "property": _property_card(r["property"])}
                    for r in ranked],
        "count": len(ranked),
    }


def _requirement_summary(spec: dict) -> str:
    """A one-line brief a human would actually read back to the buyer."""
    bits = []
    if spec.get("bedrooms_min"):
        bits.append(f"{spec['bedrooms_min']}BHK+")
    if spec.get("types"):
        bits.append("/".join(spec["types"]).replace("_", " "))
    elif spec.get("category"):
        bits.append(spec["category"])
    if spec.get("deal_kind"):
        bits.append({"sale": "to buy", "rent": "on rent", "lease": "on lease",
                     "leave_license": "on leave & license"}.get(spec["deal_kind"], spec["deal_kind"]))
    lo, hi = spec.get("area_min_sqft"), spec.get("area_max_sqft")
    if lo and hi:
        bits.append(f"{lo:,.0f}–{hi:,.0f} sqft")
    elif lo:
        bits.append(f"{lo:,.0f}+ sqft")
    elif hi:
        bits.append(f"up to {hi:,.0f} sqft")
    if spec.get("budget_max"):
        bits.append(f"budget {tax.inr(spec['budget_max'])}")
    if spec.get("city"):
        bits.append(f"in {spec['city']}")
    return " ".join(bits)


@route("GET", r"/api/v1/site-visits")
def site_visits_list(ctx: Ctx) -> dict:
    rows = ctx.store.all("site_visits", ctx.tid)
    status = (ctx.query.get("status", [""])[0] or "").strip()
    agent = (ctx.query.get("agent_id", [""])[0] or "").strip()
    if status:
        rows = [v for v in rows if v.get("status") == status]
    if agent:
        rows = [v for v in rows if v.get("agent_id") == agent]
    rows.sort(key=lambda v: int(v.get("scheduled_at") or 0))
    limit = ctx.qint("limit", 50, 200)
    return {
        "visits": [_visit_payload(ctx, v) for v in rows[:limit]],
        "count": len(rows[:limit]),
        "statuses": list(vt.VISIT_STATUSES),
    }


@route("POST", r"/api/v1/site-visits")
def site_visits_create(ctx: Ctx) -> dict:
    ctx.require("write")
    contact_id = ctx.body_str("contact_id", 40, required=True)
    property_id = ctx.body_str("property_id", 40, required=True)
    if not ctx.store.get("contacts", ctx.tid, contact_id):
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    if not ctx.store.get("properties", ctx.tid, property_id):
        raise ApiError(404, "NOT_FOUND", "Property not found.")

    scheduled_at = _epoch(ctx.body.get("scheduled_at"))
    if scheduled_at is None:
        raise ApiError(400, "VALIDATION_FAILED", "'scheduled_at' must be an epoch second or an ISO-8601 timestamp.")
    if scheduled_at < ctx.now - 3600:
        raise ApiError(400, "VALIDATION_FAILED", "A visit cannot be scheduled in the past.")

    agent_id = ctx.body_str("agent_id", 40) or ctx.user.get("id", "")
    if agent_id and not ctx.store.get("users", ctx.tid, agent_id):
        raise ApiError(400, "VALIDATION_FAILED", "Unknown agent_id.")
    duration = ctx.body_int("duration_min", vt.DEFAULT_DURATION_MIN, 15, 240)

    clash = vt.find_conflict(
        ctx.store.all("site_visits", ctx.tid),
        agent_id=agent_id,
        scheduled_at=scheduled_at,
        duration_min=duration,
    )
    if clash:
        # 409 with the conflicting visit: the UI can offer the next free slot.
        raise ApiError(
            409,
            "VISIT_CONFLICT",
            f"{clash.get('agent_name') or agent_id} already has a visit at that time.",
            [{"conflict_id": clash.get("id"), "scheduled_at": clash.get("scheduled_at")}],
        )

    visit = ctx.store.insert(
        "site_visits",
        ctx.tid,
        {
            "contact_id": contact_id,
            "property_id": property_id,
            "agent_id": agent_id,
            "scheduled_at": scheduled_at,
            "duration_min": duration,
            "status": "scheduled",
            "pickup": ctx.body_str("pickup", 120),
            "notes": ctx.body_str("notes", 500),
            "outcome": "",
            "feedback": "",
            "created_by": ctx.user.get("id", ""),
        },
        prefix="sv",
    )
    ctx.audit("site_visit.scheduled", visit["id"], {"contact_id": contact_id, "property_id": property_id, "at": scheduled_at})
    return {"visit": _visit_payload(ctx, visit)}


@route("PATCH", r"/api/v1/site-visits/(?P<sid>[\w]+)")
def site_visits_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    visit = ctx.store.get("site_visits", ctx.tid, ctx.params["sid"])
    if not visit:
        raise ApiError(404, "NOT_FOUND", "Visit not found.")

    patch: dict = {}
    if "status" in ctx.body:
        target = ctx.body_str("status", 20, required=True)
        try:
            vt.check_visit_transition(visit.get("status", ""), target)
        except vt.StageError as exc:
            raise ApiError(422, exc.code, exc.message, [exc.details])
        patch["status"] = target
    if "scheduled_at" in ctx.body:
        when = _epoch(ctx.body.get("scheduled_at"))
        if when is None:
            raise ApiError(400, "VALIDATION_FAILED", "'scheduled_at' must be an epoch second or ISO-8601.")
        clash = vt.find_conflict(
            ctx.store.all("site_visits", ctx.tid),
            agent_id=visit.get("agent_id", ""),
            scheduled_at=when,
            duration_min=int(visit.get("duration_min") or vt.DEFAULT_DURATION_MIN),
            exclude_id=visit["id"],
        )
        if clash:
            raise ApiError(409, "VISIT_CONFLICT", "That slot is taken.", [{"conflict_id": clash.get("id")}])
        patch["scheduled_at"] = when

    for key, cap in (("outcome", 200), ("feedback", 600), ("pickup", 120), ("notes", 500)):
        if key in ctx.body:
            patch[key] = sec.clean(ctx.body.get(key, ""), cap)
    if not patch:
        raise ApiError(400, "VALIDATION_FAILED", "Nothing to update.")

    upd = ctx.store.update("site_visits", ctx.tid, visit["id"], patch)
    ctx.audit("site_visit.updated", visit["id"], {"changed": sorted(patch)})
    return {"visit": _visit_payload(ctx, upd or visit)}


@route("GET", r"/api/v1/deals/(?P<did>[\w]+)")
def deals_detail(ctx: Ctx) -> dict:
    """One deal with the two things a pipeline board cannot show: what each
    remaining stage still needs, and what the commission will be."""
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    visits = ctx.store.all("site_visits", ctx.tid)
    unit = ctx.store.get("units", ctx.tid, deal["unit_id"]) if deal.get("unit_id") else None
    return {
        "deal": deal,
        "unit": ivt.describe(unit, now()) if unit else None,
        "checklist": vt.stage_checklist(deal, visits),
        "visits": [
            _visit_payload(ctx, v)
            for v in visits
            if v.get("contact_id") == deal.get("contact_id")
        ],
        "commission": deal.get("commission") or _safe_commission(deal),
        "stages": list(vt.STAGES),
    }


def _safe_commission(deal: dict) -> dict:
    try:
        return vt.commission_breakdown(deal)
    except vt.StageError:
        return {}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/documents")
def deals_documents(ctx: Ctx) -> dict:
    """Record a document against a deal — the only way past a document gate."""
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    name = ctx.body_str("name", 80, required=True)
    status_ = ctx.body_str("status", 20) or "received"
    if status_ not in ("pending", "received", "verified", "rejected"):
        raise ApiError(400, "VALIDATION_FAILED", "status must be pending, received, verified or rejected.")
    known = {n for names in vt.STAGE_DOCUMENTS.values() for n in names}
    docs = [d for d in (deal.get("documents") or []) if isinstance(d, dict) and d.get("name") != name]
    docs.append({"name": name, "status": status_, "at": now(), "by": ctx.user.get("id", ""),
                 "reference": ctx.body_str("reference", 80)})
    upd = ctx.store.update("deals", ctx.tid, deal["id"], {"documents": docs})
    ctx.audit("deal.document_recorded", deal["id"], {"name": name, "status": status_, "expected": name in known})
    return {"deal": upd, "document": docs[-1], "checklist": vt.stage_checklist(upd or deal, ctx.store.all("site_visits", ctx.tid))}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/advance")
def deals_advance(ctx: Ctx) -> dict:
    """Move a deal, refusing stages its documents or visits do not support."""
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    target = ctx.body_str("stage", 30, required=True)
    force = bool(ctx.body.get("force"))
    role = ctx.role
    visits = ctx.store.all("site_visits", ctx.tid)

    try:
        gate = vt.check_transition(deal, target, role, visits)
    except vt.StageError as exc:
        if not (force and role in ("owner", "admin")):
            raise ApiError(409, exc.code, exc.message, [exc.details])
        # An override is allowed, but it is never quiet.
        ctx.audit("deal.stage_forced", deal["id"], {"from": deal.get("stage"), "to": target, "blocked_by": exc.code})
        gate = {"forced": True, "blocked_by": exc.code, "to": target}

    # Track G-1: When a property has a Land & Revenue Vault populated, enforce
    # Collector Class-II transfer permission before Token and zero-Boja / fresh
    # 7/12 clearance before Agreement or Registration (unless owner/admin forces).
    if deal.get("property_id") and target in ("token", "agreement", "registration"):
        prop_row = ctx.store.get("properties", ctx.tid, deal["property_id"])
        if prop_row and prop_row.get("revenue_vault") and not (force and role in ("owner", "admin")):
            rev_clear = gsc_mod.evaluate_revenue_clearance(prop_row)
            if target == "token" and not rev_clear.get("can_accept_token", True):
                blk = rev_clear["blockers"][0]
                raise ApiError(409, blk["code"], blk["message"], rev_clear["blockers"])
            if target in ("agreement", "registration") and not rev_clear.get("can_execute_agreement", True):
                blk = rev_clear["blockers"][0]
                raise ApiError(409, blk["code"], blk["message"], rev_clear["blockers"])

    # A deal that holds a unit moves that unit with it: token books it,
    # registration/won sells it. Done before the stage write so a refusal leaves
    # the deal exactly where it was.
    unit_note = _apply_unit_for_stage(ctx, deal, target)

    history = list(deal.get("stage_history") or [])
    history.append({"stage": target, "at": now(), "by": ctx.user.get("id", ""), "forced": bool(gate.get("forced"))})
    patch: dict = {"stage": target, "probability": vt.PROBABILITY[target], "stage_history": history[-25:]}
    # When a deal was won is its own fact, not a property of the row's last edit:
    # commission reports and the post-close review ask (`deal.closed`, docs/23)
    # both read it, and "whenever this row was last touched" is a different day.
    if target in ("won", "lost") and not deal.get("closed_at"):
        patch["closed_at"] = now()
    upd = ctx.store.update("deals", ctx.tid, deal["id"], patch)
    ctx.audit("deal.stage_changed", deal["id"], {"from": deal.get("stage"), "to": target})
    return {"deal": upd, "gate": gate, "unit": unit_note, "checklist": vt.stage_checklist(upd or deal, visits)}


def _apply_unit_for_stage(ctx: Ctx, deal: dict, target: str) -> dict | None:
    """Carry the deal's unit through the stages that change its state."""
    unit_id = deal.get("unit_id")
    if not unit_id:
        return None
    unit = ctx.store.get("units", ctx.tid, unit_id)
    if not unit:
        return {"unit_id": unit_id, "status": "missing"}
    try:
        if target == "token":
            patch = ivt.apply_booking(unit, deal_id=deal["id"], contact_id=deal.get("contact_id", ""),
                                      by=ctx.user.get("id", ""), at=now())
            if patch:
                ctx.store.update("units", ctx.tid, unit_id, patch)
                ctx.audit("unit.booked", unit_id, {"deal_id": deal["id"], "via": "stage:token"})
        elif target in ("registration", "won"):
            patch = ivt.apply_sale(unit, deal_id=deal["id"], by=ctx.user.get("id", ""), at=now())
            if patch:
                ctx.store.update("units", ctx.tid, unit_id, patch)
                ctx.audit("unit.sold", unit_id, {"deal_id": deal["id"], "via": f"stage:{target}"})
    except ivt.InventoryError as exc:
        raise ApiError(409, exc.code, exc.message, [exc.details])
    fresh = ctx.store.get("units", ctx.tid, unit_id) or unit
    return {"unit_id": unit_id, "unit_no": fresh.get("unit_no"), "status": ivt.effective_status(fresh, now())}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/commission")
def deals_commission(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    try:
        breakdown = vt.commission_breakdown(
            deal,
            rate_pct=ctx.body.get("rate_pct"),
            agent_share_pct=ctx.body.get("agent_share_pct"),
            channel_partner_id=ctx.body_str("channel_partner_id", 40),
            channel_partner_pct=ctx.body.get("channel_partner_pct"),
        )
    except vt.StageError as exc:
        raise ApiError(400, exc.code, exc.message)
    payload = {**breakdown, "computed_at": now(), "computed_by": ctx.user.get("id", "")}
    upd = ctx.store.update("deals", ctx.tid, deal["id"], {"commission": payload})
    ctx.audit("deal.commission_set", deal["id"], {"gross": breakdown["gross"], "rate_pct": breakdown["rate_pct"]})
    return {"deal": upd, "commission": payload}


# ==========================================================================
# Unit-level inventory (docs/18 §4 #4, §6 #6)
#
# A deal blocks a unit (a timed hold), books it on the token, and owns it at
# registration. Every refusal names the holder, because "this unit is not
# available" is useless to an agent standing in front of a buyer.
# ==========================================================================
@route("GET", r"/api/v1/properties/covers")
def property_covers(ctx: Ctx) -> dict:
    """Covers for a list of properties, in one round trip.

    A table of forty rows that needs forty media calls is a table that renders
    forty times. This is a *read* — ids in the query string, no CSRF — and it
    answers per id even when the answer is "we cannot show you a photo", with the
    reason, so a cell can print a monogram tile instead of a broken image
    (docs/25).
    """
    raw = (ctx.query.get("ids", [""])[0] or "").strip()
    ids = [i.strip() for i in raw.split(",") if i.strip()][:200]
    if not ids:
        raise ApiError(400, "VALIDATION_FAILED", "Pass ?ids=prp_1,prp_2 (up to 200).")
    backend = getattr(ctx.store, "backend", None)
    out: dict[str, dict] = {}
    for pid in ids:
        prop = ctx.store.get("properties", ctx.tid, pid)
        if not prop:
            out[pid] = {"state": "not_found", "cover": None, "photo_count": 0,
                        "placeholder": md.placeholder({"id": pid}), "note": "No such property in this workspace."}
            continue
        if backend is None:
            out[pid] = {"state": "unavailable", "cover": None, "photo_count": 0,
                        "placeholder": md.placeholder(prop),
                        "note": "This instance runs on the file store; listing media lives in Postgres."}
            continue
        rows = backend.property_media(prop["id"], ctx.tid)
        photos = md.gallery(rows, photo_order=prop.get("photo_order"), pinned_cover_id=prop.get("pinned_cover_id", ""))
        cover = photos[0] if photos else None
        if cover and neon.storage_configured():
            try:
                cover["url"] = neon.presign_get(cover["key"], expires=900)
            except Exception:
                cover["url"] = ""
        state = "ok" if cover and cover.get("url") else ("none" if not photos else "unavailable")
        out[pid] = {
            "state": state,
            "cover": cover,
            "photo_count": len(photos),
            "readiness": md.readiness(rows),
            "placeholder": md.placeholder(prop),
            "note": {"ok": "", "none": "No photos uploaded yet.",
                     "unavailable": "Photos exist but this instance has no storage credentials to sign a URL."}[state],
        }
    return {"covers": out, "count": len(out)}


@route("GET", r"/api/v1/properties/(?P<pid>[\w]+)/units")
def property_units(ctx: Ctx) -> dict:
    at = now()
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    rows = [ivt.describe(u, at) for u in ctx.store.all("units", ctx.tid) if u.get("property_id") == prop["id"]]
    rows.sort(key=lambda u: (int(u.get("floor") or 0), str(u.get("unit_no") or "")))
    deals = {d["id"]: d for d in ctx.store.all("deals", ctx.tid)}
    for row in rows:
        held = (row.get("block") or {}).get("deal_id") or (row.get("booking") or {}).get("deal_id")
        row["deal_title"] = (deals.get(held) or {}).get("title", "")
    return {"property": {k: prop.get(k) for k in ("id", "title", "city", "locality", "type", "price", "rera")},
            "units": rows, "summary": ivt.summarize(rows, at), "block_hours_default": ivt.DEFAULT_BLOCK_HOURS}


@route("POST", r"/api/v1/properties/(?P<pid>[\w]+)/units")
def property_units_create(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    unit_no = ctx.body_str("unit_no", 16, required=True).upper()
    if any(u.get("property_id") == prop["id"] and u.get("unit_no") == unit_no for u in ctx.store.all("units", ctx.tid)):
        raise ApiError(409, "DUPLICATE_UNIT", f"Unit {unit_no} already exists in this property.")
    unit = ctx.store.insert("units", ctx.tid, {
        "property_id": prop["id"],
        "unit_no": unit_no,
        "label": f"{prop['title']} · {unit_no}",
        "floor": ctx.body_int("floor", 0, 0, 60),
        "type": ctx.body_str("type", 24) or prop.get("type", ""),
        "area_sqft": ctx.body_int("area_sqft", 0, 0, 100_000),
        "price": ctx.body_int("price", int(prop.get("price") or 0), 0, 10**11),
        "facing": ctx.body_str("facing", 12),
        "status": "available",
        "history": [{"event": "created", "at": now(), "by": ctx.user.get("id", "")}],
    }, prefix="unt")
    ctx.audit("unit.created", unit["id"], {"property_id": prop["id"], "unit_no": unit_no})
    return {"unit": ivt.describe(unit, now())}


@route("POST", r"/api/v1/properties/(?P<pid>[\w]+)/units/bulk")
def property_units_bulk(ctx: Ctx) -> dict:
    """Generate a floor grid in one call, skipping unit numbers that already exist."""
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    try:
        grid = ivt.generate_units(
            floors=ctx.body_int("floors", 4, 1, 60),
            per_floor=ctx.body_int("per_floor", 4, 1, 12),
            base_price=ctx.body_int("base_price", int(prop.get("price") or 0), 1, 10**11),
            price_step=ctx.body_int("price_step", 0, 0, 10**9),
            start_floor=ctx.body_int("start_floor", 1, 0, 60),
            type_=ctx.body_str("type", 24) or str(prop.get("type") or ""),
            area_sqft=ctx.body_int("area_sqft", int(prop.get("size_sqft") or 0), 0, 100_000),
        )
    except ivt.InventoryError as exc:
        raise ApiError(400, exc.code, exc.message, [exc.details])
    existing = {u.get("unit_no") for u in ctx.store.all("units", ctx.tid) if u.get("property_id") == prop["id"]}
    created = []
    for row in grid:
        if row["unit_no"] in existing:
            continue
        created.append(ctx.store.insert("units", ctx.tid, {
            **row, "property_id": prop["id"], "label": f"{prop['title']} · {row['unit_no']}",
            "history": [{"event": "created", "at": now(), "by": ctx.user.get("id", "")}],
        }, prefix="unt"))
    ctx.audit("unit.bulk_created", prop["id"], {"created": len(created), "skipped": len(grid) - len(created)})
    return {"created": len(created), "skipped": len(grid) - len(created),
            "units": [{"id": u["id"], "unit_no": u["unit_no"], "price": u["price"]} for u in created],
            "summary": ivt.summarize(ctx.store.all("units", ctx.tid))}


@route("GET", r"/api/v1/units")
def units_list(ctx: Ctx) -> dict:
    at = now()
    rows = [ivt.describe(u, at) for u in ctx.store.all("units", ctx.tid)]
    status = (ctx.query.get("status", [""])[0] or "").strip()
    if status:
        rows = [u for u in rows if u["status"] == status]
    return {"units": rows[: ctx.qint("limit", 200, 500)], "summary": ivt.summarize(rows, at),
            "counts": ivt.summarize(rows, at)["counts"]}


@route("PATCH", r"/api/v1/units/(?P<uid>[\w]+)")
def units_patch(ctx: Ctx) -> dict:
    """Withdraw or reinstate a unit, or correct its price. Never a fabricated sale."""
    ctx.require("write")
    unit = ctx.store.get("units", ctx.tid, ctx.params["uid"])
    if not unit:
        raise ApiError(404, "NOT_FOUND", "Unit not found.")
    patch: dict = {}
    if "status" in ctx.body:
        try:
            patch.update(ivt.apply_manual_status(unit, ctx.body_str("status", 20, required=True), ctx.user.get("id", ""), now()))
        except ivt.InventoryError as exc:
            raise ApiError(409, exc.code, exc.message, [exc.details])
    if "price" in ctx.body:
        patch["price"] = ctx.body_int("price", int(unit.get("price") or 0), 0, 10**11)
    if "area_sqft" in ctx.body:
        patch["area_sqft"] = ctx.body_int("area_sqft", 0, 0, 100_000)
    if "facing" in ctx.body:
        patch["facing"] = ctx.body_str("facing", 12)
    if not patch:
        raise ApiError(400, "VALIDATION_FAILED", "Nothing to update.")
    upd = ctx.store.update("units", ctx.tid, unit["id"], patch)
    ctx.audit("unit.updated", unit["id"], {"changed": sorted(patch)})
    return {"unit": ivt.describe(upd or unit, now())}


@route("POST", r"/api/v1/units/(?P<uid>[\w]+)/block")
def units_block(ctx: Ctx) -> dict:
    ctx.require("write")
    deal_id_req = ctx.body_str("deal_id", 40, required=True)
    hours_req = ctx.body_int("hours", ivt.DEFAULT_BLOCK_HOURS, 1, ivt.MAX_BLOCK_HOURS)
    reason_req = ctx.body_str("reason", 120)
    with ctx.store.lock:
        unit = ctx.store.get("units", ctx.tid, ctx.params["uid"])
        if not unit:
            raise ApiError(404, "NOT_FOUND", "Unit not found.")
        deal = ctx.store.get("deals", ctx.tid, deal_id_req)
        if not deal:
            raise ApiError(404, "NOT_FOUND", "Deal not found.")
        try:
            patch = ivt.apply_block(unit, deal_id=deal["id"], contact_id=deal.get("contact_id", ""),
                                    by=ctx.user.get("id", ""), hours=hours_req,
                                    reason=reason_req, at=now())
        except ivt.InventoryError as exc:
            raise ApiError(409, exc.code, exc.message, [exc.details])
        upd = ctx.store.update("units", ctx.tid, unit["id"], patch)
        ctx.store.update("deals", ctx.tid, deal["id"], {"unit_id": unit["id"]})
    ctx.audit("unit.blocked", unit["id"], {"deal_id": deal["id"], "hours": patch["block"]["hours"]})
    return {"unit": ivt.describe(upd or unit, now()), "deal_id": deal["id"]}


@route("POST", r"/api/v1/units/(?P<uid>[\w]+)/release")
def units_release(ctx: Ctx) -> dict:
    ctx.require("write")
    deal_id_req = ctx.body_str("deal_id", 40)
    with ctx.store.lock:
        unit = ctx.store.get("units", ctx.tid, ctx.params["uid"])
        if not unit:
            raise ApiError(404, "NOT_FOUND", "Unit not found.")
        holder = (unit.get("block") or {}).get("deal_id") or (unit.get("booking") or {}).get("deal_id")
        # Releasing someone else's hold is a manager action, not an agent convenience.
        if holder and holder != deal_id_req and ctx.role not in ("owner", "admin", "manager"):
            raise ApiError(403, "FORBIDDEN_SCOPE", "Only a manager can release another deal's hold.")
        try:
            patch = ivt.apply_release(unit, by=ctx.user.get("id", ""), deal_id=deal_id_req, at=now())
        except ivt.InventoryError as exc:
            raise ApiError(409, exc.code, exc.message, [exc.details])
        upd = ctx.store.update("units", ctx.tid, unit["id"], patch)
        if holder:
            ctx.store.update("deals", ctx.tid, holder, {"unit_id": ""})
    ctx.audit("unit.released", unit["id"], {"was": holder or ""})
    return {"unit": ivt.describe(upd or unit, now())}


@route("POST", r"/api/v1/units/(?P<uid>[\w]+)/book")
def units_book(ctx: Ctx) -> dict:
    """Book against a token payment. Booking is what stops a second sale."""
    ctx.require("write")
    deal_id_req = ctx.body_str("deal_id", 40, required=True)
    tok_amt = float(ctx.body_int("token_amount", 0, 0, 10**11))
    with ctx.store.lock:
        unit = ctx.store.get("units", ctx.tid, ctx.params["uid"])
        if not unit:
            raise ApiError(404, "NOT_FOUND", "Unit not found.")
        deal = ctx.store.get("deals", ctx.tid, deal_id_req)
        if not deal:
            raise ApiError(404, "NOT_FOUND", "Deal not found.")
        try:
            patch = ivt.apply_booking(unit, deal_id=deal["id"], contact_id=deal.get("contact_id", ""),
                                      by=ctx.user.get("id", ""),
                                      token_amount=tok_amt, at=now())
        except ivt.InventoryError as exc:
            raise ApiError(409, exc.code, exc.message, [exc.details])
        if not patch:
            return {"unit": ivt.describe(unit, now()), "already": True}
        upd = ctx.store.update("units", ctx.tid, unit["id"], patch)
        ctx.store.update("deals", ctx.tid, deal["id"], {"unit_id": unit["id"]})
    ctx.audit("unit.booked", unit["id"], {"deal_id": deal["id"], "token_amount": patch["booking"]["token_amount"]})
    return {"unit": ivt.describe(upd or unit, now())}


@route("GET", r"/api/v1/units/audit")
def units_audit(ctx: Ctx) -> dict:
    """Who held, booked or sold which unit, newest first."""
    ctx.require("write")
    events = []
    for unit in ctx.store.all("units", ctx.tid):
        for entry in unit.get("history") or []:
            events.append({"unit_id": unit["id"], "unit_no": unit.get("unit_no"), "label": unit.get("label"),
                           **entry})
    events.sort(key=lambda e: -int(e.get("at") or 0))
    return {"events": events[:100], "count": len(events)}


@route("GET", r"/api/v1/crm/summary")
def crm_summary(ctx: Ctx) -> dict:
    """The desk's morning numbers: what needs a reply, a visit, a document."""
    deals = ctx.store.all("deals", ctx.tid)
    visits = ctx.store.all("site_visits", ctx.tid)
    contacts = ctx.store.all("contacts", ctx.tid)
    today_start = ctx.now - (ctx.now % 86400)
    upcoming = [
        v for v in visits
        if v.get("status") in ("scheduled", "confirmed") and int(v.get("scheduled_at") or 0) >= ctx.now
    ]
    upcoming.sort(key=lambda v: int(v.get("scheduled_at") or 0))
    by_stage = {stage: 0 for stage in vt.STAGES}
    by_stage[vt.LOST] = 0
    for d in deals:
        by_stage[d.get("stage", "new")] = by_stage.get(d.get("stage", "new"), 0) + 1
    blocked = []
    for d in deals:
        try:
            vt.check_transition(d, _next_stage(d.get("stage", "new")), "owner", visits)
        except vt.StageError as exc:
            blocked.append({"deal_id": d["id"], "title": d.get("title"), "code": exc.code, "detail": exc.message})
    pipeline_value = sum(int(d.get("value") or 0) for d in deals if d.get("stage") not in ("won", vt.LOST))
    won_value = sum(int(d.get("value") or 0) for d in deals if d.get("stage") == "won")
    return {
        "contacts": len(contacts),
        "new_contacts_today": len([c for c in contacts if int(c.get("created_at") or 0) >= today_start]),
        "deals": len(deals),
        "by_stage": by_stage,
        "pipeline_value": pipeline_value,
        "won_value": won_value,
        "visits_upcoming": len(upcoming),
        "next_visits": [_visit_payload(ctx, v) for v in upcoming[:5]],
        "visits_today": len([v for v in visits if int(v.get("scheduled_at") or 0) >= today_start and int(v.get("scheduled_at") or 0) < today_start + 86400]),
        "stage_blockers": blocked[:5],
    }


def _next_stage(stage: str) -> str:
    order = list(vt.STAGES)
    if stage in order and order.index(stage) + 1 < len(order):
        return order[order.index(stage) + 1]
    return stage


def _epoch(raw) -> int | None:
    """Accept epoch seconds or an ISO-8601 string; None when neither parses."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.isdigit():
            return int(text)
        try:
            return int(datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


@route("GET", r"/api/v1/compliance/runs")
def compliance_runs(ctx: Ctx) -> dict:
    """Recent nightly-sweep results.

    The sweep is platform-wide, so its numbers are aggregates over every
    workspace: the route is gated on `manage_tenant` and returns counts, never
    another tenant's records.
    """
    ctx.require("manage_tenant")
    backend = getattr(ctx.store, "backend", None)
    if backend is None:
        raise ApiError(503, "PIPELINE_UNAVAILABLE", "Scheduled-run records live in Postgres; this instance is on the file store.")
    runs = backend.compliance_runs(limit=ctx.qint("limit", 20, 100))
    latest = runs[0] if runs else None
    return {
        "runs": runs,
        "count": len(runs),
        "latest": latest,
        "healthy": bool(latest and latest.get("status") == "ok"),
    }


@route("GET", r"/api/v1/webhooks/events")
def webhook_events(ctx: Ctx) -> dict:
    """Verified payment webhook events for this workspace.

    Only events whose payment `notes` carried this tenant_id are returned; an
    event the function could not attribute belongs to no workspace and is
    therefore visible to none.
    """
    ctx.require("manage_tenant")
    backend = getattr(ctx.store, "backend", None)
    if backend is None:
        raise ApiError(503, "PIPELINE_UNAVAILABLE", "Webhook events live in Postgres; this instance is on the file store.")
    events = backend.webhook_events(ctx.tid, limit=ctx.qint("limit", 50, 200))
    for e in events:
        if e.get("amount") is not None:
            e["amount_major"] = f"{int(e['amount']) / 100:.2f}"   # paise → display units
    return {"events": events, "count": len(events)}


# ---------------------------------------------------------------------------
# Operator secret intake — the one place the web tier writes a credential.
#
# Why it exists: the credential file is mode 0600 and git-ignored, which is
# precisely why a browser-based file viewer often cannot edit it. This pair of
# routes lets an authenticated **owner** drop a single named key into
# `.neon/api_key` from the app itself (`/keysetup.html`), without the value ever
# being echoed into a response, a log line, or an audit record. The file is not
# `.env.local` on purpose: the app process never loads it.
#
# The blast radius is deliberately tiny: one key name, a strict character
# allowlist (no newlines → no injecting other variables), atomic replace, mode
# forced to 0600, and `manage_tenant`-class authority required.
# ---------------------------------------------------------------------------
@route("GET", r"/api/v1/setup/neon-key", min_role="owner")
def setup_neon_key_status(ctx: Ctx) -> dict:
    from . import keysetup

    path = keysetup.secrets_path()
    value = keysetup.read_cli_key()
    rel = str(path.relative_to(keysetup.ROOT)) if path.is_relative_to(keysetup.ROOT) else ".secrets/neon-api-key"
    bpath = keysetup.branch_url_path()
    bval = keysetup.read_branch_url()
    brel = str(bpath.relative_to(keysetup.ROOT)) if bpath.is_relative_to(keysetup.ROOT) else ".secrets/neon-test-branch-url"
    return {
        "configured": bool(value),
        "fingerprint": keysetup.fingerprint(value) if value else "",
        "masked": keysetup.mask(value) if value else "",
        "file": rel,
        "mode": "0600" if path.exists() else "absent",
        "stored_in_runtime_env": bool(keysetup.read_value("NEON_API_KEY", keysetup.env_local_path())),
        "branch_configured": bool(bval),
        "branch_fingerprint": keysetup.fingerprint(bval) if bval else "",
        "branch_masked": keysetup.mask_dsn(bval) if bval else "",
        "branch_file": brel,
        "branch_mode": "0600" if bpath.exists() else "absent",
    }


@route("POST", r"/api/v1/setup/neon-key", min_role="owner")
def setup_neon_key_write(ctx: Ctx) -> dict:
    from . import keysetup

    # A write to the credential file is worth throttling on its own: an attacker
    # with a stolen owner session should not be able to slot in a key and then
    # drive deploys, at machine speed.
    allowed, _remaining, retry = sec.LIMITER.check("setup_key", ctx.client_ip)
    if not allowed:
        raise ApiError(429, "RATE_LIMITED", f"Too many attempts. Retry in {retry}s.")

    # Check the confirmation *before* touching the file on disk.
    ctx.require_step_up("secret_write", "neon_api_key")

    raw = str(ctx.body.get("api_key", "")).strip()
    branch_raw = str(ctx.body.get("branch_url", "")).strip()
    if not raw and not branch_raw:
        raise ApiError(400, "VALIDATION_FAILED", "Provide 'api_key' and/or 'branch_url'.")

    out: dict = {"ok": True}
    if raw:
        try:
            report = keysetup.write_cli_key(raw)
        except ValueError as exc:
            raise ApiError(400, "VALIDATION_FAILED", str(exc))
        except OSError as exc:
            raise ApiError(500, "SECRET_WRITE_FAILED", f"Could not write the file ({exc.__class__.__name__}).")
        path = keysetup.secrets_path()
        rel = str(path.relative_to(keysetup.ROOT)) if path.is_relative_to(keysetup.ROOT) else ".secrets/neon-api-key"
        ctx.audit(
            "setup.neon_api_key_set",
            report["fingerprint"],
            {"file": rel, "mode": report["mode"]},
        )
        out.update({
            "configured": True,
            "fingerprint": report["fingerprint"],
            "masked": report["masked"],
            "file": rel,
            "mode": report["mode"],
            "replaced": report["replaced"],
            "next": "scripts/neon-cli.sh deploy --env .env.local",
        })

    if branch_raw:
        try:
            breport = keysetup.write_branch_url(branch_raw)
        except ValueError as exc:
            raise ApiError(400, "VALIDATION_FAILED", str(exc))
        except OSError as exc:
            raise ApiError(500, "SECRET_WRITE_FAILED", f"Could not write branch file ({exc.__class__.__name__}).")
        bpath = keysetup.branch_url_path()
        brel = str(bpath.relative_to(keysetup.ROOT)) if bpath.is_relative_to(keysetup.ROOT) else ".secrets/neon-test-branch-url"
        ctx.audit(
            "setup.neon_branch_url_set",
            breport["fingerprint"],
            {"file": brel, "mode": breport["mode"]},
        )
        out.update({
            "branch_configured": True,
            "branch_fingerprint": breport["fingerprint"],
            "branch_masked": breport["masked"],
            "branch_file": brel,
            "branch_mode": breport["mode"],
            "branch_replaced": breport["replaced"],
        })
        if not raw:
            out.update({
                "fingerprint": breport["fingerprint"],
                "file": brel,
                "mode": breport["mode"],
                "replaced": breport["replaced"],
            })

    return out


# ==========================================================================
# Lead scraping / extraction (compliance-gated)
# ==========================================================================
SCRAPE_SOURCES = {
    "99acres": {"mode": "portal_api", "allowed": True, "note": "Requires a signed data-sharing agreement; portal API only."},
    "magicbricks": {"mode": "portal_api", "allowed": True, "note": "Portal API / lead-push webhook."},
    "meta_lead_ads": {"mode": "webhook", "allowed": True, "note": "Webhook + Graph API, 60s SLA."},
    "google_ads": {"mode": "lead_form_api", "allowed": True, "note": "Lead form API + offline conversion upload."},
    "public_web": {"mode": "crawler", "allowed": True, "note": "Robots-respecting crawl of business listings; no login walls, no bypassing protections."},
    "linkedin": {"mode": "blocked", "allowed": False, "note": "Blocked: ToS forbids scraping; use the partner API."},
    "magicbricks_scrape": {"mode": "blocked", "allowed": False, "note": "Blocked: direct scraping violates the portal's ToS."},
}


@route("POST", r"/api/v1/leads/scrape")
def leads_scrape(ctx: Ctx) -> dict:
    ctx.require("write")
    feature = ctx.tenant.get("features", {}).get("lead_scraper", False)
    if not feature:
        raise ApiError(403, "FEATURE_DISABLED", "Lead extraction is disabled for this workspace. Enable it in Owner Controls.")
    source = ctx.body_str("source", 40, required=True)
    meta = SCRAPE_SOURCES.get(source)
    if not meta:
        raise ApiError(400, "VALIDATION_FAILED", "Unknown source.")
    if not meta["allowed"]:
        # Compliance gate: refuse unlawful extraction, log the attempt.
        ctx.audit("scrape.blocked", source, {"reason": meta["note"]})
        raise ApiError(451, "COMPLIANCE_BLOCKED", meta["note"])
    limit = ctx.body_int("limit", 5, 1, 10)
    keyword = ctx.body_str("keyword", 120) or "plots"
    city = ctx.body_str("city", 60) or "Nanded"
    import random as _r

    rng = _r.Random(hash((source, keyword, city)) & 0xFFFFFFFF)
    first = ["Aarav", "Isha", "Rohan", "Meera", "Kabir", "Ananya", "Vivaan", "Diya", "Aditya", "Pooja"]
    last = ["Kale", "Shinde", "Bhatt", "Rane", "Shaikh", "Naik", "Wagh", "Pawar"]
    candidates = []
    for i in range(limit):
        candidates.append(
            {
                "id": nid("cand"),
                "name": f"{rng.choice(first)} {rng.choice(last)}",
                "phone_masked": f"+9198•••{rng.randint(1000, 9999)}",
                "city": city,
                "interest": rng.choice(["2BHK", "plot 2400 sqft", "villa", "commercial"]),
                "source": source,
                "lawful_basis": "public business listing" if source == "public_web" else "inbound enquiry via partner API",
                "consent_status": "pending",
                "dedupe": "clear",
                "next_step": "capture consent before any outbound contact",
            }
        )
    job = ctx.store.insert(
        "runs",
        ctx.tid,
        {
            "type": "scrape",
            "source": source,
            "mode": meta["mode"],
            "keyword": keyword,
            "city": city,
            "count": len(candidates),
            "candidates": candidates,
            "policy": meta["note"],
            "status": "complete",
        },
        prefix="job",
    )
    ctx.audit("scrape.executed", job["id"], {"source": source, "count": len(candidates)})
    return {"job": job, "candidates": candidates, "note": "Candidates are NOT contactable until consent is captured (DPDP §6)."}


@route("GET", r"/api/v1/leads/jobs")
def leads_jobs(ctx: Ctx) -> dict:
    jobs = [r for r in ctx.store.all("runs", ctx.tid) if r.get("type") == "scrape"]
    return {"jobs": sorted(jobs, key=lambda j: -j["created_at"])}


@route("POST", r"/api/v1/leads/import")
def leads_import(ctx: Ctx) -> dict:
    ctx.require("write")
    rows = ctx.body.get("rows") or []
    if not isinstance(rows, list):
        raise ApiError(400, "VALIDATION_FAILED", "rows must be a list.")
    accepted, rejected = [], []
    for row in rows[:500]:
        if not isinstance(row, dict):
            rejected.append({"row": row, "reason": "not an object"})
            continue
        name = sec.clean(row.get("name"), 80)
        phone = sec.clean(row.get("phone"), 20)
        if not name or not sec.valid_phone(phone):
            rejected.append({"name": name, "reason": "invalid name/phone"})
            continue
        if comp_mod.is_suppressed(ctx.store, ctx.tid, phone=phone, email=sec.clean(row.get("email"), 254)):
            rejected.append({"name": name, "reason": "suppressed_by_erasure"})
            continue
        if sec.find_by_blind_index(ctx.store, "contacts", ctx.tid, "phone_hash", phone):
            rejected.append({"name": name, "reason": "duplicate"})
            continue
        c = ctx.store.insert(
            "contacts",
            ctx.tid,
            {
                "name": name,
                "phone_enc": sec.seal(phone, aad=ctx.tid),
                "phone_hash": sec.blind_index(phone, tenant_id=ctx.tid),
                "email_enc": sec.seal(sec.clean(row.get("email"), 254), aad=ctx.tid),
                "email_hash": sec.blind_index(sec.clean(row.get("email"), 254), tenant_id=ctx.tid),
                "city": sec.clean(row.get("city"), 60),
                "source": sec.clean(row.get("source", "CSV import"), 40),
                "stage": "new",
                "score": 0,
                "requirement": sec.clean(row.get("requirement"), 200),
                "consent": {
                    "status": "pending",
                    "purpose": "import",
                    "channel": "csv",
                    "captured_at": now(),
                    "notice_version": "v3.1",
                    "evidence": "pending",
                },
                "tags": [],
                "last_activity_at": now(),
            },
            prefix="cnt",
        )
        accepted.append(c["id"])
    ctx.audit("leads.imported", "", {"accepted": len(accepted), "rejected": len(rejected)})
    return {"accepted": len(accepted), "rejected": rejected[:50], "note": "Imported leads are not contactable until consent is recorded."}


# ==========================================================================
# Portal ingestion + speed-to-lead (docs/18 §4, §8 P1)
#
# The public endpoint is authenticated by a hashed per-tenant ingest key, not a
# session: portals cannot hold a cookie or a CSRF token. Everything about the
# lead — dedupe, source attribution, assignment, the SLA clock — is decided
# here, because a portal will happily retry the same enquiry five times.
# ==========================================================================
def _ingest_key_tenant(ctx: Ctx, raw_key: str) -> tuple[dict, dict] | None:
    """Resolve an ingest key to (key record, tenant). Constant-time by lookup."""
    if not raw_key or len(raw_key) < 16 or len(raw_key) > 200:
        return None
    digest = sec.blind_index(raw_key)
    for key in ctx.store.db["ingest_keys"]:
        if not key.get("active", True):
            continue
        stored = key.get("key_hash", "")
        if stored and sec.hmac.compare_digest(stored, digest):
            tenant = next((t for t in ctx.store.db["tenants"] if t["id"] == key.get("tenant_id")), {})
            if tenant and not tenant.get("kill_switch"):
                return key, tenant
    return None


@route("POST", r"/api/v1/ingest/(?P<source>[a-z0-9\-]+)", public=True, bucket="public")
def ingest_portal(ctx: Ctx) -> dict:
    """Public: a portal posts a lead. `X-Ingest-Key` (or ?key=) authorises it."""
    raw_key = ctx.headers.get("x-ingest-key", "") or (ctx.query.get("key", [""])[0] or "")
    resolved = _ingest_key_tenant(ctx, raw_key)
    if not resolved:
        # Same answer for a bad key and a dead tenant: no tenant enumeration.
        raise ApiError(401, "INGEST_UNAUTHORIZED", "Invalid or revoked ingest key.")
    key, tenant = resolved
    tid = tenant["id"]

    try:
        parsed = ing.normalize(ctx.params["source"], ctx.body if isinstance(ctx.body, dict) else {})
    except ing.IngestError as exc:
        raise ApiError(422, exc.code, exc.message, [exc.details])
    lead = parsed["lead"]

    existing = (sec.find_by_blind_index(ctx.store, "contacts", tid, "phone_hash", lead["phone"]) or [None])[0]
    now_ts = now()

    if existing:
        patch = ing.merge_duplicate(existing, lead, now_ts)
        patch["last_activity_at"] = now_ts
        ctx.store.update("contacts", tid, existing["id"], patch)
        ctx.store.audit(tid, f"ingest:{key.get('label', '')}", "lead.duplicate", existing["id"],
                        {"source": lead["source_slug"], "filled": sorted(patch.keys())})
        return {
            "ok": True,
            "duplicate": True,
            "contact_id": existing["id"],
            "merged_fields": [k for k in patch if k not in ("sources", "tags", "last_activity_at")],
            "sources": patch.get("sources"),
            "source": lead["source"],
            "note": "Existing contact: source appended, blanks filled, nothing overwritten.",
        }

    users = ctx.store.all("users", tid)
    open_deals = [d for d in ctx.store.all("deals", tid) if d.get("stage") not in ("won", "lost")]
    load: dict[str, int] = {}
    for d in open_deals:
        owner = d.get("owner_id")
        if owner:
            load[owner] = load.get(owner, 0) + 1
    owner_id = ing.assign_owner(users, load) or ""
    window = ing.sla_window(lead["source_slug"], tenant)

    contact = ctx.store.insert(
        "contacts",
        tid,
        {
            "name": lead["name"],
            "phone_enc": sec.seal(lead["phone"], aad=tid),
            "phone_hash": sec.blind_index(lead["phone"], tenant_id=tid),
            "email_enc": sec.seal(lead["email"], aad=tid) if lead["email"] else "",
            "email_hash": sec.blind_index(lead["email"], tenant_id=tid) if lead["email"] else "",
            "city": lead["city"],
            "locality": lead["locality"],
            "source": lead["source"],
            "sources": [{"slug": lead["source_slug"], "label": lead["source"], "campaign": lead["campaign"],
                         "external_id": lead["external_id"], "at": now_ts}],
            "campaign": lead["campaign"],
            "external_id": lead["external_id"],
            "ingest_key_id": key["id"],
            "stage": "new",
            "score": 0,
            "intent": "unknown",
            "requirement": lead["requirement"],
            "property_type": lead["property_type"],
            "budget_min": lead["budget_min"],
            "budget_max": lead["budget_max"],
            "budget_text": lead["budget_text"],
            # The buyer submitted a form on the portal asking to be contacted;
            # that is the consent basis, recorded with the portal submission as
            # evidence and a distinct notice_version so it is never confused
            # with our own notice. Sending is still gated by quiet hours.
            "consent": {
                "status": "granted",
                "purpose": "portal enquiry",
                "channel": lead["source_slug"],
                "captured_at": now_ts,
                "evidence": f"portal:{lead['source_slug']}:{lead['external_id'] or 'n/a'}",
                "notice_version": "portal-tos-v1",
            },
            "owner_id": owner_id,
            "assigned_at": now_ts if owner_id else None,
            "first_response_at": None,
            "sla": {"seconds": window, "due_at": now_ts + window, "source": lead["source_slug"]},
            "tags": [lead["source_slug"], "portal"],
            "value": lead["budget_max"] or 0,
            "created_at": now_ts,
            "last_activity_at": now_ts,
        },
        prefix="cnt",
    )
    ctx.store.insert(
        "activities",
        tid,
        {"contact_id": contact["id"], "type": "lead_captured", "text": f"{lead['source']} lead captured",
         "actor": f"ingest:{key.get('label', '')}", "at": now_ts},
        prefix="act",
    )
    ctx.store.update("ingest_keys", tid, key["id"], {
        "last_used_at": now_ts,
        "count": int(key.get("count") or 0) + 1,
    })
    ctx.store.audit(tid, f"ingest:{key.get('label', '')}", "lead.ingested", contact["id"],
                    {"source": lead["source_slug"], "assigned": bool(owner_id), "sla_seconds": window})
    return {
        "ok": True,
        "duplicate": False,
        "contact_id": contact["id"],
        "assigned_to": owner_id,
        "sla": {"seconds": window, "due_at": now_ts + window},
        "warnings": parsed["warnings"],
        "source": lead["source"],
    }


# ==========================================================================
# Channel partners (docs/18 §4 #3, §6 #5)
#
# A partner is a login with role `channel_partner`, reachable only through
# /api/v1/cp/*. Registration is a claim with a 90-day clock; the house's own
# leads are not claimable; commission is shown from the deal's ledger with an
# explicit basis, and only a manager can move a registration or a payout.
# ==========================================================================
def _cp_profile(user: dict) -> dict:
    return {
        "user_id": user["id"],
        "name": user["name"],
        "firm": user.get("cp_firm", user["name"]),
        "email": user["email"],
        "commission_pct": cpm.clamp_pct(user.get("cp_commission_pct")),
        "protection_days": int(user.get("cp_protection_days") or cpm.PROTECTION_DAYS),
        "rera": user.get("cp_rera", ""),
        "active": user.get("active", True),
    }


def _require_partner(ctx: Ctx) -> None:
    if ctx.role != "channel_partner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "This endpoint belongs to the partner portal.")


def _cp_registrations(ctx: Ctx, cp_id: str) -> list[dict]:
    return [r for r in ctx.store.all("cp_registrations", ctx.tid) if r.get("cp_user_id") == cp_id]


def _cp_contact_ids(ctx: Ctx, cp_id: str) -> set[str]:
    at = now()
    live = {
        r["contact_id"]
        for r in _cp_registrations(ctx, cp_id)
        if cpm.registration_state(r, at) in ("approved", "pending")
    }
    # also anything credited to this partner on the contact itself
    live |= {c["id"] for c in ctx.store.all("contacts", ctx.tid) if c.get("cp_user_id") == cp_id}
    return live


@route("GET", r"/api/v1/cp/me")
def cp_me(ctx: Ctx) -> dict:
    _require_partner(ctx)
    regs = _cp_registrations(ctx, ctx.user["id"])
    at = now()
    visits = ctx.store.all("site_visits", ctx.tid)
    contact_ids = _cp_contact_ids(ctx, ctx.user["id"])
    return {
        "partner": _cp_profile(ctx.user),
        "counts": {
            "registrations": len(regs),
            "approved": len([r for r in regs if cpm.registration_state(r, at) == "approved"]),
            "pending": len([r for r in regs if cpm.registration_state(r, at) == "pending"]),
            "visits_credited": cpm.credit_visit_count(visits, contact_ids),
        },
    }


@route("POST", r"/api/v1/cp/register")
def cp_register(ctx: Ctx) -> dict:
    """A partner claims a buyer. Idempotent for the same partner, refused for
    one who is already claimed or who is already the house's own lead."""
    _require_partner(ctx)
    name = ctx.body_str("name", 80, required=True)
    phone_raw = ctx.body_str("phone", 20, required=True)
    phone = ing.normalize_phone(phone_raw)
    if not phone:
        raise ApiError(400, "VALIDATION_FAILED", "Enter a valid 10-digit Indian mobile number.")
    at = now()
    phone_hash = sec.blind_index(phone, tenant_id=ctx.tid)
    contact = (sec.find_by_blind_index(ctx.store, "contacts", ctx.tid, "phone_hash", phone) or [None])[0]
    mine = next((r for r in _cp_registrations(ctx, ctx.user["id"]) if contact and r.get("contact_id") == contact["id"]), None)
    theirs = next((r for r in ctx.store.all("cp_registrations", ctx.tid)
                   if contact and r.get("contact_id") == contact["id"] and r.get("cp_user_id") != ctx.user["id"]), None)

    verdict = cpm.decide(cp_id=ctx.user["id"], contact=contact, registration=mine,
                         other_registration=theirs, at=at)
    if verdict["outcome"] == "duplicate":
        return {"outcome": "duplicate", "registration": mine, "message": verdict["reason"]}
    if verdict["outcome"] == "conflict":
        raise ApiError(409, "CP_LEAD_CONFLICT", verdict["reason"], [verdict])
    if verdict["outcome"] == "existing_lead":
        raise ApiError(409, "CP_LEAD_ALREADY_IN_PIPELINE", verdict["reason"], [verdict])

    if not contact:
        users = [u for u in ctx.store.all("users", ctx.tid)]
        load: dict[str, int] = {}
        for d in ctx.store.all("deals", ctx.tid):
            if d.get("stage") not in ("won", "lost") and d.get("owner_id"):
                load[d["owner_id"]] = load.get(d["owner_id"], 0) + 1
        owner_id = ing.assign_owner([u for u in users if u["role"] in ("agent", "manager")], load)
        contact = ctx.store.insert("contacts", ctx.tid, {
            "name": name,
            "phone_enc": sec.seal(phone, aad=ctx.tid),
            "phone_hash": phone_hash,
            "email_enc": "",
            "email_hash": "",
            "city": ctx.body_str("city", 60),
            "source": "Channel partner",
            "sources": [{"slug": "channel_partner", "label": _cp_profile(ctx.user)["firm"],
                         "campaign": "", "external_id": "", "at": at}],
            "stage": "new",
            "score": 0,
            "intent": "unknown",
            "requirement": ctx.body_str("requirement", 200),
            "budget_min": ctx.body_int("budget_min", 0, 0, 10**10) or None,
            "budget_max": ctx.body_int("budget_max", 0, 0, 10**10) or None,
            "owner_id": owner_id,
            # A partner's attestation is the consent *basis*, recorded as such and
            # distinguishable from our own notice. Messaging stays blocked until
            # someone captures consent in the house's own words.
            "consent": {"status": "pending", "purpose": "channel partner referral",
                        "channel": "channel_partner", "captured_at": at,
                        "evidence": f"cp:{ctx.user['id']}", "notice_version": "cp-attestation-v1"},
            "cp_user_id": ctx.user["id"],
            "tags": ["channel_partner"],
            "value": ctx.body_int("budget_max", 0, 0, 10**10) or 0,
            "created_at": at,
            "last_activity_at": at,
        }, prefix="cnt")

    reg = ctx.store.insert("cp_registrations", ctx.tid, {
        "cp_user_id": ctx.user["id"],
        "cp_name": _cp_profile(ctx.user)["firm"],
        "contact_id": contact["id"],
        "contact_name": contact["name"],
        "status": "pending",
        "protection_until": None,
        "created_at": at,
    }, prefix="cpr")
    ctx.store.update("contacts", ctx.tid, contact["id"], {"cp_registration_id": reg["id"], "cp_user_id": ctx.user["id"]})
    ctx.audit("cp.registration_created", reg["id"], {"contact_id": contact["id"], "cp": ctx.user["id"]})
    return {"outcome": "created", "registration": reg, "contact_id": contact["id"],
            "message": "Registration submitted for approval. Once approved the buyer is protected for "
                       f"{_cp_profile(ctx.user)['protection_days']} days."}


@route("GET", r"/api/v1/cp/leads")
def cp_leads(ctx: Ctx) -> dict:
    _require_partner(ctx)
    at = now()
    visits = ctx.store.all("site_visits", ctx.tid)
    deals = {d.get("contact_id"): d for d in ctx.store.all("deals", ctx.tid)}
    contacts = {c["id"]: c for c in ctx.store.all("contacts", ctx.tid)}
    rows = []
    for reg in sorted(_cp_registrations(ctx, ctx.user["id"]), key=lambda r: -int(r.get("created_at") or 0)):
        state = cpm.registration_state(reg, at)
        contact = contacts.get(reg.get("contact_id"), {})
        deal = deals.get(reg.get("contact_id"), {})
        row = {
            "registration_id": reg["id"],
            "contact_id": reg.get("contact_id"),
            "name": reg.get("contact_name") or contact.get("name"),
            "phone": mask_phone(sec.unseal(contact.get("phone_enc", ""), aad=ctx.tid)),
            "status": state,
            "created_at": reg.get("created_at"),
            "protection_until": reg.get("protection_until"),
            "days_left": max(0, (int(reg["protection_until"]) - at) // 86400) if reg.get("protection_until") else None,
            "reason": reg.get("reason", ""),
            "visits_credited": len([v for v in visits if v.get("contact_id") == reg.get("contact_id")
                                    and v.get("status") in ("visited", "revisit")]),
            "deal_stage": deal.get("stage", ""),
            "deal_value": deal.get("value"),
        }
        rows.append(row)
    return {"leads": rows, "count": len(rows), "partner": _cp_profile(ctx.user)}


@route("GET", r"/api/v1/cp/visits")
def cp_visits(ctx: Ctx) -> dict:
    _require_partner(ctx)
    contact_ids = _cp_contact_ids(ctx, ctx.user["id"])
    props = {p["id"]: p.get("title") for p in ctx.store.all("properties", ctx.tid)}
    rows = [
        {
            "visit_id": v["id"],
            "contact_name": v.get("contact_name") or "",
            "property": props.get(v.get("property_id"), ""),
            "scheduled_at": v.get("scheduled_at"),
            "status": v.get("status"),
            "credited": v.get("status") in ("visited", "revisit"),
        }
        for v in ctx.store.all("site_visits", ctx.tid)
        if v.get("contact_id") in contact_ids
    ]
    contact_names = {c["id"]: c["name"] for c in ctx.store.all("contacts", ctx.tid)}
    for row in rows:
        row["contact_name"] = contact_names.get(
            next((v["contact_id"] for v in ctx.store.all("site_visits", ctx.tid) if v["id"] == row["visit_id"]), ""), "")
    rows.sort(key=lambda r: -int(r.get("scheduled_at") or 0))
    return {"visits": rows, "credited": len([r for r in rows if r["credited"]])}


@route("GET", r"/api/v1/cp/earnings")
def cp_earnings(ctx: Ctx) -> dict:
    _require_partner(ctx)
    """Per-deal rows with an explicit basis: `recorded` (from the house's
    commission ledger) or `expected` (estimate at the partner's rate)."""
    profile = _cp_profile(ctx.user)
    contact_ids = _cp_contact_ids(ctx, ctx.user["id"])
    rows = cpm.earnings_rows(ctx.store.all("deals", ctx.tid), contact_ids, ctx.user["id"], profile["commission_pct"])
    return {"earnings": rows, "summary": cpm.earnings_summary(rows), "partner": profile}


@route("GET", r"/api/v1/cp/inventory")
def cp_inventory(ctx: Ctx) -> dict:
    """What a partner may sell — the stock, not the books.

    G4: a partner could register a buyer and watch a protection clock, but had no
    way to see what the brokerage actually had. The catalogue is opened along the
    narrowest line that still lets them sell: free stock only, no seller, no buyer,
    no invented commission rate. `partners.WITHHELD` is returned with every page so
    a partner is never guessing why a field is missing.
    """
    _require_partner(ctx)
    profile = _cp_profile(ctx.user)
    at = now()
    include_unavailable = (ctx.query.get("include_unavailable", ["0"])[0] or "0") in ("1", "true", "yes")

    units_by_prop: dict[str, list[dict]] = {}
    for u in ctx.store.all("units", ctx.tid):
        units_by_prop.setdefault(u.get("property_id"), []).append(u)

    gross_rate, rate_basis = cpm.implied_gross_rate(ctx.store.all("deals", ctx.tid))
    all_props = ctx.store.all("properties", ctx.tid)
    media_rows = _media_rows(ctx, all_props)
    rows = []
    for prop in all_props:
        units = units_by_prop.get(prop["id"], [])
        avail = cpm.availability(units)
        if not cpm.is_listable(prop, avail, include_unavailable=include_unavailable):
            continue
        row = cpm.inventory_row(prop, units, profile["commission_pct"], gross_rate, rate_basis)
        # a partner who advertises a listing with no photographs wastes a buyer's
        # visit; the count is public, the maker's upload is not
        mine_rows = media_rows.get(prop["id"], [])
        photos = _photo_state(ctx, prop, mine_rows)
        row["photos"] = photos
        row["cover"] = _cover_url(ctx, prop, photos, mine_rows)
        # law and polish are different questions: RERA decides whether it may be
        # advertised at all, photographs decide whether it is worth a visit
        row["marketing"]["photos"] = photos
        row["marketing"]["ready_to_show"] = bool(row["marketing"].get("ready")) and (
            photos["ready"] if photos.get("known") else True)
        rows.append(row)

    total_listed = len(rows)
    rows = cpm.apply_filters(
        rows,
        city=(ctx.query.get("city", [""])[0] or ""),
        ptype=(ctx.query.get("type", [""])[0] or ""),
        deal_kind=(ctx.query.get("deal_kind", [""])[0] or ""),
        min_price=_qnum(ctx, "min_price"),
        max_price=_qnum(ctx, "max_price"),
        q=(ctx.query.get("q", [""])[0] or ""),
    )
    # facets come from the *unfiltered* catalogue: a filter that hides its own
    # alternatives is how a filter panel becomes a dead end
    all_rows = rows if not any((ctx.query.get(k) for k in ("city", "type", "deal_kind", "q"))) else None
    if all_rows is None:
        all_rows = [cpm.inventory_row(p, units_by_prop.get(p["id"], []), profile["commission_pct"],
                                     gross_rate, rate_basis)
                    for p in ctx.store.all("properties", ctx.tid)
                    if cpm.is_listable(p, cpm.availability(units_by_prop.get(p["id"], [])),
                                       include_unavailable=include_unavailable)]
    facet = {
        "cities": _tally(r["city"] for r in all_rows),
        "types": _tally(r["type"] for r in all_rows),
        "deal_kinds": _tally(r["deal_kind"] for r in all_rows),
    }
    rows.sort(key=lambda r: (-(r["availability"]["available"]), -(r.get("price") or 0)))
    limit = ctx.qint("limit", 60, hi=200)
    return {
        "inventory": rows[:limit],
        "count": len(rows),
        "listed": total_listed,
        "truncated": len(rows) > limit,
        "facets": facet,
        "commission_basis": {"gross_rate_pct": gross_rate, "basis": rate_basis,
                             "your_share_pct": profile["commission_pct"]},
        "withheld": list(cpm.WITHHELD),
        "partner": profile,
        "as_of": at,
    }


def _media_rows(ctx: Ctx, props: list[dict]) -> dict[str, list[dict]]:
    """All media rows for these properties, in one query where the backend can.

    Forty listings × two queries each (one for the count, one for the cover) is how
    a catalogue becomes a six-second page over a pooled Postgres — so the bulk read
    happens once and every caller reads from this dict.
    """
    backend = getattr(ctx.store, "backend", None)
    if backend is None or not props:
        return {}
    ids = [p["id"] for p in props]
    bulk = getattr(backend, "property_media_bulk", None)
    if bulk:
        try:
            return bulk(ids, ctx.tid)
        except Exception:
            return {i: [] for i in ids}
    return {p["id"]: backend.property_media(p["id"], ctx.tid) for p in props}


def _photo_state(ctx: Ctx, prop: dict, rows: list[dict] | None = None) -> dict:
    """How many photos a listing has — without leaking who uploaded them.

    `unknown` is a first-class answer: an instance on the file store has no media
    table, and pretending every listing has zero photos would make the catalogue
    lie about stock it cannot see.
    """
    backend = getattr(ctx.store, "backend", None)
    if backend is None:
        return {"count": 0, "have": 0, "ready": False, "known": False,
                "note": "photo pipeline not attached to this instance"}
    if rows is None:
        try:
            rows = backend.property_media(prop["id"], ctx.tid)
        except Exception:
            return {"count": 0, "have": 0, "ready": False, "known": False,
                    "note": "photo pipeline unavailable"}
    ready = md.readiness(rows)
    return {"count": ready["have"], "have": ready["have"], "need": ready["need"],
            "ready": ready["ready"], "known": True, "note": ready["note"]}


def _cover_url(ctx: Ctx, prop: dict, photos: dict | None = None,
               rows: list[dict] | None = None) -> dict | None:
    """The signed URL of one cover, or None with the reason left to `photos`."""
    photos = photos or _photo_state(ctx, prop, rows)
    if not photos.get("count") or not neon.storage_configured():
        return None
    if rows is None:
        backend = getattr(ctx.store, "backend", None)
        if backend is None:
            return None
        try:
            rows = backend.property_media(prop["id"], ctx.tid)
        except Exception:
            return None
    cover = md.cover(rows, photo_order=prop.get("photo_order"), pinned_cover_id=prop.get("pinned_cover_id", ""))
    if not cover:
        return None
    try:
        url = neon.presign_get(cover.get("object_key"), expires=900)
    except Exception:
        return None
    return {"url": url, "variant": cover.get("variant"), "width": cover.get("width"),
            "height": cover.get("height"), "photo_id": md.photo_stem(cover.get("object_key", ""))}


def _signed_gallery(ctx: Ctx, prop: dict) -> list[dict]:
    backend = getattr(ctx.store, "backend", None)
    if backend is None or not neon.storage_configured():
        return []
    try:
        photos = md.gallery(
            backend.property_media(prop["id"], ctx.tid),
            photo_order=prop.get("photo_order"),
            pinned_cover_id=prop.get("pinned_cover_id", ""),
        )
    except Exception:
        return []
    out = []
    for photo in photos:
        try:
            photo["url"] = neon.presign_get(photo["key"], expires=900) if photo.get("key") else ""
        except Exception:
            photo["url"] = ""
        out.append(photo)
    return [p for p in out if p.get("url")]


@route("GET", r"/api/v1/cp/inventory/(?P<pid>[A-Za-z0-9_\-]+)")
def cp_inventory_item(ctx: Ctx) -> dict:
    """One property in full — units by label and state, and nothing about who holds them."""
    _require_partner(ctx)
    profile = _cp_profile(ctx.user)
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    units = [u for u in ctx.store.all("units", ctx.tid) if u.get("property_id") == prop["id"]]
    avail = cpm.availability(units)
    if not cpm.is_listable(prop, avail, include_unavailable=True):
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    gross_rate, rate_basis = cpm.implied_gross_rate(ctx.store.all("deals", ctx.tid))
    row = cpm.inventory_row(prop, units, profile["commission_pct"], gross_rate, rate_basis)
    # a unit is described by what a buyer asks about; `booking` and `history` are
    # deliberately not copied — they name the deal, the buyer and the person who
    # blocked it
    row["units"] = [
        {
            "id": u["id"],
            "unit_no": u.get("unit_no"),
            "label": u.get("label"),
            "type": u.get("type"),
            "floor": u.get("floor"),
            "facing": u.get("facing"),
            "area_sqft": u.get("area_sqft"),
            "price": u.get("price"),
            "status": u.get("status"),
            "sellable": u.get("status") in cpm.UNIT_FREE and not u.get("blocked_for_me"),
            "available_in_hours": u.get("block_hours_left") if u.get("status") == "blocked" else None,
        }
        for u in sorted(units, key=lambda x: (str(x.get("unit_no") or ""), x.get("id")))
    ]
    photos = _photo_state(ctx, prop)
    row["photos"] = photos
    row["cover"] = _cover_url(ctx, prop, photos)
    row["photos"]["gallery"] = _signed_gallery(ctx, prop)
    sellable = len([u for u in row["units"] if u["sellable"]])
    # a property with no unit list is sold whole — "0 sellable units" would be a lie
    # on the one page a partner reads before calling their buyer
    row["sellable_units"] = sellable if row["units"] else (1 if row["status"] == "available" else 0)
    row["sellable_as"] = "units" if row["units"] else "whole property"
    return {"property": row, "withheld": list(cpm.WITHHELD), "as_of": now()}


def _qnum(ctx: Ctx, key: str) -> int:
    try:
        return max(0, int(float(ctx.query.get(key, ["0"])[0] or 0)))
    except Exception:
        return 0


def _tally(values) -> list[dict]:
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[str(v)] = counts.get(str(v), 0) + 1
    return [{"value": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---- the house side: partners, approvals, payouts --------------------------
@route("GET", r"/api/v1/cp/partners")
def cp_partners(ctx: Ctx) -> dict:
    ctx.require("write")
    at = now()
    regs = ctx.store.all("cp_registrations", ctx.tid)
    visits = ctx.store.all("site_visits", ctx.tid)
    rows = []
    for u in ctx.store.all("users", ctx.tid):
        if u.get("role") != "channel_partner":
            continue
        mine = [r for r in regs if r.get("cp_user_id") == u["id"]]
        ids = {r["contact_id"] for r in mine if cpm.registration_state(r, at) in ("approved", "pending")}
        rows.append({
            **_cp_profile(u),
            "registrations": len(mine),
            "pending": len([r for r in mine if cpm.registration_state(r, at) == "pending"]),
            "approved": len([r for r in mine if cpm.registration_state(r, at) == "approved"]),
            "visits_credited": cpm.credit_visit_count(visits, ids),
        })
    return {"partners": rows, "count": len(rows)}


@route("POST", r"/api/v1/cp/partners")
def cp_partner_create(ctx: Ctx) -> dict:
    """Create a partner login. The password is generated, returned once, and
    stored only as a scrypt hash."""
    ctx.require("manage_users")
    email = ctx.body_str("email", 254, required=True).lower()
    if not sec.valid_email(email):
        raise ApiError(400, "VALIDATION_FAILED", "Invalid email.")
    if any(u["email"] == email for u in ctx.store.db["users"]):
        raise ApiError(409, "DUPLICATE_ACCOUNT", "An account with this email already exists.")
    name = ctx.body_str("name", 80) or email.split("@")[0]
    pct = cpm.clamp_pct(ctx.body.get("commission_pct"))
    days = ctx.body_int("protection_days", cpm.PROTECTION_DAYS, 1, 365)
    temp = "Cp-" + sec.secrets.token_urlsafe(9)
    u = ctx.store.insert("users", ctx.tid, {
        "name": name,
        "email": email,
        "role": "channel_partner",
        "password_hash": sec.hash_password(temp),
        "active": True,
        "mfa_enabled": False,
        "failed_attempts": 0,
        "cp_firm": ctx.body_str("firm", 80) or name,
        "cp_commission_pct": pct,
        "cp_protection_days": days,
        "cp_rera": ctx.body_str("rera", 40),
        "cp_phone": ing.normalize_phone(ctx.body_str("phone", 20)),
        "invited_by": ctx.user["id"],
    }, prefix="usr")
    ctx.audit("cp.partner_created", u["id"], {"firm": u.get("cp_firm"), "commission_pct": pct, "protection_days": days})
    return {"partner": _cp_profile(u), "temp_password": temp,
            "note": "Shown once. The partner signs in at /cp.html and should change it on first use."}


@route("PATCH", r"/api/v1/cp/partners/(?P<uid>[\w]+)")
def cp_partner_patch(ctx: Ctx) -> dict:
    ctx.require("manage_users")
    u = ctx.store.get("users", ctx.tid, ctx.params["uid"])
    if not u or u.get("role") != "channel_partner":
        raise ApiError(404, "NOT_FOUND", "Partner not found.")
    patch: dict = {}
    if "commission_pct" in ctx.body:
        patch["cp_commission_pct"] = cpm.clamp_pct(ctx.body.get("commission_pct"))
    if "protection_days" in ctx.body:
        patch["cp_protection_days"] = ctx.body_int("protection_days", cpm.PROTECTION_DAYS, 1, 365)
    if "active" in ctx.body:
        patch["active"] = bool(ctx.body.get("active"))
    if not patch:
        raise ApiError(400, "VALIDATION_FAILED", "Nothing to update.")
    upd = ctx.store.update("users", ctx.tid, u["id"], patch)
    ctx.audit("cp.partner_updated", u["id"], {"changed": sorted(patch)})
    return {"partner": _cp_profile(upd or u)}


@route("GET", r"/api/v1/cp/registrations")
def cp_registrations_list(ctx: Ctx) -> dict:
    ctx.require("write")
    at = now()
    rows = []
    for reg in ctx.store.all("cp_registrations", ctx.tid):
        rows.append({**reg, "state": cpm.registration_state(reg, at),
                     "days_left": max(0, (int(reg["protection_until"]) - at) // 86400) if reg.get("protection_until") else None})
    rows.sort(key=lambda r: (r["state"] != "pending", -int(r.get("created_at") or 0)))
    return {"registrations": rows, "count": len(rows)}


@route("POST", r"/api/v1/cp/registrations/(?P<rid>[\w]+)/decide")
def cp_registration_decide(ctx: Ctx) -> dict:
    """Approve (grant protection) or reject a claim. Manager and above only."""
    ctx.require("approve_cp")
    reg = ctx.store.get("cp_registrations", ctx.tid, ctx.params["rid"])
    if not reg:
        raise ApiError(404, "NOT_FOUND", "Registration not found.")
    decision = (ctx.body_str("decision", 12) or "").lower()
    if decision not in ("approve", "reject"):
        raise ApiError(400, "VALIDATION_FAILED", "decision must be 'approve' or 'reject'.")
    if reg.get("status") != "pending":
        raise ApiError(409, "ALREADY_DECIDED", f"This registration is already {reg.get('status')}.")
    partner = ctx.store.get("users", ctx.tid, reg.get("cp_user_id"))
    if not partner:
        raise ApiError(409, "PARTNER_MISSING", "The partner account no longer exists.")
    at = now()
    if decision == "approve":
        days = int(partner.get("cp_protection_days") or cpm.PROTECTION_DAYS)
        patch = {"status": "approved", "protection_until": cpm.protection_until(at, days),
                 "decided_by": ctx.user["id"], "decided_at": at, "reason": ""}
        ctx.store.update("contacts", ctx.tid, reg["contact_id"], {"cp_user_id": reg["cp_user_id"]})
    else:
        patch = {"status": "rejected", "decided_by": ctx.user["id"], "decided_at": at,
                 "reason": ctx.body_str("reason", 200) or "not eligible"}
        ctx.store.update("contacts", ctx.tid, reg["contact_id"], {"cp_user_id": ""})
    upd = ctx.store.update("cp_registrations", ctx.tid, reg["id"], patch)
    ctx.audit("cp.registration_" + ("approved" if decision == "approve" else "rejected"),
              reg["id"], {"contact_id": reg["contact_id"], "cp": reg["cp_user_id"]})
    return {"registration": upd}


@route("GET", r"/api/v1/cp/payouts")
def cp_payouts_list(ctx: Ctx) -> dict:
    """What the house owes its partners: pending and paid, per deal."""
    ctx.require("write")
    deals = {d["id"]: d for d in ctx.store.all("deals", ctx.tid)}
    partners = {u["id"]: u for u in ctx.store.all("users", ctx.tid) if u.get("role") == "channel_partner"}
    rows = []
    for deal in deals.values():
        commission = deal.get("commission") or {}
        cp_id = commission.get("channel_partner_id")
        if not cp_id:
            continue
        payout = deal.get("cp_payout") or {}
        rows.append({
            "deal_id": deal["id"],
            "title": deal.get("title"),
            "stage": deal.get("stage"),
            "partner": (partners.get(cp_id) or {}).get("cp_firm") or cp_id,
            "partner_id": cp_id,
            "amount": commission.get("channel_partner", 0),
            "status": payout.get("status", "pending" if deal.get("stage") == "won" else "not_due"),
            "reference": payout.get("reference", ""),
            "paid_at": payout.get("paid_at"),
        })
    rows.sort(key=lambda r: (r["status"] != "pending", -(r["amount"] or 0)))
    pending = round(sum(r["amount"] for r in rows if r["status"] == "pending"), 2)
    return {"payouts": rows, "pending_total": pending}


@route("POST", r"/api/v1/cp/payouts/(?P<deal_id>[\w]+)/pay")
def cp_payout_pay(ctx: Ctx) -> dict:
    """Mark a partner's commission on a closed deal as paid. Owner/admin only —
    paying money is not an agent action."""
    ctx.require("pay_partner")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["deal_id"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    if deal.get("stage") != "won":
        raise ApiError(409, "DEAL_NOT_CLOSED", "Commission is payable only on a won deal.")
    commission = deal.get("commission") or {}
    if not commission.get("channel_partner_id"):
        raise ApiError(409, "NO_PARTNER_CREDIT", "This deal has no channel-partner share recorded.")
    reference = ctx.body_str("reference", 60, required=True)
    at = now()
    payout = {"status": "paid", "paid_at": at, "reference": reference, "amount": commission.get("channel_partner", 0),
              "paid_by": ctx.user["id"]}
    ctx.store.update("deals", ctx.tid, deal["id"], {"cp_payout": payout,
                                                   "commission": {**commission, "payout_status": "paid"}})
    rows = [p for p in ctx.store.all("cp_payouts", ctx.tid) if p.get("deal_id") == deal["id"]]
    if rows:
        ctx.store.update("cp_payouts", ctx.tid, rows[0]["id"], {**payout})
    else:
        ctx.store.insert("cp_payouts", ctx.tid, {
            "cp_user_id": commission["channel_partner_id"], "deal_id": deal["id"],
            "amount": commission.get("channel_partner", 0), **payout,
        }, prefix="cpp")
    ctx.audit("cp.payout_paid", deal["id"], {"amount": payout["amount"], "reference": reference,
                                             "cp": commission["channel_partner_id"]})
    return {"deal_id": deal["id"], "payout": payout}


@route("GET", r"/api/v1/leads/sources")
def leads_sources(ctx: Ctx) -> dict:
    """The adapter catalogue, so a portal integration can be configured from the UI."""
    counts: dict[str, int] = {}
    for c in ctx.store.all("contacts", ctx.tid):
        for entry in (c.get("sources") or []):
            slug = entry.get("slug", "")
            if slug:
                counts[slug] = counts.get(slug, 0) + 1
    return {"sources": [{**s, "leads": counts.get(s["slug"], 0)} for s in ing.sources()]}


@route("GET", r"/api/v1/leads/sla")
def leads_sla(ctx: Ctx) -> dict:
    """Speed-to-lead board: who is inside their window and who has burned it."""
    ctx.require("write")
    board = ing.sla_board(ctx.store.all("contacts", ctx.tid), at=now())
    board["tenants_window_sec"] = ing.sla_window("", ctx.tenant)
    return board


@route("POST", r"/api/v1/leads/(?P<cid>[\w]+)/respond")
def leads_respond(ctx: Ctx) -> dict:
    """Record the first human touch (a call made, a WhatsApp sent elsewhere)."""
    ctx.require("write")
    contact = ctx.store.get("contacts", ctx.tid, ctx.params["cid"])
    if not contact:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    if contact.get("first_response_at"):
        return {"contact_id": contact["id"], "already_responded": True,
                "first_response_at": contact["first_response_at"],
                "sla": ing.sla_state(contact, now())}
    at = now()
    updated = ctx.store.update("contacts", ctx.tid, contact["id"], {
        "first_response_at": at,
        "responded_by": ctx.user.get("id", ""),
        "responded_channel": ctx.body_str("channel", 20) or "call",
    })
    state = ing.sla_state(updated or contact, at)
    ctx.audit("lead.first_response", contact["id"], {"status": state["status"], "seconds": state["responded_in_sec"]})
    return {"contact_id": contact["id"], "already_responded": False, "first_response_at": at, "sla": state}


@route("POST", r"/api/v1/tenant/ingest-keys")
def tenant_ingest_key_create(ctx: Ctx) -> dict:
    """Mint a portal feed key. The token is returned once and never stored."""
    ctx.require("manage_tenant")
    label = ctx.body_str("label", 60) or "portal"
    source = ctx.body_str("source", 30)
    if source and source not in ing.ADAPTERS:
        raise ApiError(400, "VALIDATION_FAILED", f"Unknown source '{source}'.", [{"sources": list(ing.ADAPTERS)}])
    token = "rink_" + sec.secrets.token_urlsafe(24)
    rec = ctx.store.insert("ingest_keys", ctx.tid, {
        "label": label,
        "source": source,
        "prefix": token[:12],
        "key_hash": sec.blind_index(token),
        "created_by": ctx.user.get("id", ""),
        "active": True,
        "count": 0,
        "last_used_at": None,
    }, prefix="ik")
    ctx.audit("ingest_key.created", rec["id"], {"label": label, "source": source})
    return {
        "key": {k: rec[k] for k in ("id", "label", "source", "prefix", "active", "created_at")},
        "token": token,
        "note": "Shown once. Configure the portal with it as X-Ingest-Key; rotate by revoking and minting a new one.",
        "endpoint": f"/api/v1/ingest/{source or '{source}'}",
    }


@route("GET", r"/api/v1/tenant/ingest-keys")
def tenant_ingest_keys(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    rows = sorted(ctx.store.all("ingest_keys", ctx.tid), key=lambda k: -int(k.get("created_at") or 0))
    return {"keys": [
        {k: r.get(k) for k in ("id", "label", "source", "prefix", "active", "created_at", "last_used_at", "count")}
        for r in rows
    ]}


@route("DELETE", r"/api/v1/tenant/ingest-keys/(?P<kid>[\w]+)")
def tenant_ingest_key_revoke(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    rec = ctx.store.get("ingest_keys", ctx.tid, ctx.params["kid"])
    if not rec:
        raise ApiError(404, "NOT_FOUND", "Key not found.")
    updated = ctx.store.update("ingest_keys", ctx.tid, rec["id"], {"active": False, "revoked_at": now()})
    ctx.audit("ingest_key.revoked", rec["id"], {"label": rec.get("label", "")})
    return {"key": {k: updated[k] for k in ("id", "label", "active", "revoked_at")}}


@route("GET", r"/api/v1/leads/scoring")
def leads_scoring(ctx: Ctx) -> dict:
    rows = [ag.score_lead(c) | {"contact_id": c["id"], "name": c["name"]} for c in ctx.store.all("contacts", ctx.tid)]
    rows.sort(key=lambda r: -r["score"])
    return {"scored": rows[:50], "model": "re-score-v4"}


# ==========================================================================
# Inbox & Call Recordings
# ==========================================================================
ALLOWED_RECORDING_TYPES: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "application/json": "json",
    "text/plain": "txt",
}
MAX_RECORDING_BYTES = 512 * 1024


def _archive_recording(
    ctx: Ctx,
    act_id: str,
    raw_bytes: bytes,
    content_type: str,
    ext: str,
    *,
    require_s3: bool = False,
    strict_upload: bool = True,
) -> tuple[dict[str, Any], str]:
    """Upload a call recording/transcript to `recordings/tenants/<tid>/<act_id>.<ext>`."""
    if len(raw_bytes) > MAX_RECORDING_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Call recording exceeds 512 KB inline upload limit.")
    if require_s3 and not neon.storage_configured():
        raise ApiError(503, "STORAGE_NOT_CONFIGURED", "Object storage is not configured on this instance.")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    object_key = f"recordings/tenants/{ctx.tid}/{act_id}.{ext}"
    meta: dict[str, Any] = {
        "bytes": len(raw_bytes),
        "sha256": digest,
        "content_type": content_type,
    }
    download_url = ""
    if neon.storage_configured():
        try:
            up = neon.put_object(object_key, raw_bytes, content_type=content_type)
            meta["stored_in"] = "object_storage"
            meta["object_key"] = up["key"]
            meta["bucket"] = up["bucket"]
            try:
                download_url = neon.presign_get(up["key"], expires=900)
            except Exception:
                download_url = ""
            return meta, download_url
        except Exception as exc:
            if strict_upload:
                raise ApiError(502, "STORAGE_UPLOAD_FAILED",
                               f"Recording upload failed: {cfg.redact(str(exc))[:120]}")
    meta["stored_in"] = "inline"
    meta["object_key"] = ""
    return meta, ""


@route("GET", r"/api/v1/inbox")
def inbox(ctx: Ctx) -> dict:
    # Single batched SQL read (`WHERE collection = ANY(%s)`) coalesced across
    # simultaneous opens, invalidated immediately by realtime events (`docs/37`).
    ctx.store.prefetch(("conversations", "contacts"), ctx.tid)
    ctx.store.reload_many(("conversations", "contacts"), ctx.tid, max_age_ms=250)
    convs = ctx.store.all("conversations", ctx.tid)
    contacts = {c["id"]: c for c in ctx.store.all("contacts", ctx.tid)}
    out = []
    for cv in convs:
        c = contacts.get(cv["contact_id"], {})
        last = cv["messages"][-1] if cv["messages"] else {}
        out.append(
            {
                "id": cv["id"],
                "contact_id": cv["contact_id"],
                "contact_name": c.get("name"),
                "stage": c.get("stage"),
                "score": c.get("score"),
                "channel": cv.get("channel"),
                "unread": cv.get("unread", 0),
                "last_text": last.get("text", "")[:120],
                "last_at": cv.get("last_at", 0),
                "ai_handled": any(m.get("ai") for m in cv["messages"]),
            }
        )
    out.sort(key=lambda r: -r["last_at"])
    return {"threads": out}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/calls")
def contact_log_call(ctx: Ctx) -> dict:
    """Record a phone call — the event a telephony webhook would deliver.

    Without this, "text back a missed call" is a playbook with no way to learn
    that a call was missed. When a `recording_b64` clip or `transcript` payload
    accompanies the event, the artifact is archived to private tenant-scoped
    object storage (`recordings/tenants/<tid>/<act_id>.*`) rather than bloating
    `re_documents`.
    """
    ctx.require("write")
    cid = ctx.params["cid"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    direction = (ctx.body_str("direction", 10) or "in").lower()
    if direction not in ("in", "out"):
        raise ApiError(422, "VALIDATION_FAILED", "direction must be 'in' or 'out'.")
    outcome = (ctx.body_str("outcome", 20) or "").lower()
    if outcome not in ("answered", "missed", "no_answer", "voicemail", "busy"):
        raise ApiError(422, "VALIDATION_FAILED",
                       "outcome must be answered, missed, no_answer, voicemail or busy.")
    try:
        duration = int(ctx.body.get("duration_sec") or 0)
    except (TypeError, ValueError):
        raise ApiError(422, "VALIDATION_FAILED", "duration_sec must be an integer.")
    if duration < 0 or duration > 4 * 3600:
        raise ApiError(422, "VALIDATION_FAILED", "duration_sec is out of range.")

    act_id = nid("act")
    recording_meta: dict[str, Any] | None = None
    recording_url = ""
    require_s3 = bool(ctx.body.get("require_object_storage"))
    rec_b64 = ctx.body.get("recording_b64")
    transcript = ctx.body.get("transcript")

    if isinstance(rec_b64, str) and rec_b64.strip():
        raw_ctype = (ctx.body_str("recording_content_type", 64) or "audio/wav").split(";")[0].strip().lower()
        ext = ALLOWED_RECORDING_TYPES.get(raw_ctype)
        if not ext:
            raise ApiError(422, "VALIDATION_FAILED",
                           f"Unsupported recording_content_type '{raw_ctype}'.")
        try:
            audio_bytes = base64.b64decode(rec_b64.strip(), validate=True)
        except Exception:
            raise ApiError(422, "VALIDATION_FAILED", "recording_b64 is not valid base64.")
        if not audio_bytes:
            raise ApiError(422, "VALIDATION_FAILED", "recording_b64 decoded to zero bytes.")
        recording_meta, recording_url = _archive_recording(
            ctx, act_id, audio_bytes, raw_ctype, ext, require_s3=require_s3
        )
    elif transcript is not None and transcript != "":
        if isinstance(transcript, str):
            cleaned_tr = sec.redact_secrets(transcript[:16000])
            artifact = {
                "tenant_id": ctx.tid,
                "activity_id": act_id,
                "contact_id": cid,
                "direction": direction,
                "outcome": outcome,
                "duration_sec": duration,
                "recorded_at": now(),
                "transcript": cleaned_tr,
            }
        elif isinstance(transcript, list):
            artifact = {
                "tenant_id": ctx.tid,
                "activity_id": act_id,
                "contact_id": cid,
                "direction": direction,
                "outcome": outcome,
                "duration_sec": duration,
                "recorded_at": now(),
                "turns": transcript[:100],
            }
        else:
            raise ApiError(422, "VALIDATION_FAILED", "transcript must be a string or list of turns.")
        raw_json = json.dumps(artifact, ensure_ascii=False, indent=2).encode("utf-8")
        recording_meta, recording_url = _archive_recording(
            ctx, act_id, raw_json, "application/json; charset=utf-8", "json", require_s3=require_s3
        )

    act_doc: dict[str, Any] = {
        "id": act_id,
        "contact_id": cid,
        "type": "call",
        "direction": direction,
        "outcome": outcome,
        "duration_sec": duration,
        "number_masked": mask_phone(c.get("phone", "")),
        "at": now(),
        "actor": ctx.user["name"],
        "text": f"{direction}bound call · {outcome}" + (f" · {duration}s" if duration else ""),
    }
    if recording_meta:
        act_doc["recording"] = recording_meta
    activity = ctx.store.insert("activities", ctx.tid, act_doc, prefix="act")
    ctx.store.update("contacts", ctx.tid, cid, {"last_activity_at": now()})
    audit_meta: dict[str, Any] = {"direction": direction, "outcome": outcome}
    if recording_meta:
        audit_meta["recording_stored_in"] = recording_meta["stored_in"]
        audit_meta["recording_bytes"] = recording_meta["bytes"]
    ctx.store.audit(ctx.tid, ctx.user["email"], "contact.call_logged", cid, audit_meta)
    missed = direction == "in" and outcome in ("missed", "no_answer", "voicemail", "busy")
    rt.HUB.publish(
        ctx.tid,
        "inbox.call",
        cid,
        {"direction": direction, "outcome": outcome, "missed": missed, "recorded": bool(recording_meta)},
        collections=("activities", "contacts"),
    )
    resp: dict[str, Any] = {
        "call": activity,
        "missed": missed,
        "recovers_with": "missed-call-textback" if missed else "",
    }
    if recording_meta:
        resp["recording"] = {**recording_meta, "activity_id": activity["id"], "download_url": recording_url,
                             "expires_in": 900 if recording_url else 0}
    return resp


@route("GET", r"/api/v1/recordings/(?P<rid>[\w]+)")
def recording_get(ctx: Ctx) -> dict:
    """Issue a short-lived tenant-scoped presigned GET URL for a call recording."""
    # Voice recordings carry unmasked buyer speech/transcripts: require `pii:read`
    # (`owner`, `admin`, `manager`) rather than exposing raw audio to viewer seats.
    ctx.require("pii:read")
    rid = ctx.params["rid"]
    act = ctx.store.get("activities", ctx.tid, rid)
    if not act and getattr(ctx.store, "backend", None) is not None:
        ctx.store.reload("activities", ctx.tid)
        act = ctx.store.get("activities", ctx.tid, rid)
    rec = (act or {}).get("recording") if act else None
    if not act or act.get("type") != "call" or not isinstance(rec, dict):
        raise ApiError(404, "NOT_FOUND", "Recording not found.")
    url = ""
    expires_in = 0
    if rec.get("stored_in") == "object_storage" and rec.get("object_key") and neon.storage_configured():
        try:
            url = neon.presign_get(rec["object_key"], expires=900)
            expires_in = 900
        except Exception:
            url = ""
    ctx.audit("recording.accessed", rid, {
        "contact_id": act.get("contact_id", ""),
        "stored_in": rec.get("stored_in", ""),
    })
    return {
        "recording": {**rec, "activity_id": rid, "contact_id": act.get("contact_id", ""),
                      "download_url": url, "expires_in": expires_in},
    }


@route("GET", r"/api/v1/inbox/(?P<cid>[\w]+)")
def inbox_thread(ctx: Ctx) -> dict:
    cid = ctx.params["cid"]
    ctx.store.reload_many(("conversations", "contacts"), ctx.tid, max_age_ms=250)
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    conv = next((x for x in ctx.store.all("conversations", ctx.tid) if x["contact_id"] == cid), None)
    if conv:
        ctx.store.update("conversations", ctx.tid, conv["id"], {"unread": 0})
    return {"contact": out_contact(c, ctx), "conversation": conv, "scoring": ag.score_lead(c)}


@route("POST", r"/api/v1/inbox/(?P<cid>[\w]+)/send")
def inbox_send(ctx: Ctx) -> dict:
    ctx.require("write")
    cid = ctx.params["cid"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    channel = ctx.body_str("channel", 20) or "whatsapp"
    tpl_name = ctx.body_str("template_name", 80)
    raw_params = ctx.body.get("template_params")
    tpl_params = [sec.clean(str(p), 160) for p in raw_params[:10]] if isinstance(raw_params, list) else []
    dlt_tpl_id = ctx.body_str("dlt_template_id", 24)
    sender_id = ctx.body_str("sender_id", 8)
    text = ctx.body_str("text", 2000)
    if not text and not tpl_name:
        raise ApiError(400, "VALIDATION_FAILED", "text or template_name is required.")
    if channel not in ("whatsapp", "sms", "voice", "email", "webchat"):
        raise ApiError(400, "VALIDATION_FAILED", "Unknown channel.")
    # The month's message allowance, before the compliance gates: a plan that has
    # run out is not a compliance problem and must not be reported as one.
    if channel == "whatsapp":
        ut.enforce(ctx.store, ctx.tenant, "whatsapp_out")
    elif channel == "sms":
        ut.enforce(ctx.store, ctx.tenant, "sms_out")
    # Compliance gate before any outbound message
    if (c.get("consent") or {}).get("status") != "granted":
        raise ApiError(422, "CONSENT_MISSING", "Consent is not granted for this contact. Capture consent before messaging.")
    if channel in ("whatsapp", "sms", "voice"):
        in_quiet, window = in_quiet_hours(ctx.tenant)
        if in_quiet and not ctx.body.get("override_quiet_hours"):
            raise ApiError(451, "COMPLIANCE_BLOCKED", f"Quiet hours ({window}). Schedule instead of sending now.")

    conv = next((x for x in ctx.store.all("conversations", ctx.tid) if x["contact_id"] == cid), None)
    phone_plain = sec.unseal(c.get("phone_enc", ""), aad=ctx.tid) or str(c.get("phone") or "")

    # Pre-render template text if template_name was supplied so the output guardrail
    # inspects the actual copy that will reach the buyer.
    candidate_text = text
    if channel == "whatsapp" and tpl_name:
        tpl_map = channels.whatsapp_templates_for(ctx.tenant)
        tpl_def = tpl_map.get(tpl_name)
        if not tpl_def or tpl_def.get("status", "approved") != "approved":
            raise ApiError(422, "WHATSAPP_TEMPLATE_UNKNOWN", f"WhatsApp template '{tpl_name}' is not approved.")
        try:
            candidate_text = channels.render_whatsapp_template(tpl_def, tpl_params)
        except channels.ChannelError as exc:
            raise ApiError(exc.status, exc.code, exc.message)

    guard = sec.inspect_text(candidate_text)
    if guard["action"] == "block":
        raise ApiError(451, "COMPLIANCE_BLOCKED", "Message blocked by output guardrail.", guard["findings"])

    delivery: dict[str, Any] = {
        "mode": "simulated",
        "provider": f"{channel}-sim",
        "provider_message_id": f"{channel}.sim_{nid('m')[2:]}",
        "status": "sent",
    }
    try:
        if channel == "whatsapp":
            delivery = channels.send_whatsapp(
                phone_plain or "+919999999999",
                candidate_text,
                template_name=tpl_name,
                template_params=tpl_params,
                language=ctx.body_str("language", 12),
                tenant=ctx.tenant,
                conv=conv,
                contact=c,
                strict_window=bool(ctx.body.get("strict_window")),
            )
            candidate_text = delivery.get("rendered_text") or candidate_text
        elif channel == "sms":
            delivery = channels.send_sms(
                phone_plain or "+919999999999",
                candidate_text,
                dlt_template_id=dlt_tpl_id,
                sender_id=sender_id,
                tenant=ctx.tenant,
                strict_dlt=bool(ctx.body.get("strict_dlt")),
            )
    except channels.ChannelError as exc:
        raise ApiError(exc.status, exc.code, exc.message)

    msg = {
        "id": nid("msg"),
        "dir": "out",
        "channel": channel,
        "text": sec.redact_secrets(candidate_text),
        "at": now(),
        "ai": bool(ctx.body.get("ai")),
        "status": delivery.get("status", "sent"),
        "delivery_mode": delivery.get("mode", "simulated"),
        "provider": delivery.get("provider", ""),
        "provider_message_id": delivery.get("provider_message_id", ""),
        "guardrail": guard["action"],
        "actor": ctx.user["name"],
    }
    if tpl_name:
        msg["template_name"] = tpl_name
    if delivery.get("dlt_template_id"):
        msg["dlt_template_id"] = delivery["dlt_template_id"]
        msg["sender_id"] = delivery.get("sender_id", "")
    if "window_24h_open" in delivery:
        msg["window_24h_open"] = bool(delivery["window_24h_open"])

    if conv:
        msgs = conv["messages"] + [msg]
        ctx.store.update("conversations", ctx.tid, conv["id"], {"messages": msgs, "last_at": now(), "channel": channel})
    else:
        ctx.store.insert("conversations", ctx.tid, {"contact_id": cid, "channel": channel, "unread": 0, "messages": [msg], "last_at": now()}, prefix="cnv")
    # Speed-to-lead: the clock stops on the first *outbound* message, which is
    # the only event that proves a human actually reached the buyer.
    contact_patch: dict = {"last_activity_at": now()}
    if not c.get("first_response_at"):
        contact_patch["first_response_at"] = now()
        contact_patch["responded_by"] = ctx.user.get("id", "")
        contact_patch["responded_channel"] = channel
    ctx.store.update("contacts", ctx.tid, cid, contact_patch)
    ctx.audit("message.sent", cid, {"channel": channel, "guardrail": guard["action"], "mode": delivery.get("mode", "simulated")})
    rt.HUB.publish(
        ctx.tid,
        "inbox.message",
        cid,
        {"channel": channel, "dir": "out", "ai": bool(ctx.body.get("ai")), "mode": delivery.get("mode", "simulated")},
        collections=("conversations", "contacts"),
    )
    return {"message": msg, "guardrail": guard, "delivery": {k: v for k, v in delivery.items() if k != "rendered_text"}}


# ==========================================================================
# Phase 1 S1 — Carrier status, template registries, voice dispatch & webhooks
# ==========================================================================
@route("GET", r"/api/v1/channels/status")
def channels_status(ctx: Ctx) -> dict:
    return {"channels": channels.status_summary(ctx.tenant)}


@route("GET", r"/api/v1/channels/whatsapp/templates")
def whatsapp_templates_list(ctx: Ctx) -> dict:
    tpls = list(channels.whatsapp_templates_for(ctx.tenant).values())
    tpls.sort(key=lambda r: str(r.get("name") or ""))
    return {"templates": tpls, "count": len(tpls)}


@route("POST", r"/api/v1/channels/whatsapp/templates")
def whatsapp_templates_register(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    name = ctx.body_str("name", 64, required=True).lower()
    if not re.fullmatch(r"[a-z0-9_]{3,64}", name):
        raise ApiError(422, "VALIDATION_FAILED", "Template name must match ^[a-z0-9_]{3,64}$.")
    body_txt = ctx.body_str("body", 1024, required=True)
    category = (ctx.body_str("category", 24) or "UTILITY").upper()
    if category not in ("UTILITY", "MARKETING", "AUTHENTICATION"):
        raise ApiError(422, "VALIDATION_FAILED", "category must be UTILITY, MARKETING, or AUTHENTICATION.")
    language = ctx.body_str("language", 12) or "en"
    placeholders = sorted({int(m) for m in re.findall(r"\{\{(\d+)\}\}", body_txt)})
    expected = list(range(1, len(placeholders) + 1))
    if placeholders != expected:
        raise ApiError(422, "VALIDATION_FAILED", "Template placeholders must be sequential {{1}}, {{2}}, ...")
    entry = {
        "name": name,
        "language": language,
        "category": category,
        "body": body_txt,
        "param_count": len(placeholders),
        "status": "approved",
        "created_at": now(),
    }
    settings = dict(ctx.tenant.get("settings") or {})
    custom = [x for x in (settings.get("whatsapp_templates") or []) if isinstance(x, dict) and x.get("name") != name]
    custom.append(entry)
    settings["whatsapp_templates"] = custom[-50:]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("whatsapp.template_registered", name, {"param_count": entry["param_count"], "category": category})
    return {"template": entry}


@route("GET", r"/api/v1/channels/sms/templates")
def sms_templates_list(ctx: Ctx) -> dict:
    tpls = list(channels.dlt_templates_for(ctx.tenant).values())
    tpls.sort(key=lambda r: str(r.get("dlt_template_id") or ""))
    return {"templates": tpls, "count": len(tpls)}


@route("POST", r"/api/v1/channels/sms/templates")
def sms_templates_register(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    dlt_id = ctx.body_str("dlt_template_id", 24, required=True)
    if not re.fullmatch(r"\d{12,20}", dlt_id):
        raise ApiError(422, "VALIDATION_FAILED", "dlt_template_id must be 12–20 digits.")
    name = ctx.body_str("name", 64, required=True)
    sender_id = (ctx.body_str("sender_id", 8) or cfg.get("SMS_SENDER_ID", "") or "SNRSUN").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6}", sender_id):
        raise ApiError(422, "VALIDATION_FAILED", "sender_id must be 6 uppercase alphanumeric characters.")
    pattern = ctx.body_str("pattern", 600, required=True)
    category = (ctx.body_str("category", 32) or "service_implicit").lower()
    entry = {
        "dlt_template_id": dlt_id,
        "name": name,
        "sender_id": sender_id,
        "category": category,
        "pattern": pattern,
        "created_at": now(),
    }
    settings = dict(ctx.tenant.get("settings") or {})
    custom = [x for x in (settings.get("dlt_templates") or []) if isinstance(x, dict) and x.get("dlt_template_id") != dlt_id]
    custom.append(entry)
    settings["dlt_templates"] = custom[-100:]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("sms.dlt_template_registered", dlt_id, {"name": name, "sender_id": sender_id})
    return {"template": entry}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/voice-call")
def contact_voice_call(ctx: Ctx) -> dict:
    """Initiate an outbound AI qualification call with DPDP + TRAI quiet-hours gates."""
    ctx.require("write")
    cid = ctx.params["cid"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    ut.enforce(ctx.store, ctx.tenant, "voice_minutes")
    if (c.get("consent") or {}).get("status") != "granted":
        raise ApiError(422, "CONSENT_MISSING", "DPDP consent is required before initiating an AI voice call.")
    in_quiet, window = in_quiet_hours(ctx.tenant)
    if in_quiet and not ctx.body.get("override_quiet_hours"):
        raise ApiError(451, "COMPLIANCE_BLOCKED", f"TRAI quiet hours ({window}). Defer outbound voice call.")
    phone_plain = sec.unseal(c.get("phone_enc", ""), aad=ctx.tid) or str(c.get("phone") or "")
    act_id = nid("act")
    language = ctx.body_str("language", 12) or "mr"
    agent_name = ctx.body_str("agent_name", 60) or "Asha (Voice Qualification)"
    try:
        dispatch = channels.start_voice_call(
            phone_plain or "+919999999999",
            tenant_id=ctx.tid,
            contact_id=cid,
            activity_id=act_id,
            agent_name=agent_name,
            language=language,
            tenant=ctx.tenant,
        )
    except channels.ChannelError as exc:
        raise ApiError(exc.status, exc.code, exc.message)

    # Zero-blockage Voice Local Twin (`docs/39`): when no external telephony
    # provider is configured, synthesize a real playable 8 kHz PCM `.wav` clip +
    # qualification transcript and archive it straight to object storage so the
    # entire voice workflow works out of the box with zero external keys.
    rec_meta: dict[str, Any] | None = None
    rec_url = ""
    transcript_txt = ""
    sim_duration = 0
    if dispatch["mode"] != "live":
        wav_bytes = conn_mod.synthesize_wav_clip(duration_ms=1200)
        transcript_txt = conn_mod.build_local_voice_transcript(c, ctx.tenant, agent_name, language)
        rec_meta, rec_url = _archive_recording(ctx, act_id, wav_bytes, "audio/wav", "wav", strict_upload=False)
        sim_duration = 48

    act_doc: dict[str, Any] = {
        "id": act_id,
        "type": "call",
        "contact_id": cid,
        "direction": "out",
        "outcome": "initiated" if dispatch["mode"] == "live" else "answered",
        "duration_sec": sim_duration,
        "provider": dispatch["provider"],
        "provider_call_id": dispatch["provider_call_id"],
        "delivery_mode": dispatch["mode"],
        "language": language,
        "ai_disclosure": True,
        "note": sec.clean(ctx.body.get("note", "Outbound AI qualification call"), 240),
        "actor": ctx.user["name"],
        "at": now(),
    }
    if rec_meta:
        act_doc["recording"] = rec_meta
    if transcript_txt:
        act_doc["transcript"] = transcript_txt

    act = ctx.store.insert(
        "activities",
        ctx.tid,
        act_doc,
        prefix="act",
    )
    ctx.store.update("contacts", ctx.tid, cid, {"last_activity_at": now()})
    ctx.audit("voice.call_initiated", cid, {"activity_id": act["id"], "mode": dispatch["mode"], "provider": dispatch["provider"]})
    rt.HUB.publish(
        ctx.tid,
        "inbox.call",
        cid,
        {"direction": "out", "outcome": act["outcome"], "mode": dispatch["mode"]},
        collections=("activities", "contacts"),
    )
    return {"activity": act, "dispatch": dispatch, "recording_url": rec_url}


def _find_contact_for_webhook(store: Store, phone: str, tenant_hint: str = "", phone_number_id: str = "") -> tuple[str, dict | None]:
    """Resolve `(tenant_id, contact)` using workspace metadata or `phone_hash` blind index."""
    candidates: list[str] = []
    if tenant_hint and any(t["id"] == tenant_hint for t in store.db["tenants"]):
        candidates.append(tenant_hint)
    if phone_number_id:
        for t in store.db["tenants"]:
            if str((t.get("settings") or {}).get("whatsapp_phone_id") or "") == phone_number_id and t["id"] not in candidates:
                candidates.append(t["id"])
    if not candidates:
        candidates = [t["id"] for t in store.db["tenants"]]

    digits = re.sub(r"\D", "", phone or "")
    local10 = digits[-10:] if len(digits) >= 10 else digits
    variants = {v for v in {phone, "+" + digits, digits, local10, "+91" + local10} if v}
    for tid in candidates:
        probe_hashes = set().union(*(sec.blind_index_candidates(v, tid) for v in variants)) if variants else set()
        for c in store.all("contacts", tid):
            if c.get("phone_hash") in probe_hashes:
                return tid, c
    return (candidates[0] if candidates else ""), None


@route("GET", r"/api/v1/webhooks/whatsapp", public=True, bucket="webhook")
def whatsapp_webhook_verify(ctx: Ctx) -> dict:
    try:
        challenge = channels.verify_whatsapp_challenge(ctx.query)
    except channels.ChannelError as exc:
        raise ApiError(exc.status, exc.code, exc.message)
    return {"_plain_text": challenge}


@route("POST", r"/api/v1/webhooks/whatsapp", public=True, bucket="webhook")
def whatsapp_webhook_receive(ctx: Ctx) -> dict:
    sig = ctx.headers.get("x-hub-signature-256", "")
    if not channels.verify_hmac_sha256(ctx.raw, sig, ("WHATSAPP_APP_SECRET", "RE_WEBHOOK_SIGNING_SECRET")):
        raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", "Invalid or missing X-Hub-Signature-256.")

    parsed = channels.parse_whatsapp_webhook(ctx.body)
    ingested = 0
    statuses_applied = 0

    for item in parsed["inbound"]:
        tid, contact = _find_contact_for_webhook(
            ctx.store, item["from_phone"], item.get("tenant_id", ""), item.get("phone_number_id", "")
        )
        if not tid or not contact:
            continue
        cid = contact["id"]
        conv = next((x for x in ctx.store.all("conversations", tid) if x["contact_id"] == cid), None)
        wamid = item.get("wamid") or nid("wamid")
        existing = (conv or {}).get("messages") or []
        if any(m.get("provider_message_id") == wamid for m in existing):
            continue
        in_msg = {
            "id": nid("msg"),
            "dir": "in",
            "channel": "whatsapp",
            "text": sec.redact_secrets(item.get("text") or ""),
            "at": int(item.get("at") or now()),
            "ai": False,
            "status": "received",
            "provider_message_id": wamid,
            "actor": contact.get("name") or "Buyer",
        }
        if conv:
            ctx.store.update(
                "conversations",
                tid,
                conv["id"],
                {
                    "messages": existing + [in_msg],
                    "unread": int(conv.get("unread") or 0) + 1,
                    "last_at": in_msg["at"],
                    "channel": "whatsapp",
                },
            )
        else:
            ctx.store.insert(
                "conversations",
                tid,
                {
                    "contact_id": cid,
                    "channel": "whatsapp",
                    "unread": 1,
                    "messages": [in_msg],
                    "last_at": in_msg["at"],
                },
                prefix="cnv",
            )
        ctx.store.update(
            "contacts",
            tid,
            cid,
            {"last_inbound_at": in_msg["at"], "last_activity_at": in_msg["at"]},
        )
        ctx.store.audit(tid, "webhook:whatsapp", "whatsapp.inbound", cid, {"wamid": wamid})
        rt.HUB.publish(
            tid,
            "inbox.message",
            cid,
            {"channel": "whatsapp", "dir": "in"},
            collections=("conversations", "contacts"),
        )
        ingested += 1

    for st in parsed["statuses"]:
        wamid = st.get("wamid") or ""
        new_status = st.get("status") or ""
        if not wamid or new_status not in ("sent", "delivered", "read", "failed"):
            continue
        tids = [st["tenant_id"]] if st.get("tenant_id") else [t["id"] for t in ctx.store.db["tenants"]]
        for tid in tids:
            for conv in ctx.store.all("conversations", tid):
                changed = False
                new_msgs = []
                for m in (conv.get("messages") or []):
                    if m.get("provider_message_id") == wamid:
                        m = {**m, "status": new_status, "status_at": int(st.get("at") or now())}
                        if st.get("error_code"):
                            m["error_code"] = st["error_code"]
                        changed = True
                    new_msgs.append(m)
                if changed:
                    ctx.store.update("conversations", tid, conv["id"], {"messages": new_msgs})
                    rt.HUB.publish(
                        tid,
                        "inbox.status",
                        conv["contact_id"],
                        {"channel": "whatsapp", "status": new_status, "wamid": wamid},
                        collections=("conversations",),
                    )
                    statuses_applied += 1
                    break

    return {"ok": True, "inbound": ingested, "statuses": statuses_applied}


@route("POST", r"/api/v1/webhooks/sms", public=True, bucket="webhook")
def sms_webhook_receive(ctx: Ctx) -> dict:
    sig = ctx.headers.get("x-sms-signature") or ctx.headers.get("x-webhook-signature", "")
    if not channels.verify_hmac_sha256(ctx.raw, sig, ("SMS_API_KEY", "RE_WEBHOOK_SIGNING_SECRET")):
        raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", "Invalid or missing SMS DLR signature.")
    msg_id = ctx.body_str("provider_message_id", 80) or ctx.body_str("message_id", 80, required=True)
    status = (ctx.body_str("status", 24, required=True)).lower()
    if status not in ("submitted", "sent", "delivered", "failed", "dlt_rejected"):
        raise ApiError(422, "VALIDATION_FAILED", "Unsupported SMS DLR status.")
    tid_hint = ctx.body_str("tenant_id", 40)
    tids = [tid_hint] if tid_hint else [t["id"] for t in ctx.store.db["tenants"]]
    updated = 0
    for tid in tids:
        for conv in ctx.store.all("conversations", tid):
            changed = False
            msgs = []
            for m in (conv.get("messages") or []):
                if m.get("provider_message_id") == msg_id:
                    m = {**m, "status": status, "status_at": now()}
                    if ctx.body.get("dlt_error_code"):
                        m["dlt_error_code"] = sec.clean(str(ctx.body["dlt_error_code"]), 32)
                    changed = True
                msgs.append(m)
            if changed:
                ctx.store.update("conversations", tid, conv["id"], {"messages": msgs})
                rt.HUB.publish(
                    tid,
                    "inbox.status",
                    conv["contact_id"],
                    {"channel": "sms", "status": status},
                    collections=("conversations",),
                )
                updated += 1
    return {"ok": True, "updated": updated}


@route("POST", r"/api/v1/webhooks/voice", public=True, bucket="webhook")
def voice_webhook_receive(ctx: Ctx) -> dict:
    sig = ctx.headers.get("x-voice-signature") or ctx.headers.get("x-webhook-signature", "")
    if not channels.verify_hmac_sha256(ctx.raw, sig, ("VOICE_WEBHOOK_SECRET", "RE_WEBHOOK_SIGNING_SECRET")):
        raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", "Invalid or missing voice webhook signature.")
    tid = ctx.body_str("tenant_id", 40, required=True)
    if not any(t["id"] == tid for t in ctx.store.db["tenants"]):
        raise ApiError(404, "NOT_FOUND", "Unknown workspace.")
    ctx.claims = {"tid": tid}
    call_id = ctx.body_str("provider_call_id", 80)
    act_id = ctx.body_str("activity_id", 40)
    outcome = (ctx.body_str("outcome", 20) or "answered").lower()
    if outcome not in ("answered", "missed", "no_answer", "voicemail", "busy"):
        raise ApiError(422, "VALIDATION_FAILED", "Invalid call outcome.")
    duration = max(0, min(4 * 3600, int(ctx.body.get("duration_sec") or 0)))

    acts = ctx.store.all("activities", tid)
    target = next(
        (a for a in acts if (act_id and a["id"] == act_id) or (call_id and a.get("provider_call_id") == call_id)),
        None,
    )
    if not target:
        raise ApiError(404, "NOT_FOUND", "Matching call activity not found.")

    patch: dict[str, Any] = {"outcome": outcome, "duration_sec": duration, "completed_at": now()}
    rec_b64 = ctx.body.get("recording_b64")
    transcript = ctx.body.get("transcript")
    if isinstance(rec_b64, str) and rec_b64.strip():
        raw_ctype = (ctx.body_str("recording_content_type", 64) or "audio/wav").split(";")[0].strip().lower()
        ext = ALLOWED_RECORDING_TYPES.get(raw_ctype, "wav")
        try:
            clip_bytes = base64.b64decode(rec_b64, validate=True)
        except Exception:
            raise ApiError(400, "VALIDATION_FAILED", "recording_b64 is not valid base64.")
        meta, _url = _archive_recording(ctx, target["id"], clip_bytes, raw_ctype, ext, strict_upload=False)
        patch["recording"] = meta
    elif isinstance(transcript, str) and transcript.strip():
        t_bytes = sec.redact_secrets(transcript[:32_000]).encode("utf-8")
        meta, _url = _archive_recording(ctx, target["id"], t_bytes, "text/plain", "txt", strict_upload=False)
        patch["recording"] = meta

    upd = ctx.store.update("activities", tid, target["id"], patch)
    ctx.store.audit(tid, "webhook:voice", "voice.call_completed", target["contact_id"], {"activity_id": target["id"], "duration_sec": duration})
    rt.HUB.publish(
        tid,
        "inbox.call",
        target["contact_id"],
        {"direction": target.get("direction", "out"), "outcome": outcome, "duration_sec": duration},
        collections=("activities", "contacts"),
    )
    return {"ok": True, "activity": upd}


@route("POST", r"/api/v1/inbox/(?P<cid>[\w]+)/ai-draft")
def inbox_ai_draft(ctx: Ctx) -> dict:
    c = ctx.store.get("contacts", ctx.tid, ctx.params["cid"])
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    agent = (ctx.store.all("agents", ctx.tid) or [{}])[1 if len(ctx.store.all("agents", ctx.tid)) > 1 else 0]
    ai_res = conn_mod.complete_ai(
        ctx.tenant,
        [
            {"role": "system", "content": "Draft a concise, RERA-compliant real-estate reply in India. Never invent prices or unapproved claims."},
            {"role": "user", "content": f"Buyer: {c.get('name')} · City: {c.get('city') or 'Nanded'} · Requirement: {c.get('requirement') or 'clear-title plot'}"},
        ],
        model=str(agent.get("model") or ""),
    )
    if ai_res.get("ok") and (ai_res.get("text") or "").strip():
        draft = ai_res["text"].strip()
        m_route = {"provider": ai_res.get("provider", "live-ai"), "model": ai_res.get("model", ""), "reason": "active AI connector"}
    else:
        draft = ag._local_completion("whatsapp_reply", {"name": c["name"], "requirement": c.get("requirement")})
        m_route = ag.route_model("in", "medium")
    guard = sec.inspect_text(draft)
    return {"draft": sec.redact_secrets(draft), "guardrail": guard, "agent": agent.get("name"), "model_route": m_route}


# ==========================================================================
# Agents + learning loop
# ==========================================================================
@route("GET", r"/api/v1/agents")
def agents_list(ctx: Ctx) -> dict:
    return {"agents": [out_agent(a) for a in ctx.store.all("agents", ctx.tid)]}


@route("POST", r"/api/v1/agents")
def agents_create(ctx: Ctx) -> dict:
    ctx.require("write")
    name = ctx.body_str("name", 60, required=True)
    scopes = [sec.clean(s, 40) for s in (ctx.body.get("scopes") or ["contacts:read"])][:12]
    tools = [sec.clean(t, 60) for t in (ctx.body.get("tools") or ["crm.lookup_contact"])][:12]
    a = ctx.store.insert(
        "agents",
        ctx.tid,
        {
            "name": name,
            "role": ctx.body_str("role", 120) or "Custom agent",
            "department": ctx.body_str("department", 40) or "Sales",
            "model": ctx.body_str("model", 60) or "sarvam-m",
            "temperature": 0.3,
            "status": "active",
            "channels": ["whatsapp"],
            "scopes": scopes,
            "tools": tools,
            "skills": [],
            "guardrails": {
                "human_approval_for": ["bulk_message", "price_concession", "delete_record", "refund"],
                "max_discount_pct": 0,
                "spend_cap_usd": 100,
                "pii_redaction": True,
                "fail_closed": True,
                "claim_set": "rera_registered_only",
            },
            "spend_usd": 0,
            "runs": 0,
            "success_rate": 0,
        },
        prefix="agt",
    )
    ctx.audit("agent.created", a["id"], {"name": name, "tools": tools})
    return {"agent": out_agent(a)}


@route("PATCH", r"/api/v1/agents/(?P<aid>[\w]+)")
def agents_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    a = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not a:
        raise ApiError(404, "NOT_FOUND", "Agent not found.")
    patch = sec.pick(ctx.body, {"name", "role", "status", "temperature", "model", "channels"})
    if "status" in patch and patch["status"] not in ("active", "paused", "shadow"):
        raise ApiError(400, "VALIDATION_FAILED", "status must be active, paused or shadow.")
    upd = ctx.store.update("agents", ctx.tid, a["id"], patch)
    ctx.audit("agent.updated", a["id"], patch)
    return {"agent": out_agent(upd)}


@route("GET", r"/api/v1/agents/(?P<aid>[\w]+)/tools")
def agents_tools(ctx: Ctx) -> dict:
    a = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not a:
        raise ApiError(404, "NOT_FOUND", "Agent not found.")
    return {"tools": ag.tools_for(a, ctx.tenant), "catalog_size": len(ag.TOOL_CATALOG), "approval_required": sorted(ag.APPROVAL_REQUIRED)}


@route("POST", r"/api/v1/agents/(?P<aid>[\w]+)/run")
def agents_run(ctx: Ctx) -> dict:
    ctx.require("write")
    # an AI run costs money at a provider; the allowance is checked before the call
    ut.enforce(ctx.store, ctx.tenant, "ai_runs")
    a = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not a:
        raise ApiError(404, "NOT_FOUND", "Agent not found.")
    text = ctx.body_str("input", 8000, required=True)
    cid = sec.clean(ctx.body.get("contact_id", ""), 40)
    contact = (ctx.store.get("contacts", ctx.tid, cid) if cid else None) or {}
    consent_ok = (contact.get("consent", {}) or {}).get("status") == "granted" if contact else False
    result = ag.run_agent(
        a,
        text,
        {
            "tenant": ctx.tenant,
            "contact_id": cid,
            "name": contact.get("name", "the buyer"),
            "requirement": contact.get("requirement", "a clear-title property"),
            "compliance": {"ok": bool(consent_ok)},
        },
        policy={"kill_switch": ctx.tenant.get("kill_switch", False)},
    )
    ctx.store.update("agents", ctx.tid, a["id"], {"runs": a.get("runs", 0) + 1, "spend_usd": round(a.get("spend_usd", 0) + result.get("cost_usd", 0), 4)})
    ctx.store.insert(
        "runs",
        ctx.tid,
        {"type": "agent_run", "agent_id": a["id"], "status": result.get("status"), "input_digest": sec.sha256_hex(text)[:16], "latency_ms": result.get("latency_ms", 0), "blocked_reason": result.get("reason", ""), "cost_usd": result.get("cost_usd", 0)},
        prefix="run",
    )
    ctx.audit("agent.run", a["id"], {"status": result.get("status"), "guardrail": result.get("guardrail", {}).get("action")})
    return result


@route("POST", r"/api/v1/agents/(?P<aid>[\w]+)/voice-test")
def agents_voice(ctx: Ctx) -> dict:
    ctx.require("write")
    a = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not a:
        raise ApiError(404, "NOT_FOUND", "Agent not found.")
    if not ctx.tenant.get("features", {}).get("ai_voice", False):
        raise ApiError(403, "FEATURE_DISABLED", "Voice is disabled for this workspace.")
    cid = sec.clean(ctx.body.get("contact_id", ""), 40)
    contact = ctx.store.get("contacts", ctx.tid, cid) or {"name": "Test Buyer", "id": "test"} if cid else {"name": "Test Buyer", "id": "test"}
    lang = ctx.body_str("language", 4) or "mr"
    call = ag.simulate_call(a, contact, lang)
    act_id = nid("act")
    artifact = json.dumps(
        {
            "tenant_id": ctx.tid,
            "activity_id": act_id,
            "agent_id": a["id"],
            "contact_id": contact.get("id", "test"),
            "language": lang,
            "duration_s": call.get("duration_s", 0),
            "turns": call.get("turns", []),
            "extracted": call.get("extracted", {}),
            "compliance": call.get("compliance", {}),
            "recorded_at": now(),
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    rec_meta, rec_url = _archive_recording(
        ctx, act_id, artifact, "application/json; charset=utf-8", "json", strict_upload=False
    )
    ctx.store.insert("activities", ctx.tid, {
        "id": act_id,
        "contact_id": contact.get("id", "test"),
        "type": "call",
        "subtype": "ai_voice",
        "direction": "out",
        "outcome": "answered",
        "duration_sec": int(call.get("duration_s") or 0),
        "at": now(),
        "actor": a["name"],
        "text": f"AI voice call ({lang}) · {call.get('outcome', 'completed')}",
        "recording": rec_meta,
    }, prefix="act")
    call["recording"] = {
        **rec_meta,
        "activity_id": act_id,
        "download_url": rec_url,
        "expires_in": 900 if rec_url else 0,
    }
    ctx.audit("voice.test_call", a["id"], {
        "contact": contact.get("id"),
        "language": lang,
        "recording_stored_in": rec_meta["stored_in"],
    })
    return call


@route("POST", r"/api/v1/agents/(?P<aid>[\w]+)/propose")
def agents_propose(ctx: Ctx) -> dict:
    ctx.require("write")
    a = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not a:
        raise ApiError(404, "NOT_FOUND", "Agent not found.")
    observation = ctx.body_str("observation", 800, required=True)
    proposal = ctx.body_str("proposal", 800, required=True)
    scope_change = [sec.clean(s, 40) for s in (ctx.body.get("scope_change") or [])]
    p = ag.propose_skill(a, observation, proposal, ["manual"], scope_change)
    rec = ctx.store.insert("proposals", ctx.tid, p, prefix="prp")
    ctx.audit("learning.proposed", rec["id"], {"agent": a["id"], "risk": p["risk"]})
    rt.HUB.publish(ctx.tid, "learning.updated", rec["id"], {"status": "pending", "risk": p["risk"]}, collections=("proposals",))
    return {"proposal": rec}


@route("GET", r"/api/v1/learning")
def learning_list(ctx: Ctx) -> dict:
    ctx.store.prefetch(("proposals", "agents"), ctx.tid)
    rows = sorted(ctx.store.all("proposals", ctx.tid), key=lambda p: -p.get("created_at", 0))
    agents = {a["id"]: a["name"] for a in ctx.store.all("agents", ctx.tid)}
    for r in rows:
        r["agent_name"] = agents.get(r.get("agent_id"), "—")
    return {"proposals": rows, "pending": sum(1 for r in rows if r.get("status") == "pending")}


@route("POST", r"/api/v1/learning/(?P<pid>[\w]+)/decide")
def learning_decide(ctx: Ctx) -> dict:
    ctx.require("decide_learning")
    p = ctx.store.get("proposals", ctx.tid, ctx.params["pid"])
    if not p:
        raise ApiError(404, "NOT_FOUND", "Proposal not found.")
    if p.get("status") != "pending":
        raise ApiError(409, "VERSION_CONFLICT", "Proposal already decided.")
    decision = ctx.body_str("decision", 20)
    if decision not in ("approve", "reject"):
        raise ApiError(400, "VALIDATION_FAILED", "decision must be approve or reject.")
    note = ctx.body_str("note", 500)
    # Guardrail: high/critical risk needs an explicit acknowledgement
    if decision == "approve" and p.get("risk") in ("high", "critical") and not ctx.body.get("acknowledge_risk"):
        raise ApiError(422, "ACKNOWLEDGEMENT_REQUIRED", "High-risk promotion requires explicit risk acknowledgement.")
    if decision == "approve" and p.get("risk") == "critical" and ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only the owner can approve a critical-risk skill.")
    status = "promoted" if decision == "approve" else "rejected"
    upd = ctx.store.update("proposals", ctx.tid, p["id"], {"status": status, "decided_by": ctx.user["name"], "decided_at": now(), "decision_note": note})
    if decision == "approve":
        if p.get("kind") == "ads_budget_increase" and p.get("campaign_id"):
            camp = ctx.store.get("campaigns", ctx.tid, p["campaign_id"])
            if camp:
                proposed_daily = int(p.get("proposed_budget_daily") or camp.get("budget_daily") or 0)
                headroom = p3_mod.check_campaign_spend_headroom(
                    ctx.store, ctx.tenant, campaign_id=camp["id"], proposed_daily_inr=proposed_daily
                )
                if headroom["daily_cap_exceeded"] or headroom["monthly_cap_exceeded"]:
                    ctx.store.update("proposals", ctx.tid, p["id"], {"status": "pending", "decided_by": "", "decided_at": 0})
                    raise ApiError(422, "ADS_HARD_SPEND_CAP_EXCEEDED", "Approved budget would breach workspace hard spend cap.")
                ctx.store.update("campaigns", ctx.tid, camp["id"], {"budget_daily": proposed_daily, "last_approved_by": ctx.user["name"]})
        agent = ctx.store.get("agents", ctx.tid, p.get("agent_id", ""))
        if agent:
            skills = list(agent.get("skills", []))
            skills.append(
                {
                    "id": nid("skl"),
                    "name": p["title"][:60],
                    "version": max([s.get("version", 1) for s in skills] or [0]) + 1,
                    "status": "promoted",
                    "lift": 0.0,
                    "origin": "learned",
                    "promoted_by": ctx.user["name"],
                    "promoted_at": now(),
                }
            )
            ctx.store.update("agents", ctx.tid, agent["id"], {"skills": skills})
    ctx.audit(f"learning.{status}", p["id"], {"agent": p.get("agent_id"), "risk": p.get("risk"), "note": note})
    rt.HUB.publish(ctx.tid, "learning.updated", p["id"], {"status": status}, collections=("proposals",))
    return {"proposal": upd}


# ==========================================================================
# Automations / social / ads / research / funnels / pages
# ==========================================================================
@route("GET", r"/api/v1/automations")
def automations_list(ctx: Ctx) -> dict:
    rows = ctx.store.all("automations", ctx.tid)
    runs = [r for r in ctx.store.all("runs", ctx.tid) if r.get("type") == "automation_run"]
    by_automation: dict[str, dict] = {}
    for run in runs:
        bucket = by_automation.setdefault(run.get("automation_id", ""), {"ok": 0, "blocked": 0,
                                                                        "deferred": 0, "dead": 0, "pending": 0})
        bucket[run.get("status", "pending")] = bucket.get(run.get("status", "pending"), 0) + 1
    out = []
    for a in rows:
        stats = by_automation.get(a["id"], {})
        out.append({
            **a,
            "step_meta": [{"type": s_ if isinstance(s_, str) else s_.get("type"),
                           "label": autm.STEP_TYPES.get(s_ if isinstance(s_, str) else s_.get("type"), {}).get("label", ""),
                           "outbound": autm.STEP_TYPES.get(s_ if isinstance(s_, str) else s_.get("type"), {}).get("outbound", False)}
                          for s_ in (a.get("steps") or [])],
            "trigger_label": autm.TRIGGERS.get(a.get("trigger", ""), {}).get("label", a.get("trigger", "")),
            "stats": stats,
        })
    return {"automations": out, "count": len(out),
            "triggers": [{"slug": k, **v} for k, v in autm.TRIGGERS.items()],
            "step_types": [{"slug": k, **v} for k, v in autm.STEP_TYPES.items()],
            "runtime": {"tick_seconds": autm.DEFAULT_TICK_SECONDS, "max_attempts": autm.MAX_ATTEMPTS,
                        "frequency_cap_per_day": autm.FREQUENCY_CAP_PER_DAY}}


def _clean_steps(raw: Any) -> list[dict]:
    """Steps are typed; an unknown type is a 422, never a step that dies at 3 a.m."""
    steps: list[dict] = []
    for item in (raw or [])[:12]:
        step = item if isinstance(item, dict) else {"type": str(item)}
        kind = str(step.get("type") or "").strip()
        if kind not in autm.STEP_TYPES:
            raise ApiError(422, "UNKNOWN_STEP_TYPE",
                           f"Unknown step type '{kind}'.",
                           [{"step_types": sorted(autm.STEP_TYPES)}])
        clean: dict = {"type": kind}
        for key in ("text", "template", "document", "title", "stage", "priority", "duration"):
            if step.get(key):
                clean[key] = sec.clean(step[key], 600)
        if "align_to_window" in step:
            clean["align_to_window"] = bool(step.get("align_to_window", True))
        if step.get("due_min") not in (None, ""):
            try:
                clean["due_min"] = max(1, min(20_160, int(step["due_min"])))
            except Exception:
                raise ApiError(400, "VALIDATION_FAILED", "'due_min' must be an integer")
        missing = [n for n in autm.STEP_TYPES[kind]["needs"] if n not in clean]
        if missing:
            raise ApiError(422, "STEP_INCOMPLETE",
                           f"Step '{kind}' needs: {', '.join(missing)}.", [{"step": clean}])
        if kind == "wait":
            try:
                autm.parse_wait_seconds(clean)
            except autm.AutomationError as exc:
                raise ApiError(422, exc.code, exc.message)
        steps.append(clean)
    if not steps:
        raise ApiError(400, "VALIDATION_FAILED", "'steps' must contain at least one step.")
    return steps


@route("POST", r"/api/v1/automations")
def automations_create(ctx: Ctx) -> dict:
    ctx.require("write")
    trigger = ctx.body_str("trigger", 40) or "lead.created"
    if trigger not in autm.TRIGGERS:
        raise ApiError(422, "UNKNOWN_TRIGGER", f"Unknown trigger '{trigger}'.",
                       [{"triggers": sorted(autm.TRIGGERS)}])
    a = ctx.store.insert("automations", ctx.tid, {
        "name": ctx.body_str("name", 100, required=True),
        "trigger": trigger,
        "steps": _clean_steps(ctx.body.get("steps")),
        "enabled": bool(ctx.body.get("enabled", False)),
        "runs": 0,
        "compliance_gate": ["consent", "quiet hours", "frequency cap"],
    }, prefix="aut")
    ctx.audit("automation.created", a["id"], {"name": a["name"], "trigger": trigger,
                                              "steps": len(a["steps"])})
    return {"automation": a}


@route("GET", r"/api/v1/automations/playbooks")
def automation_playbooks(ctx: Ctx) -> dict:
    """The playbook catalogue — the nine-pattern list from the open-agency-os research."""
    installed = {a.get("playbook") for a in ctx.store.all("automations", ctx.tid)}
    return {"playbooks": [{**p, "installed": p["slug"] in installed} for p in autm.PLAYBOOKS],
            "count": len(autm.PLAYBOOKS)}


@route("POST", r"/api/v1/automations/playbooks/(?P<slug>[a-z0-9\-]+)/install")
def automation_playbook_install(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        doc = autm.install_playbook(ctx.params["slug"])
    except autm.AutomationError as exc:
        raise ApiError(404, exc.code, exc.message, [exc.details])
    candidates = [a for a in ctx.store.all("automations", ctx.tid)
                  if a.get("playbook") == ctx.params["slug"]]
    existing = next((a for a in candidates if a.get("enabled") and not a.get("paused_reason")), None) \
        or (candidates[0] if candidates else None)
    if existing:
        # Re-installing is a no-op, not a resurrection: a human who paused this
        # automation (a duplicate, a seasonal offer) must not find it running again
        # because somebody clicked "Install" in the catalogue. Enabling is an
        # explicit act — `{"enable": true}` here, or PATCH /automations/{id}.
        enable = bool(ctx.body.get("enable"))
        patch = {"enabled": True, "paused_reason": ""} if enable else {}
        if patch:
            ctx.store.update("automations", ctx.tid, existing["id"], patch)
        return {"automation": {**existing, **patch}, "already_installed": True,
                "enabled": bool(existing.get("enabled")) or enable,
                "paused_reason": "" if enable else (existing.get("paused_reason") or "")}
    a = ctx.store.insert("automations", ctx.tid, {**doc, "runs": 0}, prefix="aut")
    ctx.audit("automation.playbook_installed", a["id"], {"playbook": doc["playbook"]})
    return {"automation": a, "already_installed": False}


@route("PATCH", r"/api/v1/automations/(?P<aid>[\w]+)")
def automations_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    patch = sec.pick(ctx.body, {"enabled", "name", "trigger"})
    if patch.get("enabled") is True:
        patch["paused_reason"] = ""      # re-enabling clears why it was paused
    if "trigger" in patch and patch["trigger"] not in autm.TRIGGERS:
        raise ApiError(422, "UNKNOWN_TRIGGER", f"Unknown trigger '{patch['trigger']}'.",
                       [{"triggers": sorted(autm.TRIGGERS)}])
    if "steps" in ctx.body:
        patch["steps"] = _clean_steps(ctx.body.get("steps"))
    a = ctx.store.update("automations", ctx.tid, ctx.params["aid"], patch)
    if not a:
        raise ApiError(404, "NOT_FOUND", "Automation not found.")
    ctx.audit("automation.updated", a["id"], {"enabled": a.get("enabled")})
    return {"automation": a}


@route("GET", r"/api/v1/automations/queue")
def automation_queue(ctx: Ctx) -> dict:
    rt = autm.Runtime(ctx.store)
    limit = ctx.qint("limit", 40, 200)
    queue = rt.queue(ctx.tid, limit=limit)
    queue["tick_seconds"] = autm.DEFAULT_TICK_SECONDS
    queue["triggers"] = [{"slug": k, "label": v["label"]} for k, v in autm.TRIGGERS.items()]
    return queue


@route("POST", r"/api/v1/automations/tick")
def automation_tick(ctx: Ctx) -> dict:
    """Run one tick now. Manager+ — a tick sends messages."""
    ctx.require("approve_cp")
    rt = autm.Runtime(ctx.store)
    dry = bool(ctx.body.get("dry_run"))
    result = rt.tick(ctx.tid, limit=ctx.body_int("limit", 100, 1, 500), dry_run=dry,
                     budget_sec=float(ctx.body_int("budget_sec", int(autm.TICK_BUDGET_SEC), 1, 60)))
    ctx.audit("automation.ticked", "", {"planned": result["counts"]["planned"],
                                        "due": result["counts"]["due"],
                                        "dry_run": dry,
                                        "ok": result["counts"].get("ok", 0),
                                        "deferred": result["counts"].get("deferred", 0),
                                        "blocked": result["counts"].get("blocked", 0),
                                        "carried_over": result.get("carried_over", 0)})
    return result


@route("POST", r"/api/v1/automations/(?P<aid>[\w]+)/run")
def automation_run_once(ctx: Ctx) -> dict:
    """Fire one automation now, for the subjects its trigger currently matches."""
    ctx.require("approve_cp")
    automation = ctx.store.get("automations", ctx.tid, ctx.params["aid"])
    if not automation:
        raise ApiError(404, "NOT_FOUND", "Automation not found.")
    rt = autm.Runtime(ctx.store)
    planned = rt.plan(ctx.tid, automation_id=automation["id"])
    result = rt.tick(ctx.tid, limit=ctx.body_int("limit", 100, 1, 500),
                     dry_run=bool(ctx.body.get("dry_run")))
    result["planned_for_automation"] = len(planned)
    ctx.audit("automation.ran", automation["id"], {"planned": len(planned),
                                                   "dry_run": bool(ctx.body.get("dry_run"))})
    return result


@route("GET", r"/api/v1/automations/runs")
def automation_runs(ctx: Ctx) -> dict:
    status = (ctx.query.get("status", [""])[0] or "").strip()
    rows = [r for r in ctx.store.all("runs", ctx.tid) if r.get("type") == "automation_run"]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: int(r.get("created_at") or 0), reverse=True)
    return {"runs": [autm.Runtime._brief(r) | {"trace": (r.get("trace") or [])[-4:]}
                     for r in rows[: ctx.qint("limit", 50, 200)]],
            "count": len(rows), "statuses": list(autm.RUN_STATUSES)}


@route("GET", r"/api/v1/social")
def social_list(ctx: Ctx) -> dict:
    return {"posts": sorted(ctx.store.all("social", ctx.tid), key=lambda p: p.get("scheduled_at", 0))}


@route("POST", r"/api/v1/social")
def social_create(ctx: Ctx) -> dict:
    ctx.require("write")
    p = ctx.store.insert(
        "social",
        ctx.tid,
        {
            "channel": ctx.body_str("channel", 24) or "instagram",
            "text": ctx.body_str("text", 900, required=True),
            "media_hint": ctx.body_str("media_hint", 40),
            "scheduled_at": int(ctx.body.get("scheduled_at") or now() + 86400),
            "status": "draft",
            "engagement": None,
        },
        prefix="soc",
    )
    ctx.audit("social.drafted", p["id"], {"channel": p["channel"]})
    return {"post": p}


@route("PATCH", r"/api/v1/social/(?P<sid>[\w]+)")
def social_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    patch = sec.pick(ctx.body, {"status", "scheduled_at", "text", "channel"})
    if patch.get("status") == "published" and ctx.role not in ("owner", "admin", "manager"):
        raise ApiError(403, "FORBIDDEN_SCOPE", "Publishing requires a manager or above (brand-safety gate).")
    p = ctx.store.update("social", ctx.tid, ctx.params["sid"], patch)
    if not p:
        raise ApiError(404, "NOT_FOUND", "Post not found.")
    ctx.audit("social.updated", p["id"], patch)
    return {"post": p}


@route("GET", r"/api/v1/campaigns")
def campaigns_list(ctx: Ctx) -> dict:
    return {"campaigns": ctx.store.all("campaigns", ctx.tid)}


@route("POST", r"/api/v1/campaigns")
def campaigns_create(ctx: Ctx) -> dict:
    ctx.require("write")
    if not ctx.tenant.get("features", {}).get("ads_autopilot", True):
        raise ApiError(403, "FEATURE_DISABLED", "Ads autopilot is disabled.")
    name = ctx.body_str("name", 120, required=True)
    budget_daily = ctx.body_int("budget_daily", 2000, 100, 500_000)
    rera_violations = aigov.inspect_rera_advertising(name)
    if rera_violations:
        raise ApiError(422, "RERA_AD_CLAIM_BLOCKED", "; ".join(rera_violations))
    headroom = p3_mod.check_campaign_spend_headroom(ctx.store, ctx.tenant, proposed_daily_inr=budget_daily)
    if headroom["daily_cap_exceeded"] or headroom["monthly_cap_exceeded"]:
        raise ApiError(422, "ADS_HARD_SPEND_CAP_EXCEEDED", "Campaign daily budget exceeds workspace hard spend cap.")
    c = ctx.store.insert(
        "campaigns",
        ctx.tid,
        {
            "platform": ctx.body_str("platform", 20) or "google_ads",
            "name": name,
            "objective": ctx.body_str("objective", 20) or "leads",
            "budget_daily": budget_daily,
            "spend": 0,
            "leads": 0,
            "cpl": 0,
            "status": "draft",
            "roas": 0,
        },
        prefix="cmp",
    )
    ctx.audit("campaign.created", c["id"], {"platform": c["platform"], "budget": c["budget_daily"]})
    return {"campaign": c}


@route("PATCH", r"/api/v1/campaigns/(?P<cid>[\w]+)")
def campaigns_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    existing = ctx.store.get("campaigns", ctx.tid, ctx.params["cid"])
    if not existing:
        raise ApiError(404, "NOT_FOUND", "Campaign not found.")
    patch = sec.pick(ctx.body, {"status", "budget_daily", "name"})
    if "name" in patch:
        rera_violations = aigov.inspect_rera_advertising(str(patch["name"]))
        if rera_violations:
            raise ApiError(422, "RERA_AD_CLAIM_BLOCKED", "; ".join(rera_violations))
    if "budget_daily" in patch:
        new_b = int(patch["budget_daily"])
        old_b = max(1, int(existing.get("budget_daily") or 1))
        headroom = p3_mod.check_campaign_spend_headroom(ctx.store, ctx.tenant, campaign_id=existing["id"], proposed_daily_inr=new_b)
        if headroom["daily_cap_exceeded"] or headroom["monthly_cap_exceeded"]:
            raise ApiError(422, "ADS_HARD_SPEND_CAP_EXCEEDED", "Updated daily budget exceeds workspace hard spend cap.")
        policy = headroom["policy"]
        pct_increase = ((new_b - old_b) / old_b) * 100.0
        if new_b > old_b and pct_increase > policy["require_approval_above_pct"]:
            raise ApiError(
                422,
                "APPROVAL_REQUIRED",
                f"Raising budget by {pct_increase:.1f}% (> {policy['require_approval_above_pct']}%) requires human approval via /api/v1/ads/budget-requests.",
            )
    if patch.get("status") == "active":
        headroom = p3_mod.check_campaign_spend_headroom(
            ctx.store, ctx.tenant, campaign_id=existing["id"], proposed_daily_inr=int(patch.get("budget_daily") or existing.get("budget_daily") or 0)
        )
        if headroom["monthly_cap_exceeded"]:
            raise ApiError(422, "ADS_MONTHLY_SPEND_CAP_EXCEEDED", "Cannot activate campaign: monthly hard spend cap reached.")
    c = ctx.store.update("campaigns", ctx.tid, ctx.params["cid"], patch)
    ctx.audit("campaign.updated", c["id"], patch)
    return {"campaign": c}


@route("GET", r"/api/v1/ads/policy")
def ads_policy_get(ctx: Ctx) -> dict:
    ctx.require("write")
    headroom = p3_mod.check_campaign_spend_headroom(ctx.store, ctx.tenant)
    return {"policy": headroom["policy"], "headroom": headroom}


@route("PATCH", r"/api/v1/ads/policy")
def ads_policy_patch(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    try:
        pol = p3_mod.update_ads_policy(ctx.store, ctx.tenant, ctx.body or {})
    except ValueError as exc:
        raise ApiError(422, "ADS_INVALID_POLICY", str(exc))
    ctx.audit("ads.policy_updated", ctx.tid, pol)
    return {"policy": pol}


@route("POST", r"/api/v1/ads/budget-requests")
def ads_budget_request_create(ctx: Ctx) -> dict:
    ctx.require("write")
    cid = ctx.body_str("campaign_id", 40, required=True)
    camp = ctx.store.get("campaigns", ctx.tid, cid)
    if not camp:
        raise ApiError(404, "NOT_FOUND", "Campaign not found.")
    proposed = ctx.body_int("proposed_budget_daily", 0, 100, 5_000_000)
    reason = ctx.body_str("reason", 300)
    try:
        prop = p3_mod.stage_campaign_budget_request(
            ctx.store, ctx.tenant, camp, proposed, reason=reason, actor=ctx.user.get("email", "operator")
        )
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "ADS_CAP_EXCEEDED"
        raise ApiError(422, code, msg)
    ctx.audit("ads.budget_increase_requested", camp["id"], {"proposal_id": prop["id"], "proposed_budget_daily": proposed})
    rt.HUB.publish(ctx.tid, "learning.updated", prop["id"], {"status": "pending", "risk": "high"}, collections=("proposals",))
    return {"proposal": prop}


@route("POST", r"/api/v1/ads/autopilot/run", bucket="ai")
def ads_autopilot_run(ctx: Ctx) -> dict:
    ctx.require("write")
    if not ctx.tenant.get("features", {}).get("ads_autopilot", True):
        raise ApiError(403, "FEATURE_DISABLED", "Ads autopilot is disabled.")
    res = p3_mod.run_ads_autopilot(ctx.store, ctx.tenant, actor=ctx.user.get("email", "autopilot"))
    ctx.audit("ads.autopilot_executed", ctx.tid, {
        "paused": len(res["paused_campaigns"]),
        "staged_proposals": len(res["staged_budget_proposals"]),
        "hard_cap_halted": res["hard_cap_halted"],
    })
    return res


@route("GET", r"/api/v1/research")
def research_list(ctx: Ctx) -> dict:
    return {"jobs": sorted(ctx.store.all("research", ctx.tid), key=lambda j: -j["created_at"])}


@route("POST", r"/api/v1/research")
def research_create(ctx: Ctx) -> dict:
    ctx.require("write")
    topic = ctx.body_str("topic", 160, required=True)
    import random as _r

    rng = _r.Random(hash(topic) & 0xFFFF)
    titles = [
        f"{topic.title()} — what 2027 buyers actually search",
        f"5 mistakes in {topic} that cost ₹5L",
        f"Verify before you visit: {topic}",
    ]
    videos = [
        {
            "title": t,
            "channel": rng.choice(["MH Realty Insights", "Paisa Pundit", "Legal Land India", "Nanded Property Guru"]),
            "views": rng.randint(20_000, 400_000),
            "score": rng.randint(62, 95),
            "intent": rng.choice(["high", "medium"]),
            "angle": rng.choice(["data-led", "due-diligence authority", "comparison", "myth-busting"]),
        }
        for t in titles
    ]
    videos.sort(key=lambda v: -v["score"])
    job = ctx.store.insert(
        "research",
        ctx.tid,
        {
            "topic": topic,
            "engine": "JEV (research-only)",
            "status": "complete",
            "videos": videos,
            "brief": f"3-part series on {topic}. Hook: documents first. CTA: doc-pack funnel. Publish Tue/Thu/Sat 19:30 IST. Human approval before publish.",
            "recommendation": "Due-diligence authority angles score highest; price-led content converts lowest for plotted land.",
        },
        prefix="rsc",
    )
    ctx.audit("research.run", job["id"], {"topic": topic})
    return {"job": job}


@route("GET", r"/api/v1/funnels")
def funnels_list(ctx: Ctx) -> dict:
    return {"funnels": ctx.store.all("funnels", ctx.tid)}


@route("POST", r"/api/v1/funnels")
def funnels_create(ctx: Ctx) -> dict:
    ctx.require("write")
    slug = ctx.body_str("slug", 60, required=True)
    if not sec.valid_slug(slug):
        raise ApiError(400, "VALIDATION_FAILED", "Slug must be lowercase letters, digits and hyphens.")
    if any(f.get("slug") == slug for f in ctx.store.db["funnels"]):
        raise ApiError(409, "DUPLICATE_SLUG", "Slug already in use.")
    prop_id = ctx.body_str("property_id", 60)
    if prop_id and not ctx.store.get("properties", ctx.tid, prop_id):
        raise ApiError(404, "PROPERTY_NOT_FOUND", "Property not found in this workspace.")
    f = ctx.store.insert(
        "funnels",
        ctx.tid,
        {
            "name": ctx.body_str("name", 100) or slug,
            "slug": slug,
            "property_id": prop_id or None,
            "headline": ctx.body_str("headline", 160) or "Own a clear-title property",
            "sub": ctx.body_str("sub", 240),
            "cta": ctx.body_str("cta", 60) or "Book a site visit",
            "sections": [],
            "published": False,
            "visits": 0,
            "submissions": 0,
        },
        prefix="fnl",
    )
    ctx.audit("funnel.created", f["id"], {"slug": slug, "property_id": prop_id or None})
    return {"funnel": f}


@route("PATCH", r"/api/v1/funnels/(?P<fid>[\w]+)")
def funnels_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    patch = sec.pick(ctx.body, {"headline", "sub", "cta", "published", "name", "property_id"})
    if patch.get("property_id") and not ctx.store.get("properties", ctx.tid, str(patch["property_id"])):
        raise ApiError(404, "PROPERTY_NOT_FOUND", "Property not found in this workspace.")
    f = ctx.store.update("funnels", ctx.tid, ctx.params["fid"], patch)
    if not f:
        raise ApiError(404, "NOT_FOUND", "Funnel not found.")
    ctx.audit("funnel.updated", f["id"], patch)
    return {"funnel": f}


@route("GET", r"/api/v1/pages")
def pages_list(ctx: Ctx) -> dict:
    return {"pages": ctx.store.all("pages", ctx.tid)}


@route("PATCH", r"/api/v1/pages/(?P<pgid>[\w]+)")
def pages_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    p = ctx.store.update("pages", ctx.tid, ctx.params["pgid"], sec.pick(ctx.body, {"title", "body", "published"}))
    if not p:
        raise ApiError(404, "NOT_FOUND", "Page not found.")
    ctx.audit("page.updated", p["id"], {"slug": p.get("slug")})
    return {"page": p}


# ==========================================================================
# Analytics
# ==========================================================================
@route("GET", r"/api/v1/analytics/overview")
def analytics_overview(ctx: Ctx) -> dict:
    ctx.store.prefetch(("deals", "contacts", "tasks"), ctx.tid)
    a = ctx.store.analytics(ctx.tid)
    deals = ctx.store.all("deals", ctx.tid)
    contacts = ctx.store.all("contacts", ctx.tid)
    won = [d for d in deals if d.get("stage") == "won"]
    pipeline_value = sum(d.get("value", 0) for d in deals if d.get("stage") not in ("won", "lost"))
    leaked = len([c for c in contacts if (c.get("consent") or {}).get("status") != "granted"])
    return {
        "kpi": {
            "leads_30d": sum(a["leads"][-1:]),
            "site_visits_30d": sum(a["site_visits"][-1:]),
            "bookings_30d": sum(a["bookings"][-1:]),
            "revenue_30d": sum(a["revenue"][-1:]),
            "pipeline_value": pipeline_value,
            "won_value": sum(d.get("value", 0) for d in won),
            "open_deals": len([d for d in deals if d.get("stage") not in ("won", "lost")]),
            "contacts": len(contacts),
            "avg_lead_score": round(sum(c.get("score", 0) for c in contacts) / max(1, len(contacts))),
            "consent_gap": leaked,
            "ai_handled_pct": 0.64,
            "median_first_response_s": 78,
        },
        "series": a,
        "today": {
            "site_visits_booked": 6,
            "ai_calls_placed": 41,
            "whatsapp_sent": 268,
            "holds_expiring": 2,
            "tasks_due": len([t for t in ctx.store.all("tasks", ctx.tid) if not t.get("done")]),
        },
    }


@route("GET", r"/api/v1/bootstrap")
def workspace_bootstrap(ctx: Ctx) -> dict:
    """Single-round-trip operator shell + Today Action Desk hydration (`v1.40.0`).

    Batches all tenant operational collections into one RLS `documents_many`
    query so login and tab entry never fan out into 5 parallel serverless calls.
    """
    ctx.store.prefetch(
        (
            "contacts",
            "deals",
            "properties",
            "units",
            "tasks",
            "agents",
            "proposals",
            "site_visits",
            "conversations",
            "automations",
            "cp_registrations",
        ),
        ctx.tid,
    )
    at = now()
    me_payload = auth_me(ctx)
    ov = analytics_overview(ctx)
    contacts = ctx.store.all("contacts", ctx.tid)
    deals = ctx.store.all("deals", ctx.tid)
    props = ctx.store.all("properties", ctx.tid)
    units_raw = ctx.store.all("units", ctx.tid)
    tasks_raw = sorted(ctx.store.all("tasks", ctx.tid), key=lambda t: t.get("due_at", 0))
    agents_raw = [out_agent(a) for a in ctx.store.all("agents", ctx.tid)]
    proposals_raw = sorted(ctx.store.all("proposals", ctx.tid), key=lambda p: -p.get("created_at", 0))
    agent_names = {a["id"]: a["name"] for a in agents_raw}
    for p in proposals_raw:
        p["agent_name"] = agent_names.get(p.get("agent_id"), "—")

    # Speed-to-lead urgent queue
    sla_overdue = 0
    sla_due_soon = 0
    sla_met = 0
    urgent_leads = []
    cmap = {c["id"]: c for c in contacts}
    pmap = {p["id"]: p for p in props}
    for c in contacts:
        st = ing.sla_state(c, at)
        s_kind = st.get("status")
        if s_kind in ("overdue", "breached"):
            sla_overdue += 1
        elif s_kind in ("due_soon", "open", "pending"):
            sla_due_soon += 1
        elif s_kind == "met":
            sla_met += 1
        if s_kind in ("overdue", "breached", "due_soon", "open", "pending"):
            urgent_leads.append({
                "id": c["id"],
                "name": c.get("name"),
                "source": c.get("source"),
                "city": c.get("city"),
                "stage": c.get("stage"),
                "score": c.get("score"),
                "budget_max": c.get("budget_max"),
                "consent_ok": (c.get("consent") or {}).get("status") == "granted",
                "sla": st,
            })
    urgent_leads.sort(key=lambda x: (0 if x["sla"].get("status") in ("overdue", "breached") else 1, x["sla"].get("due_at") or 0))

    # Site visits enriched
    visits_raw = sorted(ctx.store.all("site_visits", ctx.tid), key=lambda v: v.get("scheduled_at", 0))
    visits_out = []
    for v in visits_raw[:25]:
        c_doc = cmap.get(v.get("contact_id", ""), {})
        p_doc = pmap.get(v.get("property_id", ""), {})
        visits_out.append({
            **v,
            "contact_name": c_doc.get("name") or v.get("contact_name") or "Buyer",
            "property_title": p_doc.get("title") or v.get("property_title") or "Property",
            "city": p_doc.get("city") or c_doc.get("city") or "",
        })

    # Units & holds summary
    described_units = [ivt.describe(u, at) for u in units_raw]
    unit_summary = ivt.summarize(described_units, at) if described_units else {
        "total": 0, "available": 0, "blocked": 0, "booked": 0, "sold": 0, "withdrawn": 0,
    }
    expiring_holds = [
        {
            "id": u["id"],
            "unit_no": u.get("unit_no"),
            "property_id": u.get("property_id"),
            "property_title": (pmap.get(u.get("property_id", "")) or {}).get("title", ""),
            "price": u.get("price"),
            "expires_at": (u.get("block") or {}).get("expires_at"),
        }
        for u in described_units
        if u.get("effective_status") == "blocked"
    ]
    expiring_holds.sort(key=lambda x: x.get("expires_at") or 0)

    # Inbox unread summary
    convs = ctx.store.all("conversations", ctx.tid)
    unread_threads = sum(1 for cv in convs if cv.get("unread", 0) > 0)

    # Pipeline stage rollup
    stage_counts = {s: 0 for s in vt.STAGES}
    weighted_val = 0
    for d in deals:
        st_name = d.get("stage", "new")
        stage_counts[st_name] = stage_counts.get(st_name, 0) + 1
        if st_name not in ("lost",):
            weighted_val += round((d.get("value", 0) * (d.get("probability") or 0)) / 100)

    return {
        "me": me_payload,
        "csrf": sec.csrf_token(ctx.raw_token) if ctx.raw_token else None,
        "overview": ov,
        "tasks": tasks_raw[:30],
        "agents": agents_raw,
        "proposals": proposals_raw[:20],
        "pending_approvals": sum(1 for p in proposals_raw if p.get("status") == "pending"),
        "inbox_unread": unread_threads,
        "sla": {
            "overdue": sla_overdue,
            "due_soon": sla_due_soon,
            "met": sla_met,
            "urgent": urgent_leads[:8],
        },
        "site_visits": visits_out,
        "units_summary": {
            **unit_summary,
            "expiring_holds": expiring_holds[:6],
        },
        "pipeline_summary": {
            "stages": list(vt.STAGES),
            "counts": stage_counts,
            "total_deals": len(deals),
            "weighted_value": weighted_val,
        },
        "counts": {
            "contacts": len(contacts),
            "deals": len(deals),
            "properties": len(props),
            "units": len(units_raw),
            "agents": len(agents_raw),
            "automations": len(ctx.store.all("automations", ctx.tid)),
            "cp_registrations": len(ctx.store.all("cp_registrations", ctx.tid)),
        },
    }


@route("GET", r"/api/v1/analytics/forecast")
def analytics_forecast(ctx: Ctx) -> dict:
    metric = ctx.query.get("metric", ["leads"])[0]
    horizon = min(6, ctx.qint("horizon", 3, 6))
    a = ctx.store.analytics(ctx.tid)
    if metric not in ("leads", "site_visits", "bookings", "revenue"):
        raise ApiError(400, "VALIDATION_FAILED", "metric must be leads, site_visits, bookings or revenue.")
    f = ag.forecast([float(x) for x in a[metric]], horizon=horizon)
    return {"metric": metric, "history": a[metric], "months": a["months"], "forecast": f}


@route("GET", r"/api/v1/analytics/insights")
def analytics_insights(ctx: Ctx) -> dict:
    a = ctx.store.analytics(ctx.tid)
    leads = a["leads"]
    trend = leads[-1] - leads[0]
    best_source = max(a["sources"], key=lambda s: s["conversion"])
    worst_source = min([s for s in a["sources"] if s["cpl"] > 0], key=lambda s: s["conversion"])
    return {
        "insights": [
            {
                "title": "Shift 12% of Meta budget to Google Search",
                "detail": f"Google converts at {int(a['sources'][3]['conversion']*100)}% vs Meta {int(a['sources'][2]['conversion']*100)}% at a similar CPL. Expected +9 site visits/month.",
                "confidence": 0.78,
                "impact": "high",
            },
            {
                "title": "Referral is the highest-converting channel — formalise it",
                "detail": f"Referral converts at {int(best_source['conversion']*100)}% with zero CPL. A ₹25k referral incentive pays back on the first booking.",
                "confidence": 0.83,
                "impact": "high",
            },
            {
                "title": f"Pause or rebuild: {worst_source['name']}",
                "detail": f"CPL ₹{worst_source['cpl']} with {int(worst_source['conversion']*100)}% conversion — the worst unit economics in the mix.",
                "confidence": 0.71,
                "impact": "medium",
            },
            {
                "title": "Doc-pack-first sequence lifts show-rate",
                "detail": "Conversations where the document pack preceded the visit confirmation showed higher attendance. Test in the next 200 leads.",
                "confidence": 0.66,
                "impact": "medium",
            },
            {
                "title": f"Lead volume trend {trend:+d} over 12 months",
                "detail": "Seasonality peaks in Oct–Nov (festive). Increase ad budget 3 weeks before the peak rather than during it.",
                "confidence": 0.74,
                "impact": "medium",
            },
        ]
    }


# ==========================================================================
# Integrations
# ==========================================================================
@route("GET", r"/api/v1/integrations")
def integrations_list(ctx: Ctx) -> dict:
    custom_hosts = sorted(
        {
            norm
            for h in ((ctx.tenant.get("settings") or {}).get("egress_allowlist") or [])
            for ok, norm in [sec.valid_egress_host_pattern(str(h or ""))]
            if ok
        }
    )
    return {
        "integrations": ctx.store.all("integrations", ctx.tid),
        "egress_allowlist": sorted(sec.tenant_egress_hosts(ctx.tenant)),
        "custom_egress_hosts": custom_hosts,
    }


@route("POST", r"/api/v1/integrations/(?P<slug>[\w\-]+)/connect")
def integrations_connect(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    slug = ctx.params["slug"]
    rows = [i for i in ctx.store.all("integrations", ctx.tid) if i.get("slug") == slug]
    if not rows:
        raise ApiError(404, "NOT_FOUND", "Unknown connector.")
    upd = ctx.store.update("integrations", ctx.tid, rows[0]["id"], {"status": "connected", "connected_at": now(), "health": "healthy"})
    ctx.audit("integration.connected", slug, {})
    return {"integration": upd}


@route("POST", r"/api/v1/integrations/(?P<slug>[\w\-]+)/disconnect")
def integrations_disconnect(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    slug = ctx.params["slug"]
    rows = [i for i in ctx.store.all("integrations", ctx.tid) if i.get("slug") == slug]
    if not rows:
        raise ApiError(404, "NOT_FOUND", "Unknown connector.")
    upd = ctx.store.update("integrations", ctx.tid, rows[0]["id"], {"status": "available", "health": "n/a"})
    ctx.audit("integration.disconnected", slug, {})
    return {"integration": upd}


@route("GET", r"/api/v1/webhooks")
def webhooks_list(ctx: Ctx) -> dict:
    return {"webhooks": ctx.store.all("webhooks", ctx.tid), "deliveries": ctx.store.all("deliveries", ctx.tid)[-20:]}


@route("POST", r"/api/v1/webhooks")
def webhooks_create(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    url = ctx.body_str("url", 400, required=True)
    ok, reason = sec.ssrf_check(url, extra_hosts=sec.tenant_egress_hosts(ctx.tenant))
    if not ok:
        raise ApiError(400, "INVALID_WEBHOOK_URL", f"Blocked by egress policy: {reason}")
    w = ctx.store.insert(
        "webhooks",
        ctx.tid,
        {"url": url, "events": [sec.clean(e, 40) for e in (ctx.body.get("events") or ["contact.created"])][:10], "secret_hint": "whsec_••••" + nid("k")[-4:], "active": True},
        prefix="whk",
    )
    ctx.audit("webhook.created", w["id"], {"url": url})
    return {"webhook": w}


# ==========================================================================
# Universal Provider, Tool & Inbound Webhook Fabric (`docs/39`)
# ==========================================================================
@route("GET", r"/api/v1/connectors")
def connectors_list(ctx: Ctx) -> dict:
    presets = {
        k: {
            "preset": k,
            "category": v["category"],
            "label": v["label"],
            "default_url": v.get("default_url", ""),
            "auth_scheme": v.get("auth_scheme", "bearer"),
            "auth_header": v.get("auth_header", ""),
        }
        for k, v in conn_mod.PROVIDER_PRESETS.items()
    }
    return {
        "presets": presets,
        "categories": list(conn_mod.CATEGORIES),
        "auth_schemes": list(conn_mod.AUTH_SCHEMES),
        "connectors": [conn_mod.public_connector(r) for r in conn_mod.list_connectors(ctx.tenant)],
        "inbound_hooks": [conn_mod.public_inbound_hook(r) for r in conn_mod.list_inbound_hooks(ctx.tenant)],
        "channels": channels.status_summary(ctx.tenant),
        "egress_allowlist": sorted(sec.tenant_egress_hosts(ctx.tenant)),
        "custom_egress_hosts": sorted(((ctx.tenant.get("settings") or {}).get("egress_allowlist") or [])),
    }


@route("POST", r"/api/v1/connectors")
def connectors_upsert(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    category = (ctx.body_str("category", 24, required=True)).lower()
    if category not in conn_mod.CATEGORIES:
        raise ApiError(422, "VALIDATION_FAILED", f"category must be one of {conn_mod.CATEGORIES}.")
    preset = (ctx.body_str("preset", 40) or "custom_http").lower()
    if preset not in conn_mod.PROVIDER_PRESETS:
        raise ApiError(422, "VALIDATION_FAILED", f"Unknown preset '{preset}'. Use 'custom_http' for new providers.")
    pmeta = conn_mod.PROVIDER_PRESETS[preset]
    if pmeta["category"] not in ("*", category):
        raise ApiError(422, "VALIDATION_FAILED", f"Preset '{preset}' belongs to category '{pmeta['category']}'.")

    name = ctx.body_str("name", 80) or pmeta["label"][:80]
    raw_slug = ctx.body_str("slug", 48) or re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:36] or preset
    if not re.fullmatch(r"[a-z0-9_]{2,48}", raw_slug):
        raise ApiError(422, "VALIDATION_FAILED", "slug must match ^[a-z0-9_]{2,48}$.")

    endpoint_url = (ctx.body_str("endpoint_url", 400) or pmeta.get("default_url") or "").strip()
    if preset == "neon_ai_gateway" and not endpoint_url:
        endpoint_url = cfg.get("NEON_AI_GATEWAY_URL", "").strip()

    settings = dict(ctx.tenant.get("settings") or {})
    dyn_hosts = list(settings.get("egress_allowlist") or [])

    if endpoint_url:
        # Zero-blockage convenience: when `auto_allow_host` (default True) is on,
        # automatically add a valid public HTTPS hostname to the tenant allowlist
        # while still enforcing the hard SSRF wall against private/loopback/internal IPs.
        from urllib.parse import urlparse as _up
        cand_host = (_up(endpoint_url).hostname or "").lower()
        if ctx.body.get("auto_allow_host", True) and cand_host:
            ok_pat, norm_host = sec.valid_egress_host_pattern(cand_host)
            if ok_pat and norm_host not in sec.EGRESS_ALLOWLIST and norm_host not in dyn_hosts:
                dyn_hosts.append(norm_host)
        ok_ssrf, reason = sec.ssrf_check(
            endpoint_url,
            strict_allowlist=True,
            extra_hosts=set(sec.EGRESS_ALLOWLIST) | set(dyn_hosts),
            allow_loopback_dev=True,
        )
        if not ok_ssrf:
            raise ApiError(400, "EGRESS_BLOCKED", f"Endpoint URL rejected by SSRF guard: {reason}")

    auth_scheme = (ctx.body_str("auth_scheme", 16) or pmeta.get("auth_scheme") or "bearer").lower()
    if auth_scheme not in conn_mod.AUTH_SCHEMES:
        raise ApiError(422, "VALIDATION_FAILED", f"auth_scheme must be one of {conn_mod.AUTH_SCHEMES}.")
    auth_header = ctx.body_str("auth_header", 64) or pmeta.get("auth_header") or ""
    risk = (ctx.body_str("risk", 20) or "read").lower()
    if risk not in ("read", "write", "outbound", "irreversible"):
        raise ApiError(422, "VALIDATION_FAILED", "risk must be read, write, outbound, or irreversible.")

    existing = conn_mod.list_connectors(ctx.tenant)
    conn_id = ctx.body_str("id", 40) or next((r["id"] for r in existing if r.get("slug") == raw_slug and r.get("category") == category), "") or nid("con")
    prev = next((r for r in existing if r["id"] == conn_id), {})

    aad = conn_mod.connector_aad(ctx.tid, conn_id)
    api_key = str(ctx.body.get("api_key") or "").strip()
    api_secret = str(ctx.body.get("api_secret") or "").strip()
    secret_enc = sec.seal(api_key, aad=aad) if api_key else str(prev.get("secret_enc") or "")
    secret2_enc = sec.seal(api_secret, aad=aad) if api_secret else str(prev.get("secret2_enc") or "")
    secret_fp = cfg.fingerprint(api_key) if api_key else str(prev.get("secret_fp") or "")

    raw_hdrs = ctx.body.get("headers")
    clean_hdrs: dict[str, str] = {}
    if isinstance(raw_hdrs, dict):
        for hk, hv in list(raw_hdrs.items())[:12]:
            if re.fullmatch(r"[A-Za-z0-9\-]{1,64}", str(hk)):
                clean_hdrs[str(hk)] = str(hv)[:240]

    body_tmpl = ctx.body.get("body_template")
    if body_tmpl is not None and not isinstance(body_tmpl, dict):
        raise ApiError(422, "VALIDATION_FAILED", "body_template must be a JSON object or null.")

    is_primary = bool(ctx.body.get("is_primary", True))
    row = {
        "id": conn_id,
        "name": name,
        "slug": raw_slug,
        "category": category,
        "preset": preset,
        "endpoint_url": endpoint_url,
        "auth_scheme": auth_scheme,
        "auth_header": auth_header,
        "key_id_public": ctx.body_str("key_id_public", 120) or str(prev.get("key_id_public") or ""),
        "model": ctx.body_str("model", 80) or str(prev.get("model") or ""),
        "headers": clean_hdrs or (prev.get("headers") or {}),
        "body_template": body_tmpl if body_tmpl is not None else prev.get("body_template"),
        "response_id_path": ctx.body_str("response_id_path", 120) or str(prev.get("response_id_path") or ""),
        "response_text_path": ctx.body_str("response_text_path", 120) or str(prev.get("response_text_path") or ""),
        "risk": risk,
        "enabled": bool(ctx.body.get("enabled", True)),
        "is_primary": is_primary,
        "secret_enc": secret_enc,
        "secret2_enc": secret2_enc,
        "secret_fp": secret_fp,
        "last_test_ok": prev.get("last_test_ok"),
        "last_test_at": prev.get("last_test_at"),
        "created_at": prev.get("created_at") or now(),
        "updated_at": now(),
    }

    updated_list = []
    for r in existing:
        if r["id"] == conn_id:
            continue
        if is_primary and r.get("category") == category:
            r = {**r, "is_primary": False}
        updated_list.append(r)
    updated_list.append(row)

    settings["connector_profiles"] = updated_list[-60:]
    settings["egress_allowlist"] = dyn_hosts[-80:]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("connector.upserted", conn_id, {"category": category, "preset": preset, "slug": raw_slug, "has_secret": bool(secret_enc)})
    return {"connector": conn_mod.public_connector(row), "channels": channels.status_summary(ctx.tenant)}


@route("DELETE", r"/api/v1/connectors/(?P<cid>[\w]+)")
def connectors_delete(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    cid = ctx.params["cid"]
    existing = conn_mod.list_connectors(ctx.tenant)
    kept = [r for r in existing if r["id"] != cid]
    if len(kept) == len(existing):
        raise ApiError(404, "NOT_FOUND", "Connector not found.")
    settings = dict(ctx.tenant.get("settings") or {})
    settings["connector_profiles"] = kept
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("connector.deleted", cid, {})
    return {"ok": True, "channels": channels.status_summary(ctx.tenant)}


@route("POST", r"/api/v1/connectors/(?P<cid>[\w]+)/test")
def connectors_test(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    cid = ctx.params["cid"]
    existing = conn_mod.list_connectors(ctx.tenant)
    row = next((r for r in existing if r["id"] == cid), None)
    if not row:
        raise ApiError(404, "NOT_FOUND", "Connector not found.")

    ok_probe = False
    detail = ""
    if bool(ctx.body.get("live_call")):
        try:
            if row.get("category") == "ai":
                res = conn_mod.complete_ai(
                    {"id": ctx.tid, "settings": {**(ctx.tenant.get("settings") or {}), "connector_profiles": [{**row, "enabled": True, "is_primary": True}]}},
                    [{"role": "user", "content": "Reply with OK"}],
                    model=str(row.get("model") or ""),
                )
                ok_probe = bool(res.get("ok"))
                detail = cfg.redact(str(res.get("text") or res.get("error") or ""))[:140]
            else:
                out = conn_mod.execute_connector_http(
                    ctx.tenant,
                    row,
                    {
                        "to": "+919999999999",
                        "phone": "+919999999999",
                        "text": "Health probe",
                        "prompt": "Health probe",
                        "language": "en",
                        "agent_name": "Probe",
                        "amount_paise": 10000,
                        "currency": "INR",
                        "receipt": "probe_01",
                        "tenant_id": ctx.tid,
                        "contact_id": "probe",
                        "activity_id": "probe",
                    },
                )
                ok_probe = 200 <= int(out["http_status"]) < 300
                detail = f"HTTP {out['http_status']}"
        except conn_mod.ConnectorError as exc:
            ok_probe = False
            detail = exc.message[:160]
    else:
        url = str(row.get("endpoint_url") or "")
        ok_ssrf, reason = sec.ssrf_check(url, strict_allowlist=True, extra_hosts=sec.tenant_egress_hosts(ctx.tenant), allow_loopback_dev=True)
        ok_probe = bool(ok_ssrf and (row.get("secret_enc") or row.get("auth_scheme") == "none"))
        detail = "SSRF/egress & credential check passed" if ok_probe else f"check failed: {reason}"

    settings = dict(ctx.tenant.get("settings") or {})
    settings["connector_profiles"] = [
        ({**r, "last_test_ok": ok_probe, "last_test_at": now()} if r["id"] == cid else r)
        for r in existing
    ]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("connector.tested", cid, {"ok": ok_probe, "live_call": bool(ctx.body.get("live_call"))})
    return {"ok": ok_probe, "detail": detail}


@route("POST", r"/api/v1/connectors/inbound-hooks")
def inbound_hooks_create(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    name = ctx.body_str("name", 80, required=True)
    target_action = (ctx.body_str("target_action", 32) or "lead.capture").lower()
    if target_action not in ("lead.capture", "whatsapp.inbound", "call.log", "deal.note"):
        raise ApiError(422, "VALIDATION_FAILED", "target_action must be lead.capture, whatsapp.inbound, call.log, or deal.note.")
    auth_mode = (ctx.body_str("auth_mode", 24) or "hmac_sha256").lower()
    if auth_mode not in ("hmac_sha256", "header_secret"):
        raise ApiError(422, "VALIDATION_FAILED", "auth_mode must be hmac_sha256 or header_secret.")
    sig_hdr = (ctx.body_str("signature_header", 64) or "x-webhook-signature").lower()
    if not re.fullmatch(r"[a-z0-9\-]{3,64}", sig_hdr):
        raise ApiError(422, "VALIDATION_FAILED", "Invalid signature_header name.")

    raw_map = ctx.body.get("field_map") or {}
    if not isinstance(raw_map, dict):
        raise ApiError(422, "VALIDATION_FAILED", "field_map must be a JSON object mapping CRM fields to payload dot-paths.")
    clean_map = {sec.clean(str(k), 32): sec.clean(str(v), 120) for k, v in list(raw_map.items())[:16] if k and v}

    hook_id = nid("ihk")
    raw_secret = str(ctx.body.get("signing_secret") or "").strip() or ("ihs_" + secrets_placeholder())
    aad = conn_mod.hook_aad(ctx.tid, hook_id)
    entry = {
        "id": hook_id,
        "name": name,
        "target_action": target_action,
        "auth_mode": auth_mode,
        "signature_header": sig_hdr,
        "field_map": clean_map,
        "enabled": True,
        "secret_enc": sec.seal(raw_secret, aad=aad),
        "secret_fp": cfg.fingerprint(raw_secret),
        "deliveries": 0,
        "last_received_at": None,
        "created_at": now(),
    }
    settings = dict(ctx.tenant.get("settings") or {})
    hooks = conn_mod.list_inbound_hooks(ctx.tenant)
    hooks.append(entry)
    settings["inbound_hooks"] = hooks[-40:]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("inbound_hook.created", hook_id, {"name": name, "target_action": target_action})
    # Return the secret ONCE at creation (`Cache-Control: no-store` on all /api/**)
    return {
        "inbound_hook": conn_mod.public_inbound_hook(entry),
        "signing_secret_once": raw_secret,
    }


@route("DELETE", r"/api/v1/connectors/inbound-hooks/(?P<hid>[\w]+)")
def inbound_hooks_delete(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    hid = ctx.params["hid"]
    hooks = conn_mod.list_inbound_hooks(ctx.tenant)
    kept = [h for h in hooks if h["id"] != hid]
    if len(kept) == len(hooks):
        raise ApiError(404, "NOT_FOUND", "Inbound hook not found.")
    settings = dict(ctx.tenant.get("settings") or {})
    settings["inbound_hooks"] = kept
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("inbound_hook.deleted", hid, {})
    return {"ok": True}


@route("POST", r"/api/v1/webhooks/custom/(?P<hid>[\w]+)", public=True, bucket="webhook")
def custom_inbound_webhook_receive(ctx: Ctx) -> dict:
    hid = ctx.params["hid"]
    found_tenant = None
    found_hook = None
    for t in ctx.store.db["tenants"]:
        for h in conn_mod.list_inbound_hooks(t):
            if h["id"] == hid and h.get("enabled", True):
                found_tenant = t
                found_hook = h
                break
        if found_hook:
            break
    if not found_tenant or not found_hook:
        raise ApiError(404, "NOT_FOUND", "Inbound webhook endpoint not found or disabled.")

    tid = found_tenant["id"]
    secret = sec.unseal(found_hook["secret_enc"], aad=conn_mod.hook_aad(tid, hid))
    if not secret or len(secret.strip()) < 8:
        raise ApiError(403, "WEBHOOK_SECRET_NOT_CONFIGURED", "Inbound webhook secret is missing or invalid; rotate the hook secret.")

    sig_hdr_name = str(found_hook.get("signature_header") or "x-webhook-signature").lower()
    provided = (ctx.headers.get(sig_hdr_name) or "").strip()
    if not provided:
        raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", f"Missing {sig_hdr_name} header.")

    ts_hdr = (ctx.headers.get("x-webhook-timestamp") or "").strip()
    if ts_hdr:
        if not ts_hdr.isdigit() or abs(now() - int(ts_hdr)) > 300:
            raise ApiError(401, "WEBHOOK_TIMESTAMP_EXPIRED", "Webhook timestamp header outside 300s freshness window.")

    if found_hook.get("auth_mode") == "header_secret":
        if not sec.hmac.compare_digest(provided.encode(), secret.encode()):
            raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", "Invalid webhook header secret.")
    else:
        cand = provided[7:].strip() if provided.lower().startswith("sha256=") else provided
        expected_raw = sec.hmac.new(secret.encode(), ctx.raw, hashlib.sha256).hexdigest()
        sig_ok = sec.hmac.compare_digest(cand.lower(), expected_raw.lower())
        if not sig_ok and ts_hdr:
            expected_ts = sec.hmac.new(secret.encode(), f"{ts_hdr}.".encode("utf-8") + ctx.raw, hashlib.sha256).hexdigest()
            sig_ok = sec.hmac.compare_digest(cand.lower(), expected_ts.lower())
        if not sig_ok:
            raise ApiError(401, "INVALID_WEBHOOK_SIGNATURE", "Invalid HMAC-SHA256 signature.")

    nonce_hdr = (ctx.headers.get("x-webhook-id") or ctx.headers.get("x-request-id") or "").strip()
    replay_key = f"{hid}:{provided}:{ts_hdr}:{nonce_hdr}:{hashlib.sha256(ctx.raw).hexdigest()}"
    if not sec.claim_webhook_delivery(replay_key, ttl=600.0):
        raise ApiError(409, "WEBHOOK_REPLAY_DETECTED", "Duplicate or replayed webhook delivery rejected.")

    fmap = found_hook.get("field_map") or {}

    def _pick_field(field_name: str, fallback_keys: tuple[str, ...]) -> Any:
        if field_name in fmap:
            v = conn_mod.extract_dot_path(ctx.body, fmap[field_name])
            if v is not None and v != "":
                return v
        for fk in fallback_keys:
            v = conn_mod.extract_dot_path(ctx.body, fk)
            if v is not None and v != "":
                return v
        return ""

    action = found_hook.get("target_action", "lead.capture")
    phone_raw = sec.clean(str(_pick_field("phone", ("phone", "mobile", "from", "lead.phone"))), 24)
    name_raw = sec.clean(str(_pick_field("name", ("name", "full_name", "profile_name", "lead.name"))), 80) or "Webhook Lead"
    text_raw = sec.clean(str(_pick_field("text", ("text", "message", "body", "note", "transcript"))), 2000)
    city_raw = sec.clean(str(_pick_field("city", ("city", "location", "lead.city"))), 60)

    result_meta: dict[str, Any] = {"action": action}

    if action in ("lead.capture", "whatsapp.inbound", "call.log"):
        if not phone_raw or not sec.valid_phone(phone_raw):
            raise ApiError(422, "VALIDATION_FAILED", "Mapped payload did not contain a valid phone number.")
        p_hash = sec.blind_index(phone_raw, tenant_id=tid)
        existing_contacts = sec.find_by_blind_index(ctx.store, "contacts", tid, "phone_hash", phone_raw)
        if existing_contacts:
            contact = existing_contacts[0]
        else:
            contact = ctx.store.insert(
                "contacts",
                tid,
                {
                    "name": name_raw,
                    "phone_enc": sec.seal(phone_raw, aad=tid),
                    "phone_hash": p_hash,
                    "email_enc": "",
                    "email_hash": "",
                    "city": city_raw,
                    "source": f"Webhook:{found_hook['name'][:24]}",
                    "stage": "new",
                    "score": 62,
                    "intent": "warm",
                    "consent": {"status": "granted", "source": f"webhook:{hid}", "at": now()},
                    "created_at": now(),
                    "last_activity_at": now(),
                },
                prefix="cnt",
            )
        cid = contact["id"]
        result_meta["contact_id"] = cid

        if action == "whatsapp.inbound" or (action == "lead.capture" and text_raw):
            conv = next((x for x in ctx.store.all("conversations", tid) if x["contact_id"] == cid), None)
            in_msg = {
                "id": nid("msg"),
                "dir": "in",
                "channel": "whatsapp",
                "text": sec.redact_secrets(text_raw or f"New enquiry from {name_raw}"),
                "at": now(),
                "ai": False,
                "status": "received",
                "actor": contact.get("name") or name_raw,
            }
            if conv:
                ctx.store.update(
                    "conversations", tid, conv["id"],
                    {"messages": (conv.get("messages") or []) + [in_msg], "unread": int(conv.get("unread") or 0) + 1, "last_at": in_msg["at"]},
                )
            else:
                ctx.store.insert(
                    "conversations", tid,
                    {"contact_id": cid, "channel": "whatsapp", "unread": 1, "messages": [in_msg], "last_at": in_msg["at"]},
                    prefix="cnv",
                )
            ctx.store.update("contacts", tid, cid, {"last_inbound_at": in_msg["at"], "last_activity_at": in_msg["at"]})
            rt.HUB.publish(tid, "inbox.message", cid, {"channel": "whatsapp", "dir": "in"}, collections=("conversations", "contacts"))
        elif action == "call.log":
            outcome = sec.clean(str(_pick_field("outcome", ("outcome", "status")) or "answered"), 20).lower()
            if outcome not in ("answered", "missed", "no_answer", "voicemail", "busy"):
                outcome = "answered"
            dur = max(0, min(14400, int(_pick_field("duration_sec", ("duration_sec", "duration")) or 0)))
            act = ctx.store.insert(
                "activities", tid,
                {
                    "type": "call",
                    "contact_id": cid,
                    "direction": "in",
                    "outcome": outcome,
                    "duration_sec": dur,
                    "note": sec.redact_secrets(text_raw[:300]),
                    "actor": f"webhook:{found_hook['name'][:20]}",
                    "at": now(),
                },
                prefix="act",
            )
            result_meta["activity_id"] = act["id"]
            rt.HUB.publish(tid, "inbox.call", cid, {"direction": "in", "outcome": outcome}, collections=("activities", "contacts"))
        else:
            rt.HUB.publish(tid, "contact.created", cid, {"source": found_hook["name"]}, collections=("contacts",))
    elif action == "deal.note":
        did = sec.clean(str(_pick_field("deal_id", ("deal_id", "deal.id"))), 40)
        deal = ctx.store.get("deals", tid, did) if did else (ctx.store.all("deals", tid) or [None])[0]
        if not deal:
            raise ApiError(404, "NOT_FOUND", "Target deal not found.")
        act = ctx.store.insert(
            "activities", tid,
            {"type": "note", "deal_id": deal["id"], "contact_id": deal.get("contact_id", ""), "note": sec.redact_secrets(text_raw or "Inbound webhook update"), "actor": f"webhook:{found_hook['name'][:20]}", "at": now()},
            prefix="act",
        )
        result_meta["deal_id"] = deal["id"]
        result_meta["activity_id"] = act["id"]

    # Increment delivery counter on hook
    settings = dict(found_tenant.get("settings") or {})
    settings["inbound_hooks"] = [
        ({**h, "deliveries": int(h.get("deliveries") or 0) + 1, "last_received_at": now()} if h["id"] == hid else h)
        for h in conn_mod.list_inbound_hooks(found_tenant)
    ]
    ctx.store.update("tenants", tid, tid, {"settings": settings})
    found_tenant["settings"] = settings
    ctx.store.audit(tid, f"webhook:{hid}", "inbound_hook.received", hid, result_meta)
    return {"ok": True, **result_meta}


@route("POST", r"/api/v1/connectors/simulate")
def connectors_simulate(ctx: Ctx) -> dict:
    """Zero-blockage interactive simulator for inbound WhatsApp & playable AI voice calls."""
    ctx.require("write")
    kind = (ctx.body_str("kind", 32) or "whatsapp_inbound").lower()
    contacts = ctx.store.all("contacts", ctx.tid)
    if not contacts:
        raise ApiError(404, "NOT_FOUND", "Create at least one contact first.")
    cid = ctx.body_str("contact_id", 40) or contacts[0]["id"]
    c = ctx.store.get("contacts", ctx.tid, cid)
    if not c:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")

    if kind == "whatsapp_inbound":
        txt = ctx.body_str("text", 1000) or "Namaste! Please share the 7/12 extract, NA order & Saturday site-visit slots."
        conv = next((x for x in ctx.store.all("conversations", ctx.tid) if x["contact_id"] == cid), None)
        in_msg = {
            "id": nid("msg"),
            "dir": "in",
            "channel": "whatsapp",
            "text": sec.redact_secrets(txt),
            "at": now(),
            "ai": False,
            "status": "received",
            "provider_message_id": f"wamid.twin_{nid('w')[2:]}",
            "actor": c.get("name") or "Buyer",
        }
        if conv:
            ctx.store.update(
                "conversations", ctx.tid, conv["id"],
                {"messages": (conv.get("messages") or []) + [in_msg], "unread": int(conv.get("unread") or 0) + 1, "last_at": in_msg["at"], "channel": "whatsapp"},
            )
        else:
            ctx.store.insert(
                "conversations", ctx.tid,
                {"contact_id": cid, "channel": "whatsapp", "unread": 1, "messages": [in_msg], "last_at": in_msg["at"]},
                prefix="cnv",
            )
        ctx.store.update("contacts", ctx.tid, cid, {"last_inbound_at": in_msg["at"], "last_activity_at": in_msg["at"]})
        ctx.audit("simulator.whatsapp_inbound", cid, {"window_24h_opened": True})
        rt.HUB.publish(ctx.tid, "inbox.message", cid, {"channel": "whatsapp", "dir": "in", "simulated": True}, collections=("conversations", "contacts"))
        return {"ok": True, "kind": kind, "contact_id": cid, "message": in_msg, "window_24h_open": True}

    if kind == "voice_call":
        if (c.get("consent") or {}).get("status") != "granted":
            ctx.store.update("contacts", ctx.tid, cid, {"consent": {"status": "granted", "source": "local_twin", "at": now()}})
            c = ctx.store.get("contacts", ctx.tid, cid) or c
        act_id = nid("act")
        wav_bytes = conn_mod.synthesize_wav_clip(duration_ms=1200)
        transcript = conn_mod.build_local_voice_transcript(c, ctx.tenant, "Asha (Voice Twin)", "mr")
        rec_meta, rec_url = _archive_recording(ctx, act_id, wav_bytes, "audio/wav", "wav", strict_upload=False)
        act = ctx.store.insert(
            "activities", ctx.tid,
            {
                "id": act_id,
                "type": "call",
                "contact_id": cid,
                "direction": "out",
                "outcome": "answered",
                "duration_sec": 48,
                "provider": "local-voice-twin",
                "delivery_mode": "local-twin",
                "ai_disclosure": True,
                "recording": rec_meta,
                "transcript": transcript,
                "note": "Zero-blockage Local Twin qualification call (playable WAV + transcript)",
                "actor": ctx.user["name"],
                "at": now(),
            },
            prefix="act",
        )
        ctx.audit("simulator.voice_call", cid, {"activity_id": act["id"], "stored_in": rec_meta.get("stored_in")})
        rt.HUB.publish(ctx.tid, "inbox.call", cid, {"direction": "out", "outcome": "answered"}, collections=("activities", "contacts"))
        return {"ok": True, "kind": kind, "activity": act, "recording_url": rec_url}

    raise ApiError(422, "VALIDATION_FAILED", "kind must be whatsapp_inbound or voice_call.")


@route("POST", r"/api/v1/security/egress-allowlist")
def egress_allowlist_add(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    raw_host = ctx.body_str("host", 180, required=True)
    ok, norm = sec.valid_egress_host_pattern(raw_host)
    if not ok:
        raise ApiError(400, "INVALID_EGRESS_HOST", norm)
    settings = dict(ctx.tenant.get("settings") or {})
    cur = [str(x).lower() for x in (settings.get("egress_allowlist") or []) if x]
    if norm not in cur and norm not in sec.EGRESS_ALLOWLIST:
        cur.append(norm)
    settings["egress_allowlist"] = cur[-80:]
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("security.egress_host_added", norm, {})
    return {"ok": True, "host": norm, "egress_allowlist": sorted(sec.tenant_egress_hosts(ctx.tenant))}


@route("DELETE", r"/api/v1/security/egress-allowlist")
def egress_allowlist_remove(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    raw_host = (ctx.body_str("host", 180) or (ctx.query.get("host", [""])[0] or "")).strip().lower()
    settings = dict(ctx.tenant.get("settings") or {})
    cur = [str(x).lower() for x in (settings.get("egress_allowlist") or []) if str(x).lower() != raw_host]
    settings["egress_allowlist"] = cur
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.tenant["settings"] = settings
    ctx.audit("security.egress_host_removed", raw_host, {})
    return {"ok": True, "egress_allowlist": sorted(sec.tenant_egress_hosts(ctx.tenant))}


@route("POST", r"/api/v1/properties/(?P<pid>[\w]+)/documents/scan")
def property_document_scan_upload(ctx: Ctx) -> dict:
    """Magic-byte MIME verification + malware/active-PDF scan for document packs (`PLANS.md` S2)."""
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    doc_type = ctx.body_str("doc_type", 40) or "7_12_extract"
    filename = ctx.body_str("filename", 120) or f"{doc_type}.pdf"
    declared_mime = ctx.body_str("content_type", 80, required=True)
    b64 = str(ctx.body.get("content_b64") or "").strip()
    if not b64:
        raise ApiError(400, "VALIDATION_FAILED", "content_b64 is required.")
    try:
        raw_bytes = base64.b64decode(b64, validate=True)
    except Exception:
        raise ApiError(400, "VALIDATION_FAILED", "content_b64 is not valid base64.")
    try:
        verdict = conn_mod.scan_document_upload(raw_bytes, declared_mime, filename)
    except conn_mod.ConnectorError as exc:
        ctx.audit("document.upload_blocked", prop["id"], {"doc_type": doc_type, "code": exc.code})
        raise ApiError(exc.status, exc.code, exc.message)

    ext = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "audio/wav": "wav",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    }[verdict["verified_mime"]]
    obj_key = f"exports/tenants/{ctx.tid}/docpacks/{prop['id']}_{re.sub(r'[^a-zA-Z0-9_]', '_', doc_type)}.{ext}"
    stored_in = "inline_verified"
    download_url = ""
    if neon.storage_configured():
        try:
            neon.put_object(obj_key, raw_bytes, content_type=verdict["verified_mime"])
            stored_in = "object_storage"
            download_url = neon.presign_get(obj_key, expires=900)
        except Exception:
            stored_in = "inline_verified"

    docs_map = dict(prop.get("documents") or {})
    docs_map[doc_type] = {
        "status": "verified",
        "verified_mime": verdict["verified_mime"],
        "sha256": verdict["sha256"],
        "bytes": verdict["bytes"],
        "stored_in": stored_in,
        "object_key": obj_key if stored_in == "object_storage" else "",
        "sandboxed_preview": verdict.get("sandboxed_preview", ""),
        "scanned_at": now(),
    }
    ctx.store.update("properties", ctx.tid, prop["id"], {"documents": docs_map})
    ctx.audit("document.scanned_and_verified", prop["id"], {"doc_type": doc_type, "mime": verdict["verified_mime"], "stored_in": stored_in})
    return {"ok": True, "scan": verdict, "stored_in": stored_in, "object_key": obj_key if stored_in == "object_storage" else "", "download_url": download_url}


# ==========================================================================
# Sprint S3/S4/S5 — Compliance Launch Pack, Crash-Safe DLQ, SaaS Billing & Cost Drivers
# ==========================================================================
@route("GET", r"/api/v1/compliance/pack")
def compliance_pack_get(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    return comp_mod.compliance_pack(ctx.store, ctx.tenant)


@route("PATCH", r"/api/v1/compliance/officer")
def compliance_officer_patch(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    settings = dict(ctx.tenant.get("settings") or {})
    cur = comp_mod.grievance_officer_of(ctx.tenant)
    officer = {
        "name": ctx.body_str("name", 120) or cur["name"],
        "designation": ctx.body_str("designation", 120) or cur["designation"],
        "email": ctx.body_str("email", 120) or cur["email"],
        "phone": ctx.body_str("phone", 24) or cur["phone"],
        "address": ctx.body_str("address", 240) or cur["address"],
        "sla_days": comp_mod.GRIEVANCE_SLA_DAYS,
        "updated_at": now(),
    }
    settings["grievance_officer"] = officer
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.audit("compliance.officer_updated", ctx.tid, {"email": officer["email"]})
    return {"grievance_officer": officer}


@route("POST", r"/api/v1/compliance/subprocessors")
def compliance_subprocessors_add(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    name = ctx.body_str("name", 120, required=True)
    purpose = ctx.body_str("purpose", 240, required=True)
    category = ctx.body_str("category", 40) or "processor"
    region = ctx.body_str("region", 40) or "ap-south-1"
    dpa_status = ctx.body_str("dpa_status", 40) or "signed"
    if dpa_status not in ("signed", "standard_terms", "pending"):
        raise ApiError(400, "VALIDATION_FAILED", "dpa_status must be signed, standard_terms, or pending.")
    settings = dict(ctx.tenant.get("settings") or {})
    subs = list(settings.get("subprocessors") or [])
    item = {
        "id": f"sp_{hashlib.sha256(f'{name}:{now()}'.encode()).hexdigest()[:10]}",
        "name": name,
        "category": category,
        "purpose": purpose,
        "data_categories": [sec.clean(str(x), 60) for x in (ctx.body.get("data_categories") or ["contact metadata"])][:8],
        "region": region,
        "dpa_status": dpa_status,
        "retention": ctx.body_str("retention", 120) or "Active DPA term + 30 days",
        "added_at": now(),
    }
    subs.append(item)
    settings["subprocessors"] = subs
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.audit("compliance.subprocessor_added", item["id"], {"name": name, "region": region, "dpa_status": dpa_status})
    return {"subprocessor": item, "subprocessors": comp_mod.subprocessors_of(ctx.store.get("tenants", ctx.tid, ctx.tid))}


@route("DELETE", r"/api/v1/compliance/subprocessors/(?P<spid>[\w\-]+)")
def compliance_subprocessors_delete(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    spid = ctx.params["spid"]
    settings = dict(ctx.tenant.get("settings") or {})
    subs = list(settings.get("subprocessors") or [])
    kept = [s for s in subs if s.get("id") != spid]
    if len(kept) == len(subs):
        raise ApiError(404, "NOT_FOUND", "Custom sub-processor entry not found.")
    settings["subprocessors"] = kept
    ctx.store.update("tenants", ctx.tid, ctx.tid, {"settings": settings})
    ctx.audit("compliance.subprocessor_removed", spid, {})
    return {"ok": True}


@route("GET", r"/api/v1/compliance/grievances")
def compliance_grievances_list(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    rows = sorted(ctx.store.all("grievances", ctx.tid), key=lambda r: -int(r.get("created_at") or 0))
    ts = now()
    formatted = [comp_mod.format_grievance(r, ts) for r in rows]
    return {
        "grievances": formatted,
        "officer": comp_mod.grievance_officer_of(ctx.tenant),
        "open_count": sum(1 for g in formatted if g["status"] != "resolved"),
        "overdue_count": sum(1 for g in formatted if g["overdue"]),
    }


@route("POST", r"/api/v1/compliance/grievances")
def compliance_grievances_create(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        g = comp_mod.create_grievance(ctx.store, ctx.tenant, ctx.body, actor=ctx.user.get("email", "operator"))
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("compliance.grievance_logged", g["id"], {"category": g["category"], "sla_due_at": g["sla_due_at"]})
    return {"grievance": g}


@route("PATCH", r"/api/v1/compliance/grievances/(?P<gid>[\w]+)")
def compliance_grievances_patch(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    gid = ctx.params["gid"]
    try:
        g = comp_mod.update_grievance(ctx.store, ctx.tid, gid, ctx.body, actor=ctx.user.get("email", "operator"))
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    if not g:
        raise ApiError(404, "NOT_FOUND", "Grievance ticket not found.")
    ctx.audit("compliance.grievance_updated", gid, {"status": g["status"]})
    return {"grievance": g}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/erase")
def contacts_erase(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    cid = ctx.params["cid"]
    reason = ctx.body_str("reason", 240) or "DPDP §8(5) data-principal erasure request"
    try:
        cert = comp_mod.erase_contact(
            ctx.store,
            ctx.tenant,
            cid,
            actor=ctx.user.get("email", "owner"),
            reason=reason,
        )
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Contact not found in this workspace.")
    except ValueError as exc:
        if str(exc) == "ALREADY_ERASED":
            raise ApiError(409, "ALREADY_ERASED", "This contact has already been erased under DPDP §8(5).")
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("compliance.contact_erased", cid, {
        "certificate_id": cert["id"],
        "items_purged": cert["items_purged"],
    })
    return {"erased": True, "certificate": cert}


@route("GET", r"/api/v1/compliance/erasures")
def compliance_erasures_list(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    rows = sorted(ctx.store.all("erasures", ctx.tid), key=lambda r: -int(r.get("issued_at") or 0))
    return {"erasures": rows}


@route("GET", r"/api/v1/compliance/erasures/(?P<eid>[\w]+)/verify")
def compliance_erasures_verify(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    cert = ctx.store.get("erasures", ctx.tid, ctx.params["eid"])
    if not cert:
        raise ApiError(404, "NOT_FOUND", "Erasure certificate not found.")
    return {"verification": comp_mod.verify_erasure_certificate(cert), "certificate": cert}


@route("GET", r"/api/v1/contacts/(?P<cid>[\w]+)/dsr-export")
def contacts_dsr_export(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    cid = ctx.params["cid"]
    try:
        bundle = comp_mod.build_dsr_bundle(ctx.store, ctx.tenant, cid, can_read_pii=ctx.can("pii:read"))
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    ctx.audit("compliance.dsr_exported", cid, {"schema": bundle["schema"]})
    return {"bundle": bundle}


@route("GET", r"/api/v1/contacts/(?P<cid>[\w]+)/consent-artefact")
def contacts_consent_artefact(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    cid = ctx.params["cid"]
    try:
        artefact = comp_mod.build_consent_artefact(ctx.store, ctx.tenant, cid)
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    return {"artefact": artefact}


@route("POST", r"/api/v1/contacts/(?P<cid>[\w]+)/score-override")
def contacts_score_override(ctx: Ctx) -> dict:
    ctx.require("write")
    cid = ctx.params["cid"]
    if "score" not in ctx.body:
        raise ApiError(400, "VALIDATION_FAILED", "'score' (0..100) is required.")
    reason = ctx.body_str("reason", 300, required=True)
    try:
        res = comp_mod.override_lead_score(
            ctx.store,
            ctx.tid,
            cid,
            new_score=int(ctx.body["score"]),
            reason=reason,
            actor=ctx.user.get("email", "operator"),
        )
    except LookupError:
        raise ApiError(404, "NOT_FOUND", "Contact not found.")
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("score.human_override", cid, res["override"])
    return {"contact": out_contact(res["contact"], ctx), "override": res["override"]}


@route("POST", r"/api/v1/compliance/attestations")
def compliance_attestations_create(ctx: Ctx) -> dict:
    ctx.require("run_redline")
    att = comp_mod.generate_partner_attestation(
        ctx.store, ctx.tenant, ctx, actor=ctx.user.get("email", "owner")
    )
    ctx.audit("compliance.partner_attestation", att["id"], {
        "gate_passed": att["gate_passed"],
        "redline_score": att["redline"]["score"],
        "queue_median_h": att["approval_queue"]["median_age_hours"],
    })
    return {"attestation": att}


@route("GET", r"/api/v1/automations/dead-letters")
def automations_dead_letters(ctx: Ctx) -> dict:
    ctx.require("write")
    rows = autm.dead_letters(ctx.store, ctx.tid)
    return {"dead_letters": rows, "count": len(rows)}


@route("POST", r"/api/v1/automations/dead-letters/(?P<rid>[\w]+)/replay")
def automations_dead_letter_replay(ctx: Ctx) -> dict:
    ctx.require("write")
    rid = ctx.params["rid"]
    try:
        replayed = autm.replay_dead_letter(ctx.store, ctx.tid, rid, actor=ctx.user.get("email", "operator"))
    except autm.AutomationError as exc:
        raise ApiError(409, exc.code, exc.message)
    if not replayed:
        raise ApiError(404, "NOT_FOUND", "Dead-lettered run not found.")
    ctx.audit("automation.dead_letter_replayed", rid, {"step_index": replayed.get("step_index", 0)})
    return {"run": replayed}


@route("POST", r"/api/v1/automations/dead-letters/(?P<rid>[\w]+)/dismiss")
def automations_dead_letter_dismiss(ctx: Ctx) -> dict:
    ctx.require("write")
    rid = ctx.params["rid"]
    reason = ctx.body_str("reason", 160) or "dismissed_by_operator"
    try:
        dismissed = autm.dismiss_dead_letter(
            ctx.store, ctx.tid, rid, reason=reason, actor=ctx.user.get("email", "operator")
        )
    except autm.AutomationError as exc:
        raise ApiError(409, exc.code, exc.message)
    if not dismissed:
        raise ApiError(404, "NOT_FOUND", "Dead-lettered run not found.")
    ctx.audit("automation.dead_letter_dismissed", rid, {"reason": reason})
    return {"run": dismissed}


@route("GET", r"/api/v1/billing/subscription")
def billing_subscription_get(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    period = sec.clean((ctx.query.get("period") or [""])[0], 7) or None
    if period and not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ApiError(400, "VALIDATION_FAILED", "period must look like 2026-11.")
    preview = ut.subscription_preview(ctx.store, ctx.tenant, period)
    saas_invoices = sorted(
        [inv for inv in ctx.store.all("invoices", ctx.tid) if inv.get("kind") == "saas_subscription"],
        key=lambda r: -int(r.get("issued_at") or 0),
    )
    return {
        "subscription": preview,
        "plans_available": list(ut.PLAN_PRICING_PAISE.keys()),
        "invoices": saas_invoices[:24],
    }


@route("POST", r"/api/v1/billing/plan")
def billing_plan_change(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    new_plan = ctx.body_str("plan", 24, required=True).lower()
    if new_plan not in ut.PLAN_PRICING_PAISE:
        raise ApiError(400, "VALIDATION_FAILED", f"plan must be one of {list(ut.PLAN_PRICING_PAISE.keys())}.")
    prev_plan = ctx.tenant.get("plan", ut.DEFAULT_PLAN)
    updated_tenant = ctx.store.update("tenants", ctx.tid, ctx.tid, {"plan": new_plan})
    ctx.audit("billing.plan_changed", ctx.tid, {"from": prev_plan, "to": new_plan})
    return {"plan": new_plan, "previous_plan": prev_plan, "subscription": ut.subscription_preview(ctx.store, updated_tenant or ctx.tenant)}


@route("POST", r"/api/v1/billing/invoices/generate")
def billing_invoices_generate(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    period = ctx.body_str("period", 10) or None
    inv, dup = ut.generate_subscription_invoice(
        ctx.store, ctx.tenant, period, by=ctx.user.get("email", "owner")
    )
    if not dup:
        ctx.audit("billing.subscription_invoiced", inv["id"], {
            "invoice_no": inv["invoice_no"],
            "period": inv["period"],
            "total_paise": inv["total_paise"],
        })
    return {"invoice": inv, "duplicate": dup}


@route("GET", r"/api/v1/billing/cost-drivers")
def billing_cost_drivers_get(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    period = sec.clean((ctx.query.get("period") or [""])[0], 7) or None
    if period and not re.fullmatch(r"\d{4}-\d{2}", period):
        raise ApiError(400, "VALIDATION_FAILED", "period must look like 2026-11.")
    return ut.cost_drivers_report(ctx.store, ctx.tenant, period)


# ==========================================================================
# Phase 2 AI Safety, Dual-LLM Quarantined Intake, Canary Rollback & Observability
# (`PLANS.md` W4, W5, W6, W10, W13 · `docs/41`)
# ==========================================================================
@route("POST", r"/api/v1/agents/intake-document", bucket="ai")
def agents_intake_document(ctx: Ctx) -> dict:
    ctx.require("write")
    raw_text = str(ctx.body.get("text") or "")[:24_000]
    if not raw_text.strip():
        raise ApiError(400, "VALIDATION_FAILED", "'text' is required for quarantined document intake.")
    filename = ctx.body_str("filename", 120) or "intake.txt"
    contact_id = ctx.body_str("contact_id", 40)
    agent_id = ctx.body_str("agent_id", 40)
    agent = ctx.store.get("agents", ctx.tid, agent_id) if agent_id else (ctx.store.all("agents", ctx.tid)[0] if ctx.store.all("agents", ctx.tid) else None)

    # Stage 1: Quarantined Reader (zero tools, zero secrets, untrusted parser)
    facts = aigov.quarantined_reader_extract(raw_text, filename=filename)
    # Stage 2: Privileged Planner (never receives `raw_text`, only `facts`)
    decision = aigov.privileged_planner_decide(
        ctx.store,
        ctx.tenant,
        agent,
        facts,
        contact_id=contact_id,
        actor=ctx.user.get("email", "operator"),
    )
    ctx.audit("ai.dual_llm_intake", filename, {
        "injection_quarantined": facts["injection_quarantined"],
        "rera_id": facts["rera_id"],
        "parked_approvals": len(decision["parked_approvals"]),
    })
    return decision


@route("POST", r"/api/v1/learning/(?P<pid>[\w]+)/shadow-benchmark")
def learning_shadow_eval(ctx: Ctx) -> dict:
    ctx.require("decide_learning")
    p = ctx.store.get("proposals", ctx.tid, ctx.params["pid"])
    if not p:
        raise ApiError(404, "NOT_FOUND", "Proposal not found.")
    res = aigov.run_shadow_eval(ctx.store, ctx.tid, p)
    ctx.audit("learning.shadow_evaluated", p["id"], {
        "pass_rate": res["pass_rate"],
        "safe_to_promote": res["safe_to_promote"],
    })
    return {"proposal_id": p["id"], "shadow_eval": res}


@route("POST", r"/api/v1/learning/(?P<pid>[\w]+)/canary")
def learning_canary_start(ctx: Ctx) -> dict:
    ctx.require("decide_learning")
    p = ctx.store.get("proposals", ctx.tid, ctx.params["pid"])
    if not p:
        raise ApiError(404, "NOT_FOUND", "Proposal not found.")
    if p.get("risk") == "critical" and ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only the workspace owner may launch a critical-risk canary.")
    canary_pct = ctx.body_int("canary_pct", 20, 5, 100)
    window_hours = ctx.body_int("window_hours", 72, 1, 720)
    ack = bool(ctx.body.get("acknowledge_risk"))
    try:
        updated = aigov.start_canary(
            ctx.store,
            ctx.tid,
            p,
            canary_pct=canary_pct,
            window_hours=window_hours,
            actor=ctx.user.get("name") or ctx.user.get("email", "owner"),
            acknowledge_risk=ack,
        )
    except PermissionError:
        raise ApiError(422, "ACKNOWLEDGEMENT_REQUIRED", "High/critical-risk canary requires explicit acknowledge_risk=true.")
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        if str(exc) == "SHADOW_EVAL_REGRESSION":
            raise ApiError(422, "SHADOW_EVAL_REGRESSION", "Shadow evaluation detected guardrail/RERA regressions — promotion refused.")
        raise ApiError(409, "INVALID_STATE", str(exc))
    ctx.audit("learning.canary_started", p["id"], {"canary_pct": canary_pct, "window_hours": window_hours})
    rt.HUB.publish(ctx.tid, "learning.updated", p["id"], {"status": "canary"}, collections=("proposals",))
    return {"proposal": updated}


@route("POST", r"/api/v1/learning/(?P<pid>[\w]+)/record-outcome")
def learning_canary_outcome(ctx: Ctx) -> dict:
    ctx.require("write")
    p = ctx.store.get("proposals", ctx.tid, ctx.params["pid"])
    if not p:
        raise ApiError(404, "NOT_FOUND", "Proposal not found.")
    ok = bool(ctx.body.get("ok", True))
    guardrail_blocked = bool(ctx.body.get("guardrail_blocked", False))
    try:
        res = aigov.record_canary_outcome(
            ctx.store,
            ctx.tid,
            p,
            ok=ok,
            guardrail_blocked=guardrail_blocked,
            actor=ctx.user.get("email", "monitor"),
        )
    except ValueError as exc:
        raise ApiError(409, "INVALID_STATE", str(exc))
    if res.get("auto_rolled_back"):
        ctx.audit("learning.auto_rolled_back", p["id"], {"reason": (res["proposal"] or {}).get("rollback_reason", "")})
    return res


@route("POST", r"/api/v1/learning/(?P<pid>[\w]+)/rollback")
def learning_rollback(ctx: Ctx) -> dict:
    ctx.require("decide_learning")
    p = ctx.store.get("proposals", ctx.tid, ctx.params["pid"])
    if not p:
        raise ApiError(404, "NOT_FOUND", "Proposal not found.")
    reason = ctx.body_str("reason", 240) or "Manual operator rollback to pinned skill snapshot"
    try:
        res = aigov.rollback_promotion(
            ctx.store,
            ctx.tid,
            p,
            reason=reason,
            actor=ctx.user.get("name") or ctx.user.get("email", "operator"),
            automatic=False,
        )
    except ValueError as exc:
        raise ApiError(409, "INVALID_STATE", str(exc))
    ctx.audit("learning.rolled_back", p["id"], {"reason": reason})
    rt.HUB.publish(ctx.tid, "learning.updated", p["id"], {"status": "rolled_back"}, collections=("proposals",))
    return res


@route("POST", r"/api/v1/security/guardrail-benchmark", bucket="redline")
def security_guardrail_eval(ctx: Ctx) -> dict:
    ctx.require("run_redline")
    report = aigov.run_guardrail_eval()
    ctx.audit("security.guardrail_eval", ctx.tid, {
        "recall": report["adversarial_recall"],
        "fp_rate": report["benign_false_positive_rate"],
    })
    return {"eval": report}


@route("POST", r"/api/v1/social/(?P<sid>[\w]+)/publish")
def social_publish_route(ctx: Ctx) -> dict:
    if ctx.role not in ("owner", "admin", "manager"):
        raise ApiError(403, "FORBIDDEN_SCOPE", "Publishing requires a manager or above (brand-safety gate).")
    post = ctx.store.get("social", ctx.tid, ctx.params["sid"])
    if not post:
        raise ApiError(404, "NOT_FOUND", "Social post not found.")
    prop_id = ctx.body_str("property_id", 40)
    try:
        res = aigov.publish_social_post(
            ctx.store,
            ctx.tenant,
            post,
            property_id=prop_id,
            actor=ctx.user.get("email", "manager"),
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        raise ApiError(422, "RERA_PUBLISH_BLOCKED", str(exc))
    ctx.audit("social.published", post["id"], {"delivery_mode": res["delivery_mode"], "property_id": prop_id})
    return res


@route("GET", r"/api/v1/observability/metrics")
def observability_metrics_get(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    snap = obs.REGISTRY.snapshot(tenant_id=ctx.tid)
    fmt = (ctx.query.get("format") or ["json"])[0].lower()
    if fmt == "prometheus":
        snap["prometheus_text"] = obs.REGISTRY.prometheus_text()
    return snap


@route("GET", r"/api/v1/observability/alerts")
def observability_alerts_get(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    return obs.evaluate_alerts(ctx.store, ctx.tenant)


# ==========================================================================
# v1.31.0 — A2A Specialist Delegation & DPDP Registered Consent Manager
# ==========================================================================
@route("GET", r"/api/v1/agents/specialists")
def agents_specialists_list(ctx: Ctx) -> dict:
    return {
        "specialists": [
            {"slug": k, **v} for k, v in aigov.SPECIALIST_CATALOG.items()
        ],
        "max_hop_depth": aigov.MAX_A2A_HOP_DEPTH,
        "forbidden_delegation_scopes": sorted(aigov.FORBIDDEN_DELEGATION_SCOPES),
    }


@route("POST", r"/api/v1/agents/(?P<aid>[\w]+)/delegate", bucket="ai")
def agents_delegate_specialist(ctx: Ctx) -> dict:
    ctx.require("write")
    parent = ctx.store.get("agents", ctx.tid, ctx.params["aid"])
    if not parent:
        raise ApiError(404, "NOT_FOUND", "Parent agent not found.")
    specialist = ctx.body_str("specialist", 40, required=True)
    task = ctx.body_str("task", 2000, required=True)
    target_lang = ctx.body_str("target_lang", 8) or "mr"
    hop_depth = int(ctx.body.get("hop_depth") or 1)
    req_scopes = ctx.body.get("requested_scopes")
    if req_scopes is not None and not isinstance(req_scopes, list):
        raise ApiError(400, "VALIDATION_FAILED", "'requested_scopes' must be a list of scope strings.")
    try:
        res = aigov.delegate_specialist_a2a(
            ctx.store,
            ctx.tenant,
            parent,
            specialist=specialist,
            task=task,
            target_lang=target_lang,
            hop_depth=hop_depth,
            requested_scopes=[str(s) for s in req_scopes[:10]] if isinstance(req_scopes, list) else None,
            trace_id=ctx.headers.get("x-trace-id", ""),
            actor=ctx.user.get("email", "agent"),
        )
    except PermissionError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "A2A_DENIED"
        raise ApiError(403, code, msg)
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "A2A_REJECTED"
        raise ApiError(422, code, msg)
    ctx.audit(
        "agent.a2a_delegated",
        parent["id"],
        {
            "specialist": specialist,
            "hop_depth": res["hop_claims"]["hop"],
            "scopes": res["hop_claims"]["scopes"],
            "mode": res["delivery_mode"],
        },
    )
    return res


@route("GET", r"/api/v1/compliance/consent-manager/status")
def consent_manager_status_get(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    return comp_mod.consent_manager_status(ctx.store, ctx.tenant)


@route("POST", r"/api/v1/compliance/consent-manager/directives")
def consent_manager_directive_post(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    try:
        ack = comp_mod.process_consent_manager_directive(
            ctx.store, ctx.tenant, ctx.body, actor=ctx.user.get("email", "operator")
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("compliance.consent_manager_directive", ack["contact_id"], {
        "directive_id": ack["directive_id"],
        "action": ack["action"],
        "deduplicated": ack["deduplicated"],
    })
    return {"acknowledgement": ack}


@route("POST", r"/api/v1/webhooks/consent-manager", public=True, bucket="public")
def consent_manager_webhook(ctx: Ctx) -> dict:
    sig = ctx.headers.get("x-consent-manager-signature") or ctx.headers.get("x-webhook-signature") or ""
    raw = ctx.raw or json.dumps(ctx.body or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = ctx.body
    if ctx.raw and not payload:
        try:
            payload = json.loads(ctx.raw)
        except Exception:
            raise ApiError(400, "MALFORMED_JSON", "Body is not valid JSON.")
    if not isinstance(payload, dict):
        raise ApiError(400, "VALIDATION_FAILED", "Body must be a JSON object.")

    slug = str(payload.get("tenant_slug") or "").strip()
    tid = str(payload.get("tenant_id") or "").strip()
    tenant = None
    if tid:
        tenant = ctx.store.get("tenants", tid, tid)
    elif slug:
        tenant = next((t for t in ctx.store.db["tenants"] if t.get("slug") == slug), None)
    if not tenant:
        raise ApiError(404, "NOT_FOUND", "Target workspace not found.")

    secret = cfg.get("CONSENT_MANAGER_WEBHOOK_SECRET", "")
    if not secret or cfg.is_placeholder(secret):
        # Check workspace custom connector secret if configured
        cm_conn = next(
            (c for c in conn_mod.list_connectors(tenant) if c.get("enabled") and "consent" in str(c.get("slug", "")).lower()),
            None,
        )
        if cm_conn and cm_conn.get("secret_enc"):
            secret = sec.unseal(cm_conn["secret_enc"], aad=tenant["id"])
    if not comp_mod.verify_consent_manager_signature(raw, sig, secret):
        raise ApiError(401, "INVALID_SIGNATURE", "Consent Manager HMAC signature did not verify.")

    try:
        ack = comp_mod.process_consent_manager_directive(
            ctx.store, tenant, payload, actor="webhook:consent-manager"
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.store.audit(tenant["id"], "consent-manager", "compliance.cm_webhook", ack["contact_id"], {
        "directive_id": ack["directive_id"],
        "action": ack["action"],
    })
    return {"ok": True, "acknowledgement": ack}


# ==========================================================================
# v1.32.0 — Buyer/NRI Portal & FEMA, Inbound Voice Receptionist, Offline PWA & Pack Exchange
# ==========================================================================
@route("POST", r"/api/v1/fema/check")
def fema_eligibility_check(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        res = p3_mod.evaluate_fema_eligibility(
            buyer_residency=ctx.body_str("buyer_residency", 40) or "nri",
            property_category=ctx.body_str("property_category", 40) or "residential",
            acquisition_mode=ctx.body_str("acquisition_mode", 40) or "purchase",
            existing_repatriable_residential_count=int(ctx.body.get("existing_repatriable_residential_count") or 0),
            na_order_verified=bool(ctx.body.get("na_order_verified", True)),
        )
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    return {"fema": res}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/buyer-portal")
def deal_issue_buyer_portal(ctx: Ctx) -> dict:
    ctx.require("write")
    ttl_days = int(ctx.body.get("ttl_days") or 30)
    residency = ctx.body_str("buyer_residency", 40) or "resident_indian"
    try:
        res = p3_mod.issue_buyer_portal_pass(
            ctx.store,
            ctx.tenant,
            ctx.params["did"],
            ttl_days=ttl_days,
            buyer_residency=residency,
            actor=ctx.user.get("email", "agent"),
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("deal.buyer_portal_issued", ctx.params["did"], {"residency": residency, "expires_at": res["expires_at"]})
    return res


@route("DELETE", r"/api/v1/deals/(?P<did>[\w]+)/buyer-portal")
def deal_revoke_buyer_portal(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        res = p3_mod.revoke_buyer_portal_pass(
            ctx.store, ctx.tid, ctx.params["did"], actor=ctx.user.get("email", "agent")
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    ctx.audit("deal.buyer_portal_revoked", ctx.params["did"], {})
    return res


@route("GET", r"/api/v1/public/buyer-portal/(?P<token>[\w\-\.]+)", public=True, bucket="public")
def public_buyer_portal_get(ctx: Ctx) -> dict:
    try:
        view = p3_mod.resolve_buyer_portal_view(ctx.store, ctx.params["token"])
    except PermissionError as exc:
        raise ApiError(401, str(exc), "Invalid, expired, or revoked buyer portal pass.")
    return {"portal": view}


@route("POST", r"/api/v1/voice/inbound", bucket="ai")
def voice_inbound_receptionist(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        res = p3_mod.process_inbound_voice_call(
            ctx.store, ctx.tenant, ctx.body, actor=ctx.user.get("email", "receptionist")
        )
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("voice.inbound_processed", res["contact_id"], {
        "escalated": res["sentiment_trajectory"]["escalated_to_human"],
        "turns": res["diarised_turn_count"],
    })
    return res


@route("POST", r"/api/v1/webhooks/voice-inbound", public=True, bucket="public")
def voice_inbound_webhook(ctx: Ctx) -> dict:
    sig = ctx.headers.get("x-voice-signature") or ctx.headers.get("x-webhook-signature") or ""
    raw = ctx.raw or json.dumps(ctx.body or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload = ctx.body
    if ctx.raw and not payload:
        try:
            payload = json.loads(ctx.raw)
        except Exception:
            raise ApiError(400, "MALFORMED_JSON", "Body is not valid JSON.")
    if not isinstance(payload, dict):
        raise ApiError(400, "VALIDATION_FAILED", "Body must be a JSON object.")
    tid = str(payload.get("tenant_id") or "").strip()
    slug = str(payload.get("tenant_slug") or "").strip()
    tenant = ctx.store.get("tenants", tid, tid) if tid else next((t for t in ctx.store.db["tenants"] if t.get("slug") == slug), None)
    if not tenant:
        raise ApiError(404, "NOT_FOUND", "Target workspace not found.")
    secret = cfg.get("VOICE_WEBHOOK_SECRET", "")
    if not comp_mod.verify_consent_manager_signature(raw, sig, secret):
        raise ApiError(401, "INVALID_SIGNATURE", "Voice inbound HMAC signature did not verify.")
    try:
        res = p3_mod.process_inbound_voice_call(ctx.store, tenant, payload, actor="webhook:voice-inbound")
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.store.audit(tenant["id"], "voice-webhook", "voice.inbound_webhook", res["contact_id"], {
        "escalated": res["sentiment_trajectory"]["escalated_to_human"],
    })
    return {"ok": True, **res}


@route("GET", r"/api/v1/push/subscriptions")
def push_subscriptions_get(ctx: Ctx) -> dict:
    ctx.require("write")
    uid = "" if ctx.role in ("owner", "admin") else ctx.user.get("id", "")
    subs = p3_mod.list_push_subscriptions(ctx.tenant, user_id=uid)
    return {"subscriptions": subs, "count": len(subs)}


@route("POST", r"/api/v1/push/subscribe")
def push_subscribe_post(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        sub = p3_mod.register_push_subscription(ctx.store, ctx.tenant, ctx.user, ctx.body)
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "PUSH_INVALID"
        raise ApiError(422, code, msg)
    ctx.audit("push.subscribed", sub["id"], {"host": sub["host"], "topics": sub["topics"]})
    return {"subscription": sub}


@route("DELETE", r"/api/v1/push/subscriptions/(?P<sid>[\w]+)")
def push_subscribe_delete(ctx: Ctx) -> dict:
    ctx.require("write")
    removed = p3_mod.remove_push_subscription(
        ctx.store,
        ctx.tenant,
        ctx.params["sid"],
        user_id=ctx.user.get("id", ""),
        is_admin=ctx.role in ("owner", "admin"),
    )
    if not removed:
        raise ApiError(404, "NOT_FOUND", "Subscription not found.")
    ctx.audit("push.unsubscribed", ctx.params["sid"], {})
    return {"ok": True}


@route("POST", r"/api/v1/offline/sync")
def offline_mutations_sync(ctx: Ctx) -> dict:
    ctx.require("write")
    muts = ctx.body.get("mutations")
    if not isinstance(muts, list):
        raise ApiError(400, "VALIDATION_FAILED", "'mutations' must be a list.")
    res = p3_mod.sync_offline_mutations(ctx.store, ctx.tenant, ctx.user, muts)
    ctx.audit("offline.synced", ctx.user.get("id", ""), {"applied": res["applied"], "deduplicated": res["deduplicated"]})
    return res


@route("POST", r"/api/v1/exchange/export")
def exchange_pack_export(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    fids = [str(x) for x in (ctx.body.get("funnel_ids") or [])[:10]]
    aids = [str(x) for x in (ctx.body.get("agent_ids") or [])[:10]]
    name = ctx.body_str("name", 100) or "Verified Workspace Pack"
    try:
        pack = p3_mod.export_template_pack(
            ctx.store, ctx.tenant, funnel_ids=fids, agent_ids=aids, pack_name=name, actor=ctx.user.get("email", "owner")
        )
    except ValueError as exc:
        raise ApiError(422, "PACK_GUARDRAIL_FAILED", str(exc))
    ctx.audit("exchange.pack_exported", pack["pack_id"], {"funnels": len(pack["funnels"]), "skills": len(pack["skills"])})
    return {"pack": pack}


@route("POST", r"/api/v1/exchange/import")
def exchange_pack_import(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    pack = ctx.body.get("pack") if isinstance(ctx.body.get("pack"), dict) else ctx.body
    try:
        res = p3_mod.import_template_pack(
            ctx.store, ctx.tenant, pack, actor=ctx.user.get("email", "owner")
        )
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "PACK_IMPORT_REJECTED"
        raise ApiError(422, code, msg)
    ctx.audit("exchange.pack_imported", res["pack_id"], {
        "funnels": len(res["imported_funnels"]),
        "proposals": len(res["staged_skill_proposals"]),
    })
    return res


# ==========================================================================
# v1.34.0 — Phase 4 Platform Horizon & G5/G7 Vertical Polish
# ==========================================================================
@route("GET", r"/api/v1/prescreen/model-card")
def prescreen_model_card_get(ctx: Ctx) -> dict:
    ctx.require("write")
    return {"model_card": p4_mod.ONDEVICE_MODEL_CARD}


@route("POST", r"/api/v1/prescreen/verify")
def prescreen_verify_post(ctx: Ctx) -> dict:
    ctx.require("write")
    text = str(ctx.body.get("text") or "")
    runtime = ctx.body_str("runtime", 40) or "cpu-deterministic-fp32"
    return {"prescreen": p4_mod.prescreen_and_verify_payload(text, client_runtime=runtime)}


@route("GET", r"/api/v1/commerce/mandates")
def commerce_mandates_list(ctx: Ctx) -> dict:
    ctx.require("write")
    items = p4_mod.list_ap2_mandates(ctx.tenant)
    return {"mandates": items, "count": len(items)}


@route("POST", r"/api/v1/commerce/mandates")
def commerce_mandates_create(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        res = p4_mod.issue_ap2_mandate(
            ctx.store, ctx.tenant, ctx.body or {}, actor=ctx.user.get("email", "owner")
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "MANDATE_INVALID"
        raise ApiError(422, code, msg)
    ctx.audit("commerce.mandate_issued", res["mandate"]["id"], {
        "deal_id": res["mandate"]["deal_id"],
        "unit_id": res["mandate"]["unit_id"],
        "cap": res["mandate"]["max_amount_inr"],
    })
    return res


@route("DELETE", r"/api/v1/commerce/mandates/(?P<mid>[\w]+)")
def commerce_mandates_delete(ctx: Ctx) -> dict:
    ctx.require("write")
    if not p4_mod.revoke_ap2_mandate(ctx.store, ctx.tenant, ctx.params["mid"]):
        raise ApiError(404, "NOT_FOUND", "Mandate not found.")
    ctx.audit("commerce.mandate_revoked", ctx.params["mid"], {})
    return {"ok": True}


@route("POST", r"/api/v1/commerce/settle")
def commerce_settle_post(ctx: Ctx) -> dict:
    ctx.require("write")
    try:
        res = p4_mod.settle_ap2_mandate(
            ctx.store, ctx.tenant, ctx.body or {}, actor=ctx.user.get("email", "agent")
        )
    except PermissionError as exc:
        raise ApiError(401, str(exc), "AP2 mandate token is invalid, expired, or revoked.")
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "SETTLEMENT_REJECTED"
        raise ApiError(422, code, msg)
    ctx.audit("commerce.mandate_settled", res["settlement"]["mandate_id"], {
        "action": res["settlement"]["action"],
        "unit_id": res["settlement"]["unit_id"],
        "amount_inr": res["settlement"]["amount_inr"],
    })
    return res


@route("GET", r"/api/v1/regions/profiles")
def regions_profiles_get(ctx: Ctx) -> dict:
    ctx.require("write")
    active = dict(ctx.tenant.get("region_profile") or p4_mod.REGION_PROFILES["IN"])
    return {"active": active, "profiles": p4_mod.REGION_PROFILES, "sg_absd_rates": p4_mod.SG_ABSD_RATES}


@route("PATCH", r"/api/v1/regions/pin")
def regions_pin_patch(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    region_code = ctx.body_str("region_code", 10, required=True)
    try:
        prof = p4_mod.pin_tenant_region(ctx.store, ctx.tenant, region_code)
    except ValueError as exc:
        raise ApiError(422, "UNSUPPORTED_REGION", str(exc))
    ctx.audit("tenant.region_pinned", ctx.tid, {"region": prof["code"], "zone": prof["data_residency_zone"]})
    return {"region_profile": prof}


@route("POST", r"/api/v1/regions/compliance-check")
def regions_compliance_check_post(ctx: Ctx) -> dict:
    ctx.require("write")
    pid = ctx.body_str("property_id", 40)
    prop = ctx.store.get("properties", ctx.tid, pid) if pid else (ctx.body.get("property") or {})
    if pid and not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    buyer_profile = ctx.body_str("buyer_profile", 30)
    target_zone = ctx.body_str("target_export_zone", 30)
    verdict = p4_mod.evaluate_regional_listing_compliance(
        ctx.tenant, prop, buyer_profile=buyer_profile, target_export_zone=target_zone
    )
    return {"compliance": verdict}


@route("GET", r"/api/v1/security/crypto-agility")
def security_crypto_agility_get(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    return p4_mod.audit_crypto_agility(ctx.store, ctx.tid)


@route("POST", r"/api/v1/security/crypto-agility/upgrade")
def security_crypto_agility_upgrade(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    res = p4_mod.upgrade_tenant_archive_to_v2(ctx.store, ctx.tid)
    ctx.audit("security.crypto_upgraded_v2", ctx.tid, {
        "upgraded_contacts": res["upgraded_contacts"],
        "upgraded_fields": res["upgraded_fields"],
    })
    return res


@route("PATCH", r"/api/v1/properties/(?P<pid>[\w]+)/media-order")
def property_media_order_patch(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    pinned = ctx.body_str("pinned_cover_id", 80)
    order_raw = ctx.body.get("photo_order")
    order = [sec.clean(str(x), 80) for x in (order_raw if isinstance(order_raw, list) else [])][:25]
    upd = ctx.store.update("properties", ctx.tid, prop["id"], {
        "pinned_cover_id": pinned,
        "photo_order": order,
    })
    ctx.audit("property.media_reordered", prop["id"], {"pinned_cover_id": pinned, "count": len(order)})
    return {"property_id": prop["id"], "pinned_cover_id": upd.get("pinned_cover_id", ""), "photo_order": upd.get("photo_order", [])}


@route("GET", r"/api/v1/properties/(?P<pid>[\w]+)/floor-plans")
def property_floor_plans_get(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    plans = list(prop.get("floor_plans") or [])
    return {"property_id": prop["id"], "floor_plans": plans, "count": len(plans)}


@route("POST", r"/api/v1/properties/(?P<pid>[\w]+)/floor-plans")
def property_floor_plans_post(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    try:
        fp = p4_mod.add_property_floor_plan(ctx.store, ctx.tid, prop, ctx.body or {})
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0] if ":" in msg else "FLOOR_PLAN_INVALID"
        raise ApiError(422, code, msg)
    ctx.audit("property.floor_plan_added", prop["id"], {"floor_plan_id": fp["id"], "carpet_sqft": fp["carpet_sqft"]})
    return {"floor_plan": fp}


@route("GET", r"/api/v1/demo/proof-tenants")
def demo_proof_tenants_get(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    items = p4_mod.list_disposable_proof_tenants(ctx.store, ctx.tid)
    return {"proof_tenants": items, "count": len(items)}


@route("POST", r"/api/v1/demo/proof-tenants")
def demo_proof_tenants_post(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    label = ctx.body_str("label", 80) or "QA Proof Sandbox"
    ttl_h = ctx.body_int("ttl_hours", 24, 1, 168)
    res = p4_mod.create_disposable_proof_tenant(
        ctx.store, label=label, ttl_hours=ttl_h, created_by_tid=ctx.tid
    )
    ctx.audit("demo.proof_tenant_created", res["proof_tenant"]["id"], {"slug": res["proof_tenant"]["slug"]})
    return res


@route("DELETE", r"/api/v1/demo/proof-tenants/(?P<ptid>[\w]+)")
def demo_proof_tenants_delete(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    try:
        res = p4_mod.destroy_disposable_proof_tenant(ctx.store, ctx.params["ptid"], caller_tid=ctx.tid)
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except PermissionError as exc:
        raise ApiError(403, str(exc), "Only disposable proof workspaces owned by this tenant can be destroyed.")
    ctx.audit("demo.proof_tenant_destroyed", ctx.params["ptid"], {"removed_rows": res["removed_rows"]})
    return res


# ==========================================================================
# v1.35.0 — Complete Government, Revenue, e-Registration, Loan/CLP,
# E-Ferfar Mutation & Marketing Trust Lifecycle (Tracks G-1..G-6)
# ==========================================================================
@route("GET", r"/api/v1/properties/(?P<pid>[\w]+)/revenue-vault")
def property_revenue_vault_get(ctx: Ctx) -> dict:
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    return {
        "property_id": prop["id"],
        "revenue_vault": prop.get("revenue_vault") or {},
        "clearance": gsc_mod.evaluate_revenue_clearance(prop),
    }


@route("PUT", r"/api/v1/properties/(?P<pid>[\w]+)/revenue-vault")
def property_revenue_vault_put(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    try:
        clearance = gsc_mod.upsert_revenue_vault(
            ctx.store, ctx.tid, prop, ctx.body or {}, actor=ctx.user.get("id", "legal")
        )
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit(
        "property.revenue_vault_updated",
        ctx.params["pid"],
        {
            "can_execute_agreement": clearance["can_execute_agreement"],
            "blocker_count": len(clearance["blockers"]),
        },
    )
    return {"property_id": prop["id"], "clearance": clearance, "revenue_vault": clearance["vault"]}


@route("POST", r"/api/v1/govt/cost-sheet")
def govt_cost_sheet_post(ctx: Ctx) -> dict:
    try:
        sheet = gsc_mod.compute_statutory_cost_sheet(ctx.body or {})
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    return {"cost_sheet": sheet}


@route("GET", r"/api/v1/deals/(?P<did>[\w]+)/registration")
def deal_registration_get(ctx: Ctx) -> dict:
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    return {
        "deal_id": deal["id"],
        "stage": deal.get("stage"),
        "govt_registration": deal.get("govt_registration") or {},
        "home_loan": deal.get("home_loan") or {},
        "clp_schedule": deal.get("clp_schedule") or [],
        "post_reg_handover": deal.get("post_reg_handover") or {},
    }


@route("PUT", r"/api/v1/deals/(?P<did>[\w]+)/registration")
def deal_registration_put(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    prop = (
        ctx.store.get("properties", ctx.tid, deal["property_id"])
        if deal.get("property_id")
        else None
    ) or {"id": "", "revenue_vault": {"property_card": {"verified": True}}}
    try:
        reg = gsc_mod.upsert_registration_desk(
            ctx.store, ctx.tid, deal, prop, ctx.body or {}, actor=ctx.user.get("id", "crm-legal")
        )
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0].strip() if ":" in msg else "VALIDATION_FAILED"
        raise ApiError(409 if code == "REVENUE_CLEARANCE_BLOCKED" else 400, code, msg)
    ctx.audit(
        "deal.registration_desk_updated",
        ctx.params["did"],
        {"mode": reg["mode"], "ready_for_execution": reg["ready_for_execution"]},
    )
    return {"deal_id": deal["id"], "govt_registration": reg}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/registration/index-ii")
def deal_registration_index2_post(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    try:
        res = gsc_mod.complete_registration_index2(
            ctx.store, ctx.tid, deal, ctx.body or {}, actor=ctx.user.get("id", "crm-legal")
        )
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0].strip() if ":" in msg else "REGISTRATION_GATE_FAILED"
        raise ApiError(409 if code == "REGISTRATION_NOT_READY" else 400, code, msg)
    ctx.audit(
        "deal.index_ii_completed",
        ctx.params["did"],
        {
            "registered_doc_no": res["govt_registration"].get("registered_doc_no"),
            "index_ii_ref": res["govt_registration"].get("index_ii_ref"),
        },
    )
    return {"deal": ctx.store.get("deals", ctx.tid, deal["id"]), **res}


@route("GET", r"/api/v1/properties/(?P<pid>[\w]+)/bank-apf")
def property_bank_apf_get(ctx: Ctx) -> dict:
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    return {"property_id": prop["id"], "bank_apf": prop.get("bank_apf") or []}


@route("POST", r"/api/v1/properties/(?P<pid>[\w]+)/bank-apf")
def property_bank_apf_post(ctx: Ctx) -> dict:
    ctx.require("write")
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    try:
        apf = gsc_mod.add_project_apf(ctx.store, ctx.tid, prop, ctx.body or {})
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    upd_prop = ctx.store.get("properties", ctx.tid, prop["id"]) or prop
    ctx.audit("property.bank_apf_added", ctx.params["pid"], {"apf_code": apf["apf_code"], "bank_name": apf["bank_name"]})
    return {"property_id": prop["id"], "apf": apf, "bank_apf": upd_prop.get("bank_apf") or []}


@route("PUT", r"/api/v1/deals/(?P<did>[\w]+)/home-loan")
def deal_home_loan_put(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    try:
        loan = gsc_mod.upsert_home_loan(ctx.store, ctx.tid, deal, ctx.body or {})
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("deal.home_loan_updated", ctx.params["did"], {"stage": loan["stage"], "bank_name": loan["bank_name"]})
    return {"deal_id": deal["id"], "home_loan": loan}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/clp/init")
def deal_clp_init_post(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    kind = ctx.body_str("schedule_kind", 40) or "rera_building_7stage"
    milestones = gsc_mod.init_rera_clp_schedule(ctx.store, ctx.tid, deal, schedule_kind=kind)
    ctx.audit("deal.clp_initialized", ctx.params["did"], {"schedule_kind": kind, "stages": len(milestones)})
    return {"deal_id": deal["id"], "schedule_kind": kind, "clp_schedule": milestones}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/clp/demand")
def deal_clp_demand_post(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    mcode = ctx.body_str("milestone_code", 40, required=True)
    cert_ref = ctx.body_str("architect_cert_ref", 80)
    try:
        ms = gsc_mod.raise_clp_milestone_demand(
            ctx.store, ctx.tid, deal, mcode, architect_cert_ref=cert_ref
        )
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = msg.split(":", 1)[0].strip() if ":" in msg else "CLP_VALIDATION_FAILED"
        raise ApiError(409 if code == "ARCHITECT_CERTIFICATE_REQUIRED" else 400, code, msg)
    ctx.audit("deal.clp_demand_raised", ctx.params["did"], {"milestone": ms["code"], "amount_inr": ms["amount_inr"]})
    return {"deal_id": deal["id"], "milestone": ms}


@route("PUT", r"/api/v1/deals/(?P<did>[\w]+)/handover")
def deal_handover_put(ctx: Ctx) -> dict:
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    handover = gsc_mod.upsert_post_reg_handover(ctx.store, ctx.tid, deal, ctx.body or {})
    ctx.audit(
        "deal.post_reg_handover_updated",
        ctx.params["did"],
        {"ferfar_entry_no": handover["ferfar_entry_no"], "completion_pct": handover["completion_pct"]},
    )
    return {"deal_id": deal["id"], "post_reg_handover": handover}


@route("GET", r"/api/v1/properties/(?P<pid>[\w]+)/marketing-trust-pack")
def property_marketing_trust_pack_get(ctx: Ctx) -> dict:
    prop = ctx.store.get("properties", ctx.tid, ctx.params["pid"])
    if not prop:
        raise ApiError(404, "NOT_FOUND", "Property not found.")
    zone = str((ctx.query.get("zone") or ["municipal_corp_tier2"])[0])
    return gsc_mod.compute_marketing_trust_pack(prop, local_body_zone=zone)


# ==========================================================================
# Security console
# ==========================================================================
@route("GET", r"/api/v1/security/posture")
def security_posture(ctx: Ctx) -> dict:
    audit_chain = ctx.store.verify_audit(ctx.tid)
    t = ctx.tenant
    envelope = kms_ctl.status()
    # The score used to be the literal 88 — a number on a security dashboard that
    # nobody computed, which is the same defect as a usage meter with 4820 in it
    # (docs/29). It is now the share of controls that are actually holding.
    controls = redteam.control_matrix(t, ctx)
    # an envelope on disk that this process has not unwrapped does not count: the
    # server would still be running on RE_MASTER_KEY and the score would be a lie
    controls.append({
        "control": "Key management (KMS envelope)",
        "state": "enforced" if envelope["active_in_process"] else "degraded",
        "detail": ("root unwrapped from a key-encryption key at boot; the DEK is never at rest"
                   if envelope["active_in_process"] else
                   "an envelope exists but this process booted before it — restart to use it "
                   "(docs/30 §7)" if envelope["envelope"] else
                   "the root is RE_MASTER_KEY in .env — the process holds the same key that is on "
                   "disk (docs/30 §2)"),
    })
    # Same rule as the envelope: a control is only "holding" if the running
    # process actually has it. In-memory limits are real on one instance and
    # decorative on three.
    shared_now = shared.describe()
    controls.append({
        "control": "Shared rate-limit and session state",
        "state": "enforced" if shared_now["shared"] else "degraded",
        "detail": (f"{shared_now['backend']} — limits and revocations hold across instances"
                   if shared_now["shared"] else
                   "in-process: every limit is multiplied by the number of instances and a "
                   "logout only bites on one of them (docs/31)"),
    })
    holding = ("enforced", "ready")
    score = round(100 * sum(1 for c in controls if c["state"] in holding) / len(controls))
    return {
        "score": score,
        "score_basis": {"controls": len(controls), "holding": sum(1 for c in controls if c["state"] in holding),
                        "why": "share of controls in a holding state — computed, not typed"},
        "audit_chain": audit_chain,
        "controls": controls,
        "envelope": envelope,
        "owasp_llm": redteam.OWASP_LLM,
        "last_redline": next((r for r in reversed(ctx.store.all("runs", ctx.tid)) if r.get("type") == "redline"), None),
        "last_sentinel": next((r for r in reversed(ctx.store.all("runs", ctx.tid)) if r.get("type") == "sentinel"), None),
        "pii_fields_encrypted": ["contacts.phone", "contacts.email", "messages.body", "recordings.transcript"],
        "key_rotation_days": 90,
        "retention": {
            "call_recordings_days": 365,
            "chat_days": 540,
            "audit_days": aud_ret.ARCHIVE_DAYS,
            "audit_pii_days": aud_ret.pii_days_for(ctx.tenant),
        },
    }


@route("POST", r"/api/v1/security/sentinel-runs", min_role="owner")
def security_sentinel_run(ctx: Ctx) -> dict:
    """Record a scrubbed Sentinel run in the hash-chained audit trail (`docs/35`).

    `PLANS.md` docs/14 follow-up #3: a security scan should itself be tamper-evident
    evidence. Refuses any payload that still carries a credential pattern so the
    audit chain never becomes a secret sink.
    """
    serialized = json.dumps(ctx.body or {})
    found_secrets = sec.contains_secret(serialized)
    if found_secrets or re.search(r"re_(?:session|refresh)=[A-Za-z0-9._\-]{12,}", serialized):
        raise ApiError(400, "SECRET_IN_REPORT",
                       "Sentinel report summary contains unredacted credential material.")
    checks_in = ctx.body.get("checks") if isinstance(ctx.body.get("checks"), dict) else {}
    sev_in = ctx.body.get("severity") if isinstance(ctx.body.get("severity"), dict) else {}
    total = max(0, min(10000, int(checks_in.get("total") or 0)))
    passed = max(0, min(total, int(checks_in.get("passed") or 0)))
    failed = max(0, min(total, int(checks_in.get("failed") or max(0, total - passed))))
    severity = {k: max(0, min(10000, int(sev_in.get(k) or 0)))
                for k in ("critical", "high", "medium", "low")}
    modules = [sec.clean(str(m), 40) for m in (ctx.body.get("modules") or [])[:30] if str(m).strip()]
    finding_ids = [sec.clean(str(fid), 40) for fid in (ctx.body.get("finding_ids") or [])[:100] if str(fid).strip()]
    report_sha = sec.clean(str(ctx.body.get("report_sha256") or ""), 64)
    if not re.fullmatch(r"[0-9a-f]{16,64}", report_sha):
        raise ApiError(400, "VALIDATION_FAILED", "report_sha256 must be a lowercase hex digest.")
    requests_made = max(0, min(100000, int(ctx.body.get("requests") or 0)))
    refused = max(0, min(100000, int(ctx.body.get("refused") or 0)))
    redactions = max(0, min(100000, int(ctx.body.get("report_redactions") or 0)))

    summary = {
        "checks": {"total": total, "passed": passed, "failed": failed},
        "severity": severity,
        "finding_ids": finding_ids,
        "modules": modules,
        "requests": requests_made,
        "refused": refused,
        "report_sha256": report_sha,
        "report_redactions": redactions,
    }
    run = ctx.store.insert(
        "runs",
        ctx.tid,
        {
            "type": "sentinel",
            "created_at": now(),
            **summary,
        },
        prefix="sen",
    )
    aud = ctx.store.audit(
        ctx.tid,
        ctx.user.get("email", "sentinel"),
        "security.sentinel_run",
        report_sha[:16],
        {"run_id": run["id"], **summary},
    )
    return {
        "ok": True,
        "run_id": run["id"],
        "audit_id": aud["id"],
        "audit_hash": aud["hash"],
        "report_sha256": report_sha,
    }


@route("GET", r"/api/v1/security/secrets")
def security_secrets(ctx: Ctx) -> dict:
    """Secret inventory: presence, source and a one-way fingerprint — never a value.

    Owner/admin only. The response is passed through the redactor before it
    leaves the process, so even a bug in the report cannot leak key material.
    """
    ctx.require("manage_tenant")
    report = cfg.secrets_report()
    blob = json.dumps(report)
    leaked = [s["name"] for s in report["secrets"] if len(cfg.get(s["name"], "")) >= 12 and cfg.get(s["name"]) in blob]
    if leaked:  # defensive: should be impossible
        ctx.audit("security.secrets_redaction_tripped", "", {"fields": leaked})
        report = json.loads(cfg.redact(blob))
        report["redaction_tripped"] = leaked
    # where the root actually comes from, and whether it is wrapped at rest
    report["master_key"]["source"] = sec.KEY_SOURCE
    report["envelope"] = kms_ctl.status()
    ctx.audit("security.secrets_viewed", "", {"configured": sum(1 for s in report["secrets"] if s["present"])})
    return {"secrets": report}


@route("GET", r"/api/v1/security/envelope")
def security_envelope(ctx: Ctx) -> dict:
    """Where the root key comes from — and whether the answer is good enough.

    Owner/admin. Presence, provider, a KEK *identity* and a probe result; never a
    key, never a KEK, and never a claim stronger than the truth: with the `env`
    provider the KEK is in this process' memory, and the panel says so.
    """
    ctx.require("manage_tenant")
    report = kms_ctl.verify()
    state = kms_ctl.status()
    return {"envelope": state, "verify": report,
            "next_step": ("restart the server — the envelope is on disk but this process is still "
                          "running on RE_MASTER_KEY" if state["pending_restart"] else
                          "point RE_KMS_URL at a KMS and re-run `verify`"
                          if not state["envelope"] else "")}


@route("POST", r"/api/v1/security/envelope/rewrap")
def security_envelope_rewrap(ctx: Ctx) -> dict:
    """Re-wrap the root under the current KEK — a KEK rotation, not a key rotation.

    Owner only, and the tenant slug is not asked for because nothing is destroyed:
    the root does not move, so no ciphertext becomes unreadable. That is exactly why
    this is safe to run as a drill every quarter.
    """
    ctx.require("manage_tenant")
    if ctx.role != "owner":
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner can re-wrap the root key.")
    ctx.require_step_up("key_rewrap", "root")
    confirm = sec.clean(ctx.body.get("confirm", ""), 20).lower()
    if confirm != "rewrap":
        raise ApiError(400, "CONFIRM_REQUIRED", "Send {\"confirm\": \"rewrap\"} to re-wrap the root key.")
    before = kms_ctl.load() or {}
    try:
        new = kms_ctl.rewrap()
    except kms_ctl.EnvelopeError as exc:
        ctx.audit("security.envelope_rewrap_failed", "", {"code": exc.code})
        raise ApiError(409, exc.code, exc.message)
    ctx.audit("security.envelope_rewrapped", "", {"kek_before": before.get("kek_id"),
                                                  "kek_after": new.get("kek_id"),
                                                  "root_unchanged": True})
    return {"ok": True, "kek_id_before": before.get("kek_id", ""), "kek_id_after": new.get("kek_id", ""),
            "rotated_at": new.get("rotated_at", 0), "root_unchanged": True,
            "verify": kms_ctl.verify()}


@route("GET", r"/api/v1/security/services")
def security_services(ctx: Ctx) -> dict:
    """Which Neon services are wired: booleans only, never a URL-with-credentials."""
    ctx.require("manage_tenant")
    return {"services": neon.status()}


@route("GET", r"/api/v1/security/shared-state")
def security_shared_state(ctx: Ctx) -> dict:
    """Where rate limits and logout state live — and whether that is good enough.

    Owner/admin. This is the route that answers "can I run a second instance?",
    and it answers it with the truth: `memory` means every limit is multiplied by
    the number of instances and a logout only bites on one of them (docs/31).
    No connection string, no password — a backend *name* and its properties.
    """
    ctx.require("manage_tenant")
    state = shared.describe()
    limits = sec.LIMITER.describe()
    return {"shared_state": state, "limiter": limits,
            "revocation": {"shared": state["shared"],
                           "backend": state["backend"],
                           "local_entries": len(sec.REVOKED)},
            "next_step": ("" if state["shared"] else
                          "set RE_REDIS_URL (any host) or RE_SHARED_DIR (one host) before adding "
                          "a second instance — until then limits and logouts are per process"),
            "why_it_matters": [
                "a per-process limit is not a limit: N instances multiply every bucket by N",
                "a logout on one instance leaves the token valid on the others",
            ]}


@route("POST", r"/api/v1/security/inspect", bucket="ai")
def security_inspect(ctx: Ctx) -> dict:
    text = ctx.body_str("text", 8000, required=True)
    return {"inspection": sec.inspect_text(text)}


@route("POST", r"/api/v1/security/redline", bucket="redline")
def security_redline(ctx: Ctx) -> dict:
    ctx.require("run_redline")
    report = redteam.internal_suite(ctx.store, ctx.tenant, ctx)
    rec = ctx.store.insert(
        "runs",
        ctx.tid,
        {"type": "redline", "score": report["score"], "passed": report["passed"], "failed": report["failed"], "findings": report["findings"], "duration_ms": report["duration_ms"], "status": "complete"},
        prefix="run",
    )
    ctx.audit("security.redline_run", rec["id"], {"score": report["score"], "failed": report["failed"]})
    return report


# ==========================================================================
# Money: payments in, documents out (PLANS.md G2, docs/24)
#
# Two entry points, one reconciliation path. In production the payments Neon
# Function verifies the provider's signature and writes `re_webhook_events`; this
# app then pulls and reconciles (`POST /payments/reconcile`). For a local demo and
# for CI there is also a direct receiver (`POST /webhooks/payments`) that verifies
# the same signature over the same raw bytes and reconciles immediately — same
# code path, so what the proof exercises is what production runs.
# ==========================================================================
def _payment_secret_ok(raw: bytes, signature: str) -> bool:
    """Constant-time HMAC over the exact bytes received, current or previous secret."""
    provided = (signature or "").strip().lower().replace("sha256=", "")
    if not provided or not raw:
        return False
    for name in ("PAYMENTS_WEBHOOK_SECRET", "PAYMENTS_WEBHOOK_SECRET_PREV"):
        secret = cfg.get(name, "")
        if not secret:
            continue
        expected = sec.hmac.new(secret.encode(), raw, sec.hashlib.sha256).hexdigest()
        if sec.hmac.compare_digest(expected, provided):
            return True
    return False


def _next_invoice_number(store: Store, tid: str, tenant: dict) -> str:
    """The next number in the tenant's one running series, skipping any collision.

    A GST series is *one* running number per financial year across documents — not
    one series per document type. Numbering receipts and invoices independently
    produced two documents both called SLD/2026-27/0002, which an accountant reads
    as a duplicate and a tax officer reads as a problem.
    """
    # `tid` is passed explicitly: a provider webhook has no session, so `ctx.tid`
    # is empty and reading it here silently restarted the series at 0001.
    existing = {i.get("number") for i in store.all("invoices", tid)}
    seq = len(existing) + 1
    while True:
        candidate = money_rules.invoice_number(money_rules.series_prefix(tenant), seq)
        if candidate not in existing:
            return candidate
        seq += 1


def _reconcile_event(ctx: Ctx, payload: dict, *, event_id: str = "", header_tenant: str = "") -> dict:
    """Record the payment, then let a pure verdict decide what it may change."""
    event = money_rules.read_event(payload, header_event_id=event_id)
    if header_tenant and not event["tenant_id"]:
        event["tenant_id"] = header_tenant
    tid = event["tenant_id"] or ctx.tid
    if not tid or (ctx.tid and tid != ctx.tid):
        # A webhook has no session; the tenant comes from the payment's notes.
        return {"outcome": "unattributed", "event": event,
                "reason": "The payment carries no tenant in its order notes."}

    existing = next((p for p in ctx.store.all("payments", tid)
                     if p.get("payment_id") and p.get("payment_id") == event["payment_id"]), None)
    # the expectation, if the app wrote one down (POST /payments/orders)
    pending_order = next((p for p in ctx.store.all("payments", tid)
                          if p.get("order_id") and p.get("order_id") == event.get("order_id")
                          and p.get("status") in ("created", "pending")), None)
    if pending_order and not event.get("deal_id"):
        event["deal_id"] = pending_order.get("deal_id", "")
    if pending_order and not event.get("unit_id"):
        event["unit_id"] = pending_order.get("unit_id", "")
    if pending_order and event.get("kind") == "other":
        event["kind"] = pending_order.get("kind", "other")
    deal = ctx.store.get("deals", tid, event["deal_id"]) if event["deal_id"] else None
    unit = ctx.store.get("units", tid, event["unit_id"]) if event["unit_id"] else None
    check = money_rules.verdict(event, deal=deal, unit=unit, existing_payment=existing,
                                expected_paise=int((pending_order or {}).get("amount_paise") or 0))
    if check["outcome"] != "applied":
        outcome = {"duplicate": "duplicate", "unattributed": "unattributed",
                   "unknown_subject": "unknown_subject", "unit_conflict": "unit_conflict",
                   "amount_mismatch": "amount_mismatch"}[check["outcome"]]
        if outcome == "duplicate":
            return {"outcome": "duplicate", "payment": existing, "reason": check["reason"]}
        # Recorded, flagged, and not applied: the money is real, the claim is not.
        held = ctx.store.insert("payments", tid, {
            "kind": event["kind"], "status": "succeeded", "currency": event["currency"],
            "amount_paise": event["amount_paise"] or 0,
            "amount": (event["amount_paise"] or 0) / 100,
            "provider": "razorpay", "payment_id": event["payment_id"] or "",
            "event_id": event["event_id"], "event_type": event["event_type"],
            "method": event["method"], "deal_id": event["deal_id"], "unit_id": event["unit_id"],
            "contact_id": event["contact_id"], "at": now(), "reconciled_at": 0,
            "outcome": outcome, "note": check["reason"][:200],
        }, prefix="pay")
        ctx.store.audit(tid, "payments-webhook", f"payment.{outcome}", held["id"],
                        {"payment_id": event["payment_id"], "reason": check["reason"]})
        return {"outcome": outcome, "payment": held, "reason": check["reason"]}

    payment = ctx.store.insert("payments", tid, {
        "kind": event["kind"], "status": "succeeded", "currency": event["currency"],
        "amount_paise": event["amount_paise"], "amount": event["amount_paise"] / 100,
        "provider": "razorpay", "payment_id": event["payment_id"],
        "order_id": event.get("order_id", ""),
        "event_id": event["event_id"], "event_type": event["event_type"],
        "method": event["method"], "deal_id": event["deal_id"], "unit_id": event["unit_id"],
        "contact_id": event["contact_id"] or (deal or {}).get("contact_id", ""),
        "at": now(), "reconciled_at": now(), "outcome": "applied", "note": event.get("note", "")[:200],
    }, prefix="pay")

    applied: list[str] = []
    stage_note = ""
    if event["kind"] == "token" and unit and deal:
        if unit.get("status") in ("available", None) or (unit.get("block") or {}).get("deal_id") == deal["id"]:
            patch = ivt.apply_booking(unit, deal_id=deal["id"], contact_id=deal.get("contact_id", ""),
                                      by="payments-webhook", at=now(),
                                      token_amount=event["amount_paise"] / 100)
            if patch:
                ctx.store.update("units", tid, unit["id"], patch)
                applied.append("unit_booked")
        # The stage gate is not for sale: a payment cannot push a deal past papers
        # and visits it does not have. It is recorded and the block is reported.
        visits = ctx.store.all("site_visits", tid)
        try:
            gate = vt.check_transition(deal, "token", "owner", visits)
        except vt.StageError as exc:
            stage_note = f"{exc.code}: {exc.message}"
            ctx.store.audit(tid, "payments-webhook", "deal.stage_blocked_by_money", deal["id"],
                            {"payment_id": event["payment_id"], "blocked_by": exc.code})
        else:
            hist = list(deal.get("stage_history") or [])
            hist.append({"stage": "token", "at": now(), "by": "payments-webhook", "forced": False})
            ctx.store.update("deals", tid, deal["id"], {
                "stage": "token", "probability": vt.PROBABILITY.get("token", deal.get("probability", 20)),
                "stage_history": hist[-25:], "unit_id": unit["id"],
                "token_amount": event["amount_paise"] / 100})
            applied.append("deal_at_token")
        contact_id = deal.get("contact_id", "")
        client = ctx.store.get("contacts", tid, contact_id) or {}
        tenant = ctx.store.get("tenants", tid, tid) or {}
        receipt = money_rules.token_receipt(
            number=_next_invoice_number(ctx.store, tid, tenant),
            issued_at=now(), tenant=tenant, client=client, deal=deal, unit=unit,
            amount_paise=event["amount_paise"], payment_id=event["payment_id"])
        doc = ctx.store.insert("invoices", tid, {**receipt, "payment_id": payment["id"]}, prefix="inv")
        applied.append("receipt_issued")

    if event["kind"] == "commission" and deal:
        # A commission paid against an issued invoice marks it paid (up to its total).
        invoice = next((i for i in ctx.store.all("invoices", tid)
                        if i.get("deal_id") == deal["id"] and i.get("type") == "tax_invoice"
                        and int(i.get("paid_paise") or 0) < int(i.get("total_paise") or 0)), None)
        if invoice:
            paid = min(int(invoice.get("total_paise") or 0),
                       int(invoice.get("paid_paise") or 0) + event["amount_paise"])
            ctx.store.update("invoices", tid, invoice["id"],
                             {"paid_paise": paid,
                              "status": "paid" if paid >= int(invoice.get("total_paise") or 0) else "part_paid"})
            applied.append("invoice_marked_paid")

    if pending_order:
        # The intent keeps its own identity: writing the provider's payment id onto
        # the order row made "how many payments carry this id" answer 2.
        ctx.store.update("payments", tid, pending_order["id"],
                         {"status": "succeeded", "outcome": "applied",
                          "reconciled_at": now(), "settled_by": payment["id"]})
        applied.append("order_settled")

    ctx.store.audit(tid, "payments-webhook", "payment.reconciled", payment["id"],
                    {"payment_id": event["payment_id"], "kind": event["kind"],
                     "amount_paise": event["amount_paise"], "applied": applied,
                     "stage_blocked_by": stage_note})
    return {"outcome": "applied", "payment": payment, "applied": applied,
            "stage_blocked": stage_note, "receipt_issued": "receipt_issued" in applied}


@route("POST", r"/api/v1/webhooks/payments", public=True, bucket="public")
def payments_webhook(ctx: Ctx) -> dict:
    """Provider webhook receiver — signature over the raw bytes, then reconcile."""
    signature = ctx.headers.get("x-razorpay-signature") or ctx.headers.get("x-webhook-signature") or ""
    if not ctx.raw:
        raise ApiError(400, "EMPTY_BODY", "The webhook body is empty.")
    if not _payment_secret_ok(ctx.raw, signature):
        raise ApiError(401, "INVALID_SIGNATURE", "Signature does not verify.")
    try:
        payload = json.loads(ctx.raw)
    except Exception:
        raise ApiError(400, "MALFORMED_JSON", "Body is not valid JSON.")
    if not isinstance(payload, dict):
        raise ApiError(400, "VALIDATION_FAILED", "Body must be a JSON object.")
    result = _reconcile_event(ctx, payload,
                             event_id=ctx.headers.get("x-razorpay-event-id", ""))
    result.update({"ok": result["outcome"] in ("applied", "duplicate"), "provider": "razorpay"})
    return result


@route("POST", r"/api/v1/payments/reconcile")
def payments_reconcile(ctx: Ctx) -> dict:
    """Pull verified events the Neon Function stored and reconcile them.

    This is the production path: the function owns `re_webhook_events`, this route
    applies them to the workspace. Re-running is safe — reconciliation is keyed on
    the provider's payment id.
    """
    ctx.require("approve_cp")
    backend = getattr(ctx.store, "backend", None)
    if backend is None:
        raise ApiError(503, "PIPELINE_UNAVAILABLE",
                       "Verified webhook events live in Postgres; this instance is on the file store.")
    events = backend.webhook_events(ctx.tid, limit=ctx.qint("limit", 50, 200))
    results = []
    for row in events:
        payload = {"event": row.get("event_type"), "payload": {"payment": {"entity": {
            "id": row.get("payment_id"), "amount": row.get("amount"),
            "currency": row.get("currency"), "notes": {"tenant_id": ctx.tid}}}}}
        results.append(_reconcile_event(ctx, payload, event_id=row.get("event_id", "")))
    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {"reconciled": counts, "results": results, "scanned": len(results)}


@route("POST", r"/api/v1/payments/orders")
def payments_order(ctx: Ctx) -> dict:
    """Create a payment intent, calling live Razorpay `/v1/orders` when configured."""
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.body_str("deal_id", 40, required=True))
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    unit_id = ctx.body_str("unit_id", 40)
    unit = ctx.store.get("units", ctx.tid, unit_id) if unit_id else None
    if unit_id and not unit:
        raise ApiError(404, "NOT_FOUND", "Unit not found.")
    kind = (ctx.body_str("kind", 20) or "token").lower()
    amount_paise = int(ctx.body.get("amount_paise") or 0)
    if not amount_paise:
        try:
            amount_paise = int(round(float(ctx.body.get("amount") or 0) * 100))
        except (TypeError, ValueError):
            raise ApiError(400, "VALIDATION_FAILED", "amount must be a number.")
    if not amount_paise and unit:
        amount_paise = int(round(float(unit.get("token_amount") or 0) * 100))
    local_order_id = "order_" + nid("x")[2:]
    try:
        doc = money_rules.order(
            order_id=local_order_id, tenant_id=ctx.tid, deal_id=deal["id"],
            unit_id=unit_id, contact_id=deal.get("contact_id", ""), kind=kind,
            amount_paise=amount_paise, at=now(), note=ctx.body_str("note", 200))
    except money_rules.MoneyError as exc:
        raise ApiError(422, exc.code, exc.message)

    try:
        rzp = channels.create_razorpay_order(
            amount_paise=amount_paise,
            receipt=local_order_id,
            notes=doc["notes"],
            tenant=ctx.tenant,
        )
    except channels.ChannelError as exc:
        raise ApiError(exc.status, exc.code, exc.message)

    if rzp["mode"] == "live":
        doc["order_id"] = rzp["order_id"]
        doc["provider"] = "razorpay"
        doc["provider_mode"] = "live"
        checkout = {
            "order_id": doc["order_id"],
            "key_id": rzp["key_id"],
            "currency": "INR",
            "amount_paise": amount_paise,
            "notes": doc["notes"],
            "provider_call": "live",
        }
    else:
        doc["provider_mode"] = "simulated"
        checkout = {
            "order_id": doc["order_id"],
            "notes": doc["notes"],
            "provider_call": "none — no live Razorpay order is created in this build",
        }

    rec = ctx.store.insert("payments", ctx.tid, doc, prefix="pay")
    ctx.audit("payment.order_created", rec["id"],
              {"order_id": doc["order_id"], "amount_paise": amount_paise, "kind": kind, "mode": rzp["mode"]})
    return {
        "order": rec,
        "checkout": checkout,
        "pay_url": "/api/v1/webhooks/payments  (sign the notes back with PAYMENTS_WEBHOOK_SECRET)",
    }


@route("GET", r"/api/v1/payments")
def payments_list(ctx: Ctx) -> dict:
    rows = sorted(ctx.store.all("payments", ctx.tid), key=lambda p: -int(p.get("at") or 0))
    kind = (ctx.query.get("kind", [""])[0] or "").strip()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    return {"payments": rows, "count": len(rows)}


@route("GET", r"/api/v1/invoices")
def invoices_list(ctx: Ctx) -> dict:
    rows = sorted(ctx.store.all("invoices", ctx.tid), key=lambda i: -int(i.get("issued_at") or 0))
    kind = (ctx.query.get("type", [""])[0] or "").strip()
    rows = [r for r in rows if r.get("type") == kind] if kind else rows
    return {"invoices": rows, "count": len(rows)}


@route("GET", r"/api/v1/invoices/(?P<iid>[\w]+)")
def invoice_detail(ctx: Ctx) -> dict:
    inv = ctx.store.get("invoices", ctx.tid, ctx.params["iid"])
    if not inv:
        raise ApiError(404, "NOT_FOUND", "Invoice not found.")
    return {"invoice": inv}


@route("POST", r"/api/v1/deals/(?P<did>[\w]+)/invoice")
def deal_invoice(ctx: Ctx) -> dict:
    """Issue the brokerage's tax invoice for a deal's commission."""
    ctx.require("write")
    deal = ctx.store.get("deals", ctx.tid, ctx.params["did"])
    if not deal:
        raise ApiError(404, "NOT_FOUND", "Deal not found.")
    # The same commission must not be invoiced twice: two invoices for one deal is
    # an over-billing that a client's accountant finds long after the money moved.
    already = [i for i in ctx.store.all("invoices", ctx.tid)
               if i.get("type") == "tax_invoice" and i.get("deal_id") == deal["id"]
               and i.get("status") in ("issued", "part_paid")]
    if already and not ctx.body.get("force"):
        raise ApiError(409, "INVOICE_ALREADY_ISSUED",
                       f"Deal {deal['id']} already has {already[0]['number']} issued. "
                       "Pass force=true (owner only) to issue another, e.g. a revised invoice.",
                       [{"number": already[0]["number"], "total_paise": already[0].get("total_paise")}])
    if already and ctx.body.get("force") and ctx.role not in ("owner", "admin"):
        raise ApiError(403, "FORBIDDEN_SCOPE", "Only an owner or admin can issue a second invoice for a deal.")
    if not (deal.get("commission") or {}).get("gross"):
        try:
            breakdown = vt.commission_breakdown(
                deal, rate_pct=ctx.body.get("rate_pct"), agent_share_pct=ctx.body.get("agent_share_pct"))
        except vt.StageError as exc:
            raise ApiError(400, exc.code, exc.message)
        ctx.store.update("deals", ctx.tid, deal["id"],
                         {"commission": {**breakdown, "computed_at": now(), "computed_by": ctx.user.get("id", "")}})
        deal = ctx.store.get("deals", ctx.tid, deal["id"]) or deal
    tenant = ctx.tenant
    client = ctx.store.get("contacts", ctx.tid, deal.get("contact_id", "")) or {}
    history = sum(int(i.get("taxable_paise") or 0) for i in ctx.store.all("invoices", ctx.tid)
                  if i.get("type") == "tax_invoice" and i.get("deal_id") == deal["id"])
    try:
        inv = money_rules.commission_invoice(
            number=_next_invoice_number(ctx.store, ctx.tid, tenant),
            issued_at=now(), tenant=tenant, client=client, deal=deal,
            breakdown=deal["commission"], tds_history_paise=history)
    except money_rules.MoneyError as exc:
        raise ApiError(422, exc.code, exc.message, [exc.details])
    doc = ctx.store.insert("invoices", ctx.tid, inv, prefix="inv")
    ctx.audit("invoice.issued", doc["id"], {"number": doc["number"], "deal_id": deal["id"],
                                            "taxable_paise": doc["taxable_paise"],
                                            "gst_paise": doc["gst"]["total_paise"],
                                            "tds_paise": doc["tds"]["withheld_paise"]})
    return {"invoice": doc, "deal": ctx.store.get("deals", ctx.tid, deal["id"])}


@route("GET", r"/api/v1/money/summary")
def money_summary(ctx: Ctx) -> dict:
    return money_rules.summary(ctx.store.all("payments", ctx.tid), ctx.store.all("invoices", ctx.tid))


def _next_prefixed_doc_number(store: Store, tid: str, prefix: str, tenant: dict) -> str:
    """Mint a sequential `RF/<SERIES>/<FY>/<seq>` or `CN/<SERIES>/<FY>/<seq>` number."""
    series = f"{prefix}-{money_rules.series_prefix(tenant)}"
    fy = money_rules.financial_year()
    existing = {i.get("number") for i in store.all("invoices", tid)}
    seq = 1
    while True:
        cand = f"{series}/{fy}/{seq:04d}"
        if cand not in existing:
            return cand
        seq += 1


@route("POST", r"/api/v1/payments/(?P<pid>[\w]+)/refund")
def payment_refund(ctx: Ctx) -> dict:
    """Refund a booking token or payment, mint a refund voucher, and release any booked unit."""
    ctx.require("pii:read")
    pay = ctx.store.get("payments", ctx.tid, ctx.params["pid"])
    if not pay:
        raise ApiError(404, "NOT_FOUND", "Payment not found.")
    remaining = int(pay.get("amount_paise") or 0) - int(pay.get("refunded_paise") or 0)
    raw_amt = ctx.body.get("amount_paise")
    refund_paise = int(raw_amt) if raw_amt is not None else remaining
    reason = ctx.body_str("reason", 200) or "Buyer cancellation / booking release"
    try:
        voucher = money_rules.refund_voucher(
            number=_next_prefixed_doc_number(ctx.store, ctx.tid, "RF", ctx.tenant),
            issued_at=now(),
            payment=pay,
            refund_paise=refund_paise,
            reason=reason,
        )
    except money_rules.MoneyError as exc:
        raise ApiError(422, exc.code, exc.message)

    new_refunded = int(pay.get("refunded_paise") or 0) + refund_paise
    full = new_refunded >= int(pay.get("amount_paise") or 0)
    new_status = "refunded" if full else "partially_refunded"
    updated_pay = ctx.store.update("payments", ctx.tid, pay["id"], {
        "status": new_status,
        "refunded_paise": new_refunded,
        "refunded_at": now(),
        "refund_reason": reason,
    })
    doc = ctx.store.insert("invoices", ctx.tid, voucher, prefix="inv")

    unit_released = False
    if full and pay.get("kind") == "token" and pay.get("unit_id"):
        unit = ctx.store.get("units", ctx.tid, pay["unit_id"])
        holder = ((unit or {}).get("booking") or {}).get("deal_id") or ((unit or {}).get("block") or {}).get("deal_id")
        if unit and (not holder or holder == pay.get("deal_id")):
            ctx.store.update("units", ctx.tid, unit["id"], {
                "status": "available",
                "booking": None,
                "block": None,
            })
            unit_released = True

    ctx.audit("payment.refunded", pay["id"], {
        "refund_paise": refund_paise,
        "voucher": doc["number"],
        "unit_released": unit_released,
    })
    return {"payment": updated_pay, "voucher": doc, "unit_released": unit_released, "mode": "simulated_twin"}


@route("POST", r"/api/v1/invoices/(?P<iid>[\w]+)/credit-note")
def invoice_credit_note(ctx: Ctx) -> dict:
    """Issue a CGST §34 Credit Note against a brokerage `tax_invoice`."""
    ctx.require("pii:read")
    inv = ctx.store.get("invoices", ctx.tid, ctx.params["iid"])
    if not inv:
        raise ApiError(404, "NOT_FOUND", "Invoice not found.")
    already_credited = sum(
        int(i.get("taxable_paise") or 0)
        for i in ctx.store.all("invoices", ctx.tid)
        if i.get("type") == "credit_note" and i.get("original_invoice_id") == inv["id"]
    )
    raw_taxable = ctx.body.get("taxable_paise")
    credit_taxable = int(raw_taxable) if raw_taxable is not None else (int(inv.get("taxable_paise") or 0) - already_credited)
    reason = ctx.body_str("reason", 200) or "Post-supply commission revision"
    try:
        cn = money_rules.credit_note(
            number=_next_prefixed_doc_number(ctx.store, ctx.tid, "CN", ctx.tenant),
            issued_at=now(),
            invoice=inv,
            credit_taxable_paise=credit_taxable,
            reason=reason,
            already_credited_paise=already_credited,
        )
    except money_rules.MoneyError as exc:
        raise ApiError(422, exc.code, exc.message)

    cn_doc = ctx.store.insert("invoices", ctx.tid, cn, prefix="inv")
    new_cred_taxable = already_credited + credit_taxable
    new_cred_total = int(inv.get("credited_total_paise") or 0) + int(cn["total_paise"])
    parent_status = "credited" if new_cred_taxable >= int(inv.get("taxable_paise") or 0) else inv.get("status", "issued")
    updated_inv = ctx.store.update("invoices", ctx.tid, inv["id"], {
        "credited_taxable_paise": new_cred_taxable,
        "credited_total_paise": new_cred_total,
        "status": parent_status,
    })
    ctx.audit("invoice.credit_note_issued", inv["id"], {
        "credit_note": cn_doc["number"],
        "taxable_paise": credit_taxable,
        "total_paise": cn["total_paise"],
    })
    return {"credit_note": cn_doc, "invoice": updated_inv}


@route("POST", r"/api/v1/invoices/(?P<iid>[\w]+)/collect")
def invoice_collect_commission(ctx: Ctx) -> dict:
    """Record commission bank/UTR collection + optional Form 16A TDS reconciliation."""
    ctx.require("write")
    inv = ctx.store.get("invoices", ctx.tid, ctx.params["iid"])
    if not inv:
        raise ApiError(404, "NOT_FOUND", "Invoice not found.")
    amt = int(ctx.body.get("amount_paise") or 0)
    cert = ctx.body_str("tds_certificate_no", 60)
    utr = ctx.body_str("utr", 64)
    method = ctx.body_str("method", 40) or "bank_transfer"
    ts = now()
    try:
        patch = money_rules.apply_collection(
            invoice=inv,
            amount_paise=amt,
            tds_certificate_no=cert,
            utr=utr,
            method=method,
            at=ts,
        )
    except money_rules.MoneyError as exc:
        raise ApiError(422, exc.code, exc.message)

    pay_row = None
    if amt > 0:
        pay_row = ctx.store.insert("payments", ctx.tid, {
            "kind": "commission",
            "status": "succeeded",
            "provider": method,
            "payment_id": utr or ("utr_" + sec.hashlib.sha256(f"{inv['id']}:{ts}:{amt}".encode()).hexdigest()[:10]),
            "invoice_id": inv["id"],
            "deal_id": inv.get("deal_id", ""),
            "amount_paise": amt,
            "amount": amt / 100,
            "currency": "INR",
            "method": method,
            "at": ts,
            "outcome": "applied",
        }, prefix="pay")

    updated_inv = ctx.store.update("invoices", ctx.tid, inv["id"], patch)
    if inv.get("deal_id"):
        deal = ctx.store.get("deals", ctx.tid, inv["deal_id"])
        if deal:
            comm = dict(deal.get("commission") or {})
            comm["collected_paise"] = patch["cash_collected_paise"]
            comm["tds_certificate_no"] = patch["tds_certificate_no"]
            comm["invoice_status"] = patch["status"]
            ctx.store.update("deals", ctx.tid, deal["id"], {"commission": comm})

    ctx.audit("invoice.collected", inv["id"], {
        "amount_paise": amt,
        "tds_certificate_no": cert,
        "status": patch["status"],
    })
    return {"invoice": updated_inv, "payment": pay_row}


@route("GET", r"/api/v1/money/gstr1")
def money_gstr1_export(ctx: Ctx) -> dict:
    ctx.require("pii:read")
    period = (ctx.query.get("period") or [""])[0].strip()
    fmt = (ctx.query.get("format") or ["json"])[0].strip().lower()
    gstr1 = money_rules.gstr1_return(
        tenant=ctx.tenant,
        invoices=ctx.store.all("invoices", ctx.tid),
        period=period,
    )
    if fmt == "csv":
        gstr1["csv"] = money_rules.gstr1_csv(gstr1, safe_cell=sec.csv_safe)
    return {"gstr1": gstr1}


# ==========================================================================
# Demo hygiene (docs/26)
# ==========================================================================
@route("GET", r"/api/v1/demo/hygiene")
def demo_hygiene(ctx: Ctx) -> dict:
    """What on this workspace came from a proof run, and what nobody has claimed.

    Read-only and cheap: the desk's job is to make the mess visible — a demo that
    silently contains thirty proof runs is a demo that gets demoed with them in it.
    """
    ctx.require("manage_tenant")
    by_coll = {coll: ctx.store.all(coll, ctx.tid)
               for coll in pf.PURGEABLE + pf.SIDE_EFFECTS if coll in ctx.store.db}
    summary = pf.summarise(by_coll)
    counts = {coll: len(rows) for coll, rows in by_coll.items() if rows}
    # a legacy preview: what a prefix *would* match, so nobody deletes on a guess
    legacy_counts: dict[str, int] = {}
    dated_counts: dict[str, int] = {}
    try:
        prefixes = pf.normalise_prefixes(ctx.query.get("legacy_prefix", [""])[0])
    except pf.ProofError as e:
        raise ApiError(400, e.code, e.message)
    try:
        since = max(0, int(ctx.query.get("legacy_since", ["0"])[0] or 0))
    except Exception:
        raise ApiError(400, "VALIDATION_FAILED", "legacy_since must be a unix timestamp.")
    if since:
        for coll in pf.DATED_COLLECTIONS:
            hit = len([r for r in (by_coll.get(coll) or []) if pf.looks_like_dated_proof(r, since)])
            if hit:
                dated_counts[coll] = hit
    if prefixes:
        for coll, rows in by_coll.items():
            # only collections a purge may touch: reporting 24 tasks we will never
            # delete would make the preview a promise we do not keep
            if coll not in pf.PURGEABLE:
                continue
            hit = len([r for r in rows if pf.looks_like_legacy_proof(r, prefixes)])
            if hit:
                legacy_counts[coll] = hit
    return {
        "tenant": {"id": ctx.tid, "slug": ctx.tenant.get("slug", ""), "name": ctx.tenant.get("name", "")},
        "rows": counts,
        **summary,
        "purgeable": list(pf.PURGEABLE),
        "never_purged": list(pf.NEVER_PURGE),
        "legacy_prefixes": prefixes,
        "legacy_matches": legacy_counts,
        "legacy_since": since,
        "legacy_dated": dated_counts,
        "note": ("Rows without a proof run are treated as real work. A purge deletes only "
                 "stamped rows, unless an operator names a legacy prefix — a heuristic, and "
                 "reported as one."),
    }


@route("POST", r"/api/v1/demo/proof-rows/purge")
def demo_purge(ctx: Ctx) -> dict:
    """Put the demo back: delete rows a proof wrote.

    Three guards, because this deletes: an owner only; the tenant slug typed out in
    the body (a purge cannot be a mis-click); and only stamped rows — plus, when an
    operator explicitly asks, legacy rows matching a named prefix of at least four
    characters. The audit chain and the tenant row are never touched.
    """
    ctx.require("kill_switch")   # owner only
    slug = ctx.body_str("confirm", 60)
    ctx.require_step_up("demo_purge", slug or ctx.tenant.get("slug", ""))
    if not slug or slug != ctx.tenant.get("slug"):
        raise ApiError(400, "CONFIRM_SLUG",
                       f"Type the workspace slug to confirm: '{ctx.tenant.get('slug', '')}'.")
    try:
        prefixes = pf.normalise_prefixes(ctx.body_str("legacy_prefix", 80))
    except pf.ProofError as e:
        raise ApiError(400, e.code, e.message)
    try:
        since = max(0, int(ctx.body.get("legacy_since") or 0))
    except Exception:
        raise ApiError(400, "VALIDATION_FAILED", "legacy_since must be a unix timestamp.")

    # Pass 1: decide. Nothing is deleted until the whole set is known, because a
    # parent can only be judged against children that are actually staying.
    data = {coll: ctx.store.all(coll, ctx.tid)
            for coll in pf.PURGEABLE + pf.SIDE_EFFECTS if coll in ctx.store.db}
    # A closed month is an invoice. Its ledger rows are not disposable, whatever
    # stamp they carry (docs/29): the frozen figures were already reported to the
    # customer, and a purge must not be able to shrink a bill.
    closed_periods = {s.get("period") for s in ctx.store.all("usage_snapshots", ctx.tid)}
    frozen: dict[str, int] = {}
    doomed: dict[str, set[str]] = {coll: set() for coll in data}
    stamped_of: dict[str, dict[str, str]] = {}
    for coll, rows in data.items():
        if coll not in pf.PURGEABLE:
            continue
        for row in rows:
            if coll in pf.FROZEN_BY_SNAPSHOT and row.get("period") in closed_periods:
                # refused on principle, and reported: a purge that leaves rows behind
                # has to say which, or the next operator reads a clean report and
                # believes a month it never touched
                frozen[coll] = frozen.get(coll, 0) + 1
                continue
            if coll == "users" and row.get("id") == ctx.user.get("id"):
                continue    # never delete the person doing the purge
            if pf.is_proof(row):
                doomed[coll].add(row["id"])
                stamped_of.setdefault(coll, {})[row["id"]] = row["proof_run"]
            elif prefixes and pf.looks_like_legacy_proof(row, prefixes):
                doomed[coll].add(row["id"])
            elif since and coll in pf.DATED_COLLECTIONS and pf.looks_like_dated_proof(row, since):
                doomed[coll].add(row["id"])
    # cascade down: a claim without a claimant, a unit without its property
    changed = True
    while changed:
        changed = False
        for parent, links in pf.CASCADE.items():
            loose = doomed.get(parent, set())
            if not loose:
                continue
            for child, key in links:
                for row in data.get(child, []):
                    if row.get(key) in loose and row["id"] not in doomed.get(child, set()):
                        doomed.setdefault(child, set()).add(row["id"])
                        changed = True
    surviving = {coll: [r for r in rows if r.get("id") not in doomed.get(coll, set())]
                 for coll, rows in data.items()}

    # Pass 2: delete children first, and never orphan a survivor.
    removed: dict[str, int] = {}
    legacy: dict[str, int] = {}
    skipped: dict[str, int] = {}
    runs: set[str] = set()
    for coll in pf.PURGEABLE:
        for row in [r for r in data.get(coll, []) if r.get("id") in doomed.get(coll, set())]:
            held = pf.would_orphan(coll, row["id"], surviving)
            if held:
                skipped[coll] = skipped.get(coll, 0) + 1
                continue
            if not ctx.store.remove(coll, ctx.tid, row["id"]):
                continue
            run = stamped_of.get(coll, {}).get(row["id"])
            if run:
                removed[coll] = removed.get(coll, 0) + 1
                runs.add(run)
            else:
                legacy[coll] = legacy.get(coll, 0) + 1
    ctx.audit("demo.proof_rows_purged", ctx.tid,
              {"proof_rows": removed, "legacy_rows": legacy, "runs": sorted(runs),
               "skipped": skipped, "frozen": frozen, "legacy_prefix": prefixes,
               "legacy_since": since})
    return {"removed": removed, "legacy_removed": legacy, "runs": sorted(runs),
            "total_removed": sum(removed.values()) + sum(legacy.values()),
            "skipped": skipped, "frozen": frozen,
            "legacy_prefixes": prefixes,
            "legacy_since": since,
            "note": ("Stamped rows are proof output by construction. Legacy rows matched a "
                     "prefix you named; check them before trusting the demo again.")}


# ==========================================================================
# Public surfaces (funnel + website) — no auth, rate limited, CSRF-free by design
# ==========================================================================
@route("POST", r"/api/v1/public/funnel/(?P<slug>[\w\-]+)/submit", public=True, bucket="public")
def public_funnel_submit(ctx: Ctx) -> dict:
    slug = ctx.params["slug"]
    funnel = ctx.store.public_funnel_by_slug(slug, published_only=True)
    if not funnel:
        raise ApiError(404, "NOT_FOUND", "Funnel not found.")
    if ctx.body.get("company_website"):  # honeypot
        ctx.store.audit(funnel["tenant_id"], ctx.client_ip, "public.honeypot", slug, {})
        return {"ok": True, "message": "Thanks — we'll be in touch."}
    ftok = str(ctx.body.get("_ftok") or "").strip()
    strict_tok = cfg.get("RE_STRICT_FUNNEL_TOKEN", "").strip().lower() in ("1", "true", "yes")
    if ftok or ctx.body.get("_form_urlencoded") or strict_tok:
        ok_tok, reason_tok = sec.verify_funnel_token(slug, ftok)
        if not ok_tok:
            raise ApiError(403, "INVALID_FORM_TOKEN", f"Public form anti-bot token rejected ({reason_tok}).")
    name = ctx.body_str("name", 80, required=True)
    phone = ctx.body_str("phone", 20, required=True)
    if not sec.valid_phone(phone):
        raise ApiError(400, "VALIDATION_FAILED", "Enter a valid phone number.")
    if not ctx.body.get("consent"):
        raise ApiError(422, "CONSENT_MISSING", "Consent is required to contact you.")
    # DPDP §9 children's data guard: property marketing is restricted to 18+
    raw_age = ctx.body.get("age")
    if ctx.body.get("age_under_18") is True or (raw_age is not None and str(raw_age).strip().isdigit() and int(raw_age) < 18):
        raise ApiError(422, "MINOR_PROCESSING_REFUSED", "DPDP §9: Property enquiries and marketing are restricted to adults aged 18 or above.")
    tid = funnel["tenant_id"]
    if comp_mod.is_suppressed(ctx.store, tid, phone=phone, email=ctx.body_str("email", 254)):
        raise ApiError(422, "SUPPRESSED_BY_ERASURE", "This contact previously exercised DPDP §8(5) erasure and is on the suppression list.")
    existing = sec.find_by_blind_index(ctx.store, "contacts", tid, "phone_hash", phone)
    if existing:
        cid = existing[0]["id"]
    else:
        c = ctx.store.insert(
            "contacts",
            tid,
            {
                "name": name,
                "phone_enc": sec.seal(phone, aad=tid),
                "phone_hash": sec.blind_index(phone, tenant_id=tid),
                "email_enc": sec.seal(ctx.body_str("email", 254), aad=tid),
                "email_hash": sec.blind_index(ctx.body_str("email", 254), tenant_id=tid),
                "city": ctx.body_str("city", 60),
                "source": f"Funnel: {funnel['name']}"[:60],
                "stage": "new",
                "score": 55,
                "requirement": ctx.body_str("requirement", 200) or "funnel enquiry",
                "consent": {
                    "status": "granted",
                    "purpose": "site visit follow-up",
                    "channel": "funnel",
                    "captured_at": now(),
                    "notice_version": "v3.1",
                    "evidence": f"funnel:{slug}:{ctx.client_ip}",
                },
                "tags": ["funnel"],
                "last_activity_at": now(),
                "value": 0,
            },
            prefix="cnt",
        )
        cid = c["id"]
        ctx.store.insert("activities", tid, {"contact_id": cid, "type": "created", "text": f"Funnel submission from {slug}", "actor": "website", "at": now()}, prefix="act")
    ctx.store.update("funnels", tid, funnel["id"], {"submissions": funnel.get("submissions", 0) + 1})
    ctx.store.audit(tid, "public-funnel", "public.lead_captured", cid, {"slug": slug, "ip": ctx.client_ip})
    return {"ok": True, "message": "Thanks — the document pack is on its way on WhatsApp.", "contact_id": cid}


@route("GET", r"/api/v1/public/privacy-notice/(?P<slug>[\w\-]+)", public=True, bucket="public")
def public_privacy_notice(ctx: Ctx) -> dict:
    slug = ctx.params["slug"]
    tenant = next((t for t in ctx.store.db["tenants"] if t.get("slug") == slug), None)
    if not tenant:
        raise ApiError(404, "NOT_FOUND", "Workspace not found.")
    return {"notice": comp_mod.privacy_notice_of(tenant)}


@route("POST", r"/api/v1/public/grievance/(?P<slug>[\w\-]+)", public=True, bucket="public")
def public_grievance_submit(ctx: Ctx) -> dict:
    slug = ctx.params["slug"]
    tenant = next((t for t in ctx.store.db["tenants"] if t.get("slug") == slug), None)
    if not tenant:
        raise ApiError(404, "NOT_FOUND", "Workspace not found.")
    if ctx.body.get("company_website"):  # honeypot
        return {"ok": True, "message": "Grievance recorded."}
    try:
        g = comp_mod.create_grievance(ctx.store, tenant, ctx.body, actor=f"public:{ctx.client_ip}")
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.store.audit(tenant["id"], "public-grievance", "compliance.public_grievance", g["id"], {"sla_due_at": g["sla_due_at"]})
    return {
        "ok": True,
        "ticket_id": g["id"],
        "sla_due_at": g["sla_due_at"],
        "assigned_officer": g["assigned_officer"],
        "assigned_email": g["assigned_email"],
    }


@route("GET", r"/api/v1/security/hardening-status", min_role="admin")
def security_hardening_status(ctx: Ctx) -> dict:
    return hard_mod.hardening_posture(ctx.store, ctx.tid)


@route("POST", r"/api/v1/security/hardening-self-test", min_role="admin")
def security_hardening_self_test(ctx: Ctx) -> dict:
    res = hard_mod.run_hardening_self_test(ctx.store, ctx.tid)
    ctx.audit("security.hardening_self_test", ctx.tid, {"ok": res["ok"], "passed": res["passed"], "total": res["total"]})
    return res


# ==========================================================================
# v1.37.0 — Governed Native MCP Server, OpenAPI 3.1 & OAuth 2.1 PKCE Apps
# ==========================================================================
@route("GET", r"/api/v1/mcp/doctor", min_role="viewer")
def mcp_doctor_route(ctx: Ctx) -> dict:
    return mcp_mod.mcp_doctor(ctx.store, ctx.tenant, ctx.role, oauth_scopes=getattr(ctx, "oauth_scopes", None))


@route("POST", r"/api/v1/mcp", min_role="viewer", bucket="ai")
def mcp_streamable_http_route(ctx: Ctx) -> dict:
    raw_params = ctx.body.get("params") if isinstance(ctx.body.get("params"), dict) else {}
    prof = str((ctx.query.get("profile") or [""])[0] or raw_params.get("profile") or "curated").strip().lower()
    return mcp_mod.handle_mcp_rpc(
        ctx.store,
        ctx.tenant,
        ctx.user,
        ctx.role,
        ctx.body,
        profile=prof,
        oauth_scopes=getattr(ctx, "oauth_scopes", None),
    )


@route("GET", r"/api/v1/openapi\.json", min_role="viewer")
def openapi_spec_route(ctx: Ctx) -> dict:
    return mcp_mod.build_openapi_spec(ROUTES, globals())


@route("GET", r"/api/v1/oauth/apps", min_role="admin")
def oauth_apps_list(ctx: Ctx) -> dict:
    return {"apps": mcp_mod.list_oauth_apps(ctx.tenant), "valid_scopes": sorted(mcp_mod.VALID_OAUTH_SCOPES)}


@route("POST", r"/api/v1/oauth/apps", min_role="admin")
def oauth_apps_register(ctx: Ctx) -> dict:
    ctx.require("manage_tenant")
    try:
        out = mcp_mod.register_oauth_app(ctx.store, ctx.tenant, ctx.body)
    except ValueError as exc:
        raise ApiError(400, "VALIDATION_FAILED", str(exc))
    ctx.audit("oauth.app_registered", out["app"]["client_id"], {"name": out["app"]["name"], "scopes": out["app"]["scopes"]})
    return out


@route("POST", r"/api/v1/oauth/authorize", min_role="viewer")
def oauth_authorize_route(ctx: Ctx) -> dict:
    try:
        out = mcp_mod.create_oauth_authorization_code(ctx.store, ctx.tenant, ctx.user, ctx.body)
    except LookupError as exc:
        raise ApiError(404, "NOT_FOUND", str(exc))
    except ValueError as exc:
        code = str(exc).split(":", 1)[0] if ":" in str(exc) else "VALIDATION_FAILED"
        raise ApiError(400, code, str(exc))
    ctx.audit("oauth.code_issued", str(ctx.body.get("client_id") or ""), {"scopes": out["scopes"]})
    return out


@route("POST", r"/api/v1/oauth/token", public=True, bucket="auth")
def oauth_token_exchange_route(ctx: Ctx) -> dict:
    try:
        return mcp_mod.exchange_oauth_code(ctx.store, ctx.body)
    except PermissionError as exc:
        code = str(exc).split(":", 1)[0] if ":" in str(exc) else "INVALID_GRANT"
        raise ApiError(401, code, str(exc))
    except ValueError as exc:
        code = str(exc).split(":", 1)[0] if ":" in str(exc) else "VALIDATION_FAILED"
        raise ApiError(400, code, str(exc))


@route("POST", r"/api/v1/oauth/revoke", public=True, bucket="auth")
def oauth_token_revoke_route(ctx: Ctx) -> dict:
    revoked = mcp_mod.revoke_oauth_token(str(ctx.body.get("token") or ""))
    return {"ok": True, "revoked": revoked}


# ==========================================================================
# White-Label Agency SaaS, Visual DAG & Growth Suite (v1.38.0 — docs/49)
# ==========================================================================
def _ags_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ags_mod.AgencyGrowthError as exc:
        raise ApiError(exc.status, exc.code, exc.message)


@route("GET", r"/api/v1/agency/whitelabel", min_role="admin")
def agency_whitelabel_get(ctx: Ctx) -> dict:
    return {"ok": True, "white_label": dict((ctx.tenant or {}).get("white_label") or {})}


@route("POST", r"/api/v1/agency/whitelabel", min_role="owner")
def agency_whitelabel_set(ctx: Ctx) -> dict:
    wl = _ags_call(ags_mod.configure_white_label, ctx.store, ctx.tid, ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "agency.whitelabel.update", wl.get("custom_domain") or wl["brand_name"])
    return {"ok": True, "white_label": wl}


@route("POST", r"/api/v1/agency/whitelabel/verify-dns", min_role="owner")
def agency_whitelabel_verify_dns(ctx: Ctx) -> dict:
    recs = ctx.body.get("dns_txt_records") if isinstance(ctx.body.get("dns_txt_records"), list) else []
    wl = _ags_call(ags_mod.verify_white_label_domain, ctx.store, ctx.tid, recs)
    ctx.store.audit(ctx.tid, ctx.user["name"], "agency.whitelabel.domain_verified", wl.get("custom_domain") or "")
    return {"ok": True, "white_label": wl}


@route("GET", r"/api/v1/agency/subaccounts", min_role="owner")
def agency_subaccounts_list(ctx: Ctx) -> dict:
    return {"ok": True, "subaccounts": ags_mod.list_subaccounts(ctx.store, ctx.tid)}


@route("POST", r"/api/v1/agency/subaccounts", min_role="owner")
def agency_subaccounts_create(ctx: Ctx) -> dict:
    out = _ags_call(ags_mod.provision_subaccount, ctx.store, ctx.tid, ctx.user["id"], ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "agency.subaccount.create", out["subaccount"]["tenant_id"])
    return {"ok": True, **out}


@route("POST", r"/api/v1/agency/snapshots/apply", min_role="owner")
def agency_snapshot_apply(ctx: Ctx) -> dict:
    out = _ags_call(ags_mod.apply_snapshot_to_subaccount, ctx.store, ctx.tid, ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "agency.snapshot.apply", out["subaccount_tenant_id"])
    return out


@route("GET", r"/api/v1/agency/rebilling", min_role="owner")
def agency_rebilling_get(ctx: Ctx) -> dict:
    return {"ok": True, **ags_mod.compute_agency_rebilling(ctx.store, ctx.tid)}


@route("POST", r"/api/v1/workflows/dag/validate", min_role="agent")
def workflow_dag_validate(ctx: Ctx) -> dict:
    return _ags_call(ags_mod.validate_and_compile_workflow_dag, ctx.tenant, ctx.body or {})


@route("POST", r"/api/v1/workflows/(?P<wid>[a-zA-Z0-9_-]+)/dag", min_role="admin")
def workflow_dag_save(ctx: Ctx) -> dict:
    wid = ctx.params["wid"]
    wf = ctx.store.get("automations", ctx.tid, wid)
    if not wf:
        raise ApiError(404, "NOT_FOUND", "Workflow automation not found.")
    compiled = _ags_call(ags_mod.validate_and_compile_workflow_dag, ctx.tenant, ctx.body or {})
    updated = ctx.store.update(
        "automations",
        ctx.tid,
        wid,
        {
            "trigger": compiled["trigger"],
            "steps": compiled["compiled_steps"],
            "dag": compiled["dag"],
            "updated_at": now(),
        },
    )
    ctx.store.audit(ctx.tid, ctx.user["name"], "workflow.dag.save", wid, {"nodes": compiled["node_count"], "depth": compiled["max_depth"]})
    return {"ok": True, "workflow": updated, "compiled": compiled}


@route("GET", r"/api/v1/funnels/block-catalog", min_role="viewer")
def funnel_block_catalog_get(ctx: Ctx) -> dict:
    return {"ok": True, "catalog": ags_mod.FUNNEL_BLOCK_CATALOG}


@route("POST", r"/api/v1/funnels/(?P<fid>[a-zA-Z0-9_-]+)/blocks", min_role="agent")
def funnel_blocks_save(ctx: Ctx) -> dict:
    fid = ctx.params["fid"]
    blocks = ctx.body.get("blocks") if isinstance(ctx.body.get("blocks"), list) else []
    out = _ags_call(ags_mod.validate_and_save_funnel_blocks, ctx.store, ctx.tid, fid, blocks)
    ctx.store.audit(ctx.tid, ctx.user["name"], "funnel.blocks.save", fid, {"blocks": out["block_count"]})
    return out


@route("POST", r"/api/v1/calendar/availability", min_role="manager")
def calendar_availability_set(ctx: Ctx) -> dict:
    cal = _ags_call(ags_mod.configure_calendar_availability, ctx.store, ctx.tid, ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "calendar.availability.update", ctx.tid)
    return {"ok": True, "calendar_settings": cal}


@route("GET", r"/api/v1/calendar/slots", min_role="viewer")
def calendar_slots_get(ctx: Ctx) -> dict:
    prop_id = str((ctx.query.get("property_id") or [""])[0]).strip()
    date_str = str((ctx.query.get("date") or [cfg.today_iso() if hasattr(cfg, "today_iso") else "2026-11-18"])[0]).strip()
    return _ags_call(ags_mod.compute_available_slots, ctx.store, ctx.tid, property_id=prop_id, date_str=date_str)


@route("POST", r"/api/v1/calendar/book", min_role="agent")
def calendar_slot_book(ctx: Ctx) -> dict:
    out = _ags_call(ags_mod.book_calendar_slot, ctx.store, ctx.tid, ctx.user["id"], ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "calendar.slot.book", out["site_visit"]["id"])
    return out


@route("POST", r"/api/v1/calendar/ical/import", min_role="agent")
def calendar_ical_import(ctx: Ctx) -> dict:
    ics_txt = str(ctx.body.get("ics_text") or "")
    out = _ags_call(ags_mod.import_ical_busy, ctx.store, ctx.tid, ics_txt)
    ctx.store.audit(ctx.tid, ctx.user["name"], "calendar.ical.import", str(out["imported_busy_events"]))
    return out


@route("GET", r"/api/v1/calendar/ical/export", min_role="viewer")
def calendar_ical_export(ctx: Ctx) -> dict:
    prop_id = str((ctx.query.get("property_id") or [""])[0]).strip()
    ics = ags_mod.export_ical_feed(ctx.store, ctx.tid, property_id=prop_id)
    if str((ctx.query.get("format") or ["json"])[0]).lower() == "ics":
        return {"_plain_text": ics}
    return {"ok": True, "ics": ics}


@route("POST", r"/api/v1/email/deliverability/check", min_role="admin")
def email_deliverability_check(ctx: Ctx) -> dict:
    return {"ok": True, **_ags_call(ags_mod.check_email_deliverability, ctx.store, ctx.tid, ctx.body or {})}


@route("POST", r"/api/v1/reputation/request", min_role="agent")
def reputation_request_send(ctx: Ctx) -> dict:
    out = _ags_call(ags_mod.send_reputation_request, ctx.store, ctx.tid, ctx.user["id"], ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "reputation.request.send", out["request"]["id"])
    return out


@route("POST", r"/api/v1/reputation/respond", min_role="viewer")
def reputation_response_submit(ctx: Ctx) -> dict:
    return _ags_call(ags_mod.submit_reputation_response, ctx.store, ctx.tid, ctx.body or {})


@route("GET", r"/api/v1/reputation/summary", min_role="viewer")
def reputation_summary_get(ctx: Ctx) -> dict:
    return {"ok": True, **ags_mod.reputation_summary(ctx.store, ctx.tid)}


# ==========================================================================
# Stateless W3C OTLP Observability, Enterprise OIDC/SSO & sa11y Gate (v1.39.0)
# ==========================================================================
def _eos_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except eos_mod.EnterprisePlatformError as exc:
        raise ApiError(exc.status, exc.code, exc.message)


@route("GET", r"/api/v1/observability/traces", min_role="admin")
def observability_traces_list(ctx: Ctx) -> dict:
    lim = int((ctx.query.get("limit") or ["50"])[0])
    return {"ok": True, **eos_mod.list_tenant_traces(ctx.tid, limit=lim)}


@route("GET", r"/api/v1/observability/otlp", min_role="admin")
def observability_otlp_export(ctx: Ctx) -> dict:
    lim = int((ctx.query.get("limit") or ["100"])[0])
    return eos_mod.export_tenant_otlp(ctx.tid, limit=lim)


@route("POST", r"/api/v1/observability/flush", min_role="owner")
def observability_otlp_flush(ctx: Ctx) -> dict:
    return _eos_call(eos_mod.flush_otlp_collector, ctx.tenant, ctx.tid)


@route("GET", r"/api/v1/sso/config", min_role="admin")
def enterprise_sso_config_get(ctx: Ctx) -> dict:
    raw = dict((ctx.tenant or {}).get("enterprise_sso") or {})
    raw.pop("client_secret_sealed", None)
    return {"ok": True, "enterprise_sso": raw}


@route("POST", r"/api/v1/sso/config", min_role="owner")
def enterprise_sso_config_set(ctx: Ctx) -> dict:
    pub = _eos_call(eos_mod.configure_enterprise_sso, ctx.store, ctx.tid, ctx.body or {})
    ctx.store.audit(ctx.tid, ctx.user["name"], "sso.config.update", pub.get("issuer") or "")
    return {"ok": True, "enterprise_sso": pub}


@route("POST", r"/api/v1/auth/sso/start", public=True, bucket="auth")
def enterprise_sso_start(ctx: Ctx) -> dict:
    workspace = str(ctx.body.get("workspace") or ctx.body.get("tenant_id") or "").strip()
    redir = str(ctx.body.get("redirect_uri") or "").strip()
    return _eos_call(eos_mod.start_sso_login, ctx.store, workspace, redir)


@route("POST", r"/api/v1/auth/sso/callback", public=True, bucket="auth")
def enterprise_sso_callback(ctx: Ctx) -> dict:
    out = _eos_call(
        eos_mod.complete_sso_callback,
        ctx.store,
        ctx.body or {},
        client_ip=ctx.client_ip,
        user_agent=str(ctx.headers.get("user-agent") or ""),
    )
    _stash_refresh(out.pop("refresh_token", ""))
    return out


@route("GET", r"/api/v1/accessibility/audit", min_role="viewer")
def accessibility_audit_route(ctx: Ctx) -> dict:
    return eos_mod.audit_workspace_accessibility(
        ctx.store,
        ctx.tid,
        public_funnel_renderer=public_funnel_page,
    )


# ==========================================================================
# Dispatch
# ==========================================================================
PUBLIC_PATHS = {"/api/v1/auth/login", "/api/v1/auth/signup", "/api/v1/auth/login/mfa",
                "/api/v1/auth/refresh"}


def resolve(method: str, path: str):
    for m, pattern, name, _ in ROUTES:
        if m != method:
            continue
        match = pattern.match(path)
        if match:
            fn = globals().get(name)
            if not callable(fn):
                for k, v in list(globals().items()):
                    if getattr(v, "_min_role", None) is not None and k == name:
                        fn = v
                        break
            return fn, match.groupdict()
    return None, {}


def resolve_neon_auth_identity(store: Store, token: str) -> tuple[dict, dict, dict] | None:
    """Resolve `(claims, user, session)` from a live Neon Auth token (`docs/38`)."""
    if not token or sec.is_revoked(token):
        return None
    verified = neon.verify_session_token(token)
    if not verified:
        return None
    email = str(verified.get("email") or "").strip().lower()
    if not email:
        return None
    matches = [u for u in store.db["users"] if str(u.get("email") or "").lower() == email]
    if not matches:
        return None
    user = matches[0]
    sid = str(verified.get("session_id") or f"nauth_{verified['id'][:16]}")
    claims = {
        "uid": user["id"],
        "tid": user["tenant_id"],
        "role": user["role"],
        "sid": sid,
        "amr": ["neon_auth"],
    }
    session = {
        "id": sid,
        "tenant_id": user["tenant_id"],
        "user_id": user["id"],
        "source": "neon-auth",
        "neon_user_id": verified["id"],
        "revoked_at": None,
    }
    return claims, user, session


def handle(store: Store, method: str, path: str, query: dict, body: dict, headers: dict, client_ip: str, cookie_token: str | None, raw: bytes = b"") -> tuple[int, dict, dict]:
    """Returns (status, payload, extra_headers) with W3C `traceparent` + OTLP span recording (`W13`, `v1.39.0`)."""
    t0 = time.perf_counter()
    start_ns = time.time_ns()
    tctx = eos_mod.parse_or_create_traceparent((headers or {}).get("traceparent"))
    trace_id = tctx["trace_id"]
    fn, _ = resolve(method, path)
    route_name = getattr(fn, "__name__", "unmatched") if fn else "unmatched"
    store.begin_request_cache()
    try:
        status, payload, extra = _handle_inner(store, method, path, query, body, headers, client_ip, cookie_token, raw)
    finally:
        store.end_request_cache()
    end_ns = time.time_ns()
    dur_ms = (time.perf_counter() - t0) * 1000.0
    extra_out = dict(extra or {})
    tid_resolved = str(extra_out.pop("_tid", "") or "")
    auth_kind = str(extra_out.pop("_auth_kind", "session") or "session")
    has_incoming_tp = bool((headers or {}).get("traceparent"))
    extra_out.setdefault("X-Trace-Id", trace_id if has_incoming_tp else f"trc_{trace_id[:16]}")
    extra_out.setdefault("traceparent", tctx["traceparent"])
    obs.REGISTRY.record_span(
        trace_id=trace_id,
        method=method,
        route_key=route_name,
        status=status,
        duration_ms=dur_ms,
    )
    eos_mod.record_http_span(
        trace_ctx=tctx,
        tenant_id=tid_resolved,
        method=method,
        path=path,
        status_code=status,
        start_ns=start_ns,
        end_ns=end_ns,
        auth_kind=auth_kind,
    )
    return status, payload, extra_out


def _handle_inner(store: Store, method: str, path: str, query: dict, body: dict, headers: dict, client_ip: str, cookie_token: str | None, raw: bytes = b"") -> tuple[int, dict, dict]:
    """Returns (status, payload, extra_headers)."""
    fn, params = resolve(method, path)
    if not fn:
        return 404, {"error": {"code": "NOT_FOUND", "message": "No such endpoint.", "trace_id": nid("trc")}}, {}

    ctx = Ctx(store, method, path, query, body if isinstance(body, dict) else {}, headers, client_ip, raw)
    ctx.params = params
    # a thread is reused between requests: a refresh token stashed by the previous
    # one must not end up in this one's cookies
    _stash_refresh("")
    public = getattr(fn, "_public", False)
    bucket = getattr(fn, "_bucket", None) or ("write" if method in ("POST", "PATCH", "PUT", "DELETE") else "read")

    ok, remaining, retry = sec.LIMITER.check(bucket, client_ip)
    if not ok:
        return 429, {"error": {"code": "RATE_LIMITED", "message": "Too many requests.", "trace_id": nid("trc")}}, {"Retry-After": str(retry)}

    token = None
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif cookie_token:
        token = cookie_token

    ctx.raw_token = token or ""

    if not public:
        mode = neon.auth_mode()
        neon_resolved = None
        oauth_resolved = False
        claims = None
        if (token or "").startswith("re_oa_"):
            oa_claims, oa_user, oa_scopes, oa_err = mcp_mod.resolve_oauth_bearer(store, token or "", method, path)
            if oa_err == "OAUTH_SCOPE_DENIED":
                return 403, {"error": {"code": "OAUTH_SCOPE_DENIED", "message": "OAuth2 token lacks required scope for this endpoint.", "trace_id": nid("trc")}}, {}
            if oa_err:
                return 401, {"error": {"code": oa_err, "message": "Invalid or expired OAuth2 access token.", "trace_id": nid("trc")}}, {}
            if oa_claims and oa_user:
                claims = oa_claims
                ctx.user = oa_user
                ctx.session = {"id": oa_claims["sid"], "tenant_id": oa_claims["tid"], "user_id": oa_user["id"], "source": "oauth2"}
                ctx.oauth_scopes = oa_scopes
                oauth_resolved = True
        if not claims and mode in ("local", "hybrid"):
            claims = sec.verify_token(token or "")
        if not claims and mode in ("hybrid", "neon"):
            neon_resolved = resolve_neon_auth_identity(store, token or "")
            if neon_resolved:
                claims, ctx.user, ctx.session = neon_resolved
        if not claims:
            return 401, {"error": {"code": "UNAUTHENTICATED", "message": "Sign in to continue.", "trace_id": nid("trc")}}, {}
        # A challenge is signed by the same key as a session and names the same
        # user, so it must be refused here explicitly: otherwise a stolen password
        # is a stolen session, and the second factor is decoration (docs/27).
        if claims.get("mfa") == "pending":
            return 401, {"error": {"code": "MFA_CHALLENGE_NOT_A_SESSION",
                                   "message": "That is a two-factor challenge, not a session. "
                                              "Finish the sign-in with a code.", "trace_id": nid("trc")}}, {}
        ctx.claims = claims
        if neon_resolved is None and not oauth_resolved:
            users = [u for u in store.db["users"] if u["id"] == claims.get("uid") and u["tenant_id"] == claims.get("tid")]
            if not users:
                return 401, {"error": {"code": "UNAUTHENTICATED", "message": "Session no longer valid.", "trace_id": nid("trc")}}, {}
            ctx.user = users[0]
        if not ctx.user.get("active", True):
            return 403, {"error": {"code": "ACCOUNT_DISABLED", "message": "Account deactivated.", "trace_id": nid("trc")}}, {}
        sid = claims.get("sid", "")
        if not sid:
            # tokens minted before sessions existed carried no sid; they are not
            # upgraded silently, because "which session is this?" has no answer
            # for them and the answer is what revocation works on (docs/28)
            return 401, {"error": {"code": "SESSION_REQUIRED",
                                   "message": "Sign in again — this token predates revocable sessions.",
                                   "trace_id": nid("trc")}}, {}
        if neon_resolved is None and not oauth_resolved:
            ctx.session = sess.active(store, sid, tenant_id=claims.get("tid", "")) or {}
            if not ctx.session:
                return 401, {"error": {"code": "SESSION_ENDED",
                                       "message": "This session has ended. Sign in again.", "trace_id": nid("trc")}}, {}
            sess.touch(store, ctx.session, ip=ctx.client_ip)
        ctx.tenant = next((t for t in store.db["tenants"] if t["id"] == claims["tid"]), {})
        # a workspace that demands MFA, and a session that has not enrolled one:
        # the session works, the dangerous actions do not (see Ctx.require)
        ctx.mfa_pending = bool((ctx.tenant.get("settings") or {}).get("require_mfa")) \
            and not ctx.user.get("mfa_enrolled") and claims.get("amr") is None
        if ctx.role == "channel_partner" and not (
            path.startswith(CP_ROUTE_PREFIX) or path.startswith("/api/v1/auth/")
        ):
            # The partner portal is a separate surface by construction, not by
            # convention: a partner token cannot read contacts, deals, audit or
            # analytics even if a future route forgets to declare a role.
            return 403, {"error": {"code": "FORBIDDEN_SCOPE", "message": "Partner accounts may only use the partner portal.", "trace_id": nid("trc")}}, {}
        min_role = getattr(fn, "_min_role", None)
        if min_role and ROLE_RANK.get(ctx.role, 0) < ROLE_RANK[min_role]:
            return 403, {"error": {"code": "FORBIDDEN_SCOPE", "message": f"Requires {min_role} role.", "trace_id": nid("trc")}}, {}
        # CSRF for cookie-authenticated state changes
        if method in ("POST", "PATCH", "PUT", "DELETE") and not auth.lower().startswith("bearer "):
            sent = headers.get("x-csrf-token", "")
            if not sent or not sec.hmac.compare_digest(sent, sec.csrf_token(token)):
                return 403, {"error": {"code": "CSRF_FAILED", "message": "Missing or invalid CSRF token.", "trace_id": nid("trc")}}, {}
            origin = headers.get("origin")
            if origin:
                host = headers.get("host", "")
                if host not in origin:
                    return 403, {"error": {"code": "ORIGIN_MISMATCH", "message": "Cross-origin write blocked.", "trace_id": nid("trc")}}, {}

    # A proof stamps its rows as it writes (docs/26). The capture is per thread and
    # reset on every request, so a leaked run id cannot follow a later caller.
    store.begin_capture()
    # Who may declare a run: an authenticated caller, or a *signed* provider
    # delivery. A payment is written by the webhook route, which is public by
    # design — but it verifies an HMAC before it writes anything, so only someone
    # holding the webhook secret can stamp a payment as disposable. An anonymous
    # caller on any other public route cannot.
    declarable = (
        method in ("POST", "PATCH", "PUT", "DELETE")
        # a step-up is a write, but what it writes is a second-factor mark on the
        # caller's own row; it stamps explicitly, inside the handler, and only when
        # a proof run is actually declared (docs/34 §6). Automatic stamping here
        # would let an attacker aim a purge at a credential row in someone's account.
        and path != "/api/v1/auth/step-up"
        and (not public or path.startswith(("/api/v1/webhooks/", "/api/v1/auth/login")))
        # a login is public because the caller has no session yet, and the only row
        # it creates is the caller's own session — so stamping is not a deletion
        # primitive somebody else could aim (docs/28)
    )
    if declarable:
        try:
            ctx.proof_run = pf.parse_run(headers.get(pf.HEADER))
        except pf.ProofError as e:
            store.end_capture()
            return 400, {"error": {"code": e.code, "message": e.message, "trace_id": nid("trc")}}, {}
    try:
        payload = fn(ctx)
        if ctx.proof_run:
            for coll, tid, rid in store.end_capture():
                if coll in pf.PURGEABLE:
                    store.update(coll, tid, rid, {"proof_run": ctx.proof_run})
        else:
            store.end_capture()
        extra: dict = {"_tid": ctx.tid or "", "_auth_kind": "oauth2" if getattr(ctx, "oauth_scopes", None) else ("public" if public else "session")}
        if method in ("POST", "PATCH", "PUT", "DELETE") and ctx.tenant:
            extra.update(ut.warnings(store, ctx.tenant))
        return 200, payload, extra
    except ut.QuotaExceeded as e:
        return 402, {"error": {"code": "QUOTA_EXCEEDED", "message": e.message,
                               "details": [{"meter": e.meter, "used": e.used, "cap": e.cap,
                                            "period": e.period, "resets_at": e.resets_at}],
                               "trace_id": nid("trc")}}, {}
    except ut.PeriodClosed as e:
        return 409, {"error": {"code": "PERIOD_CLOSED", "message": e.message,
                               "details": [{"period": e.period}], "trace_id": nid("trc")}}, {}
    except ApiError as e:
        return e.status, {"error": {"code": e.code, "message": e.message, "details": e.details, "trace_id": nid("trc")}}, {"_tid": ctx.tid or ""}
    except (ValueError, OverflowError) as e:
        return 400, {"error": {"code": "VALIDATION_FAILED", "message": str(e), "trace_id": nid("trc")}}, {}
    except Exception as e:  # never leak internals
        import traceback

        traceback.print_exc()
        return 500, {"error": {"code": "INTERNAL", "message": "Something went wrong. Reference logged.", "trace_id": nid("trc")}}, {}


def public_funnel_page(store: Store, slug: str) -> tuple[int, str]:
    """Server-rendered, escaped, no-JS-required funnel page (works on 2G)."""
    funnel = store.public_funnel_by_slug(slug)
    if not funnel:
        return 404, "<h1>Not found</h1>"
    if not funnel.get("published"):
        return 404, "<h1>This page is not published.</h1>"
    tenant = next((t for t in store.db["tenants"] if t["id"] == funnel["tenant_id"]), {})
    wl = tenant.get("white_label") or {}
    brand_display = str(wl.get("brand_name") or tenant.get("name") or "Realtor Estates")
    raw_hex = str(wl.get("primary_color") or "#0f6b52").strip()
    brand_hex = raw_hex if ags_mod._HEX_COLOR_RE.match(raw_hex) else "#0f6b52"
    blocks_html = ags_mod.render_funnel_blocks_html(funnel)
    sec_rows = "".join(
        f'<section class="card"><h3>{sec.esc(s.get("title"))}</h3><p>{sec.esc(s.get("body"))}</p></section>'
        for s in funnel.get("sections", [])
    )
    trust_badges_html = "<span>7/12 + NA order</span><span>48-hour price hold</span><span>Documents before visit</span>"
    cost_card_html = ""
    if funnel.get("property_id"):
        try:
            prop_row = store.get("properties", funnel["tenant_id"], str(funnel["property_id"]))
            if prop_row:
                pack = gsc_mod.compute_marketing_trust_pack(prop_row)
                if pack.get("verified_badges"):
                    trust_badges_html = "".join(
                        f'<span>✓ {sec.esc(b["label"])}</span>'
                        for b in pack["verified_badges"]
                    )
                cs = pack.get("sample_cost_sheet") or {}
                if cs.get("all_in_buyer_outflow_inr"):
                    cost_card_html = (
                        f'<section class="card"><h3>Transparent All-In Buyer Cost Sheet</h3>'
                        f'<p>Base Agreement: ₹{int(cs["agreement_value_inr"]):,} · '
                        f'Stamp Duty ({cs["effective_stamp_duty_pct"]}%): ₹{int(cs["stamp_duty_inr"]):,} · '
                        f'Registration: ₹{int(cs["registration_fee_inr"]):,} · '
                        f'Total All-In Outflow: <strong>₹{int(cs["all_in_buyer_outflow_inr"]):,}</strong></p></section>'
                    )
        except Exception:
            pass
    return 200, f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sec.esc(funnel['headline'])} — {sec.esc(brand_display)}</title>
<meta name="description" content="{sec.esc(funnel.get('sub'))}">
<style>
:root{{--bg:#f7f8fa;--ink:#14181f;--muted:#5a6472;--brand:{brand_hex};--line:#dde1e7}}
*{{box-sizing:border-box}}body{{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
header{{background:#fff;border-bottom:1px solid var(--line);padding:14px 20px;display:flex;justify-content:space-between;align-items:center}}
.brand{{font-weight:700;letter-spacing:-.02em}}.pill{{font-size:12px;background:#e7f2ee;color:#0f4a38;padding:4px 10px;border-radius:999px}}
main{{max-width:760px;margin:0 auto;padding:24px 20px 64px}}
h1{{font-size:clamp(26px,5vw,40px);line-height:1.15;letter-spacing:-.02em;margin:18px 0 10px}}
.lead{{color:var(--muted);font-size:18px;margin-bottom:22px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin:12px 0}}
.card h3{{margin:0 0 6px;font-size:17px}}.card p{{margin:0;color:var(--muted);font-size:15px}}
form{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px;margin-top:22px}}
label{{display:block;font-size:13px;font-weight:600;margin:12px 0 6px}}
input,textarea{{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;font:inherit}}
button{{width:100%;margin-top:16px;padding:14px;border:0;border-radius:10px;background:var(--brand);color:#fff;font:inherit;font-weight:650;cursor:pointer}}
small{{color:var(--muted);display:block;margin-top:10px;font-size:12px}}
.trust{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}}
.trust span{{font-size:12px;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 10px}}
</style></head><body>
<header><div class="brand">{sec.esc(brand_display)}</div><div class="pill">RERA registered</div></header>
<main>
<h1>{sec.esc(funnel['headline'])}</h1>
<p class="lead">{sec.esc(funnel.get('sub'))}</p>
<div class="trust">{trust_badges_html}</div>
<h2 style="font-size:19px;margin:18px 0 8px">Project Details &amp; Title Verification</h2>
{cost_card_html}
{blocks_html}
{sec_rows}
<form method="post" action="/api/v1/public/funnel/{sec.esc(slug)}/submit" id="f">
<input type="hidden" name="_ftok" value="{sec.esc(sec.mint_funnel_token(slug))}">
<label for="name">Your name</label><input id="name" name="name" required maxlength="80" autocomplete="name">
<label for="phone">WhatsApp number</label><input id="phone" name="phone" required maxlength="20" inputmode="tel" autocomplete="tel">
<label for="city">City</label><input id="city" name="city" maxlength="60">
<label for="requirement">What are you looking for?</label><input id="requirement" name="requirement" maxlength="120">
<label><input type="checkbox" name="consent" value="1" required style="width:auto"> I agree to be contacted by {sec.esc(tenant.get('name'))} about this enquiry. Calls only 09:00–21:00 IST; opt out anytime.</label>
<input type="text" name="company_website" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px" aria-hidden="true">
<button type="submit">{sec.esc(funnel.get('cta'))}</button>
<small>Consent-first (DPDP Act 2023). We never share your number with third parties.</small>
</form>
</main></body></html>"""

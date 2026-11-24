"""Regression tests for every finding in the 2026-11-17 security audit.

`scripts/security_audit.py` is the wide sweep (44 live checks against a running
server). These are the narrow ones that must never silently regress, and they run
inside `npm run build` with no server of their own.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from app import agents, security as sec

ROOT = Path(__file__).resolve().parent.parent
CANARY = "sk-ant-" + "0123456789abcdef"


# --- finding 1: a field called `redacted` must actually be redacted -------------
def test_inspect_text_redacts_credentials():
    out = sec.inspect_text(f"my key is {CANARY}")
    assert CANARY not in json.dumps(out), "inspect_text echoed a credential in `redacted`"
    assert "[REDACTED_KEY]" in out["redacted"]


@pytest.mark.parametrize(
    "value",
    [
        "sk-live-abcdefghijklmnop",
        "sk-ant-0123456789abcdef",
        "nak_live_9f8e7d6c5b4a",
        "nsk_live_abcdef123456",
        "ghp_0123456789abcdefghij0123456789abcd",
        "xoxb-1234567890-abcdefghijkl",
        "AIzaSyD-abcdefghijklmnopqrstuvwx",
        "AKIAIOSFODNN7EXAMPLE",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
    ],
)
def test_redact_secrets_covers_provider_shapes(value):
    assert value not in sec.redact_secrets(f"token={value}"), f"{value} survived redaction"


def test_redact_secrets_masks_a_database_dsn():
    masked = sec.redact_secrets("postgresql://user:s3cr3tpass@db.example.com/neondb")
    assert "s3cr3tpass" not in masked
    assert "postgresql://[REDACTED]@" in masked


# --- finding 2: an agent must not echo a credential back -----------------------
def test_agent_output_never_contains_the_credential_it_was_given():
    agent = {
        "id": "agt_test", "name": "test", "status": "active", "model": "local",
        "tools": [], "scopes": [], "spend_usd": 0, "guardrails": {"spend_cap_usd": 5},
    }
    out = agents.run_agent(agent, f"my openai key is {CANARY}, write a reply", {"contact_id": "cnt_1"})
    assert CANARY not in json.dumps(out), "agent echoed the credential (guardrail.redacted or output)"


def test_agent_trace_never_carries_raw_pii_verbatim():
    agent = {
        "id": "agt_test", "name": "test", "status": "active", "model": "local",
        "tools": [], "scopes": [], "spend_usd": 0, "guardrails": {"spend_cap_usd": 5},
    }
    out = agents.run_agent(agent, "call +919812345678", {"contact_id": "cnt_1"})
    guard = out.get("guardrail") or {}
    assert "+919812345678" not in json.dumps(guard)


# --- finding 3: the local store file must not be world-readable ----------------
def test_store_file_is_created_0600(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_DB", "json")   # force the file backend: that is the code under test
    from app.store import Store

    store = Store(path=str(tmp_path / "store.json"))
    if store.backend is not None:  # Postgres mode writes no file at all
        pytest.skip("Postgres backend active — no JSON file is written")
    store.save()
    mode = (tmp_path / "store.json").stat().st_mode & 0o777
    assert mode == 0o600, f"store.json is {oct(mode)}"


def test_existing_store_file_is_tightened_on_boot(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_DB", "json")
    from app.store import Store

    path = tmp_path / "store.json"
    path.write_text('{"meta": {"version": 1, "created_at": 1}}')
    os.chmod(path, 0o644)

    store = Store(path=str(path))
    if store.backend is not None:
        pytest.skip("Postgres backend active — no JSON file is used")
    assert path.stat().st_mode & 0o777 == 0o600


# --- finding 4: login throttling must be per account and time-limited ----------
def test_rate_limiter_buckets_exist_for_auth():
    limits = sec.LIMITER.limits
    assert "auth" in limits and limits["auth"][0] <= 30, "per-IP auth budget is too generous"
    assert "auth_account" in limits, "no per-account throttle: one host can spray or lock a victim"


def test_limiter_cannot_be_disabled_in_production(monkeypatch):
    from app.security import RateLimiter

    monkeypatch.setenv("RE_LIMITER_OFF", "1")
    monkeypatch.setenv("RE_ENV", "production")
    limiter = RateLimiter({"auth": (1, 60)})
    assert limiter.disabled is False, "RE_LIMITER_OFF must not work in production"
    assert limiter.check("auth", "1.2.3.4")[0] is True
    assert limiter.check("auth", "1.2.3.4")[0] is False, "the limit did not apply"


def test_account_throttle_is_checked_before_the_user_lookup():
    src = (ROOT / "app" / "api.py").read_text()
    login = src[src.index("def auth_login"):]
    login = login[: login.index("@route")] if "@route" in login else login
    throttle_at = login.index("auth_account")
    branch_at = login.index("if len(users) != 1")
    assert throttle_at < branch_at, "throttle is after the unknown-account branch: spraying escapes it"


def test_lockout_expires_instead_of_lasting_forever():
    src = (ROOT / "app" / "api.py").read_text()
    assert "locked_until" in src and "now() + 900" in src, "account lockout must self-expire (DoS vector)"


# --- finding 5: request bodies are capped -------------------------------------
def test_body_cap_is_configured_and_enforced(owner):
    from app import server

    assert server.MAX_BODY <= 2_000_000, "body cap is too large to stop a memory-exhaustion request"
    status, _, _ = owner.call("POST", "/api/v1/security/inspect", {"text": "A" * (server.MAX_BODY + 100)})
    assert status == 413, f"oversized body returned {status}"


# --- finding 6: session cookie hardening --------------------------------------
def test_session_cookie_is_httponly_and_samesite(owner):
    """`owner` logs in fresh, so the Set-Cookie header is observable here."""
    status, _, hdrs = owner.call("GET", "/api/v1/auth/me")
    assert status == 200
    # the flags are set at login; re-verify them from the source of truth
    src = (ROOT / "app" / "server.py").read_text()
    line = next(li for li in src.splitlines() if "SESSION_COOKIE" in li and "HttpOnly" in li)
    assert "HttpOnly" in line and "SameSite=Strict" in line


# --- finding 7: authenticated responses must not be cached --------------------
def test_api_responses_are_no_store(owner):
    status, _, hdrs = owner.call("GET", "/api/v1/contacts?limit=1")
    cc = {k.lower(): v for k, v in hdrs.items()}.get("cache-control", "")
    assert status == 200 and "no-store" in cc.lower(), f"Cache-Control={cc or '(absent)'}"


# --- finding 8: a tampered session token is worthless -------------------------
def test_tampered_session_token_is_rejected(owner):
    token = owner.token
    forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    status, _, _ = owner.call("GET", "/api/v1/contacts", headers={"Authorization": f"Bearer {forged}"})
    assert status in (401, 403), f"forged token accepted ({status})"


# --- finding 9: no secret is reachable from the web tier ---------------------
@pytest.mark.parametrize("path", ["/.env", "/.env.local", "/data/store.json", "/data/keys.json",
                                  "/app/config.py", "/.git/config", "/uploads/env.txt"])
def test_secret_paths_are_not_served(path, owner):
    status, body, _ = owner.call("GET", path)
    assert status in (404, 401, 403), f"{path} → {status}"
    assert "RE_MASTER_KEY" not in json.dumps(body)


# --- finding 10: source-level guard against re-introducing string-built SQL ---
def test_no_string_built_sql_in_the_backend():
    src = (ROOT / "app" / "db.py").read_text()
    risky = re.findall(r'execute\(\s*f["\']|execute\([^)]*\.format\(', src)
    assert not risky, f"string-built SQL: {risky}"


def test_no_clipboard_write_can_carry_a_credential():
    js = "\n".join(p.read_text(errors="ignore") for p in (ROOT / "web" / "js").glob("*.js"))
    writes = re.findall(r"clipboard\.writeText\(([^)]{0,120})\)", js)
    bad = [w for w in writes if re.search(r"token|key|secret|password|session", w, re.I)]
    assert not bad, f"clipboard write with a credential: {bad}"


# ---------------------------------------------------------------------------
# Second pass (sentinel/agent.py) — regressions for SEN-001 … SEN-009
# ---------------------------------------------------------------------------
def test_xff_is_ignored_unless_the_peer_is_a_trusted_proxy(owner):
    """SEN-001: a client-supplied X-Forwarded-For must not move the caller.

    Proven on the live server: the same request with two different forged
    addresses must be treated as the same peer. We cannot read the limiter from
    here, so we assert the decision function and the source contract.
    """
    from app import server as srv

    assert srv._peer_is_trusted("127.0.0.1") is False, "nothing is trusted by default"
    assert srv._peer_is_trusted("10.1.2.3") is False
    assert srv.TRUSTED_PROXIES == [] or all(
        not srv._peer_is_trusted(getattr(n, "network_address", "0.0.0.0")) for n in srv.TRUSTED_PROXIES
    )
    src = (ROOT / "app" / "server.py").read_text()
    fn = src[src.index("def client_ip"):src.index("def _health_payload")]
    assert "TRUSTED_PROXIES" in fn and "_peer_is_trusted" in fn, "client_ip trusts the header again"


def test_trusted_proxy_list_is_honoured_when_configured(monkeypatch):
    import ipaddress

    from app import server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXIES", [ipaddress.ip_network("10.0.0.0/8")])
    assert srv._peer_is_trusted("10.4.5.6") is True
    assert srv._peer_is_trusted("192.168.1.1") is False


def test_healthz_is_liveness_only_without_a_session(owner):
    """SEN-007: the public probe must not map the backend."""
    status, body, _ = owner.call("GET", "/healthz")  # owner's call helper carries a token…
    assert status == 200
    import urllib.request

    from conftest import BASE

    with urllib.request.urlopen(BASE + "/healthz", timeout=5) as r:   # …this one does not
        anon = json.loads(r.read())
    blob = json.dumps(anon)
    for leaked in ("neon.tech", "postgresql://", "PostgreSQL ", "documents", "secrets", "version"):
        assert leaked not in blob, f"/healthz leaked {leaked} to an anonymous caller"
    assert anon["ok"] is True and anon["db"]["backend"] in ("postgres", "json")


def test_server_banner_is_not_disclosed(owner):
    """SEN-006: `Server: RealtorEstates/1.0 Python/3.13` is a targeting hint."""
    status, _, hdrs = owner.call("GET", "/healthz")
    assert status == 200
    lowered = {k.lower() for k in hdrs}
    assert "server" not in lowered, f"banner disclosed: {hdrs.get('Server')}"


def test_access_log_scrubs_tokens_and_query_credentials():
    """SEN-005: a token in a URL must not be written to the access log."""
    import io
    import sys as _sys

    from app import server as srv

    class Fake(srv.Handler):
        def __init__(self):            # no socket, no base-class init on purpose
            self.client_address = ("127.0.0.1", 1234)

    buf = io.StringIO()
    old, _sys.stderr = _sys.stderr, buf
    try:
        Fake().log_message('"%s" %s %s', "GET /api/v1/contacts?token=eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOiJ1c3JfMSJ9.abc123def456ghi HTTP/1.1", 200, "-")
    finally:
        _sys.stderr = old
    out = buf.getvalue()
    assert "eyJhbGciOiJIUzI1NiJ9" not in out, f"token written to the log: {out}"
    assert "redacted" in out


def test_user_directory_and_audit_require_an_admin_seat(viewer):
    """SEN-008: a read-only seat could enumerate colleagues and the audit trail."""
    for path in ("/api/v1/tenant/users", "/api/v1/tenant/audit", "/api/v1/security/secrets"):
        status, body, _ = viewer.call("GET", path)
        assert status == 403, f"{path} answered {status} to a viewer"
        assert "email" not in json.dumps(body).lower()


def test_viewer_sees_masked_phone_numbers(viewer):
    status, body, _ = viewer.call("GET", "/api/v1/contacts?limit=5")
    assert status == 200
    for row in body.get("contacts", []):
        assert not re.search(r"\d{6,}", str(row.get("phone", "")).replace(" ", "")), \
            f"unmasked number for a viewer: {row.get('id')}"


def test_runtime_role_and_privileged_role_are_separate():
    """SEN-003: the web tier must not connect as the DSN that owns the schema."""
    from app import db as dbmod

    src = (ROOT / "app" / "db.py").read_text()
    assert "def admin_dsn" in src and "RE_ADMIN_DATABASE_URL" in src
    assert "StorageUnavailable" in src, "a lost connection must degrade to 503, not a traceback"
    runtime_user = dbmod.dsn().split("//")[1].split(":")[0] if dbmod.dsn() else ""
    admin_user = dbmod.admin_dsn().split("//")[1].split(":")[0] if dbmod.admin_dsn() else ""
    if runtime_user == "re_app":
        assert admin_user != "re_app", "runtime and maintenance roles are the same credential"


def test_tenant_context_is_set_before_every_write():
    """RLS only bites if the app tells Postgres which tenant it is acting for."""
    src = (ROOT / "app" / "db.py").read_text()
    upsert = src[src.index("def upsert_documents"):src.index("def upsert_document(")]
    delete = src[src.index("def delete_document"):src.index("# -- audit")]
    audit = src[src.index("def append_audit"):src.index("def audit_rows")]
    for name, chunk in (("upsert_documents", upsert), ("delete_document", delete), ("append_audit", audit)):
        assert "_set_tenant" in chunk, f"{name} writes without a tenant context (RLS will not filter it)"

"""
Realtor Estates — Stateless W3C `traceparent` + OTLP v1 Observability
(`hyperdx`-compatible), Enterprise OIDC/SSO (Pure-Stdlib RFC 8017 `RS256` JWKS
Verification + PKCE + Nonce + Corporate Domain Binding), and Automated
`sa11y`-Style WCAG 2.2 AA Accessibility Auditor (`v1.39.0`, `docs/50`).
"""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import hmac
from html.parser import HTMLParser
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from . import config as cfg, security as sec, sessions as sess, shared_state as shared
from .store import Store, nid

# W3C Trace Context Level 1: version 00, non-zero 32-hex trace-id, non-zero 16-hex parent-id, 2-hex trace-flags
_TRACEPARENT_RE = re.compile(r"^00-(?!0{32})([0-9a-f]{32})-(?!0{16})([0-9a-f]{16})-([0-9a-f]{2})$")
# ASN.1 DER DigestInfo prefix for SHA-256 (RFC 8017 §9.2 Notes 1)
_DER_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")

_SPAN_LOCK = threading.Lock()
_SPAN_BUFFER: deque[dict[str, Any]] = deque(maxlen=1000)


class EnterprisePlatformError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 1. Stateless W3C `traceparent` & Bounded OTLP v1 Telemetry Exporter
# ---------------------------------------------------------------------------
def parse_or_create_traceparent(raw_header: str | None) -> dict[str, str]:
    """Validate incoming W3C `traceparent` or mint a fresh root trace context."""
    cand = (raw_header or "").strip().lower()
    m = _TRACEPARENT_RE.match(cand)
    span_id = secrets.token_hex(8)
    if m:
        trace_id, parent_span_id, flags = m.group(1), m.group(2), m.group(3)
    else:
        trace_id = secrets.token_hex(16)
        parent_span_id = ""
        flags = "01"
    return {
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "span_id": span_id,
        "flags": flags,
        "traceparent": f"00-{trace_id}-{span_id}-{flags}",
    }


def record_http_span(
    *,
    trace_ctx: dict[str, str],
    tenant_id: str,
    method: str,
    path: str,
    status_code: int,
    start_ns: int,
    end_ns: int,
    auth_kind: str = "session",
) -> dict[str, Any]:
    """Append a secret-scrubbed HTTP span into the bounded 1,000-span ring buffer."""
    dur_ms = round(max(0.0, (end_ns - start_ns) / 1_000_000.0), 3)
    clean_path = sec.redact_secrets(str(path or "")[:200])
    span = {
        "trace_id": trace_ctx["trace_id"],
        "span_id": trace_ctx["span_id"],
        "parent_span_id": trace_ctx["parent_span_id"],
        "traceparent": trace_ctx["traceparent"],
        "tenant_id": str(tenant_id or ""),
        "name": f"{method} {clean_path}",
        "kind": "SERVER",
        "start_time_unix_nano": str(start_ns),
        "end_time_unix_nano": str(end_ns),
        "duration_ms": dur_ms,
        "status_code": int(status_code),
        "otel_status": "ERROR" if status_code >= 500 else "OK",
        "attributes": {
            "http.request.method": str(method)[:16],
            "url.path": clean_path,
            "http.response.status_code": int(status_code),
            "realtor.auth.kind": str(auth_kind)[:24],
        },
    }
    with _SPAN_LOCK:
        _SPAN_BUFFER.append(span)
    return span


def list_tenant_traces(tid: str, *, limit: int = 50) -> dict[str, Any]:
    """Return recent spans strictly isolated to `tid`."""
    cap = max(1, min(250, int(limit)))
    with _SPAN_LOCK:
        rows = [dict(s) for s in reversed(_SPAN_BUFFER) if s.get("tenant_id") == tid][:cap]
    avg_ms = round(sum(r["duration_ms"] for r in rows) / len(rows), 3) if rows else 0.0
    err_n = sum(1 for r in rows if r["status_code"] >= 500)
    return {
        "tenant_id": tid,
        "span_count": len(rows),
        "avg_duration_ms": avg_ms,
        "server_error_spans": err_n,
        "spans": rows,
    }


def export_tenant_otlp(tid: str, *, limit: int = 100) -> dict[str, Any]:
    """Build an OpenTelemetry `ExportTraceServiceRequest` v1 JSON payload for `tid`."""
    with _SPAN_LOCK:
        spans = [dict(s) for s in list(_SPAN_BUFFER) if s.get("tenant_id") == tid][-max(1, min(500, limit)):]
    otlp_spans = []
    for s in spans:
        attrs = [
            {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": sec.redact_secrets(str(v))}}
            for k, v in s["attributes"].items()
        ]
        otlp_spans.append({
            "traceId": s["trace_id"],
            "spanId": s["span_id"],
            "parentSpanId": s["parent_span_id"],
            "name": s["name"],
            "kind": 2,  # SPAN_KIND_SERVER
            "startTimeUnixNano": s["start_time_unix_nano"],
            "endTimeUnixNano": s["end_time_unix_nano"],
            "attributes": attrs,
            "status": {"code": 2 if s["status_code"] >= 500 else 1},
        })
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "realtor-estates"}},
                        {"key": "service.version", "value": {"stringValue": "1.39.0"}},
                        {"key": "realtor.tenant.id", "value": {"stringValue": tid}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "realtor.http.tracer", "version": "1.39.0"},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


def flush_otlp_collector(tenant: dict[str, Any] | None, tid: str) -> dict[str, Any]:
    payload = export_tenant_otlp(tid, limit=200)
    span_count = len(payload["resourceSpans"][0]["scopeSpans"][0]["spans"])
    endpoint = (cfg.get("OTEL_EXPORTER_OTLP_ENDPOINT", "") or "").strip()
    if not endpoint:
        return {"ok": True, "mode": "local-ring-buffer", "exported_spans": span_count, "otlp": payload}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with sec.safe_urlopen(req, timeout=6.0, extra_hosts=sec.tenant_egress_hosts(tenant)) as resp:
            return {"ok": True, "mode": "live-otlp", "collector_status": resp.status, "exported_spans": span_count}
    except Exception as exc:
        raise EnterprisePlatformError(502, "OTLP_EXPORT_FAILED", f"OTLP collector unreachable or blocked: {cfg.redact(str(exc))[:140]}")


# ---------------------------------------------------------------------------
# 2. Enterprise OIDC / Corporate SSO (RFC 8017 RS256 JWKS + PKCE + Nonce)
# ---------------------------------------------------------------------------
def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def verify_rs256_jwt(
    id_token: str,
    jwks: dict[str, Any],
    *,
    expected_issuer: str,
    expected_audience: str,
    expected_nonce: str,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Cryptographically verify an OIDC `RS256` JWT against `jwks` (RFC 8017 §8.2.2).

    Defends against:
      - `alg: none` and `HS256` RSA-public-key algorithm confusion
      - Weak `< 2048-bit` RSA keys or small-exponent Bleichenbacher padding tricks
      - Cross-IdP issuer/audience spoofing, expired/not-yet-valid tokens, and nonce replay
    """
    parts = (id_token or "").strip().split(".")
    if len(parts) != 3:
        raise EnterprisePlatformError(401, "SSO_MALFORMED_JWT", "ID token must be a 3-part JWS.")
    try:
        header = json.loads(_b64u_decode(parts[0]).decode("utf-8"))
        claims = json.loads(_b64u_decode(parts[1]).decode("utf-8"))
        sig_bytes = _b64u_decode(parts[2])
    except Exception:
        raise EnterprisePlatformError(401, "SSO_MALFORMED_JWT", "ID token base64url/JSON decoding failed.")

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise EnterprisePlatformError(401, "SSO_MALFORMED_JWT", "Invalid JWT header or claims object.")
    if header.get("alg") != "RS256":
        raise EnterprisePlatformError(401, "SSO_UNSAFE_JWT_ALG", "Only asymmetric RS256 OIDC signatures are permitted.")

    kid = str(header.get("kid") or "")
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list) or not keys:
        raise EnterprisePlatformError(400, "SSO_JWKS_EMPTY", "Workspace SSO JWKS has no signing keys.")
    jwk = next(
        (
            k for k in keys
            if isinstance(k, dict)
            and k.get("kty") == "RSA"
            and k.get("use", "sig") in ("sig", "")
            and (not kid or k.get("kid") == kid)
        ),
        None,
    )
    if not jwk:
        raise EnterprisePlatformError(401, "SSO_UNKNOWN_KID", "No matching RSA signing key found in JWKS.")

    try:
        n_bytes = _b64u_decode(str(jwk["n"]))
        e_bytes = _b64u_decode(str(jwk["e"]))
    except Exception:
        raise EnterprisePlatformError(400, "SSO_INVALID_JWK", "Malformed RSA modulus/exponent in JWK.")

    n_int = int.from_bytes(n_bytes, "big")
    e_int = int.from_bytes(e_bytes, "big")
    if n_int.bit_length() < 2048 or e_int < 65537 or (e_int % 2 == 0):
        raise EnterprisePlatformError(400, "SSO_WEAK_RSA_KEY", "RSA JWK must be >= 2048 bits with odd exponent >= 65537.")

    k_len = (n_int.bit_length() + 7) // 8
    if len(sig_bytes) != k_len:
        raise EnterprisePlatformError(401, "SSO_BAD_SIGNATURE", "JWT signature length does not match RSA modulus.")
    s_int = int.from_bytes(sig_bytes, "big")
    if s_int >= n_int:
        raise EnterprisePlatformError(401, "SSO_BAD_SIGNATURE", "JWT signature integer out of range.")

    em = pow(s_int, e_int, n_int).to_bytes(k_len, "big")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    digest_info = _DER_SHA256_PREFIX + hashlib.sha256(signing_input).digest()
    ps_len = k_len - len(digest_info) - 3
    if ps_len < 8:
        raise EnterprisePlatformError(401, "SSO_BAD_SIGNATURE", "RSA modulus too short for EMSA-PKCS1-v1_5.")
    expected_em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + digest_info
    if not hmac.compare_digest(em, expected_em):
        raise EnterprisePlatformError(401, "SSO_BAD_SIGNATURE", "RSA-SHA256 cryptographic signature verification failed.")

    now = int(time.time()) if now_ts is None else int(now_ts)
    skew = 60
    if str(claims.get("iss") or "") != expected_issuer:
        raise EnterprisePlatformError(401, "SSO_ISSUER_MISMATCH", "ID token issuer does not match workspace OIDC issuer.")
    aud = claims.get("aud")
    aud_ok = (aud == expected_audience) or (isinstance(aud, list) and expected_audience in aud)
    if not aud_ok:
        raise EnterprisePlatformError(401, "SSO_AUDIENCE_MISMATCH", "ID token audience does not match workspace client_id.")
    exp = int(claims.get("exp") or 0)
    nbf = int(claims.get("nbf") or 0)
    iat = int(claims.get("iat") or 0)
    if exp <= now - skew:
        raise EnterprisePlatformError(401, "SSO_TOKEN_EXPIRED", "ID token has expired.")
    if nbf and nbf > now + skew:
        raise EnterprisePlatformError(401, "SSO_TOKEN_NOT_YET_VALID", "ID token nbf is in the future.")
    if iat and iat > now + 300:
        raise EnterprisePlatformError(401, "SSO_INVALID_IAT", "ID token iat is unreasonably in the future.")
    tok_nonce = str(claims.get("nonce") or "")
    if not expected_nonce or not tok_nonce or not hmac.compare_digest(tok_nonce.encode("utf-8"), expected_nonce.encode("utf-8")):
        raise EnterprisePlatformError(401, "SSO_NONCE_MISMATCH", "ID token OIDC nonce verification failed.")
    if claims.get("email_verified") is not True:
        raise EnterprisePlatformError(403, "SSO_EMAIL_UNVERIFIED", "Corporate IdP did not assert email_verified=true.")
    return claims


def configure_enterprise_sso(store: Store, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    if not tenant:
        raise EnterprisePlatformError(404, "TENANT_NOT_FOUND", "Workspace not found.")
    provider = str(body.get("provider") or "oidc").strip().lower()[:32]
    issuer = str(body.get("issuer") or "").strip().rstrip("/")
    auth_url = str(body.get("authorization_endpoint") or f"{issuer}/authorize").strip()
    token_url = str(body.get("token_endpoint") or f"{issuer}/oauth/token").strip()
    client_id = str(body.get("client_id") or "").strip()[:160]
    if not issuer.startswith("https://") or not client_id:
        raise EnterprisePlatformError(400, "SSO_INVALID_CONFIG", "HTTPS issuer and client_id are required.")
    for label, u in (("issuer", issuer), ("authorization_endpoint", auth_url), ("token_endpoint", token_url)):
        ok_u, reason = sec.ssrf_check(u, strict_allowlist=False)
        if not ok_u:
            raise EnterprisePlatformError(400, "SSO_UNSAFE_ENDPOINT", f"{label} failed SSRF safety check: {reason}.")

    domains = [str(d).strip().lower() for d in (body.get("allowed_domains") or []) if str(d).strip()]
    if not domains or any(not _DOMAIN_RE.match(d) for d in domains):
        raise EnterprisePlatformError(400, "SSO_DOMAINS_REQUIRED", "Provide at least one valid corporate email domain in allowed_domains.")

    # Never allow JIT SSO provisioning to auto-mint `owner` or `admin`!
    default_role = str(body.get("default_role") or "agent").strip().lower()
    if default_role not in ("viewer", "agent", "manager"):
        default_role = "agent"

    jwks = body.get("jwks") if isinstance(body.get("jwks"), dict) else {"keys": []}
    sso_cfg: dict[str, Any] = {
        "enabled": bool(body.get("enabled", True)),
        "provider": provider,
        "issuer": issuer,
        "authorization_endpoint": auth_url,
        "token_endpoint": token_url,
        "client_id": client_id,
        "allowed_domains": domains,
        "default_role": default_role,
        "enforce_sso_only": bool(body.get("enforce_sso_only", False)),
        "jwks": jwks,
        "updated_at": int(time.time()),
    }
    raw_secret = str(body.get("client_secret") or "").strip()
    if raw_secret:
        sso_cfg["client_secret_sealed"] = sec.seal_tenant(tid, raw_secret)

    with store.lock:
        tenant["enterprise_sso"] = sso_cfg
        store._pg_put("tenants", tid, tenant)
        store.save()

    pub = {k: v for k, v in sso_cfg.items() if k != "client_secret_sealed"}
    pub["has_client_secret"] = bool(sso_cfg.get("client_secret_sealed"))
    return pub


def start_sso_login(store: Store, tenant_slug_or_id: str, redirect_uri: str) -> dict[str, Any]:
    tenant = next(
        (t for t in store.db["tenants"] if t["id"] == tenant_slug_or_id or t.get("slug") == tenant_slug_or_id),
        None,
    )
    sso = (tenant or {}).get("enterprise_sso") or {}
    if not tenant or not sso.get("enabled"):
        raise EnterprisePlatformError(404, "SSO_NOT_ENABLED", "Enterprise OIDC SSO is not enabled for this workspace.")
    redir = str(redirect_uri or "").strip()
    if not redir.startswith("https://") and not redir.startswith("http://127.0.0.1"):
        raise EnterprisePlatformError(400, "SSO_INVALID_REDIRECT", "redirect_uri must use HTTPS.")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = _b64u_encode(secrets.token_bytes(32))
    code_challenge = _b64u_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())

    sealed_tx = sec.issue_token(
        {
            "typ": "oidc_tx",
            "tid": tenant["id"],
            "state": state,
            "nonce": nonce,
            "cv": code_verifier,
            "redirect_uri": redir,
        },
        ttl=600,
    )
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": sso["client_id"],
        "redirect_uri": redir,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return {
        "ok": True,
        "tenant_id": tenant["id"],
        "authorization_url": f"{sso['authorization_endpoint']}?{q}",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "sso_tx": sealed_tx,
    }


def complete_sso_callback(
    store: Store,
    body: dict[str, Any],
    *,
    client_ip: str,
    user_agent: str,
) -> dict[str, Any]:
    tx_tok = str(body.get("sso_tx") or "").strip()
    tx = sec.verify_token(tx_tok)
    if not tx or tx.get("typ") != "oidc_tx":
        raise EnterprisePlatformError(401, "SSO_INVALID_TRANSACTION", "Expired or invalid OIDC login transaction.")
    provided_state = str(body.get("state") or "").strip()
    if not provided_state or not hmac.compare_digest(provided_state.encode("utf-8"), str(tx["state"]).encode("utf-8")):
        raise EnterprisePlatformError(401, "SSO_STATE_MISMATCH", "OIDC state mismatch (CSRF blocked).")
    if not shared.ADAPTER.claim_once(f"oidc_state:{tx['state']}", 900):
        raise EnterprisePlatformError(401, "SSO_STATE_REPLAYED", "OIDC login state was already consumed.")

    tid = str(tx["tid"])
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    sso = (tenant or {}).get("enterprise_sso") or {}
    if not tenant or not sso.get("enabled"):
        raise EnterprisePlatformError(403, "SSO_DISABLED", "Enterprise SSO is disabled.")

    id_token = str(body.get("id_token") or "").strip()
    claims = verify_rs256_jwt(
        id_token,
        sso.get("jwks") or {},
        expected_issuer=sso["issuer"],
        expected_audience=sso["client_id"],
        expected_nonce=str(tx["nonce"]),
    )
    if not shared.ADAPTER.claim_once(f"oidc_nonce:{tid}:{tx['nonce']}", 900):
        raise EnterprisePlatformError(401, "SSO_NONCE_REPLAYED", "OIDC nonce replay blocked.")

    email = str(claims.get("email") or "").strip().lower()
    if "@" not in email:
        raise EnterprisePlatformError(400, "SSO_MISSING_EMAIL", "ID token lacks email claim.")
    email_domain = email.rsplit("@", 1)[1]
    if email_domain not in set(sso.get("allowed_domains") or []):
        raise EnterprisePlatformError(
            403,
            "SSO_DOMAIN_FORBIDDEN",
            f"Email domain '{email_domain}' is not authorized for this workspace.",
        )

    with store.lock:
        user = next((u for u in store.db["users"] if u.get("tenant_id") == tid and str(u.get("email") or "").lower() == email), None)
        if user and not user.get("active", True):
            raise EnterprisePlatformError(403, "ACCOUNT_DISABLED", "Workspace user account is deactivated.")
        if user is None:
            user = {
                "id": nid("usr"),
                "tenant_id": tid,
                "email": email,
                "name": str(claims.get("name") or email.split("@", 1)[0]).strip()[:80],
                "role": sso.get("default_role") or "agent",
                "password_hash": "",
                "sso_subject": str(claims.get("sub") or "")[:160],
                "mfa_enrolled": False,
                "active": True,
                "created_at": int(time.time()),
            }
            store.db["users"].append(user)
            store._pg_put("users", tid, user)
            store.save()

    sess_row, refresh = sess.open_session(store, tid, user["id"], ip=client_ip, ua=user_agent, amr=["oidc", "sso"])
    access_tok = sec.issue_token(
        {"sub": user["email"], "uid": user["id"], "tid": tid, "role": user["role"], "sid": sess_row["id"], "amr": ["oidc", "sso"]},
        ttl=sess.ACCESS_TTL,
    )
    store.audit(tid, user["email"], "auth.sso_session_started", sess_row["id"], {"iss": sso["issuer"], "ip": client_ip})
    return {
        "ok": True,
        "token": access_tok,
        "csrf": sec.csrf_token(access_tok),
        "expires_in": sess.ACCESS_TTL,
        "refresh_token": refresh,
        "user": {k: v for k, v in user.items() if k not in ("pw", "password_hash")},
    }


# ---------------------------------------------------------------------------
# 3. Automated `sa11y`-Style WCAG 2.2 AA Accessibility Auditor
# ---------------------------------------------------------------------------
def _srgb_channel_lum(c8: int) -> float:
    s = c8 / 255.0
    return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return 0.0
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _srgb_channel_lum(r) + 0.7152 * _srgb_channel_lum(g) + 0.0722 * _srgb_channel_lum(b)


def contrast_ratio(hex_fg: str, hex_bg: str) -> float:
    l1, l2 = relative_luminance(hex_fg), relative_luminance(hex_bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


class _Sa11yParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.html_lang = ""
        self.in_title = False
        self.title_text = ""
        self.viewport_content = ""
        self.headings: list[int] = []
        self.labels_for: set[str] = set()
        self.label_depth = 0
        self.inputs: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self.buttons: list[dict[str, Any]] = []
        self._cur_btn: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        amap = {k.lower(): (v if v is not None else "") for k, v in attrs}
        t = tag.lower()
        if t == "html":
            self.html_lang = amap.get("lang", "").strip()
        elif t == "title":
            self.in_title = True
        elif t == "meta" and amap.get("name", "").lower() == "viewport":
            self.viewport_content = amap.get("content", "").lower()
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(t[1]))
        elif t == "label":
            self.label_depth += 1
            if amap.get("for"):
                self.labels_for.add(amap["for"])
        elif t in ("input", "select", "textarea"):
            itype = amap.get("type", "text").lower()
            if itype not in ("hidden", "submit", "button") and amap.get("aria-hidden") != "true":
                self.inputs.append({
                    "tag": t,
                    "id": amap.get("id", ""),
                    "name": amap.get("name", ""),
                    "wrapped_in_label": self.label_depth > 0,
                    "aria_label": bool(amap.get("aria-label") or amap.get("aria-labelledby")),
                })
        elif t == "img":
            if amap.get("aria-hidden") != "true" and amap.get("role") != "presentation":
                self.images.append({"has_alt": "alt" in amap, "src": amap.get("src", "")[:80]})
        elif t == "button":
            self._cur_btn = {"aria_label": amap.get("aria-label", "").strip(), "text": ""}

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self.in_title = False
        elif t == "label" and self.label_depth > 0:
            self.label_depth -= 1
        elif t == "button" and self._cur_btn is not None:
            self.buttons.append(self._cur_btn)
            self._cur_btn = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_text += data
        if self._cur_btn is not None:
            self._cur_btn["text"] += data


def audit_html_accessibility(
    html_doc: str,
    *,
    page_name: str,
    brand_hex: str = "#0f6b52",
) -> dict[str, Any]:
    parser = _Sa11yParser()
    parser.feed(html_doc or "")
    violations: list[dict[str, str]] = []

    if not parser.html_lang:
        violations.append({"rule": "html-has-lang", "wcag": "3.1.1", "severity": "serious", "detail": "<html> is missing lang attribute."})
    if not parser.title_text.strip():
        violations.append({"rule": "document-title", "wcag": "2.4.2", "severity": "serious", "detail": "Document lacks non-empty <title>."})
    if "user-scalable=no" in parser.viewport_content or "maximum-scale=1" in re.sub(r"maximum-scale=1\.[1-9]", "", parser.viewport_content):
        violations.append({"rule": "meta-viewport", "wcag": "1.4.4", "severity": "critical", "detail": "Viewport disables pinch-to-zoom."})
    if not parser.headings or 1 not in parser.headings:
        violations.append({"rule": "page-has-heading-one", "wcag": "1.3.1", "severity": "moderate", "detail": "Page has no <h1> landmark heading."})
    for prev, cur in zip(parser.headings, parser.headings[1:]):
        if cur > prev + 1:
            violations.append({"rule": "heading-order", "wcag": "1.3.1", "severity": "moderate", "detail": f"Heading jumped from <h{prev}> to <h{cur}>."})
            break
    for img in parser.images:
        if not img["has_alt"]:
            violations.append({"rule": "image-alt", "wcag": "1.1.1", "severity": "critical", "detail": f"Image {img['src']!r} missing alt attribute."})
    for inp in parser.inputs:
        labeled = inp["wrapped_in_label"] or inp["aria_label"] or (inp["id"] and inp["id"] in parser.labels_for)
        if not labeled:
            violations.append({"rule": "form-field-label", "wcag": "4.1.2", "severity": "critical", "detail": f"Form control {inp['name'] or inp['tag']!r} has no accessible label."})
    for btn in parser.buttons:
        if not btn["text"].strip() and not btn["aria_label"]:
            violations.append({"rule": "button-name", "wcag": "4.1.2", "severity": "critical", "detail": "Button has empty accessible name."})

    cr = contrast_ratio("#ffffff", brand_hex)
    if cr < 4.5:
        violations.append({"rule": "color-contrast", "wcag": "1.4.3", "severity": "serious", "detail": f"Brand color {brand_hex} against #ffffff is {cr}:1 (WCAG AA requires >= 4.5:1)."})

    score = max(0, 100 - len(violations) * 15)
    return {
        "page": page_name,
        "wcag_target": "WCAG 2.2 AA",
        "passed": len(violations) == 0,
        "score": score,
        "brand_contrast_ratio": cr,
        "violation_count": len(violations),
        "violations": violations,
    }


def audit_workspace_accessibility(store: Store, tid: str, *, public_funnel_renderer: Any = None, public_site_html: str = "") -> dict[str, Any]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), {})
    brand_hex = str(((tenant.get("white_label") or {}).get("primary_color")) or "#0f6b52")
    reports: list[dict[str, Any]] = []

    idx_path = os.path.join(root, "web", "index.html")
    if os.path.isfile(idx_path):
        with open(idx_path, "r", encoding="utf-8") as fh:
            reports.append(audit_html_accessibility(fh.read(), page_name="web/index.html", brand_hex=brand_hex))
    if public_site_html:
        reports.append(audit_html_accessibility(public_site_html, page_name="/site", brand_hex=brand_hex))
    if callable(public_funnel_renderer):
        for f in store.all("funnels", tid)[:5]:
            if f.get("published"):
                st, html = public_funnel_renderer(store, f["slug"])
                if st == 200:
                    reports.append(audit_html_accessibility(html, page_name=f"/f/{f['slug']}", brand_hex=brand_hex))

    total_v = sum(r["violation_count"] for r in reports)
    return {
        "ok": total_v == 0,
        "wcag_standard": "WCAG 2.2 AA (sa11y-equivalent)",
        "pages_audited": len(reports),
        "total_violations": total_v,
        "reports": reports,
    }

"""
Realtor Estates — Neon service adapters.

`neon deploy` provisions the services declared in `neon.ts` and writes their
credentials into `.env.local`. This module is the single place where the app
touches them, so every service is:

  * optional (the app runs with none of them configured),
  * fail-soft (a provider outage degrades to the local path, never a crash),
  * never logged (URLs and keys are redacted through app/config.py).

Services and how they are wired here:

| neon.ts service | Env vars                                | Adapter |
|-----------------|-----------------------------------------|---------|
| postgres        | DATABASE_URL                            | app/db.py (PGBackend) |
| ai-gateway      | NEON_AI_GATEWAY_URL/KEY                 | `ai_chat()` — OpenAI-compatible `/v1/chat/completions` |
| auth            | NEON_AUTH_BASE_URL                      | `verify_session_token()` — bearer-token introspection |
| object-storage  | NEON_STORAGE_*                          | `storage_config()` + `presign_get()` (SigV4) |
| realtime        | NEON_REALTIME_URL/TOKEN                 | `realtime_config()` — replaces the in-process SSE fan-out |
| functions       | NEON_FUNCTIONS_URL                      | `functions_config()` — `hello.ts` smoke function |
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

from . import config as cfg

TIMEOUT = 12


# ---------------------------------------------------------------------------
# Presence helpers — never return a credential
# ---------------------------------------------------------------------------
def _present(name: str, min_len: int = 16) -> bool:
    """Configured *and* not a placeholder.

    The 16-character floor is right for keys and wrong for a bucket name
    ("assets" is a real value), so non-secret fields pass `min_len=1`.
    """
    value = cfg.get(name, "").strip()
    if not value or len(value) < min_len:
        return False
    return not cfg.is_placeholder(value) if min_len >= 16 else True


def status() -> dict[str, Any]:
    return {
        "postgres": {"configured": _present("DATABASE_URL", 1)},
        "ai_gateway": {"configured": _present("NEON_AI_GATEWAY_URL", 1) and _present("NEON_AI_GATEWAY_KEY")},
        "auth": {"configured": _present("NEON_AUTH_BASE_URL", 1)},
        "object_storage": {
            "configured": all(
                (
                    _present("NEON_STORAGE_ENDPOINT", 1),
                    _present("NEON_STORAGE_BUCKET", 1),
                    _present("NEON_STORAGE_ACCESS_KEY"),
                    _present("NEON_STORAGE_SECRET_KEY"),
                )
            )
        },
        "realtime": {"configured": _present("NEON_REALTIME_URL", 1)},
        "functions": {"configured": _present("NEON_FUNCTIONS_URL", 1)},
        "env_source": cfg._SOURCES.get("DATABASE_URL", "unset"),
    }


# ---------------------------------------------------------------------------
# AI gateway — OpenAI-compatible chat completions
# ---------------------------------------------------------------------------
def ai_configured() -> bool:
    return _present("NEON_AI_GATEWAY_URL", 1) and _present("NEON_AI_GATEWAY_KEY")


def ai_chat(messages: list[dict], model: str = "", max_tokens: int = 512, temperature: float = 0.3) -> dict[str, Any]:
    """Call the gateway. Returns {ok, text, model, usage} or {ok: False, error}."""
    if not ai_configured():
        return {"ok": False, "error": "ai-gateway not configured", "text": ""}
    base = cfg.get("NEON_AI_GATEWAY_URL", "").rstrip("/")
    url = base if base.endswith("/chat/completions") else base + "/v1/chat/completions"
    body = json.dumps(
        {
            "model": model or cfg.get("NEON_AI_GATEWAY_MODEL", "gpt-4o-mini"),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", f"Bearer {cfg.get('NEON_AI_GATEWAY_KEY', '')}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "text": text, "model": data.get("model", ""), "usage": data.get("usage", {})}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}", "text": ""}
    except Exception as exc:  # network, DNS, TLS
        return {"ok": False, "error": cfg.redact(f"{exc.__class__.__name__}: {exc}")[:200], "text": ""}


# ---------------------------------------------------------------------------
# Auth — verify a bearer/cookie token issued by Neon Auth (Better Auth / Stack)
# ---------------------------------------------------------------------------
_AUTH_CACHE_LOCK = threading.Lock()
_AUTH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
AUTH_CACHE_TTL_SEC = 30.0


def auth_configured() -> bool:
    return _present("NEON_AUTH_BASE_URL", 1)


def auth_mode() -> str:
    """`local` (default), `hybrid` (local or Neon Auth), or `neon` (Neon Auth only)."""
    raw = (cfg.get("RE_AUTH_MODE", "local") or "local").strip().lower()
    return raw if raw in ("local", "hybrid", "neon") else "local"


def _auth_cache_key(token: str) -> str:
    return hashlib.sha256(("re-neon-auth:" + token).encode("utf-8")).hexdigest()


def invalidate_auth_cache(token: str = "") -> None:
    with _AUTH_CACHE_LOCK:
        if token:
            _AUTH_CACHE.pop(_auth_cache_key(token), None)
        else:
            _AUTH_CACHE.clear()


def _normalise_neon_auth_payload(payload: Any) -> dict[str, Any] | None:
    """Extract a verified user identity from Better Auth or Stack Auth responses.

    Live Neon Auth (Better Auth `GET /get-session`) returns HTTP 200 with JSON
    body `null` when a token is invalid or signed out; treating any 200 as
    authenticated would be an auth bypass. Require both a non-empty user `id`
    and `email` (plus an unexpired `session` when Better Auth wraps it).
    """
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("user"), dict):
        user_obj = payload["user"]
        sess_obj = payload.get("session")
        if not isinstance(sess_obj, dict) or not sess_obj.get("id"):
            return None
        exp_raw = sess_obj.get("expiresAt")
        if isinstance(exp_raw, str) and exp_raw:
            try:
                exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                if exp_dt.timestamp() <= time.time():
                    return None
            except ValueError:
                pass
        uid = str(user_obj.get("id") or "").strip()
        email = str(user_obj.get("email") or "").strip().lower()
        if not uid or "@" not in email:
            return None
        return {
            "id": uid,
            "email": email,
            "name": str(user_obj.get("name") or ""),
            "email_verified": bool(user_obj.get("emailVerified", False)),
            "session_id": str(sess_obj["id"]),
            "_source": "neon-auth",
        }
    # Legacy Stack Auth shape (`/users/me`)
    uid = str(payload.get("id") or "").strip()
    email = str(payload.get("primary_email") or payload.get("email") or "").strip().lower()
    if not uid or "@" not in email:
        return None
    return {
        "id": uid,
        "email": email,
        "name": str(payload.get("display_name") or payload.get("name") or ""),
        "email_verified": bool(payload.get("primary_email_verified", payload.get("email_verified", False))),
        "session_id": str(payload.get("session_id") or f"nauth_{uid[:16]}"),
        "_source": "neon-auth",
    }


def verify_session_token(token: str, *, use_cache: bool = True) -> dict[str, Any] | None:
    """Introspect a Neon Auth token. Returns normalised user payload or None."""
    if not auth_configured() or not token:
        return None
    ck = _auth_cache_key(token)
    if use_cache:
        with _AUTH_CACHE_LOCK:
            hit = _AUTH_CACHE.get(ck)
            if hit and hit[0] > time.monotonic():
                return dict(hit[1])

    base = cfg.get("NEON_AUTH_BASE_URL", "").rstrip("/")
    cookie_hdr = (
        f"__Secure-neon-auth.session_token={token}; "
        f"neon-auth.session_token={token}; "
        f"better-auth.session_token={token}"
    )
    for path in ("/get-session", "/users/me", "/v1/users/me"):
        req = urllib.request.Request(base + path, method="GET")
        req.add_header("authorization", f"Bearer {token}")
        req.add_header("cookie", cookie_hdr)
        req.add_header("accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read())
            norm = _normalise_neon_auth_payload(payload)
            if norm is not None:
                if use_cache:
                    with _AUTH_CACHE_LOCK:
                        if len(_AUTH_CACHE) > 2048:
                            now_m = time.monotonic()
                            for k_old in [k for k, (exp_m, _) in _AUTH_CACHE.items() if exp_m <= now_m]:
                                _AUTH_CACHE.pop(k_old, None)
                        _AUTH_CACHE[ck] = (time.monotonic() + AUTH_CACHE_TTL_SEC, norm)
                return dict(norm)
            if path == "/get-session":
                # `/get-session` answered 200 `null`: token is invalid on Better Auth;
                # only fall through if `/get-session` returned 404 (a Stack Auth mock).
                return None
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return None
            continue
        except Exception:
            continue
    return None


def auth_sign_out(token: str) -> bool:
    """Revoke a session upstream on Neon Auth and evict the local introspection cache."""
    invalidate_auth_cache(token)
    if not auth_configured() or not token:
        return False
    base = cfg.get("NEON_AUTH_BASE_URL", "").rstrip("/")
    cookie_hdr = (
        f"__Secure-neon-auth.session_token={token}; "
        f"neon-auth.session_token={token}; "
        f"better-auth.session_token={token}"
    )
    req = urllib.request.Request(
        base + "/sign-out",
        data=b"{}",
        headers={
            "authorization": f"Bearer {token}",
            "cookie": cookie_hdr,
            "content-type": "application/json",
            "origin": base,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Object storage — S3-compatible, SigV4 query-string presign
# ---------------------------------------------------------------------------
def storage_config() -> dict[str, str]:
    return {
        "endpoint": cfg.get("NEON_STORAGE_ENDPOINT", ""),
        "bucket": cfg.get("NEON_STORAGE_BUCKET", ""),
        "access_key": cfg.get("NEON_STORAGE_ACCESS_KEY", ""),
        "secret_key": cfg.get("NEON_STORAGE_SECRET_KEY", ""),
        "region": cfg.get("NEON_STORAGE_REGION", "") or "auto",
    }


def storage_configured() -> bool:
    c = storage_config()
    return all(c[k] for k in ("endpoint", "bucket", "access_key", "secret_key"))


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signing_key(secret_key: str, date: str, region: str) -> bytes:
    k = _sign(("AWS4" + secret_key).encode(), date)
    for part in (region, "s3", "aws4_request"):
        k = _sign(k, part)
    return k


def _clean_object_key(key: str) -> str:
    """Normalise an object key and refuse path-traversal or control characters.

    Keys carry tenant prefixes (`exports/tenants/<tid>/…`, `recordings/tenants/<tid>/…`);
    a `..` segment that reached `quote(..., safe="/-_.~")` could otherwise climb
    into a sibling prefix on an S3 gateway that normalises URL paths.
    """
    if not isinstance(key, str) or "\x00" in key or "\\" in key:
        raise ValueError("invalid object key")
    cleaned = key.strip().lstrip("/")
    if not cleaned:
        raise ValueError("object key must not be empty")
    parts = cleaned.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("object key must not contain empty or traversal segments")
    return cleaned


def presign_get(key: str, expires: int = 900) -> str:
    """SigV4 query-string presigned GET URL (AWS S3 / R2 / Neon storage).

    Verified live against Neon `projectassets` (`docs/12` §4, `docs/36`, `docs/37`).
    """
    c = storage_config()
    if not storage_configured():
        raise RuntimeError("object storage is not configured")
    clean_key = _clean_object_key(key)
    if expires < 1 or expires > 604800:
        raise ValueError("expires must be between 1 and 604800 seconds")
    u = urlparse(c["endpoint"])
    host = u.netloc
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    scope = f"{date}/{c['region']}/s3/aws4_request"
    canonical_uri = "/" + quote(f"{c['bucket']}/{clean_key}", safe="/-_.~")
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{c['access_key']}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(expires)),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    canonical_request = "\n".join(["GET", canonical_uri, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    k = _signing_key(c["secret_key"], date, c["region"])
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    scheme = u.scheme or "https"
    return f"{scheme}://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def _sigv4_request(
    method: str,
    key: str,
    body: bytes = b"",
    extra_headers: dict[str, str] | None = None,
    timeout: float = TIMEOUT,
) -> tuple[int, dict[str, str], bytes]:
    """Execute a header-signed SigV4 S3 request (`PUT`, `HEAD`, `DELETE`, `GET`).

    Uses SHA-256 payload signing (`x-amz-content-sha256`) rather than
    `UNSIGNED-PAYLOAD` so object storage verifies upload integrity on arrival.
    Stdlib-only (`D2`): zero third-party SDK dependencies.
    """
    c = storage_config()
    if not storage_configured():
        raise RuntimeError("object storage is not configured")
    clean_key = _clean_object_key(key)
    u = urlparse(c["endpoint"])
    host = u.netloc
    scheme = u.scheme or "https"
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    scope = f"{date}/{c['region']}/s3/aws4_request"
    canonical_uri = "/" + quote(f"{c['bucket']}/{clean_key}", safe="/-_.~")
    payload_sha = hashlib.sha256(body).hexdigest()

    to_sign: dict[str, str] = {
        "host": host,
        "x-amz-content-sha256": payload_sha,
        "x-amz-date": amz_date,
    }
    for hk, hv in (extra_headers or {}).items():
        to_sign[hk.lower().strip()] = " ".join(str(hv).strip().split())

    sorted_keys = sorted(to_sign)
    canonical_headers = "".join(f"{hk}:{to_sign[hk]}\n" for hk in sorted_keys)
    signed_headers = ";".join(sorted_keys)
    canonical_request = "\n".join(
        [method.upper(), canonical_uri, "", canonical_headers, signed_headers, payload_sha]
    )
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    k = _signing_key(c["secret_key"], date, c["region"])
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={c['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    wire_headers = {hk: hv for hk, hv in to_sign.items() if hk != "host"}
    wire_headers["Authorization"] = auth_header
    if method.upper() == "PUT":
        wire_headers["Content-Length"] = str(len(body))

    url = f"{scheme}://{host}{canonical_uri}"
    req = urllib.request.Request(
        url,
        data=body if method.upper() in ("PUT", "POST") else None,
        headers=wire_headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_headers = {rk.lower(): rv for rk, rv in resp.headers.items()}
            resp_body = b"" if method.upper() == "HEAD" else resp.read()
            return resp.status, resp_headers, resp_body
    except urllib.error.HTTPError as exc:
        err_headers = {rk.lower(): rv for rk, rv in (exc.headers.items() if exc.headers else [])}
        err_body = b""
        if method.upper() != "HEAD":
            try:
                err_body = exc.read(512)
            except Exception:
                err_body = b""
        return exc.code, err_headers, err_body
    except Exception as exc:
        raise RuntimeError(f"object storage {method.upper()} failed: {cfg.redact(str(exc))[:160]}") from None


def put_object(
    key: str,
    data: bytes | str,
    content_type: str = "application/octet-stream",
    timeout: float = TIMEOUT,
) -> dict[str, Any]:
    """Upload bytes to S3-compatible object storage with SHA-256 content verification."""
    clean_key = _clean_object_key(key)
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    payload_sha = hashlib.sha256(raw).hexdigest()
    ctype = (content_type or "application/octet-stream").strip()[:120]
    status_code, hdrs, err_body = _sigv4_request(
        "PUT",
        clean_key,
        body=raw,
        extra_headers={"content-type": ctype},
        timeout=timeout,
    )
    if status_code not in (200, 201, 204):
        snippet = cfg.redact(err_body.decode("utf-8", "ignore"))[:160]
        raise RuntimeError(f"put_object HTTP {status_code}: {snippet}")
    return {
        "ok": True,
        "bucket": storage_config()["bucket"],
        "key": clean_key,
        "bytes": len(raw),
        "sha256": payload_sha,
        "etag": (hdrs.get("etag") or "").strip('"'),
        "content_type": ctype,
    }


def head_object(key: str, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Stat an object in storage without downloading its body."""
    clean_key = _clean_object_key(key)
    status_code, hdrs, _ = _sigv4_request("HEAD", clean_key, timeout=timeout)
    if status_code == 404:
        return {"exists": False, "key": clean_key}
    if status_code != 200:
        raise RuntimeError(f"head_object HTTP {status_code}")
    return {
        "exists": True,
        "key": clean_key,
        "bytes": int(hdrs.get("content-length") or 0),
        "content_type": hdrs.get("content-type", ""),
        "etag": (hdrs.get("etag") or "").strip('"'),
    }


def delete_object(key: str, timeout: float = TIMEOUT) -> dict[str, Any]:
    """Delete an object from storage (`200`/`204`/`404` all converge on absent)."""
    clean_key = _clean_object_key(key)
    status_code, _, err_body = _sigv4_request("DELETE", clean_key, timeout=timeout)
    if status_code not in (200, 202, 204, 404):
        snippet = cfg.redact(err_body.decode("utf-8", "ignore"))[:160]
        raise RuntimeError(f"delete_object HTTP {status_code}: {snippet}")
    return {"deleted": True, "key": clean_key, "status": status_code}


# ---------------------------------------------------------------------------
# Realtime + functions
# ---------------------------------------------------------------------------
def realtime_config() -> dict[str, Any]:
    """Neon Realtime replaces the in-process SSE fan-out in app/server.py."""
    return {"configured": _present("NEON_REALTIME_URL", 1), "url": cfg.get("NEON_REALTIME_URL", "")}


FUNCTION_URL_VARS = (
    ("propertyphotos", "NEON_PHOTOS_FUNCTION_URL"),
    ("compliancesweep", "NEON_SWEEP_FUNCTION_URL"),
    ("paymentswebhook", "NEON_WEBHOOK_FUNCTION_URL"),
)


def functions_config() -> dict[str, Any]:
    urls = {slug: cfg.get(var, "") for slug, var in FUNCTION_URL_VARS if _present(var, 8)}
    legacy = cfg.get("NEON_FUNCTIONS_URL", "")
    return {
        "configured": bool(urls) or _present("NEON_FUNCTIONS_URL", 8),
        "url": legacy or next(iter(urls.values()), ""),
        "endpoints": urls,
    }


def ping_functions() -> dict[str, Any]:
    """Smoke-test the deployed Neon Functions (`propertyphotos`, `compliancesweep`, `paymentswebhook`)."""
    fc = functions_config()
    if not fc["configured"]:
        return {"ok": False, "error": "functions not configured"}
    endpoints: dict[str, str] = dict(fc.get("endpoints") or {})
    if not endpoints and fc.get("url"):
        endpoints = {"default": fc["url"]}
    results: dict[str, Any] = {}
    all_ok = True
    for slug, url in endpoints.items():
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                results[slug] = {
                    "ok": resp.status == 200,
                    "status": resp.status,
                    "body": resp.read(200).decode("utf-8", "ignore"),
                }
                if resp.status != 200:
                    all_ok = False
        except Exception as exc:
            all_ok = False
            results[slug] = {"ok": False, "error": cfg.redact(str(exc))[:160]}
    return {"ok": all_ok, "functions": results}


def smoke() -> dict[str, Any]:
    """Report which services are live. Used by /healthz and the ops runbook."""
    out: dict[str, Any] = {"status": status()}
    if ai_configured():
        r = ai_chat([{"role": "user", "content": "Reply with the single word: ok"}], max_tokens=8)
        out["ai_gateway"] = {"ok": r["ok"], "error": r.get("error", ""), "sample": (r.get("text") or "")[:20]}
    if functions_config()["configured"]:
        out["functions"] = ping_functions()
    return out


if __name__ == "__main__":
    print(json.dumps(smoke(), indent=2))

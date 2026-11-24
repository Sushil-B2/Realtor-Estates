"""
Realtor Estates — HTTP edge.

Responsibilities kept deliberately small:
  * static file serving with traversal protection + correct MIME types
  * JSON API dispatch (all auth/RBAC/CSRF lives in app/api.py)
  * server-rendered public funnel pages (works with JS disabled, 2G-friendly)
  * Server-Sent Events for the live activity rail
  * security headers on *every* response, including errors
  * request logging with PII scrubbing

Run:  python3 -m app.server --port 8000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import posixpath
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import api, config as CFG, neon, security as sec  # noqa: E402
from app import realtime as rt  # noqa: E402
from app import sessions as sess  # noqa: E402
from app import shared_state as shared  # noqa: E402
from app.db import StorageUnavailable  # noqa: E402
from app.store import STORE, nid  # noqa: E402
from app import store as store_mod  # noqa: E402
from app import automations as autm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_ROOT = os.path.join(ROOT, "web")
MAX_BODY = 1_000_000
SESSION_COOKIE = "re_session"
# The refresh cookie is sent only to the auth routes. A credential that travels
# with every request is an access token with a longer life.
REFRESH_PATH = "/api/v1/auth"

# Stat-validated in-memory static shell cache: `(mtime_ns, size, raw_bytes, etag)`.
# Serving the shell from RAM + ETag/304 means page loads and navigations never
# touch `STORE` or wake scaled-to-zero database compute (`docs/37`).
_STATIC_LOCK = threading.Lock()
_STATIC_CACHE: dict[str, tuple[int, int, bytes, str]] = {}

# Coalesced per-tenant SSE summary so 100 simultaneous subscribers share one
# snapshot computation per second instead of scanning `STORE` 100 times.
_SSE_SUMMARY_LOCK = threading.Lock()
_SSE_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}


def _on_remote_realtime_event(tenant_id: str, ev: dict) -> None:
    cols = ev.get("collections") or None
    STORE.invalidate_freshness(tenant_id, cols)
    with _SSE_SUMMARY_LOCK:
        _SSE_SUMMARY_CACHE.pop(tenant_id, None)


rt.HUB.on_remote_event(_on_remote_realtime_event)


# ---------------------------------------------------------------------------
# Proxy trust
#
# `client_ip()` used to return `X-Forwarded-For.split(",")[0]` unconditionally.
# XFF is a *client-supplied* header: anyone can rotate it per request and move
# themselves into a fresh rate-limit bucket, and every audit row then records
# an address the caller invented (SEN-001, reproduced by sentinel/agent.py —
# 14 failed logins, 14 different XFF values, logged as 14 different peers).
#
# The peer address is the only fact we own. A proxy header is honoured *only*
# when the TCP peer is a proxy we listed in RE_TRUSTED_PROXIES (CIDR list,
# empty by default). Behind a load balancer, list it; otherwise a spoofed
# header is simply ignored.
# ---------------------------------------------------------------------------
TRUSTED_PROXIES: list = []


def _load_trusted_proxies() -> list:
    import ipaddress

    nets = []
    for raw in (CFG.get("RE_TRUSTED_PROXIES", "") or "").replace(";", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            nets.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print(f"[server] RE_TRUSTED_PROXIES: ignoring invalid CIDR {raw!r}")
    return nets


TRUSTED_PROXIES = _load_trusted_proxies()


def _peer_is_trusted(peer: str) -> bool:
    import ipaddress

    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in TRUSTED_PROXIES if addr.version == net.version)


_FUNNEL_VIEW_LOCK = threading.Lock()
_FUNNEL_VIEW_SEEN: dict[tuple[str, str], float] = {}
_FUNNEL_VIEW_PENDING: dict[tuple[str, str], int] = {}
_FUNNEL_LAST_FLUSH: float = 0.0
_NEVER_FRAME_PREFIXES = ("/api/v1/auth/", "/api/v1/security/", "/api/v1/stepup/", "/keysetup")


def _frame_ancestors_directive(path: str) -> str | None:
    """Compute a safe `frame-ancestors` value when preview framing is requested (`F-09`).

    1. Sensitive auth, security, step-up, and keysetup routes are NEVER frameable (`None`).
    2. In `CFG.PRODUCTION`, wildcard framing is unconditionally prohibited; only explicit
       `https://host` origins in `RE_PREVIEW_FRAME_ANCESTORS` are honoured.
    3. In non-production dev preview (`RE_ALLOW_PREVIEW_FRAME=1`), defaults to HTTPS
       preview parents + localhost without emitting wildcard `*`.
    """
    norm_path = (path or "/").lower()
    if any(norm_path.startswith(pref) for pref in _NEVER_FRAME_PREFIXES):
        return None
    if os.environ.get("RE_ALLOW_PREVIEW_FRAME", "").strip() != "1":
        return None
    explicit = os.environ.get("RE_PREVIEW_FRAME_ANCESTORS", "").strip()
    if CFG.PRODUCTION:
        if not explicit:
            return None
        tokens = [t.strip() for t in explicit.split() if t.strip()]
        if not tokens or any(t in ("*", "http:", "https:") or not t.startswith("https://") for t in tokens):
            return None
        return " ".join(tokens)
    if explicit:
        tokens = [t.strip() for t in explicit.split() if t.strip() and t != "*"]
        return " ".join(tokens) if tokens else None
    return "https: http://localhost:* http://127.0.0.1:*"


def flush_funnel_views(store: Any | None = None) -> int:
    """Flush coalesced public funnel impression increments to durable storage (`F-04`)."""
    global _FUNNEL_LAST_FLUSH
    target = store or STORE
    with _FUNNEL_VIEW_LOCK:
        if not _FUNNEL_VIEW_PENDING:
            _FUNNEL_LAST_FLUSH = time.monotonic()
            return 0
        batch = dict(_FUNNEL_VIEW_PENDING)
        _FUNNEL_VIEW_PENDING.clear()
        _FUNNEL_LAST_FLUSH = time.monotonic()
    flushed = 0
    for (tid, fid), delta in batch.items():
        cur = target.get("funnels", tid, fid)
        if cur:
            target.update("funnels", tid, fid, {"visits": int(cur.get("visits") or 0) + int(delta)})
            flushed += int(delta)
    return flushed


def _record_funnel_view(store: Any, funnel: dict, client_ip: str) -> None:
    """Coalesce anonymous `GET /f/<slug>` impressions without per-request global lock/DB write (`F-04`)."""
    now_m = time.monotonic()
    key_ip = (str(client_ip or ""), str(funnel.get("id") or ""))
    key_fn = (str(funnel.get("tenant_id") or ""), str(funnel.get("id") or ""))
    should_flush = False
    with _FUNNEL_VIEW_LOCK:
        if len(_FUNNEL_VIEW_SEEN) > 4096:
            cutoff = now_m - 60.0
            for k in [k for k, ts in _FUNNEL_VIEW_SEEN.items() if ts < cutoff]:
                _FUNNEL_VIEW_SEEN.pop(k, None)
        prev = _FUNNEL_VIEW_SEEN.get(key_ip, 0.0)
        if (now_m - prev) >= 60.0:
            _FUNNEL_VIEW_SEEN[key_ip] = now_m
            _FUNNEL_VIEW_PENDING[key_fn] = _FUNNEL_VIEW_PENDING.get(key_fn, 0) + 1
        if _FUNNEL_VIEW_PENDING and (now_m - _FUNNEL_LAST_FLUSH) >= 5.0:
            should_flush = True
    if should_flush:
        flush_funnel_views(store)


class Handler(BaseHTTPRequestHandler):
    # No product/version banner: `Server: RealtorEstates/1.0 Python/3.13.18`
    # tells an attacker exactly which runtime to aim at (SEN-006). The header is
    # optional in RFC 9110, so we stop sending it.
    server_version = ""
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = max(5, int(os.environ.get("RE_HTTP_SOCKET_TIMEOUT", "15") or 15))

    def version_string(self) -> str:  # pragma: no cover - exercised by the sentinel
        return ""

    def send_response(self, code: int, message: str | None = None) -> None:
        # BaseHTTPRequestHandler.send_response() writes `Server:` and `Date:`.
        # Keep the date, drop the banner.
        self.log_request(code)
        self.send_response_only(code, message)
        self.send_header("Date", self.date_time_string())

    # ---------------- utilities ----------------
    _SENSITIVE_QS = ("token", "access_token", "api_key", "apikey", "key", "secret",
                     "password", "sig", "signature", "code", "auth", "session")

    def log_message(self, fmt: str, *args) -> None:  # quieter, scrubbed
        """Access log line with credentials removed.

        The request line is attacker-controlled, so it is scrubbed, not trusted:
        a token in the query string (`?token=eyJ...`, sent by anything that
        followed an old link) used to land in the log verbatim — a credential at
        rest in a file nobody thinks of as a credential store (SEN-005).
        """
        line = fmt % args
        line = re.sub(r"([?&](?:" + "|".join(self._SENSITIVE_QS) + r")=)[^&\s\"]*", r"\1[redacted]",
                      line, flags=re.IGNORECASE)
        line = re.sub(r"\bBearer\s+\S+", "Bearer [redacted]", line, flags=re.IGNORECASE)
        line = re.sub(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", "[redacted-jwt]", line)
        line = re.sub(r"(re_session=)[^;\s\"]*", r"\1[redacted]", line)
        line = sec.redact_secrets(line)
        sys.stderr.write(f"{self.log_date_time_string()} {line}\n")

    def _cookie_is_secure(self) -> bool:
        """Whether auth cookies must carry `; Secure` (`docs/35`).

        Set whenever:
          1. `RE_ENV=production` (always `Secure` in production),
          2. `RE_SECURE_COOKIES=1` is set explicitly, or
          3. a configured `RE_TRUSTED_PROXIES` peer forwarded `X-Forwarded-Proto: https`.

        An untrusted peer's `X-Forwarded-Proto` is ignored on purpose: honouring it
        on plain HTTP lets an attacker force `Secure` cookies a browser on `http://`
        will subsequently refuse to send back (a cookie-forcing denial of service).
        """
        if CFG.PRODUCTION and os.environ.get("RE_INSECURE_COOKIES_TEST", "") != "1":
            return True
        if os.environ.get("RE_SECURE_COOKIES", "").strip().lower() in ("1", "true", "yes"):
            return True
        peer = self.client_address[0] if getattr(self, "client_address", None) else ""
        if TRUSTED_PROXIES and peer and _peer_is_trusted(peer):
            protos = [p.strip().lower() for p in (self.headers.get("x-forwarded-proto") or "").split(",") if p.strip()]
            if protos and protos[-1] == "https":
                return True
        return False

    def _session_cookie(self, token: str) -> str:
        sec_flag = "; Secure" if self._cookie_is_secure() else ""
        return (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age={sess.ACCESS_TTL}{sec_flag}")

    def _refresh_cookie(self, token: str | None) -> str:
        """The long-lived half, and the most carefully scoped cookie here.

        `Path=/api/v1/auth` means it is sent to `/auth/refresh` and to nothing
        else — not to a contact list, not to a public funnel, not to a webhook.
        A refresh token that travels with every request is an access token with a
        longer life.
        """
        if not token:
            return self._clear_cookie(sess.REFRESH_COOKIE, REFRESH_PATH)
        sec_flag = "; Secure" if self._cookie_is_secure() else ""
        return (f"{sess.REFRESH_COOKIE}={token}; Path={REFRESH_PATH}; HttpOnly; "
                f"SameSite=Strict; Max-Age={sess.REFRESH_TTL}{sec_flag}")

    def _clear_cookie(self, name: str, path: str) -> str:
        sec_flag = "; Secure" if self._cookie_is_secure() else ""
        return f"{name}=; Path={path}; HttpOnly; SameSite=Strict; Max-Age=0{sec_flag}"

    def _headers(self, status: int, ctype: str, length: int, extra: dict | None = None, cache: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        req_path = unquote(urlparse(getattr(self, "path", "/")).path)
        frame_anc = _frame_ancestors_directive(req_path)
        for k, v in sec.SECURITY_HEADERS.items():
            if k == "Cache-Control" and cache:
                continue
            if frame_anc:
                if k in ("X-Frame-Options", "Cross-Origin-Opener-Policy",
                         "Cross-Origin-Embedder-Policy", "Cross-Origin-Resource-Policy"):
                    continue
                if k == "Content-Security-Policy":
                    v = v.replace("frame-ancestors 'none'", f"frame-ancestors {frame_anc}")
            self.send_header(k, v)
        if cache:
            self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            if isinstance(v, (list, tuple)):
                # a login sets two cookies (session + refresh); HTTP allows one
                # header line each, and a dict cannot hold both
                for item in v:
                    self.send_header(k, item)
            else:
                self.send_header(k, v)
        self.end_headers()

    def _json(self, status: int, payload: dict, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        hdrs = {**(extra or {})}
        self._headers(status, "application/json; charset=utf-8", len(body), hdrs)
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _html(self, status: int, html: str) -> None:
        body = html.encode()
        self._headers(status, "text/html; charset=utf-8", len(body))
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _text(self, status: int, text: str, extra: dict | None = None) -> None:
        body = text.encode("utf-8")
        self._headers(status, "text/plain; charset=utf-8", len(body), extra)
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def client_ip(self) -> str:
        """The rate-limit / audit identity of the caller.

        Peer address unless the peer is a configured proxy, in which case the
        *last* entry a trusted proxy appended is used (left-most values are
        whatever the client chose to send).
        """
        peer = self.client_address[0]
        if TRUSTED_PROXIES and _peer_is_trusted(peer):
            fwd = [p.strip() for p in (self.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
            if fwd:
                return fwd[-1][:45]
        return peer

    def _health_payload(self) -> dict:
        token = self.cookie_token()
        if not token:
            auth = self.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        claims = sec.verify_session_token(token or "")
        privileged = bool(claims and claims.get("role") in ("owner", "admin", "manager"))
        # Anonymous liveness checks do not receive DB latency/version anyway
        # (SEN-007); skipping `ping()` here lets scaled-to-zero compute sleep.
        db = STORE.backend_status(probe=privileged)
        payload: dict = {"ok": True, "service": "realtor-estates", "time": int(time.time()),
                         "db": {"backend": db.get("backend"), "durable": db.get("durable")}}
        if privileged:
            payload["db"].update({k: db.get(k) for k in ("ok", "latency_ms", "version", "documents", "error", "dsn")})
            payload["secrets"] = {"master_key": bool(CFG.MASTER_KEY), "env": CFG.ENV}
            # where rate limits and revocations actually live — the one fact an
            # operator needs before adding a second instance (docs/31)
            payload["shared_state"] = shared.describe()
            payload["realtime"] = rt.HUB.stats()
        return payload

    def cookie_token(self) -> str | None:
        raw = self.headers.get("cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == SESSION_COOKIE:
                    return v
        return None

    # ---------------- verbs ----------------
    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PATCH(self) -> None:
        self._route("PATCH")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def do_OPTIONS(self) -> None:
        # No CORS: this API is same-origin only. Preflight is answered with 405.
        self._json(405, {"error": {"code": "METHOD_NOT_ALLOWED", "message": "Cross-origin requests are not supported."}})

    def do_HEAD(self) -> None:
        self._route("GET", head_only=True)

    def _route(self, method: str, head_only: bool = False) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if path == "/healthz":
                # Unauthenticated callers get a liveness answer, not an
                # infrastructure report. The DSN host, the Postgres build and
                # the table counts were readable by anyone who found this URL
                # (SEN-007): that is a map for an attacker, and the demo's
                # "nothing public" claim cannot survive it.
                return self._json(200, self._health_payload())

            if path.startswith("/api/v1/events"):
                return self._sse(query)

            if path.startswith("/api/"):
                body = {}
                raw = b""          # a body-less request still has to bind `raw`
                is_funnel_form = False
                length = int(self.headers.get("content-length") or 0)
                if length:
                    if length > MAX_BODY:
                        return self._json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Body exceeds 1 MB."}})
                    ctype = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
                    allow_form = (
                        ctype == "application/x-www-form-urlencoded"
                        and method == "POST"
                        and bool(re.fullmatch(r"/api/v1/public/funnel/[\w\-]+/submit", path))
                    )
                    if ctype != "application/json" and not allow_form:
                        return self._json(415, {"error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": "Content-Type must be application/json."}})
                    raw = self.rfile.read(length)
                    if allow_form:
                        is_funnel_form = True
                        parsed_form = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
                        body = {k: (vals[-1] if vals else "") for k, vals in parsed_form.items()}
                        c_val = str(body.get("consent") or "").strip().lower()
                        body["consent"] = c_val in ("1", "true", "yes", "on")
                        body["_form_urlencoded"] = True
                    else:
                        try:
                            body = json.loads(raw or b"{}")
                        except Exception:
                            return self._json(400, {"error": {"code": "MALFORMED_JSON", "message": "Body is not valid JSON."}})
                        if not isinstance(body, dict):
                            return self._json(400, {"error": {"code": "VALIDATION_FAILED", "message": "Body must be a JSON object."}})

                status, payload, extra = api.handle(
                    STORE, method, path, query, body, {k.lower(): v for k, v in self.headers.items()},
                    self.client_ip(), self.cookie_token(), raw
                )
                accept_hdr = (self.headers.get("accept") or "").lower()
                if is_funnel_form and "text/html" in accept_hdr and "application/json" not in accept_hdr:
                    msg_txt = (
                        payload.get("message")
                        if status < 400 and isinstance(payload, dict)
                        else ((payload.get("error") or {}).get("message") if isinstance(payload, dict) else "Submission error.")
                    )
                    title_txt = "Enquiry Received" if status < 400 else "Unable to Submit Enquiry"
                    html_resp = (
                        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                        f"<title>{sec.esc(title_txt)}</title>"
                        "<style>body{font:16px/1.55 system-ui,sans-serif;background:#f7f8fa;color:#14181f;margin:0;padding:32px 20px}"
                        "main{max-width:540px;margin:24px auto;background:#fff;border:1px solid #dde1e7;border-radius:14px;padding:24px}"
                        "a{color:#0f6b52;font-weight:600}</style></head><body><main>"
                        f"<h1>{sec.esc(title_txt)}</h1><p>{sec.esc(msg_txt)}</p>"
                        "<p><a href=\"/site\">Return to project listings</a></p></main></body></html>"
                    )
                    return self._html(status, html_resp)
                # Only the two routes that *issue* a session may set the session
                # cookie. The old rule was "any 200 whose body has a `token`",
                # which meant a route that happened to return a token in its
                # payload (a fresh portal ingest key, a webhook echo) silently
                # replaced the caller's session with that value — session
                # fixation by any endpoint that reflects a string back.
                if (
                    status == 200
                    and isinstance(payload, dict)
                    and payload.get("token")
                    # only the routes that *issue* a session may set its cookie
                    # (`docs/28`): a route that happens to echo a token back must
                    # never be able to replace the caller's session
                    and path.endswith(("/auth/login", "/auth/signup", "/auth/login/mfa",
                                       "/auth/refresh", "/auth/sso/callback"))
                ):
                    extra["Set-Cookie"] = [self._session_cookie(payload["token"]),
                                           self._refresh_cookie(api.take_refresh_cookie())]
                if status == 200 and path.endswith("/auth/logout"):
                    extra["Set-Cookie"] = [self._clear_cookie(SESSION_COOKIE, "/"),
                                           self._clear_cookie(sess.REFRESH_COOKIE, REFRESH_PATH)]
                if status == 200 and isinstance(payload, dict) and "_plain_text" in payload:
                    return self._text(200, str(payload["_plain_text"]), extra)
                return self._json(status, payload, extra)

            # public funnel
            if path.startswith("/f/"):
                slug = path[3:].strip("/")
                status, html = api.public_funnel_page(STORE, slug)
                if status == 200:
                    f = STORE.public_funnel_by_slug(slug)
                    if f:
                        _record_funnel_view(STORE, f, self.client_ip())
                return self._html(status, html)

            if path == "/site":
                return self._html(200, self._public_site())

            return self._static(path, head_only)

        except BrokenPipeError:
            return
        except StorageUnavailable:
            # The durable store is down. A 503 with a trace id and no detail is
            # the honest answer; a 500 + traceback in the log is not (SEN-004).
            return self._json(503, {"error": {"code": "STORAGE_UNAVAILABLE",
                                              "message": "Storage is temporarily unavailable. Retry shortly.",
                                              "trace_id": nid("trc")}})
        except Exception:
            # Provider exceptions can contain credentials; never log payloads.
            try:
                self._json(500, {"error": {"code": "INTERNAL", "message": "Server error.", "trace_id": nid("trc")}})
            except Exception:
                pass

    # ---------------- static ----------------
    def _static(self, path: str, head_only: bool) -> None:
        # Imported marketing SPA is isolated below /website; CRM routes stay intact.
        if path in ("/website", "/website/") or (
            path.startswith("/website/") and (
                path.rstrip("/").removeprefix("/website/") in ("privacy-policy", "terms-and-conditions")
                or re.fullmatch(r"/website/projects/[a-z0-9-]+(?:/thank-you)?/?", path)
            )
        ):
            path = "/website/index.html"
        if path in ("/", ""):
            path = "/index.html"
        safe = posixpath.normpath(path).lstrip("/")
        if safe.startswith("..") or "\x00" in safe:
            return self._json(400, {"error": {"code": "BAD_PATH", "message": "Invalid path."}})
        full = os.path.abspath(os.path.join(WEB_ROOT, safe))
        if not full.startswith(os.path.abspath(WEB_ROOT) + os.sep) and full != os.path.abspath(WEB_ROOT):
            return self._json(403, {"error": {"code": "FORBIDDEN", "message": "Path outside web root."}})
        try:
            st = os.stat(full)
        except OSError:
            return self._json(404, {"error": {"code": "NOT_FOUND", "message": "Not found."}})
        if not os.path.isfile(full):
            return self._json(404, {"error": {"code": "NOT_FOUND", "message": "Not found."}})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if full.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        if full.endswith(".svg"):
            ctype = "image/svg+xml"
        with _STATIC_LOCK:
            cached = _STATIC_CACHE.get(full)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            data, etag = cached[2], cached[3]
        else:
            with open(full, "rb") as fh:
                data = fh.read()
            etag = f'"{hashlib.sha256(data).hexdigest()[:24]}"'
            with _STATIC_LOCK:
                _STATIC_CACHE[full] = (st.st_mtime_ns, st.st_size, data, etag)
        cache = (
            "no-cache"
            if full.endswith(("index.html", "sw.js"))
            else "public, max-age=3600, stale-while-revalidate=86400"
        )
        robots = "noindex" if not full.endswith(".html") else "all"
        inm_raw = self.headers.get("if-none-match") or ""
        if inm_raw:
            candidates = [t.strip() for t in inm_raw.split(",") if t.strip()]
            norm = [t[2:].strip() if t.startswith("W/") else t for t in candidates]
            if "*" in norm or etag in norm:
                self._headers(304, ctype, 0, {"ETag": etag, "X-Robots-Tag": robots}, cache=cache)
                return
        self._headers(200, ctype, len(data), {"ETag": etag, "X-Robots-Tag": robots}, cache=cache)
        if not head_only:
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

    # ---------------- public website ----------------
    def _public_site(self) -> str:
        tenant = next((t for t in STORE.db["tenants"] if t.get("slug") == "sunrise"), None)
        if not tenant:
            return "<h1>Site not configured</h1>"
        pages = [p for p in STORE.all("pages", tenant["id"]) if p.get("published")]
        funnels = [f for f in STORE.all("funnels", tenant["id"]) if f.get("published")]
        props = [p for p in STORE.all("properties", tenant["id"]) if p.get("status") == "available"][:6]
        cards = "".join(
            f"""<article class="card"><h3>{sec.esc(p['title'])}</h3>
            <p class="meta">{sec.esc(p['type'])} · {sec.esc(p['locality'])}, {sec.esc(p['city'])} · {p['size_sqft']} sqft</p>
            <p class="price">₹{p['price']/100000:.1f} L</p>
            <p class="meta">RERA: {sec.esc(p.get('rera') or '—')} · {len(p.get('docs', []))} documents on file</p></article>"""
            for p in props
        )
        fun = "".join(f'<a class="cta-link" href="/f/{sec.esc(f["slug"])}">{sec.esc(f["name"])} →</a>' for f in funnels)
        body = "".join(f"<section><h2>{sec.esc(p['title'])}</h2><p>{sec.esc(p['body'])}</p></section>" for p in pages)
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{sec.esc(tenant['name'])} — clear-title land & plotted layouts</title>
<meta name="description" content="RERA-registered plots, villas and land in Maharashtra. Documents before the price sheet.">
<style>
:root{{--ink:#0e1420;--muted:#5a6472;--brand:#0f6b52;--line:#e3e7ec;--bg:#f7f8fa}}
*{{box-sizing:border-box}}body{{margin:0;font:16px/1.6 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:var(--ink);background:var(--bg)}}
header{{background:#fff;border-bottom:1px solid var(--line);padding:14px 22px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0}}
.brand{{font-weight:800;letter-spacing:-.02em}}.nav a{{color:var(--muted);text-decoration:none;margin-left:18px;font-size:14px}}
.hero{{padding:56px 22px 32px;max-width:1080px;margin:0 auto}}
.hero h1{{font-size:clamp(30px,5vw,52px);line-height:1.06;letter-spacing:-.03em;margin:0 0 14px;max-width:16ch}}
.hero p{{color:var(--muted);font-size:19px;max-width:52ch}}
.btn{{display:inline-block;background:var(--brand);color:#fff;padding:13px 20px;border-radius:11px;text-decoration:none;font-weight:650;margin-top:10px}}
main{{max-width:1080px;margin:0 auto;padding:0 22px 80px}}
.grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));margin:18px 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px}}
.card h3{{margin:0 0 4px;font-size:16px}}.meta{{color:var(--muted);font-size:13px;margin:2px 0}}
.price{{font-weight:750;font-size:20px;margin:8px 0}}
section{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0}}
h2{{font-size:20px;margin:0 0 8px;letter-spacing:-.01em}}
.cta-link{{display:block;background:#e7f2ee;color:#0f4a38;padding:14px;border-radius:12px;text-decoration:none;font-weight:650;margin:10px 0}}
footer{{border-top:1px solid var(--line);padding:22px;color:var(--muted);font-size:13px;text-align:center;background:#fff}}
</style></head><body>
<header><div class="brand">{sec.esc(tenant['name'])}</div>
<nav class="nav"><a href="/site">Home</a><a href="#projects">Projects</a><a href="#about">About</a><a href="/f/nanded-plots">Book a visit</a></nav></header>
<div class="hero"><h1>Clear-title land, documents before the price sheet.</h1>
<p>RERA-registered plotted layouts, villas and land parcels across Maharashtra. Every enquiry gets the 7/12, NA order, sanctioned layout and encumbrance status before you spend a rupee on travel.</p>
<a class="btn" href="/f/nanded-plots">Book a free site visit</a></div>
<main><h2 style="font-size:20px;margin:18px 0 8px">Available RERA-Registered Projects</h2><div id="projects" class="grid">{cards}</div>
{fun}
<div id="about">{body}</div>
</main>
<footer>© 2027 {sec.esc(tenant['name'])} · MahaRERA registered · Consent-first contact (DPDP 2023) · No spam, opt out anytime</footer>
</body></html>"""

    # ---------------- SSE ----------------
    @staticmethod
    def _tenant_sse_summary(tid: str, can_view_audit: bool) -> dict:
        now_m = time.monotonic()
        with _SSE_SUMMARY_LOCK:
            cached = _SSE_SUMMARY_CACHE.get(tid)
        if cached and (now_m - cached[0]) < 1.0:
            base = cached[1]
        else:
            pending = sum(1 for p in STORE.all("proposals", tid) if p.get("status") == "pending")
            agents_active = sum(1 for a in STORE.all("agents", tid) if a.get("status") == "active")
            audit_rows = sorted(STORE.all("audit", tid), key=lambda r: -r["at"])[:3]
            base = {
                "pending_approvals": pending,
                "agents_active": agents_active,
                "_audit": [{"action": a_["action"], "actor": a_["actor"], "at": a_["at"]} for a_ in audit_rows],
            }
            with _SSE_SUMMARY_LOCK:
                _SSE_SUMMARY_CACHE[tid] = (now_m, base)
        return {
            "pending_approvals": base["pending_approvals"],
            "agents_active": base["agents_active"],
            "activity": base["_audit"] if can_view_audit else [],
        }

    def _sse(self, query: dict | None = None) -> None:
        query = query or {}
        token = None
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif self.cookie_token():
            token = self.cookie_token()
        mode = neon.auth_mode()
        neon_resolved = None
        claims = sec.verify_session_token(token or "") if mode in ("local", "hybrid") else None
        if not claims and mode in ("hybrid", "neon"):
            neon_resolved = api.resolve_neon_auth_identity(STORE, token or "")
            if neon_resolved:
                claims = neon_resolved[0]
        if not claims:
            return self._json(401, {"error": {"code": "UNAUTHENTICATED", "message": "SSE requires a session."}})
        tid = str(claims.get("tid") or "")
        role = str(claims.get("role") or "")
        if role == "channel_partner":
            return self._json(403, {"error": {"code": "FORBIDDEN_SCOPE",
                                              "message": "Partner accounts may only use the partner portal."}})
        sid = str(claims.get("sid") or "")
        if neon_resolved is None and (not sid or not sess.active(STORE, sid, tenant_id=tid)):
            return self._json(401, {"error": {"code": "SESSION_ENDED",
                                              "message": "This session has ended. Sign in again."}})
        user = neon_resolved[1] if neon_resolved else next(
            (u for u in STORE.db["users"] if u["id"] == claims.get("uid") and u["tenant_id"] == tid),
            None,
        )
        if user is not None and not user.get("active", True):
            return self._json(403, {"error": {"code": "ACCOUNT_DISABLED", "message": "Account deactivated."}})

        def _qint(name: str, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int((query.get(name) or [str(default)])[0])))
            except Exception:
                return default

        raw_since = self.headers.get("last-event-id") or (query.get("since") or ["0"])[0]
        try:
            since_id = max(0, int(raw_since))
        except Exception:
            since_id = 0
        max_events = _qint("max_events", 0, 0, 200)
        timeout_sec = _qint("timeout_ms", 120_000, 100, 120_000) / 1000.0
        heartbeat_sec = _qint("heartbeat_ms", 4_000, 200, 15_000) / 1000.0
        can_view_audit = role in ("owner", "admin")

        # Under HTTP/1.1 (`protocol_version = "HTTP/1.1"`), an unchunked stream
        # with no `Content-Length` is delimited by connection close: keeping the
        # socket open after `_sse()` returns wedges the handler in `handle_one_request`
        # while the client waits for EOF.
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        frame_anc = _frame_ancestors_directive("/api/v1/events")
        for k, v in sec.SECURITY_HEADERS.items():
            if k == "Cache-Control":
                continue
            if frame_anc:
                if k in ("X-Frame-Options", "Cross-Origin-Opener-Policy",
                         "Cross-Origin-Embedder-Policy", "Cross-Origin-Resource-Policy"):
                    continue
                if k == "Content-Security-Policy":
                    v = v.replace("frame-ancestors 'none'", f"frame-ancestors {frame_anc}")
            self.send_header(k, v)
        self.end_headers()

        deadline = time.monotonic() + timeout_sec
        delivered = 0
        try:
            with rt.HUB.subscribe(tid, since_id=since_id) as sub:
                # Subscribe BEFORE flushing the initial snapshot so callers have
                # a race-free readiness barrier the moment the first frame lands.
                snap = {
                    "type": "snapshot",
                    "t": int(time.time()),
                    **self._tenant_sse_summary(tid, can_view_audit),
                }
                self.wfile.write(f"data: {json.dumps(snap, separators=(',', ':'))}\n\n".encode())
                self.wfile.flush()

                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    ev = sub.get(timeout=min(heartbeat_sec, remaining))
                    # Re-check revocation on every wake so a killed session ends
                    # its open stream instead of coasting for two minutes.
                    if neon_resolved is not None:
                        if sec.is_revoked(token or "") or not api.resolve_neon_auth_identity(STORE, token or ""):
                            break
                    elif not sec.verify_session_token(token or "") or not sess.active(STORE, sid, tenant_id=tid):
                        break
                    if ev is None:
                        hb = {
                            "type": "heartbeat",
                            "t": int(time.time()),
                            **self._tenant_sse_summary(tid, can_view_audit),
                        }
                        self.wfile.write(f"data: {json.dumps(hb, separators=(',', ':'))}\n\n".encode())
                        self.wfile.flush()
                        continue
                    if ev.get("tid") != tid:
                        continue
                    if ev.get("type") == "learning.updated":
                        with _SSE_SUMMARY_LOCK:
                            _SSE_SUMMARY_CACHE.pop(tid, None)
                    frame = {
                        "id": ev["id"],
                        "type": ev.get("type", "event"),
                        "target": ev.get("target", ""),
                        "meta": ev.get("meta") or {},
                        "t": int(ev.get("ts") or time.time()),
                        "ts_ms": round(float(ev.get("ts") or time.time()) * 1000.0, 2),
                        **self._tenant_sse_summary(tid, can_view_audit),
                    }
                    eid = int(ev["id"])
                    self.wfile.write(
                        f"id: {eid}\ndata: {json.dumps(frame, separators=(',', ':'))}\n\n".encode()
                    )
                    self.wfile.flush()
                    delivered += 1
                    if max_events and delivered >= max_events:
                        break
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def start_automation_worker(interval: int | None = None) -> None:
    """Tick every tenant's automations on an interval.

    Without this the runtime is a library nobody calls: the executor exists, the
    playbooks exist, and the 90-second promise still depends on a human. With it,
    a lead that lands in `contacts` is answered whether or not anyone has the
    console open.

    Safe to run in more than one process: every run carries an idempotency key and
    is refused if it already exists, so two workers converge on one run instead of
    messaging the buyer twice. `RE_AUTOMATION_TICK=0` disables the worker (the test
    suite does this so counts stay deterministic); `POST /automations/tick` still
    works by hand.
    """
    raw = os.environ.get("RE_AUTOMATION_TICK", str(autm.DEFAULT_TICK_SECONDS))
    if raw.strip().lower() in ("0", "off", "false", "no", ""):
        print("[automations] worker disabled (RE_AUTOMATION_TICK=0); ticks are manual")
        return
    try:
        seconds = max(2, min(3600, int(raw)))
    except ValueError:
        seconds = autm.DEFAULT_TICK_SECONDS

    def loop() -> None:
        # The worker gets its **own** Store, and therefore its own Postgres
        # connection. Sharing the request threads' connection made one session
        # serve two transactions: a tick's `set_config(app.tenant_id)` could land
        # inside a login's statement and RLS would refuse the audit row for
        # belonging to another workspace (observed, not theoretical). A session per
        # thread is also why `Runtime` re-reads the rows it decides on.
        worker_store = store_mod.Store()
        runtime = autm.Runtime(worker_store)
        while True:
            try:
                for tenant in list(worker_store.db["tenants"]):
                    runtime.tick(tenant["id"])
            except Exception as exc:  # a tick must never kill the server
                print(f"[automations] tick error: {type(exc).__name__}: {exc}")
            time.sleep(seconds)

    threading.Thread(target=loop, name="automation-worker", daemon=True).start()
    print(f"[automations] worker every {seconds}s · "
          f"{len(autm.TRIGGERS)} triggers · {len(autm.PLAYBOOKS)} playbooks")


class _HTTPServer(ThreadingHTTPServer):
    # Stdlib `TCPServer.request_queue_size` defaults to 5 (`listen(5)`): a post-deploy
    # reconnect storm of 50–100 SSE inboxes overflows the SYN backlog immediately.
    request_queue_size = 256
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        max_conc = max(8, int(os.environ.get("RE_MAX_CONCURRENT_REQUESTS", "128") or 128))
        self.max_concurrent_requests = max_conc
        self._req_sem = threading.BoundedSemaphore(max_conc)
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)

    def process_request(self, request, client_address) -> None:
        if not self._req_sem.acquire(blocking=False):
            try:
                body = b'{"error":{"code":"SERVICE_BUSY","message":"Server concurrency ceiling reached; retry shortly."}}'
                resp = (
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: application/json; charset=utf-8\r\n"
                    b"Retry-After: 2\r\n"
                    b"Connection: close\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
                request.sendall(resp)
            except Exception:
                pass
            finally:
                self.shutdown_request(request)
            return
        t = threading.Thread(target=self._bounded_request_thread, args=(request, client_address), daemon=True)
        t.start()

    def _bounded_request_thread(self, request, client_address) -> None:
        try:
            self.process_request_thread(request, client_address)
        finally:
            self._req_sem.release()


def serve(port: int, host: str) -> None:
    httpd = _HTTPServer((host, port), Handler)
    print(f"Realtor Estates PWA → http://{host}:{port}  (demo login: owner@realtorestates.app / Demo@12345!)")
    start_automation_worker()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.server_close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    args = ap.parse_args()
    serve(args.port, args.host)

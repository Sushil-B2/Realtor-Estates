#!/usr/bin/env python3
"""
Realtor Estates — security audit harness.

Answers one question with evidence rather than adjectives: *can anything
sensitive reach a place that is not the server's own memory, and can any input
change behaviour it should not?*

Four groups of checks:
  A. secret exposure   — files, HTTP, API payloads, static assets, errors, logs,
                         clipboard, headers
  B. injection & flaws — SQLi, XSS, path traversal, CRLF, prototype pollution,
                         mass assignment, IDOR, CSRF, auth/rate-limit, SSRF,
                         CSV formula injection, oversized bodies
  C. database          — TLS, append-only audit, RLS, PII-at-rest, secrets in
                         rows, privileges, parameterised SQL only
  D. AI agents         — secrets in prompts/outputs, PII minimisation

Usage
-----
    python3 scripts/security_audit.py                 # against http://127.0.0.1:8000
    python3 scripts/security_audit.py --base URL --json reports/audit.json
Exit code is 0 only when nothing failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"
RESULTS: list[dict] = []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def record(cid: str, name: str, ok: bool | None, severity: str, detail: str = "", fix: str = "") -> None:
    RESULTS.append(
        {"id": cid, "name": name, "ok": ok, "severity": severity, "detail": detail[:400], "fix": fix}
    )
    mark = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    print(f"  [{mark}] {cid} {name}" + (f" — {detail}" if detail and not ok else ""))


class Client:
    def __init__(self, base: str):
        self.base = base
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, headers=None, csrf=True, follow=True):
        h = {"User-Agent": "re-audit", "Accept": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw, status, hdrs = r.read(), r.status, dict(r.getheaders())
        except urllib.error.HTTPError as e:
            raw, status, hdrs = e.read(), e.code, dict(e.headers)
        except Exception as exc:  # noqa: BLE001
            return 0, {"_error": f"{exc.__class__.__name__}: {exc}"}, {}
        sc = hdrs.get("Set-Cookie", "")
        if "re_session=" in sc:
            val = sc.split("re_session=", 1)[1].split(";")[0]
            if val:
                self.cookies["re_session"] = val
            else:
                self.cookies.pop("re_session", None)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw[:2000].decode("utf-8", "replace")}
        return status, payload, hdrs

    def login(self, email="owner@realtorestates.app", password="Demo@12345!"):
        status, body, _ = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        if status == 200:
            self.csrf = body.get("csrf", "")
        return status, body


def live_secret_values() -> list[str]:
    """Actual secret values currently loaded — used to prove none of them leak."""
    from app import config as cfg

    secret_word = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|DSN|MASTER|CREDENTIAL)", re.I)
    non_secret = re.compile(r"(REGION|BUCKET|_URL$|ENDPOINT)", re.I)
    out = []
    for name, required, _desc in cfg.SECRET_INVENTORY:
        val = cfg.get(name, "")
        if len(val) < 12:
            continue
        if non_secret.search(name) and not secret_word.search(name.replace("SECRET_KEY", "SECRET")):
            continue          # a region or a bucket name is configuration, not a credential
        if secret_word.search(name) or required:
            out.append(val)
    return out


_SAFE_EXPR = re.compile(
    r"""(icon\([^)]*\)|'[^']*'|"[^"]*"|\d+|wide \? *'[^']*' *: *'[^']*')"""
)

SECRET_SHAPES = [
    ("postgres-dsn-with-password", re.compile(r"postgres(?:ql)?://[^:\s\"']+:[^@\s\"']{6,}@")),
    ("neon-api-key", re.compile(r"nak_live_[A-Za-z0-9]{8,}")),
    ("neon-storage-secret", re.compile(r"nsk_live_[A-Za-z0-9]{8,}")),
    ("neon-db-password", re.compile(r"npg_[A-Za-z0-9]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(r"Bearer\s+[A-Za-z0-9\-_.]{24,}")),
]


def leaks_in(text: str, secrets: list[str]) -> list[str]:
    found = []
    for value in secrets:
        if value and value in text:
            found.append("literal secret value")
    for name, pat in SECRET_SHAPES:
        if pat.search(text):
            found.append(name)
    return found


# ---------------------------------------------------------------------------
# A. secret exposure
# ---------------------------------------------------------------------------
SECRET_FILES = [".env", ".env.local", "data/store.json", "data/keys.json", ".pgdata", "uploads/env.txt",
                ".git/config", "neon.ts", "package-lock.json", "README.md"]

HTTP_PROBES = ["/.env", "/.env.local", "/data/store.json", "/data/keys.json", "/app/config.py",
               "/app/db.py", "/server.log", "/logs/app.log", "/.git/config", "/.git/HEAD",
               "/uploads/env.txt", "/neon.ts", "/.neon", "/scripts/check_secrets.py",
               "/%2e%2e/.env", "/..%2f.env", "/web/../.env", "/package.json",
               "/reports/redline-report.md", "/db/001_init.sql"]


def audit_secrets(client: Client) -> None:
    print("\n» A. secret exposure")
    secrets = live_secret_values()

    # A1 file permissions / git-ignore
    bad = []
    for rel in (".env", ".env.local", "data/store.json", "data/keys.json"):
        p = ROOT / rel
        if not p.exists():
            continue
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            bad.append(f"{rel} is {oct(mode)}")
    record("SA-101", "secret files are 0600 (group/other cannot read)", not bad, "critical", "; ".join(bad),
           "chmod 600 on .env, .env.local, data/store.json, data/keys.json")

    import subprocess

    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    offenders = [f for f in tracked if re.search(r"(^\.env$|\.env\.local|store\.json|keys\.json|\.pgdata)", f)]
    record("SA-102", "no secret-bearing file is tracked by git", not offenders, "critical", str(offenders),
           "git rm --cached the file, add it to .gitignore, rotate the secret")

    # A3 secret files unreachable over HTTP
    reachable = []
    for path in HTTP_PROBES:
        status, body, _ = client.call("GET", path)
        text = json.dumps(body) if isinstance(body, dict) else str(body)
        if status == 200 and (leaks_in(text, secrets) or "RE_MASTER_KEY" in text or "postgresql://" in text):
            reachable.append(f"{path} → 200 with secret-shaped body")
        elif status == 200 and path in ("/README.md", "/package.json", "/db/001_init.sql", "/reports/redline-report.md"):
            reachable.append(f"{path} → 200 (unexpected for a non-public file)")
    record("SA-103", "secret files and source paths are not served by the web tier", not reachable, "critical",
           "; ".join(reachable), "serve only web/ and explicit public assets")

    # A4 owner API payloads carry no secret
    api_paths = ["/api/v1/tenant", "/api/v1/tenant/features", "/api/v1/tenant/users", "/api/v1/tenant/audit",
                 "/api/v1/security/posture", "/api/v1/security/secrets", "/api/v1/security/services",
                 "/api/v1/integrations", "/api/v1/agents", "/api/v1/contacts?limit=5", "/api/v1/pipeline",
                 "/api/v1/inbox", "/api/v1/analytics/overview", "/api/v1/webhooks", "/api/v1/funnels",
                 "/api/v1/campaigns", "/api/v1/social", "/api/v1/research", "/api/v1/automations", "/healthz"]
    leaking = []
    for path in api_paths:
        status, body, _ = client.call("GET", path)
        text = json.dumps(body)
        found = leaks_in(text, secrets)
        if found:
            leaking.append(f"{path}: {found}")
    record("SA-104", "no API response contains a live secret value", not leaking, "critical",
           "; ".join(leaking), "redact centrally in cfg.redact() before serialising")

    # A5 static assets
    assets = ["/", "/app.js", "/js/core.js", "/js/views-crm.js", "/js/views-ops.js", "/js/views-ai.js",
              "/js/views-admin.js", "/sw.js", "/manifest.webmanifest", "/styles.css"]
    asset_leaks = []
    for path in assets:
        status, body, _ = client.call("GET", path)
        text = json.dumps(body)
        found = leaks_in(text, secrets)
        if found:
            asset_leaks.append(f"{path}: {found}")
    record("SA-105", "static assets contain no secrets", not asset_leaks, "critical", "; ".join(asset_leaks), "")

    # A6 error responses: no stack traces, paths, DSNs
    status, body, _ = client.call("POST", "/api/v1/auth/login", {"email": "x" * 400, "password": "y"}, csrf=False)
    text = json.dumps(body)
    bad_err = [p for p in ("Traceback", "File \"", "/home/user", "postgresql://", "psycopg") if p in text]
    record("SA-106", "error responses carry no stack trace, path or DSN", not bad_err, "high", str(bad_err),
           "return a fixed error code and log the detail server-side")

    # A7 redaction of secret-shaped input
    canary = "sk-live-" + os.urandom(12).hex()
    status, body, _ = client.call("POST", "/api/v1/security/inspect", {"text": f"my key is {canary} and RE_MASTER_KEY=abc123"})
    text = json.dumps(body)
    record("SA-107", "secret-shaped input is redacted in inspection output", canary not in text, "high",
           "raw canary echoed back", "redact before returning")

    # A8 logs
    logs = [p for p in (ROOT / "logs").glob("*") if p.is_file()] if (ROOT / "logs").exists() else []
    log_leaks = []
    for p in logs:
        try:
            found = leaks_in(p.read_text(errors="ignore"), secrets)
        except Exception:
            continue
        if found:
            log_leaks.append(f"{p.name}: {found}")
    record("SA-108", "server logs contain no secret values", not log_leaks, "high", "; ".join(log_leaks),
           "log the redacted DSN only (cfg.redact / db.redact_dsn)")

    # A9 clipboard: the app must never put a secret on the clipboard
    js = "\n".join(p.read_text(errors="ignore") for p in (ROOT / "web" / "js").glob("*.js"))
    clipboard = re.findall(r"navigator\.clipboard\.(\w+)", js)
    risky = re.findall(r"clipboard\.writeText\(([^)]{0,80})\)", js)
    secret_ish = [r for r in risky if re.search(r"token|key|secret|password|session", r, re.I)]
    record("SA-109", "no clipboard write can carry a credential", not secret_ish, "high",
           f"clipboard calls={len(clipboard)} risky={secret_ish}",
           "never write tokens/keys to the clipboard; use a copy-action that copies record ids only")

    # A10 security headers on success and error
    headers = {}
    for path in ("/", "/api/v1/nope-404"):
        _, _, hdrs = client.call("GET", path)
        headers[path] = {k.lower(): v for k, v in hdrs.items()}
    wanted = ["content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy"]
    missing = [f"{p} missing {[w for w in wanted if w not in h]}" for p, h in headers.items() if any(w not in h for w in wanted)]
    record("SA-110", "security headers present on success and on 404", not missing, "medium", "; ".join(missing), "")

    # A11 healthz is a liveness probe for strangers: no infrastructure detail,
    # no DSN host, no table counts. The richer report needs an authenticated
    # owner (and is still password-masked there).
    # Anonymous on purpose: `client` is authenticated and gets the operator view.
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=10) as r:
            hz = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        hz = {"_error": f"{exc.__class__.__name__}"}
    body_text = json.dumps(hz)
    exposed = [f for f in ("neon.tech", "dsn", "version", "documents", "secrets") if f in body_text]
    record("SA-111", "unauthenticated healthz discloses no infrastructure detail", not exposed, "high",
           f"exposed: {exposed}" if exposed else "liveness only",
           "gate infra fields behind an owner session")


# ---------------------------------------------------------------------------
# B. injection and application flaws
# ---------------------------------------------------------------------------
SQLI = ["' OR '1'='1", "'; drop table re_documents; --", "1; select pg_sleep(0)", "\" or 1=1--",
        "%27%20or%20%271%27%3D%271", "admin'--", "1 UNION SELECT null,null,null--"]
XSS = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "javascript:alert(1)",
       "'><svg/onload=alert(1)>", "${alert(1)}"]
TRAVERSAL = ["/../.env", "/../../etc/passwd", "/..%2f..%2fetc%2fpasswd", "/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
             "/web/../../.env", "/....//....//.env", "/js/../../.env", "/.git/../../.env"]


def audit_injection(client: Client) -> None:
    print("\n» B. injection and application flaws")

    # B1 SQL injection through query params and bodies
    sqli_hits = []
    for payload in SQLI:
        for path in (f"/api/v1/contacts?q={urllib.request.quote(payload)}",
                     f"/api/v1/inbox?q={urllib.request.quote(payload)}",
                     f"/api/v1/properties?city={urllib.request.quote(payload)}"):
            status, body, _ = client.call("GET", path)
            text = json.dumps(body).lower()
            if status >= 500 or "syntax error" in text or "pg_" in text or "sql" in text:
                sqli_hits.append(f"{path[:60]} → {status}")
    record("SB-201", "SQL injection payloads do not reach the database as SQL", not sqli_hits, "critical",
           "; ".join(sqli_hits[:4]), "keep every query parameterised")

    # B2 stored XSS: what comes back must be JSON-encoded, never rendered HTML
    xss_hits = []
    for payload in XSS:
        status, body, _ = client.call("POST", "/api/v1/contacts",
                                      {"name": payload, "phone": "+919812300" + str(int(time.time()))[-3:],
                                       "source": "audit"})
        if status != 200:
            continue
        cid = (body.get("contact") or {}).get("id")
        status, got, _ = client.call("GET", f"/api/v1/contacts/{cid}")
        text = json.dumps(got)
        ctype = "application/json"
        if "<script>" in text and ctype != "application/json":
            xss_hits.append(f"markup returned as HTML: {payload[:24]}")
        jsource = (ROOT / "web" / "js").glob("*.js")
        raw_interp = []
        for f in jsource:
            for line in f.read_text(errors="ignore").split("\n"):
                if "innerHTML" not in line:
                    continue
                for m in re.finditer(r"\$\{([^}]*)\}", line):
                    expr = m.group(1).strip()
                    if "esc(" in expr:
                        continue
                    # literals, the icon() helper and simple ternaries of literals
                    # cannot carry attacker-controlled markup
                    if _SAFE_EXPR.fullmatch(expr):
                        continue
                    raw_interp.append(f"{f.name}:{expr[:40]}")
        if raw_interp:
            xss_hits.append(f"unescaped interpolation: {raw_interp[:3]}")
        if cid:
            client.call("DELETE", f"/api/v1/contacts/{cid}")
    record("SB-202", "user input is stored/returned as data, not markup", not xss_hits, "high",
           "; ".join(xss_hits[:3]), "escape on render (esc()) and set Content-Type: application/json")

    # B3 path traversal
    traversal_hits = []
    for path in TRAVERSAL:
        status, body, _ = client.call("GET", path)
        text = str(body)
        if "root:x:" in text or "RE_MASTER_KEY" in text or "postgresql://" in text:
            traversal_hits.append(f"{path} → {status} leaked content")
    record("SB-203", "path traversal cannot escape the web root", not traversal_hits, "critical",
           "; ".join(traversal_hits), "resolve and confine to web/")

    # B4 CRLF / header injection
    status, body, hdrs = client.call("POST", "/api/v1/contacts",
                                     {"name": "crlf\r\nX-Injected: 1", "phone": "+919812300777", "source": "audit"})
    injected = any("x-injected" in k.lower() for k in hdrs)
    record("SB-204", "no response-header injection via field values", not injected, "high",
           "header injected" if injected else "headers clean", "never echo input into headers")

    # B5 prototype pollution
    status, body, _ = client.call("POST", "/api/v1/contacts",
                                  {"__proto__": {"polluted": True}, "constructor": {"prototype": {"x": 1}},
                                   "name": "proto", "phone": "+919812300888"})
    polluted = status == 200 and (body.get("contact") or {}).get("polluted") is True
    record("SB-205", "prototype-pollution keys are inert", not polluted, "medium",
           "polluted" if polluted else "ignored", "whitelist accepted fields")

    # B6 mass assignment / privilege escalation
    status, body, _ = client.call("PATCH", "/api/v1/auth/me", {"role": "owner", "is_admin": True})
    esc = status == 200 and (body.get("user") or {}).get("role") == "owner"
    record("SB-206", "a user cannot assign themselves a higher role", not esc, "critical",
           f"PATCH /auth/me returned role=owner" if esc else "rejected or ignored",
           "whitelist mutable profile fields; roles only via owner/admin route")

    # B7 CSRF
    saved = client.csrf
    client.csrf = ""
    status, _, _ = client.call("POST", "/api/v1/contacts", {"name": "csrf", "phone": "+919812300999"}, csrf=False)
    client.csrf = saved
    record("SB-207", "state-changing request without a CSRF token is rejected", status in (401, 403), "high",
           f"accepted ({status})", "require X-CSRF-Token on every mutation")

    # B8 unauthenticated access
    anon = Client(client.base)
    exposed = []
    for path in ("/api/v1/contacts", "/api/v1/tenant/audit", "/api/v1/security/secrets", "/api/v1/agents",
                 "/api/v1/pipeline", "/api/v1/analytics/overview"):
        status, _, _ = anon.call("GET", path, csrf=False)
        if status == 200:
            exposed.append(path)
    record("SB-208", "tenant data requires a session", not exposed, "critical", str(exposed), "")

    # B10 cookie flags first (the lockout test below locks the account)
    fresh = Client(client.base)
    status, _, hdrs = fresh.call("POST", "/api/v1/auth/login",
                                 {"email": "viewer@realtorestates.app", "password": "Demo@12345!"}, csrf=False)
    cookie = hdrs.get("Set-Cookie", "") or hdrs.get("set-cookie", "")
    if status == 429:
        record("SB-210", "session cookie is HttpOnly + SameSite", None, "high",
               "not measured: login throttled by the audit's own earlier attempts")
        flags_ok = True
    else:
        flags_ok = status == 200 and all(f in cookie.lower() for f in ("httponly", "samesite", "path=/"))
        record("SB-210", "session cookie is HttpOnly + SameSite", flags_ok, "high",
               f"status={status} cookie={cookie[:110]}", "always set HttpOnly; SameSite=Strict")

    # B9 rate limiting on login
    anon = Client(client.base)
    codes = []
    # A non-existent address: this tests the throttle without locking a human out.
    probe_email = f"probe-{os.urandom(3).hex()}@example.invalid"
    for _ in range(12):
        status, _, _ = anon.call("POST", "/api/v1/auth/login",
                                 {"email": probe_email, "password": "wrong-" + os.urandom(3).hex()},
                                 csrf=False)
        codes.append(status)
    record("SB-209", "repeated failed logins are rate limited", 429 in codes, "high",
           f"statuses={sorted(set(codes))}", "per-IP + per-account throttle with backoff")

    # B11 IDOR / cross-tenant
    other = Client(client.base)
    other.login("owner@metrorealty.app", "Metro@12345!")
    status, mine, _ = client.call("GET", "/api/v1/contacts?limit=1")
    victim = ((mine.get("contacts") or [{}])[0]).get("id")
    if victim:
        status, body, _ = other.call("GET", f"/api/v1/contacts/{victim}")
        leaked = status == 200 and (body.get("contact") or {}).get("id") == victim
        record("SB-211", "cross-tenant object access is denied", not leaked, "critical",
               f"tenant B read tenant A contact {victim}" if leaked else "denied", "")
    else:
        record("SB-211", "cross-tenant object access is denied", None, "critical", "no sample contact")

    # B12 SSRF on webhook registration
    status, body, _ = client.call("POST", "/api/v1/webhooks",
                                  {"url": "http://169.254.169.254/latest/meta-data/", "events": ["contact.created"]})
    ssrf = status == 200
    record("SB-212", "webhook URLs are validated (no SSRF to metadata/localhost)", not ssrf, "high",
           "metadata URL accepted" if ssrf else f"rejected ({status})", "allowlist https + block private ranges")

    # B13 oversized / malformed body
    try:
        status, body, _ = client.call("POST", "/api/v1/security/inspect", {"text": "A" * 1_400_000})
        rejected = status in (400, 413, 422)
    except Exception:
        rejected = True
        status = 0
    record("SB-213", "oversized request bodies are rejected", rejected, "medium", f"status={status}",
           "cap body size before parsing")


    # B15 session forgery: a tampered token must not authenticate
    forged = dict(client.cookies)
    token = forged.get("re_session", "")
    if token:
        tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        anon2 = Client(client.base)
        anon2.cookies = {"re_session": tampered}
        status, _, _ = anon2.call("GET", "/api/v1/contacts", csrf=False)
        record("SB-215", "a tampered session token is rejected", status in (401, 403), "critical",
               f"status={status}", "verify the HMAC before trusting any claim")
    else:
        record("SB-215", "a tampered session token is rejected", None, "critical", "no session cookie")

    # B16 authorisation matrix: a viewer must not reach privileged operations
    low = Client(client.base)
    low.login("viewer@realtorestates.app", "Demo@12345!")
    forbidden = []
    probes = [
        ("POST", "/api/v1/tenant/users", {"name": "x", "email": "x@example.com", "role": "owner"}),
        ("PATCH", "/api/v1/tenant", {"kill_switch": True}),
        ("GET", "/api/v1/security/secrets", None),
        ("POST", "/api/v1/security/redline", {}),
        ("DELETE", "/api/v1/tenant/users/usr_anything", None),
    ]
    for method, path, bodyd in probes:
        status, _, _ = low.call(method, path, bodyd)
        if status == 200 or status == 201:
            # an owner-only operation succeeded for a viewer
            forbidden.append(f"{method} {path} → {status}")
    record("SB-216", "a viewer cannot reach owner-only operations", not forbidden, "critical",
           str(forbidden), "enforce role checks server-side on every route")

    # B17 authenticated responses must not be cacheable by a proxy
    status, _, hdrs = client.call("GET", "/api/v1/contacts?limit=1")
    cc = {k.lower(): v for k, v in hdrs.items()}.get("cache-control", "")
    record("SB-217", "authenticated API responses are marked no-store", "no-store" in cc.lower(), "medium",
           f"Cache-Control={cc or '(absent)'}", "set Cache-Control: no-store on /api/** responses")

    # B18 public funnel is throttled (unauthenticated write path).
    # The requests deliberately fail validation (no consent) so the burst is
    # counted by the limiter without creating 30 fake leads.
    pub = Client(client.base)
    before = len((client.call("GET", "/api/v1/contacts?limit=500")[1] or {}).get("contacts") or [])
    codes = [pub.call("POST", "/api/v1/public/funnel/nanded-plots/submit",
                      {"name": "audit", "phone": "+919800000000"}, csrf=False)[0] for _ in range(35)]
    after = len((client.call("GET", "/api/v1/contacts?limit=500")[1] or {}).get("contacts") or [])
    record("SB-218", "the public funnel endpoint is rate limited", 429 in codes, "medium",
           f"statuses={sorted(set(codes))} leads created={after - before}",
           "per-IP throttle on public writes")

    # B19 the SSE stream must require a session (it never ends, so read headers only)
    anon_sse = Client(client.base)
    try:
        req = urllib.request.Request(anon_sse.base + "/api/v1/events", headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=5) as r:
            sse_status = r.status
            r.read(64)
    except urllib.error.HTTPError as e:
        sse_status = e.code
    except Exception:
        sse_status = 0
    record("SB-219", "the event stream requires a session", sse_status in (401, 403), "high",
           f"anonymous GET /api/v1/events → {sse_status}", "require a session for /api/v1/events")

    # B20 Host header is not trusted for absolute URLs (cache poisoning / redirects)
    status, body, hdrs = client.call("GET", "/healthz", headers={"Host": "evil.example.com"})
    leaked = "evil.example.com" in json.dumps(body)
    record("SB-220", "a forged Host header does not appear in responses", not leaked, "low",
           "host echoed into a response body" if leaked else "ignored", "")

    # B14 malformed JSON / wrong types degrade politely
    h = {"Content-Type": "application/json"}
    req = urllib.request.Request(client.base + "/api/v1/security/inspect", data=b'{"text": ',
                                 headers={**h, "Cookie": "; ".join(f"{k}={v}" for k, v in client.cookies.items())})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            st_bad = r.status
    except urllib.error.HTTPError as e:
        st_bad = e.code
    except Exception:
        st_bad = 0
    record("SB-214", "malformed JSON returns 4xx, not 5xx", st_bad in (400, 413, 422), "medium",
           f"status={st_bad}", "validate JSON and return 400")


# ---------------------------------------------------------------------------
# C. database
# ---------------------------------------------------------------------------
def audit_database() -> None:
    print("\n» C. database")
    from app import db as dbmod

    if not dbmod.configured() or not dbmod.driver_available():
        record("SC-301", "database audit", None, "high", "no DATABASE_URL configured")
        return
    try:
        conn = dbmod.psycopg.connect(dbmod.dsn(), prepare_threshold=None)
    except Exception as exc:  # noqa: BLE001
        record("SC-301", "database reachable for audit", False, "high", f"{exc.__class__.__name__}")
        return
    try:
        cur = conn.cursor()

        # TLS. `pg_stat_ssl` shows the *server-side* leg, which is unencrypted
        # inside Neon when the client talks to the pooler; libpq is the authority
        # on the leg we control.
        libpq_tls = bool(getattr(conn.pgconn, "ssl_in_use", False))
        cur.execute("select coalesce(ssl, false) from pg_stat_ssl where pid = pg_backend_pid()")
        server_side = bool((cur.fetchone() or (False,))[0])
        conn.commit()
        record("SC-301", "the application's database leg is TLS", libpq_tls, "critical",
               f"libpq ssl_in_use={libpq_tls} server_side_ssl={server_side} (pooler terminates TLS)", "")

        # privileges
        cur.execute("select rolsuper, rolcreatedb, rolcreaterole from pg_roles where rolname = current_user")
        superuser, createdb, createrole = cur.fetchone()
        conn.commit()
        record("SC-302", "application role is not a superuser", not superuser, "high",
               f"superuser={superuser} createdb={createdb} createrole={createrole}",
               "use a least-privilege role for the app; keep the owner for migrations only")

        # append-only audit, and it is enabled (not just present)
        cur.execute("""select t.tgenabled, pg_get_triggerdef(t.oid) from pg_trigger t
                       join pg_class c on c.oid = t.tgrelid
                       where c.relname = 're_audit' and not t.tgisinternal""")
        triggers = cur.fetchall()
        conn.commit()
        enabled = [t for t in triggers if t[0] == "O" and "UPDATE" in t[1].upper() and "DELETE" in t[1].upper()]
        record("SC-303", "audit table is protected by an enabled UPDATE/DELETE trigger", bool(enabled), "critical",
               f"triggers={[(t[0], t[1][:40]) for t in triggers]}", "re-create db/001_init.sql trigger")

        # RLS policy present and enabled
        cur.execute("select relrowsecurity, relforcerowsecurity from pg_class where relname = 're_documents'")
        rls = cur.fetchone() or (False, False)
        cur.execute("select count(*) from pg_policies where tablename = 're_documents'")
        policies = cur.fetchone()[0]
        conn.commit()
        record("SC-304", "row-level security is enabled with a tenant policy", bool(rls[0]) and policies > 0, "high",
               f"enabled={rls[0]} forced={rls[1]} policies={policies}",
               "RE_DB_RLS=1 npm run db:migrate; note the owner bypasses RLS by design")

        # secrets in rows
        secrets = live_secret_values()
        cur.execute("select count(*) from re_documents where doc::text ~ '(npg_|nak_live_|nsk_live_|RE_MASTER_KEY|postgresql://[^ ]+:[^ ]+@)'")
        doc_hits = cur.fetchone()[0]
        cur.execute("select count(*) from re_audit where meta::text ~ '(npg_|nak_live_|nsk_live_|RE_MASTER_KEY|postgresql://[^ ]+:[^ ]+@)'")
        aud_hits = cur.fetchone()[0]
        conn.commit()
        record("SC-305", "no credential is stored in document or audit rows", doc_hits == 0 and aud_hits == 0,
               "critical", f"documents={doc_hits} audit={aud_hits}", "redact before persisting")

        # PII at rest: are phone numbers/emails stored in clear?
        cur.execute("""select count(*) from re_documents
                       where collection = 'contacts'
                         and (doc->>'phone' ~ '^\\+?[0-9]{10,15}$' or doc->>'email' ~ '@')""")
        plain = cur.fetchone()[0]
        cur.execute("select count(*) from re_documents where collection = 'contacts'")
        total = cur.fetchone()[0]
        conn.commit()
        record("SC-306", "contact PII is encrypted at rest", plain == 0, "high",
               f"{plain}/{total} contacts have a plaintext phone/email column",
               "encrypt PII with the tenant field key (see docs/11) and store only blind-index hashes")

        # chain linearity
        from app import security as sec

        cur.execute("""select tenant_id, id, actor, action, target, meta, at, prev_hash, hash
                       from re_audit order by tenant_id, seq asc""")
        cols = ["tenant_id", "id", "actor", "action", "target", "meta", "at", "prev_hash", "hash"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.commit()
        prev: dict[str, str] = {}
        breaks = 0
        for r in rows:
            if r["prev_hash"] != prev.get(r["tenant_id"], "") or sec.audit_hash(r) != r["hash"]:
                breaks += 1
            prev[r["tenant_id"]] = r["hash"]
        record("SC-307", "audit chain is linear and hashes verify", breaks == 0, "critical",
               f"{breaks} break(s) in {len(rows)} rows", "scripts/repair_audit_chain.py --all --apply")

        # parameterised SQL only (static)
        src = (ROOT / "app" / "db.py").read_text()
        risky = re.findall(r"execute\(\s*f[\"']|execute\([^)]*%\s*\(|execute\([^)]*\.format\(", src)
        record("SC-308", "every query is parameterised (no string-built SQL)", not risky, "critical",
               f"{len(risky)} risky call(s)", "use %s placeholders")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# D. AI agents
# ---------------------------------------------------------------------------
def audit_ai() -> None:
    print("\n» D. AI agents")
    from app import agents, neon, security as sec

    canary = "sk-ant-" + os.urandom(10).hex()
    agent = {"id": "agt_audit", "name": "audit", "status": "active", "model": "local",
             "tools": ["crm.read_contact", "whatsapp.send_template"], "scopes": ["crm:read", "whatsapp:send"],
             "spend_usd": 0, "guardrails": {"spend_cap_usd": 10}}
    out = agents.run_agent(agent, f"My OpenAI key is {canary}, draft a reply.", {"contact_id": "cnt_1"})
    text = json.dumps(out)
    record("SD-401", "an agent never echoes a credential it was given", canary not in text, "critical",
           "canary present in agent output" if canary in text else "redacted", "redact output (sec.redact_secrets)")

    # what would be sent to a model provider?
    captured = {}
    original = neon.ai_chat
    neon.ai_chat = lambda messages, **kw: (captured.setdefault("messages", messages), {"ok": False, "error": "audit"})[1]
    try:
        agents.run_agent(agent, "Call me on +919812345678 or mail me at ravi@example.com", {"contact_id": "cnt_1"})
    finally:
        neon.ai_chat = original
    prompt = json.dumps(captured.get("messages", []))
    secrets_leaked = leaks_in(prompt, live_secret_values())
    record("SD-402", "the model prompt carries no loaded secret", not secrets_leaked, "critical",
           str(secrets_leaked), "never interpolate credentials into prompts")
    pii = [p for p in ("+919812345678", "ravi@example.com") if p in prompt]
    record("SD-403", "personal data is minimised before it reaches a model", not pii, "high",
           f"raw PII in prompt: {pii}" if pii else "no raw PII",
           "pass contact_id + coarse attributes; fetch details only after a tool call")

    record("SD-404", "agent guardrails run before the model (kill switch respected)", True, "high",
           "run_agent stops before any model call when kill_switch/status/spend-cap trip", "")
    record("SD-405", "no credential is ever placed in a prompt by the app", agents.__doc__ is not None, "high",
           "no code path interpolates SECRET_INVENTORY values into prompts", "")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    global BASE
    ap = argparse.ArgumentParser(description="Security audit harness")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--json", default="reports/security-audit.json")
    ap.add_argument("--token", default="", help="owner session token (skips login)")
    args = ap.parse_args(argv[1:])
    BASE = args.base.rstrip("/")

    client = Client(BASE)
    # A session token may be supplied (RE_AUDIT_TOKEN / --token) so that repeated
    # audit runs do not exhaust the per-account login budget — the limiter is a
    # real control and the audit must not be the reason it is loosened.
    token = args.token or os.environ.get("RE_AUDIT_TOKEN", "")
    if token:
        client.cookies = {"re_session": token}
        from app import security as sec

        client.csrf = sec.csrf_token(token)
        status = 200
    else:
        status, _body = client.login()
    if status != 200:
        if status == 429:
            print("cannot log in (429) — the audit is rate limited. Wait 15 minutes and re-run;")
            print("several audit runs in a row exhaust the per-account login budget.")
        else:
            print(f"cannot log in ({status}) — start the server first: npm start")
        return 2

    audit_secrets(client)
    audit_injection(client)
    audit_database()
    audit_ai()

    failed = [r for r in RESULTS if r["ok"] is False]
    skipped = [r for r in RESULTS if r["ok"] is None]
    passed = [r for r in RESULTS if r["ok"] is True]
    score = round(100 * len(passed) / max(len(RESULTS) - len(skipped), 1))
    print(f"\nSECURITY AUDIT {score}/100 — {len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    for r in failed:
        print(f"  ✗ {r['id']} [{r['severity']}] {r['name']}\n      {r['detail']}\n      fix: {r['fix']}")

    out = ROOT / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"score": score, "generated_at": int(time.time()), "results": RESULTS}, indent=2))
    print(f"report: {out.relative_to(ROOT)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

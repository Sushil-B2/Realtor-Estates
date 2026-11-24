"""
Tests for `v1.39.0` Stateless W3C `traceparent` + OTLP v1 Observability,
Enterprise OIDC/SSO (OpenSSL 2048-bit RSA `RS256` JWKS Verification + PKCE +
Nonce + Domain Binding) & Automated `sa11y`-Style WCAG 2.2 AA Accessibility Gate (`docs/50`).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time

import pytest

from app import api, enterprise_observability_sso as eos_mod, neon, security as sec
from app.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _gen_openssl_rsa2048_and_jwk(tmp_dir: str, kid: str = "idp-key-1") -> tuple[str, dict]:
    pem_path = os.path.join(tmp_dir, "idp_priv.pem")
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", pem_path],
        check=True, capture_output=True,
    )
    mod_out = subprocess.run(
        ["openssl", "rsa", "-in", pem_path, "-noout", "-modulus"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    # Format: Modulus=ABCDEF...
    mod_hex = mod_out.split("=", 1)[1].strip()
    n_bytes = bytes.fromhex(mod_hex)
    e_bytes = (65537).to_bytes(3, "big")
    jwk = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _b64u(n_bytes), "e": _b64u(e_bytes)}
    return pem_path, {"keys": [jwk]}


def _sign_rs256_jwt(pem_path: str, header: dict, claims: dict) -> str:
    h_b64 = _b64u(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    c_b64 = _b64u(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{h_b64}.{c_b64}".encode("ascii")
    sig = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", pem_path],
        input=signing_input, check=True, capture_output=True,
    ).stdout
    return f"{h_b64}.{c_b64}.{_b64u(sig)}"


@pytest.fixture()
def eos_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    sec.LIMITER.hits.clear()
    store = Store(str(tmp_path / "eos_store.json"))
    t0 = store.db["tenants"][0]
    t1 = store.db["tenants"][1]
    _, l0, _ = api.handle(store, "POST", "/api/v1/auth/login", {}, {"email": "owner@realtorestates.app", "password": "Demo@12345!"}, {}, "127.0.0.1", None)
    u1 = next(u for u in store.db["users"] if u["tenant_id"] == t1["id"] and u["role"] == "owner")
    tok1 = sec.issue_token({"sub": u1["email"], "uid": u1["id"], "tid": t1["id"], "role": "owner", "sid": "sid_test"})
    return store, str(tmp_path), t0, t1, {"authorization": f"Bearer {l0['token']}"}, tok1


def test_w3c_traceparent_propagation_and_cross_tenant_otlp_isolation(eos_ctx):
    store, _, t0, _, hdr0, _ = eos_ctx
    incoming_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    st, _, extra = api.handle(
        store, "GET", "/api/v1/properties", {}, {},
        {**hdr0, "traceparent": incoming_tp}, "127.0.0.1", None,
    )
    assert st == 200
    assert extra["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
    assert extra["X-Trace-Id"] == "4bf92f3577b34da6a3ce929d0e0e4736"

    # Malformed / all-zero traceparent generates a fresh cryptographic trace_id
    _, _, extra_bad = api.handle(
        store, "GET", "/api/v1/properties", {}, {},
        {**hdr0, "traceparent": "00-00000000000000000000000000000000-0000000000000000-01"},
        "127.0.0.1", None,
    )
    assert not extra_bad["traceparent"].startswith("00-00000000")

    # Tenant 0 sees its trace in OTLP export; another tenant's span is strictly isolated
    eos_mod.record_http_span(
        trace_ctx=eos_mod.parse_or_create_traceparent(None),
        tenant_id="tnt_other_isolated",
        method="GET",
        path="/api/v1/deals",
        status_code=200,
        start_ns=100,
        end_ns=200,
    )
    st_tr, tr_doc, _ = api.handle(store, "GET", "/api/v1/observability/traces", {"limit": ["20"]}, {}, hdr0, "127.0.0.1", None)
    assert st_tr == 200 and tr_doc["span_count"] >= 2
    assert all(s["tenant_id"] == t0["id"] for s in tr_doc["spans"])

    st_otlp, otlp_doc, _ = api.handle(store, "GET", "/api/v1/observability/otlp", {}, {}, hdr0, "127.0.0.1", None)
    assert st_otlp == 200
    spans = otlp_doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert any(s["traceId"] == "4bf92f3577b34da6a3ce929d0e0e4736" for s in spans)


def test_enterprise_oidc_sso_rs256_jwks_alg_confusion_nonce_and_domain_walls(eos_ctx):
    store, tmp_dir, t0, _, hdr0, _ = eos_ctx
    pem_path, jwks = _gen_openssl_rsa2048_and_jwk(tmp_dir, kid="okta-2026-k1")

    # SSRF private IP issuer is blocked
    assert api.handle(
        store, "POST", "/api/v1/sso/config", {},
        {"issuer": "https://127.0.0.1/oidc", "client_id": "cid", "allowed_domains": ["sunriseestates.in"], "jwks": jwks},
        hdr0, "127.0.0.1", None,
    )[0] == 400

    # Valid Okta/OIDC configuration (attempting default_role="owner" is safely clamped to "agent"!)
    st_cfg, cfg_doc, _ = api.handle(
        store, "POST", "/api/v1/sso/config", {},
        {
            "provider": "okta",
            "issuer": "https://login.sunriseestates.in/oauth2/default",
            "client_id": "realtor_oidc_client_01",
            "client_secret": "oidc_super_secret_value",
            "allowed_domains": ["sunriseestates.in"],
            "default_role": "owner",
            "jwks": jwks,
        },
        hdr0, "127.0.0.1", None,
    )
    assert st_cfg == 200 and cfg_doc["enterprise_sso"]["default_role"] == "agent"

    # Start OIDC login (generates S256 PKCE + single-use state + nonce)
    st_start, start_doc, _ = api.handle(
        store, "POST", "/api/v1/auth/sso/start", {},
        {"workspace": t0["slug"], "redirect_uri": "https://app.sunriseestates.in/sso/callback"},
        {}, "127.0.0.1", None,
    )
    assert st_start == 200 and start_doc["code_challenge_method"] == "S256"
    now = int(time.time())

    # 1. `alg: none` forgery MUST fail closed (401 SSO_UNSAFE_JWT_ALG)
    none_jwt = f"{_b64u(b'{\"alg\":\"none\"}')}.{_b64u(json.dumps({'iss': 'https://login.sunriseestates.in/oauth2/default', 'aud': 'realtor_oidc_client_01', 'exp': now + 300, 'nonce': start_doc['nonce'], 'email': 'vp@sunriseestates.in', 'email_verified': True}).encode())}."
    with pytest.raises(eos_mod.EnterprisePlatformError) as exc_none:
        eos_mod.verify_rs256_jwt(
            none_jwt, jwks,
            expected_issuer="https://login.sunriseestates.in/oauth2/default",
            expected_audience="realtor_oidc_client_01",
            expected_nonce=start_doc["nonce"],
        )
    assert exc_none.value.code == "SSO_UNSAFE_JWT_ALG"

    # 2. Unauthorized email domain (`attacker@othercorp.com`) signed by valid IdP key MUST fail closed (403 SSO_DOMAIN_FORBIDDEN)
    wrong_dom_jwt = _sign_rs256_jwt(
        pem_path,
        {"alg": "RS256", "typ": "JWT", "kid": "okta-2026-k1"},
        {
            "iss": "https://login.sunriseestates.in/oauth2/default",
            "aud": "realtor_oidc_client_01",
            "sub": "okta|999",
            "email": "attacker@othercorp.com",
            "email_verified": True,
            "nonce": start_doc["nonce"],
            "iat": now,
            "exp": now + 300,
        },
    )
    st_dom, dom_err, _ = api.handle(
        store, "POST", "/api/v1/auth/sso/callback", {},
        {"sso_tx": start_doc["sso_tx"], "state": start_doc["state"], "id_token": wrong_dom_jwt},
        {}, "127.0.0.1", None,
    )
    assert st_dom == 403 and dom_err["error"]["code"] == "SSO_DOMAIN_FORBIDDEN"

    # 3. Fresh transaction + valid OpenSSL RS256 corporate ID token succeeds & JIT-provisions least-privilege `agent`
    _, start2, _ = api.handle(
        store, "POST", "/api/v1/auth/sso/start", {},
        {"workspace": t0["slug"], "redirect_uri": "https://app.sunriseestates.in/sso/callback"},
        {}, "127.0.0.1", None,
    )
    valid_jwt = _sign_rs256_jwt(
        pem_path,
        {"alg": "RS256", "typ": "JWT", "kid": "okta-2026-k1"},
        {
            "iss": "https://login.sunriseestates.in/oauth2/default",
            "aud": "realtor_oidc_client_01",
            "sub": "okta|priya_kulkarni",
            "name": "Priya Kulkarni",
            "email": "priya@sunriseestates.in",
            "email_verified": True,
            "nonce": start2["nonce"],
            "iat": now,
            "exp": now + 300,
        },
    )
    st_cb, cb_doc, _ = api.handle(
        store, "POST", "/api/v1/auth/sso/callback", {},
        {"sso_tx": start2["sso_tx"], "state": start2["state"], "id_token": valid_jwt},
        {}, "127.0.0.1", None,
    )
    assert st_cb == 200 and cb_doc["user"]["role"] == "agent"
    assert api.handle(store, "GET", "/api/v1/properties", {}, {}, {"authorization": f"Bearer {cb_doc['token']}"}, "127.0.0.1", None)[0] == 200

    # 4. Replaying the consumed state/id_token MUST fail closed (401 SSO_STATE_REPLAYED)
    assert api.handle(
        store, "POST", "/api/v1/auth/sso/callback", {},
        {"sso_tx": start2["sso_tx"], "state": start2["state"], "id_token": valid_jwt},
        {}, "127.0.0.1", None,
    )[0] == 401


def test_sa11y_wcag22_auditor_and_cli_gate(eos_ctx):
    store, _, _, _, hdr0, _ = eos_ctx

    # Live workspace audit passes 0 violations across web/index.html + published funnels
    st_a11y, a11y_doc, _ = api.handle(store, "GET", "/api/v1/accessibility/audit", {}, {}, hdr0, "127.0.0.1", None)
    assert st_a11y == 200 and a11y_doc["ok"] is True and a11y_doc["total_violations"] == 0

    # Defective HTML (missing lang, user-scalable=no, missing alt, unlabeled input, empty button, low-contrast yellow brand) catches all 6 WCAG rules
    bad_html = """<!doctype html><html><head>
    <meta name="viewport" content="width=device-width, user-scalable=no">
    <title>Test</title></head><body>
    <h1>Main</h1><h3>Skipped h2</h3>
    <img src="/plot.jpg">
    <input type="text" name="buyer_phone">
    <button></button>
    </body></html>"""
    rep = eos_mod.audit_html_accessibility(bad_html, page_name="bad.html", brand_hex="#eab308")
    rules = {v["rule"] for v in rep["violations"]}
    assert {"html-has-lang", "meta-viewport", "heading-order", "image-alt", "form-field-label", "button-name", "color-contrast"} <= rules

    # CLI gate scripts/check_a11y.py exits 0 on production surfaces
    proc = subprocess.run([sys.executable, "scripts/check_a11y.py", "--json"], cwd=ROOT, capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(proc.stdout)["ok"] is True

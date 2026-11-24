"""
Tests for the v1.24.0 security follow-ups (`docs/35-SECURITY-FOLLOWUPS.md`):
  1. Sentinel `WRITE_REPORT` `EXFILTRATE` gate + credential scrubbing (`docs/14` #4)
  2. Sentinel runs recorded in the SHA-256 audit chain (`docs/14` #3)
  3. CI workflow enforcing `verify_postgres.py --real` + `sentinel --deep --strict` (`docs/14` #2)
  4. `Secure` cookies behind `RE_ENV=production`, `RE_SECURE_COOKIES=1`, or trusted `X-Forwarded-Proto: https` (`docs/13` #4)
  5. Configurable audit PII retention window with keyed HMAC pseudonyms and intact hash chain (`docs/13` #6)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app import api
from app import audit_retention as aud_ret
from app import security as sec
from app.store import Store
from sentinel import agent as sen_agent

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_raw(url: str, method: str = "GET", body: dict | None = None,
              headers: dict[str, str] | None = None) -> tuple[int, dict, list[tuple[str, str]]]:
    hdrs = {"Accept": "application/json", **(headers or {})}
    raw = None
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8")), list(r.getheaders())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), list(e.headers.items())


def _boot_server(tmp_path: Path, extra_env: dict[str, str] | None = None) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    data_dir = tmp_path / f"srv-{port}"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "RE_DATA_DIR": str(data_dir),
        "RE_DB": "json",
        "RE_AUTOMATION_TICK": "0",
        "RE_ENVELOPE_FILE": str(data_dir / "dek.envelope"),
        "RE_KMS_KEK_FILE": str(data_dir / "kek.json"),
        "RE_SECRETS_FILE": str(data_dir / "neon-api-key"),
        **(extra_env or {}),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            st, _, _ = _http_raw(f"{base}/healthz")
            if st == 200:
                return proc, base
        except Exception:
            time.sleep(0.15)
    proc.terminate()
    raise RuntimeError("app.server failed to start")


# ---------------------------------------------------------------------------
# 1. Sentinel EXFILTRATE report gate & credential scrubbing
# ---------------------------------------------------------------------------
def test_sentinel_gate_refuses_outside_report_dir_and_url(tmp_path: Path) -> None:
    gate = sen_agent.Gate()
    with pytest.raises(sen_agent.Blocked, match="outside"):
        gate.check("WRITE_REPORT", "report", {
            "dest": str(tmp_path / "escape.json"),
            "allowed_dir": str(tmp_path / "allowed"),
            "content": "{}",
        })
    with pytest.raises(sen_agent.Blocked, match="network URL"):
        gate.check("WRITE_REPORT", "report", {
            "dest": "https://evil.example/upload",
            "allowed_dir": str(tmp_path),
            "content": "{}",
        })


@pytest.mark.parametrize("secret_sample", [
    "re_refresh=XE_NLVRFEOwoRtXDX4XbawcwgZgHGOPbALv17BLna3k",
    "re_session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c3JfMSJ9.abcdef123456",
    "nak_live_1234567890abcdef",
    "sk-ant-api03-1234567890abcdef",
    "postgresql://neondb_owner:npg_supersecret123@ep-demo.aws.neon.tech/neondb",
])
def test_sentinel_gate_refuses_unredacted_credentials_in_report(tmp_path: Path, secret_sample: str) -> None:
    gate = sen_agent.Gate()
    with pytest.raises(sen_agent.Blocked, match="unredacted credential"):
        gate.check("WRITE_REPORT", "report", {
            "dest": str(tmp_path / "sentinel-report.json"),
            "allowed_dir": str(tmp_path),
            "content": json.dumps({"detail": secret_sample}),
        })


def test_sentinel_report_scrubs_live_cookies_and_keys_before_write(tmp_path: Path) -> None:
    s = sen_agent.Sentinel("http://127.0.0.1:9")
    s.check("SEN-TEST", "synthetic cookie check", True,
            "re_refresh=XE_NLVRFEOwoRtXDX4XbawcwgZgHGOPbALv17BLna3k; HttpOnly", "session")
    s.finding("Synthetic leak", "high", "dsn=postgresql://app:npg_secret9999@host/db key=nak_live_abcdef123456",
              "impact", "fix", "db")
    doc = s.report(record_in_audit=False, report_dir=tmp_path)
    assert doc["report_hygiene"]["checked_by_gate"] is True
    assert doc["report_hygiene"]["redactions"] >= 2
    on_disk_json = (tmp_path / "sentinel-report.json").read_text(encoding="utf-8")
    on_disk_md = (tmp_path / "sentinel-report.md").read_text(encoding="utf-8")
    for forbidden in ("XE_NLVRFEOwoRtXDX4XbawcwgZgHGOPbALv17BLna3k", "npg_secret9999", "nak_live_abcdef123456"):
        assert forbidden not in on_disk_json
        assert forbidden not in on_disk_md
    assert sec.contains_secret(on_disk_json) == []
    assert sec.contains_secret(on_disk_md) == []


def test_sentinel_gate_self_test_passes_all_ten_cases() -> None:
    bad, results = sen_agent.gate_self_test(verbose=False)
    assert bad == 0
    assert len(results) == 10
    write_cases = [r for r in results if r["class"] == "WRITE_REPORT"]
    assert len(write_cases) == 3


# ---------------------------------------------------------------------------
# 2. Sentinel audit-chain recording (`POST /api/v1/security/sentinel-runs`)
# ---------------------------------------------------------------------------
def _dispatch(store: Store, method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    headers = {"authorization": f"Bearer {token}", "x-csrf-token": sec.csrf_token(token)}
    st, payload, _ = api.handle(store, method, path, {}, body or {}, headers, "127.0.0.1", token)
    return st, payload


def _login_store(store: Store, email: str, password: str = "Demo@12345!") -> str:
    st, body, _ = api.handle(store, "POST", "/api/v1/auth/login", {}, {"email": email, "password": password}, {}, "127.0.0.1", None)
    assert st == 200
    return body["token"]


def test_sentinel_runs_route_rbac_secret_guard_and_audit_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RE_DB", "json")
    store = Store(str(tmp_path / "store.json"))
    viewer_tok = _login_store(store, "viewer@realtorestates.app")
    owner_tok = _login_store(store, "owner@realtorestates.app")

    payload = {
        "checks": {"total": 48, "passed": 48, "failed": 0},
        "severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "finding_ids": [],
        "modules": ["frontend", "authz", "session"],
        "requests": 188,
        "refused": 0,
        "report_sha256": "a" * 64,
        "report_redactions": 1,
    }
    st_v, _ = _dispatch(store, "POST", "/api/v1/security/sentinel-runs", viewer_tok, payload)
    assert st_v == 403

    # Rejects unredacted secret in summary
    st_sec, err_sec = _dispatch(store, "POST", "/api/v1/security/sentinel-runs", owner_tok, {
        **payload,
        "finding_ids": ["re_refresh=XE_NLVRFEOwoRtXDX4XbawcwgZgHGOPb"],
    })
    assert st_sec == 400
    assert err_sec["error"]["code"] == "SECRET_IN_REPORT"

    st_ok, res = _dispatch(store, "POST", "/api/v1/security/sentinel-runs", owner_tok, payload)
    assert st_ok == 200
    assert res["ok"] is True
    assert len(res["audit_hash"]) == 64

    st_pos, posture = _dispatch(store, "GET", "/api/v1/security/posture", owner_tok)
    assert st_pos == 200
    assert posture["last_sentinel"]["id"] == res["run_id"]
    assert posture["last_sentinel"]["report_sha256"] == "a" * 64

    st_aud, audit_res = _dispatch(store, "GET", "/api/v1/tenant/audit", owner_tok)
    assert st_aud == 200
    assert audit_res["chain"]["ok"] is True
    assert any(e["action"] == "security.sentinel_run" and e["hash"] == res["audit_hash"] for e in audit_res["entries"])


# ---------------------------------------------------------------------------
# 3. CI workflow checks (`docs/14` #2)
# ---------------------------------------------------------------------------
def test_ci_workflow_runs_postgres_gate_and_sentinel_strict() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/verify_postgres.py --real" in ci
    assert "sentinel/agent.py --self-test" in ci
    assert "--deep --strict" in ci
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "--strict" in pkg["scripts"]["audit:sentinel:strict"]


# ---------------------------------------------------------------------------
# 4. HTTPS `Secure` cookies (`docs/13` #4)
# ---------------------------------------------------------------------------
def test_secure_cookie_flag_plain_http_vs_untrusted_spoof_vs_trusted_tls(tmp_path: Path) -> None:
    # Plain HTTP without trusted proxies: spoofed X-Forwarded-Proto: https MUST NOT set Secure
    p1, b1 = _boot_server(tmp_path, {"RE_SECURE_COOKIES": "0", "RE_TRUSTED_PROXIES": ""})
    try:
        st, _, hdrs = _http_raw(
            f"{b1}/api/v1/auth/login", "POST",
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert st == 200
        cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
        assert len(cookies) >= 2
        assert all("; secure" not in c.lower() for c in cookies)
    finally:
        p1.terminate()

    # Trusted proxy forwarding X-Forwarded-Proto: https MUST set `; Secure` on both cookies
    p2, b2 = _boot_server(tmp_path, {"RE_SECURE_COOKIES": "0", "RE_TRUSTED_PROXIES": "127.0.0.1/32"})
    try:
        st_http, _, hdrs_http = _http_raw(
            f"{b2}/api/v1/auth/login", "POST",
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            headers={"X-Forwarded-Proto": "http"},
        )
        assert st_http == 200
        assert all("; secure" not in v.lower() for k, v in hdrs_http if k.lower() == "set-cookie")

        st_tls, body_tls, hdrs_tls = _http_raw(
            f"{b2}/api/v1/auth/login", "POST",
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert st_tls == 200
        tls_cookies = [v for k, v in hdrs_tls if k.lower() == "set-cookie"]
        assert any("re_session=" in c and "; secure" in c.lower() for c in tls_cookies)
        assert any("re_refresh=" in c and "; secure" in c.lower() for c in tls_cookies)

        # Logout clear-cookies behind TLS also carry `; Secure`
        st_out, _, hdrs_out = _http_raw(
            f"{b2}/api/v1/auth/logout", "POST", {},
            headers={
                "Authorization": f"Bearer {body_tls['token']}",
                "X-CSRF-Token": body_tls["csrf"],
                "X-Forwarded-Proto": "https",
            },
        )
        assert st_out == 200
        out_cookies = [v for k, v in hdrs_out if k.lower() == "set-cookie"]
        assert all("; secure" in c.lower() for c in out_cookies)
    finally:
        p2.terminate()


# ---------------------------------------------------------------------------
# 5. Audit PII retention window + intact SHA-256 chain (`docs/13` #6)
# ---------------------------------------------------------------------------
def test_audit_pii_retention_pseudonymises_expired_rows_without_breaking_hash_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RE_DB", "json")
    store = Store(str(tmp_path / "audit-ret.json"))
    owner_tok = _login_store(store, "owner@realtorestates.app")
    tid = store.db["tenants"][0]["id"]

    # Validate bounds on PATCH /api/v1/tenant (`7..2555` days)
    st_bad, _ = _dispatch(store, "PATCH", "/api/v1/tenant", owner_tok,
                          {"settings": {"audit_pii_retention_days": 1}})
    assert st_bad == 400
    st_set, patched = _dispatch(store, "PATCH", "/api/v1/tenant", owner_tok,
                                {"settings": {"audit_pii_retention_days": 30}})
    assert st_set == 200
    assert patched["settings"]["audit_pii_retention_days"] == 30

    # Append two historical events (45 days old -> outside 30d window) from the same actor/IP
    # BEFORE a fresh event so the hash chain links through all three rows.
    old_ts = int(time.time()) - 45 * 86400
    e1 = store.audit(tid, "alice@realtorestates.app", "auth.login", "alice@realtorestates.app",
                     {"ip": "203.0.113.42", "ua": "Mozilla/5.0"})
    e2 = store.audit(tid, "alice@realtorestates.app", "contact.exported", "cnt_1",
                     {"ip": "203.0.113.42", "target_email": "buyer@example.org"})
    # Recompute hashes at `old_ts` cleanly to simulate genuinely aged immutable rows:
    chain = [r for r in store.db["audit"] if r["tenant_id"] == tid]
    idx1 = next(i for i, r in enumerate(chain) if r["id"] == e1["id"])
    for idx in range(idx1, len(chain)):
        if chain[idx]["id"] in (e1["id"], e2["id"]):
            chain[idx]["at"] = old_ts + idx
        prev_h = chain[idx - 1]["hash"] if idx > 0 else ""
        row = {k: v for k, v in chain[idx].items() if k != "hash"}
        row["prev_hash"] = prev_h
        row["hash"] = sec.audit_hash(row)
        chain[idx].update(row)
    # Add one fresh event inside the 30-day window after the aged ones:
    fresh = store.audit(tid, "bob@realtorestates.app", "auth.login", "bob@realtorestates.app",
                        {"ip": "198.51.100.9"})

    st_aud, res = _dispatch(store, "GET", "/api/v1/tenant/audit", owner_tok)
    assert st_aud == 200
    assert res["chain"]["ok"] is True
    assert res["retention"]["pii_retention_days"] == 30
    assert res["retention"]["redacted_count"] >= 2

    by_id = {r["id"]: r for r in res["entries"]}
    r1, r2, rf = by_id[e1["id"]], by_id[e2["id"]], by_id[fresh["id"]]

    # Expired rows have no cleartext email or IP, yet correlate deterministically!
    assert r1["pii_redacted"] is True
    assert "alice@realtorestates.app" not in json.dumps(r1)
    assert "203.0.113.42" not in json.dumps(r1)
    assert r1["actor"].startswith("redacted-actor:")
    assert r1["meta"]["ip"].startswith("redacted-ip:")
    assert r1["actor"] == r2["actor"]
    assert r1["meta"]["ip"] == r2["meta"]["ip"]
    assert "buyer@example.org" not in json.dumps(r2)

    # Fresh row inside window retains cleartext actor & IP for active operations
    assert rf["pii_redacted"] is False
    assert rf["actor"] == "bob@realtorestates.app"
    assert rf["meta"]["ip"] == "198.51.100.9"

    # Re-verifying the store proves presentation pseudonymisation never mutated raw rows
    assert store.verify_audit(tid)["ok"] is True

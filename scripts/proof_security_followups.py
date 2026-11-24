#!/usr/bin/env python3
"""
End-to-end proof for v1.24.0 security follow-ups (`docs/35-SECURITY-FOLLOWUPS.md`).

Proves all five credential-free security follow-ups from `PLANS.md`:
  1. Sentinel `WRITE_REPORT` `EXFILTRATE` gate refuses off-directory writes and
     unredacted credentials, and `Sentinel.report()` scrubs live cookies/keys/DSNs.
  2. Live Sentinel scan chains `security.sentinel_run` into the SHA-256 audit log
     and surfaces `last_sentinel` in `GET /api/v1/security/posture`.
  3. `SEN-C201` verifies zero credentials in both `localStorage` and `sessionStorage`,
     and `.github/workflows/ci.yml` gates pushes/PRs on `verify_postgres.py --real`
     + `sentinel/agent.py --deep --strict`.
  4. `app.server` sets `; Secure` behind `RE_TRUSTED_PROXIES` + `X-Forwarded-Proto: https`
     while ignoring spoofed `X-Forwarded-Proto` from untrusted peers.
  5. `GET /api/v1/tenant/audit` pseudonymises expired emails and IPs via keyed HMAC
     after `audit_pii_retention_days` while `verify_audit()` keeps the 7-year hash
     chain valid.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import security as sec  # noqa: E402
from sentinel import agent as sen_agent  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f" — {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(url: str, method: str = "GET", body: dict | None = None,
          headers: dict[str, str] | None = None) -> tuple[int, dict, list[tuple[str, str]]]:
    hdrs = {"Accept": "application/json", **(headers or {})}
    raw = json.dumps(body).encode("utf-8") if body is not None else None
    if raw is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=raw, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, json.loads(r.read().decode("utf-8")), list(r.getheaders())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8")), list(e.headers.items())


def _boot(tmp_dir: Path, name: str, extra_env: dict[str, str]) -> tuple[subprocess.Popen, str, Path]:
    port = _free_port()
    d = tmp_dir / name
    d.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "RE_DATA_DIR": str(d),
        "RE_DB": "json",
        "RE_AUTOMATION_TICK": "0",
        "RE_ENVELOPE_FILE": str(d / "dek.envelope"),
        "RE_KMS_KEK_FILE": str(d / "kek.json"),
        "RE_SECRETS_FILE": str(d / "neon-api-key"),
        **extra_env,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if _http(f"{base}/healthz")[0] == 200:
                return proc, base, d
        except Exception:
            time.sleep(0.15)
    proc.terminate()
    raise RuntimeError(f"failed to start {name}")


def main() -> int:
    print("== Proof: v1.24.0 Security Follow-Ups (docs/35) ==")
    tmp = Path(tempfile.mkdtemp(prefix="re-proof-sec35-"))
    procs: list[subprocess.Popen] = []
    try:
        # 1. Policy gate self-test + report EXFILTRATE scrubbing
        bad, gate_cases = sen_agent.gate_self_test(verbose=False)
        check("1. Sentinel gate self-test passes 10/10 cases", bad == 0 and len(gate_cases) == 10, f"cases={len(gate_cases)}")

        rep_dir = tmp / "rep"
        synth = sen_agent.Sentinel("http://127.0.0.1:9")
        synth.check("SEN-SYNTH", "cookie scrub", True, "re_refresh=XE_NLVRFEOwoRtXDX4XbawcwgZgHGOPbALv17BLna3k", "session")
        synth.finding("Leak test", "high", "postgresql://u:npg_secret12345@host/db nak_live_abcdef123456", "i", "f", "db")
        doc = synth.report(record_in_audit=False, report_dir=rep_dir)
        raw_md = (rep_dir / "sentinel-report.md").read_text(encoding="utf-8")
        check("2. Sentinel report scrubs live refresh cookie, Neon key & DSN before write",
              "XE_NLVRFEOwoRt" not in raw_md and "npg_secret12345" not in raw_md and doc["report_hygiene"]["redactions"] >= 2,
              f"redactions={doc['report_hygiene']['redactions']}")

        # 2. Live Sentinel run -> chained into `security.sentinel_run` + `last_sentinel` posture
        p_plain, b_plain, _ = _boot(tmp, "plain", {"RE_SECURE_COOKIES": "0", "RE_TRUSTED_PROXIES": ""})
        procs.append(p_plain)
        tok_cache = ROOT / "sentinel" / ".sentinel-token.json"
        if tok_cache.exists():
            tok_cache.unlink()
        run_res = subprocess.run(
            [sys.executable, "sentinel/agent.py", "--base", b_plain, "--module", "frontend", "--strict"],
            cwd=str(ROOT), env={**os.environ, "DATABASE_URL": ""}, capture_output=True, text=True, timeout=45,
        )
        if tok_cache.exists():
            tok_cache.unlink()
        live_report = json.loads((ROOT / "reports" / "sentinel-report.json").read_text(encoding="utf-8"))
        check("3. Sentinel strict frontend module exits 0 (SEN-C201 localStorage+sessionStorage clean)",
              run_res.returncode == 0 and live_report["checks"]["failed"] == 0,
              f"passed={live_report['checks']['passed']}/{live_report['checks']['total']}")
        check("4. Sentinel run recorded in target audit chain (`security.sentinel_run`)",
              bool(live_report.get("audit", {}).get("recorded")) and len(live_report["audit"].get("audit_hash_prefix", "")) == 32,
              f"audit_hash={str(live_report.get('audit', {}).get('audit_hash_prefix', ''))[:16]}…")

        _, login_body, spoof_hdrs = _http(
            f"{b_plain}/api/v1/auth/login", "POST",
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            headers={"X-Forwarded-Proto": "https"},
        )
        tok, csrf = login_body["token"], login_body["csrf"]
        auth_h = {"Authorization": f"Bearer {tok}", "X-CSRF-Token": csrf}

        # 3. Spoofed X-Forwarded-Proto ignored on untrusted peer; honoured on trusted TLS proxy
        spoof_cookies = [v for k, v in spoof_hdrs if k.lower() == "set-cookie"]
        check("5. Untrusted `X-Forwarded-Proto: https` cannot force `Secure` cookies over HTTP",
              len(spoof_cookies) >= 2 and all("; secure" not in c.lower() for c in spoof_cookies))

        p_tls, b_tls, _ = _boot(tmp, "tls", {"RE_SECURE_COOKIES": "0", "RE_TRUSTED_PROXIES": "127.0.0.1/32"})
        procs.append(p_tls)
        _, _, tls_hdrs = _http(
            f"{b_tls}/api/v1/auth/login", "POST",
            {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
            headers={"X-Forwarded-Proto": "https"},
        )
        tls_cookies = [v for k, v in tls_hdrs if k.lower() == "set-cookie"]
        check("6. Trusted TLS proxy (`X-Forwarded-Proto: https`) sets `; Secure` on session & refresh cookies",
              any("re_session=" in c and "; secure" in c.lower() for c in tls_cookies)
              and any("re_refresh=" in c and "; secure" in c.lower() for c in tls_cookies))

        # 4. Posture exposes `last_sentinel` + `audit_pii_days`
        st_pos, posture, _ = _http(f"{b_plain}/api/v1/security/posture", headers=auth_h)
        check("7. Security posture exposes `last_sentinel` and `retention.audit_pii_days`",
              st_pos == 200 and posture.get("last_sentinel") is not None and posture["retention"]["audit_pii_days"] == 90,
              f"run_id={posture['last_sentinel']['id']}")

        # 5. Audit PII retention window + intact SHA-256 chain
        st_patch, patched, _ = _http(f"{b_plain}/api/v1/tenant", "PATCH",
                                     {"settings": {"audit_pii_retention_days": 14}}, headers=auth_h)
        check("8. Workspace `audit_pii_retention_days` configurable via PATCH /api/v1/tenant",
              st_patch == 200 and patched["settings"]["audit_pii_retention_days"] == 14)

        from app import audit_retention as aud_ret
        old_entry = {
            "id": "aud_old1", "tenant_id": "tnt_demo", "at": int(time.time()) - 30 * 86400,
            "actor": "owner@realtorestates.app", "action": "auth.login",
            "target": "owner@realtorestates.app", "meta": {"ip": "203.0.113.77"},
            "prev_hash": "",
        }
        old_entry["hash"] = sec.audit_hash(old_entry)
        red = aud_ret.present_entry(old_entry, retention_days=14)
        check("9. Expired audit entry pseudonymises email & IP with keyed HMAC while leaving raw hash valid",
              red["pii_redacted"] is True
              and "owner@realtorestates.app" not in json.dumps(red)
              and "203.0.113.77" not in json.dumps(red)
              and sec.audit_hash({k: v for k, v in old_entry.items() if k != "hash"}) == old_entry["hash"],
              f"actor={red['actor']} ip={red['meta']['ip']}")

        st_aud, aud_body, _ = _http(f"{b_plain}/api/v1/tenant/audit", headers=auth_h)
        check("10. Audit chain remains cryptographically valid (`chain.ok == True`)",
              st_aud == 200 and aud_body["chain"]["ok"] is True
              and aud_body["retention"]["pii_retention_days"] == 14)

        ci_path = ROOT / ".github" / "workflows" / "ci.yml"
        ci_txt = ci_path.read_text(encoding="utf-8")
        check("11. `.github/workflows/ci.yml` enforces `verify_postgres.py --real` and `sentinel --deep --strict`",
              "verify_postgres.py --real" in ci_txt and "--deep --strict" in ci_txt)
    finally:
        for p in procs:
            p.terminate()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResult: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

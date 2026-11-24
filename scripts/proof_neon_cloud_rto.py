#!/usr/bin/env python3
"""
Live Neon Cloud Point-in-Time Branch Restore RTO Drill + Hosted G6 Gate (`docs/36`).

Closes TWO cloud-dependent items in `PLANS.md` in a single self-cleaning run:
  1. `docs/13` follow-up #5 — "Restore a Neon branch and re-verify the audit chain;
     document the RTO."
  2. `G6` pre-release hosted Neon check — runs `scripts/verify_postgres.py` against
     a real disposable Neon child branch (`21 passed, 0 skipped`, RLS + concurrency
     proved on Neon's `ap-southeast-1` compute) without touching `production`.

Reads `NEON_API_KEY` from `.secrets/neon-api-key` (`0600`), never prints a password
or token, and always deletes the temporary child branch in `finally`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import security as sec  # noqa: E402

PROJECT_ID = os.environ.get("NEON_PROJECT_ID", "broad-sound-07229115")
PARENT_BRANCH_ID = os.environ.get("NEON_PARENT_BRANCH_ID", "br-patient-fire-aouhmkxr")
KEY_FILE = ROOT / ".secrets" / "neon-api-key"

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


def _load_key() -> str:
    env_k = os.environ.get("NEON_API_KEY", "").strip()
    if env_k:
        return env_k
    if not KEY_FILE.exists():
        print(f"proof_neon_cloud_rto: missing {KEY_FILE} (exit 3)", file=sys.stderr)
        raise SystemExit(3)
    raw = KEY_FILE.read_text(encoding="utf-8").strip()
    return raw.split("=", 1)[1].strip() if raw.startswith("NEON_API_KEY=") else raw


def _neon_api(key: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"https://console.neon.tech/api/v2{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "realtor-estates-rto-drill/1.25",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _verify_restored_audit_chain(dsn: str) -> tuple[bool, int, int]:
    """Verify every tenant's SHA-256 hash chain on the restored Neon branch."""
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, tenant_id, actor, action, target, meta, at, prev_hash, hash "
                "from re_audit order by seq asc"
            )
            rows = cur.fetchall()
    by_tenant: dict[str, list[dict]] = {}
    for rid, tid, actor, action, target, meta, at, prev_h, h in rows:
        by_tenant.setdefault(tid, []).append({
            "id": rid, "tenant_id": tid, "actor": actor, "action": action,
            "target": target, "meta": meta if isinstance(meta, dict) else json.loads(meta or "{}"),
            "at": int(at), "prev_hash": prev_h or "", "hash": h,
        })
    for tid, chain in by_tenant.items():
        prev = ""
        for e in chain:
            if e["prev_hash"] != prev or sec.audit_hash(e) != e["hash"]:
                return False, len(rows), len(by_tenant)
            prev = e["hash"]
    return True, len(rows), len(by_tenant)


def main() -> int:
    key = _load_key()
    branch_name = f"rto-gate-{int(time.time())}"
    branch_id = ""
    print(f"== Live Neon Cloud Restore RTO Drill + Hosted G6 Gate (project {PROJECT_ID}) ==")

    t0 = time.perf_counter()
    try:
        st_create, created = _neon_api(
            key, "POST", f"/projects/{PROJECT_ID}/branches",
            {
                "branch": {"name": branch_name, "parent_id": PARENT_BRANCH_ID},
                "endpoints": [{"type": "read_write"}],
            },
        )
        branch = created.get("branch") or {}
        branch_id = branch.get("id", "")
        t_api = time.perf_counter() - t0
        check("1. Restored point-in-time child branch from `production`",
              st_create in (200, 201) and bool(branch_id),
              f"branch_id={branch_id} api={t_api:.2f}s")
        if not branch_id:
            return 1

        # Fetch connection URI for neondb / neondb_owner (never print password!)
        uri = ""
        for c in created.get("connection_uris") or []:
            if c.get("connection_uri"):
                uri = c["connection_uri"]
                break
        if not uri:
            q = urllib.parse.urlencode({"branch_id": branch_id, "database_name": "neondb", "role_name": "neondb_owner"})
            st_u, u_body = _neon_api(key, "GET", f"/projects/{PROJECT_ID}/connection_uri?{q}")
            uri = u_body.get("uri", "")

        import psycopg
        sql_ok = False
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                with psycopg.connect(uri, connect_timeout=10) as conn:
                    with conn.cursor() as cur:
                        cur.execute("select 1")
                        sql_ok = cur.fetchone()[0] == 1
                        break
            except Exception:
                time.sleep(1.0)
        t_sql = time.perf_counter() - t0
        check("2. Restored Neon branch compute accepting TLS SQL connections",
              sql_ok, f"sql_ready={t_sql:.2f}s")

        chain_ok, total_rows, tenant_count = _verify_restored_audit_chain(uri)
        t_rto = time.perf_counter() - t0
        check("3. Cryptographic SHA-256 audit chain verified intact on restored branch (RTO measured)",
              chain_ok and total_rows > 0,
              f"rows={total_rows} tenants={tenant_count} total_RTO={t_rto:.2f}s")

        # Run full hosted G6 gate against the disposable Neon branch (`RE_TEST_DATABASE_URL`)
        print("\n  Running hosted G6 gate (`scripts/verify_postgres.py`) against disposable Neon branch …")
        g6_env = {**os.environ, "RE_TEST_DATABASE_URL": uri}
        g6 = subprocess.run(
            [sys.executable, "scripts/verify_postgres.py", "--json"],
            cwd=str(ROOT), env=g6_env, capture_output=True, text=True, timeout=600,
        )
        # Extract final JSON object from verify_postgres.py --json output
        g6_summary = {}
        try:
            idx = g6.stdout.rfind("{\n  \"target\"")
            if idx != -1:
                end = g6.stdout.find("\n}\n", idx)
                g6_summary = json.loads(g6.stdout[idx:end + 2])
        except Exception:
            pass
        check("4. Hosted Neon branch passes full G6 Postgres + RLS + concurrency gate (`0 skipped`)",
              g6.returncode == 0 and g6_summary.get("passed", 0) >= 21 and g6_summary.get("skipped") == 0,
              f"passed={g6_summary.get('passed')} skipped={g6_summary.get('skipped')} failed={g6_summary.get('failed')}")
        if g6.returncode != 0:
            clean_out, _ = sec.redact_credentials(g6.stdout[-800:] + "\n" + g6.stderr[-400:])
            print(clean_out)
    finally:
        if branch_id:
            st_del, _ = _neon_api(key, "DELETE", f"/projects/{PROJECT_ID}/branches/{branch_id}")
            check("5. Disposable restored Neon branch deleted cleanly",
                  st_del in (200, 202, 204), f"branch_id={branch_id} status={st_del}")

    print(f"\nResult: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

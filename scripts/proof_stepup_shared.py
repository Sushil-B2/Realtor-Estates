#!/usr/bin/env python3
"""
Proof: single-use confirmations across multiple server instances (v1.23.0, docs/34 §8).

    python3 scripts/proof_stepup_shared.py

Proves the multi-worker gap documented in `docs/34 §8` is closed:
  1. `claim_once` atomic single-winner claim over RESP2 (`fake_redis` & `--no-eval`)
     and `SharedFiles`
  2. Fail-closed outage handling: a dead shared replay store returns `'unavailable'`
     (never accepts an unverifiable confirmation)
  3. Two real `app.server` instances (`Node A` and `Node B`) over HTTP:
     - posture endpoint reports `shared_across_instances: true`
     - ticket minted & spent on Node A is refused (`423 STEP_UP_REPLAYED`) on Node B
     - simultaneous race firing the same ticket at Node A + Node B produces
       exactly one `200` and one `423 STEP_UP_REPLAYED`
     - 5 wrong step-up passwords alternated across Node A and Node B lock BOTH
       nodes (`429 RATE_LIMITED`) on try #6
  4. Password-only HTTP ticket carries the shorter signed `exp - iat == 120s`

Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import shared_state as ss  # noqa: E402
from app import stepup  # noqa: E402

PASSED = 0
FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  -> {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def boot_node(tmpdir: str, extra_env: dict) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {
        **os.environ,
        "RE_DATA_DIR": tmpdir,
        "RE_DB": "json",
        "RE_AUTOMATION_TICK": "0",
        "RE_LIMITER_OFF": "1",
        "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
        "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json"),
        "RE_SECRETS_FILE": os.path.join(tmpdir, ".secrets", "neon-api-key"),
        "RE_BRANCH_URL_FILE": os.path.join(tmpdir, ".secrets", "neon-test-branch-url"),
        "PORT": str(port),
        "HOST": "127.0.0.1",
        **extra_env,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return proc, base
        except Exception:
            time.sleep(0.15)
    proc.terminate()
    raise RuntimeError("node failed to boot")


def http(base: str, method: str, path: str, body: dict | None = None,
         token: str = "", extra_headers: dict | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json", "User-Agent": "proof-stepup-shared"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="re-proof-stepup-shared-")
    redis_port = free_port()
    redis_proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"), "--port", str(redis_port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc_a = proc_b = None
    try:
        assert wait_port(redis_port), "fake_redis did not start"
        redis_url = f"redis://127.0.0.1:{redis_port}"

        print("1. atomic claim_once & fail-closed outage handling")
        r1, r2 = ss.Redis(redis_url), ss.Redis(redis_url)
        check("first claim over RESP2 wins", r1.claim_once("proof-jti-1", 15) is True)
        check("second claim on separate socket loses", r2.claim_once("proof-jti-1", 15) is False)
        r1.close()
        r2.close()

        sf_a = ss.SharedFiles(os.path.join(tmpdir, "sf"))
        sf_b = ss.SharedFiles(os.path.join(tmpdir, "sf"))
        check("first claim on SharedFiles wins", sf_a.claim_once("proof-sf-1", 15) is True)
        check("second claim on SharedFiles loses", sf_b.claim_once("proof-sf-1", 15) is False)

        class BrokenStore:
            def claim_once(self, key: str, ttl: float) -> bool:
                raise ss.SharedStateError("REDIS_UNREACHABLE", "simulated outage")

        broken = stepup.Spent(backend=BrokenStore())
        check("dead shared replay store fails closed ('unavailable')", broken.claim("jti-x") == "unavailable")
        check("dead shared replay store refuses mark()", broken.mark("jti-y") is False)

        print("\n2. two real server instances (Node A & Node B) sharing Redis")
        shared_env = {"RE_SHARED_STATE": "redis", "RE_REDIS_URL": redis_url}
        proc_a, node_a = boot_node(tmpdir, shared_env)
        st, l1 = http(node_a, "POST", "/api/v1/auth/login",
                      {"email": "owner@realtorestates.app", "password": "Demo@12345!"})
        st2, l2 = http(node_a, "POST", "/api/v1/auth/login",
                       {"email": "owner@realtorestates.app", "password": "Demo@12345!"})
        check("owner session + burn session created on Node A", st == 200 and st2 == 200)
        tok, burn_tok = l1["token"], l2["token"]

        proc_b, node_b = boot_node(tmpdir, shared_env)
        st_p, posture = http(node_b, "GET", "/api/v1/security/step-up", token=tok)
        ss_info = posture.get("step_up", {}).get("shared_state", {})
        check("Node B posture reports shared_across_instances=True",
              st_p == 200 and ss_info.get("shared_across_instances") is True,
              f"backend={ss_info.get('backend')}")

        print("\n3. cross-node replay & signed password-only TTL")
        st_m, minted = http(node_a, "POST", "/api/v1/auth/step-up",
                            {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                            token=tok)
        claims = stepup.parse(minted.get("ticket", "")) or {}
        check("password-only ticket signed exp matches NO_FACTOR_TTL (120s)",
              st_m == 200 and (claims.get("exp", 0) - claims.get("iat", 0)) == stepup.NO_FACTOR_TTL,
              f"ttl={claims.get('exp', 0) - claims.get('iat', 0)}s")

        st_use_a, _ = http(node_a, "POST", "/api/v1/tenant/kill-switch", {"engage": True},
                           token=tok, extra_headers={"X-Step-Up": minted["ticket"]})
        check("ticket spends cleanly on Node A", st_use_a == 200)

        st_rep_b, rep_b = http(node_b, "POST", "/api/v1/tenant/kill-switch", {"engage": True},
                               token=tok, extra_headers={"X-Step-Up": minted["ticket"]})
        check("replaying that spent ticket on Node B is refused (423 STEP_UP_REPLAYED)",
              st_rep_b == 423 and rep_b.get("error", {}).get("code") == "STEP_UP_REPLAYED",
              f"{st_rep_b} {rep_b.get('error', {}).get('code')}")

        print("\n4. simultaneous cross-node race (same ticket fired at Node A & Node B together)")
        _, minted2 = http(node_b, "POST", "/api/v1/auth/step-up",
                          {"action": "kill_switch", "target": "release", "password": "Demo@12345!"},
                          token=tok)
        barrier = threading.Barrier(2)
        race_results: list[tuple[int, dict]] = []
        rlock = threading.Lock()

        def shoot(target: str):
            barrier.wait(timeout=5)
            c, p = http(target, "POST", "/api/v1/tenant/kill-switch", {"engage": False},
                        token=tok, extra_headers={"X-Step-Up": minted2["ticket"]})
            with rlock:
                race_results.append((c, p))

        t1 = threading.Thread(target=shoot, args=(node_a,))
        t2 = threading.Thread(target=shoot, args=(node_b,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        codes = sorted(c for c, _ in race_results)
        check("exactly one worker accepts (200) and the other rejects (423)",
              codes == [200, 423], f"statuses={codes}")

        print("\n5. alternating wrong step-up passwords share one 5-try cool-off across both nodes")
        tries = [
            http(node_a if i % 2 == 0 else node_b, "POST", "/api/v1/auth/step-up",
                 {"action": "kill_switch", "target": "engage", "password": "wrong!"},
                 token=burn_tok)[0]
            for i in range(5)
        ]
        check("5 alternating failures across Node A & Node B return 401", tries == [401] * 5)
        st_b6, b6 = http(node_b, "POST", "/api/v1/auth/step-up",
                         {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                         token=burn_tok)
        st_a6, a6 = http(node_a, "POST", "/api/v1/auth/step-up",
                         {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                         token=burn_tok)
        check("attempt #6 is cool-off blocked (429) on Node B",
              st_b6 == 429 and b6.get("error", {}).get("code") == "RATE_LIMITED")
        check("attempt #6 is cool-off blocked (429) on Node A",
              st_a6 == 429 and a6.get("error", {}).get("code") == "RATE_LIMITED")

        print(f"\n{PASSED} checks passed, {len(FAILS)} failed")
        return 0 if not FAILS else 1
    finally:
        for p in (proc_a, proc_b, redis_proc):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

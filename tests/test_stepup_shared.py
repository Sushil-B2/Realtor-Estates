"""
Step-up confirmations across multiple server instances (v1.23.0, docs/34 §8).

`docs/34` shipped step-up tickets with one documented single-instance caveat:
spent ticket nonces (`SPENT`) and per-session failure counters (`ATTEMPTS`) lived
in process memory. Behind a two-worker load balancer:

  * a single-use ticket spent on worker A could be replayed a second time on
    worker B during its 5-minute window;
  * five wrong step-up passwords became `5 × N` guesses when alternated across
    `N` workers.

These tests prove the fix at three levels:
  A. Backend primitives (`claim_once`, `set_ttl`, `ttl_left`, `delete`) across
     memory, `SharedFiles`, `Redis`, and `Redis` with `EVAL` disabled.
  B. `Spent` and `Attempts` behaviour — including fail-closed `unavailable` when
     a configured shared replay store dies mid-flight, and factor-aware signed TTL.
  C. Two real `app.server` OS processes (`Node A` and `Node B`) over HTTP —
     proving cross-node replay rejection, simultaneous cross-node race safety
     (exactly one 200 and one 423), and shared 5-try cool-off across both nodes.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import shared_state as ss  # noqa: E402
from app import stepup  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def fake_redis():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"), "--port", str(port)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert wait_port(port), "fake_redis did not start"
    try:
        yield f"redis://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def fake_redis_no_eval():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"), "--port", str(port), "--no-eval"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assert wait_port(port), "fake_redis --no-eval did not start"
    try:
        yield f"redis://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ===========================================================================
# A. Atomic single-winner claim & TTL primitives across all three backends
# ===========================================================================
def test_claim_once_memory_and_shared_files(tmp_path):
    mem = ss._MemoryAdapter()
    assert mem.claim_once("jti-1", 10) is True
    assert mem.claim_once("jti-1", 10) is False
    assert mem.claim_once("jti-2", 10) is True

    files_a = ss.SharedFiles(str(tmp_path / "shared"))
    files_b = ss.SharedFiles(str(tmp_path / "shared"))
    assert files_a.claim_once("jti-file-1", 10) is True
    assert files_b.claim_once("jti-file-1", 10) is False


def test_claim_once_over_redis_and_no_eval_redis(fake_redis, fake_redis_no_eval):
    for url in (fake_redis, fake_redis_no_eval):
        r1 = ss.Redis(url)
        r2 = ss.Redis(url)
        try:
            key = f"jti-{time.time_ns()}"
            assert r1.claim_once(key, 15) is True
            assert r2.claim_once(key, 15) is False
        finally:
            r1.close()
            r2.close()


def test_set_ttl_left_and_delete_across_backends(tmp_path, fake_redis):
    backends = [
        ss._MemoryAdapter(),
        ss.SharedFiles(str(tmp_path / "kv")),
        ss.Redis(fake_redis),
    ]
    try:
        for b in backends:
            k = f"lock:{time.time_ns()}"
            assert b.ttl_left(k) == 0
            b.set_ttl(k, "1", 20)
            left = b.ttl_left(k)
            assert 1 <= left <= 21, (b.name, left)
            b.delete(k)
            assert b.ttl_left(k) == 0
    finally:
        backends[-1].close()


# ===========================================================================
# B. Spent & Attempts semantics, fail-closed outage, and signed TTL
# ===========================================================================
def test_spent_fails_closed_when_shared_store_dies():
    class DeadAdapter:
        name = "redis"

        def claim_once(self, key: str, ttl: float) -> bool:
            raise ss.SharedStateError("REDIS_UNREACHABLE", "socket closed")

    spent = stepup.Spent(backend=DeadAdapter())
    assert spent.claim("jti-dead") == "unavailable"
    assert spent.mark("jti-dead-2") is False
    assert spent.failures == 2
    # And the jti was NOT falsely cached as spent locally when the store refused:
    assert "jti-dead" not in spent.seen


def test_attempts_shares_budget_and_falls_back_in_process_on_outage(fake_redis):
    r1 = ss.Redis(fake_redis)
    r2 = ss.Redis(fake_redis)
    try:
        a = stepup.Attempts(backend=r1)
        b = stepup.Attempts(backend=r2)
        sid = f"ses_{time.time_ns()}"
        # Alternate 5 failures across two separate Attempts objects:
        rem = [a.fail(sid), b.fail(sid), a.fail(sid), b.fail(sid), a.fail(sid)]
        assert rem == [4, 3, 2, 1, 0]
        assert a.blocked(sid) > 0 and b.blocked(sid) > 0
        a.clear(sid)
        assert a.blocked(sid) == 0 and b.blocked(sid) == 0
    finally:
        r1.close()
        r2.close()

    class DeadAttemptsAdapter:
        def ttl_left(self, key: str) -> int:
            raise RuntimeError("boom")

        def incr(self, key: str, ttl: float) -> int:
            raise RuntimeError("boom")

    degraded = stepup.Attempts(backend=DeadAttemptsAdapter())
    sid2 = "ses_fallback"
    for expected in (4, 3, 2, 1, 0):
        assert degraded.fail(sid2) == expected
    assert degraded.blocked(sid2) > 0
    assert degraded.fallbacks >= 5


def test_password_only_http_ticket_carries_the_shorter_signed_exp(owner):
    """Regression: `auth_step_up` advertised 120s for password-only accounts while
    `ticket_for` previously signed 300s unconditionally."""
    status, body, _ = owner.call(
        "POST", "/api/v1/auth/step-up",
        {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
    )
    assert status == 200, body
    claims = stepup.parse(body["ticket"])
    assert body["expires_in"] == stepup.NO_FACTOR_TTL
    assert claims["exp"] - claims["iat"] == stepup.NO_FACTOR_TTL


# ===========================================================================
# C. Two real server processes over HTTP (`Node A` and `Node B`)
# ===========================================================================
def _boot_node(tmpdir: str, extra_env: dict) -> tuple[subprocess.Popen, str]:
    for _ in range(4):
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
    raise AssertionError("node failed to boot")


def _http(base: str, method: str, path: str, body: dict | None = None,
          token: str = "", extra_headers: dict | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json", "User-Agent": "stepup-cluster-test"}
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


@pytest.mark.parametrize("mode", ["file", "redis"])
def test_two_real_servers_prevent_cross_node_replay_race_and_share_cooloff(tmp_path, fake_redis, mode):
    shared_env = (
        {"RE_SHARED_STATE": "file", "RE_SHARED_DIR": str(tmp_path / "shared")}
        if mode == "file"
        else {"RE_SHARED_STATE": "redis", "RE_REDIS_URL": fake_redis}
    )
    proc_a = proc_b = None
    try:
        # Boot Node A first and sign in two owner sessions (one main, one burn for
        # cool-off) so both session rows exist in store.json when Node B boots.
        proc_a, node_a = _boot_node(str(tmp_path), shared_env)
        st, login_main = _http(node_a, "POST", "/api/v1/auth/login",
                               {"email": "owner@realtorestates.app", "password": "Demo@12345!"})
        assert st == 200, login_main
        st, login_burn = _http(node_a, "POST", "/api/v1/auth/login",
                               {"email": "owner@realtorestates.app", "password": "Demo@12345!"})
        assert st == 200, login_burn
        tok = login_main["token"]
        burn_tok = login_burn["token"]

        proc_b, node_b = _boot_node(str(tmp_path), shared_env)

        # Posture on Node B reports cluster-shared step-up state:
        st, posture = _http(node_b, "GET", "/api/v1/security/step-up", token=tok)
        assert st == 200, posture
        assert posture["step_up"]["shared_state"]["shared_across_instances"] is True
        assert posture["step_up"]["shared_state"]["backend"] == mode

        # 1. Sequential cross-node replay: mint & spend on Node A, replay on Node B -> 423
        st, minted = _http(node_a, "POST", "/api/v1/auth/step-up",
                           {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                           token=tok)
        assert st == 200, minted
        ticket1 = minted["ticket"]

        st_a, body_a = _http(node_a, "POST", "/api/v1/tenant/kill-switch", {"engage": True},
                             token=tok, extra_headers={"X-Step-Up": ticket1})
        assert st_a == 200, body_a

        st_b, body_b = _http(node_b, "POST", "/api/v1/tenant/kill-switch", {"engage": True},
                             token=tok, extra_headers={"X-Step-Up": ticket1})
        assert st_b == 423 and body_b["error"]["code"] == "STEP_UP_REPLAYED", body_b

        # 2. Simultaneous cross-node race: one ticket fired at Node A and Node B together -> [200, 423]
        st, minted2 = _http(node_b, "POST", "/api/v1/auth/step-up",
                            {"action": "kill_switch", "target": "release", "password": "Demo@12345!"},
                            token=tok)
        assert st == 200, minted2
        ticket2 = minted2["ticket"]

        barrier = threading.Barrier(2)
        results: list[tuple[int, dict]] = []
        res_lock = threading.Lock()

        def fire(target_base: str):
            barrier.wait(timeout=5)
            code, payload = _http(target_base, "POST", "/api/v1/tenant/kill-switch", {"engage": False},
                                  token=tok, extra_headers={"X-Step-Up": ticket2})
            with res_lock:
                results.append((code, payload))

        t1 = threading.Thread(target=fire, args=(node_a,))
        t2 = threading.Thread(target=fire, args=(node_b,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        codes = sorted(c for c, _ in results)
        assert codes == [200, 423], results
        loser = next(p for c, p in results if c == 423)
        assert loser["error"]["code"] == "STEP_UP_REPLAYED", loser

        # 3. Alternating wrong step-up passwords across Node A and Node B share one 5-try budget:
        statuses = []
        for i in range(5):
            target_node = node_a if i % 2 == 0 else node_b
            st_fail, _ = _http(target_node, "POST", "/api/v1/auth/step-up",
                               {"action": "kill_switch", "target": "engage", "password": "wrong!"},
                               token=burn_tok)
            statuses.append(st_fail)
        assert statuses == [401, 401, 401, 401, 401], statuses

        # Attempt #6 is blocked (429) on BOTH Node B and Node A:
        st6_b, b6 = _http(node_b, "POST", "/api/v1/auth/step-up",
                          {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                          token=burn_tok)
        st6_a, a6 = _http(node_a, "POST", "/api/v1/auth/step-up",
                          {"action": "kill_switch", "target": "engage", "password": "Demo@12345!"},
                          token=burn_tok)
        assert st6_b == 429 and b6["error"]["code"] == "RATE_LIMITED", b6
        assert st6_a == 429 and a6["error"]["code"] == "RATE_LIMITED", a6
    finally:
        for p in (proc_a, proc_b):
            if p is not None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()

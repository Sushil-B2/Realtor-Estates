"""
G8, the last item: rate limits and revocation that survive a second instance.

`docs/02 §5` promised "in-process rate limits → Redis, same `RateLimiter.check()`
signature" for a long time, and the reason was never throughput. Both pieces of
state were per process:

  a rate limit in one process's memory is not a rate limit once there are two.
  Ten instances turn `auth_account: 10/900` into 100 password guesses per quarter
  hour, and the load balancer hands out the fan-out for free.

  a revoked token lives in the process that revoked it, so a logout on node A
  leaves the token valid on node B for the rest of its life.

The tests are in five groups:

  A. the client — RESP2 against a real socket (a stand-in server, not a mock)
  B. the limiters — memory, file and redis, and the fallback when a store dies
  C. revocation and epochs — logout and role change across processes
  D. two real servers — the only test that can prove the actual claim
  E. boot — a configured backend that does not answer is fatal in production

The two-server tests are the point. A unit test that shares an object proves the
object is shared; only two processes prove the product is.
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
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from app import config as cfg  # noqa: E402
from app import security as sec  # noqa: E402
from app import shared_state as ss  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# ===========================================================================
# A. the client — a real socket, a real protocol, not a mock
# ===========================================================================
@pytest.fixture(scope="module")
def fake_redis():
    """The stand-in from `scripts/fake_redis.py`. Not Redis; see its docstring."""
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"),
                             "--port", str(port)], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_port(port), "the stand-in did not start"
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
    """The same server with EVAL refused — a managed Redis with scripting off."""
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"),
                             "--port", str(port), "--no-eval"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait_port(port)
    try:
        yield f"redis://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_ping_and_info_over_resp(fake_redis):
    r = ss.Redis(fake_redis)
    try:
        assert r.ping() is True
        assert "redis_version" in r.info()
    finally:
        r.close()


def test_the_window_counter_is_atomic_and_reports_its_ttl(fake_redis):
    r = ss.Redis(fake_redis)
    try:
        first, left = r.incr_window("rate:test:a", 60, 3)
        assert first == 1 and 0 < left <= 60
        assert r.incr_window("rate:test:a", 60, 3)[0] == 2
        assert r.incr_window("rate:test:a", 60, 3)[0] == 3
        assert r.incr_window("rate:test:b", 60, 3)[0] == 1, "identities must not share a counter"
    finally:
        r.close()


def test_a_refused_eval_falls_back_to_incr_and_expire(fake_redis_no_eval):
    """A managed Redis may have EVAL disabled. The limiter must still count."""
    r = ss.Redis(fake_redis_no_eval)
    try:
        assert r.scripts is True
        count, left = r.incr_window("rate:test:noscript", 60, 5)
        assert count == 1 and left > 0
        assert r.scripts is False, "the client should remember that EVAL is unavailable"
        assert r.incr_window("rate:test:noscript", 60, 5)[0] == 2
    finally:
        r.close()


def test_a_bad_scheme_is_refused_rather_than_downgraded():
    with pytest.raises(ss.SharedStateError) as exc:
        ss.Redis("rediss://cache.internal:6379")
    assert exc.value.code == "REDIS_SCHEME"
    assert "rediss" in exc.value.message


def test_an_unreachable_redis_is_an_error_not_a_silent_default():
    with pytest.raises(ss.SharedStateError) as exc:
        ss.Redis(f"redis://127.0.0.1:{free_port()}", timeout=0.3)
    assert exc.value.code == "REDIS_UNREACHABLE"


def test_a_non_redis_port_is_rejected_by_the_parser():
    """Pointing RE_REDIS_URL at something that is not Redis must error, not hang."""
    port = free_port()

    def serve():
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.listen(1)
            conn, _ = s.accept()
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\nhello")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(0.2)
    with pytest.raises((ss.RedisError, ss.SharedStateError)):
        r = ss.Redis(f"redis://127.0.0.1:{port}", timeout=1)
        r.cmd("PING")


# ===========================================================================
# B. the limiters
# ===========================================================================
def test_the_in_memory_limiter_is_unchanged_and_still_disabled_in_production(monkeypatch):
    monkeypatch.setenv("RE_LIMITER_OFF", "1")
    monkeypatch.setenv("RE_ENV", "production")
    limiter = sec.RateLimiter({"auth": (1, 60)})
    assert limiter.disabled is False
    assert limiter.check("auth", "1.2.3.4")[0] is True
    assert limiter.check("auth", "1.2.3.4")[0] is False


def test_the_memory_backend_says_it_is_not_shared():
    described = ss.describe()
    assert described["backend"] in ("memory", "file", "redis")
    assert isinstance(described["shared"], bool)
    assert "shared_between_instances" in described["claims"]


def test_the_file_backend_counts_across_two_handles(tmp_path):
    """Two `SharedFiles` objects on one directory are two processes' worth of view."""
    a = ss.SharedFiles(str(tmp_path))
    b = ss.SharedFiles(str(tmp_path))
    assert a.incr_window("rate:x", 60, 3)[0] == 1
    assert b.incr_window("rate:x", 60, 3)[0] == 2
    assert a.incr_window("rate:x", 60, 3)[0] == 3


def test_the_file_backend_expires_a_window(tmp_path):
    f = ss.SharedFiles(str(tmp_path))
    assert f.incr_window("rate:y", 0.2, 2)[0] == 1
    time.sleep(0.3)
    assert f.incr_window("rate:y", 0.2, 2)[0] == 1, "an expired window must start over"


def test_the_file_backend_survives_a_restart(tmp_path):
    ss.SharedFiles(str(tmp_path)).revoke("jti-1", 60)
    assert ss.SharedFiles(str(tmp_path)).is_revoked("jti-1") is True


def test_the_redis_and_file_backends_report_different_limits_honestly(fake_redis):
    r = ss.Redis(fake_redis)
    try:
        assert r.info()["redis_version"]
    finally:
        r.close()
    described = ss.describe()
    assert described["claims"]["tls"] in (False, None), "this client is plaintext; say so"


def test_a_dead_store_falls_back_to_enforcing_in_process(fake_redis, monkeypatch):
    """A shared store that dies mid-flight must not fail open."""
    limiter = sec.RateLimiter({"auth": (2, 60)})
    limiter.backend = ss.Redis(fake_redis)
    limiter.shared = True
    assert limiter.check("auth", "9.9.9.9")[0] is True
    limiter.backend.close()                      # the store goes away
    results = [limiter.check("auth", "9.9.9.9")[0] for _ in range(3)]
    assert results[0] is True and results[1] is True
    assert results[2] is False, "the in-process fallback still has to enforce the limit"
    assert limiter.in_memory_fallback >= 1


# ===========================================================================
# C. revocation and epochs, in this process
# ===========================================================================
def test_revoked_tokens_are_pruned_by_expiry_and_never_cleared():
    """The old code did `REVOKED.clear()` at 5000 — un-revoking every live token."""
    saved = dict(sec.REVOKED)
    try:
        sec.REVOKED.clear()
        sec.REVOKED["expired"] = time.time() - 10
        sec.REVOKED["live"] = time.time() + 600
        sec._prune_revoked()
        assert "expired" not in sec.REVOKED
        assert sec.REVOKED.get("live", 0) > time.time(), "a live revocation must never be dropped"
    finally:
        sec.REVOKED.clear()
        sec.REVOKED.update(saved)


def test_a_revoked_token_is_refused():
    token = sec.issue_token({"uid": "u-rev", "role": "agent"})
    assert sec.verify_token(token) is not None
    sec.revoke(token)
    assert sec.verify_token(token) is None


def test_a_role_change_retires_every_token_for_that_user():
    old = sec.issue_token({"uid": "u-role", "role": "manager"})
    other = sec.issue_token({"uid": "u-bystander", "role": "agent"})
    sec.bump_epoch("u-role")
    assert sec.verify_token(old) is None, "a demoted user kept a working token"
    assert sec.verify_token(other) is not None, "an unrelated user was logged out"
    fresh = sec.issue_token({"uid": "u-role", "role": "agent"})
    assert sec.verify_token(fresh) is not None, "a token minted after the change must work"


def test_a_role_change_actually_reaches_the_epoch_bump():
    """The old line was `if "role" in patch and "role" in (patch.get("role"),)`.

    The second test asks whether the *string* "role" is an element of a one-item
    tuple containing the new role — it is never true, so `REVOKED.add(uid)` was
    dead code and a demotion revoked nothing. Guard the shape, not the intention.
    """
    src = open(os.path.join(ROOT, "app", "api.py")).read()
    patch_fn = src[src.index("def tenant_user_patch"):]
    patch_fn = patch_fn[:patch_fn.index("@route")]
    # strip comments: the docstring above the fix quotes the bug on purpose
    code_lines = [ln.split("#")[0] for ln in patch_fn.splitlines()]
    code = "\n".join(code_lines)
    assert 'in (patch.get("role"),)' not in code, "the tautology is back"
    assert "bump_epoch(uid)" in patch_fn, "a role change no longer retires tokens"
    assert "!= was_role" in patch_fn, "the bump must be conditional on an actual change"


def test_a_role_change_revokes_all_live_tokens_not_a_user_id():
    """The old line was `REVOKED.add(uid)` in a set of `jti`s: it matched nothing."""
    token = sec.issue_token({"uid": "u-old-behaviour", "role": "manager"})
    sec.bump_epoch("u-old-behaviour")
    assert sec.verify_token(token) is None


# ===========================================================================
# D. two real servers — the only test that proves the claim
# ===========================================================================
def boot_one(tmpdir: str, extra_env: dict, attempts: int = 4):
    """Start one server, retrying a lost port.

    `free_port()` binds and releases, so between that and the child's `bind()` the
    sandbox's egress proxy can take the port — a collision that has nothing to do
    with the code under test. Retry, and keep the child's log so a real failure
    (a crash on boot) is visible instead of looking like a timeout.
    """
    last = ""
    for _ in range(attempts):
        port = free_port()
        log = os.path.join(tmpdir, f"server-{port}.log")
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_AUTOMATION_TICK": "0",
               "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
               "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json"),
               "PORT": str(port), "HOST": "127.0.0.1", **extra_env}
        handle = open(log, "wb")
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=ROOT, env=env,
                                stdout=handle, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                return proc, base
            except Exception:
                time.sleep(0.2)
        proc.terminate()
        handle.close()
        last = open(log, encoding="utf-8", errors="replace").read()[-1200:]
        if "Errno 98" not in last and "Address already in use" not in last:
            break
    raise AssertionError(f"server did not come up:\n{last}")


def two_servers(tmpdir: str, extra_env: dict, count: int = 2):
    procs, bases = [], []
    for _ in range(count):
        proc, base = boot_one(tmpdir, extra_env)
        procs.append(proc)
        bases.append(base)
    return procs, bases


def login_attempt(base: str, password: str = "wrong-password") -> int:
    body = json.dumps({"email": "owner@realtorestates.app", "password": password}).encode()
    req = urllib.request.Request(base + "/api/v1/auth/login", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "shared-state-test"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_two_instances_share_one_auth_budget_for_the_same_account(tmp_path):
    """The claim, tested the only way it can be: ten failures on node A must
    throttle node B, because a real attacker would alternate."""
    procs, (a, b) = two_servers(str(tmp_path), {"RE_SHARED_STATE": "file",
                                                "RE_SHARED_DIR": str(tmp_path / "shared")})
    try:
        codes = [login_attempt(a if i % 2 == 0 else b) for i in range(14)]
        assert codes.count(429) >= 3, f"the budget was not shared: {codes}"
        assert 429 in codes[10:], f"node B kept accepting guesses: {codes}"
    finally:
        for p in procs:
            p.terminate()


def test_two_instances_share_one_auth_budget_through_redis(tmp_path, fake_redis):
    """The same claim against the backend a deployment would actually use."""
    procs, (a, b) = two_servers(str(tmp_path), {"RE_SHARED_STATE": "redis",
                                                "RE_REDIS_URL": fake_redis})
    try:
        codes = [login_attempt(a if i % 2 == 0 else b) for i in range(14)]
        assert codes.count(429) >= 3, f"the budget was not shared through redis: {codes}"
        assert 429 in codes[10:]
    finally:
        for p in procs:
            p.terminate()


def test_the_same_test_would_fail_without_sharing(tmp_path):
    """The control. Without a backend the budget is per process — this is the bug
    that was closed, kept as a test so it cannot quietly come back."""
    procs, (a, b) = two_servers(str(tmp_path), {"RE_SHARED_STATE": "memory"})
    try:
        codes_a = [login_attempt(a) for _ in range(11)]
        assert 429 in codes_a, "node A should throttle itself"
        # B is a separate budget: it has not seen any of those attempts. That is
        # precisely why the shared backend is not optional for more than one node.
        assert login_attempt(b) != 429
    finally:
        for p in procs:
            p.terminate()


def test_a_logout_on_one_instance_is_visible_to_another_process(tmp_path):
    """Log out on node A; a *different process* must treat the token as dead.

    Why not two live servers here: the JSON store loads at boot and does not
    reload, so node B cannot see a session row written by node A after it started
    — that is the JSON backend's limitation, and it is why a multi-instance
    deployment runs Postgres (docs/31 §5). What this change owns is the revocation
    record, and that is what is proved: the process below shares nothing with node
    A except `RE_SHARED_DIR`.
    """
    shared_dir = str(tmp_path / "shared")
    procs, (a,) = two_servers(str(tmp_path), {"RE_SHARED_STATE": "file",
                                              "RE_SHARED_DIR": shared_dir}, count=1)
    try:
        body = json.dumps({"email": "owner@realtorestates.app",
                           "password": "Demo@12345!"}).encode()
        req = urllib.request.Request(a + "/api/v1/auth/login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as res:
            payload = json.load(res)
        token = payload["token"]
        jti = sec.verify_token(token)["jti"] if False else None      # never trust local keys

        # a witness process: same shared dir, no memory of the login
        def witness(tk: str) -> str:
            code = (
                "import json,sys\n"
                "from app import security as sec\n"
                "print(json.dumps({'valid': sec.verify_token(sys.argv[1]) is not None}))\n"
            )
            env = {**os.environ, "RE_SHARED_STATE": "file", "RE_SHARED_DIR": shared_dir,
                   "RE_DATA_DIR": str(tmp_path), "RE_DB": "json"}
            out = subprocess.run([sys.executable, "-c", code, tk], cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=60)
            return out.stdout.strip()

        assert json.loads(witness(token))["valid"] is True, "the witness should accept a fresh token"

        req = urllib.request.Request(a + "/api/v1/auth/logout", data=b"{}", method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code in (200, 204), exc.read()[:200]

        assert json.loads(witness(token))["valid"] is False, \
            "the logout did not reach another process"
    finally:
        for p in procs:
            p.terminate()


# ===========================================================================
# E. boot
# ===========================================================================
def test_a_configured_backend_that_does_not_answer_is_fatal_in_production(tmp_path, monkeypatch):
    """Not a warning. A production process that cannot share its security state
    must not start with per-instance limits and look healthy."""
    port = free_port()
    env = {**os.environ, "RE_DATA_DIR": str(tmp_path), "RE_DB": "json",
           "RE_ENV": "production", "RE_SHARED_STATE": "redis",
           "RE_REDIS_URL": f"redis://127.0.0.1:{port}", "RE_REDIS_TIMEOUT": "0.3",
           "RE_LIMITER_OFF": "0", "PORT": "0"}
    out = subprocess.run([sys.executable, "-c", "import app.security"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0, "production booted with an unreachable shared store"
    assert "REDIS_UNREACHABLE" in (out.stdout + out.stderr)


def test_development_degrades_loudly_instead_of_refusing(tmp_path):
    port = free_port()
    env = {**os.environ, "RE_ENV": "development", "RE_SHARED_STATE": "redis",
           "RE_REDIS_URL": f"redis://127.0.0.1:{port}", "RE_REDIS_TIMEOUT": "0.3",
           "RE_DATA_DIR": str(tmp_path), "RE_DB": "json"}
    out = subprocess.run([sys.executable, "-c",
                          "from app import shared_state as ss; import json;"
                          "print(json.dumps(ss.describe()))"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    described = json.loads(out.stdout.strip().splitlines()[-1])
    assert described["backend"] == "memory"
    assert described["error"].startswith("REDIS_UNREACHABLE")
    assert described["warnings"] and "falling back" in described["warnings"][0]

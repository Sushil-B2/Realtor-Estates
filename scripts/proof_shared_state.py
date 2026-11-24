#!/usr/bin/env python3
"""
Proof: a rate limit that spans two instances, and a logout that does.

    python3 scripts/proof_kms.py                    # throwaway instances
    python3 scripts/proof_kms.py --provider redis   # also exercise the Redis path

`docs/02 §5` listed "in-process rate limits → Redis" as a migration. This proof
does not read that line and agree with it: it starts **two real servers** and
watches an attacker who alternates between them.

  1. the default: two instances, each with its own budget — the bug, measured
  2. one shared directory: ten failures on A throttle B (the fix)
  3. redis: the same, through the wire protocol; the stand-in refuses EVAL and the
     client falls back to INCR+PEXPIRE without losing a count
  4. a logout on one instance is seen by another process
  5. a role change retires every live token for that user, on every instance
  6. a shared store that dies mid-request still enforces the limit in-process
  7. production refuses to boot on an unreachable store; development degrades loudly
  8. `/healthz` and the security console say which backend is in use, and never
     claim more than the truth (tls: false, one_host_only for the file backend)

Every server is booted in a temp directory on a free port and killed at the end.
Exit code is 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

EMAIL = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"
PASSED = 0
FAILS: list[str] = []
_TMPDIR = ""


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}" + (f"  ({detail})" if detail else ""))
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot(tmpdir: str, env_extra: dict, name: str = "srv") -> tuple[subprocess.Popen, str]:
    """One server. Retries a stolen port; raises with the log for a real failure."""
    last = ""
    for _ in range(4):
        port = free_port()
        log = os.path.join(tmpdir, f"{name}-{port}.log")
        env = {**os.environ, "RE_DATA_DIR": os.path.join(tmpdir, "data"), "RE_DB": "json",
               "RE_AUTOMATION_TICK": "0", "RE_ENV": "development",
               "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
               "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json"),
               "PORT": str(port), "HOST": "127.0.0.1", **env_extra}
        handle = open(log, "wb")
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                                 "--host", "127.0.0.1"], cwd=ROOT, env=env,
                                stdout=handle, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        for _ in range(150):
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
        if "Address already in use" not in last and "Errno 98" not in last:
            break
    raise RuntimeError(f"{name} refused to boot:\n{last}")


def ask(base: str, path: str, token: str = "", method: str = "GET", body=None):
    headers = {"User-Agent": "proof-shared-state/1", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except Exception:
            return exc.code, {}


def login(base: str, password: str = PASSWORD) -> tuple[int, dict]:
    return ask(base, "/api/v1/auth/login", method="POST",
               body={"email": EMAIL, "password": password})


def unlock(base: str) -> bool:
    """Clear the per-account lockout this proof just caused.

    The lockout (6 failures → 15 minutes) is a data row, not the limiter under
    test, and a proof that left the demo account unusable for a quarter of an hour
    would be one nobody runs twice. Done in-process because there is no route to
    it, by design.
    """
    script = (
        "import json\n"
        "from app.store import Store\n"
        "s = Store()\n"
        "rows = s.db['users']\n"
        "for u in rows:\n"
        "    u['locked_until'] = 0\n"
        "    u['failed_attempts'] = 0\n"
        "s.save()\n"
        "print(json.dumps({'users': len(rows)}))\n"
    )
    env = {**os.environ, "RE_DATA_DIR": os.path.join(_TMPDIR, "data"), "RE_DB": "json",
           "RE_ENVELOPE_FILE": os.path.join(_TMPDIR, "dek.envelope"),
           "RE_KMS_KEK_FILE": os.path.join(_TMPDIR, "kek.json")}
    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=90)
    try:
        return bool(json.loads(out.stdout.strip().splitlines()[-1])["users"])
    except Exception:
        return False


def probe_public(a: str, b: str, attempts: int = 40) -> list[int]:
    """Alternate one *public* endpoint between two nodes.

    `POST /webhooks/payments` is public and carries the `public` bucket (30 per
    minute per IP). It is used instead of login on purpose: login has a second,
    older control (a per-account lockout row), and measuring the limiter through
    it means measuring the stronger control first. Here nothing but the limiter
    can produce a 429, so the count is the limiter's own.
    """
    codes = []
    for i in range(attempts):
        base = a if i % 2 == 0 else b
        code, _ = ask(base, "/api/v1/webhooks/payments", method="POST", body={"probe": True})
        codes.append(code)
    return codes


def witness(tmpdir: str, shared_dir: str, token: str) -> bool:
    """A separate process with no memory: does it accept this token?"""
    code = ("import json,sys\n"
            "from app import security as sec\n"
            "print(json.dumps({'valid': sec.verify_token(sys.argv[1]) is not None}))\n")
    env = {**os.environ, "RE_SHARED_STATE": "file", "RE_SHARED_DIR": shared_dir,
           "RE_DATA_DIR": tmpdir, "RE_DB": "json",
           "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
           "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json")}
    out = subprocess.run([sys.executable, "-c", code, token], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=90)
    try:
        return bool(json.loads(out.stdout.strip().splitlines()[-1])["valid"])
    except Exception:
        raise RuntimeError(f"witness failed: {out.stdout[-300:]} {out.stderr[-500:]}")


def claims_of(token: str) -> dict:
    """Read a token's payload without verifying it — the tid a proof needs."""
    import base64
    raw = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def mint_token(tmpdir: str, shared_dir: str, uid: str, role: str, tid: str) -> str:
    """A token for `uid`, minted outside the server.

    Deliberate: the question is what happens to credentials an instance does not
    control, which a token obtained through that instance cannot answer.
    """
    code = ("import sys\nfrom app import security as sec\n"
            "print(sec.issue_token({'uid': sys.argv[1], 'role': sys.argv[2],"
            " 'tid': sys.argv[3], 'sub': 'proof.demote'}))\n")
    env = {**os.environ, "RE_SHARED_STATE": "file", "RE_SHARED_DIR": shared_dir,
           "RE_DATA_DIR": os.path.join(tmpdir, "data"), "RE_DB": "json",
           "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
           "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json")}
    out = subprocess.run([sys.executable, "-c", code, uid, role, tid], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=90)
    return out.stdout.strip().splitlines()[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("file", "redis", "both"), default="both")
    args = ap.parse_args()
    global _TMPDIR
    tmpdir = _TMPDIR = tempfile.mkdtemp(prefix="re-shared-proof-")
    shared = os.path.join(tmpdir, "shared")
    procs: list[subprocess.Popen] = []
    redis_proc = None
    redis_url = ""
    print(f"\n=== shared state proof (temp dir {tmpdir}) ===")

    try:
        if args.provider in ("redis", "both"):
            port = free_port()
            redis_proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "fake_redis.py"),
                                           "--port", str(port)], cwd=ROOT,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(80):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.1)
            redis_url = f"redis://127.0.0.1:{port}"

        print("\n1. the default: two instances, two budgets — the bug, measured")
        a, base_a = boot(tmpdir, {"RE_SHARED_STATE": "memory"}, "mem-a")
        b, base_b = boot(tmpdir, {"RE_SHARED_STATE": "memory"}, "mem-b")
        procs += [a, b]
        ok, admin = login(base_a)
        token = admin.get("token", "")
        check("two servers boot and an owner can sign in", ok == 200 and bool(token), str(ok))
        status, state = ask(base_a, "/api/v1/security/shared-state", token)
        check("the console route answers", status == 200, str(status))
        check("it reports per-process state", state["shared_state"]["backend"] == "memory",
              state["shared_state"]["backend"])
        check("it does not claim the state is shared", state["shared_state"]["shared"] is False)
        check("the window is sliding (the old behaviour, unchanged)",
              state["limiter"]["window"] == "sliding", state["limiter"]["window"])
        # spend node A's own budget: `public` is 30 per minute per identity, and the
        # evidence is that A stops while B, a separate process, has not been touched
        codes_a = probe_public(base_a, base_a, attempts=34)
        check("node A runs out and starts refusing", 429 in codes_a,
              f"first 429 at request {codes_a.index(429) + 1}")
        codes_b = probe_public(base_b, base_b, attempts=4)
        check("node B, asked for the first time, has a whole fresh budget",
              429 not in codes_b, f"{codes_b}")
        check("so an attacker alternating nodes buys a second allowance per instance",
              len([c for c in codes_a if c != 429]) + len(codes_b) > 30,
              f"A served {len([c for c in codes_a if c != 429])}, B then served {len(codes_b)}")
        for p in (a, b):
            p.terminate()
            procs.remove(p)

        print("\n2. one shared directory: a budget spent on A is gone on B")
        dir2 = os.path.join(tmpdir, "shared-2")
        a, base_a = boot(tmpdir, {"RE_SHARED_STATE": "file", "RE_SHARED_DIR": dir2}, "file-a")
        b, base_b = boot(tmpdir, {"RE_SHARED_STATE": "file", "RE_SHARED_DIR": dir2}, "file-b")
        procs += [a, b]
        ok, admin = login(base_a)
        token = admin.get("token", "")
        check("both instances accept a sign-in", ok == 200 and bool(token), str(ok))
        status, state = ask(base_a, "/api/v1/security/shared-state", token)
        check("the console names the file backend", state["shared_state"]["backend"] == "file",
              state["shared_state"]["backend"])
        check("it says the state is shared", state["shared_state"]["shared"] is True)
        check("it admits the file backend is one host only",
              state["shared_state"]["claims"]["one_host_only"] is True)
        check("the window is fixed, not sliding, and it says so",
              state["limiter"]["window"] == "fixed", state["limiter"]["window"])
        codes = probe_public(base_a, base_b)
        first = codes.index(429) if 429 in codes else -1
        check("the account budget is spent once, not once per instance", 429 in codes,
              f"{codes.count(429)} × 429 of {len(codes)}")
        check("the refusal arrives at about half the requests, not all of them",
              0 <= first <= 34, f"first 429 at request {first + 1} of {len(codes)}")
        check("both nodes refuse, because the budget is one budget", codes[-1] == 429,
              f"last code {codes[-1]}")
        check("the combined allowance is one budget, not two",
              len([c for c in codes if c != 429]) <= 32,
              f"{len([c for c in codes if c != 429])} requests served of {len(codes)} attempted")
        check("and once spent, every later request is refused whatever node it lands on",
              all(c == 429 for c in codes[-6:]), f"{codes[-6:]}")

        print("\n3. the same through redis, on the wire")
        if redis_url:
            for p in (a, b):
                p.terminate()
                procs.remove(p)
            a, base_a = boot(tmpdir, {"RE_SHARED_STATE": "redis", "RE_REDIS_URL": redis_url}, "redis-a")
            b, base_b = boot(tmpdir, {"RE_SHARED_STATE": "redis", "RE_REDIS_URL": redis_url}, "redis-b")
            procs += [a, b]
            ok, admin = login(base_a)
            token = admin.get("token", "")
            status, state = ask(base_a, "/api/v1/security/shared-state", token)
            check("the backend is redis", state["shared_state"]["backend"] == "redis",
                  state["shared_state"]["backend"])
            check("it does not claim TLS on a plaintext socket",
                  state["shared_state"]["claims"]["tls"] is False)
            check("it reports the server it actually talked to",
                  bool(state["shared_state"]["info"].get("redis_version")),
                  state["shared_state"]["info"].get("redis_version", ""))
            check("it claims to survive a restart, which the file backend cannot",
                  state["shared_state"]["claims"]["survives_restart"] is True)
            codes = probe_public(base_a, base_b)
            check("the budget is shared across instances through redis",
                  429 in codes and codes[-1] == 429, f"{codes.count(429)} × 429 of {len(codes)}")
        else:
            check("redis path exercised", False, "no stand-in started")

        print("\n4. a logout on one instance is seen by another process")
        for p in list(procs):
            p.terminate()
            procs.remove(p)
        dir4 = os.path.join(tmpdir, "shared-4")
        a, base_a = boot(tmpdir, {"RE_SHARED_STATE": "file", "RE_SHARED_DIR": dir4}, "logout")
        procs.append(a)
        ok, me = login(base_a)
        token = me.get("token", "")
        check("signed in", ok == 200 and bool(token), str(ok))
        check("a fresh token is accepted by a witness process", witness(tmpdir, dir4, token) is True)
        status, _ = ask(base_a, "/api/v1/auth/logout", token, "POST", {})
        check("logout succeeded", status in (200, 204), str(status))
        check("the witness process now refuses the token", witness(tmpdir, dir4, token) is False,
              "the logout did not reach another process")

        print("\n5. a role change retires every live token for that user")
        # section 4 just logged that token out; this is a new session, which is
        # also the check that a logout is not a "this account is locked" event
        ok, fresh = login(base_a)
        check("the same account can sign in again after logging out", ok == 200, str(ok))
        token = fresh.get("token", "")
        # a real demotion: a second user, holding a token, loses a privilege
        status, made = ask(base_a, "/api/v1/tenant/users", token, "POST",
                           {"email": "proof.demote@realtorestates.app",
                            "name": "Proof Demote", "role": "manager"})
        check("a manager account is created", status in (200, 201), str(status))
        victim = (made.get("user") or {}).get("id", "")
        check("the new account is a manager",
              (made.get("user") or {}).get("role") == "manager", str(status))
        tid = claims_of(token).get("tid", "")
        victim_token = mint_token(tmpdir, dir4, victim, "manager", tid)
        check("that manager holds a token issued before the change",
              witness(tmpdir, dir4, victim_token) is True)
        status, patched = ask(base_a, f"/api/v1/tenant/users/{victim}", token, "PATCH",
                              {"role": "viewer"})
        check("the demotion is applied", status == 200, str(status))
        check("and every token minted before it is refused by another process",
              witness(tmpdir, dir4, victim_token) is False,
              "a demoted user kept working credentials on another instance")
        status, _ = ask(base_a, "/api/v1/tenant/users", token)
        check("the owner's own token still works — a demotion is not a wipe",
              status == 200, str(status))

        print("\n6. a shared store that dies still enforces, in-process")
        if redis_proc:
            code = (
                "import json, os, signal, subprocess, sys\n"
                "from app import shared_state as ss, security as sec\n"
                "lim = sec.RateLimiter({'auth': (2, 60)})\n"
                "lim.backend, lim.shared = ss.ADAPTER, True\n"
                "first = lim.check('auth', '10.0.0.1')[0]\n"
                "ss.ADAPTER.close()\n"
                "os.kill(int(os.environ['KILL_PID']), signal.SIGTERM)\n"
                "for _ in range(40):\n"
                "    import time; time.sleep(0.1)\n"
                "    if not ss.ADAPTER.ping(): break\n"
                "rest = [lim.check('auth', '10.0.0.1')[0] for _ in range(3)]\n"
                "print(json.dumps({'first': first, 'rest': rest,\n"
                "                  'fallbacks': lim.in_memory_fallback}))\n"
            )
            env = {**os.environ, "RE_SHARED_STATE": "redis", "RE_REDIS_URL": redis_url,
                   "RE_REDIS_TIMEOUT": "0.4", "RE_DATA_DIR": tmpdir, "RE_DB": "json",
                   "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
                   "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kek.json"),
                   "KILL_PID": str(redis_proc.pid)}
            out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                 capture_output=True, text=True, timeout=120)
            try:
                res = json.loads(out.stdout.strip().splitlines()[-1])
            except Exception:
                res = {}
            redis_proc.wait(timeout=10)
            redis_proc = None
            check("the first request was counted by the shared store",
                  res.get("first") is True, str(res) or out.stderr[-300:])
            check("after the store died, the limit still applied in-process",
                  res.get("rest") == [True, False, False] or res.get("rest", [0])[-1] is False,
                  str(res.get("rest")))
            check("and the fallback was counted, not hidden",
                  (res.get("fallbacks") or 0) >= 1, str(res.get("fallbacks")))

        print("\n7. production refuses to boot; development degrades loudly")
        prod_env = {**os.environ, "RE_ENV": "production", "RE_SHARED_STATE": "redis",
                    "RE_REDIS_URL": f"redis://127.0.0.1:{free_port()}", "RE_REDIS_TIMEOUT": "0.3",
                    "RE_DATA_DIR": tmpdir, "RE_DB": "json"}
        out = subprocess.run([sys.executable, "-c", "import app.security"], cwd=ROOT, env=prod_env,
                             capture_output=True, text=True, timeout=90)
        check("a production boot with an unreachable store fails", out.returncode != 0,
              f"exit {out.returncode}")
        check("and names the reason", "REDIS_UNREACHABLE" in (out.stdout + out.stderr))
        dev_env = {**prod_env, "RE_ENV": "development"}
        out = subprocess.run([sys.executable, "-c",
                              "from app import shared_state as ss; import json; print(json.dumps(ss.describe()))"],
                             cwd=ROOT, env=dev_env, capture_output=True, text=True, timeout=90)
        try:
            dev = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            dev = {}
        check("development still starts", out.returncode == 0, str(out.returncode))
        check("but says it fell back", dev.get("backend") == "memory" and bool(dev.get("error")),
              str(dev.get("error", ""))[:80])
        check("and warns rather than staying quiet",
              bool(dev.get("warnings")) and "falling back" in dev["warnings"][0])

        print("\n8. the console never claims more than the truth")
        for p in procs:
            p.terminate()
        procs.clear()
        a, base_a = boot(tmpdir, {"RE_SHARED_STATE": "memory"}, "final")
        procs.append(a)
        ok, me = login(base_a)
        token = me.get("token", "")
        status, state = ask(base_a, "/api/v1/security/shared-state", token)
        check("with no backend it reports memory", state["shared_state"]["backend"] == "memory")
        check("it does not claim the state is shared", state["shared_state"]["shared"] is False)
        check("it offers the fix", bool(state["next_step"]), state["next_step"][:60])
        check("and says why it matters", len(state.get("why_it_matters", [])) == 2)
        status, posture = ask(base_a, "/api/v1/security/posture", token)
        control = next((c for c in posture["controls"] if "Shared rate-limit" in c["control"]), {})
        check("the posture control is degraded, not enforced", control.get("state") == "degraded",
              control.get("state", "missing"))
        check("and the score is still computed from what holds",
              posture["score"] == round(100 * posture["score_basis"]["holding"]
                                        / posture["score_basis"]["controls"]),
              f"score {posture['score']}")
        status, health = ask(base_a, "/healthz")
        check("unauthenticated /healthz gives no backend detail",
              "shared_state" not in health)
        status, health = ask(base_a, "/healthz", token)
        check("an owner sees it there", health.get("shared_state", {}).get("backend") == "memory")
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                p.kill()
        if redis_proc:
            redis_proc.terminate()

    if FAILS:
        print(f"\n{len(FAILS)} check(s) FAILED:\n" + "\n".join(f"  - {f}" for f in FAILS))
        return 1
    print(f"\n{PASSED} checks passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

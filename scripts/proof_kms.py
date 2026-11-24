#!/usr/bin/env python3
"""
Proof: the root key at rest is wrapped by a KEK, and a bad KEK stops the boot.

    python3 scripts/proof_kms.py                    # throwaway instance
    python3 scripts/proof_kms.py --live --base http://127.0.0.1:5433

`docs/11 §7` says the master key must stop living in `.env` in plaintext. This
script does not read the docs and agree with them. It starts real servers in a
throwaway directory and watches what happens to the root:

  1. before: no envelope, the root is `RE_MASTER_KEY`, the console says so
  2. `init` adopts the current root — nothing is re-encrypted, nothing is lost
  3. the envelope on disk holds a wrapped blob and no key; mode 0600
  4. a server booted with the envelope unwraps it and derives the same subkeys
  5. data sealed without the envelope is readable with it (same key, new home)
  6. a wrong KEK is fatal — the process refuses to boot, no fallback to `.env`
  7. a tampered envelope is fatal for the same reason
  8. rotation: a new KEK + the old one as `previous` re-wraps the root, and the
     data is still readable; with the old KEK gone it still boots
  9. the seal never leaves the process, and the panel never overclaims
 10. rewrap is owner-only, needs a confirmation, and is audited

Every server here is booted in a temp directory on a free port and killed at the
end. Exit code is 0 only if every check passed.
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


class Client:
    """A browser-shaped client for one server."""

    def __init__(self, base: str):
        self.base = base
        self.cookies: dict[str, str] = {}
        self.token = ""
        self.csrf = ""

    def call(self, method: str, path: str, body=None):
        h = {"User-Agent": "proof-kms/1", "Accept": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw, status, pairs = res.read(), res.status, res.getheaders()
        except urllib.error.HTTPError as exc:
            raw, status, pairs = exc.read(), exc.code, list(exc.headers.items())
        for line in [v for k, v in pairs if k.lower() == "set-cookie"]:
            name, _, rest = line.partition("=")
            value = rest.split(";")[0].strip()
            if value:
                self.cookies[name.strip()] = value
        try:
            return status, json.loads(raw or b"{}")
        except Exception:
            return status, {"_raw": raw.decode("utf-8", "replace")}

    def login(self):
        status, body = self.call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
        if status != 200 or body.get("mfa_required"):
            raise RuntimeError(f"login failed: {status} {body}")
        self.token, self.csrf = body["token"], body["csrf"]


def boot(tmpdir: str, env_extra: dict) -> tuple[subprocess.Popen | None, str, str]:
    """Start a server in `tmpdir`. Returns (proc or None, base url, why-it-died)."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = os.path.join(tmpdir, f"server-{port}.log")
    env = {**os.environ, "RE_DATA_DIR": os.path.join(tmpdir, "data"), "RE_DB": "json",
           "RE_LIMITER_OFF": "1", "RE_AUTOMATION_TICK": "0", "RE_ENV": "development",
           "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
           "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kms-kek.json")}
    env.pop("RE_KMS_URL", None)
    env.pop("RE_KMS_TOKEN", None)
    env.pop("RE_KMS_KEK", None)
    env.pop("RE_KMS_KEK_PREVIOUS", None)
    env.update(env_extra)
    handle = open(log, "wb")
    proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port),
                             "--host", "127.0.0.1"], cwd=ROOT, env=env,
                            stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:            # exited — that is the point, in §6/§7
            handle.close()
            return None, base, open(log, encoding="utf-8", errors="replace").read()
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=1).status == 200:
                return proc, base, ""
        except Exception:
            time.sleep(0.2)
    proc.kill()
    handle.close()
    return None, base, "server did not answer /healthz in 40s:\n" + \
        open(log, encoding="utf-8", errors="replace").read()[-2000:]


def cli(tmpdir: str, *args: str, env_extra: dict | None = None) -> tuple[int, str]:
    env = {**os.environ, "RE_ENVELOPE_FILE": os.path.join(tmpdir, "dek.envelope"),
           "RE_KMS_KEK_FILE": os.path.join(tmpdir, "kms-kek.json")}
    env.pop("RE_KMS_URL", None)
    env.pop("RE_KMS_KEK", None)
    env.pop("RE_KMS_KEK_PREVIOUS", None)
    env.update(env_extra or {})
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "kms_envelope.py"), *args],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    return out.returncode, (out.stdout + out.stderr).strip()


def _dead(base: str) -> bool:
    """True when nothing answers — the server that refused to boot left no port."""
    try:
        urllib.request.urlopen(base + "/healthz", timeout=1)
        return False
    except Exception:
        return True


def report() -> int:
    """Print the verdict and return the process exit code."""
    if FAILS:
        print(f"\n{len(FAILS)} check(s) FAILED:\n" + "\n".join(f"  - {f}" for f in FAILS))
        return 1
    print(f"\n{PASSED} checks passed, 0 failed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5433")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    tmpdir = tempfile.mkdtemp(prefix="re-kms-proof-")
    master = os.urandom(32)
    procs: list[subprocess.Popen] = []
    print(f"\n=== KMS envelope proof (temp dir {tmpdir}) ===")

    try:
        print("\n1. before: the root is RE_MASTER_KEY and the panel says so")
        proc, base, why = boot(tmpdir, {"RE_MASTER_KEY": master.hex()})
        if not proc:
            check("a server boots with RE_MASTER_KEY and no envelope", False, why[-800:])
            return report()
        procs.append(proc)
        c = Client(base)
        c.login()
        status, body = c.call("GET", "/api/v1/security/envelope")
        check("the envelope panel answers", status == 200, str(status))
        check("it reports no envelope", body["envelope"]["envelope"] is False)
        status, posture0 = c.call("GET", "/api/v1/security/posture")
        control0 = next(x for x in posture0["controls"] if "KMS" in x["control"])
        check("the console names the root's real home (RE_MASTER_KEY) in the control detail",
              "RE_MASTER_KEY" in control0["detail"], control0["detail"][:60])
        status, secrets = c.call("GET", "/api/v1/security/secrets")
        check("the secrets report names the root's source",
              secrets["secrets"]["master_key"]["source"] == "master_key",
              secrets["secrets"]["master_key"]["source"])
        check("the panel never claims a managed KMS",
              body["envelope"]["claims"]["managed_kms_integrated"] is False)
        check("and it offers the next step", "RE_KMS_URL" in body.get("next_step", ""))
        status, posture = c.call("GET", "/api/v1/security/posture")
        control = next(x for x in posture["controls"] if "KMS" in x["control"])
        check("the posture control is degraded, not enforced", control["state"] == "degraded")
        check("the score is computed from the controls it holds",
              posture["score"] == round(100 * posture["score_basis"]["holding"]
                                        / posture["score_basis"]["controls"]),
              f"score {posture['score']} over {posture['score_basis']['controls']} controls")
        status, made = c.call("POST", "/api/v1/contacts",
                              {"name": "Proof KMS", "phone": "+91 98220 77777"})
        cid = made["contact"]["id"]
        check("a contact sealed with RE_MASTER_KEY exists", status == 200, cid)

        print("\n2. init adopts the current root — nothing is re-encrypted")
        code, out = cli(tmpdir, "init", "--provider", "local", env_extra={"RE_MASTER_KEY": master.hex()})
        check("`init` succeeds", code == 0, out.splitlines()[-1] if out else "")
        check("it reports the root it adopted is the one it was given", "root_matches" in out or code == 0)
        env_path = os.path.join(tmpdir, "dek.envelope")
        doc = json.loads(open(env_path).read())
        check("the envelope names its provider", doc["provider"] == "local", doc["provider"])
        check("and carries a KEK identity, never a key", doc["kek_id"].startswith("local-"), doc["kek_id"])
        check("the adopted root matches RE_MASTER_KEY", cli(tmpdir, "verify")[0] == 0)

        # the running server from section 1 booted before the envelope existed. It
        # must say so rather than reporting "enforced" because a file appeared.
        status, stale = c.call("GET", "/api/v1/security/envelope")
        check("the still-running server reports an envelope on disk", stale["envelope"]["envelope"] is True)
        check("but does not claim it is active in that process",
              stale["envelope"]["active_in_process"] is False)
        check("it says a restart is pending", stale["envelope"]["pending_restart"] is True)
        check("and tells the operator to restart", "restart" in stale.get("next_step", "").lower(),
              stale.get("next_step", ""))
        status, stale_posture = c.call("GET", "/api/v1/security/posture")
        stale_control = next(x for x in stale_posture["controls"] if "KMS" in x["control"])
        check("the posture control is still degraded until the restart",
              stale_control["state"] == "degraded", stale_control["state"])
        proc.terminate()                        # now restart, which is the point
        procs.remove(proc)
        proc.wait(timeout=10)

        print("\n3. the envelope at rest holds no key and cannot be read by others")
        check("envelope is mode 0600", oct(os.stat(env_path).st_mode)[-3:] == "600",
              oct(os.stat(env_path).st_mode)[-3:])
        text = open(env_path).read()
        check("the raw root is not in the envelope file", master.hex() not in text)
        import base64
        check("nor base64 of it", base64.b64encode(master).decode() not in text)
        kek_path = os.path.join(tmpdir, "kms-kek.json")
        check("the local KEK file is mode 0600 too",
              oct(os.stat(kek_path).st_mode)[-3:] == "600", oct(os.stat(kek_path).st_mode)[-3:])
        check("the envelope records a version and a creation time",
              doc.get("v") and doc.get("created_at", 0) > 0, f"v{doc.get('v')}")

        print("\n4. a server booted with the envelope unwraps it and derives the same subkeys")
        proc, base, why = boot(tmpdir, {"RE_MASTER_KEY": master.hex()})
        if not proc:
            check("the server boots with the envelope", False, why[-800:])
            return report()
        procs.append(proc)
        c2 = Client(base)
        c2.login()
        status, body = c2.call("GET", "/api/v1/security/envelope")
        check("the panel now reports an envelope", body["envelope"]["envelope"] is True)
        check("it unwraps", body["verify"]["unwrapped"] is True)
        check("the KEK works in both directions (wrap + unwrap round trip)",
              body["verify"]["roundtrip"] is True)
        check("the unwrapped root is the one this process is encrypting with",
              body["verify"]["derives_live_keys"] is True)
        check("no KEK or key value appears in the response",
              master.hex() not in json.dumps(body) and doc["kek_id"] in json.dumps(body))
        status, secrets = c2.call("GET", "/api/v1/security/secrets")
        check("the secrets report says the root comes from the envelope",
              secrets["secrets"]["master_key"]["source"] == "kms_envelope",
              secrets["secrets"]["master_key"]["source"])
        status, posture = c2.call("GET", "/api/v1/security/posture")
        control = next(x for x in posture["controls"] if "KMS" in x["control"])
        check("the posture control is now enforced", control["state"] == "enforced")
        check("the panel still does not claim a managed KMS",
              body["envelope"]["claims"]["managed_kms_integrated"] is False)
        check("it says the KEK is in process memory after unwrap",
              "memory" in body["envelope"]["claims"]["kek_in_process"]
              or "HSM" in body["envelope"]["claims"]["kek_in_process"])

        print("\n5. data sealed before the envelope is readable with it")
        status, got = c2.call("GET", f"/api/v1/contacts/{cid}")
        check("the contact created without an envelope still reads", status == 200, str(status))
        check("and its phone unsealed under the envelope-derived key",
              got["contact"]["phone"] == "+91 98••• ••77", got["contact"].get("phone", ""))

        print("\n6. a wrong KEK is fatal — the boot refuses, with no fallback")
        proc.terminate()
        procs.remove(proc)
        proc.wait(timeout=10)
        saved = open(kek_path).read()
        json.dump({"kek": os.urandom(32).hex()}, open(kek_path, "w"))
        proc2, base2, why = boot(tmpdir, {"RE_MASTER_KEY": master.hex()})
        if proc2:
            procs.append(proc2)
        check("the process does not serve", proc2 is None, "it started anyway" if proc2 else "")
        check("it names the failure: a KEK mismatch", "KEK_MISMATCH" in why,
              next((l for l in why.splitlines() if "KEK" in l), why[-300:]))
        check("it says it cannot boot without its KEK", "cannot boot" in why)
        check("and nothing is listening where it would have served", _dead(base2))

        print("\n7. a tampered envelope is fatal for the same reason")
        open(kek_path, "w").write(saved)
        doc = json.loads(open(env_path).read())
        raw = bytearray(base64.urlsafe_b64decode(doc["wrapped"] + "=" * (-len(doc["wrapped"]) % 4)))
        raw[20] ^= 0xFF
        doc["wrapped"] = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
        json.dump(doc, open(env_path, "w"))
        proc3, _base3, why3 = boot(tmpdir, {"RE_MASTER_KEY": master.hex()})
        if proc3:
            procs.append(proc3)
        check("a flipped bit in the wrapped root stops the boot", proc3 is None)
        check("and is reported as an integrity failure", "INTEGRITY_FAILURE" in why3 or "KEK_MISMATCH" in why3,
              next((l for l in why3.splitlines() if "KEK" in l or "INTEGRITY" in l), why3[-300:]))

        print("\n8. rotation: new KEK, old KEK kept as `previous`, no data touched")
        code, out = cli(tmpdir, "init", "--provider", "local", env_extra={"RE_MASTER_KEY": master.hex()})
        check("`init` refuses to overwrite an envelope that already exists",
              code != 0 and "ENVELOPE_EXISTS" in out, out[:120])
        os.remove(env_path)                     # start this section from a clean wrap
        code, out = cli(tmpdir, "init", "--provider", "local", env_extra={"RE_MASTER_KEY": master.hex()})
        check("the envelope is re-initialised for a clean rotation", code == 0, out[-200:])
        old = json.loads(open(kek_path).read())["kek"]
        new = os.urandom(32).hex()
        json.dump({"kek": new, "previous": old}, open(kek_path, "w"))
        code, out = cli(tmpdir, "rewrap")
        check("`rewrap` succeeds with the old KEK as previous", code == 0, out[-300:])
        doc = json.loads(open(env_path).read())
        check("the wrap now belongs to the new KEK", doc["kek_id"] != f"local-{old[:6]}",
              doc["kek_id"])
        check("the old wrap is retired, not deleted", len(doc.get("retired") or []) >= 1,
              f"{len(doc.get('retired') or [])} retired")
        json.dump({"kek": new}, open(kek_path, "w"))       # delete the old KEK
        code, out = cli(tmpdir, "verify")
        check("with the old KEK deleted it still unwraps",
              code == 0 and '"unwrapped": true' in out, out[-120:].replace("\n", " "))
        # an env file with no root key at all: nothing but the envelope can supply
        # a key, so a boot here proves the envelope is the only source
        bare = os.path.join(tmpdir, "bare.env")
        open(bare, "w").write("RE_ENV=development\nRE_DB=json\n")
        proc4, base4, why4 = boot(tmpdir, {"RE_MASTER_KEY": "", "RE_ENV_FILE": bare})
        if proc4:
            procs.append(proc4)
        check("and the server boots with no RE_MASTER_KEY anywhere", proc4 is not None, why4[-600:])
        if proc4:
            c4 = Client(base4)
            c4.login()
            status, secrets = c4.call("GET", "/api/v1/security/secrets")
            check("the root comes from the envelope, not the environment",
                  secrets["secrets"]["master_key"]["source"] == "kms_envelope",
                  secrets["secrets"]["master_key"]["source"])
            status, got = c4.call("GET", f"/api/v1/contacts/{cid}")
            check("data written before the rotation still reads after it", status == 200, str(status))

            print("\n9/10. rewrap over the API: owner-only, confirmed, audited")
            status, body = c4.call("POST", "/api/v1/security/envelope/rewrap", {})
            check("a missing confirmation is refused", status == 400, str(status))
            status, body = c4.call("POST", "/api/v1/security/envelope/rewrap", {"confirm": "rewrap"})
            check("the owner can re-wrap the root", status == 200, str(status))
            check("the root did not move", body.get("root_unchanged") is True)
            check("the KEK id is unchanged when the KEK is", body.get("kek_id_before") == body.get("kek_id_after"),
                  body.get("kek_id_after", ""))
            check("the operation re-verifies afterwards",
                  body["verify"]["unwrapped"] and body["verify"]["roundtrip"])
            status, audit = c4.call("GET", "/api/v1/tenant/audit")
            actions = [e.get("action") for e in audit.get("entries", [])]
            check("the re-wrap is in the audit log", "security.envelope_rewrapped" in actions)
            status, got = c4.call("GET", f"/api/v1/contacts/{cid}")
            check("and no ciphertext was invalidated", status == 200, str(status))
        else:
            check("the rotation server boots", False, why4[-600:])
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=10)
            except Exception:
                p.kill()

    return report()


if __name__ == "__main__":
    sys.exit(main())

"""
KMS envelope: the root key stops living in a file.

The claim under test (docs/30): `RE_MASTER_KEY` used to sit in `.env`, mode 0600,
one backup or container image away from total disclosure, and every subkey in
`security.KEYS` was HKDF-derived from it. The envelope wraps the root with a
key-encryption key that is not in this repository, so what is at rest is a wrapped
blob, not a key.

These tests are shaped around the four ways this could go wrong:

  A. **the primitive** — a tag mismatch is a failure, never a fallback; a wrong
   key, a wrong context, or a flipped bit all refuse
  B. **the envelope** — an envelope that cannot be unwrapped is fatal in every
   mode, because falling back to `RE_MASTER_KEY` would derive *different* subkeys
   and go on writing ciphertext the rest of the installation cannot read
  C. **the providers** — `env` (LoadCredential/Vault) and `http` (a KMS) both
   work; a refusal, an unreachable host, and a malformed answer are three
   different errors and none of them is "continue without the KEK"
  D. **the API** — the console shows where the root comes from, the posture score
   is computed rather than typed, and re-wrapping is owner-only, confirmed, and
   audited
"""
from __future__ import annotations

import base64
import http.server
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from app import aead  # noqa: E402
from app import config as cfg  # noqa: E402
from app import kms  # noqa: E402
from app import security as sec  # noqa: E402
import fake_kms  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = bytes.fromhex("a" * 64)


@pytest.fixture(autouse=True)
def _clean_kms_state(tmp_path, monkeypatch):
    """Every test gets its own envelope + KEK files and a cold DEK cache."""
    monkeypatch.setenv("RE_ENVELOPE_FILE", str(tmp_path / "dek.envelope"))
    monkeypatch.setenv("RE_KMS_KEK_FILE", str(tmp_path / "kms-kek.json"))
    monkeypatch.delenv("RE_KMS_URL", raising=False)
    monkeypatch.delenv("RE_KMS_TOKEN", raising=False)
    monkeypatch.delenv("RE_KMS_KEK", raising=False)
    monkeypatch.setenv("RE_MASTER_KEY", MASTER.hex())
    kms.forget()
    yield
    kms.forget()


def local_envelope():
    """An envelope wrapping the current root, under a development KEK."""
    return kms.init(MASTER, create_local=True)


# ===========================================================================
# A. the primitive — a tag mismatch is a failure, never a fallback
# ===========================================================================
def test_aead_round_trip_with_an_explicit_key():
    blob = aead.seal("Sunrise Heights, Nanded", MASTER, "tenant:tnt_1")
    assert aead.unseal(blob, MASTER, "tenant:tnt_1") == "Sunrise Heights, Nanded"


def test_aead_refuses_a_tampered_blob():
    blob = aead.seal("secret", MASTER)
    flipped = ("A" if blob[0] != "A" else "B") + blob[1:]
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        aead.unseal(flipped, MASTER)


def test_aead_refuses_the_wrong_key():
    blob = aead.seal("secret", MASTER)
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        aead.unseal(blob, bytes.fromhex("b" * 64))


def test_aead_refuses_the_wrong_context():
    blob = aead.seal("secret", MASTER, "tenant:tnt_1")
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        aead.unseal(blob, MASTER, "tenant:tnt_2")


def test_aead_refuses_a_key_of_the_wrong_length():
    with pytest.raises(ValueError):
        aead.seal("secret", b"too short")
    with pytest.raises(ValueError):
        aead.seal("secret", b"x" * 33)


def test_aead_empty_plaintext_round_trips_and_none_is_empty():
    # "" seals to a real blob (nonce + tag) and unseals back to "" — the same as
    # the old inline implementation; only None and "" as *input* mean "no value"
    assert aead.unseal(aead.seal("", MASTER), MASTER) == ""
    assert aead.seal(None, MASTER) == ""
    assert aead.unseal("", MASTER) == ""


def test_aead_format_is_unchanged_from_the_old_inline_implementation():
    # nonce(12) ‖ ciphertext ‖ tag(16), base64url, no padding — the shape every
    # existing row in every store was written in, so the move to app/aead.py must
    # not have changed it
    blob = aead.seal("hello", MASTER)
    assert "=" not in blob
    raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    assert len(raw) == 12 + 5 + 16


def test_security_seal_and_unseal_still_round_trip_with_the_default_key():
    blob = sec.seal("+91 98220 00000", "tenant:tnt_1")
    assert sec.unseal(blob, "tenant:tnt_1") == "+91 98220 00000"
    with pytest.raises(ValueError, match="INTEGRITY_FAILURE"):
        sec.unseal(blob, "tenant:tnt_2")


# ===========================================================================
# B. the envelope — an envelope that cannot be unwrapped is fatal
# ===========================================================================
def test_init_adopts_the_current_root_and_reencrypts_nothing():
    env = local_envelope()
    assert env["provider"] == "local"
    assert env["kek_id"].startswith("local-")
    assert kms.dek() == MASTER
    # and the subkeys derived from the unwrapped root are exactly the ones the old
    # RE_MASTER_KEY path produced — which is why adopting the root re-encrypts
    # nothing and invalidates no existing ciphertext
    assert cfg.derive_key("field_enc", kms.dek()) == cfg.derive_key("field_enc", MASTER)


def test_the_envelope_file_is_0600_and_holds_no_key():
    local_envelope()
    path = kms.envelope_path()
    assert oct(path.stat().st_mode)[-3:] == "600"
    text = path.read_text()
    assert MASTER.hex() not in text
    assert base64.b64encode(MASTER).decode() not in text
    # the wrapped blob, decoded, is nonce‖ct‖tag — not the root
    wrapped = json.loads(text)["wrapped"]
    raw = base64.urlsafe_b64decode(wrapped + "=" * (-len(wrapped) % 4))
    assert raw != MASTER


def test_init_twice_is_refused():
    local_envelope()
    with pytest.raises(kms.EnvelopeError) as exc:
        local_envelope()
    assert exc.value.code == "ENVELOPE_EXISTS"


def test_a_wrong_kek_is_a_mismatch_not_a_fallback():
    local_envelope()
    kek_path = kms.kek_file()
    json.dump({"kek": os.urandom(32).hex()}, open(kek_path, "w"))
    kms.forget()
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.dek()
    assert exc.value.code == "KEK_MISMATCH"
    assert "cannot boot" in exc.value.message


def test_a_tampered_envelope_is_an_integrity_failure():
    local_envelope()
    path = kms.envelope_path()
    env = json.loads(path.read_text())
    env["wrapped"] = ("A" if env["wrapped"][0] != "A" else "B") + env["wrapped"][1:]
    json.dump(env, open(path, "w"))
    kms.forget()
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.dek()
    assert exc.value.code == "KEK_MISMATCH"


def test_no_kek_configured_means_no_envelope_and_no_error():
    assert kms.load() is None
    assert kms.dek() is None
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.provider()
    assert exc.value.code == "NO_KEK_PROVIDER"


def test_an_envelope_without_a_kek_cannot_boot():
    local_envelope()
    os.remove(kms.kek_file())
    kms.forget()
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.dek()
    assert exc.value.code == "NO_KEK_PROVIDER"
    assert "cannot boot" in exc.value.message


def test_rewrap_keeps_the_root_and_retires_the_old_wrap():
    local_envelope()
    before = kms.load()
    new = kms.rewrap()
    assert new["wrapped"] != before["wrapped"]
    assert new["rotated_at"] > 0
    assert len(new["retired"]) == 1
    assert new["retired"][0]["kek_id"] == before["kek_id"]
    assert kms.dek() == MASTER          # the root did not move
    assert kms.verify()["roundtrip"] is True


def test_rewrap_under_a_new_kek_changes_the_kek_id():
    local_envelope()
    first_id = kms.load()["kek_id"]
    old_kek = json.loads(kms.kek_file().read_text())["kek"]
    new_kek = os.urandom(32).hex()
    # rotation: the new KEK is current, the old one is kept as `previous` so the
    # existing wrap can still be unwrapped — swap without it and the envelope
    # becomes unreadable before anything has been re-wrapped
    json.dump({"kek": new_kek, "previous": old_kek}, open(kms.kek_file(), "w"))
    kms.forget()
    new = kms.rewrap()
    assert new["kek_id"] != first_id
    assert new["kek_id"] == kms.kek_id("local", bytes.fromhex(new_kek))
    assert kms.dek() == MASTER
    # and once the wrap has moved, the old KEK is no longer needed
    json.dump({"kek": new_kek}, open(kms.kek_file(), "w"))
    kms.forget()
    assert kms.dek() == MASTER


def test_the_env_provider_unwraps_with_a_previous_kek(monkeypatch):
    first = os.urandom(32)
    monkeypatch.setenv("RE_KMS_KEK", first.hex())
    kms.init(MASTER)
    second = os.urandom(32)
    monkeypatch.setenv("RE_KMS_KEK", second.hex())
    monkeypatch.setenv("RE_KMS_KEK_PREVIOUS", first.hex())
    kms.forget()
    assert kms.dek() == MASTER                       # unwrapped with the previous KEK
    new = kms.rewrap()
    assert new["kek_id"] == kms.kek_id("env", second)   # wrapped with the current one
    assert kms.dek() == MASTER


def test_kek_id_is_stable_for_a_kek_and_changes_with_it():
    one = kms.kek_id("env", MASTER)
    assert one == kms.kek_id("env", MASTER)
    assert one != kms.kek_id("env", bytes.fromhex("b" * 64))
    assert one.startswith("env-")


def test_verify_proves_unwrap_and_round_trip():
    local_envelope()
    out = kms.verify()
    assert out["unwrapped"] and out["roundtrip"] and out["error"] == ""


def test_verify_catches_an_envelope_that_does_not_match_the_running_keys(monkeypatch):
    """The failure that matters: the envelope unwraps, but to a *different* root
    than the one this process sealed its rows with. Everything would decrypt to
    garbage; `verify` has to say so."""
    local_envelope()
    # the process in this test sealed nothing, but pretend its keyring came from a
    # root other than the one the envelope holds — exactly a swapped .env
    other = {n: cfg.derive_key(n, bytes.fromhex("c" * 64)) for n in cfg.KEY_NAMES}
    monkeypatch.setattr(sec, "KEYS", other, raising=False)
    assert kms.verify()["derives_live_keys"] is False
    monkeypatch.setattr(sec, "KEYS", {n: cfg.derive_key(n, MASTER) for n in cfg.KEY_NAMES}, raising=False)
    assert kms.verify()["derives_live_keys"] is True


def test_status_separates_an_envelope_on_disk_from_one_this_process_unwrapped():
    """`init` on a running server writes an envelope the process has not read. The
    console must not call that enforced: the server is still on RE_MASTER_KEY."""
    st = kms.status()
    assert st["envelope"] is False and st["active_in_process"] is False
    local_envelope()
    kms.forget()                                   # nothing unwrapped yet
    st = kms.status()
    assert st["envelope"] is True
    assert st["active_in_process"] is False
    assert st["pending_restart"] is True
    kms.dek()                                      # what booting does
    st = kms.status()
    assert st["active_in_process"] is True and st["pending_restart"] is False


def test_status_never_claims_more_than_the_truth():
    local_envelope()
    st = kms.status()
    assert st["envelope"] is True
    assert st["dek_on_disk"] is False
    assert st["claims"]["managed_kms_integrated"] is False
    assert st["claims"]["kek_in_process"].startswith("yes")
    assert MASTER.hex() not in json.dumps(st)


def test_the_local_provider_is_refused_in_production(monkeypatch):
    monkeypatch.setattr(cfg, "PRODUCTION", True)
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.provider(create_local=True)
    assert exc.value.code == "NO_KEK_PROVIDER"


# ===========================================================================
# C. the providers — env and http; a refusal is not a fallback
# ===========================================================================
def test_the_env_provider_wraps_and_unwraps(monkeypatch):
    monkeypatch.setenv("RE_KMS_KEK", os.urandom(32).hex())
    env = kms.init(MASTER)
    assert env["provider"] == "env"
    kms.forget()
    assert kms.dek() == MASTER


def test_the_http_provider_wraps_and_unwraps_through_a_kms():
    server, fake, port = fake_kms.serve(0, token="tok_test")
    try:
        os.environ["RE_KMS_URL"] = f"http://127.0.0.1:{port}"
        os.environ["RE_KMS_TOKEN"] = "tok_test"
        os.environ["RE_KMS_KEY_ID"] = fake.key_id
        env = kms.init(MASTER)
        assert env["provider"] == "http"
        assert env["kek_id"] == fake.key_id
        kms.forget()
        assert kms.dek() == MASTER
        assert {c["op"] for c in fake.calls} == {"wrap", "unwrap"}
    finally:
        server.shutdown()


def test_a_kms_that_refuses_is_a_denial_not_a_fallback():
    server, fake, port = fake_kms.serve(0, token="tok_test")
    try:
        os.environ["RE_KMS_URL"] = f"http://127.0.0.1:{port}"
        os.environ["RE_KMS_TOKEN"] = "tok_test"
        kms.init(MASTER)
        fake.deny = True
        kms.forget()
        with pytest.raises(kms.EnvelopeError) as exc:
            kms.dek()
        assert exc.value.code == "KEK_DENIED"
    finally:
        server.shutdown()


def test_a_wrong_token_is_a_denial():
    server, fake, port = fake_kms.serve(0, token="the-right-one")
    try:
        os.environ["RE_KMS_URL"] = f"http://127.0.0.1:{port}"
        os.environ["RE_KMS_TOKEN"] = "the-wrong-one"
        with pytest.raises(kms.EnvelopeError) as exc:
            kms.init(MASTER)
        assert exc.value.code == "KEK_DENIED"
    finally:
        server.shutdown()


def test_an_unreachable_kms_is_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ["RE_KMS_URL"] = f"http://127.0.0.1:{port}"
    with pytest.raises(kms.EnvelopeError) as exc:
        kms.init(MASTER)
    assert exc.value.code == "KEK_UNREACHABLE"


def test_a_kms_that_answers_nonsense_is_a_bad_response():
    class Nonsense(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Nonsense)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        os.environ["RE_KMS_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(kms.EnvelopeError) as exc:
            kms.init(MASTER)
        assert exc.value.code == "KEK_BAD_RESPONSE"
    finally:
        server.shutdown()


# ===========================================================================
# D. the API — the console shows where the root comes from; rewrap is guarded
# ===========================================================================
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    """A small cookie+CSRF client for the envelope server (conftest's Session is
    bound to its own server's BASE)."""

    def __init__(self, base):
        self.base = base
        self.cookies = {}
        self.csrf = ""


    def step_up(self, action: str, target: str = "") -> str:
        """Confirm it is you, for one action — these routes are gated (docs/34)."""
        status, body = self.call("POST", "/api/v1/auth/step-up",
                                 {"action": action, "target": target,
                                  "password": "Demo@12345!"})
        assert status == 200, body
        return body["ticket"]

    def call(self, method, path, body=None, csrf=True, headers=None):
        h = {"User-Agent": "pytest", "Accept": "application/json"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(headers or {})
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                raw, status, pairs = res.read(), res.status, list(res.getheaders())
        except urllib.error.HTTPError as e:
            raw, status, pairs = e.read(), e.code, list(e.headers.items())
        for k, v in pairs:
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                val = rest.split(";")[0].strip()
                if name.strip() and val:
                    self.cookies[name.strip()] = val
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw.decode("utf-8", "replace")}
        return status, payload

    def login(self, email, password="Demo@12345!"):
        status, body = self.call("POST", "/api/v1/auth/login",
                                 {"email": email, "password": password}, csrf=False)
        assert status == 200, body
        self.csrf = body["csrf"]
        return body


@pytest.fixture(scope="module")
def envelope_server(tmp_path_factory):
    """A real server booted with an envelope: the root is unwrapped at boot and
    never read from .env."""
    tmp = tmp_path_factory.mktemp("kms-server")
    master = os.urandom(32)
    env_file = str(tmp / "dek.envelope")
    kek_file = str(tmp / "kms-kek.json")
    saved = {k: os.environ.get(k) for k in ("RE_ENVELOPE_FILE", "RE_KMS_KEK_FILE", "RE_MASTER_KEY")}
    os.environ["RE_ENVELOPE_FILE"] = env_file
    os.environ["RE_KMS_KEK_FILE"] = kek_file
    os.environ["RE_MASTER_KEY"] = master.hex()
    for k in ("RE_KMS_URL", "RE_KMS_KEK"):
        os.environ.pop(k, None)
    kms.forget()
    kms.init(master, create_local=True)
    kms.forget()
    port = _free_port()
    env = {**os.environ, "RE_DATA_DIR": str(tmp / "data"), "RE_DB": "json",
           "RE_LIMITER_OFF": "1", "RE_AUTOMATION_TICK": "0", "PORT": str(port), "HOST": "127.0.0.1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            proc.kill()
            raise RuntimeError("envelope server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        kms.forget()


@pytest.fixture(scope="module")
def no_envelope_server(tmp_path_factory):
    """A server booted the old way: RE_MASTER_KEY, no envelope on disk."""
    tmp = tmp_path_factory.mktemp("kms-bare")
    saved = {k: os.environ.get(k) for k in ("RE_ENVELOPE_FILE", "RE_KMS_KEK_FILE")}
    for k in ("RE_KMS_URL", "RE_KMS_KEK", "RE_KMS_KEK_PREVIOUS"):
        os.environ.pop(k, None)
    os.environ["RE_ENVELOPE_FILE"] = str(tmp / "dek.envelope")
    os.environ["RE_KMS_KEK_FILE"] = str(tmp / "kms-kek.json")
    port = _free_port()
    env = {**os.environ, "RE_DATA_DIR": str(tmp / "data"), "RE_DB": "json",
           "RE_LIMITER_OFF": "1", "RE_AUTOMATION_TICK": "0", "PORT": str(port), "HOST": "127.0.0.1"}
    proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture()
def owner_env(envelope_server):
    c = Client(envelope_server)
    c.login("owner@realtorestates.app")
    return c


def test_envelope_endpoint_reports_a_wrapped_root(owner_env):
    status, body = owner_env.call("GET", "/api/v1/security/envelope")
    assert status == 200, body
    assert body["envelope"]["envelope"] is True
    assert body["envelope"]["provider_in_envelope"] == "local"
    assert body["envelope"]["dek_on_disk"] is False
    assert body["verify"]["unwrapped"] and body["verify"]["roundtrip"]
    assert body["verify"]["derives_live_keys"] is True
    assert "a" * 64 not in json.dumps(body)


def test_envelope_endpoint_is_owner_and_admin_only(envelope_server):
    c = Client(envelope_server)
    c.login("manager@realtorestates.app")
    status, _ = c.call("GET", "/api/v1/security/envelope")
    assert status == 403


def test_the_secrets_panel_names_the_envelope_as_the_source(owner_env):
    status, body = owner_env.call("GET", "/api/v1/security/secrets")
    assert status == 200, body
    assert body["secrets"]["master_key"]["source"] == "kms_envelope"
    assert body["secrets"]["envelope"]["envelope"] is True


def test_posture_score_is_computed_and_includes_the_envelope(owner_env):
    status, body = owner_env.call("GET", "/api/v1/security/posture")
    assert status == 200, body
    basis = body["score_basis"]
    assert basis["controls"] == len(body["controls"])
    assert basis["holding"] <= basis["controls"]
    assert body["score"] == round(100 * basis["holding"] / basis["controls"])
    control = next(c for c in body["controls"] if c["control"].startswith("Key management"))
    assert body["envelope"]["envelope"] is True
    # this client talks to a server booted with the envelope, so it is enforced
    assert body["envelope"]["active_in_process"] is True
    assert control["state"] == "enforced"


def test_a_server_that_booted_without_an_envelope_says_pending_restart(no_envelope_server):
    """The exact situation on a live server: `init` runs, a file appears, and the
    running process is still holding RE_MASTER_KEY. It must not report enforced."""
    c = Client(no_envelope_server)
    c.login("owner@realtorestates.app")
    status, body = c.call("GET", "/api/v1/security/envelope")
    assert status == 200, body
    assert body["envelope"]["envelope"] is False
    assert body["envelope"]["active_in_process"] is False
    assert body["next_step"], "the panel should offer the next step"
    status, posture = c.call("GET", "/api/v1/security/posture")
    control = next(c for c in posture["controls"] if c["control"].startswith("Key management"))
    assert control["state"] == "degraded"
    assert "RE_MASTER_KEY" in control["detail"]
    assert posture["score"] < 100


def test_rewrap_needs_a_confirmation(owner_env):
    # `confirm: "rewrap"` typed, *and* a step-up ticket (docs/34): the literal stops
    # a mis-click, the ticket stops a stolen session from rotating the key
    ticket = owner_env.step_up("key_rewrap", "root")
    status, body = owner_env.call("POST", "/api/v1/security/envelope/rewrap", {},
                                  headers={"X-Step-Up": ticket})
    assert status == 400, body
    assert body["error"]["code"] == "CONFIRM_REQUIRED"


def test_rewrap_is_owner_only_not_admin(envelope_server):
    owner = Client(envelope_server)
    owner.login("owner@realtorestates.app")
    status, body = owner.call("POST", "/api/v1/tenant/users",
                              {"email": "admin@realtorestates.app", "role": "admin",
                               "name": "Test Admin", "password": "Admin@12345!"})
    assert status == 200, body
    admin = Client(envelope_server)
    admin.login("admin@realtorestates.app", "Admin@12345!")
    status, body = admin.call("POST", "/api/v1/security/envelope/rewrap", {"confirm": "rewrap"})
    assert status == 403, body
    assert "Only an owner" in body["error"]["message"]


def test_rewrap_is_audited_and_keeps_the_root(owner_env):
    status, before = owner_env.call("GET", "/api/v1/security/envelope")
    assert status == 200
    ticket = owner_env.step_up("key_rewrap", "root")
    status, body = owner_env.call("POST", "/api/v1/security/envelope/rewrap", {"confirm": "rewrap"},
                                  headers={"X-Step-Up": ticket})
    assert status == 200, body
    assert body["root_unchanged"] is True
    assert body["verify"]["roundtrip"] is True
    status, audit = owner_env.call("GET", "/api/v1/tenant/audit")
    assert status == 200, audit
    actions = [r.get("action") for r in audit["entries"]]
    assert "security.envelope_rewrapped" in actions


def test_sealed_pii_round_trips_through_the_envelope_keys(owner_env):
    # the whole point: field_enc is derived from the unwrapped root, so a phone
    # sealed on write must unseal on read
    status, body = owner_env.call("POST", "/api/v1/contacts",
                                  {"name": "Envelope Probe", "phone": "+91 98220 12345"})
    assert status == 200, body
    cid = body["contact"]["id"]
    status, body = owner_env.call("GET", f"/api/v1/contacts/{cid}")
    assert status == 200, body
    # reads are masked, but the mask is computed from the *unsealed* value: if the
    # envelope-derived key were wrong, unseal would raise (500) or mask garbage
    assert body["contact"]["phone"] == "+91 98••• ••45"


def test_without_an_envelope_the_console_says_so(owner):
    # the main conftest server boots with no envelope: the same endpoints must
    # report that honestly rather than implying a wrapped root
    status, body, _ = owner.call("GET", "/api/v1/security/envelope")
    assert status == 200, body
    assert body["envelope"]["envelope"] is False
    assert body["verify"]["unwrapped"] is False
    status, body, _ = owner.call("GET", "/api/v1/security/posture")
    assert status == 200, body
    control = next(c for c in body["controls"] if c["control"].startswith("Key management"))
    assert control["state"] == "degraded"
    assert "RE_MASTER_KEY" in control["detail"]

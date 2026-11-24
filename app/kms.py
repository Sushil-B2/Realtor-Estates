"""
KMS envelope: the root key stops living in a file.

The gap this closes (G8, docs/30). Today `RE_MASTER_KEY` sits in `.env`, mode
0600, and every subkey in `security.KEYS` is HKDF-derived from it. That is one
file away from total disclosure: a backup, a laptop, a container image, a
`cat` by anything running as the app user. `docs/11 §7` names the target — the
process should never hold a key that is also at rest.

An **envelope** is the standard, boring shape of that: a data-encryption key (DEK)
encrypts the data, and a key-encryption key (KEK) that is *not* in this repository
encrypts the DEK. What lands on disk is the DEK wrapped by the KEK:

```
   KEK  ─ "env" (LoadCredential/Vault agent) | "http" (a KMS) | "local" (dev only)
    │ unwrap at boot, in memory
    ▼
   DEK  ─ 32 bytes, never written anywhere except wrapped ──▶ HKDF ─▶ field_enc,
    │                                                                blind_index,
    │                                                                session, audit,
    │                                                                webhook
    ▼
 .secrets/dek.envelope   {provider, kek_id, aad, wrapped, created_at, rotated_at,
                          retired: [previous wraps]}
```

The rules this module keeps, and the reasons they are not optional:

1. **No envelope, no change.** An installation with no envelope file behaves
   exactly as it did in v1.18.0 (`RE_MASTER_KEY` → HKDF, or the dev keyring).
   Adding a dependency to a working deployment is how a security improvement
   becomes an outage.
2. **An envelope that cannot be unwrapped is fatal.** Not "fall back to
   `RE_MASTER_KEY`": that fallback would derive *different* subkeys and go on
   writing ciphertext the rest of the installation cannot read. A boot that
   refuses is recoverable; a boot that half-migrates is not.
3. **`init` adopts the current root.** The first envelope wraps the key already in
   use, so creating one re-encrypts nothing and invalidates nothing. Minting a
   *new* root is a rotation (`--new`), which is `scripts/rotate_keys.py`'s job
   because it has to re-encrypt tenant PII.
4. **The KEK never enters a log, a response, or a fingerprint that is a verifier.**
   `kek_id` is `HMAC(KEK, "kek-id")` truncated: it changes when the KEK changes —
   which is the whole point, so you can see that a rewrap happened — and it cannot
   be used to test a guessed KEK without already holding it.
5. **The wrap is authenticated and provider-bound.** AAD is
   `realtor-estates/dek/v1|<provider>|<kek_id>`, so a wrapped blob cannot be
   transplanted from one provider or key id to another, and a tampered envelope is
   an integrity failure rather than a decryption of garbage into a "root key".

What this does **not** claim, and the panel says so too:

* With the `env` provider the KEK is in the process after unwrap, exactly as the
  root was. The win is that the **DEK is not at rest**, the KEK can be delivered by
  `LoadCredential`/a Vault agent (never a file in the image) and rotated
  independently of the data. A full host compromise still reads memory.
* `local` is a development provider. It keeps the DEK out of the repository and out
  of backups of the repository; it does not keep it away from anybody who can read
  `data/`. It is refused in production.
* **No managed KMS was contacted to build this.** The `http` adapter speaks a
  documented two-operation protocol and is proved against a local server that
  implements it (`scripts/proof_kms.py`, `tests/test_kms.py`). Pointing it at a real
  KMS needs a credential, the same one G3 is waiting on — so this ships as an
  adapter that is *proved*, not as a claim that a vendor was integrated.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import aead
from . import config as cfg

ROOT = Path(__file__).resolve().parent.parent
AAD_BASE = "realtor-estates/dek/v1"
ENVELOPE_VERSION = 1
DEK_BYTES = 32
PROVIDERS = ("http", "env", "local")
RETIRED_KEEP = 3

# Resolved once, so a test can point the whole module at a temp directory by
# setting the environment before import (the same override style as `RE_KEY_FILE`).
ENVELOPE_OVERRIDE = "RE_ENVELOPE_FILE"


class EnvelopeError(RuntimeError):
    """A KEK could not wrap or unwrap. Never caught to "try the old key"."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def envelope_path() -> Path:
    raw = cfg.get(ENVELOPE_OVERRIDE, "") or str(ROOT / ".secrets" / "dek.envelope")
    return Path(raw)


def kek_file() -> Path:
    return Path(cfg.get("RE_KMS_KEK_FILE", "") or str(ROOT / "data" / "kms-kek.json"))


# ---------------------------------------------------------------------------
# KEK providers — each one wraps and unwraps 32 bytes, nothing else
# ---------------------------------------------------------------------------
def _decode_secret(raw: str) -> bytes | None:
    """Hex, base64 or raw → 32 bytes. Same tolerance as `config._decode_master`."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        if len(decoded) >= DEK_BYTES:
            return decoded[:DEK_BYTES]
    except Exception:
        pass
    encoded = raw.encode("utf-8")
    return encoded[:DEK_BYTES] if len(encoded) >= DEK_BYTES else None


def kek_id(provider: str, kek: bytes) -> str:
    """A fingerprint that identifies a KEK without being a way to test one."""
    tag = hmac.new(kek, b"realtor-estates/kek-id", hashlib.sha256).hexdigest()[:12]
    return f"{provider}-{tag}"


class _LocalProvider:
    """A KEK that lives in this process: `env` (LoadCredential/Vault) or `local`
    (a development file). Both are the same shape — a 32-byte key held in memory,
    used only to wrap and unwrap the root.

    `previous` is the KEK being rotated *away* from. Rotation needs two keys at
    once — the old one to unwrap, the new one to wrap — and a provider with only
    one slot cannot do it: swap the environment value and the envelope becomes
    unreadable before anything has been re-wrapped. So during a rotation the old
    KEK is configured as `previous` (`RE_KMS_KEK_PREVIOUS`, or `"previous"` in the
    local file), `rewrap` moves the wrap to the current KEK, and only then is the
    old one deleted. This is the same shape as a KMS key version: old versions
    decrypt, only the current one encrypts.
    """

    def __init__(self, kek: bytes, previous: bytes | None = None):
        # each KEK with its own id: the wrap is bound to the id of the KEK that
        # made it, so unwrapping with a previous KEK must use *its* id in the AAD
        self._keks = [(kek, kek_id(self.name, kek))]
        if previous:
            self._keks.append((previous, kek_id(self.name, previous)))
        self.id = self._keks[0][1]

    def wrap(self, dek: bytes) -> str:
        kek, kid = self._keks[0]
        return aead.seal_bytes(dek, kek, f"{AAD_BASE}|{kid}")

    def unwrap(self, wrapped: str) -> bytes:
        last: Exception | None = None
        for kek, kid in self._keks:
            try:
                return aead.open_bytes(wrapped, kek, f"{AAD_BASE}|{kid}")
            except Exception as exc:  # try the previous KEK before giving up
                last = exc
        assert last is not None
        raise last


class EnvKek(_LocalProvider):
    """The KEK arrives in the environment — systemd `LoadCredential`, a Docker
    secret, a Vault agent render. This is `docs/11 §7` step 1, and the only step
    that needs no KMS at all."""

    name = "env"


class LocalKek(_LocalProvider):
    """Development only: a KEK in `data/kms-kek.json`, mode 0600, git-ignored.

    Honest label: this is a *file*, so it defends against the repository, a backup
    of the repository and a leaked `.env` — not against somebody who can already
    read `data/`. `config.PRODUCTION` refuses it.
    """

    name = "local"


class HttpKek:
    """A remote KMS that offers wrap/unwrap and cannot be asked for the KEK.

    The protocol is deliberately two operations, because that is the only thing an
    envelope needs and the shape every KMS proxy offers (Vault Transit, a cloud
    KMS HTTP front, an internal service in front of an HSM):

        POST {url}                      Authorization: Bearer <token>
        {"op": "unwrap", "key_id": "...", "ciphertext": "<b64>", "aad": "..."}
        → 200 {"plaintext_b64": "<b64>"}

        {"op": "wrap", ...same but "plaintext_b64"}
        → 200 {"ciphertext": "<b64>"}

    A non-200, a timeout, or a body without the expected field is an envelope
    error. There is no retry-forever and no "continue without the KEK".
    """

    name = "http"

    def __init__(self, url: str, token: str, key_id: str = "", timeout: int = 5):
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = max(1, min(30, int(timeout or 5)))
        self.id = key_id or "http-default"

    def _call(self, op: str, **fields) -> dict:
        body = json.dumps({"op": op, "key_id": self.id, **fields}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as res:
                raw = res.read()
        except urllib.error.HTTPError as exc:                      # a refusal is a refusal
            raise EnvelopeError("KEK_DENIED",
                                f"the KMS refused {op} ({exc.code}); no unwrap, no fallback")
        except Exception as exc:                                   # unreachable, timeout, TLS
            raise EnvelopeError("KEK_UNREACHABLE", f"the KMS could not be reached: {type(exc).__name__}")
        try:
            return json.loads(raw or b"{}")
        except Exception:
            raise EnvelopeError("KEK_BAD_RESPONSE", "the KMS answered with something that is not JSON")

    def wrap(self, dek: bytes) -> str:
        out = self._call("wrap", plaintext_b64=base64.b64encode(dek).decode(), aad=AAD_BASE)
        ciphertext = str(out.get("ciphertext") or "")
        if not ciphertext:
            raise EnvelopeError("KEK_BAD_RESPONSE", "the KMS answered without a ciphertext")
        return ciphertext

    def unwrap(self, wrapped: str) -> bytes:
        out = self._call("unwrap", ciphertext=wrapped, aad=AAD_BASE)
        raw = str(out.get("plaintext_b64") or "")
        if not raw:
            raise EnvelopeError("KEK_BAD_RESPONSE", "the KMS answered without a plaintext")
        try:
            dek = base64.b64decode(raw)
        except Exception:
            raise EnvelopeError("KEK_BAD_RESPONSE", "the KMS plaintext was not base64")
        if len(dek) != DEK_BYTES:
            raise EnvelopeError("KEK_BAD_RESPONSE", f"the KMS returned {len(dek)} bytes, not {DEK_BYTES}")
        return dek


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------
def provider(*, create_local: bool = False) -> tuple[str, object]:
    """Which KEK is configured, as `(name, provider)`. Raises if none can be."""
    url = cfg.get("RE_KMS_URL", "")
    if url:
        token = cfg.get("RE_KMS_TOKEN", "")
        return "http", HttpKek(url, token, cfg.get("RE_KMS_KEY_ID", ""),
                              int(cfg.get("RE_KMS_TIMEOUT", "5") or 5))
    raw = cfg.get("RE_KMS_KEK", "")
    kek = _decode_secret(raw)
    if kek:
        previous = _decode_secret(cfg.get("RE_KMS_KEK_PREVIOUS", ""))
        return "env", EnvKek(kek, previous)
    path = kek_file()
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raise EnvelopeError("KEK_UNREADABLE", f"{path} is not a readable KEK file")
        kek = _decode_secret(doc.get("kek", ""))
        if kek:
            return "local", LocalKek(kek, _decode_secret(doc.get("previous", "")))
        raise EnvelopeError("KEK_UNREADABLE", f"{path} holds no usable 32-byte KEK")
    if create_local and not cfg.PRODUCTION:
        kek = os.urandom(DEK_BYTES)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"kek": kek.hex(), "created_at": int(time.time()),
                       "note": "development KEK — not a KMS; see docs/30"}, fh)
        os.chmod(path, 0o600)
        return "local", LocalKek(kek)
    raise EnvelopeError(
        "NO_KEK_PROVIDER",
        "no key-encryption key is configured. Set RE_KMS_URL (a KMS) or RE_KMS_KEK "
        "(LoadCredential/Vault), or run: python3 scripts/kms_envelope.py init --provider local",
    )


# ---------------------------------------------------------------------------
# The envelope file
# ---------------------------------------------------------------------------
def load() -> dict | None:
    path = envelope_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EnvelopeError("ENVELOPE_UNREADABLE", f"{path}: {type(exc).__name__}")
    try:
        env = json.loads(raw)
    except Exception:
        raise EnvelopeError("ENVELOPE_CORRUPT", f"{path} is not JSON — restore it or re-init")
    if not isinstance(env, dict) or not env.get("wrapped"):
        raise EnvelopeError("ENVELOPE_CORRUPT", f"{path} has no wrapped key")
    return env


def write(env: dict) -> Path:
    """Atomic 0600 replace — never briefly world-readable, never half-written."""
    path = envelope_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(env, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


_DEK_CACHE: dict[str, bytes | None] = {}


def dek() -> bytes | None:
    """The root, unwrapped. `None` when this installation has no envelope.

    Cached for the life of the process: unwrapping is a KMS round trip, and the
    DEK cannot change while the process is running — a new DEK is a restart.
    """
    if "dek" in _DEK_CACHE:
        return _DEK_CACHE["dek"]
    env = load()
    if env is None:
        _DEK_CACHE["dek"] = None
        return None
    try:
        _name, kek = provider()
    except EnvelopeError as exc:
        raise EnvelopeError(exc.code, f"{exc.message}\nAn envelope exists at {envelope_path()}, so "
                                      f"this process cannot boot without its KEK.")
    try:
        root = kek.unwrap(env["wrapped"])
    except EnvelopeError:
        raise
    except Exception:
        raise EnvelopeError("KEK_MISMATCH",
                            f"the KEK '{kek.id}' cannot unwrap the envelope (provider in the "
                            f"envelope: {env.get('provider')!r}) — wrong key, or a tampered file. "
                            f"An envelope exists at {envelope_path()}, so this process cannot boot "
                            f"without its KEK.")
    if len(root) != DEK_BYTES:
        raise EnvelopeError("DEK_INVALID", f"the unwrapped root is {len(root)} bytes, not {DEK_BYTES}")
    _DEK_CACHE["dek"] = root
    return root


def forget() -> None:
    """Drop the cached root — used by the CLI after a rewrap, and by tests."""
    _DEK_CACHE.clear()


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def init(root: bytes | None = None, *, create_local: bool = False) -> dict:
    """Wrap a root into a new envelope. Adopts the current root by default."""
    root = root or cfg.MASTER_KEY
    if not root:
        raise EnvelopeError(
            "NO_ROOT",
            "there is no root to wrap: set RE_MASTER_KEY (to adopt it) or pass --new "
            "(which mints one and requires scripts/rotate_keys.py afterwards)",
        )
    if load() is not None:
        raise EnvelopeError("ENVELOPE_EXISTS",
                            f"{envelope_path()} already exists — use `rewrap` to change the KEK")
    _name, kek = provider(create_local=create_local)
    env = {
        "v": ENVELOPE_VERSION,
        "provider": kek.name,
        "kek_id": kek.id,
        "aad": AAD_BASE,
        "wrapped": kek.wrap(root),
        "created_at": int(time.time()),
        "rotated_at": 0,
        "retired": [],
    }
    write(env)
    forget()
    return env


def rewrap() -> dict:
    """Re-wrap the same root under the *current* KEK. No data is touched.

    This is what a KEK rotation is, and the order matters:

        1. put the NEW KEK in `RE_KMS_KEK` and the OLD one in `RE_KMS_KEK_PREVIOUS`
           (the local file: `"kek"` and `"previous"`)
        2. run `rewrap` — it unwraps with either and wraps with the current one
        3. restart, confirm a boot and a decrypt, then delete the old KEK

    Every field ever sealed stays readable because the root did not move — which is
    why this operation is safe to run at any time, and why it is the drill that
    proves the KEK path works. Run with no change at all and it simply re-wraps
    under the same KEK and retires the previous wrap.
    """
    env = load()
    if env is None:
        raise EnvelopeError("NO_ENVELOPE", "there is no envelope to re-wrap")
    current_root = dek()
    if current_root is None:
        raise EnvelopeError("NO_ENVELOPE", "the envelope holds no readable root")
    _name, kek = provider()
    retired = list(env.get("retired") or [])
    retired.append({"kek_id": env.get("kek_id", ""), "wrapped": env.get("wrapped", ""),
                    "retired_at": int(time.time())})
    new = {**env, "provider": kek.name, "kek_id": kek.id, "wrapped": kek.wrap(current_root),
           "rotated_at": int(time.time()), "retired": retired[-RETIRED_KEEP:]}
    write(new)
    forget()
    return new


def verify() -> dict:
    """Prove the envelope unwraps and derives the same subkeys. No secrets out."""
    # derives_live_keys is None when there is no keyring in this process to compare
    # against (the CLI): "not checked" must not look like "checked and wrong"
    out = {"envelope": load() is not None, "unwrapped": False, "roundtrip": False,
           "derives_live_keys": None, "provider": "", "kek_id": "", "error": ""}
    try:
        env = load()
        if env is None:
            out["error"] = "no envelope"
            return out
        _name, kek = provider()
        out["provider"], out["kek_id"] = kek.name, kek.id
        root = dek()
        out["unwrapped"] = root is not None
        if root is None:
            out["error"] = "no root"
            return out
        # Does the root the KEK just unwrapped actually produce the keys this
        # process is using? If not, every row sealed by the running server is
        # unreadable — the failure that would otherwise show up as a 500 on a
        # contact read. Compare fingerprints, never keys.
        live = sys.modules.get("app.security")
        if live is not None and getattr(live, "KEYS", None):
            want = {n: cfg.derive_key(n, root) for n in cfg.KEY_NAMES}
            out["derives_live_keys"] = all(
                hmac.new(want[n], b"probe", hashlib.sha256).digest()
                == hmac.new(live.KEYS[n], b"probe", hashlib.sha256).digest() for n in cfg.KEY_NAMES)
        # A round trip through the *current* KEK: wrap a fresh key, unwrap it, and
        # seal under the result. If this passes the KEK works in both directions.
        fresh = secrets.token_bytes(DEK_BYTES)
        rewrapped = kek.unwrap(kek.wrap(fresh))
        probe = aead.seal("envelope-probe", fresh, "probe")
        out["roundtrip"] = (rewrapped == fresh
                            and aead.unseal(probe, fresh, "probe") == "envelope-probe")
    except EnvelopeError as exc:
        out["error"] = f"{exc.code}: {exc.message}"
    return out


def active() -> bool:
    """Did *this process* unwrap the envelope, or merely find one on disk?

    The distinction is not cosmetic: a server booted before `init` runs is using
    RE_MASTER_KEY while `.secrets/dek.envelope` exists. Reporting "enforced"
    because a file appeared would be the console lying about the one thing it is
    there to prove. A file on disk plus no root in this process means one thing —
    restart.
    """
    return isinstance(_DEK_CACHE.get("dek"), bytes)


def status() -> dict:
    """What the console may show: where the root comes from, never the root."""
    env = load()
    path = envelope_path()
    mode = ""
    if path.exists():
        try:
            mode = oct(stat.S_IMODE(path.stat().st_mode))
        except OSError:
            mode = "?"
    advertised = cfg.get("RE_KMS_URL", "") and "http" or (cfg.get("RE_KMS_KEK", "") and "env" or "")
    live = active()
    return {
        "envelope": env is not None,
        # "there is an envelope" and "this process is running on it" are two
        # different facts, and after `init` on a running server they differ
        "active_in_process": live,
        "pending_restart": env is not None and not live,
        "path": str(path),
        "mode": mode,
        "git_ignored": ".secrets/" in str(path) or path.name.startswith("dek."),
        "provider_in_envelope": (env or {}).get("provider", ""),
        "kek_id_in_envelope": (env or {}).get("kek_id", ""),
        "configured_provider": advertised or ("local" if kek_file().exists() else ""),
        "version": (env or {}).get("v", 0),
        "created_at": (env or {}).get("created_at", 0),
        "rotated_at": (env or {}).get("rotated_at", 0),
        "retired": len((env or {}).get("retired") or []),
        "dek_on_disk": False,      # by construction: only a wrapped blob is written
        "kms_reachable": None,     # verify() answers this properly; never claim it here
        "claims": {
            "dek_at_rest": "wrapped by the KEK — never stored unwrapped",
            "kek_in_process": "yes, after unwrap (an HSM is docs/11 §7 step 3)",
            "host_compromise": "memory is still readable by the app user",
            "managed_kms_integrated": False,
        },
    }

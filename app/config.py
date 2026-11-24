"""
Realtor Estates — configuration & secret loading.

Design rules (all of them are enforced, not aspirational):

1. Secrets enter the process through the environment, preferring a real
   environment variable over the `.env` file (so a container/systemd/Vault
   injection always wins over a developer's local file).
2. `.env` is **never** read in a way that can echo a value. Only presence,
   source and a one-way fingerprint are ever reported.
3. Production **fails closed**. If `RE_ENV=production` and the master key is
   missing, short, or a placeholder, the process refuses to start. A server
   that boots without keys is a server that silently writes plaintext.
4. One root secret. All subkeys are derived with HKDF-SHA256, so rotating the
   master key is a single operation rather than five.
5. Nothing in this module ever writes a secret to a log, an exception message,
   an API response or a model prompt.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# `.env` is the operator's file; `.env.local` is where `neon deploy` writes the
# provisioned service credentials. Both are 0600 and git-ignored. A real
# environment variable always wins over either.
ENV_FILE = Path(os.environ.get("RE_ENV_FILE") or (ROOT / ".env"))
ENV_LOCAL = ROOT / ".env.local"


# ---------------------------------------------------------------------------
# .env parsing (no third-party dependency, deliberately dumb and strict)
# ---------------------------------------------------------------------------
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            # strip trailing comment only for unquoted values
            hash_at = val.find(" #")
            if hash_at != -1:
                val = val[:hash_at].strip()
        out[key] = val
    return out


_FILE_VALUES: dict[str, str] = {}
for _env_path in (ENV_FILE, ENV_LOCAL):
    for _k, _v in _parse_env_file(_env_path).items():
        _FILE_VALUES[_k] = _v  # later file wins over earlier; env still wins over both
_FILE_ORIGIN: dict[str, str] = {
    k: (".env.local" if _parse_env_file(ENV_LOCAL).get(k) == v else ".env") for k, v in _FILE_VALUES.items()
}
_VAULT_VALUES: dict[str, str] = {}
_VAULT_META: dict[str, Any] = {"configured": False, "loaded": 0, "provider": "", "error": ""}
_SOURCES: dict[str, str] = {}


def _extract_vault_kv(doc: Any) -> dict[str, str]:
    """Normalise Doppler, Infisical v3, HashiCorp KV-v2, and flat JSON maps."""
    if not isinstance(doc, dict):
        return {}
    out: dict[str, str] = {}
    # Infisical v3: `{"secrets": [{"secretKey": "K", "secretValue": "V"}, ...]}`
    if isinstance(doc.get("secrets"), list):
        for item in doc["secrets"]:
            if isinstance(item, dict) and item.get("secretKey"):
                out[str(item["secretKey"])] = str(item.get("secretValue") or "")
        return out
    # Doppler API v3 structured: `{"secrets": {"K": {"computed": "V"}}}`
    if isinstance(doc.get("secrets"), dict):
        for k, v in doc["secrets"].items():
            if isinstance(v, dict):
                out[str(k)] = str(v.get("computed") if v.get("computed") is not None else v.get("raw") or "")
            elif isinstance(v, (str, int, float)):
                out[str(k)] = str(v)
        return out
    # HashiCorp Vault KV v2 / v1: `{"data": {"data": {...}}}` or `{"data": {...}}`
    inner = doc.get("data")
    if isinstance(inner, dict):
        leaf = inner.get("data") if isinstance(inner.get("data"), dict) else inner
        for k, v in leaf.items():
            if isinstance(v, (str, int, float)):
                out[str(k)] = str(v)
        return out
    # Doppler `?format=json` / generic flat JSON object: `{"K": "V"}`
    for k, v in doc.items():
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)) and isinstance(v, (str, int, float)):
            out[str(k)] = str(v)
    return out


def load_from_secret_manager(
    url: str = "",
    token: str = "",
    provider: str = "",
    *,
    production: bool | None = None,
    timeout: float = 5.0,
) -> dict[str, str]:
    """Pull secrets into memory from Doppler, Infisical, or an HTTPS Vault (`docs/14` #1).

    Nothing fetched here is ever written to `.env` or `.env.local`. Precedence is
    `process env > vault > .env.local > .env`.
    """
    import json
    import urllib.request
    from urllib.parse import urlparse

    target_url = (url or os.environ.get("RE_SECRET_MANAGER_URL") or _FILE_VALUES.get("RE_SECRET_MANAGER_URL") or "").strip()
    bearer = (token or os.environ.get("RE_SECRET_MANAGER_TOKEN") or _FILE_VALUES.get("RE_SECRET_MANAGER_TOKEN") or "").strip()
    prov = (provider or os.environ.get("RE_SECRET_MANAGER_PROVIDER") or _FILE_VALUES.get("RE_SECRET_MANAGER_PROVIDER") or "auto").strip().lower()
    is_prod = (
        production
        if production is not None
        else ((os.environ.get("RE_ENV") or _FILE_VALUES.get("RE_ENV") or "development").lower() in ("production", "prod", "staging"))
    )

    if not target_url:
        _VAULT_META.update({"configured": False, "loaded": 0, "provider": "", "error": ""})
        return {}

    parsed = urlparse(target_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        if is_prod or host not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("RE_SECRET_MANAGER_URL must use https:// (loopback http:// allowed in dev/test only)")
    if not bearer:
        raise RuntimeError("RE_SECRET_MANAGER_URL is set but RE_SECRET_MANAGER_TOKEN is empty")

    detected = prov if prov != "auto" else (
        "doppler" if "doppler.com" in host else
        "infisical" if "infisical.com" in host else
        "vault"
    )
    req = urllib.request.Request(
        target_url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {bearer}",
            "X-Vault-Token": bearer,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    kv = {k: v for k, v in _extract_vault_kv(payload).items() if v != ""}
    _VAULT_VALUES.clear()
    _VAULT_VALUES.update(kv)
    _VAULT_META.update({"configured": True, "loaded": len(kv), "provider": detected, "error": ""})
    return dict(kv)


def vault_status() -> dict[str, Any]:
    return dict(_VAULT_META)


def get(name: str, default: str = "") -> str:
    """Environment first, then secret manager (`vault`), then `.env`, then default."""
    if name in os.environ and os.environ[name] != "":
        _SOURCES[name] = "env"
        return os.environ[name]
    if name in _VAULT_VALUES and _VAULT_VALUES[name] != "":
        _SOURCES[name] = "vault"
        return _VAULT_VALUES[name]
    if name in _FILE_VALUES and _FILE_VALUES[name] != "":
        _SOURCES[name] = _FILE_ORIGIN.get(name, ".env")
        return _FILE_VALUES[name]
    return default


# ---------------------------------------------------------------------------
# Key derivation (RFC 5869 HKDF-SHA256)
# ---------------------------------------------------------------------------
def hkdf_sha256(master: bytes, info: bytes, length: int = 32, salt: bytes = b"") -> bytes:
    prk = hmac.new(salt or b"\x00" * 32, master, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


KEY_NAMES = ("field_enc", "blind_index", "session", "audit", "webhook")


def derive_key(name: str, master: bytes) -> bytes:
    return hkdf_sha256(master, b"realtor-estates/v1/" + name.encode(), 32)


# ---------------------------------------------------------------------------
# Secret inventory
# ---------------------------------------------------------------------------
# name, required-in-production, description
SECRET_INVENTORY: tuple[tuple[str, bool, str], ...] = (
    ("RE_MASTER_KEY", True, "Root of every cryptographic operation (HKDF → field encryption, blind index, sessions, audit, webhooks)"),
    ("RE_WEBHOOK_SIGNING_SECRET", False, "Outbound webhook HMAC key (falls back to a master-derived key)"),
    # --- external secret manager (`docs/14` #1: Doppler / Infisical / Vault) --
    ("RE_SECRET_MANAGER_URL", False, "HTTPS secret-manager endpoint (Doppler, Infisical v3, or Vault KV)"),
    ("RE_SECRET_MANAGER_TOKEN", False, "Service token used once at boot to hydrate secrets in memory"),
    ("RE_SECRET_MANAGER_PROVIDER", False, "Secret-manager dialect: auto | doppler | infisical | vault"),
    # --- KMS envelope (docs/30): wraps the root, so the root is not at rest ---
    ("RE_KMS_KEK", False, "Key-encryption key for the envelope (`env` provider) — 32 bytes, hex or base64"),
    ("RE_KMS_KEK_PREVIOUS", False, "The KEK being rotated away from — set only for the duration of a rewrap"),
    ("RE_KMS_URL", False, "KMS wrap/unwrap endpoint (`http` provider) — the KEK never reaches this host"),
    ("RE_KMS_TOKEN", False, "Bearer token for RE_KMS_URL"),
    # --- shared security state (docs/31): limits and logouts across instances ---
    ("RE_REDIS_URL", False, "redis:// or unix:// — shared rate-limit counters and revocations"),
    ("RE_SHARED_DIR", False, "Directory shared by the instances on one host (file backend)"),
    ("RE_SHARED_STATE", False, "Force memory | redis | file instead of inferring from the two above"),
    ("RE_REDIS_TIMEOUT", False, "Seconds before an unreachable Redis counts as unreachable (default 1.5)"),
    # --- Neon services (written by `neon deploy` into .env.local) -----------
    ("DATABASE_URL", True, "Neon Postgres DSN for the *runtime* role (re_app): no BYPASSRLS, no DDL"),
    ("RE_ADMIN_DATABASE_URL", True, "Owner DSN — migrations and boot hydration only, never the web tier"),
    ("NEON_AUTH_BASE_URL", False, "Neon Auth (Stack Auth) base URL — bearer-token introspection"),
    ("NEON_AI_GATEWAY_URL", False, "Neon AI Gateway base URL (OpenAI-compatible /v1/chat/completions)"),
    ("NEON_AI_GATEWAY_KEY", False, "Neon AI Gateway API key"),
    ("NEON_AI_GATEWAY_MODEL", False, "Default model routed through the AI Gateway"),
    ("NEON_STORAGE_ENDPOINT", False, "Object storage S3-compatible endpoint"),
    ("NEON_STORAGE_BUCKET", False, "Object storage bucket (document packs, recordings, listing photos)"),
    ("NEON_STORAGE_ACCESS_KEY", False, "Object storage access key id"),
    ("NEON_STORAGE_SECRET_KEY", False, "Object storage secret access key"),
    ("NEON_STORAGE_REGION", False, "Object storage region (SigV4 scope)"),
    ("NEON_REALTIME_URL", False, "Neon Realtime endpoint (replaces the in-process SSE fan-out)"),
    ("NEON_REALTIME_TOKEN", False, "Neon Realtime token"),
    ("NEON_FUNCTIONS_URL", False, "Deployed Neon Functions base URL"),
    ("OPENAI_API_KEY", False, "Voice realtime / general LLM provider"),
    ("ANTHROPIC_API_KEY", False, "Nurture + drafting model provider"),
    ("GOOGLE_API_KEY", False, "Gemini (lead intelligence) + Google Ads API"),
    ("SARVAM_API_KEY", False, "Indian-language speech-to-speech voice provider"),
    ("ELEVENLABS_API_KEY", False, "Premium voice provider (upsell)"),
    ("JEV_API_KEY", False, "JEV / jev-ultrafast — YouTube research + content scoring"),
    ("WHATSAPP_TOKEN", False, "WhatsApp Cloud API access token"),
    ("WHATSAPP_PHONE_ID", False, "WhatsApp Cloud API phone number id"),
    ("WHATSAPP_VERIFY_TOKEN", False, "Inbound webhook verification token"),
    ("WHATSAPP_APP_SECRET", False, "WhatsApp Cloud API app secret for X-Hub-Signature-256 HMAC"),
    ("SMS_API_KEY", False, "DLT-registered SMS gateway key"),
    ("SMS_SENDER_ID", False, "DLT header (e.g. SNRSUN)"),
    ("SMS_ENTITY_ID", False, "TRAI DLT 19-digit Principal Entity ID"),
    ("SMS_API_URL", False, "DLT SMS HTTPS dispatch endpoint"),
    ("VOICE_API_URL", False, "Outbound telephony provider HTTPS endpoint"),
    ("VOICE_WEBHOOK_SECRET", False, "Telephony status/recording webhook HMAC secret"),
    ("META_ADS_TOKEN", False, "Meta Marketing API"),
    ("GOOGLE_ADS_DEVELOPER_TOKEN", False, "Google Ads API developer token"),
    ("RAZORPAY_KEY_ID", False, "Billing (public id)"),
    ("RAZORPAY_KEY_SECRET", False, "Billing (private secret)"),
    ("SENTRY_DSN", False, "Error reporting (contains a project key)"),
)

# Values that must never be accepted as real secrets.
_PLACEHOLDERS = (
    "changeme", "change-me", "your-", "xxxx", "placeholder", "todo",
    "replace-me", "demo", "example", "insert-here", "dummy", "test",
)

MIN_MASTER_KEY_BYTES = 32


def is_placeholder(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return True
    if len(v) < 16:
        return True
    return any(p in v for p in _PLACEHOLDERS)


def _decode_master(raw: str) -> bytes:
    """Accept 64 hex chars, base64ish, or raw ≥32 bytes. Normalise to bytes."""
    raw = (raw or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        return bytes.fromhex(raw)
    if re.fullmatch(r"[0-9a-fA-F]{96}", raw):
        return bytes.fromhex(raw)[:MIN_MASTER_KEY_BYTES]
    try:
        import base64

        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        if len(decoded) >= MIN_MASTER_KEY_BYTES:
            return decoded[:MIN_MASTER_KEY_BYTES]
    except Exception:
        pass
    return raw.encode("utf-8")[:MIN_MASTER_KEY_BYTES]


ENV = (get("RE_ENV", "development") or "development").lower()
PRODUCTION = ENV in ("production", "prod", "staging")
DEMO_MODE = not PRODUCTION

BOOT_WARNINGS: list[str] = []
BOOT_ERRORS: list[str] = []

if os.environ.get("RE_SECRET_MANAGER_URL") or _FILE_VALUES.get("RE_SECRET_MANAGER_URL"):
    try:
        load_from_secret_manager(production=PRODUCTION)
    except Exception as _vault_exc:
        _msg = f"secret manager load failed: {type(_vault_exc).__name__}: {str(_vault_exc)[:160]}"
        _VAULT_META.update({"configured": True, "loaded": 0, "error": _msg})
        if PRODUCTION:
            BOOT_ERRORS.append(_msg)
        else:
            BOOT_WARNINGS.append(_msg)

_MASTER_RAW = get("RE_MASTER_KEY", "")
MASTER_KEY: bytes | None = None

if _MASTER_RAW:
    if is_placeholder(_MASTER_RAW):
        BOOT_ERRORS.append(
            "RE_MASTER_KEY looks like a placeholder — generate one with: python3 -c \"import secrets;print(secrets.token_hex(32))\""
        )
    else:
        MASTER_KEY = _decode_master(_MASTER_RAW)
        if len(MASTER_KEY) < MIN_MASTER_KEY_BYTES:
            BOOT_ERRORS.append("RE_MASTER_KEY is shorter than 32 bytes after decoding")
            MASTER_KEY = None
else:
    if PRODUCTION:
        BOOT_ERRORS.append(
            "RE_MASTER_KEY is not set. Production refuses to start without it "
            "(no key ⇒ no encryption ⇒ no protection for tenant PII)."
        )
    else:
        BOOT_WARNINGS.append(
            "RE_MASTER_KEY not set — development fallback keyring in data/keys.json is in use. "
            "Never deploy this way."
        )

# A demo/staging convenience: refuse obviously-derived keys in production.
if PRODUCTION and MASTER_KEY and hashlib.sha256(MASTER_KEY).hexdigest() in {
    hashlib.sha256(b"demo").hexdigest(),
}:
    BOOT_ERRORS.append("RE_MASTER_KEY is a known demo value")

if BOOT_ERRORS and PRODUCTION:
    raise RuntimeError("refusing to start: " + "; ".join(BOOT_ERRORS))


# ---------------------------------------------------------------------------
# File-permission hygiene on the secret files themselves
# ---------------------------------------------------------------------------
def _mode_warning(path: Path, label: str) -> list[str]:
    try:
        st = path.stat()
    except OSError:
        return []
    if os.name != "posix":
        return []
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return [f"{label} ({path}) is group/world accessible (mode {oct(st.st_mode & 0o777)}). Run: chmod 600 {path}"]
    return []


def file_warnings() -> list[str]:
    warns: list[str] = []
    if ENV_FILE.exists():
        warns += _mode_warning(ENV_FILE, ".env")
    if ENV_LOCAL.exists():
        warns += _mode_warning(ENV_LOCAL, ".env.local")
    key_file = Path(get("RE_KEY_FILE", str(ROOT / "data" / "keys.json")))
    if key_file.exists():
        warns += _mode_warning(key_file, "keyring")
    return warns


# ---------------------------------------------------------------------------
# Redaction — used by logs, error pages, the owner console and the guardrails
# ---------------------------------------------------------------------------
REDACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("aws", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{12,}")),
    ("whatsapp", re.compile(r"\bEAA[A-Za-z0-9]{20,}")),
    ("webhook", re.compile(r"\bwhsec_[A-Za-z0-9]{8,}")),
    ("razorpay", re.compile(r"\brzp_[A-Za-z0-9]{10,}")),
    ("bearer", re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("hex32", re.compile(r"\b[0-9a-fA-F]{64}\b")),
)


def redact(text: str) -> str:
    """Replace anything that looks like key material. Idempotent and cheap."""
    if not text:
        return text
    out = text
    for label, rx in REDACT_PATTERNS:
        out = rx.sub(f"[REDACTED:{label}]", out)
    # never leak the live master key, whatever shape it is in
    for name, _required, _desc in SECRET_INVENTORY:
        val = get(name, "")
        if len(val) >= 12:
            out = out.replace(val, "[REDACTED:secret]")
    return out


def fingerprint(value: str) -> str:
    """One-way, short, non-reversible identifier for 'is this the same key?'."""
    if not value:
        return ""
    return hashlib.sha256(("re-fp:" + value).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Reporting (this is what the owner console and the health endpoint render)
# ---------------------------------------------------------------------------
def secrets_report() -> dict[str, Any]:
    entries = []
    for name, required, desc in SECRET_INVENTORY:
        val = get(name, "")
        present = bool(val) and not is_placeholder(val)
        entries.append(
            {
                "name": name,
                "required": required,
                "present": present,
                "source": _SOURCES.get(name, "unset" if not val else "env"),
                "fingerprint": fingerprint(val) if present else "",
                "description": desc,
            }
        )
    missing_required = [e["name"] for e in entries if e["required"] and not e["present"]]
    return {
        "environment": ENV,
        "production_mode": PRODUCTION,
        "secret_manager": vault_status(),
        "master_key": {
            "present": MASTER_KEY is not None,
            "derived_subkeys": list(KEY_NAMES),
            "fingerprint": fingerprint(_MASTER_RAW) if MASTER_KEY else "",
            "age_warning": "rotate before go-live" if DEMO_MODE else "",
        },
        "secrets": entries,
        "missing_required": missing_required,
        "warnings": BOOT_WARNINGS + file_warnings(),
        "errors": BOOT_ERRORS,
        "policy": {
            "storage": ".env (mode 0600, git-ignored) → environment → KMS/HSM in production",
            "never_in": ["git", "API responses", "logs", "model prompts", "client bundles", "webhook payloads"],
            "rotation": "rotate RE_MASTER_KEY with scripts/rotate_keys.py (re-encrypts tenant PII)",
            "leak_response": "docs/11-SECRETS-MANAGEMENT.md §6",
        },
    }


def generate_master_key() -> str:
    return secrets.token_hex(32)


if __name__ == "__main__":  # tiny CLI: python3 -m app.config
    import json

    print(json.dumps(secrets_report(), indent=2))

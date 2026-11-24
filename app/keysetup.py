"""
Secret intake for operators — writing one key into a file safely.

Why this module exists: a `.env.local` is mode 0600 and git-ignored, which is
exactly why it is also awkward to edit from a browser-based workspace file
viewer (dotfiles are often hidden, and an editor that round-trips the file would
break its mode). So the app exposes a one-shot, owner-only form that writes **one
named key** into that file — nothing else.

The rules that keep this from becoming a secret-leak primitive:

  * **One key, one value.** The caller names the key; the module refuses anything
    that is not a plain `[A-Za-z0-9_]` identifier.
  * **No newline, no metacharacters.** A value containing `\\n` would let a caller
    inject *other* environment variables (a new `DATABASE_URL`, for example), so
    the charset is a strict allowlist.
  * **Separate from the runtime env.** The CLI credential lives in
    `.neon/api_key`, not in `.env.local`: the app process never loads it, so no
    config dump or traceback can surface it.
  * **Atomic replace, mode preserved.** Write to a temp file in the same
    directory, `fsync`, `os.replace`, then `chmod 0600`. A crash cannot leave a
    half-written secrets file, and the file is never briefly world-readable.
  * **The value is never logged.** Callers log a fingerprint (SHA-256 prefix), not
    the key, and the response carries only a masked form.
  * **Idempotent.** An existing `KEY=…` line, or the `# KEY=` placeholder the repo
    ships, is replaced in place rather than appended again.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Where the CLI credential lives. Deliberately NOT `.env.local`: that file is the
# *runtime* environment (loaded into the app process), and the deploy key has no
# business being in a process that serves HTTP. Separate file = separate blast
# radius: no config dump, traceback, or env inspector can reach it.
# `.neon` is a *file* in this repo (the CLI's link context), so the credential
# cannot live inside it — `.secrets/` is a directory of its own, git-ignored.
DEFAULT_SECRETS_FILE = ROOT / ".secrets" / "neon-api-key"
DEFAULT_BRANCH_FILE = ROOT / ".secrets" / "neon-test-branch-url"
DEFAULT_ENV_LOCAL = ROOT / ".env.local"

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# Neon keys are `nak_…`; the allowlist is deliberately about *characters*, not
# shape, so a future prefix does not require a code change.
VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-+/=]{8,512}$")
DSN_RE = re.compile(r"^postgres(?:ql)?://[^\s'\"`\\]{12,1024}$")

# Tests point this at a temp file so a suite never touches a real key.
SECRETS_OVERRIDE = "RE_SECRETS_FILE"
BRANCH_OVERRIDE = "RE_BRANCH_URL_FILE"
ENV_LOCAL_OVERRIDE = "RE_ENV_LOCAL"


def secrets_path() -> Path:
    """The CLI-credential file (`.secrets/neon-api-key`, 0600, git-ignored)."""
    override = os.environ.get(SECRETS_OVERRIDE, "").strip()
    return Path(override) if override else DEFAULT_SECRETS_FILE


def branch_url_path() -> Path:
    """Disposable Neon branch DSN file (`.secrets/neon-test-branch-url`, 0600)."""
    override = os.environ.get(BRANCH_OVERRIDE, "").strip()
    return Path(override) if override else DEFAULT_BRANCH_FILE


def env_local_path() -> Path:
    override = os.environ.get(ENV_LOCAL_OVERRIDE, "").strip()
    return Path(override) if override else DEFAULT_ENV_LOCAL


def read_cli_key() -> str:
    return read_value("NEON_API_KEY", secrets_path())


def write_cli_key(value: str) -> dict:
    return set_value("NEON_API_KEY", value, secrets_path())


def shred_cli_key() -> bool:
    """Remove the credential file entirely — the default after a deploy."""
    target = secrets_path()
    if not target.exists():
        return False
    try:
        size = target.stat().st_size
        with open(target, "r+b") as fh:      # overwrite before unlink
            fh.write(b"\x00" * size)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    try:
        target.unlink()
        return True
    except OSError:
        return False


def validate(key: str, value: str) -> None:
    """Raise ValueError with a safe message (never echo the value)."""
    if not KEY_RE.match(key or ""):
        raise ValueError("key must match [A-Za-z_][A-Za-z0-9_]*")
    if not value:
        raise ValueError("value is empty")
    if "\n" in value or "\r" in value:
        raise ValueError("value must not contain line breaks")
    if not VALUE_RE.match(value):
        raise ValueError("value contains characters outside [A-Za-z0-9_.-+/=] or is the wrong length")


def read_value(key: str, path: Path | None = None) -> str:
    """Current value of `key` in the env file, or ''."""
    target = path or env_local_path()
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError, OSError):
        return ""
    return ""


def set_value(key: str, value: str, path: Path | None = None) -> dict:
    """Write `key=value` into the env file atomically. Returns a non-secret report."""
    validate(key, value)
    target = path or env_local_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # 0700 on the containing directory: 0600 on the file hides its contents, but a
    # world-executable directory still lets someone learn the *name* is there.
    if target.parent.name == ".secrets":
        os.chmod(target.parent, 0o700)

    try:
        existing = target.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        existing = []

    out: list[str] = []
    replaced = False
    dropped_placeholders = 0
    assignment = f"{key}="
    placeholder = f"# {key}="
    for line in existing:
        stripped = line.strip()
        if stripped.startswith(assignment) and not stripped.startswith("#"):
            if replaced:
                dropped_placeholders += 1        # a duplicate: keep the first slot only
                continue
            out.append(f"{key}={value}")
            replaced = True
            continue
        if stripped.startswith(placeholder):
            if replaced:
                dropped_placeholders += 1
                continue
            out.append(f"{key}={value}")
            replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    # Atomic write: temp file in the same directory, then rename over the target.
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise

    os.chmod(target, 0o600)   # os.replace keeps the temp file's mode; be explicit anyway
    return {
        "key": key,
        "path": str(target),
        "mode": "0600",
        "bytes": target.stat().st_size,
        "replaced": replaced,
        "dropped_duplicates": dropped_placeholders,
        "fingerprint": fingerprint(value),
        "masked": mask(value),
    }


def remove(key: str, path: Path | None = None) -> bool:
    """Drop `KEY=…` (used by tests and by an operator rotating out a value)."""
    target = path or env_local_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False
    kept = [ln for ln in lines if not ln.strip().startswith(f"{key}=")]
    if len(kept) == len(lines):
        return False
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".env.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp_name, 0o600)
    os.replace(tmp_name, target)
    return True


def fingerprint(value: str) -> str:
    """Stable, non-reversible identifier for a secret — safe to log and to show."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def mask(value: str) -> str:
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * max(4, len(value) - 8)}{value[-4:]}"


def mask_dsn(url: str) -> str:
    """Redact the password in a postgres:// DSN while keeping host/db recognisable."""
    if not url:
        return ""
    return re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+(@)", r"\1••••••\2", url)


def read_branch_url() -> str:
    target = branch_url_path()
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return s
    except OSError:
        return ""
    return ""


def write_branch_url(url: str) -> dict:
    clean = (url or "").strip()
    if "\n" in clean or "\r" in clean or not DSN_RE.match(clean):
        raise ValueError("branch_url must be a single-line postgresql:// connection string")
    target = branch_url_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    replaced = target.exists() and bool(read_branch_url())
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".branch.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(clean + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise
    os.chmod(target, 0o600)
    return {
        "path": str(target),
        "mode": "0600",
        "replaced": replaced,
        "fingerprint": fingerprint(clean),
        "masked": mask_dsn(clean),
    }


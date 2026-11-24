#!/usr/bin/env python3
"""
Realtor Estates — secret hygiene gate.

Runs five checks and exits non-zero on any failure, so it can be used as a
pre-commit hook, a CI step, or a cron job on the host:

  1. `.env` exists, is mode 0600, and is git-ignored
  2. no runtime secret file (`data/keys.json`, `data/store.json`, `*.pem`, …) is tracked
  3. no tracked file contains key material matching a known provider pattern
  4. no tracked file contains a high-entropy value assigned to a secret-ish name
  5. no exposure-zone file (uploads/, Downloads/, Desktop/) contains a plaintext
     credential — an attached key is never committed, and still leaves the box
  6. (optional, --history) git history is clean of the same patterns

Usage:
    python3 scripts/check_secrets.py
    python3 scripts/check_secrets.py --history     # slower: walks every commit
    python3 scripts/check_secrets.py --fix-perms    # chmod 600 the secret files

Exit codes: 0 clean · 1 findings · 2 not a git repo / cannot inspect
"""
from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Patterns that indicate real key material (not placeholders).
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("google-api", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("whatsapp-token", re.compile(r"\bEAA[A-Za-z0-9]{40,}")),
    ("razorpay", re.compile(r"\brzp_(live|test)_[A-Za-z0-9]{10,}")),
    ("stripe", re.compile(r"\b(sk|rk)_(live|test)_[A-Za-z0-9]{20,}")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("private-key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("jwt-long", re.compile(r"\beyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}")),
    ("neon-credential", re.compile(r"\b(?:npg|nak|nsk|napi)_[A-Za-z0-9_\-]{16,}")),
    ("google-oauth-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}")),
    ("vercel-token", re.compile(r"\bvcp_[A-Za-z0-9_\-]{20,}")),
    ("webhook-secret", re.compile(r"\bwhsec_[A-Za-z0-9_\-]{20,}")),
    ("dsn-password", re.compile(r"\bpostgres(?:ql)?://[^:\s\"'<>{}]+:[^@\s\"'<>{}*]{8,}@")),
)

# A 64-char hex run is a plausible 32-byte key. Placeholders are excluded by
# the placeholder heuristic in app/config.py.
HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
# Only flag assignments of something that *looks like a literal secret*:
# no spaces, no call syntax, no attribute access — a code expression is not a leak.
ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|DSN)[A-Z0-9_]*)\s*[:=]\s*['\"]([A-Za-z0-9_\-+/=.$!@#%^&*]{16,})['\"]"
)

# Files that legitimately contain example/pattern strings.
ALLOWLIST_SUFFIXES = {".md", ".example", ".lock"}
ALLOWLIST_NAMES = {".env.example", "check_secrets.py", "config.py", "security.py"}
# Code that must mention patterns in order to detect them.
ALLOWLIST_PATH_PARTS = ("redline/", "tests/", "scripts/", "app/redteam.py")


def sh(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout


def is_git_repo() -> bool:
    code, _ = sh(["git", "rev-parse", "--is-inside-work-tree"])
    return code == 0


def entropy(s: str) -> float:
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in freq.values())


def placeholderish(value: str) -> bool:
    v = value.strip().strip("'\"").lower()
    if len(v) < 16:
        return True
    return any(
        p in v
        for p in ("changeme", "change-me", "your-", "xxxx", "placeholder", "todo", "replace", "example", "dummy", "insert", "<", "{")
    )


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []  # severity, where, what

    def add(self, sev: str, where: str, what: str) -> None:
        self.items.append((sev, where, what))

    def report(self) -> int:
        if not self.items:
            print("check_secrets: OK — no findings")
            return 0
        print(f"check_secrets: {len(self.items)} finding(s)\n")
        for sev, where, what in self.items:
            print(f"  [{sev}] {where}\n        {what}")
        crit = [i for i in self.items if i[0] == "critical"]
        print(f"\n{len(crit)} critical, {len(self.items) - len(crit)} warning")
        if crit:
            print("Harden: rotate the exposed secret, then follow docs/11-SECRETS-MANAGEMENT.md §6.")
            return 1
        print("No critical findings — warnings above are advisory.")
        return 0


def check_env_file(f: Findings, fix_perms: bool) -> None:
    env = ROOT / ".env"
    if not env.exists():
        f.add("warning", ".env", "not present — copy .env.example → .env and generate RE_MASTER_KEY")
        return
    mode = env.stat().st_mode & 0o777
    if mode & 0o077:
        if fix_perms:
            env.chmod(0o600)
            print(f"  fixed: .env mode {oct(mode)} → 0o600")
        else:
            f.add("critical", ".env", f"mode {oct(mode)} is group/world readable — run: chmod 600 .env")
    if env.read_text(encoding="utf-8", errors="replace").count("RE_MASTER_KEY=") == 0:
        f.add("critical", ".env", "RE_MASTER_KEY is not defined")
    if is_git_repo():
        code, _ = sh(["git", "check-ignore", "-q", ".env"])
        if code != 0:
            f.add("critical", ".env", "not git-ignored — add '.env' to .gitignore immediately")


def tracked_files() -> list[str]:
    code, out = sh(["git", "ls-files"])
    if code != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def check_tracked_secret_containers(f: Findings, tracked: list[str]) -> None:
    bad_names = re.compile(r"(^|/)(\.env($|\.)|.*keys?\.json|store\.json|.*\.(pem|key|p12|pfx)|id_rsa|credentials.*)$")
    for path in tracked:
        if path.endswith(".env.example"):
            continue
        if bad_names.search(path):
            f.add("critical", path, "secret-bearing file is tracked by git — run: git rm --cached " + path)


def check_file_contents(f: Findings, tracked: list[str]) -> None:
    for path in tracked:
        p = ROOT / path
        if not p.is_file():
            continue
        if path.endswith(tuple(ALLOWLIST_SUFFIXES)) or p.name in ALLOWLIST_NAMES:
            continue
        if any(part in path for part in ALLOWLIST_PATH_PARTS):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, rx in SECRET_PATTERNS:
            m = rx.search(text)
            if m:
                f.add("critical", path, f"looks like a live {label} credential (value withheld)")
        for m in HEX64.finditer(text):
            if entropy(m.group(0)) > 3.0:
                f.add("critical", path, "64-char high-entropy string (possible 32-byte key)")
                break
        for name, value in ASSIGN.findall(text):
            if "realtorestates" in value or value.startswith("$") or value.startswith("{{"):
                continue
            # `secretAccessKey: process.env.X ?? ""` is a *read*, not a literal:
            # flagging it teaches people to ignore the gate.
            if "process.env" in value or value.startswith("os.environ") or value.startswith("cfg.get"):
                continue
            # A lowercase snake_case word is a config identifier, not a credential
            # (`key: "call_recordings_days"` in a retention table). Credentials are
            # mixed-case, base64 or hex; this shape never is.
            if re.fullmatch(r"[a-z][a-z0-9_]{2,}", value):
                continue
            if placeholderish(value) or entropy(value) < 3.2:
                continue
            f.add("warning", path, f"{name} assigned a high-entropy value")


CLEAN_CREDENTIAL = re.compile(r"^[A-Za-z0-9_\-+/=.]{20,}$")


def _is_literal_credential(token: str) -> bool:
    """Distinguish a real key from a detection regex or a test fixture.

    A regex contains metacharacters ({, [, (, |, \\) and a fixture usually has
    runs like ABCDEF. We only escalate when the match is a clean, high-entropy
    token that could actually authenticate somewhere.
    """
    if not CLEAN_CREDENTIAL.match(token):
        return False
    if entropy(token) < 3.6:
        return False
    return not re.search(r"(.)\1{7,}", token)  # ABCDEFGH-style fixtures are runs, not entropy


def fixture_tokens() -> set[str]:
    """Literal credentials that exist *only* as test fixtures at HEAD.

    History has no file paths — `git log -p` is one flat blob — so the path
    allowlist that keeps `tests/` out of the working-tree scan cannot apply.
    Without this, the gate cries wolf on its own fixtures (an AWS example key in
    a monkeypatch, a fake GitHub PAT in an assertion) and people learn to ignore
    a red critical.

    The rule is deliberately narrow: a token counts as a fixture only if it is
    still present in an allowlisted fixture path at HEAD. A real secret that was
    committed and later deleted is therefore *not* excused — it was never a
    fixture — which is exactly the case this check exists for.
    """
    tokens: set[str] = set()
    code, out = sh(["git", "ls-files"])
    if code != 0:
        return tokens
    for path in out.splitlines():
        if not any(part in path for part in ALLOWLIST_PATH_PARTS):
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for _label, rx in SECRET_PATTERNS:
            for m in rx.finditer(text):
                tokens.add(m.group(0))
    return tokens


def check_history(f: Findings) -> None:
    code, out = sh(["git", "log", "--all", "--pretty=format:@@%H", "-p"])
    if code != 0:
        f.add("warning", "git history", "could not read history")
        return
    # A match only counts if it is NOT part of detection code or a fixture:
    # the repo legitimately contains regexes that look for these prefixes.
    CONTEXT = ("redact", "re.search", "re.compile", "SECRET_PATTERNS", "sample_secret", "fake_", "xxxx")
    hits = 0
    fixtures = fixture_tokens()
    lines = out.splitlines()
    for label, rx in SECRET_PATTERNS:
        for i, line in enumerate(lines):
            for m in rx.finditer(line):
                token = m.group(0)
                if not _is_literal_credential(token):
                    continue
                if token in fixtures:
                    continue  # a fixture at HEAD, not a leaked credential
                if any(c in line.lower() for c in (x.lower() for x in CONTEXT)):
                    continue
                if token[8:14].isalpha() and token[8:14] == "ABCDEF":
                    continue  # placeholder fixture
                hits += 1
                f.add("critical", "git history", f"a {label} credential appears in commit history (line {i + 1}) — rotate it; history is public forever")
    for m in HEX64.finditer(out):
        if entropy(m.group(0)) > 3.0 and _is_literal_credential(m.group(0)):
            f.add("critical", "git history", "a 64-char hex key appears in history — rotate the master key")
            break
    if hits == 0:
        print(f"  history: no literal credentials found ({len(fixtures)} test fixtures ignored)")


EXPOSURE_ZONES = ("/home/user/uploads", "/home/user/Downloads", "/home/user/Desktop")

SECRET_FILE_PATTERNS = re.compile(r"(^\.env|\.env\.(local|prod|production|staging|dev)|store\.json|keys\.json|\.pgdata|credentials|id_rsa)", re.I)
SECRET_VALUE_PATTERNS = (
    re.compile(r"RE_MASTER_KEY\s*[=:]\s*['\"]?[A-Fa-f0-9]{32,}"),
    re.compile(r"postgres(?:ql)?://[^:\s\"']+:[^@\s\"']{8,}@"),
    # Neon: API keys are `nak_…` (project-scoped) and the older documented form
    # was `nak_live_…`; `npg_`/`nsk_` cover the other credential families. The
    # first version only matched `nak_live_`, so a real project key dropped in
    # uploads/ scanned clean — the gap that let an attachment sit unnoticed.
    re.compile(r"\b(?:npg_|nak_|nsk_)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def check_exposure_zones(f) -> None:
    """Plaintext secret copies outside the repository (and outside git's reach)."""
    import os
    from pathlib import Path

    for zone in EXPOSURE_ZONES:
        base = Path(zone)
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path)
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if re.search(r"(?i)(key|secret|token|credential|passw|api[-_]?key)", path.name):
                f.add("warning", "exposure-zone",
                      f"{rel} — filename suggests credential material; shred it once its value has been used")
            if SECRET_FILE_PATTERNS.search(path.name):
                f.add("critical", "exposure-zone", f"{rel} looks like a secret container and is outside the repository")
                continue
            for pat in SECRET_VALUE_PATTERNS:
                if pat.search(text):
                    f.add("critical", "exposure-zone",
                          f"{rel} contains a plaintext credential outside the repository — shred it and rotate")
                    break


def main() -> int:
    ap = argparse.ArgumentParser(description="Fail-closed secret hygiene check")
    ap.add_argument("--history", action="store_true", help="also scan git history (slower)")
    ap.add_argument("--fix-perms", action="store_true", help="chmod 600 the secret files")
    args = ap.parse_args()

    if not is_git_repo():
        print("check_secrets: not a git repository", file=sys.stderr)
        return 2

    f = Findings()
    print("check_secrets: scanning", ROOT)
    check_env_file(f, args.fix_perms)
    tracked = tracked_files()
    print(f"  tracked files: {len(tracked)}")
    check_tracked_secret_containers(f, tracked)
    _, untracked = sh(["git", "ls-files", "--others", "--exclude-standard"])
    candidates = sorted(set(tracked + untracked.splitlines()))
    print(f"  source candidates (including untracked): {len(candidates)}")
    check_file_contents(f, candidates)
    if args.history:
        check_history(f)

    # The audit found a plaintext copy of every credential sitting in the
    # workspace *outside* the repo (an uploaded file). Git-ignore protects the
    # repository; nothing protects the neighbouring directories, so scan them.
    check_exposure_zones(f)

    # runtime report (never prints values)
    try:
        from app import config

        rep = config.secrets_report()
        print(
            f"  runtime: env={rep['environment']} master_key={'present' if rep['master_key']['present'] else 'MISSING'}"
            f" configured={sum(1 for s in rep['secrets'] if s['present'])}"
        )
        for w in rep["warnings"]:
            print(f"  warning: {w}")
        for e in rep["errors"]:
            print(f"  error: {e}")
            f.add("critical", "runtime", e)
    except Exception as exc:  # pragma: no cover - config import failure is itself a finding
        f.add("critical", "runtime", f"configuration failed to load: {exc.__class__.__name__}")

    return f.report()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Provision the least-privilege runtime role (`re_app`) and split the DSNs.

Why this exists
---------------
The web tier used to connect to Neon as `neondb_owner`, a role with
`rolbypassrls = true`. Row-level security was enabled on `re_documents` and
looked impressive in the schema, but the connection that served HTTP bypassed
it: one forgotten `where tenant_id = ...` would have been a cross-tenant leak.
That is finding SEN-003 from `sentinel/agent.py`.

What this script does
---------------------
1. creates or rotates `re_app` with NOSUPERUSER / NOBYPASSRLS / NOCREATEDB /
   NOCREATEROLE and a fresh random password (never printed);
2. applies `db/004_runtime_role.sql`, which grants only DML and forces RLS;
3. rewrites `.env` (mode 0600) with two clearly separated credentials:
       DATABASE_URL            → re_app     (what the web tier uses)
       RE_ADMIN_DATABASE_URL   → owner      (migrations + boot hydration only)

It is idempotent. Run it with the owner DSN in the environment or in `.env`:

    python3 scripts/provision_app_role.py            # plan + apply
    python3 scripts/provision_app_role.py --dry-run  # show what would change
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import db  # noqa: E402
from app import config as cfg  # noqa: E402

ENV_PATH = os.path.join(ROOT, ".env")
ENV_LOCAL = os.path.join(ROOT, ".env.local")
MIGRATION = os.path.join(ROOT, "db", "004_runtime_role.sql")
APP_ROLE = "re_app"


def _owner_dsn() -> str:
    for name in ("RE_ADMIN_DATABASE_URL", "DATABASE_URL"):
        raw = cfg.get(name, "")
        if raw and not _is_app_dsn(raw):
            return db.normalise_dsn(raw)
    raise SystemExit("No owner DSN found (set RE_ADMIN_DATABASE_URL or DATABASE_URL).")


def _is_app_dsn(raw: str) -> bool:
    try:
        return urlsplit(raw).username == APP_ROLE
    except ValueError:
        return False


def _with_user(raw: str, user: str, password: str) -> str:
    parts = urlsplit(raw)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{user}:{quote(password, safe='')}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _env_set(text: str, key: str, value: str) -> str:
    """Replace KEY=... in place, or append. Comments and order are preserved."""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def _write_env(text: str) -> None:
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(ENV_PATH, 0o600)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-password", action="store_true",
                    help="reuse the password already in .env instead of rotating")
    args = ap.parse_args(argv[1:])

    if db.psycopg is None:
        print("psycopg is not installed: pip install 'psycopg[binary]'")
        return 2

    owner = _owner_dsn()
    existing = cfg.get("DATABASE_URL", "")
    password = ""
    if args.keep_password and _is_app_dsn(existing):
        password = urlsplit(existing).password or ""
    password = password or secrets.token_urlsafe(32)

    app_dsn = db.normalise_dsn(_with_user(owner, APP_ROLE, password))
    print(f"owner  host: {db.redact_dsn(owner)}")
    print(f"app    role: {APP_ROLE} (NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE)")
    print(f"app    dsn : {db.redact_dsn(app_dsn)}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    import psycopg
    from psycopg import sql

    with psycopg.connect(owner, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("select rolbypassrls from pg_roles where rolname = %s", (APP_ROLE,))
            row = cur.fetchone()
            if row and row[0]:
                # A role that already holds BYPASSRLS cannot be de-escalated by
                # a non-superuser owner (Neon's owner is not SUPERUSER), so the
                # role is recreated without it. Grants are re-applied below.
                cur.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(APP_ROLE)))
                row = None
                print("role: dropped (held BYPASSRLS)")
            if row is not None:
                cur.execute(sql.SQL("alter role {} nocreatedb nocreaterole").format(sql.Identifier(APP_ROLE)))
                cur.execute(sql.SQL("alter role {} with login password {}")
                            .format(sql.Identifier(APP_ROLE), sql.Literal(password)))
                print("role: rotated")
            else:
                cur.execute(sql.SQL("create role {} login password {} nosuperuser nobypassrls "
                                    "nocreatedb nocreaterole").format(sql.Identifier(APP_ROLE),
                                                                      sql.Literal(password)))
                print("role: created")
            cur.execute("select rolcanlogin, rolsuper, rolbypassrls from pg_roles where rolname = %s",
                        (APP_ROLE,))
            can_login, is_super, bypass = cur.fetchone()
            print(f"role: can_login={can_login} superuser={is_super} bypassrls={bypass}")
            if is_super or bypass:
                print("ABORT: re_app still has elevated attributes")
                return 1

            with open(MIGRATION, encoding="utf-8") as fh:
                cur.execute(fh.read())
            cur.execute("select relname, relrowsecurity, relforcerowsecurity from pg_class "
                        "where relname in ('re_documents','re_audit') order by relname")
            for row in cur.fetchall():
                print(f"rls : {row[0]} enabled={row[1]} forced={row[2]}")

    text = ""
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as fh:
            text = fh.read()
    text = _env_set(text, "RE_ADMIN_DATABASE_URL", owner)
    text = _env_set(text, "DATABASE_URL", app_dsn)
    _write_env(text)
    print(f".env: written 0600 ({len(text)} bytes) — DATABASE_URL now uses {APP_ROLE}")

    # `.env.local` is loaded *after* `.env` and used to carry its own
    # DATABASE_URL, which silently beat the runtime role. One owner per key:
    # the DSNs live in `.env`; `.env.local` keeps only the Neon service URLs.
    if os.path.exists(ENV_LOCAL):
        with open(ENV_LOCAL, encoding="utf-8") as fh:
            local = fh.read()
        if re.search(r"^DATABASE_URL=", local, re.MULTILINE):
            local = re.sub(r"^DATABASE_URL=.*$",
                           "# DATABASE_URL is defined in .env (single source of truth; "
                           ".env.local would override it)", local, count=1, flags=re.MULTILINE)
            fd = os.open(ENV_LOCAL, os.O_WRONLY | os.O_TRUNC)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(local)
            os.chmod(ENV_LOCAL, 0o600)
            print(".env.local: DATABASE_URL override removed (0600)")
    print("next: restart the app so it picks up the runtime role")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

#!/usr/bin/env python3
"""
Realtor Estates — connect a Postgres/Neon database in one safe step.

Usage
-----
    # 1. put the DSN in a file you control (never paste it into a chat):
    #    .env.local  ->  DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
    python3 scripts/connect-db.py --from-env

    # 2. or hand it to the script directly (it is not echoed, ever):
    python3 scripts/connect-db.py --dsn 'postgresql://…'

    # extra flags
    --rls-apply        also apply db/002_rls.sql (needs a real server with roles)
    --env-file PATH    write credentials to another file (default .env.local)
    --no-write         test the connection only, write nothing
    --seed             load the demo dataset if the database is empty

What it does, in order, and stops at the first failure:
    1. normalises the DSN (postgres:// -> postgresql://, sslmode for hosted hosts)
    2. connects with psycopg, prints server version and round-trip latency
    3. applies db/001_init.sql (+ 002 with --rls-apply), idempotently
    4. verifies the append-only audit trigger actually exists
    5. writes the DSN to .env.local with mode 0600 and confirms it is git-ignored
    6. prints a redacted summary — the password never reaches stdout
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as cfg  # noqa: E402
from app import db as dbmod  # noqa: E402


def fail(msg: str) -> "None":
    print(f"FAIL  {msg}")
    raise SystemExit(1)


def read_from_env_file(path: Path) -> str:
    if not path.exists():
        fail(f"{path.name} not found — create it and add DATABASE_URL=…")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    fail(f"{path.name} has no DATABASE_URL= line")


def write_env_file(path: Path, dsn: str) -> None:
    lines: list[str] = []
    if path.exists():
        lines = [ln for ln in path.read_text().splitlines() if not ln.strip().startswith("DATABASE_URL=")]
    lines.append(f"DATABASE_URL={dsn}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    mode = oct(path.stat().st_mode & 0o777)
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(path)], cwd=ROOT
    ).returncode == 0
    print(f"wrote {path.name} ({mode}{'' if ignored else ', NOT git-ignored!'})")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Connect the Postgres/Neon backend.")
    ap.add_argument("--dsn", default="")
    ap.add_argument("--from-env", action="store_true", help="read DATABASE_URL from --env-file")
    ap.add_argument("--env-file", default=".env.local")
    ap.add_argument("--rls-apply", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args(argv[1:])

    env_path = ROOT / args.env_file
    dsn = args.dsn or (read_from_env_file(env_path) if args.from_env else "")
    if not dsn:
        fail("no DSN: pass --dsn, or --from-env with DATABASE_URL in .env.local")

    if not dbmod.driver_available():
        fail("psycopg is not installed: pip install 'psycopg[binary]'")

    aligned = dbmod.normalise_dsn(dsn)
    if not aligned.startswith("postgresql://"):
        fail("that does not look like a Postgres URL (expected postgresql://…)")
    print(f"dsn   {dbmod.redact_dsn(aligned)}")

    backend = None
    try:
        t0 = time.perf_counter()
        backend = dbmod.PgBackend(aligned)  # runs migrate() during construction
        connect_ms = round((time.perf_counter() - t0) * 1000, 1)
        print(f"server {backend._server_version.split(',')[0]}  connect+migrate {connect_ms} ms")

        if args.rls_apply:
            applied = dbmod.migrate(backend.conn, include_rls=True)
            print(f"rls   {'applied ' + str(applied) if applied else 'already up to date'}")

        ping = backend.ping()
        print(f"ping  ok={ping.get('ok')} {ping.get('latency_ms')} ms")

        counts = backend.counts()
        total = sum(counts.values()) if isinstance(counts, dict) else 0
        print(f"rows  {total} documents across {len(counts) if isinstance(counts, dict) else '?'} collections")

        if args.seed and total == 0:
            os.environ["DATABASE_URL"] = aligned
            os.environ["RE_DB"] = "postgres"
            from app.store import Store

            store = Store()
            print(f"seeded  {sum(store.counts().values())} documents")

        if not args.no_write:
            write_env_file(env_path, aligned)
            print("next  npm run build   (41 tests; the Postgres ones use RE_TEST_DATABASE_URL)")
            print("      RE_TEST_DATABASE_URL=<same dsn> npm run verify:pg")

        # The immutability probe deliberately provokes failing statements, so it
        # runs last: on the PGlite stand-in the session does not survive it.
        probe = backend.audit_immutable_check()
        print(f"audit trigger  present={probe.get('trigger_present')} "
              f"update_blocked={probe.get('update_blocked')} "
              f"delete_blocked={probe.get('delete_blocked')} "
              f"(probe rows {probe.get('probe_rows')})")
        if not probe.get("trigger_present"):
            fail("re_audit has no append-only trigger — do not put real tenant data here")
        if probe.get("probe_rows") and not (probe.get("update_blocked") and probe.get("delete_blocked")):
            fail("the audit table is editable — do not put real tenant data in this database")
        if not probe.get("session_recovered"):
            print("note  session not reusable after the probe (PGlite stand-in only; "
                  "restart scripts/pg-local.mjs, or ignore on a real server)")

    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {cfg.redact(str(exc))[:300]}")
    finally:
        if backend is not None:
            backend.close()

    print(json.dumps({"connected": True, "env_file": None if args.no_write else args.env_file}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

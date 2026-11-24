#!/usr/bin/env python3
"""
Run the Postgres/RLS suite against a real database, in one command.

    python3 scripts/verify_postgres.py                 # Neon, from DATABASE_URL
    python3 scripts/verify_postgres.py --real          # a real PostgreSQL, downloaded once
    python3 scripts/verify_postgres.py --local         # throwaway PGlite on a free port
    python3 scripts/verify_postgres.py --url "$NEON_BRANCH_URL"

This closes **G6** in `PLANS.md`: "Postgres/RLS tests skip by default, so the S0
gate 'every pytest passes against Postgres' is unverified in a clean checkout."
A test that skips is not evidence, and a skip that reads like a pass is worse. So:

  * it refuses to report success when the target cannot prove what it claims.
    The local PGlite stand-in shares one session between connections and has no
    role system, so it can verify the schema, migrations, document round trip,
    tenant scoping and the append-only trigger — and it **cannot** verify
    transaction isolation, concurrent writers, or RLS. Those are named as skips
    in the output, with the reason, and the exit code says `partial`.
  * it prints what it did *not* verify. A green run that hides its blind spots is
    the failure mode this script exists to remove.

`--real` is the difference between "the suite passes" and "the gate passes". PGlite
is the real query engine compiled to WASM, but it serves every client from **one
session**, so two connections are not two transactions and `pg_advisory_xact_lock`
proves nothing. `--real` runs an actual `postgres` binary from the
`embedded-postgres` wheel — 18 MB, no root, no Docker, no system package — which
isolates transactions and has a role system, so the concurrency and RLS tests run
instead of skipping.

Exit codes: 0 = everything the target can prove passed · 2 = passed with
documented skips (partial evidence) · 1 = a failure · 3 = no database.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _safe(url: str) -> str:
    """Never print a password. The scanner would catch it, but so should we."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.password:
            netloc = f"{parts.username}:***@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
    except Exception:
        pass
    return re.sub(r"://[^@/]+@", "://***@", url)


def _dsn(url: str) -> str:
    """Normalise a DSN without losing a unix-socket `host=` parameter.

    `app.db.normalise_dsn` exists to add TLS and a connect timeout to hosted
    Postgres. A local server is reached by socket (`?host=/path/pgdata`), and
    rebuilding that URI as a TCP one turns a working connection into a 3-minute
    connect timeout. So: normalise what the app would, then keep only what the
    normaliser dropped.
    """
    from app import db as dbmod

    base = dbmod.normalise_dsn(url)
    if "host=" in url and "%2F" not in url and "host=" not in base:
        sep = "&" if "?" in base else "?"
        tail = url.split("?", 1)[1]
        return f"{base}{sep}{tail}"
    return base


def _connect(url: str, timeout: int = 25):
    import psycopg

    return psycopg.connect(_dsn(url), connect_timeout=timeout)


def _reachable(url: str, timeout: int = 25) -> tuple[bool, str]:
    try:
        conn = _connect(url, timeout)
        conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"[:200]


def _capabilities(url: str) -> dict:
    """What can this target actually prove? Measured, not assumed."""
    caps = {"isolated_transactions": False, "roles": False, "version": "", "why": ""}
    try:
        import psycopg

        from app import db as dbmod

        a = _connect(url)
        b = _connect(url)
        try:
            # Hold BOTH transactions open simultaneously while sampling backend PIDs:
            # through Neon's PgBouncer `-pooler`, sequential autocommit queries release
            # the backend back to the pool in <1 ms and get handed the *same* idle PID,
            # falsely looking like single-session PGlite (`docs/36`). Two concurrent
            # `select pg_backend_pid()` transactions never touch a table (zero DDL
            # deadlock risk) yet force PgBouncer to pin two distinct backends.
            with a.transaction(), b.transaction():
                with a.cursor() as ca, b.cursor() as cb:
                    ca.execute("select pg_backend_pid(), version()")
                    pid_a, version = ca.fetchone()
                    caps["version"] = str(version).split(" on ")[0]
                    cb.execute("select pg_backend_pid()")
                    pid_b = cb.fetchone()[0]
                    caps["isolated_transactions"] = pid_a != pid_b
            a.autocommit = True
            b.autocommit = True
            with a.cursor() as ca, b.cursor() as cb:
                ca.execute("drop table if exists re_caps_probe")
                ca.execute("create table re_caps_probe(x int)")
                cb.execute("insert into re_caps_probe values (1)")
                ca.execute("select count(*) from re_caps_probe")
                seen = ca.fetchone()[0]
                ca.execute("drop table re_caps_probe")
                if seen != 1:
                    caps["isolated_transactions"] = False
                    caps["why"] = "two connections do not see each other's committed rows"
                # "roles" means this target *has a role system*, not "re_app exists
                # yet": the role is created by migration 002, which runs after this
                # probe on a fresh database. Asking the wrong question here made the
                # report say "RLS unproven" about a server that proves it perfectly.
                try:
                    ca.execute("select count(*) from pg_roles")
                    caps["roles"] = ca.fetchone()[0] > 1        # more than just the bootstrap role
                    ca.execute("select rolsuper or rolbypassrls from pg_roles where rolname = current_user")
                    caps["privileged"] = bool(ca.fetchone()[0])
                except Exception:  # noqa: BLE001 - a stand-in may not have pg_roles
                    caps["roles"] = False
        finally:
            a.close()
            b.close()
    except Exception as exc:  # noqa: BLE001
        caps["why"] = f"{exc.__class__.__name__}: {exc}"[:200]
    return caps


def _start_real(data_dir: str):
    """An actual `postgres` server from the `embedded-postgres` wheel.

    Returns (server, uri). The caller stops it; `cleanup_mode=None` means this
    function owns the lifetime rather than the library's atexit hook.
    """
    try:
        from pathlib import Path

        from embedded_postgres import PostgresServer
    except ImportError:
        raise SystemExit(
            "embedded-postgres is not installed. It ships real PostgreSQL binaries:\n"
            "    pip install embedded-postgres\n"
            "(no root, no Docker, ~18 MB) — or use --local for the PGlite stand-in, "
            "or --url to point at a Neon branch.")
    pgdata = Path(data_dir) / "pgdata"
    server = PostgresServer(pgdata, cleanup_mode=None)
    server.ensure_pgdata_inited()
    server.ensure_postgres_running()
    return server, server.get_uri()


def _start_local(data_dir: str) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = {**os.environ, "PG_LOCAL_DATA": data_dir}
    proc = subprocess.Popen(["node", os.path.join(ROOT, "scripts", "pg-local.mjs"), str(port)],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"pg-local exited:\n{proc.stdout.read()[-1500:] if proc.stdout else ''}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc, f"postgresql://postgres@127.0.0.1:{port}/postgres"
        except OSError:
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("pg-local did not start within 120s")


def verdict(passed: int, skipped: int, failed: int, caps: dict) -> tuple[int, list[str], str]:
    """The one place that decides whether the gate passed.

    Pure on purpose: "did this pass?" is the question the whole script exists to
    answer, and an answer computed inline from a subprocess's stdout is untestable.
    A skip is never a pass, and a target that cannot isolate transactions cannot
    prove the concurrency claim no matter what its test report says.
    """
    partial: list[str] = []
    if failed:
        partial.append("failures above; the gate is NOT passed")
    if skipped:
        partial.append(f"{skipped} test(s) skipped — the reasons are printed above")
    if not caps.get("isolated_transactions"):
        partial.append("concurrency/isolation unproven: one session for all connections")
    if not caps.get("roles"):
        partial.append("RLS unproven: no role system on this target "
                       "(point at a Neon branch to prove db/002)")
    if failed:
        return 1, partial, f"FAIL — {failed} failed · {passed} passed · {skipped} skipped"
    if partial:
        return 2, partial, f"PARTIAL — {passed} passed, {skipped} skipped"
    return 0, [], f"PASS — {passed} passed, nothing skipped, RLS and concurrency proved"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="Postgres DSN (default: RE_TEST_DATABASE_URL or DATABASE_URL)")
    ap.add_argument("--local", action="store_true", help="start a throwaway PGlite and use it")
    ap.add_argument("--real", action="store_true",
                    help="start a real PostgreSQL from the embedded-postgres wheel "
                         "(pip install embedded-postgres)")
    ap.add_argument("--from-env", action="store_true",
                    help="also run the tests that opt in to this checkout's own database")
    ap.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    args = ap.parse_args()

    proc = None
    server = None
    tmpdir = None
    url = args.url.strip() or os.environ.get("RE_TEST_DATABASE_URL", "").strip()
    if args.real and not url:
        tmpdir = tempfile.mkdtemp(prefix="re-pg-real-")
        print(f"starting a real PostgreSQL in {tmpdir} …")
        server, url = _start_real(tmpdir)
    elif args.local and not url:
        tmpdir = tempfile.mkdtemp(prefix="re-pg-verify-")
        print(f"starting a local PGlite in {tmpdir} …")
        proc, url = _start_local(os.path.join(tmpdir, "pgdata"))
    elif not url:
        from app import db as dbmod
        url = dbmod.admin_dsn()
    if not url:
        print("no database: pass --url, set RE_TEST_DATABASE_URL, or use --local", file=sys.stderr)
        return 3

    try:
        print(f"target: {_safe(url)}")
        ok, why = _reachable(url)
        if not ok:
            print(f"cannot connect: {why}", file=sys.stderr)
            return 3

        # Migrate first, then ask what this target can prove. Order matters:
        # migration 002 creates `re_app` and 004 forces RLS, so probing before
        # migrating would report "no roles" about a database that is one command
        # away from having them — and the report would be wrong in the direction
        # that hides the gate.
        base = subprocess.run([sys.executable, "-m", "app.db", "migrate"], cwd=ROOT,
                              env={**os.environ, "DATABASE_URL": url, "RE_DB": "postgres"},
                              capture_output=True, text=True, timeout=900)
        print(f"migrations: exit {base.returncode}")
        for line in (base.stdout or base.stderr).strip().splitlines()[-2:]:
            print(f"  {line[:140]}")

        caps = _capabilities(url)
        print(f"server: {caps['version'] or 'unknown'}"
              f"  · isolated transactions: {'yes' if caps['isolated_transactions'] else 'NO'}"
              f"  · re_app role before the RLS migrations: {'yes' if caps['roles'] else 'no'}")
        if not caps["isolated_transactions"]:
            print("  → concurrency and isolation cannot be proved against this target "
                  "(the stand-in shares one session); those tests will skip and say so")

        if caps["roles"]:
            # 002 creates the policy, 003 makes `re_app` impersonable so the policy
            # can be *exercised*, 004 forces it. A skip here would be precisely what
            # G6 is about, so they are applied through the product's own code path.
            role_code = (
                "from app import db as dbmod\n"
                "c = dbmod.psycopg.connect(dbmod.normalise_dsn(__import__('os').environ['DATABASE_URL']),"
                " prepare_threshold=None)\n"
                "c.autocommit = True\n"
                "applied = dbmod.migrate(c, include_rls=True, verbose=False)\n"
                "with c.cursor() as cur:\n"
                "    cur.execute(\"select 1 from pg_roles where rolname = 're_app'\")\n"
                "    has_role = bool(cur.fetchone())\n"
                "print(f'{len(applied)} rls migration(s) applied, re_app={has_role}')\n"
                "c.close()\n"
            )
            role = subprocess.run([sys.executable, "-c", role_code], cwd=ROOT,
                                  env={**os.environ, "DATABASE_URL": url, "RE_DB": "postgres"},
                                  capture_output=True, text=True, timeout=900)
            last = (role.stdout or role.stderr).strip().splitlines()
            print(f"rls migrations: exit {role.returncode} — {last[-1][:120] if last else ''}")
            caps["roles"] = "re_app=True" in (last[-1] if last else "")

        # This script *is* the explicit opt-in for the tests that need a real
        # server, so it sets the flag rather than leaving them skipped. They stay
        # guarded for a bare `pytest` run, where pointing a test suite at a
        # production database by accident must not be possible.
        env = {**os.environ, "RE_TEST_DATABASE_URL": url, "DATABASE_URL": url,
               "RE_DB": "postgres", "RE_DB_STRICT": "1", "RE_TEST_DB_FROM_ENV": "1"}
        targets = ["tests/test_postgres.py", "tests/test_rls.py",
                   "tests/test_function_tables_api.py"]
        report = subprocess.run([sys.executable, "-m", "pytest", *targets, "-q", "-rs",
                                 "--no-header", "-p", "no:cacheprovider"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=2400)
        out = report.stdout + report.stderr
        tail = [ln for ln in out.strip().splitlines() if ln.strip()]
        print()
        print("\n".join(tail[-12:]))

        def count(pattern: str) -> int:
            m = re.search(pattern, out)
            return int(m.group(1)) if m else 0

        passed, skipped, failures = count(r"(\d+) passed"), count(r"(\d+) skipped"), count(r"(\d+) failed")
        code, partial, summary = verdict(passed, skipped, failures, caps)
        print()
        if args.json:
            print(json.dumps({"target": _safe(url), "passed": passed, "skipped": skipped,
                              "failed": failures, "capabilities": caps,
                              "partial": partial, "exit": code}, indent=2))
        print(f"RESULT: {summary}")
        for line in partial:
            if code != 1:
                print(f"  · {line}")
        if code == 2:
            print("  A skip is not a pass. Run against a Neon branch for the whole gate.")
        return code
    finally:
        if server is not None:
            try:
                server.cleanup()
            except Exception:
                pass
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if tmpdir:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

"""
G6: the Postgres/RLS gate, verified in a clean checkout.

`PLANS.md` G6 read: *"Postgres/RLS tests skip by default — the S0 gate 'every
pytest passes against Postgres' is unverified in a clean checkout."* Fourteen
tests skipped on a machine with no database, and a skip that reads like a pass is
worse than no test at all.

`scripts/verify_postgres.py` is the answer, and these tests are about **its
verdict**, not about Postgres itself. The distinction matters: a verifier that
reports PASS when a test skipped, or when the target cannot isolate transactions,
reproduces the original sin in a new place.

  A. the verdict — a skip is not a pass; a stand-in cannot prove concurrency
  B. discovery — a real `postgres` binary is used when one can be found
  C. the real thing — the RLS policy and the concurrent-writer chain, run against
     a downloaded real PostgreSQL (skipped when `embedded-postgres` is absent)
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

vp = pytest.importorskip("verify_postgres", reason="scripts/verify_postgres.py")


# ===========================================================================
# A. the verdict
# ===========================================================================
STRONG = {"isolated_transactions": True, "roles": True}
WEAK = {"isolated_transactions": False, "roles": False}


def test_a_full_run_against_a_real_server_is_the_only_pass():
    code, partial, summary = vp.verdict(passed=21, skipped=0, failed=0, caps=STRONG)
    assert code == 0 and not partial
    assert summary.startswith("PASS")


def test_a_skip_is_not_a_pass():
    """The whole point of G6: a skipped test used to look like a green run."""
    code, partial, summary = vp.verdict(passed=21, skipped=2, failed=0, caps=STRONG)
    assert code == 2, "a skipped test must never report success"
    assert summary.startswith("PARTIAL")
    assert any("skipped" in p for p in partial)


def test_a_shared_session_cannot_prove_concurrency_even_with_no_skips():
    """The stand-in shares one session. Even if every test 'passed', the
    concurrent-writer claim is unproven and the verdict has to say so."""
    code, partial, _ = vp.verdict(passed=20, skipped=0, failed=0, caps=WEAK)
    assert code == 2
    assert any("concurrency" in p for p in partial)
    assert any("RLS" in p for p in partial)


def test_a_failure_always_fails_and_never_looks_partial():
    code, partial, summary = vp.verdict(passed=19, skipped=0, failed=1, caps=STRONG)
    assert code == 1
    assert summary.startswith("FAIL")
    assert any("NOT passed" in p for p in partial)


def test_a_failure_outranks_a_skip():
    code, _, _ = vp.verdict(passed=5, skipped=9, failed=2, caps=WEAK)
    assert code == 1, "a partial-read 'pass' must not hide an actual failure"


# ===========================================================================
# B. discovery
# ===========================================================================
def test_a_dsn_survives_normalisation_without_losing_its_socket():
    """A local server is reached by `?host=/path`; rebuilding the URI as TCP turns
    a working connection into a connect timeout."""
    dsn = "postgresql://postgres:@/postgres?host=/tmp/some-pgdata"
    assert "host=/tmp/some-pgdata" in vp._dsn(dsn)


def test_a_password_is_never_printed():
    shown = vp._safe("postgresql://re_app:sup3rs3cret@db.example.com:5432/neondb")
    assert "sup3rs3cret" not in shown
    assert "db.example.com" in shown


def test_no_database_is_an_explicit_exit_not_a_pass(tmp_path, monkeypatch):
    """Exit 3: distinct from 0 (pass), 1 (fail) and 2 (partial). A missing
    database must never be confused with any of them."""
    env = {**os.environ, "RE_TEST_DATABASE_URL": "", "DATABASE_URL": "",
           "RE_ADMIN_DATABASE_URL": "", "RE_ENV_FILE": str(tmp_path / "empty.env")}
    (tmp_path / "empty.env").write_text("RE_ENV=development\n")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_postgres.py")],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert out.returncode == 3, f"expected 3, got {out.returncode}: {out.stdout[-300:]}"


def test_a_local_stand_in_reports_itself_as_partial():
    """`--local` starts a PGlite, which cannot isolate transactions. The contract is
    exit 2 and a printed blind spot — never 0.

    Skipped when PGlite will not start here. It is a WASM build of Postgres and
    does its own `initdb` in memory; in a small sandbox it is OOM-killed
    (exit 137), and a hard failure on "the test harness has no memory" would be a
    flaky test dressed up as a finding. Note `--real` needs no such allowance: it
    is a native binary, and it is the mode a CI job should use.
    """
    have_node = os.path.exists(os.path.join(ROOT, "node_modules", "@electric-sql"))
    if not have_node:
        pytest.skip("PGlite is not installed in node_modules")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "verify_postgres.py"),
                          "--local"], cwd=ROOT, capture_output=True, text=True, timeout=900)
    if out.returncode == 1 and "pg-local exited" in (out.stderr or ""):
        pytest.skip("PGlite could not start in this environment (it is memory-hungry); "
                    "`--real` covers the same ground with a native binary")
    assert out.returncode == 2, f"a stand-in must report PARTIAL, not {out.returncode}"
    assert "PARTIAL" in out.stdout
    assert "concurrency" in out.stdout.lower(), "the blind spot must be printed, not implied"


# ===========================================================================
# C. the real thing
# ===========================================================================
REAL = importlib.util.find_spec("embedded_postgres") is not None


@pytest.fixture(scope="module")
def real_pg(tmp_path_factory):
    """A real `postgres` binary from the `embedded-postgres` wheel.

    Skipped when it is not installed, with the command that installs it — because
    the alternative is a green run that quietly did not check the thing G6 is
    about.
    """
    if not REAL:
        pytest.skip("pip install embedded-postgres (real PostgreSQL binaries, no root)")
    os.environ.setdefault("XDG_RUNTIME_DIR", "/tmp")
    from pathlib import Path

    from embedded_postgres import PostgresServer

    from app import db as dbmod

    tmp = tmp_path_factory.mktemp("real-pg")
    server = PostgresServer(Path(tmp) / "pgdata", cleanup_mode=None)
    server.ensure_pgdata_inited()
    server.ensure_postgres_running()
    url = server.get_uri()
    conn = dbmod.psycopg.connect(dbmod.normalise_dsn(url), prepare_threshold=None)
    conn.autocommit = True
    dbmod.migrate(conn)
    dbmod.migrate(conn, include_rls=True)
    conn.close()
    yield url
    try:
        server.cleanup()
    except Exception:
        pass


def test_a_real_server_isolates_two_connections(real_pg):
    """The capability `--local` lacks, proved rather than assumed."""
    isolates, why = vp._capabilities(real_pg)["isolated_transactions"], ""
    caps = vp._capabilities(real_pg)
    assert caps["isolated_transactions"], f"two connections are not isolated: {caps.get('why')}"
    assert caps["roles"], "migrations 002/004 should have created a role system"


def test_the_rls_policy_actually_filters_a_restricted_role(real_pg):
    """db/002 is only a control if a non-owner is subject to it."""
    from app import db as dbmod

    conn = dbmod.psycopg.connect(dbmod.normalise_dsn(real_pg), prepare_threshold=None)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("set role re_app")
            cur.execute("select set_config('app.tenant_id', '__no_such_tenant__', false)")
            cur.execute("select count(*) from re_documents")
            visible = cur.fetchone()[0]
            cur.execute("reset role")
        assert visible == 0, f"re_app saw {visible} rows with no tenant set: RLS is decorative"
    finally:
        conn.close()


def test_boot_refuses_a_json_store_in_postgres_strict_mode(real_pg):
    """`RE_DB_STRICT=1` is what stops a deployment from silently falling back to a
    file when the database is misconfigured — the failure that would make every
    number in the product wrong while every page still rendered."""
    env = {**os.environ, "DATABASE_URL": real_pg, "RE_DB": "postgres", "RE_DB_STRICT": "1"}
    out = subprocess.run([sys.executable, "-c",
                          "from app.store import Store; s=Store(); "
                          "print(s.backend_name); assert s.backend_name=='postgres'"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-500:]
    assert "postgres" in out.stdout

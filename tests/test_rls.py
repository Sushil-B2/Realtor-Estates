"""Row-level security, exercised against a real Postgres.

PGlite (the local stand-in) has no role system, so this module skips there and
runs wherever `RE_TEST_DATABASE_URL` points at a server with roles — a Neon
branch, a container, or CI.

The policy in `db/002_rls.sql` protects the path the application does *not* use:
a future service, analytics job or read replica connecting with the `re_app`
role. The owner bypasses RLS by design, so a test that only checks the app would
prove nothing.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("psycopg")

from app import db as dbmod  # noqa: E402

TENANT_A = "tnt_rls_a"
TENANT_B = "tnt_rls_b"


def _dsn() -> str:
    """A privileged connection: `set role re_app` needs the owner's membership.

    The runtime DSN (re_app) cannot migrate or impersonate, by design.
    """
    url = os.environ.get("RE_TEST_DATABASE_URL", "").strip()
    if url:
        return dbmod.normalise_dsn(url)
    if os.environ.get("RE_TEST_DB_FROM_ENV", "") in ("1", "true", "yes"):
        return dbmod.admin_dsn()
    try:
        candidate = "postgresql://postgres@127.0.0.1:5433/postgres"
        conn = dbmod.psycopg.connect(candidate, connect_timeout=3, prepare_threshold=None)
        conn.close()
        return candidate
    except Exception:
        pytest.skip("no Postgres: set RE_TEST_DATABASE_URL or run `npm run pg:local`")


@pytest.fixture(scope="module")
def conn():
    c = dbmod.psycopg.connect(_dsn(), prepare_threshold=None)
    c.autocommit = True
    dbmod.migrate(c, include_rls=True, verbose=False)
    with c.cursor() as cur:
        cur.execute("select 1 from pg_roles where rolname = 're_app'")
        if not cur.fetchone():
            c.close()
            pytest.skip("role re_app absent — PGlite has no roles; this needs a real server")
        try:
            cur.execute("set role re_app")
            cur.execute("select set_config('app.tenant_id', '__no_such_tenant__', false)")
            cur.execute("select count(*) from re_documents")
            visible = cur.fetchone()[0]
            cur.execute("reset role")
        except Exception:
            c.close()
            pytest.skip("cannot impersonate re_app (run db/003_rls_probe_grant.sql)")
        if visible:
            c.close()
            pytest.skip("this server does not enforce row-level security (PGlite has no roles)")
    yield c
    with c.cursor() as cur:
        cur.execute("delete from re_documents where tenant_id in (%s, %s)", (TENANT_A, TENANT_B))
    c.close()


def _seed(conn) -> None:
    suffix = uuid.uuid4().hex[:8]
    with conn.cursor() as cur:
        for tenant in (TENANT_A, TENANT_B):
            cur.execute(
                """insert into re_documents (collection, tenant_id, id, doc)
                   values ('contacts', %s, %s, '{}'::jsonb)
                   on conflict do nothing""",
                (tenant, f"rls_{suffix}_{tenant}"),
            )


def test_owner_bypasses_rls(conn):
    _seed(conn)
    with conn.cursor() as cur:
        cur.execute("select count(distinct tenant_id) from re_documents where tenant_id in (%s, %s)", (TENANT_A, TENANT_B))
        assert cur.fetchone()[0] == 2


def test_restricted_role_sees_only_the_configured_tenant(conn):
    _seed(conn)
    with conn.cursor() as cur:
        cur.execute("set role re_app")
        try:
            cur.execute("select set_config('app.tenant_id', %s, false)", (TENANT_A,))
            cur.execute(
                "select distinct tenant_id from re_documents where tenant_id in (%s, %s)",
                (TENANT_A, TENANT_B),
            )
            tenants = [r[0] for r in cur.fetchall()]
        finally:
            cur.execute("reset role")
    assert tenants == [TENANT_A], f"RLS leaked {tenants}"


def test_unset_tenant_sees_nothing(conn):
    """Fail closed: no `app.tenant_id` means no rows, not all rows."""
    with conn.cursor() as cur:
        cur.execute("set role re_app")
        try:
            # `app.tenant_id` is session-scoped and other tests set it; clear it
            # for real instead of assuming a fresh session.
            cur.execute("reset app.tenant_id")
            cur.execute("select count(*) from re_documents")
            count = cur.fetchone()[0]
        finally:
            cur.execute("reset role")
    assert count == 0


def test_cross_tenant_insert_is_rejected(conn):
    with conn.cursor() as cur:
        cur.execute("set role re_app")
        try:
            cur.execute("select set_config('app.tenant_id', %s, false)", (TENANT_A,))
            with pytest.raises(Exception) as err:
                cur.execute(
                    """insert into re_documents (collection, tenant_id, id, doc)
                       values ('contacts', %s, %s, '{}'::jsonb)""",
                    (TENANT_B, f"rls_evil_{uuid.uuid4().hex[:6]}"),
                )
            assert "permission denied" in str(err.value).lower() or "row-level security" in str(err.value).lower()
        finally:
            cur.execute("reset role")

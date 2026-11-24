"""
Postgres / Neon backend tests.

Self-contained: if `RE_TEST_DATABASE_URL` points at a reachable Postgres, that
is used (point it at a Neon branch to test the real thing). Otherwise the module
spins up a local Postgres (PGlite, real wire protocol) on a free port, so the
suite is reproducible on a laptop and in CI with no external service.

    # against Neon
    RE_TEST_DATABASE_URL="postgresql://user:pw@ep-xxx.neon.tech/neondb" python3 -m pytest tests/test_postgres.py -q

    # against a local stand-in (auto-spawned if RE_TEST_DATABASE_URL is absent)
    node scripts/pg-local.mjs 5433 &
    RE_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5433/postgres python3 -m pytest tests/test_postgres.py -q

What is asserted, and why it matters on Neon specifically: schema idempotence,
DSN normalisation + TLS defaulting, document round-trip with tenant scoping,
an audit log the *database* refuses to let anyone edit, and a store that
survives a process restart without also writing the JSON file.

Known stand-in limitation (PGlite, not Postgres): its wire server shares one
session across clients and does not recover from an aborted transaction. Tests
that deliberately provoke errors therefore run against their own instance.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

psycopg = pytest.importorskip("psycopg", reason="pip install 'psycopg[binary]' to run the Postgres tests")

from app import db as dbmod  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


_REACH_ERR = ""


def _reachable(url: str, timeout: int = 25) -> bool:
    """Can psycopg open a connection? 25 s because a Neon compute may be asleep."""
    global _REACH_ERR
    try:
        import psycopg as pg

        from app import db as dbmod

        conn = pg.connect(dbmod.normalise_dsn(url), connect_timeout=timeout)
        conn.close()
        _REACH_ERR = ""
        return True
    except Exception as exc:  # noqa: BLE001 - any driver/network error means "not usable"
        _REACH_ERR = f"{exc.__class__.__name__}: {exc}"
        return False


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def pg_url():
    """A reachable Postgres URL.

    Order of preference:
      1. RE_TEST_DATABASE_URL — point this at a Neon branch to test the real thing.
      2. a local stand-in already listening on 5433 (`npm run pg:local`).
      3. skip, with the command to fix it.

    No spawning here on purpose: PGlite's cold `initdb` can exhaust memory in a
    small sandbox, and copying clusters per test filled the filesystem. One
    explicit server, owned by the developer or CI, is simpler and reproducible.
    """
    # These tests create schema and inspect the catalogue: they need the
    # privileged DSN, not the re_app runtime role, which has no DDL rights by
    # design (SEN-003). RE_TEST_DB_FROM_ENV opts in to using this checkout's
    # configured database instead of a throwaway one.
    external = os.environ.get("RE_TEST_DATABASE_URL", "").strip()
    if not external and os.environ.get("RE_TEST_DB_FROM_ENV", "") in ("1", "true", "yes"):
        external = dbmod.admin_dsn()
    if external:
        if _reachable(external):
            yield external
            return
        pytest.skip(f"RE_TEST_DATABASE_URL is set but unreachable ({_REACH_ERR[:160]})")
    if _port_open(5433):
        url = "postgresql://postgres@127.0.0.1:5433/postgres"
        if _reachable(url):
            yield url
            return
    pytest.skip("no Postgres: set RE_TEST_DATABASE_URL (Neon branch) or run `npm run pg:local`")


def _isolates_transactions(url: str) -> tuple[bool, str]:
    """Does this server give two connections their own transactions?

    The local stand-in (`scripts/pg-local.mjs`, PGlite) shares **one session**
    across every client: `pg_backend_pid()` is identical on two connections and
    uncommitted work from one is visible to the other. Anything that tests
    isolation — a concurrent-writer test, an advisory lock, a rollback — is then
    testing the stand-in rather than the product, and it fails in a way that looks
    exactly like a real defect.

    Probe rather than guess from a port number: `:5433` says nothing about which
    server is listening on it.
    """
    try:
        import psycopg as pg

        from app import db as dbmod

        a = pg.connect(dbmod.normalise_dsn(url), connect_timeout=25, prepare_threshold=None)
        b = pg.connect(dbmod.normalise_dsn(url), connect_timeout=25, prepare_threshold=None)
        try:
            # Sample backend PIDs inside two simultaneous transactions so PgBouncer
            # (`-pooler`) pins two backends instead of recycling one idle backend
            # between sequential autocommit statements (`docs/36`).
            with a.transaction(), b.transaction():
                with a.cursor() as ca, b.cursor() as cb:
                    ca.execute("select pg_backend_pid()")
                    cb.execute("select pg_backend_pid()")
                    pid_a, pid_b = ca.fetchone()[0], cb.fetchone()[0]
            a.autocommit = True          # no open transaction to deadlock on the DDL
            b.autocommit = True
            with a.cursor() as ca, b.cursor() as cb:
                ca.execute("drop table if exists re_isolation_probe")
                ca.execute("create table re_isolation_probe(x int)")
                cb.execute("insert into re_isolation_probe values (1)")
                ca.execute("select count(*) from re_isolation_probe")
                leaked = ca.fetchone()[0]
                ca.execute("drop table re_isolation_probe")
                if leaked != 1:
                    return False, "a committed row is not visible to the other connection"
        finally:
            a.close()
            b.close()
        if pid_a != pid_b:
            return True, ""
        return False, (f"shared session: two connections report the same backend pid ({pid_a})")
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"


@pytest.fixture(scope="module")
def backend(pg_url):
    from app import db as dbmod

    db = dbmod.PgBackend(pg_url)
    yield db
    db.close()


def _env(url: str, **extra):
    env = {**os.environ, "DATABASE_URL": url, "RE_DB": "postgres", "RE_DB_STRICT": "1"}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_dsn_normalisation():
    from app import db as dbmod

    raw = "postgres://user:s3cret@ep-cool-1234.ap-south-1.aws.neon.tech/neondb"
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = raw
    try:
        d = dbmod.dsn()
        assert d.startswith("postgresql://"), "legacy postgres:// scheme must be normalised"
        assert "sslmode=require" in d, "hosted Neon hosts must default to TLS"
        assert "connect_timeout=10" in d
        assert "s3cret" not in json.dumps(dbmod.status()), "DSN password leaked into status output"
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old


def test_migrations_are_idempotent(backend):
    from app import db as dbmod

    again = dbmod.migrate(backend.conn)
    assert again == [], f"second run should apply nothing, applied {again}"
    with backend.conn.cursor() as cur:
        cur.execute("select count(*) from re_migrations")
        assert cur.fetchone()[0] >= 1
    backend.conn.commit()


def test_documents_roundtrip_and_tenant_scope(backend):
    docs = [
        ("contacts", "tnt_pgtest_a", {"id": "cnt_pgtest_a1", "tenant_id": "tnt_pgtest_a", "name": "A", "created_at": 1}),
        ("contacts", "tnt_pgtest_b", {"id": "cnt_pgtest_b1", "tenant_id": "tnt_pgtest_b", "name": "B", "created_at": 1}),
    ]
    assert backend.upsert_documents(docs) == 2
    loaded = backend.load_all()
    ids_a = {d["id"] for d in loaded.get("contacts", []) if d["tenant_id"] == "tnt_pgtest_a"}
    ids_b = {d["id"] for d in loaded.get("contacts", []) if d["tenant_id"] == "tnt_pgtest_b"}
    assert "cnt_pgtest_a1" in ids_a and "cnt_pgtest_b1" in ids_b
    assert "cnt_pgtest_b1" not in ids_a, "tenant scoping leaked across tenants"

    # upsert is idempotent, not an append
    backend.upsert_documents([("contacts", "tnt_pgtest_a", {**docs[0][2], "name": "A2"})])
    with backend.conn.cursor() as cur:
        cur.execute(
            "select doc->>'name', count(*) from re_documents "
            "where collection='contacts' and tenant_id='tnt_pgtest_a' group by 1"
        )
        rows = dict(cur.fetchall())
    backend.conn.commit()
    assert rows == {"A2": 1}

    assert backend.delete_document("contacts", "tnt_pgtest_a", "cnt_pgtest_a1") is True
    assert backend.delete_document("contacts", "tnt_pgtest_a", "cnt_pgtest_a1") is False

    # Leave nothing behind. A test tenant that survives the run is a row the
    # sentinel's "PII encrypted at rest" check then flags, and a synthetic row
    # in a production database is exactly the kind of debris an audit should
    # not have to explain (found as cnt_pgtest_b1).
    backend.delete_document("contacts", "tnt_pgtest_b", "cnt_pgtest_b1")
    with backend.conn.cursor() as cur:
        cur.execute("select count(*) from re_documents where tenant_id like 'tnt_pgtest%'")
        assert cur.fetchone()[0] == 0, "test rows leaked into the database"
    backend.conn.commit()


def test_audit_append_only_trigger_is_installed(backend):
    """The immutability must be enforced by Postgres, not only by the app.

    Asserted by inspecting the catalogue rather than by provoking an error:
    an aborted transaction inside a shared session makes a test suite
    order-dependent (PGlite) and hides real failures behind cascading errors.
    """
    with backend.conn.cursor() as cur:
        cur.execute(
            """
            select t.tgname, p.proname, pg_get_triggerdef(t.oid)
            from pg_trigger t
            join pg_class c on c.oid = t.tgrelid
            join pg_proc p on p.oid = t.tgfoid
            where c.relname = 're_audit' and not t.tgisinternal
            """
        )
        rows = cur.fetchall()
    backend.conn.commit()
    assert rows, "re_audit has no triggers — history could be edited"
    name, fn, ddl = rows[0]
    assert "block_mutation" in fn, f"unexpected trigger function {fn}"
    ddl_up = ddl.upper()
    assert "BEFORE" in ddl_up and "UPDATE" in ddl_up and "DELETE" in ddl_up, ddl


def test_audit_tampering_is_rejected(backend):
    """Live proof that Postgres itself refuses to edit or delete history.

    Each attempt runs inside its own SAVEPOINT (`conn.transaction()`), so the
    failed statement is rolled back to the savepoint and the session stays
    usable — verified at the end of the test. Without that, one aborted
    transaction would poison every later connection to a PGlite stand-in.
    """
    marker = f"aud_pgtest_{os.urandom(4).hex()}"
    backend.append_audit(
        {
            "tenant_id": "tnt_pgtest_a",
            "id": marker,
            "actor": "pytest",
            "action": "test.audit",
            "target": "",
            "meta": {"k": "v"},
            "at": int(time.time()),
            "prev_hash": "",
            "hash": "deadbeef",
        }
    )
    for sql in (
        f"update re_audit set actor = 'tamper' where id = '{marker}'",
        f"delete from re_audit where id = '{marker}'",
    ):
        with pytest.raises(Exception) as err:
            with backend.conn.transaction():  # savepoint
                with backend.conn.cursor() as cur:
                    cur.execute(sql)
        assert "append-only" in str(err.value), f"unexpected error: {err.value}"

    assert backend._healthy(), "session was left unusable after the blocked writes"
    rows = backend.audit_rows("tnt_pgtest_a")
    assert any(r.get("id") == marker for r in rows), "the audit row disappeared"


def test_store_runs_on_postgres_and_survives_a_restart(pg_url, tmp_path):
    """End-to-end: seed → write → new process → same data, and no JSON copy."""
    smoke = tmp_path / "smoke.py"
    smoke.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        f"from app.store import Store\n"
        f"s = Store(path={str(tmp_path / 'store.json')!r})\n"
        'print(json.dumps({"backend": s.backend_name, "contacts": len(s.db["contacts"]), "docs": sum(len(v) for v in s.db.values() if isinstance(v, list))}))\n',
        encoding="utf-8",
    )
    runs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, str(smoke)],
            capture_output=True,
            text=True,
            env=_env(pg_url, RE_DATA_DIR=str(tmp_path)),
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-1500:]
        runs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert runs[0]["backend"] == "postgres", runs
    assert not (tmp_path / "store.json").exists(), "Postgres mode must not also write the JSON store"
    assert runs[1]["contacts"] == runs[0]["contacts"] >= 1, "data must reload from Postgres, not reseed"


def test_healthz_reports_the_backend():
    from app import db as dbmod

    st = dbmod.status()
    assert st["backend"] in ("postgres", "json")
    assert ":***@" in st["dsn"] or st["dsn"] == "", "health output must not carry a password"


def test_audit_chain_survives_concurrent_writers(pg_url):
    """Two writers, one tenant, one linear chain.

    Before `append_audit()` took a per-tenant advisory lock, each writer computed
    `prev_hash` from its own view of the tail and the log forked — which is
    indistinguishable from tampering once you look at the chain.
    """
    import threading

    isolates, why = _isolates_transactions(pg_url)
    if not isolates:
        pytest.skip(f"needs a server that isolates transactions — {why} "
                    f"(point RE_TEST_DATABASE_URL at a Neon branch or a real local Postgres)")

    from app import db as dbmod
    from app import security as sec

    tenant = "tnt_conc_" + os.urandom(3).hex()
    errors: list[str] = []

    def writer(n: int) -> None:
        try:
            b = dbmod.PgBackend(pg_url)
            for i in range(5):
                b.append_audit(
                    {
                        "tenant_id": tenant,
                        "id": f"aud_{n}_{i}_{os.urandom(2).hex()}",
                        "actor": f"w{n}",
                        "action": "concurrency.test",
                        "target": "",
                        "meta": {"n": n, "i": i},
                        "at": int(time.time()),
                        "prev_hash": "",
                        "hash": "",
                    }
                )
            b.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{exc.__class__.__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not errors, f"writer failed: {errors}"
    b = dbmod.PgBackend(pg_url)
    try:
        # rows for *this* tenant only: `re_audit.seq` is a global sequence, so a
        # load_all() slice is the whole installation's interleaved history and
        # counting it measures other tests' writes as much as this one's
        rows = b.audit_rows(tenant)
    finally:
        b.close()
    assert len(rows) == 10, f"expected 10 entries for this tenant, got {len(rows)}"
    prev = ""
    for idx, row in enumerate(rows):
        assert row["prev_hash"] == prev, f"chain forked at {idx}"
        assert sec.audit_hash(row) == row["hash"], f"hash mismatch at {idx}"
        prev = row["hash"]

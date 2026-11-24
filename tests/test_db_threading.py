"""
The two properties that make a threaded server safe on one Postgres session.

Both were found the hard way, on the running demo, not by reading code:

* the automation worker (a background thread since v1.11.0) shares one psycopg
  connection with the request threads. Two statements interleaving inside one
  transaction meant a tick's `set_config('app.tenant_id')` landed inside a login's
  insert, and RLS refused the audit row for belonging to the wrong workspace;
* a single failed statement aborts the session's transaction, so every later
  statement fails with "current transaction is aborted" — one bad read took the
  whole workspace down until a restart.

These tests are pure fakes (no Postgres required) so they run everywhere.
"""
from __future__ import annotations

import threading
import time

import pytest

from app import db as dbmod
from app.store import Store


class FakeCursor:
    def __init__(self, log: list, fail: bool = False):
        self.log = log
        self.fail = fail

    def execute(self, sql, params=None):
        self.log.append(("execute", sql[:40], params))
        if self.fail:
            raise RuntimeError("new row violates row-level security policy")

    def fetchall(self):
        return [{"doc": {"id": "run_1", "tenant_id": "tnt_1"}}]

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """A session that records what happened to it."""

    def __init__(self, fail_once: bool = False):
        self.log: list = []
        self.fail_once = fail_once
        self.rolled_back = 0
        self.committed = 0
        self.kwargs = {"row_factory": object}

    def cursor(self, **kwargs):
        fail = self.fail_once
        self.fail_once = False
        return FakeCursor(self.log, fail=fail)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def backend_with(conn) -> dbmod.PgBackend:
    """A PgBackend with no real connection — just the parts under test."""
    backend = object.__new__(dbmod.PgBackend)
    backend._lock = threading.RLock()
    backend.conn = conn
    backend._admin_conn = None
    return backend


def test_a_failed_statement_is_rolled_back_so_the_session_survives():
    conn = FakeConn(fail_once=True)
    backend = backend_with(conn)

    def work(c):
        with c.cursor() as cur:
            cur.execute("insert into re_audit ...")   # fails
        c.commit()
        return "done"

    with pytest.raises(RuntimeError):
        backend._run(work)
    assert conn.rolled_back == 1, "an aborted transaction must be reset, not left for the next thread"
    assert conn.committed == 0

    # the same session then serves a healthy statement
    assert backend._run(work) == "done"
    assert conn.committed == 1


def test_two_threads_never_interleave_statements_on_one_session():
    conn = FakeConn()
    backend = backend_with(conn)
    inside = {"now": 0, "max": 0}
    guard = threading.Lock()
    overlap = []

    def work(c):
        with guard:
            inside["now"] += 1
            inside["max"] = max(inside["max"], inside["now"])
        time.sleep(0.01)          # widen the window a real statement would have
        with guard:
            inside["now"] -= 1
        return "ok"

    threads = [threading.Thread(target=backend._run, args=(work,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert inside["max"] == 1, "two threads were inside the session at once"
    assert overlap == []


class FakeDocumentsBackend:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def documents(self, collection, tenant_id):
        self.calls.append((collection, tenant_id))
        return [dict(r) for r in self.rows.get(collection, [])]


def test_reload_replaces_only_one_tenant():
    store = object.__new__(Store)
    store.lock = threading.RLock()
    store.backend = FakeDocumentsBackend({"runs": [{"id": "run_new", "tenant_id": "tnt_1"}]})
    store.backend_name = "postgres"
    store.db = {"runs": [{"id": "run_old", "tenant_id": "tnt_1"},
                         {"id": "run_other", "tenant_id": "tnt_2"}]}

    assert store.reload("runs", "tnt_1") == 1
    ids = {r["id"] for r in store.db["runs"]}
    assert ids == {"run_new", "run_other"}, "the other tenant's rows must be untouched"
    assert store.backend.calls == [("runs", "tnt_1")]


def test_reload_many_refreshes_several_collections_in_one_call():
    class MultiBackend:
        def __init__(self):
            self.calls = 0

        def documents_many(self, collections, tenant_id):
            self.calls += 1
            return {name: [{"id": f"{name}_new", "tenant_id": tenant_id}] for name in collections}

        def documents(self, collection, tenant_id):   # must not be used
            raise AssertionError("reload_many fell back to a per-collection read")

    store = object.__new__(Store)
    store.lock = threading.RLock()
    store.backend = MultiBackend()
    store.backend_name = "postgres"
    store.db = {"runs": [{"id": "run_old", "tenant_id": "tnt_1"}],
                "contacts": [{"id": "cnt_old", "tenant_id": "tnt_1"},
                             {"id": "cnt_other", "tenant_id": "tnt_2"}]}

    assert store.reload_many(["runs", "contacts"], "tnt_1") == 2
    assert store.backend.calls == 1, "a tick must not make one query per collection"
    assert {r["id"] for r in store.db["runs"]} == {"runs_new"}
    assert {c["id"] for c in store.db["contacts"]} == {"contacts_new", "cnt_other"}


def test_reload_is_a_no_op_without_a_database():
    store = object.__new__(Store)
    store.lock = threading.RLock()
    store.backend = None
    store.backend_name = "json"
    store.db = {"runs": [{"id": "run_old", "tenant_id": "tnt_1"}]}
    assert store.reload("runs", "tnt_1") == 0
    assert store.db["runs"][0]["id"] == "run_old"

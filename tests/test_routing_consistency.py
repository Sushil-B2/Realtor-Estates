"""Shared routing records stay current across instances and are never undone by a stale copy.

Runs without a database: a fake shared backend stands in for PostgreSQL. The real
two-instance proof is tests/test_postgres_routing_consistency.py.
"""
import copy
import threading
import time
import types

import pytest

import app.db as dbmod
from app import api
from app.store import Store

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"  # public seeded demo credential


class RoutingBackend:
    """Fake shared database with a change counter per collection."""
    rows = {}
    versions = {}
    loads = []

    def __init__(self):
        pass

    @classmethod
    def bump(cls, collection):
        cls.versions[collection] = cls.versions.get(collection, 0) + 1

    def _all(self, collection):
        out = []
        for (name, _tid), records in self.rows.items():
            if name == collection:
                out.extend(copy.deepcopy(list(records.values())))
        return out

    def counts(self):
        return {"tenants": len(self._all("tenants"))}

    def load_collection_admin(self, collection):
        return self._all(collection)

    def load_collections_admin(self, collections):
        RoutingBackend.loads.append(sorted(collections))
        return {name: self._all(name) for name in collections}

    def routing_signature(self, collections):
        return {name: [len(self._all(name)), self.versions.get(name, 0)] for name in collections}

    def documents(self, collection, tenant):
        return copy.deepcopy(list(self.rows.get((collection, tenant), {}).values()))

    def get_document(self, collection, tenant, rid):
        return copy.deepcopy(self.rows.get((collection, tenant), {}).get(rid))

    def upsert_document(self, collection, tenant, doc):
        self.rows.setdefault((collection, tenant), {})[doc["id"]] = copy.deepcopy(doc)
        RoutingBackend.bump(collection)

    def merge_document(self, collection, tenant, rid, patch, *, expect_hash=None, require_live=False):
        doc = self.rows.get((collection, tenant), {}).get(rid)
        if doc is None:
            return None
        doc.update(copy.deepcopy(patch))
        RoutingBackend.bump(collection)
        return copy.deepcopy(doc)

    def delete_document(self, collection, tenant, rid):
        gone = self.rows.get((collection, tenant), {}).pop(rid, None) is not None
        if gone:
            RoutingBackend.bump(collection)
        return gone

    def find_session_by_refresh_hash(self, digest):
        return None, False


def configure(monkeypatch):
    RoutingBackend.rows = {("tenants", "t1"): {"t1": {"id": "t1", "tenant_id": "t1", "name": "One"}}}
    RoutingBackend.versions = {}
    RoutingBackend.loads = []
    monkeypatch.setenv("RE_DB", "postgres")
    monkeypatch.setenv("RE_STATELESS_READS", "1")
    monkeypatch.setattr(dbmod, "configured", lambda: True)
    monkeypatch.setattr(dbmod, "driver_available", lambda: True)
    monkeypatch.setattr(dbmod, "PgBackend", RoutingBackend)


def ids(store, collection):
    return {r["id"] for r in store.db[collection]}


def test_records_created_elsewhere_reach_an_instance_started_earlier(tmp_path, monkeypatch):
    configure(monkeypatch)
    a = Store(str(tmp_path / "a.json"))
    b = Store(str(tmp_path / "b.json"))
    a.insert("users", "t1", {"id": "u2", "email": "new@example.test", "active": True})
    a.insert("ingest_keys", "t1", {"id": "k2", "active": True})
    a.insert("funnels", "t1", {"id": "f2", "slug": "new-funnel"})
    assert "u2" not in ids(b, "users")
    b.sync_routing()
    assert "u2" in ids(b, "users") and "k2" in ids(b, "ingest_keys") and "f2" in ids(b, "funnels")


def test_sync_reloads_only_what_changed(tmp_path, monkeypatch):
    configure(monkeypatch)
    a = Store(str(tmp_path / "a.json"))
    b = Store(str(tmp_path / "b.json"))
    b.sync_routing()
    RoutingBackend.loads.clear()
    b.sync_routing()
    assert RoutingBackend.loads == []
    a.insert("funnels", "t1", {"id": "f3", "slug": "another"})
    b.sync_routing()
    assert RoutingBackend.loads == [["funnels"]]


def test_a_stale_copy_cannot_undo_a_revocation_made_elsewhere(tmp_path, monkeypatch):
    configure(monkeypatch)
    a = Store(str(tmp_path / "a.json"))
    b = Store(str(tmp_path / "b.json"))
    a.insert("ingest_keys", "t1", {"id": "k1", "active": True})
    b.sync_routing()
    a.update("ingest_keys", "t1", "k1", {"active": False, "revoked_at": 123})
    # b still holds active=True and records a use without syncing first
    b.update("ingest_keys", "t1", "k1", {"last_used_at": 456})
    stored = RoutingBackend.rows[("ingest_keys", "t1")]["k1"]
    assert stored["active"] is False and stored["revoked_at"] == 123 and stored["last_used_at"] == 456
    assert next(r for r in b.db["ingest_keys"] if r["id"] == "k1")["active"] is False


def test_updating_a_record_deleted_elsewhere_does_not_bring_it_back(tmp_path, monkeypatch):
    configure(monkeypatch)
    a = Store(str(tmp_path / "a.json"))
    b = Store(str(tmp_path / "b.json"))
    a.insert("funnels", "t1", {"id": "f1", "slug": "gone-soon"})
    b.sync_routing()
    a.remove("funnels", "t1", "f1")
    assert b.update("funnels", "t1", "f1", {"name": "edit"}) is None
    assert "f1" not in ids(b, "funnels")
    assert "f1" not in RoutingBackend.rows.get(("funnels", "t1"), {})


def test_sync_is_a_no_op_outside_per_request_mode(tmp_path):
    store = Store(str(tmp_path / "file.json"))
    assert store.backend is None
    store.sync_routing()  # must not raise or touch anything


class OperationalError(Exception):
    """Named like the driver's connection error, which is how db.py classifies it."""


class FakeConn:
    def __init__(self, name):
        self.name, self.closed, self.dead = name, False, False

    def close(self):
        self.closed = True

    def rollback(self):
        pass


def admin_backend(monkeypatch, connect):
    backend = object.__new__(dbmod.PgBackend)
    backend.conn = FakeConn("runtime")
    backend._admin_conn = None
    backend._admin_attempted = False
    backend._admin_retry_at = 0.0
    backend._admin_dsn = "configured"
    backend._lock = threading.RLock()
    monkeypatch.setattr(dbmod, "normalise_dsn", lambda _v: "")
    monkeypatch.setattr(dbmod, "psycopg", types.SimpleNamespace(
        connect=connect, OperationalError=OperationalError, InterfaceError=OperationalError))
    return backend


def test_maintenance_connection_is_reopened_after_the_database_drops_it(monkeypatch):
    made = []

    def connect(*_a, **_k):
        made.append(FakeConn(f"admin{len(made) + 1}"))
        return made[-1]

    backend = admin_backend(monkeypatch, connect)
    first = backend.admin
    first.dead = True  # dropped by the server; the driver does not know yet

    def work(conn):
        if conn.dead:
            raise OperationalError("terminating connection due to administrator command")
        return conn.name

    assert backend._run(work, conn=backend.admin) == "admin2"
    assert first.closed is True and len(made) == 2
    assert backend.admin is made[1]


def test_a_second_failure_after_reopening_is_an_outage_not_a_fallback(monkeypatch):
    backend = admin_backend(monkeypatch, lambda *_a, **_k: FakeConn("admin"))

    def always_fails(_conn):
        raise OperationalError("server closed the connection unexpectedly")

    with pytest.raises(dbmod.StorageUnavailable):
        backend._run(always_fails, conn=backend.admin)


def test_failed_maintenance_connection_is_retried_after_the_wait_not_before(monkeypatch):
    attempts = []

    def connect(*_a, **_k):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("synthetic: database waking up")
        return FakeConn("admin")

    clock = [1000.0]
    fake_time = types.SimpleNamespace(**{n: getattr(time, n) for n in dir(time) if not n.startswith("_")})
    fake_time.monotonic = lambda: clock[0]
    backend = admin_backend(monkeypatch, connect)
    monkeypatch.setattr(dbmod, "time", fake_time)
    for _ in range(2):
        with pytest.raises(dbmod.StorageUnavailable):
            _ = backend.admin
    assert len(attempts) == 1  # no hammering inside the wait
    clock[0] += dbmod.PgBackend.ADMIN_RETRY_SECONDS + 0.1
    conn = backend.admin
    assert conn.name == "admin" and conn is not backend.conn and len(attempts) == 2


def call(store, method, path, body=None, token="", headers=None):
    h = {"user-agent": "pytest-routing"}
    if token:
        h["authorization"] = "Bearer " + token
    h.update(headers or {})
    return api.handle(store, method, path, {}, body or {}, h, "203.0.113.30", None)


def test_storage_outage_answers_a_retryable_503_not_a_500(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "s.json"))

    def down():
        raise dbmod.StorageUnavailable("synthetic outage")

    monkeypatch.setattr(store, "sync_routing", down)
    status, payload, extra = call(store, "GET", "/api/v1/tenant")
    assert status == 503 and payload["error"]["code"] == "STORAGE_UNAVAILABLE" and extra.get("Retry-After") == "2"
    monkeypatch.setattr(store, "sync_routing", lambda: None)
    monkeypatch.setattr(store, "public_funnel_by_slug", lambda *_a, **_k: down())
    status, payload, _ = call(store, "POST", "/api/v1/public/funnel/any-slug/submit",
                              {"name": "Synthetic", "phone": "+91 90000 00031", "consent": True})
    assert status == 503 and payload["error"]["code"] == "STORAGE_UNAVAILABLE"


def test_workspace_settings_features_and_kill_switch_are_written_through_the_store(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "w.json"))
    writes = []
    original = store.update

    def recording(coll, tid, rid, patch):
        writes.append((coll, rid == tid, dict(patch)))
        return original(coll, tid, rid, patch)

    monkeypatch.setattr(store, "update", recording)
    status, login, _ = call(store, "POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD})
    assert status == 200
    token = login["token"]
    writes.clear()
    status, _, _ = call(store, "PATCH", "/api/v1/tenant",
                        {"name": "Renamed Workspace", "settings": {"timezone": "Asia/Kolkata"}}, token)
    assert status == 200
    assert ("tenants", True) == writes[0][:2] and writes[0][2]["name"] == "Renamed Workspace"
    assert writes[0][2]["settings"]["timezone"] == "Asia/Kolkata"
    status, body, _ = call(store, "PATCH", "/api/v1/tenant/features", {"social_scheduler": False}, token)
    assert status == 200 and body["features"]["social_scheduler"] is False
    assert writes[-1][0] == "tenants" and writes[-1][2]["features"]["social_scheduler"] is False
    status, ticket, _ = call(store, "POST", "/api/v1/auth/step-up",
                             {"action": "kill_switch", "target": "engage", "password": PASSWORD}, token)
    assert status == 200
    status, _, _ = call(store, "POST", "/api/v1/tenant/kill-switch", {"engage": True}, token,
                        {"x-step-up": ticket["ticket"]})
    assert status == 200 and writes[-1] == ("tenants", True, {"kill_switch": True})
    reopened = Store(str(tmp_path / "w.json"))
    tenant = next(t for t in reopened.db["tenants"] if t["name"] == "Renamed Workspace")
    assert tenant["kill_switch"] is True and tenant["features"]["social_scheduler"] is False

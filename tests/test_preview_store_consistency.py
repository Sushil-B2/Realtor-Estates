"""Simulate two independent stores sharing one fake backend, without external services."""
import copy
import app.db as dbmod
from app import sessions, security, shared_state
from app.store import Store

class SharedBackend:
    rows = {}
    def __init__(self):
        pass
    def counts(self):
        return {'tenants': 1}
    def load_collection_admin(self, collection):
        return [{'id': 't1', 'tenant_id': 't1'}] if collection == 'tenants' else []
    def load_all(self):
        data = {'tenants': self.load_collection_admin('tenants')}
        for (collection, _tid), records in self.rows.items():
            data.setdefault(collection, []).extend(copy.deepcopy(list(records.values())))
        return data
    def documents(self, collection, tenant):
        return copy.deepcopy(list(self.rows.get((collection, tenant), {}).values()))
    def get_document(self, collection, tenant, rid):
        return copy.deepcopy(self.rows.get((collection, tenant), {}).get(rid))
    def upsert_document(self, collection, tenant, doc):
        self.rows.setdefault((collection, tenant), {})[doc['id']] = copy.deepcopy(doc)
    def merge_document(self, collection, tenant, rid, patch, *, expect_hash=None, require_live=False):
        doc = self.rows.get((collection, tenant), {}).get(rid)
        if doc is None:
            return None
        if expect_hash is not None and doc.get('refresh_hash') != expect_hash:
            return None
        if require_live and doc.get('revoked_at'):
            return None
        doc.update(copy.deepcopy(patch))
        return copy.deepcopy(doc)
    def delete_document(self, collection, tenant, rid):
        return self.rows.get((collection, tenant), {}).pop(rid, None) is not None
    def find_session_by_refresh_hash(self, digest):
        superseded = (None, False)
        for (collection, _tid), records in self.rows.items():
            if collection != 'sessions':
                continue
            for doc in records.values():
                if doc.get('refresh_hash') == digest:
                    return copy.deepcopy(doc), True
                if any(old.get('h') == digest for old in doc.get('refresh_history') or []):
                    superseded = (copy.deepcopy(doc), False)
        return superseded

def configure(monkeypatch, stateless, fresh=None):
    SharedBackend.rows = {}
    if fresh is None:
        monkeypatch.delenv('RE_FRESH_SESSIONS', raising=False)
    else:
        monkeypatch.setenv('RE_FRESH_SESSIONS', '1' if fresh else '0')
    monkeypatch.setenv('RE_DB', 'postgres')
    monkeypatch.setenv('RE_STATELESS_READS', '1' if stateless else '0')
    monkeypatch.setattr(dbmod, 'configured', lambda: True)
    monkeypatch.setattr(dbmod, 'driver_available', lambda: True)
    monkeypatch.setattr(dbmod, 'PgBackend', SharedBackend)

def test_snapshot_mode_other_instance_does_not_see_new_contact(tmp_path, monkeypatch):
    configure(monkeypatch, False)
    writer = Store(str(tmp_path / 'a.json'))
    reader = Store(str(tmp_path / 'b.json'))
    doc = writer.insert('contacts', 't1', {'name': 'Synthetic'}, prefix='cnt')
    assert writer.get('contacts', 't1', doc['id']) is not None
    assert reader.get('contacts', 't1', doc['id']) is None
    reader.reload('contacts', 't1')
    assert reader.get('contacts', 't1', doc['id']) is not None

def test_stateless_mode_other_instance_sees_new_contact_and_tenant_isolation(tmp_path, monkeypatch):
    configure(monkeypatch, True)
    writer = Store(str(tmp_path / 'a.json'))
    reader = Store(str(tmp_path / 'b.json'))
    doc = writer.insert('contacts', 't1', {'name': 'Synthetic'}, prefix='cnt')
    assert reader.get('contacts', 't1', doc['id'])['name'] == 'Synthetic'
    assert reader.get('contacts', 't2', doc['id']) is None
    assert len(reader.all('contacts', 't1')) == 1
    assert reader.all('contacts', 't2') == []

def test_legacy_cached_session_remains_active_on_other_instance(tmp_path, monkeypatch):
    # Legacy (fresh sessions off) characterization: a risk, not desired behavior.
    configure(monkeypatch, True, fresh=False)
    monkeypatch.setattr(shared_state, 'SHARED', False)
    monkeypatch.setattr(security, 'is_revoked', lambda _sid: False)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    reader = Store(str(tmp_path / 'b.json'))
    assert sessions.active(reader, row['id'], tenant_id='t1') is not None
    sessions.revoke(writer, row, 'logout')
    assert sessions.active(reader, row['id'], tenant_id='t1') is not None
    reader.reload('sessions', 't1')
    assert sessions.active(reader, row['id'], tenant_id='t1') is None

def test_legacy_stale_touch_can_overwrite_a_revoked_session(tmp_path, monkeypatch):
    # Legacy (fresh sessions off) characterization: a risk, not desired behavior.
    configure(monkeypatch, True, fresh=False)
    monkeypatch.setattr(shared_state, 'SHARED', False)
    monkeypatch.setattr(security, 'is_revoked', lambda _sid: False)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    writer.update('sessions', 't1', row['id'], {'last_seen': 0})
    reader = Store(str(tmp_path / 'b.json'))
    old = sessions.active(reader, row['id'], tenant_id='t1')
    sessions.revoke(writer, row, 'logout')
    assert SharedBackend.rows[('sessions', 't1')][row['id']]['revoked_at']
    sessions.touch(reader, old)
    assert not SharedBackend.rows[('sessions', 't1')][row['id']]['revoked_at']


def test_legacy_new_refresh_token_is_not_found_by_another_stale_instance(tmp_path, monkeypatch):
    configure(monkeypatch, True, fresh=False)
    writer = Store(str(tmp_path / 'a.json'))
    reader = Store(str(tmp_path / 'b.json'))
    row, token = sessions.open_session(writer, 't1', 'u1')
    assert sessions.by_refresh(reader, token) is None
    reader.reload('sessions', 't1')
    assert sessions.by_refresh(reader, token)['id'] == row['id']

def test_legacy_two_stale_refresh_rotations_both_issue_new_tokens(tmp_path, monkeypatch):
    # Legacy characterization; sequentially models a stale read, not real DB concurrency.
    configure(monkeypatch, True, fresh=False)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    reader = Store(str(tmp_path / 'b.json'))
    reader.reload('sessions', 't1')
    stale = next(r for r in reader.db['sessions'] if r['id'] == row['id'])
    first, token_a = sessions.rotate(writer, row)
    second, token_b = sessions.rotate(reader, stale)
    assert token_a != token_b
    assert first['refresh_hash'] != second['refresh_hash']
    assert SharedBackend.rows[('sessions', 't1')][row['id']]['refresh_hash'] == second['refresh_hash']


def _no_shared_revocation(monkeypatch):
    monkeypatch.setattr(shared_state, 'SHARED', False)
    monkeypatch.setattr(security, 'is_revoked', lambda _sid: False)


def test_fresh_sessions_refuse_session_revoked_on_another_instance(tmp_path, monkeypatch):
    # Snapshot reads with the explicit flag: the path used if per-request reads were off.
    configure(monkeypatch, False, fresh=True)
    _no_shared_revocation(monkeypatch)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    reader = Store(str(tmp_path / 'b.json'))
    assert any(r['id'] == row['id'] for r in reader.db['sessions'])
    assert sessions.active(reader, row['id'], tenant_id='t1') is not None
    sessions.revoke(writer, row, 'logout')
    assert sessions.active(reader, row['id'], tenant_id='t1') is None
    assert sessions.active(reader, row['id']) is None


def test_fresh_touch_cannot_resurrect_revoked_session(tmp_path, monkeypatch):
    configure(monkeypatch, True)
    _no_shared_revocation(monkeypatch)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    writer.update('sessions', 't1', row['id'], {'last_seen': 0})
    reader = Store(str(tmp_path / 'b.json'))
    old = sessions.active(reader, row['id'], tenant_id='t1')
    assert old is not None
    sessions.revoke(writer, row, 'logout')
    sessions.touch(reader, old)
    stored = SharedBackend.rows[('sessions', 't1')][row['id']]
    assert stored['revoked_at'] and stored['revoked_reason'] == 'logout'


def test_fresh_rotation_is_compare_and_set(tmp_path, monkeypatch):
    import pytest
    configure(monkeypatch, True)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    reader = Store(str(tmp_path / 'b.json'))
    reader.reload('sessions', 't1')
    stale = next(r for r in reader.db['sessions'] if r['id'] == row['id'])
    first, _token_a = sessions.rotate(writer, row)
    with pytest.raises(sessions.RefreshConflict):
        sessions.rotate(reader, stale)
    assert SharedBackend.rows[('sessions', 't1')][row['id']]['refresh_hash'] == first['refresh_hash']


def test_fresh_refresh_lookup_sees_other_instances(tmp_path, monkeypatch):
    configure(monkeypatch, True)
    writer = Store(str(tmp_path / 'a.json'))
    reader = Store(str(tmp_path / 'b.json'))
    row, token = sessions.open_session(writer, 't1', 'u1')
    assert sessions.by_refresh(reader, token)['id'] == row['id']
    sessions.rotate(writer, row)
    assert sessions.by_refresh(reader, token) is None
    assert sessions.reuse_of(reader, token)['id'] == row['id']
    assert sessions.by_refresh(reader, 'unknown-token') is None
    assert sessions.reuse_of(reader, 'unknown-token') is None


def test_fresh_revoke_all_reaches_sessions_created_elsewhere(tmp_path, monkeypatch):
    configure(monkeypatch, True)
    _no_shared_revocation(monkeypatch)
    reader = Store(str(tmp_path / 'b.json'))
    writer = Store(str(tmp_path / 'a.json'))
    first, _ = sessions.open_session(writer, 't1', 'u1')
    second, _ = sessions.open_session(writer, 't1', 'u1')
    other, _ = sessions.open_session(writer, 't1', 'u2')
    assert sessions.revoke_all(reader, 't1', 'u1', 'password_changed') == 2
    stored = SharedBackend.rows[('sessions', 't1')]
    assert stored[first['id']]['revoked_at'] and stored[second['id']]['revoked_at']
    assert not stored[other['id']]['revoked_at']


def test_fresh_session_storage_failure_fails_closed(tmp_path, monkeypatch):
    import pytest
    configure(monkeypatch, True)
    _no_shared_revocation(monkeypatch)
    writer = Store(str(tmp_path / 'a.json'))
    row, _token = sessions.open_session(writer, 't1', 'u1')
    reader = Store(str(tmp_path / 'b.json'))
    assert sessions.active(reader, row['id'], tenant_id='t1') is not None
    def unavailable(*_args, **_kwargs):
        raise dbmod.StorageUnavailable('synthetic outage')
    monkeypatch.setattr(SharedBackend, 'get_document', unavailable)
    with pytest.raises(dbmod.StorageUnavailable):
        sessions.active(reader, row['id'], tenant_id='t1')


def test_refresh_conflict_ends_session_as_reuse(monkeypatch):
    import types
    import pytest
    from app import api
    row = {'id': 'ses_x', 'tenant_id': 't1', 'user_id': 'u1', 'expires_at': 2 ** 40,
           'revoked_at': 0, 'device': {'label': 'Synthetic'}}
    ended, audits = [], []
    class FakeStore:
        def get(self, coll, tid, rid):
            return {'id': rid, 'active': True, 'email': 'synthetic@example.invalid',
                    'role': 'agent', 'name': 'Synthetic'}
        def audit(self, *args, **kwargs):
            audits.append(args[2])
    def conflict(*_args, **_kwargs):
        raise sessions.RefreshConflict('superseded')
    monkeypatch.setattr(sessions, 'by_refresh', lambda _store, _token: dict(row))
    monkeypatch.setattr(sessions, 'rotate', conflict)
    monkeypatch.setattr(sessions, 'revoke', lambda _store, r, reason, **_kw: ended.append(reason) or r)
    ctx = types.SimpleNamespace(store=FakeStore(), headers={'x-refresh-token': 'synthetic'},
                                query={}, client_ip='203.0.113.9', user_agent='pytest')
    with pytest.raises(api.ApiError) as raised:
        api.auth_refresh(ctx)
    assert raised.value.status == 401 and raised.value.code == 'REFRESH_REUSED'
    assert ended == ['refresh_reuse'] and audits == ['auth.refresh_reuse']


def test_stateless_usage_ledger_reads_the_database(tmp_path, monkeypatch):
    from app import meters
    configure(monkeypatch, True)
    store = Store(str(tmp_path / 'a.json'))
    first = meters.record(store, 't1', 'ai_runs', 1, source_id='run_synthetic_1')
    again = meters.record(Store(str(tmp_path / 'b.json')), 't1', 'ai_runs', 1, source_id='run_synthetic_1')
    assert not first.get('_duplicate') and again.get('_duplicate') is True
    assert [r['id'] for r in meters.ledger(store, 't1')] == [first['id']]
    assert meters.ledger(store, 't2') == []


def test_stateless_proof_teardown_removes_database_rows(tmp_path, monkeypatch):
    from app import phase4_platform as p4
    configure(monkeypatch, True)
    store = Store(str(tmp_path / 'a.json'))
    store.db['tenants'].append({'id': 'tproof', 'tenant_id': 'tproof', 'is_proof_tenant': True})
    contact = store.insert('contacts', 'tproof', {'name': 'Synthetic'}, prefix='cnt')
    kept = store.insert('contacts', 't1', {'name': 'Synthetic kept'}, prefix='cnt')
    result = p4.destroy_disposable_proof_tenant(store, 'tproof')
    assert result['removed_rows'] >= 1
    assert contact['id'] not in SharedBackend.rows.get(('contacts', 'tproof'), {})
    assert kept['id'] in SharedBackend.rows[('contacts', 't1')]


def test_admin_connection_failure_fails_closed_repeatedly(monkeypatch):
    import types
    import pytest
    backend = object.__new__(dbmod.PgBackend)
    backend.conn = object()
    backend._admin_conn = None
    backend._admin_attempted = False
    backend._admin_dsn = 'configured'
    monkeypatch.setattr(dbmod, 'normalise_dsn', lambda _v: '')
    def fail(*_args, **_kwargs):
        raise ConnectionError('synthetic connection failure')
    # Replace the driver module itself, so the test also runs where psycopg is not installed.
    monkeypatch.setattr(dbmod, 'psycopg', types.SimpleNamespace(connect=fail))
    for _ in range(2):
        with pytest.raises(dbmod.StorageUnavailable, match='Maintenance database connection unavailable'):
            _ = backend.admin
    assert backend._admin_attempted is True


def test_explicit_connection_without_admin_configuration_uses_runtime_connection():
    backend = object.__new__(dbmod.PgBackend)
    backend.conn = object()
    backend._admin_conn = None
    backend._admin_attempted = False
    backend._admin_dsn = ''
    assert backend.admin is backend.conn

def test_strict_existing_data_mode_refuses_empty_hydration_without_seeding(tmp_path, monkeypatch):
    import pytest
    configure(monkeypatch, False)
    monkeypatch.setenv('RE_REQUIRE_EXISTING_DATA', '1')
    monkeypatch.setenv('RE_DB_STRICT', '1')
    monkeypatch.setattr(SharedBackend, 'load_all', lambda self: {})
    seeded = []
    monkeypatch.setattr(Store, 'seed', lambda _store: seeded.append(True))
    monkeypatch.setattr(Store, '_flush_all', lambda _store: seeded.append(True))
    with pytest.raises(dbmod.StorageUnavailable):
        Store(str(tmp_path / 'a.json'))
    assert not seeded


def test_strict_existing_data_mode_refuses_empty_stateless_routing(tmp_path, monkeypatch):
    import pytest
    configure(monkeypatch, True)
    monkeypatch.setenv('RE_REQUIRE_EXISTING_DATA', '1')
    monkeypatch.setenv('RE_DB_STRICT', '1')
    monkeypatch.setattr(SharedBackend, 'load_collection_admin', lambda self, collection: [])
    seeded = []
    monkeypatch.setattr(Store, 'seed', lambda _store: seeded.append(True))
    with pytest.raises(dbmod.StorageUnavailable):
        Store(str(tmp_path / 'a.json'))
    assert not seeded

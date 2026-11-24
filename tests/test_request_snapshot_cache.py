"""Synthetic backend tests for request-lifetime and tenant-scoped caching."""
import threading
from app.store import Store


class FakeBackend:
    def __init__(self):
        self.reads = 0
        self.batches = 0

    def documents(self, collection, tenant):
        self.reads += 1
        return [{'id': tenant + '-row', 'tenant_id': tenant, 'collection': collection}]

    def documents_many(self, collections, tenant):
        self.batches += 1
        return {name: [{'id': tenant + '-row', 'tenant_id': tenant, 'collection': name}] for name in collections}


def fake_store():
    store = Store.__new__(Store)
    store.stateless_reads = True
    store.backend = FakeBackend()
    store._req_cache = threading.local()
    return store


def test_batch_prefetch_and_tenant_partition():
    s = fake_store()
    s.begin_request_cache()
    s.prefetch(('contacts', 'deals'), 'tenant-a')
    s.all('contacts', 'tenant-a')
    s.all('deals', 'tenant-a')
    assert s.backend.batches == 1
    assert s.backend.reads == 0
    assert s.all('contacts', 'tenant-b')[0]['tenant_id'] == 'tenant-b'
    assert s.backend.reads == 1
    s.end_request_cache()


def test_invalidation_and_end_request():
    s = fake_store()
    s.begin_request_cache()
    s.all('contacts', 'a')
    s.all('contacts', 'a')
    assert s.backend.reads == 1
    s._invalidate_req_cache('a', 'contacts')
    s.all('contacts', 'a')
    assert s.backend.reads == 2
    s.end_request_cache()
    s.begin_request_cache()
    s.all('contacts', 'a')
    assert s.backend.reads == 3
    s.end_request_cache()


def test_snapshot_is_thread_local():
    s = fake_store()
    s.begin_request_cache()
    s.all('contacts', 'a')
    observed = []
    def worker():
        s.begin_request_cache()
        observed.append(s.all('contacts', 'a')[0]['tenant_id'])
        s.end_request_cache()
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert observed == ['a']
    assert s.backend.reads == 2
    s.all('contacts', 'a')
    assert s.backend.reads == 2
    s.end_request_cache()

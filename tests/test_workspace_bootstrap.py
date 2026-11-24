"""Regression coverage for single-request shell hydration. Never log response payloads."""
from conftest import Session


def test_bootstrap_requires_session():
    status, _, _ = Session().call('GET', '/api/v1/bootstrap')
    assert status == 401


def test_bootstrap_owner_payload(owner):
    status, payload, _ = owner.call('GET', '/api/v1/bootstrap')
    assert status == 200
    assert payload['me']['user']['role'] == 'owner'
    assert payload['me']['user']['permissions']['manage_users'] is True
    assert bool(payload['csrf'])
    for name in ('overview', 'tasks', 'agents', 'proposals', 'sla', 'pipeline_summary', 'units_summary'):
        assert name in payload
    assert payload['pending_approvals'] >= 0
    assert payload['sla']['overdue'] >= 0


def test_bootstrap_viewer_permissions(viewer):
    status, payload, _ = viewer.call('GET', '/api/v1/bootstrap')
    assert status == 200
    permissions = payload['me']['user']['permissions']
    assert permissions['manage_users'] is False
    assert permissions['kill_switch'] is False
    assert permissions['pii:read'] is False


def test_bootstrap_tenant_isolation(owner, other_tenant):
    a_status, a, _ = owner.call('GET', '/api/v1/bootstrap')
    b_status, b, _ = other_tenant.call('GET', '/api/v1/bootstrap')
    assert a_status == b_status == 200
    assert a['me']['tenant']['id'] != b['me']['tenant']['id']
    assert {v['id'] for v in a['agents']}.isdisjoint({v['id'] for v in b['agents']})
    assert {v['id'] for v in a['tasks']}.isdisjoint({v['id'] for v in b['tasks']})

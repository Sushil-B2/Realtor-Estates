"""
Read paths for the two function-owned tables (schedule sweep, payment webhooks).

Same split as `tests/test_property_media.py`: the HTTP half runs against the file
store the suite pins, the SQL half runs only when pointed at a real database.

What matters here is the authority boundary, not the SQL:

  * a `viewer` — the role most tenants hand out — cannot read either table;
  * on the file store both routes say 503 rather than returning an empty list
    that would look like "no payments yet";
  * the webhook query is scoped by `tenant_id`, so a workspace never sees a
    payment that belongs to another one (proved against Postgres below).
"""
from __future__ import annotations

import os
import sys

import pytest
from conftest import Session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# HTTP path
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/v1/compliance/runs", "/api/v1/webhooks/events"])
def test_needs_auth(path):
    status, body, _ = Session().call("GET", path, csrf=False)
    assert status == 401, body


@pytest.mark.parametrize("path", ["/api/v1/compliance/runs", "/api/v1/webhooks/events"])
def test_viewer_is_forbidden(viewer, path):
    status, body, _ = viewer.call("GET", path)
    assert status == 403, body
    assert body["error"]["code"] == "FORBIDDEN_SCOPE"


@pytest.mark.parametrize("path", ["/api/v1/compliance/runs", "/api/v1/webhooks/events"])
def test_file_store_says_unavailable(owner, path):
    status, body, _ = owner.call("GET", path)
    assert status == 503, body
    assert body["error"]["code"] == "PIPELINE_UNAVAILABLE"


def test_webhook_events_are_not_publicly_listable():
    # A webhook endpoint is public to the provider; its *records* are not.
    status, _, _ = Session().call("GET", "/api/v1/webhooks/events", csrf=False)
    assert status in (401, 403)


# ---------------------------------------------------------------------------
# Postgres path
# ---------------------------------------------------------------------------
def _opted_in() -> bool:
    return os.environ.get("RE_TEST_DB_FROM_ENV", "") in ("1", "true", "yes")


def test_webhook_events_query_filters_by_tenant():
    if not _opted_in():
        pytest.skip("set RE_TEST_DB_FROM_ENV=1 to run the Postgres webhook test")
    psycopg = pytest.importorskip("psycopg", reason="pip install 'psycopg[binary]'")
    from app import db as dbmod

    url = dbmod.admin_dsn()
    if not url:
        pytest.skip("no admin DSN configured")

    backend = dbmod.PgBackend(url)
    mine, theirs = "tnt_evt_a", "tnt_evt_b"
    try:
        with psycopg.connect(dbmod.normalise_dsn(url)) as conn:
            with conn.cursor() as cur:
                cur.execute("delete from re_webhook_events where event_id like 'evt_test_%'")
                for tenant, event in ((mine, "evt_test_a"), (theirs, "evt_test_b")):
                    cur.execute(
                        """insert into re_webhook_events
                             (id, provider, event_id, event_type, status, received_at,
                              tenant_id, payment_id, amount, currency, key_fingerprint, payload)
                           values (%s,'razorpay',%s,'payment.captured','accepted',0,%s,'pay_x',1250000,'INR','fp',%s)""",
                        (f"row_{event}", event, tenant, "{}"),
                    )
                # an unattributable event must reach nobody
                cur.execute(
                    """insert into re_webhook_events
                         (id, provider, event_id, event_type, status, received_at, tenant_id,
                          key_fingerprint, payload)
                       values ('row_evt_test_orphan','razorpay','evt_test_orphan','payment.captured',
                               'accepted',0,NULL,'fp','{}')"""
                )
            conn.commit()

        rows = backend.webhook_events(mine)
        assert [r["event_id"] for r in rows] == ["evt_test_a"]
        assert backend.webhook_events("tnt_evt_nobody") == []
    finally:
        try:
            with psycopg.connect(dbmod.normalise_dsn(url)) as conn:
                with conn.cursor() as cur:
                    cur.execute("delete from re_webhook_events where event_id like 'evt_test_%'")
                conn.commit()
        finally:
            backend.close()


def test_compliance_runs_are_readable_and_ordered():
    if not _opted_in():
        pytest.skip("set RE_TEST_DB_FROM_ENV=1 to run the Postgres compliance test")
    pytest.importorskip("psycopg", reason="pip install 'psycopg[binary]'")
    from app import db as dbmod

    url = dbmod.admin_dsn()
    if not url:
        pytest.skip("no admin DSN configured")

    backend = dbmod.PgBackend(url)
    try:
        runs = backend.compliance_runs(limit=5)
        assert isinstance(runs, list)
        if len(runs) > 1:
            assert runs[0]["started_at"] >= runs[1]["started_at"]
        for run in runs:
            assert run["status"] in {"ok", "attention", "degraded", "failed"}
            assert isinstance(run["audit"], dict)
    finally:
        backend.close()

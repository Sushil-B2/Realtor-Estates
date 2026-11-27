"""Two app instances over one disposable PostgreSQL database.

Run by tests/test_postgres_two_instances.py in a clean subprocess. Instance A and
instance B are separate Store objects with separate connections, as two
serverless instances would be. The runtime connection uses the restricted
`re_app` role under row-level security. Output is a JSON summary of statuses and
booleans only: no tokens, connection strings or personal data.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"          # public seeded demo credential, also used by tests/test_sessions.py


def main() -> int:
    import psycopg

    from app import api
    from app import db as dbmod
    from app.store import Store

    out: dict = {}
    tmp = tempfile.mkdtemp(prefix="re-two-instances-")

    def call(store, method, path, body=None, token=None, headers=None, query=None):
        h = {"user-agent": "pytest-two-instances"}
        if token:
            h["authorization"] = "Bearer " + token
        h.update(headers or {})
        payload = body or {}
        status, data, _extra = api.handle(store, method, path, query or {}, payload, h,
                                          "203.0.113.10", None, json.dumps(payload).encode())
        return status, data

    def code(data):
        return ((data or {}).get("error") or {}).get("code", "")

    a = Store(os.path.join(tmp, "a.json"))
    os.environ["RE_REQUIRE_EXISTING_DATA"] = "1"
    b = Store(os.path.join(tmp, "b.json"))
    out["backends"] = [a.backend_name, b.backend_name]
    out["stateless_reads"] = bool(a.stateless_reads and b.stateless_reads)

    status, login = call(a, "POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD},
                         query={"refresh": ["body"]})
    out["login_a"] = status
    token, refresh = login.get("token", ""), login.get("refresh_token", "")

    status, created = call(a, "POST", "/api/v1/contacts",
                           {"name": "Synthetic QA Contact", "phone": "+91 90000 00001",
                            "stage": "contacted", "source": "qa-local", "consent": False},
                           token=token)
    out["create_contact_a"] = status
    contact = created.get("contact") if isinstance(created.get("contact"), dict) else created
    cid = (contact or {}).get("id", "")
    out["contact_id_returned"] = bool(cid)

    status, listing = call(b, "GET", "/api/v1/contacts", token=token)
    out["list_contacts_b"] = status
    out["new_contact_visible_on_b"] = bool(cid) and cid in json.dumps(listing)
    status, _detail = call(b, "GET", f"/api/v1/contacts/{cid}", token=token)
    out["contact_detail_b"] = status

    status, refreshed = call(b, "POST", "/api/v1/auth/refresh", headers={"x-refresh-token": refresh},
                             query={"refresh": ["body"]})
    out["refresh_on_b"] = status
    new_token = refreshed.get("token", "")
    out["refresh_rotated"] = bool(refreshed.get("refresh_token")) and refreshed.get("refresh_token") != refresh

    status, replay = call(a, "POST", "/api/v1/auth/refresh", headers={"x-refresh-token": refresh},
                          query={"refresh": ["body"]})
    out["replay_old_refresh_on_a"] = [status, code(replay)]
    status, after_replay = call(b, "GET", "/api/v1/contacts", token=new_token)
    out["rotated_access_after_replay_on_b"] = [status, code(after_replay)]

    status, second = call(b, "POST", "/api/v1/auth/login", {"email": OWNER, "password": PASSWORD})
    out["login_b"] = status
    token2 = second.get("token", "")
    status, _ = call(b, "GET", "/api/v1/contacts", token=token2)
    out["second_session_works_on_b"] = status
    status, _ = call(a, "POST", "/api/v1/auth/logout", token=token2)
    out["logout_on_a"] = status
    # Both Store objects share this process. Separate serverless instances do not
    # share the in-memory access-token denylist, so clear it to model instance B.
    from app import security as sec
    sec.REVOKED.clear()
    status, after_logout = call(b, "GET", "/api/v1/contacts", token=token2)
    out["after_logout_on_b"] = [status, code(after_logout)]

    tid = next((s.get("tenant_id") for s in a.db["sessions"] if s.get("tenant_id")), "")
    runtime = dbmod.normalise_dsn(os.environ["DATABASE_URL"])
    admin = dbmod.normalise_dsn(os.environ["RE_ADMIN_DATABASE_URL"])
    with psycopg.connect(runtime, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute("select current_user, (select rolbypassrls from pg_roles where rolname = current_user)")
        role, bypass = cur.fetchone()
        out["runtime_role"] = [role, bool(bypass)]
        cur.execute("select count(*) from re_documents")
        out["runtime_rows_without_tenant"] = cur.fetchone()[0]
        cur.execute("select set_config('app.tenant_id', %s, false)", (tid,))
        cur.execute("select count(*) from re_documents")
        out["runtime_rows_with_tenant_positive"] = cur.fetchone()[0] > 0
        cur.execute("select count(*) from re_documents where tenant_id <> %s", (tid,))
        out["runtime_other_tenant_rows"] = cur.fetchone()[0]
    with psycopg.connect(admin, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute("select count(*), count(distinct tenant_id) from re_documents")
        total, tenants = cur.fetchone()
        out["admin_sees_multiple_tenants"] = tenants > 1

    # A maintenance connection that cannot see existing tenants must refuse to boot
    # and must not seed. Simulate it by pointing maintenance reads at re_app.
    os.environ["RE_ADMIN_DATABASE_URL"] = os.environ["DATABASE_URL"]
    try:
        Store(os.path.join(tmp, "c.json"))
        out["blind_maintenance_boot"] = "booted"
    except dbmod.StorageUnavailable:
        out["blind_maintenance_boot"] = "refused"
    with psycopg.connect(admin, prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from re_documents")
        out["rows_unchanged_after_refused_boot"] = cur.fetchone()[0] == total

    print("RESULT_JSON " + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

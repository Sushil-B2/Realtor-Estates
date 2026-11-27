"""Two app instances: shared records that every request routes through.

Run by tests/test_postgres_routing_consistency.py in a clean subprocess, like
tests/pg_two_instances_scenario.py. Instance A changes workspaces, users,
website (ingest) keys and funnels after instance B has started; B must act on
the current database state, must not undo A's changes, and must recover when
its maintenance connection is dropped (Neon suspends idle computes and closes
their connections). Output is statuses and booleans only: no tokens,
connection strings or personal data. All people and numbers are synthetic.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OWNER = "owner@realtorestates.app"
PASSWORD = "Demo@12345!"          # public seeded demo credential, also used by tests/test_sessions.py
USER_PASSWORD = "Qa-Routing-Check-7"


def main() -> int:
    import psycopg

    from app import api
    from app import db as dbmod
    from app import security as sec
    from app.store import Store

    out: dict = {}
    tmp = tempfile.mkdtemp(prefix="re-routing-")
    admin_dsn = dbmod.normalise_dsn(os.environ["RE_ADMIN_DATABASE_URL"])

    def call(store, method, path, body=None, token=None, headers=None, query=None):
        h = {"user-agent": "pytest-routing-consistency"}
        if token:
            h["authorization"] = "Bearer " + token
        h.update(headers or {})
        payload = body or {}
        status, data, _extra = api.handle(store, method, path, query or {}, payload, h,
                                          "203.0.113.20", None, json.dumps(payload).encode())
        return status, (data if isinstance(data, dict) else {})

    def login(store, email, password, refresh=False):
        status, data = call(store, "POST", "/api/v1/auth/login", {"email": email, "password": password},
                            query={"refresh": ["body"]} if refresh else None)
        return status, data.get("token", ""), data.get("refresh_token", "")

    def step_up(store, token, action, target):
        status, data = call(store, "POST", "/api/v1/auth/step-up",
                            {"action": action, "target": target, "password": PASSWORD}, token=token)
        return {"x-step-up": data.get("ticket", "")} if status == 200 else {}

    def stored(collection, rid):
        with psycopg.connect(admin_dsn, prepare_threshold=None) as conn, conn.cursor() as cur:
            cur.execute("select doc from re_documents where collection = %s and id = %s", (collection, rid))
            row = cur.fetchone()
            return dict(row[0]) if row else {}

    def new_key(store, token, label):
        status, data = call(store, "POST", "/api/v1/tenant/ingest-keys", {"label": label}, token=token)
        return status, (data.get("key") or {}).get("id", ""), data.get("token", "")

    def ingest(store, raw_key, phone):
        status, _ = call(store, "POST", "/api/v1/ingest/website",
                         {"name": "Synthetic Ingest Lead", "phone": phone}, headers={"x-ingest-key": raw_key})
        return status

    def drop_admin_connection(store):
        try:
            conn = store.backend.admin
            with conn.cursor() as cur:
                cur.execute("select pg_backend_pid()")
                pid = cur.fetchone()[0]
            conn.commit()
        except Exception:
            return  # already dead and never replaced: nothing left to drop
        with psycopg.connect(admin_dsn, autocommit=True, prepare_threshold=None) as killer:
            killer.execute("select pg_terminate_backend(%s)", (pid,))

    # --- A starts and prepares records that B will load at start-up -------------------
    a = Store(os.path.join(tmp, "a.json"))
    status, token_a, _ = login(a, OWNER, PASSWORD)
    out["login_a"] = status
    status, f1 = call(a, "POST", "/api/v1/funnels", {"slug": "qa-routing-one", "name": "QA Routing One"},
                      token=token_a)
    fid1 = (f1.get("funnel") or {}).get("id", "")
    out["create_funnel_before_b"] = status
    status, kid1, key1 = new_key(a, token_a, "qa-revoked-later")
    _, kid3, key3 = new_key(a, token_a, "qa-kept")
    out["create_keys_before_b"] = status
    status, u1 = call(a, "POST", "/api/v1/tenant/users",
                      {"email": "qa-routing-one@example.test", "role": "agent", "password": USER_PASSWORD},
                      token=token_a)
    uid1 = (u1.get("user") or {}).get("id", "")
    out["invite_user_before_b"] = status

    # --- B starts: its start-up copy now holds F1, K1, K3 and U1 ---------------------
    os.environ["RE_REQUIRE_EXISTING_DATA"] = "1"
    b = Store(os.path.join(tmp, "b.json"))
    out["stateless_reads"] = bool(a.stateless_reads and b.stateless_reads)
    status, token_b, refresh_b = login(b, OWNER, PASSWORD, refresh=True)
    out["login_b"] = status

    # --- A changes things after B started --------------------------------------------
    status, f2 = call(a, "POST", "/api/v1/funnels", {"slug": "qa-routing-two", "name": "QA Routing Two"},
                      token=token_a)
    fid2 = (f2.get("funnel") or {}).get("id", "")
    out["create_funnel_after_b"] = status
    out["publish_funnel_on_a"] = call(a, "PATCH", f"/api/v1/funnels/{fid1}", {"published": True}, token=token_a)[0]
    status, _kid2, key2 = new_key(a, token_a, "qa-new-after-b")
    out["create_key_after_b"] = status
    out["revoke_key_on_a"] = call(a, "DELETE", f"/api/v1/tenant/ingest-keys/{kid1}", token=token_a)[0]
    status, _u2 = call(a, "POST", "/api/v1/tenant/users",
                       {"email": "qa-routing-two@example.test", "role": "agent", "password": USER_PASSWORD},
                       token=token_a)
    out["invite_user_after_b"] = status
    out["deactivate_user_on_a"] = call(a, "PATCH", f"/api/v1/tenant/users/{uid1}", {"active": False},
                                       token=token_a, headers=step_up(a, token_a, "user_delete", uid1))[0]
    out["rename_workspace_on_a"] = call(a, "PATCH", "/api/v1/tenant", {"name": "QA Routing Workspace"},
                                        token=token_a)[0]
    out["feature_off_on_a"] = call(a, "PATCH", "/api/v1/tenant/features", {"social_scheduler": False},
                                   token=token_a)[0]

    # --- B acts ------------------------------------------------------------------------
    status, listing = call(b, "GET", "/api/v1/funnels", token=token_b)
    rows = {f.get("id"): f for f in listing.get("funnels", []) if isinstance(f, dict)}
    out["new_funnel_listed_on_b"] = fid2 in rows
    out["publish_seen_on_b"] = bool(rows.get(fid1, {}).get("published"))
    out["rename_funnel_on_b"] = call(b, "PATCH", f"/api/v1/funnels/{fid1}", {"name": "Renamed On B"},
                                     token=token_b)[0]
    f1_now = stored("funnels", fid1)
    out["funnel_publish_kept_after_b_edit"] = bool(f1_now.get("published"))
    out["funnel_rename_saved"] = f1_now.get("name") == "Renamed On B"

    out["new_key_works_on_b"] = ingest(b, key2, "+91 90000 00011")
    out["revoked_key_on_b"] = ingest(b, key1, "+91 90000 00012")
    out["revoked_key_still_revoked"] = stored("ingest_keys", kid1).get("active") is False

    out["new_user_login_on_b"] = login(b, "qa-routing-two@example.test", USER_PASSWORD)[0]
    out["deactivated_user_login_on_b"] = login(b, "qa-routing-one@example.test", USER_PASSWORD)[0]
    out["deactivated_user_still_inactive"] = stored("users", uid1).get("active") is False

    status, tenant_b = call(b, "GET", "/api/v1/tenant", token=token_b)
    out["workspace_name_seen_on_b"] = "QA Routing Workspace" in json.dumps(tenant_b)
    tid = (stored("users", uid1) or {}).get("tenant_id", "")
    t_now = stored("tenants", tid)
    out["workspace_name_saved"] = t_now.get("name") == "QA Routing Workspace"
    out["feature_change_saved"] = (t_now.get("features") or {}).get("social_scheduler") is False

    # --- Emergency stop on A must stop B's website intake too ------------------------
    out["kill_switch_on_a"] = call(a, "POST", "/api/v1/tenant/kill-switch", {"engage": True}, token=token_a,
                                   headers=step_up(a, token_a, "kill_switch", "engage"))[0]
    out["kill_switch_saved"] = bool(stored("tenants", tid).get("kill_switch"))
    out["ingest_on_b_after_kill_switch"] = ingest(b, key3, "+91 90000 00013")
    out["kill_switch_release_on_a"] = call(a, "POST", "/api/v1/tenant/kill-switch", {"engage": False},
                                           token=token_a, headers=step_up(a, token_a, "kill_switch", "release"))[0]

    # --- Neon closes idle connections: B must reconnect its maintenance connection ---
    publish = call(a, "PATCH", f"/api/v1/funnels/{fid2}", {"published": True}, token=token_a)[0]
    out["publish_second_funnel"] = publish
    drop_admin_connection(b)
    status, _ = call(b, "POST", "/api/v1/public/funnel/qa-routing-two/submit",
                     {"name": "Synthetic Funnel Lead", "phone": "+91 90000 00014", "consent": True,
                      "_ftok": sec.mint_funnel_token("qa-routing-two")})
    out["public_form_on_b_after_connection_drop"] = status
    drop_admin_connection(b)
    status, _ = call(b, "POST", "/api/v1/auth/refresh", headers={"x-refresh-token": refresh_b},
                     query={"refresh": ["body"]})
    out["refresh_on_b_after_connection_drop"] = status
    out["key_after_reconnect"] = ingest(b, key3, "+91 90000 00015")

    print("RESULT_JSON " + json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

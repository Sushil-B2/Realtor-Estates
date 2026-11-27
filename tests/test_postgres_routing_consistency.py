"""Workspaces, users, website keys and funnels stay current across instances.

Skipped unless RE_TEST_DATABASE_URL points at a privileged connection to a
*disposable* server and RE_TEST_DB_DISPOSABLE=1. The test creates and drops its
own database there, seeds demo data and writes synthetic rows. Never point it at
a shared or production database. It does not prove Neon pooling, Vercel routing
or the deployed configuration.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import urllib.parse

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("RE_TEST_DATABASE_URL", "").strip()
DISPOSABLE = os.environ.get("RE_TEST_DB_DISPOSABLE", "").strip().lower() in ("1", "true", "yes")
pytestmark = pytest.mark.skipif(
    not (URL and DISPOSABLE),
    reason="needs RE_TEST_DATABASE_URL for a disposable server and RE_TEST_DB_DISPOSABLE=1")


def _database(url: str, name: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parts._replace(path="/" + name))


def _as_role(url: str, role: str) -> str:
    return url + ("&" if "?" in url else "?") + "options=-c%20role%3D" + role


@pytest.fixture(scope="module")
def result():
    sys.path.insert(0, ROOT)
    from app import db as dbmod

    name = "re_routing_" + secrets.token_hex(4)
    server = psycopg.connect(dbmod.normalise_dsn(URL), autocommit=True, prepare_threshold=None)
    try:
        server.execute(f'create database "{name}"')
        privileged = _database(URL, name)
        with psycopg.connect(dbmod.normalise_dsn(privileged), prepare_threshold=None) as conn:
            dbmod.migrate(conn, include_rls=True, verbose=False)
        env = {
            "PATH": os.environ.get("PATH", os.defpath), "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": os.environ.get("HOME", ROOT),
            "RE_ENV": "development", "RE_MASTER_KEY": secrets.token_hex(32),
            "RE_DB": "postgres", "RE_DB_STRICT": "1", "RE_SKIP_BOOT_MIGRATE": "1",
            "RE_STATELESS_READS": "1", "RE_FRESH_SESSIONS": "1", "RE_AUTOMATION_TICK": "0",
            "RE_ENV_FILE": os.path.join(ROOT, ".env.test-absent"),
            "DATABASE_URL": _as_role(privileged, "re_app"),
            "RE_ADMIN_DATABASE_URL": privileged,
        }
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "pg_routing_consistency_scenario.py")],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON ")), "")
        assert proc.returncode == 0 and line, "routing scenario did not complete"
        yield json.loads(line[len("RESULT_JSON "):])
    finally:
        try:
            server.execute(f'drop database if exists "{name}" with (force)')
        finally:
            server.close()


def test_setup_steps_succeeded(result):
    assert result["stateless_reads"] is True
    assert result["login_a"] == 200 and result["login_b"] == 200
    for key in ("create_funnel_before_b", "create_keys_before_b", "invite_user_before_b",
                "create_funnel_after_b", "publish_funnel_on_a", "create_key_after_b", "revoke_key_on_a",
                "invite_user_after_b", "deactivate_user_on_a", "rename_workspace_on_a", "feature_off_on_a",
                "kill_switch_on_a", "kill_switch_release_on_a", "publish_second_funnel"):
        assert result[key] in (200, 201), key


def test_funnels_created_or_changed_elsewhere_are_current(result):
    assert result["new_funnel_listed_on_b"] is True
    assert result["publish_seen_on_b"] is True
    assert result["rename_funnel_on_b"] == 200
    assert result["funnel_rename_saved"] is True


def test_an_edit_on_a_stale_instance_does_not_undo_other_changes(result):
    assert result["funnel_publish_kept_after_b_edit"] is True
    assert result["revoked_key_still_revoked"] is True
    assert result["deactivated_user_still_inactive"] is True


def test_website_keys_created_or_revoked_elsewhere_are_honoured(result):
    assert result["new_key_works_on_b"] in (200, 201)
    assert result["revoked_key_on_b"] == 401


def test_users_created_or_deactivated_elsewhere_are_honoured(result):
    assert result["new_user_login_on_b"] == 200
    assert result["deactivated_user_login_on_b"] == 403


def test_workspace_settings_are_saved_and_seen_everywhere(result):
    assert result["workspace_name_saved"] is True
    assert result["workspace_name_seen_on_b"] is True
    assert result["feature_change_saved"] is True


def test_kill_switch_is_saved_and_stops_intake_on_every_instance(result):
    assert result["kill_switch_saved"] is True
    assert result["ingest_on_b_after_kill_switch"] == 401


def test_maintenance_connection_recovers_after_the_database_drops_it(result):
    assert result["public_form_on_b_after_connection_drop"] in (200, 201)
    assert result["refresh_on_b_after_connection_drop"] == 200
    assert result["key_after_reconnect"] in (200, 201)

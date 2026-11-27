"""Two instances, one real PostgreSQL database, restricted runtime role under RLS.

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

    name = "re_two_instances_" + secrets.token_hex(4)
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
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "pg_two_instances_scenario.py")],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON ")), "")
        assert proc.returncode == 0 and line, "two-instance scenario did not complete"
        yield json.loads(line[len("RESULT_JSON "):])
    finally:
        try:
            server.execute(f'drop database if exists "{name}" with (force)')
        finally:
            server.close()


def test_both_instances_use_postgres_with_fresh_reads(result):
    assert result["backends"] == ["postgres", "postgres"]
    assert result["stateless_reads"] is True


def test_runtime_role_is_restricted_and_isolated(result):
    assert result["runtime_role"] == ["re_app", False]
    assert result["runtime_rows_without_tenant"] == 0
    assert result["runtime_rows_with_tenant_positive"] is True
    assert result["runtime_other_tenant_rows"] == 0
    assert result["admin_sees_multiple_tenants"] is True


def test_contact_written_on_one_instance_is_read_on_the_other(result):
    assert result["login_a"] == 200
    assert result["create_contact_a"] in (200, 201)
    assert result["contact_id_returned"] is True
    assert result["list_contacts_b"] == 200
    assert result["new_contact_visible_on_b"] is True
    assert result["contact_detail_b"] == 200


def test_refresh_rotation_and_replay_detection_across_instances(result):
    assert result["refresh_on_b"] == 200 and result["refresh_rotated"] is True
    assert result["replay_old_refresh_on_a"] == [401, "REFRESH_REUSED"]
    assert result["rotated_access_after_replay_on_b"] == [401, "SESSION_ENDED"]


def test_logout_on_one_instance_ends_access_on_the_other(result):
    assert result["login_b"] == 200 and result["second_session_works_on_b"] == 200
    assert result["logout_on_a"] == 200
    assert result["after_logout_on_b"] == [401, "SESSION_ENDED"]


def test_blind_maintenance_connection_refuses_to_boot_without_writing(result):
    assert result["blind_maintenance_boot"] == "refused"
    assert result["rows_unchanged_after_refused_boot"] is True

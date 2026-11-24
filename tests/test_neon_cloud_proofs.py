"""
Tests for v1.25.0 Neon Cloud Functions deployment, PgBouncer capability probe,
and RTO proof (`docs/36-NEON-CLOUD-PROOFS.md`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import neon

ROOT = Path(__file__).resolve().parents[1]


def test_neon_ts_declares_sharp_external_package_and_function_envs() -> None:
    src = (ROOT / "neon.ts").read_text(encoding="utf-8")
    assert 'externalPackages: ["sharp"]' in src
    assert "PAYMENTS_WEBHOOK_SECRET" in src
    assert "NEON_STORAGE_ENDPOINT" in src


def test_deploy_script_protects_local_dotenv_with_no_env_pull() -> None:
    sh = (ROOT / "scripts" / "deploy_functions.sh").read_text(encoding="utf-8")
    assert "--no-env-pull" in sh
    assert 'config plan --env "$ENV_FILE"' in sh


def test_proof_deployed_functions_refuses_to_pass_without_urls() -> None:
    env = {"PHOTOS_URL": "", "WEBHOOK_URL": "", "SWEEP_URL": "", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    res = subprocess.run(
        [sys.executable, "scripts/proof_deployed_functions.py"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert res.returncode == 2
    assert "NOT PROVEN" in res.stdout


def test_pgbouncer_isolation_probes_hold_simultaneous_transactions() -> None:
    vp = (ROOT / "scripts" / "verify_postgres.py").read_text(encoding="utf-8")
    tp = (ROOT / "tests" / "test_postgres.py").read_text(encoding="utf-8")
    assert "with a.transaction(), b.transaction():" in vp
    assert "with a.transaction(), b.transaction():" in tp


def test_app_neon_functions_config_and_ping_all_three_slugs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, _n: int = 200) -> bytes:
            return b'{"ok":true}'

    monkeypatch.setenv("NEON_PHOTOS_FUNCTION_URL", "https://example.neon.tech/photos")
    monkeypatch.setenv("NEON_SWEEP_FUNCTION_URL", "https://example.neon.tech/sweep")
    monkeypatch.setenv("NEON_WEBHOOK_FUNCTION_URL", "https://example.neon.tech/webhook")
    monkeypatch.setattr(neon.urllib.request, "urlopen", lambda req, timeout=8: (calls.append(req.full_url), _FakeResp())[1])

    fc = neon.functions_config()
    assert fc["configured"] is True
    assert set(fc["endpoints"]) == {"propertyphotos", "compliancesweep", "paymentswebhook"}

    res = neon.ping_functions()
    assert res["ok"] is True
    assert len(calls) == 3
    assert all(v["ok"] is True for v in res["functions"].values())

"""
The operator key-intake path: `app/keysetup.py` and its two routes.

Two halves, again on purpose:

  * the **writer** is tested directly against a temp file, because that is where
    the dangerous behaviour lives (newline injection, file mode, atomicity,
    idempotence). A test that went through HTTP would need to write to the real
    `.env.local`, which is exactly what a suite must never do.
  * the **routes** are tested over HTTP for authority only: anonymous → 401,
    viewer → 403. Both refuse before touching any file, so the assertions are
    safe even though the running server owns a real secrets file.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import Session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import keysetup  # noqa: E402


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------
@pytest.fixture()
def env_file():
    with tempfile.TemporaryDirectory(prefix="re-keys-") as tmp:
        path = Path(tmp) / ".env.local"
        path.write_text(
            "# a comment\n"
            "NEON_STORAGE_BUCKET=projectassets\n"
            "# NEON_API_KEY=\n"
            "PAYMENTS_WEBHOOK_SECRET=deadbeef\n",
            encoding="utf-8",
        )
        yield path


def test_writes_value_in_place_of_the_placeholder(env_file):
    report = keysetup.set_value("NEON_API_KEY", "nak_example000011112222333", env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "NEON_API_KEY=nak_example000011112222333" in text
    assert "# NEON_API_KEY=" not in text          # the placeholder is consumed, not duplicated
    assert "PAYMENTS_WEBHOOK_SECRET=deadbeef" in text   # neighbours untouched
    assert report["replaced"] is True
    assert keysetup.read_value("NEON_API_KEY", env_file) == "nak_example000011112222333"


def test_file_mode_is_0600(env_file):
    os.chmod(env_file, 0o644)
    keysetup.set_value("NEON_API_KEY", "nak_example000011112222333", env_file)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_idempotent_and_no_growth(env_file):
    keysetup.set_value("NEON_API_KEY", "nak_aaaaaaaaaaaa1111", env_file)
    before = env_file.read_text(encoding="utf-8")
    keysetup.set_value("NEON_API_KEY", "nak_bbbbbbbbbbbb2222", env_file)
    after = env_file.read_text(encoding="utf-8")
    assert after.count("NEON_API_KEY=") == 1
    assert "nak_bbbbbbbbbbbb2222" in after
    assert len(after.splitlines()) == len(before.splitlines())


def test_creates_the_file_when_absent():
    with tempfile.TemporaryDirectory(prefix="re-keys-") as tmp:
        path = Path(tmp) / ".env.local"
        keysetup.set_value("NEON_API_KEY", "nak_fresh0000011112222", path)
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text(encoding="utf-8").strip() == "NEON_API_KEY=nak_fresh0000011112222"


@pytest.mark.parametrize(
    "value",
    [
        "evil\nDATABASE_URL=postgres://attacker@host/db",   # variable injection
        "boundary\rEVIL=1",
        "with a space",
        "short",
        "quote'; drop--",
    ],
)
def test_rejects_anything_that_could_inject(env_file, value):
    original = env_file.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        keysetup.set_value("NEON_API_KEY", value, env_file)
    # A rejected write must leave the file byte-identical.
    assert env_file.read_text(encoding="utf-8") == original


def test_rejects_a_bad_key_name(env_file):
    with pytest.raises(ValueError):
        keysetup.set_value("NEON API KEY", "nak_value000111222333", env_file)


def test_remove_drops_only_that_key(env_file):
    keysetup.set_value("NEON_API_KEY", "nak_value000111222333", env_file)
    assert keysetup.remove("NEON_API_KEY", env_file) is True
    text = env_file.read_text(encoding="utf-8")
    assert "NEON_API_KEY=" not in text
    assert "NEON_STORAGE_BUCKET=projectassets" in text
    assert keysetup.remove("NEON_API_KEY", env_file) is False   # already gone


def test_cli_key_path_is_separate_from_the_runtime_env(monkeypatch, tmp_path):
    """The deploy credential must not live in the file the app process loads."""
    monkeypatch.setenv(keysetup.SECRETS_OVERRIDE, str(tmp_path / ".secrets" / "neon-api-key"))
    monkeypatch.delenv(keysetup.ENV_LOCAL_OVERRIDE, raising=False)
    report = keysetup.write_cli_key("nak_separate000111222333444")
    assert report["path"].endswith(".secrets/neon-api-key")
    assert keysetup.env_local_path().name == ".env.local"
    assert keysetup.secrets_path() != keysetup.env_local_path()
    assert stat.S_IMODE(keysetup.secrets_path().stat().st_mode) == 0o600
    assert keysetup.read_cli_key() == "nak_separate000111222333444"


def test_shred_removes_the_file_and_the_value(monkeypatch, tmp_path):
    monkeypatch.setenv(keysetup.SECRETS_OVERRIDE, str(tmp_path / "api_key"))
    keysetup.write_cli_key("nak_shred000111222333444555")
    assert keysetup.shred_cli_key() is True
    assert not keysetup.secrets_path().exists()
    assert keysetup.read_cli_key() == ""
    assert keysetup.shred_cli_key() is False      # already gone


def test_fingerprint_and_mask_never_reveal_the_value():
    value = "nak_supersecretvalue0123456789"
    assert keysetup.mask(value).startswith("nak_")
    assert keysetup.mask(value).endswith("6789")
    assert value not in keysetup.mask(value)
    assert value not in keysetup.fingerprint(value)
    assert keysetup.fingerprint(value) != keysetup.fingerprint(value + "x")


# ---------------------------------------------------------------------------
# The routes (authority only)
# ---------------------------------------------------------------------------
def test_status_needs_auth():
    status, body, _ = Session().call("GET", "/api/v1/setup/neon-key", csrf=False)
    assert status == 401, body


def test_write_needs_auth():
    status, body, _ = Session().call(
        "POST", "/api/v1/setup/neon-key", {"api_key": "nak_x000000000000"}, csrf=False
    )
    assert status == 401, body


def test_viewer_cannot_read_or_write(viewer):
    status, body, _ = viewer.call("GET", "/api/v1/setup/neon-key")
    assert status == 403, body
    status, body, _ = viewer.call("POST", "/api/v1/setup/neon-key", {"api_key": "nak_x000000000000"})
    assert status == 403, body


def test_owner_can_read_status(owner):
    status, body, _ = owner.call("GET", "/api/v1/setup/neon-key")
    assert status == 200, body
    assert set(body) >= {"configured", "fingerprint", "masked", "file", "mode", "stored_in_runtime_env"}
    assert body["mode"] in {"0600", "absent"}
    # Whatever is configured, no full credential may appear in the response —
    # a masked value shows at most the first four and last four characters.
    import json
    import re

    assert not re.search(r"nak_[A-Za-z0-9_-]{8,}", json.dumps(body)), body


def test_intake_page_is_served_without_secrets():
    import urllib.request

    from conftest import BASE

    with urllib.request.urlopen(BASE + "/keysetup.html", timeout=10) as res:
        html = res.read().decode("utf-8")
    assert res.status == 200
    assert "Add your Neon API key" in html
    assert "nak_" in html            # only the placeholder in the input hint
    assert "NEON_API_KEY" in html

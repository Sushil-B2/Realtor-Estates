"""Neon service adapters: the contract must hold with zero services configured.

These tests never touch the network — they pin the *shape* of the integration so
that "not deployed yet" is a state the app models explicitly rather than a
crash waiting to happen.
"""
from __future__ import annotations

import pytest

from app import neon


def test_status_shape_lists_every_declared_service():
    st = neon.status()
    for key in ("postgres", "ai_gateway", "auth", "object_storage", "realtime", "functions"):
        assert key in st, f"{key} missing from service status"
        assert isinstance(st[key]["configured"], bool)
    assert "env_source" in st


def test_ai_chat_without_configuration_fails_soft():
    r = neon.ai_chat([{"role": "user", "content": "hi"}])
    assert r["ok"] is False
    assert r["text"] == ""
    assert "not configured" in r["error"]


def test_auth_never_authenticates_without_a_token():
    assert neon.verify_session_token("") is None


def test_presign_refuses_when_storage_is_unconfigured():
    if neon.storage_configured():  # pragma: no cover - only on a deployed env
        pytest.skip("storage is configured in this environment")
    with pytest.raises(RuntimeError):
        neon.presign_get("exports/listing.pdf")


def test_presign_signature_is_deterministic_for_fixed_inputs(monkeypatch):
    """SigV4 self-consistency (not verified against a live bucket yet)."""
    monkeypatch.setenv("NEON_STORAGE_ENDPOINT", "https://storage.example.com")
    monkeypatch.setenv("NEON_STORAGE_BUCKET", "assets")
    monkeypatch.setenv("NEON_STORAGE_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("NEON_STORAGE_SECRET_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("NEON_STORAGE_REGION", "auto")
    url = neon.presign_get("exports/listing.pdf", expires=900)
    assert url.startswith("https://storage.example.com/assets/exports/listing.pdf?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Expires=900" in url
    assert "X-Amz-Signature=" in url
    assert "wJalrXUtnFEMI" not in url, "secret key must never appear in a URL"

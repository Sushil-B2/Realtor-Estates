"""
Tests for `v1.26.0` (`docs/37`):
  1. Pure-stdlib SigV4 object-storage upload/stat/delete (`app/neon.py`) and
     API export & call-recording archiving (`POST/GET /exports`, `GET /recordings/{id}`).
  2. Cross-instance SSE realtime fan-out (`app/realtime.py` + `app/shared_state.py`)
     across `_MemoryAdapter`, `SharedFiles`, and RESP2 `Redis`, plus SSE RBAC/BOLA,
     `Last-Event-ID` cursor replay, and mid-stream session revocation.
  3. Batched multi-collection inbox SQL reads (`documents_many`), static shell
     `ETag` / `304 Not Modified` caching, and scale-to-zero anonymous `/healthz`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socketserver
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import conftest
from app import api, neon, security as sec
from app.realtime import RealtimeHub
from app.shared_state import Redis, SharedFiles, _MemoryAdapter
from app.store import Store


@pytest.fixture(autouse=True)
def _pin_json_store(monkeypatch):
    monkeypatch.setenv("RE_DB", "json")


# ---------------------------------------------------------------------------
# Local mock S3 HTTP server (no external network in unit tests)
# ---------------------------------------------------------------------------
class _MockS3:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.objects: dict[str, tuple[bytes, str, str]] = {}  # path -> (body, ctype, sha256)
        self.last_headers: dict[str, str] = {}
        self.fail_next_put = False


@pytest.fixture()
def mock_s3(monkeypatch):
    state = _MockS3()

    class S3Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _record(self):
            with state.lock:
                state.last_headers = {k.lower(): v for k, v in self.headers.items()}

        def do_PUT(self):
            self._record()
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("AWS4-HMAC-SHA256 "):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"AccessDenied")
                return
            if state.fail_next_put:
                state.fail_next_put = False
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"InternalError")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            claimed_sha = self.headers.get("x-amz-content-sha256", "")
            actual_sha = hashlib.sha256(body).hexdigest()
            if claimed_sha != actual_sha:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"XAmzContentSHA256Mismatch")
                return
            ctype = self.headers.get("Content-Type", "application/octet-stream")
            with state.lock:
                state.objects[self.path] = (body, ctype, actual_sha)
            self.send_response(200)
            self.send_header("ETag", f'"{actual_sha[:16]}"')
            self.end_headers()

        def do_HEAD(self):
            self._record()
            with state.lock:
                obj = state.objects.get(self.path)
            if not obj:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype, sha = obj
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", ctype)
            self.send_header("ETag", f'"{sha[:16]}"')
            self.end_headers()

        def do_DELETE(self):
            self._record()
            with state.lock:
                state.objects.pop(self.path, None)
            self.send_response(204)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), S3Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("NEON_STORAGE_ENDPOINT", endpoint)
    monkeypatch.setenv("NEON_STORAGE_BUCKET", "testbucket")
    monkeypatch.setenv("NEON_STORAGE_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("NEON_STORAGE_SECRET_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("NEON_STORAGE_REGION", "ap-southeast-1")
    try:
        yield state
    finally:
        srv.shutdown()
        srv.server_close()


def _login(store: Store, email: str, pw: str = "Demo@12345!") -> tuple[str, str]:
    raw = json.dumps({"email": email, "password": pw}).encode()
    st, body, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {}, {"email": email, "password": pw},
        {"content-type": "application/json"}, "127.0.0.1", None, raw,
    )
    assert st == 200, (st, body)
    tok = body["token"]
    return tok, sec.csrf_token(tok)


def _api(store: Store, tok: str, csrf: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    raw = json.dumps(body or {}).encode() if method in ("POST", "PATCH", "PUT", "DELETE") else b""
    hdrs = {"authorization": f"Bearer {tok}", "x-csrf-token": csrf, "content-type": "application/json"}
    st, payload, _ = api.handle(store, method, path, {}, body or {}, hdrs, "127.0.0.1", None, raw)
    return st, payload


# ===========================================================================
# 1. SigV4 object-storage primitives + Exports & Recordings API
# ===========================================================================
@pytest.mark.parametrize("bad_key", [
    "",
    "   ",
    "../escape.json",
    "exports/tenants/t1/../../t2/audit.json",
    "exports//double.json",
    "exports\\win.json",
    "exports/null\x00byte.json",
])
def test_object_key_traversal_is_refused(bad_key: str):
    with pytest.raises(ValueError):
        neon._clean_object_key(bad_key)


def test_sigv4_put_head_delete_and_secret_redaction(mock_s3):
    data = b'{"hello":"s3","rows":3}'
    up = neon.put_object("/exports/tenants/t1/sample.json", data, content_type="application/json")
    assert up["ok"] is True
    assert up["key"] == "exports/tenants/t1/sample.json"
    assert up["bytes"] == len(data)
    assert "AWS4-HMAC-SHA256" in mock_s3.last_headers["authorization"]
    assert "wJalrXUtnFEMI" not in mock_s3.last_headers["authorization"]

    hd = neon.head_object("exports/tenants/t1/sample.json")
    assert hd["exists"] is True
    assert hd["bytes"] == len(data)

    dl = neon.delete_object("exports/tenants/t1/sample.json")
    assert dl["deleted"] is True
    assert neon.head_object("exports/tenants/t1/sample.json")["exists"] is False


def test_exports_upload_to_object_storage_and_enforce_bola_and_rbac(tmp_path, mock_s3):
    store = Store(str(tmp_path / "store.json"))
    sun_tok, sun_csrf = _login(store, "owner@realtorestates.app")
    met_tok, met_csrf = _login(store, "owner@metrorealty.app", "Metro@12345!")
    view_tok, view_csrf = _login(store, "viewer@realtorestates.app")

    # Viewer cannot create or list exports
    st_v, _ = _api(store, view_tok, view_csrf, "POST", "/api/v1/exports", {"kind": "audit"})
    assert st_v == 403

    # Owner creates compliance_bundle JSON + contacts CSV
    st1, r1 = _api(store, sun_tok, sun_csrf, "POST", "/api/v1/exports", {"kind": "compliance_bundle", "format": "json"})
    assert st1 == 200
    exp1 = r1["export"]
    assert exp1["stored_in"] == "object_storage"
    assert exp1["object_key"].startswith("exports/tenants/")
    assert "X-Amz-Signature=" in exp1["download_url"]
    assert f"/testbucket/{exp1['object_key']}" in mock_s3.objects

    st2, r2 = _api(store, sun_tok, sun_csrf, "POST", "/api/v1/exports", {"kind": "contacts", "format": "csv"})
    assert st2 == 200
    assert r2["export"]["content_type"].startswith("text/csv")

    # Upload failure surfaces 502 STORAGE_UPLOAD_FAILED rather than faking success
    mock_s3.fail_next_put = True
    st_fail, r_fail = _api(store, sun_tok, sun_csrf, "POST", "/api/v1/exports", {"kind": "audit"})
    assert st_fail == 502
    assert r_fail["error"]["code"] == "STORAGE_UPLOAD_FAILED"

    # Cross-tenant BOLA: Metro owner gets 404 on Sunrise's export ID
    st_bola, _ = _api(store, met_tok, met_csrf, "GET", f"/api/v1/exports/{exp1['id']}")
    assert st_bola == 404


def test_exports_inline_fallback_and_require_object_storage_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(neon, "storage_configured", lambda: False)
    store = Store(str(tmp_path / "store.json"))
    tok, csrf = _login(store, "owner@realtorestates.app")

    st_req, r_req = _api(store, tok, csrf, "POST", "/api/v1/exports", {"kind": "audit", "require_object_storage": True})
    assert st_req == 503
    assert r_req["error"]["code"] == "STORAGE_NOT_CONFIGURED"

    st_inl, r_inl = _api(store, tok, csrf, "POST", "/api/v1/exports", {"kind": "audit"})
    assert st_inl == 200
    assert r_inl["export"]["stored_in"] == "inline"
    assert r_inl["export"]["download_url"] == ""
    assert "entries" in r_inl["export"]["inline_preview"]


def test_call_recordings_and_voice_test_upload_and_pii_rbac(tmp_path, mock_s3):
    store = Store(str(tmp_path / "store.json"))
    sun_tok, sun_csrf = _login(store, "owner@realtorestates.app")
    mgr_tok, mgr_csrf = _login(store, "manager@realtorestates.app")
    view_tok, view_csrf = _login(store, "viewer@realtorestates.app")
    met_tok, met_csrf = _login(store, "owner@metrorealty.app", "Metro@12345!")

    _, cnts = _api(store, sun_tok, sun_csrf, "GET", "/api/v1/contacts")
    cid = cnts["contacts"][0]["id"]

    # Reject bad MIME and invalid base64
    st_mime, _ = _api(store, sun_tok, sun_csrf, "POST", f"/api/v1/contacts/{cid}/calls", {
        "direction": "in", "outcome": "answered", "recording_b64": base64.b64encode(b"abc").decode(),
        "recording_content_type": "text/html",
    })
    assert st_mime == 422

    st_b64, _ = _api(store, sun_tok, sun_csrf, "POST", f"/api/v1/contacts/{cid}/calls", {
        "direction": "in", "outcome": "answered", "recording_b64": "not!base64!",
        "recording_content_type": "audio/wav",
    })
    assert st_b64 == 422

    # Valid WAV recording archives to object storage
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 28
    st_ok, call_r = _api(store, sun_tok, sun_csrf, "POST", f"/api/v1/contacts/{cid}/calls", {
        "direction": "in", "outcome": "answered", "duration_sec": 19,
        "recording_b64": base64.b64encode(wav).decode(), "recording_content_type": "audio/wav",
    })
    assert st_ok == 200
    rec = call_r["recording"]
    assert rec["stored_in"] == "object_storage"
    assert rec["object_key"].startswith("recordings/tenants/")
    rid = rec["activity_id"]

    # Manager (`pii:read`) may fetch presigned recording URL; Viewer (`no pii:read`) gets 403; Metro gets 404
    st_mgr, mgr_r = _api(store, mgr_tok, mgr_csrf, "GET", f"/api/v1/recordings/{rid}")
    assert st_mgr == 200
    assert "X-Amz-Signature=" in mgr_r["recording"]["download_url"]

    st_vw, _ = _api(store, view_tok, view_csrf, "GET", f"/api/v1/recordings/{rid}")
    assert st_vw == 403

    st_met, _ = _api(store, met_tok, met_csrf, "GET", f"/api/v1/recordings/{rid}")
    assert st_met == 404


# ===========================================================================
# 2. Shared-state event rings + RealtimeHub cross-instance fan-out
# ===========================================================================
def test_event_ring_isolation_and_cursor_across_memory_and_files(tmp_path):
    for adapter in (_MemoryAdapter(), SharedFiles(str(tmp_path / "sh"))):
        e1 = adapter.publish_event("tnt_a", {"type": "inbox.message", "target": "cnt_1"})
        e2 = adapter.publish_event("tnt_b", {"type": "inbox.message", "target": "cnt_foreign"})
        e3 = adapter.publish_event("tnt_a", {"type": "inbox.call", "target": "cnt_2"})
        assert (e1["id"], e3["id"]) == (1, 2)
        assert e2["id"] == 1

        all_a = adapter.events_since("tnt_a", 0)
        assert [x["target"] for x in all_a] == ["cnt_1", "cnt_2"]
        since_1 = adapter.events_since("tnt_a", 1)
        assert [x["target"] for x in since_1] == ["cnt_2"]
        assert all(x["tid"] == "tnt_a" for x in all_a)


def test_realtime_hub_relays_across_instances_and_fires_invalidation_hook(tmp_path):
    shared_dir = str(tmp_path / "hub-shared")
    hub_a = RealtimeHub(adapter=SharedFiles(shared_dir), relay_interval=0.03)
    hub_b = RealtimeHub(adapter=SharedFiles(shared_dir), relay_interval=0.03)
    invalidated: list[tuple[str, list[str]]] = []
    hub_b.on_remote_event(lambda tid, ev: invalidated.append((tid, list(ev.get("collections") or []))))

    try:
        with hub_b.subscribe("tnt_1") as sub_1, hub_b.subscribe("tnt_2") as sub_2:
            hub_a.publish("tnt_1", "inbox.message", "cnt_99", {"channel": "whatsapp"}, collections=("conversations", "contacts"))
            ev1 = sub_1.get(timeout=1.5)
            ev2 = sub_2.get(timeout=0.12)
        assert ev1 is not None and ev1["target"] == "cnt_99"
        assert ev2 is None
        assert invalidated == [("tnt_1", ["conversations", "contacts"])]
    finally:
        hub_a.close()
        hub_b.close()


def test_redis_resp2_event_ring_commands():
    kv: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    lock = threading.Lock()

    class RespHandler(socketserver.StreamRequestHandler):
        def handle(self):
            while True:
                line = self.rfile.readline()
                if not line or not line.startswith(b"*"):
                    return
                argc = int(line[1:].strip())
                args = []
                for _ in range(argc):
                    ln = int(self.rfile.readline()[1:].strip())
                    args.append(self.rfile.read(ln + 2)[:ln].decode())
                op = args[0].upper()
                with lock:
                    if op == "PING":
                        self.wfile.write(b"+PONG\r\n")
                    elif op == "INCR":
                        kv[args[1]] = str(int(kv.get(args[1], "0")) + 1)
                        self.wfile.write(f":{kv[args[1]]}\r\n".encode())
                    elif op == "PEXPIRE":
                        self.wfile.write(b":1\r\n")
                    elif op == "GET":
                        v = kv.get(args[1])
                        self.wfile.write(b"$-1\r\n" if v is None else f"${len(v)}\r\n{v}\r\n".encode())
                    elif op == "RPUSH":
                        lst = lists.setdefault(args[1], [])
                        lst.extend(args[2:])
                        self.wfile.write(f":{len(lst)}\r\n".encode())
                    elif op == "LTRIM":
                        lst = lists.get(args[1], [])
                        s, e = int(args[2]), int(args[3])
                        ln = len(lst)
                        s_i = max(0, ln + s if s < 0 else s)
                        e_i = (ln + e if e < 0 else e) + 1
                        lists[args[1]] = lst[s_i:e_i]
                        self.wfile.write(b"+OK\r\n")
                    elif op == "LRANGE":
                        lst = lists.get(args[1], [])
                        s, e = int(args[2]), int(args[3])
                        ln = len(lst)
                        sub = lst[max(0, ln + s if s < 0 else s):(ln + e if e < 0 else e) + 1]
                        buf = [f"*{len(sub)}\r\n".encode()] + [f"${len(x)}\r\n{x}\r\n".encode() for x in sub]
                        self.wfile.write(b"".join(buf))
                self.wfile.flush()

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), RespHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = Redis(f"redis://127.0.0.1:{srv.server_address[1]}/0")
        r.publish_event("tnt_r", {"type": "inbox.message", "target": "cnt_r1"})
        r.publish_event("tnt_r", {"type": "inbox.call", "target": "cnt_r2"})
        assert [x["target"] for x in r.events_since("tnt_r", 0)] == ["cnt_r1", "cnt_r2"]
        assert [x["target"] for x in r.events_since("tnt_r", 1)] == ["cnt_r2"]
        assert r.events_since("tnt_r", 2) == []
    finally:
        srv.shutdown()
        srv.server_close()


# ===========================================================================
# 3. Batched inbox reads, static ETag/304 shell cache, and scale-to-zero /healthz
# ===========================================================================
def test_inbox_batches_collections_into_one_query_and_invalidates_on_event(tmp_path):
    store = Store(str(tmp_path / "store.json"))
    tok, _ = _login(store, "owner@realtorestates.app")
    tid = sec.verify_token(tok)["tid"]
    calls = {"many": 0, "single": 0, "pings": 0}

    class SpyPg:
        def documents_many(self, collections, tenant_id):
            calls["many"] += 1
            return {c: store.all(c, tenant_id) for c in collections}

        def documents(self, collection, tenant_id):
            calls["single"] += 1
            return store.all(collection, tenant_id)

        def ping(self):
            calls["pings"] += 1
            return {"ok": True, "latency_ms": 1, "version": "PostgreSQL 18"}

    store.backend = SpyPg()
    store.backend_name = "postgres"

    st1, body1, _ = api.handle(store, "GET", "/api/v1/inbox", {}, {}, {"authorization": f"Bearer {tok}"}, "127.0.0.1", None)
    st2, _, _ = api.handle(store, "GET", "/api/v1/inbox", {}, {}, {"authorization": f"Bearer {tok}"}, "127.0.0.1", None)
    assert st1 == 200 and st2 == 200
    assert calls["many"] == 1
    assert calls["single"] == 0
    assert len(body1["threads"]) >= 1

    # Remote realtime invalidation triggers a fresh batched query on the next read
    store.invalidate_freshness(tid, ("conversations", "contacts"))
    st3, _, _ = api.handle(store, "GET", "/api/v1/inbox", {}, {}, {"authorization": f"Bearer {tok}"}, "127.0.0.1", None)
    assert st3 == 200
    assert calls["many"] == 2
    assert calls["single"] == 0

    # Anonymous liveness status (`probe=False`) never calls `ping()`
    info = store.backend_status(probe=False)
    assert info["backend"] == "postgres" and info["durable"] is True
    assert calls["pings"] == 0


def test_live_server_static_etag_304_and_sse_stream(owner):
    base = conftest.BASE
    # 1. Static shell returns ETag + 304 Not Modified on If-None-Match (and weak W/ tag)
    with urllib.request.urlopen(base + "/styles.css", timeout=5) as r1:
        etag = r1.headers.get("ETag", "")
        cc = r1.headers.get("Cache-Control", "")
        data = r1.read()
    assert etag.startswith('"') and len(data) > 50
    assert "max-age=3600" in cc

    req_304 = urllib.request.Request(base + "/styles.css", headers={"If-None-Match": f"W/{etag}"})
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req_304, timeout=5)
    assert exc_info.value.code == 304
    assert exc_info.value.read() == b""

    # 2. Live SSE emits initial snapshot + live inbox event with `id:` cursor
    st_cnt, cnt_body, _ = owner.call("GET", "/api/v1/contacts")
    assert st_cnt == 200
    cid = next(c["id"] for c in cnt_body["contacts"] if (c.get("consent") or {}).get("status") == "granted")

    received: list[dict] = []
    snap_ready = threading.Event()

    def reader():
        req = urllib.request.Request(
            base + "/api/v1/events?max_events=1&timeout_ms=5000",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            for raw in resp:
                s = raw.decode("utf-8", "replace").strip()
                if s.startswith("data:"):
                    doc = json.loads(s[5:].strip())
                    if doc.get("type") == "snapshot":
                        snap_ready.set()
                    else:
                        received.append(doc)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    assert snap_ready.wait(timeout=4.0), "SSE snapshot did not arrive"

    st_send, _, _ = owner.call(
        "POST", f"/api/v1/inbox/{cid}/send",
        {"channel": "whatsapp", "text": "Hello from SSE test", "override_quiet_hours": True},
    )
    assert st_send == 200
    t.join(timeout=5.0)
    assert len(received) == 1
    assert received[0]["type"] == "inbox.message"
    assert received[0]["target"] == cid

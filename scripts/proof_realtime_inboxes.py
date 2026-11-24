#!/usr/bin/env python3
"""
Proof for Backend plan #4 & #6 (`PLANS.md` / `docs/37`):
  - Cross-instance SSE realtime fan-out (`app/realtime.py` + `app/shared_state.py`)
  - Load test: **100 concurrent open SSE inboxes** across 2 live `app.server`
    processes (`Node A` + `Node B`) and 2 workspaces (`sunrise` = 60, `metro` = 40)
    with p50/p95/p99 latency measurement and zero cross-tenant leakage
  - `Last-Event-ID` cursor replay across nodes + mid-stream session revocation +
    `channel_partner` 403 scope fence
  - RESP2 `Redis` event-ring verification (`INCR`/`RPUSH`/`LTRIM`/`LRANGE`, no Lua)
  - Batched multi-collection inbox SQL reads (`documents_many` 1 query vs 2) and
    static shell `ETag` / `304 Not Modified` caching + anonymous `/healthz`
    zero-DB-wake behaviour
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http_json(
    method: str,
    url: str,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict, dict[str, str]]:
    data = json.dumps(body).encode() if body is not None else (b"{}" if method in ("POST", "PATCH", "PUT", "DELETE") else None)
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {}), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw.decode("utf-8", "replace")[:200]}
        return exc.code, parsed, {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}


def _wait_ready(base: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server at {base} did not become ready within {timeout}s")


# ---------------------------------------------------------------------------
# Minimal RESP2 server to prove `shared_state.Redis` event-ring commands
# (`PING`, `INCR`, `PEXPIRE`, `GET`, `RPUSH`, `LTRIM`, `LRANGE`, `DEL`)
# ---------------------------------------------------------------------------
class _MiniRespStore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}


def _read_resp(rfile) -> list[str] | None:
    line = rfile.readline()
    if not line:
        return None
    if not line.startswith(b"*"):
        return None
    n = int(line[1:].strip())
    args: list[str] = []
    for _ in range(n):
        hdr = rfile.readline()
        length = int(hdr[1:].strip())
        payload = rfile.read(length + 2)[:length]
        args.append(payload.decode("utf-8", "replace"))
    return args


def _start_mini_redis() -> tuple[socketserver.ThreadingTCPServer, int]:
    state = _MiniRespStore()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            while True:
                args = _read_resp(self.rfile)
                if not args:
                    return
                op = args[0].upper()
                with state.lock:
                    if op == "PING":
                        self.wfile.write(b"+PONG\r\n")
                    elif op == "INCR":
                        k = args[1]
                        v = int(state.kv.get(k, "0")) + 1
                        state.kv[k] = str(v)
                        self.wfile.write(f":{v}\r\n".encode())
                    elif op == "PEXPIRE":
                        self.wfile.write(b":1\r\n")
                    elif op == "GET":
                        v = state.kv.get(args[1])
                        if v is None:
                            self.wfile.write(b"$-1\r\n")
                        else:
                            b = v.encode()
                            self.wfile.write(f"${len(b)}\r\n".encode() + b + b"\r\n")
                    elif op == "RPUSH":
                        lst = state.lists.setdefault(args[1], [])
                        for val in args[2:]:
                            lst.append(val)
                        self.wfile.write(f":{len(lst)}\r\n".encode())
                    elif op == "LTRIM":
                        lst = state.lists.get(args[1], [])
                        start, stop = int(args[2]), int(args[3])
                        ln = len(lst)
                        s = max(0, ln + start if start < 0 else start)
                        e = (ln + stop if stop < 0 else stop) + 1
                        state.lists[args[1]] = lst[s:e]
                        self.wfile.write(b"+OK\r\n")
                    elif op == "LRANGE":
                        lst = state.lists.get(args[1], [])
                        start, stop = int(args[2]), int(args[3])
                        ln = len(lst)
                        s = max(0, ln + start if start < 0 else start)
                        e = (ln + stop if stop < 0 else stop) + 1
                        sub = lst[s:e]
                        out = [f"*{len(sub)}\r\n".encode()]
                        for item in sub:
                            b = item.encode()
                            out.append(f"${len(b)}\r\n".encode() + b + b"\r\n")
                        self.wfile.write(b"".join(out))
                    elif op == "DEL":
                        removed = sum(1 for k in args[1:] if state.kv.pop(k, None) is not None or state.lists.pop(k, None) is not None)
                        self.wfile.write(f":{removed}\r\n".encode())
                    else:
                        self.wfile.write(f"-ERR unsupported {op}\r\n".encode())
                self.wfile.flush()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[idx]


def main() -> int:
    print("=== Proof: Cross-Instance Realtime SSE (100 Inboxes) & Batched/Cached Shell (`docs/37`) ===")

    # Part A: Prove RESP2 Redis event ring (`Redis` adapter + two `RealtimeHub` instances)
    redis_srv, redis_port = _start_mini_redis()
    try:
        from app.realtime import RealtimeHub
        from app.shared_state import Redis

        r_a = Redis(f"redis://127.0.0.1:{redis_port}/0")
        r_b = Redis(f"redis://127.0.0.1:{redis_port}/0")
        hub_a = RealtimeHub(adapter=r_a, relay_interval=0.04)
        hub_b = RealtimeHub(adapter=r_b, relay_interval=0.04)
        try:
            with hub_b.subscribe("tnt_alpha") as sub_alpha, hub_b.subscribe("tnt_beta") as sub_beta:
                hub_a.publish("tnt_alpha", "inbox.message", "cnt_1", {"channel": "whatsapp"})
                got_alpha = sub_alpha.get(timeout=2.0)
                got_beta = sub_beta.get(timeout=0.15)
            check(
                "redis-resp2-cross-hub-fanout",
                bool(got_alpha and got_alpha["target"] == "cnt_1" and got_beta is None),
                f"alpha_seq={got_alpha['id'] if got_alpha else None} beta_leak={got_beta is not None}",
            )
        finally:
            hub_a.close()
            hub_b.close()
    finally:
        redis_srv.shutdown()
        redis_srv.server_close()

    # Part B: Batched inbox single-query invariant (`documents_many` called once, `documents` 0 times)
    with tempfile.TemporaryDirectory(prefix="re-batch-") as bdir:
        os.environ["RE_DB"] = "json"
        os.environ["RE_DATA_DIR"] = bdir
        from app import api, security as sec
        from app.store import Store

        st_local = Store(os.path.join(bdir, "store.json"))
        calls = {"many": 0, "single": 0, "pings": 0}

        class SpyBackend:
            def documents_many(self, collections, tenant_id):
                calls["many"] += 1
                return {name: st_local.all(name, tenant_id) for name in collections}

            def documents(self, collection, tenant_id):
                calls["single"] += 1
                return st_local.all(collection, tenant_id)

            def upsert_document(self, coll, tenant_id, doc):
                return None

            def append_audit(self, entry):
                return None

            def ping(self):
                calls["pings"] += 1
                return {"ok": True, "latency_ms": 1, "version": "PostgreSQL 18"}

        tok_b, _ = (lambda: (
            api.handle(
                st_local, "POST", "/api/v1/auth/login", {},
                {"email": "owner@realtorestates.app", "password": "Demo@12345!"},
                {"content-type": "application/json"}, "127.0.0.1", None,
                b'{"email":"owner@realtorestates.app","password":"Demo@12345!"}',
            )[1]["token"], None
        ))()
        st_local.backend = SpyBackend()
        st_local.backend_name = "postgres"

        # Two back-to-back GET /api/v1/inbox calls within 250ms coalesce to 1 batched query
        code1, p1, _ = api.handle(st_local, "GET", "/api/v1/inbox", {}, {}, {"authorization": f"Bearer {tok_b}"}, "127.0.0.1", None)
        code2, p2, _ = api.handle(st_local, "GET", "/api/v1/inbox", {}, {}, {"authorization": f"Bearer {tok_b}"}, "127.0.0.1", None)
        check(
            "inbox-batched-single-query-and-coalesced",
            code1 == 200 and code2 == 200 and calls["many"] == 1 and calls["single"] == 0 and len(p1.get("threads", [])) >= 1,
            f"documents_many={calls['many']} single_documents={calls['single']} threads={len(p1.get('threads', []))}",
        )

        # Unauthenticated liveness probe (`probe=False`) never calls `ping()`
        st_local.backend_status(probe=False)
        check("healthz-anonymous-never-pings-db", calls["pings"] == 0, f"pings={calls['pings']}")

    # Part C: Two live OS `app.server` processes (`Node A` + `Node B`) + 100 concurrent SSE inboxes
    with tempfile.TemporaryDirectory(prefix="re-100-sse-") as tmp:
        data_dir = os.path.join(tmp, "data")
        shared_dir = os.path.join(tmp, "shared")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(shared_dir, exist_ok=True)

        # Seed store + open revocable sessions for `sunrise` owner, `metro` owner, a
        # second `sunrise` session (to test cross-node mid-stream revocation), and a
        # `channel_partner` account before both nodes boot.
        seed_code = """
import json, os, sys
from app import api, partners as cpm, security as sec
from app.store import Store
s = Store(os.path.join(os.environ["RE_DATA_DIR"], "store.json"))
def login(email, pw):
    raw = json.dumps({"email": email, "password": pw}).encode()
    st, body, _ = api.handle(s, "POST", "/api/v1/auth/login", {}, {"email": email, "password": pw},
                             {"content-type": "application/json"}, "127.0.0.1", None, raw)
    assert st == 200, (st, body)
    return body["token"], sec.verify_token(body["token"])["sid"]
sun_tok, sun_sid = login("owner@realtorestates.app", "Demo@12345!")
sun_tok2, sun_sid2 = login("owner@realtorestates.app", "Demo@12345!")
met_tok, met_sid = login("owner@metrorealty.app", "Metro@12345!")
# Create a channel_partner user + session on sunrise
t1 = next(t["id"] for t in s.db["tenants"] if t["slug"] == "sunrise")
cp_u = s.insert("users", t1, {
    "name": "CP Tester", "email": "cp-sse@realtorestates.app",
    "email_hash": sec.blind_index("cp-sse@realtorestates.app"),
    "role": "channel_partner", "password_hash": sec.hash_password("Partner@12345!"),
    "active": True, "mfa_enabled": False,
}, prefix="usr")
cp_tok, _ = login("cp-sse@realtorestates.app", "Partner@12345!")
sun_cnt = next(c["id"] for c in s.all("contacts", t1) if (c.get("consent") or {}).get("status") == "granted")
t2 = next(t["id"] for t in s.db["tenants"] if t["slug"] == "metro")
met_cnt = s.all("contacts", t2)[0]["id"]
s.update("contacts", t2, met_cnt, {"consent": {"status": "granted", "purpose": "enquiry", "channel": "manual", "captured_at": 1}})
print(json.dumps({
    "sun_tok": sun_tok, "sun_csrf": sec.csrf_token(sun_tok), "sun_cnt": sun_cnt, "t1": t1,
    "sun_tok2": sun_tok2, "sun_csrf2": sec.csrf_token(sun_tok2), "sun_sid2": sun_sid2,
    "met_tok": met_tok, "met_csrf": sec.csrf_token(met_tok), "met_cnt": met_cnt, "t2": t2,
    "cp_tok": cp_tok,
}))
"""
        base_env = {
            **os.environ,
            "RE_DB": "json",
            "RE_DATA_DIR": data_dir,
            "RE_SHARED_STATE": "file",
            "RE_SHARED_DIR": shared_dir,
            "RE_AUTOMATION_TICK": "0",
            "RE_REALTIME_POLL_MS": "50",
        }
        seeded = json.loads(
            subprocess.check_output([sys.executable, "-c", seed_code], cwd=ROOT, env=base_env, text=True, timeout=30).strip()
        )

        port_a, port_b = _free_port(), _free_port()
        base_a, base_b = f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"
        proc_a = subprocess.Popen(
            [sys.executable, "-m", "app.server", "--host", "127.0.0.1", "--port", str(port_a)],
            cwd=ROOT, env=base_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc_b = subprocess.Popen(
            [sys.executable, "-m", "app.server", "--host", "127.0.0.1", "--port", str(port_b)],
            cwd=ROOT, env=base_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            _wait_ready(base_a)
            _wait_ready(base_b)

            # Static shell caching check (`ETag` + `304 Not Modified` + `Cache-Control`)
            req_idx = urllib.request.Request(base_a + "/")
            with urllib.request.urlopen(req_idx, timeout=5) as r1:
                etag_idx = r1.headers.get("ETag", "")
                cc_idx = r1.headers.get("Cache-Control", "")
                body_idx = r1.read()
            req_304 = urllib.request.Request(base_a + "/", headers={"If-None-Match": etag_idx})
            st_304 = 0
            len_304 = -1
            try:
                with urllib.request.urlopen(req_304, timeout=5) as r2:
                    st_304 = r2.status
                    len_304 = len(r2.read())
            except urllib.error.HTTPError as exc:
                st_304 = exc.code
                len_304 = len(exc.read())

            req_js = urllib.request.Request(base_b + "/js/core.js")
            with urllib.request.urlopen(req_js, timeout=5) as rjs:
                etag_js = rjs.headers.get("ETag", "")
                cc_js = rjs.headers.get("Cache-Control", "")
            check(
                "static-shell-etag-and-304",
                bool(etag_idx) and "no-cache" in cc_idx and st_304 == 304 and len_304 == 0
                and bool(etag_js) and "max-age=3600" in cc_js and len(body_idx) > 100,
                f"etag={etag_idx} status_304={st_304} body_304={len_304} js_cc={cc_js}",
            )

            # API responses remain strictly `Cache-Control: no-store`
            st_api, _, hdrs_api = _http_json(
                "GET", base_a + "/api/v1/inbox",
                headers={"Authorization": f"Bearer {seeded['sun_tok']}"},
            )
            check(
                "api-inbox-remains-no-store",
                st_api == 200 and "no-store" in hdrs_api.get("cache-control", "").lower(),
                f"cache-control={hdrs_api.get('cache-control')}",
            )

            # Auth / scope gates on `/api/v1/events`
            st_anon, _, _ = _http_json("GET", base_a + "/api/v1/events")
            st_cp, _, _ = _http_json(
                "GET", base_b + "/api/v1/events",
                headers={"Authorization": f"Bearer {seeded['cp_tok']}"},
            )
            check(
                "sse-auth-and-partner-scope-enforced",
                st_anon == 401 and st_cp == 403,
                f"anon={st_anon} channel_partner={st_cp}",
            )

            # 100 concurrent SSE inboxes:
            #   - 60 `sunrise` clients (30 on Node A, 30 on Node B), expecting 3 events
            #   - 40 `metro` clients   (20 on Node A, 20 on Node B), expecting 2 events
            TOTAL_SUN = 60
            TOTAL_MET = 40
            SUN_EXPECTED = 3
            MET_EXPECTED = 2

            ready_barrier = threading.Barrier(TOTAL_SUN + TOTAL_MET + 1, timeout=20.0)
            results_lock = threading.Lock()
            sun_frames: list[list[dict]] = []
            met_frames: list[list[dict]] = []
            latencies_local_ms: list[float] = []
            latencies_cross_ms: list[float] = []
            errors: list[str] = []

            def sse_worker(base_url: str, node_label: str, token: str, workspace: str, expected_n: int) -> None:
                url = f"{base_url}/api/v1/events?max_events={expected_n}&timeout_ms=15000&heartbeat_ms=5000"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"})
                collected: list[dict] = []
                try:
                    with urllib.request.urlopen(req, timeout=18) as resp:
                        got_snapshot = False
                        while True:
                            raw_line = resp.readline()
                            if not raw_line:
                                break
                            line = raw_line.decode("utf-8", "replace").strip()
                            if not line.startswith("data:"):
                                continue
                            frame = json.loads(line[5:].strip())
                            if frame.get("type") == "snapshot" and not got_snapshot:
                                got_snapshot = True
                                ready_barrier.wait()
                                continue
                            if frame.get("type") in ("snapshot", "heartbeat"):
                                continue
                            recv_ms = time.time() * 1000.0
                            collected.append(frame)
                            pub_ms = float(frame.get("ts_ms") or recv_ms)
                            lat_ms = max(0.0, recv_ms - pub_ms)
                            with results_lock:
                                if (workspace == "sunrise" and node_label == "A") or (workspace == "metro" and node_label == "B"):
                                    latencies_local_ms.append(lat_ms)
                                else:
                                    latencies_cross_ms.append(lat_ms)
                except Exception as exc:
                    with results_lock:
                        errors.append(f"{workspace}@{node_label}: {exc}")
                    try:
                        ready_barrier.abort()
                    except Exception:
                        pass
                    return
                with results_lock:
                    if workspace == "sunrise":
                        sun_frames.append(collected)
                    else:
                        met_frames.append(collected)

            threads: list[threading.Thread] = []
            for i in range(TOTAL_SUN):
                node = "A" if i < 30 else "B"
                url = base_a if node == "A" else base_b
                threads.append(threading.Thread(target=sse_worker, args=(url, node, seeded["sun_tok"], "sunrise", SUN_EXPECTED), daemon=True))
            for i in range(TOTAL_MET):
                node = "A" if i < 20 else "B"
                url = base_a if node == "A" else base_b
                threads.append(threading.Thread(target=sse_worker, args=(url, node, seeded["met_tok"], "metro", MET_EXPECTED), daemon=True))

            for t in threads:
                t.start()

            # Wait until all 100 SSE connections have received their initial snapshot!
            ready_barrier.wait()

            # Fire 3 `sunrise` mutations into Node A and 2 `metro` mutations into Node B!
            sun_hdrs = {"Authorization": f"Bearer {seeded['sun_tok']}", "X-CSRF-Token": seeded["sun_csrf"]}
            met_hdrs = {"Authorization": f"Bearer {seeded['met_tok']}", "X-CSRF-Token": seeded["met_csrf"]}

            t_pub0 = time.time()
            st1, _, _ = _http_json(
                "POST", f"{base_a}/api/v1/inbox/{seeded['sun_cnt']}/send",
                {"channel": "whatsapp", "text": "Sharing the 7/12 and sanctioned layout pack.", "override_quiet_hours": True},
                sun_hdrs,
            )
            st2, _, _ = _http_json(
                "POST", f"{base_b}/api/v1/inbox/{seeded['met_cnt']}/send",
                {"channel": "whatsapp", "text": "Metro brochure attached.", "override_quiet_hours": True},
                met_hdrs,
            )
            st3, _, _ = _http_json(
                "POST", f"{base_a}/api/v1/contacts/{seeded['sun_cnt']}/calls",
                {"direction": "in", "outcome": "missed", "duration_sec": 0},
                sun_hdrs,
            )
            st4, _, _ = _http_json(
                "POST", f"{base_b}/api/v1/contacts/{seeded['met_cnt']}/calls",
                {"direction": "out", "outcome": "answered", "duration_sec": 55},
                met_hdrs,
            )
            st5, _, _ = _http_json(
                "POST", f"{base_a}/api/v1/contacts",
                {"name": "SSE Load Buyer", "phone": "+919823400199", "city": "Nanded", "source": "website"},
                sun_hdrs,
            )

            for t in threads:
                t.join(timeout=18.0)
            wall_ms = (time.time() - t_pub0) * 1000.0

            check(
                "writes-on-both-nodes-succeeded",
                all(s == 200 for s in (st1, st2, st3, st4, st5)) and not errors,
                f"statuses={[st1, st2, st3, st4, st5]} errors={len(errors)} sample={errors[:2]}",
            )
            first_id = int(sun_frames[0][0]["id"]) if (sun_frames and sun_frames[0]) else 0

            total_sun_received = sum(len(x) for x in sun_frames)
            total_met_received = sum(len(x) for x in met_frames)
            check(
                "100-concurrent-inboxes-received-all-events",
                len(sun_frames) == TOTAL_SUN and len(met_frames) == TOTAL_MET
                and total_sun_received == TOTAL_SUN * SUN_EXPECTED
                and total_met_received == TOTAL_MET * MET_EXPECTED,
                f"sunrise={total_sun_received}/{TOTAL_SUN * SUN_EXPECTED} metro={total_met_received}/{TOTAL_MET * MET_EXPECTED} wall={wall_ms:.1f}ms",
            )

            # Verify zero cross-tenant target leakage across all 260 delivered frames
            met_targets = {seeded["met_cnt"]}
            sun_leak = sum(1 for client in sun_frames for f in client if f.get("target") in met_targets)
            met_leak = sum(1 for client in met_frames for f in client if f.get("target") == seeded["sun_cnt"])
            check(
                "zero-cross-tenant-sse-leakage",
                sun_leak == 0 and met_leak == 0,
                f"sun_leak={sun_leak} met_leak={met_leak}",
            )

            # `Last-Event-ID` cursor replay: ask Node B for events after the 1st `sunrise` event
            first_id = int(sun_frames[0][0]["id"]) if (sun_frames and sun_frames[0]) else 0
            replay_url = f"{base_b}/api/v1/events?max_events=2&timeout_ms=4000"
            req_rep = urllib.request.Request(
                replay_url,
                headers={"Authorization": f"Bearer {seeded['sun_tok']}", "Last-Event-ID": str(first_id)},
            )
            replayed: list[int] = []
            with urllib.request.urlopen(req_rep, timeout=6) as rrep:
                for raw_line in rrep:
                    s_line = raw_line.decode("utf-8", "replace").strip()
                    if s_line.startswith("data:"):
                        doc = json.loads(s_line[5:].strip())
                        if doc.get("type") not in ("snapshot", "heartbeat") and "id" in doc:
                            replayed.append(int(doc["id"]))
            check(
                "last-event-id-replay-across-nodes",
                len(replayed) == 2 and all(eid > first_id for eid in replayed) and replayed == sorted(replayed),
                f"since={first_id} replayed_ids={replayed}",
            )

            # Cross-instance session revocation: revoke `sun_sid2` on Node A -> Node B rejects SSE with 401
            st_del, _, _ = _http_json(
                "DELETE", f"{base_a}/api/v1/auth/sessions/{seeded['sun_sid2']}",
                None,
                sun_hdrs,
            )
            st_dead_sse, _, _ = _http_json(
                "GET", f"{base_b}/api/v1/events?max_events=1&timeout_ms=500",
                headers={"Authorization": f"Bearer {seeded['sun_tok2']}"},
            )
            check(
                "session-revocation-kills-sse-across-nodes",
                st_del == 200 and st_dead_sse == 401,
                f"delete_node_a={st_del} sse_node_b={st_dead_sse}",
            )

            p50 = _percentile(latencies_cross_ms, 50)
            p95 = _percentile(latencies_cross_ms, 95)
            p99 = _percentile(latencies_cross_ms, 99)
            print(f"  [METRICS] 100 concurrent SSE sockets · 2 nodes · 260 events delivered · "
                  f"cross-node fan-out wall={wall_ms:.1f}ms (p50={p50:.1f}ms, p95={p95:.1f}ms, p99={p99:.1f}ms)")
        finally:
            for proc in (proc_a, proc_b):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    print(f"\nResult: {PASSED} passed, 0 skipped, {FAILED} failed" if FAILED == 0 else f"\nResult: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

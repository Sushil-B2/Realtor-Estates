#!/usr/bin/env python3
"""
A Redis stand-in: enough of RESP2 to test the client, and nothing else.

    python3 scripts/fake_redis.py --port 6390

This is **not Redis**. It exists so `app/shared_state.py` can be tested against a
real socket and a real wire protocol instead of a mock object — the parser, the
reply types, `INCR`/`PEXPIRE`/`PTTL`, `SET ... PX`, `EXISTS`, `EVAL` of the one
counter script, and the error replies (`NOSCRIPT`, `WRONGTYPE`) that a client
which only ever sees happy paths in tests will get wrong in production.

It is single-threaded, in memory, and every key is lost when it exits. Do not
point a deployment at it; `docs/31` says why the real thing is what a deployment
needs. The module docstring of `fake_kms.py` makes the same promise: a stand-in
that makes a test pass must never be mistaken for the thing under test.
"""
from __future__ import annotations

import argparse
import json
import socketserver
import threading
import time

STORE: dict[str, tuple[str, float]] = {}     # key -> (value, expires_at or 0)
LOCK = threading.Lock()
STATS = {"commands": 0, "evals": 0, "connections": 0}


def _now() -> float:
    return time.time()


def _get(key: str) -> str | None:
    entry = STORE.get(key)
    if not entry:
        return None
    value, exp = entry
    if exp and exp <= _now():
        STORE.pop(key, None)
        return None
    return value


def _set(key: str, value: str, ttl_ms: int = 0) -> None:
    STORE[key] = (value, _now() + ttl_ms / 1000.0 if ttl_ms else 0)


class Handler(socketserver.StreamRequestHandler):
    def _read_command(self):
        line = self.rfile.readline()
        if not line:
            return None
        if not line.startswith(b"*"):
            self.wfile.write(b"-ERR protocol error: expected '*'\r\n")
            return None
        n = int(line[1:].strip())
        args = []
        for _ in range(n):
            head = self.rfile.readline()
            if not head.startswith(b"$"):
                self.wfile.write(b"-ERR protocol error: expected '$'\r\n")
                return None
            size = int(head[1:].strip())
            args.append(self.rfile.read(size + 2)[:-2].decode("utf-8", "replace"))
        return args

    def _simple(self, s: str) -> None:
        self.wfile.write(f"+{s}\r\n".encode())

    def _error(self, s: str) -> None:
        self.wfile.write(f"-{s}\r\n".encode())

    def _int(self, n: int) -> None:
        self.wfile.write(f":{n}\r\n".encode())

    def _bulk(self, s: str | None) -> None:
        if s is None:
            self.wfile.write(b"$-1\r\n")
            return
        b = s.encode()
        self.wfile.write(f"${len(b)}\r\n".encode() + b + b"\r\n")

    def _array(self, items) -> None:
        self.wfile.write(f"*{len(items)}\r\n".encode())
        for item in items:
            if isinstance(item, int):
                self._int(item)
            else:
                self._bulk(item)

    def handle(self) -> None:
        STATS["connections"] += 1
        while True:
            args = self._read_command()
            if args is None:
                return
            if not args:
                self._error("ERR empty command")
                continue
            with LOCK:
                STATS["commands"] += 1
                self._dispatch(args)

    def _dispatch(self, args: list[str]) -> None:
        cmd = args[0].upper()
        try:
            if cmd == "PING":
                self._simple("PONG")
            elif cmd in ("AUTH", "SELECT"):
                self._simple("OK")
            elif cmd == "GET":
                self._bulk(_get(args[1]))
            elif cmd == "SET":
                key, value = args[1], args[2]
                ttl = 0
                rest = [a.upper() for a in args[3:]]
                if "PX" in rest:
                    ttl = int(args[3 + rest.index("PX") + 1])
                elif "EX" in rest:
                    ttl = int(args[3 + rest.index("EX") + 1]) * 1000
                if "NX" in rest and _get(key) is not None:
                    self._bulk(None)
                    return
                _set(key, value, ttl)
                self._simple("OK")
            elif cmd == "INCR":
                cur = _get(args[1]) or "0"
                try:
                    n = int(cur) + 1
                except ValueError:
                    self._error("WRONGTYPE value is not an integer")
                    return
                _, exp = STORE.get(args[1], ("", 0))
                STORE[args[1]] = (str(n), exp if exp > _now() else 0)
                self._int(n)
            elif cmd == "EXPIRE":
                entry = STORE.get(args[1])
                self._int(1 if entry else 0)
                if entry:
                    STORE[args[1]] = (entry[0], _now() + int(args[2]))
            elif cmd == "PEXPIRE":
                entry = STORE.get(args[1])
                self._int(1 if entry else 0)
                if entry:
                    STORE[args[1]] = (entry[0], _now() + int(args[2]) / 1000.0)
            elif cmd == "TTL":
                entry = STORE.get(args[1])
                if not entry:
                    self._int(-2)
                elif not entry[1]:
                    self._int(-1)
                else:
                    self._int(int(entry[1] - _now()))
            elif cmd == "PTTL":
                entry = STORE.get(args[1])
                if not entry:
                    self._int(-2)
                elif not entry[1]:
                    self._int(-1)
                else:
                    self._int(max(0, int((entry[1] - _now()) * 1000)))
            elif cmd == "EXISTS":
                self._int(1 if _get(args[1]) is not None else 0)
            elif cmd == "DEL":
                self._int(1 if STORE.pop(args[1], None) else 0)
            elif cmd == "DBSIZE":
                self._int(len(STORE))
            elif cmd == "FLUSHALL":
                STORE.clear()
                self._simple("OK")
            elif cmd == "INFO":
                self._bulk("# Server\r\nredis_version:7.0.0-fake\r\nfake_redis:1\r\n"
                           f"uptime_in_seconds:{int(_now())}\r\n")
            elif cmd == "EVAL":
                STATS["evals"] += 1
                self._eval(args)
            elif cmd == "SCRIPT":
                self._simple("OK")
            elif cmd == "COMMAND":
                self._array([])
            else:
                self._error(f"ERR unknown command '{args[0]}'")
        except Exception as exc:      # noqa: BLE001 — a stand-in should never wedge a test
            self._error(f"ERR {exc}")


    def _eval(self, args: list[str]) -> None:
        """Only the rate-counter script, recognised by its text.

        Returns a *bare* error for anything else, which is what lets the client's
        no-script fallback (`INCR` + `PEXPIRE`) be tested by pointing the same code
        at a server that refuses `EVAL`.
        """
        script = args[1]
        if "INCR" not in script.upper():
            self._error("NOSCRIPT No matching script. Please use EVAL.")
            return
        if "--no-eval" in _FLAGS:
            self._error("NOSCRIPT No matching script. Please use EVAL.")
            return
        key = args[3]
        ttl_ms = int(args[4])
        cur = _get(key) or "0"
        n = int(cur) + 1
        entry = STORE.get(key)
        if n == 1 or not entry or entry[1] <= _now():
            _set(key, str(n), ttl_ms)
        else:
            STORE[key] = (str(n), entry[1])
        _, exp = STORE[key]
        left = max(0, int((exp - _now()) * 1000)) if exp else ttl_ms
        self._array([n, left])


_FLAGS: set[str] = set()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6390)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-eval", action="store_true", help="refuse EVAL so the fallback path is exercised")
    ap.add_argument("--stats", action="store_true", help="print counters on exit")
    args = ap.parse_args()
    if args.no_eval:
        _FLAGS.add("--no-eval")
    srv = Server((args.host, args.port), Handler)
    print(json.dumps({"ok": True, "fake_redis": True, "port": args.port,
                      "eval": not args.no_eval, "note": "not a Redis — see the module docstring"}))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if args.stats:
            print(json.dumps(STATS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

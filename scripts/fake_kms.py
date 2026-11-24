#!/usr/bin/env python3
"""
A stand-in KMS that implements the two-operation protocol of `app/kms.py`.

**This is not a KMS.** It is a local HTTP server that holds a KEK in memory and
answers `wrap`/`unwrap` over HTTP, so that the `http` adapter in the envelope can be
proved end to end — request shape, response shape, a refusal, an unreachable host —
without a vendor credential and without a claim that a vendor was integrated. That
distinction is the same one `docs/15` makes about the Neon Functions: what is built
is proved locally, and what is not deployed is labelled as not deployed.

    python3 scripts/fake_kms.py --port 5455            # serves until killed
    python3 scripts/fake_kms.py --port 5455 --deny     # refuses every unwrap
    python3 scripts/fake_kms.py --port 5455 --require-token tok

Protocol (see `app/kms.py` → `HttpKek`):

    POST /         {"op":"wrap",   "key_id":"…", "plaintext_b64":"…", "aad":"…"}
                →  {"ciphertext":"…"}
    POST /         {"op":"unwrap", "key_id":"…", "ciphertext":"…",   "aad":"…"}
                →  {"plaintext_b64":"…"}

Anything else is a 400, and a missing/wrong bearer token is a 403 — a KMS that
answers questions from anyone is a KMS that hands out the key.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import aead  # noqa: E402


class FakeKms:
    def __init__(self, kek: bytes, token: str = "", deny: bool = False, key_id: str = "fake-kms-key"):
        self.kek = kek
        self.token = token
        self.deny = deny
        self.key_id = key_id
        self.calls: list[dict] = []

    def handle(self, payload: dict) -> tuple[int, dict]:
        op = payload.get("op")
        self.calls.append({"op": op, "key_id": payload.get("key_id", "")})
        if self.deny:
            return 403, {"error": "denied by policy"}
        if op == "wrap":
            raw = base64.b64decode(payload.get("plaintext_b64") or "")
            if len(raw) != 32:
                return 400, {"error": "only 32-byte keys"}
            return 200, {"ciphertext": aead.seal_bytes(raw, self.kek, payload.get("aad") or "")}
        if op == "unwrap":
            try:
                raw = aead.open_bytes(payload.get("ciphertext") or "", self.kek,
                                      payload.get("aad") or "")
            except Exception:
                return 400, {"error": "integrity"}
            return 200, {"plaintext_b64": base64.b64encode(raw).decode()}
        return 400, {"error": "unknown op"}


def make_handler(kms: FakeKms):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, status: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802 (http.server's spelling)
            if kms.token:
                expect = "Bearer " + kms.token
                if not hmac.compare_digest(self.headers.get("Authorization", ""), expect):
                    return self._json(403, {"error": "bad token"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._json(400, {"error": "not json"})
            status, body = kms.handle(payload)
            self._json(status, body)

        def log_message(self, *args) -> None:  # keep a proof's output readable
            return

    return Handler


def serve(port: int = 0, kek: bytes | None = None, token: str = "", deny: bool = False,
          key_id: str = "fake-kms-key") -> tuple[ThreadingHTTPServer, FakeKms, int]:
    kms = FakeKms(kek or os.urandom(32), token=token, deny=deny, key_id=key_id)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(kms))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, kms, server.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="A stand-in KMS (not a KMS).")
    ap.add_argument("--port", type=int, default=5455)
    ap.add_argument("--token", default="")
    ap.add_argument("--deny", action="store_true")
    ap.add_argument("--kek-file", default="")
    args = ap.parse_args()
    kek = os.urandom(32)
    if args.kek_file:
        kek = bytes.fromhex(json.loads(Path(args.kek_file).read_text())["kek"])
    server, kms, port = serve(args.port, kek=kek, token=args.token, deny=args.deny)
    print(f"fake KMS on http://127.0.0.1:{port} · key_id {kms.key_id} · "
          f"{'DENYING' if args.deny else 'wrapping/unwrapping'} · token {bool(args.token)}")
    print("this is a stand-in for a KMS, not a KMS. Ctrl-C to stop.")
    try:
        while True:
            for call in kms.calls[-5:]:
                print("  call:", call)
            kms.calls.clear()
            threading.Event().wait(5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())

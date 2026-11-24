#!/usr/bin/env python3
"""
Realtor Estates — Native CLI `stdio` MCP Server (`v1.37.0`).

Exposes the governed Realtor Estates Model Context Protocol server over
newline-delimited JSON-RPC 2.0 `stdin`/`stdout` for Claude Desktop, Cursor,
Windsurf, or Salesforce `zana` fleets, plus `--doctor` JSON readiness checks.

Usage:
    python3 scripts/mcp_stdio.py --profile curated
    python3 scripts/mcp_stdio.py --doctor
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import mcp_platform as mcp_mod  # noqa: E402
from app.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Realtor Estates Governed MCP stdio server")
    ap.add_argument("--profile", default=os.environ.get("RE_MCP_PROFILE", "curated"), choices=mcp_mod.MCP_PROFILES)
    ap.add_argument("--tenant-slug", default=os.environ.get("RE_MCP_TENANT", "sunrise"))
    ap.add_argument("--user-email", default=os.environ.get("RE_MCP_USER", "owner@realtorestates.app"))
    ap.add_argument("--store-path", default=os.environ.get("RE_STORE_PATH", ""))
    ap.add_argument("--doctor", action="store_true", help="Print JSON MCP readiness diagnostic and exit")
    args = ap.parse_args()

    store = Store(args.store_path) if args.store_path else Store()
    tenant = next((t for t in store.db["tenants"] if t.get("slug") == args.tenant_slug), None) or store.db["tenants"][0]
    tid = tenant["id"]
    user = next((u for u in store.db["users"] if u.get("tenant_id") == tid and u.get("email") == args.user_email), None)
    if not user:
        user = next((u for u in store.db["users"] if u.get("tenant_id") == tid), {"id": "usr_mcp", "email": "mcp@local", "name": "MCP Operator", "role": "viewer"})

    if args.doctor:
        sys.stdout.write(json.dumps(mcp_mod.mcp_doctor(store, tenant, user["role"])) + "\n")
        sys.stdout.flush()
        return 0

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            rpc = json.loads(line)
        except Exception:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = mcp_mod.handle_mcp_rpc(store, tenant, user, user["role"], rpc, profile=args.profile)
        # JSON-RPC notifications without id do not require a response frame
        if "id" in rpc:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

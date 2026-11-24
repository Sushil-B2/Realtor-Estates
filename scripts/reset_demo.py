#!/usr/bin/env python3
"""
Put the demo back (gap G7, docs/26).

    python3 scripts/reset_demo.py                       # report only, changes nothing
    python3 scripts/reset_demo.py --purge               # delete rows a proof wrote
    python3 scripts/reset_demo.py --purge --legacy-prefix "Proof "

What it does:

1. asks `GET /api/v1/demo/hygiene` what is on the workspace and what of it carries
   a `proof_run` stamp;
2. prints the runs, the counts per collection, and — if you asked for a legacy
   prefix — what it would match, **before** anything is deleted;
3. with `--purge`, calls `POST /api/v1/demo/proof-rows/purge` (owner only, slug
   typed out in the body) and prints what went;
4. sweeps ended session rows (`POST /api/v1/demo/sessions/sweep`) — a login is a
   row now, and every script that signs in leaves one (docs/28);
5. signs itself out, for the same reason.

The default is a dry run on purpose. A demo can be reseeded; a prospect's first
impression cannot.

`--legacy-prefix` exists for rows written *before* the stamp existed: the proofs
that made the original mess could not mark it. It is a heuristic and the script
says so — a name beginning with "Proof " is probably a proof's, and if it is not,
the deletion is not recoverable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = os.environ.get("RE_BASE_URL", "http://127.0.0.1:5433")
OWNER_EMAIL = os.environ.get("RE_OWNER_EMAIL", "owner@realtorestates.app")
OWNER_PASSWORD = os.environ.get("RE_OWNER_PASSWORD", "Demo@12345!")


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = ""

    def call(self, method: str, path: str, body=None):
        headers = {"Accept": "application/json", "User-Agent": "reset-demo"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except Exception:
                return e.code, {"_raw": raw[:300].decode("utf-8", "replace")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--purge", action="store_true", help="actually delete (default: report only)")
    ap.add_argument("--legacy-prefix", default="",
                    help='also delete pre-stamp rows whose title/name/email starts with one of these '
                         '(comma-separated, e.g. "Proof ,Rival")')
    ap.add_argument("--legacy-since", type=int, default=0,
                    help="also delete payments and invoices created at or after this unix time "
                         "(the blunt instrument, for money rows that carry no name)")
    args = ap.parse_args()

    c = Client(args.base)
    status, body = c.call("POST", "/api/v1/auth/login", {"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    if status != 200:
        print(f"login failed: {status} {body}")
        return 1
    c.token = body["token"]

    print(f"=== demo hygiene · {args.base} ===")
    path = "/api/v1/demo/hygiene"
    qs = []
    if args.legacy_prefix:
        qs.append("legacy_prefix=" + urllib.parse.quote(args.legacy_prefix))
    if args.legacy_since:
        qs.append(f"legacy_since={int(args.legacy_since)}")
    if qs:
        path += "?" + "&".join(qs)
    status, hygiene = c.call("GET", path)
    if status != 200:
        print(f"hygiene failed: {status} {hygiene}")
        return 1

    tenant = hygiene["tenant"]
    print(f"workspace: {tenant['name']} ({tenant['slug']})")
    print("\nrows by collection")
    for coll, n in sorted(hygiene["rows"].items(), key=lambda kv: -kv[1]):
        print(f"  {coll:<20} {n:>4}")

    print("\nproof-stamped rows")
    if not hygiene["total"]:
        print("  none — every row on this workspace is either real work or predates the stamp")
    else:
        print(f"  {hygiene['total']} row(s) from {len(hygiene['runs'])} run(s)")
        for coll, n in sorted(hygiene["proof_rows"].items(), key=lambda kv: -kv[1]):
            print(f"    {coll:<20} {n:>4}")
        for run in hygiene["runs"][:10]:
            print(f"    run {run['run']:<28} {run['rows']:>4} row(s)")

    if args.legacy_prefix:
        matches = hygiene.get("legacy_matches") or {}
        print(f'\nlegacy prefix "{args.legacy_prefix}" would match (heuristic, not a stamp)')
        if not matches:
            print("  nothing")
        for coll, n in sorted(matches.items(), key=lambda kv: -kv[1]):
            print(f"    {coll:<20} {n:>4}")
    if args.legacy_since:
        dated = hygiene.get("legacy_dated") or {}
        print(f"\nmoney rows created at or after {args.legacy_since} would match (blunt, by date)")
        if not dated:
            print("  nothing")
        for coll, n in sorted(dated.items(), key=lambda kv: -kv[1]):
            print(f"    {coll:<20} {n:>4}")

    if not args.purge:
        print("\ndry run — nothing was deleted. Re-run with --purge to put the demo back.")
        if not args.legacy_prefix:
            print('(add --legacy-prefix "Proof " to also remove rows written before the stamp)')
        return 0

    if args.legacy_since:
        print(f"\n!! every payment and invoice created at or after {args.legacy_since} will be deleted,")
        print("   stamped or not. If a real payment landed after that moment, it goes too.")
    if args.legacy_prefix:
        print(f'\n!! legacy prefix "{args.legacy_prefix}" is a heuristic: any row whose title, name, e-mail,')
        print("   unit number or firm starts with it will be deleted, stamped or not. This cannot be undone.")

    body = {"confirm": tenant["slug"], "legacy_prefix": args.legacy_prefix}
    if args.legacy_since:
        body["legacy_since"] = int(args.legacy_since)
    status, purged = c.call("POST", "/api/v1/demo/proof-rows/purge", body)
    if status != 200:
        print(f"\npurge failed: {status} {json.dumps(purged)[:300]}")
        return 1
    print(f"\nremoved {purged['total_removed']} row(s)")
    for label, counts in (("proof", purged["removed"]), ("legacy", purged["legacy_removed"])):
        for coll, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {label:<7} {coll:<20} {n:>4}")
    if purged["runs"]:
        print(f"  runs cleared: {', '.join(purged['runs'])}")
    print("\nThe audit chain was not touched; the purge itself is an audit entry.")
    sweep = c.call("POST", "/api/v1/demo/sessions/sweep", {})
    if sweep[0] == 200:
        print(f"sessions swept: {sweep[1].get('removed', 0)} ended row(s) deleted "
              f"({sweep[1].get('live', 0)} still signed in)")
    # this tool signs in to do its work; leaving its own session behind is the same
    # drift it exists to clean up (docs/28)
    c.call("POST", "/api/v1/auth/logout", {})
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Prune throwaway accounts created by the security harnesses.

The redline and audit harnesses sign up disposable tenants to test lockout,
weak-password rejection and cross-tenant isolation. Those signups are real
accounts: an owner user, a tenant, and any documents created under it. Nothing
deleted them, so a database that has been audited five times contains five
dormant owner accounts whose passwords nobody knows (SEN-009).

This script finds and removes them, deliberately and narrowly:

    email matches  lockout-probe-*@example.invalid
                   probe<n>@example.com
                   sentinel-*@example.invalid
                   redline-*@example.invalid

It refuses to touch any tenant that also contains a non-probe user, so a real
signup that happens to look like a probe cannot take data with it.

    python3 scripts/prune_probe_accounts.py            # report only
    python3 scripts/prune_probe_accounts.py --apply    # delete
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import db  # noqa: E402
from app import config as cfg  # noqa: E402

PROBE_EMAIL = re.compile(
    r"^(lockout-probe-?\d*|probe\d+|sentinel-[\w.-]*|redline-[\w.-]*)@example\.(invalid|com)$",
    re.IGNORECASE,
)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually delete (default is a report)")
    args = ap.parse_args(argv[1:])

    if db.psycopg is None:
        print("psycopg is not installed")
        return 2
    dsn = cfg.get("RE_ADMIN_DATABASE_URL", "") or db.dsn()
    if not dsn:
        print("no database configured")
        return 2

    with db.psycopg.connect(db.normalise_dsn(dsn), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select doc->>'email', tenant_id, id
                from re_documents
                where collection = 'users'
                order by created_at asc
            """)
            users = cur.fetchall()
            probe_users = [u for u in users if PROBE_EMAIL.match(u[0] or "")]
            keep_tenants = {u[1] for u in users if not PROBE_EMAIL.match(u[0] or "")}
            targets = [u for u in probe_users if u[1] not in keep_tenants]
            skipped = [u for u in probe_users if u[1] in keep_tenants]

            print(f"users scanned     : {len(users)}")
            print(f"probe users found  : {len(probe_users)}")
            print(f"tenants to prune   : {len({t[1] for t in targets})}")
            for email, tid, uid in targets:
                print(f"  - {email}  ({tid})")
            for email, tid, _uid in skipped:
                print(f"  ! kept {email}: tenant {tid} also holds a real user")

            if not args.apply:
                print("report only — re-run with --apply to delete")
                return 0

            tenant_ids = sorted({t[1] for t in targets})
            docs = 0
            if tenant_ids:
                cur.execute("delete from re_documents where tenant_id = any(%s)", (tenant_ids,))
                docs = cur.rowcount
            conn.commit()
            print(json.dumps({"tenants_removed": len(tenant_ids), "documents_removed": docs}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

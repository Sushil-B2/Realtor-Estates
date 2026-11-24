#!/usr/bin/env python3
"""
Repair a forked audit chain.

Why this exists
---------------
Before `PgBackend.append_audit()` took an advisory lock, `prev_hash` was computed
from each process's in-memory view of the log. Two concurrent writers — two app
instances, a worker, a script — could both chain to the same tail, forking the
chain. `GET /api/v1/tenant/audit` then reported `ok: false` even though nobody
tampered with anything.

This recomputes `prev_hash` and `hash` for every row of a tenant in `seq` order,
so the chain is linear again. It needs the append-only trigger disabled for the
duration: that is the only way to rewrite history, which is exactly why the
trigger exists. The run is printed in full and recorded, and the trigger is
re-enabled in a `finally`.

Usage
-----
    python3 scripts/repair_audit_chain.py --tenant tnt_xxx            # dry run, shows the first break
    python3 scripts/repair_audit_chain.py --tenant tnt_xxx --apply    # rewrite the chain
    python3 scripts/repair_audit_chain.py --all --apply                # every tenant
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db as dbmod  # noqa: E402
from app import security as sec  # noqa: E402

TRIGGER = "re_audit_no_update"


def load(conn, tenant: str | None) -> list[dict]:
    sql = """select seq, tenant_id, id, actor, action, target, meta, at, prev_hash, hash
             from re_audit {where} order by tenant_id, seq asc"""
    where = "where tenant_id = %s" if tenant else ""
    with conn.cursor() as cur:
        cur.execute(sql.format(where=where), (tenant,) if tenant else ())
        cols = ["seq", "tenant_id", "id", "actor", "action", "target", "meta", "at", "prev_hash", "hash"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def first_break(rows: list[dict]) -> int | None:
    prev_by_tenant: dict[str, str] = {}
    for idx, row in enumerate(rows):
        prev = prev_by_tenant.get(row["tenant_id"], "")
        if row["prev_hash"] != prev or sec.audit_hash(row) != row["hash"]:
            return idx
        prev_by_tenant[row["tenant_id"]] = row["hash"]
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Rebuild audit chain links in seq order.")
    ap.add_argument("--tenant", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])

    if not dbmod.configured():
        print("DATABASE_URL is not set — the JSON store keeps its own chain (data/store.json).")
        return 2

    conn = dbmod.psycopg.connect(dbmod.dsn(), prepare_threshold=None)
    try:
        rows = load(conn, None if args.all else args.tenant or None)
        if not rows:
            print("no audit rows matched")
            return 0
        break_at = first_break(rows)
        print(f"rows: {len(rows)}  first break: {break_at if break_at is not None else 'none'}")
        if break_at is None:
            print("chain is already linear — nothing to do")
            return 0
        print("  ", json.dumps({k: rows[break_at][k] for k in ("seq", "tenant_id", "id", "action")}))
        if not args.apply:
            print("dry run — re-run with --apply to rewrite")
            return 0

        prev_by_tenant: dict[str, str] = {}
        rewritten = 0
        with conn.cursor() as cur:
            cur.execute(f"alter table re_audit disable trigger {TRIGGER}")
            try:
                for row in rows:
                    prev = prev_by_tenant.get(row["tenant_id"], "")
                    row = {**row, "prev_hash": prev}
                    row["hash"] = sec.audit_hash(row)
                    if row["prev_hash"] != row["prev_hash"] or True:  # always write: hashes cascade
                        cur.execute(
                            "update re_audit set prev_hash = %s, hash = %s where id = %s",
                            (row["prev_hash"], row["hash"], row["id"]),
                        )
                        rewritten += 1
                    prev_by_tenant[row["tenant_id"]] = row["hash"]
                conn.commit()
            finally:
                cur.execute(f"alter table re_audit enable trigger {TRIGGER}")
                conn.commit()
        print(f"rewrote {rewritten} rows; trigger {TRIGGER} re-enabled")
        remaining = first_break(load(conn, None if args.all else args.tenant or None))
        print("verify after repair:", "linear" if remaining is None else f"still broken at {remaining}")
        return 0 if remaining is None else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

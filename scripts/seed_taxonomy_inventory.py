#!/usr/bin/env python3
"""
Add the multi-category taxonomy inventory to an existing demo database.

    python3 scripts/seed_taxonomy_inventory.py --dry-run
    python3 scripts/seed_taxonomy_inventory.py

`Store.seed()` only runs on an empty database, so a demo Postgres that was seeded
before the taxonomy existed never gains the twelve listings that make the five
categories real. This script inserts the missing ones for the demo tenant,
keyed by title, and touches nothing else: it never overwrites an edited row,
never deletes, and never runs the seeder's users/contacts/deals.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import store as store_mod  # noqa: E402
from app import taxonomy as tax  # noqa: E402

DEMO_TENANT_EMAIL = "owner@realtorestates.app"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tenant-email", default=DEMO_TENANT_EMAIL)
    args = ap.parse_args()

    st = store_mod.Store()
    print(f"store backend: {st.backend_name}")
    owner = next((u for u in st.db["users"] if u.get("email") == args.tenant_email), None)
    if not owner:
        print(f"scaffold user {args.tenant_email} not found — nothing to do")
        return 1
    tid = owner["tenant_id"]

    existing = {(p.get("title") or "").strip() for p in st.db["properties"] if p.get("tenant_id") == tid}
    wanted = store_mod.taxonomy_inventory(tid)
    added, skipped = [], []
    for row in wanted:
        if row["title"] in existing:
            skipped.append(row["title"])
            continue
        added.append(row)
        if not args.dry_run:
            st.insert("properties", tid, {k: v for k, v in row.items() if k != "id"}, prefix="prp")

    # legacy rows upgraded in place so an old plot is not the one row without a category
    upgraded = 0
    for prop in list(st.db["properties"]):
        if prop.get("tenant_id") != tid or prop.get("category"):
            continue
        patched = tax.hydrate(dict(prop))
        upgraded += 1
        if not args.dry_run:
            st.update("properties", tid, prop["id"], {
                "category": patched["category"], "type": patched["type"],
                "deal_kind": patched["deal_kind"], "area": patched["area"],
                "pricing": patched["pricing"], "attributes": patched["attributes"],
            })

    print(f"tenant {tid}: {len(wanted)} catalogue rows, {len(added)} to insert, {len(skipped)} already present")
    for row in added:
        area = row.get("area") or {}
        print(f"  + {row['title']}  [{row['category']}/{row['type']} {row['deal_kind']}, "
              f"{area.get('value')} {area.get('unit')}]")
    print(f"legacy rows upgraded with a category: {upgraded}")
    if args.dry_run:
        print("dry run — nothing written")
    counts: dict[str, int] = {}
    for prop in st.db["properties"]:
        if prop.get("tenant_id") == tid:
            counts[tax.hydrate(dict(prop))["category"]] = counts.get(tax.hydrate(dict(prop))["category"], 0) + 1
    print("category counts now:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

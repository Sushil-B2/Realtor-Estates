#!/usr/bin/env python3
"""
Upgrade a demo database whose automations predate the runtime (`docs/23`).

    python3 scripts/migrate_demo_automations.py --dry-run
    python3 scripts/migrate_demo_automations.py

Before v1.11.0 the seeded automations carried **string** steps — `"consent.validate"`
`"whatsapp.send_template(intro_v6)"` — which described an intent that no executor
could run. `Store.seed()` only runs on an empty database, so a demo Postgres seeded
back then keeps those rows and the runtime correctly refuses them ("Unknown step
type"). This script replaces them with the equivalent playbook, keyed by a name
match, and installs any playbook the tenant does not have.

It never deletes an automation a human created: anything unmatched is left alone and
reported, so the operator decides.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import automations as autm  # noqa: E402
from app import store as store_mod  # noqa: E402

DEMO_TENANT_EMAIL = "owner@realtorestates.app"

# legacy name fragment → playbook slug
NAME_MAP = [
    ("portal lead", "instant-response"),
    ("doc pack", "visit-doc-pack"),
    ("stale", "stale-lead-reengagement"),
    ("reminder", "visit-reminder-24h"),
    ("no-show", "no-show-recovery"),
    ("hold", "hold-expiry-nudge"),
    ("unanswered", "unanswered-escalation"),
]


def playbook_for(name: str) -> str | None:
    lowered = (name or "").lower()
    for fragment, slug in NAME_MAP:
        if fragment in lowered:
            return slug
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tenant-email", default=DEMO_TENANT_EMAIL)
    args = ap.parse_args()

    st = store_mod.Store()
    print(f"store backend: {st.backend_name}")
    owner = next((u for u in st.db["users"] if u.get("email") == args.tenant_email), None)
    if not owner:
        print(f"{args.tenant_email} not found — nothing to do")
        return 1
    tid = owner["tenant_id"]
    rows = st.all("automations", tid)
    print(f"tenant {tid}: {len(rows)} automation(s)")

    legacy = [a for a in rows if any(not isinstance(s, dict) for s in (a.get("steps") or []))]
    print(f"legacy (string steps): {len(legacy)}")
    changed = 0
    migrated: list[dict] = []
    for automation in legacy:
        slug = playbook_for(automation.get("name", ""))
        if not slug:
            print(f"  ! left alone (no playbook equivalent): {automation.get('name')}")
            continue
        doc = autm.install_playbook(slug)
        patch = {"steps": doc["steps"], "trigger": doc["trigger"], "playbook": slug,
                 "compliance_gate": doc["compliance_gate"], "migrated_from": "string-steps"}
        print(f"  → {automation.get('name')}  ⇒  {slug} ({len(doc['steps'])} typed steps)")
        changed += 1
        migrated.append({**automation, "migrated_slug": slug})
        if not args.dry_run:
            st.update("automations", tid, automation["id"], patch)

    installed = {a.get("playbook") for a in st.all("automations", tid)}
    if args.dry_run:  # account for the writes we just described
        installed |= {automation["migrated_slug"] for automation in migrated
                      if automation.get("migrated_slug")}
    added = []
    for book in autm.PLAYBOOKS:
        if book["slug"] in installed:
            continue
        added.append(book["slug"])
        print(f"  + install {book['slug']}")
        if not args.dry_run:
            st.insert("automations", tid, {**autm.install_playbook(book["slug"]), "runs": 0}, prefix="aut")

    # Two automations on one trigger message the same buyer twice — the frequency
    # cap stops a flood, not a duplicate. Keeping one per playbook is the honest
    # state of a demo fleet, and the duplicate is paused rather than deleted so a
    # human can see what happened.
    paused = 0
    fleet = st.all("automations", tid)
    by_book: dict[str, list[dict]] = {}
    for automation in fleet:
        if automation.get("playbook"):
            by_book.setdefault(automation["playbook"], []).append(automation)
    for slug, group in by_book.items():
        if len(group) < 2:
            continue
        keep = sorted(group, key=lambda a: (bool(a.get("migrated_from")),
                                            a.get("created_at") or 0))[0]
        for automation in group:
            if automation["id"] == keep["id"]:
                continue
            print(f"  - pause duplicate {automation.get('name')!r} ({slug}) — kept {keep.get('name')!r}")
            paused += 1
            if not args.dry_run:
                st.update("automations", tid, automation["id"],
                          {"enabled": False, "last_run": automation.get("last_run"),
                           "paused_reason": f"duplicate of {keep.get('name')} ({slug})"})

    print(f"\n{changed} migrated · {len(added)} installed · {paused} duplicate(s) paused" + (" (dry run — nothing written)" if args.dry_run else ""))
    if not args.dry_run:
        remaining = [a for a in st.all("automations", tid)
                     if any(not isinstance(s, dict) for s in (a.get("steps") or []))]
        print(f"automations still holding string steps: {len(remaining)}"
              + (f" → {[a.get('name') for a in remaining]}" if remaining else ""))
        fleet = st.all("automations", tid)
        print(f"fleet now: {len(fleet)} automations, "
              f"{sum(1 for a in fleet if a.get('enabled'))} enabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())

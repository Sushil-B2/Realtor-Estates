#!/usr/bin/env python3
"""
Realtor Estates — master-key rotation.

Rotating the root key is not "edit .env and restart": every encrypted field in
the store was sealed under a key derived from the old root. This script does the
full migration, offline, with a backup and a verification pass.

What it does
------------
1. Reads the store and decrypts PII with the OLD key (from --old-master, or the
   current RE_MASTER_KEY / data/keys.json).
2. Derives the NEW keyring, re-seals `phone_enc` / `email_enc`, recomputes both
   blind indexes (phone hash and email hash — they are keyed too).
3. Writes `data/store.pre-rotation.<ts>.json` (mode 0600) as a rollback point.
4. Atomically replaces the store, then re-reads it and verifies every field
   decrypts under the new key. Any failure restores the backup and exits 1.

What it does NOT do
-------------------
* It does not rotate provider credentials (OpenAI/WhatsApp/etc.) — that is a
  vendor-console action; see docs/11-SECRETS-MANAGEMENT.md §5 step 6.
* It does not invalidate live sessions unless you restart the server (session
  tokens are signed with the derived session key, so a new root invalidates
  every token by construction).
* It cannot recover data if the OLD key is already lost. Back up first.

Usage
-----
    python3 scripts/rotate_keys.py --new-master $(python3 -c "import secrets;print(secrets.token_hex(32))")
    python3 scripts/rotate_keys.py --new-master <new> --old-master <old>   # explicit old key
    python3 scripts/rotate_keys.py --dry-run --new-master <new>            # count only

Exit codes: 0 rotated · 1 failed and rolled back · 2 refused to start.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import security as sec  # noqa: E402  (needs config loaded)

DATA_DIR = Path(os.environ.get("RE_DATA_DIR", str(ROOT / "data")))
STORE = DATA_DIR / "store.json"
KEY_FILE = Path(os.environ.get("RE_KEY_FILE", str(DATA_DIR / "keys.json")))
PII_FIELDS = ("phone_enc", "email_enc")


def load_store() -> dict:
    if not STORE.exists():
        print(f"rotate: no store at {STORE} — nothing to migrate (fresh install?)")
        raise SystemExit(2)
    with STORE.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_store(db: dict) -> None:
    tmp = STORE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False)
    os.replace(tmp, STORE)


def derive_ring(master_hex: str | None) -> dict[str, bytes]:
    """None ⇒ the local development keyring file."""
    if master_hex:
        from app import config as cfg

        master = cfg._decode_master(master_hex)
        if len(master) < 32:
            print("rotate: master key must decode to at least 32 bytes", file=sys.stderr)
            raise SystemExit(2)
        return {name: cfg.derive_key(name, master) for name in cfg.KEY_NAMES}
    if not KEY_FILE.exists():
        print("rotate: no --old-master and no keyring file — cannot decrypt", file=sys.stderr)
        raise SystemExit(2)
    raw = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    return {k: bytes.fromhex(v) for k, v in raw.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate the master key and re-encrypt tenant PII")
    ap.add_argument("--new-master", required=False, help="new master key (hex); omit to generate one")
    ap.add_argument("--old-master", default=None, help="current master key; defaults to the active keyring")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app import config as cfg

    new_hex = args.new_master or cfg.generate_master_key()
    if cfg.is_placeholder(new_hex):
        print("rotate: refusing to use a placeholder as the new key", file=sys.stderr)
        return 2

    old_ring = derive_ring(args.old_master if args.old_master is not None else (cfg.get("RE_MASTER_KEY") or None))
    new_ring = derive_ring(new_hex)

    if old_ring["field_enc"] == new_ring["field_enc"]:
        print("rotate: new key derives the same field key as the old one — aborting (nothing to do?)", file=sys.stderr)
        return 2

    db = load_store()
    contacts = db.get("contacts", [])
    tenants = {t["id"]: t for t in db.get("tenants", [])}
    counters = {"contacts": 0, "fields": 0, "skipped": 0}

    for c in contacts:
        tid = c.get("tenant_id", "")
        if tid not in tenants:
            counters["skipped"] += 1
            continue
        for field in PII_FIELDS:
            blob = c.get(field) or ""
            if not blob:
                continue
            try:
                plain = sec.unseal(blob, aad=tid, key=old_ring["field_enc"])
            except Exception:
                print(f"rotate: cannot decrypt {field} for {c.get('id')} under the old key — aborting", file=sys.stderr)
                return 1
            c[field] = sec.seal(plain, aad=tid, key=new_ring["field_enc"])
            counters["fields"] += 1
        # blind indexes are keyed too: recompute from the plaintext we just handled
        phone = sec.unseal(c.get("phone_enc", ""), aad=tid, key=new_ring["field_enc"])
        email = sec.unseal(c.get("email_enc", ""), aad=tid, key=new_ring["field_enc"])
        if phone:
            c["phone_hash"] = sec.blind_index(phone, key=new_ring["blind_index"], tenant_id=tid)
        if email:
            c["email_hash"] = sec.blind_index(email, key=new_ring["blind_index"], tenant_id=tid)
        counters["contacts"] += 1

    # Also re-seal encrypted tenant connector & inbound-webhook secrets (`docs/39`)
    for tid, t_doc in tenants.items():
        settings = (t_doc.get("settings") or {})
        for row in (settings.get("connector_profiles") or []):
            aad = f"{tid}:conn:{row.get('id')}"
            for sf in ("secret_enc", "secret2_enc"):
                if row.get(sf):
                    plain = sec.unseal(row[sf], aad=aad, key=old_ring["field_enc"])
                    row[sf] = sec.seal(plain, aad=aad, key=new_ring["field_enc"])
                    counters["fields"] += 1
        for hook in (settings.get("inbound_hooks") or []):
            aad = f"{tid}:inhook:{hook.get('id')}"
            if hook.get("secret_enc"):
                plain = sec.unseal(hook["secret_enc"], aad=aad, key=old_ring["field_enc"])
                hook["secret_enc"] = sec.seal(plain, aad=aad, key=new_ring["field_enc"])
                counters["fields"] += 1

    print(
        f"rotate: {counters['contacts']} contacts, {counters['fields']} encrypted fields re-sealed"
        + (f", {counters['skipped']} skipped (unknown tenant)" if counters["skipped"] else "")
    )
    if args.dry_run:
        print("rotate: dry run — store untouched")
        return 0

    backup = DATA_DIR / f"store.pre-rotation.{int(time.time())}.json"
    shutil.copy2(STORE, backup)
    os.chmod(backup, 0o600)
    print(f"rotate: backup at {backup.name} (mode 0600)")

    write_store(db)

    # verify: re-read from disk and decrypt every field under the new key
    check = json.loads(STORE.read_text(encoding="utf-8"))
    for c in check.get("contacts", []):
        for field in PII_FIELDS:
            if c.get(field):
                try:
                    sec.unseal(c[field], aad=c["tenant_id"], key=new_ring["field_enc"])
                except Exception:
                    print("rotate: verification failed — restoring backup", file=sys.stderr)
                    shutil.copy2(backup, STORE)
                    return 1

    print("rotate: verified — every field decrypts under the new key")
    print("\nNext steps (do not skip):")
    print("  1. Put the new key in .env as RE_MASTER_KEY (chmod 600), or your secret manager.")
    print("  2. Restart the server — all sessions are invalidated by design.")
    print("  3. python3 scripts/check_secrets.py")
    print("  4. Shred the backup once you are satisfied:  shred -u " + str(backup))
    print("     (the backup is encrypted under the OLD key — until it is destroyed the old key still matters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

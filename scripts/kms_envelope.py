#!/usr/bin/env python3
"""
KMS envelope — operate the wrapped root key. See `docs/30-KMS-ENVELOPE.md`.

    python3 scripts/kms_envelope.py status          # where the root comes from; no secrets
    python3 scripts/kms_envelope.py init            # wrap the CURRENT root (adopts it)
    python3 scripts/kms_envelope.py init --new      # mint a new root  (then: rotate_keys.py)
    python3 scripts/kms_envelope.py init --provider local   # dev KEK, refused in production
    python3 scripts/kms_envelope.py verify          # unwrap, derive, round-trip a probe
    python3 scripts/kms_envelope.py rewrap          # re-wrap the same root under the current KEK

The KEK is never an argument to this script: it comes from the environment
(`RE_KMS_KEK`, the `LoadCredential` shape) or from a KMS (`RE_KMS_URL`), because a
key on a command line is a key in `ps` and in shell history. `--provider local`
writes a development KEK to `data/kms-kek.json` (0600, git-ignored) and says out
loud that it is not a KMS.

Exit codes: 0 ok · 1 refused or failed · 2 misconfiguration.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config as cfg  # noqa: E402
from app import kms  # noqa: E402


def show(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser(description="Operate the KMS envelope (docs/30).")
    ap.add_argument("command", choices=("status", "init", "verify", "rewrap"))
    ap.add_argument("--provider", choices=kms.PROVIDERS, default="",
                    help="init only: which KEK provider to use")
    ap.add_argument("--new", action="store_true",
                    help="init only: mint a NEW root instead of adopting the current one")
    args = ap.parse_args()

    try:
        if args.command == "status":
            st = kms.status()
            show(st)
            if not st["envelope"]:
                print("\nthere is no envelope: the root is whatever RE_MASTER_KEY / the keyring holds")
            return 0

        if args.command == "verify":
            out = kms.verify()
            show(out)
            return 0 if out["unwrapped"] and out["roundtrip"] else 1

        if args.command == "rewrap":
            before = kms.load() or {}
            new = kms.rewrap()
            show({"kek_id_before": before.get("kek_id"), "kek_id_after": new.get("kek_id"),
                  "rotated_at": new.get("rotated_at"),
                  "same_root": True,
                  "note": "the root did not move, so nothing has to be re-encrypted. "
                          "If you changed the KEK, keep the old one in RE_KMS_KEK_PREVIOUS "
                          "until a restart has booted and decrypted, then delete it."})
            return 0

        # init
        if args.new:
            env = kms.init(secrets.token_bytes(kms.DEK_BYTES), create_local=args.provider == "local")
            show({"created": env["kek_id"], "provider": env["provider"], "path": str(kms.envelope_path()),
                  "root": "NEW",
                  "next": "the store is still sealed under the old root — run "
                          "scripts/rotate_keys.py, then restart. Until then every "
                          "encrypted field will fail to unseal."})
            return 0

        if args.provider == "local":
            if cfg.PRODUCTION:
                print("refused: the local KEK provider is a development convenience and "
                      "RE_ENV says production. Use RE_KMS_URL or RE_KMS_KEK.", file=sys.stderr)
                return 2
            os.environ.pop("RE_KMS_URL", None)
            os.environ.pop("RE_KMS_KEK", None)
        elif args.provider == "env" and not cfg.get("RE_KMS_KEK", ""):
            print("refused: --provider env needs RE_KMS_KEK in the environment "
                  "(LoadCredential, Docker secret, or a Vault agent render).", file=sys.stderr)
            return 2
        elif args.provider == "http" and not cfg.get("RE_KMS_URL", ""):
            print("refused: --provider http needs RE_KMS_URL (and usually RE_KMS_TOKEN).",
                  file=sys.stderr)
            return 2

        if not cfg.MASTER_KEY:
            print("refused: there is no RE_MASTER_KEY to adopt. Set one, or use --new "
                  "(which mints a root and then needs scripts/rotate_keys.py).", file=sys.stderr)
            return 2
        env = kms.init(create_local=args.provider == "local")
        root = kms.dek()
        show({
            "path": str(kms.envelope_path()),
            "provider": env["provider"],
            "kek_id": env["kek_id"],
            "root": "adopted (the one already in use — nothing was re-encrypted)",
            "root_matches": bool(root) and root == cfg.MASTER_KEY,
            "created_at": env["created_at"],
            "next": "restart the server; RE_MASTER_KEY is no longer read (keep it until you have "
                    "confirmed a boot and a decrypt)",
        })
        return 0
    except kms.EnvelopeError as exc:
        print(f"envelope: {exc.code}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

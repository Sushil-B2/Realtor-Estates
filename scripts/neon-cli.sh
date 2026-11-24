#!/usr/bin/env bash
# Run a Neon CLI command with the API key, without the key ever touching argv,
# the shell history, or the file system in plaintext beyond its 0600 home.
#
#   scripts/neon-cli.sh me
#   scripts/neon-cli.sh link --project-id broad-sound-07229115 --branch production
#   scripts/neon-cli.sh deploy --env .env.local
#   scripts/neon-cli.sh triggers list --output json
#
# Why a wrapper instead of `NEON_API_KEY=… npx neon …`:
#   * `NEON_API_KEY=…` in a shell line lands in history and in `ps` for the
#     duration of the call;
#   * `--api-key …` lands in argv, which every local user can read via /proc;
#   * this reads the key from a 0600 file into the process environment, and it
#     also honours an already-exported NEON_API_KEY (so CI can inject it).
#
# Set NEON_SHRED=1 to delete the credential file after the command succeeds —
# the default posture for a one-off deploy on a box you do not fully trust.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY_FILE="${NEON_KEY_FILE:-$ROOT/.secrets/neon-api-key}"

if [[ -z "${NEON_API_KEY:-}" ]]; then
  if [[ ! -f "$KEY_FILE" ]]; then
    echo "neon-cli: no credential. Add one at /keysetup.html, or export NEON_API_KEY." >&2
    exit 3
  fi
  # Strip the optional `NEON_API_KEY=` prefix; never echo the value.
  NEON_API_KEY="$(sed -n 's/^NEON_API_KEY=//p' "$KEY_FILE" | head -1)"
  if [[ -z "$NEON_API_KEY" ]]; then
    echo "neon-cli: $KEY_FILE exists but holds no NEON_API_KEY line." >&2
    exit 3
  fi
fi
export NEON_API_KEY

cd "$ROOT"
npx -y neon@latest "$@"
status=$?

if [[ "${NEON_SHRED:-0}" == "1" && $status -eq 0 ]]; then
  python3 - "$KEY_FILE" <<'PY'
import os, sys
p = sys.argv[1]
try:
    size = os.path.getsize(p)
    with open(p, "r+b") as fh:
        fh.write(b"\x00" * size); fh.flush(); os.fsync(fh.fileno())
    os.unlink(p)
    print("neon-cli: credential shredded (NEON_SHRED=1)")
except FileNotFoundError:
    pass
PY
fi
exit $status

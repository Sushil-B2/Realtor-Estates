#!/usr/bin/env bash
# Deploy the three Neon Functions and immediately prove them against the deployed
# URLs. One command, once `.secrets/neon-api-key` exists (/keysetup.html writes it).
#
#   scripts/deploy_functions.sh                     # link → plan → deploy
#   scripts/deploy_functions.sh --proof             # … then run the deployed proof
#   scripts/deploy_functions.sh --project-id X --branch production
#
# The key is read by scripts/neon-cli.sh from a 0600 file into the child process
# environment: never in argv, never in shell history, never printed here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${NEON_PROJECT_ID:-broad-sound-07229115}"
BRANCH="${NEON_BRANCH:-production}"
ENV_FILE="${NEON_ENV_FILE:-.env.local}"
PROOF=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --proof) PROOF=1; shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "deploy_functions: unknown argument $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

say "0/5 preflight"
if [[ -z "${NEON_API_KEY:-}" && ! -s "${NEON_KEY_FILE:-$ROOT/.secrets/neon-api-key}" ]]; then
  cat >&2 <<'EOF'
deploy_functions: no Neon credential.

The deploy needs a project-scoped API key (a `neon login` OAuth flow cannot be
completed from this environment). Two ways to supply it, both without exposing
the value to argv, history or the runtime env:

  1. open /keysetup.html on the running app, sign in as owner, paste the key
     (writes .secrets/neon-api-key, 0600, git-ignored); or
  2. export NEON_API_KEY in the shell that runs this script.

Then run this script again. Nothing else is blocking the deploy.
EOF
  exit 3
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "deploy_functions: $ENV_FILE not found — it is the env bundle handed to the functions" >&2
  exit 3
fi
printf '  project: %s\n  branch:  %s\n  env:     %s\n' "$PROJECT_ID" "$BRANCH" "$ENV_FILE"
printf '  functions: %s\n' "$(ls functions/*.ts | xargs -n1 basename | tr '\n' ' ')"
npx -y neon@latest --version | sed 's/^/  neon cli: /'

say "1/5 link project $PROJECT_ID (branch $BRANCH)"
scripts/neon-cli.sh link --project-id "$PROJECT_ID" --branch "$BRANCH"

say "2/5 plan — what would change remotely (read-only)"
scripts/neon-cli.sh config plan --env "$ENV_FILE"

say "3/5 deploy"
scripts/neon-cli.sh deploy --env "$ENV_FILE" --no-env-pull -y

say "4/5 functions and triggers, as the platform sees them"
scripts/neon-cli.sh functions list --output json || \
  echo "  (functions list unavailable on this CLI version — check the console)"
scripts/neon-cli.sh triggers list --output json || \
  echo "  (no triggers yet: create with scripts/neon-cli.sh triggers create --function-slug property-photos --bucket projectassets --prefix properties/)"

if [[ "$PROOF" == "1" ]]; then
  say "5/5 deployed-URL proof"
  if [[ -z "${PHOTOS_URL:-}" || -z "${WEBHOOK_URL:-}" || -z "${SWEEP_URL:-}" ]]; then
    FN_JSON="$(scripts/neon-cli.sh functions list --output json 2>/dev/null || echo '[]')"
    PHOTOS_URL="${PHOTOS_URL:-$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(next((x["invocation_url"] for x in d if x.get("slug")=="propertyphotos"),""))' "$FN_JSON")}"
    WEBHOOK_URL="${WEBHOOK_URL:-$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(next((x["invocation_url"] for x in d if x.get("slug")=="paymentswebhook"),""))' "$FN_JSON")}"
    SWEEP_URL="${SWEEP_URL:-$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(next((x["invocation_url"] for x in d if x.get("slug")=="compliancesweep"),""))' "$FN_JSON")}"
  fi
  python3 scripts/proof_deployed_functions.py \
    ${PHOTOS_URL:+--photos-url "$PHOTOS_URL"} \
    ${WEBHOOK_URL:+--webhook-url "$WEBHOOK_URL"} \
    ${SWEEP_URL:+--sweep-url "$SWEEP_URL"}
else
  say "5/5 proof skipped — re-run with --proof (and PHOTOS_URL/WEBHOOK_URL/SWEEP_URL if needed)"
fi

say "done"
echo "Record the deployed URLs in docs/15 §7 and close gap G3 in PLANS.md §9."

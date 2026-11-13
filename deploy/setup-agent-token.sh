#!/usr/bin/env bash
# setup-agent-token.sh — idempotent per-agent fleet token provisioning (Runner_Dashboard#3895 slice 5).
#
# Mints a bot token for a principal via the admin API (docs/agents/connect.md
# "Mint a Per-Agent Token") and writes it to the identity directory's
# agent-tokens/ folder, unless a non-empty token file is already there.
# Never prints or logs the token itself.
set -Eeuo pipefail

API_URL="${FLEET_API_URL:-http://127.0.0.1:8321}"
IDENTITY_DIR="${DASHBOARD_IDENTITY_DIR:-${HOME}/.config/runner-dashboard}"
TOKEN_NAME=""
EXPIRES_IN_DAYS=90
FORCE=0
DRY_RUN=0
PRINCIPAL=""

die() { echo "setup-agent-token: $*" >&2; exit 2; }
say() { echo "setup-agent-token: $*"; }

usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}"
    cat <<'EOF'

Usage: setup-agent-token.sh --principal <id> [options]

Options:
  --principal <id>            Principal id, e.g. agent-codex (required)
  --api-url <url>             Dashboard base URL (default: $FLEET_API_URL or http://127.0.0.1:8321)
  --identity-dir <dir>        Identity directory (default: $DASHBOARD_IDENTITY_DIR or ~/.config/runner-dashboard)
  --token-name <name>         Human-readable name recorded with the token (default: "<principal> on <hostname>")
  --expires-in-days <n>       Token lifetime in days (default: 90)
  --force                     Re-mint even if a token file already exists
  --dry-run                   Print the planned action without minting or writing
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --principal) PRINCIPAL="$2"; shift 2 ;;
        --api-url) API_URL="$2"; shift 2 ;;
        --identity-dir) IDENTITY_DIR="$2"; shift 2 ;;
        --token-name) TOKEN_NAME="$2"; shift 2 ;;
        --expires-in-days) EXPIRES_IN_DAYS="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "$PRINCIPAL" ]] || { usage; die "--principal is required"; }
[[ "$EXPIRES_IN_DAYS" =~ ^[0-9]+$ ]] || die "--expires-in-days must be a positive integer"
command -v curl >/dev/null 2>&1 || die "curl not found"
command -v python3 >/dev/null 2>&1 || die "python3 not found"

if [[ -z "$TOKEN_NAME" ]]; then
    TOKEN_NAME="${PRINCIPAL} on $(hostname 2>/dev/null || echo unknown-host)"
fi

TOKEN_DIR="${IDENTITY_DIR}/agent-tokens"
TOKEN_FILE="${TOKEN_DIR}/${PRINCIPAL}.token"

if [[ -s "$TOKEN_FILE" && "$FORCE" != "1" ]]; then
    say "token file already present for ${PRINCIPAL} (${TOKEN_FILE}); use --force to re-mint"
    exit 0
fi

if [[ "$DRY_RUN" == "1" ]]; then
    say "[dry-run] would POST ${API_URL}/api/admin/principals/${PRINCIPAL}/token and write ${TOKEN_FILE}"
    exit 0
fi

install -d -m 700 "$TOKEN_DIR"

RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

HTTP_STATUS="$(curl -sS --max-time 15 -o "$RESPONSE_FILE" -w '%{http_code}' \
    -X POST "${API_URL}/api/admin/principals/${PRINCIPAL}/token" \
    -H "X-Requested-With: XMLHttpRequest" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "expires_in_days": int(sys.argv[2])}))' "$TOKEN_NAME" "$EXPIRES_IN_DAYS")")" \
    || die "request to mint a token failed (network error)"

if [[ "$HTTP_STATUS" != "200" && "$HTTP_STATUS" != "201" ]]; then
    die "mint request for ${PRINCIPAL} failed with HTTP ${HTTP_STATUS}"
fi

TOKEN_VALUE="$(python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(1)
token = data.get("token")
if not token:
    sys.exit(1)
print(token)
' "$RESPONSE_FILE")" || die "mint response for ${PRINCIPAL} did not include a token"

TMP_TOKEN_FILE="$(mktemp "${TOKEN_DIR}/.${PRINCIPAL}.token.XXXXXX")"
printf '%s' "$TOKEN_VALUE" > "$TMP_TOKEN_FILE"
chmod 600 "$TMP_TOKEN_FILE"
mv -f "$TMP_TOKEN_FILE" "$TOKEN_FILE"
unset TOKEN_VALUE

say "wrote token for ${PRINCIPAL} to ${TOKEN_FILE}"

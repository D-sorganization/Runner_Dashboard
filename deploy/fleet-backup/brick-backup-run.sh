#!/usr/bin/env bash
# Scheduled Brick restic backup (#4010). Expects EnvironmentFile from systemd install.
set -Eeuo pipefail

ENV_FILE="${BRICK_RESTIC_ENV:-/etc/brick-restic/env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "env file not found: $ENV_FILE" >&2
    exit 2
fi

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE required}"
[[ -f "$RESTIC_PASSWORD_FILE" ]] || {
    echo "password file missing: $RESTIC_PASSWORD_FILE" >&2
    exit 2
}

if [[ "${RESTIC_DRY_RUN:-0}" == "1" ]]; then
    echo "RESTIC_DRY_RUN=1; refusing scheduled backup" >&2
    exit 2
fi

BACKUP_PATHS="${BACKUP_PATHS:-$HOME/actions-runners}"
# shellcheck disable=SC2086
exec restic --password-file "$RESTIC_PASSWORD_FILE" backup --json $BACKUP_PATHS

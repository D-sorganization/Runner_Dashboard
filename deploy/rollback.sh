#!/usr/bin/env bash
# deploy/rollback.sh -- Roll back the dashboard to a previous backup snapshot.
#
# Usage:
#   bash rollback.sh --list                   # List available backups
#   bash rollback.sh                          # Roll back to most recent backup
#   bash rollback.sh --to /path/to/backup     # Roll back to specific backup
#   bash rollback.sh --dry-run                # Preview rollback without executing
#
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
SERVICE="runner-dashboard"
LIST_ONLY=false
BACKUP_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)       LIST_ONLY=true; shift ;;
        --to)         BACKUP_PATH="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=true; shift ;;
        *) warn "Unknown option: $1"; shift ;;
    esac
done

require_dir "$DEPLOY_DIR"

if [[ "$LIST_ONLY" == "true" ]]; then
    info "Available backups:"
    ls -d "${DEPLOY_DIR}.bak."* 2>/dev/null | sort -r | head -20 \
        || warn "No backups found for $DEPLOY_DIR"
    exit 0
fi

if [[ -z "$BACKUP_PATH" ]]; then
    BACKUP_PATH=$(ls -d "${DEPLOY_DIR}.bak."* 2>/dev/null | sort -r | head -1 || true)
    [[ -n "$BACKUP_PATH" ]] || fail "No backup found. Run update-deployed.sh first."
    info "Auto-selected: $BACKUP_PATH"
fi

require_dir "$BACKUP_PATH"

info "Rolling back $DEPLOY_DIR to $BACKUP_PATH ..."
if ! dry_run "sync_dir $BACKUP_PATH $DEPLOY_DIR"; then
    sync_dir "$BACKUP_PATH" "$DEPLOY_DIR"
    ok "Files restored"
fi

info "Restarting $SERVICE ..."
if ! dry_run "sudo systemctl restart $SERVICE"; then
    sudo systemctl restart "$SERVICE"
    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE"; then
        ok "Service is running after rollback"
    else
        fail "Service failed to start after rollback"
    fi
fi
ok "Rollback complete."

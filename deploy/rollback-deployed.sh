#!/usr/bin/env bash
# rollback-deployed.sh — Restore dashboard from backup (issue #713)
# Usage:
#   deploy/rollback-deployed.sh [--backup-dir <path>] [--deploy-dir <path>]
#
# If --backup-dir is omitted, the most-recent backup under
# /var/backups/runner-dashboard/ is used.
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BACKUP_DIR=""
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
ROLLBACK_NOTIFY_URL="${ROLLBACK_NOTIFY_URL:-}"
SERVICE="runner-dashboard"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# Find most-recent backup if not specified
if [[ -z "$BACKUP_DIR" ]]; then
    BACKUP_DIR=$(ls -1dt /var/backups/runner-dashboard/* 2>/dev/null | head -1 || echo "")
fi

[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] || fail "No backup directory found"

info "Rolling back from $BACKUP_DIR to $DEPLOY_DIR..."
rsync -a --delete "$BACKUP_DIR/" "$DEPLOY_DIR/"
ok "Files restored"

info "Restarting $SERVICE after rollback..."
sudo systemctl restart "$SERVICE"
sleep 3
if sudo systemctl is-active --quiet "$SERVICE"; then
    ok "Service running after rollback"
else
    fail "Service failed to start after rollback"
fi

if [[ -n "$ROLLBACK_NOTIFY_URL" ]]; then
    curl -s -X POST "$ROLLBACK_NOTIFY_URL" \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"Runner Dashboard rollback triggered from $BACKUP_DIR\"}" \
        2>/dev/null || warn "Rollback notification failed"
fi

ok "Rollback complete"

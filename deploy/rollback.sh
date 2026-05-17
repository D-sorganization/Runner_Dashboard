#!/usr/bin/env bash
# deploy/rollback.sh -- Roll back the dashboard to a previous backup snapshot.
#
# Usage:
#   bash rollback.sh --list                   # List available backups with integrity status
#   bash rollback.sh                          # Roll back to most recent verified backup
#   bash rollback.sh --to /path/to/backup     # Roll back to specific backup
#   bash rollback.sh --dry-run                # Preview rollback without executing
#
# Exit codes:
#   0  — success
#   1  — failure (integrity check, health probe, DB migration, or service error)
#
set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
PORT="${PORT:-8321}"
SERVICE="runner-dashboard"
HEALTH_URL="http://localhost:${PORT}/api/health"
HEALTH_RETRIES="${HEALTH_RETRIES:-15}"
HEALTH_RETRY_SLEEP="${HEALTH_RETRY_SLEEP:-2}"
LIST_ONLY=false
BACKUP_PATH=""
SKIP_DB_MIGRATION="${SKIP_DB_MIGRATION:-false}"

# ─── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list)           LIST_ONLY=true; shift ;;
        --to)             BACKUP_PATH="$2"; shift 2 ;;
        --deploy-dir)     DEPLOY_DIR="$2"; shift 2 ;;
        --dry-run)        DRY_RUN=true; shift ;;
        --skip-db)        SKIP_DB_MIGRATION=true; shift ;;
        *) warn "Unknown option: $1"; shift ;;
    esac
done

require_dir "$DEPLOY_DIR"

# ─── Integrity helpers ─────────────────────────────────────────────────────────

# Compute integrity status for a backup directory.
# Returns "ok" if manifest exists and verifies, "missing" if no manifest, "fail" if mismatch.
_integrity_status() {
    local backup="$1"
    local manifest="${backup}.manifest.sha256"
    if [[ ! -f "$manifest" ]]; then
        echo "missing"
        return
    fi
    if (cd "$backup" && sha256sum -c "$manifest" --quiet 2>/dev/null); then
        echo "ok"
    else
        echo "fail"
    fi
}

# ─── List mode ────────────────────────────────────────────────────────────────

if [[ "$LIST_ONLY" == "true" ]]; then
    info "Available backups for $DEPLOY_DIR:"
    # fix: ls -d also matches *.manifest.sha256 files, which then fail the
    # require_dir check downstream. find -type d is the correct filter.
    backups=$(find "$(dirname "$DEPLOY_DIR")" -maxdepth 1 -type d -name "$(basename "$DEPLOY_DIR").bak.*" 2>/dev/null | sort -r || true)
    if [[ -z "$backups" ]]; then
        warn "No backups found for $DEPLOY_DIR"
        exit 0
    fi
    while IFS= read -r b; do
        ts="${b##*.bak.}"
        status=$(_integrity_status "$b")
        case "$status" in
            ok)      icon="ok" ;;
            missing) icon="?" ;;
            fail)    icon="FAIL" ;;
        esac
        version="unknown"
        if [[ -f "$b/VERSION" ]]; then
            version=$(cat "$b/VERSION")
        fi
        printf "  [%s]  %s  version=%s  integrity=%s\n" "$icon" "$ts" "$version" "$status"
    done <<< "$backups"
    exit 0
fi

# ─── Select backup ────────────────────────────────────────────────────────────

if [[ -z "$BACKUP_PATH" ]]; then
    BACKUP_PATH=$(find "$(dirname "$DEPLOY_DIR")" -maxdepth 1 -type d -name "$(basename "$DEPLOY_DIR").bak.*" 2>/dev/null | sort -r | head -1 || true)
    [[ -n "$BACKUP_PATH" ]] || fail "No backup found. Run update-deployed.sh first."
    info "Auto-selected: $BACKUP_PATH"
fi

require_dir "$BACKUP_PATH"

# ─── Integrity verification ───────────────────────────────────────────────────

MANIFEST="${BACKUP_PATH}.manifest.sha256"

if [[ ! -f "$MANIFEST" ]]; then
    warn "Backup is missing checksum manifest: $MANIFEST"
    warn "This backup was created before integrity tracking was added."
    warn "Proceeding without verification — use --skip-db to skip DB downgrade."
else
    info "Verifying backup integrity..."
    if ! dry_run "sha256sum -c $MANIFEST"; then
        if ! (cd "$BACKUP_PATH" && sha256sum -c "$MANIFEST" --quiet 2>&1); then
            fail "Backup integrity check FAILED: $MANIFEST — backup may be corrupt. Aborting rollback."
        fi
        ok "Integrity check passed"
    fi
fi

# ─── State directory snapshot ─────────────────────────────────────────────────

STATE_SNAPSHOT="${BACKUP_PATH}.state.tar.gz"
_STATE_DIRS=(
    "$HOME/.config/runner-dashboard"
    "$DEPLOY_DIR/config"
)
_STATE_FILES=(
    "$DEPLOY_DIR/.agent_remediation_state.json"
)

if [[ "$DRY_RUN" != "true" ]]; then
    info "Snapshotting state directories before rollback..."
    _snapshot_targets=()
    for d in "${_STATE_DIRS[@]}"; do
        [[ -d "$d" ]] && _snapshot_targets+=("$d")
    done
    for f in "${_STATE_FILES[@]}"; do
        [[ -f "$f" ]] && _snapshot_targets+=("$f")
    done

    if [[ ${#_snapshot_targets[@]} -gt 0 ]]; then
        tar czf "$STATE_SNAPSHOT" "${_snapshot_targets[@]}" 2>/dev/null \
            && ok "State snapshot: $STATE_SNAPSHOT" \
            || warn "State snapshot failed (non-fatal — continuing)"
    fi
fi

# ─── DB migration rollback ────────────────────────────────────────────────────

if [[ "$SKIP_DB_MIGRATION" != "true" ]]; then
    ALEMBIC_BIN="$DEPLOY_DIR/backend/.venv/bin/alembic"
    ALEMBIC_INI="$DEPLOY_DIR/backend/alembic.ini"

    if [[ -f "$ALEMBIC_BIN" && -f "$ALEMBIC_INI" ]]; then
        info "Rolling back DB migration (alembic downgrade -1)..."
        if ! dry_run "alembic downgrade -1 (cwd=$DEPLOY_DIR/backend)"; then
            if ! (cd "$DEPLOY_DIR/backend" && "$ALEMBIC_BIN" -c "$ALEMBIC_INI" downgrade -1); then
                fail "DB migration rollback failed. Aborting to avoid schema/code mismatch."
            fi
            ok "DB migration rolled back"
        fi
    else
        info "No alembic environment found in $DEPLOY_DIR/backend — skipping DB downgrade"
    fi
fi

# ─── File rollback ────────────────────────────────────────────────────────────

info "Rolling back $DEPLOY_DIR to $BACKUP_PATH ..."
if ! dry_run "sync_dir $BACKUP_PATH $DEPLOY_DIR"; then
    sync_dir "$BACKUP_PATH" "$DEPLOY_DIR"
    ok "Files restored"
fi

# ─── Service restart ──────────────────────────────────────────────────────────

info "Restarting $SERVICE ..."
if ! dry_run "sudo systemctl restart $SERVICE"; then
    sudo systemctl restart "$SERVICE"
fi

# ─── Health probe with retry loop ─────────────────────────────────────────────

if [[ "$DRY_RUN" == "true" ]]; then
    ok "Rollback complete (dry-run)."
    exit 0
fi

info "Waiting for service to become healthy (up to ${HEALTH_RETRIES} attempts x ${HEALTH_RETRY_SLEEP}s)..."
_attempt=0
_healthy=false
while [[ $_attempt -lt $HEALTH_RETRIES ]]; do
    _attempt=$(( _attempt + 1 ))
    _status=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null \
              | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" \
              2>/dev/null || echo "")
    if [[ "$_status" == "healthy" ]]; then
        _healthy=true
        break
    fi
    info "Attempt $_attempt/$HEALTH_RETRIES — not healthy yet (status='$_status'), retrying in ${HEALTH_RETRY_SLEEP}s..."
    sleep "$HEALTH_RETRY_SLEEP"
done

if [[ "$_healthy" != "true" ]]; then
    echo "" >&2
    echo "  Service did not become healthy after rollback." >&2
    echo "  Check the journal:" >&2
    echo "    journalctl -u $SERVICE -n 50 --no-pager" >&2
    echo "  Health endpoint: $HEALTH_URL" >&2
    fail "Health probe failed after rollback — manual intervention required"
fi

ok "Service healthy after rollback."
ok "Rollback complete."

#!/usr/bin/env bash
# ==============================================================================
# update-deployed.sh — Copy updated dashboard files to the deployed location
#                      and restart the systemd service.
#
# Run this any time server.py, local_app_monitoring.py, local_apps.json, or
# index.html changes.
# The repo lives on the Windows side; this script bridges it to WSL2.
#
# Usage (from any WSL2 terminal):
#   bash /mnt/c/Users/diete/Repositories/runner-dashboard/deploy/update-deployed.sh
#
# Or add a shell alias for convenience:
#   alias update-dashboard='bash /mnt/c/Users/diete/Repositories/runner-dashboard/deploy/update-deployed.sh'
#
# Options:
#   --repo <path>        Override the REPO path
#   --deploy-dir <path>  Override the deploy directory
#   --artifact <file>    Deploy from a pre-built artifact tarball
#   --dry-run            Preview without executing destructive steps
# ==============================================================================

set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REPO="${REPO:-/mnt/c/Users/diete/Repositories/runner-dashboard}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
SERVICE="runner-dashboard"
ARTIFACT_SOURCE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --artifact) ARTIFACT_SOURCE="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) shift ;;
    esac
done

[[ -d "$DEPLOY_DIR" ]]           || fail "Deployed dashboard not found at $DEPLOY_DIR — run setup.sh first."
if [[ -z "$ARTIFACT_SOURCE" ]]; then
    [[ -d "$REPO/backend" && -d "$REPO/frontend" ]] || fail "Dashboard repo not found at $REPO — check the path."
fi

info "Installing/updating backend dependencies..."
pip_install fastapi uvicorn psutil httpx PyYAML
ok "backend dependencies installed"

if ! dry_run "backup $DEPLOY_DIR"; then
    info "Creating backup snapshot..."
    _BACKUP=$(backup_dir "$DEPLOY_DIR") || fail "Backup failed; aborting update"
    [[ -n "$_BACKUP" ]] || fail "Backup returned empty path; aborting update"
    ok "Backup: $_BACKUP"
fi

if [[ -n "$ARTIFACT_SOURCE" ]]; then
    info "Installing dashboard artifact..."
    if ! dry_run "install-dashboard-artifact.sh --artifact $ARTIFACT_SOURCE --deploy-dir $DEPLOY_DIR"; then
        "$(dirname "$0")/install-dashboard-artifact.sh" \
            --artifact "$ARTIFACT_SOURCE" \
            --deploy-dir "$DEPLOY_DIR"
    fi
else
    info "Copying backend..."
    if ! dry_run "sync_dir $REPO/runner-dashboard/backend $DEPLOY_DIR/backend"; then
        sync_dir "$REPO/backend" "$DEPLOY_DIR/backend"
        ok  "backend deployed"
    fi

    info "Copying deploy scripts..."
    if ! dry_run "cp refresh-token.sh -> $DEPLOY_DIR/refresh-token.sh"; then
        cp "$REPO/deploy/refresh-token.sh"   "$DEPLOY_DIR/refresh-token.sh"
        chmod +x "$DEPLOY_DIR/refresh-token.sh"
        ok  "refresh-token.sh deployed"
    fi

    info "Copying frontend..."
    if ! dry_run "sync_dir $REPO/runner-dashboard/frontend $DEPLOY_DIR/frontend"; then
        sync_dir "$REPO/frontend" "$DEPLOY_DIR/frontend"
        ok  "frontend deployed"
    fi

    info "Copying local app manifest..."
    if ! dry_run "cp local_apps.json -> $DEPLOY_DIR/local_apps.json"; then
        cp "$REPO/local_apps.json"           "$DEPLOY_DIR/local_apps.json"
        cp "$REPO/VERSION"                   "$DEPLOY_DIR/VERSION"
        ok  "local_apps.json deployed"
    fi

    info "Writing deployment metadata..."
    if ! dry_run "write-deployment-metadata.sh $DEPLOY_DIR $REPO"; then
        "$(dirname "$0")/write-deployment-metadata.sh" "$DEPLOY_DIR" "$REPO"
        ok "deployment metadata written from source checkout"
    fi
fi

info "Restarting $SERVICE..."
if ! dry_run "sudo systemctl restart $SERVICE"; then
    sudo systemctl restart "$SERVICE"
fi

# Brief wait then check status — skipped in dry-run mode
if [[ "$DRY_RUN" != "true" ]]; then
    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE"; then
        ok "Service is running"
        echo ""
        echo "  Dashboard: http://localhost:8321"
        echo "  Health:    http://localhost:8321/api/health"
        echo "  Runs:      http://localhost:8321/api/runs"
        echo "  Queue:     http://localhost:8321/api/queue"
    else
        echo ""
        sudo systemctl status "$SERVICE" --no-pager
        fail "Service failed to start — check logs above"
    fi

    # Check GitHub API connectivity first (most common failure point)
    info "Checking GitHub API connectivity..."
    HEALTH_JSON=$(curl -s --max-time 8 http://localhost:8321/api/health 2>/dev/null || echo "{}")
    GH_STATUS=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('github_api','unknown'))" 2>/dev/null || echo "unknown")
    RUNNERS=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('runners_registered',0))" 2>/dev/null || echo "0")

    if [[ "$GH_STATUS" == "connected" ]]; then
        ok "GitHub API: connected | runners registered: $RUNNERS"
    else
        # Distinguish rate-limit exhaustion from a missing/bad token
        SECRETS_FILE="${HOME}/.config/runner-dashboard/env"
        STORED_TOKEN=$(grep '^GH_TOKEN=' "${SECRETS_FILE}" 2>/dev/null | cut -d= -f2-)
        RL_REMAINING="unknown"
        if [[ -n "$STORED_TOKEN" ]]; then
            RL_REMAINING=$(curl -s --max-time 5 \
                -H "Authorization: token ${STORED_TOKEN}" \
                https://api.github.com/rate_limit \
                | python3 -c "import sys,json,datetime; d=json.load(sys.stdin)['rate']; \
                  reset=datetime.datetime.fromtimestamp(d['reset']); \
                  print(f\"{d['remaining']}/{d['limit']} resets {reset.strftime('%H:%M:%S')}\")" \
                2>/dev/null || echo "unknown")
        fi

        if [[ "$RL_REMAINING" == "0/"* ]]; then
            RESET_TIME=$(echo "$RL_REMAINING" | grep -o 'resets [0-9:]*' || echo "")
            warn "GitHub API rate limit exhausted (${RL_REMAINING})"
            echo ""
            echo "  The token is valid but the 5000 req/hr limit is used up."
            echo "  Dashboard will reconnect automatically when the window resets."
            echo "  ${RESET_TIME} -- check with:"
            echo "    curl -s http://localhost:8321/api/health | python3 -m json.tool"
            echo ""
        else
            warn "GitHub API is NOT connected (status: $GH_STATUS)"
            echo ""
            if [[ -z "$STORED_TOKEN" ]]; then
                echo "  No GH_TOKEN found in ${SECRETS_FILE}."
            else
                echo "  GH_TOKEN present but API returned an error (rate limit remaining: ${RL_REMAINING})."
            fi
            echo "  Run these commands in WSL2 (as ${USER}, not root) to fix:"
            echo ""
            echo "    TOKEN=\$(gh auth token 2>/dev/null)"
            echo "    sed -i '/^GH_TOKEN=/d' ~/.config/runner-dashboard/env"
            echo "    printf 'GH_TOKEN=%s\\n' \"\$TOKEN\" >> ~/.config/runner-dashboard/env"
            echo "    sudo systemctl restart runner-dashboard"
            echo ""
            echo "  If gh auth token returns empty, re-authenticate:"
            echo "    gh auth login"
            echo "    gh auth refresh -s admin:org"
            echo ""
        fi
    fi

    # Smoke-test the runs endpoint
    info "Smoke-testing /api/runs..."
    RUNS=$(curl -s --max-time 10 http://localhost:8321/api/runs 2>/dev/null \
           | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('workflow_runs',[])))" 2>/dev/null || echo "0")
    if [[ "$GH_STATUS" == "connected" ]]; then
        ok "Endpoint returned $RUNS workflow runs"
    else
        info "Runs endpoint returned $RUNS (expected 0 -- API not connected yet)"
    fi
fi

echo ""
ok "Deploy complete."

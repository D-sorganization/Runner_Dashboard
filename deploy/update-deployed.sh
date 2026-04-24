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
#   bash /mnt/c/Users/diete/Repositories/Repository_Management/runner-dashboard/deploy/update-deployed.sh
#
# Or add a shell alias for convenience:
#   alias update-dashboard='bash /mnt/c/Users/diete/Repositories/Repository_Management/runner-dashboard/deploy/update-deployed.sh'
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

pip_install_backend_deps() {
    local -a cmd=(pip3 install --quiet fastapi uvicorn psutil httpx PyYAML)
    if pip3 install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        cmd=(pip3 install --break-system-packages --quiet fastapi uvicorn psutil httpx PyYAML)
    fi
    "${cmd[@]}"
}

REPO="${REPO:-/mnt/c/Users/diete/Repositories/Repository_Management}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
SERVICE="runner-dashboard"
ARTIFACT_SOURCE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --artifact) ARTIFACT_SOURCE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[[ -d "$DEPLOY_DIR" ]]           || fail "Deployed dashboard not found at $DEPLOY_DIR — run setup.sh first."
if [[ -z "$ARTIFACT_SOURCE" ]]; then
    [[ -d "$REPO/runner-dashboard" ]] || fail "Repo not found at $REPO — check the path."
fi

sync_dir() {
    local src="$1"
    local dest="$2"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$src/" "$dest/"
        return
    fi
    warn "rsync not found; using rm/cp fallback for ${dest}"
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -a "$src/." "$dest/"
}

info "Installing/updating backend dependencies..."
pip_install_backend_deps
ok "backend dependencies installed"

if [[ -n "$ARTIFACT_SOURCE" ]]; then
    info "Installing dashboard artifact..."
    "$(dirname "$0")/install-dashboard-artifact.sh" \
        --artifact "$ARTIFACT_SOURCE" \
        --deploy-dir "$DEPLOY_DIR"
else
    info "Copying backend..."
    sync_dir "$REPO/runner-dashboard/backend" "$DEPLOY_DIR/backend"
    ok  "backend deployed"

    info "Copying deploy scripts..."
    cp "$REPO/runner-dashboard/deploy/refresh-token.sh"   "$DEPLOY_DIR/refresh-token.sh"
    chmod +x "$DEPLOY_DIR/refresh-token.sh"
    ok  "refresh-token.sh deployed"

    info "Copying frontend..."
    sync_dir "$REPO/runner-dashboard/frontend" "$DEPLOY_DIR/frontend"
    ok  "frontend deployed"

    info "Copying local app manifest..."
    cp "$REPO/runner-dashboard/local_apps.json"           "$DEPLOY_DIR/local_apps.json"
    ok  "local_apps.json deployed"

    info "Writing deployment metadata..."
    "$(dirname "$0")/write-deployment-metadata.sh" "$DEPLOY_DIR" "$REPO"
    ok "deployment metadata written from source checkout"
fi

info "Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"

# Brief wait then check status
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
        echo -e "${YELLOW}[WARN]${NC} GitHub API rate limit exhausted (${RL_REMAINING})"
        echo ""
        echo "  The token is valid but the 5000 req/hr limit is used up."
        echo "  Dashboard will reconnect automatically when the window resets."
        echo "  ${RESET_TIME} — check with:"
        echo "    curl -s http://localhost:8321/api/health | python3 -m json.tool"
        echo ""
    else
        echo -e "${RED}[WARN]${NC} GitHub API is NOT connected (status: $GH_STATUS)"
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
    echo -e "${CYAN}[INFO]${NC} Runs endpoint returned $RUNS (expected 0 — API not connected yet)"
fi

echo ""
ok "Deploy complete."

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
#   bash /mnt/c/Users/<username>/Repositories/runner-dashboard/deploy/update-deployed.sh
#
# Or add a shell alias for convenience:
#   alias update-dashboard='bash /mnt/c/Users/<username>/Repositories/runner-dashboard/deploy/update-deployed.sh'
#
# Options:
#   --repo <path>        Override the REPO path
#   --deploy-dir <path>  Override the deploy directory
#   --artifact <file>    Deploy from a pre-built artifact tarball
#   --checksum <sha256>  Expected SHA-256 checksum for artifact
#   --dry-run            Preview without executing destructive steps
# ==============================================================================

set -euo pipefail
# shellcheck source=deploy/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DASHBOARD_USER="${DASHBOARD_USER:-$(whoami)}"
DASHBOARD_HOME="${DASHBOARD_HOME:-$HOME}"
REPO="${REPO:-/mnt/c/Users/${DASHBOARD_USER}/Repositories/runner-dashboard}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
SERVICE="runner-dashboard"
ARTIFACT_SOURCE=""
ARTIFACT_CHECKSUM=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --artifact) ARTIFACT_SOURCE="$2"; shift 2 ;;
        --checksum) ARTIFACT_CHECKSUM="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) shift ;;
    esac
done

[[ -d "$DEPLOY_DIR" ]]           || fail "Deployed dashboard not found at $DEPLOY_DIR — run setup.sh first."
if [[ -z "$ARTIFACT_SOURCE" ]]; then
    [[ -d "$REPO/backend" && -d "$REPO/frontend" ]] || fail "Dashboard repo not found at $REPO — check the path."
fi

DASHBOARD_PORT="${DASHBOARD_PORT:-}"
if [[ -z "$DASHBOARD_PORT" ]] && command -v systemctl &>/dev/null; then
    DASHBOARD_PORT=$(systemctl show "${SERVICE}.service" -p Environment --value 2>/dev/null \
        | tr ' ' '\n' \
        | awk -F= '$1 == "DASHBOARD_PORT" {print $2; exit}' || true)
fi
DASHBOARD_PORT="${DASHBOARD_PORT:-8321}"

if ! dry_run "backup $DEPLOY_DIR"; then
    info "Creating backup snapshot..."
    _BACKUP=$(backup_dir "$DEPLOY_DIR") || fail "Backup failed; aborting update"
    [[ -n "$_BACKUP" ]] || fail "Backup returned empty path; aborting update"
    ok "Backup: $_BACKUP"
fi

if [[ -n "$ARTIFACT_SOURCE" ]]; then
    info "Installing dashboard artifact..."
    INSTALL_ARGS=(--artifact "$ARTIFACT_SOURCE" --deploy-dir "$DEPLOY_DIR")
    if [[ -n "$ARTIFACT_CHECKSUM" ]]; then
        INSTALL_ARGS+=(--checksum "$ARTIFACT_CHECKSUM")
    fi
    if ! dry_run "install-dashboard-artifact.sh ${INSTALL_ARGS[*]}"; then
        "$(dirname "$0")/install-dashboard-artifact.sh" "${INSTALL_ARGS[@]}"
    fi
else
    info "Installing/updating deployed backend dependencies via uv sync (issue #333)..."
    # uv sync --frozen --no-dev ensures the deployed service venv matches uv.lock exactly.
    if ! command -v uv &>/dev/null; then
        pip install --quiet uv
    fi
    if [[ -f "$REPO/uv.lock" ]]; then
        UV_PROJECT_ENVIRONMENT="${DEPLOY_DIR}/.venv" uv sync --frozen --no-dev --project "$REPO"
    else
        fail "uv.lock missing at $REPO — refusing to install with floating transitives."
    fi
    ok "backend dependencies installed into ${DEPLOY_DIR}/.venv via uv sync --frozen --no-dev"

    info "Copying backend..."
    if ! dry_run "sync_dir $REPO/runner-dashboard/backend $DEPLOY_DIR/backend"; then
        sync_dir "$REPO/backend" "$DEPLOY_DIR/backend"
        # When the source repo lives on /mnt/c/ (Windows-mounted), files come
        # over with mode 0777 because NTFS can't represent POSIX bits faithfully.
        # security.py's _check_file_mode rejects world-writable config files,
        # which silently breaks machine_registry load. Normalise YAML/JSON
        # config perms to 0644 so the validator accepts them.
        find "$DEPLOY_DIR/backend" -maxdepth 2 -type f \
            \( -name '*.yml' -o -name '*.yaml' -o -name '*.json' \) \
            -exec chmod 0644 {} + 2>/dev/null || true
        ok  "backend deployed"
    fi

    info "Copying deploy scripts..."
    if ! dry_run "cp refresh-token.sh -> $DEPLOY_DIR/refresh-token.sh"; then
        cp "$REPO/deploy/refresh-token.sh"   "$DEPLOY_DIR/refresh-token.sh"
        chmod +x "$DEPLOY_DIR/refresh-token.sh"
        ok  "refresh-token.sh deployed"
    fi

    if ! dry_run "cp wsl-mirrored-port-helper.sh -> $DEPLOY_DIR/wsl-mirrored-port-helper.sh"; then
        # Idempotent no-op outside WSL-mirrored hosts; required on WSL hosts
        # whose systemd unit invokes it from ExecStartPre/Post to dodge the
        # Tailscale-serve port-conflict crash loop.
        cp "$REPO/deploy/wsl-mirrored-port-helper.sh" "$DEPLOY_DIR/wsl-mirrored-port-helper.sh"
        chmod +x "$DEPLOY_DIR/wsl-mirrored-port-helper.sh"
        ok  "wsl-mirrored-port-helper.sh deployed"
    fi

    info "Copying frontend..."
    if ! dry_run "sync_dir $REPO/runner-dashboard/frontend $DEPLOY_DIR/frontend"; then
        if [[ ! -d "$REPO/node_modules" ]]; then
            (cd "$REPO" && npm ci)
        fi
        (cd "$REPO" && npm run build)
        sync_dir "$REPO/frontend" "$DEPLOY_DIR/frontend"
        sync_dir "$REPO/dist" "$DEPLOY_DIR/dist"
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

# ── Pre-flight: syntax check + import test ───────────────────────────────────
if [[ -z "$ARTIFACT_SOURCE" ]]; then
    info "Running pre-flight syntax check on backend modules..."
    VENV="${DEPLOY_DIR}/.venv"
    PYTHON="${VENV}/bin/python"
    if [[ ! -x "$PYTHON" ]]; then
        PYTHON="python3"
    fi

    if ! dry_run "py_compile backend/server.py backend/runner_autoscaler.py"; then
        if ! "$PYTHON" -m py_compile "$REPO/backend/server.py" "$REPO/backend/runner_autoscaler.py" 2>&1; then
            fail "Pre-flight FAILED: syntax error in backend Python files — aborting before restart"
        fi
    fi
    ok "Pre-flight checks passed"
fi

info "Restarting $SERVICE..."
if ! dry_run "sudo systemctl restart $SERVICE"; then
    sudo systemctl restart "$SERVICE"
fi

# ── Ensure runner-unit hardening is present (idempotent) ─────────────────────
# Hosts set up before deploy/migrate-runner-units.sh existed — or where
# install-runner-maintenance.sh never ran — silently keep the runner units on
# KillMode=process. On stop that orphans Runner.Worker children and abruptly
# kills in-flight jobs instead of draining them. migrate-runner-units.sh writes
# the KillMode=mixed + TimeoutStopSec=120 drop-ins; re-applying it on every
# deploy means the hardening can never be silently missing on a host. It does
# NOT restart any unit, so a busy runner's job is never interrupted — the
# drop-in takes effect on the unit's next natural restart.
ensure_runner_hardening() {
    local migrate; migrate="$(dirname "$0")/migrate-runner-units.sh"
    if [[ ! -x "$migrate" ]]; then
        warn "migrate-runner-units.sh not found; skipping runner-unit hardening check"
        return 0
    fi

    local missing=0 unit dropin
    while IFS= read -r unit; do
        [[ -n "$unit" ]] || continue
        dropin="/etc/systemd/system/${unit}.d/10-runner-dashboard-busy-lock.conf"
        grep -qs 'KillMode=mixed' "$dropin" || missing=1
    done < <(systemctl list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}')

    if [[ "$missing" == "0" ]]; then
        ok "Runner units already hardened (KillMode=mixed drop-ins present)"
        return 0
    fi

    if dry_run "sudo $migrate (apply KillMode=mixed runner hardening)"; then
        return 0
    fi
    if sudo -n true 2>/dev/null; then
        info "Applying runner-unit hardening (KillMode=mixed, TimeoutStopSec=120; no unit restart)..."
        if sudo bash "$migrate"; then
            ok "Runner-unit hardening applied (effective on each unit's next restart)"
        else
            warn "migrate-runner-units.sh failed; runner units may still be on KillMode=process"
        fi
    else
        warn "Runner units missing KillMode=mixed hardening — run: sudo deploy/migrate-runner-units.sh"
    fi
}
ensure_runner_hardening

# ── Health-gate: verify service came up ──────────────────────────────────────
_check_health() {
    curl -fsS --max-time 10 "http://localhost:${DASHBOARD_PORT}/health" >/dev/null 2>&1
}

_wait_healthy() {
    local attempt=0 delays=(1 2 4 8 16)
    for delay in "${delays[@]}"; do
        if _check_health; then
            return 0
        fi
        warn "Health check attempt $((attempt+1)) failed; retrying in ${delay}s..."
        sleep "$delay"
        ((attempt++)) || true
    done
    return 1
}

# Brief wait then check status — skipped in dry-run mode
if [[ "$DRY_RUN" != "true" ]]; then
    if ! _wait_healthy; then
        warn "Service failed health checks — attempting rollback..."
        if [[ -n "${_BACKUP:-}" && -d "$_BACKUP" ]]; then
            "$(dirname "$0")/rollback-deployed.sh" --backup-dir "$_BACKUP" --deploy-dir "$DEPLOY_DIR"
        fi
        fail "Deploy failed health checks; rollback attempted"
    fi
    if sudo systemctl is-active --quiet "$SERVICE"; then
        ok "Service is running"
        echo ""
        echo "  Dashboard: http://localhost:${DASHBOARD_PORT}"
        echo "  Health:    http://localhost:${DASHBOARD_PORT}/api/health"
        echo "  Runs:      http://localhost:${DASHBOARD_PORT}/api/runs"
        echo "  Queue:     http://localhost:${DASHBOARD_PORT}/api/queue"
    else
        echo ""
        sudo systemctl status "$SERVICE" --no-pager
        fail "Service failed to start — check logs above"
    fi

    # Check GitHub API connectivity first (most common failure point)
    info "Checking GitHub API connectivity..."
    HEALTH_JSON=$(curl -s --max-time 8 "http://localhost:${DASHBOARD_PORT}/api/health" 2>/dev/null || echo "{}")
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
            echo "    curl -s http://localhost:${DASHBOARD_PORT}/api/health | python3 -m json.tool"
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
    RUNS=$(curl -s --max-time 10 "http://localhost:${DASHBOARD_PORT}/api/runs" 2>/dev/null \
           | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('workflow_runs',[])))" 2>/dev/null || echo "0")
    if [[ "$GH_STATUS" == "connected" ]]; then
        ok "Endpoint returned $RUNS workflow runs"
    else
        info "Runs endpoint returned $RUNS (expected 0 -- API not connected yet)"
    fi
fi

echo ""
ok "Deploy complete."

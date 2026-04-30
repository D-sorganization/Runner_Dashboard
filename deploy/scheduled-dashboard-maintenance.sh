#!/usr/bin/env bash
# ==============================================================================
# scheduled-dashboard-maintenance.sh
#
# Idempotent maintenance entrypoint for Codex Scheduled Tasks, Claude Cowork
# scheduled tasks, cron, or manual use. It keeps the local runner dashboard on
# the current repo version, restarts it after deployment, and installs the WSL
# keepalive layers needed by dashboard and self-hosted runner machines.
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
ok() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${DASHBOARD_DIR}/.." && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/actions-runners/dashboard}"
PORT="${DASHBOARD_PORT:-8321}"
SERVICE="${RUNNER_DASHBOARD_SERVICE:-runner-dashboard}"
RUNNERS="${NUM_RUNNERS:-4}"
MACHINE_NAME="${MACHINE_NAME:-$(hostname)}"
ROLE="${MACHINE_ROLE:-node}"
FLEET_NODES_ARG="${FLEET_NODES:-}"
HUB_URL_ARG="${HUB_URL:-}"
SKIP_GIT=0
SKIP_WSL_KEEPALIVE=0
INSTALL_IF_MISSING=1
INSTALL_WINDOWS_WATCHDOG=1
INSTALL_AUTOSCALER=0
WINDOWS_USER="${WINDOWS_USER:-}"
WSL_DISTRO="${WSL_DISTRO_NAME:-}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/runner-dashboard}"
BACKUP_RETENTION="${BACKUP_RETENTION:-30}"
DRY_RUN=0
BACKUP_ONLY=0

usage() {
    cat <<'EOF'
Usage:
  scheduled-dashboard-maintenance.sh [options]

Options:
  --repo PATH                  Repository_Management path (default: script parent)
  --deploy-dir PATH            Deployed dashboard path (default: ~/actions-runners/dashboard)
  --machine-name NAME          Machine display name (default: hostname)
  --role hub|node              Dashboard role (default: MACHINE_ROLE or node)
  --runners COUNT              Runner count hint for setup.sh (default: NUM_RUNNERS or 4)
  --fleet-nodes CSV            Hub FLEET_NODES string, e.g. brick=http://100.x.x.x:8321
  --hub-url URL                Node HUB_URL for proxying GitHub/fleet APIs through hub
  --skip-git                   Do not git pull before deployment
  --skip-wsl-keepalive         Do not install/verify WSL keepalive layers
  --no-install-if-missing      Fail if the dashboard has not been deployed yet
  --no-windows-watchdog        Skip Windows .wslconfig and Scheduled Task setup
  --install-autoscaler         Install/update runner-autoscaler.service after dashboard
  --windows-user NAME          Windows profile name for .wslconfig/watchdog path
  --distro NAME                WSL distribution name for Windows watchdog
  --backup-dir PATH            Directory for state snapshots (default: /var/backups/runner-dashboard, env BACKUP_DIR)
  --backup-retention N         Keep most recent N backups (default: 30, env BACKUP_RETENTION)
  --backup-only                Run only backup_state and exit (useful with --dry-run)
  --dry-run                    Print actions without making changes (currently honoured by backup_state)
  -h, --help                   Show this help

Scheduled-agent example:
  bash runner-dashboard/deploy/scheduled-dashboard-maintenance.sh \
    --role hub \
    --runners 8 \
    --fleet-nodes "brick=http://100.96.15.94:8321,oglaptop=http://100.125.64.108:8321"
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) REPO_ROOT="$2"; DASHBOARD_DIR="${REPO_ROOT}/runner-dashboard"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
        --machine-name) MACHINE_NAME="$2"; shift 2 ;;
        --role) ROLE="$2"; shift 2 ;;
        --runners) RUNNERS="$2"; shift 2 ;;
        --fleet-nodes) FLEET_NODES_ARG="$2"; shift 2 ;;
        --hub-url) HUB_URL_ARG="$2"; shift 2 ;;
        --skip-git) SKIP_GIT=1; shift ;;
        --skip-wsl-keepalive) SKIP_WSL_KEEPALIVE=1; shift ;;
        --no-install-if-missing) INSTALL_IF_MISSING=0; shift ;;
        --no-windows-watchdog) INSTALL_WINDOWS_WATCHDOG=0; shift ;;
        --install-autoscaler) INSTALL_AUTOSCALER=1; shift ;;
        --windows-user) WINDOWS_USER="$2"; shift 2 ;;
        --distro) WSL_DISTRO="$2"; shift 2 ;;
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        --backup-retention) BACKUP_RETENTION="$2"; shift 2 ;;
        --backup-only) BACKUP_ONLY=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

[[ -d "${DASHBOARD_DIR}" ]] || fail "Dashboard source not found: ${DASHBOARD_DIR}"
if [[ "${BACKUP_ONLY}" != "1" ]]; then
    [[ -d "${REPO_ROOT}/.git" ]] || fail "Repo root is not a git checkout: ${REPO_ROOT}"
fi

sync_repo() {
    if [[ "$SKIP_GIT" == "1" ]]; then
        warn "Skipping git sync by request"
        return
    fi
    info "Syncing ${REPO_ROOT}"
    git -C "${REPO_ROOT}" pull --ff-only
    ok "Repo is current with upstream"
}

install_wsl_systemd_keepalive() {
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found; skipping WSL systemd keepalive"
        return
    fi

    info "Installing WSL systemd keepalive service"
    tmp="$(mktemp)"
    cat >"${tmp}" <<'EOF'
[Unit]
Description=Keep WSL runner host alive for self-hosted GitHub Actions
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash -c 'while true; do sleep 600; done'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    sudo install -m 0644 "${tmp}" /etc/systemd/system/wsl-runner-keepalive.service
    rm -f "${tmp}"
    sudo systemctl daemon-reload
    sudo systemctl enable --now wsl-runner-keepalive.service
    ok "wsl-runner-keepalive.service is enabled and running"
}

detect_windows_user() {
    if [[ -n "${WINDOWS_USER}" ]]; then
        return
    fi
    if [[ -n "${WINDOWS_USER:-}" ]]; then
        return
    fi
    if command -v powershell.exe >/dev/null 2>&1; then
        WINDOWS_USER="$(
            powershell.exe -NoProfile -Command '$env:USERNAME' 2>/dev/null \
                | tr -d '\r' \
                | tail -1 \
                || true
        )"
    fi
}

detect_wsl_distro() {
    if [[ -n "${WSL_DISTRO}" ]]; then
        return
    fi
    WSL_DISTRO="${WSL_DISTRO_NAME:-Ubuntu-22.04}"
}

install_windows_watchdog() {
    if [[ "${INSTALL_WINDOWS_WATCHDOG}" != "1" ]]; then
        warn "Skipping Windows watchdog by request"
        return
    fi
    if ! command -v powershell.exe >/dev/null 2>&1; then
        warn "powershell.exe unavailable; skipping Windows watchdog setup"
        return
    fi

    detect_windows_user
    detect_wsl_distro
    if [[ -z "${WINDOWS_USER}" || ! -d "/mnt/c/Users/${WINDOWS_USER}" ]]; then
        warn "Could not find Windows user profile; skipping Windows watchdog setup"
        return
    fi

    local win_home="/mnt/c/Users/${WINDOWS_USER}"
    local watchdog="${win_home}/wsl-keepalive.ps1"
    info "Writing Windows WSL keepalive watchdog for distro ${WSL_DISTRO}"

    if [[ -f "${win_home}/.wslconfig" ]] && grep -q '^\[wsl2\]' "${win_home}/.wslconfig"; then
        if grep -q '^vmIdleTimeout=' "${win_home}/.wslconfig"; then
            sed -i 's/^vmIdleTimeout=.*/vmIdleTimeout=-1/' "${win_home}/.wslconfig"
        else
            printf '\nvmIdleTimeout=-1\n' >> "${win_home}/.wslconfig"
        fi
    elif [[ -f "${win_home}/.wslconfig" ]]; then
        printf '\n[wsl2]\nvmIdleTimeout=-1\n' >> "${win_home}/.wslconfig"
    else
        cat >"${win_home}/.wslconfig" <<'EOF'
[wsl2]
vmIdleTimeout=-1
EOF
    fi

    cat >"${watchdog}" <<EOF
\$Distro = '${WSL_DISTRO}'
while (\$true) {
    \$running = wsl.exe -l --running 2>\$null | Select-String -SimpleMatch \$Distro
    if (-not \$running) {
        Start-Process -FilePath 'wsl.exe' -ArgumentList "-d \$Distro -- /bin/bash -lc 'sleep infinity'" -WindowStyle Hidden
        Start-Sleep -Seconds 10
    }
    Start-Sleep -Seconds 30
}
EOF

    local ps_watchdog
    ps_watchdog="C:\\Users\\${WINDOWS_USER}\\wsl-keepalive.ps1"
    if powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\
\$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"${ps_watchdog}\"'; \
\$trigger = New-ScheduledTaskTrigger -AtStartup; \
\$trigger.Delay = 'PT30S'; \
\$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365); \
Register-ScheduledTask -TaskName 'WSL-Runner-KeepAlive' -Action \$action -Trigger \$trigger -Settings \$settings -RunLevel Highest -User '${WINDOWS_USER}' -Force; \
Start-ScheduledTask -TaskName 'WSL-Runner-KeepAlive'; \
Get-ScheduledTask -TaskName 'WSL-Runner-KeepAlive' | Select-Object TaskName,State" >/dev/null; then
        ok "Windows WSL-Runner-KeepAlive scheduled task is registered"
    else
        warn "Could not register Windows Scheduled Task. Rerun from an elevated Windows session or register ${ps_watchdog} manually."
    fi
}

deploy_dashboard() {
    chmod +x "${DASHBOARD_DIR}/deploy/setup.sh" "${DASHBOARD_DIR}/deploy/update-deployed.sh"

    if [[ ! -d "${DEPLOY_DIR}" ]]; then
        if [[ "${INSTALL_IF_MISSING}" != "1" ]]; then
            fail "Dashboard is not deployed at ${DEPLOY_DIR}; run setup.sh first"
        fi
        info "Dashboard is not deployed yet; running setup.sh"
        args=(--runners "${RUNNERS}" --machine-name "${MACHINE_NAME}" --role "${ROLE}")
        [[ -n "${FLEET_NODES_ARG}" ]] && args+=(--fleet-nodes "${FLEET_NODES_ARG}")
        [[ -n "${HUB_URL_ARG}" ]] && args+=(--hub-url "${HUB_URL_ARG}")
        "${DASHBOARD_DIR}/deploy/setup.sh" "${args[@]}"
    else
        info "Updating deployed dashboard from ${REPO_ROOT}"
        REPO="${REPO_ROOT}" DEPLOY_DIR="${DEPLOY_DIR}" "${DASHBOARD_DIR}/deploy/update-deployed.sh"
    fi
}

install_autoscaler_if_requested() {
    if [[ "${INSTALL_AUTOSCALER}" != "1" ]]; then
        return
    fi
    info "Installing/updating runner autoscaler"
    chmod +x "${DASHBOARD_DIR}/deploy/install-autoscaler.sh"
    "${DASHBOARD_DIR}/deploy/install-autoscaler.sh"
}

verify_dashboard() {
    info "Verifying ${SERVICE}"
    sudo systemctl enable "${SERVICE}.service" >/dev/null
    sudo systemctl restart "${SERVICE}.service"
    sleep 2
    sudo systemctl is-active --quiet "${SERVICE}.service" || fail "${SERVICE}.service is not active"
    curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/api/health" >/dev/null
    curl -fsS --max-time 12 "http://127.0.0.1:${PORT}/api/fleet/nodes" >/dev/null || warn "/api/fleet/nodes is not reachable yet"
    ok "Dashboard is enabled, restarted, and responding on port ${PORT}"
}

purge_stale_queue() {
    # Cancel queued runs older than STALE_QUEUE_AGE_MINUTES (default 120 min).
    local age="${STALE_QUEUE_AGE_MINUTES:-120}"
    local script
    script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../Repository_Management/scripts/cancel_stale_queue.py"
    if command -v python3 >/dev/null 2>&1 && [[ -f "${script}" ]]; then
        info "Purging stale queue (runs queued > ${age} min)"
        python3 "${script}" --cancel --min-age "${age}" || warn "Stale queue purge exited non-zero"
        return
    fi
    if curl -fsS --max-time 5 "http://127.0.0.1:${PORT:-8321}/api/health" >/dev/null 2>&1; then
        info "Purging stale queue via dashboard API (runs queued > ${age} min)"
        curl -fsS --max-time 60 -X POST \
            -H "Content-Type: application/json" \
            -d "{\"min_age\": ${age}, \"dry_run\": false}" \
            "http://127.0.0.1:${PORT:-8321}/api/queue/purge-stale" \
            | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Cancelled {d[\"cancelled_count\"]}/{d[\"stale_count\"]} stale run(s)')" \
            || warn "Stale queue API purge failed"
    else
        warn "Dashboard not reachable; skipping stale queue purge (will retry next run)"
    fi
}

backup_state() {
    # Snapshot dashboard state for disaster recovery (issue #417).
    #
    # Inputs (env-overridable):
    #   BACKUP_DIR        target dir, default /var/backups/runner-dashboard
    #   BACKUP_RETENTION  count of recent backups to keep, default 30
    #   DRY_RUN           when 1, print plan but do not write
    #
    # Snapshots (silently skipped if absent via tar --ignore-failed-read):
    #   - <DASHBOARD_DIR>/config/         (agent_remediation.json, principals.yml, etc.)
    #   - <DEPLOY_DIR>/config/            (deployed-host config, when present)
    #   - $HOME/.config/runner-dashboard/ (env, session_secret, runner-schedule.json)
    #   - $HOME/.dashboard-logs/          (recent operator logs)
    #   - /var/log/runner-dashboard/      (recent service logs, when present)
    local timestamp tarball
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    tarball="${BACKUP_DIR}/runner-dashboard-state-${timestamp}.tar.gz"

    # Candidate paths — tar will skip missing entries via --ignore-failed-read.
    local -a candidates=()
    [[ -d "${DASHBOARD_DIR}/config" ]] && candidates+=("${DASHBOARD_DIR}/config")
    [[ -d "${DEPLOY_DIR}/config" ]] && candidates+=("${DEPLOY_DIR}/config")
    [[ -d "${HOME}/.config/runner-dashboard" ]] && candidates+=("${HOME}/.config/runner-dashboard")
    [[ -d "${HOME}/.dashboard-logs" ]] && candidates+=("${HOME}/.dashboard-logs")
    [[ -d "/var/log/runner-dashboard" ]] && candidates+=("/var/log/runner-dashboard")

    if [[ "${DRY_RUN}" == "1" ]]; then
        info "[DRY-RUN] backup_state would write: ${tarball}"
        info "[DRY-RUN] would archive these existing paths:"
        if (( ${#candidates[@]} == 0 )); then
            warn "[DRY-RUN] no state paths exist on this host yet"
        else
            local p
            for p in "${candidates[@]}"; do
                echo "  - ${p}"
            done
        fi
        info "[DRY-RUN] retention: keep most recent ${BACKUP_RETENTION} backups in ${BACKUP_DIR}"
        return 0
    fi

    if (( ${#candidates[@]} == 0 )); then
        warn "No state paths exist; skipping backup_state"
        return 0
    fi

    info "Snapshotting dashboard state to ${tarball}"
    if ! mkdir -p "${BACKUP_DIR}" 2>/dev/null; then
        if ! sudo mkdir -p "${BACKUP_DIR}" || ! sudo chown "$(id -u):$(id -g)" "${BACKUP_DIR}"; then
            warn "Cannot create ${BACKUP_DIR}; skipping backup_state"
            return 0
        fi
    fi

    if tar --ignore-failed-read -czf "${tarball}" "${candidates[@]}" 2>/dev/null; then
        ok "Wrote state snapshot ${tarball}"
    else
        warn "tar reported errors while writing ${tarball} (continuing)"
    fi

    # Retention: keep the most recent BACKUP_RETENTION snapshots.
    local keep="${BACKUP_RETENTION}"
    local skip=$((keep + 1))
    # shellcheck disable=SC2012  # ls -t is fine here; filenames are timestamped and we control them.
    ls -1t "${BACKUP_DIR}"/runner-dashboard-state-*.tar.gz 2>/dev/null \
        | tail -n "+${skip}" \
        | xargs -r rm -f
    ok "Backup retention: kept most recent ${keep} snapshots in ${BACKUP_DIR}"
}

main() {
    info "Runner dashboard scheduled maintenance"
    echo "Repo:       ${REPO_ROOT}"
    echo "Dashboard:  ${DASHBOARD_DIR}"
    echo "Deploy:     ${DEPLOY_DIR}"
    echo "Machine:    ${MACHINE_NAME}"
    echo "Role:       ${ROLE}"
    echo "Port:       ${PORT}"
    echo "BackupDir:  ${BACKUP_DIR}"

    if [[ "${BACKUP_ONLY}" == "1" ]]; then
        backup_state
        ok "Backup-only run complete"
        return 0
    fi

    sync_repo
    if [[ "${SKIP_WSL_KEEPALIVE}" != "1" ]]; then
        install_wsl_systemd_keepalive
        install_windows_watchdog
    fi
    deploy_dashboard
    install_autoscaler_if_requested
    verify_dashboard
    purge_stale_queue
    backup_state
    ok "Scheduled dashboard maintenance complete"
}

main "$@"
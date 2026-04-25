#!/usr/bin/env bash
# ==============================================================================
# setup.sh — Full production deployment of the Runner Dashboard
# ==============================================================================
# This script:
#   1. Installs Python dependencies
#   2. Copies dashboard to a stable location (~/.../dashboard/)
#   3. Configures passwordless sudo for runner svc.sh commands
#   4. Installs systemd service with GH_TOKEN and machine metadata
#   5. Sets up Windows port forwarding instructions
#
# Usage (from WSL2, in the runner-dashboard directory):
#   sed -i 's/\r$//' deploy/setup.sh && chmod +x deploy/setup.sh
#
#   ControlTower  (8 runners, hub):
#     ./deploy/setup.sh --runners 8 --machine-name ControlTower --role hub
#
#   Brick-Windows   (1 runner, GPU node):
#     ./deploy/setup.sh --runners 1 --machine-name Brick-Windows
#
#   OG-Laptop     (4 runners, node):
#     ./deploy/setup.sh --runners 4 --machine-name OG-Laptop
#
#   DeskComputer (8 installed runners, schedule controls day/night count):
#     ./deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
#
#   Hub — add fleet nodes after all machines are running (Tailscale IPs):
#     ./deploy/setup.sh --runners 8 --machine-name ControlTower --role hub \
#       --fleet-nodes "Brick-Windows:http://100.x.x.x:8321,OG-Laptop:http://100.x.x.x:8321,DeskComputer:http://100.x.x.x:8321"
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
header(){ echo -e "\n${BOLD}═══ $* ═══${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$HOME/actions-runners/dashboard"
PORT="${DASHBOARD_PORT:-8321}"
USER="$(whoami)"
NUM_RUNNERS="${NUM_RUNNERS:-4}"
MACHINE_NAME=""
DISPLAY_NAME_VAL=""
RUNNER_ALIASES_VAL=""
MACHINE_ROLE="node"
FLEET_NODES_VAL=""
HUB_URL_VAL=""
ARTIFACT_SOURCE=""
SCHEDULE_CONFIG_VAL="${RUNNER_SCHEDULE_CONFIG:-$HOME/.config/runner-dashboard/runner-schedule.json}"
PYTHON_BIN="${RUNNER_DASHBOARD_PYTHON:-$(command -v python3.11 || command -v python3)}"

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --runners)      NUM_RUNNERS="$2";     shift 2 ;;
        --machine-name) MACHINE_NAME="$2";    shift 2 ;;
        --display-name) DISPLAY_NAME_VAL="$2"; shift 2 ;;
        --runner-aliases) RUNNER_ALIASES_VAL="$2"; shift 2 ;;
        --role)         MACHINE_ROLE="$2";    shift 2 ;;
        --fleet-nodes)  FLEET_NODES_VAL="$2"; shift 2 ;;
        --hub-url)      HUB_URL_VAL="$2";     shift 2 ;;
        --artifact)     ARTIFACT_SOURCE="$2"; shift 2 ;;
        --schedule-config) SCHEDULE_CONFIG_VAL="$2"; shift 2 ;;
        *) shift ;;
    esac
done

[[ -z "$MACHINE_NAME" ]] && MACHINE_NAME="$(hostname)"
[[ -z "$DISPLAY_NAME_VAL" ]] && DISPLAY_NAME_VAL="$MACHINE_NAME"

header "Runner Dashboard Production Setup"
echo "Source:       ${SCRIPT_DIR}"
echo "Deploy:       ${DEPLOY_DIR}"
echo "Port:         ${PORT}"
echo "User:         ${USER}"
echo "Machine:      ${MACHINE_NAME}"
echo "Display:      ${DISPLAY_NAME_VAL}"
[[ -n "$RUNNER_ALIASES_VAL" ]] && echo "Aliases:      ${RUNNER_ALIASES_VAL}"
echo "Role:         ${MACHINE_ROLE}"
echo "Runners:      ${NUM_RUNNERS}"
echo "Schedule:     ${SCHEDULE_CONFIG_VAL}"
echo "Python:       ${PYTHON_BIN}"
[[ -n "$FLEET_NODES_VAL" ]] && echo "Fleet nodes:  ${FLEET_NODES_VAL}"
echo ""

# ── Step 1: Install Python deps ──────────────────────────────────────────────
header "Step 1/5: Python Dependencies"
REQUIREMENTS_FILE="${SCRIPT_DIR}/backend/requirements.txt"
if [[ -f "$REQUIREMENTS_FILE" ]]; then
    PIP_ARGS=(install --quiet -r "$REQUIREMENTS_FILE")
    if "${PYTHON_BIN}" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        PIP_ARGS=(install --break-system-packages --quiet -r "$REQUIREMENTS_FILE")
    fi
else
    # Fallback if requirements.txt is somehow absent
    PIP_ARGS=(install --quiet fastapi pydantic uvicorn psutil httpx PyYAML)
    if "${PYTHON_BIN}" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        PIP_ARGS=(install --break-system-packages --quiet fastapi pydantic uvicorn psutil httpx PyYAML)
    fi
fi
"${PYTHON_BIN}" -m pip "${PIP_ARGS[@]}"
ok "backend dependencies installed from requirements.txt"

# ── Step 2: Deploy dashboard files ───────────────────────────────────────────
header "Step 2/5: Deploy Dashboard"
if [[ -n "${ARTIFACT_SOURCE}" ]]; then
    info "Installing dashboard artifact from ${ARTIFACT_SOURCE}"
    ARTIFACT_SOURCE="${ARTIFACT_SOURCE}" DEPLOY_DIR="${DEPLOY_DIR}" \
        "${SCRIPT_DIR}/deploy/install-dashboard-artifact.sh" \
        --artifact "${ARTIFACT_SOURCE}" \
        --deploy-dir "${DEPLOY_DIR}"
else
    mkdir -p "${DEPLOY_DIR}"
    cp -r "${SCRIPT_DIR}/backend"  "${DEPLOY_DIR}/"
    cp -r "${SCRIPT_DIR}/frontend" "${DEPLOY_DIR}/"
    mkdir -p "${DEPLOY_DIR}/config"
    cp -r "${SCRIPT_DIR}/config/." "${DEPLOY_DIR}/config/"
    cp "${SCRIPT_DIR}/local_apps.json" "${DEPLOY_DIR}/local_apps.json"
    cp "${SCRIPT_DIR}/VERSION" "${DEPLOY_DIR}/VERSION"
    "${SCRIPT_DIR}/deploy/write-deployment-metadata.sh" "${DEPLOY_DIR}" "${SCRIPT_DIR}"

    # Deploy and configure the token refresh script
    REFRESH_SCRIPT="${HOME}/actions-runners/dashboard/refresh-token.sh"
    cp "${SCRIPT_DIR}/deploy/refresh-token.sh" "${REFRESH_SCRIPT}"
    sed -i 's/\r$//' "${REFRESH_SCRIPT}"
    chmod +x "${REFRESH_SCRIPT}"
    ok "Dashboard deployed to ${DEPLOY_DIR}"
fi

if [[ -n "${ARTIFACT_SOURCE}" ]]; then
    ok "Dashboard artifact deployed to ${DEPLOY_DIR}"
fi

# ── Step 2.5: Token secrets file ─────────────────────────────────────────────
header "Step 2.5/5: GitHub Token"

SECRETS_FILE="${HOME}/.config/runner-dashboard/env"
mkdir -p "$(dirname "${SECRETS_FILE}")"
touch "${SECRETS_FILE}"
chmod 600 "${SECRETS_FILE}"

# Try to populate GH_TOKEN automatically from gh CLI.
# 'gh auth token' exists only in gh >= 2.40; fall back to reading hosts.yml on older versions.
AUTO_TOKEN=""
RAW_TOKEN=$(gh auth token 2>&1 || true)
if ! echo "${RAW_TOKEN}" | grep -q "^unknown command\|^Usage:"; then
    AUTO_TOKEN=$(echo "${RAW_TOKEN}" | head -1 | tr -d '[:space:]')
fi
if [[ -z "${AUTO_TOKEN}" ]] || echo "${AUTO_TOKEN}" | grep -q "unknown\|Usage"; then
    AUTO_TOKEN=$(python3 -c "
import re, pathlib, sys
hosts = pathlib.Path.home() / '.config/gh/hosts.yml'
if not hosts.exists(): sys.exit(0)
m = re.search(r'oauth_token:\s*(\S+)', hosts.read_text())
print(m.group(1) if m else '', end='')
" 2>/dev/null || true)
fi
if [[ ! "${AUTO_TOKEN}" =~ ^(gho_|ghp_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{30,}$ ]]; then
    AUTO_TOKEN=""
fi
if [[ -n "${AUTO_TOKEN}" ]]; then
    sed -i '/^GH_TOKEN=/d' "${SECRETS_FILE}"
    printf 'GH_TOKEN=%s\n' "${AUTO_TOKEN}" >> "${SECRETS_FILE}"
    ok "GH_TOKEN auto-populated from gh credentials (has scopes: $(gh auth status 2>&1 | grep -o 'Token scopes:.*' || echo 'unknown'))"
    info "Token will be refreshed automatically at each service start via refresh-token.sh"
    info "If it ever expires: gh auth login && gh auth refresh -s admin:org && sudo systemctl restart runner-dashboard"
else
    warn "gh auth token returned empty — GitHub API will be unavailable until authenticated."
    warn "After setup, run: gh auth login && gh auth refresh -s admin:org"
    warn "Then: sudo systemctl restart runner-dashboard"
    warn "(The refresh-token.sh script will pick it up automatically on restart)"
fi

# Append FLEET_NODES to secrets file if provided
if [[ -n "$FLEET_NODES_VAL" ]]; then
    # Remove existing FLEET_NODES line, then re-add
    sed -i '/^FLEET_NODES=/d' "${SECRETS_FILE}" 2>/dev/null || true
    printf 'FLEET_NODES=%s\n' "${FLEET_NODES_VAL}" >> "${SECRETS_FILE}"
    ok "FLEET_NODES written to secrets file"
fi

# Append HUB_URL to secrets file if provided
if [[ -n "$HUB_URL_VAL" ]]; then
    sed -i '/^HUB_URL=/d' "${SECRETS_FILE}" 2>/dev/null || true
    printf 'HUB_URL=%s\n' "${HUB_URL_VAL}" >> "${SECRETS_FILE}"
    ok "HUB_URL written to secrets file. API Proxying enabled."
fi

mkdir -p "$(dirname "${SCHEDULE_CONFIG_VAL}")"
if [[ ! -f "${SCHEDULE_CONFIG_VAL}" ]]; then
    cp "${SCRIPT_DIR}/config/runner-schedule.json" "${SCHEDULE_CONFIG_VAL}"
    ok "Seeded runner schedule config at ${SCHEDULE_CONFIG_VAL}"
else
    ok "Runner schedule config already exists at ${SCHEDULE_CONFIG_VAL}"
fi

# ── Step 3: Sudoers for runner control ───────────────────────────────────────
header "Step 3/5: Sudoers Configuration"
SUDOERS_FILE="/etc/sudoers.d/runner-dashboard"
SUDOERS_LINE="${USER} ALL=(ALL) NOPASSWD: ${HOME}/actions-runners/runner-*/svc.sh"

if [[ -f "${SUDOERS_FILE}" ]] && grep -qF "${SUDOERS_LINE}" "${SUDOERS_FILE}" 2>/dev/null; then
    ok "Sudoers rule already configured"
else
    info "Adding passwordless sudo for runner svc.sh..."
    echo "${SUDOERS_LINE}" | sudo tee "${SUDOERS_FILE}" > /dev/null
    sudo chmod 440 "${SUDOERS_FILE}"
    if sudo visudo -c -f "${SUDOERS_FILE}" 2>/dev/null; then
        ok "Sudoers rule installed (start/stop runners without password)"
    else
        sudo rm -f "${SUDOERS_FILE}"
        warn "Sudoers validation failed — removed. You may need sudo password for runner control."
    fi
fi

# ── Step 4: Systemd service ──────────────────────────────────────────────────
header "Step 4/5: Systemd Service (Auto-Start)"

SERVICE_FILE="/etc/systemd/system/runner-dashboard.service"

sudo tee "${SERVICE_FILE}" > /dev/null <<SVCEOF
[Unit]
Description=D-sorganization Runner Dashboard (${MACHINE_NAME})
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${DEPLOY_DIR}
ExecStartPre=${DEPLOY_DIR}/refresh-token.sh
ExecStart=${PYTHON_BIN} ${DEPLOY_DIR}/backend/server.py
Restart=always
RestartSec=5
Environment=GITHUB_ORG=D-sorganization
Environment=NUM_RUNNERS=${NUM_RUNNERS}
Environment=DASHBOARD_PORT=${PORT}
Environment=DISPLAY_NAME=${DISPLAY_NAME_VAL}
Environment=RUNNER_ALIASES=${RUNNER_ALIASES_VAL}
Environment=RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG_VAL}
Environment=RUNNER_SCHEDULER_BIN=/usr/local/bin/runner-scheduler
Environment=MACHINE_ROLE=${MACHINE_ROLE}
Environment=HOME=${HOME}
Environment=PATH=/usr/lib/wsl/lib:${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Secrets (GH_TOKEN, FLEET_NODES) are loaded from a file readable only by
# this user — not stored in this world-readable unit file.
EnvironmentFile=-${HOME}/.config/runner-dashboard/env

# Hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RemoveIPC=true
# Allow the dashboard to read/write runner secrets and config from HOME.
ReadWritePaths=${HOME}/.config/runner-dashboard

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable runner-dashboard.service
sudo systemctl restart runner-dashboard.service

if [[ -x "${SCRIPT_DIR}/deploy/install-runner-maintenance.sh" ]]; then
    RUNNER_SCHEDULE_CONFIG="${SCHEDULE_CONFIG_VAL}" \
    RUNNER_ROOT="${HOME}/actions-runners" \
    RUNNER_USER="${USER}" \
        bash "${SCRIPT_DIR}/deploy/install-runner-maintenance.sh"
else
    warn "Runner maintenance installer not found; skipping cleanup/scheduler timers"
fi

sleep 3
if sudo systemctl is-active --quiet runner-dashboard.service; then
    ok "Dashboard service started and enabled at boot"
    # Quick API check
    HEALTH=$(curl -s --max-time 5 "http://localhost:${PORT}/api/health" \
             | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['github_api'],'| runners:',d['runners_registered'])" \
             2>/dev/null || echo "starting...")
    ok "API health: ${HEALTH}"
else
    warn "Service may still be starting. Check: sudo systemctl status runner-dashboard"
fi

# ── Step 5: Windows port forwarding instructions ─────────────────────────────
header "Step 5/5: Remote Access"

WSL_IP=$(hostname -I | awk '{print $1}')
info "WSL2 IP: ${WSL_IP}"
echo ""
echo "  Run this in Windows PowerShell (Admin) to allow network access:"
echo ""
echo "  ${BOLD}# Port forwarding${NC}"
echo "  netsh interface portproxy add v4tov4 \\"
echo "    listenport=${PORT} listenaddress=0.0.0.0 \\"
echo "    connectport=${PORT} connectaddress=${WSL_IP}"
echo ""
echo "  ${BOLD}# Firewall rule${NC}"
echo "  New-NetFirewallRule -DisplayName 'Runner Dashboard (${MACHINE_NAME})' \\"
echo "    -Direction Inbound -LocalPort ${PORT} -Protocol TCP -Action Allow"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
header "Setup Complete — ${MACHINE_NAME}"
echo ""
echo "  Dashboard:   http://localhost:${PORT}"
echo "  Health:      http://localhost:${PORT}/api/health"
echo "  Queue:       http://localhost:${PORT}/api/queue"
echo "  Fleet:       http://localhost:${PORT}/api/fleet/nodes"
echo "  Schedule:    http://localhost:${PORT}/api/fleet/schedule"
echo ""
echo "  Service management:"
echo "    sudo systemctl status runner-dashboard"
echo "    sudo systemctl restart runner-dashboard"
echo "    sudo journalctl -u runner-dashboard -f"
echo ""
if [[ "${MACHINE_ROLE}" == "hub" ]]; then
    echo "  ${BOLD}Hub mode:${NC} this machine aggregates all fleet nodes."
    echo "  Once other machines are running, re-run with --fleet-nodes to connect them."
    echo ""
fi
echo "========================================================================"

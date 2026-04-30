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
#   ControlTower  (12 configured, hub):
#     ./deploy/setup.sh --runners 12 --machine-name ControlTower --role hub
#
#   Brick-Windows   (1 runner, GPU node):
#     ./deploy/setup.sh --runners 1 --machine-name Brick-Windows
#
#   OGLaptop      (8 runners, node):
#     ./deploy/setup.sh --runners 8 --machine-name OGLaptop
#
#   DeskComputer  (8 installed runners, schedule controls day/night count):
#     ./deploy/setup.sh --runners 8 --machine-name DeskComputer --runner-aliases desktop
#
#   Hub — add fleet nodes after all machines are running (Tailscale IPs):
#     ./deploy/setup.sh --runners 12 --machine-name ControlTower --role hub \
#       --fleet-nodes "Brick-Windows:http://100.96.15.94:8321,OGLaptop:http://100.125.64.108:8321,DeskComputer:http://100.122.254.109:8321"
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
CHECK_ONLY=""
DRY_RUN=""
FORCE_RESTART=""

# preflight() — assert deploy preconditions (issue #402). Idempotent.
preflight() {
    local errors=0 check_path="${DEPLOY_DIR}" avail_gb py_ver py_major py_minor
    mkdir -p "$(dirname "${DEPLOY_DIR}")" 2>/dev/null || true
    while [[ ! -d "${check_path}" && "${check_path}" != "/" ]]; do
        check_path="$(dirname "${check_path}")"
    done
    avail_gb=$(df --output=avail -BG "${check_path}" 2>/dev/null | tail -1 | tr -dc '0-9')
    if [[ -z "${avail_gb}" ]] || (( avail_gb < 1 )); then
        echo "ERROR: insufficient disk space at ${check_path}: ${avail_gb:-unknown}G available, need >1G"
        errors=$((errors + 1))
    fi
    py_ver=$(python3 --version 2>&1 | awk '{print $2}')
    if [[ -z "${py_ver}" ]]; then
        echo "ERROR: python3 not found on PATH"; errors=$((errors + 1))
    else
        py_major=$(echo "${py_ver}" | cut -d. -f1)
        py_minor=$(echo "${py_ver}" | cut -d. -f2)
        if (( py_major < 3 )) || { (( py_major == 3 )) && (( py_minor < 11 )); }; then
            echo "ERROR: python3 ${py_ver} is too old; need Python 3.11+"
            errors=$((errors + 1))
        fi
    fi
    if ss -tlnp 2>/dev/null | grep -q ':8321 '; then
        echo "WARN: port 8321 already bound; the existing process may be replaced"
    fi
    local env_file="${HOME}/.config/runner-dashboard/env" perms
    if [[ -f "${env_file}" ]]; then
        perms=$(stat -c '%a' "${env_file}" 2>/dev/null || stat -f '%Lp' "${env_file}" 2>/dev/null || echo "")
        if [[ "${perms}" != "600" ]]; then
            echo "ERROR: ${env_file} has permissions ${perms}; must be 600"
            errors=$((errors + 1))
        fi
    fi
    if (( errors > 0 )); then
        echo "ERROR: preflight failed with ${errors} error(s)"
        exit 1
    fi
    echo "[ OK ] preflight: disk, python, port, env all good"
}

# Parse flags. --check-only and --dry-run are recognised at any position.
CHECK_ONLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-only)   CHECK_ONLY=1;         shift ;;
        --dry-run)      DRY_RUN=1;            shift ;;
        --runners)      NUM_RUNNERS="$2";     shift 2 ;;
        --machine-name) MACHINE_NAME="$2";    shift 2 ;;
        --display-name) DISPLAY_NAME_VAL="$2"; shift 2 ;;
        --runner-aliases) RUNNER_ALIASES_VAL="$2"; shift 2 ;;
        --role)         MACHINE_ROLE="$2";    shift 2 ;;
        --fleet-nodes)  FLEET_NODES_VAL="$2"; shift 2 ;;
        --hub-url)      HUB_URL_VAL="$2";     shift 2 ;;
        --artifact)     ARTIFACT_SOURCE="$2"; shift 2 ;;
        --schedule-config) SCHEDULE_CONFIG_VAL="$2"; shift 2 ;;
        --force)        FORCE_RESTART=1;      shift ;;
        *) shift ;;
    esac
done

# Run preflight before any mutation. --check-only and --dry-run both exit
# here so the host is never mutated; --dry-run additionally prints the
# would-do summary (Codex P1 PR #483: announcing "no mutations" then
# continuing to install / copy / write secrets is unsafe).
preflight
if [[ -n "$CHECK_ONLY" ]]; then info "--check-only: preflight passed; exiting before any mutation"; exit 0; fi
if [[ -n "$DRY_RUN" ]]; then
    info "[dry-run] preflight passed; aborting before any mutation."
    info "[dry-run] would: deploy artifact, install requirements, write secrets, write systemd unit, install sudoers drop-in, restart service."
    exit 0
fi

# Capture state BEFORE step 2 overwrites deployment.json (Codex P0 PR #483).
PREVIOUS_DEPLOYED_SHA=""
SERVICE_WAS_ACTIVE=""
[[ -f "${DEPLOY_DIR}/deployment.json" ]] && PREVIOUS_DEPLOYED_SHA=$(python3 -c "import json; print(json.load(open('${DEPLOY_DIR}/deployment.json')).get('git_sha',''))" 2>/dev/null || echo "")
systemctl is-active --quiet runner-dashboard.service 2>/dev/null && SERVICE_WAS_ACTIVE=1

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

    # Deploy launcher scripts for PWA recovery
    cp "${SCRIPT_DIR}/deploy/launcher.sh" "${DEPLOY_DIR}/launcher.sh"
    sed -i 's/\r$//' "${DEPLOY_DIR}/launcher.sh"
    chmod +x "${DEPLOY_DIR}/launcher.sh"
    cp "${SCRIPT_DIR}/deploy/launcher.ps1" "${DEPLOY_DIR}/launcher.ps1"
    cp "${SCRIPT_DIR}/deploy/register-protocol.ps1" "${DEPLOY_DIR}/register-protocol.ps1"

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
    # Atomic sudoers replacement (issue #402): write to tmp, validate with
    # `visudo -c -f`, only then move into place. Validation failure leaves
    # any existing sudoers file untouched.
    tmp=$(mktemp)
    echo "${SUDOERS_LINE}" > "$tmp"
    if visudo -c -f "$tmp" >/dev/null 2>&1; then
        sudo install -m 0440 "$tmp" "${SUDOERS_FILE}"
        rm -f "$tmp"
        ok "Sudoers rule installed (start/stop runners without password)"
    else
        rm -f "$tmp"
        echo "ERROR: sudoers validation failed; existing file untouched"
        exit 1
    fi
fi

# ── Step 4: Systemd service ──────────────────────────────────────────────────
header "Step 4/5: Systemd Service (Auto-Start)"

SERVICE_FILE="/etc/systemd/system/runner-dashboard.service"

TEMPLATE_FILE="${SCRIPT_DIR}/deploy/runner-dashboard.service"

if [[ ! -f "${TEMPLATE_FILE}" ]]; then
    fail "Template not found at ${TEMPLATE_FILE}"
fi

# Static parity markers for the service template rendered below. Keep this list
# in sync with deploy/runner-dashboard.service so tests catch installer drift.
# ExecStart=${PYTHON_BIN} ${DEPLOY_DIR}/backend/server.py
# RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
# RestrictNamespaces=true
# CapabilityBoundingSet=
# SystemCallFilter=@system-service
# LockPersonality=true
# MemoryDenyWriteExecute=true
# ProtectHostname=true
# ProtectClock=true
# ProtectProc=invisible
# MemoryMax=2G
# CPUQuota=200%
# TasksMax=512
# WatchdogSec=120
sed -e "s|Description=D-sorganization Runner Dashboard|Description=D-sorganization Runner Dashboard (${MACHINE_NAME})|g" \
    -e "s|YOUR_USER|${USER}|g" \
    -e "s|/home/YOUR_USER|${HOME}|g" \
    -e "s|/usr/bin/python3.11|${PYTHON_BIN}|g" \
    -e "s|NUM_RUNNERS=12|NUM_RUNNERS=${NUM_RUNNERS}|g" \
    -e "s|DASHBOARD_PORT=8321|DASHBOARD_PORT=${PORT}|g" \
    -e "s|DISPLAY_NAME=ControlTower|DISPLAY_NAME=${DISPLAY_NAME_VAL}|g" \
    -e "s|RUNNER_ALIASES=controltower,control-tower-runner-monitoring|RUNNER_ALIASES=${RUNNER_ALIASES_VAL}|g" \
    -e "s|RUNNER_SCHEDULE_CONFIG=.*|RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG_VAL}|g" \
    -e "s|MACHINE_ROLE=hub|MACHINE_ROLE=${MACHINE_ROLE}|g" \
    "${TEMPLATE_FILE}" | sudo tee "${SERVICE_FILE}" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable runner-dashboard.service

# Version-skip restart (issue #402): only skip the restart when the
# *previously*-deployed git_sha (captured before step 2 overwrote
# deployment.json) matches the current checkout AND the service is already
# active. Without the active-service check, a fresh install would also match
# (deployment.json was just written) and we'd leave the unit inactive — that
# was the Codex P0 risk on PR #483.
SKIP_RESTART=""
if [[ -z "$FORCE_RESTART" && -n "$SERVICE_WAS_ACTIVE" && -n "$PREVIOUS_DEPLOYED_SHA" ]]; then
    current_sha=$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo "")
    if [[ -n "${current_sha}" && "${PREVIOUS_DEPLOYED_SHA}" == "${current_sha}" ]]; then
        info "Service active and deployed git_sha (${PREVIOUS_DEPLOYED_SHA:0:7}) matches current checkout; skipping restart (use --force to override)"
        SKIP_RESTART=1
    fi
fi

if [[ -z "$SKIP_RESTART" ]]; then
    sudo systemctl restart runner-dashboard.service
fi

# Install tightly-scoped sudoers drop-in (issue #391 AC-6).
SUDOERS_SRC="${SCRIPT_DIR}/deploy/sudoers.d-runner-dashboard"
SUDOERS_DEST="/etc/sudoers.d/runner-dashboard"
if [[ -f "${SUDOERS_SRC}" ]]; then
    SUDOERS_RENDERED="$(mktemp)"
    trap 'rm -f "${SUDOERS_RENDERED}"' EXIT
    sed "s|YOUR_USER|${USER}|g" "${SUDOERS_SRC}" > "${SUDOERS_RENDERED}"
    if visudo -cf "${SUDOERS_RENDERED}"; then
        sudo install -m 0440 "${SUDOERS_RENDERED}" "${SUDOERS_DEST}"
        echo "  installed ${SUDOERS_DEST}"
    else
        warn "sudoers drop-in failed visudo check; skipping install (manual action required)"
    fi
    rm -f "${SUDOERS_RENDERED}"
    trap - EXIT
else
    warn "sudoers template not found at ${SUDOERS_SRC}; skipping"
fi

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

# ── Step 5a: Register PWA launcher protocol (Windows only) ─────────────────
if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
    header "Step 5a/5: Register PWA Launcher (Windows)"
    info "Registering runner-dashboard:// protocol handler for PWA recovery..."

    # Make scripts executable
    chmod +x "${DEPLOY_DIR}/launcher.sh" 2>/dev/null || true

    # Call PowerShell script from Windows to register protocol
    if command -v powershell.exe &>/dev/null; then
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${DEPLOY_DIR}/register-protocol.ps1" 2>/dev/null || {
            warn "Protocol registration requires Windows PowerShell (Admin mode)"
            warn "Run this in Windows PowerShell as Administrator:"
            echo ""
            echo "    ${BOLD}powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"$(wslpath -w "${DEPLOY_DIR}")\register-protocol.ps1\"${NC}"
            echo ""
        }
    else
        warn "PowerShell not found; protocol registration skipped"
        warn "To register later, run in Windows PowerShell as Administrator:"
        echo ""
        echo "    ${BOLD}powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"$(wslpath -w "${DEPLOY_DIR}")\register-protocol.ps1\"${NC}"
        echo ""
    fi
else
    header "Step 5a/5: Register PWA Launcher (macOS/Linux)"
    info "Making launcher script executable..."
    chmod +x "${DEPLOY_DIR}/launcher.sh"
    ok "Launcher ready at: ${DEPLOY_DIR}/launcher.sh"
fi

# ── Step 5b: Windows port forwarding instructions ─────────────────────────────
header "Step 5b/5: Remote Access"

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

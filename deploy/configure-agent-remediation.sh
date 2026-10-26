#!/usr/bin/env bash
# ==============================================================================
# configure-agent-remediation.sh — Secure local setup for dashboard agent
# remediation providers on a host machine.
#
# This script:
#   1. Optionally installs Jules CLI and/or Cline CLI
#   2. Securely prompts for JULES_API_KEY without echoing it
#   3. Writes only to the private dashboard env file
#   4. Sets RUNNER_DASHBOARD_REPO_ROOT for split deploy/checkouts
#   5. Refreshes PATH for systemd so locally installed CLIs are visible
#   6. Restarts the dashboard service and verifies provider availability
#
# It is intended to be safe to rerun on multiple machines.
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="${RUNNER_DASHBOARD_SERVICE:-runner-dashboard}"
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-$HOME/.config/runner-dashboard/env}"
REPO_ROOT="${RUNNER_DASHBOARD_REPO_ROOT_OVERRIDE:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
INSTALL_JULES=0
INSTALL_CLINE=0
RESTART_SERVICE=1
PROMPT_FOR_JULES_KEY=1

usage() {
    cat <<'EOF'
Usage:
  bash runner-dashboard/deploy/configure-agent-remediation.sh [options]

Options:
  --install-jules        Install Jules CLI with npm
  --install-cline        Install Cline CLI with npm
  --no-jules-api-key     Do not prompt for JULES_API_KEY
  --repo-root <path>     Override RUNNER_DASHBOARD_REPO_ROOT
  --env-file <path>      Override dashboard env file
  --service <name>       Override dashboard service name
  --no-restart           Do not restart the dashboard service
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-jules) INSTALL_JULES=1; shift ;;
        --install-cline) INSTALL_CLINE=1; shift ;;
        --no-jules-api-key) PROMPT_FOR_JULES_KEY=0; shift ;;
        --repo-root) REPO_ROOT="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --service) SERVICE_NAME="$2"; shift 2 ;;
        --no-restart) RESTART_SERVICE=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

ensure_env_file() {
    install -d -m 700 "$(dirname "${ENV_FILE}")"
    touch "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
}

set_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    trap 'rm -f "${tmp}"' RETURN
    python3 - "${ENV_FILE}" "${key}" "${value}" >"${tmp}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = []
if path.exists():
    lines = path.read_text(encoding="utf-8").splitlines()

prefix = f"{key}="
filtered = [line for line in lines if not line.startswith(prefix)]
filtered.append(f"{key}={value}")
sys.stdout.write("\n".join(filtered) + "\n")
PY
    cat "${tmp}" > "${ENV_FILE}"
    rm -f "${tmp}"
    trap - RETURN
}

require_npm() {
    command -v npm >/dev/null 2>&1 || fail "npm is required for CLI installation."
}

install_cli() {
    local package="$1"
    local label="$2"
    require_npm
    info "Installing ${label}..."
    npm install -g "${package}"
    ok "${label} installed"
}

ensure_env_file

if [[ "${INSTALL_JULES}" -eq 1 ]]; then
    install_cli "@google/jules" "Jules CLI"
fi

if [[ "${INSTALL_CLINE}" -eq 1 ]]; then
    install_cli "cline" "Cline CLI"
fi

DEFAULT_PATH="/usr/lib/wsl/lib:${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
CURRENT_PATH="$(grep '^PATH=' "${ENV_FILE}" 2>/dev/null | cut -d= -f2- || true)"
SERVICE_PATH="${CURRENT_PATH:-$DEFAULT_PATH}"

for candidate in \
    "${HOME}/.nvm/versions/node"/*/bin \
    "${HOME}/.local/bin" \
    "/usr/local/bin"
do
    [[ -d "${candidate}" ]] || continue
    if [[ ":${SERVICE_PATH}:" != *":${candidate}:"* ]]; then
        SERVICE_PATH="${SERVICE_PATH}:${candidate}"
    fi
done

set_env_var "PATH" "${SERVICE_PATH}"
set_env_var "RUNNER_DASHBOARD_REPO_ROOT" "${REPO_ROOT}"

if [[ "${PROMPT_FOR_JULES_KEY}" -eq 1 ]]; then
    read -r -s -p "Jules API key (leave blank to skip): " JULES_API_KEY_INPUT
    echo
    if [[ -n "${JULES_API_KEY_INPUT}" ]]; then
        set_env_var "JULES_API_KEY" "${JULES_API_KEY_INPUT}"
        unset JULES_API_KEY_INPUT
        ok "Stored JULES_API_KEY in private dashboard env file"
    else
        warn "Skipped JULES_API_KEY update"
    fi
fi

if [[ "${RESTART_SERVICE}" -eq 1 ]]; then
    info "Restarting ${SERVICE_NAME}..."
    sudo systemctl restart "${SERVICE_NAME}"
    sleep 3
    sudo systemctl is-active --quiet "${SERVICE_NAME}" || fail "${SERVICE_NAME} failed to start"
    ok "${SERVICE_NAME} restarted"
fi

if command -v curl >/dev/null 2>&1; then
    info "Checking remediation provider availability..."
    curl -s --max-time 15 http://localhost:8321/api/agent-remediation/config || \
        warn "Could not fetch remediation config from the local dashboard"
    echo
fi

if command -v jules >/dev/null 2>&1; then
    info "Jules CLI detected at $(command -v jules)"
    warn "If you have not completed browser/device auth yet, run: jules login"
fi

if command -v cline >/dev/null 2>&1; then
    info "Cline CLI detected at $(command -v cline)"
    warn "If you have not configured it yet, run: cline auth"
fi

ok "Agent remediation host setup complete."

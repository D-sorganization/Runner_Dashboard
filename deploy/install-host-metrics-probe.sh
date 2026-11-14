#!/usr/bin/env bash
# Install the host-metrics-probe systemd timer (#4309, #4443).
#
# Usage:
#   bash deploy/install-host-metrics-probe.sh
#
# Idempotent; follows the same system/user scope detection as install-node-auto-deploy.sh.
#
# Privilege notes:
#   - **User scope** (Brick and other user-service hubs): no sudo; uses
#     systemctl --user and ~/.config/systemd/user/.
#   - **System scope** (DeskComputer / ControlTower / OGLaptop WSL hubs): requires
#     passwordless sudo for install/tee/cp under /etc/systemd/system and
#     systemctl enable/start.
#   - On a Windows host, run this inside the dashboard WSL distro (or use
#     deploy/windows/install-host-metrics-probe-wsl.ps1 from an elevated shell).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/auto-deploy-env.sh
source "${SCRIPT_DIR}/auto-deploy-env.sh"
# shellcheck source=deploy/resolve-auto-deploy-python.sh
source "${SCRIPT_DIR}/resolve-auto-deploy-python.sh"

DASHBOARD_USER=""
DASHBOARD_HOME=""
DEPLOY_DIR=""
STATE_DIR=""

auto_deploy_bind_paths() {
  DASHBOARD_USER="$(auto_deploy_dashboard_user)"
  DASHBOARD_HOME="$(auto_deploy_resolve_home)"
  DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${DASHBOARD_HOME}/actions-runners/dashboard}"
  STATE_DIR="${RUNNER_DASHBOARD_STATE_DIR:-${DASHBOARD_HOME}/.config/runner-dashboard}"
}

detect_scope() {
  if systemctl is-active runner-dashboard.service &>/dev/null || \
     systemctl is-enabled runner-dashboard.service &>/dev/null 2>&1; then
    echo system
    return
  fi
  if systemctl --user is-active runner-dashboard.service &>/dev/null || \
     systemctl --user is-enabled runner-dashboard.service &>/dev/null 2>&1; then
    echo user
    return
  fi
  echo system
}

render_service_unit() {
  local scope="$1"
  local python_bin="$2"
  sed -e "s|@DASHBOARD_USER@|${DASHBOARD_USER}|g" \
      -e "s|@DASHBOARD_HOME@|${DASHBOARD_HOME}|g" \
      -e "s|@STATE_DIR@|${STATE_DIR}|g" \
      -e "s|@REPO_ROOT@|${DEPLOY_DIR}|g" \
      -e "s|@PYTHON_BIN@|${python_bin}|g" \
      "${SCRIPT_DIR}/systemd/runner-dashboard-host-metrics-probe.service" \
    | if [[ "${scope}" == user ]]; then sed '/^User=/d'; else cat; fi
}

install_host_metrics_probe() {
  auto_deploy_bind_paths
  local SCOPE PYTHON_BIN TIMER_SRC
  SCOPE="$(detect_scope)"
  PYTHON_BIN="$(resolve_auto_deploy_python "${DEPLOY_DIR}" "${SCOPE}" 2>/dev/null || command -v python3)"
  TIMER_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-host-metrics-probe.timer"
  local PROBE_SRC="${SCRIPT_DIR}/host-metrics-probe.py"
  local PROBE_DEST="${DEPLOY_DIR}/deploy/host-metrics-probe.py"

  [[ -f "${PROBE_SRC}" ]] || {
    echo "Refusing host-metrics-probe install: ${PROBE_SRC} missing" >&2
    return 1
  }
  install -d -m 0755 "${DEPLOY_DIR}/deploy"
  install -m 0755 "${PROBE_SRC}" "${PROBE_DEST}"

  if [[ "${SCOPE}" == user ]]; then
    local UNIT_DIR="${DASHBOARD_HOME}/.config/systemd/user"
    mkdir -p "${UNIT_DIR}"
    render_service_unit user "${PYTHON_BIN}" > "${UNIT_DIR}/runner-dashboard-host-metrics-probe.service"
    cp "${TIMER_SRC}" "${UNIT_DIR}/runner-dashboard-host-metrics-probe.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now runner-dashboard-host-metrics-probe.timer
    systemctl --user start runner-dashboard-host-metrics-probe.service || true
    echo "Installed user timer: runner-dashboard-host-metrics-probe.timer (no sudo)"
  else
    echo "Installing system units (sudo required for /etc/systemd/system)..." >&2
    sudo install -d -m 0755 /etc/systemd/system
    render_service_unit system "${PYTHON_BIN}" | sudo tee /etc/systemd/system/runner-dashboard-host-metrics-probe.service >/dev/null
    sudo cp "${TIMER_SRC}" /etc/systemd/system/runner-dashboard-host-metrics-probe.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now runner-dashboard-host-metrics-probe.timer
    sudo systemctl start runner-dashboard-host-metrics-probe.service || true
    echo "Installed system timer: runner-dashboard-host-metrics-probe.timer (sudo)"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  install_host_metrics_probe
fi

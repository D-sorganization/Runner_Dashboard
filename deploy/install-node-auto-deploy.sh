#!/usr/bin/env bash
# Install the node-local auto-deploy systemd timer (one command per fleet node).
#
# Usage:
#   bash deploy/install-node-auto-deploy.sh
#
# Hub (WSL system service):
#   Installs system units under /etc/systemd/system/.
#
# Brick / user-service nodes:
#   Installs user units under ~/.config/systemd/user/ (no sudo system service).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/auto-deploy-env.sh
source "${SCRIPT_DIR}/auto-deploy-env.sh"
# shellcheck source=deploy/resolve-auto-deploy-python.sh
source "${SCRIPT_DIR}/resolve-auto-deploy-python.sh"

DASHBOARD_USER=""
DASHBOARD_HOME=""
DEPLOY_DIR=""
ENV_FILE=""

auto_deploy_bind_paths() {
  DASHBOARD_USER="$(auto_deploy_dashboard_user)"
  DASHBOARD_HOME="$(auto_deploy_resolve_home)"
  DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${DASHBOARD_HOME}/actions-runners/dashboard}"
  ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"
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
  sed -e "s|@DEPLOY_DIR@|${DEPLOY_DIR}|g" \
      -e "s|@ENV_FILE@|${ENV_FILE}|g" \
      -e "s|@DASHBOARD_USER@|${DASHBOARD_USER}|g" \
      -e "s|@DASHBOARD_HOME@|${DASHBOARD_HOME}|g" \
      -e "s|@PYTHON_BIN@|${python_bin}|g" \
      "${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.service" \
    | if [[ "${scope}" == user ]]; then sed '/^User=/d'; else cat; fi
}

install_node_auto_deploy() {
  auto_deploy_bind_paths
  local SCOPE PYTHON_BIN TIMER_SRC
  SCOPE="$(detect_scope)"
  PYTHON_BIN="$(resolve_auto_deploy_python "${DEPLOY_DIR}" "${SCOPE}")"
  require_auto_deploy_python_version "${PYTHON_BIN}" || {
    echo "Refusing to install auto-deploy: dashboard Python must be >=3.11 (got ${PYTHON_BIN})" >&2
    exit 1
  }
  TIMER_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.timer"

  install -d -m 0755 "${DEPLOY_DIR}/deploy"
  if [[ "$(realpath "${SCRIPT_DIR}")" != "$(realpath "${DEPLOY_DIR}/deploy")" ]]; then
    install -m 0644 "${SCRIPT_DIR}/auto-deploy-env.sh" "${DEPLOY_DIR}/deploy/auto-deploy-env.sh"
    install -m 0644 "${SCRIPT_DIR}/resolve-auto-deploy-python.sh" "${DEPLOY_DIR}/deploy/resolve-auto-deploy-python.sh"
    install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.sh" "${DEPLOY_DIR}/deploy/node-auto-deploy.sh"
    install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.py" "${DEPLOY_DIR}/deploy/node-auto-deploy.py"
    rsync -a --delete "${SCRIPT_DIR}/node_auto_deploy/" "${DEPLOY_DIR}/deploy/node_auto_deploy/"
  fi

  if [[ "${SCOPE}" == user ]]; then
    UNIT_DIR="${DASHBOARD_HOME}/.config/systemd/user"
    mkdir -p "${UNIT_DIR}"
    render_service_unit user "${PYTHON_BIN}" > "${UNIT_DIR}/runner-dashboard-auto-deploy.service"
    cp "${TIMER_SRC}" "${UNIT_DIR}/runner-dashboard-auto-deploy.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now runner-dashboard-auto-deploy.timer
    echo "Installed user timer: systemctl --user status runner-dashboard-auto-deploy.timer"
  else
    sudo install -d -m 0755 /etc/systemd/system
    render_service_unit system "${PYTHON_BIN}" | sudo tee /etc/systemd/system/runner-dashboard-auto-deploy.service >/dev/null
    sudo cp "${TIMER_SRC}" /etc/systemd/system/runner-dashboard-auto-deploy.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now runner-dashboard-auto-deploy.timer
    echo "Installed system timer: sudo systemctl status runner-dashboard-auto-deploy.timer"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  install_node_auto_deploy
fi

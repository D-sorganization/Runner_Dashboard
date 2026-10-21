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

DASHBOARD_USER="$(auto_deploy_dashboard_user)"
DASHBOARD_HOME="$(auto_deploy_resolve_home)"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${DASHBOARD_HOME}/actions-runners/dashboard}"
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"

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
  sed -e "s|@DEPLOY_DIR@|${DEPLOY_DIR}|g" \
      -e "s|@ENV_FILE@|${ENV_FILE}|g" \
      -e "s|@DASHBOARD_USER@|${DASHBOARD_USER}|g" \
      -e "s|@DASHBOARD_HOME@|${DASHBOARD_HOME}|g" \
      "${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.service" \
    | if [[ "${scope}" == user ]]; then sed '/^User=/d'; else cat; fi
}

SCOPE="$(detect_scope)"
TIMER_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.timer"

install -d -m 0755 "${DEPLOY_DIR}/deploy"
install -m 0644 "${SCRIPT_DIR}/auto-deploy-env.sh" "${DEPLOY_DIR}/deploy/auto-deploy-env.sh"
install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.sh" "${DEPLOY_DIR}/deploy/node-auto-deploy.sh"
install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.py" "${DEPLOY_DIR}/deploy/node-auto-deploy.py"
rsync -a --delete "${SCRIPT_DIR}/node_auto_deploy/" "${DEPLOY_DIR}/deploy/node_auto_deploy/"

if [[ "${SCOPE}" == user ]]; then
  UNIT_DIR="${DASHBOARD_HOME}/.config/systemd/user"
  mkdir -p "${UNIT_DIR}"
  render_service_unit user > "${UNIT_DIR}/runner-dashboard-auto-deploy.service"
  cp "${TIMER_SRC}" "${UNIT_DIR}/runner-dashboard-auto-deploy.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now runner-dashboard-auto-deploy.timer
  echo "Installed user timer: systemctl --user status runner-dashboard-auto-deploy.timer"
else
  sudo install -d -m 0755 /etc/systemd/system
  render_service_unit system | sudo tee /etc/systemd/system/runner-dashboard-auto-deploy.service >/dev/null
  sudo cp "${TIMER_SRC}" /etc/systemd/system/runner-dashboard-auto-deploy.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now runner-dashboard-auto-deploy.timer
  echo "Installed system timer: sudo systemctl status runner-dashboard-auto-deploy.timer"
fi

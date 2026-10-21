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
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${HOME}/actions-runners/dashboard}"
ENV_FILE="${HOME}/.config/runner-dashboard/env"

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

SCOPE="$(detect_scope)"
SERVICE_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.service"
TIMER_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-auto-deploy.timer"

install -d -m 0755 "${DEPLOY_DIR}/deploy"
install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.sh" "${DEPLOY_DIR}/deploy/node-auto-deploy.sh"
install -m 0755 "${SCRIPT_DIR}/node-auto-deploy.py" "${DEPLOY_DIR}/deploy/node-auto-deploy.py"
rsync -a --delete "${SCRIPT_DIR}/node_auto_deploy/" "${DEPLOY_DIR}/deploy/node_auto_deploy/"

if [[ "${SCOPE}" == user ]]; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "${UNIT_DIR}"
  sed "s|@DEPLOY_DIR@|${DEPLOY_DIR}|g;s|@ENV_FILE@|${ENV_FILE}|g" "${SERVICE_SRC}" > "${UNIT_DIR}/runner-dashboard-auto-deploy.service"
  cp "${TIMER_SRC}" "${UNIT_DIR}/runner-dashboard-auto-deploy.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now runner-dashboard-auto-deploy.timer
  echo "Installed user timer: systemctl --user status runner-dashboard-auto-deploy.timer"
else
  sudo install -d -m 0755 /etc/systemd/system
  sed "s|@DEPLOY_DIR@|${DEPLOY_DIR}|g;s|@ENV_FILE@|${ENV_FILE}|g" "${SERVICE_SRC}" | \
    sudo tee /etc/systemd/system/runner-dashboard-auto-deploy.service >/dev/null
  sudo cp "${TIMER_SRC}" /etc/systemd/system/runner-dashboard-auto-deploy.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now runner-dashboard-auto-deploy.timer
  echo "Installed system timer: sudo systemctl status runner-dashboard-auto-deploy.timer"
fi

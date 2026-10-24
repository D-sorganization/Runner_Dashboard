#!/usr/bin/env bash
# Install the node-local self-heal & Windows<->WSL credential mirroring systemd timer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/auto-deploy-env.sh"
source "${SCRIPT_DIR}/resolve-auto-deploy-python.sh"

DASHBOARD_USER="$(auto_deploy_dashboard_user)"
DASHBOARD_HOME="$(auto_deploy_resolve_home)"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${DASHBOARD_HOME}/actions-runners/dashboard}"
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"
SELF_HEAL_DIR="${DASHBOARD_HOME}/.config/runner-dashboard/self-heal"

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
PYTHON_BIN="$(resolve_auto_deploy_python "${DEPLOY_DIR}" "${SCOPE}" 2>/dev/null || command -v python3)"

install -d -m 0755 "${SELF_HEAL_DIR}"
install -m 0755 "${SCRIPT_DIR}/node-self-heal.sh" "${SELF_HEAL_DIR}/node-self-heal.sh"
install -m 0755 "${SCRIPT_DIR}/sync_provider_credentials.py" "${SELF_HEAL_DIR}/sync_provider_credentials.py"

render_unit() {
  sed -e "s|@DEPLOY_DIR@|${DEPLOY_DIR}|g" \
      -e "s|@ENV_FILE@|${ENV_FILE}|g" \
      -e "s|@DASHBOARD_USER@|${DASHBOARD_USER}|g" \
      -e "s|@DASHBOARD_HOME@|${DASHBOARD_HOME}|g" \
      -e "s|@PYTHON_BIN@|${PYTHON_BIN}|g" \
      "${SCRIPT_DIR}/systemd/runner-dashboard-self-heal.service" \
    | if [[ "${SCOPE}" == user ]]; then sed '/^User=/d'; else cat; fi
}

TIMER_SRC="${SCRIPT_DIR}/systemd/runner-dashboard-self-heal.timer"

if [[ "${SCOPE}" == user ]]; then
  UNIT_DIR="${DASHBOARD_HOME}/.config/systemd/user"
  mkdir -p "${UNIT_DIR}"
  render_unit > "${UNIT_DIR}/runner-dashboard-self-heal.service"
  cp "${TIMER_SRC}" "${UNIT_DIR}/runner-dashboard-self-heal.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now runner-dashboard-self-heal.timer
  systemctl --user start runner-dashboard-self-heal.service
  echo "Installed user timer: runner-dashboard-self-heal.timer"
else
  sudo install -d -m 0755 /etc/systemd/system
  render_unit | sudo tee /etc/systemd/system/runner-dashboard-self-heal.service >/dev/null
  sudo cp "${TIMER_SRC}" /etc/systemd/system/runner-dashboard-self-heal.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now runner-dashboard-self-heal.timer
  sudo systemctl start runner-dashboard-self-heal.service
  echo "Installed system timer: runner-dashboard-self-heal.timer"
fi

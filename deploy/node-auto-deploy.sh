#!/usr/bin/env bash
# Node-local pull-based Runner Dashboard auto-deploy (systemd timer entrypoint).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/auto-deploy-env.sh
source "${SCRIPT_DIR}/auto-deploy-env.sh"
# shellcheck source=deploy/resolve-auto-deploy-python.sh
source "${SCRIPT_DIR}/resolve-auto-deploy-python.sh"

_RESOLVED_HOME="$(auto_deploy_resolve_home)"
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${_RESOLVED_HOME}/.config/runner-dashboard/env}"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${_RESOLVED_HOME}/actions-runners/dashboard}"
PORT="${DASHBOARD_PORT:-8321}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

PYTHON_BIN="$(resolve_auto_deploy_python "${DEPLOY_DIR}")"
require_auto_deploy_python_version "${PYTHON_BIN}"

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/node-auto-deploy.py" \
  --deploy-dir "${DEPLOY_DIR}" \
  --env-file "${ENV_FILE}" \
  --port "${PORT}" \
  "$@"

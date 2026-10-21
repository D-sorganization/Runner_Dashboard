#!/usr/bin/env bash
# Node-local pull-based Runner Dashboard auto-deploy (systemd timer entrypoint).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${HOME}/.config/runner-dashboard/env}"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${HOME}/actions-runners/dashboard}"
PORT="${DASHBOARD_PORT:-8321}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

exec python3 "${SCRIPT_DIR}/node-auto-deploy.py" \
  --deploy-dir "${DEPLOY_DIR}" \
  --env-file "${ENV_FILE}" \
  --port "${PORT}" \
  "$@"

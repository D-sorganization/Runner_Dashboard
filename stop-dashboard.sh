#!/usr/bin/env bash
# Stop the Runner Dashboard started via ./start-dashboard.sh --bg (#4268).
# Only stops the PID recorded in the pid file after verifying process identity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/dashboard-process.sh
source "${SCRIPT_DIR}/scripts/dashboard-process.sh"

PID_FILE="${RUNNER_DASHBOARD_PID_FILE:-${SCRIPT_DIR}/.runner-dashboard.pid}"

if [[ ! -f "${PID_FILE}" ]]; then
    echo "Dashboard is not running (no pid file at ${PID_FILE})"
    exit 0
fi

PID="$(tr -d '[:space:]' < "${PID_FILE}")"
if [[ -z "${PID}" ]] || [[ ! "${PID}" =~ ^[0-9]+$ ]]; then
    echo "[WARN] Invalid pid file contents; removing ${PID_FILE}" >&2
    rm -f "${PID_FILE}"
    exit 1
fi

if ! dashboard_pid_is_alive "${PID}"; then
    echo "Dashboard is not running (stale pid file: ${PID})"
    rm -f "${PID_FILE}"
    exit 0
fi

if ! dashboard_pid_is_dashboard "${PID}" "${SCRIPT_DIR}"; then
    echo "[ERROR] Refusing to kill PID ${PID}: process does not match this repo's dashboard" >&2
    rm -f "${PID_FILE}"
    exit 1
fi

kill "${PID}" 2>/dev/null || true
for _ in $(seq 1 15); do
    dashboard_pid_is_alive "${PID}" || break
    sleep 1
done
if dashboard_pid_is_alive "${PID}"; then
    kill -9 "${PID}" 2>/dev/null || true
fi

rm -f "${PID_FILE}"
echo "Dashboard stopped (PID: ${PID})"

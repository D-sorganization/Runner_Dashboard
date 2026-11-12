#!/usr/bin/env bash
# One backlog-push claude lane, detached from the panel-relay oneshot (#4098).
# Usage: backlog-push-lane.sh SESSION LOG_PATH PROMPT_PATH PIDFILE [EXITFILE]
set -euo pipefail

SESSION="${1:?session}"
LOG_PATH="${2:?log}"
PROMPT_PATH="${3:?prompt}"
PIDFILE="${4:?pidfile}"
EXITFILE="${5:-${PIDFILE%.pid}.exit}"
RECEIPT_FILE="${RECEIPT_FILE:-${PIDFILE%.pid}.receipt}"

ROOT="${RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"

unset GH_TOKEN GITHUB_TOKEN
export FLEET_AGENT_SESSION=1
export PATH="${HOME}/.local/bin:${PATH}"

REFRESH_S="${BARB_BACKLOG_PUSH_GH_REFRESH_S:-2700}"
(
    trap 'exit 0' TERM INT
    while true; do
        sleep "${REFRESH_S}" &
        SLEEP_PID=$!
        trap 'kill "${SLEEP_PID}" 2>/dev/null || true; exit 0' TERM INT
        wait "${SLEEP_PID}" 2>/dev/null || true
        gh api user -q .login >/dev/null 2>&1 || true
    done
) &
REFRESH_PID=$!

stop_refresh() {
    if [[ -n "${REFRESH_PID:-}" ]]; then
        kill "${REFRESH_PID}" 2>/dev/null || true
        wait "${REFRESH_PID}" 2>/dev/null || true
        REFRESH_PID=""
    fi
}

cleanup() {
    local sig="${1:-}"
    stop_refresh
    if [[ -n "${sig}" ]]; then
        trap - "${sig}" EXIT
        kill -s "${sig}" $$
    fi
}
trap 'cleanup INT' INT
trap 'cleanup TERM' TERM
trap 'cleanup' EXIT

cd "${ROOT}"
exec >>"${LOG_PATH}" 2>&1
echo "$(date -Is) backlog-push lane start session=${SESSION}"

CLAUDE=(claude -p --output-format stream-json --verbose --permission-mode dontAsk --permission-prompts none)
"${CLAUDE[@]}" <"${PROMPT_PATH}" &
WORKER_PID=$!
echo "${WORKER_PID}" >"${PIDFILE}"

WORKER_STATUS=0
wait "${WORKER_PID}" || WORKER_STATUS=$?
stop_refresh

# Write exit status and outcome receipt atomically
if [[ -n "${EXITFILE:-}" ]]; then
    tmp_exit="${EXITFILE}.tmp.$$"
    echo "${WORKER_STATUS}" >"${tmp_exit}"
    mv -f "${tmp_exit}" "${EXITFILE}"
fi

if [[ -n "${RECEIPT_FILE:-}" ]]; then
    tmp_rcpt="${RECEIPT_FILE}.tmp.$$"
    printf '{"session":"%s","exit_code":%d,"pid":%d,"timestamp":"%s"}\n' \
        "${SESSION}" "${WORKER_STATUS}" "${WORKER_PID:-0}" "$(date -Is)" >"${tmp_rcpt}"
    mv -f "${tmp_rcpt}" "${RECEIPT_FILE}"
fi

trap - EXIT INT TERM
exit "${WORKER_STATUS}"

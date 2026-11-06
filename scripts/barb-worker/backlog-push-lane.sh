#!/usr/bin/env bash
# One backlog-push claude lane, detached from the panel-relay oneshot (#4098).
# Usage: backlog-push-lane.sh SESSION LOG_PATH PROMPT_PATH PIDFILE
set -euo pipefail

SESSION="${1:?session}"
LOG_PATH="${2:?log}"
PROMPT_PATH="${3:?prompt}"
PIDFILE="${4:?pidfile}"

ROOT="${RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"

unset GH_TOKEN GITHUB_TOKEN
export FLEET_AGENT_SESSION=1
export PATH="${HOME}/.local/bin:${PATH}"

REFRESH_S="${BARB_BACKLOG_PUSH_GH_REFRESH_S:-2700}"
(
    while sleep "${REFRESH_S}"; do
        gh api user -q .login >/dev/null 2>&1 || true
    done
) &
REFRESH_PID=$!
trap 'kill "${REFRESH_PID}" 2>/dev/null || true' EXIT

cd "${ROOT}"
exec >>"${LOG_PATH}" 2>&1
echo "$(date -Is) backlog-push lane start session=${SESSION}"

CLAUDE=(claude -p --output-format stream-json --verbose --permission-mode dontAsk --permission-prompts none)
"${CLAUDE[@]}" <"${PROMPT_PATH}" &
echo $! >"${PIDFILE}"
wait

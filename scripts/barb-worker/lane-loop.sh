#!/usr/bin/env bash
# Barb-worker lane loop: poll rd-threads inbox via barb_cli (#3072).
set -euo pipefail

ROOT="${RUNNER_DASHBOARD_ROOT:-${HOME}/src/Runner_Dashboard}"
INTERVAL="${BARB_LANE_INTERVAL_S:-30}"

# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"

cd "${ROOT}"
# Push wake (#3682): hold the agent-bus SSE stream and cut the poll sleep short on new mail.
AGENT="${FLEET_AGENT:-}"
WAKE="${HOME}/.cache/barb-lane/bus.wake"
mkdir -p "$(dirname "${WAKE}")"
if [[ -n "${AGENT}" && -r "${TOKEN_FILE}" && "${BARB_LANE_PUSH:-1}" == "1" ]]; then
    ROLE="${AGENT#agent-}"
    python3 scripts/agent_bus_listen.py --hub "${FLEET_API_URL}" --token-file "${TOKEN_FILE}" \
        --session "${ROLE}-$(hostname -s)-lane" --register-role "${ROLE}" --provider barb-lane \
        --node "$(hostname -s)" --wake-file "${WAKE}" >>"${HOME}/.cache/barb-lane/listen.log" 2>&1 &
    LISTEN_PID=$!
    trap 'kill "${LISTEN_PID}" 2>/dev/null || true' EXIT
fi
while true; do
    python3 scripts/barb_cli.py inbox '{}' >/dev/null 2>&1 || true
    if [[ -f "${WAKE}" ]]; then
        bash scripts/agent_bus_wait.sh "${WAKE}" "${INTERVAL}" && echo "$(date -Is) push wake" || true
    else
        sleep "${INTERVAL}"
    fi
done

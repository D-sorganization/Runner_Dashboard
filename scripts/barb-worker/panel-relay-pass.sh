#!/usr/bin/env bash
# One barbara relay pass as this lane (#3711): fleet env, hub-minted App token, panel + backlog-push relays.
set -euo pipefail

ROOT="${RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"
cd "${ROOT}"

# Writes must land as the fleet App bot, never a personal login (GOV-1).
unset GITHUB_TOKEN
if ! GH_TOKEN="$(python3 scripts/barb_github_hub_token.py token 2>/dev/null)" || [[ -z "${GH_TOKEN}" ]]; then
    echo "barb relay: App token mint failed; refusing personal auth fallback" >&2
    exit 3
fi
export GH_TOKEN

status=0
python3 scripts/barb_panel_relay.py "$@" || status=$?
python3 scripts/barb_backlog_push_relay.py "$@" || status=$((status == 0 ? $? : status))
exit "${status}"

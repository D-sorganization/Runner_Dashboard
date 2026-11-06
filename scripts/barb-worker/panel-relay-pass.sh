#!/usr/bin/env bash
# One barbara panel-relay pass as this lane (#3711): fleet env, a hub-minted App token, then the relay.
set -euo pipefail

ROOT="${RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"
cd "${ROOT}"

# Writes must land as the fleet App bot, never a personal login (GOV-1).
unset GITHUB_TOKEN
if ! GH_TOKEN="$(python3 scripts/barb_github_hub_token.py token 2>/dev/null)" || [[ -z "${GH_TOKEN}" ]]; then
    echo "panel relay: App token mint failed; refusing personal auth fallback" >&2
    exit 3
fi
export GH_TOKEN
exec python3 scripts/barb_panel_relay.py "$@"

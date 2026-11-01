#!/usr/bin/env bash
# Barb-worker lane loop: poll rd-threads inbox via barb_cli (#3072).
set -euo pipefail

ROOT="${RUNNER_DASHBOARD_ROOT:-${HOME}/src/Runner_Dashboard}"
INTERVAL="${BARB_LANE_INTERVAL_S:-30}"

# shellcheck source=scripts/barb-worker/with-fleet-env.sh
source "${ROOT}/scripts/barb-worker/with-fleet-env.sh"

cd "${ROOT}"
while true; do
    python3 scripts/barb_cli.py inbox '{}' >/dev/null 2>&1 || true
    sleep "${INTERVAL}"
done

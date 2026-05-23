#!/usr/bin/env bash
# Graceful-drain hook for runner-dashboard (issue #711).
# Invoked by systemd as ExecStop= when the service is being stopped.
# Sends SIGTERM to the main process, waits DRAIN_TIMEOUT_S for it to exit,
# then escalates to SIGKILL if needed.
set -euo pipefail

DRAIN_TIMEOUT_S="${DRAIN_TIMEOUT_S:-30}"
DASHBOARD_PID="${MAINPID:-}"

if [[ -z "$DASHBOARD_PID" ]]; then
    echo "drain-dashboard: MAINPID not set, skipping drain" >&2
    exit 0
fi

echo "drain-dashboard: sending SIGTERM to PID $DASHBOARD_PID (timeout=${DRAIN_TIMEOUT_S}s)"
kill -TERM "$DASHBOARD_PID" 2>/dev/null || true

# Wait for process to exit
deadline=$((SECONDS + DRAIN_TIMEOUT_S))
while kill -0 "$DASHBOARD_PID" 2>/dev/null; do
    if [[ $SECONDS -ge $deadline ]]; then
        echo "drain-dashboard: timeout after ${DRAIN_TIMEOUT_S}s — sending SIGKILL to $DASHBOARD_PID"
        kill -KILL "$DASHBOARD_PID" 2>/dev/null || true
        exit 1
    fi
    sleep 0.5
done

echo "drain-dashboard: PID $DASHBOARD_PID exited cleanly"
exit 0

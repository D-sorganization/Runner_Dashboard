#!/bin/bash
# PWA Native Launcher for macOS and Linux
# Starts backend service and opens dashboard in browser
# Called by: custom URL protocol handler when user clicks runner-dashboard://start URL

set -e

HEALTH_URL="http://localhost:8321/health"
DASHBOARD_URL="http://localhost:8321"
LOG_DIR="$HOME/.config/runner-dashboard"
LOG_FILE="$LOG_DIR/launcher.log"
MAX_ATTEMPTS=10
ATTEMPT_INTERVAL=1

# Ensure log directory exists
mkdir -p "$LOG_DIR"

log() {
    local message="$1"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo -e "$timestamp\tlauncher.sh\t$message" >> "$LOG_FILE"
}

trap 'log "ERROR\tUnexpected error on line $LINENO"' ERR

log "START\tInitializing launcher"

# Check if backend is already responding
if curl -s -f -m 2 "$HEALTH_URL" > /dev/null 2>&1; then
    log "HEALTH_CHECK\tBackend already responding, opening browser"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "$DASHBOARD_URL"
    else
        xdg-open "$DASHBOARD_URL" || firefox "$DASHBOARD_URL" || chromium "$DASHBOARD_URL"
    fi
    exit 0
fi

log "HEALTH_CHECK\tBackend not responding, will start service"

# Start the backend service
log "START_SERVICE\tAttempting to start runner-dashboard service"

if systemctl --user start runner-dashboard 2>/dev/null; then
    log "START_SERVICE\tSystemd user service started"
elif sudo systemctl start runner-dashboard 2>/dev/null; then
    log "START_SERVICE\tSystemd system service started (with sudo)"
else
    log "START_SERVICE\tSystemd not available"
    # Try to start manually if setup.sh exists
    SETUP_PATH="$(dirname "$0")/setup.sh"
    if [[ -f "$SETUP_PATH" ]]; then
        log "START_SERVICE\tRunning setup.sh manually"
        bash "$SETUP_PATH"
    else
        log "START_SERVICE\tNo service mechanism found"
    fi
fi

# Poll health endpoint
log "HEALTH_CHECK\tBeginning health checks (max $MAX_ATTEMPTS attempts)"
ATTEMPT=0
HEALTH_CHECK_PASSED=0

while [[ $ATTEMPT -lt $MAX_ATTEMPTS ]]; do
    ATTEMPT=$((ATTEMPT + 1))
    sleep "$ATTEMPT_INTERVAL"

    if curl -s -f -m 2 "$HEALTH_URL" > /dev/null 2>&1; then
        log "HEALTH_CHECK\tSuccess on attempt $ATTEMPT/$MAX_ATTEMPTS"
        HEALTH_CHECK_PASSED=1
        break
    else
        log "HEALTH_CHECK\tAttempt $ATTEMPT/$MAX_ATTEMPTS failed"
    fi
done

if [[ $HEALTH_CHECK_PASSED -eq 1 ]]; then
    log "COMPLETE\tBackend healthy, opening dashboard"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        open "$DASHBOARD_URL"
    else
        xdg-open "$DASHBOARD_URL" || firefox "$DASHBOARD_URL" || chromium "$DASHBOARD_URL"
    fi
    exit 0
else
    log "COMPLETE\tFailed after $MAX_ATTEMPTS attempts"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        osascript -e "display notification \"Dashboard backend failed to start. Check $LOG_FILE for details.\" with title \"Runner Dashboard\""
    else
        notify-send "Runner Dashboard" "Dashboard backend failed to start. Check $LOG_FILE for details." || true
    fi
    exit 1
fi

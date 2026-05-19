#!/usr/bin/env bash
# ==============================================================================
# install-autoscaler.sh — Deploy the performance-aware runner auto-scaler.
# ==============================================================================
# - Copies runner_autoscaler.py into the deployed dashboard backend dir.
# - Installs runner-autoscaler.service as a systemd unit.
# - Installs a sudoers drop-in granting the runner user the minimum rights to
#   start/stop actions.runner.* units (and nothing else).
# - Installs a systemd drop-in for ALL actions.runner.* units that raises
#   TimeoutStopSec to 600s and sets KillMode=mixed so the autoscaler's
#   `systemctl stop` sends SIGTERM (not SIGKILL) and gives running jobs
#   up to 10 minutes to finish cleanly before the process is force-killed.
#   (Fix 3 from issue #640.)
# - Enables + starts the service.
#
# Run once per fleet machine:
#   bash install-autoscaler.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARD_DIR="${DASHBOARD_DIR:-$HOME/actions-runners/dashboard}"
RUNNER_USER="${RUNNER_USER:-$(id -un)}"
SCHEDULE_CONFIG="${RUNNER_SCHEDULE_CONFIG:-$HOME/.config/runner-dashboard/runner-schedule.json}"
# Directory for the graceful-stop drop-in. All actions.runner.*.service units
# matching this glob will be governed by the drop-in placed here.
RUNNER_DROPIN_DIR="${RUNNER_DROPIN_DIR:-/etc/systemd/system/actions.runner@.service.d}"
# How long to wait for a running job to finish before systemd sends SIGKILL.
# 600s (10 min) is generous but avoids truncating legitimate long-running steps
# (pip-install on slow hosts, integration test suites, etc.).
RUNNER_STOP_TIMEOUT="${RUNNER_STOP_TIMEOUT:-600}"

echo "==> Installing runner auto-scaler on $(hostname)"
echo "    Deployed dashboard dir: $DASHBOARD_DIR"
echo "    Runner user:            $RUNNER_USER"
echo "    Schedule config:        $SCHEDULE_CONFIG"

# 1. Copy the autoscaler module
install -Dm 0755 "$PROJECT_ROOT/backend/runner_autoscaler.py" \
    "$DASHBOARD_DIR/backend/runner_autoscaler.py"

# 2. Ensure psutil is available for the system python that will run it
if ! python3 -c "import psutil" 2>/dev/null; then
    echo "==> Installing psutil"
    pip3 install --break-system-packages psutil
fi

# 3. Sudoers drop-in: allow runner user to start/stop actions.runner.* only
SUDOERS_FILE="/etc/sudoers.d/runner-autoscaler"
if ! sudo test -f "$SUDOERS_FILE"; then
    echo "==> Installing sudoers drop-in at $SUDOERS_FILE"
    sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
# Allow the autoscaler to toggle self-hosted runner units (no other commands).
$RUNNER_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start actions.runner.*, /usr/bin/systemctl stop actions.runner.*
EOF
    sudo chmod 0440 "$SUDOERS_FILE"
    sudo visudo -cf "$SUDOERS_FILE" > /dev/null || { echo "sudoers validation failed"; exit 1; }
fi

# 4. Graceful-stop drop-in for all actions.runner.* units (issue #640 Fix 3).
#
# Problem: `systemctl stop` defaults to TimeoutStopSec=90s then SIGKILL.
# Runners executing pip-install (network-bound) or long test suites may still
# be busy at 90s, so they get SIGKILL mid-job, leaving corrupted state and
# stuck GitHub workflow runs (the "in_progress for 360 minutes" symptom).
#
# Fix: a drop-in under /etc/systemd/system/actions.runner@.service.d/ raises
# TimeoutStopSec to RUNNER_STOP_TIMEOUT and sets KillMode=mixed so:
#   - MainPID (Runner.Listener) gets SIGTERM immediately.
#   - Worker processes are allowed to finish naturally up to TimeoutStopSec.
#   - Only if the timeout fires does systemd send SIGKILL to stragglers.
#
# The GitHub Actions runner honours SIGTERM on its Listener by draining the
# current job before exiting, so this cooperates with the runner's own
# graceful-shutdown contract.
#
# Note: we target actions.runner@.service (the template unit) so the drop-in
# applies to all instances (actions.runner@<runner-name>.service). If the
# runner installer on this machine uses uninstanced names
# (actions.runner.<org>.<name>.service), we also create a drop-in directory
# for those by detecting existing unit names and writing per-unit drop-ins.
echo "==> Installing graceful-stop drop-in for actions.runner.* units"
DROPIN_CONTENT="[Service]
# Raised from the default 90s so running jobs can finish before SIGKILL.
# Matches the autoscaler's busy-detection window (issue #640 Fix 3).
TimeoutStopSec=${RUNNER_STOP_TIMEOUT}
# KillMode=mixed: send SIGTERM to the main process (Runner.Listener),
# but allow Worker children to finish naturally within TimeoutStopSec.
KillMode=mixed"

# Install drop-in for the template unit (covers instanced units on some setups)
sudo mkdir -p "$RUNNER_DROPIN_DIR"
echo "$DROPIN_CONTENT" | sudo tee "${RUNNER_DROPIN_DIR}/graceful-stop.conf" > /dev/null
echo "    Installed drop-in at ${RUNNER_DROPIN_DIR}/graceful-stop.conf"

# Also apply to any non-template actions.runner.*.service units already on disk
for unit_path in /etc/systemd/system/actions.runner.*.service; do
    [[ -f "$unit_path" ]] || continue
    unit_name="$(basename "$unit_path" .service)"
    dropin_dir="/etc/systemd/system/${unit_name}.service.d"
    if [[ ! -f "${dropin_dir}/graceful-stop.conf" ]]; then
        sudo mkdir -p "$dropin_dir"
        echo "$DROPIN_CONTENT" | sudo tee "${dropin_dir}/graceful-stop.conf" > /dev/null
        echo "    Installed drop-in for ${unit_name}"
    fi
done

# 5. Install + enable the systemd unit
echo "==> Installing systemd unit"

TEMPLATE_FILE="$SCRIPT_DIR/runner-autoscaler.service"
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    echo "ERROR: Template not found at $TEMPLATE_FILE"
    exit 1
fi

sed -e "s|YOUR_USER|$RUNNER_USER|g" \
    -e "s|/home/YOUR_USER|$HOME|g" \
    -e "s|RUNNER_SCHEDULE_CONFIG=.*|RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG}|g" \
    "$TEMPLATE_FILE" | sudo tee /etc/systemd/system/runner-autoscaler.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable runner-autoscaler.service
sudo systemctl restart runner-autoscaler.service

echo ""
echo "==> Done. Tail logs with: sudo journalctl -u runner-autoscaler -f"
sudo systemctl status runner-autoscaler.service --no-pager | head -12

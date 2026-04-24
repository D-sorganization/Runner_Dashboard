#!/usr/bin/env bash
# ==============================================================================
# install-autoscaler.sh — Deploy the performance-aware runner auto-scaler.
# ==============================================================================
# - Copies runner_autoscaler.py into the deployed dashboard backend dir.
# - Installs runner-autoscaler.service as a systemd unit.
# - Installs a sudoers drop-in granting the runner user the minimum rights to
#   start/stop actions.runner.* units (and nothing else).
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

# 4. Install + enable the systemd unit
echo "==> Installing systemd unit"
sudo install -Dm 0644 "$SCRIPT_DIR/runner-autoscaler.service" \
    /etc/systemd/system/runner-autoscaler.service
sudo install -d -m 0755 /etc/systemd/system/runner-autoscaler.service.d
sudo tee /etc/systemd/system/runner-autoscaler.service.d/schedule-config.conf > /dev/null <<EOF
[Service]
Environment=RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG}
EOF
sudo systemctl daemon-reload
sudo systemctl enable runner-autoscaler.service
sudo systemctl restart runner-autoscaler.service

echo ""
echo "==> Done. Tail logs with: sudo journalctl -u runner-autoscaler -f"
sudo systemctl status runner-autoscaler.service --no-pager | head -12

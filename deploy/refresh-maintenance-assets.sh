#!/usr/bin/env bash
# refresh-maintenance-assets.sh -- keep host-level maintenance assets current (#1927).
#
# install-runner-maintenance.sh copies deploy/runner-scheduler.py to
# /usr/local/bin/runner-scheduler, but only deploy-host.sh and setup.sh call it.
# A host kept current through routine update-deployed.sh runs therefore kept the
# scheduler it was first provisioned with (ControlTower ran a pre-#1922 build that
# stops busy runners mid-job). This script reinstalls an asset whenever its
# content differs from the repo copy:
#
#   runner-scheduler            /usr/local/bin           (from runner-scheduler.py)
#   wsl-cache-trim              /usr/local/sbin
#   wsl-cache-trim.{service,timer}  /etc/systemd/system  (daemon-reload on change)
#
# It never provisions a host that was not set up by install-runner-maintenance.sh,
# and it skips the scheduler on qualified-release hosts, whose runner-scheduler
# .service executes the copy inside the release directory instead.
#
# Usage:
#   bash deploy/refresh-maintenance-assets.sh           # reinstall drifted assets
#   bash deploy/refresh-maintenance-assets.sh --check   # report drift, exit 1, write nothing
#
# Overrides (tests): MAINT_BIN_DIR, MAINT_SBIN_DIR, MAINT_UNIT_DIR, MAINT_SUDO
# (empty = run as the current user), MAINT_SYSTEMCTL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${MAINT_BIN_DIR:-/usr/local/bin}"
SBIN_DIR="${MAINT_SBIN_DIR:-/usr/local/sbin}"
UNIT_DIR="${MAINT_UNIT_DIR:-/etc/systemd/system}"
SYSTEMCTL="${MAINT_SYSTEMCTL:-systemctl}"
read -r -a SUDO <<< "${MAINT_SUDO-sudo -n}"

MODE=apply
case "${1:-}" in
    "") ;;
    --check) MODE=check ;;
    *) echo "Usage: $0 [--check]" >&2; exit 2 ;;
esac

DRIFT=0
UNITS_CHANGED=0

# refresh_asset <src> <dest> <mode> [unit]
# Installs <src> over <dest> when their bytes differ. Only touches assets that are
# already installed (callers decide provisioning), so an absent <dest> is skipped.
refresh_asset() {
    local src="$1" dest="$2" mode="$3" is_unit="${4:-}"
    [[ -f "$src" ]] || { echo "[WARN] source missing, skipped: $src" >&2; return 0; }
    [[ -f "$dest" ]] || return 0
    cmp -s "$src" "$dest" && return 0
    DRIFT=1
    if [[ "$MODE" == check ]]; then
        echo "[DRIFT] $dest differs from $src"
        return 0
    fi
    "${SUDO[@]}" install -m "$mode" "$src" "$dest"
    echo "[ OK ] refreshed $dest"
    [[ -z "$is_unit" ]] || UNITS_CHANGED=1
}

# The scheduler is host-managed only when its unit runs the /usr/local/bin copy.
scheduler_unit="$UNIT_DIR/runner-scheduler.service"
if [[ -f "$scheduler_unit" ]] && grep -qF "$BIN_DIR/runner-scheduler" "$scheduler_unit"; then
    refresh_asset "$SCRIPT_DIR/runner-scheduler.py" "$BIN_DIR/runner-scheduler" 0755
else
    echo "[INFO] runner-scheduler not host-managed here (qualified release or unprovisioned); skipped"
fi

# Fleet memory policy (RM #2071) assets from install-runner-maintenance.sh.
refresh_asset "$SCRIPT_DIR/wsl-cache-trim" "$SBIN_DIR/wsl-cache-trim" 0755
refresh_asset "$SCRIPT_DIR/wsl-cache-trim.service" "$UNIT_DIR/wsl-cache-trim.service" 0644 unit
refresh_asset "$SCRIPT_DIR/wsl-cache-trim.timer" "$UNIT_DIR/wsl-cache-trim.timer" 0644 unit

if [[ "$UNITS_CHANGED" == 1 ]]; then
    "${SUDO[@]}" "$SYSTEMCTL" daemon-reload
fi

[[ "$MODE" == check && "$DRIFT" == 1 ]] && exit 1
exit 0

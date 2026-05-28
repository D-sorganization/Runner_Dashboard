#!/usr/bin/env bash
# decouple-docker-boot.sh — take docker + containerd off the WSL boot-critical path.
#
# WSL aborts a distro boot if systemd does not reach its default target within
# ~10s (WaitForBootProcess:3432 "/sbin/init failed to start within 10000ms" ->
# forced reboot(RB_POWER_OFF) -> journal corruption -> crash loop). On
# SSD-backed distros, docker.service (~6s) + containerd.service (~4s) +
# systemd-tmpfiles-setup stack on the critical chain and push boot-ready to
# ~15s, so the distro never finishes booting in time and crash-loops.
#
# Fix: remove docker.service + containerd.service from the boot transaction
# (they stay installed and, if currently running, are NOT stopped — `disable`
# only removes the boot-time wants symlink). docker.socket stays enabled so the
# first Docker use activates Docker on demand, and docker-delayed-start.timer
# proactively starts Docker ~20s after boot, off the critical path. Result: the
# boot target is reached in ~4-5s while Docker is still available right after
# boot.
#
# Idempotent; safe to re-run. Changes take effect on the NEXT distro boot.
#
# Usage:  sudo deploy/decouple-docker-boot.sh [--dry-run]
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "must run as root" >&2; exit 2; }

DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMER_SRC="${SCRIPT_DIR}/docker-delayed-start.timer"
TIMER_DST=/etc/systemd/system/docker-delayed-start.timer

log() { printf '%s decouple-docker-boot: %s\n' "$(date --iso-8601=seconds)" "$*"; }
run() {
    if [[ $DRY == 1 ]]; then log "[dry-run] $*"; else log "+ $*"; "$@"; fi
}

# 1. Remove docker + containerd from the boot transaction (keep them installed
#    and currently-running).
for unit in docker.service containerd.service; do
    if systemctl is-enabled "$unit" >/dev/null 2>&1; then
        run systemctl disable "$unit"
    else
        log "= $unit already not boot-enabled"
    fi
done

# 2. Keep docker.socket enabled so on-demand activation covers anything that
#    needs Docker before the post-boot timer fires.
if ! systemctl is-enabled docker.socket >/dev/null 2>&1; then
    run systemctl enable docker.socket
else
    log "= docker.socket already enabled"
fi

# 3. Install the post-boot timer (canonical copy lives next to this script).
if [[ -f "$TIMER_SRC" ]]; then
    run install -m 0644 "$TIMER_SRC" "$TIMER_DST"
else
    log "ERROR: timer source not found: $TIMER_SRC" >&2
    exit 3
fi

run systemctl daemon-reload
run systemctl enable docker-delayed-start.timer

log "done: docker/containerd decoupled from boot on $(hostname) (effective next boot)"

#!/usr/bin/env bash
# refresh-maintenance-assets.sh -- keep host-level maintenance assets current (#1927).
#
# install-runner-maintenance.sh copies deploy/runner-scheduler.py to
# /usr/local/bin/runner-scheduler, but only deploy-host.sh and setup.sh call it.
# A host kept current through routine update-deployed.sh runs therefore kept the
# scheduler it was first provisioned with (node-a ran a pre-#1922 build that
# stops busy runners mid-job). This script reinstalls an asset whenever its
# content differs from the repo copy:
#
#   runner-scheduler            /usr/local/bin           (from runner-scheduler.py)
#   wsl-cache-trim              /usr/local/sbin
#   wsl-cache-trim.{service,timer}  /etc/systemd/system  (daemon-reload on change)
#   runner-hooks/{job-started,job-completed,force-drain}.sh  /usr/local/bin/runner-hooks
#   runner-hooks-restore        /usr/local/bin           (from runner-hooks-restore.sh, #1975)
#   runner-hooks/*.sh mirror    /usr/local/share/runner-hooks  (restore's source, #1977)
#   runner-hooks-restore.service.d/20-hook-source.conf   (points restore at the mirror)
#
# The runner hooks are refreshed here, from the deployed repo copy, because
# runner-hooks-restore.timer deliberately only restores a MISSING hook: its
# old source (/opt/runner-dashboard) went stale on hub-a (a 2026-07-31
# commit) and was absent on node-a and node-b. Hooks are read fresh
# per job, so no runner restart is needed.
#
# It never provisions a host that was not set up by install-runner-maintenance.sh,
# and it skips the scheduler on qualified-release hosts, whose runner-scheduler
# .service executes the copy inside the release directory instead.
#
# Usage:
#   bash deploy/refresh-maintenance-assets.sh           # reinstall drifted assets
#   bash deploy/refresh-maintenance-assets.sh --check   # report drift, exit 1, write nothing
#
# Overrides (tests): MAINT_BIN_DIR, MAINT_SBIN_DIR, MAINT_UNIT_DIR, MAINT_HOOK_DIR,
# MAINT_SHARE_DIR, MAINT_SUDO (empty = run as the current user), MAINT_SYSTEMCTL.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${MAINT_BIN_DIR:-/usr/local/bin}"
SBIN_DIR="${MAINT_SBIN_DIR:-/usr/local/sbin}"
UNIT_DIR="${MAINT_UNIT_DIR:-/etc/systemd/system}"
HOOK_DIR="${MAINT_HOOK_DIR:-$BIN_DIR/runner-hooks}"
SHARE_DIR="${MAINT_SHARE_DIR:-/usr/local/share}"
HOOK_SRC_MIRROR="$SHARE_DIR/runner-hooks"
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
    local src="$1" dest="$2"
    [[ -f "$dest" ]] || return 0
    sync_asset "$@"
}

# sync_asset <src> <dest> <mode> [unit]
# Like refresh_asset, but also creates an absent <dest> (and its directory).
sync_asset() {
    local src="$1" dest="$2" mode="$3" is_unit="${4:-}"
    [[ -f "$src" ]] || { echo "[WARN] source missing, skipped: $src" >&2; return 0; }
    [[ -f "$dest" ]] && cmp -s "$src" "$dest" && return 0
    DRIFT=1
    if [[ "$MODE" == check ]]; then
        echo "[DRIFT] $dest differs from $src"
        return 0
    fi
    "${SUDO[@]}" install -D -m "$mode" "$src" "$dest"
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

# Runner job hooks from install-runner-maintenance.sh (#664). Only installed
# hooks are refreshed; a missing one is runner-hooks-restore.sh's job.
for hook in job-started.sh job-completed.sh force-drain.sh; do
    refresh_asset "$SCRIPT_DIR/runner-hooks/$hook" "$HOOK_DIR/$hook" 0755
done

# The hook self-heal script itself (#1975). Its timer runs the installed copy;
# a pre-#1971 copy reinstalls any hook whose content differs from its (possibly
# stale) HOOK_SRC_DIR, undoing the hook refresh above within one tick.
refresh_asset "$SCRIPT_DIR/runner-hooks-restore.sh" "$BIN_DIR/runner-hooks-restore" 0755

# The restore timer's hook source (#1977). Its old HOOK_SRC_DIR was a repo
# checkout under /opt that nothing kept current (stale on hub-a, absent
# on node-a and node-b), so a deleted hook came back old or not at all.
# On a host with the restore installed, keep a mirror of the repo hooks and
# point the unit at it through a drop-in that overrides the installer's value.
if [[ -f "$BIN_DIR/runner-hooks-restore" ]]; then
    for hook in job-started.sh job-completed.sh force-drain.sh; do
        sync_asset "$SCRIPT_DIR/runner-hooks/$hook" "$HOOK_SRC_MIRROR/$hook" 0755
    done
    if [[ -f "$UNIT_DIR/runner-hooks-restore.service" ]]; then
        source_dropin="$(mktemp)"
        trap 'rm -f "$source_dropin"' EXIT
        printf '%s\n' \
            "# Managed by deploy/refresh-maintenance-assets.sh (#1977) -- do not hand-edit." \
            "[Service]" \
            "Environment=HOOK_SRC_DIR=$HOOK_SRC_MIRROR" >"$source_dropin"
        sync_asset "$source_dropin" "$UNIT_DIR/runner-hooks-restore.service.d/20-hook-source.conf" 0644 unit
    fi
fi

if [[ "$UNITS_CHANGED" == 1 ]]; then
    "${SUDO[@]}" "$SYSTEMCTL" daemon-reload
fi

[[ "$MODE" == check && "$DRIFT" == 1 ]] && exit 1
exit 0

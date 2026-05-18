#!/usr/bin/env bash
# migrate-runner-units.sh — apply orphan-Worker + job-pickup-race fixes
# to every installed actions.runner.*.service unit on this host.
#
# Changes applied (idempotent, can re-run safely):
#   1. KillMode=mixed  (was: KillMode=process) — so the systemd stop
#      signal cascade reaches the Runner.Worker child instead of
#      orphaning it. With `process` the Worker survived listener
#      shutdown and left `_diag/pages/*.log` open + `_runner_file_commands`
#      residue. See issue #651.
#   2. TimeoutStopSec=120 — bumped from 5min default (svc.sh script
#      installs 5min) down to a more aggressive window. The hooks make
#      sure jobs are accounted for; if the listener can't drain in
#      2 minutes something is wedged.
#   3. Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED=/opt/runner-dashboard/deploy/runner-hooks/job-started.sh
#      Environment=ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/opt/runner-dashboard/deploy/runner-hooks/job-completed.sh
#      Environment=RUNNER_BUSY_LOCK_DIR=/var/run/runner-busy
#      Environment=RUNNER_NAME=<unit-derived> — so the hooks know which
#      runner they belong to. The hook scripts maintain a lockfile that
#      both the autoscaler and cleanup consult as a second busy signal.
#
# Usage:
#   sudo deploy/migrate-runner-units.sh                 # apply
#   sudo deploy/migrate-runner-units.sh --dry-run       # preview
#   sudo deploy/migrate-runner-units.sh --restart-units # apply, then restart each unit
#
# Without --restart-units the changes take effect on next service restart.
# (`heal-host.sh` will restart them all; or the operator can do it during
# a quiet window.)

set -Eeuo pipefail

HOOK_DIR="${HOOK_DIR:-/opt/runner-dashboard/deploy/runner-hooks}"
LOCK_DIR="${LOCK_DIR:-/var/run/runner-busy}"
TIMEOUT_STOP_SEC="${TIMEOUT_STOP_SEC:-120}"
DRY_RUN="${DRY_RUN:-0}"
RESTART_UNITS="${RESTART_UNITS:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)         DRY_RUN=1; shift ;;
        --restart-units)   RESTART_UNITS=1; shift ;;
        -h|--help)
            sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { printf '%s migrate-runner-units: %s\n' "$(date --iso-8601=seconds)" "$*"; }

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] $*"
        return 0
    fi
    log "+ $*"
    "$@"
}

if [[ "$EUID" -ne 0 ]]; then
    log "must run as root"
    exit 2
fi

# Sanity-check the hook scripts exist (or will, after the corresponding
# `install-runner-maintenance.sh` runs).
for h in job-started.sh job-completed.sh; do
    if [[ ! -x "${HOOK_DIR}/${h}" ]]; then
        log "warn: ${HOOK_DIR}/${h} not present or not executable — units will be configured but hooks will no-op until the file lands"
    fi
done

mapfile -t units < <(
    systemctl list-unit-files --type=service --no-legend \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}' \
        | sort
)

if (( ${#units[@]} == 0 )); then
    log "no actions.runner.*.service units installed — nothing to migrate"
    exit 0
fi

log "found ${#units[@]} unit(s) to migrate"

# Ensure lockfile directory exists and is writable by the runner user.
# Find the user from the first unit's User= setting.
runner_user="$(systemctl show "${units[0]}" --property=User --value 2>/dev/null || echo '')"
runner_user="${runner_user:-${SUDO_USER:-runner}}"

run install -d -m 0775 -o "$runner_user" -g "$runner_user" "$LOCK_DIR"

# Apply per-unit changes via a drop-in override (preferred over editing
# the upstream unit file — survives runner package upgrades).
for unit in "${units[@]}"; do
    # Runner name from unit: actions.runner.<org>.<runner>.service → <runner>
    runner_name="$(echo "$unit" | sed -E 's/^actions\.runner\.[^.]+\.([^.]+)\.service$/\1/')"
    override_dir="/etc/systemd/system/${unit}.d"
    override_file="${override_dir}/10-runner-dashboard-busy-lock.conf"

    log "migrating $unit (runner_name=$runner_name)"
    run install -d -m 0755 "$override_dir"

    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] would write ${override_file}"
    else
        cat > "$override_file" <<EOF
# Managed by deploy/migrate-runner-units.sh — do not hand-edit.
# Companion to deploy/runner-hooks/*.sh and backend/runner_autoscaler.py.

[Service]
# Cascade SIGTERM (and SIGKILL after timeout) to the whole cgroup so
# Runner.Worker children don't orphan when the listener exits.
KillMode=mixed
TimeoutStopSec=${TIMEOUT_STOP_SEC}

# Job-pickup hooks: write/remove a sentinel lockfile under \$RUNNER_BUSY_LOCK_DIR.
# Both backend/runner_autoscaler.py and deploy/runner-cleanup.sh consult this
# lockfile as a defense-in-depth busy signal.
Environment=ACTIONS_RUNNER_HOOK_JOB_STARTED=${HOOK_DIR}/job-started.sh
Environment=ACTIONS_RUNNER_HOOK_JOB_COMPLETED=${HOOK_DIR}/job-completed.sh
Environment=RUNNER_BUSY_LOCK_DIR=${LOCK_DIR}
Environment=RUNNER_NAME=${runner_name}
EOF
        log "wrote ${override_file}"
    fi
done

run systemctl daemon-reload

if [[ "$RESTART_UNITS" == "1" ]]; then
    log "restarting units (--restart-units set)"
    for unit in "${units[@]}"; do
        run systemctl restart "$unit" || log "warn: $unit restart failed (continuing)"
    done
fi

log "done — ${#units[@]} unit(s) migrated"
if [[ "$RESTART_UNITS" != "1" ]]; then
    log "NOTE: changes take effect on next service restart"
    log "      operator action: run \`deploy/heal-host.sh\` during a quiet window, or"
    log "                       \`sudo systemctl restart 'actions.runner.*.service'\`"
fi

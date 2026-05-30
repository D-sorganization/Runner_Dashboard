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
#   4. Environment=RUNNER_TOOL_CACHE=<per-runner dir> — gives every runner
#      its OWN tool cache so concurrent jobs on one host never race on a
#      shared .shared-tool-cache during actions/setup-python. That race
#      corrupted the Python tree ("Directory not empty" during the old
#      force-clean, exit-127 "python: command not found", and
#      "ModuleNotFoundError: No module named 'http'"). Default cache dir is
#      <WorkingDirectory>/_work/_tool; override via RUNNER_TOOL_CACHE_ROOT.
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

# Default to where deploy/install-runner-maintenance.sh installs the hooks.
# That path is /usr/local/bin/runner-hooks (a stable system location), not
# a repo checkout, so the drop-ins survive any future change to where the
# Runner_Dashboard tree is cloned.
HOOK_DIR="${HOOK_DIR:-/usr/local/bin/runner-hooks}"
LOCK_DIR="${LOCK_DIR:-/var/run/runner-busy}"
TIMEOUT_STOP_SEC="${TIMEOUT_STOP_SEC:-120}"
DRY_RUN="${DRY_RUN:-0}"
RESTART_UNITS="${RESTART_UNITS:-0}"
# Per-runner private tool cache. Each runner gets its OWN RUNNER_TOOL_CACHE so
# concurrent jobs on the same host never race on a shared cache while
# actions/setup-python extracts Python. The shared `.shared-tool-cache` design
# let one job's clean/extract collide with another's, corrupting the Python
# tree ("Directory not empty", exit-127 "python: command not found",
# "ModuleNotFoundError: No module named 'http'"). When empty, the cache is
# derived per-unit as <WorkingDirectory>/_work/_tool — the runner-local default
# that deploy/runner-cleanup.sh already garbage-collects. Set this to override
# with a per-runner-name dir under a custom root (e.g. a dedicated NVMe path).
RUNNER_TOOL_CACHE_ROOT="${RUNNER_TOOL_CACHE_ROOT:-}"

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

    # Derive this runner's PRIVATE tool cache. Prefer an explicit
    # RUNNER_TOOL_CACHE_ROOT/<runner_name>; otherwise use the runner-local
    # default <WorkingDirectory>/_work/_tool (already GC'd by runner-cleanup.sh).
    tool_cache_stanza=""
    tool_cache_dir=""
    if [[ -n "$RUNNER_TOOL_CACHE_ROOT" ]]; then
        tool_cache_dir="${RUNNER_TOOL_CACHE_ROOT%/}/${runner_name}"
    else
        unit_workdir="$(systemctl show "$unit" --property=WorkingDirectory --value 2>/dev/null || true)"
        if [[ -n "$unit_workdir" && "$unit_workdir" != "/" ]]; then
            tool_cache_dir="${unit_workdir%/}/_work/_tool"
        fi
    fi
    if [[ -n "$tool_cache_dir" ]]; then
        # Create the cache dir owned by the runner user so the first job can
        # populate it. Best-effort: a missing dir is recreated by setup-python.
        run install -d -m 0755 -o "$runner_user" -g "$runner_user" "$tool_cache_dir" \
            2>/dev/null || run mkdir -p "$tool_cache_dir" || true
        tool_cache_stanza=$'# Per-runner private Python tool cache (RUNNER_TOOL_CACHE): each runner\n'
        tool_cache_stanza+=$'# extracts Python into its OWN cache so concurrent jobs on this host never\n'
        tool_cache_stanza+=$'# race on a shared .shared-tool-cache (which corrupted setup-python with\n'
        tool_cache_stanza+=$'# dir-not-empty / "python: command not found" / missing-stdlib failures).\n'
        tool_cache_stanza+="Environment=RUNNER_TOOL_CACHE=${tool_cache_dir}"
    else
        log "warn: $unit — could not resolve a tool-cache dir; RUNNER_TOOL_CACHE not set for this unit"
    fi

    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] would write ${override_file}"
        [[ -n "$tool_cache_dir" ]] && log "[dry-run]   with RUNNER_TOOL_CACHE=${tool_cache_dir}"
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

${tool_cache_stanza}

# Belt-and-suspenders for WSL2 hosts where the cgroup-wide kill can
# fail with EINVAL ("Failed to kill control group ...: Invalid argument").
# Observed on Ubuntu 22.04 / WSL2 6.x kernels: KillMode=mixed cascade
# silently no-ops on the Worker child, leaving an orphan that gets
# logged on the next start as "Found left-over process ... in control
# group". This ExecStop= pkill's any leftover Workers under this
# unit's WorkingDirectory.
#
# %n passes the full unit name to force-drain.sh as \$1. The script
# uses it to resolve WorkingDirectory via "systemctl show", which is
# more reliable than guessing from \$RUNNER_NAME (the runner-dir name
# often does not match the runner's registered system name).
ExecStop=-${HOOK_DIR}/force-drain.sh %n
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

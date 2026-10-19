#!/usr/bin/env bash
# Self-heal for the runner job-pickup hooks (issue #664, follow-up to
# the 2026-05-27 fleet outage where multiple runners had
# ACTIONS_RUNNER_HOOK_JOB_COMPLETED pointing at files that had been
# deleted from /usr/local/bin/runner-hooks/, producing
# `##[error]File doesn't exist` on every job and stalling all CI.
#
# Three duties, all idempotent -- a no-op when everything is already in
# place -- so it is safe to run on a short timer:
#
# 1. Hook presence.  Re-installs a hook script from the deploy/runner-hooks
#    source dir ONLY when it is missing from /usr/local/bin/runner-hooks/,
#    and only chmods it back to 0755 when it is present but not executable.
#
#    Rule (2026-10-17): this timer never rewrites the CONTENT of an installed
#    hook.  Its old HOOK_SRC_DIR (/opt/runner-dashboard) was not kept current
#    -- DeskComputer's checkout sat at a 2026-07-31 commit, ControlTower and
#    OGLaptop had none -- so a "differs from source" rule silently DOWNGRADED
#    the newer hooks that refresh-maintenance-assets.sh installs from the
#    deployed repo copy.  Content drift is refresh-maintenance-assets.sh's job
#    (it runs on every update-deployed.sh); this timer only guarantees the
#    files exist and run.
#
#    Source (#1977): refresh-maintenance-assets.sh keeps a mirror of the repo
#    hooks at /usr/local/share/runner-hooks and points this unit at it, so a
#    restored hook is the current one.  --check reports a missing source dir
#    or an installed hook that differs from its source as drift, so a stale
#    or absent source is never silent again.
#
# 2. Unit wiring.  Every actions.runner.*.service must carry
#      ACTIONS_RUNNER_HOOK_JOB_STARTED=${HOOK_DIR}/job-started.sh
#      ACTIONS_RUNNER_HOOK_JOB_COMPLETED=${HOOK_DIR}/job-completed.sh
#    in its effective Environment (`systemctl show --property=Environment`).
#    ControlTower's 8 units had none -- the hooks were installed but never
#    ran.  A unit missing either gets the drop-in
#    ${SYSTEMD_UNIT_DIR}/<unit>.d/15-runner-hooks.conf, then one
#    daemon-reload.  The new environment applies at the unit's next restart.
#    This script NEVER restarts a unit: the nightly runner-cleanup.sh pass
#    already restarts runners only after its busy checks pass, and the
#    capacity scheduler starts stopped ones, so a running job is never cut
#    short here.
#
# 3. Runner .env hook overrides.  The runner's own <runner>/.env wins over
#    the unit's Environment=, so a stale ACTIONS_RUNNER_HOOK_JOB_STARTED= /
#    _COMPLETED= line there (DeskComputer: ~/actions-runners/fleet/
#    runner-post-job.sh, which only cleared _work/_temp -- job-completed.sh
#    does that now) means the canonical hooks never run.  Any such line whose
#    value is not the canonical hook is removed after a timestamped backup of
#    the .env.  Only dirs that look like runner installs (bin/ plus .runner or
#    run.sh) are touched.  The runner reads .env at start-up, so this too
#    applies at the next runner restart.
#
# Usage:
#   runner-hooks-restore            # heal, write the Prometheus textfile
#   runner-hooks-restore --check    # report drift, write nothing, exit 1 on drift
#
# Inputs (env, all optional):
#   HOOK_SRC_DIR     — source dir containing the *.sh hooks
#                      (default: /usr/local/share/runner-hooks, the mirror
#                      refresh-maintenance-assets.sh keeps current)
#   HOOK_DIR         — install dir consulted by the runner
#                      (default: /usr/local/bin/runner-hooks)
#   PROM_FILE        — Prometheus textfile output path
#                      (default: $TEXTFILE_COLLECTOR_DIR/runner_hooks.prom)
#   SYSTEMCTL        — systemctl binary (tests pass a fake)
#   SYSTEMD_UNIT_DIR — drop-in parent dir (default: /etc/systemd/system)

set -Eeuo pipefail

HOOK_SRC_DIR="${HOOK_SRC_DIR:-/usr/local/share/runner-hooks}"
HOOK_DIR="${HOOK_DIR:-/usr/local/bin/runner-hooks}"
TEXTFILE_COLLECTOR_DIR="${TEXTFILE_COLLECTOR_DIR:-/var/lib/node_exporter/textfile_collector}"
PROM_FILE="${PROM_FILE:-${TEXTFILE_COLLECTOR_DIR}/runner_hooks.prom}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
SYSTEMD_UNIT_DIR="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
PROC_DIR="${PROC_DIR:-/proc}"
DROPIN_NAME="15-runner-hooks.conf"

MODE=apply
case "${1:-}" in
    "") ;;
    --check) MODE=check ;;
    *) echo "Usage: $0 [--check]" >&2; exit 2 ;;
esac

HOOKS=(job-started.sh job-completed.sh force-drain.sh)
STARTED_WANT="ACTIONS_RUNNER_HOOK_JOB_STARTED=${HOOK_DIR}/job-started.sh"
COMPLETED_WANT="ACTIONS_RUNNER_HOOK_JOB_COMPLETED=${HOOK_DIR}/job-completed.sh"

drift=0
restored=0
missing_src=0
wiring_missing=0
dropins_written=0
env_overrides=0
env_overrides_removed=0
restart_required_runners=0

log() { echo "runner-hooks-restore: $*"; }

# -- 1. hook presence ---------------------------------------------------------
if [[ ! -d "${HOOK_SRC_DIR}" ]]; then
    missing_src=1
    drift=1
    if [[ "$MODE" == check ]]; then
        log "[DRIFT] hook source dir ${HOOK_SRC_DIR} missing; a deleted hook cannot be restored"
    else
        log "source dir ${HOOK_SRC_DIR} not present; nothing to restore from" >&2
    fi
fi
[[ "$MODE" == check ]] || install -d -m 0755 "${HOOK_DIR}"
for hook in "${HOOKS[@]}"; do
    src="${HOOK_SRC_DIR}/${hook}"
    dst="${HOOK_DIR}/${hook}"
    if [[ -f "${dst}" && -x "${dst}" ]]; then
        # Report only: refresh-maintenance-assets.sh owns content (see rule above).
        if [[ "$MODE" == check && -f "${src}" ]] && ! cmp -s "${src}" "${dst}"; then
            drift=1
            log "[DRIFT] ${dst} differs from source ${src}; run refresh-maintenance-assets.sh"
        fi
        continue
    fi
    drift=1
    if [[ "$MODE" == check ]]; then
        log "[DRIFT] ${dst} missing or not executable"
        continue
    fi
    if [[ -f "${dst}" ]]; then
        chmod 0755 "${dst}"
        restored=$((restored + 1))
        log "made ${dst} executable"
    elif [[ -f "${src}" ]]; then
        install -m 0755 "${src}" "${dst}"
        restored=$((restored + 1))
        log "restored missing ${dst} from ${src}"
    else
        log "${dst} missing and no source at ${src}" >&2
    fi
done

list_runner_units() {
    "$SYSTEMCTL" list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}' \
        | sort -u || true
}

# -- 2. unit wiring -------------------------------------------------------------
reload=0
while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    environment=" $("$SYSTEMCTL" show "$unit" --property=Environment --value 2>/dev/null || true) "
    if [[ "$environment" == *" ${STARTED_WANT} "* && "$environment" == *" ${COMPLETED_WANT} "* ]]; then
        continue
    fi
    drift=1
    wiring_missing=$((wiring_missing + 1))
    dropin="${SYSTEMD_UNIT_DIR}/${unit}.d/${DROPIN_NAME}"
    if [[ "$MODE" == check ]]; then
        log "[DRIFT] ${unit} lacks the canonical job hooks in its Environment"
        continue
    fi
    runner_name="${unit%.service}"
    runner_name="${runner_name##*.}"
    content="# Managed by deploy/runner-hooks-restore.sh -- do not hand-edit.
# Wires the canonical job hooks; applies at the unit's next restart.
[Service]
Environment=${STARTED_WANT}
Environment=${COMPLETED_WANT}"
    # The hooks key their busy lockfile on RUNNER_NAME; keep an existing value.
    if [[ "$environment" != *" RUNNER_NAME="* ]]; then
        content+=$'\n'"Environment=RUNNER_NAME=${runner_name}"
    fi
    if [[ ! -f "$dropin" ]] || [[ "$(cat "$dropin")" != "$content" ]]; then
        install -d -m 0755 "$(dirname "$dropin")"
        printf '%s\n' "$content" >"${dropin}.tmp.$$"
        chmod 0644 "${dropin}.tmp.$$"
        mv -f "${dropin}.tmp.$$" "$dropin"
        dropins_written=$((dropins_written + 1))
        log "wrote ${dropin} (applies at ${unit}'s next restart)"
    fi
    reload=1
done < <(list_runner_units)
if (( reload == 1 )); then
    "$SYSTEMCTL" daemon-reload || log "warn: daemon-reload failed" >&2
fi

# -- 3. runner .env overrides ---------------------------------------------------
# looks_like_runner <dir>: a configured actions-runner install.
looks_like_runner() {
    [[ -d "$1" && ! -L "$1" && -d "$1/bin" ]] && [[ -f "$1/.runner" || -x "$1/run.sh" ]]
}

# is_override <line>: a JOB_STARTED/COMPLETED line not pointing at the canonical hook.
is_override() {
    local line="$1" key value
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?(ACTIONS_RUNNER_HOOK_JOB_STARTED|ACTIONS_RUNNER_HOOK_JOB_COMPLETED)= ]] || return 1
    key="${BASH_REMATCH[2]}"
    value="${line#*=}"
    value="${value%$'\r'}"
    value="${value#\"}"; value="${value%\"}"
    value="${value#\'}"; value="${value%\'}"
    [[ "${key}=${value}" != "$STARTED_WANT" && "${key}=${value}" != "$COMPLETED_WANT" ]]
}

fix_env_file() {
    local runner_dir="$1" env_file="$1/.env" line found=0 staged backup
    [[ -f "$env_file" && ! -L "$env_file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        if is_override "$line"; then
            found=$((found + 1))
            log "${env_file} overrides the canonical hook (${line%%=*})"
        fi
    done <"$env_file"
    (( found > 0 )) || return 0
    drift=1
    env_overrides=$((env_overrides + found))
    [[ "$MODE" != check ]] || return 0
    backup="${env_file}.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$env_file" "$backup" || return 1
    staged="$(mktemp "${runner_dir}/.env.XXXXXX")" || return 1
    while IFS= read -r line || [[ -n "$line" ]]; do
        is_override "$line" || printf '%s\n' "$line"
    done <"$env_file" >"$staged"
    chown --reference="$env_file" "$staged" 2>/dev/null || true
    chmod --reference="$env_file" "$staged" 2>/dev/null || true
    mv -f "$staged" "$env_file" || { rm -f "$staged"; return 1; }
    env_overrides_removed=$((env_overrides_removed + found))
    log "removed ${found} hook override(s) from ${env_file} (backup ${backup}); applies at next runner restart"
    # Issue #1987: Flag runner as restart-required so changes to .env are not silently inert.
    touch "${runner_dir}/.restart-required"
    log "flagged ${runner_dir} as restart-required"
}

while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    runner_dir="$("$SYSTEMCTL" show "$unit" --property=WorkingDirectory --value 2>/dev/null || true)"
    if [[ -z "$runner_dir" ]] || ! looks_like_runner "$runner_dir"; then
        continue
    fi
    fix_env_file "$runner_dir" || log "could not fix ${runner_dir}/.env" >&2

    # Issue #1987: Check if .restart-required flag exists
    if [[ -f "${runner_dir}/.restart-required" ]]; then
        restart_required_runners=$((restart_required_runners + 1))
        if [[ "$MODE" == check ]]; then
            drift=1
            log "[DRIFT] ${runner_dir} has .restart-required flag set; restart runner to apply .env changes"
        fi
    fi

    # Issue #1987 (Item 4): Check .env-versus-running-process drift.
    # Compare /proc/<Listener pid> start time with .env mtime.
    main_pid="$("$SYSTEMCTL" show "$unit" --property=MainPID --value 2>/dev/null || echo 0)"
    if [[ -n "$main_pid" && "$main_pid" =~ ^[0-9]+$ && "$main_pid" -gt 0 ]]; then
        proc_stat="${PROC_DIR}/${main_pid}"
        env_file="${runner_dir}/.env"
        if [[ -d "$proc_stat" && -f "$env_file" ]]; then
            pid_start=$(stat -c %Y "$proc_stat" 2>/dev/null || echo 0)
            env_mtime=$(stat -c %Y "$env_file" 2>/dev/null || echo 0)
            if (( pid_start > 0 && env_mtime > pid_start )); then
                drift=1
                if [[ "$MODE" == check ]]; then
                    log "[DRIFT] ${unit} (PID ${main_pid}) started before .env was modified (env_mtime=${env_mtime} > pid_start=${pid_start}); restart required"
                else
                    log "warn: ${unit} (PID ${main_pid}) started before .env was modified; flagging restart-required"
                    if [[ ! -f "${runner_dir}/.restart-required" ]]; then
                        touch "${runner_dir}/.restart-required"
                        restart_required_runners=$((restart_required_runners + 1))
                    fi
                fi
            fi
        fi
    fi
done < <(list_runner_units)

if [[ "$MODE" == check ]]; then
    (( drift == 0 )) || exit 1
    exit 0
fi

if [[ -d "${TEXTFILE_COLLECTOR_DIR}" ]]; then
    tmp="$(mktemp "${PROM_FILE}.XXXXXX")"
    {
        echo "# HELP runner_hooks_restore_total Hooks re-installed or re-chmodded on the most recent self-heal pass."
        echo "# TYPE runner_hooks_restore_total counter"
        echo "runner_hooks_restore_total ${restored}"
        echo "# HELP runner_hooks_source_missing 1 if the deploy hook source dir is absent on this host."
        echo "# TYPE runner_hooks_source_missing gauge"
        echo "runner_hooks_source_missing ${missing_src}"
        echo "# HELP runner_hooks_unit_wiring_missing Runner units whose Environment lacked the canonical job hooks on the most recent pass."
        echo "# TYPE runner_hooks_unit_wiring_missing gauge"
        echo "runner_hooks_unit_wiring_missing ${wiring_missing}"
        echo "# HELP runner_hooks_unit_dropins_written Hook-wiring drop-ins written on the most recent pass."
        echo "# TYPE runner_hooks_unit_dropins_written gauge"
        echo "runner_hooks_unit_dropins_written ${dropins_written}"
        echo "# HELP runner_hooks_env_overrides Runner .env lines overriding the canonical job hooks, found on the most recent pass."
        echo "# TYPE runner_hooks_env_overrides gauge"
        echo "runner_hooks_env_overrides ${env_overrides}"
        echo "# HELP runner_hooks_env_overrides_removed Runner .env hook overrides removed on the most recent pass."
        echo "# TYPE runner_hooks_env_overrides_removed gauge"
        echo "runner_hooks_env_overrides_removed ${env_overrides_removed}"
        echo "# HELP runner_hooks_restart_required_runners Runners flagged as requiring restart to apply .env changes."
        echo "# TYPE runner_hooks_restart_required_runners gauge"
        echo "runner_hooks_restart_required_runners ${restart_required_runners}"
        echo "# HELP runner_hooks_last_check_timestamp_seconds Unix time of the last self-heal pass."
        echo "# TYPE runner_hooks_last_check_timestamp_seconds gauge"
        echo "runner_hooks_last_check_timestamp_seconds $(date +%s)"
    } >"${tmp}"
    mv -f "${tmp}" "${PROM_FILE}"
fi

exit 0

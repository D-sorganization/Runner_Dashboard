#!/usr/bin/env bash
# heal-host.sh — drain, heal, and restart all GitHub Actions self-hosted
# runner systemd units on this host. Use after a corruption event (see
# issue #651: stale _runner_file_commands / _diag/pages residue, orphan
# Runner.Worker processes from the autoscaler kill-window race).
#
# Operationally:
#   1. Stop every actions.runner.*.service unit, forcibly killing any
#      orphan Runner.Worker / Runner.Listener processes that the
#      KillMode=process unit leaves behind.
#   2. Run the canonical cleanup (`runner-cleanup`) once with all
#      runners guaranteed idle — this lets PR #652's cleanup_runner_diag
#      and _runner_file_commands paths actually fire.
#   3. Restart every unit.
#
# Safe to run at any time. Re-entrant. Does NOT cancel running GitHub
# workflow jobs; cancel those via the dashboard or `gh` first if you
# need a guaranteed-quiet host. With jobs still assigned on the GitHub
# side, the restarted listeners will immediately pick them back up.
#
# Environment overrides:
#   RUNNER_ROOT          default /home/<RUNNER_USER>/actions-runners
#   RUNNER_USER          default $SUDO_USER, then $USER
#   CLEANUP_BIN          default /usr/local/bin/runner-cleanup
#   UNIT_PATTERN         default "actions.runner.*.service"
#   STOP_TIMEOUT_SEC     default 30   (per-unit graceful stop window)
#   DRY_RUN              default 0    (1 = print plan, don't act)
#   SKIP_CLEANUP         default 0    (1 = drain+restart only, no cleanup)
#
# Exit codes:
#   0  — drain+heal+restart completed; all units active
#   1  — one or more units failed to restart (oncall: see log)
#   2  — usage error
#
# Pairs with #660 (cleanup strict-mode fix). This script intentionally
# duplicates very little of runner-cleanup.sh — it's a thin orchestration
# wrapper around `runner-cleanup` plus the brute-force drain that
# operators previously had to do by hand.

set -Eeuo pipefail

RUNNER_USER="${RUNNER_USER:-${SUDO_USER:-$USER}}"
RUNNER_ROOT="${RUNNER_ROOT:-/home/${RUNNER_USER}/actions-runners}"
CLEANUP_BIN="${CLEANUP_BIN:-/usr/local/bin/runner-cleanup}"
UNIT_PATTERN="${UNIT_PATTERN:-actions.runner.*.service}"
STOP_TIMEOUT_SEC="${STOP_TIMEOUT_SEC:-30}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_CLEANUP="${SKIP_CLEANUP:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)        DRY_RUN=1; shift ;;
        --skip-cleanup)   SKIP_CLEANUP=1; shift ;;
        -h|--help)
            sed -n '1,40p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() {
    printf '%s heal-host: %s\n' "$(date --iso-8601=seconds)" "$*"
}

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] $*"
        return 0
    fi
    log "+ $*"
    "$@"
}

list_units() {
    systemctl list-unit-files --type=service --no-legend \
        | awk -v pat="^${UNIT_PATTERN//./\\.}$" '$1 ~ pat {print $1}' \
        | sort
}

stop_unit() {
    # systemctl stop with bounded wait; on timeout, escalate to SIGKILL of
    # the unit's cgroup. We do NOT trust `KillMode=process` (current unit
    # template) to reap children — orphan Workers are exactly the bug.
    local unit="$1"
    local mainpid
    mainpid="$(systemctl show "$unit" --property=MainPID --value 2>/dev/null || echo 0)"
    run timeout "${STOP_TIMEOUT_SEC}" systemctl stop "$unit" || {
        log "warn $unit: stop timed out, escalating"
    }
    # Belt-and-suspenders: kill any Runner.Worker / Runner.Listener
    # processes whose cmdline contains the runner-dir we owned.
    local runner_dir
    runner_dir="$(systemctl show "$unit" --property=WorkingDirectory --value 2>/dev/null || true)"
    if [[ -n "$runner_dir" && -d "$runner_dir" ]]; then
        if [[ "$DRY_RUN" == "1" ]]; then
            log "[dry-run] pkill -KILL -f Runner.(Worker|Listener) under $runner_dir"
        else
            pkill -KILL -f "${runner_dir}/.*Runner\\.\\(Worker\\|Listener\\)" 2>/dev/null || true
        fi
    fi
}

start_unit() {
    local unit="$1"
    run systemctl start "$unit"
}

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        log "must run as root (try: sudo $0 $*)"
        exit 2
    fi
}

main() {
    require_root "$@"

    local units
    mapfile -t units < <(list_units)
    if (( ${#units[@]} == 0 )); then
        log "no units matching ${UNIT_PATTERN}; nothing to do"
        exit 0
    fi
    log "found ${#units[@]} unit(s) matching ${UNIT_PATTERN}"

    log "=== drain ==="
    for unit in "${units[@]}"; do
        stop_unit "$unit"
    done

    # Final sweep: anything still running that *looks* like a runner is
    # an orphan. Force-kill so cleanup can touch the filesystem cleanly.
    if [[ "$DRY_RUN" != "1" ]]; then
        pkill -KILL -f 'Runner\.Worker spawnclient' 2>/dev/null || true
        sleep 1
        pkill -KILL -f 'Runner\.Listener'           2>/dev/null || true
        sleep 1
    fi

    log "=== cleanup ==="
    if [[ "$SKIP_CLEANUP" == "1" ]]; then
        log "SKIP_CLEANUP=1; not invoking ${CLEANUP_BIN}"
    elif [[ ! -x "$CLEANUP_BIN" ]]; then
        log "warn ${CLEANUP_BIN} not installed; skipping cleanup pass"
        log "warn install via deploy/install-runner-maintenance.sh"
    else
        run env \
            RUNNER_ROOT="$RUNNER_ROOT" \
            RUNNER_USER="$RUNNER_USER" \
            "$CLEANUP_BIN" || {
            log "warn cleanup exited non-zero; continuing to restart phase"
        }
    fi

    log "=== restart ==="
    local rc=0 inactive=()
    for unit in "${units[@]}"; do
        start_unit "$unit" || rc=1
    done
    if [[ "$DRY_RUN" != "1" ]]; then
        # Verify everyone made it back up.
        sleep 2
        for unit in "${units[@]}"; do
            if ! systemctl is-active --quiet "$unit"; then
                inactive+=("$unit")
                rc=1
            fi
        done
    fi
    if (( ${#inactive[@]} > 0 )); then
        log "ERROR: ${#inactive[@]} unit(s) failed to come back up:"
        for unit in "${inactive[@]}"; do log "  - $unit"; done
        log "investigate with: systemctl status <unit> && journalctl -u <unit> -n 50"
    else
        log "all ${#units[@]} unit(s) active"
    fi

    return "$rc"
}

main "$@"

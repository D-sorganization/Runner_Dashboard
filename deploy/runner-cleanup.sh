#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
# RUNNER_ROOTS lets a single host serve runners from multiple parent
# directories (e.g. one set under HDD storage, another set under NVMe).
# Format: colon-separated absolute paths, each containing runner-<N>
# subdirs. If unset, falls back to RUNNER_ROOT for backwards compat.
# The cleanup pass accepts a runner workdir if it sits under ANY of
# these roots. Guards against: 2026-05-28 disk fill where
# /home/dieterolson/actions-runners-nvme/runner-*/_work grew unbounded
# because the daily cleanup only knew about
# /home/dieterolson/actions-runners/runner-* and silently skipped
# every nvme unit with "unexpected WorkingDirectory".
RUNNER_ROOTS="${RUNNER_ROOTS:-$RUNNER_ROOT}"
RUNNER_USER="${RUNNER_USER:-${SUDO_USER:-$USER}}"
LOG_DIR="${LOG_DIR:-/var/log/runner-cleanup}"
RUNNER_WORK_DAYS="${RUNNER_WORK_DAYS:-3}"
RUNNER_TEMP_DAYS="${RUNNER_TEMP_DAYS:-1}"
TOOL_CACHE_DAYS="${TOOL_CACHE_DAYS:-21}"
DOCKER_PRUNE_UNTIL="${DOCKER_PRUNE_UNTIL:-168h}"
JOURNAL_MAX_SIZE="${JOURNAL_MAX_SIZE:-1G}"
DISK_PRESSURE_PERCENT="${DISK_PRESSURE_PERCENT:-85}"
AGGRESSIVE_ON_PRESSURE="${AGGRESSIVE_ON_PRESSURE:-1}"
PRUNE_DOCKER_VOLUMES="${PRUNE_DOCKER_VOLUMES:-0}"
# Set to 1 automatically when disk pressure is detected (see main()). When on,
# cleanup_docker() ignores age windows and reclaims ALL build cache (incl.
# buildx builders), unused images, and dangling volumes. Docker is the dominant
# space consumer on these hosts (build cache + volumes), and the routine
# age-windowed prune alone let the disk fill to 100% between daily runs
# (2026-05-29 nvme outage). Pruning never stops/removes running containers or
# in-use volumes, so live jobs are unaffected.
DOCKER_AGGRESSIVE="${DOCKER_AGGRESSIVE:-0}"
# Disk-guard mode: a lightweight, runner-safe pass that reclaims docker +
# journal + fstrim ONLY. It never stops runner units, so it is safe to run
# frequently (hourly timer) to catch docker bloat long before the daily full
# cleanup. The full cleanup still bounces idle runners to clear _work.
DISK_GUARD="${DISK_GUARD:-0}"
DRY_RUN="${DRY_RUN:-0}"
COMPACT_VHD="${COMPACT_VHD:-0}"
COMPACT_VHD_ONLY="${COMPACT_VHD_ONLY:-0}"
COMPACT_VHD_DISTRO="${COMPACT_VHD_DISTRO:-Ubuntu-22.04}"
LOCK_FILE="/run/runner-cleanup.lock"
TEXTFILE_COLLECTOR_DIR="${TEXTFILE_COLLECTOR_DIR:-/var/lib/node_exporter/textfile_collector}"
CLEANUP_METRICS_FILE="${CLEANUP_METRICS_FILE:-${TEXTFILE_COLLECTOR_DIR}/runner_cleanup.prom}"

# Tracked by write_metrics(): incremented every time cleanup_runners() fails
# to stop a runner unit. Surfaced as runner_cleanup_stop_failures_total.
CLEANUP_STOP_FAILURES=0
# Tracked by write_metrics(): "ok" for a clean run, "skipped" when another
# instance held the lock, "failed" when main() returns non-zero or the EXIT
# trap fires before main completes.
CLEANUP_RESULT="failed"

# Parse flags. Keep env-var style as the primary interface; flags are a
# convenience wrapper so callers can say `runner-cleanup.sh --compact-vhd`.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --compact-vhd)       COMPACT_VHD=1; shift ;;
        --compact-vhd-only)  COMPACT_VHD_ONLY=1; COMPACT_VHD=1; shift ;;
        --compact-vhd-distro)
            COMPACT_VHD_DISTRO="${2:?--compact-vhd-distro requires a value}"
            shift 2 ;;
        --disk-guard)        DISK_GUARD=1; shift ;;
        --dry-run)           DRY_RUN=1; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: runner-cleanup.sh [--compact-vhd] [--compact-vhd-only]
                         [--compact-vhd-distro NAME] [--disk-guard] [--dry-run]

Environment overrides: RUNNER_ROOT, RUNNER_USER, LOG_DIR, RUNNER_WORK_DAYS,
RUNNER_TEMP_DAYS, TOOL_CACHE_DAYS, DOCKER_PRUNE_UNTIL, JOURNAL_MAX_SIZE,
DISK_PRESSURE_PERCENT, AGGRESSIVE_ON_PRESSURE, PRUNE_DOCKER_VOLUMES,
DOCKER_AGGRESSIVE, DISK_GUARD, COMPACT_VHD, COMPACT_VHD_ONLY,
COMPACT_VHD_DISTRO, DRY_RUN.

--disk-guard        Lightweight, runner-safe pass: reclaim docker + journal +
                    fstrim ONLY (never stops runner units). Goes aggressive on
                    docker when used% >= DISK_PRESSURE_PERCENT. Safe to run
                    frequently (hourly timer) to keep docker bloat in check
                    between the daily full cleanups.
--compact-vhd       After cleanup, invoke scripts/compact-wsl-vhd.sh to
                    shrink the WSL2 ext4.vhdx back to Windows.
                    Requires UAC elevation on the Windows host.
                    See docs/operations/vhd_compaction.md.
--compact-vhd-only  Skip all in-guest cleanup; just compact the VHDX.
EOF
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ "$COMPACT_VHD_ONLY" == "1" ]]; then
    COMPACT_VHD=1
fi

mkdir -p "$LOG_DIR"

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] $*"
        return 0
    fi
    log "+ $*"
    "$@"
}

delete_path() {
    local path="$1"
    log "delete $path"
    [[ "$DRY_RUN" == "1" ]] || rm -rf -- "$path"
}

write_metrics() {
    # Emit a Prometheus textfile-collector snapshot of the last cleanup pass.
    # Invoked from an EXIT trap so partial / failed runs still publish a
    # signal. Failures here are non-fatal: the trap must never mask the
    # underlying exit status of main().
    local host metrics_dir tmp_file
    host="${FLEET_NODE_NAME:-$(hostname -s 2>/dev/null || hostname)}"
    metrics_dir="$(dirname -- "$CLEANUP_METRICS_FILE")"
    if ! mkdir -p -- "$metrics_dir" 2>/dev/null; then
        log "metrics: cannot create $metrics_dir; skipping textfile emit"
        return 0
    fi
    tmp_file="${CLEANUP_METRICS_FILE}.tmp"
    {
        printf '# HELP runner_cleanup_runs_total Total runner cleanup passes by result.\n'
        printf '# TYPE runner_cleanup_runs_total counter\n'
        for result in ok skipped failed; do
            local value=0
            [[ "$result" == "$CLEANUP_RESULT" ]] && value=1
            printf 'runner_cleanup_runs_total{host="%s",result="%s"} %d\n' \
                "$host" "$result" "$value"
        done
        printf '# HELP runner_cleanup_stop_failures_total Runner unit stop failures observed in the last cleanup pass.\n'
        printf '# TYPE runner_cleanup_stop_failures_total counter\n'
        printf 'runner_cleanup_stop_failures_total{host="%s"} %d\n' \
            "$host" "$CLEANUP_STOP_FAILURES"
        printf '# HELP runner_cleanup_last_run_timestamp_seconds Unix timestamp of the last cleanup pass.\n'
        printf '# TYPE runner_cleanup_last_run_timestamp_seconds gauge\n'
        printf 'runner_cleanup_last_run_timestamp_seconds{host="%s"} %d\n' \
            "$host" "$(date +%s)"
    } >"$tmp_file" 2>/dev/null || {
        log "metrics: failed writing $tmp_file"
        rm -f -- "$tmp_file" 2>/dev/null || true
        return 0
    }
    mv -f -- "$tmp_file" "$CLEANUP_METRICS_FILE" 2>/dev/null \
        || log "metrics: failed publishing $CLEANUP_METRICS_FILE"
}

bytes_human() {
    numfmt --to=iec-i --suffix=B --format='%.1f' "$1" 2>/dev/null || printf '%sB' "$1"
}

root_used_percent() {
    df -P / | awk 'NR == 2 {gsub("%", "", $5); print $5}'
}

root_used_bytes() {
    df -PB1 / | awk 'NR == 2 {print $3}'
}

service_workdir() {
    systemctl show "$1" --property=WorkingDirectory --value 2>/dev/null || true
}

unit_active() {
    systemctl is-active --quiet "$1"
}

RUNNER_BUSY_LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_BUSY_LOCK_MAX_AGE_SECONDS="${RUNNER_BUSY_LOCK_MAX_AGE_SECONDS:-86400}"
RUNNER_PICKUP_DIR_MAX_AGE_SECONDS="${RUNNER_PICKUP_DIR_MAX_AGE_SECONDS:-30}"

runner_busy_via_pickup_dir() {
    # Mirror of backend/runner_autoscaler.py::_runner_busy_via_pickup_dir.
    # The Listener writes _work/_temp/_runner_file_commands/ BEFORE forking
    # the Worker. A recent mtime means the Listener has accepted a job
    # but the Worker hasn't started yet — the exact race window that left
    # corruption residue in issue #651. Stale residue (older than
    # RUNNER_PICKUP_DIR_MAX_AGE_SECONDS) is NOT treated as busy; it gets
    # GC'd by the existing cleanup_runner_workdir paths.
    local runner_dir="$1"
    local fc="${runner_dir}/_work/_temp/_runner_file_commands"
    [[ -d "$fc" ]] || return 1
    local mtime age
    mtime=$(stat -c %Y "$fc" 2>/dev/null || echo 0)
    age=$(( $(date +%s) - mtime ))
    if (( age <= RUNNER_PICKUP_DIR_MAX_AGE_SECONDS )); then
        log "pickup-dir fresh (age=${age}s) — treating $(basename "$runner_dir") as busy"
        return 0
    fi
    return 1
}

runner_name_for_unit() {
    # actions.runner.<org-or-repo>.<runner-name>.service -> <runner-name>
    # Mirrors backend/autoscaler_systemd.py::_runner_name_for_unit. The
    # JOB_STARTED hook keys its lockfile on the *registered* runner name
    # (the unit's last dotted segment), NOT the workdir basename.
    local unit="$1" stem
    stem="${unit%.service}"
    printf '%s' "${stem##*.}"
}

runner_busy_via_lockfile() {
    # Issue #651 defense-in-depth signal mirroring backend/runner_autoscaler.py's
    # _runner_busy_via_lockfile. The runner's JOB_STARTED hook writes
    # ${RUNNER_BUSY_LOCK_DIR}/<runner-name>.lock; we treat a fresh lockfile
    # as "busy". Stale ones (older than RUNNER_BUSY_LOCK_MAX_AGE_SECONDS)
    # are ignored AND garbage-collected here so a Worker killed mid-job
    # doesn't permanently lock its runner out of cleanup.
    #
    # NOTE (#640): the argument is the *registered runner name* (e.g.
    # d-sorg-local-ControlTower-nvme-4), not the workdir basename (runner-4).
    # The hook writes ${RUNNER_NAME}.lock, so keying on the workdir basename
    # never matched and this signal was silently dead.
    local runner_name="$1"
    local lock now mtime age
    lock="${RUNNER_BUSY_LOCK_DIR}/${runner_name}.lock"
    [[ -f "$lock" ]] || return 1
    now=$(date +%s)
    mtime=$(stat -c %Y "$lock" 2>/dev/null || echo 0)
    age=$(( now - mtime ))
    if (( age > RUNNER_BUSY_LOCK_MAX_AGE_SECONDS )); then
        log "stale runner-busy lockfile (age=${age}s) — removing $lock"
        rm -f "$lock"
        return 1
    fi
    return 0
}

runner_busy() {
    local unit="$1" runner_dir="$2"
    local runner_name
    runner_name="$(runner_name_for_unit "$unit")"
    # Layered signals (#651/#640). ANY positive signal counts as busy:
    #  1. Pickup-dir mtime  — recent file-command activity mid-job
    #  2. Lockfile          — Worker has fired JOB_STARTED hook (whole-job span)
    #  3. Process tree      — a live Runner.Worker for this workdir
    if runner_busy_via_pickup_dir "$runner_dir"; then
        return 0
    fi
    if runner_busy_via_lockfile "$runner_name"; then
        return 0
    fi
    ps -eo args= | grep -F "${runner_dir}/bin/Runner.Worker" | grep -v 'grep -F' >/dev/null 2>&1
}

list_runner_units() {
    systemctl list-unit-files --type=service --no-legend \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}' \
        | sort
}

cleanup_runner_workdir() {
    local runner_dir="$1"
    local work_dir="${runner_dir}/_work"
    [[ -d "$work_dir" ]] || return 0
    log "cleaning runner workdir: $work_dir"
    find "$work_dir" -mindepth 1 -maxdepth 1 -type d \
        ! -name '_actions' \
        ! -name '_PipelineMapping' \
        ! -name '_temp' \
        ! -name '_tool' \
        -mtime +"$RUNNER_WORK_DAYS" \
        -print0 | while IFS= read -r -d '' path; do
            delete_path "$path"
        done
    if [[ -d "$work_dir/_temp" ]]; then
        find "$work_dir/_temp" -mindepth 1 -mtime +"$RUNNER_TEMP_DAYS" \
            -print0 | while IFS= read -r -d '' path; do
                delete_path "$path"
            done
    fi
    if [[ -d "$work_dir/_tool" ]]; then
        find "$work_dir/_tool" -mindepth 2 -maxdepth 2 -type d \
            -mtime +"$TOOL_CACHE_DAYS" \
            -print0 | while IFS= read -r -d '' path; do
                delete_path "$path"
            done
    fi
    # issue #651: clean stale _runner_file_commands left over from
    # jobs that died mid-execution. The directory is normally
    # created+deleted within a single job lifecycle; anything still
    # here when the runner is idle is corruption that causes the
    # next allocation to fail with "Missing file at path:
    # .../_runner_file_commands/save_state_<uuid>".
    if [[ -d "$work_dir/_temp/_runner_file_commands" ]]; then
        delete_path "$work_dir/_temp/_runner_file_commands"
    fi
}

cleanup_runner_diag() {
    # issue #651: rotate the runner's _diag/pages/ output. The actions
    # runner writes UUID-named log files there per page-event; if two
    # runs collide on the same UUID the runner aborts with "The file
    # '.../_diag/pages/<uuid>_<uuid>_1.log' already exists". Capping
    # age + leaving the directory itself in place prevents both the
    # collision and the next allocation's startup failure.
    local runner_dir="$1"
    local diag_dir="${runner_dir}/_diag/pages"
    [[ -d "$diag_dir" ]] || return 0
    find "$diag_dir" -maxdepth 1 -type f -name '*.log' \
        -mtime +"$RUNNER_TEMP_DAYS" \
        -print0 | while IFS= read -r -d '' path; do
            delete_path "$path"
        done
}

cleanup_runners() {
    # Per-runner block: a stop/start failure on ONE runner must not abort the
    # whole pass. Before this guard, `systemctl stop` returning exit 1 (which
    # happens when stop races with a job pickup on that unit and systemctl
    # reports "Job for ... canceled") tripped `set -e` and skipped runners 2..N.
    # Result: on busy hosts the cleanup was a silent no-op every night.
    local unit runner_dir was_active stop_rc start_rc
    while read -r unit; do
        [[ -n "$unit" ]] || continue
        runner_dir="$(service_workdir "$unit")"
        if [[ -z "$runner_dir" || ! -d "$runner_dir" ]]; then
            log "skip $unit: unexpected WorkingDirectory '$runner_dir'"
            continue
        fi
        # Accept the workdir if it sits directly under any configured runner root.
        local _matched=0 _root
        IFS=':' read -r -a _roots_arr <<<"$RUNNER_ROOTS"
        for _root in "${_roots_arr[@]}"; do
            [[ -n "$_root" ]] || continue
            if [[ "$runner_dir" == "$_root"/runner-* ]]; then
                _matched=1
                break
            fi
        done
        if (( _matched == 0 )); then
            log "skip $unit: workdir '$runner_dir' not under any RUNNER_ROOTS entry ($RUNNER_ROOTS)"
            continue
        fi
        if runner_busy "$unit" "$runner_dir"; then
            log "skip $unit: runner is busy"
            continue
        fi
        was_active=0
        stop_rc=0
        if unit_active "$unit"; then
            was_active=1
            run systemctl stop "$unit" || stop_rc=$?
            if (( stop_rc != 0 )); then
                # Increment the metric for #663's textfile-collector path.
                CLEANUP_STOP_FAILURES=$((CLEANUP_STOP_FAILURES + 1))
                # If a job was assigned in the brief window between runner_busy()
                # and systemctl stop, the stop is racy. Skip cleanup for this
                # runner this pass; the next pass will catch it once idle.
                log "skip $unit: systemctl stop failed (rc=$stop_rc), likely raced with job pickup; will retry next pass"
                if ! unit_active "$unit"; then
                    run systemctl start "$unit" || log "warn $unit: restart after failed stop also failed"
                fi
                continue
            fi
        fi
        cleanup_runner_workdir "$runner_dir"
        cleanup_runner_diag "$runner_dir"
        if [[ "$was_active" == "1" ]]; then
            start_rc=0
            run systemctl start "$unit" || start_rc=$?
            if (( start_rc != 0 )); then
                log "error $unit: failed to restart after cleanup (rc=$start_rc); host may have a stuck unit"
            fi
        fi
    done < <(list_runner_units)
}

cleanup_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        log "docker unavailable; skipping docker cleanup"
        return 0
    fi
    if [[ "$DOCKER_AGGRESSIVE" == "1" ]]; then
        # Disk pressure: reclaim everything reclaimable, ignoring age windows.
        # `prune` only ever removes STOPPED containers, UNUSED images, DANGLING
        # volumes, and idle build cache, so running jobs / in-use volumes /
        # active buildx builders are never touched.
        run docker container prune --force
        run docker builder prune --all --force
        # buildx builders keep their own cache pools; prune those too when the
        # buildx plugin is present (the moby/buildkit builder containers on this
        # host accumulate tens of GB of cache).
        if docker buildx version >/dev/null 2>&1; then
            run docker buildx prune --all --force
        fi
        run docker image prune --all --force
        run docker volume prune --force
    else
        run docker container prune --force --filter "until=72h"
        run docker builder prune --all --force --filter "until=${DOCKER_PRUNE_UNTIL}"
        run docker image prune --force --filter "until=${DOCKER_PRUNE_UNTIL}"
        if [[ "$PRUNE_DOCKER_VOLUMES" == "1" ]]; then run docker volume prune --force; fi
    fi
}

cleanup_common_caches() {
    run apt-get autoclean
    command -v pip3 >/dev/null 2>&1 && run sudo -u "$RUNNER_USER" -H pip3 cache purge
    command -v npm >/dev/null 2>&1 && run sudo -u "$RUNNER_USER" -H npm cache verify
    command -v pre-commit >/dev/null 2>&1 && run sudo -u "$RUNNER_USER" -H pre-commit gc
    command -v journalctl >/dev/null 2>&1 && run journalctl --vacuum-size="$JOURNAL_MAX_SIZE"
}

# Compact the WSL2 VHDX after in-guest cleanup. Invokes the shared
# scripts/compact-wsl-vhd.sh helper which in turn triggers an elevated
# powershell.exe Optimize-VHD on the Windows host. Terminates the running
# WSL session by design (wsl --shutdown), so only call when the caller has
# opted in via --compact-vhd / COMPACT_VHD=1.
compact_wsl_vhd() {
    local here repo_root helper
    here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    repo_root="$(cd -- "$here/../.." && pwd)"
    helper="$repo_root/scripts/compact-wsl-vhd.sh"
    if [[ ! -x "$helper" ]]; then
        log "compact-vhd: helper not found or not executable at $helper; skipping"
        return 0
    fi
    if ! command -v powershell.exe >/dev/null 2>&1; then
        log "compact-vhd: powershell.exe unavailable (not a WSL host?); skipping"
        return 0
    fi
    log "compact-vhd: invoking $helper (distro=${COMPACT_VHD_DISTRO})"
    if [[ "$DRY_RUN" == "1" ]]; then
        run "$helper" --distro "$COMPACT_VHD_DISTRO" --dry-run
    else
        run "$helper" --distro "$COMPACT_VHD_DISTRO"
    fi
}

main() {
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "another cleanup is already running; exiting"
        CLEANUP_RESULT="skipped"
        return 0
    fi
    local before after used
    before="$(root_used_bytes)"
    used="$(root_used_percent)"
    log "runner cleanup starting root_used=${used}% root_size=$(bytes_human "$before") dry_run=$DRY_RUN"
    if [[ "$AGGRESSIVE_ON_PRESSURE" == "1" && "$used" -ge "$DISK_PRESSURE_PERCENT" ]]; then
        log "disk pressure detected (${used}% >= ${DISK_PRESSURE_PERCENT}%); pruning docker aggressively and lowering retention windows"
        RUNNER_WORK_DAYS=0
        RUNNER_TEMP_DAYS=0
        TOOL_CACHE_DAYS=7
        DOCKER_AGGRESSIVE=1
    fi
    if [[ "$DISK_GUARD" == "1" ]]; then
        # Runner-safe fast pass: reclaim docker + journal + trim only. Never
        # touches runner units, so it can run on a frequent timer without
        # bouncing idle runners every pass. Goes aggressive on docker when the
        # pressure block above fired.
        log "disk-guard mode: docker + journal + fstrim only (no runner bounce)"
        cleanup_docker
        command -v journalctl >/dev/null 2>&1 && run journalctl --vacuum-size="$JOURNAL_MAX_SIZE"
        command -v fstrim >/dev/null 2>&1 && run fstrim -av
    elif [[ "$COMPACT_VHD_ONLY" != "1" ]]; then
        cleanup_runners
        cleanup_docker
        cleanup_common_caches
        command -v fstrim >/dev/null 2>&1 && run fstrim -av
    else
        log "compact-vhd-only mode: skipping in-guest cleanup"
    fi
    after="$(root_used_bytes)"
    log "runner cleanup finished root_size=$(bytes_human "$after") reclaimed_estimate=$(bytes_human "$((before - after))")"
    if [[ "$COMPACT_VHD" == "1" ]]; then
        compact_wsl_vhd
    fi
    CLEANUP_RESULT="ok"
}

# Emit a textfile-collector metric on every exit path. The trap fires
# regardless of whether main() returned cleanly, hit set -e, or was
# interrupted, so silent regressions (issue #651) can no longer accumulate
# undetected — Prometheus will see runner_cleanup_runs_total{result="failed"}.
trap 'write_metrics' EXIT

main "$@"

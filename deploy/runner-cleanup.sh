#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
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
DRY_RUN="${DRY_RUN:-0}"
COMPACT_VHD="${COMPACT_VHD:-0}"
COMPACT_VHD_ONLY="${COMPACT_VHD_ONLY:-0}"
COMPACT_VHD_DISTRO="${COMPACT_VHD_DISTRO:-Ubuntu-22.04}"
LOCK_FILE="/run/runner-cleanup.lock"

# Parse flags. Keep env-var style as the primary interface; flags are a
# convenience wrapper so callers can say `runner-cleanup.sh --compact-vhd`.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --compact-vhd)       COMPACT_VHD=1; shift ;;
        --compact-vhd-only)  COMPACT_VHD_ONLY=1; COMPACT_VHD=1; shift ;;
        --compact-vhd-distro)
            COMPACT_VHD_DISTRO="${2:?--compact-vhd-distro requires a value}"
            shift 2 ;;
        --dry-run)           DRY_RUN=1; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: runner-cleanup.sh [--compact-vhd] [--compact-vhd-only]
                         [--compact-vhd-distro NAME] [--dry-run]

Environment overrides: RUNNER_ROOT, RUNNER_USER, LOG_DIR, RUNNER_WORK_DAYS,
RUNNER_TEMP_DAYS, TOOL_CACHE_DAYS, DOCKER_PRUNE_UNTIL, JOURNAL_MAX_SIZE,
DISK_PRESSURE_PERCENT, AGGRESSIVE_ON_PRESSURE, PRUNE_DOCKER_VOLUMES,
COMPACT_VHD, COMPACT_VHD_ONLY, COMPACT_VHD_DISTRO, DRY_RUN.

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

runner_busy() {
    local runner_dir="$1"
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
}

cleanup_runners() {
    local unit runner_dir was_active
    while read -r unit; do
        [[ -n "$unit" ]] || continue
        runner_dir="$(service_workdir "$unit")"
        if [[ -z "$runner_dir" || ! -d "$runner_dir" || "$runner_dir" != "$RUNNER_ROOT"/runner-* ]]; then
            log "skip $unit: unexpected WorkingDirectory '$runner_dir'"
            continue
        fi
        if runner_busy "$runner_dir"; then
            log "skip $unit: runner is busy"
            continue
        fi
        was_active=0
        if unit_active "$unit"; then
            was_active=1
            run systemctl stop "$unit"
        fi
        cleanup_runner_workdir "$runner_dir"
        if [[ "$was_active" == "1" ]]; then
            run systemctl start "$unit"
        fi
    done < <(list_runner_units)
}

cleanup_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        log "docker unavailable; skipping docker cleanup"
        return 0
    fi
    run docker container prune --force --filter "until=72h"
    run docker builder prune --all --force --filter "until=${DOCKER_PRUNE_UNTIL}"
    run docker image prune --force --filter "until=${DOCKER_PRUNE_UNTIL}"
    [[ "$PRUNE_DOCKER_VOLUMES" == "1" ]] && run docker volume prune --force
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
        return 0
    fi
    local before after used
    before="$(root_used_bytes)"
    used="$(root_used_percent)"
    log "runner cleanup starting root_used=${used}% root_size=$(bytes_human "$before") dry_run=$DRY_RUN"
    if [[ "$AGGRESSIVE_ON_PRESSURE" == "1" && "$used" -ge "$DISK_PRESSURE_PERCENT" ]]; then
        log "disk pressure detected; lowering retention windows"
        RUNNER_WORK_DAYS=0
        RUNNER_TEMP_DAYS=0
        TOOL_CACHE_DAYS=7
    fi
    if [[ "$COMPACT_VHD_ONLY" != "1" ]]; then
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
}

main "$@"

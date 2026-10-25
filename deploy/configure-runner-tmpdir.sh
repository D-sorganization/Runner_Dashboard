#!/usr/bin/env bash
# configure-runner-tmpdir.sh — point each self-hosted runner's TMPDIR at the
# data disk instead of RAM-backed /tmp (Repository_Management#1489 / #1495).
#
# Why: pip build trees (pip-install-*, pip-build-env-*, ...), pytest and
# Python tempfile scratch all land in $TMPDIR, which defaults to /tmp. On
# hosts where /tmp is tmpfs (OGLaptop: 24G) a handful of cancelled jobs
# exhaust it, every later `pip install` on the host dies with ENOSPC, the
# dependency-install step fails, and required checks hard-block PRs
# fleet-wide while every root-disk gate stays green.
#
# What: for every actions.runner.*.service on the host, write
#   TMPDIR=<runner_dir>/_work/_tmp
# into <runner_dir>/.env (the runner exports every line of that file into
# job processes) and create the directory owned by the runner user. The
# fleet GC (runner-cleanup.sh: cleanup_runner_tmpdirs) reaps CI litter from
# that directory while the runner is idle, with the same allowlist and age
# window as /tmp.
#
# Idempotent: an existing TMPDIR= line is replaced, nothing else in .env is
# touched, and a run that changes nothing prints "unchanged". Never
# restarts anything — the runner reads .env at start-up, so the owner
# restarts idle runners afterwards (or lets the nightly full cleanup, which
# bounces idle runners, pick it up):
#
#   sudo RUNNER_USER=<user> deploy/configure-runner-tmpdir.sh [--dry-run]
#   sudo systemctl restart 'actions.runner.*.service'   # when idle
#
# Verify from a job: `echo $TMPDIR` / `python -c "import tempfile;print(tempfile.gettempdir())"`.
set -Eeuo pipefail

RUNNER_USER="${RUNNER_USER:-${SUDO_USER:-$USER}}"
RUNNER_TMP_SUBDIR="${RUNNER_TMP_SUBDIR:-_work/_tmp}"
DRY_RUN=0
# Explicit runner dirs may be passed instead of discovering units (tests,
# hosts without systemd).
RUNNER_DIRS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --runner-dir) RUNNER_DIRS+=("${2:?--runner-dir requires a path}"); shift 2 ;;
        -h|--help)
            sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { printf '%s configure-runner-tmpdir: %s\n' "$(date '+%F %T')" "$*"; }

discover_runner_dirs() {
    command -v systemctl >/dev/null 2>&1 || return 0
    systemctl list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}' \
        | sort \
        | while read -r unit; do
            [[ -n "$unit" ]] || continue
            systemctl show "$unit" --property=WorkingDirectory --value 2>/dev/null || true
        done
}

# configure_runner <runner_dir> -> sets RESULT to changed|unchanged|skipped.
# (A global rather than stdout so log lines stay visible to the operator.)
RESULT=""
configure_runner() {
    local runner_dir="$1"
    local env_file="${runner_dir}/.env"
    local tmp_dir="${runner_dir}/${RUNNER_TMP_SUBDIR}"
    local wanted="TMPDIR=${tmp_dir}"
    if [[ ! -d "$runner_dir" || ! -x "${runner_dir}/run.sh" && ! -d "${runner_dir}/bin" ]]; then
        log "skip ${runner_dir}: not a runner directory"
        RESULT=skipped
        return 0
    fi
    local current=""
    if [[ -f "$env_file" ]]; then
        current="$(grep -E '^TMPDIR=' "$env_file" | tail -1 || true)"
    fi
    if [[ "$current" == "$wanted" && -d "$tmp_dir" ]]; then
        log "unchanged ${runner_dir} (${wanted})"
        RESULT=unchanged
        return 0
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
        log "would set ${wanted} in ${env_file} and create ${tmp_dir}"
        RESULT=changed
        return 0
    fi
    install -d -m 0755 -o "$RUNNER_USER" -g "$RUNNER_USER" "$tmp_dir"
    local staged
    staged="$(mktemp "${runner_dir}/.env.XXXXXX")"
    if [[ -f "$env_file" ]]; then
        grep -vE '^TMPDIR=' "$env_file" > "$staged" || true
    fi
    printf '%s\n' "$wanted" >> "$staged"
    chown "$RUNNER_USER":"$RUNNER_USER" "$staged"
    chmod 0644 "$staged"
    mv -f "$staged" "$env_file"
    log "set ${wanted} in ${env_file}"
    RESULT=changed
}

main() {
    if (( ${#RUNNER_DIRS[@]} == 0 )); then
        while read -r dir; do
            [[ -n "$dir" ]] && RUNNER_DIRS+=("$dir")
        done < <(discover_runner_dirs)
    fi
    if (( ${#RUNNER_DIRS[@]} == 0 )); then
        log "no actions.runner.*.service units found and no --runner-dir given; nothing to do"
        return 0
    fi
    local changed=0 dir
    for dir in "${RUNNER_DIRS[@]}"; do
        configure_runner "$dir"
        [[ "$RESULT" == "changed" ]] && changed=$((changed + 1))
    done
    log "done: ${changed} runner(s) changed (dry_run=${DRY_RUN})"
    if (( changed > 0 && DRY_RUN == 0 )); then
        log "restart idle runners to apply: sudo systemctl restart 'actions.runner.*.service'"
    fi
}

main "$@"

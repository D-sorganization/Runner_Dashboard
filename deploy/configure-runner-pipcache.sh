#!/usr/bin/env bash
# configure-runner-pipcache.sh — point each self-hosted runner's PIP_CACHE_DIR
# at a private per-runner directory (<runner>/_work/_pip-cache) instead of
# a shared pip cache or ~/.cache/pip (Runner_Dashboard#1896).
#
# Why: concurrent jobs on one host sharing a pip cache race on temporary
# download files (raising FileNotFoundError in http-v2/) or fail with ownership
# and permission errors ("cache has been disabled"). Dedicated per-runner
# pip caches eliminate cache collisions while retaining wheel reuse across
# jobs on the same runner.
#
# What: for every actions.runner.*.service on the host, write
#   PIP_CACHE_DIR=<runner_dir>/_work/_pip-cache
# into <runner_dir>/.env (the runner exports every line of that file into
# job processes) and create the directory owned by the runner user.
#
# Idempotent: an existing PIP_CACHE_DIR= line is replaced, nothing else in .env is
# touched, and a run that changes nothing prints "unchanged". Never
# restarts anything — the runner reads .env at start-up, so the owner
# restarts idle runners afterwards:
#
#   sudo RUNNER_USER=<user> deploy/configure-runner-pipcache.sh [--dry-run]
#   sudo systemctl restart 'actions.runner.*.service'   # when idle
#
# Verify from a job: `echo $PIP_CACHE_DIR` / `python -m pip cache list`.
set -Eeuo pipefail

RUNNER_USER="${RUNNER_USER:-${SUDO_USER:-$USER}}"
RUNNER_PIPCACHE_SUBDIR="${RUNNER_PIPCACHE_SUBDIR:-_work/_pip-cache}"
DRY_RUN=0
RUNNER_DIRS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --runner-dir) RUNNER_DIRS+=("${2:?--runner-dir requires a path}"); shift 2 ;;
        -h|--help)
            sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

log() { printf '%s configure-runner-pipcache: %s\n' "$(date '+%F %T')" "$*"; }

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

RESULT=""
configure_runner() {
    local runner_dir="$1"
    local env_file="${runner_dir}/.env"
    local cache_dir="${runner_dir}/${RUNNER_PIPCACHE_SUBDIR}"
    local wanted="PIP_CACHE_DIR=${cache_dir}"
    if [[ ! -d "$runner_dir" || ! -x "${runner_dir}/run.sh" && ! -d "${runner_dir}/bin" ]]; then
        log "skip ${runner_dir}: not a runner directory"
        RESULT=skipped
        return 0
    fi
    local current=""
    if [[ -f "$env_file" ]]; then
        current="$(grep -E '^PIP_CACHE_DIR=' "$env_file" | tail -1 || true)"
    fi
    if [[ "$current" == "$wanted" && -d "$cache_dir" ]]; then
        log "unchanged ${runner_dir} (${wanted})"
        RESULT=unchanged
        return 0
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
        log "would set ${wanted} in ${env_file} and create ${cache_dir}"
        RESULT=changed
        return 0
    fi
    install -d -m 0755 -o "$RUNNER_USER" -g "$RUNNER_USER" "$cache_dir" 2>/dev/null || mkdir -p "$cache_dir"
    local staged
    staged="$(mktemp "${runner_dir}/.env.XXXXXX")"
    if [[ -f "$env_file" ]]; then
        grep -vE '^PIP_CACHE_DIR=' "$env_file" > "$staged" || true
    fi
    printf '%s\n' "$wanted" >> "$staged"
    chown "$RUNNER_USER":"$RUNNER_USER" "$staged" 2>/dev/null || true
    chmod 0644 "$staged" 2>/dev/null || true
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

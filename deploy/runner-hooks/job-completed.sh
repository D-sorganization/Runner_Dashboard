#!/usr/bin/env bash
# Runner job-completed hook (ACTIONS_RUNNER_HOOK_JOB_COMPLETED).
#
# Removes the sentinel lockfile written by job-started.sh. See
# job-started.sh for full context (issue #651).
#
# Notes on robustness:
# - Hook is called by the Worker on success, failure, AND cancellation,
#   so the lockfile is cleared in all normal terminations.
# - If the Worker is killed mid-job (autoscaler race, OOM, etc.) the
#   lockfile stays behind. The cleanup pass (runner-cleanup.sh) garbage-
#   collects stale lockfiles older than $RUNNER_BUSY_LOCK_MAX_AGE_SECONDS
#   (default 86400 = 24h) so they don't permanently mark a runner as
#   busy. A pre-stop sweep in heal-host.sh also clears them.

set -u

LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_NAME="${RUNNER_NAME:-${HOSTNAME}-unknown}"

LOCK_FILE="${LOCK_DIR}/${RUNNER_NAME}.lock"
rm -f "$LOCK_FILE" 2>/dev/null || true

# -- Runner temp cleanup (2026-10-17, supersedes runner-post-job.sh) ---------
# hub-a runners carried a legacy ACTIONS_RUNNER_HOOK_JOB_COMPLETED in
# <runner>/.env pointing at ~/actions-runners/fleet/runner-post-job.sh.  The
# runner .env wins over the systemd unit's Environment=, so the canonical hook
# never ran there; the legacy script's only job was clearing _work/_temp/*.
# That duty lives here now so runner-hooks-restore.sh can drop the override.
#
# Clears the CONTENTS of <runner>/_work/_temp, never the dir itself, and keeps
# _runner_file_commands: the runner still processes this hook step's own file
# commands after the hook exits.  Best-effort, --one-file-system, bounded.
_runner_temp="${RUNNER_TEMP:-}"
if [ -z "$_runner_temp" ] && [ -n "${GITHUB_WORKSPACE:-}" ]; then
    case "$GITHUB_WORKSPACE" in
        */_work/*) _runner_temp="${GITHUB_WORKSPACE%%/_work/*}/_work/_temp" ;;
    esac
fi
case "$_runner_temp" in
    */_work/_temp)
        if [ -d "$_runner_temp" ] && [ ! -L "$_runner_temp" ]; then
            timeout -k 10s "${RUNNER_HOOK_TEMP_CLEANUP_TIMEOUT_SECONDS:-60}s" find "$_runner_temp" \
                -mindepth 1 -maxdepth 1 ! -name _runner_file_commands \
                -exec rm -rf --one-file-system -- {} + 2>/dev/null || true
        fi
        ;;
esac

exit 0

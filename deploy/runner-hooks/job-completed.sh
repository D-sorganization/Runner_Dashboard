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

exit 0

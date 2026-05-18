#!/usr/bin/env bash
# Runner job-started hook (ACTIONS_RUNNER_HOOK_JOB_STARTED).
#
# Writes a sentinel lockfile that both the autoscaler
# (backend/runner_autoscaler.py) and the nightly cleanup
# (deploy/runner-cleanup.sh) consult before stopping a runner.
#
# Background — issue #651:
# `_runner_is_busy()` looks for a `Runner.Worker` child of the unit's
# MainPID. There is a brief window (~1-2s) between job pickup by the
# Listener and the Worker fork during which a job IS assigned to the
# runner but no Worker process exists yet. If the autoscaler runs in
# that window, it kills the Listener and leaves residue in
# `_work/_temp/_runner_file_commands/` and `_diag/pages/*.log`.
#
# This hook closes the race on the *post*-fork side: as soon as the
# Worker is alive enough to execute hooks, it touches the lockfile.
# The autoscaler/cleanup busy-check returns True whenever the file
# exists, so even a transient psutil hiccup that misses the Worker
# child still results in a safe "busy" verdict.
#
# Defense-in-depth: this does not replace the cgroup-based busy check
# (Tasks count in the unit's cgroup); it adds a second independent
# signal.

set -u

LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_NAME="${RUNNER_NAME:-${HOSTNAME}-unknown}"

# Best-effort directory creation. Hook runs as the runner user; the
# directory should be group-writable for that user (installer ensures it).
mkdir -p "$LOCK_DIR" 2>/dev/null || true

LOCK_FILE="${LOCK_DIR}/${RUNNER_NAME}.lock"

# Atomic write with metadata so observers can see who/what claimed it.
{
    printf 'pid=%s\n' "$$"
    printf 'runner=%s\n' "$RUNNER_NAME"
    printf 'job=%s\n' "${GITHUB_JOB:-unknown}"
    printf 'workflow=%s\n' "${GITHUB_WORKFLOW:-unknown}"
    printf 'run_id=%s\n' "${GITHUB_RUN_ID:-unknown}"
    printf 'repository=%s\n' "${GITHUB_REPOSITORY:-unknown}"
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
} > "$LOCK_FILE.tmp" 2>/dev/null && mv "$LOCK_FILE.tmp" "$LOCK_FILE" 2>/dev/null

# Hooks must exit 0 to avoid failing the job.
exit 0

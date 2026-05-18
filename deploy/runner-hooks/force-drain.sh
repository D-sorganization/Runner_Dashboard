#!/usr/bin/env bash
# force-drain.sh — ExecStop= helper for actions.runner.*.service units.
#
# Why this exists:
#
# `KillMode=mixed` should cascade SIGTERM and (after TimeoutStopSec) SIGKILL
# to every process in the unit's cgroup. On WSL2 the cgroup-wide kill can
# fail with `Failed to kill control group ...: Invalid argument` (kernel
# EINVAL — observed on Ubuntu 22.04 under WSL2 6.x). When that happens the
# Runner.Worker child survives and shows up as a "left-over process in
# control group" on the next start, exactly reproducing the orphan-Worker
# corruption the rest of #664 sets out to prevent.
#
# This script is invoked AFTER systemd's stop attempt, regardless of
# outcome (ExecStop= runs in addition to the unit's own shutdown). It
# pkill's any `Runner.Worker spawnclient` whose cmdline references this
# unit's WorkingDirectory, then waits up to FORCE_DRAIN_TIMEOUT seconds
# for them to exit. Exits 0 either way — never let an ExecStop failure
# block systemd from marking the unit inactive.
#
# Inputs (provided by systemd):
#   $RUNNER_NAME             — set by the drop-in from migrate-runner-units.sh
#   $SYSTEMD_UNIT (implicit) — the unit being stopped
#
# Environment:
#   FORCE_DRAIN_TIMEOUT      — seconds to wait for Worker exit, default 10
#   RUNNER_BUSY_LOCK_DIR     — lockfile dir for completion cleanup (default /var/run/runner-busy)

set -uo pipefail

FORCE_DRAIN_TIMEOUT="${FORCE_DRAIN_TIMEOUT:-10}"
LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_NAME="${RUNNER_NAME:-unknown}"

log() {
    # ExecStop= helpers go to journald; avoid noisy stdout in the happy path.
    logger --tag "runner-force-drain[${RUNNER_NAME}]" --priority user.info "$*" 2>/dev/null || true
}

# Resolve this runner's working directory from systemd so we kill ONLY
# the workers belonging to this unit, not someone else's.
WORK_DIR=""
if [[ -n "${SYSTEMD_UNIT:-}" ]]; then
    WORK_DIR="$(systemctl show "$SYSTEMD_UNIT" --property=WorkingDirectory --value 2>/dev/null || true)"
fi
# Fallback: derive from RUNNER_NAME by scanning the standard layout.
if [[ -z "$WORK_DIR" ]]; then
    for candidate in /home/*/actions-runners/"$RUNNER_NAME" /opt/actions-runners/"$RUNNER_NAME"; do
        if [[ -d "$candidate" ]]; then WORK_DIR="$candidate"; break; fi
    done
fi

if [[ -n "$WORK_DIR" ]]; then
    # Match the worker's cmdline by working-dir substring. Robust against
    # the "bin.<version>" suffix on Runner.Worker's actual location.
    pids=$(pgrep -f "${WORK_DIR}/.*Runner\.Worker spawnclient" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "found leftover Workers under $WORK_DIR: $pids — sending SIGTERM"
        # shellcheck disable=SC2086
        kill -TERM $pids 2>/dev/null || true
        # Wait for graceful exit up to FORCE_DRAIN_TIMEOUT seconds.
        for _ in $(seq 1 "$FORCE_DRAIN_TIMEOUT"); do
            pids=$(pgrep -f "${WORK_DIR}/.*Runner\.Worker spawnclient" 2>/dev/null || true)
            [[ -z "$pids" ]] && break
            sleep 1
        done
        if [[ -n "$pids" ]]; then
            log "Workers still alive after ${FORCE_DRAIN_TIMEOUT}s — escalating to SIGKILL: $pids"
            # shellcheck disable=SC2086
            kill -KILL $pids 2>/dev/null || true
        fi
    fi
fi

# Clear stale lockfile so the cleanup script doesn't have to GC it later
# and so the next start sees a clean state. The job-completed.sh hook
# usually does this — but if the Worker was killed mid-job, the hook
# never fired.
if [[ "$RUNNER_NAME" != "unknown" ]]; then
    rm -f "${LOCK_DIR}/${RUNNER_NAME}.lock" 2>/dev/null || true
fi

# Never block the stop sequence on our cleanup.
exit 0

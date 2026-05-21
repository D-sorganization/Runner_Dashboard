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
# Inputs:
#   $1                       — full unit name, passed by systemd via the %n
#                              specifier in the ExecStop= line. This is the
#                              authoritative source — do NOT rely on
#                              $SYSTEMD_UNIT being exported by systemd
#                              (it isn't, at least not portably).
#   $RUNNER_NAME             — the short runner name (e.g.
#                              "d-sorg-local-Desktop-1"), set by the drop-in
#                              from migrate-runner-units.sh. Used only for
#                              logging tag and lockfile cleanup, NOT for
#                              path resolution.
#
# Environment:
#   FORCE_DRAIN_TIMEOUT      — seconds to wait for Worker exit, default 10
#   RUNNER_BUSY_LOCK_DIR     — lockfile dir for completion cleanup
#                              (default /var/run/runner-busy)

set -uo pipefail

FORCE_DRAIN_TIMEOUT="${FORCE_DRAIN_TIMEOUT:-10}"
LOCK_DIR="${RUNNER_BUSY_LOCK_DIR:-/var/run/runner-busy}"
RUNNER_NAME="${RUNNER_NAME:-unknown}"

# Accept the unit name as $1 (systemd %n). Fall through to env vars if
# someone invoked the script directly without args.
UNIT="${1:-${SYSTEMD_UNIT:-}}"

log() {
    # ExecStop= helpers go to journald; avoid noisy stdout in the happy path.
    logger --tag "runner-force-drain[${RUNNER_NAME}]" --priority user.info "$*" 2>/dev/null || true
}

# Resolve this runner's working directory from systemd. Three resolution
# strategies, in order of authority:
#   (1) $1 (unit name from systemd %n) → systemctl show WorkingDirectory
#   (2) $SYSTEMD_UNIT (rare; not portably set by systemd, but cheap to try)
#   (3) Filesystem scan rooted at the deployed dashboard config — only as
#       a last-resort fallback. The runner-dir name often does NOT match
#       RUNNER_NAME (e.g. unit is "d-sorg-local-Desktop-1" while the dir
#       is "runner-1"), so we look for any runner dir whose own .runner
#       config file names this runner.
WORK_DIR=""
if [[ -n "$UNIT" ]]; then
    WORK_DIR="$(systemctl show "$UNIT" --property=WorkingDirectory --value 2>/dev/null || true)"
fi
if [[ -z "$WORK_DIR" && "$RUNNER_NAME" != "unknown" ]]; then
    # Walk standard host layouts looking for a runner whose `.runner`
    # config (JSON written by the runner package at registration time)
    # contains "agentName": "<RUNNER_NAME>". This is the canonical
    # mapping from system runner name → runner directory.
    for root in /home/*/actions-runners /opt/actions-runners; do
        [[ -d "$root" ]] || continue
        for d in "$root"/runner-* "$root"/"$RUNNER_NAME"; do
            [[ -d "$d" ]] || continue
            if [[ -f "$d/.runner" ]] && grep -q "\"agentName\":[[:space:]]*\"${RUNNER_NAME}\"" "$d/.runner" 2>/dev/null; then
                WORK_DIR="$d"
                break 2
            fi
        done
    done
fi

if [[ -z "$WORK_DIR" ]]; then
    log "could not resolve WorkingDirectory (unit='${UNIT}' runner='${RUNNER_NAME}'); skipping drain"
else
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

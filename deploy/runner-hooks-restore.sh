#!/usr/bin/env bash
# Self-heal for the runner job-pickup hooks (issue #664, follow-up to
# the 2026-05-27 fleet outage where multiple runners had
# ACTIONS_RUNNER_HOOK_JOB_COMPLETED pointing at files that had been
# deleted from /usr/local/bin/runner-hooks/, producing
# `##[error]File doesn't exist` on every job and stalling all CI.
#
# Re-installs the three hook scripts from the deploy/runner-hooks
# source dir into /usr/local/bin/runner-hooks/ if any of them are
# missing or non-executable. Idempotent — a no-op when everything is
# already in place — so it is safe to run on a short timer.
#
# Inputs (env, all optional):
#   HOOK_SRC_DIR   — source dir containing the *.sh hooks
#                    (default: /opt/runner-dashboard/deploy/runner-hooks)
#   HOOK_DIR       — install dir consulted by the runner
#                    (default: /usr/local/bin/runner-hooks)
#   PROM_FILE      — Prometheus textfile output path
#                    (default: $TEXTFILE_COLLECTOR_DIR/runner_hooks.prom)

set -Eeuo pipefail

HOOK_SRC_DIR="${HOOK_SRC_DIR:-/opt/runner-dashboard/deploy/runner-hooks}"
HOOK_DIR="${HOOK_DIR:-/usr/local/bin/runner-hooks}"
TEXTFILE_COLLECTOR_DIR="${TEXTFILE_COLLECTOR_DIR:-/var/lib/node_exporter/textfile_collector}"
PROM_FILE="${PROM_FILE:-${TEXTFILE_COLLECTOR_DIR}/runner_hooks.prom}"

HOOKS=(job-started.sh job-completed.sh force-drain.sh)

restored=0
missing_src=0

if [[ ! -d "${HOOK_SRC_DIR}" ]]; then
    echo "runner-hooks-restore: source dir ${HOOK_SRC_DIR} not present; nothing to do" >&2
    missing_src=1
else
    install -d -m 0755 "${HOOK_DIR}"
    for hook in "${HOOKS[@]}"; do
        src="${HOOK_SRC_DIR}/${hook}"
        dst="${HOOK_DIR}/${hook}"
        if [[ ! -f "${src}" ]]; then
            echo "runner-hooks-restore: source missing for ${hook} at ${src}" >&2
            continue
        fi
        if [[ ! -x "${dst}" ]] || ! cmp -s "${src}" "${dst}"; then
            install -m 0755 "${src}" "${dst}"
            restored=$((restored + 1))
            echo "runner-hooks-restore: restored ${dst} from ${src}"
        fi
    done
fi

if [[ -d "${TEXTFILE_COLLECTOR_DIR}" ]]; then
    tmp="$(mktemp "${PROM_FILE}.XXXXXX")"
    {
        echo "# HELP runner_hooks_restore_total Hooks re-installed on the most recent self-heal pass."
        echo "# TYPE runner_hooks_restore_total counter"
        echo "runner_hooks_restore_total ${restored}"
        echo "# HELP runner_hooks_source_missing 1 if the deploy hook source dir is absent on this host."
        echo "# TYPE runner_hooks_source_missing gauge"
        echo "runner_hooks_source_missing ${missing_src}"
        echo "# HELP runner_hooks_last_check_timestamp_seconds Unix time of the last self-heal pass."
        echo "# TYPE runner_hooks_last_check_timestamp_seconds gauge"
        echo "runner_hooks_last_check_timestamp_seconds $(date +%s)"
    } >"${tmp}"
    mv -f "${tmp}" "${PROM_FILE}"
fi

exit 0

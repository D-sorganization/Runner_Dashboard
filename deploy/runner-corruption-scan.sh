#!/usr/bin/env bash
# runner-corruption-scan.sh
#
# Scan every actions-runner directory under $RUNNER_ROOT for residue from
# mid-job kills (see issue #651) and emit a Prometheus textfile-collector
# snapshot to $PROM_FILE.
#
# Two corruption signatures are counted per runner:
#
#   * file_commands — files left under
#     <runner>/_work/_temp/_runner_file_commands/.  These are cleaned at the
#     end of every job; anything present while the runner is idle is
#     residue that will cause the next allocation to fail with
#     "Missing file at path: .../_runner_file_commands/save_state_<uuid>".
#
#   * diag_pages   — *.log files under <runner>/_diag/pages/ older than
#     $DIAG_PAGES_MIN_AGE_DAYS.  Two runs colliding on a UUID will abort
#     the runner with "the file '.../<uuid>_<uuid>_1.log' already exists".
#
# Output format (gauge):
#
#   # HELP runner_corruption_residue_count Stale corruption-residue file count by kind.
#   # TYPE runner_corruption_residue_count gauge
#   runner_corruption_residue_count{host="HOST",runner="runner-01",kind="file_commands"} 3
#   runner_corruption_residue_count{host="HOST",runner="runner-01",kind="diag_pages"} 1
#
# Designed to be invoked from a 5-minute systemd timer
# (runner-corruption-scan.timer) and scraped by node_exporter's
# textfile collector. See docs/observability.md.

set -Eeuo pipefail

RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
PROM_FILE="${PROM_FILE:-/var/lib/node_exporter/textfile_collector/runner_corruption.prom}"
DIAG_PAGES_MIN_AGE_DAYS="${DIAG_PAGES_MIN_AGE_DAYS:-1}"
FLEET_NODE_NAME="${FLEET_NODE_NAME:-$(hostname -s 2>/dev/null || hostname)}"

tmp_file="${PROM_FILE}.tmp"
trap 'rm -f -- "$tmp_file"' EXIT

mkdir -p -- "$(dirname -- "$PROM_FILE")"

{
    printf '# HELP runner_corruption_residue_count Stale corruption-residue file count by kind.\n'
    printf '# TYPE runner_corruption_residue_count gauge\n'
} >"$tmp_file"

count_file_commands() {
    local runner_dir="$1"
    local rfc="${runner_dir}/_work/_temp/_runner_file_commands"
    if [[ -d "$rfc" ]]; then
        # Count any regular file under the residue dir (recursive, in case
        # the runner ever nests). We do not filter by age — by contract the
        # directory should be empty between jobs.
        find "$rfc" -type f 2>/dev/null | wc -l | tr -d ' '
    else
        printf '0'
    fi
}

count_diag_pages() {
    local runner_dir="$1"
    local diag="${runner_dir}/_diag/pages"
    if [[ -d "$diag" ]]; then
        find "$diag" -maxdepth 1 -type f -name '*.log' \
            -mtime +"$((DIAG_PAGES_MIN_AGE_DAYS - 1))" 2>/dev/null \
            | wc -l | tr -d ' '
    else
        printf '0'
    fi
}

if [[ -d "$RUNNER_ROOT" ]]; then
    # shellcheck disable=SC2044  # paths under RUNNER_ROOT are operator-controlled
    while IFS= read -r runner_dir; do
        [[ -n "$runner_dir" ]] || continue
        runner_name="$(basename -- "$runner_dir")"
        fc_count="$(count_file_commands "$runner_dir")"
        dp_count="$(count_diag_pages "$runner_dir")"
        printf 'runner_corruption_residue_count{host="%s",runner="%s",kind="file_commands"} %s\n' \
            "$FLEET_NODE_NAME" "$runner_name" "$fc_count" >>"$tmp_file"
        printf 'runner_corruption_residue_count{host="%s",runner="%s",kind="diag_pages"} %s\n' \
            "$FLEET_NODE_NAME" "$runner_name" "$dp_count" >>"$tmp_file"
    done < <(find "$RUNNER_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'runner-*' 2>/dev/null | sort)
fi

# Atomic publish so node_exporter never sees a half-written file.
mv -f -- "$tmp_file" "$PROM_FILE"
trap - EXIT

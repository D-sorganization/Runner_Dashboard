#!/usr/bin/env bash
# Report-only pre-backup cleanup inventory for Brick (#4010).
# Never deletes on live hosts; use runner-cleanup / fleet_storage_gc after review.
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: brick-cleanup-audit.sh [--json]

Prints reclaimable-space hints (du/find only). Does not modify the filesystem.
Deletion flags are intentionally unsupported on fleet hosts.
EOF
}

refuse_delete() {
    echo "refusing destructive operation: live-host deletions are out of scope (#4010)" >&2
    echo "Review this report, then run deploy/runner-cleanup.sh or fleet_storage_gc when ready." >&2
    exit 2
}

for arg in "$@"; do
    case "$arg" in
        --help | -h)
            usage
            exit 0
            ;;
        --delete | --apply | --force-delete)
            refuse_delete
            ;;
        --json)
            JSON=1
            ;;
        *)
            echo "unknown argument: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "${BRICK_CLEANUP_ALLOW_DELETE:-0}" == "1" ]]; then
    refuse_delete
fi

JSON="${JSON:-0}"
RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
HOME_DIR="${BRICK_AUDIT_HOME:-$HOME}"

report_line() {
    local category="$1"
    local path="$2"
    if [[ ! -e "$path" ]]; then
        return 0
    fi
    local bytes
    bytes="$(du -sb "$path" 2>/dev/null | awk '{print $1}')"
    if [[ "$JSON" == "1" ]]; then
        printf '{"category":"%s","path":"%s","bytes":%s}\n' "$category" "$path" "${bytes:-0}"
    else
        printf '%-24s %12s  %s\n' "$category" "${bytes:-0}" "$path"
    fi
}

if [[ "$JSON" != "1" ]]; then
    echo "Brick cleanup audit (report-only; no deletions)"
    printf '%-24s %12s  %s\n' "category" "bytes" "path"
fi

report_line "runner_work" "$RUNNER_ROOT"
while IFS= read -r root; do
    [[ -z "$root" ]] && continue
    report_line "runner_work" "$root"
done < <(find "$HOME_DIR" -maxdepth 3 -type d -name 'actions-runners*' 2>/dev/null | head -20)

report_line "cargo_target" "$HOME_DIR/.cargo/target"
report_line "rustup_tmp" "$HOME_DIR/.rustup/tmp"
report_line "npm_cache" "$HOME_DIR/.npm"
report_line "docker_root" "/var/lib/docker"

if [[ "$JSON" != "1" ]]; then
    echo
    echo "Dry-run complete. No files were removed."
fi

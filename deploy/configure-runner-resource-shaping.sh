#!/usr/bin/env bash
# configure-runner-resource-shaping.sh — opt-in systemd weights for runners (#4312).
#
# Default OFF: reads config/resource-shaping-trial.json (override via
# RESOURCE_SHAPING_TRIAL_CONFIG). When enabled=false, removes trial drop-ins and exits.
# Does not restart runners; operator restarts idle units after apply.
#
#   sudo deploy/configure-runner-resource-shaping.sh [--dry-run]
#
# Test overrides: SYSTEMD_UNIT_DROPIN_ROOT, SYSTEMD_SLICE_ROOT (no writes outside tmp).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${RESOURCE_SHAPING_TRIAL_CONFIG:-${PROJECT_ROOT}/config/resource-shaping-trial.json}"
SLICE_DEST="${SYSTEMD_SLICE_ROOT:-/etc/systemd/system}"
DROPIN_NAME="15-runner-resource-shaping.conf"
MEMORY_HIGH_DROPIN="15-runner-resource-shaping-memory-high.conf"
DRY_RUN=0

log() { printf '%s configure-runner-resource-shaping: %s\n' "$(date '+%F %T')" "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

read_config_bool() {
    python3 - "$CONFIG_PATH" "$1" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
cur = data
for part in key.split("."):
    if not isinstance(cur, dict) or part not in cur:
        print("false")
        raise SystemExit(0)
    cur = cur[part]
print("true" if cur else "false")
PY
}

read_config_str() {
    python3 - "$CONFIG_PATH" "$1" <<'PY'
import json, sys
path, key = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
cur = data
for part in key.split("."):
    cur = cur[part]
print(cur if isinstance(cur, str) else "")
PY
}

unit_dropin_root() {
    local unit="$1"
    if [[ -n "${SYSTEMD_UNIT_DROPIN_ROOT:-}" ]]; then
        printf '%s/%s.d' "${SYSTEMD_UNIT_DROPIN_ROOT}" "${unit%.service}"
        return
    fi
    printf '/etc/systemd/system/%s.d' "$unit"
}

discover_runner_units() {
    command -v systemctl >/dev/null 2>&1 || return 0
    systemctl list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '$1 ~ /^actions\.runner\..*\.service$/ {print $1}' \
        | sort
}

install_file() {
    local src="$1" dest="$2"
    if [[ "$DRY_RUN" == "1" ]]; then
        log "[dry-run] would install ${src} -> ${dest}"
        return 0
    fi
    install -d -m 0755 "$(dirname "$dest")"
    install -m 0644 "$src" "$dest"
    log "installed ${dest}"
}

remove_trial_dropins() {
    local unit dropin_dir f
    while read -r unit; do
        [[ -n "$unit" ]] || continue
        dropin_dir="$(unit_dropin_root "$unit")"
        for f in "${DROPIN_NAME}" "${MEMORY_HIGH_DROPIN}"; do
            if [[ -f "${dropin_dir}/${f}" ]]; then
                if [[ "$DRY_RUN" == "1" ]]; then
                    log "[dry-run] would remove ${dropin_dir}/${f}"
                else
                    rm -f "${dropin_dir}/${f}"
                    log "removed ${dropin_dir}/${f}"
                fi
            fi
        done
    done < <(discover_runner_units)
}

apply_enabled() (
    local slice_name template_src memory_high rendered
    rendered="$(mktemp -d)"
    trap 'rm -rf "$rendered"' EXIT
    (cd "$PROJECT_ROOT" && python3 -m deploy.resource_shaping_trial.render "$CONFIG_PATH" "$rendered")
    slice_name="$(read_config_str systemd.slice_name)"
    slice_name="${slice_name:-runners.slice}"
    template_src="${rendered}/${DROPIN_NAME}"
    memory_high="$(read_config_bool systemd.memory_high_enabled)"

    install_file "${PROJECT_ROOT}/deploy/systemd-dropins/runners.slice" "${SLICE_DEST}/${slice_name}"

    local unit dropin_dir
    while read -r unit; do
        [[ -n "$unit" ]] || continue
        dropin_dir="$(unit_dropin_root "$unit")"
        install_file "$template_src" "${dropin_dir}/${DROPIN_NAME}"
        if [[ "$memory_high" == "true" ]]; then
            install_file \
                "${rendered}/${MEMORY_HIGH_DROPIN}" \
                "${dropin_dir}/${MEMORY_HIGH_DROPIN}"
        elif [[ -f "${dropin_dir}/${MEMORY_HIGH_DROPIN}" ]]; then
            if [[ "$DRY_RUN" == "1" ]]; then
                log "[dry-run] would remove ${dropin_dir}/${MEMORY_HIGH_DROPIN}"
            else
                rm -f "${dropin_dir}/${MEMORY_HIGH_DROPIN}"
            fi
        fi
    done < <(discover_runner_units)

    if [[ "$DRY_RUN" != "1" ]] && command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
    fi
    log "trial enabled (slice=${slice_name}); restart idle runner units to apply weights"
)

main() {
    if [[ ! -f "$CONFIG_PATH" ]]; then
        log "missing config: ${CONFIG_PATH}"
        exit 2
    fi
    local enabled
    enabled="$(read_config_bool enabled)"
    if [[ "$enabled" != "true" ]]; then
        log "resource shaping trial disabled in ${CONFIG_PATH}; removing trial drop-ins if present"
        remove_trial_dropins
        return 0
    fi
    if [[ -z "${SYSTEMD_UNIT_DROPIN_ROOT:-}" && "${EUID:-0}" -ne 0 ]]; then
        log "must run as root to install systemd drop-ins (or set SYSTEMD_UNIT_DROPIN_ROOT for dry tests)"
        exit 2
    fi
    apply_enabled
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
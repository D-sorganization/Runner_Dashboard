#!/usr/bin/env bash
# clean-stale-shell-profiles.sh — Prune stale cargo/env source lines from shell profiles (Runner_Dashboard#1159).
set -Eeuo pipefail

DRY_RUN=0
TARGET_FILES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --target-file) TARGET_FILES+=("${2:?--target-file requires a path}"); shift 2 ;;
        -h|--help)
            echo "Usage: clean-stale-shell-profiles.sh [--dry-run] [--target-file PATH]"
            exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [[ ${#TARGET_FILES[@]} -eq 0 ]]; then
    for f in "$HOME/.profile" "$HOME/.bashrc"; do
        [[ -f "$f" ]] && TARGET_FILES+=("$f")
    done
fi

log() { printf '%s clean-stale-shell-profiles: %s\n' "$(date '+%F %T')" "$*"; }

clean_profile_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0

    local modified=0
    local tmp_out
    tmp_out="$(mktemp "${file}.tmp.XXXXXX")"

    while IFS= read -r line || [[ -n "$line" ]]; do
        if echo "$line" | grep -qE '^[[:space:]]*(\.|source)[[:space:]]+.*(cargo/env|/\.ci/.*/env)'; then
            local target_env
            target_env=$(echo "$line" | sed -E "s/^[[:space:]]*(\.|source)[[:space:]]+['\"]?//; s/['\"].*//")
            if [[ ! -f "$target_env" ]]; then
                log "Found stale source line pointing to missing file: ${target_env} in ${file}"
                modified=1
                continue
            fi
        fi
        printf '%s\n' "$line" >> "$tmp_out"
    done < "$file"

    if [[ "$modified" -eq 1 ]]; then
        if [[ "$DRY_RUN" == "1" ]]; then
            log "Dry-run: would remove stale source line(s) from ${file}"
            rm -f "$tmp_out"
        else
            cp "$file" "${file}.bak.$(date +%Y%m%d%H%M%S)"
            mv -f "$tmp_out" "$file"
            log "Successfully cleaned stale source line(s) from ${file}"
        fi
    else
        rm -f "$tmp_out"
    fi
}

for target in "${TARGET_FILES[@]}"; do
    clean_profile_file "$target"
done


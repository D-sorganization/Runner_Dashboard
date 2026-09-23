#!/usr/bin/env bash
# clean-gitconfig-token-rewrites.sh — Prune credential-bearing url.*.insteadOf sections from the
# runner user's global git config (Runner_Dashboard#1216).
#
# CI jobs that ran `git config --global url."https://x-access-token:<token>@github.com/".insteadOf
# https://github.com/` leave one section per run (each token is a new key), so a busy runner host
# accumulates hundreds of expired tokens that rewrite every github.com URL and break git outside jobs.
#
# Removes every `[url "<scheme>://<userinfo>@<host>/..."]` section (insteadOf / pushInsteadOf alike)
# whose URL embeds credentials. Plain rewrites such as `url.https://github.com/.insteadOf git@github.com:`
# are kept. A timestamped 0600 backup is written before the first change; re-runs are no-ops.
# Token values are never printed: every logged URL has its userinfo replaced by `***`.
set -Eeuo pipefail

DRY_RUN=0
TARGET_FILES=()

usage() {
    echo "Usage: clean-gitconfig-token-rewrites.sh [--dry-run] [--target-file PATH]..."
    echo "Default targets: \${GIT_CONFIG_GLOBAL:-\$HOME/.gitconfig} and \${XDG_CONFIG_HOME:-\$HOME/.config}/git/config"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --target-file) TARGET_FILES+=("${2:?--target-file requires a path}"); shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ${#TARGET_FILES[@]} -eq 0 ]]; then
    for f in "${GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}" "${XDG_CONFIG_HOME:-$HOME/.config}/git/config"; do
        [[ -f "$f" ]] && TARGET_FILES+=("$f")
    done
fi

log() { printf '%s clean-gitconfig-token-rewrites: %s\n' "$(date '+%F %T')" "$*"; }

# git config --file must not depend on the caller's cwd: a cwd inside a broken or foreign
# repository makes git abort before it reads the file. Run from / with an absolute path.
gitcfg() { (cd / && GIT_CEILING_DIRECTORIES=/ git config "$@"); }

# Replace the userinfo of a scheme://userinfo@host URL with *** so no token reaches a log.
redact_url() {
    printf '%s' "$1" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/@]*@#\1***@#'
}

# Print the unique url.<subsection> names whose subsection embeds credentials.
credential_url_sections() {
    local file="$1" key sub
    gitcfg --file "$file" --name-only --get-regexp '^url\..*\.(insteadof|pushinsteadof)$' 2>/dev/null |
        while IFS= read -r key; do
            sub="${key#url.}"
            sub="${sub%.*}"
            if [[ "$sub" =~ ^[Hh][Tt][Tt][Pp][Ss]?://[^/@]+@ ]]; then
                printf '%s\n' "$sub"
            fi
        done | sort -u
}

clean_gitconfig_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    [[ "$file" == /* ]] || file="${PWD}/${file}"
    if ! gitcfg --file "$file" --list >/dev/null 2>&1; then
        log "Skipping ${file}: git cannot parse it"
        return 1
    fi

    local sections=()
    mapfile -t sections < <(credential_url_sections "$file")
    if [[ ${#sections[@]} -eq 0 ]]; then
        log "No credential-bearing url.*.insteadOf sections in ${file}"
        return 0
    fi

    local sample
    sample="$(redact_url "${sections[0]}")"
    if [[ "$DRY_RUN" == "1" ]]; then
        log "Dry-run: would remove ${#sections[@]} credential-bearing url section(s) from ${file} (e.g. url.${sample})"
        return 0
    fi

    local tmp_out sub
    tmp_out="$(mktemp "${file}.tmp.XXXXXX")"
    chmod 600 "$tmp_out"
    cat "$file" > "$tmp_out"
    for sub in "${sections[@]}"; do
        gitcfg --file "$tmp_out" --remove-section "url.${sub}" 2>/dev/null || true
    done
    if [[ -n "$(credential_url_sections "$tmp_out")" ]]; then
        rm -f "$tmp_out"
        log "Aborting ${file}: credential-bearing sections survived removal; file left unchanged"
        return 1
    fi

    local backup
    backup="${file}.bak.$(date +%Y%m%d%H%M%S)"
    (umask 077 && cp -p "$file" "$backup")
    chmod 600 "$backup"
    chmod --reference="$file" "$tmp_out" 2>/dev/null || true
    mv -f "$tmp_out" "$file"
    log "Removed ${#sections[@]} credential-bearing url section(s) from ${file} (e.g. url.${sample})"
    log "Backup (mode 0600, still holds the removed tokens; delete once verified): ${backup}"
}

rc=0
for target in "${TARGET_FILES[@]}"; do
    clean_gitconfig_file "$target" || rc=1
done
exit "$rc"

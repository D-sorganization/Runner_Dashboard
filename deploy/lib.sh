#!/usr/bin/env bash
# deploy/lib.sh -- Shared shell library for runner-dashboard deploy scripts.
# Source with: source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# Do NOT execute directly.

set -euo pipefail

# Terminal colours
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

# Guard helpers
require_dir()  { [[ -d "$1" ]] || fail "Required directory not found: $1"; }
require_file() { [[ -f "$1" ]] || fail "Required file not found: $1"; }
require_cmd()  { command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"; }

# pip install with --break-system-packages when supported
pip_install() {
    local -a pkgs=("$@")
    local python_bin="${RUNNER_DASHBOARD_PYTHON:-}"
    if [[ -z "$python_bin" ]]; then
        python_bin="$(command -v python3.11 || command -v python3)"
    fi

    local -a cmd=("$python_bin" -m pip install --quiet "${pkgs[@]}")
    if "$python_bin" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        cmd=("$python_bin" -m pip install --break-system-packages --quiet "${pkgs[@]}")
    fi
    "${cmd[@]}"
}

# Sync directory (rsync preferred, rm/cp fallback)
sync_dir() {
    local src="$1" dest="$2"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete "$src/" "$dest/"
        return
    fi
    warn "rsync not found; using rm/cp fallback for ${dest}"
    rm -rf "$dest"; mkdir -p "$dest"; cp -a "$src/." "$dest/"
}

# Backup helper: creates a timestamped copy, prints backup path to stdout.
backup_dir() {
    local src="$1"
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    local backup="${src}.bak.${ts}"
    if [[ -d "$src" ]]; then
        cp -a "$src" "$backup"
        echo "$backup"
    fi
}

# GitHub token validation. Returns 0 if the token matches any known GitHub
# token format; exits non-zero with an error message otherwise.
# Accepted prefixes: ghp_ (classic PAT), github_pat_ (fine-grained PAT),
#                    ghs_ (GitHub Apps installation), gho_ (OAuth)
validate_gh_token() {
    local token="$1"
    if [[ ! "$token" =~ ^(ghp_|github_pat_|ghs_|gho_)[A-Za-z0-9_]{20,}$ ]]; then
        echo "ERROR: GH_TOKEN does not match expected GitHub token format" >&2
        return 1
    fi
}

# Dry-run support. Set DRY_RUN=true to enable.
# Usage: dry_run "description" || { actual_command; }
DRY_RUN="${DRY_RUN:-false}"
dry_run() {
    [[ "$DRY_RUN" == "true" ]] || return 1
    warn "[DRY-RUN] Skipping: $*"
    return 0
}

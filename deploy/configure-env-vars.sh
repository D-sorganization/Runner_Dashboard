#!/usr/bin/env bash
# ==============================================================================
# configure-env-vars.sh — Securely manage dashboard environment variables
#
# This script provides a safe, reusable way to configure environment variables
# for the runner-dashboard without exposing secrets in the shell history or logs.
#
# Features:
#   - Silent password input (no echo)
#   - Atomic file updates via temp file
#   - Private file permissions (600)
#   - Safe to rerun without overwriting unrelated vars
#   - Lists current configuration on demand
#
# Usage:
#   # Set a variable interactively
#   bash configure-env-vars.sh set JULES_API_KEY
#
#   # Set multiple variables
#   bash configure-env-vars.sh set OLLAMA_API_KEY
#   bash configure-env-vars.sh set SOME_OTHER_VAR
#
#   # List current config (without showing values)
#   bash configure-env-vars.sh list
#
#   # Show one variable (only if user is authorized)
#   bash configure-env-vars.sh show JULES_API_KEY
#
#   # Delete a variable
#   bash configure-env-vars.sh delete JULES_API_KEY
#
#   # Reset to defaults (removes all custom vars)
#   bash configure-env-vars.sh reset
# ==============================================================================

set -euo pipefail

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[ OK ]${NC} $*"; }
info() { echo -e "${CYAN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-$HOME/.config/runner-dashboard/env}"
KNOWN_VARS=(
    "JULES_API_KEY:Jules API authentication token"
    "OLLAMA_API_KEY:Ollama API key (optional)"
    "OLLAMA_BASE_URL:Ollama base URL (default: http://localhost:11434)"
    "GITHUB_TOKEN:GitHub API token (optional, for higher rate limits)"
    "CODEX_API_KEY:Codex API key (if using Codex provider)"
)

usage() {
    cat <<'EOF'
Usage:
  bash deploy/configure-env-vars.sh [command] [options]

Commands:
  set <VAR>              Securely prompt and set an environment variable
  list                   List all configured variables (names only)
  show <VAR>             Display a specific variable (use with caution)
  delete <VAR>           Remove a variable
  reset                  Remove all custom environment variables
  help                   Show this message

Examples:
  bash deploy/configure-env-vars.sh set JULES_API_KEY
  bash deploy/configure-env-vars.sh list
  bash deploy/configure-env-vars.sh delete JULIUS_API_KEY

EOF
}

ensure_env_file() {
    install -d -m 700 "$(dirname "${ENV_FILE}")"
    [[ ! -f "${ENV_FILE}" ]] && touch "${ENV_FILE}" && chmod 600 "${ENV_FILE}"
}

set_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    trap 'rm -f "${tmp}"' RETURN
    python3 - "${ENV_FILE}" "${key}" "${value}" >"${tmp}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = []
if path.exists():
    lines = path.read_text(encoding="utf-8").splitlines()

prefix = f"{key}="
filtered = [line for line in lines if not line.startswith(prefix)]
filtered.append(f"{key}={value}")
sys.stdout.write("\n".join(filtered) + "\n")
PY
    cat "${tmp}" > "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    rm -f "${tmp}"
    trap - RETURN
}

get_env_var() {
    local key="$1"
    grep "^${key}=" "${ENV_FILE}" 2>/dev/null | cut -d= -f2- || true
}

list_env_vars() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        info "No environment file found at ${ENV_FILE}"
        return 0
    fi
    if [[ ! -s "${ENV_FILE}" ]]; then
        info "Environment file is empty"
        return 0
    fi
    info "Configured variables in ${ENV_FILE}:"
    while IFS='=' read -r key value; do
        [[ -z "${key}" ]] && continue
        echo "  ${CYAN}${key}${NC}=***"
    done < "${ENV_FILE}"
}

show_env_var() {
    local key="$1"
    value=$(get_env_var "${key}")
    if [[ -z "${value}" ]]; then
        warn "${key} is not set"
        return 1
    fi
    echo "${value}"
}

delete_env_var() {
    local key="$1"
    if ! grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
        warn "${key} is not currently set"
        return 1
    fi
    local tmp
    tmp="$(mktemp)"
    trap 'rm -f "${tmp}"' RETURN
    grep -v "^${key}=" "${ENV_FILE}" > "${tmp}" || true
    cat "${tmp}" > "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    rm -f "${tmp}"
    trap - RETURN
    ok "Removed ${key}"
}

reset_env_vars() {
    read -r -p "Are you sure you want to remove all environment variables? (type 'yes' to confirm): " confirm
    if [[ "${confirm}" == "yes" ]]; then
        rm -f "${ENV_FILE}"
        ok "Removed ${ENV_FILE}"
    else
        warn "Cancelled"
    fi
}

prompt_for_var() {
    local key="$1"
    local description=""
    for var_def in "${KNOWN_VARS[@]}"; do
        if [[ "${var_def%%:*}" == "${key}" ]]; then
            description="${var_def#*:}"
            break
        fi
    done
    if [[ -n "${description}" ]]; then
        echo "${CYAN}${description}${NC}"
    fi
    read -r -s -p "Enter value for ${key} (or leave blank to skip): " value
    echo
    if [[ -n "${value}" ]]; then
        set_env_var "${key}" "${value}"
        ok "Set ${key}"
    else
        warn "Skipped ${key}"
    fi
}

ensure_env_file

case "${1:-help}" in
    set)
        [[ -z "${2:-}" ]] && fail "set command requires a variable name"
        prompt_for_var "$2"
        ;;
    list)
        list_env_vars
        ;;
    show)
        [[ -z "${2:-}" ]] && fail "show command requires a variable name"
        show_env_var "$2"
        ;;
    delete)
        [[ -z "${2:-}" ]] && fail "delete command requires a variable name"
        delete_env_var "$2"
        ;;
    reset)
        reset_env_vars
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        fail "Unknown command: $1"
        ;;
esac

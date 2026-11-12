#!/usr/bin/env bash
# shellcheck disable=SC2034
# Shared fleet env for barb-worker lane processes (never echo tokens).
set -euo pipefail

BARB_FLEET_ENV_FILE="${HOME}/.config/systemd/user/barb-fleet.env"

_load_barb_fleet_env_file() {
    [[ -r "${BARB_FLEET_ENV_FILE}" ]] || return 0
    local _line _key _val
    while IFS= read -r _line || [[ -n "${_line}" ]]; do
        _line="${_line%%#*}"
        _line="${_line#"${_line%%[![:space:]]*}"}"
        [[ -z "${_line}" ]] && continue
        [[ "${_line}" == *=* ]] || continue
        _key="${_line%%=*}"
        _key="${_key%"${_key##*[![:space:]]}"}"
        _key="${_key#"${_key%%[![:space:]]*}"}"
        _val="${_line#*=}"
        case "${_key}" in
            BARB_WORKER_HUB_URL | FLEET_API_URL | BARB_LANE_PROXY_PORT | FLEET_API_TOKEN)
                if [[ -z "${!_key:-}" ]]; then
                    export "${_key}=${_val}"
                fi
                ;;
        esac
    done <"${BARB_FLEET_ENV_FILE}"
}

_load_barb_fleet_env_file

BARB_WORKER_HUB_URL="${BARB_WORKER_HUB_URL:-https://hub-a.fleet-example.ts.net}"
RD_CFG="${HOME}/.config/runner-dashboard"
TOKEN_FILE="${RD_CFG}/token"

export FLEET_API_URL="${FLEET_API_URL:-${BARB_WORKER_HUB_URL}}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1}"
export no_proxy="${no_proxy:-${NO_PROXY}}"

if [[ -z "${FLEET_API_TOKEN:-}" && -r "${TOKEN_FILE}" ]]; then
    IFS= read -r FLEET_API_TOKEN <"${TOKEN_FILE}" || FLEET_API_TOKEN=""
    export FLEET_API_TOKEN
fi

if [[ -n "${BARB_LANE_PROXY_PORT:-}" ]]; then
    _proxy="http://127.0.0.1:${BARB_LANE_PROXY_PORT}"
    export HTTP_PROXY="${HTTP_PROXY:-${_proxy}}"
    export HTTPS_PROXY="${HTTPS_PROXY:-${_proxy}}"
    export http_proxy="${http_proxy:-${HTTP_PROXY}}"
    export https_proxy="${https_proxy:-${HTTPS_PROXY}}"
fi

# Optional step-3 rollout (#3879): exchange static bootstrap bearer for per-run JWT.
# Default off; on failure keep the bootstrap token (no mid-migration lockout).
if [[ "${RUN_JWT_AGENT_SESSION:-0}" == "1" ]]; then
    _rd_root="${RUNNER_DASHBOARD_ROOT:-}"
    if [[ -z "${_rd_root}" ]]; then
        _script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        _rd_root="$(cd "${_script_dir}/../.." && pwd)"
    fi
    _jwt_helper="${_rd_root}/scripts/exchange_run_jwt.py"
    if command -v python3 >/dev/null 2>&1 \
        && [[ -f "${_jwt_helper}" ]] \
        && [[ -n "${FLEET_API_TOKEN:-}" ]] \
        && [[ -n "${FLEET_RUN_JWT_RUN_ID:-}" ]] \
        && [[ -n "${FLEET_RUN_JWT_CALLER_NODE:-}" ]]; then
        _principal="${FLEET_RUN_JWT_PRINCIPAL_ID:-${FLEET_AGENT:-}}"
        if [[ -n "${_principal}" ]]; then
            _run_jwt="$(python3 "${_jwt_helper}" token 2>/dev/null)" || _run_jwt=""
            if [[ -n "${_run_jwt}" ]]; then
                export FLEET_API_TOKEN="${_run_jwt}"
            fi
        fi
    fi
    unset _rd_root _script_dir _jwt_helper _principal _run_jwt
fi

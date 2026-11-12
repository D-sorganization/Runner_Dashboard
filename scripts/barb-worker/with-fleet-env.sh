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

#!/usr/bin/env bash
# shellcheck disable=SC2034
# Shared fleet env for barb-worker lane processes (never echo tokens).
set -euo pipefail

BARB_WORKER_HUB_URL="${BARB_WORKER_HUB_URL:-https://deskcomputer.tail2bbcc7.ts.net:8321}"
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

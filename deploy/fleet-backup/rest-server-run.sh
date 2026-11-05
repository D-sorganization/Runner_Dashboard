#!/usr/bin/env bash
# Start restic rest-server on the node Tailscale IPv4 only (append-only hub).
# Secrets live outside git; paths come from /etc/restic-rest-server/env (see
# rest-server.env.example). Intended for the fleet backup hub (example hub-a, #4016).
set -euo pipefail

RESTIC_REST_DATA_DIR="${RESTIC_REST_DATA_DIR:-/var/lib/restic-rest-server/data}"
RESTIC_REST_HTPASSWD_FILE="${RESTIC_REST_HTPASSWD_FILE:-/etc/restic-rest-server/htpasswd}"
RESTIC_REST_LISTEN_PORT="${RESTIC_REST_LISTEN_PORT:-8000}"

if [[ ! -f "${RESTIC_REST_HTPASSWD_FILE}" ]]; then
    echo "rest-server: missing htpasswd at ${RESTIC_REST_HTPASSWD_FILE} (operator must create)" >&2
    exit 1
fi

if ! command -v tailscale >/dev/null 2>&1; then
    echo "rest-server: tailscale CLI not found" >&2
    exit 1
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
if [[ -z "${TS_IP}" ]]; then
    echo "rest-server: tailscale ip -4 returned empty (daemon down or not authenticated)" >&2
    exit 1
fi

if ! command -v rest-server >/dev/null 2>&1; then
    echo "rest-server: rest-server binary not on PATH (run install-rest-server.sh)" >&2
    exit 1
fi

mkdir -p "${RESTIC_REST_DATA_DIR}"

exec rest-server \
    --append-only \
    --private-repos \
    --path "${RESTIC_REST_DATA_DIR}" \
    --htpasswd-file "${RESTIC_REST_HTPASSWD_FILE}" \
    --listen "${TS_IP}:${RESTIC_REST_LISTEN_PORT}"

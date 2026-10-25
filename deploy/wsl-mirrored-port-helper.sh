#!/usr/bin/env bash
# wsl-mirrored-port-helper.sh — clear / restore a Windows Tailscale-serve
# binding so a WSL2 service can bind the same port without conflict.
#
# Background
# ----------
# Under WSL2 ``networkingMode=mirrored`` (.wslconfig), the Linux guest
# shares the Windows host's network namespace. If Windows ``tailscaled``
# holds port 8321 on the Tailscale-assigned IP via ``tailscale serve
# --tcp 8321 ...``, **any** WSL process that tries to bind port 8321
# (even on 127.0.0.1, even on a different specific IP) fails with
# ``[Errno 98] address already in use``. ``ss -tlnp`` inside WSL shows
# nothing — the lock is enforced one layer down.
#
# The runner-dashboard hits this every time WSL cold-restarts: Tailscale
# starts first, holds 8321, and dashboard systemd then crash-loops on
# bind. The professional fix is to temporarily clear the Tailscale-serve
# binding around the dashboard's bind, then restore it once the dashboard
# is bound (the kernel allows both processes to coexist once both have an
# established bind, only the initial bind contends).
#
# This script is **idempotent** and a **no-op outside WSL-mirrored
# topologies**: on a non-WSL host, on WSL without ``tailscale.exe``, or on
# a NAT-networking WSL config, all modes succeed without action.
#
# Usage
# -----
#   wsl-mirrored-port-helper.sh clear   --port 8321
#   wsl-mirrored-port-helper.sh restore --port 8321
#
# Exit codes:
#   0 — success (including no-op)
#   2 — invalid argument
#   3 — tailscale.exe present but a call failed
#
# Wired by ``deploy/runner-dashboard.service``:
#   ExecStartPre=  wsl-mirrored-port-helper.sh clear   --port 8321
#   ExecStartPost= wsl-mirrored-port-helper.sh restore --port 8321
#
# Owner: runner-dashboard
set -euo pipefail

PROG="$(basename "$0")"

TAILSCALE_EXE="${WSL_MIRRORED_PORT_HELPER_TAILSCALE_EXE:-/mnt/c/Program Files/Tailscale/tailscale.exe}"
POWERSHELL_EXE="${WSL_MIRRORED_PORT_HELPER_POWERSHELL_EXE:-powershell.exe}"

log() { printf '[%s] %s\n' "$PROG" "$*" >&2; }

usage() {
    cat >&2 <<EOF
Usage: $PROG <clear|restore> --port <N>

Modes:
  clear     Remove any Windows ``tailscale serve`` binding on ``<port>`` so
            a WSL service can bind the same port without conflict.
  restore   Re-add the ``tailscale serve --http <port> http://127.0.0.1:<port>``
            bridge so the bound WSL service is reachable on the Tailnet.
EOF
}

# ---- argument parsing -----------------------------------------------------

MODE="${1:-}"
case "$MODE" in
    clear|restore) shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
esac

PORT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 ]] || [[ "$PORT" -gt 65535 ]]; then
    log "ERROR: --port must be 1..65535, got '${PORT}'"
    exit 2
fi

# ---- pre-flight: this is a no-op outside WSL-mirrored topologies ---------

if [[ "${WSL_MIRRORED_PORT_HELPER_ASSUME_WSL:-0}" != "1" ]] \
    && { [[ ! -r /proc/version ]] || ! grep -qiE 'microsoft|wsl' /proc/version; }; then
    log "not running under WSL; nothing to do (port $PORT)"
    exit 0
fi

windows_powershell_available() {
    command -v "$POWERSHELL_EXE" >/dev/null 2>&1 || [[ -x "$POWERSHELL_EXE" ]]
}

if ! windows_powershell_available; then
    log "Windows PowerShell not found ('$POWERSHELL_EXE'); nothing to do"
    exit 0
fi

run_windows_powershell() {
    "$POWERSHELL_EXE" -NoProfile -NonInteractive -Command "$1"
}

clear_windows_portproxy() {
    if ! windows_powershell_available; then
        log "Windows PowerShell not found ('$POWERSHELL_EXE'); skipping portproxy clear"
        return 0
    fi
    log "clearing Windows netsh portproxy bindings for port $PORT"
    local ps
    ps=$(cat <<EOF
\$ErrorActionPreference = 'SilentlyContinue'
foreach (\$addr in @('0.0.0.0', '127.0.0.1')) {
    & netsh interface portproxy delete v4tov4 listenport=$PORT listenaddress=\$addr | Out-Null
}
exit 0
EOF
)
    if ! run_windows_powershell "$ps" >/dev/null 2>&1; then
        log "ERROR: failed to clear Windows netsh portproxy bindings for port $PORT"
        exit 3
    fi
}

restore_windows_portproxy() {
    if ! windows_powershell_available; then
        log "Windows PowerShell not found ('$POWERSHELL_EXE'); skipping portproxy restore"
        return 0
    fi
    local wsl_ip
    wsl_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [[ -z "$wsl_ip" ]]; then
        log "WSL IP unavailable; skipping portproxy restore"
        return 0
    fi
    log "restoring Windows netsh portproxy: 0.0.0.0:$PORT -> $wsl_ip:$PORT"
    local ps
    ps=$(cat <<EOF
\$ErrorActionPreference = 'SilentlyContinue'
& netsh interface portproxy delete v4tov4 listenport=$PORT listenaddress=0.0.0.0 | Out-Null
& netsh interface portproxy add v4tov4 listenport=$PORT listenaddress=0.0.0.0 connectport=$PORT connectaddress=$wsl_ip | Out-Null
exit \$LASTEXITCODE
EOF
)
    if ! run_windows_powershell "$ps" >/dev/null 2>&1; then
        log "ERROR: failed to restore Windows netsh portproxy binding for port $PORT"
        exit 3
    fi
}

tailscale_available() {
    [[ -x "$TAILSCALE_EXE" ]]
}

if ! tailscale_available; then
    log "Windows tailscale.exe not found at '$TAILSCALE_EXE'; tailscale serve cleanup disabled"
fi

# ``tailscale serve status`` returns "No serve config" on stdout when nothing
# is configured. Use it as a cheap probe so we never call ``serve off`` on a
# system that has nothing to clear.
status_output() {
    if tailscale_available; then
        "$TAILSCALE_EXE" serve status 2>/dev/null || true
    fi
}

port_is_configured() {
    # The serve table prints lines like ``http://...:8321`` for each entry.
    # A literal ":${PORT}" hit anywhere in the listing is a reliable
    # "this port is currently served" signal — false positives are
    # acceptable because ``serve off`` is itself idempotent.
    status_output | grep -q ":${PORT}\\b"
}

port_has_tcp_binding() {
    status_output | grep -q "tcp://.*:${PORT}\\b"
}

port_has_http_binding() {
    status_output | grep -q "http://.*:${PORT}\\b"
}

port_has_https_binding() {
    status_output | grep -q "https://.*:${PORT}\\b"
}

case "$MODE" in
    clear)
        clear_windows_portproxy
        if ! tailscale_available || ! port_is_configured; then
            log "tailscale serve has no binding for port $PORT; nothing to clear"
            exit 0
        fi
        log "clearing tailscale serve binding for port $PORT"
        cleared=0
        failed=0
        if port_has_tcp_binding; then
            if "$TAILSCALE_EXE" serve --tcp="${PORT}" off >/dev/null 2>&1; then
                cleared=1
            else
                log "WARN: 'tailscale serve --tcp=$PORT off' failed"
                failed=1
            fi
        fi
        if port_has_http_binding; then
            if "$TAILSCALE_EXE" serve --http="${PORT}" off >/dev/null 2>&1; then
                cleared=1
            else
                log "WARN: 'tailscale serve --http=$PORT off' failed"
                failed=1
            fi
        fi
        if port_has_https_binding; then
            if "$TAILSCALE_EXE" serve --https="${PORT}" off >/dev/null 2>&1; then
                cleared=1
            else
                log "WARN: 'tailscale serve --https=$PORT off' failed"
                failed=1
            fi
        fi
        if [[ "$cleared" == "0" && "$failed" == "1" ]]; then
            log "ERROR: failed to clear all detected tailscale serve bindings for port $PORT"
            exit 3
        fi
        # Give the Windows TCP stack a moment to actually release the port
        # (TIME_WAIT on the listening socket is rare but not zero).
        sleep 1
        log "cleared"
        ;;
    restore)
        # uvicorn does not natively call ``sd_notify(READY=1)``, so systemd's
        # ``Type=notify`` actually fires ExecStartPost as soon as the process
        # is spawned — well before the bind. Poll for an established WSL
        # listener on $PORT before re-adding the Tailscale serve, otherwise
        # Tailscale wins the race again and the dashboard crash-loops.
        wait_for_local_bind() {
            local deadline=$((SECONDS + 30))
            while (( SECONDS < deadline )); do
                if ss -tln "sport = :${PORT}" 2>/dev/null | grep -q LISTEN; then
                    return 0
                fi
                sleep 1
            done
            return 1
        }
        if ! wait_for_local_bind; then
            log "ERROR: timed out waiting for a WSL listener on port $PORT (30s)"
            exit 3
        fi
        restore_windows_portproxy
        if ! tailscale_available || port_is_configured; then
            log "tailscale serve already has a binding for port $PORT; not re-adding"
            exit 0
        fi
        log "restoring tailscale serve bridge: http port $PORT -> http://127.0.0.1:$PORT"
        if ! "$TAILSCALE_EXE" serve --http "${PORT}" --bg --yes "http://127.0.0.1:${PORT}" >/dev/null 2>&1; then
            log "ERROR: 'tailscale serve --http $PORT --bg --yes http://127.0.0.1:$PORT' failed"
            exit 3
        fi
        log "restored"
        ;;
esac

exit 0

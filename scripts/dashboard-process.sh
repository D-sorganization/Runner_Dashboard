#!/usr/bin/env bash
# Shared pid-file identity helpers for start-dashboard.sh / stop-dashboard.sh (#4268).
# Sourced by the operator scripts at repo root — not meant to be executed directly.

dashboard_cmdline() {
    local pid="${1:?}"
    if [[ -r "/proc/${pid}/cmdline" ]]; then
        tr '\0' ' ' < "/proc/${pid}/cmdline"
        return 0
    fi
    if command -v ps >/dev/null 2>&1; then
        ps -p "${pid}" -o args= 2>/dev/null || return 1
        return 0
    fi
    return 1
}

dashboard_pid_is_alive() {
    local pid="${1:?}"
    kill -0 "${pid}" 2>/dev/null
}

# True when pid is our dashboard backend (server.py or uvicorn reload wrapper).
dashboard_pid_is_dashboard() {
    local pid="${1:?}"
    local repo_root="${2:?}"
    local backend_dir="${repo_root}/backend"
    local cmdline

    dashboard_pid_is_alive "${pid}" || return 1
    cmdline="$(dashboard_cmdline "${pid}")" || return 1

    if [[ "${cmdline}" == *"${backend_dir}/server.py"* ]]; then
        return 0
    fi
    if [[ "${cmdline}" == *"uvicorn"* && "${cmdline}" == *"server:app"* && "${cmdline}" == *"${backend_dir}"* ]]; then
        return 0
    fi
    return 1
}

wait_for_dashboard_health() {
    local host="${1:?}"
    local port="${2:?}"
    local timeout_s="${3:-30}"
    local probe_host="${host}"
    if [[ "${probe_host}" == "0.0.0.0" ]]; then
        probe_host="127.0.0.1"
    fi
    local url="http://${probe_host}:${port}/api/health"
    local deadline=$((SECONDS + timeout_s))

    while (( SECONDS < deadline )); do
        if command -v curl >/dev/null 2>&1; then
            if curl -fsS -o /dev/null "${url}" 2>/dev/null; then
                return 0
            fi
        elif command -v python3 >/dev/null 2>&1; then
            if python3 -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=2)" "${url}" 2>/dev/null; then
                return 0
            fi
        else
            echo "[ERROR] wait_for_dashboard_health: need curl or python3" >&2
            return 1
        fi
        sleep 1
    done
    return 1
}

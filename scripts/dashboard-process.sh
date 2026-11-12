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

# Populate DASHBOARD_CMDLINE_ARGV with argv from /proc (null-separated). Returns 1 if unreadable.
dashboard_cmdline_argv() {
    local pid="${1:?}"
    DASHBOARD_CMDLINE_ARGV=()
    if [[ ! -r "/proc/${pid}/cmdline" ]]; then
        return 1
    fi
    mapfile -d '' -t DASHBOARD_CMDLINE_ARGV < "/proc/${pid}/cmdline" || return 1
    if ((${#DASHBOARD_CMDLINE_ARGV[@]} > 0)) && [[ -z "${DASHBOARD_CMDLINE_ARGV[-1]}" ]]; then
        unset 'DASHBOARD_CMDLINE_ARGV[-1]'
    fi
    return 0
}

_dashboard_argv_is_python() {
    local bin="${1:?}"
    local base
    base="$(basename -- "${bin}")"
    [[ "${base}" == python ]] \
        || [[ "${base}" =~ ^python[0-9]+([.][0-9]+)?$ ]] \
        || [[ "${bin}" == *"/python" ]] \
        || [[ "${bin}" == *"/python3" ]]
}

_dashboard_argv_value_is_backend_dir() {
    local value="${1:?}"
    local backend_dir="${2:?}"
    [[ "${value}" == "${backend_dir}" ]] \
        || [[ "${value}" == "${backend_dir}/" ]]
}

_dashboard_cmdline_is_server_py_invocation() {
    local -n _argv=$1
    local backend_dir="${2:?}"
    local server_py="${backend_dir}/server.py"
    if ((${#_argv[@]} < 2)); then
        return 1
    fi
    _dashboard_argv_is_python "${_argv[0]}" || return 1
    [[ "${_argv[1]}" == "${server_py}" ]]
}

_dashboard_cmdline_is_uvicorn_invocation() {
    local -n _argv=$1
    local backend_dir="${2:?}"
    if ((${#_argv[@]} < 4)); then
        return 1
    fi
    _dashboard_argv_is_python "${_argv[0]}" || return 1
    [[ "${_argv[1]}" == "-m" ]] || return 1
    [[ "${_argv[2]}" == "uvicorn" ]] || return 1

    local has_server_app=0
    local has_backend_dir=0
    local i=3
    local arg
    while (( i < ${#_argv[@]} )); do
        arg="${_argv[i]}"
        if [[ "${arg}" == "server:app" ]]; then
            has_server_app=1
        fi
        if [[ "${arg}" == "--app-dir" || "${arg}" == "--reload-dir" ]]; then
            if (( i + 1 < ${#_argv[@]} )); then
                if _dashboard_argv_value_is_backend_dir "${_argv[i + 1]}" "${backend_dir}"; then
                    has_backend_dir=1
                fi
                ((i += 2))
                continue
            fi
        elif [[ "${arg}" == --app-dir=* || "${arg}" == --reload-dir=* ]]; then
            if _dashboard_argv_value_is_backend_dir "${arg#*=}" "${backend_dir}"; then
                has_backend_dir=1
            fi
        fi
        ((i += 1))
    done

    [[ "${has_server_app}" -eq 1 && "${has_backend_dir}" -eq 1 ]]
}

_dashboard_cmdline_is_server_py_invocation_ps() {
    local cmdline="${1:?}"
    local backend_dir="${2:?}"
    local server_py="${backend_dir}/server.py"
    local first="" second=""
    read -r first second _ <<< "${cmdline}"
    _dashboard_argv_is_python "${first}" || return 1
    [[ "${second}" == "${server_py}" ]]
}

_dashboard_cmdline_is_uvicorn_invocation_ps() {
    local cmdline="${1:?}"
    local backend_dir="${2:?}"
    local first="" second="" third=""
    read -r first second third _ <<< "${cmdline}"
    _dashboard_argv_is_python "${first}" || return 1
    [[ "${second}" == "-m" ]] || return 1
    [[ "${third}" == "uvicorn" ]] || return 1
    [[ "${cmdline}" == *" server:app "* || "${cmdline}" == *" server:app" ]] || return 1
    [[ "${cmdline}" == *"--app-dir ${backend_dir}"* \
        || "${cmdline}" == *"--app-dir=${backend_dir}"* \
        || "${cmdline}" == *"--reload-dir ${backend_dir}"* \
        || "${cmdline}" == *"--reload-dir=${backend_dir}"* ]]
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
    local -a argv=()

    dashboard_pid_is_alive "${pid}" || return 1

    if dashboard_cmdline_argv "${pid}"; then
        argv=("${DASHBOARD_CMDLINE_ARGV[@]}")
        if _dashboard_cmdline_is_server_py_invocation argv "${backend_dir}"; then
            return 0
        fi
        if _dashboard_cmdline_is_uvicorn_invocation argv "${backend_dir}"; then
            return 0
        fi
        return 1
    fi

    local cmdline
    cmdline="$(dashboard_cmdline "${pid}")" || return 1
    if _dashboard_cmdline_is_server_py_invocation_ps "${cmdline}" "${backend_dir}"; then
        return 0
    fi
    if _dashboard_cmdline_is_uvicorn_invocation_ps "${cmdline}" "${backend_dir}"; then
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

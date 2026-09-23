#!/usr/bin/env bash
# Select a Python runtime supported by the dashboard contract.
#
#   select_dashboard_python [REQUIRED_MINOR] [CAPABILITY]
#
# CAPABILITY names what the caller does with the interpreter:
#   pip   (default) run `python -m pip` on the host interpreter itself
#         (artifact packaging builds the wheelhouse this way).
#   venv  create a virtualenv that carries its own pip (artifact install).
#         Host pip is NOT required: a distro python3.X without pip but with
#         working venv + ensurepip qualifies (#1212). The probe builds a
#         throwaway venv, because Debian ships an importable ensurepip that
#         still refuses to run when python3.X-venv is missing.

dashboard_python_minor() {
    "$1" -c '
import sys
if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
    raise SystemExit(1)
print(f"{sys.version_info.major}.{sys.version_info.minor}")
' 2>/dev/null
}

dashboard_python_can_bootstrap_venv() {
    local candidate="$1"
    local probe
    local status=1
    probe="$(mktemp -d)" || return 1
    if "${candidate}" -m venv "${probe}/venv" >/dev/null 2>&1 \
        && "${probe}/venv/bin/python" -m pip --version >/dev/null 2>&1; then
        status=0
    fi
    rm -rf "${probe}"
    return "${status}"
}

select_dashboard_python() {
    local candidate
    local candidate_minor
    local required_minor="${1:-}"
    local capability="${2:-pip}"
    local candidates=()
    [[ -n "${RUNNER_DASHBOARD_PYTHON:-}" ]] && candidates+=("${RUNNER_DASHBOARD_PYTHON}")
    candidates+=(python3.13 python3.12 python3.11 python3)

    case "${capability}" in
        pip|venv) ;;
        *) echo "select_dashboard_python: unknown capability '${capability}' (expected pip or venv)" >&2; return 2 ;;
    esac

    for candidate in "${candidates[@]}"; do
        command -v "${candidate}" >/dev/null 2>&1 || continue
        candidate_minor="$(dashboard_python_minor "${candidate}")" || continue
        [[ -z "${required_minor}" || "${candidate_minor}" == "${required_minor}" ]] || continue
        if [[ "${capability}" == "venv" ]]; then
            dashboard_python_can_bootstrap_venv "${candidate}" || continue
        else
            "${candidate}" -m pip --version >/dev/null 2>&1 || continue
        fi
        command -v "${candidate}"
        return 0
    done

    local need="python -m pip"
    [[ "${capability}" == "venv" ]] && need="python -m venv with ensurepip (e.g. apt install python${required_minor:-3}-venv)"
    echo "No supported Python found; runner-dashboard requires >=3.11,<3.14${required_minor:+ and artifact ABI ${required_minor}} with ${need}" >&2
    return 1
}

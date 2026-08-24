#!/usr/bin/env bash
# Select a Python runtime supported by the dashboard contract.

select_dashboard_python() {
    local candidate
    local candidate_minor
    local required_minor="${1:-}"
    local candidates=()
    [[ -n "${RUNNER_DASHBOARD_PYTHON:-}" ]] && candidates+=("${RUNNER_DASHBOARD_PYTHON}")
    candidates+=(python3.13 python3.12 python3.11 python3)

    for candidate in "${candidates[@]}"; do
        command -v "${candidate}" >/dev/null 2>&1 || continue
        candidate_minor="$("${candidate}" -c '
import sys
if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
    raise SystemExit(1)
print(f"{sys.version_info.major}.{sys.version_info.minor}")
' 2>/dev/null)" || continue
        [[ -z "${required_minor}" || "${candidate_minor}" == "${required_minor}" ]] || continue
        if "${candidate}" -m pip --version >/dev/null 2>&1; then
            command -v "${candidate}"
            return 0
        fi
    done

    echo "No supported Python found; runner-dashboard requires >=3.11,<3.14${required_minor:+ and artifact ABI ${required_minor}}" >&2
    return 1
}

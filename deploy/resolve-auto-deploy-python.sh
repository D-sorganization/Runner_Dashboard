#!/usr/bin/env bash
# Resolve the Python interpreter for node auto-deploy (dashboard venv, not system python3).
# Source from node-auto-deploy.sh and install-node-auto-deploy.sh — do not execute.
set -euo pipefail

AUTO_DEPLOY_MIN_PYTHON_MAJOR=3
AUTO_DEPLOY_MIN_PYTHON_MINOR=11

_auto_deploy_execstart_python() {
  local scope="$1"
  local -a cmd=(systemctl)
  if [[ "${scope}" == user ]]; then
    cmd+=(--user)
  fi
  cmd+=(show runner-dashboard.service --property=ExecStart --value)
  local line=""
  line="$("${cmd[@]}" 2>/dev/null || true)"
  [[ -n "${line}" ]] || return 1
  local bin=""
  bin="$(sed -n 's/.*path=\([^ ;}]*\).*/\1/p' <<<"${line}" | head -n1)"
  if [[ -n "${bin}" && -x "${bin}" ]]; then
    echo "${bin}"
    return 0
  fi
  return 1
}

resolve_auto_deploy_python() {
  local deploy_dir="${1:?deploy dir required}"
  local preferred_scope="${2:-system}"

  if [[ -n "${RUNNER_DASHBOARD_PYTHON:-}" && -x "${RUNNER_DASHBOARD_PYTHON}" ]]; then
    echo "${RUNNER_DASHBOARD_PYTHON}"
    return 0
  fi

  local venv_python="${deploy_dir}/.venv/bin/python"
  if [[ -x "${venv_python}" ]]; then
    echo "${venv_python}"
    return 0
  fi

  local from_unit=""
  if [[ "${preferred_scope}" == user ]]; then
    from_unit="$(_auto_deploy_execstart_python user || _auto_deploy_execstart_python system || true)"
  else
    from_unit="$(_auto_deploy_execstart_python system || _auto_deploy_execstart_python user || true)"
  fi
  if [[ -n "${from_unit}" ]]; then
    echo "${from_unit}"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  echo "node-auto-deploy: no Python interpreter found (set RUNNER_DASHBOARD_PYTHON or install dashboard .venv)" >&2
  return 1
}

require_auto_deploy_python_version() {
  local python_bin="${1:?python required}"
  "${python_bin}" -c "
import sys
major, minor = ${AUTO_DEPLOY_MIN_PYTHON_MAJOR}, ${AUTO_DEPLOY_MIN_PYTHON_MINOR}
if sys.version_info < (major, minor):
    v = f'{sys.version_info.major}.{sys.version_info.minor}'
    need = f'{major}.{minor}'
    sys.stderr.write(
        f'node-auto-deploy requires Python {need}+ (dashboard venv); got {v} from {sys.executable}\n'
    )
    raise SystemExit(1)
"
}

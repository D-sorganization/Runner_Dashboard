#!/usr/bin/env bash
# Shared path resolution for node auto-deploy (safe when HOME is unset under systemd).
# Source from node-auto-deploy.sh and install-node-auto-deploy.sh — do not execute.
set -euo pipefail

auto_deploy_dashboard_user() {
  if [[ -n "${RUNNER_DASHBOARD_USER:-}" ]]; then
    echo "${RUNNER_DASHBOARD_USER}"
    return
  fi
  if command -v systemctl >/dev/null 2>&1; then
    local unit_user
    unit_user="$(systemctl show runner-dashboard.service -p User --value 2>/dev/null || true)"
    if [[ -n "${unit_user}" && "${unit_user}" != "0" ]]; then
      echo "${unit_user}"
      return
    fi
  fi
  id -un
}

auto_deploy_resolve_home() {
  if [[ -n "${HOME:-}" ]]; then
    echo "${HOME}"
    return
  fi
  local user home
  user="$(auto_deploy_dashboard_user)"
  home="$(getent passwd "${user}" 2>/dev/null | cut -d: -f6 || true)"
  if [[ -n "${home}" ]]; then
    echo "${home}"
    return
  fi
  echo "/home/${user}"
}

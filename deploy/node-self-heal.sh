#!/usr/bin/env bash
# Node-local self-healing watchdog for Runner Dashboard fleet hosts.
#
# Eliminates four recurring failure classes without human/LLM intervention:
#   1. Windows <-> WSL provider credential drift (claude, codex, antigravity).
#   2. Stale unactivated dashboard-releases/<sha> directories that wedge auto-deploy.
#   3. Unresponsive local runner-dashboard service on :8321.
#   4. Low disk space (<15% free) before emergency disk-full cordon triggers.
#
# Never prints or logs any token or secret values.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_HOME="${HOME:-/home/dieterolson}"
BASE_DIR="${RUNNER_BASE_DIR:-${DASHBOARD_HOME}/actions-runners}"
DEPLOY_DIR="${RUNNER_DASHBOARD_DEPLOY_DIR:-${BASE_DIR}/dashboard}"
PORT="${DASHBOARD_PORT:-8321}"
PYTHON_BIN="${RUNNER_DASHBOARD_PYTHON:-python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi
STATUS_FILE="${DASHBOARD_HOME}/.config/runner-dashboard/self-heal-status.json"

healed_actions=()

# 1. Bi-directional Windows <-> WSL provider credential sync (0600 permissions)
SYNC_SCRIPT="${SCRIPT_DIR}/sync_provider_credentials.py"
if [[ ! -f "${SYNC_SCRIPT}" && -f "${DASHBOARD_HOME}/.config/runner-dashboard/self-heal/sync_provider_credentials.py" ]]; then
  SYNC_SCRIPT="${DASHBOARD_HOME}/.config/runner-dashboard/self-heal/sync_provider_credentials.py"
fi
if [[ -f "${SYNC_SCRIPT}" ]]; then
  if sync_out="$("${PYTHON_BIN}" "${SYNC_SCRIPT}" 2>/dev/null)"; then
    if echo "${sync_out}" | grep -q '"\/'; then
      healed_actions+=("synced_provider_credentials")
    fi
  fi
fi

# 2. Prune stale unactivated release directories (>15 min old) when auto-deploy is idle
RELEASES_DIR="${BASE_DIR}/dashboard-releases"
auto_deploy_busy=0
if systemctl is-active runner-dashboard-auto-deploy.service &>/dev/null || \
   systemctl --user is-active runner-dashboard-auto-deploy.service &>/dev/null 2>&1; then
  auto_deploy_busy=1
fi

if [[ "${auto_deploy_busy}" -eq 0 && -d "${RELEASES_DIR}" && -L "${DEPLOY_DIR}" ]]; then
  live_target="$(realpath "${DEPLOY_DIR}" 2>/dev/null || true)"
  prev_commit="$("${PYTHON_BIN}" -c 'import json, os, sys; p = os.path.expanduser("~/.config/runner-dashboard/auto_deploy_state.json"); d = json.load(open(p)) if os.path.isfile(p) else {}; print(d.get("previous_commit") or "")' 2>/dev/null || true)"
  prev_target=""
  if [[ -n "${prev_commit}" && -d "${RELEASES_DIR}/${prev_commit}" ]]; then
    prev_target="$(realpath "${RELEASES_DIR}/${prev_commit}" 2>/dev/null || true)"
  fi
  while IFS= read -r -d '' cand; do
    cand_real="$(realpath "${cand}" 2>/dev/null || true)"
    [[ -z "${cand_real}" ]] && continue
    [[ "${cand_real}" == "${live_target}" ]] && continue
    [[ -n "${prev_target}" && "${cand_real}" == "${prev_target}" ]] && continue
    rm -rf "${cand_real}" 2>/dev/null || true
    healed_actions+=("pruned_stale_release:$(basename "${cand_real}")")
  done < <(find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d -mmin +15 -print0 2>/dev/null || true)
fi

# 3. Verify local Runner Dashboard HTTP health and restart if hung
health_ok=0
for _ in 1 2; do
  if curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
    health_ok=1
    break
  fi
  sleep 2
done

if [[ "${health_ok}" -eq 0 && "${auto_deploy_busy}" -eq 0 ]]; then
  if systemctl --user is-enabled runner-dashboard.service &>/dev/null 2>&1 || \
     systemctl --user is-active runner-dashboard.service &>/dev/null 2>&1; then
    systemctl --user restart runner-dashboard.service || true
    healed_actions+=("restarted_user_runner_dashboard")
  elif systemctl is-enabled runner-dashboard.service &>/dev/null 2>&1 || \
       systemctl is-active runner-dashboard.service &>/dev/null 2>&1; then
    sudo -n systemctl restart runner-dashboard.service 2>/dev/null || systemctl restart runner-dashboard.service 2>/dev/null || true
    healed_actions+=("restarted_system_runner_dashboard")
  fi
fi

# 4. Proactive disk guard (<15% free on runner base filesystem)
if [[ -d "${BASE_DIR}" ]]; then
  free_pct="$("${PYTHON_BIN}" -c 'import os, shutil, sys; u = shutil.disk_usage(sys.argv[1]); print(int(round(u.free * 100.0 / u.total)))' "${BASE_DIR}" 2>/dev/null || echo 100)"
  if [[ "${free_pct}" -lt 15 ]]; then
    find /tmp -mindepth 1 -maxdepth 1 -mmin +60 \
      \( -name "pytest-of-*" -o -name "pip-*" -o -name "tmp*" -o -name "cargo-*" -o -name "rust*" \) \
      -exec rm -rf {} + 2>/dev/null || true
    healed_actions+=("proactive_tmp_trim:free_${free_pct}pct")
  fi
fi

mkdir -p "$(dirname "${STATUS_FILE}")"
"${PYTHON_BIN}" - "${STATUS_FILE}" "${health_ok}" "${healed_actions[@]:-}" << 'PY'
import datetime, json, os, sys
status_path = sys.argv[1]
health_ok = sys.argv[2] == "1"
actions = [a for a in sys.argv[3:] if a]
payload = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "healthy": health_ok,
    "actions": actions,
}
tmp = f"{status_path}.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
os.replace(tmp, status_path)
PY

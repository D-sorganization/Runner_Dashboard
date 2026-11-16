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
ENV_CHECK="${SCRIPT_DIR}/runner_dashboard_env_check.py"
if [[ ! -f "${ENV_CHECK}" && -f "${DEPLOY_DIR}/deploy/runner_dashboard_env_check.py" ]]; then
  ENV_CHECK="${DEPLOY_DIR}/deploy/runner_dashboard_env_check.py"
fi

healed_actions=()
env_file_ok=1
env_file_issues_json="[]"

# 1. Bi-directional Windows <-> WSL provider credential sync (0600 permissions)
SYNC_SCRIPT="${SCRIPT_DIR}/sync_provider_credentials.py"
if [[ ! -f "${SYNC_SCRIPT}" && -f "${DASHBOARD_HOME}/.config/runner-dashboard/self-heal/sync_provider_credentials.py" ]]; then
  SYNC_SCRIPT="${DASHBOARD_HOME}/.config/runner-dashboard/self-heal/sync_provider_credentials.py"
fi
credential_sync_ok=1
credential_sync_error=""
if [[ -f "${SYNC_SCRIPT}" ]]; then
  if sync_out="$("${PYTHON_BIN}" "${SYNC_SCRIPT}" 2>/dev/null)"; then
    if echo "${sync_out}" | grep -q '"\/'; then
      healed_actions+=("synced_provider_credentials")
    fi
  else
    credential_sync_ok=0
    credential_sync_error="$(printf '%s' "${sync_out:-}" | "${PYTHON_BIN}" -c '
import json, sys
raw = sys.stdin.read().strip()
try:
    payload = json.loads(raw) if raw else {}
except ValueError:
    payload = {}
print(str(payload.get("error") or "sync_provider_credentials.py exited non-zero")[:200])
' 2>/dev/null || echo "sync_provider_credentials.py exited non-zero")"
    healed_actions+=("alert:credential_sync_failed")
  fi
  # Detect a self-heal copy that has drifted from the live deployed script (#3975).
  CANONICAL_SYNC_SCRIPT="${DEPLOY_DIR}/deploy/sync_provider_credentials.py"
  if [[ -f "${CANONICAL_SYNC_SCRIPT}" ]] && ! cmp -s "${SYNC_SCRIPT}" "${CANONICAL_SYNC_SCRIPT}" 2>/dev/null; then
    healed_actions+=("alert:sync_script_stale")
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

# 2b. Detect NUL bytes or unparseable lines in the secrets EnvironmentFile (systemd skips the whole file)
ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"
if [[ -f "${ENV_CHECK}" ]]; then
  env_report="$("${PYTHON_BIN}" "${ENV_CHECK}" --env-file "${ENV_FILE}" --json 2>/dev/null || echo '{"ok":true}')"
  if ! echo "${env_report}" | "${PYTHON_BIN}" -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null; then
    env_file_ok=0
    env_file_issues_json="$(echo "${env_report}" | "${PYTHON_BIN}" -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("issues") or []))' 2>/dev/null || echo "[]")"
    "${PYTHON_BIN}" "${ENV_CHECK}" --env-file "${ENV_FILE}" --warn 2>&1 || true
    healed_actions+=("alert:env_file_corrupt")
  fi
fi

# 3. Verify local Runner Dashboard HTTP health (unauthenticated /health) and restart if hung
health_ok=0
for _ in 1 2; do
  if curl -fsS --max-time 8 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    health_ok=1
    break
  fi
  sleep 2
done

if [[ "${health_ok}" -eq 0 && "${auto_deploy_busy}" -eq 0 && "${env_file_ok}" -eq 1 ]]; then
  LOOPBACK_GUARD="${SCRIPT_DIR}/loopback_bind_guard.py"
  if [[ ! -f "${LOOPBACK_GUARD}" && -f "${DASHBOARD_HOME}/actions-runners/dashboard/deploy/loopback_bind_guard.py" ]]; then
    LOOPBACK_GUARD="${DASHBOARD_HOME}/actions-runners/dashboard/deploy/loopback_bind_guard.py"
  fi
  ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"
  bind_scope=system
  if systemctl --user is-enabled runner-dashboard.service &>/dev/null 2>&1 || \
     systemctl --user is-active runner-dashboard.service &>/dev/null 2>&1; then
    bind_scope=user
  fi
  needs_loopback_repair=0
  journal_bind_failure=0
  if [[ -f "${LOOPBACK_GUARD}" ]]; then
    if "${PYTHON_BIN}" "${LOOPBACK_GUARD}" detect-journal --service runner-dashboard.service 2>/dev/null; then
      needs_loopback_repair=1
      journal_bind_failure=1
      healed_actions+=("alert:loopback_bind_guard:journal_startup_failure")
    fi
    issue_json="$("${PYTHON_BIN}" "${LOOPBACK_GUARD}" diagnose --env-file "${ENV_FILE}" 2>/dev/null || echo "null")"
    if [[ "${issue_json}" != "null" ]]; then
      needs_loopback_repair=1
      healed_actions+=("alert:loopback_bind_guard:env_misconfigured")
    fi
    if [[ "${needs_loopback_repair}" -eq 1 ]]; then
      force_dropin_flag=()
      if [[ "${journal_bind_failure}" -eq 1 ]]; then
        force_dropin_flag=(--force-dropin)
      fi
      repair_json="$("${PYTHON_BIN}" "${LOOPBACK_GUARD}" repair \
        --env-file "${ENV_FILE}" --scope "${bind_scope}" --home "${DASHBOARD_HOME}" \
        "${force_dropin_flag[@]}" 2>/dev/null || true)"
      if echo "${repair_json}" | grep -q 'env_set_dashboard_host_loopback'; then
        healed_actions+=("loopback_bind_env_repair")
      fi
      if echo "${repair_json}" | grep -q 'installed_systemd_dropin'; then
        healed_actions+=("loopback_bind_dropin_installed")
      fi
    fi
  fi
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

# 3b. Ensure native-Linux tailnet serve bridge when loopback auth binds 127.0.0.1 only
if [[ "${health_ok}" -eq 1 ]]; then
  HUB_SERVE="${SCRIPT_DIR}/hub_tailnet_serve.py"
  if [[ ! -f "${HUB_SERVE}" && -f "${DEPLOY_DIR}/deploy/hub_tailnet_serve.py" ]]; then
    HUB_SERVE="${DEPLOY_DIR}/deploy/hub_tailnet_serve.py"
  fi
  ENV_FILE="${RUNNER_DASHBOARD_ENV_FILE:-${DASHBOARD_HOME}/.config/runner-dashboard/env}"
  if [[ -f "${HUB_SERVE}" ]]; then
    if serve_out="$("${PYTHON_BIN}" "${HUB_SERVE}" ensure --env-file "${ENV_FILE}" --port "${PORT}" 2>/dev/null)"; then
      if echo "${serve_out}" | grep -q '"action": "configured"'; then
        healed_actions+=("hub_tailnet_serve_configured")
      fi
    fi
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
"${PYTHON_BIN}" - "${STATUS_FILE}" "${health_ok}" "${credential_sync_ok}" "${credential_sync_error}" "${env_file_ok}" "${env_file_issues_json}" "${healed_actions[@]:-}" << 'PY'
import datetime, json, os, sys
status_path = sys.argv[1]
health_ok = sys.argv[2] == "1"
credential_sync_ok = sys.argv[3] == "1"
credential_sync_error = sys.argv[4]
env_file_ok = sys.argv[5] == "1"
try:
    env_file_issues = json.loads(sys.argv[6]) if sys.argv[6] else []
except json.JSONDecodeError:
    env_file_issues = []
actions = [a for a in sys.argv[7:] if a]
payload = {
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "healthy": health_ok and credential_sync_ok and env_file_ok,
    "http_healthy": health_ok,
    "credential_sync_ok": credential_sync_ok,
    "credential_sync_error": credential_sync_error or None,
    "env_file_ok": env_file_ok,
    "env_file_issues": env_file_issues or None,
    "actions": actions,
}
tmp = f"{status_path}.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
os.replace(tmp, status_path)
PY

#!/usr/bin/env bash
set -euo pipefail

UNIT="${1:-runner-dashboard.service}"
RESULT="${SERVICE_RESULT:-unknown}"

if [[ "${RESULT}" == "success" && "${RUNNER_DASHBOARD_DIAG_ALWAYS:-}" != "1" ]]; then
    exit 0
fi

BASE_DIR="${RUNNER_DASHBOARD_DIAG_DIR:-$HOME/.local/share/runner-dashboard/crash-diagnostics}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BASE_DIR}/${STAMP}-${UNIT//[^A-Za-z0-9_.-]/_}-${RESULT}"
mkdir -p "${OUT_DIR}"

{
    echo "timestamp_utc=${STAMP}"
    echo "unit=${UNIT}"
    echo "service_result=${RESULT}"
    echo "exit_code=${EXIT_CODE:-unknown}"
    echo "exit_status=${EXIT_STATUS:-unknown}"
    echo "hostname=$(hostname)"
    echo "wsl_distro=${WSL_DISTRO_NAME:-}"
    echo "boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
    echo "uptime=$(cat /proc/uptime 2>/dev/null || true)"
} > "${OUT_DIR}/summary.env"

systemctl status "${UNIT}" --no-pager -l > "${OUT_DIR}/systemctl-status.txt" 2>&1 || true
journalctl -u "${UNIT}" -n 300 --no-pager > "${OUT_DIR}/journal-tail.txt" 2>&1 || true
df -h > "${OUT_DIR}/df-h.txt" 2>&1 || true
cat /proc/pressure/io > "${OUT_DIR}/pressure-io.txt" 2>&1 || true
cat /proc/pressure/memory > "${OUT_DIR}/pressure-memory.txt" 2>&1 || true
ps -eo pid,ppid,stat,pcpu,pmem,comm,args --sort=-pcpu | head -80 > "${OUT_DIR}/top-processes.txt" 2>&1 || true
systemctl list-units 'actions.runner.*.service' --no-legend --plain --all > "${OUT_DIR}/runner-units.txt" 2>&1 || true
pgrep -af Runner.Listener > "${OUT_DIR}/runner-listeners.txt" 2>&1 || true

find "${BASE_DIR}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR>50 {print $2}' \
    | xargs -r rm -rf

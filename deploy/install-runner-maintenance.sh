#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
RUNNER_USER="${RUNNER_USER:-$USER}"
SCHEDULE_CONFIG="${RUNNER_SCHEDULE_CONFIG:-$HOME/.config/runner-dashboard/runner-schedule.json}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-$(command -v systemctl)}"

echo "Installing runner maintenance services for ${RUNNER_USER}"

install -d -m 0755 "$(dirname "${SCHEDULE_CONFIG}")"
if [[ ! -f "${SCHEDULE_CONFIG}" ]]; then
    install -m 0644 "${PROJECT_ROOT}/config/runner-schedule.json" "${SCHEDULE_CONFIG}"
fi

sudo install -m 0755 "${SCRIPT_DIR}/runner-cleanup.sh" /usr/local/bin/runner-cleanup
sudo install -m 0755 "${SCRIPT_DIR}/runner-scheduler.py" /usr/local/bin/runner-scheduler
sudo install -d -m 0755 /var/log/runner-cleanup /var/lib/runner-scheduler

SCHEDULER_SUDOERS="/etc/sudoers.d/runner-dashboard-scheduler"
sudo tee "${SCHEDULER_SUDOERS}" > /dev/null <<SUDOERS
# Allow the dashboard Apply Now button to trigger the root-owned scheduler unit.
${RUNNER_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} start runner-scheduler.service
SUDOERS
sudo chmod 0440 "${SCHEDULER_SUDOERS}"
sudo visudo -cf "${SCHEDULER_SUDOERS}" > /dev/null

sudo tee /etc/systemd/system/runner-cleanup.service > /dev/null <<SERVICE
[Unit]
Description=Clean stale GitHub runner, Docker, and WSL cache data
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
User=root
Environment=RUNNER_ROOT=${RUNNER_ROOT}
Environment=RUNNER_USER=${RUNNER_USER}
Environment=RUNNER_WORK_DAYS=3
Environment=RUNNER_TEMP_DAYS=1
Environment=TOOL_CACHE_DAYS=21
Environment=DOCKER_PRUNE_UNTIL=168h
Environment=PRUNE_DOCKER_VOLUMES=0
ExecStart=/usr/local/bin/runner-cleanup
SERVICE

sudo tee /etc/systemd/system/runner-cleanup.timer > /dev/null <<'TIMER'
[Unit]
Description=Run GitHub runner cleanup daily

[Timer]
OnCalendar=*-*-* 04:20:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
TIMER

sudo tee /etc/systemd/system/runner-scheduler.service > /dev/null <<SERVICE
[Unit]
Description=Apply scheduled GitHub runner capacity
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Environment=RUNNER_ROOT=${RUNNER_ROOT}
Environment=RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG}
ExecStart=/usr/local/bin/runner-scheduler --apply
SERVICE

sudo tee /etc/systemd/system/runner-scheduler.timer > /dev/null <<'TIMER'
[Unit]
Description=Apply GitHub runner capacity schedule every five minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=5m
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable --now runner-cleanup.timer runner-scheduler.timer

echo "Installed:"
systemctl list-timers runner-cleanup.timer runner-scheduler.timer --all

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNNER_ROOT="${RUNNER_ROOT:-$HOME/actions-runners}"
# Colon-separated list of parent dirs to manage (see runner-cleanup.sh).
# Defaults to RUNNER_ROOT for backwards compat. Hosts that serve runners
# from multiple disks (e.g. HDD + NVMe) should set this explicitly, e.g.
#   RUNNER_ROOTS="$HOME/actions-runners:$HOME/actions-runners-nvme"
RUNNER_ROOTS="${RUNNER_ROOTS:-$RUNNER_ROOT}"
RUNNER_USER="${RUNNER_USER:-$USER}"
SCHEDULE_CONFIG="${RUNNER_SCHEDULE_CONFIG:-$HOME/.config/runner-dashboard/runner-schedule.json}"
SCHEDULER_PYTHON="${SCHEDULER_PYTHON:-${HOME}/actions-runners/dashboard/.venv/bin/python}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-$(command -v systemctl)}"
TEXTFILE_COLLECTOR_DIR="${TEXTFILE_COLLECTOR_DIR:-/var/lib/node_exporter/textfile_collector}"

echo "Installing runner maintenance services for ${RUNNER_USER}"

if [[ ! -x "${SCHEDULER_PYTHON}" ]]; then
    echo "Scheduler Python is not executable: ${SCHEDULER_PYTHON}" >&2
    exit 1
fi

install -d -m 0755 "$(dirname "${SCHEDULE_CONFIG}")"
if [[ ! -f "${SCHEDULE_CONFIG}" ]]; then
    install -m 0644 "${PROJECT_ROOT}/config/runner-schedule.json" "${SCHEDULE_CONFIG}"
fi

sudo install -m 0755 "${SCRIPT_DIR}/runner-scheduler.py" /usr/local/bin/runner-scheduler
sudo install -m 0755 "${SCRIPT_DIR}/runner-corruption-scan.sh" /usr/local/bin/runner-corruption-scan
# Fleet memory policy (RM #2071): drop page cache above THRESH% so WSL returns
# memory to Windows even while CI keeps the VM busy. Script is /usr/local/sbin
# (root-only), units are copied verbatim from deploy/.
sudo -n install -m 0755 "${SCRIPT_DIR}/wsl-cache-trim" /usr/local/sbin/wsl-cache-trim
sudo -n install -m 0644 "${SCRIPT_DIR}/wsl-cache-trim.service" "${SCRIPT_DIR}/wsl-cache-trim.timer" /etc/systemd/system/
# Operator break-glass from #661 — drains, heals, and restarts every
# actions.runner.*.service on the host.
sudo install -m 0755 "${SCRIPT_DIR}/heal-host.sh" /usr/local/bin/heal-host
sudo install -m 0755 "${SCRIPT_DIR}/configure-runner-tmpdir.sh" /usr/local/bin/configure-runner-tmpdir
sudo install -m 0755 "${SCRIPT_DIR}/configure-runner-pipcache.sh" /usr/local/bin/configure-runner-pipcache
if [[ -x "${SCRIPT_DIR}/configure-runner-tmpdir.sh" ]]; then
    sudo RUNNER_USER="${RUNNER_USER}" "${SCRIPT_DIR}/configure-runner-tmpdir.sh" || true
fi
if [[ -x "${SCRIPT_DIR}/configure-runner-pipcache.sh" ]]; then
    sudo RUNNER_USER="${RUNNER_USER}" "${SCRIPT_DIR}/configure-runner-pipcache.sh" || true
fi
# Prune stale cargo/env source statements from shell profiles (#1159)
if [[ -f "${SCRIPT_DIR}/clean-stale-shell-profiles.sh" ]]; then
    sudo install -m 0755 "${SCRIPT_DIR}/clean-stale-shell-profiles.sh" /usr/local/bin/clean-stale-shell-profiles
    sudo -u "${RUNNER_USER}" /usr/local/bin/clean-stale-shell-profiles || true
fi
# Prune credential-bearing url.*.insteadOf sections CI jobs left in ~/.gitconfig (#1216)
if [[ -f "${SCRIPT_DIR}/clean-gitconfig-token-rewrites.sh" ]]; then
    sudo install -m 0755 "${SCRIPT_DIR}/clean-gitconfig-token-rewrites.sh" /usr/local/bin/clean-gitconfig-token-rewrites
    sudo -u "${RUNNER_USER}" -H /usr/local/bin/clean-gitconfig-token-rewrites || true
fi
# Runner job-pickup hooks from #664 — referenced by the per-unit
# drop-ins written by migrate-runner-units.sh. Installed at a stable
# system path so the drop-ins don't depend on a repo checkout location.
HOOK_INSTALL_DIR="/usr/local/bin/runner-hooks"
# The restore timer's source is a mirror of the repo hooks that
# refresh-maintenance-assets.sh keeps current (#1977); it used to be a repo
# checkout under /opt that went stale or was absent.
HOOK_SRC_DIR_INSTALLED="/usr/local/share/runner-hooks"
sudo install -d -m 0755 "${HOOK_INSTALL_DIR}" "${HOOK_SRC_DIR_INSTALLED}"
for hook in job-started.sh job-completed.sh force-drain.sh; do
    sudo install -m 0755 "${SCRIPT_DIR}/runner-hooks/${hook}" "${HOOK_INSTALL_DIR}/${hook}"
    sudo install -m 0755 "${SCRIPT_DIR}/runner-hooks/${hook}" "${HOOK_SRC_DIR_INSTALLED}/${hook}"
done
# Self-heal pass — restores the three hook scripts if they go missing
# between deploys. Idempotent; safe to run on a short timer. Wired up
# below as runner-hooks-restore.{service,timer}. Root cause this
# guards against: fleet outage 2026-05-27, where multiple runners
# had ACTIONS_RUNNER_HOOK_JOB_COMPLETED env vars pointing at files
# that had been removed from /usr/local/bin/runner-hooks/, producing
# `##[error]File doesn't exist` on every job-completed callback and
# stalling all CI fleet-wide.
sudo install -m 0755 "${SCRIPT_DIR}/runner-hooks-restore.sh" /usr/local/bin/runner-hooks-restore
# Lockfile dir consulted by job-started.sh, job-completed.sh, the
# autoscaler busy-check (#664), and runner-cleanup.sh GC (#651).
# Must be writable by the runner user.
#
# /run is tmpfs and is wiped on every reboot/WSL restart, so this install-time
# mkdir does NOT survive a restart — and the JOB_STARTED hook runs as the
# (non-root) runner user, which cannot mkdir under root-owned /run. Without a
# boot-time recreation the lockfile busy-signal goes dead after every reboot
# and the cleanup/autoscaler can stop a busy runner mid-job (#640). Install a
# systemd-tmpfiles config so root recreates the dir on every boot, then
# materialise it now.
RUNNER_BUSY_TMPFILES=/etc/tmpfiles.d/runner-busy.conf
sudo sed "s/__RUNNER_USER__/${RUNNER_USER}/g" \
    "${SCRIPT_DIR}/tmpfiles.d/runner-busy.conf" \
    | sudo tee "${RUNNER_BUSY_TMPFILES}" > /dev/null
sudo chmod 0644 "${RUNNER_BUSY_TMPFILES}"
sudo systemd-tmpfiles --create "${RUNNER_BUSY_TMPFILES}" 2>/dev/null \
    || sudo install -d -m 0775 -o "${RUNNER_USER}" -g "${RUNNER_USER}" /run/runner-busy
sudo install -d -m 0755 /var/log/runner-cleanup /var/lib/runner-scheduler "${TEXTFILE_COLLECTOR_DIR}"

SCHEDULER_SUDOERS="/etc/sudoers.d/runner-dashboard-scheduler"
sudo tee "${SCHEDULER_SUDOERS}" > /dev/null <<SUDOERS
# Allow the dashboard Apply Now button to trigger the root-owned scheduler unit.
${RUNNER_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} start runner-scheduler.service
SUDOERS
sudo chmod 0440 "${SCHEDULER_SUDOERS}"
sudo visudo -cf "${SCHEDULER_SUDOERS}" > /dev/null

# Storage GC (runner-cleanup binary + runner-cleanup / runner-disk-guard
# service+timer pairs) has exactly one owner: install-storage-gc.sh (#1925).
# It installs, neutralises ExecStart-override drop-ins, enables both timers and
# exits non-zero unless its own --verify passes. Never re-define those units here.
sudo env RUNNER_USER="${RUNNER_USER}" RUNNER_ROOTS="${RUNNER_ROOTS}" \
    bash "${SCRIPT_DIR}/install-storage-gc.sh" --profile runner

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
ExecStart=${SCHEDULER_PYTHON} /usr/local/bin/runner-scheduler --apply
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

sudo tee /etc/systemd/system/runner-corruption-scan.service > /dev/null <<SERVICE
[Unit]
Description=Scan GitHub runner directories for corruption residue and emit Prometheus metrics
Documentation=https://github.com/D-sorganization/Runner_Dashboard/blob/main/docs/observability.md
After=local-fs.target

[Service]
Type=oneshot
User=root
Environment=RUNNER_ROOT=${RUNNER_ROOT}
Environment=PROM_FILE=${TEXTFILE_COLLECTOR_DIR}/runner_corruption.prom
Environment=DIAG_PAGES_MIN_AGE_DAYS=1
ExecStart=/usr/local/bin/runner-corruption-scan
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
SERVICE

sudo tee /etc/systemd/system/runner-corruption-scan.timer > /dev/null <<'TIMER'
[Unit]
Description=Run runner corruption residue scan every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
TIMER

sudo tee /etc/systemd/system/runner-hooks-restore.service > /dev/null <<SERVICE
[Unit]
Description=Restore /usr/local/bin/runner-hooks/*.sh if they go missing
Documentation=https://github.com/D-sorganization/Runner_Dashboard/blob/main/deploy/runner-hooks-restore.sh
After=local-fs.target

[Service]
Type=oneshot
User=root
Environment=HOOK_SRC_DIR=${HOOK_SRC_DIR_INSTALLED}
Environment=HOOK_DIR=${HOOK_INSTALL_DIR}
Environment=TEXTFILE_COLLECTOR_DIR=${TEXTFILE_COLLECTOR_DIR}
ExecStart=/usr/local/bin/runner-hooks-restore
Nice=10
SERVICE

sudo tee /etc/systemd/system/runner-hooks-restore.timer > /dev/null <<'TIMER'
[Unit]
Description=Self-heal the runner job-pickup hooks every five minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=15s
Persistent=true

[Install]
WantedBy=timers.target
TIMER

# Ensure runner-cleanup writes its textfile metric to the same collector
# directory used by node_exporter. The cleanup script defaults to
# /var/lib/node_exporter/textfile_collector but honours TEXTFILE_COLLECTOR_DIR.
sudo mkdir -p /etc/systemd/system/runner-cleanup.service.d
sudo tee /etc/systemd/system/runner-cleanup.service.d/textfile.conf > /dev/null <<CONF
[Service]
Environment=TEXTFILE_COLLECTOR_DIR=${TEXTFILE_COLLECTOR_DIR}
CONF

sudo systemctl daemon-reload
sudo systemctl enable --now runner-scheduler.timer runner-corruption-scan.timer runner-hooks-restore.timer wsl-cache-trim.timer

# Apply (or re-apply) the per-unit drop-ins from #664. Idempotent.
# Passes the installed hook dir explicitly so the drop-ins reference the
# stable /usr/local/bin path rather than whatever default the script ships.
if [[ -x "${SCRIPT_DIR}/migrate-runner-units.sh" ]]; then
    echo "Applying actions.runner.*.service drop-ins (idempotent)..."
    sudo HOOK_DIR="${HOOK_INSTALL_DIR}" LOCK_DIR=/var/run/runner-busy \
        "${SCRIPT_DIR}/migrate-runner-units.sh"
fi

echo "Installed:"
systemctl list-timers runner-cleanup.timer runner-disk-guard.timer runner-scheduler.timer runner-corruption-scan.timer wsl-cache-trim.timer --all
echo ""
echo "Binaries:"
ls -l /usr/local/bin/runner-cleanup /usr/local/bin/heal-host /usr/local/bin/runner-corruption-scan /usr/local/bin/runner-hooks-restore /usr/local/bin/clean-stale-shell-profiles /usr/local/bin/clean-gitconfig-token-rewrites "${HOOK_INSTALL_DIR}"/ 2>/dev/null | grep -v '^total' || true

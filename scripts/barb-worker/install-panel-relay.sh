#!/usr/bin/env bash
# install-panel-relay.sh — run the barbara panel relay every few minutes as one lane user (#3711).
# Run as root on barb-worker: sudo bash install-panel-relay.sh [--user barb-duet] [--disable]
set -Eeuo pipefail

LANE_USER="barb-duet"
ACTION="enable"
RD_ROOT="${BARB_RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
INTERVAL="${BARB_PANEL_RELAY_INTERVAL:-2min}"
AUTHORS="${BARB_PANEL_RELAY_AUTHORS:-dieterolson,d-sorgclaudeagent[bot],d-sorgcodexagent[bot],codex-scheduled-tasks[bot]}"
CAP="${BARB_PANEL_RELAY_DAILY_CAP:-3}"
UNIT="barb-panel-relay"

die() { echo "install-panel-relay: $*" >&2; exit 2; }
say() { echo "install-panel-relay: $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) LANE_USER="${2:?--user needs a name}"; shift 2 ;;
        --disable) ACTION="disable"; shift ;;
        -h|--help) sed -n '2,3p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || die "run as root (sudo)"
id "${LANE_USER}" >/dev/null 2>&1 || die "no such user ${LANE_USER}"
[[ -f "${RD_ROOT}/scripts/barb_panel_relay.py" ]] || die "missing ${RD_ROOT}/scripts/barb_panel_relay.py (pull first)"

HOME_DIR="/home/${LANE_USER}"
UNIT_DIR="${HOME_DIR}/.config/systemd/user"
LANE_UID="$(id -u "${LANE_USER}")"
as_lane() { sudo -u "${LANE_USER}" -H env "XDG_RUNTIME_DIR=/run/user/${LANE_UID}" "RUNNER_DASHBOARD_ROOT=${RD_ROOT}" "$@"; }

if [[ "${ACTION}" == "disable" ]]; then
    as_lane systemctl --user disable --now "${UNIT}.timer" 2>/dev/null || true
    say "${LANE_USER}: ${UNIT}.timer stopped and disabled"
    exit 0
fi

# Prove the lane can mint a bot token and read barbara before anything runs for real.
say "${LANE_USER}: dry run (writes nothing)"
as_lane bash "${RD_ROOT}/scripts/barb-worker/panel-relay-pass.sh" --dry-run || die "dry run failed; not enabling"

install -d -m 700 -o "${LANE_USER}" -g "${LANE_USER}" "${UNIT_DIR}"
umask 077
cat >"${UNIT_DIR}/${UNIT}.env" <<ENV
BARB_PANEL_RELAY_ENABLED=1
BARB_PANEL_RELAY_AUTHORS=${AUTHORS}
BARB_PANEL_RELAY_DAILY_CAP=${CAP}
RUNNER_DASHBOARD_ROOT=${RD_ROOT}
ENV
cat >"${UNIT_DIR}/${UNIT}.service" <<UNITFILE
[Unit]
Description=Barbara panel relay, one pass (${LANE_USER})
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-%h/.config/systemd/user/barb-fleet.env
EnvironmentFile=%h/.config/systemd/user/${UNIT}.env
ExecStart=/bin/bash ${RD_ROOT}/scripts/barb-worker/panel-relay-pass.sh
TimeoutStartSec=10min
UNITFILE
cat >"${UNIT_DIR}/${UNIT}.timer" <<TIMER
[Unit]
Description=Barbara panel relay every ${INTERVAL} (${LANE_USER})

[Timer]
OnBootSec=2min
OnUnitInactiveSec=${INTERVAL}
Unit=${UNIT}.service

[Install]
WantedBy=timers.target
TIMER
chown "${LANE_USER}:${LANE_USER}" "${UNIT_DIR}/${UNIT}".{env,service,timer}
chmod 600 "${UNIT_DIR}/${UNIT}.env"
chmod 644 "${UNIT_DIR}/${UNIT}.service" "${UNIT_DIR}/${UNIT}.timer"

loginctl enable-linger "${LANE_USER}"
as_lane systemctl --user daemon-reload
as_lane systemctl --user enable --now "${UNIT}.timer"
as_lane systemctl --user start "${UNIT}.service" || say "first pass failed; see the log below"
as_lane journalctl --user -u "${UNIT}.service" -n 20 --no-pager || true
say "${LANE_USER}: relay on, every ${INTERVAL}, cap ${CAP}/day. Turn off: sudo bash $0 --disable"

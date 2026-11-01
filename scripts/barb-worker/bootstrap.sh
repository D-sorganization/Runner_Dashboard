#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HUB_URL="${BARB_WORKER_HUB_URL:-https://hub-a.fleet-example.ts.net}"
GITHUB_ORG="${FLEET_GITHUB_ORG:-D-sorganization}"
# Shared checkout readable by all lane users (override for tests).
BARB_RD_ROOT="${BARB_RUNNER_DASHBOARD_ROOT:-/opt/Runner_Dashboard}"
# user -> proxy_port principal_id (tailscaled@ instance: grok / dots / duet)
BARB_USERS=(barb-grok barb-dots barb-duet)
BARB_PROXY_PORTS=(1046 1056 1066)
BARB_PRINCIPAL=(agent-grok-vm agent-codex-hands agent-duet)

# Core fleet checkouts (full list: set BARB_CLONE_ALL_FLEET_REPOS=1).
BARB_CORE_REPOS=(Runner_Dashboard Repository_Management Maxwell_Daemon UpstreamDrift Tools Tools_Private)

log() { printf '[barb-worker] %s\n' "$*"; }
die() { printf '[barb-worker] ERROR: %s\n' "$*" >&2; exit 2; }

require_vmadmin() {
    if [[ "${EUID}" -ne 0 ]]; then
        die "run as root (vmadmin via sudo)"
    fi
    if [[ ! -d /run/systemd/system ]]; then
        die "systemd is required"
    fi
}

run_as() {
    local user=$1
    shift
    sudo -u "${user}" -H env "HOME=/home/${user}" "$@"
}

run_as_user_systemd() {
    local user=$1
    shift
    local uid
    uid="$(id -u "${user}")"
    run_as "${user}" env XDG_RUNTIME_DIR="/run/user/${uid}" "$@"
}

enable_user_linger() {
    local user=$1
    log "enabling systemd linger for ${user}"
    loginctl enable-linger "${user}"
}

user_index() {
    local want=$1 i
    for i in "${!BARB_USERS[@]}"; do
        [[ "${BARB_USERS[$i]}" == "${want}" ]] && { echo "${i}"; return 0; }
    done
    return 1
}

install_apt_packages() {
    log "installing apt packages (git, gh, python3, uv prerequisites)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git python3 python3-venv python3-pip \
        gh shellcheck jq
    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    fi
}

nodejs_major_version() {
    if ! command -v node >/dev/null 2>&1; then
        echo 0
        return 0
    fi
    node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0
}

install_nodejs_for_claude_code() {
    local major
    major="$(nodejs_major_version)"
    if [[ "${major}" -ge 22 ]]; then
        log "nodejs $(node --version 2>/dev/null || echo unknown) satisfies Claude Code (>=22)"
        return 0
    fi
    log "installing Node.js 22 via NodeSource (Ubuntu node ${major:-missing} is too old for Claude Code)"
    export DEBIAN_FRONTEND=noninteractive
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
    major="$(nodejs_major_version)"
    [[ "${major}" -ge 22 ]] || die "Node.js >=22 required after NodeSource install (got major=${major})"
}

install_global_clis() {
    log "installing Claude Code and Codex CLIs (npm global)"
    npm install -g @anthropic-ai/claude-code@latest @openai/codex@latest
}

# HOOK: hub-minted GitHub App token wrapper for spoke gh/git (#2051).
# When scripts/barb-worker/install-hub-fleet-gh.sh exists on main, bootstrap will invoke it.
install_user_github_wrappers() {
    local user=$1
    local helper="${SCRIPT_DIR}/install-hub-fleet-gh.sh"
    if [[ -x "${helper}" ]]; then
        log "installing hub-minted gh wrapper for ${user}"
        run_as "${user}" env RUNNER_DASHBOARD_ROOT="${BARB_RD_ROOT}" bash "${helper}"
        return 0
    fi
    log "hub-minted gh helper not present (${helper}); real gh remains on PATH (install token flows manually)"
}

configure_user_proxy_and_env() {
    local user=$1 port=$2 principal=$3
    local home="/home/${user}"
    local cfg="${home}/.config/runner-dashboard"
    local proxy="http://127.0.0.1:${port}"

    install -d -m 700 -o "${user}" -g "${user}" "${cfg}"
    if [[ ! -f "${cfg}/token" ]]; then
        run_as "${user}" touch "${cfg}/token"
        chmod 600 "${cfg}/token"
        chown "${user}:${user}" "${cfg}/token"
        log "${user}: created empty ${cfg}/token (install hub bearer with mode 600 before verify)"
    else
        chmod 600 "${cfg}/token" 2>/dev/null || true
        chown "${user}:${user}" "${cfg}/token"
    fi

    local profile="${home}/.profile"
    touch "${profile}"
    chown "${user}:${user}" "${profile}"
    if ! grep -q 'barb-worker fleet env' "${profile}" 2>/dev/null; then
        cat >>"${profile}" <<EOF

# barb-worker fleet env (Runner_Dashboard barb-worker bootstrap)
export FLEET_API_URL="${HUB_URL}"
export FLEET_AGENT="${principal}"
export BARB_LANE_PROXY_PORT="${port}"
export HTTP_PROXY="${proxy}"
export HTTPS_PROXY="${proxy}"
export http_proxy="${proxy}"
export https_proxy="${proxy}"
export NO_PROXY="localhost,127.0.0.1,::1"
export no_proxy="\${NO_PROXY}"
export RUNNER_DASHBOARD_ROOT="${BARB_RD_ROOT}"
_rd_token="\${HOME}/.config/runner-dashboard/token"
if [[ -r "\${_rd_token}" ]]; then
  IFS= read -r FLEET_API_TOKEN <"\${_rd_token}" || FLEET_API_TOKEN=""
  export FLEET_API_TOKEN
fi
unset _rd_token
EOF
    fi

    install -d -m 700 -o "${user}" -g "${user}" "${home}/.config/systemd/user"
    cat >"${home}/.config/systemd/user/barb-fleet.env" <<EOF
FLEET_API_URL=${HUB_URL}
FLEET_AGENT=${principal}
BARB_LANE_PROXY_PORT=${port}
HTTP_PROXY=${proxy}
HTTPS_PROXY=${proxy}
NO_PROXY=localhost,127.0.0.1,::1
RUNNER_DASHBOARD_ROOT=${BARB_RD_ROOT}
EOF
    chown "${user}:${user}" "${home}/.config/systemd/user/barb-fleet.env"
    chmod 600 "${home}/.config/systemd/user/barb-fleet.env"
}

clone_shared_runner_dashboard() {
    if [[ -d "${BARB_RD_ROOT}/.git" ]]; then
        git -C "${BARB_RD_ROOT}" fetch --quiet origin 2>/dev/null || true
        log "Runner_Dashboard already present at ${BARB_RD_ROOT}"
        return 0
    fi
    log "cloning ${GITHUB_ORG}/Runner_Dashboard to ${BARB_RD_ROOT} (shared, world-readable)"
    install -d -m 755 "$(dirname "${BARB_RD_ROOT}")"
    git clone --quiet "https://github.com/${GITHUB_ORG}/Runner_Dashboard.git" "${BARB_RD_ROOT}" || \
        die "failed to clone Runner_Dashboard to ${BARB_RD_ROOT}"
    chmod -R a+rX "${BARB_RD_ROOT}"
}

clone_fleet_repos_for_user() {
    local user=$1
    local home="/home/${user}"
    local src="${home}/src"
    run_as "${user}" mkdir -p "${src}"

    local repos=()
    for repo in "${BARB_CORE_REPOS[@]}"; do
        [[ "${repo}" == Runner_Dashboard ]] && continue
        repos+=("${repo}")
    done
    if [[ "${BARB_CLONE_ALL_FLEET_REPOS:-0}" == "1" ]]; then
        local projects="${REPO_ROOT}/config/projects.json"
        if [[ -f "${projects}" ]]; then
            mapfile -t repos < <(python3 - "${projects}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for name in data.get("repos", []):
    print(name)
PY
)
        fi
    fi

    local repo
    for repo in "${repos[@]}"; do
        [[ "${repo}" == Runner_Dashboard ]] && continue
        local dest="${src}/${repo}"
        if [[ -d "${dest}/.git" ]]; then
            run_as "${user}" git -C "${dest}" fetch --quiet origin || true
            continue
        fi
        log "${user}: cloning ${GITHUB_ORG}/${repo}"
        run_as "${user}" git clone --quiet "https://github.com/${GITHUB_ORG}/${repo}.git" "${dest}" || \
            log "${user}: clone failed for ${repo} (retry after gh auth / network)"
    done
}

install_lane_systemd_service() {
    local user=$1
    local home="/home/${user}"
    local unit="${home}/.config/systemd/user/barb-lane.service"
    local loop="${BARB_RD_ROOT}/scripts/barb-worker/lane-loop.sh"

    cat >"${unit}" <<EOF
[Unit]
Description=Barb lane inbox loop (${user})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-${home}/.config/systemd/user/barb-fleet.env
ExecStart=${loop}
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF
    chown "${user}:${user}" "${unit}"
    chmod 644 "${unit}"

    enable_user_linger "${user}"
    run_as_user_systemd "${user}" systemctl --user daemon-reload
    run_as_user_systemd "${user}" systemctl --user enable --now barb-lane.service 2>/dev/null || \
        log "${user}: barb-lane.service not started (ensure ${BARB_RD_ROOT} is cloned)"
}

provision_user() {
    local user=$1
    id "${user}" >/dev/null 2>&1 || die "missing user ${user} (create barb UNIX users first)"
    local idx
    idx="$(user_index "${user}")" || die "unknown barb user ${user}"
    configure_user_proxy_and_env "${user}" "${BARB_PROXY_PORTS[$idx]}" "${BARB_PRINCIPAL[$idx]}"
    clone_fleet_repos_for_user "${user}"
    install_user_github_wrappers "${user}"
    install_lane_systemd_service "${user}"
}

cmd_install() {
    require_vmadmin
    install_apt_packages
    install_nodejs_for_claude_code
    install_global_clis
    clone_shared_runner_dashboard
    local u
    for u in "${BARB_USERS[@]}"; do
        provision_user "${u}"
    done
    log "install complete; place hub tokens in ~/.config/runner-dashboard/token for each barb user, then: sudo bash ${SCRIPT_DIR}/bootstrap.sh verify"
}

verify_lane() {
    local user=$1 port=$2
    local home="/home/${user}"
    local token_file="${home}/.config/runner-dashboard/token"
    local proxy="http://127.0.0.1:${port}"
    local live_ok=fail summary_ok=fail

    if curl -fsS --max-time 15 -x "${proxy}" "${HUB_URL%/}/livez" >/dev/null 2>&1; then
        live_ok=pass
    fi

    if [[ -s "${token_file}" ]]; then
        if run_as "${user}" env HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" FLEET_API_URL="${HUB_URL}" \
            python3 - "${token_file}" <<'PY' 2>/dev/null
import os
import sys
import urllib.request
from pathlib import Path

token = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not token:
    raise SystemExit(1)
base = os.environ["FLEET_API_URL"].rstrip("/")
proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
handlers = []
if proxy:
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
opener = urllib.request.build_opener(*handlers)
req = urllib.request.Request(
    f"{base}/api/staff/summary",
    headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
)
opener.open(req, timeout=20)
PY
        then
            summary_ok=pass
        fi
    fi

    printf '%s livez=%s staff_summary=%s\n' "${user}" "${live_ok}" "${summary_ok}"
}

cmd_verify() {
    require_vmadmin
    local u idx
    for u in "${BARB_USERS[@]}"; do
        idx="$(user_index "${u}")"
        verify_lane "${u}" "${BARB_PROXY_PORTS[$idx]}"
    done
}

usage() {
    sed -n '2,12p' "$0"
    printf '\nSubcommands:\n  install   (default) provision all barb users\n  verify    check hub /livez and /api/staff/summary per lane\n'
}

main() {
    local cmd=${1:-install}
    case "${cmd}" in
        install) cmd_install ;;
        verify) cmd_verify ;;
        -h|--help) usage ;;
        *) die "unknown subcommand: ${cmd} (try --help)" ;;
    esac
}

main "$@"

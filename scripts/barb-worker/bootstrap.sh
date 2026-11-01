#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HUB_URL="${BARB_WORKER_HUB_URL:-https://deskcomputer.tail2bbcc7.ts.net:8321}"
GITHUB_ORG="${FLEET_GITHUB_ORG:-D-sorganization}"
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

user_index() {
    local want=$1 i
    for i in "${!BARB_USERS[@]}"; do
        [[ "${BARB_USERS[$i]}" == "${want}" ]] && { echo "${i}"; return 0; }
    done
    return 1
}

install_apt_packages() {
    log "installing apt packages (nodejs, git, gh, python3, uv prerequisites)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl git python3 python3-venv python3-pip \
        nodejs npm gh shellcheck jq
    if ! command -v uv >/dev/null 2>&1; then
        curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    fi
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
        run_as "${user}" bash "${helper}"
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
        run_as "${user}" bash -c ': > "$1"' _ "${cfg}/token"
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
export RUNNER_DASHBOARD_ROOT="\${HOME}/src/Runner_Dashboard"
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
RUNNER_DASHBOARD_ROOT=${home}/src/Runner_Dashboard
EOF
    chown "${user}:${user}" "${home}/.config/systemd/user/barb-fleet.env"
    chmod 600 "${home}/.config/systemd/user/barb-fleet.env"
}

clone_fleet_repos_for_user() {
    local user=$1
    local home="/home/${user}"
    local src="${home}/src"
    run_as "${user}" mkdir -p "${src}"

    local repos=("${BARB_CORE_REPOS[@]}")
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
    local loop="${home}/src/Runner_Dashboard/scripts/barb-worker/lane-loop.sh"

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

    loginctl enable-linger "${user}" 2>/dev/null || true
    run_as "${user}" systemctl --user daemon-reload
    run_as "${user}" systemctl --user enable --now barb-lane.service 2>/dev/null || \
        log "${user}: barb-lane.service not started (clone Runner_Dashboard first)"
}

provision_user() {
    local user=$1
    id "${user}" >/dev/null 2>&1 || die "missing user ${user} (create barb UNIX users first)"
    local idx
    idx="$(user_index "${user}")" || die "unknown barb user ${user}"
    configure_user_proxy_and_env "${user}" "${BARB_PROXY_PORTS[$idx]}" "${BARB_PRINCIPAL[$idx]}"
    install_user_github_wrappers "${user}"
    clone_fleet_repos_for_user "${user}"
    install_lane_systemd_service "${user}"
}

cmd_install() {
    require_vmadmin
    install_apt_packages
    install_global_clis
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
        if run_as "${user}" env HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" \
            FLEET_API_URL="${HUB_URL}" bash -c '
                set -euo pipefail
                IFS= read -r tok < "$1"
                test -n "$tok"
                curl -fsS --max-time 20 \
                    -H "Authorization: Bearer ${tok}" \
                    -H "X-Requested-With: XMLHttpRequest" \
                    "${FLEET_API_URL%/}/api/staff/summary" >/dev/null
            ' _ "${token_file}" 2>/dev/null; then
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

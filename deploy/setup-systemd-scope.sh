#!/usr/bin/env bash
# deploy/setup-systemd-scope.sh — user vs system runner-dashboard.service install (#2058).
# Sourced by deploy/setup.sh; not executed directly.

: "${SYSTEMD_DASHBOARD_SCOPE:=}"

user_dashboard_unit_path() {
    printf '%s/.config/systemd/user/runner-dashboard.service' "${HOME}"
}

system_dashboard_unit_path() {
    printf '/etc/systemd/system/runner-dashboard.service'
}

dashboard_user_unit_installed() {
    [[ -f "$(user_dashboard_unit_path)" ]]
}

dashboard_system_unit_installed() {
    [[ -f "$(system_dashboard_unit_path)" ]]
}

# resolve_systemd_dashboard_scope <force_user> <force_system>
# Sets SYSTEMD_DASHBOARD_SCOPE to "user" or "system".
resolve_systemd_dashboard_scope() {
    local force_user="${1:-}"
    local force_system="${2:-}"
    local has_user=0 has_system=0

    dashboard_user_unit_installed && has_user=1
    dashboard_system_unit_installed && has_system=1

    if [[ -n "${force_user}" && -n "${force_system}" ]]; then
        echo "ERROR: cannot pass both --user-service and --system-service" >&2
        return 1
    fi

    if [[ -n "${force_system}" ]]; then
        if (( has_user )); then
            echo "ERROR: --system-service requested but a user unit exists at $(user_dashboard_unit_path)." >&2
            echo "ERROR: disable the user service first (systemctl --user disable --now runner-dashboard.service) or use --user-service." >&2
            return 1
        fi
        SYSTEMD_DASHBOARD_SCOPE=system
        return 0
    fi

    if [[ -n "${force_user}" ]]; then
        SYSTEMD_DASHBOARD_SCOPE=user
        return 0
    fi

    if (( has_user )); then
        if (( has_system )); then
            echo "ERROR: both user and system runner-dashboard units are installed." >&2
            echo "ERROR: remove one scope before re-running setup (prefer keeping the user unit on Brick)." >&2
            return 1
        fi
        SYSTEMD_DASHBOARD_SCOPE=user
        return 0
    fi

    if (( has_system )); then
        SYSTEMD_DASHBOARD_SCOPE=system
        return 0
    fi

    SYSTEMD_DASHBOARD_SCOPE=system
    return 0
}

# install_dashboard_systemd_unit <template> <machine_name> ...
# Uses globals from setup.sh: USER, HOME, DEPLOY_DIR, PYTHON_BIN, PORT, etc.
install_dashboard_systemd_unit() {
    local template_file="$1"
    local machine_name="$2"
    local rendered
    local unit_path
    local -a ctl

    if [[ "${SYSTEMD_DASHBOARD_SCOPE}" == "user" ]]; then
        unit_path="$(user_dashboard_unit_path)"
        mkdir -p "$(dirname "${unit_path}")"
        rendered="$(mktemp)"
        sed -e "s|Description=D-sorganization Runner Dashboard|Description=D-sorganization Runner Dashboard (${machine_name})|g" \
            -e '/^User=/d' \
            -e 's|WantedBy=multi-user.target|WantedBy=default.target|g' \
            -e "s|YOUR_USER|${USER}|g" \
            -e "s|/home/YOUR_USER|${HOME}|g" \
            -e "s|${HOME}/actions-runners/dashboard|${DEPLOY_DIR}|g" \
            -e "s|/usr/bin/python3.11|${PYTHON_BIN}|g" \
            -e "s|RUNNER_BASE_DIR=${HOME}/actions-runners|RUNNER_BASE_DIR=${RUNNER_BASE_DIR}|g" \
            -e "s|ReadWritePaths=${HOME}/actions-runners$|ReadWritePaths=${RUNNER_BASE_DIR}|g" \
            -e "s|NUM_RUNNERS=12|NUM_RUNNERS=${NUM_RUNNERS}|g" \
            -e "s|DASHBOARD_PORT=8321|DASHBOARD_PORT=${PORT}|g" \
            -e "s|DISPLAY_NAME=ControlTower|DISPLAY_NAME=${DISPLAY_NAME_VAL}|g" \
            -e "s|RUNNER_ALIASES=controltower,control-tower-runner-monitoring|RUNNER_ALIASES=${RUNNER_ALIASES_VAL}|g" \
            -e "s|RUNNER_SCHEDULE_CONFIG=.*|RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG_VAL}|g" \
            "${template_file}" > "${rendered}"
        install -m 0644 "${rendered}" "${unit_path}"
        rm -f "${rendered}"
        ctl=(systemctl --user)
    else
        unit_path="$(system_dashboard_unit_path)"
        rendered="$(mktemp)"
        sed -e "s|Description=D-sorganization Runner Dashboard|Description=D-sorganization Runner Dashboard (${machine_name})|g" \
            -e "s|YOUR_USER|${USER}|g" \
            -e "s|/home/YOUR_USER|${HOME}|g" \
            -e "s|${HOME}/actions-runners/dashboard|${DEPLOY_DIR}|g" \
            -e "s|/usr/bin/python3.11|${PYTHON_BIN}|g" \
            -e "s|RUNNER_BASE_DIR=${HOME}/actions-runners|RUNNER_BASE_DIR=${RUNNER_BASE_DIR}|g" \
            -e "s|ReadWritePaths=${HOME}/actions-runners$|ReadWritePaths=${RUNNER_BASE_DIR}|g" \
            -e "s|NUM_RUNNERS=12|NUM_RUNNERS=${NUM_RUNNERS}|g" \
            -e "s|DASHBOARD_PORT=8321|DASHBOARD_PORT=${PORT}|g" \
            -e "s|DISPLAY_NAME=ControlTower|DISPLAY_NAME=${DISPLAY_NAME_VAL}|g" \
            -e "s|RUNNER_ALIASES=controltower,control-tower-runner-monitoring|RUNNER_ALIASES=${RUNNER_ALIASES_VAL}|g" \
            -e "s|RUNNER_SCHEDULE_CONFIG=.*|RUNNER_SCHEDULE_CONFIG=${SCHEDULE_CONFIG_VAL}|g" \
            "${template_file}" > "${rendered}"
        sudo install -m 0644 "${rendered}" "${unit_path}"
        rm -f "${rendered}"
        ctl=(sudo systemctl)
    fi

    if [[ -n "${ROLE_ENV_OVERRIDE}" ]]; then
        sed -i '/^Environment=MACHINE_ROLE=/d' "${unit_path}"
        if [[ "${SYSTEMD_DASHBOARD_SCOPE}" == "user" ]]; then
            sed -i "/^Environment=DISPLAY_NAME=/a Environment=MACHINE_ROLE=${ROLE_ENV_OVERRIDE}" "${unit_path}"
        else
            sudo sed -i '/^Environment=MACHINE_ROLE=/d' "${unit_path}"
            sudo sed -i "/^Environment=DISPLAY_NAME=/a Environment=MACHINE_ROLE=${ROLE_ENV_OVERRIDE}" "${unit_path}"
        fi
    else
        if [[ "${SYSTEMD_DASHBOARD_SCOPE}" == "user" ]]; then
            sed -i '/^Environment=MACHINE_ROLE=/d' "${unit_path}"
        else
            sudo sed -i '/^Environment=MACHINE_ROLE=/d' "${unit_path}"
        fi
    fi

    "${ctl[@]}" daemon-reload
    "${ctl[@]}" enable runner-dashboard.service

    if [[ -z "${SKIP_RESTART:-}" ]]; then
        "${ctl[@]}" restart runner-dashboard.service
    fi

    printf '%s' "${unit_path}"
}

dashboard_systemctl_active() {
    if [[ "${SYSTEMD_DASHBOARD_SCOPE}" == "user" ]]; then
        systemctl --user is-active --quiet runner-dashboard.service 2>/dev/null
    else
        sudo systemctl is-active --quiet runner-dashboard.service 2>/dev/null
    fi
}

run_fleet_env_preflight() {
    local secrets_file="$1"
    local msg="$2"
    local -a args=(--repo-root "${SCRIPT_DIR}" --secrets-file "${secrets_file}")
    [[ -n "${ALLOW_HUB_URL_OVERRIDE:-}" ]] && args+=(--allow-registry-override)
    "${PYTHON_BIN}" "${SCRIPT_DIR}/deploy/fleet-env-preflight.py" "${args[@]}" || fail "${msg}"
}

capture_dashboard_service_was_active() {
    if dashboard_user_unit_installed && systemctl --user is-active --quiet runner-dashboard.service 2>/dev/null; then
        return 0
    fi
    dashboard_system_unit_installed && systemctl is-active --quiet runner-dashboard.service 2>/dev/null
}
